"""Selecciones normativas persistentes y redacción sin declaraciones de conformidad."""
import unittest

from budget_reports import ReportSettings
from project_standards import DOCUMENT_CONTROL_TEXT, document_control_text


class ProjectStandardsTests(unittest.TestCase):
    """Comprueba compatibilidad, validación y adaptación del párrafo documental."""

    def test_older_projects_have_no_implicit_normative_selections(self):
        """Los proyectos anteriores y los nuevos no afirman referencias no elegidas."""
        settings = ReportSettings.from_dict({"author": "Autor"})
        self.assertEqual(settings.project_disciplines, [])
        self.assertEqual(settings.document_standards, [])
        self.assertEqual(document_control_text([], []), DOCUMENT_CONTROL_TEXT)

    def test_invalid_selections_are_reported(self):
        """Rechaza identificadores desconocidos y valores JSON de tipo incorrecto."""
        for field in ("project_disciplines", "document_standards"):
            for invalid in ("civil", [1], ["unknown"]):
                with self.subTest(field=field, value=invalid), self.assertRaises(ValueError):
                    ReportSettings.from_dict({field: invalid})

    def test_text_changes_with_disciplines_and_only_selected_standards(self):
        """La información BIM y de sistemas no aparece en un proyecto que no la selecciona."""
        settings = ReportSettings.from_dict({
            "project_disciplines": ["civil", "installations"],
            "document_standards": ["une_157001", "iso_10006"],
        })
        restored = ReportSettings.from_dict(settings.to_dict())
        text = document_control_text(restored.project_disciplines, restored.document_standards)
        self.assertIn("Obra civil y urbanización; Instalaciones", text)
        self.assertIn("UNE 157001:2014", text)
        self.assertIn("ISO 10006:2017", text)
        self.assertIn("no acredita por sí sola el cumplimiento", text)
        self.assertNotIn("BIM", text)
        self.assertNotIn("ISO 21502", text)
        systems = document_control_text(["systems"], ["iso_15288", "iso_29148"])
        self.assertIn("Sistemas y tecnologías de la información", systems)
        self.assertIn("15288:2023", systems)
        self.assertIn("29148:2018", systems)
        self.assertNotIn("UNE 157001", systems)
