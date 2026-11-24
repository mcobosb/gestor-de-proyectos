"""Diálogos reutilizables de la interfaz PySide6 de presupuestos."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLineEdit,
    QVBoxLayout, QWidget,
)

from budget_core import MeasurementLine, ProjectItem, parse_float

class ItemEditor(QDialog):
    """Formulario para crear o editar una partida del presupuesto."""

    def __init__(
        self,
        parent: QWidget,
        item: ProjectItem | None = None,
        chapters: tuple[str, ...] = (),
    ) -> None:
        """Prepara los campos con los valores existentes o con una partida vacía."""
        super().__init__(parent)
        self.setWindowTitle("Editar partida" if item else "Añadir partida")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.chapter = QLineEdit(item.chapter if item else (chapters[-1] if chapters else "01#"))
        self.subchapter = QLineEdit(item.subchapter if item else "")
        self.code = QLineEdit(item.code if item else "")
        self.unit = QLineEdit(item.unit if item else "ud")
        self.summary = QLineEdit(item.summary if item else "")
        self.measurement = QDoubleSpinBox()
        self.measurement.setRange(-1_000_000_000, 1_000_000_000)
        self.measurement.setDecimals(4)
        self.measurement.setValue(item.measurement if item else 1.0)
        self.price = QDoubleSpinBox()
        self.price.setRange(-1_000_000_000, 1_000_000_000)
        self.price.setDecimals(4)
        self.price.setValue(item.unit_price if item else 0.0)
        self.rendimiento = QDoubleSpinBox()
        self.rendimiento.setRange(-1_000_000_000, 1_000_000_000)
        self.rendimiento.setDecimals(4)
        self.rendimiento.setValue(item.rendimiento if item else 0.0)
        for label, widget in (
            ("Capítulo", self.chapter),
            ("Subcapítulo", self.subchapter),
            ("Código", self.code),
            ("Unidad", self.unit),
            ("Resumen", self.summary),
            ("Medición", self.measurement),
            ("Precio", self.price),
            ("Rendimiento", self.rendimiento),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str | float]:
        """Devuelve los valores normalizados del formulario."""
        return {
            "chapter": self.chapter.text().strip(),
            "subchapter": self.subchapter.text().strip(),
            "code": self.code.text().strip(),
            "unit": self.unit.text().strip(),
            "summary": self.summary.text().strip(),
            "measurement": self.measurement.value(),
            "price": self.price.value(),
            "rendimiento": self.rendimiento.value(),
        }


class MeasurementEditor(QDialog):
    """Formulario de una línea de medición."""

    def __init__(self, parent: QWidget, line: MeasurementLine | None = None) -> None:
        """Carga una línea existente o valores vacíos."""
        super().__init__(parent)
        self.setWindowTitle("Editar medición" if line else "Añadir medición")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.comment = QLineEdit(line.comment if line else "")
        self.fields: dict[str, QLineEdit] = {}
        for name, label in (
            ("units", "Unidades"),
            ("length", "Longitud"),
            ("width", "Anchura"),
            ("height", "Altura"),
            ("k", "Coeficiente"),
        ):
            value = getattr(line, name, None) if line else None
            widget = QLineEdit("" if value is None else str(value))
            self.fields[name] = widget
            form.addRow(label, widget)
        form.insertRow(0, "Comentario", self.comment)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_line(self) -> MeasurementLine:
        """Convierte y valida los valores introducidos."""
        values: dict[str, float | None] = {}
        for name, widget in self.fields.items():
            text = widget.text().strip()
            if not text:
                if name == "units":
                    values[name] = None
                elif name == "k":
                    values[name] = 1.0
                else:
                    values[name] = None
                continue
            number = parse_float(text, float("nan"))
            if number != number:
                raise ValueError(f"'{text}' no es un número válido.")
            values[name] = number
        return MeasurementLine(
            comment=self.comment.text().strip(),
            units=values["units"],
            length=values["length"],
            width=values["width"],
            height=values["height"],
            k=float(values["k"] or 1.0),
        )


