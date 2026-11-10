"""Revisiones internas de las fichas y documentos generales PPTP."""
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools._library_editor import LibraryEditor
from tools._library_history import (
    INITIAL_AUTHOR, INITIAL_DATE, add_revision, initialize_history, read_revisions,
)
from tools._library_markdown import document_markdown
from tools.pptp.maintenance import initialize_library_history


class RevisionTests(unittest.TestCase):
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
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = Path(self.temporary.name) / "library.zip"
        self.original = initialize_history('"""\nConsulta privada.\n"""\n# Documento\n\nTexto.\n')
        with ZipFile(self.archive, "w") as archive:
            archive.writestr("01_Base.md", self.original)
            archive.writestr("otra.md", "# Otro documento")
            archive.writestr("UNI/01_BASE/01T_PREVIOS/01TR_REDES/01TR-Original.md", "# Redes\n\nTexto.")
        self.editor = LibraryEditor(self.root, self.archive, "Prueba", track_changes=True)
        self.addCleanup(self.editor.destroy)
        self.editor.tree.selection_set("file:01_Base.md")
        self.editor._select()
        self.root.update()

    def edit_white(self):
        """Make a white-pane edit and deliver the actual Tk modification event."""
        self.editor.text.insert("end", "Prescripción añadida.")
        self.root.update()

    def test_author_and_date_required_for_both_panes_and_versions_only_on_real_saves(self):
        """Save automatic versions, optional descriptions and both editors with no export leaks."""
        editor = self.editor
        self.assertEqual(editor.revision_version.get(), "1")
        self.assertEqual(editor.revision_last_author.get(), INITIAL_AUTHOR)
        self.assertEqual(editor.revision_date.get(), date.today().strftime("%d/%m/%Y"))
        self.assertFalse(editor.dirty)
        before = self.archive.read_bytes()
        self.edit_white()
        with patch("tools._library_editor.messagebox.showerror") as error:
            self.assertFalse(editor.save())
        error.assert_called_once()
        self.assertEqual(self.archive.read_bytes(), before)
        editor.revision_author.set("Autora de prueba")
        editor.revision_date.set("31/02/2026")
        with patch("tools._library_editor.messagebox.showerror"):
            self.assertFalse(editor.save())
        editor.revision_date.set("10/11/2026")
        self.assertTrue(editor.save())
        self.root.update()
        self.assertFalse(editor.dirty)
        self.assertEqual(editor.revision_version.get(), "2")
        self.assertEqual(editor.revision_author.get(), "")
        self.assertEqual(editor.revision_last_author.get(), "Autora de prueba")
        with ZipFile(self.archive) as source:
            saved = source.read("01_Base.md").decode()
        revisions, _ = read_revisions(saved)
        self.assertEqual([row.version for row in revisions], [2, 1])
        self.assertEqual(revisions[0].description, "")
        self.assertIn("Prescripción añadida.", document_markdown(saved))
        position = editor.reference_text.search("Consulta privada.", "1.0", "end")
        editor.reference_text.insert(position, "Nota nueva. ")
        self.root.update()
        with patch("tools._library_editor.messagebox.showerror"):
            self.assertFalse(editor.save())
        editor.revision_author.set("Segundo autor")
        editor.revision_description.set("Amplía la referencia interna")
        exported = document_markdown(saved)
        self.assertTrue(editor.save())
        self.root.update()
        with ZipFile(self.archive) as source:
            saved = source.read("01_Base.md").decode()
        revisions, _ = read_revisions(saved)
        self.assertEqual([row.version for row in revisions], [3, 2, 1])
        self.assertEqual(revisions[0].description, "Amplía la referencia interna")
        self.assertEqual(document_markdown(saved), exported)
        before = self.archive.read_bytes()
        editor.text.edit_separator()
        self.edit_white()
        editor.text.edit_undo()
        self.root.update()
        self.assertTrue(editor.save())
        self.assertEqual(self.archive.read_bytes(), before)
        self.assertEqual(editor.revision_version.get(), "3")
        self.assertTrue(editor.save())
        self.assertEqual(self.archive.read_bytes(), before)

    def test_failed_write_or_cancel_never_appends_history_or_loses_edits(self):
        """Keep pending edits on cancellation, I/O failure and historical record tampering."""
        editor = self.editor
        self.edit_white()
        editor.revision_author.set("Autor")
        before = editor._sheet_source()
        with patch("tools._library_editor.write_markdown", side_effect=ValueError("Bloqueado")), \
                patch("tools._library_editor.messagebox.showerror"):
            self.assertFalse(editor.save())
        self.assertTrue(editor.dirty)
        self.assertEqual(editor._sheet_source(), before)
        self.assertEqual(editor.revision_version.get(), "1")
        with patch("tools._library_editor.messagebox.askyesnocancel", return_value=None):
            editor.tree.selection_set("file:otra.md")
            editor._select()
        self.assertEqual(editor.selected_member, "01_Base.md")
        position = editor.reference_text.search(INITIAL_AUTHOR, "1.0", "end")
        editor.reference_text.insert(position, "Manipulado ")
        self.root.update()
        with patch("tools._library_editor.messagebox.showerror"):
            self.assertFalse(editor.save())
        with ZipFile(self.archive) as source:
            self.assertEqual(source.read("01_Base.md").decode(), self.original)

    def test_creation_and_rename_require_attribution_and_keep_it_after_pending_save(self):
        """Retain the supplied author when saving pending edits before create or rename."""
        editor = self.editor
        member = "UNI/01_BASE/01T_PREVIOS/01TR_REDES/01TR-Original.md"
        editor.tree.selection_set("file:" + member)
        editor._select()
        self.root.update()
        before = self.archive.read_bytes()
        with patch("tools._library_editor.simpledialog.askstring", return_value="01TR-Nueva.md"), \
                patch("tools._library_editor.messagebox.showerror") as error:
            editor.new_sheet()
        error.assert_called_once()
        self.assertEqual(self.archive.read_bytes(), before)
        self.edit_white()
        editor.revision_author.set("Autora de creación")
        with patch("tools._library_editor.simpledialog.askstring", return_value="01TR-Nueva.md"), \
                patch("tools._library_editor.messagebox.askyesnocancel", return_value=True):
            editor.new_sheet()
        new_member = editor.selected_member
        self.assertTrue(new_member.endswith("/01TR-Nueva.md"))
        with ZipFile(self.archive) as archive:
            history, _ = read_revisions(archive.read(new_member).decode())
            self.assertEqual([row.version for row in history], [1])
            self.assertEqual(history[0].author, "Autora de creación")
            self.assertIn("Prescripción añadida.", archive.read(member).decode())
        self.edit_white()
        editor.revision_author.set("Autora de renombrado")
        with patch("tools._library_editor.simpledialog.askstring", return_value="01TR-Renombrada.md"), \
                patch("tools._library_editor.messagebox.askyesnocancel", return_value=True):
            editor.rename_sheet()
        self.assertTrue(editor.selected_member.endswith("/01TR-Renombrada.md"))
        with ZipFile(self.archive) as archive:
            history, remainder = read_revisions(archive.read(editor.selected_member).decode())
            self.assertEqual([row.version for row in history], [3, 2, 1])
            self.assertTrue(all(row.author == "Autora de renombrado" for row in history[:2]))
            self.assertIn("Prescripción añadida.", remainder)


if __name__ == "__main__":
    unittest.main()
