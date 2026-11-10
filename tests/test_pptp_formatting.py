"""Listas numeradas y negritas reales en los documentos PPTP."""
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_reports import ReportSettings
from tools.pptp.document import text_blocks, write_document


class PptpFormattingTests(unittest.TestCase):
    def test_lists_and_separators_are_independent_blocks(self):
        """Preserve each ordered item, explicit numbering and Markdown bold runs."""
        source = (
            "**Ejecución**\n"
            "1. **Previos:** Proteger.\n"
            "2. **Desmontaje/Demolición:** Retirar.\n"
            "3. **Control y remate:** Limpiar.\n"
            "**Medición**\n"
            "Longitud neta."
        )
        self.assertEqual(text_blocks(source, preserve_bold=True), [
            (0, "**Ejecución**"),
            (-1, "1. **Previos:** Proteger."),
            (-1, "2. **Desmontaje/Demolición:** Retirar."),
            (-1, "3. **Control y remate:** Limpiar."),
            (0, "**Medición**"),
            (0, "Longitud neta."),
        ])

    def test_pdf_and_odt_have_separate_numbered_paragraphs_and_actual_bold(self):
        """Check paragraph placement and font styles, not just extracted strings."""
        source = (
            "# Ficha de prueba\n\n**Descripción**\n\nTexto con **negrita** y <etiqueta> & símbolo.\n\n"
            "**Ejecución**\n"
            "1. **Previos:** Comprobar la estabilidad.\n"
            "2. **Desmontaje/Demolición:** Retirar el revestimiento.\n"
            "3. **Control y remate:** Limpiar el soporte.\n\n"
            "**Medición**\n\nLongitud neta.\n\n**Valoración**\n\nPrecio aprobado."
        )
        namespaces = {
            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
            "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
        }
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("pdf", "odt"):
                with self.subTest(format=extension):
                    target = Path(directory) / ("PPTP." + extension)
                    write_document(
                        source, target, {"titulo_proyecto": "Proyecto"},
                        ReportSettings(output_format=extension),
                    )
                    if extension == "odt":
                        with ZipFile(target) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                        paragraphs = content.findall(".//text:p", namespaces)
                        items = [
                            paragraph for paragraph in paragraphs
                            if "".join(paragraph.itertext()).startswith(("1. Previos:", "2. Desmontaje/", "3. Control"))
                        ]
                        self.assertEqual(len(items), 3)
                        for item, label in zip(items, ("Previos:", "Desmontaje/Demolición:", "Control y remate:")):
                            bold = item.find("text:span[@text:style-name='PPTPBold']", namespaces)
                            self.assertIsNotNone(bold)
                            self.assertEqual(bold.text, label)
                        bold_properties = content.find(
                            ".//style:style[@style:name='PPTPBold']/style:text-properties", namespaces,
                        )
                        self.assertEqual(bold_properties.get(f"{{{namespaces['fo']}}}font-weight"), "bold")
                        headings = content.findall(".//text:h", namespaces)
                        self.assertEqual(len(headings), 1)
                        for label in ("Descripción", "Ejecución", "Medición", "Valoración"):
                            paragraph = next(p for p in paragraphs if "".join(p.itertext()) == label)
                            self.assertEqual(
                                paragraph.get(f"{{{namespaces['text']}}}style-name"), "PPTPSeparator",
                            )
                        text = "".join(content.itertext())
                    else:
                        pieces = []
                        for page_number, page in enumerate(PdfReader(target).pages):
                            page.extract_text(visitor_text=lambda text, cm, tm, font, size: pieces.append(
                                (text, page_number, tm[4] * cm[1] + tm[5] * cm[3] + cm[5],
                                 font.get("/BaseFont", "") if font else ""),
                            ))
                        text = "".join(piece[0] for piece in pieces)
                        starts = [
                            next(piece for piece in pieces if piece[0].startswith(prefix))
                            for prefix in ("1. ", "2. ", "3. ")
                        ]
                        self.assertEqual(len({(piece[1], piece[2]) for piece in starts}), 3)
                        for label in ("Previos:", "Desmontaje/Demolición:", "Control y remate:", "Ejecución"):
                            self.assertTrue(any(label in piece[0] and "Bold" in piece[3] for piece in pieces), label)
                    self.assertNotIn("**", text)
                    self.assertIn("<etiqueta> & símbolo", text)


if __name__ == "__main__":
    unittest.main()
