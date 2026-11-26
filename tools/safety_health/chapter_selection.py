"""Ambito por capitulos compartido por todos los analisis de Seguridad y Salud."""
from __future__ import annotations

from dataclasses import dataclass
import re

from municipalities import municipality_search_key
from tools._shared.budget_sources import matches_tool_chapter


SAFETY_BUDGET_SOURCE = "SS:"
_DEFAULT_EXCLUDED = re.compile(
    r"\br\.?\s*c\.?\s*ds?\b|\bresiduos?\s+de\s+(?:la\s+)?construccion\b|"
    r"\b(?:gestion|tratamiento)\b.*\bresiduos?\b|\bcontrol\s+(?:de\s+)?calidad\b",
    re.DOTALL,
)


@dataclass(frozen=True)
class StudyChapter:
    """Conserva el orden presupuestario y la decision revisable de cada capitulo."""

    code: str
    title: str
    items: int
    selected: bool


def restore_chapter_selection(value: object) -> dict[str, bool]:
    """Valida las casillas guardadas sin interpretar cadenas como booleanos."""
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(code, str) or not code.strip() or not isinstance(selected, bool)
        for code, selected in value.items()
    ):
        raise ValueError("La selección de capítulos de Seguridad y Salud no es válida.")
    return dict(value)


def safety_budget_chapters(app) -> set[str]:
    """Identifica los capítulos preventivos generados o reconocidos por su título."""
    return {
        item.chapter for item in app.budget_items
        if item.source.startswith(SAFETY_BUDGET_SOURCE)
        or matches_tool_chapter("safety", item.chapter, app._chapter_title(item.chapter))
    }


def study_chapters(app) -> list[StudyChapter]:
    """Presenta capitulos con partidas de obra, excluyendo SS y respetando el presupuesto."""
    counts: dict[str, int] = {}
    safety_chapters = safety_budget_chapters(app)
    for item in app.budget_items:
        if item.chapter not in safety_chapters:
            counts[item.chapter] = counts.get(item.chapter, 0) + 1
    choices = restore_chapter_selection(
        getattr(app, "safety_health_state", {}).get("chapter_selection")
    )
    order = dict.fromkeys([*getattr(app, "chapter_order", []), *counts])
    return [
        StudyChapter(
            code, app._chapter_title(code), counts[code],
            choices.get(code, not bool(_DEFAULT_EXCLUDED.search(
                municipality_search_key(app._chapter_title(code)),
            ))),
        )
        for code in order if code in counts
    ]


def study_items(app):
    """Selecciona las partidas analizadas sin alterar el presupuesto del proyecto."""
    chapters = {chapter.code for chapter in study_chapters(app) if chapter.selected}
    items = [
        item for item in app.budget_items
        if item.chapter in chapters and not item.source.startswith(SAFETY_BUDGET_SOURCE)
    ]
    if not items:
        raise ValueError("Selecciona al menos un capítulo con unidades de obra para el estudio.")
    return items


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
