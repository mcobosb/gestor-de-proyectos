"""Propuesta revisable de partidas de Seguridad y Salud basada en la matriz BCCA."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from pathlib import Path, PurePosixPath
import re
from typing import TYPE_CHECKING
import unicodedata

from budget_reports import budget_totals
from localization import translate
from tools._shared.scheduling import PlanParams, apply_saved_plan_state, build_activities, schedule
from .care_centers import restore_centers
from .chapter_selection import (
    SAFETY_BUDGET_SOURCE, restore_chapter_selection,
    study_chapters, study_items,
)
from .external_conditions import restore_external_conditions
from .drawings import restore_drawings
from .regulations import restore_review, review_context
from .technical_conditions import restore_technical_review, technical_review_confirmed

if TYPE_CHECKING:
    from bc3_gui import BudgetApp, ProjectItem
    from tools._library_editor import LibraryEditor


TOOL_LABEL = "Seguridad y salud (BCCA · Junta de Andalucía)"
TOOL_ORDER = 20
LIBRARY_PATH = Path(__file__).resolve().parent / "BIBLIOTECA.zip"
CARE_CENTERS_URL = (
    "https://www.juntadeandalucia.es/organismos/presidenciasanidadyemergencias/"
    "areas/sanidad/sistema-sanitario/centros-servicios/paginas/registro-ces.html"
)


def is_available(app: BudgetApp) -> bool:
    """Mantiene la herramienta accesible aunque el presupuesto ya incluya Seguridad y Salud."""
    return True


def restore_project_state(data: dict) -> dict:
    """Valida la revision guardada sin volver a consultar la matriz."""
    if not data:
        return {}
    return serialize_state(
        *restore_state(data), centros_asistenciales=data.get("centros_asistenciales", ""),
        centros_asistenciales_modo=data.get("centros_asistenciales_modo", "texto"),
        centros_asistenciales_texto=data.get(
            "centros_asistenciales_texto", data.get("centros_asistenciales", ""),
        ),
        centros_asistenciales_datos=restore_centers(data),
        study_criteria=data.get("study_criteria"),
        external_conditions=data.get("external_conditions"),
        resumen_actuacion=data.get("resumen_actuacion"),
        regulatory_review=data.get("regulatory_review"),
        chapter_selection=data.get("chapter_selection"),
        technical_review=data.get("technical_review"),
        drawings=data.get("drawings"),
    )


def open_tool_qt(app) -> None:
    """Abre el selector de capítulos y el asistente Qt."""
    from PySide6.QtWidgets import QMessageBox
    from .qt_window import ChapterSelectionDialogQt, SafetyHealthWindowQt

    if not app.budget_items:
        QMessageBox.information(
            app, TOOL_LABEL,
            "Carga o crea un presupuesto antes de analizar Seguridad y Salud.",
        )
        return
    for window in app._project_windows:
        if isinstance(window, SafetyHealthWindowQt) and window.isVisible():
            window.raise_()
            return

    def start(choices: dict[str, bool]) -> None:
        """Guarda el ámbito y abre la ventana principal Qt."""
        state = dict(app.safety_health_state)
        previous = {
            chapter.code for chapter in study_chapters(app)
            if chapter.selected or "chapter_selection" not in state
        }
        selected = {code for code, included in choices.items() if included}
        if not state:
            state = serialize_state(
                _budget_safety_lines(app),
                Decimal(str(app.report_settings.safety_health_percentage)),
            )
        if selected != previous:
            state["lines"] = []
            state.pop("study_criteria", None)
        state["chapter_selection"] = restore_chapter_selection(choices)
        app.safety_health_state = state
        app._mark_dirty()
        try:
            window = SafetyHealthWindowQt(app)
        except ValueError as exc:
            QMessageBox.critical(app, "Seguridad y salud", str(exc))
            return
        app._project_windows.append(window)
        window.show()

    dialog = ChapterSelectionDialogQt(app, start)
    if dialog.exec() != dialog.DialogCode.Accepted:
        return


def before_project_save_qt(app) -> None:
    """Sincroniza el capítulo preventivo generado antes de guardar."""
    if not app.safety_health_state or app._has_external_tool_chapter("safety"):
        return
    from .qt_window import SafetyHealthWindowQt

    for window in app._project_windows:
        if isinstance(window, SafetyHealthWindowQt) and window.isVisible():
            window.commit_basic_state()


def _checked_work_summary(value: object) -> str:
    """Valida el resumen sin convertir datos incorrectos en texto de la memoria."""
    if not isinstance(value, str):
        raise ValueError("El resumen de la actuación debe ser texto.")
    return value.strip()


def study_work_summary(app: BudgetApp) -> str:
    """Usa la revision de Seguridad y Salud o, inicialmente, el resumen guardado de PPTP."""
    if "resumen_actuacion" in app.safety_health_state:
        return _checked_work_summary(app.safety_health_state["resumen_actuacion"])
    return _checked_work_summary(
        getattr(app, "pptp_state", {}).get("variables", {}).get("bullet_capitulos_obra", "")
    )


RELATIONSHIP_PATH = Path(__file__).resolve().parent / "datos" / "seguridad_salud_relaciones_bcca2023.csv"
_ESS_BUDGET_THRESHOLD = Decimal("450759.08")
_ESS_WORKDAY_THRESHOLD = Decimal("500")
_SPECIAL_WORK_RE = re.compile(
    r"\b(?:tunel(?:es)?|galeria(?:s)?|conduccion(?:es)?\s+subterranea(?:s)?|presa(?:s)?)\b",
)


@dataclass(frozen=True)
class StudyCriteria:
    """Valores estimados y criterios cumplidos para clasificar el estudio preventivo."""

    criterion_1_met: bool
    criterion_2_met: bool
    criterion_3_met: bool
    criterion_4_met: bool
    contract_budget: Decimal
    duration_days: int
    peak_workers: Decimal
    worker_days: Decimal

    @property
    def fulfilled(self) -> tuple[bool, bool, bool, bool]:
        """Devuelve los cuatro estados en el orden legal del artículo 4.1."""
        return (
            self.criterion_1_met, self.criterion_2_met,
            self.criterion_3_met, self.criterion_4_met,
        )

    @property
    def special_work(self) -> bool:
        """Indica si el presupuesto describe uno de los tipos especiales de obra."""
        return self.criterion_4_met

    @property
    def requires_full_study(self) -> bool:
        """Indica si se cumple al menos uno de los criterios del artículo 4.1."""
        return any(self.fulfilled)


def _normalized_text(value: str) -> str:
    """Normaliza tildes para detectar tipos de obra en sus descripciones."""
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").casefold()


def evaluate_study_criteria(app: BudgetApp) -> StudyCriteria:
    """Evalúa los criterios automáticos del artículo 4.1 con el presupuesto y plan actuales."""
    items = study_items(app)
    pem = sum((Decimal(str(item.import_total)) for item in app.budget_items), Decimal())
    contract_budget = budget_totals(float(pem), app.report_settings)["total"]

    params = PlanParams()
    activities = build_activities(app.budget_items, app._chapter_title)
    apply_saved_plan_state(params, activities, app.planning_state)
    included = {id(item) for item in items}
    # Conserva los identificadores originales para no reasignar las predecesoras guardadas.
    activities = [
        activity for activity, item in zip(activities, app.budget_items)
        if id(item) in included
    ]
    schedule(activities, params)
    hours_per_day = Decimal(str(params.hours_per_day))
    if not hours_per_day.is_finite() or hours_per_day <= 0:
        raise ValueError("La jornada de la planificación debe ser positiva para evaluar Seguridad y Salud.")

    worker_days = Decimal()
    daily_workers: dict[int, Decimal] = {}
    for activity in activities:
        labor_hours = Decimal(str(activity.quantity)) * Decimal(str(activity.rendimiento))
        if not labor_hours.is_finite():
            raise ValueError("El rendimiento de mano de obra contiene un valor no válido.")
        labor_hours = max(Decimal(), labor_hours)
        worker_days += labor_hours / hours_per_day
        active_days = activity.finish_offset - activity.start_offset
        if active_days > 0 and labor_hours:
            workers_per_day = max(
                Decimal(max(1, activity.teams)),
                labor_hours / Decimal(active_days) / hours_per_day,
            ).to_integral_value(rounding=ROUND_CEILING)
            for day in range(activity.start_offset, activity.finish_offset):
                daily_workers[day] = daily_workers.get(day, Decimal()) + workers_per_day

    duration_days = max((activity.finish_offset for activity in activities), default=0)
    peak_workers = max(daily_workers.values(), default=Decimal(1) if activities else Decimal())
    project_text = " ".join(
        [app._project_title()]
        + [
            " ".join((app._chapter_title(item.chapter), item.summary, item.text))
            for item in items
        ]
    )
    special_work = bool(_SPECIAL_WORK_RE.search(_normalized_text(project_text)))
    fulfilled = (
        contract_budget > _ESS_BUDGET_THRESHOLD,
        duration_days > 30 and peak_workers > 20,
        worker_days > _ESS_WORKDAY_THRESHOLD,
        special_work,
    )
    return StudyCriteria(
        *fulfilled, contract_budget, duration_days, peak_workers, worker_days,
    )


@dataclass
class SafetyLine:
    code: str
    unit: str
    summary: str
    price: Decimal
    weight: Decimal
    selected: bool = True
    quantity: Decimal | None = None


def _budget_safety_lines(app: BudgetApp) -> list[SafetyLine]:
    """Recupera las partidas preventivas existentes en proyectos sin revision guardada."""
    lines = []
    for item in app.budget_items:
        if item.source.startswith(SAFETY_BUDGET_SOURCE):
            price = Decimal(str(
                item.unit_price if item.direct_unit_price is None else item.direct_unit_price
            ))
            lines.append(SafetyLine(
                item.source.removeprefix(SAFETY_BUDGET_SOURCE),
                item.unit, item.summary, price, Decimal(str(item.measurement)) * price,
            ))
    return lines


def _decimal(value: object) -> Decimal:
    """Convierte un valor a decimal admitiendo la coma como separador."""
    return Decimal(str(value).replace(",", "."))


def _format_decimal(value: Decimal) -> str:
    """Formatea una magnitud con separadores decimales españoles."""
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_euros(value: Decimal) -> str:
    """Formatea un importe en euros."""
    return f"{_format_decimal(value)} €"


def _study_criteria_values(value: object) -> tuple[bool, bool, bool, bool] | None:
    """Valida la selección persistida de los cuatro criterios legales."""
    if value is None:
        return None
    keys = ("criterion_1_met", "criterion_2_met", "criterion_3_met", "criterion_4_met")
    if isinstance(value, dict):
        if any(type(value.get(key)) is not bool for key in keys):
            raise ValueError("La selección de criterios de Seguridad y Salud no es válida.")
        values = tuple(value[key] for key in keys)
    elif isinstance(value, (list, tuple)) and len(value) == 4 and all(
        type(item) is bool for item in value
    ):
        values = tuple(value)
    else:
        raise ValueError("La selección de criterios de Seguridad y Salud no es válida.")
    return values


def serialize_state(
    lines: list[SafetyLine], percentage: Decimal, *, centros_asistenciales: str = "",
    centros_asistenciales_modo: str = "texto",
    centros_asistenciales_texto: str | None = None,
    centros_asistenciales_datos: list[dict[str, str]] | None = None,
    study_criteria: tuple[bool, bool, bool, bool] | list[bool] | dict[str, bool] | None = None,
    external_conditions: dict[str, dict[str, str]] | None = None,
    resumen_actuacion: str | None = None,
    regulatory_review: dict | None = None,
    chapter_selection: dict[str, bool] | None = None,
    technical_review: dict | None = None,
    drawings: dict | None = None,
) -> dict:
    """Conserva la revisión, sus partidas y la información de centros asistenciales."""
    state = {
        "percentage": str(percentage),
        "centros_asistenciales": centros_asistenciales,
        "centros_asistenciales_modo": centros_asistenciales_modo,
        "centros_asistenciales_texto": (
            centros_asistenciales if centros_asistenciales_texto is None else centros_asistenciales_texto
        ),
        "centros_asistenciales_datos": [
            dict(row) for row in centros_asistenciales_datos or []
        ],
        "lines": [],
    }
    for line in lines:
        row = {
            "code": line.code, "unit": line.unit, "summary": line.summary,
            "price": str(line.price), "weight": str(line.weight), "selected": line.selected,
        }
        if line.quantity is not None:
            row["quantity"] = str(line.quantity)
        state["lines"].append(row)
    criteria = _study_criteria_values(study_criteria)
    if chapter_selection is not None:
        state["chapter_selection"] = restore_chapter_selection(chapter_selection)
    if regulatory_review is not None:
        state["regulatory_review"] = restore_review(regulatory_review)
    if technical_review is not None:
        state["technical_review"] = restore_technical_review(technical_review)
    if drawings is not None:
        state["drawings"] = restore_drawings(drawings)
    if resumen_actuacion is not None:
        state["resumen_actuacion"] = _checked_work_summary(resumen_actuacion)
    if external_conditions is not None:
        state["external_conditions"] = restore_external_conditions(external_conditions)
    if criteria is not None:
        state["study_criteria"] = {
            f"criterion_{index}_met": value for index, value in enumerate(criteria, start=1)
        }
    return state


def restore_state(data: object) -> tuple[list[SafetyLine], Decimal]:
    """Valida la revisión JSON sin consultar la matriz ni recalcular el presupuesto."""
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise ValueError("Los datos de Seguridad y Salud no contienen una revisión válida.")
    _study_criteria_values(data.get("study_criteria"))
    restore_external_conditions(data.get("external_conditions"))
    if "resumen_actuacion" in data:
        _checked_work_summary(data["resumen_actuacion"])
    restore_review(data.get("regulatory_review"))
    restore_technical_review(data.get("technical_review"))
    restore_drawings(data.get("drawings"))
    restore_chapter_selection(data.get("chapter_selection"))
    if ("centros_asistenciales" in data
            and not isinstance(data["centros_asistenciales"], str)):
        raise ValueError("Los centros asistenciales deben ser texto.")
    restore_centers(data)
    lines = []
    codes = set()
    try:
        percentage = _decimal(data["percentage"])
        if not percentage.is_finite() or not 0 <= percentage <= 100:
            raise ValueError("El porcentaje debe estar entre 0 y 100.")
        for row in data["lines"]:
            if not isinstance(row, dict):
                raise ValueError("Hay una partida de Seguridad y Salud no válida.")
            if any(not isinstance(row.get(key), str) for key in ("code", "unit", "summary")):
                raise ValueError("Los textos de Seguridad y Salud no son válidos.")
            if not row["code"] or row["code"] in codes or not isinstance(row.get("selected"), bool):
                raise ValueError("La selección de Seguridad y Salud no es válida.")
            price, weight = _decimal(row["price"]), _decimal(row["weight"])
            quantity = _decimal(row["quantity"]) if "quantity" in row else None
            if (not price.is_finite() or not weight.is_finite() or price <= 0 or weight < 0):
                raise ValueError("Los precios y pesos de Seguridad y Salud no son válidos.")
            if quantity is not None and (not quantity.is_finite() or quantity <= 0):
                raise ValueError("La cantidad de Seguridad y Salud no es válida.")
            lines.append(SafetyLine(
                row["code"], row["unit"], row["summary"], price, weight, row["selected"],
                quantity,
            ))
            codes.add(row["code"])
    except (KeyError, ArithmeticError, TypeError) as exc:
        raise ValueError("Los datos numéricos de Seguridad y Salud no son válidos.") from exc
    return lines, percentage


def proposed_lines(items: list[ProjectItem]) -> list[SafetyLine]:
    """Agrupa las partidas SS de la matriz que corresponden a las UO del presupuesto."""
    item_codes = {item.code for item in items if not item.source.startswith(SAFETY_BUDGET_SOURCE)}
    grouped: dict[str, SafetyLine] = {}
    try:
        with RELATIONSHIP_PATH.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                if row["uo_codigo"] not in item_codes:
                    continue
                code = row["ss_codigo"]
                line = grouped.get(code)
                if line is None:
                    grouped[code] = SafetyLine(
                        code, row["ss_unidad"], row["ss_resumen"], _decimal(row["ss_precio"]),
                        _decimal(row["peso_presupuesto_ss"]),
                    )
                else:
                    line.weight += _decimal(row["peso_presupuesto_ss"])
    except (OSError, KeyError, ValueError, ArithmeticError) as exc:
        raise ValueError(f"No se pudo leer la matriz de Seguridad y Salud: {exc}") from exc
    return sorted(grouped.values(), key=lambda line: line.code)


def synchronize_budget(
    app: BudgetApp, lines: list[SafetyLine], percentage: Decimal, *,
    centros_asistenciales: str = "",
    centros_asistenciales_modo: str = "texto",
    centros_asistenciales_texto: str | None = None,
    centros_asistenciales_datos: list[dict[str, str]] | None = None,
    study_criteria: tuple[bool, bool, bool, bool] | None = None,
    external_conditions: dict[str, dict[str, str]] | None = None,
    resumen_actuacion: str | None = None,
) -> None:
    """Sustituye exclusivamente las partidas SS generadas por la revisión aceptada."""
    from budget_core import MeasurementLine, ProjectItem
    conditions = restore_external_conditions(
        external_conditions if external_conditions is not None
        else getattr(app, "safety_health_state", {}).get("external_conditions")
    )
    work_summary = (
        study_work_summary(app) if resumen_actuacion is None
        else _checked_work_summary(resumen_actuacion)
    )
    saved_review = app.safety_health_state.get("regulatory_review")
    if saved_review is not None:
        saved_review = restore_review(saved_review)

    if not lines:
        raise ValueError("Selecciona al menos una partida de Seguridad y Salud.")
    percentage = _decimal(percentage)
    if not percentage.is_finite() or not Decimal() <= percentage < Decimal(100):
        raise ValueError("El porcentaje de Seguridad y Salud debe ser mayor o igual que 0 y menor que 100.")
    base = sum(
        (
            Decimal(str(item.import_total))
            for item in app.budget_items
            if not item.source.startswith(SAFETY_BUDGET_SOURCE)
        ),
        Decimal(),
    )
    if base <= 0:
        raise ValueError("El presupuesto previo debe tener un importe positivo.")
    selected = [line for line in lines if line.selected]
    if not selected:
        raise ValueError("Selecciona al menos una partida de Seguridad y Salud.")
    weighted = [line for line in selected if line.quantity is None]
    total_weight = sum((line.weight for line in weighted), Decimal())
    target = (
        base * percentage / (Decimal(100) - percentage)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if weighted and total_weight <= 0:
        raise ValueError("La propuesta no contiene pesos válidos.")
    indirect_rate = _decimal(app.report_settings.indirect_costs)
    if not indirect_rate.is_finite() or not Decimal() <= indirect_rate <= Decimal(100):
        raise ValueError("El porcentaje de costes indirectos no es válido.")

    def gross_unit_price(line: SafetyLine) -> Decimal:
        """Calcula el precio unitario con el recargo que se aplicará al capítulo."""
        indirect = (
            line.price * indirect_rate / 100
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return line.price + indirect

    fixed_total = sum(
        (
            line.quantity.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            * gross_unit_price(line)
            for line in selected if line.quantity is not None
        ),
        Decimal(),
    )
    if fixed_total > target:
        raise ValueError(
            "Las cantidades mínimas seleccionadas superan el presupuesto objetivo "
            f"de Seguridad y Salud ({target:.2f}). Ajusta la selección de partidas."
        )
    remaining = target - fixed_total
    if not weighted and remaining:
        raise ValueError(
            "Las cantidades fijas seleccionadas no permiten alcanzar el porcentaje "
            "indicado del PEM."
        )
    quantities = {}
    if weighted:
        for line in weighted:
            amount = remaining * line.weight / total_weight
            quantities[line.code] = (
                amount / gross_unit_price(line)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        weighted_total = sum(
            (quantities[line.code] * gross_unit_price(line) for line in weighted),
            Decimal(),
        )
        attempts_remaining = len(weighted) * 2
        while attempts_remaining:
            attempts_remaining -= 1
            difference = target - fixed_total - weighted_total
            if not difference:
                break
            best_adjustment = None
            for line in weighted:
                unit_price = gross_unit_price(line)
                adjustment = (difference / unit_price).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP,
                )
                quantity = max(Decimal(), quantities[line.code] + adjustment)
                adjusted_total = (
                    weighted_total + (quantity - quantities[line.code]) * unit_price
                )
                error = abs(target - fixed_total - adjusted_total)
                if error < abs(difference) and (
                    best_adjustment is None or error < best_adjustment[0]
                ):
                    best_adjustment = (error, line.code, quantity, adjusted_total)
            if best_adjustment is None:
                break
            quantities[best_adjustment[1]] = best_adjustment[2]
            weighted_total = best_adjustment[3]
            if best_adjustment[0] == 0:
                break
    previous_items = [
        item for item in app.budget_items if item.source.startswith(SAFETY_BUDGET_SOURCE)
    ]
    chapter = next(
        (item.chapter for item in previous_items if _chapter_number(item.chapter) is not None),
        None,
    )
    if chapter is None:
        used_chapters = set(app.chapter_order) | {
            item.chapter for item in app.budget_items
        } | set(app.chapter_titles)
        next_number = max(
            (_chapter_number(code) or 0 for code in used_chapters), default=0,
        ) + 1
        chapter = f"CAP.{next_number:02d}#"
    previous_chapters = {item.chapter for item in previous_items}
    remaining_items = [
        item for item in app.budget_items if not item.source.startswith(SAFETY_BUDGET_SOURCE)
    ]
    app.budget_items[:] = remaining_items
    for previous_chapter in previous_chapters - {chapter}:
        if not any(item.chapter == previous_chapter for item in remaining_items):
            app.chapter_order[:] = [
                code for code in app.chapter_order if code != previous_chapter
            ]
            app.chapter_titles.pop(previous_chapter, None)
    for line in selected:
        quantity = line.quantity
        if quantity is None:
            quantity = quantities[line.code]
        item = ProjectItem(
            f"SS-{line.code}", chapter, line.unit, line.summary.upper(),
            unit_price=float(line.price), source=SAFETY_BUDGET_SOURCE + line.code,
            text=(
                "Unidad vinculada a partidas del presupuesto; cantidad mínima propuesta. "
                "Revisar por técnico competente."
                if line.quantity is not None else
                "Partida propuesta por la matriz de Seguridad y Salud. Revisar por técnico competente."
            ),
        )
        item.measurement_lines = [MeasurementLine(
            comment=(
                "Propuesta vinculada al presupuesto (1 unidad mínima)"
                if line.quantity is not None else
                f"Propuesta SS ({percentage.normalize()} % del presupuesto previo)"
            ),
            units=float(quantity),
        )]
        item.recalculate_measurement()
        item.set_indirect_costs(app.report_settings.indirect_costs, float(line.price))
        app.budget_items.append(item)
    app._ensure_chapter(chapter)
    app.chapter_titles[chapter] = "SEGURIDAD Y SALUD"
    app.safety_health_state = serialize_state(
        lines, percentage, centros_asistenciales=centros_asistenciales,
        centros_asistenciales_modo=centros_asistenciales_modo,
        centros_asistenciales_texto=centros_asistenciales_texto,
        centros_asistenciales_datos=centros_asistenciales_datos,
        study_criteria=study_criteria,
        external_conditions=conditions or None,
        resumen_actuacion=work_summary,
        regulatory_review=saved_review,
        chapter_selection=app.safety_health_state.get("chapter_selection"),
        technical_review=app.safety_health_state.get("technical_review"),
        drawings=app.safety_health_state.get("drawings"),
    )
    app.refresh_budget_tree()
    app._mark_dirty()


def _chapter_number(code: str) -> int | None:
    """Extrae el numero de un codigo de capitulo para mantener la secuencia CAP."""
    normalized = code.rstrip("#").replace(" ", "").strip()
    match = re.fullmatch(r"(?:CAP\.?)?(\d+)[A-Z0-9]*", normalized, re.IGNORECASE)
    return int(match.group(1)) if match else None


def write_study_report(app: BudgetApp, target: Path) -> tuple[str, ...]:
    """Genera el estudio en el formato configurado y devuelve sus avisos de revision."""
    from .document import write_study_report as write

    return write(app, target)


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
