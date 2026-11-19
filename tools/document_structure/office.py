"""Personaliza los ODT y prepara el documento maestro de LibreOffice."""
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from odt_reports import _tag
from document_styles import style_odt_paragraphs, style_role

MASTER_NAME = "Documento maestro.odm"
_MASTER_MIME = "application/vnd.oasis.opendocument.text-master"


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
            style_odt_paragraphs(part, automatic, text_styles, _tag, lambda element, role=role: role)
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
            _tag("xlink:href"): "../" + quote(path.as_posix()), _tag("text:filter-name"): "writer8",
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
