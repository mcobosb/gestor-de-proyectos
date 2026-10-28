"""Maquetación común: tabulaciones en puntos, logo y estilos de las plantillas.

Se usan Arial Narrow y Arial de Windows cuando están disponibles; en otros
sistemas se emplea Helvetica. Las tablas fijan columnas, no insertan espacios
para simular tabuladores. El logo procede de los PDF originales.
"""
from __future__ import annotations

import os
import re
from io import BytesIO
from html import escape
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from app_paths import application_directory

LOGO_PATH = application_directory(__file__) / "iconos" / "report_header_logo.png"
CERTIFICATION_TEMPLATE_PATH = application_directory(__file__) / "plantillas" / "plantilla.odt"
LEFT_MARGIN = 68.0
RIGHT_MARGIN = 17.0
PAGE_WIDTH = 595.2755905511812
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN


def _template_length_to_points(value: str) -> float:
    """Convierte una longitud ODF en puntos PDF."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(cm|mm|in|pt|pc)", value)
    if not match:
        raise ValueError(f"Longitud no válida en la plantilla ODT: {value!r}.")
    number = float(match.group(1))
    return number * {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72, "pt": 1, "pc": 12}[
        match.group(2)
    ]


def _read_certification_template():
    """Carga la imagen de cabecera y el texto del pie de la plantilla ODT."""
    namespaces = {
        "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
        "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
        "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        "xlink": "http://www.w3.org/1999/xlink",
    }
    try:
        with ZipFile(CERTIFICATION_TEMPLATE_PATH) as archive:
            styles = ElementTree.fromstring(archive.read("styles.xml"))
            master_page = styles.find(".//style:master-page", namespaces)
            if master_page is None:
                raise ValueError("La plantilla ODT no contiene una página maestra.")
            header = master_page.find("style:header", namespaces)
            frame = header.find(".//draw:frame", namespaces) if header is not None else None
            image = frame.find(".//draw:image", namespaces) if frame is not None else None
            if image is None:
                raise ValueError("La plantilla ODT no contiene una imagen en la cabecera.")
            image_path = image.get(f"{{{namespaces['xlink']}}}href", "")
            if not image_path or image_path.startswith("/") or ".." in Path(image_path).parts:
                raise ValueError("La ruta de la imagen de cabecera ODT no es válida.")
            image_data = archive.read(image_path)
            image_width = _template_length_to_points(
                frame.get(f"{{{namespaces['svg']}}}width", "")
            )
            image_height = _template_length_to_points(
                frame.get(f"{{{namespaces['svg']}}}height", "")
            )
            footer = master_page.find("style:footer", namespaces)
            footer_text = " ".join(
                " ".join("".join(paragraph.itertext()).split())
                for paragraph in footer.findall(".//text:p", namespaces)
            ) if footer is not None else ""
            if not footer_text:
                raise ValueError("La plantilla ODT no contiene texto en el pie de página.")
    except (OSError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError(
            f"No se pudo leer la plantilla de certificación {CERTIFICATION_TEMPLATE_PATH}: {exc}"
        ) from exc
    return image_data, image_path, image_width, image_height, footer_text


class ReportLayout:
    def __init__(self, *, certification: bool = False, use_personal_template: bool = True):
        """Carga el logo y configura fuentes, sangrías y estilos del modelo elegido."""
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.utils import ImageReader

        font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

        def font(name, filename, substitute):
            """Registra una fuente TrueType disponible o devuelve su alternativa estándar."""
            path = font_dir / filename
            if not path.is_file():
                return substitute
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            return name

        self.regular_font = font("ReportNarrow", "ARIALN.TTF", "Helvetica")
        self.bold_font = font("ReportNarrowBold", "ARIALNB.TTF", "Helvetica-Bold")
        self.italic_font = font("ReportNarrowItalic", "ARIALNI.TTF", "Helvetica-Oblique")
        body_font = font("ReportArial", "arial.ttf", "Helvetica") if certification else self.regular_font
        self.normal = ParagraphStyle("report-body", fontName=self.regular_font, fontSize=8, leading=9.5)
        self.bold = ParagraphStyle("report-bold", parent=self.normal, fontName=self.bold_font)
        self.item_summary = ParagraphStyle(
            "report-item-summary", parent=self.bold, fontSize=8 if certification else 11,
            leading=9.5 if certification else 12.5,
        )
        self.right = ParagraphStyle("report-right", parent=self.normal, alignment=TA_RIGHT)
        self.bold_right = ParagraphStyle("report-bold-right", parent=self.bold, alignment=TA_RIGHT)
        self.description_style = ParagraphStyle(
            "report-description", parent=self.normal, fontName=body_font,
            fontSize=10 if certification else 9, leading=13 if certification else 10.6,
            leftIndent=57 if certification else 65, rightIndent=44, spaceAfter=5,
        )
        self.words_style = ParagraphStyle(
            "report-words", parent=self.normal, fontSize=9, leading=11,
            leftIndent=100, rightIndent=28, alignment=TA_RIGHT, spaceAfter=5,
        )
        self.center = ParagraphStyle("report-signature", parent=self.normal, alignment=TA_CENTER)
        self.signature_note = ParagraphStyle(
            "report-signature-note", parent=self.center, fontName=self.italic_font, fontSize=7,
        )
        self.title_style = ParagraphStyle(
            "report-title", parent=self.bold, fontSize=12 if certification else 16,
            leading=14 if certification else 18, alignment=0 if certification else TA_RIGHT,
        )
        self.project_style = ParagraphStyle(
            "report-project", parent=self.bold, fontSize=8, leading=9,
        )
        self.certification = certification
        self.use_personal_template = certification and use_personal_template
        self.template_footer = ""
        if self.use_personal_template:
            (
                image_data, self.template_image_path, self.logo_width, self.logo_height,
                self.template_footer,
            ) = _read_certification_template()
            self.logo = ImageReader(BytesIO(image_data))
        elif certification:
            self.logo = None
            self.logo_width = self.logo_height = 0
        else:
            # La lectura al iniciar permite informar de un logo ausente o corrupto.
            self.logo = ImageReader(str(LOGO_PATH))

    def text(self, value, style=None):
        """Escapa texto para un párrafo PDF y conserva sus saltos de línea."""
        from reportlab.platypus import Paragraph

        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style or self.normal)

    def description(self, value):
        """Crea una descripción con la sangría y tipografía del modelo."""
        return self.text(value, self.description_style)

    def leader(self, value, style=None):
        """Crea un párrafo con puntos guía hasta el final de su columna."""
        from reportlab.platypus import Paragraph

        class LeaderParagraph(Paragraph):
            def draw(self):
                """Dibuja el texto y completa el espacio libre final con una línea de puntos."""
                super().draw()
                lines = self.blPara.lines
                if self.blPara.kind == 0 and lines:
                    unused = lines[-1][0]
                    start = self.width - unused + 4
                    if start < self.width - 4:
                        self.canv.saveState()
                        self.canv.setLineWidth(0.35)
                        self.canv.setDash(0.6, 1.2)
                        self.canv.line(start, self.style.fontSize * 0.2, self.width, self.style.fontSize * 0.2)
                        self.canv.restoreState()

        return LeaderParagraph(escape(str(value)), style or self.normal)

    def table(self, headers, rows, ratios, *, bold_rows=(), background=None, rule=False, leaders=()):
        """Compone columnas fijas con alineación numérica, negritas y reglas opcionales."""
        from decimal import Decimal
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        from bc3_gui import fmt_number

        numeric_headers = {
            "CANTIDAD", "PRECIO", "PRECIO/UD.", "SUBTOTAL", "IMPORTE", "%",
            "% CERTIFICADO", "UDS", "LONGITUD", "ANCHURA", "ALTURA", "K", "PARCIAL",
        }
        numeric = {i for i, h in enumerate(headers) if h in numeric_headers}
        data = [[self.text(h, self.bold_right if i in numeric else self.bold)
                 for i, h in enumerate(headers)]] if headers else []
        bold_rows = set(bold_rows)
        for row_index, row in enumerate(rows):
            cells = []
            for column, value in enumerate(row):
                if hasattr(value, "wrap"):
                    cells.append(value)
                    continue
                is_number = isinstance(value, Decimal) or column in numeric or bool(
                    re.fullmatch(r"-?\d[\d.,]*", str(value))
                )
                strong = row_index in bold_rows
                style = (self.bold_right if strong else self.right) if is_number else (
                    self.bold if strong else self.normal
                )
                render = self.leader if column in leaders and str(value) else self.text
                cells.append(render(fmt_number(float(value)) if isinstance(value, Decimal) else value, style))
            data.append(cells)
        block = Table(
            data, colWidths=[CONTENT_WIDTH * r / sum(ratios) for r in ratios],
            repeatRows=1 if headers else 0, splitInRow=1, hAlign="LEFT",
        )
        style = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ]
        if headers:
            style.extend([
                ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.black),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
            ])
        if background is not None:
            style.append(("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(background)))
        if rule:
            style.append(("LINEABOVE", (-1, 0), (-1, 0), 0.5, colors.black))
        block.setStyle(TableStyle(style))
        return block

    def chapter(self, code, title):
        """Construye la banda de capítulo y evita separarla del siguiente contenido."""
        from reportlab.lib.styles import ParagraphStyle

        chapter_style = ParagraphStyle(
            "report-chapter", parent=self.bold, fontSize=10 if self.certification else 12, leading=14,
        )
        block = self.table(
            [], [[self.text(code, chapter_style), self.text(title, chapter_style)]], [65, CONTENT_WIDTH - 65],
            background="#00EEEE" if self.certification else "#C7D0DF",
        )
        block.keepWithNext = True
        return block

    def total(self, label, value, *, major=True):
        # Los porcentajes y sumas están tabulados a la derecha del resumen.
        """Tabula etiquetas e importes a la derecha y diferencia los totales principales."""
        rows = [["", label, value]]
        return self.table([], rows, [0.36 if major else 0.48, 0.48 if major else 0.36, 0.16],
                          bold_rows=(0,) if major else (), rule=major, leaders=(1,))

    def top_margin(self, title, project_title, column_headers=(), ratios=()):
        """Reserva altura suficiente para títulos largos y cabeceras sin invadir el cuerpo."""
        from reportlab.lib.pagesizes import A4

        heading = self.text(title, self.title_style)
        _, height = heading.wrap(CONTENT_WIDTH if self.certification else CONTENT_WIDTH - 170, 1000)
        project = self.text(project_title, self.project_style)
        _, project_height = project.wrap(CONTENT_WIDTH, 1000)
        project_y = A4[1] - (124 if self.certification else 60) - height - project_height - 2
        columns_height = 0
        if column_headers:
            _, columns_height = self.table(column_headers, [], ratios).wrap(CONTENT_WIDTH, 1000)
        return max(98 if not self.certification else 166, A4[1] - project_y + columns_height + 4)

    def header(self, canvas, document, title, project_title, *, footer="", column_headers=(), ratios=()):
        """Dibuja el logo institucional, títulos, columnas y pie numerado de una página."""
        from reportlab.lib.pagesizes import A4

        canvas.saveState()
        top = A4[1]
        if self.certification:
            logo_width = self.logo_width
            logo_y = top - 50 - self.logo_height
            heading_y = top - 124
        else:
            logo_width = 160
            logo_y = top - 83
            heading_y = top - 60
        if self.logo is not None:
            canvas.drawImage(
                self.logo, LEFT_MARGIN, logo_y, width=logo_width,
                height=self.logo_height if self.certification else logo_width * 552 / 1434,
                preserveAspectRatio=True, mask="auto",
            )
        heading = self.text(title, self.title_style)
        heading_width = CONTENT_WIDTH if self.certification else CONTENT_WIDTH - 170
        _, height = heading.wrap(heading_width, 100)
        heading.drawOn(canvas, LEFT_MARGIN if self.certification else LEFT_MARGIN + 170, heading_y - height)
        project = self.text(project_title, self.project_style)
        _, project_height = project.wrap(CONTENT_WIDTH, 100)
        project_y = heading_y - height - project_height - 2
        project.drawOn(canvas, LEFT_MARGIN, project_y)
        if column_headers:
            block = self.table(column_headers, [], ratios)
            _, h = block.wrap(CONTENT_WIDTH, 100)
            block.drawOn(canvas, LEFT_MARGIN, project_y - h - 4)
        else:
            canvas.setLineWidth(0.5)
            canvas.line(LEFT_MARGIN, project_y - 4, PAGE_WIDTH - RIGHT_MARGIN, project_y - 4)
        if self.use_personal_template:
            canvas.setFont(self.regular_font, 6)
            canvas.drawCentredString(PAGE_WIDTH / 2, 16, self.template_footer)
            canvas.drawRightString(PAGE_WIDTH - RIGHT_MARGIN, 16, str(document.page))
        elif not self.certification:
            canvas.setLineWidth(0.5)
            canvas.line(LEFT_MARGIN, 26, PAGE_WIDTH - RIGHT_MARGIN, 26)
            canvas.setFont(self.regular_font, 7)
            canvas.drawString(LEFT_MARGIN, 16, footer)
            canvas.drawRightString(PAGE_WIDTH - RIGHT_MARGIN, 16, str(document.page))
        canvas.restoreState()
