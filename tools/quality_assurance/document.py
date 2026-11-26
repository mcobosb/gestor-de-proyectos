"""Exportación del anejo de calidad a PDF y ODT."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from budget_core import currency_label, fmt_number
from budget_reports import report_output_format
from collaborative_storage import shared_library_path
from document_styles import ROLES
from tools._library_editor import read_markdown
from tools._library_markdown import document_markdown, link_runs, text_blocks
from tools._shared.document_layout import (
    DEFAULT_TOC_DEPTH, add_odt_blue_table_cells, add_odt_blue_table_headers, add_odt_list_style,
    add_odt_narrative_styles,
    pdf_list_style, split_list_item, style_odt_blue_table,
)
from tools.document_structure.catalog import QUALITY_ASSURANCE_NUMBER

from .catalog import get_catalog_entry
from .plan import SOURCE, TRIALS, describe_price_source, validate_rows, work_units

LIBRARY_PATH = Path(__file__).with_name("BIBLIOTECA.zip")
DOCUMENTS = (
    "01_objeto.md", "02_normativa.md", "03_recepcion.md", "04_ejecucion.md",
    "05_ensayos.md", "06_no_conformidades.md", "07_presupuesto.md",
    "08_catalogo_pac.md", "09_ensayos_clave.md",
)
TITLE = f"ANEJO Nº {int(QUALITY_ASSURANCE_NUMBER)}: PLAN DE ASEGURAMIENTO Y CONTROL DE LA CALIDAD"
FOOTER = f"ANEJO Nº {int(QUALITY_ASSURANCE_NUMBER)}. ASEGURAMIENTO Y CONTROL DE LA CALIDAD"
CHAPTER_FIELD = re.compile(r"\{\{cap[ií]tulo\}\}")
RELATED_TESTS = {"concrete": ("ENS-01", "ENS-03"), "asphalt": ("ENS-14",), "paint": ("ENS-20",)}
TABLES = {
    "{{tabla_frecuencias}}": (
        "Frecuencias orientativas y criterios de aceptación",
        ("Familia de UO", "Ensayo / prueba", "Frecuencia orientativa", "Criterio de aceptación"),
        (1.3, 1.7, 1.7, 2.4), (),
    ),
    "{{tabla_ensayos}}": (
        "Ensayos revisados por unidad de obra",
        ("Unidad de obra", "Ensayo / prueba", "Frecuencia revisada", "N.º",
         "Criterio de aceptación", "Referencia normativa"),
        (1.6, 1.35, 1.3, 0.4, 1.8, 1.4), (3,),
    ),
}


def write_document(app, target: Path, rows: list[dict]) -> None:
    """Genera ambos formatos a partir de las mismas fichas y la revisión vigente."""
    settings = app.report_settings
    currency = currency_label(settings.currency, settings.custom_currency)
    units = work_units(app.budget_items)
    rows = validate_rows(rows, units)
    library = shared_library_path(app, "quality_assurance", LIBRARY_PATH)
    markdown = "\n\n".join(document_markdown(read_markdown(library, name)) for name in DOCUMENTS)
    for token in (*TABLES, "{{presupuesto_control}}", "{{detalles_catalogo}}"):
        if sum(line.strip() == token for line in markdown.splitlines()) != 1:
            raise ValueError(f"La biblioteca debe contener una vez el marcador {token} en una línea propia.")
    generated = [item for item in app.budget_items if item.source == SOURCE]
    if generated:
        total = sum(item.import_total for item in generated)
        pem = sum(item.import_total for item in app.budget_items)
        chapters = ", ".join(sorted({app._chapter_title(item.chapter) for item in generated}))
        budget = f"Las partidas incorporadas figuran en «{chapters}» y suman {fmt_number(total)} {currency}."
        if pem > 0:
            budget += f" Representan el {fmt_number(100 * total / pem)} % del PEM actual."
    else:
        budget = "Las partidas de este plan aún no se han incorporado al presupuesto."
    detail_sections = []
    for row in rows:
        detail = "\n".join(
            line for line in row.get("details", "").splitlines()
            if not line.startswith("- Estado en obra:")
        )
        trial = row.get("trial")
        related = RELATED_TESTS.get(trial, ()) if trial in TRIALS and row["name"] == TRIALS[trial].name else ()
        references = []
        for identifier in related:
            entry = get_catalog_entry(identifier)
            references.append(
                f"Ficha relacionada para consulta: {entry.label}\n\n"
                f"Referencia: {entry.reference}\n\nFrecuencia orientativa: {entry.frequency}\n\n"
                f"Criterios orientativos: {entry.criteria}\n\n{entry.details}"
            )
        price_detail = ""
        if row["price"]:
            price = Decimal(row["price"])
            price_detail = (f"Precio directo por ensayo: {fmt_number(float(price))} {currency}. "
                            f"Importe directo previsto: {fmt_number(float(price * row['count']))} {currency}.")
            source = row.get("price_source")
            if source:
                price_detail += f" Precio de referencia: {describe_price_source(source)}"
        if detail or references or price_detail:
            unit = units[row["unit_id"]]
            detail_sections.append(
                f"### {unit.code} — {row['name']}\n\n"
                + "\n\n".join(filter(None, (price_detail, detail, *references)))
            )
    details = "\n\n".join(detail_sections) or "No hay fichas ampliadas asociadas a los controles incluidos."
    markdown = markdown.replace("{{detalles_catalogo}}", details)
    blocks = text_blocks(markdown.replace("{{presupuesto_control}}", budget), preserve_links=True)
    data = {
        "{{tabla_frecuencias}}": [
            [label, TRIALS[key].name, TRIALS[key].frequency, TRIALS[key].criteria]
            for key, label in (("density", "Suelos y zanjas"), ("plate", "Suelos y explanadas"),
                               ("concrete", "Hormigones"), ("asphalt", "Mezclas bituminosas"),
                               ("paint", "Pintura vial"))
        ],
        "{{tabla_ensayos}}": [
            [f"{units[row['unit_id']].code} — {units[row['unit_id']].summary}",
             row["name"], row["frequency"], str(row["count"]), row["criteria"],
             row.get("reference", "")]
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

    def add_inline(parent, text):
        """Conserva enlaces externos como elementos editables de Writer."""
        for value, url in link_runs(text):
            if value:
                node = ET.SubElement(parent, _tag("text:a" if url else "text:span"), {
                    _tag("xlink:href"): url, _tag("xlink:type"): "simple",
                } if url else {})
                node.text = value

    doc = _new_report(TITLE, project, settings, repeat_titles=False, footer_title=FOOTER)
    if doc.template_footer is None:
        doc.footer = FOOTER
    elif not CHAPTER_FIELD.search("".join(doc.template_footer.itertext())):
        ET.SubElement(doc.template_footer, _tag("text:p"),
                      {_tag("text:style-name"): "FooterText"}).text = "{{capitulo}}"
    add_odt_blue_table_cells(doc, "QualityTable")
    add_odt_blue_table_headers(doc, "QualityTable")
    add_odt_narrative_styles(doc, "Quality")
    for level in {level for level, _ in blocks if level < 0}:
        add_odt_list_style(doc, f"QualityBullet{-level}", "QualityBody", -level - 1)
    doc.paragraph(TITLE, "ReportTitle")
    headings = [(level, text) for level, text in blocks if level > 0]
    if headings:
        doc.table_of_contents(
            headings, max_level=DEFAULT_TOC_DEPTH, name="QualityContents",
            source_styles={level: f"QualityHeading{level}"
                           for level in range(1, DEFAULT_TOC_DEPTH + 1)},
        )
    bookmark = 0
    for level, text in blocks:
        if text in TABLES:
            caption, headers, ratios, numeric = TABLES[text]
            doc.table_caption(caption, include_reference=False)
            doc.table(headers, data[text], ratios=ratios, right_columns=numeric)
            style_odt_blue_table(doc.body[-1], "QualityTable")
        elif level > 0:
            doc.heading(
                text, level, style=f"QualityHeading{level}", bookmark=f"PPTPHeading{bookmark}",
            )
            bookmark += 1
        elif level < 0:
            marker, label = split_list_item(text)
            node = ET.SubElement(doc.body, _tag("text:p"),
                                 {_tag("text:style-name"): f"QualityBullet{-level}"})
            node.text = marker
            ET.SubElement(node, _tag("text:tab"))
            add_inline(node, label)
        else:
            node = ET.SubElement(doc.body, _tag("text:p"), {_tag("text:style-name"): "QualityBody"})
            add_inline(node, text)
    doc.append_signature(settings)
    doc.name_text_styles({
        f"QualityHeading{level}": f"Calidad - Título {level}"
        for level in range(1, DEFAULT_TOC_DEPTH + 1)
    })
    doc.write(target)


def _write_pdf(target, project, settings, blocks, data) -> None:
    """Pagina el plan con los estilos y cabecera institucionales."""
    from html import escape
    from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Spacer
    from report_layout import LEFT_MARGIN, RIGHT_MARGIN, ReportLayout
    from tools._shared.pdf_contents import ContentsDocTemplate, add_pdf_contents

    def inline(text):
        """Escapa texto y conserva los enlaces HTTP del Markdown."""
        return "".join(
            f'<link href="{escape(url, quote=True)}" color="#174B83"><u>{escape(value)}</u></link>'
            if url else escape(value)
            for value, url in link_runs(text)
        )

    layout = ReportLayout(template_path=settings.template_path, logo_path=settings.master_logo_path,
                          template_title=project,
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
            heading = layout.text(text, headings[f"heading{level}"])
            if level <= DEFAULT_TOC_DEPTH:
                heading._contents_heading = (level, text)
            story.append(heading)
        elif level < 0:
            marker, label = split_list_item(text)
            story.append(layout.paragraph(inline(label), bullets[level], bulletText=marker))
        else:
            story.append(layout.paragraph(inline(text), body, role="body"))
    story.extend(layout.signature_block(settings))
    add_pdf_contents(story, layout, insertion=1)

    def page_header(canvas, document) -> None:
        """Pinta la cabecera y el pie de cada página."""
        layout.header(canvas, document, TITLE, project)

    document = ContentsDocTemplate(str(target), pagesize=layout.page_size, leftMargin=LEFT_MARGIN-6,
                                 rightMargin=RIGHT_MARGIN-6, title=TITLE, author=settings.document_author,
                                 topMargin=layout.top_margin(TITLE, project), bottomMargin=layout.bottom_margin())
    document.multiBuild(story, onFirstPage=page_header, onLaterPages=page_header)
