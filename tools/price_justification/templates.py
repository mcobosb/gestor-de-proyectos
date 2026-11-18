"""Documentos base editables y variables enlazadas a la configuración del proyecto."""

from datetime import date
from pathlib import Path
import re

from tools._library_editor import library_members, read_markdown
from tools._library_markdown import document_markdown, text_blocks
from tools._shared.document_layout import project_document_variables


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
        values[key] = value or f"[PENDIENTE EN CONFIGURACIÓN GENERAL: {key}]"
    values.update({
        "fecha_referencia": state.get("reference_date") or date.today().isoformat(),
        "porcentaje_indirectos": fmt_number(app.report_settings.indirect_costs),
        "moneda": currency_label(app.report_settings.currency, app.report_settings.custom_currency),
        "normativa_contratacion": (
            "Ley 9/2017, de 8 de noviembre, de Contratos del Sector Público, y Reglamento general "
            "de la Ley de Contratos de las Administraciones Públicas, aprobado por Real Decreto "
            "1098/2001, de 12 de octubre, en lo vigente y aplicable."
            if state.get("contract_type", "public") == "public" else
            "En contratación privada se atenderá al contrato y a la normativa aplicable; "
            "los criterios siguientes se utilizan como referencia técnica de composición de costes."
        ),
        "referencia_composicion": (
            "De acuerdo con el artículo 130 del Reglamento general de la Ley de Contratos "
            "de las Administraciones Públicas"
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


def document_blocks(source: str, values: dict[str, str]) -> list[tuple[int, str]]:
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
            blocks.append((FORMULA_LEVEL, substitute(formula, values)))
        else:
            for level, text in text_blocks(part):
                text = substitute(text, values)
                if text.strip():
                    blocks.append((level, text))
    return blocks


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
