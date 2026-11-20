"""Comprobaciones comunes de tipografía de los informes ODT."""
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_reports import ReportSettings
from odt_reports import _new_report, _tag
from tools._shared.document_layout import LIST_INDENT, template_paragraph_styles
from tools._shared.scheduling import PlanActivity, PlanParams
from tools.planning.document import write_planning_report
from tools.quality_assurance.document import _write_odt as write_quality_odt
from tools.rcd.document import _write_odt as write_rcd_odt
from tools.safety_health import write_study_report
from tools.safety_health.document import _Block, _StudyContent, _write_odt as write_safety_odt


class NarrativeLayoutTests(unittest.TestCase):
    """Verifica configuración y plantilla en varios generadores."""

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
                    styles = {
                        style.get(_tag("style:name")): style
                        for style in content.iter(_tag("style:style"))
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
