"""Reconocimiento de campos SVG compartidos por los cajetines de planos."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools._shared.drawing_templates import find_title_block, read_title_block_fields


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

    def test_reads_two_independent_authorship_blocks(self):
        """Reconoce ambos bloques de autoría sin repetir ni confundir sus IDs."""
        source = (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<text id="autor">Primera firma</text><text id="cargo">Primer cargo</text>'
            '<text id="autor_2">Segunda firma</text><text id="cargo_2">Segundo cargo</text></svg>'
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cajetin_A3.svg"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(read_title_block_fields(path), {
                "autor": "Primera firma", "cargo": "Primer cargo",
                "autor_2": "Segunda firma", "cargo_2": "Segundo cargo",
            })

    def test_selects_each_format_by_name_without_confusing_a40_or_non_svg(self):
        """Distingue ambos formatos y admite rutas antiguas a un archivo de la carpeta."""
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            a4 = folder / "CajetinA4.SVG"
            a3 = folder / "marco_a3_vertical.svg"
            for path in (a4, a3, folder / "cajetin_A40.svg", folder / "cajetin_A3.dxf"):
                path.write_text("<svg/>", encoding="utf-8")
            (folder / "otro_A4.svg").mkdir()
            self.assertEqual(find_title_block(folder, "a4"), a4)
            self.assertEqual(find_title_block(folder, " A3 "), a3)
            self.assertEqual(find_title_block(a4, "A3"), a3)

    def test_missing_ambiguous_and_unsupported_formats_are_explicit_errors(self):
        """No usa un formato alternativo ni el primer archivo de varios candidatos."""
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "cajetin_A3.svg").write_text("<svg/>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No se encontró"):
                find_title_block(folder, "A4")
            for name in ("cajetin_A4.svg", "otro_A4.svg"):
                (folder / name).write_text("<svg/>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "varios cajetines"):
                find_title_block(folder, "A4")
            with self.assertRaisesRegex(ValueError, "no admitido"):
                find_title_block(folder, "A2")
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "cajetin_A4_A3.svg").write_text("<svg/>", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contiene A4 y A3"):
                find_title_block(folder, "A3")

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
