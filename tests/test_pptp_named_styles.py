"""Estilos editables del PPTP y propagación real de cambios en Writer."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_reports import ReportSettings
from odt_reports import _tag
from tools.pptp.document import write_document
from tools.pptp.sheet_index import SheetIndexRow, markdown_index


class PptpNamedStylesTests(unittest.TestCase):
    def test_writer_named_styles_cover_all_text_without_automatic_format_overrides(self):
        """Los párrafos y caracteres usan estilos públicos únicos con etiquetas legibles."""
        source = (
            "# General\n\nPrimer cuerpo.\n\nSegundo cuerpo con **enfasis**.\n\n"
            "## Capitulo\n\n### Ficha\n\n**Descripcion**\n\n"
            "* Elemento\n    * Anidado\n\n**Tabla 9.1.** Relacion\n\n"
            + markdown_index([SheetIndexRow("CAP. 1", "FICHA", "PPTPHeading2")])
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "named.odt"
            write_document(
                source, target, {"titulo_proyecto": "Proyecto"},
                ReportSettings(output_format="odt"),
            )
            with ZipFile(target) as archive:
                content = ET.fromstring(archive.read("content.xml"))
                styles = ET.fromstring(archive.read("styles.xml"))
            named = {
                style.get(_tag("style:name")): style
                for style in styles.find(_tag("office:styles"))
                if style.tag == _tag("style:style")
            }
            automatic = content.find(_tag("office:automatic-styles"))
            public = {
                name for name, style in named.items()
                if style.get(_tag("style:display-name"), "").startswith("PPTP - ")
            }
            self.assertTrue({
                "PPTPBody", "PPTPTitle", "PPTPSeparator", "PPTPBold", "PPTPIndexCaption",
                "PPTPIndexHeader", "PPTPIndexText", "PPTPIndexBullet", "PPTPBullet0",
                "PPTPBullet1", "PPTPContents1", "PPTPContents2",
                *(f"PPTPHeading{level}" for level in range(1, 7)),
            } <= public)
            self.assertEqual(len(public), len({
                named[name].get(_tag("style:display-name")) for name in public
            }))
            self.assertFalse(any(style.get(_tag("style:name")) in public for style in automatic))
            for element in content.iter():
                style_name = element.get(_tag("text:style-name"))
                if style_name and style_name != "PPTPContentsBreak":
                    self.assertIn(style_name, public)
            for name in ("PPTPSeparator", "PPTPBullet0", "PPTPIndexText", "PPTPContents1"):
                self.assertEqual(named[name].get(_tag("style:parent-style-name")), "PPTPBody")
            self.assertEqual(named["PPTPBold"].get(_tag("style:family")), "text")
            for level in range(1, 7):
                self.assertEqual(
                    named[f"PPTPHeading{level}"].get(_tag("style:default-outline-level")), str(level),
                )

    def test_changing_one_named_body_style_updates_every_body_paragraph_in_writer(self):
        """Modificar el tamaño del estilo público cambia ambos párrafos sin tocar el contenido."""
        soffice = shutil.which("soffice") or r"C:\Program Files\LibreOffice\program\soffice.exe"
        if not Path(soffice).is_file():
            self.skipTest("LibreOffice no disponible para comprobar la propagación renderizada.")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.odt"
            modified = root / "modified.odt"
            write_document(
                "# General\n\nPrimer cuerpo.\n\nSegundo cuerpo.",
                original, {"titulo_proyecto": "Proyecto"}, ReportSettings(output_format="odt"),
            )
            with ZipFile(original) as archive, ZipFile(modified, "w") as target:
                styles = ET.fromstring(archive.read("styles.xml"))
                body = next(
                    style for style in styles.find(_tag("office:styles"))
                    if style.get(_tag("style:name")) == "PPTPBody"
                )
                properties = body.find(_tag("style:text-properties"))
                if properties is None:
                    properties = ET.SubElement(body, _tag("style:text-properties"))
                properties.set(_tag("fo:font-size"), "18pt")
                for info in archive.infolist():
                    target.writestr(
                        info, ET.tostring(styles) if info.filename == "styles.xml" else archive.read(info),
                    )
            with ZipFile(original) as before, ZipFile(modified) as after:
                self.assertEqual(before.read("content.xml"), after.read("content.xml"))
            subprocess.run(
                [soffice, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(root), str(modified)],
                check=True, capture_output=True, timeout=90,
            )
            sizes = {}
            with PdfReader(root / "modified.pdf") as reader:
                for page in reader.pages:
                    def collect(text, cm, tm, font, size):
                        """Mide la tipografía efectiva de cada párrafo con el estilo modificado."""
                        if text.strip() in {"Primer cuerpo.", "Segundo cuerpo."}:
                            sizes[text.strip()] = size

                    page.extract_text(visitor_text=collect)
            self.assertEqual(set(sizes), {"Primer cuerpo.", "Segundo cuerpo."})
            for size in sizes.values():
                self.assertAlmostEqual(size, 18, places=1)
