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

from PySide6.QtWidgets import QApplication, QDialogButtonBox

from budget_core import Bc3Dataset
from budget_qt_settings import SettingsDialog
from budget_qt_window import BudgetMainWindow
from budget_reports import ReportSettings


ROOT = Path(__file__).resolve().parent.parent


class BudgetQtWindowTests(unittest.TestCase):
    """Comprueba el ciclo mínimo de importación y persistencia Qt."""

    @classmethod
    def setUpClass(cls) -> None:
        """Crea una única aplicación Qt para las ventanas de prueba."""
        cls.application = QApplication.instance() or QApplication([])

    def test_import_and_reopen_project_uses_core_model_without_gui(self) -> None:
        """Importa un BC3, guarda el JSON y lo reabre con el mismo modelo."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "presupuesto.bc3"
            shutil.copyfile(ROOT / "tests" / "test.bc3", source)
            with patch("budget_qt_window.user_data_directory", return_value=folder):
                window = BudgetMainWindow()
                window._load_budget_file(source)
                expected_items = len(window.budget_items)
                project_path = window.project_path
                self.assertIsNotNone(project_path)
                self.assertTrue(project_path.is_file())
                self.assertEqual(
                    window.budget_tree.topLevelItemCount(),
                    len(window.chapter_order),
                )
                window.close()

                reopened = BudgetMainWindow()
                self.assertTrue(reopened._load_project_file(project_path))
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
                "Abrir proyecto JSON…",
                "Guardar",
                "Guardar como (JSON)…",
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
                ["Informes", "Idioma", "Apariencia", "Emplazamiento", "CPV"],
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
            dialog.report_fields["author"].setText("Autor")
            dialog.report_fields["indirect_costs"].setText("7.5")
            dialog.output_format.setCurrentText("ODT")
            values = dialog.values()
            self.assertEqual(values["project_title"], "Proyecto configurado")
            self.assertEqual(values["author"], "Autor")
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
                "indirect_costs": "3.5",
                "output_format": "odt",
                "currency": "OTHER",
                "custom_currency": "Ptas",
            })
            self.assertTrue(window._save_report_settings(data))
            self.assertEqual(window.project_title, "Configuración Qt")
            self.assertEqual(window.report_settings.author, "Autora")
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


if __name__ == "__main__":
    unittest.main()
