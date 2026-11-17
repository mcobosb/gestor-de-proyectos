"""Pruebas del vocabulario CPV de construccion y su seleccion persistida."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from openpyxl import Workbook

from budget_reports import ReportSettings
from cpv_catalog import load_cpv_codes


class CpvCatalogTests(unittest.TestCase):
    """Comprueba el origen de las descripciones y la limitacion al capitulo 45."""

    def test_supplied_catalog_preserves_spanish_descriptions_and_check_digits(self):
        """Lee desde el ZIP real tanto la division como sus codigos especificos."""
        entries = load_cpv_codes()
        by_code = {entry.code: entry.description for entry in entries}
        self.assertEqual(by_code["45000000-7"], "Trabajos de construcci\u00f3n")
        self.assertEqual(by_code["45111100-9"], "Trabajos de demolici\u00f3n")
        self.assertIn("45520000-8", by_code)
        self.assertTrue(all(entry.code.startswith("45") for entry in entries))
        self.assertEqual([entry.code for entry in entries], sorted(by_code))

    def test_settings_validate_and_restore_multiple_full_codes(self):
        """Guarda varios CPV sin admitir duplicados, codigos parciales u otros capitulos."""
        codes = ["45000000-7", "45111100-9"]
        settings = ReportSettings.from_dict({"cpv_codes": codes})
        codes.clear()
        restored = ReportSettings.from_dict(json.loads(json.dumps(settings.to_dict())))
        self.assertEqual(restored.cpv_codes, ["45000000-7", "45111100-9"])
        self.assertEqual(restored.cpv_entries[1].description, "Trabajos de demolici\u00f3n")
        self.assertEqual(ReportSettings.from_dict({"author": "Autor anterior"}).cpv_codes, [])
        for invalid in (
            "45000000-7", [45000000], [True], ["45000000"],
            ["03000000-1"], ["45999999-9"], ["45000000-7", "45000000-7"],
        ):
            with self.subTest(codes=invalid), self.assertRaises(ValueError):
                ReportSettings.from_dict({"cpv_codes": invalid})

    def test_reader_uses_main_vocabulary_and_es_column_and_reports_bad_source(self):
        """Filtra capitulos sin confundir columnas de idiomas ni vocabulario suplementario."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tools = root / "tools"
            tools.mkdir()
            path = tools / "cpv_2008_xls.zip"
            load_cpv_codes.cache_clear()
            self.addCleanup(load_cpv_codes.cache_clear)
            with patch("cpv_catalog.application_directory", return_value=root):
                with self.assertRaisesRegex(ValueError, "No se pudo leer el catalogo CPV"):
                    load_cpv_codes()
                workbook = Workbook()
                sheet = workbook.active
                sheet.title = "CPV codes"
                sheet.append(("CODE", "ES", "EN"))
                sheet.append(("03000000-1", "Otro capitulo", "Other division"))
                sheet.append(("45000000-7", "Construcci\u00f3n de prueba", "Test construction"))
                supplementary = workbook.create_sheet("Supplementary codes")
                supplementary.append(("CODE", "ES", "EN"))
                supplementary.append(("45999999-9", "No incluir", "Do not include"))
                data = BytesIO()
                workbook.save(data)
                workbook.close()
                with ZipFile(path, "w") as archive:
                    archive.writestr("cpv_2008_ver_2013.xlsx", data.getvalue())
                entries = load_cpv_codes()
                self.assertEqual(
                    [(entry.code, entry.description) for entry in entries],
                    [("45000000-7", "Construcci\u00f3n de prueba")],
                )
                load_cpv_codes.cache_clear()
                with ZipFile(path, "w") as archive:
                    archive.writestr("cpv_2008_ver_2013.xlsx", b"Libro no valido")
                with self.assertRaisesRegex(ValueError, "No se pudo leer el catalogo CPV"):
                    load_cpv_codes()


if __name__ == "__main__":
    unittest.main()
