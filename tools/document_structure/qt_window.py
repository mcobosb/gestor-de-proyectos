"""Checklist y copia de plantillas de la estructura documental."""
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from tools._shared.reference_pill import add_reference_pill
from . import TOOL_LABEL
from .catalog import CATALOG, Entry
from .generation import generate, generated_at


class DocumentStructureWindow(QDialog):
    """Presenta el índice y permite una sola generación por proyecto guardado."""

    def __init__(self, app) -> None:
        """Crea el árbol y conecta las acciones de selección y copia."""
        super().__init__(app)
        self.app = app
        self.setWindowTitle(TOOL_LABEL)
        self.resize(960, 680)
        layout = QVBoxLayout(self)
        add_reference_pill(layout, "document_structure")
        note = QLabel("Marca las plantillas ODT que quieras copiar. Los apartados en gris solo sirven de referencia.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Documento o apartado", "Disponibilidad"])
        self.tree.setColumnWidth(0, 620)
        self.tree.setWordWrap(True)
        layout.addWidget(self.tree, 1)
        self._templates: list[tuple[QTreeWidgetItem, Path]] = []
        for entry in CATALOG:
            self._add_entry(self.tree, entry, Path())
        self.tree.expandToDepth(1)
        controls = QHBoxLayout()
        for label, callback in (
            ("Marcar todas", self._check_all), ("Desmarcar todas", self._uncheck_all),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        controls.addStretch()
        self._copy_button = QPushButton("Copiar plantillas…")
        self._copy_button.clicked.connect(self._copy_selected)
        controls.addWidget(self._copy_button)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        controls.addWidget(close)
        layout.addLayout(controls)
        self.tree.itemChanged.connect(self._update_selection)
        self.refresh_generation()

    def refresh_generation(self) -> None:
        """Actualiza el bloqueo según el registro persistente del proyecto activo."""
        self._generation_available = False
        project = self.app.project_path
        if project is None:
            message = "Guarda el proyecto antes de copiar sus plantillas."
        else:
            try:
                date = generated_at(project)
            except (OSError, ValueError) as exc:
                message = f"No se puede comprobar el registro de generación: {exc}"
            else:
                self._generation_available = date is None
                message = (f"Documentación ya generada ({date.replace('T', ' ', 1)})."
                           if date else "La estructura documental solo se puede generar una vez.")
        self._status.setText(message)
        self._refresh_copy_button()

    def _add_entry(
        self, parent: QTreeWidget | QTreeWidgetItem, entry: Entry, folder: Path,
        *, reason: str = "", annex: bool = False,
    ) -> None:
        """Agrega el nodo y hereda la indisponibilidad de sus padres."""
        reason = reason or entry.external
        title = entry.title
        if annex:
            title = f"Anejo n.º {int(entry.key.split()[0])}: {title}"
        item = QTreeWidgetItem(parent, [title, reason or ("Plantilla ODT" if entry.outline else "")])
        item.setCheckState(0, Qt.CheckState.Unchecked)
        if reason:
            item.setDisabled(True)
            gray = self.tree.palette().brush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
            item.setForeground(0, gray)
            item.setForeground(1, gray)
            item.setToolTip(0, reason)
        elif entry.outline:
            self._templates.append((item, folder / entry.filename))
        for child in entry.children:
            self._add_entry(item, child, folder / entry.key, reason=reason,
                            annex=entry.key == "02 Anejos")

    def _set_subtree(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        """Actualiza solo los elementos que tienen plantillas seleccionables."""
        if item.isDisabled():
            return
        item.setCheckState(0, state)
        for index in range(item.childCount()):
            self._set_subtree(item.child(index), state)

    def _update_selection(self, item: QTreeWidgetItem, column: int) -> None:
        """Propaga un cambio y calcula el estado de cada grupo sin contar los grises."""
        if column != 0 or item.isDisabled():
            return
        with QSignalBlocker(self.tree):
            self._set_subtree(item, item.checkState(0))
            parent = item.parent()
            while parent is not None:
                children = [parent.child(index) for index in range(parent.childCount())]
                states = {child.checkState(0) for child in children if not child.isDisabled()}
                parent.setCheckState(0, next(iter(states)) if len(states) == 1 else Qt.CheckState.PartiallyChecked)
                parent = parent.parent()
        self._refresh_copy_button()

    def _refresh_copy_button(self) -> None:
        """Habilita la copia solo si hay algún ODT marcado."""
        self._copy_button.setEnabled(self._generation_available and any(
            item.checkState(0) == Qt.CheckState.Checked for item, _ in self._templates
        ))

    def _set_all(self, state: Qt.CheckState) -> None:
        """Marca o desmarca todos los grupos disponibles."""
        with QSignalBlocker(self.tree):
            for index in range(self.tree.topLevelItemCount()):
                self._set_subtree(self.tree.topLevelItem(index), state)
        self._refresh_copy_button()

    def _check_all(self) -> None:
        """Marca todas las plantillas."""
        self._set_all(Qt.CheckState.Checked)

    def _uncheck_all(self) -> None:
        """Desmarca todas las plantillas."""
        self._set_all(Qt.CheckState.Unchecked)

    def _copy_selected(self) -> None:
        """Solicita una carpeta y copia las plantillas seleccionadas."""
        self.refresh_generation()
        project = self.app.project_path
        if project is None or not self._generation_available:
            return
        selected = [path for item, path in self._templates if item.checkState(0) == Qt.CheckState.Checked]
        if not selected:
            return
        folder = QFileDialog.getExistingDirectory(self, "Elegir destino de las plantillas")
        if not folder:
            return
        try:
            copied, skipped = generate(
                project, Path(folder), selected, self.app._project_title(),
                text_styles=self.app.report_settings.text_styles,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, TOOL_LABEL, f"No se completó la copia de las plantillas:\n{exc}")
            return
        finally:
            self.refresh_generation()
        master = "\nDocumento maestro de LibreOffice creado." if copied else ""
        QMessageBox.information(self, TOOL_LABEL, f"Plantillas copiadas: {copied}.\nArchivos ya existentes sin modificar: {skipped}.{master}")
