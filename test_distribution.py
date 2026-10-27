"""Comprobaciones reproducibles sin presupuestos ni configuraciones privadas."""
import ast
import json
from pathlib import Path
import shutil
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from bc3_gui import Bc3Dataset, BudgetApp, find_bc3_files


ROOT = Path(__file__).resolve().parent
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
        self.assertTrue((ROOT / "iconos" / "report_header_logo.png").is_file())
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for name in ("openpyxl", "reportlab", "pillow", "matplotlib"):
            with self.subTest(package=name):
                self.assertIn(name, requirements)
        self.assertIn("-r requirements.txt", (ROOT / "requirements-test.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
