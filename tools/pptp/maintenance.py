"""Recuperación y acortado seguro de nombres de las fichas PPTP."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import unicodedata
from zipfile import ZipFile

from tools._library_archive import update_library
from tools._library_editor import _signature, library_members
from .library import ZipLibrary


MAX_SHEET_NAME = 60
MAX_SHEET_PATH = 175


def initialize_library_history(archive_path: Path) -> int:
    """Añade el registro inicial a todos los Markdown mediante una actualización atómica."""
    from tools._library_history import initialize_history

    signature = _signature(archive_path)
    members = library_members(archive_path)
    updates = {}
    with ZipFile(archive_path) as source:
        for name in members:
            if not name.lower().endswith(".md"):
                continue
            original = source.read(name).decode("utf-8-sig")
            updated = initialize_history(original)
            if updated != original:
                updates[name] = updated
    if updates:
        update_library(archive_path, signature, updates=updates)
    return len(updates)


def concise_title(title: str) -> str:
    """Elimina la repetición entre familia y variante sin perder el contexto distintivo."""
    parts = title.split(" - ")
    if len(parts) != 2 or parts[1] == "Común":
        return title
    family, variant = (
        part.replace(
            "FONTANERIA Y REDES INTERIORES DE SUMINISTRO DE AGUA", "Fontanería"
        ).replace(
            "ABASTECIMIENTO EN REDES EXTERIORES DE AGUA", "Abastecimiento de agua"
        )
        for part in parts
    )
    compact = family + " - " + variant
    demolition = family.startswith("Retirada de ")
    context = family.removeprefix("Retirada de ")

    def words(value: str) -> set[str]:
        """Compara palabras significativas ignorando tildes y plurales simples."""
        value = "".join(
            char for char in unicodedata.normalize("NFKD", value.casefold())
            if not unicodedata.combining(char)
        )
        return {
            word.rstrip("s") for word in re.findall(r"[a-z0-9]+", value)
            if word not in {"de", "del", "la", "las", "el", "los", "y", "en", "con"}
        }

    family_words, variant_words = words(context), words(variant)
    if family_words and family_words == variant_words:
        result = variant if context.isupper() else context
    elif family_words and family_words < variant_words:
        result = variant
    else:
        result = compact
    if result != compact:
        result = ("Retirada de " + result[0].lower() + result[1:]) if demolition else result
    if result.isupper():
        result = result[0] + result[1:].lower()
    return result


def repair_library(archive_path: Path, backup_path: Path) -> tuple[int, int]:
    """Recupera originales, limita rutas y elimina títulos redundantes conservando decisiones."""
    signature = _signature(archive_path)
    members = library_members(archive_path)
    restored: dict[str, bytes] = {}
    with ZipFile(archive_path) as source, ZipFile(backup_path) as backup:
        catalog = json.loads(source.read("UNI/catalogo.json"))
        for row in catalog["fichas"]:
            member = "UNI/" + row["archivo"]
            if member in members:
                continue
            if member not in backup.namelist():
                raise ValueError(f"No hay copia original de la ficha ausente: {member}")
            content = backup.read(member)
            if sha256(content).hexdigest() != row.get("sha256"):
                raise ValueError(f"La copia no coincide con la ficha del catálogo: {member}")
            restored[member] = content
    renames = {}
    titles = {}
    updates = {}
    occupied = {name.casefold() for name in members} | {name.casefold() for name in restored}
    for row in catalog["fichas"]:
        old = "UNI/" + row["archivo"]
        path = PurePosixPath(old)
        title = concise_title(row["titulo"])
        if title != row["titulo"]:
            titles[old] = title
        limit = min(MAX_SHEET_NAME, MAX_SHEET_PATH - len(path.parent.as_posix()) - 1)
        if len(path.name) <= limit and title == row["titulo"]:
            continue
        prefix = row["codigo_uni"] + "-"
        if limit < len(prefix) + 12:
            raise ValueError(f"La carpeta es demasiado larga para un nombre descriptivo: {path.parent}")
        description = title
        description = re.sub(r"\s+-\s+", " ", description)
        budget = limit - len(prefix) - len(".md")
        shortened = description if len(description) <= budget else description[:budget].rsplit(" ", 1)[0].rstrip(" .-")
        if not shortened:
            shortened = description[:budget].rstrip(" .-")
        name = prefix + shortened + ".md"
        new = (path.parent / name).as_posix()
        if new == old:
            continue
        if new.casefold() in occupied:
            suffix = "-" + sha256(old.encode()).hexdigest()[:8]
            name = prefix + shortened[:budget - len(suffix)].rstrip(" .-") + suffix + ".md"
            new = (path.parent / name).as_posix()
        if new.casefold() in occupied:
            raise ValueError(f"Colisión al acortar el nombre de ficha: {new}")
        occupied.add(new.casefold())
        renames[old] = new
    titles = {renames.get(path, path): title for path, title in titles.items()}
    with ZipFile(archive_path) as source:
        for row in catalog["fichas"]:
            old = "UNI/" + row["archivo"]
            title = titles.get(renames.get(old, old))
            if title is None:
                continue
            content = restored[old] if old in restored else source.read(old)
            original = content.decode("utf-8-sig")
            updated = re.sub(r"^# [^\n]+", lambda _: "# " + title, original, count=1, flags=re.MULTILINE)
            if updated != original:
                updates[old] = updated
    if not restored and not renames and not titles:
        ZipLibrary(archive_path, archive_path.parent / "docs")
        return 0, 0
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".pptp-repair-", suffix=".zip", dir=archive_path.parent, delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with ZipFile(archive_path) as source, ZipFile(temporary_path, "w") as target:
            target.comment = source.comment
            for info in source.infolist():
                target.writestr(info, source.read(info))
            for member, content in restored.items():
                target.writestr(member, content)
        if renames or titles:
            update_library(
                temporary_path, _signature(temporary_path), renames=renames,
                preserve_titles=True,
                titles=titles, updates=updates,
            )
        library = ZipLibrary(temporary_path, archive_path.parent / "docs")
        with ZipFile(temporary_path) as candidate:
            if candidate.testzip() is not None:
                raise ValueError("La biblioteca reparada no supera la comprobación CRC.")
        if any(
            len(entry.path) > MAX_SHEET_PATH or len(PurePosixPath(entry.path).name) > MAX_SHEET_NAME
            for entry in library.entries.values()
        ):
            raise ValueError("La biblioteca todavía contiene rutas demasiado largas.")
        if _signature(archive_path) != signature:
            raise ValueError("La biblioteca ha cambiado durante la reparación; no se ha sustituido.")
        os.replace(temporary_path, archive_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return len(restored), len(renames)
