"""Tabla de fichas, estilo de filas y enlaces reales en los documentos PPTP."""
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
from tools._library_history import read_revisions
from tools.pptp import DOCS_ROOT, LIBRARY_PATH
from tools.pptp.document import Selection, VARIABLES, compose, write_document
from tools.pptp.library import ZipLibrary
from tools.pptp.sheet_index import (
    ALTERNATE_BACKGROUND, HEADER_BACKGROUND, HEADERS, INDEX_MARKER, INDEX_TOKEN,
    SheetIndexRow, extract_index, markdown_index,
)


class PptpSheetIndexTests(unittest.TestCase):
    def setUp(self):
        """Crea capítulos y fichas sintéticos sin depender de su contenido distribuido."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for index in range(1, 10):
            source = f"# {index} Seccion general {index}\n\nTexto general.\n"
            if index == 9:
                source += "\nRelacion de fichas por capitulo.\n\nTabla 9.1. Fichas del proyecto\n\n" + INDEX_MARKER
            (self.root / f"{index:02}_Base.md").write_text(source, encoding="utf-8")
        self.paths = [
            "UNI/01_DEMOLICIONES/01RPN_PELDANOS/01RPN-Piedra.md",
            "UNI/15_URBANIZACION/15PAA_PAVIMENTOS/15PAA-Pavimento.md",
            "UNI/02_TERRENOS/02AAA_TIERRAS/02AAA-Tierra.md",
        ]
        for index, relative in enumerate(self.paths):
            path = self.root.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"# Ficha sintetica {index}\n\n"
                + (f"Prescripcion tecnica de la ficha {index}. " * 15 + "\n\n") * 7,
                encoding="utf-8",
            )
        self.state = {
            "variables": {key: "Valor" for key in VARIABLES},
            "selections": [
                asdict(Selection(3, "P3", "CAP-B#", "Repetida", self.paths[0])),
                asdict(Selection(1, "P1", "CAP-A#", "Manual", self.paths[1], automatic=False)),
                asdict(Selection(0, "P0", "CAP-A#", "Primera", self.paths[0])),
                asdict(Selection(2, "P2", "CAP-B#", "Segunda", self.paths[2])),
                asdict(Selection(4, "P4", "CAP-C#", "Excluida", self.paths[1], selected=False)),
            ],
            "regulations": {},
        }
        self.titles = {"CAP-A#": "Capitulo del presupuesto A", "CAP-B#": "Capitulo del presupuesto B"}

    def source(self):
        """Compone la tabla y los cuerpos de ficha con la misma ruta que el asistente."""
        return compose(self.root, self.state, [], budget_chapter_titles=self.titles)

    def test_composition_matches_unique_sheet_headings_and_keeps_budget_groups(self):
        """Vincula cada fila al encabezado correcto sin publicar marcadores ni duplicados."""
        source = self.source()
        stripped, rows = extract_index(source)
        self.assertIn(INDEX_TOKEN, stripped)
        self.assertNotIn(INDEX_MARKER, source)
        self.assertNotIn(INDEX_TOKEN, source)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row.chapter for row in rows], [
            "CAP-A — CAPITULO DEL PRESUPUESTO A",
            "CAP-A — CAPITULO DEL PRESUPUESTO A",
            "CAP-B — CAPITULO DEL PRESUPUESTO B",
        ])
        self.assertEqual([row.sheet for row in rows], [
            "FICHA SINTETICA 0", "FICHA SINTETICA 1", "FICHA SINTETICA 2",
        ])
        headings = re.findall(r"^#{1,6}\s+(.+)$", source, flags=re.MULTILINE)
        for row in rows:
            self.assertTrue(headings[int(row.bookmark.removeprefix("PPTPHeading"))].endswith(" " + row.sheet))
        table = source.split("| --- | --- |", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(len(table.strip().splitlines()), 2)
        self.assertIn("<br/>", table)
        self.assertNotIn("9.1.1", table)
        self.assertLess(source.index("Tabla 9.1."), source.index("| " + HEADERS[0]))
        self.assertLess(source.index("| " + HEADERS[0]), source.index("## 9.1 "))
        self.assertNotIn("CAP-C", source)

    def test_numeric_budget_chapter_is_not_replaced_by_pptp_section_number(self):
        """La tabla identifica CAP. 01 aunque su sección del documento sea 9.1."""
        for row in self.state["selections"]:
            if row["chapter"] == "CAP-A#":
                row["chapter"] = "01#"
        source = compose(
            self.root, self.state, [],
            budget_chapter_titles={"01#": "Demolicion y actuaciones previas", **self.titles},
        )
        _, rows = extract_index(source)
        self.assertEqual(rows[0].chapter, "CAP. 01 — DEMOLICION Y ACTUACIONES PREVIAS")
        self.assertIn("## 9.1 DEMOLICION Y ACTUACIONES PREVIAS", source)

    def test_table_round_trip_preserves_special_characters(self):
        """Los títulos no rompen columnas ni se interpretan como negritas o etiquetas."""
        rows = [SheetIndexRow("A | B <C> & D", "Ficha [literal] <br/> **texto** &gt;", "PPTPHeading10")]
        _, restored = extract_index(markdown_index(rows))
        self.assertEqual(restored, rows)

    def test_odt_has_one_row_per_chapter_and_linked_names_in_same_cell(self):
        """Comprueba estilos explícitos, enlaces y destinos en el XML ODF persistido."""
        target = self.root / "PPTP.odt"
        write_document(self.source(), target, self.state["variables"], ReportSettings(output_format="odt"))
        with ZipFile(target) as archive:
            content = ET.fromstring(archive.read("content.xml"))
            named_styles = ET.fromstring(archive.read("styles.xml"))
        table = next(
            table for table in content.iter(_tag("table:table"))
            if table.get(_tag("table:name")) == "PPTPSheetIndex"
        )
        header = table.find(_tag("table:table-header-rows"))
        self.assertEqual(["".join(cell.itertext()) for cell in header.iter(_tag("table:table-cell"))], list(HEADERS))
        styles = {
            style.get(_tag("style:name")): style
            for root in (content, named_styles)
            for style in root.iter(_tag("style:style"))
        }
        self.assertEqual(
            styles["PPTPIndexHeaderCell"].find(_tag("style:table-cell-properties")).get(_tag("fo:background-color")),
            HEADER_BACKGROUND,
        )
        self.assertEqual(
            styles["PPTPIndexHeader"].find(_tag("style:text-properties")).get(_tag("fo:font-weight")), "bold",
        )
        self.assertEqual(
            styles["PPTPIndexText"].find(_tag("style:text-properties")).get(_tag("fo:font-weight")), "normal",
        )
        bookmarks = {node.get(_tag("text:name")) for node in content.iter(_tag("text:bookmark"))}
        body_rows = table.findall(_tag("table:table-row"))
        self.assertEqual(len(body_rows), 2)
        _, expected = extract_index(self.source())
        actual = []
        for index, row in enumerate(body_rows):
            cells = row.findall(_tag("table:table-cell"))
            self.assertEqual(len(cells), 2)
            for cell in cells:
                style = styles[cell.get(_tag("table:style-name"))]
                self.assertEqual(
                    style.find(_tag("style:table-cell-properties")).get(_tag("fo:background-color")),
                    ALTERNATE_BACKGROUND if index % 2 == 0 else "#FFFFFF",
                )
            paragraphs = cells[1].findall(_tag("text:p"))
            self.assertEqual(len(paragraphs), 2 if index == 0 else 1)
            for paragraph in paragraphs:
                self.assertEqual(paragraph.text, "• ")
                self.assertEqual(paragraph.get(_tag("text:style-name")), "PPTPIndexBullet")
                link = paragraph.find(_tag("text:a"))
                bookmark = link.get(_tag("xlink:href")).removeprefix("#")
                self.assertIn(bookmark, bookmarks)
                actual.append(SheetIndexRow("".join(cells[0].itertext()), link.text, bookmark))
        self.assertEqual(actual, expected)
        self.assertEqual(list(table.iter(_tag("text:bookmark-ref"))), [])
        text = "".join(content.itertext())
        self.assertIn("Tabla 9.1. Fichas del proyecto", text)
        self.assertNotIn(INDEX_TOKEN, text)

    def test_pdf_names_link_to_actual_sheet_pages_without_section_numbers(self):
        """Cada nombre enlaza con su ficha aunque no figure en el índice general."""
        target = self.root / "PPTP.pdf"
        source = self.source()
        _, rows = extract_index(source)
        write_document(
            source, target, self.state["variables"], ReportSettings(output_format="pdf"), toc_depth=1,
        )
        reader = PdfReader(target)
        pages = [page.extract_text() for page in reader.pages]
        destinations = {}
        for row in rows:
            destinations[row.sheet] = next(
                index for index, text in enumerate(pages) if re.search(
                    re.escape(row.sheet) + r"\s+Prescripcion", text,
                )
            )
        links = []
        fonts = {}
        fills = []
        for index, page in enumerate(reader.pages):
            if HEADERS[0] not in pages[index]:
                continue

            def collect_font(text, cm, tm, font, size):
                """Registra las fuentes que realmente se usan para la cabecera y las filas."""
                if font is not None:
                    fonts.setdefault(text.strip(), []).append(str(font.get("/BaseFont", "")))

            def collect_fill(operator, operands, cm, tm):
                """Recoge los colores de relleno RGB emitidos en el contenido PDF."""
                if operator == b"rg":
                    fills.append(tuple(round(float(value), 4) for value in operands))

            page.extract_text(visitor_text=collect_font, visitor_operand_before=collect_fill)
            for reference in page.get("/Annots", []):
                annotation = reference.get_object()
                if annotation.get("/Subtype") != "/Link":
                    continue
                destination = annotation.get("/Dest")
                if destination is None:
                    continue
                page_number = next(
                    number for number, candidate in enumerate(reader.pages)
                    if candidate.indirect_reference == destination[0]
                )
                links.append(page_number)
                rectangle = annotation["/Rect"]
                labels = []

                def collect(text, cm, tm, font, size):
                    """Recoge el nombre visible situado dentro del enlace de ficha."""
                    x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                    y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                    if rectangle[0] <= x <= rectangle[2] and rectangle[1] - size <= y <= rectangle[3]:
                        labels.append(text.strip())

                page.extract_text(visitor_text=collect)
                self.assertIn(rows[len(links) - 1].sheet, labels)
        self.assertEqual(links, [destinations[row.sheet] for row in rows])
        self.assertTrue(all(page > 0 for page in links))
        self.assertNotIn(INDEX_TOKEN, "\n".join(pages))
        self.assertGreaterEqual(sum(text.count("•") for text in pages), len(rows))
        for label in HEADERS:
            self.assertTrue(all("bold" in font.casefold() for font in fonts[label]))
        for row in rows:
            self.assertTrue(all("bold" not in font.casefold() for font in fonts[row.sheet]))
        for label, used_fonts in fonts.items():
            if "CAPITULO DEL" in label:
                self.assertTrue(all("bold" not in font.casefold() for font in used_fonts))
        for color in (HEADER_BACKGROUND, ALTERNATE_BACKGROUND, "#FFFFFF"):
            expected = tuple(round(int(color[start:start + 2], 16) / 255, 4) for start in (1, 3, 5))
            self.assertIn(expected, fills)

    def test_long_pdf_table_splits_and_repeats_its_header(self):
        """Una tabla extensa no provoca errores y conserva enlaces en todos los fragmentos."""
        headings = [f"### Ficha extensa {index}\n\nPrescripcion.\n" for index in range(75)]
        rows = [
            SheetIndexRow("CAP. 1 — CAPITULO", f"FICHA EXTENSA {index}", f"PPTPHeading{index + 2}")
            for index in range(75)
        ]
        source = "# Fichas\n\nTabla de fichas\n\n" + markdown_index(rows) + "\n\n## Capitulo\n\n" + "\n".join(headings)
        target = self.root / "long.pdf"
        write_document(source, target, self.state["variables"], ReportSettings(output_format="pdf"))
        reader = PdfReader(target)
        table_pages = [page for page in reader.pages if HEADERS[0] in page.extract_text()]
        self.assertGreater(len(table_pages), 1)
        self.assertEqual(sum(len(page.get("/Annots", [])) for page in table_pages), len(rows))

    def test_tall_chapter_rows_repeat_header_only_on_new_pages_with_identical_alignment(self):
        """Reproduce la cabecera duplicada y el desplazamiento al partir varias filas largas."""
        rows = [
            SheetIndexRow(f"CAP. {chapter}", f"FICHA {chapter} {index}", f"PPTPHeading{chapter}")
            for chapter, count in ((1, 6), (2, 20), (3, 20))
            for index in range(count)
        ]
        source = (
            "# Fichas\n\n**Tabla 9.1.** Relacion de fichas\n\n" + markdown_index(rows)
            + "\n\n## Uno\n\n## Dos\n\n## Tres\n"
        )
        target = self.root / "tall-chapters.pdf"
        write_document(source, target, self.state["variables"], ReportSettings(output_format="pdf"))
        with PdfReader(target) as reader:
            table_pages = [page for page in reader.pages if HEADERS[0] in page.extract_text()]
            self.assertGreater(len(table_pages), 1)
            positions = []
            for page in table_pages:
                self.assertEqual(page.extract_text().count(HEADERS[0]), 1)

                def collect(text, cm, tm, font, size):
                    """Mide la posición real de la primera columna en cada fragmento."""
                    if text.strip() == HEADERS[0]:
                        positions.append(tm[4] * cm[0] + tm[5] * cm[2] + cm[4])

                page.extract_text(visitor_text=collect)
            self.assertEqual(len(positions), len(table_pages))
            for position in positions:
                self.assertAlmostEqual(position, positions[0], places=3)
            self.assertEqual(sum(len(page.get("/Annots", [])) for page in table_pages), len(rows))

    def test_distributed_section_nine_contains_editable_intro_caption_and_marker(self):
        """El ZIP real conserva el historial y ofrece el punto de inserción de la tabla."""
        source = ZipLibrary(LIBRARY_PATH, DOCS_ROOT).read_documents("09")[0][1]
        history, body = read_revisions(source)
        self.assertTrue(history)
        self.assertEqual(body.count(INDEX_MARKER), 1)
        self.assertIn("Tabla 9.1.", body)
        self.assertIn("**Tabla 9.1.**", body)
        self.assertNotIn(", sin numeración de sección,", body)
        self.assertIn("nombre", body)
        self.assertNotIn("columna «Página»", body)


if __name__ == "__main__":
    unittest.main()
