"""Separación de información interna y prescripciones exportables de las fichas."""
from dataclasses import asdict
import os
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from pypdf import PdfReader

from budget_reports import ReportSettings
from tools._library_editor import read_markdown
from tools._library_editor_qt import LibraryEditorQt
from tools._library_markdown import document_markdown, informational_ranges
from tools.pptp import DOCS_ROOT, LIBRARY_PATH
from tools.pptp.bcca_library import mark_library_information, mark_sheet_information
from tools.pptp.document import VARIABLES, Selection, compose, text_blocks, write_document
from tools.pptp.library import ZipLibrary


class InformationalMarkdownTests(unittest.TestCase):
    def test_only_standalone_delimiters_hide_information_and_keep_paragraphs_separate(self):
        """Remove multiple blocks, not literal inline quotes, and reject unclosed blocks."""
        source = 'Antes\n"""\nInterno {{sin_valor}}\n"""\nDespués\n  """  \nOtro\n"""\nFin """ literal'
        output = document_markdown(source)
        self.assertNotIn("Interno", output)
        self.assertNotIn("Otro", output)
        self.assertIn('Fin """ literal', output)
        self.assertEqual(len(informational_ranges(source)), 2)
        self.assertIn("Antes\n\n\nDespués", output)
        self.assertNotIn("Interno", str(text_blocks(source)))
        with self.assertRaisesRegex(ValueError, "sin cerrar"):
            document_markdown('"""\nPendiente')
        self.assertEqual(informational_ranges('"""\nPendiente', strict=False), [(0, 13)])

    def test_migration_keeps_technical_text_and_marks_the_requested_prefix(self):
        """Hide the exact informational prefix, including inherited family metadata."""
        prefix = (
            "# Peldaños\n\n**Clasificación BCCA:** `01RPN`.\n\n"
            "## Alcance específico\n\nInformación de aplicación.\n\n"
            "## Unidades de referencia BCCA\n\n### 01RPN00001 - Unidad\n\nDescripción BC3.\n\n"
            "## Aplicación, medición y valoración\n\nRevisar alternativas.\n\n"
            "## Prescripciones integradas de la familia\n\nRevisión técnica.\n\n"
            "### Peldaños\n\n**Clasificación:** 01RP\n**Capítulo:** Demoliciones\n\n"
        )
        technical = (
            "#### Descripción\n\nRetirada de peldaños.\n\n#### Ejecución\n\n"
            "1. Proteger los colindantes.\n\n#### Medición\n\n"
            "* Longitud realmente ejecutada.\n"
        )
        marked = mark_sheet_information(prefix + technical)
        self.assertTrue(marked.startswith('"""\n' + prefix + '"""\n'))
        self.assertEqual(document_markdown(marked), technical.strip())
        self.assertEqual(mark_sheet_information(marked), marked)
        self.assertEqual(marked.replace('"""\n', ""), prefix + technical)

    def test_multiple_variants_keep_their_names_but_hide_metadata_and_source_notes(self):
        """Preserve distinct technical alternatives without printing catalog annotations."""
        source = (
            "# Ficha\n\n## Prescripciones integradas de la familia\n\nAviso interno.\n\n"
            "### Variante A\n\n**Clasificación:** 02RR\n\n#### Descripción\n\nTexto A.\n\n"
            "Fuente: [PPTP.md](../PPTP.md), líneas 1-4. Transcripción para trazabilidad.\n\n"
            "### Variante B\n\n**Clasificación:** 02RR\n\n#### Descripción\n\nTexto B.\n"
        )
        marked = mark_sheet_information(source)
        output = document_markdown(marked)
        for expected in ("### Variante A", "### Variante B", "Texto A.", "Texto B."):
            self.assertIn(expected, output)
        for hidden in ("Clasificación", "Fuente:", "Aviso interno", "Prescripciones integradas"):
            self.assertNotIn(hidden, output)
        self.assertEqual(marked.replace('"""\n', ""), source)

    def test_unintegrated_classification_is_information_only(self):
        """Do not turn BCCA reference descriptions into invented contractual prescriptions."""
        source = "# Ensayos\n\n**Clasificación BCCA:** `18RPA`.\n\n## Unidades de referencia BCCA\n\nDatos.\n"
        self.assertEqual(document_markdown(mark_sheet_information(source)), "")

    def test_compose_omits_information_before_substitution_and_numbers_the_technical_sections(self):
        """ZIP-independent document composition must obey the same informational delimiters."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for prefix in ("01", "02", "03", "04"):
                (root / (prefix + "-Base.md")).write_text("# Sección " + prefix, encoding="utf-8")
            (root / "01-Base.md").write_text(
                '"""\n# Encabezado interno\n{{variable_base_interna}}\n"""\n# Sección 01',
                encoding="utf-8",
            )
            path = "UNI/01_BASE/01RP_PELDANOS/01RPN-Piedra.md"
            sheet = root.joinpath(*path.split("/"))
            sheet.parent.mkdir(parents=True)
            sheet.write_text(
                '"""\n# Título interno\n{{variable_interna}}\n"""\n'
                "#### Descripción\n\nRetirar sin dañar.\n\n#### Medición\n\nLongitud neta.\n",
                encoding="utf-8",
            )
            state = {
                "variables": {key: "Valor" for key in VARIABLES},
                "selections": [asdict(Selection(0, "01RPN00001", "01#", "Peldaño", path))],
                "regulations": {},
            }
            output = compose(root, state, [])
            self.assertNotIn("interno", output)
            self.assertNotIn('"""', output)
            self.assertIn("**Descripción**", output)
            self.assertNotIn("4.1.1.1", output)
            self.assertIn("Retirar sin dañar.", output)
            sheet.write_text('"""\nSolo información.\n"""\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no contiene prescripciones"):
                compose(root, state, [])
            sheet.write_text('"""\nBloque abierto', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "sin cerrar"):
                compose(root, state, [])

    def test_qt_editor_preserves_information_and_rejects_unclosed_blocks(self):
        """Save edited references without exporting them or accepting invalid delimiters."""
        self.application = QApplication.instance() or QApplication([])
        parent = QWidget()
        self.addCleanup(parent.close)
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "library.zip"
            source = '"""\nReferencia inicial.\n"""\nTexto contractual.\n"""\nReferencia final.\n"""'
            with ZipFile(archive, "w") as target:
                target.writestr("ficha.md", source)
            editor = LibraryEditorQt(parent, archive, "Prueba", used_members=set())
            self.addCleanup(editor.deleteLater)
            self.assertEqual(editor.editor.toPlainText(), source)
            edited = source.replace("Referencia inicial.", "Referencia actualizada.")
            editor.editor.setPlainText(edited)
            with patch.object(QMessageBox, "information"):
                self.assertTrue(editor.save())
            self.assertEqual(read_markdown(archive, "ficha.md"), edited)
            self.assertEqual(document_markdown(edited), "Texto contractual.")
            editor.editor.setPlainText(edited + '\n"""\nSin cierre')
            with patch.object(QMessageBox, "critical") as error:
                self.assertFalse(editor.save())
            error.assert_called_once()
            self.assertEqual(read_markdown(archive, "ficha.md"), edited)

    def test_distributed_sheets_hide_catalog_information_and_flag_only_undeveloped_groups(self):
        """Check all distributed sheets, not just the peldaño example."""
        library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        empty = []
        with ZipFile(LIBRARY_PATH) as archive:
            for entry in library.entries.values():
                source = archive.read(entry.path).decode("utf-8-sig")
                with self.subTest(code=entry.code):
                    self.assertTrue(informational_ranges(source))
                    output = document_markdown(source)
                    for marker in (
                        "**Clasificación", "**Capítulo", "**Familia", "Fuente:",
                        "## Alcance específico", "## Unidades de referencia BCCA",
                        "## Aplicación, medición y valoración",
                        "## Prescripciones integradas de la familia",
                    ):
                        self.assertNotIn(marker, output)
                    if not output:
                        empty.append(entry.code)
                    else:
                        self.assertIn("Descripción", output)
                        self.assertIn("Ejecución", output)
        self.assertEqual(empty, ["18RPA", "18RPB", "18RPC"])
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "library.zip"
            original = LIBRARY_PATH.read_bytes()
            copied.write_bytes(original)
            self.assertEqual(mark_library_information(copied), 0)
            self.assertEqual(copied.read_bytes(), original)

    def test_real_pdf_and_odt_include_only_prescriptions_with_catalog_title(self):
        """Inspect both final document formats for technical text and forbidden metadata."""
        library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        entry = next(entry for entry in library.entries.values() if entry.code == "01RPN")
        variables = {key: "Valor de prueba" for key in VARIABLES}
        variables["JUSTIFICACION_NO_APLICACION"] = "no cumplirse los supuestos de prueba"
        state = {
            "variables": variables,
            "selections": [asdict(Selection(0, "01RPN00001", "01#", "Peldaño", entry.path))],
            "regulations": {},
        }
        source = compose(library, state, [])
        self.assertIn("### 9.1.1 " + entry.title.upper(), source)
        self.assertIn("**Descripción**", source)
        self.assertIn("**Ejecución**", source)
        self.assertNotIn("9.1.1.1", source)
        with tempfile.TemporaryDirectory() as directory:
            for extension in ("pdf", "odt"):
                with self.subTest(format=extension):
                    target = Path(directory) / ("PPTP." + extension)
                    write_document(source, target, variables, ReportSettings(output_format=extension))
                    if extension == "pdf":
                        text = "\n".join(page.extract_text() or "" for page in PdfReader(target).pages)
                    else:
                        with ZipFile(target) as archive:
                            text = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                    for forbidden in (
                        "01RPN", "01RP", "Unidades de referencia", "UNIDADES DE REFERENCIA",
                        "Clasificación", "CLASIFICACIÓN", "Prescripciones integradas",
                        "PRESCRIPCIONES INTEGRADAS", "Transcripción para trazabilidad", '"""',
                    ):
                        self.assertNotIn(forbidden, text)
                    self.assertIn(entry.title.upper(), text)
                    self.assertIn("delimitación de la zona de actuación", text.lower())
                    self.assertIn("Longitud real ejecutada", text)


if __name__ == "__main__":
    unittest.main()
