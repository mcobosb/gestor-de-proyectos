from datetime import date
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree
from zipfile import ZipFile

from budget_reports import ReportSettings
from tools._shared.document_layout import TABLE_HEADER_BACKGROUND
from tools._shared.scheduling import PlanActivity, PlanParams
from tools.planning.document import write_planning_report
from tools.planning.qt_window import PlanningWindowQt


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

    def test_odt_report_contains_editable_bars_and_duration_summary(self):
        """Create an editable ODT chart and append its duration summary."""
        target = self.folder / "planning.odt"
        write_planning_report(
            self.activities, self.params, target, ReportSettings(output_format="odt"),
            "Proyecto de prueba", lambda amount: f"{amount:.2f} €",
        )

        with ZipFile(target) as archive:
            content = ElementTree.fromstring(archive.read("content.xml"))
            styles = ElementTree.fromstring(archive.read("styles.xml"))
        text = " ".join(content.itertext())
        namespaces = {
            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
            "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
        }
        table = next(
            element for element in content.iter()
            if element.tag == f"{{{namespaces['table']}}}table"
            and element.get(f"{{{namespaces['table']}}}name") == "PlanningGantt"
        )
        table_rows = [
            element for element in table.iter()
            if element.tag == f"{{{namespaces['table']}}}table-row"
        ]
        second_column = [
            " ".join(cell.itertext())
            for row in table_rows
            for cell in row.findall("table:table-cell", namespaces)[1:2]
        ]
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
        self.assertIn("3 días laborables", text)
        self.assertEqual(second_column, ["MEDICIÓN / PEM", "800.00 €", "3,00 ud", "5,00 m"])
        header_cell_style = next(
            style for style in content.iter()
            if style.get(f"{{{namespaces['style']}}}name") == "PlanningHeaderCell"
        )
        header_properties = header_cell_style.find(
            "style:table-cell-properties", namespaces,
        )
        self.assertEqual(
            header_properties.get(f"{{{namespaces['fo']}}}background-color"),
            TABLE_HEADER_BACKGROUND,
        )
        self.assertEqual(page_layout.attrib[f"{{{namespaces['fo']}}}page-width"], "29.7cm")
        self.assertEqual(
            page_layout.attrib[f"{{{namespaces['style']}}}print-orientation"], "landscape",
        )
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
            self.activities, self.params, target, ReportSettings(output_format="pdf"),
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
        self.assertIn("3,00 ud", text)
        self.assertIn("800.00 €", text)
        from pypdf.generic import ContentStream

        operations = ContentStream(page.get_contents(), reader).operations
        self.assertTrue(any(
            operator == b"rg"
            and all(abs(float(actual) - expected) < 0.002 for actual, expected in zip(
                operands, (31 / 255, 119 / 255, 180 / 255),
            ))
            for operands, operator in operations
        ))
        self.assertTrue(any(
            operator == b"rg"
            and all(abs(float(actual) - expected) < 0.002 for actual, expected in zip(
                operands, (68 / 255, 114 / 255, 196 / 255),
            ))
            for operands, operator in operations
        ))

    def test_qt_save_action_uses_the_configured_format(self):
        """Use the selected format and keep the exported file at its chosen path."""
        for output_format in ("odt", "pdf"):
            with self.subTest(output_format=output_format):
                settings = ReportSettings(output_format=output_format)
                expected = self.folder / f"plan.{output_format}"
                window = SimpleNamespace(
                    activities=self.activities,
                    params=self.params,
                    project_title="Proyecto de prueba",
                    app=SimpleNamespace(
                        report_settings=settings,
                        _format_currency=lambda amount: f"{amount:.2f} €",
                    ),
                    recalculate=Mock(),
                    status=SimpleNamespace(setText=Mock()),
                )
                with (
                    patch(
                        "tools.planning.qt_window.get_document_save_path",
                        return_value=(expected, output_format),
                    ) as dialog,
                    patch("tools.planning.qt_window.write_planning_report") as writer,
                ):
                    PlanningWindowQt.save_plan_report(window)

                self.assertTrue(expected.exists())
                dialog.assert_called_once_with(
                    window, "Guardar planificación", "Plan de trabajo.pdf",
                    document_section="DOC02", annex_tool="planning",
                )
                writer.assert_called_once()
                self.assertEqual(writer.call_args.args[0:2], (self.activities, self.params))
                self.assertEqual(writer.call_args.args[3:5], (settings, "Proyecto de prueba"))
                self.assertEqual(writer.call_args.args[2].suffix, f".{output_format}")


if __name__ == "__main__":
    unittest.main()
