"""Reorganiza las fichas PPTP con una ficha por tercera letra BCCA."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import posixpath
import re
import unicodedata
from urllib.parse import quote, unquote, urlsplit
from zipfile import ZipFile

from bc3_gui import Bc3Dataset, Concept
from tools._library_archive import sheet_name, update_library
from tools._library_editor import _signature
from tools._library_markdown import informational_ranges
from .library import ZipLibrary


def mark_sheet_information(source: str) -> str:
    """Delimita metadatos y trazabilidad conservando literalmente las prescripciones."""
    if informational_ranges(source):
        return source
    integrated = re.search(r"^## Prescripciones integradas de la familia\s*$", source, re.MULTILINE)
    if integrated:
        groups = list(re.finditer(r"^### .+$", source[integrated.end():], re.MULTILINE))
        groups = [
            (integrated.end() + group.start(), integrated.end() + group.end())
            for group in groups
        ]
        if not groups:
            raise ValueError("La ficha integrada no contiene ninguna sección de prescripciones.")
        spans = []
        for index, (start, end) in enumerate(groups):
            limit = groups[index + 1][0] if index + 1 < len(groups) else len(source)
            heading = re.search(r"^#### .+$", source[end:limit], re.MULTILINE)
            if heading is None:
                # Las fichas de mantenimiento pueden contener un cuerpo sin subtítulos.
                body = re.search(r"\n\s*\n", source[end:limit])
                stop = end + body.end() if body else end
            else:
                stop = end + heading.start()
            if len(groups) == 1:
                spans.append((0, stop))
            else:
                if index == 0:
                    spans.append((0, start))
                spans.append((end + (1 if source[end:end + 1] == "\n" else 0), stop))
    elif re.search(r"^\*\*Clasificación BCCA:\*\*", source, re.MULTILINE):
        spans = [(0, len(source))]
    else:
        first = re.search(r"^## .+$", source, re.MULTILINE)
        if first is None:
            raise ValueError("La ficha común no contiene secciones técnicas reconocibles.")
        spans = [(0, first.start())]
    spans.extend(
        (match.start(), match.end())
        for match in re.finditer(r"^Fuente: [^\n]*(?:\n|\Z)", source, re.MULTILINE)
        if not any(start <= match.start() < end for start, end in spans)
    )
    result = []
    previous = 0
    for start, end in sorted(spans):
        if end <= start:
            continue
        result.extend((source[previous:start], '"""\n', source[start:end]))
        if not source[start:end].endswith("\n"):
            result.append("\n")
        result.append('"""\n')
        previous = end
    result.append(source[previous:])
    return "".join(result)


def mark_library_information(archive_path: Path) -> int:
    """Marca la información interna en todas las fichas mediante guardado atómico."""
    library = ZipLibrary(archive_path, archive_path.parent / "docs")
    signature = _signature(archive_path)
    updates = {}
    with ZipFile(archive_path) as source:
        for entry in library.entries.values():
            original = source.read(entry.path).decode("utf-8-sig")
            try:
                marked = mark_sheet_information(original)
            except ValueError as exc:
                raise ValueError(f"No se pudo marcar la ficha {entry.path}: {exc}") from exc
            if marked != original:
                updates[entry.path] = marked
    if updates:
        update_library(archive_path, signature, updates=updates)
    return len(updates)


def _integrated_body(text: str, origin: str, destination: str) -> str:
    """Conserva las prescripciones y rebasa sus enlaces al insertarlas en otra ficha."""
    def relocate(match: re.Match[str]) -> str:
        """Mantiene destinos locales, consultas y fragmentos desde la nueva carpeta."""
        target = urlsplit(match[1])
        if target.scheme or target.netloc or not target.path:
            return match[0]
        absolute = posixpath.normpath(posixpath.join(posixpath.dirname(origin), unquote(target.path)))
        relative = posixpath.relpath(absolute, posixpath.dirname(destination))
        suffix = ("?" + target.query if target.query else "") + ("#" + target.fragment if target.fragment else "")
        return "](" + quote(relative, safe="/._-") + suffix + ")"

    text = re.sub(r"\]\(([^)\s]+)\)", relocate, text)
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return re.sub(r"^(#{1,4}) ", r"##\1 ", "\n".join(lines).strip(), flags=re.MULTILINE)


def integrate_common_sheets(archive_path: Path) -> int:
    """Integra las comunes en sus descendientes y retira solo las que tienen sustituto."""
    library = ZipLibrary(archive_path, archive_path.parent / "docs")
    signature = _signature(archive_path)
    entries = list(library.entries.values())
    specific = [entry for entry in entries if len(entry.code) == 5]
    common = [
        entry for entry in entries
        if len(entry.code) < 5 and entry.title.endswith(" - Común")
    ]
    replacements = {
        entry.path: [child.path for child in specific if child.code.startswith(entry.code)]
        for entry in common
        if any(child.code.startswith(entry.code) for child in specific)
    }
    if not replacements:
        return 0
    updates = {}
    with ZipFile(archive_path) as source:
        bodies = {entry.path: source.read(entry.path).decode("utf-8-sig") for entry in common}
        for entry in specific:
            applicable = [parent for parent in common if entry.path in replacements.get(parent.path, [])]
            if not applicable:
                continue
            text = source.read(entry.path).decode("utf-8-sig")
            text = re.sub(
                r"^## Condiciones comunes de la familia\n.*?(?=^## |\Z)", "",
                text, flags=re.MULTILINE | re.DOTALL,
            )
            sections = [
                "## Prescripciones integradas de la familia", "",
                "Estas condiciones se aplican exclusivamente al material y a la operación "
                "de esta ficha, en lo compatible con la partida aprobada. Las condiciones "
                "de variantes distintas son alternativas, no acumulables. Si una prescripción "
                "general contradice las inclusiones, exclusiones o la medición de la unidad "
                "concreta, debe revisarse antes de incorporar la ficha al contrato.", "",
            ]
            for parent in applicable:
                sections += [
                    "### " + parent.title.removesuffix(" - Común"), "",
                    _integrated_body(bodies[parent.path], parent.path, entry.path), "",
                ]
            updates[entry.path] = text.rstrip() + "\n\n" + "\n".join(sections)
    update_library(
        archive_path, signature, updates=updates, replacements=replacements,
    )
    return len(replacements)


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
    integrate_common_sheets(archive_path)
    mark_library_information(archive_path)
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
