"""Arranque y splash screen del frontend PySide6."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from app_paths import application_directory
from budget_config import APPLICATION_NAME
from budget_qt_window import BudgetMainWindow


def show_startup_splash(app: QApplication) -> QSplashScreen | None:
    """Muestra la imagen inicial mientras se construye la ventana."""
    image_path = application_directory(__file__) / "iconos" / "Arranque.jpg"
    pixmap = QPixmap(str(image_path))
    if pixmap.isNull():
        return None
    splash = QSplashScreen(pixmap.scaled(612, 245, Qt.AspectRatioMode.KeepAspectRatio))
    splash.show()
    app.processEvents()
    return splash


def main() -> int:
    """Inicia el ciclo de eventos principal de PySide6."""
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APPLICATION_NAME)
    app.setOrganizationName("mcobosb")
    splash = show_startup_splash(app)
    window = BudgetMainWindow()
    if splash is not None:
        QTimer.singleShot(1800, splash.close)
    window.showMaximized()
    if splash is not None:
        splash.raise_()
    return app.exec()
