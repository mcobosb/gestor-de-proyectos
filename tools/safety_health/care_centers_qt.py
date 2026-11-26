"""Selección de centros asistenciales SICESS para el proyecto."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tools.rcd.facilities import search_key
from .care_centers import (
    geonames_municipality_points, load_sicess, municipality_key, rcd_municipality_points,
    restore_sicess_centers, sicess_distance,
)

CATALOG_PATH = Path(__file__).with_name("centros_SICESS.sicess.sqlite3")
GEONAMES_PATH = Path(__file__).with_name("datos") / "municipios_geonames.csv"


class SicessCentersTab(QWidget):
    """Filtra el registro y conserva copias independientes de los centros elegidos."""

    def __init__(self, owner):
        """Crea el selector y carga el catálogo local."""
        super().__init__(owner)
        self.owner = owner
        self.entries: dict[str, dict[str, str]] = {}
        self.points: dict[tuple[str, str], tuple[float, float]] = {}
        self.error = ""
        layout = QVBoxLayout(self)
        note = QLabel(
            "Marca los centros que deban aparecer en el estudio. Las distancias son aproximaciones "
            "entre municipios a partir de instalaciones RCD o GeoNames, no distancias al centro ni rutas. "
            "Comprueba su ubicación, disponibilidad y capacidad de atención urgente antes de utilizarlos."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrar por nombre, tipo, municipio, provincia o código postal…")
        self.search.setAccessibleName("Filtrar centros SICESS")
        self.search.textChanged.connect(self.populate)
        actions.addWidget(self.search, 1)
        self.selected_only = QCheckBox("Solo centros del proyecto")
        self.selected_only.toggled.connect(self.populate)
        actions.addWidget(self.selected_only)
        reload_button = QPushButton("Actualizar catálogo")
        reload_button.clicked.connect(self.reload)
        actions.addWidget(reload_button)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 7)
        self.table.setObjectName("sicessCentersTable")
        self.table.setHorizontalHeaderLabels(
            ("En proyecto", "Centro", "Tipo", "Municipio", "Provincia", "Teléfono", "Distancia aprox. (km)")
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        for column, width in enumerate((90, 250, 210, 150, 100, 110, 150)):
            self.table.setColumnWidth(column, width)
        self.table.itemChanged.connect(self.selection_changed)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.reload()

    def reload(self):
        """Recarga las fuentes sin alterar las copias guardadas en el proyecto."""
        errors = []
        try:
            self.entries = {row["url"]: row for row in load_sicess(CATALOG_PATH)}
        except ValueError as exc:
            self.entries = {}
            errors.append(str(exc))
        try:
            self.points = geonames_municipality_points(GEONAMES_PATH)
        except ValueError as exc:
            self.points = {}
            errors.append(str(exc))
        try:
            path = self.owner.app.recent_reference_bases_path.with_name("rcd_centros_gestion.sqlite3")
            self.points.update(rcd_municipality_points(path))
        except ValueError as exc:
            errors.append(str(exc))
        self.error = " ".join(errors)
        self.populate()

    def populate(self):
        """Ordena por proximidad cuando existe y conserva las selecciones ocultas."""
        selected = {
            row["url"]: row for row in restore_sicess_centers(
                self.owner.app.safety_health_state.get("centros_sicess_seleccionados", [])
            )
        }
        entries = {**self.entries, **selected}
        try:
            municipalities = self.owner.app.report_settings.municipalities
            location_error = ""
        except ValueError as exc:
            municipalities = ()
            location_error = str(exc)
        keys = {(municipality_key(item.name), search_key(item.province)) for item in municipalities}
        origins = [self.points[key] for key in keys if key in self.points]
        terms = search_key(self.search.text()).split()
        visible = []
        for row in entries.values():
            if self.selected_only.isChecked() and row["url"] not in selected:
                continue
            haystack = search_key(" ".join(row.values()))
            if not all(term in haystack for term in terms):
                continue
            distance = sicess_distance(row, origins, self.points)
            same = (municipality_key(row["municipio"]), search_key(row["provincia"])) in keys
            visible.append((row, distance, same))
        visible.sort(key=lambda result: (
            0 if result[2] else 1, result[1] if result[1] is not None else float("inf"),
            search_key(result[0]["nombre"]), result[0]["url"],
        ))
        self.table.blockSignals(True)
        self.table.setRowCount(len(visible))
        for index, (center, distance, _) in enumerate(visible):
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setData(Qt.ItemDataRole.UserRole, center["url"])
            check.setCheckState(Qt.CheckState.Checked if center["url"] in selected else Qt.CheckState.Unchecked)
            self.table.setItem(index, 0, check)
            for column, key in enumerate(("nombre", "tipo", "municipio", "provincia", "telefono"), 1):
                cell = QTableWidgetItem(center[key])
                cell.setToolTip(center[key])
                self.table.setItem(index, column, cell)
            self.table.setItem(index, 6, QTableWidgetItem(
                f"{distance:.0f}" if distance is not None else "Sin datos"
            ))
        self.table.blockSignals(False)
        status = f"{len(selected)} centros seleccionados; {len(visible)} visibles."
        if not origins:
            status += " Sin coordenadas para la localidad de la obra; se prioriza el mismo municipio."
        if location_error or self.error:
            status += f" {location_error} {self.error}"
        self.status.setText(status.strip())

    def selection_changed(self, item: QTableWidgetItem):
        """Guarda la ficha marcada sin modificar la base SICESS."""
        if item.column() != 0:
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        selected = restore_sicess_centers(self.owner.app.safety_health_state.get("centros_sicess_seleccionados", []))
        selected = [row for row in selected if row["url"] != identifier]
        if item.checkState() == Qt.CheckState.Checked:
            selected.append(self.entries[identifier])
        self.owner.commit_basic_state()
        self.owner.app.safety_health_state["centros_sicess_seleccionados"] = restore_sicess_centers(selected)
        self.owner.app._mark_dirty()
        self.populate()
