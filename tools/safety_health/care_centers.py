"""Edición y formato de centros asistenciales procedentes del registro SICESS."""
from __future__ import annotations

from typing import TypedDict



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


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
