"""Temas globales de la interfaz PySide6."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


THEMES = {
    "system": "Sistema",
    "light": "Claro",
    "dark": "Oscuro",
}


def apply_theme(app: QApplication, theme: str) -> None:
    """Aplica una paleta estable sin depender del tema de Windows."""
    if theme not in THEMES:
        raise ValueError(f"Tema no admitido: {theme}")
    original = app.property("budget_default_palette")
    if not isinstance(original, QPalette):
        original = QPalette(app.palette())
        app.setProperty("budget_default_palette", original)
    app.setStyle("Fusion")
    if theme == "system":
        app.setPalette(original)
        return
    palette = QPalette()
    if theme == "light":
        colors = {
            QPalette.ColorRole.Window: "#f4f5f7",
            QPalette.ColorRole.WindowText: "#202124",
            QPalette.ColorRole.Base: "#ffffff",
            QPalette.ColorRole.AlternateBase: "#f7f8fa",
            QPalette.ColorRole.ToolTipBase: "#ffffff",
            QPalette.ColorRole.ToolTipText: "#202124",
            QPalette.ColorRole.Text: "#202124",
            QPalette.ColorRole.Button: "#e8eaed",
            QPalette.ColorRole.ButtonText: "#202124",
            QPalette.ColorRole.BrightText: "#ffffff",
            QPalette.ColorRole.Highlight: "#2563eb",
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.Link: "#1d4ed8",
        }
    else:
        colors = {
            QPalette.ColorRole.Window: "#202124",
            QPalette.ColorRole.WindowText: "#e8eaed",
            QPalette.ColorRole.Base: "#292a2d",
            QPalette.ColorRole.AlternateBase: "#323336",
            QPalette.ColorRole.ToolTipBase: "#323336",
            QPalette.ColorRole.ToolTipText: "#f1f3f4",
            QPalette.ColorRole.Text: "#f1f3f4",
            QPalette.ColorRole.Button: "#3c4043",
            QPalette.ColorRole.ButtonText: "#f1f3f4",
            QPalette.ColorRole.BrightText: "#ffffff",
            QPalette.ColorRole.Highlight: "#4285f4",
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.Link: "#8ab4f8",
        }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    if theme == "dark":
        disabled = QColor("#9aa0a6")
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    app.setPalette(palette)
