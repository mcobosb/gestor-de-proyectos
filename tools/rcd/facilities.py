"""Centros de gestión: copias del proyecto y catálogo local reutilizable."""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, field
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
_COLUMNS = ("id", *FACILITY_FIELDS)


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


class FacilityCatalog:
    """Persiste centros entre proyectos mediante transacciones SQLite locales."""

    def __init__(self, path: Path):
        """Recibe la ruta del catálogo sin crear archivos hasta el primer guardado."""
        self.path = path

    @staticmethod
    def _read(connection: sqlite3.Connection) -> list[WasteFacility]:
        """Valida las filas existentes antes de mostrarlas o modificarlas."""
        rows = connection.execute(
            "SELECT id, name, holder, address, province_access, nima, authorization "
            "FROM facilities ORDER BY name COLLATE NOCASE, id"
        )
        return facilities_from_data([dict(zip(_COLUMNS, row)) for row in rows])

    def load(self) -> list[WasteFacility]:
        """Lee el catálogo; solo la ausencia del archivo equivale a un catálogo nuevo."""
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path)) as connection:
            return self._read(connection)

    def save(self, facility: WasteFacility, *, expected: WasteFacility | None = None) -> WasteFacility:
        """Inserta o edita una instalación sin duplicar ni pisar ediciones concurrentes."""
        facility = WasteFacility.from_dict(asdict(facility))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS facilities ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, holder TEXT NOT NULL, "
                "address TEXT NOT NULL, province_access TEXT NOT NULL, nima TEXT NOT NULL, "
                "authorization TEXT NOT NULL)"
            )
            existing = self._read(connection)
            current = next((row for row in existing if row.id == facility.id), None)
            if current != expected:
                raise ValueError("El centro ha cambiado en el catálogo. Actualiza la lista antes de editarlo.")
            duplicate = next((row for row in existing if row.id != facility.id
                              and facility.same_installation(row)), None)
            if duplicate is not None:
                raise ValueError(f"La instalación ya figura en el catálogo: {duplicate.name}. Edita esa ficha.")
            connection.execute(
                "INSERT INTO facilities (id, name, holder, address, province_access, nima, authorization) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, holder=excluded.holder, address=excluded.address, "
                "province_access=excluded.province_access, nima=excluded.nima, "
                "authorization=excluded.authorization",
                tuple(getattr(facility, key) for key in _COLUMNS),
            )
        return facility
