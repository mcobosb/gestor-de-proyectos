"""Comprueba la reorganización PPTP hasta la tercera letra de la clasificación BCCA."""
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import unittest
from urllib.parse import unquote
from zipfile import ZipFile

from bc3_gui import Bc3Dataset, ProjectItem
from tools._library_editor import _signature
from tools.pptp import DOCS_ROOT, LIBRARY_PATH
from tools.pptp.bcca_library import reorganize_library
from tools.pptp.document import load_catalog, propose
from tools.pptp.library import ZipLibrary


class DistributedBccaLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Read the distributed catalog once without extracting the archive."""
        cls.library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        cls.catalog = load_catalog(cls.library)

    def test_all_original_sheets_keep_aliases_and_readable_classified_names(self):
        """Retain all 684 original sheets and enforce descriptive BCCA file names."""
        self.assertEqual(len(self.library.aliases), 684)
        for entry in self.library.entries.values():
            path = PurePosixPath(entry.path)
            with self.subTest(path=entry.path):
                self.assertTrue(path.name.startswith(entry.code + "-"))
                self.assertGreater(len(path.stem), len(entry.code) + 2)
                self.assertNotRegex(path.name, r'[<>:"\\|?*\x00-\x1f]')
                self.assertEqual(len(path.parent.name.split("_", 1)[0]), 4 if len(entry.code) == 5 else len(entry.code))
                for alias in entry.aliases:
                    self.assertEqual(self.library.resolve_sheet(alias), entry.path)
                    self.assertEqual(self.library.read_sheet(alias), self.library.read_sheet(entry.path))

    def test_every_specific_sheet_has_three_letters_and_consistent_hash_and_common_links(self):
        """Cover every source BCCA group and keep the last letter out of folder names."""
        with ZipFile(LIBRARY_PATH) as archive:
            catalog = json.loads(archive.read("UNI/catalogo.json"))
            self.assertEqual(archive.testzip(), None)
            self.assertEqual(catalog["total_fichas"], len(self.catalog))
            seen = set()
            for row in catalog["fichas"]:
                path = "UNI/" + row["archivo"]
                self.assertEqual(row["sha256"], sha256(archive.read(path)).hexdigest())
                if len(row["codigo_uni"]) == 5:
                    seen.add(row["codigo_uni"])
                    text = archive.read(path).decode("utf-8-sig")
                    for link in re.findall(r"\]\(([^)]+)\)", text):
                        destination = (PurePosixPath(path).parent / unquote(link)).as_posix()
                        self.assertIn(destination, archive.namelist())
            self.assertEqual(len(seen), 1621)
            for name in archive.namelist():
                if name.endswith("/"):
                    self.assertNotRegex(PurePosixPath(name).name, r"^\d{2}[A-Z]{3}_")
        source = LIBRARY_PATH.parent.parent / "BCCA2023_V02.bc3"
        if source.is_file():
            dataset = Bc3Dataset(source)
            expected = {
                code.rstrip("#") for code in dataset.concepts
                if re.fullmatch(r"\d{2}[A-Z]{3}#", code)
            }
            self.assertEqual(seen, expected)

    def test_stair_treads_select_only_the_matching_material_and_shared_sheet(self):
        """Never select stone, wood and ceramics together for a material-specific item."""
        for suffix in ("A", "C", "L", "M", "N", "W"):
            code = "01RP" + suffix
            auto, manual = propose([
                ProjectItem(code + "00001", "01#", "m", "Peldaños"),
            ], {}, self.catalog, {})
            self.assertFalse(manual)
            chosen_codes = {self.library.entries[row.path].code for row in auto}
            self.assertEqual(chosen_codes, {"01RP", code})
            self.assertEqual(len({PurePosixPath(row.path).parent for row in auto}), 1)
        natural = next(sheet for sheet in self.catalog if sheet.code == "01RPN")
        content = self.library.read_sheet(natural.path)
        self.assertIn("piedra natural", content.lower())
        self.assertIn("01RPN00001", content)
        self.assertNotIn("01RPC00001", content)

    def test_empty_bcca_groups_explicitly_require_completing_prescriptions(self):
        """Do not invent unit descriptions where the source only defines a classification."""
        sheet = next(sheet for sheet in self.catalog if sheet.code == "18RPB")
        self.assertIn("no contiene partidas", self.library.read_sheet(sheet.path))
        self.assertIn("completarse sus prescripciones", self.library.read_sheet(sheet.path))


class BccaReorganizationTests(unittest.TestCase):
    def test_reorganization_is_idempotent_and_preserves_original_scope(self):
        """Use a minimal source to prove repeatability, specific scope and historical paths."""
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            archive = folder / "library.zip"
            source = folder / "source.bc3"
            old = "01_BASE/01R_REVESTIMIENTOS/01RP_PELDANOS/GENERICA.md"
            original = "# Peldaños\n\nCondiciones comunes originales.\n"
            with ZipFile(archive, "w") as target:
                target.writestr("UNI/catalogo.json", json.dumps({
                    "formato": "biblioteca-fichas-uni", "version_esquema": 1,
                    "total_fichas": 1, "indice": "INDICE_FICHAS.md",
                    "fichas": [{
                        "id": "keep-id", "codigo_uni": "01RP", "titulo": "Peldaños",
                        "archivo": old, "tipo": "marco",
                    }],
                }))
                target.writestr("UNI/INDICE_FICHAS.md", "# Índice\n")
                target.writestr("UNI/" + old, original)
            source.write_text(
                "~V|TEST|FIEBDC-3/2016|\n"
                "~C|01RP#||Peldaños|0||0|\n"
                "~C|01RPN#||Piedras naturales|0||0|\n"
                "~C|01RPC#||Cerámicos|0||0|\n"
                "~C|01RPN00001|m|Levantado de piedra natural|12||0|\n"
                "~T|01RPN00001|Retirada de piedra natural. Medida la longitud.|\n"
                "~D|01RP#|01RPN#\\1\\1\\01RPC#\\1\\1\\|\n"
                "~D|01RPN#|01RPN00001\\1\\1\\|\n",
                encoding="utf-8",
            )
            self.assertEqual(reorganize_library(archive, source), (1, 2))
            library = ZipLibrary(archive, folder / "docs")
            self.assertEqual(library.read_sheet("UNI/" + old).split("\n", 1)[1], original.split("\n", 1)[1])
            natural = next(entry for entry in library.entries.values() if entry.code == "01RPN")
            self.assertIn("Retirada de piedra natural", library.read_sheet(natural.path))
            content = archive.read_bytes()
            signature = _signature(archive)
            self.assertEqual(reorganize_library(archive, source), (0, 0))
            self.assertEqual(archive.read_bytes(), content)
            self.assertEqual(_signature(archive), signature)


if __name__ == "__main__":
    unittest.main()
