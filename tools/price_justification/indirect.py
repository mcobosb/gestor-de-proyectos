"""Cálculo de indirectos del anejo, independiente del recargo del presupuesto."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite

from budget_core import fmt_number

GROUPS = {
    "staff": ("Personal de obra", ("Jefatura de obra", "Encargado de obra", "Administración de obra")),
    "facilities": ("Instalaciones provisionales", ("Caseta de obra", "Suministros provisionales", "Almacén provisional")),
    "studies": ("Levantamientos y análisis", ("Levantamiento topográfico", "Ensayos y análisis", "Otros trabajos técnicos")),
}
HEADERS = ("UD", "Puesto / concepto", "Coste/mes (€/mes)", "Tiempo (meses)", "Dedicación (%)", "Coste (€)")
RATIOS = (0.07, 0.33, 0.16, 0.14, 0.15, 0.15)
FIELDS_BY_GROUP = {
    "staff": {0: "quantity", 2: "monthly", 3: "months", 4: "dedication"},
    "facilities": {0: "quantity", 2: "monthly", 3: "months"},
    "studies": {5: "total"},
}
GROUP_HEADERS = {
    "staff": HEADERS,
    "facilities": ("UD", "Instalación", HEADERS[2], HEADERS[3], "", HEADERS[5]),
    "studies": ("", "Levantamiento / análisis", "", "", "", "Coste total (€)"),
}
DEFAULT_NUMBERS = {"quantity": 1, "monthly": 0, "months": 1, "dedication": 100, "total": 0}
CENT = Decimal("0.01")
LIMIT = Decimal(2)
CONTINGENCY = Decimal(1)


def new_row(group: str, option: int = 0) -> dict:
    """Crea una fila con los campos y valores iniciales aplicables a su grupo."""
    return {"group": group, "role": GROUPS[group][1][option],
            **{key: DEFAULT_NUMBERS[key] for key in FIELDS_BY_GROUP[group].values()}}


def default_rows() -> list[dict]:
    """Propone conceptos básicos sin imputar tarifas no confirmadas."""
    return [*(new_row(group) for group in GROUPS), new_row("studies", 1)]


def field_maximum(key: str) -> int:
    """Acota los campos editables; el importe directo admite partidas agrupadas."""
    return {"dedication": 100, "total": 999_999_999_999, "monthly": 999_999_999_999}.get(key, 999_999_999)


def _validated_number(row: dict, key: str) -> int | float:
    """Lee un valor numérico válido antes de calcular o migrar una fila."""
    value = row.get(key)
    if type(value) not in (int, float) or not 0 <= value <= field_maximum(key) or not isfinite(value):
        raise ValueError(f"Valor de coste indirecto no válido: {key}.")
    return value


def _migrate_row(row: dict, group: str) -> dict:
    """Conserva el importe imputado al retirar campos del formato anterior."""
    if group == "staff" or (group == "facilities" and "dedication" not in row) or (
        group == "studies" and "total" in row
    ):
        return row
    keys = FIELDS_BY_GROUP["staff"].values()
    units, monthly, months, dedication = (Decimal(str(_validated_number(row, key))) for key in keys)
    effective = monthly * dedication / 100
    legacy_total = (units * effective * months).quantize(CENT, rounding=ROUND_HALF_UP)
    if group == "studies":
        return {**row, "total": float(legacy_total)}
    effective_cents = effective.quantize(CENT, rounding=ROUND_HALF_UP)
    if (units * effective_cents * months).quantize(CENT, rounding=ROUND_HALF_UP) == legacy_total:
        return {**row, "monthly": float(effective_cents)}
    return {**row, "quantity": 1, "months": 1, "monthly": float(legacy_total)}


def normalize_rows(rows) -> list[dict]:
    """Valida las cantidades y ordena las filas por grupo sin guardar derivados."""
    if not isinstance(rows, list):
        raise ValueError("Los costes indirectos deben ser una lista de filas.")
    normalized = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("group"), str)
                or row["group"] not in GROUPS):
            raise ValueError("Grupo de coste indirecto no válido.")
        role = row.get("role")
        if not isinstance(role, str) or not role.strip():
            raise ValueError("Indica el puesto o concepto del coste indirecto.")
        group = row["group"]
        row = _migrate_row(row, group)
        result = {"group": group, "role": role.strip()}
        for key in FIELDS_BY_GROUP[group].values():
            result[key] = _validated_number(row, key)
        normalized.append(result)
    return [row for group in GROUPS for row in normalized if row["group"] == group]


def row_cost(row: dict) -> Decimal:
    """Calcula el coste imputable de una fila, redondeado a céntimos."""
    if row["group"] == "studies":
        return Decimal(str(row["total"])).quantize(CENT, rounding=ROUND_HALF_UP)
    units, monthly, months = (Decimal(str(row[key])) for key in ("quantity", "monthly", "months"))
    factor = Decimal(str(row["dedication"])) / 100 if row["group"] == "staff" else 1
    return (units * monthly * months * factor).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class IndirectCosts:
    """Resultado y base usados por la ventana y por ambos formatos del anejo."""

    total: Decimal
    direct: Decimal
    raw: Decimal | None
    adopted: Decimal | None
    budget_rate: Decimal

    @property
    def rate(self) -> Decimal | None:
        """Suma los imprevistos al coeficiente propio, si hay una base válida."""
        return None if self.adopted is None else self.adopted + CONTINGENCY

    @property
    def differs(self) -> bool:
        """Compara los porcentajes a la precisión con la que se presentan."""
        rate = self.rate
        return rate is not None and rounded_rate(rate) != rounded_rate(self.budget_rate)


def calculate(app, rows: list[dict]) -> IndirectCosts:
    """Divide por el coste directo total sin incorporar recargos ni generar ciclos."""
    direct = Decimal(0)
    for item in app.budget_items:
        price = item.direct_unit_price if item.direct_unit_price is not None else item.unit_price
        amount = Decimal(str(item.measurement)) * Decimal(str(price))
        direct += amount.quantize(CENT, rounding=ROUND_HALF_UP) if item.round_import_total else amount
    total = sum((row_cost(row) for row in rows), Decimal(0))
    raw = 100 * total / direct if direct > 0 else None
    adopted = None if raw is None else min(raw, LIMIT)
    return IndirectCosts(total, direct, raw, adopted, Decimal(str(app.report_settings.indirect_costs)))


def rounded_rate(rate: Decimal) -> Decimal:
    """Redondea el porcentaje a la precisión del anejo y de la aplicación de K."""
    return rate.quantize(CENT, rounding=ROUND_HALF_UP)


def rate_label(rate: Decimal) -> str:
    """Muestra el porcentaje con el mismo redondeo que la comparación."""
    return f"{fmt_number(float(rounded_rate(rate)))} %"


def equivalent_interest(years: int, interest: int) -> Decimal:
    """Devuelve el interés medio anual equivalente para vidas enteras."""
    if interest == 0:
        return Decimal(0)
    factor = (1 + Decimal(interest) / 100) ** years
    return interest * factor / (factor - 1) - Decimal(100) / years
