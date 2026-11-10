"""Pruebas de creación, renombrado y compatibilidad del catálogo PPTP."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from urllib.parse import quote
from zipfile import ZipFile

from bc3_gui import ProjectItem
from tools._library_archive import sheet_name, update_library
from tools._library_editor import LibraryEditor, _signature, read_markdown, write_markdown
from tools.pptp.document import VARIABLES, Selection, Sheet, compose, load_catalog, propose
from tools.pptp.library import ZipLibrary


class LibraryArchiveTests(unittest.TestCase):
    def setUp(self):
        """Create a cataloged family with a generic sheet and a separate folder."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.archive = self.folder / "library.zip"
        self.parent = "UNI/01_BASE/01R_REVESTIMIENTOS/01RP_PELDANOS"
        self.old = self.parent + "/GENERICA.md"
        self.new = self.parent + "/01RP-Retirada general.md"
        self.specific = self.parent + "/01RPN-Piedra natural.md"
        self.catalog = {
            "formato": "biblioteca-fichas-uni", "version_esquema": 1,
            "total_fichas": 1, "indice": "INDICE_FICHAS.md",
            "fichas": [{
                "id": "stable-id", "codigo_uni": "01RP", "titulo": "Peldaños",
                "archivo": self.old.removeprefix("UNI/"),
                "capitulo": {"codigo": "01", "titulo": "Demoliciones"},
            }],
        }
        with ZipFile(self.archive, "w") as archive:
            archive.comment = b"keep"
            archive.writestr("UNI/catalogo.json", json.dumps(self.catalog))
            archive.writestr(
                "UNI/INDICE_FICHAS.md",
                "# Índice\n\n| 01RP | [Peldaños](" + self.old.removeprefix("UNI/") + ") | Revisar. |\n",
            )
            archive.writestr(self.old, "# Peldaños\n\nTexto original.")
            archive.writestr("UNI/enlaces.md", "[Ficha](" + self.old.removeprefix("UNI/") + "#seccion)")
            archive.writestr("otra/00-Base.md", "# Otra carpeta")
            archive.writestr("binary.dat", bytes(range(256)))
            for prefix in ("01", "02", "03", "04"):
                archive.writestr(prefix + "-Base.md", "# Sección " + prefix)

    def library(self):
        """Read the current archive through the production catalog validator."""
        return ZipLibrary(self.archive, self.folder / "docs")

    def test_rename_keeps_id_content_links_and_saved_decisions(self):
        """Resolve historical paths for both automatic and manual selections."""
        update_library(self.archive, _signature(self.archive), renames={self.old: self.new})
        library = self.library()
        row = library.entries[self.new]
        self.assertEqual(row.identifier, "stable-id")
        self.assertEqual(row.code, "01RP")
        self.assertEqual(row.title, "Retirada general")
        self.assertEqual(library.read_sheet(self.old), "# Peldaños\n\nTexto original.")
        with ZipFile(self.archive) as source:
            self.assertNotIn(self.old, source.namelist())
            self.assertEqual(source.comment, b"keep")
            self.assertEqual(source.read("binary.dat"), bytes(range(256)))
            self.assertIn(quote(self.new.removeprefix("UNI/")), source.read("UNI/enlaces.md").decode())
        saved = {
            "selections": [{
                "item": index, "code": code, "chapter": "01#", "summary": "Peldaño",
                "path": self.old, "selected": False, "automatic": index == 0,
            } for index, code in enumerate(("01RPN00001", "MANUAL"))],
        }
        auto, manual = propose([
            ProjectItem("01RPN00001", "01#", "m", "Peldaño"),
            ProjectItem("MANUAL", "01#", "m", "Otro"),
        ], {}, load_catalog(library), saved)
        self.assertEqual([row.path for row in auto + manual], [self.new, self.new])
        self.assertFalse(any(row.selected for row in auto + manual))
        newer = self.parent + "/01RP-Otro nombre.md"
        update_library(self.archive, _signature(self.archive), renames={self.new: newer})
        self.assertEqual(self.library().resolve_sheet(self.old), newer)
        self.assertEqual(self.library().resolve_sheet(self.new), newer)
        state = {
            "variables": {key: "Valor" for key in VARIABLES},
            "selections": [
                asdict(Selection(0, "01RPN00001", "01#", "Peldaño", path))
                for path in (self.old, self.new, newer)
            ],
            "regulations": {},
        }
        source = compose(self.library(), state, [])
        self.assertEqual(source.count("Texto original."), 1)

    def test_content_update_and_rename_keep_hashes_and_links_consistent(self):
        """Update source content and its destination together without losing references."""
        content = "# Título revisado\n\n[Enlace](" + quote(Path(self.old).name) + ")"
        update_library(
            self.archive, _signature(self.archive), renames={self.old: self.new},
            updates={self.old: content},
        )
        with ZipFile(self.archive) as source:
            body = source.read(self.new)
            catalog = json.loads(source.read("UNI/catalogo.json"))
        self.assertIn(b"# T", body)
        self.assertIn(quote(Path(self.new).name), body.decode())
        self.assertEqual(catalog["fichas"][0]["sha256"], sha256(body).hexdigest())

    def test_new_matches_keep_manual_assignments_and_unchecked_decisions(self):
        """Adding specific matches must not discard a previously reviewed manual sheet."""
        update_library(
            self.archive, _signature(self.archive), additions={self.specific: "# Piedra natural\n"},
        )
        catalog = load_catalog(self.library())
        other = Sheet("02AA", "Otra ficha", "otra/00-Base.md")
        previous = [
            asdict(Selection(0, "01RPN00001", "01#", "Anterior", path,
                             selected=False, automatic=False))
            for path in (self.old, other.path)
        ]
        automatic, manual = propose(
            [ProjectItem("01RPN00001", "01#", "m", "Peldaños")], {}, [*catalog, other],
            {"selections": previous},
        )
        self.assertEqual([row.path for row in automatic], [self.specific])
        self.assertEqual([row.path for row in manual], [self.old, other.path])
        self.assertFalse(any(row.selected or row.automatic for row in manual))
        self.assertTrue(all(row.summary == "Peldaños" for row in manual))

    def test_saving_content_updates_hash_without_rewriting_manual_index_edits(self):
        """Editing a sheet updates its catalog checksum but preserves the user's index."""
        content = "# Ficha editada\n\nTexto revisado."
        write_markdown(self.archive, self.old, content, _signature(self.archive))
        with ZipFile(self.archive) as source:
            row = json.loads(source.read("UNI/catalogo.json"))["fichas"][0]
            self.assertEqual(row["sha256"], sha256(content.encode()).hexdigest())
        index = "# Índice revisado manualmente\n"
        write_markdown(self.archive, "UNI/INDICE_FICHAS.md", index, _signature(self.archive))
        self.assertEqual(read_markdown(self.archive, "UNI/INDICE_FICHAS.md"), index)
        self.assertEqual(self.library().read_sheet(self.old), content)

    def test_new_specific_sheet_is_cataloged_without_matching_other_materials(self):
        """Use the sheet's BCCA code rather than the containing family for matching."""
        update_library(
            self.archive, _signature(self.archive), additions={self.specific: "# Piedra natural\n"},
        )
        library = self.library()
        self.assertEqual(len(library.entries), 2)
        self.assertEqual(library.entries[self.specific].code, "01RPN")
        for code, expected in (("01RPN00001", {self.old, self.specific}), ("01RPC00001", {self.old})):
            auto, _ = propose([ProjectItem(code, "01#", "m", "Peldaños")], {}, load_catalog(library), {})
            self.assertEqual({row.path for row in auto}, expected)
        with ZipFile(self.archive) as source:
            self.assertIn("01RPN", source.read("UNI/INDICE_FICHAS.md").decode())

    def test_errors_leave_archive_unchanged(self):
        """Reject collisions, invalid names, stale writes and incompatible classification."""
        original = self.archive.read_bytes()
        signature = _signature(self.archive)
        for kwargs in (
            {"renames": {self.old: "otra/01RP-Movida.md"}},
            {"renames": {self.old: self.parent + "/02AA-Incorrecta.md"}},
            {"additions": {self.old: "overwrite"}},
            {"additions": {self.parent + "/01RP-otra.md/../01RP-Fuera.md": "invalid"}},
            {"additions": {"UNI/01RP-No terminal.md": "invalid"}},
            {"additions": {self.parent + "/01RP-Base?.md": "invalid"}},
            {"updates": {"missing.md": "invalid"}},
            {"updates": {"binary.dat": "invalid"}},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                update_library(
                    self.archive, signature, renames=kwargs.get("renames"),
                    additions=kwargs.get("additions"), updates=kwargs.get("updates"),
                )
            self.assertEqual(self.archive.read_bytes(), original)
        with self.assertRaises(ValueError):
            update_library(self.archive, (0, 0), renames={self.old: self.new})
        with patch("tools._library_archive.os.replace", side_effect=OSError("locked")):
            with self.assertRaisesRegex(ValueError, "locked"):
                update_library(self.archive, signature, renames={self.old: self.new})
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(list(self.folder.glob("*.tmp")), [])
        self.assertEqual(sheet_name("01rpn-Piedra natural"), ("01RPN-Piedra natural.md", "01RPN", "Piedra natural"))

    def test_editor_creates_renames_and_preserves_pending_changes(self):
        """Wire buttons, cancellation, pending edits, callback and usage highlights."""
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        callbacks = []
        editor = LibraryEditor(
            root, self.archive, "Biblioteca", used_members={self.old},
            on_saved=lambda: callbacks.append(True),
        )
        editor.tree.selection_set("dir:" + self.parent)
        editor._select()
        self.assertEqual(str(editor.new_button["state"]), "normal")
        with patch("tools._library_editor.simpledialog.askstring", return_value="01RPN-Piedra natural"):
            editor.new_button.invoke()
        self.assertEqual(editor.selected_member, self.specific)
        self.assertEqual(len(callbacks), 1)
        editor.text.insert("end", "Texto añadido")
        editor._edited()
        with (
            patch("tools._library_editor.simpledialog.askstring", return_value="01RPN-Nuevo nombre"),
            patch("tools._library_editor.messagebox.askyesnocancel", return_value=None),
        ):
            editor.rename_sheet()
        self.assertEqual(editor.selected_member, self.specific)
        self.assertTrue(editor.dirty)
        with (
            patch("tools._library_editor.simpledialog.askstring", return_value="01RPN-Nuevo nombre"),
            patch("tools._library_editor.messagebox.askyesnocancel", return_value=True),
        ):
            editor.rename_sheet()
        new = self.parent + "/01RPN-Nuevo nombre.md"
        self.assertEqual(editor.selected_member, new)
        self.assertIn("Texto añadido", read_markdown(self.archive, new))
        self.assertEqual(self.library().resolve_sheet(self.specific), new)
        editor.tree.selection_set("dir:UNI")
        editor._select()
        self.assertEqual(str(editor.new_button["state"]), "disabled")
        self.assertEqual(str(editor.rename_button["state"]), "disabled")


if __name__ == "__main__":
    unittest.main()
