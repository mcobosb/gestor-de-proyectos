"""Creación y renombrado atómicos de fichas ZIP y sus referencias de catálogo."""
from __future__ import annotations

from copy import copy, deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import tempfile
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED


def sheet_name(value: str) -> tuple[str, str, str]:
    """Valida un nombre de ficha y devuelve nombre, código BCCA y descripción."""
    name = value.strip()
    if not name.lower().endswith(".md"):
        name += ".md"
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError("El nombre no puede contener rutas ni caracteres reservados.")
    match = re.fullmatch(r"(\d{2}[A-Z]*)-(.+)\.md", name, re.IGNORECASE)
    if not match or not match[2].strip(" ."):
        raise ValueError("Usa el formato CÓDIGO-Descripción.md, por ejemplo 01RPN-Piedra natural.md.")
    code, title = match[1].upper(), match[2].strip(" .")
    return f"{code}-{title}.md", code, title


def _update_links(text: str, member: str, renames: dict[str, str]) -> str:
    """Actualiza destinos Markdown relativos, incluidos los codificados con porcentajes."""
    def replace(match: re.Match[str]) -> str:
        """Resuelve un enlace local y conserva su fragmento y descripción."""
        target = match[2]
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            return match[0]
        old = posixpath.normpath(posixpath.join(posixpath.dirname(member), unquote(parsed.path)))
        if old not in renames:
            return match[0]
        path = posixpath.relpath(renames[old], posixpath.dirname(renames.get(member, member)))
        suffix = ("?" + parsed.query if parsed.query else "") + ("#" + parsed.fragment if parsed.fragment else "")
        return match[1] + quote(path, safe="/._-") + suffix + match[3]

    return re.sub(r"(\]\()([^)\s]+)(\))", replace, text)


def _catalog_changes(
    source: ZipFile, renames: dict[str, str], additions: dict[str, str],
    edits: dict[str, bytes],
    replacements: dict[str, list[str]] | None = None,
) -> dict[str, bytes]:
    """Actualiza catálogo PPTP, alias históricos e índice sin cambiar identificadores."""
    replacements = replacements or {}
    catalogs = [name for name in source.namelist() if PurePosixPath(name).name == "catalogo.json"]
    changes: dict[str, bytes] = {}
    for member in catalogs:
        catalog = json.loads(source.read(member))
        if not isinstance(catalog, dict) or catalog.get("formato") != "biblioteca-fichas-uni":
            continue
        directory = PurePosixPath(member).parent
        rows = catalog["fichas"]
        retired = catalog.setdefault("fichas_sustituidas", [])
        for record in retired:
            record["destinos"] = [
                renames.get(path, path) for path in record["destinos"]
            ]
        for row in rows:
            old = (directory / row["archivo"]).as_posix()
            if old in replacements:
                retired.append({
                    "id": row["id"], "codigo_uni": row["codigo_uni"],
                    "rutas": [old, *row.get("rutas_anteriores", [])],
                    "destinos": replacements[old],
                })
        rows[:] = [
            row for row in rows
            if (directory / row["archivo"]).as_posix() not in replacements
        ]
        for row in rows:
            old = (directory / row["archivo"]).as_posix()
            if old not in renames:
                continue
            new = renames[old]
            _, code, title = sheet_name(PurePosixPath(new).name)
            parent_code = PurePosixPath(new).parent.name.split("_", 1)[0].upper()
            if not code.startswith(parent_code):
                raise ValueError("El código de la ficha debe pertenecer a la carpeta BCCA seleccionada.")
            aliases = row.setdefault("rutas_anteriores", [])
            previous = "UNI/" + row["archivo"]
            if previous not in aliases:
                aliases.append(previous)
            aliases[:] = [alias for alias in aliases if alias != "UNI/" + PurePosixPath(new).relative_to(directory).as_posix()]
            row.update(archivo=PurePosixPath(new).relative_to(directory).as_posix(),
                       codigo_uni=code, titulo=title)
        for name, content in additions.items():
            path = PurePosixPath(name)
            if not path.is_relative_to(directory) or path.parent == directory:
                continue
            _, code, title = sheet_name(path.name)
            family = path.parent.name.split("_", 1)[0].upper()
            if not re.fullmatch(r"\d{2}[A-Z]*", family) or not code.startswith(family):
                raise ValueError("El código de la ficha debe pertenecer a la carpeta BCCA seleccionada.")
            siblings = [
                row for row in rows
                if PurePosixPath(row["archivo"]).parent == path.relative_to(directory).parent
            ]
            row = {
                "id": f"UNI-{code}-{uuid4().hex}", "codigo_uni": code, "titulo": title,
                "archivo": path.relative_to(directory).as_posix(), "tipo": "pptp",
                "seleccion": {"requiere_confirmacion": True},
                "sha256": sha256(content.encode("utf-8")).hexdigest(),
            }
            if siblings:
                for key in ("capitulo", "subcapitulo", "subsubcapitulo"):
                    if key in siblings[0]:
                        row[key] = deepcopy(siblings[0][key])
            rows.append(row)
        catalog["total_fichas"] = len(rows)
        codes: dict[str, list[str]] = {}
        for row in rows:
            codes.setdefault(row["codigo_uni"], []).append(row["id"])
        for row in rows:
            same = codes[row["codigo_uni"]]
            selection = row.setdefault("seleccion", {})
            selection["varias_fichas_mismo_codigo"] = len(same) > 1
            selection["ids_mismo_codigo"] = same if len(same) > 1 else []
            member_path = (directory / row["archivo"]).as_posix()
            old_path = next((old for old, new in renames.items() if new == member_path), member_path)
            if member_path in additions:
                content = additions[member_path].encode("utf-8")
            else:
                content = edits.get(old_path)
                if content is None:
                    content = source.read(old_path)
            row["sha256"] = sha256(content).hexdigest()
        changes[member] = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if not renames and not additions and not replacements:
            continue
        index_member = (directory / catalog["indice"]).as_posix()
        original = source.read(index_member).decode("utf-8-sig")
        by_member = {(directory / row["archivo"]).as_posix(): row for row in rows}
        index_lines = []
        indexed = set()
        for line in original.splitlines():
            match = re.match(r"\|\s*\d{2}[A-Z]*\s*\|\s*\[([^]]*)\]\(([^)]+)\)(.*)", line)
            if match:
                old_member = posixpath.normpath(posixpath.join(directory.as_posix(), unquote(match[2])))
                if old_member in replacements:
                    continue
                new_member = renames.get(old_member, old_member)
                row = by_member.get(new_member)
                if row is not None:
                    indexed.add(new_member)
                    line = (
                        f"| {row['codigo_uni']} | [{row['titulo'].replace('|', '/').replace(']', '')}]"
                        f"({quote(row['archivo'], safe='/._-')}){match[3]}"
                    )
            index_lines.append(line)
        for name, row in by_member.items():
            if name not in indexed:
                index_lines.append(
                    f"| {row['codigo_uni']} | [{row['titulo'].replace('|', '/').replace(']', '')}]"
                    f"({quote(row['archivo'], safe='/._-')}) | Revisar aplicación. |"
                )
        index = "\n".join(index_lines) + "\n"
        changes[index_member] = index.encode("utf-8")
    return changes


def update_library(
    archive_path: Path, expected_signature: tuple[int, int], *,
    renames: dict[str, str] | None = None, additions: dict[str, str] | None = None,
    updates: dict[str, str] | None = None,
    replacements: dict[str, list[str]] | None = None,
    allow_new_folders: bool = False,
) -> tuple[int, int]:
    """Crea o renombra fichas y actualiza sus enlaces mediante un único reemplazo ZIP."""
    from ._library_editor import (
        MAX_MARKDOWN_BYTES, _signature, _validate_member_name, library_members,
    )

    renames, additions, updates = renames or {}, additions or {}, updates or {}
    replacements = replacements or {}
    if _signature(archive_path) != expected_signature:
        raise ValueError("La biblioteca ha cambiado desde que se abrió. Ciérrala y vuelve a intentarlo.")
    members = library_members(archive_path)
    names = set(members)
    if set(replacements) & (set(renames) | set(updates)):
        raise ValueError("No se puede editar o renombrar una ficha que se va a sustituir.")
    for old, targets in replacements.items():
        if old not in names or not old.lower().endswith(".md") or not targets:
            raise ValueError(f"Sustitución de ficha no válida: {old}")
        if any(target not in names | set(additions) or target in replacements for target in targets):
            raise ValueError(f"El destino de la sustitución no existe: {old}")
    if any(name not in names or not name.lower().endswith(".md") for name in updates):
        raise ValueError("Solo se pueden actualizar fichas Markdown existentes.")
    for old in renames:
        if old not in names or PurePosixPath(old).suffix.casefold() != ".md":
            raise ValueError(f"No existe la ficha Markdown: {old}")
    destinations = [renames.get(name, name) for name in members] + list(additions)
    if len({name.casefold() for name in destinations}) != len(destinations):
        raise ValueError("Ya existe una ficha con ese nombre.")
    for name in [*renames.values(), *additions]:
        _validate_member_name(name)
        sheet_name(PurePosixPath(name).name)
        parent = PurePosixPath(name).parent.as_posix()
        if parent == "." or (not allow_new_folders and not any(
            PurePosixPath(old).parent.as_posix() == parent and old.lower().endswith(".md")
            for old in names
        )):
            raise ValueError("Selecciona una carpeta terminal que ya contenga fichas Markdown.")
        if not allow_new_folders and name in additions and any(
            old.startswith(parent + "/") and "/" in old[len(parent) + 1:] for old in names
        ):
            raise ValueError("La carpeta seleccionada no es terminal.")
    for old, new in renames.items():
        if PurePosixPath(old).parent != PurePosixPath(new).parent:
            raise ValueError("Renombrar una ficha no permite cambiarla de carpeta.")
    temporary_path = None
    try:
        with ZipFile(archive_path) as source:
            changes: dict[str, bytes] = {}
            redirects = {
                old: targets[0] if len(targets) == 1 else "UNI/INDICE_FICHAS.md"
                for old, targets in replacements.items()
            }
            for name in members:
                if name in replacements:
                    continue
                if name.lower().endswith(".md"):
                    info = source.getinfo(name)
                    if info.file_size > MAX_MARKDOWN_BYTES or info.flag_bits & 1:
                        raise ValueError(f"Archivo Markdown demasiado grande o cifrado: {name}")
                    original = source.read(name).decode("utf-8-sig")
                    updated = _update_links(updates.get(name, original), name, {**redirects, **renames})
                    if updated != original and name not in changes:
                        changes[name] = updated.encode("utf-8")
            changes.update(_catalog_changes(source, renames, additions, changes, replacements))
            for name, content in additions.items():
                changes[name] = content.encode("utf-8")
            if any(len(content) > MAX_MARKDOWN_BYTES for content in changes.values()):
                raise ValueError("Un archivo de biblioteca supera el tamaño máximo permitido.")
            with tempfile.NamedTemporaryFile(
                prefix=f".{archive_path.name}.", suffix=".tmp",
                dir=archive_path.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as target:
                target.comment = source.comment
                for info in source.infolist():
                    name = info.filename
                    if name in replacements:
                        continue
                    output = copy(info)
                    output.filename = renames.get(name, name)
                    if name in changes:
                        target.writestr(output, changes.pop(name))
                    else:
                        with source.open(info) as original, target.open(output, "w") as destination:
                            while chunk := original.read(1024 * 1024):
                                destination.write(chunk)
                for name, content in changes.items():
                    target.writestr(name, content)
            from .pptp.library import ZipLibrary

            if any(PurePosixPath(name).name == "catalogo.json" for name in members):
                # El catálogo completo se valida antes de sustituir la biblioteca original.
                with ZipFile(temporary_path) as candidate:
                    is_pptp = any(
                        json.loads(candidate.read(name)).get("formato") == "biblioteca-fichas-uni"
                        for name in members if PurePosixPath(name).name == "catalogo.json"
                    )
                if is_pptp:
                    ZipLibrary(temporary_path, archive_path.parent / "docs")
        if _signature(archive_path) != expected_signature:
            raise ValueError("La biblioteca ha cambiado durante la edición. No se guardaron los cambios.")
        os.replace(temporary_path, archive_path)
        temporary_path = None
        return _signature(archive_path)
    except (BadZipFile, KeyError, OSError, UnicodeError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f"No se pudo modificar la biblioteca: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
