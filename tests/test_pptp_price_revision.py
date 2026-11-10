"""Revisión de precios condicional y persistencia de los datos del PPTP."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_reports import ReportSettings
from tools.pptp import DOCS_ROOT, LIBRARY_PATH
from tools.pptp.document import (
    VARIABLES, Selection, compose, conditional_markdown, restore_state, substitute, write_document,
    duration_within_two_years, duration_price_revision, SHORT_DURATION_REASON,
)
from tools.pptp.library import ZipLibrary


class PriceRevisionTests(unittest.TestCase):
    def test_calendar_duration_limits_and_unknown_inputs(self):
        """Check exact boundaries including leap years, decimals and unparseable durations."""
        for duration, start, expected in (
            ("2 años", "", True), ("2,01 años", "", False),
            ("24 meses", "", True), ("24.1 meses", "", False),
            ("730 días naturales", "2025-01-01", True),
            ("731 días naturales", "2025-01-01", False),
            ("731 días naturales", "2023-01-01", True),
            ("732 días naturales", "2023-01-01", False),
            ("730 días", "2024-02-29", True), ("731 días", "2024-02-29", False),
            ("730 días", "", None), ("dos años", "", None),
            ("2 años y 1 mes", "", None), ("0 meses", "", None), ("-1 años", "", None),
        ):
            with self.subTest(duration=duration, start=start):
                self.assertIs(duration_within_two_years(duration, start), expected)
        variables = {"plazo_ejecucion": "24 meses", "JUSTIFICACION_NO_APLICACION": "otro motivo"}
        applies, reason = duration_price_revision(variables, True)
        self.assertTrue(applies)
        self.assertEqual(reason, "otro motivo; además, " + SHORT_DURATION_REASON)
        variables["JUSTIFICACION_NO_APLICACION"] = reason
        self.assertEqual(duration_price_revision(variables, False)[1], reason)
        variables["plazo_ejecucion"] = "25 meses"
        self.assertEqual(duration_price_revision(variables, False), (False, "otro motivo"))
        variables["plazo_ejecucion"] = "plazo pendiente de concretar"
        self.assertEqual(duration_price_revision(variables, False), (False, "otro motivo"))
        variables["JUSTIFICACION_NO_APLICACION"] = SHORT_DURATION_REASON
        self.assertEqual(duration_price_revision(variables, False), (False, ""))

    def test_only_selected_branch_requires_its_variables(self):
        """Resolve conditions before variables, keeping inactive data out of the result."""
        source = (
            "Antes {{#if APLICA_REVISION_PRECIOS}}Sí {{FORMULA_REVISION_PRECIOS}}"
            "{{else}}No {{JUSTIFICACION_NO_APLICACION}}{{/if}} Después"
        )
        self.assertEqual(substitute(source, {"FORMULA_REVISION_PRECIOS": "Kt = 1"},
                                    price_revision=True), "Antes Sí Kt = 1 Después")
        self.assertEqual(substitute(source, {"JUSTIFICACION_NO_APLICACION": "motivo"}),
                         "Antes No motivo Después")
        for enabled, required in ((True, "FORMULA_REVISION_PRECIOS"),
                                  (False, "JUSTIFICACION_NO_APLICACION")):
            with self.subTest(enabled=enabled), self.assertRaisesRegex(ValueError, required):
                substitute(source, {}, price_revision=enabled)
        nested = "{{#if APLICA_REVISION_PRECIOS}}A{{#if APLICA_REVISION_PRECIOS}}B{{else}}C{{/if}}{{else}}D{{/if}}"
        self.assertEqual(conditional_markdown(nested, True), "AB")
        self.assertEqual(conditional_markdown(nested, False), "D")

    def test_malformed_or_unknown_conditions_report_errors(self):
        """Never execute arbitrary expressions or silently publish malformed templates."""
        for source in (
            "{{#if OTRA}}Sí{{/if}}", "{{#if __import__('os')}}No{{/if}}",
            "{{else}}", "{{/if}}", "{{#if APLICA_REVISION_PRECIOS}}",
            "{{#if APLICA_REVISION_PRECIOS}}A{{else}}B{{else}}C{{/if}}",
            "{{#if APLICA_REVISION_PRECIOS}}A{{/if extra}}",
        ):
            for enabled in (False, True):
                with self.subTest(source=source, enabled=enabled), self.assertRaises(ValueError):
                    substitute(source, {}, price_revision=enabled)

    def test_old_states_restore_unchecked_without_inventing_justification(self):
        """Open older projects but require the missing active field when generating."""
        legacy = {"variables": {key: "Valor" for key in VARIABLES},
                  "selections": [], "regulations": {}}
        original = deepcopy(legacy)
        restored = restore_state(legacy)
        self.assertFalse(restored["aplica_revision_precios"])
        self.assertEqual(restored["variables"]["JUSTIFICACION_NO_APLICACION"], "")
        self.assertEqual(restored["variables"]["FORMULA_REVISION_PRECIOS"], "")
        self.assertEqual(legacy, original)
        for invalid in ("True", 1, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                restore_state({**legacy, "aplica_revision_precios": invalid})
        for key in ("FORMULA_REVISION_PRECIOS", "JUSTIFICACION_NO_APLICACION"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                restore_state({**legacy, "variables": {**legacy["variables"], key: False}})

    def test_real_library_both_branches_reach_pdf_and_odt_without_tokens(self):
        """Check actual template text, substitution and both document exporters."""
        library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        sheet = next(entry for entry in library.entries.values() if entry.code == "01RPN")
        base = {
            "variables": {key: "Valor de prueba" for key in VARIABLES},
            "selections": [asdict(Selection(0, "01RPN00001", "01#", "Peldaños", sheet.path))],
            "regulations": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            for enabled in (True, False):
                state = deepcopy(base)
                state["aplica_revision_precios"] = enabled
                state["variables"].update(
                    FORMULA_REVISION_PRECIOS="Kt = 0,40 + 0,60 * It / I0" if enabled else "",
                    JUSTIFICACION_APLICACION="Motivo contractual de aplicación" if enabled else "",
                    JUSTIFICACION_NO_APLICACION="" if enabled else "no concurrir los requisitos del contrato de prueba",
                )
                state["variables"]["plazo_ejecucion"] = "24 meses"
                restored = restore_state(state)
                self.assertEqual(restored["aplica_revision_precios"], enabled)
                source = compose(library, restored, [])
                for extension in ("pdf", "odt"):
                    with self.subTest(enabled=enabled, format=extension):
                        target = Path(directory) / (str(enabled) + "." + extension)
                        write_document(source, target, restored["variables"],
                                       ReportSettings(output_format=extension))
                        if extension == "pdf":
                            text = "\n".join(page.extract_text() for page in PdfReader(target).pages)
                        else:
                            with ZipFile(target) as archive:
                                text = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                        text = " ".join(text.split())
                        self.assertNotIn("{{", text)
                        self.assertNotIn("}}", text)
                        if enabled:
                            self.assertIn("ESTÁ SUJETO A REVISIÓN DE PRECIOS", text)
                            self.assertIn(state["variables"]["FORMULA_REVISION_PRECIOS"], text)
                            self.assertIn(state["variables"]["JUSTIFICACION_APLICACION"], text)
                            self.assertNotIn("NO PROCEDE", text)
                        else:
                            self.assertIn("NO PROCEDE", text)
                            self.assertIn(state["variables"]["JUSTIFICACION_NO_APLICACION"], text)
                            self.assertNotIn("ESTÁ SUJETO A REVISIÓN DE PRECIOS", text)
                            self.assertNotIn("Kt =", text)
                            self.assertIn(SHORT_DURATION_REASON, text)


if __name__ == "__main__":
    unittest.main()
