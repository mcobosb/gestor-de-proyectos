"""Composición del anejo de justificación sin duplicar los cálculos presupuestarios."""

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import tempfile
from xml.etree import ElementTree as ET
from .templates import (
    FORMULA_LEVEL, LIBRARY_PATH, document_blocks, formula_runs, load_documents, template_variables,
)


TITLE = "Justificación de precios"
PENDING_VALUE = "PENDIENTE DE DEFINIR Y VERIFICAR"
CONSTRUCTION_AGREEMENT_REFERENCES = (
    "VII Convenio Colectivo General del Sector de la Construcción "
    "(código 99005585011900); modificación BOE-A-2025-25234, publicada el 10/12/2025.",
)
SALARY_REVISION_REFERENCES = (
    "2022 — actualización del 3,00 % de las tablas de 2021 con efectos de 01/01/2022 "
    "(art. 55.1, BOE-A-2025-25234).",
    "2023 — actualización del 3,00 % de las tablas vigentes en 2022 con efectos de 01/01/2023 "
    "(art. 55.1, BOE-A-2025-25234).",
    "2024 — actualización del 2,75 % de las tablas vigentes en 2023 con efectos de 01/01/2024 "
    "(art. 55.1, BOE-A-2025-25234).",
    "2025 — garantía salarial del 0,40 % sobre las tablas de 2024 y actualización del 3,50 % "
    "con efectos de 01/01/2025 (art. 55.5, BOE-A-2025-25234).",
    "2026 — actualización del 3,00 % de las tablas vigentes en 2025 con efectos de 01/01/2026 "
    "(art. 55.5, BOE-A-2025-25234).",
    "Garantía 2022–2024 — si la suma de IPC supera el 10,00 % (máximo 13,00 %), el exceso "
    "se reparte entre tablas salariales y plan de pensiones con efectos de 01/01/2025 "
    "(art. 55.4, BOE-A-2025-25234).",
    "Garantía 2025–2026 — si la suma de IPC supera el 7,25 % (máximo 9,25 %), el exceso "
    "se reparte entre tablas salariales y plan de pensiones con efectos de 01/01/2027 "
    "(art. 55.7, BOE-A-2025-25234).",
)
WAGE_REFERENCES = (
    "2022 — nivel XII: 17.919,57 €/año; mínimo bruto anual de convenio, no es el SMI "
    "(art. 53, BOE-A-2025-25234).",
    "2023 — nivel XII: 18.457,16 €/año; mínimo bruto anual de convenio, no es el SMI "
    "(art. 53, BOE-A-2025-25234).",
    "2024 — nivel XII: 18.964,73 €/año; mínimo bruto anual de convenio, no es el SMI "
    "(art. 53, BOE-A-2025-25234).",
    "2025 — nivel XII: 19.707,01 €/año; mínimo bruto anual de convenio, no es el SMI "
    "(art. 53, BOE-A-2025-25234).",
    "2026 — nivel XII: 20.298,22 €/año; mínimo bruto anual de convenio, no es el SMI "
    "(art. 53, BOE-A-2025-25234).",
)
PENSION_CONTRIBUTION_REFERENCES = (
    "2022 — 1,00 % de los conceptos salariales de las tablas de 2021 "
    "(art. 56, BOE-A-2025-25234).",
    "2023 — 1,00 % de los conceptos salariales de las tablas de 2021 "
    "(art. 56, BOE-A-2025-25234).",
    "2024 — 1,00 % de las tablas de 2021 más 0,25 % de las tablas de 2023 "
    "(art. 56, BOE-A-2025-25234).",
    "2025 — 1,00 % de las tablas de 2021, 0,25 % de las de 2023, 0,40 % de las de 2024 "
    "por garantía salarial y 0,50 % de las de 2024 actualizadas "
    "(art. 56, BOE-A-2025-25234).",
    "2026 — aportaciones de 2025 más 0,25 % de los conceptos salariales de las tablas de 2025 "
    "(art. 56, BOE-A-2025-25234).",
)
ANNUAL_HOURS_REFERENCES = ("1736",)
CALENDAR_REFERENCES = (
    "Calendario 2026: 12 festivos nacionales/autonómicos y 2 locales; los días retribuidos "
    "de ajuste (habitualmente 4–6) se fijan en el calendario provincial publicado en el BOP. "
    "Compensar con descanso si se trabaja en esos días para no superar la jornada anual.",
    "Ejemplo Córdoba 2026: días de ajuste 5 de enero, 29 de mayo, 24 y 31 de diciembre; "
    "confirmar con el BOP y el calendario local antes de aplicarlo.",
)
WORK_CONDITION_REFERENCES = (
    "Vacaciones: 30 días naturales (22 laborables/176 horas), con al menos 21 días naturales "
    "ininterrumpidos. Horas extraordinarias: máximo 80 al año y voluntarias salvo fuerza mayor. "
    "Con alertas AEMET naranja o roja por temperaturas extremas, adaptar horarios/exposición "
    "sin merma salarial.",
)
CONTRIBUTION_BASE_REFERENCES = (
    "2026 — base máxima: 5.101,20 €/mes (170,04 €/día). Mínimas mensuales: grupo 1, "
    "1.989,30 €; grupo 2, 1.649,70 €; grupo 3, 1.435,20 €; grupos 4–7, 1.424,40 €. "
    "Obra, grupos 8–10: 47,48 €/día (1.424,40 € en mes de 30 días).",
)
CONTRIBUTION_RATE_REFERENCES = (
    "2026 — obra, CNAE 41/42/43, contrato indefinido: empresa/trabajador — contingencias "
    "comunes 23,60/4,70 %; AT/EP 6,70/0 % (3,35 % IT + 3,35 % IMS); desempleo 5,50/1,55 %; "
    "MEI 0,75/0,15 %; formación 0,60/0,10 %; FOGASA 0,20/0 %. Totales: 37,35/6,50 %.",
    "2026 — personal administrativo exclusivamente de oficina: AT/EP empresarial 1,50 % "
    "(0,80 % IT + 0,70 % IMS); verificar el resto de tipos según contrato y actividad.",
)
CONTRIBUTION_DETAIL_REFERENCES = (
    "Cotizan salario base, plus de asistencia, prorrata de pagas extra y pluses extrasalariales "
    "de transporte/ropa. Dietas y locomoción exentas dentro de límites legales; referencia "
    "facilitada: 0,26 €/km. Las aportaciones al plan de pensiones están exentas de IRPF dentro "
    "del límite legal, integran la base de cotización y pueden dar derecho a reducción empresarial "
    "por contingencias comunes hasta el límite legal.",
)
FIELDS = {
    "reference_date": "",
    "contract_type": "public",
    "price_source": "Precios y descomposiciones del presupuesto del proyecto.",
    "legislation_notes": "",
    "composition_notes": "",
    "indirect_staff": "",
    "indirect_installations": "",
    "indirect_technical": "",
    "indirect_other": "",
    "agreement": CONSTRUCTION_AGREEMENT_REFERENCES[0],
    "salary_revision": SALARY_REVISION_REFERENCES[4],
    "wage_reference": WAGE_REFERENCES[-1],
    "contribution_bases": CONTRIBUTION_BASE_REFERENCES[0],
    "contributions": CONTRIBUTION_RATE_REFERENCES[0],
    "contribution_details": CONTRIBUTION_DETAIL_REFERENCES[0],
    "pension_contribution": PENSION_CONTRIBUTION_REFERENCES[-1],
    "calendar": CALENDAR_REFERENCES[0],
    "work_conditions": WORK_CONDITION_REFERENCES[0],
    "annual_hours": ANNUAL_HOURS_REFERENCES[0],
    "labor_notes": "",
    "materials_notes": "",
    "machinery_method": "budget",
    "machinery_parameters": "",
    "machinery_consumption": "",
    "machinery_notes": "",
}
LISTINGS = (
    ("3. LISTADO DE PRECIOS ELEMENTALES", "Precios elementales"),
    ("4. LISTADO DE PRECIOS AUXILIARES", "Precios Auxiliares"),
    ("5. LISTADO DE PRECIOS DESCOMPUESTOS", "Descompuestos"),
)


def restore_state(data) -> dict:
    """Valida los datos persistidos sin incorporar coeficientes o importes de ejemplo."""
    if not isinstance(data, dict):
        raise ValueError("Los datos de justificación de precios deben ser un objeto.")
    if not data:
        return {}
    state = FIELDS | data
    state.pop("territory", None)
    for key in FIELDS:
        if not isinstance(state[key], str):
            raise ValueError(f"El campo {key} de justificación de precios debe ser texto.")
        state[key] = state[key].strip()
    if state["contract_type"] not in {"public", "private"}:
        raise ValueError("El régimen de contratación no es válido.")
    if state["machinery_method"] not in {"budget", "analytical"}:
        raise ValueError("El método de valoración de maquinaria no es válido.")
    return state


def validate_state(data) -> dict:
    """Comprueba los datos finalizados, permitiendo restaurar ediciones intermedias al deshacer."""
    state = restore_state(data)
    if not state:
        return state
    if state["reference_date"]:
        try:
            if date.fromisoformat(state["reference_date"]).isoformat() != state["reference_date"]:
                raise ValueError("La fecha no está en formato ISO extendido.")
        except ValueError as exc:
            raise ValueError("La fecha de referencia debe tener formato AAAA-MM-DD.") from exc
    if state["annual_hours"] and state["annual_hours"] != PENDING_VALUE:
        try:
            hours = Decimal(state["annual_hours"].replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError("La jornada anual debe ser un número de horas positivo.") from exc
        if not hours.is_finite() or hours <= 0:
            raise ValueError("La jornada anual debe ser un número de horas positivo.")
    return state


def narrative(app, data: dict) -> list[tuple[int, str]]:
    """Compone los documentos base de la biblioteca con los datos actuales del proyecto."""
    state = FIELDS | validate_state(data)
    values = template_variables(app, state)
    return [
        block for _, source in load_documents(LIBRARY_PATH)
        for block in document_blocks(source, values)
    ]


def write_document(app, target: Path, data: dict) -> None:
    """Exporta un único anejo en la plantilla y formato seleccionados en el proyecto."""
    blocks = narrative(app, data)
    output_format = app.report_settings.output_format
    if output_format == "odt":
        writer = _write_odt
    elif output_format == "pdf":
        writer = _write_pdf
    else:
        raise ValueError(f"Formato de informe no admitido: {output_format}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=f".{output_format}", delete=False,
        ) as handle:
            temporary = Path(handle.name)
        writer(app, temporary, blocks)
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _write_odt(app, target: Path, blocks: list[tuple[int, str]]) -> None:
    """Conserva texto, índice y listados como elementos nativos editables de ODF."""
    from odt_reports import _new_report, _tag, append_report_odt

    document = _new_report(
        TITLE.upper(), app._project_title(), app.report_settings,
        footer_title=TITLE, template_title=TITLE, repeat_titles=False,
    )
    for level in range(1, 7):
        name = f"PricesHeading{level}"
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ReportBody",
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), {
            _tag("fo:keep-with-next"): "always",
            _tag("fo:margin-top"): "12pt", _tag("fo:margin-bottom"): "6pt",
        })
        ET.SubElement(style, _tag("style:text-properties"), {
            _tag("fo:font-weight"): "bold", _tag("fo:font-size"): f"{15 - level}pt",
        })
    for name, position in (("PricesSubscript", "sub"), ("PricesSuperscript", "super")):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "text",
        })
        ET.SubElement(style, _tag("style:text-properties"), {
            _tag("style:text-position"): f"{position} 75%",
        })
    for name, properties in (
        ("PricesFormula", {_tag("fo:text-align"): "center", _tag("fo:margin-top"): "6pt",
                           _tag("fo:margin-bottom"): "9pt"}),
        ("PricesBullet", {_tag("fo:margin-left"): "12pt", _tag("fo:text-indent"): "-7pt"}),
    ):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ReportBody",
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), properties)
    style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "PricesListingHeading", _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "PricesHeading1",
    })
    ET.SubElement(style, _tag("style:paragraph-properties"), {
        _tag("fo:break-before"): "page", _tag("fo:keep-with-next"): "always",
    })
    headings = [(level, text) for level, text in blocks if level > 0]
    headings.extend((1, title) for title, _ in LISTINGS)
    document.paragraph(TITLE.upper(), style="PricesHeading1")
    document.table_of_contents(headings, max_level=3, name="PricesContents")
    bookmark = 0
    for level, text in blocks:
        if level > 0:
            document.heading(text, level, style=f"PricesHeading{level}", bookmark=f"PPTPHeading{bookmark}")
            bookmark += 1
        elif level == FORMULA_LEVEL:
            paragraph = ET.SubElement(document.body, _tag("text:p"), {
                _tag("text:style-name"): "PricesFormula",
            })
            for value, position in formula_runs(text):
                attributes = {}
                if position:
                    attributes[_tag("text:style-name")] = (
                        "PricesSubscript" if position == "sub" else "PricesSuperscript"
                    )
                ET.SubElement(paragraph, _tag("text:span"), attributes).text = value
        else:
            document.paragraph(text, "PricesBullet" if level < 0 else "ReportBody")
    for title, report in LISTINGS:
        document.heading(title, 1, style="PricesListingHeading", bookmark=f"PPTPHeading{bookmark}")
        bookmark += 1
        append_report_odt(app, report, document, embedded=True)
    settings = app.report_settings
    signature = [value for value in (
        settings.location, settings.role, settings.author, settings.organization, settings.signature,
    ) if value]
    if signature:
        document.space(12)
        for value in signature:
            document.paragraph(value, "SignatureNote" if value == settings.signature else "Signature")
    document.write(target)


def _write_pdf(app, target: Path, blocks: list[tuple[int, str]]) -> None:
    """Pagina el anejo con índice real y tablas del generador presupuestario común."""
    from html import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.platypus.doctemplate import LayoutError
    from reportlab.platypus.tableofcontents import TableOfContents
    from budget_reports import build_pdf_report
    from report_layout import LEFT_MARGIN, RIGHT_MARGIN, ReportLayout

    settings = app.report_settings
    layout = ReportLayout(
        template_path=settings.template_path, expediente=settings.expediente,
        template_title=TITLE, footer_title=TITLE, repeat_titles=False,
    )
    body = ParagraphStyle(
        "PricesBody", fontName="Helvetica", fontSize=10, leading=14,
        spaceAfter=7, alignment=TA_JUSTIFY,
    )
    formula_style = ParagraphStyle(
        "PricesFormula", parent=body, fontName="Times-Roman", fontSize=10,
        alignment=TA_CENTER, spaceBefore=6, spaceAfter=9,
    )
    bullet_style = ParagraphStyle("PricesBullet", parent=body, leftIndent=12, firstLineIndent=-7)
    heading_styles = {
        level: ParagraphStyle(
            f"PricesHeading{level}", parent=body, fontName="Helvetica-Bold",
            fontSize=15 - level, leading=18 - level, spaceBefore=12,
            spaceAfter=7, keepWithNext=True,
        ) for level in range(1, 7)
    }
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            f"PricesContents{level}", parent=body, leftIndent=12 * level,
            fontSize=10, textColor=colors.black, spaceBefore=4,
        ) for level in range(3)
    ]
    story = [
        Paragraph(escape(TITLE.upper()), heading_styles[1]), Spacer(1, 12),
        Paragraph("ÍNDICE", heading_styles[2]), toc, PageBreak(),
    ]
    heading_index = 0
    reports = dict(LISTINGS)
    for level, text in blocks + [(1, title) for title, _ in LISTINGS]:
        if level > 0:
            if text in reports:
                story.append(PageBreak())
            paragraph = Paragraph(escape(text), heading_styles[level])
            paragraph._prices_heading = (level - 1, text, f"PricesHeading{heading_index}")
            heading_index += 1
            story.append(paragraph)
            report = reports.get(text)
            if report:
                listing, _ = build_pdf_report(app, report, layout, embedded=True)
                story.extend(listing)
        elif level == FORMULA_LEVEL:
            formula = "".join(
                f"<{position}>{escape(value)}</{position}>" if position else escape(value)
                for value, position in formula_runs(text)
            )
            story.append(Paragraph(formula, formula_style))
        else:
            story.append(Paragraph(
                escape(text).replace("\n", "<br/>"), bullet_style if level < 0 else body,
            ))
    signature = [value for value in (
        settings.location, settings.role, settings.author, settings.organization, settings.signature,
    ) if value]
    if signature:
        story.append(Spacer(1, 12))
        story.extend(Paragraph(escape(value).replace("\n", "<br/>"), body) for value in signature)

    class PricesDocument(SimpleDocTemplate):
        """Resuelve los destinos del índice durante las pasadas de maquetación."""

        def afterFlowable(self, flowable):
            """Registra exclusivamente los epígrafes del anejo, no los títulos presupuestarios."""
            entry = getattr(flowable, "_prices_heading", None)
            if entry is not None:
                level, text, bookmark = entry
                self.canv.bookmarkPage(bookmark)
                self.notify("TOCEntry", (level, text, self.page, bookmark))

    def page_header(canvas, document):
        """Dibuja la plantilla común sin cabeceras presupuestarias en la parte narrativa."""
        layout.header(canvas, document, TITLE.upper(), app._project_title())

    document = PricesDocument(
        str(target), pagesize=A4, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN,
        topMargin=layout.top_margin(TITLE.upper(), app._project_title()),
        bottomMargin=layout.bottom_margin(),
        title=TITLE, author=settings.author,
    )
    try:
        document.multiBuild(story, onFirstPage=page_header, onLaterPages=page_header)
    except LayoutError as exc:
        raise ValueError(f"No se pudo paginar la justificación de precios: {exc}") from exc
