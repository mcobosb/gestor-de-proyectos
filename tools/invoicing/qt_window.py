"""Lectura y conciliación de facturas PDF con PySide6."""
from __future__ import annotations

from functools import partial
import os
from pathlib import Path
import queue
import threading
import traceback

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from budget_core import fmt_eur
from . import (
    ALL_PERIODS,
    UNDATED_PERIOD,
    CostRow,
    Invoice,
    InvoiceLine,
    InvoiceState,
    amount,
    assign_line,
    assign_matching_lines,
    budget_rows,
    identifier,
    invoice_month,
    invoice_sort_key,
    period_invoices,
    read_invoice,
    render_invoice_page,
    total,
)


class InvoicingWindowQt(QDialog):
    """Matriz de costes, registro de facturas y visor PDF."""

    def __init__(self, app, state: InvoiceState) -> None:
        """Restaura una copia validada del estado y construye los paneles."""
        super().__init__(app)
        self.app = app
        self.data = InvoiceState.from_dict(state.to_dict())
        self.resource_filter = ""
        self.visible_invoice_id: str | None = None
        self.dirty = False
        self.scanning = False
        self._closed = threading.Event()
        self.results: queue.Queue[Invoice | None] = queue.Queue()
        self.pdf_page = 0
        self.pdf_pages = 0
        self.pdf_zoom = 1.0
        self.pdf_source: tuple[str, str, str] | None = None
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_scan)
        if not self.data.rows:
            self.data.rows, warnings = budget_rows(self.app)
            if warnings:
                QMessageBox.warning(self, "Facturación", "\n".join(warnings))
        self.setWindowTitle("Facturación")
        self.resize(1480, 800)
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._billing_panel())
        splitter.addWidget(self._pdf_panel())
        splitter.setSizes((780, 640))
        layout.addWidget(splitter)
        if self.data.to_dict() != state.to_dict():
            self.changed()
        self.refresh()

    def _billing_panel(self) -> QWidget:
        """Crea comandos, filtros, registro y tablas conciliadas."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        toolbar = QHBoxLayout()
        for text, slot in (
            ("Carpeta…", self.change_folder),
            ("Actualizar lectura", self.scan),
            ("Revisar líneas", self.review_lines),
            ("Eliminar factura", self.delete_invoice),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        toolbar.addStretch()
        self.save_button = QPushButton("Guardar")
        self.save_button.clicked.connect(self.save)
        toolbar.addWidget(self.save_button)
        layout.addLayout(toolbar)
        controls = QHBoxLayout()
        self.unify = QCheckBox("Unificar recursos")
        self.unify.toggled.connect(self.refresh)
        controls.addWidget(self.unify)
        for kind, label in (
            ("Material", "Material"),
            ("Maquinaria", "Maquinaria"),
            ("RCDs", "RCDs"),
        ):
            button = QCheckBox(label)
            button.toggled.connect(partial(self.toggle_filter, kind))
            controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(QLabel("Periodo acumulado:"))
        self.period = QComboBox()
        self.period.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.period)
        layout.addLayout(controls)
        self.folder_label = QLabel(self.data.folder)
        layout.addWidget(self.folder_label)
        invoice_controls = QHBoxLayout()
        invoice_controls.addWidget(QLabel("Factura:"))
        self.invoice_selector = QComboBox()
        self.invoice_selector.currentIndexChanged.connect(self.refresh)
        invoice_controls.addWidget(self.invoice_selector, 1)
        layout.addLayout(invoice_controls)
        self.register = QTableWidget(0, 2)
        self.register.setHorizontalHeaderLabels(("Fecha", "Fichero PDF"))
        self.register.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.register.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.register.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.register.cellDoubleClicked.connect(self.open_registered)
        layout.addWidget(self.register)
        self.status = QLabel(
            "Selecciona una carpeta y pulsa Actualizar lectura."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        tables = QHBoxLayout()
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels((
            "Capítulo", "Partida", "Recurso", "Descripción", "Tipo",
            "Unidad", "Factura",
        ))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.edit_amount)
        self.table.itemSelectionChanged.connect(self.sync_totals_selection)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        tables.addWidget(self.table, 4)
        self.totals = QTableWidget(0, 3)
        self.totals.setHorizontalHeaderLabels(
            ("Previsto", "Facturado", "Restante")
        )
        self.totals.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.totals.itemSelectionChanged.connect(self.sync_matrix_selection)
        tables.addWidget(self.totals, 1)
        layout.addLayout(tables, 1)
        self.footer = QLabel()
        self.footer.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.footer)
        return panel

    def _pdf_panel(self) -> QWidget:
        """Crea controles, zoom y visor de la página PDF."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        controls = QHBoxLayout()
        previous = QPushButton("←")
        previous.clicked.connect(lambda: self.change_pdf_page(-1))
        controls.addWidget(previous)
        following = QPushButton("→")
        following.clicked.connect(lambda: self.change_pdf_page(1))
        controls.addWidget(following)
        self.pdf_page_label = QLabel()
        controls.addWidget(self.pdf_page_label)
        controls.addWidget(QLabel("Zoom:"))
        self.zoom = QComboBox()
        self.zoom.addItems(("50%", "75%", "100%", "125%", "150%", "200%"))
        self.zoom.setCurrentText("100%")
        self.zoom.currentTextChanged.connect(self.change_zoom)
        controls.addWidget(self.zoom)
        controls.addStretch()
        external = QPushButton("Abrir PDF externo")
        external.clicked.connect(self.open_external_pdf)
        controls.addWidget(external)
        layout.addLayout(controls)
        self.pdf_status = QLabel("Selecciona una factura.")
        layout.addWidget(self.pdf_status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.pdf_image = QLabel()
        self.pdf_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(self.pdf_image)
        layout.addWidget(scroll, 1)
        return panel

    def changed(self) -> None:
        """Valida, ordena y publica el estado en el proyecto."""
        self.data.invoices.sort(key=invoice_sort_key)
        self.app.invoicing_state = InvoiceState.from_dict(
            self.data.to_dict()
        ).to_dict()
        self.app._mark_dirty()
        self.dirty = True
        self.save_button.setStyleSheet(
            "font-weight: bold; background: #202124; color: white;"
        )

    def current_invoice(self) -> Invoice | None:
        """Devuelve la factura seleccionada."""
        invoice_id = self.invoice_selector.currentData()
        return next(
            (
                invoice for invoice in self.data.invoices
                if invoice.id == invoice_id
            ),
            None,
        )

    def refresh(self) -> None:
        """Reconstruye registro, matriz y totales acumulados."""
        self.data.invoices.sort(key=invoice_sort_key)
        months = sorted({
            invoice_month(invoice)
            for invoice in self.data.invoices
            if invoice_month(invoice) != UNDATED_PERIOD
        })
        values = [ALL_PERIODS, *months, UNDATED_PERIOD]
        current_period = self.period.currentData() or ALL_PERIODS
        self.period.blockSignals(True)
        self.period.clear()
        for value in values:
            self.period.addItem(value, value)
        self.period.setCurrentIndex(
            max(0, self.period.findData(current_period))
        )
        self.period.blockSignals(False)
        current_invoice = self.current_invoice()
        self.invoice_selector.blockSignals(True)
        self.invoice_selector.clear()
        for invoice in self.data.invoices:
            self.invoice_selector.addItem(
                f"{invoice.date or 'Sin fecha'} — {invoice.name}",
                invoice.id,
            )
        if self.data.invoices:
            index = self.invoice_selector.findData(
                current_invoice.id if current_invoice else self.data.invoices[0].id
            )
            self.invoice_selector.setCurrentIndex(max(0, index))
        self.invoice_selector.blockSignals(False)
        invoice = self.current_invoice()
        self.visible_invoice_id = invoice.id if invoice else None
        period = str(self.period.currentData() or ALL_PERIODS)
        invoices = period_invoices(self.data.invoices, period)
        rows = [
            row for row in self.data.rows
            if not self.resource_filter or row.kind == self.resource_filter
        ]
        groups: dict[tuple[str, ...], list[CostRow]] = {}
        for row in rows:
            key = (
                (row.resource, row.description, row.kind, row.unit)
                if self.unify.isChecked() and row.resource
                else (row.id,)
            )
            groups.setdefault(key, []).append(row)
        self.table.setRowCount(len(groups))
        self.totals.setRowCount(len(groups))
        self.visible_rows: list[tuple[str, list[CostRow]]] = []
        for index, members in enumerate(groups.values()):
            row = members[0]
            self.visible_rows.append((row.id, members))
            row_budget = total(member.budget for member in members)
            billed = total(
                invoice_item.amounts.get(member.id, 0)
                for invoice_item in invoices
                for member in members
            )
            remaining = total((row_budget, -billed))
            values = (
                row.chapter,
                row.item,
                row.resource,
                row.description,
                row.kind,
                row.unit,
                (
                    fmt_eur(total(
                        invoice.amounts.get(member.id, 0)
                        for member in members
                    ))
                    if invoice else ""
                ),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.id)
                self.table.setItem(index, column, item)
            for column, value in enumerate((
                fmt_eur(row_budget), fmt_eur(billed), fmt_eur(remaining)
            )):
                cell = QTableWidgetItem(value)
                if remaining < 0:
                    cell.setForeground(Qt.GlobalColor.darkRed)
                self.totals.setItem(index, column, cell)
        self.register.setRowCount(len(self.data.invoices))
        for index, registered in enumerate(self.data.invoices):
            date_item = QTableWidgetItem(registered.date)
            date_item.setData(Qt.ItemDataRole.UserRole, registered.id)
            self.register.setItem(index, 0, date_item)
            self.register.setItem(
                index, 1, QTableWidgetItem(Path(registered.path).name)
            )
        budget = total(row.budget for row in rows)
        billed = total(
            registered.amounts.get(row.id, 0)
            for registered in invoices
            for row in rows
        )
        self.footer.setText(
            f"Previsto: {fmt_eur(budget)}    Facturado: {fmt_eur(billed)}    "
            f"Restante: {fmt_eur(total((budget, -billed)))}"
        )
        self.show_invoice_status(invoice)

    def toggle_filter(self, kind: str, checked: bool) -> None:
        """Activa o desactiva un filtro exclusivo."""
        self.resource_filter = kind if checked else ""
        self.refresh()

    def selected_invoice(self) -> Invoice | None:
        """Exige una factura seleccionada."""
        invoice = self.current_invoice()
        if invoice is None:
            QMessageBox.information(self, "Facturación", "Selecciona una factura.")
        return invoice

    def edit_amount(self, row: int, column: int) -> None:
        """Edita el importe de una factura para una fila de coste."""
        if column != 6 or self.unify.isChecked():
            return
        invoice = self.selected_invoice()
        if invoice is None:
            return
        row_id = self.visible_rows[row][0]
        value, accepted = QInputDialog.getText(
            self,
            "Asignar importe",
            "Importe:",
            text=str(invoice.amounts.get(row_id, 0)),
        )
        if accepted:
            self.commit_cell(invoice.id, row_id, value)

    def commit_cell(self, invoice_id: str, row_id: str, raw: str) -> bool:
        """Valida una asignación manual, desvinculando líneas automáticas."""
        try:
            value = amount(raw)
        except ValueError as exc:
            QMessageBox.critical(self, "Facturación", str(exc))
            return False
        invoice = next(
            invoice for invoice in self.data.invoices if invoice.id == invoice_id
        )
        for line in invoice.lines:
            if line.row_id == row_id:
                line.row_id = ""
        invoice.amounts[row_id] = value
        self.changed()
        self.refresh()
        return True

    def show_invoice_status(self, invoice: Invoice | None) -> None:
        """Actualiza metadatos, incidencias y vista PDF."""
        if invoice is None:
            self.status.setText(
                "Selecciona una carpeta y pulsa Actualizar lectura."
            )
            self.pdf_image.clear()
            self.pdf_source = None
            return
        pending = sum(not line.row_id for line in invoice.lines)
        difference = total((invoice.declared, -total(invoice.amounts.values())))
        self.status.setText(
            f"{invoice.name} | Obra: {invoice.work} | "
            f"Expediente: {invoice.reference} | Fecha: {invoice.date}\n"
            f"Importe: {fmt_eur(invoice.declared)} | "
            f"Sin asignar: {fmt_eur(difference)} | "
            f"Líneas pendientes: {pending}\n{invoice.notes[:500]}"
        )
        self.show_pdf(invoice)

    def sync_totals_selection(self) -> None:
        """Sincroniza la selección de matriz con totales."""
        row = self.table.currentRow()
        if row >= 0 and self.totals.currentRow() != row:
            self.totals.blockSignals(True)
            self.totals.selectRow(row)
            self.totals.blockSignals(False)

    def sync_matrix_selection(self) -> None:
        """Sincroniza la selección de totales con matriz."""
        row = self.totals.currentRow()
        if row >= 0 and self.table.currentRow() != row:
            self.table.blockSignals(True)
            self.table.selectRow(row)
            self.table.blockSignals(False)

    def open_registered(self, row: int, _column: int) -> None:
        """Selecciona y visualiza la factura registrada."""
        if 0 <= row < len(self.data.invoices):
            invoice = self.data.invoices[row]
            self.invoice_selector.setCurrentIndex(
                self.invoice_selector.findData(invoice.id)
            )

    def show_pdf(self, invoice: Invoice) -> None:
        """Renderiza la página activa con pypdfium2."""
        path = Path(invoice.path).expanduser()
        source = (invoice.id, invoice.digest, invoice.path)
        if source != self.pdf_source:
            self.pdf_source = source
            self.pdf_page = 0
        if not invoice.path.strip() or not path.is_file() or path.suffix.lower() != ".pdf":
            self.pdf_status.setText("No se encuentra un PDF válido.")
            self.pdf_image.clear()
            return
        try:
            image, self.pdf_pages = render_invoice_page(
                path, self.pdf_page, 900, self.pdf_zoom
            )
            qt_image = ImageQt(image)
            self.pdf_image.setPixmap(QPixmap.fromImage(qt_image))
            self.pdf_status.setText(path.name)
            self.pdf_page_label.setText(
                f"{self.pdf_page + 1} / {self.pdf_pages}"
            )
        except (ImportError, OSError, ValueError) as exc:
            self.pdf_status.setText(f"No se pudo visualizar: {exc}")
            self.pdf_image.clear()

    def change_pdf_page(self, direction: int) -> None:
        """Avanza o retrocede dentro del PDF."""
        target = self.pdf_page + direction
        if 0 <= target < self.pdf_pages:
            self.pdf_page = target
            invoice = self.current_invoice()
            if invoice is not None:
                self.show_pdf(invoice)

    def change_zoom(self, text: str) -> None:
        """Actualiza zoom y vuelve a renderizar."""
        self.pdf_zoom = int(text.rstrip("%")) / 100
        invoice = self.current_invoice()
        if invoice is not None:
            self.show_pdf(invoice)

    def open_external_pdf(self) -> None:
        """Abre la factura activa fuera de la aplicación."""
        invoice = self.selected_invoice()
        if invoice is None:
            return
        path = Path(invoice.path).expanduser()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            QMessageBox.critical(
                self, "Facturación", f"No se encuentra un PDF válido:\n{invoice.path}"
            )
            return
        try:
            os.startfile(path.resolve())  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.critical(self, "Facturación", str(exc))

    def change_folder(self) -> None:
        """Selecciona otra carpeta y lanza lectura."""
        if self.scanning:
            QMessageBox.information(
                self, "Facturación", "Espera a que termine la lectura."
            )
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Carpeta de facturas", self.data.folder
        )
        if folder:
            self.data.folder = folder
            self.folder_label.setText(folder)
            self.changed()
            self.scan()

    def scan(self) -> None:
        """Lee PDFs en un hilo y devuelve resultados al bucle Qt."""
        if self.scanning:
            return
        if not self.data.folder.strip():
            QMessageBox.information(
                self, "Facturación", "Selecciona una carpeta."
            )
            return
        try:
            paths = sorted(
                path
                for path in Path(self.data.folder).iterdir()
                if path.is_file() and path.suffix.lower() == ".pdf"
            )
        except OSError as exc:
            QMessageBox.critical(self, "Facturación", str(exc))
            return
        self.scanning = True
        self.status.setText(f"Leyendo {len(paths)} PDF…")

        def worker() -> None:
            """Aísla errores por factura y entrega cada resultado."""
            try:
                for path in paths:
                    if self._closed.is_set():
                        break
                    try:
                        self.results.put(read_invoice(path))
                    except Exception as exc:
                        self.results.put(Invoice(
                            identifier(),
                            path.stem,
                            path=str(path.resolve()),
                            notes=(
                                f"Error al procesar el PDF: "
                                f"{type(exc).__name__}: {exc}"
                            ),
                        ))
            finally:
                self.results.put(None)

        threading.Thread(target=worker, daemon=True).start()
        self.poll_timer.start(100)

    def poll_scan(self) -> None:
        """Integra resultados conservando asignaciones existentes."""
        changed = False
        finished = False
        notices = []
        while True:
            try:
                result = self.results.get_nowait()
            except queue.Empty:
                break
            if result is None:
                finished = True
                break
            same = next(
                (
                    invoice for invoice in self.data.invoices
                    if invoice.path == result.path
                ),
                None,
            )
            duplicate = next(
                (
                    invoice for invoice in self.data.invoices
                    if result.digest and invoice.digest == result.digest
                ),
                None,
            )
            if same is not None:
                if "Error al procesar el PDF:" in result.notes:
                    notices.append(f"{result.name}: {result.notes}")
                    continue
                previous = list(same.lines)
                for line in result.lines:
                    match = next(
                        (
                            old for old in previous
                            if old.item == line.item and old.amount == line.amount
                        ),
                        None,
                    )
                    if match is not None:
                        line.row_id = match.row_id
                        previous.remove(match)
                if any(line.row_id for line in previous):
                    result.notes += (
                        "\nHay líneas asignadas cambiadas o desaparecidas; "
                        "sus importes se conservan como asignaciones manuales."
                    )
                for key in (
                    "name", "digest", "work", "reference", "date",
                    "declared", "notes", "lines",
                ):
                    setattr(same, key, getattr(result, key))
                changed = True
                if result.notes:
                    notices.append(f"{result.name}: {result.notes}")
                continue
            if duplicate is not None:
                if not Path(duplicate.path).is_file():
                    duplicate.path = result.path
                    changed = True
                else:
                    notices.append(
                        f"{result.name}: duplicado de {duplicate.name}."
                    )
                continue
            assign_matching_lines(result, self.data.rows)
            self.data.invoices.append(result)
            changed = True
        if changed:
            self.changed()
        if finished:
            self.scanning = False
            self.poll_timer.stop()
            self.refresh()
            if notices:
                summary = "\n".join(notices)
                QMessageBox.warning(
                    self, "Revisión de facturas", summary[:2000]
                )
            if not self.data.invoices:
                self.status.setText("No se encontraron PDF.")

    def review_lines(self) -> None:
        """Abre la revisión de líneas extraídas y sus asignaciones."""
        invoice = self.selected_invoice()
        if invoice is None:
            return
        dialog = InvoiceLinesDialogQt(self, invoice)
        dialog.exec()

    def delete_invoice(self) -> None:
        """Elimina el registro visible sin borrar el PDF."""
        invoice = self.selected_invoice()
        if invoice is None:
            return
        if QMessageBox.question(
            self,
            "Facturación",
            "¿Eliminar esta columna y sus importes? No se borrará el PDF.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.data.invoices.remove(invoice)
        self.changed()
        self.refresh()

    def save(self) -> None:
        """Guarda la facturación dentro del proyecto."""
        if self.scanning:
            QMessageBox.information(
                self, "Facturación", "Espera a que termine la lectura."
            )
            return
        self.changed()
        if self.app.save_project():
            self.dirty = False
            self.save_button.setStyleSheet("")
            self.status.setText("Facturación guardada con el proyecto.")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Cancela lecturas pendientes con confirmación."""
        if self.scanning and QMessageBox.question(
            self,
            "Facturación",
            "La lectura sigue en curso. ¿Cerrar y cancelar el proceso?",
        ) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._closed.set()
        self.poll_timer.stop()
        event.accept()


class InvoiceLinesDialogQt(QDialog):
    """Revisa, añade, edita, asigna y elimina líneas de una factura."""

    def __init__(self, owner: InvoicingWindowQt, invoice: Invoice) -> None:
        """Carga detalle y selector de costes destino."""
        super().__init__(owner)
        self.owner, self.invoice = owner, invoice
        self.setWindowTitle("Revisar líneas de factura")
        self.resize(960, 540)
        layout = QVBoxLayout(self)
        detail = QPlainTextEdit(
            f"{invoice.name} | {invoice.date}\n{invoice.path}\n{invoice.notes}"
        )
        detail.setReadOnly(True)
        detail.setMaximumHeight(120)
        layout.addWidget(detail)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ("Partida leída", "Importe", "Asignación")
        )
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table, 1)
        self.target = QComboBox()
        self.target.addItems([
            f"{index + 1}. {row.chapter} / {row.item} / {row.resource} "
            f"— {row.description} ({row.kind})"
            for index, row in enumerate(owner.data.rows)
        ])
        layout.addWidget(self.target)
        actions = QHBoxLayout()
        for text, slot in (
            ("Asignar al coste seleccionado", self.assign),
            ("Desasignar", self.unassign),
            ("Añadir línea", self.add_line),
            ("Editar línea", self.edit_line),
            ("Eliminar línea", self.delete_line),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        """Reconstruye las líneas con la asignación vigente."""
        names = {
            row.id: f"{row.chapter} / {row.item} / {row.resource}"
            for row in self.owner.data.rows
        }
        self.table.setRowCount(len(self.invoice.lines))
        for index, line in enumerate(self.invoice.lines):
            self.table.setItem(index, 0, QTableWidgetItem(line.item))
            self.table.setItem(
                index, 1, QTableWidgetItem(f"{line.amount:.2f}")
            )
            self.table.setItem(
                index, 2, QTableWidgetItem(names.get(line.row_id, "Pendiente"))
            )

    def selected_line(self) -> InvoiceLine | None:
        """Exige una línea seleccionada."""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Facturación", "Selecciona una línea."
            )
            return None
        return self.invoice.lines[row]

    def assign(self) -> None:
        """Asigna una línea al coste activo."""
        line = self.selected_line()
        row = self.target.currentIndex()
        if line is None or row < 0:
            return
        assign_line(
            self.invoice, line, self.owner.data.rows[row].id
        )
        self.owner.changed()
        self.owner.refresh()
        self.refresh()

    def unassign(self) -> None:
        """Retira la asignación sin eliminar la línea."""
        line = self.selected_line()
        if line is not None:
            assign_line(self.invoice, line, "")
            self.owner.changed()
            self.owner.refresh()
            self.refresh()

    def add_line(self) -> None:
        """Añade una línea manual validada."""
        self.edit_line(new=True)

    def edit_line(self, *, new: bool = False) -> None:
        """Edita o añade descripción e importe."""
        line = InvoiceLine("", 0) if new else self.selected_line()
        if line is None:
            return
        description, accepted = QInputDialog.getText(
            self, "Editar línea", "Partida / descripción:",
            text=line.item,
        )
        if not accepted or not description.strip():
            return
        raw, accepted = QInputDialog.getText(
            self, "Editar línea", "Importe:", text=str(line.amount)
        )
        if not accepted:
            return
        try:
            value = amount(raw)
        except ValueError as exc:
            QMessageBox.critical(self, "Facturación", str(exc))
            return
        previous = line.row_id
        assign_line(self.invoice, line, "")
        line.item, line.amount = description.strip(), value
        if new:
            self.invoice.lines.append(line)
        assign_line(self.invoice, line, previous)
        self.owner.changed()
        self.owner.refresh()
        self.refresh()

    def delete_line(self) -> None:
        """Elimina una línea y su asignación."""
        line = self.selected_line()
        if line is None:
            return
        if QMessageBox.question(
            self, "Facturación", "¿Eliminar esta línea y su asignación?"
        ) != QMessageBox.StandardButton.Yes:
            return
        assign_line(self.invoice, line, "")
        self.invoice.lines.remove(line)
        self.owner.changed()
        self.owner.refresh()
        self.refresh()
