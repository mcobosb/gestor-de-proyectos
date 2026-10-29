from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZIP_STORED, ZipFile

from budget_reports import (
    CERTIFICATION_REPORT_NAMES, DEVIATION_REPORT_NAME, REPORT_NAMES, ReportSettings,
    amount_in_words, budget_totals, certification_deviation_rows,
    certification_totals, write_certification_pdf, write_pdf,
)
from odt_reports import write_certification_odt, write_report_odt


class ReportCalculationsTests(unittest.TestCase):
    def test_deviations_use_original_plan_and_include_uncertified_items(self):
        """Preserve original monthly forecasts and reconcile rounded row totals."""
        from bc3_gui import ProjectItem

        items = [
            ProjectItem(code="P1", chapter="01#", unit="ud", summary="Partida uno",
                        certifications={"2026-10-01": 120.005},
                        certification_plans={"2026-10-01": 100}),
            ProjectItem(code="P2", chapter="01#", unit="ud", summary="Pendiente"),
        ]
        rows, total = certification_deviation_rows(items, [120.005, 50], "2026-10-01")
        self.assertEqual(rows[0][3:6], [Decimal("100.00"), Decimal("120.01"), Decimal("20.01")])
        self.assertEqual(rows[1][3:6], [Decimal("50.00"), Decimal("0.00"), Decimal("-50.00")])
        self.assertEqual(total[3:6], [Decimal("150.00"), Decimal("120.01"), Decimal("-29.99")])
        for plans in (None, [], [float("nan"), 50], [float("inf"), 50]):
            with self.subTest(plans=plans):
                if plans and len(plans) == 2:
                    items[0].certification_plans.clear()
                with self.assertRaises(ValueError):
                    certification_deviation_rows(items, plans, "2026-10-01")

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
            {"indirect_costs": True}, {"indirect_costs": "nan"},
            {"indirect_costs": "inf"}, {"indirect_costs": -1},
            {"indirect_costs": 101}, {"indirect_costs": ""},
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
        self.assertEqual(ReportSettings.from_dict({}).indirect_costs, 0)
        self.assertEqual(ReportSettings.from_dict({"indirect_costs": "3,5"}).indirect_costs, 3.5)


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

    def assert_odt_styles(self, archive, expected_used_styles=()):
        """Verify ODT paragraphs and cells reference declared reusable styles."""
        namespaces = {
            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        }
        content = ElementTree.fromstring(archive.read("content.xml"))
        styles = ElementTree.fromstring(archive.read("styles.xml"))
        declarations = {
            (element.get(f"{{{namespaces['style']}}}family"),
             element.get(f"{{{namespaces['style']}}}name"))
            for root in (content, styles)
            for element in root.iter(f"{{{namespaces['style']}}}style")
        }
        used_paragraph_styles = set()
        for element in content.iter():
            if element.tag in {
                f"{{{namespaces['text']}}}p",
                f"{{{namespaces['text']}}}h",
            }:
                name = element.get(f"{{{namespaces['text']}}}style-name")
                self.assertTrue(name, f"Unstyled text element: {element.tag}")
                self.assertIn(("paragraph", name), declarations)
                used_paragraph_styles.add(name)
            elif element.tag == f"{{{namespaces['table']}}}table-cell":
                name = element.get(f"{{{namespaces['table']}}}style-name")
                self.assertTrue(name, "Unstyled table cell")
                self.assertIn(("table-cell", name), declarations)
        for element in styles.iter(f"{{{namespaces['text']}}}p"):
            name = element.get(f"{{{namespaces['text']}}}style-name")
            self.assertTrue(name, "Unstyled header or footer text")
            self.assertIn(("paragraph", name), declarations)
            used_paragraph_styles.add(name)
        for name in expected_used_styles:
            self.assertIn(name, used_paragraph_styles)

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
        from io import BytesIO
        from PIL import Image
        from pypdf import PdfReader
        from report_layout import _read_report_template

        template = _read_report_template()
        footer = template[-1]
        with Image.open(BytesIO(template[0])) as logo:
            expected_logo_size = logo.size

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
                    self.assertEqual(page.images[0].image.size, expected_logo_size)
                    fonts = [font.get_object().get("/BaseFont", "") for font in page["/Resources"]["/Font"].values()]
                    self.assertTrue(any("Bold" in name for name in fonts))
                    self.assertTrue(any("Arial" in name or "Helvetica" in name for name in fonts))
                if name == "Cuadro de precios 1 y 2 por capitulos":
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
                    styles = ElementTree.fromstring(archive.read("styles.xml"))
                    report_text = " ".join((*content.itertext(), *styles.itertext()))
                    expected_titles = {
                        "Cuadro de precios 1 y 2 por capitulos": "CUADRO DE PRECIOS 1",
                        "Mediciones": "MEDICIONES",
                        "Descompuestos": "CUADRO DE PRECIOS DESCOMPUESTOS",
                        "Materiales": "LISTADO ELEMENTALES VALORADOS",
                        "Precios Auxiliares": "CUADRO DE PRECIOS AUXILIARES",
                        "Presupuesto mediciones": "PRESUPUESTO",
                        "Presupuesto resumen": "RESUMEN DE PRESUPUESTO",
                    }
                    self.assertIn("Proyecto singular", report_text)
                    self.assertIn(expected_titles[name], report_text)
                    self.assert_odt_styles(
                        archive,
                        {"ReportTitle", "ProjectTitle"} |
                        ({"ChapterTitle", "TableItemTitle", "ItemDescription", "AmountInWords"}
                         if name == "Cuadro de precios 1 y 2 por capitulos" else set()),
                    )
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
                    self.assert_odt_styles(
                        archive,
                        {"ReportTitle", "ProjectTitle"} |
                        ({"ChapterTitle", "TableItemTitle", "ItemDescription"}
                         if name == CERTIFICATION_REPORT_NAMES[1] else set()),
                    )

    def test_work_item_units_are_printed_in_price_budget_and_certification_details(self):
        """Verify work-item units remain visible in item-level report templates."""
        from pypdf import PdfReader

        reports = (
            ("Cuadro de precios 1 y 2 por capitulos", False),
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
                    write_pdf(self.app, "Cuadro de precios 1 y 2 por capitulos", target)
                spans = self.spans(PdfReader(target).pages[0])
                code = next(span for span in spans if span[0] == "P1")
                summary = next(span for span in spans if span[0] == "Resumen singular")
                description = next(span for span in spans if span[0].startswith("Descripcion singular"))
                from report_layout import LEFT_MARGIN
                self.assertAlmostEqual(code[1], LEFT_MARGIN, delta=0.1)
                self.assertAlmostEqual(description[1] - code[1], 57 if certification else 65, delta=0.1)
                self.assertIn("Bold", summary[3])
                self.assertNotIn("Bold", description[3])
                self.assertLess(description[2], summary[2])

    def test_numeric_columns_share_right_edge_and_economic_totals_are_bold(self):
        """Verify numeric columns share right edge and economic totals are bold."""
        from pypdf import PdfReader
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from report_layout import COLUMN_REFERENCE_WIDTH, CONTENT_WIDTH, LEFT_MARGIN, ReportLayout

        target = self.folder / "summary.pdf"
        write_pdf(self.app, "Presupuesto resumen", target)
        spans = self.spans(PdfReader(target).pages[0])
        layout = ReportLayout()
        amount = next(span for span in spans if span[0] == "143,99")
        tax = next(span for span in spans if span[0] == "24,99")
        self.assertIn("Bold", amount[3])
        self.assertNotIn("Bold", tax[3])
        expected_edge = LEFT_MARGIN + CONTENT_WIDTH * 468 / COLUMN_REFERENCE_WIDTH - 3
        self.assertAlmostEqual(amount[1] + stringWidth(amount[0], layout.bold_font, amount[4]),
                               expected_edge, delta=0.1)
        self.assertAlmostEqual(tax[1] + stringWidth(tax[0], layout.regular_font, tax[4]),
                               expected_edge, delta=0.1)
        label = next(span for span in spans if "Gastos generales" in span[0])
        self.assertGreater(label[1], LEFT_MARGIN + CONTENT_WIDTH * 0.45)

    def test_long_project_title_wraps_without_entering_the_body(self):
        """Verify long project title wraps without entering the body."""
        from pypdf import PdfReader

        self.app.project_title = "Proyecto con un nombre largo " * 25
        target = self.folder / "long-title.pdf"
        write_pdf(self.app, "Cuadro de precios 1 y 2 por capitulos", target)
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

    def test_price_list_two_identifies_configured_indirect_costs_separately(self):
        """Verify project overhead is named and quantified instead of hidden in grouped costs."""
        from bc3_gui import Bc3Dataset, Concept
        from pypdf import PdfReader

        dataset = Bc3Dataset.__new__(Bc3Dataset)
        dataset.concepts = {"MAT": Concept("MAT", "kg", "Material", 10, "3")}
        self.app.budget_dataset = dataset
        item = self.app.budget_items[0]
        item.decomposition = [("MAT", 1)]
        for percentage in (3, 7.5, 0):
            item.set_indirect_costs(percentage)
            for extension, writer in (("pdf", write_pdf), ("odt", write_report_odt)):
                with self.subTest(percentage=percentage, format=extension):
                    target = self.folder / f"overhead-{percentage}.{extension}"
                    writer(self.app, "Cuadro de precios 1 y 2 por capitulos", target)
                    if extension == "pdf":
                        text = "\n".join(page.extract_text() for page in PdfReader(target).pages[1:])
                    else:
                        with ZipFile(target) as archive:
                            content = ElementTree.fromstring(archive.read("content.xml"))
                            text = " ".join(content.itertext())
                    if percentage:
                        label = "Costes indirectos (3,00 %)" if percentage == 3 else "Costes indirectos (7,50 %)"
                        self.assertIn(label, text)
                        self.assertIn("0,30" if percentage == 3 else "0,75", text)
                    else:
                        self.assertNotIn("Costes indirectos (", text)
                    self.assertNotIn("Diferencia respecto al precio unitario", text)

    def test_budget_summary_totals_align_with_chapter_amount_column(self):
        """Verify all summary amounts end at the same column boundary in both formats."""
        from odt_reports import _tag
        from pypdf import PdfReader
        from report_layout import COLUMN_REFERENCE_WIDTH, CONTENT_WIDTH, LEFT_MARGIN, ReportLayout
        from reportlab.pdfbase.pdfmetrics import stringWidth

        target = self.folder / "aligned-summary.pdf"
        write_pdf(self.app, "Presupuesto resumen", target)
        spans = self.spans(PdfReader(target).pages[0])
        layout = ReportLayout()
        amounts = []
        for text, x, _, font, size in spans:
            if text in {"100,00", "13,00", "6,00", "19,00", "119,00", "24,99", "143,99"} and (
                x < LEFT_MARGIN + CONTENT_WIDTH * 468 / COLUMN_REFERENCE_WIDTH
            ):
                name = layout.bold_font if "Bold" in font else layout.regular_font
                amounts.append(x + stringWidth(text, name, size))
        self.assertEqual(len(amounts), 8)
        for boundary in amounts[1:]:
            self.assertAlmostEqual(boundary, amounts[0], delta=0.1)

        target = self.folder / "aligned-summary.odt"
        write_report_odt(self.app, "Presupuesto resumen", target)
        with ZipFile(target) as archive:
            content = ElementTree.fromstring(archive.read("content.xml"))
        widths = {
            style.get(_tag("style:name")):
            float(style.find(_tag("style:table-column-properties")).get(
                _tag("style:column-width"),
            )[:-2])
            for style in content.iter(_tag("style:style"))
            if style.get(_tag("style:family")) == "table-column"
        }
        tables = list(content.iter(_tag("table:table")))
        self.assertEqual(len(tables), 8)
        boundaries = []
        for table in tables:
            columns = table.findall(_tag("table:table-column"))
            self.assertEqual(len(columns), 4)
            boundaries.append(sum(
                widths[column.get(_tag("table:style-name"))] for column in columns[:3]
            ))
        for boundary in boundaries[1:]:
            self.assertAlmostEqual(boundary, boundaries[0], places=3)

    def test_certification_headers_totals_and_signatures_in_pdf_and_rendered_odt(self):
        """Check unbroken headings, amount alignment and paired electronic signatures."""
        import os
        import shutil
        import subprocess
        from pypdf import PdfReader
        from report_layout import COLUMN_REFERENCE_WIDTH, CONTENT_WIDTH, LEFT_MARGIN, ReportLayout
        from reportlab.pdfbase.pdfmetrics import stringWidth

        settings = self.app.report_settings
        settings.contractor = "Constructora singular"
        settings.contractor_representative = "Responsable singular"
        settings.role = "Ingeniero municipal"
        for index, name in enumerate(CERTIFICATION_REPORT_NAMES):
            args = (self.app.budget_items, self.app._chapter_title, self.app.project_title,
                    settings, name)
            for extension, writer in (("pdf", write_certification_pdf),
                                      ("odt", write_certification_odt)):
                writer(*args, self.folder / f"cert-check-{index}.{extension}",
                       "2026-10-01", 1, "Oct 26")
        soffice = shutil.which("soffice") or str(
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "LibreOffice" / "program" / "soffice.exe"
        )
        variants = [("PDF", self.folder)]
        if Path(soffice).is_file():
            converted = self.folder / "rendered"
            converted.mkdir()
            subprocess.run(
                [soffice, f"-env:UserInstallation={(self.folder / 'cert-lo-profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(converted),
                 *[str(self.folder / f"cert-check-{index}.odt") for index in range(2)]],
                check=True, capture_output=True, timeout=90,
            )
            variants.append(("ODT", converted))
        layout = ReportLayout(certification=True)
        for variant, folder in variants:
            for index in range(2):
                with self.subTest(format=variant, detail=index):
                    reader = PdfReader(folder / f"cert-check-{index}.pdf")
                    spans = [span for page in reader.pages for span in self.spans(page)]
                    texts = [span[0] for span in spans]
                    expected = ("LONGITUD", "ANCHURA") if index else ("CERTIFICADO",)
                    for word in expected:
                        self.assertTrue(any(word in text.split() for text in texts), word)
                    company = next(span for span in spans if span[0] == settings.contractor)
                    person = next(span for span in spans if span[0] == settings.contractor_representative)
                    administration = next(span for span in spans if span[0] == settings.author)
                    self.assertLess(company[1], LEFT_MARGIN + CONTENT_WIDTH / 2)
                    self.assertGreater(administration[1], LEFT_MARGIN + CONTENT_WIDTH / 2)
                    self.assertGreater(person[2], company[2])
                    signature = [span for span in spans if span[0] == settings.signature]
                    self.assertEqual(len(signature), 2)
                    self.assertTrue(any(span[1] < LEFT_MARGIN + CONTENT_WIDTH / 2 and
                                        span[2] < company[2] for span in signature))
                    if not index:
                        boundaries = []
                        for text, x, _, font, size in spans:
                            if text in {"100,00", "13,00", "6,00", "19,00", "119,00", "0,00",
                                        "24,99", "143,99"} and (
                                x < LEFT_MARGIN + CONTENT_WIDTH * 435 / COLUMN_REFERENCE_WIDTH
                            ):
                                font_name = layout.bold_font if "Bold" in font else layout.regular_font
                                boundaries.append(x + stringWidth(text, font_name, size))
                        self.assertEqual(len(boundaries), 10)
                        for boundary in boundaries:
                            self.assertAlmostEqual(boundary, boundaries[0], delta=0.3)

    def test_deviation_reports_share_template_and_paginate_with_all_rows(self):
        """Export the monthly comparison, pending items, totals and justifications."""
        from dataclasses import replace
        from odt_reports import _tag
        from pypdf import PdfReader

        original = self.app.budget_items[0]
        items = [replace(
            original, code=f"DEV{index:03}", summary=f"Partida desviada {index:03}",
            certifications={"2026-10-01": 120} if index % 2 == 0 else {},
            certification_plans={"2026-10-01": 100},
            certification_justifications={"2026-10-01": f"Motivo <especial> & revision {index:03}"},
        ) for index in range(60)]
        for extension, writer in (("pdf", write_certification_pdf), ("odt", write_certification_odt)):
            target = self.folder / f"deviations.{extension}"
            writer(items, self.app._chapter_title, self.app.project_title,
                   self.app.report_settings, DEVIATION_REPORT_NAME, target,
                   "2026-10-01", 1, "Oct 26", planned_amounts=[120] * len(items))
            if extension == "pdf":
                reader = PdfReader(target)
                self.assertGreater(len(reader.pages), 1)
                text = "\n".join(page.extract_text() for page in reader.pages)
                for page in reader.pages:
                    self.assertIn("INFORME DE DESVIACIONES", page.extract_text())
                    self.assertEqual(len(page.images), 1)
            else:
                with ZipFile(target) as archive:
                    self.assert_odt_styles(archive)
                    content = ElementTree.fromstring(archive.read("content.xml"))
                    self.assertIsNotNone(content.find(f".//{_tag('table:table-header-rows')}"))
                    text = " ".join(content.itertext())
            for expected in ("DEV000", "DEV059", "6.000,00", "3.600,00", "-2.400,00",
                             "Motivo <especial> & revision 059"):
                self.assertIn(expected, text)

    def test_material_group_subtotals_use_chapter_background_in_pdf_and_odt(self):
        """Verify group subtotal rows are colored without coloring normal material rows."""
        from bc3_gui import Bc3Dataset, Concept
        from odt_reports import _tag
        from pypdf import PdfReader

        dataset = Bc3Dataset.__new__(Bc3Dataset)
        dataset.concepts = {
            "AAA1": Concept("AAA1", "kg", "Material uno", 2, "3"),
            "BBB1": Concept("BBB1", "kg", "Material dos", 3, "3"),
        }
        self.app.budget_dataset = dataset
        self.app.budget_items[0].decomposition = [("AAA1", 1), ("BBB1", 1)]
        for extension, writer in (("pdf", write_pdf), ("odt", write_report_odt)):
            target = self.folder / f"group-colors.{extension}"
            writer(self.app, "Materiales", target)
            if extension == "pdf":
                page = PdfReader(target).pages[0]
                text = page.extract_text()
                self.assertIn("Grupo AAA", text)
                self.assertIn("Grupo BBB", text)
                self.assertIn(b".780392 .815686 .87451 rg", page.get_contents().get_data())
            else:
                with ZipFile(target) as archive:
                    content = ElementTree.fromstring(archive.read("content.xml"))
                    for row in content.iter(_tag("table:table-row")):
                        text = " ".join(row.itertext())
                        if "Grupo " in text:
                            self.assertTrue(all(
                                cell.get(_tag("table:style-name")).startswith("ChapterCell")
                                for cell in row
                            ))
                        elif "Material uno" in text or "Material dos" in text:
                            self.assertTrue(all(
                                cell.get(_tag("table:style-name")).startswith("BodyCell")
                                for cell in row
                            ))

    def test_chapters_start_close_to_header_without_overlapping_long_project_titles(self):
        """Verify chapter spacing follows the actual header height, not a large fixed gap."""
        from pypdf import PdfReader

        for project_title in (
            "Proyecto singular",
            "Proyecto con un nombre largo que ocupa varias líneas " * 8,
        ):
            with self.subTest(project_title=project_title):
                self.app.project_title = project_title
                target = self.folder / "chapter-spacing.pdf"
                write_pdf(self.app, "Mediciones", target)
                spans = self.spans(PdfReader(target).pages[0])
                column_heading = next(span for span in spans if span[0] == "RESUMEN")
                chapter = next(span for span in spans if span[0] == "Capitulo singular")
                gap = column_heading[2] - chapter[2]
                self.assertGreaterEqual(gap, 14)
                self.assertLessEqual(gap, 40)

    def test_measurement_report_lists_chapters_dimensions_and_totals_without_prices(self):
        """Verify both formats report measured quantities, not budget or certification costs."""
        from bc3_gui import MeasurementLine, ProjectItem
        from pypdf import PdfReader

        self.assertEqual(REPORT_NAMES[0], "Mediciones")
        self.app.chapter_order = ["02#", "01#"]
        self.app.chapter_titles["02#"] = "Segundo capitulo"
        measured = self.app.budget_items[0]
        measured.measurement_lines = [
            MeasurementLine(
                comment="Zona norte", units=2, length=3.25, width=0.5, height=2, k=1.2,
            ),
            MeasurementLine(comment="Deduccion hueco", units=-1, length=0.8),
            MeasurementLine(comment="Nota sin dimensiones"),
        ]
        measured.recalculate_measurement()
        measured.unit_price = 9876.54
        self.app.budget_items.append(ProjectItem(
            code="MANUAL", chapter="02#", unit="ud", summary="Medicion sin desglose",
            measurement=12.345, unit_price=8765.43,
        ))
        for extension, writer in (("pdf", write_pdf), ("odt", write_report_odt)):
            with self.subTest(format=extension):
                target = self.folder / f"measurements.{extension}"
                writer(self.app, "Mediciones", target)
                if extension == "pdf":
                    reader = PdfReader(target)
                    self.assertEqual(len(reader.pages), 2)
                    text = "\n".join(page.extract_text() for page in reader.pages)
                else:
                    with ZipFile(target) as archive:
                        content = ElementTree.fromstring(archive.read("content.xml"))
                        styles = ElementTree.fromstring(archive.read("styles.xml"))
                        text = " ".join((*content.itertext(), *styles.itertext()))
                        self.assert_odt_styles(archive)
                for expected in (
                    "MEDICIONES", "CAP. 02", "Segundo capitulo", "CAP. 01",
                    "Capitulo singular", "MANUAL", "ud", "m", "Resumen singular",
                    "Descripcion singular para verificar la sangria.",
                    "COMENTARIO", "UDS", "LONGITUD", "ANCHURA", "ALTURA", "PARCIAL",
                    "Zona norte", "Deduccion hueco", "Nota sin dimensiones",
                    "1,200", "7,800", "-0,800", "7,000", "12,345", "MEDICIÓN TOTAL",
                ):
                    self.assertIn(expected, text)
                self.assertLess(text.index("Segundo capitulo"), text.index("Capitulo singular"))
                for forbidden in (
                    "PRECIO", "IMPORTE", "EJECUCIÓN MATERIAL", "9.876,54", "8.765,43",
                    "Sin descomposición", "Importes",
                ):
                    self.assertNotIn(forbidden, text)

    def test_odt_uses_pdf_typography_columns_and_standard_page_margins(self):
        """Verify editable styles reproduce PDF metrics rather than generic defaults."""
        from odt_reports import _tag
        from report_layout import CONTENT_WIDTH, LEFT_MARGIN, PAGE_MARGIN, ReportLayout

        for certification in (False, True):
            with self.subTest(certification=certification):
                target = self.folder / f"metrics-{certification}.odt"
                if certification:
                    write_certification_odt(
                        self.app.budget_items, self.app._chapter_title, self.app._project_title(),
                        self.app.report_settings, CERTIFICATION_REPORT_NAMES[1], target,
                        "2026-10-01", 1, "Oct 26",
                    )
                else:
                    write_report_odt(self.app, "Presupuesto mediciones", target)
                layout = ReportLayout(certification=certification)
                with ZipFile(target) as archive:
                    styles = ElementTree.fromstring(archive.read("styles.xml"))
                    content = ElementTree.fromstring(archive.read("content.xml"))
                    declarations = {
                        element.get(_tag("style:name")): element
                        for element in styles.iter(_tag("style:style"))
                    }
                    for name, pdf in (
                        ("ReportTitle", layout.title_style),
                        ("ProjectTitle", layout.project_style),
                        ("ItemDescription", layout.description_style),
                        ("TableText", layout.normal),
                        ("TableItemTitle", layout.item_summary),
                    ):
                        style = declarations[name]
                        text = style.find(_tag("style:text-properties"))
                        paragraph = style.find(_tag("style:paragraph-properties"))
                        self.assertEqual(text.get(_tag("fo:font-size")), f"{pdf.fontSize}pt")
                        self.assertEqual(paragraph.get(_tag("fo:line-height")), f"{pdf.leading}pt")
                        self.assertEqual(paragraph.get(_tag("fo:margin-left")), f"{pdf.leftIndent}pt")
                    page = styles.find(f".//{_tag('style:page-layout-properties')}")
                    self.assertAlmostEqual(float(page.get(_tag("fo:margin-left"))[:-2]), PAGE_MARGIN)
                    self.assertAlmostEqual(float(page.get(_tag("fo:margin-right"))[:-2]), PAGE_MARGIN)
                    self.assertEqual(page.get(_tag("fo:margin-top")), "2.5cm")
                    self.assertEqual(page.get(_tag("fo:margin-bottom")), "2.5cm")
                    widths = [
                        float(element.get(_tag("style:width"))[:-2])
                        for element in content.iter(_tag("style:table-properties"))
                    ]
                    self.assertTrue(widths)
                    for width in widths:
                        self.assertAlmostEqual(width, CONTENT_WIDTH, places=3)
                    logo = styles.find(f".//{_tag('draw:frame')}")
                    self.assertEqual(logo.get(_tag("text:anchor-type")), "paragraph")
                    self.assertNotIn(_tag("text:anchor-page-number"), logo.attrib)
                    self.assertEqual(logo.get(_tag("svg:x")), "0pt")
                    self.assertEqual(logo.get(_tag("svg:y")), "0pt")
                    self.assert_odt_styles(archive)

    def test_logo_size_date_and_measurement_alignment_on_continuation_pages(self):
        """Check actual multipage output repeats a correctly sized header and aligned totals."""
        import os
        import shutil
        import subprocess
        from datetime import date
        from math import hypot
        from bc3_gui import MeasurementLine, ProjectItem
        from odt_reports import _tag
        from pypdf import PdfReader
        from report_layout import DEFAULT_TEMPLATE_PATH, _template_length_to_points

        template = self.folder / "custom-logo.odt"
        with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as target:
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename == "styles.xml":
                    root = ElementTree.fromstring(data)
                    frame = root.find(f".//{_tag('style:header')}//{_tag('draw:frame')}")
                    frame.set(_tag("svg:width"), "4cm")
                    frame.set(_tag("svg:height"), "2.4cm")
                    data = ElementTree.tostring(root, encoding="utf-8")
                target.writestr(entry, data)
        self.app.report_settings.template_path = str(template)
        self.app.budget_items = [
            ProjectItem(
                code=f"P{index}", chapter="01#", unit="m", summary=f"Resumen numero {index}",
                text="Descripcion de la partida para comprobar la continuidad de las paginas.",
                measurement=7, unit_price=10,
                measurement_lines=[
                    MeasurementLine(comment="Medicion positiva", units=2, length=3.5),
                ],
            )
            for index in range(24)
        ]
        reference = self.folder / "multipage-reference.pdf"
        editable = self.folder / "multipage.odt"
        write_pdf(self.app, "Mediciones", reference)
        write_report_odt(self.app, "Mediciones", editable)
        with ZipFile(editable) as archive:
            styles = ElementTree.fromstring(archive.read("styles.xml"))
            for frame in styles.findall(f".//{_tag('style:header')}//{_tag('draw:frame')}"):
                self.assertAlmostEqual(_template_length_to_points(frame.get(_tag("svg:width"))),
                                       _template_length_to_points("4cm"), delta=0.01)
                self.assertAlmostEqual(_template_length_to_points(frame.get(_tag("svg:height"))),
                                       _template_length_to_points("2.4cm"), delta=0.01)

        soffice = shutil.which("soffice") or str(
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "LibreOffice" / "program" / "soffice.exe"
        )
        documents = [reference]
        if Path(soffice).is_file():
            subprocess.run(
                [soffice, f"-env:UserInstallation={(self.folder / 'logo-profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(self.folder), str(editable)],
                check=True, capture_output=True, timeout=90,
            )
            documents.append(editable.with_suffix(".pdf"))
        for document in documents:
            with self.subTest(format=document.name):
                reader = PdfReader(document)
                self.assertGreater(len(reader.pages), 1)
                for page in reader.pages:
                    self.assertEqual(len(page.images), 1)
                    spans = self.spans(page)
                    text = page.extract_text()
                    self.assertIn(f"Proyecto singular · {date.today():%d/%m/%Y}", text)
                    dimensions = []

                    def image_size(operator, operands, cm, tm):
                        """Collect the physical dimensions of images painted on the page."""
                        if operator == b"Do":
                            dimensions.append((hypot(cm[0], cm[1]), hypot(cm[2], cm[3])))

                    page.extract_text(visitor_operand_before=image_size)
                    self.assertTrue(any(
                        abs(width - _template_length_to_points("4cm")) < 0.1 and
                        abs(height - _template_length_to_points("2.4cm")) < 0.1
                        for width, height in dimensions
                    ), dimensions)
                    partials = [span for span in spans if span[0] == "7,000"]
                    self.assertTrue(partials)
                    self.assertLess(max(span[1] for span in partials) - min(span[1] for span in partials), 0.2)
                    for total in (span for span in spans if span[0] == "MEDICIÓN TOTAL"):
                        values = [span for span in partials if abs(span[2] - total[2]) < 1]
                        self.assertEqual(len(values), 1)
                        self.assertTrue(any(
                            span[0] == "m" and span[1] > values[0][1] and
                            abs(span[2] - total[2]) < 1 for span in spans
                        ))

    def test_rendered_odt_stays_close_to_pdf_positions_sizes_and_pagination(self):
        """Compare real Writer output against PDF layouts when LibreOffice is installed."""
        import os
        import shutil
        import subprocess
        from pypdf import PdfReader
        from report_layout import PAGE_MARGIN, PAGE_WIDTH

        soffice = shutil.which("soffice") or str(
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "LibreOffice" / "program" / "soffice.exe"
        )
        if not Path(soffice).is_file():
            self.skipTest("LibreOffice no está instalado para comprobar el ODT renderizado.")
        cases = [
            ("summary", "Presupuesto resumen"),
            ("details", "Presupuesto mediciones"),
            ("prices", "Cuadro de precios 1 y 2 por capitulos"),
            ("measurements", "Mediciones"),
            ("certification", CERTIFICATION_REPORT_NAMES[1]),
        ]
        for key, name in cases:
            if key == "certification":
                args = (
                    self.app.budget_items, self.app._chapter_title, self.app._project_title(),
                    self.app.report_settings, name,
                )
                write_certification_pdf(*args, self.folder / f"{key}-reference.pdf", "2026-10-01", 1, "Oct 26")
                write_certification_odt(*args, self.folder / f"{key}.odt", "2026-10-01", 1, "Oct 26")
            else:
                write_pdf(self.app, name, self.folder / f"{key}-reference.pdf")
                write_report_odt(self.app, name, self.folder / f"{key}.odt")
        subprocess.run(
            [soffice, f"-env:UserInstallation={(self.folder / 'lo-profile').as_uri()}",
             "--headless", "--convert-to", "pdf", "--outdir", str(self.folder),
             *[str(self.folder / f"{key}.odt") for key, _ in cases]],
            check=True, capture_output=True, timeout=90,
        )
        for key, _ in cases:
            with self.subTest(report=key):
                reference = PdfReader(self.folder / f"{key}-reference.pdf")
                rendered = PdfReader(self.folder / f"{key}.pdf")
                self.assertEqual(len(rendered.pages), len(reference.pages))
                for pdf_page, odt_page in zip(reference.pages, rendered.pages):
                    self.assertEqual(len(odt_page.images), len(pdf_page.images))
                    actual = self.spans(odt_page)
                    for text, x, y, _, size in self.spans(pdf_page):
                        if text not in {
                            "Resumen singular", "Capitulo singular", "Autoria singular",
                            "Descripcion singular para verificar la sangria.",
                        } and not text.startswith("Proyecto singular"):
                            continue
                        matches = [span for span in actual if span[0] == text]
                        self.assertTrue(matches, text)
                        closest = min(matches, key=lambda span: abs(span[2] - y))
                        self.assertAlmostEqual(closest[1], x, delta=3, msg=text)
                        self.assertAlmostEqual(closest[2], y, delta=3, msg=text)
                        self.assertAlmostEqual(closest[4], size, delta=0.1, msg=text)
                    for _, x, y, _, _ in actual:
                        self.assertGreaterEqual(x, PAGE_MARGIN - 1)
                        self.assertLessEqual(x, PAGE_WIDTH - PAGE_MARGIN + 1)
                        self.assertGreaterEqual(y, PAGE_MARGIN - 1)
                        self.assertLessEqual(y, float(odt_page.mediabox.height) - PAGE_MARGIN + 1)

    def test_template_header_and_footer_borders_are_preserved_in_odt_and_pdf(self):
        """Verify inherited template rules retain their width, color and placement."""
        from report_layout import DEFAULT_TEMPLATE_PATH, _read_report_template_borders
        from pypdf import PdfReader

        style_ns = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
        text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        fo_ns = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
        template = self.folder / "borders.odt"
        with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as target:
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename == "styles.xml":
                    root = ElementTree.fromstring(data)
                    styles = root.find("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}styles")
                    for section, edge, border in (
                        ("header", "bottom", "1.5pt dashed #123456"),
                        ("footer", "top", "2pt solid #654321"),
                    ):
                        parent = ElementTree.SubElement(styles, f"{{{style_ns}}}style", {
                            f"{{{style_ns}}}name": f"Test{section}Base",
                            f"{{{style_ns}}}family": "paragraph",
                        })
                        ElementTree.SubElement(parent, f"{{{style_ns}}}paragraph-properties", {
                            f"{{{fo_ns}}}border-{edge}": border,
                        })
                        ElementTree.SubElement(styles, f"{{{style_ns}}}style", {
                            f"{{{style_ns}}}name": f"Test{section}",
                            f"{{{style_ns}}}family": "paragraph",
                            f"{{{style_ns}}}parent-style-name": f"Test{section}Base",
                        })
                        paragraphs = root.findall(f".//{{{style_ns}}}{section}/{{{text_ns}}}p")
                        for paragraph in paragraphs:
                            paragraph.set(f"{{{text_ns}}}style-name", f"Test{section}")
                    data = ElementTree.tostring(root, encoding="utf-8")
                target.writestr(entry, data)
        self.app.report_settings.template_path = str(template)
        self.assertEqual(_read_report_template_borders(str(template)), {
            "header": {"bottom": "1.5pt dashed #123456"},
            "footer": {"top": "2pt solid #654321"},
        })
        odt = self.folder / "borders.odt.report.odt"
        write_report_odt(self.app, "Presupuesto resumen", odt)
        with ZipFile(odt) as archive:
            styles = ElementTree.fromstring(archive.read("styles.xml"))
            for name, edge, expected in (
                ("ReportHeader", "bottom", "1.5pt dashed #123456"),
                ("FooterText", "top", "2pt solid #654321"),
            ):
                style = next(
                    element for element in styles.iter(f"{{{style_ns}}}style")
                    if element.get(f"{{{style_ns}}}name") == name
                )
                properties = style.find(f"{{{style_ns}}}paragraph-properties")
                self.assertEqual(properties.get(f"{{{fo_ns}}}border-{edge}"), expected)
        pdf = self.folder / "borders.pdf"
        write_pdf(self.app, "Presupuesto resumen", pdf)
        for page in PdfReader(pdf).pages:
            commands = page.get_contents().get_data()
            self.assertIn(b"1.5 w", commands)
            self.assertIn(b"2 w", commands)
            self.assertIn(b"[4.5 3] 0 d", commands)
            self.assertIn(b".070588 .203922 .337255 RG", commands)
            self.assertIn(b".396078 .262745 .129412 RG", commands)

    def test_template_without_borders_does_not_add_header_or_footer_rules(self):
        """Verify templates without paragraph borders retain their original appearance."""
        from report_layout import DEFAULT_TEMPLATE_PATH, _read_report_template_borders

        fo_ns = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
        template = self.folder / "no-borders.odt"
        with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as target:
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename == "styles.xml":
                    root = ElementTree.fromstring(data)
                    for element in root.iter():
                        for attribute in list(element.attrib):
                            if attribute.startswith(f"{{{fo_ns}}}border"):
                                element.set(attribute, "none")
                    data = ElementTree.tostring(root, encoding="utf-8")
                target.writestr(entry, data)
        self.assertEqual(
            _read_report_template_borders(str(template)), {"header": {}, "footer": {}},
        )
        self.app.report_settings.template_path = str(template)
        target = self.folder / "without-lines.odt"
        write_report_odt(self.app, "Presupuesto resumen", target)
        with ZipFile(target) as archive:
            styles = ElementTree.fromstring(archive.read("styles.xml"))
            style_ns = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
            for element in styles.iter(f"{{{style_ns}}}style"):
                if element.get(f"{{{style_ns}}}name") in {"ReportHeader", "FooterText"}:
                    self.assertFalse(any(
                        attribute.startswith(f"{{{fo_ns}}}border")
                        for child in element for attribute in child.attrib
                    ))


if __name__ == "__main__":
    unittest.main()
