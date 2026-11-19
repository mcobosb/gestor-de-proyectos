"""Traducción de botones y acciones de barra en la interfaz Qt."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTranslator
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QAbstractButton, QToolBar, QToolButton

from localization import get_language, translate


_QT_BUTTONS = {
    "OK": ("Aceptar", "OK", "OK"),
    "Cancel": ("Cancelar", "Cancel", "Annuler"),
    "Save": ("Guardar", "Save", "Enregistrer"),
    "Save All": ("Guardar todo", "Save All", "Tout enregistrer"),
    "Open": ("Abrir", "Open", "Ouvrir"),
    "Close": ("Cerrar", "Close", "Fermer"),
    "Apply": ("Aplicar", "Apply", "Appliquer"),
    "Reset": ("Restablecer", "Reset", "Réinitialiser"),
    "Restore Defaults": ("Restaurar valores predeterminados", "Restore Defaults", "Rétablir les valeurs par défaut"),
    "Help": ("Ayuda", "Help", "Aide"),
    "Discard": ("Descartar", "Discard", "Ignorer les modifications"),
    "Abort": ("Interrumpir", "Abort", "Abandonner"),
    "Retry": ("Reintentar", "Retry", "Réessayer"),
    "Ignore": ("Ignorar", "Ignore", "Ignorer"),
    "&Yes": ("&Sí", "&Yes", "&Oui"),
    "&No": ("&No", "&No", "&Non"),
    "Yes to &All": ("Sí a &todo", "Yes to &All", "Oui à &tout"),
    "N&o to All": ("N&o a todo", "N&o to All", "Non à t&out"),
    "&Open": ("&Abrir", "&Open", "&Ouvrir"),
    "&Save": ("&Guardar", "&Save", "&Enregistrer"),
    "&Choose": ("&Elegir", "&Choose", "&Choisir"),
    "Choose": ("Elegir", "Choose", "Choisir"),
    "&New Folder": ("&Nueva carpeta", "&New Folder", "&Nouveau dossier"),
    "List View": ("Vista de lista", "List View", "Vue en liste"),
    "Detail View": ("Vista detallada", "Detail View", "Vue détaillée"),
    "Create New Folder": ("Crear carpeta", "Create New Folder", "Créer un dossier"),
    "Parent Directory": ("Carpeta superior", "Parent Directory", "Dossier parent"),
    "Back": ("Atrás", "Back", "Précédent"),
    "Forward": ("Adelante", "Forward", "Suivant"),
    "Print": ("Imprimir", "Print", "Imprimer"),
    "Page Setup": ("Configurar página", "Page Setup", "Mise en page"),
}


class _QtButtonTranslator(QTranslator):
    """Proporciona las etiquetas Qt sin depender de catálogos externos."""

    def isEmpty(self) -> bool:
        """Informa a Qt de que debe emitir LanguageChange al instalarlo."""
        return False

    def translate(
        self, context: str, sourceText: str, disambiguation: str | None = None, n: int = -1,
    ) -> str | None:
        """Traduce los botones que Qt crea internamente."""
        if context in {
            "QPlatformTheme", "QFileDialog", "QPrintDialog", "QPrintPreviewDialog",
            "QInputDialog", "QMessageBox",
        } and sourceText in _QT_BUTTONS:
            return _QT_BUTTONS[sourceText][{"es": 0, "en": 1, "fr": 2}[get_language()]]
        return None


def localize_button(button: QAbstractButton | QAction, source: str | None = None) -> None:
    """Conserva la etiqueta original para cambiar de idioma sin ambigüedades."""
    current = button.text()
    if source is not None:
        button.setProperty("sourceButtonText", source)
    elif current != button.property("localizedButtonText"):
        button.setProperty("sourceButtonText", current)
    source = button.property("sourceButtonText")
    if isinstance(source, str):
        rendered = translate(source, source_language="es")
        button.setProperty("localizedButtonText", rendered)
        if current != rendered:
            button.setText(rendered)


class _ButtonLocalization(QObject):
    """Traduce controles creados después de arrancar la aplicación."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Localiza controles cuando se muestran por primera vez o se reabren."""
        if event.type() == QEvent.Type.Show:
            if isinstance(watched, QAbstractButton):
                self.update_button(watched)
            elif isinstance(watched, QToolBar):
                for action in watched.actions():
                    localize_button(action)
        return False

    @staticmethod
    def update_button(button: QAbstractButton) -> None:
        """Actualiza la acción compartida de un botón de barra si existe."""
        action = button.defaultAction() if isinstance(button, QToolButton) else None
        localize_button(action if action is not None else button)


def apply_button_language(app: QApplication) -> None:
    """Actualiza botones Qt y controles de la aplicación sin reiniciar."""
    translator = app.property("qtButtonTranslator")
    if isinstance(translator, _QtButtonTranslator):
        app.removeTranslator(translator)
    else:
        translator = _QtButtonTranslator(app)
        app.setProperty("qtButtonTranslator", translator)
        app.installEventFilter(_ButtonLocalization(app))
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    app.installTranslator(translator)
    for widget in app.allWidgets():
        if isinstance(widget, QAbstractButton):
            _ButtonLocalization.update_button(widget)
        elif isinstance(widget, QToolBar):
            for action in widget.actions():
                localize_button(action)
