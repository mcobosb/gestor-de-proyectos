"""Contenido del estudio RCD y exclusión de sus propias partidas generadas."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_core import ProjectItem
from budget_reports import ReportSettings
from tools._shared.budget_sources import RCD_BUDGET_SOURCE
from tools.rcd import RcdState, detect_waste_items
from tools.rcd import ler_catalog
from tools.rcd.management import selected_wastes
from tools.rcd.facilities import FACILITY_FIELDS, WasteFacility
from tools.rcd.document import (
    BUDGET_FIELDS, DOCUMENT_FIELDS, LEGACY_PREVENTION_TEXT, LIBRARY_PATH, StudyTable, _pending_lines,
    study_blocks, substitute, write_study,
)


class RcdDocumentTests(unittest.TestCase):
    """Comprueba las tablas y el texto real de los dos formatos de salida."""

    def test_links_in_narrative_and_table_cells(self):
        """Mantiene enlaces normativos en párrafos y celdas PDF y ODT."""
        from odt_reports import _tag
        from tools.rcd.document import _write_odt, _write_pdf

        blocks = [(1, "Normativa"), (0, "Ley 7/2022 y NTP 223."),
                  StudyTable("Referencias", ["Norma"], [["UNE-EN 12350-2:2020"]], [1])]
        with TemporaryDirectory() as folder:
            for kind, writer in (("odt", _write_odt), ("pdf", _write_pdf)):
                target = Path(folder) / f"normativa.{kind}"
                writer(blocks, "Proyecto", ReportSettings(output_format=kind), target)
                if kind == "odt":
                    with ZipFile(target) as archive:
                        tree = ET.fromstring(archive.read("content.xml"))
                    links = {node.get(_tag("xlink:href")) for node in tree.iter(_tag("text:a"))}
                else:
                    links = {
                        str(action.get("/URI"))
                        for page in PdfReader(target).pages for annotation in page.get("/Annots", [])
                        if (action := annotation.get_object().get("/A")) and action.get("/URI")
                    }
                self.assertIn("https://www.boe.es/eli/es/l/2022/04/08/7/con", links)
                self.assertIn("https://www.une.org/encuentra-tu-norma/busca-tu-norma/norma/?c=N0063378", links)
                self.assertTrue(any("ntp-223-" in link for link in links if link))

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
            chapter_titles={"17#": "Actuaciones", "99#": "Gestión de residuos"},
        )
        self.app._chapter_title = lambda chapter: self.app.chapter_titles.get(chapter, chapter)
        self.settings = ReportSettings()
        self.state.selected.update({"13": True, "15": True, "37": True, "40": True})
        generated = f"{self.generated.code}: {self.generated.summary} [LER]"
        self.state.evidence = {
            "13": [f"{self.original.code}: {self.original.summary} [LER]", generated],
            "15": [generated],
            "37": ["DEM_A: Retirada de grava [LER]", "DEM_B: Retirada de grava [texto, revisar]"],
        }

    def test_budget_subsection_uses_actual_chapter_total_and_live_code(self):
        """Cita el total económico real, incluidos indirectos y partidas manuales del capítulo."""
        self.generated.measurement = 3
        self.generated.set_indirect_costs(3, 100.10)
        self.app.budget_items.append(ProjectItem(
            "GEST-MANUAL", "99#", "ud", "Gestión complementaria", unit_price=10.75,
        ))
        before = self.state.to_dict()
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        heading = blocks.index((2, "1.11. PRESUPUESTO DE GESTIÓN DE RCDs"))
        self.assertEqual(blocks[heading + 1:heading + 3], [
            (0, "El presupuesto de gestión de RCDs se encuentra en el capítulo 99 del presupuesto del proyecto."),
            (0, "El presupuesto de gestión de RCDs asciende a "
                "TRESCIENTOS VEINTE EUROS CON CINCO CÉNTIMOS (320,05 €)."),
        ])
        self.assertTrue(blocks[heading + 3][1].startswith("2. PLIEGO"))
        self.assertFalse(any(isinstance(block, tuple) and block[0] == 1
                             and block[1].startswith("3. PRESUPUESTO") for block in blocks))
        self.assertFalse(any(isinstance(block, StudyTable) and block.caption.startswith(
            ("A1. RCDs", "A2. RCDs", "B. Resto", "TOTAL PRESUPUESTO"),
        ) for block in blocks))
        self.assertEqual(self.state.to_dict(), before)
        self.generated.chapter = "CAP.27#"
        self.app.budget_items[-1].chapter = "CAP.27#"
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        self.assertIn((0, "El presupuesto de gestión de RCDs se encuentra en el capítulo 27 "
                         "del presupuesto del proyecto."), blocks)

    def test_external_budget_chapter_is_recognized_without_generated_items(self):
        """Admite un capítulo importado y emplea sus importes, no la estimación del estudio."""
        self.generated.source = "manual"
        self.generated.unit_price = 1234.56
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        self.assertIn((0, "El presupuesto de gestión de RCDs se encuentra en el capítulo 99 "
                         "del presupuesto del proyecto."), blocks)
        self.assertIn((0, "El presupuesto de gestión de RCDs asciende a MIL DOSCIENTOS TREINTA Y "
                         "CUATRO EUROS CON CINCUENTA Y SEIS CÉNTIMOS (1.234,56 €)."), blocks)

    def test_external_chapter_detection_keeps_generated_chapters_protected(self):
        """La detección compartida mantiene la distinción entre capítulos externos y generados."""
        from budget_qt_window import BudgetMainWindow

        self.app.chapter_order = ["17#", "99#"]
        self.assertFalse(BudgetMainWindow._has_external_tool_chapter(self.app, "rcd"))
        self.generated.source = "manual"
        self.assertTrue(BudgetMainWindow._has_external_tool_chapter(self.app, "rcd"))
        self.app.chapter_titles["99#"] = "Seguridad y Salud"
        self.assertTrue(BudgetMainWindow._has_external_tool_chapter(self.app, "safety"))
        self.generated.source = "SS:chapter"
        self.assertFalse(BudgetMainWindow._has_external_tool_chapter(self.app, "safety"))

    def test_external_safety_chapter_requires_exact_title(self):
        """Solo el nombre completo del capítulo activa la protección de Seguridad y Salud."""
        from budget_qt_window import BudgetMainWindow

        self.app.chapter_order = ["17#", "SS#"]
        self.generated.chapter = "SS#"
        self.generated.source = "manual"
        for title, expected in (
            ("SEGURIDAD Y SALUD", True),
            ("  Seguridad   y salud  ", True),
            ("Señalización de seguridad", False),
            ("Salud", False),
            ("Prevención de riesgos laborales", False),
            ("Seguridad y salud complementaria", False),
            ("Instalaciones", False),
        ):
            with self.subTest(title=title):
                self.app.chapter_titles["SS#"] = title
                self.assertEqual(
                    BudgetMainWindow._has_external_tool_chapter(self.app, "safety"),
                    expected,
                )
        self.app.chapter_titles["SS#"] = "SEGURIDAD Y SALUD"
        self.generated.source = "SS:chapter"
        self.assertFalse(BudgetMainWindow._has_external_tool_chapter(self.app, "safety"))
        self.app.budget_items.remove(self.generated)
        self.assertFalse(BudgetMainWindow._has_external_tool_chapter(self.app, "safety"))

    def test_missing_and_ambiguous_chapters_remain_pending(self):
        """No inventa capítulos ni sustituye su importe por el modelo o por cero."""
        for app in (None, SimpleNamespace(
            budget_items=[self.original], _chapter_title=self.app._chapter_title,
            safety_health_state={}, pptp_state={}, library_sources={},
        )):
            with self.subTest(app=app):
                blocks = study_blocks(self.state, "Proyecto", self.settings, app=app)
                for label in BUDGET_FIELDS.values():
                    self.assertIn((0, f"[PENDIENTE: {label}]"), blocks)
        self.app.budget_items.append(ProjectItem(
            "OTRO-RCD", "100#", "ud", "Otra gestión", source=RCD_BUDGET_SOURCE + "earth",
        ))
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        self.assertIn((0, "[PENDIENTE: Hay varios capítulos de gestión de RCDs; "
                         "identificar el que debe citarse]"), blocks)

    def test_legacy_library_budget_is_relocated_without_changing_other_documents(self):
        """Las bibliotecas anteriores no vuelven a introducir la sección 3 ni sus tablas."""
        import shutil
        from tools._library_editor import _signature, read_markdown, write_markdown

        with TemporaryDirectory() as folder:
            library = Path(folder) / "biblioteca.zip"
            shutil.copyfile(LIBRARY_PATH, library)
            write_markdown(library, "08_presupuesto.md",
                           "# 3. PRESUPUESTO DE GESTIÓN DE RCDs\n\n{{tabla_presupuesto}}\n",
                           _signature(library))
            original = read_markdown(library, "01_memoria.md")
            write_markdown(library, "01_memoria.md", original + "\n\nMemoria personalizada.\n",
                           _signature(library))
            management = read_markdown(library, "03_medidas_gestion.md")
            management = management.replace("Medidas concretas de la actuación:",
                                            LEGACY_PREVENTION_TEXT + "Medidas concretas de la actuación:")
            write_markdown(library, "03_medidas_gestion.md", management, _signature(library))
            self.state.management.prevention = ["training"]
            with patch("tools.rcd.document.LIBRARY_PATH", library):
                blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
            self.assertIn((2, "1.11. PRESUPUESTO DE GESTIÓN DE RCDs"), blocks)
            self.assertIn((0, "Memoria personalizada."), blocks)
            self.assertTrue(read_markdown(library, "08_presupuesto.md").startswith("# 3."))
            self.assertIn(LEGACY_PREVENTION_TEXT, read_markdown(library, "03_medidas_gestion.md"))
            self.assertFalse(any(isinstance(block, tuple) and block[1].startswith(
                "Se ajustarán las compras y mediciones",
            ) for block in blocks))

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

    def test_management_roundtrip_and_invalid_decisions(self):
        """Conserva previsiones por proyecto y rechaza datos inválidos o contradictorios."""
        self.state.management.selections["reuse"] = ["earth", "metals"]
        self.state.management.reuse_destinations["earth"] = "Propia obra"
        self.state.management.destinations["17 03 02"] = {
            "destination": "recycling", "facility": "Planta de prueba", "details": "",
        }
        data = self.state.to_dict()
        self.assertEqual(RcdState.from_dict(data).to_dict(), data)
        legacy = deepcopy(data)
        del legacy["management"]
        self.assertEqual(RcdState.from_dict(legacy).management.selections["reuse"], [])
        self.assertIn("purchasing", RcdState.from_dict(legacy).management.prevention)
        self.assertNotIn("selective_demolition", RcdState.from_dict(legacy).management.prevention)
        self.assertEqual(RcdState.from_dict({"management": {"prevention": []}}).management.prevention, [])
        for invalid in (
            {"selections": {"reuse": ["none", "earth"]}},
            {"selections": {"recovery": ["unknown"]}},
            {"destinations": {"17 03 02": {"destination": "unknown"}}},
            {"destinations": {"99 99 99": {"destination": "recycling"}}},
            {"details": {"reuse": False}},
            {"prevention": ["unknown"]},
            {"prevention": "purchasing"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                RcdState.from_dict({**data, "management": invalid})

    def test_management_options_replace_pending_fields_in_both_formats(self):
        """Exporta solo decisiones seleccionadas, destinos por LER y observaciones conservadas."""
        self.state.management.selections.update(
            segregation=["removal", "selective"], reuse=["earth", "metals"], recovery=["r4"],
        )
        self.state.management.reuse_destinations.update(earth="Propia obra", metals="Taller externo")
        self.state.documentation["reuse"] = "Observación particular conservada."
        self.state.management.prevention = [
            "industrialized", "earth_balance", "purchasing", "selective_demolition", "training",
        ]
        self.state.documentation["prevention"] = "Medida preventiva particular conservada."
        self.state.management.facilities = [WasteFacility(
            "Planta seleccionada de prueba", holder="Titular de prueba", address="Camino de prueba 12",
            province_access="Córdoba / Acceso norte", nima="0012345678", authorization="GRU-PRUEBA / R5",
        )]
        rows = selected_wastes(self.state, {self.generated.code})
        self.assertEqual([row[0] for row in rows], ["01 04 08", "17 01 01", "17 03 02"])
        self.assertTrue(all(row[3] == "Nivel II" for row in rows))
        for code, *_ in rows:
            self.state.management.destinations[code] = {
                "destination": "recycling", "facility": "Gestor de prueba", "details": "",
            }
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        destinations = next(block for block in blocks if isinstance(block, StudyTable)
                            and block.caption == "Destino previsto de los residuos por código LER")
        self.assertEqual([row[:4] for row in destinations.rows], rows)
        self.assertTrue(all(row[-1] == "Gestor de prueba" for row in destinations.rows))
        facilities = next(block for block in blocks if isinstance(block, StudyTable)
                          and block.caption == "Centros de gestión de residuos previstos")
        self.assertEqual(facilities.headers, list(FACILITY_FIELDS.values()))
        center = blocks.index(facilities)
        section = blocks.index((2, "1.9. MEDIDAS DE PREVENCIÓN"))
        self.assertLess(blocks.index(destinations), center)
        self.assertLess(center, section)
        with TemporaryDirectory() as folder:
            for extension in ("odt", "pdf"):
                with self.subTest(extension=extension):
                    target = Path(folder) / f"gestion.{extension}"
                    write_study(self.state, "Proyecto", self.settings, target, app=self.app)
                    if extension == "odt":
                        with ZipFile(target) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                            text = " ".join(content.itertext())
                        from odt_reports import _tag
                        table = next(
                            table for table in content.iter(_tag("table:table"))
                            if "Planta seleccionada de prueba" in "".join(table.itertext())
                        )
                        header_rows = table.find(_tag("table:table-header-rows"))
                        header = header_rows.find(_tag("table:table-row")).find(_tag("table:table-cell"))
                        style = next(
                            style for style in content.iter(_tag("style:style"))
                            if style.get(_tag("style:name")) == header.get(_tag("table:style-name"))
                        )
                        properties = style.find(_tag("style:table-cell-properties"))
                        self.assertEqual(properties.get(_tag("fo:background-color")), "#4472C4")
                        caption = next(
                            paragraph for paragraph in content.iter(_tag("text:p"))
                            if "Centros de gestión de residuos previstos" in "".join(paragraph.itertext())
                        )
                        body = next(parent for parent in content.iter() if caption in list(parent))
                        self.assertIs(body[list(body).index(caption) + 1], table)
                    else:
                        text = " ".join(page.extract_text() for page in PdfReader(target).pages)
                    text = " ".join(text.split())
                    for expected in ("Previsión de segregación", "Taller externo", "Gestor de prueba",
                                     "R4.", "Naturaleza pétrea", "Nivel II",
                                     "Observación particular conservada.",
                                     "Sistemas constructivos industrializados y desmontables",
                                     "Compra ajustada y suministro escalonado",
                                     "Demolición selectiva", "Información a operarios y subcontratas",
                                     "Medida preventiva particular conservada.",
                                     "Centros de gestión de residuos previstos",
                                     "Planta seleccionada de prueba", "Titular de prueba", "Camino de prueba 12",
                                     "Córdoba / Acceso norte", "0012345678", "GRU-PRUEBA / R5"):
                        self.assertIn(expected, text)
                    self.assertNotIn("Acopio protegido en obra", text)
                    for field in ("separation", "reuse", "recovery", "destinations", "prevention"):
                        self.assertNotIn(f"[PENDIENTE: {DOCUMENT_FIELDS[field]}]", text)

    def test_facilities_export_even_without_ler_and_mark_only_missing_data(self):
        """Mantiene la tabla de centros y los datos incompletos sin inventar autorizaciones."""
        self.state.selected = {key: False for key in self.state.selected}
        self.state.management.facilities = [WasteFacility("Centro por concretar")]
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        table = next(block for block in blocks if isinstance(block, StudyTable)
                     and block.caption == "Centros de gestión de residuos previstos")
        self.assertEqual(table.rows[0][0], "Centro por concretar")
        self.assertTrue(all(value.startswith("[PENDIENTE:") for value in table.rows[0][1:]))

    def test_prevention_uses_correct_reference_and_only_applicable_regional_law(self):
        """Numera los cuatro bloques y no atribuye normativa andaluza a otras ubicaciones."""
        for codes, andalusia in ((["140386"], True), (["280796"], False)):
            with self.subTest(codes=codes):
                settings = ReportSettings.from_dict({"municipality_codes": codes})
                blocks = study_blocks(self.state, "Proyecto", settings, app=self.app)
                start = blocks.index((2, "1.9. MEDIDAS DE PREVENCIÓN"))
                end = next(index for index in range(start + 1, len(blocks))
                           if isinstance(blocks[index], tuple) and blocks[index][0] == 2)
                prevention = blocks[start:end]
                headings = [text for level, text in prevention if level == 3]
                self.assertEqual(len(headings), 4)
                self.assertTrue(all(text.startswith(f"1.9.{index}.")
                                    for index, text in enumerate(headings, 1)))
                text = " ".join(text for _, text in prevention)
                self.assertIn("artículo 4.1.a), apartado 2.º", text)
                self.assertEqual("Decreto 73/2012" in text, andalusia)
                self.assertNotIn("Demolición selectiva:", text)

    def test_none_does_not_assign_landfill_and_partial_decisions_stay_pending(self):
        """No inventa reutilizaciones, gestores ni justificaciones de recogida mezclada."""
        self.state.management.selections.update(
            segregation=["mixed"], reuse=["none"], recovery=["other"],
        )
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        tables = {block.caption: block for block in blocks if isinstance(block, StudyTable)}
        reuse = tables["Medidas de reutilización previstas"]
        self.assertEqual(reuse.rows[0][1], "No procede")
        self.assertNotIn("vertedero", str(reuse.rows).lower())
        self.assertIn("[PENDIENTE:", tables["Previsión de segregación"].rows[0][1])
        self.assertIn("[PENDIENTE:", tables["Operaciones de valorización previstas en la obra"].rows[0][0])
        destinations = tables["Destino previsto de los residuos por código LER"]
        self.assertTrue(all("[PENDIENTE:" in row[-2] for row in destinations.rows))

    def test_pending_fields_replace_their_line_prefix_but_keep_completed_text(self):
        """Distingue los campos sin completar de la documentación ya cumplimentada."""
        self.state.documentation["recovery"] = "Se entregará a un gestor autorizado."
        self.state.management.prevention = []
        blocks = study_blocks(self.state, "Proyecto", self.settings, app=self.app)
        texts = [block[1] for block in blocks if isinstance(block, tuple)]
        for key in ("prevention", "separation", "reuse", "facilities", "regional"):
            self.assertIn(f"[PENDIENTE: {DOCUMENT_FIELDS[key]}]", texts)
        destinations = next(block for block in blocks if isinstance(block, StudyTable)
                            and block.caption == "Destino previsto de los residuos por código LER")
        self.assertTrue(all("[PENDIENTE: Destino del residuo" in row[-2] for row in destinations.rows))
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
