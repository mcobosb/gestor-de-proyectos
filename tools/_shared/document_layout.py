"""Convenciones de titulos e indices compartidas por los documentos de obra."""
from __future__ import annotations

import re
import unicodedata

LIST_INDENT = 24
LIST_DEPTH_STEP = 12


def table_labels(number: int | str, title: str) -> tuple[str, str]:
    """Genera la llamada en el cuerpo y el rótulo inmediatamente antes de la tabla."""
    title = title.strip().rstrip(".")
    return f"Véase la Tabla {number}.", f"Tabla {number}. {title}"


def pdf_list_style(name, parent, depth: int = 0):
    """Alinea el marcador y las líneas de continuación de una lista PDF."""
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle

    return ParagraphStyle(
        name, parent=parent, alignment=TA_LEFT, leftIndent=LIST_INDENT + LIST_DEPTH_STEP * depth,
        firstLineIndent=0, bulletIndent=LIST_DEPTH_STEP * depth, bulletAnchor="start",
        bulletFontName=parent.fontName, bulletFontSize=parent.fontSize, spaceAfter=3,
    )


def add_odt_list_style(document, name: str, parent: str, depth: int = 0) -> None:
    """Define el tabulador y la sangría francesa de una lista ODT."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): name, _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): parent,
    })
    indent = LIST_INDENT + LIST_DEPTH_STEP * depth
    properties = ET.SubElement(style, _tag("style:paragraph-properties"), {
        _tag("fo:margin-left"): f"{indent}pt", _tag("fo:text-indent"): f"-{LIST_INDENT}pt",
        _tag("fo:margin-top"): "0pt", _tag("fo:margin-bottom"): "3pt",
        _tag("fo:text-align"): "start",
    })
    tabs = ET.SubElement(properties, _tag("style:tab-stops"))
    ET.SubElement(tabs, _tag("style:tab-stop"), {
        _tag("style:position"): f"{indent}pt", _tag("style:type"): "left",
    })


def split_list_item(text: str) -> tuple[str, str]:
    """Separa el marcador preservando el énfasis del texto de Markdown."""
    marker, _, body = text.partition(" ")
    if marker.startswith("**"):
        marker = marker[2:]
        if marker.endswith("**"):
            marker = marker[:-2]
        else:
            body = "**" + body
    return marker, body


DEFAULT_TOC_DEPTH = 2
TABLE_HEADER_BACKGROUND = "#4472C4"
TABLE_ALTERNATE_BACKGROUND = "#D9E5F5"
TABLE_BORDER_COLOR = "#B7C9E2"


def pdf_blue_table_style() -> list[tuple]:
    """Devuelve el estilo común de las tablas descriptivas de obra."""
    from reportlab.lib import colors

    return [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(TABLE_HEADER_BACKGROUND)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.HexColor(TABLE_ALTERNATE_BACKGROUND), colors.white,
        ]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor(TABLE_BORDER_COLOR)),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]


def add_odt_blue_table_cells(document, prefix: str) -> None:
    """Registra los mismos colores, bordes y márgenes de celda para Writer."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    for suffix, color in (
        ("HeaderCell", TABLE_HEADER_BACKGROUND),
        ("BlueCell", TABLE_ALTERNATE_BACKGROUND),
        ("WhiteCell", "#FFFFFF"),
    ):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): prefix + suffix, _tag("style:family"): "table-cell",
        })
        ET.SubElement(style, _tag("style:table-cell-properties"), {
            _tag("fo:background-color"): color,
            _tag("fo:padding"): "4pt", _tag("fo:border"): f"0.5pt solid {TABLE_BORDER_COLOR}",
            _tag("style:vertical-align"): "top",
        })


def add_odt_blue_table_headers(document, prefix: str) -> None:
    """Conserva la tipografía y alineación de las cabeceras con texto blanco."""
    from xml.etree import ElementTree as ET
    from odt_reports import _tag

    for suffix in ("", "Right"):
        style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): f"{prefix}Header{suffix}",
            _tag("style:family"): "paragraph",
            _tag("style:parent-style-name"): f"TableHeader{suffix}",
        })
        ET.SubElement(style, _tag("style:text-properties"), {_tag("fo:color"): "#FFFFFF"})


def style_odt_blue_table(table, prefix: str) -> None:
    """Aplica los estilos comunes sin reconstruir el contenido de las celdas."""
    from odt_reports import _tag

    headers = table.find(_tag("table:table-header-rows"))
    if headers is not None:
        for cell in headers.iter(_tag("table:table-cell")):
            cell.set(_tag("table:style-name"), f"{prefix}HeaderCell")
            for paragraph in cell.findall(_tag("text:p")):
                previous = paragraph.get(_tag("text:style-name"), "")
                suffix = "Right" if previous.endswith("Right") else ""
                paragraph.set(_tag("text:style-name"), f"{prefix}Header{suffix}")
    for index, row in enumerate(table.findall(_tag("table:table-row"))):
        suffix = "WhiteCell" if index % 2 else "BlueCell"
        for cell in row.findall(_tag("table:table-cell")):
            cell.set(_tag("table:style-name"), prefix + suffix)


def project_document_variables(title: str, settings) -> dict[str, str]:
    """Obtiene los datos comunes actuales sin mantener copias en cada herramienta."""
    municipalities = settings.municipalities
    places = ", ".join(municipality.label for municipality in municipalities)
    return {
        "titulo_proyecto": title,
        "entidad": settings.organization,
        "autor": settings.document_author,
        "cargo": settings.role,
        "expediente": settings.expediente,
        "localidad": settings.location,
        "municipios": places,
        "emplazamiento": places or settings.location,
        "provincias": ", ".join(dict.fromkeys(municipality.province for municipality in municipalities)),
        "comunidades_autonomas": ", ".join(dict.fromkeys(
            municipality.autonomous_community for municipality in municipalities
        )),
    }


def number_headings(source: str) -> str:
    """Numera la jerarquia Markdown sin duplicar la numeracion del texto original."""
    counters = [0] * 6
    result = []
    for line in source.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            level = len(match[1])
            counters[level - 1] += 1
            counters[level:] = [0] * (6 - level)
            for parent in range(level - 1):
                counters[parent] = max(1, counters[parent])
            title = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", match[2]).strip().upper()
            number = ".".join(str(value) for value in counters[:level])
            line = f"{match[1]} {number} {title}"
        result.append(line)
    return "\n".join(result)


def template_paragraph_styles(template: dict) -> dict[str, str]:
    """Localiza estilos de cuerpo y titulos por nombres ODF o su nivel semantico."""
    styles = [
        style for style in template["styles"]
        if style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family") == "paragraph"
    ]
    namespace = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
    result = {}
    for style in styles:
        name = style.get(namespace + "name", "")
        label = style.get(namespace + "display-name", name.removeprefix("Template"))
        label = re.sub(r"_([0-9A-Fa-f]{2})_", lambda match: chr(int(match[1], 16)), label)
        label = "".join(
            char for char in unicodedata.normalize("NFKD", label.casefold())
            if not unicodedata.combining(char)
        )
        if label in {"text body", "body text", "cuerpo", "cuerpo de texto", "texto del cuerpo"}:
            result["body"] = name
        level = style.get(namespace + "default-outline-level")
        match = re.fullmatch(r"(?:heading|titulo)\s*([1-6])", label)
        if match:
            result[f"heading{match[1]}"] = name
        elif level in {"1", "2", "3", "4", "5", "6"}:
            result.setdefault(f"heading{level}", name)
        if label in {"heading", "encabezado", "titulo"}:
            result.setdefault("heading", name)
        if label in {"standard", "normal", "predeterminado"}:
            result.setdefault("body", name)
    return result
