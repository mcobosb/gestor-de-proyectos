"""Incorporación explícita de propuestas BIM al presupuesto activo."""
from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import TYPE_CHECKING

from budget_core import MeasurementLine, ProjectItem
from .translation import Entry, Proposal, unit_key

if TYPE_CHECKING:
    from budget_qt_window import BudgetMainWindow
    from .scene import Element


def existing_links(app: BudgetMainWindow) -> set[str]:
    """Localiza mediciones IFC guardadas o añadidas en esta sesión."""
    return {
        line.ifc_global_id for item in app.budget_items for line in item.measurement_lines
        if line.ifc_global_id
    }


def incorporate(
    app: BudgetMainWindow, rows: list[tuple[Element, Proposal]]
) -> tuple[set[str], dict[str, str]]:
    """Añade una línea por GlobalId y devuelve las filas que no se incorporaron."""
    known = existing_links(app)
    errors: dict[str, str] = {}
    groups: dict[tuple[str, str], list[tuple[Element, Entry, float]]] = defaultdict(list)
    for element, proposal in rows:
        entry, quantity = proposal.entry, proposal.quantity
        if element.key in known:
            errors[element.key] = "Ya incorporado"
        elif element.key.startswith("#"):
            errors[element.key] = "El elemento no tiene GlobalId"
        elif entry is None or quantity is None or not isfinite(quantity) or quantity < 0.01:
            errors[element.key] = "Falta una partida o cantidad válida"
        elif not any(entry.base is base for base in app.reference_bases):
            errors[element.key] = "La base ya no está cargada"
        else:
            groups[(entry.base.path.stem, entry.code)].append((element, entry, quantity))
            known.add(element.key)

    added: set[str] = set()
    targets: dict[tuple[str, str], list[ProjectItem]] = defaultdict(list)
    for item in app.budget_items:
        if item.source == "BCCA":
            targets[item.reference_base, item.code].append(item)

    for key, group in groups.items():
        entry = group[0][1]
        matches = targets[key]
        same_stem = [base for base in app.reference_bases if base.path.stem == key[0]]
        if len(same_stem) != 1 or len(matches) > 1:
            errors.update((element.key, "Destino ambiguo en el presupuesto o las bases")
                          for element, _, _ in group)
            continue
        item = matches[0] if matches else app._build_item_from_reference(entry.base, entry.code)
        if item is None or unit_key(item.unit) != unit_key(entry.unit):
            errors.update((element.key, "La unidad de la partida no coincide") for element, _, _ in group)
            continue
        if matches and not app._acquire_item_lock(item, "measurements"):
            errors.update((element.key, "Partida bloqueada") for element, _, _ in group)
            continue
        try:
            if not matches:
                item.measurement_lines.clear()
                app._ensure_chapter(item.chapter)
                app.budget_items.append(item)
            elif not item.measurement_lines and item.measurement:
                item.measurement_lines.append(MeasurementLine(
                    comment="Medición anterior", units=item.measurement,
                ))
            for element, _, quantity in group:
                name = " ".join(element.name.replace("\\", " ").split())
                item.measurement_lines.append(MeasurementLine(
                    comment=f"IFC {element.key} · {name}", units=quantity,
                    ifc_global_id=element.key,
                ))
                added.add(element.key)
            item.recalculate_measurement()
        finally:
            if matches:
                app._release_item_lock(item.storage_id, "measurements")
    if added:
        app.refresh_budget_tree()
        app._mark_dirty()
    return added, errors
