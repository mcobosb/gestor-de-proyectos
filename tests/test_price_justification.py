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
    DOCUMENTS, FORMULA_LEVEL, LIBRARY_PATH, NarrativeTable, document_blocks,
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
        if isinstance(value, NarrativeTable) else value
        for _, value in narrative(app, state)
    )


class PriceDocumentTests(unittest.TestCase):
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

    def test_base_documents_include_the_complete_method_and_formulas(self):
        """El anejo contiene el método aportado, no solo notas o avisos sobre fórmulas pendientes."""
        self.assertEqual(len(load_documents(LIBRARY_PATH)), 8)
        blocks = narrative(self.app, {})
        formulas = [text for level, text in blocks if level == FORMULA_LEVEL]
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
        self.assertEqual(len(tables), 4)
        self.assertEqual(tables[0].rows, (("Proyecto", "Proyecto singular"),))
        self.app.report_settings.expediente = "EXP-42"
        project = next(block for _, block in narrative(
            self.app, {"reference_date": "2026-11-19"},
        ) if isinstance(block, NarrativeTable))
        self.assertEqual(dict(project.rows)["Expediente"], "EXP-42")
        self.assertEqual(dict(project.rows)["Fecha de referencia de los precios"], "2026-11-19")

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
                    for item in ("base máxima:", "grupo 2,", "contingencias comunes", "Totales:"):
                        self.assertEqual(len(re.findall(r"[•\x7f]\s*" + re.escape(item), text)), 2)
                    self.assertNotIn("Placeholder for table of contents", text)
                    self.assertIn("JUSTIFICACIÓN DE PRECIOS — 2", " ".join(text.split()))
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
                        self.assertEqual(len(content.findall(
                            ".//table:table-cell[@table:style-name='PricesHeaderCell']", ns,
                        )), 8)
                        lists = content.findall(".//text:list[@text:style-name='PricesTableBullets']", ns)
                        self.assertEqual(sorted(len(items) for items in lists), [6, 6, 7, 7])
                        self.assertEqual(" ".join(text.split()).count("47,48 €/día"), 2)
                        self.assertTrue(content.findall(".//math:mfrac/math:mrow/math:mfrac", ns))
                        self.assertTrue(content.findall(".//math:msub", ns))
                        self.assertTrue(content.findall(".//math:msup", ns))
                        self.assertTrue(content.findall(".//text:p[@text:style-name='PricesList1']/text:tab", ns))
                        self.assertTrue(content.findall(".//table:table-cell[@table:style-name='AuxiliaryBandCell']", ns))
                        self.assertTrue(content.findall(".//table:table-cell[@table:style-name='AuxiliaryTotalCellLast']", ns))
                        self.assertTrue(content.findall(".//text:p[@text:style-name='Signature']", ns))
                        footer = next(styles.iter(_tag("style:footer")))
                        self.assertIn("JUSTIFICACIÓN DE PRECIOS", "".join(footer.itertext()))
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
                for number in range(1, 5):
                    self.assertIn(f"Tabla{number}.", condensed)
                    self.assertIn(f"Véaselatabla{number}:", condensed)
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
        from tools.planning.reports import write_planning_report
        from tools.rcd import RcdState
        from tools.rcd.study import write_study
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
