"""Ambito por capitulos compartido por todos los analisis de Seguridad y Salud."""
from __future__ import annotations

from dataclasses import dataclass
import re
import tkinter as tk
from tkinter import ttk

from localization import localize_widget_tree, messagebox
from municipalities import municipality_search_key


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


def study_chapters(app) -> list[StudyChapter]:
    """Presenta capitulos con partidas de obra, excluyendo SS y respetando el presupuesto."""
    counts: dict[str, int] = {}
    for item in app.budget_items:
        if not item.source.startswith(SAFETY_BUDGET_SOURCE):
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


class ChapterSelectionWindow(tk.Toplevel):
    """Permite confirmar el ambito antes de consultar matrices, fichas o planificacion."""

    def __init__(self, app, on_continue):
        """Muestra casillas por capitulo sin iniciar todavia los calculos preventivos."""
        chapters = study_chapters(app)
        super().__init__(app.root)
        self.on_continue = on_continue
        self.title("Seguridad y salud — capítulos a analizar")
        self.geometry("850x560")
        self.minsize(650, 400)
        self.transient(app.root)
        ttk.Label(
            self, padding=12, wraplength=800,
            text="Marca los capítulos sobre los que se realizará el estudio. Gestión o tratamiento "
                 "de RCD y Control de Calidad se desmarcan inicialmente; puedes incluirlos expresamente. "
                 "El capítulo de Seguridad y Salud no se analiza como unidad de obra.",
        ).pack(fill="x")
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12)
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        scrollbar.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas, padding=8)
        canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        self.choices = {}
        for chapter in chapters:
            variable = tk.BooleanVar(self, value=chapter.selected)
            self.choices[chapter.code] = variable
            ttk.Checkbutton(
                content, variable=variable,
                text=f"{chapter.code} — {chapter.title} ({chapter.items} partidas)",
            ).pack(anchor="w", pady=6)
        actions = ttk.Frame(self, padding=12)
        actions.pack(fill="x")
        ttk.Button(actions, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(
            actions, text="Continuar con el análisis", command=self.accept, style="Accent.TButton",
        ).pack(side="right", padx=8)
        localize_widget_tree(self)

    def accept(self) -> None:
        """Exige un ambito no vacio antes de guardar e iniciar el analisis."""
        choices = {code: variable.get() for code, variable in self.choices.items()}
        if not any(choices.values()):
            messagebox.showerror("Seguridad y salud", "Marca al menos un capítulo para analizar.", parent=self)
            return
        self.on_continue(choices)
