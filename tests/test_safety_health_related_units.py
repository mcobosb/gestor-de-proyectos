"""Pruebas de vínculos por familias BCCA para Seguridad y Salud."""
import unittest
from decimal import Decimal
import unicodedata

from tools.safety_health.related_units import (
    RelatedSafetyItem,
    SafetyFamilyLink,
    load_related_safety_data,
    related_alternative_groups_for_work_families,
    related_items_for_work_families,
)


def _summary_key(value: str) -> str:
    """Normaliza una descripcion para comparar palabras clave del catálogo."""
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode().casefold()


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

    def test_real_catalog_groups_gloves_footwear_glasses_and_respirators(self):
        """Comprueba que las variantes del catálogo real ofrecen una opción por tipo."""
        family_links, catalog = load_related_safety_data()
        groups = related_alternative_groups_for_work_families(
            ["01AAB"], family_links, catalog,
        )
        group_by_code = {
            item.code: frozenset(member.code for member in group)
            for group in groups for item in group
        }

        for family, keywords in (
            ("19SIM", ("guante",)),
            ("19SIP", ("bota", "zapato")),
            ("19SIC", ("gafa",)),
        ):
            matches = {
                item.code for item in catalog[family]
                if any(keyword in _summary_key(item.summary) for keyword in keywords)
            }
            self.assertTrue(matches)
            self.assertEqual(
                {group_by_code[code] for code in matches},
                {frozenset(matches)},
            )

        masks = {
            item.code for item in catalog["19SIC"]
            if "mascarilla" in _summary_key(item.summary)
            or _summary_key(item.summary).startswith("semimascara respir")
        }
        self.assertEqual(
            {group_by_code[code] for code in masks},
            {frozenset(masks)},
        )
        filters = {
            item.code for item in catalog["19SIC"]
            if _summary_key(item.summary).startswith("filtro semimascara")
        }
        self.assertEqual(
            {group_by_code[code] for code in filters},
            {frozenset(filters)},
        )
        self.assertNotEqual(group_by_code[next(iter(masks))], group_by_code[next(iter(filters))])

        catalog_groups = related_alternative_groups_for_work_families(
            family_links.keys(), family_links, catalog,
        )
        catalog_group_by_code = {
            item.code: frozenset(member.code for member in group)
            for group in catalog_groups for item in group
        }
        railing_codes = {item.code for item in catalog["19SCB"]}
        self.assertTrue(railing_codes)
        self.assertEqual(
            {catalog_group_by_code[code] for code in railing_codes},
            {frozenset(railing_codes)},
        )

        for family, prefix in (
            ("19SCV", "conducto vent"),
            ("19SIT", "arnes anticaidas"),
            ("19SIT", "cinturon"),
            ("19SSA", "lampara intermitente"),
            ("19SCR", "proteccion perimetro forj"),
        ):
            matches = {
                item.code for item in catalog[family]
                if _summary_key(item.summary).startswith(prefix)
            }
            self.assertTrue(matches)
            self.assertEqual(
                {catalog_group_by_code[code] for code in matches},
                {frozenset(matches)},
            )

        self.assertNotEqual(
            catalog_group_by_code["19SSA00029"],
            catalog_group_by_code["19SSA00011"],
        )

    def test_only_same_type_size_and_duration_variants_are_grouped(self):
        """Mantiene separados los productos distintos de una misma familia."""
        alternatives = (
            RelatedSafetyItem(
                "19LPA90010", "u", "Aseos", "Aseos",
                "CASETA PREF. MOD. 20,50 m2 ASEOS DURACIÓN MENOR A 6 MESES",
                Decimal("1263.75"),
            ),
            RelatedSafetyItem(
                "19LPA90011", "u", "Aseos", "Aseos",
                "CASETA PREF. MOD. 15 m2 ASEOS DURACIÓN DE 6 A 12 MESES",
                Decimal("1090.78"),
            ),
        )
        distinct = (
            RelatedSafetyItem(
                "19SIC10001", "u", "Cabeza", "Cabeza",
                "PROTECTOR AUDITIVO CASQUETES", Decimal("23"),
            ),
            RelatedSafetyItem(
                "19SIC20001", "u", "Cabeza", "Cabeza",
                "GAFAS MONTURA ACETATO", Decimal("13.72"),
            ),
        )
        links = {
            "01AAA": (
                SafetyFamilyLink("19LPA", "Aseos", "Criterio"),
                SafetyFamilyLink("19SIC", "Cabeza", "Criterio"),
            ),
        }
        catalog = {
            "19LPA": alternatives,
            "19SIC": distinct,
        }

        groups = related_alternative_groups_for_work_families(
            ["01AAA"], links, catalog,
        )

        self.assertEqual([len(group) for group in groups], [2, 1, 1])
        self.assertEqual(
            {item.code for item in groups[0]},
            {"19LPA90010", "19LPA90011"},
        )

    def test_groups_material_and_sign_variants_without_merging_protection_types(self):
        """Agrupa alternativas de montaje y señal sin mezclar protecciones distintas."""
        items = (
            RelatedSafetyItem(
                "19SCB00001", "m", "Protecciones", "Barandillas",
                "BARANDILLA DE PROTECCIÓN, MADERA, SIST. MORDAZA, BORDE",
                Decimal("10"),
            ),
            RelatedSafetyItem(
                "19SCB90002", "m", "Protecciones", "Barandillas",
                "BARANDILLA DE PROTECCIÓN, METÁLICA, SIST. MORDAZA, BORDE",
                Decimal("12"),
            ),
            RelatedSafetyItem(
                "19SSS90101", "u", "Señalización", "Señales",
                'SEÑAL METÁLICA "OBLIG. PROH." 42 cm, CON SOPORTE METÁLICO',
                Decimal("8"),
            ),
            RelatedSafetyItem(
                "19SSS90202", "u", "Señalización", "Señales",
                'SEÑAL PVC. "OBLIG. PROH." 30 cm SIN SOPORTE',
                Decimal("4"),
            ),
            RelatedSafetyItem(
                "19SSS90211", "u", "Señalización", "Señales",
                'SEÑAL PVC. "ADVERTENCIA" 30 cm SOP. MET.',
                Decimal("4"),
            ),
            RelatedSafetyItem(
                "19SIC90001", "u", "Cabeza", "Cascos",
                "CASCO SEG. CONTRA IMPACTOS POLIETILENO ALTA",
                Decimal("10"),
            ),
            RelatedSafetyItem(
                "19SIC90002", "u", "Cabeza", "Cascos",
                "CASCO SEG. DIELÉCTRICO POLIETILENO ALTA",
                Decimal("12"),
            ),
        )
        links = {
            "01AAA": tuple(
                SafetyFamilyLink(family, "Protecciones", "Criterio")
                for family in ("19SCB", "19SSS", "19SIC")
            ),
        }
        catalog = {
            family: tuple(item for item in items if item.code.startswith(family))
            for family in ("19SCB", "19SSS", "19SIC")
        }

        groups = related_alternative_groups_for_work_families(
            ["01AAA"], links, catalog,
        )
        grouped_codes = [
            {item.code for item in group}
            for group in groups
            if len(group) > 1
        ]

        self.assertEqual(
            grouped_codes,
            [
                {"19SCB00001", "19SCB90002"},
                {"19SIC90001", "19SIC90002"},
                {"19SSS90101", "19SSS90202"},
            ],
        )
