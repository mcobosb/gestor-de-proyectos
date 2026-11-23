"""Pruebas de la entrega documental, la carátula ODT y el índice."""
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
from urllib.parse import unquote, urljoin
from xml.etree import ElementTree as ET
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QLockFile, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from budget_qt_window import BudgetMainWindow
from budget_reports import ReportSettings
from odt_reports import _new_report, _tag
from tools.document_structure.catalog import (
    DOCUMENTATION_PROCEDURE_FIELDS, TEMPLATES, template_paths,
)
from tools.document_structure.generation import (
    DOCUMENT_FOLDERS, MEMORY_DOCUMENT_TITLE,
    TECHNICAL_DOCUMENTS_FOLDER, default_master_name,
    generate_project_package, generated_at, receipt_path,
    suggested_document_directory,
)
from tools.document_structure.office import MASTER_LOGO, MASTER_TEMPLATE
from tools.document_structure.qt_window import DocumentStructureWindow
from qt_localization import get_document_save_path


class DocumentStructureTests(unittest.TestCase):
    """Comprueba la generación de documentos técnicos y del maestro ODM."""

    def test_annex_covers_belong_to_master_and_keep_template_pages(self) -> None:
        """Las portadas se separan de los vínculos y mantienen la cabecera y el pie."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "plantilla_maestro.odt"
            document = _new_report("Plantilla del maestro", "Proyecto de prueba", ReportSettings())
            document.table_references = False
            for name, page in (("TestTraceability", "Standard"), ("TestIndex", "Section1")):
                style = ET.SubElement(document.automatic_styles, _tag("style:style"), {
                    _tag("style:name"): name, _tag("style:family"): "paragraph",
                    _tag("style:master-page-name"): page,
                })
                ET.SubElement(style, _tag("style:paragraph-properties"), {
                    _tag("fo:break-before"): "page",
                })
            document.paragraph("HOJA DE TRAZABILIDAD", "TestTraceability")
            document.table(
                ["CÓDIGO", "DOCUMENTO", "FECHA EDICIÓN", "EDITADO", "FECHA COMPROBACIÓN", "VALIDADO"],
                [("",) * 6],
            )
            table = document.body[-1]
            header = table.find(_tag("table:table-header-rows"))
            table.insert(list(table).index(header), header[0])
            table.remove(header)
            document.section("ÍNDICE GENERAL DEL PROYECTO")
            document.paragraph("ÍNDICE GENERAL DEL PROYECTO", "TestIndex")
            document.write(template)
            settings = ReportSettings(
                master_template_path=str(template),
                master_logo_path=str(Path(__file__).resolve().parents[1] / "iconos" / "Arranque.jpg"),
            )
            app = SimpleNamespace(
                report_settings=settings, budget_items=[], planning_state={}, pptp_state={},
                _project_title=lambda: "Proyecto de prueba",
            )
            project = root / "proyecto.json"
            project.touch()
            plan = root / "Plano de situación.pdf"
            plan.write_bytes(b"plano externo")
            master, _ = generate_project_package(
                project, app, "Maestro.odm",
                [
                    {"kind": "tool", "number": "15", "title": "Justificación de precios"},
                    {"kind": "template", "number": "20", "title": "Conservación y mantenimiento",
                     "outline": ["Operaciones", "Periodicidad"]},
                ],
                [{"title": "Situación", "file": str(plan)}], include_pptp=True,
                memory_documents=[{"title": "Objeto", "outline": ["Alcance"]}],
            )
            with ZipFile(master) as archive:
                content = ET.fromstring(archive.read("content.xml"))
                styles = ET.fromstring(archive.read("styles.xml"))
            body = content.find(f"{_tag('office:body')}/{_tag('office:text')}")
            declared = {
                style.get(_tag("style:name")): style
                for style in content.iter(_tag("style:style"))
            }
            covers = [
                node for node in body
                if node.get(_tag("text:style-name"), "").startswith("ProjectAnnexCoverNumber")
            ]
            index_title = body.find(
                f"{_tag('text:p')}[@{_tag('text:style-name')}='ReportContentsTitle']",
            )
            self.assertEqual(index_title.text, "ÍNDICE")
            index_entries = [
                node for node in body
                if node.get(_tag("text:style-name"), "").startswith("PPTPContents")
            ]
            self.assertEqual(sum(
                len(list(entry.iter(_tag("text:bookmark-ref")))) for entry in index_entries
            ), 4)
            plan_entry = next(
                node for node in index_entries if "PLANO Nº 01" in "".join(node.itertext())
            )
            self.assertIsNotNone(plan_entry.find(_tag("text:a")))
            self.assertIsNone(plan_entry.find(".//" + _tag("text:bookmark-ref")))
            self.assertEqual(["".join(node.itertext()) for node in covers], ["ANEJO Nº 1", "ANEJO Nº 2"])
            for index, cover in enumerate(covers, 1):
                document_index = index + 1
                position = list(body).index(cover)
                title, page_break, section = list(body)[position + 1:position + 4]
                self.assertEqual(title.get(_tag("text:style-name")), "ProjectAnnexCoverTitle")
                self.assertEqual(
                    page_break.get(_tag("text:style-name")), f"ProjectDocumentPageBreak{document_index}",
                )
                self.assertIsNotNone(section.find(_tag("text:section-source")))
                bookmark = cover.find(_tag("text:bookmark"))
                self.assertEqual(
                    bookmark.get(_tag("text:name")), f"ProjectDocumentStart{document_index}",
                )
                self.assertIsNone(page_break.find(_tag("text:bookmark")))
                self.assertTrue(any(
                    reference.get(_tag("text:ref-name")) == bookmark.get(_tag("text:name"))
                    for entry in index_entries for reference in entry.iter(_tag("text:bookmark-ref"))
                ))
                self.assertFalse(any(
                    node.get(_tag("text:style-name"), "").startswith("ProjectAnnexCover")
                    for node in section.iter()
                ))
                number_style = declared[cover.get(_tag("text:style-name"))]
                self.assertEqual(
                    number_style.get(_tag("style:master-page-name")), f"ProjectDocumentPage{document_index}",
                )
                self.assertEqual(
                    number_style.find(_tag("style:paragraph-properties")).get(_tag("fo:break-before")),
                    "page",
                )
                for style, size in ((number_style, "26pt"), (declared["ProjectAnnexCoverTitle"], "32pt")):
                    self.assertEqual(
                        style.find(_tag("style:paragraph-properties")).get(_tag("fo:text-align")), "end",
                    )
                    self.assertEqual(
                        style.find(_tag("style:text-properties")).get(_tag("fo:font-size")), size,
                    )
                page = next(
                    node for node in styles.iter(_tag("style:master-page"))
                    if node.get(_tag("style:name")) == f"ProjectDocumentPage{document_index}"
                )
                template_page = next(styles.iter(_tag("style:master-page")))
                self.assertEqual(
                    ET.tostring(page.find(_tag("style:header"))),
                    ET.tostring(template_page.find(_tag("style:header"))),
                )
                self.assertIn(f"ANEJO Nº {index}", "".join(page.find(_tag("style:footer")).itertext()))
                self.assertIsNotNone(page.find(f".//{_tag('text:page-number')}"))
            for path in master.parent.rglob("*.odt"):
                with ZipFile(path) as archive:
                    xml = archive.read("content.xml")
                    self.assertNotIn(b"ProjectAnnexCover", xml)
                    child = ET.fromstring(xml)
                    if child.find(".//" + _tag("text:h")) is not None:
                        self.assertIsNotNone(child.find(".//" + _tag("text:table-of-content")))

            soffice = shutil.which("soffice") or str(
                Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
                / "LibreOffice" / "program" / "soffice.exe"
            )
            if Path(soffice).is_file():
                from pypdf import PdfReader

                subprocess.run(
                    [soffice, f"-env:UserInstallation={(root / 'lo-profile').as_uri()}",
                     "--headless", "--convert-to", "pdf", "--outdir", str(root), str(master)],
                    check=True, capture_output=True, timeout=90,
                )
                pages = PdfReader(root / "Maestro.pdf").pages
                index_text = " ".join(
                    page.extract_text() for page in pages
                    if "DOCUMENTO Nº 1. MEMORIA Y ANEJOS." in page.extract_text()
                )
                index_text = " ".join(index_text.split())
                for number, title in (
                    (1, "JUSTIFICACIÓN DE PRECIOS"), (2, "CONSERVACIÓN Y MANTENIMIENTO"),
                ):
                    cover_pages = [
                        index for index, page in enumerate(pages)
                        if f"ANEJO Nº {number}\n" in page.extract_text()
                    ]
                    self.assertEqual(len(cover_pages), 1)
                    page_index = cover_pages[0]
                    self.assertRegex(
                        index_text,
                        rf"(?i)ANEJO Nº {number}\. {re.escape(title)}[\s.]+{page_index + 1}\b",
                    )
                    self.assertIn(title, pages[page_index].extract_text())
                    self.assertIn(f"ANEJO Nº {number}:", pages[page_index + 1].extract_text())
                    runs = []
                    pages[page_index].extract_text(visitor_text=lambda text, cm, tm, font, size: runs.append((text, size)))
                    self.assertTrue(any(
                        text.strip() == f"ANEJO Nº {number}" and abs(size - 26) < 0.1
                        for text, size in runs
                    ))
                    self.assertTrue(any(
                        title.split()[0] in text and abs(size - 32) < 0.1
                        for text, size in runs
                    ))

    def test_external_destination_can_be_changed_and_regenerated(self) -> None:
        """Cada carpeta conserva su propio historial y los enlaces del ODM funcionan."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            root = Path(directory)
            project_folder = root / "proyecto"
            first = root / "primera entrega"
            second = root / "segunda entrega"
            for folder in (project_folder, first, second):
                folder.mkdir()
            window = BudgetMainWindow()
            try:
                project = project_folder / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                selection = [{"kind": "template", "number": "01", "title": "Base", "outline": ["Datos"]}]
                plan = root / "plano.pdf"
                plan.write_bytes(b"plano")
                master, _ = generate_project_package(
                    project, window, "Maestro.odm", selection,
                    [{"title": "Plano", "file": str(plan)}], destination=first,
                )
                self.assertEqual(master.parent, first / TECHNICAL_DOCUMENTS_FOLDER)
                self.assertIsNotNone(generated_at(project, destination=first))
                self.assertIsNone(generated_at(project, destination=second))
                receipt = json.loads(receipt_path(project).read_text(encoding="utf-8"))
                with ZipFile(master) as archive:
                    content = ET.fromstring(archive.read("content.xml"))
                links = [node.get(_tag("xlink:href")) for node in content.iter(_tag("text:section-source"))]
                self.assertEqual(
                    [urljoin(master.as_uri() + "/", link) for link in links],
                    [(first / Path(path)).as_uri() for path in receipt["documentos"]],
                )
                first_master = master.read_bytes()
                generate_project_package(project, window, "Maestro.odm", selection, [], destination=second)
                self.assertEqual(master.read_bytes(), first_master)
                self.assertIsNotNone(generated_at(project, destination=first))
                self.assertIsNotNone(generated_at(project, destination=second))
                updated, _ = generate_project_package(
                    project, window, "Maestro.odm", selection, [], destination=first,
                )
                self.assertEqual(updated, master)
                self.assertTrue((second / TECHNICAL_DOCUMENTS_FOLDER / "Maestro.odm").is_file())
                self.assertFalse((project_folder / TECHNICAL_DOCUMENTS_FOLDER).exists())
                self.assertTrue(receipt["documentos"])
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_existing_untracked_file_in_external_destination_is_not_replaced(self) -> None:
        """Un destino distinto no hereda la propiedad de archivos del proyecto."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            root = Path(directory)
            destination = root / "externo"
            destination.mkdir()
            window = BudgetMainWindow()
            try:
                project = root / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                selection = [{"kind": "template", "number": "01", "title": "Base", "outline": ["Datos"]}]
                generate_project_package(project, window, "Maestro.odm", selection, [])
                foreign = destination / TECHNICAL_DOCUMENTS_FOLDER / "Maestro.odm"
                foreign.parent.mkdir()
                foreign.write_bytes(b"documento ajeno")
                with self.assertRaisesRegex(FileExistsError, "archivo ajeno"):
                    generate_project_package(
                        project, window, "Maestro.odm", selection, [], destination=destination,
                    )
                self.assertEqual(foreign.read_bytes(), b"documento ajeno")
                self.assertIsNone(generated_at(project, destination=destination))
                self.assertTrue((root / TECHNICAL_DOCUMENTS_FOLDER / "Maestro.odm").is_file())
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_external_destination_is_locked_across_projects(self) -> None:
        """Dos proyectos no pueden generar simultáneamente en la misma carpeta."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            root = Path(directory)
            destination = root / "externo"
            destination.mkdir()
            window = BudgetMainWindow()
            lock = QLockFile(str(destination / ".estructura_documental.lock"))
            lock.setStaleLockTime(0)
            try:
                project = root / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                self.assertTrue(lock.tryLock(0))
                with self.assertRaisesRegex(OSError, "carpeta de destino"):
                    generate_project_package(
                        project, window, "Maestro.odm", [], [{"title": "Plano"}],
                        destination=destination,
                    )
                self.assertFalse((destination / TECHNICAL_DOCUMENTS_FOLDER).exists())
                self.assertTrue(lock.isLocked())
            finally:
                lock.unlock()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_folder_picker_cancel_and_external_generation(self) -> None:
        """Cancelar no guarda ni genera; la siguiente entrega recuerda su carpeta."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            root = Path(directory)
            destination = root / "destino elegido"
            destination.mkdir()
            window = BudgetMainWindow()
            try:
                project = root / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                dialog = DocumentStructureWindow(window)
                dialog._plans[0].setCheckState(0, Qt.CheckState.Checked)
                for language, title in (
                    ("en", "Select a folder for the document structure"),
                    ("fr", "Sélectionner un dossier pour la structure documentaire"),
                ):
                    with (
                        patch("localization._LANGUAGE", language),
                        patch.object(QFileDialog, "getExistingDirectory", return_value="") as picker,
                        patch.object(window, "save_project") as save,
                    ):
                        dialog._copy_button.click()
                        self.assertEqual(picker.call_args.args[1], title)
                        save.assert_not_called()
                self.assertFalse(receipt_path(project).exists())
                with (
                    patch.object(QFileDialog, "getExistingDirectory", return_value=str(destination)) as picker,
                    patch.object(QMessageBox, "exec", return_value=0),
                    patch.object(QMessageBox, "critical") as failure,
                ):
                    dialog._copy_button.click()
                    failure.assert_not_called()
                    self.assertEqual(picker.call_args.args[2], str(root))
                self.assertTrue((destination / TECHNICAL_DOCUMENTS_FOLDER / dialog.master_name.text()).is_file())
                with (
                    patch.object(QFileDialog, "getExistingDirectory", return_value=str(destination)) as picker,
                    patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.No) as warning,
                ):
                    dialog._copy_button.click()
                    self.assertEqual(picker.call_args.args[2], str(destination))
                    warning.assert_called_once()
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_document_save_picker_uses_latest_delivery_section(self) -> None:
        """Las herramientas sugieren la carpeta correspondiente a la última entrega."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "proyecto.json"
            destination = root / "entrega"
            expected_directory = (
                destination / TECHNICAL_DOCUMENTS_FOLDER / DOCUMENT_FOLDERS[1]
            )
            expected_directory.mkdir(parents=True)
            project.touch()
            receipt_path(project).write_text(json.dumps({
                "fecha": "2026-01-01T00:00:00+00:00",
                "ubicaciones": [{
                    "raiz": str(destination),
                    "fecha": "2026-01-01T00:00:00+00:00",
                }],
            }), encoding="utf-8")
            self.assertEqual(
                suggested_document_directory(project, "DOC02"), expected_directory,
            )
            parent = SimpleNamespace(app=SimpleNamespace(project_path=project))
            with patch(
                "qt_localization.QFileDialog.getSaveFileName",
                return_value=("", ""),
            ) as picker:
                self.assertIsNone(get_document_save_path(
                    parent, "Guardar planificación", "Plan de trabajo.pdf",
                    document_section="DOC02",
                ))
            self.assertEqual(
                picker.call_args.args[2],
                str(expected_directory / "Plan de trabajo.odt"),
            )

    def test_legacy_record_remains_usable_after_changing_destination(self) -> None:
        """Conserva el historial previo cuando el registro aún no contiene ubicaciones."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            root = Path(directory)
            other = root / "externo"
            other.mkdir()
            window = BudgetMainWindow()
            try:
                project = root / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                selection = [{"kind": "template", "number": "01", "title": "Base", "outline": ["Datos"]}]
                master, _ = generate_project_package(project, window, "Maestro.odm", selection, [])
                record = json.loads(receipt_path(project).read_text(encoding="utf-8"))
                record.pop("raiz")
                record.pop("ubicaciones")
                receipt_path(project).write_text(json.dumps(record), encoding="utf-8")
                generate_project_package(project, window, "Maestro.odm", selection, [], destination=other)
                self.assertIsNotNone(generated_at(project, destination=root))
                regenerated, _ = generate_project_package(project, window, "Maestro.odm", selection, [])
                self.assertEqual(regenerated, master)
                self.assertTrue((other / TECHNICAL_DOCUMENTS_FOLDER / "Maestro.odm").is_file())
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_external_destination_with_folder_project(self) -> None:
        """Mantiene el registro dentro de los proyectos colaborativos de carpeta."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            root = Path(directory)
            project = root / "proyecto"
            destination = root / "externo"
            project.mkdir()
            destination.mkdir()
            window = BudgetMainWindow()
            try:
                master, _ = generate_project_package(
                    project, window, "Maestro.odm", [], [{"title": "Plano"}], destination=destination,
                )
                self.assertEqual(master, destination / TECHNICAL_DOCUMENTS_FOLDER / "Maestro.odm")
                self.assertTrue(receipt_path(project).is_file())
                self.assertIsNotNone(generated_at(project, destination=destination))
                self.assertIsNone(generated_at(project, destination=project))
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_removed_catalog_templates_are_not_restored(self) -> None:
        """Mantiene las 29 eliminaciones y conserva los recursos de portada."""
        self.assertEqual(len(template_paths()), 29)
        self.assertTrue(all(not (TEMPLATES / path).exists() for path in template_paths()))
        self.assertTrue(MASTER_TEMPLATE.is_file())
        self.assertTrue(MASTER_LOGO.is_file())
        self.assertEqual(default_master_name("GEX2026/36718"), "GEX2026-36718-ProyectoConstructivo.odm")

    def test_master_links_resolve_all_documents_after_relocation(self) -> None:
        """Los subdocumentos y el índice apuntan a la entrega incluso al trasladarla."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory:
            root = Path(directory) / "Proyecto # % á"
            root.mkdir()
            with patch("budget_qt_window.user_data_directory", return_value=root):
                window = BudgetMainWindow()
                try:
                    project = root / "proyecto.json"
                    self.assertTrue(window._write_project_file(project))
                    plan_pdf = root / "plano # %.pdf"
                    plan_pdf.write_bytes(b"plano")
                    plan_odt = root / "plano á.odt"
                    plan_odt.write_bytes(MASTER_TEMPLATE.read_bytes())
                    master, _ = generate_project_package(
                        project, window, "Maestro.odm",
                        [
                            {"kind": "template", "number": "1", "title": "Cálculo # %", "outline": ["Datos"]},
                            {"kind": "documentation", "number": "2", "title": "Trámites"},
                        ],
                        [
                            {"title": "Plano PDF", "file": str(plan_pdf)},
                            {"title": "Plano ODT", "file": str(plan_odt)},
                            {"title": "Plano sin archivo", "file": ""},
                        ],
                        memory_documents=[{"title": "Memoria", "outline": ["Ámbito"]}],
                        include_pptp=True, include_budget=True,
                    )
                    receipt = json.loads(receipt_path(project).read_text(encoding="utf-8"))
                    document_paths = [Path(path).relative_to(TECHNICAL_DOCUMENTS_FOLDER)
                                      for path in receipt["documentos"]]
                    plan_paths = [Path(path).relative_to(TECHNICAL_DOCUMENTS_FOLDER)
                                  for path in receipt["archivos_plano"]]
                    self.assertCountEqual(
                        document_paths,
                        [path.relative_to(master.parent) for path in master.parent.rglob("*.odt")
                         if path.relative_to(master.parent) not in plan_paths],
                    )
                    self.assertEqual(len(plan_paths), 2)
                    relocated = Path(directory) / "Traslado # % á"
                    shutil.copytree(master.parent, relocated)
                    for folder in (master.parent, relocated):
                        with self.subTest(folder=folder), ZipFile(folder / master.name) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                            section_hrefs = [
                                node.attrib[_tag("xlink:href")]
                                for node in content.iter(_tag("text:section-source"))
                            ]
                            index_hrefs = [
                                node.attrib[_tag("xlink:href")]
                                for node in content.iter(_tag("text:a"))
                                if not node.attrib[_tag("xlink:href")].startswith("#")
                            ]
                            base = (folder / master.name).as_uri() + "/"
                            self.assertEqual(
                                [urljoin(base, href) for href in section_hrefs],
                                [(folder / path).as_uri() for path in document_paths],
                            )
                            self.assertCountEqual(
                                [urljoin(base, href) for href in index_hrefs],
                                [(folder / path).as_uri() for path in document_paths + plan_paths],
                            )
                            self.assertTrue(all((folder / path).is_file()
                                                for path in document_paths + plan_paths))
                            styles = ET.fromstring(archive.read("styles.xml"))
                            annex_page = next(
                                page for page in styles.iter(_tag("style:master-page"))
                                if page.get(_tag("style:name")) == "ProjectDocumentPage2"
                            )
                            self.assertIn(
                                "ANEJO Nº 1. CÁLCULO # %",
                                "".join(annex_page.find(_tag("style:footer")).itertext()),
                            )
                finally:
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_tool_annex_is_renumbered_and_linked_in_master(self) -> None:
        """Crea un anejo base vacío y lo enlaza al maestro con su número actualizado."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            window = BudgetMainWindow()
            try:
                window.project_title = "Proyecto de prueba"
                window.report_settings.expediente = "GEX42"
                window.report_settings.organization = "Ayuntamiento de prueba"
                window.report_settings.role = "Infraestructuras"
                window.report_settings.service = "Servicio de Infraestructuras"
                window.report_settings.author = "Ana"
                custom_template = Path(directory) / "plantilla_personalizada.odt"
                custom_template.write_bytes(MASTER_TEMPLATE.read_bytes())
                custom_logo = Path(directory) / "logo_personalizado.jpg"
                custom_logo.write_bytes(b"logo personalizado")
                window.report_settings.master_template_path = str(custom_template)
                window.report_settings.master_logo_path = str(custom_logo)
                project = Path(directory) / "proyecto.json"
                self.assertTrue(window._write_project_file(project))

                master, skipped = generate_project_package(
                    project, window, "Maestro de prueba.odm",
                    [{
                        "kind": "tool", "tool": "rcd", "number": "18",
                        "title": "Gestión de residuos",
                    }],
                    [],
                )

                self.assertEqual(skipped, 0)
                docs = Path(directory) / TECHNICAL_DOCUMENTS_FOLDER
                self.assertEqual(master, docs / "Maestro de prueba.odm")
                self.assertIsNotNone(generated_at(project))
                self.assertEqual(
                    {path.name for path in docs.iterdir()},
                    {*DOCUMENT_FOLDERS, master.name},
                )
                annex = docs / DOCUMENT_FOLDERS[0] / "ANEJO 01 - Gestión de residuos.odt"
                with ZipFile(annex) as document:
                    content = ET.fromstring(document.read("content.xml"))
                    self.assertIn("ANEJO Nº 1: GESTIÓN DE RESIDUOS", "".join(content.itertext()))
                    self.assertFalse(any(content.iter(_tag("table:table"))))

                with ZipFile(master) as document:
                    self.assertEqual(
                        document.read("mimetype"),
                        b"application/vnd.oasis.opendocument.text-master",
                    )
                    content_xml = document.read("content.xml")
                    content = ET.fromstring(content_xml)
                    visible = "".join(content.itertext())
                    self.assertIn("Proyecto de prueba", visible)
                    self.assertIn("GEX42", visible)
                    self.assertIn("Ayuntamiento de prueba", visible)
                    self.assertIn("Servicio de Infraestructuras", visible)
                    self.assertIn("Ana", visible)
                    cover_cell = next(
                        cell for cell in content.iter(_tag("table:table-cell"))
                        if "".join(cell.itertext()).startswith("Ayuntamiento de prueba")
                    )
                    self.assertEqual(
                        [
                            "".join(paragraph.itertext())
                            for paragraph in cover_cell.findall(_tag("text:p"))
                        ][:2],
                        ["Ayuntamiento de prueba", "Servicio de Infraestructuras"],
                    )
                    self.assertIn("ÍNDICE GENERAL DEL PROYECTO", visible)
                    for heading in (
                        "DOCUMENTO Nº 1. MEMORIA Y ANEJOS.",
                        "MEMORIA DESCRIPTIVA.",
                        "DOCUMENTO Nº 2. PLANOS.",
                        "DOCUMENTO Nº 3. PLIEGO DE PRESCRIPCIONES TÉCNICAS PARTICULARES.",
                        "DOCUMENTO Nº 4. PRESUPUESTO.",
                    ):
                        self.assertIn(heading, visible)
                    self.assertIn("ANEJO Nº 1. Gestión de residuos", visible)
                    self.assertIn("DOC01-A01", visible)
                    self.assertNotIn("REPORTAJE FOTOGRÁFICO", visible)
                    memory_index = next(
                        paragraph for paragraph in content.iter(_tag("text:p"))
                        if "".join(paragraph.itertext()) == "MEMORIA DESCRIPTIVA."
                    )
                    self.assertIsNone(memory_index.find(_tag("text:tab")))
                    annex_index = next(
                        paragraph for paragraph in content.iter(_tag("text:p"))
                        if "ANEJO Nº 1. Gestión de residuos" in "".join(paragraph.itertext())
                    )
                    self.assertEqual(annex_index.get(_tag("text:style-name")), "PPTPContents2")
                    self.assertIsNotNone(annex_index.find(_tag("text:tab")))
                    self.assertNotIn("{{", content_xml.decode("utf-8"))
                    styles_xml = document.read("styles.xml")
                    self.assertNotIn("{{", styles_xml.decode("utf-8"))
                    self.assertNotIn("GEX2026/36718", styles_xml.decode("utf-8"))
                    traceability = next(
                        table for table in content.iter(_tag("table:table"))
                        if (
                            "CÓDIGO" in "".join(table.itertext())
                            and "DOCUMENTO" in "".join(table.itertext())
                        )
                    )
                    trace_rows = traceability.findall(_tag("table:table-row"))
                    header = traceability.find(_tag("table:table-header-rows"))
                    self.assertIsNotNone(header)
                    header_cells = header.findall(f".//{_tag('table:table-cell')}")
                    self.assertEqual(
                        ["".join(cell.itertext()) for cell in header_cells],
                        [
                            "CÓDIGO", "DOCUMENTO", "FECHA EDICIÓN",
                            "EDITADO", "FECHA COMPROBACIÓN", "VALIDADO",
                        ],
                    )
                    columns = traceability.findall(_tag("table:table-column"))
                    self.assertEqual(sum(
                        int(column.get(_tag("table:number-columns-repeated"), "1"))
                        for column in columns
                    ), 6)
                    self.assertTrue(all(
                        cell.get(_tag("table:style-name")) == "TraceabilityHeaderCell"
                        for cell in header_cells
                    ))
                    self.assertEqual(len(trace_rows), 2)
                    first_row = trace_rows[0].findall(_tag("table:table-cell"))
                    second_row = trace_rows[1].findall(_tag("table:table-cell"))
                    self.assertEqual(len(first_row), 6)
                    self.assertEqual(len(second_row), 6)
                    self.assertEqual(
                        ["".join(cell.itertext()) for cell in first_row],
                        ["MAESTRO", "Maestro de prueba.odm", "", "Ana", "", ""],
                    )
                    self.assertEqual(
                        ["".join(cell.itertext()) for cell in second_row],
                        ["DOC01-A01", "Gestión de residuos", "", "Ana", "", ""],
                    )
                    for index, row in enumerate((first_row, second_row)):
                        self.assertEqual("".join(row[2].itertext()), "")
                        self.assertEqual("".join(row[3].itertext()), "Ana")
                        self.assertEqual("".join(row[4].itertext()), "")
                        self.assertEqual("".join(row[5].itertext()), "")
                        self.assertEqual(
                            row[0].get(_tag("table:style-name")),
                            "TraceabilityBlueCell" if index == 0 else "TraceabilityWhiteCell",
                        )
                    references = [
                        unquote(node.attrib[_tag("xlink:href")])
                        for node in content.iter(_tag("text:section-source"))
                    ]
                    self.assertEqual(references, [
                        "../DOC01 - MEMORIA Y ANEJOS/"
                        "ANEJO 01 - Gestión de residuos.odt",
                    ])
                    body = content.find(f"{_tag('office:body')}/{_tag('office:text')}")
                    self.assertIsNotNone(body)
                    linked_sections = [
                        child for child in body
                        if child.tag == _tag("text:section")
                        and child.get(_tag("text:name"), "").startswith("Documento vinculado ")
                    ]
                    self.assertEqual(len(linked_sections), 1)
                    previous = list(body)[list(body).index(linked_sections[0]) - 1]
                    self.assertEqual(
                        previous.get(_tag("text:style-name")),
                        "ProjectDocumentPageBreak1",
                    )
                    page_break_style = next(
                        style for style in content.iter(_tag("style:style"))
                        if style.get(_tag("style:name")) == "ProjectDocumentPageBreak1"
                    )
                    self.assertEqual(
                        page_break_style.find(_tag("style:paragraph-properties")).get(
                            _tag("fo:break-before"),
                        ),
                        "page",
                    )
                    self.assertEqual(
                        page_break_style.get(_tag("style:master-page-name")),
                        "ProjectDocumentPage1",
                    )
                    index_link = next(
                        node for node in content.iter(_tag("text:a"))
                        if "ANEJO Nº 1. Gestión de residuos" in "".join(node.itertext())
                    )
                    self.assertEqual(
                        unquote(index_link.get(_tag("xlink:href"), "")),
                        "../DOC01 - MEMORIA Y ANEJOS/"
                        "ANEJO 01 - Gestión de residuos.odt",
                    )
                    styles = ET.fromstring(document.read("styles.xml"))
                    self.assertIsNotNone(styles.find(f".//{_tag('text:page-number')}"))
                    document_page = next(
                        page for page in styles.iter(_tag("style:master-page"))
                        if page.get(_tag("style:name")) == "ProjectDocumentPage1"
                    )
                    footer = document_page.find(_tag("style:footer"))
                    self.assertIsNotNone(footer)
                    self.assertIn(
                        "ANEJO Nº 1. GESTIÓN DE RESIDUOS",
                        "".join(footer.itertext()),
                    )
                    traceability_page = next(
                        page for page in styles.iter(_tag("style:master-page"))
                        if page.get(_tag("style:name")) == "Trazabilidad"
                    )
                    self.assertIn(
                        "HOJA DE TRAZABILIDAD",
                        "".join(traceability_page.find(_tag("style:footer")).itertext()),
                    )
                    header = traceability_page.find(_tag("style:header"))
                    logo_image = header.find(".//" + _tag("draw:image"))
                    self.assertIsNotNone(logo_image)
                    logo_resource = logo_image.get(_tag("xlink:href"))
                    self.assertEqual(
                        document.read(logo_resource),
                        custom_logo.read_bytes(),
                    )
                    index_page = next(
                        page for page in styles.iter(_tag("style:master-page"))
                        if page.get(_tag("style:name")) == "Converted2"
                    )
                    self.assertIn(
                        "ÍNDICE GENERAL DEL PROYECTO",
                        "".join(index_page.find(_tag("style:footer")).itertext()),
                    )
                    self.assertEqual(
                        logo_resource,
                        "Pictures/ProjectLogo_estructura_documental.jpg",
                    )
                window.project_title = "Proyecto revisado"
                updated_master, _ = generate_project_package(
                    project, window, "Maestro de prueba.odm",
                    [{
                        "kind": "tool", "tool": "rcd", "number": "18",
                        "title": "Gestión de residuos revisada",
                    }],
                    [],
                )
                self.assertEqual(updated_master, master)
                self.assertTrue(master.is_file())
                self.assertFalse((
                    docs / DOCUMENT_FOLDERS[0] / "ANEJO 01 - Gestión de residuos.odt"
                ).exists())
                self.assertFalse((
                    Path(directory) / "Copias de seguridad estructura documental"
                ).exists())
                with ZipFile(updated_master) as new_master:
                    new_visible = "".join(ET.fromstring(
                        new_master.read("content.xml"),
                    ).itertext())
                    self.assertIn("Proyecto revisado", new_visible)
                self.assertTrue(
                    (docs / DOCUMENT_FOLDERS[0] / "ANEJO 01 - Gestión de residuos revisada.odt").is_file()
                )
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_documentation_procedure_annex_contains_all_fields_in_a_blue_table(self) -> None:
        """Genera un anejo con una fila rellenable por documento o trámite."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            window = BudgetMainWindow()
            checklist = None
            try:
                project = Path(directory) / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                checklist = DocumentStructureWindow(window)
                items = checklist.tree.findItems(
                    "Documentación Complementaria y Tramitación Municipal",
                    Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive, 0,
                )
                self.assertEqual(len(items), 1)
                self.assertFalse(items[0].isDisabled())
                items[0].setCheckState(0, Qt.CheckState.Checked)
                selected_annexes = checklist._selected_annexes()
                self.assertEqual(selected_annexes[0]["kind"], "documentation")
                generate_project_package(
                    project, window, "Maestro.odm",
                    selected_annexes,
                    [],
                )
                annex = (
                    Path(directory) / TECHNICAL_DOCUMENTS_FOLDER / DOCUMENT_FOLDERS[0]
                    / "ANEJO 01 - Documentación Complementaria y Tramitación Municipal.odt"
                )
                with ZipFile(annex) as document:
                    content = ET.fromstring(document.read("content.xml"))
                table = next(
                    table for table in content.iter(_tag("table:table"))
                    if "DOCUMENTACIÓN / TRÁMITE" in "".join(table.itertext())
                )
                header = table.find(_tag("table:table-header-rows"))
                self.assertEqual(
                    [
                        "".join(cell.itertext())
                        for cell in header.findall(f".//{_tag('table:table-cell')}")
                    ],
                    ["DOCUMENTACIÓN / TRÁMITE", "ESTADO / REFERENCIA"],
                )
                rows = table.findall(_tag("table:table-row"))
                self.assertEqual(len(rows), len(DOCUMENTATION_PROCEDURE_FIELDS))
                for index, (row, field) in enumerate(
                    zip(rows, DOCUMENTATION_PROCEDURE_FIELDS, strict=True),
                ):
                    cells = row.findall(_tag("table:table-cell"))
                    self.assertEqual(
                        ["".join(cell.itertext()) for cell in cells],
                        [field, ""],
                    )
                    self.assertEqual(
                        [cell.get(_tag("table:style-name")) for cell in cells],
                        ["DocumentationAnnexBlueCell" if index % 2 == 0 else
                         "DocumentationAnnexWhiteCell"] * 2,
                    )
                self.assertTrue(all(
                    cell.get(_tag("table:style-name")) == "DocumentationAnnexHeaderCell"
                    for cell in header.iter(_tag("table:table-cell"))
                ))
            finally:
                if checklist is not None:
                    checklist.deleteLater()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_service_is_a_separate_persisted_report_setting(self) -> None:
        """Conserva el servicio como ajuste distinto de la entidad y el cargo."""
        from budget_reports import ReportSettings

        settings = ReportSettings.from_dict({
            "organization": "Entidad",
            "service": "Servicio",
            "role": "Cargo",
        })

        self.assertEqual(settings.organization, "Entidad")
        self.assertEqual(settings.service, "Servicio")
        self.assertEqual(settings.role, "Cargo")
        self.assertEqual(settings.to_dict()["service"], "Servicio")

    def test_regeneration_preserves_untracked_files(self) -> None:
        """No reemplaza archivos existentes que el registro no atribuye a la entrega."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            window = BudgetMainWindow()
            try:
                project = Path(directory) / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                generate_project_package(
                    project, window, "Maestro.odm",
                    [{
                        "kind": "template", "number": "01", "title": "Base",
                        "outline": ["Sección"],
                    }],
                    [],
                )
                docs = Path(directory) / TECHNICAL_DOCUMENTS_FOLDER
                custom_file = docs / DOCUMENT_FOLDERS[0] / "ANEJO 01 - Manual.odt"
                custom_file.write_bytes(b"contenido manual")

                with self.assertRaisesRegex(FileExistsError, "archivo ajeno"):
                    generate_project_package(
                        project, window, "Maestro.odm",
                        [{
                            "kind": "template", "number": "02", "title": "Manual",
                            "outline": ["Otra sección"],
                        }],
                        [],
                    )

                self.assertEqual(custom_file.read_bytes(), b"contenido manual")
                self.assertTrue((docs / "Maestro.odm").is_file())
                self.assertFalse((
                    Path(directory) / "Copias de seguridad estructura documental"
                ).exists())
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_plan_file_association_from_checklist(self) -> None:
        """Permite asociar un plano y genera la entrega en la raíz del proyecto."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            window = BudgetMainWindow()
            try:
                project = Path(directory) / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                tools = next(
                    action.menu() for action in window.menuBar().actions()
                    if action.text() == "Herramientas"
                )
                tools.actions()[0].trigger()
                dialog = window.findChild(DocumentStructureWindow)
                self.assertIsNotNone(dialog)
                memory_item = dialog._memory_documents[0]
                self.assertFalse(memory_item.isDisabled())
                self.assertGreaterEqual(memory_item.sizeHint(0).height(), 52)
                catalog_annex = next(
                    item for item in dialog._annexes
                    if item.text(0) == "Cartografía y Topografía"
                )
                self.assertFalse(catalog_annex.isDisabled())
                catalog_annex.setCheckState(0, Qt.CheckState.Checked)
                selected_catalog_annex = dialog._selected_annexes()[0]
                self.assertEqual(selected_catalog_annex["kind"], "template")
                self.assertEqual(
                    selected_catalog_annex["outline"],
                    ["Sistema de referencia y fuentes", "Levantamiento y validación"],
                )
                catalog_annex.setCheckState(0, Qt.CheckState.Unchecked)
                annex = next(
                    item for item in dialog._annexes
                    if item.text(0) == "Justificación de precios"
                )
                self.assertTrue(annex.flags() & Qt.ItemFlag.ItemIsEditable)
                annex.setText(0, "Justificación de precios ajustada")
                annex.setCheckState(0, Qt.CheckState.Checked)
                self.assertEqual(
                    dialog._selected_annexes()[0]["title"],
                    "Justificación de precios ajustada",
                )
                annex.setCheckState(0, Qt.CheckState.Unchecked)
                plan = dialog.tree.topLevelItem(1).child(0)
                plan.setCheckState(0, Qt.CheckState.Checked)
                self.assertTrue(dialog._copy_button.isEnabled())
                source = Path(directory) / "plano.pdf"
                source.write_bytes(b"plano de prueba")
                dialog.tree.setCurrentItem(plan)
                with patch.object(
                    QFileDialog, "getOpenFileName", return_value=(str(source), "PDF"),
                ):
                    dialog._plan_file_button.click()
                dialog.master_name.setText("Maestro con plano.odm")
                displayed_dialogs = []

                def capture_dialog(message_box):
                    """Captura el aviso de generación sin mostrar una ventana modal."""
                    displayed_dialogs.append(message_box)
                    return 0

                with (
                    patch.object(QMessageBox, "exec", new=capture_dialog),
                    patch.object(QMessageBox, "critical") as failure,
                    patch.object(QFileDialog, "getExistingDirectory", return_value=directory),
                ):
                    dialog._copy_button.click()
                    failure.assert_not_called()
                    self.assertEqual(len(displayed_dialogs), 1)
                    message_box = displayed_dialogs[0]
                    self.assertEqual(message_box.text(), str(
                        Path(directory) / TECHNICAL_DOCUMENTS_FOLDER,
                    ))
                    self.assertEqual(message_box.informativeText(), "")
                    self.assertEqual(
                        [button.text() for button in message_box.buttons()],
                        ["Aceptar"],
                    )

                root = Path(directory)
                docs = root / TECHNICAL_DOCUMENTS_FOLDER
                self.assertTrue((docs / "Maestro con plano.odm").is_file())
                self.assertEqual(
                    {path.name for path in docs.iterdir()},
                    {*DOCUMENT_FOLDERS, "Maestro con plano.odm"},
                )
                plan_folder = next((docs / DOCUMENT_FOLDERS[1]).iterdir())
                self.assertEqual((plan_folder / source.name).read_bytes(), source.read_bytes())
                self.assertFalse(list(plan_folder.glob("*.odt")))
                self.assertTrue(dialog._copy_button.isEnabled())
                with ZipFile(docs / "Maestro con plano.odm") as master:
                    content = ET.fromstring(master.read("content.xml"))
                    links = [
                        unquote(node.attrib[_tag("xlink:href")])
                        for node in content.iter(_tag("text:a"))
                    ]
                    self.assertTrue(any(link.endswith("/plano.pdf") for link in links))
                with (
                    patch.object(
                        QMessageBox, "warning",
                        return_value=QMessageBox.StandardButton.No,
                    ) as replacement_warning,
                    patch.object(QMessageBox, "critical") as failure,
                    patch.object(QFileDialog, "getExistingDirectory", return_value=directory),
                ):
                    dialog._copy_button.click()
                    replacement_warning.assert_called_once()
                    self.assertIn(
                        "se reemplazarán",
                        replacement_warning.call_args.args[2],
                    )
                    failure.assert_not_called()
                self.assertFalse((
                    root / "Copias de seguridad estructura documental"
                ).exists())
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_pptp_and_budget_are_created_as_empty_base_documents(self) -> None:
        """Crea los ODT base sin requerir ni exportar datos de las herramientas."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            window = BudgetMainWindow()
            try:
                project = Path(directory) / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                generate_project_package(
                    project, window, "Maestro.odm", [], [],
                    include_pptp=True, include_budget=True,
                )

                documents = Path(directory) / TECHNICAL_DOCUMENTS_FOLDER
                base_files = [
                    documents / DOCUMENT_FOLDERS[2] / "PPTP.odt",
                    *((documents / DOCUMENT_FOLDERS[3]).glob("*.odt")),
                ]
                self.assertGreater(len(base_files), 1)
                for base_file in base_files:
                    self.assertTrue(base_file.is_file())
                    with ZipFile(base_file) as archive:
                        content = ET.fromstring(archive.read("content.xml"))
                        self.assertFalse(any(content.iter(_tag("table:table"))))
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_catalog_documents_use_configured_styles_and_outline(self) -> None:
        """Genera ODT editables desde esquemas y aplica la tipografía del proyecto."""
        QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory, patch(
            "budget_qt_window.user_data_directory", return_value=Path(directory),
        ):
            window = BudgetMainWindow()
            try:
                window.project_title = "Proyecto estructurado"
                window.report_settings.text_styles = {"heading1": {"size": 18}}
                project = Path(directory) / "proyecto.json"
                self.assertTrue(window._write_project_file(project))
                master, _ = generate_project_package(
                    project, window, "Maestro.odm",
                    [{
                        "kind": "template",
                        "number": "02",
                        "title": "Topografía revisada",
                        "outline": ["Sistema de referencia", "Levantamiento"],
                    }],
                    [],
                    memory_documents=[{
                        "number": "01",
                        "title": "Estado actual",
                        "outline": ["Situación e inventario", "Deficiencias"],
                    }, {
                        "number": "02",
                        "title": "Objeto del proyecto y antecedentes",
                        "outline": ["Objeto y alcance", "Antecedentes"],
                    }],
                )

                documents = Path(directory) / TECHNICAL_DOCUMENTS_FOLDER / DOCUMENT_FOLDERS[0]
                memory = documents / f"{MEMORY_DOCUMENT_TITLE}.odt"
                annex = documents / "ANEJO 01 - Topografía revisada.odt"
                self.assertTrue(memory.is_file())
                self.assertEqual(len(list(documents.glob("Memoria *.odt"))), 1)
                self.assertTrue(annex.is_file())
                with ZipFile(annex) as archive:
                    content = ET.fromstring(archive.read("content.xml"))
                    visible = "".join(content.itertext())
                    self.assertIn("ANEJO Nº 1: TOPOGRAFÍA REVISADA", visible)
                    self.assertIn("Sistema de referencia", visible)
                    self.assertIn("Levantamiento", visible)
                    self.assertFalse(any(content.iter(_tag("table:table"))))
                    styles = ET.fromstring(archive.read("styles.xml"))
                    heading = next(
                        style for style in styles.iter(_tag("style:style"))
                        if style.get(_tag("style:name")) == "Heading1"
                    )
                    text_properties = heading.find(_tag("style:text-properties"))
                    self.assertIsNotNone(text_properties)
                    self.assertEqual(text_properties.get(_tag("fo:font-size")), "18pt")
                with ZipFile(memory) as archive:
                    visible = "".join(ET.fromstring(
                        archive.read("content.xml"),
                    ).itertext())
                    self.assertIn("Estado actual", visible)
                    self.assertIn("Situación e inventario", visible)
                    self.assertIn("Objeto del proyecto y antecedentes", visible)
                    self.assertIn("Objeto y alcance", visible)
                with ZipFile(master) as archive:
                    content = ET.fromstring(archive.read("content.xml"))
                    visible = "".join(content.itertext())
                    self.assertIn("DOC01-M01", visible)
                    self.assertIn("DOC01-A01", visible)
                    self.assertIn("Estado actual", visible)
                    self.assertIn("TOPOGRAFÍA REVISADA", visible)
                    links = [
                        unquote(node.attrib[_tag("xlink:href")])
                        for node in content.iter(_tag("text:section-source"))
                    ]
                    self.assertTrue(any(f"{MEMORY_DOCUMENT_TITLE}.odt" in link for link in links))
                    self.assertTrue(any("ANEJO 01 - Topografía revisada.odt" in link for link in links))
                    memory_index_link = next(
                        node for node in content.iter(_tag("text:a"))
                        if MEMORY_DOCUMENT_TITLE in "".join(node.itertext())
                    )
                    self.assertEqual(
                        unquote(memory_index_link.get(_tag("xlink:href"), "")),
                        f"../DOC01 - MEMORIA Y ANEJOS/{MEMORY_DOCUMENT_TITLE}.odt",
                    )
                    memory_index = next(
                        paragraph for paragraph in content.iter(_tag("text:p"))
                        if paragraph.find(_tag("text:a")) is memory_index_link
                    )
                    self.assertIsNotNone(memory_index.find(_tag("text:tab")))
            finally:
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
