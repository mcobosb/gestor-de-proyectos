"""Punto de entrada PySide6 de Gestor de Proyectos."""
from __future__ import annotations

from budget_qt_window import BudgetMainWindow
from qt_app import main, show_startup_splash

__all__ = ["BudgetMainWindow", "main", "show_startup_splash"]


if __name__ == "__main__":
    raise SystemExit(main())
