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

from budget_reports import budget_totals
from cpv_catalog import resolve_cpv_codes
from .care_centers import format_centers, restore_centers
from .external_conditions import APPLICABILITY, EXTERNAL_CONDITIONS, restore_external_conditions
from localization import translate
from municipalities import resolve_municipalities
from report_layout import LEFT_MARGIN, PAGE_MARGIN, RIGHT_MARGIN, ReportLayout, report_project_label
from tools._shared.document_layout import (
    DEFAULT_TOC_DEPTH, TABLE_ALTERNATE_BACKGROUND, TABLE_HEADER_BACKGROUND,
    number_headings, template_paragraph_styles,
)
from tools.safety_health import (
    LIBRARY_PATH, SAFETY_BUDGET_SOURCE, _study_criteria_values, evaluate_study_criteria,
)

if TYPE_CHECKING:
    from bc3_gui import BudgetApp, ProjectItem


_TEMPLATE_MEMBERS = {
    "project_data": "01_datos_proyecto.md",
    "memory": "02_memoria_general.md",
    "work_units": "03_fichas_unidades_obra.md",
    "catalogues": "04_catalogos_preventivos.md",
    "emergencies": "05_gestion_emergencias.md",
    "specification": "06_pliego_condiciones.md",
    "risk_matrix": "08_matriz_riesgos_por_unidad.md",
    "budget_linking": "09_enlace_automatico_presupuesto.md",
    "index": "UNI/INDICE_FICHAS_ESS.md",
    "general_conditions": "UNI/CONDICIONES_GENERALES_ESS.md",
}
_REFERENCE_RE = re.compile(r"\b(?:R|MP|PC|EPI|EQ)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
_UNIT_RE = re.compile(r"\bUO-[A-Z0-9]+(?:-[A-Z0-9]+)*\b")
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
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
    "EPI": "Equipos de proteccion individual",
    "EQ": "Equipos y maquinaria",
}


@dataclass(frozen=True)
class _Block:
    kind: str
    text: str = ""
    level: int = 0
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    project_summary: bool = False
    section_rows: tuple[int, ...] = ()


@dataclass
class _StudyContent:
    title: str
    project_title: str
    chapter_label: str
    currency_label: str
    blocks: list[_Block] = field(default_factory=list)


def _normalized_text(value: str) -> str:
    """Normaliza tildes y mayusculas para aplicar las reglas de clasificacion."""
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()


def _budget_equipment(
    app: BudgetApp, items: list[ProjectItem], pending: list[str],
) -> tuple[str, str]:
    """Separa medios auxiliares fisicos y maquinaria sin incluir porcentajes ni precios auxiliares."""
    auxiliaries: dict[tuple[str, str], str] = {}
    machinery: dict[tuple[str, str], str] = {}
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
            found[(code, _normalized_text(label))] = f"• {code} — {label}"

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
    return (
        "\n".join(auxiliaries.values()) or (
            "No se han identificado medios auxiliares en el presupuesto disponible."
        ),
        "\n".join(machinery.values()) or (
            "No se ha identificado maquinaria en el presupuesto disponible."
        ),
    )


def _inline_text(value: str) -> str:
    """Elimina la sintaxis Markdown que no aporta formato al informe final."""
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    return value.replace("`", "").replace("**", "").replace("__", "")


def _display_value(value: str) -> str:
    """Mantiene los valores en mayusculas sin cambiar el simbolo de metro cuadrado."""
    return re.sub(r"(?<!\w)M²(?!\w)", "m²", value.upper())


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
        return _display_value(replacements.get(
            match.group(1), f"[VALIDAR] Falta definir el dato {match.group(1)}."
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
                headers = tuple(_inline_text(cell) for cell in table_lines[0])
                rows = tuple(
                    tuple(_inline_text(cell) for cell in row)
                    for row in table_lines[1:]
                )
                result.append(_Block("table", headers=headers, rows=rows))
            continue
        if line.startswith("```"):
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                index += 1
            index += 1
            continue
        if line.startswith(">"):
            line = line[1:].strip()
        elif line.startswith("- "):
            line = f"- {line[2:].strip()}"
        result.append(_Block("paragraph", _inline_text(line)))
        index += 1
    return result


def _read_library() -> dict[str, str]:
    """Lee las plantillas necesarias sin extraer ni modificar la biblioteca."""
    try:
        with ZipFile(LIBRARY_PATH) as archive:
            return {
                key: archive.read(member).decode("utf-8-sig")
                for key, member in _TEMPLATE_MEMBERS.items()
            }
    except (
        OSError, KeyError, BadZipFile, RuntimeError, NotImplementedError, UnicodeError,
    ) as exc:
        raise ValueError(f"No se pudo leer la biblioteca ESS {LIBRARY_PATH}: {exc}") from exc


def _rows_from_markdown(text: str) -> list[list[str]]:
    """Extrae todas las filas de tabla Markdown de una plantilla."""
    rows = []
    for line in text.splitlines():
        cells = _table_cells(line)
        if cells is not None and not _is_table_separator(cells):
            rows.append([_inline_text(cell) for cell in cells])
    return rows


def _parse_work_units(text: str) -> dict[str, dict[str, str]]:
    """Indexa las fichas generales de unidad de obra por su identificador UO."""
    units: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(UO-[A-Z0-9-]+)\s*(?:-\s*(.*))?$", line.strip())
        if heading:
            current = heading.group(1)
            units[current] = {"Nombre": heading.group(2) or current}
            continue
        cells = _table_cells(line)
        if current and cells and len(cells) >= 2:
            units[current][_inline_text(cells[0])] = _inline_text(cells[1])
    return units


def _parse_catalogues(text: str) -> dict[str, str]:
    """Resuelve cada identificador preventivo contra el catalogo de la biblioteca."""
    result = {}
    for row in _rows_from_markdown(text):
        if len(row) >= 2 and re.fullmatch(r"(?:R|MP|PC|EPI|EQ)-[A-Z0-9-]+", row[0]):
            result[row[0]] = row[1]
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


def _parse_fallbacks(text: str) -> list[tuple[tuple[str, ...], str]]:
    """Lee las palabras clave y unidades provisionales definidas en la plantilla 09."""
    result = []
    for line in text.splitlines():
        row = _table_cells(line)
        if row is None or len(row) < 2:
            continue
        unit = _inline_text(row[1])
        if not unit.startswith("UO-"):
            continue
        terms = tuple(re.findall(r"`([^`]+)`", row[0]))
        if terms:
            result.append((terms, unit))
    return result


def _reference_groups(text: str) -> dict[str, list[str]]:
    """Agrupa las referencias preventivas encontradas por tipo de catalogo."""
    groups = {category: [] for category in _CATEGORY_ORDER}
    for reference in _REFERENCE_RE.findall(text):
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
) -> None:
    """Agrega el contenido de una plantilla Markdown sin su titulo principal."""
    blocks.extend(_markdown_blocks(text, replacements, skip_title=True))


def _currency_label(settings) -> str:
    """Obtiene el nombre de la divisa configurada para los importes del estudio."""
    from bc3_gui import currency_label

    return currency_label(settings.currency, settings.custom_currency)


def _format_number(value: float | Decimal) -> str:
    """Formatea una magnitud conforme al formato numerico del presupuesto."""
    from bc3_gui import fmt_number

    return fmt_number(float(value))


def _format_amount(value: float | Decimal, currency: str) -> str:
    """Formatea un importe con la divisa del proyecto."""
    return f"{_format_number(value)} {currency}"


def _chapter_label(app, chapter: str) -> str:
    """Combina el codigo normalizado del capitulo con su titulo de presupuesto."""
    from bc3_gui import format_chapter_code

    title = app._chapter_title(chapter)
    code = format_chapter_code(chapter)
    return f"{code} - {title}" if title and title != code else code


def _read_member(archive: ZipFile, path: str) -> str:
    """Lee una ficha seleccionada y expresa claramente las incidencias de la biblioteca."""
    try:
        return archive.read(path).decode("utf-8-sig")
    except (KeyError, UnicodeDecodeError) as exc:
        raise ValueError(f"No se pudo leer la ficha ESS {path}: {exc}") from exc


def _load_selected_fiches(
    archive: ZipFile, paths: list[str], pending: list[str],
) -> tuple[dict[str, dict[str, list[str]]], list[tuple[str, str]]]:
    """Lee fichas unicas, acumula sus referencias y recoge sus validaciones."""
    by_unit: dict[str, dict[str, list[str]]] = {}
    contents = []
    for path in paths:
        text = _read_member(archive, path)
        contents.append((path, text))
        units = list(dict.fromkeys(_UNIT_RE.findall(text)))
        references = _reference_groups(text)
        if not units:
            pending.append(
                f"[VALIDAR] La ficha ESS {PurePosixPath(path).name} no identifica unidades UO."
            )
        for unit in units:
            by_unit[unit] = _merge_references(by_unit.get(unit, _reference_groups("")), references)
        lines = text.splitlines()
        in_validations = False
        for line in lines:
            stripped = line.strip()
            if re.match(r"^##\s+", stripped):
                in_validations = "validacion" in _normalized_text(stripped)
                continue
            if in_validations and stripped:
                validation = _inline_text(stripped.lstrip("- ").strip())
                message = (
                    f"[VALIDAR] Revisar ficha ESS {PurePosixPath(path).stem}: {validation}"
                )
                if message not in pending:
                    pending.append(message)
    return by_unit, contents


def _build_study_content(app) -> _StudyContent:
    """Combina los datos del proyecto con las secciones y reglas de las plantillas ESS."""
    work_items = [
        item for item in app.budget_items
        if not item.source.startswith(SAFETY_BUDGET_SOURCE)
    ]
    safety_items = [
        item for item in app.budget_items
        if item.source.startswith(SAFETY_BUDGET_SOURCE)
    ]
    if not safety_items:
        raise ValueError("Crea primero el capítulo CAP. XX integrado en el presupuesto.")
    if not work_items:
        raise ValueError("El presupuesto no contiene partidas de obra para clasificar en el ESS.")

    templates = _read_library()
    work_units = _parse_work_units(templates["work_units"])
    catalogues = _parse_catalogues(templates["catalogues"])
    index = _parse_index(templates["index"])
    fallbacks = _parse_fallbacks(templates["budget_linking"])
    matrix: dict[str, list[str]] = {}
    for row in _rows_from_markdown(templates["risk_matrix"]):
        if len(row) >= 2 and row[0].startswith("UO-"):
            matrix[row[0]] = list(dict.fromkeys(_REFERENCE_RE.findall(row[1])))

    pending: list[str] = []
    unit_ids = ["UO-IMPLANTACION"]
    matched_codes: list[str] = []
    trace_rows: list[tuple[str, ...]] = []
    fiche_paths: list[str] = []

    def add_unit(unit: str) -> None:
        """Anade una unidad una sola vez y conserva el orden de las partidas."""
        if unit not in unit_ids:
            unit_ids.append(unit)

    for item in work_items:
        code = re.sub(r"\s+", "", item.code).upper()
        matches = [(prefix, path) for prefix, path in index if code.startswith(prefix)]
        if matches:
            longest = max(len(prefix) for prefix, _ in matches)
            selected = [(prefix, path) for prefix, path in matches if len(prefix) == longest]
            selected_names = []
            for prefix, path in selected:
                if path not in fiche_paths:
                    fiche_paths.append(path)
                selected_names.append(f"{prefix}: {PurePosixPath(path).name}")
                matched_codes.append(item.code)
            result_label = "; ".join(selected_names)
        else:
            description = _normalized_text(item.summary + " " + item.text)
            selected_units = list(dict.fromkeys(
                unit for terms, unit in fallbacks
                if any(_normalized_text(term) in description for term in terms)
            ))
            for unit in selected_units:
                add_unit(unit)
            pending.append(
                f"[VALIDAR] Partida sin ficha ESS BCCA: {item.code} - {item.summary}."
            )
            result_label = (
                "Clasificacion provisional: " + ", ".join(selected_units)
                if selected_units else "Sin ficha ni clasificacion provisional"
            )
        trace_rows.append((
            item.code.upper(), item.unit.upper(), item.summary.upper(),
            _format_number(item.measurement),
            _chapter_label(app, item.chapter).upper(), result_label.upper(),
        ))

    fiche_paths = list(dict.fromkeys(fiche_paths))
    if fiche_paths:
        try:
            with ZipFile(LIBRARY_PATH) as archive:
                unit_fiche_refs, fiche_contents = _load_selected_fiches(
                    archive, fiche_paths, pending,
                )
        except (OSError, BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise ValueError(f"No se pudieron leer las fichas ESS seleccionadas: {exc}") from exc
    else:
        unit_fiche_refs, fiche_contents = {}, []
    for unit in unit_fiche_refs:
        add_unit(unit)

    for unit in unit_ids:
        if unit not in work_units:
            pending.append(f"[VALIDAR] No existe una ficha general para la unidad {unit}.")

    unit_references: dict[str, dict[str, list[str]]] = {}
    for unit in unit_ids:
        fields = work_units.get(unit, {})
        template_refs = _reference_groups("\n".join(
            value for field_name, value in fields.items()
            if field_name.casefold() != "nombre"
        ))
        matrix_refs = _reference_groups("\n".join(matrix.get(unit, [])))
        fiche_refs = unit_fiche_refs.get(unit, _reference_groups(""))
        unit_references[unit] = _merge_references(template_refs, matrix_refs, fiche_refs)

    all_references = _merge_references(*unit_references.values())
    for category in _CATEGORY_ORDER:
        for reference in all_references[category]:
            if reference not in catalogues:
                pending.append(f"[VALIDAR] No existe descripcion de catalogo para {reference}.")

    assessment = evaluate_study_criteria(app)
    state = app.safety_health_state if isinstance(app.safety_health_state, dict) else {}
    selected_criteria = _study_criteria_values(state.get("study_criteria"))
    if selected_criteria is None:
        selected_criteria = assessment.fulfilled
    full_study = any(selected_criteria)
    title = (
        "ESTUDIO DE SEGURIDAD Y SALUD" if full_study
        else "ESTUDIO BÁSICO DE SEGURIDAD Y SALUD"
    )
    safety_chapter = safety_items[0].chapter
    chapter_label = _chapter_label(app, safety_chapter)
    settings = app.report_settings
    project_title = app._project_title().upper()
    currency = _currency_label(settings)

    def missing(field_name: str) -> str:
        """Registra un dato no disponible y devuelve su aviso para la plantilla."""
        message = (
            f"[VALIDAR] Falta definir {field_name} antes de aprobar el Plan de Seguridad y Salud."
        )
        pending.append(message)
        return f"[VALIDAR] Falta definir {field_name}."

    work_summaries = list(dict.fromkeys(item.summary for item in work_items if item.summary))
    description = (
        "Resumen automatico segun las partidas presupuestarias: "
        + "; ".join(work_summaries)
        + ". Validar el proceso constructivo y su secuencia."
    )
    pending.append(
        "[VALIDAR] Contrastar el resumen de trabajos y la secuencia con el proyecto de ejecucion."
    )
    totals = budget_totals(
        sum(float(item.import_total) for item in app.budget_items), settings,
    )
    duration_text = (
        f"{assessment.duration_days} días laborables"
        if assessment.duration_days else missing("el plazo de ejecucion")
    )
    workers_text = f"{int(assessment.peak_workers)} " + (
        "persona" if assessment.peak_workers == 1 else "personas"
    )
    pending.append("[VALIDAR] Confirmar el numero maximo real de trabajadores simultaneos.")
    if assessment.worker_days <= 0:
        pending.append("[VALIDAR] Confirmar el volumen de mano de obra previsto.")

    saved_conditions = restore_external_conditions(state.get("external_conditions"))
    if state.get("centros_asistenciales_modo") == "tabla":
        centers = format_centers(restore_centers(state))
    else:
        centers = str(state.get(
            "centros_asistenciales_texto", state.get("centros_asistenciales", ""),
        )).strip()
    center_condition = saved_conditions.get("centro_asistencial", {})
    center_default = next(
        default for key, _label, default in EXTERNAL_CONDITIONS if key == "centro_asistencial"
    )
    if not centers and translate(center_condition.get("description", ""), "es") != center_default:
        centers = center_condition.get("description", "")
    if not centers and saved_conditions.get("centro_asistencial", {}).get("applicability") != "no":
        centers = missing("el centro asistencial de referencia y su telefono")

    location = str(getattr(settings, "location", "")).strip()
    start_date = "[VALIDAR] Falta definir la fecha de inicio prevista."
    planning_state = getattr(app, "planning_state", {})
    params_state = planning_state.get("params", {}) if isinstance(planning_state, dict) else {}
    planned_start = params_state.get("start") if isinstance(params_state, dict) else None
    if isinstance(planned_start, str):
        try:
            start_date = date.fromisoformat(planned_start).strftime("%d/%m/%Y")
        except ValueError:
            pending.append("[VALIDAR] La fecha de inicio guardada en la planificacion no es valida.")
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
    unit_names = [
        f"{unit} - {work_units[unit].get('Nombre', unit)}"
        if unit in work_units else unit for unit in unit_ids
    ]
    bcca_codes = ", ".join(dict.fromkeys(matched_codes)) or missing(
        "los codigos BCCA identificados"
    )
    surfaces = [
        Decimal(str(item.measurement)) for item in work_items
        if item.unit.strip().casefold() in {"m2", "m²", "m^2"}
    ]
    if any(not surface.is_finite() or surface < 0 for surface in surfaces):
        raise ValueError("Las mediciones de superficie deben ser finitas y no negativas.")
    surface_text = (
        f"{_format_number(max(surfaces))} m²"
        if surfaces else missing("la superficie de actuacion")
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
        "Titulo del proyecto": project_title or missing("el titulo oficial del proyecto"),
        "Tipo de actuacion": project_type,
        "Promotor / entidad": str(getattr(settings, "organization", "")).strip()
        or missing("la entidad promotora"),
        "Municipio": municipality_names or missing("el municipio"),
        "Provincia": provinces or missing("la provincia"),
        "Comunidad autonoma": communities or missing("la comunidad autonoma"),
        "Emplazamiento": location or missing("el emplazamiento de la obra"),
        "Tecnico redactor": str(getattr(settings, "author", "")).strip()
        or missing("el tecnico redactor"),
        "Fecha de emision": date.today().strftime("%d/%m/%Y"),
        "Presupuesto de ejecucion material": _format_amount(totals["pem"], currency),
        "Presupuesto de licitacion": _format_amount(totals["total"], currency),
        "Presupuesto de Seguridad y Salud integrado": _format_amount(
            sum(float(item.import_total) for item in safety_items), currency,
        ),
        "Plazo de ejecucion": duration_text,
        "Numero maximo simultaneo de trabajadores": workers_text,
        "Superficie, longitud o volumen": surface_text,
        "Fecha prevista de inicio": start_date,
        "Descripcion de las obras": description,
        "Partidas presupuestarias": "Relacion completa en el anexo de trazabilidad presupuestaria.",
        "Unidades de obra aplicables": "; ".join(unit_names),
        "Codigos BCCA identificados": bcca_codes,
        "Centro asistencial": centers,
    }
    external_values = {}
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
    auxiliary_means, machinery = _budget_equipment(app, app.budget_items, pending)
    replacements = {
        "titulo_proyecto": data_values["Titulo del proyecto"],
        "tipo_actuacion": data_values["Tipo de actuacion"],
        "entidad": data_values["Promotor / entidad"],
        "municipio": data_values["Municipio"],
        "provincia": data_values["Provincia"],
        "comunidad_autonoma": data_values["Comunidad autonoma"],
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
        **external_values,
        "medios_auxiliares": auxiliary_means,
        "maquinaria_empleada": machinery,
        "centros_asistenciales_resumen": "\n\n".join(contacts),
        "riesgos_detectados": "\n".join(
            f"• {reference}: {catalogues[reference]}"
            for reference in all_references["R"] if reference in catalogues
        ) or missing("los riesgos detectados"),
        "tipo_estudio": title,
    }
    criteria_labels = (
        "a) Presupuesto de ejecucion por contrata con IVA superior a 450.759,08 EUR.",
        "b) Duracion superior a 30 dias laborables y mas de 20 trabajadores simultaneos.",
        "c) Volumen de mano de obra superior a 500 jornadas.",
        "d) Obra de tuneles, galerias, conducciones subterraneas o presas.",
    )
    fulfilled_labels = [
        label for selected, label in zip(selected_criteria, criteria_labels) if selected
    ]
    if fulfilled_labels:
        justification = (
            "Se cumplen los siguientes criterios del articulo 4.1 del RD 1627/1997:\n\n"
            + "\n\n".join(f"- {label}" for label in fulfilled_labels)
            + f"\n\nPor tanto, se define un {title}."
        )
    else:
        justification = (
            "No se cumple ninguno de los criterios del articulo 4.1 del RD 1627/1997. "
            f"Por tanto, se define un {title}."
        )
    replacements["justificacion_estudio"] = justification
    replacements = {name: _display_value(value) for name, value in replacements.items()}
    project_data_rows = []
    section_rows = []
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
            project_data_rows.append((row[1], replacements[name]))

    blocks: list[_Block] = []
    _add_heading(blocks, "Objeto y alcance", 1)
    introduction = re.split(r"(?m)^##\s+", templates["project_data"], maxsplit=1)[0]
    _add_markdown(blocks, introduction, replacements)
    _add_heading(blocks, "Datos del proyecto", 2)
    blocks.append(_Block(
        "caption",
        "Tabla 1. Datos del proyecto y condiciones de ejecución de la obra.",
    ))
    blocks.append(_Block(
        "table",
        headers=("Aspecto del proyecto", "Descripción / información"),
        rows=tuple(project_data_rows),
        project_summary=True,
        section_rows=tuple(section_rows),
    ))

    _add_heading(blocks, "Memoria general", 1)
    _add_markdown(blocks, templates["memory"], replacements)

    _add_heading(blocks, "Fichas de unidades de obra aplicables", 1)
    for unit in unit_ids:
        details = work_units.get(unit, {})
        _add_heading(
            blocks, f"{unit} - {details.get('Nombre', unit)}", 2,
        )
        description_text = details.get("Descripcion")
        if description_text:
            blocks.append(_Block("paragraph", description_text))
        elif unit not in work_units:
            blocks.append(_Block(
                "paragraph",
                "[VALIDAR] La biblioteca no contiene una descripcion general para esta unidad.",
            ))
        references = unit_references[unit]
        reference_rows = []
        for category in _CATEGORY_ORDER:
            if references[category]:
                reference_rows.append((
                    _CATEGORY_LABELS[category],
                    ", ".join(references[category]),
                ))
        if reference_rows:
            blocks.append(_Block(
                "table", headers=("Contenido preventivo", "Referencias aplicables"),
                rows=tuple(reference_rows),
            ))

    _add_heading(blocks, "Matriz detallada de riesgos por unidad", 1)
    matrix_rows = []
    for unit in unit_ids:
        references = unit_references[unit]["R"]
        if references:
            matrix_rows.append((
                unit,
                work_units.get(unit, {}).get("Nombre", unit),
                "; ".join(
                    f"{reference}: {catalogues.get(reference, '[VALIDAR] Descripcion no disponible')}"
                    for reference in references
                ),
            ))
    if matrix_rows:
        blocks.append(_Block(
            "table", headers=("Unidad", "Descripcion", "Riesgos identificados"),
            rows=tuple(matrix_rows),
        ))
    _add_heading(blocks, "Catalogo preventivo consolidado", 1)
    catalogue_rows = []
    for category in _CATEGORY_ORDER:
        for reference in all_references[category]:
            catalogue_rows.append((
                _CATEGORY_LABELS[category], reference,
                catalogues.get(reference, "[VALIDAR] Descripcion de catalogo pendiente."),
            ))
    if catalogue_rows:
        blocks.append(_Block(
            "table", headers=("Tipo", "Referencia", "Descripcion"),
            rows=tuple(catalogue_rows),
        ))
    blocks.append(_Block(
        "paragraph",
        "La seleccion de riesgos, medidas, protecciones, EPI y equipos debe confirmarse "
        "con la evaluacion de riesgos y el procedimiento real de cada tajo.",
    ))

    _add_heading(blocks, "Fichas ESS de la biblioteca BCCA", 1)
    for path, text in fiche_contents:
        heading = next(
            (_inline_text(line.lstrip("# ").strip()) for line in text.splitlines()
             if line.startswith("# ")),
            PurePosixPath(path).stem,
        )
        _add_heading(blocks, f"{heading} ({PurePosixPath(path).name})", 2)
        _add_markdown(blocks, text)

    _add_heading(blocks, "Condiciones generales de seguridad y salud", 1)
    _add_markdown(blocks, templates["general_conditions"])
    _add_heading(blocks, "Gestion preventiva, emergencias y seguimiento", 1)
    _add_markdown(blocks, templates["emergencies"], replacements)
    _add_heading(blocks, "Pliego de condiciones", 1)
    _add_markdown(blocks, templates["specification"])

    _add_heading(blocks, "Anexo de trazabilidad presupuesto - ESS", 1)
    blocks.append(_Block(
        "paragraph",
        "Se aplico el prefijo BCCA mas largo disponible. Las clasificaciones por descripcion "
        "son provisionales y se senalan para revision.",
    ))
    if trace_rows:
        blocks.append(_Block(
            "table",
            headers=("Codigo", "Ud.", "Descripcion", "Medicion", "Capitulo", "Ficha o clasificacion"),
            rows=tuple(trace_rows),
        ))
    _add_heading(blocks, "Partidas del presupuesto general", 2)
    budget_rows = tuple(
        (
            item.code.upper(), item.unit.upper(), item.summary.upper(),
            _format_number(item.measurement), _chapter_label(app, item.chapter).upper(),
            _format_amount(item.import_total, currency),
        )
        for item in app.budget_items
    )
    blocks.append(_Block(
        "table",
        headers=("Codigo", "Ud.", "Descripcion", "Medicion", "Capitulo", "Importe"),
        rows=budget_rows,
    ))
    blocks.append(_Block(
        "paragraph",
        "Las partidas de Seguridad y Salud permanecen integradas en el presupuesto general; "
        "este documento no genera un presupuesto preventivo independiente.",
    ))

    pending = list(dict.fromkeys(pending))
    _add_heading(blocks, "Datos pendientes y advertencias de validacion", 1)
    if pending:
        blocks.extend(_Block("paragraph", message) for message in pending)
    else:
        blocks.append(_Block(
            "paragraph",
            "No se han detectado datos pendientes automaticamente. La comprobacion tecnica "
            "de las condiciones reales de obra sigue siendo necesaria.",
        ))

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
    )


def _column_ratios(headers: tuple[str, ...]) -> tuple[float, ...]:
    """Define anchos de tabla legibles en PDF y ODT segun su numero de columnas."""
    if len(headers) == 2:
        return (1.2, 4.8)
    if len(headers) == 3:
        return (1.1, 1.8, 4.1)
    if len(headers) == 6:
        return (1.0, 0.6, 3.2, 0.8, 1.5, 1.2)
    return tuple(1.0 for _ in headers)


def _numbered_blocks(blocks: list[_Block]) -> list[_Block]:
    """Aplica a los epigrafes la misma numeracion y mayusculas que el PPTP."""
    source = "\n".join(
        f"{'#' * block.level} {block.text}"
        for block in blocks if block.kind == "heading"
    )
    titles = iter(
        line.split(" ", 1)[1] for line in number_headings(source).splitlines()
    )
    return [
        replace(block, text=next(titles)) if block.kind == "heading" else block
        for block in blocks
    ]


def _pdf_project_summary(block: _Block, layout: ReportLayout):
    """Maqueta los datos del proyecto con cabecera azul y filas alternas."""
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Table

    class SummaryTable(Table):
        """Conserva la alineacion tambien en las tablas internas creadas al partir una fila."""

        def __init__(self, *args, **kwargs):
            """Evita que ReportLab vuelva al centrado en sus fragmentos intermedios."""
            kwargs.setdefault("hAlign", "LEFT")
            super().__init__(*args, **kwargs)

    text = ParagraphStyle(
        "ESSSummaryText", parent=layout.normal, fontSize=9.5, leading=12,
    )
    header = ParagraphStyle(
        "ESSSummaryHeader", parent=text, fontName=layout.bold_font, textColor=colors.white,
    )
    rows = [
        [layout.text(value, header) for value in block.headers],
        *[
            [
                layout.text(value, header if index in block.section_rows else text)
                for value in row
            ]
            for index, row in enumerate(block.rows)
        ],
    ]
    return SummaryTable(
        rows, colWidths=[layout.content_width * ratio for ratio in (0.3, 0.7)],
        repeatRows=1, splitInRow=1, hAlign="LEFT", style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(TABLE_HEADER_BACKGROUND)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
                colors.HexColor(TABLE_ALTERNATE_BACKGROUND), colors.white,
            ]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B7C9E2")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            *[
                command for index in block.section_rows for command in (
                    ("SPAN", (0, index + 1), (-1, index + 1)),
                    ("BACKGROUND", (0, index + 1), (-1, index + 1), colors.HexColor(TABLE_HEADER_BACKGROUND)),
                )
            ],
        ],
    )


def _write_odt_project_summary(document, block: _Block) -> None:
    """Aplica al resumen ODT los mismos colores y filas alternas que al PDF."""
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
    for name, color in (
        ("ESSSummaryHeaderCell", TABLE_HEADER_BACKGROUND),
        ("ESSSummaryBlueCell", TABLE_ALTERNATE_BACKGROUND),
        ("ESSSummaryWhiteCell", "#FFFFFF"),
    ):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "table-cell",
        })
        ET.SubElement(style, _tag("style:table-cell-properties"), {
            _tag("fo:background-color"): color,
            _tag("fo:padding"): "4pt", _tag("fo:border"): "0.5pt solid #B7C9E2",
            _tag("style:vertical-align"): "top",
        })
    document.table(
        block.headers, block.rows, ratios=(0.3, 0.7), body_text_style="ESSSummaryText",
    )
    table = document.body[-1]
    table.set(_tag("table:name"), "ESSProjectSummary")
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
        for cell in row.findall(_tag("table:table-cell")):
            cell.set(
                _tag("table:style-name"),
                "ESSSummaryBlueCell" if index % 2 == 0 else "ESSSummaryWhiteCell",
            )


def _write_pdf(app, target, content: _StudyContent) -> None:
    """Maqueta el estudio con indice enlazado y paginas reales, como el PPTP."""
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, SimpleDocTemplate
    from reportlab.platypus.doctemplate import LayoutError
    from reportlab.platypus.tableofcontents import TableOfContents

    settings = app.report_settings
    project = report_project_label(content.project_title, content.currency_label)
    layout = ReportLayout(
        template_path=settings.template_path,
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
    heading_styles = {
        level: ParagraphStyle(
            f"ESSHeading{level}", parent=body, fontName=layout.bold_font,
            fontSize=max(10, 15 - level), leading=max(13, 18 - level),
            spaceBefore=10, keepWithNext=True, alignment=TA_LEFT,
        )
        for level in range(1, 5)
    }
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            f"ESSContents{level}", parent=body, fontSize=10,
            leading=13, leftIndent=12 * level, firstLineIndent=0,
            rightIndent=24, spaceBefore=4 if level == 0 else 0,
            alignment=TA_LEFT,
        )
        for level in range(DEFAULT_TOC_DEPTH)
    ]
    story = [
        layout.text("ÍNDICE", layout.title_style), toc, PageBreak(),
    ]

    heading_index = 0
    caption_style = ParagraphStyle(
        "ESSCaption", parent=body, fontSize=9, leading=12,
        keepWithNext=True, alignment=TA_LEFT,
    )
    for block in content.blocks:
        if block.kind == "heading":
            paragraph = layout.text(block.text, heading_styles[block.level])
            paragraph._ess_heading = (
                block.level - 1, block.text, f"ESSHeading{heading_index}",
            )
            heading_index += 1
            story.append(paragraph)
        elif block.kind == "paragraph":
            story.append(layout.text(block.text, body))
        elif block.kind == "caption":
            story.append(layout.text(block.text, caption_style))
        elif block.kind == "table":
            if block.project_summary:
                story.append(_pdf_project_summary(block, layout))
            else:
                ratios = _column_ratios(block.headers)
                story.append(layout.table(block.headers, block.rows, ratios))

    class StudyDocTemplate(SimpleDocTemplate):
        """Registra los destinos y las paginas del indice en cada pasada."""

        def afterFlowable(self, flowable) -> None:
            """Vincula cada epigrafe con su pagina definitiva tras la maquetacion."""
            heading = getattr(flowable, "_ess_heading", None)
            if heading is not None:
                level, text, key = heading
                self.canv.bookmarkPage(key)
                if level < DEFAULT_TOC_DEPTH:
                    self.notify("TOCEntry", (level, escape(text), self.page, key))

    document = StudyDocTemplate(
        str(target), pagesize=layout.page_size, leftMargin=LEFT_MARGIN,
        rightMargin=RIGHT_MARGIN, topMargin=layout.top_margin(content.title, project),
        bottomMargin=layout.bottom_margin(), title=content.title,
        author=settings.author,
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

    try:
        document.multiBuild(
            story, onFirstPage=header, onLaterPages=header,
        )
    except LayoutError as exc:
        raise ValueError(f"No se pudo maquetar el Estudio de Seguridad y Salud: {exc}") from exc


def _write_odt(app, target, content: _StudyContent) -> None:
    """Escribe el mismo estudio como documento ODT editable basado en la plantilla."""
    from xml.etree import ElementTree as ET
    from bc3_gui import currency_label
    from odt_reports import _new_report, _tag

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
    if document.template_footer is not None:
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
    document.preserve_template_styles = True
    template_styles = template_paragraph_styles(document.template_header)
    for name, parent, level in [
        ("ESSBody", template_styles.get("body", "ReportBody"), 0),
        ("ESSCaption", template_styles.get("body", "ReportBody"), 0),
        *[
            (
                f"ESSHeading{level}",
                template_styles.get(
                    f"heading{level}", template_styles.get("heading", f"Heading{level}"),
                ),
                level,
            )
            for level in range(1, 5)
        ],
    ]:
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): parent,
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), {
            _tag("fo:text-align"): "start" if level else "justify",
            _tag("fo:break-before"): "auto", _tag("fo:break-after"): "auto",
            _tag("fo:keep-with-next"): "always" if level or name == "ESSCaption" else "auto",
            _tag("fo:keep-together"): "auto",
            _tag("fo:orphans"): "2", _tag("fo:widows"): "2",
            _tag("fo:text-indent"): "0pt",
            _tag("fo:margin-top"): f"{14 if level == 1 else 10 if level else 0}pt",
            _tag("fo:margin-bottom"): "5pt" if level else "6pt",
            _tag("fo:line-height"): "115%",
        })
        if level:
            style.set(_tag("style:default-outline-level"), str(level))
            style.set(_tag("style:next-style-name"), "ESSBody")
            if f"heading{level}" not in template_styles:
                size = (14, 12, 11, 10.5)[level - 1]
                ET.SubElement(style, _tag("style:text-properties"), {
                    _tag("fo:font-size"): f"{size}pt",
                    _tag("style:font-size-asian"): f"{size}pt",
                    _tag("style:font-size-complex"): f"{size}pt",
                    _tag("fo:font-weight"): "bold",
                    _tag("style:font-weight-asian"): "bold",
                    _tag("style:font-weight-complex"): "bold",
                    _tag("fo:font-style"): "normal",
                })
    headings = [
        (block.level, block.text) for block in content.blocks
        if block.kind == "heading"
    ]
    document.table_of_contents(headings, max_level=DEFAULT_TOC_DEPTH)
    for style in document.automatic_styles:
        name = style.get(_tag("style:name"), "")
        if re.fullmatch(r"PPTPContents[1-6]", name):
            style.set(_tag("style:parent-style-name"), "ESSBody")
            properties = style.find(_tag("style:paragraph-properties"))
            properties.set(_tag("fo:text-align"), "start")
            properties.set(_tag("fo:line-height"), "115%")
            properties.set(_tag("fo:margin-bottom"), "4pt")
            properties.set(_tag("fo:keep-with-next"), "auto")
            ET.SubElement(style, _tag("style:text-properties"), {
                _tag("fo:font-size"): "10pt",
                _tag("fo:font-weight"): "bold" if name == "PPTPContents1" else "normal",
            })
    bookmark_index = 0
    for block in content.blocks:
        if block.kind == "heading":
            document.heading(
                block.text, level=block.level, style=f"ESSHeading{block.level}",
                bookmark=f"PPTPHeading{bookmark_index}",
            )
            bookmark_index += 1
        elif block.kind == "paragraph":
            document.paragraph(block.text, style="ESSBody")
        elif block.kind == "caption":
            document.paragraph(block.text, style="ESSCaption")
        elif block.kind == "table":
            if block.project_summary:
                _write_odt_project_summary(document, block)
            else:
                document.table(
                    block.headers, block.rows, ratios=_column_ratios(block.headers),
                )
    document.name_text_styles({
        "ESSBody": "ESS - Cuerpo",
        "ESSCaption": "ESS - Leyenda de tabla",
        "ESSSummaryHeader": "ESS - Datos - Cabecera",
        "ESSSummaryText": "ESS - Datos - Texto",
        **{f"ESSHeading{level}": f"ESS - Titulo {level}" for level in range(1, 5)},
        **{f"PPTPContents{level}": f"ESS - Indice {level}" for level in range(1, 3)},
    })
    document.write(target)


def write_study_report(app, target) -> None:
    """Genera el Estudio ESS completo como PDF u ODT segun la configuracion del proyecto."""
    target = Path(target)
    content = _build_study_content(app)
    content.blocks = _numbered_blocks(content.blocks)
    output_format = app.report_settings.output_format.lower()
    if output_format not in {"pdf", "odt"}:
        raise ValueError(f"Formato de informe no admitido: {output_format}.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=target.suffix, delete=False,
        ) as handle:
            temporary = Path(handle.name)
        if output_format == "pdf":
            _write_pdf(app, temporary, content)
        else:
            _write_odt(app, temporary, content)
        temporary.replace(target)
    except (OSError, BadZipFile) as exc:
        raise ValueError(f"No se pudo guardar el Estudio de Seguridad y Salud: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
