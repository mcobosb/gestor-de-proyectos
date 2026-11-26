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

PAC_RULES = (
    (r"\b(?:estudio geotecnico|sondeo geotecnico|campana geotecnica)\w*\b", ("PAC-02",)),
    (r"\b(?:replanteo topografico|control topografico)\w*\b", ("PAC-04",)),
    (r"\b(?:gestion ambiental|rcd|residuos de construccion)\w*\b", ("PAC-05",)),
    (r"\b(?:excavacion|desmonte)\w*\b", ("PAC-06",)),
    (r"\bterraplen(?:es)?\b", ("PAC-07",)),
    (r"\bpedraplen\w*\b", ("PAC-08",)),
    (r"\b(?:relleno localizado|trasdos)\b", ("PAC-09",)),
    (r"\b(?:explanada|suelo estabilizado|estabilizacion in situ)\b", ("PAC-10",)),
    (r"\b(?:zapata|losa de cimentacion|cimentacion superficial)\w*\b", ("PAC-11",)),
    (r"\b(?:pilote|micropilote|muro pantalla|tablestaca|anclaje al terreno)\w*\b", ("PAC-12", "PAC-13", "PAC-14")),
    (r"\b(?:jet grouting|columna de grava|mejora del terreno)\w*\b", ("PAC-15",)),
    (r"\b(?:hormigon|hormigonado)\b", ("PAC-16", "PAC-17")),
    (r"\b(?:penetracion de agua|clase de exposicion)\w*\b", ("PAC-18",)),
    (r"\b(?:acero corrugado|armadura|malla electrosoldada)\w*\b", ("PAC-19",)),
    (r"\b(?:cemento|arido|aditivo para hormigon)\w*\b", ("PAC-20",)),
    (r"\b(?:encofrado|cimbra|ferrallado|recubrimiento de armadura)\w*\b", ("PAC-22",)),
    (r"\b(?:puesta en obra|vibrado|curado del hormigon)\w*\b", ("PAC-23",)),
    (r"\b(?:prefabricado de hormigon|placa alveolar|viga prefabricada)\w*\b", ("PAC-24",)),
    (r"\b(?:estructura metalica|soldadura|tornilleria estructural)\w*\b", ("PAC-25",)),
    (r"\b(?:estructura de madera|madera estructural)\w*\b", ("PAC-27",)),
    (r"\b(?:arido para firme|coeficiente los angeles|micro-deval|pulimento acelerado)\w*\b", ("PAC-29",)),
    (r"\b(?:zahorra|subbase)\w*\b", ("PAC-30",)),
    (r"\b(?:suelocemento|gravacemento)\w*\b", ("PAC-31",)),
    (r"\b(?:riego de imprimacion|riego de adherencia)\w*\b", ("PAC-32",)),
    (r"\b(?:mezcla bituminosa|aglomerado asfaltico|pavimento asfaltico)\w*\b", ("PAC-33", "PAC-34")),
    (r"\b(?:pavimento de hormigon|hormigon vibrado)\w*\b", ("PAC-35",)),
    (r"\b(?:regularidad superficial|iri|macrotextura|rozamiento transversal)\w*\b", ("PAC-36",)),
    (r"\b(?:marcas viales|pintura vial|senalizacion horizontal)\w*\b", ("PAC-42",)),
    (r"\b(?:saneamiento|colector|alcantarillado|pluviales)\w*\b", ("PAC-39",)),
    (r"\b(?:abastecimiento|tuberia de agua|red de agua potable)\w*\b", ("PAC-38",)),
    (r"\b(?:drenaje|cuneta|drenes)\w*\b", ("PAC-37",)),
    (r"\b(?:senalizacion vertical|senal vertical|cartel|balizamiento)\w*\b", ("PAC-43",)),
    (r"\b(?:barrera de seguridad|pretil|defensa metalica)\w*\b", ("PAC-44",)),
    (r"\b(?:adoquin|baldosa|bordillo|pavimento urbano|loseta|acera)\w*\b", ("PAC-50",)),
    (r"\b(?:alumbrado publico|luminaria|baculo)\w*\b", ("PAC-51",)),
    (r"\b(?:riego|jardineria|tierra vegetal)\w*\b", ("PAC-52",)),
    (r"\b(?:albanileria|fabrica de ladrillo|ladrillo|bloque)\w*\b", ("PAC-53",)),
    (r"\b(?:cubierta|impermeabilizacion)\w*\b", ("PAC-54",)),
    (r"\b(?:fachada|sate|aislamiento termico)\w*\b", ("PAC-55",)),
    (r"\b(?:carpinteria exterior|ventana|vidrio|barandilla|pasamanos|valla metalica de proteccion)\w*\b",
     ("PAC-56",)),
    (r"\b(?:fontaneria|acs|agua caliente sanitaria)\w*\b", ("PAC-57",)),
    (r"\b(?:climatizacion|ventilacion|conducto de aire)\w*\b", ("PAC-58",)),
    (r"\b(?:instalacion electrica|cuadro electrico|baja tension|alumbrado)\w*\b", ("PAC-59",)),
    (r"\b(?:proteccion contra incendios|pci|bie|rociador|sellado cortafuegos)\w*\b", ("PAC-60",)),
    (r"\b(?:ascensor|instalacion de gas|telecomunicaciones ict)\w*\b", ("PAC-61",)),
    (r"\b(?:fotovoltaica|panel solar)\w*\b", ("PAC-62",)),
    (r"\b(?:ensayo acustico|aislamiento acustico|ruido aereo|ruido de impacto)\w*\b", ("PAC-66",)),
    (r"\b(?:blower door|permeabilidad al aire|termografia envolvente)\w*\b", ("PAC-67",)),
    (r"\b(?:prueba de carga|carga estructural|puente)\w*\b", ("PAC-64",)),
)


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
    for pattern, identifiers in PAC_RULES:
        if re.search(pattern, text):
            trials.extend(identifiers)
    return list(dict.fromkeys(trials))


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
    if app.report_settings.currency != "EUR" and any(row.get("price_source") for row in valid):
        raise ValueError("Los precios BCCA están en euros; revisa la moneda y los precios antes de insertar.")
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
        description = f"{row['frequency']}. {row['criteria']}"
        if row.get("reference"):
            description += f" Referencia normativa: {row['reference']}."
        if row.get("details"):
            description += f" Detalle del control: {row['details']}."
        if row.get("price_source"):
            source = row["price_source"]
            description += (f" Precio seleccionado de {source['source']}: {source['code']} — "
                            f"{source['summary']} ({source['price']} EUR/{source['unit']}).")
        item = ProjectItem(f"CC-{next_code:03d}", chapter, "ud", row["name"], source=SOURCE,
                           text=description)
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
