"""Personaliza los ODT y prepara el documento maestro de LibreOffice."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO
from urllib.parse import quote
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from odt_reports import _tag
from document_styles import style_odt_paragraphs, style_role
from report_layout import replace_template_fields
from tools._shared.document_layout import (
    add_odt_blue_table_cells, add_odt_blue_table_headers, style_odt_blue_table,
)

MASTER_NAME = "Documento maestro.odm"
_MASTER_MIME = "application/vnd.oasis.opendocument.text-master"
MASTER_TEMPLATE = Path(__file__).with_name("plantillas") / "maestro" / "plantilla.odt"
MASTER_LOGO = MASTER_TEMPLATE.with_name("logo.jpg")


def _master_link(path: Path) -> str:
    """Construye la URL relativa al interior del paquete ODM de LibreOffice."""
    return "../" + quote(path.as_posix(), safe="/")


def _styles(xml: bytes, project: str, section: str, text_styles) -> bytes:
    """Añade cabecera y pie con un campo de página, no un número literal."""
    root = ET.fromstring(xml)
    automatic = root.find(_tag("office:automatic-styles"))
    if automatic is None:
        automatic = ET.SubElement(root, _tag("office:automatic-styles"))
    layout = ET.SubElement(automatic, _tag("style:page-layout"), {
        _tag("style:name"): "EstructuraPagina",
    })
    ET.SubElement(layout, _tag("style:page-layout-properties"), {
        _tag("fo:page-width"): "21cm", _tag("fo:page-height"): "29.7cm",
        _tag("fo:margin"): "2cm", _tag("style:print-orientation"): "portrait",
    })
    masters = root.find(_tag("office:master-styles"))
    if masters is None:
        masters = ET.SubElement(root, _tag("office:master-styles"))
    page = ET.SubElement(masters, _tag("style:master-page"), {
        _tag("style:name"): "Standard", _tag("style:page-layout-name"): "EstructuraPagina",
    })
    header = ET.SubElement(page, _tag("style:header"))
    ET.SubElement(header, _tag("text:p")).text = project
    footer = ET.SubElement(page, _tag("style:footer"))
    paragraph = ET.SubElement(footer, _tag("text:p"))
    paragraph.text = f"{section.upper()} · PÁGINA "
    ET.SubElement(paragraph, _tag("text:page-number"), {
        _tag("text:select-page"): "current",
    }).text = "1"
    if text_styles:
        for role, part in (("header", header), ("footer", footer)):
            style_odt_paragraphs(part, automatic, text_styles, _tag, lambda _element, role=role: role)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _master_content(xml: bytes, links: list[Path], folder: Path) -> bytes:
    """Vincula los ODT por ruta relativa e incluye su contenido inicial en caché."""
    root = ET.fromstring(xml)
    body = root.find(f"{_tag('office:body')}/{_tag('office:text')}")
    if body is None:
        raise ValueError("La plantilla no contiene un cuerpo de texto ODF.")
    body.clear()
    body.set(_tag("text:global"), "true")
    for index, path in enumerate(links, 1):
        section = ET.SubElement(body, _tag("text:section"), {
            _tag("text:name"): f"Documento {index}",
        })
        ET.SubElement(section, _tag("text:section-source"), {
            _tag("xlink:href"): _master_link(path), _tag("text:filter-name"): "writer8",
        })
        with ZipFile(folder / path) as document:
            linked_root = ET.fromstring(document.read("content.xml"))
        linked_body = linked_root.find(f"{_tag('office:body')}/{_tag('office:text')}")
        if linked_body is None:
            raise ValueError(f"El documento vinculado no contiene texto ODF: {path.name}")
        source_styles = linked_root.find(_tag("office:automatic-styles"))
        if source_styles is not None:
            typography = [style for style in source_styles
                          if style.get(_tag("style:name"), "").startswith("ProjectTypography_")]
            if typography:
                cached = root.find(_tag("office:automatic-styles"))
                if cached is None:
                    cached = ET.Element(_tag("office:automatic-styles"))
                    root.insert(list(root).index(root.find(_tag("office:body"))), cached)
                known = {style.get(_tag("style:name")) for style in cached}
                cached.extend(style for style in typography if style.get(_tag("style:name")) not in known)
        section.extend(linked_body)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_office_document(
    source: Path, output: BinaryIO, project: str, section: str, *,
    links: list[Path] | None = None, link_folder: Path = Path(), text_styles=None,
) -> None:
    """Copia el paquete y actualiza las partes ODF que dependen del proyecto."""
    with ZipFile(source) as original, ZipFile(output, "w", ZIP_DEFLATED) as result:
        mime = original.read("mimetype") if links is None else _MASTER_MIME
        result.writestr("mimetype", mime, compress_type=ZIP_STORED)
        for name in original.namelist():
            if name == "mimetype":
                continue
            data = original.read(name)
            if name == "styles.xml":
                data = _styles(data, project, section, text_styles)
            elif name == "content.xml":
                if links is not None:
                    data = _master_content(data, links, link_folder)
                if text_styles:
                    data = _style_content(data, text_styles)
            elif name == "META-INF/manifest.xml" and links is not None:
                manifest = ET.fromstring(data)
                for entry in manifest:
                    if entry.get(_tag("manifest:full-path")) == "/":
                        entry.set(_tag("manifest:media-type"), _MASTER_MIME)
                data = ET.tostring(manifest, encoding="utf-8", xml_declaration=True)
            result.writestr(name, data)


def _style_content(data: bytes, text_styles) -> bytes:
    """Aplica título, niveles y cuerpo a los textos editables de las plantillas."""
    root = ET.fromstring(data)
    automatic = root.find(_tag("office:automatic-styles"))
    if automatic is None:
        automatic = ET.Element(_tag("office:automatic-styles"))
        root.insert(list(root).index(root.find(_tag("office:body"))), automatic)

    def role(element):
        """Respeta el nivel de esquema y el título explícito de cada plantilla."""
        name = element.get(_tag("text:style-name"), "")
        if name.startswith("ProjectTypography_"):
            return ""
        if element.tag == _tag("text:h"):
            level = min(6, max(1, int(element.get(_tag("text:outline-level"), "1"))))
            return style_role(name, odt=True) or f"heading{level}"
        return "body"

    # El maestro usa el mismo nombre determinista que los estilos automáticos de sus hijos.
    style_odt_paragraphs(root.find(_tag("office:body")), automatic, text_styles, _tag, role)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _set_paragraph_text(
    paragraph, value: str, href: str = "", *, indent: bool = False,
) -> None:
    """Reemplaza el texto manteniendo el estilo del párrafo y sus enlaces."""
    template_link = paragraph.find(_tag("text:a"))
    link_attributes = dict(template_link.attrib) if template_link is not None else {}
    link_attributes.pop(_tag("xlink:href"), None)
    link_run = (
        next(template_link.iter(_tag("text:span")), None)
        if template_link is not None else None
    )
    run_attributes = dict(link_run.attrib) if link_run is not None else {}
    for child in list(paragraph):
        paragraph.remove(child)
    paragraph.text = None
    tab = ET.SubElement(paragraph, _tag("text:tab")) if indent else None
    if href:
        link_attributes.update({
            _tag("xlink:href"): href,
            _tag("xlink:type"): "simple",
        })
        link = ET.SubElement(paragraph, _tag("text:a"), link_attributes)
        if run_attributes:
            ET.SubElement(link, _tag("text:span"), run_attributes).text = value
        else:
            link.text = value
    elif run_attributes:
        ET.SubElement(paragraph, _tag("text:span"), run_attributes).text = value
    elif tab is not None:
        tab.tail = value
    else:
        paragraph.text = value


def _add_page_number_fields(section, page_paragraphs: set[int]) -> None:
    """Convierte los marcadores de página de la plantilla en campos ODF dinámicos."""
    paragraphs = list(section.iter(_tag("text:p")))
    for index in page_paragraphs:
        if index >= len(paragraphs):
            continue
        paragraph = paragraphs[index]
        if paragraph.find(f".//{_tag('text:page-number')}") is None:
            ET.SubElement(paragraph, _tag("text:page-number"), {
                _tag("text:select-page"): "current",
            }).text = "1"


def _is_traceability_header(row) -> bool:
    """Reconoce el encabezado por sus columnas de código, documento y fecha."""
    values = {
        "".join(cell.itertext()).strip()
        for cell in row.findall(_tag("table:table-cell"))
    }
    return {"CÓDIGO", "DOCUMENTO", "FECHA EDICIÓN"}.issubset(values)


def _fill_traceability(
    root, rows: list[tuple[str, str, str, str, str, str]],
) -> None:
    """Puebla la tabla de trazabilidad y conserva su encabezado y formato."""
    table = None
    header_index = -1
    for candidate in root.iter(_tag("table:table")):
        for index, row in enumerate(candidate.findall(_tag("table:table-row"))):
            if _is_traceability_header(row):
                table, header_index = candidate, index
                break
        if table is not None:
            break
    if table is None:
        raise ValueError("La plantilla del documento maestro no contiene la tabla de trazabilidad.")

    table_rows = table.findall(_tag("table:table-row"))
    if header_index + 1 >= len(table_rows):
        raise ValueError("La tabla de trazabilidad no contiene una fila de datos con formato.")
    prototype = table_rows[header_index + 1]
    for row in table_rows[header_index + 1:]:
        table.remove(row)
    header = table_rows[header_index]
    header_position = list(table).index(header)
    header_group = ET.Element(_tag("table:table-header-rows"))
    table.remove(header)
    header_group.append(header)
    table.insert(header_position, header_group)
    for values in rows:
        row = deepcopy(prototype)
        cells = row.findall(_tag("table:table-cell"))
        if len(cells) != len(values):
            raise ValueError("La fila de trazabilidad no coincide con las columnas de la plantilla.")
        for cell, value in zip(cells, values):
            paragraphs = cell.findall(_tag("text:p"))
            if not paragraphs:
                paragraphs = [ET.SubElement(cell, _tag("text:p"))]
            _set_paragraph_text(paragraphs[0], value)
            for paragraph in paragraphs[1:]:
                cell.remove(paragraph)
        table.append(row)


def _style_traceability(root) -> None:
    """Aplica a la tabla de trazabilidad el estilo azul compartido de los anejos."""
    table = next((
        candidate for candidate in root.iter(_tag("table:table"))
        if any(
            _is_traceability_header(row)
            for row in candidate.iter(_tag("table:table-row"))
        )
    ), None)
    if table is None:
        raise ValueError("No se encontró la tabla de trazabilidad del documento maestro.")
    automatic = root.find(_tag("office:automatic-styles"))
    if automatic is None:
        body = root.find(_tag("office:body"))
        if body is None:
            raise ValueError("La plantilla no contiene el cuerpo del documento ODF.")
        automatic = ET.Element(_tag("office:automatic-styles"))
        root.insert(list(root).index(body), automatic)
    styles = SimpleNamespace(automatic_styles=automatic)
    add_odt_blue_table_cells(styles, "Traceability")
    add_odt_blue_table_headers(styles, "Traceability")
    style_odt_blue_table(table, "Traceability")


def _paragraph_master_page(root, paragraph) -> str:
    """Resuelve el estilo de página asociado al estilo de un párrafo."""
    paragraph_style = paragraph.get(_tag("text:style-name"))
    for style in root.iter(_tag("style:style")):
        if (
            style.get(_tag("style:family")) == "paragraph"
            and style.get(_tag("style:name")) == paragraph_style
        ):
            return style.get(_tag("style:master-page-name"), "")
    return ""


def _traceability_master_page(root) -> str:
    """Encuentra el estilo de página que precede a la tabla de trazabilidad."""
    body = root.find(f"{_tag('office:body')}/{_tag('office:text')}")
    if body is None:
        raise ValueError("La plantilla no contiene un cuerpo de texto ODF.")
    table_index = next((
        index for index, child in enumerate(body)
        if child.tag == _tag("table:table")
        and any(
            _is_traceability_header(row)
            for row in child.iter(_tag("table:table-row"))
        )
    ), None)
    if table_index is None:
        raise ValueError("La plantilla del documento maestro no contiene la tabla de trazabilidad.")
    for child in reversed(list(body)[:table_index]):
        if child.tag == _tag("text:p"):
            page_name = _paragraph_master_page(root, child)
            if page_name:
                return page_name
    raise ValueError("La plantilla no asigna una página maestra a la hoja de trazabilidad.")


def _index_master_page(root) -> str:
    """Encuentra la página maestra asociada al título del índice general."""
    body = root.find(f"{_tag('office:body')}/{_tag('office:text')}")
    if body is None:
        raise ValueError("La plantilla no contiene un cuerpo de texto ODF.")
    heading = next((
        paragraph for paragraph in body.iter(_tag("text:p"))
        if "ÍNDICE GENERAL DEL PROYECTO" in "".join(paragraph.itertext()).upper()
    ), None)
    page_name = _paragraph_master_page(root, heading) if heading is not None else ""
    if not page_name:
        raise ValueError("La plantilla no asigna una página maestra al índice general.")
    return page_name


def _set_page_master_footer(root, page, label: str) -> None:
    """Asigna el rótulo y el número dinámico a los pies de una página maestra."""
    for footer_name in (
        "style:footer", "style:footer-first", "style:footer-left", "style:footer-right",
    ):
        footer = page.find(_tag(footer_name))
        if footer is None:
            continue
        paragraphs = list(footer.iter(_tag("text:p")))
        if not paragraphs:
            continue
        paragraph = next((
            item for item in paragraphs
            if item.find(".//" + _tag("text:page-number")) is not None
        ), paragraphs[0])
        page_number = paragraph.find(".//" + _tag("text:page-number"))
        page_number = deepcopy(page_number) if page_number is not None else ET.Element(
            _tag("text:page-number"),
            {_tag("text:select-page"): "current"},
        )
        for child in list(paragraph):
            paragraph.remove(child)
        paragraph.text = f"{label} - "
        page_number.text = page_number.text or "1"
        paragraph.append(page_number)


def _set_template_section_footers(
    root, traceability_name: str, index_name: str,
) -> None:
    """Distingue los pies de trazabilidad e índice y mantiene el índice entre páginas."""
    masters = root.find(_tag("office:master-styles"))
    if masters is None:
        raise ValueError("La plantilla no contiene estilos de página maestros.")
    pages = {
        page.get(_tag("style:name"), ""): page
        for page in masters.findall(_tag("style:master-page"))
    }
    if traceability_name not in pages or index_name not in pages:
        raise ValueError("La plantilla no contiene las páginas maestras de trazabilidad e índice.")
    if traceability_name == index_name:
        raise ValueError("La plantilla asigna la misma página maestra al índice y a la trazabilidad.")
    traceability_page = pages[traceability_name]
    _set_page_master_footer(root, traceability_page, "HOJA DE TRAZABILIDAD")
    traceability_page.set(_tag("style:next-style-name"), traceability_name)

    current_name = index_name
    visited = set()
    while current_name in pages and current_name not in visited:
        visited.add(current_name)
        page = pages[current_name]
        _set_page_master_footer(root, page, "ÍNDICE GENERAL DEL PROYECTO")
        next_name = page.get(_tag("style:next-style-name"), "")
        if not next_name or next_name not in pages or next_name in visited:
            page.set(_tag("style:next-style-name"), current_name)
            break
        current_name = next_name


def _new_logo_frame(resource: str, name: str):
    """Crea un marco de imagen en línea para una plantilla sin un marco de logo."""
    frame = ET.Element(_tag("draw:frame"), {
        _tag("draw:name"): name,
        _tag("text:anchor-type"): "as-char",
        _tag("svg:width"): "4.683cm",
        _tag("svg:height"): "1.633cm",
    })
    ET.SubElement(frame, _tag("draw:image"), {
        _tag("xlink:href"): resource,
        _tag("xlink:type"): "simple",
        _tag("xlink:show"): "embed",
        _tag("xlink:actuate"): "onLoad",
        _tag("draw:mime-type"): "image/jpeg",
    })
    return frame


def _insert_template_logos(root, resource: str) -> None:
    """Inserta el logo seleccionado en los marcadores de las cabeceras."""
    masters = root.find(_tag("office:master-styles"))
    if masters is None:
        raise ValueError("La plantilla no contiene estilos de página maestros.")
    header_sections = [
        section
        for page in masters.findall(_tag("style:master-page"))
        for section in page
        if section.tag.rsplit("}", 1)[-1].startswith("header")
    ]
    template_frame = next((
        frame for section in header_sections
        for frame in section.iter(_tag("draw:frame"))
        if frame.find(".//" + _tag("draw:image")) is not None
    ), None)
    placed = False
    frame_number = 0
    for section in header_sections:
        for frame in section.iter(_tag("draw:frame")):
            image = frame.find(".//" + _tag("draw:image"))
            if image is None:
                continue
            image.set(_tag("xlink:href"), resource)
            image.set(_tag("draw:mime-type"), "image/jpeg")
            placed = True
        for paragraph in section.iter(_tag("text:p")):
            content = "".join(paragraph.itertext())
            if "{{logo}}" not in content:
                continue
            prefix, suffix = content.split("{{logo}}", 1)
            for child in list(paragraph):
                paragraph.remove(child)
            paragraph.text = prefix or None
            frame_number += 1
            if template_frame is None:
                frame = _new_logo_frame(resource, f"ProjectLogoHeader{frame_number}")
            else:
                frame = deepcopy(template_frame)
                frame.set(_tag("draw:name"), f"ProjectLogoHeader{frame_number}")
                frame.set(_tag("text:anchor-type"), "as-char")
                frame.attrib.pop(_tag("svg:x"), None)
                frame.attrib.pop(_tag("svg:y"), None)
                image = frame.find(".//" + _tag("draw:image"))
                if image is None:
                    image = ET.SubElement(frame, _tag("draw:image"))
                image.set(_tag("xlink:href"), resource)
                image.set(_tag("xlink:type"), "simple")
                image.set(_tag("xlink:show"), "embed")
                image.set(_tag("xlink:actuate"), "onLoad")
                image.set(_tag("draw:mime-type"), "image/jpeg")
            paragraph.append(frame)
            frame.tail = suffix or None
            placed = True
    if not placed:
        raise ValueError(
            "La plantilla del documento maestro debe incluir un logo en la cabecera "
            "o el marcador {{logo}}."
        )


def _logo_resource_name(existing_names) -> str:
    """Elige un nombre de recurso de imagen que no colisione con la plantilla."""
    existing = set(existing_names)
    base = "Pictures/ProjectLogo_estructura_documental.jpg"
    if base not in existing:
        return base
    index = 2
    while f"Pictures/ProjectLogo_estructura_documental_{index}.jpg" in existing:
        index += 1
    return f"Pictures/ProjectLogo_estructura_documental_{index}.jpg"


def _document_footer_titles(
    entries: list[dict[str, str]], links: list[Path],
) -> list[str]:
    """Obtiene el rótulo del índice que corresponde a cada documento enlazado."""
    titles = {
        entry["href"]: entry["text"].strip().rstrip(". ")
        for entry in entries
        if entry.get("href") and entry.get("text", "").strip()
    }
    return [
        titles.get(_master_link(path), path.stem).upper()
        for path in links
    ]


def _add_document_master_pages(root, titles: list[str]) -> None:
    """Crea un estilo de página por documento con su nombre en el pie."""
    if not titles:
        return
    masters = root.find(_tag("office:master-styles"))
    if masters is None:
        raise ValueError("La plantilla no contiene estilos de página maestros.")
    base = next((
        page for page in masters.findall(_tag("style:master-page"))
        if page.find(_tag("style:footer")) is not None
        and any(
            paragraph.find(".//" + _tag("text:page-number")) is not None
            for paragraph in page.iter(_tag("text:p"))
        )
    ), None)
    if base is None:
        raise ValueError("La plantilla no contiene un pie de página numerado.")
    for index, title in enumerate(titles, 1):
        name = f"ProjectDocumentPage{index}"
        page = deepcopy(base)
        page.set(_tag("style:name"), name)
        page.set(_tag("style:next-style-name"), name)
        for footer_name in ("style:footer", "style:footer-first"):
            footer = page.find(_tag(footer_name))
            if footer is None:
                continue
            paragraph = next((
                item for item in footer.iter(_tag("text:p"))
                if item.find(".//" + _tag("text:page-number")) is not None
            ), None)
            if paragraph is None:
                continue
            page_number = paragraph.find(".//" + _tag("text:page-number"))
            if page_number is None:
                continue
            page_number = deepcopy(page_number)
            for child in list(paragraph):
                paragraph.remove(child)
            paragraph.text = f"{title} - "
            paragraph.append(page_number)
        masters.append(page)


def _style_document_page_breaks(root, master_page_names: list[str]) -> None:
    """Inicia cada documento enlazado en una página con su pie correspondiente."""
    body = root.find(f"{_tag('office:body')}/{_tag('office:text')}")
    if body is None:
        raise ValueError("La plantilla no contiene un cuerpo de texto ODF.")
    sections = [
        child for child in body
        if child.tag == _tag("text:section")
        and child.get(_tag("text:name"), "").startswith("Documento vinculado ")
    ]
    if not sections:
        return
    if len(sections) != len(master_page_names):
        raise ValueError("Los pies de página no coinciden con los documentos enlazados.")
    automatic = root.find(_tag("office:automatic-styles"))
    if automatic is None:
        office_body = root.find(_tag("office:body"))
        if office_body is None:
            raise ValueError("La plantilla no contiene el cuerpo del documento ODF.")
        automatic = ET.Element(_tag("office:automatic-styles"))
        root.insert(list(root).index(office_body), automatic)
    for page_index, (section, master_page_name) in enumerate(
        zip(sections, master_page_names), 1,
    ):
        name = f"ProjectDocumentPageBreak{page_index}"
        position = list(body).index(section)
        if position == 0 or body[position - 1].tag != _tag("text:p"):
            paragraph = ET.Element(_tag("text:p"))
            body.insert(position, paragraph)
        else:
            paragraph = body[position - 1]
        paragraph.set(_tag("text:style-name"), name)
        style = ET.SubElement(automatic, _tag("style:style"), {
            _tag("style:name"): name,
            _tag("style:family"): "paragraph",
            _tag("style:master-page-name"): master_page_name,
        })
        ET.SubElement(style, _tag("style:paragraph-properties"), {
            _tag("fo:break-before"): "page",
            _tag("fo:line-height"): "0pt",
            _tag("fo:margin-top"): "0pt",
            _tag("fo:margin-bottom"): "0pt",
        })


def _append_index_and_sections(
    root, entries: list[dict[str, str]], links: list[Path], folder: Path,
) -> None:
    """Actualiza el índice estático y añade secciones enlazadas al contenido maestro."""
    body = root.find(f"{_tag('office:body')}/{_tag('office:text')}")
    if body is None:
        raise ValueError("La plantilla no contiene un cuerpo de texto ODF.")
    body.set(_tag("text:global"), "true")
    children = list(body)
    heading_index = next((
        index for index, child in enumerate(children)
        if child.tag == _tag("text:p")
        and "ÍNDICE GENERAL DEL PROYECTO" in "".join(child.itertext()).upper()
    ), None)
    if heading_index is None:
        raise ValueError("La plantilla no contiene el índice general del proyecto.")
    samples = {}
    for child in children:
        if child.tag != _tag("text:p"):
            continue
        style = child.get(_tag("text:style-name"), "")
        current = samples.get(style)
        if current is None or (
            not "".join(current.itertext()).strip()
            and "".join(child.itertext()).strip()
        ):
            samples[style] = child
    for child in children[heading_index + 1:]:
        body.remove(child)

    insertion = heading_index + 1
    for entry in entries:
        style = entry.get("style", "P19")
        paragraph = deepcopy(samples.get(style, samples.get("P19", ET.Element(_tag("text:p")))))
        indent = next(paragraph.iter(_tag("text:tab")), None) is not None
        _set_paragraph_text(
            paragraph, entry["text"], entry.get("href", ""), indent=indent,
        )
        body.insert(insertion, paragraph)
        insertion += 1

    known_styles = root.find(_tag("office:automatic-styles"))
    for index, path in enumerate(links, 1):
        section = ET.Element(_tag("text:section"), {
            _tag("text:name"): f"Documento vinculado {index}",
        })
        ET.SubElement(section, _tag("text:section-source"), {
            _tag("xlink:href"): _master_link(path),
            _tag("text:filter-name"): "writer8",
        })
        with ZipFile(folder / path) as document:
            linked_root = ET.fromstring(document.read("content.xml"))
        linked_body = linked_root.find(f"{_tag('office:body')}/{_tag('office:text')}")
        if linked_body is None:
            raise ValueError(f"El documento vinculado no contiene texto ODF: {path.name}")
        source_styles = linked_root.find(_tag("office:automatic-styles"))
        if source_styles is not None:
            typography = [
                style for style in source_styles
                if style.get(_tag("style:name"), "").startswith("ProjectTypography_")
            ]
            if typography:
                if known_styles is None:
                    known_styles = ET.Element(_tag("office:automatic-styles"))
                    body_index = list(root).index(root.find(_tag("office:body")))
                    root.insert(body_index, known_styles)
                known = {style.get(_tag("style:name")) for style in known_styles}
                known_styles.extend(
                    style for style in typography
                    if style.get(_tag("style:name")) not in known
                )
        section.extend(deepcopy(list(linked_body)))
        body.append(ET.Element(_tag("text:p")))
        body.append(section)


def _add_annex_covers(root, entries: list[dict[str, str]], links: list[Path]) -> None:
    """Inserta portadas propias del maestro, fuera de las secciones vinculadas."""
    body = root.find(f"{_tag('office:body')}/{_tag('office:text')}")
    automatic = root.find(_tag("office:automatic-styles"))
    if body is None or automatic is None:
        raise ValueError("El maestro no contiene el cuerpo o los estilos de los documentos.")
    annexes = {
        entry["href"]: entry for entry in entries if "annex_number" in entry
    }
    if not annexes:
        return
    title_style = ET.SubElement(automatic, _tag("style:style"), {
        _tag("style:name"): "ProjectAnnexCoverTitle",
        _tag("style:family"): "paragraph",
    })
    ET.SubElement(title_style, _tag("style:paragraph-properties"), {
        _tag("fo:text-align"): "end", _tag("fo:margin-top"): "0.6cm",
        _tag("fo:margin-bottom"): "0cm", _tag("fo:line-height"): "115%",
    })
    ET.SubElement(title_style, _tag("style:text-properties"), {
        _tag("fo:font-size"): "32pt", _tag("fo:font-weight"): "bold",
        _tag("style:font-size-asian"): "32pt", _tag("style:font-size-complex"): "32pt",
    })
    sections = {
        child.get(_tag("text:name")): child for child in body
        if child.tag == _tag("text:section")
    }
    for index, path in enumerate(links, 1):
        entry = annexes.get(_master_link(path))
        if entry is None:
            continue
        number = entry["annex_number"]
        title = entry.get("annex_title", "").strip()
        if not number.isdecimal() or int(number) < 1 or not title:
            raise ValueError("La portada de un anejo no tiene número o título válido.")
        section = sections[f"Documento vinculado {index}"]
        position = list(body).index(section) - 1
        number_style = ET.SubElement(automatic, _tag("style:style"), {
            _tag("style:name"): f"ProjectAnnexCoverNumber{index}",
            _tag("style:family"): "paragraph",
            _tag("style:master-page-name"): f"ProjectDocumentPage{index}",
        })
        ET.SubElement(number_style, _tag("style:paragraph-properties"), {
            _tag("fo:break-before"): "page", _tag("fo:keep-with-next"): "always",
            _tag("fo:text-align"): "end", _tag("fo:margin-top"): "6cm",
            _tag("fo:margin-bottom"): "0cm", _tag("fo:line-height"): "115%",
        })
        ET.SubElement(number_style, _tag("style:text-properties"), {
            _tag("fo:font-size"): "26pt", _tag("fo:font-weight"): "bold",
            _tag("style:font-size-asian"): "26pt", _tag("style:font-size-complex"): "26pt",
        })
        number_paragraph = ET.Element(_tag("text:p"), {
            _tag("text:style-name"): f"ProjectAnnexCoverNumber{index}",
        })
        number_paragraph.text = f"ANEJO Nº {number}"
        title_paragraph = ET.Element(_tag("text:p"), {
            _tag("text:style-name"): "ProjectAnnexCoverTitle",
        })
        title_paragraph.text = title.upper()
        body.insert(position, number_paragraph)
        body.insert(position + 1, title_paragraph)


def write_project_master(
    source: Path,
    output: Path,
    *,
    fields: dict[str, str],
    index_entries: list[dict[str, str]],
    traceability: list[tuple[str, str, str, str, str, str]],
    links: list[Path],
    folder: Path,
    text_styles=None,
    logo: Path = MASTER_LOGO,
) -> None:
    """Construye el ODM desde la carátula elegida, sin alterar sus recursos originales."""
    if not source.is_file():
        raise FileNotFoundError(f"Falta la plantilla del documento maestro: {source}")
    if not logo.is_file():
        raise FileNotFoundError(f"Falta el logo de la plantilla del documento maestro: {logo}")

    sample_title = (
        "GEX2026/36718 – Proyecto de Construcción - Glorieta en cruce Ronda del Valle "
        "con calles María Zambrano y Juan Manuel de Aréjula en Lucena (Córdoba)."
    )
    replacements = {
        **fields,
        sample_title: fields.get("{{título}}", ""),
        "{{logo}}": "",
        "{{no_pagina}}": "",
    }
    footer_titles = _document_footer_titles(index_entries, links)
    master_page_names = [
        f"ProjectDocumentPage{index}" for index in range(1, len(footer_titles) + 1)
    ]
    with ZipFile(source) as original, ZipFile(output, "w", ZIP_DEFLATED) as result:
        template_content = ET.fromstring(original.read("content.xml"))
        traceability_page = _traceability_master_page(template_content)
        index_page = _index_master_page(template_content)
        logo_resource = _logo_resource_name(original.namelist())
        result.writestr("mimetype", _MASTER_MIME, compress_type=ZIP_STORED)
        for name in original.namelist():
            if name == "mimetype":
                continue
            data = original.read(name)
            if name in {"content.xml", "styles.xml"}:
                root = ET.fromstring(data)
                page_paragraphs = {
                    index for index, paragraph in enumerate(root.iter(_tag("text:p")))
                    if "{{no_pagina}}" in "".join(paragraph.itertext())
                }
                if name == "styles.xml":
                    _insert_template_logos(root, logo_resource)
                root = replace_template_fields(root, replacements)
                if name == "content.xml":
                    _fill_traceability(root, traceability)
                    _append_index_and_sections(root, index_entries, links, folder)
                    _add_page_number_fields(root, page_paragraphs)
                    if text_styles:
                        data = _style_content(
                            ET.tostring(root, encoding="utf-8"), text_styles,
                        )
                        root = ET.fromstring(data)
                    _style_traceability(root)
                    _style_document_page_breaks(root, master_page_names)
                    _add_annex_covers(root, index_entries, links)
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                    result.writestr(name, data)
                    continue
                else:
                    _add_page_number_fields(root, page_paragraphs)
                    _set_template_section_footers(
                        root, traceability_page, index_page,
                    )
                    _add_document_master_pages(root, footer_titles)
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            elif name == "META-INF/manifest.xml":
                manifest = ET.fromstring(data)
                root_entry = next(
                    (entry for entry in manifest
                     if entry.get(_tag("manifest:full-path")) == "/"),
                    None,
                )
                if root_entry is None:
                    raise ValueError("El paquete de la plantilla no declara su tipo de documento.")
                root_entry.set(_tag("manifest:media-type"), _MASTER_MIME)
                ET.SubElement(manifest, _tag("manifest:file-entry"), {
                    _tag("manifest:full-path"): logo_resource,
                    _tag("manifest:media-type"): "image/jpeg",
                })
                data = ET.tostring(manifest, encoding="utf-8", xml_declaration=True)
            elif name == "meta.xml":
                metadata = ET.fromstring(data)
                title = metadata.find(f".//{_tag('dc:title')}")
                if title is not None:
                    title.text = fields.get("{{título}}", "")
                data = ET.tostring(metadata, encoding="utf-8", xml_declaration=True)
            result.writestr(name, data)
        result.writestr(logo_resource, logo.read_bytes())


def renumber_annex(path: Path, previous: int, current: int) -> None:
    """Actualiza el número del anejo en títulos, pies y metadatos de un ODT."""
    if previous == current:
        return
    fields = {}
    for label in ("ANEJO Nº", "ANEJO N.º", "ANEJO N°", "ANEJO"):
        for separator in (" ", ""):
            fields[f"{label}{separator}{previous}"] = f"{label}{separator}{current}"
    temporary = path.with_suffix(path.suffix + ".tmp")
    with ZipFile(path) as original, ZipFile(temporary, "w", ZIP_DEFLATED) as result:
        for name in original.namelist():
            data = original.read(name)
            if name.endswith(".xml"):
                root = ET.fromstring(data)
                root = replace_template_fields(root, fields)
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            result.writestr(name, data, compress_type=(
                ZIP_STORED if name == "mimetype" else ZIP_DEFLATED
            ))
    temporary.replace(path)
