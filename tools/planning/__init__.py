"""Entrada opcional de planificacion; calculos compartidos con certificaciones."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bc3_gui import BudgetApp

TOOL_LABEL = "Planificación"
TOOL_ORDER = 30


def open_tool_qt(app) -> None:
    """Abre una única ventana Qt de planificación."""
    from PySide6.QtWidgets import QMessageBox
    from .qt_window import PlanningWindowQt

    if not app.budget_items:
        QMessageBox.information(
            app, "Planificación", "Carga o crea un presupuesto antes de planificar."
        )
        return
    for window in app._project_windows:
        if isinstance(window, PlanningWindowQt) and window.isVisible():
            window.raise_()
            window.activateWindow()
            return
    try:
        window = PlanningWindowQt(app)
    except ValueError as exc:
        QMessageBox.critical(app, "Planificación", str(exc))
        return
    app._project_windows.append(window)
    window.show()
