"""Condicionantes externos revisables del estudio de Seguridad y Salud."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from localization import translate


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


class ExternalConditionsEditor(ttk.Frame):
    """Permite revisar texto y aplicabilidad antes de incorporarlos al proyecto."""

    def __init__(self, master: tk.Misc, saved: object) -> None:
        """Crea las ocho filas con estado pendiente por defecto."""
        super().__init__(master, padding=12)
        conditions = restore_external_conditions(saved)
        self.columnconfigure(1, weight=1)
        self.variables: dict[str, tk.StringVar] = {}
        self.descriptions: dict[str, tk.Text] = {}
        for column, heading in enumerate(("Condicionante", "Descripción / observaciones", "Aplicabilidad")):
            ttk.Label(self, text=heading).grid(row=0, column=column, sticky="w", padx=4)
        for index, (key, label, default) in enumerate(EXTERNAL_CONDITIONS, start=1):
            row = conditions.get(key, {"description": default, "applicability": "pending"})
            ttk.Label(self, text=label, wraplength=170).grid(
                row=index, column=0, sticky="nw", padx=4, pady=4,
            )
            text = tk.Text(self, width=65, height=2, wrap="word")
            text.insert("1.0", translate(row["description"]) if key not in conditions else row["description"])
            text.grid(row=index, column=1, sticky="ew", padx=4, pady=4)
            self.descriptions[key] = text
            variable = tk.StringVar(value=translate(APPLICABILITY[row["applicability"]]))
            self.variables[key] = variable
            ttk.Combobox(
                self, textvariable=variable,
                values=tuple(translate(label) for label in APPLICABILITY.values()),
                state="readonly", width=12,
            ).grid(row=index, column=2, sticky="nw", padx=4, pady=4)

    def collect_state(self) -> dict[str, dict[str, str]]:
        """Recoge todas las filas usando estados independientes del idioma de la interfaz."""
        statuses = {translate(label): key for key, label in APPLICABILITY.items()}
        result = {
            key: {
                "description": text.get("1.0", "end-1c"),
                "applicability": statuses.get(self.variables[key].get(), ""),
            }
            for key, text in self.descriptions.items()
        }
        return restore_external_conditions(result)
