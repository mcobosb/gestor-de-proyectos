"""Exportación del anejo de calidad a PDF y ODT."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from budget_core import currency_label, fmt_number
from budget_reports import report_output_format
from collaborative_storage import shared_library_path
from document_styles import ROLES
from tools._library_editor import read_markdown
from tools._library_markdown import document_markdown, informational_ranges, link_runs, text_blocks
from tools._shared.document_layout import (
    DEFAULT_TOC_DEPTH, add_odt_blue_table_cells, add_odt_blue_table_headers, add_odt_list_style,
    add_odt_narrative_styles,
    pdf_list_style, split_list_item, style_odt_blue_table,
)
from tools.document_structure.catalog import QUALITY_ASSURANCE_NUMBER

from .catalog import correct_legacy_slip_criteria, get_catalog_entry
from .plan import (
    PARTIAL_TRIALS, SOURCE, TRIALS, describe_price_source, quantity_proposal,
    unit_snapshot, validate_rows, work_units,
)

LIBRARY_PATH = Path(__file__).with_name("BIBLIOTECA.zip")
DOCUMENTS = (
    "01_objeto.md", "02_normativa.md", "03_recepcion.md", "04_ejecucion.md",
    "05_ensayos.md", "06_no_conformidades.md", "07_presupuesto.md",
    "08_catalogo_pac.md", "09_ensayos_clave.md",
)
TITLE = f"ANEJO Nº {int(QUALITY_ASSURANCE_NUMBER)}: PLAN DE ASEGURAMIENTO Y CONTROL DE LA CALIDAD"
FOOTER = f"ANEJO Nº {int(QUALITY_ASSURANCE_NUMBER)}. ASEGURAMIENTO Y CONTROL DE LA CALIDAD"
CHAPTER_FIELD = re.compile(r"\{\{cap[ií]tulo\}\}")
RELATED_TESTS = {
    "concrete": ("ENS-01", "ENS-03"), "asphalt": ("ENS-14",), "paint": ("ENS-20",),
    "PAC-16": ("ENS-01", "ENS-02"), "PAC-17": ("ENS-03",), "PAC-18": ("ENS-04",),
    "PAC-26": ("ENS-22", "ENS-23"), "PAC-29": ("ENS-06", "ENS-07", "ENS-08"),
    "PAC-33": ("ENS-09", "ENS-10", "ENS-11", "ENS-12"),
    "PAC-34": ("ENS-09", "ENS-13", "ENS-14"),
    "PAC-36": ("ENS-15", "ENS-16", "ENS-17", "ENS-18"),
    "PAC-38": ("ENS-24",), "PAC-39": ("ENS-25", "ENS-26", "ENS-27"),
    "PAC-42": ("ENS-20", "ENS-21"), "PAC-50": ("ENS-19",), "PAC-54": ("ENS-28",),
}
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


def _row_detail(row: dict) -> str:
    """Conserva el detalle técnico sin trasladar estados de ejemplo."""
    return "\n".join(
        line for line in row.get("details", "").splitlines()
        if not line.startswith("- Estado en obra:")
    )


def _price_reference(source: dict) -> str:
    """Conserva las limitaciones y enlaza la URL de la fuente, si existe."""
    description = describe_price_source(source)
    if url := source.get("url"):
        description = description.replace(url, f"[{url}]({url})")
    return description


def _generic_control_details(source: str) -> dict[str, str] | None:
    """Lee los campos editables del bloque informativo de controles genéricos."""
    blocks = []
    for start, end in informational_ranges(source):
        lines = source[start:end].splitlines()
        if len(lines) >= 3 and lines[1].strip() == "## Datos de controles genéricos":
            blocks.append("\n".join(lines[2:-1]))
    if not blocks:
        return None
    if len(blocks) != 1:
        raise ValueError("La ficha de ensayos contiene varios bloques de controles genéricos.")
    details = {}
    headings = list(re.finditer(r"^###(?:[ \t]+(.*?))?[ \t]*$", blocks[0], re.MULTILINE))
    if not headings or blocks[0][:headings[0].start()].strip():
        raise ValueError("El bloque de controles genéricos debe utilizar títulos con sus identificadores.")
    for index, match in enumerate(headings):
        key = (match.group(1) or "").strip()
        if key not in TRIALS or key in details:
            raise ValueError(f"Identificador de control genérico no válido o repetido: {key}.")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(blocks[0])
        content = blocks[0][match.end():end].strip()
        if not content:
            raise ValueError(f"El control genérico «{key}» no tiene detalles.")
        details[key] = content
    return details


def _control_details(rows: list[dict], units: dict, currency: str, generic_details: dict[str, str]) -> str:
    """Presenta los datos de cada control revisado como una ficha de campos."""
    sections = []
    for row in rows:
        parts = [f"- Número de ensayos: {row['count']}."]
        if source := row.get("quantity_source"):
            parts.append(f"- Cantidad orientativa: {source['basis']}")
        if row["price"]:
            price = Decimal(row["price"])
            parts.append(f"- Precio directo por ensayo: {fmt_number(float(price))} {currency}.")
            parts.append(f"- Importe directo previsto: {fmt_number(float(price * row['count']))} {currency}.")
            if source := row.get("price_source"):
                parts.append(f"- Precio de referencia: {_price_reference(source)}")
        detail = _row_detail(row)
        if detail:
            parts.append(detail if detail.startswith("- ") else f"- Alcance y control: {detail}")
        trial = row.get("trial", "")
        generic = generic_details.get(trial, "")
        if generic and row["name"] == TRIALS[trial].name:
            if trial in PARTIAL_TRIALS:
                parent = get_catalog_entry(PARTIAL_TRIALS[trial])
                expected = (f"Ensayo desglosado de {parent.label}. "
                            "No incluye los demás ensayos ni inspecciones del control.")
                if detail == expected:
                    parts.append(generic)
            elif not detail:
                parts.append(generic)
        unit = units[row["unit_id"]]
        sections.append(f"### {unit.code} — {row['name']}\n\n" + "\n".join(parts))
    return "\n\n".join(sections) or "No hay controles incluidos."


def _proposal_is_current(row: dict, item) -> bool:
    """Comprueba tanto la medición como los datos usados para estimar la cantidad."""
    source = row.get("quantity_source")
    return row["snapshot"] == unit_snapshot(item) and (
        not source or source["basis"] == quantity_proposal(
            item, row.get("trial", ""), row.get("frequency", "")
        )[1]
    )


def _pending_controls(rows: list[dict], units: dict, currency: str) -> str:
    """Describe las propuestas no marcadas sin convertirlas en controles aprobados."""
    sections = []
    missing = False
    for row in rows:
        if row.get("included", True) or not str(row.get("name", "")).strip():
            continue
        item = units.get(row["unit_id"])
        if item is None:
            missing = True
            continue
        current = _proposal_is_current(row, item)
        criteria = correct_legacy_slip_criteria(row.get("trial", ""), row.get("criteria", ""))
        parts = [
            "- Estado: propuesta pendiente de revisión; no incluida en el plan aprobado.",
            f"- Frecuencia propuesta: {_inline_field(row.get('frequency') or 'Pendiente de definir.')}",
            f"- Criterio propuesto: {_inline_field(criteria or 'Pendiente de definir.')}",
        ]
        if current:
            parts.append(f"- Número propuesto: {row.get('count') or 'Pendiente de definir.'}")
            if row.get("quantity_source"):
                parts.append(f"- Base de la cantidad: {row['quantity_source']['basis']}")
        else:
            parts.append("- La unidad de obra ha cambiado; actualiza la propuesta y su cantidad.")
        if row.get("price"):
            value = str(row["price"]).strip()
            try:
                price = Decimal(value.replace(",", "."))
                if not price.is_finite() or price <= 0 or price > 1_000_000_000:
                    raise ValueError("Precio fuera de los límites admitidos.")
            except (InvalidOperation, ValueError):
                parts.append(f"- Precio propuesto pendiente de corregir: {value}.")
            else:
                price_currency = "EUR" if row.get("price_source") else currency
                parts.append(f"- Precio propuesto por ensayo: {fmt_number(float(price))} {price_currency}.")
            if source := row.get("price_source"):
                parts.append(f"- Precio de referencia: {_price_reference(source)}")
        if row.get("reference"):
            parts.append(f"- Referencia: {row['reference']}")
        if detail := _row_detail(row):
            parts.append(detail if detail.startswith("- ") else f"- Alcance y control: {detail}")
        sections.append(f"### {item.code} — {row['name']}\n\n" + "\n".join(parts))
    if missing:
        sections.append("Hay propuestas sin una unidad de obra vigente; actualízalas antes de incorporarlas.")
    return "\n\n".join(sections) or "No hay propuestas pendientes de revisión."


def _test_sheets(rows: list[dict], units: dict) -> str:
    """Asocia fichas técnicas solo a controles o ensayos del proyecto."""
    sections = []
    related: dict[str, list[str]] = {}
    for row in rows:
        item = units.get(row["unit_id"])
        trial = row.get("trial", "")
        if item is None or not str(row.get("name", "")).strip():
            continue
        if trial.startswith("ENS-"):
            identifiers = (trial,)
        elif trial in TRIALS and row["name"] == TRIALS[trial].name:
            identifiers = RELATED_TESTS.get(trial, ())
        elif trial in RELATED_TESTS and row["name"] == get_catalog_entry(trial).label:
            identifiers = RELATED_TESTS[trial]
        else:
            identifiers = ()
        for identifier in identifiers:
            direct = identifier == trial
            current = _proposal_is_current(row, item)
            if not direct:
                status = "control incluido" if row.get("included", True) else "propuesta pendiente de revisión"
                if not current:
                    status += ", pendiente de actualización"
                related.setdefault(identifier, []).append(f"- {item.code} — {row['name']} ({status}).")
                continue
            status = ("Ensayo incluido en el plan revisado." if row.get("included", True)
                      else "Ensayo propuesto, pendiente de revisión.")
            parts = [f"- Estado: {status}"]
            if not current:
                parts.append("- La unidad de obra ha cambiado; actualiza la propuesta antes de utilizarla.")
            for label, value in (("Referencia", row.get("reference", "")),
                                 ("Frecuencia", row.get("frequency", "")),
                                 ("Criterios", correct_legacy_slip_criteria(
                                     trial, row.get("criteria", "")))):
                if value:
                    parts.append(f"- {label}: {_inline_field(value)}")
            if detail := _row_detail(row):
                parts.append(detail if detail.startswith("- ") else f"- Detalle: {detail}")
            sections.append(f"### {item.code} — {row['name']}\n\n" + "\n".join(parts))
    for identifier, associations in related.items():
        entry = get_catalog_entry(identifier)
        parts = [
            "- Alcance: referencia técnica asociada a los controles siguientes; su consulta no incorpora otro ensayo al plan.",
            *(f"  {association}" for association in associations),
        ]
        for label, value in (("Referencia", entry.reference), ("Frecuencia", entry.frequency),
                             ("Criterios orientativos", entry.criteria)):
            if value:
                parts.append(f"- {label}: {_inline_field(value)}")
        if entry.details:
            parts.append(entry.details)
        sections.append(f"### {entry.label}\n\n" + "\n".join(parts))
    return "\n\n".join(sections) or "No hay fichas técnicas de ensayo asociadas a las unidades de obra."


def _inline_field(value: str) -> str:
    """Reúne líneas de una celda en un único campo legible."""
    return "; ".join(line.strip().lstrip("•").strip() for line in value.splitlines() if line.strip())


def write_document(app, target: Path, rows: list[dict]) -> None:
    """Genera ambos formatos a partir de las mismas fichas y la revisión vigente."""
    settings = app.report_settings
    currency = currency_label(settings.currency, settings.custom_currency)
    units = work_units(app.budget_items)
    all_rows = rows
    rows = validate_rows([row for row in all_rows if row.get("included", True)], units)
    rows = [row | {"criteria": correct_legacy_slip_criteria(row.get("trial", ""), row["criteria"])}
            for row in rows]
    if settings.currency != "EUR" and any(row.get("price_source") for row in rows):
        raise ValueError("Los precios de referencia están en euros; revisa la moneda y los precios antes de exportar.")
    library = shared_library_path(app, "quality_assurance", LIBRARY_PATH)
    sources = [read_markdown(library, name) for name in DOCUMENTS]
    generic_details = _generic_control_details(sources[DOCUMENTS.index("05_ensayos.md")])
    if generic_details is None:
        generic_details = _generic_control_details(read_markdown(LIBRARY_PATH, "05_ensayos.md"))
        if generic_details is None:
            raise ValueError("La biblioteca distribuida no contiene datos de controles genéricos.")
    sections = [document_markdown(source) for source in sources]
    for token, name in (("{{controles_pendientes}}", "08_catalogo_pac.md"),
                        ("{{ensayos_proyecto}}", "09_ensayos_clave.md")):
        count = sum(line.strip() == token for section in sections for line in section.splitlines())
        if count > 1:
            raise ValueError(f"La biblioteca solo puede contener una vez el marcador {token}.")
        if not count:
            index = DOCUMENTS.index(name)
            sections[index] += f"\n\n{token}"
    markdown = "\n\n".join(sections)
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
    markdown = markdown.replace("{{detalles_catalogo}}", _control_details(rows, units, currency, generic_details))
    markdown = markdown.replace("{{controles_pendientes}}", _pending_controls(all_rows, units, currency))
    markdown = markdown.replace("{{ensayos_proyecto}}", _test_sheets(all_rows, units))
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
    body = ParagraphStyle("QualityBody", fontName=layout.regular_font, fontSize=10,
                          leading=14, spaceAfter=7, alignment=TA_JUSTIFY)
    headings = {}
    for role in ("title", *(f"heading{level}" for level in range(1, 7))):
        _, size, bold = ROLES[role]
        headings[role] = ParagraphStyle(role, parent=body, fontSize=size, leading=size * 1.25,
                                         fontName=layout.bold_font if bold else layout.regular_font,
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
