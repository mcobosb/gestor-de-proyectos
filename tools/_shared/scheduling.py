"""Calculo de plazos compartido por Planificacion, Certificaciones y PPTP."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

DEFAULT_START = date(2026, 11, 9)
HOURS_PER_DAY = 8

# Festivos nacionales y de Andalucía (revisar y añadir los locales de Lucena)
DEFAULT_HOLIDAYS = [
    (date(2026, 12, 8), "Inmaculada Concepción"),
    (date(2026, 12, 25), "Navidad"),
    (date(2027, 1, 1), "Año Nuevo"),
    (date(2027, 1, 6), "Epifanía"),
    (date(2027, 3, 1), "Día de Andalucía (traslado, revisar)"),
    (date(2027, 3, 25), "Jueves Santo"),
    (date(2027, 3, 26), "Viernes Santo"),
    (date(2027, 10, 12), "Fiesta Nacional"),
    (date(2027, 11, 1), "Todos los Santos"),
    (date(2027, 12, 6), "Constitución"),
    (date(2027, 12, 8), "Inmaculada Concepción"),
    (date(2027, 12, 25), "Navidad"),
]

# Capítulos cuyas actividades se extienden por defecto a toda la obra
CONTINUOUS_CHAPTER_RE = re.compile(r"SEGURIDAD|RESIDUO", re.I)

RELATIONS = ("FC", "CC", "FF", "CF")


@dataclass
class PlanParams:
    start: date = DEFAULT_START
    hours_per_day: float = float(HOURS_PER_DAY)
    scale: int = 7
    plan: str = "Completo"
    holidays: list[tuple[date, str]] = field(default_factory=lambda: list(DEFAULT_HOLIDAYS))

    @property
    def holiday_dates(self) -> set[date]:
        """Devuelve los festivos como conjunto para el cálculo de días laborables."""
        return {d for d, _ in self.holidays}


@dataclass
class PlanActivity:
    id: int
    chapter: str
    chapter_name: str
    code: str
    summary: str
    unit: str
    quantity: float
    amount: float
    rendimiento: float
    teams: int = 1
    manual_duration: int | None = None
    continuous: bool = False
    pred: int | None = None
    relation: str = "FC"
    lag: int = 0
    duration: int = 1
    start_offset: int = 0
    finish_offset: int = 1
    start_date: date | None = None
    end_date: date | None = None

    def computed_duration(self, hours_per_day: float) -> int:
        """Duración = medición × rendimiento / (nº equipos × horas por jornada)."""
        if self.manual_duration:
            return max(1, int(self.manual_duration))
        teams = max(1, self.teams)
        workload = self.quantity * self.rendimiento
        if workload <= 0 or hours_per_day <= 0:
            return 1
        return max(1, math.ceil(workload / (teams * hours_per_day)))


def nth_working_day(start: date, index: int, holidays: set[date]) -> date:
    """Devuelve el día laborable nº `index` (1 = primero) a partir de `start`."""
    current = start
    found = 0
    while True:
        if current.weekday() < 5 and current not in holidays:
            found += 1
            if found >= max(1, index):
                return current
        current += timedelta(days=1)


def schedule(activities: list[PlanActivity], params: PlanParams) -> None:
    """Calcula duraciones, desfases en días laborables y fechas de cada actividad."""
    by_id = {act.id: act for act in activities}
    for act in activities:
        act.duration = act.computed_duration(params.hours_per_day)
        act.start_offset = 0
        act.finish_offset = act.duration

    # Varias pasadas: admite predecesoras definidas en filas posteriores.
    for _ in range(len(activities) + 1):
        changed = False
        for act in activities:
            pred = by_id.get(act.pred) if act.pred else None
            offset = 0
            if pred is not None and pred is not act:
                if act.relation == "CC":
                    offset = pred.start_offset + act.lag
                elif act.relation == "FF":
                    offset = pred.finish_offset - act.duration + act.lag
                elif act.relation == "CF":
                    offset = pred.start_offset - act.duration + act.lag
                else:  # FC
                    offset = pred.finish_offset + act.lag
            offset = max(0, offset)
            if offset != act.start_offset:
                act.start_offset = offset
                changed = True
            finish = offset + act.duration
            if not act.continuous and finish != act.finish_offset:
                act.finish_offset = finish
                changed = True
        if not changed:
            break

    base = max((a.finish_offset for a in activities if not a.continuous), default=1)
    for act in activities:
        if act.continuous:
            act.start_offset = 0
            act.finish_offset = base
            act.duration = base

    holidays = params.holiday_dates
    for act in activities:
        act.start_date = nth_working_day(params.start, act.start_offset + 1, holidays)
        act.end_date = nth_working_day(params.start, max(act.finish_offset, act.start_offset + 1), holidays)


def chain_sequence(activities: list[PlanActivity]) -> None:
    """Encadena las actividades una tras otra (fin-comienzo, sin desfase)."""
    previous: PlanActivity | None = None
    for act in activities:
        if act.continuous:
            act.pred = previous.id if previous else None
            act.relation = "CC"
            act.lag = 0
            continue
        act.pred = previous.id if previous else None
        act.relation = "FC"
        act.lag = 0
        previous = act


def build_activities(items, chapter_title) -> list[PlanActivity]:
    """Convierte partidas en actividades y marca las que abarcan toda la obra."""
    activities: list[PlanActivity] = []
    for index, item in enumerate(items, start=1):
        name = chapter_title(item.chapter)
        activities.append(
            PlanActivity(
                id=index,
                chapter=item.chapter,
                chapter_name=name,
                code=item.code,
                summary=item.summary,
                unit=item.unit,
                quantity=item.measurement,
                amount=item.import_total,
                rendimiento=item.rendimiento,
                continuous=bool(CONTINUOUS_CHAPTER_RE.search(name)),
            )
        )
    chain_sequence(activities)
    return activities


def apply_saved_plan_state(
    params: PlanParams, activities: list[PlanActivity], state: dict | None
) -> None:
    """Aplica los parámetros y la secuencia guardados a actividades reconstruidas."""
    if not state:
        return
    raw_params = state.get("params", {})
    if not isinstance(raw_params, dict):
        raise ValueError("Los parámetros de planificación guardados no son válidos.")
    try:
        params.start = date.fromisoformat(raw_params.get("start", params.start.isoformat()))
        params.hours_per_day = float(raw_params.get("hours_per_day", params.hours_per_day))
        params.scale = int(raw_params.get("scale", params.scale))
    except (TypeError, ValueError) as exc:
        raise ValueError("Los parámetros de planificación guardados no son válidos.") from exc
    params.plan = str(raw_params.get("plan", params.plan))
    holidays: list[tuple[date, str]] = []
    for entry in raw_params.get("holidays", []):
        try:
            holidays.append((date.fromisoformat(entry["date"]), str(entry["description"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Hay un festivo guardado con datos no válidos.") from exc
    params.holidays = holidays

    saved_by_activity: dict[tuple[str, str], list[dict]] = {}
    for entry in state.get("activities", []):
        if not isinstance(entry, dict):
            raise ValueError("Hay una actividad guardada con datos no válidos.")
        key = (str(entry.get("chapter", "")), str(entry.get("code", "")))
        saved_by_activity.setdefault(key, []).append(entry)

    for activity in activities:
        matches = saved_by_activity.get((activity.chapter, activity.code), [])
        if not matches:
            continue
        saved = matches.pop(0)
        try:
            activity.teams = max(1, int(saved.get("teams", activity.teams)))
            manual = saved.get("manual_duration")
            activity.manual_duration = int(manual) if manual is not None else None
            activity.continuous = bool(saved.get("continuous", activity.continuous))
            pred = saved.get("pred")
            activity.pred = int(pred) if pred is not None else None
            relation = str(saved.get("relation", activity.relation)).upper()
            activity.relation = relation if relation in RELATIONS else "FC"
            activity.lag = int(saved.get("lag", activity.lag))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"La actividad guardada {activity.code} contiene datos no válidos."
            ) from exc


def parse_date(text: str) -> date | None:
    """Interpreta una fecha día/mes/año y devuelve None si no es válida."""
    text = (text or "").strip().replace("-", "/")
    parts = text.split("/")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(p) for p in parts)
        return date(year, month, day)
    except ValueError:
        return None

