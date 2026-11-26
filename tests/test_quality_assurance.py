"""Flujo de revisión, inserción y exportación del plan de calidad."""
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from budget_core import ProjectItem
from budget_project import build_project_payload, decode_project_payload
from budget_qt_window import BudgetMainWindow
from odt_reports import _tag
from tools._library_history import read_revisions
from tools.quality_assurance.catalog import correct_legacy_slip_criteria, get_catalog_entry, load_catalog
from tools.quality_assurance.document import (
    LIBRARY_PATH, _control_details, _generic_control_details, _matrix_rows, write_document,
)
from tools.quality_assurance.plan import (
    SOURCE, TRIALS, applicable_trials, insert_budget, quantity_proposal, suggested_quantity, unit_snapshot,
)
from tools.quality_assurance.pricing import estimate_empty_price, price_candidates, price_catalog


class QualityAssuranceTests(unittest.TestCase):
    """Comprueba límites de frecuencia y el ciclo real de revisión."""

    def test_control_details_preserve_custom_text_and_do_not_invent_fields(self):
        """Conserva el detalle propio y no asigna fichas a nombres modificados."""
        from tools._library_editor import read_markdown

        generic = _generic_control_details(read_markdown(LIBRARY_PATH, "05_ensayos.md"))
        self.assertIsNotNone(generic)
        item = ProjectItem("H", "01#", "m³", "Hormigón", 12)
        base = {"unit_id": item.storage_id, "trial": "concrete",
                "name": TRIALS["concrete"].name, "count": 2, "price": ""}
        text = _control_details([base], {item.storage_id: item}, "€", generic)
        self.assertIn("Número de ensayos: 2", text)
        self.assertIn("Ensayos e inspecciones:", text)
        self.assertNotIn("Precio directo", text)
        for changed in (base | {"name": "Ensayo modificado"},
                        base | {"trial": "", "name": "Ensayo libre"}):
            self.assertNotIn("Ensayos e inspecciones:", _control_details(
                [changed], {item.storage_id: item}, "€", generic))
        custom = _control_details([base | {"details": "- Ámbito: Texto personalizado."}],
                                  {item.storage_id: item}, "€", generic)
        self.assertIn("Texto personalizado", custom)
        self.assertNotIn("Elementos y pavimentos de hormigón", custom)

    def test_generic_control_block_rejects_duplicate_and_invalid_identifiers(self):
        """No oculta errores en los identificadores editados."""
        for content in ("### concrete\n- Uno\n### concrete\n- Dos",
                        "### desconocido\n- Uno", "### concrete\n",
                        "Texto perdido\n### concrete\n- Uno"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                _generic_control_details('"""\n## Datos de controles genéricos\n' + content + '\n"""\n')

    def test_matrix_handles_unvalidated_rows_and_removed_units(self):
        """No presenta cantidades inválidas o desactualizadas como datos válidos."""
        item = ProjectItem("H", "01#", "m³", "Hormigón", 12)
        base = {"unit_id": item.storage_id, "snapshot": unit_snapshot(item), "trial": "",
                "name": "PAC-16 — Título personalizado", "frequency": "", "criteria": "",
                "count": "-1", "price": "incorrecto", "included": False}
        matrix = _matrix_rows([base, base | {"unit_id": "eliminada"}], {item.storage_id: item}, "€", {})
        self.assertIn("C-01", matrix[0][0])
        self.assertIn("PAC-16 — Título personalizado", matrix[0][1])
        self.assertIn("Frecuencia: Por definir.", matrix[0][2])
        self.assertIn("Criterio: Por definir.", matrix[0][2])
        self.assertIn("N.º por corregir: -1", matrix[0][3])
        self.assertIn("Precio por corregir: incorrecto", matrix[0][3])
        self.assertIn("C-02", matrix[1][0])
        self.assertIn("H — Hormigón", matrix[1][0])
        self.assertIn("Actualizar", matrix[1][3])
        self.assertNotIn("N.º:", matrix[1][3])

    def test_edited_control_details_and_price_links_in_both_formats(self):
        """Exporta campos editados, controles PAC y enlaces reales de la procedencia."""
        from tools._library_editor import _signature, read_markdown, write_markdown

        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"):
            window = BudgetMainWindow()
            try:
                archive = Path(folder) / "fichas.zip"
                shutil.copyfile(LIBRARY_PATH, archive)
                source = read_markdown(archive, "05_ensayos.md").replace(
                    "Ámbito: Elementos y pavimentos de hormigón.",
                    "Ámbito: Hormigón personalizado.",
                )
                write_markdown(archive, "05_ensayos.md", source, _signature(archive))
                window.library_sources["quality_assurance"] = str(archive)
                window.budget_items = [
                    ProjectItem("H", "01#", "m³", "Hormigón", 12),
                    ProjectItem("P", "01#", "m²", "Pavimento de adoquines", 100),
                ]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                concrete = next(row for row in tool.rows() if row["trial"] == "concrete")
                pac = next(row for row in tool.rows() if row["trial"] == "PAC-16")
                pending = next(row for row in tool.rows() if row["trial"] == "PAC-50")
                self.assertFalse(pending["included"])
                pac["included"] = True
                for kind in ("odt", "pdf"):
                    target = Path(folder) / f"detalle.{kind}"
                    window.report_settings.output_format = kind
                    write_document(window, target, [concrete, pac, pending])
                    if kind == "odt":
                        with ZipFile(target) as document:
                            xml = ET.fromstring(document.read("content.xml"))
                        text = " ".join(xml.itertext())
                        links = {node.get(_tag("xlink:href")) for node in xml.iter(_tag("text:a"))}
                        table_links = {
                            node.get(_tag("xlink:href"))
                            for table in xml.iter(_tag("table:table"))
                            for node in table.iter(_tag("text:a"))
                        }
                        self.assertIn(
                            "https://www.une.org/encuentra-tu-norma/busca-tu-norma/norma/?c=N0063378",
                            table_links,
                        )
                    else:
                        pages = PdfReader(target).pages
                        text = " ".join(page.extract_text() for page in pages)
                        links = {
                            str(action.get("/URI"))
                            for page in pages for annotation in page.get("/Annots", [])
                            if (action := annotation.get_object().get("/A")) and action.get("/URI")
                        }
                    text = " ".join(text.split())
                    for expected in ("Hormigón personalizado", "N.º: 2",
                                     "Base:", "Precio unitario:",
                                     "Referencia de precio", "Ensayos e inspecciones",
                                     "Registros"):
                        self.assertIn(expected, text)
                    self.assertIn(concrete["price_source"]["url"], links)
                    self.assertIn("https://www.une.org/encuentra-tu-norma/busca-tu-norma/norma/?c=N0063378", links)
                    matrix_text = text.split("8. MATRIZ DE CONTROLES DE CALIDAD DEL PROYECTO")[-1]
                    matrix_text = matrix_text.split("9. FICHAS TÉCNICAS DE ENSAYOS", 1)[0]
                    for expected in ("C-01", "C-02", "C-03", "Pavimentos Urbanos", "Frecuencia:",
                                     "Criterio:", "N.º:", "Base:", "Precio unitario:", "Referencia de precio:",
                                     "Ensayos e inspecciones:", "Registros:", "≥ 6%", "≤ 20-23 mm"):
                        self.assertIn(expected, matrix_text)
                    self.assertNotIn("PAC-16", text)
                    self.assertNotIn("PAC-50", text)
                    self.assertNotIn("pendiente de revisión", matrix_text.casefold())
                    self.assertNotIn("$", matrix_text)
                    sheet_text = text.split("9. FICHAS TÉCNICAS DE ENSAYOS")[-1]
                    for expected in ("ENS-19", "ENS-01", "ENS-02", "ENS-03",
                                     "C-03", "C-02", "no incorpora otro ensayo",
                                     "Parámetro medido", "Momento y punto de toma",
                                     "Motivo de rechazo", "Protocolo y contraprueba",
                                     "clase 1, 15 < Rd ≤ 35", "clase 2, 35 < Rd ≤ 45"):
                        self.assertIn(expected, sheet_text)
                    self.assertEqual(sheet_text.count("ENS-19 —"), 1)
                    self.assertNotIn("ENS-25 —", sheet_text)
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_slip_catalog_corrects_only_the_known_legacy_classification(self):
        """Corrige las clases del DB-SUA sin reemplazar criterios personalizados."""
        old = ("• Clase 1 (Rd ≤ 15): zonas interiores secas.\n"
               "• Clase 2 (15 < Rd ≤ 35): interiores húmedos.\n"
               "• Clase 3 (Rd > 45): exteriores, aceras, piscinas y rampas ≥ 6%.")
        corrected = correct_legacy_slip_criteria("ENS-19", old)
        self.assertIn("clase 0, Rd ≤ 15", corrected)
        self.assertIn("clase 1, 15 < Rd ≤ 35", corrected)
        self.assertIn("clase 2, 35 < Rd ≤ 45", corrected)
        self.assertEqual(correct_legacy_slip_criteria("ENS-19", "Criterio propio"), "Criterio propio")
        self.assertEqual(correct_legacy_slip_criteria("ENS-20", old), old)
        self.assertEqual(get_catalog_entry("ENS-19").criteria, corrected)

    def test_legacy_slip_review_requires_confirmation_after_correction(self):
        """Una revisión guardada con la clasificación antigua se debe confirmar de nuevo."""
        from tools.quality_assurance.qt_window import QualityWindow

        old = ("• Clase 1 (Rd ≤ 15): zonas interiores secas.\n"
               "• Clase 2 (15 < Rd ≤ 35): interiores húmedos.\n"
               "• Clase 3 (Rd > 45): exteriores, aceras, piscinas y rampas ≥ 6%.")
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"):
            window = BudgetMainWindow()
            dialogs = []
            try:
                window.budget_items = [ProjectItem("P", "01#", "m²", "Pavimento de adoquines", 100)]
                first = QualityWindow(window)
                dialogs.append(first)
                row = first.rows()[0] | {"trial": "ENS-19", "criteria": old}
                window.quality_assurance_state = {"rows": [row]}
                reopened = QualityWindow(window)
                dialogs.append(reopened)
                self.assertFalse(reopened.reviewed)
                self.assertIn("clase 1, 15 < Rd ≤ 35", reopened.rows()[0]["criteria"])
                self.assertEqual(window.quality_assurance_state["rows"][0]["criteria"], old)
            finally:
                for dialog in dialogs:
                    dialog.close()
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_surface_frequency(self):
        """Respeta la frontera de 500 m² y no convierte toneladas a superficie."""
        for unit, measure, count in (("m²", 500, 1), ("m2", 500.01, 2), ("t", 500, None)):
            with self.subTest(unit=unit, measure=measure):
                item = ProjectItem("P", "01#", unit, "Mezcla bituminosa", measure)
                self.assertEqual(suggested_quantity(item, "asphalt"), count)

    def test_explicit_frequency_requires_a_complete_compatible_basis(self):
        """Calcula intervalos conservadores y rechaza condiciones o unidades desconocidas."""
        for unit, measure, frequency, expected in (
            ("m³", 250, "1 cada 100 - 250 m3", 3),
            ("m3", 200, "2 ensayos cada 100-250 m³", 4),
            ("m²", 1000, "1 testigo cada 1.000-2.000 m2 o fracción", 1),
            ("m", 100.01, "cada 50 m lineales", 3),
            ("t", 1000, "1 cada 1.000 m²", None),
            ("m", 100, "1 cada 50 m de zanja y por tongada", None),
            ("m²", 1000, "cada 500 m² (mín. 1/día)", None),
            ("m²", 1000, "1 cada 500 m²; placa cada 2.000 m²", None),
            ("m²", 0, "1 cada 500 m²", None),
            ("m²", 100, "1 cada 0 m²", None),
        ):
            with self.subTest(unit=unit, measure=measure, frequency=frequency):
                item = ProjectItem("P", "01#", unit, "Unidad de obra", measure)
                self.assertEqual(suggested_quantity(item, "", frequency), expected)

    def test_partial_controls_use_only_compatible_measurements(self):
        """Desglosa ensayos medibles sin confundir volúmenes, superficies o partidas auxiliares."""
        for measure, count in ((343.4, 1), (2500, 1), (2500.01, 2)):
            item = ProjectItem("EXC", "01#", "m3", "Excavación apertura de caja", measure)
            self.assertIn("PAC-06", applicable_trials(item))
            self.assertIn("soil_identification", applicable_trials(item))
            self.assertEqual(suggested_quantity(item, "soil_identification"), count)
        surface = ProjectItem("ZAH", "01#", "m²", "Subbase de zahorra", 2501)
        self.assertIn("subbase_density", applicable_trials(surface))
        self.assertIn("subbase_plate", applicable_trials(surface))
        self.assertEqual(suggested_quantity(surface, "subbase_density"), 6)
        self.assertEqual(suggested_quantity(surface, "subbase_plate"), 2)
        volume = ProjectItem("ZAH", "01#", "m³", "Subbase de zahorra", 103.03)
        self.assertNotIn("subbase_density", applicable_trials(volume))
        self.assertNotIn("subbase_plate", applicable_trials(volume))
        for item in (
            ProjectItem("CORTE", "01#", "m", "Corte pavimento asfáltico c/disco", 55),
            ProjectItem("RCD", "01#", "m3", "Tratamiento de RCS tierras de excavación", 17.17),
            ProjectItem("SS", "SS#", "u", "Estructura metálica de protección", 1, source="SS:01"),
        ):
            with self.subTest(summary=item.summary):
                self.assertEqual(applicable_trials(item), [])

    def test_concrete_delivery_estimates_and_provisional_default(self):
        """Estima entregas de 6 m³ solo con un volumen o espesor identificable."""
        for unit, measure, summary, text, count in (
            ("m³", 6, "Hormigón", "", 1),
            ("m3", 6.01, "Hormigón", "", 2),
            ("m²", 185.15, "Solera de hormigón de 10 cm", "Hormigón de 10 cm de espesor.", 4),
            ("m2", 211.85, "Pavimento de hormigón", "Hormigón de hasta 10cm de espesor.", 4),
            ("m2", 185.15, "Solera de hormigón de 20 cm", "Hormigón de 10 cm de espesor.", 1),
            ("m²", 100, "Hormigón", "Espesor de 5 a 10 cm.", 1),
            ("u", 100, "Hormigón", "", 1),
        ):
            item = ProjectItem("H", "01#", unit, summary, measure, text=text)
            actual, basis = quantity_proposal(item, "concrete", TRIALS["concrete"].frequency)
            with self.subTest(unit=unit, summary=summary, text=text):
                self.assertEqual(actual, count)
                self.assertIn("entrega" if unit == "m3" or unit == "m³" or count == 4 else "provisional", basis)
        item = ProjectItem("H", "01#", "m3", "Hormigón", 12.01)
        self.assertEqual(quantity_proposal(item, "concrete", "1 cada 50 m³")[0], 1)

    def test_unchecked_quantities_and_concrete_basis_are_persisted(self):
        """Calcula filas no marcadas e invalida la revisión si cambia el espesor."""
        from tools.quality_assurance.qt_window import QualityWindow

        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ) as question:
            window = BudgetMainWindow()
            dialog = None
            try:
                work = ProjectItem("SOL", "01#", "m²", "Solera de hormigón", 185.15,
                                   text="Solera de 10 cm de espesor.")
                window.budget_items = [work]
                dialog = QualityWindow(window)
                rows = dialog.rows()
                concrete = next(row for row in rows if row.get("trial") == "concrete")
                pac = next(row for row in rows if row.get("trial") == "PAC-16")
                self.assertEqual((concrete["count"], pac["count"], pac["included"]), ("4", "4", False))
                self.assertIn("6 m³", concrete["quantity_source"]["basis"])
                self.assertTrue(all(row["count"] for row in rows))
                pac_index = next(i for i, row in enumerate(rows) if row.get("trial") == "PAC-16")
                dialog.table.item(pac_index, 0).setCheckState(Qt.CheckState.Checked)
                dialog.review()
                self.assertIn("no se dupliquen", question.call_args.args[2])
                dialog.table.item(pac_index, 0).setCheckState(Qt.CheckState.Unchecked)
                dialog.review()
                self.assertTrue(dialog.export_button.isEnabled())
                work.text = "Solera de 20 cm de espesor."
                window.refresh_budget_tree()
                self.assertFalse(dialog.export_button.isEnabled())
                with self.assertRaisesRegex(ValueError, "base de cálculo"):
                    insert_budget(window, [concrete])
                dialog.suggest()
                rows = dialog.rows()
                self.assertEqual(next(row for row in rows if row.get("trial") == "concrete")["count"], "7")
                pac = next(row for row in rows if row.get("trial") == "PAC-16")
                self.assertEqual((pac["count"], pac["included"]), ("7", False))
                dialog.review()
                dialog.insert()
                inserted = next(item for item in window.budget_items if item.source == SOURCE)
                self.assertIn("entrega estimada de 6 m³", inserted.text)
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "p.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"], dialog.rows())
            finally:
                if dialog is not None:
                    dialog.close()
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_partial_control_review_warns_about_overlap(self):
        """Conserva el PAC y pide revisar el posible solapamiento de sus precios."""
        from tools.quality_assurance.qt_window import QualityWindow

        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ) as question:
            window = BudgetMainWindow()
            dialog = None
            try:
                work = ProjectItem("EXC", "01#", "m3", "Excavación apertura de caja", 343.4)
                window.budget_items = [work]
                dialog = QualityWindow(window)
                rows = dialog.rows()
                partial = next(i for i, row in enumerate(rows) if row.get("trial") == "soil_identification")
                parent = next(i for i, row in enumerate(rows) if row.get("trial") == "PAC-06")
                row = rows[partial]
                self.assertEqual((row["count"], row["included"]), ("1", False))
                self.assertIn("PAC-06", row["details"])
                self.assertIn("PG-3", row["reference"])
                self.assertEqual(row["price_source"]["code"], "C10EF100")
                dialog.table.item(partial, 0).setCheckState(Qt.CheckState.Checked)
                dialog.table.item(parent, 0).setCheckState(Qt.CheckState.Checked)
                dialog.table.item(parent, 4).setText("1")
                dialog.review()
                self.assertTrue(dialog.reviewed)
                self.assertIn("no se dupliquen", question.call_args.args[2])
                window.budget_items = [ProjectItem("ZAH", "01#", "m²", "Subbase de zahorra", 2501)]
                dialog.suggest()
                rows = dialog.rows()
                generic = next(i for i, row in enumerate(rows) if row.get("trial") == "plate")
                partial = next(i for i, row in enumerate(rows) if row.get("trial") == "subbase_plate")
                dialog.table.item(generic, 4).setText("1")
                dialog.table.item(partial, 0).setCheckState(Qt.CheckState.Checked)
                dialog.review()
                self.assertIn("no se dupliquen", question.call_args.args[2])
            finally:
                if dialog is not None:
                    dialog.close()
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_recalculate_saved_rows_and_changed_measurement(self):
        """Recalcula lo interpretable y marca como provisional el valor desconocido."""
        from tools.quality_assurance.qt_window import QualityWindow

        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            window = BudgetMainWindow()
            dialog = None
            reopened = None
            try:
                work = ProjectItem("ZAN", "01#", "m³", "Relleno de zanja compactada", 450)
                window.budget_items = [work]
                dialog = QualityWindow(window)
                density = next(i for i, row in enumerate(dialog.rows()) if row.get("trial") == "density")
                dialog.table.item(density, 3).setText("2 ensayos cada 100 - 250 m³")
                dialog.table.item(density, 4).setText("99")
                dialog.table.item(density, 6).setText("33")
                dialog.suggest_button.click()
                row = next(row for row in dialog.rows() if row.get("trial") == "density")
                self.assertEqual((row["count"], row["price"]), ("10", "33"))
                dialog.review()
                self.assertTrue(dialog.reviewed)
                reopened = QualityWindow(window)
                self.assertEqual(next(row for row in reopened.rows() if row.get("trial") == "density")["count"], "10")
                work.measurement = 575
                dialog.suggest()
                row = next(row for row in dialog.rows() if row.get("trial") == "density")
                self.assertEqual((row["count"], row["snapshot"][-1]), ("12", 575))
                density = next(i for i, row in enumerate(dialog.rows()) if row.get("trial") == "density")
                dialog.table.item(density, 3).setText("1 cada 50 m de zanja y por tongada")
                dialog.table.item(density, 4).setText("5")
                dialog.suggest()
                row = next(row for row in dialog.rows() if row.get("trial") == "density")
                self.assertEqual(row["count"], "1")
                self.assertIn("provisional", row["quantity_source"]["basis"])
                work.measurement = 600
                dialog.suggest()
                self.assertEqual(next(row for row in dialog.rows() if row.get("trial") == "density")["count"], "1")
                self.assertFalse(dialog.reviewed)
            finally:
                if dialog is not None:
                    dialog.close()
                if reopened is not None:
                    reopened.close()
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_workbook_catalog_and_unit_matches(self):
        """Carga los catálogos PAC y propone controles para unidades adicionales."""
        entries = load_catalog()
        by_id = {entry.identifier: entry for entry in entries}
        self.assertEqual(sum(identifier.startswith("PAC-") for identifier in by_id), 68)
        self.assertEqual(sum(identifier.startswith("ENS-") for identifier in by_id), 31)
        self.assertIn("Zahorras", by_id["PAC-30"].label)
        self.assertIn("UNE-EN 16165", by_id["ENS-19"].reference)
        for summary, expected in (
            ("Subbase granular de zahorra artificial", "PAC-30"),
            ("Valla metálica de protección de baja altura", "PAC-56"),
            ("Mezcla bituminosa en caliente", "PAC-34"),
            ("H. bituminoso AC16 SURF S c/áridos", "PAC-34"),
            ("Exc. zanja tierra", "PAC-06"),
            ("Solado bald. terrazo", "PAC-50"),
            ("Señalización marca vial en calzada", "PAC-42"),
        ):
            item = ProjectItem("P", "01#", "m²", summary, 100)
            with self.subTest(summary=summary):
                self.assertIn(expected, applicable_trials(item))
        asphalt = ProjectItem("P", "01#", "m²", "H. bituminoso AC16 SURF S c/áridos", 750)
        self.assertIn("asphalt", applicable_trials(asphalt))
        self.assertIn("PAC-29", applicable_trials(asphalt))
        self.assertNotIn("PAC-20", applicable_trials(asphalt))
        for summary in ("Levantado de solado con baldosas", "Levantado de bordillo viejo"):
            self.assertEqual(applicable_trials(ProjectItem("P", "01#", "m²", summary, 10)), [])
        self.assertNotIn("PAC-51", applicable_trials(
            ProjectItem("P", "01#", "u", "Arqueta de registro de alumbrado público", 1)))
        with ZipFile(LIBRARY_PATH) as archive:
            for name in ("02_normativa.md", "05_ensayos.md", "06_no_conformidades.md",
                         "07_presupuesto.md", "08_catalogo_pac.md", "09_ensayos_clave.md"):
                source = (LIBRARY_PATH.parent / name).read_bytes().replace(b"\r\n", b"\n")
                content = archive.read(name)
                self.assertEqual(content, source)
                revisions, _remainder = read_revisions(content.decode("utf-8-sig"))
                self.assertEqual(len(revisions), 5 if name.startswith("05_") else
                                 4 if name.startswith("09_") else
                                 3 if name.startswith(("06_", "08_")) else 2)

    def test_manual_catalog_add(self):
        """Añade una ficha seleccionada con su referencia y detalle."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"):
            window = BudgetMainWindow()
            try:
                window.budget_items = [
                    ProjectItem("FENCE", "01#", "m", "Valla metálica de protección", 10)
                ]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                tool.catalog.setCurrentIndex(tool.catalog.findData("PAC-68"))
                tool.add_catalog()
                row = tool.rows()[-1]
                self.assertEqual(row["trial"], "PAC-68")
                self.assertIn("LOE", row["reference"])
                self.assertIn("Ensayos e inspecciones", row["details"])
                self.assertEqual(tool.table.item(tool.table.rowCount() - 1, 7).text(), row["reference"])
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_bcca_price_selection_and_provenance(self):
        """La selección es explícita y no conserva una referencia para un precio manual."""
        self.assertEqual([(entry.code, entry.price) for entry in price_candidates("", "18ALF00010")],
                         [("18ALF00010", 44.0)])
        self.assertFalse(any(entry.unit.casefold() not in {"u", "ud"} for entry in price_catalog()))
        self.assertFalse(any(entry.code == "18ASS000201" for entry in price_catalog()))
        self.assertFalse(price_candidates("Densidad in situ"))
        self.assertEqual({entry.code for entry in price_candidates(
            "Probetas y consistencia (cono de Abrams)")}, {"18HHL00010", "18HHL00020"})
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch(
            "tools.quality_assurance.qt_window.QInputDialog.getText", return_value=("18ALF00010", True)
        ), patch(
            "tools.quality_assurance.qt_window.QInputDialog.getItem",
            return_value=("18ALF00010 — Análisis granulométrico de suelos — 44 €/ud (BCCA 2023)", True)
        ), patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question:
            window = BudgetMainWindow()
            try:
                window.budget_items = [ProjectItem("SUELO", "01#", "m²", "Subbase de zahorra", 50)]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                tool.table.setCurrentCell(0, 2)
                tool.table.item(0, 6).setText("99")
                tool.choose_price()
                self.assertEqual(tool.rows()[0]["price"], "99")
                self.assertNotIn("price_source", tool.rows()[0])
                question.return_value = QMessageBox.StandardButton.Yes
                tool.choose_price()
                row = tool.rows()[0]
                self.assertEqual((row["price"], row["price_source"]["code"]), ("44.0", "18ALF00010"))
                tool.table.item(0, 6).setText("45")
                self.assertNotIn("price_source", tool.rows()[0])
                tool.choose_price()
                tool.table.item(0, 4).setText("1")
                tool.review()
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "p.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"][0]["price_source"]["code"], "18ALF00010")
                report = Path(folder) / "precio.odt"
                write_document(window, report, [row for row in tool.rows() if row["included"]])
                with ZipFile(report) as archive:
                    text = " ".join(ET.fromstring(archive.read("content.xml")).itertext())
                self.assertIn("18ALF00010", text)
                self.assertIn("Precio unitario", text)
                window.report_settings.currency = "USD"
                with self.assertRaisesRegex(ValueError, "euros"):
                    insert_budget(window, [tool.rows()[0]])
                window.report_settings.currency = "EUR"
                tool.insert()
                inserted = next(item for item in window.budget_items if item.source == SOURCE)
                self.assertIn("18ALF00010", inserted.text)
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_estimates_only_for_known_unedited_empty_prices(self):
        """No extrapola precios a controles desconocidos, modificados o en otra moneda."""
        from tools.quality_assurance.plan import TRIALS

        row = {"trial": "concrete", "name": TRIALS["concrete"].name, "price": ""}
        estimated = estimate_empty_price(row, "EUR")
        self.assertEqual(estimated["price"], "168.29")
        self.assertIn("151,26", estimated["price_source"]["summary"])
        self.assertIn("borm.es", estimated["price_source"]["url"])
        for changed, currency in (
            (row | {"price": "25"}, "EUR"),
            (row | {"name": "Otro control"}, "EUR"),
            (row | {"trial": "PAC-61"}, "EUR"),
            (row, "USD"),
        ):
            self.assertEqual(estimate_empty_price(changed, currency), changed)

    def test_bc3_packages_preserve_values_and_document_scope(self):
        """Suma partidas unitarias identificadas sin atribuirles un control completo."""
        name = get_catalog_entry("PAC-39").label
        row = {"trial": "PAC-39", "name": name, "price": ""}
        estimated = estimate_empty_price(row, "EUR")
        source = estimated["price_source"]
        self.assertEqual(estimated["price"], "274.15")
        self.assertIn("CM1C08S110", source["summary"])
        self.assertIn("88.72 EUR/ud", source["summary"])
        self.assertIn("C06S060", source["summary"])
        self.assertIn("185.43 EUR/ud", source["summary"])
        self.assertIn("área 1", source["summary"])
        self.assertIn("no incluye", source["summary"])
        for unchanged, currency in (
            (row | {"price": "120"}, "EUR"),
            (row | {"name": "Otro control"}, "EUR"),
            (row, "USD"),
        ):
            self.assertEqual(estimate_empty_price(unchanged, currency), unchanged)
        name = get_catalog_entry("ENS-04").label
        self.assertEqual(estimate_empty_price(
            {"trial": "ENS-04", "name": name, "price": ""}, "EUR"
        )["price"], "180.00")
        name = get_catalog_entry("ENS-22").label
        bcca = estimate_empty_price({"trial": "ENS-22", "name": name, "price": ""}, "EUR")
        self.assertEqual(bcca["price"], "50.00")
        self.assertIn("BCCA Andalucía 2023", bcca["price_source"]["summary"])
        self.assertIn("no incluye cuadrícula", bcca["price_source"]["summary"])
        name = get_catalog_entry("ENS-25").label
        self.assertEqual(estimate_empty_price(
            {"trial": "ENS-25", "name": name, "price": ""}, "EUR"
        )["price"], "")

    def test_bc3_estimate_is_preserved_in_project_budget_and_report(self):
        """Propaga las partidas, sus bases y limitaciones tras la revisión."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            window = BudgetMainWindow()
            try:
                window.budget_items = [ProjectItem("S", "01#", "m", "Colector de saneamiento", 25)]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                index = next(i for i, row in enumerate(tool.rows()) if row.get("trial") == "PAC-39")
                self.assertEqual(tool.rows()[index]["price"], "274.15")
                self.assertIn("área 1", tool.table.item(index, 6).toolTip())
                tool.table.item(index, 0).setCheckState(Qt.CheckState.Checked)
                tool.table.item(index, 4).setText("1")
                tool.review()
                row = tool.rows()[index]
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "p.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"][index]["price_source"], row["price_source"])
                report = Path(folder) / "bc3.odt"
                write_document(window, report, [row])
                with ZipFile(report) as archive:
                    text = " ".join(ET.fromstring(archive.read("content.xml")).itertext())
                for expected in ("CM1C08S110", "C06S060", "área 1", "ovalización"):
                    self.assertIn(expected, text)
                tool.insert()
                item = next(item for item in window.budget_items if item.source == SOURCE)
                self.assertEqual(item.unit_price, 274.15)
                self.assertIn("CM1C08S110", item.text)
                self.assertIn("C06S060", item.text)
                self.assertIn("ovalización", item.text)
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_estimated_prices_restore_review_and_follow_exports(self):
        """Rellena huecos sin pisar precios ni revisiones ya insertadas y conserva el alcance."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            window = BudgetMainWindow()
            try:
                work = ProjectItem("ZAH", "01#", "m²", "Subbase de zahorra", 50)
                window.budget_items = [work]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                row = tool.rows()[0]
                self.assertEqual((row["trial"], row["price"]), ("plate", "120.00"))
                self.assertIn("desplazamiento", tool.table.item(0, 6).toolTip())
                tool.table.item(0, 4).setText("1")
                tool.review()
                saved = window.quality_assurance_state["rows"]
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "p.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"][0]["price_source"], saved[0]["price_source"])
                report = Path(folder) / "estimacion.odt"
                write_document(window, report, [row for row in tool.rows() if row["included"]])
                with ZipFile(report) as archive:
                    text = " ".join(ET.fromstring(archive.read("content.xml")).itertext())
                self.assertIn("Estimación orientativa", text)
                self.assertIn("geocesa.com", text)
                self.assertIn("elemento reactivo", text)
                window.report_settings.currency = "USD"
                with self.assertRaisesRegex(ValueError, "euros"):
                    write_document(window, report, [row for row in tool.rows() if row["included"]])
                with self.assertRaisesRegex(ValueError, "euros"):
                    insert_budget(window, [row for row in tool.rows() if row["included"]])
                window.report_settings.currency = "EUR"
                tool.insert()
                inserted = next(item for item in window.budget_items if item.source == SOURCE)
                self.assertIn("Estimación orientativa", inserted.text)
                self.assertIn("geocesa.com", inserted.text)
                self.assertIn("desplazamiento", inserted.text)
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_saved_review_gains_estimate_only_before_insertion(self):
        """Pide confirmar de nuevo las revisiones antiguas y respeta precios editados."""
        from tools.quality_assurance.qt_window import QualityWindow

        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"):
            window = BudgetMainWindow()
            dialogs = []
            try:
                work = ProjectItem("ZAH", "01#", "m²", "Subbase de zahorra", 50)
                window.budget_items = [work]
                dialog = QualityWindow(window)
                dialogs.append(dialog)
                saved = dialog.rows()
                saved[0]["price"] = ""
                saved[0].pop("price_source")
                saved[1]["price"] = "33"
                window.quality_assurance_state = {"rows": saved}
                reopened = QualityWindow(window)
                dialogs.append(reopened)
                self.assertEqual(reopened.rows()[0]["price"], "120.00")
                self.assertEqual(reopened.rows()[1]["price"], "33")
                self.assertFalse(reopened.reviewed)
                window.budget_items.append(ProjectItem("CC-001", "CALIDAD#", "ud", "Ensayo", source=SOURCE))
                inserted = QualityWindow(window)
                dialogs.append(inserted)
                self.assertEqual(inserted.rows()[0]["price"], "")
                self.assertEqual(inserted.rows()[1]["price"], "33")
            finally:
                for dialog in dialogs:
                    dialog.close()
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_export_contains_project_controls_and_related_tests(self):
        """Incluye ambas clases de fila y señala las unidades desactualizadas."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ), patch.object(QMessageBox, "information"):
            window = BudgetMainWindow()
            try:
                work = ProjectItem("ASF", "01#", "m²", "Mezcla bituminosa", 750)
                excavation = ProjectItem("EXC", "01#", "m³", "Excavación de tierras", 400)
                window.budget_items = [work, excavation]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                tool.review()
                excavation.measurement = 800
                window.refresh_budget_tree()
                self.assertTrue(tool.export_button.isEnabled())
                for kind in ("odt", "pdf"):
                    target = Path(folder) / f"pac.{kind}"
                    with patch("tools.quality_assurance.qt_window.get_document_save_path",
                               return_value=(target, kind)):
                        tool.export()
                    if kind == "odt":
                        with ZipFile(target) as archive:
                            xml = ET.fromstring(archive.read("content.xml"))
                        text = " ".join(xml.itertext())
                        tables = list(xml.iter(_tag("table:table")))
                        self.assertEqual(len(tables), 1)
                    else:
                        text = " ".join(page.extract_text() for page in PdfReader(target).pages)
                    text = " ".join(text.split())
                    matrix = text.split("8. MATRIZ DE CONTROLES DE CALIDAD DEL PROYECTO")[-1]
                    matrix, sheets = matrix.split("9. FICHAS TÉCNICAS DE ENSAYOS", 1)
                    self.assertIn("C-01", matrix)
                    self.assertIn("C-02", matrix)
                    self.assertIn("Actualizar los datos de la unidad y su cantidad", matrix)
                    self.assertNotIn("pendiente de revisión", matrix.casefold())
                    self.assertNotIn("PAC-33", text)
                    self.assertNotIn("PAC-34", text)
                    self.assertIn("ENS-09", sheets)
                    self.assertIn("ENS-14", sheets)
                    self.assertIn("no incorpora otro ensayo", sheets)
                    self.assertEqual(sheets.count("ENS-14 —"), 1)
                    self.assertNotIn("PAC-39", text)
                    self.assertNotIn("Matriz_Maestra_PAC", text)
                    self.assertNotIn("Detalle_Ensayos_Clave", text)
                    self.assertNotIn("Plan_Aseguramiento_Calidad_CTE_CE_PG3-v2.xlsx", text)
                self.assertFalse(any(item.source == SOURCE for item in window.budget_items))
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_custom_quality_library_without_new_markers_remains_compatible(self):
        """Añade los apartados dinámicos sin sobrescribir una biblioteca personalizada antigua."""
        from tools._library_editor import _signature, read_markdown, write_markdown

        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"):
            window = BudgetMainWindow()
            try:
                archive = Path(folder) / "personalizada.zip"
                shutil.copyfile(LIBRARY_PATH, archive)
                for name, title in (("08_catalogo_pac.md", "Control personalizado"),
                                    ("09_ensayos_clave.md", "Ensayos personalizados")):
                    write_markdown(archive, name, f"# {title}\n\nTexto propio.\n", _signature(archive))
                window.library_sources["quality_assurance"] = str(archive)
                window.budget_items = [ProjectItem("ASF", "01#", "m²", "Mezcla bituminosa", 750)]
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                report = Path(folder) / "personalizado.odt"
                write_document(window, report, tool.rows())
                with ZipFile(report) as document:
                    text = " ".join(ET.fromstring(document.read("content.xml")).itertext())
                for expected in ("Control personalizado", "Ensayos personalizados", "Texto propio.",
                                 "C-02", "ENS-14"):
                    self.assertIn(expected, text)
                self.assertEqual(read_markdown(archive, "08_catalogo_pac.md"),
                                 "# Control personalizado\n\nTexto propio.\n")
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()

    def test_review_budget_and_exports(self):
        """Valida la revisión, la inserción única y ambos formatos editados."""
        qt = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as folder, patch(
            "budget_qt_window.user_data_directory", return_value=Path(folder)
        ), patch("budget_qt_window.apply_theme"), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ), patch.object(
            QMessageBox, "information"
        ):
            window = BudgetMainWindow()
            try:
                work = ProjectItem("ASF", "01#", "m²", "Mezcla bituminosa", 750, 20)
                window.budget_items = [work]
                window.chapter_order = ["01#", "09#"]
                window.chapter_titles["09#"] = "Control de Calidad"
                window._open_tool("quality_assurance")
                tool = window._project_windows[-1]
                self.assertEqual(tool.table.rowCount(), 3)
                self.assertEqual(tool.table.item(0, 4).text(), "2")
                self.assertEqual(tool.table.item(1, 0).checkState().name, "Unchecked")
                self.assertGreaterEqual(tool.catalog.findData("PAC-68"), 0)
                self.assertFalse(tool.insert_button.isEnabled())
                tool.review()
                self.assertTrue(tool.export_button.isEnabled())
                self.assertTrue(tool.insert_button.isEnabled())
                tool.table.item(0, 6).setText("120,50")
                tool.table.item(2, 0).setCheckState(Qt.CheckState.Checked)
                tool.table.item(2, 4).setText("1")
                tool.table.item(2, 6).setText("80")
                self.assertFalse(tool.export_button.isEnabled())
                tool.review()
                self.assertTrue(tool.insert_button.isEnabled())
                tool.insert_button.click()
                inserted, pac_control = [item for item in window.budget_items if item.source == SOURCE]
                self.assertEqual((inserted.chapter, inserted.measurement, inserted.unit_price), ("09#", 2, 120.5))
                self.assertIn("PG-3 Art. 542", pac_control.text)
                self.assertIn("Detalle del control", pac_control.text)
                self.assertFalse(tool.insert_button.isEnabled())
                restored = decode_project_payload(build_project_payload(window), Path(folder) / "proyecto.json",
                                                  window.report_settings)
                self.assertEqual(restored.quality_assurance["rows"], tool.rows())
                self.assertEqual([item.measurement for item in restored.items if item.source == SOURCE], [2, 1])
                with self.assertRaisesRegex(ValueError, "ya se ha incorporado"):
                    insert_budget(window, tool.rows())
                archive_path = Path(folder) / "fichas.zip"
                shutil.copyfile(LIBRARY_PATH, archive_path)
                window.library_sources["quality_assurance"] = str(archive_path)
                tool.open_library()
                self.assertEqual(tool.editor.members.count(), 9)
                tool.editor.members.setCurrentRow(0)
                tool.editor.author.setText("Autor de prueba")
                tool.editor.editor.appendPlainText("Texto revisado en la ficha.")
                self.assertTrue(tool.editor.save())
                for kind in ("odt", "pdf"):
                    with self.subTest(kind=kind):
                        window.report_settings.output_format = kind
                        target = Path(folder) / f"calidad.{kind}"
                        write_document(window, target, [row for row in tool.rows() if row["included"]])
                        if kind == "odt":
                            with ZipFile(target) as archive:
                                xml = ET.fromstring(archive.read("content.xml"))
                            self.assertEqual(len(list(xml.iter(_tag("table:table")))), 1)
                            links = {node.get(_tag("xlink:href")) for node in xml.iter(_tag("text:a"))}
                            text = " ".join(xml.itertext())
                        else:
                            pages = PdfReader(target).pages
                            text = " ".join(page.extract_text() for page in pages)
                            links = {
                                str(action.get("/URI"))
                                for page in pages for annotation in page.get("/Annots", [])
                                if (action := annotation.get_object().get("/A")) and action.get("/URI")
                            }
                        self.assertIn("https://www.boe.es/buscar/act.php?id=BOE-A-2003-22787", links)
                        normalized = " ".join(text.split())
                        for expected in ("ANEJO Nº 17", "Tabla 1.", "ASF", "321,00", "Texto revisado en la ficha.",
                                         "Base:",
                                         "6. GESTIÓN DE NO CONFORMIDADES",
                                         "8. MATRIZ DE CONTROLES DE CALIDAD DEL PROYECTO",
                                         "9. FICHAS TÉCNICAS DE ENSAYOS",
                                         "Ensayos e inspecciones", "ENS-14"):
                            self.assertIn(expected, normalized)
                        self.assertNotIn("CAT?LOGO", normalized)
                        self.assertNotIn("Estado en obra: Pendiente", normalized)
                work.measurement = 800
                window.refresh_budget_tree()
                self.assertFalse(tool.export_button.isEnabled())
            finally:
                window.is_dirty = False
                window.close()
                qt.processEvents()
