"""Planificación de actividades y Gantt con PySide6."""
from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import shutil
import tempfile
import webbrowser

from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from budget_core import format_chapter_code, fmt_number
from qt_localization import get_document_save_path
from tools._shared.reference_pill import add_reference_pill
from tools._shared.scheduling import (
    RELATIONS,
    PlanParams,
    PlanPhase,
    activity_segments,
    apply_saved_plan_state,
    build_activities,
    chain_sequence,
    parse_date,
    schedule,
    validate_phase_shares,
)
from .reports import write_planning_report


class PlanningWindowQt(QDialog):
    """Configura calendario, relaciones, fechas y diagramas de obra."""

    EDITABLE_COLUMNS = {
        6: "teams",
        8: "manual",
        9: "continuous",
        10: "pred",
        11: "relation",
        12: "lag",
        15: "phases",
    }

    def __init__(self, app) -> None:
        """Reconstruye actividades y restaura el estado del proyecto."""
        super().__init__(app)
        self.app = app
        self.project_title = app._project_title()
        self.params = PlanParams()
        self.activities = build_activities(app.budget_items, app._chapter_title)
        if app.planning_state:
            apply_saved_plan_state(
                self.params, self.activities, app.planning_state
            )
        self.dirty = False
        self._ready = False
        self.setWindowTitle(f"{self.project_title} — Planificación")
        self.resize(1320, 800)
        layout = QVBoxLayout(self)
        add_reference_pill(layout, "planning")
        top = QHBoxLayout()
        top.addStretch()
        self.save_button = QPushButton("Guardar")
        self.save_button.clicked.connect(self.save_changes)
        top.addWidget(self.save_button)
        layout.addLayout(top)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._params_page(), "1. Parámetros")
        self.tabs.addTab(self._plan_page(), "2. Secuencia y plan de trabajo")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.recalculate()
        self._ready = True

    def _params_page(self) -> QWidget:
        """Crea fecha, jornada, escala, detalle y calendario festivo."""
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.start = QLineEdit(f"{self.params.start:%d/%m/%Y}")
        self.hours = QLineEdit(str(int(self.params.hours_per_day)))
        self.scale = QComboBox()
        self.scale.addItems(("1", "7"))
        self.scale.setCurrentText(str(self.params.scale))
        self.detail = QComboBox()
        self.detail.addItems(("Resumen", "Completo"))
        self.detail.setCurrentText(self.params.plan)
        form.addRow("Fecha de inicio de obra (dd/mm/aaaa)", self.start)
        form.addRow("Horas por jornada", self.hours)
        form.addRow("Escala del Gantt (1 = día, 7 = semana)", self.scale)
        form.addRow("Detalle de la figura", self.detail)
        layout.addLayout(form)
        phases = QPushButton("Configurar fases de obra…")
        phases.clicked.connect(self.edit_phases)
        layout.addWidget(phases)
        heading = QLabel("Festivos (no laborables)")
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        self.holidays = QTableWidget(0, 2)
        self.holidays.setHorizontalHeaderLabels(("Fecha", "Descripción"))
        self.holidays.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.holidays.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._refresh_holidays()
        layout.addWidget(self.holidays, 1)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("Fecha:"))
        self.new_holiday_date = QLineEdit()
        self.new_holiday_date.setMaximumWidth(130)
        actions.addWidget(self.new_holiday_date)
        actions.addWidget(QLabel("Descripción:"))
        self.new_holiday_description = QLineEdit()
        actions.addWidget(self.new_holiday_description, 1)
        add = QPushButton("Añadir festivo")
        add.clicked.connect(self.add_holiday)
        actions.addWidget(add)
        remove = QPushButton("Eliminar festivo")
        remove.clicked.connect(self.delete_holiday)
        actions.addWidget(remove)
        layout.addLayout(actions)
        apply = QPushButton("Aplicar parámetros y continuar →")
        apply.clicked.connect(self.apply_params)
        layout.addWidget(apply)
        return page

    def _plan_page(self) -> QWidget:
        """Crea la tabla de actividades, relaciones y acciones de salida."""
        page = QWidget()
        layout = QVBoxLayout(page)
        actions = QHBoxLayout()
        for text, slot in (
            ("Secuenciar una tras otra", self.sequence_chain),
            ("Recalcular fechas", self.recalculate),
            ("Guardar planificación en plantilla", self.save_plan_report),
            ("Ver detalle completo", self.draw_plan),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(QLabel(
            "Doble clic en equipos, duración, continuidad, predecesora, "
            "tipo, desfase o reparto por fases para editarlos."
        ))
        self.table = QTableWidget(0, 16)
        self.table.setHorizontalHeaderLabels((
            "ID", "Capítulo", "Código", "Resumen", "Medición",
            "Rend. (h/ud)", "Equipos", "Duración", "Dur. manual",
            "Toda la obra", "Pred.", "Tipo", "Desfase", "Inicio", "Fin", "Reparto por fases",
        ))
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self.table.cellDoubleClicked.connect(self.edit_cell)
        layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.summary)
        return page

    def to_state(self) -> dict:
        """Serializa parámetros y relaciones con el formato compartido."""
        return {
            "params": {
                "start": self.params.start.isoformat(),
                "hours_per_day": self.params.hours_per_day,
                "scale": self.params.scale,
                "plan": self.params.plan,
                "holidays": [
                    {"date": day.isoformat(), "description": description}
                    for day, description in self.params.holidays
                ],
                "phases": [{"name": phase.name, "lag": phase.lag} for phase in self.params.phases],
            },
            "activities": [
                {
                    "chapter": activity.chapter,
                    "code": activity.code,
                    "teams": activity.teams,
                    "manual_duration": activity.manual_duration,
                    "continuous": activity.continuous,
                    "pred": activity.pred,
                    "relation": activity.relation,
                    "lag": activity.lag,
                    "phase_shares": {str(index): percent for index, percent in activity.phase_shares.items()},
                }
                for activity in self.activities
            ],
        }

    def _notify_state_change(self) -> None:
        """Publica la planificación vigente en el proyecto."""
        self.app.planning_state = self.to_state()

    def _set_dirty(self, value: bool) -> None:
        """Actualiza los indicadores de ventana y proyecto."""
        self.dirty = value
        self.save_button.setStyleSheet(
            "font-weight: bold; background: #202124; color: white;"
            if value else ""
        )
        if value:
            self.app._mark_dirty()

    def _refresh_holidays(self) -> None:
        """Ordena y muestra el calendario."""
        self.params.holidays.sort(key=lambda row: row[0])
        self.holidays.setRowCount(len(self.params.holidays))
        for row, (day, description) in enumerate(self.params.holidays):
            self.holidays.setItem(
                row, 0, QTableWidgetItem(f"{day:%d/%m/%Y}")
            )
            self.holidays.setItem(
                row, 1, QTableWidgetItem(description)
            )

    def add_holiday(self) -> None:
        """Añade un día no laborable validado."""
        day = parse_date(self.new_holiday_date.text())
        if day is None:
            QMessageBox.critical(
                self, "Festivo", "Introduce la fecha como dd/mm/aaaa."
            )
            return
        self.params.holidays.append(
            (day, self.new_holiday_description.text().strip() or "Festivo")
        )
        self.new_holiday_date.clear()
        self.new_holiday_description.clear()
        self._refresh_holidays()
        self.recalculate()

    def delete_holiday(self) -> None:
        """Elimina el festivo seleccionado."""
        row = self.holidays.currentRow()
        if row < 0:
            return
        del self.params.holidays[row]
        self._refresh_holidays()
        self.recalculate()

    def apply_params(self) -> None:
        """Valida parámetros y abre la secuencia."""
        start = parse_date(self.start.text())
        if start is None:
            QMessageBox.critical(
                self, "Parámetros", "La fecha debe tener formato dd/mm/aaaa."
            )
            return
        try:
            hours = float(self.hours.text().replace(",", "."))
        except ValueError:
            QMessageBox.critical(
                self, "Parámetros", "Las horas por jornada deben ser numéricas."
            )
            return
        if hours <= 0:
            QMessageBox.critical(
                self, "Parámetros", "Las horas deben ser mayores que cero."
            )
            return
        self.params.start = start
        self.params.hours_per_day = hours
        self.params.scale = int(self.scale.currentText())
        self.params.plan = self.detail.currentText()
        self.recalculate()
        self.tabs.setCurrentIndex(1)

    def sequence_chain(self) -> None:
        """Encadena actividades fin-comienzo."""
        chain_sequence(self.activities)
        self.recalculate()
        self.status.setText(
            "Actividades encadenadas una tras otra (fin-comienzo)."
        )

    def edit_phases(self) -> None:
        """Edita la lista común de fases sin perder los repartos ya asignados."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Fases de obra")
        dialog.resize(605, 340)
        layout = QVBoxLayout(dialog)
        help_text = QLabel(
            "Desfase en días laborables respecto al final de la fase anterior: "
            "0 para empezar después, positivo para una pausa, negativo para solapar."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(("Nombre de la fase", "Desfase"))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(table)
        indexes = list(range(len(self.params.phases)))
        assigned = {index for activity in self.activities for index in activity.phase_shares}

        def append(name: str, lag: int) -> None:
            """Crea una fila editable al final de la lista."""
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(name))
            table.setItem(row, 1, QTableWidgetItem(str(lag)))

        for phase in self.params.phases:
            append(phase.name, phase.lag)

        def add() -> None:
            """Añade una fase consecutiva y abre la edición de su nombre."""
            indexes.append(-1)
            append("", 0)
            table.editItem(table.item(table.rowCount() - 1, 0))

        def remove() -> None:
            """Evita retirar fases que todavía contienen porcentajes explícitos."""
            row = table.currentRow()
            if row < 0:
                return
            if indexes[row] in assigned:
                QMessageBox.warning(dialog, "Fase en uso", "Retira primero los repartos asignados a esta fase.")
                return
            indexes.pop(row)
            table.removeRow(row)
            if row == 0 and table.rowCount():
                table.item(0, 1).setText("0")

        actions = QHBoxLayout()
        for label, slot in (("Añadir fase", add), ("Eliminar fase", remove)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        phases: list[PlanPhase] = []

        def accept() -> None:
            """Valida nombres únicos y desfases antes de aceptar los cambios."""
            phases.clear()
            try:
                for row in range(table.rowCount()):
                    name = table.item(row, 0).text().strip()
                    if not name or any(phase.name.casefold() == name.casefold() for phase in phases):
                        raise ValueError("Cada fase debe tener un nombre distinto y no vacío.")
                    lag = int(table.item(row, 1).text())
                    if not -3650 <= lag <= 3650 or (row == 0 and lag):
                        raise ValueError("Usa desfases entre −3650 y 3650 días. La primera fase debe tener 0.")
                    phases.append(PlanPhase(name, lag))
            except ValueError as exc:
                QMessageBox.warning(dialog, "Fase no válida", str(exc))
                return
            dialog.accept()

        buttons.accepted.connect(accept)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            index_map = {old: new for new, old in enumerate(indexes) if old >= 0}
            for activity in self.activities:
                activity.phase_shares = {index_map[index]: value for index, value in activity.phase_shares.items()}
            self.params.phases = phases
            self.recalculate()

    def edit_phase_shares(self, activity) -> None:
        """Edita el porcentaje de medición que se ejecuta en cada fase."""
        if not self.params.phases or activity.continuous:
            QMessageBox.information(
                self, "Reparto por fases",
                "Configura primero las fases en Parámetros. Las partidas de toda la obra no se reparten.",
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{activity.code} — Reparto por fases")
        layout = QVBoxLayout(dialog)
        label = QLabel("Reparte el 100 % de la medición. Todo a 0 % usa la primera fase.")
        layout.addWidget(label)
        form = QFormLayout()
        layout.addLayout(form)
        inputs = []
        for index, phase in enumerate(self.params.phases):
            widget = QDoubleSpinBox()
            widget.setRange(0, 100)
            widget.setSuffix(" %")
            widget.setValue(activity.phase_shares.get(index, 0))
            form.addRow(phase.name, widget)
            inputs.append(widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def accept() -> None:
            """Guarda exclusivamente repartos completos o el valor automático."""
            shares = {index: widget.value() for index, widget in enumerate(inputs) if widget.value()}
            try:
                validate_phase_shares(shares, len(inputs))
            except ValueError as exc:
                QMessageBox.warning(dialog, "Reparto no válido", str(exc))
                return
            activity.phase_shares = shares
            dialog.accept()

        buttons.accepted.connect(accept)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.recalculate()

    def recalculate(self) -> None:
        """Programa fechas, repinta tabla y publica el estado."""
        schedule(self.activities, self.params)
        self.refresh_table()
        self._notify_state_change()
        if self._ready:
            self._set_dirty(True)

    def refresh_table(self) -> None:
        """Muestra actividades y resumen de plazo/PEM."""
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.activities))
        for row, activity in enumerate(self.activities):
            values = (
                activity.id,
                format_chapter_code(activity.chapter),
                activity.code,
                activity.summary,
                f"{fmt_number(activity.quantity)} {activity.unit}".strip(),
                fmt_number(activity.rendimiento, 4),
                activity.teams,
                activity.duration,
                activity.manual_duration or "",
                "S" if activity.continuous else "N",
                activity.pred or "",
                activity.relation,
                activity.lag,
                f"{activity.start_date:%d/%m/%Y}" if activity.start_date else "",
                f"{activity.end_date:%d/%m/%Y}" if activity.end_date else "",
                " · ".join(f"{self.params.phases[index].name}: {percent:g} %"
                           for index, percent in sorted(activity.phase_shares.items()))
                if activity.phase_shares and not activity.continuous else
                self.params.phases[0].name if self.params.phases and not activity.continuous else "",
            )
            for column, value in enumerate(values):
                self.table.setItem(
                    row, column, QTableWidgetItem(str(value))
                )
        self.table.blockSignals(False)
        term = max((activity.finish_offset for activity in self.activities), default=0)
        end = max(
            (
                activity.end_date
                for activity in self.activities
                if activity.end_date
            ),
            default=None,
        )
        end_text = f"  ·  Fin previsto: {end:%d/%m/%Y}" if end else ""
        pem = sum(activity.amount for activity in self.activities)
        self.summary.setText(
            f"Actividades: {len(self.activities)}  ·  "
            f"Plazo: {term} días laborables{end_text}  ·  "
            f"PEM: {self.app._format_currency(pem)}"
        )

    def edit_cell(self, row: int, column: int) -> None:
        """Edita equipos, duración, continuidad, predecesora o relación."""
        key = self.EDITABLE_COLUMNS.get(column)
        if key is None:
            return
        activity = self.activities[row]
        if key == "phases":
            self.edit_phase_shares(activity)
            return
        current = {
            "teams": str(activity.teams),
            "manual": str(activity.manual_duration or ""),
            "continuous": "S" if activity.continuous else "N",
            "pred": str(activity.pred or ""),
            "relation": activity.relation,
            "lag": str(activity.lag),
        }[key]
        if key in {"continuous", "relation"}:
            choices = ("S", "N") if key == "continuous" else RELATIONS
            value, accepted = QInputDialog.getItem(
                self,
                "Editar planificación",
                key,
                choices,
                choices.index(current) if current in choices else 0,
                False,
            )
        else:
            value, accepted = QInputDialog.getText(
                self, "Editar planificación", key, text=current
            )
        if not accepted:
            return
        self.commit_cell(activity, key, value)

    def commit_cell(self, activity, key: str, raw: str) -> None:
        """Valida una edición de secuencia y recalcula."""
        raw = raw.strip()
        try:
            if key == "teams":
                activity.teams = max(1, int(raw or 1))
            elif key == "manual":
                activity.manual_duration = int(raw) if raw else None
            elif key == "continuous":
                activity.continuous = raw.upper().startswith("S")
            elif key == "pred":
                activity.pred = int(raw) if raw else None
            elif key == "relation":
                activity.relation = (
                    raw.upper() if raw.upper() in RELATIONS else "FC"
                )
            elif key == "lag":
                activity.lag = int(raw or 0)
        except ValueError:
            QMessageBox.critical(
                self, "Valor no válido", f"«{raw}» no es un número válido."
            )
            return
        self.recalculate()

    def save_changes(self) -> None:
        """Guarda el estado mediante el proyecto principal."""
        self._notify_state_change()
        if self.app.save_project():
            self._set_dirty(False)

    def _generate_figure(self) -> tuple[Path, Path] | None:
        """Genera los PNG/PDF del Gantt con Matplotlib."""
        self.recalculate()
        if not self.activities:
            QMessageBox.information(
                self, "Plan de trabajo", "No hay actividades que dibujar."
            )
            return None
        try:
            from . import figura_gantt
        except ImportError as exc:
            QMessageBox.critical(
                self, "Plan de trabajo", f"No se pudo cargar figura_gantt: {exc}"
            )
            return None
        activities = [
            figura_gantt.Activity(
                id=activity.id,
                chapter=activity.chapter,
                code=activity.code,
                summary=f"[{activity.phase_name}] {activity.summary}" if activity.phase_name else activity.summary,
                unit=activity.unit,
                quantity=activity.quantity,
                amount=activity.amount,
                duration=activity.duration,
                start=activity.start_date,
                end=activity.end_date,
                continuous=activity.continuous,
                phased=bool(activity.phase_name),
            )
            for activity in activity_segments(self.activities)
            if activity.start_date and activity.end_date
        ]
        names = {
            activity.chapter: activity.chapter_name
            for activity in self.activities
        }
        output = Path(tempfile.gettempdir()) / "plan_de_trabajo"
        try:
            return figura_gantt.draw(
                activities,
                names,
                min(activity.start for activity in activities),
                max(activity.end for activity in activities),
                max(activity.finish_offset for activity in self.activities),
                sum(activity.amount for activity in self.activities),
                self.params.holiday_dates,
                self.project_title,
                self.params.plan,
                output,
                currency_formatter=self.app._format_currency,
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "Plan de trabajo", f"No se pudo dibujar el plan:\n{exc}"
            )
            return None

    def draw_plan(self) -> None:
        """Muestra el PNG generado con accesos a PDF y copias."""
        result = self._generate_figure()
        if result is None:
            return
        png, pdf = result
        dialog = GanttPreviewDialogQt(self, png, pdf)
        dialog.exec()

    def save_plan_report(self) -> None:
        """Guarda la planificación en PDF u ODT."""
        selection = get_document_save_path(
            self,
            "Guardar planificación",
            "Plan de trabajo.pdf",
        )
        if selection is None:
            return
        target, _output_format = selection
        extension = target.suffix
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=extension, dir=target.parent, delete=False
            ) as output:
                temporary = Path(output.name)
            self.recalculate()
            write_planning_report(
                self.activities,
                self.params,
                temporary,
                self.app.report_settings,
                self.project_title,
                self.app._format_currency,
            )
            temporary.replace(target)
            temporary = None
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "No se pudo guardar la planificación", str(exc)
            )
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.status.setText(f"Planificación guardada: {target}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Publica la última edición antes de cerrar."""
        self._notify_state_change()
        event.accept()


class GanttPreviewDialogQt(QDialog):
    """Visor del PNG con apertura y copia en PNG/PDF."""

    def __init__(
        self, parent: PlanningWindowQt, png: Path, pdf: Path
    ) -> None:
        """Carga la imagen generada manteniéndola escalable."""
        super().__init__(parent)
        self.png, self.pdf = png, pdf
        self.setWindowTitle("Plan de trabajo")
        self.resize(1250, 760)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.preview = QLabel()
        pixmap = QPixmap(str(png))
        if not pixmap.isNull():
            self.preview.setPixmap(
                pixmap.scaledToWidth(1100, Qt.TransformationMode.SmoothTransformation)
            )
            scroll.setWidget(self.preview)
        else:
            scroll.setWidget(QLabel(f"Figura generada:\n{png}"))
        layout.addWidget(scroll, 1)
        actions = QHBoxLayout()
        for text, path in (("Abrir PNG", png), ("Abrir PDF", pdf)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, selected=path: self.open_file(selected))
            actions.addWidget(button)
        copy = QPushButton("Guardar como…")
        copy.clicked.connect(self.save_copy)
        actions.addWidget(copy)
        actions.addStretch()
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)

    def open_file(self, path: Path) -> None:
        """Abre el archivo con la aplicación asociada."""
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            webbrowser.open(path.as_uri())

    def save_copy(self) -> None:
        """Guarda una copia del Gantt en PDF o PNG."""
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar plan de trabajo",
            "plan_de_trabajo.pdf",
            "PDF (*.pdf);;PNG (*.png)",
        )
        if not target:
            return
        destination = Path(target)
        source = self.pdf if destination.suffix.lower() == ".pdf" else self.png
        shutil.copyfile(source, destination)
