"""Comprobaciones reproducibles sin presupuestos ni configuraciones privadas."""
import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app_paths import user_data_directory
from bc3_gui import Bc3Dataset, find_bc3_files, main
from budget_qt_window import BudgetMainWindow


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

    @classmethod
    def setUpClass(cls) -> None:
        """Crea la aplicación PySide necesaria para verificar el arranque actual."""
        cls.application = QApplication.instance() or QApplication([])

    def test_budget_fixture_contains_only_synthetic_records(self):
        """Impide incorporar campos libres o metadatos privados en el BC3 de prueba."""
        path = ROOT / "tests" / "test.bc3"
        records = path.read_text(encoding="ascii").splitlines()
        self.assertEqual(
            records[0], "~V|SYNTHETIC TEST FIXTURE|FIEBDC-3/2016|DATOS FICTICIOS||",
        )
        dataset = Bc3Dataset(path)
        code_pattern = r"(?:OBRA\d{4}##|CAP\.\d{2}#|P\d{4}|R\d{4}|%R\d{4})"
        self.assertEqual(len(dataset.concepts), 218)
        self.assertEqual(sum(len(c.children) for c in dataset.concepts.values()), 546)
        for record in records[1:]:
            fields = record.split("|")
            kind = fields[0]
            with self.subTest(kind=kind, code=fields[1]):
                self.assertIn(kind, {"~C", "~T", "~D", "~M"})
                self.assertEqual(fields[-1], "")
                if kind != "~M":
                    self.assertRegex(fields[1], f"^{code_pattern}$")
                    self.assertIn(fields[1], dataset.concepts)
                if kind == "~C":
                    self.assertEqual(len(fields), 8)
                    self.assertIn(fields[2], {"", "ud", "h", "%"})
                    self.assertEqual(fields[3], f"CONCEPTO SINTETICO {fields[1]}")
                    self.assertRegex(fields[4], r"^\d+\.\d{2}$")
                    self.assertEqual(fields[5], "")
                    self.assertIn(fields[6], {"0", "1", "2", "3"})
                elif kind == "~T":
                    self.assertEqual(len(fields), 4)
                    self.assertEqual(
                        fields[2], f"DESCRIPCION SINTETICA DEL CONCEPTO {fields[1]}.",
                    )
                elif kind == "~D":
                    self.assertEqual(len(fields), 4)
                    self.assertRegex(
                        fields[2], rf"^(?:{code_pattern}\\1\\\d+(?:\.\d+)?\\)+$",
                    )
                    for child in fields[2].split("\\")[::3][:-1]:
                        self.assertIn(child, dataset.concepts)
                elif kind == "~M":
                    self.assertEqual(len(fields), 6)
                    parent, child = fields[1].split("\\")
                    self.assertIn(parent, dataset.concepts)
                    self.assertIn(child, dataset.concepts)
                    self.assertEqual(fields[2], "1\\")
                    self.assertRegex(fields[3], r"^\d+$")
                    self.assertRegex(
                        fields[4], r"^(?:\\TRAMO SINTETICO \d+\\[123]\\[1-5]\\\\\\)+$",
                    )

    def test_main_delegates_to_pyside6_entrypoint(self):
        """La entrada pública usa PySide6 sin construir la ventana Tk heredada."""
        with patch("bc3_qt.main", return_value=17) as qt_main:
            result = main()
        qt_main.assert_called_once_with()
        self.assertEqual(result, 17)

    def test_budget_core_has_no_gui_framework_dependency(self):
        """El parser, los modelos y los cálculos no dependen de ningún toolkit GUI."""
        core_path = ROOT / "budget_core.py"
        tree = ast.parse(core_path.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue({"tkinter", "PySide6"}.isdisjoint(imports))

    def test_startup_does_not_load_adjacent_budget_or_recent_project(self):
        """Arranca vacío aunque haya un presupuesto y un proyecto reciente junto al ejecutable."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
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
            user_directory = folder / "user"
            user_directory.mkdir()
            history = user_directory / "proyectos_recientes.json"
            history.write_text(json.dumps([str(project)]), encoding="utf-8")
            with (
                patch("budget_qt_window.application_directory", return_value=folder),
                patch(
                    "budget_qt_window.user_data_directory",
                    return_value=user_directory,
                ),
            ):
                app = BudgetMainWindow()
            try:
                self.assertEqual(app.budget_items, [])
                self.assertEqual(app.reference_bases, [])
                self.assertEqual(app.chapter_order, [])
                self.assertEqual(app.planning_state, {})
                self.assertIsNone(app.budget_dataset)
                self.assertIsNone(app.project_path)
                self.assertIsNone(app.source_budget_path)
                self.assertIsNone(app.project_title)
                self.assertFalse(app.is_dirty)
                self.assertEqual(app.budget_tree.topLevelItemCount(), 0)
                self.assertEqual(app.recent_projects, [project])
            finally:
                app.is_dirty = False
                app.close()
                self.application.processEvents()

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
            self.assertEqual(item.text, "DESCRIPCION FICTICIA DE LA PARTIDA.")

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
        for path in [*ROOT.glob("*.py"), *(ROOT / "tools").rglob("*.py")]:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not ast.get_docstring(node):
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
        self.assertEqual(missing, [], "\n".join(missing))

    def test_runtime_assets_and_dependency_manifests_are_present(self):
        """Comprueba los recursos enlazados y las dependencias externas de la app."""
        self.assertTrue((ROOT / "iconos" / "logo.ico").is_file())
        self.assertTrue((ROOT / "iconos" / "Arranque.jpg").is_file())
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for name in (
            "odfpy", "openpyxl", "reportlab", "pillow", "matplotlib",
            "pyside6", "cryptography",
        ):
            with self.subTest(package=name):
                self.assertIn(name, requirements)
        self.assertIn("-r requirements.txt", (ROOT / "requirements-test.txt").read_text(encoding="utf-8"))
        build_requirements = (ROOT / "requirements-build.txt").read_text(encoding="utf-8").lower()
        self.assertIn("nuitka", build_requirements)
        self.assertTrue((ROOT / "build_installer.ps1").is_file())
        build_script = (ROOT / "build_installer.ps1").read_text(encoding="utf-8").lower()
        for module in (
            "matplotlib.backends.backend_agg",
            "matplotlib.backends.backend_pdf",
            "urllib.parse",
        ):
            with self.subTest(module=module):
                self.assertIn(f"--include-module={module}", build_script)
        self.assertIn('$env:mplbackend = "agg"', build_script)
        self.assertIn("--module", build_script)
        self.assertIn("--nofollow-imports", build_script)
        self.assertIn('"tools"', build_script)
        self.assertIn('copy-item', build_script)
        self.assertIn("discover_tools", build_script)
        self.assertNotIn("--enable-plugin=tk-inter", build_script)
        self.assertIn("--include-package=cryptography", build_script)
        self.assertNotIn("--include-data-dir=archivos=archivos", build_script)
        self.assertNotIn("--include-data-dir=datos=datos", build_script)
        self.assertIn("get-childitem $toolssource -recurse -file", build_script)
        self.assertIn("destination = join-path $installedtools $relativepath", build_script)
        self.assertTrue((ROOT / "tools" / "safety_health" / "datos" /
                         "seguridad_salud_relaciones_bcca2023.csv").is_file())
        safety_library = ROOT / "tools" / "safety_health" / "BIBLIOTECA.zip"
        for resource in ("CMUN.csv", "CPRO.csv", "cpv_2008_xls.zip"):
            self.assertTrue((ROOT / "tools" / resource).is_file())
            self.assertIn(f'"{resource.lower()}"', build_script)
        for resource in (
            r"safety_health\datos\ntps.csv", r"pptp\docs\unes.csv",
            r"safety_health\datos\vinculos_familia_bcca2023.csv",
            r"safety_health\datos\catalogo_ss_vinculado_bcca2023.csv",
            r"safety_health\datos\riesgos_familia_bcca2023.csv",
            r"safety_health\datos\NTP_tematica_por_codigo_BCCA.csv",
            r"document_structure\plantillas\maestro\plantilla.odt",
            r"document_structure\plantillas\maestro\logo.jpg",
        ):
            self.assertTrue((ROOT / "tools" / resource).is_file())
            self.assertIn(f'"{resource.lower()}"', build_script)
        self.assertTrue(safety_library.is_file())
        with ZipFile(safety_library) as archive:
            names = archive.namelist()
        self.assertNotIn("00_guia_integracion.md", names)
        for document in ("01_objeto_y_alcance.md", "02_memoria_general.md"):
            self.assertIn(document, names)
        for category in ("RECURSOS/MATERIALES/", "RECURSOS/MAQUINARIA/"):
            self.assertTrue(any(name.startswith(category) for name in names))
        self.assertTrue((ROOT / "tools" / "pptp" / "BIBLIOTECA.zip").is_file())
        from tools.pptp import DOCS_ROOT, LIBRARY_PATH
        from tools.pptp.library import ZipLibrary

        library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        self.assertTrue(library.entries)
        for prefix in ("01", "02", "03", "04"):
            self.assertTrue(library.read_documents(prefix))
        self.assertIn("--msvc=latest", build_script)
        for package in ("matplotlib", "openpyxl", "pdfplumber", "pypdfium2"):
            self.assertIn(f"--include-package={package}", build_script)
        self.assertTrue((ROOT / "installer" / "GestorDeProyectos.iss").is_file())


if __name__ == "__main__":
    unittest.main()
