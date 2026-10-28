"""Comprobaciones reproducibles sin presupuestos ni configuraciones privadas."""
import ast
import json
import os
from pathlib import Path
import shutil
import tempfile
from time import monotonic
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from app_paths import user_data_directory
from bc3_gui import Bc3Dataset, BudgetApp, find_bc3_files, main, show_startup_screen


ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_BC3 = r"""~V|SYNTHETIC|FIEBDC-3/2016|Pruebas||
~C|OBRA##||Presupuesto de prueba|40.00||0|
~C|01#||Capitulo de prueba|40.00||0|
~C|P1|m|Partida de prueba|10.00||0|
~C|MO|h|Recurso de prueba|5.00||1|
~D|OBRA##|01#\1\1\|
~D|01#|P1\1\4\|
~D|P1|MO\1\2\|
~T|P1|Descripcion ficticia de la partida.|
~M|01#\P1|1\|4|\Tramo ficticio\2\2\\\|
"""


class DistributionTests(unittest.TestCase):
    """Verifica que el código distribuido no requiere los datos del puesto de trabajo."""

    def test_splash_displays_image_for_two_seconds_and_closes(self):
        """Mantiene la imagen visible dos segundos sin mostrar la ventana principal."""
        root = tk.Tk()
        root.withdraw()
        observed = []

        def observe_splash():
            """Registra el estado de la imagen durante la espera de arranque."""
            splash = next(child for child in root.winfo_children() if isinstance(child, tk.Toplevel))
            label = splash.winfo_children()[0]
            observed.append((
                splash.winfo_viewable(),
                splash.overrideredirect(),
                splash.winfo_width(),
                splash.winfo_height(),
                bool(label.cget("image")),
                root.state(),
            ))

        try:
            root.after(100, observe_splash)
            started = monotonic()
            show_startup_screen(root)
            elapsed = monotonic() - started
            self.assertGreaterEqual(elapsed, 2.0)
            self.assertEqual(observed, [(1, True, 612, 245, True, "withdrawn")])
            self.assertEqual(root.winfo_children(), [])
        finally:
            root.destroy()

    def test_main_shows_splash_before_building_application(self):
        """Construye la aplicación solo después de cerrar la imagen de arranque."""
        calls = Mock()
        with (
            patch("bc3_gui.tk.Tk", return_value=calls.root),
            patch("bc3_gui.show_startup_screen", calls.splash),
            patch("bc3_gui.BudgetApp", calls.application),
        ):
            main()
        self.assertEqual(
            [call[0] for call in calls.mock_calls],
            ["root.withdraw", "splash", "application", "root.deiconify", "root.mainloop"],
        )

    def test_missing_splash_image_warns_without_blocking_startup(self):
        """Notifica un recurso ausente y permite continuar al programa."""
        root = tk.Tk()
        root.withdraw()
        try:
            with (
                patch("bc3_gui.application_directory", return_value=Path("imagen_inexistente")),
                patch("bc3_gui.messagebox.showwarning") as warning,
            ):
                show_startup_screen(root)
            warning.assert_called_once()
            self.assertEqual(root.winfo_children(), [])
        finally:
            root.destroy()

    def test_startup_ignores_adjacent_data_until_explicit_project_selection(self):
        """Arranca vacío con BC3 e historial y carga solo al importar o elegir un reciente."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            shutil.copytree(ROOT / "iconos", folder / "iconos")
            budget = folder / "ejemplo.bc3"
            budget.write_text(SYNTHETIC_BC3, encoding="cp1252")
            (folder / "BCCA_ejemplo.bc3").write_text(SYNTHETIC_BC3, encoding="cp1252")
            project = folder / "ejemplo.json"
            project.write_text(json.dumps({
                "title": "Proyecto elegido",
                "source_budget_bc3": str(budget),
                "items": [{
                    "code": "P1", "chapter": "01#", "unit": "m",
                    "summary": "Partida de prueba", "measurement": 4, "unit_price": 10,
                }],
            }), encoding="utf-8")
            history = folder / "proyectos_recientes.json"
            history.write_text(json.dumps([str(project)]), encoding="utf-8")
            for selection in ("recent", "import"):
                with self.subTest(selection=selection):
                    original_files = {
                        path.relative_to(folder): path.read_bytes()
                        for path in folder.rglob("*") if path.is_file()
                    }
                    root = tk.Tk()
                    root.withdraw()
                    try:
                        with patch("bc3_gui.__file__", str(folder / "bc3_gui.py")):
                            app = BudgetApp(root)
                        self.assertEqual(app.budget_items, [])
                        self.assertEqual(app.reference_bases, [])
                        self.assertEqual(app.chapter_order, [])
                        self.assertEqual(app.planning_state, {})
                        self.assertIsNone(app.budget_dataset)
                        self.assertIsNone(app.project_path)
                        self.assertIsNone(app.source_budget_path)
                        self.assertIsNone(app.project_title)
                        self.assertFalse(app.is_dirty)
                        self.assertEqual(app.budget_tree.get_children(), ())
                        self.assertEqual(app.recent_projects, [project])
                        self.assertEqual(
                            {
                                path.relative_to(folder): path.read_bytes()
                                for path in folder.rglob("*") if path.is_file()
                            },
                            original_files,
                        )
                        if selection == "recent":
                            app.file_menu.invoke(1)
                        else:
                            with patch("bc3_gui.filedialog.askopenfilename", return_value=str(budget)):
                                app.load_budget_dialog()
                        self.assertEqual([item.code for item in app.budget_items], ["P1"])
                        self.assertEqual(app.project_path, project)
                        self.assertEqual(app.source_budget_path, budget)
                        self.assertIsNotNone(app.budget_dataset)
                        self.assertTrue(app.budget_tree.get_children())
                    finally:
                        root.destroy()

    def test_synthetic_bc3_loads_measurements_prices_and_resources(self):
        """Lee un BC3 ficticio y comprueba su presupuesto y descomposición."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prueba.bc3"
            path.write_text(SYNTHETIC_BC3, encoding="cp1252")
            dataset = Bc3Dataset(path)
            items = dataset.to_project_items()
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item.code, "P1")
            self.assertEqual(item.measurement, 4)
            self.assertEqual(item.unit_price, 10)
            self.assertEqual(item.import_total, 40)
            self.assertEqual(item.decomposition, [("MO", 2)])
            self.assertEqual(item.measurement_lines[0].partial, 4)
            self.assertEqual(item.text, "Descripcion ficticia de la partida.")

    def test_empty_installation_has_no_implicit_budget(self):
        """Permite arrancar una instalación limpia sin BC3 incluidos."""
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(find_bc3_files(Path(directory)), (None, None))

    def test_packaged_preferences_use_writable_user_data_directory(self):
        """Guarda preferencias del ejecutable en AppData y no en su carpeta instalada."""
        with tempfile.TemporaryDirectory() as directory:
            app_data = Path(directory) / "Roaming"
            executable = Path(directory) / "Program Files" / "GestorDeProyectos.exe"
            with (
                patch("app_paths.sys.argv", [str(executable)]),
                patch.dict(os.environ, {"APPDATA": str(app_data)}),
            ):
                self.assertEqual(
                    user_data_directory(str(executable)),
                    app_data / "Gestor de Proyectos",
                )
            self.assertTrue((app_data / "Gestor de Proyectos").is_dir())

    def test_every_function_has_a_nonempty_docstring(self):
        """Exige documentación integrada en las funciones y métodos distribuidos."""
        missing = []
        for path in ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not ast.get_docstring(node):
                    missing.append(f"{path.name}:{node.lineno} {node.name}")
        self.assertEqual(missing, [], "\n".join(missing))

    def test_runtime_assets_and_dependency_manifests_are_present(self):
        """Comprueba los recursos enlazados y las dependencias externas de la app."""
        self.assertTrue((ROOT / "iconos" / "logo.ico").is_file())
        self.assertTrue((ROOT / "iconos" / "Arranque.jpg").is_file())
        self.assertTrue((ROOT / "iconos" / "report_header_logo.png").is_file())
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for name in ("odfpy", "openpyxl", "reportlab", "pillow", "matplotlib"):
            with self.subTest(package=name):
                self.assertIn(name, requirements)
        self.assertIn("-r requirements.txt", (ROOT / "requirements-test.txt").read_text(encoding="utf-8"))
        build_requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8").lower()
        self.assertIn("nuitka", build_requirements)
        self.assertTrue((ROOT / "build_installer.ps1").is_file())
        self.assertTrue((ROOT / "installer" / "GestorDeProyectos.iss").is_file())


if __name__ == "__main__":
    unittest.main()
