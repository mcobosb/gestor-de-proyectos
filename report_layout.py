"""Maquetación común: tabulaciones en puntos, logo y estilos de las plantillas.

Se usan Arial Narrow y Arial de Windows cuando están disponibles; en otros
sistemas se emplea Helvetica. Las tablas fijan columnas, no insertan espacios
para simular tabuladores. El logo procede de los PDF originales.
"""
from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import date
from io import BytesIO
from html import escape
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from app_paths import application_directory

DEFAULT_TEMPLATE_PATH = application_directory(__file__) / "plantillas" / "plantilla.odt"
PAGE_MARGIN = 25 * 72 / 25.4
LEFT_MARGIN = PAGE_MARGIN
RIGHT_MARGIN = PAGE_MARGIN
PAGE_WIDTH = 595.2755905511812
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
# Las proporciones del modelo original se escalan al ancho disponible.
COLUMN_REFERENCE_WIDTH = PAGE_WIDTH - 85
CERTIFICATION_COLUMN_RATIOS = (50, 90, 22, 45, 44, 35, 60, 48, 59)
CERTIFICATION_SUMMARY_RATIOS = (65, 295, 75, COLUMN_REFERENCE_WIDTH - 435)
DEVIATION_COLUMN_RATIOS = (35, 40, 100, 60, 60, 60, 98)
DEVIATION_HEADERS = ("CAPÍTULO", "CÓDIGO", "RESUMEN", "PLAN", "CERTIFICADO", "DESVIACIÓN", "JUSTIFICACIÓN")
MEASUREMENT_COLUMN_RATIOS = (4, 1, 1, 1, 1, 0.8, 1.2, 0.5)


def report_project_label(title: str, currency: str = "") -> str:
    """Identifica el proyecto con la fecha de emisión y la divisa cuando corresponde."""
    label = f"{title} · {date.today():%d/%m/%Y}"
    return f"{label} · Importes {currency}" if currency else label


def budget_report_columns(report: str):
    """Comparte las cabeceras y proporciones de columna de los modelos PDF y ODT."""
    return {
        "Mediciones": (
            ["CÓDIGO", "UD", "RESUMEN"], [50, 15, CONTENT_WIDTH - 65],
        ),
        "Cuadro de precios 1 y 2 por capitulos": (
            ["CÓDIGO", "UD", "RESUMEN", "PRECIO"], [50, 15, CONTENT_WIDTH - 115, 50],
        ),
        "Descompuestos": (
            ["CÓDIGO", "UD", "RESUMEN", "CANTIDAD", "PRECIO", "SUBTOTAL", "IMPORTE"],
            [50, 20, CONTENT_WIDTH - 275, 45, 50, 50, 60],
        ),
        "Precios Auxiliares": (
            ["CÓDIGO", "UD", "CANTIDAD", "RESUMEN", "PRECIO", "SUBTOTAL", "IMPORTE"],
            [50, 20, 40, CONTENT_WIDTH - 275, 50, 50, 65],
        ),
        "Presupuesto mediciones": (
            ["CÓDIGO", "UD", "RESUMEN", "CANTIDAD", "PRECIO", "IMPORTE"],
            [50, 15, CONTENT_WIDTH - 215, 50, 50, 50],
        ),
    }.get(report, ((), ()))


def _template_length_to_points(value: str) -> float:
    """Convierte una longitud ODF en puntos PDF."""
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)(cm|mm|in|pt|pc)", value)
    if not match:
        raise ValueError(f"Longitud no válida en la plantilla ODT: {value!r}.")
    number = float(match.group(1))
    return number * {"cm": 72 / 2.54, "mm": 72 / 25.4, "in": 72, "pt": 1, "pc": 12}[
        match.group(2)
    ]


def _read_report_template(template_path: str = ""):
    """Carga la imagen de cabecera y el texto del pie de la plantilla ODT indicada."""
    namespaces = {
        "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
        "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
        "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        "xlink": "http://www.w3.org/1999/xlink",
    }
    path = Path(template_path).expanduser() if template_path.strip() else DEFAULT_TEMPLATE_PATH
    if path.suffix.lower() != ".odt":
        raise ValueError(f"La plantilla de informes debe ser un archivo ODT: {path}.")
    try:
        with ZipFile(path) as archive:
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
            if image_width <= 0 or image_height <= 0:
                raise ValueError("La imagen de cabecera debe tener dimensiones positivas.")
            footer = master_page.find("style:footer", namespaces)
            footer_text = " ".join(
                " ".join("".join(paragraph.itertext()).split())
                for paragraph in footer.findall(".//text:p", namespaces)
            ) if footer is not None else ""
            if not footer_text:
                raise ValueError("La plantilla ODT no contiene texto en el pie de página.")
    except (OSError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError(
            f"No se pudo leer la plantilla de informes {path}: {exc}"
        ) from exc
    return image_data, image_path, image_width, image_height, footer_text


def _normalize_template_tables(section_tables, table_sections, styles, automatic, namespaces):
    """Reancla imágenes flotantes a su celda visual y limita tablas al ancho imprimible."""
    def tag(name):
        """Resuelve los nombres ODF usados en la normalización."""
        prefix, local = name.split(":")
        return f"{{{namespaces[prefix]}}}{local}"

    def own_style(element, family, properties_name, values):
        """Crea un estilo específico sin cambiar otros usos del estilo de origen."""
        attribute = tag("draw:style-name" if family == "graphic" else
                        "text:style-name" if family == "paragraph" else "table:style-name")
        original = styles.get(element.get(attribute, ""))
        name = f"TemplateCellLayout{len(automatic)}"
        style = deepcopy(original) if original is not None else ElementTree.Element(
            tag("style:style"), {tag("style:family"): family},
        )
        style.set(tag("style:name"), name)
        properties = style.find(tag(properties_name))
        if properties is None:
            properties = ElementTree.SubElement(style, tag(properties_name))
        for key, value in values.items():
            properties.set(tag(key), value)
        automatic.append(style)
        styles[name] = style
        element.set(attribute, name)

    for section_name, tables in section_tables.items():
        for table, model in zip(tables, table_sections[section_name]):
            widths = model["widths"]
            for row in table.findall(".//table:table-row", namespaces):
                cells = row.findall("table:table-cell", namespaces)
                if len(widths) != len(cells):
                    continue
                for column, cell in enumerate(cells):
                    for frame in list(cell.findall(".//draw:frame", namespaces)):
                        if frame.get(tag("text:anchor-type")) not in {"char", "paragraph"}:
                            continue
                        graphic_style = styles.get(frame.get(tag("draw:style-name"), ""))
                        properties = graphic_style.find(tag("style:graphic-properties")) if graphic_style is not None else None
                        if properties is None or properties.get(tag("style:horizontal-rel")) != "paragraph":
                            continue
                        x = _template_length_to_points(frame.get(tag("svg:x"), "0pt"))
                        width = _template_length_to_points(frame.get(tag("svg:width"), "0pt"))
                        position = sum(widths[:column]) + x
                        destination = next((
                            index for index in range(len(widths))
                            if sum(widths[:index]) <= position
                            and position + width <= sum(widths[:index + 1])
                        ), column)
                        target = cells[destination]
                        if destination == column or "".join(target.itertext()).strip() or target.find(".//draw:frame", namespaces) is not None:
                            continue
                        parent = next(element for element in cell.iter() if frame in list(element))
                        siblings = list(parent)
                        index = siblings.index(frame)
                        if frame.tail:
                            if index:
                                siblings[index - 1].tail = (siblings[index - 1].tail or "") + frame.tail
                            else:
                                parent.text = (parent.text or "") + frame.tail
                        parent.remove(frame)
                        frame.tail = None
                        paragraph = target.find("text:p", namespaces)
                        if paragraph is None:
                            paragraph = ElementTree.SubElement(target, tag("text:p"))
                        paragraph.append(frame)
                        frame.set(tag("text:anchor-type"), "as-char")
                        frame.attrib.pop(tag("svg:x"), None)
                        frame.attrib.pop(tag("svg:y"), None)
                        own_style(paragraph, "paragraph", "style:paragraph-properties", {
                            "fo:text-align": "center",
                        })
                        own_style(frame, "graphic", "style:graphic-properties", {
                            "style:vertical-pos": "middle",
                            "style:vertical-rel": "line",
                            "style:wrap": "none",
                        })
            if model["width"] > CONTENT_WIDTH and widths and all(width > 0 for width in widths):
                scale = CONTENT_WIDTH / sum(widths)
                model["widths"] = [width * scale for width in widths]
                model["width"] = CONTENT_WIDTH
                own_style(table, "table", "style:table-properties", {
                    "style:width": f"{CONTENT_WIDTH:.6f}pt",
                })
                column_index = 0
                for column in table.findall("table:table-column", namespaces):
                    own_style(column, "table-column", "style:table-column-properties", {
                        "style:column-width": f"{model['widths'][column_index]:.6f}pt",
                    })
                    column_index += int(column.get(tag("table:number-columns-repeated"), "1"))


def _read_report_template_header(template_path: str = ""):
    """Carga cabecera y pie ODT completos, sus estilos y recursos gráficos."""
    namespaces = {
        "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
        "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
        "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
        "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
        "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
        "xlink": "http://www.w3.org/1999/xlink",
    }
    path = Path(template_path).expanduser() if template_path.strip() else DEFAULT_TEMPLATE_PATH
    style_name = f"{{{namespaces['style']}}}name"
    try:
        with ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("styles.xml"))
            master_page = root.find(".//style:master-page", namespaces)
            header = master_page.find("style:header", namespaces) if master_page is not None else None
            if header is None:
                raise ValueError("La plantilla ODT no contiene una cabecera.")
            header = deepcopy(header)
            footer = master_page.find("style:footer", namespaces)
            if footer is None:
                raise ValueError("La plantilla ODT no contiene un pie de página.")
            footer = deepcopy(footer)
            styles_section = root.find("office:styles", namespaces)
            automatic_section = root.find("office:automatic-styles", namespaces)
            source_styles = [
                deepcopy(element) for element in styles_section
                if element.tag == f"{{{namespaces['style']}}}style"
            ] if styles_section is not None else []
            default_styles = [
                deepcopy(element) for element in styles_section
                if element.tag == f"{{{namespaces['style']}}}default-style"
            ] if styles_section is not None else []
            source_automatic_styles = (
                list(automatic_section) if automatic_section is not None else []
            )
            style_names = {
                element.get(style_name)
                for element in (*source_styles, *source_automatic_styles)
                if element.get(style_name)
            }
            renamed_styles = {name: f"Template{name}" for name in style_names}

            def remap_style_references(element):
                for key, value in list(element.attrib.items()):
                    local_name = key.rsplit("}", 1)[-1]
                    if key == style_name and value in renamed_styles:
                        element.set(key, renamed_styles[value])
                    elif local_name.endswith("style-name") and value in renamed_styles:
                        element.set(key, renamed_styles[value])
                for child in element:
                    remap_style_references(child)

            for element in (*source_styles, *source_automatic_styles, header, footer):
                remap_style_references(element)

            style_definitions = {
                element.get(style_name): element
                for element in (*source_styles, *source_automatic_styles)
                if element.get(style_name)
            }
            table_sections = {"header": [], "footer": []}
            section_tables = {
                section_name: section.findall(".//table:table", namespaces)
                for section_name, section in (("header", header), ("footer", footer))
            }
            for section_name, tables in section_tables.items():
                for table in tables:
                    table_style = style_definitions.get(table.get(
                        f"{{{namespaces['table']}}}style-name", "",
                    ))
                    table_properties = table_style.find("style:table-properties", namespaces) if table_style is not None else None
                    widths = []
                    for column in table.findall("table:table-column", namespaces):
                        column_style = style_definitions.get(column.get(
                            f"{{{namespaces['table']}}}style-name", "",
                        ))
                        properties = column_style.find("style:table-column-properties", namespaces) if column_style is not None else None
                        width = _template_length_to_points(
                            properties.get(f"{{{namespaces['style']}}}column-width", "")
                        ) if properties is not None and properties.get(f"{{{namespaces['style']}}}column-width") else 0
                        repeated = int(column.get(f"{{{namespaces['table']}}}number-columns-repeated", "1"))
                        widths.extend([width] * repeated)
                    rows = []
                    row_heights = []
                    for row in table.findall(".//table:table-row", namespaces):
                        row_style = style_definitions.get(row.get(
                            f"{{{namespaces['table']}}}style-name", "",
                        ))
                        row_properties = row_style.find("style:table-row-properties", namespaces) if row_style is not None else None
                        height = 0
                        if row_properties is not None:
                            height_value = row_properties.get(
                                f"{{{namespaces['style']}}}row-height",
                                row_properties.get(f"{{{namespaces['style']}}}min-row-height", ""),
                            )
                            if height_value:
                                height = _template_length_to_points(height_value)
                        row_heights.append(height)
                        row_cells = []
                        for cell in row.findall("table:table-cell", namespaces):
                            cell_style = style_definitions.get(cell.get(
                                f"{{{namespaces['table']}}}style-name", "",
                            ))
                            cell_properties = cell_style.find("style:table-cell-properties", namespaces) if cell_style is not None else None
                            cell_borders = {}
                            padding = 0
                            if cell_properties is not None:
                                padding_value = cell_properties.get(f"{{{namespaces['fo']}}}padding", "0pt")
                                padding = _template_length_to_points(padding_value)
                                for edge in ("top", "right", "bottom", "left"):
                                    border = cell_properties.get(
                                        f"{{{namespaces['fo']}}}border-{edge}",
                                        cell_properties.get(f"{{{namespaces['fo']}}}border", "none"),
                                    )
                                    if border not in {"none", "hidden"}:
                                        cell_borders[edge] = border
                            paragraph = cell.find(".//text:p", namespaces)
                            paragraph_style = style_definitions.get(
                                paragraph.get(f"{{{namespaces['text']}}}style-name", "")
                            ) if paragraph is not None else None
                            paragraph_properties = paragraph_style.find(
                                "style:paragraph-properties", namespaces,
                            ) if paragraph_style is not None else None
                            text_properties = paragraph_style.find(
                                "style:text-properties", namespaces,
                            ) if paragraph_style is not None else None
                            font_size_value = text_properties.get(
                                f"{{{namespaces['fo']}}}font-size", "8pt",
                            ) if text_properties is not None else "8pt"
                            try:
                                font_size = _template_length_to_points(font_size_value)
                            except ValueError:
                                font_size = 8
                            row_cells.append({
                                "text": "\n".join(
                                    "".join(paragraph.itertext()).strip()
                                    for paragraph in cell.findall(".//text:p", namespaces)
                                    if "".join(paragraph.itertext()).strip()
                                ),
                                "padding": padding,
                                "borders": cell_borders,
                                "alignment": paragraph_properties.get(
                                    f"{{{namespaces['fo']}}}text-align", "start",
                                ) if paragraph_properties is not None else "start",
                                "font_size": font_size,
                                "bold": text_properties is not None and text_properties.get(
                                    f"{{{namespaces['fo']}}}font-weight"
                                ) == "bold",
                            })
                        rows.append(row_cells)
                    table_width = 0
                    align = "margins"
                    if table_properties is not None:
                        width_value = table_properties.get(f"{{{namespaces['style']}}}width", "")
                        if width_value:
                            table_width = _template_length_to_points(width_value)
                        align = table_properties.get(f"{{{namespaces['table']}}}align", align)
                    table_sections[section_name].append({
                        "width": table_width or sum(widths),
                        "widths": widths,
                        "align": align,
                        "rows": rows,
                        "row_heights": row_heights,
                    })

            _normalize_template_tables(
                section_tables, table_sections, style_definitions,
                source_automatic_styles, namespaces,
            )
            resources = []
            first_frame = None
            graphics = []
            sections = (header, footer)
            images = [image for section in sections for image in section.findall(".//draw:image", namespaces)]
            for index, image in enumerate(images):
                image_path = image.get(f"{{{namespaces['xlink']}}}href", "")
                if not image_path or image_path.startswith("/") or ".." in Path(image_path).parts:
                    raise ValueError("La ruta de una imagen de cabecera ODT no es válida.")
                extension = Path(image_path).suffix.lower() or ".png"
                output_path = (
                    f"Pictures/report-template{extension}" if index == 0 else
                    f"Pictures/report-template-{index}{extension}"
                )
                image.set(f"{{{namespaces['xlink']}}}href", output_path)
                image_data = archive.read(image_path)
                media_type = "image/jpeg" if extension in {".jpg", ".jpeg"} else "image/png"
                resources.append((output_path, media_type, image_data))
                section = next(section for section in sections if image in list(section.iter()))
                frame = next(
                    frame for frame in section.findall(".//draw:frame", namespaces)
                    if image in list(frame.iter())
                )
                table_section = "header" if section is header else "footer"
                table_index = next((
                    index for index, table in enumerate(section_tables[table_section])
                    if image in list(table.iter())
                ), None)
                table_row_index = None
                table_column_index = None
                if table_index is not None:
                    table = section_tables[table_section][table_index]
                    for row_index, row in enumerate(table.findall(".//table:table-row", namespaces)):
                        for column_index, cell in enumerate(row.findall("table:table-cell", namespaces)):
                            if image in list(cell.iter()):
                                table_row_index = row_index
                                table_column_index = column_index
                                break
                        if table_row_index is not None:
                            break
                graphics.append({
                    "section": table_section,
                    "table_index": table_index,
                    "table_row_index": table_row_index,
                    "table_column_index": table_column_index,
                    "data": image_data,
                    "x": _template_length_to_points(
                        frame.get(f"{{{namespaces['svg']}}}x", "0pt")
                    ),
                    "y": _template_length_to_points(
                        frame.get(f"{{{namespaces['svg']}}}y", "0pt")
                    ),
                    "width": _template_length_to_points(
                        frame.get(f"{{{namespaces['svg']}}}width", "")
                    ),
                    "height": _template_length_to_points(
                        frame.get(f"{{{namespaces['svg']}}}height", "")
                    ),
                })
                if section is header and first_frame is None:
                    first_frame = frame
            if first_frame is None:
                raise ValueError("La plantilla ODT no contiene una imagen en la cabecera.")
            header_table_paragraphs = {
                paragraph for table in section_tables["header"]
                for paragraph in table.findall(".//text:p", namespaces)
            }
            footer_table_paragraphs = {
                paragraph for table in section_tables["footer"]
                for paragraph in table.findall(".//text:p", namespaces)
            }
            header_text = "\n".join(
                "".join(paragraph.itertext()).strip()
                for paragraph in header.findall(".//text:p", namespaces)
                if paragraph not in header_table_paragraphs and "".join(paragraph.itertext()).strip()
            )
            footer_text = " ".join(
                " ".join("".join(paragraph.itertext()).split())
                for paragraph in footer.findall(".//text:p", namespaces)
                if paragraph not in footer_table_paragraphs
            )
            frame_x = _template_length_to_points(
                first_frame.get(f"{{{namespaces['svg']}}}x", "0pt")
            )
            frame_y = _template_length_to_points(
                first_frame.get(f"{{{namespaces['svg']}}}y", "0pt")
            )
            font_decls = root.find("office:font-face-decls", namespaces)
            font_faces = [deepcopy(element) for element in font_decls] if font_decls is not None else []
    except (OSError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError(f"No se pudo leer la cabecera de la plantilla ODT {path}: {exc}") from exc
    return {
        "header": header,
        "styles": source_styles,
        "default_styles": default_styles,
        "automatic_styles": source_automatic_styles,
        "font_faces": font_faces,
        "resources": resources,
        "graphics": graphics,
        "tables": table_sections,
        "footer": footer,
        "text": header_text,
        "footer_text": footer_text,
        "frame_x": frame_x,
        "frame_y": frame_y,
    }


def _read_report_template_borders(template_path: str = "") -> dict[str, dict[str, str]]:
    """Lee los bordes horizontales de cabecera y pie, incluidos estilos heredados."""
    namespaces = {
        "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    }
    path = Path(template_path).expanduser() if template_path.strip() else DEFAULT_TEMPLATE_PATH
    try:
        with ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("styles.xml"))
        styles = {
            element.get(f"{{{namespaces['style']}}}name"): element
            for element in root.findall(".//style:style", namespaces)
            if element.get(f"{{{namespaces['style']}}}family") == "paragraph"
        }
        master = root.find(".//style:master-page", namespaces)
        if master is None:
            raise ValueError("La plantilla ODT no contiene una página maestra.")
        borders: dict[str, dict[str, str]] = {"header": {}, "footer": {}}
        for section in borders:
            for paragraph in master.findall(f"style:{section}//text:p", namespaces):
                name = paragraph.get(f"{{{namespaces['text']}}}style-name")
                chain = []
                visited = set()
                while name in styles and name not in visited:
                    visited.add(name)
                    style = styles[name]
                    chain.append(style)
                    name = style.get(f"{{{namespaces['style']}}}parent-style-name")
                properties = {}
                for style in reversed(chain):
                    element = style.find("style:paragraph-properties", namespaces)
                    if element is not None:
                        for edge in ("top", "bottom"):
                            value = element.get(
                                f"{{{namespaces['fo']}}}border-{edge}",
                                element.get(f"{{{namespaces['fo']}}}border"),
                            )
                            if value is not None:
                                properties[edge] = value
                for edge, value in properties.items():
                    if value not in {"none", "hidden"}:
                        borders[section][edge] = value
        return borders
    except (OSError, KeyError, BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError(f"No se pudieron leer las líneas de la plantilla {path}: {exc}") from exc


def replace_template_fields(section, fields: dict[str, str]):
    """Sustituye campos de texto de plantilla incluso fragmentados entre spans."""
    from copy import deepcopy
    import re

    section = deepcopy(section)
    text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    markers = "|".join(re.escape(marker) for marker in fields)
    if not markers:
        return section
    for paragraph in section.iter(f"{{{text_ns}}}p"):
        slots = []

        def collect(element):
            """Recoge texto y colas en su orden real de lectura XML."""
            if element.text:
                slots.append((element, "text", element.text))
            for child in element:
                collect(child)
                if child.tail:
                    slots.append((child, "tail", child.tail))

        collect(paragraph)
        source = "".join(value for _, _, value in slots)
        matches = list(re.finditer(markers, source))
        for match in reversed(matches):
            offset = 0
            start_slot = None
            for element, attribute, value in slots:
                end = offset + len(value)
                if offset <= match.start() < end:
                    start_slot = (element, attribute, match.start() - offset)
                overlap_start = max(0, match.start() - offset)
                overlap_end = min(len(value), match.end() - offset)
                if overlap_start < overlap_end:
                    current = getattr(element, attribute) or ""
                    setattr(element, attribute, current[:overlap_start] + current[overlap_end:])
                offset = end
            if start_slot is None:
                continue
            element, attribute, index = start_slot
            current = getattr(element, attribute) or ""
            setattr(element, attribute, current[:index] + fields[match.group()] + current[index:])
    return section


def replace_footer_fields(section, title: str):
    """Sustituye campos especiales del pie incluso fragmentados entre spans."""
    from copy import deepcopy
    import re
    from xml.etree import ElementTree as ET

    section = deepcopy(section)
    text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    for paragraph in section.iter(f"{{{text_ns}}}p"):
        slots = []

        def collect(element):
            """Recoge texto y colas en su orden real de lectura XML."""
            if element.text:
                slots.append((element, "text", element.text))
            for child in element:
                collect(child)
                if child.tail:
                    slots.append((child, "tail", child.tail))

        collect(paragraph)
        source = "".join(value for _, _, value in slots)
        matches = list(re.finditer(r"\{\{cap[ií]tulo\}\}|\{\{no_pagina\}\}|\{no_pagina\}", source))
        for match in reversed(matches):
            offset = 0
            start_slot = None
            for element, attribute, value in slots:
                end = offset + len(value)
                if offset <= match.start() < end:
                    start_slot = (element, attribute, match.start() - offset)
                overlap_start = max(0, match.start() - offset)
                overlap_end = min(len(value), match.end() - offset)
                if overlap_start < overlap_end:
                    current = getattr(element, attribute) or ""
                    setattr(element, attribute, current[:overlap_start] + current[overlap_end:])
                offset = end
            if start_slot is None:
                continue
            element, attribute, index = start_slot
            current = getattr(element, attribute) or ""
            if match.group() in {"{{capitulo}}", "{{capítulo}}"}:
                setattr(element, attribute, current[:index] + title + current[index:])
            else:
                number = ET.Element(f"{{{text_ns}}}page-number", {
                    f"{{{text_ns}}}select-page": "current",
                })
                number.text = "1"
                number.tail = current[index:]
                setattr(element, attribute, current[:index])
                if attribute == "text":
                    element.insert(0, number)
                else:
                    parent = next(parent for parent in paragraph.iter() if element in list(parent))
                    parent.insert(list(parent).index(element) + 1, number)
    return section


class ReportLayout:
    def __init__(
        self, *, certification: bool = False, template_path: str = "",
        repeat_titles: bool = True, footer_title: str = "", expediente: str = "",
        template_title: str = "",
    ):
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
        self.repeat_titles = repeat_titles
        self.footer_title = footer_title
        self.template_fields = {
            "{{expediente}}": expediente,
            "{{título}}": template_title,
            "{{titulo}}": template_title,
        }
        self.footer_page = None
        (
            image_data, self.template_image_path, self.logo_width, self.logo_height,
            self.template_footer,
        ) = _read_report_template(template_path)
        template_header = _read_report_template_header(template_path)
        self.template_header_section = template_header["header"]
        self.template_footer_section = template_header["footer"]
        self.template_header_text = template_header["text"]
        self.template_footer = template_header["footer_text"]
        self.template_graphics = template_header["graphics"]
        self.template_tables = template_header["tables"]
        self.template_frame_x = template_header["frame_x"]
        self.template_frame_y = template_header["frame_y"]
        self.template_borders = _read_report_template_borders(template_path)
        self.logo = ImageReader(BytesIO(image_data))

    def text(self, value, style=None):
        """Escapa texto para un párrafo PDF y conserva sus saltos de línea."""
        from reportlab.platypus import Paragraph

        return Paragraph(escape(str(value)).replace("\n", "<br/>"), style or self.normal)

    def description(self, value):
        """Crea una descripción con la sangría y tipografía del modelo."""
        return self.text(value, self.description_style)

    def _template_table_flowable(self, template_table, section: str, table_index: int):
        """Reproduce una tabla de cabecera o pie con sus columnas, bordes y texto."""
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Table, TableStyle

        data = []
        cell_styles = []
        for row_index, row in enumerate(template_table["rows"]):
            data_row = []
            style_row = []
            for column_index, cell in enumerate(row):
                alignment = {"center": TA_CENTER, "end": TA_RIGHT, "justify": TA_JUSTIFY}.get(
                    cell["alignment"], 0,
                )
                cell_style = ParagraphStyle(
                    f"template-cell-{row_index}-{column_index}",
                    parent=self.normal,
                    fontName=self.bold_font if cell["bold"] else self.regular_font,
                    fontSize=cell["font_size"],
                    leading=cell["font_size"] + 1,
                    alignment=alignment,
                    leftIndent=self._template_cell_graphic_padding(
                        template_table, section, table_index, row_index, column_index,
                    ),
                )
                value = self._footer_value(cell["text"])
                data_row.append(self.text(value, cell_style) if value else "")
                style_row.append(cell)
            data.append(data_row)
            cell_styles.append(style_row)

        column_widths = template_table["widths"]
        if len(column_widths) != max((len(row) for row in data), default=0):
            count = max((len(row) for row in data), default=1)
            column_widths = [template_table["width"] / count] * count
        row_heights = [
            height if height > 0 else 20 for height in template_table["row_heights"]
        ]
        block = Table(data, colWidths=column_widths, rowHeights=row_heights, hAlign="LEFT")
        commands = [("VALIGN", (0, 0), (-1, -1), "TOP")]
        if section == "header" and table_index == 0:
            commands.append(("VALIGN", (0, 0), (0, -1), "MIDDLE"))
        for row_index, row in enumerate(cell_styles):
            for column_index, cell in enumerate(row):
                padding = cell["padding"]
                for side in ("LEFT", "RIGHT", "TOP", "BOTTOM"):
                    commands.append((f"{side}PADDING", (column_index, row_index),
                                     (column_index, row_index), padding))
                for edge, border in cell["borders"].items():
                    parts = border.split()
                    if len(parts) != 3:
                        continue
                    width = _template_length_to_points(parts[0])
                    color = colors.HexColor(parts[2])
                    command = {
                        "top": "LINEABOVE", "bottom": "LINEBELOW",
                        "left": "LINEBEFORE", "right": "LINEAFTER",
                    }[edge]
                    commands.append((command, (column_index, row_index),
                                     (column_index, row_index), width, color))
        block.setStyle(TableStyle(commands))
        return block

    def _template_cell_graphic_padding(
        self, template_table, section: str, table_index: int, row_index: int, column_index: int,
    ) -> float:
        """Evita que el texto de una celda se superponga a una imagen anclada en ella."""
        column_start = sum(template_table["widths"][:column_index])
        column_end = column_start + template_table["widths"][column_index]
        row_start = sum(template_table["row_heights"][:row_index])
        row_end = row_start + template_table["row_heights"][row_index]
        padding = 0.0
        for graphic in self.template_graphics:
            if graphic["section"] != section or graphic["table_index"] != table_index:
                continue
            if (
                graphic["table_row_index"] == row_index
                and graphic["table_column_index"] == column_index
            ):
                padding = max(padding, graphic["x"] + graphic["width"] + 6)
        return padding

    def _draw_template_tables(self, canvas, section, *, top=None, bottom=None):
        """Draw source template tables and return their PDF origins for anchored frames."""
        from reportlab.lib.pagesizes import A4

        origins = {}
        cursor = top if top is not None else bottom
        for index, template_table in enumerate(self.template_tables[section]):
            block = self._template_table_flowable(template_table, section, index)
            width, height = block.wrap(template_table["width"], 1000)
            if template_table["align"] == "margins":
                x = LEFT_MARGIN + (CONTENT_WIDTH - width) / 2
            elif template_table["align"] == "center":
                x = (PAGE_WIDTH - width) / 2
            elif template_table["align"] == "right":
                x = PAGE_WIDTH - RIGHT_MARGIN - width
            else:
                x = LEFT_MARGIN
            if top is not None:
                y = cursor - height
                cursor = y - 3
            else:
                y = cursor
                cursor = y + height + 3
            block.drawOn(canvas, x, y)
            origins[index] = (x, y, width, height)
        return origins

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

    def table(
        self, headers, rows, ratios, *, bold_rows=(), background=None, rule=False,
        leaders=(), highlighted_rows=(),
    ):
        """Compone columnas fijas con alineación numérica, negritas y reglas opcionales."""
        from decimal import Decimal
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        from bc3_gui import fmt_number

        numeric_headers = {
            "CANTIDAD", "PRECIO", "PRECIO/UD.", "SUBTOTAL", "IMPORTE", "%",
            "% CERTIFICADO", "UDS", "LONGITUD", "ANCHURA", "ALTURA", "K", "PARCIAL",
            "MEDICIÓN TOTAL",
            "PLAN", "CERTIFICADO", "DESVIACIÓN",
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
        for index in highlighted_rows:
            row = index + bool(headers)
            style.append(("BACKGROUND", (0, row), (-1, row), colors.HexColor("#C7D0DF")))
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

        if not self.repeat_titles:
            return PAGE_MARGIN + self.header_logo_height + 12
        heading = self.text(title, self.title_style)
        _, height = heading.wrap(CONTENT_WIDTH, 1000)
        project = self.text(project_title, self.project_style)
        _, project_height = project.wrap(CONTENT_WIDTH, 1000)
        project_y = A4[1] - PAGE_MARGIN - self.header_logo_height - height - project_height - 2
        columns_height = 0
        if column_headers:
            _, columns_height = self.table(column_headers, [], ratios).wrap(CONTENT_WIDTH, 1000)
        return A4[1] - project_y + columns_height + 12

    @property
    def header_logo_height(self) -> float:
        """Reserva la cabecera de plantilla y una separación antes del título."""
        text_height = 0
        if self.template_header_text:
            text_width = max(1, self.template_frame_x - 8) if self.template_frame_x else (
                CONTENT_WIDTH - self.logo_width - 8
            )
            _, text_height = self.text(self._template_value(self.template_header_text), self.normal).wrap(
                text_width, 1000,
            )
        graphic_height = max(
            (graphic["y"] + graphic["height"] for graphic in self.template_graphics
             if graphic["section"] == "header"),
            default=self.template_frame_y + self.logo_height,
        )
        table_height = max((
            sum(template_table["row_heights"])
            for template_table in self.template_tables["header"]
        ), default=0)
        return max(graphic_height, text_height, table_height) + 8

    def bottom_margin(self) -> float:
        """Reserva el pie dentro del margen exterior de 2,5 cm y separa el cuerpo."""
        _, height = self.text(self._footer_value(self.template_footer), self.normal).wrap(CONTENT_WIDTH - 20, 1000)
        table_height = max((
            sum(template_table["row_heights"])
            for template_table in self.template_tables["footer"]
        ), default=0)
        return PAGE_MARGIN + max(22, height + 12, table_height + height + 12)

    def header(self, canvas, document, title, project_title, *, footer="", column_headers=(), ratios=()):
        """Dibuja el logo institucional, títulos, columnas y pie numerado de una página."""
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader

        canvas.saveState()
        top = A4[1]
        heading_y = top - PAGE_MARGIN - self.header_logo_height
        header_tables = self._draw_template_tables(
            canvas, "header", top=top - PAGE_MARGIN,
        )
        if self.template_header_text:
            text_width = max(1, self.template_frame_x - 8) if self.template_frame_x else (
                CONTENT_WIDTH - self.logo_width - 8
            )
            template_text = self.text(self._template_value(self.template_header_text), self.normal)
            _, text_height = template_text.wrap(text_width, 1000)
            template_text.drawOn(canvas, LEFT_MARGIN, top - PAGE_MARGIN - text_height)
        for graphic in self.template_graphics:
            if graphic["section"] != "header":
                continue
            if graphic["table_index"] is not None and graphic["table_index"] in header_tables:
                table_index = graphic["table_index"]
                table_x, table_y, _, table_height = header_tables[table_index]
                template_table = self.template_tables["header"][table_index]
                column_index = graphic["table_column_index"]
                row_index = graphic["table_row_index"]
                graphic_x = (
                    table_x + sum(template_table["widths"][:column_index])
                    + (template_table["widths"][column_index] - graphic["width"]) / 2
                )
                graphic_y = (
                    table_y + table_height
                    - sum(template_table["row_heights"][:row_index])
                    - (template_table["row_heights"][row_index] + graphic["height"]) / 2
                )
            else:
                graphic_x = LEFT_MARGIN + graphic["x"]
                graphic_y = top - PAGE_MARGIN - graphic["y"] - graphic["height"]
            canvas.drawImage(
                ImageReader(BytesIO(graphic["data"])),
                graphic_x, graphic_y,
                width=graphic["width"], height=graphic["height"],
                preserveAspectRatio=False, mask="auto",
            )
        project_y = heading_y
        if self.repeat_titles:
            heading = self.text(title, self.title_style)
            _, height = heading.wrap(CONTENT_WIDTH, 100)
            heading.drawOn(canvas, LEFT_MARGIN, heading_y - height)
            project = self.text(project_title, self.project_style)
            _, project_height = project.wrap(CONTENT_WIDTH, 100)
            project_y = heading_y - height - project_height - 2
            project.drawOn(canvas, LEFT_MARGIN, project_y)
        if column_headers:
            block = self.table(column_headers, [], ratios)
            _, h = block.wrap(CONTENT_WIDTH, 100)
            block.drawOn(canvas, LEFT_MARGIN, project_y - h - 4)
        elif self.repeat_titles:
            canvas.setLineWidth(0.5)
            canvas.line(LEFT_MARGIN, project_y - 4, PAGE_WIDTH - RIGHT_MARGIN, project_y - 4)
        from reportlab.lib.styles import ParagraphStyle

        footer_style = ParagraphStyle(
            "report-footer", parent=self.normal, fontSize=6, leading=7, alignment=1,
        )
        self.footer_page = document.page if self.footer_title else None
        footer_block = self.text(self._footer_value(self.template_footer), footer_style)
        _, footer_height = footer_block.wrap(CONTENT_WIDTH - 20, 1000)
        footer_block.drawOn(canvas, LEFT_MARGIN, PAGE_MARGIN)
        footer_tables = self._draw_template_tables(
            canvas, "footer", bottom=PAGE_MARGIN + footer_height + 4,
        )
        for graphic in self.template_graphics:
            if graphic["section"] != "footer":
                continue
            if graphic["table_index"] is not None and graphic["table_index"] in footer_tables:
                table_index = graphic["table_index"]
                table_x, table_y, _, table_height = footer_tables[table_index]
                template_table = self.template_tables["footer"][table_index]
                column_index = graphic["table_column_index"]
                row_index = graphic["table_row_index"]
                graphic_x = (
                    table_x + sum(template_table["widths"][:column_index])
                    + (template_table["widths"][column_index] - graphic["width"]) / 2
                )
                graphic_y = (
                    table_y + table_height
                    - sum(template_table["row_heights"][:row_index])
                    - (template_table["row_heights"][row_index] + graphic["height"]) / 2
                )
            else:
                graphic_x = LEFT_MARGIN + graphic["x"]
                graphic_y = PAGE_MARGIN + graphic["y"]
            canvas.drawImage(
                ImageReader(BytesIO(graphic["data"])),
                graphic_x, graphic_y,
                width=graphic["width"], height=graphic["height"],
                preserveAspectRatio=False, mask="auto",
            )
        if not self.footer_title:
            canvas.setFont(self.regular_font, 6)
            canvas.drawRightString(PAGE_WIDTH - RIGHT_MARGIN, PAGE_MARGIN + 1, str(document.page))
        self.footer_page = None
        for section, borders in self.template_borders.items():
            for edge, border in borders.items():
                parts = border.split()
                if len(parts) != 3:
                    raise ValueError(f"Borde no válido en la plantilla ODT: {border!r}.")
                width, pattern, color = parts
                from reportlab.lib.colors import HexColor

                canvas.setStrokeColor(HexColor(color))
                line_width = _template_length_to_points(width)
                canvas.setLineWidth(line_width)
                if pattern == "dashed":
                    canvas.setDash(3 * line_width, 2 * line_width)
                elif pattern == "dotted":
                    canvas.setDash(line_width, 2 * line_width)
                elif pattern in {"solid", "double"}:
                    canvas.setDash()
                else:
                    raise ValueError(f"Tipo de borde ODT no compatible: {pattern!r}.")
                header_bottom = top - PAGE_MARGIN - self.header_logo_height + 4
                y = (top - PAGE_MARGIN if edge == "top" else header_bottom) if section == "header" else (
                    PAGE_MARGIN + footer_height + 4 if edge == "top" else PAGE_MARGIN
                )
                if pattern == "double":
                    canvas.setLineWidth(line_width / 3)
                    for offset in (-line_width / 3, line_width / 3):
                        canvas.line(LEFT_MARGIN, y + offset, PAGE_WIDTH - RIGHT_MARGIN, y + offset)
                else:
                    canvas.line(LEFT_MARGIN, y, PAGE_WIDTH - RIGHT_MARGIN, y)
        canvas.restoreState()

    def _footer_value(self, value: str) -> str:
        """Resuelve los campos del pie solo en documentos que lo solicitan."""
        value = self._template_value(value)
        if not self.footer_title:
            return value
        return value.replace("{{capitulo}}", self.footer_title).replace(
            "{{capítulo}}", self.footer_title,
        ).replace(
            "{{no_pagina}}", str(self.footer_page or 1),
        ).replace("{no_pagina}", str(self.footer_page or 1))

    def _template_value(self, value: str) -> str:
        """Resuelve los campos configurables de cabecera y pie."""
        for marker, replacement in self.template_fields.items():
            value = value.replace(marker, replacement)
        return value
