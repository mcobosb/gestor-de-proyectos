"""Consulta de precios BCCA y estimaciones públicas de ensayos."""
from __future__ import annotations

from functools import lru_cache
import math
from pathlib import Path

from budget_core import Bc3Dataset, Concept
from .catalog import get_catalog_entry
from .plan import TRIALS, normalized

BCCA_PATH = Path(__file__).parent.parent / "BCCA2023_V02.bc3"

# Las tasas históricas son referencias, no presupuestos actualizados de laboratorio.
BORM_URL = "https://www.borm.es/services/anuncio/ano/2018/numero/1909/pdf"
GEOCESA_URL = "https://geocesa.com/web/index.php?seccion=tarifa"
ESTIMATES = {
    "density": ("42.17", "Una determinación de densidad y humedad in situ por el método de la arena.",
                "BORM, tasas de 2018", BORM_URL),
    "plate": ("120.00", "Una placa NLT-357; elemento reactivo y desplazamiento aparte.",
              "Geocesa, tarifa sin fecha", GEOCESA_URL),
    "concrete": ("168.29", "Serie de hasta seis probetas con curado en agua y rotura a una edad (151,26 €), "
                 "más tres determinaciones de consistencia (17,03 €).",
                 "BORM, tasas de 2018, T420", BORM_URL),
    "asphalt": ("60.37", "Extracción de un testigo de mezcla bituminosa y determinación de espesor y densidad.",
                "BORM, tasas de 2018, T420", BORM_URL),
    "PAC-07": ("140.00", "Paquete parcial: granulometría, límites de Atterberg, materia orgánica, "
               "sales solubles y Proctor modificado; no incluye controles de ejecución.",
               "Geocesa, tarifa sin fecha", GEOCESA_URL),
    "PAC-16": ("34.06", "Paquete parcial: tres determinaciones de consistencia y tres de aire ocluido; "
               "no incluye el resto de inspecciones.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "PAC-17": ("151.26", "Serie de hasta seis probetas con curado en agua y rotura a una edad; "
               "no incluye una segunda edad ni evaluación estadística.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "PAC-29": ("370.85", "Paquete parcial: desgaste Los Ángeles y pulimento acelerado; "
               "no incluye Micro-Deval, caras de fractura ni lajas.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "PAC-30": ("149.00", "Paquete parcial: granulometría, comprobación de no plasticidad y placa NLT-357; "
               "no incluye equivalente de arena ni densidad in situ; elemento reactivo aparte.",
               "Geocesa, tarifa sin fecha", GEOCESA_URL),
    "PAC-33": ("113.44", "Paquete parcial Marshall: fabricación de tres probetas, densidad, estabilidad, "
               "deformación y huecos; no incluye compactador giratorio ni otros ensayos de fórmula.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "PAC-34": ("168.43", "Paquete parcial: contenido de ligante, granulometría de áridos extraídos y "
               "extracción de un testigo con espesor y densidad; no incluye el resto de controles.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "PAC-36": ("28.16", "Paquete parcial: una medida de macrotextura mediante círculo de arena; "
               "no incluye IRI, regla ni rozamiento transversal.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "PAC-54": ("60.26", "Paquete parcial: prueba de estanqueidad y evacuación de cubierta por bajante; "
               "no incluye las demás inspecciones.",
               "BORM, tasas de 2018", BORM_URL),
    "ENS-01": ("17.03", "Hasta tres determinaciones de consistencia; revisar el tamaño del lote.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-02": ("17.03", "Hasta tres determinaciones de aire ocluido; confirmar el método y tamaño del lote.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-03": ("151.26", "Serie de hasta seis probetas con curado en agua y rotura a una edad.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-05": ("202.83", "Extracción de un testigo (169,03 €) y tallado, refrentado y compresión (33,80 €).",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-06": ("86.41", "Un ensayo de desgaste Los Ángeles de árido grueso.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-08": ("284.44", "Un ensayo de pulimento acelerado y determinación del coeficiente.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-09": ("113.44", "Paquete Marshall: fabricación de tres probetas, densidad, estabilidad, "
               "deformación y huecos; no incluye compactador giratorio.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-11": ("380.55", "Ensayo de deformación en pista de dos probetas; fabricación no incluida.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-12": ("434.64", "Un ensayo Cántabro completo; confirmar condiciones y alcance.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-13": ("108.06", "Contenido de ligante y granulometría de los áridos extraídos.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-14": ("60.37", "Paquete parcial: extracción de un testigo y determinación de densidad y espesor; "
               "no incluye adherencia Leutner.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-18": ("28.16", "Una medida de textura superficial por círculo de arena.",
               "BORM, tasas de 2018, T420", BORM_URL),
    "ENS-28": ("60.26", "Prueba de estanqueidad y evacuación de cubierta por unidad de bajante.",
               "BORM, tasas de 2018", BORM_URL),
}


def estimate_empty_price(row: dict, currency: str) -> dict:
    """Propone solo precios trazables para filas originales vacías en euros."""
    identifier = row.get("trial")
    if currency != "EUR" or str(row.get("price", "")).strip() or identifier not in ESTIMATES:
        return row
    expected = TRIALS[identifier].name if identifier in TRIALS else get_catalog_entry(identifier).label
    if row.get("name") != expected:
        return row
    price, scope, source, url = ESTIMATES[identifier]
    return row | {
        "price": price,
        "price_source": {
            "code": identifier, "summary": scope, "unit": "paquete", "price": price,
            "trial_name": expected, "source": f"Estimación orientativa: {source}",
            "url": url, "consulted": "2026-11-26",
            "limitations": "Tarifa histórica o sin fecha; IVA, desplazamientos y alcance contractual por confirmar.",
        },
    }


@lru_cache(maxsize=1)
def price_catalog() -> tuple[Concept, ...]:
    """Carga partidas de control valoradas por unidad, sin sus auxiliares."""
    dataset = Bc3Dataset(BCCA_PATH)
    chapter_items = {
        child for code in dataset.concepts if code.startswith("18") and code.endswith("#")
        for child in dataset.get_children(code)
    }
    entries = tuple(sorted(
        (concept for concept in dataset.concepts.values()
         if concept.code in chapter_items and concept.code.startswith("18")
         and not concept.code.endswith("#") and concept.unit.casefold().strip() in {"u", "ud"}
         and math.isfinite(concept.price) and concept.price > 0),
        key=lambda concept: concept.code,
    ))
    if not entries:
        raise ValueError("La base BCCA no contiene partidas de calidad con precio por unidad.")
    return entries


def price_candidates(name: str, query: str = "") -> list[Concept]:
    """Ofrece correspondencias comprobables o resultados de una búsqueda explícita."""
    recommended = {
        TRIALS["concrete"].name: ("18HHL00010", "18HHL00020"),
    }
    if name == "ENS-01 — Asentamiento en Cono de Abrams":
        codes = ("18HHL00020",)
    elif name == "ENS-03 — Resistencia a Compresión de Probetas":
        codes = ("18HHL00010",)
    else:
        codes = recommended.get(name, ())
    search = normalized(query).strip()
    return [
        concept for concept in price_catalog()
        if (all(term in normalized(f"{concept.code} {concept.summary} {concept.text}")
                for term in search.split()) if search else concept.code in codes)
    ]
