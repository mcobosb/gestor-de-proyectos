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
        self.assertIn("obra civil y urbanización; instalaciones", text)
        self.assertIn("UNE 157001:2014", text)
        self.assertIn("ISO 10006:2017", text)
        self.assertIn("sin que su mera selección implique la acreditación formal", text)
        self.assertNotIn("BIM", text)
        self.assertNotIn("ISO 21502", text)
        systems = document_control_text(["systems"], ["iso_15288", "iso_29148"])
        self.assertIn("sistemas y tecnologías de la información", systems)
        self.assertIn("15288:2023", systems)
        self.assertIn("29148:2018", systems)
        self.assertNotIn("UNE 157001", systems)

    def test_civil_project_text_matches_the_requested_wording(self):
        """Renderiza el ámbito y la referencia sin dejar variables de plantilla."""
        self.assertEqual(document_control_text(["civil"], ["une_157001"]), (
            "Esta hoja tiene por objeto identificar los documentos que integran el proyecto y llevar "
            "el registro de su edición, revisión y validación. "
            "Para un proyecto en el ámbito de obra civil y urbanización, se toman como marco de "
            "referencia aplicable la norma UNE 157001:2014 (criterios generales para la elaboración "
            "formal de los documentos que constituyen un proyecto técnico). "
            "Dichas referencias orientan la estructura y el control documental del trabajo, "
            "sin que su mera selección implique la acreditación formal del cumplimiento normativo "
            "ni la certificación del proyecto."
        ))

    def test_partial_selections_do_not_invent_scopes_or_references(self):
        """Los proyectos con una sola categoría seleccionada conservan un texto coherente."""
        for discipline, label in (("civil", "obra civil y urbanización"),
                                  ("industrial", "ingeniería industrial")):
            with self.subTest(discipline=discipline):
                scope_only = document_control_text([discipline], [])
                self.assertIn(f"El proyecto se desarrolla en el ámbito de {label}.", scope_only)
                self.assertNotIn("referencia aplicable", scope_only)
        references_only = document_control_text([], ["une_157001"])
        self.assertIn("Se toman como marco de referencia aplicable la norma UNE 157001:2014", references_only)
        self.assertNotIn("en el ámbito de", references_only)
        self.assertNotIn("{{", references_only)
