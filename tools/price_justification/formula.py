"""Composición de fórmulas apiladas en PDF y como objetos MathML editables en ODT."""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree as ET
import re

from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Flowable

from .templates import formula_runs


@dataclass(frozen=True)
class Fraction:
    """Numerador y denominador, que pueden contener a su vez fracciones."""

    top: tuple[str | Fraction, ...]
    bottom: tuple[str | Fraction, ...]


def parse_formula(source: str) -> tuple[str | Fraction, ...]:
    r"""Reconoce \frac{numerador}{denominador} sin interpretar el texto como TeX."""
    parts = []
    while (start := source.find(r"\frac{")) >= 0:
        parts.append(source[:start])
        arguments = []
        cursor = start + len(r"\frac")
        for _ in range(2):
            if source[cursor:cursor + 1] != "{":
                raise ValueError(f"Fracción incompleta en la biblioteca: {source}")
            depth = 1
            for end in range(cursor + 1, len(source)):
                depth += (source[end] == "{") - (source[end] == "}")
                if not depth:
                    break
            else:
                raise ValueError(f"Fracción sin cerrar en la biblioteca: {source}")
            arguments.append(parse_formula(source[cursor + 1:end]))
            cursor = end + 1
        parts.append(Fraction(*arguments))
        source = source[cursor:]
    return tuple(part for part in (*parts, source) if part)


@dataclass
class _Box:
    """Medidas alrededor de la línea base y trazos relativos a ella."""

    width: float
    up: float
    down: float
    text: list
    rules: list

    def place(self, child: _Box, x: float, y: float) -> None:
        """Incorpora texto y reglas de otro bloque en la posición indicada."""
        self.text.extend((x + a, y + b, size, value) for a, b, size, value in child.text)
        self.rules.extend((x + a, y + b, width) for a, b, width in child.rules)


def _measure(parts: tuple[str | Fraction, ...], size: float) -> _Box:
    """Calcula dimensiones para centrar fracciones y alinear variables por su base."""
    box = _Box(0, size * 0.8, size * 0.25, [], [])
    for part in parts:
        if isinstance(part, Fraction):
            top = _measure(part.top, size * 0.88)
            bottom = _measure(part.bottom, size * 0.88)
            width = max(top.width, bottom.width) + 4
            axis = size * 0.25
            top_y = axis + 1 + top.down
            bottom_y = axis - 1 - bottom.up
            box.place(top, box.width + (width - top.width) / 2, top_y)
            box.place(bottom, box.width + (width - bottom.width) / 2, bottom_y)
            box.rules.append((box.width, axis, width))
            box.up = max(box.up, top_y + top.up)
            box.down = max(box.down, bottom.down - bottom_y)
            box.width += width
        else:
            for value, position in formula_runs(part):
                font_size = size * 0.72 if position else size
                y = size * 0.44 if position == "super" else -size * 0.22 if position else 0
                box.text.append((box.width, y, font_size, value))
                box.width += stringWidth(value, "Times-Roman", font_size)
                box.up = max(box.up, y + font_size * 0.8)
                box.down = max(box.down, font_size * 0.25 - y)
    return box


class PdfFormula(Flowable):
    """Dibuja texto PDF seleccionable y barras vectoriales de fracción."""

    def __init__(self, source: str):
        """Prepara el árbol de la expresión una sola vez."""
        super().__init__()
        self.parts = parse_formula(source)
        self.box = _measure(self.parts, 11)
        self.hAlign = "CENTER"
        self.spaceBefore = 6
        self.spaceAfter = 9

    def wrap(self, availWidth, availHeight):
        """Ajusta fórmulas anchas al ancho real del marco."""
        self.scale = min(1.0, availWidth / max(self.box.width, 1))
        self.width = self.box.width * self.scale
        self.height = (self.box.up + self.box.down) * self.scale
        return self.width, self.height

    def draw(self) -> None:
        """Dibuja la expresión alrededor de la línea base común."""
        self.canv.scale(self.scale, self.scale)
        self.canv.setLineWidth(0.55)
        for x, y, size, text in self.box.text:
            self.canv.setFont("Times-Roman", size)
            self.canv.drawString(x, y + self.box.down, text)
        for x, y, width in self.box.rules:
            self.canv.line(x, y + self.box.down, x + width, y + self.box.down)


_MATH_NS = "http://www.w3.org/1998/Math/MathML"
ET.register_namespace("math", _MATH_NS)
_TOKEN = re.compile(r"([A-Za-z]+|\d+(?:[.,]\d+)?|[^\s])(?:_([A-Za-z0-9]+))?(?:\^(?:\(([^()]*)\)|([A-Za-z0-9]+)))?")


def odt_formula(paragraph: ET.Element, source: str) -> None:
    """Inserta una fórmula nativa compatible con el editor de ecuaciones de Writer."""
    from odt_reports import _tag

    def node(parent, name, text=None):
        """Añade un elemento MathML con texto opcional."""
        element = ET.SubElement(parent, f"{{{_MATH_NS}}}{name}")
        element.text = text
        return element

    def append(parent, parts):
        """Escribe la secuencia matemática conservando el árbol de fracciones."""
        for part in parts:
            if isinstance(part, Fraction):
                fraction = node(parent, "mfrac")
                append(node(fraction, "mrow"), part.top)
                append(node(fraction, "mrow"), part.bottom)
                continue
            for match in _TOKEN.finditer(part):
                value, subscript, exponent, simple_exponent = match.groups()
                rendered = parent
                if exponent or simple_exponent:
                    rendered = node(rendered, "msup")
                base = node(rendered, "msub") if subscript else rendered
                node(base, "mi" if value.isalpha() else "mn" if value[0].isdigit() else "mo", value)
                if subscript:
                    node(base, "mi" if subscript.isalpha() else "mn", subscript)
                if exponent or simple_exponent:
                    append(node(rendered, "mrow"), (exponent or simple_exponent,))

    parts = parse_formula(source)
    box = _measure(parts, 12)
    frame = ET.SubElement(paragraph, _tag("draw:frame"), {
        _tag("text:anchor-type"): "as-char",
        _tag("svg:width"): f"{min(450, box.width + 12):.2f}pt",
        _tag("svg:height"): f"{box.up + box.down + 10:.2f}pt",
    })
    obj = ET.SubElement(frame, _tag("draw:object"))
    append(node(node(obj, "math"), "mrow"), parts)
