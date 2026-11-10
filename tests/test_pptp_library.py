"""Comprobaciones de catalogo JSON y lectura ZIP sin extraccion."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools.pptp.document import compose, load_catalog, load_regulations
from tools.pptp.library import ZipLibrary, validate_relative


class ZipLibraryTests(unittest.TestCase):
    """Usa bibliotecas sinteticas para comprobar errores sin alterar la real."""

    def setUp(self):
        """Prepara dos fichas, un indice auxiliar y plantillas independientes."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / "BIBLIOTECA.zip"
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.content = "# Ficha sintética\n\nTexto técnico."
        self.rows = [{
            "id": "UNI-01TR-REDES", "codigo_uni": "01TR", "titulo": "Redes",
            "archivo": "01_BASE/01T_PREVIOS/01TR_REDES/REDES.md",
            "seleccion": {"requiere_confirmacion": True},
        }, {
            "id": "UNI-01TR-OTRA", "codigo_uni": "01TR", "titulo": "Otra variante",
            "archivo": "01_BASE/01T_PREVIOS/01TR_REDES/OTRA.md",
        }]
        self.catalog = {
            "formato": "biblioteca-fichas-uni", "version_esquema": 1,
            "total_fichas": 2, "indice": "INDICE_FICHAS.md", "fichas": self.rows,
        }
        self.members = {
            "archivos/UNI/INDICE_FICHAS.md": "Indice legible, no se usa para buscar",
            "archivos/UNI/" + self.rows[0]["archivo"]: self.content,
            "archivos/UNI/" + self.rows[1]["archivo"]: "# Otra ficha",
        }

    def write_zip(self, catalog=None, members=None):
        """Escribe el archivo temporal, sin crear carpetas de extraccion."""
        with ZipFile(self.archive, "w") as archive:
            archive.writestr(
                "archivos/UNI/catalogo.json",
                json.dumps(self.catalog if catalog is None else catalog, ensure_ascii=False),
            )
            for name, content in (self.members if members is None else members).items():
                archive.writestr(name, content)

    def test_reads_json_without_scanning_markdown_and_preserves_variants(self):
        """La busqueda usa metadatos; solo la lectura explicita abre la ficha."""
        self.write_zip()
        with patch.object(ZipLibrary, "read_sheet", side_effect=AssertionError("Ficha leida")):
            library = ZipLibrary(self.archive, self.docs)
            catalog = load_catalog(library)
        self.assertEqual(len(catalog), 2)
        self.assertEqual(catalog[0].code, catalog[1].code)
        self.assertNotEqual(catalog[0].path, catalog[1].path)
        self.assertTrue(catalog[0].warning)
        self.assertEqual(library.read_sheet(catalog[0].path), self.content)
        self.assertFalse((self.root / "archivos").exists())

    def test_rejects_bad_schema_count_duplicate_ids_and_paths(self):
        """Rechaza catalogos inconsistentes en lugar de devolver listas parciales."""
        bad_catalogs = [
            [], {**self.catalog, "version_esquema": 2},
            {**self.catalog, "version_esquema": True},
            {**self.catalog, "total_fichas": 3},
            {**self.catalog, "fichas": []},
            {**self.catalog, "indice": "../INDICE_FICHAS.md"},
        ]
        for field, value in (
            ("id", self.rows[0]["id"]), ("archivo", self.rows[0]["archivo"]),
            ("codigo_uni", "99XX"), ("archivo", "../fuera.md"),
            ("archivo", "C:/fuera.md"), ("archivo", r"UNI\fuera.md"),
            ("titulo", 5), ("seleccion", []),
        ):
            catalog = deepcopy(self.catalog)
            catalog["fichas"][1][field] = value
            bad_catalogs.append(catalog)
        for catalog in bad_catalogs:
            with self.subTest(catalog=catalog):
                self.write_zip(catalog)
                with self.assertRaises(ValueError):
                    ZipLibrary(self.archive, self.docs)

    def test_chapter_metadata_supplies_titles_and_rejects_inconsistent_groups(self):
        """Lee el titulo del catalogo y rechaza codigos o titulos contradictorios."""
        for row in self.rows:
            row["capitulo"] = {"codigo": "01", "titulo": "Trabajos previos"}
        self.write_zip()
        self.assertEqual(
            ZipLibrary(self.archive, self.docs).chapter_titles, {"01": "Trabajos previos"},
        )
        for chapter in (
            [], {"codigo": "02", "titulo": "Otro"},
            {"codigo": "01", "titulo": ""}, {"codigo": "01", "titulo": 1},
            {"codigo": "01", "titulo": "Nombre contradictorio"},
        ):
            catalog = deepcopy(self.catalog)
            catalog["fichas"][1]["capitulo"] = chapter
            self.write_zip(catalog)
            with self.subTest(chapter=chapter), self.assertRaisesRegex(ValueError, "[Cc]apítulo"):
                ZipLibrary(self.archive, self.docs)

    def test_rejects_missing_catalog_index_sheet_and_ambiguous_catalogs(self):
        """Todos los nombres declarados deben existir y no ser ambiguos."""
        for missing in ("archivos/UNI/INDICE_FICHAS.md",
                        "archivos/UNI/" + self.rows[0]["archivo"]):
            with self.subTest(missing=missing):
                self.write_zip(members={k: v for k, v in self.members.items() if k != missing})
                with self.assertRaises(ValueError):
                    ZipLibrary(self.archive, self.docs)
        with ZipFile(self.archive, "w") as archive:
            archive.writestr("notas.md", "Sin catálogo")
        with self.assertRaises(ValueError):
            ZipLibrary(self.archive, self.docs)
        self.write_zip()
        with ZipFile(self.archive, "a") as archive:
            archive.writestr("otro/catalogo.json", "{}")
        with self.assertRaises(ValueError):
            ZipLibrary(self.archive, self.docs)

    def test_rejects_archive_paths_outside_package_without_extraction(self):
        """No acepta entradas peligrosas ni necesita escribir recursos al disco."""
        for name in ("../outside.md", "/absolute.md", "C:/outside.md"):
            with self.subTest(name=name):
                self.write_zip(members={**self.members, name: "contenido"})
                with self.assertRaises(ValueError):
                    ZipLibrary(self.archive, self.docs)
        with self.assertRaises(ValueError):
            validate_relative(r"bad\path.md")
        self.assertFalse((self.root / "outside.md").exists())

    def test_reads_modified_selected_sheet_without_digest(self):
        """La ficha se lee aunque el catálogo no declare una suma de verificación."""
        members = dict(self.members)
        members["archivos/UNI/" + self.rows[0]["archivo"]] = "Texto modificado"
        self.write_zip(members=members)
        library = ZipLibrary(self.archive, self.docs)
        self.assertEqual(len(load_catalog(library)), 2)
        self.assertEqual(
            library.read_sheet("UNI/" + self.rows[0]["archivo"]), "Texto modificado",
        )
        with self.assertRaises(ValueError):
            library.read_sheet("UNI/no_catalogada.md")

    def test_corrupt_json_zip_and_oversized_resources_are_reported(self):
        """No oculta errores de formato, corrupcion o recursos desproporcionados."""
        self.archive.write_bytes(b"not a zip")
        with self.assertRaises(ValueError):
            ZipLibrary(self.archive, self.docs)
        self.write_zip()
        with patch("tools.pptp.library.MAX_RESOURCE_BYTES", 5):
            with self.assertRaises(ValueError):
                ZipLibrary(self.archive, self.docs)
        with ZipFile(self.archive, "w") as archive:
            archive.writestr("catalogo.json", "{broken")
        with self.assertRaises(ValueError):
            ZipLibrary(self.archive, self.docs)

    def test_changing_archive_requires_reopening_assistant(self):
        """No mezcla un catalogo antiguo con contenido de otra biblioteca."""
        self.write_zip()
        library = ZipLibrary(self.archive, self.docs)
        with self.archive.open("ab") as archive:
            archive.write(b"changed")
        with self.assertRaisesRegex(ValueError, "ha cambiado"):
            library.read_sheet("UNI/" + self.rows[0]["archivo"])

    def test_reads_root_base_documents_before_legacy_external_docs(self):
        """Prefiere documentos base del ZIP y mantiene el respaldo para bibliotecas antiguas."""
        self.write_zip()
        with ZipFile(self.archive, "a") as archive:
            archive.writestr("01_intro.md", "# Base guardada en el ZIP")
        (self.docs / "01_intro.md").write_text("# Copia externa antigua", encoding="utf-8")
        library = ZipLibrary(self.archive, self.docs)
        self.assertEqual(
            library.read_documents("01"), [("01_intro.md", "# Base guardada en el ZIP")],
        )

    def test_composes_external_templates_and_only_selected_zip_sheet(self):
        """Genera con docs y fichas ZIP sin incorporar documentos fuente del paquete."""
        self.write_zip()
        (self.docs / "01_intro.md").write_text(
            "# 1 INTRODUCCIÓN\n\n{{titulo_proyecto}}", encoding="utf-8",
        )
        (self.docs / "02_legal.md").write_text(
            '# 2 DISPOSICIONES\n\n<!-- NORMATIVA_UNI '
            '{"id":"X","uni":["*"],"seleccion":"candidata","condicion":"Revisar"} -->\n'
            '- Norma sintética', encoding="utf-8",
        )
        (self.docs / "03_description.md").write_text("# 3 DESCRIPCIÓN", encoding="utf-8")
        (self.docs / "04_sheets.md").write_text("# 4 MATERIALES", encoding="utf-8")
        library = ZipLibrary(self.archive, self.docs)
        state = {
            "variables": {
                "titulo_proyecto": "Proyecto sintético", "entidad": "Entidad",
                "plazo_garantia": "12 meses", "plazo_ejecucion": "2 meses",
                "bullet_capitulos_obra": "- Redes",
            },
            "selections": [{
                "item": 0, "code": "01TR001", "chapter": "01#", "summary": "Redes",
                "path": "UNI/" + self.rows[0]["archivo"], "selected": True, "automatic": True,
            }],
            "regulations": {"X": True},
        }
        source = compose(library, state, load_regulations(library, {"01TR001"}))
        self.assertIn("Proyecto sintético", source)
        self.assertIn("Texto técnico", source)
        self.assertIn("Norma sintética", source)
        self.assertIn("## 4.1 BASE", source)
        self.assertIn("### 4.1.1 FICHA SINTÉTICA", source)
        self.assertNotIn("Otra ficha", source)
        self.assertNotIn("Indice legible", source)
        for row in self.rows:
            row["capitulo"] = {"codigo": "01", "titulo": "Nombre del catálogo"}
        self.write_zip()
        library = ZipLibrary(self.archive, self.docs)
        source = compose(library, state, [])
        self.assertIn("## 4.1 NOMBRE DEL CATÁLOGO", source)
        self.assertNotIn("## 4.1 BASE", source)
        (self.docs / "03_description.md").unlink()
        with self.assertRaisesRegex(ValueError, "sección 03"):
            compose(library, state, [])


if __name__ == "__main__":
    unittest.main()
