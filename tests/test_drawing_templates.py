"""Reconocimiento de campos SVG compartidos por los cajetines de planos."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools._shared.drawing_templates import read_title_block_fields


class DrawingTemplateTests(unittest.TestCase):
    """Comprueba IDs estándar, texto editable y conservación del archivo fuente."""

    def test_reads_multiline_fields_by_id_without_changing_original(self):
        """Lee el ID SVG, no la etiqueta del editor ni los rótulos fijos."""
        source = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape">'
            '<text id="rotulo">PROYECTO:</text>'
            '<text id="titulo_proyecto"><tspan>Primera línea </tspan><tspan>segunda línea</tspan></text>'
            '<text id="otro" inkscape:label="autor">No es un campo</text>'
            '<text id="numero_plano">01</text></svg>'
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cajetin.svg"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(read_title_block_fields(path), {
                "titulo_proyecto": "Primera línea segunda línea", "numero_plano": "01",
            })
            self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_rejects_duplicate_ids_noneditable_fields_and_non_svg(self):
        """No reconoce como dinámico un campo ambiguo, trazado o de otro formato."""
        for source in (
            '<svg xmlns="http://www.w3.org/2000/svg"><text id="autor"/><text id="autor"/></svg>',
            '<svg xmlns="http://www.w3.org/2000/svg"><path id="autor"/></svg>',
            '<document><text id="autor"/></document>',
        ):
            with self.subTest(source=source), TemporaryDirectory() as directory:
                path = Path(directory) / "cajetin.svg"
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_title_block_fields(path)
