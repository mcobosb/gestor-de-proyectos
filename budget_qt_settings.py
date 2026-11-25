"""Diálogo de configuración general y selectores de catálogos para PySide6."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QMessageBox,
    QScrollArea,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from cpv_catalog import load_cpv_codes
from localization import LANGUAGES
from municipalities import load_municipalities, municipality_search_key
from budget_reports import ReportSettings
from budget_qt_theme import THEMES
from document_styles import FLAGS, FONTS, ROLES
from project_standards import DOCUMENT_STANDARDS, PROJECT_DISCIPLINES, document_control_text
from tools._shared.drawing_templates import TITLE_BLOCK_HELP, find_title_block, read_title_block_fields


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
    """Configuración general, informes, plantillas e idioma del proyecto."""

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
        self.setWindowTitle("Configuración general")
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
        self.tabs.addTab(self._templates_tab(), "Plantillas")
        self.standards_tab = self._standards_tab()
        self.tabs.addTab(self.standards_tab, "Tipo de proyecto y normas")
        self.tabs.addTab(self._language_tab(), "Idioma")
        self.tabs.addTab(self._appearance_tab(), "Apariencia")
        self.tabs.addTab(self._collaboration_tab(), "Colaboración")
        self.tabs.addTab(self._location_tab(), "Emplazamiento")
        self.tabs.addTab(self._cpv_tab(), "CPV")
        self.tabs.addTab(self._text_styles_tab(), "Estilos de documentos")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.accepted.connect(self._accept_reports)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _standards_tab(self) -> QWidget:
        """Selecciona disciplinas y referencias sin atribuir cumplimiento automático."""
        content = QWidget()
        layout = QVBoxLayout(content)
        help_text = QLabel(
            "Selecciona los ámbitos y las referencias aplicables. No se asignan normas "
            "automáticamente ni se declara su cumplimiento. El texto se incluye antes "
            "de la tabla de trazabilidad del ODM."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addWidget(QLabel("Tipos de proyecto (se pueden combinar)"))
        self.discipline_checks: dict[str, QCheckBox] = {}
        self.standard_checks: dict[str, QCheckBox] = {}
        for key, label in PROJECT_DISCIPLINES.items():
            check = QCheckBox(label)
            check.setChecked(key in self.settings.project_disciplines)
            layout.addWidget(check)
            self.discipline_checks[key] = check
        layout.addWidget(QLabel("Referencias normativas (selección expresa)"))
        for key, (code, scope, url) in DOCUMENT_STANDARDS.items():
            row = QHBoxLayout()
            check = QCheckBox(code)
            check.setAccessibleName(f"{code}: {scope}")
            check.setChecked(key in self.settings.document_standards)
            row.addWidget(check)
            description = QLabel(scope)
            description.setWordWrap(True)
            row.addWidget(description, 1)
            source = QLabel(f'<a href="{url}">Ficha oficial</a>')
            source.setAccessibleName(f"Ficha oficial de {code}")
            source.setOpenExternalLinks(True)
            row.addWidget(source)
            layout.addLayout(row)
            self.standard_checks[key] = check
        layout.addWidget(QLabel("Vista previa del texto de trazabilidad"))
        self.standards_preview = QPlainTextEdit()
        self.standards_preview.setReadOnly(True)
        self.standards_preview.setMinimumHeight(130)
        layout.addWidget(self.standards_preview)
        for check in (*self.discipline_checks.values(), *self.standard_checks.values()):
            check.toggled.connect(self._update_standards_preview)
        self._update_standards_preview()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _update_standards_preview(self) -> None:
        """Muestra exactamente el párrafo que se generará en el documento maestro."""
        self.standards_preview.setPlainText(document_control_text(
            [key for key, check in self.discipline_checks.items() if check.isChecked()],
            [key for key, check in self.standard_checks.items() if check.isChecked()],
        ))

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
            ("Entidad", "organization"),
            ("Servicio", "service"),
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
        explanation = QLabel(
            "GG y BI se aplican al PEM; en certificaciones la baja se deduce antes "
            "del IVA. La divisa modifica la presentación, no convierte importes."
        )
        explanation.setWordWrap(True)
        form.addRow(explanation)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _templates_tab(self) -> QWidget:
        """Configura las plantillas y el logo usados por los documentos."""
        page = QWidget()
        form = QFormLayout(page)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.template_path = QLineEdit(self.settings.template_path)
        self.template_path.setPlaceholderText("Usar la plantilla de cabecera y pie incluida")
        self.master_template_path = QLineEdit(self.settings.master_template_path)
        self.master_template_path.setPlaceholderText("Usar plantilla.odt incluida en el módulo")
        self.master_logo_path = QLineEdit(self.settings.master_logo_path)
        self.master_logo_path.setPlaceholderText("Usar el logo predeterminado de la plantilla")
        self.drawing_title_block_path = QLineEdit(self.settings.drawing_title_block_path)
        self.drawing_title_block_path.setPlaceholderText("Carpeta con cajetin_A4.svg y cajetin_A3.svg")
        report_help = (
            "Plantilla ODT de cabecera y pie para los informes de las herramientas. "
            "El programa genera el contenido del informe y conserva el diseño de esta plantilla.\n\n"
            "Marcadores opcionales: {{logo}} inserta el logo institucional; {{titulo}} o "
            "{{título}} inserta el título del proyecto; {{expediente}} inserta el número de "
            "expediente. En los pies se pueden usar {{capitulo}} / {{capítulo}} y "
            "{no_pagina} / {{no_pagina}} para el capítulo y la página cuando el informe "
            "corresponda. Si {{logo}} está dentro de un marco con ancho y alto definidos, "
            "el logo se ajusta y se centra en ese espacio sin deformarse ni recortarse. "
            "Sin marco, se mantiene el ancho predeterminado de 2,314 cm.\n\n"
            "Si se deja vacía la ruta, se intenta usar la plantilla incluida. Si no está "
            "disponible, se genera el contenido que pueda sin esa plantilla."
        )
        master_help = (
            "Plantilla ODT de portada y diseño para el documento maestro ODM. El programa "
            "genera el índice, la trazabilidad y los vínculos a los documentos seleccionados; "
            "la plantilla aporta su estructura y presentación.\n\n"
            "Debe conservar una estructura ODT válida, con texto, estilos y páginas maestras, "
            "y una imagen en la cabecera o el marcador {{logo}}. Marcadores que se sustituyen "
            "si están presentes: {{entidad}}, {{servicio}}, {{expediente}}, {{tipo_proyecto}}, "
            "{{título}}, {{autores}}, {{PBL}}, {{PBL+IVA}}, {{plazo_ejecucion}}, {{fecha}} y "
            "{{capítulo}}. {{no_pagina}} se convierte en el número de página.\n\n"
            "Si se deja vacía la ruta, se usa la plantilla incluida en el módulo."
        )
        logo_help = (
            "Imagen JPEG usada para los marcadores {{logo}} de las plantillas de informes y del "
            "documento maestro ODM. Si se deja vacía la ruta, se conserva el logo incluido en la "
            "plantilla, si lo hay; no se usa el icono de la aplicación."
        )
        fields = (
            (
                "report", "Plantilla de informes (cabecera y pie, ODT)",
                self.template_path, self._select_template, "Información sobre la plantilla de informes",
                report_help,
            ),
            (
                "master", "Plantilla del documento maestro (ODT)",
                self.master_template_path, self._select_master_template,
                "Información sobre la plantilla del documento maestro", master_help,
            ),
            (
                "logo", "Logo institucional (JPEG)",
                self.master_logo_path, self._select_master_logo,
                "Información sobre el logo institucional", logo_help,
            ),
            (
                "drawings", "Carpeta de cajetines de planos (A4 / A3)",
                self.drawing_title_block_path, self._select_drawing_title_block,
                "Etiquetas del cajetín SVG", TITLE_BLOCK_HELP,
            ),
        )
        self.template_info_buttons: dict[str, QToolButton] = {}
        for key, label, field, callback, help_title, help_text in fields:
            button = QPushButton("Examinar…")
            button.clicked.connect(callback)
            row = QHBoxLayout()
            row.addWidget(field, 1)
            row.addWidget(button)
            label_widget = QLabel(label)
            label_widget.setBuddy(field)
            label_container = QWidget()
            label_row = QHBoxLayout(label_container)
            label_row.setContentsMargins(0, 0, 0, 0)
            label_row.setSpacing(4)
            label_row.addWidget(label_widget)
            info_button = QToolButton()
            info_button.setIcon(self.style().standardIcon(
                QStyle.StandardPixmap.SP_MessageBoxInformation,
            ))
            info_button.setToolTip(help_title)
            info_button.setAccessibleName(help_title)
            info_button.setAutoRaise(True)
            if key == "drawings":
                info_button.clicked.connect(self._drawing_title_block_info)
                self.drawing_title_block_info_button = info_button
            else:
                info_button.clicked.connect(
                    lambda _checked=False, title=help_title, text=help_text:
                    QMessageBox.information(self, title, text)
                )
            self.template_info_buttons[key] = info_button
            label_row.addWidget(info_button)
            form.addRow(label_container, row)
        note = QLabel(
            "Deja vacías las rutas de informes, maestro o logo para utilizar los recursos incluidos. "
            "La carpeta de cajetines es opcional; se busca A4 o A3 en los nombres de los SVG según el formato del plano. "
            "La plantilla del documento maestro debe conservar la estructura ODT "
            "con marcadores de proyecto, índice, trazabilidad y logo."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return page

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
        self._select_file(
            self.template_path, "Selecciona la plantilla de informes",
            "Plantilla ODT (*.odt)",
        )

    def _select_master_template(self) -> None:
        """Selecciona una plantilla ODT para el documento maestro."""
        self._select_file(
            self.master_template_path, "Selecciona la plantilla del documento maestro",
            "Plantilla ODT (*.odt)",
        )

    def _select_master_logo(self) -> None:
        """Selecciona el logo JPEG de los documentos."""
        self._select_file(
            self.master_logo_path, "Selecciona el logo institucional",
            "Imagen JPEG (*.jpg *.jpeg)",
        )

    def _select_drawing_title_block(self) -> None:
        """Vincula la carpeta de cajetines para resolver el formato de cada plano."""
        initial = self.drawing_title_block_path.text().strip()
        if initial and Path(initial).is_file():
            initial = str(Path(initial).parent)
        path = QFileDialog.getExistingDirectory(
            self, "Selecciona la carpeta de cajetines A4 y A3", initial,
            QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            self.drawing_title_block_path.setText(path)

    def _drawing_title_block_info(self) -> None:
        """Explica la convención y comprueba por separado los cajetines A4 y A3."""
        message = TITLE_BLOCK_HELP
        raw_path = self.drawing_title_block_path.text().strip()
        if raw_path:
            for paper_size in ("A4", "A3"):
                try:
                    path = find_title_block(Path(raw_path), paper_size)
                    fields = read_title_block_fields(path)
                except (OSError, ET.ParseError, ValueError) as exc:
                    message += f"\n\n{paper_size}: no se pudo resolver el cajetín: {exc}"
                else:
                    message += f"\n\n{paper_size} — {path.name}. Campos reconocidos: " + (
                        ", ".join(fields) if fields else "ninguno; revisa los IDs según estas instrucciones."
                    )
        QMessageBox.information(self, "Etiquetas del cajetín SVG", message)

    def _select_file(self, field: QLineEdit, title: str, file_filter: str) -> None:
        """Abre el selector de archivos y guarda la ruta seleccionada."""
        path, _ = QFileDialog.getOpenFileName(
            self, title, field.text().strip(), file_filter,
        )
        if path:
            field.setText(path)

    def _save_language(self) -> None:
        """Persiste la preferencia global de idioma sin cerrar el diálogo."""
        code = str(self.language_selector.currentData())
        if self.save_language(code):
            self.language = code

    def _text_styles_tab(self) -> QWidget:
        """Edita por separado la fuente, el tamaño y el énfasis de cada estilo."""
        page = QWidget()
        layout = QVBoxLayout(page)
        help_text = QLabel("Estándar conserva el formato original de cada documento. "
                           "Los cambios se guardan en este proyecto.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        grid = QWidget()
        form = QFormLayout(grid)
        scroll.setWidget(grid)
        layout.addWidget(scroll, 1)
        self.style_controls = {}
        self.style_flags = {}

        def toggle(button, role: str, flag: str, default: bool) -> None:
            """Cicla entre heredar, activar y desactivar un atributo."""
            current = self.style_flags[role, flag]
            self.style_flags[role, flag] = not default if current is None else default if current != default else None
            refresh_flag(button, role, flag, default)

        def refresh_flag(button, role: str, flag: str, default: bool) -> None:
            """Distingue visualmente un valor estándar de uno explícito."""
            state = self.style_flags[role, flag]
            button.setChecked(default if state is None else state)
            button.setText(button.property("styleLetter") + ("·" if state is None else ""))
            button.setToolTip(button.accessibleName() + ": " + (
                "Estándar" if state is None else "Activado" if state else "Desactivado"
            ))

        for role, (label, standard_size, standard_bold) in ROLES.items():
            saved = self.settings.text_styles.get(role, {})
            row = QHBoxLayout()
            font = QComboBox()
            font.setAccessibleName(f"{label}: fuente")
            font.addItems(("Estándar", *FONTS))
            font.setCurrentText(str(saved.get("font", "Estándar")))
            row.addWidget(font, 1)
            size = QDoubleSpinBox()
            size.setAccessibleName(f"{label}: tamaño")
            size.setDecimals(1)
            size.setSingleStep(0.5)
            size.setRange(5.9, 36)
            size.setSpecialValueText("Estándar")
            size.setSuffix(" pt")
            size.setToolTip(f"Tamaño estándar orientativo: {standard_size} pt")
            size.setValue(float(saved.get("size", size.minimum())))
            row.addWidget(size)
            self.style_controls[role] = font, size
            for flag, letter, name in zip(FLAGS, "NCS", ("Negrita", "Cursiva", "Subrayado"), strict=True):
                button = QToolButton()
                button.setAccessibleName(f"{label}: {name}")
                button.setProperty("styleLetter", letter)
                button.setCheckable(True)
                visual = button.font()
                visual.setBold(flag == "bold")
                visual.setItalic(flag == "italic")
                visual.setUnderline(flag == "underline")
                button.setFont(visual)
                self.style_flags[role, flag] = saved.get(flag)
                default = flag == "bold" and standard_bold
                refresh_flag(button, role, flag, default)
                button.clicked.connect(
                    lambda checked=False, b=button, r=role, f=flag, d=default: toggle(b, r, f, d)
                )
                row.addWidget(button)
            form.addRow(label, row)
        return page

    def _style_values(self) -> dict:
        """Recoge únicamente los atributos personalizados en la pestaña."""
        styles = {}
        for role, (font, size) in self.style_controls.items():
            result = {}
            if font.currentIndex():
                result["font"] = font.currentText()
            size.interpretText()
            if size.value() != size.minimum():
                result["size"] = size.value()
            result.update({flag: value for flag in FLAGS
                           if (value := self.style_flags[role, flag]) is not None})
            if result:
                styles[role] = result
        return styles

    def _accept_reports(self) -> None:
        """Valida selecciones pendientes y acepta la configuración."""
        for selector in (self.location_selector, self.cpv_selector):
            if not selector.confirm_pending():
                self.tabs.setCurrentWidget(selector)
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
            "currency": self.currency.currentData(),
            "custom_currency": self.custom_currency.text(),
            "template_path": self.template_path.text(),
            "master_template_path": self.master_template_path.text(),
            "master_logo_path": self.master_logo_path.text(),
            "drawing_title_block_path": self.drawing_title_block_path.text(),
            "municipality_codes": self.location_selector.codes,
            "cpv_codes": self.cpv_selector.codes,
            "project_disciplines": [
                key for key, check in self.discipline_checks.items() if check.isChecked()
            ],
            "document_standards": [
                key for key, check in self.standard_checks.items() if check.isChecked()
            ],
            "text_styles": self._style_values(),
        })
        return data
