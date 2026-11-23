"""Traducción de botones, acciones de barra y avisos en la interfaz Qt."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTranslator
from PySide6.QtGui import QAction
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QFileDialog, QMessageBox, QToolBar, QToolButton, QWidget,
)

from localization import get_language, translate


_QT_TEXT = {
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
    "File name:": ("Nombre de archivo:", "File name:", "Nom du fichier :"),
    "Files of type:": ("Tipo de archivo:", "Files of type:", "Type de fichiers :"),
    "Look in:": ("Buscar en:", "Look in:", "Regarder dans :"),
    "File &name:": ("&Nombre de archivo:", "File &name:", "&Nom du fichier :"),
    "Files of &type:": ("&Tipo de archivo:", "Files of &type:", "&Type de fichiers :"),
    "&Look in:": ("&Buscar en:", "&Look in:", "&Regarder dans :"),
    "Name": ("Nombre", "Name", "Nom"),
    "Size": ("Tamaño", "Size", "Taille"),
    "Type": ("Tipo", "Type", "Type"),
    "Date Modified": ("Fecha de modificación", "Date Modified", "Date de modification"),
    "%1 already exists.\nDo you want to replace it?": (
        "%1 ya existe.\n¿Quieres reemplazarlo?",
        "%1 already exists.\nDo you want to replace it?",
        "%1 existe déjà.\nVoulez-vous le remplacer ?",
    ),
}


def get_document_save_path(
    parent: QWidget | None, caption: str, filename: str, *,
    document_section: str | None = None,
) -> tuple[Path, str] | None:
    """Permite elegir PDF u ODT en un diálogo Qt traducido al idioma activo."""
    filters = {"odt": "ODT (*.odt)", "pdf": "PDF (*.pdf)"}
    localized_caption = translate(caption, source_language="es")
    if localized_caption == caption and caption.startswith("Generar "):
        localized_caption = (
            f"{translate('Generar', source_language='es')} "
            f"{translate(caption[len('Generar '):], source_language='es')}"
        )
    elif localized_caption == caption and caption.startswith("Guardar informe: "):
        localized_caption = (
            f"{translate('Guardar informe', source_language='es')}: "
            f"{translate(caption[len('Guardar informe: '):], source_language='es')}"
        )
    try:
        initial_path = document_save_initial_path(parent, filename, document_section)
    except (OSError, ValueError) as exc:
        QMessageBox.warning(
            parent,
            translate("Estructura documental", source_language="es"),
            f"No se puede consultar la ruta de la entrega documental:\n{exc}",
        )
        return None
    path, selected_filter = QFileDialog.getSaveFileName(
        parent,
        localized_caption,
        str(initial_path.with_suffix(".odt")),
        ";;".join(filters.values()),
        filters["odt"],
        QFileDialog.Option.DontUseNativeDialog,
    )
    if not path:
        return None

    selected_format = next(
        (kind for kind, file_filter in filters.items() if file_filter == selected_filter),
        None,
    )
    target = Path(path)
    suffix = target.suffix.casefold().lstrip(".")
    if selected_format is not None:
        suffix = selected_format
    elif suffix not in filters:
        suffix = "odt"
    if target.suffix.casefold() != f".{suffix}":
        target = target.with_suffix(f".{suffix}")
        if target.exists():
            message = QCoreApplication.translate(
                "QFileDialog", "%1 already exists.\nDo you want to replace it?"
            ).replace("%1", target.name)
            if QMessageBox.question(
                parent, translate("Confirmar reemplazo", source_language="es"), message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return None
    return target, suffix


def document_save_initial_path(
    parent: QWidget | None, filename: str, document_section: str | None = None,
) -> Path:
    """Construye la ruta inicial, usando la carpeta de entrega si existe."""
    initial_path = Path(filename)
    if document_section is None or parent is None:
        return initial_path
    app = getattr(parent, "app", parent)
    project = getattr(app, "project_path", None)
    if project is None:
        return initial_path
    from tools.document_structure.generation import suggested_document_directory

    directory = suggested_document_directory(Path(project), document_section)
    return directory / initial_path.name if directory is not None else initial_path


class _QtButtonTranslator(QTranslator):
    """Proporciona las etiquetas Qt sin depender de catálogos externos."""

    def isEmpty(self) -> bool:
        """Informa a Qt de que debe emitir LanguageChange al instalarlo."""
        return False

    def translate(
        self, context: str, sourceText: str, disambiguation: str | None = None, n: int = -1,
    ) -> str | None:
        """Traduce los textos que Qt crea internamente."""
        if context in {
            "QPlatformTheme", "QFileDialog", "QFileSystemModel", "QPrintDialog", "QPrintPreviewDialog",
            "QInputDialog", "QMessageBox",
        } and sourceText in _QT_TEXT:
            return _QT_TEXT[sourceText][{"es": 0, "en": 1, "fr": 2}[get_language()]]
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
