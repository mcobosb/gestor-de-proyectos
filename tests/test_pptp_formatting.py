"""Listas numeradas y negritas reales en los documentos PPTP."""
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_reports import ReportSettings
from odt_reports import _tag
from report_layout import DEFAULT_TEMPLATE_PATH
from tools.pptp.document import text_blocks, write_document


class PptpFormattingTests(unittest.TestCase):
    def test_tabbed_rows_and_bold_numbered_items_do_not_merge_or_lose_indentation(self):
        """Retain tabbed rows and recognize numbering even inside bold markup."""
        self.assertEqual(text_blocks(
            "\t**1. Previos:** Comprobar.\n\t**2. Remate:** Limpiar.\n"
            "Clase\tCódigo\tAplicación\nSin riesgo\tX0\tInterior\n"
            "\tPárrafo sangrado.\n\t\tOtro párrafo.\n",
            preserve_bold=True,
        ), [
            (-2, "**1. Previos:** Comprobar."),
            (-2, "**2. Remate:** Limpiar."),
            (0, "Clase\tCódigo\tAplicación"),
            (0, "Sin riesgo\tX0\tInterior"),
            (0, "\tPárrafo sangrado."),
            (0, "\t\tOtro párrafo."),
        ])

    def test_tabs_have_real_odt_elements_and_measurable_pdf_indentation_and_columns(self):
        """Check visible PDF coordinates and ODF tab elements, including tabs in bold."""
        source = (
            "# Ficha\n\nPlano\n\n\tUna sangría\n\t\tDos sangrías\n"
            "\t**1. Previos:** Comprobar.\n\t**2. Remate:** Limpiar.\n\n"
            "Clase\tCódigo\tAplicación\n"
            "Sin riesgo\t**X0**\tInterior\n"
            "\tXC1\tExterior\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("odt", "pdf"):
                with self.subTest(format=extension):
                    target = Path(directory) / ("PPTP." + extension)
                    write_document(source, target, {"titulo_proyecto": "Proyecto"},
                                   ReportSettings(output_format=extension))
                    if extension == "odt":
                        with ZipFile(target) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                        paragraphs = list(content.iter(_tag("text:p")))
                        first = next(p for p in paragraphs if "".join(p.itertext()) == "Una sangría")
                        second = next(p for p in paragraphs if "".join(p.itertext()) == "Dos sangrías")
                        self.assertEqual(len(list(first.iter(_tag("text:tab")))), 1)
                        self.assertEqual(len(list(second.iter(_tag("text:tab")))), 2)
                        row = next(p for p in paragraphs if "".join(p.itertext()) == "Sin riesgoX0Interior")
                        self.assertEqual(len(list(row.iter(_tag("text:tab")))), 2)
                        self.assertTrue(any(
                            span.text == "X0" and span.get(_tag("text:style-name")) == "PPTPBold"
                            for span in row.iter(_tag("text:span"))
                        ))
                    else:
                        pieces = []
                        for page in PdfReader(target).pages:
                            page.extract_text(visitor_text=lambda text, cm, tm, font, size: pieces.append((
                                text.strip(), tm[4] * cm[0] + tm[5] * cm[2] + cm[4],
                                tm[4] * cm[1] + tm[5] * cm[3] + cm[5],
                            )))
                        def position(label):
                            """Return the PDF location of a uniquely labeled test fragment."""
                            return next((x, y) for text, x, y in pieces if text == label)

                        plain = position("Plano")[0]
                        self.assertAlmostEqual(position("Una sangría")[0] - plain, 12)
                        self.assertAlmostEqual(position("Dos sangrías")[0] - plain, 24)
                        self.assertAlmostEqual(position("Clase")[0], position("Sin riesgo")[0])
                        self.assertAlmostEqual(position("Código")[0], position("X0")[0])
                        self.assertAlmostEqual(position("X0")[0], position("XC1")[0])
                        self.assertAlmostEqual(position("Aplicación")[0], position("Interior")[0])
                        self.assertAlmostEqual(position("Interior")[0], position("Exterior")[0])
                        self.assertNotEqual(position("Clase")[1], position("Sin riesgo")[1])

    def test_odt_inherits_template_named_styles_and_defaults_without_pdf_overrides(self):
        """Preserve template fonts, spacing, alignment, tabs and semantic heading parents."""
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "template.odt"
            with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as target:
                root = ET.fromstring(source.read("styles.xml"))
                styles = root.find(_tag("office:styles"))
                default = next((
                    style for style in styles
                    if style.tag == _tag("style:default-style")
                    and style.get(_tag("style:family")) == "paragraph"
                ), None)
                if default is None:
                    default = ET.SubElement(styles, _tag("style:default-style"), {
                        _tag("style:family"): "paragraph",
                    })
                properties = default.find(_tag("style:text-properties"))
                if properties is None:
                    properties = ET.SubElement(default, _tag("style:text-properties"))
                properties.set(_tag("fo:font-size"), "13pt")
                for name, display, size, level in (
                    ("CustomBody", "Cuerpo", "11pt", None),
                    ("CustomHeading1", "Título 1", "19pt", "1"),
                    ("CustomHeading2", "Título 2", "15pt", "2"),
                ):
                    style = ET.SubElement(styles, _tag("style:style"), {
                        _tag("style:name"): name, _tag("style:display-name"): display,
                        _tag("style:family"): "paragraph",
                        **({_tag("style:default-outline-level"): level} if level else {}),
                    })
                    ET.SubElement(style, _tag("style:text-properties"), {
                        _tag("fo:font-size"): size, _tag("fo:font-family"): "Liberation Serif",
                        _tag("fo:color"): "#123456",
                    })
                    paragraph = ET.SubElement(style, _tag("style:paragraph-properties"), {
                        _tag("fo:margin-top"): "0.37cm", _tag("fo:line-height"): "137%",
                        _tag("fo:text-align"): "end",
                    })
                    tabs = ET.SubElement(paragraph, _tag("style:tab-stops"))
                    ET.SubElement(tabs, _tag("style:tab-stop"), {
                        _tag("style:position"): "2.3cm", _tag("style:type"): "left",
                    })
                for info in source.infolist():
                    target.writestr(info, ET.tostring(root) if info.filename == "styles.xml" else source.read(info))
            before = template.read_bytes()
            output = Path(directory) / "PPTP.odt"
            write_document(
                "# Uno\n\n## Dos\n\nCuerpo\tcon tabulador.\n\n**Descripción**\n\nTexto.",
                output, {"titulo_proyecto": "Proyecto"},
                ReportSettings(output_format="odt", template_path=str(template)),
            )
            with ZipFile(output) as archive:
                content = ET.fromstring(archive.read("content.xml"))
                result = ET.fromstring(archive.read("styles.xml"))
            automatic = {
                style.get(_tag("style:name")): style
                for style in content.find(_tag("office:automatic-styles"))
            }
            for name, parent in (
                ("PPTPBody", "TemplateCustomBody"),
                ("PPTPHeading1", "TemplateCustomHeading1"),
                ("PPTPHeading2", "TemplateCustomHeading2"),
            ):
                self.assertEqual(automatic[name].get(_tag("style:parent-style-name")), parent)
                self.assertIsNone(automatic[name].find(_tag("style:paragraph-properties")))
                self.assertIsNone(automatic[name].find(_tag("style:text-properties")))
            named = {
                style.get(_tag("style:name")): style
                for style in result.find(_tag("office:styles"))
                if style.tag == _tag("style:style")
            }
            for name, size in (("CustomBody", "11pt"), ("CustomHeading1", "19pt"), ("CustomHeading2", "15pt")):
                style = named["Template" + name]
                self.assertEqual(style.find(_tag("style:text-properties")).get(_tag("fo:font-size")), size)
                properties = style.find(_tag("style:paragraph-properties"))
                self.assertEqual(properties.get(_tag("fo:text-align")), "end")
                self.assertEqual(properties.get(_tag("fo:line-height")), "137%")
                self.assertEqual(properties.find(_tag("style:tab-stops"))[0].get(_tag("style:position")), "2.3cm")
            default = next(
                style for style in result.find(_tag("office:styles"))
                if style.tag == _tag("style:default-style") and style.get(_tag("style:family")) == "paragraph"
            )
            self.assertEqual(default.find(_tag("style:text-properties")).get(_tag("fo:font-size")), "13pt")
            self.assertEqual(template.read_bytes(), before)

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
