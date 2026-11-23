"""Vinculaciones orientativas entre unidades de obra BCCA2023 y códigos LER."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from pathlib import Path
import re
from typing import TYPE_CHECKING

from .._shared.budget_sources import RCD_BUDGET_SOURCE

if TYPE_CHECKING:
    from bc3_gui import ProjectItem


CATALOG_PATH = Path(__file__).with_name("Codigos_LER_BCCA2023.csv")
COLUMNS = ("codigo_ler", "nivel", "peligroso", "descripcion", "partidas_BCCA2023_demoliciones_orientativas")
MATCH_HEADERS = ["Partida (UO)", "LER", "Residuo según el catálogo", "Revisión"]
MATCH_NOTE = (
    "Vínculos orientativos por código exacto de unidad de obra (BCCA2023). Una misma partida "
    "puede apuntar a varios residuos. Los vínculos no confirman la clasificación, no constituyen "
    "un inventario de peligrosos y no asignan masas ni volúmenes."
)


@dataclass(frozen=True)
class LerEntry:
    """Entrada de residuo de nivel 3 con su código completo y peligrosidad."""

    code: str
    description: str
    hazardous: bool


@cache
def by_work_unit(path: Path) -> dict[str, tuple[LerEntry, ...]]:
    """Indexa el CSV local y valida los códigos y los indicadores de peligrosidad."""
    index: dict[str, list[LerEntry]] = {}
    seen: set[str] = set()
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            if tuple(reader.fieldnames or ()) != COLUMNS:
                raise ValueError("cabecera no reconocida")
            for row in reader:
                code, level, hazardous, description, links = (row[column] for column in COLUMNS)
                if None in row or not all(isinstance(value, str) for value in row.values()):
                    raise ValueError(f"columnas incompletas en la línea {reader.line_num}")
                if level in {"1", "2"} and not links:
                    continue
                if (level != "3" or not re.fullmatch(r"\d{2} \d{2} \d{2}\*?", code)
                        or code in seen or not description.strip()
                        or hazardous != ("si" if code.endswith("*") else "no")):
                    raise ValueError(f"residuo LER no válido en la línea {reader.line_num}: {code}")
                seen.add(code)
                entry = LerEntry(code, description, hazardous == "si")
                for work_code in dict.fromkeys(part.strip().upper() for part in links.split(";")):
                    if work_code:
                        index.setdefault(work_code, []).append(entry)
            if not index:
                raise ValueError("no contiene vinculaciones con unidades de obra")
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        raise ValueError(f"No se pudo leer el catálogo LER–BCCA2023 ({path.name}): {exc}") from exc
    return {code: tuple(entries) for code, entries in index.items()}


def match_rows(items: list[ProjectItem]) -> list[list[str]]:
    """Traza los vínculos del presupuesto actual sin cambiar selecciones ni partidas."""
    index = by_work_unit(CATALOG_PATH)
    rows = []
    for item in items:
        if item.source.startswith(RCD_BUDGET_SOURCE):
            continue
        entries = index.get(item.code.strip().upper(), ())
        for entry in entries:
            review = "Vínculo múltiple" if len(entries) > 1 else "Vínculo único"
            if entry.hazardous:
                review += "; peligroso, comprobar"
            rows.append([f"{item.code}: {item.summary}", entry.code, entry.description, review])
    return rows
