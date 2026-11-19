"""Tema, iconos y acabado visual compartido por toda la interfaz PySide6."""
from __future__ import annotations

import unicodedata

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QWidget


THEMES = {
    "system": "Sistema",
    "light": "Claro",
    "dark": "Oscuro",
}

_ICON_RULES = {
    "guardar": QStyle.StandardPixmap.SP_DialogSaveButton,
    "abrir": QStyle.StandardPixmap.SP_DialogOpenButton,
    "nuevo": QStyle.StandardPixmap.SP_FileIcon,
    "añadir": QStyle.StandardPixmap.SP_FileDialogNewFolder,
    "eliminar": QStyle.StandardPixmap.SP_TrashIcon,
    "quitar": QStyle.StandardPixmap.SP_TrashIcon,
    "deshacer": QStyle.StandardPixmap.SP_ArrowBack,
    "editar": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "duplicar": QStyle.StandardPixmap.SP_FileDialogListView,
    "subir": QStyle.StandardPixmap.SP_ArrowUp,
    "bajar": QStyle.StandardPixmap.SP_ArrowDown,
    "anterior": QStyle.StandardPixmap.SP_ArrowLeft,
    "atras": QStyle.StandardPixmap.SP_ArrowBack,
    "siguiente": QStyle.StandardPixmap.SP_ArrowRight,
    "continuar": QStyle.StandardPixmap.SP_ArrowRight,
    "aceptar": QStyle.StandardPixmap.SP_DialogApplyButton,
    "confirmar": QStyle.StandardPixmap.SP_DialogApplyButton,
    "cancelar": QStyle.StandardPixmap.SP_DialogCancelButton,
    "cerrar": QStyle.StandardPixmap.SP_DialogCloseButton,
    "carpeta": QStyle.StandardPixmap.SP_DirOpenIcon,
    "examinar": QStyle.StandardPixmap.SP_DirOpenIcon,
    "actualizar": QStyle.StandardPixmap.SP_BrowserReload,
    "recalcular": QStyle.StandardPixmap.SP_BrowserReload,
    "secuenciar": QStyle.StandardPixmap.SP_MediaSeekForward,
    "generar": QStyle.StandardPixmap.SP_FileDialogStart,
    "informe": QStyle.StandardPixmap.SP_FileIcon,
    "exportar": QStyle.StandardPixmap.SP_DialogSaveButton,
    "imprimir": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "configuracion": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "plantilla": QStyle.StandardPixmap.SP_DirOpenIcon,
    "biblioteca": QStyle.StandardPixmap.SP_DirIcon,
    "ver ": QStyle.StandardPixmap.SP_FileDialogContentsView,
    "pdf": QStyle.StandardPixmap.SP_FileDialogContentsView,
    "consulta": QStyle.StandardPixmap.SP_MessageBoxQuestion,
    "referencia": QStyle.StandardPixmap.SP_DirLinkIcon,
    "copiar": QStyle.StandardPixmap.SP_FileDialogDetailedView,
    "revisar": QStyle.StandardPixmap.SP_MessageBoxInformation,
    "asignar": QStyle.StandardPixmap.SP_DialogApplyButton,
    "desasignar": QStyle.StandardPixmap.SP_DialogDiscardButton,
    "expandir": QStyle.StandardPixmap.SP_TitleBarMaxButton,
}


def _key(value: str) -> str:
    """Normaliza etiquetas para localizar su icono visual."""
    text = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def icon_for_text(app: QApplication, text: str):
    """Obtiene un icono estándar según la intención principal del texto."""
    normalized = _key(text)
    for keyword, icon in _ICON_RULES.items():
        if keyword in normalized:
            return app.style().standardIcon(icon)
    return app.style().standardIcon(QStyle.StandardPixmap.SP_CommandLink)


def decorate_widget(widget: QWidget) -> None:
    """Asigna iconos a botones y barras operativas, pero conserva menús de texto."""
    app = QApplication.instance()
    if app is None:
        return
    for button in widget.findChildren(QPushButton):
        if button.icon().isNull() and button.text().strip():
            button.setIcon(icon_for_text(app, button.text()))
            button.setIconSize(button.iconSize())
    for action in widget.actions():
        if action.menu() is not None:
            continue
        text = action.text().strip()
        if text and action.icon().isNull():
            action.setIcon(icon_for_text(app, text))
    menu_bar = getattr(widget, "menuBar", None)
    if callable(menu_bar):
        for top_action in menu_bar().actions():
            top_action.setIcon(QIcon())
            menu = top_action.menu()
            if menu is not None:
                for action in menu.actions():
                    action.setIcon(QIcon())
                    submenu = action.menu()
                    if submenu is not None:
                        for child in submenu.actions():
                            child.setIcon(QIcon())


class _QtDecorationFilter(QObject):
    """Decora automáticamente cualquier diálogo cuando se muestra."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Delega la decoración tras crear la jerarquía visual."""
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            QTimer.singleShot(0, lambda widget=watched: decorate_widget(widget))
        return False


def _stylesheet(theme: str) -> str:
    """Crea la hoja de estilo coherente para un tema activo."""
    dark = theme == "dark"
    accent = "#4f8cff" if dark else "#2563eb"
    accent_hover = "#73a3ff" if dark else "#1d4ed8"
    surface = "#24262b" if dark else "#ffffff"
    surface_alt = "#2f3238" if dark else "#f5f7fb"
    window = "#1e2024" if dark else "#eef2f7"
    text = "#f1f3f5" if dark else "#18212f"
    muted = "#aeb5c0" if dark else "#64748b"
    border = "#41454d" if dark else "#cbd5e1"
    return f"""
    QWidget {{
        font-family: "Segoe UI", sans-serif;
        font-size: 10.5pt;
        color: {text};
    }}
    QMainWindow, QDialog {{
        background: {window};
    }}
    QMenuBar, QToolBar, QStatusBar {{
        background: {surface};
        border: 0;
    }}
    QToolBar {{
        padding: 6px;
        spacing: 6px;
        border-bottom: 1px solid {border};
    }}
    QToolButton {{
        padding: 7px 10px;
        border-radius: 7px;
    }}
    QToolButton:hover {{
        background: {surface_alt};
    }}
    QMenuBar::item, QMenu::item {{
        padding: 7px 11px;
        border-radius: 5px;
    }}
    QMenuBar::item:selected, QMenu::item:selected {{
        background: {accent};
        color: white;
    }}
    QPushButton {{
        min-height: 30px;
        padding: 5px 12px;
        border: 1px solid {border};
        border-radius: 7px;
        background: {surface};
    }}
    QPushButton:hover {{
        border-color: {accent};
        background: {surface_alt};
    }}
    QPushButton:pressed {{
        padding-top: 7px;
        padding-bottom: 3px;
    }}
    QPushButton:checked {{
        background: {accent};
        border-color: {accent};
        color: white;
        font-weight: 600;
    }}
    QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled {{
        color: {muted};
    }}
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {surface};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: {accent};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{
        border: 2px solid {accent};
    }}
    QTableWidget, QTreeWidget, QListWidget {{
        background: {surface};
        alternate-background-color: {surface_alt};
        border: 1px solid {border};
        border-radius: 7px;
        gridline-color: {border};
    }}
    QHeaderView::section {{
        background: {surface_alt};
        color: {text};
        border: 0;
        border-right: 1px solid {border};
        border-bottom: 1px solid {border};
        padding: 7px;
        font-weight: 600;
    }}
    QTreeWidget::item, QTableWidget::item, QListWidget::item {{
        padding: 4px;
    }}
    QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{
        background: {accent};
        color: white;
    }}
    QTabWidget::pane {{
        border: 1px solid {border};
        border-radius: 8px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: {surface_alt};
        padding: 8px 14px;
        border: 1px solid {border};
        border-bottom: 0;
        border-top-left-radius: 7px;
        border-top-right-radius: 7px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background: {surface};
        color: {accent};
        font-weight: 600;
    }}
    QGroupBox {{
        border: 1px solid {border};
        border-radius: 8px;
        margin-top: 12px;
        padding-top: 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 5px;
    }}
    QScrollBar:vertical, QScrollBar:horizontal {{
        background: transparent;
    }}
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
        background: {border};
        border-radius: 5px;
        min-height: 28px;
        min-width: 28px;
    }}
    QScrollBar::handle:hover:vertical, QScrollBar::handle:hover:horizontal {{
        background: {accent_hover};
    }}
    QStatusBar {{
        color: {muted};
        border-top: 1px solid {border};
    }}
    """


def apply_theme(app: QApplication, theme: str) -> None:
    """Aplica paleta, estilos y decorador automático a toda la aplicación."""
    if theme not in THEMES:
        raise ValueError(f"Tema no admitido: {theme}")
    original = app.property("budget_default_palette")
    if not isinstance(original, QPalette):
        original = QPalette(app.palette())
        app.setProperty("budget_default_palette", original)
    app.setStyle("Fusion")
    if theme == "system":
        app.setPalette(original)
        system_dark = app.palette().color(
            QPalette.ColorRole.Window
        ).lightness() < 128
        visual_theme = "dark" if system_dark else "light"
    else:
        visual_theme = theme
        palette = QPalette()
        if theme == "light":
            colors = {
                QPalette.ColorRole.Window: "#eef2f7",
                QPalette.ColorRole.WindowText: "#18212f",
                QPalette.ColorRole.Base: "#ffffff",
                QPalette.ColorRole.AlternateBase: "#f5f7fb",
                QPalette.ColorRole.ToolTipBase: "#ffffff",
                QPalette.ColorRole.ToolTipText: "#18212f",
                QPalette.ColorRole.Text: "#18212f",
                QPalette.ColorRole.Button: "#ffffff",
                QPalette.ColorRole.ButtonText: "#18212f",
                QPalette.ColorRole.Highlight: "#2563eb",
                QPalette.ColorRole.HighlightedText: "#ffffff",
                QPalette.ColorRole.Link: "#1d4ed8",
            }
        else:
            colors = {
                QPalette.ColorRole.Window: "#1e2024",
                QPalette.ColorRole.WindowText: "#f1f3f5",
                QPalette.ColorRole.Base: "#24262b",
                QPalette.ColorRole.AlternateBase: "#2f3238",
                QPalette.ColorRole.ToolTipBase: "#2f3238",
                QPalette.ColorRole.ToolTipText: "#f1f3f5",
                QPalette.ColorRole.Text: "#f1f3f5",
                QPalette.ColorRole.Button: "#24262b",
                QPalette.ColorRole.ButtonText: "#f1f3f5",
                QPalette.ColorRole.Highlight: "#4f8cff",
                QPalette.ColorRole.HighlightedText: "#ffffff",
                QPalette.ColorRole.Link: "#8ab4f8",
            }
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
    app.setStyleSheet(_stylesheet(visual_theme))
    event_filter = app.property("budget_decoration_filter")
    if not isinstance(event_filter, _QtDecorationFilter):
        event_filter = _QtDecorationFilter(app)
        app.setProperty("budget_decoration_filter", event_filter)
        app.installEventFilter(event_filter)
    for window in app.topLevelWidgets():
        decorate_widget(window)
        window.style().unpolish(window)
        window.style().polish(window)
