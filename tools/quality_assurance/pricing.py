"""Consulta de precios de ensayos de la base BCCA distribuida."""
from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path

from budget_core import Bc3Dataset, Concept
from .plan import TRIALS, normalized

BCCA_PATH = Path(__file__).parent.parent / "BCCA2023_V02.bc3"


@lru_cache(maxsize=1)
def price_catalog() -> tuple[Concept, ...]:
    """Carga partidas de control valoradas por unidad, sin sus auxiliares."""
    dataset = Bc3Dataset(BCCA_PATH)
    chapter_items = {
        child for code in dataset.concepts if code.startswith("18") and code.endswith("#")
        for child in dataset.get_children(code)
    }
    entries = tuple(sorted(
        (concept for concept in dataset.concepts.values()
         if concept.code in chapter_items and concept.code.startswith("18")
         and not concept.code.endswith("#") and concept.unit.casefold().strip() in {"u", "ud"}
         and math.isfinite(concept.price) and concept.price > 0),
        key=lambda concept: concept.code,
    ))
    if not entries:
        raise ValueError("La base BCCA no contiene partidas de calidad con precio por unidad.")
    return entries


def price_candidates(name: str, query: str = "") -> list[Concept]:
    """Ofrece correspondencias comprobables o resultados de una búsqueda explícita."""
    recommended = {
        TRIALS["concrete"].name: ("18HHL00010", "18HHL00020"),
    }
    if name == "ENS-01 — Asentamiento en Cono de Abrams":
        codes = ("18HHL00020",)
    elif name == "ENS-03 — Resistencia a Compresión de Probetas":
        codes = ("18HHL00010",)
    else:
        codes = recommended.get(name, ())
    search = normalized(query).strip()
    return [
        concept for concept in price_catalog()
        if (all(term in normalized(f"{concept.code} {concept.summary} {concept.text}")
                for term in search.split()) if search else concept.code in codes)
    ]
