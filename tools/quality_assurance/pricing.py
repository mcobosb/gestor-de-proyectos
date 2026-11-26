"""Consulta de precios BCCA y estimaciones públicas de ensayos."""
from __future__ import annotations

from decimal import Decimal
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

# Precios unitarios revisados en las bases facilitadas; no se distribuyen los BC3.
BC3_COMPONENTS = {
    "18HAN00020": ("525.00", "nivel de iluminación de alumbrado exterior", "BCCA Andalucía 2023"),
    "18IAJ00020": ("650.04", "funcionamiento de red de extinción", "BCCA Andalucía 2023"),
    "18PAP00060": ("50.00", "adherencia por tracción de pintura", "BCCA Andalucía 2023"),
    "C01A010": ("283.10", "ensayo de aislante rígido", "Extremadura 2025"),
    "C02CA090": ("266.70", "control de arena para hormigón", "Extremadura 2025"),
    "C02CA100": ("327.12", "control de grava para hormigón", "Extremadura 2025"),
    "C02FF020": ("117.03", "serie de flexotracción de hormigón", "Extremadura 2025"),
    "C02FF140": ("180.00", "penetración de agua en tres probetas", "Extremadura 2025"),
    "C03S010": ("23.70", "líquidos penetrantes en soldadura", "Extremadura 2025"),
    "C03S040": ("178.80", "ultrasonidos en soldadura", "Extremadura 2025"),
    "C06EI030": ("61.81", "continuidad del circuito de tierra", "Extremadura 2025"),
    "C06EI040": ("61.81", "resistencia de puesta a tierra", "Extremadura 2025"),
    "C06EI070": ("30.91", "aislamiento de conductores", "Extremadura 2025"),
    "C06FI010": ("92.72", "presión y estanqueidad de fontanería", "Extremadura 2025"),
    "C06L010": ("61.81", "funcionamiento de ascensor", "Extremadura 2025"),
    "C06S060": ("185.43", "inspección de saneamiento con cámara", "Extremadura 2025"),
    "C07L020": ("313.47", "ensayo de ladrillos para revestir", "Extremadura 2025"),
    "C10EF100": ("466.34", "clasificación de suelo para rellenos", "Extremadura 2025"),
    "C10W010.": ("105.38", "desgaste Los Ángeles", "Extremadura 2025"),
    "C10W050": ("64.03", "índice de lajas y agujas", "Extremadura 2025"),
    "C10W070": ("134.71", "placa de carga de carreteras", "Extremadura 2025"),
    "C10Y080": ("71.04", "una probeta de suelo-cemento", "Extremadura 2025"),
    "C11F020": ("408.00", "estanqueidad al aire de vivienda hasta 400 m²", "Extremadura 2025"),
    "C14T010": ("315.00", "termografía de vivienda unifamiliar", "Extremadura 2025"),
    "CM1C01G060": ("648.42", "ensayos de geotextil", "Comunidad de Madrid 2024, área 1"),
    "CM1C01FS010": ("393.67", "clasificación de suelos para terraplén", "Comunidad de Madrid 2024, área 1"),
    "CM1C01FS020": ("388.69", "clasificación de suelos para rellenos localizados",
                    "Comunidad de Madrid 2024, área 1"),
    "CM1C01FS040": ("92.18", "Proctor modificado", "Comunidad de Madrid 2024, área 1"),
    "CM1C02CM180": ("733.67", "conformidad de acero para hormigón", "Comunidad de Madrid 2024, área 1"),
    "CM1C02E090": ("1939.45", "prueba de carga de viga o forjado", "Comunidad de Madrid 2024, área 1"),
    "CM1C02I020": ("192.97", "auscultación sónica de pilote menor de 15 m",
                    "Comunidad de Madrid 2024, área 1"),
    "CM1C06HA050": ("229.88", "conformidad de adoquines de hormigón", "Comunidad de Madrid 2024, área 1"),
    "CM1C06HE050": ("85.11", "resbaladicidad de baldosas de cemento", "Comunidad de Madrid 2024, área 1"),
    "CM1C07C090": ("662.74", "ensayos de carpintería de aluminio o PVC", "Comunidad de Madrid 2024, área 1"),
    "CM1C08S110": ("88.72", "estanqueidad con agua entre pozos", "Comunidad de Madrid 2024, área 1"),
    "CM1C10I200": ("221.80", "presión y estanqueidad de abastecimiento",
                    "Comunidad de Madrid 2024, área 1"),
    "CM1C10R010": ("936.61", "prueba de aislamiento acústico en edificación",
                    "Comunidad de Madrid 2024, área 1"),
}

# Cada importe representa el paquete indicado, no el control completo del PAC.
BC3_ESTIMATES = {
    "PAC-02": (("C10EF100",), "Paquete parcial: clasificación de una muestra; no incluye sondeos ni estudio."),
    "PAC-06": (("C10EF100",), "Paquete parcial: clasificación de suelo para rellenos; no incluye inspección de obra."),
    "PAC-07": (("CM1C01FS010", "CM1C01FS040"), "Paquete parcial: clasificación para terraplén "
               "y Proctor modificado; no incluye controles de ejecución."),
    "PAC-08": (("C10W010.", "C10W050"), "Paquete parcial: Los Ángeles y lajas; no incluye tramo de prueba ni finos."),
    "PAC-09": (("CM1C01FS020",), "Paquete parcial: clasificación y Proctor modificado; "
               "no incluye comprobación de tongadas."),
    "PAC-10": (("C10Y080", "C10W070"), "Paquete parcial: una probeta de suelo-cemento y placa; "
               "no incluye densidad, dosificación ni el resto de controles."),
    "PAC-12": (("CM1C02I020",), "Paquete parcial: ensayo sónico de un pilote de menos de 15 m; "
               "no incluye la instrumentación ni los demás controles."),
    "PAC-15": (("C10W070",), "Paquete parcial: una placa de carga; no incluye inyección ni control de columnas."),
    "PAC-18": (("C02FF140",), "Penetración de agua bajo presión en tres probetas de hormigón."),
    "PAC-19": (("CM1C02CM180",), "Paquete de ensayos de conformidad de acero; no incluye revisión documental."),
    "PAC-20": (("C02CA090", "C02CA100"), "Paquete parcial: controles de arena y grava; "
               "no incluye cemento, agua, aditivos ni revisión documental."),
    "PAC-25": (("C03S010", "C03S040"), "Paquete parcial: líquidos penetrantes y ultrasonidos en soldaduras; "
               "no incluye perfiles, tornillería ni el resto de inspecciones."),
    "PAC-26": (("18PAP00060",), "Paquete parcial: adherencia por tracción de pintura; "
               "no incluye espesor, cuadrícula ni continuidad."),
    "PAC-30": (("CM1C01FS020", "C10W070"), "Paquete parcial: clasificación de suelo para rellenos "
               "y placa de carga; no incluye equivalente de arena ni densidad in situ."),
    "PAC-31": (("C10Y080",), "Paquete parcial: fabricación y rotura de una probeta de suelo-cemento; "
               "no incluye dosificación, densidad ni juntas."),
    "PAC-35": (("C02FF020",), "Paquete parcial: tres probetas a flexotracción y consistencia; "
               "no incluye inspección de ejecución ni juntas."),
    "PAC-37": (("CM1C01G060",), "Paquete parcial: ensayos de conformidad de geotextil; "
               "no incluye pendientes ni colocación del drenaje."),
    "PAC-38": (("CM1C10I200",), "Prueba de presión y estanqueidad de un tramo de abastecimiento."),
    "PAC-39": (("CM1C08S110", "C06S060"), "Paquete parcial: estanqueidad con agua entre pozos "
               "e inspección con cámara; no incluye prueba con aire ni ovalización."),
    "PAC-46": (("C10W010.", "C10W050"), "Paquete parcial: Los Ángeles y lajas; no incluye "
               "Micro-Deval ni los demás controles ferroviarios."),
    "PAC-47": (("C03S040",), "Paquete parcial: ultrasonidos de un cordón de soldadura; "
               "verificar aplicabilidad a carril; no incluye neutralización ni geometría."),
    "PAC-49": (("C10W010.",), "Paquete parcial: desgaste Los Ángeles; no incluye batimetría ni colocación."),
    "PAC-50": (("CM1C06HA050",), "Paquete parcial: conformidad y deslizamiento de adoquines de hormigón; "
               "no incluye losas, bordillos ni ensayo in situ."),
    "PAC-51": (("18HAN00020",), "Paquete parcial: nivel de iluminación exterior; "
               "no incluye puesta a tierra ni revisión documental."),
    "PAC-53": (("C07L020",), "Paquete parcial: ensayo de ladrillos para revestir; no incluye inspección de fábrica."),
    "PAC-55": (("C01A010",), "Paquete parcial: propiedades de un aislante rígido; "
               "no incluye conductividad ni inspección de fachada."),
    "PAC-56": (("CM1C07C090",), "Paquete parcial: permeabilidad, agua y viento en carpintería "
               "de aluminio o PVC; no incluye vidrios ni barandillas."),
    "PAC-57": (("C06FI010",), "Paquete parcial: presión y estanqueidad de fontanería; "
               "no incluye ACS ni prevención de legionela."),
    "PAC-59": (("C06EI030", "C06EI040", "C06EI070"), "Paquete parcial: continuidad y resistencia "
               "de tierra y aislamiento de conductores; no incluye diferenciales ni media tensión."),
    "PAC-60": (("18IAJ00020",), "Paquete parcial: prueba de funcionamiento de la red de extinción; "
               "no incluye detección, control de humos ni sellados."),
    "PAC-61": (("C06L010",), "Paquete parcial: funcionamiento de ascensor; "
               "no incluye prueba de carga, inspección OCA, gas ni telecomunicaciones."),
    "PAC-64": (("CM1C02E090",), "Paquete parcial: prueba de carga de viga o forjado; "
               "no incluye pruebas de puentes ni cargas dinámicas."),
    "PAC-66": (("CM1C10R010",), "Prueba de aislamiento acústico y reverberación de edificación; "
               "comprobar muestras y alcance del proyecto."),
    "PAC-67": (("C11F020", "C14T010"), "Paquete para vivienda unifamiliar hasta 400 m²: "
               "estanqueidad al aire y termografía; comprobar condiciones de obra."),
    "ENS-04": (("C02FF140",), "Ensayo de penetración de agua en tres probetas."),
    "ENS-19": (("CM1C06HE050",), "Ensayo de resbaladicidad de baldosas de cemento; "
               "no incluye ensayo in situ sobre pavimento ejecutado."),
    "ENS-22": (("18PAP00060",), "Paquete parcial: adherencia de pintura por tracción; no incluye cuadrícula."),
    "ENS-24": (("CM1C10I200",), "Prueba de presión y estanqueidad de un tramo de abastecimiento."),
    "ENS-26": (("CM1C08S110",), "Estanqueidad con agua en un tramo entre pozos; verificar condiciones del método."),
    "ENS-27": (("C06S060",), "Inspección de saneamiento con cámara; no incluye medición de ovalización."),
    "ENS-30": (("C11F020",), "Ensayo de estanqueidad al aire de vivienda hasta 400 m²."),
    "ENS-31": (("CM1C10R010",), "Prueba acústica en edificación; comprobar muestras y alcance del proyecto."),
}

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
    if (currency != "EUR" or str(row.get("price", "")).strip()
            or identifier not in BC3_ESTIMATES and identifier not in ESTIMATES):
        return row
    expected = TRIALS[identifier].name if identifier in TRIALS else get_catalog_entry(identifier).label
    if row.get("name") != expected:
        return row
    if identifier in BC3_ESTIMATES:
        codes, scope = BC3_ESTIMATES[identifier]
        components = [BC3_COMPONENTS[code] for code in codes]
        price = str(sum((Decimal(component[0]) for component in components), Decimal("0.00")))
        detail = "; ".join(
            f"{code} ({source}): {description}, {amount} EUR/ud"
            for code, (amount, description, source) in zip(codes, components)
        )
        return row | {
            "price": price,
            "price_source": {
                "code": ", ".join(codes), "summary": f"{scope} Componentes: {detail}.",
                "unit": "paquete", "price": price, "trial_name": expected,
                "source": "Estimación orientativa basada en BC3",
                "limitations": "Precios de referencia de 2023-2025; revisar zona, alcance, "
                               "vigencia y costes antes de contratar.",
            },
        }
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
