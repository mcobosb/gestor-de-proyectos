"""Catalogo, seleccion revisable y composicion del pliego PPTP."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from html import escape
import json
from pathlib import Path, PurePosixPath
import re
from typing import TYPE_CHECKING
import unicodedata
from urllib.parse import unquote
from .library import ZipLibrary
from tools._library_markdown import document_markdown

if TYPE_CHECKING:
    from bc3_gui import BudgetApp, ProjectItem
    from budget_reports import ReportSettings

VARIABLES = ("titulo_proyecto", "entidad", "plazo_garantia", "plazo_ejecucion",
             "bullet_capitulos_obra")
PENDING_DURATION = "Pendiente de planificación"
DEFAULT_TOC_DEPTH = 2


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
    }, not bool(duration)


def load_regulations(root: Path | ZipLibrary, codes: set[str]) -> list[Regulation]:
    """Filtra las referencias clasificadas por prefijos y conserva sus avisos."""
    sources = (
        root.read_documents("02") if isinstance(root, ZipLibrary) else [
            (path.name, path.read_text(encoding="utf-8-sig"))
            for path in sorted(root.glob("02*.md"))
        ]
    )
    if not sources:
        raise ValueError("Falta la sección 02 de disposiciones técnicas legales.")
    result: dict[str, Regulation] = {}
    for name, source in sources:
        source = document_markdown(source)
        for match in re.finditer(r"<!--\s*NORMATIVA_UNI\s+(\{[^\n]*\})\s*-->\s*\n([*+-] [^\n]+)", source):
            try:
                data = json.loads(match[1])
                refs = data["uni"]
                if (not isinstance(refs, list) or not refs
                        or any(not isinstance(ref, str) or not ref for ref in refs)
                        or any(not isinstance(data.get(key), str) or not data[key]
                               for key in ("id", "seleccion", "condicion"))
                        or data["seleccion"] not in {"candidata", "revision"}):
                    raise ValueError("Metadatos normativos no válidos.")
                relevant = "*" in refs or any(
                    code.startswith(ref) or ref.startswith(code) for ref in refs for code in codes
                )
                if relevant:
                    result.setdefault(data["id"], Regulation(
                        data["id"], refs, data["seleccion"], data["condicion"], match[2],
                        data["seleccion"] == "candidata",
                    ))
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Clasificación normativa no válida en {name}: {exc}") from exc
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
    variables = data.get("variables")
    rows = data.get("selections")
    regulations = data.get("regulations")
    if (not isinstance(variables, dict)
            or any(not isinstance(variables.get(key), str) for key in VARIABLES)
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
    return {"variables": {key: variables[key] for key in VARIABLES},
            "selections": selections, "regulations": dict(regulations), "toc_depth": toc_depth}


def substitute(source: str, variables: dict[str, str]) -> str:
    """Sustituye variables conocidas y rechaza marcadores sin resolver."""
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


def compose(root: Path | ZipLibrary, state: dict, regulations: list[Regulation]) -> str:
    """Ensambla secciones y fichas unicas, con normativa revisada por el tecnico."""
    state = restore_state(state)
    if isinstance(root, ZipLibrary):
        chosen = list(dict.fromkeys(
            path for row in state["selections"] if row["selected"]
            for path in root.resolve_sheets(row["path"], row["code"])
        ))
    else:
        chosen = list(dict.fromkeys(row["path"] for row in state["selections"] if row["selected"]))
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

    chosen.sort(key=sheet_order)
    sections = []
    for prefix in ("01", "02", "03", "04"):
        sources = (
            root.read_documents(prefix) if isinstance(root, ZipLibrary) else [
                (path.name, path.read_text(encoding="utf-8-sig"))
                for path in sorted(root.glob(f"{prefix}*.md"))
            ]
        )
        if not sources:
            raise ValueError(f"Falta la sección {prefix} del PPTP.")
        sources = [(name, document_markdown(source)) for name, source in sources]
        if prefix == "02":
            selected = [ref.text for ref in regulations if state["regulations"].get(ref.id, ref.selected)]
            introductions = []
            for _, source in sources:
                source = source.split("<!-- NORMATIVA_UNI", 1)[0]
                introductions.append(re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL).strip())
            sections.append("\n\n".join(introductions + selected))
        else:
            sections.extend(source for _, source in sources)
    previous_chapter = None
    for relative in chosen:
        chapter = sheet_order(relative)[0][:2]
        if chapter != previous_chapter:
            chapter_title = root.chapter_titles.get(chapter, "") if isinstance(root, ZipLibrary) else ""
            if not chapter_title:
                folder = PurePosixPath(relative).parts[1]
                chapter_title = folder.split("_", 1)[-1].replace("_", " ")
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
        sections.append(source)
    return number_headings(substitute("\n\n".join(sections), state["variables"]))


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
        ordered = re.match(r"^(?P<indent>[ \t]*)(?P<number>\d+[.)])\s+(?P<text>.*)", line)
        separator = re.fullmatch(r"\s*\*\*[^*\n]+\*\*\s*", line)
        if not line.strip() or heading or bullet or ordered or separator or line.startswith(">"):
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
                blocks.append((-(depth + 1), ordered["number"] + " " + ordered["text"].strip()))
            elif separator:
                blocks.append((0, line.strip()))
            elif line.startswith(">"):
                blocks.append((0, line.lstrip("> ")))
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


def write_document(
    source: str, target: Path, variables: dict[str, str], settings: ReportSettings, *,
    toc_depth: int = DEFAULT_TOC_DEPTH,
) -> None:
    """Emite PDF u ODT con la misma plantilla de cabecera y pie del proyecto."""
    toc_depth = validate_toc_depth(toc_depth)
    blocks = text_blocks(number_headings(source), preserve_bold=True)
    title = "PLIEGO DE PRESCRIPCIONES TÉCNICAS PARTICULARES"
    project = variables["titulo_proyecto"]
    bullet_depths = sorted({-level - 1 for level, _ in blocks if level < 0})
    if settings.output_format == "odt":
        from xml.etree import ElementTree as ET
        from odt_reports import _new_report, _tag

        document = _new_report(title, project, settings, repeat_titles=False, footer_title=title)
        bold_style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): "PPTPBold", _tag("style:family"): "text",
        })
        ET.SubElement(bold_style, _tag("style:text-properties"), {
            _tag("fo:font-weight"): "bold",
            _tag("style:font-weight-asian"): "bold",
            _tag("style:font-weight-complex"): "bold",
        })
        for name, parent, heading in [
            ("PPTPBody", "ReportBody", False),
            ("PPTPSeparator", "PPTPBody", True),
            *[(f"PPTPHeading{level}", f"Heading{level}", True) for level in range(1, 7)],
        ]:
            style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
                _tag("style:name"): name, _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): parent,
            })
            ET.SubElement(style, _tag("style:paragraph-properties"), {
                _tag("fo:text-align"): "start" if heading else "justify",
                _tag("fo:break-before"): "auto", _tag("fo:break-after"): "auto",
                _tag("fo:keep-with-next"): "always" if heading else "auto",
                _tag("fo:keep-together"): "auto",
                _tag("fo:orphans"): "2", _tag("fo:widows"): "2",
            })
        for depth in bullet_depths:
            style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
                _tag("style:name"): f"PPTPBullet{depth}", _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): "PPTPBody",
            })
            ET.SubElement(style, _tag("style:paragraph-properties"), {
                _tag("fo:margin-left"): f"{12 * (depth + 1)}pt",
                _tag("fo:text-indent"): "-12pt",
            })
        document.table_of_contents(
            [(level, text.replace("**", "")) for level, text in blocks if level > 0],
            max_level=toc_depth,
        )
        heading_index = 0
        for level, text in blocks:
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
                paragraph = ET.SubElement(document.body, _tag("text:p"), {
                    _tag("text:style-name"): style,
                })
                for value, bold in bold_runs(text):
                    attributes = {_tag("text:style-name"): "PPTPBold"} if bold else {}
                    ET.SubElement(paragraph, _tag("text:span"), attributes).text = value
        document.write(target)
    else:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
        from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate
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
        heading_index = 0
        for level, text in blocks:
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
            markup = "".join(
                f'<font name="{escape(layout.bold_font, quote=True)}">{escape(value)}</font>'
                if bold else escape(value)
                for value, bold in bold_runs(text)
            )
            paragraph = Paragraph(markup, style)
            if level > 0:
                paragraph._pptp_heading = (
                    level - 1, text.replace("**", ""), f"PPTPHeading{heading_index}",
                )
                heading_index += 1
            story.append(paragraph)

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
                    following.wrap(available_width, available_height)[1],
                    following.style.leading * 2,
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
