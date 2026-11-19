"""Consulta independiente de los catalogos locales de NTP y UNE."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from app_paths import application_directory
from municipalities import municipality_search_key

if TYPE_CHECKING:
    from bc3_gui import BudgetApp


ReferenceKind = Literal["NTP", "UNE", "LER"]


@dataclass(frozen=True)
class ReferenceEntry:
    """Conserva los datos de consulta y enlaces sin reproducir el documento."""

    code: str
    title: str
    publication: str
    status: str
    committee: str
    url: str
    pdf_url: str = ""


def load_reference_entries(
    kind: ReferenceKind, path: Path | None = None,
) -> tuple[ReferenceEntry, ...]:
    """Lee el catalogo completo, conservando ediciones y fichas con igual designacion."""
    root = application_directory(__file__) / "tools"
    if kind == "NTP":
        source = path if path is not None else root / "safety_health" / "datos" / "ntps.csv"
        required = {"codigo", "titulo", "anio", "url_ntp", "url_pdf"}
        host, url_column = "www.insst.es", "url_ntp"
    elif kind == "UNE":
        source = path if path is not None else root / "pptp" / "docs" / "unes.csv"
        required = {"codigo", "titulo", "fecha", "estado", "comite_nombre", "url_norma"}
        host, url_column = "www.une.org", "url_norma"
    elif kind == "LER":
        source = (
            path if path is not None
            else root / "rcd" / "Codigos_LER_BCCA2023.csv"
        )
        required = {
            "codigo_ler", "nivel", "peligroso", "descripcion",
            "partidas_BCCA2023_demoliciones_orientativas",
        }
        host, url_column = "", ""
    else:
        raise ValueError("El catálogo debe ser NTP o UNE.")
    entries = []
    try:
        with source.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(
                stream, strict=True, delimiter=";" if kind == "LER" else ","
            )
            if reader.fieldnames is not None and kind == "LER":
                required = {
                    name.strip() for name in required
                }
            if not required <= set(reader.fieldnames or []):
                raise ValueError(f"El catálogo {kind} no contiene las columnas requeridas.")
            for line, row in enumerate(reader, 2):
                if any(not isinstance(row.get(key), str) for key in required):
                    raise ValueError(f"Catálogo {kind}, fila {line}: datos incompletos.")
                if kind == "LER":
                    code = row["codigo_ler"].strip()
                    description = row["descripcion"].strip()
                    level = row["nivel"].strip()
                    hazardous = row["peligroso"].strip().casefold()
                    links = row[
                        "partidas_BCCA2023_demoliciones_orientativas"
                    ].strip()
                    if (
                        not code or not description or level not in {"1", "2", "3"}
                        or hazardous not in {"", "si", "no"}
                    ):
                        raise ValueError(
                            f"Catálogo LER, fila {line}: datos no válidos."
                        )
                    status = (
                        "Peligroso" if hazardous == "si"
                        else "No peligroso" if hazardous == "no"
                        else "Categoría"
                    )
                    entries.append(ReferenceEntry(
                        code,
                        description,
                        f"Nivel {level}",
                        status,
                        links,
                        "",
                    ))
                    continue
                code, title, url = (row[key].strip() for key in ("codigo", "titulo", url_column))
                pdf_url = row["url_pdf"].strip() if kind == "NTP" else ""
                if not code or not title or not url:
                    raise ValueError(f"Catálogo {kind}, fila {line}: faltan código, título o enlace.")
                for link in (url, pdf_url):
                    if link:
                        parsed = urlsplit(link)
                        if parsed.scheme != "https" or parsed.netloc != host:
                            raise ValueError(f"Catálogo {kind}, fila {line}: enlace oficial no válido.")
                if kind == "NTP" and not re.fullmatch(r"NTP \d+", code):
                    raise ValueError(f"Catálogo NTP, fila {line}: código no válido.")
                entries.append(ReferenceEntry(
                    code, title, row["anio" if kind == "NTP" else "fecha"].strip(),
                    row["estado"].strip() if kind == "UNE" else "",
                    row["comite_nombre"].strip() if kind == "UNE" else "", url, pdf_url,
                ))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"No se pudo leer el catálogo {kind}: {exc}") from exc
    if not entries:
        raise ValueError(f"El catálogo {kind} está vacío.")
    if kind == "NTP":
        entries.sort(key=lambda entry: int(entry.code.split()[1]))
    elif kind == "LER":
        entries.sort(key=lambda entry: tuple(
            int(part.rstrip("*")) for part in entry.code.split()
        ))
    else:
        entries.sort(key=lambda entry: entry.code.casefold())
    return tuple(entries)




def __getattr__(name: str):
    """Carga la ventana Tk únicamente cuando un consumidor legacy la solicita."""
    if name == "ReferenceSearchWindow":
        from .reference_search_tk import ReferenceSearchWindow
        return ReferenceSearchWindow
    raise AttributeError(name)
