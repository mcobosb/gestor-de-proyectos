"""Editor Markdown para bibliotecas ZIP usado por herramientas PySide6."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ._library_editor import _signature, library_members, read_markdown, write_markdown
from ._library_history import add_revision


class LibraryEditorQt(QDialog):
    """Edita las fichas Markdown usadas por una biblioteca sin extraer el ZIP."""

    def __init__(
        self,
        parent: QWidget,
        archive_path: Path,
        title: str,
        *,
        used_members: set[str],
        on_saved=None,
        track_changes: bool = False,
    ) -> None:
        """Carga los nombres requeridos y prepara edición con control externo."""
        super().__init__(parent)
        self.archive_path = archive_path
        self.used_members = used_members
        self.on_saved = on_saved
        self.track_changes = track_changes
        self.signature = _signature(archive_path)
        self.selected_member: str | None = None
        self.original_source = ""
        self.dirty = False
        self._loading = False
        self.setWindowTitle(title)
        self.resize(1050, 680)
        layout = QVBoxLayout(self)
        info = QLabel(
            "Las fichas verdes forman parte de la justificación. Guarda cada "
            "edición antes de cambiar de documento."
        )
        layout.addWidget(info)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.members = QListWidget()
        self.members.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        for member in sorted(library_members(archive_path), key=str.casefold):
            item = QListWidgetItem(member)
            if member in used_members:
                item.setForeground(Qt.GlobalColor.darkGreen)
            self.members.addItem(item)
        self.members.currentItemChanged.connect(self._change_member)
        splitter.addWidget(self.members)
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        self.editor = QPlainTextEdit()
        self.editor.textChanged.connect(self._edited)
        editor_layout.addWidget(self.editor)
        revisions = QFormLayout()
        self.author = QLineEdit()
        self.change_date = QLineEdit(date.today().strftime("%d/%m/%Y"))
        self.description = QLineEdit()
        revisions.addRow("Autor de la revisión", self.author)
        revisions.addRow("Fecha (DD/MM/AAAA)", self.change_date)
        revisions.addRow("Descripción del cambio", self.description)
        editor_layout.addLayout(revisions)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Guardar")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        actions.addStretch()
        actions.addWidget(self.save_button)
        editor_layout.addLayout(actions)
        splitter.addWidget(editor)
        splitter.setSizes((260, 740))
        layout.addWidget(splitter, 1)
        if self.members.count():
            self.members.setCurrentRow(0)

    def _change_member(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        """Carga el documento seleccionado tras proteger cambios pendientes."""
        if not current or not self._confirm_pending():
            return
        try:
            source = read_markdown(self.archive_path, current.text())
        except ValueError as exc:
            QMessageBox.critical(self, "Biblioteca", str(exc))
            return
        self._loading = True
        self.selected_member = current.text()
        self.original_source = source
        self.editor.setPlainText(source)
        self.signature = _signature(self.archive_path)
        self.dirty = False
        self.save_button.setEnabled(False)
        self._loading = False

    def _edited(self) -> None:
        """Actualiza el estado de cambios sin reaccionar durante una carga."""
        if self._loading or self.selected_member is None:
            return
        self.dirty = self.editor.toPlainText() != self.original_source
        self.save_button.setEnabled(self.dirty)

    def _confirm_pending(self) -> bool:
        """Impide cambiar de ficha o cerrar perdiendo una edición."""
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Cambios sin guardar",
            "Hay cambios sin guardar en la biblioteca.",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        self.dirty = False
        return True

    def save(self) -> bool:
        """Añade revisión, guarda atómicamente y recarga la biblioteca."""
        if self.selected_member is None:
            return False
        try:
            source = self.editor.toPlainText()
            if self.track_changes:
                source = add_revision(
                    source,
                    self.author.text(),
                    self.change_date.text(),
                    self.description.text(),
                )
            self.signature = write_markdown(
                self.archive_path,
                self.selected_member,
                source,
                self.signature,
            )
            self._loading = True
            self.original_source = source
            self.editor.setPlainText(source)
            self._loading = False
            self.dirty = False
            self.save_button.setEnabled(False)
            if self.on_saved is not None:
                self.on_saved()
            QMessageBox.information(self, "Biblioteca", "Documento guardado.")
            return True
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return False

    def closeEvent(self, event: QCloseEvent) -> None:
        """Protege ediciones pendientes antes de cerrar el editor."""
        if self._confirm_pending():
            event.accept()
        else:
            event.ignore()
