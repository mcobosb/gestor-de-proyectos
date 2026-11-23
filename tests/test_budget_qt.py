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
    QPushButton, QSplashScreen, QSplitter, QTreeView, QVBoxLayout,
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
from qt_localization import (
    apply_button_language, get_document_save_path, localize_button,
)
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
from tools.planning.qt_window import GanttPreviewDialogQt, PlanningWindowQt
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

    def test_startup_image_is_wider_with_transparent_corners(self) -> None:
        """La imagen se amplía sin deformarse ni perder las esquinas transparentes."""
        splash = qt_app.show_startup_splash(self.application)
        self.assertIsNotNone(splash)
        self.addCleanup(splash.close)
        image = splash.pixmap().toImage()
        self.assertEqual((image.width(), image.height()), (520, 290))
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
            with (
                patch("budget_qt_window.user_data_directory", return_value=folder),
                patch("budget_qt_window.apply_theme"),
            ):
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
                styles = {"body": {"size": 11, "font": "Arial"}}
                data = window.report_settings.to_dict()
                data.update(project_title="Presupuesto", text_styles=styles)
                self.assertTrue(window._save_report_settings(data))
                self.assertEqual(window._read_report_settings().text_styles, {})
                window.is_dirty = False
                window.close()

                reopened = BudgetMainWindow()
                self.assertEqual(reopened.report_settings.text_styles, {})
                self.assertTrue(
                    reopened._load_collaborative_project(project_path)
                )
                self.assertEqual(reopened.report_settings.text_styles, styles)
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
                    "Informes", "Plantillas", "Tipo de proyecto y normas", "Idioma", "Apariencia", "Colaboración",
                    "Emplazamiento", "CPV", "Estilos de documentos",
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
            dialog.report_fields["organization"].setText("Entidad configurada")
            dialog.report_fields["service"].setText("Servicio configurado")
            self.assertTrue(dialog.show_author.isChecked())
            dialog.show_author.setChecked(False)
            dialog.discipline_checks["civil"].setChecked(True)
            dialog.discipline_checks["installations"].setChecked(True)
            dialog.standard_checks["une_157001"].setChecked(True)
            dialog.standard_checks["iso_10006"].setChecked(True)
            self.assertIn("ISO 10006:2017", dialog.standards_preview.toPlainText())
            self.assertNotIn("ISO 19650", dialog.standards_preview.toPlainText())
            dialog.report_fields["indirect_costs"].setText("7.5")
            report_template = str(Path.cwd() / "informes.odt")
            master_template = str(Path.cwd() / "maestro.odt")
            master_logo = str(Path.cwd() / "logo.jpg")
            dialog.template_path.setText(report_template)
            dialog.master_template_path.setText(master_template)
            dialog.master_logo_path.setText(master_logo)
            font, size = dialog.style_controls["heading1"]
            font.setCurrentText("Arial")
            size.setValue(18)
            values = dialog.values()
            self.assertEqual(values["text_styles"], {"heading1": {"font": "Arial", "size": 18}})
            self.assertEqual(values["project_title"], "Proyecto configurado")
            self.assertEqual(values["author"], "Autora Alfa\nAutor Beta")
            self.assertEqual(values["organization"], "Entidad configurada")
            self.assertEqual(values["service"], "Servicio configurado")
            self.assertEqual(values["template_path"], report_template)
            self.assertEqual(values["master_template_path"], master_template)
            self.assertEqual(values["master_logo_path"], master_logo)
            saved = ReportSettings.from_dict(values)
            reopened = SettingsDialog(window, ReportSettings.from_dict(saved.to_dict()),
                                      "Proyecto configurado", "es", lambda language: True)
            self.assertFalse(reopened.show_author.isChecked())
            self.assertEqual(reopened.report_fields["author"].toPlainText(), "Autora Alfa\nAutor Beta")
            self.assertEqual(reopened.report_fields["organization"].text(), "Entidad configurada")
            self.assertEqual(reopened.report_fields["service"].text(), "Servicio configurado")
            self.assertEqual(reopened.master_template_path.text(), master_template)
            self.assertEqual(reopened.master_logo_path.text(), master_logo)
            self.assertTrue(reopened.discipline_checks["civil"].isChecked())
            self.assertTrue(reopened.discipline_checks["installations"].isChecked())
            self.assertTrue(reopened.standard_checks["iso_10006"].isChecked())
            self.assertFalse(reopened.standard_checks["iso_19650_1"].isChecked())
            reopened.reject()
            self.assertEqual(values["indirect_costs"], "7.5")
            self.assertNotIn("output_format", values)
            dialog.reject()
            self.assertTrue(window._save_report_settings(values))
            target = Path(directory) / "proyecto.json"
            self.assertTrue(window._write_project_file(target))
            payload = json.loads(target.read_text(encoding="utf-8"))
            restored = ReportSettings.from_dict(payload["report_settings"])
            self.assertEqual(restored.document_standards, ["une_157001", "iso_10006"])
            self.assertEqual(restored.project_disciplines, ["civil", "installations"])
            self.assertEqual(window._read_report_settings().document_standards, [])
            self.assertEqual(window.default_report_settings.project_disciplines, [])
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
                "currency": "OTHER",
                "custom_currency": "Ptas",
            })
            self.assertTrue(window._save_report_settings(data))
            self.assertEqual(window.project_title, "Configuración Qt")
            self.assertEqual(window.report_settings.author, "Autora")
            self.assertFalse(window._read_report_settings().show_author)
            self.assertEqual(window.report_settings.indirect_costs, 3.5)
            self.assertEqual(window.report_settings.output_format, "pdf")
            self.assertTrue((folder / "configuracion_informes.json").is_file())
            saved_settings = json.loads(
                (folder / "configuracion_informes.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("output_format", saved_settings)
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

    def test_document_save_picker_uses_language_and_selected_format(self) -> None:
        """Traduce el diálogo y aplica la extensión del filtro escogido."""
        original_language = get_language()
        try:
            for language, caption in (
                ("es", "Generar anejo"),
                ("en", "Generate appendix"),
                ("fr", "Générer l’annexe"),
            ):
                set_language(language)
                for chosen, expected in (("ODT (*.odt)", "odt"), ("PDF (*.pdf)", "pdf")):
                    with self.subTest(language=language, chosen=chosen), tempfile.TemporaryDirectory() as directory, patch(
                        "qt_localization.QFileDialog.getSaveFileName",
                        return_value=(str(Path(directory) / "report.odt"), chosen),
                    ) as save_dialog:
                        selection = get_document_save_path(None, "Generar anejo", "report.pdf")
                        self.assertEqual(selection, (Path(directory) / f"report.{expected}", expected))
                        self.assertEqual(save_dialog.call_args.args[1:5], (
                            caption, "report.odt", "ODT (*.odt);;PDF (*.pdf)", "ODT (*.odt)",
                        ))
                        self.assertEqual(save_dialog.call_args.args[5], QFileDialog.Option.DontUseNativeDialog)
        finally:
            set_language(original_language)
            apply_button_language(self.application)

    def test_other_save_dialogs_use_global_language(self) -> None:
        """Traduce los títulos de exportación, proyecto y copia del diagrama."""
        original_language = get_language()
        try:
            with tempfile.TemporaryDirectory() as directory, patch(
                "budget_qt_window.user_data_directory", return_value=Path(directory),
            ), patch("budget_qt_window.apply_theme"):
                window = BudgetMainWindow()
                window.budget_items = [ProjectItem("P1", "01#", "ud", "Partida", 1, 10)]
                try:
                    for language, expected in (
                        ("en", ("Export budget", "Export measurements", "Export resources",
                                "Export breakdowns", "Save work plan",
                                "Select a folder for the collaborative project", "ODS spreadsheet (*.ods)")),
                        ("fr", ("Exporter le budget", "Exporter les métrés", "Exporter les ressources",
                                "Exporter les décompositions", "Enregistrer le planning des travaux",
                                "Sélectionnez un dossier pour le projet collaboratif", "Feuille ODS (*.ods)")),
                    ):
                        with self.subTest(language=language):
                            self.assertTrue(window._save_language_preference(language))
                            with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("", "")) as save:
                                window.export_budget_ods()
                                window.export_measurements_csv()
                                window.export_resource_report("Recursos")
                                window.export_resource_report("Descompuestos")
                                GanttPreviewDialogQt.save_copy(window)
                                self.assertEqual(tuple(call.args[1] for call in save.call_args_list), expected[:5])
                                self.assertEqual(save.call_args_list[2].args[3], expected[6])
                            with patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory", return_value="") as folder:
                                window.save_project_as()
                                self.assertEqual(folder.call_args.args[1], expected[5])
                finally:
                    window.is_dirty = False
                    window.close()
        finally:
            set_language(original_language)
            apply_button_language(self.application)

    def test_document_save_picker_confirms_overwrite_after_extension_change(self) -> None:
        """Pide confirmación si la extensión elegida apunta a otro archivo existente."""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.pdf"
            target.write_bytes(b"existing")
            with (
                patch("qt_localization.QFileDialog.getSaveFileName",
                      return_value=(str(target.with_suffix(".odt")), "PDF (*.pdf)")),
                patch("qt_localization.QMessageBox.question", return_value=QMessageBox.StandardButton.No) as confirm,
            ):
                self.assertIsNone(get_document_save_path(None, "Generar anejo", "report.pdf"))
                self.assertIn(target.name, confirm.call_args.args[2])
                self.assertEqual(target.read_bytes(), b"existing")
                confirm.return_value = QMessageBox.StandardButton.Yes
                self.assertEqual(get_document_save_path(None, "Generar anejo", "report.pdf"), (target, "pdf"))

    def test_document_save_picker_localizes_real_dialog_and_switches_format(self) -> None:
        """Comprueba etiquetas, columnas y cambio de formato en el selector visible."""
        original_language = get_language()
        try:
            with tempfile.TemporaryDirectory() as directory:
                for language, labels, headers, chosen in (
                    ("es", ("&Buscar en:", "&Nombre de archivo:", "&Tipo de archivo:"),
                     ("Nombre", "Tamaño", "Tipo", "Fecha de modificación"), "ODT (*.odt)"),
                    ("en", ("&Look in:", "File &name:", "Files of &type:"),
                     ("Name", "Size", "Type", "Date Modified"), "PDF (*.pdf)"),
                    ("fr", ("&Regarder dans :", "&Nom du fichier :", "&Type de fichiers :"),
                     ("Nom", "Taille", "Type", "Date de modification"), "PDF (*.pdf)"),
                ):
                    with self.subTest(language=language):
                        set_language(language)
                        apply_button_language(self.application)
                        observed = {}

                        def choose() -> None:
                            """Recoge los textos y confirma el formato seleccionado."""
                            dialog = self.application.activeModalWidget()
                            if not isinstance(dialog, QFileDialog):
                                return
                            observed["filter"] = dialog.selectedNameFilter()
                            observed["labels"] = tuple(
                                dialog.findChild(QLabel, name).text()
                                for name in ("lookInLabel", "fileNameLabel", "fileTypeLabel")
                            )
                            model = dialog.findChild(QTreeView, "treeView").model()
                            observed["headers"] = tuple(
                                model.headerData(i, Qt.Orientation.Horizontal) for i in range(4)
                            )
                            dialog.selectNameFilter(chosen)
                            dialog.accept()

                        QTimer.singleShot(100, choose)
                        selection = get_document_save_path(
                            None, "Generar anejo", str(Path(directory) / "report.pdf"),
                        )
                        extension = "odt" if chosen.startswith("ODT") else "pdf"
                        self.assertEqual(selection, (Path(directory) / f"report.{extension}", extension))
                        self.assertEqual(observed, {
                            "filter": "ODT (*.odt)", "labels": labels, "headers": headers,
                        })
        finally:
            set_language(original_language)
            apply_button_language(self.application)

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

    def test_price_justification_applies_k_and_updates_existing_indirect_costs(self) -> None:
        """Crea y actualiza %CI sin duplicarlo y guarda el mismo K en ambos archivos."""
        from tools.price_justification.indirect import default_rows

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("budget_qt_window.user_data_directory", return_value=Path(directory)),
            patch("budget_qt_window.apply_theme"),
            patch.object(QMessageBox, "critical") as error,
        ):
            window = BudgetMainWindow()
            item = ProjectItem("P1", "01#", "ud", "Partida", 4, 2500)
            window.budget_items = [item]
            window.chapter_order = ["01#"]
            window.project_title = "Proyecto de prueba"
            target = Path(directory) / "proyecto.json"
            window.project_path = target
            row = default_rows()[0] | {"monthly": 123.45}
            window.price_justification_state = {"indirect_rows": [row]}
            window._open_tool("price_justification")
            tool = window._project_windows[-1]
            try:
                self.assertEqual(item.indirect_components(), [])
                for monthly, percentage, price in (
                    (123.45, 2.23, 2555.75), (400, 3.0, 2575.0), (400, 3.0, 2575.0),
                ):
                    tool._state["indirect_rows"] = [row | {"monthly": monthly}]
                    tool.refresh_general_info()
                    self.assertEqual(tool.apply_k_button.isEnabled(),
                                     window.report_settings.indirect_costs != percentage)
                    tool.apply_k_button.click()
                    self.assertFalse(tool.apply_k_button.isEnabled())
                    self.assertEqual(window.report_settings.indirect_costs, percentage)
                    self.assertEqual(item.direct_unit_price, 2500)
                    self.assertEqual(item.unit_price, price)
                    self.assertEqual([(concept.code, amount) for concept, amount, _ in item.indirect_components()
                                      if concept.code == "%CI"], [("%CI", percentage)])
                    self.assertEqual(window._read_report_settings().indirect_costs, percentage)
                    data = json.loads(target.read_text(encoding="utf-8"))
                    self.assertEqual(data["report_settings"]["indirect_costs"], percentage)
                    self.assertEqual(data["price_justification"]["indirect_rows"][0]["monthly"], monthly)
                item.measurement = 0
                tool.refresh_general_info()
                self.assertFalse(tool.apply_k_button.isEnabled())
                error.assert_not_called()
            finally:
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
            from PySide6.QtWidgets import QPlainTextEdit
            from tools.rcd import demolition_measurements
            from tools.rcd.qt_window import DemolitionDialogQt
            from tools._shared.budget_sources import TRANSPORT_PREFIX

            self.assertFalse(tool.findChildren(QPlainTextEdit))
            self.assertTrue(tool.demolition_button.isEnabled())
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
            self.assertFalse(tool.save_button.isEnabled())
            dialog = DemolitionDialogQt(tool, demolition_measurements(window, tool.data))
            self.assertFalse(dialog.apply_button.icon().isNull())
            for include, factor, material in dialog.edits:
                include.setChecked(True)
                factor.setText("0,2")
                material.setCurrentIndex(next(index for index, (_, (key, _)) in
                                              enumerate(dialog.materials.items()) if key == "40"))
            with patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "critical") as error:
                dialog.apply_button.click()
                error.assert_not_called()
            transport = next(item for item in window.budget_items if any(
                line.comment.startswith(TRANSPORT_PREFIX) for line in item.measurement_lines
            ))
            self.assertAlmostEqual(transport.measurement, 24.0)
            self.assertFalse(tool.demolition_button.isEnabled())
            self.assertFalse(dialog.apply_button.isEnabled())
            window.budget_tree.setCurrentItem(None)
            window.budget_items.remove(transport)
            window.refresh_budget_tree()
            self.assertTrue(tool.demolition_button.isEnabled())
            tool.reject()
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_rcd_editor_revisions_are_used_in_styled_exports(self) -> None:
        """Guarda los ocho textos desde Qt y conserva su edición y estilo en PDF y ODT."""
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile

        from pypdf import PdfReader
        from odt_reports import _new_report, _tag
        from tools._shared.document_layout import template_paragraph_styles
        from tools._library_editor_qt import LibraryEditorQt
        from tools.rcd import write_report
        from tools.rcd.document import LIBRARY_PATH

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            library = folder / "rcd.zip"
            shutil.copyfile(LIBRARY_PATH, library)
            with (
                patch("budget_qt_window.user_data_directory", return_value=folder),
                patch("tools.rcd.qt_window.LIBRARY_PATH", library),
                patch("tools.rcd.document.LIBRARY_PATH", library),
                patch.object(QMessageBox, "critical") as error,
                patch.object(QMessageBox, "information"),
            ):
                window = BudgetMainWindow()
                try:
                    window.budget_items = [
                        ProjectItem("EXC", "01#", "m3", "Excavación de tierras", 10, 20),
                        ProjectItem("PAV", "02#", "m²", "Pavimento", 80, 10),
                    ]
                    window.pptp_state.setdefault("variables", {})["bullet_capitulos_obra"] = (
                        "- Descripción singular de la obra."
                    )
                    window._open_tool("rcd")
                    tool = window._project_windows[-1]
                    from tools.rcd import WASTES

                    waste_key = next(key for key, _, code, *_ in WASTES if code == "17 01 01")
                    metal_key = next(key for key, group, *_ in WASTES if group == "metals")
                    tool.data.selected = {key: key in (waste_key, metal_key) for key in tool.data.selected}
                    tool.data.groups["metals"]["percentage"] = 20
                    tool.data.evidence[waste_key] = [
                        "A: Demolición de hormigón [texto, revisar]",
                        "A: Demolición de hormigón [BCCA2023 → 17 01 01, revisar]",
                        "B: Retirada de hormigón [BCCA2023 → 17 01 01, revisar]",
                    ]
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
                    footer = "ANEJO Nº 18. ESTUDIO DE GESTIÓN DE RCDs"
                    law_urls = {
                        "https://www.boe.es/eli/es/l/2022/04/08/7/con",
                        "https://www.boe.es/eli/es/rd/2008/02/01/105/con",
                        "https://www.boe.es/eli/es/rd/2020/06/02/553/con",
                        "https://www.boe.es/eli/es/rd/2020/07/07/646/con",
                        "https://www.boe.es/eli/es/o/2017/10/10/apm1007/con",
                        "https://www.boe.es/eli/es/rd/2006/03/31/396/con",
                        "https://www.boe.es/eli/es/rd/1991/02/01/108/con",
                        "https://eur-lex.europa.eu/eli/dec/2000/532/oj?locale=es",
                    }
                    for output_format in ("odt", "pdf"):
                        window.report_settings.output_format = output_format
                        target = folder / f"estudio.{output_format}"
                        write_report(tool.data, "Proyecto RCD", window.report_settings, target, app=window)
                        if output_format == "odt":
                            with ZipFile(target) as archive:
                                content = ET.fromstring(archive.read("content.xml"))
                                styles = ET.fromstring(archive.read("styles.xml"))
                            self.assertTrue(any(footer in "".join(node.itertext())
                                                for node in styles.iter(_tag("style:footer"))))
                            self.assertTrue(law_urls <= {link.get(_tag("xlink:href"))
                                                         for link in content.iter(_tag("text:a"))})
                            text = " ".join(content.itertext())
                            body = content.find(f".//{_tag('office:text')}")
                            tables = [(index, table) for index, table in enumerate(body)
                                      if table.tag == _tag("table:table")]
                            self.assertGreaterEqual(len(tables), 7)
                            table_properties = {
                                style.get(_tag("style:name")): style.find(_tag("style:table-properties"))
                                for style in content.iter(_tag("style:style"))
                                if style.get(_tag("style:family")) == "table"
                            }
                            widths = [
                                float(table_properties[table.get(_tag("table:style-name"))]
                                      .get(_tag("style:width")).removesuffix("pt"))
                                for _, table in tables
                            ]
                            for number, (index, table) in enumerate(tables, start=1):
                                props = table_properties[table.get(_tag("table:style-name"))]
                                is_compact = 4 <= number <= 6
                                self.assertAlmostEqual(widths[number - 1] / widths[0],
                                                       0.8 if is_compact else 1.0, places=5)
                                self.assertEqual(props.get(_tag("table:align")),
                                                 "center" if is_compact else "left")
                                self.assertTrue("".join(body[index - 1].itertext()).startswith(f"Tabla {number}. "))
                                header = table.find(_tag("table:table-header-rows"))
                                self.assertIsNotNone(header)
                                self.assertTrue(all(
                                    cell.get(_tag("table:style-name")) == "RcdTableHeaderCell"
                                    for cell in header.iter(_tag("table:table-cell"))
                                ))
                            project_index, project_table = tables[0]
                            waste_rows = [
                                ["".join(cell.itertext()) for cell in row.findall(_tag("table:table-cell"))]
                                for row in tables[1][1].findall(_tag("table:table-row"))
                            ]
                            self.assertEqual(len(waste_rows), 3)
                            waste_rows = [row for row in waste_rows if row[2] != "Selección manual"]
                            self.assertEqual(waste_rows[0][0], "17 01 01")
                            self.assertEqual(waste_rows[0][2], "A: Demolición de hormigón")
                            self.assertEqual(waste_rows[1], ["", "", "B: Retirada de hormigón"])
                            heading_index = next(index for index, node in enumerate(body)
                                                 if node.tag == _tag("text:h") and
                                                 "1.2. DESCRIPCIÓN DE LA ACTUACIÓN" == "".join(node.itertext()))
                            self.assertGreater(heading_index, project_index)
                            description = body[heading_index + 1]
                            self.assertIsNotNone(description.find(_tag("text:tab")))
                            self.assertIn("Descripción singular de la obra.", "".join(description.itertext()))
                            for row in project_table.findall(_tag("table:table-row")):
                                label, value = row.findall(_tag("table:table-cell"))
                                self.assertEqual(label[0].get(_tag("text:style-name")), "TableTextRight")
                                self.assertEqual(value[0].get(_tag("text:style-name")), "TableText")
                                if label[0].text == "Emplazamiento":
                                    self.assertEqual("".join(value.itertext()), "")
                            backgrounds = {style.get(_tag("fo:background-color"))
                                           for style in content.iter(_tag("style:table-cell-properties"))}
                            self.assertTrue({"#4472C4", "#D9E5F5", "#FFFFFF"} <= backgrounds)
                            for number, expected in ((4, ("Right", "")), (5, ("Right", "Center"))):
                                row = tables[number - 1][1].find(_tag("table:table-row"))
                                styles = [cell[0].get(_tag("text:style-name")) for cell in row]
                                self.assertEqual(styles, ["TableText" + expected[0]] +
                                                 ["TableText" + expected[1]] * (len(styles) - 1))
                            threshold_table = next(table for _, table in tables
                                                   if "Umbral (t)" in "".join(table.itertext()))
                            headers = threshold_table.find(_tag("table:table-header-rows"))[0]
                            self.assertEqual(["".join(cell.itertext()) for cell in headers],
                                             ["Fracción", "Umbral (t)", "¿Se supera?"])
                            rows = {"".join(row[0].itertext()): row
                                    for row in threshold_table.findall(_tag("table:table-row"))}
                            self.assertEqual("".join(rows["Hormigón"][2].itertext()), "No")
                            self.assertEqual("".join(rows["Metales"][2].itertext()), "Sí")
                            self.assertEqual([cell[0].get(_tag("text:style-name")) for cell in rows["Metales"]],
                                             ["TableTextRight", "TableTextCenter", "TableTextCenter"])
                            headings = {
                                style.get(_tag("style:name")): style
                                for style in content.iter(_tag("style:style"))
                                if style.get(_tag("style:name"), "").startswith("RcdHeading")
                            }
                            template = _new_report("PRUEBA", "Proyecto RCD", window.report_settings)
                            template_styles = template_paragraph_styles(template.template_header)
                            for level in (1, 2):
                                self.assertEqual(
                                    headings[f"RcdHeading{level}"].get(_tag("style:parent-style-name")),
                                    template_styles[f"heading{level}"],
                                )
                            self.assertEqual(
                                headings["RcdHeading3"].find(_tag("style:text-properties"))
                                .get(_tag("fo:font-size")), "11pt",
                            )
                        else:
                            reader = PdfReader(target)
                            text = " ".join(page.extract_text() for page in reader.pages)
                            self.assertIn("Tabla 1. Datos del proyecto", " ".join(text.split()))
                            for page in reader.pages:
                                self.assertIn(footer, " ".join(page.extract_text().split()))
                            uri_actions = [
                                ref.get_object().get("/A", {})
                                for page in reader.pages for ref in page.get("/Annots", ())
                            ]
                            self.assertTrue(law_urls <= {action.get("/URI") for action in uri_actions})
                            fills = {tuple(round(float(value) * 255) for value in operands)
                                     for page in reader.pages for operands, operator in page.get_contents().operations
                                     if operator == b"rg"}
                            self.assertTrue({(68, 114, 196), (217, 229, 245)} <= fills)
                        for index in range(8):
                            self.assertIn(f"Texto RCD revisado {index}.", text)
                        normalized = " ".join(text.split())
                        ordered = ("1.1. ANTECEDENTES", "proyecto (Tabla 1).",
                                   "Tabla 1. Datos del proyecto", "1.2. DESCRIPCIÓN DE LA ACTUACIÓN",
                                   "Descripción singular de la obra.", "1.3. IDENTIFICACIÓN DE LOS RESIDUOS")
                        start = normalized.index("proyecto (Tabla 1).")
                        self.assertIn(ordered[0], normalized[:start])
                        offsets = [normalized.index(value, start) for value in ordered[1:]]
                        self.assertEqual(offsets, sorted(offsets))
                        self.assertNotIn("Véase la Tabla", normalized)
                        self.assertNotIn("(según PPTP)", normalized)
                        self.assertNotIn("DESCRIPCIÓN DE LA ACTUACIÓN:", normalized)
                        self.assertIn("Umbral (t) ¿Se supera?", normalized)
                        self.assertNotIn("PENDIENTE EN CONFIGURACIÓN GENERAL", normalized)
                        self.assertNotIn("Los apartados señalados como pendientes", normalized)
                        self.assertNotIn("[texto, revisar]", normalized)
                        self.assertNotIn("[BCCA2023", normalized)
                        self.assertNotIn("La selección automática es una ayuda", normalized)
                        self.assertNotIn("Inventario e inspecciones particulares:", normalized)
                        self.assertIn("Se realizará un inventario previo de residuos peligrosos y se preverá "
                                      "su retirada selectiva en demolición, rehabilitación, reparación y reforma.",
                                      normalized)
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
            self.assertTrue(proposal.insert_button.isEnabled())
            with patch.object(window, "save_project", return_value=False):
                self.assertTrue(proposal.create_chapter())
            generated = [
                row
                for row in window.budget_items
                if row.source.startswith("SS:")
            ]
            self.assertEqual(len(generated), 3)
            self.assertFalse(proposal.insert_button.isEnabled())
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

    def test_planning_phases_keep_export_and_monthly_progress_split(self) -> None:
        """Conserva el reparto al guardar y no imputa avance en el mes de pausa."""
        from datetime import date
        from xml.etree import ElementTree as ET
        from zipfile import ZipFile

        from pypdf import PdfReader
        from tools.certifications import economic_months, planned_monthly_amounts
        from tools.planning.document import write_planning_report

        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            self.addCleanup(QCoreApplication.sendPostedEvents, None, QEvent.Type.DeferredDelete)
            self.addCleanup(window.deleteLater)
            self.addCleanup(window.close)
            window.budget_items = [ProjectItem("P1", "01#", "m²", "Pavimento", 100, 10, rendimiento=0.8)]
            window.planning_state = {
                "params": {"start": "2027-01-04", "holidays": [], "phases": [
                    {"name": "Tramo norte"}, {"name": "Tramo sur", "lag": 40},
                ]},
                "activities": [{"chapter": "01#", "code": "P1", "phase_shares": {"0": 40, "1": 60}}],
            }
            tool = PlanningWindowQt(window)
            activity = tool.activities[0]
            self.assertEqual(tool.table.rowCount(), 1)
            self.assertEqual(tool.to_state()["activities"][0]["phase_shares"], {"0": 40, "1": 60})
            self.assertEqual([(segment.quantity, segment.duration) for segment in activity.segments], [(40, 4), (60, 6)])
            self.assertEqual(
                [segment.start_date for segment in activity.segments],
                [date(2027, 1, 4), date(2027, 3, 5)],
            )
            self.assertEqual(planned_monthly_amounts(
                tool.activities, tool.params, economic_months(tool.params.start, 3),
            ), [[400, 0, 600]])
            for output_format in ("pdf", "odt"):
                target = Path(directory) / f"plan.{output_format}"
                settings = ReportSettings(output_format=output_format)
                write_planning_report(tool.activities, tool.params, target, settings, "Pavimentación", str)
                if output_format == "pdf":
                    text = " ".join(page.extract_text() for page in PdfReader(target).pages)
                else:
                    with ZipFile(target) as archive:
                        text = " ".join(ET.fromstring(archive.read("content.xml")).itertext())
                self.assertIn("[Tramo norte]", text)
                self.assertIn("[Tramo sur]", text)
            with patch("tools.planning.qt_window.tempfile.gettempdir", return_value=directory):
                png, pdf = tool._generate_figure()
                self.assertTrue(png.is_file())
                self.assertTrue(pdf.is_file())
            window.is_dirty = False

    def test_overlapping_phases_do_not_overlap_segments_of_one_activity(self) -> None:
        """Adelanta la siguiente fase sin programar dos veces a la vez la misma partida."""
        from datetime import date
        from tools._shared.scheduling import PlanActivity, PlanParams, PlanPhase, schedule, validate_phase_shares

        items = [
            PlanActivity(1, "01#", "", "A", "", "m", 100, 1000, 0.8, phase_shares={0: 40, 1: 60}),
            PlanActivity(2, "01#", "", "B", "", "m", 80, 800, 1, phase_shares={0: 100}),
            PlanActivity(3, "01#", "", "C", "", "m", 8, 80, 1, phase_shares={1: 100}),
            PlanActivity(4, "02#", "", "D", "", "ud", 1, 30, 0, continuous=True),
        ]
        params = PlanParams(start=date(2027, 1, 4), holidays=[], phases=[PlanPhase("A"), PlanPhase("B", -8)])
        schedule(items, params)
        first, second = items[0].segments
        self.assertEqual(first.finish_offset, second.start_offset)
        self.assertEqual(items[2].start_offset, 2)
        self.assertLess(items[2].start_offset, items[1].finish_offset)
        self.assertEqual(items[3].finish_offset, 10)
        params.phases[1] = PlanPhase("B")
        schedule(items, params)
        self.assertEqual(items[2].start_offset, items[1].finish_offset)
        with self.assertRaisesRegex(ValueError, "sumar 100"):
            validate_phase_shares({0: 40, 1: 50}, 2)

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
        """Conserva los buscadores locales y abre SIRA en el navegador externo."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("budget_qt_window.user_data_directory", return_value=Path(directory)),
        ):
            window = BudgetMainWindow()
            try:
                menu = next(action.menu() for action in window.menuBar().actions()
                            if action.text() == "Consultas")
                entries = menu.actions()
                self.assertEqual([entry.text() for entry in entries], [
                    "NTP", "UNE", "LER", "Instalaciones gestoras/productoras de residuos",
                ])
                with patch.object(window, "open_reference_search") as open_catalogue:
                    for entry in entries[:3]:
                        self.assertTrue(entry.isIconVisibleInMenu())
                        self.assertFalse(entry.icon().isNull())
                        entry.trigger()
                        open_catalogue.assert_called_with(entry.text())
                with patch("budget_qt_window.webbrowser.open", return_value=True) as browser:
                    entries[3].trigger()
                    browser.assert_called_once_with(
                        "https://www.juntadeandalucia.es/medioambiente/sira-buscador-publico/",
                    )
                with (
                    patch("budget_qt_window.webbrowser.open", return_value=False),
                    patch.object(QMessageBox, "critical") as error,
                ):
                    entries[3].trigger()
                    error.assert_called_once()
                    self.assertEqual(error.call_args.args[1], "SIRA")
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

    def test_budget_tool_buttons_do_not_use_the_save_icon(self) -> None:
        """Distingue la incorporación y la aplicación de K del guardado en cada idioma."""
        from budget_qt_theme import decorate_widget, icon_for_text
        from localization import translate

        labels = (
            "Guardar RCDs en el proyecto", "Aceptar y crear capítulo",
            "Incorporar mediciones al presupuesto", "Aplicar K al presupuesto",
            "Llevar mediciones de demolición a transporte a vertedero",
            "Copiar mediciones y usar volúmenes en RCDs",
        )
        save = icon_for_text(self.application, "Guardar anejo…").pixmap(24, 24).toImage()
        self.assertFalse(save.isNull())
        for language in ("es", "en", "fr"):
            dialog = QDialog()
            buttons = []
            for label in labels:
                button = QPushButton(translate(label, language), dialog)
                button.setProperty("sourceButtonText", label)
                buttons.append(button)
            decorate_widget(dialog)
            for label, button in zip(labels, buttons, strict=True):
                with self.subTest(language=language, label=label):
                    image = button.icon().pixmap(24, 24).toImage()
                    self.assertFalse(image.isNull())
                    self.assertNotEqual(image, save)

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
