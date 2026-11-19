"""Equivalencias locales IFC → BCCA y cantidades con unidades verificables."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
from math import isfinite
from pathlib import Path
import re
import unicodedata
from typing import TYPE_CHECKING

from budget_core import Bc3Dataset, is_chapter_code, round_half_up

if TYPE_CHECKING:
    from ifcopenshell import entity_instance, file


_WORDS = set("para por una uno del con sin los las the and of for from".split())
_DIMENSIONS = {
    "m": ("IfcQuantityLength", "LENGTHUNIT", 1.0),
    "m2": ("IfcQuantityArea", "AREAUNIT", 1.0),
    "m3": ("IfcQuantityVolume", "VOLUMEUNIT", 1.0),
    "kg": ("IfcQuantityWeight", "MASSUNIT", 1000.0),
    "t": ("IfcQuantityWeight", "MASSUNIT", 1_000_000.0),
}


def _text(value: str) -> str:
    """Normaliza acentos y variantes tipográficas."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def _tokens(value: str) -> set[str]:
    """Conserva términos descriptivos, no conectores ni códigos numéricos."""
    return set(re.findall(r"[a-z]{3,}", _text(value))) - _WORDS


def unit_key(unit: str) -> str:
    """Identifica las unidades BC3 admitidas sin confundir dimensiones."""
    key = re.sub(r"[\s.^]", "", _text(unit))
    return {"u": "ud", "und": "ud", "uds": "ud", "unidad": "ud", "unidades": "ud",
            "ml": "m", "tn": "t"}.get(key, key)


@dataclass(frozen=True)
class Entry:
    """Una unidad de obra de una base de referencia concreta."""

    base: Bc3Dataset
    code: str
    unit: str
    summary: str

    @property
    def label(self) -> str:
        """Identifica la partida para la revisión humana."""
        return f"{self.code} · {self.summary} [{self.base.path.stem}]"


@dataclass(frozen=True)
class Proposal:
    """Candidato y cantidad; cualquiera puede estar pendiente de resolver."""

    entry: Entry | None
    quantity: float | None
    reason: str


@dataclass(frozen=True)
class Rule:
    """Equivalencia revisable para una clase y una especificación IFC 4.3."""

    ifc_class: str
    base: str
    code: str
    unit: str
    contains: tuple[str, ...] = ()
    predefined: str = ""
    type_name: str = ""


def read_rules(path: Path) -> tuple[Rule, ...]:
    """Valida la tabla JSON y evita reglas genéricas sin restricciones."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: se esperaba una lista de equivalencias.")
    rules = []
    for i, row in enumerate(data, 1):
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}, fila {i}: se esperaba un objeto.")
        values = [row.get(k) for k in ("ifc_class", "base", "code", "unit")]
        terms = row.get("contains", [])
        predefined = row.get("predefined", "")
        type_name = row.get("type_name", "")
        if (
            not all(isinstance(value, str) and value.strip() for value in values)
            or not isinstance(terms, list)
            or not all(isinstance(term, str) and term.strip() for term in terms)
            or not isinstance(predefined, str)
            or not isinstance(type_name, str)
            or not (terms or type_name.strip())
        ):
            raise ValueError(f"{path.name}, fila {i}: faltan el destino o las condiciones IFC.")
        rules.append(Rule(
            *(value.strip() for value in values), tuple(term.strip() for term in terms),
            predefined.strip(), type_name.strip(),
        ))
    return tuple(rules)


class Catalogue:
    """Índice de códigos exactos y términos de las bases cargadas."""

    def __init__(self, bases: tuple[Bc3Dataset, ...], rules: tuple[Rule, ...] = ()) -> None:
        """Excluye capítulos y recursos que no son unidades de obra."""
        self.bases = bases
        self.codes: dict[str, list[Entry]] = defaultdict(list)
        self.entries: list[Entry] = []
        self.words: list[set[str]] = []
        self.index: dict[str, list[int]] = defaultdict(list)
        self.rules: dict[str, list[Rule]] = defaultdict(list)
        for rule in rules:
            self.rules[rule.ifc_class].append(rule)
        for base in bases:
            for code, concept in base.concepts.items():
                if concept.ctype != "0" or is_chapter_code(code):
                    continue
                entry = Entry(base, code, concept.unit, concept.summary)
                self.codes[code.casefold()].append(entry)
                words = _tokens(concept.summary)
                for word in words:
                    self.index[word].append(len(self.entries))
                self.entries.append(entry)
                self.words.append(words)

    def propose(self, source: file, entity: entity_instance) -> Proposal:
        """Prioriza referencias BCCA explícitas y deja empates sin asignar."""
        from ifcopenshell.util.classification import get_classification, get_references
        from ifcopenshell.util.element import get_type

        element_type = get_type(entity)
        codes = set()
        for ref in get_references(entity):
            classification = get_classification(ref)
            if classification and "bcca" in _tokens(classification.Name or ""):
                code = getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None)
                if code:
                    codes.add(code.strip().casefold())
        for obj in (entity, element_type):
            if obj is not None:
                tag = (getattr(obj, "Tag", None) or "").strip().casefold()
                if tag in self.codes and any(char.isalpha() for char in tag):
                    codes.add(tag)
        if codes:
            exact = [entry for code in codes for entry in self.codes.get(code, ())]
            if len(exact) == 1 and len(codes) == 1:
                return measure(source, entity, exact[0], "Código IFC")
            return Proposal(None, None, "Código BCCA no encontrado o ambiguo")

        description = " ".join(
            (getattr(obj, "Name", None) or "") + " " + (getattr(obj, "Description", None) or "")
            for obj in (entity, element_type) if obj is not None
        )
        if source.schema_identifier.upper().startswith("IFC4X3"):
            rule = self._rule_proposal(source, entity, element_type, description)
            if rule is not None:
                return rule
        words = _tokens(description)
        candidates = set(i for word in words for i in self.index.get(word, ()))
        scores = []
        for i in candidates:
            overlap = len(words & self.words[i])
            if overlap >= 2:
                scores.append((2 * overlap / (len(words) + len(self.words[i])), i))
        scores.sort(reverse=True)
        if (scores and scores[0][0] >= 0.60
                and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.10)):
            return measure(source, entity, self.entries[scores[0][1]], "Descripción · revisar")
        return Proposal(None, None, "Sin equivalencia clara" if self.entries else "Carga una base BCCA")

    def _rule_proposal(
        self, source: file, entity: entity_instance, element_type: entity_instance | None,
        description: str,
    ) -> Proposal | None:
        """Resuelve la tabla antes de sugerir por texto y no desempata reglas distintas."""
        from ifcopenshell.util.element import get_materials, get_predefined_type

        rules = self.rules.get(entity.is_a())
        if not rules:
            return None
        predefined = get_predefined_type(entity) or ""
        type_name = getattr(element_type, "Name", None) or ""
        text = _text(" ".join([description, *(m.Name or "" for m in get_materials(entity))]))
        matches = {
            (rule.base.casefold(), rule.code.casefold(), unit_key(rule.unit))
            for rule in rules
            if (not rule.predefined or predefined.casefold() == rule.predefined.casefold())
            and (not rule.type_name or _text(type_name).strip() == _text(rule.type_name).strip())
            and all(re.search(r"(?<!\w)" + re.escape(_text(term)) + r"(?!\w)", text)
                    for term in rule.contains)
        }
        if not matches:
            return None
        if len(matches) != 1:
            return Proposal(None, None, "Reglas IFC 4.3 contradictorias")
        base, code, unit = matches.pop()
        entries = [entry for entry in self.codes.get(code, ())
                   if entry.base.path.stem.casefold() == base and unit_key(entry.unit) == unit]
        if len(entries) != 1:
            return Proposal(None, None, "Regla IFC 4.3: base, código o unidad no disponible")
        return measure(source, entity, entries[0], "Tabla IFC 4.3 · revisar")


def measure(source: file, entity: entity_instance, entry: Entry, reason: str) -> Proposal:
    """Propone una cantidad IFC expresada en la unidad de la partida."""
    from ifcopenshell.util.element import get_psets
    from ifcopenshell.util.unit import get_property_unit, get_unit_scale

    key = unit_key(entry.unit)
    if key == "ud":
        return Proposal(entry, 1.0, reason)
    if key not in _DIMENSIONS:
        return Proposal(entry, None, f"{reason} · unidad no automática")
    quantity_type, unit_type, divisor = _DIMENSIONS[key]
    quantities: set[float] = set()
    for qto in get_psets(entity, qtos_only=True, verbose=True).values():
        for value in qto.values():
            if not isinstance(value, dict) or value.get("class") != quantity_type:
                continue
            number = value["value"]
            if not isinstance(number, (int, float)) or not isfinite(number) or number <= 0:
                continue
            unit = get_property_unit(source.by_id(value["id"]), source)
            if (unit is None or getattr(unit, "UnitType", None) != unit_type
                    or not (unit.is_a("IfcSIUnit") or unit.is_a("IfcConversionBasedUnit"))):
                continue
            scale = get_unit_scale(unit)
            if isfinite(scale) and scale > 0:
                converted = round_half_up(number * scale / divisor)
                if converted > 0:
                    quantities.add(converted)
    if len(quantities) != 1:
        why = "varias cantidades IFC" if quantities else "sin cantidad IFC compatible"
        return Proposal(entry, None, f"{reason} · {why}")
    return Proposal(entry, quantities.pop(), reason)
