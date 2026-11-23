"""Edición de previsiones de gestión y destinos particulares por LER."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from tools._shared.budget_sources import RCD_BUDGET_SOURCE
from .management import DESTINATIONS, PREVENTION, REUSE_DESTINATIONS, SELECTIONS, selected_wastes


class PreventionTab(QWidget):
    """Permite revisar las medidas generales y activar las específicas de la obra."""

    def __init__(self, owner):
        """Agrupa las previsiones en las cuatro fases operativas del estudio."""
        super().__init__(owner)
        self.owner = owner
        self.checkboxes: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        note = QLabel(
            "El artículo 4.1.a), apartado 2.º, del Real Decreto 105/2008 exige medidas de prevención "
            "para la obra. Se aplica la jerarquía de la Ley 7/2022 y, en Andalucía, el Decreto 73/2012 "
            "en lo que corresponda. Las medidas generales vienen marcadas inicialmente: revísalas "
            "y activa las específicas que procedan. Desmarcar una medida no exime de obligaciones "
            "legales. Solo las seleccionadas se incorporan al apartado 1.9 del estudio."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        sections = QVBoxLayout(content)
        for number, (title, options) in enumerate(PREVENTION.values(), 1):
            box = QGroupBox(f"{number}. {title}")
            box_layout = QVBoxLayout(box)
            for key, (label, text, general) in options.items():
                row = QHBoxLayout()
                checkbox = QCheckBox()
                checkbox.setAccessibleName(label)
                checkbox.setChecked(key in owner.data.management.prevention)
                checkbox.setToolTip("Medida general" if general else "Medida específica: activar si procede")
                checkbox.toggled.connect(self._selection_changed)
                self.checkboxes[key] = checkbox
                description = QLabel(f"{label}: {text}")
                description.setWordWrap(True)
                description.setBuddy(checkbox)
                row.addWidget(checkbox)
                row.addWidget(description, 1)
                box_layout.addLayout(row)
            sections.addWidget(box)
        sections.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)

    def _selection_changed(self, _checked):
        """Conserva también la decisión explícita de desmarcar todas las medidas."""
        self.owner.data.management.prevention = [
            key for key, checkbox in self.checkboxes.items() if checkbox.isChecked()
        ]
        self.owner.changed()


class ManagementTab(QWidget):
    """Publica cambios en el mismo estado de proyecto que el resto de RCDs."""

    def __init__(self, owner):
        """Construye selecciones múltiples y una fila por LER revisado."""
        super().__init__(owner)
        self.owner = owner
        self.checkboxes: dict[tuple[str, str], QCheckBox] = {}
        self.reuse_destinations: dict[str, QComboBox] = {}
        self.details: dict[str, QLineEdit] = {}
        layout = QVBoxLayout(self)
        note = QLabel(
            "Las elecciones se incorporan al estudio. No se asigna vertedero por defecto. "
            "Los umbrales del RD 105/2008 no eximen de las separaciones obligatorias de la Ley 7/2022. "
            "Las operaciones R se refieren a su anexo II y requieren el régimen y autorizaciones "
            "aplicables. Tipo y nivel siguen la clasificación del modelo; revisa el LER y su peligrosidad."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        sections = QVBoxLayout(content)
        plan = owner.data.management
        titles = {
            "segregation": "Previsión de segregación",
            "reuse": "Medidas de reutilización",
            "recovery": "Operaciones de valorización en la obra",
        }
        hints = {
            "segregation": "Justificación de la fracción mezclada y sus condiciones de tratamiento",
            "reuse": "Otros: especificar la reutilización",
            "recovery": "Otros: especificar la operación de valorización",
        }
        for section, options in SELECTIONS.items():
            box = QGroupBox(titles[section])
            box_layout = QVBoxLayout(box)
            for key, text in options.items():
                row = QHBoxLayout()
                checkbox = QCheckBox()
                checkbox.setAccessibleName(text)
                checkbox.setChecked(key in plan.selections[section])
                self.checkboxes[section, key] = checkbox
                label = QLabel(text)
                label.setWordWrap(True)
                label.setBuddy(checkbox)
                row.addWidget(checkbox)
                row.addWidget(label, 1)
                if section == "reuse" and key != "none":
                    destination = QComboBox()
                    destination.setEditable(True)
                    destination.addItems(("", *REUSE_DESTINATIONS))
                    destination.setCurrentText(plan.reuse_destinations.get(key, ""))
                    destination.setAccessibleName(f"Destino inicial: {text}")
                    destination.setMinimumWidth(210)
                    destination.setEnabled(checkbox.isChecked())
                    destination.currentTextChanged.connect(
                        lambda value, operation=key: self._reuse_destination_changed(operation, value)
                    )
                    self.reuse_destinations[key] = destination
                    row.addWidget(destination)
                checkbox.toggled.connect(
                    lambda checked, group=section, option=key: self._selection_changed(group, option, checked)
                )
                box_layout.addLayout(row)
            form = QFormLayout()
            detail = QLineEdit(plan.details.get(section, ""))
            detail.setAccessibleName(hints[section])
            detail.textChanged.connect(
                lambda value, group=section: self._detail_changed(group, value)
            )
            self.details[section] = detail
            form.addRow(hints[section], detail)
            box_layout.addLayout(form)
            sections.addWidget(box)
        box = QGroupBox("Destino de los residuos seleccionados")
        box_layout = QVBoxLayout(box)
        self.destination_table = QTableWidget(0, 7)
        self.destination_table.setObjectName("rcdDestinationsTable")
        self.destination_table.setHorizontalHeaderLabels((
            "Código LER", "Residuo", "Tipo", "Nivel", "Destino previsto",
            "Gestor / instalación", "Detalle / Otro destino",
        ))
        self.destination_table.setAlternatingRowColors(True)
        self.destination_table.verticalHeader().hide()
        self.destination_table.verticalHeader().setDefaultSectionSize(38)
        for column, width in enumerate((100, 240, 180, 85, 300, 210, 230)):
            self.destination_table.setColumnWidth(column, width)
        self.destination_table.setMinimumHeight(260)
        box_layout.addWidget(self.destination_table)
        sections.addWidget(box)
        sections.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.refresh_destinations()

    def _selection_changed(self, section, key, checked):
        """Impide combinar ausencia de previsión y operaciones concretas."""
        if checked and section in ("reuse", "recovery"):
            for (group, option), checkbox in self.checkboxes.items():
                if group == section and option != key and (key == "none" or option == "none"):
                    checkbox.blockSignals(True)
                    checkbox.setChecked(False)
                    checkbox.blockSignals(False)
        self.owner.data.management.selections[section] = [
            option for option in SELECTIONS[section]
            if self.checkboxes[section, option].isChecked()
        ]
        if section == "reuse":
            for option, destination in self.reuse_destinations.items():
                destination.setEnabled(self.checkboxes[section, option].isChecked())
        self.owner.changed()

    def _detail_changed(self, section, value):
        """Conserva las precisiones para operaciones mixtas u otras."""
        self.owner.data.management.details[section] = value
        self.owner.changed()

    def _reuse_destination_changed(self, operation, value):
        """Guarda el destino inicial sin convertirlo en una autorización."""
        self.owner.data.management.reuse_destinations[operation] = value
        self.owner.changed()

    def _destination_changed(self, code, field, value):
        """Actualiza únicamente la decisión del LER editado."""
        record = self.owner.data.management.destinations.setdefault(code, {})
        record[field] = value
        self.owner.changed()

    def refresh_destinations(self):
        """Refleja las selecciones actuales sin borrar decisiones de LER desmarcados."""
        excluded = {
            item.code for item in self.owner.app.budget_items if item.source.startswith(RCD_BUDGET_SOURCE)
        }
        rows = selected_wastes(self.owner.data, excluded)
        self.destination_table.setRowCount(0)
        for row, values in enumerate(rows):
            code = values[0]
            record = self.owner.data.management.destinations.get(code, {})
            self.destination_table.insertRow(row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.destination_table.setItem(row, column, item)
            destination = QComboBox()
            destination.addItem("Seleccionar destino…", "")
            for key, label in DESTINATIONS.items():
                destination.addItem(label, key)
            destination.setCurrentIndex(destination.findData(record.get("destination", "")))
            destination.currentIndexChanged.connect(
                lambda _index, ler=code, combo=destination:
                self._destination_changed(ler, "destination", combo.currentData())
            )
            self.destination_table.setCellWidget(row, 4, destination)
            for column, field in ((5, "facility"), (6, "details")):
                editor = QLineEdit(record.get(field, ""))
                editor.textChanged.connect(
                    lambda value, ler=code, name=field: self._destination_changed(ler, name, value)
                )
                self.destination_table.setCellWidget(row, column, editor)
