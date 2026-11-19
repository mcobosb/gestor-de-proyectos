"""Persistencia dividida, bloqueos y detección incremental para trabajo compartido."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import getpass
import json
import os
from pathlib import Path
import socket
from typing import Any
from uuid import uuid4
import re

from PySide6.QtCore import QObject, QTimer, Signal

from budget_project import (
    ProjectDocument,
    atomic_json_write,
    decode_project_payload,
    item_from_data,
)
from budget_reports import ReportSettings


PROJECT_MANIFEST = "proyecto.json"
FORMAT_NAME = "gestor-de-proyectos-colaborativo"
FORMAT_VERSION = 3


class StorageConflictError(RuntimeError):
    """Un archivo cambió externamente desde la última lectura."""

    def __init__(self, paths: list[Path]) -> None:
        """Conserva las rutas para mostrar un diagnóstico accionable."""
        self.paths = paths
        super().__init__(
            "Hay cambios externos en: "
            + ", ".join(path.name for path in paths)
        )


def project_directory(path: Path) -> Path | None:
    """Reconoce una carpeta o manifiesto colaborativo."""
    candidate = path if path.is_dir() else path.parent
    manifest = candidate / PROJECT_MANIFEST
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if (
        isinstance(data, dict)
        and data.get("format") == FORMAT_NAME
        and data.get("format_version") == FORMAT_VERSION
    ):
        return candidate
    return None


def _read_json(path: Path) -> Any:
    """Lee JSON validando errores de sincronización y texto."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"No se pudo leer {path.name}: {exc}") from exc


def _json_bytes(payload: Any) -> bytes:
    """Serializa establemente para comparar estados locales y remotos."""
    return json.dumps(
        payload, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")


def _file_bytes(path: Path) -> bytes | None:
    """Lee bytes de un archivo si existe."""
    try:
        return path.read_bytes()


def _merge_nonconflicting(
    baseline: Any, local: Any, remote: Any, path: tuple[str, ...] = ()
) -> Any:
    """Fusiona cambios remotos y locales solo cuando afectan a campos distintos."""
    if local == remote:
        return local
    if local == baseline:
        return remote
    if remote == baseline:
        return local
    if (
        isinstance(baseline, dict)
        and isinstance(local, dict)
        and isinstance(remote, dict)
    ):
        merged = {}
        keys = set(baseline) | set(local) | set(remote)
        for key in keys:
            marker = object()
            before = baseline.get(key, marker)
            ours = local.get(key, marker)
            theirs = remote.get(key, marker)
            try:
                value = _merge_nonconflicting(
                    before, ours, theirs, (*path, str(key))
                )
            except StorageConflictError:
                raise
            if value is not marker:
                merged[key] = value
        return merged
    raise StorageConflictError([])
    except FileNotFoundError:
        return None


def _canonical_file_bytes(path: Path) -> bytes | None:
    """Normaliza JSON para comparar contenido y no formato físico."""
    if not path.is_file():
        return None
    try:
        return _json_bytes(_read_json(path))
    except ValueError:
        return path.read_bytes()


@dataclass(frozen=True)
class LockHandle:
    """Propiedad exacta que permite renovar y liberar un bloqueo."""

    path: Path
    lock_path: Path
    token: str
    scope: str


class ProjectLockManager:
    """Bloqueos caducables con token para impedir liberaciones ajenas."""

    def __init__(
        self,
        user: str | None = None,
        *,
        expiration_minutes: int = 5,
    ) -> None:
        """Configura usuario visible y caducidad del lock."""
        self.user = user or getpass.getuser()
        self.host = socket.gethostname()
        self.expiration = timedelta(minutes=expiration_minutes)
        self._owned_tokens: set[str] = set()

    @staticmethod
    def lock_path(path: Path, scope: str = "general") -> Path:
        """Obtiene la ruta .lock específica de una sección editable."""
        return path.with_suffix(f".{scope}.lock")

    def _payload(self, token: str) -> dict[str, str]:
        """Crea el contenido renovable del bloqueo."""
        return {
            "user": self.user,
            "host": self.host,
            "token": token,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def acquire(
        self, path: Path, scope: str = "general"
    ) -> tuple[LockHandle | None, str]:
        """Adquiere o renueva un lock propio y rechaza uno ajeno vigente."""
        lock_path = self.lock_path(path, scope)
        token = uuid4().hex
        while True:
            try:
                descriptor = os.open(
                    lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                try:
                    data = _read_json(lock_path)
                    timestamp = datetime.fromisoformat(str(data["timestamp"]))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    owner = str(data["user"])
                    existing_token = str(data.get("token", ""))
                    alive = datetime.now(timezone.utc) - timestamp < self.expiration
                    if alive:
                        if (
                            owner == self.user
                            and data.get("host") == self.host
                            and existing_token in self._owned_tokens
                        ):
                            return (
                                LockHandle(path, lock_path, existing_token, scope),
                                "Lock propio",
                            )
                        return None, f"En edición por {owner}"
                    lock_path.unlink()
                except (ValueError, KeyError, OSError):
                    lock_path.unlink(missing_ok=True)
                continue
            else:
                payload = self._payload(token)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(payload, output, ensure_ascii=False)
                    output.flush()
                    os.fsync(output.fileno())
                self._owned_tokens.add(token)
                return (
                    LockHandle(path, lock_path, token, scope),
                    "Bloqueo adquirido",
                )

    def renew(self, handle: LockHandle) -> tuple[bool, str]:
        """Renueva únicamente si el archivo sigue perteneciendo al token."""
        try:
            data = _read_json(handle.lock_path)
            if data.get("token") != handle.token:
                return False, f"Bloqueo tomado por {data.get('user', 'otro usuario')}"
        except (ValueError, FileNotFoundError):
            return False, "El bloqueo dejó de existir."
        atomic_json_write(handle.lock_path, self._payload(handle.token))
        self._owned_tokens.add(handle.token)
        return True, "Bloqueo renovado"

    def release(self, handle: LockHandle) -> None:
        """Libera el lock solo cuando conserva el token propio."""
        try:
            data = _read_json(handle.lock_path)
        except (ValueError, FileNotFoundError):
            return
        if data.get("token") == handle.token:
            handle.lock_path.unlink(missing_ok=True)
            self._owned_tokens.discard(handle.token)


class CollaborativeProjectStore:
    """Lee, compara y escribe un proyecto dividido por entidades."""

    TOOL_FILES = {
        "planning": ("planificacion", "tareas_gantt.json"),
        "invoicing": ("herramientas", "facturacion.json"),
        "rcds": ("herramientas", "rcds.json"),
        "safety_health": ("herramientas", "seguridad_salud.json"),
        "pptp": ("herramientas", "pptp.json"),
        "price_justification": ("herramientas", "justificacion_precios.json"),
    }

    def __init__(self, root: Path, default_settings: ReportSettings) -> None:
        """Conserva raíz y configuración usada para decodificar."""
        self.root = root.resolve()
        self.default_settings = default_settings
        self.baseline: dict[Path, bytes | None] = {}

    @property
    def manifest_path(self) -> Path:
        """Ruta del manifiesto general."""
        return self.root / PROJECT_MANIFEST

    def item_path(self, storage_id: str) -> Path:
        """Ruta estable de una partida."""
        return self.root / "partidas" / f"part_{storage_id}.json"

    def chapter_path(self, storage_id: str) -> Path:
        """Ruta estable de un capítulo."""
        return self.root / "capitulos" / f"cap_{storage_id}.json"

    def _capture(self, paths: set[Path]) -> None:
        """Actualiza la foto usada para detectar conflictos."""
        for path in paths:
            self.baseline[path] = _canonical_file_bytes(path)

    def load(self) -> ProjectDocument:
        """Ensambla el documento en memoria sin guardar totales calculados."""
        manifest = _read_json(self.manifest_path)
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != FORMAT_NAME
            or manifest.get("format_version") != FORMAT_VERSION
        ):
            raise ValueError("La carpeta no es un proyecto colaborativo compatible.")
        chapter_records = manifest.get("chapters")
        if not isinstance(chapter_records, list):
            raise ValueError("El manifiesto no contiene capítulos válidos.")
        items_by_id = {}
        chapter_order = []
        chapter_titles = {}
        chapter_ids = {}
        seen_chapter_ids: set[str] = set()
        used_files = {
            self.manifest_path,
        }
        for record in chapter_records:
            if not isinstance(record, dict):
                raise ValueError("Hay un capítulo no válido en el manifiesto.")
            chapter_id = str(record.get("id", ""))
            if not re.fullmatch(r"[0-9a-f]{32}", chapter_id):
                raise ValueError("Hay un capítulo sin UUID.")
            if chapter_id in seen_chapter_ids:
                raise ValueError("Hay capítulos con UUID repetidos.")
            seen_chapter_ids.add(chapter_id)
            path = self.chapter_path(chapter_id)
            data = _read_json(path)
            used_files.add(path)
            if data.get("id") != chapter_id or not isinstance(data.get("item_ids"), list):
                raise ValueError(f"Capítulo inválido: {path.name}")
            code = str(data["code"])
            chapter_order.append(code)
            chapter_titles[code] = str(data.get("title", ""))
            chapter_ids[code] = chapter_id
            for item_id in data["item_ids"]:
                if not isinstance(item_id, str):
                    raise ValueError(f"UUID de partida inválido en {path.name}")
                item_path = self.item_path(item_id)
                item_data = _read_json(item_path)
                used_files.add(item_path)
                if item_data.get("storage_id") != item_id:
                    raise ValueError(f"Partida inválida: {item_path.name}")
                items_by_id[item_id] = item_data
        payload = dict(manifest)
        payload.pop("format", None)
        payload.pop("format_version", None)
        payload["items"] = list(items_by_id.values())
        payload["chapters"] = {
            "order": chapter_order,
            "titles": chapter_titles,
            "ids": chapter_ids,
        }
        for state_key, parts in self.TOOL_FILES.items():
            path = self.root.joinpath(*parts)
            used_files.add(path)
            if path.is_file():
                payload[state_key] = _read_json(path)
            else:
                payload[state_key] = {}
        document = decode_project_payload(
            payload, self.manifest_path, self.default_settings
        )
        self._capture(used_files)
        return document

    def save(self, state: Any) -> None:
        """Guarda entidades cambiadas y detecta conflictos de edición remota."""
        payload = state._project_payload()
        chapter_ids = dict(getattr(state, "chapter_ids", {}))
        for chapter in payload["chapters"]["order"]:
            chapter_ids.setdefault(chapter, uuid4().hex)
        files: dict[Path, dict] = {}
        item_files = {
            item["storage_id"] for item in payload.get("items", [])
        }
        chapter_file_ids = set()
        for chapter_position, code in enumerate(payload["chapters"]["order"]):
            chapter_id = chapter_ids[code]
            chapter_file_ids.add(chapter_id)
            members = [
                item["storage_id"]
                for item in payload["items"]
                if item["chapter"] == code
            ]
            files[self.chapter_path(chapter_id)] = {
                "id": chapter_id,
                "code": code,
                "title": payload["chapters"]["titles"].get(code, code),
                "position": chapter_position,
                "item_ids": members,
            }
        for item in payload["items"]:
            item_id = str(item["storage_id"])
            if not item_id:
                raise ValueError("Una partida no tiene UUID de colaboración.")
            files[self.item_path(item_id)] = item
        root_payload = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "title": payload["title"],
            "source_budget_bc3": payload["source_budget_bc3"],
            "report_settings": payload["report_settings"],
            "chapters": [
                {"id": chapter_ids[code], "code": code}
                for code in payload["chapters"]["order"]
            ],
        }
        files[self.manifest_path] = root_payload
        for key, parts in self.TOOL_FILES.items():
            files[self.root.joinpath(*parts)] = payload.get(key, {})
        conflicts = []
        for path, data in files.items():
            serialized = _json_bytes(data)
            current = _canonical_file_bytes(path)
            baseline = self.baseline.get(path)
            local_changed = serialized != baseline
            external_changed = path in self.baseline and current != baseline
            if local_changed and external_changed:
                try:
                    merged = _merge_nonconflicting(
                        json.loads(baseline or b"null"),
                        data,
                        json.loads(current or b"null"),
                    )
                except (
                    StorageConflictError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    conflicts.append(path)
                else:
                    files[path] = merged
        if conflicts:
            raise StorageConflictError(conflicts)
        removed = [
            path
            for path in (*self.root.glob("partidas/part_*.json"),)
            if path.stem.removeprefix("part_") not in item_files
        ]
        removed.extend(
            path
            for path in self.root.glob("capitulos/cap_*.json")
            if path.stem.removeprefix("cap_") not in chapter_file_ids
        )
        for path in removed:
            current = _canonical_file_bytes(path)
            if path in self.baseline and current != self.baseline[path]:
                conflicts.append(path)
        if conflicts:
            raise StorageConflictError(list(dict.fromkeys(conflicts)))
        for path, data in files.items():
            if _json_bytes(data) != self.baseline.get(path):
                atomic_json_write(path, data)
        for path in removed:
            path.unlink(missing_ok=True)
        state.chapter_ids = chapter_ids
        self._capture(set(files) | set(removed))

    def changed_paths(self) -> list[Path]:
        """Devuelve entidades modificadas externamente desde la última foto."""
        changed = []
        for path, baseline in list(self.baseline.items()):
            if _canonical_file_bytes(path) != baseline:
                changed.append(path)
        return changed

    def load_item(self, storage_id: str):
        """Recarga una sola partida externa."""
        path = self.item_path(storage_id)
        data = _read_json(path)
        self.baseline[path] = _canonical_file_bytes(path)
        return item_from_data(data)

    @classmethod
    def create_from_payload(
        cls,
        root: Path,
        state: Any,
        default_settings: ReportSettings,
    ) -> CollaborativeProjectStore:
        """Crea una carpeta colaborativa desde el estado actual."""
        store = cls(root, default_settings)
        state.chapter_ids = dict(getattr(state, "chapter_ids", {}))
        store.save(state)
        return store


class CollaborativeWatchService(QObject):
    """Sondeo incremental compatible con carpetas locales y sincronizadas."""

    files_changed = Signal(list)

    def __init__(
        self, store: CollaborativeProjectStore, *, interval_ms: int = 2500
    ) -> None:
        """Programa comprobaciones sin hooks del sistema operativo."""
        super().__init__()
        self.store = store
        self.lock_snapshot = self._lock_snapshot()
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.check)

    def start(self) -> None:
        """Inicia la vigilancia de archivos y locks."""
        self.timer.start()

    def stop(self) -> None:
        """Detiene la vigilancia."""
        self.timer.stop()

    def check(self) -> None:
        """Emite únicamente rutas cuyo contenido cambió."""
        changed = self.store.changed_paths()
        locks = self._lock_snapshot()
        lock_paths = {
            path for path in set(locks) | set(self.lock_snapshot)
            if locks.get(path) != self.lock_snapshot.get(path)
        }
        self.lock_snapshot = locks
        changed.extend(lock_paths)
        if changed:
            self.files_changed.emit(changed)

    def _lock_snapshot(self) -> dict[Path, int | None]:
        """Firma presencia y renovación de locks sin leer el contenido aquí."""
        folder = self.store.root / "partidas"
        if not folder.is_dir():
            return {}
        return {
            path: path.stat().st_mtime_ns
            for path in folder.glob("part_*.lock")
        }
