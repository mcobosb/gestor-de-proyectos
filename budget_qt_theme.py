"""Tema, iconos y acabado visual compartido por toda la interfaz PySide6."""
from __future__ import annotations

import unicodedata

from PySide6.QtCore import QEvent, QLineF, QObject, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QToolButton, QWidget
from shiboken6 import isValid
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
    "insertar ensayos en el presupuesto",
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

_TOOL_ICON_RULES = {
    "document_structure": (
        "x-office-document",
        QStyle.StandardPixmap.SP_FileDialogContentsView,
    ),
    "invoicing": (
        "x-office-spreadsheet",
        QStyle.StandardPixmap.SP_FileDialogDetailedView,
    ),
    "planning": (
        "x-office-calendar",
        QStyle.StandardPixmap.SP_FileDialogInfoView,
    ),
    "price_justification": (
        "accessories-calculator",
        QStyle.StandardPixmap.SP_FileDialogContentsView,
    ),
    "quality_assurance": (
        "task-due",
        QStyle.StandardPixmap.SP_DialogApplyButton,
    ),
    "rcd": (
        "user-trash",
        QStyle.StandardPixmap.SP_TrashIcon,
    ),
    "safety_health": (
        "security-high",
        QStyle.StandardPixmap.SP_MessageBoxWarning,
    ),
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


def budget_concept_icon(widget: QWidget, kind: str) -> QIcon:
    """Representa el símbolo que identifica el concepto en el presupuesto."""
    if kind not in {"chapter", "0"}:
        raise ValueError(f"Tipo de concepto no admitido: {kind}")
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    color = widget.palette().color(QPalette.ColorRole.WindowText)
    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if kind == "chapter":
            painter.setPen(QPen(color, 2))
            painter.drawRect(QRectF(6, 6, 20, 20))
            painter.fillRect(QRectF(10, 10, 12, 12), color)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([
                QPointF(16, 4), QPointF(28, 16),
                QPointF(16, 28), QPointF(4, 16),
            ]))
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


def budget_tree_icon(widget: QWidget) -> QIcon:
    """Dibuja capítulos, subcapítulos y partidas conectados en árbol."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(widget.palette().color(QPalette.ColorRole.WindowText), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawRoundedRect(QRectF(2, 2, 6, 6), 1, 1)
        painter.drawLine(QLineF(5, 8, 5, 23))
        painter.drawLine(QLineF(5, 13, 11, 13))
        painter.drawLine(QLineF(5, 23, 11, 23))
        painter.drawRoundedRect(QRectF(11, 10, 6, 6), 1, 1)
        painter.drawRoundedRect(QRectF(11, 20, 6, 6), 1, 1)
        painter.drawLine(QLineF(14, 16, 14, 18))
        painter.drawLine(QLineF(14, 18, 22, 18))
        painter.drawRoundedRect(QRectF(22, 15, 6, 6), 1, 1)
    return QIcon(pixmap)


def certification_icon(widget: QWidget) -> QIcon:
    """Dibuja un sello con una marca de validación para certificaciones."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    color = widget.palette().color(QPalette.ColorRole.WindowText)
    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawEllipse(7, 3, 18, 18)
        painter.drawEllipse(10, 6, 12, 12)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(12, 12, 15, 15)
        painter.drawLine(15, 15, 20, 9)
        painter.drawLine(10, 20, 12, 29)
        painter.drawLine(12, 20, 16, 25)
        painter.drawLine(16, 25, 20, 20)
        painter.drawLine(20, 20, 23, 29)
    return QIcon(pixmap)


def technical_document_icon(widget: QWidget) -> QIcon:
    """Dibuja una hoja técnica con cotas y una referencia de plano."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    color = widget.palette().color(QPalette.ColorRole.WindowText)
    with QPainter(pixmap) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color, 2)
        painter.setPen(pen)
        painter.drawRect(5, 3, 22, 26)
        painter.drawLine(9, 8, 23, 8)
        painter.drawLine(9, 24, 23, 24)
        painter.drawLine(9, 12, 9, 21)
        painter.drawLine(23, 12, 23, 21)
        painter.drawLine(7, 14, 25, 14)
        painter.drawLine(16, 10, 16, 18)
        painter.drawLine(12, 22, 20, 22)
        painter.drawLine(7, 12, 11, 12)
        painter.drawLine(7, 21, 11, 21)
        painter.drawLine(21, 12, 25, 12)
        painter.drawLine(21, 21, 25, 21)
    return QIcon(pixmap)


def information_icon(widget: QWidget, text: str) -> QIcon:
    """Obtiene un icono relacionado con una entrada del menú de información."""
    normalized = _key(text)
    if normalized == _key("Bases de precios de referencia"):
        theme_name, fallback = (
            "folder-documents",
            QStyle.StandardPixmap.SP_DirLinkIcon,
        )
    elif normalized.startswith("version "):
        theme_name, fallback = (
            "help-about",
            QStyle.StandardPixmap.SP_MessageBoxInformation,
        )
    elif "github" in normalized:
        theme_name, fallback = (
            "applications-internet",
            QStyle.StandardPixmap.SP_ComputerIcon,
        )
    elif "licencia" in normalized:
        theme_name, fallback = (
            "text-x-generic",
            QStyle.StandardPixmap.SP_FileIcon,
        )
    elif "buy me a coffee" in normalized or "apoyar" in normalized:
        theme_name, fallback = (
            "emblem-favorite",
            QStyle.StandardPixmap.SP_DialogApplyButton,
        )
    elif "pyside" in normalized:
        theme_name, fallback = (
            "help-about",
            QStyle.StandardPixmap.SP_MessageBoxInformation,
        )
    else:
        theme_name, fallback = (
            "applications-engineering",
            QStyle.StandardPixmap.SP_FileDialogInfoView,
        )
    return QIcon.fromTheme(
        theme_name,
        widget.style().standardIcon(fallback),
    )


def tool_icon(widget: QWidget, name: str) -> QIcon:
    """Obtiene un icono temático para una herramienta registrada."""
    if name == "certifications":
        return certification_icon(widget)
    if name == "pptp":
        return technical_document_icon(widget)
    theme_name, fallback = _TOOL_ICON_RULES.get(
        name,
        ("applications-system", QStyle.StandardPixmap.SP_ComputerIcon),
    )
    return QIcon.fromTheme(
        theme_name,
        widget.style().standardIcon(fallback),
    )


def decorate_widget(widget: QWidget) -> None:
    """Asigna iconos a botones y barras operativas y respeta excepciones de menú."""
    if not isValid(widget):
        return
    app = QApplication.instance()
    if app is None:
        return
    for button in widget.findChildren(QToolButton):
        if button.objectName() in {"budgetLevelMode", "referenceLevelMode"}:
            button.setIcon(budget_tree_icon(button))
        elif button.objectName() == "addBudgetButton":
            button.setIcon(budget_insert_icon(app))
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
                    if action.property("searchIcon"):
                        action.setIcon(search_icon(menu))
                    elif not (
                        action.property("toolIcon")
                        or action.property("informationIcon")
                    ):
                        action.setIcon(QIcon())
                    submenu = action.menu()
                    if submenu is not None:
                        for child in submenu.actions():
                            if not (
                                child.property("toolIcon")
                                or child.property("informationIcon")
                            ):
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
    QToolButton#budgetLevelMode, QToolButton#referenceLevelMode {{
        border: 1px solid transparent;
    }}
    QToolButton#budgetLevelMode:checked, QToolButton#referenceLevelMode:checked {{
        background: {blue_hover};
        border: 1px solid {accent};
        color: {blue_text};
        padding-top: 8px;
        padding-bottom: 6px;
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
    QTableWidget#qualityTrialsTable QHeaderView::section,
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
    QTreeWidget#budgetTree::item {{
        min-height: 34px;
        padding: 0 4px;
    }}
    QFileDialog QTreeView::item, QFileDialog QListView::item {{
        min-height: 36px;
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
