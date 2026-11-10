"""Regresiones de tablas de cabecera con imágenes desplazadas entre celdas."""
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from odt_reports import _tag
from report_layout import CONTENT_WIDTH, DEFAULT_TEMPLATE_PATH, ReportLayout


class TemplateHeaderTableTests(unittest.TestCase):
    def setUp(self):
        """Create a two-column template with the logo anchored in the title cell."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.template = self.folder / "table-header.odt"
        with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(self.template, "w") as target:
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename == "styles.xml":
                    root = ET.fromstring(data)
                    header = root.find(f".//{_tag('style:header')}")
                    frame = header.find(f".//{_tag('draw:frame')}")
                    for child in list(header):
                        header.remove(child)
                    automatic = root.find(_tag("office:automatic-styles"))
                    for name, family, properties, attributes in (
                        ("TestTable", "table", "table-properties", {
                            "style:width": "17cm", "table:align": "margins",
                        }),
                        ("TestA", "table-column", "table-column-properties", {
                            "style:column-width": "10.9cm",
                        }),
                        ("TestB", "table-column", "table-column-properties", {
                            "style:column-width": "6.1cm",
                        }),
                        ("TestRow", "table-row", "table-row-properties", {
                            "style:min-row-height": "2.1cm",
                        }),
                        ("TestGraphic", "graphic", "graphic-properties", {
                            "style:horizontal-pos": "from-left",
                            "style:horizontal-rel": "paragraph",
                            "style:vertical-rel": "paragraph",
                            "style:wrap": "run-through",
                        }),
                    ):
                        style = ET.SubElement(automatic, _tag("style:style"), {
                            _tag("style:name"): name, _tag("style:family"): family,
                        })
                        ET.SubElement(style, _tag(f"style:{properties}"), {
                            _tag(key): value for key, value in attributes.items()
                        })
                    table = ET.SubElement(header, _tag("table:table"), {
                        _tag("table:style-name"): "TestTable",
                    })
                    for name in ("TestA", "TestB"):
                        ET.SubElement(table, _tag("table:table-column"), {
                            _tag("table:style-name"): name,
                        })
                    row = ET.SubElement(table, _tag("table:table-row"), {
                        _tag("table:style-name"): "TestRow",
                    })
                    first = ET.SubElement(row, _tag("table:table-cell"))
                    paragraph = ET.SubElement(first, _tag("text:p"))
                    paragraph.text = "TÍTULO DEL PROYECTO"
                    paragraph.append(frame)
                    frame.tail = " CONTINUACIÓN DEL TÍTULO"
                    frame.set(_tag("draw:style-name"), "TestGraphic")
                    frame.set(_tag("text:anchor-type"), "char")
                    for key, value in (
                        ("x", "11.55cm"), ("y", "0.138cm"),
                        ("width", "4.683cm"), ("height", "1.633cm"),
                    ):
                        frame.set(_tag(f"svg:{key}"), value)
                    second = ET.SubElement(row, _tag("table:table-cell"))
                    ET.SubElement(second, _tag("text:p"))
                    data = ET.tostring(root, encoding="utf-8")
                target.writestr(entry, data)

    def test_pdf_title_has_usable_width_and_logo_belongs_to_second_cell(self):
        """Prevent the floating logo from consuming the title column's entire width."""
        layout = ReportLayout(template_path=str(self.template), repeat_titles=False)
        table = layout.template_tables["header"][0]
        self.assertAlmostEqual(sum(table["widths"]), CONTENT_WIDTH)
        graphic = layout.template_graphics[0]
        self.assertEqual(graphic["table_column_index"], 1)
        block = layout._template_table_flowable(table, "header", 0)
        title = block._cellvalues[0][0]
        self.assertEqual(title.style.leftIndent, 0)
        self.assertEqual(title.getPlainText(), "TÍTULO DEL PROYECTO CONTINUACIÓN DEL TÍTULO")

    def test_odt_logo_is_inline_in_second_cell_without_external_offset(self):
        """Keep the original title and place the image inside its visual target cell."""
        from budget_reports import ReportSettings
        from odt_reports import _new_report

        report = _new_report(
            "PPTP", "Proyecto", ReportSettings(template_path=str(self.template)),
            repeat_titles=False,
        )
        report.paragraph("Contenido")
        target = self.folder / "output.odt"
        report.write(target)
        with ZipFile(target) as archive:
            root = ET.fromstring(archive.read("styles.xml"))
        header = root.find(f".//{_tag('style:header')}")
        cells = header.findall(f".//{_tag('table:table-cell')}")
        self.assertIsNone(cells[0].find(f".//{_tag('draw:frame')}"))
        self.assertIn("CONTINUACIÓN DEL TÍTULO", "".join(cells[0].itertext()))
        frame = cells[1].find(f".//{_tag('draw:frame')}")
        self.assertIsNotNone(frame)
        self.assertEqual(frame.get(_tag("text:anchor-type")), "as-char")
        self.assertIsNone(frame.get(_tag("svg:x")))
        self.assertIsNone(frame.get(_tag("svg:y")))
        table = header.find(_tag("table:table"))
        name = table.get(_tag("table:style-name"))
        table_style = next(
            style for style in root.iter(_tag("style:style"))
            if style.get(_tag("style:name")) == name
        )
        self.assertAlmostEqual(
            float(table_style.find(_tag("style:table-properties")).get(
                _tag("style:width"),
            )[:-2]), CONTENT_WIDTH, places=5,
        )


if __name__ == "__main__":
    unittest.main()
