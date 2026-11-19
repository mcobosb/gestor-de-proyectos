"""Ventana PySide6 para estimación, revisión y documentación RCDs."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import os
from pathlib import Path
import re
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tools._library_editor_qt import LibraryEditorQt
from collaborative_storage import shared_library_path
from . import (
    GROUPS,
    HEADERS,
    INPUTS,
    MEASURED_NOTE,
    MODEL_INPUTS,
    RCD_BUDGET_SOURCE,
    TRANSPORT_REVIEW,
    WASTES,
    DemolitionMeasurement,
    RcdState,
    calculate,
    demolition_measurements,
    initial_state,
    ler_catalog,
    normalize,
    synchronize_budget,
    transfer_demolition,
    write_report,
)
from .study import DOCUMENT_FIELDS, DOCUMENTS, LIBRARY_PATH, load_documents


class RcdWindowQt(QDialog):
    """Coordina datos, residuos, documentación, presupuesto e informes RCDs."""

    def __init__(self, app) -> None:
        """Recupera el estado o lo deduce una vez del presupuesto actual."""
        super().__init__(app)
        self.app = app
        self.data = RcdState.from_dict(app.rcd_state) if app.rcd_state else initial_state(app)
        self.library_editor: LibraryEditorQt | None = None
        self.dirty = False
        self.variables: dict[str, QLineEdit] = {}
        self.group_variables: dict[tuple[str, str], QLineEdit] = {}
        self.documentation_texts = {}
        self.setWindowTitle("RCDs")
        self.resize(1280, 820)
        layout = QVBoxLayout(self)
        title = QLabel("RCDs — Datos de cálculo y revisión")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_inputs_page())
        self.pages.addWidget(self._build_review_page())
        self._restore_page()

    def _build_inputs_page(self) -> QWidget:
        """Crea campos numéricos, coeficientes y decisiones documentales."""
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = QHBoxLayout()
        library = QPushButton("Editar documentación del estudio…")
        library.clicked.connect(self.open_library_editor)
        actions.addWidget(library)
        demolition = QPushButton(
            "Llevar mediciones de demolición a transporte a vertedero"
        )
        demolition.clicked.connect(self.review_demolition)
        actions.addWidget(demolition)
        actions.addStretch()
        layout.addLayout(actions)
        self.measured_label = QLabel(
            MEASURED_NOTE if self.data.demolition else ""
        )
        self.measured_label.setWordWrap(True)
        layout.addWidget(self.measured_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for key, label, _ in INPUTS:
            row = QHBoxLayout()
            field = QLineEdit(
                "" if self.data.inputs[key] is None else str(self.data.inputs[key])
            )
            field.setMaximumWidth(180)
            self.variables[key] = field
            row.addWidget(field)
            source = self.data.sources.get(key, "Valor del modelo.")
            if self.data.inputs[key] is None:
                source = "PENDIENTE — " + source
            hint = QLabel(source)
            hint.setWordWrap(True)
            row.addWidget(hint, 1)
            form.addRow(label, row)
        for key, label, *_ in GROUPS:
            group = QHBoxLayout()
            for name in ("percentage", "density"):
                field = QLineEdit(str(self.data.groups[key][name]))
                field.setMaximumWidth(130)
                if key == "earth" and name == "percentage":
                    field.setEnabled(False)
                self.group_variables[key, name] = field
                group.addWidget(QLabel("%" if name == "percentage" else "t/m³"))
                group.addWidget(field)
            form.addRow(label, group)
        from PySide6.QtWidgets import QPlainTextEdit

        heading = QLabel("Decisiones particulares de la obra")
        heading.setStyleSheet("font-weight: 600;")
        form.addRow(heading)
        for key, label in DOCUMENT_FIELDS.items():
            text = QPlainTextEdit(self.data.documentation.get(key, ""))
            text.setMaximumHeight(110)
            text.textChanged.connect(
                lambda selected=key: self._documentation_changed(selected)
            )
            self.documentation_texts[key] = text
            form.addRow(label, text)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        accept = QPushButton("Aceptar y revisar residuos")
        accept.clicked.connect(self.accept_inputs)
        actions.addWidget(accept)
        layout.addLayout(actions)
        return page

    def _build_review_page(self) -> QWidget:
        """Crea selección de residuos, vista económica y acciones finales."""
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = QHBoxLayout()
        for text, slot in (
            ("Datos de cálculo", self.show_inputs),
            ("Editar documentación del estudio…", self.open_library_editor),
            ("Vínculos LER–BCCA…", self.show_ler_links),
            ("Generar estudio RCDs", self.generate_report),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch()
        self.save_button = QPushButton("Guardar RCDs en el proyecto")
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        help_text = QLabel(
            "Marca los residuos aplicables. Las coincidencias son orientativas; "
            "revisa especialmente residuos peligrosos y códigos LER."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.waste_table = QTableWidget(0, 6)
        self.waste_table.setHorizontalHeaderLabels((
            "X", "Clave", "Grupo", "LER", "Residuo", "Detectado / revisión",
        ))
        self.waste_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.waste_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.waste_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        self.waste_table.itemChanged.connect(self._waste_changed)
        layout.addWidget(self.waste_table, 2)
        self.preview_table = QTableWidget(0, 5)
        self.preview_table.setHorizontalHeaderLabels(HEADERS)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.preview_table, 1)
        self.status_label = QLabel()
        layout.addWidget(self.status_label)
        return page

    def _restore_page(self) -> None:
        """Muestra los datos o el resumen según el estado guardado."""
        self.pages.setCurrentIndex(0)
        if self.app.rcd_state:
            self._populate_review()

    def _documentation_changed(self, key: str) -> None:
        """Actualiza una decisión documental y el indicador pendiente."""
        self.data.documentation[key] = self.documentation_texts[key].toPlainText()
        self.changed()

    def collect_inputs(self) -> RcdState:
        """Valida y convierte todos los controles del primer paso."""
        raw = self.data.to_dict()
        for key, field in self.variables.items():
            value = field.text().strip()
            raw["inputs"][key] = (
                None
                if self.data.demolition and key in MODEL_INPUTS and not value
                else value
            )
        for (key, name), field in self.group_variables.items():
            raw["groups"][key][name] = field.text()
        raw["documentation"] = {
            key: text.toPlainText()
            for key, text in self.documentation_texts.items()
        }
        candidate = RcdState.from_dict(raw)
        calculate(candidate)
        for key in self.variables:
            if candidate.inputs[key] != self.data.inputs[key]:
                candidate.sources[key] = "Revisado manualmente."
        return candidate

    def accept_inputs(self) -> None:
        """Valida el cálculo y pasa a la revisión de residuos."""
        try:
            self.data = self.collect_inputs()
        except (TypeError, ValueError) as exc:
            QMessageBox.critical(self, "RCDs", str(exc))
            return
        self.changed()
        self._populate_review()
        self.pages.setCurrentIndex(1)

    def _populate_review(self) -> None:
        """Carga marcas, evidencias y cálculos económicos."""
        self.waste_table.blockSignals(True)
        self.waste_table.setRowCount(0)
        group_names = {key: label for key, label, *_ in GROUPS}
        for key, group, code, description, _ in WASTES:
            row = self.waste_table.rowCount()
            self.waste_table.insertRow(row)
            selected = QTableWidgetItem()
            selected.setFlags(
                selected.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
            )
            selected.setCheckState(
                Qt.CheckState.Checked
                if self.data.selected[key]
                else Qt.CheckState.Unchecked
            )
            selected.setData(Qt.ItemDataRole.UserRole, key)
            self.waste_table.setItem(row, 0, selected)
            values = (
                key,
                group_names[group],
                code,
                description,
                "; ".join(self.data.evidence.get(key, []))
                or "Sin coincidencia; selección manual disponible",
            )
            for column, value in enumerate(values, 1):
                self.waste_table.setItem(row, column, QTableWidgetItem(value))
        self.waste_table.blockSignals(False)
        self.refresh_preview()

    def _waste_changed(self, item: QTableWidgetItem) -> None:
        """Actualiza la selección sin modificar evidencias automáticas."""
        if item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(key, str):
            return
        self.data.selected[key] = item.checkState() == Qt.CheckState.Checked
        self.changed()
        self.refresh_preview()

    def refresh_preview(self) -> None:
        """Recalcula la vista con la misma función usada por los informes."""
        try:
            sections, _ = calculate(self.data)
        except ValueError:
            return
        rows = [row for _label, section_rows in sections for row in section_rows]
        self.preview_table.setRowCount(0)
        for values in rows:
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            for column, value in enumerate(values):
                self.preview_table.setItem(
                    row, column, QTableWidgetItem(str(value))
                )
        self.status_label.setText(
            f"{sum(self.data.selected.values())} residuos seleccionados. "
            f"Total: {sections[-1][1][0][3]} €."
        )

    def show_inputs(self) -> None:
        """Vuelve al formulario de datos sin perder las selecciones."""
        self.pages.setCurrentIndex(0)

    def changed(self) -> None:
        """Publica el estado RCDs en el proyecto y marca guardado pendiente."""
        self.app.rcd_state = self.data.to_dict()
        self.app._mark_dirty()
        self.dirty = True

    def save(self) -> None:
        """Crea o sincroniza las siete partidas y guarda el proyecto."""
        if self.app._has_external_tool_chapter("rcd"):
            QMessageBox.warning(
                self,
                "RCDs",
                "El presupuesto contiene un capítulo RCDs externo; se conserva "
                "sin crear ni modificar otro.",
            )
            return
        try:
            synchronize_budget(self.app, self.data)
        except ValueError as exc:
            QMessageBox.critical(
                self, "No se pudo incorporar la gestión de residuos", str(exc)
            )
            return
        self.changed()
        if self.app.save_project():
            self.dirty = False
            self.status_label.setText(
                "RCDs y capítulo Gestión de residuos guardados con el proyecto."
            )

    def review_demolition(self) -> None:
        """Abre la revisión de mediciones que pueden pasar a transporte."""
        try:
            rows = demolition_measurements(self.app, self.data)
        except ValueError as exc:
            QMessageBox.critical(self, "RCDs", str(exc))
            return
        if not rows:
            QMessageBox.information(
                self,
                "RCDs",
                "No se han encontrado partidas de demolición, retirada o desmontaje.",
            )
            return
        dialog = DemolitionDialogQt(self, rows)
        dialog.exec()

    def open_library_editor(self) -> None:
        """Abre una única biblioteca de textos RCDs."""
        if self.library_editor is not None and self.library_editor.isVisible():
            self.library_editor.raise_()
            return
        self.library_editor = LibraryEditorQt(
            self,
            shared_library_path(self.app, "rcd", LIBRARY_PATH),
            "Biblioteca del estudio RCDs",
            used_members={name for name, _ in DOCUMENTS},
            on_saved=self.reload_library,
            track_changes=True,
        )
        self.library_editor.show()

    def reload_library(self) -> None:
        """Valida los ocho documentos necesarios tras una edición."""
        try:
            load_documents(shared_library_path(self.app, "rcd", LIBRARY_PATH))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Documentación RCDs", str(exc))

    def show_ler_links(self) -> None:
        """Muestra vínculos exactos BCCA–LER sin alterar las selecciones."""
        try:
            rows = ler_catalog.match_rows(self.app.budget_items)
        except ValueError as exc:
            QMessageBox.critical(self, "Vínculos LER–BCCA", str(exc))
            return
        if not rows:
            QMessageBox.information(
                self,
                "Vínculos LER–BCCA",
                "Ninguna partida coincide por código exacto con la tabla BCCA2023.",
            )
            return
        dialog = LerLinksDialogQt(self, rows)
        dialog.exec()

    def generate_report(self) -> None:
        """Genera el estudio RCDs en el formato configurado."""
        if self.library_editor is not None and self.library_editor.dirty:
            QMessageBox.critical(
                self,
                "Documentación RCDs",
                "Guarda o descarta los cambios de biblioteca antes de exportar.",
            )
            return
        extension = (
            ".odt" if self.app.report_settings.output_format == "odt" else ".pdf"
        )
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Generar estudio RCDs",
            f"Estudio de gestión de RCDs{extension}",
            f"Estudio RCDs (*{extension})",
        )
        if not target:
            return
        target = Path(target).with_suffix(extension)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, suffix=extension, delete=False
            ) as output:
                temporary = Path(output.name)
            write_report(
                self.data,
                self.app._project_title(),
                self.app.report_settings,
                temporary,
                project_pem=sum(item.import_total for item in self.app.budget_items),
                app=self.app,
            )
            temporary.replace(target)
            temporary = None
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "No se pudo generar el informe", str(exc))
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        QMessageBox.information(self, "RCDs", f"Informe guardado:\n{target}")
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError:
            pass

    def closeEvent(self, event: QCloseEvent) -> None:
        """Protege una edición abierta de biblioteca antes de cerrar."""
        if self.library_editor is not None and self.library_editor.isVisible():
            self.library_editor.close()
            if self.library_editor.isVisible():
                event.ignore()
                return
        event.accept()


class LerLinksDialogQt(QDialog):
    """Tabla de indicios LER–BCCA sin modificación del modelo."""

    def __init__(self, parent: QWidget, rows: list[list[str]]) -> None:
        """Muestra los vínculos y su condición de revisión."""
        super().__init__(parent)
        self.setWindowTitle("Vínculos LER–BCCA")
        self.resize(1050, 500)
        layout = QVBoxLayout(self)
        note = QLabel(ler_catalog.MATCH_NOTE)
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(ler_catalog.MATCH_HEADERS)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        for values in rows:
            row = table.rowCount()
            table.insertRow(row)
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(table)


class DemolitionDialogQt(QDialog):
    """Revisa conversiones de demoliciones y su transporte destino."""

    def __init__(
        self, owner: RcdWindowQt, rows: list[DemolitionMeasurement]
    ) -> None:
        """Prepara materiales, factores y partidas de transporte candidatas."""
        super().__init__(owner)
        self.owner = owner
        self.rows = rows
        self.materials = {
            f"{group} — {code} — {description}": (key, group_key)
            for key, group_key, code, description, _ in WASTES
            for group in [
                next(label for group_id, label, *_ in GROUPS if group_id == group_key)
            ]
        }
        self.targets = [
            item
            for item in owner.app.budget_items
            if re.search(r"transport", normalize(item.summary))
            and re.search(
                r"vertedero|residu|gestor|planta",
                normalize(f"{item.summary} {item.text}"),
            )
        ]
        self.edits: list[tuple[QCheckBox, QLineEdit, QComboBox]] = []
        self.setWindowTitle("Demoliciones a transporte a vertedero")
        self.resize(1250, 650)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Revisa las partidas detectadas. m³: factor 1; m²: espesor en m; "
            "m: sección en m²; ud: volumen por unidad; t: m³/t. "
            "No se suponen conversiones ni esponjamiento."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.target = QComboBox()
        self.target.addItems([
            f"{index + 1}. {item.code} — {item.summary} ({item.unit})"
            for index, item in enumerate(self.targets)
        ])
        self.target.addItem("Crear nueva partida de transporte a vertedero (m³)")
        form.addRow("Partida de destino", self.target)
        layout.addLayout(form)
        self.table = QTableWidget(len(rows), 5)
        self.table.setHorizontalHeaderLabels((
            "Incluir", "Partida / medición", "Cantidad", "Factor a m³", "Material / LER",
        ))
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for index, row in enumerate(rows):
            include = QCheckBox()
            include.setChecked(row.included)
            self.table.setCellWidget(index, 0, include)
            self.table.setItem(index, 1, QTableWidgetItem(row.source))
            self.table.setItem(
                index, 2, QTableWidgetItem(f"{row.quantity:g} {row.unit}")
            )
            factor = QLineEdit("" if row.factor is None else str(row.factor))
            unit = normalize(row.unit).replace("³", "3").replace("^", "").replace(" ", "")
            factor.setReadOnly(unit == "m3")
            self.table.setCellWidget(index, 3, factor)
            material = QComboBox()
            material.addItems(tuple(self.materials))
            selected = next(
                (
                    label
                    for label, (key, _group) in self.materials.items()
                    if key == row.waste_key
                ),
                "",
            )
            material.setCurrentText(selected)
            self.table.setCellWidget(index, 4, material)
            self.edits.append((include, factor, material))
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        apply = QPushButton("Copiar mediciones y usar volúmenes en RCDs")
        apply.clicked.connect(self.apply)
        actions.addWidget(apply)
        layout.addLayout(actions)

    def apply(self) -> None:
        """Valida todas las líneas y las copia sin modificar las demoliciones."""
        choice = self.target.currentIndex()
        if choice < 0:
            QMessageBox.critical(
                self, "RCDs", "Selecciona la partida de transporte de destino."
            )
            return
        rows = []
        try:
            for row, (include, factor, material) in zip(self.rows, self.edits):
                raw = asdict(row)
                raw["included"] = include.isChecked()
                raw["factor"] = factor.text().strip() or None
                raw["waste_key"], raw["group"] = self.materials.get(
                    material.currentText(), ("", "")
                )
                rows.append(DemolitionMeasurement.from_dict(raw))
            target = self.targets[choice] if choice < len(self.targets) else None
            created = transfer_demolition(
                self.owner.app, self.owner.data, rows, target
            )
        except ValueError as exc:
            QMessageBox.critical(self, "RCDs", str(exc))
            return
        self.owner.changed()
        self.owner.measured_label.setText(MEASURED_NOTE)
        total = sum(
            (row.volume for row in rows if row.included),
            Decimal(0),
        )
        notice = (
            f"Copiadas {sum(row.included for row in rows)} mediciones: "
            f"{total:.2f} m³.\n\n"
            "Las demoliciones originales se conservan; los volúmenes revisados "
            "sustituyen al reparto porcentual."
        )
        QMessageBox.warning(
            self,
            "Transporte creado: revisión obligatoria" if created else "Mediciones copiadas",
            f"{notice}\n\n{TRANSPORT_REVIEW}" if created else notice,
        )
        self.accept()
