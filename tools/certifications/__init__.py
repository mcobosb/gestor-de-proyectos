"""Plan económico mensual y certificaciones editables por partida.

Informes ofrece los modelos PDF/ODT «Resumen certificaciones» y «Certificaciones
con mediciones» para el mes seleccionado. Las cantidades se deducen del importe
mensual y el precio unitario; no se reutilizan las líneas del presupuesto.
Los porcentajes, la baja y las firmas proceden de Archivo > Configuración.
El informe de desviaciones usa la misma plantilla y el formato configurado.
"""
from __future__ import annotations

import calendar
import html
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from budget_core import format_chapter_code, format_currency, parse_float
from budget_config import APPLICATION_NAME
from .._shared.scheduling import PlanParams, apply_saved_plan_state, build_activities, schedule
from budget_reports import (
    CERTIFICATION_REPORT_NAMES, DEVIATION_REPORT_NAME, ReportSettings, write_certification_pdf,
)
from odt_reports import write_certification_odt

if TYPE_CHECKING:
    from bc3_gui import BudgetApp

TOOL_LABEL = "Certificaciones"
TOOL_ORDER = 40


def open_tool_qt(app) -> None:
    """Abre una única ventana de certificaciones para el proyecto activo."""
    from PySide6.QtWidgets import QMessageBox
    from .qt_window import CertificationWindowQt

    if not app.budget_items:
        QMessageBox.information(
            app, TOOL_LABEL, "Carga o crea un presupuesto antes de certificar."
        )
        return
    for window in app._project_windows:
        if isinstance(window, CertificationWindowQt) and window.isVisible():
            window.raise_()
            window.activateWindow()
            return
    try:
        window = CertificationWindowQt(app)
    except ValueError as exc:
        QMessageBox.critical(app, TOOL_LABEL, str(exc))
        return
    app._project_windows.append(window)
    window.show()


MONTHS_IN_PLAN = 24


@dataclass(frozen=True)
class EconomicMonth:
    start: date
    end: date

    @property
    def key(self) -> str:
        """Devuelve el primer día del mes en ISO como clave de almacenamiento."""
        return self.start.isoformat()

    @property
    def label(self) -> str:
        """Devuelve una etiqueta abreviada del mes y año para las cabeceras."""
        return self.start.strftime("%b %y").capitalize()


def _add_months(day: date, months: int) -> date:
    """Desplaza una fecha al primer día del mes situado a la distancia indicada."""
    month = day.month - 1 + months
    return date(day.year + month // 12, month % 12 + 1, 1)


def economic_months(start: date, count: int = MONTHS_IN_PLAN) -> list[EconomicMonth]:
    """Construye los intervalos mensuales del horizonte económico desde la fecha inicial."""
    first = date(start.year, start.month, 1)
    return [
        EconomicMonth(
            start=_add_months(first, index),
            end=date(
                _add_months(first, index).year,
                _add_months(first, index).month,
                calendar.monthrange(
                    _add_months(first, index).year, _add_months(first, index).month
                )[1],
            ),
        )
        for index in range(count)
    ]


def _working_days(start: date, end: date, holidays: set[date]) -> int:
    """Cuenta días laborables del intervalo, excluyendo fines de semana y festivos."""
    if end < start:
        return 0
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            days += 1
        current = current.fromordinal(current.toordinal() + 1)
    return days


def planned_monthly_amounts(activities, params: PlanParams, months: list[EconomicMonth]) -> list[list[float]]:
    """Reparte cada actividad por sus días laborables, como el Plan económico de Excel."""
    schedule(activities, params)
    plan: list[list[float]] = []
    for activity in activities:
        values = [0.0] * len(months)
        holidays = params.holiday_dates
        for segment in activity.segments or [activity]:
            start, end = segment.start_date, segment.end_date
            if not start or not end:
                continue
            total_days = max(1, _working_days(start, end, holidays))
            assigned, covered_days, last = 0.0, 0, None
            for index, month in enumerate(months):
                days = _working_days(max(start, month.start), min(end, month.end), holidays)
                if days:
                    amount = segment.amount * days / total_days
                    values[index] += amount
                    assigned += amount
                    covered_days += days
                    last = index
            if last is not None and (not params.phases or covered_days == total_days):
                values[last] += segment.amount - assigned
        plan.append(values)
    return plan


def reconcile_certification(plan: list[float], month_index: int, certified: float) -> float:
    """Ajusta el plan tras certificar y devuelve el exceso no compensable."""
    difference = plan[month_index] - certified
    plan[month_index] = certified
    if math.isclose(difference, 0.0, abs_tol=0.005):
        return 0.0
    if difference > 0:
        if month_index + 1 < len(plan):
            plan[month_index + 1] += difference
            return 0.0
        return difference

    pending_reduction = -difference
    for index in range(month_index + 1, len(plan)):
        reduction = min(plan[index], pending_reduction)
        plan[index] -= reduction
        pending_reduction -= reduction
        if math.isclose(pending_reduction, 0.0, abs_tol=0.005):
            return 0.0
    return pending_reduction


def certification_report_html(
    items, activities, plan: list[list[float]], month: EconomicMonth,
    certification_number: int, project_title: str,
    currency: str = "EUR", custom_currency: str = "",
) -> str:
    """Genera un informe imprimible con las partidas certificadas en un mes."""
    rows: list[str] = []
    total_plan = 0.0
    total_certified = 0.0
    for row, (item, activity) in enumerate(zip(items, activities)):
        certified = item.certifications.get(month.key, 0.0)
        if math.isclose(certified, 0.0, abs_tol=0.005):
            continue
        planned = item.certification_plans.get(month.key, plan[row][certification_number - 1])
        difference = certified - planned
        total_plan += planned
        total_certified += certified
        rows.append(
            "<tr>"
            f"<td>{html.escape(format_chapter_code(item.chapter))}</td>"
            f"<td>{html.escape(item.code)}</td>"
            f"<td>{html.escape(activity.summary)}</td>"
            f'<td class="num">{html.escape(format_currency(planned, currency, custom_currency))}</td>'
            f'<td class="num">{html.escape(format_currency(certified, currency, custom_currency))}</td>'
            f'<td class="num">{html.escape(format_currency(difference, currency, custom_currency))}</td>'
            f"<td>{html.escape(item.certification_justifications.get(month.key, ''))}</td>"
            "</tr>"
        )
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>Certificación {certification_number} · {html.escape(month.label)}</title>
<style>
body {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 11px; color: #1a1a1a; margin: 24px; }}
h1 {{ font-size: 18px; margin-bottom: 3px; }}
p {{ margin-top: 0; color: #555; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #c8d0da; padding: 4px 6px; vertical-align: top; }}
th {{ background: #1f4e78; color: #fff; }}
td.num {{ text-align: right; white-space: nowrap; }}
tfoot td {{ background: #f2f5f9; font-weight: bold; }}
@media print {{ body {{ margin: 8mm; }} }}
</style></head><body>
<h1>{html.escape(project_title)}</h1>
<p>Certificación {certification_number} · {html.escape(month.label)}</p>
<table><thead><tr><th>Capítulo</th><th>Código</th><th>Partida</th><th>Plan</th>
<th>Certificado</th><th>Desviación</th><th>Justificación</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
<tfoot><tr><td colspan="3">TOTAL</td><td class="num">{html.escape(format_currency(total_plan, currency, custom_currency))}</td>
<td class="num">{html.escape(format_currency(total_certified, currency, custom_currency))}</td>
<td class="num">{html.escape(format_currency(total_certified - total_plan, currency, custom_currency))}</td><td></td></tr></tfoot>
</table></body></html>"""


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
