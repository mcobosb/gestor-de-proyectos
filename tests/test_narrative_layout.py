"""Comprobaciones comunes de tipografía de los informes ODT."""
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_reports import ReportSettings
from odt_reports import _new_report, _tag
from tools._shared.document_layout import (
    LIST_INDENT, TABLE_HEADER_BACKGROUND, template_paragraph_styles,
)
from tools._shared.scheduling import PlanActivity, PlanParams
from tools.document_structure.generation import _write_catalog_document, _write_memory_document
from tools.document_structure.office import _master_link, write_project_master
from tools.planning.document import write_planning_report
from tools.pptp.document import write_document as write_pptp_document
from tools.price_justification.document import LISTINGS, _write_odt as write_prices_odt
from tools.quality_assurance.document import _write_odt as write_quality_odt
from tools.quality_assurance.document import _write_pdf as write_quality_pdf
from tools.rcd.document import DESCRIPTION_HEADING, StudyTable, _write_odt as write_rcd_odt
from tools.rcd.document import _write_pdf as write_rcd_pdf
from tools.safety_health import write_study_report
from tools.safety_health.document import (
    _Block, _StudyContent, _pdf_blue_table, _write_odt as write_safety_odt,
)


class NarrativeLayoutTests(unittest.TestCase):
    """Verifica configuración y plantilla en varios generadores."""

    def test_narrative_table_fonts_do_not_inherit_body_typography(self):
        """Las tablas de anejos mantienen Arial Narrow salvo ajuste específico."""
        from tools.pptp.sheet_index import SheetIndexRow, write_odt_index
        from tools.price_justification.document import _add_odt_table_styles
        from tools.safety_health.document import _add_odt_blue_table_styles

        for table_font in (None, "Courier New"):
            with self.subTest(table_font=table_font), TemporaryDirectory() as folder:
                configured = {"body": {"font": "Times New Roman"}}
                if table_font:
                    configured.update({
                        "table": {"font": table_font},
                        "table_header": {"font": table_font},
                    })
                document = _new_report("Anejo", "Proyecto", ReportSettings(text_styles=configured))
                _add_odt_table_styles(document)
                _add_odt_blue_table_styles(document)
                write_odt_index(document, [SheetIndexRow("Capítulo", "Ficha", "PPTPHeading0")])
                target = Path(folder) / "tablas.odt"
                document.write(target)
                with ZipFile(target) as archive:
                    roots = [ET.fromstring(archive.read(name)) for name in ("content.xml", "styles.xml")]
                for name in (
                    "PricesTableText", "PricesTableHeader", "ESSSummaryText", "ESSSummaryHeader",
                    "PPTPIndexText", "PPTPIndexHeader",
                ):
                    style = next(style for root in roots for style in root.iter(_tag("style:style"))
                                 if style.get(_tag("style:name")) == name)
                    props = style.find(_tag("style:text-properties"))
                    self.assertEqual(props.get(_tag("fo:font-family")), table_font or "Arial Narrow", name)

    def test_catalog_indices_match_master_pages_before_and_after_update(self):
        """Los índices de memoria y anejos conservan destinos y ámbito propios."""
        program = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "LibreOffice" / "program"
        executable = program / "python.exe"
        if not executable.is_file():
            self.skipTest("La prueba UNO requiere el Python incluido en LibreOffice.")
        expected = [
            ["MEMORY_ONE", "MEMORY_TWO"],
            ["ANNEX_ONE", "ANNEX_TWO"],
            ["OTHER_ONE", "OTHER_TWO"],
        ]
        settings = ReportSettings(text_styles={"heading1": {"font": "Courier New", "size": 18}})
        app = SimpleNamespace(_project_title=lambda: "Proyecto")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            _write_memory_document(app, root / "memoria.odt", [
                {"title": "MEMORY_ONE", "outline": ["MEMORY_TWO"]},
            ], settings)
            _write_catalog_document(app, root / "aneje1.odt", "Primero", expected[1], settings)
            _write_catalog_document(app, root / "aneje2.odt", "Segundo", expected[2], settings)
            master = root / "maestro.odm"
            links = [Path("memoria.odt"), Path("aneje1.odt"), Path("aneje2.odt")]
            write_project_master(
                None, master, fields={}, traceability=[], links=links,
                index_entries=[
                    {"text": "Memoria", "href": _master_link(links[0])},
                    {"text": "Anejo 1", "href": _master_link(links[1]),
                     "annex_number": "1", "annex_title": "Primero"},
                    {"text": "Anejo 2", "href": _master_link(links[2]),
                     "annex_number": "2", "annex_title": "Segundo"},
                ],
                folder=root, text_styles=settings.text_styles,
            )
            expectations = root / "indices.json"
            expectations.write_text(json.dumps(expected), encoding="utf-8")
            try:
                result = subprocess.run(
                    [str(executable), str(Path(__file__).with_name("_libreoffice_contents_check.py")),
                     str(master), str(expectations)], capture_output=True, text=True, timeout=120,
                )
            finally:
                pid_file = root / "soffice.pid"
                if pid_file.is_file():
                    pid = int(pid_file.read_text().strip())
                    subprocess.run([
                        "powershell", "-NoProfile", "-Command",
                        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
                        f"if ($p -and $p.CommandLine.Contains('{root.name}')) "
                        f"{{ Stop-Process -Id {pid} -Force }}",
                    ], check=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_annex_contents_refresh_locally_with_real_pages_in_master(self):
        """Cada índice conserva sus títulos y páginas al actualizar un ODM conjunto."""
        program = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "LibreOffice" / "program"
        executable = program / "python.exe"
        if not executable.is_file():
            self.skipTest("La prueba UNO requiere el Python incluido en LibreOffice.")
        expected = {
            "RcdContents": ["RCD_ONE", "RCD_TWO", "RCD_THREE"],
            "PricesContents": ["PRICE_ONE", "PRICE_TWO", "PRICE_THREE",
                               *(title for title, _report in LISTINGS)],
            "QualityContents": ["QUALITY_ONE", "QUALITY_TWO"],
            "PPTPContents": ["PPTP_ONE", "PPTP_TWO"],
            "ESSContents": ["SAFETY_ONE", "SAFETY_TWO"],
        }
        settings = ReportSettings()
        app = SimpleNamespace(report_settings=settings, _project_title=lambda: "Proyecto")
        with TemporaryDirectory() as folder:
            root = Path(folder)
            template = root / "plantilla.odt"
            report = _new_report("Maestro", "Proyecto", settings)
            report.paragraph("Portada")
            report.write(template)
            write_rcd_odt([
                (1, "RCD_ONE"), (0, "Texto de la memoria. " * 120),
                (2, "RCD_TWO"), (0, "Texto del apartado. " * 120),
                (1, "RCD_THREE"),
                StudyTable("Cabeceras", ["HEADER_PLAIN", "HEADER_RIGHT", "HEADER_CENTER"],
                           [["A", "1", "B"]], [1, 1, 1],
                           right_columns=(1,), center_columns=(2,)),
            ], "Proyecto", settings, root / "rcd.odt")

            def listing(_app, _report, document, **_kwargs):
                """Sustituye solo el presupuesto ajeno al cálculo de los índices."""
                document.paragraph("Contenido del listado. " * 120)

            with patch("odt_reports.append_report_odt", side_effect=listing):
                write_prices_odt(app, root / "precios.odt", [
                    (1, "PRICE_ONE"), (0, "Texto de justificación. " * 120),
                    (2, "PRICE_TWO"), (3, "PRICE_THREE"),
                ])
            write_quality_odt(root / "calidad.odt", "Proyecto", settings,
                              [(1, "QUALITY_ONE"), (0, "Control. " * 120),
                               (2, "QUALITY_TWO")], {})
            write_pptp_document("# PPTP_ONE\n\nTexto del pliego.\n\n## PPTP_TWO",
                                root / "pptp.odt", {"titulo_proyecto": "Proyecto"}, settings)
            write_safety_odt(app, root / "seguridad.odt", _StudyContent(
                "Seguridad", "Proyecto", "", "EUR", [
                    _Block("heading", "SAFETY_ONE", 1),
                    _Block("paragraph", "Prevención. " * 120),
                    _Block("heading", "SAFETY_TWO", 2),
                ],
            ), [], [], "A4")
            master = root / "maestro.odm"
            write_project_master(
                template, master, fields={}, index_entries=[], traceability=[],
                links=[Path(name) for name in
                       ("rcd.odt", "precios.odt", "calidad.odt", "pptp.odt", "seguridad.odt")],
                folder=root,
            )
            expectations = root / "indices.json"
            expectations.write_text(json.dumps(expected), encoding="utf-8")
            try:
                result = subprocess.run(
                    [str(executable), str(Path(__file__).with_name("_libreoffice_contents_check.py")),
                     str(master), str(expectations)], capture_output=True, text=True, timeout=120,
                )
            finally:
                pid_file = root / "soffice.pid"
                if pid_file.is_file():
                    pid = int(pid_file.read_text().strip())
                    subprocess.run([
                        "powershell", "-NoProfile", "-Command",
                        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
                        f"if ($p -and $p.CommandLine.Contains('{root.name}')) "
                        f"{{ Stop-Process -Id {pid} -Force }}",
                    ], check=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rcd_prescriptions_and_reference_lists_are_justified(self):
        """Justifica los dos apartados y sus sublistas sin cambiar los demás."""
        from report_layout import ReportLayout
        from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT

        blocks = [
            (2, "2.2. PRESCRIPCIONES DE ALMACENAMIENTO, MANEJO Y GESTIÓN EN OBRA"),
            (-1, "• ALMACENAMIENTO_PRUEBA " + "Condiciones de almacenamiento y protección. " * 12),
            (3, "2.2.1. Condiciones particulares"),
            (-2, "• MANEJO_PRUEBA " + "Se mantendrán las zonas de acopio. " * 12),
            (2, "2.3. DOCUMENTACIÓN Y TRAZABILIDAD"),
            (-1, "• CONTROL_DOCUMENTACIÓN Texto de otro apartado."),
            (2, "2.4. NORMATIVA DE REFERENCIA"),
            (-1, "• Real Decreto 105/2008, " + "referencia aplicable a la gestión de residuos. " * 12),
            (1, "3. PRESUPUESTO"),
            (-1, "• CONTROL_PRESUPUESTO Texto fuera de los apartados solicitados."),
        ]
        captured = []
        render = ReportLayout.paragraph

        def capture(layout, markup, style=None, **kwargs):
            """Observa los estilos que se utilizan realmente al generar el PDF."""
            paragraph = render(layout, markup, style, **kwargs)
            if paragraph.bulletText:
                captured.append(paragraph)
            return paragraph

        with TemporaryDirectory() as folder:
            target = Path(folder) / "listas.pdf"
            with patch.object(ReportLayout, "paragraph", capture):
                write_rcd_pdf(blocks, "Proyecto", ReportSettings(), target)
            self.assertEqual(len(captured), 5)
            for index, paragraph in enumerate(captured):
                self.assertEqual(paragraph.style.alignment,
                                 TA_LEFT if index in (2, 4) else TA_JUSTIFY)
                self.assertEqual(paragraph.style.leftIndent - paragraph.style.bulletIndent, LIST_INDENT)
            target = Path(folder) / "listas.odt"
            write_rcd_odt(blocks, "Proyecto", ReportSettings(), target)
            with ZipFile(target) as archive:
                content = ET.fromstring(archive.read("content.xml"))
                styles = ET.fromstring(archive.read("styles.xml"))
            declared = {
                style.get(_tag("style:name")): style
                for root in (content, styles) for style in root.iter(_tag("style:style"))
            }
            paragraphs = [
                node for node in content.iter(_tag("text:p"))
                if node.find(_tag("text:tab")) is not None
                and node.text == "•"
            ]
            self.assertEqual(len(paragraphs), 5)
            for index, paragraph in enumerate(paragraphs):
                style = declared[paragraph.get(_tag("text:style-name"))]
                props = style.find(_tag("style:paragraph-properties"))
                self.assertEqual(props.get(_tag("fo:text-align")),
                                 "start" if index in (2, 4) else "justify")
                self.assertEqual(props.get(_tag("fo:text-indent")), f"-{LIST_INDENT}pt")
                self.assertEqual(len(paragraph.findall(_tag("text:tab"))), 1)
            self.assertIsNotNone(paragraphs[3].find(_tag("text:a")))

    def test_work_summary_justifies_text_and_preserves_hanging_bullets(self):
        """Conserva viñetas pegadas con tabulador sin duplicar la sangría del resumen."""
        from report_layout import ReportLayout
        from reportlab.lib.enums import TA_JUSTIFY
        from tools._library_markdown import text_blocks

        source = (
            "RESUMEN_INICIO La actuación contempla la mejora integral de la vía.\n"
            "•\tMEDIDA_UNO " + "TRAZADO_CONTINUO " * 18 + "\n"
            "•\tMEDIDA_DOS Adaptación del drenaje y de los servicios urbanos."
        )
        table = _Block("table", headers=("Aspecto", "Descripción"),
                       rows=(("Actuación", source),), project_summary=True)
        layout = ReportLayout()
        cell = _pdf_blue_table(table, layout)._cellvalues[1][1]
        self.assertEqual(len(cell), 3)
        self.assertTrue(all(paragraph.style.alignment == TA_JUSTIFY for paragraph in cell))
        for paragraph in cell[1:]:
            self.assertEqual(paragraph.bulletText, "•")
            self.assertEqual(paragraph.style.leftIndent - paragraph.style.bulletIndent, LIST_INDENT)
            self.assertNotIn("\t", paragraph.getPlainText())

        blocks = [(2, DESCRIPTION_HEADING), *text_blocks(source)]
        captured = []
        render = ReportLayout.paragraph

        def capture(layout, markup, style=None, **kwargs):
            """Observa los párrafos reales del PDF RCD antes de la paginación."""
            paragraph = render(layout, markup, style, **kwargs)
            if paragraph.getPlainText().startswith(("RESUMEN_INICIO", "MEDIDA_")):
                captured.append(paragraph)
            return paragraph

        with TemporaryDirectory() as folder:
            target = Path(folder) / "summary.pdf"
            with patch.object(ReportLayout, "paragraph", capture):
                write_rcd_pdf(blocks, "Proyecto", ReportSettings(), target)
            self.assertEqual(len(captured), 3)
            self.assertTrue(all(paragraph.style.alignment == TA_JUSTIFY for paragraph in captured))
            for kind in ("rcd", "safety"):
                target = Path(folder) / f"{kind}.odt"
                if kind == "rcd":
                    write_rcd_odt(blocks, "Proyecto", ReportSettings(), target)
                    list_style, text_style = "RcdJustifiedBullet1", "RcdBody"
                else:
                    content = _StudyContent("Seguridad", "Proyecto", "", "EUR", [table])
                    write_safety_odt(SimpleNamespace(report_settings=ReportSettings()), target, content)
                    list_style, text_style = "ESSProjectSummaryList", "ESSProjectSummaryText"
                with ZipFile(target) as archive:
                    roots = [ET.fromstring(archive.read(name)) for name in ("content.xml", "styles.xml")]
                declared = {
                    style.get(_tag("style:name")): style
                    for root in roots for style in root.iter(_tag("style:style"))
                }
                for name in (list_style, text_style):
                    props = declared[name].find(_tag("style:paragraph-properties"))
                    self.assertEqual(props.get(_tag("fo:text-align")), "justify")
                props = declared[list_style].find(_tag("style:paragraph-properties"))
                self.assertEqual(props.get(_tag("fo:margin-left")), f"{LIST_INDENT}pt")
                self.assertEqual(props.get(_tag("fo:text-indent")), f"-{LIST_INDENT}pt")
                paragraphs = roots[0].findall(
                    f".//{_tag('text:p')}[@{_tag('text:style-name')}='{list_style}']",
                )
                self.assertEqual(len(paragraphs), 2)
                for paragraph in paragraphs:
                    self.assertEqual(paragraph.text, "•")
                    self.assertEqual(len(paragraph.findall(_tag("text:tab"))), 1)
                    self.assertTrue(paragraph.find(_tag("text:tab")).tail.startswith("MEDIDA_"))
            soffice = shutil.which("soffice") or r"C:\Program Files\LibreOffice\program\soffice.com"
            if Path(soffice).is_file():
                root = Path(folder)
                subprocess.run(
                    [soffice, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                     "--headless", "--convert-to", "pdf", "--outdir", str(root), str(root / "rcd.odt")],
                    check=True, capture_output=True, timeout=90,
                )
                positions = {}
                continuations = []
                for page in PdfReader(root / "rcd.pdf").pages:
                    def collect(text, cm, tm, font, size):
                        """Mide la sangría del marcador, la primera línea y las continuaciones."""
                        if text.strip() == "•":
                            positions["bullet"] = tm[4]
                        if "MEDIDA_UNO" in text:
                            positions["text"] = tm[4]
                        if text.startswith("TRAZADO_CONTINUO"):
                            continuations.append(tm[4])
                    page.extract_text(visitor_text=collect)
                self.assertAlmostEqual(positions["text"] - positions["bullet"], LIST_INDENT, delta=0.15)
                self.assertTrue(continuations)
                for position in continuations:
                    self.assertAlmostEqual(position, positions["text"], delta=0.15)

    def test_caption_base_is_independent_of_body_and_reference_visibility(self):
        """Unifica captions propios y comunes sin anular los ajustes del proyecto."""
        from report_layout import ReportLayout
        from reportlab.lib.styles import ParagraphStyle

        with TemporaryDirectory() as folder:
            for overrides in ({}, {"bold": False}, {"font": "Times New Roman", "size": 11, "bold": False}):
                settings = ReportSettings(text_styles={
                    "body": {"font": "Courier New", "size": 14}, "caption": overrides,
                })
                layout = ReportLayout(text_styles=settings.text_styles)
                layout.table_references = False
                story = []
                layout.table_caption(story, "Tabla de prueba")
                custom = layout.text("Leyenda", ParagraphStyle(
                    "ESSCaption", fontName="Helvetica", fontSize=9, leading=12,
                ))
                for paragraph in (story[-1], custom):
                    self.assertEqual(paragraph.style.fontSize, overrides.get("size", 8))
                    self.assertEqual(
                        paragraph.style.fontName,
                        "Times-Roman" if "font" in overrides else
                        layout.bold_font if overrides.get("bold", True) else layout.regular_font,
                    )
                target = Path(folder) / "caption.odt"
                content = _StudyContent("Seguridad", "Proyecto", "", "EUR", [
                    _Block("caption", "Leyenda"),
                ])
                write_safety_odt(SimpleNamespace(report_settings=settings), target, content)
                with ZipFile(target) as archive:
                    roots = [ET.fromstring(archive.read(name)) for name in ("content.xml", "styles.xml")]
                style = next(
                    node for root in roots for node in root.iter(_tag("style:style"))
                    if node.get(_tag("style:name")) == "ESSCaption"
                )
                properties = style.find(_tag("style:text-properties"))
                self.assertIsNotNone(properties)
                self.assertEqual(properties.get(_tag("fo:font-family")), overrides.get("font", "Arial Narrow"))
                self.assertEqual(properties.get(_tag("fo:font-size")), f'{overrides.get("size", 8)}pt')
                self.assertEqual(
                    properties.get(_tag("fo:font-weight")), "bold" if overrides.get("bold", True) else "normal",
                )

    def test_rcd_header_and_section_inheritance_survives_writer(self):
        """Conserva la tipografía también ante estilos homónimos del maestro."""
        blocks = [
            (1, "SECCION_PRIMERA"), (0, "Texto inicial."),
            (1, "SECCION_SEGUNDA"),
            StudyTable("Cabeceras", ["HEADER_PLAIN", "HEADER_RIGHT", "HEADER_CENTER"],
                       [["BODY_PLAIN", "1", "B"]], [1, 1, 1],
                       right_columns=(1,), center_columns=(2,)),
        ]
        with TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "rcd.odt"
            write_rcd_odt(blocks, "Proyecto", ReportSettings(), target)
            with ZipFile(target) as archive:
                styles = ET.fromstring(archive.read("styles.xml"))
                content = ET.fromstring(archive.read("content.xml"))
            named = {
                style.get(_tag("style:name"))
                for style in styles.find(_tag("office:styles"))
                if style.tag == _tag("style:style")
            }
            declared = {
                style.get(_tag("style:name")): style
                for document in (styles, content) for style in document.iter(_tag("style:style"))
            }
            for name in ("RcdTableHeader", "RcdTableHeaderRight", "RcdTableHeaderCenter"):
                properties = declared[name].find(_tag("style:text-properties"))
                self.assertEqual(properties.get(_tag("fo:font-family")), "Arial Narrow")
            for name in ("RcdDocumentStart", "RcdTableHeaderCenter"):
                self.assertIn(declared[name].get(_tag("style:parent-style-name")), named)
            template = root / "plantilla.odt"
            report = _new_report("Maestro", "Proyecto", ReportSettings(text_styles={
                "table_header": {"font": "Times New Roman", "size": 12},
            }))
            report.paragraph("Portada")
            report.write(template)
            master = root / "maestro.odm"
            write_project_master(
                template, master, fields={}, index_entries=[], traceability=[],
                links=[Path(target.name)], folder=root,
                logo=Path(__file__).resolve().parents[1] / "iconos" / "Arranque.jpg",
            )
            with ZipFile(master) as archive:
                cached = ET.fromstring(archive.read("content.xml"))
            header_cell = next(style for style in cached.iter(_tag("style:style"))
                               if style.get(_tag("style:name")) == "RcdTableHeaderCell")
            self.assertEqual(
                header_cell.find(_tag("style:table-cell-properties")).get(_tag("fo:background-color")),
                TABLE_HEADER_BACKGROUND,
            )
            soffice = shutil.which("soffice") or r"C:\Program Files\LibreOffice\program\soffice.com"
            if not Path(soffice).is_file():
                return
            subprocess.run(
                [soffice, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(root),
                 str(target), str(master)],
                check=True, capture_output=True, timeout=90,
            )
            rendered = []
            for document in (target, master):
                runs = {}
                for page in PdfReader(document.with_suffix(".pdf")).pages:
                    def collect(text, cm, tm, font, size):
                        """Recoge la fuente efectiva sin el prefijo del subconjunto PDF."""
                        for marker in ("SECCION_PRIMERA", "SECCION_SEGUNDA", "BODY_PLAIN",
                                       "HEADER_PLAIN", "HEADER_RIGHT", "HEADER_CENTER"):
                            if marker in text:
                                runs[marker] = (font["/BaseFont"].split("+")[-1], size)
                    page.extract_text(visitor_text=collect)
                rendered.append(runs)
                with self.subTest(document=document.name):
                    for marker in ("HEADER_PLAIN", "HEADER_RIGHT", "HEADER_CENTER"):
                        self.assertIn("Bold", runs[marker][0])
                        self.assertEqual(runs[marker][0].removesuffix("-Bold"), runs["BODY_PLAIN"][0])
                        self.assertAlmostEqual(runs[marker][1], 8, places=1)
                    self.assertAlmostEqual(runs["BODY_PLAIN"][1], 8, places=1)
            self.assertEqual(rendered[0]["SECCION_PRIMERA"], rendered[0]["SECCION_SEGUNDA"])
            for marker in ("HEADER_PLAIN", "HEADER_RIGHT", "HEADER_CENTER"):
                self.assertEqual(rendered[0][marker], rendered[1][marker])

    def test_rcd_header_typography_respects_project_overrides(self):
        """Los valores independientes del maestro siguen siendo personalizables."""
        settings = ReportSettings(text_styles={
            "table_header": {"font": "Courier New", "size": 11, "bold": False, "italic": True},
        })
        blocks = [StudyTable("Prueba", ["Dato", "Valor", "Ud."], [["A", "1", "m"]],
                             [1, 1, 1], right_columns=(1,), center_columns=(2,))]
        with TemporaryDirectory() as folder:
            target = Path(folder) / "rcd.odt"
            write_rcd_odt(blocks, "Proyecto", settings, target)
            with ZipFile(target) as archive:
                root = ET.fromstring(archive.read("content.xml"))
            styles = {style.get(_tag("style:name")): style
                      for style in root.iter(_tag("style:style"))}
            for name in ("RcdTableHeader", "RcdTableHeaderRight", "RcdTableHeaderCenter"):
                properties = styles[name].find(_tag("style:text-properties"))
                self.assertEqual(properties.get(_tag("fo:font-family")), "Courier New")
                self.assertEqual(properties.get(_tag("fo:font-size")), "11pt")
                self.assertEqual(properties.get(_tag("fo:font-weight")), "normal")
                self.assertEqual(properties.get(_tag("fo:font-style")), "italic")
                self.assertEqual(properties.get(_tag("fo:color")), "#FFFFFF")

    def test_safety_contents_remain_scoped_to_each_document(self):
        """Mantiene separados los indices de memoria y pliego tras unificar su estilo."""
        app = SimpleNamespace(report_settings=ReportSettings())
        content = _StudyContent("Seguridad y salud", "Proyecto", "", "EUR", [
            _Block("document", "Memoria"), _Block("heading", "MEMORIA_UNICA", 1),
            _Block("paragraph", "CUERPOMEMORIA"),
            _Block("document", "Pliego"), _Block("heading", "PLIEGO_UNICO", 1),
            _Block("paragraph", "CUERPOPLIEGO"),
        ])
        with TemporaryDirectory() as folder:
            for output in ("odt", "pdf"):
                target = Path(folder) / f"safety.{output}"
                with patch("tools.safety_health.document._build_study_content", return_value=content):
                    write_study_report(app, target)
                if output == "odt":
                    with ZipFile(target) as archive:
                        tree = ET.fromstring(archive.read("content.xml"))
                    indices = list(tree.iter(_tag("text:table-of-content")))
                    self.assertEqual(len(indices), 2)
                    contents = [" ".join(index.itertext()) for index in indices]
                    for index, toc in enumerate(indices):
                        styles = list(toc.iter(_tag("text:index-source-style")))
                        self.assertTrue(all(
                            style.get(_tag("text:style-name")).startswith(f"ESSDocument{index}Heading")
                            for style in styles
                        ))
                else:
                    contents = [
                        page.extract_text() for page in PdfReader(target).pages
                        if "ÍNDICE" in page.extract_text()
                    ]
                    self.assertEqual(len(contents), 2)
                for index, label in enumerate(("MEMORIA_UNICA", "PLIEGO_UNICO")):
                    self.assertIn(label, contents[index])
                    self.assertNotIn(label, contents[1 - index])

    def test_quality_contents_are_navigable_and_use_real_pages(self):
        """Añade un indice multipagina con destinos reales, no numeros provisionales."""
        blocks = []
        for index in range(60):
            blocks.extend([
                (1, f"APARTADO {index:03d} & <control>"),
                (0, f"Contenido técnico {index:03d}. " + "Texto normal. " * 35),
            ])
        with TemporaryDirectory() as folder:
            target = Path(folder) / "quality.pdf"
            write_quality_pdf(target, "Proyecto", ReportSettings(), blocks, {})
            pages = PdfReader(target).pages
            texts = [" ".join(page.extract_text().split()) for page in pages]
            body_start = next(index for index, text in enumerate(texts) if "Contenido técnico" in text)
            self.assertGreater(body_start, 1)
            contents = " ".join(texts[:body_start])
            self.assertIn("ÍNDICE", contents)
            for index in range(60):
                title = f"APARTADO {index:03d} & <control>"
                number = next(
                    page + 1 for page in range(body_start, len(texts)) if title in texts[page]
                )
                self.assertRegex(contents, rf"\b{number}\s+{re.escape(title)}")
            self.assertTrue(all(page.get("/Annots") for page in pages[:body_start]))

    def test_contents_styles_and_depth_are_shared_in_odt(self):
        """Unifica niveles sin perder tipografia ni los destinos tras filtrar detalles."""
        settings = ReportSettings(text_styles={"index": {"font": "Courier New", "size": 12}})
        blocks = [(1, "Primera"), (3, "Detalle excluido"), (2, "Segunda")]
        with TemporaryDirectory() as folder:
            for writer in (write_quality_odt, write_rcd_odt):
                target = Path(folder) / "contents.odt"
                if writer is write_quality_odt:
                    writer(target, "Proyecto", settings, blocks, {})
                else:
                    writer(blocks, "Proyecto", settings, target)
                with ZipFile(target) as archive:
                    content = ET.fromstring(archive.read("content.xml"))
                    styles = ET.fromstring(archive.read("styles.xml"))
                toc = content.find(".//" + _tag("text:table-of-content"))
                references = list(toc.iter(_tag("text:bookmark-ref")))
                bookmarks = {
                    node.get(_tag("text:name"))
                    for node in content.iter(_tag("text:bookmark"))
                }
                for ref, suffix in zip(references, ("_PPTPHeading0", "_PPTPHeading2")):
                    name = ref.get(_tag("text:ref-name"))
                    self.assertTrue(name.endswith(suffix))
                    self.assertIn(name, bookmarks)
                self.assertEqual(len(references), 2)
                for level in (1, 2):
                    style = next(
                        node for root in (content, styles) for node in root.iter(_tag("style:style"))
                        if node.get(_tag("style:name")) == f"PPTPContents{level}"
                    )
                    props = style.find(_tag("style:text-properties"))
                    self.assertEqual(props.get(_tag("fo:font-family")), "Courier New")
                    self.assertEqual(props.get(_tag("fo:font-size")), "12pt")
                    tab = style.find(".//" + _tag("style:tab-stop"))
                    self.assertEqual(tab.get(_tag("style:type")), "right")
                    self.assertEqual(tab.get(_tag("style:leader-char")), ".")
            write_quality_odt(target, "Proyecto", settings, [(0, "Texto sin secciones")], {})
            with ZipFile(target) as archive:
                content = ET.fromstring(archive.read("content.xml"))
            self.assertIsNone(content.find(".//" + _tag("text:table-of-content")))

    def test_quality_and_rcd_share_project_narrative_styles(self):
        """Aplica plantilla y tipografía del proyecto a texto, títulos y listas."""
        settings = ReportSettings(text_styles={
            "body": {"font": "Times New Roman", "size": 14},
            "heading1": {"font": "Arial", "size": 18},
            "list": {"font": "Courier New", "size": 13},
        })
        template = _new_report("PRUEBA", "Proyecto", settings)
        template_styles = template_paragraph_styles(template.template_header)
        blocks = [(1, "Encabezado"), (0, "Texto normal"), (-1, "• Viñeta")]
        with TemporaryDirectory() as folder:
            for prefix in ("Quality", "Rcd"):
                with self.subTest(report=prefix):
                    target = Path(folder) / f"{prefix}.odt"
                    if prefix == "Quality":
                        write_quality_odt(target, "Proyecto", settings, blocks, {})
                    else:
                        write_rcd_odt(blocks, "Proyecto", settings, target)
                    with ZipFile(target) as archive:
                        content = ET.fromstring(archive.read("content.xml"))
                        style_root = ET.fromstring(archive.read("styles.xml"))
                    styles = {
                        style.get(_tag("style:name")): style
                        for root in (content, style_root) for style in root.iter(_tag("style:style"))
                    }
                    for suffix, role in (("Body", "body"), ("Heading1", "heading1"),
                                         ("Bullet1", "list")):
                        props = styles[prefix + suffix].find(_tag("style:text-properties"))
                        self.assertEqual(props.get(_tag("fo:font-family")), settings.text_styles[role]["font"])
                        self.assertEqual(props.get(_tag("fo:font-size")), f'{settings.text_styles[role]["size"]}pt')
                    self.assertEqual(styles[prefix + "Body"].get(_tag("style:parent-style-name")),
                                     template_styles["body"])
                    self.assertEqual(styles[prefix + "Heading1"].get(_tag("style:parent-style-name")),
                                     template_styles["heading1"])
                    self.assertEqual(styles[prefix + "Bullet1"].get(_tag("style:parent-style-name")),
                                     prefix + "Body")
                    props = styles[prefix + "Bullet1"].find(_tag("style:paragraph-properties"))
                    self.assertEqual(props.get(_tag("fo:margin-left")), f"{LIST_INDENT}pt")
                    self.assertEqual(props.get(_tag("fo:text-indent")), f"-{LIST_INDENT}pt")
                    paragraphs = {''.join(p.itertext()): p.get(_tag("text:style-name"))
                                  for p in content.iter(_tag("text:p"))}
                    self.assertEqual(paragraphs["Texto normal"], prefix + "Body")

    def test_planning_description_uses_project_body_style(self):
        """Aplica la configuración al texto sin convertir el diagrama en imagen."""
        settings = ReportSettings(text_styles={"body": {"font": "Arial", "size": 14}})
        activity = PlanActivity(
            id=1, chapter="01#", chapter_name="Preparación", code="P1", summary="Actividad",
            unit="ud", quantity=3, amount=300, rendimiento=1, duration=3,
            start_offset=0, finish_offset=3, start_date=date(2026, 11, 9),
            end_date=date(2026, 11, 11),
        )
        params = PlanParams(start=date(2026, 11, 9), scale=7, plan="Completo")
        with TemporaryDirectory() as folder:
            target = Path(folder) / "planning.odt"
            write_planning_report([activity], params, target, settings, "Proyecto", str)
            with ZipFile(target) as archive:
                content = ET.fromstring(archive.read("content.xml"))
        template = _new_report("PRUEBA", "Proyecto", settings)
        style = next(style for style in content.iter(_tag("style:style"))
                     if style.get(_tag("style:name")) == "PlanningBody")
        self.assertEqual(style.get(_tag("style:parent-style-name")),
                         template_paragraph_styles(template.template_header)["body"])
        text = style.find(_tag("style:text-properties"))
        self.assertEqual(text.get(_tag("fo:font-family")), "Arial")
        self.assertEqual(text.get(_tag("fo:font-size")), "14pt")
        self.assertTrue(any(p.get(_tag("text:style-name")) == "PlanningBody"
                            for p in content.iter(_tag("text:p"))))
        self.assertTrue(any(content.iter(_tag("table:table"))))
        self.assertFalse(any(content.iter(_tag("draw:image"))))

    def test_safety_uses_shared_narrative_styles(self):
        """Conserva estilos editables y configuración de títulos, texto y leyendas."""
        settings = ReportSettings(text_styles={
            "body": {"font": "Arial", "size": 14},
            "heading1": {"font": "Courier New", "size": 18},
            "caption": {"font": "Times New Roman", "size": 9},
        })
        content = _StudyContent(
            "Seguridad y salud", "Proyecto", "", "EUR",
            [_Block("heading", "Encabezado", 1), _Block("paragraph", "Texto normal"),
             _Block("caption", "Leyenda")],
        )
        with TemporaryDirectory() as folder:
            target = Path(folder) / "safety.odt"
            write_safety_odt(SimpleNamespace(report_settings=settings), target, content)
            with ZipFile(target) as archive:
                roots = [ET.fromstring(archive.read(name)) for name in ("content.xml", "styles.xml")]
        styles = {style.get(_tag("style:name")): style for root in roots
                  for style in root.iter(_tag("style:style"))}
        for name, role in (("ESSBody", "body"), ("ESSHeading1", "heading1"),
                           ("ESSCaption", "caption")):
            with self.subTest(style=name):
                props = styles[name].find(_tag("style:text-properties"))
                self.assertEqual(props.get(_tag("fo:font-family")), settings.text_styles[role]["font"])
                self.assertEqual(props.get(_tag("fo:font-size")), f'{settings.text_styles[role]["size"]}pt')
        self.assertEqual(styles["ESSCaption"].get(_tag("style:parent-style-name")), "ESSBody")

    def test_safety_public_export_uses_document_module(self):
        """Genera ambos formatos desde el punto de entrada público."""
        app = SimpleNamespace(report_settings=ReportSettings())
        with TemporaryDirectory() as folder:
            for output in ("odt", "pdf"):
                with self.subTest(format=output):
                    content = _StudyContent(
                        "Seguridad y salud", "Proyecto", "", "EUR",
                        [_Block("heading", "Encabezado", 1), _Block("paragraph", "Texto de prueba")],
                        ("Aviso",),
                    )
                    target = Path(folder) / f"safety.{output}"
                    with patch("tools.safety_health.document._build_study_content", return_value=content):
                        self.assertEqual(write_study_report(app, target), ("Aviso",))
                    if output == "odt":
                        with ZipFile(target) as archive:
                            text = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                    else:
                        text = " ".join(page.extract_text() for page in PdfReader(target).pages)
                    self.assertIn("Texto de prueba", text)
