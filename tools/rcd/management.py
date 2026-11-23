"""Decisiones particulares de segregación, reutilización y destino de RCDs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import RcdState


SEGREGATION = {
    "removal": "Eliminación previa y separada de elementos desmontables y residuos peligrosos.",
    "selective": "Demolición selectiva / segregación en obra por fracciones, conforme a la Ley 7/2022.",
    "mixed": (
        "Recogida de la fracción mezclada residual y tratamiento posterior en planta autorizada, "
        "sin sustituir las separaciones obligatorias (justificar)."
    ),
}
REUSE = {
    "none": "No se prevé reutilización en la obra ni en emplazamientos externos.",
    "earth": "Reutilización de tierras procedentes de la excavación.",
    "minerals": "Reutilización de materiales minerales o pétreos.",
    "ceramics": "Reutilización de materiales cerámicos.",
    "nonstone": "Reutilización de materiales no pétreos: madera, vidrio u otros.",
    "metals": "Reutilización de materiales metálicos.",
    "other": "Otra reutilización (indicar).",
}
RECOVERY = {
    "none": "No se prevén operaciones de valorización en la obra; se define el destino externo por LER.",
    "r1": "R1. Utilización principal como combustible o como otro medio de generar energía.",
    "r2": "R2. Recuperación o regeneración de disolventes.",
    "r3": "R3. Reciclado o recuperación de sustancias orgánicas que no se utilizan como disolventes.",
    "r4": "R4. Reciclado o recuperación de metales y de compuestos metálicos.",
    "r5": "R5. Reciclado o recuperación de otras materias inorgánicas.",
    "r6": "R6. Regeneración de ácidos o de bases.",
    "r10": "R10. Tratamiento de suelos que produzca un beneficio a la agricultura o una mejora ecológica.",
    "r13": (
        "R13. Almacenamiento en espera de una operación R1 a R12, cuando proceda; "
        "no incluye el almacenamiento temporal en el lugar de producción."
    ),
    "other": "Otra operación de valorización (indicar).",
}
SELECTIONS = {"segregation": SEGREGATION, "reuse": REUSE, "recovery": RECOVERY}
DESTINATIONS = {
    "onsite": "Reutilización / preparación para reutilización en la propia obra",
    "offsite": "Reutilización / preparación para reutilización en emplazamiento externo",
    "recycling": "Planta autorizada de reciclaje o valorización",
    "transfer": "Centro autorizado de transferencia o almacenamiento",
    "hazardous": "Gestor autorizado para residuos peligrosos",
    "disposal": "Instalación de eliminación / vertedero autorizado (última opción)",
    "other": "Otro destino (especificar)",
}
REUSE_DESTINATIONS = ("Propia obra", "Emplazamiento externo", "Propia obra y emplazamiento externo")
TYPE_LABELS = {
    "earth": "Tierras y pétreos de excavación",
    "stone": "Naturaleza pétrea",
    "nonstone": "Naturaleza no pétrea",
    "hazardous": "Peligrosos y otros",
}
PREVENTION = {
    "design": ("Medidas de prevención en la fase de diseño y proyecto", {
        "dimensions": (
            "Optimización de mediciones y ajustes dimensionales",
            "Se ajustarán las mediciones y dimensiones a los formatos comerciales de los materiales "
            "para reducir despuntes, recortes y excedentes, dentro de las condiciones del proyecto.",
            True,
        ),
        "industrialized": (
            "Sistemas constructivos industrializados y desmontables",
            "Se priorizarán elementos prefabricados y uniones en seco o atornilladas cuando sean "
            "compatibles con el proyecto, reduciendo mermas y facilitando su futura deconstrucción.",
            False,
        ),
        "earth_balance": (
            "Minimización de movimientos de tierras",
            "Se ajustarán rasantes, cimentaciones y zanjas para equilibrar desmontes y rellenos. "
            "La utilización de tierras limpias en la propia obra requerirá comprobar su aptitud, "
            "su clasificación y las condiciones legales; no se asignará automáticamente el LER 17 05 04.",
            False,
        ),
        "recycled": (
            "Incorporación de materiales reciclados",
            "Se preverán áridos reciclados de procedencia autorizada en los usos admitidos por "
            "el proyecto, con la documentación de conformidad y el marcado CE cuando sea exigible, "
            "sin sustituir la comprobación de sus prestaciones técnicas y ambientales.",
            False,
        ),
    }),
    "supply": ("Medidas en el suministro, acopio y manipulación de materiales", {
        "purchasing": (
            "Compra ajustada y suministro escalonado",
            "Los pedidos se ajustarán a la medición real de cada tajo y al avance de la obra, "
            "evitando excedentes y acumulaciones susceptibles de deterioro.",
            True,
        ),
        "storage": (
            "Acopio protegido en obra",
            "Se habilitarán superficies limpias y niveladas y protecciones frente a lluvia, humedad "
            "e impactos, adecuadas a cementos, yesos, aislamientos, maderas y demás materiales.",
            True,
        ),
        "returns": (
            "Acuerdos de devolución con proveedores",
            "Se acordará, cuando sea posible, la devolución de palets, bobinas, envases reutilizables "
            "y excedentes intactos en su embalaje original.",
            True,
        ),
        "packaging": (
            "Reducción de embalajes",
            "Se priorizarán suministros a granel o con embalajes mínimos, retornables o reciclables, "
            "evitando envases mixtos difíciles de separar cuando exista una alternativa viable.",
            True,
        ),
    }),
    "execution": ("Medidas durante la ejecución y demolición", {
        "selective_demolition": (
            "Demolición selectiva",
            "Antes del derribo se retirarán de forma selectiva los elementos reutilizables "
            "o valorizables y, previamente y por los procedimientos y empresas habilitados, "
            "los materiales peligrosos, evitando su mezcla con otras fracciones.",
            False,
        ),
        "direct_reuse": (
            "Reutilización directa en obra",
            "Se estudiará el aprovechamiento de materiales limpios en rellenos o bases solo "
            "si su aptitud y las especificaciones del proyecto lo permiten. No se autoriza el "
            "vertido directo de escombros: se verificará si se trata de reutilización de productos "
            "o de una operación de valorización de residuos y su régimen aplicable.",
            False,
        ),
        "washing": (
            "Limpieza de útiles y control de hormigoneras",
            "Cuando se realicen estos trabajos, el lavado de canaletas y herramientas se hará "
            "en recipientes estancos o sistemas de recogida habilitados, gestionando aguas y lodos "
            "según su naturaleza y evitando vertidos al terreno o al saneamiento.",
            True,
        ),
    }),
    "hazardous_training": ("Medidas de gestión de residuos peligrosos y formación", {
        "hazardous_separation": (
            "Separación estricta de residuos peligrosos",
            "Los residuos peligrosos que se generen, incluidos los envases contaminados que "
            "correspondan, se separarán en recipientes compatibles, identificados, cubiertos y "
            "con retención de derrames cuando proceda, evitando contaminar las fracciones no peligrosas.",
            True,
        ),
        "containers": (
            "Planificación de contenedores en obra",
            "Desde el inicio se delimitarán y señalizarán zonas para las fracciones presentes "
            "y las recogidas separadas obligatorias, incluyendo madera, minerales, metales, "
            "vidrio, plástico y yeso cuando se generen, además de envases y fracción resto.",
            True,
        ),
        "training": (
            "Información a operarios y subcontratas",
            "Antes de los trabajos, el contratista informará al personal y subcontratistas de "
            "las medidas de prevención, acopio y separación en origen del Plan de Gestión de Residuos.",
            True,
        ),
    }),
}
DEFAULT_PREVENTION = tuple(
    key for _title, options in PREVENTION.values()
    for key, (_label, _text, general) in options.items() if general
)


@dataclass
class RcdManagement:
    """Conserva decisiones explícitas sin asignar destinos automáticamente."""

    selections: dict[str, list[str]] = field(default_factory=lambda: {
        key: [] for key in SELECTIONS
    })
    details: dict[str, str] = field(default_factory=dict)
    reuse_destinations: dict[str, str] = field(default_factory=dict)
    destinations: dict[str, dict[str, str]] = field(default_factory=dict)
    prevention: list[str] = field(default_factory=lambda: list(DEFAULT_PREVENTION))

    @classmethod
    def from_dict(cls, data: object) -> RcdManagement:
        """Valida opciones persistidas y rechaza decisiones contradictorias."""
        from . import WASTES

        if not isinstance(data, dict):
            raise ValueError("Las previsiones de gestión RCDs deben ser un objeto JSON.")
        plan = cls()
        prevention = data.get("prevention", plan.prevention)
        prevention_codes = {key for _title, options in PREVENTION.values() for key in options}
        if (not isinstance(prevention, list)
                or any(not isinstance(key, str) or key not in prevention_codes for key in prevention)):
            raise ValueError("Medidas preventivas RCDs no válidas.")
        plan.prevention = list(dict.fromkeys(prevention))
        selections = data.get("selections", {})
        if not isinstance(selections, dict) or set(selections).difference(SELECTIONS):
            raise ValueError("Secciones de previsiones RCDs no válidas.")
        for section, options in SELECTIONS.items():
            selected = selections.get(section, [])
            if (not isinstance(selected, list)
                    or any(not isinstance(key, str) or key not in options for key in selected)):
                raise ValueError(f"Opciones de {section} RCDs no válidas.")
            if "none" in selected and len(selected) != 1:
                raise ValueError("«No se prevé» no puede combinarse con operaciones concretas.")
            plan.selections[section] = list(dict.fromkeys(selected))
        for name, options in (("details", SELECTIONS), ("reuse_destinations", REUSE)):
            values = data.get(name, {})
            if (not isinstance(values, dict)
                    or any(key not in options or not isinstance(value, str)
                           for key, value in values.items())):
                raise ValueError(f"Textos de previsiones RCDs no válidos: {name}.")
            setattr(plan, name, dict(values))
        destinations = data.get("destinations", {})
        if not isinstance(destinations, dict):
            raise ValueError("Los destinos RCDs deben estar identificados por código LER.")
        codes = {code for _key, _group, code, *_ in WASTES}
        for code, row in destinations.items():
            if (code not in codes or not isinstance(row, dict)
                    or set(row).difference({"destination", "facility", "details"})
                    or any(not isinstance(value, str) for value in row.values())
                    or row.get("destination", "") not in ("", *DESTINATIONS)):
                raise ValueError(f"Destino RCDs no válido: {code}.")
            plan.destinations[code] = dict(row)
        return plan


def selected_wastes(state: RcdState, excluded_codes: set[str] | None = None) -> list[list[str]]:
    """Agrupa los LER seleccionados, excluyendo procedencias generadas por el estudio."""
    from . import GROUPS, WASTES

    categories = {key: category for key, _label, category, *_ in GROUPS}
    rows: dict[str, list[str]] = {}
    for key, group, code, description, _pattern in sorted(WASTES, key=lambda waste: waste[2]):
        if not state.selected[key]:
            continue
        evidence = state.evidence.get(key, [])
        if evidence and excluded_codes and all(
            item.strip().partition(": ")[0] in excluded_codes for item in evidence
        ):
            continue
        category = categories[group]
        kind = TYPE_LABELS[category]
        level = "Nivel I" if category == "earth" else "Nivel II"
        if code not in rows:
            rows[code] = [code, description, kind, level]
        elif kind not in rows[code][2].split(" / "):
            rows[code][2] += " / " + kind
    return list(rows.values())
