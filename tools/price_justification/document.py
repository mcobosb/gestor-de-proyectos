"""Composición del anejo de justificación sin duplicar los cálculos presupuestarios."""

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET


TITLE = "Justificación de precios"
FIELDS = {
    "reference_date": "",
    "territory": "",
    "contract_type": "public",
    "price_source": "Precios y descomposiciones del presupuesto del proyecto.",
    "legislation_notes": "",
    "composition_notes": "",
    "indirect_staff": "",
    "indirect_installations": "",
    "indirect_technical": "",
    "indirect_other": "",
    "agreement": "",
    "wage_reference": "",
    "contributions": "",
    "calendar": "",
    "annual_hours": "",
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
    for key in FIELDS:
        if not isinstance(state[key], str):
            raise ValueError(f"El campo {key} de justificación de precios debe ser texto.")
        state[key] = state[key].strip()
    if state["contract_type"] not in {"public", "private"}:
        raise ValueError("El régimen de contratación no es válido.")
    if state["machinery_method"] not in {"budget", "analytical"}:
        raise ValueError("El método de valoración de maquinaria no es válido.")
    if state["reference_date"]:
        try:
            date.fromisoformat(state["reference_date"])
        except ValueError as exc:
            raise ValueError("La fecha de referencia debe tener formato AAAA-MM-DD.") from exc
    if state["annual_hours"]:
        try:
            hours = Decimal(state["annual_hours"].replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError("La jornada anual debe ser un número de horas positivo.") from exc
        if not hours.is_finite() or hours <= 0:
            raise ValueError("La jornada anual debe ser un número de horas positivo.")
    return state


def _pending(value: str) -> str:
    """Identifica expresamente los datos que todavía no ha aportado el redactor."""
    return value or "[PENDIENTE DE DEFINIR Y VERIFICAR]"


def narrative(app, data: dict) -> list[tuple[int, str]]:
    """Compone los apartados técnicos con los datos actuales y sin cálculos de fórmulas."""
    from bc3_gui import fmt_number

    state = FIELDS | restore_state(data)
    public = state["contract_type"] == "public"
    blocks = [
        (1, "1. LEGISLACIÓN VIGENTE"),
        (0, f"Fecha de referencia: {_pending(state['reference_date'])}. "
            f"Ámbito territorial: {_pending(state['territory'])}."),
        (0, "La determinación de los precios debe atender a la normativa y a las condiciones "
            "laborales aplicables a la obra en su fecha y ámbito territorial. Las referencias "
            "y los datos económicos se revisarán antes de aprobar el proyecto."),
    ]
    if public:
        blocks.extend([
            (0, "Ley 9/2017, de 8 de noviembre, de Contratos del Sector Público, y Reglamento "
                "general de la Ley de Contratos de las Administraciones Públicas, aprobado "
                "por Real Decreto 1098/2001, de 12 de octubre, en lo que resulte vigente y aplicable. "
                "Para la composición de los precios se toma como referencia su artículo 130."),
        ])
    else:
        blocks.append((0, "En contratación privada se atenderá al contrato y a la normativa "
                           "aplicable. Los criterios de composición de costes se describen "
                           "como criterios técnicos, no como imposiciones generales de la contratación pública."))
    blocks.extend([
        (0, "Ley 31/1995, de 8 de noviembre, de Prevención de Riesgos Laborales, y Real "
            "Decreto 1627/1997, de 24 de octubre, en los aspectos que afecten a los trabajos y sus costes."),
        (0, "Se comprobarán el convenio colectivo y sus revisiones salariales, el salario mínimo "
            "interprofesional, las reglas de cotización y el calendario laboral aplicables. "
            "No se presuponen salarios, cotizaciones ni calendarios de ejercicios anteriores."),
        (0, state["legislation_notes"]),
        (1, "2. COMPOSICIÓN DE LOS PRECIOS"),
        (0, f"Fuente de precios: {_pending(state['price_source'])}."),
        (0, "El precio de ejecución material comprende los costes directos de mano de obra, "
            "materiales a pie de obra y utilización de maquinaria, junto con los costes indirectos "
            "que correspondan. No incluye IVA, gastos generales de empresa ni beneficio industrial."),
        (0, "Los listados finales se obtienen del presupuesto actual, conservando sus rendimientos, "
            "precios y criterios de redondeo. La presente herramienta no modifica el presupuesto."),
        (0, state["composition_notes"]),
        (2, "2.1. COSTES INDIRECTOS"),
        (0, f"Porcentaje configurado en los informes del proyecto: "
            f"{fmt_number(app.report_settings.indirect_costs)} %. "
            "Los descompuestos muestran los importes efectivos de cada partida; este anejo no añade un segundo recargo."),
        (0, "Su justificación atenderá a la naturaleza, presupuesto y plazo de la obra. No se "
            "duplicarán gastos ya valorados en unidades de obra o partidas específicas. "
            "No se adopta un límite universal ni un porcentaje fijo de imprevistos."),
        (0, f"Personal técnico y administrativo adscrito a la obra:\n{_pending(state['indirect_staff'])}"),
        (0, f"Instalaciones provisionales, oficinas, almacenes y talleres:\n{_pending(state['indirect_installations'])}"),
        (0, f"Trabajos técnicos y controles exigidos por el contrato:\n{_pending(state['indirect_technical'])}"),
        (0, f"Otros costes y criterio de adopción del porcentaje:\n{_pending(state['indirect_other'])}"),
        (0, "Los datos anteriores son documentación de apoyo. No se ha calculado automáticamente "
            "un coeficiente de costes indirectos a partir de ellos."),
        (2, "2.2. MANO DE OBRA"),
        (0, "El coste horario debe considerar las retribuciones sujetas a cotización, los "
            "conceptos no salariales que procedan, las cargas empresariales y las horas anuales "
            "efectivas de trabajo, evitando duplicidades entre conceptos."),
        (0, f"Convenio y revisión salarial: {_pending(state['agreement'])}."),
        (0, f"Referencia salarial / SMI: {_pending(state['wage_reference'])}."),
        (0, f"Cotizaciones y contingencias profesionales: {_pending(state['contributions'])}."),
        (0, f"Calendario laboral: {_pending(state['calendar'])}."),
        (0, f"Jornada anual de referencia: {state['annual_hours'] + ' h' if state['annual_hours'] else _pending('')}."),
        (0, state["labor_notes"]),
        (2, "2.3. MATERIAL A PIE DE OBRA"),
        (0, "El precio a pie de obra integra la adquisición y, cuando no estén ya incluidos, "
            "la carga, el transporte y la descarga necesarios. Se identificarán las fuentes, "
            "distancias y condiciones de suministro, sin duplicar costes recogidos en otros componentes."),
        (0, _pending(state["materials_notes"])),
        (2, "2.4. MAQUINARIA"),
        (0, "Se distinguirán los costes de disponibilidad y propiedad de los costes de funcionamiento "
            "y de los medios complementarios. Se comprobará si el precio incorpora operario, "
            "combustible, energía, mantenimiento y transporte para evitar duplicidades."),
        (3, "2.4.1. RESUMEN DEL MÉTODO DE CÁLCULO"),
        (0, "Criterio seleccionado: " + (
            "precios horarios de la fuente presupuestaria. No se presupone que se hayan obtenido "
            "mediante un método analítico concreto."
            if state["machinery_method"] == "budget" else
            "documentación de una valoración analítica. Sus parámetros y resultados deben "
            "ser aportados y contrastados por el redactor; las fórmulas no están implementadas."
        )),
        (0, "Si se adopta el método MOPU de 1976, se identificará como referencia técnica histórica "
            "y se justificarán sus parámetros y actualizaciones; no constituye por sí solo "
            "una obligación normativa ni acredita un cálculo realizado."),
        (0, "Parámetros documentales: valor de reposición, vida útil, horas totales y anuales, "
            "días de disponibilidad, interés, seguros, amortización, reparaciones y conservación. "
            "No se asignan valores automáticos."),
        (0, state["machinery_parameters"]),
        (3, "2.4.2. ESTRUCTURA DEL COSTE"),
        (0, "El coste intrínseco comprende la reposición del capital, los intereses, seguros, "
            "otros gastos fijos y las reparaciones y conservación que procedan. El coste "
            "complementario comprende el personal y los consumos no incluidos previamente."),
        (0, "Los consumos principales y secundarios se documentarán para el equipo y las "
            "condiciones reales de utilización. No se adoptan ratios universales de combustible o lubricantes."),
        (0, _pending(state["machinery_consumption"])),
        (0, state["machinery_notes"]),
    ])
    return [(level, text) for level, text in blocks if text.strip()]


def write_document(app, target: Path, data: dict) -> None:
    """Exporta un único anejo en la plantilla y formato seleccionados en el proyecto."""
    blocks = narrative(app, data)
    output_format = app.report_settings.output_format
    if output_format == "odt":
        _write_odt(app, target, blocks)
    elif output_format == "pdf":
        _write_pdf(app, target, blocks)
    else:
        raise ValueError(f"Formato de informe no admitido: {output_format}")


def _write_odt(app, target: Path, blocks: list[tuple[int, str]]) -> None:
    """Conserva texto, índice y listados como elementos nativos editables de ODF."""
    from odt_reports import _new_report, _tag, append_report_odt

    document = _new_report(
        TITLE.upper(), app._project_title(), app.report_settings,
        footer_title=TITLE, template_title=TITLE,
    )
    for level in range(1, 4):
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
    style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "PricesListingHeading", _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "PricesHeading1",
    })
    ET.SubElement(style, _tag("style:paragraph-properties"), {
        _tag("fo:break-before"): "page", _tag("fo:keep-with-next"): "always",
    })
    headings = [(level, text) for level, text in blocks if level]
    headings.extend((1, title) for title, _ in LISTINGS)
    document.heading(TITLE.upper(), 1, style="PricesHeading1")
    document.table_of_contents(headings, max_level=3, name="PricesContents")
    bookmark = 0
    for level, text in blocks:
        if level:
            document.heading(text, level, style=f"PricesHeading{level}", bookmark=f"PPTPHeading{bookmark}")
            bookmark += 1
        else:
            document.paragraph(text)
    for title, report in LISTINGS:
        document.heading(title, 1, style="PricesListingHeading", bookmark=f"PPTPHeading{bookmark}")
        bookmark += 1
        append_report_odt(app, report, document, embedded=True)
    signature = app.report_settings.signature_text()
    if signature:
        document.space(12)
        document.paragraph(signature, "SignatureText")
    document.write(target)


def _write_pdf(app, target: Path, blocks: list[tuple[int, str]]) -> None:
    """Pagina el anejo con índice real y tablas del generador presupuestario común."""
    from html import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    from reportlab.platypus.doctemplate import LayoutError
    from reportlab.platypus.tableofcontents import TableOfContents
    from budget_reports import build_pdf_report
    from report_layout import CONTENT_WIDTH, LEFT_MARGIN, RIGHT_MARGIN, ReportLayout

    settings = app.report_settings
    layout = ReportLayout(
        template_path=settings.template_path, expediente=settings.expediente,
        template_title=TITLE, footer_title=TITLE, repeat_titles=False,
    )
    body = ParagraphStyle("PricesBody", fontName="Helvetica", fontSize=10, leading=14, spaceAfter=7)
    heading_styles = {
        level: ParagraphStyle(
            f"PricesHeading{level}", parent=body, fontName="Helvetica-Bold",
            fontSize=15 - level, leading=18 - level, spaceBefore=12,
            spaceAfter=7, keepWithNext=True,
        ) for level in range(1, 4)
    }
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            f"PricesContents{level}", parent=body, leftIndent=12 * level,
            fontSize=10, textColor=colors.black, spaceBefore=4,
        ) for level in range(3)
    ]
    story = [Paragraph(escape(TITLE.upper()), heading_styles[1]), Spacer(1, 12), toc, PageBreak()]
    heading_index = 0
    for level, text in blocks + [(1, title) for title, _ in LISTINGS]:
        if level:
            if (text, dict(LISTINGS).get(text)) in LISTINGS:
                story.append(PageBreak())
            paragraph = Paragraph(escape(text), heading_styles[level])
            paragraph._prices_heading = (level - 1, text, f"PricesHeading{heading_index}")
            heading_index += 1
            story.append(paragraph)
            report = dict(LISTINGS).get(text)
            if report:
                listing, _ = build_pdf_report(app, report, layout, embedded=True)
                story.extend(listing)
        else:
            story.append(Paragraph(escape(text).replace("\n", "<br/>"), body))
    signature = settings.signature_text()
    if signature:
        story.extend([Spacer(1, 12), Paragraph(escape(signature).replace("\n", "<br/>"), body)])

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
        topMargin=layout.top_margin, bottomMargin=layout.bottom_margin,
        title=TITLE, author=settings.author,
    )
    try:
        document.multiBuild(story, onFirstPage=page_header, onLaterPages=page_header)
    except LayoutError as exc:
        raise ValueError(f"No se pudo paginar la justificación de precios: {exc}") from exc
