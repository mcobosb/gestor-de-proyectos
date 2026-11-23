"""Navegación real de pendientes en el maestro y sus documentos vinculados."""
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from budget_reports import ReportSettings
from odt_reports import _new_report, _tag
from tools._shared.document_pending import LIBRARY_NAME, SCRIPT_URI, macro_entries
from tools.document_structure.office import write_project_master


class DocumentPendingTests(unittest.TestCase):
    """Comprueba el empaquetado Basic y su ejecución en LibreOffice aislado."""

    def test_existing_libraries_and_additional_modules_are_preserved(self):
        """Añade el módulo propio sin borrar las bibliotecas de la plantilla."""
        namespace = "http://openoffice.org/2000/library"
        registry = f'''<library:libraries xmlns:library="{namespace}">
            <library:library library:name="Personal" library:link="false"/>
            <library:library library:name="{LIBRARY_NAME}" library:link="false"/>
        </library:libraries>'''.encode()
        library = f'''<library:library xmlns:library="{namespace}">
            <library:element library:name="Personalizado"/>
        </library:library>'''.encode()
        entries = macro_entries(registry, library)
        name = f"{{{namespace}}}name"
        self.assertEqual(
            [node.get(name) for node in ET.fromstring(entries["Basic/script-lc.xml"])],
            ["Personal", LIBRARY_NAME],
        )
        self.assertEqual(
            [node.get(name) for node in ET.fromstring(entries[f"Basic/{LIBRARY_NAME}/script-lb.xml"])],
            ["Personalizado", "Main"],
        )
        self.assertEqual(macro_entries(entries["Basic/script-lc.xml"])["Basic/script-lc.xml"],
                         entries["Basic/script-lc.xml"])

    def test_navigator_finds_navigates_and_refreshes_linked_documents(self):
        """Ejecuta Basic, el diálogo y sus eventos sin usar el perfil del usuario."""
        program = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "LibreOffice" / "program"
        executable = program / "python.exe"
        if not executable.is_file():
            self.skipTest("La prueba UNO requiere el Python incluido en LibreOffice.")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = ReportSettings()
            source = root / "plantilla.odt"
            template = _new_report("Maestro", "Proyecto", settings)
            template.table_references = False
            for name, page in (("TestTraceability", "Standard"), ("TestIndex", "Section1")):
                style = ET.SubElement(template.automatic_styles, _tag("style:style"), {
                    _tag("style:name"): name, _tag("style:family"): "paragraph",
                    _tag("style:master-page-name"): page,
                })
                ET.SubElement(style, _tag("style:paragraph-properties"), {
                    _tag("fo:break-before"): "page",
                })
            template.paragraph("HOJA DE TRAZABILIDAD", "TestTraceability")
            template.table(
                ["CÓDIGO", "DOCUMENTO", "FECHA EDICIÓN", "EDITADO", "FECHA COMPROBACIÓN", "VALIDADO"],
                [("",) * 6],
            )
            table = template.body[-1]
            header = table.find(_tag("table:table-header-rows"))
            table.insert(list(table).index(header), header[0])
            table.remove(header)
            template.paragraph("[PENDIENTE: Datos del maestro]")
            template.section("ÍNDICE GENERAL DEL PROYECTO")
            template.paragraph("ÍNDICE GENERAL DEL PROYECTO", "TestIndex")
            template.write(source)
            links = [Path("primero.odt"), Path("segundo.odt")]
            first = _new_report("Primero", "Proyecto", settings)
            first.paragraph("[PENDIENTE: Reutilización y destino]")
            first.write(root / links[0])
            second = _new_report("Segundo", "Proyecto", settings)
            second.table(["Dato"], [("[PENDIENTE DE CONCRETAR: Instalaciones]",)])
            second.write(root / links[1])
            target = root / "Maestro.odm"
            write_project_master(
                source, target, fields={}, index_entries=[
                    {"style": "P18", "text": "Primero", "href": "../primero.odt"},
                    {"style": "P18", "text": "Segundo", "href": "../segundo.odt"},
                ], traceability=[], links=links, folder=root,
                logo=Path(__file__).resolve().parents[1] / "iconos" / "Arranque.jpg",
            )
            with ZipFile(target) as archive:
                content = ET.fromstring(archive.read("content.xml"))
                self.assertEqual(
                    [link.text for link in content.iter(_tag("text:a"))
                     if link.get(_tag("xlink:href")) == SCRIPT_URI],
                    ["Revisar pendientes del proyecto"],
                )
                manifest = ET.fromstring(archive.read("META-INF/manifest.xml"))
                declared = {entry.get(_tag("manifest:full-path")) for entry in manifest}
                for path in macro_entries():
                    self.assertIn(path, declared)
                    self.assertEqual(archive.namelist().count(path), 1)
            try:
                result = subprocess.run(
                    [str(executable), str(Path(__file__).with_name("_libreoffice_pending_check.py")),
                     str(target)], capture_output=True, text=True, timeout=90,
                )
            except subprocess.TimeoutExpired as error:
                self.fail(f"LibreOffice no respondió:\n{error.stdout!r}\n{error.stderr!r}")
            finally:
                pid_file = root / "soffice.pid"
                if pid_file.is_file():
                    pid = int(pid_file.read_text().strip())
                    subprocess.run([
                        "powershell", "-NoProfile", "-Command",
                        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
                        f"if ($p -and $p.CommandLine.Contains('{root.name}')) "
                        f"{{ Stop-Process -Id {pid} -Force }}",
                    ], check=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

