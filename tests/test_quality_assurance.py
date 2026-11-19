"""Flujo de revisión, inserción y exportación del plan de calidad."""
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader
from PySide6.QtWidgets import QApplication, QMessageBox

from budget_core import ProjectItem
from budget_project import build_project_payload, decode_project_payload
from budget_qt_window import BudgetMainWindow
from odt_reports import _tag
from tools.quality_assurance.document import LIBRARY_PATH, write_document
from tools.quality_assurance.plan import SOURCE, insert_budget, suggested_quantity


class QualityAssuranceTests(unittest.TestCase):
    """Comprueba límites de frecuencia y el ciclo real de revisión."""

    def test_surface_frequency(self):
        """Respeta la frontera de 500 m² y no convierte toneladas a superficie."""
        for unit, measure, count in (("m²", 500, 1), ("m2", 500.01, 2), ("t", 500, None)):
            with self.subTest(unit=unit, measure=measure):
                item = ProjectItem("P", "01#", unit, "Mezcla bituminosa", measure)
                self.assertEqual(suggested_quantity(item, "asphalt"), count)

    def test_review_budget_and_exports(self):
        """Valida la revisión, la inserción única y ambos formatos editados."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            window = BudgetMainWindow()
            try:
                work = ProjectItem("ASF", "01#", "m²", "Mezcla bituminosa", 750, 20)
                window.budget_items = [work]
                window.chapter_order = ["01#", "09#"]
                window.chapter_titles["09#"] = "Control de Calidad"
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                self.assertEqual(tool.table.rowCount(), 1)
                self.assertEqual(tool.table.item(0, 4).text(), "2")
                self.assertFalse(tool.insert_button.isEnabled())
                tool.review()
                self.assertTrue(tool.export_button.isEnabled())
                self.assertFalse(tool.insert_button.isEnabled())
                tool.table.item(0, 6).setText("120,50")
                self.assertFalse(tool.export_button.isEnabled())
                tool.review()
                self.assertTrue(tool.insert_button.isEnabled())
                tool.insert_button.click()
                inserted, = [item for item in window.budget_items if item.source == SOURCE]
                self.assertEqual((inserted.chapter, inserted.measurement, inserted.unit_price), ("09#", 2, 120.5))
                self.assertFalse(tool.insert_button.isEnabled())
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "proyecto.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"], tool.rows())
                self.assertEqual([item.measurement for item in restored.items if item.source == SOURCE], [2])
                with self.assertRaisesRegex(ValueError, "ya se ha incorporado"):
                    insert_budget(window, tool.rows())
                archive_path = Path(folder) / "fichas.zip"
                shutil.copyfile(LIBRARY_PATH, archive_path)
                window.library_sources["quality_assurance"] = str(archive_path)
                tool.open_library()
                self.assertEqual(tool.editor.members.count(), 7)
                tool.editor.members.setCurrentRow(0)
                tool.editor.author.setText("Autor de prueba")
                tool.editor.editor.appendPlainText("Texto revisado en la ficha.")
                self.assertTrue(tool.editor.save())
                for kind in ("odt", "pdf"):
                    with self.subTest(kind=kind):
                        window.report_settings.output_format = kind
                        target = Path(folder) / f"calidad.{kind}"
                        write_document(window, target, tool.rows())
                        if kind == "odt":
                            with ZipFile(target) as archive:
                                xml = ET.fromstring(archive.read("content.xml"))
                            self.assertEqual(len(list(xml.iter(_tag("table:table")))), 2)
                            text = " ".join(xml.itertext())
                        else:
                            text = " ".join(page.extract_text() for page in PdfReader(target).pages)
                        normalized = " ".join(text.split())
                        for expected in ("ANEJO Nº 17", "Tabla 2.", "ASF", "241,00", "Texto revisado en la ficha.",
                                         "6. GESTIÓN DE NO CONFORMIDADES"):
                            self.assertIn(expected, normalized)
                work.measurement = 800
                window.refresh_budget_tree()
                self.assertFalse(tool.export_button.isEnabled())
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()
