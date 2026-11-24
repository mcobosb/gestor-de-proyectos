"""Centros de gestión: copias del proyecto y catálogo local reutilizable."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, field
import csv
import hashlib
import math
from pathlib import Path
import re
import sqlite3
import unicodedata
from uuid import uuid4


FACILITY_FIELDS = {
    "name": "Instalación",
    "holder": "Titular",
    "address": "Dirección",
    "province_access": "Provincia / Acceso",
    "nima": "Código NIMA",
    "authorization": "Autorización / Operación",
}
_COLUMNS = (
    "id", *FACILITY_FIELDS, "ler_codes", "operations", "municipality",
    "latitude", "longitude",
)


def search_key(text: str) -> str:
    """Compara nombres y filtros sin distinguir acentos, mayúsculas o espacios repetidos."""
    return " ".join("".join(
        character for character in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(character)
    ).split())


@dataclass(frozen=True)
class WasteFacility:
    """Identifica una instalación sin acreditar sus autorizaciones ni tratamientos."""

    name: str
    holder: str = ""
    address: str = ""
    province_access: str = ""
    nima: str = ""
    authorization: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    ler_codes: str = ""
    operations: str = ""
    municipality: str = ""
    latitude: str = ""
    longitude: str = ""

    @classmethod
    def from_dict(cls, data: object) -> WasteFacility:
        """Valida las copias guardadas, conservando los ceros iniciales del NIMA."""
        if not isinstance(data, dict) or set(data).difference(_COLUMNS):
            raise ValueError("Los datos del centro de gestión deben ser un objeto válido.")
        values = {}
        for key in _COLUMNS:
            value = data.get(key, "")
            if not isinstance(value, str):
                raise ValueError(f"El campo {FACILITY_FIELDS.get(key, key)} debe ser texto.")
            values[key] = value.strip()
        if not re.fullmatch(r"[0-9a-f]{32}", values["id"]):
            raise ValueError("El identificador del centro de gestión no es válido.")
        if not values["name"]:
            raise ValueError("Indica el nombre de la instalación.")
        if values["nima"] and not re.fullmatch(r"[0-9]{10}", values["nima"]):
            raise ValueError("El código NIMA debe contener diez dígitos o quedar pendiente.")
        return cls(**values)

    def same_installation(self, other: WasteFacility) -> bool:
        """Detecta duplicados por NIMA o por identificación y emplazamiento."""
        if self.nima and other.nima:
            return self.nima == other.nima
        return all(
            search_key(getattr(self, key)) == search_key(getattr(other, key))
            for key in ("name", "holder", "address", "province_access")
        )


def facilities_from_data(data: object) -> list[WasteFacility]:
    """Restaura una selección sin identificadores o instalaciones duplicados."""
    if not isinstance(data, list):
        raise ValueError("Los centros de gestión deben ser una lista.")
    facilities: list[WasteFacility] = []
    for row in data:
        facility = WasteFacility.from_dict(row)
        if any(facility.id == previous.id or facility.same_installation(previous)
               for previous in facilities):
            raise ValueError(f"Centro de gestión duplicado: {facility.name}.")
        facilities.append(facility)
    return facilities


def _utm_to_wgs84(easting: float, northing: float, zone: int = 30, *, northern: bool = True) -> tuple[float, float]:
    """Convierte coordenadas UTM WGS84 a latitud y longitud sin servicios externos."""
    semi_major = 6378137.0
    eccentricity_sq = 0.00669438002290
    eccentricity_prime_sq = eccentricity_sq / (1.0 - eccentricity_sq)
    scale = 0.9996
    x = easting - 500000.0
    y = northing if northern else northing - 10000000.0
    longitude_origin = (zone - 1) * 6.0 - 180.0 + 3.0
    m = y / scale
    mu = m / (
        semi_major * (
            1.0 - eccentricity_sq / 4.0
            - 3.0 * eccentricity_sq ** 2 / 64.0
            - 5.0 * eccentricity_sq ** 3 / 256.0
        )
    )
    e1 = (1.0 - math.sqrt(1.0 - eccentricity_sq)) / (
        1.0 + math.sqrt(1.0 - eccentricity_sq)
    )
    latitude = (
        mu + (3.0 * e1 / 2.0 - 27.0 * e1 ** 3 / 32.0) * math.sin(2.0 * mu)
        + (21.0 * e1 ** 2 / 16.0 - 55.0 * e1 ** 4 / 32.0) * math.sin(4.0 * mu)
        + 151.0 * e1 ** 3 / 96.0 * math.sin(6.0 * mu)
        + 1097.0 * e1 ** 4 / 512.0 * math.sin(8.0 * mu)
    )
    sin_lat = math.sin(latitude)
    cos_lat = math.cos(latitude)
    tan_lat = math.tan(latitude)
    n = semi_major / math.sqrt(1.0 - eccentricity_sq * sin_lat ** 2)
    t = tan_lat ** 2
    c = eccentricity_prime_sq * cos_lat ** 2
    a = cos_lat * x / n
    m = semi_major * (
        (1.0 - eccentricity_sq / 4.0 - 3.0 * eccentricity_sq ** 2 / 64.0
         - 5.0 * eccentricity_sq ** 3 / 256.0) * latitude
        - (3.0 * eccentricity_sq / 8.0 + 3.0 * eccentricity_sq ** 2 / 32.0
           + 45.0 * eccentricity_sq ** 3 / 1024.0) * math.sin(2.0 * latitude)
        + (15.0 * eccentricity_sq ** 2 / 256.0
           + 45.0 * eccentricity_sq ** 3 / 1024.0) * math.sin(4.0 * latitude)
        - 35.0 * eccentricity_sq ** 3 / 3072.0 * math.sin(6.0 * latitude)
    )
    r = semi_major * (1.0 - eccentricity_sq) / (
        (1.0 - eccentricity_sq * sin_lat ** 2) ** 1.5
    )
    latitude -= (
        x ** 2 * scale ** 2 * tan_lat
        / (2.0 * r * n)
        * (1.0 - x ** 2 * scale ** 2 / (12.0 * n ** 2)
           * (5.0 + 3.0 * t + 10.0 * c - 4.0 * c ** 2 - 9.0 * eccentricity_prime_sq)
           + x ** 4 * scale ** 4 / (360.0 * n ** 4)
           * (61.0 + 90.0 * t + 298.0 * c + 45.0 * t ** 2
              - 252.0 * eccentricity_prime_sq - 3.0 * c ** 2))
    )
    longitude = (
        (a - (1.0 - t + c) * a ** 3 / 6.0
         + (5.0 - 18.0 * t + t ** 2 + 72.0 * c - 58.0 * eccentricity_prime_sq)
         * a ** 5 / 120.0)
        / cos_lat
    )
    longitude = math.degrees(longitude_origin * math.pi / 180.0 + longitude)
    return math.degrees(latitude), longitude


def parse_utm_coordinates(text: str) -> tuple[float, float] | None:
    """Extrae E/N de una descripción UTM y devuelve latitud y longitud WGS84."""
    match = re.search(
        r"UTM\s+(\d+)N\s+\(EPSG:\d+\):\s*E=([0-9.,]+)\s*m,\s*N=([0-9.,]+)\s*m",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    easting = float(match[2].replace(",", "."))
    northing = float(match[3].replace(",", "."))
    try:
        from pyproj import Transformer

        transformer = Transformer.from_crs(
            f"EPSG:258{int(match[1])}",
            "EPSG:4326",
            always_xy=True,
        )
        longitude, latitude = transformer.transform(easting, northing)
        return latitude, longitude
    except (ImportError, RuntimeError, ValueError):
        return _utm_to_wgs84(easting, northing, int(match[1]))


def haversine_km(
    latitude1: float, longitude1: float, latitude2: float, longitude2: float,
) -> float:
    """Calcula la distancia ortodrómica entre dos coordenadas WGS84."""
    if not all(math.isfinite(value) for value in (
        latitude1, longitude1, latitude2, longitude2,
    )) or not (
        -90 <= latitude1 <= 90 and -90 <= latitude2 <= 90
        and -180 <= longitude1 <= 180 and -180 <= longitude2 <= 180
    ):
        raise ValueError("Coordenadas geográficas fuera de rango.")
    lat1, lat2 = math.radians(latitude1), math.radians(latitude2)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(longitude2 - longitude1)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 6371.0088 * 2.0 * math.asin(math.sqrt(value))


def municipality_coordinates(
    facilities: list[WasteFacility],
    municipality: str,
    province: str = "",
) -> tuple[float, float] | None:
    """Obtiene el punto medio de los centros conocidos del municipio seleccionado."""
    municipality_key = search_key(municipality)
    province_key = search_key(province)
    points = []
    for facility in facilities:
        if search_key(facility.municipality) != municipality_key:
            continue
        if province_key and province_key not in search_key(facility.province_access):
            continue
        if facility.latitude and facility.longitude:
            points.append((float(facility.latitude), float(facility.longitude)))
    if not points:
        return None
    return sum(point[0] for point in points) / len(points), (
        sum(point[1] for point in points) / len(points)
    )


class FacilityCatalog:
    """Persiste centros entre proyectos mediante transacciones SQLite locales."""

    def __init__(self, path: Path):
        """Recibe la ruta del catálogo sin crear archivos hasta el primer guardado."""
        self.path = path

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        """Crea el catálogo histórico y añade columnas sin perder instalaciones existentes."""
        connection.execute(
            "CREATE TABLE IF NOT EXISTS facilities ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, holder TEXT NOT NULL, "
            "address TEXT NOT NULL, province_access TEXT NOT NULL, nima TEXT NOT NULL, "
            "authorization TEXT NOT NULL)"
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(facilities)")
        }
        additions = {
            "ler_codes": "TEXT NOT NULL DEFAULT ''",
            "operations": "TEXT NOT NULL DEFAULT ''",
            "municipality": "TEXT NOT NULL DEFAULT ''",
            "latitude": "TEXT NOT NULL DEFAULT ''",
            "longitude": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE facilities ADD COLUMN {name} {definition}"
                )

    @staticmethod
    def _read(connection: sqlite3.Connection) -> list[WasteFacility]:
        """Valida las filas existentes antes de mostrarlas o modificarlas."""
        FacilityCatalog._ensure_schema(connection)
        rows = connection.execute(
            "SELECT id, name, holder, address, province_access, nima, authorization, "
            "ler_codes, operations, municipality, latitude, longitude "
            "FROM facilities ORDER BY name COLLATE NOCASE, id"
        )
        return facilities_from_data([dict(zip(_COLUMNS, row)) for row in rows])

    def load(self) -> list[WasteFacility]:
        """Lee el catálogo; solo la ausencia del archivo equivale a un catálogo nuevo."""
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path)) as connection:
            return self._read(connection)

    def import_csv(self, csv_path: Path, *, replace: bool = False) -> int:
        """Importa el catálogo CSV oficial sin duplicar NIMA ni modificar proyectos."""
        csv_path = Path(csv_path)
        required = {
            "idInstalacion", "denominacionEmpresa", "denominacionInstalacion",
            "nima", "numInscripcion", "codLer", "tiposOperacion",
            "operacionesPorLER", "provincia", "municipio", "coordenadas",
        }
        facilities = []
        seen_nima = set()
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
                raise ValueError("El CSV no tiene el formato esperado del catálogo de centros RCD.")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("El CSV contiene filas incompletas.")
                nima = row["nima"].strip()
                if not re.fullmatch(r"[0-9]{10}", nima):
                    raise ValueError(f"Código NIMA no válido en la fila {reader.line_num}.")
                if nima in seen_nima:
                    continue
                seen_nima.add(nima)
                name = row["denominacionInstalacion"].strip()
                municipality = row["municipio"].strip()
                province = row["provincia"].strip()
                if not name:
                    raise ValueError(f"Instalación sin nombre en la fila {reader.line_num}.")
                point = parse_utm_coordinates(row["coordenadas"])
                source_key = f"{nima}:{row['idInstalacion'].strip()}:{name}:{municipality}"
                identifier = hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:32]
                ler_codes = ", ".join(code.strip() for code in row["codLer"].split(",") if code.strip())
                operations = ", ".join(
                    code.strip() for code in row["tiposOperacion"].split(",") if code.strip()
                )
                authorization = " | ".join(part for part in (
                    row["numInscripcion"].strip(),
                    operations,
                ) if part)
                facilities.append(WasteFacility(
                    name=name,
                    holder=row["denominacionEmpresa"].strip(),
                    address=f"{municipality}, {province}",
                    province_access=f"{province} / {municipality}",
                    nima=nima,
                    authorization=authorization,
                    id=identifier,
                    ler_codes=ler_codes,
                    operations=operations,
                    municipality=municipality,
                    latitude="" if point is None else f"{point[0]:.8f}",
                    longitude="" if point is None else f"{point[1]:.8f}",
                ))
        if not facilities:
            raise ValueError("El CSV no contiene instalaciones.")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_schema(connection)
            if replace:
                connection.execute("DELETE FROM facilities")
            existing = self._read(connection)
            by_nima = {row.nima: row for row in existing if row.nima}
            imported = 0
            for facility in facilities:
                previous = by_nima.get(facility.nima)
                if previous is not None:
                    facility = WasteFacility(
                        name=facility.name,
                        holder=facility.holder or previous.holder,
                        address=facility.address,
                        province_access=facility.province_access,
                        nima=facility.nima,
                        authorization=facility.authorization,
                        id=previous.id,
                        ler_codes=facility.ler_codes,
                        operations=facility.operations,
                        municipality=facility.municipality,
                        latitude=facility.latitude,
                        longitude=facility.longitude,
                    )
                connection.execute(
                    "INSERT INTO facilities (id, name, holder, address, province_access, nima, authorization, "
                    "ler_codes, operations, municipality, latitude, longitude) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name, holder=excluded.holder, address=excluded.address, "
                    "province_access=excluded.province_access, nima=excluded.nima, "
                    "authorization=excluded.authorization, ler_codes=excluded.ler_codes, "
                    "operations=excluded.operations, municipality=excluded.municipality, "
                    "latitude=excluded.latitude, longitude=excluded.longitude",
                    tuple(getattr(facility, key) for key in _COLUMNS),
                )
                imported += 1
        return imported

    def save(self, facility: WasteFacility, *, expected: WasteFacility | None = None) -> WasteFacility:
        """Inserta o edita una instalación sin duplicar ni pisar ediciones concurrentes."""
        facility = WasteFacility.from_dict(asdict(facility))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_schema(connection)
            existing = self._read(connection)
            current = next((row for row in existing if row.id == facility.id), None)
            if current != expected:
                raise ValueError("El centro ha cambiado en el catálogo. Actualiza la lista antes de editarlo.")
            duplicate = next((row for row in existing if row.id != facility.id
                              and facility.same_installation(row)), None)
            if duplicate is not None:
                raise ValueError(f"La instalación ya figura en el catálogo: {duplicate.name}. Edita esa ficha.")
            connection.execute(
                "INSERT INTO facilities (id, name, holder, address, province_access, nima, authorization, "
                "ler_codes, operations, municipality, latitude, longitude) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, holder=excluded.holder, address=excluded.address, "
                "province_access=excluded.province_access, nima=excluded.nima, "
                "authorization=excluded.authorization, ler_codes=excluded.ler_codes, "
                "operations=excluded.operations, municipality=excluded.municipality, "
                "latitude=excluded.latitude, longitude=excluded.longitude",
                tuple(getattr(facility, key) for key in _COLUMNS),
            )
        return facility
