"""Propuestas de ensayo, validación y traslado al presupuesto."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import re
import unicodedata

from budget_core import MeasurementLine, ProjectItem

SOURCE = "QUALITY:control"


@dataclass(frozen=True)
class Trial:
    """Criterio orientativo asociado a una clase de unidad de obra."""

    name: str
    frequency: str
    criteria: str


TRIALS = {
    "density": Trial("Densidad in situ", "1 cada 50 m de zanja y por tongada",
                     "Referencia: > 95 % del Proctor modificado; confirmar con el pliego."),
    "plate": Trial("Placa de carga", "1 por zona de calzada ampliada",
                   "Referencia EV₂/EV₁ < 2,2; confirmar con el pliego."),
    "concrete": Trial("Probetas y consistencia (cono de Abrams)", "1 amasada por elemento (orientativo)",
                      "Resistencia y consistencia según proyecto, lotes y Código Estructural."),
    "asphalt": Trial("Testigos de mezcla bituminosa (densidad y espesor)", "1 cada 500 m² o fracción",
                     "Espesor de proyecto y densidad según capa, fórmula de trabajo y pliego."),
    "paint": Trial("Retrorreflexión de marcas viales", "En cada paso o línea, según el plan de control",
                   "Según clase, proyecto y PG-3 aplicable."),
}


def normalized(text: str) -> str:
    """Normaliza variantes de acentuación y unidades de superficie."""
    return "".join(char for char in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(char))


def applicable_trials(item: ProjectItem) -> list[str]:
    """Detecta candidatos conservadores en la descripción; no certifica su aplicabilidad."""
    text = normalized(item.summary)
    if re.search(r"\b(?:demolicion|demoler|derribo|retirada|fresado|transporte|vertido)\b", text):
        return []
    trials = []
    if "zanja" in text and re.search(r"relleno|compact|terraplen", text):
        trials.append("density")
    if re.search(r"zahorra|subbase|explanada|terraplen", text):
        trials.append("plate")
    if re.search(r"\bhormigon\b", text) and not re.search(r"prefabricad|bordillo|baldosa", text):
        trials.append("concrete")
    if re.search(r"mezcla bituminosa|aglomerado asfaltico|pavimento asfaltico", text):
        trials.append("asphalt")
    if re.search(r"marca[s]? vial|pintura vial|senalizacion horizontal", text):
        trials.append("paint")
    return trials


def suggested_quantity(item: ProjectItem, trial: str) -> int | None:
    """Redondea por exceso solo si se conoce toda la base del cálculo."""
    if trial != "asphalt" or normalized(item.unit).replace(" ", "").replace("^", "") != "m2":
        return None
    measurement = Decimal(str(item.measurement))
    if not measurement.is_finite() or measurement <= 0:
        return None
    return int((measurement / 500).to_integral_value(rounding=ROUND_CEILING))


def work_units(items: list[ProjectItem]) -> dict[str, ProjectItem]:
    """Indexa partidas de obra por identidad persistente, sin incluir las de control generadas."""
    return {item.storage_id: item for item in items if item.source != SOURCE}


def unit_snapshot(item: ProjectItem) -> list:
    """Devuelve los datos cuya modificación requiere volver a revisar la UO."""
    return [item.code, item.chapter, item.summary, item.unit, item.measurement]


def validate_rows(rows: list[dict], items: dict[str, ProjectItem], *, require_prices: bool = False) -> list[dict]:
    """Valida cantidades y precios de los ensayos incluidos sin modificar el plan."""
    if not rows:
        raise ValueError("Incluye al menos un ensayo revisado.")
    valid = []
    for row in rows:
        item = items.get(row["unit_id"])
        if item is None or row["snapshot"] != unit_snapshot(item):
            raise ValueError("Ha cambiado una unidad de obra. Actualiza y revisa el plan.")
        name, frequency, criteria = (str(row[key]).strip() for key in ("name", "frequency", "criteria"))
        if not all((name, frequency, criteria)):
            raise ValueError(f"{item.code}: completa el ensayo, la frecuencia y el criterio de aceptación.")
        try:
            count = Decimal(str(row["count"]).strip())
            text = str(row["price"]).strip()
            price = Decimal(text.replace(",", ".")) if text else None
        except InvalidOperation as exc:
            raise ValueError(f"{item.code}: introduce un número de ensayos y un precio válidos.") from exc
        if not count.is_finite() or count < 1 or count > 999999 or count != count.to_integral_value():
            raise ValueError(f"{item.code}: el número de ensayos debe ser un entero positivo (máx. 999 999).")
        if price is not None and (not price.is_finite() or price <= 0 or price > 1_000_000_000):
            raise ValueError(f"{item.code}: el precio unitario debe ser positivo y válido.")
        if require_prices and price is None:
            raise ValueError(f"{item.code}: falta el precio unitario de «{name}».")
        valid.append(row | {"name": name, "frequency": frequency, "criteria": criteria,
                            "count": int(count), "price": str(price) if price is not None else ""})
    return valid


def has_inserted(items: list[ProjectItem]) -> bool:
    """Identifica el capítulo generado por las partidas realmente presentes."""
    return any(item.source == SOURCE for item in items)


def insert_budget(app, rows: list[dict]) -> None:
    """Incorpora una partida por ensayo y UO, con código único y precio revisado."""
    if has_inserted(app.budget_items):
        raise ValueError("El plan ya se ha incorporado al presupuesto.")
    units = work_units(app.budget_items)
    valid = validate_rows(rows, units, require_prices=True)
    chapters = set(app.chapter_order) | set(app.chapter_titles) | {item.chapter for item in app.budget_items}
    matching = [chapter for chapter in chapters if re.search(
        r"\b(?:control|aseguramiento)(?:\s+y\s+control)?\s+de\s+(?:la\s+)?calidad\b",
        normalized(app._chapter_title(chapter)),
    )]
    if len(matching) > 1:
        raise ValueError("Hay varios capítulos de control de calidad. Deja un único capítulo de destino.")
    chapter = matching[0] if matching else "CALIDAD#"
    suffix = 1
    while not matching and chapter in chapters:
        suffix += 1
        chapter = f"CALIDAD{suffix}#"
    codes = {item.code for item in app.budget_items}
    pending = []
    next_code = 1
    for row in valid:
        while f"CC-{next_code:03d}" in codes:
            next_code += 1
        item = ProjectItem(f"CC-{next_code:03d}", chapter, "ud", row["name"], source=SOURCE,
                           text=f"{row['frequency']}. {row['criteria']}")
        next_code += 1
        source = units[row["unit_id"]]
        item.measurement_lines = [MeasurementLine(comment=f"UO {source.code}: {source.summary}", units=row["count"])]
        item.recalculate_measurement()
        item.set_indirect_costs(app.report_settings.indirect_costs, float(row["price"]))
        pending.append(item)
    app._ensure_chapter(chapter)
    if not matching:
        app.chapter_titles[chapter] = "Control de Calidad"
    position = max((index + 1 for index, item in enumerate(app.budget_items)
                    if item.chapter == chapter), default=len(app.budget_items))
    app.budget_items[position:position] = pending
    app.refresh_budget_tree()
    app._mark_dirty()
