"""Tabla de capítulos del presupuesto con nombres de ficha enlazados."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape, unescape
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from odt_reports import _OdtReport
    from report_layout import ReportLayout
    from reportlab.lib.styles import ParagraphStyle


INDEX_MARKER = "<!-- TABLA_FICHAS_CAPITULOS -->"
INDEX_TOKEN = "[[PPTP_SHEET_INDEX]]"
HEADERS = ("Capítulo del proyecto", "Ficha")
HEADER_BACKGROUND = "#4472C4"
ALTERNATE_BACKGROUND = "#D9E5F5"
COLUMN_RATIOS = (0.35, 0.65)
TABLE_HEADER = "| " + " | ".join(HEADERS) + " |\n| --- | --- |"


@dataclass(frozen=True)
class SheetIndexRow:
    """Vincula una ficha y su capítulo con el marcador real de su encabezado."""

    chapter: str
    sheet: str
    bookmark: str


def chapter_groups(rows: list[SheetIndexRow]) -> dict[str, list[SheetIndexRow]]:
    """Agrupa las fichas sin alterar el orden de capítulos ni de sus nombres."""
    groups: dict[str, list[SheetIndexRow]] = {}
    for row in rows:
        groups.setdefault(row.chapter, []).append(row)
    return groups


def escape_cell(value: str) -> str:
    """Protege delimitadores Markdown y saltos HTML sin perder el texto visible."""
    return (
        escape(value, quote=False).replace("|", "&#124;")
        .replace("[", "&#91;").replace("]", "&#93;").replace("\n", " ")
    )


def markdown_index(rows: list[SheetIndexRow]) -> str:
    """Representa la tabla sin perder barras verticales ni interpretar HTML del proyecto."""
    lines = [TABLE_HEADER]
    for chapter, sheets in chapter_groups(rows).items():
        links = "<br/>".join(
            f"[{escape_cell(row.sheet)}](#{row.bookmark})" for row in sheets
        )
        lines.append(f"| {escape_cell(chapter)} | {links} |")
    return "\n".join(lines)


def extract_index(source: str) -> tuple[str, list[SheetIndexRow]]:
    """Extrae únicamente la tabla generada, dejando intacto el resto del Markdown."""
    pattern = re.compile(
        r"^" + re.escape(TABLE_HEADER) + r"\n((?:\|[^\n]*\|(?:\n|$))+)",
        flags=re.MULTILINE,
    )
    matches = list(pattern.finditer(source))
    if not matches:
        return source, []
    if len(matches) != 1:
        raise ValueError("El PPTP solo admite una tabla de fichas por capítulos.")
    match = matches[0]
    rows = []
    for line in match[1].splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 2:
            raise ValueError("La tabla de fichas debe contener dos columnas.")
        for value in cells[1].split("<br/>"):
            link = re.fullmatch(r"\[([^]]+)\]\(#(PPTPHeading\d+)\)", value)
            if link is None:
                raise ValueError("La tabla de fichas contiene un enlace de ficha no válido.")
            rows.append(SheetIndexRow(unescape(cells[0]), unescape(link[1]), link[2]))
    return source[:match.start()] + INDEX_TOKEN + "\n\n" + source[match.end():], rows


def write_odt_index(document: _OdtReport, rows: list[SheetIndexRow]) -> None:
    """Escribe una fila por capítulo y cada nombre de ficha como enlace independiente."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    for name, bold in [
        ("PPTPIndexHeader", True),
        ("PPTPIndexText", False),
        ("PPTPIndexBullet", False),
    ]:
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): "PPTPBody",
        })
        properties = {
            _tag("fo:text-align"): "start",
            _tag("fo:margin-top"): "0pt", _tag("fo:margin-bottom"): "0pt",
            _tag("fo:keep-with-next"): "auto",
            _tag("fo:line-height"): "115%",
            _tag("fo:break-before"): "auto", _tag("fo:break-after"): "auto",
            _tag("fo:keep-together"): "auto",
            _tag("fo:orphans"): "2", _tag("fo:widows"): "2",
        }
        if name == "PPTPIndexBullet":
            properties.update({
                _tag("fo:margin-left"): "10pt", _tag("fo:text-indent"): "-10pt",
            })
        ET.SubElement(style, _tag("style:paragraph-properties"), properties)
        ET.SubElement(style, _tag("style:text-properties"), {
            _tag("fo:font-weight"): "bold" if bold else "normal",
            _tag("style:font-weight-asian"): "bold" if bold else "normal",
            _tag("style:font-weight-complex"): "bold" if bold else "normal",
            _tag("fo:color"): "#FFFFFF" if bold else "#000000",
            _tag("fo:font-size"): "9.5pt",
        })
    for name, background in [
        ("PPTPIndexHeaderCell", HEADER_BACKGROUND),
        ("PPTPIndexBlueCell", ALTERNATE_BACKGROUND),
        ("PPTPIndexWhiteCell", "#FFFFFF"),
    ]:
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name, _tag("style:family"): "table-cell",
        })
        ET.SubElement(style, _tag("style:table-cell-properties"), {
            _tag("fo:background-color"): background,
            _tag("fo:padding"): "4pt",
            _tag("fo:border"): "0.5pt solid #B7C9E2",
            _tag("style:vertical-align"): "top",
        })
    groups = chapter_groups(rows)
    document.table(
        HEADERS, [[chapter, ""] for chapter in groups],
        ratios=COLUMN_RATIOS, body_text_style="PPTPIndexText",
    )
    table = document.body[-1]
    table.set(_tag("table:name"), "PPTPSheetIndex")
    for cell in table.find(_tag("table:table-header-rows")).iter(_tag("table:table-cell")):
        cell.set(_tag("table:style-name"), "PPTPIndexHeaderCell")
        cell.find(_tag("text:p")).set(_tag("text:style-name"), "PPTPIndexHeader")
    for index, (element, sheets) in enumerate(zip(table.findall(_tag("table:table-row")), groups.values())):
        cells = element.findall(_tag("table:table-cell"))
        for cell in cells:
            cell.set(
                _tag("table:style-name"),
                "PPTPIndexBlueCell" if index % 2 == 0 else "PPTPIndexWhiteCell",
            )
        cell = cells[1]
        cell.remove(cell.find(_tag("text:p")))
        for sheet in sheets:
            paragraph = ET.SubElement(cell, _tag("text:p"), {
                _tag("text:style-name"): "PPTPIndexBullet",
            })
            paragraph.text = "• "
            ET.SubElement(paragraph, _tag("text:a"), {
                _tag("xlink:href"): "#" + sheet.bookmark, _tag("xlink:type"): "simple",
            }).text = sheet.sheet


def pdf_index(rows: list[SheetIndexRow], layout: ReportLayout, body: ParagraphStyle):
    """Maqueta una fila por capítulo con nombres enlazados y cabecera repetida."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import FrameBreak, Paragraph, Table
    from report_layout import CONTENT_WIDTH

    normal = ParagraphStyle(
        "PPTPIndexText", parent=body, fontName=layout.normal.fontName,
        fontSize=9.5, leading=12, spaceBefore=0, spaceAfter=0, alignment=TA_LEFT,
    )
    header = ParagraphStyle(
        "PPTPIndexHeader", parent=normal, fontName=layout.bold_font, textColor=colors.white,
    )
    bullet = ParagraphStyle(
        "PPTPIndexBullet", parent=normal, leftIndent=10, firstLineIndent=0,
        bulletIndent=0, bulletFontName=normal.fontName, bulletFontSize=normal.fontSize,
    )
    data = [[Paragraph(escape(value), header) for value in HEADERS]]
    for chapter, sheets in chapter_groups(rows).items():
        data.append([
            Paragraph(escape(chapter), normal),
            [
                Paragraph(
                    f'<a href="#{row.bookmark}">{escape(row.sheet)}</a>', bullet, bulletText="•",
                )
                for row in sheets
            ],
        ])
    class SheetIndexTable(Table):
        """Mantiene la alineación y reserva las cabeceras repetidas para la página siguiente."""

        def split(self, available_width, available_height):
            """Evita encadenar fragmentos con otra cabecera dentro del mismo marco."""
            fragments = super().split(available_width, available_height)
            for fragment in fragments:
                fragment.hAlign = self.hAlign
            result = []
            for fragment in fragments:
                if result:
                    result.append(FrameBreak())
                result.append(fragment)
            return result

    return SheetIndexTable(
        data, colWidths=[CONTENT_WIDTH * ratio for ratio in COLUMN_RATIOS],
        repeatRows=1, splitInRow=1, hAlign="LEFT", style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(HEADER_BACKGROUND)),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
                colors.HexColor(ALTERNATE_BACKGROUND), colors.white,
            ]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B7C9E2")),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ],
    )
