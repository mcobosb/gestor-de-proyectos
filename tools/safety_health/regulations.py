"""Propuestas revisables de normativa preventiva y NTP, sin inferir su obligatoriedad."""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from reference_search import ReferenceEntry, load_reference_entries


DATA_ROOT = Path(__file__).resolve().parent / "datos"


@dataclass(frozen=True)
class WorkUnit:
    """Vincula una partida real con las referencias expresas de sus fichas ESS."""

    code: str
    summary: str
    fiches: tuple[str, ...]
    risks: tuple[str, ...]
    measures: tuple[str, ...]


@dataclass(frozen=True)
class Regulation:
    """Describe una disposicion y los indicios que permiten proponer su revision."""

    code: str
    title: str
    triggers: tuple[str, ...] = ()


REGULATIONS = (
    Regulation("L31-1995", "Ley 31/1995, de 8 de noviembre, de Prevención de Riesgos Laborales."),
    Regulation("RD39-1997", "Real Decreto 39/1997, de 17 de enero, Reglamento de los Servicios de Prevención."),
    Regulation("RD1627-1997", "Real Decreto 1627/1997, de 24 de octubre, disposiciones mínimas de seguridad y salud en las obras de construcción."),
    Regulation("RD171-2004", "Real Decreto 171/2004, de 30 de enero, coordinación de actividades empresariales."),
    Regulation("L32-2006", "Ley 32/2006, de 18 de octubre, subcontratación en el Sector de la Construcción, y Real Decreto 1109/2007, de 24 de agosto, de desarrollo."),
    Regulation("RD1215-1997", "Real Decreto 1215/1997, de 18 de julio, utilización de equipos de trabajo."),
    Regulation("RD773-1997", "Real Decreto 773/1997, de 30 de mayo, utilización de equipos de protección individual."),
    Regulation("RD485-1997", "Real Decreto 485/1997, de 14 de abril, señalización de seguridad y salud en el trabajo."),
    Regulation("RD487-1997", "Real Decreto 487/1997, de 14 de abril, manipulación manual de cargas.", ("R-SOBREESFUERZO", "MP-MANIPULACION-CARGAS")),
    Regulation("RD614-2001", "Real Decreto 614/2001, de 8 de junio, protección frente al riesgo eléctrico.", ("R-ELECTRICO", "R-SERVICIOS-AFECTADOS")),
    Regulation("RD374-2001", "Real Decreto 374/2001, de 6 de abril, protección frente a los agentes químicos durante el trabajo.", ("R-QUIMICO", "R-CONTACTO-CEMENTO", "R-HUMOS", "R-POLVO", "MP-HIGIENE-QUIMICA")),
    Regulation("RD286-2006", "Real Decreto 286/2006, de 10 de marzo, protección frente al ruido.", ("R-RUIDO",)),
    Regulation("RD1311-2005", "Real Decreto 1311/2005, de 4 de noviembre, exposición a vibraciones mecánicas.", ("R-VIBRACION",)),
    Regulation("RD1644-2008", "Real Decreto 1644/2008, de 10 de octubre, comercialización y puesta en servicio de las máquinas, en lo que continúe siendo aplicable a los equipos utilizados. Comprobar el régimen temporal y las disposiciones que lo sustituyan."),
    Regulation("RD842-2002", "Real Decreto 842/2002, de 2 de agosto, Reglamento Electrotécnico para Baja Tensión, cuando exista instalación eléctrica provisional.", ("R-ELECTRICO",)),
    Regulation("TRAFICO", "Real Decreto Legislativo 6/2015, de 30 de octubre, Ley sobre Tráfico, Circulación de Vehículos a Motor y Seguridad Vial, y Real Decreto 1428/2003, de 21 de noviembre, Reglamento General de Circulación, cuando se actúe en la vía.", ("R-ATROPELLO", "R-TERCEROS", "MP-SEGREGACION")),
    Regulation("MUNICIPAL", "Ordenanzas e instrucciones municipales, de la autoridad de tráfico y de los titulares competentes, aplicables al emplazamiento y a la ocupación de vía pública del proyecto.", ("R-TERCEROS", "MP-SEGREGACION")),
)

# Indicios para revision, no correspondencias normativas ni evaluaciones de riesgos.
_NTP_TRIGGERS = {
    "NTP 223": ("R-ESPACIO-CONFINADO", "R-ASFIXIA", "MP-PERMISO-ESPACIO-CONFINADO"),
    "NTP 278": ("R-DERRUMBE", "MP-ESTABILIDAD-EXCAVACION"),
    "NTP 477": ("R-SOBREESFUERZO", "MP-MANIPULACION-CARGAS"),
    "NTP 270": ("R-RUIDO",),
    "NTP 839": ("R-VIBRACION",),
    "NTP 71": ("R-ELECTRICO",),
    "NTP 718": ("R-ATROPELLO", "MP-SEGREGACION"),
    "NTP 922": ("R-CALOR",),
    "NTP 923": ("R-CALOR",),
}


def _csv_rows(path: Path, columns: set[str]) -> list[dict[str, str]]:
    """Lee datos locales completos o informa de la fila y del catalogo incorrectos."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if not columns <= set(reader.fieldnames or ()):
                raise ValueError(f"El catálogo {path.name} no contiene las columnas requeridas.")
            rows = []
            for index, row in enumerate(reader, 2):
                if any(not isinstance(row.get(column), str) for column in columns):
                    raise ValueError(f"Catálogo {path.name}, fila {index}: datos incompletos.")
                rows.append({column: row[column].strip() for column in columns})
            return rows
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"No se pudo leer {path.name}: {exc}") from exc


def load_ntps() -> dict[str, ReferenceEntry]:
    """Reutiliza el lector oficial y conserva los avisos de actualizacion del CSV."""
    path = DATA_ROOT / "ntps.csv"
    states = {
        row["codigo"]: row["estado"]
        for row in _csv_rows(path, {"codigo", "estado"})
    }
    return {
        entry.code: replace(entry, status=states[entry.code])
        for entry in load_reference_entries("NTP", path)
    }


def review_context(units: list[WorkUnit]) -> str:
    """Detecta cambios de partidas, referencias, reglas o catalogos tras la confirmacion."""
    unit_values = sorted(
        (asdict(unit) for unit in units),
        key=lambda row: (row["code"], row["summary"]),
    )
    try:
        catalogues = {
            name: hashlib.sha256((DATA_ROOT / name).read_bytes()).hexdigest()
            for name in ("ntps.csv", "NTP_tematica_por_codigo_BCCA.csv")
        }
    except OSError as exc:
        raise ValueError(f"No se pudo comprobar el contexto de los catálogos NTP: {exc}") from exc
    payload = {
        "units": unit_values, "catalogues": catalogues,
        "laws": [asdict(law) for law in REGULATIONS], "ntp_rules": _NTP_TRIGGERS,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def restore_review(value: object) -> dict:
    """Valida las decisiones persistidas sin convertir valores incorrectos a booleanos."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("La revisión de normativa y NTP no es válida.")
    if not isinstance(value.get("context"), str):
        raise ValueError("La revisión de normativa y NTP no contiene su contexto.")
    laws, ntps = value.get("laws"), value.get("ntps")
    if not isinstance(laws, dict) or not isinstance(ntps, dict):
        raise ValueError("Faltan las decisiones de normativa o NTP.")
    for choices in (laws, *ntps.values()):
        if not isinstance(choices, dict) or any(
            not isinstance(key, str) or not isinstance(selected, bool)
            for key, selected in choices.items()
        ):
            raise ValueError("Las selecciones de normativa y NTP deben ser booleanas.")
    if any(not isinstance(code, str) for code in ntps):
        raise ValueError("Hay un código de unidad de obra no válido en las NTP.")
    if any(not re.fullmatch(r"NTP \d+", code) for choices in ntps.values() for code in choices):
        raise ValueError("Hay un código NTP no válido en la revisión.")
    return {"context": value["context"], "laws": dict(laws),
            "ntps": {code: dict(choices) for code, choices in ntps.items()}}


def law_proposals(units: list[WorkUnit]) -> dict[str, bool]:
    """Propone normas generales e indicios especificos, siempre sujetos a confirmacion."""
    references = {reference for unit in units for reference in (*unit.risks, *unit.measures)}
    return {
        law.code: not law.triggers or bool(references.intersection(law.triggers))
        for law in REGULATIONS
    }


def ntp_proposals(
    units: list[WorkUnit], catalog: dict[str, ReferenceEntry],
) -> dict[str, dict[str, str]]:
    """Cruza codigos expresos y relaciones BCCA tematicas sin buscar en descripciones."""
    thematic = _csv_rows(
        DATA_ROOT / "NTP_tematica_por_codigo_BCCA.csv", {"codigo_bcca", "codigo_NTP"},
    )
    result: dict[str, dict[str, str]] = {}
    for unit in units:
        choices = result.setdefault(unit.code, {})
        references = set((*unit.risks, *unit.measures))
        for code, triggers in _NTP_TRIGGERS.items():
            matched = references.intersection(triggers)
            if matched:
                if code not in catalog:
                    raise ValueError(f"La propuesta {code} no existe en el catálogo NTP.")
                choices[code] = "Propuesta por " + ", ".join(sorted(matched)) + "."
        for row in thematic:
            prefix, code = row["codigo_bcca"], row["codigo_NTP"]
            if prefix and any(
                item_code.upper().startswith(prefix.upper())
                for item_code in (unit.code, *unit.fiches)
            ):
                if code not in catalog:
                    raise ValueError(f"La relación BCCA {prefix} remite a {code}, no disponible.")
                choices.setdefault(code, f"Relación temática BCCA {prefix}; confirmar su pertinencia.")
    return result


def initially_selected(entry: ReferenceEntry, reason: str) -> bool:
    """No preselecciona asociaciones solo tematicas ni referencias con avisos."""
    text = unicodedata.normalize("NFKD", entry.title + " " + entry.status)
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    return reason.startswith("Propuesta por ") and not any(
        token in text for token in ("sustitu", "actualizad", "anula", "retirad", "complementad")
    )
