"""Entrada opcional de planificacion; calculos compartidos con certificaciones."""
from __future__ import annotations

from typing import TYPE_CHECKING

from localization import messagebox
from ._planning import PlanningWindow

if TYPE_CHECKING:
    from bc3_gui import BudgetApp

TOOL_LABEL = "Planificación"
TOOL_ORDER = 30


def open_tool(app: BudgetApp) -> None:
    """Abre la planificacion con las partidas y los datos guardados del proyecto."""
    if not app.budget_items:
        messagebox.showinfo("Planificación", "Carga o crea un presupuesto antes de planificar.")
        return
    window = PlanningWindow(
        app.root,
        app.budget_items,
        app._chapter_title,
        app._project_title(),
        saved_state=app.planning_state,
        on_state_change=app._store_planning_state,
        on_save=app.save_project,
        on_dirty_change=lambda dirty: app._mark_dirty() if dirty else None,
        currency_formatter=app._format_currency,
    )
    app._project_windows.append(window)
