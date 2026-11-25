"""Pruebas de vínculos por familias BCCA para Seguridad y Salud."""
import unittest
from decimal import Decimal

from tools.safety_health.related_units import (
    RelatedSafetyItem,
    SafetyFamilyLink,
    load_related_safety_data,
    related_items_for_work_families,
)


class SafetyHealthRelatedUnitsTests(unittest.TestCase):
    def test_work_family_links_resolve_to_concrete_safety_catalog_items(self):
        """Expone las unidades del catálogo sin materializarlas en el presupuesto."""
        family_links, catalog = load_related_safety_data()

        links = family_links["01AAB"]
        self.assertTrue(links)
        self.assertTrue(any(link.code == "19SIC" for link in links))
        self.assertTrue(catalog["19SIC"])
        self.assertTrue(all(unit.code.startswith("19SIC") for unit in catalog["19SIC"]))
        self.assertTrue(all(unit.unit and unit.summary for unit in catalog["19SIC"]))

    def test_related_items_are_deduplicated_across_work_families(self):
        """Una misma unidad vinculada desde varias familias se propone una sola vez."""
        unit = RelatedSafetyItem(
            "19SIC00001", "ud", "Protecciones", "Colectivas", "Barandilla",
            Decimal("12.50"),
        )
        links = {
            "01AAA": (SafetyFamilyLink("19SIC", "Protecciones", "Criterio"),),
            "02BBB": (SafetyFamilyLink("19SIC", "Protecciones", "Criterio"),),
        }
        catalog = {"19SIC": (unit,)}

        result = related_items_for_work_families(
            ["01AAA", "02BBB", "01AAA"], links, catalog,
        )

        self.assertEqual(result, (unit,))
