"""Herramienta de aseguramiento y control de la calidad."""

TOOL_LABEL = "Aseguramiento de la calidad"
TOOL_ORDER = 1


def open_tool_qt(app) -> None:
    """Abre una única ventana de revisión por proyecto."""
    from .qt_window import QualityWindow

    for window in app._project_windows:
        if isinstance(window, QualityWindow):
            window.show()
            window.raise_()
            window.activateWindow()
            return
    window = QualityWindow(app)
    app._project_windows.append(window)
    window.show()
