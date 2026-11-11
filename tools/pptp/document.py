"""Catalogo, seleccion revisable y composicion del pliego PPTP."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from html import escape
import json
from pathlib import Path, PurePosixPath
import re
from typing import TYPE_CHECKING
import unicodedata
from urllib.parse import unquote
from .library import ZipLibrary
from .sheet_index import (
    INDEX_MARKER, INDEX_TOKEN, SheetIndexRow, extract_index, markdown_index,
    pdf_index, write_odt_index,
)
from tools._library_markdown import document_markdown

if TYPE_CHECKING:
    from bc3_gui import BudgetApp, ProjectItem
    from budget_reports import ReportSettings

VARIABLES = ("titulo_proyecto", "entidad", "plazo_garantia", "plazo_ejecucion",
             "bullet_capitulos_obra")
PRICE_VARIABLES = (
    "FORMULA_REVISION_PRECIOS", "JUSTIFICACION_NO_APLICACION", "JUSTIFICACION_APLICACION",
)
SHORT_DURATION_REASON = "no superar el plazo de ejecución previsto los dos años"


def duration_within_two_years(duration: str, start: str = "") -> bool | None:
    """Compara plazos simples; los días requieren el inicio real para contar años de calendario."""
    match = re.fullmatch(
        r"\s*(\d+(?:[.,]\d+)?)\s*(años?|mes(?:es)?|d[ií]as?(?:\s+naturales)?)\s*",
        duration, flags=re.IGNORECASE,
    )
    if not match or Decimal(match[1].replace(",", ".")) <= 0:
        return None
    amount = Decimal(match[1].replace(",", "."))
    unit = match[2].casefold()
    if unit.startswith("año"):
        return amount <= 2
    if unit.startswith("mes"):
        return amount <= 24
    if not start:
        return None
    beginning = date.fromisoformat(start)
    anniversary = date(
        beginning.year + 2, beginning.month,
        28 if beginning.month == 2 and beginning.day == 29 else beginning.day,
    )
    return amount <= (anniversary - beginning).days


def duration_price_revision(
    variables: dict[str, str], applies: bool, start: str = "",
) -> tuple[bool, str]:
    """Completa la razón temporal sin borrar justificaciones manuales ni activar revisión."""
    within = duration_within_two_years(variables.get("plazo_ejecucion", ""), start)
    reason = variables.get("JUSTIFICACION_NO_APLICACION", "").strip()
    reason = reason.replace("; además, " + SHORT_DURATION_REASON, "").replace(SHORT_DURATION_REASON, "").strip()
    if within is True:
        reason = reason + "; además, " + SHORT_DURATION_REASON if reason else SHORT_DURATION_REASON
    return applies, reason
PENDING_DURATION = "Pendiente de planificación"
DEFAULT_TOC_DEPTH = 2
REGULATION_PATTERN = re.compile(
    r"<!--\s*NORMATIVA_UNI\s+(\{[^\n]*\})\s*-->\s*\n([*+-] [^\n]+)",
)


def validate_toc_depth(value: object) -> int:
    """Exige un numero entero de niveles dentro de la jerarquia Markdown."""
    if type(value) is not int or not 1 <= value <= 6:
        raise ValueError("Los niveles del índice deben ser un entero entre 1 y 6.")
    return value


@dataclass(frozen=True)
class Sheet:
    """Ficha identificada por su ruta relativa, no solo por su clasificacion."""

    code: str
    title: str
    path: str
    warning: str = ""
    aliases: tuple[str, ...] = ()
    replaces: tuple[str, ...] = ()


@dataclass
class Selection:
    """Vinculo independiente entre una partida del presupuesto y una ficha."""

    item: int
    code: str
    chapter: str
    summary: str
    path: str
    selected: bool = True
    automatic: bool = True


@dataclass
class Regulation:
    """Referencia legal candidata que requiere comprobar su condicion."""

    id: str
    codes: list[str]
    status: str
    condition: str
    text: str
    selected: bool = False


def safe_path(root: Path, relative: str) -> Path:
    """Resuelve un recurso interno sin admitir rutas externas al catalogo."""
    parts = PurePosixPath(relative)
    if "\\" in relative or parts.is_absolute() or ".." in parts.parts or ":" in relative:
        raise ValueError(f"Ruta de ficha no válida: {relative}")
    path = root.joinpath(*parts.parts).resolve()
    if path.suffix.lower() != ".md" or not path.is_relative_to(root.resolve()):
        raise ValueError(f"Ruta de ficha no válida: {relative}")
    return path


def load_catalog(root: Path | ZipLibrary) -> list[Sheet]:
    """Lee todas las opciones del indice y valida sus archivos asociados."""
    if isinstance(root, ZipLibrary):
        return [
            Sheet(
                entry.code, entry.title, entry.path, entry.warning, entry.aliases,
                tuple(path for path, targets in root.replacements.items() if entry.path in targets),
            )
            for entry in root.entries.values()
        ]
    source = (root / "UNI" / "INDICE_FICHAS.md").read_text(encoding="utf-8-sig")
    result: dict[str, Sheet] = {}
    for line in source.splitlines():
        match = re.match(r"\|\s*([0-9A-Za-z]+)\s*\|\s*\[([^]]+)\]\(([^)]+)\)\s*\|([^|]*)", line)
        if not match:
            continue
        code, title, relative, warning = match.groups()
        relative = "UNI/" + unquote(relative)
        path = safe_path(root, relative)
        if not path.is_file():
            raise ValueError(f"No se encuentra la ficha del índice: {relative}")
        if relative in result:
            raise ValueError(f"Ficha duplicada en el índice: {relative}")
        sheet = Sheet(code.upper(), title, relative, warning.strip())
        levels = directory_codes(sheet)
        if (not levels or any(not re.fullmatch(r"\d{2}[A-Z]*", level) for level in levels)
                or not sheet.code.startswith(levels[-1])
                or any(not child.startswith(parent) for parent, child in zip(levels, levels[1:]))):
            raise ValueError(f"Clasificación de ficha no válida en el índice: {relative}")
        result[relative] = sheet
    if not result:
        raise ValueError("El índice de fichas PPTP no contiene opciones válidas.")
    for path in sorted((root / "UNI").rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        if relative in result or path.parent == root / "UNI":
            continue
        code = path.parent.name.split("_", 1)[0].upper()
        if not re.fullmatch(r"\d{2}[A-Z]*", code):
            continue
        title = next((line[2:].strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
                      if line.startswith("# ")), path.stem)
        result[relative] = Sheet(code, title, relative)
    return list(result.values())


def normalized_code(code: str) -> str:
    """Normaliza un codigo UNI sin inferir clasificaciones por su descripcion."""
    return code.strip().upper().rstrip("#*")


def directory_codes(sheet: Sheet) -> list[str]:
    """Obtiene los prefijos clasificados de las carpetas de una ficha."""
    return [part.split("_", 1)[0].upper() for part in PurePosixPath(sheet.path).parts[1:-1]]


def excluded_item(item: ProjectItem, excluded: set[str]) -> bool:
    """Excluye especialidades generadas o importadas, incluidos sus subcapitulos."""
    if item.source.startswith(("RCD:", "SS:")):
        return True
    code = normalized_code(item.code)
    if re.match(r"^(?:17|19)[A-Z]", code):
        return True
    return any(item.chapter == chapter or normalized_code(item.chapter).startswith(normalized_code(chapter))
               for chapter in excluded)


def excluded_chapters(titles: dict[str, str]) -> set[str]:
    """Reconoce las especialidades separadas mediante nombres y codigos explicitos."""
    result = set()
    for chapter, title in titles.items():
        text = unicodedata.normalize("NFKD", f"{chapter} {title}".casefold())
        text = "".join(char for char in text if not unicodedata.combining(char))
        if (re.search(r"\b(?:rcds?|residuos?|escombros?|seguridad|salud)\b|"
                      r"\bprevencion\b.*\briesgos\b|\bs\s+(?:y\s+)?s\b", text)
                or normalized_code(chapter) in {"17", "19", "SS", "SYS", "RCD", "RCDS"}):
            result.add(chapter)
    return result


def propose(items: list[ProjectItem], titles: dict[str, str], catalog: list[Sheet],
            saved: dict) -> tuple[list[Selection], list[Selection]]:
    """Busca ficheros en cada nivel coincidente y conserva decisiones anteriores."""
    automatic, unmatched = [], []
    excluded = excluded_chapters({
        **{item.chapter: "" for item in items}, **titles,
    })
    old: dict[tuple[int, str, str], list[dict]] = {}
    aliases = {alias: sheet.path for sheet in catalog for alias in sheet.aliases}
    replacements: dict[str, list[Sheet]] = {}
    for sheet in catalog:
        for path in sheet.replaces:
            replacements.setdefault(path, []).append(sheet)
    for row in saved.get("selections", []):
        candidates = replacements.get(row["path"], [])
        code = normalized_code(row["code"])
        matching = [sheet for sheet in candidates if code.startswith(sheet.code) or sheet.code.startswith(code)]
        if not matching:
            chapter = normalized_code(row["chapter"])
            matching = [sheet for sheet in candidates if chapter.startswith(sheet.code) or sheet.code.startswith(chapter)]
        targets = matching or candidates
        paths = [sheet.path for sheet in targets] if targets else [aliases.get(row["path"], row["path"])]
        for path in paths:
            previous = old.setdefault((row["item"], row["code"], row["chapter"]), [])
            mapped = {**row, "path": path}
            existing = next((entry for entry in previous if entry["path"] == path), None)
            if existing is None:
                previous.append(mapped)
            elif row["path"] not in replacements:
                previous[previous.index(existing)] = mapped
    for index, item in enumerate(items):
        if excluded_item(item, excluded):
            continue
        code = normalized_code(item.code)
        # Solo los archivos del propio nivel: no se incluyen hermanos por compartir capitulo.
        matches = [sheet for sheet in catalog if code.startswith(sheet.code)]
        chapter = normalized_code(item.chapter)
        if not matches and re.fullmatch(r"\d{2}[A-Z]+", chapter):
            matches = [
                sheet for sheet in catalog
                if (chapter.startswith(sheet.code) or sheet.code.startswith(chapter))
            ]
        previous = old.get((index, item.code, item.chapter), [])
        if matches:
            manual = [row for row in previous if not row["automatic"] and row["path"]]
            manual_paths = {row["path"] for row in manual}
            unmatched.extend(
                Selection(**{**row, "summary": item.summary, "automatic": False})
                for row in manual
            )
            for sheet in matches:
                if sheet.path in manual_paths:
                    continue
                row = next((row for row in previous if row["path"] == sheet.path), None)
                automatic.append(Selection(index, item.code, item.chapter, item.summary,
                                           sheet.path, row["selected"] if row else True))
        elif previous:
            unmatched.extend(Selection(**{**row, "summary": item.summary, "automatic": False})
                             for row in previous)
        else:
            unmatched.append(Selection(index, item.code, item.chapter, item.summary, "",
                                       automatic=False))
    return automatic, unmatched


def planning_duration(app: BudgetApp) -> str:
    """Reutiliza el calendario guardado para expresar el plazo en dias naturales."""
    if not app.planning_state:
        return ""
    from .._shared.scheduling import PlanParams, apply_saved_plan_state, build_activities, schedule

    params = PlanParams()
    activities = build_activities(app.budget_items, app._chapter_title)
    apply_saved_plan_state(params, activities, app.planning_state)
    schedule(activities, params)
    dates = [activity.end_date for activity in activities if activity.end_date is not None]
    if not dates:
        raise ValueError("La planificación no contiene actividades para calcular el plazo.")
    return f"{(max(dates) - params.start).days + 1} días naturales"


def initial_variables(app: BudgetApp) -> tuple[dict[str, str], bool]:
    """Carga la configuracion actual y el plazo calculado por planificacion."""
    duration = planning_duration(app)
    saved = app.pptp_state.get("variables", {})
    return {
        "titulo_proyecto": app._project_title(),
        "entidad": app.report_settings.organization,
        "plazo_garantia": app.report_settings.guarantee_period,
        "plazo_ejecucion": duration or saved.get("plazo_ejecucion", PENDING_DURATION),
        "bullet_capitulos_obra": saved.get("bullet_capitulos_obra", ""),
        **{key: saved.get(key, "") for key in PRICE_VARIABLES},
    }, not bool(duration)


def load_documents(root: Path | ZipLibrary) -> list[tuple[str, str]]:
    """Lee en orden todos los documentos numerados sin mezclar el ZIP con copias antiguas."""
    sources = (
        root.read_documents("") if isinstance(root, ZipLibrary) else [
            (path.name, path.read_text(encoding="utf-8-sig"))
            for path in sorted(root.iterdir())
            if path.is_file() and path.suffix.lower() == ".md"
        ]
    )
    sources = sorted(
        (name, source) for name, source in sources if re.match(r"^\d{2}(?!\d)", name)
    )
    prefixes = {int(name[:2]) for name, _ in sources}
    for index in range(1, max({4, *prefixes}) + 1):
        if index not in prefixes:
            raise ValueError(f"Falta la sección {index:02} del PPTP.")
    return sources


def _read_regulation(match: re.Match[str], name: str) -> Regulation:
    """Valida una referencia normativa anotada antes de seleccionarla o componerla."""
    try:
        data = json.loads(match[1])
        if not isinstance(data, dict):
            raise ValueError("Metadatos normativos no válidos.")
        refs = data["uni"]
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or not ref for ref in refs)
                or any(not isinstance(data.get(key), str) or not data[key]
                       for key in ("id", "seleccion", "condicion"))
                or data["seleccion"] not in {"candidata", "revision"}):
            raise ValueError("Metadatos normativos no válidos.")
        return Regulation(
            data["id"], refs, data["seleccion"], data["condicion"], match[2],
            data["seleccion"] == "candidata",
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Clasificación normativa no válida en {name}: {exc}") from exc


def load_regulations(root: Path | ZipLibrary, codes: set[str]) -> list[Regulation]:
    """Filtra referencias de todos los documentos generales sin depender de su ordinal."""
    result: dict[str, Regulation] = {}
    for name, source in load_documents(root):
        source = document_markdown(source)
        for match in REGULATION_PATTERN.finditer(source):
            ref = _read_regulation(match, name)
            if "*" in ref.codes or any(
                code.startswith(prefix) or prefix.startswith(code)
                for prefix in ref.codes for code in codes
            ):
                result.setdefault(ref.id, ref)
    if not result:
        raise ValueError("No se encontraron disposiciones legales clasificadas.")
    return list(result.values())


def restore_state(data: object) -> dict:
    """Valida selecciones persistidas sin recalcularlas ni depender de los archivos."""
    if not isinstance(data, dict):
        raise ValueError("El estado PPTP debe ser un objeto JSON.")
    if not data:
        return {}
    toc_depth = validate_toc_depth(data.get("toc_depth", DEFAULT_TOC_DEPTH))
    price_revision = data.get("aplica_revision_precios", False)
    if type(price_revision) is not bool:
        raise ValueError("La opción de revisión de precios debe ser verdadera o falsa.")
    price_start = data.get("revision_inicio_planificacion", "")
    if not isinstance(price_start, str):
        raise ValueError("La fecha de inicio para la revisión de precios no es válida.")
    if price_start:
        try:
            date.fromisoformat(price_start)
        except ValueError as exc:
            raise ValueError("La fecha de inicio para la revisión de precios no es válida.") from exc
    variables = data.get("variables")
    rows = data.get("selections")
    regulations = data.get("regulations")
    if (not isinstance(variables, dict)
            or any(not isinstance(variables.get(key), str) for key in VARIABLES)
            or any(not isinstance(variables.get(key, ""), str) for key in PRICE_VARIABLES)
            or not isinstance(rows, list) or not isinstance(regulations, dict)
            or any(not isinstance(key, str) or not isinstance(value, bool)
                   for key, value in regulations.items())):
        raise ValueError("El estado PPTP no contiene variables y selecciones válidas.")
    selections = []
    for row in rows:
        if (not isinstance(row, dict)
                or any(not isinstance(row.get(key), str)
                       for key in ("code", "chapter", "summary", "path"))
                or type(row.get("item")) is not int or row["item"] < 0
                or any(not isinstance(row.get(key), bool) for key in ("selected", "automatic"))):
            raise ValueError("Hay una selección PPTP no válida.")
        if row["path"]:
            safe_path(Path.cwd(), row["path"])
        elif row["selected"]:
            raise ValueError("Una partida seleccionada no tiene ficha PPTP.")
        selections.append(asdict(Selection(**{key: row[key] for key in Selection.__dataclass_fields__})))
    return {"variables": {
                **{key: variables[key] for key in VARIABLES},
                **{key: variables.get(key, "") for key in PRICE_VARIABLES},
            },
            "selections": selections, "regulations": dict(regulations), "toc_depth": toc_depth,
            "aplica_revision_precios": price_revision,
            "revision_inicio_planificacion": price_start}


def price_revision_field(applies: bool) -> str:
    """Identifica el dato obligatorio de la alternativa de revisión elegida."""
    return PRICE_VARIABLES[0] if applies else PRICE_VARIABLES[1]


def price_revision_fields(applies: bool) -> tuple[str, ...]:
    """Exige fórmula y motivo de aplicación o el motivo de no aplicación según la decisión."""
    return (PRICE_VARIABLES[0], PRICE_VARIABLES[2]) if applies else (PRICE_VARIABLES[1],)


def conditional_markdown(source: str, applies: bool) -> str:
    """Resuelve únicamente condiciones booleanas conocidas sin ejecutar expresiones Markdown."""
    parts = []
    stack: list[tuple[bool, bool]] = []
    previous = 0
    active = True
    for match in re.finditer(r"\{\{\s*(#if\b[^{}]*|else\b[^{}]*|/if\b[^{}]*)\s*\}\}", source):
        if active:
            parts.append(source[previous:match.start()])
        token = match[1].strip()
        if token.startswith("#if"):
            if token != "#if APLICA_REVISION_PRECIOS":
                raise ValueError(f"Condición PPTP no admitida: {token}.")
            stack.append((active, False))
            active = active and applies
        elif token == "else":
            if not stack or stack[-1][1]:
                raise ValueError("El documento contiene un {{else}} sin {{#if}} o repetido.")
            parent, _ = stack[-1]
            stack[-1] = (parent, True)
            active = parent and not applies
        elif token == "/if":
            if not stack:
                raise ValueError("El documento contiene un {{/if}} sin {{#if}}.")
            active, _ = stack.pop()
        else:
            raise ValueError(f"Marcador condicional PPTP no válido: {token}.")
        previous = match.end()
    if stack:
        raise ValueError("El documento contiene un {{#if}} sin cerrar con {{/if}}.")
    if active:
        parts.append(source[previous:])
    return "".join(parts)


def substitute(
    source: str, variables: dict[str, str], *, price_revision: bool = False,
) -> str:
    """Resuelve alternativas y sustituye solo las variables de la rama elegida."""
    source = conditional_markdown(source, price_revision)
    def replace(match: re.Match[str]) -> str:
        """Exige un valor expreso para cada variable utilizada en los textos."""
        name = match[1].strip()
        value = variables.get(name, "").strip()
        if not value or value == PENDING_DURATION:
            raise ValueError(f"Falta rellenar la variable {{{{{name}}}}}.")
        return value

    result = re.sub(r"\{\{([^{}]+)\}\}", replace, source)
    if "{{" in result or "}}" in result:
        raise ValueError("El documento contiene marcadores de variables sin resolver.")
    return result


def compose(
    root: Path | ZipLibrary, state: dict, regulations: list[Regulation], *,
    budget_chapter_titles: dict[str, str] | None = None,
) -> str:
    """Ensambla secciones y fichas únicas, agrupadas opcionalmente por capítulo presupuestario."""
    state = restore_state(state)
    state["aplica_revision_precios"], state["variables"]["JUSTIFICACION_NO_APLICACION"] = duration_price_revision(
        state["variables"], state["aplica_revision_precios"], state["revision_inicio_planificacion"],
    )
    if state["aplica_revision_precios"] and not state["variables"]["JUSTIFICACION_APLICACION"].strip():
        raise ValueError("Rellena el campo JUSTIFICACION_APLICACION antes de generar.")
    selected_rows = sorted(
        (row for row in state["selections"] if row["selected"]),
        key=lambda row: row["item"],
    )
    chosen_chapters: dict[str, str] = {}
    budget_chapter_order: dict[str, int] = {}
    for row in selected_rows:
        budget_chapter_order.setdefault(row["chapter"], row["item"])
        paths = (
            root.resolve_sheets(row["path"], row["code"])
            if isinstance(root, ZipLibrary) else (row["path"],)
        )
        for path in paths:
            chosen_chapters.setdefault(path, row["chapter"])
    chosen = list(chosen_chapters)
    if not chosen:
        raise ValueError("Selecciona al menos una ficha para generar el PPTP.")
    if isinstance(root, ZipLibrary):
        unknown = [path for path in chosen if path not in root.entries]
        if unknown:
            raise ValueError(f"La ficha no figura en catalogo.json: {unknown[0]}")

    def sheet_order(relative: str) -> tuple[str, str]:
        """Ordena por clasificacion UNI y desempata variantes por su ruta estable."""
        code = (
            root.entries[relative].code if isinstance(root, ZipLibrary)
            else directory_codes(Sheet("", "", relative))[-1]
        )
        return code, relative.casefold()

    if budget_chapter_titles is None:
        chosen.sort(key=sheet_order)
    else:
        chosen.sort(key=lambda relative: (
            budget_chapter_order[chosen_chapters[relative]],
            sheet_order(relative),
        ))
    selected = {
        ref.id for ref in regulations if state["regulations"].get(ref.id, ref.selected)
    }
    included: set[str] = set()
    sections = []
    for name, source in load_documents(root):
        source = document_markdown(source)

        def filter_regulation(match: re.Match[str]) -> str:
            """Incluye cada norma marcada en su posición original y una sola vez."""
            ref = _read_regulation(match, name)
            if ref.id not in selected or ref.id in included:
                return ""
            included.add(ref.id)
            return match[2]

        source = REGULATION_PATTERN.sub(filter_regulation, source)

        def filter_topic(match: re.Match[str]) -> str:
            """Suprime encabezados de grupos temáticos que no conservan contenido seleccionado."""
            body = match[2]
            content = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
            content = re.sub(r"^#{1,6}[ \t]+.*$", "", content, flags=re.MULTILINE)
            return body if content.strip() else ""

        source = re.sub(
            r"<!--\s*(\w+)\s*-->(.*?)<!--\s*FIN_\1\s*-->",
            filter_topic, source, flags=re.DOTALL,
        )
        source = source.replace(INDEX_MARKER, INDEX_TOKEN)
        sections.append(re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL).strip())
    previous_chapter = None
    sheet_sections: list[tuple[int, int]] = []
    chapter_labels: dict[int, str] = {}
    chapter_section = 0
    for relative in chosen:
        chapter = (
            chosen_chapters[relative]
            if budget_chapter_titles is not None
            else sheet_order(relative)[0][:2]
        )
        if chapter != previous_chapter:
            if budget_chapter_titles is not None:
                chapter_title = budget_chapter_titles.get(chapter, "").strip() or chapter.rstrip("#*")
            else:
                chapter_title = root.chapter_titles.get(chapter, "") if isinstance(root, ZipLibrary) else ""
                if not chapter_title:
                    folder = PurePosixPath(relative).parts[1]
                    chapter_title = folder.split("_", 1)[-1].replace("_", " ")
            chapter_section = len(sections)
            from bc3_gui import format_chapter_code

            budget_code = format_chapter_code(chosen_chapters[relative])
            chapter_labels[chapter_section] = (
                f"{budget_code} — {chapter_title}" if chapter_title != budget_code else budget_code
            )
            sections.append(f"## {chapter_title}")
            previous_chapter = chapter
        source = (
            root.read_sheet(relative) if isinstance(root, ZipLibrary)
            else safe_path(root, relative).read_text(encoding="utf-8-sig")
        )
        try:
            source = document_markdown(source)
        except ValueError as exc:
            raise ValueError(f"Ficha {relative}: {exc}") from exc
        if not source or not re.sub(r"^#{1,6}\s+.*$", "", source, flags=re.MULTILINE).strip():
            raise ValueError(
                f"La ficha {relative} no contiene prescripciones para el PPTP fuera de los "
                'bloques informativos ("""). Complétala o desmarca su incorporación.'
            )
        source = re.sub(
            r"^#{2,6}\s+(Descripción|Ejecución|Medición|Valoración|Medición y valoración)\s*$",
            lambda match: f"\n**{match[1]}**\n",
            source, flags=re.MULTILINE | re.IGNORECASE,
        )
        if not re.search(r"^# ", source, flags=re.MULTILINE):
            title = root.entries[relative].title if isinstance(root, ZipLibrary) else PurePosixPath(relative).stem
            levels = [len(match[1]) for match in re.finditer(r"^(#{2,6}) ", source, flags=re.MULTILINE)]
            if levels:
                offset = min(levels) - 2
                source = re.sub(
                    r"^(#{2,6}) ",
                    lambda match: "#" * (len(match[1]) - offset) + " ",
                    source, flags=re.MULTILINE,
                )
            source = f"# {title}\n\n{source}"
        source = re.sub(r"^(#{1,6}) (.*)$",
                        lambda match: "#" * min(len(match[1]) + 2, 6) + " " + match[2],
                        source, flags=re.MULTILINE)
        sheet_sections.append((chapter_section, len(sections)))
        sections.append(source)
    sections = [
        substitute(section, state["variables"], price_revision=state["aplica_revision_precios"])
        for section in sections
    ]
    result = number_headings("\n\n".join(sections))
    if INDEX_TOKEN in result:
        if result.count(INDEX_TOKEN) != 1:
            raise ValueError("El PPTP solo admite un marcador de tabla de fichas por capítulos.")
        heading_pattern = r"^#{1,6}\s+(.+)$"
        offsets = []
        heading_count = 0
        for section in sections:
            offsets.append(heading_count)
            heading_count += len(re.findall(heading_pattern, section, flags=re.MULTILINE))
        headings = re.findall(heading_pattern, result, flags=re.MULTILINE)
        rows = [
            SheetIndexRow(
                substitute(
                    chapter_labels[chapter], state["variables"],
                    price_revision=state["aplica_revision_precios"],
                ).upper(),
                re.sub(r"^\d+(?:\.\d+)*\s+", "", headings[offsets[sheet]]),
                f"PPTPHeading{offsets[sheet]}",
            )
            for chapter, sheet in sheet_sections
        ]
        result = result.replace(INDEX_TOKEN, markdown_index(rows))
    return result


def number_headings(source: str) -> str:
    """Numera la jerarquia Markdown sin duplicar la numeracion del texto original."""
    counters = [0] * 6
    result = []
    for line in source.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            level = len(match[1])
            counters[level - 1] += 1
            counters[level:] = [0] * (6 - level)
            for parent in range(level - 1):
                counters[parent] = max(1, counters[parent])
            title = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", match[2]).strip().upper()
            number = ".".join(str(value) for value in counters[:level])
            line = f"{match[1]} {number} {title}"
        result.append(line)
    return "\n".join(result)


def text_blocks(source: str, *, preserve_bold: bool = False) -> list[tuple[int, str]]:
    """Transforma Markdown y conserva niveles de lista junto a titulos y avisos."""
    source = document_markdown(source)
    source = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    source = re.sub(r"\[([^]]+)\]\(([^)]+)\)",
                    lambda match: (f"{match[1]} ({match[2]})" if match[2].startswith(("http:", "https:"))
                                   else match[1]), source)
    source = source.replace("`", "")
    blocks, paragraph = [], []
    for line in [*source.splitlines(), ""]:
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        bullet = re.match(r"^(?P<indent>[ \t]*)(?:[-*+]\s+|•\s*)(?P<text>.*)", line)
        ordered = re.match(
            r"^(?P<indent>[ \t]*)(?P<bold>\*\*)?(?P<number>\d+[.)])[ \t]+(?P<text>.*)", line,
        )
        separator = re.fullmatch(r"\s*\*\*[^*\n]+\*\*\s*", line)
        if not line.strip() or heading or bullet or ordered or separator or "\t" in line or line.startswith(">"):
            if paragraph:
                blocks.append((0, " ".join(paragraph)))
                paragraph = []
            if heading:
                blocks.append((len(heading[1]), heading[2]))
            elif bullet:
                spaces = len(bullet["indent"].expandtabs(4))
                depth = (spaces + 3) // 4
                blocks.append((-(depth + 1), "• " + bullet["text"].strip()))
            elif ordered:
                spaces = len(ordered["indent"].expandtabs(4))
                depth = (spaces + 3) // 4
                text = (ordered["bold"] or "") + ordered["number"] + " " + ordered["text"].strip()
                blocks.append((-(depth + 1), text))
            elif separator:
                blocks.append((0, line.rstrip()))
            elif line.startswith(">"):
                blocks.append((0, line.lstrip("> ")))
            elif line.strip():
                blocks.append((0, line.rstrip(" \r")))
        else:
            paragraph.append(line.strip())
    return blocks if preserve_bold else [(level, text.replace("**", "")) for level, text in blocks]


def bold_runs(text: str) -> list[tuple[str, bool]]:
    """Separa negritas Markdown equilibradas sin interpretar etiquetas del usuario."""
    runs = []
    previous = 0
    for match in re.finditer(r"\*\*(.+?)\*\*", text):
        if match.start() > previous:
            runs.append((text[previous:match.start()], False))
        runs.append((match[1], True))
        previous = match.end()
    if previous < len(text):
        runs.append((text[previous:], False))
    return runs


def template_paragraph_styles(template: dict) -> dict[str, str]:
    """Localiza estilos de cuerpo y títulos por nombres ODF o su nivel semántico."""
    styles = [
        style for style in template["styles"]
        if style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family") == "paragraph"
    ]
    namespace = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
    result = {}
    for style in styles:
        name = style.get(namespace + "name", "")
        label = style.get(namespace + "display-name", name.removeprefix("Template"))
        label = re.sub(r"_([0-9A-Fa-f]{2})_", lambda match: chr(int(match[1], 16)), label)
        label = "".join(
            char for char in unicodedata.normalize("NFKD", label.casefold())
            if not unicodedata.combining(char)
        )
        if label in {"text body", "body text", "cuerpo", "cuerpo de texto", "texto del cuerpo"}:
            result["body"] = name
        level = style.get(namespace + "default-outline-level")
        match = re.fullmatch(r"(?:heading|titulo)\s*([1-6])", label)
        if match:
            result[f"heading{match[1]}"] = name
        elif level in {"1", "2", "3", "4", "5", "6"}:
            result.setdefault(f"heading{level}", name)
        if label in {"heading", "encabezado", "titulo"}:
            result.setdefault("heading", name)
        if label in {"standard", "normal", "predeterminado"}:
            result.setdefault("body", name)
    return result


def write_document(
    source: str, target: Path, variables: dict[str, str], settings: ReportSettings, *,
    toc_depth: int = DEFAULT_TOC_DEPTH,
) -> None:
    """Emite PDF u ODT con la misma plantilla de cabecera y pie del proyecto."""
    toc_depth = validate_toc_depth(toc_depth)
    source, sheet_rows = extract_index(number_headings(source))
    blocks = text_blocks(source, preserve_bold=True)
    title = "PLIEGO DE PRESCRIPCIONES TÉCNICAS PARTICULARES"
    project = variables["titulo_proyecto"]
    bullet_depths = sorted({-level - 1 for level, _ in blocks if level < 0})
    if settings.output_format == "odt":
        from xml.etree import ElementTree as ET
        from odt_reports import _new_report, _tag

        document = _new_report(title, project, settings, repeat_titles=False, footer_title=title)
        document.preserve_template_styles = True
        template_styles = template_paragraph_styles(document.template_header)
        body_parent = template_styles.get("body", "ReportBody")
        bold_style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): "PPTPBold", _tag("style:family"): "text",
        })
        ET.SubElement(bold_style, _tag("style:text-properties"), {
            _tag("fo:font-weight"): "bold",
            _tag("style:font-weight-asian"): "bold",
            _tag("style:font-weight-complex"): "bold",
        })
        for name, parent, heading in [
            ("PPTPBody", body_parent, False),
            ("PPTPSeparator", "PPTPBody", True),
            *[(
                f"PPTPHeading{level}",
                template_styles.get(f"heading{level}", template_styles.get("heading", f"Heading{level}")),
                True,
            ) for level in range(1, 7)],
        ]:
            style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
                _tag("style:name"): name, _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): parent,
            })
            properties = {
                _tag("fo:text-align"): "start" if heading else "justify",
                _tag("fo:break-before"): "auto", _tag("fo:break-after"): "auto",
                _tag("fo:keep-with-next"): "always" if heading else "auto",
                _tag("fo:keep-together"): "auto",
                _tag("fo:orphans"): "2", _tag("fo:widows"): "2",
                _tag("fo:text-indent"): "0pt",
            }
            if name == "PPTPBody":
                properties.update({
                    _tag("fo:margin-top"): "0pt", _tag("fo:margin-bottom"): "6pt",
                    _tag("fo:line-height"): "115%",
                })
            elif name == "PPTPSeparator":
                properties.update({
                    _tag("fo:margin-top"): "8pt", _tag("fo:margin-bottom"): "3pt",
                })
            else:
                level = int(name.removeprefix("PPTPHeading"))
                properties.update({
                    _tag("fo:margin-top"): f"{14 if level == 1 else 10}pt",
                    _tag("fo:margin-bottom"): "5pt",
                    _tag("fo:line-height"): "115%",
                })
                style.set(_tag("style:next-style-name"), "PPTPBody")
                if f"heading{level}" not in template_styles:
                    size = (14, 12, 11, 10.5, 10, 10)[level - 1]
                    ET.SubElement(style, _tag("style:text-properties"), {
                        _tag("fo:font-size"): f"{size}pt",
                        _tag("style:font-size-asian"): f"{size}pt",
                        _tag("style:font-size-complex"): f"{size}pt",
                        _tag("fo:font-weight"): "bold",
                        _tag("style:font-weight-asian"): "bold",
                        _tag("style:font-weight-complex"): "bold",
                        _tag("fo:font-style"): "normal",
                    })
            ET.SubElement(style, _tag("style:paragraph-properties"), properties)
        for depth in bullet_depths:
            style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
                _tag("style:name"): f"PPTPBullet{depth}", _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): "PPTPBody",
            })
            ET.SubElement(style, _tag("style:paragraph-properties"), {
                _tag("fo:margin-left"): f"{12 * (depth + 1)}pt",
                _tag("fo:text-indent"): "-12pt",
                _tag("fo:margin-top"): "0pt", _tag("fo:margin-bottom"): "3pt",
                _tag("fo:text-align"): "start",
            })
            properties = style.find(_tag("style:paragraph-properties"))
            tabs = ET.SubElement(properties, _tag("style:tab-stops"))
            ET.SubElement(tabs, _tag("style:tab-stop"), {
                _tag("style:position"): "12pt", _tag("style:type"): "left",
            })
        if sheet_rows:
            style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
                _tag("style:name"): "PPTPIndexCaption", _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): "PPTPBody",
            })
            ET.SubElement(style, _tag("style:paragraph-properties"), {
                _tag("fo:text-align"): "center", _tag("fo:keep-with-next"): "always",
                _tag("fo:margin-top"): "8pt", _tag("fo:margin-bottom"): "5pt",
                _tag("fo:line-height"): "115%",
            })
        document.table_of_contents(
            [(level, text.replace("**", "")) for level, text in blocks if level > 0],
            max_level=toc_depth,
        )
        for style in document.automatic_styles:
            name = style.get(_tag("style:name"), "")
            if re.fullmatch(r"PPTPContents[1-6]", name):
                style.set(_tag("style:parent-style-name"), "PPTPBody")
                properties = style.find(_tag("style:paragraph-properties"))
                properties.set(_tag("fo:text-align"), "start")
                properties.set(_tag("fo:line-height"), "115%")
                properties.set(_tag("fo:margin-bottom"), "4pt")
                properties.set(_tag("fo:keep-with-next"), "auto")
                ET.SubElement(style, _tag("style:text-properties"), {
                    _tag("fo:font-size"): "10pt",
                    _tag("fo:font-weight"): "bold" if name == "PPTPContents1" else "normal",
                })
        heading_index = 0
        for block_index, (level, text) in enumerate(blocks):
            if level == 0 and text == INDEX_TOKEN:
                write_odt_index(document, sheet_rows)
                continue
            if level > 0:
                document.heading(
                    text.replace("**", ""), level, style=f"PPTPHeading{level}",
                    bookmark=f"PPTPHeading{heading_index}",
                )
                heading_index += 1
            else:
                separator = re.fullmatch(r"\*\*[^*]+\*\*", text) is not None
                style = (
                    f"PPTPBullet{-level - 1}" if level < 0
                    else "PPTPSeparator" if separator else "PPTPBody"
                )
                if block_index + 1 < len(blocks) and blocks[block_index + 1] == (0, INDEX_TOKEN):
                    style = "PPTPIndexCaption"
                paragraph = ET.SubElement(document.body, _tag("text:p"), {
                    _tag("text:style-name"): style,
                })
                if level < 0:
                    text = re.sub(r"^(\*\*)?(\d+[.)]|•) ", r"\1\2\t ", text)
                for value, bold in bold_runs(text):
                    attributes = {_tag("text:style-name"): "PPTPBold"} if bold else {}
                    span = ET.SubElement(paragraph, _tag("text:span"), attributes)
                    for index, part in enumerate(value.split("\t")):
                        if index:
                            ET.SubElement(span, _tag("text:tab")).tail = part
                        else:
                            span.text = part
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): "PPTPTitle", _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ReportTitle",
        })
        for element in document.body.iter():
            if element.get(_tag("text:style-name")) == "ReportTitle":
                element.set(_tag("text:style-name"), "PPTPTitle")
        for level in range(1, 7):
            style = next(
                style for style in document.automatic_styles
                if style.get(_tag("style:name")) == f"PPTPHeading{level}"
            )
            style.set(_tag("style:default-outline-level"), str(level))
        document.name_text_styles({
            "PPTPBody": "PPTP - Cuerpo",
            "PPTPTitle": "PPTP - Título del índice",
            "PPTPSeparator": "PPTP - Apartado de ficha",
            "PPTPBold": "PPTP - Énfasis",
            "PPTPIndexCaption": "PPTP - Título de tabla",
            "PPTPIndexHeader": "PPTP - Tabla - Cabecera",
            "PPTPIndexText": "PPTP - Tabla - Capítulo",
            "PPTPIndexBullet": "PPTP - Tabla - Ficha con viñeta",
            **{f"PPTPHeading{level}": f"PPTP - Título {level}" for level in range(1, 7)},
            **{f"PPTPBullet{depth}": f"PPTP - Lista {depth + 1}" for depth in bullet_depths},
            **{f"PPTPContents{level}": f"PPTP - Índice {level}" for level in range(1, 7)},
        })
        document.write(target)
    else:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
        from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Table
        from reportlab.platypus.doctemplate import LayoutError
        from reportlab.platypus.tableofcontents import TableOfContents
        from report_layout import LEFT_MARGIN, RIGHT_MARGIN, ReportLayout

        layout = ReportLayout(
            template_path=settings.template_path, repeat_titles=False, footer_title=title,
            expediente=settings.expediente, template_title=project,
        )
        body = ParagraphStyle("PPTPBody", parent=layout.normal, fontSize=10,
                              leading=13, spaceAfter=6, alignment=TA_JUSTIFY)
        toc = TableOfContents()
        toc.levelStyles = [
            ParagraphStyle(
                f"PPTPContents{level}", parent=body, fontSize=10,
                leading=13, leftIndent=12 * level, firstLineIndent=0,
                rightIndent=24, spaceBefore=4 if level == 0 else 0,
                alignment=TA_LEFT,
            ) for level in range(toc_depth)
        ]
        bullet_styles = {
            depth: ParagraphStyle(
                f"PPTPBullet{depth}", parent=body,
                leftIndent=12 * (depth + 1), firstLineIndent=-12,
            )
            for depth in bullet_depths
        }
        story = [
            layout.text("ÍNDICE", layout.title_style), toc, PageBreak(),
        ]

        def pdf_paragraph(text: str, style):
            """Crea un párrafo PDF con negritas explícitas y sin interpretar HTML del usuario."""
            markup = "".join(
                f'<font name="{escape(layout.bold_font, quote=True)}">{escape(value)}</font>'
                if bold else escape(value)
                for value, bold in bold_runs(text)
            )
            return Paragraph(markup, style)

        tabular_rows = []

        def flush_tabular_rows() -> None:
            """Alinea las columnas separadas por tabuladores con celdas que admiten ajuste."""
            if not tabular_rows:
                return
            width = A4[0] - LEFT_MARGIN - RIGHT_MARGIN
            columns = max(len(row) for row in tabular_rows)
            rows = [
                [pdf_paragraph(cell, body) for cell in row] + [""] * (columns - len(row))
                for row in tabular_rows
            ]
            story.append(Table(
                rows, colWidths=[width / columns] * columns, hAlign="LEFT",
                style=[
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ],
            ))
            tabular_rows.clear()

        heading_index = 0
        for block_index, (level, text) in enumerate(blocks):
            if level == 0 and text == INDEX_TOKEN:
                flush_tabular_rows()
                story.append(pdf_index(sheet_rows, layout, body))
                continue
            if level == 0 and "\t" in text.lstrip("\t"):
                tabular_rows.append(text.split("\t"))
                continue
            flush_tabular_rows()
            style = body
            if level > 0:
                style = ParagraphStyle(
                    f"PPTPHeading{level}", parent=body, fontName=layout.bold_font,
                    fontSize=max(10, 15 - level), leading=max(13, 18 - level),
                    spaceBefore=10, keepWithNext=True, alignment=TA_LEFT,
                )
            elif level < 0:
                style = bullet_styles[-level - 1]
            elif re.fullmatch(r"\*\*[^*]+\*\*", text):
                style = ParagraphStyle(
                    "PPTPSeparator", parent=body, spaceBefore=8,
                    keepWithNext=True, alignment=TA_LEFT,
                )
            if block_index + 1 < len(blocks) and blocks[block_index + 1] == (0, INDEX_TOKEN):
                style = ParagraphStyle(
                    "PPTPIndexCaption", parent=body, keepWithNext=True, alignment=TA_CENTER,
                )
            if text.startswith("\t"):
                tabs = len(text) - len(text.lstrip("\t"))
                style = ParagraphStyle(
                    style.name + "Indented", parent=style, leftIndent=style.leftIndent + 12 * tabs,
                )
                text = text.lstrip("\t")
            paragraph = pdf_paragraph(text, style)
            if level > 0:
                paragraph._pptp_heading = (
                    level - 1, text.replace("**", ""), f"PPTPHeading{heading_index}",
                )
                heading_index += 1
            story.append(paragraph)
        flush_tabular_rows()

        class PptpKeepTogether(KeepTogether):
            """Conserva los titulos con dos lineas, no con todo el parrafo siguiente."""

            def wrap(self, available_width, available_height):
                """Limita la reserva para permitir partir parrafos largos tras un titulo."""
                size = super().wrap(available_width, available_height)
                headings = self._content[:-1]
                following = self._content[-1]
                self._H0 = sum(
                    heading.wrap(available_width, available_height)[1]
                    + heading.getSpaceBefore() + heading.getSpaceAfter()
                    for heading in headings
                ) + min(
                    following.wrapOn(self.canv, available_width, available_height)[1],
                    following.style.leading * 2 if isinstance(following, Paragraph) else 40,
                )
                self._maxHeight = self._H
                return size

        class PptpDocTemplate(SimpleDocTemplate):
            """Recoge paginas reales de los epigrafes durante la maquetacion."""

            def afterFlowable(self, flowable) -> None:
                """Publica destinos e indice para converger en varias pasadas."""
                heading = getattr(flowable, "_pptp_heading", None)
                if heading is not None:
                    level, text, key = heading
                    self.canv.bookmarkPage(key)
                    if level < toc_depth:
                        self.notify("TOCEntry", (level, escape(text), self.page, key))

        document = PptpDocTemplate(
            str(target), pagesize=A4, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN,
            topMargin=layout.top_margin(title, project), bottomMargin=layout.bottom_margin(),
            title=title, author=settings.author,
            keepTogetherClass=PptpKeepTogether,
        )

        def header(canvas, doc) -> None:
            """Repite la identidad visual de la plantilla en cada pagina."""
            layout.header(canvas, doc, title, project)

        try:
            document.multiBuild(story, onFirstPage=header, onLaterPages=header)
        except LayoutError as exc:
            raise ValueError(f"No se pudo maquetar el PPTP: {exc}") from exc
