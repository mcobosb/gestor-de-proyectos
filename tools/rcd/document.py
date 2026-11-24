"""Memoria, pliego y presupuesto RCDs desde documentos base editables."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from html import escape
from pathlib import Path
import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from budget_reports import amount_in_words, money, report_output_format
from tools._shared.budget_sources import RCD_BUDGET_SOURCE, matches_tool_chapter
from tools._library_editor import library_members, read_markdown
from tools._library_markdown import document_markdown, text_blocks
from tools.document_structure.catalog import RCD_NUMBER
from document_styles import ROLES, odt_typography
from tools._shared.document_layout import (
    add_odt_blue_table_cells, add_odt_blue_table_headers, add_odt_list_style,
    add_odt_narrative_styles,
    pdf_list_style, project_document_variables, split_list_item, style_odt_blue_table,
)
from . import ler_catalog
from .management import DESTINATIONS, PREVENTION, SELECTIONS, selected_wastes
from .facilities import FACILITY_FIELDS
from collaborative_storage import shared_library_path

if TYPE_CHECKING:
    from bc3_gui import BudgetApp
    from budget_reports import ReportSettings
    from . import RcdState


TITLE = "ESTUDIO DE GESTIÓN DE RESIDUOS DE CONSTRUCCIÓN Y DEMOLICIÓN"
FOOTER_TITLE = f"ANEJO Nº {int(RCD_NUMBER)}. ESTUDIO DE GESTIÓN DE RCDs"
FOOTER_CHAPTER = re.compile(r"\{\{cap[ií]tulo\}\}")
DESCRIPTION_HEADING = "1.2. DESCRIPCIÓN DE LA ACTUACIÓN"
DESCRIPTION_MARKER = "{{descripcion_actuacion}}"
COMPACT_TABLE_WIDTH = 0.80
LAW_URLS = {
    "Ley 7/2022": "https://www.boe.es/eli/es/l/2022/04/08/7/con",
    "Ley 31/1995": "https://www.boe.es/eli/es/l/1995/11/08/31/con",
    "Ley 21/2013": "https://www.boe.es/eli/es/l/2013/12/09/21/con",
    "Ley 37/2003": "https://www.boe.es/eli/es/l/2003/11/17/37/con",
    "Real Decreto 105/2008": "https://www.boe.es/eli/es/rd/2008/02/01/105/con",
    "Real Decreto 553/2020": "https://www.boe.es/eli/es/rd/2020/06/02/553/con",
    "Real Decreto 646/2020": "https://www.boe.es/eli/es/rd/2020/07/07/646/con",
    "Real Decreto 396/2006": "https://www.boe.es/eli/es/rd/2006/03/31/396/con",
    "Real Decreto 108/1991": "https://www.boe.es/eli/es/rd/1991/02/01/108/con",
    "Real Decreto 180/2015": "https://www.boe.es/eli/es/rd/2015/02/17/180/con",
    "Real Decreto 110/2015": "https://www.boe.es/eli/es/rd/2015/02/20/110/con",
    "Real Decreto 1055/2022": "https://www.boe.es/eli/es/rd/2022/12/27/1055/con",
    "Real Decreto 9/2005": "https://www.boe.es/eli/es/rd/2005/01/14/9/con",
    "Real Decreto 1627/1997": "https://www.boe.es/eli/es/rd/1997/10/24/1627/con",
    "Real Decreto 486/1997": "https://www.boe.es/eli/es/rd/1997/04/14/486/con",
    "Real Decreto 1432/2008": "https://www.boe.es/eli/es/rd/2008/08/29/1432/con",
    "Orden APM/1007/2017": "https://www.boe.es/eli/es/o/2017/10/10/apm1007/con",
    "Decisión 2000/532/CE": "https://eur-lex.europa.eu/eli/dec/2000/532/oj?locale=es",
}
LAW_URLS.update({name.replace("Real Decreto", "RD"): url
                 for name, url in tuple(LAW_URLS.items()) if name.startswith("Real Decreto")})
LAW_CITATION = re.compile(r"\b(" + "|".join(map(re.escape, LAW_URLS)) + r")(?![\w/])", re.IGNORECASE)
LAW_URLS = {name.casefold(): url for name, url in LAW_URLS.items()}
LIBRARY_PATH = Path(__file__).resolve().parent / "BIBLIOTECA.zip"
DOCUMENTS = (
    ("01_memoria.md", "1. Memoria y antecedentes"),
    ("02_identificacion_estimacion.md", "1.3–1.4. Identificación y estimación"),
    ("03_medidas_gestion.md", "1.5–1.10. Medidas y operaciones de gestión"),
    ("08_presupuesto.md", "1.11. Presupuesto de gestión"),
    ("04_obligaciones.md", "2.1. Obligaciones de los agentes"),
    ("05_prescripciones.md", "2.2. Prescripciones de gestión"),
    ("06_documentacion.md", "2.3. Documentación y trazabilidad"),
    ("07_normativa.md", "2.4. Normativa"),
)
BUDGET_SECTION = """## 1.11. PRESUPUESTO DE GESTIÓN DE RCDs

El presupuesto de gestión de RCDs se encuentra en el capítulo {{capitulo_presupuesto_rcds}} del presupuesto del proyecto.

El presupuesto de gestión de RCDs asciende a {{presupuesto_rcds}}.
"""
BUDGET_FIELDS = {
    "capitulo_presupuesto_rcds": "Capítulo de gestión de RCDs en el presupuesto",
    "presupuesto_rcds": "Importe del capítulo de gestión de RCDs",
}
LEGACY_PREVENTION_TEXT = (
    "Se ajustarán las compras y mediciones, se planificarán los desmontajes y cortes, "
    "se protegerán los acopios frente a deterioros y se estudiarán suministros con "
    "embalajes retornables, sistemas reutilizables y posibilidades de devolución al proveedor.\n\n"
    "Se evitarán mezclas que conviertan fracciones aprovechables en residuos no valorizables. "
    "El personal conocerá las zonas de depósito y las instrucciones de separación.\n\n"
)
DOCUMENT_FIELDS = {
    "prevention": "Prevención y reducción de residuos",
    "separation": "Separación y organización de los medios",
    "reuse": "Reutilización prevista y destino",
    "recovery": "Valorización prevista en la obra",
    "destinations": "Destinos, gestores y operaciones por residuo",
    "facilities": "Instalaciones y planos de gestión",
    "hazardous": "Inspección e inventario de residuos peligrosos",
    "regional": "Normativa autonómica y condicionantes municipales",
}


@dataclass
class StudyTable:
    """Tabla nativa con los datos del cálculo o la identificación del proyecto."""

    caption: str
    headers: list[str]
    rows: list[list[str]]
    ratios: list[float]
    bold_rows: tuple[int, ...] = ()
    right_columns: tuple[int, ...] | None = None
    center_columns: tuple[int, ...] = ()


StudyBlock = tuple[int, str] | StudyTable


def work_summary(app: BudgetApp) -> str | None:
    """Reutiliza el resumen guardado de Seguridad y Salud o, en su defecto, el del PPTP."""
    for data, key in (
        (app.safety_health_state, "resumen_actuacion"),
        (app.pptp_state.get("variables", {}), "bullet_capitulos_obra"),
    ):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def load_documents(archive_path: Path) -> list[tuple[str, str]]:
    """Lee la biblioteca obligatoria sin extraerla ni sustituir documentos ausentes."""
    members = set(library_members(archive_path))
    missing = [name for name, _ in DOCUMENTS if name not in members]
    if missing:
        raise ValueError("Faltan documentos base del estudio RCDs: " + ", ".join(missing))
    documents = []
    for name, _ in DOCUMENTS:
        text = read_markdown(archive_path, name)
        if name == "03_medidas_gestion.md":
            text = text.replace("\r\n", "\n").replace(LEGACY_PREVENTION_TEXT, "")
        if name == "08_presupuesto.md" and re.match(
            r"#\s+3\.\s+PRESUPUESTO", document_markdown(text).lstrip(), re.IGNORECASE,
        ):
            text = BUDGET_SECTION
        documents.append((name, text))
    return documents


def _budget_reference_variables(app: BudgetApp | None, settings: ReportSettings) -> dict[str, str]:
    """Cita el capítulo económico real sin confundirlo con la estimación del modelo."""
    from budget_core import format_chapter_code, format_currency

    values = {key: f"[PENDIENTE: {label}]" for key, label in BUDGET_FIELDS.items()}
    if app is None:
        return values
    chapters = {
        item.chapter for item in app.budget_items if item.source.startswith(RCD_BUDGET_SOURCE)
    }
    if not chapters:
        chapters = {
            item.chapter for item in app.budget_items
            if matches_tool_chapter("rcd", item.chapter, app._chapter_title(item.chapter))
        }
    if not chapters:
        return values
    if len(chapters) != 1:
        values["capitulo_presupuesto_rcds"] = (
            "[PENDIENTE: Hay varios capítulos de gestión de RCDs; identificar el que debe citarse]"
        )
        return values
    chapter = next(iter(chapters))
    total = money(sum(
        (Decimal(str(item.import_total)) for item in app.budget_items if item.chapter == chapter),
        Decimal(0),
    ))
    values["capitulo_presupuesto_rcds"] = format_chapter_code(chapter).removeprefix("CAP. ")
    values["presupuesto_rcds"] = (
        f"{amount_in_words(total, settings.currency, settings.custom_currency)} "
        f"({format_currency(float(total), settings.currency, settings.custom_currency)})"
    )
    return values


def study_variables(state: RcdState, title: str, settings: ReportSettings) -> dict[str, str]:
    """Enlaza los datos generales actuales y las decisiones particulares sin inventar destinos."""
    from budget_core import fmt_number
    from . import MEASURED_NOTE, normalize

    values = {
        key: value if key == "autor" and not settings.show_author
        else value or f"[PENDIENTE EN CONFIGURACIÓN GENERAL: {key}]"
        for key, value in project_document_variables(title, settings).items()
    }
    values.update({
        key: state.documentation.get(key, "").strip()
        or f"[PENDIENTE: {label}]"
        for key, label in DOCUMENT_FIELDS.items()
    })
    if values["regional"].startswith("[PENDIENTE:"):
        values["regional"] = (
            "Se aplicarán las disposiciones autonómicas y las ordenanzas municipales "
            "vigentes en el emplazamiento, junto con los condicionados de licencia."
        )
    values["nota_modelo"] = ""
    values["metodo_estimacion"] = MEASURED_NOTE if state.demolition else (
        "Se aplica el modelo estimativo revisable de superficie afectada, coeficiente de "
        "generación en m³/m², densidad global y composición en peso por grupos. Los valores "
        "utilizados figuran en la tabla de datos de cálculo."
    )
    values["coeficiente_generacion"] = (
        fmt_number(state.inputs["volume_factor"], 4)
        if state.inputs["volume_factor"] is not None else "[PENDIENTE]"
    )
    communities = [municipality.autonomous_community for municipality in settings.municipalities]
    values["normativa_autonomica"] = (
        "Para la parte de la obra situada en Andalucía, Decreto 73/2012, de 20 de marzo, "
        "por el que se aprueba el Reglamento de Residuos de Andalucía, en su redacción "
        "vigente y en lo que resulte aplicable, junto con las disposiciones autonómicas "
        "complementarias y las ordenanzas municipales."
        if any(normalize(community) == "andalucia" for community in communities) else
        "Se comprobarán las disposiciones autonómicas y ordenanzas del emplazamiento "
        "configurado, así como los condicionados de las autorizaciones y de la licencia. "
        "No se aplica automáticamente normativa de otra comunidad autónoma."
    )
    values["aviso_moneda"] = (
        "" if settings.currency == "EUR" else
        "ATENCIÓN: las tarifas y tablas del modelo RCDs están expresadas en euros. "
        "No se realiza conversión de divisas; deben revisarse antes de utilizar el presupuesto."
    )
    return values


def substitute(source: str, values: dict[str, str]) -> str:
    """Resuelve únicamente variables declaradas, sin ejecutar expresiones del documento."""
    def replacement(match: re.Match[str]) -> str:
        """Informa del marcador desconocido en lugar de emitir datos sin resolver."""
        key = match[1].strip()
        if key not in values:
            raise ValueError(f"Variable desconocida en la biblioteca RCDs: {key}")
        return values[key]

    return re.sub(r"\{\{([^{}]+)\}\}", replacement, source)


def _pending_lines(source: str, values: dict[str, str]) -> str:
    """Elimina líneas de datos pendientes y conserva solo los pendientes económicos explícitos."""
    def replacement(match: re.Match[str]) -> str:
        """Retira de las líneas documentales los marcadores sin dato real."""
        key = match[1].strip()
        if key in DOCUMENT_FIELDS and values[key].startswith("[PENDIENTE:"):
            return ""
        if key in BUDGET_FIELDS and values[key].startswith("[PENDIENTE:"):
            return "{{" + key + "}}" + match[2]
        return match[0]

    return re.sub(r"(?m)^[^\r\n{}]*\{\{([^{}\r\n]+)\}\}([^\r\n{}]*)", replacement, source)


def study_tables(
    state: RcdState, title: str, settings: ReportSettings, project_pem: float | None,
    *, excluded_codes: set[str] | None = None,
) -> dict[str, list[StudyTable]]:
    """Reutiliza las filas del cálculo existente sin distribuir artificialmente cantidades por LER."""
    from budget_core import currency_label, fmt_number
    from . import GROUPS, INPUTS, WASTES, budget_calculation, calculation_rows, number

    lines, groups = budget_calculation(state)
    _, details = calculation_rows(state, lines, groups)
    weights = {group.key: group.weight for group in groups}
    values = project_document_variables(title, settings)
    pem = (
        f"{fmt_number(number(project_pem, 'PEM de la obra'))} "
        f"{currency_label(settings.currency, settings.custom_currency)}"
        if project_pem is not None else ""
    )
    project_rows = [
        ["Proyecto", title], ["Emplazamiento", values["emplazamiento"]],
        ["Promotor / entidad", values["entidad"]],
        ["Titulación / cargo", values["cargo"]],
        *([["Autor del proyecto", values["autor"]]] if settings.show_author else []),
        ["PEM de la obra", pem],
        ["Base de cálculo sin gestión de residuos (€)", fmt_number(state.inputs["budget"])],
        *([["Redactor del estudio", values["autor"]]] if settings.show_author else []),
        ["Expediente", values["expediente"]],
    ]
    waste_rows = []
    for key, _, code, description, _ in sorted(WASTES, key=lambda waste: waste[2]):
        if not state.selected[key]:
            continue
        original_evidence = state.evidence.get(key, [])
        evidence_rows = [
            evidence for evidence in original_evidence
            if not excluded_codes or evidence.strip().partition(": ")[0] not in excluded_codes
        ]
        if original_evidence and not evidence_rows:
            continue
        units: dict[str, str] = {}
        for evidence in evidence_rows:
            unit = re.sub(r"\s*\[(?:texto, revisar|BCCA2023 → [^,\]]+, revisar)\]\s*$", "", evidence).strip()
            if unit:
                units.setdefault(unit.removesuffix(" [LER]"), unit)
        for index, unit in enumerate(units.values() or ("Selección manual",)):
            waste_rows.append(["", "", unit] if index else [code, description, unit])
    inputs = [
        [label, fmt_number(value, 4 if key == "volume_factor" else 2)]
        for key, label, _ in INPUTS if (value := state.inputs[key]) is not None
    ]
    group_names = {key: label for key, label, *_ in GROUPS}
    demolition_rows = [
        [row.source, f"{fmt_number(row.quantity, 3)} {row.unit}",
         fmt_number(row.factor, 3), group_names[row.group], fmt_number(float(row.volume))]
        for row in state.demolition if row.included
    ]
    return {
        "tabla_proyecto": [StudyTable("Datos del proyecto", ["Dato", "Descripción"], project_rows, [1.4, 4],
                                      right_columns=(0,))],
        "tabla_residuos": [StudyTable(
            "Residuos seleccionados",
            ["Código LER", "Residuo", "Procedencia"],
            waste_rows or [["—", "No se han seleccionado residuos; revisar el análisis.", "Pendiente"]],
            [1.2, 2.5, 4],
        )],
        "tabla_estimacion": [
            StudyTable("Datos de cálculo y revisión", ["Dato", "Valor"], inputs, [4, 1]),
            StudyTable("Estimación por grupos",
                       ["Grupo", "X", "Peso (t)", "Densidad (t/m³)", "Volumen (m³)"],
                       details, [4, 0.5, 1, 1, 1]),
        ],
        "tabla_demolicion": [StudyTable(
            "Mediciones de demolición copiadas al transporte",
            ["Procedencia", "Medición original", "Factor a m³", "Material", "Volumen (m³)"],
            demolition_rows, [3, 1, 1, 1.5, 1],
        )] if demolition_rows else [],
        "tabla_separacion": [StudyTable(
            "Umbrales del artículo 5.5 del RD 105/2008 (referencia complementaria)",
            ["Fracción", "Umbral (t)", "¿Se supera?"],
            [[label, str(limit).replace(".", ","), "Sí" if weights[key] > limit else "No"]
             for key, label, limit in (
                ("concrete", "Hormigón", 80), ("ceramics", "Ladrillos, tejas y cerámicos", 40),
                ("metals", "Metales", 2), ("wood", "Madera", 1), ("glass", "Vidrio", 1),
                ("plastic", "Plásticos", Decimal("0.5")), ("paper", "Papel y cartón", Decimal("0.5")),
             )], [3, 1, 1], right_columns=(0,), center_columns=(1, 2),
        )],
    }


def study_blocks(
    state: RcdState, title: str, settings: ReportSettings, *, project_pem: float | None = None,
    app: BudgetApp | None = None,
) -> list[StudyBlock]:
    """Intercala memoria y pliego con las tablas del análisis en sus marcadores obligatorios."""
    excluded_codes = {
        item.code for item in app.budget_items if item.source.startswith(RCD_BUDGET_SOURCE)
    } if app is not None else set()
    tables = study_tables(state, title, settings, project_pem, excluded_codes=excluded_codes)
    summary = work_summary(app) if app is not None else None
    links = ler_catalog.match_rows(app.budget_items) if app is not None else []
    values = study_variables(state, title, settings)
    values.update(_budget_reference_variables(app, settings))
    library_path = shared_library_path(
        app, "rcd", LIBRARY_PATH
    ) if app is not None else LIBRARY_PATH
    source = "\n\n".join(
        document_markdown(text).replace("\r\n", "\n")
        for _, text in load_documents(library_path)
    )
    source = source.replace("## 1.2. Descripción de la actuación:", f"## {DESCRIPTION_HEADING}")
    if DESCRIPTION_MARKER not in source:
        # Las bibliotecas anteriores no tienen el epígrafe ni su marcador.
        source = re.sub(r"^(## 1\.)([2-9])(\. )",
                        lambda match: f"{match[1]}{int(match[2]) + 1}{match[3]}", source,
                        flags=re.MULTILINE)
        source = re.sub(r"^([ \t]*\{\{tabla_proyecto\}\}[ \t]*)$",
                        rf"\1\n\n## {DESCRIPTION_HEADING}\n\n{DESCRIPTION_MARKER}", source,
                        flags=re.MULTILINE)
    management_fields = {}
    for field, field_tables in _management_tables(state, excluded_codes).items():
        name = f"tabla_gestion_{field}"
        source = re.sub(
            r"^[^\r\n{}]*\{\{" + field + r"\}\}[^\r\n{}]*$",
            "{{" + name + "}}", source, flags=re.MULTILINE,
        )
        tables[name] = field_tables
        management_fields[name] = field
    prevention = _prevention_blocks(state, values["normativa_autonomica"])
    narrative = {"medidas_prevencion": prevention} if prevention else {}
    if prevention:
        source = re.sub(
            r"^[^\r\n{}]*\{\{prevention\}\}[^\r\n{}]*$",
            "{{medidas_prevencion}}", source, flags=re.MULTILINE,
        )
        management_fields["medidas_prevencion"] = "prevention"
    marker = r"^[ \t]*\{\{(tabla_[a-z_]+|medidas_prevencion)\}\}[ \t]*$"
    counts = Counter(re.findall(marker, source, flags=re.MULTILINE))
    if set(counts) != set(tables) | set(narrative) or any(count != 1 for count in counts.values()):
        raise ValueError("La biblioteca RCDs debe contener una vez cada marcador del estudio.")
    blocks: list[StudyBlock] = []
    for index, part in enumerate(re.split(marker, source, flags=re.MULTILINE)):
        if index % 2:
            blocks.extend(narrative[part] if part in narrative else tables[part])
            if part in management_fields:
                observation = state.documentation.get(management_fields[part], "").strip()
                if observation:
                    blocks.append((0, "Observaciones: " + observation))
            if part == "tabla_residuos" and app is not None:
                if links:
                    blocks.append(StudyTable(
                        "Vínculos orientativos LER–UO", ler_catalog.MATCH_HEADERS,
                        links, [3, 0.9, 3.2, 1.7],
                    ))
                else:
                    blocks.append((0, "No se han encontrado vínculos por código exacto en el presupuesto."))
        else:
            for level, text in text_blocks(_pending_lines(part, values)):
                if level == 0 and text == DESCRIPTION_MARKER:
                    blocks.extend(text_blocks(summary or ""))
                    continue
                text = substitute(text, values)
                if text.strip():
                    blocks.append((level, text))
    return blocks


def _prevention_blocks(state: RcdState, regional_reference: str) -> list[StudyBlock]:
    """Emite las medidas elegidas sin afirmar actuaciones ya ejecutadas ni usos autorizados."""
    if not state.management.prevention:
        return []
    blocks: list[StudyBlock] = [
        (0, "De acuerdo con el artículo 4.1.a), apartado 2.º, del Real Decreto 105/2008 "
            "y con la jerarquía de residuos de la Ley 7/2022, se prevén las siguientes medidas "
            "para la obra, que se concretarán en el Plan de Gestión de Residuos."),
        (0, regional_reference),
    ]
    number = 0
    for title, options in PREVENTION.values():
        measures = [
            (-1, f"• {label}: {text}") for key, (label, text, _general) in options.items()
            if key in state.management.prevention
        ]
        if measures:
            number += 1
            blocks.append((3, f"1.9.{number}. {title}"))
            blocks.extend(measures)
    return blocks


def _management_tables(state: RcdState, excluded_codes: set[str]) -> dict[str, list[StudyTable]]:
    """Convierte decisiones explícitas en tablas y conserva avisos para los datos incompletos."""
    plan = state.management
    result: dict[str, list[StudyTable]] = {}
    fields = {"segregation": "separation", "reuse": "reuse", "recovery": "recovery"}
    titles = {
        "segregation": "Previsión de segregación",
        "reuse": "Medidas de reutilización previstas",
        "recovery": "Operaciones de valorización previstas en la obra",
    }
    for section, options in SELECTIONS.items():
        selected = plan.selections[section]
        if not selected:
            continue
        rows = []
        for key, label in options.items():
            if key not in selected:
                continue
            if key == "other":
                label += " " + (plan.details.get(section, "").strip()
                                 or "[PENDIENTE: Precisar otra operación]")
            if section == "segregation":
                condition = (
                    plan.details.get(section, "").strip()
                    or "[PENDIENTE: Justificación de la fracción mezclada y su tratamiento]"
                ) if key == "mixed" else ""
                rows.append([label, condition])
            elif section == "reuse":
                destination = "No procede" if key == "none" else (
                    plan.reuse_destinations.get(key, "").strip()
                    or "[PENDIENTE: Destino inicial de reutilización]"
                )
                rows.append([label, destination])
            else:
                rows.append([label])
        headers = (["Operación prevista", "Condiciones"] if section == "segregation"
                   else ["Operación prevista", "Destino inicial"] if section == "reuse"
                   else ["Operación prevista"])
        result[fields[section]] = [StudyTable(
            titles[section], headers, rows, [3, 2] if len(headers) == 2 else [1],
        )]
    rows = []
    for metadata in selected_wastes(state, excluded_codes):
        code = metadata[0]
        record = plan.destinations.get(code, {})
        key = record.get("destination", "")
        destination = DESTINATIONS.get(key, f"[PENDIENTE: Destino del residuo {code}]")
        detail = record.get("details", "").strip()
        if detail:
            destination += ": " + detail
        elif key == "other":
            destination += ": [PENDIENTE: Precisar otro destino]"
        facility = record.get("facility", "").strip() or (
            "Propia obra" if key == "onsite" else f"[PENDIENTE: Gestor/instalación para {code}]"
        )
        rows.append([*metadata, destination, facility])
    if rows:
        result["destinations"] = [StudyTable(
            "Destino previsto de los residuos por código LER",
            ["Código LER", "Residuo", "Tipo", "Nivel", "Destino previsto", "Gestor / instalación"],
            rows, [1.1, 2.2, 1.5, 0.8, 2.4, 2],
        )]
    if plan.facilities:
        result.setdefault("destinations", []).append(StudyTable(
            "Centros de gestión de residuos previstos", list(FACILITY_FIELDS.values()),
            [[getattr(facility, key) or f"[PENDIENTE: {label} de {facility.name}]"
              for key, label in FACILITY_FIELDS.items()] for facility in plan.facilities],
            [1.5, 1.3, 1.7, 1.5, 1, 1.7],
        ))
    return result


def _odt_law_links(paragraph: ET.Element, text: str) -> None:
    """Inserta enlaces externos nativos conservando el texto y sus tabulaciones."""
    from odt_reports import _tag

    last = paragraph[-1] if len(paragraph) else None
    for index, part in enumerate(LAW_CITATION.split(text)):
        if index % 2:
            last = ET.SubElement(paragraph, _tag("text:a"), {
                _tag("xlink:href"): LAW_URLS[part.casefold()], _tag("xlink:type"): "simple",
            })
            last.text = part
        elif last is not None:
            last.tail = part
        else:
            paragraph.text = part


def _odt_link_paragraph_spans(paragraph: ET.Element) -> None:
    """Conserva los saltos de línea que aplica el escritor de párrafos ODT."""
    from odt_reports import _tag

    for span in paragraph.findall(_tag("text:span")):
        if span.text and LAW_CITATION.search(span.text):
            _odt_law_links(span, span.text)


def _pdf_law_links(text: str) -> str:
    """Escapa el texto y añade vínculos únicamente a las citas conocidas."""
    return "".join(
        f'<link href="{escape(LAW_URLS[part.casefold()])}" color="#174B83"><u>{escape(part)}</u></link>'
        if index % 2 else escape(part)
        for index, part in enumerate(LAW_CITATION.split(text))
    )


def _table_width(number: int) -> float:
    """Reduce el ancho de las tablas 4 a 6 sin cambiar sus proporciones."""
    return COMPACT_TABLE_WIDTH if 4 <= number <= 6 else 1.0


def _table_alignment(table: StudyTable, number: int) -> tuple[tuple[int, ...] | None, tuple[int, ...]]:
    """Conserva la alineación de los umbrales aunque haya tablas opcionales."""
    if table.center_columns:
        return table.right_columns, table.center_columns
    if number == 5:
        return (0,), tuple(range(1, len(table.headers)))
    if number == 4:
        return (0,), ()
    return table.right_columns, ()


def _justified_list_section(text: str) -> bool:
    """Reconoce los apartados aunque se cambie su numeración o sus mayúsculas."""
    title = re.sub(r"^\d[\d.]*\s+", "", text.strip()).casefold()
    return title in {
        "descripción de la actuación",
        "prescripciones de almacenamiento, manejo y gestión en obra",
        "normativa de referencia",
        "medidas de prevención",
    }


def write_study(
    state: RcdState, title: str, settings: ReportSettings, target: Path, *,
    project_pem: float | None = None, app: BudgetApp | None = None,
) -> None:
    """Emite el mismo estudio completo en el formato indicado por el destino."""
    from . import RcdState

    state = RcdState.from_dict(state.to_dict())
    blocks = study_blocks(state, title, settings, project_pem=project_pem, app=app)
    output_format = report_output_format(target, settings.output_format)
    if output_format == "odt":
        _write_odt(blocks, title, settings, target)
    else:
        _write_pdf(blocks, title, settings, target)


def _write_odt(blocks: list[StudyBlock], title: str, settings: ReportSettings, target: Path) -> None:
    """Conserva el índice, texto y tablas como elementos nativos editables de ODF."""
    from odt_reports import _new_report, _tag

    document = _new_report(
        TITLE, title, settings, repeat_titles=False, footer_title=FOOTER_TITLE,
    )
    if document.template_footer is None:
        document.footer = FOOTER_TITLE
    elif not FOOTER_CHAPTER.search("".join(document.template_footer.itertext())):
        ET.SubElement(document.template_footer, _tag("text:p"), {
            _tag("text:style-name"): "FooterText",
        }).text = "{{capitulo}}"
    add_odt_blue_table_cells(document, "RcdTable")
    add_odt_blue_table_headers(document, "RcdTable", centered=True)
    for style in document.automatic_styles:
        if style.get(_tag("style:name")) in {
            "RcdTableHeader", "RcdTableHeaderRight", "RcdTableHeaderCenter",
        }:
            # El ODM puede definir otro TableHeader y reemplazar la herencia.
            odt_typography(style, {
                "font": "Arial Narrow", "size": ROLES["table_header"][1],
                "bold": True, "italic": False,
            }, _tag)
    add_odt_narrative_styles(document, "Rcd")
    style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "RcdDocumentStart", _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "RcdHeading1",
    })
    ET.SubElement(style, _tag("style:paragraph-properties"), {_tag("fo:break-before"): "page"})
    for level in sorted({block[0] for block in blocks if isinstance(block, tuple) and block[0] < 0}):
        add_odt_list_style(document, f"RcdBullet{-level}", "RcdBody", -level - 1)
        add_odt_list_style(
            document, f"RcdJustifiedBullet{-level}", "RcdBody", -level - 1, justified=True,
        )
    headings = [block for block in blocks if isinstance(block, tuple) and block[0] > 0]
    title_style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "RcdDocumentTitle", _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "ReportTitle",
    })
    ET.SubElement(title_style, _tag("style:paragraph-properties"), {
        _tag("fo:keep-with-next"): "always", _tag("fo:margin-top"): "14pt",
        _tag("fo:margin-bottom"): "5pt",
    })
    document.paragraph(TITLE, "RcdDocumentTitle")
    document.table_of_contents(
        headings, max_level=2, name="RcdContents",
        source_styles={1: ("RcdHeading1", "RcdDocumentStart"), 2: "RcdHeading2"},
    )
    heading_index = 0
    main_seen = False
    justified_section_level = 0
    for block in blocks:
        if isinstance(block, StudyTable):
            document.table_caption(block.caption, include_reference=False)
            _odt_link_paragraph_spans(document.body[-1])
            number = document.data_table_number
            width = _table_width(number)
            right, center = _table_alignment(block, number)
            document.table(block.headers, block.rows, ratios=block.ratios,
                           bold_rows=block.bold_rows, right_columns=right or (),
                           center_columns=center, infer_right_columns=number not in (4, 5),
                           width_fraction=width, centered=width < 1)
            style_odt_blue_table(document.body[-1], "RcdTable")
            document.space(12)
            continue
        level, text = block
        if level > 0:
            if _justified_list_section(text):
                justified_section_level = level
            elif level <= justified_section_level:
                justified_section_level = 0
            style = "RcdDocumentStart" if level == 1 and main_seen else f"RcdHeading{level}"
            document.heading(text, level, style=style, bookmark=f"PPTPHeading{heading_index}")
            heading_index += 1
            main_seen = main_seen or level == 1
        elif level < 0:
            marker, label = split_list_item(text)
            paragraph = ET.SubElement(document.body, _tag("text:p"), {
                _tag("text:style-name"): (
                    f"RcdJustifiedBullet{-level}" if justified_section_level else f"RcdBullet{-level}"
                ),
            })
            paragraph.text = marker
            ET.SubElement(paragraph, _tag("text:tab"))
            _odt_law_links(paragraph, label)
        else:
            document.paragraph(text, "RcdBody")
            _odt_link_paragraph_spans(document.body[-1])
    document.name_text_styles({
        "RcdHeading1": "RCD - Título 1", "RcdHeading2": "RCD - Título 2",
        "RcdDocumentStart": "RCD - Inicio de sección", "RcdBody": "RCD - Cuerpo",
    })
    document.append_signature(settings)
    document.write(target)


def _write_pdf(blocks: list[StudyBlock], title: str, settings: ReportSettings, target: Path) -> None:
    """Calcula el índice real y conserva las tablas paginadas del presupuesto original."""
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, SimpleDocTemplate, Spacer
    from reportlab.platypus.doctemplate import LayoutError
    from tools._shared.pdf_contents import contents_table, contents_title
    from report_layout import LEFT_MARGIN, RIGHT_MARGIN, ReportLayout

    layout = ReportLayout(
        text_styles=settings.text_styles,
        template_path=settings.template_path, expediente=settings.expediente,
        template_title=title, repeat_titles=False, footer_title=FOOTER_TITLE,
    )
    footer_text = "\n".join([layout.template_footer, *(
        cell["text"] for table in layout.template_tables["footer"] for row in table["rows"] for cell in row
    )])
    missing = [] if FOOTER_CHAPTER.search(footer_text) else ["{{capitulo}}"]
    if not re.search(r"\{no_p[aá]gina\}", footer_text):
        missing.append("{{no_pagina}}")
    if missing:
        layout.template_footer = "\n".join(filter(None, (layout.template_footer, " — ".join(missing))))
    body = ParagraphStyle("RcdBody", fontName="Helvetica", fontSize=10,
                          leading=14, spaceAfter=7, alignment=TA_JUSTIFY)
    bullets = {
        level: pdf_list_style(f"RcdBullet{-level}", body, -level - 1)
        for level in sorted({block[0] for block in blocks if isinstance(block, tuple) and block[0] < 0})
    }
    justified_bullets = {
        level: pdf_list_style(f"RcdJustifiedBullet{-level}", body, -level - 1, justified=True)
        for level in bullets
    }
    headings = {}
    for level in range(1, 7):
        _, size, bold = ROLES[f"heading{level}"]
        headings[level] = ParagraphStyle(
            f"RcdHeading{level}", parent=body, fontName="Helvetica-Bold" if bold else "Helvetica",
            fontSize=size, leading=size * 1.28, spaceBefore=12,
            keepWithNext=True, alignment=TA_LEFT,
        )
    title_style = ParagraphStyle("RcdDocumentTitle", parent=headings[1],
                                 fontSize=ROLES["title"][1], leading=ROLES["title"][1] * 1.28)
    contents = contents_table(layout, 2)
    story = [layout.text(TITLE, title_style, role="title"), contents_title(layout),
             contents, PageBreak()]
    heading_index = 0
    main_seen = False
    justified_section_level = 0
    for block in blocks:
        if isinstance(block, StudyTable):
            layout.table_caption(story, block.caption, body=body, include_reference=False)
            if LAW_CITATION.search(block.caption):
                caption = story[-1]
                story[-1] = layout.paragraph(_pdf_law_links(caption.getPlainText()), caption.style)
            number = layout.data_table_number
            width = _table_width(number)
            right, center = _table_alignment(block, number)
            story.extend([
                layout.table(block.headers, block.rows, block.ratios,
                             bold_rows=block.bold_rows, blue_header=True,
                             right_columns=right, center_columns=center,
                             width_fraction=width, centered=width < 1),
                Spacer(1, 12),
            ])
            continue
        level, text = block
        if level > 0:
            if _justified_list_section(text):
                justified_section_level = level
            elif level <= justified_section_level:
                justified_section_level = 0
            if level == 1 and main_seen:
                story.append(PageBreak())
            paragraph = layout.text(text, headings[level])
            paragraph._rcd_heading = (level-1, text, f"RcdHeading{heading_index}")
            heading_index += 1
            main_seen = main_seen or level == 1
            story.append(paragraph)
        elif level < 0:
            marker, label = split_list_item(text)
            style = justified_bullets[level] if justified_section_level else bullets[level]
            story.append(layout.paragraph(_pdf_law_links(label), style, bulletText=marker))
        else:
            story.append(layout.paragraph(_pdf_law_links(text), body))
    story.extend(layout.signature_block(settings))

    class RcdDocument(SimpleDocTemplate):
        """Registra los epígrafes del estudio para resolver su índice multipasada."""

        def afterFlowable(self, flowable):
            """Asocia cada entrada a su página final y a un destino enlazado."""
            entry = getattr(flowable, "_rcd_heading", None)
            if entry is not None:
                level, text, bookmark = entry
                self.canv.bookmarkPage(bookmark)
                if level < 2:
                    self.notify("TOCEntry", (level, layout.index_entry(escape(text)), self.page, bookmark))

    def header(canvas, document):
        """Conserva la cabecera institucional y la numeración continua del estudio."""
        layout.header(canvas, document, TITLE, title)

    document = RcdDocument(
        str(target), pagesize=layout.page_size, leftMargin=LEFT_MARGIN-6, rightMargin=RIGHT_MARGIN-6,
        topMargin=layout.top_margin(TITLE, title), bottomMargin=layout.bottom_margin(),
        title=f"{TITLE} - {title}", author=settings.document_author,
    )
    try:
        document.multiBuild(story, onFirstPage=header, onLaterPages=header)
    except LayoutError as exc:
        raise ValueError(f"No se pudo paginar el estudio RCDs: {exc}") from exc
