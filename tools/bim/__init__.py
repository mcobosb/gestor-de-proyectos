"""Entrada de Motor BIM en la ventana principal."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

from .qt import BimController


TOOL_LABEL = "Motor BIM"


def _controller(app: QMainWindow) -> BimController:
    """Reutiliza el controlador de carga y el modelo activo de la ventana."""
    controller = app.findChild(BimController)
    if controller is None:
        controller = BimController(app)
    return controller


def open_tool_qt(app: QMainWindow) -> None:
    """Abre el selector IFC desde el menú Motor BIM."""
    _controller(app).choose_file()
