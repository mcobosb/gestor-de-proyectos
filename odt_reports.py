"""Exporta informes editables en formato OpenDocument Text."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZIP_DEFLATED, ZipFile

from budget_reports import (
    CERTIFICATION_REPORT_NAMES, REPORT_NAMES, ReportSettings, _collect_report_resources,
    amount_in_words, budget_totals, certification_totals, money,
)


_ODF_MIMETYPE = "application/vnd.oasis.opendocument.text"
_NAMESPACES = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
}
for _prefix, _uri in _NAMESPACES.items():
    ET.register_namespace(_prefix, _uri)


def _tag(name: str) -> str:
    """Convierte un nombre prefijado en su nombre expandido XML."""
    prefix, local_name = name.split(":")
    return f"{{{_NAMESPACES[prefix]}}}{local_name}"


class _OdtReport:
    def __init__(
        self, title: str, project_title: str, author: str, *,
        image_data: bytes | None = None, image_path: str = "", image_type: str = "",
        image_width: float = 0, image_height: float = 0, footer: str = "",
    ):
        """Prepara el cuerpo editable y los datos de cabecera y pie del informe."""
        self.title = title
        self.author = author
        self.image_data = image_data
        self.image_path = image_path
        self.image_type = image_type
        self.image_width = image_width
        self.image_height = image_height
        self.footer = footer
        self.table_number = 0
        self.content_root = ET.Element(_tag("office:document-content"), {
            _tag("office:version"): "1.3",
        })
        body = ET.SubElement(self.content_root, _tag("office:body"))
        self.body = ET.SubElement(body, _tag("office:text"))
        self.heading(title, level=1, style="ReportTitle")
        self.paragraph(project_title, style="ProjectTitle")

    def heading(self, text: str, level: int = 2, style: str | None = None) -> None:
        """Añade un encabezado con nivel jerárquico y estilo semántico."""
        element = ET.SubElement(self.body, _tag("text:h"), {
            _tag("text:style-name"): style or f"Heading{min(level, 3)}",
            _tag("text:outline-level"): str(level),
        })
        element.text = str(text)

    def paragraph(self, text: str, style: str = "ReportBody") -> None:
        """Añade un párrafo con estilo reutilizable y conserva sus saltos de línea."""
        paragraph = ET.SubElement(self.body, _tag("text:p"), {
            _tag("text:style-name"): style,
        })
        lines = str(text).splitlines()
        for index, line in enumerate(lines):
            if index:
                ET.SubElement(paragraph, _tag("text:line-break"))
            if line:
                ET.SubElement(paragraph, _tag("text:span")).text = line

    def table(
        self, headers, rows, *, bold_rows=(), bold_text_style: str = "TableTotal",
        body_text_style: str = "TableText",
    ) -> None:
        """Añade una tabla ODF con estilos semánticos para cabecera, texto y énfasis."""
        column_count = max(
            [len(headers), *(len(row) for row in rows)], default=0,
        )
        if not column_count:
            return
        self.table_number += 1
        table = ET.SubElement(self.body, _tag("table:table"), {
            _tag("table:name"): f"Table{self.table_number}",
        })
        for _ in range(column_count):
            ET.SubElement(table, _tag("table:table-column"))

        def append_row(values, *, header=False, bold=False):
            """Escribe una fila y rellena las celdas que falten."""
            row = ET.SubElement(table, _tag("table:table-row"))
            for value in (*values, *("" for _ in range(column_count - len(values)))):
                cell = ET.SubElement(row, _tag("table:table-cell"), {
                    _tag("table:style-name"): (
                        "HeaderCell" if header else "TotalCell" if bold else "BodyCell"
                    ),
                    _tag("office:value-type"): "string",
                })
                paragraph = ET.SubElement(cell, _tag("text:p"), {
                    _tag("text:style-name"): (
                        "TableHeader" if header else
                        bold_text_style if bold else
                        body_text_style
                    ),
                })
                paragraph.text = "" if value is None else str(value)

        if headers:
            append_row(headers, header=True)
        for index, row in enumerate(rows):
            append_row(row, bold=index in bold_rows)

    def totals(self, rows) -> None:
        """Añade las líneas económicas como filas destacadas."""
        self.table([], rows, bold_rows=range(len(rows)))

    def write(self, target: Path) -> None:
        """Empaqueta el documento, los estilos, metadatos y recursos en un ODT."""
        content = ET.tostring(self.content_root, encoding="utf-8", xml_declaration=True)
        styles = self._styles_xml()
        metadata = self._metadata_xml()
        manifest = self._manifest_xml()
        with ZipFile(target, "w") as archive:
            archive.writestr("mimetype", _ODF_MIMETYPE, compress_type=ZIP_STORED)
            archive.writestr("content.xml", content, compress_type=ZIP_DEFLATED)
            archive.writestr("styles.xml", styles, compress_type=ZIP_DEFLATED)
            archive.writestr("meta.xml", metadata, compress_type=ZIP_DEFLATED)
            archive.writestr("META-INF/manifest.xml", manifest, compress_type=ZIP_DEFLATED)
            if self.image_data is not None:
                archive.writestr(self.image_path, self.image_data, compress_type=ZIP_DEFLATED)

    def _styles_xml(self) -> bytes:
        """Construye estilos de párrafo, tabla y página, incluida la plantilla."""
        root = ET.Element(_tag("office:document-styles"), {
            _tag("office:version"): "1.3",
        })
        styles = ET.SubElement(root, _tag("office:styles"))
        self._paragraph_style(styles, "Normal")
        self._paragraph_style(styles, "Project", size="10pt", bold=True, after="0.08in")
        self._paragraph_style(styles, "Title", size="16pt", bold=True, after="0.12in")
        self._paragraph_style(styles, "ReportBody")
        self._paragraph_style(styles, "ReportTitle", size="16pt", bold=True, after="0.12in")
        self._paragraph_style(styles, "ProjectTitle", size="10pt", bold=True, after="0.08in")
        self._paragraph_style(styles, "ChapterTitle", size="12pt", bold=True, before="0.12in")
        self._paragraph_style(styles, "PriceSectionTitle", size="12pt", bold=True, before="0.12in")
        self._paragraph_style(styles, "AuxiliaryTitle", size="12pt", bold=True, before="0.12in")
        self._paragraph_style(styles, "ItemDescription", after="0.04in")
        self._paragraph_style(styles, "AmountInWords", after="0.08in")
        self._paragraph_style(styles, "ReportNote", after="0.06in")
        self._paragraph_style(styles, "Signature", size="9pt", after="0.04in")
        self._paragraph_style(styles, "FooterText", size="8pt")
        self._paragraph_style(styles, "TableText")
        self._paragraph_style(styles, "TableTotal")
        self._paragraph_style(styles, "TableItemTitle")
        self._paragraph_style(styles, "Justification", size="9pt", after="0.04in")
        self._paragraph_style(styles, "ReportHeader")
        for level, size in ((1, "16pt"), (2, "12pt"), (3, "10pt")):
            self._paragraph_style(styles, f"Heading{level}", size=size, bold=True, before="0.12in")
        self._paragraph_style(styles, "TableHeader", size="8pt", bold=True)
        for name, background in (("HeaderCell", "#D9E2F3"), ("BodyCell", "#FFFFFF"),
                                 ("TotalCell", "#EAF0F6")):
            style = ET.SubElement(styles, _tag("style:style"), {
                _tag("style:name"): name,
                _tag("style:family"): "table-cell",
            })
            ET.SubElement(style, _tag("style:table-cell-properties"), {
                _tag("fo:background-color"): background,
                _tag("fo:padding"): "0.04in",
                _tag("fo:border"): "0.5pt solid #A6A6A6",
            })

        automatic = ET.SubElement(root, _tag("office:automatic-styles"))
        page_layout = ET.SubElement(automatic, _tag("style:page-layout"), {
            _tag("style:name"): "PageLayout",
        })
        ET.SubElement(page_layout, _tag("style:page-layout-properties"), {
            _tag("fo:page-width"): "21cm",
            _tag("fo:page-height"): "29.7cm",
            _tag("style:print-orientation"): "portrait",
            _tag("fo:margin-left"): "2cm",
            _tag("fo:margin-right"): "2cm",
            _tag("fo:margin-top"): "3.2cm" if self.image_data else "2cm",
            _tag("fo:margin-bottom"): "2cm",
        })
        master_styles = ET.SubElement(root, _tag("office:master-styles"))
        master_page = ET.SubElement(master_styles, _tag("style:master-page"), {
            _tag("style:name"): "Standard",
            _tag("style:page-layout-name"): "PageLayout",
        })
        if self.image_data is not None:
            header = ET.SubElement(master_page, _tag("style:header"))
            paragraph = ET.SubElement(header, _tag("text:p"), {
                _tag("text:style-name"): "ReportHeader",
            })
            frame = ET.SubElement(paragraph, _tag("draw:frame"), {
                _tag("draw:name"): "ReportHeader",
                _tag("text:anchor-type"): "as-char",
                _tag("svg:width"): f"{self.image_width * 2.54 / 72:.3f}cm",
                _tag("svg:height"): f"{self.image_height * 2.54 / 72:.3f}cm",
            })
            ET.SubElement(frame, _tag("draw:image"), {
                _tag("xlink:href"): self.image_path,
                _tag("xlink:type"): "simple",
                _tag("xlink:show"): "embed",
                _tag("xlink:actuate"): "onLoad",
            })
        if self.footer:
            footer = ET.SubElement(master_page, _tag("style:footer"))
            paragraph = ET.SubElement(footer, _tag("text:p"), {
                _tag("text:style-name"): "FooterText",
            })
            paragraph.text = self.footer
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _paragraph_style(parent, name, *, size="9pt", bold=False, before=None, after=None):
        """Define un estilo de párrafo con tipografía y espaciado opcionales."""
        style = ET.SubElement(parent, _tag("style:style"), {
            _tag("style:name"): name,
            _tag("style:family"): "paragraph",
        })
        if before or after:
            properties = {}
            if before:
                properties[_tag("fo:margin-top")] = before
            if after:
                properties[_tag("fo:margin-bottom")] = after
            ET.SubElement(style, _tag("style:paragraph-properties"), properties)
        text_properties = {_tag("fo:font-size"): size}
        if bold:
            text_properties[_tag("fo:font-weight")] = "bold"
        ET.SubElement(style, _tag("style:text-properties"), text_properties)

    def _metadata_xml(self) -> bytes:
        """Genera los metadatos OpenDocument del informe."""
        root = ET.Element(_tag("office:document-meta"), {
            _tag("office:version"): "1.3",
        })
        metadata = ET.SubElement(root, _tag("office:meta"))
        ET.SubElement(metadata, _tag("dc:title")).text = self.title
        ET.SubElement(metadata, _tag("dc:creator")).text = self.author
        ET.SubElement(metadata, _tag("meta:generator")).text = "Gestor de Proyectos"
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def _manifest_xml(self) -> bytes:
        """Genera el manifiesto ODF y declara sus recursos empaquetados."""
        root = ET.Element(_tag("manifest:manifest"), {
            _tag("manifest:version"): "1.3",
        })
        ET.SubElement(root, _tag("manifest:file-entry"), {
            _tag("manifest:full-path"): "/",
            _tag("manifest:media-type"): _ODF_MIMETYPE,
            _tag("manifest:version"): "1.3",
        })
        for name, media_type in (
            ("content.xml", "text/xml"),
            ("styles.xml", "text/xml"),
            ("meta.xml", "text/xml"),
        ):
            ET.SubElement(root, _tag("manifest:file-entry"), {
                _tag("manifest:full-path"): name,
                _tag("manifest:media-type"): media_type,
            })
        if self.image_data is not None:
            ET.SubElement(root, _tag("manifest:file-entry"), {
                _tag("manifest:full-path"): self.image_path,
                _tag("manifest:media-type"): self.image_type,
            })
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _new_report(title: str, project_title: str, settings: ReportSettings, *, certification=False):
    """Crea un documento y prepara la identidad visual correspondiente al informe."""
    from report_layout import _read_report_template

    image, image_path, width, height, footer = _read_report_template(settings.template_path)
    return _OdtReport(
        title, project_title, settings.author, image_data=image,
        image_path=f"Pictures/report-template{Path(image_path).suffix.lower()}",
        image_type="image/jpeg" if Path(image_path).suffix.lower() in {".jpg", ".jpeg"}
        else "image/png",
        image_width=width, image_height=height, footer=footer,
    )


def write_report_odt(app, report: str, target: Path) -> None:
    """Genera en ODT editable cualquiera de los informes de presupuesto."""
    from bc3_gui import fmt_number, format_chapter_code

    if report not in REPORT_NAMES:
        raise ValueError(f"Tipo de informe desconocido: {report}")
    settings = app.report_settings
    project_title = app._project_title()
    report_title = {
        REPORT_NAMES[0]: "CUADRO DE PRECIOS 1 Y 2 POR CAPÍTULOS",
        "Descompuestos": "CUADRO DE PRECIOS DESCOMPUESTOS",
        "Materiales": "LISTADO ELEMENTALES VALORADOS (PRESUPUESTO)",
        "Precios Auxiliares": "CUADRO DE PRECIOS AUXILIARES",
        "Presupuesto mediciones": "PRESUPUESTO",
        "Presupuesto resumen": "RESUMEN DE PRESUPUESTO",
    }[report]
    document = _new_report(report_title, project_title, settings)
    codes = list(dict.fromkeys([*app.chapter_order, *(item.chapter for item in app.budget_items)]))
    groups = [
        (code, [item for item in app.budget_items if item.chapter == code])
        for code in codes
    ]
    groups = [(code, items) for code, items in groups if items]
    pem = sum((money(item.import_total) for item in app.budget_items), money(0))

    def money_text(value) -> str:
        """Formatea importes del presupuesto con la divisa del proyecto."""
        return app._format_currency(float(value))

    def chapter(code):
        """Añade el título del capítulo y su nombre visible."""
        document.heading(
            f"{format_chapter_code(code)} — {app._chapter_title(code)}", level=2,
            style="ChapterTitle",
        )

    def total(label, value):
        """Añade una línea de total con la divisa del proyecto."""
        document.table([], [[label, money_text(value)]], bold_rows=(0,))

    if report == "Presupuesto resumen":
        rows = []
        for code, items in groups:
            amount = sum((money(item.import_total) for item in items), money(0))
            percent = amount * 100 / pem if pem else money(0)
            rows.append([
                format_chapter_code(code), app._chapter_title(code), money_text(amount),
                fmt_number(float(percent)),
            ])
        document.table(["CAPÍTULO", "RESUMEN", "IMPORTE", "%"], rows)
        totals = budget_totals(float(pem), settings)
        for label, amount in (
            ("PRESUPUESTO DE EJECUCIÓN MATERIAL", totals["pem"]),
            (f"{fmt_number(settings.general_expenses)} % Gastos generales", totals["general_expenses"]),
            (f"{fmt_number(settings.industrial_profit)} % Beneficio industrial", totals["industrial_profit"]),
            ("Suma", totals["additions"]),
            ("PRESUPUESTO BASE DE LICITACIÓN SIN IVA", totals["taxable"]),
            (f"{fmt_number(settings.vat)} % IVA", totals["vat"]),
            ("PRESUPUESTO BASE DE LICITACIÓN", totals["total"]),
        ):
            total(label, amount)
        document.paragraph(
            "Asciende el presupuesto a la expresada cantidad de " +
            amount_in_words(
                totals["total"], settings.currency, settings.custom_currency
            ),
            style="AmountInWords",
        )
    elif report in {"Materiales", "Precios Auxiliares"}:
        auxiliaries, materials = _collect_report_resources(app)
        if report == "Materiales":
            rows = []
            group = None
            subtotal = money(0)
            grand_total = money(0)
            for _, (concept, quantity, price) in sorted(materials.items()):
                prefix = concept.code[:3]
                if group is not None and prefix != group:
                    rows.append(["", "", "", f"Grupo {group}", "", money_text(subtotal)])
                    subtotal = money(0)
                amount = money(quantity * Decimal(str(price)))
                rows.append([
                    concept.code, fmt_number(float(quantity), 3), concept.unit,
                    concept.summary, money_text(price), money_text(amount),
                ])
                subtotal += amount
                grand_total += amount
                group = prefix
            if group is not None:
                rows.append(["", "", "", f"Grupo {group}", "", money_text(subtotal)])
            if rows:
                document.table(
                    ["CÓDIGO", "CANTIDAD", "UD.", "RESUMEN", "PRECIO/UD.", "IMPORTE"], rows,
                )
            else:
                document.paragraph(
                    "No hay materiales en las descomposiciones del proyecto.",
                    style="ReportNote",
                )
            total("TOTAL", grand_total)
        else:
            if not auxiliaries:
                document.paragraph(
                    "No hay precios auxiliares en las descomposiciones del proyecto.",
                    style="ReportNote",
                )
            for concept, price, children in auxiliaries.values():
                document.heading(
                    f"{concept.code} · {concept.unit} · {concept.summary}", level=2,
                    style="AuxiliaryTitle",
                )
                if concept.text:
                    document.paragraph(concept.text, style="ItemDescription")
                rows = [[
                    child.code, child.unit, fmt_number(quantity, 3), child.summary,
                    money_text(child_price), money_text(money(quantity * child_price)),
                ] for child, quantity, child_price in children]
                document.table(
                    ["CÓDIGO", "UD", "CANTIDAD", "RESUMEN", "PRECIO", "SUBTOTAL"], rows,
                )
                total("COSTE UNITARIO TOTAL", price)
                difference = money(price) - sum(
                    (money(quantity * child_price) for _, quantity, child_price in children),
                    money(0),
                )
                if difference:
                    total("Diferencia respecto a los componentes", difference)
                document.paragraph(
                    "Asciende el precio unitario del concepto auxiliar a " + amount_in_words(
                        price, settings.currency, settings.custom_currency
                    ),
                    style="AmountInWords",
                )
    else:
        for section in ((1, 2) if report == REPORT_NAMES[0] else (0,)):
            if section:
                document.heading(
                    f"CUADRO DE PRECIOS {section}", level=2,
                    style="PriceSectionTitle",
                )
            for code, items in groups:
                chapter(code)
                for item in items:
                    shown_amount = (
                        item.unit_price if section == 1 else
                        item.import_total if report == "Presupuesto mediciones" else None
                    )
                    item_text = [item.code, item.unit, item.summary]
                    if shown_amount is not None:
                        item_text.append(money_text(shown_amount))
                    document.table(
                        [], [item_text], bold_rows=(0,),
                        bold_text_style="TableItemTitle",
                    )
                    if item.text:
                        document.paragraph(item.text, style="ItemDescription")
                    if section == 1:
                        document.paragraph(amount_in_words(
                            item.unit_price, settings.currency, settings.custom_currency
                        ), style="AmountInWords")
                    elif section == 2:
                        grouped = {}
                        for concept, quantity, price in app._report_components(item):
                            kind = app._budget_concept_type(item, concept.code)
                            label = {
                                "1": "Mano de obra", "2": "Maquinaria", "3": "Materiales",
                            }.get(kind, "Resto de obra y costes complementarios")
                            grouped[label] = grouped.get(label, money(0)) + money(quantity * price)
                        if grouped:
                            document.table(
                                ["RESUMEN", "IMPORTE"],
                                [[label, money_text(amount)] for label, amount in grouped.items()],
                            )
                            difference = money(item.unit_price) - sum(grouped.values(), money(0))
                            if difference:
                                total("Diferencia respecto al precio unitario", difference)
                        else:
                            document.paragraph("Sin descomposición.", style="ReportNote")
                        total("TOTAL PARTIDA", item.unit_price)
                    elif report == "Descompuestos":
                        components = app._report_components(item)
                        if components:
                            document.table(
                                ["CÓDIGO", "UD", "RESUMEN", "CANTIDAD", "PRECIO", "SUBTOTAL"],
                                [[
                                    concept.code, concept.unit, concept.summary,
                                    fmt_number(quantity, 3), money_text(price),
                                    money_text(money(quantity * price)),
                                ] for concept, quantity, price in components],
                            )
                            difference = money(item.unit_price) - sum(
                                (money(quantity * price) for _, quantity, price in components),
                                money(0),
                            )
                            if difference:
                                total("Diferencia respecto al precio unitario", difference)
                        else:
                            document.paragraph("Sin descomposición.", style="ReportNote")
                        total("TOTAL PARTIDA", item.unit_price)
                    elif report == "Presupuesto mediciones":
                        if item.measurement_lines:
                            document.table(
                                ["COMENTARIO", "UDS", "LONGITUD", "ANCHURA", "ALTURA", "K", "PARCIAL"],
                                [[
                                    line.comment,
                                    *[
                                        "" if value is None else fmt_number(value, 3)
                                        for value in (
                                            line.units, line.length, line.width, line.height
                                        )
                                    ],
                                    fmt_number(line.k, 3), money_text(line.partial),
                                ] for line in item.measurement_lines],
                            )
                        document.table(
                            ["CANTIDAD", "PRECIO", "IMPORTE"],
                            [[fmt_number(item.measurement), money_text(item.unit_price),
                              money_text(item.import_total)]],
                        )
                if report == "Presupuesto mediciones":
                    total(
                        f"TOTAL {format_chapter_code(code)}",
                        sum((money(item.import_total) for item in items), money(0)),
                    )
        if report == "Presupuesto mediciones":
            total("TOTAL EJECUCIÓN MATERIAL", pem)

    signature = [
        value for value in (
            settings.location, settings.role, settings.author, settings.organization,
            settings.signature,
        ) if value
    ]
    if signature:
        document.paragraph("\n".join(signature), style="Signature")
    document.write(target)


def write_certification_odt(
    items, chapter_title, project_title: str, settings: ReportSettings,
    report: str, target: Path, month_key: str, certification_number: int, month_label: str,
) -> None:
    """Genera en ODT editable el resumen o el detalle de una certificación."""
    from bc3_gui import format_currency, fmt_number, format_chapter_code

    if report not in CERTIFICATION_REPORT_NAMES:
        raise ValueError(f"Tipo de informe desconocido: {report}")
    detail = report == CERTIFICATION_REPORT_NAMES[1]
    certified = []
    for item in items:
        value = item.certifications.get(month_key, 0.0)
        if not isinstance(value, (int, float)) or not float("-inf") < value < float("inf"):
            raise ValueError(f"Importe certificado no válido en {item.code}.")
        amount = money(value)
        if amount:
            if detail and (not isinstance(item.unit_price, (int, float))
                           or not float("-inf") < item.unit_price < float("inf")
                           or item.unit_price == 0):
                raise ValueError(
                    f"No se puede calcular la cantidad certificada de {item.code}: "
                    "el precio unitario debe ser finito y distinto de cero."
                )
            certified.append((item, amount))
    if not certified:
        raise ValueError("No hay partidas con importe certificado en el mes seleccionado.")

    heading = "CERTIFICACIÓN Y MEDICIONES" if detail else "RESUMEN DE CERTIFICACIÓN"
    title = f"{heading} · Certificación {certification_number} · {month_label}"
    document = _new_report(title, project_title, settings, certification=True)

    def money_text(value) -> str:
        """Formatea importes certificados con la divisa de su configuración."""
        return format_currency(
            float(value), settings.currency, settings.custom_currency
        )
    codes = list(dict.fromkeys(item.chapter for item, _ in certified))
    chapter_amounts = {
        code: sum((amount for item, amount in certified if item.chapter == code), money(0))
        for code in codes
    }
    pem = sum(chapter_amounts.values(), money(0))

    def total(label, value):
        """Añade una línea de total de la certificación."""
        document.table([], [[label, money_text(value)]], bold_rows=(0,))

    if detail:
        for code in codes:
            document.heading(
                f"{format_chapter_code(code)} — {chapter_title(code)}", level=2,
                style="ChapterTitle",
            )
            for item, amount in certified:
                if item.chapter != code:
                    continue
                document.table(
                    [], [[item.code, item.unit, item.summary]], bold_rows=(0,),
                    bold_text_style="TableItemTitle",
                )
                if item.text:
                    document.paragraph(item.text, style="ItemDescription")
                quantity = float(item.certifications[month_key]) / item.unit_price
                document.table(
                    ["CANTIDAD", "PRECIO", "IMPORTE"],
                    [[fmt_number(quantity, 6), money_text(item.unit_price), money_text(amount)]],
                )
                justification = item.certification_justifications.get(month_key, "")
                if justification:
                    document.paragraph(
                        f"Justificación: {justification}", style="Justification",
                    )
            total(f"TOTAL {format_chapter_code(code)}", chapter_amounts[code])
        total("TOTAL EJECUCIÓN MATERIAL CERTIFICADA", pem)
        document.paragraph(
            "Cantidad certificada = importe certificado del mes / precio unitario. "
            "No se incluyen líneas de medición del presupuesto ni dimensiones no certificadas. "
            "La cantidad se muestra con seis decimales; el importe mensual guardado prevalece.",
            style="ReportNote",
        )
    else:
        rows = []
        for code in codes:
            budget = sum(
                (money(item.import_total) for item in items if item.chapter == code), money(0)
            )
            percentage = fmt_number(float(chapter_amounts[code] * 100 / budget)) if budget else "No aplicable"
            rows.append([
                format_chapter_code(code), chapter_title(code), money_text(chapter_amounts[code]),
                percentage,
            ])
        document.table(["CAPÍTULO", "RESUMEN", "IMPORTE", "% CERTIFICADO"], rows)
        document.paragraph(
            "% certificado respecto al presupuesto de cada capítulo.",
            style="ReportNote",
        )
        totals = certification_totals(float(pem), settings)
        for label, amount in (
            ("EJECUCIÓN MATERIAL", totals["pem"]),
            (f"{fmt_number(settings.general_expenses)} % Gastos generales", totals["general_expenses"]),
            (f"{fmt_number(settings.industrial_profit)} % Beneficio industrial", totals["industrial_profit"]),
            ("Suma", totals["additions"]),
            ("EJECUCIÓN POR CONTRATA SIN IVA", totals["taxable"]),
            (f"{fmt_number(settings.tender_discount, 7)} % Baja (a deducir)", totals["discount"]),
            ("EJECUCIÓN DESPUÉS DE BAJA", totals["net"]),
            (f"{fmt_number(settings.vat)} % IVA", totals["vat"]),
            (f"LÍQUIDO CERTIFICACIÓN {certification_number}", totals["total"]),
        ):
            total(label, amount)
        document.paragraph(
            "Asciende la presente certificación a la expresada cantidad de " +
            amount_in_words(
                totals["total"], settings.currency, settings.custom_currency
            ),
            style="AmountInWords",
        )

    signatures = [
        "\n".join(filter(None, (
            settings.contractor, settings.contractor_representative, settings.signature,
        ))),
        "\n".join(filter(None, (
            settings.role, settings.author, settings.organization, settings.location,
            settings.signature,
        ))),
    ]
    document.table(
        ["CONTRATISTA", "DIRECCIÓN FACULTATIVA"], [signatures],
        body_text_style="Signature",
    )
    document.write(target)
