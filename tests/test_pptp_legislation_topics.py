"""Segmentación temática de referencias seleccionadas en ambos formatos de PPTP."""
from dataclasses import asdict
from pathlib import Path
import re
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_reports import ReportSettings
from odt_reports import _tag
from tools.pptp import DOCS_ROOT, LIBRARY_PATH
from tools.pptp.document import (
    REGULATION_PATTERN, VARIABLES, Selection, compose, load_documents, load_regulations, write_document,
)
from tools.pptp.library import ZipLibrary


class LegislationTopicsTests(unittest.TestCase):
    def test_selected_topics_have_visible_headings_in_pdf_and_odt_without_empty_groups(self):
        """Conserva normas marcadas y omite temáticas vacías sin alterar textos adyacentes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(1, 10):
                (root / f"{index:02}_Base.md").write_text(f"# Seccion {index}", encoding="utf-8")
            annotated = (
                '<!-- NORMATIVA_UNI {"id":"%s","uni":["*"],'
                '"seleccion":"candidata","condicion":"Revisar"} -->\n* Norma %s'
            )
            legal = "# Legislacion\n\nTexto anterior.\n"
            for topic, ids in (("Contratacion", ("A", "B")), ("Electricidad", ("C",)), ("Residuos", ("D",))):
                legal += (
                    f"\n<!-- {topic} -->\n## {topic}\n\n"
                    + "\n".join(annotated % (identifier, identifier) for identifier in ids)
                    + f"\n<!-- FIN_{topic} -->\n"
                )
            legal += "\nTexto posterior obligatorio.\n"
            (root / "07_Base.md").write_text(legal, encoding="utf-8")
            path = "UNI/01_BASE/Ficha.md"
            sheet = root / "UNI" / "01_BASE" / "Ficha.md"
            sheet.parent.mkdir(parents=True)
            sheet.write_text("# Ficha\n\nPrescripciones.", encoding="utf-8")
            state = {
                "variables": {key: "Valor" for key in VARIABLES},
                "selections": [asdict(Selection(0, "P1", "01#", "Partida", path))],
                "regulations": {"A": True, "B": False, "C": False, "D": True},
            }
            refs = load_regulations(root, {"01"})
            source = compose(root, state, refs, budget_chapter_titles={"01#": "Capitulo"})
            self.assertIn("## 7.1 CONTRATACION", source)
            self.assertIn("## 7.2 RESIDUOS", source)
            self.assertNotIn("ELECTRICIDAD", source)
            self.assertNotIn("Norma B", source)
            self.assertNotIn("Norma C", source)
            for extension in ("pdf", "odt"):
                with self.subTest(format=extension):
                    target = root / ("PPTP." + extension)
                    write_document(source, target, state["variables"], ReportSettings(output_format=extension))
                    if extension == "pdf":
                        with PdfReader(target) as reader:
                            text = "\n".join(page.extract_text() for page in reader.pages)
                    else:
                        with ZipFile(target) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                        text = "".join(content.itertext())
                        headings = [
                            "".join(heading.itertext()) for heading in content.iter(_tag("text:h"))
                        ]
                        self.assertIn("7.1 CONTRATACION", headings)
                        self.assertIn("7.2 RESIDUOS", headings)
                    for expected in ("7.1 CONTRATACION", "7.2 RESIDUOS", "Norma A", "Norma D",
                                     "Texto anterior.", "Texto posterior obligatorio."):
                        self.assertIn(expected, text)
                    self.assertNotIn("ELECTRICIDAD", text)
                    self.assertNotIn("FIN_", text)

    def test_distributed_legislation_topics_cover_existing_annotated_references(self):
        """Cada bloque de normativa del ZIP tiene título editable y mantiene su selección."""
        library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        legal = next(source for name, source in load_documents(library) if name.startswith("07"))
        groups = re.findall(r"<!--\s*(\w+)\s*-->(.*?)<!--\s*FIN_\1\s*-->", legal, flags=re.DOTALL)
        existing_topics = {
            "Normativa_Base_Contratacion", "Accesibilidad_Urbanismo", "Urbanismo_Medio_Ambiente",
            "Edificacion", "Estructuras_Hormigon_Cementos", "Firmes_Pavimentos",
            "Saneamiento_Abastecimiento", "Redes_Electricas_Alumbrado",
            "Carreteras_Movilidad_Transporte", "Telecomunicaciones",
            "Proteccion_Contra_Incendios", "Jardineria_Fertilizantes",
            "Gestion_Residuos_Medio_Ambiente", "Seguridad_Salud_Laboral", "Control_Calidad_NTE",
        }
        self.assertEqual(sum(topic in existing_topics for topic, _ in groups), 15)
        self.assertFalse(any(topic.startswith("UNE_BCCA_") for topic, _ in groups))
        self.assertNotIn("Normas UNE relacionadas con", legal)
        for topic, body in groups:
            with self.subTest(topic=topic):
                self.assertRegex(body, r"(?m)^## .+")
                self.assertTrue(REGULATION_PATTERN.search(body))
        refs = load_regulations(library, {"01RPN", "15PAA"})
        first = next(iter(library.entries.values()))
        state = {
            "variables": {key: "Valor" for key in VARIABLES},
            "selections": [asdict(Selection(0, "P", "01#", "Partida", first.path))],
            "regulations": {ref.id: False for ref in refs},
        }
        state["variables"]["JUSTIFICACION_NO_APLICACION"] = "Motivo contractual de prueba"
        source = compose(library, state, refs)
        self.assertNotRegex(source, r"(?m)^## 7\.")
        state["regulations"]["LEY-9-2017"] = True
        source = compose(library, state, refs)
        self.assertIn("## 7.1 CONTRATACIÓN PÚBLICA Y DISPOSICIONES GENERALES", source)
        self.assertNotIn("NORMATIVA_UNI", source)
