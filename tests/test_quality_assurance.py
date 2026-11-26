"""Flujo de revisión, inserción y exportación del plan de calidad."""
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from budget_core import ProjectItem
from budget_project import build_project_payload, decode_project_payload
from budget_qt_window import BudgetMainWindow
from odt_reports import _tag
from tools._library_history import read_revisions
from tools.quality_assurance.catalog import load_catalog
from tools.quality_assurance.document import LIBRARY_PATH, write_document
from tools.quality_assurance.plan import SOURCE, applicable_trials, insert_budget, suggested_quantity
from tools.quality_assurance.pricing import estimate_empty_price, price_candidates, price_catalog


class QualityAssuranceTests(unittest.TestCase):
    """Comprueba límites de frecuencia y el ciclo real de revisión."""

    def test_surface_frequency(self):
        """Respeta la frontera de 500 m² y no convierte toneladas a superficie."""
        for unit, measure, count in (("m²", 500, 1), ("m2", 500.01, 2), ("t", 500, None)):
            with self.subTest(unit=unit, measure=measure):
                item = ProjectItem("P", "01#", unit, "Mezcla bituminosa", measure)
                self.assertEqual(suggested_quantity(item, "asphalt"), count)

    def test_workbook_catalog_and_unit_matches(self):
        """Carga los catálogos PAC y propone controles para unidades adicionales."""
        entries = load_catalog()
        by_id = {entry.identifier: entry for entry in entries}
        self.assertEqual(sum(identifier.startswith("PAC-") for identifier in by_id), 68)
        self.assertEqual(sum(identifier.startswith("ENS-") for identifier in by_id), 31)
        self.assertIn("Zahorras", by_id["PAC-30"].label)
        self.assertIn("UNE-EN 16165", by_id["ENS-19"].reference)
        for summary, expected in (
            ("Subbase granular de zahorra artificial", "PAC-30"),
            ("Valla metálica de protección de baja altura", "PAC-56"),
            ("Mezcla bituminosa en caliente", "PAC-34"),
        ):
            item = ProjectItem("P", "01#", "m²", summary, 100)
            with self.subTest(summary=summary):
                self.assertIn(expected, applicable_trials(item))
        with ZipFile(LIBRARY_PATH) as archive:
            for name in ("02_normativa.md", "05_ensayos.md", "06_no_conformidades.md",
                         "07_presupuesto.md", "08_catalogo_pac.md", "09_ensayos_clave.md"):
                source = (LIBRARY_PATH.parent / name).read_bytes().replace(b"\r\n", b"\n")
                content = archive.read(name)
                self.assertEqual(content, source)
                revisions, _remainder = read_revisions(content.decode("utf-8-sig"))
                self.assertEqual(len(revisions), 1 if name.startswith(("08_", "09_")) else 2)

    def test_manual_catalog_add(self):
        """Añade una ficha seleccionada con su referencia y detalle."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"):
            window = BudgetMainWindow()
            try:
                window.budget_items = [
                    ProjectItem("FENCE", "01#", "m", "Valla metálica de protección", 10)
                ]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                tool.catalog.setCurrentIndex(tool.catalog.findData("PAC-68"))
                tool.add_catalog()
                row = tool.rows()[-1]
                self.assertEqual(row["trial"], "PAC-68")
                self.assertIn("LOE", row["reference"])
                self.assertIn("Ensayos e inspecciones", row["details"])
                self.assertEqual(tool.table.item(tool.table.rowCount() - 1, 7).text(), row["reference"])
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_bcca_price_selection_and_provenance(self):
        """La selección es explícita y no conserva una referencia para un precio manual."""
        self.assertEqual([(entry.code, entry.price) for entry in price_candidates("", "18ALF00010")],
                         [("18ALF00010", 44.0)])
        self.assertFalse(any(entry.unit.casefold() not in {"u", "ud"} for entry in price_catalog()))
        self.assertFalse(any(entry.code == "18ASS000201" for entry in price_catalog()))
        self.assertFalse(price_candidates("Densidad in situ"))
        self.assertEqual({entry.code for entry in price_candidates(
            "Probetas y consistencia (cono de Abrams)")}, {"18HHL00010", "18HHL00020"})
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch(
            "tools.quality_assurance.qt_window.QInputDialog.getText", return_value=("18ALF00010", True)
        ), patch(
            "tools.quality_assurance.qt_window.QInputDialog.getItem",
            return_value=("18ALF00010 — Análisis granulométrico de suelos — 44 €/ud (BCCA 2023)", True)
        ), patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
            window = BudgetMainWindow()
            try:
                window.budget_items = [ProjectItem("SUELO", "01#", "m²", "Subbase de zahorra", 50)]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                tool.table.setCurrentCell(0, 2)
                tool.table.item(0, 6).setText("99")
                tool.choose_price()
                self.assertEqual(tool.rows()[0]["price"], "99")
                self.assertNotIn("price_source", tool.rows()[0])
                question.return_value = QMessageBox.StandardButton.Yes
                tool.choose_price()
                row = tool.rows()[0]
                self.assertEqual((row["price"], row["price_source"]["code"]), ("44.0", "18ALF00010"))
                tool.table.item(0, 6).setText("45")
                self.assertNotIn("price_source", tool.rows()[0])
                tool.choose_price()
                tool.table.item(0, 4).setText("1")
                tool.review()
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "p.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"][0]["price_source"]["code"], "18ALF00010")
                report = Path(folder) / "precio.odt"
                write_document(window, report, [row for row in tool.rows() if row["included"]])
                with ZipFile(report) as archive:
                    text = " ".join(ET.fromstring(archive.read("content.xml")).itertext())
                self.assertIn("18ALF00010", text)
                self.assertIn("Precio directo por ensayo", text)
                window.report_settings.currency = "USD"
                with self.assertRaisesRegex(ValueError, "euros"):
                    insert_budget(window, [tool.rows()[0]])
                window.report_settings.currency = "EUR"
                tool.insert()
                inserted = next(item for item in window.budget_items if item.source == SOURCE)
                self.assertIn("18ALF00010", inserted.text)
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_estimates_only_for_known_unedited_empty_prices(self):
        """No extrapola precios a controles desconocidos, modificados o en otra moneda."""
        from tools.quality_assurance.plan import TRIALS

        row = {"trial": "concrete", "name": TRIALS["concrete"].name, "price": ""}
        estimated = estimate_empty_price(row, "EUR")
        self.assertEqual(estimated["price"], "168.29")
        self.assertIn("151,26", estimated["price_source"]["summary"])
        self.assertIn("borm.es", estimated["price_source"]["url"])
        for changed, currency in (
            (row | {"price": "25"}, "EUR"),
            (row | {"name": "Otro control"}, "EUR"),
            (row | {"trial": "PAC-61"}, "EUR"),
            (row, "USD"),
        ):
            self.assertEqual(estimate_empty_price(changed, currency), changed)

    def test_estimated_prices_restore_review_and_follow_exports(self):
        """Rellena huecos sin pisar precios ni revisiones ya insertadas y conserva el alcance."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            window = BudgetMainWindow()
            try:
                work = ProjectItem("ZAH", "01#", "m²", "Subbase de zahorra", 50)
                window.budget_items = [work]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                row = tool.rows()[0]
                self.assertEqual((row["trial"], row["price"]), ("plate", "120.00"))
                self.assertIn("desplazamiento", tool.table.item(0, 6).toolTip())
                tool.table.item(0, 4).setText("1")
                tool.review()
                saved = window.quality_assurance_state["rows"]
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "p.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"][0]["price_source"], saved[0]["price_source"])
                report = Path(folder) / "estimacion.odt"
                write_document(window, report, [row for row in tool.rows() if row["included"]])
                with ZipFile(report) as archive:
                    text = " ".join(ET.fromstring(archive.read("content.xml")).itertext())
                self.assertIn("Estimación orientativa", text)
                self.assertIn("geocesa.com", text)
                self.assertIn("elemento reactivo", text)
                tool.insert()
                inserted = next(item for item in window.budget_items if item.source == SOURCE)
                self.assertIn("Estimación orientativa", inserted.text)
                self.assertIn("geocesa.com", inserted.text)
                self.assertIn("desplazamiento", inserted.text)
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_saved_review_gains_estimate_only_before_insertion(self):
        """Pide confirmar de nuevo las revisiones antiguas y respeta precios editados."""
        from tools.quality_assurance.qt_window import QualityWindow

        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"):
            window = BudgetMainWindow()
            dialogs = []
            try:
                work = ProjectItem("ZAH", "01#", "m²", "Subbase de zahorra", 50)
                window.budget_items = [work]
                dialog = QualityWindow(window)
                dialogs.append(dialog)
                saved = dialog.rows()
                saved[0]["price"] = ""
                saved[0].pop("price_source")
                saved[1]["price"] = "33"
                window.quality_assurance_state = {"rows": saved}
                reopened = QualityWindow(window)
                dialogs.append(reopened)
                self.assertEqual(reopened.rows()[0]["price"], "120.00")
                self.assertEqual(reopened.rows()[1]["price"], "33")
                self.assertFalse(reopened.reviewed)
                window.budget_items.append(ProjectItem("CC-001", "CALIDAD#", "ud", "Ensayo", source=SOURCE))
                inserted = QualityWindow(window)
                dialogs.append(inserted)
                self.assertEqual(inserted.rows()[0]["price"], "")
                self.assertEqual(inserted.rows()[1]["price"], "33")
            finally:
                for dialog in dialogs:
                    dialog.close()
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_review_budget_and_exports(self):
        """Valida la revisión, la inserción única y ambos formatos editados."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ), patch.object(
            QMessageBox, "information"
        ):
            window = BudgetMainWindow()
            try:
                work = ProjectItem("ASF", "01#", "m²", "Mezcla bituminosa", 750, 20)
                window.budget_items = [work]
                window.chapter_order = ["01#", "09#"]
                window.chapter_titles["09#"] = "Control de Calidad"
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                self.assertEqual(tool.table.rowCount(), 3)
                self.assertEqual(tool.table.item(0, 4).text(), "2")
                self.assertEqual(tool.table.item(1, 0).checkState().name, "Unchecked")
                self.assertGreaterEqual(tool.catalog.findData("PAC-68"), 0)
                self.assertFalse(tool.insert_button.isEnabled())
                tool.review()
                self.assertTrue(tool.export_button.isEnabled())
                self.assertTrue(tool.insert_button.isEnabled())
                tool.table.item(0, 6).setText("120,50")
                tool.table.item(2, 0).setCheckState(Qt.CheckState.Checked)
                tool.table.item(2, 4).setText("1")
                tool.table.item(2, 6).setText("80")
                self.assertFalse(tool.export_button.isEnabled())
                tool.review()
                self.assertTrue(tool.insert_button.isEnabled())
                tool.insert_button.click()
                inserted, pac_control = [item for item in window.budget_items if item.source == SOURCE]
                self.assertEqual((inserted.chapter, inserted.measurement, inserted.unit_price), ("09#", 2, 120.5))
                self.assertIn("PG-3 Art. 542", pac_control.text)
                self.assertIn("Detalle del control", pac_control.text)
                self.assertFalse(tool.insert_button.isEnabled())
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "proyecto.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"], tool.rows())
                self.assertEqual([item.measurement for item in restored.items if item.source == SOURCE], [2, 1])
                with self.assertRaisesRegex(ValueError, "ya se ha incorporado"):
                    insert_budget(window, tool.rows())
                archive_path = Path(folder) / "fichas.zip"
                shutil.copyfile(LIBRARY_PATH, archive_path)
                window.library_sources["quality_assurance"] = str(archive_path)
                tool.open_library()
                self.assertEqual(tool.editor.members.count(), 9)
                tool.editor.members.setCurrentRow(0)
                tool.editor.author.setText("Autor de prueba")
                tool.editor.editor.appendPlainText("Texto revisado en la ficha.")
                self.assertTrue(tool.editor.save())
                for kind in ("odt", "pdf"):
                    with self.subTest(kind=kind):
                        window.report_settings.output_format = kind
                        target = Path(folder) / f"calidad.{kind}"
                        write_document(window, target, [row for row in tool.rows() if row["included"]])
                        if kind == "odt":
                            with ZipFile(target) as archive:
                                xml = ET.fromstring(archive.read("content.xml"))
                            self.assertEqual(len(list(xml.iter(_tag("table:table")))), 2)
                            links = {node.get(_tag("xlink:href")) for node in xml.iter(_tag("text:a"))}
                            text = " ".join(xml.itertext())
                        else:
                            pages = PdfReader(target).pages
                            text = " ".join(page.extract_text() for page in pages)
                            links = {
                                str(action.get("/URI"))
                                for page in pages for annotation in page.get("/Annots", [])
                                if (action := annotation.get_object().get("/A")) and action.get("/URI")
                            }
                        self.assertIn("https://www.boe.es/buscar/act.php?id=BOE-A-2003-22787", links)
                        normalized = " ".join(text.split())
                        for expected in ("ANEJO Nº 17", "Tabla 2.", "ASF", "321,00", "Texto revisado en la ficha.",
                                         "6. GESTIÓN DE NO CONFORMIDADES", "8. CATÁLOGO DE CONTROLES PAC",
                                         "9. FICHAS TÉCNICAS DE ENSAYOS CLAVE",
                                         "Ensayos e inspecciones", "ENS-14", "CATÁLOGO"):
                            self.assertIn(expected, normalized)
                        self.assertNotIn("CAT?LOGO", normalized)
                        self.assertNotIn("Estado en obra: Pendiente", normalized)
                work.measurement = 800
                window.refresh_budget_tree()
                self.assertFalse(tool.export_button.isEnabled())
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()
