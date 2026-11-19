"""Documentos base editables y variables enlazadas a la configuración del proyecto."""

from dataclasses import dataclass
from pathlib import Path
import re

from tools._library_editor import library_members, read_markdown
from tools._library_markdown import document_markdown, text_blocks
from tools._shared.document_layout import project_document_variables
from .indirect import GROUPS, GROUP_HEADERS, RATIOS, LIMIT, calculate, default_rows, equivalent_interest, rate_label, row_cost


LIBRARY_PATH = Path(__file__).resolve().parent / "BIBLIOTECA.zip"
DOCUMENTS = (
    ("01_legislacion.md", "1. Legislación vigente"),
    ("02_composicion.md", "2. Composición de los precios"),
    ("03_indirectos.md", "2.1. Costes indirectos"),
    ("04_mano_obra.md", "2.2. Mano de obra"),
    ("05_materiales.md", "2.3. Material a pie de obra"),
    ("06_maquinaria.md", "2.4. Maquinaria"),
    ("07_metodo_calculo.md", "2.4.1. Resumen del método de cálculo"),
    ("08_estructura_coste.md", "2.4.2. Estructura del coste"),
)
FORMULA_LEVEL = -100


@dataclass(frozen=True)
class NarrativeTable:
    """Tabla con columnas, rótulo y clave de referencia del anejo."""

    title: str
    rows: tuple[tuple[str, str], ...]
    key: str = ""
    headers: tuple[str, ...] = ("Concepto", "Detalle")
    ratios: tuple[float, ...] = (0.3, 0.7)
    numeric: tuple[int, ...] = ()
    bands: tuple[int, ...] = ()
    subheaders: tuple[int, ...] = ()
    width: float = 1.0
    centered: bool = False


@dataclass(frozen=True)
class Equation:
    """Expresión editable con una referencia opcional."""

    source: str
    key: str
    number: int


_PROJECT_ROWS = (
    ("Proyecto", "titulo_proyecto"), ("Entidad", "entidad"), ("Expediente", "expediente"),
    ("Emplazamiento", "emplazamiento"), ("Provincia o provincias", "provincias"),
    ("Comunidad o comunidades autónomas", "comunidades_autonomas"),
    ("Fecha de referencia de los precios", "fecha_referencia"),
)
_CONTRIBUTION_ROWS = (
    ("Bases de cotización", "bases_tabla"), ("Tipos de cotización", "tipos_tabla"),
)
_AGREEMENT_ROWS = (("Convenio", "convenio_texto"), ("Revisión salarial", "revision_texto"))
_TABLES = {
    "tabla_proyecto": ("Datos del proyecto", _PROJECT_ROWS),
    "tabla_convenio": ("Convenio y revisión salarial", _AGREEMENT_ROWS),
    "tabla_mano_obra": ("Referencias adoptadas para la mano de obra", (
        ("Referencia salarial o SMI", "salario_texto"), *_CONTRIBUTION_ROWS,
        ("Conceptos cotizables y exentos", "contribution_details"),
        ("Aportación empresarial al plan de pensiones", "pension_texto"),
        ("Calendario laboral", "calendario_texto"), ("Condiciones de trabajo", "condiciones_texto"),
        ("Jornada anual efectiva de referencia", "jornada_tabla"),
    )),
}


def calculated_tables(app, state: dict) -> tuple[dict[str, NarrativeTable], str]:
    """Prepara los importes, el cálculo razonado de K y la sensibilidad de i_m."""
    from budget_core import fmt_number

    rows = state.get("indirect_rows", default_rows())
    costs = calculate(app, rows)
    detail, bands, subheaders = [], [], []
    for group, (label, _) in GROUPS.items():
        bands.append(len(detail))
        detail.append(("", label, "", "", "", ""))
        subheaders.append(len(detail))
        detail.append(GROUP_HEADERS[group])
        detail.extend((
            fmt_number(row["quantity"]) if "quantity" in row else "", row["role"],
            fmt_number(row["monthly"]) if "monthly" in row else "",
            fmt_number(row["months"]) if "months" in row else "",
            fmt_number(row["dedication"]) if "dedication" in row else "",
            fmt_number(float(row_cost(row))),
        ) for row in rows if row["group"] == group)
    bands.append(len(detail))
    detail.append(("", "Total de costes indirectos propios", "", "", "", fmt_number(float(costs.total))))
    indirect = NarrativeTable(
        "Estimación de costes indirectos propios (importe en €)", tuple(detail),
        "tabla_indirectos", (), RATIOS, (0, 2, 3, 4, 5), tuple(bands), tuple(subheaders),
    )
    rates = (2, 4, 6, 8)
    columns = len(rates) + 1
    interest = NarrativeTable(
        "Interés medio anual equivalente por vida económica (%)",
        tuple((str(years), *(fmt_number(float(equivalent_interest(years, rate))) for rate in rates))
              for years in range(1, 21)),
        "tabla_interes", ("T (años)", *(f"i = {rate} %" for rate in rates)), (1 / columns,) * columns,
        width=0.68, centered=True,
    )
    if costs.raw is None:
        explanation = ("No se obtiene K_1 ni K porque el coste directo total del presupuesto no es positivo. "
                       "La previsión de imprevistos K_2 es del 1 %.")
    else:
        decision = ("Como el resultado supera el 2 %, se adopta el límite del 2 %."
                    if costs.raw > LIMIT else f"Como no supera el 2 %, se adopta {rate_label(costs.adopted)}.")
        explanation = (
            f"Según la ecuación [[ec:k1]], el total de {fmt_number(float(costs.total))} € se divide entre "
            f"el coste directo total de {fmt_number(float(costs.direct))} €, sin indirectos, gastos generales, "
            f"beneficio industrial ni IVA. Se obtiene un K_1 de {rate_label(costs.raw)}. {decision} "
            f"Se añade un K_2 del 1 % por imprevistos y, siguiendo la ecuación [[ec:k]], "
            f"se obtiene un K de {rate_label(costs.rate)}."
        )
        if costs.differs:
            explanation += (
                f" El presupuesto conserva su porcentaje configurado de {rate_label(costs.budget_rate)}; "
                "los listados de precios reflejan ese porcentaje y no el K calculado en este apartado."
            )
    return {"tabla_indirectos": indirect, "tabla_interes": interest}, explanation


def load_documents(archive_path: Path) -> list[tuple[str, str]]:
    """Lee los documentos obligatorios sin extraer ni mantener copias paralelas."""
    members = set(library_members(archive_path))
    missing = [name for name, _ in DOCUMENTS if name not in members]
    if missing:
        raise ValueError("Faltan documentos base de justificación de precios: " + ", ".join(missing))
    return [(name, read_markdown(archive_path, name)) for name, _ in DOCUMENTS]


def general_variables(app) -> dict[str, str]:
    """Obtiene los datos comunes actuales sin utilizar copias guardadas en la herramienta."""
    return project_document_variables(app._project_title(), app.report_settings)


def template_variables(app, state: dict) -> dict[str, str]:
    """Combina los datos específicos con los valores generales, que siempre prevalecen."""
    from budget_core import currency_label, fmt_number

    values = {key: value for key, value in state.items() if isinstance(value, str)}
    for key, value in general_variables(app).items():
        values[key] = value or ""
    values.update({
        "fecha_referencia": state.get("reference_date", ""),
        "porcentaje_indirectos": fmt_number(app.report_settings.indirect_costs),
        "moneda": currency_label(app.report_settings.currency, app.report_settings.custom_currency),
        "normativa_contratacion": (
            "[Ley 9/2017, de 8 de noviembre, de Contratos del Sector Público]"
            "(https://www.boe.es/buscar/act.php?id=BOE-A-2017-12902), y Reglamento general "
            "de la Ley de Contratos de las Administraciones Públicas, aprobado por "
            "[Real Decreto 1098/2001, de 12 de octubre]"
            "(https://www.boe.es/buscar/act.php?id=BOE-A-2001-19995), en lo vigente y aplicable."
            if state.get("contract_type", "public") == "public" else
            "En contratación privada se atenderá al contrato y a la normativa aplicable; "
            "los criterios siguientes se utilizan como referencia técnica de composición de costes."
        ),
        "referencia_composicion": (
            "De acuerdo con el [artículo 130 del Reglamento general de la Ley de Contratos "
            "de las Administraciones Públicas](https://www.boe.es/buscar/act.php?id=BOE-A-2001-19995#a130)"
            if state.get("contract_type", "public") == "public" else
            "Como criterio técnico de composición de costes"
        ),
        "criterio_maquinaria": (
            "Se utilizan los precios horarios de la fuente presupuestaria. El método que sigue "
            "describe su estructura de referencia, sin acreditar un cálculo analítico no realizado."
            if state.get("machinery_method", "budget") == "budget" else
            "Se adopta una valoración analítica documentada; los parámetros y resultados "
            "concretos deberán justificarse para cada máquina."
        ),
    })
    for key in (
        "agreement", "salary_revision", "wage_reference", "contribution_bases",
        "contributions", "contribution_details", "pension_contribution", "calendar",
        "work_conditions", "annual_hours",
    ):
        values[key] = values.get(key) or "[PENDIENTE DE DEFINIR Y VERIFICAR]"
    for key in ("salary_revision", "wage_reference", "contribution_bases",
                "contributions", "pension_contribution"):
        values[key] = re.sub(r"^2026\s+[—–-]\s+", "", values[key])
    values["jornada_tabla"] = (values["annual_hours"] if values["annual_hours"].startswith("[")
                              else f"{values['annual_hours']} horas")
    for key, field in (
        ("convenio_texto", "agreement"), ("revision_texto", "salary_revision"),
        ("salario_texto", "wage_reference"), ("bases_tabla", "contribution_bases"),
        ("tipos_tabla", "contributions"), ("pension_texto", "pension_contribution"),
        ("calendario_texto", "calendar"), ("condiciones_texto", "work_conditions"),
    ):
        values[key] = values[field].strip().rstrip(".")
    values["condiciones_texto"] = re.sub(
        r"\b(Vacaciones|Horas extraordinarias):\s*", lambda match: {
            "Vacaciones": "Las vacaciones son de ",
            "Horas extraordinarias": "Las horas extraordinarias tienen un límite de ",
        }[match[1]], values["condiciones_texto"],
    ).replace("un límite de máximo ", "un límite de ")
    salary_revision = values.get("salary_revision", "")
    if salary_revision:
        values["agreement"] = f"{values['agreement']}\nRevisión salarial: {salary_revision}"
    values["contributions"] = "\n".join((
        f"Bases de cotización: {values['contribution_bases']}",
        f"Tipos de cotización: {values['contributions']}",
        f"Conceptos cotizables y exentos: {values['contribution_details']}",
        "Aportación empresarial al plan de pensiones de empleo simplificado: "
        f"{values['pension_contribution']}",
    ))
    values["calendar"] = "\n".join((
        values["calendar"],
        values["work_conditions"],
    ))
    return values


def substitute(source: str, values: dict[str, str]) -> str:
    """Sustituye marcadores conocidos sin evaluar expresiones ni ocultar errores de plantilla."""
    def replacement(match: re.Match[str]) -> str:
        """Exige una variable declarada para evitar documentos con campos sin resolver."""
        key = match[1].strip()
        if key not in values:
            raise ValueError(f"Variable desconocida en la biblioteca de justificación de precios: {key}")
        return values[key]

    return re.sub(r"\{\{([^{}]+)\}\}", replacement, source)


def document_blocks(source: str, values: dict[str, str], tables=None) -> list:
    """Reutiliza el Markdown del PPTP y separa las fórmulas escritas entre líneas $$."""
    source = document_markdown(source)
    parts = re.split(r"^\s*\$\$[ \t]*\r?$", source, flags=re.MULTILINE)
    if len(parts) % 2 == 0:
        raise ValueError("Hay una fórmula sin cerrar en la biblioteca: falta una línea $$.")
    blocks = []
    for index, part in enumerate(parts):
        if index % 2:
            formula = part.strip()
            if not formula or "\n" in formula:
                raise ValueError("Cada bloque $$ debe contener una fórmula en una sola línea.")
            expression, marker, key = formula.partition(" [#")
            if marker and re.fullmatch(r"\w+\]", key) is None:
                raise ValueError(f"Identificador de ecuación no válido: {key}")
            expression = substitute(expression, values)
            blocks.append((FORMULA_LEVEL, Equation(expression, key[:-1], 0) if marker else expression))
        else:
            for level, text in text_blocks(part, preserve_links=True, preserve_bold=True):
                marker = re.fullmatch(r"\{\{(\w+)\}\}", text)
                if marker and marker[1] == "tabla_cotizaciones":
                    continue
                if marker and tables and marker[1] in tables:
                    blocks.append((0, tables[marker[1]]))
                    continue
                if marker and marker[1] in _TABLES:
                    title, fields = _TABLES[marker[1]]
                    rows = tuple((label, _table_value(key, values[key]))
                                 for label, key in fields if values[key].strip())
                    if rows:
                        blocks.append((0, NarrativeTable(title, rows, marker[1])))
                    continue
                text = substitute(text, values)
                if text.strip():
                    blocks.append((level, text))
    return blocks


def _table_value(key: str, value: str) -> str:
    """Desglosa las referencias corridas manteniendo intactos los textos ya estructurados."""
    if "\n" in value:
        return value
    if key == "bases_tabla" and (match := re.fullmatch(
        r"base máxima:\s*(.+?)\.\s*Mínimas mensuales:\s*(.+?)\.\s*Obra,\s*(.+)", value, re.I,
    )):
        maximum, minimum, works = match.groups()
        groups = "\n".join(f"\t{group}" for group in minimum.split("; "))
        return f"Base máxima:\n\t{maximum}\nMínimas mensuales:\n{groups}\nObra:\n\t{works}"
    elif key == "tipos_tabla" and "empresa/trabajador — " in value:
        introduction, _, rates = value.partition("empresa/trabajador — ")
        parts = re.split(r";\s+|(?<=\.)\s+(?=Totales:)", rates)
        return introduction + "empresa/trabajador:\n" + "\n".join(f"• {part}" for part in parts)
    return value


def formula_runs(source: str) -> list[tuple[str, str]]:
    """Conserva las fórmulas como texto nativo con subíndices y exponentes editables."""
    result = []
    previous = 0
    pattern = r"_([A-Za-z0-9]+)|\^\(([^()]*)\)|\^([A-Za-z0-9]+)"
    for match in re.finditer(pattern, source):
        result.append((source[previous:match.start()], ""))
        result.append((match[1] or match[2] or match[3], "sub" if match[1] else "super"))
        previous = match.end()
    result.append((source[previous:], ""))
    return [(text, position) for text, position in result if text]
