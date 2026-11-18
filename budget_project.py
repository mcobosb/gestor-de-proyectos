"""Casos de uso y persistencia de proyectos sin dependencia de framework GUI."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from budget_core import (
    Bc3Dataset,
    Concept,
    MeasurementLine,
    ProjectItem,
    format_chapter_code,
    is_chapter_code,
)
from budget_reports import ReportSettings


@dataclass(frozen=True)
class ProjectDocument:
    """Estado validado leído de un archivo JSON."""

    items: list[ProjectItem]
    chapter_order: list[str]
    chapter_titles: dict[str, str]
    settings: ReportSettings
    title: str | None
    source_budget_path: Path | None
    budget_dataset: Bc3Dataset | None
    planning: dict
    invoicing: dict
    rcds: dict
    safety_health: dict
    pptp: dict
    price_justification: dict
    missing_source: Path | None = None


def item_from_data(data: dict) -> ProjectItem:
    """Reconstruye una partida desde su representación JSON."""
    round_import_total = data.get("round_import_total", False)
    if not isinstance(round_import_total, bool):
        raise ValueError("El redondeo del importe de la partida debe ser booleano.")
    measurement_lines = [
        MeasurementLine(
            comment=str(line.get("comment", "")),
            units=line.get("units"),
            length=line.get("length"),
            width=line.get("width"),
            height=line.get("height"),
            k=float(line.get("k", 1.0)),
        )
        for line in data.get("measurement_lines", [])
    ]
    return ProjectItem(
        code=str(data["code"]),
        chapter=str(data["chapter"]),
        unit=str(data.get("unit", "")),
        summary=str(data.get("summary", "")),
        measurement=float(data.get("measurement", 0.0)),
        unit_price=float(data.get("unit_price", 0.0)),
        round_import_total=round_import_total,
        direct_unit_price=(
            float(data["direct_unit_price"])
            if data.get("direct_unit_price") is not None
            else None
        ),
        rendimiento=float(data.get("rendimiento", 0.0)),
        source=str(data.get("source", "manual")),
        reference_base=str(data.get("reference_base", "")),
        text=str(data.get("text", "")),
        measurement_detail=str(data.get("measurement_detail", "")),
        decomposition=[
            (str(code), float(quantity))
            for code, quantity in data.get("decomposition", [])
        ],
        component_prices={
            str(code): float(price)
            for code, price in data.get("component_prices", {}).items()
        },
        concept_type=str(data.get("concept_type", "")),
        component_types={
            str(code): str(kind)
            for code, kind in data.get("component_types", {}).items()
        },
        component_concepts={
            str(code): Concept(
                code=str(code),
                unit=str(concept.get("unit", "")),
                summary=str(concept.get("summary", "")),
                price=float(concept.get("price", 0.0)),
                ctype=str(concept.get("ctype", "0")),
                text=str(concept.get("text", "")),
                children=[
                    (str(child), float(quantity))
                    for child, quantity in concept.get("children", [])
                ],
            )
            for code, concept in data.get("component_concepts", {}).items()
        },
        measurement_lines=measurement_lines,
        planned_amounts={
            str(month): float(amount)
            for month, amount in data.get("planned_amounts", {}).items()
        },
        certifications={
            str(month): float(amount)
            for month, amount in data.get("certifications", {}).items()
        },
        certification_plans={
            str(month): float(amount)
            for month, amount in data.get("certification_plans", {}).items()
        },
        certification_justifications={
            str(month): str(text)
            for month, text in data.get("certification_justifications", {}).items()
        },
    )


def build_project_payload(state: Any) -> dict:
    """Serializa el estado editable de una fachada de proyecto."""
    source = (
        str(state.source_budget_path.resolve())
        if state.source_budget_path
        else None
    )
    return {
        "format_version": 2,
        "title": state._project_title(),
        "source_budget_bc3": source,
        "chapters": {
            "order": state.chapter_order,
            "titles": state.chapter_titles,
        },
        "planning": state.planning_state,
        "invoicing": state.invoicing_state,
        "rcds": state.rcd_state,
        "safety_health": state.safety_health_state,
        "pptp": state.pptp_state,
        "price_justification": state.price_justification_state,
        "report_settings": state.report_settings.to_dict(),
        "items": [_item_payload(item) for item in state.budget_items],
    }


def _item_payload(item: ProjectItem) -> dict:
    """Convierte una partida en su representación persistente."""
    return {
        "code": item.code,
        "chapter": item.chapter,
        "unit": item.unit,
        "summary": item.summary,
        "measurement": item.measurement,
        "unit_price": item.unit_price,
        "round_import_total": item.round_import_total,
        "direct_unit_price": item.direct_unit_price,
        "rendimiento": item.rendimiento,
        "source": item.source,
        "reference_base": item.reference_base,
        "text": item.text,
        "measurement_detail": item.measurement_detail,
        "component_prices": item.component_prices,
        "concept_type": item.concept_type,
        "component_types": item.component_types,
        "component_concepts": {
            code: {
                "unit": concept.unit,
                "summary": concept.summary,
                "price": concept.price,
                "ctype": concept.ctype,
                "text": concept.text,
                "children": concept.children,
            }
            for code, concept in item.component_concepts.items()
        },
        "planned_amounts": item.planned_amounts,
        "certifications": item.certifications,
        "certification_plans": item.certification_plans,
        "certification_justifications": item.certification_justifications,
        "measurement_lines": [
            {
                "comment": line.comment,
                "units": line.units,
                "length": line.length,
                "width": line.width,
                "height": line.height,
                "k": line.k,
            }
            for line in item.measurement_lines
        ],
        "decomposition": item.decomposition,
    }


def read_project(
    project_path: Path,
    default_settings: ReportSettings,
    *,
    budget_dataset: Bc3Dataset | None = None,
    source_budget_path: Path | None = None,
) -> ProjectDocument:
    """Valida un proyecto JSON y carga su BC3 de origen cuando está disponible."""
    raw = json.loads(project_path.read_text(encoding="utf-8"))
    payload = {"items": raw} if isinstance(raw, list) else raw
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("El archivo no contiene una lista de partidas válida.")
    settings = ReportSettings.from_dict(
        payload.get("report_settings", default_settings.to_dict())
    )
    missing_source: Path | None = None
    if source_budget_path is not None:
        declared_source = payload.get("source_budget_bc3")
        if declared_source is not None and Path(str(declared_source)).name != source_budget_path.name:
            raise ValueError(
                f"El proyecto está asociado a '{declared_source}', "
                f"no a '{source_budget_path.name}'."
            )
    if source_budget_path is None and payload.get("source_budget_bc3"):
        source_budget_path = Path(str(payload["source_budget_bc3"]))
        if not source_budget_path.is_absolute():
            source_budget_path = project_path.parent / source_budget_path
        if source_budget_path.is_file():
            budget_dataset = Bc3Dataset(source_budget_path)
        else:
            local_source = project_path.parent / source_budget_path.name
            if local_source.is_file():
                source_budget_path = local_source
                budget_dataset = Bc3Dataset(local_source)
            else:
                missing_source = source_budget_path
    items: list[ProjectItem] = []
    for entry in payload["items"]:
        if not isinstance(entry, dict):
            raise ValueError("Hay una partida con datos no válidos.")
        items.append(item_from_data(entry))
    for item in items:
        item.set_indirect_costs(settings.indirect_costs)
    chapters = payload.get("chapters", {})
    order = chapters.get("order", []) if isinstance(chapters, dict) else []
    chapter_order = [str(code) for code in order]
    for item in items:
        if item.chapter not in chapter_order:
            chapter_order.append(item.chapter)
    titles = chapters.get("titles", {}) if isinstance(chapters, dict) else {}
    chapter_titles = (
        {str(code): str(title) for code, title in titles.items()}
        if isinstance(titles, dict)
        else {}
    )
    if budget_dataset is not None:
        for code, concept in budget_dataset.concepts.items():
            if is_chapter_code(code) and not code.endswith("##"):
                chapter_titles.setdefault(code, concept.summary)
    title = payload.get("title")
    return ProjectDocument(
        items=items,
        chapter_order=chapter_order,
        chapter_titles=chapter_titles,
        settings=settings,
        title=str(title) if title else None,
        source_budget_path=source_budget_path,
        budget_dataset=budget_dataset,
        planning=dict(payload.get("planning", {})),
        invoicing=dict(payload.get("invoicing", {})),
        rcds=dict(payload.get("rcds", {})),
        safety_health=dict(payload.get("safety_health", {})),
        pptp=dict(payload.get("pptp", {})),
        price_justification=dict(payload.get("price_justification", {})),
        missing_source=missing_source,
    )


def write_project(path: Path, payload: dict) -> None:
    """Escribe un proyecto JSON mediante sustitución atómica."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(json.dumps(payload, ensure_ascii=False, indent=2))
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def project_title_for(state: Any) -> str:
    """Deduce el título del proyecto sin consultar controles visuales."""
    if state.project_title:
        return state.project_title
    if state.budget_dataset:
        for code, concept in state.budget_dataset.concepts.items():
            if code.endswith("##") and concept.summary:
                return concept.summary
    return "Presupuesto"


def chapter_title_for(state: Any, chapter: str) -> str:
    """Deduce el título de un capítulo desde proyecto o bases cargadas."""
    if chapter in state.chapter_titles:
        return state.chapter_titles[chapter]
    for dataset in (state.budget_dataset, *state.reference_bases):
        if dataset and chapter in dataset.concepts:
            summary = dataset.concepts[chapter].summary
            if summary:
                return summary
    return format_chapter_code(chapter)


def reference_base_for(state: Any, item: ProjectItem) -> Bc3Dataset | None:
    """Selecciona la base de referencia que corresponde a una partida."""
    for base in state.reference_bases:
        if item.reference_base and base.path.stem == item.reference_base:
            return base
    for base in state.reference_bases:
        if item.code in base.concepts:
            return base
    return state.reference_bases[0] if state.reference_bases else None


def component_dataset_for(state: Any, item: ProjectItem) -> Bc3Dataset | None:
    """Selecciona la fuente válida para resolver descomposiciones."""
    return reference_base_for(state, item) if item.source == "BCCA" else state.budget_dataset


def budget_concept_type(
    state: Any, item: ProjectItem, component_code: str | None = None
) -> str:
    """Calcula el tipo de una partida o componente respetando overrides."""
    from budget_config import CONCEPT_TYPES

    override = (
        item.concept_type
        if component_code is None
        else item.component_types.get(component_code, "")
    )
    if override in CONCEPT_TYPES and override != "chapter":
        return override
    code = item.code if component_code is None else component_code
    if code.startswith("%"):
        return "%"
    dataset = component_dataset_for(state, item)
    concept = item.component_concepts.get(code) if component_code is not None else None
    if concept is None:
        concept = dataset.concepts.get(code) if dataset else None
    if concept is None:
        return "0"
    if concept.ctype == "0" and component_code is not None and concept.children:
        return "aux"
    return concept.ctype if concept.ctype in {"0", "1", "2", "3"} else "0"


def report_components(
    state: Any, item: ProjectItem
) -> list[tuple[Concept, float, float]]:
    """Obtiene la descomposición efectiva utilizada por informes."""
    rows = item.cost_components(component_dataset_for(state, item))
    return rows + item.indirect_components()
