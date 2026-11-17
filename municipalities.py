"""Catalogo de municipios y su provincia y comunidad a partir de CMUN y CPRO."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
import unicodedata

from app_paths import application_directory


@dataclass(frozen=True)
class Municipality:
    """Conserva el codigo de ambas tablas y los nombres oficiales relacionados."""

    code: str
    name: str
    province: str
    autonomous_community: str

    @property
    def label(self) -> str:
        """Distingue los municipios homonimos mediante su provincia."""
        return f"{self.name} ({self.province})"


def municipality_search_key(text: str) -> str:
    """Normaliza mayusculas y tildes sin perder las letras de los nombres."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    ).strip()


@lru_cache(maxsize=1)
def load_municipalities() -> tuple[Municipality, ...]:
    """Enlaza los CSV Windows-1252 por CPRO y ordena los municipios alfabeticamente."""
    directory = application_directory(__file__) / "tools"
    try:
        provinces = {}
        with (directory / "CPRO.csv").open(encoding="cp1252", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames != ["CPRO", "Provincia", "Comunidad Aut\u00f3noma"]:
                raise ValueError("La cabecera de CPRO.csv no es valida.")
            for row in reader:
                if None in row or None in row.values():
                    raise ValueError(f"Fila incompleta en CPRO.csv: {row}.")
                code = row["CPRO"].strip().zfill(2)
                province = row["Provincia"].strip()
                community = row["Comunidad Aut\u00f3noma"].strip()
                if not code.isdigit() or len(code) != 2 or not province or not community:
                    raise ValueError(f"Provincia incompleta o no valida: {row}.")
                if code in provinces:
                    raise ValueError(f"Codigo de provincia duplicado: {code}.")
                provinces[code] = (province, community)
        municipalities = {}
        with (directory / "CMUN.csv").open(encoding="cp1252", newline="") as handle:
            next(handle, None)
            reader = csv.DictReader(handle, strict=True)
            if reader.fieldnames != ["CPRO", "CMUN", "NOMBRE"]:
                raise ValueError("La cabecera de CMUN.csv no es valida.")
            for row in reader:
                if None in row or None in row.values():
                    raise ValueError(f"Fila incompleta en CMUN.csv: {row}.")
                province_code = row["CPRO"].strip().zfill(2)
                municipal_code = row["CMUN"].strip()
                name = row["NOMBRE"].strip()
                if (
                    province_code not in provinces or not name
                    or not municipal_code.isdigit() or len(municipal_code) != 4
                ):
                    raise ValueError(f"Municipio incompleto o sin provincia valida: {row}.")
                code = province_code + municipal_code
                if code in municipalities:
                    raise ValueError(f"Codigo de municipio duplicado: {code}.")
                province, community = provinces[province_code]
                municipalities[code] = Municipality(code, name, province, community)
        if not municipalities:
            raise ValueError("El catalogo de municipios esta vacio.")
        return tuple(sorted(municipalities.values(), key=lambda municipality: (
            municipality_search_key(municipality.name.casefold().replace("\u00f1", "n~")),
            municipality.province,
        )))
    except (OSError, UnicodeError, csv.Error, KeyError, ValueError) as exc:
        raise ValueError(f"No se pudo leer el catalogo de municipios de {directory}: {exc}") from exc


def resolve_municipalities(codes: list[str]) -> tuple[Municipality, ...]:
    """Recupera una seleccion sin aceptar codigos desconocidos ni repetidos."""
    if not codes:
        return ()
    if len(set(codes)) != len(codes):
        raise ValueError("La seleccion contiene municipios repetidos.")
    by_code = {municipality.code: municipality for municipality in load_municipalities()}
    unknown = [code for code in codes if code not in by_code]
    if unknown:
        raise ValueError("Codigos de municipio desconocidos: " + ", ".join(unknown))
    return tuple(by_code[code] for code in codes)
