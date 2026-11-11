"""Pruebas del asistente, clasificacion y documentos PPTP con datos ficticios."""
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from bc3_gui import BudgetApp, ProjectItem
from budget_reports import ReportSettings
from tools.pptp.document import (
    PENDING_DURATION, VARIABLES, PRICE_VARIABLES, Selection, compose, initial_variables, load_catalog,
    load_regulations, planning_duration, propose, restore_state, safe_path,
    substitute, text_blocks, write_document,
)
from tools.pptp import DOCS_ROOT, LIBRARY_PATH, PptpWindow, open_tool
from tools.pptp.library import ZipLibrary


def variables():
    """Devuelve variables ficticias completas para el documento."""
    return {
        "titulo_proyecto": "Proyecto sintético PPTP",
        "entidad": "Entidad de prueba", "plazo_garantia": "12 meses",
        "plazo_ejecucion": "5 días naturales",
        "bullet_capitulos_obra": "- Reposición de servicios\n- Pavimentación",
        "JUSTIFICACION_NO_APLICACION": "no cumplirse los supuestos contractuales de prueba",
        "FORMULA_REVISION_PRECIOS": "",
    }


def state_for(rows):
    """Serializa una revision sencilla sin decisiones normativas previas."""
    return {"variables": variables(), "selections": [asdict(row) for row in rows],
            "regulations": {}}


class PptpTests(unittest.TestCase):
    """Comprueba las correspondencias sin depender de un presupuesto privado."""

    def setUp(self):
        """Lee las fichas distribuidas para cada prueba."""
        self.library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        self.catalog = load_catalog(self.library)

    def test_index_decodes_unicode_paths_and_keeps_distinct_variants(self):
        """Las fichas del mismo codigo no se confunden por clasificacion."""
        self.assertEqual(len(self.catalog), 1656)
        matches = [sheet for sheet in self.catalog if sheet.code == "01TRA"]
        self.assertEqual(len(matches), 1)
        self.assertTrue(bool(self.library.read_sheet(matches[0].path)))
        self.assertIn("Ó", matches[0].path)
        self.assertGreater(len([sheet for sheet in self.catalog if sheet.code.startswith("02RR")]), 1)

    def test_all_document_placeholders_have_supported_project_variables(self):
        """Comprueba secciones generales y todas las fichas para detectar campos olvidados."""
        sources = [
            document
            for prefix in ("01", "02", "03", "04")
            for document in self.library.read_documents(prefix)
        ]
        sources.extend(
            (sheet.path, self.library.read_sheet(sheet.path)) for sheet in self.catalog
        )
        found = set()
        for name, source in sources:
            placeholders = {
                match[1].strip() for match in re.finditer(r"\{\{([^{}]+)\}\}", source)
                if match[1].strip() not in {"#if APLICA_REVISION_PRECIOS", "else", "/if"}
            }
            with self.subTest(source=name):
                self.assertEqual(placeholders - set(VARIABLES) - set(PRICE_VARIABLES), set())
                substituted = substitute(source, variables())
                self.assertNotIn("{{", substituted)
                self.assertNotIn("}}", substituted)
            found.update(placeholders)
        self.assertTrue(found <= set(VARIABLES) | set(PRICE_VARIABLES))
        self.assertTrue(set(PRICE_VARIABLES) <= found)

    def test_matching_reads_each_ancestor_but_not_siblings(self):
        """Busca los ficheros de 01, 01T y 01TR sin arrastrar 01TV."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = (
                "01_BASE/GENERAL.md", "01_BASE/01T_PREVIOS/PREVIOS.md",
                "01_BASE/01T_PREVIOS/01TR_REDES/REDES.md",
                "01_BASE/01T_PREVIOS/01TV_VEGETACION/VEGETACION.md",
            )
            for relative in paths:
                path = root / "UNI" / Path(relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# " + path.stem, encoding="utf-8")
            (root / "UNI" / "INDICE_FICHAS.md").write_text(
                "| 01TR | [Redes](" + paths[2] + ") | Revisar |\n", encoding="utf-8",
            )
            catalog = load_catalog(root)
            automatic, manual = propose([
                ProjectItem("01tr001*", "CAP#", "ud", "Servicios"),
            ], {}, catalog, {})
            self.assertEqual(manual, [])
            self.assertEqual({row.path.split("/")[-1] for row in automatic},
                             {"GENERAL.md", "PREVIOS.md", "REDES.md"})
            self.assertTrue(all(row.selected for row in automatic))

    def test_unknown_codes_and_separate_specialties(self):
        """No infiere codigos de titulos ni incorpora RCD o seguridad importados."""
        items = [
            ProjectItem("P1", "01#", "ud", "Demoliciones 01TR"),
            ProjectItem("01TRA00001", "02#", "ud", "Servicios"),
            ProjectItem("01TR002", "R#", "ud", "Residuo", source="RCD:X"),
            ProjectItem("01TR003", "S#", "ud", "Seguridad", source="SS:X"),
            ProjectItem("01TR004", "17A#", "ud", "Residuo"),
            ProjectItem("19SS001", "99#", "ud", "Casco"),
            ProjectItem("01TR005", "SYSA#", "ud", "Seguridad importada"),
        ]
        automatic, manual = propose(items, {
            "01#": "Demoliciones", "02#": "Redes", "17#": "Gestión de residuos",
            "SYS#": "Seguridad e higiene",
        }, self.catalog, {})
        self.assertEqual([row.code for row in automatic], ["01TRA00001"])
        self.assertEqual([row.code for row in manual], ["P1"])
        self.assertEqual(manual[0].path, "")

    def test_restoring_checks_manual_assignments_and_multiple_sheets(self):
        """Recupera tanto filas desmarcadas como asignaciones multiples."""
        items = [ProjectItem("01TRA00001", "01#", "ud", "Servicios"),
                 ProjectItem("P1", "02#", "ud", "Manual")]
        automatic, manual = propose(items, {}, self.catalog, {})
        automatic[0].selected = False
        manual[0].path = self.catalog[0].path
        manual.append(Selection(1, "P1", "02#", "Manual", self.catalog[1].path,
                                automatic=False))
        state = restore_state(json.loads(json.dumps(state_for(automatic + manual))))
        again, other = propose(items, {}, self.catalog, state)
        self.assertFalse(again[0].selected)
        self.assertEqual([row.path for row in other], [row.path for row in manual])
        self.assertEqual(restore_state({}), {})

    def test_classified_subchapters_propose_descendants_but_ordinals_do_not(self):
        """Admite clasificacion UNI del subcapitulo sin interpretar ordinales numericos."""
        items = [ProjectItem("P1", "01T#", "ud", "Subcapítulo clasificado"),
                 ProjectItem("P2", "01#", "ud", "Capítulo ordinal"),
                 ProjectItem("P3", "RCD#", "ud", "Especialidad sin título")]
        automatic, manual = propose(items, {}, self.catalog, {})
        self.assertEqual({row.code for row in automatic}, {"P1"})
        self.assertEqual({next(sheet.code for sheet in self.catalog if sheet.path == row.path)
                          for row in automatic},
                         {sheet.code for sheet in self.catalog if sheet.code.startswith("01T")})
        self.assertEqual([row.code for row in manual], ["P2"])

    def test_invalid_state_and_paths_are_rejected(self):
        """Rechaza valores booleanos incorrectos y rutas que salgan del catalogo."""
        row = Selection(0, "P1", "01#", "Prueba", self.catalog[0].path)
        valid = state_for([row])
        for invalid in (
            [], None, {"variables": {}}, {**valid, "regulations": {"X": 1}},
            {**valid, "selections": [{**asdict(row), "selected": "true"}]},
            {**valid, "selections": [{**asdict(row), "item": True}]},
            {**valid, "selections": [{**asdict(row), "path": ""}]},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                restore_state(invalid)
        for relative in ("../fuera.md", "UNI/../../fuera.md", "C:/fuera.md", r"C:\fuera.md",
                         "/fuera.md", "UNI/ficha.txt"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                safe_path(Path.cwd(), relative)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises((OSError, ValueError)):
            load_catalog(Path(folder))

    def test_regulations_are_filtered_deduplicated_and_review_is_unchecked(self):
        """Incluye prefijos y normas comunes sin dar por validas referencias dudosas."""
        refs = load_regulations(self.library, {"01TR001"})
        self.assertEqual(len({ref.id for ref in refs}), len(refs))
        self.assertTrue(any("01" in ref.codes for ref in refs))
        self.assertTrue(any("*" in ref.codes for ref in refs))
        self.assertTrue(any(ref.status == "revision" for ref in refs))
        self.assertTrue(all(not ref.selected for ref in refs if ref.status == "revision"))
        self.assertTrue(all(ref.condition for ref in refs))
        self.assertTrue(all("*" in ref.codes or any("01TR001".startswith(code)
                                                  or code.startswith("01TR001") for code in ref.codes)
                            for ref in refs))
        aggregate = load_regulations(self.library, {"15S"})
        self.assertTrue(any("15SH" in ref.codes for ref in aggregate))

    def test_contents_depth_restores_legacy_default_and_validates_saved_values(self):
        """Los proyectos anteriores usan dos niveles y los nuevos conservan la opcion."""
        state = state_for([Selection(0, "P1", "01#", "Prueba", self.catalog[0].path)])
        self.assertEqual(restore_state(state)["toc_depth"], 2)
        for depth in (1, 2, 6):
            self.assertEqual(restore_state({**state, "toc_depth": depth})["toc_depth"], depth)
        for depth in (0, 7, True, 1.5, "2", None):
            with self.subTest(depth=depth), self.assertRaisesRegex(ValueError, "índice"):
                restore_state({**state, "toc_depth": depth})

    def test_planning_duration_uses_saved_calendar_holidays_and_sequence(self):
        """Usa dias naturales del calendario calculado, no la suma de dias laborables."""
        app = SimpleNamespace(
            budget_items=[ProjectItem("P1", "01#", "ud", "Uno")],
            _chapter_title=lambda chapter: "Prueba",
            planning_state={"params": {
                "start": "2026-01-02", "holidays": [
                    {"date": "2026-01-05", "description": "Festivo ficticio"},
                ],
            }, "activities": [
                {"chapter": "01#", "code": "P1", "manual_duration": 3},
            ]},
        )
        self.assertEqual(planning_duration(app), "6 días naturales")
        app.pptp_state = {}
        app.report_settings = ReportSettings(organization="Entidad", guarantee_period="1 año")
        app._project_title = lambda: "Proyecto"
        values, missing = initial_variables(app)
        self.assertEqual(values["plazo_ejecucion"], "6 días naturales")
        self.assertFalse(missing)
        app.planning_state = {}
        values, missing = initial_variables(app)
        self.assertTrue(missing)
        self.assertEqual(values["plazo_ejecucion"], PENDING_DURATION)
        self.assertEqual(planning_duration(app), "")
        app.pptp_state = {"variables": {"plazo_ejecucion": "2 meses"}}
        self.assertEqual(initial_variables(app)[0]["plazo_ejecucion"], "2 meses")

    def test_guarantee_settings_are_validated_and_backward_compatible(self):
        """La garantia admite unidades y no rompe configuraciones anteriores."""
        self.assertEqual(ReportSettings.from_dict({}).guarantee_period, "")
        settings = ReportSettings.from_dict({"guarantee_period": "  12 meses  "})
        self.assertEqual(ReportSettings.from_dict(settings.to_dict()).guarantee_period, "12 meses")
        with self.assertRaises(ValueError):
            ReportSettings.from_dict({"guarantee_period": 12})

    def test_composition_substitutes_all_variables_and_deduplicates_sheets(self):
        """Mantiene las nueve secciones y solo incorpora fichas y normas elegidas."""
        sheet = self.catalog[0]
        rows = [Selection(0, "01TR001", "01#", "Uno", sheet.path),
                Selection(1, "01TR002", "01#", "Dos", sheet.path)]
        state = state_for(rows)
        refs = load_regulations(self.library, {"01TR001"})
        state["regulations"] = {ref.id: index == 0 for index, ref in enumerate(refs)}
        source = compose(self.library, state, refs)
        self.assertNotIn("{{", source)
        self.assertIn(variables()["titulo_proyecto"], source)
        self.assertIn(variables()["entidad"], source)
        self.assertIn("12 meses", source)
        self.assertIn("5 días naturales", source)
        self.assertIn("Reposición de servicios", source)
        self.assertEqual(source.count("### 9.1.1 " + sheet.title.upper()), 1)
        self.assertIn("## 9.1 DEMOLICIONES Y TRABAJOS PREVIOS", source)
        self.assertNotIn("## 9.2 ", source)
        self.assertIn(refs[0].text, source)
        self.assertNotIn(refs[1].text, source)
        self.assertIn("## Ejecución", self.library.read_sheet(sheet.path))
        with self.assertRaises(ValueError):
            substitute("{{variable_desconocida}}", variables())
        with self.assertRaises(ValueError):
            substitute("{{plazo_ejecucion}}", {**variables(), "plazo_ejecucion": PENDING_DURATION})
        rows[0].selected = rows[1].selected = False
        with self.assertRaises(ValueError):
            compose(self.library, state_for(rows), refs)

    def test_pdf_and_editable_odt_contain_sections_variables_and_template(self):
        """Verifica contenido real de ambos formatos, no solo su existencia."""
        row = Selection(0, "01TR001", "01#", "Servicios", self.catalog[0].path)
        source = compose(self.library, state_for([row]), load_regulations(self.library, {"01TR001"}))
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(output_format=output_format):
                    target = Path(folder) / ("PPTP." + output_format)
                    write_document(source, target, variables(), ReportSettings(output_format=output_format))
                    if output_format == "pdf":
                        reader = PdfReader(target)
                        text = "\n".join(page.extract_text() or "" for page in reader.pages)
                        self.assertGreater(len(reader.pages), 3)
                    else:
                        with ZipFile(target) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                            text = "".join(content.itertext())
                            styles = archive.read("styles.xml").decode("utf-8")
                            self.assertIn("header", styles)
                            self.assertIn("footer", styles)
                            self.assertTrue(any(name.startswith("Pictures/") for name in archive.namelist()))
                    for value in ("INTRODUCCIÓN", "DISPOSICIONES", "DESCRIPCIÓN DE LAS OBRAS",
                                  "MATERIALES", "Entidad de prueba", "12 meses", "5 días naturales",
                                  "EJECUCIÓN EN OBRA", "MEDICIÓN Y ABONO"):
                        self.assertIn(value, text)
                    self.assertNotIn("{{", text)
                    self.assertNotIn("NORMATIVA_UNI", text)
                    self.assertIn("9.1 DEMOLICIONES Y TRABAJOS PREVIOS", text)
                    self.assertNotIn("CAPÍTULO 01", text)
                    self.assertIn("9.1.1 " + self.catalog[0].title.upper(), text)
                    if output_format == "odt":
                        namespace = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
                        index = content.find(f".//{{{namespace}}}index-body")
                        index_text = "".join(index.itertext())
                        self.assertIn("9.1 DEMOLICIONES Y TRABAJOS PREVIOS", index_text)
                        self.assertNotIn(self.catalog[0].title.upper(), index_text)
                    else:
                        index_text = reader.pages[0].extract_text()
                        self.assertIn("9.1 DEMOLICIONES Y TRABAJOS PREVIOS", index_text)
                        self.assertNotIn(self.catalog[0].title.upper(), index_text)

    def test_sheets_are_sorted_by_uni_code_without_changing_saved_selection(self):
        """Ordena las fichas del capitulo nueve por codigo, no por partida ni titulo."""
        early = next(sheet for sheet in self.catalog if sheet.code.startswith("01A"))
        late = next(sheet for sheet in self.catalog if sheet.code.startswith("15P"))
        variants = [sheet for sheet in self.catalog if sheet.code.startswith("02RR")][:2]
        sheets = [late, variants[1], early, variants[0], late]
        state = state_for([
            Selection(index, "P" + str(index), "01#", "Prueba", sheet.path)
            for index, sheet in enumerate(sheets)
        ])
        original = deepcopy(state)
        source = compose(self.library, state, [])
        ordered = sorted([early, late, *variants], key=lambda sheet: (sheet.code, sheet.path.casefold()))
        positions = [
            source.index(f"### {number} {sheet.title.upper()}")
            for number, sheet in zip(("9.1.1", "9.2.1", "9.2.2", "9.3.1"), ordered)
        ]
        self.assertEqual(positions, sorted(positions))
        group_headings = [line for line in source.splitlines() if line.startswith("## 9.")]
        self.assertEqual(group_headings, [
            "## 9.1 DEMOLICIONES Y TRABAJOS PREVIOS",
            "## 9.2 ACONDICIONAMIENTO DE TERRENOS",
            "## 9.3 URBANIZACIONES",
        ])
        self.assertIn("#### 9.1.1.1 DEFINICIÓN", source)
        self.assertIn("#### 9.1.1.2 EJECUCIÓN EN OBRA", source)
        self.assertIn("##### 9.1.1.2.1 PREPARACIÓN Y PROCEDIMIENTO", source)
        self.assertIn("#### 9.1.1.3 MEDICIÓN Y ABONO", source)
        self.assertNotIn("### 9.3.2 ", source)
        self.assertEqual(state, original)
        for sheet in ordered:
            original_blocks = text_blocks(self.library.read_sheet(sheet.path))
            for level, text in original_blocks:
                if level == 0:
                    self.assertIn(text, "\n".join(text for _, text in text_blocks(source)))

    def test_composition_groups_unique_sheets_by_budget_chapter(self):
        """Presenta fichas por capítulo presupuestario sin alterar la selección BCCA."""
        early = next(sheet for sheet in self.catalog if sheet.code.startswith("01A"))
        late = next(sheet for sheet in self.catalog if sheet.code.startswith("15P"))
        state = state_for([
            Selection(2, "P3", "02#", "Tercera partida", early.path),
            Selection(0, "P1", "01#", "Primera partida", late.path),
            Selection(1, "P2", "02#", "Segunda partida", early.path),
        ])
        source = compose(
            self.library, state, [],
            budget_chapter_titles={"01#": "Demoliciones del presupuesto", "02#": "Redes del presupuesto"},
        )
        first_heading = "## 9.1 DEMOLICIONES DEL PRESUPUESTO"
        second_heading = "## 9.2 REDES DEL PRESUPUESTO"
        self.assertIn(first_heading, source)
        self.assertIn(second_heading, source)
        self.assertLess(source.index(first_heading), source.index(second_heading))
        self.assertLess(source.index(late.title.upper()), source.index(second_heading))
        self.assertGreater(source.index(early.title.upper()), source.index(second_heading))
        self.assertEqual(source.count(early.title.upper()), 1)

    def test_markdown_preserves_structure_and_external_references(self):
        """No imprime comentarios internos ni enlaces locales rotos como URLs."""
        blocks = text_blocks("# Título\n\nPárrafo **destacado**.\n\n"
                             "- Elemento\n> Aviso\n\n[Norma](https://example.org)\n"
                             "[Origen](../../PPTP.md)\n<!-- oculto -->")
        self.assertEqual(blocks[0], (1, "Título"))
        text = "\n".join(text for _, text in blocks)
        self.assertIn("https://example.org", text)
        self.assertNotIn("../", text)
        self.assertNotIn("oculto", text)

    def test_bullet_indentation_is_preserved_in_pdf_and_odt(self):
        """Conserva los niveles de sangría de las listas al exportar ambos formatos."""
        source = "# Lista\n\n* Nivel cero\n    * Nivel uno\n        * Nivel dos\n"
        blocks = text_blocks(source)
        self.assertEqual(blocks, [
            (1, "Lista"), (-1, "• Nivel cero"), (-2, "• Nivel uno"),
            (-3, "• Nivel dos"),
        ])

        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(output_format=output_format):
                    target = Path(folder) / ("PPTP." + output_format)
                    write_document(
                        source, target, variables(), ReportSettings(output_format=output_format),
                    )
                    if output_format == "pdf":
                        positions = {}
                        for page in PdfReader(target).pages:
                            page.extract_text(visitor_text=lambda *args: positions.setdefault(
                                args[0].strip(), args[2][4],
                            ))
                        bullet_positions = [
                            positions[f"• Nivel {level}"] for level in ("cero", "uno", "dos")
                        ]
                        self.assertAlmostEqual(bullet_positions[1] - bullet_positions[0], 12)
                        self.assertAlmostEqual(bullet_positions[2] - bullet_positions[1], 12)
                    else:
                        namespaces = {
                            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
                            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
                            "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
                        }
                        with ZipFile(target) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                            named_styles = ET.fromstring(archive.read("styles.xml"))
                        paragraphs = content.findall(".//text:p", namespaces)
                        bullet_styles = [
                            paragraph.get(f"{{{namespaces['text']}}}style-name")
                            for paragraph in paragraphs
                            if "Nivel" in "".join(paragraph.itertext())
                        ]
                        self.assertEqual(bullet_styles, [
                            "PPTPBullet0", "PPTPBullet1", "PPTPBullet2",
                        ])
                        styles = {
                            style.get(f"{{{namespaces['style']}}}name"): style
                            for root in (content, named_styles)
                            for style in root.findall(".//style:style", namespaces)
                        }
                        for depth in range(3):
                            properties = styles[f"PPTPBullet{depth}"].find(
                                "style:paragraph-properties", namespaces,
                            )
                            self.assertEqual(
                                properties.get(f"{{{namespaces['fo']}}}margin-left"),
                                f"{12 * (depth + 1)}pt",
                            )
                            self.assertEqual(
                                properties.get(f"{{{namespaces['fo']}}}text-indent"), "-12pt",
                            )

    def test_editable_odt_renders_in_libreoffice(self):
        """Confirma que Writer abre el ODT y conserva sus secciones al imprimirlo."""
        soffice = shutil.which("soffice") or str(
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "LibreOffice" / "program" / "soffice.exe"
        )
        if not Path(soffice).is_file():
            self.skipTest("LibreOffice no disponible para validar el ODT renderizado.")
        row = Selection(0, "01TR001", "01#", "Servicios", self.catalog[0].path)
        source = compose(self.library, state_for([row]), load_regulations(self.library, {"01TR001"}))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            editable = root / "PPTP.odt"
            write_document(source, editable, variables(), ReportSettings(output_format="odt"))
            result = subprocess.run(
                [soffice, f"-env:UserInstallation={(root / 'lo-profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(root), str(editable)],
                check=True, capture_output=True, timeout=90,
            )
            rendered = root / "PPTP.pdf"
            self.assertTrue(rendered.is_file(), result.stdout.decode(errors="replace"))
            reader = PdfReader(rendered)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            for value in ("INTRODUCCIÓN", "DESCRIPCIÓN DE LAS OBRAS", "Entidad de prueba",
                          "12 meses", "5 días naturales", "MEDICIÓN Y ABONO"):
                self.assertIn(value, text)
            self.assertGreater(len(reader.pages), 3)


class PptpWindowTests(unittest.TestCase):
    """Comprueba navegacion, persistencia y cancelacion con ventanas Tk reales."""

    def setUp(self):
        """Aisla preferencias, proyecto y ventanas de los datos del usuario."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with patch.object(self.root, "state"), patch("bc3_gui.user_data_directory", return_value=self.folder):
            self.app = BudgetApp(self.root)
        self.app.project_title = "Proyecto ficticio"
        self.app.report_settings.organization = "Entidad ficticia"
        self.app.report_settings.guarantee_period = "12 meses"
        self.app.budget_items = [
            ProjectItem("01TRA00001", "01#", "ud", "Servicios"),
            ProjectItem("P1", "01#", "ud", "Sin clasificación"),
        ]
        self.app._reset_history()

    def make_window(self):
        """Crea un asistente registrado para comprobar su cierre al cambiar proyecto."""
        open_tool(self.app)
        return next(window for window in self.app._project_windows if isinstance(window, PptpWindow))

    def next_steps(self, window):
        """Completa variables pendientes y avanza hasta las asignaciones manuales."""
        window.variables["plazo_ejecucion"].set("36 meses" if window.price_revision.get() else "2 meses")
        window.variables["JUSTIFICACION_NO_APLICACION"].set("no cumplirse los supuestos de prueba")
        window.summary.insert("1.0", "Resumen ficticio de obras")
        window.accept()
        self.assertEqual(window.step, 1)
        window.accept()
        self.assertEqual(window.step, 2)

    def test_first_step_exposes_every_document_variable(self):
        """Cada variable sustituible tiene un campo editable en la primera pantalla."""
        window = self.make_window()
        self.assertEqual(window.step, 0)
        self.assertEqual(set(window.variables), set(VARIABLES) | set(PRICE_VARIABLES))
        entries = [
            child
            for frame in window.content.winfo_children()
            for child in frame.winfo_children()
            if isinstance(child, ttk.Entry)
        ]
        bound_variables = {str(entry.cget("textvariable")) for entry in entries}
        for name in set(VARIABLES) - {"bullet_capitulos_obra"}:
            self.assertIn(str(window.variables[name]), bound_variables)
        self.assertTrue(window.summary.winfo_exists())
        self.assertEqual(
            window.summary.get("1.0", "end-1c"), window.variables["bullet_capitulos_obra"].get(),
        )
        self.next_steps(window)
        self.assertEqual(
            window.variables["bullet_capitulos_obra"].get(), "Resumen ficticio de obras",
        )

    def test_first_step_omits_requested_hints_and_keeps_summary_and_missing_plan_warning(self):
        """Remove display-only details without changing fields or planning validation."""
        window = self.make_window()
        for missing in (True, False):
            window.missing_plan = missing
            window.show_step()
            widgets = list(window.content.winfo_children())
            labels = []
            while widgets:
                widget = widgets.pop()
                widgets.extend(widget.winfo_children())
                if isinstance(widget, ttk.Label):
                    labels.append(widget.cget("text"))
            self.assertIn("Resumen de las obras", labels)
            for forbidden in ("La no revisión se propone", "Plazo de ejecución obtenido", "{{bullet_capitulos_obra}}"):
                self.assertFalse(any(forbidden in text for text in labels))
            self.assertEqual(any("No se ha realizado la planificación" in text for text in labels), missing)

    def test_library_edits_reload_matches_without_losing_review_or_project_text(self):
        """Create and rename real sheets while keeping open wizard decisions intact."""
        from tools._library_history import read_revisions

        archive = self.folder / "library.zip"
        shutil.copyfile(LIBRARY_PATH, archive)
        self.app.budget_items[0].code = "01TRA00001"
        with patch("tools.pptp.LIBRARY_PATH", archive):
            window = self.make_window()
            common = next(sheet for sheet in window.catalog if sheet.code == "01TRA")
            next(row for row in window.automatic if row.path == common.path).selected = False
            window.unmatched[0].path = common.path
            window.unmatched[0].selected = False
            window.variables["plazo_ejecucion"].set("2 meses")
            window.variables["JUSTIFICACION_NO_APLICACION"].set("no concurrir los requisitos de prueba")
            window.summary.insert("1.0", "Texto pendiente del proyecto")
            window.open_library_editor()
            editor = window.library_editor
            editor.tree.selection_set("file:" + common.path)
            editor._select()
            editor.revision_author.set("Autor de prueba")
            with patch(
                "tools._library_editor.simpledialog.askstring",
                return_value="01TRA-Condiciones de prueba.md",
            ):
                editor.rename_sheet()
            renamed = window.library.resolve_sheet(common.path)
            self.assertNotEqual(renamed, common.path)
            history, _ = read_revisions(window.library.read_sheet(renamed))
            self.assertEqual([row.version for row in history], [2, 1])
            self.assertEqual(history[0].author, "Autor de prueba")
            self.assertFalse(next(row for row in window.automatic if row.path == renamed).selected)
            self.assertEqual(window.unmatched[0].path, renamed)
            self.assertFalse(window.unmatched[0].selected)
            editor.revision_author.set("Autor de prueba")
            with patch(
                "tools._library_editor.simpledialog.askstring",
                return_value="01TRA-Ficha de prueba.md",
            ):
                editor.new_sheet()
            new_path = editor.selected_member
            history, _ = read_revisions(window.library.read_sheet(new_path))
            self.assertEqual([row.version for row in history], [1])
            self.assertTrue(any(row.path == new_path for row in window.automatic))
            self.assertEqual(window.summary.get("1.0", "end-1c"), "Texto pendiente del proyecto")
            self.assertEqual(editor.tree.item("file:" + new_path, "tags"), ("used",))
            window.accept()
            self.assertEqual(window.step, 1)
            editor.revision_author.set("Autor de prueba")
            with patch(
                "tools._library_editor.simpledialog.askstring",
                return_value="01TRA-Ficha de prueba renombrada.md",
            ):
                editor.rename_sheet()
            self.assertTrue(any(
                row.path == editor.selected_member for row in window.automatic
            ))
            history, _ = read_revisions(window.library.read_sheet(editor.selected_member))
            self.assertEqual([row.version for row in history], [2, 1])
            self.assertEqual(len(window.tree.get_children()), len(window.automatic))
            self.assertEqual(self.app.pptp_state, {})

    def test_steps_checks_manual_dropdown_and_regulation_refresh(self):
        """Navega, abre desplegable, selecciona ficha y recalcula normas vinculadas."""
        window = self.make_window()
        self.assertTrue(window.missing_plan)
        self.assertTrue(any("Realiza Herramientas" in child.cget("text")
                            for child in window.content.winfo_children() if isinstance(child, ttk.Label)))
        with patch("tools.pptp.messagebox.showerror") as error:
            window.accept()
        self.assertEqual(window.step, 0)
        self.assertIn("plazo_ejecucion", error.call_args.args[1])
        self.next_steps(window)
        window.tree.selection_set("0")
        window.edit_sheet(SimpleNamespace(keysym="Return"))
        self.root.update_idletasks()
        self.assertIsNotNone(window.editor)
        choices = window.editor.cget("values")
        self.assertEqual(len(choices), len(window.catalog))
        target_index = next(index for index, sheet in enumerate(window.catalog) if sheet.code.startswith("15SH"))
        window.editor.current(target_index)
        window.editor.event_generate("<<ComboboxSelected>>")
        self.assertEqual(window.unmatched[0].path, window.catalog[target_index].path)
        self.assertTrue(any("15SH" in ref.codes for ref in window.regulations))
        window.tree.selection_set("0")
        window.add_sheet()
        self.assertEqual(len(window.unmatched), 2)
        window.tree.selection_set("1")
        window.toggle(SimpleNamespace(keysym="space"))
        self.assertFalse(window.unmatched[1].selected)
        window.back()
        window.back()
        self.assertIn("Resumen ficticio", window.summary.get("1.0", "end"))
        self.assertEqual(self.app.pptp_state, {})

    def test_generate_saves_json_and_reopens_selections(self):
        """Guarda de verdad el proyecto y un ODT recuperable con las selecciones."""
        window = self.make_window()
        window.variables["titulo_proyecto"].set("Título revisado")
        window.variables["entidad"].set("Entidad revisada")
        window.variables["plazo_garantia"].set("24 meses")
        self.assertEqual(window.toc_depth.get(), "2")
        window.toc_depth.set("3")
        window.price_revision.set(True)
        window.update_price_revision()
        window.variables["FORMULA_REVISION_PRECIOS"].set("Kt = 0,40 + 0,60 * It / I0")
        window.variables["JUSTIFICACION_APLICACION"].set("Motivo contractual de prueba")
        self.next_steps(window)
        window.variables["plazo_ejecucion"].set("24 meses")
        window.unmatched[0].selected = False
        self.app.report_settings.output_format = "odt"
        self.app.project_path = self.folder / "proyecto.json"
        target = self.folder / "PPTP.odt"
        with patch("tools.pptp.filedialog.asksaveasfilename", return_value=str(target)), \
                patch("tools.pptp.messagebox.showinfo"):
            window.generate()
        saved = json.loads(self.app.project_path.read_text(encoding="utf-8"))
        self.assertTrue(target.is_file())
        self.assertEqual(saved["pptp"], self.app.pptp_state)
        self.assertEqual(saved["title"], "Título revisado")
        self.assertEqual(saved["report_settings"]["organization"], "Entidad revisada")
        self.assertEqual(saved["report_settings"]["guarantee_period"], "24 meses")
        self.assertEqual(saved["pptp"]["toc_depth"], 3)
        self.assertTrue(saved["pptp"]["aplica_revision_precios"])
        with ZipFile(target) as archive:
            content = ET.fromstring(archive.read("content.xml"))
        text_namespace = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        toc_source = content.find(f".//{{{text_namespace}}}table-of-content-source")
        self.assertEqual(toc_source.get(f"{{{text_namespace}}}outline-level"), "3")
        self.assertFalse(saved["pptp"]["selections"][-1]["selected"])
        self.assertFalse(self.app.is_dirty)
        state = deepcopy(self.app.pptp_state)
        self.app.pptp_state = {}
        self.assertTrue(self.app._load_project_file(self.app.project_path))
        self.assertEqual(self.app.pptp_state, state)
        restored = self.make_window()
        self.assertFalse(restored.unmatched[0].selected)
        self.assertEqual(restored.variables["plazo_ejecucion"].get(), "24 meses")
        self.assertEqual(restored.toc_depth.get(), "3")
        self.assertTrue(restored.price_revision.get())
        self.assertEqual(restored.variables["JUSTIFICACION_APLICACION"].get(), "Motivo contractual de prueba")
        self.assertEqual(restored.variables["FORMULA_REVISION_PRECIOS"].get(),
                         "Kt = 0,40 + 0,60 * It / I0")
        self.app.pptp_state["variables"]["entidad"] = "Otra entidad"
        self.app._mark_dirty()
        self.app.undo_last_action()
        self.assertFalse(restored.winfo_exists())
        self.assertEqual(self.app.pptp_state, state)

    def test_invalid_contents_depth_prevents_advancing_and_navigation_keeps_value(self):
        """El primer paso avisa ante valores incorrectos y conserva los validos al volver."""
        window = self.make_window()
        window.toc_depth.set("7")
        with patch("tools.pptp.messagebox.showerror") as error:
            window.accept()
        self.assertEqual(window.step, 0)
        self.assertIn("índice", error.call_args.args[1])
        window.toc_depth.set("4")
        self.next_steps(window)
        window.back()
        window.back()
        self.assertEqual(window.toc_depth.get(), "4")
        self.assertEqual(self.app.pptp_state, {})

    def test_price_revision_toggle_validates_only_active_field_and_preserves_values(self):
        """Require justification or formula, with no silent defaults or inactive-field errors."""
        window = self.make_window()
        self.assertFalse(window.price_revision.get())
        self.assertEqual(str(window.price_entries["FORMULA_REVISION_PRECIOS"]["state"]), "disabled")
        self.assertEqual(str(window.price_entries["JUSTIFICACION"]["state"]), "normal")
        window.variables["plazo_ejecucion"].set("36 meses")
        window.summary.insert("1.0", "Resumen")
        with patch("tools.pptp.messagebox.showerror") as error:
            window.accept()
        self.assertEqual(window.step, 0)
        self.assertIn("JUSTIFICACION_NO_APLICACION", error.call_args.args[1])
        window.price_revision.set(True)
        window.update_price_revision()
        self.assertEqual(str(window.price_entries["FORMULA_REVISION_PRECIOS"]["state"]), "normal")
        with patch("tools.pptp.messagebox.showerror") as error:
            window.accept()
        self.assertIn("FORMULA_REVISION_PRECIOS", error.call_args.args[1])
        window.variables["FORMULA_REVISION_PRECIOS"].set("Kt = 1")
        with patch("tools.pptp.messagebox.showerror") as error:
            window.accept()
        self.assertIn("JUSTIFICACION_APLICACION", error.call_args.args[1])
        window.variables["JUSTIFICACION_APLICACION"].set("Motivo contractual")
        window.accept()
        self.assertEqual(window.step, 1)
        window.back()
        self.assertTrue(window.price_revision.get())
        window.price_revision.set(False)
        window.update_price_revision()
        window.variables["JUSTIFICACION_NO_APLICACION"].set("no concurrir los requisitos")
        window.accept()
        self.assertEqual(window.step, 1)
        window.back()
        self.assertEqual(window.variables["FORMULA_REVISION_PRECIOS"].get(), "Kt = 1")
        self.assertEqual(self.app.pptp_state, {})

    def test_single_justification_precedes_formula_and_keeps_both_alternatives(self):
        """Show only one justification row, bound to the selected branch without losing edits."""
        from tools.pptp.document import SHORT_DURATION_REASON

        window = self.make_window()
        window.variables["plazo_ejecucion"].set("24 meses")
        justification = window.price_entries["JUSTIFICACION"]
        formula = window.price_entries["FORMULA_REVISION_PRECIOS"]
        labels = [child.cget("text") for child in justification.master.winfo_children()
                  if isinstance(child, ttk.Label)]
        self.assertEqual(labels.count("Justificación"), 1)
        self.assertNotIn("Justificación de aplicación de revisión", labels)
        self.assertNotIn("Justificación de no aplicación de revisión", labels)
        self.assertLess(int(window.price_check.grid_info()["row"]), int(justification.grid_info()["row"]))
        self.assertLess(int(justification.grid_info()["row"]), int(formula.grid_info()["row"]))
        self.assertIn(SHORT_DURATION_REASON, justification.get())
        justification.delete(0, "end")
        justification.insert(0, "Motivo de no aplicación")
        window.price_check.invoke()
        self.assertEqual(justification.get(), "")
        justification.insert(0, "Motivo de aplicación")
        self.assertEqual(window.variables["JUSTIFICACION_APLICACION"].get(), "Motivo de aplicación")
        window.price_check.invoke()
        self.assertEqual(justification.get(), "Motivo de no aplicación")
        window.price_check.invoke()
        self.assertEqual(justification.get(), "Motivo de aplicación")
        self.assertEqual(str(formula["state"]), "normal")

    def test_short_duration_proposes_reason_but_preserves_manual_revision(self):
        """React to edits without blocking or overriding the manually selected alternative."""
        from tools.pptp.document import SHORT_DURATION_REASON

        window = self.make_window()
        window.variables["JUSTIFICACION_NO_APLICACION"].set("motivo contractual")
        window.variables["plazo_ejecucion"].set("24 meses")
        self.assertFalse(window.price_revision.get())
        self.assertEqual(str(window.price_check["state"]), "normal")
        self.assertIn(SHORT_DURATION_REASON, window.variables["JUSTIFICACION_NO_APLICACION"].get())
        self.assertIn("motivo contractual", window.variables["JUSTIFICACION_NO_APLICACION"].get())
        window.price_check.invoke()
        self.assertTrue(window.price_revision.get())
        window.duration_changed()
        self.assertTrue(window.price_revision.get())
        window.variables["plazo_ejecucion"].set("3 años")
        self.assertTrue(window.price_revision.get())
        self.assertEqual(str(window.price_check["state"]), "normal")
        self.assertEqual(window.variables["JUSTIFICACION_NO_APLICACION"].get(), "motivo contractual")
        window.price_start = "2023-01-01"
        window.variables["plazo_ejecucion"].set("731 días naturales")
        self.assertEqual(str(window.price_check["state"]), "normal")
        window.variables["plazo_ejecucion"].set("732 días naturales")
        self.assertEqual(str(window.price_check["state"]), "normal")

    def test_cancel_does_not_change_project_and_new_project_clears_state(self):
        """Cancelar y cambiar de proyecto no deja datos ni ventanas de otro proyecto."""
        window = self.make_window()
        self.next_steps(window)
        with patch("tools.pptp.filedialog.asksaveasfilename", return_value=""):
            window.unmatched[0].selected = False
            window.generate()
        self.assertEqual(self.app.pptp_state, {})
        self.assertFalse(self.app.is_dirty)
        row = window.automatic[0]
        self.app.pptp_state = state_for([row])
        with patch("bc3_gui.simpledialog.askstring", return_value="Nuevo"), \
                patch.object(self.app, "_confirm_project_change", return_value=True):
            self.app.new_project()
        self.assertFalse(window.winfo_exists())
        self.assertEqual(self.app.pptp_state, {})

    def test_invalid_selection_failed_save_and_export_preserve_existing_output(self):
        """No sobrescribe documentos previos cuando falla validacion, JSON o exportacion."""
        window = self.make_window()
        self.next_steps(window)
        with self.assertRaises(ValueError):
            window.generate()
        window.unmatched[0].selected = False
        target = self.folder / "PPTP.pdf"
        target.write_bytes(b"documento previo")
        with patch("tools.pptp.filedialog.asksaveasfilename", return_value=str(target)), \
                patch.object(self.app, "save_project", return_value=False), \
                patch("tools.pptp.write_document") as writer:
            window.generate()
        writer.assert_not_called()
        self.assertEqual(target.read_bytes(), b"documento previo")
        self.app.project_path = self.folder / "proyecto.json"
        with patch("tools.pptp.filedialog.asksaveasfilename", return_value=str(target)), \
                patch("tools.pptp.write_document", side_effect=ValueError("error ficticio")), \
                self.assertRaises(ValueError):
            window.generate()
        self.assertEqual(target.read_bytes(), b"documento previo")
        self.assertEqual(list(self.folder.glob("tmp*")), [])
        self.assertTrue(json.loads(self.app.project_path.read_text(encoding="utf-8"))["pptp"])
        self.app.budget_items.append(ProjectItem("P2", "02#", "ud", "Cambio"))
        with self.assertRaisesRegex(ValueError, "presupuesto ha cambiado"):
            window.generate()
