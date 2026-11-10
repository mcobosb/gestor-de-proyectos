"""Pruebas del editor compartido de bibliotecas Markdown comprimidas."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from tools._library_editor import LibraryEditor, library_members, read_markdown, write_markdown


class LibraryEditorTests(unittest.TestCase):
    """Comprueba edición segura y estructura de ZIP sin tocar bibliotecas reales."""

    def setUp(self):
        """Crea una biblioteca temporal con fichas y archivos auxiliares."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive_path = Path(self.temporary.name) / "BIBLIOTECA.zip"
        with ZipFile(self.archive_path, "w", compression=ZIP_DEFLATED) as archive:
            archive.comment = b"comentario de prueba"
            archive.writestr("00_base.md", "# Documento base")
            archive.writestr("UNI/01_PRUEBAS/01AB_FICHA.md", "# Ficha\n\nTexto.")
            archive.writestr("UNI/02_GUIA.txt", "Guía")
            archive.writestr("catalogo.json", '{"fichas": []}')

    def signature(self):
        """Devuelve la firma esperada por el guardado optimista."""
        info = self.archive_path.stat()
        return info.st_size, info.st_mtime_ns

    def test_lists_and_reads_archive_files_without_extracting_them(self):
        """Incluye archivos de apoyo en el árbol y abre Markdown como UTF-8."""
        self.assertEqual(library_members(self.archive_path), [
            "00_base.md", "catalogo.json", "UNI/01_PRUEBAS/01AB_FICHA.md", "UNI/02_GUIA.txt",
        ])
        self.assertEqual(read_markdown(
            self.archive_path, "UNI/01_PRUEBAS/01AB_FICHA.md",
        ), "# Ficha\n\nTexto.")
        self.assertFalse((Path(self.temporary.name) / "UNI").exists())

    def test_atomic_markdown_save_preserves_other_members_and_zip_comment(self):
        """Reemplaza solo la ficha conservando catálogo, guías y comentario ZIP."""
        signature = write_markdown(
            self.archive_path, "UNI/01_PRUEBAS/01AB_FICHA.md",
            "# Ficha revisada\n\nTexto técnico.",
            self.signature(),
        )
        self.assertNotEqual(signature, (0, 0))
        self.assertEqual(read_markdown(
            self.archive_path, "UNI/01_PRUEBAS/01AB_FICHA.md",
        ), "# Ficha revisada\n\nTexto técnico.")
        with ZipFile(self.archive_path) as archive:
            self.assertEqual(archive.read("UNI/02_GUIA.txt"), "Guía".encode())
            self.assertEqual(archive.read("catalogo.json"), b'{"fichas": []}')
            self.assertEqual(archive.comment, b"comentario de prueba")

    def test_rejects_stale_signatures_non_markdown_and_unsafe_members(self):
        """Evita sobrescribir cambios externos o rutas que no son fichas Markdown."""
        current = self.signature()
        with self.assertRaisesRegex(ValueError, "cambiado"):
            write_markdown(
                self.archive_path, "UNI/01_PRUEBAS/01AB_FICHA.md", "cambio",
                (current[0] + 1, current[1]),
            )
        with self.assertRaises(ValueError):
            write_markdown(self.archive_path, "catalogo.json", "{}", current)
        with self.assertRaises(ValueError):
            read_markdown(self.archive_path, "../fuera.md")

    def test_editor_loads_selected_markdown_and_saves_changes(self):
        """Conecta el árbol, el editor de texto y la persistencia del ZIP."""
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk no disponible: {exc}")
        root.withdraw()
        self.addCleanup(root.destroy)
        editor = LibraryEditor(root, self.archive_path, "Biblioteca de prueba")
        self.addCleanup(editor.destroy)
        self.assertEqual(
            editor.tree.get_children(),
            ("file:00_base.md", "dir:UNI", "file:catalogo.json"),
        )
        editor.tree.selection_set("file:UNI/01_PRUEBAS/01AB_FICHA.md")
        editor._select()
        self.assertEqual(editor.text.get("1.0", "end-1c"), "# Ficha\n\nTexto.")
        editor.text.delete("1.0", "end")
        editor.text.insert("1.0", "# Editada")
        editor._edited()
        self.assertTrue(editor.dirty)
        self.assertTrue(editor.save())
        self.assertEqual(
            read_markdown(self.archive_path, "UNI/01_PRUEBAS/01AB_FICHA.md"), "# Editada",
        )

    def test_rejects_duplicate_and_unsafe_archive_paths(self):
        """No muestra ni reescribe bibliotecas con rutas ambiguas."""
        with ZipFile(self.archive_path, "a") as archive:
            archive.writestr("../fuera.md", "inseguro")
        with self.assertRaises(ValueError):
            library_members(self.archive_path)

        with ZipFile(self.archive_path, "w") as archive:
            archive.writestr("duplicado.md", "primero")
            archive.writestr("duplicado.md", "segundo")
        with self.assertRaisesRegex(ValueError, "duplicada"):
            library_members(self.archive_path)

    def test_used_sheets_highlight_all_ancestors_and_refresh_without_editing(self):
        """Color only used Markdown paths and clear outdated marks on refresh."""
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        member = "UNI/01_PRUEBAS/01AB_FICHA.md"
        editor = LibraryEditor(root, self.archive_path, "Prueba", used_members={member})
        for node in ("dir:UNI", "dir:UNI/01_PRUEBAS", f"file:{member}"):
            self.assertEqual(editor.tree.item(node, "tags"), ("used",))
        self.assertFalse(editor.tree.item("file:UNI/02_GUIA.txt", "tags"))
        self.assertFalse(editor.tree.item("file:00_base.md", "tags"))
        editor.tree.selection_set(f"file:{member}")
        editor._select()
        editor.text.insert("end", "\nEdición pendiente")
        editor._edited()
        editor.highlight_used({"00_base.md", "no-existe.md", "UNI/02_GUIA.txt"})
        self.assertTrue(editor.dirty)
        self.assertEqual(editor.selected_member, member)
        self.assertIn("Edición pendiente", editor.text.get("1.0", "end-1c"))
        self.assertFalse(editor.tree.item("dir:UNI", "tags"))
        self.assertEqual(editor.tree.item("file:00_base.md", "tags"), ("used",))
        self.assertFalse(editor.tree.item("file:UNI/02_GUIA.txt", "tags"))

    def test_pptp_uses_current_selected_paths_and_refreshes_open_editor(self):
        """Include manual assignments but not excluded or missing catalog entries."""
        from tools.pptp import PptpWindow

        used = "UNI/01_PRUEBAS/01AB_FICHA.md"
        window = SimpleNamespace(
            library_editor=None, reload_library=lambda: None,
            automatic=[SimpleNamespace(path=used, selected=True)],
            unmatched=[
                SimpleNamespace(path="manual.md", selected=True),
                SimpleNamespace(path="excluded.md", selected=False),
                SimpleNamespace(path="missing.md", selected=True),
            ],
            sheets={used: None, "manual.md": None, "excluded.md": None},
        )
        with patch("tools._library_editor.LibraryEditor") as editor:
            PptpWindow.open_library_editor(window)
        self.assertEqual(editor.call_args.kwargs["used_members"], {used, "manual.md"})
        window.automatic[0].selected = False
        PptpWindow.open_library_editor(window)
        editor.return_value.highlight_used.assert_called_once_with({"manual.md"})

    def test_safety_highlights_matching_units_and_materials_but_not_personnel(self):
        """Resolve preventive sheets by work code and resources by budget components."""
        from tools.safety_health import SafetyHealthWindow

        members = [
            "UNI/grupo/FICHA_ESS_01UPP.md", "UNI/otro/FICHA_ESS_15CP.md",
            "RECURSOS/MATERIALES/MAT.md", "RECURSOS/MAQUINARIA/MAQ.md",
            "RECURSOS/PERSONAL/MO.md",
        ]
        app = SimpleNamespace(
            budget_items=[SimpleNamespace(code="01UPP00001")],
            _report_components=lambda item: [
                (SimpleNamespace(code=code, ctype=kind), 1, 10)
                for code, kind in (("MAT", "3"), ("MAQ", "2"), ("MO", "1"))
            ],
        )
        window = SimpleNamespace(app=app, library_editor=None)
        with (
            patch("tools._library_editor.library_members", return_value=members),
            patch("tools._library_editor.LibraryEditor") as editor,
        ):
            SafetyHealthWindow.open_library_editor(window)
            self.assertEqual(
                editor.call_args.kwargs["used_members"], {members[0], members[2], members[3]},
            )
            app.budget_items = []
            SafetyHealthWindow.open_library_editor(window)
        editor.return_value.highlight_used.assert_called_once_with(set())


if __name__ == "__main__":
    unittest.main()
