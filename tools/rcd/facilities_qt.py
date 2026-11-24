"""Selección por proyecto y mantenimiento del catálogo local de centros RCDs."""
from __future__ import annotations

from dataclasses import asdict
import csv
from pathlib import Path
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QComboBox, QFileDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .facilities import (
    FACILITY_FIELDS, FacilityCatalog, WasteFacility, facilities_from_data, search_key,
    haversine_km, municipality_coordinates,
)


class FacilityDialog(QDialog):
    """Edita todos los datos de una instalación sin exigir inventar los desconocidos."""

    def __init__(self, parent, facility: WasteFacility | None = None):
        """Construye la ficha conservando su identificador al editar."""
        super().__init__(parent)
        self.setWindowTitle("Editar centro de gestión" if facility else "Añadir centro de gestión")
        self.resize(650, 340)
        self.facility = facility or WasteFacility("")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for key, label in FACILITY_FIELDS.items():
            field = QLineEdit(getattr(self.facility, key))
            field.setAccessibleName(label)
            if key == "nima":
                field.setPlaceholderText("Diez dígitos; vacío si está pendiente")
            self.fields[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        note = QLabel(
            "Comprueba los LER y operaciones autorizados antes de contratar. "
            "Los datos sin completar se identificarán como PENDIENTE en el informe."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        """Valida antes de cerrar para conservar las ediciones si falta algún dato."""
        try:
            self.facility = WasteFacility.from_dict({
                "id": self.facility.id, **{key: field.text() for key, field in self.fields.items()},
            })
        except ValueError as exc:
            QMessageBox.warning(self, "Centro de gestión", str(exc))
            return
        super().accept()


class FacilitiesTab(QWidget):
    """Filtra el catálogo y guarda copias independientes de los centros marcados."""

    def __init__(self, owner):
        """Carga el catálogo de usuario y recupera los centros propios del proyecto."""
        super().__init__(owner)
        self.owner = owner
        self.catalog = FacilityCatalog(
            owner.app.recent_reference_bases_path.with_name("rcd_centros_gestion.sqlite3"),
        )
        self.entries: dict[str, WasteFacility] = {}
        self.rows: dict[str, WasteFacility] = {}
        self.available = False
        layout = QVBoxLayout(self)
        note = QLabel(
            "Marca los centros previstos para incluirlos en el apartado 1.8. "
            "El catálogo es local y reutilizable entre proyectos; comienza vacío. "
            "Editar una ficha actualiza el catálogo y, si está marcada, este proyecto, "
            "sin modificar otros proyectos guardados. La selección no acredita la autorización del centro."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por instalación, titular, dirección, provincia, NIMA, LER u operación…")
        self.search.setAccessibleName("Filtrar centros de gestión")
        self.search.textChanged.connect(self._populate)
        actions.addWidget(self.search, 1)
        self.selected_only = QCheckBox("Solo centros del proyecto")
        self.selected_only.toggled.connect(self._populate)
        actions.addWidget(self.selected_only)
        actions.addWidget(QLabel("Radio:"))
        self.distance_limit = QComboBox()
        self.distance_limit.addItem("Sin límite", 0)
        for kilometers in (25, 50, 100, 150, 250, 500):
            self.distance_limit.addItem(f"{kilometers} km", kilometers)
        self.distance_limit.currentIndexChanged.connect(self._populate)
        actions.addWidget(self.distance_limit)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 2 + len(FACILITY_FIELDS))
        self.table.setObjectName("rcdFacilitiesTable")
        table_headers = tuple(
            "Códigos LER" if key == "province_access" else label
            for key, label in FACILITY_FIELDS.items()
        )
        self.table.setHorizontalHeaderLabels(("En proyecto", *table_headers, "Distancia (km)"))
        for column, header in enumerate(("En proyecto", *table_headers, "Distancia (km)")):
            item = self.table.horizontalHeaderItem(column)
            if item is not None:
                item.setToolTip(header)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        for column, width in enumerate((90, 220, 180, 230, 190, 110, 220, 110)):
            self.table.setColumnWidth(column, width)
        self.table.itemChanged.connect(self._selection_changed)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.doubleClicked.connect(lambda _index: self._edit())
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.add_button = QPushButton("Añadir centro…")
        self.add_button.clicked.connect(lambda: self._edit(new=True))
        self.edit_button = QPushButton("Editar centro…")
        self.edit_button.clicked.connect(lambda: self._edit())
        import_button = QPushButton("Importar CSV…")
        import_button.clicked.connect(self.import_csv)
        reload_button = QPushButton("Actualizar catálogo")
        reload_button.clicked.connect(self.reload)
        for button in (self.add_button, self.edit_button, import_button, reload_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.reload()

    def reload(self):
        """Recarga sin sustituir las copias guardadas en el proyecto."""
        try:
            entries = self.catalog.load()
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.available = False
            self.entries = {}
            QMessageBox.critical(self, "No se pudo leer el catálogo de centros", str(exc))
        else:
            self.entries = {entry.id: entry for entry in entries}
            self.available = True
        self._populate()

    def import_csv(self):
        """Importa el CSV público de centros y conserva las fichas seleccionadas."""
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Importar catálogo de centros RCD",
            "",
            "CSV de centros (*.csv)",
        )
        if not path:
            return
        try:
            imported = self.catalog.import_csv(Path(path), replace=True)
        except (OSError, csv.Error, sqlite3.Error, ValueError) as exc:
            QMessageBox.critical(self, "No se pudo importar el catálogo", str(exc))
            return
        QMessageBox.information(
            self,
            "Catálogo RCD importado",
            f"Se incorporaron {imported} centros al catálogo local.",
        )
        self.reload()

    def _current(self) -> WasteFacility | None:
        """Obtiene la ficha de la fila activa sin depender del filtro."""
        item = self.table.item(self.table.currentRow(), 0)
        return self.rows.get(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _update_actions(self):
        """Evita editar un catálogo ilegible o una fila inexistente."""
        self.add_button.setEnabled(self.available)
        self.edit_button.setEnabled(self.available and self._current() is not None)

    def _populate(self):
        """Aplica filtros sin perder las marcas de los centros ocultos."""
        selected = {facility.id: facility for facility in self.owner.data.management.facilities}
        self.rows = {**self.entries, **selected}
        terms = search_key(self.search.text()).split()
        origin_points = []
        try:
            municipalities = self.owner.app.report_settings.municipalities
        except ValueError:
            municipalities = ()
        for municipality in municipalities:
            point = municipality_coordinates(
                list(self.entries.values()), municipality.name, municipality.province,
            )
            if point is not None:
                origin_points.append(point)
        origin = (
            (
                sum(point[0] for point in origin_points) / len(origin_points),
                sum(point[1] for point in origin_points) / len(origin_points),
            )
            if origin_points else None
        )
        radius = int(self.distance_limit.currentData() or 0)
        visible = []
        for facility in self.rows.values():
            if self.selected_only.isChecked() and facility.id not in selected:
                continue
            haystack = search_key(
                " ".join(
                    getattr(facility, key) for key in (*FACILITY_FIELDS, "ler_codes", "operations")
                )
            )
            if not all(term in haystack for term in terms):
                continue
            distance = None
            if origin is not None and facility.latitude and facility.longitude:
                distance = haversine_km(
                    origin[0], origin[1],
                    float(facility.latitude), float(facility.longitude),
                )
                if radius and distance > radius:
                    continue
            visible.append((distance if distance is not None else float("inf"), facility))
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for distance, facility in sorted(
            visible,
            key=lambda row: (
                row[0],
                search_key(row[1].name),
                row[1].id,
            ) if origin is not None else (
                search_key(row[1].name),
                row[1].id,
            ),
        ):
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setData(Qt.ItemDataRole.UserRole, facility.id)
            check.setCheckState(Qt.CheckState.Checked if facility.id in selected else Qt.CheckState.Unchecked)
            check.setToolTip(
                "Incluido en el proyecto" if facility.id in selected
                else "No incluido en el proyecto"
            )
            self.table.setItem(row, 0, check)
            for column, key in enumerate(FACILITY_FIELDS, 1):
                value = facility.ler_codes if key == "province_access" else getattr(facility, key)
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(row, column, cell)
            distance_text = "" if distance == float("inf") else f"{distance:.1f}"
            distance_cell = QTableWidgetItem(distance_text)
            distance_cell.setToolTip(distance_text)
            self.table.setItem(row, 1 + len(FACILITY_FIELDS), distance_cell)
        self.table.blockSignals(False)
        self.status.setText(
            (
                f"{len(selected)} centros en el proyecto; {self.table.rowCount()} visibles."
                + ("" if origin is not None else " Localización de obra sin coordenadas disponibles.")
            ) if self.available else
            "Catálogo no disponible. Las copias del proyecto se conservan; corrige el error y actualiza."
        )
        self._update_actions()

    def _selection_changed(self, item: QTableWidgetItem):
        """Incluye o retira la copia del proyecto sin borrar fichas del catálogo."""
        if item.column() != 0:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        selected = [facility for facility in self.owner.data.management.facilities
                    if facility.id != identifier]
        if item.checkState() == Qt.CheckState.Checked:
            selected.append(self.rows[identifier])
        try:
            selected = facilities_from_data([asdict(facility) for facility in selected])
        except ValueError as exc:
            QMessageBox.warning(self, "Centros de gestión", str(exc))
        else:
            self.owner.data.management.facilities = selected
            self.owner.changed()
        self._populate()

    def _edit(self, *, new=False):
        """Guarda la ficha y su selección solo cuando se confirma la transacción."""
        if not self.available:
            return
        current = None if new else self._current()
        if not new and current is None:
            return
        dialog = FacilityDialog(self, current)
        expected = self.entries.get(current.id) if current else None
        try:
            while dialog.exec() == QDialog.DialogCode.Accepted:
                facility = dialog.facility
                selected = self.owner.data.management.facilities
                included = new or any(row.id == facility.id for row in selected)
                candidate = [row for row in selected if row.id != facility.id]
                if included:
                    candidate.append(facility)
                try:
                    candidate = facilities_from_data([asdict(row) for row in candidate])
                    self.catalog.save(facility, expected=expected)
                except (OSError, sqlite3.Error, ValueError) as exc:
                    QMessageBox.warning(self, "No se pudo guardar el centro", str(exc))
                    continue
                if included:
                    self.owner.data.management.facilities = candidate
                    self.owner.changed()
                self.reload()
                break
        finally:
            dialog.deleteLater()
