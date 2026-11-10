"""Historial de revisiones interno de los documentos Markdown de biblioteca."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re

from ._library_markdown import informational_ranges


HISTORY_HEADER = '"""\n## Registro de cambios\n'
INITIAL_AUTHOR = "Biblioteca incluida en el software v1.6.0"
INITIAL_DATE = "10/11/2026"
INITIAL_DESCRIPTION = "Incorporación del registro; autoría y fechas anteriores no documentadas."
ENTRY_PATTERN = re.compile(
    r"- Versión: ([1-9]\d*) \| Autor: ([^\n|]+) \| Fecha: (\d{2}/\d{2}/\d{4})"
    r" \| Cambio: ([^\n|]*)"
)


@dataclass(frozen=True)
class Revision:
    """Identifica una edición persistida sin confundirla con la fecha del archivo ZIP."""

    version: int
    author: str
    date: str
    description: str


def revision_values(author: str, date: str, description: str) -> tuple[str, str, str]:
    """Exige autor y fecha válidos y mantiene cada entrada en una única línea."""
    values = tuple(value.strip() for value in (author, date, description))
    if not values[0] or not values[1]:
        raise ValueError("Indica el autor y la fecha del cambio antes de guardar.")
    if any(any(char in value for char in ("\n", "\r", "|")) or '"""' in value for value in values):
        raise ValueError("Los datos del cambio no admiten saltos de línea, barras verticales ni comillas triples.")
    try:
        parsed = datetime.strptime(values[1], "%d/%m/%Y")
    except ValueError as exc:
        raise ValueError("La fecha del cambio debe ser una fecha válida con formato DD/MM/AAAA.") from exc
    if parsed.strftime("%d/%m/%Y") != values[1]:
        raise ValueError("La fecha del cambio debe tener formato DD/MM/AAAA.")
    return values[0], values[1], values[2]


def read_revisions(source: str) -> tuple[list[Revision], str]:
    """Lee el bloque inicial de registro y devuelve intacto el resto del Markdown."""
    if not source.startswith(HISTORY_HEADER):
        if "## Registro de cambios" in source:
            raise ValueError("El registro de cambios debe conservarse al principio de la ficha.")
        return [], source
    ranges = informational_ranges(source)
    if not ranges or ranges[0][0] != 0:
        raise ValueError("El registro de cambios no tiene un bloque informativo válido.")
    end = ranges[0][1]
    block = source[:end]
    lines = block[len(HISTORY_HEADER):].splitlines()
    if not lines or lines[-1] != '"""':
        raise ValueError("El registro de cambios no tiene un cierre válido.")
    revisions = []
    for line in lines[:-1]:
        match = ENTRY_PATTERN.fullmatch(line)
        if match is None:
            raise ValueError("El registro de cambios contiene una entrada no válida.")
        author, date, description = revision_values(match[2], match[3], match[4])
        revisions.append(Revision(int(match[1]), author, date, description))
    if not revisions or [revision.version for revision in revisions] != list(range(len(revisions), 0, -1)):
        raise ValueError("Las versiones del registro de cambios deben ser consecutivas.")
    return revisions, source[end:]


def add_revision(source: str, author: str, date: str, description: str = "") -> str:
    """Añade una revisión al principio del historial sin cambiar el contenido existente."""
    author, date, description = revision_values(author, date, description)
    revisions, remainder = read_revisions(source)
    version = revisions[0].version + 1 if revisions else 1
    revisions.insert(0, Revision(version, author, date, description))
    lines = [
        f"- Versión: {row.version} | Autor: {row.author} | Fecha: {row.date} | Cambio: {row.description}"
        for row in revisions
    ]
    return HISTORY_HEADER + "\n".join(lines) + '\n"""\n' + remainder


def initialize_history(source: str) -> str:
    """Marca la inclusión en v1.6.0 sin atribuir autoría histórica ni sobrescribir registros."""
    revisions, _ = read_revisions(source)
    if revisions:
        return source
    return add_revision(source, INITIAL_AUTHOR, INITIAL_DATE, INITIAL_DESCRIPTION)
