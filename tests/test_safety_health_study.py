from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MethodType, SimpleNamespace
import os
import re
import shutil
import subprocess
import tempfile
import tkinter as tk
import unittest
from unittest.mock import create_autospec, patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader
from pypdf.generic import ContentStream

from bc3_gui import Concept, ProjectItem
from budget_project import budget_concept_type
from budget_reports import ReportSettings, budget_totals
from report_layout import ReportLayout
from reference_search import load_reference_entries
from tests._report_template import write_report_template
from tools.safety_health import (
    SAFETY_BUDGET_SOURCE, SafetyLine, restore_project_state,
    synchronize_budget, write_study_report,
)
from tools.safety_health.document import (
    _Block, _StudyContent, _build_study_content, _markdown_blocks, _numbered_blocks,
    _pdf_blue_table, study_technical_review, study_work_units,
)
from tools.safety_health.regulations import (
    WorkUnit, general_une_references, initially_selected, law_proposals, load_ntps,
    ntp_proposals, review_context,
)
from tools.safety_health.chapter_selection import study_chapters, study_items
from tools.safety_health.technical_conditions import (
    TECHNICAL_BLOCKS, restore_technical_review,
    selected_technical_template, technical_review_confirmed, technical_review_data,
)


class SafetyHealthStudyTests(unittest.TestCase):
    """Comprueba el enlace de plantillas y la generacion del ESS en sus formatos."""

    def make_app(self, output_format: str):
        """Crea un proyecto ficticio con partidas BCCA y una clasificacion provisional."""
        app = SimpleNamespace(
            budget_items=[
                ProjectItem(
                    "15APAA00001", "01#", "ud", "Arqueta de registro",
                    measurement=1, unit_price=100, rendimiento=400,
                ),
                ProjectItem(
                    "OBRA001", "02#", "m2", "Fresado de pavimento",
                    measurement=5, unit_price=10, rendimiento=80,
                ),
            ],
            chapter_order=["01#", "02#"],
            chapter_titles={"01#": "Saneamiento", "02#": "Pavimentos"},
            report_settings=ReportSettings(
                output_format=output_format,
                general_expenses=0,
                industrial_profit=0,
                vat=0,
            ),
            planning_state={},
            safety_health_state={},
            _project_title=lambda: "Proyecto de prueba",
            refresh_budget_tree=lambda: None,
            _mark_dirty=lambda: None,
            _component_dataset=lambda item: None,
        )
        app._budget_concept_type = MethodType(budget_concept_type, app)

        def chapter_title(chapter):
            """Devuelve el titulo guardado del capitulo."""
            return app.chapter_titles.get(chapter, "Obra")

        def ensure_chapter(chapter):
            """Registra el capitulo si todavia no existe."""
            if chapter not in app.chapter_order:
                app.chapter_order.append(chapter)

        app._chapter_title = chapter_title
        app._ensure_chapter = ensure_chapter
        synchronize_budget(
            app,
            [SafetyLine("SS0001", "ud", "Protecciones colectivas", Decimal("3.11"), Decimal(1))],
            Decimal("2"),
            study_criteria=(False, False, False, False),
        )
        return app

    def test_pdf_and_odt_use_project_setting_and_assemble_selected_templates(self):
        """Genera ambos formatos con ficha BCCA, fallback, riesgos y datos pendientes."""
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(output_format=output_format):
                    app = self.make_app(output_format)
                    app.report_settings.municipality_codes = ["140386", "280796"]
                    app.safety_health_state["resumen_actuacion"] = "La actuación de ACME mejora la red."
                    target = Path(folder) / f"estudio.{output_format}"

                    warnings = write_study_report(app, target)

                    self.assertTrue(target.is_file())
                    self.assertGreater(target.stat().st_size, 0)
                    if output_format == "pdf":
                        text = "\n".join(
                            page.extract_text() or "" for page in PdfReader(target).pages
                        )
                    else:
                        with ZipFile(target) as archive:
                            content = ET.fromstring(archive.read("content.xml"))
                        text = "".join(content.itertext())
                        ns = {
                            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
                            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
                            "xlink": "http://www.w3.org/1999/xlink",
                        }
                        summary = content.find(
                            ".//table:table[@table:name='ESSProjectSummary']", ns,
                        )
                        linked_rows = {}
                        for row in summary.findall("table:table-row", ns):
                            cells = row.findall("table:table-cell", ns)
                            label = "".join(cells[0].itertext())
                            if label not in ("Riesgos detectados", "Medidas preventivas"):
                                continue
                            links = cells[1].findall(".//text:a", ns)
                            self.assertTrue(links)
                            self.assertEqual(
                                "".join(cells[1].itertext()), ", ".join(link.text for link in links),
                            )
                            for link in links:
                                self.assertEqual(
                                    link.get(f"{{{ns['xlink']}}}href"), f"#ESSRef-{link.text}",
                                )
                            linked_rows[label] = links
                        self.assertEqual(
                            set(linked_rows), {"Riesgos detectados", "Medidas preventivas"},
                        )
                        text_ns = ns["text"]
                        office_ns = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                        body = content.find(
                            f"{{{office_ns}}}body/{{{office_ns}}}text",
                        )
                        children = list(body)
                        risk_heading = next(
                            index for index, child in enumerate(children)
                            if child.tag == f"{{{text_ns}}}h"
                            and "".join(child.itertext()) == "3.1.2 RIESGOS DETECTADOS"
                        )
                        risk_codes = children[risk_heading + 2]
                        risk_links = risk_codes.findall(".//text:a", ns)
                        self.assertEqual(
                            [link.text for link in risk_links], list(study_work_units(app)[0].risks),
                        )
                        self.assertEqual(
                            "".join(risk_codes.itertext()),
                            ", ".join(link.text for link in risk_links) + ".",
                        )
                        for link in risk_links:
                            self.assertEqual(
                                link.get(f"{{{ns['xlink']}}}href"), f"#ESSRef-{link.text}",
                            )
                    text = " ".join(text.split())
                    self.assertNotIn("Registro de cambios", text)
                    self.assertNotIn("Biblioteca distribuida con el programa", text)
                    self.assertIn("ESTUDIO BÁSICO DE SEGURIDAD Y SALUD", text)
                    self.assertIn("ÍNDICE", text)
                    self.assertIn("LUCENA (CÓRDOBA); MADRID (MADRID)", text)
                    self.assertIn("ANDALUCÍA; MADRID, COMUNIDAD DE", text)
                    self.assertIn("3.1 IMBORNALES Y ARQUETAS DE SANEAMIENTO", text)
                    self.assertNotIn("15APA — IMBORNALES", text)
                    self.assertNotIn("Unidades de obra vinculadas:", text)
                    self.assertNotIn("Clasificación BCCA:", text)
                    self.assertIn("OBRA001", text)
                    self.assertIn("Ficha específica pendiente.", text)
                    self.assertNotIn("UO-", text)
                    for label in (
                        "Tabla 5. Unidades de obra, riesgos detectados y medidas preventivas.",
                        "3.1.1 DEFINICIÓN Y ALCANCE",
                        "3.1.2 RIESGOS DETECTADOS",
                        "3.1.3 MEDIDAS PREVENTIVAS",
                        "3.1.3.1 PROTECCIONES COLECTIVAS",
                        "3.1.3.2 EQUIPOS DE PROTECCIÓN INDIVIDUAL",
                        "3.1.3.3 EQUIPOS DE TRABAJO", "3.1.4 VALIDACIÓN",
                        "4 SEGUIMIENTO, CONTROL, COORDINACIÓN, FORMACIÓN Y DOCUMENTACIÓN",
                        "DOCUMENTO I. MEMORIA", "DOCUMENTO III. PLIEGO DE CONDICIONES",
                        "1 OBJETO DEL PRESENTE PLIEGO",
                        "2 CONDICIONES DE NATURALEZA LEGAL",
                        "2.4 NORMAS UNE DE APLICACIÓN",
                        "2.5 NOTAS TÉCNICAS DE PREVENCIÓN POR CAPÍTULOS DEL PRESUPUESTO",
                        "Se diferenciarán los filtros contra partículas",
                        "Los cinturones de retención o posicionamiento no sustituyen",
                        "3 CONDICIONES DE NATURALEZA FACULTATIVA",
                        "3.7.1 ACTA O RESOLUCIÓN DE APROBACIÓN DEL PLAN",
                        "3.9 CONTROL DE LA EFECTIVIDAD",
                        "3.9.2 ÍNDICES DE CONTROL",
                        "I.I. = (A / T) x 100.",
                        "I.F. = (A / H) x 1.000.000.",
                        "No se exigirá adquirir ni habilitar un nuevo Libro de Visitas.",
                        "3.10 CONSULTA Y PARTICIPACIÓN DE LOS TRABAJADORES",
                        "3.11 OBLIGACIONES DE LOS TRABAJADORES",
                        "cincuenta o más trabajadores",
                        "4 CONDICIONES DE NATURALEZA TÉCNICA",
                        "4.1.1 MEDIOS DE PROTECCIÓN COLECTIVA",
                        "4.4 INSTALACIONES PROVISIONALES DE SALUD Y CONFORT",
                        "4.4.4 COMEDORES, COCINA Y LOCALES DE DESCANSO",
                        "marcado CE en el soporte que legalmente corresponda",
                        "5 CONDICIONES DE NATURALEZA ECONÓMICA",
                        "Salvo pacto en contrario, una vez al mes",
                        "no podrá ser abonada por el Promotor",
                        "conforme se estipule en el contrato de obra",
                        "se definirán total y correctamente las mismas",
                        "el Contratista comunicará esta proposición al Promotor, por escrito",
                    ):
                        self.assertIn(label, text)
                    self.assertIn("Partida sin ficha ESS BCCA: OBRA001", "\n".join(warnings))
                    self.assertNotIn("Partida sin ficha ESS BCCA: OBRA001", text)
                    self.assertNotIn("DATOS PENDIENTES Y ADVERTENCIAS", text)
                    self.assertNotIn("ANEXO DE TRAZABILIDAD", text)
                    self.assertNotIn("PARTIDAS DEL PRESUPUESTO GENERAL", text)
                    self.assertIn("PLIEGO DE CONDICIONES", text)
                    self.assertRegex(
                        text, r"\b2\.\d+ INSTALACIONES PROVISIONALES Y EMERGENCIAS",
                    )
                    self.assertNotIn("PRESUPUESTO DE SEGURIDAD Y SALUD", text)
                    self.assertNotIn("{{", text)
                    self.assertIn("La actuación de ACME mejora la red.", text)
                    self.assertNotIn("véase la tabla", text.casefold())
                    self.assertIn("Tabla 1. Datos del proyecto", text)
                    self.assertIn("La prevención se integrará en la planificación", text)
                    self.assertNotIn("Resumen automatico segun las partidas presupuestarias", text)
                    self.assertNotIn("DATOS DEL PROYECTO", text)
                    self.assertIn(
                        "Por tanto, se define un ESTUDIO BÁSICO DE SEGURIDAD Y SALUD.", text,
                    )

    def test_pdf_uses_the_configured_master_logo(self):
        """Pasa el logo institucional de Configuración general a la maquetación PDF."""
        from PIL import Image
        import report_layout

        app = self.make_app("pdf")
        with tempfile.TemporaryDirectory() as folder:
            logo = Path(folder) / "logo-institucional.jpg"
            Image.new("RGB", (240, 120), (180, 30, 30)).save(logo, format="JPEG")
            app.report_settings.master_logo_path = str(logo)
            target = Path(folder) / "estudio.pdf"
            with patch(
                "report_layout._read_report_template",
                wraps=report_layout._read_report_template,
            ) as read_template:
                write_study_report(app, target)

            self.assertTrue(target.is_file())
            self.assertTrue(any(
                len(call.args) > 1 and call.args[1] == str(logo)
                for call in read_template.call_args_list
            ))

    def test_first_section_combines_scope_selected_criteria_and_summary(self):
        """Integra alcance, criterios manuales y magnitudes sin duplicados ni contratista."""
        app = self.make_app("odt")
        app.report_settings.organization = "Ayuntamiento de prueba"
        app.report_settings.author = "Técnico redactor"
        app.report_settings.contractor = "Contratista que no debe mostrarse"
        app.report_settings.general_expenses = 13
        app.report_settings.industrial_profit = 6
        app.report_settings.vat = 21
        app.report_settings.service = "Servicio de obras"
        app.safety_health_state["resumen_actuacion"] = "Actuación de prueba."
        app.safety_health_state["study_criteria"].update(
            criterion_1_met=True, criterion_4_met=True,
        )

        content = _build_study_content(app)

        memory_index = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Memoria descriptiva"
        )
        introduction = content.blocks[:memory_index]
        text = "\n".join(block.text for block in introduction)
        self.assertEqual(introduction[0].kind, "document")
        self.assertEqual(introduction[1].text, "Objeto y alcance")
        self.assertIn("Su objeto es servir de base al Plan de Seguridad", text)
        self.assertIn("La actuación comprende", text)
        self.assertIn("PROYECTO DE PRUEBA", text)
        self.assertIn("AYUNTAMIENTO DE PRUEBA", text)
        self.assertIn("Se cumplen los siguientes criterios del artículo 4.1", text)
        self.assertIn("a) Presupuesto de ejecución", text)
        self.assertIn("d) Obra de túneles", text)
        self.assertNotIn("b) Duración", text)
        self.assertNotIn("c) Volumen", text)
        self.assertIn("Por tanto, se define un ESTUDIO DE SEGURIDAD Y SALUD.", text)
        self.assertNotIn("SE CUMPLEN LOS SIGUIENTES CRITERIOS", text)
        self.assertIn("ESTUDIO DE SEGURIDAD Y SALUD", text)
        self.assertNotIn("Evaluacion automatica", text)
        self.assertNotIn("Objeto y alcance", [
            block.text for block in content.blocks[memory_index:]
            if block.kind == "heading"
        ])
        self.assertNotIn("Datos del proyecto", [
            block.text for block in content.blocks if block.kind == "heading"
        ])
        summary = next(block for block in introduction if block.project_summary)
        rows = dict(summary.rows)
        self.assertNotIn("Contratista, si se conoce", rows)
        self.assertNotIn("Importe sin IVA", rows)
        self.assertNotIn("Importe total o sin IVA, indicando criterio", rows)
        pem = sum(Decimal(str(item.import_total)) for item in app.budget_items)
        pec = budget_totals(float(pem), app.report_settings)["total"]
        self.assertEqual(rows["PEM"], f"{pem:.2f} €".replace(".", ","))
        self.assertEqual(rows["PEC (con IVA)"], f"{pec:.2f} €".replace(".", ","))
        self.assertEqual(rows["Técnico/s redactor/es"], "TÉCNICO REDACTOR")
        self.assertEqual(rows["Servicio promotor"], "SERVICIO DE OBRAS")
        safety_total = sum(
            Decimal(str(item.import_total)) for item in app.budget_items
            if item.source.startswith(SAFETY_BUDGET_SOURCE)
        )
        self.assertAlmostEqual(float(safety_total / pem * 100), 2, delta=0.02)
        self.assertTrue(all(value.replace("m²", "M²") == value.upper() for value in rows.values()))
        self.assertNotIn(
            "Falta definir el contratista",
            "\n".join(block.text for block in content.blocks),
        )

    def test_surface_uses_largest_area_measurement_without_summing(self):
        """Toma el maximo en m2 y deja validacion explicita cuando no hay superficies."""
        app = self.make_app("odt")
        app.budget_items.extend([
            ProjectItem("AREA01", "02#", "m²", "Pintura", measurement=80),
            ProjectItem("AREA02", "02#", " m2 ", "Solera", measurement=125),
            ProjectItem("VOLUME", "02#", "m3", "Relleno", measurement=9000),
            ProjectItem(
                "SS_AREA", "CAP.03#", "m2", "Proteccion", measurement=2000,
                source="SS:prueba",
            ),
        ])
        content = _build_study_content(app)
        summary = next(block for block in content.blocks if block.project_summary)
        self.assertEqual(dict(summary.rows)["Superficie (m²)"], "125,00 m²")

        app.budget_items = [
            item for item in app.budget_items
            if item.unit.strip() not in ("m²", "m2")
        ]
        content = _build_study_content(app)
        summary = next(block for block in content.blocks if block.project_summary)
        self.assertIn("[VALIDAR]", dict(summary.rows)["Superficie (m²)"])
        text = "\n".join(block.text for block in content.blocks)
        self.assertIn("No se cumple ninguno", text)
        self.assertIn("ESTUDIO BÁSICO DE SEGURIDAD Y SALUD", text)

    def test_work_summary_uses_pptp_or_reviewed_text_without_an_automatic_fallback(self):
        """Respeta la revision propia y avisa si se borra, sin listar partidas como resumen."""
        app = self.make_app("odt")
        app.safety_health_state.pop("resumen_actuacion", None)
        app.pptp_state = {"variables": {"bullet_capitulos_obra": "Renovación de la red de agua."}}
        content = _build_study_content(app)
        text = "\n".join(block.text for block in content.blocks)
        self.assertIn("Renovación de la red de agua.", text)
        self.assertNotIn("Resumen automatico segun las partidas presupuestarias", text)
        app.safety_health_state["resumen_actuacion"] = "Resumen preventivo revisado."
        content = _build_study_content(app)
        text = "\n".join(block.text for block in content.blocks)
        self.assertIn("Resumen preventivo revisado.", text)
        self.assertNotIn("Renovación de la red de agua.", text)
        app.safety_health_state["resumen_actuacion"] = ""
        content = _build_study_content(app)
        self.assertIn(
            "Falta definir el resumen de la actuación",
            "\n".join(content.warnings),
        )

    def test_study_uses_general_municipalities_and_deduplicates_regions(self):
        """Integra la seleccion general y mantiene mayusculas sin avisos territoriales falsos."""
        app = self.make_app("odt")
        app.report_settings.municipality_codes = ["140386", "140214", "280796"]

        content = _build_study_content(app)

        summary = next(block for block in content.blocks if block.project_summary)
        rows = dict(summary.rows)
        self.assertEqual(rows["Municipio"], "LUCENA (CÓRDOBA); CÓRDOBA (CÓRDOBA); MADRID (MADRID)")
        self.assertEqual(rows["Provincia"], "CÓRDOBA; MADRID")
        self.assertEqual(rows["Comunidad autónoma"], "ANDALUCÍA; MADRID, COMUNIDAD DE")
        pending = "\n".join(block.text for block in content.blocks if block.kind == "paragraph")
        for datum in ("el municipio", "la provincia", "la comunidad autonoma"):
            self.assertNotIn(f"Falta definir {datum}", pending)

    def test_project_summary_renders_blue_header_and_alternating_rows(self):
        """Verifica colores PDF reales y estilos de cada fila del resumen ODT."""
        ns = {
            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
            "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
        }
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(format=output_format):
                    app = self.make_app(output_format)
                    content = _build_study_content(app)
                    memory_index = next(
                        index for index, block in enumerate(content.blocks)
                        if block.kind == "heading" and block.text == "Memoria descriptiva"
                    )
                    content.blocks = content.blocks[:memory_index]
                    target = Path(folder) / f"resumen.{output_format}"
                    with patch(
                        "tools.safety_health.document._build_study_content",
                        return_value=content,
                    ):
                        write_study_report(app, target)
                    if output_format == "pdf":
                        reader = PdfReader(target)
                        colors = {
                            tuple(round(float(value), 5) for value in operands)
                            for page in reader.pages
                            for operands, operator in ContentStream(
                                page.get_contents(), reader,
                            ).operations
                            if operator == b"rg"
                        }
                        for color in ((68, 114, 196), (217, 229, 245)):
                            self.assertIn(
                                tuple(round(value / 255, 5) for value in color), colors,
                            )
                        text = " ".join(
                            page.extract_text() for page in reader.pages
                        )
                        self.assertIn("PROYECTO DE PRUEBA", text)
                        self.assertIn("PEC (con IVA)", text)
                    else:
                        with ZipFile(target) as archive:
                            root = ET.fromstring(archive.read("content.xml"))
                            named_styles = ET.fromstring(archive.read("styles.xml"))
                        table = root.find(
                            ".//table:table[@table:name='ESSProjectSummary']", ns,
                        )
                        style_attribute = f"{{{ns['table']}}}style-name"
                        cells = table.findall(
                            "table:table-header-rows/table:table-row/table:table-cell", ns,
                        )
                        styles = {
                            style.get(f"{{{ns['style']}}}name"): style
                            for tree in (root, named_styles)
                            for style in tree.findall(".//style:style", ns)
                        }
                        table_properties = styles[table.get(style_attribute)].find(
                            "style:table-properties", ns,
                        )
                        self.assertEqual(
                            table_properties.get(f"{{{ns['table']}}}align"), "left",
                        )
                        self.assertEqual(
                            table_properties.get(f"{{{ns['fo']}}}margin-left"), "0pt",
                        )
                        self.assertAlmostEqual(
                            float(table_properties.get(f"{{{ns['style']}}}width").removesuffix("pt")),
                            ReportLayout().content_width, places=3,
                        )
                        for name in ("ESSSummaryHeader", "ESSSummaryText"):
                            paragraph = styles[name].find("style:paragraph-properties", ns)
                            for attribute in ("margin-left", "margin-right", "text-indent"):
                                self.assertEqual(
                                    paragraph.get(f"{{{ns['fo']}}}{attribute}"), "0pt",
                                )
                        for index, row in enumerate(table.findall("table:table-row", ns)):
                            cells.extend(row.findall("table:table-cell", ns))
                            expected = "ESSSummaryBlueCell" if index % 2 == 0 else (
                                "ESSSummaryWhiteCell"
                            )
                            if index in next(
                                block for block in content.blocks if block.project_summary
                            ).section_rows:
                                expected = "ESSSummaryHeaderCell"
                            self.assertTrue(all(
                                cell.get(style_attribute) == expected
                                for cell in row.findall("table:table-cell", ns)
                            ))
                        self.assertTrue(cells)
                        for cell in cells:
                            name = cell.get(style_attribute)
                            properties = styles[name].find("style:table-cell-properties", ns)
                            expected = {
                                "ESSSummaryHeaderCell": "#4472C4",
                                "ESSSummaryBlueCell": "#D9E5F5",
                                "ESSSummaryWhiteCell": "#FFFFFF",
                            }[name]
                            self.assertEqual(
                                properties.get(f"{{{ns['fo']}}}background-color"), expected,
                            )

    def test_memory_consolidates_common_rules_and_preserves_specific_obligations(self):
        """Reune criterios y comprobaciones sin eliminar obligaciones particulares."""
        app = self.make_app("odt")
        units = study_work_units(app)
        app.safety_health_state["regulatory_review"] = {
            "context": review_context(units), "laws": law_proposals(units), "ntps": {},
        }
        content = _build_study_content(app)
        headings = [block.text for block in content.blocks if block.kind == "heading"]
        self.assertEqual(headings.count("Criterios preventivos generales"), 1)
        self.assertNotIn("Condiciones generales de seguridad y salud", headings)
        self.assertNotIn("Criterios generales", headings)
        self.assertNotIn("Comprobaciones previas", headings)
        memory_index = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Memoria descriptiva"
        )
        units_index = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Fichas de unidades de obra aplicables"
        )
        memory_headings = {
            block.text: block.level for block in content.blocks[memory_index:units_index]
            if block.kind == "heading"
        }
        self.assertNotIn("Aplicación por unidades de obra y alcance de las fichas", headings)
        self.assertNotIn("Datos y condicionantes", headings)
        self.assertEqual(
            memory_headings["Instalaciones provisionales y emergencias"], 2,
        )
        for title in (
            "Instalaciones y organización del tajo", "Emergencias y primeros auxilios",
        ):
            self.assertEqual(memory_headings[title], 3)
            self.assertEqual(headings.count(title), 1)
        self.assertEqual(headings.count("Instalaciones provisionales y emergencias"), 1)
        self.assertNotIn(
            "Instalaciones provisionales y emergencias",
            [block.text for block in content.blocks if block.kind == "heading" and block.level == 1],
        )
        start = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Criterios preventivos generales"
        )
        end = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Catálogo preventivo"
        )
        overview = [
            block for block in content.blocks[start:end] if block.kind == "table"
        ]
        self.assertEqual(len(overview), 1)
        self.assertEqual([row[0] for row in overview[0].rows], [
            "Tráfico y maquinaria móvil", "Caídas", "Proyecciones y partículas",
            "Ruido y vibraciones", "Térmicos", "Químicos", "Sobreesfuerzos",
            "Servicios urbanos", "Terceros",
        ])
        self.assertTrue(all(len(row) == 3 for row in overview[0].rows))
        self.assertIn(
            "protección respiratoria solo cuando la evaluación lo requiera.",
            overview[0].rows[5][2],
        )
        text = "\n".join(block.text for block in content.blocks)
        self.assertEqual(text.count("Antes de abrir cada tajo"), 1)
        for obligation in (
            "tapas resistentes e inmovilizadas", "aristas peligrosas",
            "se repondrán inmediatamente", "Real Decreto 614/2001",
            "No se admitira la entrada para rescate improvisado.",
            "responsables, plazos de corrección", "La Dirección Facultativa",
            "Libro de incidencias", "Las futuras operaciones de mantenimiento",
        ):
            self.assertIn(obligation, text)
        self.assertIn("Catálogo preventivo", headings)
        self.assertNotIn("Matriz detallada de riesgos por unidad", headings)
        self.assertNotIn("Fichas ESS de la biblioteca BCCA", headings)
        self.assertNotIn("Anexo de trazabilidad presupuesto - ESS", headings)
        self.assertNotIn("Datos pendientes y advertencias de validacion", headings)
        self.assertEqual(
            [block.text for block in content.blocks if block.kind == "heading" and block.level == 1][-1],
            "Plan de Seguridad y Salud, control y certificación",
        )

    def test_third_party_protection_adapts_the_example_to_generic_project_conditions(self):
        """Integra las afecciones al entorno sin copiar datos ni dotaciones del ejemplo."""
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(format=output_format):
                    app = self.make_app(output_format)
                    content = _build_study_content(app)
                    start = next(
                        index for index, block in enumerate(content.blocks)
                        if block.kind == "heading" and block.text == "Delimitación y protección de terceros"
                    )
                    end = next(
                        index for index in range(start + 1, len(content.blocks))
                        if content.blocks[index].kind == "heading" and content.blocks[index].level <= 2
                    )
                    section = content.blocks[start:end]
                    self.assertEqual([block.level for block in section if block.kind == "heading"], [
                        2, 3, 3, 3, 3, 3, 3, 3, 3,
                    ])
                    target = Path(folder) / f"terceros.{output_format}"
                    with patch(
                        "tools.safety_health.document._build_study_content",
                        return_value=content,
                    ):
                        write_study_report(app, target)
                    if output_format == "pdf":
                        text = "\n".join(page.extract_text() for page in PdfReader(target).pages)
                    else:
                        with ZipFile(target) as archive:
                            text = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                    text = " ".join(text.split())
                    for measure in (
                        "no sustituirán barreras resistentes", "itinerarios peatonales accesibles",
                        "solo se admitirán vertidos expresamente autorizados",
                        "conservando los justificantes", "no ocuparán cauces naturales",
                        "No se quemarán residuos", "horarios y límites aplicables",
                        "sin deslumbrar", "arrastren tierras, barro o restos",
                        "inspección técnica deberán tenerla vigente",
                        "consulta y participación de los trabajadores",
                    ):
                        self.assertIn(measure, text)
                    for example_detail in (
                        "PÁRROCO JOAQUÍN", "MAQUEDANO", "ANEJO Nº 07",
                        "100 cm", "1.5 kw", "tipo “Jirafa”", "Art. 39",
                    ):
                        self.assertNotIn(example_detail.casefold(), text.casefold())

    def test_protection_and_temporary_facilities_are_integrated_without_duplicate_emergencies(self):
        """Integra ambos textos en el capitulo 2 sin imponer dotaciones del ejemplo."""
        content = _build_study_content(self.make_app("odt"))
        start = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Memoria descriptiva"
        )
        end = next(
            index for index in range(start + 1, len(content.blocks))
            if content.blocks[index].kind == "heading" and content.blocks[index].level == 1
        )
        memory = content.blocks[start:end]
        headings = [block.text for block in memory if block.kind == "heading"]
        for title in (
            "Protecciones colectivas y equipos de protección individual",
            "Protecciones colectivas", "Equipos de protección individual",
            "Señalización del centro de trabajo", "Orden y limpieza",
            "Equipos de trabajo y organización de los tajos",
            "Utilización y mantenimiento de equipos",
            "Almacenamiento y manipulación de materiales",
            "Iluminación de puestos y recorridos", "Circulación de personas y vehículos",
            "Instalación eléctrica provisional, si fuese necesaria",
            "Emergencias y primeros auxilios", "Actuación en caso de accidente",
            "Incendio y derrames",
        ):
            self.assertEqual(headings.count(title), 1, title)
        ppe = next(block for block in memory if block.kind == "table" and block.headers[0] == "EPI")
        self.assertTrue(ppe.blue_table)
        self.assertEqual(len(ppe.rows), 9)
        rows = {row[0]: row[1:] for row in ppe.rows}
        self.assertIn("sin imponerlo automáticamente", rows["Casco de seguridad"][1])
        self.assertIn("solo cuando la evaluación lo requiera", rows["Protección respiratoria"][1])
        text = "\n".join(block.text for block in memory)
        for requirement in (
            "personal máximo simultáneo y la duración de la Tabla 1",
            "agua potable, aseos", "botiquín portátil accesible", "caducidades",
            "sobreintensidades y contactos eléctricos", "desconexión y consignación",
            "palés se apilarán conforme a las indicaciones del fabricante",
            "botellas de gases", "sin exposición a humos ni riesgos adicionales",
            "Los absorbentes y residuos contaminados",
        ):
            self.assertIn(requirement, text)
        for example_detail in (
            "Por tratarse de una obra urbana distribuida", "se establecen dos puntos de acceso",
            "21 A 113 B", "UNE 1-011-75", "20 lux", "24 V", "1.8m",
            "PÁRROCO JOAQUÍN", "MAQUEDANO", "ANEJO Nº 07",
        ):
            self.assertNotIn(example_detail.casefold(), text.casefold())

    def test_catalogue_follows_overview_and_includes_references_from_project_text(self):
        """Incluye tambien referencias redactadas fuera de las fichas y evita duplicados."""
        app = self.make_app("odt")
        app.safety_health_state["resumen_actuacion"] = (
            "Control de R-EXPLOSIÓN, R-EXPLOSION, R-RUIDO y R-SIN-CATALOGO."
        )
        content = _build_study_content(app)
        overview_index = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "table" and block.headers[0] == "GRUPO DE RIESGO"
        )
        self.assertEqual(
            content.blocks[overview_index + 1].text, "Catálogo preventivo",
        )
        self.assertEqual(content.blocks[overview_index + 2].kind, "paragraph")
        self.assertIn("Los códigos citados", content.blocks[overview_index + 2].text)
        self.assertEqual(content.blocks[overview_index + 3].kind, "caption")
        self.assertTrue(content.blocks[overview_index + 3].text.startswith("Tabla 3."))
        catalogue = content.blocks[overview_index + 4]
        self.assertIn("El contratista deberá", content.blocks[overview_index + 5].text)
        self.assertIn("Plan de Seguridad y Salud (PSS)", content.blocks[overview_index + 5].text)
        self.assertTrue(catalogue.blue_table)
        definitions = {row[1]: row[2] for row in catalogue.rows}
        self.assertEqual(len(definitions), len(catalogue.rows))
        self.assertIn("onda de presión", definitions["R-EXPLOSION"])
        self.assertIn("tiempo de exposición", definitions["R-RUIDO"])
        self.assertIn("evaluación identifica riesgo residual", definitions["MP-HUMECTACION"])
        self.assertIn("[VALIDAR]", definitions["R-SIN-CATALOGO"])
        self.assertIn(
            "[VALIDAR] No existe descripción de catálogo para R-SIN-CATALOGO.",
            content.warnings,
        )
        summary_rows = dict(next(
            block for block in content.blocks if block.project_summary
        ).rows)
        for label, category in (("Riesgos detectados", "R"), ("Medidas preventivas", "MP")):
            expected = [row[1] for row in catalogue.rows if row[1].startswith(f"{category}-")]
            self.assertEqual(summary_rows[label], ", ".join(expected))
        self.assertEqual(summary_rows["Riesgos detectados"].count("R-EXPLOSION"), 1)

    @unittest.skip("Interfaz Tk retirada; pendiente de migraci?n a Qt.")
    def test_report_confirmation_is_informational_and_does_not_display_warnings(self):
        """Confirma PDF y ODT como el PPTP, aunque el generador devuelva avisos."""
        for output_format in ("pdf", "odt"):
            for warnings in ((), ("Aviso de revisión.",) * 4):
                with self.subTest(format=output_format, warnings=bool(warnings)):
                    proposal = create_autospec(SafetyProposalWindow, instance=True)
                    proposal.library_editor = None
                    proposal.app = self.make_app(output_format)
                    units = study_work_units(proposal.app)
                    proposal.app.safety_health_state["regulatory_review"] = {
                        "context": review_context(units), "laws": law_proposals(units), "ntps": {},
                    }
                    proposal.app._set_status = lambda message: None
                    proposal._create_chapter.return_value = True
                    target = f"estudio.{output_format}"
                    with patch(
                        "tools.safety_health.filedialog.asksaveasfilename", return_value=target,
                    ), patch(
                        "tools.safety_health.write_study_report", return_value=warnings,
                    ) as write, patch(
                        "tools.safety_health.messagebox.showwarning",
                    ) as notice, patch(
                        "tools.safety_health.messagebox.showinfo",
                    ) as success:
                        SafetyProposalWindow.generate_report(proposal)
                    write.assert_called_once_with(proposal.app, Path(target))
                    success.assert_called_once_with(
                        "Seguridad y salud", f"Estudio completo generado:\n{Path(target)}",
                        parent=proposal,
                    )
                    notice.assert_not_called()

    def test_descriptive_memory_removes_pending_states_and_renumbers_sections(self):
        """Mantiene los estados en el resumen, no en la narracion de la memoria."""
        app = self.make_app("odt")
        app.safety_health_state["external_conditions"] = {
            "instalaciones_provisionales": {
                "description": "Caseta y aseos previstos", "applicability": "pending",
            },
            "coordinador_seguridad_salud": {
                "description": "Coordinador designado", "applicability": "pending",
            },
        }
        content = _build_study_content(app)
        numbered = [
            block.text for block in _numbered_blocks(content.blocks) if block.kind == "heading"
        ]
        for heading in (
            "2 MEMORIA DESCRIPTIVA", "2.1 CRITERIOS PREVENTIVOS GENERALES",
            "2.2 CATÁLOGO PREVENTIVO", "2.6 INSTALACIONES PROVISIONALES Y EMERGENCIAS",
            "2.6.1 INSTALACIONES Y ORGANIZACIÓN DEL TAJO",
            "4.2 COORDINACIÓN DE ACTIVIDADES EMPRESARIALES",
        ):
            self.assertIn(heading, numbered)
        text = "\n".join(block.text for block in content.blocks)
        self.assertNotIn("APLICABILIDAD:", text)
        self.assertNotIn("El centro asistencial de referencia es", text)
        self.assertIn(
            "Se comprobarán los datos y teléfonos de los centros asistenciales de referencia "
            "(Tabla 1) antes del inicio,", text,
        )
        self.assertIn("CASETA Y ASEOS PREVISTOS.", text)
        self.assertIn("COORDINADOR DESIGNADO, cuando proceda.", text)
        summary = dict(next(block.rows for block in content.blocks if block.project_summary))
        for label in ("Instalaciones provisionales", "Coordinación de seguridad y salud"):
            self.assertIn("APLICABILIDAD: PENDIENTE", summary[label])
        units = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Fichas de unidades de obra aplicables"
        )
        self.assertTrue(content.blocks[units + 1].text.startswith("Este capítulo relaciona"))

    def test_chapter_three_summarizes_budget_units_and_emits_unique_bcca_fiches(self):
        """Vincula partidas y fichas BCCA sin clasificar por descripcion ni duplicar fichas."""
        app = self.make_app("odt")
        for code, summary in (
            ("01UPP00001", "Retirada de pavimento"),
            ("15APA00002", "Segunda arqueta"),
            ("15APP00001", "Pozo de registro"),
            ("15ASW00001", "Sumidero de variante"),
            ("15CPP00001", "Marca vial"),
            ("17RB00001", "Residuo bituminoso"),
            ("17RRBN00001", "Residuo con código anterior"),
        ):
            app.budget_items.append(ProjectItem(
                code, "01#", "ud", summary, measurement=1, unit_price=10,
            ))
        content = _build_study_content(app)
        start = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Fichas de unidades de obra aplicables"
        )
        end = next(
            index for index in range(start + 1, len(content.blocks))
            if content.blocks[index].kind == "heading" and content.blocks[index].level == 1
        )
        chapter = content.blocks[start:end]
        self.assertEqual(chapter[1].kind, "paragraph")
        self.assertIn("unidades sin ficha específica", chapter[1].text)
        self.assertEqual(chapter[2].kind, "caption")
        self.assertTrue(chapter[2].text.startswith("Tabla 5."))
        summary = chapter[3]
        self.assertEqual(summary.kind, "table")
        self.assertTrue(summary.blue_table)
        self.assertEqual(len([block for block in chapter if block.kind == "table"]), 1)
        rows = {row[0].split(" — ", 1)[0]: row for row in summary.rows}
        self.assertEqual(
            set(rows), {item.code for item in app.budget_items if not item.source.startswith("SS:")},
        )
        self.assertIn("Ficha BCCA: 15APA", rows["15APAA00001"][0])
        self.assertEqual(rows["15APAA00001"][1:], rows["15APA00002"][1:])
        self.assertNotEqual(rows["15APA00002"][1:], rows["15APP00001"][1:])
        self.assertIn("Ficha BCCA: 15CP", rows["15CPP00001"][0])
        self.assertIn("Ficha BCCA: 17RB", rows["17RRBN00001"][0])
        self.assertTrue(all("[VALIDAR]" in cell for cell in rows["OBRA001"]))
        for code, row in rows.items():
            if code != "OBRA001":
                self.assertRegex(row[1], r"^R-[A-Z0-9-]+(?:, R-[A-Z0-9-]+)*$")
                self.assertRegex(row[2], r"^MP-[A-Z0-9-]+(?:, MP-[A-Z0-9-]+)*$")
        fiche_starts = [
            index for index, block in enumerate(chapter)
            if block.kind == "heading" and block.level == 2
        ]
        self.assertEqual(
            [chapter[index].text for index in fiche_starts],
            ["Retirada de pavimentos", "Imbornales y arquetas de saneamiento",
             "Pozos de registro de saneamiento", "Sumideros - Varios",
             "Pinturas y marcas viales", "Residuos bituminosos"],
        )
        for index, finish in zip(fiche_starts, [*fiche_starts[1:], len(chapter)]):
            fiche = chapter[index:finish]
            self.assertEqual([block.text for block in fiche if block.level == 3 and block.kind == "heading"], [
                "Definición y alcance", "Riesgos detectados", "Medidas preventivas", "Validación",
            ])
            self.assertEqual([block.text for block in fiche if block.level == 4 and block.kind == "heading"], [
                "Protecciones colectivas", "Equipos de protección individual",
                "Equipos de trabajo",
            ])
            self.assertEqual(fiche[1].text, "Definición y alcance")
            risk_start = next(i for i, block in enumerate(fiche) if block.text == "Riesgos detectados")
            measure_start = next(i for i, block in enumerate(fiche) if block.text == "Medidas preventivas")
            risk_blocks = fiche[risk_start + 1:measure_start]
            self.assertTrue(all(block.kind == "paragraph" for block in risk_blocks))
            self.assertRegex(risk_blocks[-1].text, r"^R-[A-Z0-9-]+(?:, R-[A-Z0-9-]+)*\.$")
            self.assertTrue(any(block.kind == "list_item" for block in fiche))
        text = "\n".join(
            value for block in content.blocks for value in (block.text, *(cell for row in block.rows for cell in row))
        )
        self.assertNotIn("UO-", text)
        self.assertNotIn("FICHA_ESS_", text)
        self.assertEqual(
            content.blocks[end].text,
            "Seguimiento, control, coordinación, formación y documentación",
        )
        self.assertTrue(any(
            "código anterior 17RRBN00001 con la ficha BCCA 17RB" in warning
            for warning in content.warnings
        ))

    def test_followup_chapter_integrates_texts_without_duplicate_training_or_project_assumptions(self):
        """Reune obligaciones y condiciona salud, servicios y recursos a los riesgos reales."""
        content = _build_study_content(self.make_app("odt"))
        start = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading"
            and block.text == "Seguimiento, control, coordinación, formación y documentación"
        )
        end = next(
            index for index in range(start + 1, len(content.blocks))
            if content.blocks[index].kind == "heading" and content.blocks[index].level == 1
        )
        followup = content.blocks[start:end]
        self.assertEqual(followup[1].kind, "paragraph")
        expected = [
            "Seguimiento y control preventivo", "Coordinación de actividades empresariales",
            "Formación e información de los trabajadores", "Documentación preventiva disponible",
            "Vigilancia de la salud y primeros auxilios", "Localización y control de servicios ocultos",
            "Presencia de recursos preventivos", "Previsiones para trabajos posteriores",
        ]
        self.assertEqual(
            [block.text for block in followup if block.kind == "heading" and block.level == 2],
            expected,
        )
        for title in expected:
            self.assertEqual(sum(
                block.kind == "heading" and block.text == title for block in content.blocks
            ), 1)
            self.assertFalse(any(
                block.kind == "heading" and block.text == title for block in content.blocks[:start]
            ))
        text = "\n".join(block.text for block in followup)
        for obligation in (
            "Inspeccionar diariamente", "después de desplazar el tajo",
            "responsables, plazos de corrección y cierre", "su modificación y aprobación",
            "laboratorios, gestores de residuos", "consulta y participación de los trabajadores",
            "artículo 19", "formación teórica y práctica", "procedimientos, tecnologías o riesgos",
            "sin coste para el trabajador", "operadores y señalistas",
            "Plan de Seguridad y Salud aprobado y sus modificaciones aprobadas",
            "Libro de incidencias", "Documentación de mantenimiento",
            "artículo 22", "consentimiento del trabajador", "previo informe de sus representantes",
            "menores molestias posibles", "no los datos clínicos individuales",
            "No se fija en este Estudio una periodicidad médica genérica",
            "centros y teléfonos de la Tabla 1", "Cuando el tamaño de la obra o el tipo de actividad",
            "apartado 14 de la parte A del anexo IV", "se considerará en servicio",
            "se suspenderán los trabajos", "al titular del servicio",
            "cuando concurran los supuestos legalmente exigibles",
            "capacitación suficiente", "su corrección inmediata y se informará al empresario",
            "mientras", "medios de apertura y manipulación de tapas", "medidas químicas y térmicas",
        ):
            self.assertIn(obligation, text)
        self.assertNotIn("línea de alta tensión detectada", text)
        self.assertNotIn("APLICABILIDAD:", text)
        self.assertNotIn("{{", text)
        self.assertEqual(
            [block.text for block in _numbered_blocks(content.blocks)
             if block.kind == "heading" and block.level == 1],
            [
                "1 OBJETO Y ALCANCE", "2 MEMORIA DESCRIPTIVA",
                "3 FICHAS DE UNIDADES DE OBRA APLICABLES",
                "4 SEGUIMIENTO, CONTROL, COORDINACIÓN, FORMACIÓN Y DOCUMENTACIÓN",
                "1 OBJETO DEL PRESENTE PLIEGO",
                "2 CONDICIONES DE NATURALEZA LEGAL",
                "3 CONDICIONES DE NATURALEZA FACULTATIVA",
                "4 CONDICIONES DE NATURALEZA TÉCNICA",
                "5 CONDICIONES DE NATURALEZA ECONÓMICA",
            ],
        )
        self.assertTrue(all(
            block.level == 0 for block in followup if block.kind == "list_item"
        ))

    def test_specification_restarts_chapters_and_uses_actual_safety_budget(self):
        """Separa el pliego de la memoria y adapta obligaciones e importe al proyecto."""
        app = self.make_app("odt")
        safety_item = next(item for item in app.budget_items if item.source.startswith("SS:"))
        safety_item.measurement = 7
        safety_item.unit_price = 12.34
        blocks = _numbered_blocks(_build_study_content(app).blocks)
        document_starts = [index for index, block in enumerate(blocks) if block.kind == "document"]
        self.assertEqual([blocks[index].text for index in document_starts], [
            "DOCUMENTO I. MEMORIA", "DOCUMENTO III. PLIEGO DE CONDICIONES",
        ])
        specification = blocks[document_starts[1] + 1:]
        self.assertEqual(
            [block.text for block in specification if block.kind == "heading" and block.level == 1],
            [
                "1 OBJETO DEL PRESENTE PLIEGO",
                "2 CONDICIONES DE NATURALEZA LEGAL",
                "3 CONDICIONES DE NATURALEZA FACULTATIVA",
                "4 CONDICIONES DE NATURALEZA TÉCNICA",
                "5 CONDICIONES DE NATURALEZA ECONÓMICA",
            ],
        )
        self.assertEqual(
            [block.text for block in specification if block.kind == "heading" and block.level == 2],
            [
                "2.1 NORMATIVA DE APLICACIÓN", "2.2 CONDICIONES GENERALES DE EJECUCIÓN",
                "2.3 PRIORIDAD DE LA PROTECCIÓN COLECTIVA",
                "2.4 NORMAS UNE DE APLICACIÓN",
                "2.5 NOTAS TÉCNICAS DE PREVENCIÓN POR CAPÍTULOS DEL PRESUPUESTO",
                "3.1 PROMOTOR", "3.2 CONTRATISTA", "3.3 SUBCONTRATISTAS Y TRABAJADORES AUTÓNOMOS",
                "3.4 COORDINADOR DE SEGURIDAD Y SALUD DURANTE LA EJECUCIÓN",
                "3.5 RECURSOS PREVENTIVOS", "3.6 VIGILANCIA DE LA SALUD",
                "3.7 PLAN DE SEGURIDAD Y SALUD", "3.8 LIBROS Y CONTROL DOCUMENTAL",
                "3.9 CONTROL DE LA EFECTIVIDAD",
                "3.10 CONSULTA Y PARTICIPACIÓN DE LOS TRABAJADORES",
                "3.11 OBLIGACIONES DE LOS TRABAJADORES",
                "4.1 DE LOS MEDIOS DE PROTECCIÓN", "4.2 DE LA MAQUINARIA Y EQUIPOS",
                "4.3 SOBRE SEÑALIZACIÓN, BALIZAMIENTO Y PROTECCIÓN DE TERCEROS",
                "4.4 INSTALACIONES PROVISIONALES DE SALUD Y CONFORT",
                "5.1 CERTIFICACIÓN DE MEDIDAS DE SEGURIDAD Y SALUD",
                "5.2 RÉGIMEN ECONÓMICO DEL CONTRATO",
            ],
        )
        text = "\n".join(block.text for block in specification)
        legal_start = next(
            index for index, block in enumerate(specification)
            if block.text == "2 CONDICIONES DE NATURALEZA LEGAL" and block.kind == "heading"
        )
        self.assertEqual(specification[1].text, (
            "El objeto de este Pliego de Condiciones es fijar condiciones generales y particulares "
            "por las que se desarrollarán los trabajos y se utilizarán las dotaciones de Seguridad "
            "y Salud. Estas condiciones se plantean agrupadas de acuerdo con su naturaleza, en:"
        ))
        self.assertEqual(
            [(block.kind, block.text) for block in specification[2:legal_start]],
            [("list_item", f"- CONDICIONES DE NATURALEZA {nature}.")
             for nature in ("LEGAL", "FACULTATIVA", "TÉCNICA", "ECONÓMICA")],
        )
        self.assertIn("mediante el procedimiento del artículo 7 del Real Decreto 1627/1997", text)
        self.assertNotIn("capítulo 2 de este Pliego", text)
        for obligation in (
            "asciende a 86,38", "medición real", "no sustituye la revisión de sus cantidades",
            "por insuficiencia o agotamiento", "antes del comienzo de los trabajos",
            "Administración pública que haya adjudicado la obra, previo informe",
            "cuando no sea preceptivo, de la Dirección Facultativa",
            "cuando corresponda, con la Policía Local", "cuando proceda, los de fresado",
            "consentimiento del trabajador", "La documentación médica será confidencial",
            "actividades y situaciones en que resulte preceptiva",
        ):
            self.assertIn(obligation, text)
        self.assertNotIn("1.998,77", text)
        self.assertNotIn("{{", text)
        end = next(index for index, block in enumerate(specification) if block.text == "5 CONDICIONES DE NATURALEZA ECONÓMICA")
        economic_opening = specification[end + 1:end + 5]
        self.assertTrue(all(block.kind == "paragraph" for block in economic_opening))
        for block, beginning in zip(economic_opening, (
            "Salvo pacto en contrario, una vez al mes",
            "El abono de las certificaciones",
            "En caso de ejecutar en obra unidades no previstas",
            "En caso de plantearse una revisión de precios",
        )):
            self.assertTrue(block.text.startswith(beginning))
        self.assertIn("precios contratados por el Promotor", economic_opening[0].text)
        self.assertIn(
            "habiendo obtenido la aprobación previa de la Dirección Facultativa "
            "o la coordinación de Seguridad y Salud en fase de ejecución de las obras.",
            economic_opening[-1].text,
        )

    def test_specification_completes_participation_without_obsolete_facultative_duties(self):
        """Completa representación y trabajadores sin confundir competencias ni plantillas."""
        blocks = _numbered_blocks(_build_study_content(self.make_app("odt")).blocks)
        document_start = next(
            index for index, block in enumerate(blocks)
            if block.kind == "document" and block.text.startswith("DOCUMENTO III.")
        )
        specification = blocks[document_start + 1:]
        texts = [block.text for block in specification]
        facultative_end = texts.index("4 CONDICIONES DE NATURALEZA TÉCNICA")
        facultative = texts[:facultative_end]
        for heading in (
            "3.10 CONSULTA Y PARTICIPACIÓN DE LOS TRABAJADORES",
            "3.10.1 DELEGADOS DE PREVENCIÓN", "3.10.2 COMITÉ DE SEGURIDAD Y SALUD",
            "3.11 OBLIGACIONES DE LOS TRABAJADORES",
        ):
            self.assertEqual(facultative.count(heading), 1)
        self.assertEqual(facultative.count("3.8.1 LIBRO DE INCIDENCIAS"), 1)
        text = "\n".join(facultative)
        for required in (
            "técnico competente integrado en la Dirección Facultativa",
            "dentro de sus respectivas competencias",
            "no atribuye por sí sola facultades para modificar precios",
            "artículo 11.2 del Real Decreto 1627/1997",
            "La documentación de empresas y personal se comprobará por los medios vigentes",
            "actualizando las incorporaciones y bajas",
            "sin recopilar datos personales ajenos a su finalidad",
            "una copia del Plan y de sus modificaciones",
            "reglas legales de cómputo de plantilla y contratación temporal",
            "cincuenta o más trabajadores",
            "No se deducirá su obligatoriedad exclusivamente de sumar trabajadores",
            "Se reunirá trimestralmente y siempre que lo solicite",
            "en igual número", "con voz y sin voto",
            "artículo 32 bis", "artículo 22 bis",
            "La presencia requerida por la Inspección",
            "su antigüedad no implica por sí sola",
            "Permanecerán durante todo el tiempo",
            "el empresario actuará de inmediato",
            "No anular ni poner fuera de funcionamiento",
            "Informar de inmediato al superior",
            "no reducirán el deber de protección del empresario",
        ):
            self.assertIn(required, text)
        for obsolete in (
            "independiente de la Dirección Facultativa",
            "podrá ser objeto de demolición", "TC1", "TC2", "libro de matrícula",
            "antes del día 10", "superior a 50", "homologados por el organismo",
            "Hash:", "CÓDIGO CSV", "ANEJO Nº",
        ):
            self.assertNotIn(obsolete, text)
        workers = specification[
            texts.index("3.11 OBLIGACIONES DE LOS TRABAJADORES"):facultative_end
        ]
        self.assertEqual(sum(block.kind == "list_item" for block in workers), 6)

    def test_specification_integrates_protections_and_welfare_in_technical_conditions(self):
        """Situa protecciones e higiene en el pliego sin imponer dotaciones universales."""
        blocks = _numbered_blocks(_build_study_content(self.make_app("odt")).blocks)
        document_start = next(
            index for index, block in enumerate(blocks)
            if block.kind == "document" and block.text.startswith("DOCUMENTO III.")
        )
        specification = blocks[document_start + 1:]
        texts = [block.text for block in specification]
        start = texts.index("4 CONDICIONES DE NATURALEZA TÉCNICA")
        end = texts.index("5 CONDICIONES DE NATURALEZA ECONÓMICA")
        technical = specification[start:end]
        self.assertEqual([block.text for block in technical if block.kind == "heading"], [
            "4 CONDICIONES DE NATURALEZA TÉCNICA",
            "4.1 DE LOS MEDIOS DE PROTECCIÓN",
            "4.1.1 MEDIOS DE PROTECCIÓN COLECTIVA",
            "4.1.2 MEDIOS DE PROTECCIÓN INDIVIDUAL",
            "4.2 DE LA MAQUINARIA Y EQUIPOS",
            "4.3 SOBRE SEÑALIZACIÓN, BALIZAMIENTO Y PROTECCIÓN DE TERCEROS",
            "4.4 INSTALACIONES PROVISIONALES DE SALUD Y CONFORT",
            "4.4.1 VESTUARIOS", "4.4.2 ASEOS, LAVABOS Y DUCHAS",
            "4.4.3 RETRETES", "4.4.4 COMEDORES, COCINA Y LOCALES DE DESCANSO",
        ])
        protection = "\n".join(texts[
            texts.index("4.1 DE LOS MEDIOS DE PROTECCIÓN"):
            texts.index("4.2 DE LA MAQUINARIA Y EQUIPOS")
        ])
        for required in (
            "antes de comenzar los trabajos", "medidas alternativas de eficacia equivalente",
            "El contratista organizará las revisiones por personal competente",
            "El empresario organizará su mantenimiento y reposición",
            "los suministrará gratuitamente", "marcado CE en el soporte que legalmente corresponda",
            "Si excepcionalmente fueran compartidos", "impedir su uso",
        ):
            self.assertIn(required, protection)
        welfare = "\n".join(texts[
            texts.index("4.4 INSTALACIONES PROVISIONALES DE SALUD Y CONFORT"):end
        ])
        for required in (
            "anexo IV del Real Decreto 1627/1997", "convenio colectivo aplicable",
            "número máximo de trabajadores que coincidan", "respetando los mínimos exigibles",
            "limpieza diaria", "disponibilidad efectiva, proximidad, capacidad",
            "guardar bajo llave", "agua corriente fría y caliente",
            "utilización por separado", "sistemas sanitarios autónomos",
            "No se admitirán vertidos incontrolados", "agua potable",
            "La capacidad se revisará si cambia la plantilla simultánea",
        ):
            self.assertIn(required, welfare)
        for obsolete in (
            "1 ducha por cada 10", "1 retrete por cada 25",
            "2 m² por cada", "2,30 metros", "cada semana) por el Delegado",
        ):
            self.assertNotIn(obsolete, "\n".join(block.text for block in technical))

    def test_specification_integrates_effectiveness_and_current_documentary_requirements(self):
        """Integra controles y tramites sin duplicar PSS ni mantener obligaciones derogadas."""
        blocks = _numbered_blocks(_build_study_content(self.make_app("odt")).blocks)
        start = next(
            index for index, block in enumerate(blocks)
            if block.kind == "document" and block.text.startswith("DOCUMENTO III.")
        )
        specification = blocks[start + 1:]
        texts = [block.text for block in specification]
        for heading in (
            "3.7.1 ACTA O RESOLUCIÓN DE APROBACIÓN DEL PLAN",
            "3.7.2 COMUNICACIÓN DE APERTURA DEL CENTRO DE TRABAJO",
            "3.7.3 MODIFICACIONES Y SITUACIONES NO PREVISTAS",
            "3.8.1 LIBRO DE INCIDENCIAS", "3.8.2 LIBRO DE ÓRDENES Y ASISTENCIAS",
            "3.8.3 DILIGENCIAS DE LA INSPECCIÓN DE TRABAJO Y SEGURIDAD SOCIAL",
            "3.8.4 LIBRO DE SUBCONTRATACIÓN",
            "3.9.1 CUADRO DE CONTROL Y SEGUIMIENTO", "3.9.2 ÍNDICES DE CONTROL",
            "3.9.3 PARTES DE ACCIDENTES E INVESTIGACIÓN",
            "3.9.4 PARTES DE DEFICIENCIAS Y COMPROBACIÓN DEL CIERRE",
        ):
            self.assertEqual(texts.count(heading), 1)
        text = "\n".join(texts)
        for required in (
            "I.I. = (A / T) x 100.", "I.F. = (A / H) x 1.000.000.",
            "I.G. = (J / H) x 1.000.", "D.M.I. = J / A.",
            "número medio de trabajadores expuestos",
            "Cuando el denominador de un indicador sea cero",
            "No calculable", "una casilla vacía no acreditará conformidad",
            "número total de conceptos efectivamente examinados",
            "persona que haya comprobado su implantación y eficacia",
            "hechos comprobados de las hipótesis",
            "No se establece un visado colegial obligatorio general",
            "previo informe del coordinador",
            "adjuntarán el Plan de Seguridad y Salud y su aprobación",
            "antes del comienzo de sus trabajos",
            "se expondrá en lugar visible", "plazo de diez días",
            "incumplimiento de advertencias u observaciones previamente anotadas",
            "supuesto de paralización regulado en el artículo 14",
            "plazo de veinticuatro horas",
            "No se exigirá adquirir ni habilitar un nuevo Libro de Visitas",
            "cinco años desde su expedición",
            "habilitado por la autoridad laboral competente antes de subcontratar",
            "Los técnicos y delegados de prevención",
            "precios contradictorios", "trabajos por administración",
            "indemnizaciones mutuas", "liquidación económica y final",
            "no atribuye por sí sola competencia para aprobar precios",
        ):
            self.assertIn(required, text)
        for removed in ("I.I. = (A / H)", "visado por el Colegio Profesional correspondiente",
                        "Hash:", "CÓDIGO CSV", "FIRMANTE NIF/CIF", "ANEJO Nº"):
            self.assertNotIn(removed, text)
        economic_start = texts.index("5 CONDICIONES DE NATURALEZA ECONÓMICA")
        economic_headings = [
            block.text for block in specification[economic_start:]
            if block.kind == "heading"
        ]
        self.assertEqual(economic_headings, [
            "5 CONDICIONES DE NATURALEZA ECONÓMICA",
            "5.1 CERTIFICACIÓN DE MEDIDAS DE SEGURIDAD Y SALUD",
            "5.2 RÉGIMEN ECONÓMICO DEL CONTRATO",
        ])

    def test_general_une_list_excludes_withdrawn_drafts_and_unrelated_number_matches(self):
        """Lista equipos preventivos vigentes sin confundir familias EN e ISO homonimas."""
        catalog = load_reference_entries("UNE")
        ladder = next(
            entry for entry in catalog
            if entry.code.startswith("UNE-EN 131-1:") and entry.status == "Vigente"
        )
        excluded = (
            "PNE-EN 131-1:2099", "UNE-EN ISO 354:2004", "UNE-EN ISO 361:2016",
            "UNE-EN 16321-1:2014", "UNE-EN ISO 374-6:2025",
        )
        with patch(
            "tools.safety_health.regulations.load_reference_entries",
            return_value=(*catalog, ladder, *(replace(ladder, code=code) for code in excluded)),
        ):
            markdown = general_une_references()
        blocks = _markdown_blocks(markdown)
        codes = [
            block.text.removeprefix("- ").split(" — ", 1)[0]
            for block in blocks if block.kind == "list_item"
        ]
        self.assertEqual(len(codes), len(set(codes)))
        for prefix in (
            "UNE-EN 131-", "UNE-EN 352-", "UNE-EN 143:", "UNE-EN 388:",
            "UNE-EN ISO 20345:", "UNE-EN 1263-", "UNE-EN 358:", "UNE-EN 361:",
        ):
            self.assertTrue(any(code.startswith(prefix) for code in codes), prefix)
        current_codes = {entry.code for entry in catalog if entry.status == "Vigente"}
        self.assertTrue(set(codes) <= current_codes)
        for code in (*excluded, "UNE-EN 131-1:1994", "UNE-EN 143:2001"):
            self.assertNotIn(code, codes)
        self.assertIn("Los cinturones de retención o posicionamiento no sustituyen", markdown)
        with patch("tools.safety_health.regulations.load_reference_entries", return_value=()):
            with self.assertRaisesRegex(ValueError, "no contiene referencias vigentes"):
                general_une_references()

    def test_ntp_proposals_use_explicit_references_and_do_not_confirm_thematic_matches(self):
        """No infiere riesgos por la descripcion ni atribuye vigencia a coincidencias BCCA."""
        app = self.make_app("pdf")
        units = study_work_units(app)
        catalog = load_ntps()
        proposals = ntp_proposals(units, catalog)
        self.assertIn("NTP 278", proposals["15APAA00001"])
        self.assertIn("MP-ESTABILIDAD-EXCAVACION", proposals["15APAA00001"]["NTP 278"])
        self.assertEqual(proposals["OBRA001"], {})
        self.assertFalse(law_proposals([units[1]])["RD614-2001"])
        self.assertTrue(law_proposals([units[0]])["RD614-2001"])
        waste = WorkUnit("17RB00001", "Residuo", ("17RB",), (), ())
        thematic = ntp_proposals([waste], catalog)[waste.code]
        self.assertIn("NTP 781", thematic)
        self.assertFalse(initially_selected(catalog["NTP 781"], thematic["NTP 781"]))
        self.assertIn("Actualizada", catalog["NTP 359"].status)
        self.assertFalse(initially_selected(catalog["NTP 359"], "Propuesta por R-QUIMICO."))
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            for name in ("ntps.csv", "NTP_tematica_por_codigo_BCCA.csv"):
                (data / name).write_text("catálogo inicial", encoding="utf-8")
            with patch("tools.safety_health.regulations.DATA_ROOT", data):
                initial = review_context(units)
                (data / "ntps.csv").write_text("catálogo actualizado", encoding="utf-8")
                self.assertNotEqual(review_context(units), initial)

    def test_confirmed_norms_and_manual_ntps_survive_budget_sync_and_export(self):
        """Exporta solo decisiones confirmadas con codigos reales y conserva asignaciones manuales."""
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(format=output_format):
                    app = self.make_app(output_format)
                    units = study_work_units(app)
                    review = {
                        "context": review_context(units),
                        "laws": {"L31-1995": True, "RD614-2001": False},
                        "ntps": {"15APAA00001": {"NTP 278": True, "NTP 477": False},
                                 "OBRA001": {"NTP 4": True, "NTP 278": True}},
                    }
                    app.chapter_order = ["02#", "01#", "CAP.03#"]
                    app.safety_health_state["regulatory_review"] = review
                    restored = restore_project_state(app.safety_health_state)
                    self.assertEqual(restored["regulatory_review"], review)
                    synchronize_budget(
                        app, [SafetyLine("SS0001", "ud", "Protección", Decimal("3.11"), Decimal(1))],
                        Decimal(2),
                    )
                    self.assertEqual(app.safety_health_state["regulatory_review"], review)
                    content = _build_study_content(app)
                    table = next(
                        block for block in content.blocks if block.headers
                        and block.headers[-1] == "NTP confirmadas"
                    )
                    self.assertTrue(table.blue_table)
                    self.assertEqual(table.headers, ("Capítulo del presupuesto", "NTP confirmadas"))
                    self.assertIn("Pavimentos", table.rows[0][0])
                    self.assertIn("Saneamiento", table.rows[1][0])
                    self.assertEqual(len(table.rows[0][1].splitlines()), 2)
                    self.assertTrue(all(line.startswith("• ") for line in table.rows[0][1].splitlines()))
                    self.assertIn("NTP 278 —", table.rows[0][1])
                    self.assertIn("NTP 4 —", table.rows[0][1])
                    self.assertIn("Sin NTP adicionales", table.rows[1][1])
                    target = Path(folder) / f"normativa.{output_format}"
                    write_study_report(app, target)
                    if output_format == "pdf":
                        text = "\n".join(page.extract_text() for page in PdfReader(target).pages)
                    else:
                        with ZipFile(target) as archive:
                            tree = ET.fromstring(archive.read("content.xml"))
                        text = "".join(tree.itertext())
                    text = " ".join(text.split())
                    for label in (
                        "1 OBJETO DEL PRESENTE PLIEGO", "Tabla 6.", "NTP 278",
                        "NTP 4", "Ley 31/1995", "no disposiciones legales obligatorias",
                        "2 CONDICIONES DE NATURALEZA LEGAL",
                        "3 CONDICIONES DE NATURALEZA FACULTATIVA",
                        "4 CONDICIONES DE NATURALEZA TÉCNICA",
                        "5 CONDICIONES DE NATURALEZA ECONÓMICA",
                    ):
                        self.assertIn(label, text)
                    self.assertNotIn("NTP 477", text)
                    self.assertEqual(len(re.findall(r"NTP\s+278(?!\d)", text)), 1)
                    self.assertEqual(len(re.findall(r"NTP\s+4(?!\d)", text)), 1)
                    self.assertNotIn("Real Decreto 614/2001", text)
                    self.assertNotIn("Lucena", text)
                    app.budget_items.append(ProjectItem("NUEVA", "01#", "ud", "Nueva fase"))
                    changed = _build_study_content(app)
                    table = next(
                        block for block in changed.blocks if block.headers
                        and block.headers[-1] == "NTP confirmadas"
                    )
                    self.assertTrue(all("pendiente de confirmación" in row[1] for row in table.rows))
                    self.assertTrue(any("Confirmar normativa y NTP" in warning for warning in changed.warnings))

    def test_selected_chapters_filter_analysis_without_changing_project_amounts(self):
        """Excluye fichas, recursos y mano de obra ajenos al ambito, no el importe legal global."""
        from tools.safety_health import evaluate_study_criteria, proposed_lines

        app = self.make_app("odt")
        for chapter, title, code, summary in (
            ("17#", "Gestión de RCDs", "17RB00001", "Residuo descartado"),
            ("18#", "Control de Calidad", "18HHI00001", "Ensayo de túnel descartado"),
            ("03#", "Trabajos no seleccionados", "01UPP00001", "Escalera portátil SOLOEXCLUIDO"),
        ):
            app.chapter_order.append(chapter)
            app.chapter_titles[chapter] = title
            app.budget_items.append(ProjectItem(
                code, chapter, "m2", summary, measurement=1000, unit_price=1000, rendimiento=4000,
            ))
        app.safety_health_state["chapter_selection"] = {"03#": False}
        selection = {chapter.code: chapter.selected for chapter in study_chapters(app)}
        self.assertEqual(selection, {"01#": True, "02#": True, "17#": False, "18#": False, "03#": False})
        app.planning_state = {"activities": [
            {"chapter": "01#", "code": "15APAA00001", "manual_duration": 3},
            {"chapter": "02#", "code": "OBRA001", "manual_duration": 4, "pred": 1},
            {"chapter": "17#", "code": "17RB00001", "manual_duration": 999},
        ]}
        original = list(app.budget_items)
        assessment = evaluate_study_criteria(app)
        self.assertEqual(assessment.duration_days, 7)
        self.assertEqual(assessment.worker_days, Decimal(100))
        self.assertEqual(assessment.peak_workers, Decimal(17))
        self.assertTrue(assessment.criterion_1_met)
        self.assertFalse(assessment.criterion_3_met)
        self.assertFalse(assessment.special_work)
        self.assertGreater(assessment.contract_budget, Decimal(3000000))
        content = _build_study_content(app)
        text = "\n".join(
            value for block in content.blocks
            for value in (block.text, *(cell for row in block.rows for cell in row))
        )
        for excluded in ("17RB00001", "18HHI00001", "01UPP00001", "SOLOEXCLUIDO",
                         "Gestión de RCDs", "Control de Calidad", "Trabajos no seleccionados"):
            self.assertNotIn(excluded.casefold(), text.casefold())
        self.assertEqual([unit.code for unit in study_work_units(app)], ["15APAA00001", "OBRA001"])
        with tempfile.TemporaryDirectory() as folder:
            matrix = Path(folder) / "matriz.csv"
            matrix.write_text(
                "uo_codigo,ss_codigo,ss_unidad,ss_resumen,ss_precio,peso_presupuesto_ss\n"
                "15APAA00001,INCLUIDA,ud,Proteccion incluida,1,1\n"
                "17RB00001,EXCLUIDA,ud,Proteccion excluida,1,100\n",
                encoding="utf-8",
            )
            with patch("tools.safety_health.RELATIONSHIP_PATH", matrix):
                self.assertEqual([line.code for line in proposed_lines(study_items(app))], ["INCLUIDA"])
        self.assertEqual(app.budget_items, original)
        synchronize_budget(
            app, [SafetyLine("S", "ud", "Protección", Decimal(1), Decimal(1))], Decimal(2),
        )
        self.assertEqual(app.safety_health_state["chapter_selection"], {"03#": False})
        self.assertAlmostEqual(
            sum(item.import_total for item in app.budget_items if item.source.startswith("SS:")),
            3.0, places=2,
        )
        app.safety_health_state["chapter_selection"]["17#"] = True
        self.assertIn("17RB00001", [item.code for item in study_items(app)])
        self.assertIn("17RB00001", [unit.code for unit in study_work_units(app)])

    @unittest.skip("Interfaz Tk retirada; pendiente de migraci?n a Qt.")
    def test_chapter_screen_precedes_analysis_and_persists_explicit_opt_in(self):
        """No consulta la matriz ni calcula criterios antes de aceptar las casillas."""
        from tools.safety_health import open_tool

        app = self.make_app("odt")
        app.budget_items.extend((
            ProjectItem("17RB00001", "17#", "ud", "Residuo", measurement=1, unit_price=1),
            ProjectItem("18HHI00001", "18#", "ud", "Ensayo", measurement=1, unit_price=1),
        ))
        app.chapter_order.extend(("17#", "18#"))
        app.chapter_titles.update({"17#": "Tratamiento de residuos", "18#": "Control de calidad"})
        root = tk.Tk()
        root.withdraw()
        app.root, app._safety_health_window = root, None
        try:
            with patch("tools.safety_health.SafetyHealthWindow") as analysis, patch(
                "tools.safety_health.proposed_lines",
            ) as proposals, patch("tools.safety_health.evaluate_study_criteria") as criteria:
                open_tool(app)
                screen = app._safety_health_window
                self.assertIsInstance(screen, ChapterSelectionWindow)
                self.assertFalse(screen.choices["17#"].get())
                self.assertFalse(screen.choices["18#"].get())
                proposals.assert_not_called()
                criteria.assert_not_called()
                analysis.assert_not_called()
                for variable in screen.choices.values():
                    variable.set(False)
                with patch("tools.safety_health.chapter_selection.messagebox.showerror") as error:
                    screen.accept()
                error.assert_called_once()
                analysis.assert_not_called()
                screen.choices["01#"].set(True)
                screen.choices["17#"].set(True)
                screen.accept()
                analysis.assert_called_once_with(root, app)
                self.assertEqual(
                    restore_project_state(app.safety_health_state)["chapter_selection"],
                    {"01#": True, "02#": False, "17#": True, "18#": False},
                )
                self.assertEqual(app.safety_health_state["lines"], [])
                self.assertNotIn("study_criteria", app.safety_health_state)
            reopened = ChapterSelectionWindow(app, lambda choices: None)
            self.assertTrue(reopened.choices["17#"].get())
            self.assertFalse(reopened.choices["02#"].get())
            reopened.destroy()
        finally:
            root.destroy()

    @unittest.skip("Interfaz Tk retirada; pendiente de migraci?n a Qt.")
    def test_first_chapter_confirmation_preserves_legacy_budget_lines(self):
        """Conserva las partidas SS de proyectos antiguos si no cambia el ambito completo."""
        from tools.safety_health import SafetyHealthWindow, open_tool

        app = self.make_app("odt")
        app.safety_health_state = {}
        root = tk.Tk()
        root.withdraw()
        app.root, app._safety_health_window = root, None
        try:
            with patch("tools.safety_health.proposed_lines", return_value=[]) as proposals, patch(
                "tools.safety_health.messagebox.showerror",
            ) as error:
                open_tool(app)
                app._safety_health_window.accept()
                window = app._safety_health_window
                self.assertIsInstance(window, SafetyHealthWindow)
                self.assertEqual([line.code for line in window.lines], ["SS0001"])
                self.assertEqual(window.lines[0].price, Decimal("3.11"))
                proposals.assert_not_called()
                error.assert_not_called()
        finally:
            root.destroy()

    @unittest.skip("Interfaz Tk retirada; pendiente de migraci?n a Qt.")
    def test_regulatory_window_preserves_exclusions_and_manual_assignments_on_reopening(self):
        """Guarda la revision explicita y mantiene independientes las parejas partida/NTP."""
        from tools.safety_health.regulatory_review import RegulatoryReviewWindow

        app = self.make_app("pdf")
        app.budget_items.append(ProjectItem("15APA00002", "01#", "ud", "Otra arqueta"))
        units = study_work_units(app)
        root = tk.Tk()
        root.withdraw()
        try:
            window = RegulatoryReviewWindow(root, app, units, app.safety_health_state)
            window.legal_tree.selection_set("RD614-2001")
            window._toggle_law()
            row = next(
                row for row, pair in window.rows.items() if pair == ("15APAA00001", "NTP 278")
            )
            window.ntp_tree.selection_set(row)
            window._toggle_ntp()
            window.unit_choice.set("OBRA001")
            window.ntp_choice.set("NTP 4")
            window._assign()
            window._accept()
            reopened = RegulatoryReviewWindow(root, app, units, app.safety_health_state)
            self.assertFalse(reopened.laws["RD614-2001"])
            self.assertFalse(reopened.ntps["15APAA00001"]["NTP 278"])
            self.assertTrue(reopened.ntps["15APA00002"]["NTP 278"])
            self.assertTrue(reopened.ntps["OBRA001"]["NTP 4"])
            self.assertEqual(app.safety_health_state["regulatory_review"]["context"], review_context(units))
            reopened.destroy()
        finally:
            root.destroy()

    def test_technical_proposals_use_scoped_references_real_resources_and_confirmed_conditions(self):
        """No interpreta palabras de UO sin ficha ni maquinaria de capítulos excluidos."""
        app = self.make_app("pdf")
        app.safety_health_state["chapter_selection"] = {"01#": False, "02#": True}
        item = app.budget_items[1]
        item.summary = "Camión grúa, extendido y compactación"
        _context, proposals, pending = study_technical_review(app)
        self.assertEqual(proposals, {})
        self.assertTrue(any("Partida sin ficha" in message for message in pending))
        item.component_concepts.update({
            "AUX": Concept("AUX", "ud", "Auxiliar", children=[("GRUA", 1)]),
            "GRUA": Concept("GRUA", "h", "Camión grúa", ctype="2"),
            "MO": Concept("MO", "h", "Conductor de rodillo", ctype="1"),
            "CERO": Concept("CERO", "h", "Rodillo compactador", ctype="2"),
        })
        item.decomposition = [("AUX", 1), ("MO", 1), ("CERO", 0)]
        app.safety_health_state["external_conditions"] = {
            "peatones_terceros": {"applicability": "yes", "description": "Accesos a viviendas."},
            "trafico_existente": {"applicability": "pending", "description": "Por confirmar."},
        }
        _context, proposals, _pending = study_technical_review(app)
        self.assertIn("GRUA", proposals["lifting"])
        self.assertIn("mobile", proposals)
        self.assertIn("pedestrians", proposals)
        self.assertNotIn("paving", proposals)
        self.assertNotIn("openings", proposals)
        self.assertNotIn("signage", proposals)
        app.safety_health_state["chapter_selection"]["01#"] = True
        self.assertIn("openings", study_technical_review(app)[1])

    def test_technical_confirmation_requires_current_sources_and_complete_boolean_choices(self):
        """Invalida cambios de ámbito, referencias, equipos, entorno o prescripciones."""
        units = study_work_units(self.make_app("pdf"))
        refs = {"R": ["R-TERCEROS"]}
        resources = [("GRUA", "Camión grúa")]
        context, _proposals = technical_review_data(units, refs, resources, {}, "Pliego")
        saved = {
            "context": context,
            "blocks": {block.key: block.key == "lifting" for block in TECHNICAL_BLOCKS},
        }
        self.assertTrue(technical_review_confirmed(saved, context))
        changed_contexts = (
            technical_review_data(units[1:], refs, resources, {}, "Pliego")[0],
            technical_review_data(units, {"EQ": ["EQ-CORTE"]}, resources, {}, "Pliego")[0],
            technical_review_data(units, refs, [("GRUA", "Grúa revisada")], {}, "Pliego")[0],
            technical_review_data(units, refs, resources, {
                "trafico_existente": {"description": "Vía pública.", "applicability": "yes"},
            }, "Pliego")[0],
            technical_review_data(units, refs, resources, {}, "Pliego revisado")[0],
        )
        for changed in changed_contexts:
            self.assertFalse(technical_review_confirmed(saved, changed))
        self.assertFalse(technical_review_confirmed(
            {"context": context, "blocks": {"lifting": True}}, context,
        ))
        for choices in ({"lifting": "sí"}, {"desconocido": True}):
            with self.assertRaises(ValueError):
                restore_technical_review({"context": context, "blocks": choices})

    def test_technical_template_distinguishes_explicit_empty_selection_from_pending_review(self):
        """Conserva el núcleo, admite exclusión total y rechaza marcadores incompletos."""
        template = "Núcleo general.\n<!-- ESS-TECH-REVIEW -->\n" + "\n".join(
            f"<!-- ESS-TECH:{block.key} -->\n#### {block.title}\nContenido {block.key}.\n<!-- /ESS-TECH -->"
            for block in TECHNICAL_BLOCKS
        )
        context = "0" * 64
        saved = {
            "context": context, "blocks": {block.key: False for block in TECHNICAL_BLOCKS},
        }
        pending = []
        text = selected_technical_template(template, saved, context, pending)
        self.assertIn("Núcleo general.", text)
        self.assertIn("Tras la revisión no se han seleccionado", text)
        self.assertNotIn("Contenido", text)
        self.assertNotIn("ESS-TECH", text)
        self.assertEqual(pending, [])
        text = selected_technical_template(template, None, context, pending)
        self.assertIn("este documento no acredita su innecesariedad", text)
        self.assertTrue(pending)
        for malformed in (
            template.replace("<!-- /ESS-TECH -->", "", 1),
            template + "\n<!-- ESS-TECH:desconocido -->\n",
        ):
            with self.assertRaises(ValueError):
                selected_technical_template(malformed, saved, context, [])

    @unittest.skip("Interfaz Tk retirada; pendiente de migraci?n a Qt.")
    def test_technical_window_preserves_manual_choices_and_exports_only_confirmed_blocks(self):
        """Persiste exclusiones e inclusiones manuales y comparte el filtro PDF/ODT."""
        app = self.make_app("pdf")
        context, proposals, pending = study_technical_review(app)
        root = tk.Tk()
        root.withdraw()
        try:
            window = TechnicalReviewWindow(root, app, context, proposals, pending, app.safety_health_state)
            self.assertTrue(window.choices["openings"])
            self.assertFalse(window.choices["paving"])
            for key in ("openings", "paving"):
                window.tree.selection_set(key)
                window._toggle()
            window._accept()
            app.safety_health_state = restore_project_state(app.safety_health_state)
            synchronize_budget(
                app, [SafetyLine("SS0001", "ud", "Protecciones", Decimal("3.11"), Decimal(1))],
                Decimal("2"),
            )
            context, proposals, pending = study_technical_review(app)
            self.assertTrue(technical_review_confirmed(app.safety_health_state["technical_review"], context))
            reopened = TechnicalReviewWindow(root, app, context, proposals, pending, app.safety_health_state)
            self.assertFalse(reopened.choices["openings"])
            self.assertTrue(reopened.choices["paving"])
            reopened.destroy()
        finally:
            root.destroy()
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                app.report_settings.output_format = output_format
                path = Path(folder) / f"tecnica.{output_format}"
                warnings = write_study_report(app, path)
                if output_format == "pdf":
                    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
                else:
                    with ZipFile(path) as archive:
                        text = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                text = " ".join(text.split())
                self.assertIn("EQUIPOS DE EXTENDIDO Y COMPACTACIÓN", text)
                self.assertNotIn("PROTECCIÓN DE HUECOS, ARQUETAS Y REGISTROS", text)
                self.assertIn("Las plataformas, accesos y peldaños", text)
                self.assertIn("La protección respiratoria no sustituirá", text)
                self.assertIn("energías aisladas o controladas", text)
                self.assertNotIn("ESS-TECH", text)
                self.assertFalse(any("Confirmar las condiciones técnicas específicas" in item for item in warnings))
        app.safety_health_state["external_conditions"] = {
            "trafico_existente": {"description": "Nueva afección.", "applicability": "yes"},
        }
        draft = _build_study_content(app)
        self.assertTrue(any("Confirmar las condiciones técnicas específicas" in item for item in draft.warnings))
        draft_text = "\n".join(block.text for block in draft.blocks)
        self.assertIn("este documento no acredita su innecesariedad", draft_text)
        self.assertNotIn("EQUIPOS DE EXTENDIDO Y COMPACTACIÓN", draft_text)

    @unittest.skip("Interfaz Tk retirada; pendiente de migraci?n a Qt.")
    def test_cancelling_required_technical_review_prevents_export(self):
        """No permite que la selección pendiente se eluda al cancelar la revisión."""
        proposal = create_autospec(SafetyProposalWindow, instance=True)
        proposal.library_editor = None
        proposal.app = self.make_app("pdf")
        proposal.app.safety_health_state["regulatory_review"] = {
            "context": review_context(study_work_units(proposal.app)), "laws": {}, "ntps": {},
        }
        proposal._create_chapter.return_value = True
        for window in (None, object()):
            with self.subTest(window=window):
                proposal.owner = SimpleNamespace(open_technical_review=lambda: window)
                with patch("tools.safety_health.filedialog.asksaveasfilename") as choose:
                    SafetyProposalWindow.generate_report(proposal)
                choose.assert_not_called()

    @unittest.skip("Interfaz Tk retirada; pendiente de migraci?n a Qt.")
    def test_cancelling_required_regulatory_review_prevents_export(self):
        """No genera ni pide ruta si se cancela la confirmacion pendiente."""
        proposal = create_autospec(SafetyProposalWindow, instance=True)
        proposal.library_editor = None
        proposal.app = self.make_app("pdf")
        proposal.owner = SimpleNamespace(open_regulatory_review=lambda: None)
        proposal._create_chapter.return_value = True
        with patch("tools.safety_health.filedialog.asksaveasfilename") as choose:
            SafetyProposalWindow.generate_report(proposal)
        choose.assert_not_called()

    @unittest.skip("Interfaz Tk retirada; pendiente de migraci?n a Qt.")
    def test_unsaved_library_revision_blocks_export_before_budget_changes(self):
        """No usa una versión distinta de la que se está editando en la biblioteca."""
        proposal = create_autospec(SafetyProposalWindow, instance=True)
        proposal.library_editor = SimpleNamespace(winfo_exists=lambda: True, dirty=True)
        with patch("tools.safety_health.messagebox.showerror") as warning:
            SafetyProposalWindow.generate_report(proposal)
        warning.assert_called_once()
        proposal._create_chapter.assert_not_called()

    def test_catalogue_types_follow_native_pagination_without_forcing_category_breaks(self):
        """Repite tipo y cabecera una vez por pagina PDF y combina los grupos ODT."""
        from odt_reports import _tag

        groups = (
            ("Riesgos", "R", 15), ("Medidas preventivas", "MP", 12),
            ("Protecciones colectivas", "PC", 4),
            ("Equipos de protección individual", "EPI", 3), ("Equipos y maquinaria", "EQ", 2),
        )
        rows = tuple(
            (
                category, f"{prefix}-{index:03d}",
                f"Marcador{prefix} contenido preventivo. " * (160 if prefix == "R" and index == 0 else 9),
            )
            for category, prefix, count in groups for index in range(count)
        )
        content = _StudyContent(
            "ESTUDIO DE SEGURIDAD Y SALUD", "Proyecto de prueba", "CAP.03#", "EUR",
            [_Block("heading", "Catálogo preventivo", 1), _Block(
                "table", headers=("Tipo", "Referencia", "Descripción"), rows=rows, blue_table=True,
            )],
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "catalogo.pdf"
            with patch(
                "tools.safety_health.document._build_study_content", return_value=content,
            ):
                write_study_report(self.make_app("pdf"), target)
            pages = [" ".join(page.extract_text().split()) for page in PdfReader(target).pages]
            table_pages = [text for text in pages if "Marcador" in text]
            self.assertGreater(len(table_pages), 3)
            for text in table_pages:
                for header in ("Tipo", "Referencia", "Descripción"):
                    self.assertEqual(text.count(header), 1)
                for category, prefix, _ in groups:
                    if f"Marcador{prefix} " in text:
                        self.assertEqual(text.count(category), 1, (prefix, text))
            self.assertTrue(any(
                sum(f"Marcador{prefix} " in text for _, prefix, _ in groups) > 1
                for text in table_pages
            ))
            for _, code, _ in rows:
                self.assertEqual(" ".join(table_pages).count(code), 1, code)
            target = Path(folder) / "catalogo.odt"
            with patch(
                "tools.safety_health.document._build_study_content", return_value=content,
            ):
                write_study_report(self.make_app("odt"), target)
            with ZipFile(target) as archive:
                root = ET.fromstring(archive.read("content.xml"))
            table = next(
                table for table in root.iter(_tag("table:table"))
                if table.get(_tag("table:name")) == "ESSPreventiveCatalogue"
            )
            table_rows = table.findall(_tag("table:table-row"))
            self.assertEqual(len(table.findall(_tag("table:table-header-rows"))), 1)
            start = 0
            for category, _, count in groups:
                cell = table_rows[start][0]
                self.assertEqual("".join(cell.itertext()), category)
                self.assertEqual(cell.get(_tag("table:number-rows-spanned")), str(count))
                for row in table_rows[start + 1:start + count]:
                    self.assertEqual(row[0].tag, _tag("table:covered-table-cell"))
                start += count
            for row, (_, code, _) in zip(table_rows, rows):
                bookmarks = row[1].findall(f".//{_tag('text:bookmark')}")
                self.assertEqual(len(bookmarks), 1)
                self.assertEqual(bookmarks[0].get(_tag("text:name")), f"ESSRef-{code}")

    def test_bullets_and_numbered_items_have_hanging_indents_in_pdf_and_odt(self):
        """Comprueba sangrias reales, niveles y enlaces en listas y celdas."""
        from odt_reports import _tag

        blocks = _markdown_blocks(
            "# Listas\nPárrafo base.\n- Viñeta R-RUIDO\n"
            "  1. Numerada R-RUIDO\n\t2) Tabulada R-RUIDO\nb) Alfabética R-RUIDO"
        )
        self.assertEqual(
            [(block.kind, block.level) for block in blocks[2:]],
            [("list_item", 0), ("list_item", 1), ("list_item", 2), ("list_item", 0)],
        )
        blocks.extend([
            _Block("table", headers=("Aspecto", "Descripción"), rows=(
                ("Recursos", "• Celda R-RUIDO\n• Otra celda R-RUIDO"),
            ), project_summary=True),
            _Block("table", headers=("Tipo", "Referencia", "Descripción"), rows=(
                ("Riesgos", "R-RUIDO", "Definición del ruido."),
            ), blue_table=True),
        ])
        content = _StudyContent(
            "ESTUDIO DE SEGURIDAD Y SALUD", "Proyecto de prueba", "CAP.03#", "EUR", blocks,
        )
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(format=output_format):
                    target = Path(folder) / f"listas.{output_format}"
                    with patch(
                        "tools.safety_health.document._build_study_content", return_value=content,
                    ):
                        write_study_report(self.make_app(output_format), target)
                    if output_format == "pdf":
                        reader = PdfReader(target)
                        positions = {}

                        def collect(text, cm, tm, font, size):
                            """Obtiene posiciones finales tras transformar el lienzo."""
                            for label in ("Párrafo base.", "Viñeta", "Numerada", "Tabulada", "Alfabética"):
                                if label in text:
                                    positions[label] = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]

                        reader.pages[-1].extract_text(visitor_text=collect)
                        self.assertAlmostEqual(positions["Viñeta"] - positions["Párrafo base."], 30)
                        self.assertAlmostEqual(positions["Numerada"] - positions["Viñeta"], 12)
                        self.assertAlmostEqual(positions["Tabulada"] - positions["Numerada"], 12)
                        self.assertAlmostEqual(positions["Alfabética"], positions["Viñeta"])
                        self.assertEqual(len(reader.pages[-1].get("/Annots", [])), 6)
                    else:
                        with ZipFile(target) as archive:
                            root = ET.fromstring(archive.read("content.xml"))
                            styles = ET.fromstring(archive.read("styles.xml"))
                        named = {
                            style.get(_tag("style:name")): style for tree in (root, styles)
                            for style in tree.iter(_tag("style:style"))
                        }
                        for name, margin in (
                            ("ESSList0", 30), ("ESSList1", 42), ("ESSList2", 54), ("ESSCellList", 24),
                        ):
                            properties = named[name].find(_tag("style:paragraph-properties"))
                            self.assertEqual(properties.get(_tag("fo:margin-left")), f"{margin}pt")
                            self.assertEqual(properties.get(_tag("fo:text-indent")), "-12pt")
                        paragraphs = [
                            paragraph for paragraph in root.iter(_tag("text:p"))
                            if paragraph.get(_tag("text:style-name"), "").startswith(("ESSList", "ESSCellList"))
                        ]
                        self.assertEqual(len(paragraphs), 6)
                        for paragraph in paragraphs:
                            self.assertIsNotNone(paragraph.find(_tag("text:tab")))
                            self.assertEqual(len(paragraph.findall(_tag("text:a"))), 1)

    def test_catalogue_references_link_to_specific_rows_in_pdf_and_odt(self):
        """Vincula referencias normales y acentuadas en parrafos y ambos tipos de tabla."""
        catalogue_rows = (
            ("Riesgos", "R-EXPLOSION", "Definición de explosión."),
            ("Riesgos", "R-RUIDO", "Definición de ruido. " + "Descripción preventiva. " * 140),
            ("Medidas preventivas", "MP-HIGIENE-QUIMICA", "Ficha de datos de seguridad."),
            ("Protecciones colectivas", "PC-VALLADO", "Cerramiento continuo."),
            ("Equipos de proteccion individual", "EPI-AUDITIVO", "Protección auditiva."),
            ("Equipos y maquinaria", "EQ-FRESADORA", "Fresadora de pavimento."),
        )
        content = _StudyContent(
            "ESTUDIO DE SEGURIDAD Y SALUD", "Proyecto de prueba", "CAP.03#", "EUR",
            [
                _Block("heading", "Memoria", 1),
                _Block("paragraph", "Ver R-EXPLOSIÓN, R-RUIDO y R-INEXISTENTE. <control> & revisión."),
                _Block("paragraph", "MP-HIGIENE-QUIMICA\nPC-VALLADO; EPI-AUDITIVO; EQ-FRESADORA."),
                _Block("table", headers=("Contenido", "Referencias"), rows=(
                    ("Lista", "R-EXPLOSION y R-RUIDO"),
                    ("Multilínea", "MP-HIGIENE-QUIMICA\nEPI-AUDITIVO"),
                )),
                _Block(
                    "table", headers=("Aspecto", "Descripción"),
                    rows=(
                        ("Riesgos", "R-EXPLOSIÓN, R-RUIDO"),
                        ("Medidas preventivas", "MP-HIGIENE-QUIMICA"),
                    ),
                    project_summary=True,
                ),
                _Block("heading", "Catálogo preventivo", 2),
                _Block("caption", "Tabla 3. Catálogo preventivo."),
                _Block(
                    "table", headers=("Tipo", "Referencia", "Descripción"),
                    rows=catalogue_rows, blue_table=True,
                ),
            ],
        )
        ns = {
            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
            "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
            "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
            "xlink": "http://www.w3.org/1999/xlink",
        }
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(format=output_format):
                    target = Path(folder) / f"enlaces.{output_format}"
                    with patch(
                        "tools.safety_health.document._build_study_content",
                        return_value=content,
                    ):
                        write_study_report(self.make_app(output_format), target)
                    if output_format == "pdf":
                        reader = PdfReader(target)
                        text = " ".join(page.extract_text() for page in reader.pages)
                        self.assertIn("<control> & revisión", text)
                        self.assertIn("R-INEXISTENTE", text)
                        destinations = [
                            annotation.get_object()["/Dest"]
                            for page in reader.pages
                            for annotation in page.get("/Annots", [])
                            if "/Dest" in annotation.get_object()
                            and annotation.get_object()["/Dest"][1] == "/XYZ"
                        ]
                        self.assertGreaterEqual(len(destinations), 13)
                        unique_destinations = {
                            (destination[0].idnum, float(destination[3]))
                            for destination in destinations
                        }
                        self.assertEqual(len(unique_destinations), len(catalogue_rows))
                        positions = {}
                        for page in reader.pages:
                            page_id = page.indirect_reference.idnum

                            def collect_reference(text, cm, tm, font, size):
                                """Recoge coordenadas del codigo impreso para comprobar el destino."""
                                if text.strip() in {row[1] for row in catalogue_rows}:
                                    positions.setdefault(page_id, []).append(
                                        tm[4] * cm[1] + tm[5] * cm[3] + cm[5],
                                    )

                            page.extract_text(visitor_text=collect_reference)
                        for page_id, top in unique_destinations:
                            self.assertTrue(any(
                                abs(top - baseline) < 15 for baseline in positions[page_id]
                            ))
                    else:
                        with ZipFile(target) as archive:
                            root = ET.fromstring(archive.read("content.xml"))
                        table = root.find(
                            ".//table:table[@table:name='ESSPreventiveCatalogue']", ns,
                        )
                        self.assertIsNotNone(table)
                        rows = table.findall("table:table-row", ns)
                        targets = {}
                        for index, row in enumerate(rows):
                            cells = list(row)
                            bookmarks = cells[1].findall(".//text:bookmark", ns)
                            self.assertEqual(len(bookmarks), 1)
                            name = bookmarks[0].get(f"{{{ns['text']}}}name")
                            targets[name] = "".join(cells[1].itertext())
                            expected = "ESSSummaryBlueCell" if index % 2 == 0 else "ESSSummaryWhiteCell"
                            self.assertTrue(all(
                                cell.get(f"{{{ns['table']}}}style-name") == expected
                                for cell in cells if cell.tag.endswith("}table-cell")
                            ))
                        self.assertEqual(len(targets), len(catalogue_rows))
                        links = [
                            link for link in root.findall(".//text:a", ns)
                            if link.get(f"{{{ns['xlink']}}}href", "").startswith("#ESSRef-")
                        ]
                        self.assertEqual(len(links), 13)
                        for link in links:
                            name = link.get(f"{{{ns['xlink']}}}href")[1:]
                            self.assertIn(name, targets)
                        accented = next(link for link in links if link.text == "R-EXPLOSIÓN")
                        self.assertEqual(
                            targets[accented.get(f"{{{ns['xlink']}}}href")[1:]], "R-EXPLOSION",
                        )
                        summary = root.find(
                            ".//table:table[@table:name='ESSProjectSummary']", ns,
                        )
                        self.assertEqual(len(summary.findall(".//text:a", ns)), 3)
                        self.assertIn("<control> & revisión", "".join(root.itertext()))

    def test_risk_overview_preserves_all_cells_and_blue_styles_in_pdf_and_odt(self):
        """Exporta tablas de riesgos y EPI completas con sus colores en ambos formatos."""
        ns = {
            "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
            "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
            "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
        }
        with tempfile.TemporaryDirectory() as folder:
            for output_format, first_header, table_name, caption_prefix in (
                (output_format, first_header, table_name, caption_prefix)
                for output_format in ("pdf", "odt")
                for first_header, table_name, caption_prefix in (
                    ("GRUPO DE RIESGO", "ESSGeneralRisks", "Tabla 2."),
                    ("EPI", "ESSPPE", "Tabla 4."),
                )
            ):
                with self.subTest(format=output_format, table=table_name):
                    app = self.make_app(output_format)
                    content = _build_study_content(app)
                    overview = next(
                        block for block in content.blocks
                        if block.kind == "table" and block.headers[0] == first_header
                    )
                    caption = next(
                        block for block in content.blocks
                        if block.kind == "caption" and block.text.startswith(caption_prefix)
                    )
                    self.assertLess(
                        content.blocks.index(caption), content.blocks.index(overview),
                    )
                    if output_format == "pdf":
                        content.blocks = [caption, overview]
                    target = Path(folder) / f"riesgos.{output_format}"
                    with patch(
                        "tools.safety_health.document._build_study_content",
                        return_value=content,
                    ):
                        write_study_report(app, target)
                    if output_format == "pdf":
                        reader = PdfReader(target)
                        text = "\n".join(page.extract_text() for page in reader.pages)
                        colors = {
                            tuple(round(float(value), 5) for value in operands)
                            for page in reader.pages
                            for operands, operator in ContentStream(
                                page.get_contents(), reader,
                            ).operations
                            if operator == b"rg"
                        }
                        for color in ((68, 114, 196), (217, 229, 245), (255, 255, 255)):
                            self.assertIn(
                                tuple(round(value / 255, 5) for value in color), colors,
                            )
                    else:
                        with ZipFile(target) as archive:
                            root = ET.fromstring(archive.read("content.xml"))
                        table = root.find(
                            f".//table:table[@table:name='{table_name}']", ns,
                        )
                        self.assertIsNotNone(table)
                        self.assertEqual(len(table.findall("table:table-column", ns)), 3)
                        rows = table.findall("table:table-row", ns)
                        self.assertEqual(len(rows), 9)
                        style_attribute = f"{{{ns['table']}}}style-name"
                        styles = {
                            style.get(f"{{{ns['style']}}}name"): style
                            for style in root.findall(".//style:style", ns)
                        }
                        all_names = [
                            style.get(f"{{{ns['style']}}}name")
                            for style in root.findall(".//style:style", ns)
                        ]
                        self.assertEqual(len(all_names), len(set(all_names)))
                        header_cells = table.findall(
                            "table:table-header-rows/table:table-row/table:table-cell", ns,
                        )
                        self.assertEqual(len(header_cells), 3)
                        for cell in header_cells:
                            self.assertEqual(
                                cell.get(style_attribute), "ESSSummaryHeaderCell",
                            )
                        for index, row in enumerate(rows):
                            cells = row.findall("table:table-cell", ns)
                            self.assertEqual(len(cells), 3)
                            for cell in cells:
                                properties = styles[cell.get(style_attribute)].find(
                                    "style:table-cell-properties", ns,
                                )
                                self.assertEqual(
                                    properties.get(f"{{{ns['fo']}}}background-color"),
                                    "#D9E5F5" if index % 2 == 0 else "#FFFFFF",
                                )
                        text = "".join(table.itertext())
                    compact_text = re.sub(r"\s+", "", text)
                    for cell in (*overview.headers, *(cell for row in overview.rows for cell in row)):
                        self.assertIn(re.sub(r"\s+", "", cell), compact_text)

    def test_summary_has_cpv_chapter_bullets_planned_days_conditions_and_risks(self):
        """Respeta los datos revisados sin mezclar unidades de obra y riesgos."""
        app = self.make_app("odt")
        app.report_settings.cpv_codes = ["45000000-7"]
        app.budget_items[0].rendimiento = 7.9
        app.planning_state = {"activities": [
            {"chapter": "01#", "code": "15APAA00001", "manual_duration": 3},
            {"chapter": "02#", "code": "OBRA001", "manual_duration": 4, "pred": 1},
        ]}
        app.safety_health_state["external_conditions"] = {
            "trafico_existente": {"description": "Corte de un carril", "applicability": "yes"},
            "peatones_terceros": {"description": "Sin tránsito peatonal", "applicability": "no"},
            "centro_asistencial": {
                "description": "Hospital de referencia. Teléfono 123456789",
                "applicability": "yes",
            },
        }

        content = _build_study_content(app)

        summary = next(block for block in content.blocks if block.project_summary)
        rows = dict(summary.rows)
        self.assertIn("Denominación del proyecto", rows)
        self.assertIn("Técnico/s redactor/es", rows)
        self.assertEqual(rows["Tipologia de obra"], (
            "45000000-7 — TRABAJOS DE CONSTRUCCIÓN\n\n• SANEAMIENTO\n• PAVIMENTOS"
        ))
        self.assertEqual(rows["Duración prevista del proyecto"], "7 DÍAS LABORABLES")
        self.assertRegex(rows["Maximo simultaneo previsto"], r"^[1-9]\d* PERSONAS?$")
        self.assertIn("APLICABILIDAD: SÍ", rows["Tráfico"])
        self.assertIn("APLICABILIDAD: NO", rows["Peatones y terceros"])
        self.assertIn("APLICABILIDAD: PENDIENTE", rows["Servicios afectados"])
        self.assertRegex(
            rows["Riesgos detectados"], r"^R-[A-Z0-9-]+(?:, R-[A-Z0-9-]+)*$",
        )
        self.assertRegex(
            rows["Medidas preventivas"], r"^MP-[A-Z0-9-]+(?:, MP-[A-Z0-9-]+)*$",
        )
        keys = list(rows)
        self.assertLess(keys.index("Condicionantes externos"), keys.index("Tráfico"))
        self.assertLess(keys.index("Coordinación de seguridad y salud"), keys.index("Riesgos detectados"))
        self.assertFalse(any(
            "Resumen de unidades" in key or "Partidas importadas" in key
            or "Resultado generado" in key for key in keys
        ))
        self.assertEqual(content.blocks[content.blocks.index(summary) - 1].kind, "caption")
        paragraphs = "\n".join(block.text for block in content.blocks)
        self.assertNotIn("Confirmar el plazo contractual", paragraphs)
        self.assertNotIn("Falta definir el centro asistencial", paragraphs)

    def test_auxiliary_means_come_from_budget_and_nested_resources_in_both_formats(self):
        """Extrae medios reales una sola vez, no mano de obra, porcentajes ni morteros auxiliares."""
        dataset = SimpleNamespace(concepts={
            "AUX": Concept("AUX", "m3", "Mortero auxiliar", ctype="0", children=[
                ("GRUA", 1), ("ANDAMIO", 2),
            ]),
            "GRUA": Concept("GRUA", "h", "Camión grúa", ctype="2"),
            "ANDAMIO": Concept("ANDAMIO", "ud", "Andamio de referencia", ctype="3"),
            "MAT": Concept("MAT", "kg", "Cemento", ctype="3"),
            "MO": Concept("MO", "h", "Montador de andamios", ctype="1"),
            "%A": Concept("%A", "%", "Medios auxiliares", ctype="%"),
            "SIN_USO": Concept("SIN_USO", "h", "Equipo sin uso", ctype="2"),
        })
        with tempfile.TemporaryDirectory() as folder:
            for output_format in ("pdf", "odt"):
                with self.subTest(format=output_format):
                    app = self.make_app(output_format)
                    app._component_dataset = lambda item: dataset
                    app.safety_health_state.update(
                        centros_asistenciales_modo="tabla",
                        centros_asistenciales_datos=[{
                            "nombre": "Hospital de referencia", "via": "Calle",
                            "domicilio": "Salud", "numero": "2", "telefono": "957123456",
                        }],
                    )
                    app.budget_items[0].decomposition = [
                        ("AUX", 1), ("MAT", 1), ("MO", 1), ("%A", 2), ("SIN_USO", 0),
                    ]
                    app.budget_items[0].component_concepts["ANDAMIO"] = Concept(
                        "ANDAMIO", "ud", "Andamio tubular revisado", ctype="3",
                    )
                    app.budget_items[1].decomposition = [("GRUA", 2)]
                    app.budget_items.extend([
                        ProjectItem(
                            "PLATAFORMA", "02#", "ud", "Plataforma elevadora",
                            concept_type="2",
                        ),
                        ProjectItem("ESCALERA", "02#", "ud", "Escalera de hormigón"),
                    ])

                    content = _build_study_content(app)

                    rows = dict(next(
                        block for block in content.blocks if block.project_summary
                    ).rows)
                    self.assertEqual(list(rows)[-5:], [
                        "Medios auxiliares", "Maquinaria empleada", "Riesgos detectados",
                        "Medidas preventivas",
                        "Centros asistenciales, dirección y teléfonos de interés",
                    ])
                    self.assertEqual(rows["Medios auxiliares"], (
                        "• ANDAMIO — ANDAMIO TUBULAR REVISADO\n"
                        "• PLATAFORMA — PLATAFORMA ELEVADORA"
                    ))
                    self.assertEqual(rows["Maquinaria empleada"], "• GRUA — CAMIÓN GRÚA")
                    self.assertEqual(list(rows).count("Servicios afectados"), 1)
                    target = Path(folder) / f"medios.{output_format}"
                    write_study_report(app, target)
                    if output_format == "pdf":
                        text = "\n".join(
                            page.extract_text() or "" for page in PdfReader(target).pages
                        )
                    else:
                        with ZipFile(target) as archive:
                            text = "".join(ET.fromstring(archive.read("content.xml")).itertext())
                    self.assertIn("Medios auxiliares", text)
                    self.assertIn("ANDAMIO TUBULAR REVISADO", text)
                    self.assertIn("CAMIÓN GRÚA", text)
                    self.assertNotIn("{{medios_auxiliares}}", text)
                    self.assertIn("Maquinaria empleada", text)
                    self.assertIn("HOSPITAL DE REFERENCIA", text)
                    self.assertIn("CALLE SALUD, 2", text)
                    self.assertIn("957123456", text)
                    self.assertIn("EMERGENCIAS: 112", text)

    def test_summary_contacts_use_full_sicess_address_or_the_selected_free_text(self):
        """Incluye direccion postal, telefonos y notas sin recuperar el modo inactivo."""
        app = self.make_app("odt")
        app.safety_health_state.update(
            centros_asistenciales_modo="tabla",
            centros_asistenciales_texto="Centro antiguo que no debe mostrarse",
            centros_asistenciales_datos=[{
                "nombre": "Hospital de referencia", "via": "Avenida",
                "domicilio": "de la Salud", "numero": "2", "cp": "14001",
                "municipio": "Córdoba", "provincia": "Córdoba", "telefono": "957123456",
            }],
            external_conditions={
                "centro_asistencial": {
                    "description": "Contacto de obra: 600123456", "applicability": "yes",
                },
                "servicios_afectados": {
                    "description": "Red de agua municipal", "applicability": "yes",
                },
            },
        )
        content = _build_study_content(app)
        rows = dict(next(block for block in content.blocks if block.project_summary).rows)
        label = "Centros asistenciales, dirección y teléfonos de interés"
        self.assertEqual(list(rows)[-1], label)
        self.assertIn("HOSPITAL DE REFERENCIA", rows[label])
        self.assertIn("AVENIDA DE LA SALUD, 2, 14001, CÓRDOBA, CÓRDOBA", rows[label])
        self.assertIn("TELÉFONO: 957123456", rows[label])
        self.assertIn("CONTACTO DE OBRA: 600123456", rows[label])
        self.assertIn("EMERGENCIAS: 112", rows[label])
        self.assertNotIn("CENTRO ANTIGUO", rows[label])
        self.assertIn("RED DE AGUA MUNICIPAL", rows["Servicios afectados"])
        self.assertNotIn("Centro asistencial", rows)
        app.safety_health_state.update(
            centros_asistenciales_modo="texto",
            centros_asistenciales_texto="Centro manual, calle Mayor, 5. Teléfono: 900123456",
        )
        content = _build_study_content(app)
        rows = dict(next(block for block in content.blocks if block.project_summary).rows)
        self.assertIn("CENTRO MANUAL, CALLE MAYOR, 5. TELÉFONO: 900123456", rows[label])
        self.assertNotIn("HOSPITAL DE REFERENCIA", rows[label])

    def test_auxiliary_means_warn_about_missing_data_and_reject_circular_resources(self):
        """No confunde datos ausentes con una lista completa ni recorre ciclos indefinidamente."""
        app = self.make_app("odt")
        app.budget_items[0].decomposition = [("FALTA", 1)]
        content = _build_study_content(app)
        self.assertIn(
            "Falta la descripción del recurso FALTA",
            "\n".join(content.warnings),
        )
        self.assertIn(
            "NO SE HAN IDENTIFICADO MEDIOS AUXILIARES",
            dict(next(block for block in content.blocks if block.project_summary).rows)[
                "Medios auxiliares"
            ],
        )
        app.budget_items[0].component_concepts["FALTA"] = Concept(
            "FALTA", ctype="0", children=[("FALTA", 1)],
        )
        with self.assertRaisesRegex(ValueError, "circular"):
            _build_study_content(app)

    def test_summary_preserves_alignment_when_splitting_inside_a_long_row(self):
        """Reproduce la perdida de alineacion del Table interno de ReportLab."""
        layout = ReportLayout()
        block = _Block(
            "table", headers=("Aspecto del proyecto", "Descripción / información"),
            rows=(("Condicionante", "Descripción extensa. " * 300),),
            project_summary=True,
        )
        table = _pdf_blue_table(block, layout)
        table.wrap(layout.content_width, 700)
        fragments = table.split(layout.content_width, 200)
        self.assertEqual(len(fragments), 2)
        for fragment in fragments:
            self.assertEqual(fragment.hAlign, "LEFT")
            self.assertAlmostEqual(sum(fragment._colWidths), layout.content_width)

    def test_summary_pdf_keeps_the_same_left_edge_across_pages_and_split_rows(self):
        """Mide los fondos de tabla del PDF, incluidas las continuaciones dentro de una fila."""
        app = self.make_app("pdf")
        app.safety_health_state["external_conditions"] = {
            "trafico_existente": {
                "description": "Corte de un carril por ejecución de las obras. " * 400,
                "applicability": "yes",
            },
        }
        content = _build_study_content(app)
        memory_index = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Memoria descriptiva"
        )
        content.blocks = content.blocks[:memory_index]
        width = ReportLayout().content_width
        backgrounds = {
            tuple(round(component / 255, 5) for component in rgb)
            for rgb in ((68, 114, 196), (217, 229, 245))
        }
        edges = []
        pages_with_table = set()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "resumen_largo.pdf"
            with patch(
                "tools.safety_health.document._build_study_content", return_value=content,
            ):
                write_study_report(app, target)
            reader = PdfReader(target)
            for page_number, page in enumerate(reader.pages):
                matrix = (1, 0, 0, 1, 0, 0)
                color = None
                stack = []
                for operands, operator in ContentStream(page.get_contents(), reader).operations:
                    if operator == b"q":
                        stack.append((matrix, color))
                    elif operator == b"Q":
                        matrix, color = stack.pop()
                    elif operator == b"cm":
                        a, b, c, d, e, f = map(float, operands)
                        aa, bb, cc, dd, ee, ff = matrix
                        matrix = (
                            a * aa + b * cc, a * bb + b * dd,
                            c * aa + d * cc, c * bb + d * dd,
                            e * aa + f * cc + ee, e * bb + f * dd + ff,
                        )
                    elif operator == b"rg":
                        color = tuple(round(float(value), 5) for value in operands)
                    elif operator == b"re" and color in backgrounds:
                        x, y, rectangle_width, _height = map(float, operands)
                        if abs(rectangle_width - width) < 0.01:
                            edges.append(matrix[0] * x + matrix[2] * y + matrix[4])
                            self.assertAlmostEqual(matrix[0] * rectangle_width, width, places=2)
                            pages_with_table.add(page_number)
        self.assertGreaterEqual(len(pages_with_table), 3)
        self.assertLess(max(edges) - min(edges), 0.01)

    def test_pdf_contents_links_to_real_pages_when_index_spans_pages(self):
        """Numera y enlaza cada entrada con su pagina definitiva, sin titulos repetidos."""
        labels = [
            f"Sección {index:03d} & control <marca> con título largo para "
            "comprobar la paginación del índice"
            for index in range(60)
        ]
        blocks = []
        for index, label in enumerate(labels):
            blocks.extend((
                _Block("heading", label, 1),
                _Block(
                    "paragraph",
                    f"Contenido técnico {index:03d}. " + "Texto del estudio. " * 45,
                ),
            ))
        content = _StudyContent(
            "ESTUDIO DE SEGURIDAD Y SALUD", "Proyecto de prueba", "CAP.03#", "EUR",
            blocks,
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "indice.pdf"
            with patch(
                "tools.safety_health.document._build_study_content", return_value=content,
            ):
                write_study_report(self.make_app("pdf"), target)
            reader = PdfReader(target)
            texts = [" ".join(page.extract_text().split()) for page in reader.pages]
            body_start = next(
                index for index, text in enumerate(texts) if "Contenido técnico" in text
            )
            self.assertGreater(body_start, 1)
            self.assertIn("ÍNDICE", texts[0])
            contents = " ".join(texts[:body_start])
            destinations = [
                annotation.get_object()["/Dest"][0]
                for page in reader.pages[:body_start]
                for annotation in page.get("/Annots", [])
                if "/Dest" in annotation.get_object()
            ]
            self.assertTrue(destinations)
            for index, label in enumerate(labels):
                heading = f"{index + 1} {label.upper()}"
                page_number = next(
                    page + 1 for page in range(body_start, len(texts))
                    if heading in texts[page]
                )
                self.assertRegex(contents, rf"\b{page_number}\s+" + re.escape(heading))
                self.assertIn(
                    reader.pages[page_number - 1].indirect_reference, destinations,
                )
            self.assertTrue(all(
                text.count("ESTUDIO DE SEGURIDAD Y SALUD") == 1 for text in texts
            ))
            self.assertTrue(all("Proyecto de prueba" not in text for text in texts))
            for number, page in enumerate(reader.pages, 1):
                footer_parts = []

                def collect_footer(text, cm, tm, font, size):
                    """Recoge los textos situados en el margen inferior de la pagina."""
                    if tm[5] < 80:
                        footer_parts.append(text.strip())

                page.extract_text(visitor_text=collect_footer)
                self.assertIn(str(number), footer_parts)
                self.assertIn(content.title, " ".join(footer_parts))
            self.assertEqual(reader.metadata.title, content.title)

    def test_footer_uses_selected_study_type_with_and_without_template_fields(self):
        """Respeta la seleccion del usuario en ambos formatos y conserva la pagina."""
        text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        style_ns = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
        titles = (
            "ESTUDIO BÁSICO DE SEGURIDAD Y SALUD", "ESTUDIO DE SEGURIDAD Y SALUD",
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template = root / "plantilla.odt"
            base = write_report_template(root / "base.odt")
            with ZipFile(base) as source, ZipFile(template, "w") as target:
                for member in source.infolist():
                    data = source.read(member.filename)
                    if member.filename == "styles.xml":
                        styles = ET.fromstring(data)
                        for footer in styles.findall(f".//{{{style_ns}}}footer"):
                            footer.clear()
                            ET.SubElement(footer, f"{{{text_ns}}}p").text = (
                                "{{capitulo}} / {{no_página}}"
                            )
                        data = ET.tostring(styles, encoding="utf-8", xml_declaration=True)
                    target.writestr(member, data)
            for output_format in ("pdf", "odt"):
                for full_study, expected_title in enumerate(titles):
                    for with_fields in (False, True):
                        with self.subTest(
                            format=output_format, full=full_study, fields=with_fields,
                        ):
                            app = self.make_app(output_format)
                            app.safety_health_state["study_criteria"]["criterion_4_met"] = bool(
                                full_study
                            )
                            if with_fields:
                                app.report_settings.template_path = str(template)
                            target = root / f"pie.{output_format}"

                            write_study_report(app, target)

                            if output_format == "pdf":
                                for page in PdfReader(target).pages:
                                    text = " ".join(page.extract_text().split())
                                    footer_parts = []

                                    def collect_footer(text, cm, tm, font, size):
                                        """Distingue el pie del tipo de estudio en la introduccion."""
                                        if tm[4] * cm[1] + tm[5] * cm[3] + cm[5] < 80:
                                            footer_parts.append(text)

                                    page.extract_text(visitor_text=collect_footer)
                                    self.assertEqual(
                                        " ".join(footer_parts).count(expected_title), 1,
                                    )
                                    self.assertNotIn(titles[1 - full_study], text)
                                    self.assertNotIn("{{", text)
                            else:
                                with ZipFile(target) as archive:
                                    styles = ET.fromstring(archive.read("styles.xml"))
                                    content = ET.fromstring(archive.read("content.xml"))
                                self.assertIn(expected_title, "".join(content.itertext()))
                                for footer in styles.findall(f".//{{{style_ns}}}footer"):
                                    text = "".join(footer.itertext())
                                    self.assertEqual(text.count(expected_title), 1)
                                    self.assertNotIn(titles[1 - full_study], text)
                                    self.assertNotIn("{{", text)
                                    self.assertEqual(
                                        len(footer.findall(f".//{{{text_ns}}}page-number")), 1,
                                    )

    def test_odt_contents_matches_numbered_headings_and_preserves_template(self):
        """Usa indice nativo con enlaces y campos de pagina y estilos como el PPTP."""
        text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        style_ns = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
        fo_ns = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
        xlink_ns = "http://www.w3.org/1999/xlink"
        content = _StudyContent(
            "ESTUDIO DE SEGURIDAD Y SALUD", "Proyecto de prueba", "CAP.03#", "EUR",
            [
                _Block("heading", "1 Memoria", 1),
                _Block("heading", "1.1 Definición", 2),
                _Block("heading", "Preparación", 3),
                _Block("paragraph", "Texto del estudio."),
                _Block("heading", "Pliego", 1),
            ],
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "indice.odt"
            with patch(
                "tools.safety_health.document._build_study_content", return_value=content,
            ):
                write_study_report(self.make_app("odt"), target)
            with ZipFile(target) as archive:
                root = ET.fromstring(archive.read("content.xml"))
                styles = ET.fromstring(archive.read("styles.xml"))
            toc = root.find(f".//{{{text_ns}}}table-of-content")
            entries = toc.findall(f".//{{{text_ns}}}index-body//{{{text_ns}}}a")
            headings = root.findall(f".//{{{text_ns}}}h")
            self.assertEqual(
                [heading.text for heading in headings],
                ["1 MEMORIA", "1.1 DEFINICIÓN", "1.1.1 PREPARACIÓN", "2 PLIEGO"],
            )
            self.assertEqual(
                [entry.text for entry in entries], ["1 MEMORIA", "1.1 DEFINICIÓN", "2 PLIEGO"],
            )
            for entry in entries:
                reference = entry.find(f"{{{text_ns}}}bookmark-ref")
                name = reference.get(f"{{{text_ns}}}ref-name")
                self.assertEqual(entry.get(f"{{{xlink_ns}}}href"), f"#{name}")
                self.assertEqual(reference.get(f"{{{text_ns}}}reference-format"), "page")
                heading = next(
                    heading for heading in headings
                    if heading.find(f"{{{text_ns}}}bookmark").get(f"{{{text_ns}}}name") == name
                )
                self.assertEqual(entry.text, heading.text)
            self.assertEqual(
                len(toc.findall(f".//{{{text_ns}}}index-entry-page-number")), 2,
            )
            footers = []
            for master in styles.findall(f".//{{{style_ns}}}master-page"):
                header = master.find(f"{{{style_ns}}}header")
                self.assertNotIn(
                    "ESTUDIO DE SEGURIDAD Y SALUD", "".join(header.itertext()),
                )
                self.assertNotIn("Proyecto de prueba", "".join(header.itertext()))
                footer = master.find(f"{{{style_ns}}}footer")
                if footer is not None:
                    footers.append(footer)
            self.assertTrue(any(footer.find(f".//{{{text_ns}}}page-number") is not None
                                and content.title in "".join(footer.itertext()) for footer in footers))
            named = {
                style.get(f"{{{style_ns}}}name"): style
                for style in styles.findall(f".//{{{style_ns}}}style")
            }
            properties = named["ESSBody"].find(f"{{{style_ns}}}paragraph-properties")
            self.assertEqual(properties.get(f"{{{fo_ns}}}text-align"), "justify")
            forced = [
                props for tree in (root, styles)
                for props in tree.findall(f".//{{{style_ns}}}paragraph-properties")
                if props.get(f"{{{fo_ns}}}break-before") == "page"
                or props.get(f"{{{fo_ns}}}break-after") == "page"
            ]
            self.assertEqual(len(forced), 1)

    def test_document_indices_stay_separate_with_real_pages_and_cross_document_links(self):
        """Comprueba ambos indices, incluidos su paginado y actualizacion nativa ODT."""
        from odt_reports import _tag

        labels = [
            f"Memoria preventiva {index:02d}: comprobaciones y condiciones del procedimiento para el tajo"
            for index in range(36)
        ]
        blocks = [
            _Block("document", "DOCUMENTO I. MEMORIA"),
            _Block("heading", "Memoria", 1),
            _Block("paragraph", "CUERPO MEMORIA."),
        ]
        for label in labels:
            blocks.extend((
                _Block("heading", label, 2),
                _Block("paragraph", "Comprobación de las condiciones del tajo. " * 8),
            ))
        blocks.extend((
            _Block("heading", "Detalle fuera del índice", 3),
            _Block(
                "table", headers=("Tipo", "Referencia", "Descripción"),
                rows=(("Riesgos", "R-RUIDO", "Definición acústica de referencia."),),
                blue_table=True,
            ),
            _Block("document", "DOCUMENTO III. PLIEGO DE CONDICIONES"),
            _Block("heading", "Obligaciones", 1),
            _Block("paragraph", "CUERPO PLIEGO. Consulta preventiva: R-RUIDO."),
            _Block("heading", "Contratista", 2),
            _Block("paragraph", "Obligaciones de la empresa."),
            _Block("heading", "Plan y control", 1),
            _Block("paragraph", "CIERRE PLIEGO."),
        ))
        expected = [
            ["1 MEMORIA", *[f"1.{index + 1} {label.upper()}" for index, label in enumerate(labels)]],
            ["1 OBLIGACIONES", "1.1 CONTRATISTA", "2 PLAN Y CONTROL"],
        ]
        soffice = shutil.which("soffice") or str(
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "LibreOffice" / "program" / "soffice.exe"
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for output_format in ("pdf", "odt"):
                with self.subTest(format=output_format):
                    target = root / f"indices-{output_format}.{output_format}"
                    content = _StudyContent(
                        "ESTUDIO DE SEGURIDAD Y SALUD", "Proyecto de prueba", "CAP.03#", "EUR",
                        blocks,
                    )
                    with patch(
                        "tools.safety_health.document._build_study_content", return_value=content,
                    ):
                        write_study_report(self.make_app(output_format), target)
                    if output_format == "odt":
                        with ZipFile(target) as archive:
                            tree = ET.fromstring(archive.read("content.xml"))
                            styles = ET.fromstring(archive.read("styles.xml"))
                        contents = tree.findall(f".//{_tag('text:table-of-content')}")
                        self.assertEqual(len(contents), 2)
                        names = [toc.get(_tag("text:name")) for toc in contents]
                        self.assertEqual(len(set(names)), 2)
                        bookmarks = {
                            bookmark.get(_tag("text:name")): heading.text
                            for heading in tree.findall(f".//{_tag('text:h')}")
                            for bookmark in heading.findall(_tag("text:bookmark"))
                        }
                        for index, toc in enumerate(contents):
                            source = toc.find(_tag("text:table-of-content-source"))
                            self.assertEqual(source.get(_tag("text:use-outline-level")), "false")
                            self.assertEqual(source.get(_tag("text:use-index-source-styles")), "true")
                            self.assertEqual([
                                style.get(_tag("text:style-name"))
                                for style in source.findall(f".//{_tag('text:index-source-style')}")
                            ], [f"ESSDocument{index}Heading{level}" for level in (1, 2)])
                            entries = toc.findall(f".//{_tag('text:a')}")
                            self.assertEqual([entry.text for entry in entries], expected[index])
                            for entry in entries:
                                reference = entry.find(_tag("text:bookmark-ref"))
                                self.assertEqual(
                                    bookmarks[reference.get(_tag("text:ref-name"))], entry.text,
                                )
                        style_names = [
                            style.get(_tag("style:name")) for style in styles.findall(
                                f".//{_tag('style:style')}",
                            )
                        ]
                        for index in range(2):
                            self.assertIn(f"ESSDocument{index}Heading1", style_names)
                        if not Path(soffice).is_file():
                            continue
                        subprocess.run(
                            [soffice, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                             "--headless", "--convert-to", "pdf", "--outdir", str(root), str(target)],
                            check=True, capture_output=True, timeout=90,
                        )
                        target = target.with_suffix(".pdf")
                    reader = PdfReader(target)
                    texts = [" ".join(page.extract_text().split()) for page in reader.pages]
                    compact_texts = [re.sub(r"\s+", "", text) for text in texts]
                    first_body = next(index for index, text in enumerate(texts) if "CUERPO MEMORIA." in text)
                    second_index = next(
                        index for index, text in enumerate(texts)
                        if "DOCUMENTO III. PLIEGO DE CONDICIONES" in text
                    )
                    second_body = next(index for index, text in enumerate(texts) if "CUERPO PLIEGO." in text)
                    self.assertGreater(first_body, 1)
                    regions = (
                        (0, first_body, first_body, second_index),
                        (second_index, second_body, second_body, len(texts)),
                    )
                    for index, (toc_start, toc_end, body_start, body_end) in enumerate(regions):
                        contents_text = " ".join(texts[toc_start:toc_end])
                        destinations = [
                            destination[0]
                            for page in reader.pages[toc_start:toc_end]
                            for annotation in page.get("/Annots", [])
                            if (destination := (
                                annotation.get_object().get("/Dest")
                                or annotation.get_object().get("/A", {}).get("/D")
                            ))
                        ]
                        for heading in expected[index]:
                            compact_heading = re.sub(r"\s+", "", heading)
                            page_number = next((
                                page + 1 for page in range(body_start, body_end)
                                if compact_heading in compact_texts[page]
                            ), 0)
                            self.assertGreater(page_number, 0, heading)
                            heading_pattern = r"\s*".join(map(re.escape, compact_heading))
                            pattern = (
                                rf"\b{page_number}\s+{heading_pattern}"
                                if output_format == "pdf"
                                else rf"{heading_pattern}\s+{page_number}\b"
                            )
                            self.assertRegex(contents_text, pattern)
                            self.assertIn(reader.pages[page_number - 1].indirect_reference, destinations)
                        for heading in expected[1 - index]:
                            self.assertNotIn(
                                re.sub(r"\s+", "", heading), re.sub(r"\s+", "", contents_text),
                            )
                        self.assertNotIn("DETALLEFUERADELÍNDICE", re.sub(r"\s+", "", contents_text))
                    annotations = reader.pages[second_body].get("/Annots", [])
                    self.assertEqual(len(annotations), 1)
                    annotation = annotations[0].get_object()
                    destination = annotation.get("/Dest") or annotation["/A"]["/D"]
                    reference_page = next(
                        page for page in reader.pages if page.indirect_reference == destination[0]
                    )
                    self.assertIn("Definición acústica de referencia.", reference_page.extract_text())

    def test_writer_resolves_odt_contents_page_numbers(self):
        """Comprueba las paginas del indice despues de paginar el ODT con Writer."""
        soffice = shutil.which("soffice") or str(
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "LibreOffice" / "program" / "soffice.exe"
        )
        if not Path(soffice).is_file():
            self.skipTest("LibreOffice no disponible para validar el indice ODT paginado.")
        content = _StudyContent(
            "ESTUDIO DE SEGURIDAD Y SALUD", "Proyecto de prueba", "CAP.03#", "EUR",
            [
                _Block("heading", "Memoria", 1),
                _Block("paragraph", "Consulta preventiva: R-EXPLOSIÓN y R-RUIDO."),
                *[
                    _Block("paragraph", "Contenido técnico. " * 45)
                    for _ in range(12)
                ],
                _Block("heading", "Pliego", 1),
                _Block("paragraph", "Fin del estudio."),
                _Block("heading", "Catálogo preventivo", 2),
                _Block(
                    "table", headers=("Tipo", "Referencia", "Descripción"),
                    rows=(
                        ("Riesgos", "R-EXPLOSION", "Definicion de explosion."),
                        ("Riesgos", "R-RUIDO", "Definicion de ruido."),
                    ),
                    blue_table=True,
                ),
            ],
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "indice.odt"
            with patch(
                "tools.safety_health.document._build_study_content", return_value=content,
            ):
                write_study_report(self.make_app("odt"), target)
            subprocess.run(
                [soffice, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(root), str(target)],
                check=True, capture_output=True, timeout=90,
            )
            reader = PdfReader(root / "indice.pdf")
            texts = [
                " ".join(page.extract_text().split())
                for page in reader.pages
            ]
            last_page = next(
                index + 1 for index, text in enumerate(texts) if "Fin del estudio." in text
            )
            self.assertGreater(last_page, 2)
            self.assertRegex(texts[0], rf"2 PLIEGO\s+{last_page}\b")
            source = reader.pages[next(
                index for index, text in enumerate(texts) if "Consulta preventiva:" in text
            )]
            destinations = [
                annotation.get_object().get("/Dest")
                or annotation.get_object().get("/A", {}).get("/D")
                for annotation in source.get("/Annots", [])
            ]
            self.assertEqual(len(destinations), 2)
            self.assertEqual(len({
                (destination[0].idnum, float(destination[3])) for destination in destinations
            }), 2)
            for destination in destinations:
                page = next(
                    page for page in reader.pages
                    if page.indirect_reference == destination[0]
                )
                self.assertIn("Definicion de", page.extract_text())

    def test_legacy_safety_chapter_migrates_to_next_cap_number(self):
        """Sustituye el antiguo capitulo SS por el siguiente CAP numerico."""
        app = self.make_app("pdf")
        generated = [
            item for item in app.budget_items if item.source.startswith("SS:")
        ]
        app.budget_items = [
            item for item in app.budget_items if not item.source.startswith("SS:")
        ]
        app.budget_items.extend(
            ProjectItem(
                item.code, "SS#", item.unit, item.summary,
                unit_price=item.unit_price, source=item.source,
                measurement=item.measurement,
            )
            for item in generated
        )
        app.chapter_order.remove("CAP.03#")
        app.chapter_titles.pop("CAP.03#")
        app.chapter_order.append("SS#")
        app.chapter_titles["SS#"] = "SEGURIDAD Y SALUD"

        synchronize_budget(
            app,
            [SafetyLine("SS0001", "ud", "Protecciones colectivas", Decimal("3.11"), Decimal(1))],
            Decimal("2"),
        )

        generated = [
            item for item in app.budget_items if item.source.startswith("SS:")
        ]
        self.assertEqual({item.chapter for item in generated}, {"CAP.03#"})
        self.assertNotIn("SS#", app.chapter_order)
        self.assertNotIn("SS#", app.chapter_titles)


if __name__ == "__main__":
    unittest.main()
