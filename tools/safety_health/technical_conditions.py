"""Selección revisable de prescripciones técnicas, sin sustituir la evaluación de riesgos."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import unicodedata

from .external_conditions import restore_external_conditions
from .regulations import WorkUnit


@dataclass(frozen=True)
class TechnicalBlock:
    """Identifica una prescripción y los indicios que permiten proponerla."""

    key: str
    title: str
    references: tuple[str, ...] = ()
    equipment: str = ""
    conditions: tuple[str, ...] = ()


TECHNICAL_BLOCKS = (
    TechnicalBlock(
        "barriers", "Vallas, barreras y cerramientos móviles",
        ("PC-BARRERA-RIGIDA", "MP-SEGREGACION", "R-TERCEROS"),
        r"\b(?:valla|barrera|cerramiento)", ("peatones_terceros",),
    ),
    TechnicalBlock(
        "openings", "Protección de huecos, arquetas y registros",
        ("MP-PROTEGER-HUECOS", "PC-TAPA-RESISTENTE", "PC-ENTIBACION"),
    ),
    TechnicalBlock(
        "access", "Pasos provisionales y accesos",
        ("MP-ACCESOS-SEGUROS", "PC-ACCESO-EXCAVACION"), conditions=("peatones_terceros",),
    ),
    TechnicalBlock(
        "mobile", "Maquinaria móvil y vehículos",
        ("EQ-CAMION", "EQ-EXCAVADORA", "EQ-CARGADORA", "R-RETROCESO-MAQUINA"),
        r"\b(?:camion|excavadora|cargadora|dumper|motoniveladora|carretilla|tractor)",
    ),
    TechnicalBlock(
        "paving", "Equipos de extendido y compactación",
        ("EQ-EXTENDEDORA", "EQ-RODILLO-COMPACTADOR"),
        r"\b(?:extendedora|rodillo|rulo|compactador(?!a?\s+manual))",
    ),
    TechnicalBlock(
        "cutting", "Equipos de corte, demolición y compactación manual",
        ("EQ-CORTE", "EQ-MARTILLO-DEMOLEDOR", "EQ-EXTRACCION-TESTIGOS"),
        r"\b(?:cortadora|sierra|martillo|radial|pison|bandeja\s+vibrante)",
    ),
    TechnicalBlock(
        "lifting", "Elevación de cargas",
        ("EQ-ELEVACION", "R-IZADO-CARGAS", "R-CAIDA-CARGA-SUSPENDIDA"),
        r"\b(?:grua|polipasto|maquinillo)",
    ),
    TechnicalBlock(
        "signage", "Señalización temporal de obras",
        ("PC-SENALIZACION", "PC-BALIZAMIENTO"), conditions=("trafico_existente",),
    ),
    TechnicalBlock(
        "signallers", "Señalistas",
        ("R-RETROCESO-MAQUINA", "R-IZADO-CARGAS"), conditions=("trafico_existente",),
    ),
    TechnicalBlock(
        "pedestrians", "Peatones, residentes y accesos",
        ("R-TERCEROS", "MP-SEGREGACION"), conditions=("peatones_terceros",),
    ),
)
_KEYS = {block.key for block in TECHNICAL_BLOCKS}
_BLOCK_RE = re.compile(
    r"^<!-- ESS-TECH:([a-z]+) -->\n(.*?)^<!-- /ESS-TECH -->[ \t]*\n?",
    re.MULTILINE | re.DOTALL,
)
_REVIEW_MARKER = "<!-- ESS-TECH-REVIEW -->"


def restore_technical_review(value: object) -> dict:
    """Valida las decisiones explícitas sin confirmar datos ausentes o incompletos."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("La revisión de condiciones técnicas debe ser un objeto JSON.")
    context, blocks = value.get("context"), value.get("blocks")
    if (
        not isinstance(context, str) or not re.fullmatch(r"[a-f0-9]{64}", context)
        or not isinstance(blocks, dict)
        or any(key not in _KEYS or not isinstance(selected, bool) for key, selected in blocks.items())
    ):
        raise ValueError("La revisión de condiciones técnicas contiene decisiones no válidas.")
    return {"context": context, "blocks": dict(blocks)}


def technical_review_confirmed(value: object, context: str) -> bool:
    """Exige revisar todos los bloques para el contexto actual."""
    review = restore_technical_review(value)
    return review.get("context") == context and set(review.get("blocks", {})) == _KEYS


def technical_review_data(
    units: list[WorkUnit], references: dict[str, list[str]],
    resources: list[tuple[str, str]], conditions: object, template: str,
) -> tuple[str, dict[str, str]]:
    """Propone bloques por referencias, recursos físicos y condicionantes expresamente aplicables."""
    external = restore_external_conditions(conditions)
    codes = {code for group in references.values() for code in group}
    proposals = {}
    for block in TECHNICAL_BLOCKS:
        reasons = []
        matched = codes.intersection(block.references)
        if matched:
            reasons.append("Referencias de las fichas: " + ", ".join(sorted(matched)))
        for code, label in resources:
            normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().casefold()
            if block.equipment and re.search(block.equipment, normalized):
                reasons.append(f"Recurso del presupuesto: {code} — {label}")
        for key in block.conditions:
            if external.get(key, {}).get("applicability") == "yes":
                reasons.append("Condicionante confirmado: " + key)
        if reasons:
            proposals[block.key] = "; ".join(reasons)
    payload = {
        "units": sorted(
            (asdict(unit) for unit in units),
            key=lambda row: (row["chapter"], row["code"], row["summary"]),
        ),
        "references": sorted(codes),
        "resources": sorted(set(resources)), "conditions": external,
        "template": template, "rules": [asdict(block) for block in TECHNICAL_BLOCKS],
    }
    context = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return context, proposals


def selected_technical_template(
    template: str, saved: object, context: str, pending: list[str],
) -> str:
    """Mantiene el núcleo general e incorpora exclusivamente decisiones actuales confirmadas."""
    template = template.replace("\r\n", "\n")
    keys = [match.group(1) for match in _BLOCK_RE.finditer(template)]
    if (
        len(keys) != len(_KEYS) or set(keys) != _KEYS
        or template.count("<!-- ESS-TECH:") != len(keys)
        or template.count("<!-- /ESS-TECH -->") != len(keys)
        or template.count(_REVIEW_MARKER) != 1
    ):
        raise ValueError("La plantilla del pliego no contiene todos los bloques técnicos revisables.")
    confirmed = technical_review_confirmed(saved, context)
    review = restore_technical_review(saved)
    choices = review.get("blocks", {}) if confirmed else {}
    notice = ""
    if not confirmed:
        notice = (
            "[VALIDAR] Confirmar las condiciones técnicas específicas para las unidades, "
            "equipos y condicionantes actuales. Los bloques específicos pendientes de "
            "revisión no se han incorporado; este documento no acredita su innecesariedad."
        )
        pending.append(notice)
    elif not any(choices.values()):
        notice = "Tras la revisión no se han seleccionado bloques técnicos específicos; se conservan las condiciones generales."

    def include(match: re.Match[str]) -> str:
        """Retira también los marcadores de los bloques incluidos."""
        return match.group(2).strip() + "\n\n" if choices.get(match.group(1), False) else ""

    return _BLOCK_RE.sub(include, template).replace(_REVIEW_MARKER, notice)


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
