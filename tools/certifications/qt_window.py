"""Plan económico y certificaciones mensuales con PySide6."""
from __future__ import annotations

import math
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from budget_core import format_chapter_code, format_currency, parse_float
from budget_reports import CERTIFICATION_REPORT_NAMES, DEVIATION_REPORT_NAME, write_certification_pdf
from odt_reports import write_certification_odt
from qt_localization import get_document_save_path
from tools._shared.reference_pill import add_reference_pill
from tools._shared.scheduling import PlanParams, apply_saved_plan_state, build_activities
from . import economic_months, planned_monthly_amounts, reconcile_certification


class CertificationWindowQt(QDialog):
    """Edita plan, certificaciones, desviaciones y justificaciones."""

    def __init__(self, app) -> None:
        """Carga planificación, importes guardados y horizonte mensual."""
        super().__init__(app)
        self.app = app
        self.items = app.budget_items
        self.chapter_title = app._chapter_title
        self.project_title = app._project_title()
        self.params = PlanParams()
        self.activities = build_activities(self.items, self.chapter_title)
        try:
            apply_saved_plan_state(self.params, self.activities, app.planning_state)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                "Planificación guardada no válida",
                f"No se ha podido aplicar la planificación guardada:\n{exc}",
            )
        self.months = economic_months(self.params.start)
        self.plan = planned_monthly_amounts(
            self.activities, self.params, self.months
        )
        self.selected_plan_month: int | None = None
        self.selected_certification_month: int | None = None
        self.selected_cell: tuple[int, int] | None = None
        self.dirty = False
        self._load_saved_plan()
        self.setWindowTitle(f"{app._project_title()} — Certificaciones")
        self.resize(1500, 780)
        layout = QVBoxLayout(self)
        add_reference_pill(layout, "certifications")
        toolbar = QHBoxLayout()
        self.copy_button = QPushButton("Copiar plan seleccionado a su certificación")
        self.copy_button.clicked.connect(self.copy_selected_plan)
        toolbar.addWidget(self.copy_button)
        deviation = QPushButton("Informe de desviaciones")
        deviation.clicked.connect(
            lambda: self.generate_template_report(DEVIATION_REPORT_NAME)
        )
        toolbar.addWidget(deviation)
        self.report_selector = QComboBox()
        self.report_selector.addItems(CERTIFICATION_REPORT_NAMES)
        toolbar.addWidget(self.report_selector)
        report = QPushButton("Generar informe")
        report.clicked.connect(
            lambda: self.generate_template_report(
                self.report_selector.currentText()
            )
        )
        toolbar.addWidget(report)
        self.selection_label = QLabel(
            "Selecciona una cabecera Plan para copiar esa columna."
        )
        toolbar.addWidget(self.selection_label, 1)
        self.save_button = QPushButton("Guardar")
        self.save_button.clicked.connect(self.save_changes)
        toolbar.addWidget(self.save_button)
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 4 + 2 * len(self.months))
        headers = ["Capítulo", "Código", "Actividad", "Importe"]
        for index, month in enumerate(self.months):
            headers.extend((f"Plan {month.label}", f"Certificación {index + 1}"))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().sectionClicked.connect(
            self._header_selected
        )
        self.table.cellDoubleClicked.connect(self._cell_double_clicked)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        detail = QVBoxLayout()
        self.deviation_label = QLabel(
            "Selecciona una celda de certificación."
        )
        detail.addWidget(self.deviation_label)
        detail.addWidget(QLabel("Justificación"))
        self.justification = QPlainTextEdit()
        self.justification.setMaximumHeight(90)
        self.justification.textChanged.connect(self.save_justification)
        detail.addWidget(self.justification)
        layout.addLayout(detail)
        self.refresh_table()

    def _format_currency(self, value: float) -> str:
        """Formatea con la divisa vigente del proyecto."""
        settings = self.app.report_settings
        return format_currency(
            value, settings.currency, settings.custom_currency
        )

    def _load_saved_plan(self) -> None:
        """Restaura previsiones editadas por partida y mes."""
        for row, item in enumerate(self.items):
            for index, month in enumerate(self.months):
                if month.key in item.planned_amounts:
                    self.plan[row][index] = item.planned_amounts[month.key]

    def refresh_table(self) -> None:
        """Reconstruye filas mensuales y sus desviaciones."""
        self.table.setRowCount(len(self.items))
        for row, (item, activity) in enumerate(zip(self.items, self.activities)):
            values = [
                format_chapter_code(item.chapter),
                item.code,
                item.summary,
                self._format_currency(activity.amount),
            ]
            for index, month in enumerate(self.months):
                planned = self.plan[row][index]
                certified = item.certifications.get(month.key)
                values.append(self._format_currency(planned) if planned else "")
                if certified is None:
                    values.append("")
                else:
                    planned_original = item.certification_plans.get(
                        month.key, planned
                    )
                    difference = certified - planned_original
                    sign = "+" if difference > 0.005 else ""
                    values.append(
                        f"{self._format_currency(certified)}\n"
                        f"{sign}{self._format_currency(difference)}"
                    )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                if column >= 4 and (column - 4) % 2 == 1:
                    cell.setForeground(
                        Qt.GlobalColor.darkRed
                        if "\n+" in value
                        else Qt.GlobalColor.darkGreen
                    )
                self.table.setItem(row, column, cell)
        self.table.resizeColumnsToContents()
        for column in range(4, self.table.columnCount()):
            self.table.setColumnWidth(column, 120)

    def _header_selected(self, column: int) -> None:
        """Selecciona plan o certificación desde una cabecera mensual."""
        if column < 4:
            return
        month_index = (column - 4) // 2
        if (column - 4) % 2 == 0:
            self.selected_plan_month = month_index
            self.selected_certification_month = None
            self.selection_label.setText(
                f"Plan {self.months[month_index].label} seleccionado."
            )
        else:
            self.selected_plan_month = None
            self.selected_certification_month = month_index
            self.selection_label.setText(
                f"Certificación {month_index + 1} "
                f"({self.months[month_index].label}) seleccionada."
            )
        self.table.selectColumn(column)

    def _selection_changed(self) -> None:
        """Muestra detalle al entrar en una celda de certificación."""
        item = self.table.currentItem()
        if item is None or item.column() < 4:
            return
        if (item.column() - 4) % 2 == 1:
            self.select_certification(
                item.row(), (item.column() - 4) // 2
            )

    def select_certification(self, row: int, month_index: int) -> None:
        """Actualiza detalle y justificación de la celda activa."""
        self.selected_cell = row, month_index
        item = self.items[row]
        month = self.months[month_index]
        planned = item.certification_plans.get(
            month.key, self.plan[row][month_index]
        )
        certified = item.certifications.get(month.key)
        difference = (certified if certified is not None else 0.0) - planned
        status = "Pendiente" if certified is None else (
            "Sin desviación"
            if math.isclose(difference, 0.0, abs_tol=0.005)
            else f"Desviación: {self._format_currency(difference)}"
        )
        self.deviation_label.setText(
            f"{item.code} · {month.label} · Plan: "
            f"{self._format_currency(planned)} · Certificado: "
            f"{self._format_currency(certified) if certified is not None else '-'}"
            f" · {status}"
        )
        self.justification.blockSignals(True)
        self.justification.setPlainText(
            item.certification_justifications.get(month.key, "")
        )
        self.justification.blockSignals(False)

    def _cell_double_clicked(self, row: int, column: int) -> None:
        """Edita únicamente columnas Certificación."""
        if column < 4 or (column - 4) % 2 == 0:
            return
        month_index = (column - 4) // 2
        item = self.items[row]
        current = item.certifications.get(
            self.months[month_index].key,
            self.plan[row][month_index],
        )
        value, accepted = QInputDialog.getText(
            self,
            "Certificación",
            "Importe certificado:",
            text=f"{current:.2f}",
        )
        if accepted:
            self.commit_certification(row, month_index, value)

    def commit_certification(
        self, row: int, month_index: int, raw: str
    ) -> None:
        """Valida, concilia y guarda un importe mensual."""
        certified = parse_float(raw, float("nan"))
        if not math.isfinite(certified) or certified < 0:
            QMessageBox.critical(
                self,
                "Certificación",
                "Introduce un importe numérico igual o mayor que cero.",
            )
            return
        item = self.items[row]
        month = self.months[month_index]
        item.certification_plans.setdefault(
            month.key, self.plan[row][month_index]
        )
        remainder = reconcile_certification(
            self.plan[row], month_index, certified
        )
        item.certifications[month.key] = certified
        item.planned_amounts = {
            entry.key: self.plan[row][index]
            for index, entry in enumerate(self.months)
        }
        self._set_dirty(True)
        self.refresh_table()
        self.select_certification(row, month_index)
        if remainder:
            self.deviation_label.setText(
                self.deviation_label.text()
                + f" · Falta por compensar: {self._format_currency(remainder)}"
            )

    def copy_selected_plan(self) -> None:
        """Copia la previsión seleccionada a su certificación."""
        if self.selected_plan_month is None:
            QMessageBox.warning(
                self, "Certificaciones", "Selecciona una cabecera Plan."
            )
            return
        index = self.selected_plan_month
        month = self.months[index]
        for row, item in enumerate(self.items):
            item.certifications[month.key] = self.plan[row][index]
            item.certification_plans[month.key] = self.plan[row][index]
        self.refresh_table()
        self._set_dirty(True)

    def save_justification(self) -> None:
        """Guarda el texto de la celda seleccionada."""
        if self.selected_cell is None:
            return
        row, index = self.selected_cell
        value = self.justification.toPlainText().strip()
        key = self.months[index].key
        if self.items[row].certification_justifications.get(key, "") != value:
            self.items[row].certification_justifications[key] = value
            self._set_dirty(True)

    def _set_dirty(self, value: bool) -> None:
        """Marca los cambios en la ventana y el proyecto."""
        self.dirty = value
        self.save_button.setProperty("pending", value)
        self.save_button.setStyleSheet(
            "font-weight: bold; background: #202124; color: white;"
            if value else ""
        )
        if value:
            self.app._mark_dirty()

    def save_changes(self) -> None:
        """Guarda plan y certificaciones mediante el proyecto principal."""
        self.save_justification()
        if self.app.save_project():
            self._set_dirty(False)

    def generate_template_report(self, report: str) -> None:
        """Genera resumen, detalle o desviaciones del mes seleccionado."""
        if self.selected_certification_month is None:
            QMessageBox.warning(
                self,
                "Informe de certificación",
                "Selecciona primero una cabecera Certificación.",
            )
            return
        index = self.selected_certification_month
        month = self.months[index]
        if any(
            not math.isfinite(item.certifications.get(month.key, 0.0))
            for item in self.items
        ):
            QMessageBox.critical(
                self, "Informe de certificación", "Hay importes no válidos."
            )
            return
        if report != DEVIATION_REPORT_NAME and not any(
            abs(item.certifications.get(month.key, 0.0)) >= 0.005
            for item in self.items
        ):
            QMessageBox.information(
                self,
                "Informe de certificación",
                f"No hay importes certificados en Certificación {index + 1}.",
            )
            return
        settings = self.app.report_settings
        selection = get_document_save_path(
            self,
            f"Generar {report}",
            f"{report} {index + 1} {month.key}.pdf",
            document_section="DOC04",
        )
        if selection is None:
            return
        target_path, output_format = selection
        extension = f".{output_format}"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=extension, dir=target_path.parent, delete=False
            ) as output:
                temporary = Path(output.name)
            writer = (
                write_certification_odt
                if extension == ".odt"
                else write_certification_pdf
            )
            writer(
                self.items,
                self.chapter_title,
                self.project_title,
                settings,
                report,
                temporary,
                month.key,
                index + 1,
                month.label,
                planned_amounts=(
                    [row[index] for row in self.plan]
                    if report == DEVIATION_REPORT_NAME
                    else None
                ),
            )
            temporary.replace(target_path)
            temporary = None
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "No se pudo generar el informe", str(exc)
            )
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.selection_label.setText(f"Informe guardado: {target_path}")
        try:
            os.startfile(str(target_path))  # type: ignore[attr-defined]
        except OSError:
            pass
