"""Editor reutilizable de archivos Markdown dentro de bibliotecas ZIP."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path, PurePosixPath
import tempfile
from typing import Callable
from zipfile import BadZipFile, ZipFile

from ._library_markdown import document_markdown, informational_ranges
from ._library_history import add_revision, read_revisions, revision_values

MAX_MARKDOWN_BYTES = 16 * 1024 * 1024


def _validate_member_name(name: str) -> str:
    """Rechaza nombres ambiguos o externos antes de mostrarlos o escribirlos."""
    relative = name[:-1] if name.endswith("/") else name
    path = PurePosixPath(relative)
    if (not relative or "\\" in relative or path.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or ":" in relative or "\x00" in relative or path.as_posix() != relative):
        raise ValueError(f"Ruta de biblioteca no válida: {name}")
    return name


def _signature(archive_path: Path) -> tuple[int, int]:
    """Obtiene el tamaño y fecha de modificación para detectar escrituras externas."""
    info = archive_path.stat()
    return info.st_size, info.st_mtime_ns


def library_members(archive_path: Path) -> list[str]:
    """Devuelve archivos del ZIP tras validar rutas y entradas duplicadas."""
    try:
        with ZipFile(archive_path) as archive:
            seen = set()
            for info in archive.infolist():
                name = _validate_member_name(info.filename)
                if name in seen:
                    raise ValueError(f"Entrada duplicada en la biblioteca: {name}")
                seen.add(name)
            return sorted((name for name in seen if not name.endswith("/")), key=str.casefold)
    except (BadZipFile, OSError) as exc:
        raise ValueError(f"No se pudo leer la biblioteca {archive_path}: {exc}") from exc


def read_markdown(archive_path: Path, member_name: str) -> str:
    """Lee un archivo Markdown UTF-8 sin extraerlo del ZIP."""
    member_name = _validate_member_name(member_name)
    if PurePosixPath(member_name).suffix.casefold() != ".md":
        raise ValueError("Solo se pueden abrir archivos Markdown.")
    try:
        with ZipFile(archive_path) as archive:
            info = archive.getinfo(member_name)
            if info.is_dir() or info.file_size > MAX_MARKDOWN_BYTES or info.flag_bits & 1:
                raise ValueError(f"Archivo Markdown demasiado grande o cifrado: {member_name}")
            with archive.open(info) as source:
                content = source.read(MAX_MARKDOWN_BYTES + 1)
            if len(content) > MAX_MARKDOWN_BYTES:
                raise ValueError(f"Archivo Markdown demasiado grande: {member_name}")
            return content.decode("utf-8-sig")
    except (BadZipFile, KeyError, OSError, UnicodeError) as exc:
        raise ValueError(f"No se pudo leer {member_name}: {exc}") from exc


def write_markdown(
    archive_path: Path, member_name: str, content: str, expected_signature: tuple[int, int],
) -> tuple[int, int]:
    """Reemplaza un Markdown y conserva el resto del ZIP mediante sustitución atómica."""
    member_name = _validate_member_name(member_name)
    if PurePosixPath(member_name).suffix.casefold() != ".md":
        raise ValueError("Solo se pueden guardar archivos Markdown.")
    informational_ranges(content)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_MARKDOWN_BYTES:
        raise ValueError("El archivo Markdown supera el tamaño máximo permitido.")
    if _signature(archive_path) != expected_signature:
        raise ValueError("La biblioteca ha cambiado desde que se abrió. Ciérrala y vuelve a intentarlo.")

    temporary_path = None
    try:
        with ZipFile(archive_path) as source:
            infos = source.infolist()
            names = [_validate_member_name(info.filename) for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("La biblioteca contiene entradas duplicadas.")
            if member_name not in names:
                raise ValueError(f"El archivo ya no existe en la biblioteca: {member_name}")
            selected = infos[names.index(member_name)]
            if selected.is_dir() or selected.flag_bits & 1:
                raise ValueError(f"El archivo Markdown no se puede modificar: {member_name}")
            if selected.file_size > MAX_MARKDOWN_BYTES:
                raise ValueError(f"Archivo Markdown demasiado grande: {member_name}")
            from ._library_archive import _catalog_changes

            changes = {member_name: encoded}
            changes.update(_catalog_changes(source, {}, {}, changes))
            with tempfile.NamedTemporaryFile(
                prefix=f".{archive_path.name}.", suffix=".tmp",
                dir=archive_path.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            with ZipFile(temporary_path, "w") as target:
                target.comment = source.comment
                for info, name in zip(infos, names):
                    if name in changes:
                        target.writestr(info, changes[name])
                    else:
                        with source.open(info) as original:
                            with target.open(info, "w") as copy:
                                while chunk := original.read(1024 * 1024):
                                    copy.write(chunk)
        if _signature(archive_path) != expected_signature:
            raise ValueError("La biblioteca ha cambiado durante la edición. No se guardaron los cambios.")
        os.replace(temporary_path, archive_path)
        temporary_path = None
        return _signature(archive_path)
    except (BadZipFile, KeyError, OSError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f"No se pudo guardar {member_name}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
