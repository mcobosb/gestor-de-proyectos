"""Estimación revisable de RCDs basada en las hojas Resumen, Lista MAM y Presupuesto."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import os
from pathlib import Path
import re
import tempfile
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING
import unicodedata

from localization import filedialog, localize_widget_tree, messagebox

if TYPE_CHECKING:
    from bc3_gui import BudgetApp, ProjectItem


# Porcentajes y densidades del modelo, no tasas de generación medidas en la obra.
GROUPS = (
    ("earth", "Tierras y pétreos de excavación", "earth", 0, 1.5),
    ("asphalt", "Asfalto", "nonstone", 5, 1.3),
    ("wood", "Madera", "nonstone", 2.5, 0.6),
    ("metals", "Metales", "nonstone", 4, 1.5),
    ("paper", "Papel", "nonstone", 3, 0.9),
    ("plastic", "Plástico", "nonstone", 1.5, 0.9),
    ("glass", "Vidrio", "nonstone", 0.5, 1.5),
    ("gypsum", "Yeso", "nonstone", 10, 1.2),
    ("aggregates", "Arena, grava y otros áridos", "stone", 4, 1.5),
    ("concrete", "Hormigón", "stone", 12, 1.5),
    ("ceramics", "Ladrillos, azulejos y cerámicos", "stone", 54, 1.5),
    ("mixed", "Piedra / RCDs mezclados", "stone", 5, 1.5),
    ("rubbish", "Basuras", "hazardous", 7, 0.9),
    ("hazardous", "Potencialmente peligrosos y otros", "hazardous", 4, 0.5),
)

# Se conserva la identificación por fila: el ODS repite algunos códigos LER.
WASTES = (
    ("5", "earth", "17 05 04", "Tierras y piedras no contaminadas", r"excav|tierras|desmonte"),
    ("6", "earth", "17 05 06", "Lodos de drenaje no peligrosos", r"lodos?.*drenaje"),
    ("7", "earth", "17 05 08", "Balasto de vías férreas no peligroso", r"balast[or]"),
    ("13", "asphalt", "17 03 02", "Mezclas bituminosas no peligrosas", r"asfalt|bituminos|fresado"),
    ("15", "wood", "17 02 01", "Madera", r"madera"),
    ("17", "metals", "17 04 01", "Cobre, bronce y latón", r"cobre|bronce|laton"),
    ("18", "metals", "17 04 02", "Aluminio", r"aluminio"),
    ("19", "metals", "17 04 03", "Plomo", r"plomo"),
    ("20", "metals", "17 04 04", "Zinc", r"\bzinc\b"),
    ("21", "metals", "17 04 05", "Hierro y acero", r"hierro|acero|ferralla"),
    ("22", "metals", "17 04 06", "Estaño", r"estano"),
    ("23", "metals", "17 04 06", "Metales mezclados (código repetido en ODS)", r"metales mezclados"),
    ("24", "metals", "17 04 11", "Cables no peligrosos", r"cables?"),
    ("26", "paper", "20 01 01", "Papel", r"papel|carton"),
    ("28", "plastic", "17 02 03", "Plástico", r"plastic|pvc|polietileno"),
    ("30", "glass", "17 02 02", "Vidrio", r"vidrio|cristal"),
    ("32", "gypsum", "17 08 02", "Materiales a partir de yeso no peligrosos", r"yeso|escayola"),
    ("37", "aggregates", "01 04 08", "Grava y rocas trituradas no peligrosas", r"grava|aridos?|zahorra"),
    ("38", "aggregates", "01 04 09", "Arena y arcilla", r"arena|arcilla"),
    ("40", "concrete", "17 01 01", "Hormigón", r"hormigon"),
    ("42", "ceramics", "17 01 02", "Ladrillos", r"ladrill"),
    ("43", "ceramics", "17 01 03", "Tejas y materiales cerámicos", r"tejas?|ceramic|azulejo|baldosa"),
    ("44", "ceramics", "17 01 07", "Mezclas de hormigón y cerámicos no peligrosas", r"mezclas?.*(hormigon|ceramic)"),
    ("46", "mixed", "17 09 04", "RCDs mezclados no peligrosos", r"rcds?.*mezcl|escombro|piedra|petre"),
    ("51", "rubbish", "20 02 01", "Residuos biodegradables", r"vegetal|poda|biodegrad"),
    ("52", "rubbish", "20 03 01", "Residuos municipales mezclados", r"basura|municipales"),
    ("55", "hazardous", "17 01 06", "Hormigón y cerámicos con sustancias peligrosas", r"hormigon.*(contamin|peligros)"),
    ("56", "hazardous", "17 02 04", "Madera, vidrio o plástico contaminados", r"(madera|vidrio|plastic).*(contamin|peligros)"),
    ("57", "hazardous", "17 03 01", "Mezclas bituminosas con alquitrán de hulla", r"bituminos.*hulla"),
    ("58", "hazardous", "17 03 03", "Alquitrán de hulla", r"alquitran.*hulla"),
    ("59", "hazardous", "17 04 09", "Residuos metálicos contaminados", r"metal.*contamin"),
    ("60", "hazardous", "17 04 10", "Cables con sustancias peligrosas", r"cables?.*(hidrocarb|alquitran|peligros)"),
    ("61", "hazardous", "17 06 01", "Aislamiento con amianto", r"aislamiento.*amianto"),
    ("62", "hazardous", "17 06 03", "Otros aislamientos con sustancias peligrosas", r"aislamiento.*peligros"),
    ("63", "hazardous", "17 06 05", "Materiales de construcción con amianto", r"amianto|fibrocement"),
    ("64", "hazardous", "17 08 01", "Yeso contaminado", r"yeso.*contamin"),
    ("65", "hazardous", "17 09 01", "RCDs con mercurio", r"mercurio"),
    ("66", "hazardous", "17 09 02", "RCDs con PCB", r"\bpcb"),
    ("67", "hazardous", "17 09 03", "Otros RCDs con sustancias peligrosas", r"residuos?.*peligros"),
    ("68", "hazardous", "17 06 04", "Aislamientos no peligrosos", r"aislamiento(?!.*(amianto|peligros))"),
    ("69", "hazardous", "17 05 03", "Tierras y piedras contaminadas", r"(tierras|piedras).*(contamin|peligros)"),
    ("70", "hazardous", "17 05 05", "Lodos de drenaje peligrosos", r"lodos?.*(contamin|peligros)"),
    ("71", "hazardous", "17 05 07", "Balasto con sustancias peligrosas", r"balast[or].*(contamin|peligros)"),
    ("72", "hazardous", "15 02 02", "Absorbentes contaminados", r"absorbente|trapos?.*contamin"),
    ("73", "hazardous", "13 02 05", "Aceites usados", r"aceites?.*usad"),
    ("74", "hazardous", "16 01 07", "Filtros de aceite", r"filtros?.*aceite"),
    ("75", "hazardous", "20 01 21", "Tubos fluorescentes", r"fluorescente"),
    ("76", "hazardous", "16 06 04", "Pilas alcalinas y salinas", r"pilas?.*(alcalina|salina)"),
    ("77", "hazardous", "16 06 03", "Pilas botón", r"pilas?.*boton"),
    ("78", "hazardous", "15 01 10", "Envases contaminados", r"envases?.*contamin"),
    ("79", "hazardous", "08 01 11", "Sobrantes de pintura o barniz", r"(sobrante|residuo).*pintura|barniz"),
    ("80", "hazardous", "14 06 03", "Disolventes no halogenados", r"disolvente"),
    ("81", "hazardous", "07 07 01", "Sobrantes de desencofrantes", r"desencofrante"),
    ("82", "hazardous", "15 01 11", "Aerosoles vacíos", r"aerosol"),
    ("83", "hazardous", "16 06 01", "Baterías de plomo", r"baterias?.*plomo"),
    ("84", "hazardous", "13 07 03", "Hidrocarburos con agua", r"hidrocarburos?.*agua"),
    ("85", "hazardous", "17 09 04", "Otros RCDs mezclados (grupo otros del ODS)", ""),
)

INPUTS = (
    ("surface", "Superficie afectada (m²)", None),
    ("volume_factor", "Volumen de residuos por superficie (m³/m²)", 0.15),
    ("density", "Densidad global (t/m³)", 1.5),
    ("earth_volume", "Volumen de excavación (m³)", None),
    ("budget", "Presupuesto sin gestión de residuos (€)", None),
    ("earth_budget", "Movimiento de tierras / trabajos previos (€)", None),
    ("earth_rate", "Gestión Nivel I (€/m³)", 5.9),
    ("stone_rate", "Gestión pétreos (€/m³)", 8.61),
    ("nonstone_rate", "Gestión no pétreos (€/m³)", 21.13),
    ("hazardous_rate", "Gestión peligrosos y otros (€/m³)", 22.25),
    ("level1_extra", "B1. Complemento Nivel I (€), manual", 0),
    ("level2_min", "B2. Mínimo Nivel II (%), criterio del modelo", 0.4),
    ("management", "B3. Gestión, alquileres, etc. (%)", 0.1),
)
HEADERS = ("Tipología RCDs", "Estimación volumen (m³)", "Precio gestión (€/m³)",
           "Importe (€)", "% presupuesto de obra")
MODEL_INPUTS = {"surface", "earth_volume", "earth_budget", "density", "volume_factor"}
NOTE = (
    "Estimación orientativa, sin fianza ni IVA. Modelo revisado a partir del ODS aportado: "
    "Nivel II mínimo configurable (inicial 0,4 %), B1 manual y total de todas las filas. "
    "Los porcentajes no se renormalizan al marcar residuos. No acredita cumplimiento normativo "
    "ni sustituye la caracterización del residuo; revisar los códigos LER y los peligrosos."
)


def normalize(text: str) -> str:
    """Prepara textos para comparar sin tildes ni diferencias de mayúsculas."""
    return "".join(char for char in unicodedata.normalize("NFKD", text.lower())
                   if not unicodedata.combining(char))


def number(value: object, label: str) -> float:
    """Valida magnitudes no negativas con coma decimal y sin valores no finitos."""
    if isinstance(value, bool):
        raise ValueError(f"{label}: indica un número válido.")
    text = str(value).strip().replace(" ", "")
    if "," in text and "." in text:
        separator = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if separator == "," else ",", "").replace(separator, ".")
    else:
        text = text.replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{label}: indica un número válido.") from exc
    if not result.is_finite() or not 0 <= result <= Decimal("1e12"):
        raise ValueError(f"{label}: valor fuera de rango.")
    return float(result)


def money(value: Decimal) -> Decimal:
    """Redondea con el criterio comercial del presupuesto."""
    with localcontext() as context:
        context.prec = max(28, value.adjusted() + 4)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class DemolitionMeasurement:
    key: str
    source: str
    unit: str
    quantity: float
    group: str = ""
    factor: float | None = None
    included: bool = True
    waste_key: str = ""

    @classmethod
    def from_dict(cls, data: object) -> DemolitionMeasurement:
        """Valida una medición revisada, incluidos descuentos y conversiones."""
        if not isinstance(data, dict):
            raise ValueError("Medición de demolición no válida.")
        for key in ("key", "source", "unit", "group"):
            if not isinstance(data.get(key), str):
                raise ValueError("Procedencia o material de demolición no válido.")
        quantity = data.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise ValueError("Cantidad de demolición no válida.")
        number(abs(quantity), "Medición de demolición")
        included = data.get("included", True)
        if not isinstance(included, bool):
            raise ValueError("Selección de demolición no válida.")
        factor = data.get("factor")
        if factor is not None:
            factor = number(factor, "Factor de conversión a m³")
        group = data["group"]
        if group and group not in {row[0] for row in GROUPS}:
            raise ValueError("Material de demolición no válido.")
        waste_key = data.get("waste_key", "")
        if not isinstance(waste_key, str) or (waste_key and not any(
            row[0] == waste_key and row[1] == group for row in WASTES
        )):
            raise ValueError("Residuo de demolición no válido.")
        if included and (not group or not waste_key or factor is None):
            raise ValueError(f"Indica material y factor a m³: {data['source']}.")
        if normalize(data["unit"]).replace("³", "3").replace("^", "").replace(" ", "") == "m3":
            if included and factor != 1:
                raise ValueError("Las mediciones en m³ se copian directamente con factor 1.")
        return cls(data["key"], data["source"], data["unit"], quantity, group, factor, included, waste_key)

    @property
    def volume(self) -> Decimal:
        """Obtiene el volumen con el mismo redondeo que las líneas de transporte."""
        return money(Decimal(str(self.quantity)) * Decimal(str(self.factor or 0)))


@dataclass
class RcdState:
    inputs: dict[str, float | None] = field(default_factory=lambda: {
        key: default for key, _, default in INPUTS
    })
    groups: dict[str, dict[str, float]] = field(default_factory=lambda: {
        key: {"percentage": percentage, "density": density}
        for key, _, _, percentage, density in GROUPS
    })
    selected: dict[str, bool] = field(default_factory=lambda: {key: False for key, *_ in WASTES})
    evidence: dict[str, list[str]] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    demolition: list[DemolitionMeasurement] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializa las estimaciones, las marcas manuales y su procedencia."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> RcdState:
        """Restaura un estado antiguo o valida todos los valores antes de usarlo."""
        if not isinstance(data, dict):
            raise ValueError("Los datos RCDs deben ser un objeto JSON.")
        state = cls()
        for section in ("inputs", "groups", "selected", "evidence", "sources"):
            if not isinstance(data.get(section, {}), dict):
                raise ValueError(f"Sección RCDs no válida: {section}.")
        for key, label, _ in INPUTS:
            if key in data.get("inputs", {}):
                value = data["inputs"][key]
                state.inputs[key] = None if value is None else number(value, label)
        for key, label, *_ in GROUPS:
            values = data.get("groups", {}).get(key, state.groups[key])
            if not isinstance(values, dict):
                raise ValueError(f"Grupo no válido: {label}.")
            for name in ("percentage", "density"):
                state.groups[key][name] = number(values.get(name, state.groups[key][name]), label)
            if state.groups[key]["density"] <= 0 or state.groups[key]["percentage"] > 100:
                raise ValueError(f"Densidad o porcentaje no válido: {label}.")
        for key, *_ in WASTES:
            value = data.get("selected", {}).get(key, False)
            if not isinstance(value, bool):
                raise ValueError("La selección RCDs debe contener valores booleanos.")
            state.selected[key] = value
            matches = data.get("evidence", {}).get(key, [])
            if not isinstance(matches, list) or any(not isinstance(item, str) for item in matches):
                raise ValueError("Evidencias RCDs no válidas.")
            if key in data.get("evidence", {}):
                state.evidence[key] = matches
        for key, value in data.get("sources", {}).items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("Procedencia RCDs no válida.")
            state.sources[key] = value
        for key in ("level2_min", "management"):
            if state.inputs[key] is not None and state.inputs[key] > 100:
                raise ValueError("Los porcentajes de gestión deben estar entre 0 y 100.")
        demolition = data.get("demolition", [])
        if not isinstance(demolition, list):
            raise ValueError("Las mediciones de demolición deben ser una lista.")
        state.demolition = [DemolitionMeasurement.from_dict(row) for row in demolition]
        if state.demolition and not any(row.included for row in state.demolition):
            raise ValueError("Selecciona al menos una medición de demolición.")
        if len({row.key for row in state.demolition}) != len(state.demolition):
            raise ValueError("Mediciones de demolición duplicadas.")
        demolition_volumes(state.demolition)
        return state


def demolition_volumes(rows: list[DemolitionMeasurement]) -> dict[str, Decimal]:
    """Agrupa volúmenes revisados sin permitir totales negativos por material."""
    volumes = {key: Decimal(0) for key, *_ in GROUPS}
    waste_volumes = {}
    for row in rows:
        if row.included:
            volumes[row.group] += row.volume
            waste_volumes[row.waste_key] = waste_volumes.get(row.waste_key, Decimal(0)) + row.volume
    for key, volume in waste_volumes.items():
        number(volume, f"Volumen del residuo {key}")
    for key, volume in volumes.items():
        number(volume, f"Volumen de {key}")
    return volumes


def demolition_measurements(app: BudgetApp, state: RcdState) -> list[DemolitionMeasurement]:
    """Recoge mediciones originales de demolición sin incluir transportes ni subtotales."""
    from bc3_gui import MeasurementLine

    previous = {row.key: row for row in state.demolition}
    rows = []
    occurrences = {}
    for item in app.budget_items:
        summary = normalize(item.summary)
        demolition = re.search(r"demoli|derrib|desmont|desmantel|levantad|levantamiento|retirada|picad|fresad", summary)
        transport = re.search(r"transport|gestion.*residu|tratamiento.*residu|canon.*vertedero", summary)
        if transport and (not demolition or transport.start() < demolition.start()):
            continue
        if not demolition and not re.search(r"demoli|derrib|desmont|desmantel",
                                           normalize(app._chapter_title(item.chapter))):
            continue
        identity = f"{item.chapter}|{item.code}"
        occurrences[identity] = occurrences.get(identity, 0) + 1
        lines = item.measurement_lines or [MeasurementLine(units=item.measurement)]
        probe = RcdState()
        detect_waste_items(probe, [item])
        matches = [(key, group) for key, group, *_ in WASTES if probe.selected[key]]
        waste_key, group = matches[0] if len(matches) == 1 else ("", "")
        unit = normalize(item.unit).replace("³", "3").replace("^", "").replace(" ", "")
        for index, line in enumerate(lines):
            if line.is_subtotal:
                continue
            key = f"{identity}|{occurrences[identity]}|{index}"
            old = previous.get(key)
            rows.append(DemolitionMeasurement(
                key, f"{item.code}: {item.summary}" + (f" — {line.comment}" if line.comment else ""),
                item.unit, line.partial, old.group if old else group,
                1.0 if unit == "m3" else old.factor if old and old.unit == item.unit else None,
                old.included if old else True,
                old.waste_key if old else waste_key,
            ))
    return rows


TRANSPORT_PREFIX = "[RCD demolición] "
TRANSPORT_REVIEW = (
    "Partida creada automáticamente por RCDs. REVISIÓN PENDIENTE: coste/precio unitario, "
    "rendimiento, descomposición, distancia, esponjamiento y condiciones de transporte y vertido. "
    "El precio inicial es 0; no constituye un precio válido de presupuesto."
)
MEASURED_NOTE = (
    "Volúmenes obtenidos de las mediciones de demolición revisadas, no de porcentajes del ODS. "
    "La superficie, el coeficiente de generación y la densidad global no intervienen en este modo. "
    "Se mantiene el volumen de excavación salvo que se hayan revisado mediciones del grupo tierras. "
    "Las marcas X permiten excluir residuos del cálculo RCDs, "
    "sin modificar las mediciones copiadas al transporte."
)


def transfer_demolition(app: BudgetApp, state: RcdState,
                        rows: list[DemolitionMeasurement], target: ProjectItem | None) -> bool:
    """Sincroniza solo las líneas generadas, conservando las mediciones y precios manuales."""
    from bc3_gui import MeasurementLine, ProjectItem

    checked = [DemolitionMeasurement.from_dict(asdict(row)) for row in rows]
    if len({row.key for row in checked}) != len(checked):
        raise ValueError("Mediciones de demolición duplicadas.")
    if not any(row.included for row in checked):
        raise ValueError("Selecciona al menos una medición de demolición.")
    demolition_volumes(checked)
    if target is not None:
        if not any(item is target for item in app.budget_items):
            raise ValueError("La partida de transporte ya no pertenece al presupuesto.")
        unit = normalize(target.unit).replace("³", "3").replace("^", "").replace(" ", "")
        if unit != "m3":
            raise ValueError("La partida de transporte debe estar medida en m³.")
    created = target is None
    if target is None:
        code = "RCD-TRANS"
        suffix = 1
        codes = {item.code for item in app.budget_items}
        while code in codes:
            suffix += 1
            code = f"RCD-TRANS-{suffix}"
        chapter = next((chapter for chapter in app.chapter_order
                        if re.search(r"residu|vertedero", normalize(app._chapter_title(chapter)))),
                       app.budget_items[-1].chapter)
        target = ProjectItem(code, chapter, "m³",
                             "TRANSPORTE DE RESIDUOS DE DEMOLICIÓN A VERTEDERO (REVISAR COSTES Y RENDIMIENTO)",
                             measurement=0, text=TRANSPORT_REVIEW.upper())
        target.set_indirect_costs(app.report_settings.indirect_costs)
    manual = [deepcopy(line) for line in target.measurement_lines
              if not line.comment.startswith(TRANSPORT_PREFIX)]
    if not target.measurement_lines and target.measurement:
        manual.append(MeasurementLine(comment="Medición previa de transporte", units=target.measurement))
    names = {key: label for key, label, *_ in GROUPS}
    generated = [
        MeasurementLine(comment=f"{TRANSPORT_PREFIX}{row.source} [{names[row.group]}; "
                        f"{row.quantity:g} {row.unit} × {row.factor:g} m³/{row.unit}]",
                        units=float(row.volume))
        for row in checked if row.included
    ]
    target.measurement_lines = manual + generated
    target.recalculate_measurement()
    for item in app.budget_items:
        if item is not target and any(line.comment.startswith(TRANSPORT_PREFIX)
                                      for line in item.measurement_lines):
            item.measurement_lines = [line for line in item.measurement_lines
                                      if not line.comment.startswith(TRANSPORT_PREFIX)]
            item.measurement = 0
            item.measurement_detail = ""
            item.recalculate_measurement()
    if created:
        app.budget_items.insert(app._end_of_chapter(target.chapter), target)
        app._ensure_chapter(target.chapter)
    state.demolition = checked
    for key, *_ in WASTES:
        evidence = [text for text in state.evidence.get(key, []) if not text.startswith(TRANSPORT_PREFIX)]
        evidence.extend(TRANSPORT_PREFIX + row.source for row in checked if row.included and row.waste_key == key)
        state.evidence[key] = evidence
        if any(row.included and row.waste_key == key for row in checked):
            state.selected[key] = True
    app._select_budget_item(target)
    return created


def initial_state(app: BudgetApp) -> RcdState:
    """Recupera magnitudes inequívocas y deja pendientes superficie y datos desconocidos."""
    state = RcdState()
    budget = Decimal(0)
    earth_budget = Decimal(0)
    earth_volume = Decimal(0)
    earth_codes = []
    waste_codes = []
    for item in app.budget_items:
        text = normalize(f"{item.summary} {item.text} {app._chapter_title(item.chapter)}")
        if re.search(r"gestion.*residu|tratamiento.*residu|canon.*vertedero", text):
            waste_codes.append(item.code)
            continue
        budget += Decimal(str(item.import_total))
        if re.search(r"excav|movimiento de tierras|desmonte|trabajos previos", text):
            earth_budget += Decimal(str(item.import_total))
        unit = normalize(item.unit).replace("³", "3").replace("^", "").replace(" ", "")
        if unit == "m3" and re.search(r"excav|desmonte", normalize(item.summary)) and not re.search(
            r"transporte|relleno|carga", normalize(item.summary)
        ):
            earth_volume += Decimal(str(item.measurement))
            earth_codes.append(item.code)
    state.inputs["budget"] = number(budget, "Presupuesto")
    state.sources["budget"] = (
        "PEM de partidas; exclusión orientativa de gestión de residuos: " + ", ".join(waste_codes)
        if waste_codes else "Suma del PEM. Revisa si hay gestión de residuos no identificada."
    )
    if earth_codes:
        state.inputs["earth_volume"] = number(earth_volume, "Excavación")
        state.sources["earth_volume"] = "Suma de partidas m³ de excavación: " + ", ".join(earth_codes)
    if earth_budget:
        state.inputs["earth_budget"] = number(earth_budget, "Movimiento de tierras")
        state.sources["earth_budget"] = "Estimación por descripciones y capítulos; revisar."
    state.sources["surface"] = "Pendiente: no se suman superficies de partidas que pueden solaparse."
    detect_wastes(state, app)
    return state


def detect_wastes(state: RcdState, app: BudgetApp) -> None:
    """Marca coincidencias LER o materiales en partidas de retirada/demolición, no suministros."""
    detect_waste_items(state, app.budget_items)


def detect_waste_items(state: RcdState, items: list[ProjectItem]) -> None:
    """Conserva coincidencias por residuo para un conjunto concreto de partidas."""
    for key, group, code, _, pattern in WASTES:
        matches = []
        code_pattern = r"(?<!\d)" + r"[\s.\-]*".join(re.findall(r"\d{2}", code)) + r"(?!\d)"
        for item in items:
            text = normalize(f"{item.summary} {item.text}")
            code_text = re.sub(r"(?:distint[oa]s?|excepto|excluid[oa]s?).*?(?:[;]|$)", "", text)
            material_text = re.sub(
                r"\b(?:no|sin)\s+(?:contaminad\w*|sustancias peligrosas|amianto|peligros\w*)",
                "", code_text,
            )
            context = re.search(r"demoli|derrib|retirada|residuo|desmont|desmantel|excav|fresad|levantad|levantamiento|picad", text)
            # La fila 85 repite el LER de la 46 en otro grupo; requiere selección manual.
            explicit = key != "85" and re.search(code_pattern, code_text)
            material = pattern and context and re.search(pattern, material_text)
            if group != "hazardous" and re.search(r"amianto|contaminad|alquitran de hulla", material_text):
                material = False
            if explicit or material:
                matches.append(f"{item.code}: {item.summary}" + (" [LER]" if explicit else " [texto, revisar]"))
        state.evidence[key] = matches
        state.selected[key] = bool(matches)


@dataclass(frozen=True)
class RcdBudgetLine:
    key: str
    label: str
    volume: Decimal | None
    rate: Decimal | None
    cost: Decimal


def budget_calculation(state: RcdState) -> tuple[list[RcdBudgetLine], list[list[str]]]:
    """Calcula magnitudes económicas sin convertir los números en texto de presentación."""
    from bc3_gui import fmt_number

    state = RcdState.from_dict(state.to_dict())
    values = {}
    for key, label, _ in INPUTS:
        raw = state.inputs[key]
        if raw is None:
            if state.demolition and key in MODEL_INPUTS:
                raw = 0
            else:
                raise ValueError(f"Completa el campo {label}; indica 0 si no procede.")
        values[key] = Decimal(str(number(raw, label)))
    if (not state.demolition and values["density"] <= 0) or values["budget"] <= 0:
        raise ValueError("La densidad global y el presupuesto deben ser mayores que cero.")
    mass = values["surface"] * values["volume_factor"] * values["density"]
    measured = demolition_volumes([
        row for row in state.demolition if row.included and state.selected[row.waste_key]
    ]) if state.demolition else None
    volumes = {category: Decimal(0) for category in ("earth", "stone", "nonstone", "hazardous")}
    details = []
    for key, label, category, *_ in GROUPS:
        enabled = any(state.selected[row[0]] for row in WASTES if row[1] == key)
        fraction = Decimal(str(state.groups[key]["percentage"])) / 100
        density = Decimal(str(state.groups[key]["density"]))
        weight = mass * fraction if enabled else Decimal(0)
        volume = weight / density
        if key == "earth":
            volume = values["earth_volume"] if enabled else Decimal(0)
            weight = volume * density
        if measured is not None and (key != "earth" or any(
            row.included and row.group == "earth" for row in state.demolition
        )):
            volume = measured[key] if enabled else Decimal(0)
            weight = volume * density
        volumes[category] += volume
        details.append([label, "X" if enabled else "", fmt_number(float(weight)),
                        fmt_number(float(density)), fmt_number(float(volume))])
    budget = values["budget"]

    costs = {}
    rows = []
    for category, label in (
        ("earth", "Tierras y pétreos de la excavación"),
        ("stone", "RCDs naturaleza pétrea"), ("nonstone", "RCDs naturaleza no pétrea"),
        ("hazardous", "RCDs potencialmente peligrosos y otros"),
    ):
        volume = money(volumes[category])
        rate = money(values[f"{category}_rate"])
        costs[category] = money(volume * rate)
        rows.append(RcdBudgetLine(category, label, volume, rate, costs[category]))
    level2 = costs["stone"] + costs["nonstone"] + costs["hazardous"]
    b1 = money(values["level1_extra"])
    b2 = money(max(Decimal(0), money(budget * values["level2_min"] / 100) - level2))
    b3 = money(budget * values["management"] / 100)
    rows.extend([
        RcdBudgetLine("b1", "B1. Complemento Nivel I (manual)", None, None, b1),
        RcdBudgetLine("b2", "B2. Hasta cubrir el mínimo Nivel II", None, None, b2),
        RcdBudgetLine("b3", "B3. Gestión, alquileres, etc.", None, None, b3),
    ])
    return rows, details


def calculate(state: RcdState) -> tuple[list[tuple[str, list[list[str]]]], list[list[str]]]:
    """Presenta los mismos importes del presupuesto en la vista previa y los informes."""
    from bc3_gui import fmt_number

    state = RcdState.from_dict(state.to_dict())
    lines, details = budget_calculation(state)
    budget = Decimal(str(state.inputs["budget"]))

    def render(line: RcdBudgetLine) -> list[str]:
        """Forma una fila económica con los mismos campos que la hoja Presupuesto."""
        return [line.label, "" if line.volume is None else fmt_number(float(line.volume)),
                "" if line.rate is None else fmt_number(float(line.rate)),
                fmt_number(float(line.cost)), fmt_number(float(line.cost / budget * 100), 4)]

    rows = [render(line) for line in lines]
    total = RcdBudgetLine("total", "TOTAL (sin fianza)", None, None,
                         sum((line.cost for line in lines), Decimal(0)))
    sections = [
        ("A1. RCDs Nivel I", rows[:1]),
        ("A2. RCDs Nivel II", rows[1:4]),
        ("B. Resto de costes de gestión", rows[4:]),
        ("TOTAL PRESUPUESTO PLAN GESTIÓN RCDs", [render(total)]),
    ]
    return sections, details


RCD_BUDGET_SOURCE = "RCD:"


def synchronize_budget(app: BudgetApp, state: RcdState) -> None:
    """Crea o actualiza solo las siete partidas económicas generadas por RCDs."""
    from bc3_gui import MeasurementLine, ProjectItem

    lines, _ = budget_calculation(state)
    generated = {line.key: next((item for item in app.budget_items
                                if item.source == RCD_BUDGET_SOURCE + line.key), None)
                 for line in lines}
    existing = next((item for item in generated.values() if item is not None), None)
    chapter = existing.chapter if existing is not None else "RCD#"
    if existing is None:
        used = set(app.chapter_order) | set(app.chapter_titles) | {item.chapter for item in app.budget_items}
        suffix = 1
        while chapter in used:
            suffix += 1
            chapter = f"RCD{suffix}#"
    pending = []
    codes = {item.code for item in app.budget_items}
    for line in lines:
        item = generated[line.key]
        if item is None:
            code = f"RCD-{line.key.upper()}"
            suffix = 1
            while code in codes:
                suffix += 1
                code = f"RCD-{line.key.upper()}-{suffix}"
            codes.add(code)
            item = ProjectItem(code, chapter, "", "", source=RCD_BUDGET_SOURCE + line.key)
        level = "Nivel I" if line.key == "earth" else "Nivel II" if line.volume is not None else "Otros costes"
        item.chapter = chapter
        item.unit = "m³" if line.volume is not None else "ud"
        item.round_import_total = True
        item.summary = f"{level} — {line.label}".upper()
        item.text = (
            "Gestión de residuos generada desde RCDs. Medición y precio directo se actualizan "
            "al guardar RCDs. No incluye fianza ni IVA. Los costes indirectos de configuración "
            "se aplican como en las demás partidas. El transporte existente se conserva por separado."
        ).upper()
        item.measurement_lines = [MeasurementLine(
            comment=f"Gestión de residuos RCDs: {line.label}",
            units=float(line.volume) if line.volume is not None else 1.0,
        )]
        item.recalculate_measurement()
        item.set_indirect_costs(app.report_settings.indirect_costs,
                               float(line.rate if line.rate is not None else line.cost))
        if generated[line.key] is None:
            pending.append(item)
    app._ensure_chapter(chapter)
    if existing is None:
        app.chapter_titles[chapter] = "Gestión de residuos"
    app.budget_items.extend(pending)
    app.refresh_budget_tree()
    app._mark_dirty()


def write_report(state: RcdState, title: str, settings, target: Path) -> None:
    """Genera el mismo presupuesto tabulado en PDF u ODT con la plantilla elegida."""
    from bc3_gui import fmt_number

    state = RcdState.from_dict(state.to_dict())
    sections, details = calculate(state)
    inputs = [[label, fmt_number(value, 4 if key == "volume_factor" else 2)]
              for key, label, _ in INPUTS if (value := state.inputs[key]) is not None]
    waste_rows = [[code, description, "; ".join(state.evidence.get(key, [])) or "Selección manual"]
                  for key, _, code, description, _ in WASTES if state.selected[key]]
    group_names = {key: label for key, label, *_ in GROUPS}
    demolition_rows = [
        [
            row.source,
            f"{fmt_number(float(row.quantity), 3)} {row.unit}",
            fmt_number(float(row.factor), 3),
            group_names[row.group],
            fmt_number(float(row.volume)),
        ]
        for row in state.demolition if row.included
    ]
    demolition_headers = ["Procedencia", "Medición original", "Factor a m³", "Material", "Volumen (m³)"]
    heading = "PRESUPUESTO DE GESTIÓN DE RCDs"
    if settings.output_format == "odt":
        from odt_reports import _new_report

        document = _new_report(heading, title, settings)
        document.paragraph(NOTE)
        document.space(12)
        if state.demolition:
            document.paragraph(MEASURED_NOTE)
            document.heading("Mediciones de demolición copiadas al transporte")
            document.table(demolition_headers, demolition_rows, ratios=[3, 1, 1, 1.5, 1])
            document.space(12)
        for label, rows in sections:
            document.heading(label)
            document.table(
                HEADERS, rows, bold_rows=(0,) if label.startswith("TOTAL") else (),
                ratios=[40, 15, 17, 14, 14],
            )
            document.space(12)
        document.heading("Datos de cálculo y revisión")
        document.table(["Dato", "Valor"], inputs, ratios=[4, 1])
        document.space(12)
        document.table(
            ["Grupo", "X", "Peso (t)", "Densidad (t/m³)", "Volumen (m³)"], details,
            ratios=[4, 0.5, 1, 1, 1],
        )
        document.space(12)
        document.heading("Residuos seleccionados (revisión manual)")
        document.table(
            ["LER (modelo ODS)", "Residuo", "Procedencia"], waste_rows,
            ratios=[1.2, 2.5, 4],
        )
        document.paragraph("Los códigos repetidos y la clasificación del grupo otros proceden del modelo.")
        document.space(24)
        for value in (settings.location, settings.role, settings.author, settings.organization, settings.signature):
            if value:
                document.paragraph(value, style="Signature")
        document.write(target)
        return
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import KeepTogether, SimpleDocTemplate, Spacer
    from report_layout import LEFT_MARGIN, RIGHT_MARGIN, ReportLayout, report_project_label

    layout = ReportLayout(template_path=settings.template_path)
    project_label = report_project_label(title)
    story = [layout.text(NOTE), Spacer(1, 12)]
    if state.demolition:
        story.extend([layout.text(MEASURED_NOTE),
                      layout.text("Mediciones de demolición copiadas al transporte", layout.bold),
                      layout.table(demolition_headers, demolition_rows, [3, 1, 1, 1.5, 1]),
                      Spacer(1, 12)])
    for label, rows in sections:
        story.extend([layout.text(label, layout.bold), layout.table(
            HEADERS, rows, [40, 15, 17, 14, 14],
            bold_rows=(0,) if label.startswith("TOTAL") else (),
        ), Spacer(1, 12)])
    story.extend([layout.text("Datos de cálculo y revisión", layout.bold),
                  layout.table(["Dato", "Valor"], inputs, [4, 1]),
                  Spacer(1, 12),
                  layout.table(["Grupo", "X", "Peso (t)", "Densidad (t/m³)", "Volumen (m³)"],
                               details, [4, 0.5, 1, 1, 1]),
                  Spacer(1, 12),
                  layout.text("Residuos seleccionados (revisión manual)", layout.bold)])
    if waste_rows:
        story.append(layout.table(["LER (modelo ODS)", "Residuo", "Procedencia"], waste_rows, [1.2, 2.5, 4]))
    signatures = [
        layout.text(value, layout.center)
        for value in (settings.location, settings.role, settings.author, settings.organization, settings.signature)
        if value
    ]
    if signatures:
        story.append(KeepTogether([Spacer(1, 24), *signatures]))

    def header(canvas, document):
        """Dibuja cabecera y pie con el mismo diseño que los demás informes."""
        layout.header(canvas, document, heading, project_label)

    document = SimpleDocTemplate(str(target), pagesize=A4, leftMargin=LEFT_MARGIN - 6,
                                 rightMargin=RIGHT_MARGIN - 6, bottomMargin=layout.bottom_margin(),
                                 topMargin=layout.top_margin(heading, project_label),
                                 title=f"{heading} - {title}", author=settings.author)
    document.build(story, onFirstPage=header, onLaterPages=header)


class RcdWindow(tk.Toplevel):
    def __init__(self, master: tk.Misc, app: BudgetApp):
        """Abre el primer paso con los datos recuperados o las últimas ediciones."""
        super().__init__(master)
        self.app = app
        self.title("RCDs")
        self.geometry("1250x800")
        self.minsize(1000, 600)
        self.data = RcdState.from_dict(app.rcd_state) if app.rcd_state else initial_state(app)
        self._dirty = False
        self.content = ttk.Frame(self, padding=12)
        self.content.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show_inputs()

    def show_inputs(self):
        """Muestra todos los datos editables y destaca los que aún faltan."""
        self.unbind("<Control-s>")
        for widget in self.content.winfo_children():
            widget.destroy()
        ttk.Label(self.content, text="RCDs — Datos de cálculo (revisa también los valores recuperados)",
                  style="Total.TLabel").pack(anchor="w", pady=6)
        ttk.Label(self.content, text=NOTE, wraplength=1100).pack(fill="x", pady=6)
        ttk.Button(self.content, text="Llevar mediciones de demolición a transporte a vertedero",
                   command=self.review_demolition).pack(anchor="w", pady=6)
        self.measured_label = ttk.Label(self.content, text=MEASURED_NOTE if self.data.demolition else "",
                                        wraplength=1100)
        self.measured_label.pack(fill="x", pady=(0, 6))
        canvas_frame = ttk.Frame(self.content)
        canvas_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scroll = ttk.Scrollbar(canvas_frame, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(canvas, padding=8)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        self.variables = {}
        self.group_variables = {}
        for row, (key, label, _) in enumerate(INPUTS):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3)
            value = self.data.inputs[key]
            variable = tk.StringVar(value="" if value is None else str(value))
            self.variables[key] = variable
            ttk.Entry(form, textvariable=variable, width=18).grid(row=row, column=1, padx=8)
            source = self.data.sources.get(key, "Valor del modelo; editable.")
            if value is None:
                source = "PENDIENTE — " + self.data.sources.get(key, "Indica el valor, o 0 si no procede.")
            ttk.Label(form, text=source, wraplength=500).grid(row=row, column=2, sticky="w")
        start = len(INPUTS)
        for column, label in enumerate(("Coeficientes del modelo por grupo", "% del peso", "Densidad (t/m³)")):
            ttk.Label(form, text=label).grid(row=start, column=column, sticky="w", pady=8)
        for index, (key, label, *_rest) in enumerate(GROUPS, start + 1):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=3)
            for column, name in enumerate(("percentage", "density"), 1):
                variable = tk.StringVar(value=str(self.data.groups[key][name]))
                self.group_variables[key, name] = variable
                entry = ttk.Entry(form, textvariable=variable, width=18)
                entry.grid(row=index, column=column, sticky="w", padx=8)
                if key == "earth" and name == "percentage":
                    entry.configure(state="disabled")
        ttk.Button(self.content, text="Aceptar y revisar residuos", command=self.accept_inputs,
                   style="Accent.TButton").pack(anchor="e", pady=8)
        localize_widget_tree(self)

    def review_demolition(self):
        """Revisa materiales y conversiones antes de modificar el presupuesto."""
        rows = demolition_measurements(self.app, self.data)
        if not rows:
            messagebox.showinfo("RCDs", "No se han encontrado partidas de demolición, retirada "
                                "o desmontaje. Revisa sus descripciones y capítulos.", parent=self)
            return
        DemolitionDialog(self, rows)

    def accept_inputs(self):
        """Valida el formulario completo antes de pasar a la selección de residuos."""
        raw = self.data.to_dict()
        for key, variable in self.variables.items():
            value = variable.get()
            raw["inputs"][key] = None if self.data.demolition and key in MODEL_INPUTS and not value.strip() else value
        for (key, name), variable in self.group_variables.items():
            raw["groups"][key][name] = variable.get()
        try:
            candidate = RcdState.from_dict(raw)
            calculate(candidate)
        except ValueError as exc:
            messagebox.showerror("RCDs", str(exc), parent=self)
            return
        for key, variable in self.variables.items():
            if candidate.inputs[key] != self.data.inputs[key]:
                candidate.sources[key] = "Revisado manualmente."
        self.data = candidate
        self.changed()
        self.show_wastes()

    def changed(self):
        """Publica cada edición aceptada en el proyecto y señala guardado pendiente."""
        self.app.rcd_state = self.data.to_dict()
        self.app._mark_dirty()
        self._dirty = True
        if hasattr(self, "save_button") and self.save_button.winfo_exists():
            self.save_button.configure(background="#000000", foreground="#FFFFFF")

    def show_wastes(self):
        """Muestra marcas editables, evidencias y el presupuesto calculado."""
        from bc3_gui import ToolTip

        for widget in self.content.winfo_children():
            widget.destroy()
        toolbar = ttk.Frame(self.content)
        toolbar.pack(fill="x", pady=6)
        ttk.Button(toolbar, text="Datos de cálculo", command=self.show_inputs).pack(side="left")
        ttk.Button(toolbar, text="Generar informe RCDs", command=self.generate_report).pack(side="left", padx=8)
        self.save_button = tk.Button(toolbar, text="💾", width=3, command=self.save)
        self._save_background = self.save_button.cget("background")
        self._save_foreground = self.save_button.cget("foreground")
        self.save_button.pack(side="right")
        if self._dirty:
            self.save_button.configure(background="#000000", foreground="#FFFFFF")
        ToolTip(self.save_button, "Guardar los cambios RCDs en el proyecto (Ctrl+S).")
        self.bind("<Control-s>", lambda _event: self.save())
        ttk.Label(self.content, text="Pulsa X o Espacio para incluir/excluir. Las coincidencias son orientativas; "
                  "revisa especialmente residuos peligrosos y códigos LER.", wraplength=1100).pack(fill="x")
        frame = ttk.Frame(self.content)
        frame.pack(fill="both", expand=True, pady=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=("x", "group", "ler", "description", "evidence"),
                                 show="headings", selectmode="browse", height=12)
        for column, label, width in (
            ("x", "X", 35), ("group", "Grupo", 160), ("ler", "LER (modelo ODS)", 120),
            ("description", "Residuo", 350), ("evidence", "Detectado en partidas / revisión", 420),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, stretch=column in {"description", "evidence"})
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(frame, command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind("<ButtonRelease-1>", self.click_waste)
        self.tree.bind("<space>", self.toggle_selected)
        self.tree.bind("<KeyPress-x>", self.toggle_selected)
        names = {key: label for key, label, *_ in GROUPS}
        for key, group, code, description, _ in WASTES:
            self.tree.insert("", "end", iid=key, values=(
                "X" if self.data.selected[key] else "", names[group], code, description,
                "; ".join(self.data.evidence.get(key, [])) or "Sin coincidencia; selección manual disponible",
            ))
        self.preview = ttk.Treeview(self.content, columns=tuple(f"c{i}" for i in range(5)),
                                    show="headings", height=8)
        for index, label in enumerate(HEADERS):
            self.preview.heading(f"c{index}", text=label)
            self.preview.column(f"c{index}", width=410 if index == 0 else 150)
        self.preview.pack(fill="x", pady=6)
        self.status = ttk.Label(self.content)
        self.status.pack(fill="x")
        self.refresh_preview()
        localize_widget_tree(self)

    def click_waste(self, event):
        """Alterna únicamente las marcas pulsadas en la primera columna."""
        row = self.tree.identify_row(event.y)
        if row and self.tree.identify_column(event.x) == "#1":
            self.tree.selection_set(row)
            self.toggle_selected()

    def toggle_selected(self, _event=None):
        """Conserva las evidencias automáticas al cambiar una marca manualmente."""
        selection = self.tree.selection()
        if selection:
            key = selection[0]
            self.data.selected[key] = not self.data.selected[key]
            self.tree.set(key, "x", "X" if self.data.selected[key] else "")
            self.changed()
            self.refresh_preview()
        return "break"

    def refresh_preview(self):
        """Recalcula importes y saldo total al cambiar los residuos incluidos."""
        for row in self.preview.get_children():
            self.preview.delete(row)
        sections, _ = calculate(self.data)
        for _, rows in sections:
            for row in rows:
                self.preview.insert("", "end", values=row)
        self.status.configure(text=f"{sum(self.data.selected.values())} residuos seleccionados. "
                              f"Total: {sections[-1][1][0][3]} € (sin fianza ni IVA).")

    def save(self):
        """Incorpora el presupuesto RCDs al proyecto antes de guardarlo."""
        try:
            synchronize_budget(self.app, self.data)
        except ValueError as exc:
            messagebox.showerror("No se pudo incorporar la gestión de residuos", str(exc), parent=self)
            return
        self.changed()
        if self.app.save_project():
            self._dirty = False
            self.save_button.configure(background=self._save_background, foreground=self._save_foreground)
            self.status.configure(text="RCDs y capítulo Gestión de residuos guardados con el proyecto.")

    def generate_report(self):
        """Genera atómicamente el formato configurado y abre el archivo resultante."""
        settings = self.app.report_settings
        extension = ".odt" if settings.output_format == "odt" else ".pdf"
        raw = filedialog.asksaveasfilename(
            parent=self, title="Generar informe RCDs", initialfile=f"Presupuesto RCDs{extension}",
            defaultextension=extension, filetypes=[("Informe RCDs", f"*{extension}")],
        )
        if not raw:
            return
        target = Path(raw).with_suffix(extension)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, suffix=extension, delete=False) as handle:
                temporary = Path(handle.name)
            write_report(self.data, self.app._project_title(), settings, temporary)
            temporary.replace(target)
        except (OSError, ValueError, ImportError) as exc:
            messagebox.showerror("No se pudo generar el informe", str(exc), parent=self)
            return
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self.status.configure(text=f"Informe guardado: {target}")
        try:
            os.startfile(str(target))
        except OSError as exc:
            messagebox.showwarning("Informe guardado", f"{target}\n\nNo se pudo abrir:\n{exc}", parent=self)

    def close(self):
        """Cierra conservando las ediciones aceptadas en memoria del proyecto."""
        self.destroy()


class DemolitionDialog(tk.Toplevel):
    def __init__(self, owner: RcdWindow, rows: list[DemolitionMeasurement]):
        """Permite revisar cada línea y elegir un transporte sin asumir espesores."""
        super().__init__(owner)
        self.owner = owner
        self.rows = rows
        self.title("Demoliciones a transporte a vertedero")
        self.geometry("1250x650")
        self.transient(owner)
        self.grab_set()
        self.materials = {
            f"{group} — {code} — {description}": (key, group_key)
            for key, group_key, code, description, _ in WASTES
            for group in [next(label for group_id, label, *_ in GROUPS if group_id == group_key)]
        }
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, wraplength=1150, text=(
            "Revisa las partidas detectadas y desmarca las que no procedan. Cada línea conserva su origen. "
            "m³: factor 1; m²: espesor en metros; m: sección en m²; ud: volumen por unidad; "
            "t: m³/t. No se suponen conversiones ni esponjamiento. Revisa el material/LER."
        )).pack(fill="x", pady=6)
        self.targets = [
            item for item in owner.app.budget_items
            if re.search(r"transport", normalize(item.summary))
            and re.search(r"vertedero|residu|gestor|planta", normalize(f"{item.summary} {item.text}"))
        ]
        choices = [f"{i + 1}. {item.code} — {item.summary} ({item.unit})"
                   for i, item in enumerate(self.targets)]
        choices.append("Crear nueva partida de transporte a vertedero (m³)")
        ttk.Label(frame, text="Partida de destino").pack(anchor="w")
        self.target_choice = ttk.Combobox(frame, values=choices, state="readonly", width=110)
        self.target_choice.pack(fill="x", pady=6)
        if len(self.targets) <= 1:
            self.target_choice.current(0)
        ttk.Label(frame, text="Se conservan las líneas manuales del destino. Las líneas generadas por RCDs "
                  "se sustituyen, no se acumulan. Si cambias de destino, se retiran del anterior.",
                  wraplength=1150).pack(fill="x", pady=6)
        area = ttk.Frame(frame)
        area.pack(fill="both", expand=True)
        canvas = tk.Canvas(area, highlightthickness=0)
        scroll = ttk.Scrollbar(area, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        for column, heading in enumerate(("Incluir", "Partida / medición de origen", "Cantidad",
                                           "Factor a m³", "Material / LER")):
            ttk.Label(form, text=heading).grid(row=0, column=column, sticky="w", padx=4)
        form.columnconfigure(1, weight=1)
        self.edits = []
        for index, row in enumerate(rows, 1):
            included = tk.BooleanVar(value=row.included)
            factor = tk.StringVar(value="" if row.factor is None else str(row.factor))
            material = tk.StringVar(value=next(
                (label for label, (key, _) in self.materials.items() if key == row.waste_key), ""
            ))
            ttk.Checkbutton(form, variable=included).grid(row=index, column=0)
            ttk.Label(form, text=row.source, wraplength=350).grid(row=index, column=1, sticky="w", pady=5)
            ttk.Label(form, text=f"{row.quantity:g} {row.unit}").grid(row=index, column=2, padx=4)
            entry = ttk.Entry(form, textvariable=factor, width=12)
            entry.grid(row=index, column=3, padx=4)
            if normalize(row.unit).replace("³", "3").replace("^", "").replace(" ", "") == "m3":
                entry.configure(state="readonly")
            ttk.Combobox(form, values=list(self.materials), textvariable=material,
                         state="readonly", width=48).grid(row=index, column=4, padx=4)
            self.edits.append((included, factor, material))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Copiar mediciones y usar volúmenes en RCDs",
                   command=self.apply, style="Accent.TButton").pack(side="right", padx=8)
        localize_widget_tree(self)

    def apply(self):
        """Valida todo el desglose antes de copiar y publicar las mediciones."""
        choice = self.target_choice.current()
        if choice < 0:
            messagebox.showerror("RCDs", "Selecciona la partida de transporte de destino.", parent=self)
            return
        rows = []
        try:
            for row, (included, factor, material) in zip(self.rows, self.edits):
                raw = asdict(row)
                raw["included"] = included.get()
                raw["factor"] = factor.get() if factor.get().strip() else None
                raw["waste_key"], raw["group"] = self.materials.get(material.get(), ("", ""))
                rows.append(DemolitionMeasurement.from_dict(raw))
            target = self.targets[choice] if choice < len(self.targets) else None
            created = transfer_demolition(self.owner.app, self.owner.data, rows, target)
        except ValueError as exc:
            messagebox.showerror("RCDs", str(exc), parent=self)
            return
        self.owner.changed()
        self.owner.measured_label.configure(text=MEASURED_NOTE)
        total = sum((row.volume for row in rows if row.included), Decimal(0))
        notice = (
            f"Copiadas {sum(row.included for row in rows)} mediciones: {total:.2f} m³.\n\n"
            "Se conservan las demoliciones originales. Los volúmenes revisados por material "
            "sustituyen al reparto porcentual del ODS. Guarda el proyecto para conservar los cambios.\n\n"
            "Revisa el presupuesto sin gestión de residuos y los costes de gestión."
        )
        if created:
            messagebox.showwarning("Transporte creado: revisión obligatoria",
                                   f"{notice}\n\n{TRANSPORT_REVIEW}", parent=self)
        else:
            messagebox.showinfo("Mediciones copiadas", notice, parent=self)
        self.destroy()
