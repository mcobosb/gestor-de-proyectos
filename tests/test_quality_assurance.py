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
            for name in ("08_catalogo_pac.md", "09_ensayos_clave.md"):
                source = (LIBRARY_PATH.parent / name).read_bytes().replace(b"\r\n", b"\n")
                content = archive.read(name)
                self.assertEqual(content, source)
                revisions, _remainder = read_revisions(content.decode("utf-8-sig"))
                self.assertEqual(len(revisions), 1)

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
                self.assertFalse(tool.insert_button.isEnabled())
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
                            text = " ".join(xml.itertext())
                        else:
                            text = " ".join(page.extract_text() for page in PdfReader(target).pages)
                        normalized = " ".join(text.split())
                        for expected in ("ANEJO Nº 17", "Tabla 2.", "ASF", "321,00", "Texto revisado en la ficha.",
                                         "6. GESTIÓN DE NO CONFORMIDADES", "8. CATÁLOGO DE CONTROLES PAC",
                                         "9. FICHAS TÉCNICAS DE ENSAYOS CLAVE",
                                         "Ensayos e inspecciones"):
                            self.assertIn(expected, normalized)
                work.measurement = 800
                window.refresh_budget_tree()
                self.assertFalse(tool.export_button.isEnabled())
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()
