"""Memoria, pliego y presupuesto RCDs desde documentos base editables."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path
import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from tools._library_editor import library_members, read_markdown
from tools._library_markdown import document_markdown, text_blocks
from tools._shared.document_layout import project_document_variables
from . import ler_catalog
from collaborative_storage import shared_library_path

if TYPE_CHECKING:
    from bc3_gui import BudgetApp
    from budget_reports import ReportSettings
    from . import RcdState


TITLE = "ESTUDIO DE GESTIÓN DE RESIDUOS DE CONSTRUCCIÓN Y DEMOLICIÓN"
LIBRARY_PATH = Path(__file__).resolve().parent / "BIBLIOTECA.zip"
DOCUMENTS = (
    ("01_memoria.md", "1. Memoria y datos del proyecto"),
    ("02_identificacion_estimacion.md", "1.1–1.2. Identificación y estimación"),
    ("03_medidas_gestion.md", "1.3–1.8. Medidas y operaciones de gestión"),
    ("04_obligaciones.md", "2.1. Obligaciones de los agentes"),
    ("05_prescripciones.md", "2.2. Prescripciones de gestión"),
    ("06_documentacion.md", "2.3. Documentación y trazabilidad"),
    ("07_normativa.md", "2.4. Normativa"),
    ("08_presupuesto.md", "3. Presupuesto de gestión"),
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


StudyBlock = tuple[int, str] | StudyTable


def work_summary(app: BudgetApp) -> tuple[str, str] | None:
    """Reutiliza el resumen guardado de Seguridad y Salud o, en su defecto, el del PPTP."""
    for data, key, label in (
        (app.safety_health_state, "resumen_actuacion", "Seguridad y Salud"),
        (app.pptp_state.get("variables", {}), "bullet_capitulos_obra", "PPTP"),
    ):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), label
    return None


def load_documents(archive_path: Path) -> list[tuple[str, str]]:
    """Lee la biblioteca obligatoria sin extraerla ni sustituir documentos ausentes."""
    members = set(library_members(archive_path))
    missing = [name for name, _ in DOCUMENTS if name not in members]
    if missing:
        raise ValueError("Faltan documentos base del estudio RCDs: " + ", ".join(missing))
    return [(name, read_markdown(archive_path, name)) for name, _ in DOCUMENTS]


def study_variables(state: RcdState, title: str, settings: ReportSettings) -> dict[str, str]:
    """Enlaza los datos generales actuales y las decisiones particulares sin inventar destinos."""
    from budget_core import fmt_number
    from . import MEASURED_NOTE, NOTE, normalize

    values = {
        key: value if key == "autor" and not settings.show_author
        else value or f"[PENDIENTE EN CONFIGURACIÓN GENERAL: {key}]"
        for key, value in project_document_variables(title, settings).items()
    }
    values.update({
        key: state.documentation.get(key, "").strip()
        or f"[PENDIENTE DE CONCRETAR: {label}]"
        for key, label in DOCUMENT_FIELDS.items()
    })
    values["nota_modelo"] = NOTE
    values["metodo_estimacion"] = MEASURED_NOTE if state.demolition else (
        "Se aplica el modelo estimativo revisable de superficie afectada, coeficiente de "
        "generación en m³/m², densidad global y composición en peso por grupos. Los valores "
        "utilizados figuran en la tabla de datos de cálculo; no se sustituyen por los del estudio tipo."
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


def study_tables(
    state: RcdState, title: str, settings: ReportSettings, project_pem: float | None,
) -> dict[str, list[StudyTable]]:
    """Reutiliza las filas del cálculo existente sin distribuir artificialmente cantidades por LER."""
    from budget_core import currency_label, fmt_number
    from . import GROUPS, HEADERS, INPUTS, WASTES, calculate, number

    sections, details = calculate(state)
    values = study_variables(state, title, settings)
    pem = (
        f"{fmt_number(number(project_pem, 'PEM de la obra'))} "
        f"{currency_label(settings.currency, settings.custom_currency)}"
        if project_pem is not None else "[PENDIENTE DE CONFIRMAR EN EL PRESUPUESTO DEL PROYECTO]"
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
    waste_rows = [
        [code, description, "; ".join(state.evidence.get(key, [])) or "Selección manual"]
        for key, _, code, description, _ in WASTES if state.selected[key]
    ]
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
        "tabla_proyecto": [StudyTable("", ["Dato", "Descripción"], project_rows, [1.4, 4])],
        "tabla_residuos": [StudyTable(
            "Residuos seleccionados (revisión manual)",
            ["LER (modelo ODS)", "Residuo", "Procedencia"],
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
            ["Fracción", "Cantidad prevista que supera el umbral (t)"],
            [[label, amount] for label, amount in (
                ("Hormigón", "80"), ("Ladrillos, tejas y cerámicos", "40"),
                ("Metales", "2"), ("Madera", "1"), ("Vidrio", "1"),
                ("Plásticos", "0,5"), ("Papel y cartón", "0,5"),
            )], [3, 2],
        )],
        "tabla_presupuesto": [
            StudyTable(label, list(HEADERS), rows, [40, 15, 17, 14, 14],
                       (0,) if label.startswith("TOTAL") else ())
            for label, rows in sections
        ],
    }


def study_blocks(
    state: RcdState, title: str, settings: ReportSettings, *, project_pem: float | None = None,
    app: BudgetApp | None = None,
) -> list[StudyBlock]:
    """Intercala memoria y pliego con las tablas del análisis en sus marcadores obligatorios."""
    tables = study_tables(state, title, settings, project_pem)
    summary = work_summary(app) if app is not None else None
    links = ler_catalog.match_rows(app.budget_items) if app is not None else []
    values = study_variables(state, title, settings)
    library_path = shared_library_path(
        app, "rcd", LIBRARY_PATH
    ) if app is not None else LIBRARY_PATH
    source = "\n\n".join(
        document_markdown(text).replace("\r\n", "\n")
        for _, text in load_documents(library_path)
    )
    marker = r"^[ \t]*\{\{(tabla_[a-z_]+)\}\}[ \t]*$"
    counts = Counter(re.findall(marker, source, flags=re.MULTILINE))
    if set(counts) != set(tables) or any(count != 1 for count in counts.values()):
        raise ValueError("La biblioteca RCDs debe contener una vez cada marcador de las tablas del estudio.")
    blocks: list[StudyBlock] = []
    for index, part in enumerate(re.split(marker, source, flags=re.MULTILINE)):
        if index % 2:
            blocks.extend(tables[part])
            if part == "tabla_proyecto" and summary is not None:
                text, model = summary
                blocks.append((0, f"Descripción de la actuación (según {model}):"))
                for line in text.splitlines():
                    if line.strip():
                        blocks.append((0, line.strip()))
            if part == "tabla_residuos" and app is not None:
                blocks.append((0, ler_catalog.MATCH_NOTE))
                if links:
                    blocks.append(StudyTable(
                        "Vínculos orientativos LER–UO (BCCA2023)", ler_catalog.MATCH_HEADERS,
                        links, [3, 0.9, 3.2, 1.7],
                    ))
                else:
                    blocks.append((0, "No se han encontrado vínculos por código exacto en el presupuesto."))
        else:
            for level, text in text_blocks(part):
                text = substitute(text, values)
                if text.strip():
                    blocks.append((level, text))
    return blocks


def write_study(
    state: RcdState, title: str, settings: ReportSettings, target: Path, *,
    project_pem: float | None = None, app: BudgetApp | None = None,
) -> None:
    """Emite el mismo estudio completo en el formato de la configuración general."""
    from . import RcdState

    state = RcdState.from_dict(state.to_dict())
    blocks = study_blocks(state, title, settings, project_pem=project_pem, app=app)
    if settings.output_format == "odt":
        _write_odt(blocks, title, settings, target)
    elif settings.output_format == "pdf":
        _write_pdf(blocks, title, settings, target)
    else:
        raise ValueError(f"Formato de estudio RCDs no admitido: {settings.output_format}")


def _write_odt(blocks: list[StudyBlock], title: str, settings: ReportSettings, target: Path) -> None:
    """Conserva el índice, texto y tablas como elementos nativos editables de ODF."""
    from odt_reports import _new_report, _tag

    document = _new_report(
        TITLE, title, settings, repeat_titles=False, footer_title="Estudio de gestión de RCDs",
    )
    for level in range(1, 7):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): f"RcdHeading{level}", _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ReportBody",
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), {
            _tag("fo:keep-with-next"): "always", _tag("fo:margin-top"): "12pt",
            _tag("fo:margin-bottom"): "6pt",
        })
        ET.SubElement(style, _tag("style:text-properties"), {
            _tag("fo:font-weight"): "bold", _tag("fo:font-size"): f"{max(10, 15-level)}pt",
        })
    for name, parent, properties in (
        ("RcdDocumentStart", "RcdHeading1", {_tag("fo:break-before"): "page"}),
        ("RcdTableCaption", "ReportBody", {_tag("fo:keep-with-next"): "always"}),
        ("RcdBullet", "ReportBody", {_tag("fo:margin-left"): "12pt",
                                    _tag("fo:text-indent"): "-7pt"}),
    ):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): parent,
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), properties)
        if name == "RcdTableCaption":
            ET.SubElement(style, _tag("style:text-properties"), {_tag("fo:font-weight"): "bold"})
    headings = [block for block in blocks if isinstance(block, tuple) and block[0] > 0]
    document.paragraph(TITLE, "RcdHeading1")
    document.table_of_contents(headings, max_level=2, name="RcdContents")
    heading_index = 0
    main_seen = False
    for block in blocks:
        if isinstance(block, StudyTable):
            if block.caption:
                document.paragraph(block.caption, "RcdTableCaption")
            document.table(block.headers, block.rows, ratios=block.ratios, bold_rows=block.bold_rows)
            document.space(12)
            continue
        level, text = block
        if level > 0:
            style = "RcdDocumentStart" if level == 1 and main_seen else f"RcdHeading{level}"
            document.heading(text, level, style=style, bookmark=f"PPTPHeading{heading_index}")
            heading_index += 1
            main_seen = main_seen or level == 1
        else:
            document.paragraph(text, "RcdBullet" if level < 0 else "ReportBody")
    document.append_signature(settings)
    document.write(target)


def _write_pdf(blocks: list[StudyBlock], title: str, settings: ReportSettings, target: Path) -> None:
    """Calcula el índice real y conserva las tablas paginadas del presupuesto original."""
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.platypus.doctemplate import LayoutError
    from reportlab.platypus.tableofcontents import TableOfContents
    from report_layout import LEFT_MARGIN, RIGHT_MARGIN, ReportLayout

    layout = ReportLayout(
        template_path=settings.template_path, expediente=settings.expediente,
        template_title=title, repeat_titles=False, footer_title="Estudio de gestión de RCDs",
    )
    body = ParagraphStyle("RcdBody", fontName="Helvetica", fontSize=10,
                          leading=14, spaceAfter=7, alignment=TA_JUSTIFY)
    bullet = ParagraphStyle("RcdBullet", parent=body, leftIndent=12, firstLineIndent=-7)
    caption = ParagraphStyle("RcdTableCaption", parent=layout.bold, keepWithNext=True)
    headings = {
        level: ParagraphStyle(
            f"RcdHeading{level}", parent=body, fontName="Helvetica-Bold",
            fontSize=max(10, 15-level), leading=18, spaceBefore=12,
            keepWithNext=True, alignment=TA_LEFT,
        ) for level in range(1, 7)
    }
    contents = TableOfContents()
    contents.levelStyles = [
        ParagraphStyle(f"RcdContents{level}", parent=body, leftIndent=12*level, alignment=TA_LEFT)
        for level in range(2)
    ]
    story = [Paragraph(TITLE, headings[1]), Paragraph("ÍNDICE", headings[2]), contents, PageBreak()]
    heading_index = 0
    main_seen = False
    for block in blocks:
        if isinstance(block, StudyTable):
            if block.caption:
                story.append(layout.text(block.caption, caption))
            story.extend([
                layout.table(block.headers, block.rows, block.ratios, bold_rows=block.bold_rows),
                Spacer(1, 12),
            ])
            continue
        level, text = block
        if level > 0:
            if level == 1 and main_seen:
                story.append(PageBreak())
            paragraph = Paragraph(escape(text), headings[level])
            paragraph._rcd_heading = (level-1, text, f"RcdHeading{heading_index}")
            heading_index += 1
            main_seen = main_seen or level == 1
            story.append(paragraph)
        else:
            story.append(Paragraph(escape(text).replace("\n", "<br/>"), bullet if level < 0 else body))
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
                    self.notify("TOCEntry", (level, text, self.page, bookmark))

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
