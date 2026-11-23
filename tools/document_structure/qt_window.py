"""Checklist para generar la entrega documental del proyecto."""
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from PySide6.QtCore import QSignalBlocker, QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
    QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from localization import translate
from tools._shared.reference_pill import add_reference_pill
from . import TOOL_LABEL
from .catalog import (
    CATALOG, DOCUMENTATION_ANNEX_NUMBER, DOCUMENTATION_PROCEDURE_FIELDS, Entry,
)
from .generation import (
    generate_project_package, generated_at, default_master_name, suggested_destination,
)

ROLE = Qt.ItemDataRole.UserRole
FILE_ROLE = int(ROLE) + 1
_TOOL_ANNEXES = {
    "15": ("price", "Justificación de precios"),
    "16": ("planning", "Plan de Obra y Programación de los Trabajos"),
    "17": ("quality", "Plan de Aseguramiento y Control de la Calidad"),
    "18": ("rcd", "Estudio de Gestión de Residuos de Construcción y Demolición (RCDs)"),
    "19": ("safety", "Estudio de Seguridad y Salud (ESS) o Estudio Básico (EBSS)"),
}
_PLAN_FILTER = (
    "Documentos de planos (*.pdf *.odt *.odg *.dwg *.dxf *.dgn *.ifc *.shp *.zip);;"
    "Todos los archivos (*)"
)


class DocumentStructureWindow(QDialog):
    """Prepara las carpetas técnicas, los anejos y el documento maestro ODM."""

    def __init__(self, app) -> None:
        """Crea el árbol editable y conecta las acciones de generación."""
        super().__init__(app)
        self.app = app
        self._generation_error = ""
        self.setWindowTitle(TOOL_LABEL)
        self.resize(1180, 760)
        layout = QVBoxLayout(self)
        add_reference_pill(layout, "document_structure")

        naming = QFormLayout()
        self.master_name = QLineEdit(default_master_name(self.app.report_settings.expediente))
        master_row = QHBoxLayout()
        master_row.addWidget(self.master_name, 1)
        self._standards_button = QPushButton("Tipo de proyecto y normas…")
        self._standards_button.clicked.connect(self._open_project_standards)
        master_row.addWidget(self._standards_button)
        naming.addRow("Documento maestro (.odm)", master_row)
        layout.addLayout(naming)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Documento o apartado", "Disponibilidad", "Archivo de plano"])
        self.tree.setColumnWidth(0, 560)
        self.tree.setColumnWidth(1, 320)
        self.tree.setColumnWidth(2, 260)
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setStyleSheet(
            "QTreeWidget::item { padding: 5px 4px; } "
            "QTreeWidget QLineEdit { min-height: 34px; padding: 3px; }"
        )
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        layout.addWidget(self.tree, 1)

        self._annexes: list[QTreeWidgetItem] = []
        self._memory_documents: list[QTreeWidgetItem] = []
        self._plans: list[QTreeWidgetItem] = []
        self._pptp_item: QTreeWidgetItem | None = None
        self._budget_item: QTreeWidgetItem | None = None
        for index, entry in enumerate(CATALOG, 1):
            self._add_entry(self.tree, entry, index)
        self.tree.expandToDepth(1)

        controls = QHBoxLayout()
        for label, callback in (
            ("Marcar disponibles", self._check_all),
            ("Desmarcar todas", self._uncheck_all),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        self._plan_file_button = QPushButton("Asociar archivo de plano…")
        self._plan_file_button.setEnabled(False)
        self._plan_file_button.clicked.connect(self._associate_plan_file)
        controls.addWidget(self._plan_file_button)
        controls.addStretch()
        self._copy_button = QPushButton("Generar documentación…")
        self._copy_button.clicked.connect(self._copy_selected)
        controls.addWidget(self._copy_button)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        controls.addWidget(close)
        layout.addLayout(controls)

        self.tree.itemChanged.connect(self._update_selection)
        self.tree.currentItemChanged.connect(self._refresh_plan_file_button)
        self.refresh_generation()

    def _open_project_standards(self) -> None:
        """Abre la pestaña normativa y mantiene la selección documental al volver."""
        self.app.open_report_settings(project_standards=True)
        self.refresh_generation()

    def _item(self, parent, title: str, availability: str) -> QTreeWidgetItem:
        """Inserta una fila y conserva los metadatos de generación en el árbol."""
        item = QTreeWidgetItem(parent, [title, availability, ""])
        for column in range(3):
            item.setSizeHint(column, QSize(0, 52))
        item.setCheckState(0, Qt.CheckState.Unchecked)
        return item

    def _disable(self, item: QTreeWidgetItem, reason: str) -> None:
        """Marca un apartado informativo como no generable."""
        item.setDisabled(True)
        gray = self.tree.palette().brush(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        item.setForeground(0, gray)
        item.setForeground(1, gray)
        item.setToolTip(0, reason)
        item.setToolTip(1, reason)

    def _add_entry(
        self, parent, entry: Entry, document_number: int, *,
        annex: bool = False, inherited_reason: str = "",
    ) -> bool:
        """Agrega una fila generable, informativa o de selección de planos."""
        title = entry.title
        tool = _TOOL_ANNEXES.get(entry.key)
        is_pptp = document_number == 3 and title.startswith("DOCUMENTO")
        is_budget = document_number == 4 and title.startswith("DOCUMENTO")
        is_plan = document_number == 2 and title.startswith("Plano n.º")
        if tool:
            tool_name, title = tool
            item = self._item(parent, title, "Archivo base; completar desde la herramienta")
            outline = list(entry.outline) or [child.title for child in entry.children]
            item.setData(0, ROLE, {
                "kind": "tool", "tool": tool_name, "number": entry.key,
                "annex": True, "outline": outline,
            })
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            self._annexes.append(item)
            for section in outline:
                details = self._item(item, section, "Sección del archivo base")
                self._disable(details, "El contenido se completará posteriormente desde la herramienta.")
            return True
        if is_plan:
            item = self._item(parent, title, "Solo carpeta; archivo gráfico opcional")
            item.setData(0, ROLE, {"kind": "plan", "title": title})
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            self._plans.append(item)
            return True
        if is_pptp or is_budget:
            label = (
                "Archivo base; completar desde PPTP"
                if is_pptp else "Archivos base del presupuesto"
            )
            item = self._item(parent, title, label)
            item.setData(0, ROLE, {"kind": "pptp" if is_pptp else "budget"})
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if is_pptp:
                self._pptp_item = item
            else:
                self._budget_item = item
            for child in entry.children:
                details = self._item(item, child.title, "Incluido al generar el documento")
                self._disable(details, "El apartado forma parte del documento completo.")
            return True
        if annex and entry.key == DOCUMENTATION_ANNEX_NUMBER:
            item = self._item(parent, title, "Anejo con tabla editable de documentación y trámites")
            item.setData(0, ROLE, {
                "kind": "documentation",
                "number": entry.key,
                "fields": list(DOCUMENTATION_PROCEDURE_FIELDS),
            })
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            self._annexes.append(item)
            for field in DOCUMENTATION_PROCEDURE_FIELDS:
                details = self._item(item, field, "Fila de la tabla del anejo")
                self._disable(details, "El campo se incluirá como fila editable en la tabla del anejo.")
            return True
        if entry.outline:
            item = self._item(parent, title, "Documento editable según el catálogo y los estilos del proyecto")
            item.setData(0, ROLE, {
                "kind": "template",
                "number": entry.key,
                "title": title,
                "outline": list(entry.outline),
                "annex": annex,
            })
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            (self._annexes if annex else self._memory_documents).append(item)
            return True

        reason = entry.external or inherited_reason or "Apartado informativo."
        item = self._item(parent, title, reason if not entry.children else "")
        has_children = False
        child_annex = annex or entry.key == "02 Anejos"
        child_reason = entry.external or inherited_reason
        for child in entry.children:
            has_children = self._add_entry(
                item, child, document_number,
                annex=child_annex, inherited_reason=child_reason,
            ) or has_children
        if has_children:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            return True
        self._disable(item, reason)
        return False

    def refresh_generation(self) -> None:
        """Actualiza la disponibilidad de generación según el proyecto activo."""
        self._generation_available = False
        self._generation_error = ""
        project = self.app.project_path
        if project is None:
            self._generation_error = "Guarda el proyecto antes de generar la estructura documental."
        else:
            try:
                generated_at(project)
            except (OSError, ValueError) as exc:
                self._generation_error = f"No se puede comprobar el registro de generación: {exc}"
            else:
                self._generation_available = True
        self._refresh_copy_button()

    def _set_subtree(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        """Marca solo opciones disponibles dentro de un grupo."""
        if item.isDisabled():
            return
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            item.setCheckState(0, state)
        for index in range(item.childCount()):
            self._set_subtree(item.child(index), state)

    def _update_selection(self, item: QTreeWidgetItem, column: int) -> None:
        """Propaga las selecciones, sincroniza títulos editados y actualiza acciones."""
        if column != 0 or item.isDisabled():
            return
        data = item.data(0, ROLE)
        if isinstance(data, dict) and data.get("kind") in {
            "tool", "template", "plan", "documentation",
        }:
            data["title"] = item.text(0)
            item.setData(0, ROLE, data)
        with QSignalBlocker(self.tree):
            self._set_subtree(item, item.checkState(0))
            parent = item.parent()
            while parent is not None:
                children = [
                    parent.child(index) for index in range(parent.childCount())
                    if not parent.child(index).isDisabled()
                ]
                states = {child.checkState(0) for child in children}
                if states and parent.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    parent.setCheckState(
                        0, next(iter(states)) if len(states) == 1 else Qt.CheckState.PartiallyChecked,
                    )
                parent = parent.parent()
        self._refresh_copy_button()

    def _refresh_plan_file_button(self, current) -> None:
        """Habilita la asociación de archivo solo para un plano seleccionado."""
        data = current.data(0, ROLE) if current is not None else None
        self._plan_file_button.setEnabled(
            isinstance(data, dict) and data.get("kind") == "plan"
            and current.checkState(0) == Qt.CheckState.Checked
        )

    def _associate_plan_file(self) -> None:
        """Asocia un archivo existente a la carpeta del plano seleccionado."""
        item = self.tree.currentItem()
        data = item.data(0, ROLE) if item is not None else None
        if not isinstance(data, dict) or data.get("kind") != "plan":
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar archivo del plano", "", _PLAN_FILTER,
        )
        if filename:
            item.setData(0, FILE_ROLE, filename)
            item.setText(2, Path(filename).name)
            self._refresh_copy_button()

    def _selected_annexes(self) -> list[dict[str, object]]:
        """Devuelve los anejos seleccionados en el orden del catálogo."""
        result = []
        for item in self._annexes:
            if item.checkState(0) != Qt.CheckState.Checked:
                continue
            data = item.data(0, ROLE)
            selected = {
                "kind": data["kind"],
                "number": data["number"],
                "title": item.text(0).strip(),
                "outline": data.get("outline", []),
            }
            if data["kind"] == "tool":
                selected["tool"] = data["tool"]
            elif data["kind"] == "documentation":
                selected["fields"] = data["fields"]
            else:
                selected["outline"] = data["outline"]
            result.append(selected)
        return result

    def _selected_memory_documents(self) -> list[dict[str, object]]:
        """Devuelve los apartados de memoria que se generarán como ODT editables."""
        return [
            {
                "title": item.text(0).strip(),
                "number": item.data(0, ROLE)["number"],
                "outline": item.data(0, ROLE)["outline"],
            }
            for item in self._memory_documents
            if item.checkState(0) == Qt.CheckState.Checked
        ]

    def _selected_plans(self) -> list[dict[str, str]]:
        """Devuelve los planos y sus archivos opcionales en el orden del catálogo."""
        return [
            {
                "title": item.text(0).strip(),
                "file": item.data(0, FILE_ROLE) or "",
            }
            for item in self._plans
            if item.checkState(0) == Qt.CheckState.Checked
        ]

    def _refresh_copy_button(self) -> None:
        """Habilita la generación solo cuando hay una selección y un proyecto guardado."""
        selected = bool(
            self._selected_annexes() or self._selected_memory_documents() or self._selected_plans()
            or self._pptp_item and self._pptp_item.checkState(0) == Qt.CheckState.Checked
            or self._budget_item and self._budget_item.checkState(0) == Qt.CheckState.Checked
        )
        self._copy_button.setEnabled(self._generation_available and selected)
        self._copy_button.setToolTip(self._generation_error)

    def _set_all(self, state: Qt.CheckState) -> None:
        """Marca o desmarca las opciones generables del catálogo."""
        with QSignalBlocker(self.tree):
            for index in range(self.tree.topLevelItemCount()):
                self._set_subtree(self.tree.topLevelItem(index), state)
        self._refresh_copy_button()

    def _check_all(self) -> None:
        """Marca todas las opciones generables."""
        self._set_all(Qt.CheckState.Checked)

    def _uncheck_all(self) -> None:
        """Desmarca todas las opciones."""
        self._set_all(Qt.CheckState.Unchecked)

    def _copy_selected(self) -> None:
        """Genera la entrega en la carpeta elegida por el usuario."""
        self.refresh_generation()
        project = self.app.project_path
        if project is None or not self._generation_available:
            return
        try:
            initial_folder = suggested_destination(project)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, TOOL_LABEL, f"No se puede comprobar la generación anterior:\n{exc}",
            )
            return
        folder = QFileDialog.getExistingDirectory(
            self, translate("Seleccionar carpeta para la estructura documental", source_language="es"),
            str(initial_folder),
        )
        if not folder:
            return
        destination = Path(folder)
        try:
            has_previous_generation = generated_at(project, destination=destination) is not None
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, TOOL_LABEL, f"No se puede comprobar la generación anterior:\n{exc}",
            )
            return
        if has_previous_generation:
            answer = QMessageBox.warning(
                self,
                TOOL_LABEL,
                "Los documentos técnicos generados se reemplazarán. ¿Deseas continuar?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if not self.app.save_project():
            return
        project = self.app.project_path
        if project is None:
            return
        pptp = bool(self._pptp_item and self._pptp_item.checkState(0) == Qt.CheckState.Checked)
        budget = bool(self._budget_item and self._budget_item.checkState(0) == Qt.CheckState.Checked)
        try:
            master, _ = generate_project_package(
                project, self.app, self.master_name.text(),
                self._selected_annexes(), self._selected_plans(),
                memory_documents=self._selected_memory_documents(),
                include_pptp=pptp, include_budget=budget,
                destination=destination,
            )
        except (ImportError, OSError, ParseError, BadZipFile, ValueError) as exc:
            QMessageBox.critical(self, TOOL_LABEL, f"No se generó la documentación:\n{exc}")
            return
        finally:
            self.refresh_generation()
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Icon.Information)
        message_box.setWindowTitle(TOOL_LABEL)
        message_box.setTextFormat(Qt.TextFormat.PlainText)
        message_box.setText(str(master.parent))
        accept_button = message_box.addButton(
            "Aceptar", QMessageBox.ButtonRole.AcceptRole,
        )
        message_box.setDefaultButton(accept_button)
        message_box.setEscapeButton(accept_button)
        message_box.exec()
