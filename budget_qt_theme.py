"""Tema, iconos y acabado visual compartido por toda la interfaz PySide6."""
from __future__ import annotations

import unicodedata

from PySide6.QtCore import QEvent, QLineF, QObject, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QWidget
from localization import translate


THEMES = {
    "system": "Sistema",
    "light": "Claro",
    "dark": "Oscuro",
}

_BUDGET_INSERT_LABELS = {
    "guardar rcds en el proyecto",
    "aceptar y crear capitulo",
    "incorporar mediciones al presupuesto",
    "llevar mediciones de demolicion a transporte a vertedero",
    "copiar mediciones y usar volumenes en rcds",
}

_ICON_RULES = {
    "aplicar k al presupuesto": ("dialog-ok-apply", QStyle.StandardPixmap.SP_DialogApplyButton),
    "guardar": ("document-save", QStyle.StandardPixmap.SP_DialogSaveButton),
    "abrir": ("document-open", QStyle.StandardPixmap.SP_DialogOpenButton),
    "nuevo": ("document-new", QStyle.StandardPixmap.SP_FileIcon),
    "anadir": ("list-add", QStyle.StandardPixmap.SP_FileDialogNewFolder),
    "editar": ("edit", QStyle.StandardPixmap.SP_FileDialogDetailedView),
    "duplicar": ("edit-copy", QStyle.StandardPixmap.SP_FileDialogListView),
    "subir": ("go-up", QStyle.StandardPixmap.SP_ArrowUp),
    "bajar": ("go-down", QStyle.StandardPixmap.SP_ArrowDown),
    "anterior": ("go-previous", QStyle.StandardPixmap.SP_ArrowLeft),
    "atras": ("go-previous", QStyle.StandardPixmap.SP_ArrowBack),
    "siguiente": ("go-next", QStyle.StandardPixmap.SP_ArrowRight),
    "continuar": ("go-next", QStyle.StandardPixmap.SP_ArrowRight),
    "confirmar": ("dialog-ok", QStyle.StandardPixmap.SP_DialogApplyButton),
    "cancelar": ("dialog-cancel", QStyle.StandardPixmap.SP_DialogCancelButton),
    "cerrar": ("window-close", QStyle.StandardPixmap.SP_DialogCloseButton),
    "carpeta": ("folder-open", QStyle.StandardPixmap.SP_DirOpenIcon),
    "examinar": ("folder-open", QStyle.StandardPixmap.SP_DirOpenIcon),
    "actualizar": ("view-refresh", QStyle.StandardPixmap.SP_BrowserReload),
    "recalcular": ("view-refresh", QStyle.StandardPixmap.SP_BrowserReload),
    "secuenciar": ("media-seek-forward", QStyle.StandardPixmap.SP_MediaSeekForward),
    "generar": ("document-save-as", QStyle.StandardPixmap.SP_FileDialogStart),
    "informe": ("document-print-preview", QStyle.StandardPixmap.SP_FileIcon),
    "exportar": ("document-save-as", QStyle.StandardPixmap.SP_DialogSaveButton),
    "imprimir": ("document-print", QStyle.StandardPixmap.SP_FileDialogDetailedView),
    "configuracion": ("preferences-system", QStyle.StandardPixmap.SP_FileDialogDetailedView),
    "plantilla": ("document-open", QStyle.StandardPixmap.SP_DirOpenIcon),
    "biblioteca": ("folder", QStyle.StandardPixmap.SP_DirIcon),
    "ver ": ("document-print-preview", QStyle.StandardPixmap.SP_FileDialogContentsView),
    "pdf": ("application-pdf", QStyle.StandardPixmap.SP_FileDialogContentsView),
    "consulta": ("help-browser", QStyle.StandardPixmap.SP_MessageBoxQuestion),
    "referencia": ("internet-web-browser", QStyle.StandardPixmap.SP_DirLinkIcon),
    "copiar": ("edit-copy", QStyle.StandardPixmap.SP_FileDialogDetailedView),
    "revisar": ("document-properties", QStyle.StandardPixmap.SP_MessageBoxInformation),
    "asignar": ("document-properties", QStyle.StandardPixmap.SP_DialogApplyButton),
    "desasignar": ("edit-clear", QStyle.StandardPixmap.SP_DialogDiscardButton),
    "expandir": ("view-fullscreen", QStyle.StandardPixmap.SP_TitleBarMaxButton),
}


def _key(value: str) -> str:
    """Normaliza etiquetas para localizar su icono visual."""
    text = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def icon_for_text(app: QApplication, text: str):
    """Obtiene un icono estándar según la intención principal del texto."""
    normalized = _key(text)
    if normalized in _BUDGET_INSERT_LABELS:
        return budget_insert_icon(app)
    if "deshacer" in normalized:
        return QIcon.fromTheme(
            "edit-undo",
            app.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
        )
    if "eliminar" in normalized or "quitar" in normalized:
        return QIcon.fromTheme(
            "edit-delete",
            app.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon),
        )
    for keyword, (theme_name, fallback) in _ICON_RULES.items():
        if keyword in normalized:
            return QIcon.fromTheme(
                theme_name,
                app.style().standardIcon(fallback),
            )
    return QIcon()


def budget_insert_icon(app: QApplication) -> QIcon:
    """Dibuja un signo más para incorporar datos sin sugerir un guardado."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(app.palette().color(QPalette.ColorRole.ButtonText), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QLineF(7, 16, 25, 16))
        painter.drawLine(QLineF(16, 7, 16, 25))
    return QIcon(pixmap)


def search_icon(widget: QWidget) -> QIcon:
    """Dibuja una lupa con el color de texto del menú."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(widget.palette().color(QPalette.ColorRole.WindowText), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(QRectF(5, 5, 15, 15))
        painter.drawLine(QLineF(18, 18, 27, 27))
    return QIcon(pixmap)


def decorate_widget(widget: QWidget) -> None:
    """Asigna iconos a botones y barras operativas y respeta excepciones de menú."""
    app = QApplication.instance()
    if app is None:
        return
    for button in widget.findChildren(QPushButton):
        source = button.property("sourceButtonText") or translate(button.text(), "es")
        normalized = _key(source)
        if button.property("textOnlyIcon"):
            button.setIcon(QIcon())
            continue
        if normalized in {"ok", "aceptar"}:
            button.setIcon(QIcon())
            continue
        if button.icon().isNull() and button.text().strip():
            button.setIcon(icon_for_text(app, source))
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
                    action.setIcon(search_icon(menu) if action.property("searchIcon") else QIcon())
                    submenu = action.menu()
                    if submenu is not None:
                        for child in submenu.actions():
                            child.setIcon(QIcon())


class _QtDecorationFilter(QObject):
    """Decora automáticamente cualquier diálogo cuando se muestra."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Delega la decoración tras crear la jerarquía visual."""
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            QTimer.singleShot(0, watched, lambda widget=watched: decorate_widget(widget))
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
    blue_text = "#a7c7ff" if dark else "#1e4f9a"
    blue_tint = "#29374c" if dark else "#e8f0fc"
    blue_hover = "#354a68" if dark else "#dce9fc"
    teal_text = "#8cd7ca" if dark else "#16675d"
    teal_tint = "#233b3b" if dark else "#e7f3ef"
    teal_border = "#3e5a59" if dark else "#b8d7ce"
    plum_text = "#e0c5eb" if dark else "#654575"
    plum_tint = "#3a3042" if dark else "#f0e9f5"
    plum_border = "#63516f" if dark else "#d4c2e0"
    selection = "#345785" if dark else "#2455a3"
    chrome = "#262d3a" if dark else "#f2f6fd"
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
        background: {chrome};
        padding: 6px;
        spacing: 6px;
        border-bottom: 1px solid {border};
    }}
    QToolButton {{
        padding: 7px 10px;
        border-radius: 7px;
    }}
    QToolButton:hover {{
        background: {blue_tint};
    }}
    QToolButton#saveBudgetButton {{
        background: {blue_tint};
        color: {blue_text};
    }}
    QToolButton#saveBudgetButton:hover {{
        background: {blue_hover};
    }}
    QLabel#projectCaption, QLabel#budgetTotal {{
        color: {blue_text};
        font-weight: 600;
    }}
    QLabel#referenceCaption {{
        color: {teal_text};
        font-weight: 600;
    }}
    QWidget#collaborationPresence {{
        background: {teal_tint};
        border: 1px solid {teal_border};
        border-radius: 6px;
    }}
    QWidget#collaborationPresence QLabel {{
        color: {teal_text};
    }}
    QLabel#priceJustificationTitle {{
        color: {blue_text};
    }}
    QLabel#priceJustificationProject, QLabel#priceIndirectSummary {{
        background: {blue_tint};
        color: {blue_text};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 6px 10px;
    }}
    QLabel#priceIndirectGroup {{
        background: {teal_tint};
        color: {teal_text};
        padding: 4px 10px;
        font-weight: 600;
    }}
    QMenuBar::item, QMenu::item {{
        padding: 7px 11px;
        border-radius: 5px;
    }}
    QMenuBar::item:selected, QMenu::item:selected {{
        background: {selection};
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
        background: {blue_tint};
    }}
    QPushButton:default:enabled:!checked {{
        border-color: {accent};
        background: {blue_tint};
        color: {blue_text};
        font-weight: 600;
    }}
    QPushButton:default:enabled:!checked:hover {{
        background: {blue_hover};
    }}
    QPushButton:pressed {{
        padding-top: 7px;
        padding-bottom: 3px;
    }}
    QPushButton:checked {{
        background: {selection};
        border-color: {accent};
        color: white;
        font-weight: 600;
    }}
    QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled {{
        color: {muted};
    }}
    QPushButton#referencePill {{
        min-height: 18px;
        padding: 3px 10px;
        border: 1px solid {teal_border};
        border-radius: 12px;
        background: {teal_tint};
        color: {teal_text};
    }}
    QPushButton#referencePill:hover, QPushButton#referencePill:focus {{
        border-color: {teal_text};
    }}
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {surface};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: {selection};
        selection-color: white;
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
    QTreeWidget#budgetTree QHeaderView::section,
    QTableWidget#rcdPreviewTable QHeaderView::section,
    QTableWidget#rcdDemolitionTable QHeaderView::section,
    QTableWidget#priceIndirectTable QHeaderView::section {{
        background: {blue_tint};
        color: {blue_text};
    }}
    QTreeWidget#referenceTree QHeaderView::section,
    QTableWidget#rcdLinksTable QHeaderView::section,
    QTableWidget#rcdWasteTable QHeaderView::section {{
        background: {teal_tint};
        color: {teal_text};
        border-bottom-color: {teal_border};
    }}
    QTableWidget#rcdPreviewTable, QTableWidget#rcdDemolitionTable {{
        alternate-background-color: {blue_tint};
    }}
    QTableWidget#rcdWasteTable, QTableWidget#rcdLinksTable {{
        alternate-background-color: {teal_tint};
    }}
    QGroupBox[rcdInputs="true"] {{
        border: 1px solid {teal_border};
        border-radius: 7px;
        margin-top: 12px;
        padding: 8px;
    }}
    QGroupBox[rcdInputs="true"]::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 5px;
        color: {teal_text};
        font-weight: 600;
    }}
    QTreeWidget::item, QTableWidget::item, QListWidget::item {{
        padding: 4px;
    }}
    QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected {{
        background: {selection};
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
        background: {plum_tint};
        border-color: {plum_border};
        color: {plum_text};
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
        color: {blue_text};
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
                QPalette.ColorRole.Highlight: "#2455a3",
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
                QPalette.ColorRole.Highlight: "#345785",
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
