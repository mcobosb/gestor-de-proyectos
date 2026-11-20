"""Exportación del anejo de calidad a PDF y ODT."""
from __future__ import annotations

from pathlib import Path
import re
from xml.etree import ElementTree as ET

from budget_core import currency_label, fmt_number
from budget_reports import report_output_format
from collaborative_storage import shared_library_path
from document_styles import ROLES
from tools._library_editor import read_markdown
from tools._library_markdown import document_markdown, text_blocks
from tools._shared.document_layout import (
    add_odt_blue_table_cells, add_odt_blue_table_headers, add_odt_list_style,
    pdf_list_style, split_list_item, style_odt_blue_table,
)
from tools.document_structure.catalog import QUALITY_ASSURANCE_NUMBER

from .plan import SOURCE, TRIALS, validate_rows, work_units

LIBRARY_PATH = Path(__file__).with_name("BIBLIOTECA.zip")
DOCUMENTS = (
    "01_objeto.md", "02_normativa.md", "03_recepcion.md", "04_ejecucion.md",
    "05_ensayos.md", "06_no_conformidades.md", "07_presupuesto.md",
)
TITLE = f"ANEJO Nº {int(QUALITY_ASSURANCE_NUMBER)}: PLAN DE ASEGURAMIENTO Y CONTROL DE LA CALIDAD"
FOOTER = f"ANEJO Nº {int(QUALITY_ASSURANCE_NUMBER)}. ASEGURAMIENTO Y CONTROL DE LA CALIDAD"
CHAPTER_FIELD = re.compile(r"\{\{cap[ií]tulo\}\}")
TABLES = {
    "{{tabla_frecuencias}}": (
        "Frecuencias orientativas y criterios de aceptación",
        ("Familia de UO", "Ensayo / prueba", "Frecuencia orientativa", "Criterio de aceptación"),
        (1.3, 1.7, 1.7, 2.4), (),
    ),
    "{{tabla_ensayos}}": (
        "Ensayos revisados por unidad de obra",
        ("Unidad de obra", "Ensayo / prueba", "Frecuencia revisada", "N.º", "Criterio de aceptación"),
        (1.8, 1.65, 1.5, 0.45, 2.1), (3,),
    ),
}


def write_document(app, target: Path, rows: list[dict]) -> None:
    """Genera ambos formatos a partir de las mismas fichas y la revisión vigente."""
    settings = app.report_settings
    units = work_units(app.budget_items)
    rows = validate_rows(rows, units)
    library = shared_library_path(app, "quality_assurance", LIBRARY_PATH)
    markdown = "\n\n".join(document_markdown(read_markdown(library, name)) for name in DOCUMENTS)
    for token in (*TABLES, "{{presupuesto_control}}"):
        if sum(line.strip() == token for line in markdown.splitlines()) != 1:
            raise ValueError(f"La biblioteca debe contener una vez el marcador {token} en una línea propia.")
    generated = [item for item in app.budget_items if item.source == SOURCE]
    if generated:
        total = sum(item.import_total for item in generated)
        pem = sum(item.import_total for item in app.budget_items)
        chapters = ", ".join(sorted({app._chapter_title(item.chapter) for item in generated}))
        currency = currency_label(settings.currency, settings.custom_currency)
        budget = f"Las partidas incorporadas figuran en «{chapters}» y suman {fmt_number(total)} {currency}."
        if pem > 0:
            budget += f" Representan el {fmt_number(100 * total / pem)} % del PEM actual."
    else:
        budget = "Las partidas de este plan aún no se han incorporado al presupuesto."
    blocks = text_blocks(markdown.replace("{{presupuesto_control}}", budget))
    data = {
        "{{tabla_frecuencias}}": [
            [label, TRIALS[key].name, TRIALS[key].frequency, TRIALS[key].criteria]
            for key, label in (("density", "Suelos y zanjas"), ("plate", "Suelos y explanadas"),
                               ("concrete", "Hormigones"), ("asphalt", "Mezclas bituminosas"),
                               ("paint", "Pintura vial"))
        ],
        "{{tabla_ensayos}}": [
            [f"{units[row['unit_id']].code} — {units[row['unit_id']].summary}",
             row["name"], row["frequency"], str(row["count"]), row["criteria"]]
            for row in rows
        ],
    }
    output_format = report_output_format(target, settings.output_format)
    if output_format == "odt":
        _write_odt(target, app._project_title(), settings, blocks, data)
    else:
        _write_pdf(target, app._project_title(), settings, blocks, data)


def _write_odt(target, project, settings, blocks, data) -> None:
    """Conserva encabezados, tablas y listas nativos y editables."""
    from odt_reports import _new_report, _tag

    doc = _new_report(TITLE, project, settings, repeat_titles=False, footer_title=FOOTER)
    if doc.template_footer is None:
        doc.footer = FOOTER
    elif not CHAPTER_FIELD.search("".join(doc.template_footer.itertext())):
        ET.SubElement(doc.template_footer, _tag("text:p"),
                      {_tag("text:style-name"): "FooterText"}).text = "{{capitulo}}"
    add_odt_blue_table_cells(doc, "QualityTable")
    add_odt_blue_table_headers(doc, "QualityTable")
    for role in ("title", *(f"heading{level}" for level in range(1, 7))):
        _, size, bold = ROLES[role]
        style = ET.SubElement(doc.automatic_styles, _tag("style:style"), {
            _tag("style:name"): role, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "ReportBody",
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), {
            _tag("fo:keep-with-next"): "always", _tag("fo:margin-top"): "10pt",
            _tag("fo:margin-bottom"): "5pt",
        })
        ET.SubElement(style, _tag("style:text-properties"), {
            _tag("fo:font-size"): f"{size:g}pt", _tag("fo:font-weight"): "bold" if bold else "normal",
        })
    for level in {level for level, _ in blocks if level < 0}:
        add_odt_list_style(doc, f"QualityBullet{-level}", "ReportBody", -level - 1)
    doc.paragraph(TITLE, "title")
    for level, text in blocks:
        if text in TABLES:
            caption, headers, ratios, numeric = TABLES[text]
            doc.table_caption(caption, include_reference=False)
            doc.table(headers, data[text], ratios=ratios, right_columns=numeric)
            style_odt_blue_table(doc.body[-1], "QualityTable")
        elif level > 0:
            doc.heading(text, level, style=f"heading{level}")
        elif level < 0:
            marker, label = split_list_item(text)
            node = ET.SubElement(doc.body, _tag("text:p"),
                                 {_tag("text:style-name"): f"QualityBullet{-level}"})
            node.text = marker
            ET.SubElement(node, _tag("text:tab")).tail = label
        else:
            doc.paragraph(text)
    doc.append_signature(settings)
    doc.write(target)


def _write_pdf(target, project, settings, blocks, data) -> None:
    """Pagina el plan con los estilos y cabecera institucionales."""
    from html import escape
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Spacer
    from report_layout import LEFT_MARGIN, RIGHT_MARGIN, ReportLayout

    layout = ReportLayout(template_path=settings.template_path, template_title=project,
                          expediente=settings.expediente, repeat_titles=False,
                          footer_title=FOOTER, text_styles=settings.text_styles)
    footer = "\n".join([layout.template_footer, *(
        cell["text"] for table in layout.template_tables["footer"] for row in table["rows"] for cell in row
    )])
    missing = [] if CHAPTER_FIELD.search(footer) else ["{{capitulo}}"]
    if not re.search(r"\{no_p[aá]gina\}", footer):
        missing.append("{{no_pagina}}")
    if missing:
        layout.template_footer = "\n".join(filter(None, (layout.template_footer, " — ".join(missing))))
    body = ParagraphStyle("QualityBody", fontName="Helvetica", fontSize=10,
                          leading=14, spaceAfter=7, alignment=TA_JUSTIFY)
    headings = {}
    for role in ("title", *(f"heading{level}" for level in range(1, 7))):
        _, size, bold = ROLES[role]
        headings[role] = ParagraphStyle(role, parent=body, fontSize=size, leading=size * 1.25,
                                         fontName="Helvetica-Bold" if bold else "Helvetica",
                                         spaceBefore=10, alignment=TA_LEFT, keepWithNext=True)
    bullets = {level: pdf_list_style(f"QualityBullet{-level}", body, -level - 1)
               for level, _ in blocks if level < 0}
    story = [layout.text(TITLE, headings["title"], role="title")]
    for level, text in blocks:
        if text in TABLES:
            caption, headers, ratios, numeric = TABLES[text]
            layout.table_caption(story, caption, body=body, include_reference=False)
            story.extend([layout.table(headers, data[text], ratios, blue_header=True,
                                       right_columns=numeric), Spacer(1, 10)])
        elif level > 0:
            story.append(layout.text(text, headings[f"heading{level}"]))
        elif level < 0:
            marker, label = split_list_item(text)
            story.append(layout.paragraph(escape(label), bullets[level], bulletText=marker))
        else:
            story.append(layout.text(text, body, role="body"))
    story.extend(layout.signature_block(settings))

    def page_header(canvas, document) -> None:
        """Pinta la cabecera y el pie de cada página."""
        layout.header(canvas, document, TITLE, project)

    document = SimpleDocTemplate(str(target), pagesize=layout.page_size, leftMargin=LEFT_MARGIN-6,
                                 rightMargin=RIGHT_MARGIN-6, title=TITLE, author=settings.document_author,
                                 topMargin=layout.top_margin(TITLE, project), bottomMargin=layout.bottom_margin())
    document.build(story, onFirstPage=page_header, onLaterPages=page_header)
