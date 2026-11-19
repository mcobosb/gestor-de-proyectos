"""Bloques informativos editables que no forman parte del documento generado."""
from __future__ import annotations

import re


def informational_ranges(source: str, *, strict: bool = True) -> list[tuple[int, int]]:
    """Localiza bloques delimitados por comillas triples en líneas independientes."""
    ranges = []
    start = None
    for match in re.finditer(r'^[ \t]*"""[ \t]*(?:\r?\n|\Z)', source, flags=re.MULTILINE):
        if start is None:
            start = match.start()
        else:
            ranges.append((start, match.end()))
            start = None
    if start is not None:
        if strict:
            raise ValueError('Hay un bloque informativo sin cerrar. Añade una línea con """ al final del bloque.')
        ranges.append((start, len(source)))
    return ranges


def document_markdown(source: str) -> str:
    """Excluye información interna sin unir párrafos separados por sus bloques."""
    parts = []
    previous = 0
    for start, end in informational_ranges(source):
        parts.extend((source[previous:start], "\n\n"))
        previous = end
    parts.append(source[previous:])
    result = "".join(parts).strip("\r\n")
    return result if result.strip() else ""


def text_blocks(
    source: str, *, preserve_bold: bool = False, preserve_links: bool = False,
) -> list[tuple[int, str]]:
    """Transforma Markdown y conserva niveles de lista junto a titulos y avisos."""
    source = document_markdown(source)
    source = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    if not preserve_links:
        source = re.sub(r"\[([^]]+)\]\(([^)]+)\)",
                        lambda match: (f"{match[1]} ({match[2]})" if match[2].startswith(("http:", "https:"))
                                       else match[1]), source)
    source = source.replace("`", "")
    blocks, paragraph = [], []
    for line in [*source.splitlines(), ""]:
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        bullet = re.match(r"^(?P<indent>[ \t]*)(?:[-*+]\s+|•\s*)(?P<text>.*)", line)
        ordered = re.match(
            r"^(?P<indent>[ \t]*)(?P<bold>\*\*)?(?P<number>\d+[.)])[ \t]+(?P<text>.*)", line,
        )
        separator = re.fullmatch(r"\s*\*\*[^*\n]+\*\*\s*", line)
        if not line.strip() or heading or bullet or ordered or separator or "\t" in line or line.startswith(">"):
            if paragraph:
                blocks.append((0, " ".join(paragraph)))
                paragraph = []
            if heading:
                blocks.append((len(heading[1]), heading[2]))
            elif bullet:
                spaces = len(bullet["indent"].expandtabs(4))
                depth = (spaces + 3) // 4
                blocks.append((-(depth + 1), "• " + bullet["text"].strip()))
            elif ordered:
                spaces = len(ordered["indent"].expandtabs(4))
                depth = (spaces + 3) // 4
                text = (ordered["bold"] or "") + ordered["number"] + " " + ordered["text"].strip()
                blocks.append((-(depth + 1), text))
            elif separator:
                blocks.append((0, line.rstrip()))
            elif line.startswith(">"):
                blocks.append((0, line.lstrip("> ")))
            elif line.strip():
                blocks.append((0, line.rstrip(" \r")))
        else:
            paragraph.append(line.strip())
    return blocks if preserve_bold else [(level, text.replace("**", "")) for level, text in blocks]
