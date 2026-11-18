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

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox
from PySide6.QtCore import Qt

from budget_core import Bc3Dataset
from budget_core import ProjectItem
from budget_qt_settings import SettingsDialog
from budget_qt_window import BudgetMainWindow
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
            from tests.test_invoicing import set_budget

            set_budget(window)
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


if __name__ == "__main__":
    unittest.main()
