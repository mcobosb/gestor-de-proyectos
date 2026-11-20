from datetime import date
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from budget_reports import ReportSettings
from report_layout import DEFAULT_TEMPLATE_PATH
from tools._shared.scheduling import PlanActivity, PlanParams
from tools.planning.window import PlanningWindow
from tools.planning.document import write_planning_report


class PlanningReportTests(unittest.TestCase):
    def setUp(self):
        """Create a temporary folder and a sample Gantt image."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.params = PlanParams(start=date(2026, 11, 9), scale=7, plan="Completo")
        self.activities = [
            PlanActivity(
                id=1, chapter="01#", chapter_name="Preparación", code="P1",
                summary="Primera actividad", unit="ud", quantity=3, amount=300,
                rendimiento=1, duration=3, start_offset=0, finish_offset=3,
                start_date=date(2026, 11, 9), end_date=date(2026, 11, 11),
            ),
            PlanActivity(
                id=2, chapter="01#", chapter_name="Preparación", code="P2",
                summary="Actividad más larga", unit="m", quantity=5, amount=500,
                rendimiento=1, duration=5, start_offset=3, finish_offset=8,
                start_date=date(2026, 11, 12), end_date=date(2026, 11, 18),
            ),
        ]
        self.template = self.folder / "template.odt"
        self._create_template_with_footer_fields()

    def _create_template_with_footer_fields(self):
        """Create a copy of the bundled template with chapter and page markers."""
        namespaces = {
            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        }
        with ZipFile(DEFAULT_TEMPLATE_PATH) as source:
            files = {name: source.read(name) for name in source.namelist()}
        styles = ElementTree.fromstring(files["styles.xml"])
        footer = styles.find(".//style:master-page/style:footer", namespaces)
        paragraph = footer.find(".//text:p", namespaces)
        paragraph_attributes = dict(paragraph.attrib)
        paragraph.clear()
        paragraph.attrib.update(paragraph_attributes)
        paragraph.text = "{{capítulo}} / {{no_página}}"
        files["styles.xml"] = ElementTree.tostring(
            styles, encoding="utf-8", xml_declaration=True,
        )
        with ZipFile(self.template, "w") as target:
            target.writestr("mimetype", files.pop("mimetype"), compress_type=ZIP_STORED)
            for name, content in files.items():
                target.writestr(name, content, compress_type=ZIP_DEFLATED)

    def _settings(self, output_format):
        """Return report settings bound to the temporary template."""
        return ReportSettings(output_format=output_format, template_path=str(self.template))

    def test_odt_report_contains_editable_bars_and_duration_summary(self):
        """Create an editable ODT chart and append its duration summary."""
        target = self.folder / "planning.odt"
        write_planning_report(
            self.activities, self.params, target, self._settings("odt"),
            "Proyecto de prueba", lambda amount: f"{amount:.2f} €",
        )

        with ZipFile(target) as archive:
            content = ElementTree.fromstring(archive.read("content.xml"))
            styles = ElementTree.fromstring(archive.read("styles.xml"))
        text = " ".join(content.itertext())
        styles_text = " ".join(styles.itertext())
        namespaces = {
            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
            "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
        }
        page_layout = styles.find(
            ".//style:page-layout[@style:name='PageLayout']/style:page-layout-properties",
            namespaces,
        )
        self.assertTrue(any(element.tag.endswith("}table") for element in content.iter()))
        self.assertFalse(any(element.tag.endswith("}image") for element in content.iter()))
        self.assertIn("P1", text)
        self.assertIn("P2", text)
        self.assertIn("8 días laborables (10 días naturales)", text)
        self.assertIn("P2 — Actividad más larga", text)
        self.assertNotIn("Proyecto de prueba", styles_text)
        self.assertNotIn("PLAN DE TRABAJO", styles_text)
        self.assertEqual(page_layout.attrib[f"{{{namespaces['fo']}}}page-width"], "29.7cm")
        self.assertEqual(
            page_layout.attrib[f"{{{namespaces['style']}}}print-orientation"], "landscape",
        )
        self.assertIn("PLANIFICACIÓN", styles_text)
        self.assertTrue(any(element.tag.endswith("}page-number") for element in styles.iter()))
        self.assertTrue(any(
            element.tag.endswith("}table-cell-properties")
            and element.attrib.get("{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}background-color")
            == "#1f77b4"
            for element in content.iter()
        ))

    def test_pdf_report_contains_vector_bars_and_duration_summary(self):
        """Write a PDF with vector bars, searchable activity labels and summary."""
        from pypdf import PdfReader

        target = self.folder / "planning.pdf"
        write_planning_report(
            self.activities, self.params, target, self._settings("pdf"),
            "Proyecto de prueba", lambda amount: f"{amount:.2f} €",
        )

        reader = PdfReader(target)
        self.assertEqual(len(reader.pages), 1)
        page = reader.pages[0]
        text = page.extract_text()
        self.assertGreater(float(page.mediabox.width), float(page.mediabox.height))
        self.assertNotIn("Proyecto de prueba", text)
        self.assertNotIn("PLAN DE TRABAJO", text)
        self.assertIn("Actividad más larga", text)
        self.assertIn("8 días laborables (10 días naturales)", text)
        self.assertIn("PLANIFICACIÓN", text)
        self.assertIn("1", text)
        from pypdf.generic import ContentStream

        operations = ContentStream(page.get_contents(), reader).operations
        self.assertTrue(any(
            operator == b"rg"
            and all(abs(float(actual) - expected) < 0.002 for actual, expected in zip(
                operands, (31 / 255, 119 / 255, 180 / 255),
            ))
            for operands, operator in operations
        ))

    def test_save_action_uses_the_configured_format_and_keeps_detail_action(self):
        """Use the selected format without generating the secondary detail figure."""
        for output_format in ("odt", "pdf"):
            with self.subTest(output_format=output_format):
                settings = ReportSettings(output_format=output_format)
                requested = self.folder / "plan.other"
                expected = self.folder / f"plan.{output_format}"
                window = SimpleNamespace(
                    activities=self.activities,
                    params=self.params,
                    project_title="Proyecto de prueba",
                    _report_settings=lambda: settings,
                    _currency_formatter=lambda amount: f"{amount:.2f} €",
                    _recalculate=Mock(),
                    _generate_figure=Mock(),
                    _set_status=Mock(),
                )
                with (
                    patch(
                        "tools.planning.window.filedialog.asksaveasfilename",
                        return_value=str(requested),
                    ) as dialog,
                    patch("tools.planning.window.write_planning_report") as writer,
                ):
                    PlanningWindow.save_plan_report(window)

                self.assertTrue(expected.exists())
                self.assertEqual(dialog.call_args.kwargs["defaultextension"], f".{output_format}")
                self.assertEqual(dialog.call_args.kwargs["filetypes"][0][1], f"*.{output_format}")
                writer.assert_called_once()
                self.assertEqual(writer.call_args.args[0:2], (self.activities, self.params))
                self.assertEqual(writer.call_args.args[3:5], (settings, "Proyecto de prueba"))
                window._generate_figure.assert_not_called()

        detail_window = SimpleNamespace(
            _generate_figure=Mock(return_value=(self.folder / "detail.png", self.folder / "detail.pdf")),
            _show_result=Mock(),
        )
        PlanningWindow.draw_plan(detail_window)
        detail_window._show_result.assert_called_once_with(
            self.folder / "detail.png", self.folder / "detail.pdf",
        )


if __name__ == "__main__":
    unittest.main()
