"""Consultas independientes con filtrado inmediato sobre los catalogos NTP y UNE."""
import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget
from reference_search import ReferenceEntry, load_reference_entries
from reference_search_qt import ReferenceSearchWindowQt


NTP_ENTRIES = (
    ReferenceEntry(
        "NTP 1", "Señalización de seguridad", "1982", "", "",
        "https://www.insst.es/ntp-1", "https://www.insst.es/ntp-1.pdf",
    ),
    ReferenceEntry(
        "NTP 495", "Soldadura y oxicorte", "1998", "", "",
        "https://www.insst.es/ntp-495",
    ),
)
UNE_ENTRIES = (
    ReferenceEntry(
        "UNE 1:2020", "Señalización accesible", "2020-01-01", "Vigente", "Accesibilidad",
        "https://www.une.org/norma-1",
    ),
    ReferenceEntry(
        "UNE 1:1990", "Señalización anterior", "1990-01-01", "Anulada", "Accesibilidad",
        "https://www.une.org/norma-2",
    ),
)


class ReferenceCatalogueTests(unittest.TestCase):
    """Valida las fuentes reales sin perder revisiones ni enlaces distintos."""

    def test_real_catalogues_keep_official_metadata_and_same_code_different_entries(self):
        """Lee NTP numericamente y conserva las dos fichas del proyecto UNE repetido."""
        ntps = load_reference_entries("NTP")
        unes = load_reference_entries("UNE")
        numbers = [int(entry.code.split()[1]) for entry in ntps]
        self.assertEqual(numbers, sorted(numbers))
        self.assertTrue(any(entry.code == "NTP 495" and entry.pdf_url for entry in ntps))
        projects = [entry for entry in unes if entry.code == "UNE-EN 61076-4-100:2001"]
        self.assertEqual(len(projects), 2)
        self.assertEqual(len({entry.url for entry in projects}), 2)
        self.assertTrue(any(
            entry.code == "UNE 149201:2008" and entry.status == "Anulada" for entry in unes
        ))

    def test_missing_corrupt_or_untrusted_catalogues_raise_explicit_errors(self):
        """No devuelve resultados vacios con apariencia de exito ante una fuente invalida."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ntp.csv"
            with self.assertRaisesRegex(ValueError, "No se pudo leer"):
                load_reference_entries("NTP", path)
            path.write_text("otra,cabecera\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "columnas"):
                load_reference_entries("NTP", path)
            for code, url in (("INVALIDO", "https://www.insst.es/ficha"), ("NTP 1", "file:///test")):
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(("codigo", "titulo", "anio", "url_ntp", "url_pdf"))
                    writer.writerow((code, "Título", "2000", url, ""))
                with self.subTest(code=code, url=url), self.assertRaises(ValueError):
                    load_reference_entries("NTP", path)


class ReferenceSearchTests(unittest.TestCase):
    """Comprueba el filtrado y la apertura sin navegar realmente ni editar el presupuesto."""

    def setUp(self):
        """Crea un escritorio de prueba independiente del proyecto."""
        self.application = QApplication.instance() or QApplication([])
        self.app = QWidget()
        self.app._open_web_page = Mock()
        self.addCleanup(self.app.close)

    def test_typing_filters_code_title_and_metadata_and_clears_hidden_selection(self):
        """Filtra inmediatamente sin tildes y no deja abrir una seleccion que ya no se ve."""
        window = ReferenceSearchWindowQt(self.app, "UNE", UNE_ENTRIES)
        window.query.setText("senalizacion 2020 vigente")
        self.assertEqual(window.table.rowCount(), 1)
        window.table.selectRow(0)
        self.assertIn("Señalización accesible", window.detail.toPlainText())
        window.open_selected()
        self.app._open_web_page.assert_called_once_with(
            UNE_ENTRIES[0].url, "Buscador de UNE", "Referencia abierta en el navegador.",
        )
        self.app._open_web_page.reset_mock()
        window.query.setText("sin coincidencias")
        self.assertEqual(window.table.rowCount(), 0)
        self.assertIsNone(window.selected_entry())
        self.assertFalse(window.open_button.isEnabled())
        self.assertEqual(window.detail.toPlainText().strip(), "")
        window.open_selected()
        self.app._open_web_page.assert_not_called()
        window.query.setText("anulada accesibilidad")
        self.assertEqual(window.table.rowCount(), 1)
        self.assertEqual(window.table.item(0, 0).text(), UNE_ENTRIES[1].code)
        window.query.setText("")
        self.assertEqual(window.table.rowCount(), 2)

    def test_ntp_and_une_queries_are_independent_without_a_pdf_button(self):
        """Mantiene ambos buscadores separados y solo ofrece abrir la ficha oficial."""
        ntp = ReferenceSearchWindowQt(self.app, "NTP", NTP_ENTRIES)
        une = ReferenceSearchWindowQt(self.app, "UNE", UNE_ENTRIES)
        ntp.query.setText("ntp 495")
        une.query.setText("2020")
        self.assertEqual(ntp.table.rowCount(), 1)
        self.assertEqual(une.table.rowCount(), 1)
        ntp.table.selectRow(0)
        for window in (ntp, une):
            self.assertEqual(window.open_button.text(), "Ver ficha oficial")
        ntp.query.setText("senalizacion 1982")
        ntp.table.selectRow(0)
        ntp.open_button.click()
        self.assertEqual(self.app._open_web_page.call_args.args[0], NTP_ENTRIES[0].url)
        self.assertEqual(une.query.text(), "2020")
