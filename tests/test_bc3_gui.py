from datetime import date
import csv
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import planning

from bc3_gui import (
    Bc3Dataset,
    BudgetApp,
    Concept,
    MeasurementLine,
    ProjectItem,
    ToolTip,
    find_bc3_files,
    format_chapter_code,
    format_measurement_lines,
    parse_float,
    parse_measurement_lines,
)
from certifications import (
    CertificationWindow,
    EconomicMonth,
    certification_report_html,
    economic_months,
    planned_monthly_amounts,
    reconcile_certification,
)
from ui_theme import COLORS
from budget_reports import (
    CERTIFICATION_REPORT_NAMES, REPORT_NAMES, ReportSettings, write_certification_pdf, write_pdf,
)


BASE_DIR = Path(__file__).resolve().parent.parent
BCCA_PATH, BUDGET_PATH = find_bc3_files(BASE_DIR)


def read_ods_rows(path: Path, sheet_index: int = 0) -> list[list[object]]:
    """Lee valores de una hoja ODS para comprobar las exportaciones."""
    from odf.opendocument import load
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    document = load(str(path))
    tables = document.spreadsheet.getElementsByType(Table)
    rows = []
    for row in tables[sheet_index].getElementsByType(TableRow):
        values = []
        for cell in row.getElementsByType(TableCell):
            if cell.getAttribute("valuetype") == "float":
                values.append(float(cell.getAttribute("value")))
            else:
                paragraphs = cell.getElementsByType(P)
                values.append("".join(
                    child.data for paragraph in paragraphs for child in paragraph.childNodes
                    if child.nodeType == child.TEXT_NODE
                ))
        rows.append(values)
    return rows


class ParseFloatTests(unittest.TestCase):
    def test_accepts_bc3_decimal_formats(self):
        """Verify accepts bc3 decimal formats."""
        self.assertEqual(parse_float("3.12"), 3.12)
        self.assertEqual(parse_float("1606,4"), 1606.4)
        self.assertEqual(parse_float("1.606,40"), 1606.4)
        self.assertEqual(parse_float("1,606.40"), 1606.4)


@unittest.skipUnless(BUDGET_PATH, "Requiere el presupuesto BC3 local, no distribuido.")
class BudgetDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Load the optional local budget once for the dataset regression tests."""
        cls.dataset = Bc3Dataset(BUDGET_PATH)
        cls.items = cls.dataset.to_project_items()

    def test_reconstructs_root_chapters_and_real_work_items(self):
        """Verify reconstructs root chapters and real work items."""
        self.assertEqual(
            [format_chapter_code(code) for code in self.dataset.root_codes],
            [f"CAP. {number:02d}" for number in range(1, 10)],
        )
        self.assertEqual(len(self.items), 37)
        self.assertTrue(all(item.chapter.upper().startswith("CAP") for item in self.items))
        self.assertNotIn("TP00100", {item.code for item in self.items})

    def test_preserves_prices_measurements_and_text(self):
        """Verify preserves prices measurements and text."""
        first = next(item for item in self.items if item.code == "01UPP00082")
        self.assertEqual(format_chapter_code(first.chapter), "CAP. 01")
        self.assertEqual(first.unit_price, 3.77)
        self.assertEqual(first.measurement, 2417.13)
        self.assertEqual(first.rendimiento, 0.018)
        self.assertIn("FRESADO DE PAVIMENTO", first.text)
        self.assertTrue(first.measurement_detail)

    def test_reconstructed_total_matches_bc3_root_with_rounding_tolerance(self):
        """Verify reconstructed total matches bc3 root with rounding tolerance."""
        calculated = sum(item.import_total for item in self.items)
        declared = self.dataset.concepts["CONECTIVIDAD PP01##"].price
        self.assertAlmostEqual(calculated, declared, delta=0.02)


@unittest.skipUnless(BCCA_PATH, "Requiere la base BCCA local, no distribuida.")
class BccaDatasetTests(unittest.TestCase):
    def test_keeps_resources_at_the_last_decomposition_level(self):
        """Verify keeps resources at the last decomposition level."""
        self.assertIsNotNone(BCCA_PATH)
        dataset = Bc3Dataset(BCCA_PATH)
        item = dataset.concepts["01AAB00001"]

        self.assertEqual(
            item.children,
            [("TP00100", 0.625), ("MK00100", 0.056)],
        )
        self.assertEqual(dataset.concepts["MK00100"].summary, "CAMIÓN BASCULANTE")


class MeasurementLineTests(unittest.TestCase):
    def test_parses_fiebdc_groups_and_skips_subtotal_rows(self):
        """Verify parses fiebdc groups and skips subtotal rows."""
        detail = "\\Acometidas\\86\\1.2\\0.6\\0.6\\1\\\\\\\\\\"
        lines = parse_measurement_lines(detail)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].comment, "Acometidas")
        self.assertAlmostEqual(lines[0].partial, 37.15)

    def test_partial_multiplies_only_informed_dimensions(self):
        """Verify partial multiplies only informed dimensions."""
        self.assertAlmostEqual(MeasurementLine(units=3.0).partial, 3.0)
        self.assertAlmostEqual(MeasurementLine(units=2.0, length=5.0).partial, 10.0)
        self.assertAlmostEqual(
            MeasurementLine(units=2.0, length=5.0, width=2.0, height=0.5).partial, 10.0
        )
        self.assertEqual(MeasurementLine(comment="vacía").partial, 0.0)

    def test_round_trip_keeps_values_readable_by_the_parser(self):
        """Verify round trip keeps values readable by the parser."""
        lines = [
            MeasurementLine(comment="Tramo 1", units=1.0, length=491.45, width=0.8),
            MeasurementLine(comment="Tramo 2", units=2.0),
        ]
        self.assertEqual(
            [line.partial for line in parse_measurement_lines(format_measurement_lines(lines))],
            [line.partial for line in lines],
        )

    def test_k_scales_partial_before_rounding(self):
        """Verify k scales partial before rounding."""
        for k, expected in ((1.0, 10.0), (1.2, 12.0), (0.8, 8.0), (0.0, 0.0), (-1.0, -10.0)):
            with self.subTest(k=k):
                self.assertEqual(MeasurementLine(units=2, length=5, k=k).partial, expected)
        self.assertEqual(MeasurementLine(units=1.005, k=2).partial, 2.01)
        self.assertEqual(MeasurementLine(k=1.2).partial, 0.0)
        self.assertEqual(MeasurementLine(length=5, k=1.2).partial, 6.0)

    def test_fiebdc_detail_preserves_adjusted_quantity_with_six_fields(self):
        """Verify fiebdc detail preserves adjusted quantity with six fields."""
        lines = [
            MeasurementLine(units=2, length=5, k=1.2),
            MeasurementLine(length=5, k=0.8),
            MeasurementLine(units=2, k=0),
            MeasurementLine(comment="Empty", k=1.2),
            MeasurementLine(units=1234567, k=1.234567),
        ]
        detail = format_measurement_lines(lines)
        self.assertEqual(len(detail.split("\\")), 6 * len(lines) + 1)
        self.assertEqual(
            [line.partial for line in parse_measurement_lines(detail)],
            [line.partial for line in lines if line.comment or line.partial != 0],
        )

    def test_old_project_measurements_default_to_k_one(self):
        """Verify old project measurements default to k one."""
        item = BudgetApp._item_from_project_data({
            "code": "X", "chapter": "CAP.01#", "measurement_lines": [{"units": 2, "length": 5}],
        })
        self.assertEqual(item.measurement_lines[0].k, 1.0)
        self.assertEqual(item.recalculate_measurement(), 10.0)

    def test_recalculating_item_updates_measurement_and_import(self):
        """Verify recalculating item updates measurement and import."""
        item = ProjectItem(
            code="X",
            chapter="CAP.01#",
            unit="m3",
            summary="Prueba",
            unit_price=10.0,
            measurement_lines=[
                MeasurementLine(comment="Zanja", units=2.0, length=10.0, width=0.5, height=0.8),
                MeasurementLine(comment="Pozo", units=1.0, length=2.0, width=2.0, height=2.0),
            ],
        )
        self.assertAlmostEqual(item.recalculate_measurement(), 16.0)
        self.assertAlmostEqual(item.import_total, 160.0)

        item.measurement_lines[0].height = 1.0
        self.assertAlmostEqual(item.recalculate_measurement(), 18.0)
        self.assertAlmostEqual(item.import_total, 180.0)


@unittest.skipUnless(BUDGET_PATH, "Requiere el presupuesto BC3 local, no distribuido.")
class BudgetItemsFromBc3Tests(unittest.TestCase):
    def test_bc3_detail_lines_reproduce_declared_measurement(self):
        """Verify bc3 detail lines reproduce declared measurement."""
        dataset = Bc3Dataset(BUDGET_PATH)
        items = dataset.to_project_items()
        detailed = [item for item in items if item.measurement_lines]
        self.assertTrue(detailed)
        for item in detailed:
            with self.subTest(code=item.code):
                self.assertAlmostEqual(
                    sum(line.partial for line in item.measurement_lines),
                    item.measurement,
                    delta=0.005,
                )


class PlanningTests(unittest.TestCase):
    @staticmethod
    def _activity(**kwargs):
        """Build a synthetic planning activity with overridable fields."""
        base = dict(
            id=1, chapter="CAP. 01#", chapter_name="DEMOLICIONES", code="X", summary="Partida",
            unit="m2", quantity=100.0, amount=1000.0, rendimiento=0.5,
        )
        base.update(kwargs)
        return planning.PlanActivity(**base)

    def test_duration_uses_measurement_performance_and_teams(self):
        """Verify duration uses measurement performance and teams."""
        act = self._activity()
        self.assertEqual(act.computed_duration(8), 7)  # 100 × 0,5 / 8 = 6,25 → 7
        act.teams = 2
        self.assertEqual(act.computed_duration(8), 4)
        act.manual_duration = 3
        self.assertEqual(act.computed_duration(8), 3)

    def test_chain_sequence_links_each_activity_to_the_previous_one(self):
        """Verify chain sequence links each activity to the previous one."""
        acts = [self._activity(id=index) for index in (1, 2, 3)]
        planning.chain_sequence(acts)
        self.assertEqual([a.pred for a in acts], [None, 1, 2])
        self.assertEqual({a.relation for a in acts}, {"FC"})

    def test_schedule_skips_weekends_and_holidays(self):
        """Verify schedule skips weekends and holidays."""
        acts = [self._activity(id=1, quantity=16.0, rendimiento=1.0), self._activity(id=2, quantity=8.0, rendimiento=1.0)]
        planning.chain_sequence(acts)
        params = planning.PlanParams(
            start=date(2026, 11, 9), hours_per_day=8,
            holidays=[(date(2026, 11, 11), "Festivo local")],
        )
        planning.schedule(acts, params)
        self.assertEqual(acts[0].start_date, date(2026, 11, 9))
        self.assertEqual(acts[0].end_date, date(2026, 11, 10))
        # El 11 es festivo: la segunda actividad empieza el 12.
        self.assertEqual(acts[1].start_date, date(2026, 11, 12))
        self.assertEqual(acts[1].end_date, date(2026, 11, 12))

    def test_relations_cc_ff_and_lag(self):
        """Verify relations cc ff and lag."""
        acts = [self._activity(id=1, quantity=24.0, rendimiento=1.0), self._activity(id=2, quantity=8.0, rendimiento=1.0)]
        acts[1].pred, acts[1].relation, acts[1].lag = 1, "CC", 1
        planning.schedule(acts, planning.PlanParams(start=date(2026, 11, 9), hours_per_day=8))
        self.assertEqual(acts[1].start_offset, 1)
        acts[1].relation, acts[1].lag = "FF", 0
        planning.schedule(acts, planning.PlanParams(start=date(2026, 11, 9), hours_per_day=8))
        self.assertEqual(acts[1].finish_offset, acts[0].finish_offset)

    def test_continuous_activity_lasts_until_the_end_of_the_works(self):
        """Verify continuous activity lasts until the end of the works."""
        acts = [self._activity(id=1, quantity=40.0, rendimiento=1.0), self._activity(id=2, continuous=True)]
        planning.chain_sequence(acts)
        planning.schedule(acts, planning.PlanParams(start=date(2026, 11, 9), hours_per_day=8))
        self.assertEqual(acts[1].finish_offset, acts[0].finish_offset)
        self.assertEqual(acts[1].start_offset, 0)

    def test_continuous_activity_starts_with_the_works_despite_a_predecessor(self):
        """Verify continuous activity starts with the works despite a predecessor."""
        acts = [
            self._activity(id=1, manual_duration=5),
            self._activity(id=2, manual_duration=5, pred=1, relation="FC"),
            self._activity(id=3, continuous=True, pred=2, relation="CC"),
        ]

        planning.schedule(acts, planning.PlanParams(start=date(2026, 11, 9), hours_per_day=8))

        self.assertEqual(acts[2].start_offset, 0)
        self.assertEqual(acts[2].finish_offset, acts[1].finish_offset)

    def test_saved_continuous_activity_is_distributed_across_the_whole_plan(self):
        """Verify saved continuous activity is distributed across the whole plan."""
        acts = [
            self._activity(id=1, code="NORMAL", quantity=600.0, rendimiento=1.0, amount=1000.0),
            self._activity(id=2, code="CONTINUA", amount=1200.0),
        ]
        params = planning.PlanParams()
        planning.apply_saved_plan_state(
            params,
            acts,
            {
                "params": {
                    "start": "2026-10-01",
                    "hours_per_day": 8,
                    "scale": 7,
                    "plan": "Completo",
                    "holidays": [],
                },
                "activities": [
                    {
                        "chapter": "CAP. 01#",
                        "code": "NORMAL",
                        "teams": 1,
                        "manual_duration": None,
                        "continuous": False,
                        "pred": None,
                        "relation": "FC",
                        "lag": 0,
                    },
                    {
                        "chapter": "CAP. 01#",
                        "code": "CONTINUA",
                        "teams": 1,
                        "manual_duration": None,
                        "continuous": True,
                        "pred": 1,
                        "relation": "CC",
                        "lag": 0,
                    },
                ],
            },
        )

        amounts = planned_monthly_amounts(acts, params, economic_months(params.start, 6))[1]

        self.assertAlmostEqual(sum(amounts), 1200.0)
        self.assertGreaterEqual(sum(amount > 0 for amount in amounts), 3)


class CertificationTests(unittest.TestCase):
    def test_under_certification_moves_the_balance_to_next_month(self):
        """Verify under certification moves the balance to next month."""
        plan = [100.0, 40.0, 0.0]

        remainder = reconcile_certification(plan, 0, 75.0)

        self.assertEqual(remainder, 0.0)
        self.assertEqual(plan, [75.0, 65.0, 0.0])

    def test_over_certification_reduces_later_plan_without_negative_values(self):
        """Verify over certification reduces later plan without negative values."""
        plan = [100.0, 25.0, 30.0]

        remainder = reconcile_certification(plan, 0, 140.0)

        self.assertEqual(remainder, 0.0)
        self.assertEqual(plan, [140.0, 0.0, 15.0])

    def test_uncovered_over_certification_is_reported_for_justification(self):
        """Verify uncovered over certification is reported for justification."""
        plan = [100.0, 10.0]

        remainder = reconcile_certification(plan, 0, 125.0)

        self.assertEqual(remainder, 15.0)
        self.assertEqual(plan, [125.0, 0.0])

    def test_report_contains_only_nonzero_certified_items(self):
        """Verify report contains only nonzero certified items."""
        certified = ProjectItem(
            code="CERT", chapter="CAP.01#", unit="m2", summary="Partida certificada",
            certifications={"2026-11-01": 75.0}, certification_plans={"2026-11-01": 100.0},
            certification_justifications={"2026-11-01": "Medición ejecutada"},
        )
        empty = ProjectItem(
            code="ZERO", chapter="CAP.01#", unit="m2", summary="Partida sin certificar",
        )
        activities = [
            planning.PlanActivity(
                id=index,
                chapter=item.chapter,
                chapter_name="Capítulo de prueba",
                code=item.code,
                summary=item.summary,
                unit=item.unit,
                quantity=1.0,
                amount=100.0,
                rendimiento=0.0,
            )
            for index, item in enumerate((certified, empty), start=1)
        ]

        report = certification_report_html(
            [certified, empty],
            activities,
            [[100.0], [50.0]],
            EconomicMonth(date(2026, 11, 1), date(2026, 11, 30)),
            1,
            "Proyecto de prueba",
        )

        self.assertIn("Partida certificada", report)
        self.assertIn("Medición ejecutada", report)
        self.assertNotIn("Partida sin certificar", report)


class ReportsAndRecentProjectsTests(unittest.TestCase):
    def setUp(self):
        """Create isolated fixtures and register cleanup for this test case."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with (
            patch.object(BudgetApp, "_read_recent_projects", return_value=[]),
            patch("bc3_gui.user_data_directory", return_value=self.directory),
            patch.object(self.root, "state"),
        ):
            self.app = BudgetApp(self.root)
        self.app.recent_projects_path = self.directory / "proyectos_recientes.json"
        self.app.report_settings_path = self.directory / "configuracion_informes.json"

    def set_report_items(self):
        """Create synthetic work items and resource prices for report tests."""
        dataset = Bc3Dataset.__new__(Bc3Dataset)
        dataset.concepts = {
            "MO": Concept("MO", "h", "Operario", 10.0, "1"),
            "MAT": Concept("MAT", "kg", "Material", 3.0, "3"),
        }
        self.app.budget_dataset = dataset
        self.app.project_title = "Proyecto <prueba>"
        self.app.budget_items = [
            ProjectItem(
                code="P1", chapter="CAP.01#", unit="m2", summary="Partida <uno>",
                measurement=100, unit_price=32, source="BC3",
                decomposition=[("MO", 2), ("MAT", 1)], component_prices={"MO": 14},
            ),
            ProjectItem(
                code="P2", chapter="CAP.01#", unit="m2", summary="Partida dos",
                unit_price=23, source="BC3", decomposition=[("MO", 2), ("MAT", 1)],
            ),
            ProjectItem(code="P3", chapter="CAP.01#", unit="ud", summary="Partida sin descomponer", unit_price=5),
        ]

    def test_theme_colors_keep_selection_and_input_states_readable(self):
        """Verify theme colors keep selection and input states readable."""
        style = ttk.Style(self.root)
        self.assertEqual(style.theme_use(), "clam")
        self.assertEqual(style.lookup("Treeview", "rowheight"), 24)
        for state in (("selected",), ("selected", "focus")):
            self.assertEqual(style.lookup("Treeview", "background", state), COLORS["blue"])
            self.assertEqual(style.lookup("Treeview", "foreground", state), COLORS["surface"])
        self.assertEqual(
            style.lookup("TCombobox", "fieldbackground", ("readonly",)), COLORS["surface"]
        )
        self.assertEqual(
            style.lookup("Accent.TButton", "foreground", ("disabled",)), COLORS["muted"]
        )
        self.assertEqual(self.app.description_text.cget("background"), COLORS["surface"])
        self.assertEqual(self.app.description_text.cget("selectbackground"), COLORS["blue"])
        self.assertEqual(self.app.budget_summary.cget("style"), "Total.TLabel")
        self.assertEqual(
            style.lookup("Reference.Section.TLabel", "background"), COLORS["teal"]
        )

    def test_application_name_has_small_icon_and_explicit_bold_font(self):
        """Verify application name has small icon and explicit bold font."""
        label = self.app.application_label
        self.assertEqual(label.cget("text"), "Gestor de Proyectos (v1.0.0)")
        self.assertEqual(str(label.cget("compound")), "left")
        self.assertTrue(label.cget("image"))
        self.assertEqual(self.app._toolbar_icon.width(), 24)
        self.assertEqual(self.app._toolbar_icon.height(), 24)
        self.assertEqual(tkfont.Font(root=self.root, font=label.cget("font")).actual("weight"), "bold")
        from PIL import Image, ImageTk

        actual = ImageTk.getimage(self.app._toolbar_icon).convert("RGBA")
        with Image.open(BASE_DIR / "iconos" / "logo.ico") as source:
            expected_alpha = source.convert("RGBA").resize(
                (24, 24), Image.Resampling.LANCZOS
            ).getchannel("A")
        self.assertEqual(actual.getchannel("A").tobytes(), expected_alpha.tobytes())
        visible = [pixel for pixel in actual.get_flattened_data() if pixel[3] > 0]
        self.assertTrue(visible)
        self.assertTrue(all(pixel[:3] == (255, 255, 255) for pixel in visible))

    def test_window_title_stays_application_after_status_updates(self):
        """Keep the window-frame title distinct from the toolbar application name."""
        self.assertEqual(self.root.title(), "Aplicación")
        self.app._set_status("Proyecto guardado.")
        self.assertEqual(self.root.title(), "Aplicación")
        self.assertEqual(self.app.application_label.cget("text"), "Gestor de Proyectos (v1.0.0)")

    def test_information_menu_opens_mit_license_and_responsibility_notice(self):
        """Verify information menu opens mit license and responsibility notice."""
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        button = next(child for child in toolbar.winfo_children()
                      if isinstance(child, ttk.Menubutton) and child.cget("text") == "Información")
        menu = self.root.nametowidget(button.cget("menu"))
        self.assertEqual(menu.entrycget(0, "label"), "Gestor de Proyectos (v1.0.0)")
        index = next(i for i in range(menu.index("end") + 1)
                     if menu.type(i) == "command" and
                     menu.entrycget(i, "label") == "Licencia y responsabilidad…")
        self.assertEqual(menu.entrycget(index, "state"), "normal")
        with patch("bc3_gui.messagebox.showinfo") as info:
            menu.invoke(index)
            info.assert_called_once()
            title, notice = info.call_args.args
            self.assertIn("MIT", title)
            for content in (
                "Software libre", "Licencia MIT", "copyright", "sin garantías",
                "LIMITACIÓN DE RESPONSABILIDAD", "revisión técnica",
                "https://opensource.org/license/mit",
            ):
                self.assertIn(content, notice)
            self.assertEqual(info.call_args.kwargs["parent"], self.root)

    def test_child_windows_share_theme_and_keep_dirty_save_indicator(self):
        """Verify child windows share theme and keep dirty save indicator."""
        self.set_report_items()
        with patch.object(tk.Toplevel, "state"):
            plan = planning.PlanningWindow(
                self.root, self.app.budget_items, self.app._chapter_title, "Prueba"
            )
            self.addCleanup(plan.destroy)
            certification = CertificationWindow(
                self.root, self.app.budget_items, self.app._chapter_title, "Prueba"
            )
            self.addCleanup(certification.destroy)
        self.root.update_idletasks()
        for window in (plan, certification):
            self.assertEqual(window.save_button.cget("background"), COLORS["teal_soft"])
            window._set_dirty(True)
            self.assertEqual(window.save_button.cget("background"), "#000000")
            window._set_dirty(False)
            self.assertEqual(window.save_button.cget("background"), COLORS["teal_soft"])
        self.assertEqual(certification.justification.cget("background"), COLORS["surface"])
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup("Certification.Treeview", "rowheight"), 36)
        self.assertEqual(style.lookup("Treeview", "rowheight"), 24)

    def set_measurement_item(self):
        """Select a synthetic work item with an editable measurement line."""
        self.set_report_items()
        item = self.app.budget_items[0]
        item.measurement_lines = [MeasurementLine(comment="Test", units=2, length=5)]
        item.recalculate_measurement()
        self.app.refresh_budget_tree()
        self.app.selected_budget_index = 0
        self.app._show_item_details(item)
        return item

    def test_k_column_edits_recalculate_partial_budget_and_reset_blank_to_one(self):
        """Verify k column edits recalculate partial budget and reset blank to one."""
        item = self.set_measurement_item()
        columns = self.app.measurement_tree["columns"]
        self.assertEqual(columns[-2:], ("k", "parcial"))
        self.assertEqual(self.app.measurement_tree.heading("k", "text"), "K")
        for text, k, total in (("1,2", 1.2, 12), ("0.8", 0.8, 8), ("0", 0, 0), ("", 1, 10)):
            with self.subTest(text=text):
                self.app._commit_measurement_cell("0", "#6", text)
                self.assertEqual(item.measurement_lines[0].k, k)
                self.assertEqual(item.measurement, total)
                self.assertEqual(item.import_total, total * item.unit_price)
                self.assertEqual(self.app.measurement_tree.set("0", "k"), f"{k:g}")
                self.assertEqual(float(self.app.measurement_tree.set("0", "parcial")), total)
        for text in ("invalid", "nan", "inf"):
            with self.subTest(text=text), patch("bc3_gui.messagebox.showerror") as error:
                self.app._commit_measurement_cell("0", "#6", text)
                error.assert_called_once()
                self.assertEqual(item.measurement_lines[0].k, 1)
        with (
            patch.object(self.app.measurement_tree, "identify_row", return_value="0"),
            patch.object(self.app.measurement_tree, "identify_column", return_value="#7"),
            patch.object(self.app, "_close_measurement_editor") as close,
        ):
            event = tk.Event()
            event.x, event.y = 0, 0
            self.assertEqual(self.app.edit_measurement_cell(event), "break")
            close.assert_not_called()

    def test_k_survives_project_save_and_reopen(self):
        """Verify k survives project save and reopen."""
        item = self.set_measurement_item()
        self.app._commit_measurement_cell("0", "#6", "1.2")
        target = self.directory / "k-project.json"
        self.assertTrue(self.app._write_project_file(target))
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["items"][0]["measurement_lines"][0]["k"], 1.2)
        self.app.budget_items = []
        self.assertTrue(self.app._load_project_file(target))
        reopened = self.app.budget_items[0]
        self.assertEqual(reopened.measurement_lines[0].k, 1.2)
        self.assertEqual(reopened.recalculate_measurement(), 12)
        self.assertEqual(reopened.import_total, item.import_total)

    def test_measurement_single_click_edits_recalculates_marks_dirty_and_persists(self):
        """Verify measurement single click edits recalculates marks dirty and persists."""
        item = self.set_measurement_item()
        self.root.deiconify()
        self.root.update()
        self.app.budget_tree.selection_set("item:0")
        self.root.update()
        self.app.is_dirty = False
        tree = self.app.measurement_tree
        x, y, width, height = tree.bbox("0", "#3")
        tree.event_generate("<ButtonPress-1>", x=x + width // 2, y=y + height // 2)
        tree.event_generate("<ButtonRelease-1>", x=x + width // 2, y=y + height // 2)
        self.root.update()
        editor = self.app._measurement_editor
        self.assertIsNotNone(editor)
        editor.focus_force()
        self.root.update()
        editor.delete(0, "end")
        editor.insert(0, "8,5")
        editor.event_generate("<Return>")
        self.root.update()
        self.assertIsNone(self.app._measurement_editor)
        self.assertEqual(item.measurement_lines[0].length, 8.5)
        self.assertEqual(item.measurement, 17)
        self.assertEqual(item.import_total, 17 * item.unit_price)
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(self.app.budget_fields["measurement_var"].get(), "17.0000")
        target = self.directory / "edited-measurement.json"
        self.assertTrue(self.app._write_project_file(target))
        self.assertFalse(self.app.is_dirty)
        self.assertTrue(self.app._load_project_file(target))
        self.assertEqual(self.app.budget_items[0].measurement_lines[0].length, 8.5)
        self.assertEqual(self.app.budget_items[0].measurement, 17)

    def test_measurement_focus_out_commits_and_escape_cancels(self):
        """Verify measurement focus out commits and escape cancels."""
        item = self.set_measurement_item()
        self.root.deiconify()
        self.root.update()
        self.app.budget_tree.selection_set("item:0")
        self.root.update()
        tree = self.app.measurement_tree
        self.assertFalse(tree.bind("<Enter>"))
        for event_name, value, expected in (("<Escape>", "20", 5), ("<FocusOut>", "7", 7)):
            with self.subTest(event=event_name):
                self.app.is_dirty = False
                x, y, width, height = tree.bbox("0", "#3")
                event = tk.Event()
                event.x, event.y = x + width // 2, y + height // 2
                self.app.edit_measurement_cell(event)
                self.root.update()
                editor = self.app._measurement_editor
                editor.focus_force()
                self.root.update()
                editor.delete(0, "end")
                editor.insert(0, value)
                editor.event_generate(event_name)
                self.root.update()
                self.assertEqual(item.measurement_lines[0].length, expected)
                self.assertEqual(self.app.is_dirty, event_name == "<FocusOut>")

    def test_adding_and_removing_measurements_marks_project_dirty(self):
        """Verify adding and removing measurements marks project dirty."""
        item = self.set_measurement_item()
        self.app.is_dirty = False
        self.app.add_measurement_line()
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(len(item.measurement_lines), 2)
        self.app.is_dirty = False
        self.app.delete_measurement_line()
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(len(item.measurement_lines), 1)

    def test_measurement_exports_include_k_and_adjusted_partial(self):
        """Verify measurement exports include k and adjusted partial."""
        self.set_measurement_item()
        self.app._commit_measurement_cell("0", "#6", "1.2")
        csv_path = self.directory / "k-measurements.csv"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(csv_path)):
            self.app.export_measurements_csv()
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertEqual(rows[0][-2:], ["K", "Parcial"])
        self.assertEqual(rows[1][-2:], ["1.2", "12.0"])
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        self.assertEqual(rows[2][-2], "")

        selected_path = self.directory / "k-budget.xlsx"
        ods_path = selected_path.with_suffix(".ods")
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(selected_path)):
            self.app.export_budget_ods()
        self.assertTrue(ods_path.is_file())
        sheet = read_ods_rows(ods_path, sheet_index=1)
        self.assertEqual(sheet[0][-2:], ["K", "Parcial"])
        self.assertEqual(sheet[1][-2:], [1.2, 12.0])

    def test_recent_menu_is_first_and_retains_five_unique_absolute_paths(self):
        """Verify recent menu is first and retains five unique absolute paths."""
        self.assertEqual(self.app.file_menu.entrycget(0, "label"), "Recientes")
        for number in range(6):
            self.app._remember_recent_project(self.directory / f"proyecto{number}.json")
        latest = self.directory / "proyecto3.json"
        self.app._remember_recent_project(latest)
        expected = [self.directory / f"proyecto{number}.json" for number in (3, 5, 4, 2, 1)]
        self.assertEqual(self.app.recent_projects, expected)
        self.assertEqual(self.app._read_recent_projects(), expected)
        self.assertEqual(self.app.file_menu.type(6), "separator")
        self.assertEqual(self.app.file_menu.entrycget(7, "label"), "Nuevo proyecto")
        with patch.object(self.app, "_load_project_file") as load:
            self.app.file_menu.invoke(1)
            load.assert_called_once_with(latest)

    def test_icon_buttons_have_hover_help_without_replacing_commands(self):
        """Verify icon buttons have hover help without replacing commands."""
        actions = self.root.grid_slaves(row=1, column=0)[0]
        buttons = actions.winfo_children()
        self.assertEqual(len(buttons), 7)
        for button in buttons:
            self.assertTrue(button.bind("<Enter>"))
            self.assertTrue(button.bind("<Leave>"))
            self.assertTrue(button.bind("<ButtonPress>"))
            self.assertTrue(button.cget("command"))

    def test_tooltip_shows_without_focus_and_hides_on_leave_click_and_destroy(self):
        """Verify tooltip shows without focus and hides on leave click and destroy."""
        self.root.deiconify()
        button = tk.Button(self.root, text="Test")
        button.grid(row=3, column=0)
        self.root.update()
        tooltip = ToolTip(button, "Ayuda de prueba")
        for event in ("<Leave>", "<ButtonPress-1>"):
            tooltip._schedule()
            self.assertIsNotNone(tooltip._after_id)
            button.event_generate(event)
            self.assertIsNone(tooltip._after_id)
            tooltip._show()
            self.assertIsNotNone(tooltip.window)
            self.assertEqual(tooltip.window.winfo_children()[0].cget("text"), "Ayuda de prueba")
            button.event_generate(event)
            self.assertIsNone(tooltip.window)
        tooltip._schedule()
        button.destroy()
        self.assertIsNone(tooltip._after_id)
        self.assertIsNone(tooltip.window)

    def test_reports_menu_contains_and_executes_all_template_options(self):
        """Verify reports menu contains and executes all template options."""
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        button = next(child for child in toolbar.winfo_children() if child.cget("text") == "Informes")
        menu = self.root.nametowidget(button.cget("menu"))
        self.assertEqual(
            [menu.entrycget(index, "label") for index in range(menu.index("end") + 1)],
            list(REPORT_NAMES),
        )
        with patch.object(self.app, "generate_report") as generate:
            for index, name in enumerate(REPORT_NAMES):
                menu.invoke(index)
                generate.assert_called_with(name)

    def test_spreadsheet_export_menu_uses_open_ods_format(self):
        """Verify all spreadsheet exports in the app menu use ODS."""
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        button = next(
            child for child in toolbar.winfo_children()
            if child.cget("text") == "Imprimir y exportar"
        )
        menu = self.root.nametowidget(button.cget("menu"))
        labels = [
            menu.entrycget(index, "label")
            for index in range(menu.index("end") + 1)
            if menu.type(index) == "command"
        ]
        self.assertEqual(
            [label for label in labels if "ODS" in label],
            [
                "Exportar presupuesto a ODS…",
                "Exportar recursos a ODS…",
                "Exportar descompuestos a ODS…",
            ],
        )

    def test_settings_validate_save_reload_and_follow_the_project(self):
        """Verify settings validate save reload and follow the project."""
        self.set_report_items()
        project = self.directory / "settings-project.json"
        self.assertTrue(self.app._write_project_file(project))
        self.assertTrue(self.app._save_report_settings({
            "author": "Autor de prueba", "role": "Ingeniería", "location": "Lucena",
            "general_expenses": "13,5", "industrial_profit": "6", "vat": "21",
        }))
        self.assertEqual(self.app._read_report_settings().author, "Autor de prueba")
        self.assertEqual(self.app.report_settings.general_expenses, 13.5)
        self.app.report_settings = ReportSettings(author="Otro")
        self.assertTrue(self.app._load_project_file(project))
        self.assertEqual(self.app.report_settings.author, "Autor de prueba")
        previous = self.app.report_settings_path.read_bytes()
        for value in ("nan", "inf", "-1", "101", "abc", ""):
            with self.subTest(value=value), patch("bc3_gui.messagebox.showerror") as error:
                self.assertFalse(self.app._save_report_settings({"vat": value}))
                error.assert_called_once()
                self.assertEqual(self.app.report_settings_path.read_bytes(), previous)
                self.assertEqual(self.app.report_settings.author, "Autor de prueba")

    def test_settings_dialog_saves_fields_through_file_menu(self):
        """Verify settings dialog saves fields through file menu."""
        menu = self.app.file_menu
        index = next(i for i in range(menu.index("end") + 1)
                     if menu.type(i) == "command" and menu.entrycget(i, "label") == "Configuración…")
        menu.invoke(index)
        window = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
        notebook = next(child for child in window.winfo_children() if isinstance(child, ttk.Notebook))
        reports_tab = window.nametowidget(notebook.tabs()[0])
        entries = [child for child in reports_tab.winfo_children() if isinstance(child, ttk.Entry)]
        entries[0].delete(0, "end")
        entries[0].insert(0, "Autor desde ventana")
        format_selector = next(
            child for child in reports_tab.winfo_children()
            if isinstance(child, ttk.Combobox)
        )
        format_selector.set("ODT")
        next(
            child for child in reports_tab.winfo_children()
            if isinstance(child, ttk.Checkbutton)
        ).invoke()
        next(
            child for child in reports_tab.winfo_children()
            if isinstance(child, ttk.Button) and child.cget("text") == "Guardar informes"
        ).invoke()
        self.assertFalse(window.winfo_exists())
        saved = self.app._read_report_settings()
        self.assertEqual(saved.author, "Autor desde ventana")
        self.assertEqual(saved.output_format, "odt")
        self.assertFalse(saved.use_certification_template)

    def test_language_preference_is_saved_and_applied_immediately(self):
        """Save the application language globally and update the visible interface."""
        self.assertTrue(self.app._save_language_preference("en"))
        self.assertEqual(json.loads(self.app.preferences_path.read_text(encoding="utf-8")), {
            "language": "en",
        })
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        file_button = next(
            child for child in toolbar.winfo_children()
            if isinstance(child, ttk.Menubutton) and child.cget("text") == "File"
        )
        self.assertEqual(file_button.cget("text"), "File")
        self.assertTrue(self.app._save_language_preference("es"))

    def test_language_can_be_selected_in_settings_dialog(self):
        """Allow choosing and saving a language from the settings language tab."""
        self.app.open_report_settings()
        window = next(
            child for child in self.root.winfo_children()
            if isinstance(child, tk.Toplevel)
        )
        notebook = next(
            child for child in window.winfo_children()
            if isinstance(child, ttk.Notebook)
        )
        language_tab = window.nametowidget(notebook.tabs()[1])
        selector = next(
            child for child in language_tab.winfo_children()
            if isinstance(child, ttk.Combobox)
        )
        selector.set("Français")
        next(
            child for child in language_tab.winfo_children()
            if isinstance(child, ttk.Button) and child.cget("text") == "Guardar idioma"
        ).invoke()
        self.assertEqual(self.app.language, "fr")
        self.assertEqual(
            json.loads(self.app.preferences_path.read_text(encoding="utf-8"))["language"],
            "fr",
        )
        self.assertTrue(self.app._save_language_preference("es"))

    def test_language_preference_is_restored_on_next_application_start(self):
        """Load the selected global language when a new application instance starts."""
        self.app.preferences_path.write_text('{"language": "fr"}', encoding="utf-8")
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with (
            patch.object(BudgetApp, "_read_recent_projects", return_value=[]),
            patch("bc3_gui.user_data_directory", return_value=self.directory),
            patch.object(root, "state"),
        ):
            reopened = BudgetApp(root)
        self.assertEqual(reopened.language, "fr")
        toolbar = root.grid_slaves(row=0, column=0)[0]
        self.assertTrue(any(
            isinstance(child, ttk.Menubutton) and child.cget("text") == "Fichier"
            for child in toolbar.winfo_children()
        ))

    def test_pdf_generation_is_atomic_and_uses_saved_settings(self):
        """Verify pdf generation is atomic and uses saved settings."""
        from pypdf import PdfReader

        self.set_report_items()
        self.app.report_settings = ReportSettings(author="Autora PDF")
        target = self.directory / "resumen.pdf"
        with (
            patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(target)),
            patch("bc3_gui.os.startfile") as open_pdf,
        ):
            self.app.generate_report("Presupuesto resumen")
            open_pdf.assert_called_once_with(str(target))
        report = "\n".join(page.extract_text() for page in PdfReader(target).pages)
        self.assertIn("Autora PDF", report)
        self.assertIn("4.648,00", report)
        original = target.read_bytes()
        with (
            patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(target)),
            patch("bc3_gui.write_pdf", side_effect=ValueError("recurso ausente")),
            patch("bc3_gui.messagebox.showerror") as error,
        ):
            self.app.generate_report("Materiales")
            error.assert_called_once()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.directory.glob("tmp*.pdf")), [])

    def test_budget_reports_use_the_saved_odt_format(self):
        """Verify budget report generation follows the saved ODT format."""
        self.set_report_items()
        self.app.report_settings = ReportSettings(output_format="odt")
        target = self.directory / "resumen.odt"
        with (
            patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(target)) as dialog,
            patch("bc3_gui.os.startfile") as open_odt,
        ):
            self.app.generate_report("Presupuesto resumen")
            self.assertEqual(dialog.call_args.kwargs["defaultextension"], ".odt")
            open_odt.assert_called_once_with(str(target))
        with ZipFile(target) as archive:
            self.assertEqual(
                archive.read("mimetype").decode(),
                "application/vnd.oasis.opendocument.text",
            )
            content = ElementTree.fromstring(archive.read("content.xml"))
            report_text = " ".join(content.itertext())
            self.assertIn("RESUMEN DE PRESUPUESTO", report_text)
            self.assertIn("4.648,00", report_text)

    def test_budget_odt_exports_include_current_resources_and_item_details(self):
        """Verify ODT reports contain decompositions, materials and measurements."""
        self.set_report_items()
        from odt_reports import write_report_odt

        expectations = {
            "Cuadro de precios 1 y 2 por capitulos": ("Partida <uno>", "TREINTA Y DOS"),
            "Descompuestos": ("Operario", "14,00"),
            "Materiales": ("Material", "303,00"),
            "Precios Auxiliares": ("No hay precios auxiliares",),
            "Presupuesto mediciones": ("3.228,00", "TOTAL EJECUCIÓN MATERIAL"),
            "Presupuesto resumen": ("4.648,00", "PRESUPUESTO BASE DE LICITACIÓN"),
        }
        for index, name in enumerate(REPORT_NAMES):
            with self.subTest(name=name):
                target = self.directory / f"editable-{index}.odt"
                write_report_odt(self.app, name, target)
                with ZipFile(target) as archive:
                    content = ElementTree.fromstring(archive.read("content.xml"))
                    report_text = " ".join(content.itertext())
                for expected in expectations[name]:
                    self.assertIn(expected, report_text)

    @unittest.skipUnless(BUDGET_PATH, "Requiere el presupuesto BC3 local, no distribuido.")
    def test_all_pdf_models_generate_with_actual_budget_data(self):
        """Verify all pdf models generate with actual budget data."""
        from pypdf import PdfReader

        dataset = Bc3Dataset(BUDGET_PATH)
        self.app.budget_dataset = dataset
        self.app.budget_items = dataset.to_project_items()
        self.app.chapter_order = dataset.root_codes
        self.app.report_settings = ReportSettings(author="Autora de prueba")
        self.app.project_title = "Proyecto de prueba"
        for name in REPORT_NAMES:
            with self.subTest(name=name):
                target = self.directory / f"{name}.pdf"
                write_pdf(self.app, name, target)
                reader = PdfReader(target)
                report = "\n".join(page.extract_text() for page in reader.pages)
                self.assertGreater(len(reader.pages), 0)
                self.assertIn("Proyecto de prueba", report)
                self.assertIn("Autora de prueba", report)
                self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 595.28, delta=0.1)
                if name == REPORT_NAMES[0]:
                    self.assertIn("CUADRO DE PRECIOS 1", report)
                    self.assertIn("CUADRO DE PRECIOS 2", report)
                elif name == "Descompuestos":
                    self.assertIn("TOTAL PARTIDA", report)
                elif name == "Presupuesto mediciones":
                    self.assertIn("TOTAL EJECUCIÓN MATERIAL", report)
                    self.assertIn("COMENTARIO", report)
                elif name == "Presupuesto resumen":
                    self.assertIn("199.808,75", report)

    def test_empty_cancelled_pdf_generation_does_not_create_files(self):
        """Verify empty cancelled pdf generation does not create files."""
        with (
            patch("bc3_gui.messagebox.showinfo") as info,
            patch("bc3_gui.filedialog.asksaveasfilename") as dialog,
        ):
            self.app.generate_report("Materiales")
            info.assert_called_once()
            dialog.assert_not_called()
        self.set_report_items()
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=""):
            self.app.generate_report("Materiales")
        self.assertEqual(list(self.directory.iterdir()), [])

    def certification_window(self):
        """Open an isolated certification window linked to the current report settings."""
        self.set_report_items()
        with patch.object(tk.Toplevel, "state"):
            window = CertificationWindow(
                self.root, self.app.budget_items, self.app._chapter_title, self.app._project_title(),
                report_settings=lambda: self.app.report_settings,
            )
        self.root.update_idletasks()
        self.addCleanup(window.destroy)
        return window

    def test_certification_menu_contains_and_executes_both_templates(self):
        """Verify certification menu contains and executes both templates."""
        window = self.certification_window()
        toolbar = window.grid_slaves(row=0, column=0)[0]
        button = next(child for child in toolbar.winfo_children()
                      if isinstance(child, ttk.Menubutton) and child.cget("text") == "Informes")
        menu = window.nametowidget(button.cget("menu"))
        self.assertEqual(
            [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)],
            list(CERTIFICATION_REPORT_NAMES),
        )
        with patch.object(window, "generate_template_report") as generate:
            for index, name in enumerate(CERTIFICATION_REPORT_NAMES):
                menu.invoke(index)
                generate.assert_called_with(name)

    def test_certification_pdfs_use_only_selected_month_and_derived_quantity(self):
        """Verify certification pdfs use only selected month and derived quantity."""
        from pypdf import PdfReader

        window = self.certification_window()
        month = window.months[0]
        first, second, third = self.app.budget_items
        first.measurement_lines = [MeasurementLine(comment="LINEA DEL PRESUPUESTO", units=100)]
        first.certifications = {month.key: 320, window.months[1].key: 999}
        second.certifications = {window.months[1].key: 230}
        third.certifications = {month.key: 0}
        first.certification_justifications = {month.key: "Trabajo realizado"}
        self.app.report_settings = ReportSettings(
            author="Tecnica nueva", contractor="Empresa nueva",
            contractor_representative="Representante nuevo", tender_discount=10,
        )
        window.selected_certification_month = 0
        for name in CERTIFICATION_REPORT_NAMES:
            with self.subTest(name=name):
                target = self.directory / f"{name}.pdf"
                with (
                    patch("certifications.filedialog.asksaveasfilename", return_value=str(target)),
                    patch("certifications.os.startfile") as open_pdf,
                ):
                    window.generate_template_report(name)
                    open_pdf.assert_called_once_with(str(target))
                reader = PdfReader(target)
                report = "\n".join(page.extract_text() for page in reader.pages)
                self.assertIn("Certificación 1", report)
                self.assertIn("Tecnica nueva", report)
                self.assertIn("Empresa nueva", report)
                self.assertIn("Representante nuevo", report)
                self.assertNotIn("Partida dos", report)
                self.assertNotIn("Partida sin descomponer", report)
                self.assertNotIn("999,00", report)
                self.assertNotIn("LINEA DEL PRESUPUESTO", report)
                self.assertEqual(reader.metadata.author, "Tecnica nueva")
                if name == CERTIFICATION_REPORT_NAMES[1]:
                    self.assertIn("10,000000", report)
                    self.assertIn("320,00", report)
                    self.assertIn("Trabajo realizado", report)
                    self.assertIn("No se incluyen líneas de medición", report)
                else:
                    self.assertIn("9,91", report)
                    self.assertIn("414,69", report)

    def test_certification_reports_use_the_saved_odt_format(self):
        """Verify certification report generation follows the saved ODT format."""
        window = self.certification_window()
        item = self.app.budget_items[0]
        month = window.months[0]
        item.certifications[month.key] = 100
        self.app.report_settings = ReportSettings(output_format="odt")
        window.selected_certification_month = 0
        target = self.directory / "certification.odt"
        with (
            patch("certifications.filedialog.asksaveasfilename", return_value=str(target)) as dialog,
            patch("certifications.os.startfile") as open_odt,
        ):
            window.generate_template_report(CERTIFICATION_REPORT_NAMES[1])
            self.assertEqual(dialog.call_args.kwargs["defaultextension"], ".odt")
            open_odt.assert_called_once_with(str(target))
        with ZipFile(target) as archive:
            self.assertEqual(
                archive.read("mimetype").decode(),
                "application/vnd.oasis.opendocument.text",
            )
            content = ElementTree.fromstring(archive.read("content.xml"))
            self.assertIn("Partida", " ".join(content.itertext()))

    def test_certification_pdf_selection_empty_cancel_and_errors(self):
        """Verify certification pdf selection empty cancel and errors."""
        window = self.certification_window()
        target = self.directory / "certification.pdf"
        with (
            patch("certifications.messagebox.showwarning") as warning,
            patch("certifications.messagebox.showinfo") as info,
            patch("certifications.filedialog.asksaveasfilename", return_value="") as dialog,
        ):
            window.generate_template_report(CERTIFICATION_REPORT_NAMES[0])
            warning.assert_called_once()
            dialog.assert_not_called()
            window.selected_certification_month = 0
            window.generate_template_report(CERTIFICATION_REPORT_NAMES[0])
            info.assert_called_once()
            dialog.assert_not_called()
            self.app.budget_items[0].certifications[window.months[0].key] = 100
            window.generate_template_report(CERTIFICATION_REPORT_NAMES[0])
            dialog.assert_called_once()
        target.write_bytes(b"existing")
        with (
            patch("certifications.filedialog.asksaveasfilename", return_value=str(target)),
            patch("certifications.write_certification_pdf", side_effect=ValueError("error PDF")),
            patch("certifications.messagebox.showerror") as error,
            patch("certifications.os.startfile") as open_pdf,
        ):
            window.generate_template_report(CERTIFICATION_REPORT_NAMES[0])
            error.assert_called_once()
            open_pdf.assert_not_called()
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(list(self.directory.glob("tmp*.pdf")), [])

    def test_certification_measurements_reject_zero_price_instead_of_inventing_quantity(self):
        """Verify certification measurements reject zero price instead of inventing quantity."""
        self.set_report_items()
        item = self.app.budget_items[0]
        item.unit_price = 0
        item.certifications = {"2026-10-01": 100}
        target = self.directory / "zero-price.pdf"
        with self.assertRaisesRegex(ValueError, "P1"):
            write_certification_pdf(
                self.app.budget_items, self.app._chapter_title, "Proyecto", ReportSettings(),
                CERTIFICATION_REPORT_NAMES[1], target, "2026-10-01", 1, "Oct 26",
            )
        self.assertFalse(target.exists())

    def test_invalid_certification_values_are_not_stored_or_reported_as_empty(self):
        """Verify invalid certification values are not stored or reported as empty."""
        window = self.certification_window()
        item = self.app.budget_items[0]
        for value in ("nan", "inf", "-inf", "-1", "texto"):
            with self.subTest(value=value), patch("certifications.messagebox.showerror") as error:
                window._commit_certification(0, 0, value)
                error.assert_called_once()
                self.assertEqual(item.certifications, {})
        window.selected_certification_month = 0
        item.certifications[window.months[0].key] = float("nan")
        with (
            patch("certifications.messagebox.showerror") as error,
            patch("certifications.filedialog.asksaveasfilename") as dialog,
        ):
            window.generate_template_report(CERTIFICATION_REPORT_NAMES[0])
            error.assert_called_once()
            dialog.assert_not_called()

    @unittest.skipUnless(BUDGET_PATH, "Requiere el presupuesto BC3 local, no distribuido.")
    def test_certification_details_paginate_large_actual_budget(self):
        """Verify certification details paginate large actual budget."""
        from pypdf import PdfReader

        dataset = Bc3Dataset(BUDGET_PATH)
        items = dataset.to_project_items()
        for item in items:
            item.certifications["2026-10-01"] = item.import_total
        target = self.directory / "actual-certification.pdf"
        write_certification_pdf(
            items, dataset.get_summary, "Proyecto", ReportSettings(),
            CERTIFICATION_REPORT_NAMES[1], target, "2026-10-01", 1, "Oct 26",
        )
        reader = PdfReader(target)
        self.assertGreaterEqual(len(reader.pages), 9)
        report = "\n".join(page.extract_text() for page in reader.pages)
        self.assertIn("138.765,72", report)
        self.assertIn("TOTAL CAP. 09", report)

    def test_corrupt_recent_history_is_reported(self):
        """Verify corrupt recent history is reported."""
        for data in ("{", '{"items": []}', '["relative.json"]', "[3]"):
            self.app.recent_projects_path.write_text(data, encoding="utf-8")
            with patch("bc3_gui.messagebox.showwarning") as warning:
                self.assertEqual(self.app._read_recent_projects(), [])
                warning.assert_called_once()

    def test_history_write_failure_does_not_fail_project_save(self):
        """Verify history write failure does not fail project save."""
        self.app.recent_projects_path = self.directory
        with patch("bc3_gui.messagebox.showwarning") as warning:
            self.assertTrue(self.app._write_project_file(self.directory / "project.json"))
            warning.assert_called_once()
        self.assertTrue((self.directory / "project.json").is_file())
        self.assertFalse(self.app.is_dirty)

    def test_recent_open_prompts_before_discarding_changes(self):
        """Verify recent open prompts before discarding changes."""
        self.app.is_dirty = True
        path = self.directory / "next.json"
        with (
            patch("bc3_gui.messagebox.askyesnocancel", return_value=None),
            patch.object(self.app, "_load_project_file") as load,
        ):
            self.app.open_recent_project(path)
            load.assert_not_called()
        with (
            patch("bc3_gui.messagebox.askyesnocancel", return_value=True),
            patch.object(self.app, "save_project", return_value=False),
            patch.object(self.app, "_load_project_file") as load,
        ):
            self.app.open_recent_project(path)
            load.assert_not_called()
        with (
            patch("bc3_gui.messagebox.askyesnocancel", return_value=True),
            patch.object(self.app, "save_project", return_value=True) as save,
            patch.object(self.app, "_load_project_file") as load,
        ):
            self.app.open_recent_project(path)
            save.assert_called_once()
            load.assert_called_once_with(path)

    def test_failed_open_does_not_add_a_recent_project(self):
        """Verify failed open does not add a recent project."""
        with patch("bc3_gui.messagebox.showerror") as error:
            self.app.open_recent_project(self.directory / "missing.json")
            error.assert_called_once()
        self.assertEqual(self.app.recent_projects, [])

    @unittest.skipUnless(BUDGET_PATH, "Requiere el presupuesto BC3 local, no distribuido.")
    def test_saved_project_reopens_with_source_dataset_and_edited_prices(self):
        """Verify saved project reopens with source dataset and edited prices."""
        self.app.source_budget_path = BUDGET_PATH
        self.app.budget_dataset = Bc3Dataset(BUDGET_PATH)
        self.app.budget_items = self.app.budget_dataset.to_project_items()
        first = self.app.budget_items[0]
        component_code = first.decomposition[0][0]
        first.component_prices[component_code] = 123.45
        project_path = self.directory / "saved.json"
        self.assertTrue(self.app._write_project_file(project_path))
        self.app.budget_dataset = None
        self.app.budget_items = []
        self.assertTrue(self.app._load_project_file(project_path))
        self.assertIsNotNone(self.app.budget_dataset)
        components = self.app._report_components(self.app.budget_items[0])
        self.assertEqual(components[0][2], 123.45)
        self.assertEqual(self.app.recent_projects, [project_path])

    @unittest.skipUnless(BUDGET_PATH, "Requiere el presupuesto BC3 local, no distribuido.")
    def test_legacy_relative_source_is_resolved_next_to_project(self):
        """Verify legacy relative source is resolved next to project."""
        source = self.directory / BUDGET_PATH.name
        source.write_bytes(BUDGET_PATH.read_bytes())
        project = self.directory / "legacy.json"
        project.write_text(json.dumps({"items": [], "source_budget_bc3": source.name}), encoding="utf-8")
        self.assertTrue(self.app._load_project_file(project))
        self.assertEqual(self.app.source_budget_path, source)
        self.assertIsNotNone(self.app.budget_dataset)

    def test_project_can_still_open_when_its_source_bc3_is_missing(self):
        """Verify project can still open when its source bc3 is missing."""
        project = self.directory / "without_source.json"
        project.write_text(
            json.dumps({"items": [], "source_budget_bc3": "missing.bc3"}), encoding="utf-8"
        )
        with patch("bc3_gui.messagebox.showwarning") as warning:
            self.assertTrue(self.app._load_project_file(project))
            warning.assert_called_once()
        self.assertIsNone(self.app.budget_dataset)
        self.assertEqual(self.app.recent_projects, [project])

    def test_resources_ods_has_only_its_sheet_and_preserves_price_variants(self):
        """Verify resources ODS has only its sheet and preserves price variants."""
        self.set_report_items()
        target = self.directory / "resources.ods"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.export_resource_report("Recursos")
        from odf.opendocument import load
        from odf.table import Table

        workbook = load(str(target))
        tables = workbook.spreadsheet.getElementsByType(Table)
        self.assertEqual([table.getAttribute("name") for table in tables], ["Recursos"])
        rows = read_ods_rows(target)
        self.assertEqual(len(rows), 4)
        self.assertEqual({row[4] for row in rows[1:] if row[0] == "MO"}, {10, 14})
        self.assertEqual(rows[0][6], "Medición")
        self.assertEqual(
            {(row[0], row[4]): row[6] for row in rows[1:]},
            {("MO", 14): 200, ("MO", 10): 2, ("MAT", 3): 101},
        )

    def test_resource_measurements_sum_all_occurrences_without_early_rounding(self):
        """Verify resource ODS measurements sum without early rounding."""
        self.set_report_items()
        first, second = self.app.budget_items[:2]
        first.measurement = 0.12345
        first.decomposition = [("MAT", 0.1), ("MAT", 0.2)]
        second.measurement = 0.2
        second.decomposition = [("MAT", 0.3), ("MO", 0)]
        target = self.directory / "measurements.ods"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.export_resource_report("Recursos")
        rows = read_ods_rows(target)
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(rows[1][6], 0.097035, places=12)
        self.assertEqual(rows[2][6], 0)
        self.assertEqual(rows[1][4], 3.0)

    def test_decompositions_ods_uses_current_rendimientos_and_prices(self):
        """Verify decompositions ODS uses current yields and prices."""
        self.set_report_items()
        target = self.directory / "decompositions.ods"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(target)):
            self.app.export_resource_report("Descompuestos")
        rows = read_ods_rows(target)
        self.assertEqual(rows[1], ["P1", "MO", "MO", "h", "Operario", 2.0, 14.0, 28.0, "BC3"])
        self.assertEqual(rows[3][6:8], [10, 20])

    def test_price_reports_escape_text_and_use_unit_not_budget_totals(self):
        """Verify price reports escape text and use unit not budget totals."""
        self.set_report_items()
        first = self.app._price_report_html(1)
        self.assertIn("Cuadro de precios Nº1", first)
        self.assertIn("Proyecto &lt;prueba&gt;", first)
        self.assertIn("Partida &lt;uno&gt;", first)
        self.assertIn("32,00 €", first)
        self.assertIn("<th>Precio unitario</th>", first)
        self.assertNotIn("<th>Rendimiento</th>", first)
        self.assertNotIn("3.200,00 €", first)
        self.assertNotIn("Operario", first)
        second = self.app._price_report_html(2)
        self.assertIn("Cuadro de precios Nº2", second)
        self.assertIn("Operario", second)
        self.assertIn("28,00 €", second)
        self.assertIn("31,00 €", second)
        self.assertIn("Diferencia respecto al precio unitario", second)
        self.assertIn("Sin descomposición", second)

    def test_price_preview_opens_the_generated_printable_html(self):
        """Verify price preview opens the generated printable html."""
        self.set_report_items()
        with (
            patch("bc3_gui.tempfile.tempdir", str(self.directory)),
            patch("bc3_gui.webbrowser.open", return_value=True) as browser,
        ):
            self.app.preview_price_report(2)
        files = list(self.directory.glob("cuadro_precios_2_*.html"))
        self.assertEqual(len(files), 1)
        browser.assert_called_once_with(files[0].as_uri())
        self.assertIn("Cuadro de precios Nº2", files[0].read_text(encoding="utf-8"))

    def test_ods_write_failure_is_reported(self):
        """Verify ODS write failure is reported."""
        self.set_report_items()
        with (
            patch(
                "bc3_gui.filedialog.asksaveasfilename",
                return_value=str(self.directory / "resources.ods"),
            ),
            patch("ods_export.write_ods", side_effect=OSError("disk full")),
            patch("bc3_gui.messagebox.showerror") as error,
        ):
            self.app.export_resource_report("Recursos")
            error.assert_called_once()

    def test_missing_resource_price_is_not_silently_exported_as_zero(self):
        """Verify missing resource price is not silently exported as zero."""
        self.set_report_items()
        self.app.budget_dataset = None
        with self.assertRaisesRegex(ValueError, "MAT"):
            self.app._price_report_html(2)
        target = self.directory / "missing_resources.ods"
        with (
            patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(target)),
            patch("bc3_gui.messagebox.showerror") as error,
        ):
            self.app.export_resource_report("Recursos")
            error.assert_called_once()
        self.assertFalse(target.exists())

    def test_empty_and_cancelled_exports_create_no_files(self):
        """Verify empty and cancelled exports create no files."""
        with (
            patch("bc3_gui.messagebox.showinfo") as info,
            patch("bc3_gui.filedialog.asksaveasfilename") as dialog,
        ):
            self.app.export_resource_report("Recursos")
            self.app.preview_price_report(1)
            self.assertEqual(info.call_count, 2)
            dialog.assert_not_called()
        self.set_report_items()
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=""):
            self.app.export_resource_report("Descompuestos")
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
