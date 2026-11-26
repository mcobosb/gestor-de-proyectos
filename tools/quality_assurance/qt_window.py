"""Revisión de ensayos por unidad de obra."""
from __future__ import annotations

from pathlib import Path
from decimal import Decimal, InvalidOperation
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from collaborative_storage import shared_library_path
from qt_localization import get_document_save_path
from tools._library_editor_qt import LibraryEditorQt

from . import TOOL_LABEL
from .catalog import get_catalog_entry, load_catalog
from .document import DOCUMENTS, LIBRARY_PATH, write_document
from .plan import (
    TRIALS, applicable_trials, has_inserted, insert_budget, suggested_quantity, unit_snapshot,
    validate_rows, work_units,
)
from .pricing import price_candidates

FIELDS = ("name", "frequency", "count", "criteria", "price", "reference")
ROLE = Qt.ItemDataRole.UserRole


class QualityWindow(QDialog):
    """Edita y confirma el plan antes de exportar o incorporar partidas."""

    def __init__(self, app) -> None:
        """Restaura la revisión del proyecto o prepara propuestas para sus partidas."""
        super().__init__(app)
        self.app = app
        self.editor: LibraryEditorQt | None = None
        self.reviewed = False
        self.setWindowTitle(TOOL_LABEL)
        self.resize(1250, 690)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Revisa las UO, la frecuencia, el número de ensayos y los criterios del pliego. "
            "Los precios se introducen sin costes indirectos y solo son obligatorios para insertar. "
            "Las coincidencias del catálogo aparecen desmarcadas; confirma su aplicabilidad. "
            "Las frecuencias y tolerancias del libro son orientativas y no acreditan cumplimiento normativo."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        top = QHBoxLayout()
        library = QPushButton("Editar fichas del anejo…")
        library.clicked.connect(self.open_library)
        top.addWidget(library)
        self.suggest_button = QPushButton("Completar propuestas")
        self.suggest_button.clicked.connect(self.suggest)
        top.addWidget(self.suggest_button)
        top.addStretch()
        layout.addLayout(top)
        manual = QHBoxLayout()
        self.unit = QComboBox()
        self.unit.setMinimumContentsLength(25)
        self.unit.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        manual.addWidget(QLabel("Unidad de obra:"))
        manual.addWidget(self.unit, 1)
        self.catalog = QComboBox()
        self.catalog.setMinimumContentsLength(32)
        self.catalog.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.catalog.setMaxVisibleItems(20)
        for entry in load_catalog():
            self.catalog.addItem(entry.label, entry.identifier)
        manual.addWidget(QLabel("Control / ensayo:"))
        manual.addWidget(self.catalog, 2)
        self.add_catalog_button = QPushButton("Añadir del catálogo")
        self.add_catalog_button.clicked.connect(self.add_catalog)
        manual.addWidget(self.add_catalog_button)
        self.add_button = QPushButton("Añadir fila libre")
        self.add_button.clicked.connect(self.add_manual)
        manual.addWidget(self.add_button)
        self.remove_button = QPushButton("Quitar filas seleccionadas")
        self.remove_button.clicked.connect(self.remove_rows)
        manual.addWidget(self.remove_button)
        layout.addLayout(manual)
        price_bar = QHBoxLayout()
        self.price_button = QPushButton("Consultar precios BCCA para la fila…")
        self.price_button.clicked.connect(self.choose_price)
        price_bar.addWidget(self.price_button)
        price_bar.addStretch()
        layout.addLayout(price_bar)
        self.table = QTableWidget(0, 8)
        self.table.setObjectName("qualityTrialsTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setHorizontalHeaderLabels(("Incluir", "Unidad de obra", "Ensayo / prueba",
                                              "Frecuencia", "N.º", "Aceptación", "Precio directo/ud",
                                              "Referencia"))
        for column, width in enumerate((65, 220, 210, 165, 65, 260, 120, 180)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.invalidate)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.review_button = QPushButton("Confirmar revisión")
        self.review_button.clicked.connect(self.review)
        actions.addWidget(self.review_button)
        actions.addStretch()
        self.insert_button = QPushButton("Insertar ensayos en el presupuesto")
        self.insert_button.clicked.connect(self.insert)
        actions.addWidget(self.insert_button)
        self.export_button = QPushButton("Generar anejo…")
        self.export_button.clicked.connect(self.export)
        actions.addWidget(self.export_button)
        layout.addLayout(actions)
        self.refresh_actions()
        saved = app.quality_assurance_state.get("rows", [])
        if saved:
            self.table.blockSignals(True)
            for row in saved:
                self.append_row(row)
            self.table.blockSignals(False)
            self.reviewed = True
        else:
            self.suggest()
        self.refresh_actions()
        app.budget_refreshed.connect(self.refresh_actions)

    def append_row(self, row: dict) -> None:
        """Añade una fila con referencia estable a la partida de origen."""
        index = self.table.rowCount()
        self.table.insertRow(index)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        check.setCheckState(Qt.CheckState.Checked if row.get("included", True) else Qt.CheckState.Unchecked)
        check.setData(ROLE, row)
        self.table.setItem(index, 0, check)
        code, chapter, summary, unit, quantity = row["snapshot"]
        label = f"{code} — {summary} ({quantity:g} {unit})"
        origin = QTableWidgetItem(label)
        origin.setFlags(origin.flags() & ~Qt.ItemFlag.ItemIsEditable)
        origin.setToolTip(f"{chapter} · {label}")
        self.table.setItem(index, 1, origin)
        for column, name in enumerate(FIELDS, 2):
            cell = QTableWidgetItem(str(row.get(name, "")))
            if name == "name":
                cell.setToolTip(str(row.get("details", "")))
            if name == "price" and row.get("price_source"):
                source = row["price_source"]
                cell.setToolTip(f"{source['code']} — {source['summary']} · {source['source']}")
            self.table.setItem(index, column, cell)

    def rows(self) -> list[dict]:
        """Recoge todas las filas, preservando las exclusiones para futuras revisiones."""
        rows = [
            dict(self.table.item(index, 0).data(ROLE)) |
            {name: self.table.item(index, column).text() for column, name in enumerate(FIELDS, 2)} |
            {"included": self.table.item(index, 0).checkState() == Qt.CheckState.Checked}
            for index in range(self.table.rowCount())
        ]
        for row in rows:
            source = row.get("price_source")
            if source:
                try:
                    same_price = Decimal(row["price"].replace(",", ".")) == Decimal(source["price"])
                except InvalidOperation:
                    same_price = False
                if not same_price or row["name"] != source["trial_name"]:
                    row.pop("price_source")
        return rows

    def choose_price(self) -> None:
        """Permite seleccionar expresamente una partida BCCA sin pisar precios sin aviso."""
        index = self.table.currentRow()
        if index < 0:
            QMessageBox.information(self, TOOL_LABEL, "Selecciona primero una fila de ensayo.")
            return
        if self.app.report_settings.currency != "EUR":
            QMessageBox.warning(self, TOOL_LABEL, "Los precios BCCA están en euros; revisa la moneda del proyecto.")
            return
        name = self.table.item(index, 2).text().strip()
        query, accepted = QInputDialog.getText(
            self, TOOL_LABEL, "Busca por palabras o código; deja vacío para ver propuestas orientativas:"
        )
        if not accepted:
            return
        try:
            candidates = price_candidates(name, query)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, TOOL_LABEL, f"No se pudo consultar la base BCCA: {exc}")
            return
        if not candidates:
            QMessageBox.information(
                self, TOOL_LABEL,
                "No hay coincidencias por unidad. Prueba otra búsqueda; las partidas con otras unidades "
                "no se pueden aplicar directamente a un número de ensayos.",
            )
            return
        labels = [f"{item.code} — {item.summary} — {item.price:g} €/{item.unit} (BCCA 2023)"
                  for item in candidates]
        selected, accepted = QInputDialog.getItem(
            self, TOOL_LABEL, "Comprueba el alcance y selecciona una partida:", labels, 0, False
        )
        if not accepted:
            return
        concept = candidates[labels.index(selected)]
        price_cell = self.table.item(index, 6)
        replacement = "\n\nSe sustituirá el precio que ya figura en la fila." if price_cell.text().strip() else ""
        if QMessageBox.question(
            self, TOOL_LABEL,
            f"{concept.code} — {concept.summary}\n\n{concept.text}\n\n"
            f"Precio BCCA 2023: {concept.price:g} €/{concept.unit}.{replacement}\n\n"
            "La partida puede cubrir solo una parte del control; comprueba su alcance.\n\n"
            "¿Aplicar este precio al ensayo seleccionado?",
        ) != QMessageBox.StandardButton.Yes:
            return
        check = self.table.item(index, 0)
        row = dict(check.data(ROLE))
        row["price_source"] = {
            "code": concept.code, "summary": concept.summary, "unit": concept.unit,
            "price": str(concept.price), "trial_name": name, "source": "BCCA 2023",
        }
        check.setData(ROLE, row)
        price_cell.setText(str(concept.price))
        price_cell.setToolTip(f"{concept.code} — {concept.summary} · BCCA 2023")
        self.invalidate()

    def suggest(self) -> None:
        """Completa candidatos nuevos y actualiza referencias sin pisar valores revisados."""
        units = work_units(self.app.budget_items)
        saved = self.rows()
        existing = {(row["unit_id"], row.get("trial")) for row in saved}
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row in saved:
            if (item := units.get(row["unit_id"])) is not None:
                snapshot = unit_snapshot(item)
                self.append_row(row if snapshot == row["snapshot"] else row | {"snapshot": snapshot, "count": ""})
        for key, item in units.items():
            for trial in applicable_trials(item):
                if (key, trial) in existing:
                    continue
                specification = TRIALS.get(trial)
                if specification is not None:
                    row = {"name": specification.name, "frequency": specification.frequency,
                           "criteria": specification.criteria,
                           "count": suggested_quantity(item, trial) or ""}
                else:
                    entry = get_catalog_entry(trial)
                    row = {"name": entry.label, "frequency": entry.frequency, "criteria": entry.criteria,
                           "reference": entry.reference, "details": entry.details, "count": "",
                           "included": False}
                self.append_row({"unit_id": key, "snapshot": unit_snapshot(item), "trial": trial, **row})
        self.table.blockSignals(False)
        self.invalidate()

    def add_catalog(self) -> None:
        """Añade a la UO la ficha PAC o el ensayo elegido en el catálogo."""
        key = self.unit.currentData()
        item = work_units(self.app.budget_items).get(key)
        identifier = self.catalog.currentData()
        if item is None or not identifier:
            return
        if any(row["unit_id"] == key and row.get("trial") == identifier for row in self.rows()):
            QMessageBox.information(self, TOOL_LABEL, "Esta ficha ya está asociada a la unidad de obra.")
            return
        entry = get_catalog_entry(identifier)
        self.table.blockSignals(True)
        self.append_row({
            "unit_id": key, "snapshot": unit_snapshot(item), "trial": identifier,
            "name": entry.label, "frequency": entry.frequency, "criteria": entry.criteria,
            "reference": entry.reference, "details": entry.details, "count": "",
        })
        self.table.blockSignals(False)
        self.table.setCurrentCell(self.table.rowCount() - 1, 2)
        self.invalidate()

    def add_manual(self) -> None:
        """Permite planificar ensayos no detectados, en cualquier UO."""
        key = self.unit.currentData()
        item = work_units(self.app.budget_items).get(key)
        if item is not None:
            self.table.blockSignals(True)
            self.append_row({"unit_id": key, "snapshot": unit_snapshot(item)})
            self.table.blockSignals(False)
            self.table.setCurrentCell(self.table.rowCount() - 1, 2)
            self.invalidate()

    def remove_rows(self) -> None:
        """Retira de la revisión solo las filas seleccionadas."""
        for index in sorted({cell.row() for cell in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(index)
        self.invalidate()

    def invalidate(self, *_args) -> None:
        """Requiere una revisión nueva cuando cambia una estimación."""
        self.reviewed = False
        self.refresh_actions()

    def refresh_actions(self) -> None:
        """Sincroniza destinos, vigencia de la revisión y botones de inserción."""
        units = work_units(self.app.budget_items)
        if [self.unit.itemData(index) for index in range(self.unit.count())] != list(units):
            previous = self.unit.currentData()
            self.unit.clear()
            for key, item in units.items():
                self.unit.addItem(f"{item.code} — {item.summary}", key)
            index = self.unit.findData(previous)
            if index >= 0:
                self.unit.setCurrentIndex(index)
        inserted = has_inserted(self.app.budget_items)
        self.suggest_button.setEnabled(not inserted)
        self.add_button.setEnabled(not inserted and bool(units))
        self.add_catalog_button.setEnabled(not inserted and bool(units) and self.catalog.count() > 0)
        self.remove_button.setEnabled(not inserted)
        self.price_button.setEnabled(not inserted and bool(units))
        self.review_button.setEnabled(not inserted)
        self.table.setEnabled(not inserted)
        approved = [row for row in self.rows() if row["included"]]
        current = self.reviewed and bool(approved) and all(
            row["unit_id"] in units and row["snapshot"] == unit_snapshot(units[row["unit_id"]])
            for row in approved
        )
        self.export_button.setEnabled(current)
        self.insert_button.setEnabled(not inserted and current and all(row["price"].strip() for row in approved))
        self.status.setText("Ensayos ya incorporados; guarda el proyecto para conservarlos." if inserted else
                            "Plan revisado." if current else "Pendiente de revisión.")
        if current and not inserted and not self.insert_button.isEnabled():
            self.status.setText("Plan revisado. Completa los precios y confirma de nuevo para insertar.")

    def review(self) -> None:
        """Guarda la revisión únicamente tras validar las filas incluidas."""
        rows = self.rows()
        try:
            validate_rows([row for row in rows if row["included"]], work_units(self.app.budget_items))
        except ValueError as exc:
            QMessageBox.warning(self, TOOL_LABEL, str(exc))
            return
        answer = QMessageBox.question(
            self, TOOL_LABEL,
            "¿Has comprobado que los ensayos, cantidades y criterios de aceptación se ajustan al proyecto y su pliego?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.app.quality_assurance_state = {"rows": rows}
            self.reviewed = True
            self.app._mark_dirty()
            self.refresh_actions()

    def insert(self) -> None:
        """Incorpora exclusivamente la revisión vigente y con precios."""
        if not self.insert_button.isEnabled():
            return
        try:
            insert_budget(self.app, [row for row in self.rows() if row["included"]])
        except ValueError as exc:
            QMessageBox.warning(self, TOOL_LABEL, str(exc))

    def open_library(self) -> None:
        """Abre las fichas editables con registro de revisiones."""
        if self.editor is not None and self.editor.isVisible():
            self.editor.raise_()
            return
        try:
            self.editor = LibraryEditorQt(
                self, shared_library_path(self.app, "quality_assurance", LIBRARY_PATH),
                "Fichas del plan de aseguramiento y control de la calidad",
                used_members=set(DOCUMENTS), track_changes=True,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, TOOL_LABEL, str(exc))
            return
        self.editor.show()

    def export(self) -> None:
        """Escribe el anejo sin sustituir el destino si falla su generación."""
        if not self.export_button.isEnabled():
            return
        if self.editor is not None and self.editor.dirty:
            QMessageBox.warning(self, TOOL_LABEL, "Guarda o descarta la edición de la ficha antes de exportar.")
            return
        selection = get_document_save_path(
            self, "Generar anejo", "Plan de control de calidad.pdf",
            document_section="DOC01",
            annex_tool="quality",
        )
        if selection is None:
            return
        target, _output_format = selection
        try:
            with tempfile.TemporaryDirectory(dir=target.parent) as folder:
                pending = Path(folder) / target.name
                write_document(self.app, pending, [row for row in self.rows() if row["included"]])
                pending.replace(target)
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.warning(self, TOOL_LABEL, str(exc))
            return
        QMessageBox.information(self, TOOL_LABEL, f"Anejo guardado:\n{target}")

    def closeEvent(self, event) -> None:
        """Respeta la protección de cambios pendientes del editor de fichas."""
        if self.editor is not None and self.editor.isVisible() and not self.editor.close():
            event.ignore()
            return
        super().closeEvent(event)
