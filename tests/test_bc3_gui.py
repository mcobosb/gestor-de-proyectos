from datetime import date
import csv
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

import planning

from bc3_gui import (
    APPLICATION_NAME,
    APPLICATION_RELEASE_DATE,
    APPLICATION_VERSION,
    Bc3Dataset,
    BudgetApp,
    Concept,
    MeasurementLine,
    ProjectItem,
    ToolTip,
    find_bc3_files,
    format_chapter_code,
    format_currency,
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
    CERTIFICATION_REPORT_NAMES, REPORT_NAMES, ReportSettings, amount_in_words,
    write_certification_pdf, write_pdf,
)


BASE_DIR = Path(__file__).resolve().parent.parent
BUDGET_PATH = BASE_DIR / "tests" / "test.bc3"
BCCA_PATH = BASE_DIR / "tests" / "BCCA2023_V02.bc3"
if not BCCA_PATH.is_file():
    BCCA_PATH = None


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


class IndirectCostsTests(unittest.TestCase):
    def test_supplied_bc3_balances_all_items_with_and_without_configured_indirect_costs(self):
        """Verify all imported unit prices equal rounded components without modifying the source."""
        from decimal import Decimal, ROUND_HALF_UP

        dataset = Bc3Dataset(BUDGET_PATH)
        for item in dataset.to_project_items():
            original = item.unit_price
            for percentage in (0, 3, 7.5, 0):
                item.set_indirect_costs(percentage)
                rows = item.cost_components(dataset) + item.indirect_components()
                if not rows:
                    direct = item.unit_price if item.direct_unit_price is None else item.direct_unit_price
                    rows.insert(0, (Concept(item.code), 1, direct))
                total = sum((
                    (Decimal(str(quantity)) * Decimal(str(price))).quantize(
                        Decimal(".01"), rounding=ROUND_HALF_UP,
                    ) for _, quantity, price in rows
                ), Decimal(0))
                self.assertEqual(total, Decimal(str(item.unit_price)), (item.code, percentage))
            self.assertEqual(item.unit_price, original)

    def test_bc3_percentages_use_rounded_accumulated_cost_and_keep_budget_price(self):
        """Verify successive imported percentages and configured overhead reconcile exactly."""
        from decimal import Decimal, ROUND_HALF_UP

        dataset = Bc3Dataset.__new__(Bc3Dataset)
        dataset.concepts = {
            "R": Concept("R", "ud", "Recurso", 26.80, "3"),
            "%A": Concept("%A", "u", "Medios auxiliares", 2, "0"),
            "%I": Concept("%I", "u", "Indirectos importados", 3, "0"),
        }
        item = ProjectItem(
            "P", "01#", "ud", "Partida", unit_price=28.16,
            decomposition=[("R", 1), ("%A", 0.02), ("%I", 0.03)],
        )
        for percentage, expected in ((0, "28.16"), (3, "29.00"), (3, "29.00"), (7.5, "30.27"), (0, "28.16")):
            item.set_indirect_costs(percentage)
            rows = item.cost_components(dataset) + item.indirect_components()
            amounts = [
                (Decimal(str(quantity)) * Decimal(str(price))).quantize(
                    Decimal(".01"), rounding=ROUND_HALF_UP,
                ) for _, quantity, price in rows
            ]
            self.assertEqual(amounts[:3], [Decimal("26.80"), Decimal(".54"), Decimal(".82")])
            self.assertEqual(sum(amounts), Decimal(expected))
            self.assertEqual(Decimal(str(item.unit_price)), Decimal(expected))
        self.assertEqual(dataset.concepts["%A"].price, 2)

    def test_rounding_adjustment_is_explicit_but_does_not_hide_real_price_changes(self):
        """Reconcile cents without altering a stored unit price or masking larger differences."""
        dataset = Bc3Dataset.__new__(Bc3Dataset)
        dataset.concepts = {"R": Concept("R", price=24.91)}
        item = ProjectItem("P", "01#", "ud", "Partida", unit_price=24.93, decomposition=[("R", 1)])
        rows = item.cost_components(dataset)
        self.assertEqual(len(rows), 1)
        dataset.concepts["R"].price = 24.92
        rows = item.cost_components(dataset)
        self.assertEqual(rows[-1][0].summary, "Ajuste de redondeo")
        self.assertEqual(rows[-1][2], .01)
        self.assertEqual(item.unit_price, 24.93)
        self.assertEqual(len(item.cost_components(dataset, reconcile=False)), 1)
        dataset.concepts["R"].price = 20
        self.assertEqual(len(item.cost_components(dataset)), 1)

    def test_percentage_only_safety_study_preserves_lump_sum_and_configured_overhead(self):
        """Treat a percentage-only placeholder as an undecomposed fixed-price item."""
        dataset = Bc3Dataset.__new__(Bc3Dataset)
        dataset.concepts = {"%A": Concept("%A", price=2), "%I": Concept("%I", price=3)}
        item = ProjectItem(
            "19SWW001", "01#", "ud", "Seguridad y salud", unit_price=1064.56,
            decomposition=[("%A", .02), ("%I", .03)],
        )
        item.set_indirect_costs(3)
        rows = item.cost_components(dataset) + item.indirect_components()
        self.assertEqual(rows[0][0].summary, "Precio sin descomposición")
        self.assertEqual(rows[0][2], 1064.56)
        self.assertEqual(item.unit_price, 1096.5)
        self.assertEqual(len(rows), 2)

    def test_percentage_changes_do_not_compound_and_zero_restores_direct_price(self):
        item = ProjectItem("P", "01#", "ud", "Partida", unit_price=100, measurement=2)
        for percentage, expected in ((3, 103), (3, 103), (7.5, 107.5), (0, 100)):
            with self.subTest(percentage=percentage):
                item.set_indirect_costs(percentage)
                self.assertEqual(item.unit_price, expected)
                self.assertEqual(item.import_total, expected * 2)
                self.assertEqual(bool(item.indirect_components()), bool(percentage))
        self.assertIsNone(item.direct_unit_price)
        self.assertEqual(item.decomposition, [])

    def test_rounding_uses_decimal_half_up_and_preserves_original_price(self):
        item = ProjectItem("P", "01#", "ud", "Partida", unit_price=1.005)
        item.set_indirect_costs(100)
        self.assertEqual(item.unit_price, 2.015)
        item.set_indirect_costs(0)
        self.assertEqual(item.unit_price, 1.005)

    def test_project_currency_formats_known_and_custom_units_without_conversion(self):
        self.assertEqual(format_currency(1234.5), "1.234,50 €")
        self.assertEqual(format_currency(1234.5, "USD"), "$1.234,50")
        self.assertEqual(format_currency(1234.5, "GBP"), "£1.234,50")
        self.assertEqual(format_currency(1234.5, "CHF"), "1.234,50 CHF")
        self.assertEqual(format_currency(1234.5, "OTHER", "MXN"), "1.234,50 MXN")
        self.assertIn("DÓLARES CON CINCUENTA CENTAVOS", amount_in_words(10.5, "USD"))
        self.assertEqual(
            ReportSettings.from_dict({"currency": "OTHER", "custom_currency": "COP"}).currency,
            "OTHER",
        )
        with self.assertRaisesRegex(ValueError, "personalizada"):
            ReportSettings.from_dict({"currency": "OTHER"})


class BudgetDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Load the anonymized fixture once for the dataset regression tests."""
        cls.dataset = Bc3Dataset(BUDGET_PATH)
        cls.items = cls.dataset.to_project_items()

    def test_reconstructs_root_chapters_and_real_work_items(self):
        """Verify reconstructs root chapters and real work items."""
        self.assertEqual(
            [format_chapter_code(code) for code in self.dataset.root_codes],
            [f"CAP. {number:02d}" for number in range(1, 10)],
        )
        self.assertEqual(len(self.items), 71)
        self.assertTrue(all(item.chapter.upper().startswith("CAP") for item in self.items))
        self.assertNotIn("R0001", {item.code for item in self.items})

    def test_preserves_prices_measurements_and_text(self):
        """Verify preserves prices measurements and text."""
        first = next(item for item in self.items if item.code == "P0001")
        self.assertEqual(format_chapter_code(first.chapter), "CAP. 01")
        self.assertEqual(first.unit_price, 23.08)
        self.assertEqual(first.measurement, 54)
        self.assertEqual(first.rendimiento, 0.5)
        self.assertEqual(first.text, "DESCRIPCION SINTETICA DEL CONCEPTO P0001.")
        self.assertTrue(first.measurement_detail)

    def test_reconstructed_total_matches_bc3_root_with_rounding_tolerance(self):
        """Verify reconstructed total matches bc3 root with rounding tolerance."""
        calculated = sum(item.import_total for item in self.items)
        declared = self.dataset.concepts["OBRA0001##"].price
        self.assertEqual(declared, 49698.44)
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
        self.app.recent_reference_bases_path = self.directory / "bases_referencia_recientes.json"
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

    def prepare_history(self):
        """Establece un proyecto conocido como punto inicial del historial."""
        self.set_report_items()
        self.app.chapter_order = ["CAP.01#"]
        self.app.refresh_budget_tree()
        self.app._reset_history()

    def test_project_safety_open_does_not_copy_or_modify_json_until_saved(self):
        """Abre el JSON sin escribir copias y lo actualiza solo al guardar."""
        project = self.directory / "original.json"
        original = b'{"title": "Original", "items": []}\r\n'
        project.write_bytes(original)
        self.assertTrue(self.app._load_project_file(project))
        self.assertTrue(self.app._load_project_file(project))
        self.assertEqual(project.read_bytes(), original)
        self.assertFalse((self.directory / "copias_seguridad").exists())
        self.app.project_title = "Editado"
        self.app._mark_dirty()
        self.assertTrue(self.app.save_project())
        self.assertNotEqual(project.read_bytes(), original)
        self.assertFalse((self.directory / "copias_seguridad").exists())

    def test_project_safety_import_keeps_source_unchanged_without_copies(self):
        """No copia ni modifica el BC3 importado y guarda las ediciones en JSON."""
        from test_distribution import SYNTHETIC_BC3

        source = self.directory / "presupuesto.bc3"
        original = SYNTHETIC_BC3.encode("cp1252")
        source.write_bytes(original)
        self.app._load_budget_file(source)
        self.assertEqual(self.app.project_path, source.with_suffix(".json"))
        self.assertTrue(self.app.project_path.is_file())
        self.assertFalse((self.directory / "copias_seguridad").exists())
        self.app.budget_items[0].summary = "EDITADO"
        self.app._mark_dirty()
        self.assertTrue(self.app.save_project())
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(
            json.loads(self.app.project_path.read_text(encoding="utf-8"))["items"][0]["summary"],
            "EDITADO",
        )
        self.assertTrue(self.app._load_project_file(self.app.project_path))
        self.assertFalse((self.directory / "copias_seguridad").exists())
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(self.app._undo_history, [])

    def test_project_safety_failed_initial_import_save_keeps_pending_changes(self):
        """Mantiene pendiente el BC3 importado si falla la creación del JSON inicial."""
        from test_distribution import SYNTHETIC_BC3

        source = self.directory / "presupuesto.bc3"
        source.write_text(SYNTHETIC_BC3, encoding="cp1252")
        with (
            patch("bc3_gui.os.replace", side_effect=OSError("Sin espacio")),
            patch("bc3_gui.messagebox.showerror") as error,
        ):
            self.app._load_budget_file(source)
            error.assert_called_once()
            self.assertFalse(self.app.save_project())
        self.assertTrue(self.app.is_dirty)
        self.assertFalse(source.with_suffix(".json").exists())
        self.assertEqual(self.app.budget_items[0].code, "P1")
        self.assertTrue(self.app.save_project())
        self.assertFalse(self.app.is_dirty)

    def test_project_safety_failed_atomic_save_preserves_previous_file_and_dirty_state(self):
        """Un fallo al sustituir no trunca el JSON ni marca las ediciones como guardadas."""
        self.prepare_history()
        project = self.directory / "original.json"
        self.assertTrue(self.app._write_project_file(project))
        original = project.read_bytes()
        self.app.budget_items[0].summary = "CAMBIO"
        self.app._mark_dirty()
        with (
            patch("bc3_gui.os.replace", side_effect=OSError("Bloqueado")),
            patch("bc3_gui.messagebox.showerror") as error,
        ):
            self.assertFalse(self.app.save_project())
            error.assert_called_once()
        self.assertEqual(project.read_bytes(), original)
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        self.app.undo_last_action()
        self.assertFalse(self.app.is_dirty)

    def test_project_safety_undo_reverses_insert_delete_and_move(self):
        """Revierte operaciones estructurales y conserva los datos y totales originales."""
        self.prepare_history()
        initial = self.app._capture_project_state()
        inserted = ProjectItem("N", "02#", "ud", "Nueva", unit_price=7)
        self.app._insert_item(inserted)
        self.assertTrue(self.app.is_dirty)
        self.app._move_item(inserted, -1)
        self.assertEqual(len(self.app._undo_history), 2)
        self.app.undo_last_action()
        self.assertEqual(self.app.budget_items[-1].chapter, "02#")
        self.app.selected_budget_index = len(self.app.budget_items) - 1
        with patch("bc3_gui.messagebox.askyesno", return_value=True):
            self.app.delete_selected_budget_entry()
        self.app.undo_last_action()
        self.assertEqual(self.app.budget_items[-1].code, "N")
        self.app.undo_last_action()
        self.assertEqual(self.app._capture_project_state(), initial)
        self.assertFalse(self.app.is_dirty)
        self.assertEqual(str(self.app.undo_button["state"]), "disabled")

    def test_project_safety_undo_restores_measurements_components_and_description(self):
        """Recupera mediciones, precios, tipos y textos sin compartir las copias."""
        self.prepare_history()
        initial = self.app._capture_project_state()
        self.app.selected_budget_index = 0
        self.app._show_item_details(self.app.budget_items[0])
        self.app.add_measurement_line()
        self.app._commit_measurement_cell("0", "#2", "3")
        self.app._commit_component_cell("0", "#4", "25")
        self.app._commit_budget_type("resource:0:0", "2")
        self.app.selected_budget_index = 0
        self.app.description_text.delete("1.0", "end")
        self.app.description_text.insert("1.0", "TEXTO NUEVO")
        self.app.on_description_edited()
        self.assertEqual(len(self.app._undo_history), 5)
        for _ in range(5):
            self.app.undo_last_action()
        self.assertEqual(self.app._capture_project_state(), initial)
        self.assertFalse(self.app.is_dirty)
        self.app.budget_items[0].component_prices["MO"] = 99
        self.assertEqual(self.app._history_state["items"][0].component_prices["MO"], 14)

    def test_component_descriptions_are_local_persistent_and_used_in_reports(self):
        """Edit all basic natures without changing shared source concepts or prices."""
        self.prepare_history()
        self.app.budget_dataset.concepts["MAT"].ctype = "2"
        item = self.app.budget_items[0]
        self.app.selected_budget_index = 0
        original_price = item.unit_price
        for row, text in (("0", "Operario especializado"), ("1", "Maquina propia")):
            self.app._commit_component_cell(row, "#2", text)
            self.assertEqual(self.app.component_tree.set(row, "descripcion"), text)
            self.assertEqual(self.app.budget_tree.set(f"resource:0:{row}", "texto"), text)
        self.assertEqual(item.unit_price, original_price)
        self.assertEqual(self.app.budget_dataset.concepts["MO"].summary, "Operario")
        self.assertEqual(self.app.budget_items[1].cost_components(self.app.budget_dataset)[0][0].summary,
                         "Operario")
        self.assertIn("Operario especializado", self.app._price_report_html(2))
        payload = json.loads(json.dumps(self.app._project_payload()))
        restored = self.app._item_from_project_data(payload["items"][0])
        self.assertEqual(restored.component_concepts, item.component_concepts)
        self.assertEqual(restored.cost_components(self.app.budget_dataset)[1][0].summary,
                         "Maquina propia")
        self.app.undo_last_action()
        self.assertNotIn("MAT", self.app.budget_items[0].component_concepts)
        self.app.undo_last_action()
        self.assertFalse(self.app.budget_items[0].component_concepts)

    def test_component_description_editor_accepts_text_and_rejects_empty_values(self):
        """Open the description cell editor and reject an empty description."""
        self.prepare_history()
        self.app.selected_budget_index = 0
        self.app._show_item_details(self.app.budget_items[0])
        event = tk.Event()
        event.x = event.y = 0
        with (
            patch.object(self.app.component_tree, "identify_row", return_value="0"),
            patch.object(self.app.component_tree, "identify_column", return_value="#2"),
            patch.object(self.app.component_tree, "bbox", return_value=(0, 0, 200, 24)),
        ):
            self.app.edit_component_cell(event)
        self.assertIsNotNone(self.app._component_editor)
        self.app._component_editor.delete(0, "end")
        self.app._component_editor.insert(0, "Texto editado")
        self.app._close_component_editor()
        self.app._commit_component_cell("0", "#2", "Texto editado")
        with patch("bc3_gui.messagebox.showerror") as error:
            self.app._commit_component_cell("0", "#2", "  ")
        error.assert_called_once()
        self.assertEqual(self.app.budget_items[0].component_concepts["MO"].summary, "Texto editado")

    def test_copy_basic_from_reference_to_selected_breakdown_and_save_without_base(self):
        """Copy each nature into a manual item and retain the data without its base."""
        base = self.set_reference_filter_base()
        self.app.budget_items = [ProjectItem("P", "01#", "ud", "Partida")]
        self.app.refresh_budget_tree()
        self.app._reset_history()
        self.app.selected_budget_index = 0
        self.app.selected_bcca_base = base
        for code, quantity in (("MAT", 2), ("MO", 3), ("MAQ", 0.5)):
            self.app.selected_bcca_code = code
            with patch("bc3_gui.simpledialog.askfloat", return_value=quantity):
                self.app.add_selected_bcca_to_budget()
        item = self.app.budget_items[0]
        self.assertEqual(item.decomposition, [("MAT", 2), ("MO", 3), ("MAQ", 0.5)])
        self.assertEqual(item.unit_price, 46)
        self.assertEqual(item.rendimiento, 3)
        self.assertEqual(item.component_types, {"MAT": "3", "MO": "1", "MAQ": "2"})
        self.assertIsNot(item.component_concepts["MAT"], base.concepts["MAT"])
        self.app._commit_component_cell("0", "#2", "Cemento propio")
        self.assertEqual(base.concepts["MAT"].summary, "Cemento")
        project = self.directory / "basicos.json"
        self.assertTrue(self.app._write_project_file(project))
        self.app.reference_bases.clear()
        self.assertTrue(self.app._load_project_file(project))
        item = self.app.budget_items[0]
        self.assertEqual(item.unit_price, 46)
        rows = self.app._report_components(item)
        self.assertEqual([(concept.unit, concept.summary, price) for concept, _, price in rows],
                         [("kg", "Cemento propio", 3), ("h", "Operario", 10), ("h", "Grua", 20)])

    def test_copy_basic_validation_and_undo(self):
        """Handle missing targets, duplicate codes, invalid quantities and cancellation."""
        base = self.set_reference_filter_base()
        self.app.budget_items = [ProjectItem("P", "01#", "ud", "Partida")]
        self.app.refresh_budget_tree()
        self.app._reset_history()
        initial = self.app._capture_project_state()
        with patch("bc3_gui.messagebox.showwarning") as warning:
            self.app._add_basic_to_item(base.concepts["MAT"], None)
        warning.assert_called_once()
        for quantity in (None, 0, -1, float("inf")):
            with (
                patch("bc3_gui.simpledialog.askfloat", return_value=quantity),
                patch("bc3_gui.messagebox.showerror"),
            ):
                self.app._add_basic_to_item(base.concepts["MAT"], 0)
            self.assertEqual(self.app._capture_project_state(), initial)
        with patch("bc3_gui.simpledialog.askfloat", return_value=2):
            self.app._add_basic_to_item(base.concepts["MAT"], 0)
        with (
            patch("bc3_gui.simpledialog.askfloat") as quantity,
            patch("bc3_gui.messagebox.showwarning") as warning,
        ):
            self.app._add_basic_to_item(base.concepts["MAT"], 0)
        warning.assert_called_once()
        quantity.assert_not_called()
        self.app.undo_last_action()
        self.assertEqual(self.app._capture_project_state(), initial)

    def test_resource_arrows_reorder_breakdown_not_items_and_recalculate_percentages(self):
        """Move components in both trees, preserve metadata, and undo the operation."""
        self.prepare_history()
        item = self.app.budget_items[0]
        self.app.budget_dataset.concepts["%A"] = Concept("%A", "%", "Auxiliares", ctype="%")
        item.decomposition.append(("%A", 0.1))
        self.app.selected_budget_index = 0
        self.app._commit_component_cell("0", "#2", "Operario propio")
        self.app._recalculate_component_cost(item)
        self.app.refresh_budget_tree()
        self.app._reset_history()
        original = self.app._capture_project_state()
        self.app.budget_tree.selection_set("resource:0:1")
        self.app.on_budget_select(None)
        self.app.move_selected_budget_entry(1)
        self.assertEqual(item.decomposition, [("MO", 2), ("%A", 0.1), ("MAT", 1)])
        self.assertEqual(item.unit_price, 33.8)
        self.assertEqual([entry.code for entry in self.app.budget_items], ["P1", "P2", "P3"])
        self.assertEqual(self.app.budget_tree.selection(), ("resource:0:2",))
        self.app.component_tree.selection_set("2")
        self.app.move_selected_budget_entry(-1)
        self.assertEqual(item.decomposition, [("MO", 2), ("MAT", 1), ("%A", 0.1)])
        self.assertEqual(item.unit_price, 34.1)
        self.assertEqual(item.component_concepts["MO"].summary, "Operario propio")
        self.app.undo_last_action()
        self.app.undo_last_action()
        self.assertEqual(self.app._capture_project_state(), original)

    def test_drag_basic_uses_the_drop_item_instead_of_the_previous_selection(self):
        """Insert a dragged basic into the row under the pointer and reject chapters."""
        self.prepare_history()
        base = self.set_reference_filter_base()
        event = tk.Event()
        event.x_root = event.y_root = 5
        tree = self.app.budget_tree
        for row, expected_calls in (("item:1", 1), ("chapter:0", 0)):
            self.app._drag_code = "MAQ"
            self.app._drag_base = base
            self.app._dragging = True
            self.app.selected_budget_index = 0
            with (
                patch.object(tree, "winfo_rootx", return_value=0),
                patch.object(tree, "winfo_rooty", return_value=0),
                patch.object(tree, "winfo_width", return_value=100),
                patch.object(tree, "winfo_height", return_value=100),
                patch.object(tree, "identify_row", return_value=row),
                patch("bc3_gui.simpledialog.askfloat", return_value=0.5) as quantity,
                patch("bc3_gui.messagebox.showwarning") as warning,
            ):
                self.app.drop_on_budget(event)
            self.assertEqual(quantity.call_count, expected_calls)
            self.assertEqual(warning.call_count, 1 - expected_calls)
        self.assertNotIn("MAQ", dict(self.app.budget_items[0].decomposition))
        self.assertEqual(self.app.budget_items[1].decomposition[-1], ("MAQ", 0.5))
        self.assertEqual(self.app.budget_items[1].unit_price, 33)

    def test_resource_moving_preserves_prices_boundaries_and_filtered_view(self):
        """Keep stored prices and resource metadata while moving in a filtered view."""
        self.prepare_history()
        item = self.app.budget_items[0]
        self.app.toggle_budget_resource_filter("3")
        self.app.budget_tree.selection_set("resource:0:1")
        self.app.on_budget_select(None)
        self.app.move_selected_budget_entry(-1)
        self.assertEqual(item.unit_price, 32)
        self.assertEqual(item.decomposition, [("MAT", 1), ("MO", 2)])
        self.assertEqual(self.app.budget_tree.selection(), ("resource:0:0",))
        self.assertEqual(self.app.budget_tree.get_children(), ("resource:0:0", "resource:1:1"))
        self.app.root.update()
        self.app.move_selected_budget_entry(-1)
        self.assertEqual(item.decomposition, [("MAT", 1), ("MO", 2)])
        self.app.move_selected_budget_entry(1)
        self.assertEqual(item.decomposition, [("MO", 2), ("MAT", 1)])
        self.assertEqual(item.unit_price, 32)
        self.assertEqual(len(self.app._undo_history), 2)

    def test_editing_filtered_resources_keeps_the_destination_selected(self):
        """Retain the selected parent while editing descriptions in the resource table."""
        self.prepare_history()
        self.app.toggle_budget_resource_filter("3")
        self.app.budget_tree.selection_set("resource:0:1")
        self.app.on_budget_select(None)
        self.app._commit_component_cell("1", "#2", "Material editado")
        self.assertEqual(self.app.selected_budget_index, 0)
        self.assertEqual(self.app.component_tree.set("1", "descripcion"), "Material editado")
        base = self.set_reference_filter_base()
        with patch("bc3_gui.simpledialog.askfloat", return_value=2):
            self.app._add_basic_to_item(base.concepts["LIBRE"], self.app.selected_budget_index)
        self.assertEqual(self.app.selected_budget_index, 0)
        self.app._commit_component_cell("2", "#2", "Otro material")
        self.assertEqual(self.app.budget_tree.set("resource:0:2", "texto"), "Otro material")

    def test_project_safety_history_is_limited_to_exactly_50_real_changes(self):
        """Conserva las últimas 50 acciones, ignorando avisos de cambio sin edición."""
        self.prepare_history()
        for value in range(1, 56):
            self.app.budget_items[0].measurement = value
            self.app._mark_dirty()
            self.app._mark_dirty()
        self.assertEqual(len(self.app._undo_history), 50)
        for _ in range(50):
            self.app.undo_last_action()
        self.assertEqual(self.app.budget_items[0].measurement, 5)
        self.assertEqual(self.app.undo_last_action(), "break")
        self.assertEqual(self.app.budget_items[0].measurement, 5)

    def test_project_safety_undo_across_save_does_not_write_to_disk(self):
        """Deshacer después de guardar marca cambios sin alterar el JSON guardado."""
        self.prepare_history()
        project = self.directory / "working.json"
        self.app.budget_items[0].summary = "GUARDADO"
        self.app._mark_dirty()
        self.assertTrue(self.app._write_project_file(project))
        written = project.read_bytes()
        self.app.budget_items[0].summary = "OTRO"
        self.app._mark_dirty()
        self.app.undo_last_action()
        self.assertFalse(self.app.is_dirty)
        self.app.undo_last_action()
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(project.read_bytes(), written)
        self.assertTrue(self.app.save_project())
        self.assertNotEqual(project.read_bytes(), written)

    def test_project_safety_undo_restores_tool_state_and_closes_stale_windows(self):
        """Cierra vistas con referencias antiguas y restaura todos los estados asociados."""
        self.prepare_history()
        initial = self.app._capture_project_state()
        window = tk.Toplevel(self.root)
        self.app._project_windows.append(window)
        self.app._rcd_window = tk.Toplevel(self.root)
        self.app._safety_health_window = tk.Toplevel(self.root)
        self.app._invoicing_window = tk.Toplevel(self.root)
        self.app.planning_state = {"activities": [{"code": "P1"}]}
        self.app.invoicing_state = {"marker": "modificado"}
        self.app.rcd_state = {"marker": "modificado"}
        self.app.budget_items[0].certifications["2026-10-01"] = 20
        self.app.report_settings.indirect_costs = 5
        self.app._mark_dirty()
        self.app.undo_last_action()
        self.assertFalse(window.winfo_exists())
        self.assertEqual(self.app._project_windows, [])
        self.assertIsNone(self.app._rcd_window)
        self.assertIsNone(self.app._invoicing_window)
        self.assertIsNone(self.app._safety_health_window)
        self.assertEqual(self.app._capture_project_state(), initial)

    def test_project_safety_buttons_shortcuts_and_pending_cell(self):
        """Expone Guardar y Deshacer e impide guardar una celda sin confirmar."""
        self.assertTrue(self.root.bind("<Control-s>"))
        self.assertTrue(self.root.bind("<Control-z>"))
        with patch.object(self.app, "save_project", return_value=True) as save:
            self.assertEqual(self.app._save_shortcut(), "break")
            save.assert_called_once()
        project = self.directory / "button.json"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(project)):
            self.app.save_button.invoke()
        self.assertTrue(project.is_file())
        self.app.project_title = "CAMBIO"
        self.app._mark_dirty()
        self.app.undo_button.invoke()
        self.assertFalse(self.app.is_dirty)
        self.app._measurement_editor = ttk.Entry(self.app.measurement_tree)
        with (
            patch("bc3_gui.messagebox.showwarning") as warning,
            patch.object(self.app, "_write_project_file") as write,
        ):
            self.assertFalse(self.app.save_project())
            warning.assert_called_once()
            write.assert_not_called()
        self.app._close_measurement_editor()

    def test_project_safety_new_project_clears_history_and_asks_before_discarding(self):
        """No mezcla sesiones ni descarta cambios sin confirmación."""
        self.prepare_history()
        self.app.budget_items[0].summary = "CAMBIO"
        self.app._mark_dirty()
        with (
            patch("bc3_gui.simpledialog.askstring", return_value="Nuevo"),
            patch("bc3_gui.messagebox.askyesnocancel", return_value=None),
        ):
            self.app.new_project()
        self.assertEqual(len(self.app._undo_history), 1)
        with (
            patch("bc3_gui.simpledialog.askstring", return_value="Nuevo"),
            patch("bc3_gui.messagebox.askyesnocancel", return_value=False),
        ):
            self.app.new_project()
        self.assertEqual(self.app.budget_items, [])
        self.assertEqual(self.app._undo_history, [])
        self.assertFalse(self.app.is_dirty)

    def test_save_as_menu_selects_folder_and_updates_subsequent_save_destination(self):
        """Guardar como elige otra carpeta sin modificar el JSON anterior."""
        self.prepare_history()
        original = self.directory / "original.json"
        self.assertTrue(self.app._write_project_file(original))
        original_bytes = original.read_bytes()
        self.app.budget_items[0].summary = "EDITADO"
        self.app._mark_dirty()
        folder = self.directory / "otra_carpeta"
        folder.mkdir()
        target = folder / "nuevo.json"
        menu = self.app.file_menu
        index = next(
            index for index in range(menu.index("end") + 1)
            if menu.type(index) == "command"
            and menu.entrycget(index, "label") == "Guardar como (JSON)…"
        )
        self.assertEqual(menu.entrycget(index, "accelerator"), "")
        with patch(
            "bc3_gui.filedialog.asksaveasfilename", return_value=str(target),
        ) as dialog:
            menu.invoke(index)
        self.assertEqual(dialog.call_args.kwargs["initialdir"], str(original.parent))
        self.assertEqual(dialog.call_args.kwargs["initialfile"], original.name)
        self.assertTrue(dialog.call_args.kwargs["confirmoverwrite"])
        self.assertEqual(self.app.project_path, target)
        self.assertEqual(self.app.recent_projects[0], target)
        self.assertFalse(self.app.is_dirty)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8"))["items"][0]["summary"], "EDITADO",
        )
        self.app.budget_items[0].summary = "SIGUIENTE"
        self.app._mark_dirty()
        with patch("bc3_gui.filedialog.asksaveasfilename") as dialog:
            self.assertTrue(self.app.save_project())
            dialog.assert_not_called()
        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8"))["items"][0]["summary"], "SIGUIENTE",
        )
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_save_as_cancel_and_failure_keep_original_path_and_pending_changes(self):
        """Cancelar o fallar no cambia la ruta ni descarta los cambios pendientes."""
        self.prepare_history()
        original = self.directory / "original.json"
        self.assertTrue(self.app._write_project_file(original))
        self.app.budget_items[0].summary = "PENDIENTE"
        self.app._mark_dirty()
        history = self.app._undo_history.copy()
        recent = self.app.recent_projects.copy()
        with (
            patch("bc3_gui.filedialog.asksaveasfilename", return_value=""),
            patch.object(self.app, "_write_project_file") as write,
        ):
            self.assertFalse(self.app.save_project_as())
            write.assert_not_called()
        self.assertEqual(self.app.project_path, original)
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(self.app._undo_history, history)
        target = self.directory / "nuevo.json"
        with (
            patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(target)),
            patch("bc3_gui.os.replace", side_effect=OSError("Bloqueado")),
            patch("bc3_gui.messagebox.showerror") as error,
        ):
            self.assertFalse(self.app.save_project_as())
            error.assert_called_once()
        self.assertEqual(self.app.project_path, original)
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(self.app.recent_projects, recent)
        self.assertFalse(target.exists())

    def test_selected_chapter_name_can_be_edited_and_saved_in_project_data(self):
        """Verify chapter names remain editable while preserving their code."""
        self.set_report_items()
        self.app.chapter_order = ["CAP.01#"]
        self.app.refresh_budget_tree()
        chapter_iid = self.app.chapter_iid_by_code["CAP.01#"]
        self.app.budget_tree.selection_set(chapter_iid)
        self.app.on_budget_select(None)

        with patch(
            "bc3_gui.simpledialog.askstring",
            side_effect=["CAP.01#", "  Preparación del terreno  "],
        ) as ask:
            self.app.update_selected_budget_item()

        self.assertEqual(ask.call_count, 2)
        self.assertEqual(ask.call_args_list[0].args[:2], ("Editar capítulo", "Código del capítulo:"))
        self.assertEqual(ask.call_args_list[0].kwargs["initialvalue"], "CAP.01#")
        self.assertEqual(ask.call_args_list[1].args[:2], ("Editar capítulo", "Nombre del capítulo:"))
        self.assertEqual(ask.call_args_list[1].kwargs["initialvalue"], "CAP. 01")
        self.assertEqual(self.app.chapter_titles, {"CAP.01#": "Preparación del terreno"})
        self.assertEqual(self.app.budget_tree.item(chapter_iid, "values")[1], "Preparación del terreno")
        self.assertEqual(self.app.selected_chapter, "CAP.01#")
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(self.app._project_payload()["chapters"]["titles"], self.app.chapter_titles)
        self.assertEqual(self.app.budget_items[0].chapter, "CAP.01#")

    def test_selected_chapter_code_updates_order_titles_items_and_selection(self):
        """Verify editing a chapter code updates every project reference."""
        self.set_report_items()
        self.app.chapter_order = ["CAP.01#"]
        self.app.chapter_titles = {}
        self.app.refresh_budget_tree()
        chapter_iid = self.app.chapter_iid_by_code["CAP.01#"]
        self.app.budget_tree.selection_set(chapter_iid)
        self.app.on_budget_select(None)

        with patch(
            "bc3_gui.simpledialog.askstring",
            side_effect=["  02  ", "CAP. 02"],
        ) as ask:
            self.app.update_selected_budget_item()

        self.assertEqual(ask.call_args_list[1].kwargs["initialvalue"], "CAP. 02")
        self.assertEqual(self.app.chapter_order, ["02#"])
        self.assertEqual(self.app.chapter_titles, {"02#": "CAP. 02"})
        self.assertEqual([item.chapter for item in self.app.budget_items], ["02#"] * 3)
        self.assertEqual(self.app.selected_chapter, "02#")
        self.assertEqual(
            self.app.budget_tree.item(self.app.chapter_iid_by_code["02#"], "text"),
            "CAP. 02",
        )
        self.assertTrue(self.app.is_dirty)

    def test_selected_chapter_cannot_be_renamed_to_an_existing_code(self):
        """Verify an existing chapter code is rejected without modifying the project."""
        self.set_report_items()
        self.app.chapter_order = ["CAP.01#", "CAP.02#"]
        self.app.refresh_budget_tree()
        chapter_iid = self.app.chapter_iid_by_code["CAP.01#"]
        self.app.budget_tree.selection_set(chapter_iid)
        self.app.on_budget_select(None)

        with (
            patch("bc3_gui.simpledialog.askstring", return_value="CAP.02#"),
            patch("bc3_gui.messagebox.showwarning") as warning,
        ):
            self.app.update_selected_budget_item()

        warning.assert_called_once()
        self.assertEqual(self.app.chapter_order, ["CAP.01#", "CAP.02#"])
        self.assertEqual([item.chapter for item in self.app.budget_items], ["CAP.01#"] * 3)
        self.assertFalse(self.app.is_dirty)

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
        self.assertEqual(self.app.budget_summary.cget("style"), "Toolbar.TLabel")
        self.assertEqual(
            style.lookup("Reference.Section.TLabel", "background"), COLORS["teal"]
        )

    def test_toolbar_has_no_redundant_application_branding(self):
        """Replace redundant application branding with the budget total."""
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        self.assertTrue(toolbar.winfo_children())
        self.assertEqual(self.app.budget_summary.master, toolbar)
        self.assertEqual(self.app.budget_summary.pack_info()["side"], "right")
        self.assertTrue(all(isinstance(child, ttk.Menubutton) or child == self.app.budget_summary
                            for child in toolbar.winfo_children()))

    def test_project_search_preserves_totals_order_and_item_indexes(self):
        """Search descriptions and codes without changing the model or the total."""
        self.set_report_items()
        self.app.refresh_budget_tree()
        total = self.app.budget_summary.cget("text")
        self.app.budget_items[1].text = "Detalle exclusivo de excavación"
        self.app.budget_search_var.set("EXCAVACIÓN")
        chapter = self.app.chapter_iid_by_code["CAP.01#"]
        self.assertEqual(self.app.budget_tree.get_children(chapter), ("item:1",))
        self.assertEqual(self.app.budget_index_by_iid["item:1"], 1)
        self.assertEqual(self.app.budget_summary.cget("text"), total)
        self.app.budget_search_var.set("no existe")
        self.assertEqual(self.app.budget_tree.get_children(), ())
        self.app.refresh_budget_tree()
        self.app.budget_search_var.set("")
        self.assertEqual(self.app.budget_tree.get_children(chapter),
                         ("item:0", "item:1", "item:2"))
        self.assertEqual(len(self.app.budget_items), 3)

    def test_resource_filters_show_context_to_the_left_of_resource_rows(self):
        """Show only resource leaves, with chapter and work item columns to the left."""
        self.set_report_items()
        self.app.budget_dataset.concepts["MAQ"] = Concept("MAQ", "h", "Excavadora", 20, "2")
        self.app.budget_items[0].decomposition.append(("MAQ", 1))
        self.app.refresh_budget_tree()
        total = self.app.budget_summary.cget("text")
        original_components = self.app.budget_tree.get_children("item:0")
        self.app.chapter_titles["CAP.01#"] = "Trabajos"
        for kind, icon, expected in (("2", "⚙", ("resource:0:2",)),
                                     ("1", "♟", ("resource:0:0", "resource:1:0")),
                                     ("3", "▧", ("resource:0:1", "resource:1:1"))):
            with self.subTest(kind=kind):
                self.assertEqual(self.app.budget_filter_buttons[kind].cget("text"), icon)
                self.app.budget_filter_buttons[kind].invoke()
                self.assertEqual(self.app.budget_tree.get_children(), expected)
                self.assertEqual(tuple(map(str, self.app.budget_tree.cget("show"))), ("headings",))
                self.assertEqual(tuple(self.app.budget_tree.cget("displaycolumns")),
                                 ("capitulo", "partida", "codigo", "tipo", "texto", "medicion", "importe"))
                for row in expected:
                    self.assertEqual(self.app.budget_tree.get_children(row), ())
                    self.assertEqual(self.app.budget_tree.set(row, "capitulo"), "CAP. 01 · Trabajos")
                    index = int(row.split(":")[1])
                    item = self.app.budget_items[index]
                    self.assertEqual(self.app.budget_tree.set(row, "partida"),
                                     f"{item.code} · {item.summary}")
                    self.assertEqual(self.app.budget_tree.set(row, "codigo"),
                                     self.app.budget_tree.item(row, "text"))
                self.assertEqual(sum(var.get() for var in self.app.budget_filter_variables.values()), 1)
                self.assertEqual(self.app.budget_summary.cget("text"), total)
        self.app.budget_search_var.set("P2")
        chapter = self.app.chapter_iid_by_code["CAP.01#"]
        self.assertEqual(self.app.budget_tree.get_children(), ("resource:1:1",))
        self.app.budget_filter_buttons["3"].invoke()
        self.assertEqual(tuple(map(str, self.app.budget_tree.cget("show"))), ("tree", "headings"))
        self.assertEqual(tuple(self.app.budget_tree.cget("displaycolumns")),
                         ("tipo", "texto", "medicion", "importe"))
        self.assertEqual(self.app.budget_tree.get_children(chapter), ("item:1",))
        self.assertEqual(self.app.budget_tree.get_children("item:1"),
                         ("resource:1:0", "resource:1:1"))
        self.app.budget_search_var.set("")
        self.assertEqual(self.app.budget_tree.get_children(chapter),
                         ("item:0", "item:1", "item:2"))
        self.assertEqual(self.app.budget_tree.get_children("item:0"), original_components)

    def test_resource_table_handles_empty_results_standalone_items_and_repeated_searches(self):
        """Restore hierarchy, row values and open states after repeated flat projections."""
        self.set_report_items()
        self.app.budget_items[2].concept_type = "3"
        self.app.budget_items[2].chapter = "CAP.02#"
        self.app.chapter_order = ["VACIO", "CAP.01#", "CAP.02#"]
        self.app.refresh_budget_tree()
        chapters = self.app.budget_tree.get_children()
        self.app.budget_tree.item(chapters[1], open=False)
        self.app.budget_tree.item("item:0", open=True)
        original_values = self.app.budget_tree.item("resource:0:1", "values")
        for _ in range(2):
            self.app.toggle_budget_resource_filter("3")
            self.assertEqual(self.app.budget_tree.get_children(),
                             ("resource:0:1", "resource:1:1", "item:2"))
            self.assertEqual(self.app.budget_tree.set("item:2", "codigo"), "P3")
            self.assertEqual(self.app.budget_tree.set("item:2", "partida"), "")
            self.assertEqual(self.app.budget_tree.item("resource:0:1", "values")[:4],
                             original_values)
            for term, expected in (
                ("CAP.02", ("item:2",)),
                ("P1", ("resource:0:1",)),
                ("Material", ("resource:0:1", "resource:1:1", "item:2")),
                ("no existe", ()),
                ("", ("resource:0:1", "resource:1:1", "item:2")),
            ):
                self.app.budget_search_var.set(term)
                self.assertEqual(self.app.budget_tree.get_children(), expected)
            self.app.toggle_budget_resource_filter("3")
            self.assertEqual(self.app.budget_tree.get_children(), chapters)
            self.assertEqual(self.app.budget_tree.get_children(chapters[1]), ("item:0", "item:1"))
            self.assertEqual(self.app.budget_tree.get_children(chapters[2]), ("item:2",))
            self.assertEqual(self.app.budget_tree.get_children("item:0"),
                             ("resource:0:0", "resource:0:1"))
            self.assertFalse(self.app.budget_tree.item(chapters[1], "open"))
            self.assertTrue(self.app.budget_tree.item("item:0", "open"))
        self.app.toggle_budget_resource_filter("2")
        self.assertEqual(self.app.budget_tree.get_children(), ())
        self.app.toggle_budget_resource_filter("2")
        self.assertEqual(self.app.budget_tree.get_children(), chapters)

    def test_resource_table_type_editor_uses_the_reordered_type_column(self):
        """Edit nature in the flat table and immediately reapply the active filter."""
        self.set_report_items()
        self.app.refresh_budget_tree()
        self.app.toggle_budget_resource_filter("3")
        event = tk.Event()
        event.x = 0
        event.y = 0
        tree = self.app.budget_tree
        self.assertEqual(tree.column("#4", "id"), "tipo")
        with (
            patch.object(tree, "identify_region", return_value="cell"),
            patch.object(tree, "identify_column", return_value="#4"),
            patch.object(tree, "identify_row", return_value="resource:0:1"),
            patch.object(tree, "bbox", return_value=(0, 0, 120, 24)),
        ):
            self.app.edit_budget_type(event)
        editor = self.app._type_editor
        self.assertIsNotNone(editor)
        editor.set("⚙ Maquinaria")
        editor.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.app.budget_items[0].component_types["MAT"], "2")
        self.assertEqual(tree.get_children(), ("resource:1:1",))
        self.assertTrue(self.app.is_dirty)
        self.app.toggle_budget_resource_filter("2")
        self.assertEqual(tree.get_children(), ("resource:0:1",))
        self.app.toggle_budget_resource_filter("2")
        self.assertEqual(tree.get_children("item:0"), ("resource:0:0", "resource:0:1"))

    def set_reference_filter_base(self, name="Referencia"):
        """Create a reference with shared resources and an unexpanded auxiliary."""
        base = Bc3Dataset.__new__(Bc3Dataset)
        base.path = self.directory / f"{name}.bc3"
        base.root_codes = ["01#"]
        base.concepts = {
            "01#": Concept("01#", summary="Cimentacion", children=[("P1", 1), ("P2", 1)]),
            "P1": Concept("P1", "m3", "Hormigon", children=[("AUX", 2), ("MAQ", 0.5)]),
            "P2": Concept("P2", "m2", "Encofrado", children=[("MAT", 3)]),
            "AUX": Concept("AUX", children=[("MO", 2), ("MAT", 4)]),
            "MO": Concept("MO", "h", "Operario", 10, "1"),
            "MAQ": Concept("MAQ", "h", "Grua", 20, "2"),
            "MAT": Concept("MAT", "kg", "Cemento", 3, "3"),
            "LIBRE": Concept("LIBRE", "ud", "Recurso suelto", 1, "3"),
        }
        base.chapter_children = {
            code: [child for child, _ in concept.children]
            for code, concept in base.concepts.items()
        }
        self.app.reference_bases.append(base)
        return base

    def test_reference_filter_buttons_are_above_the_reference_tree_and_independent(self):
        """Place exclusive nature controls in the reference header, not the toolbar."""
        self.set_reference_filter_base()
        self.app._refresh_reference_views()
        self.app.toggle_budget_resource_filter("1")
        for kind, icon, expected in (
            ("2", "⚙", ["MAQ"]), ("1", "♟", ["MO"]),
            ("3", "▧", ["MAT", "MAT", "LIBRE"]),
        ):
            button = self.app.bcca_filter_buttons[kind]
            self.assertEqual(button.cget("text"), icon)
            self.assertEqual(button.master.master, self.app.right_panel)
            self.assertEqual(int(button.master.grid_info()["row"]), 0)
            button.invoke()
            self.assertEqual(self.app.bcca_resource_filter, kind)
            self.assertEqual(self.app.budget_resource_filter, "1")
            self.assertEqual(sum(var.get() for var in self.app.bcca_filter_variables.values()), 1)
            rows = self.app.bcca_tree.get_children()
            self.assertEqual([self.app.bcca_code_by_iid[row] for row in rows], expected)
            self.assertTrue(all(not self.app.bcca_tree.get_children(row) for row in rows))
        rows = self.app.bcca_tree.get_children()
        self.assertEqual(tuple(self.app.bcca_tree.cget("displaycolumns")),
                         ("base", "capitulo", "partida", "codigo", "texto", "ud", "precio"))
        self.assertEqual(self.app.bcca_tree.set(rows[0], "capitulo"), "CAP. 01 · Cimentacion")
        self.assertEqual(self.app.bcca_tree.set(rows[0], "partida"), "P1 · Hormigon")
        self.assertEqual(self.app.bcca_tree.set(rows[0], "ud"), "4,0000 kg")
        self.assertEqual(self.app.bcca_tree.set(rows[1], "partida"), "P2 · Encofrado")
        self.assertEqual(self.app.bcca_tree.set(rows[2], "capitulo"), "")
        self.assertEqual(self.app.bcca_tree.set(rows[2], "partida"), "")
        self.app.bcca_search.insert(0, "P1")
        self.app.bcca_filter_buttons["3"].invoke()
        self.assertEqual(self.app.bcca_resource_filter, "")
        self.assertFalse(any(var.get() for var in self.app.bcca_filter_variables.values()))
        self.assertEqual(list(self.app.bcca_code_by_iid.values()), ["P1"])
        self.app.bcca_search.delete(0, "end")
        self.app.filter_bcca()
        root = self.app.bcca_tree.get_children()[0]
        self.assertEqual(self.app.bcca_code_by_iid[root], "01#")
        self.assertEqual(len(self.app.bcca_tree.get_children(root)), 2)
        self.assertEqual(tuple(map(str, self.app.bcca_tree.cget("show"))), ("tree", "headings"))
        self.assertFalse(self.app.is_dirty)

    def test_reference_filters_search_context_and_preserve_base_identity(self):
        """Combine resource and ancestor searches across bases and clear stale selection."""
        first = self.set_reference_filter_base("Primera")
        second = self.set_reference_filter_base("Segunda")
        self.app.toggle_bcca_resource_filter("3")
        tree = self.app.bcca_tree
        rows = tree.get_children()
        self.assertEqual(len(rows), 6)
        for row, base in ((rows[0], first), (rows[3], second)):
            self.assertIs(self.app.bcca_base_by_iid[row], base)
            self.assertEqual(tree.set(row, "base"), base.path.stem)
        tree.selection_set(rows[3])
        self.app.on_bcca_select(None)
        self.assertEqual(self.app.selected_bcca_code, "MAT")
        self.assertIs(self.app.selected_bcca_base, second)
        for term, expected in (
            ("P1", ["MAT", "MAT"]), ("Cimentacion", ["MAT"] * 4),
            ("cemento", ["MAT"] * 4), ("no existe", []), ("", ["MAT", "MAT", "LIBRE"] * 2),
        ):
            self.app.bcca_search.delete(0, "end")
            self.app.bcca_search.insert(0, term)
            self.app.filter_bcca()
            self.assertEqual(
                [self.app.bcca_code_by_iid[row] for row in tree.get_children()], expected,
            )
            self.assertIsNone(self.app.selected_bcca_code)
            self.assertIsNone(self.app.selected_bcca_base)
            self.assertEqual(self.app.bcca_detail_text.get("1.0", "end").strip(), "")
        self.app.reference_bases.remove(first)
        self.app._refresh_reference_views()
        self.assertEqual(len(tree.get_children()), 3)
        self.assertTrue(all(base is second for base in self.app.bcca_base_by_iid.values()))
        self.app.reference_bases.clear()
        self.app._refresh_reference_views()
        self.assertEqual(tree.get_children(), ())
        self.app.toggle_bcca_resource_filter("3")
        self.assertEqual(tree.get_children(), ())

    def test_reference_resource_limit_and_cycles(self):
        """Bound filtered results and terminate cyclic decompositions without placeholders."""
        base = self.set_reference_filter_base()
        base.concepts["AUX"].children.append(("AUX", 1))
        self.app.toggle_bcca_resource_filter("1")
        self.assertEqual(len(self.app.bcca_tree.get_children()), 1)
        base.concepts.update({
            f"M{index}": Concept(f"M{index}", summary="Material", ctype="3")
            for index in range(510)
        })
        with patch.object(self.app, "_set_status") as status:
            self.app.toggle_bcca_resource_filter("3")
        self.assertEqual(len(self.app.bcca_tree.get_children()), 500)
        self.assertIn("500", status.call_args.args[0])
        self.app.bcca_search.insert(0, "M509")
        self.app.filter_bcca()
        self.assertEqual(
            list(self.app.bcca_code_by_iid.values()), ["M509"],
        )

    def test_project_search_matches_resource_and_chapter_and_clears_hidden_selection(self):
        """Keep ancestor context while removing stale selection from hidden rows."""
        self.set_report_items()
        self.app.refresh_budget_tree()
        self.app.budget_tree.selection_set("item:2")
        self.app.on_budget_select(None)
        self.app.budget_search_var.set("Operario")
        self.assertIsNone(self.app.selected_budget_index)
        self.assertEqual(self.app.budget_tree.selection(), ())
        self.assertEqual(self.app.budget_tree.get_children("item:0"), ("resource:0:0",))
        self.app.budget_search_var.set("CAP.01")
        chapter = self.app.chapter_iid_by_code["CAP.01#"]
        self.assertEqual(len(self.app.budget_tree.get_children(chapter)), 3)
        self.app.budget_search_var.set("")
        self.app.toggle_all_budget_nodes()
        for iid in (*self.app.chapter_iid_by_code.values(), *self.app.budget_index_by_iid):
            if self.app.budget_tree.get_children(iid):
                self.assertTrue(self.app.budget_tree.item(iid, "open"))
        self.app.toggle_all_budget_nodes()
        for iid in (*self.app.chapter_iid_by_code.values(), *self.app.budget_index_by_iid):
            self.assertFalse(self.app.budget_tree.item(iid, "open"))

    def test_project_filters_survive_edits_and_restore_all_chapters(self):
        """Respect manual resource natures, edits, and original order across chapters."""
        self.set_report_items()
        self.app.budget_items[0].component_types["MAT"] = "2"
        self.app.budget_items[2].chapter = "CAP.02#"
        self.app.chapter_order = ["VACIO", "CAP.01#", "CAP.02#"]
        self.app.refresh_budget_tree()
        original_chapters = self.app.budget_tree.get_children()
        self.app.toggle_budget_resource_filter("2")
        self.assertEqual(self.app.budget_tree.get_children(), ("resource:0:1",))
        self.app.budget_items[0].summary = "Resumen cambiado"
        self.app._refresh_item_row(0)
        self.assertEqual(self.app.budget_tree.get_children(), ("resource:0:1",))
        self.assertEqual(self.app.budget_tree.set("resource:0:1", "partida"),
                         "P1 · Resumen cambiado")
        self.app.refresh_budget_tree()
        self.app.toggle_budget_resource_filter("2")
        self.assertEqual(self.app.budget_tree.get_children(), original_chapters)
        self.assertEqual(self.app.budget_tree.get_children("item:0"),
                         ("resource:0:0", "resource:0:1"))
        self.assertFalse(self.app.is_dirty)

    def test_window_title_stays_application_after_status_updates(self):
        """Preserve the window-frame title after status updates."""
        self.assertEqual(self.root.title(), "Aplicación")
        self.app._set_status("Proyecto guardado.")
        self.assertEqual(self.root.title(), "Aplicación")

    def test_application_information_shows_release_version(self):
        """Display release metadata and the repository without personal details."""
        with patch("bc3_gui.messagebox.showinfo") as info:
            self.app.show_application_info()
        self.assertIn(f"v{APPLICATION_VERSION}", info.call_args.args[1])
        self.assertIn("https://github.com/mcobosb/gestor-de-proyectos", info.call_args.args[1])
        for removed in ("Manuel", "Ingeniero", "Servicio de Infraestructuras", "Ayuntamiento"):
            self.assertNotIn(removed, info.call_args.args[1])

    def test_information_menu_replaces_personal_details_with_repository(self):
        """Open the project repository directly from the information menu."""
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        button = next(child for child in toolbar.winfo_children()
                      if child.cget("text") == "Información")
        menu = self.root.nametowidget(button.cget("menu"))
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)
                  if menu.type(i) == "command"]
        for removed in ("Manuel", "Ingeniero", "Servicio de Infraestructuras", "Ayuntamiento"):
            self.assertFalse(any(removed in label for label in labels))
        index = next(i for i in range(menu.index("end") + 1)
                     if menu.type(i) == "command" and
                     menu.entrycget(i, "label") == "Repositorio en GitHub")
        with patch("bc3_gui.webbrowser.open", return_value=True) as browser:
            menu.invoke(index)
        browser.assert_called_once_with("https://github.com/mcobosb/gestor-de-proyectos")

    def test_information_menu_reference_price_database_links(self):
        """Expose a native cascade and open each official README link."""
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        button = next(child for child in toolbar.winfo_children()
                      if child.cget("text") == "Información")
        menu = self.root.nametowidget(button.cget("menu"))
        index = next(i for i in range(menu.index("end") + 1)
                     if menu.type(i) == "cascade" and
                     menu.entrycget(i, "label") == "Bases de precios de referencia")
        submenu = self.root.nametowidget(menu.entrycget(index, "menu"))
        self.assertFalse(submenu.cget("tearoff"))
        expected = (
            ("Andalucía (JA) — BCCA",
             "https://www.juntadeandalucia.es/organismos/viviendajuventudyordenaciondelterritorio/areas/vivienda-rehabilitacion/planes-instrumentos/paginas/vivienda-bcca.html"),
            ("Extremadura — Base de precios",
             "https://www.juntaex.es/w/base-precios-construccion-2026"),
            ("Madrid — Base de Datos de la Construcción",
             "https://www.comunidad.madrid/vivienda/base-datos-construccion"),
        )
        self.assertEqual(submenu.index("end"), 2)
        for index, (label, url) in enumerate(expected):
            with self.subTest(label=label):
                self.assertEqual(submenu.entrycget(index, "label"), label)
                with patch("bc3_gui.webbrowser.open", return_value=True) as browser:
                    submenu.invoke(index)
                browser.assert_called_once_with(url)
                for result in (False, OSError("browser unavailable"), ValueError("invalid browser")):
                    with (
                        patch("bc3_gui.webbrowser.open", return_value=False,
                              side_effect=result if isinstance(result, Exception) else None),
                        patch("bc3_gui.messagebox.showerror") as error,
                        patch.object(self.app, "_set_status") as status,
                    ):
                        submenu.invoke(index)
                    error.assert_called_once()
                    self.assertIn(url, error.call_args.args[1])
                    self.assertEqual(error.call_args.kwargs["parent"], self.root)
                    status.assert_not_called()

    def test_information_menu_groups_metadata_reference_links_and_project_links(self):
        """Keep release metadata disabled and separate the three menu blocks."""
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        button = next(child for child in toolbar.winfo_children()
                      if child.cget("text") == "Información")
        menu = self.root.nametowidget(button.cget("menu"))
        self.assertEqual(menu.index("end"), 8)
        expected = (
            ("command", APPLICATION_NAME),
            ("command", f"Versión {APPLICATION_VERSION} — "
             f"{APPLICATION_RELEASE_DATE.strftime('%d/%m/%Y')}"),
            ("command", "Presupuestos BC3, mediciones, planificación y certificaciones"),
            ("separator", None),
            ("cascade", "Bases de precios de referencia"),
            ("separator", None),
            ("command", "Repositorio en GitHub"),
            ("command", "Licencia y responsabilidad…"),
            ("command", "Apoyar el proyecto (Buy Me a Coffee)"),
        )
        for index, (entry_type, label) in enumerate(expected):
            with self.subTest(index=index):
                self.assertEqual(menu.type(index), entry_type)
                if label is not None:
                    self.assertEqual(menu.entrycget(index, "label"), label)
                    self.assertEqual(menu.entrycget(index, "state"),
                                     "disabled" if index < 3 else "normal")

    def test_external_specialty_chapter_names_disable_only_the_corresponding_tool(self):
        """Recognize common imported chapter names without examining resource descriptions."""
        for title, tool in (
            ("GESTIÓN DE RESIDUOS DE CONSTRUCCIÓN Y DEMOLICIÓN", "rcd"),
            ("R.C.D.s", "rcd"), ("Retirada de escombros", "rcd"),
            ("RCDS", "rcd"), ("SEGURIDAD Y SALUD", "safety"),
            ("Seguridad e higiene", "safety"), ("Prevención de riesgos laborales", "safety"),
            ("Seguridad", "safety"), ("Estudio básico de seguridad", "safety"),
            ("S. Y S.", "safety"),
        ):
            with self.subTest(title=title):
                self.app.budget_items = [ProjectItem("P", "09#", "ud", "Partida", source="BC3")]
                self.app.chapter_order = ["09#"]
                self.app.chapter_titles = {"09#": title}
                self.app.refresh_budget_tree()
                index = 0 if tool == "rcd" else 1
                self.assertEqual(self.app.tools_menu.entrycget(index, "state"), "disabled")
                self.assertEqual(self.app.tools_menu.entrycget(1 - index, "state"), "normal")
                before = self.app._project_payload()
                with (
                    patch("bc3_gui.messagebox.showwarning") as warning,
                    patch("rcd.RcdWindow") as rcd_window,
                    patch("safety_health.SafetyHealthWindow") as safety_window,
                ):
                    (self.app.open_rcds if tool == "rcd" else self.app.open_safety_health)()
                warning.assert_called_once()
                rcd_window.assert_not_called()
                safety_window.assert_not_called()
                self.assertEqual(self.app._project_payload(), before)
        self.app.chapter_titles["09#"] = "Albañilería"
        self.app.budget_items[0].summary = "Contenedor de residuos y casco de seguridad"
        self.app.refresh_budget_tree()
        self.assertEqual(self.app.tools_menu.entrycget(0, "state"), "normal")
        self.assertEqual(self.app.tools_menu.entrycget(1, "state"), "normal")

    def test_imported_specialty_chapters_remain_locked_after_json_save(self):
        """Read chapter titles from BC3 and retain external ownership in its JSON sidecar."""
        path = self.directory / "specialties.bc3"
        path.write_text(
            "~V|TEST|FIEBDC-3/2016|\n"
            "~C|ROOT##||OBRA|0||0|\n"
            "~C|01#||GESTION DE RESIDUOS|0||0|\n"
            "~C|02#||SEGURIDAD Y SALUD|0||0|\n"
            "~C|P1|ud|Residuos|123.45||0|\n"
            "~C|P2|ud|Protecciones|67.89||0|\n"
            "~D|ROOT##|01#\\1\\1\\02#\\1\\1\\|\n"
            "~D|01#|P1\\1\\2\\|\n"
            "~D|02#|P2\\1\\3\\|\n", encoding="utf-8",
        )
        self.app._load_budget_file(path)
        self.assertEqual(len(self.app.budget_items), 2)
        before = self.app._project_payload()
        self.assertEqual(self.app.tools_menu.entrycget(0, "state"), "disabled")
        self.assertEqual(self.app.tools_menu.entrycget(1, "state"), "disabled")
        self.assertTrue(self.app._load_project_file(path.with_suffix(".json")))
        self.assertEqual(self.app._project_payload()["items"], before["items"])
        self.assertEqual(self.app.tools_menu.entrycget(0, "state"), "disabled")
        self.assertEqual(self.app.tools_menu.entrycget(1, "state"), "disabled")
        path.unlink()
        with patch("bc3_gui.messagebox.showwarning"):
            self.assertTrue(self.app._load_project_file(path.with_suffix(".json")))
        self.assertEqual(self.app.tools_menu.entrycget(0, "state"), "disabled")
        self.assertEqual(self.app.tools_menu.entrycget(1, "state"), "disabled")

    def test_safety_review_roundtrip_opens_saved_selection_without_recalculation(self):
        """Restore selected and excluded lines and percentage without reading the matrix."""
        from decimal import Decimal
        from safety_health import SafetyLine, synchronize_budget

        self.app.budget_items = [ProjectItem("P", "01#", "ud", "Obra", unit_price=1000)]
        self.app.chapter_order = ["01#"]
        lines = [
            SafetyLine("A", "ud", "Protección", Decimal("12"), Decimal("3")),
            SafetyLine("B", "ud", "Señal", Decimal("9"), Decimal("1"), False),
        ]
        synchronize_budget(self.app, lines, Decimal("3.5"))
        path = self.directory / "safety.json"
        self.assertTrue(self.app._write_project_file(path))
        before = self.app._project_payload()
        with (
            patch("safety_health.proposed_lines", side_effect=AssertionError("matrix read")),
            patch("safety_health.synchronize_budget", side_effect=AssertionError("recalculation")),
        ):
            self.assertTrue(self.app._load_project_file(path))
            loaded = self.app._capture_project_state()
            self.app.open_safety_health()
            window = self.app._safety_health_window
            self.assertEqual(window.percentage.get(), "3.5")
            self.assertEqual(window.lines, lines)
            self.assertEqual(window.tree.set("B", "selected"), "")
            self.app.open_safety_health()
            self.assertIs(self.app._safety_health_window, window)
        self.assertEqual(self.app.tools_menu.entrycget(1, "state"), "normal")
        self.assertEqual(self.app._project_payload()["items"], before["items"])
        self.assertEqual(self.app._capture_project_state(), loaded)
        self.assertFalse(self.app.is_dirty)

    def test_legacy_safety_json_uses_generated_items_without_new_proposal(self):
        """Older project JSON already contains the generated safety chapter and its prices."""
        self.app.budget_items = [
            ProjectItem("SS-A", "SS#", "ud", "Protección existente", measurement=7,
                        unit_price=11, source="SS:A"),
        ]
        self.app.chapter_order = ["SS#"]
        self.app.chapter_titles = {"SS#": "SEGURIDAD Y SALUD"}
        self.app.refresh_budget_tree()
        before = self.app._project_payload()
        with patch("safety_health.proposed_lines", side_effect=AssertionError("matrix read")):
            self.app.open_safety_health()
        window = self.app._safety_health_window
        self.assertEqual(window.lines[0].price, 11)
        self.assertEqual(window.lines[0].weight, 77)
        self.assertEqual(self.app._project_payload(), before)

    def test_rcd_json_opens_saved_inputs_without_recalculating_generated_budget(self):
        """Opening saved RCD data must not invoke initial recovery or calculation."""
        from rcd import RcdState, synchronize_budget

        self.app.budget_items = [ProjectItem("P", "01#", "ud", "Obra", unit_price=1000)]
        self.app.chapter_order = ["01#"]
        state = RcdState()
        state.inputs.update(surface=20, budget=1000, earth_budget=0, earth_volume=3)
        synchronize_budget(self.app, state)
        self.app.rcd_state = state.to_dict()
        path = self.directory / "rcd.json"
        self.assertTrue(self.app._write_project_file(path))
        before = self.app._project_payload()
        with (
            patch("rcd.initial_state", side_effect=AssertionError("initial recovery")),
            patch("rcd.calculate", side_effect=AssertionError("calculation")),
            patch("rcd.synchronize_budget", side_effect=AssertionError("budget sync")),
        ):
            self.assertTrue(self.app._load_project_file(path))
            loaded = self.app._capture_project_state()
            self.app.open_rcds()
            window = self.app._rcd_window
            self.assertEqual(window.data.to_dict(), state.to_dict())
            self.assertEqual(window.variables["surface"].get(), "20.0")
        self.assertEqual(self.app.tools_menu.entrycget(0, "state"), "normal")
        self.assertEqual(self.app._project_payload()["items"], before["items"])
        self.assertEqual(self.app._capture_project_state(), loaded)
        self.assertFalse(self.app.is_dirty)

    def test_safety_review_participates_in_undo_and_new_project_reset(self):
        """Undo accepted safety reviews and clear them when starting another project."""
        from decimal import Decimal
        from safety_health import SafetyLine, synchronize_budget

        self.prepare_history()
        lines = [SafetyLine("A", "ud", "Protección", Decimal("12"), Decimal("3"))]
        synchronize_budget(self.app, lines, Decimal("2"))
        self.assertTrue(self.app.safety_health_state)
        self.app.open_safety_health()
        self.app.undo_last_action()
        self.assertEqual(self.app.safety_health_state, {})
        self.assertIsNone(self.app._safety_health_window)
        self.assertFalse(any(item.source.startswith("SS:") for item in self.app.budget_items))
        synchronize_budget(self.app, lines, Decimal("2"))
        with (
            patch("bc3_gui.simpledialog.askstring", return_value="Nuevo"),
            patch.object(self.app, "_confirm_project_change", return_value=True),
        ):
            self.app.new_project()
        self.assertEqual(self.app.safety_health_state, {})
        self.assertEqual(self.app.tools_menu.entrycget(1, "state"), "normal")

    def test_external_chapter_is_not_synced_on_save_even_with_stale_rcd_state(self):
        """A saved estimate must not override a separately imported waste chapter."""
        from rcd import RcdState

        self.app.budget_items = [
            ProjectItem("P", "RCD#", "ud", "Importado", unit_price=321, source="BC3"),
        ]
        self.app.chapter_order = ["RCD#"]
        self.app.rcd_state = RcdState().to_dict()
        self.app.budget_items.append(
            ProjectItem("OLD", "OLD#", "ud", "Generado antiguo", source="RCD:earth")
        )
        self.app.chapter_order.append("OLD#")
        before = self.app._project_payload()["items"]
        with patch("rcd.synchronize_budget", side_effect=AssertionError("budget sync")):
            self.assertTrue(self.app._write_project_file(self.directory / "external.json"))
        self.assertEqual(self.app._project_payload()["items"], before)

    def test_repository_browser_failures_are_reported(self):
        """Report a refused or failed browser launch without claiming success."""
        for result in (False, OSError("browser unavailable"), ValueError("invalid browser")):
            with (
                self.subTest(result=result),
                patch("bc3_gui.webbrowser.open", return_value=result,
                      side_effect=result if isinstance(result, Exception) else None),
                patch("bc3_gui.messagebox.showerror") as error,
                patch.object(self.app, "_set_status") as status,
            ):
                self.app.open_repository_page()
            error.assert_called_once()
            self.assertIn("https://github.com/mcobosb/gestor-de-proyectos",
                          error.call_args.args[1])
            status.assert_not_called()

    def test_information_menu_opens_mit_license_and_responsibility_notice(self):
        """Verify information menu opens mit license and responsibility notice."""
        toolbar = self.root.grid_slaves(row=0, column=0)[0]
        button = next(child for child in toolbar.winfo_children()
                      if isinstance(child, ttk.Menubutton) and child.cget("text") == "Información")
        menu = self.root.nametowidget(button.cget("menu"))
        self.assertEqual(menu.entrycget(0, "label"), APPLICATION_NAME)
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
        self.assertEqual(self.app.file_menu.entrycget(6, "label"), "Bases de referencia recientes")
        self.assertEqual(self.app.file_menu.type(8), "separator")
        self.assertEqual(self.app.file_menu.entrycget(9, "label"), "Nuevo proyecto")
        with patch.object(self.app, "_load_project_file") as load:
            self.app.file_menu.invoke(1)
            load.assert_called_once_with(latest)

    def test_recent_reference_bases_keep_three_unique_paths_and_open_from_menu(self):
        """Verify BCCA history is limited to three paths and can be reopened."""
        self.assertEqual(self.app.file_menu.entrycget(2, "label"), "Bases de referencia recientes")
        for number in range(4):
            self.app._remember_recent_reference_base(self.directory / f"base{number}.bc3")
        latest = self.directory / "base2.bc3"
        self.app._remember_recent_reference_base(latest)
        expected = [self.directory / f"base{number}.bc3" for number in (2, 3, 1)]
        self.assertEqual(self.app.recent_reference_bases, expected)
        self.assertEqual(self.app._read_recent_reference_bases(), expected)
        with patch.object(self.app, "_load_bcca_file") as load:
            self.app.file_menu.invoke(3)
            load.assert_called_once_with(latest)

    def test_icon_buttons_have_hover_help_without_replacing_commands(self):
        """Verify icon buttons have hover help without replacing commands."""
        actions = self.root.grid_slaves(row=1, column=0)[0]
        buttons = [
            child for child in actions.winfo_children()
            if isinstance(child, (ttk.Button, ttk.Checkbutton))
        ]
        self.assertEqual(len(buttons), 14)
        for button in buttons:
            self.assertTrue(button.bind("<Enter>"))
            self.assertTrue(button.bind("<Leave>"))
            self.assertTrue(button.bind("<ButtonPress>"))
            self.assertTrue(button.cget("command"))
        icon_buttons = [
            button for button in buttons
            if button not in (self.app.save_button, self.app.undo_button)
        ]
        self.assertEqual([button.cget("text") for button in icon_buttons[7:]],
                         ["⧉", "↕", "⚙", "♟", "▧"])

    def test_duplicate_selected_item_is_independent_and_marked_for_manual_review(self):
        """Duplicate a work item, avoid copied tracking data, and flag manual review."""
        self.set_report_items()
        self.app.chapter_order = ["CAP.01#"]
        source = self.app.budget_items[0]
        source.measurement_lines = [MeasurementLine(comment="Tramo", units=2, length=5)]
        source.recalculate_measurement()
        source.planned_amounts = {"2030-01": 100}
        source.certifications = {"2030-01": 50}
        source.certification_plans = {"2030-01": 75}
        source.certification_justifications = {"2030-01": "Justificación"}
        self.app.refresh_budget_tree()
        self.app.budget_tree.selection_set("item:0")
        self.app.on_budget_select(None)
        self.app.is_dirty = False

        with patch("bc3_gui.messagebox.showwarning") as warning:
            self.app.duplicate_selected_budget_item()

        duplicate = self.app.budget_items[1]
        self.assertEqual(duplicate.code, "P1*")
        self.assertEqual(duplicate.chapter, source.chapter)
        self.assertEqual(duplicate.unit, source.unit)
        self.assertEqual(duplicate.summary, source.summary.upper())
        self.assertEqual(duplicate.text, source.text.upper())
        self.assertEqual(duplicate.measurement, source.measurement)
        self.assertEqual(duplicate.unit_price, source.unit_price)
        self.assertEqual(duplicate.decomposition, source.decomposition)
        self.assertIsNot(duplicate.decomposition, source.decomposition)
        self.assertEqual(duplicate.component_prices, source.component_prices)
        self.assertIsNot(duplicate.component_prices, source.component_prices)
        self.assertEqual(duplicate.measurement_lines, source.measurement_lines)
        self.assertIsNot(duplicate.measurement_lines, source.measurement_lines)
        self.assertEqual(duplicate.planned_amounts, {})
        self.assertEqual(duplicate.certifications, {})
        self.assertEqual(duplicate.certification_plans, {})
        self.assertEqual(duplicate.certification_justifications, {})
        self.assertEqual(self.app.budget_tree.item("item:1", "tags"), ("duplicate_review",))
        self.assertEqual(
            str(self.app.budget_tree.tag_configure("duplicate_review", "background")),
            COLORS["warning_soft"],
        )
        self.assertIn("revísala manualmente", warning.call_args.args[1])
        self.assertEqual(warning.call_args.kwargs["parent"], self.root)
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(self.app.selected_budget_index, 1)
        self.assertEqual(self.app._project_payload()["items"][1]["code"], "P1*")

        with patch("bc3_gui.messagebox.showwarning"):
            self.app.duplicate_selected_budget_item()
        self.assertEqual(self.app.budget_items[2].code, "P1**")

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
        self.assertEqual(menu.entrycget(0, "label"), "Mediciones")
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
            "currency": "USD",
            "contractor": "Constructora de prueba",
            "contractor_representative": "Responsable de prueba",
        }))
        self.assertEqual(self.app._read_report_settings().author, "Autor de prueba")
        self.assertEqual(self.app.report_settings.general_expenses, 13.5)
        self.assertEqual(self.app.report_settings.currency, "USD")
        self.app.report_settings = ReportSettings(author="Otro")
        self.assertTrue(self.app._load_project_file(project))
        self.assertEqual(self.app.report_settings.author, "Autor de prueba")
        self.assertEqual(self.app.report_settings.currency, "USD")
        previous = self.app.report_settings_path.read_bytes()
        self.assertEqual(self.app.report_settings.contractor, "Constructora de prueba")
        self.assertEqual(self.app.report_settings.contractor_representative, "Responsable de prueba")
        for value in ("nan", "inf", "-1", "101", "abc", ""):
            with self.subTest(value=value), patch("bc3_gui.messagebox.showerror") as error:
                self.assertFalse(self.app._save_report_settings({"vat": value}))
                error.assert_called_once()
                self.assertEqual(self.app.report_settings_path.read_bytes(), previous)
                self.assertEqual(self.app.report_settings.author, "Autor de prueba")

    def test_project_currency_formats_budget_and_custom_symbols_without_changing_values(self):
        """Format budget amounts in the configured currency without converting prices."""
        self.set_report_items()
        item = self.app.budget_items[0]
        original_price = item.unit_price
        self.assertTrue(self.app._save_report_settings({"currency": "USD"}))
        self.assertEqual(item.unit_price, original_price)
        self.assertEqual(
            self.app.budget_tree.item("item:0", "values")[-1],
            "$3.200,00",
        )
        self.assertIn("$32,00", self.app._budget_html())

        self.assertTrue(self.app._save_report_settings({
            "currency": "OTHER",
            "custom_currency": "COP",
        }))
        self.assertEqual(
            self.app.budget_tree.item("item:0", "values")[-1],
            "3.200,00 COP",
        )
        self.assertIn("COP", self.app._budget_html())

    def test_indirect_costs_update_all_items_and_persist_without_compounding(self):
        self.set_report_items()
        original_prices = [item.unit_price for item in self.app.budget_items]
        self.assertTrue(self.app._save_report_settings({"indirect_costs": "3,0"}))
        self.assertEqual([item.unit_price for item in self.app.budget_items], [32.96, 23.69, 5.15])
        for index, item in enumerate(self.app.budget_items):
            self.assertEqual(item.direct_unit_price, original_prices[index])
            rows = self.app._report_components(item)
            self.assertEqual(rows[-1][0].summary, "Costes Indirectos")
            self.assertEqual(rows[-1][1], 3)
            self.assertEqual(
                self.app.budget_tree.item(f"indirect:{index}:0", "values")[1],
                "Coste directo" if not item.decomposition else "Costes Indirectos",
            )
        self.app._show_item_details(self.app.budget_items[1])
        self.assertEqual(self.app.component_tree.item("indirect:0", "values")[1], "Costes Indirectos")
        project = self.directory / "indirect.json"
        self.assertTrue(self.app._write_project_file(project))
        for _ in range(2):
            self.assertTrue(self.app._load_project_file(project))
            self.assertEqual([item.unit_price for item in self.app.budget_items], [32.96, 23.69, 5.15])
        self.assertTrue(self.app._save_report_settings({"indirect_costs": 0}))
        self.assertEqual([item.unit_price for item in self.app.budget_items], original_prices)
        self.assertFalse(any(row.startswith("indirect:") for row in self.app.component_tree.get_children()))
        self.assertTrue(self.app._load_project_file(project))
        self.assertEqual(self.app.report_settings.indirect_costs, 0)
        self.assertEqual([item.unit_price for item in self.app.budget_items], original_prices)

    def test_indirect_costs_follow_new_items_resource_edits_and_direct_price_edits(self):
        self.set_report_items()
        self.assertTrue(self.app._save_report_settings({"indirect_costs": 3}))
        item = ProjectItem("NEW", "CAP.01#", "ud", "Nueva", unit_price=100)
        self.app._insert_item(item)
        self.assertEqual(item.unit_price, 103)
        self.app.on_budget_select(None)
        self.assertEqual(self.app.budget_fields["price_var"].get(), "100.00")
        self.app.budget_fields["price_var"].set("200")
        self.assertTrue(self.app._apply_budget_fields())
        self.assertEqual(item.unit_price, 206)
        self.app.selected_budget_index = 0
        first = self.app.budget_items[0]
        self.app._commit_component_cell("0", "#3", "3")
        self.assertEqual(first.direct_unit_price, 45)
        self.assertEqual(first.unit_price, 46.35)
        self.assertEqual(self.app.budget_tree.item("indirect:0:0", "values")[-1], "1,35 €")
        self.app._commit_component_cell("indirect:0", "#3", "99")
        self.assertEqual(first.unit_price, 46.35)

    def test_indirect_costs_defaults_apply_to_bc3_and_reference_items(self):
        self.assertTrue(self.app._save_report_settings({"indirect_costs": 3}))
        source = self.directory / "budget.bc3"
        source.write_text(
            "~C|OBRA##||Obra|40||0|\n"
            "~C|01#||Capitulo|40||0|\n"
            "~C|P1|m|Partida|10||0|\n"
            "~C|MO|h|Operario|5||1|\n"
            "~D|OBRA##|01#\\1\\1\\|\n"
            "~D|01#|P1\\1\\4\\|\n"
            "~D|P1|MO\\1\\2\\|\n",
            encoding="cp1252",
        )
        self.app._load_budget_file(source)
        self.assertEqual(len(self.app.budget_items), 1)
        self.assertEqual(self.app.budget_items[0].unit_price, 10.3)
        self.app._load_budget_file(source)
        self.assertEqual(self.app.budget_items[0].unit_price, 10.3)
        base = self.app.budget_dataset
        assert base is not None
        self.app.reference_bases.append(base)
        item = self.app._build_item_from_bcca("P1", base)
        assert item is not None
        self.app._insert_item(item)
        self.assertEqual(item.unit_price, 10.3)

    def test_new_and_copied_item_text_is_uppercase_without_changing_source_or_saved_projects(self):
        """Normalize new project text while preserving source data, units and persisted text."""
        base_path = self.directory / "mixed-case.bc3"
        base_path.write_text(
            "~C|01#||Capítulo original|10||0|\n"
            "~C|pMin|m²|Partida de hormigón|10||0|\n"
            "~T|pMin|Descripción con ñ y tildes: ejecución.|\n"
            "~D|01#|pMin\\1\\1\\|\n", encoding="cp1252",
        )
        base = Bc3Dataset(base_path)
        original = (base.concepts["pMin"].summary, base.concepts["pMin"].text)
        for item in (base.to_project_items()[0], self.app._build_item_from_bcca("pMin", base)):
            self.assertIsNotNone(item)
            self.assertEqual(item.summary, original[0].upper())
            self.assertEqual(item.text, original[1].upper())
            self.assertEqual((item.code, item.unit), ("pMin", "m²"))
        self.assertEqual((base.concepts["pMin"].summary, base.concepts["pMin"].text), original)
        self.app.reference_bases.append(base)
        self.app._insert_bcca_chapter("01#", base)
        self.assertEqual(self.app.budget_items[0].summary, original[0].upper())
        self.assertEqual(self.app.budget_items[0].text, original[1].upper())
        manual = ProjectItem("nuevo", "01#", "m²", "Resumen manual", text="Texto manual con ñ")
        self.app._insert_item(manual)
        self.assertEqual((manual.summary, manual.text), ("RESUMEN MANUAL", "TEXTO MANUAL CON Ñ"))
        manual.summary, manual.text = "Edición posterior", "Descripción conservada"
        self.app.selected_budget_index = self.app.budget_items.index(manual)
        with patch("bc3_gui.messagebox.showwarning"):
            self.app.duplicate_selected_budget_item()
        duplicate = self.app.budget_items[self.app.selected_budget_index]
        self.assertEqual((duplicate.summary, duplicate.text),
                         ("EDICIÓN POSTERIOR", "DESCRIPCIÓN CONSERVADA"))
        self.assertEqual((manual.summary, manual.text),
                         ("Edición posterior", "Descripción conservada"))
        target = self.directory / "mixed-case.json"
        self.assertTrue(self.app._write_project_file(target))
        self.assertTrue(self.app._load_project_file(target))
        restored = next(item for item in self.app.budget_items if item.code == "nuevo")
        self.assertEqual((restored.summary, restored.text),
                         ("Edición posterior", "Descripción conservada"))

    def test_adding_reference_chapter_imports_all_direct_and_nested_items(self):
        """Import a selected reference chapter and all its nested work items."""
        base_path = self.directory / "reference.bc3"
        base_path.write_text(
            "~C|01#||Capítulo principal|30||0|\n"
            "~C|02#||Subcapítulo|20||0|\n"
            "~C|P1|m|Partida uno|10||0|\n"
            "~C|P2|ud|Partida dos|20||0|\n"
            "~D|01#|02#\\1\\1\\P1\\1\\1\\|\n"
            "~D|02#|P2\\1\\1\\|\n",
            encoding="cp1252",
        )
        base = Bc3Dataset(base_path)
        self.app.reference_bases = [base]
        self.app.selected_bcca_code = "01#"
        self.app.selected_bcca_base = base

        self.app.add_selected_bcca_to_budget()

        self.assertEqual(self.app.chapter_order, ["01#"])
        self.assertEqual(self.app.chapter_titles, {"01#": "Capítulo principal"})
        self.assertEqual([item.code for item in self.app.budget_items], ["P2", "P1"])
        self.assertEqual({item.chapter for item in self.app.budget_items}, {"01#"})
        self.assertEqual([item.unit_price for item in self.app.budget_items], [20, 10])
        self.assertEqual(self.app.selected_budget_index, 0)
        self.assertEqual(self.app.budget_tree.selection(), ("item:0",))

        self.app.budget_items.clear()
        self.app.chapter_order.clear()
        self.app.refresh_budget_tree()
        self.app.budget_tree.selection_remove(self.app.budget_tree.selection())
        self.app._drag_code = "01#"
        self.app._drag_base = base
        self.app._dragging = True
        event = tk.Event()
        event.x_root = 20
        event.y_root = 20
        with (
            patch.object(self.app.budget_tree, "winfo_rootx", return_value=0),
            patch.object(self.app.budget_tree, "winfo_rooty", return_value=0),
            patch.object(self.app.budget_tree, "winfo_width", return_value=100),
            patch.object(self.app.budget_tree, "winfo_height", return_value=100),
        ):
            self.app.drop_on_budget(event)
        self.assertEqual([item.code for item in self.app.budget_items], ["P2", "P1"])

    def test_indirect_costs_appear_in_html_pdf_odt_and_ods(self):
        from odt_reports import write_report_odt
        from pypdf import PdfReader

        self.set_report_items()
        self.assertTrue(self.app._save_report_settings({"indirect_costs": 3}))
        self.assertIn("Costes Indirectos", self.app._price_report_html(2))
        pdf = self.directory / "indirect.pdf"
        write_pdf(self.app, "Descompuestos", pdf)
        self.assertEqual("".join(page.extract_text() for page in PdfReader(pdf).pages).count("Costes Indirectos"), 3)
        odt = self.directory / "indirect.odt"
        write_report_odt(self.app, "Descompuestos", odt)
        with ZipFile(odt) as archive:
            content = ElementTree.fromstring(archive.read("content.xml"))
        self.assertEqual("".join(content.itertext()).count("Costes Indirectos"), 3)
        ods = self.directory / "indirect.ods"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(ods)):
            self.app.export_resource_report("Descompuestos")
        indirect_rows = [row for row in read_ods_rows(ods) if row[4] == "Costes Indirectos"]
        self.assertEqual(len(indirect_rows), 3)
        self.assertEqual([row[5] for row in indirect_rows], [3, 3, 3])
        self.assertAlmostEqual(indirect_rows[0][7], 0.96)

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
        indirect_label = next(
            child for child in reports_tab.winfo_children()
            if isinstance(child, ttk.Label) and child.cget("text") == "Costes Indirectos (%)"
        )
        indirect_entry = reports_tab.grid_slaves(row=indirect_label.grid_info()["row"], column=1)[0]
        indirect_entry.delete(0, "end")
        indirect_entry.insert(0, "3,5")
        format_selector = next(
            child for child in reports_tab.winfo_children()
            if isinstance(child, ttk.Combobox)
        )
        format_selector.set("ODT")
        currency_selector = next(
            child for child in reports_tab.winfo_children()
            if isinstance(child, ttk.Combobox) and "USD ($)" in child.cget("values")
        )
        currency_selector.set("USD ($)")
        currency_selector.event_generate("<<ComboboxSelected>>")
        next(
            child for child in reports_tab.winfo_children()
            if isinstance(child, ttk.Button) and child.cget("text") == "Guardar informes"
        ).invoke()
        self.assertFalse(window.winfo_exists())
        saved = self.app._read_report_settings()
        self.assertEqual(saved.author, "Autor desde ventana")
        self.assertEqual(saved.output_format, "odt")
        self.assertEqual(saved.template_path, "")
        self.assertEqual(saved.indirect_costs, 3.5)
        self.assertEqual(saved.currency, "USD")

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
            styles = ElementTree.fromstring(archive.read("styles.xml"))
            report_text = " ".join((*content.itertext(), *styles.itertext()))
            self.assertIn("RESUMEN DE PRESUPUESTO", report_text)
            self.assertIn("4.648,00", report_text)

    def test_budget_odt_exports_include_current_resources_and_item_details(self):
        """Verify ODT reports contain decompositions, materials and measurements."""
        self.set_report_items()
        from odt_reports import write_report_odt

        expectations = {
            "Mediciones": ("Partida <uno>", "MEDICIÓN TOTAL"),
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

    def test_all_pdf_models_generate_with_actual_budget_data(self):
        """Verify all PDF models generate with the anonymized budget fixture."""
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
                if name == "Cuadro de precios 1 y 2 por capitulos":
                    self.assertIn("CUADRO DE PRECIOS 1", report)
                    self.assertIn("CUADRO DE PRECIOS 2", report)
                elif name == "Descompuestos":
                    self.assertIn("TOTAL PARTIDA", report)
                elif name == "Presupuesto mediciones":
                    self.assertIn("TOTAL EJECUCIÓN MATERIAL", report)
                    self.assertIn("COMENTARIO", report)
                elif name == "Presupuesto resumen":
                    self.assertIn("71.560,79", report)

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

    def test_deviation_button_exports_configured_format_and_original_monthly_plan(self):
        """Export the selected month's deviations through the normal report workflow."""
        from pypdf import PdfReader

        window = self.certification_window()
        toolbar = window.grid_slaves(row=0, column=0)[0]
        button = next(child for child in toolbar.winfo_children()
                      if child.cget("text") == "Informe de desviaciones")
        window.selected_certification_month = 0
        month = window.months[0]
        item = self.app.budget_items[0]
        item.certifications[month.key] = 120
        item.certification_plans[month.key] = 100
        item.certification_justifications[month.key] = "Motivo singular"
        window.plan[0][0] = 120
        window.plan[1][0] = 50
        window.plan[2][0] = 0
        for extension in ("pdf", "odt"):
            self.app.report_settings.output_format = extension
            target = self.directory / f"deviations.{extension}"
            with (
                patch("certifications.filedialog.asksaveasfilename", return_value=str(target)) as dialog,
                patch("certifications.os.startfile") as open_report,
            ):
                button.invoke()
            self.assertEqual(dialog.call_args.kwargs["defaultextension"], "." + extension)
            open_report.assert_called_once_with(str(target))
            if extension == "pdf":
                text = "\n".join(page.extract_text() for page in PdfReader(target).pages)
            else:
                with ZipFile(target) as archive:
                    text = " ".join(ElementTree.fromstring(archive.read("content.xml")).itertext())
            for expected in ("Motivo singular", "Partida dos", "150,00", "120,00", "-30,00"):
                self.assertIn(expected, text)

    def test_deviation_export_handles_pending_month_cancel_and_invalid_plan(self):
        """Allow uncertified months, respect cancellation and preserve existing files on error."""
        window = self.certification_window()
        with (
            patch("certifications.messagebox.showwarning") as warning,
            patch("certifications.filedialog.asksaveasfilename", return_value="") as dialog,
        ):
            window.generate_certification_report()
            warning.assert_called_once()
            dialog.assert_not_called()
            window.selected_certification_month = 0
            window.generate_certification_report()
            dialog.assert_called_once()
        target = self.directory / "pending.pdf"
        with (
            patch("certifications.filedialog.asksaveasfilename", return_value=str(target)),
            patch("certifications.os.startfile"),
        ):
            window.generate_certification_report()
        self.assertTrue(target.is_file())
        original = target.read_bytes()
        window.plan[0][0] = float("nan")
        with (
            patch("certifications.filedialog.asksaveasfilename", return_value=str(target)),
            patch("certifications.messagebox.showerror") as error,
            patch("certifications.os.startfile") as open_report,
        ):
            window.generate_certification_report()
        error.assert_called_once()
        open_report.assert_not_called()
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.directory.glob("tmp*.pdf")), [])

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

    def test_certification_details_paginate_large_actual_budget(self):
        """Verify certification details paginate the large anonymized budget."""
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
        self.assertIn("49.698,44", report)
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

    def test_donation_action_opens_buy_me_a_coffee_page(self):
        """Verify the donation action opens the configured support page."""
        with patch("bc3_gui.webbrowser.open", return_value=True) as browser:
            self.app.open_donation_page()
        browser.assert_called_once_with("https://buymeacoffee.com/mcobosb")

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
