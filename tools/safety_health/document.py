"""Generacion del Estudio de Seguridad y Salud a partir de BIBLIOTECA.zip."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from html import escape
from pathlib import Path
from pathlib import PurePosixPath
import posixpath
import re
import tempfile
from typing import TYPE_CHECKING
import unicodedata
from zipfile import BadZipFile, ZipFile

from budget_reports import budget_totals, money, report_output_format
from cpv_catalog import resolve_cpv_codes
from .care_centers import format_centers, format_sicess_centers, restore_centers, restore_sicess_centers
from .chapter_selection import SAFETY_BUDGET_SOURCE, safety_budget_chapters, study_chapters, study_items
from .external_conditions import APPLICABILITY, EXTERNAL_CONDITIONS, restore_external_conditions
from .drawings import (
    group_sheets_by_page, render_drawings, restore_drawings, selected_sheets,
)
from .regulations import (
    REGULATIONS, WorkUnit, general_une_references, load_ntps, restore_review, review_context,
)
from .risk_links import FamilyRiskLink, family_risk_link_for_code, load_family_risk_links
from .technical_conditions import selected_technical_template, technical_review_data
from localization import translate
from municipalities import resolve_municipalities
from report_layout import LEFT_MARGIN, PAGE_MARGIN, RIGHT_MARGIN, ReportLayout, report_project_label
from tools._library_history import read_revisions
from tools._shared.normative_links import append_odt_links, normative_runs
from tools._shared.document_layout import (
    DEFAULT_TOC_DEPTH, TABLE_HEADER_BACKGROUND, add_odt_blue_table_cells, add_odt_list_style,
    add_odt_narrative_styles, number_headings, pdf_blue_table_style, pdf_list_style,
)
from tools.safety_health import (
    LIBRARY_PATH, _study_criteria_values, evaluate_study_criteria,
    study_work_summary,
)
from collaborative_storage import shared_library_path

if TYPE_CHECKING:
    from bc3_gui import BudgetApp, ProjectItem


_TEMPLATE_MEMBERS = {
    "project_data": "01_objeto_y_alcance.md",
    "memory": "02_memoria_general.md",
    "catalogues": "04_catalogos_preventivos.md",
    "emergencies": "05_gestion_emergencias.md",
    "monitoring": "07_seguimiento_control.md",
    "specification": "06_pliego_condiciones.md",
    "normative": "08_normativa_disposiciones_generales.md",
    "index": "UNI/INDICE_FICHAS_ESS.md",
    "general_conditions": "UNI/CONDICIONES_GENERALES_ESS.md",
}
_PLIEGO_DOCUMENT_TITLE = "DOCUMENTO III. PLIEGO DE CONDICIONES"
_PLANS_DOCUMENT_TITLE = "DOCUMENTO II. PLANOS"
_MEASUREMENTS_DOCUMENT_TITLE = "DOCUMENTO IV. MEDICIONES"
_BUDGET_DOCUMENT_TITLE = "DOCUMENTO V. PRESUPUESTO"
_IMPLEMENTATION_WARNING = (
    "[VALIDAR] Revisar e incorporar al proyecto el plano específico de implantación y "
    "señalización de obra; las fichas tipo no lo sustituyen."
)
_REFERENCE_RE = re.compile(
    r"\b(?:R|MP|PC|EPI|EQ)-[^\W_]+(?:-[^\W_]+)*\b", re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
_LIST_ITEM_RE = re.compile(r"^([-+*•]|\d+[.)]|[a-zA-Z]\))\s+(.+)$")
_AUXILIARY_MEANS_RE = re.compile(
    r"\b(?:andamios?|borriquetas?|puntales?|apeos?|cimbras?|encofrados?|"
    r"pasarelas?|montacargas?|maquinillos?|polipastos?|cabrestantes?|"
    r"escaleras?\s+(?:portatiles?|telescopicas?|extensibles?|de\s+(?:mano|tijera|aluminio))|"
    r"plataformas?\s+(?:elevadoras?|de\s+trabajo|suspendidas?))\b"
)
_CATEGORY_ORDER = ("R", "MP", "PC", "EPI", "EQ")
_CATEGORY_LABELS = {
    "R": "Riesgos",
    "MP": "Medidas preventivas",
    "PC": "Protecciones colectivas",
    "EPI": "Equipos de protección individual",
    "EQ": "Equipos y maquinaria",
}
_GENERAL_RISK_HEADERS = (
    "GRUPO DE RIESGO", "SITUACIONES PREVISIBLES", "CRITERIO PREVENTIVO GENERAL",
)
_CATALOGUE_HEADERS = ("Tipo", "Referencia", "Descripción")
_PPE_HEADERS = ("EPI", "USOS PREVISIBLES", "OBSERVACIONES")
_WORK_UNIT_HEADERS = (
    "Unidad de obra / ficha o familia BCCA",
    "Riesgos",
    "Medidas preventivas",
)
_NTP_HEADERS = ("Capítulo del presupuesto", "NTP confirmadas")
_MEASUREMENT_HEADERS = (
    "CÓDIGO", "UD", "RESUMEN / COMENTARIO", "UDS", "LONGITUD", "ANCHURA", "ALTURA", "K", "PARCIAL",
)
_BUDGET_HEADERS = ("CÓDIGO", "UD", "RESUMEN", "CANTIDAD", "PRECIO", "IMPORTE")
_LEGACY_SAFETY_DESCRIPTIONS = {
    "Unidad vinculada a partidas del presupuesto; cantidad mínima propuesta. Revisar por técnico competente.",
    "Partida propuesta por la matriz de Seguridad y Salud. Revisar por técnico competente.",
}
_LEGACY_PPE_DISCLAIMER = (
    " La relación siguiente es orientativa y no sustituye la selección específica para el proyecto."
)


@dataclass(frozen=True)
class _Block:
    kind: str
    text: str = ""
    level: int = 0
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    project_summary: bool = False
    section_rows: tuple[int, ...] = ()
    blue_table: bool = False
    bold_rows: tuple[int, ...] = ()
    highlighted_rows: tuple[int, ...] = ()
    economic_table: bool = False


@dataclass
class _StudyContent:
    title: str
    project_title: str
    chapter_label: str
    currency_label: str
    blocks: list[_Block] = field(default_factory=list)
    warnings: tuple[str, ...] = ()


def _normalized_text(value: str) -> str:
    """Normaliza tildes y mayusculas para aplicar las reglas de clasificacion."""
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()


def _budget_resources(
    app: BudgetApp, items: list[ProjectItem], pending: list[str],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Separa medios auxiliares fisicos y maquinaria sin incluir porcentajes ni precios auxiliares."""
    auxiliaries: dict[tuple[str, str], tuple[str, str]] = {}
    machinery: dict[tuple[str, str], tuple[str, str]] = {}
    for item in items:
        if item.measurement <= 0 or item.unit == "%" or "%" in item.code:
            continue
        dataset = app._component_dataset(item)

        def include(code: str, description: str, kind: str) -> None:
            """Clasifica cada equipo una sola vez, respetando los tipos editados del presupuesto."""
            if kind in {"1", "%"}:
                return
            if _AUXILIARY_MEANS_RE.search(_normalized_text(description)):
                found = auxiliaries
            elif kind == "2":
                found = machinery
            else:
                return
            label = description.strip() or code
            found[(code, _normalized_text(label))] = (code, label)

        def walk(code: str, quantity: float, stack: tuple[str, ...]) -> None:
            """Recorre tambien los descompuestos anidados y avisa si faltan recursos."""
            if not Decimal(str(quantity)).is_finite():
                raise ValueError(f"El rendimiento del recurso {code} no es finito.")
            if quantity <= 0 or "%" in code:
                return
            if code in stack:
                raise ValueError(f"Descomposición circular: {' → '.join((*stack, code))}")
            concept = item.component_concepts.get(code)
            if concept is None and dataset is not None:
                concept = dataset.concepts.get(code)
            if concept is None:
                pending.append(
                    f"[VALIDAR] Falta la descripción del recurso {code} de la partida "
                    f"{item.code} para identificar sus medios auxiliares y maquinaria."
                )
                return
            kind = app._budget_concept_type(item, code)
            if kind == "%" or concept.unit == "%":
                return
            include(code, concept.summary, kind)
            if kind == "aux" and not concept.children:
                pending.append(f"[VALIDAR] Falta la descomposición del auxiliar {code}.")
            for child, factor in concept.children:
                walk(child, quantity * factor, (*stack, code))

        include(item.code, item.summary, app._budget_concept_type(item))
        for code, quantity in item.decomposition:
            walk(code, quantity, (item.code,))
    return list(auxiliaries.values()), list(machinery.values())


def _inline_text(value: str, *, preserve_links: bool = False) -> str:
    """Elimina la sintaxis Markdown que no aporta formato al informe final."""
    if not preserve_links:
        value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return value.replace("`", "").replace("**", "").replace("__", "")


def _display_value(value: str) -> str:
    """Mantiene los valores en mayusculas sin cambiar el simbolo de metro cuadrado."""
    return re.sub(r"(?<!\w)M²(?!\w)", "m²", value.upper())


def _template_value(name: str, value: str) -> str:
    """Conserva el resumen y los textos normativos; capitaliza los demás datos."""
    return value if name in {
        "descripcion_obras", "justificacion_estudio", "normativa_confirmada", "normas_une",
    } else _display_value(value)


def _table_cells(line: str) -> list[str] | None:
    """Separa una fila Markdown en celdas y descarta las lineas que no son tablas."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _is_table_separator(cells: list[str]) -> bool:
    """Identifica las filas Markdown usadas solo para separar cabeceras."""
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells
    )


def _markdown_blocks(
    text: str, replacements: dict[str, str] | None = None, *, skip_title: bool = False,
) -> list[_Block]:
    """Convierte titulos, listas, parrafos y tablas Markdown en bloques imprimibles."""
    replacements = replacements or {}

    def replace(match: re.Match[str]) -> str:
        """Sustituye una variable de plantilla o la marca para validacion."""
        name = match.group(1)
        return _template_value(name, replacements.get(
            name, f"[VALIDAR] Falta definir el dato {name}."
        ))

    lines = _PLACEHOLDER_RE.sub(replace, text).splitlines()
    result: list[_Block] = []
    index = 0
    skipped_title = False
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            if skip_title and not skipped_title and level == 1:
                skipped_title = True
            else:
                result.append(_Block("heading", _inline_text(heading.group(2)), min(level, 4)))
            index += 1
            continue
        cells = _table_cells(line)
        if cells is not None:
            table_lines = []
            while index < len(lines):
                row = _table_cells(lines[index])
                if row is None:
                    break
                table_lines.append(row)
                index += 1
            table_lines = [row for row in table_lines if not _is_table_separator(row)]
            if table_lines:
                headers = tuple(_inline_text(cell, preserve_links=True) for cell in table_lines[0])
                rows = tuple(
                    tuple(_inline_text(cell, preserve_links=True) for cell in row)
                    for row in table_lines[1:]
                )
                result.append(_Block(
                    "table", headers=headers, rows=rows,
                    blue_table=headers in (_GENERAL_RISK_HEADERS, _PPE_HEADERS),
                ))
            continue
        if line.startswith("```"):
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                index += 1
            index += 1
            continue
        if line.startswith(">"):
            line = line[1:].strip()
        item = _LIST_ITEM_RE.match(line)
        if item:
            expanded = lines[index].expandtabs(4)
            indentation = len(expanded) - len(expanded.lstrip())
            result.append(_Block(
                "list_item", _inline_text(line, preserve_links=True), min(indentation // 2, 3),
            ))
            index += 1
            continue
        kind = "caption" if re.match(r"^Tabla\s+\d+\.", line) else "paragraph"
        result.append(_Block(kind, _inline_text(line, preserve_links=True)))
        index += 1
    return result


def _read_library(archive_path: Path = LIBRARY_PATH) -> dict[str, str]:
    """Lee las plantillas necesarias sin extraer ni modificar la biblioteca."""
    try:
        with ZipFile(archive_path) as archive:
            return {
                key: _read_member(archive, member)
                for key, member in _TEMPLATE_MEMBERS.items()
            }
    except (
        OSError, KeyError, BadZipFile, RuntimeError, NotImplementedError, UnicodeError,
    ) as exc:
        raise ValueError(f"No se pudo leer la biblioteca ESS {archive_path}: {exc}") from exc


def _rows_from_markdown(text: str) -> list[list[str]]:
    """Extrae todas las filas de tabla Markdown de una plantilla."""
    rows = []
    for line in text.splitlines():
        cells = _table_cells(line)
        if cells is not None and not _is_table_separator(cells):
            rows.append([_inline_text(cell) for cell in cells])
    return rows


def _parse_catalogues(text: str) -> dict[str, str]:
    """Resuelve cada identificador preventivo contra el catalogo de la biblioteca."""
    result = {}
    for row in _rows_from_markdown(text):
        if len(row) >= 2 and _REFERENCE_RE.fullmatch(row[0]):
            result[_normalized_text(row[0]).upper()] = row[1]
    return result


def _parse_index(text: str) -> list[tuple[str, str]]:
    """Obtiene los prefijos BCCA y las rutas de sus fichas ESS."""
    result = []
    for line in text.splitlines():
        row = _table_cells(line)
        if row is None or len(row) < 3:
            continue
        prefix = _inline_text(row[0]).upper()
        if not re.fullmatch(r"[A-Z0-9]+", prefix):
            continue
        match = re.search(r"\(([^)]+\.md)\)", row[2])
        if match:
            path = posixpath.normpath(posixpath.join("UNI", match.group(1)))
            result.append((prefix, path))
    return result


def _reference_groups(text: str) -> dict[str, list[str]]:
    """Agrupa las referencias preventivas encontradas por tipo de catalogo."""
    groups = {category: [] for category in _CATEGORY_ORDER}
    for match in _REFERENCE_RE.findall(text):
        reference = _normalized_text(match).upper()
        category = reference.split("-", 1)[0]
        if reference not in groups[category]:
            groups[category].append(reference)
    return groups


def _merge_references(*groups: dict[str, list[str]]) -> dict[str, list[str]]:
    """Combina referencias en orden estable y elimina duplicados."""
    merged = {category: [] for category in _CATEGORY_ORDER}
    for group in groups:
        for category in _CATEGORY_ORDER:
            for value in group[category]:
                if value not in merged[category]:
                    merged[category].append(value)
    return merged


def _add_heading(blocks: list[_Block], text: str, level: int = 1) -> None:
    """Agrega un encabezado con nivel acotado para los dos formatos de salida."""
    blocks.append(_Block("heading", text, min(level, 4)))


def _add_markdown(
    blocks: list[_Block], text: str, replacements: dict[str, str] | None = None,
    *, heading_offset: int = 0,
) -> None:
    """Agrega una plantilla sin su titulo principal y ajusta sus niveles de epigrafe."""
    blocks.extend(
        replace(block, level=min(block.level + heading_offset, 4))
        if block.kind == "heading" else block
        for block in _markdown_blocks(text, replacements, skip_title=True)
    )


def _currency_label(settings) -> str:
    """Obtiene el nombre de la divisa configurada para los importes del estudio."""
    from budget_core import currency_label

    return currency_label(settings.currency, settings.custom_currency)


def _format_number(value: float | Decimal) -> str:
    """Formatea una magnitud conforme al formato numerico del presupuesto."""
    from budget_core import fmt_number

    return fmt_number(float(value))


def _format_amount(value: float | Decimal, currency: str) -> str:
    """Formatea un importe con la divisa del proyecto."""
    return f"{_format_number(value)} {currency}"


def _chapter_label(app, chapter: str) -> str:
    """Combina el codigo normalizado del capitulo con su titulo de presupuesto."""
    from budget_core import format_chapter_code

    title = app._chapter_title(chapter)
    code = format_chapter_code(chapter)
    return f"{code} - {title}" if title and title != code else code


def _read_member(archive: ZipFile, path: str) -> str:
    """Lee el texto ESS sin su historial interno e identifica el documento si falla."""
    try:
        return read_revisions(archive.read(path).decode("utf-8-sig"))[1]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"No se pudo leer la ficha ESS {path}: {exc}") from exc


def _load_selected_fiches(
    archive: ZipFile, paths: list[str], pending: list[str],
) -> tuple[dict[str, dict[str, list[str]]], list[tuple[str, str]]]:
    """Lee fichas BCCA completas, indexa sus referencias y recoge sus validaciones."""
    by_path: dict[str, dict[str, list[str]]] = {}
    contents = []
    for path in paths:
        text = _read_member(archive, path)
        contents.append((path, text))
        by_path[path] = _reference_groups(text)
        headings = {
            (_normalized_text(block.text), block.level)
            for block in _markdown_blocks(text) if block.kind == "heading"
        }
        for title, level in (
            ("Definición y alcance", 2), ("Riesgos detectados", 2), ("Medidas preventivas", 2),
            ("Protecciones colectivas", 3), ("Equipos de protección individual", 3),
            ("Equipos de trabajo", 3), ("Validación", 2),
        ):
            if (_normalized_text(title), level) not in headings:
                raise ValueError(f"La ficha ESS {path} no contiene el apartado {title}.")
        lines = text.splitlines()
        in_validations = False
        for line in lines:
            stripped = line.strip()
            if re.match(r"^#{1,3}\s+", stripped):
                in_validations = "validacion" in _normalized_text(stripped)
                continue
            if in_validations and stripped:
                validation = _inline_text(stripped.lstrip("- ").strip())
                message = (
                    f"[VALIDAR] Revisar ficha ESS {PurePosixPath(path).stem}: {validation}"
                )
                if message not in pending:
                    pending.append(message)
    return by_path, contents


def _study_fiches(
    app, pending: list[str],
    family_risk_links: dict[str, FamilyRiskLink] | None = None,
):
    """Comparte entre revision y exportacion la misma vinculacion BCCA de las fichas."""
    work_items = study_items(app)
    library_path = shared_library_path(app, "safety_health", LIBRARY_PATH)
    templates = _read_library(library_path)
    index = _parse_index(templates["index"])
    fiche_paths: list[str] = []
    item_fiches: list[tuple[ProjectItem, list[str]]] = []
    for item in work_items:
        code = re.sub(r"\s+", "", item.code).upper()
        matches = [(prefix, path) for prefix, path in index if code.startswith(prefix)]
        if matches:
            longest = max(len(prefix) for prefix, _ in matches)
            selected = [(prefix, path) for prefix, path in matches if len(prefix) == longest]
            paths = list(dict.fromkeys(path for _, path in selected))
            for _, path in selected:
                if path not in fiche_paths:
                    fiche_paths.append(path)
                canonical_code = PurePosixPath(path).stem.split("-", 1)[0]
                if not code.startswith(canonical_code):
                    pending.append(
                        f"[VALIDAR] Revisar la correspondencia del código anterior "
                        f"{item.code} con la ficha BCCA {canonical_code}."
                    )
        else:
            paths = []
            if family_risk_link_for_code(code, family_risk_links or {}) is None:
                pending.append(
                    f"[VALIDAR] Partida sin ficha ESS ni vínculo de riesgos BCCA: "
                    f"{item.code} - {item.summary}."
                )
        item_fiches.append((item, paths))

    fiche_paths.sort(key=lambda path: PurePosixPath(path).parts)
    if fiche_paths:
        try:
            with ZipFile(library_path) as archive:
                fiche_references, fiche_contents = _load_selected_fiches(
                    archive, fiche_paths, pending,
                )
        except (OSError, BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise ValueError(f"No se pudieron leer las fichas ESS seleccionadas: {exc}") from exc
    else:
        fiche_references, fiche_contents = {}, []
    return templates, item_fiches, fiche_references, fiche_contents


def _work_units(app, item_fiches, fiche_references) -> list[WorkUnit]:
    """Expone las referencias de cada partida sin clasificar por palabras de su texto."""
    units = []
    for item, paths in item_fiches:
        references = _merge_references(*(fiche_references[path] for path in paths))
        units.append(WorkUnit(
            item.code, item.summary,
            tuple(PurePosixPath(path).stem.split("-", 1)[0] for path in paths),
            tuple(references["R"]), tuple(references["MP"]),
            item.chapter, app._chapter_title(item.chapter),
        ))
    return units


def study_work_units(app) -> list[WorkUnit]:
    """Obtiene el contexto revisable incluso antes de crear el capitulo presupuestario SS."""
    _templates, item_fiches, references, _contents = _study_fiches(app, [])
    return _work_units(app, item_fiches, references)


def study_technical_review(app) -> tuple[str, dict[str, str], list[str]]:
    """Comparte entre revisión y exportación las fuentes técnicas del ámbito seleccionado."""
    pending: list[str] = []
    templates, item_fiches, references, _contents = _study_fiches(app, pending)
    if not item_fiches:
        raise ValueError("No hay unidades de obra para revisar las condiciones técnicas.")
    auxiliaries, machinery = _budget_resources(app, study_items(app), pending)
    context, proposals = technical_review_data(
        _work_units(app, item_fiches, references), _merge_references(*references.values()),
        auxiliaries + machinery, app.safety_health_state.get("external_conditions"),
        templates["specification"],
    )
    return context, proposals, pending


def _add_normative_chapter(blocks, template, units, state, pending, chapters) -> None:
    """Incluye solo decisiones confirmadas para el contexto actual y explicita las carencias."""
    review = restore_review(state.get("regulatory_review"))
    confirmed = review.get("context") == review_context(units)
    if not confirmed:
        pending.append("[VALIDAR] Confirmar normativa y NTP para las unidades de obra actuales.")
    laws = [
        law.title for law in REGULATIONS if confirmed and review["laws"].get(law.code, False)
    ]
    _add_markdown(blocks, template, {
        "normas_une": general_une_references(),
        "normativa_confirmada": "\n".join(f"- {title}" for title in laws) if laws else (
            "[VALIDAR] No hay disposiciones confirmadas para las unidades de obra actuales."
        ),
    }, heading_offset=-1)
    catalog = load_ntps()
    chapter_ntps = {code: set() for code, _label in chapters}
    for unit in units:
        codes = {
            code for code, selected in review.get("ntps", {}).get(unit.code, {}).items()
            if selected and confirmed
        }
        for code in codes:
            if code not in catalog:
                raise ValueError(f"La NTP confirmada {code} ya no existe en el catálogo.")
        chapter_ntps[unit.chapter].update(codes)
    rows = []
    included = set()
    for chapter, label in chapters:
        codes = sorted(chapter_ntps[chapter] - included, key=lambda code: int(code.split()[1]))
        included.update(codes)
        rows.append((
            label,
            "\n".join(
                f"• {code} — {catalog[code].title} "
                f"({catalog[code].publication}; {catalog[code].status})."
                for code in codes
            ) or (
                "Sin NTP adicionales; las compartidas figuran en capítulos anteriores."
                if confirmed and chapter_ntps[chapter] else
                "Sin NTP asignadas tras la revisión." if confirmed
                else "[VALIDAR] Asignación NTP pendiente de confirmación."
            ),
        ))
    blocks.extend((
        _Block("caption", "Tabla 6. NTP confirmadas por capítulo del presupuesto."),
        _Block("table", headers=_NTP_HEADERS, rows=tuple(rows), blue_table=True),
    ))


def _basic_sections(template: str, titles: tuple[str, ...], pending: list[str]) -> str:
    """Conserva las secciones editables seleccionadas o la plantilla completa si faltan."""
    sections = re.split(r"(?m)(?=^## )", template)
    chosen = [
        section for section in sections
        if section.splitlines() and section.splitlines()[0].removeprefix("## ").strip() in titles
    ]
    found = {section.splitlines()[0][3:].strip() for section in chosen}
    if found != set(titles):
        notice = (
            "[VALIDAR] La plantilla personalizada no contiene todos los apartados previstos "
            "para el estudio básico; se ha conservado completa."
        )
        pending.append(notice)
        return f"{template}\n\n{notice}\n"
    return "\n".join(chosen)


def _add_safety_budget_documents(blocks: list[_Block], items: list[ProjectItem], app) -> None:
    """Construye una tabla de mediciones y otra de presupuesto con las partidas reales."""
    from budget_core import fmt_number, format_chapter_code

    chapters = list(dict.fromkeys([*app.chapter_order, *(item.chapter for item in items)]))
    for report in ("measurements", "budget"):
        measurements = report == "measurements"
        headers = _MEASUREMENT_HEADERS if measurements else _BUDGET_HEADERS
        rows: list[tuple[str, ...]] = []
        bold_rows: list[int] = []
        chapter_rows: list[int] = []

        def add_row(values: tuple[str, ...], *, bold: bool = False, chapter: bool = False) -> None:
            """Registra filas destacadas sin dividir el listado en varias tablas."""
            if bold or chapter:
                bold_rows.append(len(rows))
            if chapter:
                chapter_rows.append(len(rows))
            rows.append(values + ("",) * (len(headers) - len(values)))

        for chapter in chapters:
            chapter_items = [item for item in items if item.chapter == chapter]
            if not chapter_items:
                continue
            code = format_chapter_code(chapter)
            add_row((code, "", app._chapter_title(chapter)), chapter=True)
            for item in chapter_items:
                generated = item.source.startswith(SAFETY_BUDGET_SOURCE)
                description = item.text
                if generated and description in _LEGACY_SAFETY_DESCRIPTIONS:
                    description = ""
                if measurements:
                    add_row((item.code, item.unit, item.summary), bold=True)
                else:
                    add_row((
                        item.code, item.unit, item.summary, fmt_number(item.measurement),
                        fmt_number(float(money(item.unit_price))), fmt_number(float(money(item.import_total))),
                    ), bold=True)
                if description:
                    add_row(("", "", description))
                if measurements:
                    for line in item.measurement_lines:
                        comment = line.comment
                        if generated and (
                            comment == "Propuesta vinculada al presupuesto (1 unidad mínima)"
                            or re.fullmatch(r"Propuesta SS \(.+ % del presupuesto previo\)", comment)
                        ):
                            comment = ""
                        add_row((
                            "", "", comment,
                            *("" if value is None else fmt_number(value, 3) for value in (
                                line.units, line.length, line.width, line.height,
                            )),
                            fmt_number(line.k, 3), fmt_number(line.partial, 3),
                        ))
                    add_row(("", "", "MEDICIÓN TOTAL", "", "", "", "", "",
                             fmt_number(item.measurement, 3)), bold=True)
            if not measurements:
                total = sum((money(item.import_total) for item in chapter_items), Decimal())
                add_row(("", "", f"TOTAL {code}", "", "", fmt_number(float(total))), bold=True)
        if not measurements:
            total = sum((money(item.import_total) for item in items), Decimal())
            add_row(("", "", "TOTAL EJECUCIÓN MATERIAL", "", "", fmt_number(float(total))), bold=True)
        title = _MEASUREMENTS_DOCUMENT_TITLE if measurements else _BUDGET_DOCUMENT_TITLE
        heading = "Mediciones por capítulos" if measurements else "Presupuesto por capítulos"
        blocks.extend((
            _Block("document", title),
            _Block("heading", heading, 1),
            _Block("table", headers=headers, rows=tuple(rows),
                   bold_rows=tuple(bold_rows), highlighted_rows=tuple(chapter_rows),
                   economic_table=True),
        ))


def _build_study_content(app) -> _StudyContent:
    """Combina los datos del proyecto con las secciones y reglas de las plantillas ESS."""
    work_items = study_items(app)
    safety_chapters = safety_budget_chapters(app)
    safety_items = [item for item in app.budget_items if item.chapter in safety_chapters]
    assessment = evaluate_study_criteria(app)
    state = app.safety_health_state if isinstance(app.safety_health_state, dict) else {}
    selected_criteria = _study_criteria_values(state.get("study_criteria"))
    if selected_criteria is None:
        selected_criteria = assessment.fulfilled
    full_study = any(selected_criteria)
    if full_study and not safety_items:
        raise ValueError("Crea primero el capítulo CAP. XX integrado en el presupuesto.")
    if not work_items:
        raise ValueError("El presupuesto no contiene partidas de obra para clasificar en el ESS.")
    pending: list[str] = []
    family_risk_links = load_family_risk_links()
    templates, item_fiches, fiche_references, fiche_contents = _study_fiches(
        app, pending, family_risk_links,
    )
    family_only_items = [
        item for item, paths in item_fiches
        if not paths and family_risk_link_for_code(item.code, family_risk_links) is not None
    ]
    if family_only_items:
        pending.append(
            "[VALIDAR] Revisar los riesgos y medidas asignados por familia BCCA "
            "según el procedimiento, los equipos y las condiciones reales de cada tajo."
        )
    catalogues = _parse_catalogues(templates["catalogues"])
    matched_codes = [
        item.code for item, paths in item_fiches
        if paths or family_risk_link_for_code(item.code, family_risk_links) is not None
    ]
    all_references = _merge_references(*fiche_references.values())
    title = (
        "ESTUDIO DE SEGURIDAD Y SALUD" if full_study
        else "ESTUDIO BÁSICO DE SEGURIDAD Y SALUD"
    )
    chapter_label = _chapter_label(app, safety_items[0].chapter) if safety_items else ""
    if full_study:
        pending.append(_IMPLEMENTATION_WARNING)
    settings = app.report_settings
    project_title = app._project_title().upper()
    currency = _currency_label(settings)
    service = str(getattr(settings, "service", "")).strip()

    def missing(field_name: str) -> str:
        """Registra un dato no disponible y devuelve su aviso para la plantilla."""
        message = (
            f"[VALIDAR] Falta definir {field_name} antes de aprobar el Plan de Seguridad y Salud."
        )
        pending.append(message)
        return f"[VALIDAR] Falta definir {field_name}."

    description = study_work_summary(app) or missing(
        "el resumen de la actuación en la primera ventana de Seguridad y Salud"
    )
    totals = budget_totals(
        sum(float(item.import_total) for item in app.budget_items), settings,
    )
    duration_text = (
        f"{assessment.duration_days} días laborables"
        if assessment.duration_days else missing("el plazo de ejecución")
    )
    workers_text = f"{int(assessment.peak_workers)} " + (
        "persona" if assessment.peak_workers == 1 else "personas"
    )
    pending.append("[VALIDAR] Confirmar el número máximo real de trabajadores simultáneos.")
    if assessment.worker_days <= 0:
        pending.append("[VALIDAR] Confirmar el volumen de mano de obra previsto.")

    saved_conditions = restore_external_conditions(state.get("external_conditions"))
    if state.get("centros_asistenciales_modo") == "tabla":
        centers = format_centers(restore_centers(state))
    else:
        centers = str(state.get(
            "centros_asistenciales_texto", state.get("centros_asistenciales", ""),
        )).strip()
    selected_centers = format_sicess_centers(
        restore_sicess_centers(state.get("centros_sicess_seleccionados", []))
    )
    centers = "\n\n".join(filter(None, (centers, selected_centers)))
    center_condition = saved_conditions.get("centro_asistencial", {})
    center_default = next(
        default for key, _label, default in EXTERNAL_CONDITIONS if key == "centro_asistencial"
    )
    if not centers and translate(center_condition.get("description", ""), "es") != center_default:
        centers = center_condition.get("description", "")
    if not centers and saved_conditions.get("centro_asistencial", {}).get("applicability") != "no":
        centers = missing("el centro asistencial de referencia y su teléfono")

    location = str(getattr(settings, "location", "")).strip()
    start_date = "[VALIDAR] Falta definir la fecha de inicio prevista."
    planning_state = getattr(app, "planning_state", {})
    params_state = planning_state.get("params", {}) if isinstance(planning_state, dict) else {}
    planned_start = params_state.get("start") if isinstance(params_state, dict) else None
    if isinstance(planned_start, str):
        try:
            start_date = date.fromisoformat(planned_start).strftime("%d/%m/%Y")
        except ValueError:
            pending.append("[VALIDAR] La fecha de inicio guardada en la planificación no es válida.")
    else:
        pending.append("[VALIDAR] Falta definir la fecha de inicio prevista.")

    chapter_titles = list(dict.fromkeys(
        app._chapter_title(item.chapter)
        for item in work_items if app._chapter_title(item.chapter)
    ))
    cpv_entries = resolve_cpv_codes(getattr(settings, "cpv_codes", []))
    project_type = "\n".join(entry.label for entry in cpv_entries) or missing(
        "los códigos CPV de la obra en la configuración general"
    )
    if chapter_titles:
        project_type += "\n\n" + "\n".join(f"• {chapter}" for chapter in chapter_titles)
    unit_names = list(dict.fromkeys(f"{item.code} — {item.summary}" for item in work_items))
    bcca_codes = ", ".join(dict.fromkeys(matched_codes)) or missing(
        "los códigos BCCA identificados"
    )
    surfaces = [
        Decimal(str(item.measurement)) for item in work_items
        if item.unit.strip().casefold() in {"m2", "m²", "m^2"}
    ]
    if any(not surface.is_finite() or surface < 0 for surface in surfaces):
        raise ValueError("Las mediciones de superficie deben ser finitas y no negativas.")
    surface_text = (
        f"{_format_number(max(surfaces))} m²"
        if surfaces else missing("la superficie de actuación")
    )
    municipalities = resolve_municipalities(getattr(settings, "municipality_codes", []))
    municipality_names = "; ".join(
        municipality.label if len(municipalities) > 1 else municipality.name
        for municipality in municipalities
    )
    provinces = "; ".join(dict.fromkeys(
        municipality.province for municipality in municipalities
    ))
    communities = "; ".join(dict.fromkeys(
        municipality.autonomous_community for municipality in municipalities
    ))

    data_values = {
        "Titulo del proyecto": project_title or missing("el título oficial del proyecto"),
        "Tipo de actuacion": project_type,
        "Promotor / entidad": str(getattr(settings, "organization", "")).strip()
        or missing("la entidad promotora"),
        "Municipio": municipality_names or missing("el municipio"),
        "Provincia": provinces or missing("la provincia"),
        "Comunidad autonoma": communities or missing("la comunidad autónoma"),
        "Emplazamiento": location or missing("el emplazamiento de la obra"),
        "Tecnico redactor": (
            settings.document_author.strip() or missing("el técnico redactor")
        ) if settings.show_author else "",
        "Fecha de emision": date.today().strftime("%d/%m/%Y"),
        "Presupuesto de ejecucion material": _format_amount(totals["pem"], currency),
        "Presupuesto de licitacion": _format_amount(totals["total"], currency),
        "Presupuesto de Seguridad y Salud integrado": (
            _format_amount(sum((money(item.import_total) for item in safety_items), Decimal()), currency)
            if safety_items else "No se ha incorporado un capítulo de Seguridad y Salud."
        ),
        "Plazo de ejecucion": duration_text,
        "Numero maximo simultaneo de trabajadores": workers_text,
        "Superficie, longitud o volumen": surface_text,
        "Fecha prevista de inicio": start_date,
        "Descripcion de las obras": description,
        "Partidas presupuestarias": "Relación completa en el anexo de trazabilidad presupuestaria.",
        "Unidades de obra aplicables": "; ".join(unit_names),
        "Codigos BCCA identificados": bcca_codes,
        "Centro asistencial": centers,
    }
    external_values = {}
    external_descriptions = {}
    for key, label, default in EXTERNAL_CONDITIONS:
        condition = saved_conditions.get(key, {
            "description": default, "applicability": "pending",
        })
        condition_text = condition["description"]
        if (
            key == "centro_asistencial" and condition_text == default
            and centers and not centers.startswith("[VALIDAR]")
        ):
            condition_text = centers
        applicability = condition["applicability"]
        external_descriptions[key] = condition_text
        external_values[key] = (
            f"{condition_text}\nAplicabilidad: {APPLICABILITY[applicability]}"
        ).strip()
        if applicability == "pending":
            pending.append(f"[VALIDAR] Revisar la aplicabilidad de {label.lower()}.")
        elif applicability == "yes" and not condition_text:
            pending.append(f"[VALIDAR] Describir el condicionante {label.lower()}.")

    contacts = [centers or "No se han indicado centros asistenciales."]
    center_note = center_condition.get("description", "")
    if (
        center_note and translate(center_note, "es") != center_default
        and center_note not in centers
    ):
        contacts.append(center_note)
    contacts.extend((
        "Emergencias: 112",
        f"Aplicabilidad: {APPLICABILITY[center_condition.get('applicability', 'pending')]}",
    ))
    auxiliary_rows, machinery_rows = _budget_resources(app, work_items, pending)
    auxiliary_means = "\n".join(f"• {code} — {label}" for code, label in auxiliary_rows) or (
        "No se han identificado medios auxiliares en el presupuesto disponible."
    )
    machinery = "\n".join(f"• {code} — {label}" for code, label in machinery_rows) or (
        "No se ha identificado maquinaria en el presupuesto disponible."
    )
    replacements = {
        "titulo_proyecto": data_values["Titulo del proyecto"],
        "tipo_actuacion": data_values["Tipo de actuacion"],
        "entidad": data_values["Promotor / entidad"],
        "municipio": data_values["Municipio"],
        "provincia": data_values["Provincia"],
        "comunidad_autonoma": data_values["Comunidad autonoma"],
        "servicio": service,
        "expediente": str(getattr(settings, "expediente", "")).strip(),
        "emplazamiento": data_values["Emplazamiento"],
        "autor_estudio": data_values["Tecnico redactor"],
        "fecha_documento": data_values["Fecha de emision"],
        "presupuesto_ejecucion_material": data_values["Presupuesto de ejecucion material"],
        "presupuesto_licitacion": data_values["Presupuesto de licitacion"],
        "presupuesto_seguridad_salud": data_values["Presupuesto de Seguridad y Salud integrado"],
        "plazo_ejecucion": duration_text,
        "numero_trabajadores_punta": workers_text,
        "superficie_actuacion": data_values["Superficie, longitud o volumen"],
        "fecha_inicio_prevista": start_date,
        "descripcion_obras": description,
        "partidas_presupuesto": data_values["Partidas presupuestarias"],
        "unidades_obra": "; ".join(unit_names),
        "unidades_obra_bcca": bcca_codes,
        **external_descriptions,
        "medios_auxiliares": auxiliary_means,
        "maquinaria_empleada": machinery,
        "centros_asistenciales_resumen": "\n\n".join(contacts),
        "riesgos_detectados": ", ".join(all_references["R"]),
        "medidas_preventivas": ", ".join(all_references["MP"]),
        "tipo_estudio": title,
    }
    criteria_labels = (
        "a) Presupuesto de ejecución por contrata con IVA igual o superior a 450.759,08 EUR.",
        "b) Duración superior a 30 días laborables y más de 20 trabajadores simultáneos.",
        "c) Volumen de mano de obra superior a 500 jornadas.",
        "d) Obra de túneles, galerías, conducciones subterráneas o presas.",
    )
    fulfilled_labels = [
        label for selected, label in zip(selected_criteria, criteria_labels) if selected
    ]
    if fulfilled_labels:
        justification = (
            "Se cumplen los siguientes criterios del artículo 4.1 del RD 1627/1997:\n\n"
            + "\n\n".join(f"- {label}" for label in fulfilled_labels)
            + f"\n\nPor tanto, se define un {title}."
        )
    else:
        justification = (
            "No se cumple ninguno de los criterios del artículo 4.1 del RD 1627/1997.\n\n"
            f"Por tanto, se define un {title}."
        )
    replacements["justificacion_estudio"] = justification
    replacements = {name: _template_value(name, value) for name, value in replacements.items()}
    project_data_rows = []
    project_data_fields = set()
    section_rows = []
    reference_summary_rows = {}
    summary_categories = {"riesgos_detectados": "R", "medidas_preventivas": "MP"}
    for line in templates["project_data"].splitlines():
        if line.strip() == "## Condicionantes externos":
            section_rows.append(len(project_data_rows))
            project_data_rows.append(("Condicionantes externos", ""))
        row = _table_cells(line)
        if row is None:
            continue
        row = [_inline_text(cell) for cell in row]
        if len(row) < 2:
            continue
        variable = re.fullmatch(r"\{\{([A-Za-z0-9_]+)\}\}", row[0])
        if variable:
            name = variable.group(1)
            if name not in replacements:
                raise ValueError(f"No hay un valor preparado para la variable ESS {name}.")
            project_data_fields.add(name)
            if name in summary_categories:
                reference_summary_rows[len(project_data_rows)] = summary_categories[name]
            project_data_rows.append((
                row[1],
                _display_value(external_values[name]) if name in external_values else replacements[name],
            ))
    if service and "servicio" not in project_data_fields:
        service_index = next(
            (
                index + 1 for index, (label, _value) in enumerate(project_data_rows)
                if label == "Promotor / entidad"
            ),
            len(project_data_rows),
        )
        project_data_rows.insert(
            service_index, ("Servicio promotor", _template_value("servicio", service)),
        )

    blocks: list[_Block] = []
    _add_heading(blocks, "Objeto y alcance", 1)
    introduction = re.split(r"(?m)^##\s+", templates["project_data"], maxsplit=1)[0]
    _add_markdown(blocks, introduction, replacements)
    blocks.append(_Block(
        "caption",
        "Tabla 1. Datos del proyecto y condiciones de ejecución de la obra.",
    ))
    summary_index = len(blocks)
    blocks.append(_Block(
        "table",
        headers=("Aspecto del proyecto", "Descripción / información"),
        rows=tuple(project_data_rows),
        project_summary=True,
        section_rows=tuple(section_rows),
    ))

    if not full_study:
        _add_heading(blocks, "Normativa de aplicación", 2)
        units = _work_units(app, item_fiches, fiche_references)
        review = restore_review(state.get("regulatory_review"))
        confirmed = review.get("context") == review_context(units)
        laws = [law.title for law in REGULATIONS if confirmed and review["laws"].get(law.code, False)]
        if not confirmed:
            pending.append("[VALIDAR] Confirmar normativa para las unidades de obra actuales.")
        if laws:
            blocks.append(_Block(
                "paragraph", "Se aplicarán las disposiciones vigentes correspondientes a los trabajos "
                "y sus modificaciones, tras su revisión para las condiciones reales de la obra.",
            ))
            blocks.extend(_Block("list_item", f"• {law}", 1) for law in laws)
        else:
            blocks.append(_Block("paragraph", "[VALIDAR] No hay disposiciones confirmadas para esta obra."))

    _add_heading(blocks, "Memoria descriptiva", 1)
    memory = templates["memory"].replace(_LEGACY_PPE_DISCLAIMER, "")
    memory = memory if full_study else _basic_sections(
        memory, ("Criterios preventivos generales",), pending,
    )
    _add_markdown(blocks, memory, replacements)
    _add_heading(blocks, "Instalaciones provisionales y emergencias", 2)
    _add_markdown(blocks, templates["emergencies"], replacements, heading_offset=1)

    _add_heading(blocks, "Fichas de unidades de obra aplicables", 1)
    _add_markdown(blocks, templates["general_conditions"], replacements)
    work_unit_rows = []
    for item, paths in item_fiches:
        references = _merge_references(*(fiche_references[path] for path in paths))
        family_link = family_risk_link_for_code(item.code, family_risk_links)
        if family_link is not None and not paths:
            family_references = {category: [] for category in _CATEGORY_ORDER}
            family_references["R"] = list(family_link.risks)
            family_references["MP"] = list(family_link.measures)
            references = _merge_references(references, family_references)
        codes = ", ".join(PurePosixPath(path).stem.split("-", 1)[0] for path in paths)
        normalized_code = re.sub(r"\s+", "", item.code).upper()
        work_unit_rows.append((
            f"{item.code} — {item.summary}\nFicha BCCA: {codes}" if paths else (
                f"{item.code} — {item.summary}\nFamilia BCCA: "
                f"{normalized_code[:5]}" if family_link is not None else
                f"{item.code} — {item.summary}\n"
                "[VALIDAR] Ficha y vínculo de riesgos específicos pendientes."
            ),
            ", ".join(references["R"]) or "[VALIDAR] Identificación de riesgos pendiente.",
            ", ".join(references["MP"]) or "[VALIDAR] Medidas preventivas pendientes.",
        ))
    blocks.extend((
        _Block(
            "paragraph",
            "Las partidas con ficha ESS incorporan sus referencias específicas. Para las demás, "
            "se utiliza la vinculación por familia BCCA (dos cifras y tres letras), "
            "basada en las categorías preventivas disponibles. Esta vinculación "
            "cubre los capítulos disponibles entre el 01 y el 17; el capítulo 18 y "
            "los códigos fuera de ese patrón quedan pendientes de una revisión específica.",
        ),
        _Block(
            "caption",
            "Tabla 5. Vinculación por unidad de obra: riesgos y medidas preventivas.",
        ),
        _Block("table", headers=_WORK_UNIT_HEADERS, rows=tuple(work_unit_rows), blue_table=True),
    ))
    for path, text in fiche_contents:
        heading = next(
            (_inline_text(line.lstrip("# ").strip()) for line in text.splitlines()
             if line.startswith("# ")),
            PurePosixPath(path).stem.split("-", 1)[-1],
        )
        _add_heading(blocks, heading, 2)
        _add_markdown(blocks, text, heading_offset=1)

    _add_heading(blocks, "Seguimiento, control, coordinación, formación y documentación", 1)
    monitoring = templates["monitoring"] if full_study else _basic_sections(
        templates["monitoring"],
        ("Localización y control de servicios ocultos", "Presencia de recursos preventivos",
         "Previsiones para trabajos posteriores"), pending,
    )
    _add_markdown(blocks, monitoring, replacements)

    if full_study:
        technical_context, _proposals = technical_review_data(
            _work_units(app, item_fiches, fiche_references), all_references,
            auxiliary_rows + machinery_rows, state.get("external_conditions"), templates["specification"],
        )
        blocks.append(_Block("document", _PLIEGO_DOCUMENT_TITLE))
        _add_normative_chapter(
            blocks, templates["normative"], _work_units(app, item_fiches, fiche_references), state, pending,
            [(chapter.code, _chapter_label(app, chapter.code))
             for chapter in study_chapters(app) if chapter.selected],
        )
        specification = selected_technical_template(
            templates["specification"], state.get("technical_review"), technical_context, pending,
        )
        _add_markdown(blocks, specification, replacements, heading_offset=-1)
        _add_safety_budget_documents(blocks, safety_items, app)

    all_references = _merge_references(all_references, _reference_groups("\n".join(
        value for block in blocks
        for value in (block.text, *block.headers, *(cell for row in block.rows for cell in row))
    )))
    catalogue_rows = []
    described = set()
    remaining = [reference for group in all_references.values() for reference in group]
    while remaining:
        reference = remaining.pop(0)
        if reference in described:
            continue
        described.add(reference)
        description_text = catalogues.get(reference)
        if description_text is None:
            pending.append(f"[VALIDAR] No existe descripción de catálogo para {reference}.")
            description_text = "[VALIDAR] Descripción de catálogo pendiente."
        catalogue_rows.append((
            _CATEGORY_LABELS[reference.split("-", 1)[0]], reference, description_text,
        ))
        remaining.extend(
            child for group in _reference_groups(description_text).values()
            for child in group if child not in described
        )
    for row_index, category in reference_summary_rows.items():
        references = ", ".join(
            row[1] for row in catalogue_rows if row[1].startswith(f"{category}-")
        )
        project_data_rows[row_index] = (
            project_data_rows[row_index][0],
            references or "[VALIDAR] Identificación pendiente.",
        )
    blocks[summary_index] = replace(blocks[summary_index], rows=tuple(project_data_rows))
    if catalogue_rows:
        catalogue_rows.sort(key=lambda row: _CATEGORY_ORDER.index(row[1].split("-", 1)[0]))
        catalogue_blocks = [
            _Block("heading", "Catálogo preventivo", 2),
            _Block(
                "paragraph",
                "El catálogo reúne las referencias preventivas aplicables a las unidades de obra "
                "y a los condicionantes del proyecto. Define los riesgos y los criterios de "
                "selección de las medidas preventivas, protecciones colectivas, equipos de "
                "protección individual y maquinaria. Los códigos citados en el estudio enlazan "
                "con su definición en la tabla siguiente.",
            ),
            _Block("caption", "Tabla 3. Catálogo preventivo: referencias y definiciones."),
            _Block(
                "table", headers=_CATALOGUE_HEADERS,
                rows=tuple(catalogue_rows), blue_table=True,
            ),
            _Block(
                "paragraph",
                "La selección de riesgos, medidas, protecciones, EPI y equipos debe confirmarse "
                "con la evaluación de riesgos y el procedimiento real de cada tajo. "
                "El contratista deberá realizar esta comprobación al elaborar el Plan de Seguridad "
                "y Salud (PSS), adaptando y desarrollando las previsiones del estudio según sus "
                "métodos de ejecución, medios y organización. El PSS concretará las medidas, "
                "responsables y controles necesarios y se someterá a la aprobación que proceda "
                "antes del inicio de los trabajos; deberá actualizarse cuando cambien las "
                "condiciones de ejecución.",
            ),
        ]
        insertion = next(
            (
                index + 1 for index, block in enumerate(blocks)
                if block.kind == "table" and block.headers == _GENERAL_RISK_HEADERS
            ),
            None,
        )
        if insertion is None:
            raise ValueError("La memoria general no contiene la tabla de grupos de riesgo prevista.")
        blocks[insertion:insertion] = catalogue_blocks

    blocks.insert(0, _Block("document", "DOCUMENTO I. MEMORIA" if full_study else "MEMORIA"))
    for block in blocks:
        values = [block.text, *block.headers, *(value for row in block.rows for value in row)]
        if any(_PLACEHOLDER_RE.search(value) for value in values):
            raise ValueError("La plantilla del estudio contiene variables sin sustituir.")

    return _StudyContent(
        title=title,
        project_title=project_title,
        chapter_label=chapter_label,
        currency_label=currency,
        blocks=blocks,
        warnings=tuple(dict.fromkeys(pending)),
    )


def _column_ratios(headers: tuple[str, ...]) -> tuple[float, ...]:
    """Define anchos de tabla legibles en PDF y ODT segun su numero de columnas."""
    if headers in (_GENERAL_RISK_HEADERS, _PPE_HEADERS):
        return (0.20, 0.36, 0.44)
    if headers == _WORK_UNIT_HEADERS:
        return (0.34, 0.33, 0.33)
    if headers == _CATALOGUE_HEADERS:
        return (0.18, 0.28, 0.54)
    if headers == _NTP_HEADERS:
        return (0.30, 0.70)
    if headers == _MEASUREMENT_HEADERS:
        return (52, 18, 188, 35, 40, 40, 35, 27, 45)
    if headers == _BUDGET_HEADERS:
        return (50, 20, 220, 55, 55, 60)
    if len(headers) == 2:
        return (1.2, 4.8)
    if len(headers) == 3:
        return (1.1, 1.8, 4.1)
    if len(headers) == 6:
        return (1.0, 0.6, 3.2, 0.8, 1.5, 1.2)
    if len(headers) == 7:
        return (3.0, 0.7, 0.9, 0.9, 0.9, 0.7, 1.0)
    return tuple(1.0 for _ in headers)


def _study_documents(blocks: list[_Block]) -> list[tuple[str, list[_Block]]]:
    """Separa los documentos manteniendo compatibles los estudios de un solo cuerpo."""
    documents: list[tuple[str, list[_Block]]] = []
    title = ""
    current: list[_Block] = []
    for block in blocks:
        if block.kind == "document":
            if current or title:
                documents.append((title, current))
            title, current = block.text, []
        else:
            current.append(block)
    if current or title or not documents:
        documents.append((title, current))
    return documents


def _numbered_blocks(blocks: list[_Block]) -> list[_Block]:
    """Numera cada documento de forma independiente con las convenciones del PPTP."""
    result = []
    for title, document_blocks in _study_documents(blocks):
        if title:
            result.append(_Block("document", title))
        source = "\n".join(
            f"{'#' * block.level} {block.text}"
            for block in document_blocks if block.kind == "heading"
        )
        titles = iter(
            line.split(" ", 1)[1] for line in number_headings(source).splitlines()
        )
        result.extend(
            replace(block, text=next(titles)) if block.kind == "heading" else block
            for block in document_blocks
        )
    return result


def _tables_with_labels(blocks: list[_Block]):
    """Conserva las referencias del estudio y rotula las tablas sin leyenda."""
    caption_pattern = re.compile(r"Tabla\s+(\d+(?:\.\d+)*)\.\s*(.*)")
    used = {
        match[1] for block in blocks if block.kind == "caption"
        if (match := caption_pattern.fullmatch(block.text))
    }
    next_number = 1
    for index, block in enumerate(blocks):
        if block.kind == "caption" and index + 1 < len(blocks) and blocks[index + 1].kind == "table":
            continue
        if block.kind == "table" and not block.economic_table:
            previous = blocks[index - 1] if index else None
            match = caption_pattern.fullmatch(previous.text) if previous and previous.kind == "caption" else None
            if match:
                number, title = match.groups()
            else:
                while str(next_number) in used:
                    next_number += 1
                number = str(next_number)
                next_number += 1
                title = previous.text if previous and previous.kind == "caption" else " / ".join(block.headers)
            yield block, (number, title)
        else:
            yield block, None


def _reference_target(reference: str) -> str:
    """Genera un destino ASCII estable para una fila del catalogo preventivo."""
    return f"ESSRef-{_normalized_text(reference).upper()}"


def _catalogue_targets(content: _StudyContent) -> dict[str, str]:
    """Indexa solo las referencias que disponen de una fila de destino."""
    return {
        _normalized_text(row[1]).upper(): _reference_target(row[1])
        for block in content.blocks
        if block.kind == "table" and block.headers == _CATALOGUE_HEADERS
        for row in block.rows
    }


def _pdf_reference_markup(text: str, targets: dict[str, str]) -> str:
    """Escapa el texto y convierte las referencias conocidas en enlaces internos."""
    def external(value: str) -> str:
        """Conserva las citas normativas y enlaces externos del fragmento."""
        return "".join(
            f'<link href="{escape(url, quote=True)}" color="#174B83"><u>{escape(label)}</u></link>'
            if url else escape(label)
            for label, url in normative_runs(value)
        )

    fragments = []
    offset = 0
    for match in _REFERENCE_RE.finditer(text):
        fragments.append(external(text[offset:match.start()]))
        label = escape(match.group())
        target = targets.get(_normalized_text(match.group()).upper())
        fragments.append(
            f'<link href="#{target}" color="#1F4E79"><u>{label}</u></link>'
            if target else label
        )
        offset = match.end()
    fragments.append(external(text[offset:]))
    return "".join(fragments).replace("\n", "<br/>")


def _pdf_blue_table(block: _Block, layout: ReportLayout):
    """Maqueta tablas ESS con cabecera azul, filas alternas y anchos propios."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table

    class SummaryTable(Table):
        """Conserva la alineacion tambien en las tablas internas creadas al partir una fila."""

        def __init__(self, *args, **kwargs):
            """Evita que ReportLab vuelva al centrado en sus fragmentos intermedios."""
            kwargs.setdefault("hAlign", "LEFT")
            super().__init__(*args, **kwargs)

        def wrap(self, availWidth, availHeight):
            """Reserva una sola continuacion del catalogo en cada pagina."""
            width, height = super().wrap(availWidth, availHeight)
            if self._catalogue_drawn_on_page():
                return width, max(height, availHeight + 1)
            return width, height

        def split(self, availWidth, availHeight):
            """Lleva el siguiente fragmento a otra pagina sin repetir la cabecera."""
            if self._catalogue_drawn_on_page():
                return []
            return super().split(availWidth, availHeight)

        def _catalogue_drawn_on_page(self):
            """Consulta el estado de dibujo, aislado por tabla y pasada del documento."""
            if not hasattr(self, "canv"):
                return False
            return (
                block.headers == _CATALOGUE_HEADERS
                and getattr(self.canv, "_ess_catalogue_pages", {}).get(id(block))
                == self.canv.getPageNumber()
            )

        def draw(self):
            """Registra solo los fragmentos realmente colocados en la pagina."""
            if block.headers == _CATALOGUE_HEADERS:
                pages = getattr(self.canv, "_ess_catalogue_pages", None)
                if pages is None:
                    pages = self.canv._ess_catalogue_pages = {}
                pages[id(block)] = self.canv.getPageNumber()
            super().draw()

        def _splitCell(self, value, style, oldHeight, newHeight, width):
            """Conserva la categoria en las continuaciones de una definicion larga."""
            if (
                isinstance(value, (tuple, list)) and len(value) == 1
                and isinstance(value[0], Paragraph)
                and value[0].style.name == "ESSCatalogueType"
            ):
                if value[0].wrap(width - style.leftPadding - style.rightPadding, oldHeight)[1] > (
                    newHeight - style.topPadding - style.bottomPadding
                ):
                    return []
                return (value, value)
            return super()._splitCell(value, style, oldHeight, newHeight, width)

        def _drawCell(self, cellval, cellstyle, pos, size):
            """Imprime cada tipo solo en su primera fila visible de cada pagina."""
            if isinstance(cellval, (tuple, list)) and cellval:
                paragraph = cellval[0]
                if isinstance(paragraph, Paragraph) and paragraph.style.name == "ESSCatalogueType":
                    seen = getattr(self.canv, "_ess_catalogue_types", None)
                    if seen is None:
                        seen = self.canv._ess_catalogue_types = set()
                    key = (id(block), self.canv.getPageNumber(), paragraph.getPlainText())
                    if key in seen:
                        return
                    seen.add(key)
            super()._drawCell(cellval, cellstyle, pos, size)

    text = ParagraphStyle(
        "ESSSummaryText", parent=layout.normal, fontSize=9.5, leading=12,
    )
    header = ParagraphStyle(
        "ESSSummaryHeader", parent=text, fontName=layout.bold_font, textColor=colors.white,
    )
    category_style = ParagraphStyle("ESSCatalogueType", parent=text)
    cell_list_style = pdf_list_style("ESSCellList", text)
    project_text = ParagraphStyle("ESSProjectSummaryText", parent=text, alignment=TA_JUSTIFY)
    project_list = pdf_list_style("ESSProjectSummaryList", project_text, justified=True)

    def cell_text(value, style, *, project_description=False):
        """Da sangria tambien a las listas incluidas en las celdas del resumen."""
        lines = value.splitlines()
        if not any(_LIST_ITEM_RE.match(line.strip()) for line in lines):
            return layout.text(value, style)
        paragraphs = []
        for line in lines:
            item = _LIST_ITEM_RE.match(line.strip())
            paragraph = layout.text(
                item.group(2) if item else line or "\N{NO-BREAK SPACE}",
                (project_list if project_description else cell_list_style) if item else style,
            )
            if item:
                paragraph.bulletText = item.group(1)
            paragraphs.append(paragraph)
        return paragraphs

    rows = [
        [layout.text(value, header) for value in block.headers],
        *[
            [
                layout.paragraph(
                    f'<a name="{_reference_target(value)}"/>{escape(value)}', text,
                ) if block.headers == _CATALOGUE_HEADERS and column == 1 else (
                    cell_text(
                        value, category_style if block.headers == _CATALOGUE_HEADERS and column == 0
                        else header if index in block.section_rows else
                        project_text if block.project_summary and column == 1 else text,
                        project_description=block.project_summary and column == 1,
                    )
                )
                for column, value in enumerate(row)
            ]
            for index, row in enumerate(block.rows)
        ],
    ]
    ratios = (0.3, 0.7) if block.project_summary else _column_ratios(block.headers)
    return SummaryTable(
        rows, colWidths=[layout.content_width * ratio for ratio in ratios],
        repeatRows=1, splitInRow=1, hAlign="LEFT", style=[
            *pdf_blue_table_style(),
            *[
                command for index in block.section_rows for command in (
                    ("SPAN", (0, index + 1), (-1, index + 1)),
                    ("BACKGROUND", (0, index + 1), (-1, index + 1), colors.HexColor(TABLE_HEADER_BACKGROUND)),
                )
            ],
        ],
    )


def _add_odt_blue_table_styles(document) -> None:
    """Registra una sola vez los estilos compartidos por las tablas azules."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    for name, bold in (("ESSSummaryHeader", True), ("ESSSummaryText", False)):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ESSBody",
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), {
            _tag("fo:text-align"): "start",
            _tag("fo:margin-left"): "0pt", _tag("fo:margin-right"): "0pt",
            _tag("fo:text-indent"): "0pt",
            _tag("fo:margin-top"): "0pt", _tag("fo:margin-bottom"): "0pt",
            _tag("fo:keep-with-next"): "auto", _tag("fo:line-height"): "115%",
        })
        ET.SubElement(style, _tag("style:text-properties"), {
            _tag("fo:font-weight"): "bold" if bold else "normal",
            _tag("fo:color"): "#FFFFFF" if bold else "#000000",
            _tag("fo:font-size"): "9.5pt",
        })
    add_odt_blue_table_cells(document, "ESSSummary")
    add_odt_list_style(document, "ESSCellList", "ESSSummaryText")
    style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "ESSProjectSummaryText", _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "ESSSummaryText",
    })
    ET.SubElement(style, _tag("style:paragraph-properties"), {
        _tag("fo:text-align"): "justify",
    })
    add_odt_list_style(document, "ESSProjectSummaryList", "ESSSummaryText", justified=True)


def _write_odt_blue_table(document, block: _Block) -> None:
    """Aplica a las tablas ODT los mismos colores y anchos que al PDF."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    ratios = (0.3, 0.7) if block.project_summary else _column_ratios(block.headers)
    document.table(
        block.headers, block.rows, ratios=ratios, body_text_style="ESSSummaryText",
    )
    table = document.body[-1]
    if block.project_summary:
        table.set(_tag("table:name"), "ESSProjectSummary")
    elif block.headers == _GENERAL_RISK_HEADERS:
        table.set(_tag("table:name"), "ESSGeneralRisks")
    elif block.headers == _CATALOGUE_HEADERS:
        table.set(_tag("table:name"), "ESSPreventiveCatalogue")
    elif block.headers == _PPE_HEADERS:
        table.set(_tag("table:name"), "ESSPPE")
    elif block.headers == _WORK_UNIT_HEADERS:
        table.set(_tag("table:name"), "ESSWorkUnits")
    elif block.headers == _NTP_HEADERS:
        table.set(_tag("table:name"), "ESSChapterNTPs")
    for cell in table.find(_tag("table:table-header-rows")).iter(_tag("table:table-cell")):
        cell.set(_tag("table:style-name"), "ESSSummaryHeaderCell")
        cell.find(_tag("text:p")).set(_tag("text:style-name"), "ESSSummaryHeader")
    for index, row in enumerate(table.findall(_tag("table:table-row"))):
        if index in block.section_rows:
            cells = row.findall(_tag("table:table-cell"))
            cells[0].set(_tag("table:number-columns-spanned"), "2")
            cells[0].set(_tag("table:style-name"), "ESSSummaryHeaderCell")
            cells[0].find(_tag("text:p")).set(_tag("text:style-name"), "ESSSummaryHeader")
            row.remove(cells[1])
            ET.SubElement(row, _tag("table:covered-table-cell"))
            continue
        for column, cell in enumerate(row.findall(_tag("table:table-cell"))):
            cell.set(
                _tag("table:style-name"),
                "ESSSummaryBlueCell" if index % 2 == 0 else "ESSSummaryWhiteCell",
            )
            project_description = block.project_summary and column == 1
            text_style = "ESSProjectSummaryText" if project_description else "ESSSummaryText"
            list_style = "ESSProjectSummaryList" if project_description else "ESSCellList"
            lines = block.rows[index][column].splitlines()
            if any(_LIST_ITEM_RE.match(line.strip()) for line in lines):
                for child in list(cell):
                    cell.remove(child)
                for line in lines:
                    item = _LIST_ITEM_RE.match(line.strip())
                    paragraph = ET.SubElement(cell, _tag("text:p"), {
                        _tag("text:style-name"): list_style if item else text_style,
                    })
                    paragraph.text = item.group(1) if item else line
                    if item:
                        ET.SubElement(paragraph, _tag("text:tab")).tail = item.group(2)
            elif project_description:
                for paragraph in cell.findall(_tag("text:p")):
                    paragraph.set(_tag("text:style-name"), text_style)
    if block.headers == _CATALOGUE_HEADERS:
        rows = table.findall(_tag("table:table-row"))
        start = 0
        while start < len(rows):
            end = start + 1
            while end < len(rows) and block.rows[end][0] == block.rows[start][0]:
                end += 1
            if end - start > 1:
                rows[start][0].set(_tag("table:number-rows-spanned"), str(end - start))
                for row in rows[start + 1:end]:
                    row.remove(row[0])
                    row.insert(0, ET.Element(_tag("table:covered-table-cell")))
            start = end


def _append_odt_reference_text(parent, text: str, targets: dict[str, str]) -> None:
    """Anade texto y enlaces ODF sin perder los espacios entre fragmentos."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    offset = 0
    for match in _REFERENCE_RE.finditer(text):
        target = targets.get(_normalized_text(match.group()).upper())
        if target is None:
            continue
        append_odt_links(parent, text[offset:match.start()])
        ET.SubElement(parent, _tag("text:a"), {
            _tag("xlink:type"): "simple", _tag("xlink:href"): f"#{target}",
            _tag("text:style-name"): "ESSReferenceLink",
        }).text = match.group()
        offset = match.end()
    append_odt_links(parent, text[offset:])


def _link_odt_references(document, targets: dict[str, str]) -> None:
    """Vincula referencias en todo el cuerpo y situa cada destino en su propia fila."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    anchors = {}
    for table in document.body.iter(_tag("table:table")):
        if table.get(_tag("table:name")) != "ESSPreventiveCatalogue":
            continue
        for row in table.findall(_tag("table:table-row")):
            cells = [
                cell for cell in row
                if cell.tag in (_tag("table:table-cell"), _tag("table:covered-table-cell"))
            ]
            paragraph = cells[1].find(_tag("text:p"))
            anchors[paragraph] = _reference_target("".join(paragraph.itertext()))

    def link_element(element) -> None:
        """Conserva estilos, campos y saltos de linea evitando enlaces anidados."""
        if element.tag == _tag("text:a") or element in anchors:
            return
        children = list(element)
        value = element.text or ""
        element.text = None
        for child in children:
            element.remove(child)
        _append_odt_reference_text(element, value, targets)
        for child in children:
            tail = child.tail or ""
            child.tail = None
            link_element(child)
            element.append(child)
            _append_odt_reference_text(element, tail, targets)

    link_element(document.body)
    for paragraph, target in anchors.items():
        value = paragraph.text
        paragraph.text = None
        bookmark = ET.Element(_tag("text:bookmark"), {_tag("text:name"): target})
        bookmark.tail = value
        paragraph.insert(0, bookmark)


def _validate_drawing_title_groups(
    drawings: list[bytes], title_groups: list[list[str]],
) -> None:
    """Comprueba que cada página de planos tenga sus entradas de índice."""
    if (
        len(drawings) != len(title_groups)
        or any(
            not group or any(not isinstance(title, str) or not title.strip() for title in group)
            for group in title_groups
        )
    ):
        raise ValueError("El índice de planos no coincide con las páginas seleccionadas.")


def _append_odt_plans_index(document, title_groups: list[list[str]], bookmark_offset: int) -> int:
    """Inserta el índice del Documento II con enlaces a las páginas de planos."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    plan_title_style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "ESSPlansTitle", _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "ESSHeading1",
    })
    ET.SubElement(plan_title_style, _tag("style:paragraph-properties"), {
        _tag("fo:break-before"): "page",
    })
    for name, break_before in (
        ("ESSPlanAnchorStart", True),
        ("ESSPlanAnchorNext", True),
        ("ESSPlanAnchor", False),
    ):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ESSBody",
            _tag("style:master-page-name"): "ESSDrawingPage",
        })
        properties = ET.SubElement(style, _tag("style:paragraph-properties"), {
            _tag("fo:margin"): "0cm", _tag("fo:line-height"): "1pt",
        })
        if break_before:
            properties.set(_tag("fo:break-before"), "page")
        ET.SubElement(style, _tag("style:text-properties"), {
            _tag("fo:font-size"): "1pt", _tag("fo:color"): "#FFFFFF",
        })

    document.paragraph(_PLANS_DOCUMENT_TITLE, "ESSPlansTitle")
    headings = [
        (1, f"PLANO Nº {index:02}. {title}")
        for index, title in enumerate(
            (title for group in title_groups for title in group), 1,
        )
    ]
    document.table_of_contents(
        headings, max_level=1, name="ESSPlansContents",
        bookmark_offset=bookmark_offset,
        source_styles={
            1: ("ESSPlanAnchorStart", "ESSPlanAnchorNext", "ESSPlanAnchor"),
        },
    )
    return len(headings)


def _write_pdf(
    app, target, content: _StudyContent, drawings: list[bytes],
    title_groups: list[list[str]], paper: str,
) -> None:
    """Maqueta el estudio con indice enlazado y paginas reales, como el PPTP."""
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from io import BytesIO
    from reportlab.lib.pagesizes import A3, A4, landscape
    from reportlab.platypus import (
        BaseDocTemplate, Frame, Image, NextPageTemplate, PageBreak, PageTemplate, Spacer,
    )
    from reportlab.platypus.doctemplate import LayoutError
    from reportlab.platypus.tableofcontents import TableOfContents

    _validate_drawing_title_groups(drawings, title_groups)
    settings = app.report_settings
    project = report_project_label(content.project_title, content.currency_label)
    targets = _catalogue_targets(content)

    class StudyLayout(ReportLayout):
        """Incorpora enlaces preventivos tambien en las tablas del maquetador comun."""

        def text(self, value, style=None, *, role=""):
            """Crea parrafos seguros con referencias navegables al catalogo."""
            return self.paragraph(_pdf_reference_markup(str(value), targets), style, role=role)

    layout = StudyLayout(
        text_styles=settings.text_styles,
        template_path=settings.template_path,
        logo_path=settings.master_logo_path,
        expediente=settings.expediente,
        template_title=content.project_title,
        repeat_titles=False,
        footer_title=content.title,
    )
    footer_source = (
        "".join(layout.template_footer_section.itertext())
        if layout.template_footer_section is not None else ""
    )
    if not re.search(r"\{\{cap[ií]tulo\}\}", footer_source) and content.title not in footer_source:
        layout.template_footer = "\n".join(
            value for value in (layout.template_footer, content.title) if value
        )
    body = ParagraphStyle(
        "ESSBody", parent=layout.normal, fontSize=10,
        leading=13, spaceAfter=6, alignment=TA_JUSTIFY,
    )
    list_styles = {
        depth: pdf_list_style(f"ESSList{depth}", body, depth)
        for depth in {block.level for block in content.blocks if block.kind == "list_item"}
    }
    heading_styles = {
        level: ParagraphStyle(
            f"ESSHeading{level}", parent=body, fontName=layout.bold_font,
            fontSize=max(10, 15 - level), leading=max(13, 18 - level),
            spaceBefore=10, keepWithNext=True, alignment=TA_LEFT,
        )
        for level in range(1, 5)
    }
    from tools._shared.pdf_contents import contents_styles, contents_title

    class StudyContents(TableOfContents):
        """Recibe solo las entradas del documento al que pertenece cada indice."""

        document_index = 0

        def notify(self, kind, stuff):
            """Filtra las notificaciones sin alterar las pasadas de paginacion."""
            if kind == f"ESSContents{self.document_index}":
                super().notify("TOCEntry", stuff)

    plan_size = A4 if paper == "A4" else landscape(A3)
    story = []
    documents = _study_documents(content.blocks)
    plan_insert_at = min(1, len(documents))

    def append_plans_section(following_document: bool) -> None:
        """Inserta el Documento II y sus páginas entre la memoria y el pliego."""
        plan_contents = StudyContents()
        plan_contents.document_index = plan_insert_at
        plan_contents.levelStyles = contents_styles(layout, DEFAULT_TOC_DEPTH)
        plan_contents.dotsMinLevel = 0
        story.extend((
            PageBreak(),
            layout.text(_PLANS_DOCUMENT_TITLE, layout.title_style),
            contents_title(layout),
            plan_contents,
            NextPageTemplate("ESSDrawings"),
            PageBreak(),
        ))
        plan_number = 0
        for group_index, (drawing, titles) in enumerate(zip(drawings, title_groups)):
            if group_index:
                story.append(PageBreak())
            for title in titles:
                plan_number += 1
                entry = f"PLANO Nº {plan_number:02}. {title}"
                anchor = Spacer(0, 0.1)
                anchor._ess_heading = (
                    0, entry, f"ESSPlanHeading{plan_number}", plan_insert_at,
                )
                story.append(anchor)
            story.append(Image(
                BytesIO(drawing),
                width=plan_size[0] - 0.1,
                height=plan_size[1] - len(titles) * 0.1 - 0.1,
            ))
        story.append(NextPageTemplate("Later"))
        if not following_document:
            story.append(PageBreak())

    heading_index = 0
    plans_inserted = False
    caption_style = ParagraphStyle(
        "ESSCaption", parent=body, fontSize=9, leading=12,
        keepWithNext=True, alignment=TA_LEFT,
    )
    for document_index, (document_title, blocks) in enumerate(documents):
        if drawings and document_index == plan_insert_at:
            append_plans_section(following_document=True)
            plans_inserted = True
        if document_index:
            story.append(PageBreak())
        toc_document_index = (
            document_index + 1
            if drawings and document_index >= plan_insert_at else document_index
        )
        if document_title:
            story.append(layout.text(document_title, layout.title_style))
        toc = StudyContents()
        toc.document_index = toc_document_index
        toc.levelStyles = contents_styles(layout, DEFAULT_TOC_DEPTH)
        toc.dotsMinLevel = 0
        story.extend((contents_title(layout), toc, PageBreak()))
        for block, label in _tables_with_labels(blocks):
            if block.kind == "heading":
                paragraph = layout.text(block.text, heading_styles[block.level])
                paragraph._ess_heading = (
                    block.level - 1, block.text, f"ESSHeading{heading_index}", toc_document_index,
                )
                heading_index += 1
                story.append(paragraph)
            elif block.kind == "paragraph":
                story.append(layout.text(block.text, body))
            elif block.kind == "list_item":
                marker, value = block.text.split(maxsplit=1)
                story.append(layout.paragraph(
                    _pdf_reference_markup(value, targets), list_styles[block.level],
                    bulletText=marker,
                ))
            elif block.kind == "caption":
                story.append(layout.text(block.text, caption_style))
            elif block.kind == "table":
                if label is not None:
                    number, title = label
                    layout.table_caption(
                        story, title, number=number, body=body, caption=caption_style, include_reference=False,
                    )
                if block.project_summary or block.blue_table:
                    story.append(_pdf_blue_table(block, layout))
                else:
                    ratios = _column_ratios(block.headers)
                    story.append(layout.table(
                        block.headers, block.rows, ratios, bold_rows=block.bold_rows,
                        highlighted_rows=block.highlighted_rows,
                    ))

    if drawings and not plans_inserted:
        append_plans_section(following_document=False)
    story.extend(layout.signature_block(settings))

    class StudyDocTemplate(BaseDocTemplate):
        """Registra los destinos y las paginas del indice en cada pasada."""

        def afterFlowable(self, flowable) -> None:
            """Vincula cada epigrafe con su pagina definitiva tras la maquetacion."""
            heading = getattr(flowable, "_ess_heading", None)
            if heading is not None:
                level, text, key, document_index = heading
                self.canv.bookmarkPage(key)
                if level < DEFAULT_TOC_DEPTH:
                    self.notify(
                        f"ESSContents{document_index}", (level, layout.index_entry(escape(text)), self.page, key),
                    )

    document = StudyDocTemplate(
        str(target), pagesize=layout.page_size, leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN, topMargin=layout.top_margin(content.title, project),
        bottomMargin=layout.bottom_margin(), title=content.title,
        author=settings.document_author,
    )
    has_page_field = bool(re.search(r"\{no_p[aá]gina\}", footer_source))

    def header(canvas, doc) -> None:
        """Conserva la plantilla y numera tambien los pies sin marcador de pagina."""
        layout.header(canvas, doc, content.title, project)
        if not has_page_field:
            canvas.saveState()
            canvas.setFont(layout.regular_font, 6)
            canvas.drawRightString(
                layout.page_width - RIGHT_MARGIN, PAGE_MARGIN + 1, str(doc.page),
            )
            canvas.restoreState()

    frame = Frame(
        document.leftMargin, document.bottomMargin, document.width, document.height,
        id="ESSBody",
    )
    plan_frame = Frame(
        0, 0, *plan_size, id="ESSDrawings", leftPadding=0, rightPadding=0,
        topPadding=0, bottomPadding=0,
    )
    document.addPageTemplates([
        PageTemplate(id="First", frames=frame, onPage=header, autoNextPageTemplate="Later"),
        PageTemplate(id="Later", frames=frame, onPage=header),
        PageTemplate(id="ESSDrawings", frames=plan_frame, pagesize=plan_size),
    ])
    try:
        document.multiBuild(story)
    except LayoutError as exc:
        raise ValueError(f"No se pudo maquetar el Estudio de Seguridad y Salud: {exc}") from exc


def _write_odt(
    app, target, content: _StudyContent, drawings: list[bytes],
    title_groups: list[list[str]], paper: str,
) -> None:
    """Escribe el mismo estudio como documento ODT editable basado en la plantilla."""
    from xml.etree import ElementTree as ET
    from budget_core import currency_label
    from odt_reports import _new_report, _tag

    _validate_drawing_title_groups(drawings, title_groups)
    settings = app.report_settings
    project = report_project_label(
        content.project_title,
        currency_label(settings.currency, settings.custom_currency),
    )
    document = _new_report(
        content.title, content.project_title, settings,
        repeat_titles=False,
        footer_title=content.title,
        template_title=content.project_title,
    )
    document.project_title = project
    if document.template_footer is None and not document.footer:
        document.footer = content.title
    elif document.template_footer is not None:
        from report_layout import replace_footer_fields

        footer = replace_footer_fields(document.template_footer, content.title)
        if content.title not in "".join(footer.itertext()):
            ET.SubElement(footer, _tag("text:p"), {
                _tag("text:style-name"): "FooterText",
            }).text = content.title
        if footer.find(f".//{_tag('text:page-number')}") is None:
            paragraph = ET.SubElement(footer, _tag("text:p"), {
                _tag("text:style-name"): "ESSPageNumber",
            })
            ET.SubElement(paragraph, _tag("text:page-number"), {
                _tag("text:select-page"): "current",
            }).text = "1"
            style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
                _tag("style:name"): "ESSPageNumber", _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): "FooterText",
            })
            ET.SubElement(style, _tag("style:paragraph-properties"), {
                _tag("fo:text-align"): "end",
            })
        document.template_footer = footer
    add_odt_narrative_styles(document, "ESS", heading_levels=4)
    style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "ESSCaption", _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "ESSBody",
    })
    ET.SubElement(style, _tag("style:paragraph-properties"), {
        _tag("fo:keep-with-next"): "always",
    })
    documents = _study_documents(content.blocks)
    scoped_contents = any(title for title, _blocks in documents)
    document_styles = {}
    if scoped_contents:
        for index, (_title, _blocks) in enumerate(documents):
            document_number = {
                _PLANS_DOCUMENT_TITLE: 2,
                _PLIEGO_DOCUMENT_TITLE: 3,
                _MEASUREMENTS_DOCUMENT_TITLE: 4,
                _BUDGET_DOCUMENT_TITLE: 5,
            }.get(_title, index + 1)
            for level in range(1, 5):
                name = f"ESSDocument{index}Heading{level}"
                ET.SubElement(document.automatic_styles, _tag("style:style"), {
                    _tag("style:name"): name, _tag("style:family"): "paragraph",
                    _tag("style:parent-style-name"): f"ESSHeading{level}",
                })
                document_styles[name] = f"ESS - Documento {document_number} - Titulo {level}"
        for name in ("ESSDocumentTitle", "ESSDocumentNextTitle"):
            properties = {
                _tag("style:name"): name, _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): "ESSHeading1",
            }
            if drawings and name == "ESSDocumentNextTitle":
                properties[_tag("style:master-page-name")] = "Standard"
            style = ET.SubElement(
                document.automatic_styles, _tag("style:style"), properties,
            )
            ET.SubElement(style, _tag("style:paragraph-properties"), {
                _tag("fo:break-before"): "page" if name == "ESSDocumentNextTitle" else "auto",
            })
    for depth in {block.level for block in content.blocks if block.kind == "list_item"}:
        add_odt_list_style(document, f"ESSList{depth}", "ESSBody", depth)
    _add_odt_blue_table_styles(document)
    reference_style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "ESSReferenceLink", _tag("style:family"): "text",
    })
    ET.SubElement(reference_style, _tag("style:text-properties"), {
        _tag("fo:color"): "#1F4E79", _tag("style:text-underline-style"): "solid",
    })
    bookmark_index = 0
    plan_insert_at = min(1, len(documents))
    plan_bookmark_offset = 0
    plans_inserted = False
    for index, (document_title, blocks) in enumerate(documents):
        if drawings and index == plan_insert_at:
            plan_bookmark_offset = bookmark_index
            bookmark_index += _append_odt_plans_index(
                document, title_groups, plan_bookmark_offset,
            )
            plans_inserted = True
        if document_title:
            document.paragraph(
                document_title,
                style="ESSDocumentNextTitle" if index else "ESSDocumentTitle",
            )
        headings = [(block.level, block.text) for block in blocks if block.kind == "heading"]
        document.table_of_contents(
            headings, max_level=DEFAULT_TOC_DEPTH, bookmark_offset=bookmark_index,
            name=f"ESSDocument{index}Contents" if scoped_contents else "ESSContents",
            source_styles={
                level: f"ESSDocument{index}Heading{level}"
                for level in range(1, DEFAULT_TOC_DEPTH + 1)
            } if scoped_contents else {
                level: f"ESSHeading{level}" for level in range(1, DEFAULT_TOC_DEPTH + 1)
            },
        )
        for block, label in _tables_with_labels(blocks):
            if block.kind == "heading":
                style = (
                    f"ESSDocument{index}Heading{block.level}"
                    if scoped_contents else f"ESSHeading{block.level}"
                )
                document.heading(
                    block.text, level=block.level, style=style,
                    bookmark=f"PPTPHeading{bookmark_index}",
                )
                bookmark_index += 1
            elif block.kind == "paragraph":
                document.paragraph(block.text, style="ESSBody")
            elif block.kind == "list_item":
                marker, value = block.text.split(maxsplit=1)
                paragraph = ET.SubElement(document.body, _tag("text:p"), {
                    _tag("text:style-name"): f"ESSList{block.level}",
                })
                paragraph.text = marker
                ET.SubElement(paragraph, _tag("text:tab")).tail = value
            elif block.kind == "caption":
                document.paragraph(block.text, style="ESSCaption")
            elif block.kind == "table":
                if label is not None:
                    number, title = label
                    document.table_caption(
                        title, number=number, body="ESSBody", caption="ESSCaption", include_reference=False,
                    )
                if block.project_summary or block.blue_table:
                    _write_odt_blue_table(document, block)
                else:
                    document.table(
                        block.headers, block.rows, ratios=_column_ratios(block.headers),
                        bold_rows=block.bold_rows, highlighted_rows=block.highlighted_rows,
                    )
    if drawings and not plans_inserted:
        plan_bookmark_offset = bookmark_index
        bookmark_index += _append_odt_plans_index(
            document, title_groups, plan_bookmark_offset,
        )
    _link_odt_references(document, _catalogue_targets(content))
    document.append_signature(settings)
    document.name_text_styles({
        "ESSBody": "ESS - Cuerpo",
        "ESSCaption": "ESS - Leyenda de tabla",
        "ESSSummaryHeader": "ESS - Tablas - Cabecera",
        "ESSSummaryText": "ESS - Tablas - Texto",
        **document_styles,
        "ESSPlansTitle": "ESS - Documento 2 - Título",
        "ESSPlanAnchorStart": "ESS - Plano - Ancla inicial",
        "ESSPlanAnchorNext": "ESS - Plano - Ancla siguiente",
        "ESSPlanAnchor": "ESS - Plano - Ancla",
        **{f"ESSHeading{level}": f"ESS - Titulo {level}" for level in range(1, 5)},
        **{f"PPTPContents{level}": f"ESS - Indice {level}" for level in range(1, 3)},
    })
    document.write(target)
    if drawings:
        _append_odt_drawings(
            target, drawings, title_groups, paper, plan_bookmark_offset,
            following_document=plan_insert_at < len(documents),
        )


def _append_odt_drawings(
    target: Path, drawings: list[bytes], title_groups: list[list[str]],
    paper: str, bookmark_offset: int, *, following_document: bool,
) -> None:
    """Añade páginas de plano con maestro propio e imágenes integradas en el ODT."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    _validate_drawing_title_groups(drawings, title_groups)
    width, height = ("21cm", "29.7cm") if paper == "A4" else ("42cm", "29.7cm")
    temporary = None
    try:
        with ZipFile(target) as source:
            content = ET.fromstring(source.read("content.xml"))
            styles = ET.fromstring(source.read("styles.xml"))
            manifest = ET.fromstring(source.read("META-INF/manifest.xml"))
            automatic = content.find(_tag("office:automatic-styles"))
            body = content.find(f"{_tag('office:body')}/{_tag('office:text')}")
            graphic = ET.SubElement(automatic, _tag("style:style"), {
                _tag("style:name"): "ESSDrawingGraphic", _tag("style:family"): "graphic",
            })
            ET.SubElement(graphic, _tag("style:graphic-properties"), {
                _tag("style:horizontal-pos"): "from-left",
                _tag("style:horizontal-rel"): "page",
                _tag("style:vertical-pos"): "from-top",
                _tag("style:vertical-rel"): "page",
                _tag("style:wrap"): "run-through",
            })
            layouts = styles.find(_tag("office:automatic-styles"))
            page_layout = ET.SubElement(layouts, _tag("style:page-layout"), {
                _tag("style:name"): "ESSDrawingLayout",
            })
            ET.SubElement(page_layout, _tag("style:page-layout-properties"), {
                _tag("fo:page-width"): width, _tag("fo:page-height"): height,
                _tag("fo:margin"): "0cm",
                _tag("style:print-orientation"): "portrait" if paper == "A4" else "landscape",
            })
            masters = styles.find(_tag("office:master-styles"))
            ET.SubElement(masters, _tag("style:master-page"), {
                _tag("style:name"): "ESSDrawingPage",
                _tag("style:page-layout-name"): "ESSDrawingLayout",
            })
            insertion_index = next(
                index + 1 for index, node in enumerate(body)
                if node.tag == _tag("text:table-of-content")
                and node.get(_tag("text:name")) == "ESSPlansContents"
            )
            needs_standard_page = not following_document and insertion_index < len(body)
            image_names = []
            existing_names = set(source.namelist())
            title_index = 0
            for index, (_image, titles) in enumerate(zip(drawings, title_groups), 1):
                name = f"Pictures/ESSDrawing{index}.png"
                if name in existing_names:
                    raise ValueError(f"El ODT ya contiene el recurso de plano {name}.")
                image_names.append(name)
                for anchor_index, title in enumerate(titles):
                    style_name = (
                        "ESSPlanAnchorStart" if index == 1 and anchor_index == 0 else
                        "ESSPlanAnchorNext" if anchor_index == 0 else
                        "ESSPlanAnchor"
                    )
                    anchor = ET.Element(_tag("text:p"), {
                        _tag("text:style-name"): (
                            style_name
                        ),
                    })
                    anchor.text = f"PLANO Nº {title_index + 1:02}. {title}"
                    ET.SubElement(anchor, _tag("text:bookmark"), {
                        _tag("text:name"): f"PPTPHeading{bookmark_offset + title_index}",
                    })
                    title_index += 1
                    if anchor_index == len(titles) - 1:
                        frame = ET.SubElement(anchor, _tag("draw:frame"), {
                            _tag("draw:name"): f"ESSDrawing{index}",
                            _tag("draw:style-name"): "ESSDrawingGraphic",
                            _tag("text:anchor-type"): "paragraph",
                            _tag("svg:x"): "0cm", _tag("svg:y"): "0cm",
                            _tag("svg:width"): width, _tag("svg:height"): height,
                        })
                        ET.SubElement(frame, _tag("draw:image"), {
                            _tag("xlink:href"): name, _tag("xlink:type"): "simple",
                            _tag("xlink:show"): "embed", _tag("xlink:actuate"): "onLoad",
                        })
                    body.insert(insertion_index, anchor)
                    insertion_index += 1
                ET.SubElement(manifest, _tag("manifest:file-entry"), {
                    _tag("manifest:full-path"): name, _tag("manifest:media-type"): "image/png",
                })
            if needs_standard_page:
                style = ET.SubElement(automatic, _tag("style:style"), {
                    _tag("style:name"): "ESSAfterDrawings", _tag("style:family"): "paragraph",
                    _tag("style:parent-style-name"): "ESSBody",
                    _tag("style:master-page-name"): "Standard",
                })
                ET.SubElement(style, _tag("style:paragraph-properties"), {
                    _tag("fo:break-before"): "page",
                })
                body.insert(insertion_index, ET.Element(_tag("text:p"), {
                    _tag("text:style-name"): "ESSAfterDrawings",
                }))
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".odt", delete=False) as handle:
                temporary = Path(handle.name)
            replacements = {
                "content.xml": ET.tostring(content, encoding="utf-8", xml_declaration=True),
                "styles.xml": ET.tostring(styles, encoding="utf-8", xml_declaration=True),
                "META-INF/manifest.xml": ET.tostring(manifest, encoding="utf-8", xml_declaration=True),
            }
            with ZipFile(temporary, "w") as output:
                for info in source.infolist():
                    output.writestr(info, replacements.get(info.filename, source.read(info.filename)))
                for name, image in zip(image_names, drawings):
                    output.writestr(name, image)
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_study_report(app, target) -> tuple[str, ...]:
    """Genera el ESS como PDF u ODT y devuelve los avisos, que no se imprimen."""
    target = Path(target)
    content = _build_study_content(app)
    output_format = report_output_format(
        target, app.report_settings.output_format,
    )
    drawing_state = restore_drawings(app.safety_health_state.get("drawings"))
    full_study = any(
        block.kind == "document" and block.text == _PLIEGO_DOCUMENT_TITLE
        for block in content.blocks
    )
    include_drawings = bool(drawing_state) and (
        full_study or drawing_state.get("include_basic", False)
    )
    selected = selected_sheets(drawing_state) if include_drawings else []
    paper = drawing_state["paper"] if drawing_state else "A4"
    title_groups = [
        [sheet.title for sheet in group]
        for group in group_sheets_by_page(selected, paper)
    ]
    drawings = render_drawings(app, drawing_state) if selected else []
    if full_study and not drawings:
        index = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "document" and block.text == _PLIEGO_DOCUMENT_TITLE
        )
        content.blocks[index:index] = [
            _Block("document", _PLANS_DOCUMENT_TITLE),
            _Block("heading", "Planos pendientes de incorporación", 1),
            _Block("paragraph", _IMPLEMENTATION_WARNING),
        ]
    content.blocks = _numbered_blocks(content.blocks)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=target.suffix, delete=False,
        ) as handle:
            temporary = Path(handle.name)
        if output_format == "pdf":
            _write_pdf(app, temporary, content, drawings, title_groups, paper)
        else:
            _write_odt(app, temporary, content, drawings, title_groups, paper)
        temporary.replace(target)
    except (OSError, BadZipFile) as exc:
        raise ValueError(f"No se pudo guardar el Estudio de Seguridad y Salud: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return content.warnings
