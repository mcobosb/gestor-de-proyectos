"""Contenido del estudio RCD y exclusión de sus propias partidas generadas."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_core import ProjectItem
from budget_reports import ReportSettings
from tools._shared.budget_sources import RCD_BUDGET_SOURCE
from tools.rcd import RcdState, detect_waste_items
from tools.rcd import ler_catalog
from tools.rcd.document import (
    DOCUMENT_FIELDS, StudyTable, _pending_lines, study_blocks, substitute, write_study,
)


class RcdDocumentTests(unittest.TestCase):
    """Comprueba las tablas y el texto real de los dos formatos de salida."""

    def setUp(self):
        """Prepara residuos revisados y una partida de gestión ya incorporada."""
        self.state = RcdState()
        self.state.inputs.update(surface=100, earth_volume=10, budget=10000, earth_budget=1000)
        self.original = ProjectItem("01UPP00030", "17#", "m²", "Retirada de pavimento existente")
        self.generated = ProjectItem(
            "01QIF00001N", "99#", "m³", "Residuos de madera 17 02 01",
            source=RCD_BUDGET_SOURCE + "nonstone",
        )
        self.app = SimpleNamespace(
            budget_items=[self.original, self.generated], safety_health_state={},
            pptp_state={}, library_sources={},
        )
        self.settings = ReportSettings()
        self.state.selected.update({"13": True, "15": True, "37": True, "40": True})
        generated = f"{self.generated.code}: {self.generated.summary} [LER]"
        self.state.evidence = {
            "13": [f"{self.original.code}: {self.original.summary} [LER]", generated],
            "15": [generated],
            "37": ["DEM_A: Retirada de grava [LER]", "DEM_B: Retirada de grava [texto, revisar]"],
        }

    def test_wastes_are_sorted_with_provenance_grouped_and_outputs_excluded(self):
        """Ordena LER sin separar procedencias ni borrar selecciones manuales."""
        before = self.state.to_dict()
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        table = next(
            block for block in blocks
            if isinstance(block, StudyTable) and block.caption == "Residuos seleccionados"
        )
        self.assertEqual(table.headers, ["Código LER", "Residuo", "Procedencia"])
        self.assertEqual([row[0] for row in table.rows], ["01 04 08", "", "17 01 01", "17 03 02"])
        self.assertEqual(table.rows[0][2], "DEM_A: Retirada de grava [LER]")
        self.assertEqual(table.rows[1][2], "DEM_B: Retirada de grava")
        self.assertEqual(table.rows[2][2], "Selección manual")
        self.assertFalse(any(self.generated.code in row[2] for row in table.rows))
        self.assertEqual(self.state.to_dict(), before)

    def test_pending_fields_replace_their_line_prefix_but_keep_completed_text(self):
        """Distingue los campos sin completar de la documentación ya cumplimentada."""
        self.state.documentation["recovery"] = "Se entregará a un gestor autorizado."
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        texts = [block[1] for block in blocks if isinstance(block, tuple)]
        for key in ("prevention", "separation", "reuse", "destinations", "facilities", "regional"):
            self.assertIn(f"[PENDIENTE: {DOCUMENT_FIELDS[key]}]", texts)
        self.assertIn("Previsión particular: Se entregará a un gestor autorizado.", texts)
        self.assertFalse(any("PENDIENTE DE CONCRETAR:" in text for text in texts))

    def test_pending_prefix_removal_preserves_other_physical_lines_and_variable_errors(self):
        """No elimina el contexto anterior que el lector Markdown fusionaría después."""
        values = {
            "reuse": "[PENDIENTE: Reutilización prevista y destino]",
            "recovery": "Entrega a gestor.",
        }
        source = "Contexto anterior.\nDestino previsto: {{reuse}}\n\nPrevisión: {{recovery}}"
        self.assertEqual(
            substitute(_pending_lines(source, values), values),
            "Contexto anterior.\n[PENDIENTE: Reutilización prevista y destino]\n\nPrevisión: Entrega a gestor.",
        )
        with self.assertRaises(ValueError):
            substitute(_pending_lines("Otra variable: {{desconocida}}", values), values)

    def test_detection_and_catalogue_links_ignore_study_outputs_not_ler_or_chapter_17(self):
        """Usa la marca de origen, conservando partidas y códigos LER 17 legítimos."""
        state = RcdState()
        detect_waste_items(state, self.app.budget_items)
        self.assertEqual([key for key, selected in state.selected.items() if selected], ["13"])
        self.assertEqual(len(state.evidence["13"]), 1)
        self.assertIn(self.original.summary, state.evidence["13"][0])
        rows = ler_catalog.match_rows(self.app.budget_items)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "17 03 02")
        self.assertIn(self.original.summary, rows[0][0])

    def test_revised_captions_and_text_are_exported_in_pdf_and_odt(self):
        """Retira las frases solicitadas y mantiene las tablas y el método de cálculo."""
        with TemporaryDirectory() as folder:
            for extension in ("odt", "pdf"):
                with self.subTest(format=extension):
                    target = Path(folder) / f"rcd.{extension}"
                    write_study(self.state, "Proyecto", self.settings, target, app=self.app)
                    if extension == "odt":
                        with ZipFile(target) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                        text = " ".join(content.itertext())
                    else:
                        text = " ".join(page.extract_text() for page in PdfReader(target).pages)
                    text = " ".join(text.split())
                    for expected in (
                        "Tabla 2. Residuos seleccionados", "Código LER",
                        "Tabla 3. Vínculos orientativos LER–UO",
                        "utilizados figuran en la tabla de datos de cálculo.",
                    ):
                        self.assertTrue(expected in text, f"Falta en {extension}: {expected}")
                    for removed in (
                        "(revisión manual)", "LER (modelo ODS)", "(BCCA2023)",
                        "Vínculos orientativos por código exacto", "no asignan masas ni volúmenes",
                        "no se sustituyen por los del estudio tipo",
                        "Estimación orientativa, sin fianza ni IVA",
                        self.generated.summary,
                    ):
                        self.assertFalse(removed in text, f"Sobra en {extension}: {removed}")
