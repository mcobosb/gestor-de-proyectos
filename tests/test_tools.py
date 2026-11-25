"""Pruebas de descubrimiento, menu dinamico y proyectos sin herramientas."""
import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QMenu

from budget_qt_window import BudgetMainWindow
from tool_loader import discover_tools


ROOT = Path(__file__).resolve().parent.parent
PLUGIN = '''TOOL_LABEL = "Extra"
TOOL_ORDER = 1
def open_tool_qt(app):
    """Registra la ejecucion del complemento."""
    app.extra_opened = True
'''


class ToolTestCase(unittest.TestCase):
    """Aisla carpetas y paquetes de las herramientas para cada prueba."""

    def setUp(self):
        """Prepara una carpeta temporal y preserva los modulos de otras pruebas."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.modules = {
            name: module for name, module in sys.modules.items()
            if name == "tools" or name.startswith("tools.")
        }
        self.package_path = list(getattr(sys.modules.get("tools"), "__path__", []))
        self.addCleanup(self.restore_modules)

    def restore_modules(self):
        """Restablece el espacio de nombres usado por las herramientas oficiales."""
        for name in list(sys.modules):
            if name == "tools" or name.startswith("tools."):
                del sys.modules[name]
        sys.modules.update(self.modules)
        package = sys.modules.get("tools")
        if package is not None:
            package.__path__ = self.package_path
        importlib.invalidate_caches()

    def write_plugin(self, name, source=PLUGIN):
        """Crea un complemento ficticio con un contrato conocido."""
        (self.directory / f"{name}.py").write_text(source, encoding="utf-8")


class ToolDiscoveryTests(ToolTestCase):
    """Comprueba contratos y aislamiento respecto al directorio de trabajo."""

    def test_missing_or_empty_directory_has_no_tools(self):
        """Permite arrancar aunque no exista la carpeta o no contenga modulos."""
        self.assertEqual(discover_tools(self.directory / "missing"), ([], []))
        self.assertEqual(discover_tools(self.directory), ([], []))
        self.assertEqual(discover_tools(ROOT / "README.md")[0], [])
        self.assertTrue(discover_tools(ROOT / "README.md")[1])

    def test_missing_directory_does_not_require_tools_package(self):
        """El nucleo no depende de que exista un paquete tools importable."""
        for name in list(sys.modules):
            if name == "tools" or name.startswith("tools."):
                del sys.modules[name]
        self.assertEqual(discover_tools(self.directory / "missing"), ([], []))
        self.write_plugin("extra")
        loaded, errors = discover_tools(self.directory)
        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in loaded], ["extra"])

    def test_unreadable_directory_is_reported(self):
        """Los problemas de acceso no se presentan como una carpeta vacia."""
        with patch.object(Path, "stat", side_effect=PermissionError("denied")):
            loaded, errors = discover_tools(self.directory)
        self.assertEqual(loaded, [])
        self.assertIn("denied", errors[0])
        with patch.object(Path, "iterdir", side_effect=PermissionError("unreadable")):
            loaded, errors = discover_tools(self.directory)
        self.assertEqual(loaded, [])
        self.assertIn("unreadable", errors[0])

    def test_discovers_new_module_without_a_builtin_list(self):
        """Encuentra un archivo nuevo y ejecuta su funcion de entrada."""
        self.write_plugin("extra")
        loaded, errors = discover_tools(self.directory)
        self.assertEqual(errors, [])
        self.assertEqual([(tool.name, tool.label) for tool in loaded], [("extra", "Extra")])
        app = SimpleNamespace()
        loaded[0].open(app)
        self.assertTrue(app.extra_opened)

    def test_ignores_helpers_and_non_modules_and_reports_broken_plugins(self):
        """Un complemento invalido no oculta los validos ni se importa como ayuda."""
        self.write_plugin("extra")
        self.write_plugin("_helper", 'raise RuntimeError("No debe importarse")')
        self.write_plugin("broken", 'raise ImportError("Dependencia ausente")')
        self.write_plugin("invalid", 'TOOL_LABEL = "Sin entrada"')
        self.write_plugin("syntax", "def bad(:")
        self.write_plugin("bad_hook", PLUGIN + "\nis_available = 7\n")
        self.write_plugin("bad_order", PLUGIN.replace("TOOL_ORDER = 1", "TOOL_ORDER = True"))
        self.write_plugin("exits", 'raise SystemExit("No debe cerrar la aplicacion")')
        (self.directory / "readme.txt").write_text("Notas", encoding="ascii")
        loaded, errors = discover_tools(self.directory)
        self.assertEqual([tool.name for tool in loaded], ["extra"])
        self.assertEqual(len(errors), 6)
        self.assertTrue(any("Dependencia ausente" in error for error in errors))
        self.assertNotIn("tools._helper", sys.modules)

    def test_discovers_packages_and_orders_by_metadata(self):
        """Acepta paquetes de herramientas y ordena por prioridad, no por archivo."""
        self.write_plugin("z_first")
        self.write_plugin("a_last", PLUGIN.replace("TOOL_ORDER = 1", "TOOL_ORDER = 90"))
        package = self.directory / "package_tool"
        package.mkdir()
        (package / "__init__.py").write_text(
            PLUGIN.replace("TOOL_ORDER = 1", "TOOL_ORDER = 50"), encoding="utf-8",
        )
        loaded, errors = discover_tools(self.directory)
        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in loaded], ["z_first", "package_tool", "a_last"])

    def test_builtin_modules_are_moved_and_keep_their_order(self):
        """Justificación encabeza las herramientas y PPTP sigue a Planificación."""
        loaded, errors = discover_tools(ROOT / "tools")
        self.assertEqual(errors, [])
        self.assertEqual(
            [tool.name for tool in loaded],
            [
                "document_structure", "quality_assurance", "price_justification",
                "rcd", "safety_health", "planning", "pptp", "certifications",
                "invoicing", "bim",
            ],
        )
        labels = {tool.name: tool.label for tool in loaded}
        self.assertEqual(labels["pptp"], "PPTP (BCCA · Junta de Andalucía)")
        self.assertEqual(
            labels["safety_health"], "Seguridad y salud (BCCA · Junta de Andalucía)",
        )
        self.assertNotIn("BCCA", labels["rcd"])
        for tool in loaded:
            self.assertFalse((ROOT / f"{tool.name}.py").exists())
            self.assertFalse((ROOT / "tools" / f"{tool.name}.py").exists())
            self.assertTrue((ROOT / "tools" / tool.name / "__init__.py").is_file())

    def test_package_loads_internal_scripts_and_resources(self):
        """Solo la entrada del paquete aparece en el menu, no sus auxiliares."""
        package = self.directory / "packaged"
        package.mkdir()
        (package / "__init__.py").write_text(
            PLUGIN + "\nfrom .helper import read_resource\n", encoding="utf-8",
        )
        (package / "helper.py").write_text(
            'from pathlib import Path\n'
            'def read_resource():\n'
            '    """Lee un recurso vecino al script."""\n'
            '    return Path(__file__).with_name("value.txt").read_text(encoding="ascii")\n',
            encoding="utf-8",
        )
        (package / "value.txt").write_text("contenido", encoding="ascii")
        loaded, errors = discover_tools(self.directory)
        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in loaded], ["packaged"])
        self.assertEqual(sys.modules["tools.packaged"].read_resource(), "contenido")

    def test_package_cache_does_not_reuse_helpers_from_another_directory(self):
        """Cambiar la carpeta de tools sustituye tambien sus submodulos."""
        for folder, label in ((self.directory / "first", "Uno"), (self.directory / "second", "Dos")):
            package = folder / "extra"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(
                'from .helper import TOOL_LABEL\n'
                'def open_tool(app):\n    """Entrada del paquete."""\n    pass\n',
                encoding="utf-8",
            )
            (package / "helper.py").write_text(f'TOOL_LABEL = "{label}"', encoding="utf-8")
            loaded, errors = discover_tools(folder)
            self.assertEqual(errors, [])
            self.assertEqual(loaded[0].label, label)

    def test_shared_scheduling_does_not_require_planning_tool(self):
        """Certificaciones y PPTP conservan sus calculos al retirar Planificacion."""
        folder = self.directory / "tools"
        folder.mkdir()
        for name in ("_shared", "certifications", "pptp"):
            shutil.copytree(
                ROOT / "tools" / name, folder / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        shutil.copyfile(ROOT / "tools" / "_library_markdown.py", folder / "_library_markdown.py")
        for name in list(sys.modules):
            if name == "tools" or name.startswith("tools."):
                del sys.modules[name]
        loaded, errors = discover_tools(folder)
        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in loaded], ["pptp", "certifications"])
        self.assertNotIn("tools.planning", sys.modules)
        self.assertIn("tools._shared.scheduling", sys.modules)

    def test_tool_resources_resolve_inside_their_own_folder(self):
        """Los datos exclusivos no se buscan ya en la carpeta principal."""
        discover_tools(ROOT / "tools")
        from tools.safety_health import RELATIONSHIP_PATH
        from tools.pptp import DOCS_ROOT, LIBRARY_PATH

        self.assertEqual(RELATIONSHIP_PATH.parent, ROOT / "tools" / "safety_health" / "datos")
        self.assertTrue(RELATIONSHIP_PATH.is_file())
        self.assertEqual(LIBRARY_PATH, ROOT / "tools" / "pptp" / "BIBLIOTECA.zip")
        self.assertEqual(DOCS_ROOT, ROOT / "tools" / "pptp" / "docs")

    def test_removing_package_hides_it_even_when_imported(self):
        """Retirar una carpeta completa elimina su entrada en el siguiente arranque."""
        package = self.directory / "extra"
        package.mkdir()
        (package / "__init__.py").write_text(PLUGIN, encoding="utf-8")
        loaded, errors = discover_tools(self.directory)
        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in loaded], ["extra"])
        package.rename(self.directory / "_removed")
        self.assertEqual(discover_tools(self.directory), ([], []))

    def test_pptp_uses_catalog_from_relocated_package(self):
        """Lee fichas sinteticas desde archivos de su propia carpeta."""
        for name in ("_shared", "pptp"):
            shutil.copytree(
                ROOT / "tools" / name, self.directory / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        shutil.copyfile(
            ROOT / "tools" / "_library_markdown.py",
            self.directory / "_library_markdown.py",
        )
        resources = self.directory / "pptp" / "archivos" / "UNI"
        resources.mkdir(parents=True, exist_ok=True)
        (resources / "INDICE_FICHAS.md").write_text(
            "| 01TR | [Redes](01TR_REDES/REDES.md) | Revisar |",
            encoding="utf-8",
        )
        (resources / "01TR_REDES").mkdir()
        (resources / "01TR_REDES" / "REDES.md").write_text("# Redes sinteticas", encoding="utf-8")
        for name in list(sys.modules):
            if name == "tools" or name.startswith("tools."):
                del sys.modules[name]
        loaded, errors = discover_tools(self.directory)
        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in loaded], ["pptp"])
        package = sys.modules["tools.pptp"]
        catalog = package.load_catalog(self.directory / "pptp" / "archivos")
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0].code, "01TR")


class QtToolMenuTests(ToolTestCase):
    """Verifica el descubrimiento y los hooks de herramientas en la interfaz PySide6."""

    def write_plugin(self, name, source=PLUGIN):
        """Crea un complemento en la carpeta que descubre la ventana Qt."""
        tools_directory = self.directory / "tools"
        tools_directory.mkdir(exist_ok=True)
        (tools_directory / f"{name}.py").write_text(source, encoding="utf-8")

    @classmethod
    def setUpClass(cls) -> None:
        """Crea una aplicación Qt compartida por las pruebas de menú."""
        cls.application = QApplication.instance() or QApplication([])

    def make_app(self) -> BudgetMainWindow:
        """Construye la ventana con herramientas y preferencias temporales."""
        (self.directory / "tools").mkdir(exist_ok=True)
        (self.directory / "user").mkdir(exist_ok=True)
        with (
            patch("budget_qt_window.application_directory", return_value=self.directory),
            patch(
                "budget_qt_window.user_data_directory",
                return_value=self.directory / "user",
            ),
        ):
            app = BudgetMainWindow()
        self.addCleanup(app.close)
        self.addCleanup(setattr, app, "is_dirty", False)
        return app

    def tool_menu(self, app: BudgetMainWindow) -> QMenu:
        """Devuelve el menú de herramientas de la ventana principal."""
        for action in app.menuBar().actions():
            if action.text() == "Herramientas" and action.menu() is not None:
                return action.menu()
        self.fail("No se encontró el menú Herramientas.")

    def test_qt_menu_discovers_and_executes_plugin(self):
        """Muestra y ejecuta el hook Qt de un complemento descubierto."""
        self.write_plugin("extra")
        app = self.make_app()

        actions = self.tool_menu(app).actions()

        self.assertEqual([action.text() for action in actions], ["Extra"])
        actions[0].trigger()
        self.assertTrue(app.extra_opened)

    def test_qt_menu_reports_plugin_open_errors(self):
        """Notifica un error de apertura del complemento sin romper el menú."""
        self.write_plugin(
            "broken",
            PLUGIN.replace(
                "app.extra_opened = True",
                'raise ValueError("opening")',
            ),
        )
        app = self.make_app()

        with patch.object(QMessageBox, "critical") as error:
            self.tool_menu(app).actions()[0].trigger()

        error.assert_called_once()

    def test_qt_save_hook_does_not_overwrite_project_after_validation_error(self):
        """Preserva el JSON existente si falla el hook Qt previo al guardado."""
        self.write_plugin(
            "bad_save",
            PLUGIN + (
                '\ndef before_project_save_qt(app):\n'
                '    """Falla antes del guardado."""\n'
                '    raise ValueError("saving")\n'
            ),
        )
        app = self.make_app()
        target = self.directory / "previous.json"
        target.write_text('{"previous": true}', encoding="ascii")

        with patch.object(QMessageBox, "critical") as error:
            self.assertFalse(app._write_project_file(target))

        error.assert_called_once()
        self.assertEqual(target.read_text(encoding="ascii"), '{"previous": true}')


if __name__ == "__main__":
    unittest.main()
