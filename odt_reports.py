"""Exporta informes editables en formato OpenDocument Text."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZIP_DEFLATED, ZipFile

from budget_reports import (
    CERTIFICATION_REPORT_NAMES, DEVIATION_REPORT_NAME, REPORT_NAMES, ReportSettings,
    _collect_report_resources, amount_in_words, budget_totals,
    certification_deviation_rows, certification_totals, money,
)
from report_layout import (
    CERTIFICATION_COLUMN_RATIOS, CERTIFICATION_SUMMARY_RATIOS, COLUMN_REFERENCE_WIDTH,
    CONTENT_WIDTH, DEVIATION_COLUMN_RATIOS, DEVIATION_HEADERS, LEFT_MARGIN,
    MEASUREMENT_COLUMN_RATIOS, PAGE_MARGIN, RIGHT_MARGIN, ReportLayout,
    budget_report_columns, report_project_label,
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
        layout: ReportLayout | None = None,
    ):
        """Prepara el cuerpo editable y los datos de cabecera y pie del informe."""
        self.title = title
        self.project_title = project_title
        self.layout = layout
        self.page_titles = {"Standard": title}
        self.current_page = "Standard"
        self.header_columns = ()
        self.header_ratios = ()
        self.pending_page_break = False
        self.author = author
        self.image_data = image_data
        self.image_path = image_path
        self.image_type = image_type
        self.image_width = image_width
        self.image_height = image_height
        self.footer = footer
        self.template_borders: dict[str, dict[str, str]] = {}
        self.table_number = 0
        self.content_root = ET.Element(_tag("office:document-content"), {
            _tag("office:version"): "1.3",
        })
        self.automatic_styles = ET.SubElement(self.content_root, _tag("office:automatic-styles"))
        body = ET.SubElement(self.content_root, _tag("office:body"))
        self.body = ET.SubElement(body, _tag("office:text"))

    def section(self, title: str) -> None:
        """Cambia la cabecera repetida al comenzar un nuevo cuadro de precios."""
        self.current_page = f"Section{len(self.page_titles)}"
        self.page_titles[self.current_page] = title
        self.pending_page_break = bool(len(self.body))

    def chapter(self, code: str, title: str, *, new_page: bool = False) -> None:
        """Dibuja la banda de capítulo con las mismas columnas que el PDF."""
        self.pending_page_break = self.pending_page_break or new_page
        self.table(
            [], [[code, title]], ratios=[65, CONTENT_WIDTH - 65],
            bold_rows=(0,), bold_text_style="ChapterTitle", chapter=True,
        )

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

    def space(self, height: float) -> None:
        """Reserva el mismo espacio vertical explícito que el PDF."""
        name = f"Space{height:.4f}"
        if not any(style.get(_tag("style:name")) == name for style in self.automatic_styles):
            style = ET.SubElement(self.automatic_styles, _tag("style:style"), {
                _tag("style:name"): name, _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): "ReportBody",
            })
            ET.SubElement(style, _tag("style:paragraph-properties"), {
                _tag("fo:line-height"): f"{height:.4f}pt",
                _tag("fo:margin-top"): "0pt", _tag("fo:margin-bottom"): "0pt",
            })
            ET.SubElement(style, _tag("style:text-properties"), {_tag("fo:font-size"): "1pt"})
        self.paragraph("", style=name)

    def table(
        self, headers, rows, *, bold_rows=(), bold_text_style: str = "TableTotal",
        body_text_style: str = "TableText",
        ratios=(), right_columns=(), chapter: bool = False, keep_next: bool = False,
        highlighted_rows=(),
    ) -> None:
        """Añade una tabla ODF con estilos semánticos para cabecera, texto y énfasis."""
        column_count = max(
            [len(headers), *(len(row) for row in rows)], default=0,
        )
        if not column_count:
            return
        self.table_number += 1
        name = f"Table{self.table_number}"
        table_style = ET.SubElement(self.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name,
            _tag("style:family"): "table",
        })
        if self.pending_page_break or not len(self.body):
            table_style.set(_tag("style:master-page-name"), self.current_page)
        properties = {
            _tag("style:width"): f"{CONTENT_WIDTH:.4f}pt",
            _tag("table:align"): "left",
            _tag("fo:margin-left"): "0pt",
            _tag("fo:margin-top"): "5.6693pt" if chapter and not (
                self.layout and self.layout.certification
            ) else "0pt",
            _tag("fo:margin-bottom"): "0pt",
        }
        if self.pending_page_break:
            properties[_tag("fo:break-before")] = "page"
            self.pending_page_break = False
        if chapter or keep_next:
            properties[_tag("fo:keep-with-next")] = "always"
        ET.SubElement(table_style, _tag("style:table-properties"), properties)
        table = ET.SubElement(self.body, _tag("table:table"), {
            _tag("table:name"): name,
            _tag("table:style-name"): name,
        })
        widths = ratios or [1] * column_count
        if len(widths) != column_count or any(width <= 0 for width in widths):
            raise ValueError("Los anchos de columna ODT deben ser positivos y coincidir con la tabla.")
        for index, width in enumerate(widths):
            column_name = f"{name}Column{index}"
            column_style = ET.SubElement(self.automatic_styles, _tag("style:style"), {
                _tag("style:name"): column_name, _tag("style:family"): "table-column",
            })
            ET.SubElement(column_style, _tag("style:table-column-properties"), {
                _tag("style:column-width"): f"{CONTENT_WIDTH * width / sum(widths):.4f}pt",
            })
            ET.SubElement(table, _tag("table:table-column"), {
                _tag("table:style-name"): column_name,
            })
        numeric_headers = {
            "CANTIDAD", "PRECIO", "PRECIO/UD.", "SUBTOTAL", "IMPORTE", "%",
            "% CERTIFICADO", "UDS", "LONGITUD", "ANCHURA", "ALTURA", "K", "PARCIAL",
            "MEDICIÓN TOTAL",
        }
        numeric = set(right_columns) | {
            index for index, label in enumerate(headers) if label in numeric_headers
        }

        def append_row(values, *, header=False, bold=False, highlighted=False):
            """Escribe una fila y rellena las celdas que falten."""
            parent = ET.SubElement(table, _tag("table:table-header-rows")) if header else table
            row = ET.SubElement(parent, _tag("table:table-row"))
            for index, value in enumerate((*values, *("" for _ in range(column_count - len(values))))):
                cell = ET.SubElement(row, _tag("table:table-cell"), {
                    _tag("table:style-name"): (
                        "ChapterCell" if chapter or highlighted else
                        "HeaderCell" if header else "TotalCell" if bold else "BodyCell"
                    ),
                    _tag("office:value-type"): "string",
                })
                if index == column_count - 1:
                    cell.set(
                        _tag("table:style-name"),
                        cell.get(_tag("table:style-name")) + "Last",
                    )
                text_style = (
                        "TableHeader" if header else
                        bold_text_style if bold else
                        body_text_style
                )
                if not header and bold_text_style in {"TableItemTitle", "AuxiliaryTitle"} and index != 2:
                    text_style = "TableTotal"
                if index in numeric:
                    text_style += "Right"
                paragraph = ET.SubElement(cell, _tag("text:p"), {
                    _tag("text:style-name"): text_style,
                })
                lines = ("" if value is None else str(value)).split("\n")
                paragraph.text = lines[0]
                for line in lines[1:]:
                    ET.SubElement(paragraph, _tag("text:line-break")).tail = line

        if headers:
            append_row(headers, header=True)
        for index, row in enumerate(rows):
            append_row(row, bold=index in bold_rows, highlighted=index in highlighted_rows)

    def totals(self, rows) -> None:
        """Añade las líneas económicas como filas destacadas."""
        self.table([], rows, bold_rows=range(len(rows)))

    def write(self, target: Path) -> None:
        """Empaqueta el documento, los estilos, metadatos y recursos en un ODT."""
        styles = self._styles_xml()
        content = ET.tostring(self.content_root, encoding="utf-8", xml_declaration=True)
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
        if self.layout:
            self._pdf_styles(styles)
        for name in (
            "TableHeader", "TableText", "TableTotal", "TableItemTitle",
            "Signature", "ChapterTitle", "AuxiliaryTitle",
        ):
            style = ET.SubElement(styles, _tag("style:style"), {
                _tag("style:name"): f"{name}Right",
                _tag("style:family"): "paragraph",
                _tag("style:parent-style-name"): name,
            })
            ET.SubElement(style, _tag("style:paragraph-properties"), {
                _tag("fo:text-align"): "end",
            })
        for section, name in (("header", "ReportHeader"), ("footer", "FooterText")):
            borders = self.template_borders.get(section, {})
            if borders:
                style = next(
                    element for element in styles
                    if element.get(_tag("style:name")) == name
                )
                properties = style.find(_tag("style:paragraph-properties"))
                if properties is None:
                    properties = ET.SubElement(style, _tag("style:paragraph-properties"))
                for edge, value in borders.items():
                    properties.set(_tag(f"fo:border-{edge}"), value)
        automatic = ET.SubElement(root, _tag("office:automatic-styles"))
        for name, background in (("HeaderCell", "#FFFFFF"), ("BodyCell", "#FFFFFF"),
                                 ("TotalCell", "#FFFFFF"), ("ChapterCell", (
                                     "#00EEEE" if self.layout and self.layout.certification
                                     else "#C7D0DF"
                                 ))):
            style = ET.SubElement(automatic, _tag("style:style"), {
                _tag("style:name"): name,
                _tag("style:family"): "table-cell",
            })
            ET.SubElement(style, _tag("style:table-cell-properties"), {
                _tag("fo:background-color"): background,
                _tag("fo:padding-top"): "1.5pt",
                _tag("fo:padding-bottom"): "1.5pt",
                _tag("fo:padding-left"): "0pt",
                _tag("fo:padding-right"): "3pt",
                _tag("fo:border"): "none",
                **({
                    _tag("fo:border-top"): "0.6pt solid #000000",
                    _tag("fo:border-bottom"): "0.5pt solid #000000",
                } if name == "HeaderCell" else {}),
            })
            self.automatic_styles.append(deepcopy(style))
            last_style = deepcopy(style)
            last_style.set(_tag("style:name"), f"{name}Last")
            last_style.find(_tag("style:table-cell-properties")).set(
                _tag("fo:padding-right"), "0pt",
            )
            automatic.append(last_style)
            self.automatic_styles.append(deepcopy(last_style))

        page_layout = ET.SubElement(automatic, _tag("style:page-layout"), {
            _tag("style:name"): "PageLayout",
        })
        ET.SubElement(page_layout, _tag("style:page-layout-properties"), {
            _tag("fo:page-width"): "21cm",
            _tag("fo:page-height"): "29.7cm",
            _tag("style:print-orientation"): "portrait",
            _tag("fo:margin-left"): f"{LEFT_MARGIN}pt",
            _tag("fo:margin-right"): f"{RIGHT_MARGIN}pt",
            _tag("fo:margin-top"): "2.5cm",
            _tag("fo:margin-bottom"): "2.5cm",
        })
        header_style = ET.SubElement(page_layout, _tag("style:header-style"))
        top_margin = self.layout.top_margin(
            self.title, self.project_title, self.header_columns, self.header_ratios,
        ) if self.layout else 166
        ET.SubElement(header_style, _tag("style:header-footer-properties"), {
            _tag("fo:min-height"): f"{top_margin - PAGE_MARGIN + 5:.4f}pt",
            _tag("fo:margin-bottom"): "6pt",
        })
        footer_style = ET.SubElement(page_layout, _tag("style:footer-style"))
        ET.SubElement(footer_style, _tag("style:header-footer-properties"), {
            _tag("fo:min-height"): "7pt", _tag("fo:margin-top"): "15pt",
        })
        master_styles = ET.SubElement(root, _tag("office:master-styles"))
        logo_style = ET.SubElement(automatic, _tag("style:style"), {
            _tag("style:name"): "HeaderLogo",
            _tag("style:family"): "graphic",
        })
        ET.SubElement(logo_style, _tag("style:graphic-properties"), {
            _tag("style:horizontal-pos"): "from-left",
            _tag("style:horizontal-rel"): "paragraph",
            _tag("style:vertical-pos"): "from-top",
            _tag("style:vertical-rel"): "paragraph",
            _tag("style:wrap"): "none",
        })
        for page_name, page_title in self.page_titles.items():
            self._master_page(master_styles, automatic, page_name, page_title)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def _pdf_styles(self, styles) -> None:
        """Asigna a los estilos editables los valores tipográficos reales del PDF."""
        layout = self.layout
        if layout is None:
            return
        mapping = {
            "Normal": layout.normal, "ReportBody": layout.normal,
            "ReportTitle": layout.title_style, "ProjectTitle": layout.project_style,
            "ChapterTitle": layout.bold, "PriceSectionTitle": layout.title_style,
            "AuxiliaryTitle": layout.item_summary,
            "ItemDescription": layout.description_style, "AmountInWords": layout.words_style,
            "ReportNote": layout.normal, "Signature": layout.center,
            "SignatureNote": layout.signature_note, "TableHeader": layout.bold,
            "TableText": layout.normal, "TableTotal": layout.bold,
            "TableItemTitle": layout.item_summary, "Justification": layout.normal,
            "FooterText": layout.normal, "ReportHeader": layout.normal,
            "Heading1": layout.bold, "Heading2": layout.bold, "Heading3": layout.bold,
        }
        self._paragraph_style(styles, "SignatureNote")
        for element in styles:
            name = element.get(_tag("style:name"))
            if name not in mapping:
                continue
            pdf = mapping[name]
            for child in list(element):
                element.remove(child)
            font = (
                "Arial Narrow" if pdf.fontName.startswith("ReportNarrow") else
                "Arial" if pdf.fontName.startswith("ReportArial") else "Helvetica"
            )
            size = 6 if name == "FooterText" else (
                10 if layout.certification else 12
            ) if name == "ChapterTitle" else pdf.fontSize
            ET.SubElement(element, _tag("style:text-properties"), {
                _tag("fo:font-family"): font,
                _tag("fo:font-size"): f"{size}pt",
                _tag("fo:font-weight"): "bold" if "Bold" in pdf.fontName else "normal",
                _tag("fo:font-style"): "italic" if (
                    "Italic" in pdf.fontName or "Oblique" in pdf.fontName
                ) else "normal",
            })
            properties = {
                _tag("fo:line-height"): f"{14 if name == 'ChapterTitle' else pdf.leading}pt",
                _tag("fo:text-align"): {0: "start", 1: "center", 2: "end", 4: "justify"}.get(
                    pdf.alignment, "start",
                ),
                _tag("fo:margin-left"): f"{pdf.leftIndent}pt",
                _tag("fo:margin-right"): f"{pdf.rightIndent}pt",
                _tag("fo:margin-top"): f"{pdf.spaceBefore}pt",
                _tag("fo:margin-bottom"): f"{pdf.spaceAfter}pt",
            }
            if name == "ReportHeader":
                properties[_tag("fo:line-height")] = f"{layout.header_logo_height}pt"
            if name == "ProjectTitle":
                properties[_tag("fo:margin-top")] = "2pt"
            if name == "FooterText":
                properties[_tag("fo:line-height")] = "7pt"
            if name in {"ChapterTitle", "TableItemTitle", "AuxiliaryTitle"}:
                properties[_tag("fo:keep-with-next")] = "always"
            paragraph_properties = ET.SubElement(element, _tag("style:paragraph-properties"), properties)
            if name == "FooterText":
                stops = ET.SubElement(paragraph_properties, _tag("style:tab-stops"))
                for position, alignment in ((CONTENT_WIDTH / 2, "center"), (CONTENT_WIDTH, "right")):
                    ET.SubElement(stops, _tag("style:tab-stop"), {
                        _tag("style:position"): f"{position:.4f}pt",
                        _tag("style:type"): alignment,
                    })

    def _master_page(self, master_styles, styles, name: str, title: str) -> None:
        """Repite logo, título, proyecto y pie numerado con la geometría del PDF."""
        master_page = ET.SubElement(master_styles, _tag("style:master-page"), {
            _tag("style:name"): name,
            _tag("style:page-layout-name"): "PageLayout",
        })
        if self.image_data is not None:
            header = ET.SubElement(master_page, _tag("style:header"))
            paragraph = ET.SubElement(header, _tag("text:p"), {
                _tag("text:style-name"): "ReportHeader",
            })
            frame = ET.SubElement(paragraph, _tag("draw:frame"), {
                _tag("draw:name"): "ReportHeader",
                _tag("draw:style-name"): "HeaderLogo",
                _tag("text:anchor-type"): "paragraph",
                _tag("svg:x"): "0pt",
                _tag("svg:y"): "0pt",
                _tag("svg:width"): f"{self.image_width * 2.54 / 72:.3f}cm",
                _tag("svg:height"): f"{self.image_height * 2.54 / 72:.3f}cm",
            })
            ET.SubElement(frame, _tag("draw:image"), {
                _tag("xlink:href"): self.image_path,
                _tag("xlink:type"): "simple",
                _tag("xlink:show"): "embed",
                _tag("xlink:actuate"): "onLoad",
            })
        else:
            header = ET.SubElement(master_page, _tag("style:header"))
        paragraph = ET.SubElement(header, _tag("text:p"), {
            _tag("text:style-name"): "ReportTitle",
        })
        for index, line in enumerate(title.split("\n")):
            if index:
                ET.SubElement(paragraph, _tag("text:line-break"))
            ET.SubElement(paragraph, _tag("text:span")).text = line
        ET.SubElement(header, _tag("text:p"), {
            _tag("text:style-name"): "ProjectTitle",
        }).text = self.project_title
        if self.header_columns:
            self._header_table(header, styles)
        if self.footer:
            footer = ET.SubElement(master_page, _tag("style:footer"))
            paragraph = ET.SubElement(footer, _tag("text:p"), {
                _tag("text:style-name"): "FooterText",
            })
            ET.SubElement(paragraph, _tag("text:tab")).tail = self.footer
            ET.SubElement(paragraph, _tag("text:tab"))
            ET.SubElement(paragraph, _tag("text:page-number"), {
                _tag("text:select-page"): "current",
            }).text = "1"

    def _header_table(self, header, styles) -> None:
        """Alinea las cabeceras repetidas con las columnas del cuerpo del informe."""
        count = len(self.body)
        first_style = len(self.automatic_styles)
        self.table(self.header_columns, [], ratios=self.header_ratios)
        table = self.body[count]
        self.body.remove(table)
        header.append(table)
        for style in list(self.automatic_styles)[first_style:]:
            self.automatic_styles.remove(style)
            style.attrib.pop(_tag("style:master-page-name"), None)
            styles.append(style)

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
    from report_layout import _read_report_template, _read_report_template_borders

    image, image_path, width, height, footer = _read_report_template(settings.template_path)
    document = _OdtReport(
        title, report_project_label(project_title), settings.author, image_data=image,
        image_path=f"Pictures/report-template{Path(image_path).suffix.lower()}",
        image_type="image/jpeg" if Path(image_path).suffix.lower() in {".jpg", ".jpeg"}
        else "image/png",
        image_width=width, image_height=height, footer=footer,
        layout=ReportLayout(certification=certification, template_path=settings.template_path),
    )
    document.template_borders = _read_report_template_borders(settings.template_path)
    return document


def write_report_odt(app, report: str, target: Path) -> None:
    """Genera en ODT editable cualquiera de los informes de presupuesto."""
    from bc3_gui import currency_label, fmt_number, format_chapter_code

    if report not in REPORT_NAMES:
        raise ValueError(f"Tipo de informe desconocido: {report}")
    settings = app.report_settings
    project_title = app._project_title()
    report_title = {
        "Cuadro de precios 1 y 2 por capitulos": "CUADRO DE PRECIOS 1",
        "Mediciones": "MEDICIONES",
        "Descompuestos": "CUADRO DE PRECIOS DESCOMPUESTOS",
        "Materiales": "LISTADO ELEMENTALES VALORADOS\n(PRESUPUESTO)",
        "Precios Auxiliares": "CUADRO DE PRECIOS AUXILIARES",
        "Presupuesto mediciones": "PRESUPUESTO",
        "Presupuesto resumen": "RESUMEN DE PRESUPUESTO",
    }[report]
    document = _new_report(
        report_title,
        project_title,
        settings,
    )
    document.project_title = report_project_label(
        project_title, "" if report == "Mediciones" else
        currency_label(settings.currency, settings.custom_currency),
    )
    _, component_widths = budget_report_columns("Descompuestos")
    _, auxiliary_widths = budget_report_columns("Precios Auxiliares")
    document.header_columns, document.header_ratios = budget_report_columns(report)
    codes = list(dict.fromkeys([*app.chapter_order, *(item.chapter for item in app.budget_items)]))
    groups = [
        (code, [item for item in app.budget_items if item.chapter == code])
        for code in codes
    ]
    groups = [(code, items) for code, items in groups if items]
    pem = sum((money(item.import_total) for item in app.budget_items), money(0))

    def money_text(value) -> str:
        """Formatea importes como en el PDF; la divisa figura en la cabecera."""
        return fmt_number(float(value))

    def chapter(code, *, new_page=False):
        """Añade el título del capítulo y su nombre visible."""
        document.chapter(
            format_chapter_code(code), app._chapter_title(code), new_page=new_page,
        )

    def total(label, value, major=True):
        """Añade una línea de total con la divisa del proyecto."""
        if report == "Presupuesto resumen":
            indent = COLUMN_REFERENCE_WIDTH * (0.36 if major else 0.48)
            document.table(
                [], [["", label, fmt_number(float(value)), ""]],
                ratios=[indent, 393 - indent, 75, COLUMN_REFERENCE_WIDTH - 468],
                bold_rows=(0,) if major else (), right_columns=(2,),
            )
            return
        document.table(
            [], [["", label, fmt_number(float(value))]],
            ratios=[0.36 if major else 0.48, 0.48 if major else 0.36, 0.16],
            bold_rows=(0,) if major else (), right_columns=(2,),
        )

    if report == "Presupuesto resumen":
        rows = []
        for code, items in groups:
            amount = sum((money(item.import_total) for item in items), money(0))
            percent = amount * 100 / pem if pem else money(0)
            rows.append([
                format_chapter_code(code), app._chapter_title(code), money_text(amount),
                fmt_number(float(percent)),
            ])
        document.table(
            ["CAPÍTULO", "RESUMEN", "IMPORTE", "%"], rows,
            ratios=[65, 328, 75, COLUMN_REFERENCE_WIDTH - 468],
        )
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
            total(label, amount, major=label not in {
                f"{fmt_number(settings.general_expenses)} % Gastos generales",
                f"{fmt_number(settings.industrial_profit)} % Beneficio industrial",
                f"{fmt_number(settings.vat)} % IVA", "Suma",
            })
        document.space(5 * 72 / 25.4)
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
                    ratios=[50, 45, 18, 290, 50, 57],
                    bold_rows=[i for i, row in enumerate(rows) if str(row[3]).startswith("Grupo ")],
                    highlighted_rows=[i for i, row in enumerate(rows) if str(row[3]).startswith("Grupo ")],
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
                document.table(
                    [], [[concept.code, concept.unit, concept.summary]],
                    ratios=[50, 15, CONTENT_WIDTH - 65], bold_rows=(0,),
                    bold_text_style="AuxiliaryTitle", keep_next=True,
                )
                if concept.text:
                    document.paragraph(concept.text, style="ItemDescription")
                rows = [[
                    child.code, child.unit, fmt_number(quantity, 3), child.summary,
                    fmt_number(child_price), fmt_number(float(money(quantity * child_price))), "",
                ] for child, quantity, child_price in children]
                document.table(
                    [], rows, ratios=auxiliary_widths, right_columns=(2, 4, 5, 6),
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
                document.space(4 * 72 / 25.4)
    else:
        for section in ((1, 2) if report == "Cuadro de precios 1 y 2 por capitulos" else (0,)):
            if section:
                document.section(f"CUADRO DE PRECIOS {section}")
            for chapter_index, (code, items) in enumerate(groups):
                chapter(code, new_page=chapter_index > 0)
                for item in items:
                    shown_amount = (
                        item.unit_price if section == 1 else
                        item.import_total if report == "Presupuesto mediciones" else None
                    )
                    item_text = [item.code, item.unit, item.summary]
                    if shown_amount is not None:
                        item_text.append(fmt_number(float(shown_amount)))
                    document.table(
                        [], [item_text], bold_rows=(0,),
                        bold_text_style="TableItemTitle",
                        ratios=([50, 15, CONTENT_WIDTH - 115, 50] if shown_amount is not None else
                                [50, 15, CONTENT_WIDTH - 65]),
                        right_columns=(3,) if shown_amount is not None else (),
                        keep_next=True,
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
                            if concept.code == "%CI" and item.indirect_costs:
                                label = f"Costes indirectos ({fmt_number(item.indirect_costs)} %)"
                            grouped[label] = grouped.get(label, money(0)) + money(quantity * price)
                        if grouped:
                            document.table(
                                ["RESUMEN", "IMPORTE"],
                                [[label, fmt_number(float(amount))] for label, amount in grouped.items()],
                                ratios=[8, 2],
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
                                [],
                                [[
                                    concept.code, concept.unit, concept.summary,
                                    fmt_number(quantity, 3), fmt_number(price),
                                    fmt_number(float(money(quantity * price))), "",
                                ] for concept, quantity, price in components],
                                ratios=component_widths, right_columns=(3, 4, 5, 6),
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
                    elif report in {"Mediciones", "Presupuesto mediciones"}:
                        if item.measurement_lines:
                            document.table(
                                ["COMENTARIO", "UDS", "LONGITUD", "ANCHURA", "ALTURA", "K", "PARCIAL"]
                                + ([""] if report == "Mediciones" else []),
                                [[
                                    line.comment,
                                    *[
                                        "" if value is None else fmt_number(value, 3)
                                        for value in (
                                            line.units, line.length, line.width, line.height
                                        )
                                    ],
                                    fmt_number(line.k, 3),
                                    fmt_number(line.partial, 3 if report == "Mediciones" else 2),
                                ] + ([""] if report == "Mediciones" else [])
                                 for line in item.measurement_lines],
                                ratios=MEASUREMENT_COLUMN_RATIOS if report == "Mediciones" else
                                [4, 1, 1, 1, 1, 0.8, 1.2],
                            )
                        if report == "Mediciones":
                            document.table(
                                [],
                                [["MEDICIÓN TOTAL", "", "", "", "", "",
                                  fmt_number(item.measurement, 3), item.unit]],
                                ratios=MEASUREMENT_COLUMN_RATIOS, bold_rows=(0,),
                                right_columns=(6,),
                            )
                        else:
                            document.table(
                                ["CANTIDAD", "PRECIO", "IMPORTE"],
                                [[fmt_number(item.measurement), fmt_number(item.unit_price),
                                  fmt_number(item.import_total)]],
                                ratios=[CONTENT_WIDTH - 100, 50, 50],
                            )
                    document.space(3 * 72 / 25.4)
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
        document.space(8 * 72 / 25.4)
        for value in signature:
            document.paragraph(
                value, style="SignatureNote" if value == settings.signature else "Signature",
            )
    document.write(target)


def write_certification_odt(
    items, chapter_title, project_title: str, settings: ReportSettings,
    report: str, target: Path, month_key: str, certification_number: int, month_label: str,
    *, planned_amounts: list[float] | None = None,
) -> None:
    """Genera en ODT editable el resumen o el detalle de una certificación."""
    from bc3_gui import currency_label, fmt_number, format_chapter_code

    if report not in (*CERTIFICATION_REPORT_NAMES, DEVIATION_REPORT_NAME):
        raise ValueError(f"Tipo de informe desconocido: {report}")
    detail = report == CERTIFICATION_REPORT_NAMES[1]
    deviation = report == DEVIATION_REPORT_NAME
    deviation_rows, deviation_total = (
        certification_deviation_rows(items, planned_amounts, month_key) if deviation else ([], [])
    )
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
    if not certified and not deviation:
        raise ValueError("No hay partidas con importe certificado en el mes seleccionado.")

    heading = "INFORME DE DESVIACIONES" if deviation else (
        "CERTIFICACIÓN Y MEDICIONES" if detail else "RESUMEN DE CERTIFICACIÓN"
    )
    title = f"{heading} · Certificación {certification_number} · {month_label}"
    document = _new_report(
        title, project_title,
        settings, certification=True,
    )
    document.project_title = report_project_label(
        project_title, currency_label(settings.currency, settings.custom_currency),
    )
    certification_widths = CERTIFICATION_COLUMN_RATIOS
    if detail:
        document.header_columns = [
            "CÓDIGO", "RESUMEN", "UDS", "LONGITUD", "ANCHURA", "ALTURA",
            "CANTIDAD", "PRECIO", "IMPORTE",
        ]
        document.header_ratios = certification_widths

    def money_text(value) -> str:
        """Formatea importes como en el PDF; la divisa figura en la cabecera."""
        return fmt_number(float(value))
    codes = list(dict.fromkeys(item.chapter for item, _ in certified))
    chapter_amounts = {
        code: sum((amount for item, amount in certified if item.chapter == code), money(0))
        for code in codes
    }
    pem = sum(chapter_amounts.values(), money(0))

    def total(label, value, major=True):
        """Añade una línea de total de la certificación."""
        indent = COLUMN_REFERENCE_WIDTH * (0.25 if major else 0.35)
        document.table(
            [], [["", label, money_text(value)]] if detail else [["", label, money_text(value), ""]],
            ratios=[0.36 if major else 0.48, 0.48 if major else 0.36, 0.16] if detail else [
                indent, sum(CERTIFICATION_SUMMARY_RATIOS[:2]) - indent,
                *CERTIFICATION_SUMMARY_RATIOS[2:],
            ],
            bold_rows=(0,) if major else (), right_columns=(2,),
        )

    if deviation:
        document.table(
            DEVIATION_HEADERS,
            [[money_text(value) if isinstance(value, Decimal) else value for value in row]
             for row in [*deviation_rows, deviation_total]],
            ratios=DEVIATION_COLUMN_RATIOS, right_columns=(3, 4, 5),
            bold_rows=(len(deviation_rows),),
        )
        document.paragraph(
            "Desviación = certificado - plan original del mes. "
            "Se incluyen las partidas pendientes de certificar.", style="ReportNote",
        )
    elif detail:
        for chapter_index, code in enumerate(codes):
            document.chapter(
                format_chapter_code(code), chapter_title(code), new_page=chapter_index > 0,
            )
            for item, amount in certified:
                if item.chapter != code:
                    continue
                document.table(
                    [], [[item.code, item.unit, item.summary]], bold_rows=(0,),
                    bold_text_style="TableItemTitle",
                    ratios=[57, 14, CONTENT_WIDTH - 71], keep_next=True,
                )
                if item.text:
                    document.paragraph(item.text, style="ItemDescription")
                quantity = float(item.certifications[month_key]) / item.unit_price
                document.table(
                    [],
                    [["", "", "", "", "", "", fmt_number(quantity, 6),
                      money_text(item.unit_price), money_text(amount)]],
                    ratios=certification_widths, right_columns=(6, 7, 8),
                )
                justification = item.certification_justifications.get(month_key, "")
                if justification:
                    document.paragraph(
                        f"Justificación: {justification}", style="Justification",
                    )
                document.space(4 * 72 / 25.4)
            total(f"TOTAL {format_chapter_code(code)}", chapter_amounts[code])
        total("TOTAL EJECUCIÓN MATERIAL CERTIFICADA", pem)
        document.space(4 * 72 / 25.4)
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
        document.table(
            ["CAPÍTULO", "RESUMEN", "IMPORTE", "% CERTIFICADO"], rows,
            ratios=CERTIFICATION_SUMMARY_RATIOS,
        )
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
            total(label, amount, major=label not in {
                f"{fmt_number(settings.general_expenses)} % Gastos generales",
                f"{fmt_number(settings.industrial_profit)} % Beneficio industrial",
                f"{fmt_number(settings.vat)} % IVA",
                f"{fmt_number(settings.tender_discount, 7)} % Baja (a deducir)", "Suma",
            })
        document.paragraph(
            "Asciende la presente certificación a la expresada cantidad de " +
            amount_in_words(
                totals["total"], settings.currency, settings.custom_currency
            ),
            style="AmountInWords",
        )

    signatures = [
        "\n".join(filter(None, (
            settings.contractor_representative, settings.contractor, settings.signature,
        ))),
        "\n".join(filter(None, (
            settings.role, settings.author, settings.organization, settings.location,
            settings.signature,
        ))),
    ]
    document.space(8 * 72 / 25.4)
    document.table(
        [], [signatures], body_text_style="Signature", ratios=[1, 1],
    )
    document.write(target)
