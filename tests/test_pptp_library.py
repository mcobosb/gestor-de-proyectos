"""Comprobaciones de catalogo JSON y lectura ZIP sin extraccion."""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools.pptp.document import VARIABLES, compose, load_catalog, load_documents, load_regulations
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

    def test_expanded_general_documents_and_relocated_regulations_keep_their_order(self):
        """Read every numbered base, filtering only annotated bullets wherever they occur."""
        bases = {f"{index:02}_Base.md": f"# {index} Sección {index}\n\nTexto base {index}."
                 for index in reversed(range(1, 11))}
        bases["02_Base.md"] += "\n\nDisposiciones generales que deben conservarse."
        bases["07_Base.md"] += (
            '\n\n<!-- NORMATIVA_UNI {"id":"A","uni":["*"],'
            '"seleccion":"candidata","condicion":"Revisar A"} -->\n- Norma elegida A\n'
            '\n## Normas adicionales\n\nTexto intermedio.\n'
            '<!-- NORMATIVA_UNI {"id":"B","uni":["05"],'
            '"seleccion":"candidata","condicion":"Revisar B"} -->\n- Norma ajena B\n'
            '<!-- NORMATIVA_UNI {"id":"C","uni":["01TR"],'
            '"seleccion":"revision","condicion":"Revisar C"} -->\n- Norma opcional C\n'
            '\nTexto final obligatorio de la sección normativa.'
        )
        bases["08_Base.md"] += (
            '\n\n<!-- NORMATIVA_UNI {"id":"A","uni":["*"],'
            '"seleccion":"candidata","condicion":"Revisar A"} -->\n- Norma elegida A\n'
        )
        self.write_zip(members={**self.members, **bases, "notas.md": "No publicar notas."})
        for name, content in bases.items():
            (self.docs / name).write_text(content, encoding="utf-8")
        (self.docs / "notas.md").write_text("No publicar notas.", encoding="utf-8")
        sheet = self.docs.joinpath("UNI", *Path(self.rows[0]["archivo"]).parts)
        sheet.parent.mkdir(parents=True)
        sheet.write_text(self.content, encoding="utf-8")
        state = {
            "variables": {key: "Valor" for key in VARIABLES},
            "selections": [{
                "item": 0, "code": "01TR001", "chapter": "01#", "summary": "Redes",
                "path": "UNI/" + self.rows[0]["archivo"], "selected": True, "automatic": True,
            }],
            "regulations": {},
        }
        for root in (ZipLibrary(self.archive, self.docs), self.docs):
            with self.subTest(root=type(root).__name__):
                refs = load_regulations(root, {"01TR001"})
                self.assertEqual([ref.id for ref in refs], ["A", "C"])
                self.assertFalse(refs[1].selected)
                state["regulations"] = {}
                source = compose(root, state, refs)
                positions = [source.index(f"# {index} SECCIÓN {index}") for index in range(1, 11)]
                self.assertEqual(positions, sorted(positions))
                self.assertIn("### 10.1.1 FICHA SINTÉTICA", source)
                self.assertIn("Disposiciones generales que deben conservarse.", source)
                self.assertIn("Texto intermedio.", source)
                self.assertIn("Texto final obligatorio de la sección normativa.", source)
                self.assertEqual(source.count("Norma elegida A"), 1)
                self.assertLess(source.index("Norma elegida A"), source.index("# 8 SECCIÓN 8"))
                for excluded in ("Norma ajena B", "Norma opcional C", "NORMATIVA_UNI", "No publicar"):
                    self.assertNotIn(excluded, source)
                state["regulations"] = {"A": False, "C": True}
                source = compose(root, state, refs)
                self.assertNotIn("Norma elegida A", source)
                self.assertIn("Norma opcional C", source)
        (self.docs / "11_Antiguo.md").write_text("# Copia externa obsoleta", encoding="utf-8")
        self.assertEqual(len(load_documents(ZipLibrary(self.archive, self.docs))), 10)

    def test_expanded_library_reports_invalid_regulation_metadata(self):
        """Report malformed annotations in new sections instead of publishing them unfiltered."""
        bases = {f"{index:02}_Base.md": f"# Sección {index}" for index in range(1, 10)}
        bases["07_Base.md"] += '\n<!-- NORMATIVA_UNI {"id":"X","uni":5} -->\n- No publicar'
        self.write_zip(members={**self.members, **bases})
        with self.assertRaisesRegex(ValueError, "07_Base.md"):
            load_regulations(ZipLibrary(self.archive, self.docs), {"01TR001"})

    def test_base_fallback_never_combines_partial_zip_with_external_sections(self):
        """Ignore unnumbered resources and use external bases only when the ZIP has none."""
        for index in range(1, 5):
            (self.docs / f"{index:02}_Base.MD").write_text(f"# Base {index}", encoding="utf-8")
        self.write_zip(members={**self.members, "notas.md": "No publicar."})
        self.assertEqual(len(load_documents(ZipLibrary(self.archive, self.docs))), 4)
        self.write_zip(members={**self.members, "01_Base.md": "# Base del ZIP"})
        with self.assertRaisesRegex(ValueError, "sección 02"):
            load_documents(ZipLibrary(self.archive, self.docs))
        bases = {f"{index:02}_Base.md": f"# Base {index}"
                 for index in range(1, 10) if index != 7}
        self.write_zip(members={**self.members, **bases})
        with self.assertRaisesRegex(ValueError, "sección 07"):
            load_documents(ZipLibrary(self.archive, self.docs))


class DistributedGeneralDocumentsTests(unittest.TestCase):
    def test_nine_sections_and_selected_regulations_reach_pdf_and_odt(self):
        """Exercise the updated real ZIP through composition and both document exporters."""
        from xml.etree import ElementTree as ET
        from pypdf import PdfReader
        from budget_reports import ReportSettings
        from tools.pptp import DOCS_ROOT, LIBRARY_PATH
        from tools.pptp.document import write_document

        library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        documents = load_documents(library)
        self.assertGreaterEqual(len(documents), 9)
        self.assertTrue(documents[6][0].startswith("07"))
        sheet = next(entry for entry in library.entries.values() if entry.code == "01RPN")
        refs = load_regulations(library, {"01RPN00001"})
        self.assertTrue(any(ref.id == "LEY-9-2017" for ref in refs))
        state = {
            "variables": {
                "titulo_proyecto": "Proyecto de prueba", "entidad": "Entidad de prueba",
                "plazo_garantia": "12 meses", "plazo_ejecucion": "2 meses",
                "bullet_capitulos_obra": "- Peldaños de piedra natural",
                "JUSTIFICACION_NO_APLICACION": "no cumplirse los supuestos de prueba",
            },
            "selections": [{
                "item": 0, "code": "01RPN00001", "chapter": "01#", "summary": "Peldaños",
                "path": sheet.path, "selected": True, "automatic": True,
            }],
            "regulations": {"LEY-9-2017": True, "RD-30-1991": False},
        }
        source = compose(library, state, refs)
        headings = re.findall(r"^# (.+)$", source, flags=re.MULTILINE)
        self.assertEqual(len(headings), len(documents))
        self.assertIn("## 9.1 DEMOLICIONES Y TRABAJOS PREVIOS", source)
        self.assertIn("### 9.1.1 " + sheet.title.upper(), source)
        self.assertIn("El Contratista está obligado al cumplimiento", source)
        self.assertNotIn("NORMATIVA_UNI", source)
        self.assertNotIn("Real Decreto 30/1991", source)
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("pdf", "odt"):
                with self.subTest(format=extension):
                    target = Path(directory) / ("PPTP." + extension)
                    write_document(source, target, state["variables"],
                                   ReportSettings(output_format=extension))
                    if extension == "pdf":
                        text = "\n".join(page.extract_text() for page in PdfReader(target).pages)
                    else:
                        with ZipFile(target) as archive:
                            text = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                    text = " ".join(text.split())
                    for heading in headings:
                        self.assertIn(" ".join(heading.split()), text)
                    for expected in ("Proyecto de prueba", "Entidad de prueba", "12 meses", "2 meses",
                                     "9.1.1 " + sheet.title.upper(), "Ley 9/2017"):
                        self.assertIn(expected, text)
                    self.assertNotIn("Real Decreto 30/1991", text)
                    self.assertNotIn("{{", text)


if __name__ == "__main__":
    unittest.main()
