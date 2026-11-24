"""Comprobaciones de los planos de Seguridad y Salud."""
from __future__ import annotations

from base64 import b64encode
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from PIL import Image
from PySide6.QtCore import QByteArray, qInstallMessageHandler
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication
from pypdf import PdfReader

from budget_core import ProjectItem
from budget_reports import ReportSettings
from tools.safety_health import restore_project_state, serialize_state
from tools.safety_health.document import _Block, _StudyContent, _write_odt, _write_pdf
from tools.safety_health.drawings import (
    LIBRARY, SHEETS, _filled_title_block, add_user_sheet, available_sheets, read_sheet,
    render_drawings, restore_drawings,
)


class SafetyDrawingsTests(unittest.TestCase):
    """Valida propuestas, conservación de decisiones y documentos generados."""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.library = TemporaryDirectory()
        self.addCleanup(self.library.cleanup)
        patcher = patch(
            "tools.safety_health.drawings.user_library_directory",
            return_value=Path(self.library.name) / "fichas",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_app(self, directory: str):
        """Crea un proyecto y cajetines mínimos independientes de los del usuario."""
        for paper, size in (("A4", "210 297"), ("A3", "420 297")):
            (Path(directory) / f"cajetin_{paper}.svg").write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size}">'
                '<text id="titulo_proyecto" x="10" y="280">Anterior</text>'
                '<text id="titulo_plano"><tspan x="10" y="285"><tspan>Anterior</tspan></tspan></text>'
                '<text id="numero_plano" x="180" y="280">0</text>'
                '</svg>', encoding="utf-8",
            )
        return SimpleNamespace(
            report_settings=ReportSettings(drawing_title_block_path=directory),
            budget_items=[
                ProjectItem("Z", "01", "m", "Excavación de zanja", measurement=1),
                ProjectItem("E", "02", "ud", "Instalación eléctrica", measurement=1),
            ],
            chapter_order=["01", "02"], chapter_titles={"01": "Tierras", "02": "Electricidad"},
            safety_health_state={"chapter_selection": {"01": True, "02": False}},
            _chapter_title=lambda code: {"01": "Tierras", "02": "Electricidad"}[code],
            _project_title=lambda: "Proyecto de prueba",
        )

    def test_saved_builtin_and_custom_selections_roundtrip_without_library(self):
        code = "CUSTOM-" + "a" * 64
        decisions = {"paper": "A3", "selected": {"ZAN13": False, "SEN09": True, code: True}}
        state = serialize_state([], Decimal("2"), drawings=decisions)
        self.assertEqual(restore_project_state(state)["drawings"], decisions)
        for value in (
            {"paper": "A2", "selected": {}},
            {"paper": "A4", "selected": {"ZAN13": "yes"}},
            {"paper": "A4", "selected": {"CUSTOM-../../file": True}},
        ):
            with self.assertRaises(ValueError):
                restore_drawings(value)

    def test_added_sheet_is_copied_reused_and_exported(self):
        with TemporaryDirectory() as directory:
            app = self.make_app(directory)
            path = Path(directory) / "Protección de prueba.svg"
            original = (
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 200">'
                b'<rect width="100" height="200" fill="red"/></svg>'
            )
            path.write_bytes(original)
            sheet = add_user_sheet(path)
            self.assertEqual(sheet.title, "Protección de prueba")
            self.assertEqual(read_sheet(sheet), original)
            with self.assertRaisesRegex(ValueError, "ya está"):
                add_user_sheet(path)
            path.unlink()
            self.assertIn(sheet, available_sheets())
            state = {"paper": "A4", "selected": {sheet.code: True}}
            self.assertEqual(len(render_drawings(app, state)), 1)
            sheet.path.unlink()
            self.assertEqual(restore_drawings(state), state)
            with self.assertRaisesRegex(ValueError, "Faltan fichas"):
                render_drawings(app, state)

    def test_invalid_or_nonportable_svg_is_not_added(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ficha.svg"
            for data in (
                b"not svg",
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                b'<image href="otra.png"/></svg>',
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                b'<style>@import "externo.css";</style></svg>',
                b'<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>',
            ):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    add_user_sheet(path)
            self.assertEqual(available_sheets(), list(SHEETS))

    def test_packaged_library_contains_each_catalogued_original(self):
        with ZipFile(LIBRARY) as archive:
            self.assertEqual(set(archive.namelist()), {sheet.filename for sheet in SHEETS})

    def test_title_block_updates_nested_fields_without_modifying_original(self):
        with TemporaryDirectory() as directory:
            self.make_app(directory)
            path = Path(directory) / "cajetin_A4.svg"
            original = path.read_bytes()
            result = ET.fromstring(_filled_title_block(path, {
                "titulo_proyecto": "Proyecto nuevo", "titulo_plano": "Plano nuevo",
                "numero_plano": "4",
            }))
            fields = {e.get("id"): e for e in result.iter() if e.get("id")}
            self.assertEqual("".join(fields["titulo_plano"].itertext()), "Plano nuevo")
            self.assertEqual(fields["titulo_plano"].get("x"), "10")
            self.assertEqual("".join(fields["titulo_proyecto"].itertext()), "Proyecto nuevo")
            self.assertEqual(path.read_bytes(), original)

    def test_title_block_renders_nested_text_and_embedded_image_without_qt_warnings(self):
        image = BytesIO()
        Image.new("RGB", (2, 2), "red").save(image, format="PNG")
        encoded = b64encode(image.getvalue()).decode("ascii")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cajetin.svg"
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" '
                'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">'
                f'<image x="0" y="0" width="20" height="20" xlink:href="data:image/png;base64,{encoded}"/>'
                '<text id="titulo_proyecto"><tspan id="outer" x="30" y="40">'
                '<tspan id="inner" style="font-size:12px;fill:blue">Anterior</tspan>'
                '</tspan></text></svg>', encoding="utf-8",
            )
            result = _filled_title_block(path, {"titulo_proyecto": "Proyecto"})
            root = ET.fromstring(result)
            span = next(node for node in root.iter() if node.get("id") == "outer")
            self.assertEqual(span.get("x"), "30")
            self.assertEqual(span.get("y"), "40")
            self.assertEqual(span.get("style"), "font-size:12px;fill:blue")
            self.assertEqual(span.text, "Proyecto")
            messages = []
            previous = qInstallMessageHandler(
                lambda _kind, context, message: messages.append(message)
                if context.category == "qt.svg" else None
            )
            try:
                renderer = QSvgRenderer(QByteArray(result))
                canvas = QImage(100, 100, QImage.Format.Format_RGB32)
                canvas.fill(0xFFFFFFFF)
                painter = QPainter(canvas)
                renderer.render(painter)
                painter.end()
            finally:
                qInstallMessageHandler(previous)
            self.assertTrue(renderer.isValid())
            self.assertEqual(messages, [])
            self.assertEqual(canvas.pixelColor(5, 5).red(), 255)
            self.assertLess(canvas.pixelColor(5, 5).green(), 10)

    def test_title_block_preserves_referenced_or_styled_nested_spans(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cajetin.svg"
            for extra in (
                '<use href="#inner"/>',
                '<style>text tspan tspan { fill: blue; }</style>',
            ):
                path.write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                    + extra + '<text id="titulo_proyecto"><tspan x="30" y="40">'
                    '<tspan id="inner">Anterior</tspan></tspan></text></svg>',
                    encoding="utf-8",
                )
                root = ET.fromstring(_filled_title_block(path, {"titulo_proyecto": "Proyecto"}))
                inner = next(node for node in root.iter() if node.get("id") == "inner")
                self.assertEqual(inner.text, "Proyecto")

    def test_pdf_and_odt_embed_one_a4_or_two_a3_sheets_per_page(self):
        with TemporaryDirectory() as directory:
            app = self.make_app(directory)
            content = _StudyContent(
                "Estudio", "Proyecto", "Seguridad", "EUR",
                [_Block("heading", "Memoria", 1), _Block("paragraph", "Texto")],
            )
            for paper, count in (("A4", 3), ("A3", 2)):
                with self.subTest(paper=paper):
                    state = {"paper": paper, "selected": {
                        sheet.code: index < 3 for index, sheet in enumerate(SHEETS)
                    }}
                    images = render_drawings(app, state)
                    self.assertEqual(len(images), count)
                    self.assertTrue(all(image.startswith(b"\x89PNG\r\n\x1a\n") for image in images))
                    pdf, odt = Path(directory) / f"planos-{paper}.pdf", Path(directory) / f"planos-{paper}.odt"
                    _write_pdf(app, pdf, content, images, paper)
                    _write_odt(app, odt, content, images, paper)
                    pages = PdfReader(pdf).pages
                    for page in pages[-count:]:
                        self.assertAlmostEqual(float(page.mediabox.height), 842, delta=1)
                        self.assertAlmostEqual(
                            float(page.mediabox.width), 595 if paper == "A4" else 1191, delta=1,
                        )
                    with ZipFile(odt) as archive:
                        names = {name for name in archive.namelist() if name.startswith("Pictures/ESSDrawing")}
                        self.assertEqual(len(names), count)
                        styles = archive.read("styles.xml")
                        self.assertIn(b"ESSDrawingLayout", styles)
                        self.assertIn(b"42cm" if paper == "A3" else b"21cm", styles)

    def test_selected_drawings_require_a_configured_cajetin(self):
        with TemporaryDirectory() as directory:
            app = self.make_app(directory)
            app.report_settings.drawing_title_block_path = ""
            with self.assertRaisesRegex(ValueError, "cajetines"):
                render_drawings(app, {"paper": "A4", "selected": {"SEN01": True}})
            self.assertEqual(render_drawings(app, {"paper": "A4", "selected": {}}), [])
