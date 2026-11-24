"""Persistencia local, validación y copias portables de centros de gestión."""
from dataclasses import asdict, replace
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from tools.rcd import RcdState
from tools.rcd.facilities import FacilityCatalog, WasteFacility


class FacilityCatalogTests(unittest.TestCase):
    """Comprueba altas, ediciones y protección de los datos de otros proyectos."""

    def test_import_csv_coordinates_and_distance(self):
        """Importa una fila CSV, conserva NIMA y calcula distancias WGS84."""
        from tools.rcd.facilities import (
            FacilityCatalog, haversine_km, parse_utm_coordinates,
        )

        point = parse_utm_coordinates(
            "ETRS89 / UTM 30N (EPSG:25830): E=576169.61 m, N=4137857.10 m"
        )
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point[0], 37.38434, places=3)
        self.assertAlmostEqual(point[1], -2.13959, places=3)
        self.assertAlmostEqual(haversine_km(0, 0, 0, 1), 111.195, places=2)
        with TemporaryDirectory() as directory:
            csv_path = Path(directory) / "centros.csv"
            csv_path.write_text(
                "\ufeffidInstalacion;denominacionEmpresa;denominacionInstalacion;nima;"
                "numInscripcion;codLer;tiposOperacion;operacionesPorLER;provincia;"
                "municipio;telefono;email;urlDetalle;erroresDetalle;coordenadas\n"
                "2;Empresa;Instalacion;0400000003;AN000019;170204,170301;"
                "R12,R1201;{};Almería;Albox;;;;;"
                "ETRS89 / UTM 30N (EPSG:25830): E=576169.61 m, N=4137857.10 m\n",
                encoding="utf-8",
            )
            catalog = FacilityCatalog(Path(directory) / "centros.sqlite3")
            self.assertEqual(catalog.import_csv(csv_path, replace=True), 1)
            facility = catalog.load()[0]
            self.assertEqual(facility.nima, "0400000003")
            self.assertEqual(facility.ler_codes, "170204, 170301")
            self.assertEqual(facility.municipality, "Albox")

    def test_catalog_grows_and_project_snapshots_remain_independent(self):
        """Reutiliza el catálogo sin alterar copias de proyectos ya guardados."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "centros.sqlite3"
            catalog = FacilityCatalog(path)
            self.assertEqual(catalog.load(), [])
            self.assertFalse(path.exists())
            original = catalog.save(WasteFacility(
                "Planta de prueba", holder="Titular original", nima="0012345678",
            ))
            state = RcdState()
            state.management.facilities = [original]
            saved = state.to_dict()
            edited = replace(original, holder="Titular actualizado", authorization="R5")
            catalog.save(edited, expected=original)
            other = catalog.save(WasteFacility("Otro centro", nima="0098765432"))
            self.assertCountEqual(FacilityCatalog(path).load(), [edited, other])
            restored = RcdState.from_dict(saved)
            self.assertEqual(restored.management.facilities, [original])
            self.assertEqual(restored.management.facilities[0].nima, "0012345678")
            self.assertEqual(RcdState.from_dict({}).management.facilities, [])
            self.assertEqual(restored.to_dict(), saved)

    def test_duplicates_and_stale_edits_do_not_overwrite_catalog(self):
        """Rechaza duplicados por NIMA o identidad y conserva ediciones concurrentes."""
        with TemporaryDirectory() as directory:
            catalog = FacilityCatalog(Path(directory) / "centros.sqlite3")
            original = catalog.save(WasteFacility("Áridos de prueba", holder="Empresa", nima="0012345678"))
            for duplicate in (
                WasteFacility("Otro nombre", nima=original.nima),
                WasteFacility(" ARIDOS  DE PRUEBA ", holder="empresa"),
            ):
                with self.subTest(duplicate=duplicate.name), self.assertRaises(ValueError):
                    catalog.save(duplicate)
                self.assertEqual(catalog.load(), [original])
            edited = replace(original, address="Nuevo acceso")
            catalog.save(edited, expected=original)
            with self.assertRaisesRegex(ValueError, "ha cambiado"):
                catalog.save(replace(original, holder="Edición antigua"), expected=original)
            self.assertEqual(catalog.load(), [edited])

    def test_invalid_project_centers_and_corrupt_catalog_are_reported(self):
        """No interpreta un fichero ilegible ni datos incorrectos como un catálogo vacío."""
        valid = asdict(WasteFacility("Instalación de prueba"))
        for invalid in (
            "centro", {"name": "Sin identificador"}, {**valid, "name": ""},
            {**valid, "nima": "123"}, {**valid, "nima": 1234567890},
            {**valid, "holder": False}, {**valid, "id": "../centro"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                RcdState.from_dict({"management": {"facilities": [invalid]}})
        with self.assertRaises(ValueError):
            RcdState.from_dict({"management": {"facilities": [valid, valid]}})
        with TemporaryDirectory() as directory:
            path = Path(directory) / "centros.sqlite3"
            path.write_text("No es una base SQLite.", encoding="utf-8")
            with self.assertRaises(sqlite3.DatabaseError):
                FacilityCatalog(path).load()
