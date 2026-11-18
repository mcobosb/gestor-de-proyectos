"""Asistente PySide6 de revisión y generación del PPTP."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tools._library_editor_qt import LibraryEditorQt
from .library import ZipLibrary
from .document import (
    DEFAULT_TOC_DEPTH,
    PENDING_DURATION,
    VARIABLES,
    Selection,
    compose,
    directory_codes,
    duration_price_revision,
    initial_variables,
    load_catalog,
    load_regulations,
    normalized_code,
    price_revision_fields,
    propose,
    regulation_matches,
    restore_state,
    une_designation,
    write_document,
)
from . import DOCS_ROOT, LIBRARY_PATH, TOOL_LABEL


class PptpWindowQt(QDialog):
    """Tres pasos Qt con estado intermedio aislado del proyecto."""

    def __init__(self, app) -> None:
        """Lee biblioteca, propuestas y valores guardados."""
        super().__init__(app)
        self.app = app
        self.library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        self.catalog = load_catalog(self.library)
        self.sheets = {sheet.path: sheet for sheet in self.catalog}
        self.saved = restore_state(app.pptp_state)
        self.automatic, self.unmatched = propose(
            app.budget_items, app.chapter_titles, self.catalog, self.saved
        )
        values, self.missing_plan = initial_variables(app)
        self.budget_snapshot = deepcopy(app.budget_items)
        self.variables = {
            key: QLineEdit(value) for key, value in values.items()
        }
        self.price_start = (
            app.planning_state.get("params", {}).get("start", "")
            if app.planning_state
            else self.saved.get("revision_inicio_planificacion", "")
        )
        self.regulations = []
        self.regulation_choices = dict(self.saved.get("regulations", {}))
        self.reviewed_unes = set(self.saved.get("une_revisadas", []))
        self.library_editor: LibraryEditorQt | None = None
        self.setWindowTitle("PPTP — Paso 1 de 3")
        self.resize(1160, 760)
        layout = QVBoxLayout(self)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._variables_page())
        self.pages.addWidget(self._automatic_page())
        self.pages.addWidget(self._manual_page())
        layout.addWidget(self.pages, 1)
        navigation = QHBoxLayout()
        self.back_button = QPushButton("Atrás")
        self.back_button.clicked.connect(self.back)
        navigation.addWidget(self.back_button)
        navigation.addStretch()
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        navigation.addWidget(cancel)
        self.next_button = QPushButton("Aceptar")
        self.next_button.clicked.connect(self.accept_step)
        navigation.addWidget(self.next_button)
        layout.addLayout(navigation)
        self.pages.setCurrentIndex(0)
        self._refresh_navigation()

    def _variables_page(self) -> QWidget:
        """Crea el primer paso con campos y revisión de precios."""
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        labels = {
            "titulo_proyecto": "Título del proyecto",
            "entidad": "Entidad",
            "plazo_garantia": "Plazo de garantía (con unidad)",
            "plazo_ejecucion": "Plazo de ejecución (con unidad)",
        }
        for key, label in labels.items():
            form.addRow(label, self.variables[key])
        self.variables["plazo_ejecucion"].textChanged.connect(
            self.update_price_revision
        )
        self.toc_depth = QComboBox()
        self.toc_depth.addItems(tuple(str(level) for level in range(1, 7)))
        self.toc_depth.setCurrentText(
            str(self.saved.get("toc_depth", DEFAULT_TOC_DEPTH))
        )
        form.addRow("Niveles del índice (1–6)", self.toc_depth)
        self.price_revision = QCheckBox("Aplicar revisión de precios")
        self.price_revision.setChecked(
            bool(self.saved.get("aplica_revision_precios", False))
        )
        self.price_revision.toggled.connect(self.update_price_revision)
        form.addRow(self.price_revision)
        self.justification = QLineEdit()
        form.addRow("Justificación", self.justification)
        form.addRow(
            "Fórmula de revisión de precios",
            self.variables["FORMULA_REVISION_PRECIOS"],
        )
        layout.addLayout(form)
        if self.missing_plan:
            warning = QLabel(
                "No se ha realizado la planificación. Rellena manualmente el "
                "plazo de ejecución; no se asigna un plazo ficticio."
            )
            warning.setWordWrap(True)
            layout.addWidget(warning)
        layout.addWidget(QLabel("Resumen de las obras"))
        self.summary = QPlainTextEdit(
            self.variables["bullet_capitulos_obra"].text()
        )
        layout.addWidget(self.summary, 1)
        library = QPushButton("Editar biblioteca de fichas…")
        library.clicked.connect(self.open_library_editor)
        layout.addWidget(library, alignment=Qt.AlignmentFlag.AlignRight)
        self.update_price_revision()
        return page

    def _automatic_page(self) -> QWidget:
        """Crea la revisión de coincidencias por código."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(
            "Fichas encontradas por código. Desmarca las que no procedan."
        ))
        self.automatic_table = self._selection_table()
        self.automatic_table.itemChanged.connect(self._selection_changed)
        self.automatic_table.itemSelectionChanged.connect(
            self._show_automatic_detail
        )
        layout.addWidget(self.automatic_table, 1)
        self.automatic_detail = QPlainTextEdit()
        self.automatic_detail.setReadOnly(True)
        self.automatic_detail.setMaximumHeight(90)
        layout.addWidget(self.automatic_detail)
        self._refresh_automatic()
        return page

    def _manual_page(self) -> QWidget:
        """Crea asignación manual y normativa técnica legal."""
        page = QWidget()
        layout = QVBoxLayout(page)
        tabs = QTabWidget()
        manual = QWidget()
        manual_layout = QVBoxLayout(manual)
        manual_layout.addWidget(QLabel(
            "Selecciona una ficha en la columna correspondiente. "
            "Puedes añadir varias fichas a una partida."
        ))
        self.manual_table = QTableWidget(0, 3)
        self.manual_table.setHorizontalHeaderLabels(
            ("Partida", "Ficha del catálogo", "Incluir")
        )
        self.manual_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.manual_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.manual_table.itemChanged.connect(self._manual_changed)
        self.manual_table.itemSelectionChanged.connect(self._show_manual_detail)
        manual_layout.addWidget(self.manual_table, 1)
        self.manual_detail = QPlainTextEdit()
        self.manual_detail.setReadOnly(True)
        self.manual_detail.setMaximumHeight(90)
        manual_layout.addWidget(self.manual_detail)
        add = QPushButton("Añadir otra ficha a la partida")
        add.clicked.connect(self.add_sheet)
        manual_layout.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)
        legal = QWidget()
        legal_layout = QVBoxLayout(legal)
        legal_layout.addWidget(QLabel(
            "Confirma ámbito, vigencia y condiciones de cada referencia."
        ))
        self.legal_table = QTableWidget(0, 4)
        self.legal_table.setHorizontalHeaderLabels(
            ("Referencia", "Partidas", "Fichas", "Incluir")
        )
        self.legal_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.legal_table.itemChanged.connect(self._regulation_changed)
        self.legal_table.itemSelectionChanged.connect(self.show_regulation)
        legal_layout.addWidget(self.legal_table, 1)
        self.legal_detail = QPlainTextEdit()
        self.legal_detail.setReadOnly(True)
        self.legal_detail.setMaximumHeight(120)
        legal_layout.addWidget(self.legal_detail)
        tabs.addTab(manual, "Partidas sin coincidencia")
        tabs.addTab(legal, "Disposiciones técnicas legales")
        layout.addWidget(tabs)
        self._refresh_manual()
        self.update_regulations()
        return page

    def _selection_table(self) -> QTableWidget:
        """Crea una tabla de fichas con checkbox nativo."""
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(
            ("Partida del presupuesto", "Código y ficha", "Incluir")
        )
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        return table

    def _check_item(self, row, selected: bool) -> QTableWidgetItem:
        """Crea una casilla con el índice estable de la fila."""
        item = QTableWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, row)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
        )
        return item

    def _refresh_automatic(self) -> None:
        """Repinta coincidencias automáticas."""
        table = self.automatic_table
        table.blockSignals(True)
        table.setRowCount(0)
        for index, row in enumerate(self.automatic):
            sheet = self.sheets[row.path]
            table.insertRow(index)
            table.setItem(
                index, 0, QTableWidgetItem(f"{row.code} — {row.summary}")
            )
            table.setItem(
                index, 1, QTableWidgetItem(f"{sheet.code} — {sheet.title}")
            )
            table.setItem(index, 2, self._check_item(index, row.selected))
        table.blockSignals(False)

    def _refresh_manual(self) -> None:
        """Repinta asignaciones manuales con desplegable del catálogo."""
        if not hasattr(self, "manual_table"):
            return
        self.manual_table.blockSignals(True)
        self.manual_table.setRowCount(0)
        for index, row in enumerate(self.unmatched):
            self.manual_table.insertRow(index)
            self.manual_table.setItem(
                index, 0, QTableWidgetItem(f"{row.code} — {row.summary}")
            )
            selector = QComboBox()
            selector.addItem("Seleccionar ficha…", "")
            for sheet in self.catalog:
                selector.addItem(f"{sheet.code} — {sheet.title}", sheet.path)
            selector.setCurrentIndex(selector.findData(row.path))
            selector.currentIndexChanged.connect(
                lambda _index, row_index=index: self._sheet_selected(row_index)
            )
            self.manual_table.setCellWidget(index, 1, selector)
            self.manual_table.setItem(index, 2, self._check_item(index, row.selected))
        self.manual_table.blockSignals(False)

    def _sheet_selected(self, row: int) -> None:
        """Guarda la ruta estable elegida y actualiza normativa."""
        selector = self.manual_table.cellWidget(row, 1)
        if isinstance(selector, QComboBox):
            self.unmatched[row].path = str(selector.currentData() or "")
            self.update_regulations()

    def _selection_changed(self, item: QTableWidgetItem) -> None:
        """Alterna una coincidencia automática."""
        if item.column() == 2:
            self.automatic[int(item.data(Qt.ItemDataRole.UserRole))].selected = (
                item.checkState() == Qt.CheckState.Checked
            )

    def _manual_changed(self, item: QTableWidgetItem) -> None:
        """Alterna una asignación manual y recalcula relaciones."""
        if item.column() == 2:
            self.unmatched[int(item.data(Qt.ItemDataRole.UserRole))].selected = (
                item.checkState() == Qt.CheckState.Checked
            )
            self.update_regulations()

    def _show_automatic_detail(self) -> None:
        """Muestra ruta y aviso de una ficha automática."""
        self._show_sheet_detail(
            self.automatic_table, self.automatic, self.automatic_detail
        )

    def _show_manual_detail(self) -> None:
        """Muestra ruta y aviso de una asignación manual."""
        self._show_sheet_detail(
            self.manual_table, self.unmatched, self.manual_detail
        )

    def _show_sheet_detail(self, table, rows, detail) -> None:
        """Presenta datos técnicos de la fila activa."""
        row = table.currentRow()
        if 0 <= row < len(rows):
            sheet = self.sheets.get(rows[row].path)
            detail.setPlainText(
                f"{sheet.path}\n{sheet.warning}" if sheet else "Sin ficha asignada."
            )

    def add_sheet(self) -> None:
        """Añade otra asignación para la partida activa."""
        row = self.manual_table.currentRow()
        if row < 0:
            QMessageBox.information(
                self, TOOL_LABEL, "Selecciona primero una partida."
            )
            return
        source = self.unmatched[row]
        self.unmatched.append(
            Selection(
                source.item, source.code, source.chapter, source.summary, "",
                automatic=False,
            )
        )
        self._refresh_manual()
        self.manual_table.selectRow(len(self.unmatched) - 1)

    def update_regulations(self) -> None:
        """Recalcula normativa para fichas seleccionadas."""
        codes = set()
        relations: list[tuple[str, str, str]] = []
        for row in (*self.automatic, *self.unmatched):
            if row.selected and row.path in self.sheets:
                sheet = self.sheets[row.path]
                levels = directory_codes(sheet)
                code = normalized_code(row.code)
                code = (
                    code
                    if row.automatic and code.startswith(levels[0])
                    else sheet.code
                )
                codes.add(code)
                relations.append((
                    code,
                    f"{row.code} — {row.summary}",
                    f"{sheet.code} — {sheet.title}",
                ))
        self.regulations = load_regulations(self.library, codes)
        for ref in self.regulations:
            if une_designation(ref) and ref.id not in self.reviewed_unes:
                self.regulation_choices[ref.id] = ref.selected
            else:
                self.regulation_choices.setdefault(ref.id, ref.selected)
        self.regulation_relations = {
            ref.id: [
                (item, path)
                for code, item, path in relations
                if regulation_matches(ref, {code})
            ]
            for ref in self.regulations
        }
        self.refresh_regulations()

    def refresh_regulations(self) -> None:
        """Repinta decisiones normativas y UNE."""
        if not hasattr(self, "legal_table"):
            return
        self.legal_table.blockSignals(True)
        self.legal_table.setRowCount(0)
        for index, ref in enumerate(self.regulations):
            self.legal_table.insertRow(index)
            relations = self.regulation_relations[ref.id]
            for column, value in enumerate((
                ref.text[2:],
                "; ".join(dict.fromkeys(item for item, _ in relations)),
                "; ".join(dict.fromkeys(sheet for _, sheet in relations)),
            )):
                self.legal_table.setItem(index, column, QTableWidgetItem(value))
            self.legal_table.setItem(
                index, 3,
                self._check_item(index, self.regulation_choices[ref.id]),
            )
        self.legal_table.blockSignals(False)

    def _regulation_changed(self, item: QTableWidgetItem) -> None:
        """Confirma una referencia y conserva decisiones UNE."""
        if item.column() != 3:
            return
        index = int(item.data(Qt.ItemDataRole.UserRole))
        ref = self.regulations[index]
        value = item.checkState() == Qt.CheckState.Checked
        self.regulation_choices[ref.id] = value
        if une_designation(ref):
            self.reviewed_unes.add(ref.id)
        self.show_regulation()

    def show_regulation(self) -> None:
        """Muestra condición, relaciones y estado de inclusión."""
        row = self.legal_table.currentRow()
        if not 0 <= row < len(self.regulations):
            return
        ref = self.regulations[row]
        relations = "\n".join(
            f"• {item}\n  Ficha: {sheet}"
            for item, sheet in self.regulation_relations[ref.id]
        )
        status = (
            "Incluida en el pliego."
            if self.regulation_choices[ref.id]
            else "No incluida."
        )
        self.legal_detail.setPlainText(
            f"{ref.text}\n\n{ref.condition}\n\n{status}\n\n"
            f"Partidas y fichas relacionadas:\n{relations}"
        )

    def update_price_revision(self) -> None:
        """Calcula el motivo temporal sin activar revisión automáticamente."""
        values = {
            key: field.text() for key, field in self.variables.items()
        }
        applies, reason = duration_price_revision(
            values, self.price_revision.isChecked(), self.price_start
        )
        self.price_revision.blockSignals(True)
        self.price_revision.setChecked(applies)
        self.price_revision.blockSignals(False)
        self.justification.setText(
            self.variables[
                "JUSTIFICACION_APLICACION"
                if applies else "JUSTIFICACION_NO_APLICACION"
            ].text()
            if self.variables["JUSTIFICACION_APLICACION"].text()
            or self.variables["JUSTIFICACION_NO_APLICACION"].text()
            else reason
        )
        self.variables["FORMULA_REVISION_PRECIOS"].setEnabled(applies)

    def _sync_justification(self) -> None:
        """Guarda la justificación en la alternativa activa."""
        key = (
            "JUSTIFICACION_APLICACION"
            if self.price_revision.isChecked()
            else "JUSTIFICACION_NO_APLICACION"
        )
        self.variables[key].setText(self.justification.text())

    def open_library_editor(self) -> None:
        """Abre una única biblioteca PPTP con resaltado de fichas usadas."""
        used = {
            row.path
            for row in (*self.automatic, *self.unmatched)
            if row.selected and row.path in self.sheets
        }
        if self.library_editor is not None and self.library_editor.isVisible():
            self.library_editor.raise_()
            return
        self.library_editor = LibraryEditorQt(
            self,
            LIBRARY_PATH,
            "Biblioteca de fichas PPTP",
            used_members=used,
            on_saved=self.reload_library,
            track_changes=True,
        )
        self.library_editor.show()

    def reload_library(self) -> None:
        """Recarga catálogo y conserva las decisiones por ruta."""
        self.library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        self.catalog = load_catalog(self.library)
        self.sheets = {sheet.path: sheet for sheet in self.catalog}
        for row in (*self.automatic, *self.unmatched):
            row.path = self.library.resolve_sheet(row.path)
        self.automatic, self.unmatched = propose(
            self.budget_snapshot,
            self.app.chapter_titles,
            self.catalog,
            {"selections": [asdict(row) for row in (*self.automatic, *self.unmatched)]},
        )
        self._refresh_automatic()
        self._refresh_manual()
        self.update_regulations()

    def _refresh_navigation(self) -> None:
        """Actualiza título y botones según el paso."""
        step = self.pages.currentIndex()
        self.setWindowTitle(f"PPTP — Paso {step + 1} de 3")
        self.back_button.setEnabled(step > 0)
        self.next_button.setText("Aceptar y generar" if step == 2 else "Aceptar")

    def back(self) -> None:
        """Vuelve al paso anterior."""
        self._sync_variables()
        if self.pages.currentIndex():
            self.pages.setCurrentIndex(self.pages.currentIndex() - 1)
            self._refresh_navigation()

    def accept_step(self) -> None:
        """Valida cada paso y genera en el tercero."""
        try:
            if self.pages.currentIndex() == 0:
                self._sync_variables()
                self._validate_variables()
            if self.pages.currentIndex() < 2:
                self.pages.setCurrentIndex(self.pages.currentIndex() + 1)
                if self.pages.currentIndex() == 2:
                    self.update_regulations()
                self._refresh_navigation()
                return
            self.generate()
        except (ImportError, OSError, UnicodeError, ValueError) as exc:
            QMessageBox.critical(self, TOOL_LABEL, str(exc))

    def _sync_variables(self) -> None:
        """Copia los valores de texto al estado editable compartido."""
        self._sync_justification()
        self.variables["bullet_capitulos_obra"].setText(
            self.summary.toPlainText().strip()
        )

    def _validate_variables(self) -> None:
        """Exige los campos PPTP del primer paso."""
        for key in (*VARIABLES, *price_revision_fields(self.price_revision.isChecked())):
            value = self.variables[key].text().strip()
            if not value or value == PENDING_DURATION:
                raise ValueError(f"Rellena el campo {key} antes de continuar.")
        if self.toc_depth.currentText() not in {
            str(level) for level in range(1, 7)
        }:
            raise ValueError(
                "Los niveles del índice deben ser un entero entre 1 y 6."
            )

    def generate(self) -> None:
        """Persiste la selección, guarda el proyecto y genera el documento."""
        if self.app.budget_items != self.budget_snapshot:
            raise ValueError(
                "El presupuesto ha cambiado. Cierra y vuelve a abrir PPTP."
            )
        self.update_regulations()
        self._sync_variables()
        for row in (*self.automatic, *self.unmatched):
            if row.selected and not row.path:
                raise ValueError(
                    f"Asigna una ficha a {row.code} o desmarca su incorporación."
                )
            if row.path and row.path not in self.sheets:
                raise ValueError(
                    f"La ficha guardada de {row.code} ya no figura en el índice."
                )
        state = restore_state({
            "variables": {
                key: field.text().strip()
                for key, field in self.variables.items()
            },
            "selections": [
                asdict(row) for row in (*self.automatic, *self.unmatched)
            ],
            "regulations": self.regulation_choices,
            "toc_depth": int(self.toc_depth.currentText()),
            "aplica_revision_precios": self.price_revision.isChecked(),
            "revision_inicio_planificacion": self.price_start,
            "une_revisadas": sorted(self.reviewed_unes),
        })
        chapter_titles = {
            row["chapter"]: self.app._chapter_title(row["chapter"])
            for row in state["selections"] if row["selected"]
        }
        source = compose(
            self.library, state, self.regulations,
            budget_chapter_titles=chapter_titles,
        )
        extension = "." + self.app.report_settings.output_format
        target, _ = QFileDialog.getSaveFileName(
            self, "Generar PPTP", "PPTP" + extension,
            f"Documento {extension[1:].upper()} (*{extension})",
        )
        if not target:
            return
        self.app.pptp_state = state
        self.app.project_title = state["variables"]["titulo_proyecto"]
        self.app.report_settings.organization = state["variables"]["entidad"]
        self.app.report_settings.guarantee_period = state["variables"]["plazo_garantia"]
        self.app._mark_dirty()
        if not self.app.save_project():
            return
        path = Path(target).with_suffix(extension)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, suffix=extension, delete=False
            ) as output:
                temporary = Path(output.name)
            write_document(
                source, temporary, state["variables"],
                self.app.report_settings, toc_depth=state["toc_depth"],
            )
            temporary.replace(path)
            temporary = None
        except (ImportError, OSError, ValueError):
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        self.app._set_status(f"PPTP guardado en {path}")
        QMessageBox.information(
            self, TOOL_LABEL, f"Selección guardada y documento generado:\n{path}"
        )
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Protege ediciones abiertas de biblioteca."""
        if self.library_editor is not None and self.library_editor.isVisible():
            self.library_editor.close()
            if self.library_editor.isVisible():
                event.ignore()
                return
        event.accept()
