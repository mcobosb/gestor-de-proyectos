"""Reorganiza las fichas PPTP con una ficha por tercera letra BCCA."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import unicodedata
from zipfile import ZipFile

from bc3_gui import Bc3Dataset, Concept
from tools._library_archive import sheet_name, update_library
from tools._library_editor import _signature
from .library import ZipLibrary


def _filename(code: str, title: str) -> str:
    """Construye nombres legibles compatibles con Windows y el editor."""
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).strip(" .")
    title = re.sub(r"\s+", " ", title)
    if not title:
        raise ValueError(f"La clasificación BCCA {code} no contiene descripción.")
    return sheet_name(f"{code}-{title[0].upper() + title[1:]}.md")[0]


def _group_name(code: str, title: str) -> str:
    """Nombra una carpeta de agrupación sin añadir un nivel para la última letra."""
    title = unicodedata.normalize("NFKD", title)
    title = "".join(char for char in title if not unicodedata.combining(char))
    return code + "_" + re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").upper()


def _scope_sheet(
    code: str, group: Concept, family: Concept | None, units: list[Concept],
    common_paths: list[str], parent: PurePosixPath,
) -> str:
    """Delimita el alcance con descripciones BC3, sin inventar prescripciones técnicas."""
    from urllib.parse import quote

    label = f"{family.summary} - {group.summary}" if family is not None else group.summary
    title = f"Retirada de {label.lower()}" if code.startswith("01") else label
    lines = [
        f"# {title[0].upper() + title[1:]}", "",
        f"**Clasificación BCCA:** `{code}`.",
        "**Fuente:** BCCA 2023, revisión V02, clasificación y descripciones BC3.", "",
        "## Alcance específico", "",
        f"Esta ficha corresponde exclusivamente a **{group.summary}**"
        + (f", dentro de **{family.summary}**." if family is not None else "."),
        f"Aplicable a las partidas cuyo código BCCA comienza por `{code}`. "
        "No extiende su alcance a las variantes con una tercera letra distinta.", "",
    ]
    if common_paths:
        lines += ["## Condiciones comunes de la familia", ""]
        for path in common_paths:
            relative = PurePosixPath(path).relative_to(parent).as_posix()
            lines.append(f"- [{PurePosixPath(path).stem}]({quote(relative, safe='/._-')})")
        lines += [
            "",
            "Las condiciones comunes se aplican solo en lo compatible con el material, "
            "la operación y las inclusiones de la partida aprobada. Las variantes "
            "específicas no se consideran intercambiables.",
            "",
        ]
    lines += ["## Unidades de referencia BCCA", ""]
    if units:
        for unit in units:
            lines += [f"### {unit.code} - {unit.summary}", ""]
            if unit.unit:
                lines += [f"**Unidad:** {unit.unit}.", ""]
            if unit.text.strip():
                lines += [unit.text.strip(), ""]
            else:
                lines += [
                    "El BC3 de origen no aporta una descripción ampliada para esta unidad. "
                    "Completar y revisar sus condiciones en el proyecto.", "",
                ]
    else:
        lines += [
            "El BC3 de origen define esta clasificación pero no contiene partidas "
            "desarrolladas bajo este código. La ficha identifica su alcance; deben "
            "completarse sus prescripciones antes de usarla como condición contractual.", "",
        ]
    lines += [
        "## Aplicación, medición y valoración", "",
        "Seleccionar la unidad concreta del presupuesto y comprobar sus características, "
        "inclusiones, exclusiones y criterio de medición. Las descripciones anteriores son "
        "referencias alternativas del BCCA, no prestaciones acumulables ni una orden de "
        "ejecutar todas las variantes.", "",
        "La ejecución, los controles y la recepción deben ajustarse a la documentación "
        "contractual, a las condiciones comunes aplicables y a la normativa vigente. "
        "Esta clasificación no sustituye la revisión técnica del pliego.", "",
    ]
    return "\n".join(lines)


def reorganize_library(archive_path: Path, source_path: Path) -> tuple[int, int]:
    """Renombra las fichas originales y completa todas las terceras letras del BC3."""
    if archive_path.resolve() == source_path.resolve():
        raise ValueError("El BC3 de origen no puede coincidir con la biblioteca.")
    dataset = Bc3Dataset(source_path)
    library = ZipLibrary(archive_path, archive_path.parent / "docs")
    signature = _signature(archive_path)
    entries = list(library.entries.values())
    parents: dict[str, PurePosixPath] = {}
    for entry in entries:
        path = PurePosixPath(entry.path).parent
        for folder in path.parents:
            code = folder.name.split("_", 1)[0]
            if re.fullmatch(r"\d{2}[A-Z]*", code):
                parents.setdefault(code, folder)
        code = path.name.split("_", 1)[0]
        parents.setdefault(code, path)
    groups = {
        concept.code.rstrip("#"): concept for concept in dataset.concepts.values()
        if re.fullmatch(r"\d{2}[A-Z]{3}#", concept.code)
    }
    if not groups:
        raise ValueError("El BC3 no contiene clasificaciones BCCA de tres letras.")
    renames = {}
    updates = {}
    common: dict[str, list[str]] = {}
    with ZipFile(archive_path) as archive:
        for entry in entries:
            # Las fichas específicas ya migradas no vuelven a convertirse en comunes.
            if len(entry.code) >= 5:
                continue
            family = dataset.concepts.get(entry.code + "#")
            if family is None:
                raise ValueError(f"El código de la ficha no existe en el BCCA: {entry.code}")
            title = entry.title.removesuffix(" - Común")
            if title.casefold() in {"varios", "continuos", "ligeros", "maderas", "acero", "hormigón"}:
                parent_code = entry.code[:-1]
                context = dataset.concepts.get(parent_code + "#")
                if context is not None:
                    title = context.summary + " - " + title
            title += " - Común"
            new = (PurePosixPath(entry.path).parent / _filename(entry.code, title)).as_posix()
            if new != entry.path:
                renames[entry.path] = new
            original = archive.read(entry.path).decode("utf-8-sig")
            updated = re.sub(r"^# [^\n]+", "# " + title[0].upper() + title[1:], original, count=1)
            if updated != original:
                updates[entry.path] = updated
            common.setdefault(PurePosixPath(new).parent.as_posix(), []).append(new)
    existing_codes = {entry.code for entry in entries if len(entry.code) >= 5}
    additions = {}
    units_by_group: dict[str, list[Concept]] = {}
    for concept in dataset.concepts.values():
        if not concept.code.endswith("#") and concept.code[:5] in groups:
            units_by_group.setdefault(concept.code[:5], []).append(concept)
    for code, group in sorted(groups.items()):
        if code in existing_codes:
            continue
        family_code = code[:4]
        parent = parents.get(family_code)
        family = dataset.concepts.get(family_code + "#")
        if parent is None:
            ancestor = next((parents[code[:length]] for length in (3, 2) if code[:length] in parents), None)
            if ancestor is None:
                raise ValueError(f"No existe carpeta para la clasificación BCCA: {code}")
            # El BC3 contiene 18RPA/B/C pero no define 18RP: se agrupan por prefijo,
            # sin inventar una denominación BCCA para ese nivel ausente.
            parent = ancestor / _group_name(
                family_code, family.summary if family is not None else "Agrupación por código",
            )
            parents[family_code] = parent
        label = f"{family.summary} - {group.summary}" if family is not None else group.summary
        title = f"Retirada de {label.lower()}" if code.startswith("01") else label
        member = (parent / _filename(code, title)).as_posix()
        additions[member] = _scope_sheet(
            code, group, family, sorted(units_by_group.get(code, []), key=lambda unit: unit.code),
            common.get(parent.as_posix(), []), parent,
        )
    if renames or additions or updates:
        update_library(
            archive_path, signature, renames=renames, additions=additions,
            updates=updates, allow_new_folders=True,
        )
    with ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("La biblioteca generada no supera la comprobación CRC.")
    return len(renames), len(additions)


def main() -> None:
    """Ejecuta la reorganización solicitando explícitamente biblioteca y BC3 de origen."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("biblioteca", type=Path)
    parser.add_argument("bc3", type=Path)
    args = parser.parse_args()
    renamed, added = reorganize_library(args.biblioteca, args.bc3)
    print(f"Fichas renombradas: {renamed}. Fichas específicas añadidas: {added}.")


if __name__ == "__main__":
    main()
