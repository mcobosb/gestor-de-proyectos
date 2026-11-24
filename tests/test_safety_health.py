from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from bc3_gui import ProjectItem
from budget_reports import ReportSettings
from pypdf import PdfReader
from tools.safety_health import (
    SAFETY_BUDGET_SOURCE, SafetyLine, evaluate_study_criteria, proposed_lines, restore_state,
    restore_project_state, serialize_state, synchronize_budget, write_study_report,
)


class SafetyHealthTests(unittest.TestCase):
    def test_study_preserves_summary_case_and_omits_table_references_in_both_formats(self):
        """Conserva el resumen revisado, las tildes y los títulos de las tablas."""
        app = SimpleNamespace(
            budget_items=[
                ProjectItem("01AAB00001", "01#", "m2", "Demolición", measurement=1, unit_price=10),
                ProjectItem("SS-A", "CAP.02#", "ud", "Protección", measurement=1,
                            unit_price=1, source="SS:A"),
            ],
            chapter_order=["01#", "CAP.02#"],
            chapter_titles={"01#": "Obra", "CAP.02#": "Seguridad y salud"},
            report_settings=ReportSettings(),
            planning_state={},
            safety_health_state={"resumen_actuacion": "La actuación de ACME mejora la red."},
            _project_title=lambda: "Proyecto de prueba",
            _chapter_title=lambda chapter: {"01#": "Obra", "CAP.02#": "Seguridad y salud"}[chapter],
            _component_dataset=lambda item: None,
            _budget_concept_type=lambda item, code=None: "0",
        )
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(output_format=output_format):
                    app.report_settings.output_format = output_format
                    target = Path(folder) / f"estudio.{output_format}"
                    write_study_report(app, target)
                    if output_format == "pdf":
                        text = " ".join(page.extract_text() or "" for page in PdfReader(target).pages)
                    else:
                        with ZipFile(target) as archive:
                            text = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                    text = " ".join(text.split())
                    self.assertIn("La actuación de ACME mejora la red.", text)
                    self.assertIn("La prevención se integrará en la planificación", text)
                    self.assertIn("Tabla 1. Datos del proyecto", text)
                    self.assertNotIn("véase la tabla", text.casefold())

    def test_workforce_uses_whole_people_with_at_least_one_per_planned_team(self):
        """No diluye una plantilla de una persona al redondear las jornadas hacia arriba."""
        app = SimpleNamespace(
            budget_items=[ProjectItem("P", "01#", "ud", "Obra", rendimiento=7.9)],
            report_settings=SimpleNamespace(general_expenses=0, industrial_profit=0, vat=0),
            planning_state={},
            _chapter_title=lambda chapter: "OBRA",
            _project_title=lambda: "Proyecto",
        )
        self.assertEqual(evaluate_study_criteria(app).peak_workers, Decimal(1))
        app.planning_state = {"activities": [
            {"chapter": "01#", "code": "P", "teams": 3, "manual_duration": 10},
        ]}
        self.assertEqual(evaluate_study_criteria(app).peak_workers, Decimal(3))

    def test_study_criteria_use_strict_budget_and_workday_thresholds(self):
        """Do not mark thresholds as met when the estimated values equal their limits."""
        item = ProjectItem(
            "01", "01#", "ud", "Partida de prueba",
            unit_price=450759.08, rendimiento=4000,
        )
        app = SimpleNamespace(
            budget_items=[item],
            report_settings=SimpleNamespace(general_expenses=0, industrial_profit=0, vat=0),
            planning_state={},
            _chapter_title=lambda chapter: "OBRA",
            _project_title=lambda: "Proyecto de prueba",
        )

        result = evaluate_study_criteria(app)

        self.assertEqual(result.contract_budget, Decimal("450759.08"))
        self.assertEqual(result.worker_days, Decimal("500"))
        self.assertEqual(result.fulfilled, (False, False, False, False))
        self.assertFalse(result.requires_full_study)

    def test_study_criteria_calculate_schedule_workforce_and_special_work(self):
        """Use the saved crew count, labor workload and work descriptions."""
        item = ProjectItem(
            "01", "01#", "ud", "Ejecución de túnel",
            unit_price=1, rendimiento=5200,
        )
        app = SimpleNamespace(
            budget_items=[item],
            report_settings=SimpleNamespace(general_expenses=0, industrial_profit=0, vat=0),
            planning_state={
                "params": {"hours_per_day": 8},
                "activities": [{"chapter": "01#", "code": "01", "teams": 21}],
            },
            _chapter_title=lambda chapter: "OBRA",
            _project_title=lambda: "Proyecto de prueba",
        )

        result = evaluate_study_criteria(app)

        self.assertEqual(result.duration_days, 31)
        self.assertGreater(result.peak_workers, Decimal("20"))
        self.assertEqual(result.worker_days, Decimal("650"))
        self.assertEqual(result.fulfilled, (False, True, True, True))
        self.assertTrue(result.requires_full_study)

    def test_saved_review_validation_and_roundtrip(self):
        """Validate saved selections and reject malformed or nonfinite project data."""
        lines = [SafetyLine("A", "ud", "Casco", Decimal("3.50"), Decimal("2"), False)]
        state = serialize_state(
            lines, Decimal("2.5"), centros_asistenciales="Hospital General\nCentro de Salud",
            external_conditions={
                "trafico_existente": {"description": "Un carril ocupado", "applicability": "yes"},
            },
            resumen_actuacion="Renovación del abastecimiento.",
            regulatory_review={
                "context": "contexto revisado", "laws": {"RD614-2001": False},
                "ntps": {"01AAB00001": {"NTP 278": True}},
            },
            chapter_selection={"01#": True, "17#": False},
        )
        self.assertEqual(
            restore_project_state(state)["external_conditions"], state["external_conditions"],
        )
        self.assertEqual(restore_state(state), (lines, Decimal("2.5")))
        self.assertEqual(
            restore_project_state(state)["resumen_actuacion"], "Renovación del abastecimiento.",
        )
        self.assertEqual(
            restore_project_state(state)["regulatory_review"], state["regulatory_review"],
        )
        self.assertEqual(
            restore_project_state(state)["chapter_selection"], state["chapter_selection"],
        )
        self.assertEqual(
            restore_state({key: value for key, value in state.items() if key != "centros_asistenciales"}),
            (lines, Decimal("2.5")),
        )
        for invalid in (
            [], {"lines": []}, {**state, "percentage": "NaN"},
            {**state, "percentage": -1},
            {**state, "centros_asistenciales": ["Hospital General"]},
            {**state, "lines": [state["lines"][0], state["lines"][0]]},
            {**state, "lines": [{**state["lines"][0], "price": "inf"}]},
            {**state, "lines": [{**state["lines"][0], "selected": "yes"}]},
            {**state, "external_conditions": []},
            {**state, "resumen_actuacion": None},
            {**state, "resumen_actuacion": []},
            {**state, "regulatory_review": []},
            {**state, "chapter_selection": []},
            {**state, "chapter_selection": {"01#": "yes"}},
            {**state, "regulatory_review": {
                "context": "x", "laws": {"RD614-2001": "yes"}, "ntps": {},
            }},
            {**state, "regulatory_review": {
                "context": "x", "laws": {}, "ntps": {"01AAB": {"NTP inventada": True}},
            }},
            {**state, "external_conditions": {
                "trafico_existente": {"description": "Obra", "applicability": []},
            }},
            {**state, "external_conditions": {
                "trafico_existente": {"description": "Obra", "applicability": True},
            }},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                restore_state(invalid)

    def test_proposal_and_generated_chapter_preserve_the_original_budget(self):
        source = ProjectItem(
            "01AAB00001", "01#", "m2", "Demolición de bóveda",
            measurement=10, unit_price=15.89,
        )
        lines = proposed_lines([source])
        self.assertTrue(lines)
        self.assertTrue(any(line.code == "19SSW90051" for line in lines))
        app = SimpleNamespace(
            budget_items=[source],
            chapter_order=["01#"],
            chapter_titles={},
            report_settings=ReportSettings(
                general_expenses=0, industrial_profit=0, vat=0,
                author="Autora Alfa\nAutor Beta", role="Cargo de prueba",
            ),
            _ensure_chapter=lambda chapter: None,
            refresh_budget_tree=lambda: None,
            _mark_dirty=lambda: None,
            _project_title=lambda: "Proyecto de prueba",
            _chapter_title=lambda chapter: "SEGURIDAD Y SALUD",
            _report_components=lambda item: [],
            _component_dataset=lambda item: None,
            _budget_concept_type=lambda item, code=None: "0",
            planning_state={},
            safety_health_state={"external_conditions": {
                "trafico_existente": {"description": "Un carril ocupado", "applicability": "yes"},
            }},
        )

        synchronize_budget(app, lines, Decimal("2"))

        self.assertEqual(app.budget_items[0], source)
        self.assertEqual(
            app.safety_health_state["external_conditions"]["trafico_existente"],
            {"description": "Un carril ocupado", "applicability": "yes"},
        )
        generated = [item for item in app.budget_items if item.source.startswith(SAFETY_BUDGET_SOURCE)]
        self.assertTrue(generated)
        self.assertEqual({item.chapter for item in generated}, {"CAP.02#"})
        self.assertEqual(app.chapter_titles["CAP.02#"], "SEGURIDAD Y SALUD")
        self.assertAlmostEqual(
            sum(item.measurement * item.unit_price for item in generated),
            source.measurement * source.unit_price * 0.02,
            delta=0.1,
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "seguridad-salud.pdf"
            write_study_report(app, target)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)
            text = "\n".join(page.extract_text() or "" for page in PdfReader(target).pages)
            self.assertIn("ESTUDIO BÁSICO DE SEGURIDAD Y SALUD", text)
            self.assertIn("ÍNDICE", text)
            self.assertIn("01AAB00001", text)
            self.assertIn("Ficha específica pendiente.", text)
            self.assertNotIn("UO-", text)
            self.assertNotIn("DATOS DEL PROYECTO", text)
            self.assertIn("Tabla 1.", text)
            self.assertIn("PLIEGO DE CONDICIONES", text)
            self.assertNotIn("DESCOMPUESTOS", text)
            self.assertNotIn("PARTIDAS DEL PRESUPUESTO GENERAL", text)
            self.assertNotRegex(text, r"\b3\.11\b(?!\s+OBLIGACIONES\b)")
            self.assertNotIn("{{", text)
            signature = text[text.rindex("Cargo de prueba"):]
            self.assertRegex(signature, r"Cargo de prueba\s+Autora Alfa\s+Autor Beta")


if __name__ == "__main__":
    unittest.main()
