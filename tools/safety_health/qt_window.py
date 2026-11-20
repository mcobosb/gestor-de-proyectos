"""Ventanas PySide6 del asistente de Seguridad y Salud."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path, PurePosixPath
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tools._library_editor import library_members
from tools._library_editor_qt import LibraryEditorQt
from tools._shared.reference_pill import add_reference_pill
from collaborative_storage import shared_library_path
from qt_localization import get_document_save_path
from . import (
    CARE_CENTERS_URL,
    SAFETY_BUDGET_SOURCE,
    LIBRARY_PATH,
    _budget_safety_lines,
    _decimal,
    _format_decimal,
    _format_euros,
    _study_criteria_values,
    evaluate_study_criteria,
    proposed_lines,
    restore_state,
    serialize_state,
    synchronize_budget,
    write_study_report,
)
from .care_centers import FIELDS, format_centers, restore_centers
from .chapter_selection import restore_chapter_selection, study_chapters, study_items
from .external_conditions import APPLICABILITY, EXTERNAL_CONDITIONS, restore_external_conditions
from .regulations import (
    REGULATIONS,
    initially_selected,
    law_proposals,
    load_ntps,
    ntp_proposals,
    restore_review,
    review_context,
)
from .study_report import study_technical_review, study_work_units
from .technical_conditions import (
    TECHNICAL_BLOCKS,
    restore_technical_review,
    technical_review_confirmed,
)


class ChapterSelectionDialogQt(QDialog):
    """Confirma los capítulos que forman parte del estudio preventivo."""

    def __init__(self, app, on_continue) -> None:
        """Presenta capítulos de obra excluyendo el generado."""
        super().__init__(app)
        self.app = app
        self.on_continue = on_continue
        self.setWindowTitle("Seguridad y salud — capítulos a analizar")
        self.resize(850, 560)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Marca los capítulos que se analizarán. RCDs y Control de Calidad "
            "se desmarcan inicialmente; el capítulo preventivo no se analiza."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.choices: dict[str, QCheckBox] = {}
        for chapter in study_chapters(app):
            checkbox = QCheckBox(
                f"{chapter.code} — {chapter.title} ({chapter.items} partidas)"
            )
            checkbox.setChecked(chapter.selected)
            self.choices[chapter.code] = checkbox
            layout.addWidget(checkbox)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        accept = QPushButton("Continuar con el análisis")
        accept.clicked.connect(self.accept_selection)
        actions.addWidget(accept)
        layout.addLayout(actions)

    def accept_selection(self) -> None:
        """Exige al menos un capítulo y entrega la selección al asistente."""
        choices = {
            code: checkbox.isChecked()
            for code, checkbox in self.choices.items()
        }
        if not any(choices.values()):
            QMessageBox.critical(
                self, "Seguridad y salud", "Marca al menos un capítulo."
            )
            return
        self.accept()
        self.on_continue(choices)


class CareCentersEditorQt(QWidget):
    """Editor Qt de centros asistenciales en texto libre o tabla SICESS."""

    def __init__(self, parent: QWidget, state: dict) -> None:
        """Restaura ambas representaciones sin perder proyectos antiguos."""
        super().__init__(parent)
        self.rows = restore_centers(state)
        if not self.rows:
            self.rows = [{key: "" for key, _ in FIELDS}]
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        text_page = QWidget()
        text_layout = QVBoxLayout(text_page)
        self.text = QPlainTextEdit(
            state.get(
                "centros_asistenciales_texto",
                state.get("centros_asistenciales", ""),
            )
        )
        text_layout.addWidget(self.text)
        self.tabs.addTab(text_page, "Texto libre")
        table_page = QWidget()
        table_layout = QHBoxLayout(table_page)
        left = QVBoxLayout()
        self.list = QListWidget()
        for row in self.rows:
            self.list.addItem(row["nombre"] or "—")
        self.list.currentRowChanged.connect(self.select_center)
        left.addWidget(self.list)
        add_remove = QHBoxLayout()
        add = QPushButton("Añadir")
        add.clicked.connect(self.add_center)
        add_remove.addWidget(add)
        remove = QPushButton("Eliminar")
        remove.clicked.connect(self.remove_center)
        add_remove.addWidget(remove)
        left.addLayout(add_remove)
        table_layout.addLayout(left, 1)
        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for key, label in FIELDS:
            field = QLineEdit()
            self.fields[key] = field
            form.addRow(label, field)
        table_layout.addLayout(form, 2)
        to_text = QPushButton("Pasar tabla a texto")
        to_text.clicked.connect(self.to_text)
        left.addWidget(to_text)
        self.tabs.addTab(table_page, "Tabla SICESS")
        if state.get("centros_asistenciales_modo") == "tabla":
            self.tabs.setCurrentIndex(1)
        self.current = 0
        self.list.setCurrentRow(0)
        self.select_center(0)

    def commit_center(self) -> None:
        """Guarda los campos activos en la fila actual."""
        if 0 <= self.current < len(self.rows):
            self.rows[self.current] = {
                key: field.text() for key, field in self.fields.items()
            }
            self.list.item(self.current).setText(self.rows[self.current]["nombre"] or "—")

    def select_center(self, row: int) -> None:
        """Muestra una ficha conservando la anterior."""
        if row < 0:
            return
        self.commit_center()
        self.current = row
        for key, field in self.fields.items():
            field.setText(self.rows[row][key])

    def add_center(self) -> None:
        """Añade una fila SICESS vacía."""
        self.commit_center()
        self.rows.append({key: "" for key, _ in FIELDS})
        self.list.addItem("—")
        self.list.setCurrentRow(len(self.rows) - 1)

    def remove_center(self) -> None:
        """Elimina la fila seleccionada."""
        if not self.rows:
            return
        if QMessageBox.question(
            self, "Centros Asistenciales", "¿Eliminar el centro seleccionado?"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.list.takeItem(self.current)
        del self.rows[self.current]
        if not self.rows:
            self.rows.append({key: "" for key, _ in FIELDS})
            self.list.addItem("—")
        self.current = min(self.current, len(self.rows) - 1)
        self.list.setCurrentRow(self.current)
        self.select_center(self.current)

    def to_text(self) -> None:
        """Convierte la tabla al texto final."""
        self.commit_center()
        generated = format_centers(self.rows)
        previous = self.text.toPlainText().strip()
        if previous and previous != generated and QMessageBox.question(
            self,
            "Centros Asistenciales",
            "¿Sustituir el texto libre por el generado desde la tabla?",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.text.setPlainText(generated)
        self.tabs.setCurrentIndex(0)

    def collect_state(self) -> dict:
        """Devuelve ambas entradas y el texto vigente."""
        self.commit_center()
        mode = "tabla" if self.tabs.currentIndex() == 1 else "texto"
        manual = self.text.toPlainText().strip()
        return {
            "centros_asistenciales": format_centers(self.rows) if mode == "tabla" else manual,
            "centros_asistenciales_modo": mode,
            "centros_asistenciales_texto": manual,
            "centros_asistenciales_datos": [dict(row) for row in self.rows],
        }


class SafetyHealthWindowQt(QDialog):
    """Asistente principal de criterios, información y revisiones preventivas."""

    def __init__(self, app) -> None:
        """Evalúa el estudio y carga la revisión guardada."""
        super().__init__(app)
        self.app = app
        if app.safety_health_state:
            self.lines, percentage = restore_state(app.safety_health_state)
        else:
            self.lines = _budget_safety_lines(app)
            percentage = Decimal(str(app.report_settings.safety_health_percentage))
        if not self.lines:
            self.lines = proposed_lines(study_items(app))
        self.study_criteria = evaluate_study_criteria(app)
        saved = _study_criteria_values(
            app.safety_health_state.get("study_criteria")
        )
        restore_external_conditions(
            app.safety_health_state.get("external_conditions")
        )
        self._stored_percentage = percentage
        self._proposal: SafetyProposalDialogQt | None = None
        self._conditions: ExternalConditionsDialogQt | None = None
        self._regulatory: RegulatoryReviewDialogQt | None = None
        self._technical: TechnicalReviewDialogQt | None = None
        self.setWindowTitle("Seguridad y salud")
        self.resize(1120, 800)
        layout = QVBoxLayout(self)
        add_reference_pill(layout, "safety_health")
        criteria = QGroupBox("Criterios del artículo 4.1 de la LPRL")
        criteria_layout = QVBoxLayout(criteria)
        values = saved or self.study_criteria.fulfilled
        descriptions = (
            f"Presupuesto con IVA > 450.759,08 €: {_format_euros(self.study_criteria.contract_budget)}.",
            "Duración > 30 días y más de 20 trabajadores simultáneos: "
            f"{self.study_criteria.duration_days} días; máximo "
            f"{_format_decimal(self.study_criteria.peak_workers)} trabajadores/día.",
            "Volumen de mano de obra > 500 jornadas: "
            f"{_format_decimal(self.study_criteria.worker_days)} jornadas.",
            "Túneles, galerías, conducciones subterráneas o presas: "
            f"{'Sí' if self.study_criteria.special_work else 'No'}.",
        )
        self.criteria_checks: list[QCheckBox] = []
        for selected, text in zip(values, descriptions):
            check = QCheckBox(text)
            check.setChecked(selected)
            check.toggled.connect(self._criteria_changed)
            self.criteria_checks.append(check)
            criteria_layout.addWidget(check)
        self.study_result = QLabel()
        self.study_result.setStyleSheet("font-weight: 600;")
        criteria_layout.addWidget(self.study_result)
        self._update_study_result()
        layout.addWidget(criteria)
        basics = QGroupBox("Información básica del Estudio de Seguridad y Salud")
        basics_layout = QVBoxLayout(basics)
        basics_layout.addWidget(QLabel("Resumen de la actuación"))
        from . import study_work_summary

        self.summary_text = QPlainTextEdit(study_work_summary(app))
        self.summary_text.setMaximumHeight(100)
        self.summary_text.textChanged.connect(self.commit_basic_state)
        basics_layout.addWidget(self.summary_text)
        register = QPushButton("Centros Asistenciales — abrir registro SICESS")
        register.clicked.connect(self.open_care_centers_registry)
        basics_layout.addWidget(register, alignment=Qt.AlignmentFlag.AlignLeft)
        self.centers_editor = CareCentersEditorQt(basics, app.safety_health_state)
        basics_layout.addWidget(self.centers_editor)
        layout.addWidget(basics, 1)
        actions = QHBoxLayout()
        for text, slot in (
            ("Condicionantes externos…", self.open_external_conditions),
            ("Normativa y NTP…", self.open_regulatory_review),
            ("Condiciones técnicas…", self.open_technical_review),
            ("Revisar propuesta de unidades…", self.open_proposal),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        layout.addLayout(actions)

    @property
    def selected_study_criteria(self) -> tuple[bool, bool, bool, bool]:
        """Devuelve las cuatro decisiones editables."""
        return tuple(check.isChecked() for check in self.criteria_checks)

    def _update_study_result(self) -> None:
        """Actualiza el tipo de estudio indicado."""
        self.study_result.setText(
            "Según los criterios marcados, procede un Estudio de Seguridad y Salud."
            if any(self.selected_study_criteria)
            else "Según los criterios marcados, procede un Estudio Básico de Seguridad y Salud."
        )

    def _criteria_changed(self) -> None:
        """Persiste las cuatro decisiones legales."""
        self._update_study_result()
        self.commit_basic_state()

    def commit_basic_state(self) -> None:
        """Actualiza resumen, centros y criterios sin alterar las partidas."""
        state = dict(self.app.safety_health_state) or serialize_state(
            self.lines, self._stored_percentage
        )
        state["study_criteria"] = {
            f"criterion_{index}_met": checked
            for index, checked in enumerate(self.selected_study_criteria, 1)
        }
        state["resumen_actuacion"] = self.summary_text.toPlainText().strip()
        state.update(self.centers_editor.collect_state())
        self.app.safety_health_state = state
        self.app._mark_dirty()

    def open_external_conditions(self) -> ExternalConditionsDialogQt:
        """Abre una única revisión de condicionantes externos."""
        if self._conditions is not None and self._conditions.isVisible():
            self._conditions.raise_()
            return self._conditions
        self._conditions = ExternalConditionsDialogQt(self)
        self._conditions.show()
        return self._conditions

    def open_regulatory_review(self) -> RegulatoryReviewDialogQt | None:
        """Abre el revisor de normativa y NTP para las unidades actuales."""
        if self._regulatory is not None and self._regulatory.isVisible():
            self._regulatory.raise_()
            return self._regulatory
        try:
            units = study_work_units(self.app)
            if not units:
                raise ValueError("No hay unidades de obra para revisar.")
            self._regulatory = RegulatoryReviewDialogQt(self, units)
        except ValueError as exc:
            QMessageBox.critical(self, "Normativa y NTP", str(exc))
            return None
        self._regulatory.show()
        return self._regulatory

    def open_technical_review(self) -> TechnicalReviewDialogQt | None:
        """Abre la revisión de condiciones técnicas detectadas."""
        if self._technical is not None and self._technical.isVisible():
            self._technical.raise_()
            return self._technical
        try:
            context, proposals, pending = study_technical_review(self.app)
            self._technical = TechnicalReviewDialogQt(
                self, context, proposals, pending
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Condiciones técnicas", str(exc))
            return None
        self._technical.show()
        return self._technical

    def open_proposal(self) -> SafetyProposalDialogQt:
        """Abre la propuesta revisable de unidades preventivas."""
        self.commit_basic_state()
        if self._proposal is not None and self._proposal.isVisible():
            self._proposal.raise_()
            return self._proposal
        self._proposal = SafetyProposalDialogQt(self)
        self._proposal.show()
        return self._proposal

    def open_care_centers_registry(self) -> None:
        """Abre el registro SICESS oficial."""
        if not webbrowser.open(CARE_CENTERS_URL):
            QMessageBox.critical(
                self, "Centros Asistenciales", "No se pudo abrir el registro."
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Protege editores secundarios antes de cerrar."""
        for window in (
            self._proposal, self._conditions, self._regulatory, self._technical
        ):
            if window is not None and window.isVisible():
                window.close()
            if window is not None and window.isVisible():
                event.ignore()
                return
        self.commit_basic_state()
        event.accept()


class ExternalConditionsDialogQt(QDialog):
    """Revisión de los ocho condicionantes externos."""

    def __init__(self, owner: SafetyHealthWindowQt) -> None:
        """Carga texto y aplicabilidad vigente."""
        super().__init__(owner)
        self.owner = owner
        conditions = restore_external_conditions(
            owner.app.safety_health_state.get("external_conditions")
        )
        self.setWindowTitle("Condicionantes externos")
        self.resize(920, 600)
        layout = QVBoxLayout(self)
        self.rows: dict[str, tuple[QPlainTextEdit, QComboBox]] = {}
        for key, label, default in EXTERNAL_CONDITIONS:
            group = QGroupBox(label)
            row_layout = QHBoxLayout(group)
            current = conditions.get(
                key, {"description": default, "applicability": "pending"}
            )
            text = QPlainTextEdit(current["description"])
            text.setMaximumHeight(70)
            applicability = QComboBox()
            for code, text_label in APPLICABILITY.items():
                applicability.addItem(text_label, code)
            applicability.setCurrentIndex(
                applicability.findData(current["applicability"])
            )
            row_layout.addWidget(text, 1)
            row_layout.addWidget(applicability)
            self.rows[key] = text, applicability
            layout.addWidget(group)
        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Guardar condicionantes")
        save.clicked.connect(self.save)
        actions.addWidget(save)
        layout.addLayout(actions)

    def save(self) -> None:
        """Valida y guarda el estado independiente del presupuesto."""
        conditions = {
            key: {
                "description": text.toPlainText(),
                "applicability": str(applicability.currentData()),
            }
            for key, (text, applicability) in self.rows.items()
        }
        try:
            state = dict(self.owner.app.safety_health_state)
            state["external_conditions"] = restore_external_conditions(conditions)
        except ValueError as exc:
            QMessageBox.critical(self, "Seguridad y salud", str(exc))
            return
        self.owner.app.safety_health_state = state
        self.owner.app._mark_dirty()
        self.accept()


class RegulatoryReviewDialogQt(QDialog):
    """Confirma normativa y asociaciones NTP antes del estudio."""

    def __init__(self, owner: SafetyHealthWindowQt, units) -> None:
        """Carga propuestas y decisiones previas."""
        super().__init__(owner)
        self.owner, self.units = owner, units
        self.catalog = load_ntps()
        self.proposals = ntp_proposals(units, self.catalog)
        saved = restore_review(owner.app.safety_health_state.get("regulatory_review"))
        self.laws = law_proposals(units)
        self.laws.update(saved.get("laws", {}))
        self.ntps = {code: dict(choices) for code, choices in saved.get("ntps", {}).items()}
        for unit, proposals in self.proposals.items():
            choices = self.ntps.setdefault(unit, {})
            for code, reason in proposals.items():
                choices.setdefault(code, initially_selected(self.catalog[code], reason))
        self.setWindowTitle("Confirmar normativa y NTP del estudio")
        self.resize(1180, 740)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._laws_tab(), "Normativa de aplicación")
        tabs.addTab(self._ntp_tab(), "Unidades de obra y NTP")
        layout.addWidget(tabs, 1)
        actions = QHBoxLayout()
        official = QPushButton("Ver NTP oficial")
        official.clicked.connect(self.open_official)
        actions.addWidget(official)
        actions.addStretch()
        confirm = QPushButton("Confirmar selección")
        confirm.clicked.connect(self.accept_review)
        actions.addWidget(confirm)
        layout.addLayout(actions)

    def _laws_tab(self) -> QWidget:
        """Construye la tabla de disposiciones."""
        page = QWidget()
        layout = QVBoxLayout(page)
        self.law_table = QTableWidget(0, 3)
        self.law_table.setHorizontalHeaderLabels(
            ("Incluir", "Disposición", "Unidades / condición")
        )
        for law in REGULATIONS:
            row = self.law_table.rowCount()
            self.law_table.insertRow(row)
            selected = QTableWidgetItem()
            selected.setData(Qt.ItemDataRole.UserRole, law.code)
            selected.setCheckState(
                Qt.CheckState.Checked
                if self.laws[law.code]
                else Qt.CheckState.Unchecked
            )
            selected.setFlags(
                selected.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            self.law_table.setItem(row, 0, selected)
            related = [
                unit.code for unit in self.units
                if set(law.triggers).intersection((*unit.risks, *unit.measures))
            ]
            self.law_table.setItem(row, 1, QTableWidgetItem(law.title))
            self.law_table.setItem(
                row,
                2,
                QTableWidgetItem(
                    ", ".join(dict.fromkeys(related))
                    if law.triggers else "Marco general; confirmar condiciones."
                ),
            )
        self.law_table.itemChanged.connect(self._law_changed)
        layout.addWidget(self.law_table)
        return page

    def _ntp_tab(self) -> QWidget:
        """Construye selector y tabla de NTP."""
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        self.unit_choice = QComboBox()
        self.unit_choice.addItems(list(dict.fromkeys(unit.code for unit in self.units)))
        controls.addWidget(self.unit_choice)
        self.ntp_choice = QComboBox()
        self.ntp_choice.setEditable(True)
        for entry in self.catalog.values():
            self.ntp_choice.addItem(f"{entry.code} — {entry.title}", entry.code)
        controls.addWidget(self.ntp_choice, 1)
        assign = QPushButton("Asignar NTP")
        assign.clicked.connect(self.assign)
        controls.addWidget(assign)
        layout.addLayout(controls)
        self.ntp_table = QTableWidget(0, 6)
        self.ntp_table.setHorizontalHeaderLabels(
            ("Incluir", "Unidad", "NTP", "Título", "Aviso", "Origen")
        )
        self.ntp_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        self.ntp_table.itemChanged.connect(self._ntp_changed)
        self.ntp_table.itemSelectionChanged.connect(self.show_detail)
        layout.addWidget(self.ntp_table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(110)
        layout.addWidget(self.detail)
        self.refresh_ntps()
        return page

    def _law_changed(self, item: QTableWidgetItem) -> None:
        """Actualiza una disposición."""
        if item.column() == 0:
            code = item.data(Qt.ItemDataRole.UserRole)
            self.laws[str(code)] = item.checkState() == Qt.CheckState.Checked

    def refresh_ntps(self) -> None:
        """Reconstruye las parejas unidad/NTP y conserva filas vacías."""
        self.ntp_table.blockSignals(True)
        self.ntp_table.setRowCount(0)
        for index in range(self.unit_choice.count()):
            unit = self.unit_choice.itemText(index)
            choices = self.ntps.get(unit, {})
            if not choices:
                row = self.ntp_table.rowCount()
                self.ntp_table.insertRow(row)
                self.ntp_table.setItem(
                    row, 3, QTableWidgetItem("Sin propuesta: asignar manualmente.")
                )
            for code, selected in choices.items():
                entry = self.catalog[code]
                row = self.ntp_table.rowCount()
                self.ntp_table.insertRow(row)
                check = QTableWidgetItem()
                check.setData(Qt.ItemDataRole.UserRole, (unit, code))
                check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                check.setCheckState(
                    Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
                )
                values = (
                    unit, code, entry.title, entry.status,
                    self.proposals.get(unit, {}).get(code, "Asignación manual."),
                )
                self.ntp_table.setItem(row, 0, check)
                for column, value in enumerate(values, 1):
                    self.ntp_table.setItem(
                        row, column, QTableWidgetItem(value)
                    )
        self.ntp_table.blockSignals(False)

    def _ntp_changed(self, item: QTableWidgetItem) -> None:
        """Actualiza una pareja unidad/NTP."""
        if item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            unit, code = key
            self.ntps[unit][code] = item.checkState() == Qt.CheckState.Checked

    def assign(self) -> None:
        """Asocia una NTP válida a la unidad seleccionada."""
        unit = self.unit_choice.currentText()
        code = self.ntp_choice.currentData()
        if code not in self.catalog:
            QMessageBox.critical(
                self, "Normativa y NTP", "Selecciona una NTP del catálogo."
            )
            return
        self.ntps.setdefault(unit, {})[str(code)] = True
        self.refresh_ntps()

    def show_detail(self) -> None:
        """Muestra riesgos, medidas y enlace de la NTP activa."""
        row = self.ntp_table.currentRow()
        item = self.ntp_table.item(row, 0) if row >= 0 else None
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not key:
            self.detail.clear()
            return
        unit_code, code = key
        units = [unit for unit in self.units if unit.code == unit_code]
        entry = self.catalog[code]
        self.detail.setPlainText("\n".join((
            f"{code} — {entry.title} ({entry.publication}; {entry.status})",
            "Riesgos: " + ", ".join(dict.fromkeys(r for u in units for r in u.risks)),
            "Medidas: " + ", ".join(dict.fromkeys(m for u in units for m in u.measures)),
            self.proposals.get(unit_code, {}).get(code, "Asignación manual."),
            entry.url,
        )))

    def open_official(self) -> None:
        """Abre el enlace oficial de la NTP seleccionada."""
        row = self.ntp_table.currentRow()
        item = self.ntp_table.item(row, 0) if row >= 0 else None
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        if key and not webbrowser.open(self.catalog[key[1]].url):
            QMessageBox.critical(
                self, "Normativa y NTP", "No se pudo abrir la ficha oficial."
            )

    def accept_review(self) -> None:
        """Guarda la revisión con la huella exacta de unidades."""
        state = dict(self.owner.app.safety_health_state)
        state["regulatory_review"] = restore_review({
            "context": review_context(self.units),
            "laws": self.laws,
            "ntps": self.ntps,
        })
        self.owner.app.safety_health_state = state
        self.owner.app._mark_dirty()
        self.accept()


class TechnicalReviewDialogQt(QDialog):
    """Confirma todos los bloques técnicos del estudio."""

    def __init__(
        self, owner: SafetyHealthWindowQt, context: str,
        proposals: dict[str, str], pending: list[str],
    ) -> None:
        """Restaura decisiones anteriores y advierte los cambios de contexto."""
        super().__init__(owner)
        self.owner, self.context = owner, context
        self.proposals, self.pending = proposals, pending
        saved = restore_technical_review(
            owner.app.safety_health_state.get("technical_review")
        )
        previous = saved.get("blocks", {})
        self.choices = {
            block.key: previous.get(block.key, block.key in proposals)
            for block in TECHNICAL_BLOCKS
        }
        self.setWindowTitle("Confirmar condiciones técnicas del estudio")
        self.resize(1060, 680)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Las propuestas son indicios. Confirma también bloques no detectados "
            "si la obra real los requiere."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ("Incluir", "Condición técnica", "Origen / revisión")
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        for block in TECHNICAL_BLOCKS:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setData(Qt.ItemDataRole.UserRole, block.key)
            check.setCheckState(
                Qt.CheckState.Checked
                if self.choices[block.key]
                else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(block.title))
            self.table.setItem(
                row,
                2,
                QTableWidgetItem(
                    proposals.get(
                        block.key, "Sin propuesta: revisar manualmente."
                    )
                ),
            )
        self.table.itemChanged.connect(self.changed)
        self.table.itemSelectionChanged.connect(self.show_detail)
        layout.addWidget(self.table, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(120)
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        actions.addStretch()
        confirm = QPushButton("Confirmar selección")
        confirm.clicked.connect(self.accept_review)
        actions.addWidget(confirm)
        layout.addLayout(actions)

    def changed(self, item: QTableWidgetItem) -> None:
        """Actualiza una decisión técnica."""
        if item.column() == 0:
            self.choices[str(item.data(Qt.ItemDataRole.UserRole))] = (
                item.checkState() == Qt.CheckState.Checked
            )

    def show_detail(self) -> None:
        """Muestra la razón y avisos del bloque activo."""
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        key = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
        reason = self.proposals.get(key, "Comprueba los procedimientos reales.")
        self.detail.setPlainText("\n".join((reason, *self.pending)))

    def accept_review(self) -> None:
        """Guarda la revisión completa con su huella."""
        state = dict(self.owner.app.safety_health_state)
        state["technical_review"] = restore_technical_review({
            "context": self.context,
            "blocks": self.choices,
        })
        self.owner.app.safety_health_state = state
        self.owner.app._mark_dirty()
        self.accept()


class SafetyProposalDialogQt(QDialog):
    """Revisa unidades preventivas, crea el capítulo y exporta el estudio."""

    def __init__(self, owner: SafetyHealthWindowQt) -> None:
        """Carga la propuesta vigente con selección editable."""
        super().__init__(owner)
        self.owner = owner
        self.app = owner.app
        self.lines = owner.lines
        self.library_editor: LibraryEditorQt | None = None
        self.setWindowTitle("Propuesta de unidades de Seguridad y Salud")
        self.resize(1120, 720)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.percentage = QLineEdit(str(owner._stored_percentage))
        header.addWidget(QLabel("Porcentaje orientativo:"))
        header.addWidget(self.percentage)
        header.addWidget(QLabel("%"))
        header.addStretch()
        library = QPushButton("Editar biblioteca de fichas…")
        library.clicked.connect(self.open_library_editor)
        header.addWidget(library)
        layout.addLayout(header)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("Incluir", "Código", "Partida", "Ud.", "Precio", "Peso")
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        for line in self.lines:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setData(Qt.ItemDataRole.UserRole, line.code)
            check.setCheckState(
                Qt.CheckState.Checked if line.selected else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, check)
            for column, value in enumerate(
                (line.code, line.summary, line.unit, f"{line.price:.2f}", f"{line.weight:.2f}"),
                1,
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.itemChanged.connect(self.changed)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        report = QPushButton("Generar estudio completo")
        report.clicked.connect(self.generate_report)
        actions.addWidget(report)
        actions.addStretch()
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        self.insert_button = QPushButton("Aceptar y crear capítulo")
        self.insert_button.clicked.connect(self.accept_proposal)
        actions.addWidget(self.insert_button)
        layout.addLayout(actions)
        self.app.budget_refreshed.connect(self._refresh_insert_action)
        self._refresh_insert_action()

    def _refresh_insert_action(self) -> None:
        """Impide volver a insertar un capítulo preventivo existente."""
        exists = any(item.source.startswith(SAFETY_BUDGET_SOURCE) for item in self.app.budget_items)
        exists = exists or self.app._has_external_tool_chapter("safety")
        self.insert_button.setEnabled(not exists)
        self.insert_button.setToolTip("El presupuesto ya contiene un capítulo de Seguridad y Salud." if exists else "")

    def changed(self, item: QTableWidgetItem) -> None:
        """Actualiza una línea seleccionada."""
        if item.column() != 0:
            return
        code = str(item.data(Qt.ItemDataRole.UserRole))
        line = next(line for line in self.lines if line.code == code)
        line.selected = item.checkState() == Qt.CheckState.Checked

    def create_chapter(self) -> bool:
        """Valida y crea o sincroniza el capítulo preventivo."""
        if self.app._has_external_tool_chapter("safety"):
            QMessageBox.warning(
                self,
                "Seguridad y salud",
                "Existe un capítulo preventivo externo; se conserva.",
            )
            return False
        try:
            percentage = _decimal(self.percentage.text())
            if not Decimal() <= percentage <= 100:
                raise ValueError("El porcentaje debe estar entre 0 y 100.")
            synchronize_budget(
                self.app,
                self.lines,
                percentage,
                **self.owner.centers_editor.collect_state(),
                study_criteria=self.owner.selected_study_criteria,
                resumen_actuacion=self.owner.summary_text.toPlainText().strip(),
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Seguridad y salud", str(exc))
            return False
        self.owner._stored_percentage = percentage
        self.app._set_status(
            "Capítulo Seguridad y Salud creado; revisa sus mediciones."
        )
        return True

    def accept_proposal(self) -> None:
        """Crea el capítulo y deja abierta la revisión."""
        self.create_chapter()

    def open_library_editor(self) -> None:
        """Abre las fichas ESS utilizadas por unidades y recursos."""
        used_members = _used_safety_members(self.app)
        if self.library_editor is not None and self.library_editor.isVisible():
            self.library_editor.raise_()
            return
        self.library_editor = LibraryEditorQt(
            self,
            LIBRARY_PATH,
            "Biblioteca de fichas de Seguridad y Salud",
            used_members=used_members,
            track_changes=True,
        )
        self.library_editor.show()

    def generate_report(self) -> None:
        """Confirma revisión y genera el estudio completo."""
        if self.library_editor is not None and self.library_editor.dirty:
            QMessageBox.critical(
                self,
                "Seguridad y salud",
                "Guarda los cambios de biblioteca antes de generar.",
            )
            return
        if not self.create_chapter():
            return
        try:
            units = study_work_units(self.app)
            if restore_review(
                self.app.safety_health_state.get("regulatory_review")
            ).get("context") != review_context(units):
                dialog = self.owner.open_regulatory_review()
                if dialog is None or dialog.exec() != QDialog.DialogCode.Accepted:
                    return
            context, _proposals, _pending = study_technical_review(self.app)
            if not technical_review_confirmed(
                self.app.safety_health_state.get("technical_review"), context
            ):
                dialog = self.owner.open_technical_review()
                if dialog is None or dialog.exec() != QDialog.DialogCode.Accepted:
                    return
            selection = get_document_save_path(
                self,
                "Generar Estudio de Seguridad y Salud",
                "Estudio de Seguridad y Salud.pdf",
            )
            if selection is None:
                return
            path, _output_format = selection
            write_study_report(self.app, path)
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "No se pudo generar el informe", str(exc))
            return
        self.app._set_status(f"Estudio de Seguridad y Salud guardado en {path}")
        QMessageBox.information(
            self, "Seguridad y salud", f"Estudio completo generado:\n{path}"
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        """Protege ediciones pendientes de biblioteca."""
        if self.library_editor is not None and self.library_editor.isVisible():
            self.library_editor.close()
            if self.library_editor.isVisible():
                event.ignore()
                return
        event.accept()


def _used_safety_members(app) -> set[str]:
    """Obtiene fichas y recursos ESS usados por el presupuesto."""
    item_codes = {item.code.upper() for item in app.budget_items}
    resource_codes = {
        resource.code.upper()
        for item in app.budget_items
        for resource, _quantity, _price in app._report_components(item)
        if resource.ctype in {"2", "3"}
    }
    used = set()
    for member in library_members(
        shared_library_path(app, "safety_health", LIBRARY_PATH)
    ):
        path = PurePosixPath(member)
        if path.suffix.casefold() != ".md":
            continue
        if member.startswith("UNI/"):
            code = path.stem.removeprefix("FICHA_ESS_").split("-", 1)[0].upper()
            if code[:2].isdigit() and code[2:].isalpha() and any(
                item_code.startswith(code) for item_code in item_codes
            ):
                used.add(member)
        elif (
            member.startswith("RECURSOS/")
            and path.stem.split("-", 1)[0].upper() in resource_codes
        ):
            used.add(member)
    return used
