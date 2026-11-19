"""Ventana de datos específicos y generación de justificación de precios."""

from datetime import date
from pathlib import Path
from .document import (
    ANNUAL_HOURS_REFERENCES, CALENDAR_REFERENCES, CONSTRUCTION_AGREEMENT_REFERENCES,
    CONTRIBUTION_BASE_REFERENCES, CONTRIBUTION_DETAIL_REFERENCES,
    CONTRIBUTION_RATE_REFERENCES, FIELDS, PENSION_CONTRIBUTION_REFERENCES,
    SALARY_REVISION_REFERENCES, TITLE, WAGE_REFERENCES, WORK_CONDITION_REFERENCES,
    restore_state, validate_state, write_document,
)
from .templates import DOCUMENTS, LIBRARY_PATH, load_documents


TOOL_LABEL = TITLE
TOOL_ORDER = 0


def restore_project_state(data) -> dict:
    """Restaura los datos específicos del anejo sin duplicar la configuración general."""
    return restore_state(data)


def open_tool_qt(app) -> None:
    """Abre o enfoca la ventana Qt de justificación de precios."""
    from .qt_window import PriceJustificationWindowQt

    if not app.budget_items:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            app,
            TITLE,
            "Añade partidas al proyecto antes de justificar sus precios.",
        )
        return
    for window in getattr(app, "_project_windows", []):
        if isinstance(window, PriceJustificationWindowQt) and window.isVisible():
            window.refresh_general_info()
            window.raise_()
            window.activateWindow()
            return
    window = PriceJustificationWindowQt(app)
    app._project_windows.append(window)
    window.show()


def before_project_save_qt(app) -> None:
    """Recoge el formulario Qt antes de serializar el proyecto."""
    from .qt_window import PriceJustificationWindowQt

    state = app.price_justification_state
    for window in getattr(app, "_project_windows", []):
        if isinstance(window, PriceJustificationWindowQt) and window.isVisible():
            state = window.collect()
    app.price_justification_state = validate_state(state)


# GUI vigente: tools.price_justification.qt_window.
