"""Ventana PySide6 de justificación de precios."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from budget_core import fmt_number
from qt_localization import get_document_save_path
from .document import (
    ANNUAL_HOURS_REFERENCES,
    CALENDAR_REFERENCES,
    CONSTRUCTION_AGREEMENT_REFERENCES,
    CONTRIBUTION_BASE_REFERENCES,
    CONTRIBUTION_DETAIL_REFERENCES,
    CONTRIBUTION_RATE_REFERENCES,
    FIELDS,
    PENSION_CONTRIBUTION_REFERENCES,
    SALARY_REVISION_REFERENCES,
    TITLE,
    WAGE_REFERENCES,
    WORK_CONDITION_REFERENCES,
    restore_state,
    validate_state,
    write_document,
)
from .templates import DOCUMENTS, LIBRARY_PATH, general_variables, load_documents
from .indirect import (
    FIELDS_BY_GROUP, GROUP_HEADERS, GROUPS, HEADERS, IndirectCosts, calculate, default_rows, field_maximum,
    new_row, normalize_rows, rate_label, rounded_rate, row_cost,
)
from tools._library_editor_qt import LibraryEditorQt
from tools._shared.reference_pill import add_reference_pill
from collaborative_storage import shared_library_path


def indirect_summary(costs: IndirectCosts) -> str:
    """Presenta la base, el tope de K₁ y cualquier discrepancia con el presupuesto."""
    if costs.rate is None:
        return "K no calculable: el coste directo total del presupuesto no es positivo."
    limit = f" (limitado desde {rate_label(costs.raw)})" if costs.raw > costs.adopted else ""
    summary = f"K₁: {rate_label(costs.adopted)}{limit} · K₂: 1 % · K: {rate_label(costs.rate)}"
    if costs.differs:
        summary += f"\nAviso: el presupuesto tiene aplicado {rate_label(costs.budget_rate)}."
    return summary


class IndirectCostsDialog(QDialog):
    """Edita una estimación agrupada con totales y coeficientes de solo lectura."""

    def __init__(self, parent, app, rows: list[dict]) -> None:
        """Crea la tabla editable y permite aceptar o cancelar sus cambios."""
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("Costes indirectos")
        self.resize(880, 480)
        layout = QVBoxLayout(self)
        hint = QLabel("Revisa las tarifas y dedicaciones. Los importes iniciales son cero; no son tarifas de referencia.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setObjectName("priceIndirectTable")
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalHeaderLabels(HEADERS)
        vertical_header = self.table.verticalHeader()
        vertical_header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vertical_header.setDefaultSectionSize(46)
        vertical_header.hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        tools = QHBoxLayout()
        self.group = QComboBox()
        for key, (label, _) in GROUPS.items():
            self.group.addItem(label, key)
        tools.addWidget(self.group)
        add = QPushButton("Añadir fila")
        add.clicked.connect(self.add_row)
        tools.addWidget(add)
        remove = QPushButton("Quitar fila seleccionada")
        remove.clicked.connect(self.remove_row)
        tools.addWidget(remove)
        layout.addLayout(tools)
        self.summary = QLabel()
        self.summary.setObjectName("priceIndirectSummary")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load_rows(rows)

    def load_rows(self, rows: list[dict]) -> None:
        """Reconstruye la tabla en su orden de presentación."""
        self.table.clearSpans()
        self.table.setRowCount(0)
        for group, (label, options) in GROUPS.items():
            index = self.table.rowCount()
            self.table.insertRow(index)
            self.table.setSpan(index, 0, 1, len(HEADERS))
            heading = QTableWidgetItem()
            heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(index, 0, heading)
            band = QLabel(label)
            band.setObjectName("priceIndirectGroup")
            self.table.setCellWidget(index, 0, band)
            self.table.setRowHeight(index, max(42, band.sizeHint().height() + 12))
            if group != "staff":
                index = self.table.rowCount()
                self.table.insertRow(index)
                for column, title in enumerate(GROUP_HEADERS[group]):
                    heading = QTableWidgetItem(title)
                    heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    font = heading.font()
                    font.setBold(True)
                    heading.setFont(font)
                    self.table.setItem(index, column, heading)
            for row in rows:
                if row["group"] != group:
                    continue
                index = self.table.rowCount()
                self.table.insertRow(index)
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(index, 5, item)
                for column in (0, 2, 3, 4):
                    if column not in FIELDS_BY_GROUP[group]:
                        blank = QTableWidgetItem()
                        blank.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                        self.table.setItem(index, column, blank)
                role = QComboBox()
                role.setEditable(True)
                role.addItems(options)
                role.setCurrentText(row["role"])
                role.setProperty("costGroup", group)
                self.table.setCellWidget(index, 1, role)
                height = role.sizeHint().height()
                for column, key in FIELDS_BY_GROUP[group].items():
                    field = QDoubleSpinBox()
                    field.setRange(0, field_maximum(key))
                    field.setDecimals(2)
                    field.setValue(row[key])
                    self.table.setCellWidget(index, column, field)
                    height = max(height, field.sizeHint().height())
                    field.valueChanged.connect(self.update_totals)
                self.table.setRowHeight(index, max(46, height + 12))
        self.update_totals()

    def rows(self) -> list[dict]:
        """Devuelve los valores editables, sin las filas separadoras ni los totales."""
        rows = []
        for index in range(self.table.rowCount()):
            role = self.table.cellWidget(index, 1)
            if not isinstance(role, QComboBox):
                continue
            row = {"group": role.property("costGroup"), "role": role.currentText()}
            for column, key in FIELDS_BY_GROUP[row["group"]].items():
                field = self.table.cellWidget(index, column)
                if isinstance(field, QDoubleSpinBox):
                    row[key] = field.value()
            rows.append(row)
        return rows

    def add_row(self) -> None:
        """Añade una fila en el grupo seleccionado con una tarifa pendiente."""
        group = self.group.currentData()
        self.load_rows(self.rows() + [new_row(group)])

    def remove_row(self) -> None:
        """Retira únicamente una fila editable seleccionada."""
        index = self.table.currentRow()
        if index >= 0 and self.table.cellWidget(index, 1) is not None:
            self.table.removeRow(index)
            self.update_totals()

    def update_totals(self) -> None:
        """Actualiza el coste de cada fila y el resumen del anejo."""
        rows = self.rows()
        items = (self.table.item(index, 5) for index in range(self.table.rowCount())
                 if self.table.cellWidget(index, 1) is not None)
        for item, row in zip(items, rows, strict=True):
            item.setText(fmt_number(float(row_cost(row))))
        total = sum(row_cost(row) for row in rows)
        self.summary.setText(f"Total indirectos propios: {fmt_number(float(total))} €\n"
                             f"{indirect_summary(calculate(self.app, rows))}")

    def accept(self) -> None:
        """Impide aceptar conceptos vacíos u otros valores no válidos."""
        try:
            normalize_rows(self.rows())
        except ValueError as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        super().accept()


class PriceJustificationWindowQt(QDialog):
    """Edita los datos específicos y genera el anejo compatible con Tk."""

    def __init__(self, app) -> None:
        """Construye el formulario con referencias editables y valores vigentes."""
        super().__init__(app)
        self.app = app
        state = FIELDS | restore_state(app.price_justification_state)
        if not state["reference_date"]:
            state["reference_date"] = date.today().isoformat()
        self._state = state
        self.library_editor: LibraryEditorQt | None = None
        self._loading = True
        self.variables: dict[str, QLineEdit | QComboBox] = {}
        self.setWindowTitle(TITLE)
        available = self.screen().availableGeometry()
        self.resize(min(1120, available.width() - 24), min(760, available.height() - 64))
        layout = QVBoxLayout(self)
        add_reference_pill(layout, "price_justification")
        title = QLabel(TITLE)
        title.setObjectName("priceJustificationTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)
        instructions = QLabel(
            "Completa los datos específicos. Los textos base se editan desde "
            "la biblioteca de documentos."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.general_info = QLabel()
        self.general_info.setObjectName("priceJustificationProject")
        self.general_info.setWordWrap(True)
        layout.addWidget(self.general_info)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._entry(form, "reference_date", "Fecha de referencia (AAAA-MM-DD)")
        self._choice(form, "contract_type", "Régimen de contratación", {
            "public": "Contratación pública",
            "private": "Contratación privada",
        })
        self._entry(form, "price_source", "Fuente de precios y fecha / edición")
        for key, label, references in (
            ("agreement", "Convenio colectivo y revisión salarial", CONSTRUCTION_AGREEMENT_REFERENCES),
            ("salary_revision", "Revisión salarial (art. 55)", SALARY_REVISION_REFERENCES),
            ("wage_reference", "Referencia salarial / SMI aplicable", WAGE_REFERENCES),
            ("contribution_bases", "Bases de cotización (referencia 2026)", CONTRIBUTION_BASE_REFERENCES),
            ("contributions", "Tipos de cotización (referencia 2026)", CONTRIBUTION_RATE_REFERENCES),
            ("contribution_details", "Conceptos cotizables y exentos (referencia 2026)", CONTRIBUTION_DETAIL_REFERENCES),
            ("pension_contribution", "Aportación empresarial al plan de pensiones (art. 56)", PENSION_CONTRIBUTION_REFERENCES),
            ("calendar", "Calendario laboral y días de ajuste (referencia 2026)", CALENDAR_REFERENCES),
            ("work_conditions", "Vacaciones, horas extra y condiciones climáticas", WORK_CONDITION_REFERENCES),
            ("annual_hours", "Jornada anual efectiva (horas)", ANNUAL_HOURS_REFERENCES),
        ):
            self._editable_choice(form, key, label, references)
        self._choice(form, "machinery_method", "Criterio de maquinaria", {
            "budget": "Precios de la fuente presupuestaria",
            "analytical": "Valoración analítica documentada",
        })
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        costs = QPushButton("Editar costes indirectos…")
        costs.clicked.connect(self.edit_indirect_costs)
        layout.addWidget(costs)
        self.apply_k_button = QPushButton("Aplicar K al presupuesto")
        self.apply_k_button.setToolTip("Guarda el K calculado en Configuración y actualiza los costes indirectos de las partidas.")
        self.apply_k_button.clicked.connect(self.apply_indirect_costs)
        layout.addWidget(self.apply_k_button)
        self.costs_summary = QLabel()
        self.costs_summary.setObjectName("priceIndirectSummary")
        self.costs_summary.setWordWrap(True)
        layout.addWidget(self.costs_summary)
        common_info = QLabel("Datos comunes: Archivo → Configuración…")
        common_info.setWordWrap(True)
        layout.addWidget(common_info)
        library = QPushButton("Editar biblioteca de documentos…")
        library.clicked.connect(self.open_library_editor)
        layout.addWidget(library)
        bottom = QHBoxLayout()
        export = QPushButton("Guardar anejo…")
        export.clicked.connect(self.export)
        bottom.addWidget(export)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self._loading = False
        self.app.budget_refreshed.connect(self.refresh_general_info)
        self.refresh_general_info()

    def _entry(self, form: QFormLayout, key: str, label: str) -> None:
        """Añade un campo de texto sencillo."""
        field = QLineEdit(str(self._state[key]))
        field.textChanged.connect(self.changed)
        self.variables[key] = field
        form.addRow(label, field)

    def _choice(
        self, form: QFormLayout, key: str, label: str, choices: dict[str, str]
    ) -> None:
        """Añade una lista con códigos persistentes."""
        field = QComboBox()
        for code, text in choices.items():
            field.addItem(text, code)
        field.setCurrentIndex(field.findData(self._state[key]))
        field.currentIndexChanged.connect(self.changed)
        self.variables[key] = field
        form.addRow(label, field)

    def _editable_choice(
        self, form: QFormLayout, key: str, label: str, references: tuple[str, ...]
    ) -> None:
        """Añade referencias seleccionables que admiten texto local."""
        field = QComboBox()
        field.setEditable(True)
        field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        field.setMinimumContentsLength(20)
        field.addItems(references)
        field.setEditText(str(self._state[key]))
        field.editTextChanged.connect(self.changed)
        self.variables[key] = field
        form.addRow(label, field)

    def collect(self) -> dict:
        """Recoge datos normalizados sin perder campos específicos no visibles."""
        state = dict(self._state)
        for key, field in self.variables.items():
            if isinstance(field, QComboBox):
                state[key] = (
                    str(field.currentData())
                    if key in {"contract_type", "machinery_method"}
                    else field.currentText()
                )
            else:
                state[key] = field.text()
        return state

    def changed(self, *_args) -> None:
        """Actualiza estado e historial al editar sin recalcular el presupuesto."""
        if self._loading:
            return
        self.app.price_justification_state = self.collect()
        self.app._mark_dirty()

    def refresh_general_info(self) -> None:
        """Muestra los datos generales vigentes usados por el documento."""
        general = general_variables(self.app)
        self.general_info.setText(
            f"Proyecto: {general['titulo_proyecto']}\n"
            f"Emplazamiento: {general['emplazamiento'] or 'Pendiente en Configuración general'}\n"
            f"Provincia: {general['provincias'] or 'Pendiente'} · "
            f"Comunidad: {general['comunidades_autonomas'] or 'Pendiente'}"
        )
        costs = calculate(self.app, self._state.get("indirect_rows", default_rows()))
        self.costs_summary.setText(indirect_summary(costs))
        self.apply_k_button.setEnabled(costs.rate is not None and not self._rate_applied(costs.rate))

    def _rate_applied(self, rate: Decimal) -> bool:
        """Comprueba el K guardado y su aplicación a todas las partidas actuales."""
        value = float(rounded_rate(rate))
        return self.app.report_settings.indirect_costs == value and all(
            item.indirect_costs == value and (value == 0 or item.direct_unit_price is not None)
            for item in self.app.budget_items
        )

    def apply_indirect_costs(self) -> None:
        """Guarda el K calculado por la misma vía que Archivo → Configuración."""
        try:
            state = validate_state(self.collect())
            costs = calculate(self.app, state.get("indirect_rows", default_rows()))
            rate = costs.rate
            if rate is None:
                raise ValueError("No se puede aplicar K: el coste directo total del presupuesto no es positivo.")
            if self._rate_applied(rate):
                return
        except ValueError as exc:
            QMessageBox.warning(self, TITLE, str(exc))
            self.refresh_general_info()
            return
        data = self.app.report_settings.to_dict()
        data.update(project_title=self.app._project_title(), indirect_costs=float(rounded_rate(rate)))
        self.app.price_justification_state = state
        saved = self.app._save_report_settings(data)
        self.refresh_general_info()
        if saved:
            self.app._set_status(f"Costes indirectos actualizados: K = {rate_label(rate)}.")

    def edit_indirect_costs(self) -> None:
        """Guarda solo las filas aceptadas, sin aplicar K al presupuesto."""
        dialog = IndirectCostsDialog(self, self.app, self._state.get("indirect_rows", default_rows()))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._state["indirect_rows"] = normalize_rows(dialog.rows())
            self.changed()
            self.refresh_general_info()

    def open_library_editor(self) -> None:
        """Abre una única ventana de edición de la biblioteca ZIP."""
        if self.library_editor is not None and self.library_editor.isVisible():
            self.library_editor.raise_()
            return
        self.library_editor = LibraryEditorQt(
            self,
            shared_library_path(
                self.app, "price_justification", LIBRARY_PATH
            ),
            "Biblioteca de Justificación de precios",
            used_members={name for name, _ in DOCUMENTS},
            on_saved=self.reload_library,
            track_changes=True,
        )
        self.library_editor.show()

    def reload_library(self) -> None:
        """Comprueba que la biblioteca modificada contiene los ocho documentos."""
        try:
            load_documents(shared_library_path(
                self.app, "price_justification", LIBRARY_PATH
            ))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, TITLE, str(exc))

    def validated(self) -> dict:
        """Valida el estado y lo copia al proyecto."""
        state = validate_state(self.collect())
        self.app.price_justification_state = state
        self.app._mark_dirty()
        return state

    def export(self) -> None:
        """Genera PDF u ODT con la configuración y textos vigentes."""
        try:
            if self.library_editor is not None and self.library_editor.dirty:
                raise ValueError(
                    "Guarda o descarta los cambios de la biblioteca antes de generar el anejo."
                )
            state = self.validated()
            selection = get_document_save_path(
                self,
                "Guardar justificación de precios",
                "Justificación de precios.pdf",
                document_section="DOC01",
                annex_tool="price",
            )
            if selection is None:
                return
            target, _output_format = selection
            write_document(
                self.app,
                target,
                state,
            )
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.critical(
                self, TITLE, f"No se pudo generar el anejo:\n{exc}"
            )
            return
        QMessageBox.information(self, TITLE, "Justificación de precios guardada.")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Valida los campos y protege la biblioteca antes de cerrar."""
        if self.library_editor is not None and self.library_editor.isVisible():
            self.library_editor.close()
            if self.library_editor.isVisible():
                event.ignore()
                return
        try:
            self.validated()
        except ValueError as exc:
            QMessageBox.critical(self, TITLE, str(exc))
            event.ignore()
            return
        event.accept()
