import gc
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from pypdf import PdfReader

from bc3_gui import APPLICATION_VERSION, BudgetApp, MeasurementLine, ProjectItem
from budget_reports import ReportSettings
from rcd import (
    GROUPS, HEADERS, INPUTS, WASTES, DemolitionDialog, DemolitionMeasurement,
    RCD_BUDGET_SOURCE, RcdState, RcdWindow, TRANSPORT_PREFIX, TRANSPORT_REVIEW, calculate,
    demolition_measurements, detect_wastes, initial_state, number, synchronize_budget,
    transfer_demolition, write_report,
)
from report_layout import DEFAULT_TEMPLATE_PATH, _read_report_template


ROOT = Path(__file__).resolve().parent.parent


def example_state():
    state = RcdState()
    state.inputs.update(surface=1076.81, earth_volume=1853.84, budget=421392.81, earth_budget=85138.73)
    state.selected.update({key: True for key in ("5", "13", "15", "21", "28", "37", "40", "42", "46")})
    return state


def set_items(app):
    app.budget_items = [
        ProjectItem("EXC", "01#", "m³", "Excavación de tierras", measurement=10, unit_price=20),
        ProjectItem("DEM", "02#", "m²", "Demolición de pavimento de hormigón", measurement=50, unit_price=10),
        ProjectItem("MAT", "02#", "m²", "Suministro de madera y vidrio", measurement=50, unit_price=10),
        ProjectItem("GEST", "03#", "m³", "Gestión de residuos", measurement=10, unit_price=5),
    ]
    app.chapter_order = ["01#", "02#", "03#"]
    app.chapter_titles = {"01#": "Movimiento de tierras", "02#": "Obra", "03#": "Gestión de residuos"}
    app.budget_dataset = None
    app.reference_bases = []


class RcdCalculationTests(unittest.TestCase):
    def test_numeric_validation(self):
        self.assertEqual(number("1.234,56", "Valor"), 1234.56)
        self.assertEqual(number("1,234.56", "Valor"), 1234.56)
        for value in (True, None, "", "nan", "inf", "-1", "abc", "1e100"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value, "Valor")

    def test_workbook_volumes_and_revised_row_rounded_total(self):
        state = example_state()
        sections, details = calculate(state)
        self.assertEqual([row[1:4] for row in sections[0][1]], [["1.853,84", "5,90", "10.937,66"]])
        self.assertEqual(
            [row[1:4] for row in sections[1][1]],
            [["121,14", "8,61", "1.043,02"], ["29,91", "21,13", "632,00"], ["0,00", "22,25", "0,00"]],
        )
        self.assertEqual(sections[2][1][1][3], "10,55")
        self.assertEqual(sections[-1][1][0][3], "13.044,62")
        self.assertEqual(len(details), len(GROUPS))
        self.assertEqual(details[1][-1], "9,32")
        self.assertEqual(details[10][-1], "87,22")

    def test_manual_b1_is_included_and_minimum_is_configurable(self):
        state = example_state()
        state.inputs["level1_extra"] = 100
        self.assertEqual(calculate(state)[0][-1][1][0][3], "13.144,62")
        state.inputs["level2_min"] = 0.2
        sections, _ = calculate(state)
        self.assertEqual(sections[2][1][1][3], "0,00")
        self.assertEqual(sections[-1][1][0][3], "13.134,07")
        state.inputs["management"] = 0
        self.assertEqual(calculate(state)[0][2][1][2][3], "0,00")

    def test_unselected_groups_zero_and_multiple_rows_do_not_duplicate_volume(self):
        state = example_state()
        state.selected = {key: False for key in state.selected}
        state.inputs["level2_min"] = state.inputs["management"] = 0
        self.assertEqual(calculate(state)[0][-1][1][0][3], "0,00")
        state.selected["42"] = True
        sections, _ = calculate(state)
        first = sections[1][1][0][1]
        state.selected["43"] = True
        self.assertEqual(calculate(state)[0][1][1][0][1], first)
        state.selected["42"] = state.selected["43"] = False
        self.assertEqual(calculate(state)[0][1][1][0][1], "0,00")

    def test_invalid_or_missing_required_fields(self):
        for key, value in (("surface", None), ("surface", -1), ("density", 0), ("budget", 0)):
            state = example_state()
            state.inputs[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                calculate(state)

    def test_roundtrip_defaults_and_validation(self):
        state = example_state()
        state.evidence["40"] = ["DEM: Hormigón"]
        state.sources["surface"] = "Manual"
        self.assertEqual(RcdState.from_dict(state.to_dict()).to_dict(), state.to_dict())
        self.assertIsNone(RcdState.from_dict({}).inputs["surface"])
        for data in (
            [], {"inputs": []}, {"selected": {"5": "X"}}, {"groups": {"wood": []}},
            {"groups": {"wood": {"density": 0}}}, {"groups": {"wood": {"percentage": 101}}},
            {"evidence": {"5": [1]}}, {"sources": {"surface": 123}},
            {"inputs": {"level2_min": 101}}, {"inputs": {"management": True}},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                RcdState.from_dict(data)

    def test_recover_budget_inputs_without_summing_overlapping_surfaces(self):
        app = BudgetApp.__new__(BudgetApp)
        set_items(app)
        state = initial_state(app)
        self.assertIsNone(state.inputs["surface"])
        self.assertEqual(state.inputs["budget"], 1200)
        self.assertEqual(state.inputs["earth_budget"], 200)
        self.assertEqual(state.inputs["earth_volume"], 10)
        self.assertIn("EXC", state.sources["earth_volume"])
        self.assertTrue(state.selected["5"])
        self.assertTrue(state.selected["40"])
        self.assertFalse(state.selected["15"])
        self.assertFalse(state.selected["30"])
        self.assertIn("DEM", state.evidence["40"][0])

    def test_no_excavation_does_not_invent_zero_or_reuse_transport(self):
        app = BudgetApp.__new__(BudgetApp)
        set_items(app)
        app.budget_items = [ProjectItem("T", "02#", "m3", "Transporte de tierras de excavación",
                                       measurement=20, unit_price=1)]
        state = initial_state(app)
        self.assertIsNone(state.inputs["earth_volume"])
        self.assertEqual(state.inputs["budget"], 20)

    def test_exact_ler_and_dangerous_materials_need_review(self):
        app = BudgetApp.__new__(BudgetApp)
        set_items(app)
        app.budget_items = [
            ProjectItem("LER", "02#", "t", "Retirada LER 17.02.03"),
            ProjectItem("AMI", "02#", "m2", "Retirada de fibrocemento con amianto"),
        ]
        state = initial_state(app)
        self.assertTrue(state.selected["28"])
        self.assertTrue(state.selected["63"])
        self.assertIn("[LER]", state.evidence["28"][0])
        self.assertFalse(state.selected["40"])

    def test_excluded_ler_references_and_nonhazardous_wording_do_not_mark_dangerous_waste(self):
        app = BudgetApp.__new__(BudgetApp)
        set_items(app)
        app.budget_items = [
            ProjectItem("LER", "02#", "t", "Retirada de tierras no contaminadas LER 17 05 04",
                        text="Tierras distintas de las especificadas en el código 17 05 03"),
            ProjectItem("MIX", "02#", "t", "Residuos mezclados LER 17 09 04"),
        ]
        state = initial_state(app)
        self.assertTrue(state.selected["5"])
        self.assertFalse(state.selected["69"])
        self.assertTrue(state.selected["46"])
        self.assertFalse(state.selected["85"])

    def test_catalogue_and_version_are_packaged_without_private_workbook(self):
        self.assertEqual(len(WASTES), 57)
        self.assertEqual(len({entry[0] for entry in WASTES}), 57)
        self.assertEqual(APPLICATION_VERSION, "1.5.0")
        self.assertIn('--file-version=1.5.0', (ROOT / "build_installer.ps1").read_text(encoding="utf-8"))
        self.assertIn('--product-version=1.5.0', (ROOT / "build_installer.ps1").read_text(encoding="utf-8"))
        self.assertIn('#define AppVersion "1.5.0"',
                      (ROOT / "installer" / "GestorDeProyectos.iss").read_text(encoding="utf-8"))

    def test_demolition_measured_volumes_replace_model_and_follow_waste_marks(self):
        state = example_state()
        state.demolition = [
            DemolitionMeasurement("one", "DEM: Hormigón", "m²", 50, "concrete", 0.2, True, "40"),
            DemolitionMeasurement("two", "DEM: Madera", "m³", 3, "wood", 1, True, "15"),
        ]
        state.inputs["surface"] = state.inputs["earth_volume"] = state.inputs["earth_budget"] = None
        sections, details = calculate(state)
        self.assertEqual(sections[0][1][0][1], "0,00")
        self.assertEqual(sections[1][1][0][1], "10,00")
        self.assertEqual(sections[1][1][1][1], "3,00")
        self.assertEqual(details[9][-1], "10,00")
        state.inputs["earth_volume"] = 25
        self.assertEqual(calculate(state)[0][0][1][0][1], "25,00")
        state.demolition.append(DemolitionMeasurement("earth", "Tierras revisadas",
                                                      "m³", 4, "earth", 1, True, "5"))
        self.assertEqual(calculate(state)[0][0][1][0][1], "4,00")
        state.inputs["surface"] = 100000
        state.groups["concrete"]["percentage"] = 99
        self.assertEqual(calculate(state)[0][1][1][0][1], "10,00")
        state.selected["40"] = False
        self.assertEqual(calculate(state)[0][1][1][0][1], "0,00")
        self.assertEqual(RcdState.from_dict(state.to_dict()).to_dict(), state.to_dict())

    def test_invalid_demolition_data_rejects_missing_conversion_or_material(self):
        valid = dict(key="a", source="DEM", unit="m²", quantity=5, group="concrete",
                     factor=0.1, included=True, waste_key="40")
        for update in (
            {"factor": None}, {"factor": -1}, {"group": ""}, {"waste_key": "63"},
            {"quantity": float("nan")}, {"quantity": True}, {"included": "yes"},
            {"unit": "m³", "factor": 2},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                DemolitionMeasurement.from_dict({**valid, **update})
        for rows in ([{**valid, "quantity": -5}], [valid, valid], [{**valid, "included": False}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                RcdState.from_dict({"demolition": rows})
        state = RcdState.from_dict({"demolition": [valid, {**valid, "key": "b", "quantity": -1}]})
        self.assertEqual(str(sum(row.volume for row in state.demolition)), "0.40")


class RcdReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def test_reports_separate_tables_and_signature_vertically(self):
        """Verify both renderers reserve space between tables and before signatures."""
        from unittest.mock import patch
        from reportlab.platypus import KeepTogether, Spacer

        settings = ReportSettings(author="Autor RCD", output_format="odt")
        target = self.folder / "spaced.odt"
        write_report(example_state(), "Proyecto RCD", settings, target)
        with ZipFile(target) as archive:
            root = ElementTree.fromstring(archive.read("content.xml"))
        ns = {
            "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
        }
        body = root.find("office:body/office:text", ns)
        styles = [element.get(f"{{{ns['text']}}}style-name") for element in body]
        self.assertGreaterEqual(styles.count("Space12.0000"), 7)
        signature = styles.index("Signature")
        self.assertEqual(styles[signature - 1], "Space24.0000")
        settings.output_format = "pdf"
        with patch("reportlab.platypus.SimpleDocTemplate.build") as build:
            write_report(example_state(), "Proyecto RCD", settings, self.folder / "spaced.pdf")
        story = build.call_args.args[0]
        self.assertGreaterEqual(sum(isinstance(item, Spacer) and item.height == 12 for item in story), 7)
        self.assertIsInstance(story[-1], KeepTogether)
        self.assertEqual(story[-1]._content[0].height, 24)

    def test_pdf_and_odt_contain_budget_rows_and_configured_template(self):
        template = self.folder / "custom.odt"
        with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as target:
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename == "styles.xml":
                    root = ElementTree.fromstring(data)
                    ns = {"s": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
                          "t": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}
                    paragraph = root.find(".//s:footer/t:p", ns)
                    assert paragraph is not None
                    paragraph.clear()
                    paragraph.text = "PIE PERSONALIZADO RCD"
                    data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
                target.writestr(entry, data)
        state = example_state()
        state.evidence["40"] = ["DEM: Demolición de hormigón"]
        settings = ReportSettings(template_path=str(template), author="Autor de prueba RCD")
        pdf = self.folder / "rcd.pdf"
        write_report(state, "Proyecto RCD", settings, pdf)
        reader = PdfReader(pdf)
        text = "\n".join(page.extract_text() for page in reader.pages)
        for label in ("13.044,62", "10.937,66", "B1.", "B2.", "B3.", "Proyecto RCD", "Autor de prueba RCD",
                      "17 01 01", "DEM: Demolición de hormigón"):
            self.assertIn(label, text)
        self.assertTrue(all("PIE PERSONALIZADO RCD" in page.extract_text() for page in reader.pages))
        self.assertTrue(any(page.images for page in reader.pages))
        settings.output_format = "odt"
        odt = self.folder / "rcd.odt"
        write_report(state, "Proyecto RCD", settings, odt)
        with ZipFile(odt) as archive:
            self.assertEqual(archive.read("mimetype"), b"application/vnd.oasis.opendocument.text")
            content = ElementTree.fromstring(archive.read("content.xml"))
            text = " ".join(content.itertext())
            self.assertIn("13.044,62", text)
            self.assertIn("TOTAL PRESUPUESTO PLAN GESTIÓN RCDs", text)
            styles = ElementTree.fromstring(archive.read("styles.xml"))
            self.assertIn("PIE PERSONALIZADO RCD", " ".join(styles.itertext()))
            image = next(name for name in archive.namelist() if name.startswith("Pictures/"))
            self.assertEqual(archive.read(image), _read_report_template(str(template))[0])
            tables = content.findall(".//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table")
            self.assertEqual(len(tables), 7)
            self.assertIn(HEADERS[0], text)
            self.assertIn("modelo revisado", text.lower())

    def test_missing_template_or_invalid_input_does_not_generate_report(self):
        for output in ("pdf", "odt"):
            settings = ReportSettings(output_format=output, template_path=str(self.folder / "missing.odt"))
            target = self.folder / f"bad.{output}"
            with self.subTest(output=output), self.assertRaises(ValueError):
                write_report(example_state(), "Proyecto", settings, target)
            self.assertFalse(target.exists())

    def test_measured_demolition_provenance_in_both_report_formats(self):
        state = example_state()
        state.demolition = [DemolitionMeasurement("DEM", "DEM: Pavimento zona norte",
                                                "m²", 50, "concrete", 0.2, True, "40")]
        for output in ("pdf", "odt"):
            target = self.folder / f"demolition.{output}"
            write_report(state, "Proyecto", ReportSettings(output_format=output), target)
            if output == "pdf":
                text = "\n".join(page.extract_text() for page in PdfReader(target).pages)
            else:
                with ZipFile(target) as archive:
                    text = " ".join(ElementTree.fromstring(archive.read("content.xml")).itertext())
            text = " ".join(text.split())
            for value in ("DEM: Pavimento zona norte", "50,000 m", "10,00", "0,200",
                          "no de porcentajes del ODS"):
                self.assertIn(value, text)


class RcdWindowTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with (
            patch("bc3_gui.user_data_directory", return_value=self.folder),
            patch.object(BudgetApp, "_read_recent_projects", return_value=[]),
            patch.object(self.root, "state"),
        ):
            self.app = BudgetApp(self.root)
        set_items(self.app)

    def open(self):
        self.app.open_rcds()
        window = self.app._rcd_window
        assert isinstance(window, RcdWindow)
        return window

    def accept(self, window):
        window.variables["surface"].set("100")
        window.accept_inputs()

    def test_first_tools_entry_and_empty_project_guard(self):
        button = next(child for child in self.root.grid_slaves(row=0, column=0)[0].winfo_children()
                      if isinstance(child, ttk.Menubutton) and child.cget("text") == "Herramientas")
        menu = self.root.nametowidget(button.cget("menu"))
        self.assertEqual(menu.entrycget(0, "label"), "RCDs")
        menu.invoke(0)
        first = self.app._rcd_window
        self.app.open_rcds()
        self.assertIs(self.app._rcd_window, first)
        self.app.budget_items = []
        with patch("bc3_gui.messagebox.showinfo") as info:
            self.app.open_rcds()
            info.assert_called_once()

    def test_two_steps_manual_toggle_and_budget_refresh(self):
        window = self.open()
        self.assertEqual(window.variables["surface"].get(), "")
        self.assertEqual(window.variables["earth_volume"].get(), "10.0")
        with patch("rcd.messagebox.showerror") as error:
            window.accept_inputs()
            error.assert_called_once()
            self.assertFalse(hasattr(window, "tree"))
        self.accept(window)
        self.assertEqual(len(window.tree.get_children()), 57)
        self.assertEqual(window.tree.set("40", "x"), "X")
        self.assertEqual(window.tree.set("15", "x"), "")
        self.assertEqual(len(window.preview.get_children()), 8)
        previous = window.status.cget("text")
        window.tree.selection_set("15")
        window.toggle_selected()
        self.assertEqual(window.tree.set("15", "x"), "X")
        self.assertTrue(self.app.rcd_state["selected"]["15"])
        self.assertNotEqual(window.status.cget("text"), previous)
        evidence = window.tree.set("40", "evidence")
        with patch.object(window.tree, "identify_row", return_value="40"), patch.object(
            window.tree, "identify_column", return_value="#1"
        ):
            window.click_waste(SimpleNamespace(x=1, y=1))
        self.assertFalse(window.data.selected["40"])
        self.assertEqual(window.tree.set("40", "evidence"), evidence)
        window.show_inputs()
        self.assertFalse(window.bind("<Control-s>"))
        window.accept_inputs()
        self.assertFalse(window.data.selected["40"])
        self.assertTrue(window.data.selected["15"])

    def test_save_reload_and_manual_selection_is_not_redetected(self):
        window = self.open()
        self.accept(window)
        window.tree.selection_set("15")
        window.toggle_selected()
        window.tree.selection_set("40")
        window.toggle_selected()
        project = self.folder / "project.json"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(project)):
            window.save_button.invoke()
        self.assertFalse(self.app.is_dirty)
        self.assertFalse(window._dirty)
        self.assertTrue(self.app._load_project_file(project))
        self.assertFalse(window.winfo_exists())
        reopened = self.open()
        self.assertEqual(reopened.variables["surface"].get(), "100.0")
        reopened.accept_inputs()
        self.assertTrue(reopened.data.selected["15"])
        self.assertFalse(reopened.data.selected["40"])
        self.assertTrue(reopened.bind("<Control-s>"))
        with patch("bc3_gui.simpledialog.askstring", return_value="Nuevo"):
            self.app.new_project()
        self.assertEqual(self.app.rcd_state, {})
        self.assertFalse(reopened.winfo_exists())

    def test_cancelled_save_and_invalid_saved_state(self):
        window = self.open()
        self.accept(window)
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=""):
            window.save()
        self.assertTrue(window._dirty)
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(len([item for item in self.app.budget_items
                              if item.source.startswith(RCD_BUDGET_SOURCE)]), 7)
        self.assertEqual(window.save_button.cget("background"), "#000000")
        old = self.folder / "old.json"
        old.write_text('{"items": [{"code": "P", "chapter": "01#"}]}', encoding="utf-8")
        self.assertTrue(self.app._load_project_file(old))
        self.assertEqual(self.app.rcd_state, {})
        old.write_text(json.dumps({"items": [], "rcds": {"selected": {"5": "X"}}}), encoding="utf-8")
        with patch("bc3_gui.messagebox.showerror") as error:
            self.assertFalse(self.app._load_project_file(old))
            error.assert_called_once()

    def test_saving_rcds_adds_levels_typologies_and_exact_direct_budget_to_project(self):
        window = self.open()
        self.accept(window)
        window.data = example_state()
        manual = list(self.app.budget_items)
        previous_total = sum(item.import_total for item in manual)
        project = self.folder / "rcd-budget.json"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(project)):
            window.save_button.invoke()
        generated = [item for item in self.app.budget_items if item.source.startswith(RCD_BUDGET_SOURCE)]
        self.assertEqual(len(generated), 7)
        chapter = generated[0].chapter
        self.assertEqual(self.app.chapter_titles[chapter], "Gestión de residuos")
        self.assertEqual(self.app.chapter_order[-1], chapter)
        self.assertTrue(all(item.chapter == chapter for item in generated))
        self.assertEqual([(item.measurement, item.unit_price) for item in generated[:4]],
                         [(1853.84, 5.9), (121.14, 8.61), (29.91, 21.13), (0, 22.25)])
        self.assertEqual([item.unit for item in generated], ["m³"] * 4 + ["ud"] * 3)
        self.assertIn("NIVEL I", generated[0].summary)
        self.assertTrue(all("NIVEL II" in item.summary for item in generated[1:4]))
        self.assertTrue(all(item.summary == item.summary.upper() and
                            item.text == item.text.upper() for item in generated))
        self.assertEqual([item.unit_price for item in generated[4:]], [0, 10.55, 421.39])
        self.assertEqual(round(sum(item.import_total for item in generated), 2), 13044.62)
        self.assertEqual(round(sum(item.import_total for item in self.app.budget_items) - previous_total, 2),
                         13044.62)
        self.assertTrue(all(item in self.app.budget_items for item in manual))
        self.assertEqual(len(self.app.budget_tree.get_children(self.app.chapter_iid_by_code[chapter])), 7)
        self.assertIn("13.044,62", self.app.budget_tree.set(self.app.chapter_iid_by_code[chapter], "importe"))
        self.assertFalse(window._dirty)
        self.assertTrue(self.app._load_project_file(project))
        restored = [item for item in self.app.budget_items if item.source.startswith(RCD_BUDGET_SOURCE)]
        self.assertEqual(len(restored), 7)
        self.assertEqual(round(sum(item.import_total for item in restored), 2), 13044.62)
        self.assertEqual(restored[1].measurement_lines[0].units, 121.14)
        reopened = self.open()
        reopened.accept_inputs()
        reopened.save()
        self.assertEqual(len([item for item in self.app.budget_items
                              if item.source.startswith(RCD_BUDGET_SOURCE)]), 7)

    def test_rcd_budget_sync_preserves_manual_rows_and_certifications_without_collisions(self):
        self.app.chapter_order.append("RCD#")
        self.app.chapter_titles["RCD#"] = "Capítulo manual"
        manual = ProjectItem("RCD-EARTH", "RCD#", "ud", "Partida manual", unit_price=99)
        self.app.budget_items.append(manual)
        state = example_state()
        synchronize_budget(self.app, state)
        generated = [item for item in self.app.budget_items if item.source.startswith(RCD_BUDGET_SOURCE)]
        earth = generated[0]
        self.assertNotEqual(earth.code, manual.code)
        self.assertEqual(earth.chapter, "RCD2#")
        earth.certifications["2026-10"] = 5
        earth.planned_amounts["2026-10"] = 100
        extra = ProjectItem("EXTRA", earth.chapter, "ud", "Coste manual adicional", unit_price=123)
        self.app.budget_items.append(extra)
        state.inputs["earth_volume"] = 100
        state.inputs["earth_rate"] = 7
        state.selected["40"] = False
        synchronize_budget(self.app, state)
        self.assertEqual(earth.measurement, 100)
        self.assertEqual(earth.unit_price, 7)
        self.assertEqual(earth.certifications, {"2026-10": 5})
        self.assertEqual(earth.planned_amounts, {"2026-10": 100})
        self.assertLess(generated[1].measurement, 121.14)
        self.assertEqual(extra.unit_price, 123)
        self.assertEqual(manual.unit_price, 99)
        self.assertEqual(self.app.chapter_titles["RCD#"], "Capítulo manual")
        self.assertEqual(len({item.code for item in self.app.budget_items}), len(self.app.budget_items))
        self.assertEqual(self.app.chapter_order.count(earth.chapter), 1)

    def test_regular_project_save_updates_existing_generated_rcd_budget(self):
        window = self.open()
        self.accept(window)
        self.app.project_path = self.folder / "parent-save.json"
        window.save()
        generated = [item for item in self.app.budget_items if item.source.startswith(RCD_BUDGET_SOURCE)]
        window.tree.selection_set("40")
        window.toggle_selected()
        self.assertTrue(self.app.save_project())
        self.assertEqual(generated[1].measurement, 0)
        data = json.loads(self.app.project_path.read_text(encoding="utf-8"))
        stone = next(item for item in data["items"] if item["source"] == "RCD:stone")
        self.assertEqual(stone["measurement"], 0)

    def test_rcd_budget_indirect_costs_are_applied_once_and_generated_costs_not_recovered_as_base(self):
        state = example_state()
        previous_budget = initial_state(self.app).inputs["budget"]
        self.app.report_settings.indirect_costs = 5
        synchronize_budget(self.app, state)
        generated = [item for item in self.app.budget_items if item.source.startswith(RCD_BUDGET_SOURCE)]
        direct_prices = [item.direct_unit_price for item in generated]
        effective_prices = [item.unit_price for item in generated]
        self.assertTrue(all(item.indirect_costs == 5 for item in generated))
        self.assertEqual(direct_prices[:4], [5.9, 8.61, 21.13, 22.25])
        self.assertEqual(initial_state(self.app).inputs["budget"], previous_budget)
        synchronize_budget(self.app, state)
        self.assertEqual([item.unit_price for item in generated], effective_prices)
        self.assertEqual([item.direct_unit_price for item in generated], direct_prices)

    def test_invalid_rcd_state_does_not_create_budget_chapter_and_failed_save_remains_dirty(self):
        window = self.open()
        self.accept(window)
        previous = self.app._project_payload()
        window.data.inputs["budget"] = 0
        with patch("rcd.messagebox.showerror") as error, patch.object(self.app, "save_project") as save:
            window.save()
            error.assert_called_once()
            save.assert_not_called()
        self.assertEqual(self.app._project_payload(), previous)
        window.data.inputs["budget"] = 1200
        with patch.object(self.app, "save_project", return_value=False):
            window.save()
        self.assertTrue(window._dirty)
        self.assertTrue(self.app.is_dirty)
        self.assertNotIn("guardados", window.status.cget("text"))

    def test_generate_both_configured_formats_and_atomic_failure(self):
        window = self.open()
        self.accept(window)
        for output in ("pdf", "odt"):
            self.app.report_settings.output_format = output
            path = self.folder / f"report.{output}"
            with (
                patch("rcd.filedialog.asksaveasfilename", return_value=str(path)),
                patch("rcd.os.startfile") as open_file,
            ):
                window.generate_report()
                open_file.assert_called_once_with(str(path))
            self.assertTrue(path.is_file())
            old = path.read_bytes()
            with (
                patch("rcd.filedialog.asksaveasfilename", return_value=str(path)),
                patch("rcd.write_report", side_effect=ValueError("Plantilla no válida")),
                patch("rcd.messagebox.showerror") as error,
            ):
                window.generate_report()
                error.assert_called_once()
            self.assertEqual(path.read_bytes(), old)
        self.assertFalse(any(path.name.startswith("tmp") for path in self.folder.iterdir()))

    def test_demolition_detection_preserves_lines_and_ignores_subtotals_transport_and_supplies(self):
        item = self.app.budget_items[1]
        item.measurement_lines = [
            MeasurementLine(comment="Zona norte", units=2, length=3, width=4, k=0.5),
            MeasurementLine(comment="Descuento", units=-2),
            MeasurementLine(comment="Subtotal", units=999, ltype="1"),
        ]
        self.app.budget_items.append(ProjectItem("T", "02#", "m³",
                                                "Transporte de demolición a vertedero", measurement=99))
        rows = demolition_measurements(self.app, RcdState())
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.quantity for row in rows], [12, -2])
        self.assertIn("Zona norte", rows[0].source)
        self.assertTrue(all(row.factor is None and row.waste_key == "40" for row in rows))
        self.assertEqual(item.measurement_lines[-1].units, 999)

    def test_demolition_description_can_include_transport_without_importing_transport_items(self):
        self.app.budget_items = [
            ProjectItem("DEM", "02#", "m³", "Derribo de hormigón con transporte a vertedero",
                        measurement=4),
            ProjectItem("T", "02#", "m³", "Transporte a vertedero de residuos de demolición",
                        measurement=8),
        ]
        rows = demolition_measurements(self.app, RcdState())
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].quantity, rows[0].factor, rows[0].waste_key), (4, 1, "40"))
        rows[0].factor = 1
        state = RcdState()
        state.demolition = rows
        self.app.budget_items[0].unit = "t"
        self.assertIsNone(demolition_measurements(self.app, state)[0].factor)

    def test_demolition_review_requires_factor_then_creates_transport_and_updates_calculation(self):
        window = self.open()
        button = next(child for child in window.content.winfo_children()
                      if isinstance(child, ttk.Button) and
                      child.cget("text") == "Llevar mediciones de demolición a transporte a vertedero")
        button.invoke()
        dialog = next(child for child in window.winfo_children() if isinstance(child, DemolitionDialog))
        with patch("rcd.messagebox.showerror") as error:
            dialog.apply()
            error.assert_called_once()
        self.assertEqual(len(self.app.budget_items), 4)
        dialog.edits[0][1].set("0,2")
        with patch("rcd.messagebox.showwarning") as warning:
            dialog.apply()
            self.assertIn("REVISIÓN PENDIENTE", warning.call_args.args[1])
        target = next(item for item in self.app.budget_items if item.code == "RCD-TRANS")
        self.assertEqual((target.unit, target.measurement, target.unit_price, target.rendimiento),
                         ("m³", 10, 0, 0))
        self.assertEqual(target.text, TRANSPORT_REVIEW.upper())
        self.assertEqual(target.summary, target.summary.upper())
        self.assertIn("DEM", target.measurement_lines[0].comment)
        self.assertIn("Hormigón", target.measurement_lines[0].comment)
        self.assertEqual(self.app.budget_items[1].measurement, 50)
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(window.variables["surface"].get(), "")
        window.accept_inputs()
        self.assertEqual(calculate(window.data)[0][1][1][0][1], "10,00")
        project = self.folder / "demolition.json"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(project)):
            window.save()
        self.assertTrue(self.app._load_project_file(project))
        reopened = self.open()
        self.assertEqual(reopened.data.demolition[0].factor, 0.2)
        copied = next(item for item in self.app.budget_items if item.code == "RCD-TRANS")
        self.assertEqual(copied.measurement, 10)
        self.assertIn("DEM", copied.measurement_lines[0].comment)

    def test_transfer_is_idempotent_preserves_manual_lines_prices_and_other_project_data(self):
        window = self.open()
        rows = demolition_measurements(self.app, window.data)
        rows[0].factor = 0.2
        target = ProjectItem("T", "03#", "m³", "Transporte a vertedero", measurement=7,
                             unit_price=12, rendimiento=3, decomposition=[("CAMION", 0.4)])
        self.app.budget_items.append(target)
        self.assertFalse(transfer_demolition(self.app, window.data, rows, target))
        self.assertEqual(target.measurement, 17)
        self.assertEqual(len(target.measurement_lines), 2)
        transfer_demolition(self.app, window.data, rows, target)
        self.assertEqual(target.measurement, 17)
        self.assertEqual(len(target.measurement_lines), 2)
        self.assertEqual((target.unit_price, target.rendimiento, target.decomposition),
                         (12, 3, [("CAMION", 0.4)]))
        self.app.budget_items[1].measurement = 100
        updated = demolition_measurements(self.app, window.data)
        self.assertEqual(updated[0].factor, 0.2)
        transfer_demolition(self.app, window.data, updated, target)
        self.assertEqual(target.measurement, 27)
        second = ProjectItem("T2", "03#", "m³", "Transporte a gestor", measurement=0)
        self.app.budget_items.append(second)
        transfer_demolition(self.app, window.data, updated, second)
        self.assertEqual(target.measurement, 7)
        self.assertEqual(second.measurement, 20)
        self.assertEqual(sum(line.comment.startswith(TRANSPORT_PREFIX)
                             for item in self.app.budget_items for line in item.measurement_lines), 1)

    def test_transfer_rejects_incompatible_unit_and_keeps_budget_unchanged(self):
        window = self.open()
        rows = demolition_measurements(self.app, window.data)
        rows[0].factor = 0.2
        target = ProjectItem("T", "03#", "t", "Transporte a vertedero", measurement=7)
        self.app.budget_items.append(target)
        before = self.app._project_payload()
        with self.assertRaisesRegex(ValueError, "m³"):
            transfer_demolition(self.app, window.data, rows, target)
        self.assertEqual(self.app._project_payload(), before)
        rows[0].factor = None
        with self.assertRaises(ValueError):
            transfer_demolition(self.app, window.data, rows, None)
        self.assertEqual(self.app._project_payload(), before)

    def test_excluded_measurements_are_not_copied_and_deductions_do_not_duplicate_volume(self):
        window = self.open()
        source = self.app.budget_items[1]
        source.measurement_lines = [
            MeasurementLine(comment="Positivo", units=50),
            MeasurementLine(comment="Descuento", units=-5),
            MeasurementLine(comment="Excluir", units=20),
        ]
        rows = demolition_measurements(self.app, window.data)
        for row in rows:
            row.factor = 0.2
        rows[-1].included = False
        transfer_demolition(self.app, window.data, rows, None)
        target = next(item for item in self.app.budget_items if item.code == "RCD-TRANS")
        self.assertEqual(target.measurement, 9)
        self.assertEqual(len(target.measurement_lines), 2)
        self.assertEqual(calculate(window.data)[0][1][1][0][1], "9,00")
        revised = demolition_measurements(self.app, window.data)
        self.assertFalse(revised[-1].included)
        self.assertEqual(source.measurement_lines[-1].units, 20)

    def test_direct_m3_and_unique_automatic_code_with_indirect_costs(self):
        window = self.open()
        item = self.app.budget_items[1]
        item.unit = "m3"
        self.app.budget_items.append(ProjectItem("RCD-TRANS", "03#", "ud", "Otro concepto"))
        self.app.report_settings.indirect_costs = 5
        rows = demolition_measurements(self.app, window.data)
        self.assertEqual(rows[0].factor, 1)
        transfer_demolition(self.app, window.data, rows, None)
        target = next(item for item in self.app.budget_items if item.code == "RCD-TRANS-2")
        self.assertEqual(target.measurement, 50)
        self.assertEqual(target.indirect_costs, 5)
        self.assertEqual(target.chapter, "03#")

    def test_demolition_review_no_candidates_or_multiple_targets(self):
        window = self.open()
        self.app.budget_items = [self.app.budget_items[0]]
        with patch("rcd.messagebox.showinfo") as info:
            window.review_demolition()
            info.assert_called_once()
        set_items(self.app)
        self.app.budget_items.extend([
            ProjectItem("T1", "03#", "m³", "Transporte a vertedero"),
            ProjectItem("T2", "03#", "m³", "Transporte de residuos a gestor"),
        ])
        window.review_demolition()
        dialog = next(child for child in window.winfo_children() if isinstance(child, DemolitionDialog))
        self.assertEqual(dialog.target_choice.current(), -1)
        with patch("rcd.messagebox.showerror") as error:
            dialog.apply()
            error.assert_called_once()
        dialog.destroy()
        self.assertFalse(window.data.demolition)


if __name__ == "__main__":
    unittest.main()
