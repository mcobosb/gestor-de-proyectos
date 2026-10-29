from decimal import Decimal
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from bc3_gui import ProjectItem
from pypdf import PdfReader
from safety_health import SAFETY_BUDGET_SOURCE, proposed_lines, synchronize_budget, write_report


class SafetyHealthTests(unittest.TestCase):
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
            report_settings=SimpleNamespace(
                indirect_costs=0, template_path="", currency="EUR", custom_currency="", author="",
            ),
            _ensure_chapter=lambda chapter: None,
            refresh_budget_tree=lambda: None,
            _mark_dirty=lambda: None,
            _project_title=lambda: "Proyecto de prueba",
            _chapter_title=lambda chapter: "SEGURIDAD Y SALUD",
            _report_components=lambda item: [],
        )

        synchronize_budget(app, lines, Decimal("2"))

        self.assertEqual(app.budget_items[0], source)
        generated = [item for item in app.budget_items if item.source.startswith(SAFETY_BUDGET_SOURCE)]
        self.assertTrue(generated)
        self.assertEqual({item.chapter for item in generated}, {"SS#"})
        self.assertEqual(app.chapter_titles["SS#"], "SEGURIDAD Y SALUD")
        self.assertAlmostEqual(
            sum(item.measurement * item.unit_price for item in generated),
            source.measurement * source.unit_price * 0.02,
            delta=0.1,
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "seguridad-salud.pdf"
            write_report(app, target)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)
            text = "\n".join(page.extract_text() or "" for page in PdfReader(target).pages)
            self.assertIn("PRESUPUESTO DE SEGURIDAD Y SALUD", text)
            self.assertNotIn("DESCOMPUESTOS", text)
            self.assertIn("3,11", text)
            self.assertNotIn("3.11", text)


if __name__ == "__main__":
    unittest.main()
