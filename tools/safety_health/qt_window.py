"""Ventanas PySide6 del asistente de Seguridad y Salud."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path, PurePosixPath
import webbrowser

from PySide6.QtCore import QByteArray, QSignalBlocker, Qt
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
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
    QSplitter,
    QStackedWidget,
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
    SafetyLine,
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
from .drawings import (
    add_user_sheet, available_sheets, prepare_svg_for_qt, read_sheet, restore_drawings,
)
from .regulations import (
    REGULATIONS,
    initially_selected,
    law_proposals,
    load_ntps,
    ntp_proposals,
    restore_review,
    review_context,
)
from .document import study_technical_review, study_work_units
from .technical_conditions import (
    TECHNICAL_BLOCKS,
    restore_technical_review,
    technical_review_confirmed,
)
from .related_units import (
    RelatedSafetyItem,
    load_related_safety_data,
    related_alternative_groups_for_work_families,
)


class ChapterSelectionDialogQt(QDialog):
    """Confirma los capítulos que forman parte del estudio preventivo."""

    def __init__(self, app, on_continue) -> None:
        """Presenta capítulos de obra excluyendo el generado."""
        super().__init__(app)
        self.app = app
        self.on_continue = on_continue
        self.setWindowTitle("Seguridad y salud — capítulos a analizar")
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        width = 850 if available is None else min(850, max(700, available.width() - 48))
        height = 560 if available is None else min(560, max(360, available.height() - 64))
        self.resize(width, height)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Marca los capítulos que se analizarán. RCDs y Control de Calidad "
            "se desmarcan inicialmente; el capítulo preventivo no se analiza."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        choices_scroll = QScrollArea()
        choices_scroll.setWidgetResizable(True)
        choices_content = QWidget()
        choices_layout = QVBoxLayout(choices_content)
        choices_layout.setContentsMargins(0, 0, 0, 0)
        self.choices: dict[str, QCheckBox] = {}
        for chapter in study_chapters(app):
            checkbox = QCheckBox(
                f"{chapter.code} — {chapter.title} ({chapter.items} partidas)"
            )
            checkbox.setChecked(chapter.selected)
            self.choices[chapter.code] = checkbox
            choices_layout.addWidget(checkbox)
        choices_layout.addStretch()
        choices_scroll.setWidget(choices_content)
        layout.addWidget(choices_scroll, 1)
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
        self.current = -1
        self.list.setCurrentRow(0)

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
        row = self.current
        with QSignalBlocker(self.list):
            self.list.takeItem(row)
            del self.rows[row]
            if not self.rows:
                self.rows.append({key: "" for key, _ in FIELDS})
                self.list.addItem("—")
            row = min(row, len(self.rows) - 1)
            self.list.setCurrentRow(row)
        self.current = -1
        self.select_center(row)

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
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        self.resize(
            1120 if available is None else min(1120, max(1, available.width() - 48)),
            800 if available is None else min(800, max(1, available.height() - 64)),
        )
        layout = QVBoxLayout(self)
        add_reference_pill(layout, "safety_health")
        self.pages = QTabWidget()
        layout.addWidget(self.pages, 1)
        basic_page = QWidget()
        basic_layout = QVBoxLayout(basic_page)
        self.pages.addTab(basic_page, "Información y revisión")
        self.basic_scroll = QScrollArea()
        self.basic_scroll.setWidgetResizable(True)
        basic_content = QWidget()
        content_layout = QVBoxLayout(basic_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        basic_layout.addWidget(self.basic_scroll, 1)
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
        content_layout.addWidget(criteria)
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
        content_layout.addWidget(basics, 1)
        self.basic_scroll.setWidget(basic_content)
        actions = QGridLayout()
        for index, (text, slot) in enumerate((
            ("Condicionantes externos…", self.open_external_conditions),
            ("Normativa y NTP…", self.open_regulatory_review),
            ("Condiciones técnicas…", self.open_technical_review),
            ("Revisar propuesta de unidades…", self.open_proposal),
        )):
            button = QPushButton(text)
            button.clicked.connect(slot)
            if index < 2:
                actions.addWidget(button, 0, index)
            else:
                actions.addWidget(button, index - 1, 0, 1, 2)
        basic_layout.addLayout(actions)
        self._create_drawings_page()

    def _create_drawings_page(self) -> None:
        """Presenta la selección manual, el visor y el formato de los planos."""
        saved = restore_drawings(self.app.safety_health_state.get("drawings"))
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel(
            "Selecciona las fichas del formato elegido que correspondan a la obra tras su revisión técnica. "
            "Las fichas personales también están disponibles en ambos formatos. "
            "Se utiliza el cajetín configurado para el proyecto. Revisa la legibilidad "
            "y el contenido antes de utilizar los planos."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel("Formato de los planos:"))
        self.drawings_format = QComboBox()
        self.drawings_format.addItems(["A4", "A3"])
        self.drawings_format.setCurrentText(saved["paper"] if saved else "A4")
        row.addWidget(self.drawings_format)
        row.addStretch()
        add_button = QPushButton("Añadir ficha SVG…")
        add_button.clicked.connect(self._add_drawing)
        row.addWidget(add_button)
        layout.addLayout(row)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.drawings_table = QTableWidget(0, 2)
        self.drawings_table.setHorizontalHeaderLabels(("Incluir", "Ficha"))
        self.drawings_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.drawings_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.drawings_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.drawings_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        splitter.addWidget(self.drawings_table)
        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.addWidget(QLabel("Vista previa"))
        self.drawings_preview = QStackedWidget()
        self.drawings_preview_message = QLabel("Selecciona una ficha para verla.")
        self.drawings_preview_message.setWordWrap(True)
        self.drawings_preview_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drawings_preview.addWidget(self.drawings_preview_message)
        self.drawings_svg = QSvgWidget()
        self.drawings_svg.setMinimumSize(200, 260)
        self.drawings_svg.renderer().setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.drawings_preview.addWidget(self.drawings_svg)
        preview_layout.addWidget(self.drawings_preview, 1)
        splitter.addWidget(preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self._populate_drawings(saved)
        self.pages.addTab(page, "Planos")
        self.drawings_format.currentTextChanged.connect(self._drawings_format_changed)
        self.drawings_table.itemChanged.connect(self._save_drawings)
        self.drawings_table.itemSelectionChanged.connect(self._preview_drawing)
        self.pages.currentChanged.connect(self._drawings_page_changed)

    def _populate_drawings(self, saved: dict | None) -> None:
        """Añade también referencias guardadas a fichas que ya no están disponibles."""
        self._drawing_sheets = {sheet.code: sheet for sheet in available_sheets()}
        selected = saved["selected"] if saved else {}
        paper = self.drawings_format.currentText()
        rows = [
            (sheet.code, sheet.title)
            for sheet in self._drawing_sheets.values()
            if sheet.paper == paper or sheet.path is not None
        ]
        rows.extend(
            (code, f"Ficha no disponible: {code}")
            for code in selected if code not in self._drawing_sheets
        )
        blocker = QSignalBlocker(self.drawings_table)
        self.drawings_table.setRowCount(0)
        for code, title in rows:
            row_index = self.drawings_table.rowCount()
            self.drawings_table.insertRow(row_index)
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            check.setData(Qt.ItemDataRole.UserRole, code)
            enabled = selected.get(code, False)
            check.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            self.drawings_table.setItem(row_index, 0, check)
            self.drawings_table.setItem(row_index, 1, QTableWidgetItem(title))
        del blocker

    def _preview_drawing(self) -> None:
        """Muestra la ficha seleccionada sin cambiar su inclusión en el estudio."""
        row = self.drawings_table.currentRow()
        check = self.drawings_table.item(row, 0) if row >= 0 else None
        sheet = self._drawing_sheets.get(str(check.data(Qt.ItemDataRole.UserRole))) if check else None
        if sheet is None:
            self.drawings_preview_message.setText(
                "La ficha seleccionada no está disponible en esta biblioteca."
                if check else "Selecciona una ficha para verla."
            )
            self.drawings_preview.setCurrentIndex(0)
            return
        try:
            data = read_sheet(sheet)
            self.drawings_svg.load(QByteArray(prepare_svg_for_qt(data)))
            if not self.drawings_svg.renderer().isValid():
                raise ValueError(f"No se puede representar la ficha {sheet.title}.")
        except ValueError as exc:
            self.drawings_preview_message.setText(str(exc))
            self.drawings_preview.setCurrentIndex(0)
            return
        self.drawings_preview.setCurrentIndex(1)

    def _add_drawing(self) -> None:
        """Incorpora una copia del SVG a la biblioteca personal."""
        path, _filter = QFileDialog.getOpenFileName(
            self, "Añadir ficha de Seguridad y Salud", "", "Fichas SVG (*.svg)",
        )
        if not path:
            return
        try:
            sheet = add_user_sheet(Path(path))
            self._populate_drawings(restore_drawings(self.app.safety_health_state.get("drawings")))
        except ValueError as exc:
            QMessageBox.critical(self, "No se pudo añadir la ficha", str(exc))
            return
        for row in range(self.drawings_table.rowCount()):
            if self.drawings_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == sheet.code:
                self.drawings_table.selectRow(row)
                break

    def _drawings_page_changed(self, index: int) -> None:
        """Guarda el formato inicial sin modificar la selección de proyectos existentes."""
        if index == 1 and "drawings" not in self.app.safety_health_state:
            self._save_drawings()

    def _drawings_format_changed(self, *_args) -> None:
        """Cambia el catálogo visible sin perder las decisiones del otro formato."""
        saved = restore_drawings(self.app.safety_health_state.get("drawings"))
        self._populate_drawings(saved)
        self._save_drawings()

    def _save_drawings(self, *_args) -> None:
        """Guarda el formato y todas las decisiones explícitas del usuario."""
        self.commit_basic_state()
        state = dict(self.app.safety_health_state)
        saved = restore_drawings(state.get("drawings"))
        selected = dict(saved["selected"]) if saved else {}
        selected.update({
            str(self.drawings_table.item(row, 0).data(Qt.ItemDataRole.UserRole)):
            self.drawings_table.item(row, 0).checkState() == Qt.CheckState.Checked
            for row in range(self.drawings_table.rowCount())
        })
        state["drawings"] = restore_drawings({
            "paper": self.drawings_format.currentText(),
            "selected": selected,
        })
        self.app.safety_health_state = state
        self.app._mark_dirty()

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
        self.related_data_error = ""
        try:
            self.related_family_links, self.related_catalog = load_related_safety_data()
        except ValueError as exc:
            self.related_family_links = {}
            self.related_catalog = {}
            self.related_data_error = str(exc)
        work_items = study_items(self.app)
        self.work_families: dict[str, str] = {}
        for work_item in work_items:
            family = work_item.code[:5].upper()
            if family in self.related_family_links:
                self.work_families.setdefault(family, work_item.summary)
        self.related_groups = related_alternative_groups_for_work_families(
            self.work_families, self.related_family_links, self.related_catalog,
        )
        self.related_items = tuple(
            item for group in self.related_groups for item in group
        )
        related_codes = {item.code for item in self.related_items}
        previous_lines = owner.lines
        self.lines = [
            line for line in previous_lines if line.code not in related_codes
        ]
        self.alternative_groups: dict[str, tuple[RelatedSafetyItem, ...]] = {}
        self.alternative_line_indices: dict[str, int] = {}
        for group in self.related_groups:
            group_codes = {item.code for item in group}
            previous = [line for line in previous_lines if line.code in group_codes]
            explicit = [line for line in previous if line.quantity is not None]
            remembered = min(
                explicit or previous,
                key=lambda line: (not line.selected, line.code),
                default=None,
            )
            chosen = next(
                (item for item in group if remembered and item.code == remembered.code),
                group[0],
            )
            line = SafetyLine(
                chosen.code, chosen.unit, chosen.summary, chosen.price, Decimal(),
                selected=True if remembered is None else remembered.selected,
                quantity=Decimal("1"),
            )
            self.lines.append(line)
            if len(group) > 1:
                group_id = group[0].code
                self.alternative_groups[group_id] = group
                self.alternative_line_indices[group_id] = len(self.lines) - 1
        owner.lines = self.lines
        self.library_editor: LibraryEditorQt | None = None
        self.setWindowTitle("Propuesta de unidades de Seguridad y Salud")
        self.resize(1120, 720)
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.percentage = QLineEdit(str(self.app.report_settings.safety_health_percentage))
        header.addWidget(QLabel("Porcentaje del PEM total:"))
        header.addWidget(self.percentage)
        header.addWidget(QLabel("%"))
        header.addStretch()
        library = QPushButton("Editar biblioteca de fichas…")
        library.clicked.connect(self.open_library_editor)
        header.addWidget(library)
        layout.addLayout(header)
        proposal_note = QLabel(
            "Las alternativas del mismo tipo (barandillas, guantes, arneses, cinturones, "
            "calzado, gafas, cascos, protecciones auditivas o respiratorias, conductos y "
            "lámparas intermitentes) se agrupan en desplegables y se "
            "preselecciona una opción; las protecciones complementarias permanecen separadas. "
            "Las unidades vinculadas se proponen una sola vez, con cantidad 1 y precio de "
            "catálogo. El capítulo se calcula para aproximarse al porcentaje indicado del "
            "PEM total (las mediciones se redondean a céntimos); las cantidades mínimas que "
            "superen ese objetivo deben ajustarse."
        )
        proposal_note.setWordWrap(True)
        layout.addWidget(proposal_note)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("Incluir", "Código / alternativa", "Partida", "Ud.", "Precio", "Cantidad")
        )
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(1, 330)
        self.alternative_rows: dict[str, int] = {}
        self.alternative_combos: dict[str, QComboBox] = {}
        group_by_line_index = {
            line_index: group_id
            for group_id, line_index in self.alternative_line_indices.items()
        }
        for line_index, line in enumerate(self.lines):
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setData(Qt.ItemDataRole.UserRole, line.code)
            check.setCheckState(
                Qt.CheckState.Checked if line.selected else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, check)
            group_id = group_by_line_index.get(line_index)
            if group_id is not None:
                combo = QComboBox()
                combo.setMinimumHeight(36)
                for option in self.alternative_groups[group_id]:
                    combo.addItem(
                        f"{option.code} — {option.summary}", option.code,
                    )
                combo.setCurrentIndex(combo.findData(line.code))
                combo.currentIndexChanged.connect(
                    lambda index, key=group_id: self._select_alternative(key, index)
                )
                self.table.setCellWidget(row, 1, combo)
                self.alternative_rows[group_id] = row
                self.alternative_combos[group_id] = combo
            else:
                self.table.setItem(row, 1, QTableWidgetItem(line.code))
            for column, value in enumerate(
                (
                    line.summary, line.unit, f"{line.price:.2f}",
                    _format_decimal(line.quantity) if line.quantity is not None
                    else "Según porcentaje",
                ),
                2,
            ):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.itemChanged.connect(self.changed)
        self.tabs = QTabWidget()
        proposal_tab = QWidget()
        proposal_layout = QVBoxLayout(proposal_tab)
        proposal_layout.setContentsMargins(0, 0, 0, 0)
        proposal_layout.addWidget(self.table)
        self.tabs.addTab(proposal_tab, "Propuesta")
        self.tabs.addTab(self._build_related_units_tab(), "Vínculos con obra")
        layout.addWidget(self.tabs, 1)
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

    def _build_related_units_tab(self) -> QWidget:
        """Crea la consulta revisable de unidades vinculadas a las partidas de obra."""
        tab = QWidget()
        related_layout = QVBoxLayout(tab)
        note = QLabel(
            "Vínculos orientativos por familia BCCA de cinco caracteres. "
            "Las variantes de un mismo tipo se agrupan en Propuesta; los tipos distintos "
            "se mantienen separados. Revisa técnicamente cada selección."
        )
        note.setWordWrap(True)
        related_layout.addWidget(note)
        family_row = QHBoxLayout()
        family_row.addWidget(QLabel("Partida de obra:"))
        self.work_family_combo = QComboBox()
        family_row.addWidget(self.work_family_combo, 1)
        related_layout.addLayout(family_row)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.related_families_table = QTableWidget(0, 3)
        self.related_families_table.setHorizontalHeaderLabels(
            ("Familia SyS", "Categoría", "Criterio")
        )
        self.related_families_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.related_families_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.related_families_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        splitter.addWidget(self.related_families_table)
        self.related_units_table = QTableWidget(0, 4)
        self.related_units_table.setHorizontalHeaderLabels(
            ("Código", "Ud.", "Unidad de Seguridad y Salud", "Precio (€)")
        )
        self.related_units_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.related_units_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.related_units_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        splitter.addWidget(self.related_units_table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        related_layout.addWidget(splitter, 1)
        self.related_status = QLabel()
        self.related_status.setWordWrap(True)
        related_layout.addWidget(self.related_status)
        self.related_families_table.itemSelectionChanged.connect(
            self._show_related_units
        )
        self.work_family_combo.currentIndexChanged.connect(
            self._show_related_families
        )
        if self.related_data_error:
            self.related_status.setText(self.related_data_error)
            self.work_family_combo.setEnabled(False)
            self.related_families_table.setEnabled(False)
            self.related_units_table.setEnabled(False)
            return tab

        for family, summary in sorted(self.work_families.items()):
            self.work_family_combo.addItem(f"{family} — {summary}", family)
        if self.work_family_combo.count():
            self._show_related_families()
        else:
            self.related_status.setText(
                "No hay partidas seleccionadas con vínculos disponibles."
            )
        return tab

    def _select_alternative(self, group_id: str, index: int) -> None:
        """Actualiza la partida de propuesta al cambiar una alternativa del desplegable."""
        combo = self.alternative_combos[group_id]
        code = combo.itemData(index)
        option = next(
            item for item in self.alternative_groups[group_id] if item.code == code
        )
        line = self.lines[self.alternative_line_indices[group_id]]
        line.code = option.code
        line.unit = option.unit
        line.summary = option.summary
        line.price = option.price
        line.weight = Decimal()
        line.quantity = Decimal("1")
        row = self.alternative_rows[group_id]
        check = self.table.item(row, 0)
        line.selected = check.checkState() == Qt.CheckState.Checked
        check.setData(Qt.ItemDataRole.UserRole, line.code)
        for column, value in (
            (2, line.summary),
            (3, line.unit),
            (4, f"{line.price:.2f}"),
            (5, _format_decimal(line.quantity)),
        ):
            self.table.item(row, column).setText(value)

    def _show_related_families(self) -> None:
        """Muestra las familias de Seguridad y Salud asociadas a la partida elegida."""
        self.related_families_table.setRowCount(0)
        self.related_units_table.setRowCount(0)
        family = self.work_family_combo.currentData()
        links = self.related_family_links.get(family, ())
        for link in links:
            row = self.related_families_table.rowCount()
            self.related_families_table.insertRow(row)
            code = QTableWidgetItem(link.code)
            code.setData(Qt.ItemDataRole.UserRole, link.code)
            self.related_families_table.setItem(row, 0, code)
            self.related_families_table.setItem(row, 1, QTableWidgetItem(link.category))
            self.related_families_table.setItem(row, 2, QTableWidgetItem(link.criterion))
        if links:
            self.related_families_table.selectRow(0)
            self.related_status.setText(
                f"{len(links)} familias vinculadas. Selecciona una para ver sus unidades."
            )
        else:
            self.related_status.setText("La partida seleccionada no tiene vínculos.")

    def _show_related_units(self) -> None:
        """Muestra las unidades del catálogo para la familia de Seguridad seleccionada."""
        self.related_units_table.setRowCount(0)
        row = self.related_families_table.currentRow()
        family_item = self.related_families_table.item(row, 0) if row >= 0 else None
        if family_item is None:
            return
        family = str(family_item.data(Qt.ItemDataRole.UserRole))
        for unit in self.related_catalog.get(family, ()):
            row = self.related_units_table.rowCount()
            self.related_units_table.insertRow(row)
            values = (
                unit.code, unit.unit, unit.summary, f"{unit.price:.2f}",
            )
            for column, value in enumerate(values):
                self.related_units_table.setItem(row, column, QTableWidgetItem(value))

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
                document_section="DOC01",
                annex_tool="safety",
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
