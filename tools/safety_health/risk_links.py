"""Vinculaciones preventivas iniciales por familia de unidades BCCA."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re

FAMILY_RISK_LINKS_PATH = (
    Path(__file__).resolve().parent / "datos" / "riesgos_familia_bcca2023.csv"
)
_FAMILY_CODE = re.compile(r"\d{2}[A-Z]{3}")
_CATEGORY = re.compile(r"(?:general|demolition|height|electrical|excavation|machinery)")
_RISK_CODE = re.compile(r"R-[A-Z0-9-]+")
_MEASURE_CODE = re.compile(r"MP-[A-Z0-9-]+")


@dataclass(frozen=True)
class FamilyRiskLink:
    """Conserva el perfil de riesgos propuesto para una familia BCCA."""

    unit_count: int
    source_categories: tuple[str, ...]
    risks: tuple[str, ...]
    measures: tuple[str, ...]


def _csv_value(row: dict[str, str | None], name: str) -> str:
    """Devuelve un campo CSV no vacío o informa de la fila inválida."""
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Falta el campo {name}.")
    return value.strip()


def _csv_codes(
    row: dict[str, str | None], name: str, pattern: re.Pattern[str],
) -> tuple[str, ...]:
    """Lee códigos separados por punto y coma y rechaza valores repetidos o inválidos."""
    values = tuple(value.strip() for value in _csv_value(row, name).split(";"))
    if not values or any(not pattern.fullmatch(value) for value in values):
        raise ValueError(f"Hay códigos no válidos en el campo {name}.")
    if len(values) != len(set(values)):
        raise ValueError(f"Hay códigos duplicados en el campo {name}.")
    return values


def load_family_risk_links() -> dict[str, FamilyRiskLink]:
    """Carga y valida la propuesta de riesgos BCCA, agrupada por código de cinco caracteres."""
    links: dict[str, FamilyRiskLink] = {}
    required = {
        "familia_bcca", "unidades_bcca", "categorias_origen", "riesgos", "medidas",
    }
    try:
        with FAMILY_RISK_LINKS_PATH.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ValueError("El archivo de vínculos por familia tiene columnas no válidas.")
            for row in reader:
                family = _csv_value(row, "familia_bcca")
                if not _FAMILY_CODE.fullmatch(family):
                    raise ValueError(f"El código de familia BCCA {family} no es válido.")
                if family in links:
                    raise ValueError(f"El código de familia BCCA {family} está duplicado.")
                try:
                    unit_count = int(_csv_value(row, "unidades_bcca"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"El número de unidades de la familia {family} no es válido."
                    ) from exc
                if unit_count <= 0:
                    raise ValueError(f"La familia {family} no contiene unidades BCCA.")
                categories = _csv_codes(row, "categorias_origen", _CATEGORY)
                if "general" not in categories:
                    raise ValueError(f"Falta el perfil general de la familia {family}.")
                links[family] = FamilyRiskLink(
                    unit_count,
                    categories,
                    _csv_codes(row, "riesgos", _RISK_CODE),
                    _csv_codes(row, "medidas", _MEASURE_CODE),
                )
        if not links:
            raise ValueError("El archivo de vínculos por familia está vacío.")
    except (OSError, csv.Error, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"No se pudieron cargar los vínculos de riesgos BCCA: {exc}") from exc
    return links


def family_risk_link_for_code(
    code: str, links: dict[str, FamilyRiskLink],
) -> FamilyRiskLink | None:
    """Busca la propuesta por el prefijo BCCA de dos cifras y tres letras."""
    normalized = re.sub(r"\s+", "", code).upper()
    family = normalized[:5]
    return links.get(family) if _FAMILY_CODE.fullmatch(family) else None
