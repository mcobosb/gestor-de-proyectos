"""Importación de UNE vigentes al apartado normativo sin selección automática."""
import csv
import json
from pathlib import Path
import re
import tempfile
import unittest
from zipfile import ZipFile

from tools.pptp import DOCS_ROOT, LIBRARY_PATH
from tools.pptp.document import REGULATION_PATTERN, load_regulations
from tools.pptp.library import ZipLibrary
from tools.pptp.une_library import classified_markdown, import_une, read_une_csv


class UneLibraryTests(unittest.TestCase):
    def write_csv(self, root, rows):
        """Escribe un catálogo mínimo con las mismas columnas de clasificación."""
        path = root / "une.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("codigo_bcca", "capitulo", "ruta_bcca", "codigo_UNE", "titulo_UNE", "url_norma"))
            writer.writerows(rows)
        return path

    def test_unique_references_merge_existing_ids_and_are_idempotent(self):
        """Agrupa asociaciones, respeta decisiones persistidas y no crea bloques anidados."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            url = "https://www.une.org/encuentra-tu-norma/busca-tu-norma/norma/?c=N1"
            rows = [
                ("08OU1", "08", "INSTALACIONES > Alumbrado", "UNE-EN 1:2024", "Ensayo.", url),
                ("08OU2", "08", "INSTALACIONES > Alumbrado", "UNE-EN 1:2024", "Ensayo.", url),
                ("01ABC", "01", "DEMOLICIONES > Retirada", "ESPECIFICACION UNE 0073:2021", "Canales.", url),
            ]
            refs = read_une_csv(self.write_csv(root, rows))
            source = (
                "# Normativa\n"
                '<!-- NORMATIVA_UNI {"id":"EXISTENTE","uni":["08O"],'
                '"seleccion":"revision","condicion":"Verificar uso."} -->\n'
                f"* UNE-EN 1:2024. Ensayo previo. [UNE]({url}).\n"
            )
            updated, count = classified_markdown(source, refs)
            self.assertEqual(count, 2)
            self.assertEqual(classified_markdown(updated, refs)[0], updated)
            matches = list(REGULATION_PATTERN.finditer(updated))
            self.assertEqual(len(matches), 2)
            existing = json.loads(matches[0][1])
            self.assertEqual(existing["id"], "EXISTENTE")
            self.assertEqual(existing["uni"], ["08O", "08OU1", "08OU2"])
            self.assertIn("Ensayo previo.", updated)
            self.assertTrue(all(json.loads(match[1])["seleccion"] == "revision" for match in matches))
            for index in range(1, 8):
                (root / f"{index:02}_Base.md").write_text(
                    updated if index == 7 else "# Base", encoding="utf-8"
                )
            relevant = load_regulations(root, {"01ABC"})
            self.assertEqual([ref.id for ref in relevant], ["ESPECIFICACION-UNE-0073-2021"])
            self.assertTrue(relevant[0].selected)
            groups = re.findall(
                r"<!--\s*(\w+)\s*-->(.*?)<!--\s*FIN_\1\s*-->", updated,
                flags=re.DOTALL,
            )
            self.assertEqual([topic for topic, _ in groups], ["Urbanismo_Medio_Ambiente"])
            self.assertIn("## Urbanismo, ordenación del territorio y medio ambiente", updated)
            self.assertNotIn("Normas UNE relacionadas con", updated)

    def test_une_is_added_to_existing_general_legal_topic(self):
        """Las UNE se integran en el ámbito legal común, sin crear un grupo exclusivo."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = (
                "08OU1", "08", "INSTALACIONES > ELÉCTRICAS",
                "UNE-EN 1:2024", "Ensayo eléctrico.", "https://www.une.org/norma",
            )
            references = read_une_csv(self.write_csv(root, [row]))
            source = (
                "<!-- Redes_Electricas_Alumbrado -->\n"
                "## Redes eléctricas y alumbrado\n\n"
                '<!-- NORMATIVA_UNI {"id":"LEY","uni":["*"],'
                '"seleccion":"candidata","condicion":"Aplicable."} -->\n'
                "* Disposición legal de electricidad.\n"
                "<!-- FIN_Redes_Electricas_Alumbrado -->\n"
            )

            updated, _ = classified_markdown(source, references)

            self.assertEqual(
                re.findall(
                    r"<!--\s*(\w+)\s*-->(.*?)<!--\s*FIN_\1\s*-->",
                    updated, flags=re.DOTALL,
                )[0][0],
                "Redes_Electricas_Alumbrado",
            )
            self.assertIn("Disposición legal de electricidad.", updated)
            self.assertIn("UNE-EN 1:2024. Ensayo eléctrico.", updated)
            self.assertNotIn("Normas UNE relacionadas con", updated)

    def test_invalid_and_contradictory_csv_are_rejected(self):
        """Los errores del catálogo se notifican antes de modificar la biblioteca."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = ["08OU1", "08", "INSTALACIONES", "UNE-EN 1:2024", "Ensayo.", "https://www.une.org/norma"]
            for column, value in ((0, "INVALIDO"), (1, "01"), (3, "OTRA 1"), (5, "http://www.une.org/norma")):
                invalid = list(row)
                invalid[column] = value
                with self.subTest(column=column), self.assertRaisesRegex(ValueError, "Fila UNE 2"):
                    read_une_csv(self.write_csv(root, [invalid]))
            changed = list(row)
            changed[4] = "Título distinto."
            with self.assertRaisesRegex(ValueError, "contradictorios"):
                read_une_csv(self.write_csv(root, [row, changed]))

    def test_import_requires_confirmed_current_catalog(self):
        """Un catálogo sin confirmación de vigencia no puede escribirse en el ZIP."""
        with self.assertRaisesRegex(ValueError, "únicamente UNE vigentes"):
            import_une(Path("missing.zip"), Path("missing.csv"), current_confirmed=False)

    def test_distributed_section_contains_every_csv_standard_with_required_metadata(self):
        """Todas las UNE del listado están disponibles por BCCA y pendientes de selección."""
        references = read_une_csv(DOCS_ROOT / "UNE_tematica_por_codigo_BCCA.csv")
        with ZipFile(LIBRARY_PATH) as archive:
            source = archive.read("07_DisposicionesTecnicasLegalesAplicables.md").decode("utf-8-sig")
        matches = list(REGULATION_PATTERN.finditer(source))
        data = {json.loads(match[1])["id"]: (json.loads(match[1]), match[2]) for match in matches}
        self.assertEqual(len(data), len(matches))
        for identifier, reference in references.items():
            with self.subTest(standard=identifier):
                self.assertIn(identifier, data)
                metadata, text = data[identifier]
                self.assertTrue(reference["codes"] <= set(metadata["uni"]))
                self.assertEqual(metadata["seleccion"], "revision")
                self.assertTrue(metadata["condicion"])
                self.assertIn(reference["designation"], text)
                self.assertIn("[UNE](", text)
        library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        candidates = load_regulations(library, {"08OU1"})
        self.assertTrue(any(ref.id in references for ref in candidates))
        from tools.pptp.document import latest_une_ids
        self.assertEqual(
            {ref.id for ref in candidates if ref.id in references and ref.selected},
            latest_une_ids([ref for ref in candidates if ref.id in references]),
        )

    def test_preselection_uses_publication_dates_and_ignores_parts(self):
        """Una familia conserva solo la publicación más reciente, incluidas variantes de versión."""
        from tools.pptp.document import Regulation, latest_une_ids
        refs = [
            Regulation("A", ["01"], "revision", "Revisar", "* UNE-EN 1234-1:2024. Titulo.",
                       une_code="UNE-EN 1234-1:2024", publication_date="2024-01-01"),
            Regulation("B", ["01"], "revision", "Revisar", "* UNE-EN 1234-2:2024. Titulo.",
                       une_code="UNE-EN 1234-2:2024", publication_date="2024-10-01"),
            Regulation("C", ["01"], "revision", "Revisar", "* UNE-EN 9999 V1.2.1. Titulo.",
                       une_code="UNE-EN 9999 V1.2.1", publication_date="2020-01-01"),
            Regulation("D", ["01"], "revision", "Revisar", "* UNE-EN 9999 V1.3.1. Titulo.",
                       une_code="UNE-EN 9999 V1.3.1", publication_date="2021-01-01"),
            Regulation("LEY", ["*"], "candidata", "Revisar", "* Ley de prueba.", True),
        ]
        self.assertEqual(latest_une_ids(refs), {"B", "D"})


if __name__ == "__main__":
    unittest.main()
