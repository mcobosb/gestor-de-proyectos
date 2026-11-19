"""Herramienta Qt para preparar la estructura documental de una obra."""

TOOL_LABEL = "Estructura documental"
TOOL_ORDER = -10


def open_tool_qt(app) -> None:
    """Abre el índice incluso si todavía no hay presupuesto."""
    from .qt_window import DocumentStructureWindow

    for window in app._project_windows:
        if isinstance(window, DocumentStructureWindow) and window.isVisible():
            window.refresh_generation()
            window.raise_()
            window.activateWindow()
            return
    window = DocumentStructureWindow(app)
    app._project_windows.append(window)
    window.show()
