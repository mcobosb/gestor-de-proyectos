"""Convenciones de titulos e indices compartidas por los documentos de obra."""
from __future__ import annotations

import re
import unicodedata


DEFAULT_TOC_DEPTH = 2
TABLE_HEADER_BACKGROUND = "#4472C4"
TABLE_ALTERNATE_BACKGROUND = "#D9E5F5"


def project_document_variables(title: str, settings) -> dict[str, str]:
    """Obtiene los datos comunes actuales sin mantener copias en cada herramienta."""
    municipalities = settings.municipalities
    places = ", ".join(municipality.label for municipality in municipalities)
    return {
        "titulo_proyecto": title,
        "entidad": settings.organization,
        "autor": settings.author,
        "cargo": settings.role,
        "expediente": settings.expediente,
        "localidad": settings.location,
        "municipios": places,
        "emplazamiento": places or settings.location,
        "provincias": ", ".join(dict.fromkeys(municipality.province for municipality in municipalities)),
        "comunidades_autonomas": ", ".join(dict.fromkeys(
            municipality.autonomous_community for municipality in municipalities
        )),
    }


def number_headings(source: str) -> str:
    """Numera la jerarquia Markdown sin duplicar la numeracion del texto original."""
    counters = [0] * 6
    result = []
    for line in source.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            level = len(match[1])
            counters[level - 1] += 1
            counters[level:] = [0] * (6 - level)
            for parent in range(level - 1):
                counters[parent] = max(1, counters[parent])
            title = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", match[2]).strip().upper()
            number = ".".join(str(value) for value in counters[:level])
            line = f"{match[1]} {number} {title}"
        result.append(line)
    return "\n".join(result)


def template_paragraph_styles(template: dict) -> dict[str, str]:
    """Localiza estilos de cuerpo y titulos por nombres ODF o su nivel semantico."""
    styles = [
        style for style in template["styles"]
        if style.get("{urn:oasis:names:tc:opendocument:xmlns:style:1.0}family") == "paragraph"
    ]
    namespace = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
    result = {}
    for style in styles:
        name = style.get(namespace + "name", "")
        label = style.get(namespace + "display-name", name.removeprefix("Template"))
        label = re.sub(r"_([0-9A-Fa-f]{2})_", lambda match: chr(int(match[1], 16)), label)
        label = "".join(
            char for char in unicodedata.normalize("NFKD", label.casefold())
            if not unicodedata.combining(char)
        )
        if label in {"text body", "body text", "cuerpo", "cuerpo de texto", "texto del cuerpo"}:
            result["body"] = name
        level = style.get(namespace + "default-outline-level")
        match = re.fullmatch(r"(?:heading|titulo)\s*([1-6])", label)
        if match:
            result[f"heading{match[1]}"] = name
        elif level in {"1", "2", "3", "4", "5", "6"}:
            result.setdefault(f"heading{level}", name)
        if label in {"heading", "encabezado", "titulo"}:
            result.setdefault("heading", name)
        if label in {"standard", "normal", "predeterminado"}:
            result.setdefault("body", name)
    return result
