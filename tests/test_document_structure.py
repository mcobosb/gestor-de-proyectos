"""Pruebas de la estructura documental y de sus plantillas ODT."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from budget_qt_window import BudgetMainWindow
from tools.document_structure.catalog import TEMPLATES, copy_templates, template_paths
from tools.document_structure.generation import generate, generated_at
from tools.document_structure.office import MASTER_NAME
from odt_reports import _tag
from tools.document_structure.qt_window import DocumentStructureWindow


class DocumentStructureTests(unittest.TestCase):
    """Comprueba el catálogo distribuido y el recorrido desde Herramientas."""

    def test_templates_are_editable_and_existing_files_are_preserved(self) -> None:
        """Copia los ODT originales y preserva los documentos ya editados."""
        paths = template_paths()
        self.assertEqual(len(paths), 29)
        self.assertEqual(set(paths), {path.relative_to(TEMPLATES) for path in TEMPLATES.rglob("*.odt")})
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            self.assertEqual(copy_templates(destination, list(paths)), (29, 0))
            for relative, entry in paths.items():
                with ZipFile(destination / relative) as doc:
                    self.assertEqual(doc.read("mimetype"), b"application/vnd.oasis.opendocument.text")
                    text = list(ET.fromstring(doc.read("content.xml")).itertext())
                    self.assertIn(entry.title, text)
                    self.assertIn("Referencias y documentación de apoyo", text)
                    for heading in entry.outline:
                        self.assertIn(heading, text)
            relative = next(iter(paths))
            (destination / relative).write_bytes(b"edited")
            self.assertEqual(copy_templates(destination, [relative]), (0, 1))
            self.assertEqual((destination / relative).read_bytes(), b"edited")

    def test_generation_receipt_and_office_documents(self) -> None:
        """Personaliza los ODT, enlaza el maestro y no permite una segunda generación."""
        with TemporaryDirectory() as directory:
            project = Path(directory) / "proyecto.json"
            project.write_text("{}", encoding="utf-8")
            destination = Path(directory) / "entrega"
            destination.mkdir()
            paths = list(template_paths())[:3]
            self.assertEqual(copy_templates(destination, paths[:1]), (1, 0))
            self.assertEqual(generate(project, destination, paths[:1], "Proyecto de prueba"), (0, 1))
            self.assertIsNone(generated_at(project))
            self.assertEqual(generate(project, destination, paths, "Proyecto de prueba"), (2, 1))
            self.assertIsNotNone(generated_at(project))
            with ZipFile(destination / paths[1]) as doc:
                styles = ET.fromstring(doc.read("styles.xml"))
                self.assertIn("Proyecto de prueba", "".join(styles.find(f".//{_tag('style:header')}").itertext()))
                footer = styles.find(f".//{_tag('style:footer')}")
                self.assertIn(" · PÁGINA ", "".join(footer.itertext()))
                self.assertIsNotNone(footer.find(f".//{_tag('text:page-number')}"))
            with ZipFile(destination / MASTER_NAME) as master:
                self.assertEqual(master.read("mimetype"), b"application/vnd.oasis.opendocument.text-master")
                content = ET.fromstring(master.read("content.xml"))
                refs = [unquote(node.attrib[_tag("xlink:href")])
                        for node in content.iter(_tag("text:section-source"))]
                self.assertEqual(refs, [f"../{path.as_posix()}" for path in paths[1:]])
            with self.assertRaisesRegex(ValueError, "Ya se ha generado"):
                generate(project, destination, paths, "Proyecto de prueba")

    def test_menu_checklist_and_copy(self) -> None:
        """Abre el índice sin proyecto y exige guardarlo antes de habilitar la copia."""
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            window = BudgetMainWindow()
            try:
                tools = next(action.menu() for action in window.menuBar().actions()
                             if action.text() == "Herramientas")
                action = tools.actions()[0]
                self.assertEqual(action.text(), "Estructura documental")
                action.trigger()
                dialog = window.findChild(DocumentStructureWindow)
                self.assertIsNotNone(dialog)
                self.assertTrue(dialog.isVisible())
                tree = dialog.tree
                self.assertEqual(tree.topLevelItemCount(), 5)
                plans = tree.topLevelItem(1)
                self.assertEqual(plans.childCount(), 17)
                self.assertTrue(plans.child(0).isDisabled())
                memory = tree.topLevelItem(0).child(0)
                memory.setCheckState(0, Qt.CheckState.Checked)
                self.assertEqual(memory.child(0).checkState(0), Qt.CheckState.Checked)
                self.assertTrue(memory.child(8).isDisabled())
                self.assertEqual(memory.child(8).checkState(0), Qt.CheckState.Unchecked)
                self.assertEqual(tree.topLevelItem(0).checkState(0), Qt.CheckState.PartiallyChecked)
                self.assertFalse(dialog._copy_button.isEnabled())
                project = Path(directory) / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                dialog.refresh_generation()
                self.assertTrue(dialog._copy_button.isEnabled())
                destination = Path(directory) / "entrega"
                destination.mkdir()
                with (
                    patch.object(QFileDialog, "getExistingDirectory", side_effect=["", str(destination)]),
                    patch.object(QMessageBox, "information") as result,
                ):
                    dialog._copy_button.click()
                    self.assertEqual(list(destination.iterdir()), [])
                    dialog._copy_button.click()
                    result.assert_called_once()
                self.assertEqual(len(list(destination.rglob("*.odt"))), 8)
                self.assertFalse(dialog._copy_button.isEnabled())
                self.assertEqual([path.name for path in (destination / "01 Memoria y anejos").iterdir()],
                                 ["01 Memoria"])
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
