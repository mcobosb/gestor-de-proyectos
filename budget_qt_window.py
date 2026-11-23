"""Ventana principal de presupuestos basada en PySide6."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
import webbrowser
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QResizeEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


from budget_qt_dialogs import ItemEditor, MeasurementEditor
from qt_localization import document_save_initial_path, get_document_save_path
from PySide6.QtWidgets import QTreeWidgetItemIterator
from app_paths import application_directory, user_data_directory
from budget_config import (
    APPLICATION_NAME, APPLICATION_RELEASE_DATE, APPLICATION_VERSION,
    BUY_ME_A_COFFEE_URL, CONCEPT_TYPES, DUPLICATE_ITEM_MARKER,
    REFERENCE_PRICE_DATABASES, REPOSITORY_URL,
)
from budget_core import (
    Bc3Dataset,
    Concept,
    MeasurementLine,
    ProjectItem,
    currency_label,
    fmt_number,
    format_chapter_code,
    format_currency,
    is_chapter_code,
    round_half_up,
)
from budget_project import (
    budget_concept_type,
    build_project_payload,
    chapter_title_for,
    component_dataset_for,
    project_title_for,
    read_project,
    reference_base_for,
    report_components,
    write_project,
)
from budget_reports import REPORT_NAMES, ReportSettings, write_pdf
from budget_qt_settings import SettingsDialog
from budget_qt_theme import THEMES, apply_theme, search_icon
from tools._shared.reference_pill import add_reference_pill
from localization import LANGUAGES, set_language, translate
from qt_localization import apply_button_language
from odt_reports import write_report_odt
from ods_export import write_ods
from tool_loader import discover_tools
from reference_search import load_reference_entries
from collaborative_storage import (
    CollaborativeProjectStore,
    CollaborativeWatchService,
    ProjectLockManager,
    StorageConflictError,
    project_directory,
    encrypted_manifest_metadata,
)
from collaborative_crypto import InvalidProjectPasswordError, ProjectCrypto


ROW_KIND = Qt.ItemDataRole.UserRole
ROW_DATA = Qt.ItemDataRole.UserRole + 1


class _PanelTitle(QLabel):
    """Muestra el nombre en una línea sin imponer su ancho al panel."""

    def __init__(self, title: str) -> None:
        """Configura la etiqueta y conserva el nombre sin recortar."""
        super().__init__()
        self._title = title
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.set_title(title)

    def set_title(self, title: str) -> None:
        """Actualiza el nombre completo y su versión visible."""
        self._title = title
        self.setAccessibleName(title)
        self._update_text()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Recalcula el recorte al cambiar el ancho disponible."""
        super().resizeEvent(event)
        self._update_text()

    def _update_text(self) -> None:
        """Recorta por el ancho del texto y permite consultar el nombre completo."""
        visible = self.fontMetrics().elidedText(
            self._title, Qt.TextElideMode.ElideRight, self.contentsRect().width()
        )
        self.setText(visible)
        self.setToolTip(self._title if visible != self._title else "")


class BudgetMainWindow(QMainWindow):
    """Ventana principal Qt para editar proyectos de presupuesto."""

    budget_refreshed = Signal()

    def __init__(self) -> None:
        """Inicializa el estado, los menús y los dos paneles de presupuesto."""
        super().__init__()
        self.setWindowIcon(
            QIcon(str(application_directory(__file__) / "iconos" / "logo.ico"))
        )
        self.reference_bases: list[Bc3Dataset] = []
        self.budget_dataset: Bc3Dataset | None = None
        self.budget_items: list[ProjectItem] = []
        self.selected_budget_index: int | None = None
        self.selected_chapter: str | None = None
        self.selected_reference: tuple[int, str] | None = None
        self.budget_resource_filter = ""
        self.reference_resource_filter = ""
        self.budget_filter_buttons: dict[str, QPushButton] = {}
        self.reference_filter_buttons: dict[str, QPushButton] = {}
        self.chapter_order: list[str] = []
        self.chapter_titles: dict[str, str] = {}
        self.chapter_ids: dict[str, str] = {}
        self.planning_state: dict = {}
        self.invoicing_state: dict = {}
        self.rcd_state: dict = {}
        self.safety_health_state: dict = {}
        self.pptp_state: dict = {}
        self.price_justification_state: dict = {}
        self.quality_assurance_state: dict = {}
        self._project_windows: list[QDialog] = []
        self.project_session = 0
        self.project_title: str | None = None
        self.project_path: Path | None = None
        self.project_store: CollaborativeProjectStore | None = None
        self.project_watcher: CollaborativeWatchService | None = None
        self.lock_manager = ProjectLockManager()
        self.collaboration_session_id = uuid4().hex
        self.collaborators: list[dict[str, str]] = []
        self.collaboration_nickname = "Anónimo"
        self._active_item_locks = {}
        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(120_000)
        self._lock_timer.timeout.connect(self._renew_item_locks)
        self._presence_timer = QTimer(self)
        self._presence_timer.setInterval(15_000)
        self._presence_timer.timeout.connect(self._publish_presence)
        self.source_budget_path: Path | None = None
        self.is_dirty = False
        self._undo_history: list[dict] = []
        self._history_state: dict = {}
        self._saved_state: dict | None = {}
        data_directory = user_data_directory(__file__)
        self.recent_projects_path = data_directory / "proyectos_recientes.json"
        self.recent_reference_bases_path = data_directory / "bases_referencia_recientes.json"
        self.preferences_path = data_directory / "preferencias.json"
        self.report_settings_path = data_directory / "configuracion_informes.json"
        self.library_preferences_path = data_directory / "bibliotecas.json"
        self.library_sources = self._read_library_preferences()
        self.recent_projects = self._read_recent_paths(self.recent_projects_path, 5)
        self.recent_reference_bases = self._read_recent_paths(self.recent_reference_bases_path, 3)
        self.default_report_settings = self._read_report_settings()
        self.report_settings = ReportSettings.from_dict(self.default_report_settings.to_dict())
        self.loaded_tools, tool_errors = discover_tools(
            application_directory(__file__) / "tools"
        )
        self.tools_by_name = {
            tool.name: tool for tool in self.loaded_tools if tool.open_qt is not None
        }
        self.language = self._read_language_preference()
        self.theme = self._read_theme_preference()
        self.collaboration_nickname = self._read_collaboration_nickname()
        set_language(self.language)
        apply_button_language(QApplication.instance())
        apply_theme(QApplication.instance(), self.theme)
        self._build_ui()
        self._collaboration_presence = QWidget()
        self._collaboration_presence.setObjectName("collaborationPresence")
        presence_layout = QHBoxLayout(self._collaboration_presence)
        presence_layout.setContentsMargins(0, 0, 0, 0)
        presence_layout.setSpacing(5)
        self._collaboration_presence_icon = QLabel()
        self._collaboration_presence_icon.setPixmap(
            QIcon.fromTheme(
                "system-users",
                self.style().standardIcon(
                    QStyle.StandardPixmap.SP_FileDialogDetailedView
                ),
            ).pixmap(16, 16)
        )
        self._collaboration_presence_text = QLabel()
        presence_layout.addWidget(self._collaboration_presence_icon)
        presence_layout.addWidget(self._collaboration_presence_text)
        self.status_bar.addPermanentWidget(self._collaboration_presence)
        self._collaboration_presence.setVisible(False)
        if tool_errors:
            QMessageBox.warning(
                self,
                "Herramientas",
                "No se pudieron cargar algunas herramientas:\n"
                + "\n".join(tool_errors),
            )
        self._reset_history()
        self.resize(1480, 860)
        self.setMinimumSize(1100, 650)

    def _build_ui(self) -> None:
        """Crea menús, barra de acciones, paneles, árboles y detalles."""
        self.setWindowTitle(APPLICATION_NAME)
        self._build_menus()
        self._build_toolbar()
        main = QWidget()
        layout = QVBoxLayout(main)
        add_reference_pill(layout, "budget")
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_budget_panel())
        splitter.addWidget(self._build_reference_panel())
        splitter.setSizes((760, 580))
        layout.addWidget(splitter)
        self.setCentralWidget(main)
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def _build_menus(self) -> None:
        """Configura los menús de archivo, exportación, informes e información."""
        menu = self.menuBar()
        self._file_menu = file_menu = QMenu("Archivo", menu)
        menu.addMenu(file_menu)
        self.recent_menu = QMenu("Recientes", file_menu)
        file_menu.addMenu(self.recent_menu)
        self.recent_reference_menu = QMenu("Bases de referencia recientes", file_menu)
        file_menu.addMenu(self.recent_reference_menu)
        self._refresh_recent_menu()
        self._refresh_recent_reference_menu()
        file_menu.addSeparator()
        file_menu.addAction(self._action("Nuevo proyecto", self.new_project, "Ctrl+N"))
        file_menu.addSeparator()
        file_menu.addAction(self._action("Importar presupuesto BC3…", self.import_budget))
        file_menu.addAction(self._action("Añadir base de referencia BCCA…", self.load_references))
        file_menu.addAction(
            self._action("Quitar base de referencia…", self.remove_reference_bases)
        )
        file_menu.addSeparator()
        file_menu.addAction(
            self._action(
                "Abrir proyecto colaborativo…",
                self.open_collaborative_project,
                "Ctrl+O",
            )
        )
        file_menu.addAction(
            self._action("Importar proyecto JSON…", self.open_project)
        )
        file_menu.addAction(self._action("Guardar", self.save_project, "Ctrl+S"))
        file_menu.addAction(
            self._action("Guardar como proyecto colaborativo…", self.save_project_as)
        )
        self._export_menu = export_menu = QMenu("Exportar", file_menu)
        file_menu.addMenu(export_menu)
        export_menu.addAction("Exportar presupuesto a ODS…", self.export_budget_ods)
        export_menu.addAction("Exportar mediciones a CSV…", self.export_measurements_csv)
        export_menu.addAction(
            "Exportar recursos a ODS…",
            lambda: self.export_resource_report("Recursos"),
        )
        export_menu.addAction(
            "Exportar descompuestos a ODS…",
            lambda: self.export_resource_report("Descompuestos"),
        )
        file_menu.addSeparator()
        file_menu.addAction(self._action("Configuración…", self.open_report_settings))

        reports_menu = menu.addMenu("Informes")
        for report in REPORT_NAMES:
            reports_menu.addAction(report, lambda checked=False, name=report: self.generate_report(name))

        self._tools_menu = tools_menu = QMenu("Herramientas", menu)
        menu.addMenu(tools_menu)
        for tool in sorted(
            self.tools_by_name.values(),
            key=lambda entry: (entry.order, entry.label.casefold()),
        ):
            if tool.name == "bim":
                continue
            action = tools_menu.addAction(tool.label)
            action.triggered.connect(
                lambda checked=False, name=tool.name: self._open_tool(name)
            )
        self._references_menu = QMenu("Consultas", menu)
        menu.addMenu(self._references_menu)
        icon = search_icon(self._references_menu)
        for kind in ("NTP", "UNE", "LER"):
            action = self._references_menu.addAction(
                icon, kind, lambda checked=False, selected=kind: self.open_reference_search(selected)
            )
            action.setProperty("searchIcon", True)
            action.setIconVisibleInMenu(True)

        collaboration_menu = menu.addMenu("Colaboración")
        collaboration_menu.addAction(
            "Abrir proyecto compartido…", self.open_collaborative_project
        )
        collaboration_menu.addAction(
            "Crear o migrar proyecto compartido…", self.save_project_as
        )
        collaboration_menu.addAction(
            "Cambiar protección…", self.change_collaboration_password
        )
        collaboration_menu.addAction(
            "Fuentes de bibliotecas…", self.open_shared_libraries
        )
        collaboration_menu.addAction(
            "Estado de colaboración…", self.show_collaboration_status
        )

        bim = self.tools_by_name.get("bim")
        if bim is not None:
            bim_menu = menu.addMenu(bim.label)
            bim_menu.addAction("Abrir archivo IFC…", lambda: self._open_tool("bim"))

        info_menu = menu.addMenu("Información")
        info_menu.addAction(APPLICATION_NAME).setEnabled(False)
        info_menu.addAction(
            f"Versión {APPLICATION_VERSION} — {APPLICATION_RELEASE_DATE:%d/%m/%Y}"
        ).setEnabled(False)
        price_menu = info_menu.addMenu("Bases de precios de referencia")
        for label, url in REFERENCE_PRICE_DATABASES:
            price_menu.addAction(
                label,
                lambda checked=False, selected=url: self._open_web_page(
                    selected,
                    "Bases de precios de referencia",
                    "Base de precios abierta en el navegador.",
                ),
            )
        info_menu.addAction("Repositorio en GitHub", self.open_repository)
        info_menu.addAction("Licencia y responsabilidad…", self.show_license_info)
        info_menu.addAction(
            "Apoyar el proyecto (Buy Me a Coffee)", self.open_donation_page
        )
        info_menu.addAction("Acerca de PySide6…", self.show_about_qt)

    def _build_toolbar(self) -> None:
        """Añade las acciones rápidas del presupuesto."""
        toolbar = QToolBar("Presupuesto")
        self.addToolBar(toolbar)
        self.save_action = self._action("Guardar", self.save_project)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        toolbar.addAction(self.save_action)
        save_button = toolbar.widgetForAction(self.save_action)
        if save_button is not None:
            save_button.setObjectName("saveBudgetButton")
        self.undo_action = self._action("Deshacer", self.undo_last_action)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        toolbar.addAction(self.undo_action)
        toolbar.addSeparator()
        for tool in self.tools_by_name.values():
            if tool.install_toolbar_qt is not None:
                tool.install_toolbar_qt(self, toolbar)
        toolbar.addAction("Nuevo capítulo", self.add_chapter)
        toolbar.addAction("Nueva partida", self.add_manual_item)
        toolbar.addAction("Eliminar", self.delete_selection)
        toolbar.addAction("← Añadir referencia", self.add_selected_reference)
        toolbar.addAction("Editar", self.edit_selection)
        toolbar.addAction("Duplicar", self.duplicate_selection)
        toolbar.addAction("Subir", lambda: self.move_selection(-1))
        toolbar.addAction("Bajar", lambda: self.move_selection(1))
        toolbar.addSeparator()
        toolbar.addAction("Expandir / contraer", self.toggle_budget_expansion)
        self.summary_label = QLabel("Total: 0,00 €")
        self.summary_label.setObjectName("budgetTotal")
        toolbar.addWidget(self.summary_label)

    def _build_budget_panel(self) -> QWidget:
        """Crea el árbol principal y los detalles de la partida seleccionada."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        caption = QLabel("Proyecto")
        caption.setObjectName("projectCaption")
        titles.addWidget(caption)
        self.project_heading = _PanelTitle("Sin título")
        titles.addWidget(self.project_heading)
        heading.addLayout(titles, 1)
        for kind in ("2", "1", "3"):
            button = QPushButton(CONCEPT_TYPES[kind].split()[0])
            button.setCheckable(True)
            button.setProperty("textOnlyIcon", True)
            button.setToolTip(
                f"Filtrar recursos: {CONCEPT_TYPES[kind]}. "
                "Pulsa de nuevo para mostrar todos."
            )
            button.clicked.connect(
                lambda checked=False, selected=kind:
                    self.toggle_budget_resource_filter(selected)
            )
            self.budget_filter_buttons[kind] = button
            heading.addWidget(button)
        layout.addLayout(heading)
        self.budget_search = QLineEdit()
        self.budget_search.setPlaceholderText("Filtrar presupuesto por código o texto…")
        self.budget_search.textChanged.connect(self.refresh_budget_tree)
        layout.addWidget(self.budget_search)
        self.budget_tree = QTreeWidget()
        self.budget_tree.setObjectName("budgetTree")
        self.budget_tree.setColumnCount(5)
        self.budget_tree.setHeaderLabels(("Código", "Tipo", "Texto", "Medición", "Importe"))
        self.budget_tree.setAlternatingRowColors(True)
        self.budget_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.budget_tree.itemSelectionChanged.connect(self.on_budget_selection)
        self.budget_tree.itemDoubleClicked.connect(self.edit_selection)
        header = self.budget_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.budget_tree, 3)
        details = QTabWidget()
        details.addTab(self._build_measurements_panel(), "Mediciones")
        details.addTab(self._build_components_panel(), "Descomposición")
        self.description = QPlainTextEdit()
        self.description.textChanged.connect(self.on_description_changed)
        details.addTab(self.description, "Descripción")
        layout.addWidget(details, 2)
        return panel

    def _build_measurements_panel(self) -> QWidget:
        """Crea la tabla editable de mediciones de la partida."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.measurement_table = QTableWidget(0, 7)
        self.measurement_table.setHorizontalHeaderLabels(
            ("Comentario", "Uds", "Largo", "Ancho", "Alto", "K", "Parcial")
        )
        self.measurement_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.measurement_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.measurement_table.doubleClicked.connect(self.edit_measurement)
        layout.addWidget(self.measurement_table)
        actions = QHBoxLayout()
        actions.addWidget(QPushButton("Añadir", clicked=self.add_measurement))
        actions.addWidget(QPushButton("Editar", clicked=self.edit_measurement))
        actions.addWidget(QPushButton("Eliminar", clicked=self.delete_measurement))
        self.measurement_total = QLabel("Sin partida seleccionada.")
        actions.addStretch()
        actions.addWidget(self.measurement_total)
        layout.addLayout(actions)
        return panel

    def _build_components_panel(self) -> QWidget:
        """Crea la tabla de componentes calculados de la partida."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.component_table = QTableWidget(0, 5)
        self.component_table.setHorizontalHeaderLabels(
            ("Código", "Descripción", "Cantidad", "Precio", "Importe")
        )
        self.component_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.component_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.component_table)
        return panel

    def _build_reference_panel(self) -> QWidget:
        """Crea el buscador y árbol de bases de referencia."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        caption = QLabel("Base de referencia")
        caption.setObjectName("referenceCaption")
        titles.addWidget(caption)
        self.reference_heading = _PanelTitle("Sin cargar")
        titles.addWidget(self.reference_heading)
        heading.addLayout(titles, 1)
        for kind in ("2", "1", "3"):
            button = QPushButton(CONCEPT_TYPES[kind].split()[0])
            button.setCheckable(True)
            button.setProperty("textOnlyIcon", True)
            button.setToolTip(
                f"Filtrar recursos: {CONCEPT_TYPES[kind]}. "
                "Pulsa de nuevo para mostrar todos."
            )
            button.clicked.connect(
                lambda checked=False, selected=kind:
                    self.toggle_reference_resource_filter(selected)
            )
            self.reference_filter_buttons[kind] = button
            heading.addWidget(button)
        layout.addLayout(heading)
        self.reference_search = QLineEdit()
        self.reference_search.setPlaceholderText("Buscar código o texto en las bases…")
        self.reference_search.textChanged.connect(self.refresh_reference_tree)
        layout.addWidget(self.reference_search)
        self.reference_tree = QTreeWidget()
        self.reference_tree.setObjectName("referenceTree")
        self.reference_tree.setColumnCount(5)
        self.reference_tree.setHeaderLabels(("Código / texto", "Ud", "Rendimiento", "Precio", "Base"))
        self.reference_tree.setAlternatingRowColors(True)
        self.reference_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.reference_tree.itemDoubleClicked.connect(self.add_selected_reference)
        header = self.reference_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.reference_tree, 3)
        self.reference_details = QPlainTextEdit()
        self.reference_details.setAccessibleName("Detalles de la referencia")
        self.reference_details.setPlaceholderText("Selecciona una fila para ver sus detalles.")
        self.reference_details.setReadOnly(True)
        self.reference_details.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.reference_details, 1)
        self.reference_tree.itemSelectionChanged.connect(self.on_reference_selection)
        return panel

    def _action(self, text: str, slot, shortcut: str = "") -> QAction:
        """Crea una acción asociada a un método y a un atajo opcional."""
        action = QAction(self)
        action.setText(text)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(shortcut)
        return action

    def closeEvent(self, event: QCloseEvent) -> None:
        """Protege los cambios pendientes antes de cerrar la aplicación."""
        if (
            not self._confirm_project_change()
            or not self._dismiss_project_windows()
        ):
            event.ignore()
            return
        self._stop_collaboration()
        event.accept()

    def _set_status(self, text: str) -> None:
        """Muestra una operación terminada en la barra de estado."""
        self.status_bar.showMessage(text, 8000)

    def _project_title(self) -> str:
        """Obtiene el título explícito o lo deduce del BC3."""
        return project_title_for(self)

    def _chapter_title(self, chapter: str) -> str:
        """Obtiene el nombre visible de un capítulo."""
        return chapter_title_for(self, chapter)

    def _reference_base_for(self, item: ProjectItem) -> Bc3Dataset | None:
        """Localiza la base asociada a una partida."""
        return reference_base_for(self, item)

    def _component_dataset(self, item: ProjectItem) -> Bc3Dataset | None:
        """Selecciona el conjunto de conceptos de una partida."""
        return component_dataset_for(self, item)

    def _budget_concept_type(self, item: ProjectItem, component_code: str | None = None) -> str:
        """Determina el tipo visible de una partida o recurso."""
        return budget_concept_type(self, item, component_code)

    def _report_components(self, item: ProjectItem) -> list[tuple[Concept, float, float]]:
        """Obtiene los componentes con los precios efectivos."""
        return report_components(self, item)

    def _format_currency(self, value: float) -> str:
        """Formatea un importe con la moneda activa."""
        return format_currency(
            value, self.report_settings.currency, self.report_settings.custom_currency
        )

    def _currency_label(self) -> str:
        """Devuelve la divisa activa."""
        return currency_label(
            self.report_settings.currency, self.report_settings.custom_currency
        )

    def _ensure_chapter(self, chapter: str) -> None:
        """Añade un capítulo a la lista si todavía no existe."""
        if chapter and chapter not in self.chapter_order:
            self.chapter_order.append(chapter)

    def _capture_project_state(self) -> dict:
        """Copia únicamente los datos persistentes para el historial."""
        return deepcopy({
            "items": self.budget_items,
            "order": self.chapter_order,
            "titles": self.chapter_titles,
            "chapter_ids": self.chapter_ids,
            "planning": self.planning_state,
            "invoicing": self.invoicing_state,
            "rcds": self.rcd_state,
            "safety_health": self.safety_health_state,
            "pptp": self.pptp_state,
            "price_justification": self.price_justification_state,
            "quality_assurance": self.quality_assurance_state,
            "settings": self.report_settings,
            "title": self.project_title,
        })

    def _reset_history(self, saved: bool = True) -> None:
        """Reinicia el historial después de abrir o guardar un proyecto."""
        self._undo_history = []
        self._history_state = self._capture_project_state()
        self._saved_state = self._history_state if saved else None
        self.is_dirty = not saved
        self._update_actions()

    def _mark_dirty(self) -> None:
        """Registra una edición real y actualiza el estado de guardado."""
        state = self._capture_project_state()
        if state != self._history_state:
            self._undo_history.append(self._history_state)
            del self._undo_history[:-50]
            self._history_state = state
        self.is_dirty = state != self._saved_state
        self._update_actions()

    def _update_actions(self) -> None:
        """Actualiza los indicadores de guardado y deshacer."""
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(bool(self._undo_history))
        title = f"{self._project_title()} *" if self.is_dirty else self._project_title()
        self.project_heading.set_title(title)
        self.setWindowTitle(f"{title} — {APPLICATION_NAME}")

    def undo_last_action(self) -> None:
        """Restaura el último estado persistente editado."""
        if not self._undo_history:
            self._set_status("No hay acciones que deshacer.")
            return
        state = deepcopy(self._undo_history.pop())
        self.budget_items = state["items"]
        self.chapter_order = state["order"]
        self.chapter_titles = state["titles"]
        self.chapter_ids = state.get("chapter_ids", {})
        self.planning_state = state["planning"]
        self.invoicing_state = state["invoicing"]
        self.rcd_state = state["rcds"]
        self.safety_health_state = state["safety_health"]
        self.pptp_state = state["pptp"]
        self.price_justification_state = state["price_justification"]
        self.quality_assurance_state = state["quality_assurance"]
        self.report_settings = state["settings"]
        self.project_title = state["title"]
        self._history_state = state
        self.is_dirty = state != self._saved_state
        self.selected_budget_index = None
        self.selected_chapter = None
        self.refresh_budget_tree()
        self._show_item_details(None)
        self._update_actions()
        self._set_status("Última acción deshecha.")

    def new_project(self) -> None:
        """Crea un presupuesto vacío tras solicitar un nombre."""
        from PySide6.QtWidgets import QInputDialog

        title, accepted = QInputDialog.getText(
            self, "Nuevo proyecto", "Nombre del proyecto:", text="Nuevo proyecto"
        )
        if not accepted or not self._confirm_project_change():
            return
        if not self._dismiss_project_windows():
            return
        self.budget_dataset = None
        self.budget_items = []
        self.chapter_order = []
        self.chapter_titles = {}
        self.chapter_ids = {}
        self.project_title = title.strip() or "Nuevo proyecto"
        self.project_path = None
        self._stop_collaboration()
        self.source_budget_path = None
        self.planning_state = self.invoicing_state = self.rcd_state = {}
        self.safety_health_state = self.pptp_state = self.price_justification_state = {}
        self.quality_assurance_state = {}
        self.report_settings = ReportSettings.from_dict(self.default_report_settings.to_dict())
        self.refresh_budget_tree()
        self._show_item_details(None)
        self._reset_history()
        self._set_status("Nuevo proyecto vacío creado.")

    def import_budget(self) -> None:
        """Importa un BC3 y crea su JSON asociado."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecciona un presupuesto BC3", "", "BC3 (*.bc3)"
        )
        if path and self._confirm_project_change():
            self._load_budget_file(Path(path))

    def _load_budget_file(self, path: Path) -> None:
        """Carga un BC3 o reabre el JSON asociado si ya existe."""
        try:
            dataset = Bc3Dataset(path)
            legacy_path = path.with_suffix(".json")
            collaborative_path = path.with_suffix("")
            if project_directory(collaborative_path):
                if self._load_collaborative_project(collaborative_path):
                    return
            elif legacy_path.is_file():
                if self._load_project_file(legacy_path, dataset, path):
                    return
            if not self._dismiss_project_windows():
                return
            self.budget_dataset = dataset
            self.source_budget_path = path
            self.budget_items = dataset.to_project_items()
            self.chapter_order = list(dict.fromkeys(item.chapter for item in self.budget_items))
            self.chapter_ids = {}
            self.chapter_titles = {
                code: concept.summary
                for code, concept in dataset.concepts.items()
                if is_chapter_code(code) and not code.endswith("##")
            }
            self.project_title = path.stem
            self.project_path = collaborative_path
            self.report_settings = ReportSettings.from_dict(self.default_report_settings.to_dict())
            for item in self.budget_items:
                item.set_indirect_costs(self.report_settings.indirect_costs)
            self.refresh_budget_tree()
            self._show_item_details(None)
            self._reset_history(saved=False)
            if not self._write_collaborative_project(collaborative_path):
                return
            self._set_status(f"Presupuesto cargado: {path.name}")
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "No se pudo cargar el presupuesto", str(exc))

    def load_references(self) -> None:
        """Selecciona y carga una o varias bases BC3 de referencia."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Selecciona bases de precios BC3", "", "BC3 (*.bc3)"
        )
        for name in paths:
            self._load_reference_file(Path(name))

    def remove_reference_bases(self) -> None:
        """Permite retirar una o varias bases abiertas."""
        if not self.reference_bases:
            QMessageBox.information(
                self, "Sin bases", "No hay bases de referencia cargadas."
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Quitar base de referencia")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Selecciona las bases a quitar:"))
        list_widget = QListWidget()
        list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        list_widget.addItems(base.path.name for base in self.reference_bases)
        layout.addWidget(list_widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        removed = {list_widget.row(item) for item in list_widget.selectedItems()}
        if not removed:
            return
        self.reference_bases = [
            base
            for index, base in enumerate(self.reference_bases)
            if index not in removed
        ]
        self.refresh_reference_tree()
        self._set_status(f"Quitadas {len(removed)} base(s) de referencia.")

    def _load_reference_file(self, path: Path) -> None:
        """Carga una base, sustituyendo cualquier copia previa de la misma ruta."""
        try:
            dataset = Bc3Dataset(path)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, "No se pudo cargar la base", str(exc))
            return
        resolved = path.resolve()
        for index, base in enumerate(self.reference_bases):
            if base.path.resolve() == resolved:
                self.reference_bases[index] = dataset
                break
        else:
            self.reference_bases.append(dataset)
        self.recent_reference_bases = [path.resolve()] + [
            value for value in self.recent_reference_bases if value != path.resolve()
        ][:2]
        self._write_recent_paths(self.recent_reference_bases_path, self.recent_reference_bases)
        self._refresh_recent_reference_menu()
        self.refresh_reference_tree()
        self._set_status(f"BCCA cargada: {path.name}")

    def refresh_reference_tree(self) -> None:
        """Reconstruye el árbol de referencia respetando el filtro actual."""
        self.reference_tree.clear()
        term = self.reference_search.text().strip().lower()
        if self.reference_resource_filter:
            self._refresh_reference_resources(term)
            return
        grouped = len(self.reference_bases) > 1
        for base_index, base in enumerate(self.reference_bases):
            parent: QTreeWidgetItem = self.reference_tree.invisibleRootItem()
            if grouped:
                parent = QTreeWidgetItem([base.path.stem, "", "", "", base.path.name])
                parent.setExpanded(True)
                self.reference_tree.addTopLevelItem(parent)
            if term:
                codes = [
                    code
                    for code, concept in base.concepts.items()
                    if concept.ctype == "0"
                    and not is_chapter_code(code)
                    and term in f"{code} {concept.summary}".lower()
                ][:500]
                for code in codes:
                    self._reference_item(parent, base_index, code, None)
                continue
            for code in base.root_codes:
                self._insert_reference_branch(parent, base_index, base, code)
        label = ", ".join(base.path.stem for base in self.reference_bases) or "Sin cargar"
        self.reference_heading.set_title(label)

    def _refresh_reference_resources(self, term: str) -> None:
        """Muestra usos de un tipo de recurso con capítulo y unidad de obra."""
        count = 0
        for base_index, base in enumerate(self.reference_bases):
            reached: set[str] = set()

            def visit(
                code: str,
                chapter: str = "",
                work_item: str = "",
                quantity: float | None = None,
                path: tuple[str, ...] = (),
                inherited_match: bool = False,
            ):
                """Recorre cada uso sin seguir ciclos."""
                nonlocal count
                if code in path or code not in base.concepts or count >= 500:
                    return
                reached.add(code)
                concept = base.concepts[code]
                matches = inherited_match or not term or term in (
                    f"{code} {concept.summary} {concept.text}".lower()
                )
                if is_chapter_code(code):
                    chapter = (
                        f"{format_chapter_code(code)} · {concept.summary}"
                    )
                elif concept.ctype == "0" and not work_item:
                    work_item = f"{code} · {concept.summary}"
                if (
                    concept.ctype == self.reference_resource_filter
                    and not is_chapter_code(code)
                    and matches
                ):
                    parent = self.reference_tree.invisibleRootItem()
                    label = " · ".join(
                        value
                        for value in (
                            chapter, work_item, f"{code} · {concept.summary}"
                        )
                        if value
                    )
                    self._reference_item(
                        parent, base_index, code, quantity, label=label
                    )
                    count += 1
                    if count == 500:
                        self._set_status(
                            "Se muestran los primeros 500 recursos. Acota la búsqueda."
                        )
                for child, child_quantity in concept.children:
                    visit(
                        child,
                        chapter,
                        work_item,
                        child_quantity,
                        (*path, code),
                        matches,
                    )

            roots = [
                code for code in base.concepts
                if not any(
                    child == code
                    for concept in base.concepts.values()
                    for child, _quantity in concept.children
                )
            ]
            for root in (*base.root_codes, *roots, *base.concepts):
                if root not in reached:
                    visit(root)
        self.reference_heading.set_title(
            CONCEPT_TYPES[self.reference_resource_filter]
        )

    def toggle_reference_resource_filter(self, kind: str) -> None:
        """Alterna el filtro exclusivo de naturaleza de las bases."""
        self.reference_resource_filter = (
            "" if self.reference_resource_filter == kind else kind
        )
        for code, button in self.reference_filter_buttons.items():
            button.setChecked(code == self.reference_resource_filter)
        self.refresh_reference_tree()

    def _insert_reference_branch(
        self, parent: QTreeWidgetItem, base_index: int, base: Bc3Dataset, code: str
    ) -> QTreeWidgetItem:
        """Inserta un concepto y sus descendientes de capítulo."""
        item = self._reference_item(parent, base_index, code, None)
        if is_chapter_code(code):
            for child in base.get_children(code):
                if is_chapter_code(child):
                    self._insert_reference_branch(item, base_index, base, child)
                else:
                    concept = base.concepts.get(child)
                    if concept and concept.ctype == "0":
                        self._reference_item(item, base_index, child, None)
        return item

    def _reference_item(
        self,
        parent: QTreeWidgetItem,
        base_index: int,
        code: str,
        quantity: float | None,
        *,
        label: str | None = None,
    ) -> QTreeWidgetItem:
        """Crea una fila de referencia con sus metadatos de selección."""
        concept = self.reference_bases[base_index].concepts.get(code, Concept(code))
        item = QTreeWidgetItem(parent)
        item.setText(
            0, label or f"{code} · {concept.summary}".strip(" ·")
        )
        item.setText(1, fmt_number(quantity, 4) + f" {concept.unit}".strip() if quantity is not None else concept.unit)
        item.setText(2, fmt_number(self.reference_bases[base_index].calculate_rendimiento(code), 4)
                     if concept.ctype == "0" else "")
        item.setText(3, fmt_number(concept.price))
        item.setText(4, self.reference_bases[base_index].path.stem)
        item.setData(0, ROW_KIND, "reference")
        item.setData(0, ROW_DATA, (base_index, code))
        if parent is self.reference_tree.invisibleRootItem():
            self.reference_tree.addTopLevelItem(item)
        return item

    def on_reference_selection(self) -> None:
        """Sincroniza la referencia seleccionada con el cuadro de detalles."""
        items = self.reference_tree.selectedItems()
        data = items[0].data(0, ROW_DATA) if items else None
        self.selected_reference = None
        if not data:
            self.reference_details.clear()
            return
        base_index, code = data
        base = self.reference_bases[base_index]
        concept = base.concepts.get(code)
        if concept is None:
            self.reference_details.clear()
            return
        self.selected_reference = (base_index, code)
        lines = [
            f"Base: {base.path.stem}",
            f"Código: {concept.code}",
            f"Unidad: {concept.unit or '-'}",
            f"Precio: {self._format_currency(concept.price)}",
            f"Rendimiento: {fmt_number(base.calculate_rendimiento(code), 4)} h",
            "",
            concept.text or concept.summary or "Sin texto descriptivo.",
        ]
        if concept.children:
            lines.extend(("", "Descomposición:"))
            for child_code, quantity in concept.children:
                child = base.concepts.get(child_code, Concept(child_code))
                amount = f"{fmt_number(quantity, 4)} {child.unit}".rstrip()
                lines.append(
                    f"  {child_code}: {child.summary or child_code} | "
                    f"{amount} × {self._format_currency(child.price)}"
                )
        self.reference_details.setPlainText("\n".join(lines))

    def add_selected_reference(self, _item=None, _column=None) -> None:
        """Añade al presupuesto la partida o capítulo de referencia seleccionado."""
        if self.selected_reference is None:
            QMessageBox.information(self, "Sin selección", "Selecciona una partida o capítulo.")
            return
        base_index, code = self.selected_reference
        base = self.reference_bases[base_index]
        if is_chapter_code(code):
            self._add_reference_chapter(base, code)
        else:
            item = self._build_item_from_reference(base, code)
            if item is not None:
                self._insert_item(item)
                self._mark_dirty()
                self._set_status(f"Añadida la partida {item.code}.")

    def _build_item_from_reference(self, base: Bc3Dataset, code: str) -> ProjectItem | None:
        """Construye una partida editable desde una unidad de obra BCCA."""
        concept = base.concepts.get(code)
        if concept is None or concept.ctype != "0" or is_chapter_code(code):
            QMessageBox.warning(self, "Selección no válida", "Selecciona una partida.")
            return None
        return ProjectItem(
            code=code,
            chapter=base.find_parent_chapter(code),
            unit=concept.unit,
            summary=concept.summary,
            measurement=1.0,
            unit_price=concept.price,
            rendimiento=base.calculate_rendimiento(code),
            source="BCCA",
            reference_base=base.path.stem,
            text=concept.text,
            decomposition=list(concept.children),
            measurement_lines=[MeasurementLine(comment="Medición inicial", units=1.0)],
        )

    def _add_reference_chapter(self, base: Bc3Dataset, code: str) -> None:
        """Añade todas las unidades de obra descendientes del capítulo."""
        added = 0
        seen: set[str] = set()

        def collect(chapter: str) -> None:
            """Recorre capítulos sin repetir códigos."""
            nonlocal added
            if chapter in seen:
                return
            seen.add(chapter)
            for child_code in base.get_children(chapter):
                if is_chapter_code(child_code):
                    collect(child_code)
                    continue
                item = self._build_item_from_reference(base, child_code)
                if item is not None:
                    item.chapter = code
                    item.measurement = 1.0
                    self._insert_item(item)
                    added += 1

        collect(code)
        if added:
            self._ensure_chapter(code)
            self.chapter_titles.setdefault(code, base.concepts[code].summary)
            self.refresh_budget_tree()
            self._mark_dirty()
            self._set_status(f"Añadidas {added} partidas de {format_chapter_code(code)}.")

    def refresh_budget_tree(self) -> None:
        """Reconstruye capítulos y partidas manteniendo la selección visible."""
        self.budget_tree.clear()
        term = self.budget_search.text().strip().lower()
        if self.budget_resource_filter:
            self._refresh_budget_resources(term)
            self.budget_refreshed.emit()
            return
        chapter_items: dict[str, QTreeWidgetItem] = {}
        total = 0.0
        for chapter in self.chapter_order:
            branch = QTreeWidgetItem((
                format_chapter_code(chapter),
                CONCEPT_TYPES["chapter"],
                self._chapter_title(chapter),
                "",
                self._format_currency(sum(i.import_total for i in self.budget_items if i.chapter == chapter)),
            ))
            branch.setData(0, ROW_KIND, "chapter")
            branch.setData(0, ROW_DATA, chapter)
            branch.setExpanded(True)
            self.budget_tree.addTopLevelItem(branch)
            chapter_items[chapter] = branch
        for index, item in enumerate(self.budget_items):
            self._ensure_chapter(item.chapter)
            if item.chapter not in chapter_items:
                branch = QTreeWidgetItem((
                    format_chapter_code(item.chapter), CONCEPT_TYPES["chapter"],
                    self._chapter_title(item.chapter), "", "",
                ))
                branch.setData(0, ROW_KIND, "chapter")
                branch.setData(0, ROW_DATA, item.chapter)
                branch.setExpanded(True)
                self.budget_tree.addTopLevelItem(branch)
                chapter_items[item.chapter] = branch
            own_text = f"{item.code} {item.summary} {item.text} {item.chapter}".lower()
            dataset = self._component_dataset(item)
            resources = (
                item.cost_components(dataset, strict=False)
                + item.indirect_components()
            )
            resource_matches = [
                (concept, quantity, price)
                for concept, quantity, price in resources
                if not term
                or term in f"{concept.code} {concept.summary}".lower()
            ]
            matches = not term or term in own_text or bool(resource_matches)
            if not matches:
                continue
            row = QTreeWidgetItem(chapter_items[item.chapter], (
                item.code,
                CONCEPT_TYPES[self._budget_concept_type(item)],
                item.summary,
                f"{fmt_number(item.measurement)} {item.unit}".strip(),
                self._format_currency(item.import_total),
            ))
            row.setData(0, ROW_KIND, "item")
            row.setData(0, ROW_DATA, index)
            if item.code.endswith(DUPLICATE_ITEM_MARKER):
                row.setForeground(0, Qt.GlobalColor.darkYellow)
            row.setExpanded(False)
            for concept, quantity, price in resource_matches:
                component = QTreeWidgetItem(row, (
                    concept.code,
                    CONCEPT_TYPES[self._budget_concept_type(item, concept.code)],
                    concept.summary or concept.code,
                    f"{fmt_number(quantity, 4)} {concept.unit}".strip(),
                    self._format_currency(
                        round_half_up(
                            Decimal(str(quantity)) * Decimal(str(price))
                        )
                    ),
                ))
                component.setData(0, ROW_KIND, "resource")
                component.setData(0, ROW_DATA, index)
            total += item.import_total
        if term:
            for chapter in self.chapter_order:
                branch = chapter_items.get(chapter)
                if branch is not None:
                    branch.setHidden(branch.childCount() == 0)
            self.summary_label.setText(
                f"Total presupuesto: {self._format_currency(sum(i.import_total for i in self.budget_items))}"
            )
        else:
            self.summary_label.setText(f"Total presupuesto: {self._format_currency(total)}")
        self._refresh_item_lock_icons()
        self._update_actions()
        self.budget_refreshed.emit()

    def _refresh_budget_resources(self, term: str) -> None:
        """Lista recursos filtrados con capítulo y partida como contexto."""
        count = 0
        kind = self.budget_resource_filter
        for index, item in enumerate(self.budget_items):
            dataset = self._component_dataset(item)
            resources = (
                item.cost_components(dataset, strict=False)
                + item.indirect_components()
            )
            item_context = (
                f"{format_chapter_code(item.chapter)} · "
                f"{self._chapter_title(item.chapter)} · "
                f"{item.code} · {item.summary}"
            ).lower()
            for concept, quantity, price in resources:
                if self._budget_concept_type(item, concept.code) != kind:
                    continue
                searchable = (
                    f"{item_context} {concept.code} "
                    f"{concept.summary} {concept.text}"
                )
                if term and term not in searchable.lower():
                    continue
                row = QTreeWidgetItem(self.budget_tree, (
                    f"{concept.code} · {concept.summary or concept.code}",
                    CONCEPT_TYPES[kind],
                    (
                        f"{format_chapter_code(item.chapter)} · "
                        f"{self._chapter_title(item.chapter)} · "
                        f"{item.code} · {item.summary}"
                    ),
                    f"{fmt_number(quantity, 4)} {concept.unit}".strip(),
                    self._format_currency(
                        round_half_up(
                            Decimal(str(quantity)) * Decimal(str(price))
                        )
                    ),
                ))
                row.setData(0, ROW_KIND, "resource")
                row.setData(0, ROW_DATA, index)
                count += 1
                if count >= 500:
                    self._set_status(
                        "Se muestran los primeros 500 recursos. Acota la búsqueda."
                    )
                    break
            if count >= 500:
                break
        self.summary_label.setText(
            f"Recursos de {CONCEPT_TYPES[kind]}: {count} · "
            f"Total presupuesto: "
            f"{self._format_currency(sum(i.import_total for i in self.budget_items))}"
        )
        self._update_actions()

    def toggle_budget_resource_filter(self, kind: str) -> None:
        """Alterna el filtro exclusivo de recursos del presupuesto."""
        self.budget_resource_filter = (
            "" if self.budget_resource_filter == kind else kind
        )
        for code, button in self.budget_filter_buttons.items():
            button.setChecked(code == self.budget_resource_filter)
        self.refresh_budget_tree()

    def on_budget_selection(self) -> None:
        """Sincroniza la selección con los paneles de detalle."""
        item = self.budget_tree.currentItem()
        if item is None:
            return
        kind = item.data(0, ROW_KIND)
        data = item.data(0, ROW_DATA)
        if kind == "chapter":
            self.selected_chapter = str(data)
            self.selected_budget_index = None
            self._show_item_details(None)
        elif kind in {"item", "resource"}:
            self.selected_chapter = None
            self.selected_budget_index = int(data)
            self._show_item_details(self.budget_items[self.selected_budget_index])

    def _show_item_details(self, item: ProjectItem | None) -> None:
        """Actualiza mediciones, descomposición y descripción."""
        self.measurement_table.blockSignals(True)
        self.description.blockSignals(True)
        self.measurement_table.setRowCount(0)
        self.component_table.setRowCount(0)
        self.description.clear()
        if item is None:
            self.measurement_total.setText("Sin partida seleccionada.")
            self.description.setEnabled(False)
        else:
            self.description.setEnabled(True)
            self.description.setPlainText(item.text or item.summary)
            for line in item.measurement_lines:
                row = self.measurement_table.rowCount()
                self.measurement_table.insertRow(row)
                values = (
                    line.comment, line.units, line.length, line.width,
                    line.height, line.k, line.partial,
                )
                for column, value in enumerate(values):
                    self.measurement_table.setItem(
                        row, column, QTableWidgetItem("" if value is None else str(value))
                    )
            dataset = self._component_dataset(item)
            for concept, quantity, price in item.cost_components(dataset, strict=False) + item.indirect_components():
                row = self.component_table.rowCount()
                self.component_table.insertRow(row)
                values = (
                    concept.code, concept.summary, fmt_number(quantity, 4), fmt_number(price),
                    self._format_currency(round_half_up(Decimal(str(quantity)) * Decimal(str(price)))),
                )
                for column, value in enumerate(values):
                    self.component_table.setItem(row, column, QTableWidgetItem(value))
            self.measurement_total.setText(f"Total: {fmt_number(item.measurement, 4)} {item.unit}")
        self.description.blockSignals(False)
        self.measurement_table.blockSignals(False)

    def on_description_changed(self) -> None:
        """Guarda la descripción editada de la partida seleccionada."""
        if self.selected_budget_index is None:
            return
        value = self.description.toPlainText()
        if self.budget_items[self.selected_budget_index].text != value:
            self.budget_items[self.selected_budget_index].text = value
            self._mark_dirty()

    def add_chapter(self) -> None:
        """Solicita el código y nombre de un capítulo nuevo."""
        from PySide6.QtWidgets import QInputDialog

        code, accepted = QInputDialog.getText(self, "Añadir capítulo", "Código del capítulo:")
        if not accepted:
            return
        code = code.strip()
        if code and not code.endswith("#"):
            code += "#"
        if not code:
            QMessageBox.warning(self, "Código requerido", "Indica un código para el capítulo.")
            return
        if code in self.chapter_order:
            QMessageBox.warning(self, "Capítulo existente", "Ese capítulo ya existe.")
            return
        title, accepted = QInputDialog.getText(
            self, "Añadir capítulo", "Nombre del capítulo:", text=format_chapter_code(code)
        )
        if not accepted or not title.strip():
            return
        self.chapter_order.append(code)
        self.chapter_titles[code] = title.strip()
        self.refresh_budget_tree()
        self._mark_dirty()

    def add_manual_item(self) -> None:
        """Abre el formulario para una partida manual."""
        editor = ItemEditor(self, chapters=tuple(self.chapter_order))
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        values = editor.values()
        if not values["chapter"] or not values["code"] or not values["summary"]:
            QMessageBox.warning(self, "Datos requeridos", "Indica capítulo, código y resumen.")
            return
        item = ProjectItem(
            code=str(values["code"]),
            chapter=str(values["chapter"]),
            unit=str(values["unit"]),
            summary=str(values["summary"]),
            measurement=float(values["measurement"]),
            unit_price=float(values["price"]),
            rendimiento=float(values["rendimiento"]),
            source="manual",
        )
        self._insert_item(item)
        self._mark_dirty()

    def _insert_item(self, item: ProjectItem, position: int | None = None) -> None:
        """Inserta la partida en memoria y refresca su selección."""
        self._ensure_chapter(item.chapter)
        if position is None:
            positions = [index for index, current in enumerate(self.budget_items) if current.chapter == item.chapter]
            position = positions[-1] + 1 if positions else len(self.budget_items)
        self.budget_items.insert(position, item)
        self.selected_budget_index = position
        self.selected_chapter = None
        self.refresh_budget_tree()
        self._select_budget_item(position)
        self._show_item_details(item)

    def _select_budget_item(self, index: int) -> None:
        """Selecciona una fila por el índice estable de la lista."""
        iterator = QTreeWidgetItemIterator(self.budget_tree)
        while iterator.value():
            row = iterator.value()
            if row.data(0, ROW_KIND) == "item" and row.data(0, ROW_DATA) == index:
                self.budget_tree.setCurrentItem(row)
                break
            iterator += 1

    def edit_selection(self, *_args) -> None:
        """Edita el nombre del capítulo o los datos de la partida."""
        if self.selected_chapter is not None:
            self.rename_chapter()
            return
        if self.selected_budget_index is None:
            QMessageBox.information(self, "Sin selección", "Selecciona una partida.")
            return
        item = self.budget_items[self.selected_budget_index]
        if not self._acquire_item_lock(item):
            return
        editor = ItemEditor(self, item, tuple(self.chapter_order))
        editor.finished.connect(
            lambda _result, storage_id=item.storage_id:
                self._release_item_lock(storage_id)
        )
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        values = editor.values()
        if not values["chapter"] or not values["code"] or not values["summary"]:
            QMessageBox.warning(self, "Datos requeridos", "Indica capítulo, código y resumen.")
            return
        item.chapter = str(values["chapter"])
        self._ensure_chapter(item.chapter)
        item.code = str(values["code"])
        item.unit = str(values["unit"])
        item.summary = str(values["summary"])
        item.measurement = float(values["measurement"])
        item.rendimiento = float(values["rendimiento"])
        item.set_indirect_costs(self.report_settings.indirect_costs, float(values["price"]))
        if item.measurement_lines:
            item.recalculate_measurement()
        self.refresh_budget_tree()
        self._show_item_details(item)
        self._mark_dirty()

    def rename_chapter(self) -> None:
        """Cambia el código y título del capítulo seleccionado."""
        from PySide6.QtWidgets import QInputDialog

        chapter = self.selected_chapter
        if chapter is None:
            return
        code, accepted = QInputDialog.getText(
            self, "Editar capítulo", "Código:", text=chapter
        )
        if not accepted or not code.strip():
            return
        code = code.strip()
        if not code.endswith("#"):
            code += "#"
        if code != chapter and code in self.chapter_order:
            QMessageBox.warning(self, "Capítulo existente", "Ya existe ese código.")
            return
        title, accepted = QInputDialog.getText(
            self, "Editar capítulo", "Nombre:", text=self._chapter_title(chapter)
        )
        if not accepted or not title.strip():
            return
        position = self.chapter_order.index(chapter)
        self.chapter_order[position] = code
        for item in self.budget_items:
            if item.chapter == chapter:
                item.chapter = code
        self.chapter_titles.pop(chapter, None)
        self.chapter_titles[code] = title.strip()
        self.selected_chapter = code
        self.refresh_budget_tree()
        self._mark_dirty()

    def delete_selection(self) -> None:
        """Elimina la partida o capítulo seleccionado."""
        if self.selected_chapter is not None:
            chapter = self.selected_chapter
            answer = QMessageBox.question(
                self, "Eliminar capítulo",
                f"¿Eliminar {format_chapter_code(chapter)} y todas sus partidas?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.budget_items = [item for item in self.budget_items if item.chapter != chapter]
            self.chapter_order.remove(chapter)
            self.chapter_titles.pop(chapter, None)
            self.selected_chapter = None
        elif self.selected_budget_index is not None:
            item = self.budget_items[self.selected_budget_index]
            answer = QMessageBox.question(
                self, "Eliminar partida", f"¿Eliminar {item.code} — {item.summary}?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            del self.budget_items[self.selected_budget_index]
            self.selected_budget_index = None
        else:
            QMessageBox.information(self, "Sin selección", "Selecciona una partida o capítulo.")
            return
        self.refresh_budget_tree()
        self._show_item_details(None)
        self._mark_dirty()

    def duplicate_selection(self) -> None:
        """Duplica una partida y la marca para revisión manual."""
        if self.selected_budget_index is None:
            QMessageBox.information(self, "Sin selección", "Selecciona una partida.")
            return
        source = self.budget_items[self.selected_budget_index]
        duplicate = deepcopy(source)
        duplicate.storage_id = uuid4().hex
        existing = {item.code for item in self.budget_items}
        duplicate.code = f"{source.code}{DUPLICATE_ITEM_MARKER}"
        while duplicate.code in existing:
            duplicate.code += DUPLICATE_ITEM_MARKER
        duplicate.planned_amounts = {}
        duplicate.certifications = {}
        duplicate.certification_plans = {}
        duplicate.certification_justifications = {}
        self._insert_item(duplicate, self.selected_budget_index + 1)
        self._mark_dirty()
        QMessageBox.warning(
            self, "Revisar partida duplicada",
            f"La copia {duplicate.code} requiere revisión y edición manual."
        )

    def move_selection(self, direction: int) -> None:
        """Sube o baja la partida o capítulo seleccionado."""
        if self.selected_chapter is not None:
            index = self.chapter_order.index(self.selected_chapter)
            target = index + direction
            if 0 <= target < len(self.chapter_order):
                self.chapter_order[index], self.chapter_order[target] = (
                    self.chapter_order[target], self.chapter_order[index],
                )
                self.budget_items = [
                    item
                    for chapter in self.chapter_order
                    for item in self.budget_items
                    if item.chapter == chapter
                ]
        elif self.selected_budget_index is not None:
            index = self.selected_budget_index
            chapter = self.budget_items[index].chapter
            indices = [i for i, item in enumerate(self.budget_items) if item.chapter == chapter]
            position = indices.index(index)
            target_position = position + direction
            if 0 <= target_position < len(indices):
                target = indices[target_position]
                self.budget_items[index], self.budget_items[target] = (
                    self.budget_items[target], self.budget_items[index]
                )
                self.selected_budget_index = target
            else:
                return
        else:
            return
        self.refresh_budget_tree()
        self._mark_dirty()

    def toggle_budget_expansion(self) -> None:
        """Expande o contrae todas las ramas visibles del presupuesto."""
        expand = not self._all_branches_expanded()
        for index in range(self.budget_tree.topLevelItemCount()):
            branch = self.budget_tree.topLevelItem(index)
            if branch.childCount():
                branch.setExpanded(expand)

    def _all_branches_expanded(self) -> bool:
        """Comprueba si todos los capítulos están desplegados."""
        branches = [
            self.budget_tree.topLevelItem(index)
            for index in range(self.budget_tree.topLevelItemCount())
            if self.budget_tree.topLevelItem(index).childCount()
        ]
        return bool(branches) and all(branch.isExpanded() for branch in branches)

    def add_measurement(self) -> None:
        """Añade una línea de medición a la partida seleccionada."""
        self._edit_measurement(None)

    def edit_measurement(self, *_args) -> None:
        """Edita la línea de medición activa."""
        row = self.measurement_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Sin selección", "Selecciona una medición.")
            return
        self._edit_measurement(row)

    def _edit_measurement(self, row: int | None) -> None:
        """Muestra el formulario de una medición nueva o existente."""
        if self.selected_budget_index is None:
            QMessageBox.information(self, "Sin selección", "Selecciona una partida.")
            return
        item = self.budget_items[self.selected_budget_index]
        if not self._acquire_item_lock(item, "measurements"):
            return
        line = item.measurement_lines[row] if row is not None else None
        editor = MeasurementEditor(self, line)
        editor.finished.connect(
            lambda _result, storage_id=item.storage_id:
                self._release_item_lock(storage_id, "measurements")
        )
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = editor.result_line()
        except ValueError as exc:
            QMessageBox.warning(self, "Dato inválido", str(exc))
            return
        if row is None:
            item.measurement_lines.append(result)
        else:
            result.ifc_global_id = item.measurement_lines[row].ifc_global_id
            item.measurement_lines[row] = result
        item.recalculate_measurement()
        self.refresh_budget_tree()
        self._show_item_details(item)
        self._mark_dirty()

    def delete_measurement(self) -> None:
        """Elimina la línea de medición seleccionada."""
        if self.selected_budget_index is None:
            return
        row = self.measurement_table.currentRow()
        if row < 0:
            return
        answer = QMessageBox.question(self, "Eliminar medición", "¿Eliminar esta línea?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        item = self.budget_items[self.selected_budget_index]
        del item.measurement_lines[row]
        if item.measurement_lines:
            item.recalculate_measurement()
        self._show_item_details(item)
        self.refresh_budget_tree()
        self._mark_dirty()

    def open_project(self) -> None:
        """Selecciona y abre un proyecto JSON histórico para migrarlo."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importar proyecto JSON histórico",
            "",
            "Proyecto JSON (*.json)",
        )
        if path and self._confirm_project_change():
            self._load_project_file(Path(path))

    def open_collaborative_project(self) -> None:
        """Selecciona una carpeta colaborativa y carga su manifiesto."""
        path = QFileDialog.getExistingDirectory(
            self, "Abrir proyecto colaborativo", ""
        )
        if path and self._confirm_project_change():
            self._load_collaborative_project(Path(path))

    def _load_project_file(
        self,
        project_path: Path,
        budget_dataset: Bc3Dataset | None = None,
        source_budget_path: Path | None = None,
    ) -> bool:
        """Valida y restaura un proyecto JSON."""
        from tools.price_justification import restore_project_state
        from tools.rcd import restore_project_state as restore_rcd_state
        from tools.safety_health import restore_project_state as restore_safety_state
        from tools.pptp import restore_project_state as restore_pptp_state
        from tools.invoicing import restore_project_state as restore_invoice_state

        try:
            document = read_project(
                project_path,
                self.default_report_settings,
                budget_dataset=budget_dataset,
                source_budget_path=source_budget_path,
            )
            price_justification_state = restore_project_state(
                document.price_justification
            )
            rcd_state = restore_rcd_state(document.rcds)
            safety_state = restore_safety_state(document.safety_health)
            pptp_state = restore_pptp_state(document.pptp)
            invoicing_state = restore_invoice_state(document.invoicing)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            QMessageBox.critical(
                self, "No se pudo abrir el proyecto", f"El archivo no es válido:\n{exc}"
            )
            return False
        if not self._dismiss_project_windows():
            return False
        self.budget_dataset = document.budget_dataset
        self.budget_items = document.items
        self.chapter_order = document.chapter_order
        self.chapter_titles = document.chapter_titles
        self.report_settings = document.settings
        self.project_path = project_path
        self.project_store = None
        self._stop_collaboration()
        self.chapter_ids = document.chapter_ids or {}
        self.source_budget_path = document.source_budget_path
        self.project_title = document.title
        self.planning_state = document.planning
        self.invoicing_state = invoicing_state
        self.rcd_state = rcd_state
        self.safety_health_state = safety_state
        self.pptp_state = pptp_state
        self.price_justification_state = price_justification_state
        self.quality_assurance_state = document.quality_assurance
        self.selected_budget_index = None
        self.selected_chapter = None
        self.refresh_budget_tree()
        self._show_item_details(None)
        self._reset_history()
        self._remember_recent_project(project_path)
        if document.missing_source is not None:
            QMessageBox.warning(
                self,
                "BC3 de origen no encontrado",
                f"El proyecto se ha abierto, pero no se encuentra:\n{document.missing_source}",
            )
        self._set_status(f"Proyecto abierto: {project_path.name}")
        return True

    def _load_collaborative_project(self, path: Path) -> bool:
        """Abre una carpeta dividida e inicia vigilancia de cambios externos."""
        root = project_directory(path)
        if root is None:
            QMessageBox.critical(
                self, "Proyecto colaborativo", "La carpeta no contiene un manifiesto válido."
            )
            return False
        crypto = None
        metadata = encrypted_manifest_metadata(root)
        if metadata is not None:
            password, accepted = QInputDialog.getText(
                self,
                "Proyecto colaborativo",
                "Contraseña compartida:",
                echo=QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return False
            try:
                crypto = ProjectCrypto.from_metadata(password, metadata)
            except ValueError as exc:
                QMessageBox.critical(self, "Contraseña no válida", str(exc))
                return False
        store = CollaborativeProjectStore(
            root, self.default_report_settings, crypto
        )
        try:
            document = store.load()
        except InvalidProjectPasswordError as exc:
            QMessageBox.critical(self, "Contraseña incorrecta", str(exc))
            return False
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, "No se pudo abrir el proyecto", str(exc)
            )
            return False
        self.project_session += 1
        self._stop_collaboration()
        self.project_store = store
        self.project_path = root
        self.budget_dataset = document.budget_dataset
        self.budget_items = document.items
        self.chapter_order = document.chapter_order
        self.chapter_titles = document.chapter_titles
        self.chapter_ids = document.chapter_ids or {}
        self.report_settings = document.settings
        self.source_budget_path = document.source_budget_path
        self.project_title = document.title
        self.planning_state = document.planning
        self.invoicing_state = document.invoicing
        self.rcd_state = document.rcds
        self.safety_health_state = document.safety_health
        self.pptp_state = document.pptp
        self.price_justification_state = document.price_justification
        self.quality_assurance_state = document.quality_assurance
        self.selected_budget_index = None
        self.selected_chapter = None
        self.refresh_budget_tree()
        self._show_item_details(None)
        self._reset_history()
        self._remember_recent_project(root)
        self._start_collaboration()
        self._set_status(f"Proyecto colaborativo abierto: {root}")
        return True

    def save_project(self) -> bool:
        """Guarda en la ruta actual o solicita una nueva."""
        if self.project_path is None:
            return self.save_project_as()
        if self.project_store is not None or project_directory(self.project_path):
            return self._write_collaborative_project(
                self.project_store.root if self.project_store else self.project_path
            )
        return self._write_project_file(self.project_path)

    def save_project_as(self) -> bool:
        """Selecciona una carpeta para crear el formato colaborativo."""
        path = QFileDialog.getExistingDirectory(
            self,
            translate("Selecciona una carpeta para el proyecto colaborativo", source_language="es"),
            "",
        )
        if not path:
            return False
        target = Path(path)
        if target.exists() and any(target.iterdir()) and project_directory(target) is None:
            answer = QMessageBox.question(
                self,
                "Carpeta no vacía",
                "La carpeta contiene archivos. ¿Crear dentro la estructura del proyecto?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        return self._write_collaborative_project(target)

    def _open_tool(self, name: str) -> None:
        """Ejecuta la versión Qt de una herramienta descubierta."""
        tool = self.tools_by_name.get(name)
        if tool is None or tool.open_qt is None:
            QMessageBox.warning(self, "Herramientas", "La herramienta no está disponible.")
            return
        try:
            tool.open_qt(self)
        except (ImportError, OSError, ValueError, TypeError, AttributeError) as exc:
            QMessageBox.critical(
                self,
                tool.label,
                f"No se pudo abrir la herramienta:\n{exc}",
            )

    def _collect_tool_states(self) -> bool:
        """Valida herramientas abiertas antes de persistir el proyecto."""
        for tool in self.tools_by_name.values():
            if tool.before_save_qt is None:
                continue
            try:
                tool.before_save_qt(self)
            except (TypeError, ValueError) as exc:
                QMessageBox.critical(
                    self,
                    tool.label,
                    f"No se pudo guardar el proyecto:\n{exc}",
                )
                return False
        return True

    def _store_planning_state(self, state: dict) -> None:
        """Actualiza la planificación compartida con otras herramientas."""
        self.planning_state = state
        self._mark_dirty()

    def _has_external_tool_chapter(self, tool: str) -> bool:
        """Detecta capítulos externos que la herramienta no debe sustituir."""
        prefix = "RCD:" if tool == "rcd" else "SS:"
        chapters = set(self.chapter_order) | set(self.chapter_titles)
        chapters.update(item.chapter for item in self.budget_items)
        for chapter in chapters:
            title = self._chapter_title(chapter)
            normalized = unicodedata.normalize(
                "NFKD", f"{chapter} {title}".casefold()
            )
            text = re.sub(
                r"[^a-z0-9]+",
                " ",
                "".join(
                    char for char in normalized if not unicodedata.combining(char)
                ),
            ).strip()
            code = re.sub(r"[^a-z0-9]", "", chapter.casefold())
            if tool == "rcd":
                matches = (
                    re.search(r"\b(?:rcds?|residuos?|escombros?)\b", text)
                    or re.search(r"\br\s+c\s+d\s*s?\b", text)
                    or re.fullmatch(r"rcds?\d*", code)
                )
            else:
                matches = (
                    re.search(
                        r"\b(?:seguridad|salud)\b|\bprevencion\b.*\briesgos\b|"
                        r"\bs\s+(?:y\s+)?s\b",
                        text,
                    )
                    or re.fullmatch(r"(?:ss|sys)\d*", code)
                )
            members = [
                item for item in self.budget_items if item.chapter == chapter
            ]
            if (
                matches
                and members
                and not any(item.source.startswith(prefix) for item in members)
            ):
                return True
        return False

    def _dismiss_project_windows(self) -> bool:
        """Cierra herramientas vinculadas al proyecto antes de sustituirlo."""
        for window in list(self._project_windows):
            if window.isVisible():
                window.close()
            if window.isVisible():
                return False
        self._project_windows.clear()
        self.project_session += 1
        return True

    def _project_payload(self) -> dict:
        """Construye el formato JSON compatible con el estado Tk."""
        return build_project_payload(self)

    def _write_project_file(self, path: Path) -> bool:
        """Escribe el proyecto mediante sustitución atómica."""
        if not self._collect_tool_states():
            return False
        try:
            write_project(path, self._project_payload())
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "No se pudo guardar el proyecto", str(exc))
            return False
        self.project_path = path
        self._saved_state = deepcopy(self._history_state)
        self.is_dirty = False
        self._update_actions()
        self._remember_recent_project(path)
        self._set_status(f"Proyecto guardado: {path}")
        return True

    def _write_collaborative_project(self, root: Path) -> bool:
        """Guarda el proyecto dividido y activa sincronización incremental."""
        if not self._collect_tool_states():
            return False
        root = root.resolve()
        store = self.project_store
        try:
            if store is None or store.root != root:
                if root.exists() and (root / "proyecto.json").is_file():
                    metadata = encrypted_manifest_metadata(root)
                    if metadata is not None:
                        password, accepted = QInputDialog.getText(
                            self,
                            "Proyecto colaborativo",
                            "Contraseña compartida:",
                            echo=QLineEdit.EchoMode.Password,
                        )
                        if not accepted:
                            return False
                        crypto = ProjectCrypto.from_metadata(password, metadata)
                    else:
                        crypto = None
                else:
                    protected, accepted = self._ask_collaboration_protection()
                    if not accepted:
                        return False
                    if protected:
                        password, accepted = QInputDialog.getText(
                            self,
                            "Proteger proyecto colaborativo",
                            "Nueva contraseña compartida:",
                            echo=QLineEdit.EchoMode.Password,
                        )
                        if not accepted:
                            return False
                        confirmation, accepted = QInputDialog.getText(
                            self,
                            "Proteger proyecto colaborativo",
                            "Repite la contraseña:",
                            echo=QLineEdit.EchoMode.Password,
                        )
                        if not accepted or confirmation != password:
                            QMessageBox.critical(
                                self,
                                "Contraseñas distintas",
                                "Las contraseñas no coinciden.",
                            )
                            return False
                        crypto = ProjectCrypto.create(password)
                    else:
                        crypto = None
                store = CollaborativeProjectStore(
                    root, self.default_report_settings, crypto
                )
            if (root / "proyecto.json").is_file():
                if project_directory(root) is None:
                    raise ValueError(
                        "La carpeta contiene un proyecto.json no compatible."
                    )
                if not store.baseline:
                    store.load()
            store.save(self)
            if store.last_merges:
                merged = store.load()
                self.budget_items = merged.items
                self.chapter_order = merged.chapter_order
                self.chapter_titles = merged.chapter_titles
                self.chapter_ids = merged.chapter_ids or {}
                self.planning_state = merged.planning
                self.invoicing_state = merged.invoicing
                self.rcd_state = merged.rcds
                self.safety_health_state = merged.safety_health
                self.pptp_state = merged.pptp
                self.price_justification_state = merged.price_justification
                self.quality_assurance_state = merged.quality_assurance
                self.report_settings = merged.settings
                self.project_title = merged.title
                self.refresh_budget_tree()
                self._reset_history()
                self._set_status(
                    "Cambios remotos y locales compatibles fusionados."
                )
        except StorageConflictError as exc:
            answer = QMessageBox.question(
                self,
                "Cambios externos detectados",
                f"{exc}\n\n¿Recargar el proyecto y descartar tu copia no guardada "
                "para resolverlos?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._load_collaborative_project(root)
            return False
        except InvalidProjectPasswordError as exc:
            QMessageBox.critical(self, "Contraseña incorrecta", str(exc))
            return False
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(
                self, "No se pudo guardar el proyecto", str(exc)
            )
            return False
        self._stop_collaboration()
        self.project_store = store
        self.project_path = root
        self._saved_state = deepcopy(self._history_state)
        self.is_dirty = False
        self._update_actions()
        self._remember_recent_project(root)
        self._start_collaboration()
        self._set_status(f"Proyecto colaborativo guardado: {root}")
        return True

    def _ask_collaboration_protection(self) -> tuple[bool, bool]:
        """Elige entre carpeta pública y protección con contraseña."""
        selection, accepted = QInputDialog.getItem(
            self,
            "Proyecto colaborativo",
            "Tipo de acceso:",
            ("Público, sin cifrar", "Protegido con contraseña"),
            0,
            False,
        )
        return selection == "Protegido con contraseña", accepted

    def change_collaboration_password(self) -> None:
        """Activa, cambia o retira el cifrado del proyecto."""
        if self.project_store is None:
            QMessageBox.information(
                self,
                "Colaboración",
                "Guarda primero como proyecto colaborativo.",
            )
            return
        protected, accepted = self._ask_collaboration_protection()
        if not accepted:
            return
        if not protected:
            answer = QMessageBox.question(
                self,
                "Proyecto público",
                "¿Retirar el cifrado? Los JSON y bibliotecas quedarán legibles.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.project_store.set_crypto(None)
            try:
                self.project_store.save(self, force=True)
            except (StorageConflictError, OSError, ValueError) as exc:
                QMessageBox.critical(self, "Colaboración", str(exc))
                return
            self._set_status("Proyecto colaborativo convertido a público.")
            return
        password, accepted = QInputDialog.getText(
            self,
            "Cambiar contraseña",
            "Nueva contraseña compartida:",
            echo=QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        confirmation, accepted = QInputDialog.getText(
            self,
            "Cambiar contraseña",
            "Repite la contraseña:",
            echo=QLineEdit.EchoMode.Password,
        )
        if not accepted or confirmation != password:
            QMessageBox.critical(
                self, "Contraseñas distintas", "Las contraseñas no coinciden."
            )
            return
        external = self.project_store.changed_paths()
        if external:
            QMessageBox.critical(
                self,
                "Cambios externos",
                "Recarga el proyecto antes de cambiar la contraseña.",
            )
            return
        new_crypto = ProjectCrypto.create(password)
        self.project_store.set_crypto(new_crypto)
        try:
            self.project_store.save(self, force=True)
        except StorageConflictError as exc:
            QMessageBox.critical(self, "Conflictos externos", str(exc))
            return
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cifrado", str(exc))
            return
        self._set_status("Contraseña del proyecto actualizada.")

    def show_collaboration_status(self) -> None:
        """Resume cifrado, locks y ubicación compartida."""
        if self.project_store is None:
            QMessageBox.information(
                self,
                "Colaboración",
                "El proyecto actual todavía no está guardado como colaborativo.",
            )
            return
        locks = list(
            (self.project_store.root / "partidas").glob("part_*.lock")
        )
        QMessageBox.information(
            self,
            "Estado de colaboración",
            f"Carpeta: {self.project_store.root}\n"
            f"Acceso: "
            f"{'Protegido con contraseña' if self.project_store.is_encrypted else 'Público, sin cifrar'}\n"
            f"Partidas bloqueadas: {len(locks)}\n"
            f"Participantes activos: "
            f"{', '.join(row['nickname'] for row in self.collaborators) or 'ninguno'}\n"
            f"Bibliotecas compartidas: "
            f"{len(list((self.project_store.root / 'bibliotecas').glob('*/*.enc')))}",
        )

    def open_shared_libraries(self) -> None:
        """Elige bibliotecas incluidas o rutas externas por instalación."""
        from tools.price_justification.templates import (
            LIBRARY_PATH as PRICE_LIBRARY,
        )
        from tools.rcd.document import LIBRARY_PATH as RCD_LIBRARY
        from tools.quality_assurance.document import LIBRARY_PATH as QUALITY_LIBRARY
        from tools.safety_health import LIBRARY_PATH as SAFETY_LIBRARY
        from tools.pptp import DOCS_ROOT, LIBRARY_PATH as PPTP_LIBRARY

        libraries = {
            "price_justification": PRICE_LIBRARY,
            "rcd": RCD_LIBRARY,
            "quality_assurance": QUALITY_LIBRARY,
            "safety_health": SAFETY_LIBRARY,
            "pptp": PPTP_LIBRARY,
        }
        dialog = QDialog(self)
        dialog.setWindowTitle("Fuentes de bibliotecas")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "Elige para cada módulo la biblioteca incluida con la aplicación "
            "o una ruta ZIP externa local a este equipo."
        ))
        for module, source in libraries.items():
            row = QHBoxLayout()
            label = QLabel(module.replace("_", " ").title())
            row.addWidget(label, 1)
            selector = QComboBox()
            selector.addItems(("Biblioteca incluida", "Ruta externa"))
            selector.setCurrentText(
                "Ruta externa"
                if self.library_sources.get(module)
                else "Biblioteca incluida"
            )
            path_field = QLineEdit(self.library_sources.get(module, ""))
            path_field.setEnabled(selector.currentIndex() == 1)
            selector.currentIndexChanged.connect(
                lambda index, field=path_field: field.setEnabled(index == 1)
            )
            row.addWidget(selector)
            row.addWidget(path_field, 2)
            choose = QPushButton("Examinar…")

            def choose_file(field=path_field):
                """Selecciona un ZIP local como fuente de biblioteca."""
                path, _ = QFileDialog.getOpenFileName(
                    dialog,
                    "Biblioteca externa",
                    "",
                    "Biblioteca ZIP (*.zip)",
                )
                if path:
                    field.setText(path)

            choose.clicked.connect(choose_file)
            row.addWidget(choose)
            layout.addLayout(row)
            dialog.setProperty(
                f"library_{module}",
                (selector, path_field),
            )
            if module == "pptp":
                docs_row = QHBoxLayout()
                docs_row.addWidget(QLabel("Documentos PPTP"))
                docs_field = QLineEdit(
                    self.library_sources.get("pptp_docs", "")
                )
                docs_row.addWidget(docs_field, 1)
                choose_docs = QPushButton("Carpeta…")

                def choose_docs_folder(field=docs_field):
                    """Selecciona una carpeta docs externa para PPTP."""
                    path = QFileDialog.getExistingDirectory(
                        dialog, "Documentos PPTP", ""
                    )
                    if path:
                        field.setText(path)

                choose_docs.clicked.connect(choose_docs_folder)
                docs_row.addWidget(choose_docs)
                layout.addLayout(docs_row)
                dialog.setProperty("pptp_docs_field", docs_field)
        save = QPushButton("Guardar preferencias")

        def save_preferences() -> None:
            """Persiste biblioteca incluida o ruta externa por módulo."""
            sources = {}
            for module in libraries:
                selector, field = dialog.property(f"library_{module}")
                if selector.currentIndex() == 1:
                    path = field.text().strip()
                    if not path or not Path(path).is_file():
                        QMessageBox.critical(
                            dialog,
                            "Biblioteca no válida",
                            f"Selecciona un ZIP válido para {module}.",
                        )
                        return
                    sources[module] = path
            docs_field = dialog.property("pptp_docs_field")
            if docs_field is not None and docs_field.text().strip():
                if not Path(docs_field.text().strip()).is_dir():
                    QMessageBox.critical(
                        dialog,
                        "Documentos PPTP",
                        "La carpeta de documentos PPTP no es válida.",
                    )
                    return
                sources["pptp_docs"] = docs_field.text().strip()
            self.library_sources = sources
            try:
                self.library_preferences_path.write_text(
                    json.dumps(sources, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                QMessageBox.critical(dialog, "Bibliotecas", str(exc))
                return
            dialog.accept()

        save.clicked.connect(save_preferences)
        layout.addWidget(save)
        close = QPushButton("Cerrar")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _read_library_preferences(self) -> dict[str, str]:
        """Lee rutas externas de bibliotecas configuradas para este equipo."""
        try:
            data = json.loads(
                self.library_preferences_path.read_text(encoding="utf-8")
            )
            if not isinstance(data, dict):
                raise ValueError("Se esperaba un objeto de rutas.")
            return {
                str(key): str(value)
                for key, value in data.items()
                if isinstance(value, str)
            }
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return {}

    def _start_collaboration(self) -> None:
        """Inicia vigilancia y renovación de locks para la carpeta activa."""
        if self.project_store is None:
            return
        self.project_watcher = CollaborativeWatchService(self.project_store)
        self.project_watcher.setParent(self)
        self.project_watcher.files_changed.connect(self._external_files_changed)
        self.project_watcher.presence_changed.connect(
            self._collaborators_changed
        )
        self.project_watcher.start()
        self._lock_timer.start()
        self._presence_timer.start()
        self._publish_presence()

    def _stop_collaboration(self) -> None:
        """Detiene vigilancia y libera bloqueos propios."""
        if self.project_watcher is not None:
            self.project_watcher.leave(self.collaboration_session_id)
            self.project_watcher.stop()
            self.project_watcher = None
        self._lock_timer.stop()
        self._presence_timer.stop()
        self.collaborators = []
        if hasattr(self, "_collaboration_presence"):
            self._collaboration_presence.setVisible(False)
        for handle in self._active_item_locks.values():
            self.lock_manager.release(handle)
        self._active_item_locks.clear()

    def _renew_item_locks(self) -> None:
        """Renueva los locks activos y avisa si otro usuario tomó uno."""
        lost = []
        for lock_key, handle in list(self._active_item_locks.items()):
            renewed, message = self.lock_manager.renew(handle)
            if not renewed:
                lost.append(lock_key)
                QMessageBox.warning(
                    self, "Bloqueo perdido", f"{message}\nGuarda con precaución."
                )
        for lock_key in lost:
            self._active_item_locks.pop(lock_key, None)

    def _publish_presence(self) -> None:
        """Renueva apodo y equipo en la carpeta compartida."""
        if self.project_watcher is not None:
            self.project_watcher.heartbeat(
                self.collaboration_session_id,
                self.collaboration_nickname,
                self.lock_manager.host,
            )

    def _collaborators_changed(self, people: list[dict[str, str]]) -> None:
        """Actualiza contador, apodos y avisos del estado colaborativo."""
        self.collaborators = people
        count = len(people)
        names = ", ".join(
            person["nickname"] for person in people
        ) or "Sin participantes"
        self._collaboration_presence_text.setText(
            f"{count} colaborador{'es' if count != 1 else ''} · {names}"
        )
        self._collaboration_presence.setToolTip(
            "Participantes activos:\n" + "\n".join(
                f"{person['nickname']} ({person['host']})"
                for person in people
            )
        )
        self._collaboration_presence.setVisible(count > 1)

    def _acquire_item_lock(
        self, item: ProjectItem, scope: str = "general"
    ) -> bool:
        """Adquiere el lock de una partida del proyecto colaborativo."""
        if self.project_store is None:
            return True
        handle, message = self.lock_manager.acquire(
            self.project_store.item_path(item.storage_id), scope
        )
        if handle is None:
            QMessageBox.warning(
                self, "Partida en edición", message
            )
            return False
        self._active_item_locks[item.storage_id, scope] = handle
        return True

    def _release_item_lock(
        self, storage_id: str, scope: str = "general"
    ) -> None:
        """Libera el lock de una partida tras cerrar su editor."""
        handle = self._active_item_locks.pop((storage_id, scope), None)
        if handle is not None:
            self.lock_manager.release(handle)

    def _external_files_changed(self, paths: list[Path]) -> None:
        """Recarga partidas remotas sin tocar archivos bloqueados localmente."""
        if self.project_store is None:
            return
        item_updates = []
        structural = False
        locks_changed = False
        for path in paths:
            if path.suffix == ".lock":
                locks_changed = True
                continue
            if path.parent.name == "partidas" and path.suffix == ".json":
                storage_id = path.stem.removeprefix("part_")
                if any(
                    key[0] == storage_id
                    for key in self._active_item_locks
                ):
                    continue
                try:
                    item_updates.append(self.project_store.load_item(storage_id))
                except (OSError, ValueError):
                    structural = True
            elif path.suffix != ".lock":
                structural = True
        if item_updates:
            by_id = {item.storage_id: index for index, item in enumerate(self.budget_items)}
            for item in item_updates:
                index = by_id.get(item.storage_id)
                if index is not None:
                    self.budget_items[index] = item
            self.refresh_budget_tree()
            if not self.is_dirty:
                self._reset_history(saved=True)
            self._set_status("Partidas actualizadas por otro colaborador.")
        if structural:
            self._set_status(
                "El proyecto compartido cambió. Guarda o recarga para resolver la actualización."
            )
        if locks_changed:
            self._refresh_item_lock_icons()

    def _refresh_item_lock_icons(self) -> None:
        """Muestra qué partidas están siendo editadas por otra persona."""
        if self.project_store is None:
            return
        lock_icon = QIcon.fromTheme(
            "system-lock-screen",
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton),
        )
        empty_icon = QIcon()
        locks: dict[str, str] = {}
        folder = self.project_store.root / "partidas"
        if folder.is_dir():
            for path in folder.glob("part_*.lock"):
                storage_id = path.stem.removeprefix("part_").split(".", 1)[0]
                scope = (
                    path.stem.split(".", 1)[1]
                    if "." in path.stem else "general"
                )
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    locks[storage_id] = (
                        f"En edición por {data.get('user', 'otro usuario')} "
                        f"({scope})"
                        f" · {data.get('host', '')}"
                    )
                except (OSError, UnicodeError, json.JSONDecodeError):
                    locks[storage_id] = "Bloqueo no válido; se renovará al editar"
        iterator = QTreeWidgetItemIterator(self.budget_tree)
        while iterator.value():
            row = iterator.value()
            if row.data(0, ROW_KIND) == "item":
                item_index = int(row.data(0, ROW_DATA))
                storage_id = self.budget_items[item_index].storage_id
                row.setIcon(0, lock_icon if storage_id in locks else empty_icon)
                row.setToolTip(0, locks.get(storage_id, ""))
            iterator += 1

    def _confirm_project_change(self) -> bool:
        """Ofrece guardar antes de sustituir o cerrar el proyecto actual."""
        if not self.is_dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Cambios sin guardar",
            "Hay cambios sin guardar. ¿Quieres guardarlos?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        return answer != QMessageBox.StandardButton.Save or self.save_project()

    def _remember_recent_project(self, path: Path) -> None:
        """Añade el proyecto al historial persistente."""
        resolved = path.resolve()
        self.recent_projects = [resolved] + [
            previous for previous in self.recent_projects if previous != resolved
        ][:4]
        self._write_recent_paths(self.recent_projects_path, self.recent_projects)
        self._refresh_recent_menu()

    def _refresh_recent_menu(self) -> None:
        """Reconstruye el menú de proyectos recientes."""
        menu = self.recent_menu
        menu.clear()
        if not self.recent_projects:
            menu.addAction("(No hay proyectos recientes)").setEnabled(False)
            return
        for path in self.recent_projects:
            if path.is_file() or project_directory(path):
                action = menu.addAction(str(path))
                action.triggered.connect(
                    lambda checked=False, selected=path: self._open_recent_project(selected)
                )

    def _refresh_recent_reference_menu(self) -> None:
        """Reconstruye las bases de referencia recientes del menú Archivo."""
        menu = self.recent_reference_menu
        menu.clear()
        if not self.recent_reference_bases:
            menu.addAction("(No hay bases de referencia recientes)").setEnabled(False)
            return
        for path in self.recent_reference_bases:
            if path.is_file():
                action = menu.addAction(str(path))
                action.triggered.connect(
                    lambda checked=False, selected=path: self._load_reference_file(selected)
                )

    def _open_recent_project(self, path: Path) -> None:
        """Abre un proyecto reciente tras proteger cambios pendientes."""
        if self._confirm_project_change():
            if project_directory(path):
                self._load_collaborative_project(path)
            else:
                self._load_project_file(path)

    def _read_recent_paths(self, path: Path, maximum: int) -> list[Path]:
        """Lee rutas absolutas válidas y limita el historial."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("Se esperaba una lista de rutas.")
            return list(dict.fromkeys(Path(value) for value in data if isinstance(value, str)))[:maximum]
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "Historial", f"No se pudo leer el historial:\n{exc}")
            return []

    @staticmethod
    def _write_recent_paths(path: Path, values: list[Path]) -> None:
        """Guarda un historial normalizado sin interrumpir la edición."""
        try:
            path.write_text(
                json.dumps([str(value) for value in values], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            QApplication.instance().beep()
            print(f"No se pudo guardar el historial {path}: {exc}", file=sys.stderr)

    def _read_report_settings(self) -> ReportSettings:
        """Lee la configuración común de informes."""
        try:
            return ReportSettings.from_dict(
                json.loads(self.report_settings_path.read_text(encoding="utf-8"))
            )
        except FileNotFoundError:
            return ReportSettings()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, "Configuración", str(exc))
            return ReportSettings()

    def _read_language_preference(self) -> str:
        """Lee el idioma global conservando español ante datos inválidos."""
        try:
            data = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            code = data.get("language") if isinstance(data, dict) else None
            if code not in LANGUAGES:
                raise ValueError("El idioma guardado no es válido.")
            return str(code)
        except FileNotFoundError:
            return "es"
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "Preferencias de idioma",
                f"No se pudieron leer las preferencias; se usará español:\n{exc}",
            )
            return "es"

    def _read_collaboration_nickname(self) -> str:
        """Lee el apodo global o propone el usuario del sistema."""
        try:
            data = json.loads(
                self.preferences_path.read_text(encoding="utf-8")
            )
            value = data.get("collaboration_nickname") if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()[:40]
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            pass
        return self.lock_manager.user[:40]

    def _read_theme_preference(self) -> str:
        """Lee el tema global y usa Sistema ante datos ausentes o inválidos."""
        try:
            data = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            theme = data.get("theme") if isinstance(data, dict) else None
            return theme if theme in THEMES else "system"
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return "system"

    def _save_language_preference(self, language: str) -> bool:
        """Guarda el idioma global de forma atómica."""
        if language not in LANGUAGES:
            return False
        return self._save_global_preferences(language=language)

    def _save_theme_preference(self, theme: str) -> bool:
        """Guarda y aplica inmediatamente el tema global."""
        if theme not in THEMES:
            return False
        if not self._save_global_preferences(theme=theme):
            return False
        apply_theme(QApplication.instance(), theme)
        self.theme = theme
        self._set_status("Tema de la interfaz cambiado.")
        return True

    def _save_global_preferences(
        self,
        *,
        language: str | None = None,
        theme: str | None = None,
        collaboration_nickname: str | None = None,
    ) -> bool:
        """Actualiza preferencias globales sin borrar las demás claves."""
        try:
            current = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            current = {}
        if language is not None:
            current["language"] = language
        if theme is not None:
            current["theme"] = theme
        if collaboration_nickname is not None:
            current["collaboration_nickname"] = collaboration_nickname
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.preferences_path.parent,
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(current, output, ensure_ascii=False, indent=2)
                output.write("\n")
            os.replace(temporary, self.preferences_path)
            temporary = None
        except OSError as exc:
            QMessageBox.critical(self, "No se pudo guardar la preferencia", str(exc))
            return False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if language is not None:
            self.language = language
            set_language(language)
            apply_button_language(QApplication.instance())
            self._set_status("Idioma cambiado.")
        if collaboration_nickname is not None:
            self.collaboration_nickname = collaboration_nickname
        return True

    def open_report_settings(self) -> None:
        """Abre la configuración general con sus pestañas completas."""
        try:
            dialog = SettingsDialog(
                self,
                self.report_settings,
                self._project_title(),
                self.language,
                self._save_language_preference,
                self.theme,
                self._save_theme_preference,
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Configuración", str(exc))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._save_report_settings(dialog.values()):
            self.open_report_settings()

    def _save_report_settings(self, data: dict) -> bool:
        """Valida, persiste y aplica la configuración del proyecto."""
        try:
            title = data.get("project_title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("El título del proyecto no puede estar vacío.")
            settings = ReportSettings.from_dict({
                key: value for key, value in data.items() if key != "project_title"
            })
            defaults = settings.to_dict()
            defaults.pop("text_styles")
            self.report_settings_path.write_text(
                json.dumps(defaults, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(
                self, "No se pudo guardar la configuración", str(exc)
            )
            return False
        self.default_report_settings = ReportSettings.from_dict(defaults)
        self.report_settings = ReportSettings.from_dict(settings.to_dict())
        self.project_title = title.strip()
        for item in self.budget_items:
            item.set_indirect_costs(settings.indirect_costs)
        self.refresh_budget_tree()
        selected = self.selected_budget_index
        self._show_item_details(
            self.budget_items[selected] if selected is not None else None
        )
        self._mark_dirty()
        if self.project_path is not None and not self.save_project():
            return False
        self._set_status("Configuración de informes guardada.")
        return True

    def _document_export_initial_path(
        self, filename: str, document_section: str,
    ) -> Path | None:
        """Obtiene la ruta inicial y muestra errores del registro documental."""
        try:
            return document_save_initial_path(self, filename, document_section)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Estructura documental",
                f"No se puede consultar la ruta de la entrega documental:\n{exc}",
            )
            return None

    def export_budget_ods(self) -> None:
        """Exporta presupuesto y mediciones a una hoja ODS."""
        if not self.budget_items:
            QMessageBox.information(self, "Presupuesto vacío", "No hay partidas que exportar.")
            return
        initial_path = self._document_export_initial_path("presupuesto.ods", "DOC04")
        if initial_path is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, translate("Exportar presupuesto", source_language="es"),
            str(initial_path), "ODS (*.ods)",
        )
        if not path:
            return
        try:
            write_ods(Path(path), [
                {
                    "name": "Presupuesto",
                    "rows": [
                        ["Capítulo", "Código", "Ud", "Descripción", "Medición", "Precio", "Importe"],
                        *[
                            [
                                format_chapter_code(item.chapter), item.code, item.unit,
                                item.text or item.summary, item.measurement,
                                item.unit_price, item.import_total,
                            ]
                            for item in self.budget_items
                        ],
                    ],
                    "header": True,
                    "number_formats": {4: 4, 5: 2, 6: 2},
                },
                {
                    "name": "Mediciones",
                    "rows": [
                        ["Código", "Comentario", "Uds", "Largo", "Ancho", "Alto", "K", "Parcial"],
                        *[
                            [
                                item.code, line.comment, line.units, line.length,
                                line.width, line.height, line.k, line.partial,
                            ]
                            for item in self.budget_items
                            for line in item.measurement_lines
                        ],
                    ],
                    "header": True,
                    "number_formats": {2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 4},
                },
            ])
        except (OSError, ValueError, ImportError) as exc:
            QMessageBox.critical(self, "No se pudo exportar", str(exc))
            return
        self._set_status(f"Presupuesto exportado a {path}")

    def export_measurements_csv(self) -> None:
        """Exporta las mediciones editables a CSV."""
        if not self.budget_items:
            QMessageBox.information(self, "Presupuesto vacío", "No hay mediciones que exportar.")
            return
        initial_path = self._document_export_initial_path("mediciones.csv", "DOC04")
        if initial_path is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, translate("Exportar mediciones", source_language="es"),
            str(initial_path), "CSV (*.csv)",
        )
        if not path:
            return
        try:
            with Path(path).open("w", encoding="utf-8-sig", newline="") as output:
                import csv

                writer = csv.writer(output, delimiter=";")
                writer.writerow(("Código", "Comentario", "Uds", "Longitud", "Anchura", "Altura", "K", "Parcial"))
                for item in self.budget_items:
                    for line in item.measurement_lines:
                        writer.writerow((
                            item.code, line.comment, line.units, line.length,
                            line.width, line.height, line.k, line.partial,
                        ))
        except OSError as exc:
            QMessageBox.critical(self, "No se pudo exportar", str(exc))
            return
        self._set_status(f"Mediciones exportadas a {path}")

    def export_resource_report(self, report: str) -> None:
        """Exporta recursos o descompuestos del presupuesto a ODS."""
        if not self.budget_items:
            QMessageBox.information(
                self, "Presupuesto vacío", "No hay partidas que exportar."
            )
            return
        initial_path = self._document_export_initial_path(f"{report}.ods", "DOC04")
        if initial_path is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            translate(f"Exportar {report.lower()}", source_language="es"),
            str(initial_path),
            f"{translate('Hoja ODS', source_language='es')} (*.ods)",
        )
        if not path:
            return
        resources = report == "Recursos"
        headers = (
            ["Código", "Tipo", "Ud", "Resumen", "Precio", "Fuente", "Medición"]
            if resources
            else [
                "Partida", "Recurso", "Tipo", "Ud", "Resumen",
                "Rendimiento", "Precio", "Importe", "Fuente",
            ]
        )
        rows = [headers]
        grouped = {}
        try:
            for item in self.budget_items:
                for concept, quantity, price in self._report_components(item):
                    kind = self._budget_concept_type(item, concept.code)
                    type_label = {
                        "%": "%", "1": "MO", "2": "MAQ", "3": "MAT"
                    }.get(kind, "AUX")
                    if resources:
                        key = (
                            concept.code, type_label, concept.unit,
                            concept.summary, price, item.source,
                        )
                        grouped[key] = grouped.get(key, Decimal(0)) + (
                            Decimal(str(item.measurement)) * Decimal(str(quantity))
                        )
                    else:
                        rows.append((
                            item.code,
                            concept.code,
                            type_label,
                            concept.unit,
                            concept.summary,
                            quantity,
                            price,
                            round_half_up(
                                Decimal(str(quantity)) * Decimal(str(price))
                            ),
                            item.source,
                        ))
            if resources:
                rows.extend((*key, float(value)) for key, value in grouped.items())
            write_ods(
                Path(path),
                [{
                    "name": report,
                    "rows": rows,
                    "header": True,
                    "number_formats": (
                        {4: 2, 6: 4} if resources else {5: 4, 6: 2, 7: 2}
                    ),
                }],
            )
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "No se pudo exportar", str(exc))
            return
        self._set_status(f"{report} exportados a {path}")

    def generate_report(self, report: str) -> None:
        """Genera uno de los informes PDF u ODT disponibles."""
        if not self.budget_items:
            QMessageBox.information(self, "Presupuesto vacío", "No hay partidas para informar.")
            return
        selection = get_document_save_path(
            self,
            f"Guardar informe: {report}",
            f"{report}.pdf",
            document_section="DOC04",
        )
        if selection is None:
            return
        target, output_format = selection
        try:
            if output_format == "odt":
                write_report_odt(self, report, target)
            else:
                write_pdf(self, report, target)
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "No se pudo generar el informe", str(exc))
            return
        self._set_status(f"Informe guardado: {target}")
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError:
            pass

    def open_repository(self) -> None:
        """Abre el repositorio del proyecto en el navegador."""
        self._open_web_page(
            REPOSITORY_URL, "Repositorio", "Repositorio abierto en el navegador."
        )

    def open_reference_search(self, kind: str) -> None:
        """Abre o reutiliza cada buscador NTP/UNE."""
        from reference_search_qt import ReferenceSearchWindowQt

        for window in self._project_windows:
            if (
                isinstance(window, ReferenceSearchWindowQt)
                and window.kind == kind
                and window.isVisible()
            ):
                window.raise_()
                return
        try:
            entries = load_reference_entries(kind)
        except ValueError as exc:
            QMessageBox.critical(self, f"Buscador de {kind}", str(exc))
            return
        window = ReferenceSearchWindowQt(self, kind, entries)
        self._project_windows.append(window)
        window.show()

    def _open_web_page(
        self, url: str, title: str, success_status: str
    ) -> bool:
        """Abre una URL HTTPS validada y reporta errores."""
        from urllib.parse import urlsplit

        if urlsplit(url).scheme not in {"http", "https"}:
            QMessageBox.critical(self, title, "La dirección no es válida.")
            return False
        try:
            if not webbrowser.open(url):
                raise OSError("El navegador no aceptó la solicitud.")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self, title, f"No se pudo abrir el navegador:\n{exc}"
            )
            return False
        self._set_status(success_status)
        return True

    def open_donation_page(self) -> None:
        """Abre la página de apoyo al proyecto."""
        self._open_web_page(
            BUY_ME_A_COFFEE_URL,
            "Apoyar el proyecto",
            "Página de apoyo abierta en el navegador.",
        )

    def show_license_info(self) -> None:
        """Muestra licencia MIT y responsabilidad técnica."""
        QMessageBox.information(
            self,
            "Licencia MIT y responsabilidad",
            "Software libre · Licencia MIT\n\n"
            "El software se proporciona «tal cual», sin garantías de ningún tipo. "
            "La persona usuaria debe comprobar datos, cálculos, mediciones, "
            "presupuestos y certificaciones antes de utilizarlos.\n\n"
            "Texto íntegro: https://opensource.org/license/mit",
        )

    def show_about_qt(self) -> None:
        """Muestra el diálogo de información de PySide6."""
        QMessageBox.aboutQt(self, APPLICATION_NAME)
