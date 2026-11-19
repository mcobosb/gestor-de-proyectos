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

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox, QPushButton
from PySide6.QtCore import Qt

from budget_core import Bc3Dataset, Concept
from budget_core import ProjectItem
from budget_qt_settings import SettingsDialog
from budget_qt_window import BudgetMainWindow
from budget_qt_theme import apply_theme
from budget_reports import ReportSettings
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
            ]
            window.chapter_order = ["01#", "02#"]
            window._open_tool("rcd")
            tool = window._project_windows[-1]
            self.assertIsInstance(tool, RcdWindowQt)
            window._open_tool("rcd")
            self.assertEqual(len(window._project_windows), 1)
            self.assertEqual(tool.waste_table.rowCount(), 0)
            tool.variables["surface"].setText("100")
            tool.accept_inputs()
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
                json.dumps({"items": [], "rcds": {"documentation": {"reuse": "Texto"}}}),
                encoding="utf-8",
            )
            self.assertTrue(window._load_project_file(path))
            self.assertEqual(window.rcd_state["documentation"]["reuse"], "Texto")
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

    def test_main_qt_menus_cover_queries_exports_printing_and_information(self) -> None:
        """No deja opciones heredadas fuera de los menús principales Qt."""
        with tempfile.TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory)
        ):
            window = BudgetMainWindow()
            menus = {
                action.text(): action.menu()
                for action in window.menuBar().actions()
            }
            export_labels = [
                action.text() for action in menus["Imprimir y exportar"].actions()
            ]
            for expected in (
                "Vista previa del presupuesto…",
                "Imprimir presupuesto",
                "Exportar presupuesto a ODS…",
                "Exportar mediciones a CSV…",
                "Exportar recursos a ODS…",
                "Exportar descompuestos a ODS…",
            ):
                self.assertIn(expected, export_labels)
            query_labels = [
                action.text() for action in menus["Consultas"].actions()
            ]
            self.assertEqual(query_labels, ["NTP", "UNE", "LER"])
            info_labels = [
                action.text() for action in menus["Información"].actions()
            ]
            self.assertIn("Bases de precios de referencia", info_labels)
            self.assertIn("Licencia y responsabilidad…", info_labels)
            self.assertIn("Apoyar el proyecto (Buy Me a Coffee)", info_labels)
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
