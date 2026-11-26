"""Pruebas del editor compartido de bibliotecas Markdown comprimidas."""
from pathlib import Path
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from tools._library_editor import library_members, read_markdown, write_markdown
from tools._library_editor_qt import LibraryEditorQt


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
        """Conecta la lista Qt, el editor de texto y la persistencia del ZIP."""
        self.application = QApplication.instance() or QApplication([])
        root = QWidget()
        self.addCleanup(root.close)
        member = "UNI/01_PRUEBAS/01AB_FICHA.md"
        editor = LibraryEditorQt(root, self.archive_path, "Biblioteca de prueba", used_members={member})
        self.addCleanup(editor.deleteLater)
        item = editor.members.findItems(member, Qt.MatchFlag.MatchExactly)[0]
        self.assertEqual(item.foreground().color(), Qt.GlobalColor.darkGreen)
        editor.members.setCurrentItem(item)
        self.assertEqual(editor.editor.toPlainText(), "# Ficha\n\nTexto.")
        editor.editor.setPlainText("# Editada")
        self.assertTrue(editor.dirty)
        with patch.object(QMessageBox, "information"):
            self.assertTrue(editor.save())
        self.assertEqual(
            read_markdown(self.archive_path, member), "# Editada",
        )
        with patch.object(QMessageBox, "critical") as error:
            editor.members.setCurrentItem(
                editor.members.findItems("catalogo.json", Qt.MatchFlag.MatchExactly)[0]
            )
        error.assert_called_once()
        self.assertEqual(editor.members.currentItem().text(), member)
        self.assertEqual(editor.selected_member, member)

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

    def test_pptp_uses_current_selected_paths_in_editor(self):
        """Include manual assignments but not excluded or missing catalog entries."""
        from tools.pptp.qt_window import PptpWindowQt

        used = "UNI/01_PRUEBAS/01AB_FICHA.md"
        window = SimpleNamespace(
            library_editor=None, reload_library=lambda: None, library_path=self.archive_path,
            automatic=[SimpleNamespace(path=used, selected=True)],
            unmatched=[
                SimpleNamespace(path="manual.md", selected=True),
                SimpleNamespace(path="excluded.md", selected=False),
                SimpleNamespace(path="missing.md", selected=True),
            ],
            sheets={used: None, "manual.md": None, "excluded.md": None},
        )
        with patch("tools.pptp.qt_window.LibraryEditorQt") as editor:
            PptpWindowQt.open_library_editor(window)
        self.assertEqual(editor.call_args.kwargs["used_members"], {used, "manual.md"})

    def test_safety_highlights_matching_units_and_materials_but_not_personnel(self):
        """Resolve preventive sheets by work code and resources by budget components."""
        from tools.safety_health.qt_window import _used_safety_members

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
        with (
            patch("tools.safety_health.qt_window.shared_library_path", return_value=self.archive_path),
            patch("tools.safety_health.qt_window.library_members", return_value=members),
        ):
            self.assertEqual(
                _used_safety_members(app), {members[0], members[2], members[3]},
            )
            app.budget_items = []
            self.assertEqual(_used_safety_members(app), set())


if __name__ == "__main__":
    unittest.main()
