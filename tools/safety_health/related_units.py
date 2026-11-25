"""Carga las relaciones orientativas entre partidas BCCA y unidades de Seguridad y Salud."""
from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re
import unicodedata

DATA_ROOT = Path(__file__).resolve().parent / "datos"
FAMILY_LINKS_PATH = DATA_ROOT / "vinculos_familia_bcca2023.csv"
SAFETY_CATALOG_PATH = DATA_ROOT / "catalogo_ss_vinculado_bcca2023.csv"
_FAMILY_CODE = re.compile(r"\d{2}[A-Z]{3}")
_DURATION_VARIANT = re.compile(r"\bdurac(?:ion)?\b.*$")
_MEASUREMENT_VARIANT = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?\s*"
    r"(?:m2|m3|mm|cm|km|m)\b|"
    r"\b\d+(?:[.,]\d+)?\s*(?:m2|m3|mm|cm|km|m|h|horas?|mes(?:es)?)\b"
)
_PRODUCT_TYPE_PATTERNS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "19LAA": (
        ("adaptacion-local-aseos", re.compile(r"^adaptacion provisional local aseos")),
    ),
    "19LAS": (
        ("adaptacion-local-primeros-auxilios",
         re.compile(r"^adaptacion provisional local (?:prim auxilios|p auxilios)")),
    ),
    "19LIA": (
        ("instalacion-local-aseos", re.compile(r"^instalacion provisional local aseos")),
    ),
    "19SIC": (
        ("proteccion-auditiva",
         re.compile(r"^(?:protector auditivo|par tapones antirr)")),
        ("gafas-proteccion-ocular", re.compile(r"^gafas? ")),
        ("pantallas-soldadura", re.compile(r"^pantalla soldadura")),
        ("proteccion-respiratoria",
         re.compile(r"^(?:mascarilla|semimascara respir)")),
        ("filtros-semimascaras", re.compile(r"^filtro semimascara")),
        ("equipos-autonomos-respiracion",
         re.compile(r"^equipo autonomo respiracion")),
        ("cascos-seguridad", re.compile(r"^casco seg")),
    ),
    "19SIM": (
        ("guantes", re.compile(r"^par guantes")),
    ),
    "19SIP": (
        ("calzado-seguridad", re.compile(r"^par (?:de )?(?:botas|zapatos)")),
    ),
    "19SIT": (
        ("arneses-anticaidas", re.compile(r"^arnes anticaidas")),
    ),
}


@dataclass(frozen=True)
class SafetyFamilyLink:
    """Representa una familia de Seguridad y Salud relacionada con una familia de obra."""

    code: str
    category: str
    criterion: str


@dataclass(frozen=True)
class RelatedSafetyItem:
    """Describe una unidad concreta del catálogo BCCA 2023 de Seguridad y Salud."""

    code: str
    unit: str
    category: str
    subcategory: str
    summary: str
    price: Decimal


def _csv_value(row: dict[str, str | None], name: str) -> str:
    """Devuelve un campo CSV no vacío o informa de la fila inválida."""
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Falta el campo {name}.")
    return value.strip()


def load_related_safety_data() -> tuple[
    dict[str, tuple[SafetyFamilyLink, ...]],
    dict[str, tuple[RelatedSafetyItem, ...]],
]:
    """Carga las familias relacionadas y las unidades del catálogo que las desarrollan."""
    family_links: dict[str, list[SafetyFamilyLink]] = {}
    catalog: dict[str, list[RelatedSafetyItem]] = {}
    seen_links: set[tuple[str, str]] = set()
    try:
        with FAMILY_LINKS_PATH.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            required = {"familia_obra", "familia_ss", "categoria_ss", "criterio"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ValueError("El archivo de vínculos tiene columnas no válidas.")
            for row in reader:
                work_family = _csv_value(row, "familia_obra")
                safety_family = _csv_value(row, "familia_ss")
                if not _FAMILY_CODE.fullmatch(work_family) or not _FAMILY_CODE.fullmatch(safety_family):
                    raise ValueError("Hay un código de familia no válido.")
                pair = work_family, safety_family
                if pair in seen_links:
                    raise ValueError(f"El vínculo {work_family} → {safety_family} está duplicado.")
                seen_links.add(pair)
                family_links.setdefault(work_family, []).append(SafetyFamilyLink(
                    safety_family,
                    _csv_value(row, "categoria_ss"),
                    _csv_value(row, "criterio"),
                ))

        with SAFETY_CATALOG_PATH.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            required = {
                "familia_ss", "codigo", "unidad", "categoria", "subcategoria",
                "resumen", "precio",
            }
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise ValueError("El catálogo vinculado tiene columnas no válidas.")
            for row in reader:
                family = _csv_value(row, "familia_ss")
                code = _csv_value(row, "codigo")
                if not _FAMILY_CODE.fullmatch(family) or not code.startswith(family):
                    raise ValueError(f"El código {code} no corresponde a la familia {family}.")
                price = Decimal(_csv_value(row, "precio"))
                if not price.is_finite() or price < 0:
                    raise ValueError(f"El precio de {code} no es válido.")
                catalog.setdefault(family, []).append(RelatedSafetyItem(
                    code,
                    _csv_value(row, "unidad"),
                    _csv_value(row, "categoria"),
                    _csv_value(row, "subcategoria"),
                    _csv_value(row, "resumen"),
                    price,
                ))
        if not family_links or not catalog:
            raise ValueError("Los archivos de vínculos o catálogo están vacíos.")
        missing_families = {
            link.code
            for links in family_links.values()
            for link in links
            if link.code not in catalog
        }
        if missing_families:
            raise ValueError(
                "Faltan unidades del catálogo para las familias: "
                + ", ".join(sorted(missing_families))
            )
    except (OSError, csv.Error, KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise ValueError(f"No se pudieron cargar las unidades vinculadas: {exc}") from exc
    return (
        {
            family: tuple(sorted(links, key=lambda link: link.code))
            for family, links in family_links.items()
        },
        {
            family: tuple(sorted(items, key=lambda item: item.code))
            for family, items in catalog.items()
        },
    )


def related_items_for_work_families(
    work_families: Iterable[str],
    family_links: dict[str, tuple[SafetyFamilyLink, ...]],
    catalog: dict[str, tuple[RelatedSafetyItem, ...]],
) -> tuple[RelatedSafetyItem, ...]:
    """Devuelve unidades vinculadas a las familias de obra sin repetir códigos."""
    items: dict[str, RelatedSafetyItem] = {}
    for work_family in set(work_families):
        for link in family_links.get(work_family, ()):
            for item in catalog.get(link.code, ()):
                items.setdefault(item.code, item)
    return tuple(sorted(items.values(), key=lambda item: item.code))


def _alternative_key(item: RelatedSafetyItem) -> tuple[str, str, str, str]:
    """Agrupa variantes del mismo tipo sin mezclar protecciones distintas."""
    summary = unicodedata.normalize("NFKD", item.summary)
    summary = summary.encode("ascii", "ignore").decode("ascii").casefold()
    summary = _DURATION_VARIANT.sub("", summary)
    summary = _MEASUREMENT_VARIANT.sub(" ", summary)
    summary = " ".join(re.sub(r"[^a-z0-9]+", " ", summary).split())
    family = item.code[:5]
    if family == "19SCB":
        summary = re.sub(r"\b(?:madera|metalica)\b", " ", summary)
    elif family == "19SSA":
        if summary.startswith("valla metalica para acotamiento de espacios"):
            summary = "valla para acotamiento de espacios"
        elif summary.startswith("cerramiento prov obra panel"):
            summary = "cerramiento provisional obra panel"
    elif family == "19SSS":
        if "salv" in summary:
            summary = "senal salvamento"
        else:
            summary = re.sub(
                r"\b(?:senal|metalica|metalico|pvc|con|sin|soporte|met|sop)\b",
                " ",
                summary,
            )
    summary = " ".join(summary.split())
    for group, pattern in _PRODUCT_TYPE_PATTERNS.get(item.code[:5], ()):
        if pattern.match(summary):
            summary = group
            break
    return item.code[:5], item.category.casefold(), item.unit.casefold(), summary


def related_alternative_groups_for_work_families(
    work_families: Iterable[str],
    family_links: dict[str, tuple[SafetyFamilyLink, ...]],
    catalog: dict[str, tuple[RelatedSafetyItem, ...]],
) -> tuple[tuple[RelatedSafetyItem, ...], ...]:
    """Agrupa unidades equivalentes por tipo y conserva separadas las distintas."""
    items = {
        item.code: item
        for item in related_items_for_work_families(work_families, family_links, catalog)
    }
    groups: dict[tuple[str, str, str, str], list[RelatedSafetyItem]] = {}
    for item in items.values():
        key = _alternative_key(item)
        if key[3]:
            groups.setdefault(key, []).append(item)
        else:
            groups.setdefault((*key[:3], item.code), []).append(item)
    return tuple(
        tuple(sorted(group, key=lambda item: item.code))
        for group in sorted(groups.values(), key=lambda group: min(item.code for item in group))
    )
