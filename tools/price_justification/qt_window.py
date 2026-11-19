"""Ventana PySide6 de justificación de precios."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

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
from tools._library_editor_qt import LibraryEditorQt
from collaborative_storage import shared_library_path


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
        self.resize(1120, 760)
        layout = QVBoxLayout(self)
        title = QLabel(TITLE)
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(QLabel(
            "Completa los datos específicos. Los textos base se editan desde "
            "la biblioteca de documentos."
        ))
        self.general_info = QLabel()
        self.general_info.setWordWrap(True)
        layout.addWidget(self.general_info)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
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
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Datos comunes: Archivo → Configuración…"))
        bottom.addStretch()
        library = QPushButton("Editar biblioteca de documentos…")
        library.clicked.connect(self.open_library_editor)
        bottom.addWidget(library)
        export = QPushButton("Guardar anejo…")
        export.clicked.connect(self.export)
        bottom.addWidget(export)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.close)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self._loading = False
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
            output_format = self.app.report_settings.output_format
            if output_format not in {"pdf", "odt"}:
                raise ValueError(f"Formato de informe no admitido: {output_format}")
            target, _ = QFileDialog.getSaveFileName(
                self,
                "Guardar justificación de precios",
                f"Justificación de precios.{output_format}",
                f"{output_format.upper()} (*.{output_format})",
            )
            if not target:
                return
            write_document(
                self.app,
                Path(target).with_suffix(f".{output_format}"),
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
