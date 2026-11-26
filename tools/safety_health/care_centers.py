"""Edición y formato de centros asistenciales procedentes del registro SICESS."""
from __future__ import annotations

from contextlib import closing
import csv
import json
from pathlib import Path
import re
import sqlite3
from typing import TypedDict

from tools.rcd.facilities import haversine_km, search_key


FIELDS = (
    ("nombre", "Nombre"), ("ambito", "Ámbito"), ("dependencia", "Dep. funcional"),
    ("sspa", "Sistema Sanitario Público Andaluz"), ("zona", "Zona Básica de Salud"),
    ("hospitalario", "Es C. Hospitalario"), ("via", "Vía"), ("domicilio", "Domicilio"),
    ("numero", "Nº"), ("escalera", "Escalera"), ("piso", "Piso"), ("puerta", "Puerta"),
    ("cp", "CP"), ("provincia", "Provincia"), ("municipio", "Municipio"),
    ("localidad", "Localidad"), ("telefono", "Teléfono"),
)


class CareCentersState(TypedDict):
    """Estado persistente del texto libre y de las fichas SICESS."""

    centros_asistenciales: str
    centros_asistenciales_modo: str
    centros_asistenciales_texto: str
    centros_asistenciales_datos: list[dict[str, str]]


def restore_sicess_centers(data: object) -> list[dict[str, str]]:
    """Valida las copias seleccionadas sin depender del catálogo actual."""
    if not isinstance(data, list):
        raise ValueError("La selección de centros SICESS debe ser una lista.")
    rows = []
    identifiers = set()
    for row in data:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key, ""), str) for key in ("url", "tipo", *(key for key, _ in FIELDS))
        ):
            raise ValueError("Los datos de los centros SICESS deben ser texto.")
        identifier = row.get("url", "").strip()
        if not identifier or identifier in identifiers or not row.get("nombre", "").strip():
            raise ValueError("La selección de centros SICESS contiene identificadores o nombres inválidos.")
        identifiers.add(identifier)
        rows.append({key: row.get(key, "") for key in ("url", "tipo", *(key for key, _ in FIELDS))})
    return rows


def load_sicess(path: Path) -> list[dict[str, str]]:
    """Lee el catálogo local en modo de solo lectura y valida sus registros JSON."""
    if not path.is_file():
        raise ValueError(f"No se encuentra el catálogo SICESS: {path}")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            records = connection.execute("SELECT url, registro FROM centros ORDER BY url").fetchall()
        rows = []
        for url, text in records:
            record = json.loads(text)
            if not isinstance(record, dict) or not isinstance(record.get("fichaDetalle", {}), dict):
                raise ValueError("Ficha SICESS no válida.")
            detail = record.get("fichaDetalle", {})
            values = {
                "url": url, "tipo": record.get("tipoCentro", ""), "nombre": record.get("nombre", ""),
                "ambito": detail.get("Ámbito", ""), "dependencia": record.get("dependencia", ""),
                "sspa": detail.get("Sistema Sanitario Público Andaluz", ""),
                "zona": detail.get("Zona Básica Salud", ""), "hospitalario": detail.get("Es C.Hospitalario", ""),
                "via": detail.get("Vía", ""), "domicilio": record.get("domicilio", ""),
                "numero": record.get("numero", ""), "escalera": detail.get("Esc", ""),
                "piso": detail.get("Piso", ""), "puerta": detail.get("Pta", ""),
                "cp": record.get("codigoPostal", ""), "provincia": record.get("provincia", ""),
                "municipio": record.get("municipio", ""), "localidad": record.get("localidad", ""),
                "telefono": record.get("telefono", ""),
            }
            rows.append(values)
        return restore_sicess_centers(rows)
    except (OSError, sqlite3.Error, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"No se pudo leer el catálogo SICESS {path}: {exc}") from exc


def municipality_key(name: str) -> str:
    """Normaliza artículos pospuestos presentes en los catálogos municipales."""
    match = re.fullmatch(r"\s*(.+?)\s*\((el|la|los|las)\)\s*", name, re.IGNORECASE)
    if match:
        name = f"{match.group(2)} {match.group(1)}"
    parts = name.split(",", 1)
    if len(parts) == 2 and search_key(parts[1]) in {"el", "la", "los", "las"}:
        name = f"{parts[1]} {parts[0]}"
    return search_key(name)


def rcd_municipality_points(path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    """Calcula referencias municipales aproximadas a partir de instalaciones RCD."""
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            records = connection.execute(
                "SELECT municipality, province_access, latitude, longitude FROM facilities "
                "WHERE latitude != '' AND longitude != ''"
            ).fetchall()
        points: dict[tuple[str, str], list[tuple[float, float]]] = {}
        for municipality, province, latitude, longitude in records:
            point = (float(latitude), float(longitude))
            haversine_km(*point, *point)
            key = (municipality_key(municipality), search_key(province.split("/", 1)[0]))
            points.setdefault(key, []).append(point)
        return {
            key: (sum(point[0] for point in values) / len(values),
                  sum(point[1] for point in values) / len(values))
            for key, values in points.items()
        }
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise ValueError(f"No se pudieron leer las coordenadas del catálogo RCD {path}: {exc}") from exc


def geonames_municipality_points(path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    """Carga las referencias municipales públicas de GeoNames."""
    try:
        points = {}
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames != ["codigo_ine", "municipio", "provincia", "latitud", "longitud", "geonames_id"]:
                raise ValueError("Cabecera de coordenadas no válida.")
            for row in reader:
                if None in row or any(not value for value in row.values()):
                    raise ValueError("Fila de coordenadas incompleta.")
                key = (municipality_key(row["municipio"]), search_key(row["provincia"]))
                point = (float(row["latitud"]), float(row["longitud"]))
                haversine_km(*point, *point)
                if key in points:
                    raise ValueError(f"Municipio duplicado: {row['municipio']}, {row['provincia']}.")
                points[key] = point
        if not points:
            raise ValueError("El catálogo de coordenadas está vacío.")
        return points
    except (OSError, UnicodeError, csv.Error, TypeError, ValueError) as exc:
        raise ValueError(f"No se pudieron leer las coordenadas GeoNames {path}: {exc}") from exc


def sicess_distance(row: dict[str, str], origins: list[tuple[float, float]],
                    points: dict[tuple[str, str], tuple[float, float]]) -> float | None:
    """Calcula distancia aproximada entre municipios, nunca una ruta al centro."""
    point = points.get((municipality_key(row["municipio"]), search_key(row["provincia"])))
    if point is None or not origins:
        return None
    return min(haversine_km(*origin, *point) for origin in origins)


def restore_centers(data: dict) -> list[dict[str, str]]:
    """Valida las filas SICESS y el modo sin alterar proyectos antiguos."""
    if data.get("centros_asistenciales_modo", "texto") not in ("texto", "tabla"):
        raise ValueError("El modo de centros asistenciales no es válido.")
    if not isinstance(data.get("centros_asistenciales_texto", ""), str):
        raise ValueError("El texto de centros asistenciales no es válido.")
    rows = data.get("centros_asistenciales_datos", [])
    if not isinstance(rows, list):
        raise ValueError("La tabla de centros asistenciales no es válida.")
    result = []
    for row in rows:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key, ""), str) for key, _ in FIELDS
        ):
            raise ValueError("Los datos SICESS de los centros deben ser texto.")
        result.append({key: row.get(key, "") for key, _ in FIELDS})
    return result


def format_centers(rows: list[dict[str, str]]) -> str:
    """Resume cada centro con nombre, dirección postal y teléfono."""
    blocks = []
    for row in rows:
        values = {key: row.get(key, "").strip() for key, _ in FIELDS}
        street = " ".join(filter(None, (values["via"], values["domicilio"])))
        address = ", ".join(filter(None, (
            street, values["numero"],
            f"Esc. {values['escalera']}" if values["escalera"] else "",
            f"Piso {values['piso']}" if values["piso"] else "",
            f"Pta. {values['puerta']}" if values["puerta"] else "",
            values["cp"], values["localidad"] or values["municipio"], values["provincia"],
        )))
        lines = [values["nombre"].upper(), address]
        if values["telefono"]:
            lines.append(f"Teléfono: {values['telefono']}")
        block = "\n".join(line for line in lines if line)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def format_sicess_centers(rows: list[dict[str, str]]) -> str:
    """Incluye el tipo de cada centro, además de su dirección y teléfono."""
    blocks = []
    for row in rows:
        block = format_centers([row])
        if row.get("tipo", "").strip():
            block += f"\nTipo de centro: {row['tipo'].strip()}"
        blocks.append(block)
    return "\n\n".join(blocks)


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
