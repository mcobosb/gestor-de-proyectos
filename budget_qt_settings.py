"""Diálogo de configuración general y selectores de catálogos para PySide6."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QMessageBox,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from cpv_catalog import load_cpv_codes
from localization import LANGUAGES
from municipalities import load_municipalities, municipality_search_key
from budget_reports import ReportSettings
from budget_qt_theme import THEMES


@dataclass(frozen=True)
class QtCatalogEntry:
    """Entrada mostrable y validable en un selector de catálogo."""

    code: str
    label: str
    values: tuple[str, ...]
    aliases: tuple[str, ...] = ()


class CatalogSelector(QWidget):
    """Selector filtrable con tabla de selección múltiple."""

    def __init__(
        self,
        parent: QWidget,
        entries: tuple[QtCatalogEntry, ...],
        selected_codes: list[str],
        headers: tuple[str, ...],
        title: str,
        help_text: str,
        add_label: str,
    ) -> None:
        """Carga el catálogo y reconstruye la selección del proyecto."""
        super().__init__(parent)
        self.entries = entries
        self.by_code = {entry.code: entry for entry in entries}
        self._selected = list(dict.fromkeys(selected_codes))
        layout = QVBoxLayout(self)
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(title_label)
        search_layout = QHBoxLayout()
        self.query = QComboBox()
        self.query.setEditable(True)
        self.query.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.query.setMinimumContentsLength(45)
        self.query.editTextChanged.connect(self._filter)
        self.query.lineEdit().returnPressed.connect(self.add_entry)
        search_layout.addWidget(self.query, 1)
        add_button = QPushButton(add_label)
        add_button.clicked.connect(self.add_entry)
        search_layout.addWidget(add_button)
        layout.addLayout(search_layout)
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        remove_button = QPushButton("Quitar seleccionados")
        remove_button.clicked.connect(self.remove_selected)
        layout.addWidget(remove_button, alignment=Qt.AlignmentFlag.AlignRight)
        help_label = QLabel(help_text)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self._refresh_table()
        self._filter("")
        self.query.setCurrentIndex(-1)
        self.query.setEditText("")

    @property
    def codes(self) -> list[str]:
        """Devuelve los códigos en el orden de incorporación."""
        return list(self._selected)

    def _filter(self, text: str) -> None:
        """Limita el desplegable a las primeras cien coincidencias."""
        query = municipality_search_key(text)
        self.query.blockSignals(True)
        self.query.clear()
        if not query:
            self.query.setEditText("")
            self.query.setCurrentIndex(-1)
            self.query.blockSignals(False)
            return
        matches = []
        for entry in self.entries:
            aliases = tuple(
                municipality_search_key(value)
                for value in (entry.label, entry.code, *entry.aliases)
            )
            if query in aliases[0] or any(query in alias for alias in aliases[1:]):
                matches.append(entry)
                if len(matches) == 100:
                    break
        for entry in matches:
            self.query.addItem(entry.label, entry.code)
        if text:
            self.query.setEditText(text)
        self.query.blockSignals(False)

    def add_entry(self) -> bool:
        """Confirma una selección inequívoca y evita duplicados."""
        raw = self.query.currentText().strip()
        normalized = municipality_search_key(raw)
        matches = []
        for entry in self.entries:
            aliases = {
                municipality_search_key(value)
                for value in (entry.label, entry.code, *entry.aliases)
            }
            if normalized and normalized in aliases:
                matches.append(entry)
        if len(matches) != 1:
            QMessageBox.warning(
                self,
                "Configuración",
                "Selecciona una opción inequívoca del desplegable.",
            )
            return False
        entry = matches[0]
        if entry.code not in self._selected:
            self._selected.append(entry.code)
            self._refresh_table()
        self.query.setEditText("")
        self.query.setFocus()
        return True

    def remove_selected(self) -> None:
        """Quita de la selección las filas activas."""
        selected = {
            self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in range(self.table.rowCount())
            if self.table.item(row, 0) is not None and self.table.item(row, 0).isSelected()
        }
        if selected:
            self._selected = [code for code in self._selected if code not in selected]
            self._refresh_table()

    def _refresh_table(self) -> None:
        """Reconstruye las filas seleccionadas desde sus códigos."""
        self.table.setRowCount(0)
        for code in self._selected:
            entry = self.by_code[code]
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, value in enumerate(entry.values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, code)
                self.table.setItem(row, column, item)

    def confirm_pending(self) -> bool:
        """Convierte texto escrito en una selección antes de guardar."""
        return not self.query.currentText().strip() or self.add_entry()


class SettingsDialog(QDialog):
    """Configuración de informes, idioma, emplazamiento y códigos CPV."""

    def __init__(
        self,
        parent: QWidget,
        settings: ReportSettings,
        project_title: str,
        language: str,
        save_language: Callable[[str], bool],
        theme: str = "system",
        save_theme: Callable[[str], bool] | None = None,
    ) -> None:
        """Construye todas las pestañas y carga el estado vigente."""
        super().__init__(parent)
        self.setWindowTitle("Configuración de informes")
        self.resize(900, 760)
        self.settings = settings
        self.project_title = project_title
        self.language = language
        self.save_language = save_language
        self.theme = theme
        self.save_theme = save_theme
        self.report_fields: dict[str, QLineEdit | QPlainTextEdit] = {}
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._reports_tab(), "Informes")
        self.tabs.addTab(self._language_tab(), "Idioma")
        self.tabs.addTab(self._appearance_tab(), "Apariencia")
        self.tabs.addTab(self._collaboration_tab(), "Colaboración")
        self.tabs.addTab(self._location_tab(), "Emplazamiento")
        self.tabs.addTab(self._cpv_tab(), "CPV")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept_reports)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reports_tab(self) -> QWidget:
        """Crea los campos generales y económicos de los informes."""
        content = QWidget()
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.project_title_field = QLineEdit(self.project_title)
        form.addRow("Título del proyecto", self.project_title_field)
        values = self.settings.to_dict()
        fields = (
            ("Expediente", "expediente"),
            ("Autoría / nombre", "author"),
            ("Cargo / titulación", "role"),
            ("Entidad / servicio", "organization"),
            ("Localidad", "location"),
            ("Plazo de garantía (con unidad)", "guarantee_period"),
            ("Texto de firma / fecha", "signature"),
            ("Empresa constructora", "contractor"),
            ("Responsable de la empresa constructora", "contractor_representative"),
            ("Costes indirectos (%)", "indirect_costs"),
            ("Gastos generales (%)", "general_expenses"),
            ("Beneficio industrial (%)", "industrial_profit"),
            ("IVA (%)", "vat"),
            ("Baja de adjudicación (%)", "tender_discount"),
            ("Seguridad y salud orientativa (%)", "safety_health_percentage"),
        )
        for label, key in fields:
            if key == "author":
                authors = QPlainTextEdit(str(values[key]))
                authors.setPlaceholderText("Un nombre por línea")
                authors.setAccessibleName(label)
                authors.setTabChangesFocus(True)
                authors.setFixedHeight(authors.fontMetrics().lineSpacing() * 3 + 12)
                self.report_fields[key] = authors
                self.show_author = QCheckBox("Mostrar en los documentos")
                self.show_author.setChecked(self.settings.show_author)
                row = QHBoxLayout()
                row.addWidget(authors, 1)
                row.addWidget(self.show_author)
                form.addRow(label, row)
            else:
                field = QLineEdit(str(values[key]))
                self.report_fields[key] = field
                form.addRow(label, field)
        self.output_format = QComboBox()
        self.output_format.addItems(("PDF", "ODT"))
        self.output_format.setCurrentText(str(values["output_format"]).upper())
        form.addRow("Formato de informes", self.output_format)
        currency_options = (
            ("EUR", "EUR (€)"),
            ("USD", "USD ($)"),
            ("GBP", "GBP (£)"),
            ("CHF", "CHF"),
            ("CAD", "CAD (CA$)"),
            ("AUD", "AUD (A$)"),
            ("OTHER", "Otra (personalizada)"),
        )
        self.currency = QComboBox()
        for code, label in currency_options:
            self.currency.addItem(label, code)
        index = self.currency.findData(str(values["currency"]))
        self.currency.setCurrentIndex(index)
        self.custom_currency = QLineEdit(str(values["custom_currency"]))
        form.addRow("Divisa del proyecto", self.currency)
        form.addRow("Símbolo o código personalizado", self.custom_currency)
        self.currency.currentIndexChanged.connect(self._currency_changed)
        self._currency_changed()
        self.template_path = QLineEdit(str(values["template_path"]))
        browse = QPushButton("Examinar…")
        browse.clicked.connect(self._select_template)
        template_layout = QHBoxLayout()
        template_layout.addWidget(self.template_path)
        template_layout.addWidget(browse)
        form.addRow("Plantilla de cabecera y pie (ODT)", template_layout)
        explanation = QLabel(
            "La plantilla aporta cabecera y pie. Déjala vacía para usar la incluida. "
            "GG y BI se aplican al PEM; en certificaciones la baja se deduce antes "
            "del IVA. La divisa modifica la presentación, no convierte importes."
        )
        explanation.setWordWrap(True)
        form.addRow(explanation)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _language_tab(self) -> QWidget:
        """Crea la preferencia global de idioma."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.language_selector = QComboBox()
        for code, label in LANGUAGES.items():
            self.language_selector.addItem(label, code)
        self.language_selector.setCurrentIndex(self.language_selector.findData(self.language))
        form.addRow("Idioma", self.language_selector)
        layout.addLayout(form)
        note = QLabel(
            "El idioma se guarda como preferencia global y se aplica inmediatamente."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        save = QPushButton("Guardar idioma")
        save.clicked.connect(self._save_language)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addStretch()
        return tab

    def _location_tab(self) -> QWidget:
        """Crea el selector de municipios oficiales."""
        entries = tuple(
            QtCatalogEntry(
                item.code,
                item.label,
                (item.name, item.province, item.autonomous_community),
                (item.name,),
            )
            for item in load_municipalities()
        )
        self.location_selector = CatalogSelector(
            self,
            entries,
            [item.code for item in self.settings.municipalities],
            ("Municipio", "Provincia", "Comunidad autónoma"),
            "Municipios de la obra",
            "Filtra, selecciona y añade varios municipios. Si hay homónimos, "
            "elige la fila correspondiente mediante la provincia.",
            "Añadir municipio",
        )
        return self.location_selector

    def _appearance_tab(self) -> QWidget:
        """Crea el selector global de tema de la interfaz."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.theme_selector = QComboBox()
        for code, label in THEMES.items():
            self.theme_selector.addItem(label, code)
        self.theme_selector.setCurrentIndex(self.theme_selector.findData(self.theme))
        form.addRow("Tema de la interfaz", self.theme_selector)
        layout.addLayout(form)
        note = QLabel(
            "Sistema utiliza la paleta predeterminada; Claro y Oscuro aplican "
            "un tema estable de alto contraste."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        save = QPushButton("Guardar apariencia")
        save.clicked.connect(self._save_theme)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addStretch()
        return tab

    def _save_theme(self) -> None:
        """Persiste y aplica el tema seleccionado."""
        code = str(self.theme_selector.currentData())
        if self.save_theme is not None and self.save_theme(code):
            self.theme = code

    def _cpv_tab(self) -> QWidget:
        """Crea el selector del capítulo 45 del catálogo CPV."""
        entries = tuple(
            QtCatalogEntry(
                entry.code,
                entry.label,
                (entry.code, entry.description),
                (entry.code, entry.code[:8]),
            )
            for entry in load_cpv_codes()
        )
        self.cpv_selector = CatalogSelector(
            self,
            entries,
            [entry.code for entry in self.settings.cpv_entries],
            ("Código CPV", "Descripción"),
            "Códigos CPV de la obra (capítulo 45)",
            "Escribe un código o descripción del capítulo 45, selecciona la entrada "
            "y pulsa Añadir CPV.",
            "Añadir CPV",
        )
        return self.cpv_selector

    def _collaboration_tab(self) -> QWidget:
        """Configura el apodo visible para otros colaboradores."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form = QFormLayout()
        self.nickname_field = QLineEdit(
            str(self.parent().collaboration_nickname)
        )
        self.nickname_field.setMaxLength(40)
        form.addRow("Apodo colaborativo", self.nickname_field)
        layout.addLayout(form)
        note = QLabel(
            "Este apodo y el nombre del equipo se muestran cuando abres un "
            "proyecto colaborativo. No se incluye en los documentos técnicos."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        save = QPushButton("Guardar apodo")
        save.clicked.connect(self._save_nickname)
        layout.addWidget(save, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addStretch()
        return tab

    def _save_nickname(self) -> None:
        """Persiste el apodo global y renueva presencia inmediata."""
        nickname = self.nickname_field.text().strip()
        if not nickname:
            return
        parent = self.parent()
        if parent._save_global_preferences(
            collaboration_nickname=nickname
        ):
            parent.collaboration_nickname = nickname
            parent._publish_presence()

    def _currency_changed(self) -> None:
        """Habilita la divisa personalizada solo para OTHER."""
        self.custom_currency.setEnabled(self.currency.currentData() == "OTHER")

    def _select_template(self) -> None:
        """Selecciona una plantilla ODT absoluta."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecciona la plantilla de informes", "", "Plantilla ODT (*.odt)"
        )
        if path:
            self.template_path.setText(path)

    def _save_language(self) -> None:
        """Persiste la preferencia global de idioma sin cerrar el diálogo."""
        code = str(self.language_selector.currentData())
        if self.save_language(code):
            self.language = code

    def _accept_reports(self) -> None:
        """Valida selecciones pendientes y acepta la configuración."""
        for tab_index, selector in (
            (4, self.location_selector),
            (5, self.cpv_selector),
        ):
            if not selector.confirm_pending():
                self.tabs.setCurrentIndex(tab_index)
                return
        self.accept()

    def values(self) -> dict[str, object]:
        """Devuelve todos los valores del formulario de informes."""
        data: dict[str, object] = {
            key: field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text()
            for key, field in self.report_fields.items()
        }
        data.update({
            "project_title": self.project_title_field.text(),
            "show_author": self.show_author.isChecked(),
            "output_format": self.output_format.currentText().lower(),
            "currency": self.currency.currentData(),
            "custom_currency": self.custom_currency.text(),
            "template_path": self.template_path.text(),
            "municipality_codes": self.location_selector.codes,
            "cpv_codes": self.cpv_selector.codes,
        })
        return data
