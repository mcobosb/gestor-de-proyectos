"""Pruebas sin pantalla del frontend principal PySide6."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSplashScreen, QSplitter, QVBoxLayout,
)
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer, QUrl
from PySide6.QtTest import QTest

import qt_app
from budget_core import Bc3Dataset, Concept
from budget_core import ProjectItem
from budget_qt_settings import SettingsDialog
from budget_qt_window import BudgetMainWindow
from budget_qt_theme import apply_theme
from budget_reports import ReportSettings
from localization import get_language, set_language
from qt_localization import apply_button_language, localize_button
from tools._shared.reference_pill import ReferencePill
from tools.price_justification.qt_window import PriceJustificationWindowQt
from tools.rcd import RCD_BUDGET_SOURCE
from tools.rcd.qt_window import RcdWindowQt
from tools.safety_health import proposed_lines, serialize_state
from tools.safety_health.qt_window import (
    ChapterSelectionDialogQt,
    ExternalConditionsDialogQt,
    SafetyHealthWindowQt,
)
from tools.pptp.qt_window import PptpWindowQt
from tools.certifications.qt_window import CertificationWindowQt
from tools.planning.qt_window import PlanningWindowQt
from tools.invoicing import Invoice, InvoiceLine, InvoiceState
from tools.invoicing.qt_window import InvoicingWindowQt
from reference_search import ReferenceEntry, load_reference_entries
from reference_search_qt import ReferenceSearchWindowQt
from decimal import Decimal


ROOT = Path(__file__).resolve().parent.parent


class BudgetQtWindowTests(unittest.TestCase):
    """Comprueba el ciclo mínimo de importación y persistencia Qt."""

    @classmethod
    def setUpClass(cls) -> None:
        """Crea una única aplicación Qt para las ventanas de prueba."""
        cls.application = QApplication.instance() or QApplication([])

    def test_startup_image_has_transparent_corners(self) -> None:
        """Las esquinas son transparentes aunque Windows no redondee la ventana."""
        splash = qt_app.show_startup_splash(self.application)
        self.assertIsNotNone(splash)
        self.addCleanup(splash.close)
        image = splash.pixmap().toImage()
        right, bottom = image.width() - 1, image.height() - 1
        for x, y in ((0, 0), (right, 0), (0, bottom), (right, bottom)):
            self.assertEqual(image.pixelColor(x, y).alpha(), 0)
        self.assertEqual(image.pixelColor(right // 2, bottom // 2).alpha(), 255)
        self.assertTrue(splash.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))

    def test_startup_has_no_overlap_between_splash_and_main_window(self) -> None:
        """Maximiza la ventana solo después de retirar la imagen de arranque."""
        splash = QSplashScreen()
        window = QMainWindow()
        self.addCleanup(window.close)
        self.addCleanup(splash.close)
        splash.show()

        with (
            patch("qt_app.show_startup_splash", return_value=splash),
            patch("qt_app.BudgetMainWindow", return_value=window),
            patch("qt_app.QTimer.singleShot") as schedule,
            patch.object(self.application, "exec", return_value=0),
            patch.object(window, "showMaximized", wraps=window.showMaximized) as maximize,
        ):
            qt_app.main()
            maximize.assert_not_called()
            self.assertTrue(splash.isVisible())
            callback = schedule.call_args.args[-1]

            def maximize_without_splash() -> None:
                """Comprueba la visibilidad en el momento exacto del relevo."""
                self.assertFalse(splash.isVisible())

            maximize.side_effect = maximize_without_splash
            callback()
            maximize.assert_called_once()

    def test_startup_without_splash_maximizes_immediately(self) -> None:
        """La aplicación sigue arrancando si no puede cargar la imagen."""
        with (
            patch("qt_app.show_startup_splash", return_value=None),
            patch("qt_app.BudgetMainWindow") as window_class,
            patch("qt_app.QTimer.singleShot") as schedule,
            patch.object(self.application, "exec", return_value=0),
        ):
            qt_app.main()
            window_class.return_value.showMaximized.assert_called_once()
            schedule.assert_not_called()

    def test_reference_pill_opens_context_and_launches_only_selected_link(self) -> None:
        """La píldora muestra el contexto de la herramienta sin abrir páginas por sí sola."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("budget_qt_window.user_data_directory", return_value=Path(directory)),
            patch("tools._shared.reference_pill.QDesktopServices.openUrl", return_value=True) as launch,
        ):
            window = BudgetMainWindow()
            self.addCleanup(QCoreApplication.sendPostedEvents, None, QEvent.Type.DeferredDelete)
            self.addCleanup(window.deleteLater)
            main_pill = window.findChild(ReferencePill)
            self.assertIsNotNone(main_pill)
            window.budget_items = [ProjectItem("P1", "01#", "ud", "Partida", 1, 10)]
            window._open_tool("price_justification")
            tool = window._project_windows[-1]
            pill = tool.findChild(ReferencePill)
            self.assertIsNotNone(pill)
            QTest.mouseClick(pill, Qt.MouseButton.LeftButton)
            dialog = tool.findChild(QDialog, "referencePillDialog")
            self.assertIsNotNone(dialog)
            self.assertTrue(dialog.isVisible())
            launch.assert_not_called()
            link = next(label for label in dialog.findChildren(QLabel) if "artículo 130" in label.text())
            address = "https://www.boe.es/buscar/act.php?id=BOE-A-2001-19995#a130"
            self.assertIn(address, link.text())
            link.linkActivated.emit(address)
            launch.assert_called_once_with(QUrl(address))
            dialog.reject()
            self.assertFalse(dialog.isVisible())

    def test_long_panel_titles_elide_without_limiting_splitter(self) -> None:
        """Los nombres largos se recortan y no ensanchan ninguno de los paneles."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            self.addCleanup(QCoreApplication.sendPostedEvents, None, QEvent.Type.DeferredDelete)
            self.addCleanup(window.deleteLater)
            self.addCleanup(window.close)
            window.resize(1800, 860)
            window.show()
            self.application.processEvents()
            splitter = window.findChild(QSplitter)
            self.assertIsNotNone(splitter)
            splitter.setChildrenCollapsible(False)
            left_narrow = [0, splitter.width()]
            right_narrow = left_narrow[::-1]
            splitter.setSizes(left_narrow)
            self.application.processEvents()
            original_range = splitter.getRange(1)

            project = ("Proyecto de rehabilitación y acondicionamiento " * 8).rstrip()
            reference = Path(directory) / (("Base regional de precios " * 6).strip() + ".bc3")
            reference.touch()
            window.project_title = project
            window._update_actions()
            window.reference_bases = [Bc3Dataset(reference)]
            window.refresh_reference_tree()
            self.application.processEvents()
            self.assertEqual(splitter.getRange(1), original_range)

            for index, caption_text, label, title, narrow, wide in (
                (0, "Proyecto", window.project_heading, project, left_narrow, right_narrow),
                (1, "Base de referencia", window.reference_heading, reference.stem,
                 right_narrow, left_narrow),
            ):
                with self.subTest(panel=caption_text):
                    caption = next(
                        widget for widget in splitter.widget(index).findChildren(QLabel)
                        if widget.text() == caption_text
                    )
                    self.assertLess(caption.geometry().bottom(), label.geometry().top())
                    splitter.setSizes(narrow)
                    self.application.processEvents()
                    narrow_text = label.text()
                    self.assertTrue(narrow_text.endswith("…"))
                    self.assertTrue(title.startswith(narrow_text[:-1]))
                    self.assertEqual(label.toolTip(), title)
                    splitter.setSizes(wide)
                    self.application.processEvents()
                    self.assertGreater(len(label.text()), len(narrow_text))

            window.reference_bases = []
            window.refresh_reference_tree()
            self.assertEqual(window.reference_heading.text(), "Sin cargar")
            self.assertEqual(window.reference_heading.toolTip(), "")

    def test_import_and_reopen_project_uses_core_model_without_gui(self) -> None:
        """Importa un BC3, guarda el JSON y lo reabre con el mismo modelo."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "presupuesto.bc3"
            shutil.copyfile(ROOT / "tests" / "test.bc3", source)
            with patch("budget_qt_window.user_data_directory", return_value=folder):
                window = BudgetMainWindow()
                with patch.object(
                    BudgetMainWindow,
                    "_ask_collaboration_protection",
                    return_value=(False, True),
                ):
                    window._load_budget_file(source)
                expected_items = len(window.budget_items)
                project_path = window.project_path
                self.assertIsNotNone(project_path)
                self.assertTrue(project_path.is_dir())
                self.assertEqual(
                    window.budget_tree.topLevelItemCount(),
                    len(window.chapter_order),
                )
                window.close()

                reopened = BudgetMainWindow()
                self.assertTrue(
                    reopened._load_collaborative_project(project_path)
                )
                self.assertEqual(len(reopened.budget_items), expected_items)
                self.assertIsInstance(reopened.budget_dataset, Bc3Dataset)
                self.assertEqual(reopened.budget_items[0].__class__.__module__, "budget_core")
                reopened.close()
            self.application.processEvents()

    def test_file_menu_contains_all_original_actions_and_both_recent_histories(self) -> None:
        """La opción Archivo conserva todas las acciones de la ventana anterior."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            file_menu = window.menuBar().actions()[0].menu()
            labels = [action.text() for action in file_menu.actions()]
            for expected in (
                "Nuevo proyecto",
                "Importar presupuesto BC3…",
                "Añadir base de referencia BCCA…",
                "Quitar base de referencia…",
                "Abrir proyecto colaborativo…",
                "Importar proyecto JSON…",
                "Guardar",
                "Guardar como proyecto colaborativo…",
                "Configuración…",
                "Recientes",
                "Bases de referencia recientes",
            ):
                self.assertIn(expected, labels)
            window.close()
            self.application.processEvents()

    def test_settings_dialog_exposes_every_tab_and_preserves_report_fields(self) -> None:
        """Configura informes, idioma, municipios y CPV en una sola ventana."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            dialog = SettingsDialog(
                window,
                ReportSettings(),
                "Proyecto Qt",
                "es",
                lambda language: True,
            )
            self.assertEqual(
                [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())],
                [
                    "Informes", "Idioma", "Apariencia", "Colaboración",
                    "Emplazamiento", "CPV",
                ],
            )
            self.assertEqual(dialog.location_selector.query.currentIndex(), -1)
            self.assertEqual(dialog.location_selector.query.currentText(), "")
            buttons = dialog.findChild(QDialogButtonBox)
            self.assertIsNotNone(buttons)
            self.assertEqual(
                buttons.button(QDialogButtonBox.StandardButton.Save).text(),
                "Guardar",
            )
            self.assertEqual(
                buttons.button(QDialogButtonBox.StandardButton.Cancel).text(),
                "Cancelar",
            )
            dialog.project_title_field.setText("Proyecto configurado")
            dialog.report_fields["author"].setPlainText("Autora Alfa\nAutor Beta")
            self.assertTrue(dialog.show_author.isChecked())
            dialog.show_author.setChecked(False)
            dialog.report_fields["indirect_costs"].setText("7.5")
            dialog.output_format.setCurrentText("ODT")
            values = dialog.values()
            self.assertEqual(values["project_title"], "Proyecto configurado")
            self.assertEqual(values["author"], "Autora Alfa\nAutor Beta")
            saved = ReportSettings.from_dict(values)
            reopened = SettingsDialog(window, ReportSettings.from_dict(saved.to_dict()),
                                      "Proyecto configurado", "es", lambda language: True)
            self.assertFalse(reopened.show_author.isChecked())
            self.assertEqual(reopened.report_fields["author"].toPlainText(), "Autora Alfa\nAutor Beta")
            reopened.reject()
            self.assertEqual(values["indirect_costs"], "7.5")
            self.assertEqual(values["output_format"], "odt")
            dialog.reject()
            window.close()
            self.application.processEvents()

    def test_general_settings_and_language_are_persisted_atomically(self) -> None:
        """Guarda preferencias globales y actualiza la configuración del proyecto."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            folder = Path(directory)
            window = BudgetMainWindow()
            data = window.report_settings.to_dict()
            data.update({
                "project_title": "Configuración Qt",
                "author": "Autora",
                "show_author": False,
                "indirect_costs": "3.5",
                "output_format": "odt",
                "currency": "OTHER",
                "custom_currency": "Ptas",
            })
            self.assertTrue(window._save_report_settings(data))
            self.assertEqual(window.project_title, "Configuración Qt")
            self.assertEqual(window.report_settings.author, "Autora")
            self.assertFalse(window._read_report_settings().show_author)
            self.assertEqual(window.report_settings.indirect_costs, 3.5)
            self.assertEqual(window.report_settings.output_format, "odt")
            self.assertTrue((folder / "configuracion_informes.json").is_file())
            self.assertTrue(window._save_language_preference("fr"))
            self.assertTrue(window._save_theme_preference("dark"))
            self.assertTrue((folder / "preferencias.json").is_file())
            preferences = json.loads((folder / "preferencias.json").read_text(encoding="utf-8"))
            self.assertEqual(preferences, {"language": "fr", "theme": "dark"})
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_button_language_updates_existing_controls_and_new_dialogs(self) -> None:
        """Cambia y restaura el idioma de botones propios, de barra y estándar."""
        original_language = get_language()
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("budget_qt_window.user_data_directory", return_value=Path(directory)),
            patch("budget_qt_window.apply_theme"),
        ):
            window = BudgetMainWindow()
            dialog = QDialog(window)
            layout = QVBoxLayout(dialog)
            button = QPushButton("Añadir selección o rama")
            layout.addWidget(button)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            layout.addWidget(buttons)
            try:
                dialog.show()
                for language, save, add, ok, cancel, open_ in (
                    ("en", "Save", "Add selection or branch", "OK", "Cancel", "&Open"),
                    ("fr", "Enregistrer", "Ajouter la sélection ou la branche", "OK", "Annuler", "&Ouvrir"),
                    ("es", "Guardar", "Añadir selección o rama", "Aceptar", "Cancelar", "&Abrir"),
                ):
                    with self.subTest(language=language):
                        self.assertTrue(window._save_language_preference(language))
                        self.application.processEvents()
                        self.assertEqual(window.save_action.text(), save)
                        self.assertEqual(button.text(), add)
                        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Ok).text(), ok)
                        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Cancel).text(), cancel)
                        picker = QFileDialog(dialog)
                        self.assertIn(open_, [item.text() for item in picker.findChildren(QPushButton)])
                        picker.close()
                self.assertTrue(window._save_language_preference("fr"))
                localize_button(button, "Aceptar y generar")
                self.assertEqual(button.text(), "Accepter et générer")
            finally:
                dialog.close()
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                set_language(original_language)
                apply_button_language(self.application)
                self.application.processEvents()

    def test_price_justification_qt_roundtrips_state_and_reuses_window(self) -> None:
        """Migra el anejo a Qt sin cambiar el estado persistente compatible."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            window.budget_items = [
                ProjectItem("P1", "01#", "ud", "Partida", 1, 10)
            ]
            window.chapter_order = ["01#"]
            window._reset_history()
            window._open_tool("price_justification")
            tool = window._project_windows[-1]
            self.assertIsInstance(tool, PriceJustificationWindowQt)
            window._open_tool("price_justification")
            self.assertEqual(len(window._project_windows), 1)
            self.assertEqual(len(tool.variables), 14)
            self.assertTrue(tool.variables["agreement"].isEditable())
            tool.variables["price_source"].setText("Fuente Qt")
            tool.variables["agreement"].setEditText("Convenio editable")
            self.assertTrue(window.is_dirty)
            self.assertTrue(window._collect_tool_states())
            target = Path(directory) / "proyecto.json"
            self.assertTrue(window._write_project_file(target))
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["price_justification"]["price_source"],
                "Fuente Qt",
            )
            self.assertEqual(
                payload["price_justification"]["agreement"],
                "Convenio editable",
            )
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_file_picker_localizes_overwrite_confirmation(self) -> None:
        """Traduce la confirmación de Qt y conserva su marcador de nombre de archivo."""
        previous_language = get_language()
        try:
            source = "%1 already exists.\nDo you want to replace it?"
            for language, expected in (
                ("es", "%1 ya existe.\n¿Quieres reemplazarlo?"),
                ("en", source),
                ("fr", "%1 existe déjà.\nVoulez-vous le remplacer ?"),
            ):
                with self.subTest(language=language):
                    set_language(language)
                    apply_button_language(self.application)
                    self.assertEqual(QCoreApplication.translate("QFileDialog", source), expected)
        finally:
            set_language(previous_language)
            apply_button_language(self.application)

    def test_price_justification_save_button_fits_and_opens_file_picker(self) -> None:
        """Abre un proyecto reciente y permite guardar el anejo desde una ventana estrecha."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("budget_qt_window.user_data_directory", return_value=Path(directory)),
            patch.object(QMessageBox, "information"),
            patch.object(QMessageBox, "critical") as error,
        ):
            window = BudgetMainWindow()
            try:
                window.budget_items = [ProjectItem("P1", "01#", "ud", "Partida", 1, 10)]
                window.report_settings.output_format = "odt"
                window.project_title = "Proyecto reciente"
                project_path = Path(directory) / "proyecto.json"
                self.assertTrue(window._write_project_file(project_path))
                window.project_title = "Sin abrir"
                window.showMaximized()
                self.application.processEvents()
                recent_action = window.recent_menu.actions()[0]
                recent_action.trigger()
                self.assertEqual(window.project_title, "Proyecto reciente")
                self.assertFalse(window.undo_action.isEnabled())
                self.assertIn("Proyecto abierto:", window.status_bar.currentMessage())
                window._open_tool("price_justification")
                tool = window._project_windows[-1]
                tool.resize(760, 600)
                self.application.processEvents()
                button = next(b for b in tool.findChildren(QPushButton)
                              if b.text() == "Guardar anejo…")
                self.assertLessEqual(tool.width(), 760)
                self.assertTrue(tool.rect().contains(button.mapTo(tool, button.rect().bottomRight())))

                target = Path(directory) / "anejo.odt"
                opened: list[QFileDialog.AcceptMode] = []

                def choose_destination() -> None:
                    """Confirma el archivo en el selector visible y cierra cualquier modal inesperado."""
                    dialog = self.application.activeModalWidget()
                    if isinstance(dialog, QFileDialog) and dialog.isVisible():
                        name_edit = dialog.findChild(QLineEdit, "fileNameEdit")
                        if name_edit is None:
                            dialog.reject()
                            return
                        opened.append(dialog.acceptMode())
                        dialog.setDirectory(directory)
                        name_edit.setText(target.name)
                        dialog.accept()
                    elif isinstance(dialog, QDialog):
                        dialog.reject()

                QTimer.singleShot(100, choose_destination)
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                self.assertEqual(opened, [QFileDialog.AcceptMode.AcceptSave])
                error.assert_not_called()
                self.assertTrue(target.is_file())
                tool.close()
                self.assertFalse(tool.isVisible())
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_price_justification_qt_rejects_invalid_state_before_save(self) -> None:
        """Impide serializar una fecha o jornada no válida desde Qt."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            window.budget_items = [
                ProjectItem("P1", "01#", "ud", "Partida", 1, 10)
            ]
            window._open_tool("price_justification")
            tool = window._project_windows[-1]
            tool.variables["annual_hours"].setEditText("NaN")
            with patch.object(QMessageBox, "critical") as error:
                self.assertFalse(window._collect_tool_states())
            error.assert_called_once()
            tool.variables["annual_hours"].setEditText("1736")
            tool.variables["reference_date"].setText("2026-02-31")
            with patch.object(QMessageBox, "critical") as error:
                self.assertFalse(window._collect_tool_states())
            error.assert_called_once()
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_rcd_qt_reuses_window_reviews_wastes_and_synchronizes_budget(self) -> None:
        """Migra los dos pasos RCDs y la sincronización del capítulo."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            window.budget_items = [
                ProjectItem("EXC", "01#", "m3", "Excavación de tierras", 10, 20),
                ProjectItem("DEM", "02#", "m2", "Demolición de hormigón", 50, 10),
                ProjectItem("PAV", "02#", "m²", "Pavimento", 80, 10),
            ]
            window.chapter_order = ["01#", "02#"]
            window._open_tool("rcd")
            tool = window._project_windows[-1]
            self.assertIsInstance(tool, RcdWindowQt)
            window._open_tool("rcd")
            self.assertEqual(len(window._project_windows), 1)
            self.assertEqual(tool.waste_table.rowCount(), 0)
            self.assertTrue(tool.variables["surface"].isReadOnly())
            self.assertEqual(float(tool.variables["surface"].text()), 80)
            self.assertIn("PAV", tool.surface_hint.text())
            window.budget_items[1].measurement = 120
            tool.accept_inputs()
            self.assertEqual(tool.data.inputs["surface"], 120)
            self.assertIn("DEM", tool.data.sources["surface"])
            self.assertEqual(tool.waste_table.rowCount(), 57)
            self.assertEqual(tool.preview_table.rowCount(), 8)
            selected = tool.data.selected["40"]
            waste_item = next(
                tool.waste_table.item(row, 0)
                for row in range(tool.waste_table.rowCount())
                if tool.waste_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "40"
            )
            waste_item.setCheckState(
                Qt.CheckState.Unchecked if selected else Qt.CheckState.Checked
            )
            self.assertNotEqual(tool.data.selected["40"], selected)
            with patch.object(window, "save_project", return_value=False):
                tool.save()
            generated = [
                item
                for item in window.budget_items
                if item.source.startswith(RCD_BUDGET_SOURCE)
            ]
            self.assertEqual(len(generated), 7)
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_rcd_editor_revisions_are_used_in_styled_exports(self) -> None:
        """Guarda los ocho textos desde Qt y conserva su edición y estilo en PDF y ODT."""
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile

        from pypdf import PdfReader
        from odt_reports import _tag
        from tools._library_editor_qt import LibraryEditorQt
        from tools.rcd import write_report
        from tools.rcd.study import LIBRARY_PATH

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            library = folder / "rcd.zip"
            shutil.copyfile(LIBRARY_PATH, library)
            with (
                patch("budget_qt_window.user_data_directory", return_value=folder),
                patch("tools.rcd.qt_window.LIBRARY_PATH", library),
                patch("tools.rcd.study.LIBRARY_PATH", library),
                patch.object(QMessageBox, "critical") as error,
                patch.object(QMessageBox, "information"),
            ):
                window = BudgetMainWindow()
                try:
                    window.budget_items = [
                        ProjectItem("EXC", "01#", "m3", "Excavación de tierras", 10, 20),
                        ProjectItem("PAV", "02#", "m²", "Pavimento", 80, 10),
                    ]
                    window._open_tool("rcd")
                    tool = window._project_windows[-1]
                    tool.open_library_editor()
                    editor = tool.library_editor
                    self.assertIsInstance(editor, LibraryEditorQt)
                    self.assertTrue(editor.track_changes)
                    self.assertEqual(editor.members.count(), 8)
                    editor.author.setText("Autor de prueba")
                    for index in range(8):
                        editor.members.setCurrentRow(index)
                        editor.editor.appendPlainText(f"Texto RCD revisado {index}.")
                        if index == 0:
                            tool.generate_report()
                            self.assertIn("Guarda o descarta", error.call_args.args[2])
                            error.reset_mock()
                        self.assertTrue(editor.save(), error.call_args)
                    error.assert_not_called()
                    for output_format in ("odt", "pdf"):
                        window.report_settings.output_format = output_format
                        target = folder / f"estudio.{output_format}"
                        write_report(tool.data, "Proyecto RCD", window.report_settings, target, app=window)
                        if output_format == "odt":
                            with ZipFile(target) as archive:
                                content = ET.fromstring(archive.read("content.xml"))
                            text = " ".join(content.itertext())
                            body = content.find(f".//{_tag('office:text')}")
                            tables = [(index, table) for index, table in enumerate(body)
                                      if table.tag == _tag("table:table")]
                            self.assertGreater(len(tables), 1)
                            for number, (index, table) in enumerate(tables, start=1):
                                self.assertEqual("".join(body[index - 2].itertext()), f"Véase la Tabla {number}.")
                                self.assertTrue("".join(body[index - 1].itertext()).startswith(f"Tabla {number}. "))
                                header = table.find(_tag("table:table-header-rows"))
                                self.assertIsNotNone(header)
                                self.assertTrue(all(
                                    cell.get(_tag("table:style-name")) == "RcdTableHeaderCell"
                                    for cell in header.iter(_tag("table:table-cell"))
                                ))
                            backgrounds = {style.get(_tag("fo:background-color"))
                                           for style in content.iter(_tag("style:table-cell-properties"))}
                            self.assertTrue({"#4472C4", "#D9E5F5", "#FFFFFF"} <= backgrounds)
                        else:
                            reader = PdfReader(target)
                            text = " ".join(page.extract_text() for page in reader.pages)
                            self.assertIn("Véase la Tabla 1. Tabla 1. Datos del proyecto", " ".join(text.split()))
                            fills = {tuple(round(float(value) * 255) for value in operands)
                                     for page in reader.pages for operands, operator in page.get_contents().operations
                                     if operator == b"rg"}
                            self.assertTrue({(68, 114, 196), (217, 229, 245)} <= fills)
                        for index in range(8):
                            self.assertIn(f"Texto RCD revisado {index}.", text)
                finally:
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_rcd_qt_restores_valid_state_and_rejects_invalid_json(self) -> None:
        """Usa el mismo validador RCDs al abrir proyectos Qt."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            path = Path(directory) / "project.json"
            path.write_text(
                json.dumps({"items": [], "rcds": {"selected": {"5": "X"}}}),
                encoding="utf-8",
            )
            window = BudgetMainWindow()
            with patch.object(QMessageBox, "critical") as error:
                self.assertFalse(window._load_project_file(path))
            error.assert_called_once()
            path.write_text(
                json.dumps({"items": [], "rcds": {
                    "documentation": {"reuse": "Texto"}, "inputs": {"surface": 500},
                }}),
                encoding="utf-8",
            )
            self.assertTrue(window._load_project_file(path))
            self.assertEqual(window.rcd_state["documentation"]["reuse"], "Texto")
            window.budget_items = [ProjectItem("PAV", "01#", "m^2", "Pavimento", 80, 10)]
            window._open_tool("rcd")
            tool = window._project_windows[-1]
            self.assertEqual(float(tool.variables["surface"].text()), 80)
            self.assertTrue(tool.variables["surface"].isReadOnly())
            window.budget_items.clear()
            tool.show_inputs()
            self.assertEqual(tool.variables["surface"].text(), "")
            self.assertFalse(tool.variables["surface"].isReadOnly())
            window.close()
            self.application.processEvents()

    def test_safety_health_qt_reviews_chapter_criteria_centers_and_conditions(self) -> None:
        """Migra el asistente preventivo conservando estado y propuesta."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            item = ProjectItem(
                "01AAB00001", "01#", "m2", "Demolición de bóveda",
                measurement=10, unit_price=15.89, rendimiento=2,
            )
            window.budget_items = [item]
            window.chapter_order = ["01#"]
            state = serialize_state(
                proposed_lines([item]),
                Decimal("2"),
                chapter_selection={"01#": True},
            )
            window.safety_health_state = state
            tool = SafetyHealthWindowQt(window)
            self.assertEqual(len(tool.criteria_checks), 4)
            self.assertEqual(len(tool.lines), 3)
            tool.summary_text.setPlainText("Resumen preventivo Qt")
            tool.commit_basic_state()
            self.assertEqual(
                window.safety_health_state["resumen_actuacion"],
                "Resumen preventivo Qt",
            )
            conditions = tool.open_external_conditions()
            self.assertIsInstance(conditions, ExternalConditionsDialogQt)
            conditions.rows["trafico_existente"][1].setCurrentIndex(
                conditions.rows["trafico_existente"][1].findData("yes")
            )
            conditions.save()
            self.assertEqual(
                window.safety_health_state["external_conditions"][
                    "trafico_existente"
                ]["applicability"],
                "yes",
            )
            proposal = tool.open_proposal()
            self.assertEqual(proposal.table.rowCount(), 3)
            with patch.object(window, "save_project", return_value=False):
                self.assertTrue(proposal.create_chapter())
            generated = [
                row
                for row in window.budget_items
                if row.source.startswith("SS:")
            ]
            self.assertEqual(len(generated), 3)
            proposal.reject()
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_safety_health_chapter_selector_requires_nonempty_scope(self) -> None:
        """La selección inicial Qt conserva la obligatoriedad de un capítulo."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            window.budget_items = [
                ProjectItem("P", "01#", "ud", "Obra", 1, 10)
            ]
            dialog = ChapterSelectionDialogQt(window, lambda choices: None)
            self.assertTrue(all(
                check.isChecked() for check in dialog.choices.values()
            ))
            for check in dialog.choices.values():
                check.setChecked(False)
            with patch.object(QMessageBox, "critical") as error:
                dialog.accept_selection()
            error.assert_called_once()
            dialog.reject()
            window.close()
            self.application.processEvents()

    def test_pptp_qt_keeps_three_steps_manual_sheets_and_price_settings(self) -> None:
        """Migra el asistente PPTP sin perder asignaciones ni revisión económica."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            window.budget_items = [
                ProjectItem(
                    "01AAB00001", "01#", "m2", "Partida PPTP", 1, 10
                )
            ]
            window.chapter_order = ["01#"]
            window.chapter_titles = {"01#": "Obra"}
            window._open_tool("pptp")
            tool = window._project_windows[-1]
            self.assertIsInstance(tool, PptpWindowQt)
            window._open_tool("pptp")
            self.assertEqual(len(window._project_windows), 1)
            self.assertEqual(tool.pages.count(), 3)
            self.assertIn(tool.toc_depth.currentText(), tuple(str(i) for i in range(1, 7)))
            tool.price_revision.setChecked(True)
            self.assertTrue(tool.variables["FORMULA_REVISION_PRECIOS"].isEnabled())
            for key, field in tool.variables.items():
                if not field.text().strip():
                    field.setText("Dato de prueba")
            tool.variables["plazo_ejecucion"].setText("18 meses")
            tool.summary.setPlainText("Resumen de obra")
            tool.open_library_editor()
            self.assertGreater(
                tool.library_editor.members.count(),
                len(tool.automatic) + len(tool.unmatched),
            )
            tool.library_editor.reject()
            tool.accept_step()
            self.assertEqual(tool.pages.currentIndex(), 1)
            tool.accept_step()
            self.assertEqual(tool.pages.currentIndex(), 2)
            before = len(tool.unmatched)
            if tool.manual_table.rowCount():
                tool.manual_table.selectRow(0)
                tool.add_sheet()
                self.assertEqual(len(tool.unmatched), before + 1)
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_certifications_qt_reconciles_plan_edits_and_justification(self) -> None:
        """Migra plan mensual, certificación, conciliación y justificación."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            window.budget_items = [
                ProjectItem(
                    "P", "01#", "m2", "Partida",
                    measurement=100, unit_price=10, rendimiento=.5,
                )
            ]
            window.chapter_order = ["01#"]
            window._open_tool("certifications")
            tool = window._project_windows[-1]
            self.assertIsInstance(tool, CertificationWindowQt)
            window._open_tool("certifications")
            self.assertEqual(len(window._project_windows), 1)
            self.assertEqual(tool.table.rowCount(), 1)
            self.assertEqual(tool.table.columnCount(), 52)
            month = tool.months[0]
            initial_next = tool.plan[0][1]
            tool.commit_certification(0, 0, str(tool.plan[0][0] - 25))
            self.assertEqual(tool.items[0].certifications[month.key], tool.plan[0][0])
            self.assertGreater(tool.plan[0][1], initial_next)
            tool.select_certification(0, 0)
            tool.justification.setPlainText("Medición ejecutada")
            self.assertEqual(
                tool.items[0].certification_justifications[month.key],
                "Medición ejecutada",
            )
            self.assertTrue(tool.dirty)
            with patch.object(QMessageBox, "critical") as error:
                tool.commit_certification(0, 0, "-1")
            error.assert_called_once()
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_planning_qt_roundtrips_parameters_sequence_and_shared_state(self) -> None:
        """Migra calendario, edición de actividades y estado compartido."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            window.budget_items = [
                ProjectItem(
                    "P1", "01#", "m2", "Primera", 16, 10, rendimiento=1
                ),
                ProjectItem(
                    "P2", "01#", "m2", "Segunda", 8, 10, rendimiento=1
                ),
            ]
            window.chapter_order = ["01#"]
            window._open_tool("planning")
            tool = window._project_windows[-1]
            self.assertIsInstance(tool, PlanningWindowQt)
            window._open_tool("planning")
            self.assertEqual(len(window._project_windows), 1)
            self.assertEqual(tool.table.rowCount(), 2)
            tool.commit_cell(tool.activities[0], "teams", "2")
            self.assertEqual(tool.activities[0].duration, 1)
            self.assertEqual(
                window.planning_state["activities"][0]["teams"], 2
            )
            tool.sequence_chain()
            self.assertEqual(
                [activity.pred for activity in tool.activities],
                [None, 1],
            )
            tool.new_holiday_date.setText("10/12/2026")
            tool.new_holiday_description.setText("Festivo Qt")
            tool.add_holiday()
            self.assertIn(
                "2026-12-10",
                [entry["date"] for entry in tool.to_state()["params"]["holidays"]],
            )
            with patch.object(window, "save_project", return_value=True):
                tool.save_changes()
            self.assertFalse(tool.dirty)
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_invoicing_qt_preserves_matrix_assignments_and_review_lines(self) -> None:
        """Migra registro, visor, matriz y asignaciones persistentes."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            dataset = Bc3Dataset.__new__(Bc3Dataset)
            dataset.concepts = {
                "MO": Concept("MO", "h", "Operario", 10, "1"),
                "MAT": Concept("MAT", "kg", "Material", 3, "3"),
                "MAQ": Concept("MAQ", "h", "Equipo", 5, "2"),
                "AUX": Concept(
                    "AUX", "ud", "Auxiliar", 8, "0",
                    children=[("MAT", 1), ("MAQ", 1)],
                ),
            }
            window.budget_dataset = dataset
            window.budget_items = [
                ProjectItem(
                    "P1", "01#", "m", "Unidad uno", measurement=2,
                    unit_price=35, decomposition=[
                        ("MO", 2), ("MAT", 1), ("AUX", 1)
                    ], component_prices={"MO": 12},
                ),
                ProjectItem("P2", "01#", "ud", "Sin descomposición", unit_price=50),
            ]
            window.reference_bases = []
            window.chapter_order = ["01#"]
            window.chapter_titles = {}
            window.invoicing_state = InvoiceState(
                invoices=[
                    Invoice(
                        "one",
                        "Factura",
                        date="01/02/2026",
                        declared=30,
                        lines=[InvoiceLine("MAT", 30)],
                    )
                ]
            ).to_dict()
            window._open_tool("invoicing")
            tool = window._project_windows[-1]
            self.assertIsInstance(tool, InvoicingWindowQt)
            window._open_tool("invoicing")
            self.assertEqual(len(window._project_windows), 1)
            self.assertEqual(tool.register.rowCount(), 1)
            self.assertGreater(tool.table.rowCount(), 0)
            invoice = tool.current_invoice()
            self.assertIsNotNone(invoice)
            row_id = tool.data.rows[0].id
            self.assertTrue(tool.commit_cell(invoice.id, row_id, "12,50"))
            self.assertEqual(invoice.amounts[row_id], 12.5)
            self.assertEqual(
                window.invoicing_state["invoices"][0]["amounts"][row_id],
                12.5,
            )
            tool.resource_filter = "Material"
            tool.refresh()
            self.assertTrue(all(
                tool.visible_rows[index][1][0].kind == "Material"
                for index in range(len(tool.visible_rows))
            ))
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_main_qt_menus_move_exports_under_file_without_printing(self) -> None:
        """Conserva las cuatro acciones de exportación en Archivo y retira la impresión."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("budget_qt_window.user_data_directory", return_value=Path(directory)),
            patch.object(BudgetMainWindow, "export_budget_ods", autospec=True) as budget,
            patch.object(BudgetMainWindow, "export_measurements_csv", autospec=True) as measurements,
            patch.object(BudgetMainWindow, "export_resource_report", autospec=True) as resources,
        ):
            window = BudgetMainWindow()
            menus = {
                action.text(): action.menu()
                for action in window.menuBar().actions()
            }
            self.assertNotIn("Imprimir y exportar", menus)
            self.assertIn("Informes", menus)
            file_actions = {action.text(): action for action in menus["Archivo"].actions()}
            self.assertNotIn("Imprimir presupuesto", file_actions)
            self.assertNotIn("Vista previa del presupuesto…", file_actions)
            export_menu = file_actions["Exportar"].menu()
            self.assertIsNotNone(export_menu)
            actions = export_menu.actions()
            self.assertEqual([action.text() for action in actions], [
                "Exportar presupuesto a ODS…",
                "Exportar mediciones a CSV…",
                "Exportar recursos a ODS…",
                "Exportar descompuestos a ODS…",
            ])
            for action in actions:
                action.trigger()
            budget.assert_called_once_with(window)
            measurements.assert_called_once_with(window)
            self.assertEqual([call.args for call in resources.call_args_list],
                             [(window, "Recursos"), (window, "Descompuestos")])
            self.assertIn("Consultas", menus)
            self.assertIn("Información", menus)
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_query_menu_entries_have_visible_search_icons_and_open_their_catalogues(self) -> None:
        """Las lupas de Consultas no cambian cuál de los tres buscadores se abre."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("budget_qt_window.user_data_directory", return_value=Path(directory)),
        ):
            window = BudgetMainWindow()
            try:
                menu = next(action.menu() for action in window.menuBar().actions()
                            if action.text() == "Consultas")
                entries = menu.actions()
                self.assertEqual([entry.text() for entry in entries], ["NTP", "UNE", "LER"])
                with patch.object(window, "open_reference_search") as open_catalogue:
                    for entry in entries:
                        self.assertTrue(entry.isIconVisibleInMenu())
                        self.assertFalse(entry.icon().isNull())
                        entry.trigger()
                        open_catalogue.assert_called_with(entry.text())
            finally:
                window.close()
                self.application.processEvents()

    def test_reference_search_qt_filters_and_exposes_only_visible_entry(self) -> None:
        """Consulta NTP/UNE sin conservar ventanas o datos heredados Tk."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            entries = (
                ReferenceEntry(
                    "NTP 1", "Seguridad", "1990", "", "",
                    "https://www.insst.es/example",
                ),
                ReferenceEntry(
                    "NTP 20", "Ruido", "1995", "", "",
                    "https://www.insst.es/example-two",
                ),
            )
            search = ReferenceSearchWindowQt(window, "NTP", entries)
            search.query.setText("ruido")
            self.assertEqual(search.table.rowCount(), 1)
            search.table.selectRow(0)
            self.assertEqual(search.selected_entry().code, "NTP 20")
            self.assertIn("NTP 20", search.detail.toPlainText())
            search.reject()
            window.close()
            self.application.processEvents()

    def test_ler_reference_catalog_is_loaded_and_has_no_external_link(self) -> None:
        """Consulta todos los niveles LER desde el CSV local."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            entries = load_reference_entries("LER")
            self.assertGreater(len(entries), 100)
            search = ReferenceSearchWindowQt(window, "LER", entries)
            search.query.setText("hormigon")
            self.assertGreater(search.table.rowCount(), 0)
            search.table.selectRow(0)
            self.assertTrue(search.selected_entry().code)
            self.assertFalse(search.open_button.isEnabled())
            search.reject()
            window.close()
            self.application.processEvents()

    def test_collaboration_menu_exposes_shared_project_and_libraries(self) -> None:
        """La barra principal concentra apertura, cifrado y bibliotecas."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            menus = {
                action.text(): action.menu()
                for action in window.menuBar().actions()
            }
            labels = [
                action.text() for action in menus["Colaboración"].actions()
            ]
            self.assertIn("Abrir proyecto compartido…", labels)
            self.assertIn(
                "Crear o migrar proyecto compartido…", labels
            )
            self.assertIn("Cambiar protección…", labels)
            self.assertIn("Fuentes de bibliotecas…", labels)
            self.assertIn("Estado de colaboración…", labels)
            window.close()
            self.application.processEvents()

    def test_collaborator_nickname_preference_is_saved_globally(self) -> None:
        """El apodo se conserva aparte del documento colaborativo."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            self.assertTrue(window._save_global_preferences(
                collaboration_nickname="Manuel"
            ))
            preferences = json.loads(
                (Path(directory) / "preferencias.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(preferences["collaboration_nickname"], "Manuel")
            window.close()
            self.application.processEvents()

    def test_presence_indicator_only_appears_for_multiple_users(self) -> None:
        """El icono se muestra cuando hay colaboración real de dos o más personas."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            window._collaborators_changed([
                {
                    "id": "a", "nickname": "Alicia", "host": "PC",
                    "timestamp": "2026-11-19T00:00:00+00:00",
                }
            ])
            self.assertTrue(window._collaboration_presence.isHidden())
            window._collaborators_changed([
                {
                    "id": "a", "nickname": "Alicia", "host": "PC",
                    "timestamp": "2026-11-19T00:00:00+00:00",
                },
                {
                    "id": "b", "nickname": "Berto", "host": "LAPTOP",
                    "timestamp": "2026-11-19T00:00:00+00:00",
                },
            ])
            self.assertFalse(window._collaboration_presence.isHidden())
            self.assertIn("2 colaboradores", window._collaboration_presence_text.text())
            self.assertFalse(window._collaboration_presence_icon.pixmap().isNull())
            window.close()
            self.application.processEvents()

    def test_modern_theme_applies_icons_to_actions_buttons_and_dialogs(self) -> None:
        """El tema moderno echa raíces globales en botones y acciones."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            apply_theme(self.application, "light")
            window = BudgetMainWindow()
            window.show()
            self.application.processEvents()
            self.assertTrue(self.application.styleSheet())
            self.assertFalse(window.save_action.icon().isNull())
            self.assertFalse(window.windowIcon().isNull())
            buttons = [
                action
                for action in window.menuBar().actions()[0].menu().actions()
                if action.text()
            ]
            self.assertTrue(all(action.icon().isNull() for action in buttons))
            self.assertTrue(all(
                action.icon().isNull()
                for top_action in window.menuBar().actions()
                if top_action.menu() is not None
                for action in top_action.menu().actions()
            ))
            dialog = SettingsDialog(
                window,
                ReportSettings(),
                "Proyecto",
                "es",
                lambda language: True,
            )
            dialog.show()
            self.application.processEvents()
            decorated = dialog.findChildren(QPushButton)
            self.assertTrue(all(
                not button.icon().isNull()
                or button.text() == "Aceptar"
                for button in decorated
            ))
            for button in decorated:
                self.assertNotEqual(button.text(), "OK")
            dialog.reject()
            window.close()
            self.application.processEvents()

    def test_reference_details_follow_the_selected_concept_and_clear_with_the_tree(self) -> None:
        """Muestra el concepto de la base seleccionada sin conservar detalles obsoletos."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            self.addCleanup(QCoreApplication.sendPostedEvents, None, QEvent.Type.DeferredDelete)
            self.addCleanup(window.deleteLater)
            self.addCleanup(window.close)
            first_path = Path(directory) / "Base A.bc3"
            second_path = Path(directory) / "Base B.bc3"
            first_path.touch()
            second_path.touch()
            first, second = Bc3Dataset(first_path), Bc3Dataset(second_path)
            first.root_codes = second.root_codes = ["P01"]
            first.concepts["P01"] = Concept("P01", summary="Resumen de la primera base")
            second.concepts["P01"] = Concept(
                "P01", "m²", "Pavimento", 35, text="Descripción técnica con acentos.",
                children=[("MO", 1.25)],
            )
            second.concepts["MO"] = Concept("MO", "h", "Oficial de albañilería", 20, "1")
            window.reference_bases = [first, second]
            window.refresh_reference_tree()
            tree = window.reference_tree
            details = window.reference_details
            first_group, second_group = tree.topLevelItem(0), tree.topLevelItem(1)

            self.assertTrue(details.isReadOnly())
            tree.setCurrentItem(first_group.child(0))
            self.assertIn("Resumen de la primera base", details.toPlainText())

            tree.setCurrentItem(second_group.child(0))
            self.assertEqual(window.selected_reference, (1, "P01"))
            for text in (
                "Base: Base B", "Código: P01", "Unidad: m²", "Precio: " + window._format_currency(35),
                "Rendimiento: 1,2500 h", "Descripción técnica con acentos.", "Descomposición:",
                "MO: Oficial de albañilería | 1,2500 h × " + window._format_currency(20),
            ):
                self.assertIn(text, details.toPlainText())

            tree.clearSelection()
            self.assertIsNone(window.selected_reference)
            self.assertEqual(details.toPlainText(), "")
            tree.setCurrentItem(second_group.child(0))
            tree.setCurrentItem(second_group)
            self.assertIsNone(window.selected_reference)
            self.assertEqual(details.toPlainText(), "")
            tree.setCurrentItem(second_group.child(0))
            window.reference_search.setText("sin coincidencias")
            self.assertIsNone(window.selected_reference)
            self.assertEqual(details.toPlainText(), "")

    def test_budget_and_reference_resource_filters_are_visible_and_exclusive(self) -> None:
        """Los botones de naturaleza filtran usos reales en ambos paneles."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            source = Path(ROOT / "tests" / "test.bc3")
            window._load_budget_file(source)
            window._load_reference_file(source)
            self.assertEqual(
                set(window.budget_filter_buttons), {"2", "1", "3"}
            )
            self.assertEqual(
                set(window.reference_filter_buttons), {"2", "1", "3"}
            )
            for button in (
                *window.budget_filter_buttons.values(),
                *window.reference_filter_buttons.values(),
            ):
                self.assertTrue(button.icon().isNull())
            window.toggle_budget_resource_filter("3")
            self.assertTrue(window.budget_filter_buttons["3"].isChecked())
            self.assertFalse(window.budget_filter_buttons["1"].isChecked())
            self.assertGreater(window.budget_tree.topLevelItemCount(), 0)
            self.assertTrue(all(
                window.budget_tree.topLevelItem(index).data(
                    0, Qt.ItemDataRole.UserRole
                ) == "resource"
                for index in range(window.budget_tree.topLevelItemCount())
            ))
            window.toggle_budget_resource_filter("3")
            self.assertEqual(window.budget_resource_filter, "")
            window.toggle_reference_resource_filter("2")
            self.assertTrue(window.reference_filter_buttons["2"].isChecked())
            self.assertGreater(
                window.reference_tree.topLevelItemCount(), 0
            )
            for index in range(window.reference_tree.topLevelItemCount()):
                self.assertEqual(
                    window.reference_tree.topLevelItem(index).text(2),
                    "",
                )
            window.toggle_reference_resource_filter("2")
            self.assertEqual(window.reference_resource_filter, "")
            window.is_dirty = False
            window.close()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
