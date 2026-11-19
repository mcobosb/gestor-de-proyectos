"""Condicionantes externos revisables del estudio de Seguridad y Salud."""
from __future__ import annotations




EXTERNAL_CONDITIONS = (
    ("trafico_existente", "Tráfico", "Tipo de tráfico, cortes, desvíos y restricciones."),
    ("peatones_terceros", "Peatones y terceros", "Afección a peatones, residentes, comercios y accesibilidad."),
    ("servicios_afectados", "Servicios afectados", "Redes, instalaciones y verificaciones necesarias."),
    ("condiciones_terreno", "Terreno", "Terreno, pendientes, huecos, excavaciones o estabilidad."),
    ("condiciones_ambientales", "Condiciones ambientales", "Calor, lluvia, viento, polvo, ruido u otras exposiciones."),
    ("instalaciones_provisionales", "Instalaciones provisionales", "Casetas, aseos, electricidad, acopios y accesos."),
    ("centro_asistencial", "Centro asistencial", "Centro de referencia y teléfono de emergencia."),
    ("coordinador_seguridad_salud", "Coordinación de seguridad y salud", "CSS durante la ejecución, si procede."),
)
APPLICABILITY = {"yes": "Sí", "no": "No", "pending": "Pendiente"}


def restore_external_conditions(value: object) -> dict[str, dict[str, str]]:
    """Valida las descripciones y decisiones guardadas sin inventar aplicabilidad."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("Los condicionantes externos deben ser un objeto JSON.")
    keys = {key for key, _label, _description in EXTERNAL_CONDITIONS}
    result = {}
    for key, row in value.items():
        if key not in keys or not isinstance(row, dict):
            raise ValueError("Hay un condicionante externo no válido.")
        description = row.get("description")
        status = row.get("applicability")
        if (
            not isinstance(description, str) or not isinstance(status, str)
            or status not in APPLICABILITY
        ):
            raise ValueError("La descripción o aplicabilidad de un condicionante no es válida.")
        result[key] = {"description": description.strip(), "applicability": status}
    return result


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
