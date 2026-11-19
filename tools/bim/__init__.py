"""Entrada de Motor BIM en la ventana principal."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QToolBar

from .qt import BimController


TOOL_LABEL = "Motor BIM"


def _controller(app: QMainWindow) -> BimController:
    """Comparte la carga y el modelo activo entre el menú y la barra."""
    controller = app.findChild(BimController)
    if controller is None:
        controller = BimController(app)
    return controller


def open_tool_qt(app: QMainWindow) -> None:
    """Abre el selector IFC desde el menú Motor BIM."""
    _controller(app).choose_file()


def install_toolbar_qt(app: QMainWindow, toolbar: QToolBar) -> None:
    """Instala el desplegable Motor BIM en la barra principal."""
    _controller(app).install_button(toolbar, TOOL_LABEL)
