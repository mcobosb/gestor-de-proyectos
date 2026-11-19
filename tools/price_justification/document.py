"""Composición del anejo de justificación sin duplicar los cálculos presupuestarios."""

from datetime import date
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
import re
import tempfile
from xml.etree import ElementTree as ET
from .templates import (
    FORMULA_LEVEL, LIBRARY_PATH, NarrativeTable, document_blocks, load_documents, template_variables,
)
from .formula import PdfFormula, odt_formula
from collaborative_storage import shared_library_path
from tools._shared.document_layout import (
    LIST_INDENT, add_odt_blue_table_cells, add_odt_list_style, pdf_blue_table_style, pdf_list_style,
    split_list_item,
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


def narrative(app, data: dict) -> list[tuple[int, str | NarrativeTable]]:
    """Compone los documentos base de la biblioteca con los datos actuales del proyecto."""
    state = FIELDS | validate_state(data)
    values = template_variables(app, state)
    values["fuentes_cotizacion"] = (
        "Los datos de 2026 se contrastan con la [Orden PJC/297/2026, de 30 de marzo]"
        "(https://www.boe.es/buscar/act.php?id=BOE-A-2026-7296)."
        if state["contribution_bases"] in CONTRIBUTION_BASE_REFERENCES
        or state["contributions"] in CONTRIBUTION_RATE_REFERENCES else ""
    )
    values["fuentes_calendario"] = (
        "Para 2026, consúltese la [relación oficial de fiestas laborales]"
        "(https://www.boe.es/buscar/doc.php?id=BOE-A-2025-21667) y complétese con "
        "las publicaciones provinciales y locales que correspondan."
        if state["calendar"] in CALENDAR_REFERENCES else ""
    )
    return [
        block for _, source in load_documents(
            shared_library_path(app, "price_justification", LIBRARY_PATH)
        )
        for block in document_blocks(source, values)
    ]


_INLINE = re.compile(
    r"\[([^]\n]+)\]\((https?://[^)\s]+)\)|\b(BOE-A-\d{4}-\d+)\b|"
    r"\b([A-Za-z])_(intrinseco|utm|ut|ua|dm|hm|[nIaDtdhm12])\b|(\n)"
)


def _inline_runs(text: str):
    """Separa enlaces, variables y saltos del texto, sin interpretar etiquetas HTML."""
    cursor = 0
    for match in _INLINE.finditer(text):
        yield text[cursor:match.start()], "", ""
        if match[1]:
            yield match[1], "link", match[2]
        elif match[3]:
            yield match[3], "link", f"https://www.boe.es/buscar/doc.php?id={match[3]}"
        elif match[4]:
            yield match[4], "", ""
            yield match[5], "sub", ""
        else:
            yield "", "break", ""
        cursor = match.end()
    yield text[cursor:], "", ""


def _odt_inline(paragraph, text: str) -> None:
    """Añade texto con enlaces activos y símbolos formateados en Writer."""
    from odt_reports import _tag

    for value, kind, url in _inline_runs(text):
        if kind == "link":
            ET.SubElement(paragraph, _tag("text:a"), {
                _tag("xlink:href"): url, _tag("xlink:type"): "simple",
            }).text = value
        elif kind == "break":
            ET.SubElement(paragraph, _tag("text:line-break"))
        elif value:
            attrs = {_tag("text:style-name"): "PricesSubscript"} if kind == "sub" else {}
            ET.SubElement(paragraph, _tag("text:span"), attrs).text = value


def _pdf_inline(text: str) -> str:
    """Convierte únicamente las marcas admitidas en formato de párrafo PDF."""
    return "".join(
        f'<link href="{escape(url)}" color="#174B83"><u>{escape(value)}</u></link>' if kind == "link" else
        f"<sub>{escape(value)}</sub>" if kind == "sub" else
        "<br/>" if kind == "break" else escape(value)
        for value, kind, url in _inline_runs(text)
    )


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


def _add_odt_table_styles(document) -> None:
    """Registra los estilos del texto y las viñetas editables de las tablas."""
    from odt_reports import _tag

    add_odt_blue_table_cells(document, "Prices")
    for name, bold in (("PricesTableText", False), ("PricesTableHeader", True)):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ReportBody",
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), {
            _tag("fo:text-align"): "start", _tag("fo:margin"): "0pt",
            _tag("fo:text-indent"): "0pt", _tag("fo:line-height"): "115%",
        })
        ET.SubElement(style, _tag("style:text-properties"), {
            _tag("fo:font-weight"): "bold" if bold else "normal",
            _tag("fo:color"): "#FFFFFF" if bold else "#000000",
            _tag("fo:font-size"): "9.5pt",
        })
    style = ET.SubElement(document.automatic_styles, _tag("text:list-style"), {
        _tag("style:name"): "PricesTableBullets",
    })
    level = ET.SubElement(style, _tag("text:list-level-style-bullet"), {
        _tag("text:level"): "1", _tag("text:bullet-char"): "•",
    })
    ET.SubElement(level, _tag("style:list-level-properties"), {
        _tag("text:space-before"): "0pt", _tag("text:min-label-width"): f"{LIST_INDENT}pt",
    })


def _write_odt_table(document, table: NarrativeTable) -> None:
    """Aplica el estilo común y conserva enlaces y listas nativas en las celdas."""
    from odt_reports import _tag

    document.table(["Concepto", "Detalle"], table.rows, ratios=(0.3, 0.7),
                   body_text_style="PricesTableText")
    element = document.body[-1]
    for cell in element.find(_tag("table:table-header-rows")).iter(_tag("table:table-cell")):
        cell.set(_tag("table:style-name"), "PricesHeaderCell")
        cell[0].set(_tag("text:style-name"), "PricesTableHeader")
    for index, row in enumerate(element.findall(_tag("table:table-row"))):
        for cell, value in zip(row, table.rows[index], strict=True):
            cell.set(_tag("table:style-name"), "PricesBlueCell" if index % 2 == 0 else "PricesWhiteCell")
            cell.remove(cell[0])
            bullets = None
            for line in value.splitlines():
                if line.startswith(("• ", "- ")):
                    if bullets is None:
                        bullets = ET.SubElement(cell, _tag("text:list"), {
                            _tag("text:style-name"): "PricesTableBullets",
                        })
                    parent = ET.SubElement(bullets, _tag("text:list-item"))
                    line = line[2:]
                else:
                    bullets = None
                    parent = cell
                _odt_inline(ET.SubElement(parent, _tag("text:p"), {
                    _tag("text:style-name"): "PricesTableText",
                }), line)


def _write_odt(app, target: Path, blocks: list[tuple[int, str | NarrativeTable]]) -> None:
    """Conserva texto, índice y listados como elementos nativos editables de ODF."""
    from odt_reports import _new_report, _tag, append_report_odt

    document = _new_report(
        TITLE.upper(), app._project_title(), app.report_settings,
        footer_title=TITLE.upper(), template_title=TITLE, repeat_titles=False,
    )
    _add_odt_table_styles(document)
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
    style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "PricesSubscript", _tag("style:family"): "text",
    })
    ET.SubElement(style, _tag("style:text-properties"), {
        _tag("style:text-position"): "sub 75%",
    })

    def paragraph(name):
        """Crea un párrafo de la narrativa con el estilo indicado."""
        return ET.SubElement(document.body, _tag("text:p"), {_tag("text:style-name"): name})

    for name, properties in (
        ("PricesFormula", {_tag("fo:text-align"): "center", _tag("fo:margin-top"): "6pt",
                           _tag("fo:margin-bottom"): "9pt"}),
        ("PricesCaption", {_tag("fo:keep-with-next"): "always", _tag("fo:margin-top"): "6pt",
                           _tag("fo:margin-bottom"): "4pt"}),
    ):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ReportBody",
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), properties)
        if name == "PricesCaption":
            ET.SubElement(style, _tag("style:text-properties"), {_tag("fo:font-weight"): "bold"})
    depths = {-level for level, text in blocks if FORMULA_LEVEL < level < 0}
    for depth in sorted(depths):
        add_odt_list_style(document, f"PricesList{depth}", "ReportBody", depth - 1)
    style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "PricesListingHeading", _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "PricesHeading1",
    })
    ET.SubElement(style, _tag("style:paragraph-properties"), {
        _tag("fo:break-before"): "page", _tag("fo:keep-with-next"): "always",
    })
    headings = [(level, text) for level, text in blocks if level > 0 and isinstance(text, str)]
    headings.extend((1, title) for title, _ in LISTINGS)
    document.paragraph(TITLE.upper(), style="PricesHeading1")
    document.table_of_contents(headings, max_level=3, name="PricesContents")
    bookmark = 0
    for level, text in blocks:
        if isinstance(text, NarrativeTable):
            document.table_caption(text.title, caption="PricesCaption")
            _write_odt_table(document, text)
        elif level > 0:
            document.heading(text, level, style=f"PricesHeading{level}", bookmark=f"PPTPHeading{bookmark}")
            bookmark += 1
        elif level == FORMULA_LEVEL:
            odt_formula(paragraph("PricesFormula"), text)
        elif level < 0:
            item = paragraph(f"PricesList{-level}")
            marker, label = split_list_item(text)
            item.text = marker
            ET.SubElement(item, _tag("text:tab"))
            _odt_inline(item, label)
        else:
            _odt_inline(paragraph("ReportBody"), text)
    for title, report in LISTINGS:
        document.heading(title, 1, style="PricesListingHeading", bookmark=f"PPTPHeading{bookmark}")
        bookmark += 1
        append_report_odt(app, report, document, embedded=True)
    document.append_signature(app.report_settings)
    document.write(target)


def _write_pdf(app, target: Path, blocks: list[tuple[int, str | NarrativeTable]]) -> None:
    """Pagina el anejo con índice real y tablas del generador presupuestario común."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table
    from reportlab.platypus.doctemplate import LayoutError
    from reportlab.platypus.tableofcontents import TableOfContents
    from budget_reports import build_pdf_report
    from report_layout import LEFT_MARGIN, RIGHT_MARGIN, ReportLayout

    settings = app.report_settings
    layout = ReportLayout(
        template_path=settings.template_path, expediente=settings.expediente,
        template_title=TITLE, footer_title=TITLE.upper(), repeat_titles=False,
    )
    body = ParagraphStyle(
        "PricesBody", fontName="Helvetica", fontSize=10, leading=14,
        spaceAfter=7, alignment=TA_JUSTIFY,
    )
    caption_style = ParagraphStyle(
        "PricesCaption", parent=body, fontName="Helvetica-Bold",
        spaceBefore=6, spaceAfter=4, keepWithNext=True,
    )
    cell_style = ParagraphStyle("PricesTableText", parent=layout.normal, fontSize=9.5, leading=12)
    header_style = ParagraphStyle(
        "PricesTableHeader", parent=cell_style, fontName=layout.bold_font, textColor=colors.white,
    )
    cell_bullet_style = pdf_list_style("PricesTableBullet", cell_style)
    list_styles = {
        level: pdf_list_style(f"PricesList{-level}", body, -level - 1)
        for level, _ in blocks if FORMULA_LEVEL < level < 0
    }
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
        if isinstance(text, NarrativeTable):
            layout.table_caption(story, text.title, body=body, caption=caption_style)
            cells = [[Paragraph(label, header_style) for label in ("Concepto", "Detalle")]]
            cells.extend([
                [
                    Paragraph(_pdf_inline(line[2:]), cell_bullet_style, bulletText="•")
                    if line.startswith(("• ", "- ")) else Paragraph(_pdf_inline(line), cell_style)
                    for line in value.splitlines()
                ]
                for value in row
            ] for row in text.rows)
            table = Table(cells, colWidths=[layout.content_width * ratio for ratio in (0.3, 0.7)],
                          repeatRows=1, splitInRow=1, hAlign="LEFT", style=pdf_blue_table_style())
            table.spaceAfter = 9
            story.append(table)
        elif level > 0:
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
            story.append(PdfFormula(text))
        elif level < 0:
            marker, label = split_list_item(text)
            story.append(Paragraph(_pdf_inline(label), list_styles[level], bulletText=marker))
        else:
            story.append(Paragraph(_pdf_inline(text), body))
    story.extend(layout.signature_block(settings))

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
        title=TITLE, author=settings.document_author,
    )
    try:
        document.multiBuild(story, onFirstPage=page_header, onLaterPages=page_header)
    except LayoutError as exc:
        raise ValueError(f"No se pudo paginar la justificación de precios: {exc}") from exc
