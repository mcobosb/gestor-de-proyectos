"""Pruebas del enlace CMUN-CPRO y la seleccion territorial persistida."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from budget_reports import ReportSettings
from municipalities import load_municipalities


class MunicipalityTests(unittest.TestCase):
    """Comprueba los datos oficiales sin deducir provincias a partir del nombre."""

    def test_catalog_joins_codes_preserving_accents_and_leading_zeroes(self):
        """Lee Windows-1252 y resuelve provincias con codigos de una o dos cifras."""
        municipalities = load_municipalities()
        lucena = next(item for item in municipalities if item.code == "140386")
        self.assertEqual(lucena.name, "Lucena")
        self.assertEqual(lucena.province, "C\u00f3rdoba")
        self.assertEqual(lucena.autonomous_community, "Andaluc\u00eda")
        alava = next(item for item in municipalities if item.code == "010014")
        self.assertEqual(alava.name, "Alegr\u00eda-Dulantzi")
        self.assertEqual(alava.province, "Araba/\u00c1lava")
        self.assertEqual(alava.autonomous_community, "Pa\u00eds Vasco")
        names = [item.name for item in municipalities]
        self.assertLess(names.index("Abad\u00edn"), names.index("Abadi\u00f1o"))
        homonyms = [item for item in municipalities if item.name == "Cabanes"]
        self.assertEqual(len(homonyms), 2)
        self.assertNotEqual(homonyms[0].code, homonyms[1].code)
        self.assertNotEqual(homonyms[0].label, homonyms[1].label)

    def test_settings_round_trip_multiple_municipalities_and_legacy_location(self):
        """Serializa codigos estables y conserva Localidad de proyectos anteriores."""
        codes = ["140386", "280796"]
        settings = ReportSettings.from_dict({
            "municipality_codes": codes, "location": "Direcci\u00f3n de la obra",
        })
        codes.clear()
        restored = ReportSettings.from_dict(json.loads(json.dumps(settings.to_dict())))
        self.assertEqual(restored.municipality_codes, ["140386", "280796"])
        self.assertEqual([item.name for item in restored.municipalities], ["Lucena", "Madrid"])
        self.assertEqual(restored.location, "Direcci\u00f3n de la obra")
        legacy = ReportSettings.from_dict({"location": "Lucena"})
        self.assertEqual(legacy.municipalities, ())
        self.assertEqual(legacy.location, "Lucena")

    def test_settings_reject_unknown_duplicate_or_malformed_codes(self):
        """Evita municipios inventados y selecciones que no puedan restaurarse."""
        for codes in ("140386", [140386], [True], ["999999"], ["140386", "140386"]):
            with self.subTest(codes=codes), self.assertRaises(ValueError):
                ReportSettings.from_dict({"municipality_codes": codes})

    def test_missing_or_unlinked_catalog_is_reported_explicitly(self):
        """No sustituye errores de los CSV por un catalogo vacio."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tools = root / "tools"
            tools.mkdir()
            load_municipalities.cache_clear()
            self.addCleanup(load_municipalities.cache_clear)
            with patch("municipalities.application_directory", return_value=root):
                with self.assertRaisesRegex(ValueError, "CPRO.csv"):
                    load_municipalities()
                (tools / "CPRO.csv").write_text(
                    "CPRO,Provincia,Comunidad Aut\u00f3noma\n14,C\u00f3rdoba,Andaluc\u00eda\n",
                    encoding="cp1252",
                )
                (tools / "CMUN.csv").write_text(
                    "Titulo del catalogo,,\nCPRO,CMUN,NOMBRE\n99,0014,Inexistente\n",
                    encoding="cp1252",
                )
                with self.assertRaisesRegex(ValueError, "sin provincia valida"):
                    load_municipalities()


if __name__ == "__main__":
    unittest.main()
