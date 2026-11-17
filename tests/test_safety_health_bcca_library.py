"""Pruebas de importación selectiva de recursos BCCA a Seguridad y Salud."""
import json
from pathlib import Path, PurePosixPath
import tempfile
import unittest
from zipfile import ZipFile

from tools.safety_health.bcca_resources import add_bcca_resources
from tools.safety_health.study_report import (
    _load_selected_fiches, _markdown_blocks, _parse_catalogues, _parse_index,
)


SYNTHETIC_BC3 = (
    "~V|SYNTHETIC|FIEBDC-3/2016|Pruebas||\n"
    "~C|-BAS#||Precios básicos|0||0|\n"
    "~D|-BAS#|M#\\1\\1\\A#\\1\\1\\T#\\1\\1|\n"
    "~C|M#||MAQUINARIA Y EQUIPOS|0||0|\n"
    "~D|M#|MW#\\1\\1|\n"
    "~C|MW#||MAQUINARIA AUXILIAR|0||0|\n"
    "~D|MW#|MAQ1\\1\\1|\n"
    "~C|MAQ1|h|PLATAFORMA ELEVADORA|12.5||2|\n"
    "~T|MAQ1|Equipo elevador de prueba.|\n"
    "~C|A#||ÁRIDOS|0||0|\n"
    "~D|A#|AR#\\1\\1|\n"
    "~C|AR#||ÁRIDOS NATURALES|0||0|\n"
    "~D|AR#|MAT1\\1\\1|\n"
    "~C|MAT1|m3|ARENA LAVADA|8.75||3|\n"
    "~C|T#||TRABAJO MANO DE OBRA|0||0|\n"
    "~D|T#|TR1\\1\\1|\n"
    "~C|TR1|h|PEÓN DE PRUEBA|20||1|\n"
)


class SafetyHealthBccaLibraryTests(unittest.TestCase):
    """Comprueba que se importa el árbol de materiales y maquinaria sin personal."""

    def test_bcca_resources_are_present_without_labor_categories(self):
        """Comprueba las fichas importadas y que la categoría de mano de obra se excluye."""
        archive_path = Path(__file__).resolve().parents[1] / "tools" / "safety_health" / "BIBLIOTECA.zip"
        with ZipFile(archive_path) as archive:
            names = archive.namelist()
        materials = [name for name in names if name.startswith("RECURSOS/MATERIALES/")]
        machinery = [name for name in names if name.startswith("RECURSOS/MAQUINARIA/")]
        self.assertEqual(len(materials), 5110)
        self.assertEqual(len(machinery), 57)
        self.assertFalse(any(
            name.startswith("RECURSOS/MATERIALES/T_TRABAJO_MANO_DE_OBRA/")
            for name in names
        ))

    def test_eleven_ess_fiches_use_pptp_folders_and_complete_text_sections(self):
        """Conserva once fichas autonomas, con codigos BCCA y apartados uniformes."""
        root = Path(__file__).resolve().parents[1]
        with ZipFile(root / "tools" / "pptp" / "BIBLIOTECA.zip") as archive:
            catalog = json.loads(archive.read("UNI/catalogo.json"))
        folders = {
            PurePosixPath("UNI", *PurePosixPath(row["archivo"]).parts[:level]).as_posix()
            for row in catalog["fichas"]
            for level in range(1, len(PurePosixPath(row["archivo"]).parts))
        }
        with ZipFile(root / "tools" / "safety_health" / "BIBLIOTECA.zip") as archive:
            index = _parse_index(archive.read("UNI/INDICE_FICHAS_ESS.md").decode("utf-8-sig"))
            paths = list(dict.fromkeys(path for _, path in index))
            self.assertEqual(len(paths), 11)
            references, fiches = _load_selected_fiches(archive, paths, [])
            catalogues = _parse_catalogues(archive.read("04_catalogos_preventivos.md").decode("utf-8-sig"))
            for path, text in fiches:
                self.assertIn(PurePosixPath(path).parent.as_posix(), folders)
                self.assertNotIn("FICHA_ESS_", path)
                self.assertNotIn("UO-", text)
                blocks = _markdown_blocks(text)
                self.assertFalse(any(block.kind == "table" for block in blocks))
                for category in ("R", "MP", "PC", "EPI"):
                    self.assertTrue(references[path][category], (path, category))
                self.assertTrue(all(
                    reference in catalogues for group in references[path].values() for reference in group
                ))
            for name in archive.namelist():
                if name.endswith(".md") and not name.startswith("RECURSOS/"):
                    text = archive.read(name).decode("utf-8-sig")
                    self.assertNotIn("UO-", text, name)
                    for removed in ("03_fichas_unidades_obra.md", "08_matriz_riesgos_por_unidad.md",
                                    "09_enlace_automatico_presupuesto.md"):
                        self.assertNotIn(removed, text, name)
            by_code = {PurePosixPath(path).stem.split("-", 1)[0]: text for path, text in fiches}
            self.assertIn("muestras de escollera", by_code["18CEE"])
            self.assertNotIn("EQ-EXTRACCION-TESTIGOS", by_code["18CEE"])
            self.assertIn("pruebas de carga", by_code["18HHI"])
            self.assertEqual(dict(index)["17RRBN"], dict(index)["17RB"])

    def test_imports_materials_and_machinery_and_preserves_base_library(self):
        """Crea fichas clasificadas para recursos 2/3 y excluye recursos 1."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "base.bc3"
            source.write_bytes(SYNTHETIC_BC3.encode("cp1252"))
            archive_path = root / "BIBLIOTECA.zip"
            with ZipFile(archive_path, "w") as archive:
                archive.writestr("00_guia.md", "# Guía")
                archive.writestr("UNI/15_BASE/FICHA_ESS.md", "# Ficha ESS")

            self.assertEqual(add_bcca_resources(archive_path, source), (1, 1))
            with ZipFile(archive_path) as archive:
                names = archive.namelist()
                material = next(name for name in names if name.endswith("/MAT1.md"))
                machinery = next(name for name in names if name.endswith("/MAQ1.md"))
                self.assertIn("RECURSOS/MATERIALES/A_ARIDOS/AR_ARIDOS_NATURALES", material)
                self.assertIn("RECURSOS/MAQUINARIA/M_MAQUINARIA_Y_EQUIPOS/MW_MAQUINARIA_AUXILIAR",
                              machinery)
                self.assertIn("Código BC3: `MAT1`", archive.read(material).decode())
                self.assertIn("Precio unitario BCCA 2023: 8.75 €", archive.read(material).decode())
                self.assertIn("Equipo elevador de prueba.", archive.read(machinery).decode())
                self.assertNotIn("TR1", "\n".join(names))
                self.assertEqual(archive.read("00_guia.md"), "# Guía".encode("utf-8"))
                self.assertEqual(
                    archive.read("UNI/15_BASE/FICHA_ESS.md"), b"# Ficha ESS",
                )

            self.assertEqual(add_bcca_resources(archive_path, source), (1, 1))
            with ZipFile(archive_path) as archive:
                self.assertEqual(sum(name.endswith("/MAT1.md") for name in archive.namelist()), 1)
                self.assertEqual(sum(name.endswith("/MAQ1.md") for name in archive.namelist()), 1)


if __name__ == "__main__":
    unittest.main()
