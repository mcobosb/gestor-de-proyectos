"""Convención compartida para identificar campos de los cajetines SVG."""
from __future__ import annotations

from pathlib import Path
import re
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
    "autor_2": "Segunda autoría",
    "cargo_2": "Titulación / cargo de la segunda autoría",
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
    "Para conservar dos bloques de firma, utiliza autor y cargo en el primero, "
    "y autor_2 y cargo_2 en el segundo.\n\n"
    "Título del proyecto, expediente, autoría y cargo corresponden a la configuración general; "
    "título y número de plano, escala y fecha corresponden a cada plano. "
    "Selecciona la carpeta que contiene los cajetines SVG. Sus nombres deben incluir A4 o A3, "
    "por ejemplo cajetin_A4.svg y cajetin_A3.svg, sin distinguir mayúsculas. "
    "La búsqueda se realiza en esa carpeta, no en subcarpetas. Debe haber un único SVG "
    "para cada formato y no debe contener ambos formatos en su nombre.\n\n"
    "Esta convención identifica los campos para la generación; vincular la carpeta "
    "no modifica automáticamente los planos existentes."
)


def find_title_block(directory: Path, paper_size: str) -> Path:
    """Localiza un SVG inequívoco por formato, sin recurrir al cajetín de otro tamaño."""
    paper_size = paper_size.strip().upper()
    if paper_size not in {"A4", "A3"}:
        raise ValueError(f"Formato de plano no admitido para el cajetín: {paper_size}.")
    directory = directory.expanduser()
    if directory.is_file():
        # Las configuraciones anteriores guardaban un archivo, no su carpeta.
        directory = directory.parent
    candidates = []
    for path in directory.iterdir():
        if path.suffix.casefold() != ".svg" or not path.is_file():
            continue
        formats = set(re.findall(r"A[34](?![0-9])", path.stem.upper()))
        if paper_size in formats:
            if len(formats) > 1:
                raise ValueError(f"El archivo {path.name} contiene A4 y A3; indica un solo formato en su nombre.")
            candidates.append(path)
    if not candidates:
        raise ValueError(f"No se encontró un cajetín SVG con {paper_size} en el nombre en {directory}.")
    if len(candidates) > 1:
        names = ", ".join(sorted(path.name for path in candidates))
        raise ValueError(f"Hay varios cajetines para {paper_size}: {names}. Deja un único SVG para ese formato.")
    return candidates[0]


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
