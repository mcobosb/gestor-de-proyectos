"""Pruebas de la estructura documental y de sus plantillas ODT."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from budget_qt_window import BudgetMainWindow
from tools.document_structure.catalog import TEMPLATES, copy_templates, template_paths
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

    def test_menu_checklist_and_copy_without_open_project(self) -> None:
        """Abre la primera herramienta y copia un grupo sin incluir sus filas grises."""
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
                self.assertEqual([path.name for path in (destination / "01 Memoria y anejos").iterdir()],
                                 ["01 Memoria"])
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
