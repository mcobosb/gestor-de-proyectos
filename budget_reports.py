"""Informes A4 basados en los modelos de la carpeta plantillas.

Los PDF originales son ejemplos con datos, no formularios: se reconstruyen
sus tablas para permitir proyectos de cualquier longitud sin superponer texto.
Los presupuestos conservan el PEM; GG, BI e IVA se aplican en su resumen.
Las certificaciones usan importes mensuales, deducen la baja antes del IVA
y calculan cantidades sin inventar ni reutilizar mediciones presupuestadas.
La maquetación compartida conserva el logo institucional, columnas numéricas
alineadas a la derecha, descripciones sangradas y títulos y totales en negrita.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bc3_gui import BudgetApp


REPORT_NAMES = (
    "Mediciones",
    "Cuadro de precios 1 y 2 por capitulos",
    "Descompuestos",
    "Materiales",
    "Precios Auxiliares",
    "Presupuesto mediciones",
    "Presupuesto resumen",
)
CERTIFICATION_REPORT_NAMES = ("Resumen certificaciones", "Certificaciones con mediciones")
DEVIATION_REPORT_NAME = "Informe de desviaciones"
PERCENTAGE_FIELDS = {"general_expenses", "industrial_profit", "vat", "tender_discount", "indirect_costs"}
CURRENCY_CODES = {"EUR", "USD", "GBP", "CHF", "CAD", "AUD", "OTHER"}


@dataclass
class ReportSettings:
    author: str = ""
    role: str = ""
    organization: str = ""
    location: str = ""
    signature: str = "(Fecha y firma electrónicas)"
    contractor: str = ""
    contractor_representative: str = ""
    template_path: str = ""
    output_format: str = "pdf"
    currency: str = "EUR"
    custom_currency: str = ""
    general_expenses: float = 13.0
    industrial_profit: float = 6.0
    vat: float = 21.0
    tender_discount: float = 0.0
    indirect_costs: float = 0.0

    @classmethod
    def from_dict(cls, data: object) -> ReportSettings:
        """Valida textos y porcentajes del diccionario y construye la configuración de informes."""
        if not isinstance(data, dict):
            raise ValueError("La configuración debe ser un objeto JSON.")
        values = asdict(cls())
        for key in values:
            if key not in data:
                continue
            value = data[key]
            if key in PERCENTAGE_FIELDS:
                if isinstance(value, bool):
                    raise ValueError(f"Porcentaje no válido: {key}.")
                try:
                    number = float(str(value).strip().replace(",", "."))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Porcentaje no válido: {key}.") from exc
                if not isfinite(number) or not 0 <= number <= 100:
                    raise ValueError(f"El porcentaje {key} debe estar entre 0 y 100.")
                values[key] = number
            elif key == "output_format":
                if not isinstance(value, str) or value.lower() not in {"pdf", "odt"}:
                    raise ValueError("El formato de informes debe ser PDF u ODT.")
                values[key] = value.lower()
            elif key == "currency":
                if not isinstance(value, str) or value.upper() not in CURRENCY_CODES:
                    raise ValueError("La divisa seleccionada no es válida.")
                values[key] = value.upper()
            elif key == "custom_currency":
                if not isinstance(value, str):
                    raise ValueError("La divisa personalizada debe ser texto.")
                custom_currency = value.strip()
                if len(custom_currency) > 12 or any(ord(char) < 32 for char in custom_currency):
                    raise ValueError("La divisa personalizada debe tener como máximo 12 caracteres.")
                values[key] = custom_currency
            elif key == "template_path":
                if not isinstance(value, str):
                    raise ValueError("La ruta de la plantilla debe ser texto.")
                raw_path = value.strip()
                if raw_path and not Path(raw_path).expanduser().is_absolute():
                    raise ValueError("La ruta de la plantilla debe ser absoluta.")
                values[key] = str(Path(raw_path).expanduser()) if raw_path else ""
            else:
                if not isinstance(value, str):
                    raise ValueError(f"El campo {key} debe ser texto.")
                values[key] = value.strip()
        if values["currency"] == "OTHER" and not values["custom_currency"]:
            raise ValueError("Indica el símbolo o código de la divisa personalizada.")
        return cls(**values)

    def to_dict(self) -> dict:
        """Devuelve la configuración como diccionario serializable en JSON."""
        return asdict(self)


def money(value: Decimal | float) -> Decimal:
    """Redondea un importe a céntimos mediante redondeo comercial decimal."""
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def budget_totals(pem: float, settings: ReportSettings) -> dict[str, Decimal]:
    """Calcula GG y BI sobre el PEM, y el IVA sobre la base de licitación sin impuestos."""
    base = money(pem)
    gg = money(base * Decimal(str(settings.general_expenses)) / 100)
    bi = money(base * Decimal(str(settings.industrial_profit)) / 100)
    taxable = base + gg + bi
    iva = money(taxable * Decimal(str(settings.vat)) / 100)
    return dict(pem=base, general_expenses=gg, industrial_profit=bi,
                additions=gg + bi, taxable=taxable, vat=iva, total=taxable + iva)


def certification_totals(pem: float, settings: ReportSettings) -> dict[str, Decimal]:
    """Calcula el líquido certificado deduciendo la baja de adjudicación antes del IVA."""
    totals = budget_totals(pem, settings)
    discount = money(totals["taxable"] * Decimal(str(settings.tender_discount)) / 100)
    net = totals["taxable"] - discount
    vat = money(net * Decimal(str(settings.vat)) / 100)
    return {**totals, "discount": discount, "net": net, "vat": vat, "total": net + vat}


def amount_in_words(
    value: Decimal | float, currency: str = "EUR", custom_currency: str = ""
) -> str:
    """Expresa un importe redondeado en la divisa seleccionada y su unidad menor."""
    small = (
        "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",
        "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince",
        "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte",
        "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco",
        "veintiséis", "veintisiete", "veintiocho", "veintinueve",
    )
    tens = ("", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa")
    hundreds = ("", "ciento", "doscientos", "trescientos", "cuatrocientos",
                "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos")

    def apocopate(text: str) -> str:
        """Apocopa el numeral uno antes de un sustantivo masculino."""
        if text.endswith("veintiuno"):
            return text[:-9] + "veintiún"
        if text.endswith("uno"):
            return text[:-3] + "un"
        return text

    def integer(n: int) -> str:
        """Convierte un entero no negativo en palabras, agrupando miles, millones y billones."""
        if n < 30:
            return small[n]
        if n < 100:
            return tens[n // 10] + (" y " + small[n % 10] if n % 10 else "")
        if n == 100:
            return "cien"
        if n < 1000:
            return hundreds[n // 100] + (" " + integer(n % 100) if n % 100 else "")
        for scale, singular, plural in (
            (10**12, "un billón", "billones"),
            (10**6, "un millón", "millones"),
            (1000, "mil", "mil"),
        ):
            if n >= scale:
                count, rest = divmod(n, scale)
                prefix = singular if count == 1 else apocopate(integer(count)) + " " + plural
                return prefix + (" " + integer(rest) if rest else "")
        raise ValueError("Importe fuera de rango.")

    amount = money(value)
    negative = amount < 0
    cents = int(abs(amount) * 100)
    units, remainder = divmod(cents, 100)
    unit_names = {
        "EUR": ("euro", "euros", "céntimo", "céntimos"),
        "USD": ("dólar", "dólares", "centavo", "centavos"),
        "GBP": ("libra", "libras", "penique", "peniques"),
        "CHF": ("franco", "francos", "céntimo", "céntimos"),
        "CAD": ("dólar canadiense", "dólares canadienses", "centavo", "centavos"),
        "AUD": ("dólar australiano", "dólares australianos", "centavo", "centavos"),
    }
    singular, plural, minor_singular, minor_plural = unit_names.get(
        currency, (custom_currency or currency, custom_currency or currency, "centavo", "centavos")
    )
    text = apocopate(integer(units))
    text += (f" de {plural}" if units and units % 10**6 == 0 else
             f" {singular}" if units == 1 else f" {plural}")
    if remainder:
        text += " con " + apocopate(integer(remainder))
        text += f" {minor_singular}" if remainder == 1 else f" {minor_plural}"
    return (("menos " if negative else "") + text).upper()


def _collect_report_resources(app):
    """Acumula auxiliares y materiales usados con sus cantidades y precios actuales."""
    from bc3_gui import Concept, ProjectItem

    auxiliaries: dict[tuple, tuple] = {}
    materials: dict[tuple, list] = {}

    def walk(item, concept, quantity: Decimal, price: float, stack=(), kind=None):
        """Recorre una descomposición y comprueba recursos auxiliares circulares o ausentes."""
        if concept.code in stack:
            raise ValueError(f"Descomposición circular: {' → '.join((*stack, concept.code))}")
        if concept.children:
            dataset = app._component_dataset(item)
            auxiliary = ProjectItem(
                concept.code, item.chapter, concept.unit, concept.summary,
                unit_price=price, decomposition=list(concept.children),
            )
            children = auxiliary.cost_components(dataset)
            key = (concept.code, price, tuple((c.code, q, p) for c, q, p in children))
            auxiliaries[key] = (concept, price, children)
            for child, factor, child_price in children:
                walk(item, child, quantity * Decimal(str(factor)), child_price,
                     (*stack, concept.code))
        elif kind == "aux":
            raise ValueError(f"No se dispone de la descomposición del auxiliar {concept.code}.")
        elif (kind or concept.ctype) == "3":
            key = (concept.code, concept.unit, concept.summary, price)
            if key not in materials:
                materials[key] = [concept, Decimal(0), price]
            materials[key][1] += quantity

    for item in app.budget_items:
        if item.decomposition:
            for concept, quantity, price in app._report_components(item):
                walk(item, concept, Decimal(str(item.measurement)) * Decimal(str(quantity)),
                     price, kind=app._budget_concept_type(item, concept.code))
        else:
            dataset = app._component_dataset(item)
            concept = dataset.concepts.get(item.code) if dataset else None
            concept = concept or Concept(
                item.code, item.unit, item.summary, item.unit_price,
                app._budget_concept_type(item), item.text,
            )
            walk(item, concept, Decimal(str(item.measurement)), item.unit_price,
                 kind=app._budget_concept_type(item))
    return auxiliaries, materials


def write_pdf(app: BudgetApp, report: str, target: Path) -> None:
    """Genera tablas paginadas; solo recorre los auxiliares usados en la obra."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether, PageBreak, SimpleDocTemplate, Spacer, Flowable,
    )
    from reportlab.platypus.doctemplate import LayoutError
    from bc3_gui import fmt_number, format_chapter_code
    from report_layout import (
        COLUMN_REFERENCE_WIDTH, CONTENT_WIDTH, LEFT_MARGIN, RIGHT_MARGIN, ReportLayout,
        MEASUREMENT_COLUMN_RATIOS, budget_report_columns, report_project_label,
    )

    if report not in REPORT_NAMES:
        raise ValueError(f"Tipo de informe desconocido: {report}")
    settings = app.report_settings
    title = app._project_title()
    from bc3_gui import currency_label

    project_label = report_project_label(
        title, "" if report == "Mediciones" else currency_label(settings.currency, settings.custom_currency),
    )
    layout = ReportLayout(template_path=settings.template_path)
    text = layout.text
    table = layout.table
    story = []
    header_title = {
        "Descompuestos": "CUADRO DE PRECIOS DESCOMPUESTOS",
        "Materiales": "LISTADO ELEMENTALES VALORADOS\n(PRESUPUESTO)",
        "Precios Auxiliares": "CUADRO DE PRECIOS AUXILIARES",
        "Presupuesto mediciones": "PRESUPUESTO",
        "Presupuesto resumen": "RESUMEN DE PRESUPUESTO",
        "Mediciones": "MEDICIONES",
    }.get(report, "CUADRO DE PRECIOS 1")
    page_headings = {}
    current_heading = header_title
    _, component_widths = budget_report_columns("Descompuestos")
    _, auxiliary_widths = budget_report_columns("Precios Auxiliares")
    column_headers, column_ratios = budget_report_columns(report)

    class SectionMarker(Flowable):
        def __init__(self, value):
            """Crea un marcador sin altura que cambia el título de la sección impresa."""
            super().__init__()
            self.value = value
            self.width = self.height = 0

        def draw(self):
            """Asocia la página actual al cuadro de precios que se está imprimiendo."""
            nonlocal current_heading
            current_heading = self.value
            page_headings[self.canv.getPageNumber()] = self.value

    def total(label, value, major=True):
        """Añade una línea tabulada de importe con el énfasis del total correspondiente."""
        if report == "Presupuesto resumen":
            indent = COLUMN_REFERENCE_WIDTH * (0.36 if major else 0.48)
            block = table(
                [], [["", label, money(value), ""]],
                [indent, 393 - indent, 75, COLUMN_REFERENCE_WIDTH - 468],
                bold_rows=(0,) if major else (), leaders=(1,),
            )
            if major:
                from reportlab.lib import colors

                block.setStyle([("LINEABOVE", (2, 0), (2, 0), 0.5, colors.black)])
            story.append(block)
        else:
            story.append(layout.total(label, money(value), major=major))

    def chapter(code):
        """Inserta la banda de capítulo con su código normalizado y su título."""
        story.append(Spacer(1, 2 * mm))
        story.append(layout.chapter(format_chapter_code(code), app._chapter_title(code)))

    def item_heading(item, value=None):
        """Añade la partida en negrita y su descripción sangrada, con importe opcional."""
        row = [item.code, item.unit, text(item.summary, layout.item_summary)]
        ratios = [50, 15, CONTENT_WIDTH - 65]
        if value is not None:
            row.append(money(value))
            ratios[-1] -= 50
            ratios.append(50)
        block = table([], [row], ratios, bold_rows=(0,))
        block.keepWithNext = True
        story.append(block)
        if item.text:
            story.append(layout.description(item.text))

    def components(item):
        """Obtiene la descomposición con precios actuales y valida recursos disponibles."""
        return app._report_components(item)

    def grouped_items():
        """Agrupa partidas por capítulo respetando el orden configurado en el proyecto."""
        codes = list(dict.fromkeys([*app.chapter_order, *(i.chapter for i in app.budget_items)]))
        return [(code, [i for i in app.budget_items if i.chapter == code]) for code in codes]

    groups = [(code, items) for code, items in grouped_items() if items]
    pem = sum((money(i.import_total) for i in app.budget_items), Decimal(0))

    if report in {"Materiales", "Precios Auxiliares"}:
        auxiliaries, materials = _collect_report_resources(app)
    else:
        auxiliaries, materials = {}, {}

    if report == "Presupuesto resumen":
        rows = []
        for code, items in groups:
            amount = sum((money(i.import_total) for i in items), Decimal(0))
            percent = amount * 100 / pem if pem else Decimal(0)
            rows.append([format_chapter_code(code), app._chapter_title(code), amount, money(percent)])
        story.append(table(["CAPÍTULO", "RESUMEN", "IMPORTE", "%"], rows,
                           [65, 328, 75, COLUMN_REFERENCE_WIDTH - 468], leaders=(1,)))
        totals = budget_totals(float(pem), settings)
        total("PRESUPUESTO DE EJECUCIÓN MATERIAL", totals["pem"])
        total(f"{fmt_number(settings.general_expenses)} % Gastos generales", totals["general_expenses"], False)
        total(f"{fmt_number(settings.industrial_profit)} % Beneficio industrial", totals["industrial_profit"], False)
        total("Suma", totals["additions"], False)
        total("PRESUPUESTO BASE DE LICITACIÓN SIN IVA", totals["taxable"])
        total(f"{fmt_number(settings.vat)} % IVA", totals["vat"], False)
        total("PRESUPUESTO BASE DE LICITACIÓN", totals["total"])
        story.append(Spacer(1, 5 * mm))
        story.append(text("Asciende el presupuesto a la expresada cantidad de " +
                          amount_in_words(
                              totals["total"], settings.currency, settings.custom_currency
                          ), layout.words_style))
    elif report == "Materiales":
        rows = []
        group = None
        subtotal = Decimal(0)
        grand_total = Decimal(0)
        for _, (concept, quantity, price) in sorted(materials.items()):
            prefix = concept.code[:3]
            if group is not None and prefix != group:
                rows.append(["", "", "", f"Grupo {group}", "", subtotal])
                subtotal = Decimal(0)
            amount = money(quantity * Decimal(str(price)))
            rows.append([concept.code, fmt_number(float(quantity), 3), concept.unit,
                         concept.summary, money(price), amount])
            subtotal += amount
            grand_total += amount
            group = prefix
        if group is not None:
            rows.append(["", "", "", f"Grupo {group}", "", subtotal])
        if rows:
            subtotal_rows = [i for i, row in enumerate(rows) if str(row[3]).startswith("Grupo ")]
            story.append(table(["CÓDIGO", "CANTIDAD", "UD.", "RESUMEN", "PRECIO/UD.", "IMPORTE"],
                               rows, [50, 45, 18, 290, 50, 57], bold_rows=subtotal_rows,
                               highlighted_rows=subtotal_rows))
        else:
            story.append(text("No hay materiales en las descomposiciones del proyecto."))
        total("TOTAL", grand_total)
    elif report == "Precios Auxiliares":
        for concept, price, children in auxiliaries.values():
            p = table([], [[concept.code, concept.unit, text(concept.summary, layout.item_summary)]],
                      [50, 15, CONTENT_WIDTH - 65], bold_rows=(0,))
            p.keepWithNext = True
            story.append(p)
            if concept.text:
                story.append(layout.description(concept.text))
            rows = [[c.code, c.unit, fmt_number(q, 3), c.summary, money(p), money(q * p), ""]
                    for c, q, p in children]
            story.append(table([], rows, auxiliary_widths))
            total("COSTE UNITARIO TOTAL", price)
            difference = money(price) - sum((money(q * p) for _, q, p in children), Decimal(0))
            if difference:
                total("Diferencia respecto a los componentes", difference)
            story.append(text("Asciende el precio unitario del concepto auxiliar a " +
                              amount_in_words(
                                  price, settings.currency, settings.custom_currency
                              ), layout.words_style))
            story.append(Spacer(1, 4 * mm))
        if not auxiliaries:
            story.append(text("No hay precios auxiliares en las descomposiciones del proyecto."))
    else:
        sections = (1, 2) if report == "Cuadro de precios 1 y 2 por capitulos" else (0,)
        for section_index, section in enumerate(sections):
            if section_index:
                story.append(PageBreak())
            section_title = (
                f"CUADRO DE PRECIOS {section}" if section else
                "PRESUPUESTO" if report == "Presupuesto mediciones" else
                "MEDICIONES" if report == "Mediciones" else
                "CUADRO DE PRECIOS DESCOMPUESTOS"
            )
            story.append(SectionMarker(section_title))
            for chapter_index, (code, items) in enumerate(groups):
                if chapter_index:
                    story.append(PageBreak())
                chapter(code)
                for item in items:
                    item_heading(item, item.unit_price if section == 1 else
                                 item.import_total if report == "Presupuesto mediciones" else None)
                    if section == 1:
                        story.append(text(amount_in_words(
                            item.unit_price, settings.currency, settings.custom_currency
                        ), layout.words_style))
                    elif section == 2:
                        grouped = {}
                        for concept, quantity, price in components(item):
                            kind = app._budget_concept_type(item, concept.code)
                            label = {"1": "Mano de obra", "2": "Maquinaria", "3": "Materiales"}.get(
                                kind, "Resto de obra y costes complementarios")
                            if concept.code == "%CI" and item.indirect_costs:
                                label = f"Costes indirectos ({fmt_number(item.indirect_costs)} %)"
                            grouped[label] = grouped.get(label, Decimal(0)) + money(quantity * price)
                        if grouped:
                            story.append(table(["RESUMEN", "IMPORTE"], list(grouped.items()), [8, 2]))
                            difference = money(item.unit_price) - sum(grouped.values(), Decimal(0))
                            if difference:
                                total("Diferencia respecto al precio unitario", difference)
                        else:
                            story.append(text("Sin descomposición."))
                        total("TOTAL PARTIDA", item.unit_price)
                    elif report == "Descompuestos":
                        rows = [[c.code, c.unit, c.summary, fmt_number(q, 3), money(p), money(q * p), ""]
                                for c, q, p in components(item)]
                        if rows:
                            story.append(table([], rows, component_widths))
                            difference = money(item.unit_price) - sum(
                                (money(q * p) for _, q, p in components(item)), Decimal(0))
                            if difference:
                                total("Diferencia respecto al precio unitario", difference)
                        else:
                            story.append(text("Sin descomposición."))
                        total("TOTAL PARTIDA", item.unit_price)
                    else:
                        if item.measurement_lines:
                            rows = [[line.comment, *[
                                "" if v is None else fmt_number(v, 3)
                                for v in (line.units, line.length, line.width, line.height)
                            ], fmt_number(line.k, 3),
                                fmt_number(line.partial, 3) if report == "Mediciones" else money(line.partial)]
                                for line in item.measurement_lines]
                            story.append(table(
                                ["COMENTARIO", "UDS", "LONGITUD", "ANCHURA", "ALTURA", "K", "PARCIAL"]
                                + ([""] if report == "Mediciones" else []),
                                [row + [""] for row in rows] if report == "Mediciones" else rows,
                                MEASUREMENT_COLUMN_RATIOS if report == "Mediciones" else
                                [4, 1, 1, 1, 1, 0.8, 1.2]))
                        if report == "Mediciones":
                            story.append(table(
                                [],
                                [["MEDICIÓN TOTAL", "", "", "", "", "",
                                  text(fmt_number(item.measurement, 3), layout.bold_right), item.unit]],
                                MEASUREMENT_COLUMN_RATIOS, bold_rows=(0,),
                            ))
                        else:
                            story.append(table(
                                ["CANTIDAD", "PRECIO", "IMPORTE"],
                                [[money(item.measurement), money(item.unit_price), money(item.import_total)]],
                                [CONTENT_WIDTH - 100, 50, 50]))
                    story.append(Spacer(1, 3 * mm))
                if report == "Presupuesto mediciones":
                    total(f"TOTAL {format_chapter_code(code)}",
                          sum((money(i.import_total) for i in items), Decimal(0)))
            if report == "Presupuesto mediciones":
                total("TOTAL EJECUCIÓN MATERIAL", pem)

    signature = [v for v in (
        settings.location, settings.role, settings.author, settings.organization, settings.signature
    ) if v]
    if signature:
        story.append(KeepTogether([Spacer(1, 8 * mm), *[
            text(v, layout.signature_note if v == settings.signature else layout.center)
            for v in signature
        ]]))

    def page_header(canvas, document):
        """Dibuja el logo, título de sección, cabeceras tabuladas y pie de página."""
        layout.header(canvas, document, page_headings.get(document.page, current_heading),
                      project_label,
                      footer=f"{date.today():%d/%m/%Y}",
                      column_headers=column_headers, ratios=column_ratios)

    class ReportDocument(SimpleDocTemplate):
        def afterPage(self):
            """Dibuja la cabecera tras conocer la sección efectivamente impresa en la página."""
            page_header(self.canv, self)

    document = ReportDocument(
        str(target), pagesize=A4, rightMargin=RIGHT_MARGIN - 6, leftMargin=LEFT_MARGIN - 6,
        topMargin=layout.top_margin(header_title, project_label, column_headers, column_ratios),
        bottomMargin=layout.bottom_margin(), title=f"{report} - {title}",
        author=settings.author,
    )
    try:
        document.build(story)
    except LayoutError as exc:
        raise ValueError(f"No se pudo paginar el informe: {exc}") from exc


def certification_deviation_rows(items, planned_amounts, month_key: str):
    """Calcula las desviaciones del mes con la previsión original y redondeo monetario."""
    from bc3_gui import format_chapter_code

    if planned_amounts is None or len(planned_amounts) != len(items):
        raise ValueError("Las previsiones deben corresponder a todas las partidas del proyecto.")
    rows = []
    for item, planned in zip(items, planned_amounts):
        planned = item.certification_plans.get(month_key, planned)
        certified = item.certifications.get(month_key, 0.0)
        if not isfinite(planned) or not isfinite(certified):
            raise ValueError(f"Importe de previsión o certificación no válido en {item.code}.")
        planned, certified = money(planned), money(certified)
        rows.append([
            format_chapter_code(item.chapter), item.code, item.summary,
            planned, certified, certified - planned,
            item.certification_justifications.get(month_key, ""),
        ])
    if not rows:
        raise ValueError("No hay partidas para el informe de desviaciones.")
    totals = [sum((row[column] for row in rows), Decimal(0)) for column in (3, 4, 5)]
    return rows, ["", "", "TOTAL", *totals, ""]


def write_certification_pdf(
    items, chapter_title, project_title: str, settings: ReportSettings,
    report: str, target: Path, month_key: str, certification_number: int, month_label: str,
    *, planned_amounts: list[float] | None = None,
) -> None:
    """Certifica importes del mes; no atribuye las mediciones del presupuesto al mes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        KeepTogether, PageBreak, SimpleDocTemplate, Spacer,
    )
    from reportlab.platypus.doctemplate import LayoutError
    from bc3_gui import fmt_number, format_chapter_code
    from report_layout import (
        CERTIFICATION_COLUMN_RATIOS, CERTIFICATION_SUMMARY_RATIOS, COLUMN_REFERENCE_WIDTH,
        CONTENT_WIDTH, DEVIATION_COLUMN_RATIOS, DEVIATION_HEADERS,
        LEFT_MARGIN, RIGHT_MARGIN, ReportLayout, report_project_label,
    )

    if report not in (*CERTIFICATION_REPORT_NAMES, DEVIATION_REPORT_NAME):
        raise ValueError(f"Tipo de informe desconocido: {report}")
    detail = report == CERTIFICATION_REPORT_NAMES[1]
    deviation = report == DEVIATION_REPORT_NAME
    deviation_rows, deviation_total = (
        certification_deviation_rows(items, planned_amounts, month_key) if deviation else ([], [])
    )
    certified = []
    for item in items:
        value = item.certifications.get(month_key, 0.0)
        if not isfinite(value):
            raise ValueError(f"Importe certificado no válido en {item.code}.")
        amount = money(value)
        if amount:
            if detail and (not isfinite(item.unit_price) or item.unit_price == 0):
                raise ValueError(
                    f"No se puede calcular la cantidad certificada de {item.code}: "
                    "el precio unitario debe ser finito y distinto de cero."
                )
            certified.append((item, amount))
    if not certified and not deviation:
        raise ValueError("No hay partidas con importe certificado en el mes seleccionado.")

    report_heading = "INFORME DE DESVIACIONES" if deviation else (
        "CERTIFICACIÓN Y MEDICIONES" if detail else "RESUMEN DE CERTIFICACIÓN"
    )
    heading = f"{report_heading} · Certificación {certification_number} · {month_label}"
    layout = ReportLayout(certification=True, template_path=settings.template_path)
    from bc3_gui import currency_label

    project_label = report_project_label(
        project_title, currency_label(settings.currency, settings.custom_currency),
    )
    text = layout.text
    table = layout.table
    story = []

    def total(label, value, major=True):
        """Añade una línea económica tabulada al detalle o resumen de certificación."""
        if detail:
            story.append(layout.total(label, value, major=major))
        else:
            indent = COLUMN_REFERENCE_WIDTH * (0.25 if major else 0.35)
            story.append(table(
                [], [["", label, value, ""]],
                [indent, sum(CERTIFICATION_SUMMARY_RATIOS[:2]) - indent,
                 *CERTIFICATION_SUMMARY_RATIOS[2:]],
                bold_rows=(0,) if major else (), rule=major, leaders=(1,),
            ))

    codes = list(dict.fromkeys(item.chapter for item, _ in certified))
    chapter_amounts = {
        code: sum((amount for item, amount in certified if item.chapter == code), Decimal(0))
        for code in codes
    }
    pem = sum(chapter_amounts.values(), Decimal(0))
    if deviation:
        story.append(table(
            DEVIATION_HEADERS, [*deviation_rows, deviation_total],
            DEVIATION_COLUMN_RATIOS, bold_rows=(len(deviation_rows),),
        ))
        story.append(text(
            "Desviación = certificado - plan original del mes. "
            "Se incluyen las partidas pendientes de certificar."
        ))
    elif detail:
        for chapter_index, code in enumerate(codes):
            if chapter_index:
                story.append(PageBreak())
            story.append(layout.chapter(format_chapter_code(code), chapter_title(code)))
            for item, amount in certified:
                if item.chapter != code:
                    continue
                block = table([], [[item.code, item.unit, item.summary]],
                              [57, 14, CONTENT_WIDTH - 71], bold_rows=(0,))
                block.keepWithNext = True
                story.append(block)
                if item.text:
                    story.append(layout.description(item.text))
                quantity = Decimal(str(item.certifications[month_key])) / Decimal(str(item.unit_price))
                story.append(table(
                    [],
                    [["", "", "", "", "", "", fmt_number(float(quantity), 6), money(item.unit_price), amount]],
                    CERTIFICATION_COLUMN_RATIOS,
                ))
                justification = item.certification_justifications.get(month_key, "")
                if justification:
                    story.append(text(f"Justificación: {justification}"))
                story.append(Spacer(1, 4 * mm))
            total(f"TOTAL {format_chapter_code(code)}", chapter_amounts[code])
        total("TOTAL EJECUCIÓN MATERIAL CERTIFICADA", pem)
        story.append(Spacer(1, 4 * mm))
        story.append(text(
            "Cantidad certificada = importe certificado del mes / precio unitario. "
            "No se incluyen líneas de medición del presupuesto ni dimensiones no certificadas. "
            "La cantidad se muestra con seis decimales; el importe mensual guardado prevalece."
        ))
    else:
        rows = []
        for code in codes:
            budget = sum((money(i.import_total) for i in items if i.chapter == code), Decimal(0))
            percentage = fmt_number(float(chapter_amounts[code] * 100 / budget)) if budget else "No aplicable"
            rows.append([format_chapter_code(code), chapter_title(code), chapter_amounts[code], percentage])
        story.append(table(["CAPÍTULO", "RESUMEN", "IMPORTE", "% CERTIFICADO"],
                           rows, CERTIFICATION_SUMMARY_RATIOS, leaders=(1,)))
        story.append(text("% certificado respecto al presupuesto de cada capítulo."))
        totals = certification_totals(float(pem), settings)
        total("EJECUCIÓN MATERIAL", totals["pem"])
        total(f"{fmt_number(settings.general_expenses)} % Gastos generales", totals["general_expenses"], False)
        total(f"{fmt_number(settings.industrial_profit)} % Beneficio industrial", totals["industrial_profit"], False)
        total("Suma", totals["additions"], False)
        total("EJECUCIÓN POR CONTRATA SIN IVA", totals["taxable"])
        total(f"{fmt_number(settings.tender_discount, 7)} % Baja (a deducir)", totals["discount"], False)
        total("EJECUCIÓN DESPUÉS DE BAJA", totals["net"])
        total(f"{fmt_number(settings.vat)} % IVA", totals["vat"], False)
        total(f"LÍQUIDO CERTIFICACIÓN {certification_number}", totals["total"])
        story.append(Spacer(1, 4 * mm))
        story.append(text("Asciende la presente certificación a la expresada cantidad de " +
                          amount_in_words(
                              totals["total"], settings.currency, settings.custom_currency
                          ), layout.words_style))

    signatures = [[
        text("\n".join(filter(None, (settings.contractor_representative, settings.contractor, settings.signature))),
             layout.center),
        text("\n".join(filter(None, (settings.role, settings.author, settings.organization,
                                    settings.location, settings.signature))), layout.center),
    ]]
    story.append(KeepTogether([Spacer(1, 8 * mm), table([], signatures, [1, 1])]))

    def page_header(canvas, document):
        """Dibuja la cabecera institucional y la identificación del mes certificado."""
        layout.header(
            canvas, document, heading,
            project_label,
            footer=f"Certificación {certification_number} · {month_label}",
            column_headers=["CÓDIGO", "RESUMEN", "UDS", "LONGITUD", "ANCHURA", "ALTURA",
                            "CANTIDAD", "PRECIO", "IMPORTE"] if detail else (),
            ratios=CERTIFICATION_COLUMN_RATIOS,
        )

    column_headers = (["CÓDIGO", "RESUMEN", "UDS", "LONGITUD", "ANCHURA", "ALTURA",
                       "CANTIDAD", "PRECIO", "IMPORTE"] if detail else ())
    column_ratios = CERTIFICATION_COLUMN_RATIOS
    document = SimpleDocTemplate(
        str(target), pagesize=A4, leftMargin=LEFT_MARGIN - 6, rightMargin=RIGHT_MARGIN - 6,
        topMargin=layout.top_margin(heading, project_label, column_headers, column_ratios),
        bottomMargin=layout.bottom_margin(),
        title=f"{report} - Certificación {certification_number} - {project_title}", author=settings.author,
    )
    try:
        document.build(story, onFirstPage=page_header, onLaterPages=page_header)
    except LayoutError as exc:
        raise ValueError(f"No se pudo paginar el informe: {exc}") from exc
