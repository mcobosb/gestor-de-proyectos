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
