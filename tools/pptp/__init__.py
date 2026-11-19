"""Asistente de revision y generacion del PPTP del proyecto."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

from .library import ZipLibrary
from .document import (
    DEFAULT_TOC_DEPTH, PENDING_DURATION, VARIABLES, Selection, compose, directory_codes, initial_variables,
    load_catalog, load_regulations, normalized_code, price_revision_fields, propose, restore_state, write_document,
    duration_price_revision, regulation_matches, une_designation,
)

if TYPE_CHECKING:
    from bc3_gui import BudgetApp
    from tools._library_editor import LibraryEditor

TOOL_LABEL = "PPTP (BCCA · Junta de Andalucía)"
TOOL_ORDER = 35
LIBRARY_PATH = Path(__file__).resolve().parent / "BIBLIOTECA.zip"
DOCS_ROOT = Path(__file__).resolve().parent / "docs"


def restore_project_state(data: dict) -> dict:
    """Recupera la revision aceptada sin consultar ni modificar el presupuesto."""
    return restore_state(data)


def open_tool_qt(app) -> None:
    """Abre el asistente PPTP de PySide6 o enfoca su única instancia."""
    from PySide6.QtWidgets import QMessageBox
    from .qt_window import PptpWindowQt

    if not app.budget_items:
        QMessageBox.information(
            app, TOOL_LABEL, "Carga o crea un presupuesto antes de preparar el PPTP."
        )
        return
    for window in app._project_windows:
        if isinstance(window, PptpWindowQt) and window.isVisible():
            window.raise_()
            window.activateWindow()
            return
    try:
        window = PptpWindowQt(app)
    except (ImportError, OSError, UnicodeError, ValueError) as exc:
        QMessageBox.critical(app, TOOL_LABEL, str(exc))
        return
    app._project_windows.append(window)
    window.show()


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
