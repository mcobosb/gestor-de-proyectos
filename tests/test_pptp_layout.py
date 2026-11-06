"""Comprueba el pie de plantilla y la ausencia de titulos repetidos en PPTP."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader

from budget_reports import ReportSettings
from report_layout import DEFAULT_TEMPLATE_PATH, replace_footer_fields, replace_template_fields
from tools.pptp.document import number_headings, write_document


TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
STYLE = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
TITLE = "PLIEGO DE PRESCRIPCIONES TÉCNICAS PARTICULARES"


class PptpLayoutTests(unittest.TestCase):
    """Valida documentos reales y campos fragmentados entre estilos."""

    def test_pdf_sections_continue_on_same_page_and_body_is_justified(self):
        """No desplaza una seccion completa si caben el titulo y el inicio del parrafo."""
        source = "# Inicio\n\n" + "\n\n".join(
            f"Párrafo previo {index} con una línea de contenido." for index in range(14)
        )
        source += "\n\n# Continuación\n## Grupo\n### Ficha\n#### Definición\n\n"
        source += "Contenido técnico para justificar y dividir entre páginas. " * 85
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "continuous.pdf"
            write_document(source, target, {"titulo_proyecto": "Proyecto"}, ReportSettings())
            pages = PdfReader(target).pages
            body = pages[1].extract_text()
            self.assertIn("Párrafo previo 13", body)
            self.assertIn("2 CONTINUACIÓN", body)
            self.assertIn("2.1.1.1 DEFINICIÓN", body)
            self.assertIn("Contenido técnico", body)
            commands = pages[1].get_contents().get_data()
            self.assertRegex(commands, rb"[1-9]\d*(?:\.\d+)? Tw")

    def test_odt_body_is_justified_and_sections_have_no_forced_page_breaks(self):
        """El ODT mantiene flujo continuo y solo separa el indice del cuerpo."""
        source = "# Inicio\n\n" + "\n\n".join(
            f"Párrafo previo {index} con una línea de contenido." for index in range(14)
        )
        source += "\n\n# Continuación\n## Grupo\n### Ficha\n#### Definición\n\n"
        source += "Contenido técnico para justificar y dividir entre páginas. " * 85
        fo = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "continuous.odt"
            write_document(
                source, target, {"titulo_proyecto": "Proyecto"}, ReportSettings(output_format="odt"),
            )
            with ZipFile(target) as archive:
                xml = ET.fromstring(archive.read("content.xml"))
            styles = {
                element.get(f"{{{STYLE}}}name"): element
                for element in xml.findall(f".//{{{STYLE}}}style")
            }
            for name in ("PPTPBody", *(f"PPTPHeading{level}" for level in range(1, 7))):
                properties = styles[name].find(f"{{{STYLE}}}paragraph-properties")
                self.assertEqual(properties.get(f"{{{fo}}}text-align"), "justify" if name == "PPTPBody" else "start")
                self.assertEqual(properties.get(f"{{{fo}}}break-before"), "auto")
                self.assertEqual(properties.get(f"{{{fo}}}break-after"), "auto")
                self.assertEqual(properties.get(f"{{{fo}}}keep-together"), "auto")
            forced = [
                properties for properties in xml.findall(f".//{{{STYLE}}}paragraph-properties")
                if properties.get(f"{{{fo}}}break-before") == "page"
                or properties.get(f"{{{fo}}}break-after") == "page"
            ]
            self.assertEqual(len(forced), 1)
            soffice = shutil.which("soffice") or str(
                Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
                / "LibreOffice" / "program" / "soffice.exe"
            )
            if not Path(soffice).is_file():
                self.skipTest("LibreOffice no disponible para validar el flujo ODT renderizado.")
            subprocess.run(
                [soffice, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(root), str(target)],
                check=True, capture_output=True, timeout=90,
            )
            pages = PdfReader(root / "continuous.pdf").pages
            body = " ".join(pages[1].extract_text().split())
            self.assertIn("Párrafo previo 13", body)
            self.assertIn("2 CONTINUACIÓN", body)
            self.assertIn("2.1.1.1 DEFINICIÓN", body)
            self.assertIn("Contenido técnico", body)

    def test_heading_numbering_uppercases_titles_and_is_idempotent(self):
        """Numera hasta el cuarto nivel sin cambiar los parrafos ni duplicar prefijos."""
        source = (
            "# 1 Introducción\n\nTexto con Minúsculas.\n"
            "## 1.1 Definición\n### 1.1.1 Procedimiento\n"
            "#### 1.1.1.1 Primera fase\n#### Segunda fase\n"
            "### Medición\n## Otro apartado\n# 2. Materiales"
        )
        expected = (
            "# 1 INTRODUCCIÓN\n\nTexto con Minúsculas.\n"
            "## 1.1 DEFINICIÓN\n### 1.1.1 PROCEDIMIENTO\n"
            "#### 1.1.1.1 PRIMERA FASE\n#### 1.1.1.2 SEGUNDA FASE\n"
            "### 1.1.2 MEDICIÓN\n## 1.2 OTRO APARTADO\n# 2 MATERIALES"
        )
        self.assertEqual(number_headings(source), expected)
        self.assertEqual(number_headings(expected), expected)

    def test_pdf_contents_uses_real_pages_when_index_spans_multiple_pages(self):
        """El indice inicial enlaza cada titulo con su pagina tras maquetar el propio indice."""
        headings = [
            f"Sección {index:03d} & control <marca> con título suficientemente largo "
            "para comprobar el ajuste de líneas en el índice"
            for index in range(60)
        ]
        source = "\n\n".join(
            f"# {heading}\n\nContenido técnico para sección {index:03d}. " + "Texto normal. " * 35
            for index, heading in enumerate(headings)
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "index.pdf"
            write_document(source, target, {"titulo_proyecto": "Proyecto"}, ReportSettings())
            pages = PdfReader(target).pages
            texts = [" ".join(page.extract_text().split()) for page in pages]
            body_start = next(
                index for index, text in enumerate(texts) if "Contenido técnico" in text
            )
            self.assertGreater(body_start, 1)
            self.assertIn("ÍNDICE", texts[0])
            contents = " ".join(texts[:body_start])
            for index, heading in enumerate(headings):
                label = f"{index + 1} {heading.upper()}"
                page_number = next(
                    page + 1 for page in range(body_start, len(texts)) if label in texts[page]
                )
                self.assertRegex(contents, r"\b" + str(page_number) + r"\s+" + re.escape(label))
            self.assertTrue(all(page.get("/Annots") for page in pages[:body_start]))

    def test_odt_contents_has_outline_levels_bookmarks_and_matching_uppercase_titles(self):
        """El indice ODF nativo incluye titulos y referencias de pagina editables."""
        source = "# Capítulo\n## Ficha\n### Definición\n#### Preparación\n\nTexto normal."
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "index.odt"
            write_document(
                source, target, {"titulo_proyecto": "Proyecto"}, ReportSettings(output_format="odt"),
                toc_depth=6,
            )
            with ZipFile(target) as archive:
                xml = ET.fromstring(archive.read("content.xml"))
                toc = xml.find(f".//{{{TEXT}}}table-of-content")
                self.assertIsNotNone(toc)
                entries = toc.findall(f".//{{{TEXT}}}index-body//{{{TEXT}}}a")
                headings = xml.findall(f".//{{{TEXT}}}h")
                self.assertEqual(len(entries), 4)
                for level, (entry, heading) in enumerate(zip(entries, headings), 1):
                    self.assertEqual(entry.text, heading.text)
                    self.assertEqual(heading.text, heading.text.upper())
                    self.assertEqual(heading.get(f"{{{TEXT}}}outline-level"), str(level))
                    self.assertEqual(heading.get(f"{{{TEXT}}}style-name"), f"PPTPHeading{level}")
                    bookmark = heading.find(f"{{{TEXT}}}bookmark")
                    reference = entry.find(f"{{{TEXT}}}bookmark-ref")
                    self.assertEqual(reference.get(f"{{{TEXT}}}ref-name"), bookmark.get(f"{{{TEXT}}}name"))
                    self.assertEqual(reference.get(f"{{{TEXT}}}reference-format"), "page")
                self.assertEqual(len(toc.findall(f".//{{{TEXT}}}index-entry-page-number")), 6)

    def test_contents_depth_filters_only_index_and_keeps_bookmark_targets(self):
        """Respeta el limite elegido en ambos formatos sin quitar titulos del cuerpo."""
        source = (
            "# Principal\n## Ficha\n### Detalle\n#### Procedimiento\n"
            "## Otra ficha\n### Otro detalle\n\nTexto del cuerpo."
        )
        labels = [
            "1 PRINCIPAL", "1.1 FICHA", "1.1.1 DETALLE", "1.1.1.1 PROCEDIMIENTO",
            "1.2 OTRA FICHA", "1.2.1 OTRO DETALLE",
        ]
        levels = [1, 2, 3, 4, 2, 3]
        with tempfile.TemporaryDirectory() as directory:
            for output_format in ("pdf", "odt"):
                for depth in (None, 1, 2, 3, 6):
                    with self.subTest(format=output_format, depth=depth):
                        target = Path(directory) / ("depth." + output_format)
                        options = {} if depth is None else {"toc_depth": depth}
                        write_document(
                            source, target, {"titulo_proyecto": "Proyecto"},
                            ReportSettings(output_format=output_format), **options,
                        )
                        expected = [
                            label for label, level in zip(labels, levels)
                            if level <= (2 if depth is None else depth)
                        ]
                        if output_format == "pdf":
                            pages = PdfReader(target).pages
                            contents = " ".join(pages[0].extract_text().split())
                            body = " ".join(pages[1].extract_text().split())
                            for label in labels:
                                self.assertIn(label, body)
                                self.assertEqual(label in contents, label in expected)
                        else:
                            with ZipFile(target) as archive:
                                xml = ET.fromstring(archive.read("content.xml"))
                            toc = xml.find(f".//{{{TEXT}}}table-of-content")
                            entries = toc.findall(f".//{{{TEXT}}}index-body//{{{TEXT}}}a")
                            self.assertEqual([entry.text for entry in entries], expected)
                            self.assertEqual(
                                toc.find(f"{{{TEXT}}}table-of-content-source").get(f"{{{TEXT}}}outline-level"),
                                str(2 if depth is None else depth),
                            )
                            headings = xml.findall(f".//{{{TEXT}}}h")
                            self.assertEqual([heading.text for heading in headings], labels)
                            for entry in entries:
                                reference = entry.find(f"{{{TEXT}}}bookmark-ref")
                                bookmark = reference.get(f"{{{TEXT}}}ref-name")
                                heading = next(
                                    heading for heading in headings
                                    if heading.find(f"{{{TEXT}}}bookmark").get(f"{{{TEXT}}}name") == bookmark
                                )
                                self.assertEqual(entry.text, heading.text)

    def test_invalid_contents_depth_is_rejected_before_writing(self):
        """No crea documentos si se recibe un limite fuera del rango o de otro tipo."""
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "invalid.pdf"
            for depth in (0, 7, True, 2.5, "2", None):
                with self.subTest(depth=depth), self.assertRaisesRegex(ValueError, "índice"):
                    write_document(
                        "# Título", target, {"titulo_proyecto": "Proyecto"},
                        ReportSettings(), toc_depth=depth,
                    )
                self.assertFalse(target.exists())

    def test_writer_resolves_odt_contents_page_numbers(self):
        """Writer calcula las referencias del indice al paginar, sin mostrar unos fijos."""
        soffice = shutil.which("soffice") or str(
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "LibreOffice" / "program" / "soffice.exe"
        )
        if not Path(soffice).is_file():
            self.skipTest("LibreOffice no disponible para validar las paginas del indice ODT.")
        source = "# Introducción\n\nTexto normal.\n\n## Definición\n\nDetalle.\n\n"
        source += "\n\n".join("Contenido técnico. " * 45 for index in range(12))
        source += "\n\n# Final\n\nFin del documento."
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "index.odt"
            write_document(
                source, target, {"titulo_proyecto": "Proyecto"}, ReportSettings(output_format="odt"),
            )
            result = subprocess.run(
                [soffice, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(root), str(target)],
                check=True, capture_output=True, timeout=90,
            )
            rendered = root / "index.pdf"
            self.assertTrue(rendered.is_file(), result.stdout.decode(errors="replace"))
            texts = [" ".join(page.extract_text().split()) for page in PdfReader(rendered).pages]
            self.assertIn("ÍNDICE", texts[0])
            final_page = next(
                index + 1 for index, text in enumerate(texts) if "Fin del documento." in text
            )
            self.assertGreater(final_page, 2)
            self.assertRegex(texts[0], rf"2 FINAL\s+{final_page}\b")
            self.assertIn("1.1 DEFINICIÓN", texts[0])

    def test_fragmented_footer_fields_keep_styles_and_dynamic_number(self):
        """Los marcadores repartidos en spans conservan el texto y sus estilos."""
        footer = ET.fromstring(
            f'<s:footer xmlns:s="{STYLE}" xmlns:t="{TEXT}"><t:p>'
            '<t:span t:style-name="Bold">Antes {{capi</t:span>'
            '<t:span>tulo}} medio {no_</t:span>'
            '<t:span>pagina} final</t:span></t:p></s:footer>',
        )
        result = replace_footer_fields(footer, TITLE)
        self.assertEqual("".join(result.itertext()), f"Antes {TITLE} medio 1 final")
        self.assertIsNotNone(result.find(f".//{{{TEXT}}}page-number"))
        self.assertEqual(result.find(f".//{{{TEXT}}}span").get(f"{{{TEXT}}}style-name"), "Bold")
        self.assertIn("{{capi", "".join(footer.itertext()))

    def test_multiple_fields_in_same_slot_and_nested_tails(self):
        """Las sustituciones preservan el orden con varios campos en un parrafo."""
        for content in (
            "{{no_pagina}} / {{capitulo}} / {no_pagina}",
            "<t:span><t:span>prefijo</t:span> {{no_pagina}} / "
            "{{capitulo}} / {no_pagina}</t:span>",
        ):
            with self.subTest(content=content):
                footer = ET.fromstring(
                    f'<s:footer xmlns:s="{STYLE}" xmlns:t="{TEXT}">'
                    f"<t:p>{content}</t:p></s:footer>",
                )
                result = replace_footer_fields(footer, TITLE)
                self.assertTrue("".join(result.itertext()).endswith(f"1 / {TITLE} / 1"))
                self.assertEqual(len(result.findall(f".//{{{TEXT}}}page-number")), 2)

    def test_accented_chapter_marker_is_supported(self):
        """El marcador de capitulo tambien admite la tilde del texto de plantilla."""
        footer = ET.fromstring(
            f'<s:footer xmlns:s="{STYLE}" xmlns:t="{TEXT}"><t:p>'
            "{{capítulo}} / {{no_pagina}}</t:p></s:footer>",
        )
        result = replace_footer_fields(footer, TITLE)
        self.assertEqual("".join(result.itertext()), f"{TITLE} / 1")
        self.assertIsNotNone(result.find(f".//{{{TEXT}}}page-number"))

    def test_configured_template_fields_replace_fragmented_markers(self):
        """Los marcadores configurables se sustituyen sin perder sus estilos."""
        section = ET.fromstring(
            f'<s:header xmlns:s="{STYLE}" xmlns:t="{TEXT}"><t:p>'
            '<t:span>Expediente: {{expe</t:span>diente}} · '
            '<t:span>{{tít</t:span>ulo}}</t:p></s:header>'
        )
        result = replace_template_fields(section, {
            "{{expediente}}": "EXP-2026-001",
            "{{título}}": "Reforma del mercado",
            "{{titulo}}": "Reforma del mercado",
        })
        self.assertEqual(
            "".join(result.itertext()),
            "Expediente: EXP-2026-001 · Reforma del mercado",
        )

    def test_missing_markers_do_not_add_footer_title_or_page_number(self):
        """La ausencia de un campo no se suple con texto o numeros adicionales."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for marker in ("PIE EXISTENTE", "{{capítulo}}", "{{no_pagina}}"):
                template = root / "footer.odt"
                with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as archive:
                    for member in source.infolist():
                        data = source.read(member.filename)
                        if member.filename == "styles.xml":
                            xml = ET.fromstring(data)
                            footer = xml.find(f".//{{{STYLE}}}footer")
                            footer.clear()
                            ET.SubElement(footer, f"{{{TEXT}}}p").text = marker
                            data = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
                        archive.writestr(member, data)
                for output_format in ("pdf", "odt"):
                    with self.subTest(marker=marker, output_format=output_format):
                        target = root / ("PPTP." + output_format)
                        write_document(
                            "# Introducción\n\nTexto del cuerpo.", target,
                            {"titulo_proyecto": "PROYECTO NO REPETIDO"},
                            ReportSettings(output_format=output_format, template_path=str(template)),
                        )
                        if output_format == "pdf":
                            for page_number, page in enumerate(PdfReader(target).pages, 1):
                                footer_runs = []

                                def observe(value, cm, tm, font, size):
                                    """Recoge solo las cadenas dibujadas a la altura del pie."""
                                    if value.strip() and cm[5] + tm[5] < 130:
                                        footer_runs.append(value.strip())

                                page.extract_text(visitor_text=observe)
                                footer_text = " ".join(footer_runs)
                                self.assertEqual(
                                    footer_text, TITLE if marker == "{{capítulo}}"
                                    else str(page_number)
                                    if marker == "{{no_pagina}}" else "PIE EXISTENTE",
                                )
                        else:
                            with ZipFile(target) as archive:
                                styles = ET.fromstring(archive.read("styles.xml"))
                                footer = styles.find(f".//{{{STYLE}}}footer")
                                self.assertEqual(
                                    "".join(footer.itertext()),
                                    TITLE if marker == "{{capítulo}}"
                                    else "1" if marker == "{{no_pagina}}" else "PIE EXISTENTE",
                                )
                                self.assertEqual(
                                    footer.find(f".//{{{TEXT}}}page-number") is not None,
                                    marker == "{{no_pagina}}",
                                )

    def test_pdf_and_odt_use_template_footer_without_repeated_project_title(self):
        """Cada pagina lleva el titulo solo en el pie y su numero en el marcador."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "footer.odt"
            with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as target:
                for member in source.infolist():
                    data = source.read(member.filename)
                    if member.filename == "styles.xml":
                        xml = ET.fromstring(data)
                        footer = xml.find(f".//{{{STYLE}}}footer")
                        for child in list(footer):
                            footer.remove(child)
                        paragraph = ET.SubElement(footer, f"{{{TEXT}}}p")
                        ET.SubElement(paragraph, f"{{{TEXT}}}span").text = "{{capi"
                        ET.SubElement(paragraph, f"{{{TEXT}}}span").text = "tulo}} — {no_"
                        ET.SubElement(paragraph, f"{{{TEXT}}}span").text = "pagina}"
                        data = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
                    target.writestr(member, data)
            body = "# INTRODUCCIÓN\n\n" + "\n\n".join(
                f"Párrafo sintético {index}: " + "Contenido técnico para paginación. " * 12
                for index in range(65)
            )
            for output_format in ("pdf", "odt"):
                with self.subTest(format=output_format):
                    target = root / ("PPTP." + output_format)
                    write_document(
                        body, target, {"titulo_proyecto": "PROYECTO NO REPETIDO"},
                        ReportSettings(output_format=output_format, template_path=str(template)),
                    )
                    if output_format == "pdf":
                        reader = PdfReader(target)
                        self.assertGreater(len(reader.pages), 2)
                        for index, page in enumerate(reader.pages, 1):
                            text = page.extract_text()
                            normalized = " ".join(text.split())
                            self.assertEqual(normalized.count(TITLE), 1)
                            self.assertIn(f"{TITLE} — {index}", normalized)
                            self.assertNotIn("PROYECTO NO REPETIDO", text)
                            self.assertNotIn("{{capitulo}}", text)
                            self.assertNotIn("{no_pagina}", text)
                            footer_words = []

                            def observe(value, cm, tm, font, size):
                                """Registra la posicion del texto del pie en el PDF."""
                                if "PLIEGO" in value:
                                    footer_words.append(cm[5] + tm[5])

                            page.extract_text(visitor_text=observe)
                            self.assertTrue(footer_words)
                            self.assertTrue(all(y < 150 for y in footer_words))
                    else:
                        with ZipFile(target) as archive:
                            styles = ET.fromstring(archive.read("styles.xml"))
                            header = styles.find(f".//{{{STYLE}}}header")
                            footer = styles.find(f".//{{{STYLE}}}footer")
                            self.assertNotIn(TITLE, "".join(header.itertext()))
                            self.assertNotIn("PROYECTO NO REPETIDO", "".join(header.itertext()))
                            self.assertEqual("".join(footer.itertext()), f"{TITLE} — 1")
                            self.assertIsNotNone(footer.find(f".//{{{TEXT}}}page-number"))

            table_template = root / "table-footer.odt"
            with ZipFile(template) as source, ZipFile(table_template, "w") as target:
                for member in source.infolist():
                    data = source.read(member.filename)
                    if member.filename == "styles.xml":
                        xml = ET.fromstring(data)
                        footer = xml.find(f".//{{{STYLE}}}footer")
                        paragraph = footer.find(f"{{{TEXT}}}p")
                        footer.remove(paragraph)
                        automatic = xml.find(
                            "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}automatic-styles",
                        )
                        column_style = ET.SubElement(automatic, f"{{{STYLE}}}style", {
                            f"{{{STYLE}}}name": "FooterColumn",
                            f"{{{STYLE}}}family": "table-column",
                        })
                        ET.SubElement(column_style, f"{{{STYLE}}}table-column-properties", {
                            f"{{{STYLE}}}column-width": "16cm",
                        })
                        table = ET.SubElement(footer, f"{{{TABLE}}}table")
                        ET.SubElement(table, f"{{{TABLE}}}table-column", {
                            f"{{{TABLE}}}style-name": "FooterColumn",
                        })
                        row = ET.SubElement(table, f"{{{TABLE}}}table-row")
                        cell = ET.SubElement(row, f"{{{TABLE}}}table-cell")
                        cell.append(paragraph)
                        data = ET.tostring(xml, encoding="utf-8", xml_declaration=True)
                    target.writestr(member, data)
            for output_format in ("pdf", "odt"):
                with self.subTest(table_format=output_format):
                    target = root / ("table." + output_format)
                    write_document(
                        body, target, {"titulo_proyecto": "PROYECTO NO REPETIDO"},
                        ReportSettings(
                            output_format=output_format, template_path=str(table_template),
                        ),
                    )
                    if output_format == "pdf":
                        for index, page in enumerate(PdfReader(target).pages, 1):
                            text = " ".join(page.extract_text().split())
                            self.assertEqual(text.count(TITLE), 1)
                            self.assertIn(f"{TITLE} — {index}", text)
                    else:
                        with ZipFile(target) as archive:
                            styles = ET.fromstring(archive.read("styles.xml"))
                            cell = styles.find(f".//{{{STYLE}}}footer//{{{TABLE}}}table-cell")
                            self.assertEqual("".join(cell.itertext()), f"{TITLE} — 1")
                            self.assertIsNotNone(cell.find(f".//{{{TEXT}}}page-number"))


if __name__ == "__main__":
    unittest.main()
