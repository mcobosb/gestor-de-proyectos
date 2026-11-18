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

from bc3_gui import Bc3Dataset, BudgetApp, Concept, ProjectItem
from budget_reports import ReportSettings, _collect_report_resources
from tools.price_justification import before_project_save, open_tool
from tools.price_justification.document import (
    LISTINGS, narrative, restore_state, validate_state, write_document,
)
from tools.price_justification.templates import (
    DOCUMENTS, FORMULA_LEVEL, LIBRARY_PATH, document_blocks, formula_runs,
    general_variables, load_documents,
)


class PriceDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.app = BudgetApp.__new__(BudgetApp)
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
        private = "\n".join(text for _, text in narrative(self.app, state))
        self.assertNotIn("Ley 9/2017", private)
        self.assertIn("contratación privada", private)
        self.assertIn("1736,5 horas", private)
        self.assertIn("PENDIENTE DE DEFINIR Y VERIFICAR", private)
        public = "\n".join(text for _, text in narrative(self.app, {}))
        self.assertIn("Ley 9/2017", public)
        self.assertIn("6,00 %", public)
        self.assertNotIn("2008", public)
        self.assertNotIn("2009", public)

    def test_base_documents_include_the_complete_method_and_formulas(self):
        """El anejo contiene el método aportado, no solo notas o avisos sobre fórmulas pendientes."""
        self.assertEqual(len(load_documents(LIBRARY_PATH)), 8)
        blocks = narrative(self.app, {})
        formulas = [text for level, text in blocks if level == FORMULA_LEVEL]
        self.assertIn("P_n = (1 + K / 100) C_n", formulas)
        self.assertIn("C = [A + B + (k / 100) A] / H_a", formulas)
        self.assertIn("T = H_ut / H_ua", formulas)
        self.assertIn("C_d = (i_m + S) / E + (A_d H_ua) / (E H_ut)", formulas)
        self.assertIn("C_hm = [100 + (M + C)] / H_ut + (i_m + S) / H_ua", formulas)
        text = "\n".join(text for _, text in blocks)
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
        first = "\n".join(text for _, text in narrative(self.app, state))
        for value in ("Lucena", "Córdoba", "Andalucía", "Madrid", "Entidad configurada", "EXP-PRUEBA"):
            self.assertIn(value, first)
        self.assertNotIn("obsoleto", first)
        settings.municipality_codes = ["280796"]
        settings.organization = "Entidad actualizada"
        settings.indirect_costs = 8.25
        second = "\n".join(text for _, text in narrative(self.app, state))
        self.assertIn("Entidad actualizada", second)
        self.assertIn("8,25 %", second)
        self.assertNotIn("Córdoba", second)
        settings.municipality_codes = []
        settings.location = "Localidad configurada"
        self.assertEqual(general_variables(self.app)["emplazamiento"], "Localidad configurada")
        self.assertEqual(general_variables(self.app)["provincias"], "")
        self.assertNotIn("territory", restore_state(state))

    def test_library_edits_and_malformed_templates_are_not_ignored(self):
        """Una revisión del texto base afecta al documento y los errores se muestran sin contenido alternativo."""
        from tools._library_editor import _signature, read_markdown, write_markdown

        library = self.folder / "library.zip"
        shutil.copyfile(LIBRARY_PATH, library)
        member = DOCUMENTS[1][0]
        source = read_markdown(library, member)
        write_markdown(library, member, source + "\n\nTexto técnico revisado.\n", _signature(library))
        with patch("tools.price_justification.document.LIBRARY_PATH", library):
            text = "\n".join(text for _, text in narrative(self.app, {}))
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

    def test_single_pdf_and_odt_include_editable_listings_and_one_signature(self):
        """Genera ambos formatos reales, con las cinco secciones y sin alterar el presupuesto."""
        from pypdf import PdfReader
        from test_budget_reports import ReportLayoutTests

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
                    self.assertNotIn("Placeholder for table of contents", text)
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
                        text = " ".join(content.itertext())
                        ns = {
                            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
                            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
                        }
                        self.assertIsNotNone(content.find(".//text:table-of-content", ns))
                        self.assertTrue(content.findall(".//table:table-header-rows", ns))
                        formulas = [
                            "".join(paragraph.itertext())
                            for paragraph in content.findall(".//text:p", ns)
                            if paragraph.get(f"{{{ns['text']}}}style-name") == "PricesFormula"
                        ]
                        expected = [
                            "".join(value for value, _ in formula_runs(text))
                            for level, text in narrative(self.app, {
                                "reference_date": "2026-11-17", "materials_notes": "Transporte singular documentado.",
                            })
                            if level == FORMULA_LEVEL
                        ]
                        self.assertEqual(formulas, expected)
                        self.assertTrue(content.findall(
                            ".//text:span[@text:style-name='PricesSubscript']", ns,
                        ))
                        self.assertTrue(content.findall(
                            ".//text:span[@text:style-name='PricesSuperscript']", ns,
                        ))
                        ReportLayoutTests.assert_odt_styles(self, archive)
                condensed = re.sub(r"\s+", "", text)
                self.assertNotIn("Registro de cambios", " ".join(text.split()))
                self.assertNotIn("Biblioteca distribuida con el programa", " ".join(text.split()))
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
        self.assertEqual(window.variables["agreement"].get(), "PENDIENTE DE DEFINIR Y VERIFICAR")
        self.assertEqual(window.variables["annual_hours"].get(), "PENDIENTE DE DEFINIR Y VERIFICAR")
        window.variables["price_source"].set("Fuente de prueba")
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(self.app.price_justification_state["price_source"], "Fuente de prueba")
        before_project_save(self.app)
        self.assertEqual(self.app.price_justification_state["materials_notes"], "Suministro documentado")
        target = self.folder / "project.json"
        self.assertTrue(self.app._write_project_file(target))
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["price_justification"]["price_source"], "Fuente de prueba")
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
