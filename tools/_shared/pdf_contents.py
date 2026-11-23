"""Indices PDF comunes con paginas reales y destinos navegables."""
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, SimpleDocTemplate
from reportlab.platypus.tableofcontents import TableOfContents

from .document_layout import CONTENTS_LEVEL_INDENT


def contents_styles(layout, depth: int) -> list[ParagraphStyle]:
    """Comparte tipografia, sangrias y espaciado entre todos los indices."""
    return layout.index_styles([
        ParagraphStyle(
            f"ContentsLevel{level}", fontName="Helvetica-Bold" if level == 0 else "Helvetica",
            fontSize=10, leading=11.5, leftIndent=CONTENTS_LEVEL_INDENT * level, firstLineIndent=0,
            rightIndent=24, spaceBefore=0, spaceAfter=4, alignment=TA_LEFT,
            textColor=colors.black,
        )
        for level in range(depth)
    ])


def contents_title(layout):
    """Aplica al rotulo del indice el mismo titulo y la tipografia del proyecto."""
    style = ParagraphStyle(
        "ReportContentsTitle", fontName="Helvetica-Bold", fontSize=16, leading=18.4,
        alignment=TA_LEFT, spaceBefore=6, spaceAfter=8, keepWithNext=True,
        textColor=colors.black,
    )
    return layout.text("ÍNDICE", style, role="title")


def contents_table(layout, depth: int) -> TableOfContents:
    """Crea un indice con puntos guia en todos sus niveles."""
    contents = TableOfContents()
    contents.levelStyles = contents_styles(layout, depth)
    contents.dotsMinLevel = 0
    return contents


def add_pdf_contents(story: list, layout, *, insertion: int = 0) -> bool:
    """Inserta un indice si los bloques contienen secciones identificadas."""
    headings = [
        block for block in story if getattr(block, "_contents_heading", None) is not None
    ]
    if not headings:
        return False
    depth = 1
    for index, block in enumerate(headings):
        level, title = block._contents_heading
        depth = max(depth, level)
        block._contents_entry = (
            level - 1, layout.index_entry(escape(title)), f"ReportContentsHeading{index}",
        )
    story[insertion:insertion] = [
        contents_title(layout), contents_table(layout, depth), PageBreak(),
    ]
    return True


class ContentsDocTemplate(SimpleDocTemplate):
    """Resuelve los indices de informes que antes no tenian tabla de contenido."""

    def afterFlowable(self, flowable) -> None:
        """Registra paginas y enlaces solo para los bloques de seccion marcados."""
        entry = getattr(flowable, "_contents_entry", None)
        if entry is not None:
            level, text, key = entry
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (level, text, self.page, key))
