"""Arranque y splash screen del frontend PySide6."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from app_paths import application_directory
from budget_config import APPLICATION_NAME
from budget_qt_window import BudgetMainWindow


def show_startup_splash(app: QApplication) -> QSplashScreen | None:
    """Muestra la imagen inicial a 520 px de ancho, sin deformarla."""
    image_path = application_directory(__file__) / "iconos" / "Arranque.jpg"
    pixmap = QPixmap(str(image_path))
    if pixmap.isNull():
        return None
    pixmap = pixmap.scaledToWidth(520, Qt.TransformationMode.SmoothTransformation)
    rounded = QPixmap(pixmap.size())
    rounded.fill(Qt.GlobalColor.transparent)
    outline = QPainterPath()
    outline.addRoundedRect(QRectF(rounded.rect()), 12, 12)
    with QPainter(rounded) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(outline, QBrush(pixmap))
    splash = QSplashScreen(rounded, Qt.WindowType.FramelessWindowHint)
    splash.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    splash.show()
    app.processEvents()
    return splash


def main() -> int:
    """Inicia el ciclo de eventos principal de PySide6."""
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APPLICATION_NAME)
    app.setOrganizationName("mcobosb")
    app.setWindowIcon(
        QIcon(str(application_directory(__file__) / "iconos" / "logo.ico"))
    )
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    elapsed = QElapsedTimer()
    splash = show_startup_splash(app)
    elapsed.start()
    window = BudgetMainWindow()
    if splash is None:
        window.showMaximized()
    else:
        def show_main_window() -> None:
            """Retira la imagen antes de mostrar la ventana maximizada."""
            splash.hide()
            window.showMaximized()
            splash.close()

        QTimer.singleShot(max(0, 1000 - elapsed.elapsed()), window, show_main_window)
    return app.exec()
