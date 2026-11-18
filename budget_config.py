"""Metadatos y constantes compartidas por las interfaces de la aplicación."""
from __future__ import annotations

from datetime import date


APPLICATION_NAME = "Gestor de Proyectos"
APPLICATION_VERSION = "1.7.0"
APPLICATION_RELEASE_DATE = date(2026, 11, 17)
REPOSITORY_URL = "https://github.com/mcobosb/gestor-de-proyectos"
BUY_ME_A_COFFEE_URL = "https://buymeacoffee.com/mcobosb"
REFERENCE_PRICE_DATABASES = (
    (
        "Andalucía (JA) — BCCA",
        "https://www.juntadeandalucia.es/organismos/viviendajuventudyordenaciondelterritorio/areas/vivienda-rehabilitacion/planes-instrumentos/paginas/vivienda-bcca.html",
    ),
    (
        "Extremadura — Base de precios",
        "https://www.juntaex.es/w/base-precios-construccion-2026",
    ),
    (
        "Madrid — Base de Datos de la Construcción",
        "https://www.comunidad.madrid/vivienda/base-datos-construccion",
    ),
)

CONCEPT_TYPES = {
    "chapter": "▣ Capítulo",
    "0": "◆ Partida",
    "aux": "◇ Auxiliar",
    "1": "♟ Mano de obra",
    "2": "⚙ Maquinaria",
    "3": "▧ Material",
    "%": "% Porcentaje",
}

DUPLICATE_ITEM_MARKER = "*"
