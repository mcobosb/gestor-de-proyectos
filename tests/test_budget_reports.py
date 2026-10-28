from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZIP_STORED, ZipFile

from budget_reports import (
    CERTIFICATION_REPORT_NAMES, REPORT_NAMES, ReportSettings, amount_in_words,
    budget_totals, certification_totals, write_certification_pdf, write_pdf,
)
from odt_reports import write_certification_odt, write_report_odt


class ReportCalculationsTests(unittest.TestCase):
    def test_summary_reproduces_the_template_calculation(self):
        """Verify summary reproduces the template calculation."""
        result = budget_totals(138765.72, ReportSettings())
        self.assertEqual(result, {
            "pem": Decimal("138765.72"),
            "general_expenses": Decimal("18039.54"),
            "industrial_profit": Decimal("8325.94"),
            "additions": Decimal("26365.48"),
            "taxable": Decimal("165131.20"),
            "vat": Decimal("34677.55"),
            "total": Decimal("199808.75"),
        })

    def test_zero_and_custom_percentages(self):
        """Verify zero and custom percentages."""
        self.assertEqual(budget_totals(100, ReportSettings(
            general_expenses=0, industrial_profit=0, vat=0,
        ))["total"], Decimal("100.00"))
        self.assertEqual(budget_totals(100, ReportSettings(
            general_expenses=10, industrial_profit=5, vat=21,
        ))["total"], Decimal("139.15"))
        self.assertEqual(budget_totals(0, ReportSettings())["total"], Decimal("0.00"))
        self.assertEqual(budget_totals(0.50, ReportSettings(
            general_expenses=1, industrial_profit=1, vat=0,
        ))["total"], Decimal("0.52"))

    def test_certification_reproduces_discount_and_tax_from_template(self):
        """Verify certification reproduces discount and tax from template."""
        totals = certification_totals(28909.49, ReportSettings(tender_discount=17.302015))
        self.assertEqual(totals["general_expenses"], Decimal("3758.23"))
        self.assertEqual(totals["industrial_profit"], Decimal("1734.57"))
        self.assertEqual(totals["discount"], Decimal("5952.29"))
        self.assertEqual(totals["net"], Decimal("28450.00"))
        self.assertEqual(totals["vat"], Decimal("5974.50"))
        self.assertEqual(totals["total"], Decimal("34424.50"))
        self.assertEqual(certification_totals(100, ReportSettings(tender_discount=100))["total"], Decimal("0.00"))
        self.assertEqual(budget_totals(100, ReportSettings(tender_discount=100))["total"], Decimal("143.99"))

    def test_spanish_amount_words_and_commercial_rounding(self):
        """Verify spanish amount words and commercial rounding."""
        for value, expected in (
            (0, "CERO EUROS"),
            (1.01, "UN EURO CON UN CÉNTIMO"),
            (21.21, "VEINTIÚN EUROS CON VEINTIÚN CÉNTIMOS"),
            (31, "TREINTA Y UN EUROS"),
            (1000000, "UN MILLÓN DE EUROS"),
            (21000, "VEINTIÚN MIL EUROS"),
            (1.005, "UN EURO CON UN CÉNTIMO"),
            (-2, "MENOS DOS EUROS"),
            (199808.75, "CIENTO NOVENTA Y NUEVE MIL OCHOCIENTOS OCHO EUROS CON SETENTA Y CINCO CÉNTIMOS"),
        ):
            with self.subTest(value=value):
                self.assertEqual(amount_in_words(value), expected)

    def test_invalid_configuration_is_rejected(self):
        """Verify invalid configuration is rejected."""
        for data in (
            None, [], {"author": 123}, {"vat": True}, {"vat": "NaN"}, {"vat": -1},
            {"template_path": 123}, {"template_path": "plantilla.odt"},
            {"output_format": "docx"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                ReportSettings.from_dict(data)
        path = str(Path.cwd() / "plantilla.odt")
        settings = ReportSettings.from_dict({"template_path": path})
        self.assertEqual(settings.template_path, path)
        self.assertEqual(
            ReportSettings.from_dict(settings.to_dict()).template_path, path
        )
        self.assertEqual(
            ReportSettings.from_dict({"output_format": "ODT"}).output_format, "odt"
        )


class ReportLayoutTests(unittest.TestCase):
    def setUp(self):
        """Create isolated fixtures and register cleanup for this test case."""
        from bc3_gui import BudgetApp, ProjectItem

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.app = BudgetApp.__new__(BudgetApp)
        self.app.budget_items = [ProjectItem(
            code="P1", unit="m", summary="Resumen singular", chapter="01#",
            text="Descripcion singular para verificar la sangria.",
            measurement=10, unit_price=10, certifications={"2026-10-01": 100},
        )]
        self.app.budget_dataset = None
        self.app.reference_bases = []
        self.app.chapter_order = ["01#"]
        self.app.chapter_titles = {"01#": "Capitulo singular"}
        self.app.project_title = "Proyecto singular"
        self.app.report_settings = ReportSettings(author="Autoria singular")

    @staticmethod
    def spans(page):
        """Extract PDF text spans with their transformed coordinates and fonts."""
        values = []

        def visitor(text, cm, tm, font, size):
            """Collect the extracted PDF text with its position, font and size."""
            if text.strip():
                values.append((
                    text.strip(), tm[4] * cm[0] + tm[5] * cm[2] + cm[4],
                    tm[4] * cm[1] + tm[5] * cm[3] + cm[5],
                    font.get("/BaseFont", "") if font else "", size,
                ))

        page.extract_text(visitor_text=visitor)
        return values

    def test_all_models_repeat_the_original_logo_and_use_real_bold_fonts(self):
        """Verify all models repeat the original logo and use real bold fonts."""
        from pypdf import PdfReader
        from report_layout import _read_report_template

        footer = _read_report_template()[-1]

        for index, name in enumerate((*REPORT_NAMES, *CERTIFICATION_REPORT_NAMES)):
            with self.subTest(name=name):
                target = self.folder / f"{index}.pdf"
                if index < len(REPORT_NAMES):
                    write_pdf(self.app, name, target)
                else:
                    write_certification_pdf(
                        self.app.budget_items, self.app._chapter_title, self.app._project_title(),
                        self.app.report_settings, name, target, "2026-10-01", 1, "Oct 26",
                    )
                reader = PdfReader(target)
                self.assertEqual(reader.metadata.author, "Autoria singular")
                report_text = "\n".join(page.extract_text() for page in reader.pages)
                self.assertIn("Autoria singular", report_text)
                self.assertIn(footer, report_text)
                for page in reader.pages:
                    self.assertEqual(len(page.images), 1)
                    self.assertEqual(page.images[0].image.size, (1513, 570))
                    fonts = [font.get_object().get("/BaseFont", "") for font in page["/Resources"]["/Font"].values()]
                    self.assertTrue(any("Bold" in name for name in fonts))
                    self.assertTrue(any("Arial" in name or "Helvetica" in name for name in fonts))
                if name == REPORT_NAMES[0]:
                    self.assertIn("CUADRO DE PRECIOS 1", reader.pages[0].extract_text())
                    self.assertIn("CUADRO DE PRECIOS 2", reader.pages[1].extract_text())
                    self.assertNotIn("CUADRO DE PRECIOS 1", reader.pages[1].extract_text())

    def test_all_budget_reports_export_as_editable_odt_documents(self):
        """Verify every budget report is written as a readable ODT document."""
        for index, name in enumerate(REPORT_NAMES):
            with self.subTest(name=name):
                target = self.folder / f"report-{index}.odt"
                write_report_odt(self.app, name, target)
                with ZipFile(target) as archive:
                    self.assertEqual(archive.namelist()[0], "mimetype")
                    self.assertEqual(archive.getinfo("mimetype").compress_type, ZIP_STORED)
                    self.assertEqual(archive.read("mimetype").decode(), "application/vnd.oasis.opendocument.text")
                    content = ElementTree.fromstring(archive.read("content.xml"))
                    report_text = " ".join(content.itertext())
                    expected_titles = {
                        REPORT_NAMES[0]: "CUADRO DE PRECIOS 1 Y 2 POR CAPÍTULOS",
                        "Descompuestos": "CUADRO DE PRECIOS DESCOMPUESTOS",
                        "Materiales": "LISTADO ELEMENTALES VALORADOS",
                        "Precios Auxiliares": "CUADRO DE PRECIOS AUXILIARES",
                        "Presupuesto mediciones": "PRESUPUESTO",
                        "Presupuesto resumen": "RESUMEN DE PRESUPUESTO",
                    }
                    self.assertIn("Proyecto singular", report_text)
                    self.assertIn(expected_titles[name], report_text)
                    self.assertIsNone(archive.testzip())

    def test_certification_reports_export_odt_with_the_shared_template(self):
        """Verify certification ODTs retain report data and use the shared template."""
        from odt_reports import _ODF_MIMETYPE
        from report_layout import _read_report_template

        footer = _read_report_template()[-1]

        for index, name in enumerate(CERTIFICATION_REPORT_NAMES):
            with self.subTest(name=name):
                target = self.folder / f"certification-{index}.odt"
                write_certification_odt(
                    self.app.budget_items, self.app._chapter_title, self.app._project_title(),
                    self.app.report_settings, name, target, "2026-10-01", 1, "Oct 26",
                )
                with ZipFile(target) as archive:
                    self.assertEqual(archive.read("mimetype").decode(), _ODF_MIMETYPE)
                    content = ElementTree.fromstring(archive.read("content.xml"))
                    report_text = " ".join(content.itertext())
                    self.assertIn(
                        "Resumen singular" if name == CERTIFICATION_REPORT_NAMES[1]
                        else "Capitulo singular",
                        report_text,
                    )
                    self.assertIn("Pictures/report-template.jpg", archive.namelist())
                    styles = ElementTree.fromstring(archive.read("styles.xml"))
                    self.assertIn(footer, " ".join(styles.itertext()))

    def test_work_item_units_are_printed_in_price_budget_and_certification_details(self):
        """Verify work-item units remain visible in item-level report templates."""
        from pypdf import PdfReader

        reports = (
            (REPORT_NAMES[0], False),
            ("Descompuestos", False),
            ("Presupuesto mediciones", False),
            (CERTIFICATION_REPORT_NAMES[1], True),
        )
        for index, (name, certification) in enumerate(reports):
            with self.subTest(name=name):
                target = self.folder / f"unit-{index}.pdf"
                if certification:
                    write_certification_pdf(
                        self.app.budget_items, self.app._chapter_title, self.app._project_title(),
                        self.app.report_settings, name, target, "2026-10-01", 1, "Oct 26",
                    )
                else:
                    write_pdf(self.app, name, target)
                report_text = "\n".join(page.extract_text() for page in PdfReader(target).pages)
                self.assertRegex(report_text, r"P1\s+m\s+Resumen singular")

    def test_descriptions_are_indented_and_item_summaries_are_bold(self):
        """Verify descriptions are indented and item summaries are bold."""
        from pypdf import PdfReader

        for certification in (False, True):
            with self.subTest(certification=certification):
                target = self.folder / f"indent-{certification}.pdf"
                if certification:
                    write_certification_pdf(
                        self.app.budget_items, self.app._chapter_title, "Proyecto singular",
                        self.app.report_settings, CERTIFICATION_REPORT_NAMES[1], target,
                        "2026-10-01", 1, "Oct 26",
                    )
                else:
                    write_pdf(self.app, REPORT_NAMES[0], target)
                spans = self.spans(PdfReader(target).pages[0])
                code = next(span for span in spans if span[0] == "P1")
                summary = next(span for span in spans if span[0] == "Resumen singular")
                description = next(span for span in spans if span[0].startswith("Descripcion singular"))
                self.assertAlmostEqual(code[1], 68, delta=0.1)
                self.assertAlmostEqual(description[1] - code[1], 57 if certification else 65, delta=0.1)
                self.assertIn("Bold", summary[3])
                self.assertNotIn("Bold", description[3])
                self.assertLess(description[2], summary[2])

    def test_numeric_columns_share_right_edge_and_economic_totals_are_bold(self):
        """Verify numeric columns share right edge and economic totals are bold."""
        from pypdf import PdfReader
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from report_layout import CONTENT_WIDTH, LEFT_MARGIN, ReportLayout

        target = self.folder / "summary.pdf"
        write_pdf(self.app, "Presupuesto resumen", target)
        spans = self.spans(PdfReader(target).pages[0])
        layout = ReportLayout()
        amount = next(span for span in spans if span[0] == "143,99")
        tax = next(span for span in spans if span[0] == "24,99")
        self.assertIn("Bold", amount[3])
        self.assertNotIn("Bold", tax[3])
        self.assertAlmostEqual(amount[1] + stringWidth(amount[0], layout.bold_font, amount[4]),
                               LEFT_MARGIN + CONTENT_WIDTH, delta=0.1)
        self.assertAlmostEqual(tax[1] + stringWidth(tax[0], layout.regular_font, tax[4]),
                               LEFT_MARGIN + CONTENT_WIDTH, delta=0.1)
        label = next(span for span in spans if "Gastos generales" in span[0])
        self.assertGreater(label[1], LEFT_MARGIN + CONTENT_WIDTH * 0.45)

    def test_long_project_title_wraps_without_entering_the_body(self):
        """Verify long project title wraps without entering the body."""
        from pypdf import PdfReader

        self.app.project_title = "Proyecto con un nombre largo " * 25
        target = self.folder / "long-title.pdf"
        write_pdf(self.app, REPORT_NAMES[0], target)
        spans = self.spans(PdfReader(target).pages[0])
        title_lines = [span for span in spans if "Proyecto con" in span[0]]
        chapter = next(span for span in spans if span[0] == "Capitulo singular")
        self.assertGreater(len(title_lines), 1)
        self.assertGreater(min(span[2] for span in title_lines), chapter[2] + 8)

    def test_missing_report_template_raises_instead_of_generating_an_unbranded_pdf(self):
        """Verify a missing report template does not generate an unbranded PDF."""
        target = self.folder / "missing-template.pdf"
        with patch("report_layout.DEFAULT_TEMPLATE_PATH", self.folder / "absent.odt"), self.assertRaises(ValueError):
            write_pdf(self.app, "Presupuesto resumen", target)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
