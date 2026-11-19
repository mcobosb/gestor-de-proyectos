"""Tipografía de proyecto compartida por los exportadores PDF y ODF."""
from __future__ import annotations

from math import isfinite
import re
from xml.etree import ElementTree as ET

FONTS = {
    "Arial": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
    "Times New Roman": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "Courier New": ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"),
}
ROLES = {
    "title": ("Título del documento", 16, True),
    **{f"heading{n}": (f"Título {n}", size, True)
       for n, size in enumerate((14, 12, 11, 10.5, 10, 10), 1)},
    "body": ("Texto normal", 10, False),
    "list": ("Listas y viñetas", 10, False),
    "index": ("Índice", 10, False),
    "caption": ("Títulos de tabla", 9, True),
    "table_header": ("Cabeceras de tabla", 8, True),
    "table": ("Texto de tabla", 8, False),
    "emphasis": ("Totales y énfasis en tablas", 8, True),
    "header": ("Cabecera de página", 8, False),
    "footer": ("Pie de página", 6, False),
    "signature": ("Firma", 8, False),
}
FLAGS = ("bold", "italic", "underline")
StyleTable = dict[str, dict[str, str | float | bool]]


def validate_styles(value: object) -> StyleTable:
    """Valida ajustes parciales; un campo ausente conserva el estándar."""
    if not isinstance(value, dict):
        raise ValueError("La tabla de estilos debe ser un objeto JSON.")
    result: StyleTable = {}
    for role, fields in value.items():
        if role not in ROLES or not isinstance(fields, dict):
            raise ValueError(f"Estilo de documento no válido: {role}.")
        style = {}
        for key, setting in fields.items():
            if key == "font" and isinstance(setting, str) and setting in FONTS:
                style[key] = setting
            elif key == "size" and type(setting) in (float, int) and isfinite(setting) and 6 <= setting <= 36:
                style[key] = float(setting)
            elif key in FLAGS and isinstance(setting, bool):
                style[key] = setting
            else:
                raise ValueError(f"Propiedad tipográfica no válida: {role}.{key}.")
        if style:
            result[role] = style
    return result


def style_role(name: str, *, odt: bool = False) -> str:
    """Asocia los estilos existentes a la misma función en los dos formatos."""
    name = name.lower().replace("-", "")
    heading = re.fullmatch(r"(?:(?:pptp|rcd|prices|ess)(?:document\d+)?)?heading([1-6])", name)
    if heading:
        return "heading" + heading[1]
    if name in {"chaptertitle", "pricesectiontitle", "reportchapter", "priceslistingheading"}:
        return "heading1"
    if name == "rcddocumentstart":
        return "heading1"
    if "signature" in name:
        return "signature"
    if "footer" in name:
        return "footer"
    if name in {"reportheader", "reportproject", "projecttitle"}:
        return "header"
    if name in {"title", "reporttitle", "pptptitle", "essdocumenttitle", "essdocumentnexttitle",
                "tituloplantilla", "pricesdocumenttitle", "rcddocumenttitle"}:
        return "title"
    if "caption" in name:
        return "caption"
    if "contents" in name or name in {"reportindex", "tocheading"}:
        return "index"
    if any(part in name for part in ("bullet", "list")):
        return "list"
    if "header" in name:
        return "table_header"
    if any(part in name for part in ("tabletotal", "itemtitle", "itemsummary", "auxiliarytitle", "cataloguetype",
                                     "reportbold")):
        return "emphasis"
    if any(part in name for part in ("tabletext", "summarytext", "indextext", "reporttable", "planningaxis")):
        return "table"
    if name in {"normal", "reportbody", "pptpbody", "pricesbody", "essbody", "rcdbody", "reportdescription",
                "reportwords", "itemdescription", "amountinwords", "reportnote", "justification",
                "reportright", "pptpseparator"} or "tablereference" in name:
        return "body"
    return "" if odt else "body"


def pdf_typography(style, styles: StyleTable, role: str):
    """Retoca una copia del estilo sin alterar sangrías, colores ni paginación."""
    from copy import copy
    from reportlab.lib.fonts import tt2ps, ps2tt

    fields = styles.get(role)
    if not fields:
        return style, False
    copy_style = copy(style)
    if "size" in fields:
        copy_style.fontSize = float(fields["size"])
        copy_style.leading = style.leading * copy_style.fontSize / style.fontSize
        if hasattr(copy_style, "bulletFontSize"):
            copy_style.bulletFontSize = copy_style.fontSize
    if any(key in fields for key in ("font", "bold", "italic")):
        try:
            family, bold, italic = ps2tt(style.fontName)
        except ValueError:
            family = "helvetica"
            bold, italic = "bold" in style.fontName.lower(), "italic" in style.fontName.lower()
        bold = bool(fields.get("bold", bold))
        italic = bool(fields.get("italic", italic))
        if "font" in fields:
            copy_style.fontName = FONTS[str(fields["font"])][int(bold) + 2 * int(italic)]
        else:
            copy_style.fontName = tt2ps(family, bold, italic)
        copy_style.bulletFontName = copy_style.fontName
    return copy_style, bool(fields.get("underline", False))


def odt_typography(style, fields: dict[str, str | float | bool], tag) -> None:
    """Escribe propiedades ODF de texto sin reemplazar el estilo de párrafo."""
    if not fields:
        return
    props = style.find(tag("style:text-properties"))
    if props is None:
        props = ET.SubElement(style, tag("style:text-properties"))
    for key, attribute, on, off in (
        ("bold", "fo:font-weight", "bold", "normal"),
        ("italic", "fo:font-style", "italic", "normal"),
        ("underline", "style:text-underline-style", "solid", "none"),
    ):
        if key in fields:
            props.set(tag(attribute), on if fields[key] else off)
    if fields.get("underline"):
        props.set(tag("style:text-underline-type"), "single")
    if "size" in fields:
        for name in ("fo:font-size", "style:font-size-asian", "style:font-size-complex"):
            props.set(tag(name), f"{float(fields['size']):g}pt")
    if "font" in fields:
        for key in ("style:font-name", "style:font-name-asian", "style:font-name-complex"):
            props.attrib.pop(tag(key), None)
        props.set(tag("fo:font-family"), str(fields["font"]))


def style_odt_tree(root, styles: StyleTable, tag) -> None:
    """Personaliza estilos de párrafo nombrados y automáticos ya existentes."""
    if not styles:
        return
    for style in root.iter(tag("style:style")):
        if style.get(tag("style:family")) != "paragraph":
            continue
        role = style_role(style.get(tag("style:name"), ""), odt=True)
        if role:
            odt_typography(style, styles.get(role, {}), tag)


def style_odt_paragraphs(section, container, styles: StyleTable, tag, role_for) -> None:
    """Aplica roles explícitos a párrafos preexistentes sin perder su estilo padre."""
    if not styles:
        return
    names = {style.get(tag("style:name")) for style in container}
    for element in section.iter():
        if element.tag not in (tag("text:p"), tag("text:h")):
            continue
        role = role_for(element)
        if not (fields := styles.get(role)):
            continue
        parent = element.get(tag("text:style-name"), "")
        name = f"ProjectTypography_{role}_{parent}"
        if name not in names:
            attributes = {tag("style:name"): name, tag("style:family"): "paragraph"}
            if parent:
                attributes[tag("style:parent-style-name")] = parent
            style = ET.SubElement(container, tag("style:style"), attributes)
            odt_typography(style, fields, tag)
            names.add(name)
        element.set(tag("text:style-name"), name)
