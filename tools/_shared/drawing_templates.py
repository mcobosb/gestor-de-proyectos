"""Convención compartida para identificar campos de los cajetines SVG."""
from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET


TITLE_BLOCK_FIELDS = {
    "titulo_proyecto": "Título del proyecto",
    "expediente": "Número de expediente",
    "titulo_plano": "Título del plano",
    "numero_plano": "Número del plano",
    "escala": "Escala del plano",
    "fecha": "Fecha del plano",
    "autor": "Autoría del proyecto",
    "cargo": "Titulación / cargo de la autoría",
}
TITLE_BLOCK_HELP = (
    "En un cajetín SVG, asigna al ID de cada objeto de texto uno de estos nombres, "
    "en minúsculas, sin espacios ni llaves:\n\n"
    + "\n".join(f"{key} — {label}" for key, label in TITLE_BLOCK_FIELDS.items())
    + "\n\nEn Inkscape: selecciona el texto y abre Propiedades del objeto → ID. "
    "La etiqueta visible puede tener el mismo nombre, pero el reconocimiento utiliza el ID SVG.\n\n"
    "Usa un único objeto de texto por campo; puede contener varias líneas. "
    "No lo conviertas en trayectos ni lo incluyas en una imagen. "
    "Conserva los rótulos fijos (PROYECTO:, ESCALA:, etc.) con otros IDs. "
    "Los campos son opcionales y cada ID debe ser único.\n\n"
    "Título del proyecto, expediente, autoría y cargo corresponden a la configuración general; "
    "título y número de plano, escala y fecha corresponden a cada plano. "
    "Esta convención identifica los campos para la generación; vincular el archivo "
    "no modifica automáticamente los planos existentes. Los demás formatos se guardan como referencia."
)


def read_title_block_fields(path: Path) -> dict[str, str]:
    """Identifica campos SVG editables sin modificar sus textos, geometría o imágenes."""
    root = ET.parse(path).getroot()
    namespace = "{http://www.w3.org/2000/svg}"
    if root.tag != namespace + "svg":
        raise ValueError("El cajetín debe ser un documento SVG.")
    fields = {}
    for element in root.iter():
        identifier = element.get("id")
        if identifier not in TITLE_BLOCK_FIELDS:
            continue
        if identifier in fields:
            raise ValueError(f"El ID del campo está duplicado: {identifier}.")
        if element.tag != namespace + "text":
            raise ValueError(f"El campo {identifier} debe ser un objeto de texto SVG editable.")
        fields[identifier] = "".join(element.itertext())
    return fields
