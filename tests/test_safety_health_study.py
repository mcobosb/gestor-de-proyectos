from decimal import Decimal
from pathlib import Path
from types import MethodType, SimpleNamespace
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader
from pypdf.generic import ContentStream

from bc3_gui import BudgetApp, Concept, ProjectItem
from budget_reports import ReportSettings
from report_layout import DEFAULT_TEMPLATE_PATH, ReportLayout
from tools.safety_health import SafetyLine, synchronize_budget, write_study_report
from tools.safety_health.study_report import (
    _Block, _StudyContent, _build_study_content, _pdf_project_summary,
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
        app._budget_concept_type = MethodType(BudgetApp._budget_concept_type, app)

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
                    target = Path(folder) / f"estudio.{output_format}"

                    write_study_report(app, target)

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
                    text = " ".join(text.split())
                    self.assertIn("ESTUDIO BÁSICO DE SEGURIDAD Y SALUD", text)
                    self.assertIn("ÍNDICE", text)
                    self.assertIn("LUCENA (CÓRDOBA); MADRID (MADRID)", text)
                    self.assertIn("ANDALUCÍA; MADRID, COMUNIDAD DE", text)
                    self.assertIn("FICHA_ESS_15APA.MD", text)
                    self.assertIn("UO-ARQUETAS", text)
                    self.assertIn("UO-FRESADO", text)
                    self.assertIn("Partida sin ficha ESS BCCA: OBRA001", text)
                    self.assertIn("DATOS PENDIENTES Y ADVERTENCIAS", text)
                    self.assertIn("PLIEGO DE CONDICIONES", text)
                    self.assertNotIn("PRESUPUESTO DE SEGURIDAD Y SALUD", text)
                    self.assertNotIn("{{", text)

    def test_first_section_combines_scope_selected_criteria_and_summary(self):
        """Integra alcance, criterios manuales y magnitudes sin duplicados ni contratista."""
        app = self.make_app("odt")
        app.report_settings.organization = "Ayuntamiento de prueba"
        app.report_settings.author = "Técnico redactor"
        app.report_settings.contractor = "Contratista que no debe mostrarse"
        app.report_settings.general_expenses = 13
        app.report_settings.industrial_profit = 6
        app.report_settings.vat = 21
        app.safety_health_state["study_criteria"].update(
            criterion_1_met=True, criterion_4_met=True,
        )

        content = _build_study_content(app)

        memory_index = next(
            index for index, block in enumerate(content.blocks)
            if block.kind == "heading" and block.text == "Memoria general"
        )
        introduction = content.blocks[:memory_index]
        text = "\n".join(block.text for block in introduction)
        self.assertEqual(introduction[0].text, "Objeto y alcance")
        self.assertIn("Su objeto es servir de base al Plan de Seguridad", text)
        self.assertIn("La actuacion comprende", text)
        self.assertIn("PROYECTO DE PRUEBA", text)
        self.assertIn("AYUNTAMIENTO DE PRUEBA", text)
        self.assertIn("a) Presupuesto".upper(), text)
        self.assertIn("d) Obra de tuneles".upper(), text)
        self.assertNotIn("b) Duracion".upper(), text)
        self.assertNotIn("c) Volumen".upper(), text)
        self.assertIn("ESTUDIO DE SEGURIDAD Y SALUD", text)
        self.assertNotIn("Evaluacion automatica", text)
        self.assertNotIn("Objeto y alcance", [
            block.text for block in content.blocks[memory_index:]
            if block.kind == "heading"
        ])
        self.assertNotIn(("heading", 1, "Datos del proyecto"), [
            (block.kind, block.level, block.text) for block in content.blocks
        ])
        summary = next(block for block in introduction if block.project_summary)
        rows = dict(summary.rows)
        self.assertNotIn("Contratista, si se conoce", rows)
        self.assertNotIn("Importe sin IVA", rows)
        self.assertNotIn("Importe total o sin IVA, indicando criterio", rows)
        pem = sum(Decimal(str(item.import_total)) for item in app.budget_items)
        pec = (pem * Decimal("1.19") * Decimal("1.21")).quantize(Decimal("0.01"))
        self.assertEqual(rows["PEM"], f"{pem:.2f} €".replace(".", ","))
        self.assertEqual(rows["PEC (con IVA)"], f"{pec:.2f} €".replace(".", ","))
        self.assertEqual(rows["Técnico/s redactor/es"], "TÉCNICO REDACTOR")
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
        self.assertIn("No se cumple ninguno".upper(), text)
        self.assertIn("ESTUDIO BÁSICO DE SEGURIDAD Y SALUD", text)

    def test_study_uses_general_municipalities_and_deduplicates_regions(self):
        """Integra la seleccion general y mantiene mayusculas sin avisos territoriales falsos."""
        app = self.make_app("odt")
        app.report_settings.municipality_codes = ["140386", "140214", "280796"]

        content = _build_study_content(app)

        summary = next(block for block in content.blocks if block.project_summary)
        rows = dict(summary.rows)
        self.assertEqual(rows["Municipio"], "LUCENA (CÓRDOBA); CÓRDOBA (CÓRDOBA); MADRID (MADRID)")
        self.assertEqual(rows["Provincia"], "CÓRDOBA; MADRID")
        self.assertEqual(rows["Comunidad autonoma"], "ANDALUCÍA; MADRID, COMUNIDAD DE")
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
                        if block.kind == "heading" and block.text == "Memoria general"
                    )
                    content.blocks = content.blocks[:memory_index]
                    target = Path(folder) / f"resumen.{output_format}"
                    with patch(
                        "tools.safety_health.study_report._build_study_content",
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
        self.assertIn("• R-", rows["Riesgos detectados"])
        self.assertNotIn("UO-", rows["Riesgos detectados"])
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
                    self.assertEqual(list(rows)[-4:], [
                        "Medios auxiliares", "Maquinaria empleada", "Riesgos detectados",
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
            "\n".join(block.text for block in content.blocks),
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
        table = _pdf_project_summary(block, layout)
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
            if block.kind == "heading" and block.text == "Memoria general"
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
                "tools.safety_health.study_report._build_study_content", return_value=content,
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
                "tools.safety_health.study_report._build_study_content", return_value=content,
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
            with ZipFile(DEFAULT_TEMPLATE_PATH) as source, ZipFile(template, "w") as target:
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
                "tools.safety_health.study_report._build_study_content", return_value=content,
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
            for master in styles.findall(f".//{{{style_ns}}}master-page"):
                header = master.find(f"{{{style_ns}}}header")
                self.assertNotIn(
                    "ESTUDIO DE SEGURIDAD Y SALUD", "".join(header.itertext()),
                )
                self.assertNotIn("Proyecto de prueba", "".join(header.itertext()))
                footer = master.find(f"{{{style_ns}}}footer")
                self.assertIsNotNone(footer.find(f".//{{{text_ns}}}page-number"))
                self.assertIn(content.title, "".join(footer.itertext()))
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
                *[
                    _Block("paragraph", "Contenido técnico. " * 45)
                    for _ in range(12)
                ],
                _Block("heading", "Pliego", 1),
                _Block("paragraph", "Fin del estudio."),
            ],
        )
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "indice.odt"
            with patch(
                "tools.safety_health.study_report._build_study_content", return_value=content,
            ):
                write_study_report(self.make_app("odt"), target)
            subprocess.run(
                [soffice, f"-env:UserInstallation={(root / 'profile').as_uri()}",
                 "--headless", "--convert-to", "pdf", "--outdir", str(root), str(target)],
                check=True, capture_output=True, timeout=90,
            )
            texts = [
                " ".join(page.extract_text().split())
                for page in PdfReader(root / "indice.pdf").pages
            ]
            last_page = next(
                index + 1 for index, text in enumerate(texts) if "Fin del estudio." in text
            )
            self.assertGreater(last_page, 2)
            self.assertRegex(texts[0], rf"2 PLIEGO\s+{last_page}\b")

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
