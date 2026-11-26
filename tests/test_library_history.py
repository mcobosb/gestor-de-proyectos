"""Revisiones internas de las fichas y documentos generales PPTP."""
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from tools._library_editor_qt import LibraryEditorQt
from tools._library_history import (
    INITIAL_AUTHOR, INITIAL_DATE, add_revision, initialize_history, read_revisions,
)
from tools._library_markdown import document_markdown
from tools.pptp.maintenance import initialize_library_history


class RevisionTests(unittest.TestCase):
    def test_study_libraries_ship_revision_histories_for_every_document(self):
        """Cada ficha y plantilla distribuida arranca con un historial editable."""
        root = Path(__file__).resolve().parents[1] / "tools"
        for module in ("safety_health", "rcd", "price_justification"):
            with self.subTest(module=module), ZipFile(root / module / "BIBLIOTECA.zip") as archive:
                documents = [name for name in archive.namelist() if name.lower().endswith(".md")]
                self.assertTrue(documents)
                for name in documents:
                    history, _ = read_revisions(archive.read(name).decode("utf-8-sig"))
                    self.assertTrue(history, name)

    def test_initial_record_preserves_all_original_text_and_is_idempotent(self):
        """Record the distribution provenance, not an invented author of previous edits."""
        for source in ("# Documento\n\nTexto.\n", '"""\nReferencia.\n"""\n\tTexto.\n'):
            with self.subTest(source=source):
                initialized = initialize_history(source)
                revisions, remainder = read_revisions(initialized)
                self.assertEqual(remainder, source)
                self.assertEqual(revisions[0].version, 1)
                self.assertEqual(revisions[0].author, INITIAL_AUTHOR)
                self.assertEqual(revisions[0].date, INITIAL_DATE)
                self.assertEqual(initialize_history(initialized), initialized)
                self.assertEqual(document_markdown(initialized), document_markdown(source))
                changed = add_revision(initialized, "Autor de prueba", "11/11/2026")
                history, remainder = read_revisions(changed)
                self.assertEqual([row.version for row in history], [2, 1])
                self.assertEqual(history[1:], revisions)
                self.assertEqual(history[0].description, "")
                self.assertEqual(remainder, source)
                self.assertEqual(document_markdown(changed), document_markdown(source))

    def test_invalid_revision_fields_and_history_are_explicit_errors(self):
        """Reject absent authors, impossible dates and fields that would break the record."""
        for author, date_value, description in (
            ("", "10/11/2026", ""), ("Autor", "", ""), ("Autor", "31/02/2026", ""),
            ("Autor", "1/1/2026", ""), ("Autor\nOtro", "10/11/2026", ""),
            ("Autor", "10/11/2026", '"""\nTexto'), ("Autor", "10/11/2026", "Uno | Otro"),
        ):
            with self.subTest(author=author, date=date_value), self.assertRaises(ValueError):
                add_revision("# Documento", author, date_value, description)
        original = initialize_history("# Documento")
        for invalid in (
            original.replace("Versión: 1", "Versión: 5"),
            original.replace("Autor: ", "Sin autor: "),
            "Texto antes.\n" + original,
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                read_revisions(invalid)

    def test_archive_migration_covers_every_markdown_and_preserves_catalog_and_bodies(self):
        """Update hashes and include the auxiliary index without changing IDs or content."""
        from shutil import copyfile
        from tools.pptp import LIBRARY_PATH, DOCS_ROOT
        from tools.pptp.library import ZipLibrary

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "library.zip"
            copyfile(LIBRARY_PATH, target)
            with ZipFile(target) as source:
                originals = {name: source.read(name) for name in source.namelist()
                             if name.lower().endswith(".md")}
                catalog = json.loads(source.read("UNI/catalogo.json"))
            expected = sum(not read_revisions(content.decode("utf-8-sig"))[0]
                           for content in originals.values())
            self.assertEqual(initialize_library_history(target), expected)
            with ZipFile(target) as source:
                self.assertIsNone(source.testzip())
                updated_catalog = json.loads(source.read("UNI/catalogo.json"))
                for name, original in originals.items():
                    revised = source.read(name).decode("utf-8-sig")
                    self.assertTrue(read_revisions(revised)[0])
                    self.assertEqual(document_markdown(revised),
                                     document_markdown(original.decode("utf-8-sig")))
                for old, new in zip(catalog["fichas"], updated_catalog["fichas"]):
                    self.assertEqual(old["id"], new["id"])
                    self.assertEqual(old["archivo"], new["archivo"])
                    self.assertEqual(new["sha256"],
                                     sha256(source.read("UNI/" + new["archivo"])).hexdigest())
            ZipLibrary(target, DOCS_ROOT)
            original = target.read_bytes()
            self.assertEqual(initialize_library_history(target), 0)
            self.assertEqual(target.read_bytes(), original)

    def test_revision_history_never_reaches_pdf_or_odt(self):
        """Check rendered output, not merely the Markdown exclusion helper."""
        from xml.etree import ElementTree as ET
        from pypdf import PdfReader
        from budget_reports import ReportSettings
        from tools.pptp.document import write_document

        source = add_revision(
            initialize_history("# Documento\n\nPrescripción contractual visible."),
            "Autor reservado de prueba", "10/11/2026", "Descripción interna reservada",
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("pdf", "odt"):
                with self.subTest(format=extension):
                    target = Path(directory) / ("PPTP." + extension)
                    write_document(source, target, {"titulo_proyecto": "Proyecto"},
                                   ReportSettings(output_format=extension))
                    if extension == "pdf":
                        rendered = "\n".join(page.extract_text() for page in PdfReader(target).pages)
                    else:
                        with ZipFile(target) as archive:
                            rendered = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                    self.assertIn("Prescripción contractual visible.", rendered)
                    for forbidden in ("Registro de cambios", INITIAL_AUTHOR, "Autor reservado de prueba",
                                      "Descripción interna reservada", "10/11/2026"):
                        self.assertNotIn(forbidden, rendered)


class RevisionEditorTests(unittest.TestCase):
    def setUp(self):
        """Open an isolated tracked editor with a base document and a reference block."""
        self.application = QApplication.instance() or QApplication([])
        self.root = QWidget()
        self.addCleanup(self.root.close)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = Path(self.temporary.name) / "library.zip"
        self.original = initialize_history('"""\nConsulta privada.\n"""\n# Documento\n\nTexto.\n')
        with ZipFile(self.archive, "w") as archive:
            archive.writestr("01_Base.md", self.original)
            archive.writestr("otra.md", "# Otro documento")
            archive.writestr("UNI/01_BASE/01T_PREVIOS/01TR_REDES/01TR-Original.md", "# Redes\n\nTexto.")
        self.editor = LibraryEditorQt(
            self.root, self.archive, "Prueba", used_members=set(), track_changes=True,
        )
        self.addCleanup(self.editor.deleteLater)

    def edit_white(self):
        """Edit the selected document."""
        self.editor.editor.appendPlainText("Prescripción añadida.")

    def test_author_and_date_required_and_revisions_are_recorded(self):
        """Valida la autoría y registra los cambios sin exportar información interna."""
        editor = self.editor
        self.assertEqual(editor.change_date.text(), date.today().strftime("%d/%m/%Y"))
        self.assertFalse(editor.dirty)
        before = self.archive.read_bytes()
        self.edit_white()
        with patch.object(QMessageBox, "critical") as error:
            self.assertFalse(editor.save())
        error.assert_called_once()
        self.assertEqual(self.archive.read_bytes(), before)
        editor.author.setText("Autora de prueba")
        editor.change_date.setText("31/02/2026")
        with patch.object(QMessageBox, "critical"):
            self.assertFalse(editor.save())
        editor.change_date.setText("10/11/2026")
        with patch.object(QMessageBox, "information"):
            self.assertTrue(editor.save())
        self.assertFalse(editor.dirty)
        with ZipFile(self.archive) as source:
            saved = source.read("01_Base.md").decode()
        revisions, _ = read_revisions(saved)
        self.assertEqual([row.version for row in revisions], [2, 1])
        self.assertEqual(revisions[0].author, "Autora de prueba")
        self.assertEqual(revisions[0].description, "")
        self.assertIn("Prescripción añadida.", document_markdown(saved))
        editor.editor.setPlainText(saved.replace("Consulta privada.", "Consulta privada ampliada."))
        editor.author.setText("Segundo autor")
        editor.description.setText("Amplía la referencia interna")
        with patch.object(QMessageBox, "information"):
            self.assertTrue(editor.save())
        with ZipFile(self.archive) as source:
            revised = source.read("01_Base.md").decode()
        revisions, _ = read_revisions(revised)
        self.assertEqual([row.version for row in revisions], [3, 2, 1])
        self.assertEqual(revisions[0].description, "Amplía la referencia interna")
        self.assertEqual(document_markdown(revised), document_markdown(saved))

    def test_failed_write_or_cancel_never_appends_history_or_loses_edits(self):
        """Conserva las ediciones pendientes al cancelar o fallar el guardado."""
        editor = self.editor
        self.edit_white()
        editor.author.setText("Autor")
        before = editor.editor.toPlainText()
        with patch("tools._library_editor_qt.write_markdown", side_effect=ValueError("Bloqueado")), \
                patch.object(QMessageBox, "critical"):
            self.assertFalse(editor.save())
        self.assertTrue(editor.dirty)
        self.assertEqual(editor.editor.toPlainText(), before)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            editor.members.setCurrentRow(1)
        self.assertEqual(editor.selected_member, "01_Base.md")
        self.assertEqual(editor.members.currentItem().text(), "01_Base.md")
        self.assertEqual(editor.editor.toPlainText(), before)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            editor.members.setCurrentRow(1)
        self.assertNotEqual(editor.selected_member, "01_Base.md")
        with ZipFile(self.archive) as source:
            self.assertEqual(source.read("01_Base.md").decode(), self.original)


if __name__ == "__main__":
    unittest.main()
