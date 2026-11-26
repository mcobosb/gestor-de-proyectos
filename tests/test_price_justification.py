from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import re
import shutil
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import bc3_gui
from bc3_gui import Bc3Dataset, Concept, ProjectItem
from budget_project import (
    budget_concept_type, chapter_title_for, component_dataset_for, project_title_for, report_components,
)
from budget_reports import ReportSettings, _collect_report_resources
from tools import price_justification
from tools.price_justification.document import (
    ANNUAL_HOURS_REFERENCES, CALENDAR_REFERENCES, CONSTRUCTION_AGREEMENT_REFERENCES,
    CONTRIBUTION_BASE_REFERENCES, CONTRIBUTION_DETAIL_REFERENCES,
    CONTRIBUTION_RATE_REFERENCES, LISTINGS, PENSION_CONTRIBUTION_REFERENCES,
    SALARY_REVISION_REFERENCES, WAGE_REFERENCES, WORK_CONDITION_REFERENCES, narrative,
    restore_state, validate_state, write_document,
)
from tools.price_justification.templates import (
    DOCUMENTS, FORMULA_LEVEL, LIBRARY_PATH, Equation, NarrativeTable, document_blocks,
    general_variables, load_documents,
)

BudgetApp = getattr(bc3_gui, "BudgetApp", None)
before_project_save = getattr(price_justification, "before_project_save", None)
open_tool = getattr(price_justification, "open_tool", None)


class ReportProject:
    """Usa los mismos métodos de consulta del proyecto que la ventana Qt."""

    _project_title = project_title_for
    _chapter_title = chapter_title_for
    _component_dataset = component_dataset_for
    _budget_concept_type = budget_concept_type
    _report_components = report_components


def narrative_text(app, state):
    """Reúne los textos de los párrafos y todas las celdas del anejo."""
    return "\n".join(
        "\n".join((value.title, *(part for row in value.rows for part in row)))
        if isinstance(value, NarrativeTable) else value.source if isinstance(value, Equation) else value
        for _, value in narrative(app, state)
    )


class PriceDocumentTests(unittest.TestCase):
    def test_inline_regulatory_links_in_pdf_and_odt(self):
        """Vincula las citas sin alterar los enlaces personalizados existentes."""
        from odt_reports import _tag
        from tools.price_justification.document import _odt_inline, _pdf_inline

        text = ("Ley 31/1995, UNE-EN 12350-2:2020 y "
                "[texto propio](https://example.org/norma).")
        root = ET.Element(_tag("text:p"))
        _odt_inline(root, text)
        urls = {node.get(_tag("xlink:href")) for node in root.iter(_tag("text:a"))}
        pdf = _pdf_inline(text)
        for url in ("https://www.boe.es/eli/es/l/1995/11/08/31/con",
                    "https://www.une.org/encuentra-tu-norma/busca-tu-norma/norma/?c=N0063378",
                    "https://example.org/norma"):
            self.assertIn(url, urls)
            self.assertIn(url, pdf)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.app = ReportProject()
        dataset = Bc3Dataset.__new__(Bc3Dataset)
        dataset.concepts = {
            "MO1": Concept("MO1", "h", "Operario singular", 17.25, "1"),
            "MQ1": Concept("MQ1", "h", "Compactador singular", 45, "2"),
            "MT1": Concept("MT1", "m3", "Zahorra singular", 23.50, "3"),
            "AX1": Concept(
                "AX1", "m3", "Mezcla auxiliar singular", 105, "0",
                children=[("MO1", 2), ("MT1", 3)],
            ),
        }
        self.app.budget_dataset = dataset
        self.app.reference_bases = []
        self.app.budget_items = [ProjectItem(
            "P1", "01#", "ud", "Partida singular", 4, 135.15, source="BC3",
            decomposition=[("AX1", 1), ("MQ1", 0.5)], indirect_costs=6,
        )]
        self.app.chapter_order = ["01#"]
        self.app.chapter_titles = {"01#": "Capítulo singular"}
        self.app.project_title = "Proyecto singular"
        self.app.report_settings = ReportSettings(
            author="Autor único del anejo", indirect_costs=6,
        )

    def test_caption_base_is_narrow_eight_and_bold_in_both_formats(self):
        """Corrige el caption propio de nueve puntos sin alterar las tablas nativas."""
        from pypdf import PdfReader
        from report_layout import ReportLayout
        from reportlab.pdfbase import pdfmetrics
        from odt_reports import _tag

        font = pdfmetrics.getFont(ReportLayout().bold_font).face.name
        expected_font = font.decode() if isinstance(font, bytes) else font
        table = NarrativeTable("CAPTION_MARCA", (("Campo", "Valor"),))
        for extension in ("odt", "pdf"):
            with self.subTest(format=extension):
                target = self.folder / f"caption.{extension}"
                with patch("tools.price_justification.document.narrative", return_value=[(0, table)]):
                    write_document(self.app, target, {})
                if extension == "odt":
                    with ZipFile(target) as archive:
                        content = ET.fromstring(archive.read("content.xml"))
                    style = next(
                        node for node in content.iter(_tag("style:style"))
                        if node.get(_tag("style:name")) == "PricesCaption"
                    )
                    props = style.find(_tag("style:text-properties"))
                    self.assertEqual(props.get(_tag("fo:font-family")), "Arial Narrow")
                    self.assertEqual(props.get(_tag("fo:font-size")), "8pt")
                    self.assertEqual(props.get(_tag("fo:font-weight")), "bold")
                    text = " ".join(content.itertext())
                else:
                    runs = []
                    pages = PdfReader(target).pages
                    text = " ".join(page.extract_text() or "" for page in pages)
                    for page in pages:
                        def collect(text, cm, tm, font, size):
                            """Comprueba la fuente incrustada y su tamaño efectivo."""
                            if "CAPTION_MARCA" in text:
                                runs.append((font["/BaseFont"], size))
                        page.extract_text(visitor_text=collect)
                    self.assertTrue(runs)
                    for name, size in runs:
                        self.assertTrue(name.endswith(expected_font))
                        self.assertAlmostEqual(size, 8, places=1)
                self.assertEqual(re.findall(r"Tabla\s+\d+\.", text), ["Tabla 1."])
                self.assertIn("CAPTION_MARCA", text)

    def test_listing_headings_and_blue_rows_preserve_template_and_table_layout(self):
        """Conserva títulos de plantilla y resalta solo las filas solicitadas."""
        from odt_reports import _tag
        from tools._shared.document_layout import TABLE_HEADER_BACKGROUND

        target = self.folder / "listados.odt"
        with patch("tools.price_justification.document.narrative", return_value=[]):
            write_document(self.app, target, {})
        with ZipFile(target) as archive:
            content = ET.fromstring(archive.read("content.xml"))
            styles = ET.fromstring(archive.read("styles.xml"))
        declared = {
            style.get(_tag("style:name")): style
            for root in (styles, content) for style in root.iter(_tag("style:style"))
        }
        parent = declared["PricesListingHeading"].get(_tag("style:parent-style-name"))
        self.assertEqual(parent, "TemplateHeading")
        heading_properties = declared["PricesListingHeading"].find(_tag("style:text-properties"))
        self.assertIsNotNone(heading_properties)
        self.assertEqual(heading_properties.get(_tag("fo:font-weight")), "bold")
        self.assertIsNotNone(styles.find(
            f".//{_tag('office:styles')}/{_tag('style:style')}[@{_tag('style:name')}='{parent}']",
        ))
        for title, _ in LISTINGS:
            heading = next(
                node for node in content.iter(_tag("text:h"))
                if "".join(node.itertext()) == title
            )
            self.assertEqual(heading.get(_tag("text:style-name")), "PricesListingHeading")
        for blue, original in (
            ("BlueHeaderCell", "TintedHeaderCell"),
            ("BlueHeaderCellLast", "TintedHeaderCellLast"),
            ("BlueChapterCell", "ChapterCell"),
            ("BlueChapterCellLast", "ChapterCellLast"),
        ):
            props = dict(declared[blue].find(_tag("style:table-cell-properties")).attrib)
            self.assertEqual(props.pop(_tag("fo:background-color")), TABLE_HEADER_BACKGROUND)
            original_props = dict(declared[original].find(_tag("style:table-cell-properties")).attrib)
            original_props.pop(_tag("fo:background-color"))
            self.assertEqual(props, original_props)
            self.assertTrue(content.findall(
                f".//{_tag('table:table-cell')}[@{_tag('table:style-name')}='{blue}']",
            ))
        for name in ("BlueTableHeader", "BlueTableHeaderRight", "BlueChapterTitle"):
            self.assertEqual(
                declared[name].find(_tag("style:text-properties")).get(_tag("fo:color")),
                "#FFFFFF",
            )

    def test_listing_heading_bold_respects_project_typography(self):
        """Mantiene la negrita predeterminada sin anular la configuración del proyecto."""
        from odt_reports import _tag

        for bold in (True, False):
            with self.subTest(bold=bold):
                self.app.report_settings.text_styles = {"heading1": {"bold": bold}}
                target = self.folder / "negrita.odt"
                with patch("tools.price_justification.document.narrative", return_value=[]):
                    write_document(self.app, target, {})
                with ZipFile(target) as archive:
                    content = ET.fromstring(archive.read("content.xml"))
                    styles = ET.fromstring(archive.read("styles.xml"))
                style = next(
                    node for root in (content, styles) for node in root.iter(_tag("style:style"))
                    if node.get(_tag("style:name")) == "PricesListingHeading"
                )
                self.assertEqual(style.get(_tag("style:parent-style-name")), "TemplateHeading")
                self.assertEqual(
                    style.find(_tag("style:text-properties")).get(_tag("fo:font-weight")),
                    "bold" if bold else "normal",
                )

    def test_embedded_pdf_listings_use_blue_headers_and_chapter_bands(self):
        """Aplica el mismo azul en PDF sin recolorear los informes independientes."""
        from reportlab.lib import colors
        from reportlab.platypus import Table
        from budget_reports import build_pdf_report
        from report_layout import ReportLayout
        from tools._shared.document_layout import TABLE_HEADER_BACKGROUND

        blue = colors.HexColor(TABLE_HEADER_BACKGROUND)
        for report in ("Precios elementales", "Descompuestos"):
            for embedded in (False, True):
                with self.subTest(report=report, embedded=embedded):
                    story, _ = build_pdf_report(self.app, report, ReportLayout(), embedded=embedded)
                    tables = [block for block in story if isinstance(block, Table)]
                    self.assertTrue(tables)
                    self.assertEqual(any(
                        command[0] == "BACKGROUND" and command[3] == blue
                        for table in tables for command in table._bkgrndcmds
                    ), embedded)

    def test_odt_uses_project_typography_and_shared_list_layout(self):
        """Respeta la configuración para texto, títulos, tablas y viñetas."""
        from odt_reports import _tag
        from tools._shared.document_layout import LIST_INDENT, template_paragraph_styles
        from odt_reports import _new_report

        styles = {
            "title": {"font": "Courier New", "size": 20},
            "heading1": {"font": "Arial", "size": 18},
            "body": {"font": "Times New Roman", "size": 14},
            "list": {"font": "Courier New", "size": 13},
            "table": {"font": "Arial", "size": 11},
            "table_header": {"font": "Arial", "size": 12},
            "emphasis": {"font": "Arial", "size": 14},
            "caption": {"font": "Times New Roman", "size": 9},
            "index": {"font": "Arial", "size": 10},
        }
        self.app.report_settings.text_styles = styles
        template = _new_report("PRUEBA", "Proyecto singular", self.app.report_settings)
        template_styles = template_paragraph_styles(template.template_header)
        table = NarrativeTable(
            "Datos", (("Concepto", "• **Primera** [norma](https://example.org)\n- Segunda"),
                      ("Total", "12,34")),
            bands=(1,),
        )
        blocks = [
            (1, "Título principal"), (0, "Texto normal."),
            (-1, "• Viñeta exterior"), (-2, "- Viñeta anidada"), (0, table),
        ]
        target = self.folder / "estilos.odt"
        with patch("tools.price_justification.document.narrative", return_value=blocks):
            write_document(self.app, target, {})
        with ZipFile(target) as archive:
            content = ET.fromstring(archive.read("content.xml"))
            style_root = ET.fromstring(archive.read("styles.xml"))
        declared = {
            style.get(_tag("style:name")): style
            for root in (content, style_root) for style in root.iter(_tag("style:style"))
        }
        for name, role in (
            ("PricesDocumentTitle", "title"), ("PricesHeading1", "heading1"),
            ("PricesBody", "body"), ("PricesList1", "list"), ("PricesTableBulletText", "list"),
            ("PricesTableText", "table"), ("PricesTableHeader", "table_header"),
            ("PricesTableTotal", "emphasis"), ("PricesCaption", "caption"),
            ("PPTPContents1", "index"),
        ):
            with self.subTest(style=name):
                props = declared[name].find(_tag("style:text-properties"))
                self.assertEqual(props.get(_tag("fo:font-family")), styles[role]["font"])
                self.assertEqual(props.get(_tag("fo:font-size")), f'{styles[role]["size"]}pt')
        self.assertEqual(
            declared["PricesBody"].get(_tag("style:parent-style-name")), template_styles["body"],
        )
        self.assertEqual(
            declared["PricesHeading1"].get(_tag("style:parent-style-name")),
            template_styles["heading1"],
        )
        self.assertEqual(
            declared["PricesBody"].find(_tag("style:paragraph-properties")).get(_tag("fo:text-align")),
            "justify",
        )
        for name, indent in (
            ("PricesList1", LIST_INDENT), ("PricesList2", LIST_INDENT + 12),
        ):
            props = declared[name].find(_tag("style:paragraph-properties"))
            self.assertEqual(props.get(_tag("fo:margin-left")), f"{indent}pt")
            self.assertEqual(props.get(_tag("fo:text-indent")), f"-{LIST_INDENT}pt")
            self.assertEqual(
                props.find(f"{_tag('style:tab-stops')}/{_tag('style:tab-stop')}").get(
                    _tag("style:position")
                ), f"{indent}pt",
            )
        list_style = next(
            style for style in content.iter(_tag("text:list-style"))
            if style.get(_tag("style:name")) == "PricesTableBullets"
        )
        self.assertEqual(
            list_style.find(f".//{_tag('style:list-level-properties')}").get(
                _tag("text:list-level-position-and-space-mode")
            ), "label-alignment",
        )
        alignment = list_style.find(
            f".//{_tag('style:list-level-properties')}/{_tag('style:list-level-label-alignment')}"
        )
        self.assertEqual(alignment.get(_tag("text:label-followed-by")), "listtab")
        self.assertEqual(alignment.get(_tag("text:list-tab-stop-position")), f"{LIST_INDENT}pt")
        self.assertEqual(alignment.get(_tag("fo:margin-left")), f"{LIST_INDENT}pt")
        self.assertEqual(alignment.get(_tag("fo:text-indent")), f"-{LIST_INDENT}pt")
        cell_items = [
            p for p in content.iter(_tag("text:p"))
            if p.get(_tag("text:style-name")) == "PricesTableBulletText"
        ]
        self.assertEqual(len(cell_items), 2)
        self.assertIn("Primera norma", "".join(cell_items[0].itertext()))
        self.assertEqual(
            cell_items[0].find(f".//{_tag('text:a')}").get(_tag("xlink:href")),
            "https://example.org",
        )

    def test_elementals_include_all_three_kinds_without_changing_materials(self):
        """Las cantidades anidadas se conservan y Materiales continúa limitado al tipo 3."""
        _, materials = _collect_report_resources(self.app)
        self.assertEqual({key[0] for key in materials}, {"MT1"})
        auxiliaries, elements = _collect_report_resources(self.app, elementary=True)
        self.assertEqual({key[0] for key in auxiliaries}, {"AX1"})
        self.assertEqual(
            {key[0]: value[1] for key, value in elements.items()},
            {"MO1": Decimal("8"), "MQ1": Decimal("2.0"), "MT1": Decimal("12")},
        )

    def test_data_validation_and_public_private_narrative(self):
        """Las referencias dependen del contrato y los datos incompletos no se presentan como verificados."""
        for data in ([], {"contract_type": "other"}, {"annual_hours": 1700}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                restore_state(data)
        for data in (
            {"annual_hours": "NaN"}, {"annual_hours": "-1"},
            {"annual_hours": "abc"}, {"reference_date": "2026-02-31"},
            {"reference_date": "20261117"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_state(data)
        state = validate_state({
            "annual_hours": "1736,5", "reference_date": "2026-11-17",
            "territory": "Ámbito de prueba", "contract_type": "private",
        })
        private = narrative_text(self.app, state)
        self.assertNotIn("Ley 9/2017", private)
        self.assertIn("contratación privada", private)
        self.assertIn("1736,5 horas", private)
        self.assertIn("20.298,22 €/año", private)
        public = narrative_text(self.app, {})
        self.assertIn("Ley 9/2017", public)
        self.assertIn("6,00 %", public)
        self.assertNotIn("2008", public)
        self.assertNotIn("2009", public)

    def test_parentheses_span_nested_fractions_and_keep_the_exponent(self):
        """Comprueba la altura en PDF y los delimitadores editables en ODT."""
        from odt_reports import _tag
        from tools.price_justification.formula import PdfFormula, odt_formula

        source = r"(1 + \frac{A + (\frac{k}{100})}{H_a})^T + (M + C)"
        formula = PdfFormula(source)
        fences = sorted(formula.box.fences)
        self.assertEqual(len(fences), 4)
        outer_left, inner_left, inner_right, outer_right = fences
        for left, right in ((outer_left, outer_right), (inner_left, inner_right)):
            self.assertEqual(left[1:3], right[1:3])
            self.assertLess(left[1], 0 if left is outer_left else 12)
            self.assertGreater(left[2] - left[1], 16)
        self.assertLess(outer_left[1], inner_left[1])
        self.assertGreater(outer_left[2], inner_left[2])
        self.assertTrue(any("(M + C)" in text for _, _, _, text in formula.box.text))

        paragraph = ET.Element(_tag("text:p"))
        odt_formula(paragraph, source)
        ns = {"math": "http://www.w3.org/1998/Math/MathML"}
        self.assertEqual(len(paragraph.findall(".//math:mo[@stretchy='true']", ns)), 4)
        power = paragraph.find(".//math:msup", ns)
        self.assertIsNotNone(power)
        self.assertIsNotNone(power.find("./math:mrow/math:mrow/math:mfrac", ns))
        self.assertEqual("".join(power[1].itertext()), "T")

    def test_base_documents_include_the_complete_method_and_formulas(self):
        """El anejo contiene el método aportado, no solo notas o avisos sobre fórmulas pendientes."""
        self.assertEqual(len(load_documents(LIBRARY_PATH)), 8)
        blocks = narrative(self.app, {})
        formulas = [text.source if isinstance(text, Equation) else text
                    for level, text in blocks if level == FORMULA_LEVEL]
        self.assertIn(r"P_n = (1 + \frac{K}{100}) C_n", formulas)
        self.assertIn(r"C = \frac{A + B + (\frac{k}{100}) A}{H_a}", formulas)
        self.assertIn(r"T = \frac{H_ut}{H_ua}", formulas)
        self.assertIn(r"C_hm = \frac{100 + (M + C)}{H_ut} + \frac{i_m + S}{H_ua}", formulas)
        self.assertTrue(all("/" not in formula for formula in formulas))
        text = narrative_text(self.app, {})
        self.assertIn("Reposición del capital.", text)
        self.assertIn("Reparaciones generales y conservación ordinaria.", text)
        self.assertIn("Consumos secundarios:", text)
        self.assertNotIn("fórmulas no están implementadas", text)
        self.assertNotIn("Texto base adaptado del ejemplo", text)

    def test_general_configuration_overrides_legacy_copies_and_updates_on_each_export(self):
        """Lee todos los emplazamientos y los datos actuales sin conservar valores territoriales obsoletos."""
        settings = self.app.report_settings
        settings.municipality_codes = ["140386", "280796"]
        settings.organization = "Entidad configurada"
        settings.expediente = "EXP-PRUEBA"
        state = {"territory": "Territorio obsoleto", "titulo_proyecto": "Título obsoleto"}
        first = narrative_text(self.app, state)
        for value in ("Lucena", "Córdoba", "Andalucía", "Madrid", "Entidad configurada", "EXP-PRUEBA"):
            self.assertIn(value, first)
        self.assertNotIn("obsoleto", first)
        settings.municipality_codes = ["280796"]
        settings.organization = "Entidad actualizada"
        settings.indirect_costs = 8.25
        second = narrative_text(self.app, state)
        self.assertIn("Entidad actualizada", second)
        self.assertIn("8,25 %", second)
        self.assertNotIn("Córdoba", second)
        settings.municipality_codes = []
        settings.location = "Localidad configurada"
        self.assertEqual(general_variables(self.app)["emplazamiento"], "Localidad configurada")
        self.assertEqual(general_variables(self.app)["provincias"], "")
        self.assertNotIn("territory", restore_state(state))

    def test_project_table_omits_missing_fields_and_uses_live_configuration(self):
        """No imprime expediente ni fecha ficticios y refleja los cambios del proyecto."""
        tables = [block for _, block in narrative(self.app, {}) if isinstance(block, NarrativeTable)]
        self.assertEqual(len(tables), 5)
        self.assertEqual(tables[0].rows, (("Proyecto", "Proyecto singular"),))
        self.app.report_settings.expediente = "EXP-42"
        project = next(block for _, block in narrative(
            self.app, {"reference_date": "2026-11-19"},
        ) if isinstance(block, NarrativeTable))
        self.assertEqual(dict(project.rows)["Expediente"], "EXP-42")
        self.assertEqual(dict(project.rows)["Fecha de referencia de los precios"], "2026-11-19")

    def test_indirect_costs_use_direct_total_cap_and_leave_budget_unchanged(self):
        """Cubre importe, base sin recargo, límite, discrepancia y presupuesto vacío."""
        from tools.price_justification.indirect import calculate, default_rows, equivalent_interest

        item = self.app.budget_items[0]
        item.set_indirect_costs(6, direct_price=2500)
        row = default_rows()[0] | {"quantity": 2, "monthly": 300, "months": 2, "dedication": 10}
        costs = calculate(self.app, [row])
        self.assertEqual((costs.direct, costs.total, costs.raw, costs.rate),
                         (10000, 120, Decimal("1.2"), Decimal("2.2")))
        row["monthly"] = 600
        data = {"indirect_rows": [row]}
        costs = calculate(self.app, validate_state(data)["indirect_rows"])
        self.assertEqual((costs.raw, costs.adopted, costs.rate), (Decimal("2.4"), 2, 3))
        self.assertTrue(costs.differs)
        text = narrative_text(self.app, data)
        self.assertIn("supera el 2 %", text)
        self.assertIn("siguiendo la ecuación (3)", text)
        self.assertIn("presupuesto conserva su porcentaje configurado de 6,00 %", text)
        self.assertEqual(item.unit_price, 2650)
        self.app.budget_items.clear()
        self.assertIsNone(calculate(self.app, [row]).rate)
        self.assertAlmostEqual(equivalent_interest(1, 4), Decimal(4))
        self.assertEqual(equivalent_interest(20, 0), 0)
        with self.assertRaises(ValueError):
            validate_state({"indirect_rows": [row | {"dedication": 101}]})

    def test_legacy_facilities_and_studies_keep_their_imputed_costs(self):
        """Migra los dos grupos sin perder céntimos al retirar campos antiguos."""
        from tools.price_justification.indirect import row_cost

        legacy = {"role": "Concepto", "quantity": 3, "monthly": 10.01, "months": 1, "dedication": 50}
        rows = validate_state({"indirect_rows": [
            legacy | {"group": "facilities"}, legacy | {"group": "studies"},
        ]})["indirect_rows"]
        self.assertNotIn("dedication", rows[0])
        self.assertEqual(set(rows[1]), {"group", "role", "total"})
        self.assertEqual([row_cost(row) for row in rows], [Decimal("15.02")] * 2)
        self.assertEqual(validate_state({"indirect_rows": rows})["indirect_rows"], rows)

    def test_indirect_costs_dialog_updates_read_only_total_and_keeps_group_order(self):
        """Edita los campos reales y comprueba el importe mostrado y recogido."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from tools.price_justification.indirect import default_rows
        from tools.price_justification.qt_window import IndirectCostsDialog

        application = QApplication.instance() or QApplication([])
        dialog = IndirectCostsDialog(None, self.app, default_rows())
        dialog.table.cellWidget(1, 1).setCurrentText("Personal de prueba")
        for column, value in ((0, 2), (2, 150), (3, 3), (4, 25)):
            dialog.table.cellWidget(1, column).setValue(value)
        total = dialog.table.item(1, 5)
        self.assertEqual(total.text(), "225,00")
        self.assertFalse(total.flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertIn("Aviso", dialog.summary.text())
        dialog.group.setCurrentIndex(1)
        dialog.add_row()
        self.assertEqual([row["group"] for row in dialog.rows()],
                         ["staff", "facilities", "facilities", "studies", "studies"])
        self.assertEqual(dialog.rows()[0]["role"], "Personal de prueba")
        for index in range(dialog.table.rowCount()):
            role = dialog.table.cellWidget(index, 1)
            if role is None:
                continue
            group = role.property("costGroup")
            if group == "facilities":
                self.assertIsNone(dialog.table.cellWidget(index, 4))
            elif group == "studies":
                for column in (0, 2, 3, 4):
                    self.assertIsNone(dialog.table.cellWidget(index, column))
                dialog.table.cellWidget(index, 5).setValue(12.34)
        self.assertEqual([row["total"] for row in dialog.rows() if row["group"] == "studies"], [12.34, 12.34])
        dialog.table.setCurrentCell(1, 5)
        dialog.remove_row()
        self.assertNotIn("staff", [row["group"] for row in dialog.rows()])
        dialog.close()
        application.processEvents()

    def test_indirect_cost_rows_fit_editors_before_and_after_adding(self):
        """Comprueba el espacio real de los controles con ambos temas de la aplicación."""
        from PySide6.QtWidgets import QApplication
        from budget_qt_theme import _stylesheet
        from tools.price_justification.indirect import default_rows
        from tools.price_justification.qt_window import IndirectCostsDialog

        application = QApplication.instance() or QApplication([])
        for theme in ("light", "dark"):
            dialog = IndirectCostsDialog(None, self.app, default_rows())
            dialog.setStyleSheet(_stylesheet(theme))
            dialog.show()
            try:
                for added in (False, True):
                    if added:
                        dialog.add_row()
                    application.processEvents()
                    with self.subTest(theme=theme, added=added):
                        for row in range(dialog.table.rowCount()):
                            for column in range(dialog.table.columnCount()):
                                widget = dialog.table.cellWidget(row, column)
                                if widget is not None:
                                    self.assertGreaterEqual(widget.height(), widget.sizeHint().height())
            finally:
                dialog.close()

    def test_boe_references_are_exported_without_replacing_custom_values(self):
        """Las referencias del BOE se incorporan al anejo y admiten datos locales editables."""
        custom = {
            "agreement": "Convenio provincial editable",
            "salary_revision": "Revisión provincial editable",
            "wage_reference": "Tabla salarial provincial editable",
            "contribution_bases": "Bases provinciales editables",
            "pension_contribution": "Aportación revisada editable",
            "contributions": "Cotizaciones provinciales editables",
            "contribution_details": "Conceptos provinciales editables",
            "calendar": "Calendario municipal editable",
            "work_conditions": "Condiciones laborales editables",
        }
        text = narrative_text(self.app, custom)
        for value in custom.values():
            self.assertIn(value, text)
        self.assertIn("Aportación empresarial al plan de pensiones", text)

    def test_library_edits_and_malformed_templates_are_not_ignored(self):
        """Una revisión del texto base afecta al documento y los errores se muestran sin contenido alternativo."""
        from tools._library_editor import _signature, read_markdown, write_markdown

        library = self.folder / "library.zip"
        shutil.copyfile(LIBRARY_PATH, library)
        member = DOCUMENTS[1][0]
        source = read_markdown(library, member)
        write_markdown(library, member, source + "\n\nTexto técnico revisado.\n", _signature(library))
        with patch("tools.price_justification.document.LIBRARY_PATH", library):
            text = narrative_text(self.app, {})
        self.assertIn("Texto técnico revisado.", text)
        with self.assertRaisesRegex(ValueError, "sin cerrar"):
            document_blocks("$$\nK = 1", {})
        with self.assertRaisesRegex(ValueError, "desconocida"):
            document_blocks("{{variable_inexistente}}", {})
        broken = self.folder / "missing.zip"
        with ZipFile(broken, "w") as archive:
            archive.writestr(DOCUMENTS[0][0], "# Legislación")
        with self.assertRaisesRegex(ValueError, "Faltan documentos"):
            load_documents(broken)

    def test_bundled_documents_accept_revisions_in_the_shared_editor(self):
        """Comprueba el guardado real de los ocho documentos y su efecto en el anejo."""
        from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget
        from tools._library_editor import read_markdown
        from tools._library_history import read_revisions
        from tools.pptp.qt_window import LibraryEditorQt
        from tools.price_justification.qt_window import PriceJustificationWindowQt

        class ProjectWidget(QWidget, ReportProject):
            """Combina los datos del proyecto de prueba con un padre Qt."""

        application = QApplication.instance() or QApplication([])
        project = ProjectWidget()
        vars(project).update(vars(self.app))
        project.price_justification_state = {}
        library = self.folder / "library.zip"
        shutil.copyfile(LIBRARY_PATH, library)
        with (
            patch("tools.price_justification.qt_window.LIBRARY_PATH", library),
            patch("tools.price_justification.document.LIBRARY_PATH", library),
            patch.object(QMessageBox, "critical") as error,
            patch.object(QMessageBox, "information"),
        ):
            window = PriceJustificationWindowQt(project)
            self.addCleanup(project.deleteLater)
            button = next(b for b in window.findChildren(QPushButton) if b.text().startswith("Editar biblioteca"))
            button.click()
            editor = window.library_editor
            self.assertIsInstance(editor, LibraryEditorQt)
            assert editor is not None
            self.assertEqual(editor.members.count(), 8)
            editor.author.setText("Autor de prueba")
            for index in range(editor.members.count()):
                editor.members.setCurrentRow(index)
                name = editor.members.currentItem().text()
                with self.subTest(document=name):
                    editor.editor.appendPlainText("Texto revisado para el anejo.")
                    if index == 0:
                        window.export()
                        self.assertIn("Guarda o descarta", error.call_args.args[2])
                        error.reset_mock()
                    self.assertTrue(editor.save(), error.call_args)
                    revisions, _ = read_revisions(read_markdown(library, name))
                    self.assertEqual(revisions[0].author, "Autor de prueba")
            error.assert_not_called()
            editor.close()
            project.report_settings.output_format = "odt"
            target = self.folder / "anejo.odt"
            write_document(project, target, {})
            with ZipFile(target) as exported:
                content = exported.read("content.xml")
                self.assertEqual(content.count(b"Texto revisado para el anejo."), 8)
                self.assertNotIn(b"Autor de prueba", content)
        application.processEvents()

    def test_single_pdf_and_odt_include_editable_listings_and_one_signature(self):
        """Genera ambos formatos reales, con las cinco secciones y sin alterar el presupuesto."""
        from pypdf import PdfReader
        from odt_reports import _tag
        from report_layout import DEFAULT_TEMPLATE_PATH
        from test_budget_reports import ReportLayoutTests

        template = self.folder / "pie.odt"
        with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as archive:
            for entry in source.infolist():
                data = source.read(entry)
                if entry.filename == "styles.xml":
                    root = ET.fromstring(data)
                    footer = next(root.iter(_tag("style:footer")))
                    for child in list(footer):
                        footer.remove(child)
                    paragraph = ET.SubElement(footer, _tag("text:p"), {
                        _tag("text:style-name"): "Standard",
                    })
                    paragraph.text = "{{capitulo}} — {{no_pagina}}"
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                archive.writestr(entry, data)
        self.app.report_settings.template_path = str(template)
        self.app.budget_dataset.concepts["AX2"] = Concept(
            "AX2", "ud", "Segundo auxiliar", 17.25, "0", children=[("MO1", 1)],
        )
        self.app.budget_items.append(ProjectItem(
            "P2", "01#", "ud", "Segunda partida", 1, 17.25,
            source="BC3", decomposition=[("AX2", 1)],
        ))
        original = deepcopy(self.app.budget_items)
        for output_format in ("pdf", "odt"):
            with self.subTest(output_format=output_format):
                self.app.report_settings.output_format = output_format
                target = self.folder / f"anejo.{output_format}"
                write_document(self.app, target, {
                    "reference_date": "2026-11-17", "territory": "Ámbito singular",
                    "materials_notes": "Transporte singular documentado.",
                })
                if output_format == "pdf":
                    reader = PdfReader(target)
                    text = "\n".join(page.extract_text() for page in reader.pages)
                    self.assertGreater(len(reader.pages), 4)
                    for item in ("contingencias comunes", "Totales:"):
                        self.assertEqual(len(re.findall(r"[•\x7f]\s*" + re.escape(item), text)), 1)
                    self.assertNotIn("Placeholder for table of contents", text)
                    self.assertIn("ANEJO Nº 15. JUSTIFICACIÓN DE PRECIOS — 2", " ".join(text.split()))
                    links = {
                        ref.get_object().get("/A", {}).get("/URI")
                        for page in reader.pages for ref in page.get("/Annots", ())
                    }
                    self.assertIn("https://www.boe.es/buscar/act.php?id=BOE-A-2001-19995#a130", links)
                    pages = [re.sub(r"\s+", "", page.extract_text()) for page in reader.pages]
                    for title, _ in LISTINGS:
                        title = re.sub(r"\s+", "", title)
                        actual_page = next(
                            index for index, page in enumerate(pages, 1)
                            if index > 1 and title in page
                        )
                        prefix = pages[0].split(title, 1)[0]
                        page_number = re.search(r"(\d+)$", prefix)
                        self.assertIsNotNone(page_number)
                        self.assertEqual(int(page_number.group(1)), actual_page)
                else:
                    with ZipFile(target) as archive:
                        self.assertIsNone(archive.testzip())
                        content = ET.fromstring(archive.read("content.xml"))
                        styles = ET.fromstring(archive.read("styles.xml"))
                        text = " ".join(content.itertext())
                        ns = {
                            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
                            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
                            "math": "http://www.w3.org/1998/Math/MathML",
                            "xlink": "http://www.w3.org/1999/xlink",
                        }
                        self.assertIsNotNone(content.find(".//text:table-of-content", ns))
                        self.assertTrue(content.findall(".//table:table-header-rows", ns))
                        self.assertTrue(content.findall(
                            ".//table:table-cell[@table:style-name='TintedHeaderCell']", ns,
                        ))
                        for base in ("HeaderCell", "HeaderCellLast", "BodyCell", "BodyCellLast"):
                            cell_properties = []
                            for name in (base, "Tinted" + base):
                                props = content.find(f".//{_tag('style:style')}[@{_tag('style:name')}='{name}']"
                                                     f"/{_tag('style:table-cell-properties')}")
                                properties = dict(props.attrib)
                                properties.pop(_tag("fo:background-color"))
                                cell_properties.append(properties)
                            self.assertEqual(*cell_properties)
                        indented = content.findall(".//text:p[@text:style-name='PricesTableTextIndented']", ns)
                        self.assertTrue(any("5.101,20" in "".join(p.itertext()) for p in indented))
                        self.assertTrue(any("grupo 2," in "".join(p.itertext()) for p in indented))
                        interest = next(table for table in content.findall(".//table:table", ns)
                                        if "T (años)" in "".join(table.itertext()))
                        interest_name = interest.get(_tag("table:style-name"))
                        props = content.find(f".//{_tag('style:style')}[@{_tag('style:name')}='{interest_name}']"
                                             f"/{_tag('style:table-properties')}")
                        self.assertLess(float(props.get(_tag("style:width")).removesuffix("pt")), 350)
                        self.assertEqual(props.get(_tag("table:align")), "center")
                        self.assertEqual(len(interest.findall("table:table-column", ns)), 5)
                        for paragraph in interest.findall(".//text:p", ns):
                            style_name = paragraph.get(_tag("text:style-name"))
                            cell_props = content.find(f".//{_tag('style:style')}[@{_tag('style:name')}='{style_name}']"
                                                      f"/{_tag('style:paragraph-properties')}")
                            self.assertEqual(cell_props.get(_tag("fo:text-align")), "center")
                        subtitle = next(p for p in content.findall(".//text:h", ns)
                                        if "".join(p.itertext()).strip() == "Operarios, consumos y condiciones particulares")
                        self.assertEqual(subtitle.get(_tag("text:outline-level")), "4")
                        lists = content.findall(".//text:list[@text:style-name='PricesTableBullets']", ns)
                        self.assertEqual([len(items) for items in lists], [7])
                        self.assertEqual(" ".join(text.split()).count("47,48 €/día"), 1)
                        self.assertTrue(content.findall(".//math:mfrac/math:mrow/math:mfrac", ns))
                        self.assertTrue(content.findall(".//math:msub", ns))
                        self.assertTrue(content.findall(".//math:msup", ns))
                        self.assertTrue(content.findall(".//text:p[@text:style-name='PricesList1']/text:tab", ns))
                        self.assertTrue(content.findall(".//table:table-cell[@table:style-name='AuxiliaryBandCell']", ns))
                        self.assertTrue(content.findall(".//table:table-cell[@table:style-name='AuxiliaryTotalCellLast']", ns))
                        self.assertTrue(content.findall(".//text:p[@text:style-name='Signature']", ns))
                        footer = next(styles.iter(_tag("style:footer")))
                        self.assertIn("ANEJO Nº 15. JUSTIFICACIÓN DE PRECIOS", "".join(footer.itertext()))
                        self.assertIsNotNone(footer.find(".//text:page-number", ns))
                        self.assertIn(
                            "https://www.boe.es/buscar/act.php?id=BOE-A-2001-19995#a130",
                            {anchor.get(f"{{{ns['xlink']}}}href") for anchor in content.findall(".//text:a", ns)},
                        )
                        ReportLayoutTests.assert_odt_styles(self, archive)
                condensed = re.sub(r"\s+", "", text)
                self.assertNotIn("Registro de cambios", " ".join(text.split()))
                self.assertNotIn("Biblioteca distribuida con el programa", " ".join(text.split()))
                self.assertNotIn("[PENDIENTE", text)
                self.assertNotIn("2026—", condensed)
                self.assertIn("01/01/2026", text)
                self.assertNotIn("VéaselaTabla", condensed)
                self.assertNotIn("Fuentedepreciosydescomposiciones", condensed)
                self.assertNotIn("Basesytiposdecotización", condensed)
                for number in range(1, 6):
                    self.assertIn(f"(Tabla{number}).Tabla{number}.", condensed)
                self.assertIn("empresa/trabajador:", text)
                self.assertIn("Operarios,consumosycondicionesparticulares", condensed)
                self.assertNotIn("i=0%", condensed)
                self.assertIn("Jornadaanualefectivadereferencia1736horas", condensed)
                self.assertNotIn("Jornadaanualefectivadereferencia:1736horas.", condensed)
                self.assertNotRegex(condensed, r"Tabla\d+\.(?:PreciosAuxiliares|Descompuestos)\(importeen€\)")
                self.assertNotIn("PreciodescompuestodeP1", condensed)
                self.assertNotIn("Componentesdelprecioauxiliar", condensed)
                self.assertIn("Segundoauxiliar", condensed)
                self.assertIn("Segundapartida", condensed)
                for title, _ in LISTINGS:
                    self.assertIn(re.sub(r"\s+", "", title), condensed)
                for value in (
                    "Operario singular", "Compactador singular", "Zahorra singular",
                    "Mezcla auxiliar singular", "Transporte singular documentado.",
                ):
                    self.assertIn(re.sub(r"\s+", "", value), condensed)
                self.assertEqual(text.count("Autor único del anejo"), 1)
                self.assertEqual(self.app.budget_items, original)

    def test_failed_generation_does_not_replace_a_previous_document(self):
        """Un error deja intacto el destino y limpia únicamente su archivo temporal."""
        target = self.folder / "previous.pdf"
        target.write_bytes(b"previous")
        with patch(
            "tools.price_justification.document._write_pdf", side_effect=ValueError("error de prueba"),
        ), self.assertRaisesRegex(ValueError, "error de prueba"):
            write_document(self.app, target, {})
        self.assertEqual(target.read_bytes(), b"previous")
        self.assertEqual(list(self.folder.iterdir()), [target])

    def test_author_visibility_controls_signatures_and_document_metadata(self):
        """Oculta o restaura varios autores, sin alterar el cargo ni perder los nombres."""
        from pypdf import PdfReader
        from datetime import date
        from budget_reports import CERTIFICATION_REPORT_NAMES, write_certification_pdf, write_pdf
        from odt_reports import _tag, write_certification_odt, write_report_odt
        from tools._shared.scheduling import PlanActivity, PlanParams
        from tools.planning.document import write_planning_report
        from tools.rcd import RcdState
        from tools.rcd.document import write_study
        from tools.pptp.document import write_document as write_pptp

        authors = "Autora Alfa\nAutor Beta"
        names = authors.splitlines()
        role = "Cargo de prueba"
        settings = ReportSettings.from_dict({"author": authors, "role": role})
        settings.contractor = "Contrata de prueba"
        self.app.report_settings = settings
        activity = PlanActivity(
            id=1, chapter="01#", chapter_name="Obra", code="P1", summary="Actividad",
            unit="ud", quantity=1, amount=100, rendimiento=1, duration=2,
            start_offset=0, finish_offset=2, start_date=date(2026, 11, 9), end_date=date(2026, 11, 10),
        )
        self.app.budget_items[0].certifications = {"2026-10-01": 100}
        for output in ("pdf", "odt"):
            settings.output_format = output
            for show in (False, True):
                settings.show_author = show
                for report in ("presupuesto", "anejo", "rcd", "pptp", "planificacion", "certificacion"):
                    with self.subTest(format=output, show_author=show, report=report):
                        target = self.folder / f"{report}.{output}"
                        if report == "presupuesto":
                            if output == "pdf":
                                write_pdf(self.app, "Presupuesto resumen", target)
                            else:
                                write_report_odt(self.app, "Presupuesto resumen", target)
                        elif report == "anejo":
                            write_document(self.app, target, {})
                        elif report == "rcd":
                            state = RcdState()
                            state.inputs.update(surface=100, earth_volume=10, budget=1000, earth_budget=0)
                            write_study(state, self.app.project_title, settings, target)
                        elif report == "pptp":
                            write_pptp("# Alcance\n\nContenido técnico.", target,
                                       {"titulo_proyecto": self.app.project_title}, settings)
                        elif report == "planificacion":
                            write_planning_report([activity], PlanParams(start=activity.start_date), target,
                                                  settings, self.app.project_title, str)
                        else:
                            writer = write_certification_pdf if output == "pdf" else write_certification_odt
                            writer(self.app.budget_items, self.app._chapter_title, self.app.project_title,
                                   settings, CERTIFICATION_REPORT_NAMES[0], target, "2026-10-01", 1, "Oct 26")
                        if output == "pdf":
                            reader = PdfReader(target)
                            text = "\n".join(page.extract_text() for page in reader.pages)
                            creator = reader.metadata.author
                        else:
                            with ZipFile(target) as archive:
                                content = ET.fromstring(archive.read("content.xml"))
                                text = " ".join(content.itertext())
                                metadata = ET.fromstring(archive.read("meta.xml"))
                                creator = metadata.findtext(f".//{_tag('dc:creator')}")
                                signature_lines = [
                                    "".join(paragraph.itertext()) for paragraph in content.iter(_tag("text:p"))
                                    if paragraph.get(_tag("text:style-name")) in {"Signature", "SignatureNote"}
                                ]
                                self.assertEqual(signature_lines[-(4 if show else 2):],
                                                 [role, *(names if show else []), settings.signature])
                        self.assertEqual(creator == authors, show)
                        self.assertIn(role, text)
                        for name in names:
                            self.assertEqual(name in text, show)
                        if show:
                            signature_text = text[text.rindex(role):]
                            self.assertLess(signature_text.index(names[0]), signature_text.index(names[1]))
                        if report == "certificacion":
                            self.assertIn(settings.contractor, text)
                self.assertEqual(settings.author, authors)


@unittest.skipIf(BudgetApp is None, "El equivalente vigente se comprueba en test_budget_qt.")
class PriceWindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with (
            patch.object(self.root, "state"),
            patch.object(BudgetApp, "_read_recent_projects", return_value=[]),
            patch("bc3_gui.user_data_directory", return_value=self.folder),
        ):
            self.app = BudgetApp(self.root)
        self.app.budget_items = [ProjectItem("P1", "01#", "ud", "Partida", 1, 10)]
        self.app.chapter_order = ["01#"]
        self.app._reset_history()

    def test_first_menu_entry_and_edit_save_load_undo_and_legacy_project(self):
        """Comprueba la ventana real, el guardado, la carga y la independencia del presupuesto."""
        self.assertEqual(self.app.tools_menu.entrycget(0, "label"), "Justificación de precios")
        self.app.price_justification_state = {
            "agreement": "", "annual_hours": "", "materials_notes": "Suministro documentado",
        }
        open_tool(self.app)
        window = self.app._project_windows[-1]
        open_tool(self.app)
        self.assertEqual(len(self.app._project_windows), 1)
        self.assertNotIn("territory", window.variables)
        self.assertEqual(
            [window.notebook.tab(tab, "text") for tab in window.notebook.tabs()],
            ["Datos específicos"],
        )
        self.assertTrue(all(variable.get() for variable in window.variables.values()))
        self.assertEqual(window.variables["agreement"].get(), CONSTRUCTION_AGREEMENT_REFERENCES[0])
        self.assertEqual(window.variables["salary_revision"].get(), SALARY_REVISION_REFERENCES[4])
        self.assertEqual(window.variables["wage_reference"].get(), WAGE_REFERENCES[-1])
        self.assertEqual(
            window.variables["pension_contribution"].get(), PENSION_CONTRIBUTION_REFERENCES[-1],
        )
        self.assertEqual(window.variables["annual_hours"].get(), ANNUAL_HOURS_REFERENCES[0])
        for key, values in (
            ("agreement", CONSTRUCTION_AGREEMENT_REFERENCES),
            ("salary_revision", SALARY_REVISION_REFERENCES),
            ("wage_reference", WAGE_REFERENCES),
            ("contribution_bases", CONTRIBUTION_BASE_REFERENCES),
            ("contributions", CONTRIBUTION_RATE_REFERENCES),
            ("contribution_details", CONTRIBUTION_DETAIL_REFERENCES),
            ("pension_contribution", PENSION_CONTRIBUTION_REFERENCES),
            ("calendar", CALENDAR_REFERENCES),
            ("work_conditions", WORK_CONDITION_REFERENCES),
            ("annual_hours", ANNUAL_HOURS_REFERENCES),
        ):
            self.assertEqual(str(window.selectors[key].cget("state")), "normal")
            self.assertEqual(tuple(window.selectors[key]["values"]), values)
        window.variables["price_source"].set("Fuente de prueba")
        window.variables["agreement"].set("Convenio provincial editable")
        window.variables["salary_revision"].set("Revisión provincial editable")
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(self.app.price_justification_state["price_source"], "Fuente de prueba")
        before_project_save(self.app)
        self.assertEqual(self.app.price_justification_state["materials_notes"], "Suministro documentado")
        target = self.folder / "project.json"
        self.assertTrue(self.app._write_project_file(target))
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["price_justification"]["price_source"], "Fuente de prueba")
        self.assertEqual(
            payload["price_justification"]["agreement"], "Convenio provincial editable",
        )
        self.assertEqual(
            payload["price_justification"]["salary_revision"], "Revisión provincial editable",
        )
        self.app.price_justification_state["price_source"] = "Cambio"
        self.app._mark_dirty()
        self.app.undo_last_action()
        self.assertEqual(self.app.price_justification_state["price_source"], "Fuente de prueba")
        self.assertTrue(self.app._load_project_file(target))
        self.assertEqual(self.app.price_justification_state["materials_notes"], "Suministro documentado")
        self.assertEqual(self.app.budget_items[0].unit_price, 10)
        legacy = self.folder / "legacy.json"
        legacy.write_text('{"items": []}', encoding="utf-8")
        self.assertTrue(self.app._load_project_file(legacy))
        self.assertEqual(self.app.price_justification_state, {})

    def test_invalid_export_and_cancel_do_not_write_a_document(self):
        """Una entrada incompleta o cancelar el diálogo no inicia la generación."""
        open_tool(self.app)
        window = self.app._project_windows[-1]
        window.variables["annual_hours"].set("NaN")
        with (
            patch("tools.price_justification.messagebox.showerror") as error,
            patch("tools.price_justification.filedialog.asksaveasfilename") as choose,
        ):
            window._export()
        error.assert_called_once()
        choose.assert_not_called()
        window.variables["annual_hours"].set("1736")
        with (
            patch("tools.price_justification.filedialog.asksaveasfilename", return_value=""),
            patch("tools.price_justification.write_document") as write,
        ):
            window._export()
        write.assert_not_called()

    def test_partial_input_survives_undo_but_cannot_replace_a_saved_project(self):
        """Deshacer restaura una edición intermedia; guardar exige que sus datos sean válidos."""
        target = self.folder / "previous.json"
        target.write_text('{"previous": true}', encoding="ascii")
        open_tool(self.app)
        window = self.app._project_windows[-1]
        window.variables["reference_date"].set("2026-")
        window.variables["price_source"].set("Otra fuente")
        self.app.undo_last_action()
        self.assertEqual(self.app.price_justification_state["reference_date"], "2026-")
        with patch("bc3_gui.messagebox.showerror") as error:
            self.assertFalse(self.app._write_project_file(target))
        error.assert_called_once()
        self.assertEqual(target.read_text(encoding="ascii"), '{"previous": true}')

    def test_library_editor_preview_and_unsaved_change_export_guard(self):
        """Reutiliza el editor PPTP y no exporta una versión diferente de la edición sin guardar."""
        from tools._library_editor import _signature, read_markdown, write_markdown

        library = self.folder / "library.zip"
        shutil.copyfile(LIBRARY_PATH, library)
        with patch("tools.price_justification.LIBRARY_PATH", library):
            open_tool(self.app)
            window = self.app._project_windows[-1]
            window.open_library_editor()
            self.assertEqual(window.library_editor.archive_path, library)
            self.assertTrue(window.library_editor.track_changes)
            member = DOCUMENTS[0][0]
            source = read_markdown(library, member)
            write_markdown(library, member, source + "\n\nRevisión visible en el asistente.\n", _signature(library))
            window._reload_library()
            self.assertIn("Revisión visible", dict(load_documents(library))[member])
            window.library_editor.dirty = True
            with (
                patch("tools.price_justification.messagebox.showerror") as error,
                patch("tools.price_justification.filedialog.asksaveasfilename") as choose,
            ):
                window._export()
            error.assert_called_once()
            choose.assert_not_called()
            window.library_editor.dirty = False


if __name__ == "__main__":
    unittest.main()
