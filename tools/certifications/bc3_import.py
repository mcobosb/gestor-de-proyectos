"""Validación de certificaciones FIEBDC a origen frente al presupuesto abierto."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, DecimalException
import math

from budget_core import Bc3Dataset, ProjectItem, norm_code, round_half_up


def certification_metadata(dataset: Bc3Dataset) -> tuple[int, date]:
    """Obtiene el número y el mes declarados en la cabecera FIEBDC."""
    if dataset.information_type != "3":
        raise ValueError("El BC3 no declara una certificación a origen.")
    if not dataset.certification_number.isdigit() or int(dataset.certification_number) < 1:
        raise ValueError("El BC3 no indica un número de certificación válido.")
    raw = dataset.certification_date
    if not raw.isdigit() or len(raw) > 8:
        raise ValueError("El BC3 no indica una fecha de certificación válida.")
    if len(raw) <= 2:
        raise ValueError("La fecha de certificación no indica el mes.")
    if len(raw) <= 4:
        raw = raw.zfill(4)
        day, month, year = 1, int(raw[:2]), int(raw[2:])
    elif len(raw) <= 6:
        raw = raw.zfill(6)
        day, month, year = int(raw[:2]) or 1, int(raw[2:4]), int(raw[4:])
    else:
        raw = raw.zfill(8)
        day, month, year = int(raw[:2]) or 1, int(raw[2:4]), int(raw[4:])
        if year < 1:
            raise ValueError("El año de certificación no es válido.")
        try:
            return int(dataset.certification_number), date(year, month, day).replace(day=1)
        except ValueError as exc:
            raise ValueError("La fecha de certificación no es válida.") from exc
    year += 1900 if year >= 80 else 2000
    try:
        return int(dataset.certification_number), date(year, month, day).replace(day=1)
    except ValueError as exc:
        raise ValueError("La fecha de certificación no es válida.") from exc


def _key(item: ProjectItem) -> tuple[str, str, str]:
    """Identifica una partida por su ubicación y código BC3."""
    return norm_code(item.chapter), norm_code(item.subchapter), item.code


def prepare_import(
    dataset: Bc3Dataset, items: list[ProjectItem]
) -> tuple[int, date, list[tuple[float, float]]]:
    """Valida la secuencia completa y calcula cantidades acumuladas e importes mensuales."""
    number, month = certification_metadata(dataset)
    month_key = month.isoformat()
    targets: dict[tuple[str, str, str], int] = {}
    for index, item in enumerate(items):
        key = _key(item)
        if key in targets:
            raise ValueError(f"La partida {item.code} aparece más de una vez en el presupuesto.")
        targets[key] = index

    quantities = [0.0] * len(items)
    seen: set[tuple[str, str, str]] = set()
    for source in dataset.to_project_items():
        key = _key(source)
        if key in seen:
            raise ValueError(f"La partida {source.code} aparece más de una vez en el BC3.")
        seen.add(key)
        if not math.isfinite(source.measurement) or source.measurement < 0:
            raise ValueError(f"La medición de {source.code} no es válida.")
        if source.measurement == 0:
            continue
        if key not in targets:
            raise ValueError(f"La partida certificada {source.code} no coincide con el presupuesto.")
        target = items[targets[key]]
        if source.unit.strip().casefold() != target.unit.strip().casefold():
            raise ValueError(f"La unidad de {source.code} no coincide con el presupuesto.")
        if (not math.isfinite(source.unit_price) or not math.isfinite(target.unit_price)
                or source.unit_price <= 0 or target.unit_price <= 0
                or abs(source.unit_price - target.unit_price) >= 0.005):
            raise ValueError(f"El precio de {source.code} no coincide con el presupuesto.")
        quantities[targets[key]] = source.measurement

    results = []
    expected_months: list[str] | None = None
    for item, quantity in zip(items, quantities):
        if not isinstance(item.certification_bc3, dict):
            raise ValueError("Los datos guardados de certificaciones BC3 no son válidos.")
        entries = item.certification_bc3
        if len(entries) != number - 1:
            raise ValueError("Importa primero todas las certificaciones BC3 anteriores, en orden.")
        if expected_months is None:
            expected_months = sorted(entries)
        elif sorted(entries) != expected_months:
            raise ValueError("Las certificaciones BC3 guardadas no coinciden entre partidas.")
        if not math.isfinite(item.unit_price) or item.unit_price < 0:
            raise ValueError(f"El precio de {item.code} no es válido.")
        previous = 0.0
        previous_amount = Decimal(0)
        previous_month = ""
        for expected, (key, entry) in enumerate(sorted(entries.items()), 1):
            if not isinstance(entry, dict) or entry.get("number") != expected:
                raise ValueError("La secuencia guardada de certificaciones BC3 no es válida.")
            if key >= month_key or key <= previous_month:
                raise ValueError("Las certificaciones deben importarse en meses distintos y por orden de fecha.")
            try:
                previous = float(entry["quantity"])
                amount = float(entry["amount"])
                price = float(entry["price"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Los datos guardados de certificaciones BC3 no son válidos.") from exc
            if (not math.isfinite(previous) or not math.isfinite(amount)
                    or not math.isfinite(price) or price != item.unit_price
                    or item.certifications.get(key) != amount):
                raise ValueError(f"La certificación anterior de {item.code} ha sido modificada.")
            previous_amount += Decimal(str(amount))
            previous_month = key
        if any(key not in entries for key in item.certifications):
            raise ValueError(f"La partida {item.code} tiene certificaciones manuales; revísalas antes de importar.")
        if quantity < previous:
            raise ValueError(f"La medición acumulada de {item.code} disminuye; revísala manualmente.")
        try:
            expected_amount = Decimal(str(round_half_up(
                Decimal(str(previous)) * Decimal(str(item.unit_price))
            )))
            cumulative_amount = Decimal(str(round_half_up(
                Decimal(str(quantity)) * Decimal(str(item.unit_price))
            )))
            amount = round_half_up(cumulative_amount - previous_amount)
        except DecimalException as exc:
            raise ValueError(f"El importe de {item.code} no se puede calcular.") from exc
        if previous_amount != expected_amount:
            raise ValueError(f"El acumulado anterior de {item.code} no coincide con sus importes mensuales.")
        results.append((quantity, amount))
    return number, month, results
