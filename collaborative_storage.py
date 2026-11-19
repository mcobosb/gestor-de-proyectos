"""Persistencia dividida, bloqueos y detección incremental para trabajo compartido."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import getpass
import json
import os
from pathlib import Path
import socket
import tempfile
from typing import Any
from uuid import uuid4
import re
import shutil
from zipfile import ZipFile

from PySide6.QtCore import QObject, QTimer, Signal

from budget_project import (
    ProjectDocument,
    atomic_json_write,
    decode_project_payload,
    item_from_data,
)
from budget_reports import ReportSettings
from collaborative_crypto import ProjectCrypto


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
    except FileNotFoundError:
        return None


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


def _canonical_file_bytes(path: Path) -> bytes | None:
    """Normaliza JSON para comparar contenido y no formato físico."""
    if not path.is_file():
        return None
    try:
        return _json_bytes(_read_json(path))
    except ValueError:
        return path.read_bytes()


def encrypted_manifest_metadata(path: Path) -> dict | None:
    """Obtiene los parámetros públicos de un proyecto cifrado."""
    root = project_directory(path)
    if root is None:
        return None
    try:
        manifest = json.loads(
            (root / PROJECT_MANIFEST).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    metadata = manifest.get("encryption")
    return dict(metadata) if isinstance(metadata, dict) else None


def shared_library_path(app: Any, module: str, fallback: Path) -> Path:
    """Usa la biblioteca compartida del proyecto si el módulo la tiene."""
    configured = getattr(app, "library_sources", {}).get(module, "")
    if configured:
        selected = Path(configured).expanduser()
        if selected.is_file():
            return selected
    store = getattr(app, "project_store", None)
    if store is None:
        return fallback
    return store.materialize_library(module, fallback.name) or fallback


def shared_docs_path(app: Any, module: str, fallback: Path) -> Path:
    """Usa documentos compartidos junto a una biblioteca ZIP."""
    configured = getattr(app, "library_sources", {}).get(f"{module}_docs", "")
    if configured:
        selected = Path(configured).expanduser()
        if selected.is_dir():
            return selected
    store = getattr(app, "project_store", None)
    if store is None:
        return fallback
    return store.materialize_docs(module) or fallback


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

    def __init__(
        self,
        root: Path,
        default_settings: ReportSettings,
        crypto: ProjectCrypto | None = None,
    ) -> None:
        """Conserva raíz y configuración usada para decodificar."""
        self.root = root.resolve()
        self.default_settings = default_settings
        self.crypto = crypto
        self.baseline: dict[Path, bytes | None] = {}
        self.last_merges = 0
        self._materialized: dict[tuple[str, str], tuple[int, Path]] = {}

    def read_payload(self, path: Path) -> Any:
        """Lee un JSON cifrado o transparente según el proyecto."""
        envelope = _read_json(path)
        if self.crypto is not None:
            return self.crypto.decrypt(envelope)
        if isinstance(envelope, dict) and envelope.get("format") == "gdp-encrypted-json-v1":
            raise ValueError("Este proyecto requiere contraseña.")
        return envelope

    def write_payload(self, path: Path, payload: Any) -> None:
        """Cifra cada entidad por separado o escribe formato transparente."""
        if self.crypto is not None and path != self.manifest_path:
            atomic_json_write(path, self.crypto.encrypt(payload))
        elif self.crypto is not None:
            outer = {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "encryption": self.crypto.metadata,
                "payload": self.crypto.encrypt(payload),
            }
            atomic_json_write(path, outer)
        else:
            atomic_json_write(path, payload)

    def canonical_payload_bytes(self, path: Path) -> bytes | None:
        """Normaliza una entidad descifrada para comparar conflictos."""
        if not path.is_file():
            return None
        try:
            return _json_bytes(self.read_payload(path))
        except ValueError:
            return path.read_bytes()

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
            self.baseline[path] = self.canonical_payload_bytes(path)

    def load(self) -> ProjectDocument:
        """Ensambla el documento en memoria sin guardar totales calculados."""
        outer = _read_json(self.manifest_path)
        manifest = (
            self.read_manifest_payload(outer)
            if outer.get("format") == FORMAT_NAME and "payload" in outer
            else outer
        )
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
            data = self.read_payload(path)
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
                item_data = self.read_payload(item_path)
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
                payload[state_key] = self.read_payload(path)
            else:
                payload[state_key] = {}
        document = decode_project_payload(
            payload, self.manifest_path, self.default_settings
        )
        self._capture(used_files)
        return document

    def read_manifest_payload(self, envelope: object) -> dict:
        """Valida el contenedor externo del manifiesto y descifra su contenido."""
        if not isinstance(envelope, dict):
            raise ValueError("Manifiesto colaborativo no válido.")
        if not (
            envelope.get("format") == FORMAT_NAME
            and envelope.get("format_version") == FORMAT_VERSION
            and isinstance(envelope.get("payload"), dict)
        ):
            raise ValueError("La carpeta no contiene un manifiesto compatible.")
        if self.crypto is None:
            raise ValueError("Este proyecto está cifrado y requiere contraseña.")
        payload = self.crypto.decrypt(envelope["payload"])
        if not isinstance(payload, dict):
            raise ValueError("El manifiesto descifrado no es válido.")
        return payload

    def save(self, state: Any, *, force: bool = False) -> None:
        """Guarda entidades cambiadas y detecta conflictos de edición remota."""
        self.last_merges = 0
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
            current = self.canonical_payload_bytes(path)
            baseline = self.baseline.get(path)
            local_changed = serialized != baseline
            external_changed = path in self.baseline and current != baseline
            if not force and local_changed and external_changed:
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
                    self.last_merges += 1
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
            current = self.canonical_payload_bytes(path)
            if path in self.baseline and current != self.baseline[path]:
                conflicts.append(path)
        if conflicts:
            raise StorageConflictError(list(dict.fromkeys(conflicts)))
        for path, data in files.items():
            if force or _json_bytes(data) != self.baseline.get(path):
                self.write_payload(path, data)
        for path in removed:
            path.unlink(missing_ok=True)
        state.chapter_ids = chapter_ids
        self._capture(set(files) | set(removed))

    def set_crypto(self, crypto: ProjectCrypto | None) -> None:
        """Activa o retira protección y convierte las bibliotecas asociadas."""
        libraries = []
        library_root = self.root / "bibliotecas"
        if library_root.is_dir():
            for module_dir in library_root.iterdir():
                if not module_dir.is_dir():
                    continue
                for source in module_dir.iterdir():
                    if source.is_file() and not source.name.endswith(".enc"):
                        libraries.append((module_dir.name, source))
                    elif source.is_file() and source.name.endswith(".enc"):
                        filename = source.name.removesuffix(".enc")
                        materialized = self.materialize_library(
                            module_dir.name, filename
                        )
                        if materialized is not None:
                            libraries.append((module_dir.name, materialized))
                    elif source.is_dir() and source.name == "docs":
                        libraries.append((module_dir.name, source))
                encrypted_docs = module_dir / "docs.enc"
                if encrypted_docs.is_file():
                    docs = self.materialize_docs(module_dir.name)
                    if docs is not None:
                        libraries.append((module_dir.name, docs))
        if library_root.exists():
            shutil.rmtree(library_root)
        self.crypto = crypto
        for module, source in libraries:
            if source.is_dir():
                self.install_docs(module, source)
            else:
                self.install_library(module, source)

    def changed_paths(self) -> list[Path]:
        """Devuelve entidades modificadas externamente desde la última foto."""
        changed = []
        for path, baseline in list(self.baseline.items()):
            if self.canonical_payload_bytes(path) != baseline:
                changed.append(path)
        return changed

    def load_item(self, storage_id: str):
        """Recarga una sola partida externa."""
        path = self.item_path(storage_id)
        data = self.read_payload(path)
        self.baseline[path] = self.canonical_payload_bytes(path)
        return item_from_data(data)

    @classmethod
    def create_from_payload(
        cls,
        root: Path,
        state: Any,
        default_settings: ReportSettings,
        crypto: ProjectCrypto | None = None,
    ) -> CollaborativeProjectStore:
        """Crea una carpeta colaborativa desde el estado actual."""
        store = cls(root, default_settings, crypto)
        state.chapter_ids = dict(getattr(state, "chapter_ids", {}))
        store.save(state)
        return store

    @property
    def is_encrypted(self) -> bool:
        """Indica si la sesión dispone de una clave autenticada."""
        return self.crypto is not None

    def install_library(self, module: str, source: Path) -> Path:
        """Copia una biblioteca al proyecto mediante escritura atómica."""
        if not source.is_file():
            raise ValueError(f"No existe la biblioteca seleccionada: {source}")
        if self.crypto is None:
            target = self.root / "bibliotecas" / module / source.name
        else:
            target = self.root / "bibliotecas" / module / (source.name + ".enc")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            if self.crypto is None:
                shutil.copyfile(source, temporary)
            else:
                with temporary.open("w", encoding="utf-8") as output:
                    json.dump(
                        self.crypto.encrypt_bytes(source.read_bytes()),
                        output,
                        ensure_ascii=False,
                    )
                    output.flush()
                    os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def install_docs(self, module: str, source: Path) -> Path:
        """Copia documentos auxiliares conservando una copia completa."""
        if not source.is_dir():
            raise ValueError(f"No existe la carpeta seleccionada: {source}")
        target = self.root / "bibliotecas" / module / (
            "docs.enc" if self.crypto is not None else "docs"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(prefix="gdp-docs-"))
        archive = temporary_dir / "docs.zip"
        try:
            with ZipFile(archive, "w") as output:
                for path in source.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(source).as_posix())
            if self.crypto is None:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    shutil.rmtree(target)
                with ZipFile(archive) as bundle:
                    bundle.extractall(target)
            else:
                encrypted = self.crypto.encrypt_bytes(archive.read_bytes())
                atomic_json_write(target, encrypted)
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)
        return target

    def materialize_library(self, module: str, filename: str) -> Path | None:
        """Descifra una biblioteca a una caché temporal de esta sesión."""
        folder = self.root / "bibliotecas" / module
        if self.crypto is None:
            public = folder / filename
            return public if public.is_file() else None
        source = folder / (filename + ".enc")
        if not source.is_file():
            return None
        signature = source.stat().st_mtime_ns
        key = module, filename
        cached = self._materialized.get(key)
        if cached is not None and cached[0] == signature and cached[1].is_file():
            return cached[1]
        target = (
            Path(tempfile.gettempdir())
            / "gestor-proyectos-libraries"
            / uuid4().hex
            / module
            / filename
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.crypto.decrypt_bytes(_read_json(source)))
        self._materialized[key] = signature, target
        return target

    def materialize_docs(self, module: str) -> Path | None:
        """Descifra y extrae documentos auxiliares de forma segura."""
        folder = self.root / "bibliotecas" / module
        if self.crypto is None:
            public = folder / "docs"
            return public if public.is_dir() else None
        source = folder / "docs.enc"
        if not source.is_file():
            return None
        signature = source.stat().st_mtime_ns
        key = module, "docs"
        cached = self._materialized.get(key)
        if cached is not None and cached[0] == signature and cached[1].is_dir():
            return cached[1]
        base = (
            Path(tempfile.gettempdir())
            / "gestor-proyectos-libraries"
            / uuid4().hex
            / module
        )
        base.mkdir(parents=True, exist_ok=True)
        archive_path = base / "docs.zip"
        archive_path.write_bytes(self.crypto.decrypt_bytes(_read_json(source)))
        destination = base / "docs"
        with ZipFile(archive_path) as archive:
            for info in archive.infolist():
                path = Path(info.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("La biblioteca compartida contiene rutas no válidas.")
            archive.extractall(destination)
        self._materialized[key] = signature, destination
        return destination


class CollaborativeWatchService(QObject):
    """Sondeo incremental compatible con carpetas locales y sincronizadas."""

    files_changed = Signal(list)
    presence_changed = Signal(list)

    def __init__(
        self, store: CollaborativeProjectStore, *, interval_ms: int = 2500
    ) -> None:
        """Programa comprobaciones sin hooks del sistema operativo."""
        super().__init__()
        self.store = store
        self.lock_snapshot = self._lock_snapshot()
        self.presence_snapshot = self._presence_snapshot()
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
        presence = self._presence_snapshot()
        if presence != self.presence_snapshot:
            self.presence_snapshot = presence
            self.presence_changed.emit(presence)
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

    def _presence_snapshot(self) -> list[dict[str, str]]:
        """Devuelve usuarios vivos y elimina registros huérfanos."""
        presence_dir = self.store.root / "presencia"
        if not presence_dir.is_dir():
            return []
        now = datetime.now(timezone.utc)
        active = []
        for path in presence_dir.glob("*.json"):
            try:
                data = self.store.read_payload(path)
                timestamp = datetime.fromisoformat(str(data["timestamp"]))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if now - timestamp > timedelta(seconds=45):
                    path.unlink(missing_ok=True)
                    continue
                active.append({
                    "id": path.stem,
                    "nickname": str(data.get("nickname", "Sin apodo")),
                    "host": str(data.get("host", "")),
                    "timestamp": timestamp.isoformat(),
                })
            except (OSError, KeyError, TypeError, ValueError):
                continue
        return sorted(
            active, key=lambda row: row["nickname"].casefold()
        )

    def heartbeat(
        self, session_id: str, nickname: str, host: str
    ) -> None:
        """Publica la presencia de esta sesión."""
        directory = self.store.root / "presencia"
        directory.mkdir(parents=True, exist_ok=True)
        self.store.write_payload(
            directory / f"{session_id}.json",
            {
                "nickname": nickname,
                "host": host,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    def leave(self, session_id: str) -> None:
        """Retira la presencia al cerrar o salir del proyecto."""
        (self.store.root / "presencia" / f"{session_id}.json").unlink(
            missing_ok=True
        )
