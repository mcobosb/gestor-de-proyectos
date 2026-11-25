"""Lectura del catálogo PAC y las fichas de ensayo del libro de referencia."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from openpyxl import load_workbook


WORKBOOK_PATH = Path(__file__).with_name("Plan_Aseguramiento_Calidad_CTE_CE_PG3-v2.xlsx")


@dataclass(frozen=True)
class CatalogEntry:
    """Control o ensayo revisable con trazabilidad a la hoja de origen."""

    identifier: str
    label: str
    reference: str
    frequency: str
    criteria: str
    details: str


def _normalized(value: object) -> str:
    """Normaliza títulos de columna para tolerar diferencias de acentuación."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def _sheet_rows(sheet, required_headers: set[str]) -> tuple[list[str], list[tuple]]:
    """Encuentra la cabecera y devuelve las filas posteriores no vacías."""
    for row_index, values in enumerate(sheet.iter_rows(values_only=True)):
        headers = [_normalized(value) for value in values]
        if required_headers.issubset(headers):
            records = [
                tuple(row)
                for row in sheet.iter_rows(min_row=row_index + 2, values_only=True)
                if any(value not in (None, "") for value in row)
            ]
            return headers, records
    raise ValueError(f"No se encuentra la cabecera esperada en la hoja «{sheet.title}».")


def _value(row: tuple, headers: list[str], header: str) -> str:
    """Lee y limpia una celda identificada por su cabecera normalizada."""
    try:
        index = headers.index(_normalized(header))
    except ValueError as exc:
        raise ValueError(f"Falta la columna «{header}» en el catálogo PAC.") from exc
    return str(row[index] or "").strip()


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CatalogEntry, ...]:
    """Carga los controles y ensayos del XLSX incluido con la herramienta."""
    if not WORKBOOK_PATH.is_file():
        raise FileNotFoundError(f"No se encuentra el catálogo de calidad: {WORKBOOK_PATH}")
    workbook = load_workbook(WORKBOOK_PATH, read_only=True, data_only=True)
    try:
        pac_headers, pac_rows = _sheet_rows(
            workbook["Matriz_Maestra_PAC"],
            {
                _normalized("ID"),
                _normalized("Unidad de Obra / Hito de Control"),
                _normalized("Referencia Normativa Exacta"),
                _normalized("Ensayos, Inspecciones y Parámetros Clave"),
                _normalized("Criterios de Aceptación / Tolerancias"),
                _normalized("Frecuencia / Lote de Control"),
            },
        )
        entries = []
        for row in pac_rows:
            identifier = _value(row, pac_headers, "ID")
            if not re.fullmatch(r"PAC-\d{2}", identifier):
                continue
            details = "\n".join(
                f"- {label}: {value}"
                for label, value in (
                    ("Ámbito", _value(row, pac_headers, "Ámbito")),
                    ("Especialidad", _value(row, pac_headers, "Especialidad / Bloque")),
                    ("Tipo de control", _value(row, pac_headers, "Tipo de Control")),
                    ("Criticidad / PPI", _value(row, pac_headers, "Criticidad / PPI")),
                    ("Ensayos e inspecciones", _value(row, pac_headers, "Ensayos, Inspecciones y Parámetros Clave")),
                    ("Registros", _value(row, pac_headers, "Registro / Entregable Documental")),
                    ("Estado en obra", _value(row, pac_headers, "Estado en Obra")),
                ) if value
            )
            entries.append(CatalogEntry(
                identifier=identifier,
                label=f"{identifier} — {_value(row, pac_headers, 'Unidad de Obra / Hito de Control')}",
                reference=_value(row, pac_headers, "Referencia Normativa Exacta"),
                frequency=_value(row, pac_headers, "Frecuencia / Lote de Control"),
                criteria=_value(row, pac_headers, "Criterios de Aceptación / Tolerancias"),
                details=details,
            ))

        test_headers, test_rows = _sheet_rows(
            workbook["Detalle_Ensayos_Clave"],
            {
                _normalized("Familia de Ensayo"),
                _normalized("Nombre del Ensayo"),
                _normalized("Norma UNE / EN / Pliego"),
                _normalized("Valores Límite / Tolerancias Reglamentarias"),
            },
        )
        test_number = 0
        for row in test_rows:
            name = _value(row, test_headers, "Nombre del Ensayo")
            if not name:
                continue
            test_number += 1
            identifier = f"ENS-{test_number:02d}"
            details = "\n".join(
                f"- {label}: {value}"
                for label, value in (
                    ("Familia", _value(row, test_headers, "Familia de Ensayo")),
                    ("Parámetro medido", _value(row, test_headers, "Parámetro Medido")),
                    ("Momento y punto de toma", _value(row, test_headers, "Momento y Punto de Toma")),
                    ("Motivo de rechazo", _value(row, test_headers, "Motivo de Rechazo Inmediato")),
                    ("Protocolo y contraprueba", _value(row, test_headers, "Protocolo de Actuación / Contraprueba")),
                ) if value
            )
            entries.append(CatalogEntry(
                identifier=identifier,
                label=f"{identifier} — {name}",
                reference=_value(row, test_headers, "Norma UNE / EN / Pliego"),
                frequency="Según el lote del PAC aplicable y el pliego del proyecto.",
                criteria=_value(row, test_headers, "Valores Límite / Tolerancias Reglamentarias"),
                details=details,
            ))
        identifiers = [entry.identifier for entry in entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("El catálogo de calidad contiene identificadores duplicados.")
        if not any(identifier.startswith("PAC-") for identifier in identifiers):
            raise ValueError("La hoja Matriz_Maestra_PAC no contiene hitos PAC.")
        if not any(identifier.startswith("ENS-") for identifier in identifiers):
            raise ValueError("La hoja Detalle_Ensayos_Clave no contiene fichas de ensayo.")
        return tuple(entries)
    finally:
        workbook.close()


@lru_cache(maxsize=1)
def _catalog_index() -> dict[str, CatalogEntry]:
    """Indexa una vez el catálogo por identificador estable."""
    return {entry.identifier: entry for entry in load_catalog()}


def get_catalog_entry(identifier: str) -> CatalogEntry:
    """Devuelve una ficha por ID o comunica que el identificador no existe."""
    try:
        return _catalog_index()[identifier]
    except KeyError as exc:
        raise ValueError(f"No existe la ficha de catálogo «{identifier}».") from exc
