"""Propuesta revisable de partidas de Seguridad y Salud basada en la matriz BCCA."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from pathlib import Path, PurePosixPath
import re
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING
import unicodedata

from budget_reports import budget_totals
from localization import filedialog, localize_widget_tree, messagebox, translate
from tools._shared.scheduling import PlanParams, apply_saved_plan_state, build_activities, schedule
from .care_centers import CareCentersEditor, restore_centers
from .external_conditions import ExternalConditionsEditor, restore_external_conditions

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


def open_tool(app: BudgetApp) -> None:
    """Abre o recupera la revision de seguridad y salud del proyecto."""
    if not app.budget_items:
        messagebox.showinfo(
            TOOL_LABEL, "Carga o crea un presupuesto antes de analizar Seguridad y Salud.",
        )
        return
    if app._safety_health_window is not None and app._safety_health_window.winfo_exists():
        app._safety_health_window.lift()
        return
    app._safety_health_window = SafetyHealthWindow(app.root, app)


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
    )


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


SAFETY_BUDGET_SOURCE = "SS:"
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
    items = app.budget_items
    pem = sum((Decimal(str(item.import_total)) for item in items), Decimal())
    contract_budget = budget_totals(float(pem), app.report_settings)["total"]

    params = PlanParams()
    activities = build_activities(items, app._chapter_title)
    apply_saved_plan_state(params, activities, app.planning_state)
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
        "lines": [
            {"code": line.code, "unit": line.unit, "summary": line.summary,
             "price": str(line.price), "weight": str(line.weight), "selected": line.selected}
            for line in lines
        ],
    }
    criteria = _study_criteria_values(study_criteria)
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
            if (not price.is_finite() or not weight.is_finite() or price <= 0 or weight < 0):
                raise ValueError("Los precios y pesos de Seguridad y Salud no son válidos.")
            lines.append(SafetyLine(
                row["code"], row["unit"], row["summary"], price, weight, row["selected"],
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
    from bc3_gui import MeasurementLine, ProjectItem
    conditions = restore_external_conditions(
        external_conditions if external_conditions is not None
        else getattr(app, "safety_health_state", {}).get("external_conditions")
    )
    work_summary = (
        study_work_summary(app) if resumen_actuacion is None
        else _checked_work_summary(resumen_actuacion)
    )

    if not lines:
        raise ValueError("Selecciona al menos una partida de Seguridad y Salud.")
    base = sum(
        (Decimal(str(item.measurement)) * Decimal(str(item.unit_price))
         for item in app.budget_items if not item.source.startswith(SAFETY_BUDGET_SOURCE)),
        Decimal(),
    )
    if base <= 0:
        raise ValueError("El presupuesto previo debe tener un importe positivo.")
    selected = [line for line in lines if line.selected]
    if not selected:
        raise ValueError("Selecciona al menos una partida de Seguridad y Salud.")
    total_weight = sum((line.weight for line in selected), Decimal())
    target = (base * percentage / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if total_weight <= 0:
        raise ValueError("La propuesta no contiene pesos válidos.")
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
        amount = target * line.weight / total_weight
        quantity = (amount / line.price).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        item = ProjectItem(
            f"SS-{line.code}", chapter, line.unit, line.summary.upper(),
            unit_price=float(line.price), source=SAFETY_BUDGET_SOURCE + line.code,
            text="Partida propuesta por la matriz de Seguridad y Salud. Revisar por técnico competente.",
        )
        item.measurement_lines = [MeasurementLine(
            comment=f"Propuesta SS ({percentage.normalize()} % del presupuesto previo)",
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
    from .study_report import write_study_report as write

    return write(app, target)


class SafetyHealthWindow(tk.Toplevel):
    """Muestra criterios del estudio, información básica y acceso a la propuesta de unidades."""
    def __init__(self, master: tk.Misc, app: BudgetApp):
        """Calcula estimaciones y permite guardar la selección de los criterios legales."""
        super().__init__(master)
        self.app = app
        self.title("Seguridad y salud")
        self.geometry("1100x760")
        self.minsize(950, 650)
        self._proposal_window: SafetyProposalWindow | None = None
        self._conditions_window: tk.Toplevel | None = None
        try:
            if app.safety_health_state:
                self.lines, percentage = restore_state(app.safety_health_state)
            else:
                generated = [
                    item for item in app.budget_items
                    if item.source.startswith(SAFETY_BUDGET_SOURCE)
                ]
                self.lines = []
                for item in generated:
                    price = Decimal(str(
                        item.unit_price if item.direct_unit_price is None else item.direct_unit_price
                    ))
                    self.lines.append(SafetyLine(
                        item.source.removeprefix(SAFETY_BUDGET_SOURCE),
                        item.unit, item.summary, price,
                        Decimal(str(item.measurement)) * price,
                    ))
                if not generated:
                    self.lines = proposed_lines(app.budget_items)
                percentage = Decimal(str(app.report_settings.safety_health_percentage))
            self.study_criteria = evaluate_study_criteria(app)
            saved_criteria = _study_criteria_values(
                app.safety_health_state.get("study_criteria")
                if isinstance(app.safety_health_state, dict) else None
            )
            restore_external_conditions(app.safety_health_state.get("external_conditions"))
            work_summary = study_work_summary(app)
        except ValueError as exc:
            messagebox.showerror("Seguridad y salud", str(exc), parent=self)
            self.after_idle(self.destroy)
            return
        self._stored_percentage = percentage
        self.percentage = tk.StringVar(value=str(percentage))
        self.study_criteria_selection = saved_criteria or self.study_criteria.fulfilled
        self._build_study_criteria()
        basics = ttk.LabelFrame(
            self, text="Información básica del Estudio de Seguridad y Salud", padding=8,
        )
        basics.pack(fill="x", padx=12, pady=(8, 4))
        ttk.Label(basics, text="Resumen de la actuación").pack(anchor="w")
        summary_frame = ttk.Frame(basics)
        summary_frame.pack(fill="x")
        self.summary_text = tk.Text(summary_frame, height=3, wrap="word", undo=True)
        summary_scroll = ttk.Scrollbar(summary_frame, command=self.summary_text.yview)
        self.summary_text.configure(yscrollcommand=summary_scroll.set)
        summary_scroll.pack(side="right", fill="y")
        self.summary_text.insert("1.0", work_summary)
        self.summary_text.pack(fill="x")
        self.summary_text.edit_modified(False)
        self.summary_text.bind("<<Modified>>", self._summary_changed)
        centers_label = tk.Label(
            basics, text="Centros Asistenciales", foreground="#0563C1",
            cursor="hand2", font=("TkDefaultFont", 9, "underline"),
        )
        centers_label.pack(anchor="w")
        centers_label.bind("<Button-1>", self.open_care_centers_registry)
        centers_label.bind("<Return>", self.open_care_centers_registry)
        centers_label.configure(takefocus=True)
        from bc3_gui import ToolTip

        ToolTip(self.summary_text, translate(
            "Se toma inicialmente del resumen guardado en PPTP; puedes editarlo aquí."
        ))
        ToolTip(
            centers_label,
            translate(
                "Rellena este apartado con la información del registro SICESS de la Junta de Andalucía "
                "(al menos Hospitales Generales y Centros de Salud)."
            ),
        )
        self.centers_editor = CareCentersEditor(basics, app.safety_health_state)
        self.centers_editor.pack(fill="x", pady=(4, 0))
        self.centers_text = self.centers_editor.text
        actions = ttk.Frame(self, padding=12)
        actions.pack(fill="x")
        ttk.Button(
            actions, text="Condicionantes externos...", command=self.open_external_conditions,
        ).pack(side="left")
        ttk.Button(
            actions, text="Revisar propuesta de unidades de Seguridad y Salud...",
            command=self.open_proposal,
        ).pack(side="right")
        localize_widget_tree(self)

    def _summary_changed(self, _event=None) -> None:
        """Conserva el resumen editado sin recalcular el capitulo preventivo."""
        if not self.summary_text.edit_modified():
            return
        self.summary_text.edit_modified(False)
        summary = self.summary_text.get("1.0", "end-1c").strip()
        state = self.app.safety_health_state
        if state.get("resumen_actuacion") == summary:
            return
        state = dict(state) or serialize_state(self.lines, self._stored_percentage)
        state["resumen_actuacion"] = summary
        self.app.safety_health_state = state
        self.app._mark_dirty()

    def _build_study_criteria(self) -> None:
        """Muestra los criterios legales evaluados con los datos del proyecto."""
        assessment = self.study_criteria
        criteria = (
            (
                assessment.fulfilled[0],
                translate(
                    "Presupuesto de ejecución por contrata con IVA > 450.759,08 €: {amount}."
                ).format(amount=_format_euros(assessment.contract_budget)),
            ),
            (
                assessment.fulfilled[1],
                translate(
                    "Duración > 30 días laborables y más de 20 trabajadores simultáneos: "
                    "{days} días; máximo estimado {workers} trabajadores/día."
                ).format(
                    days=assessment.duration_days,
                    workers=_format_decimal(assessment.peak_workers),
                ),
            ),
            (
                assessment.fulfilled[2],
                translate("Volumen estimado de mano de obra > 500 jornadas: {days} jornadas.").format(
                    days=_format_decimal(assessment.worker_days),
                ),
            ),
            (
                assessment.fulfilled[3],
                translate(
                    "Obra de túneles, galerías, conducciones subterráneas o presas: {detected}."
                ).format(detected=translate("Sí" if assessment.special_work else "No")),
            ),
        )
        criteria_frame = ttk.LabelFrame(
            self, text=translate("Criterios del artículo 4.1 de la LPRL"), padding=8,
        )
        criteria_frame.pack(fill="x", padx=12, pady=(8, 4))
        self.study_criteria_vars: list[tk.BooleanVar] = []
        self.study_criteria_checkboxes: list[ttk.Checkbutton] = []
        for index, (_, text) in enumerate(criteria):
            fulfilled = self.study_criteria_selection[index]
            variable = tk.BooleanVar(self, value=fulfilled)
            self.study_criteria_vars.append(variable)
            row = ttk.Frame(criteria_frame)
            row.pack(fill="x", anchor="w", pady=1)
            checkbox = ttk.Checkbutton(
                row, variable=variable, command=self._criteria_changed,
            )
            self.study_criteria_checkboxes.append(checkbox)
            checkbox.pack(
                side="left", anchor="n",
            )
            label = ttk.Label(row, text=text, wraplength=1040, justify="left", cursor="hand2")
            label.pack(
                side="left", fill="x", expand=True,
            )
            label.bind(
                "<Button-1>",
                lambda event, value=variable: self._toggle_study_criterion(value),
            )

        self.study_result_var = tk.StringVar(self)
        ttk.Label(criteria_frame, textvariable=self.study_result_var, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", pady=(5, 0),
        )
        self._update_study_result()
        ttk.Label(
            criteria_frame,
            text=translate(
                "Estimación automática basada en los rendimientos de mano de obra del presupuesto "
                "y la planificación guardada. La detección del tipo de obra se basa en sus descripciones. "
                "Comprueba especialmente la concurrencia real de trabajadores. La selección de casillas es editable."
            ),
            wraplength=1040, justify="left",
        ).pack(anchor="w", pady=(2, 0))

    @property
    def selected_study_criteria(self) -> tuple[bool, bool, bool, bool]:
        """Devuelve las cuatro selecciones editables en su orden legal."""
        return tuple(variable.get() for variable in self.study_criteria_vars)

    def _toggle_study_criterion(self, variable: tk.BooleanVar) -> str:
        """Alterna el criterio al pulsar su descripción."""
        variable.set(not variable.get())
        self._criteria_changed()
        return "break"

    def _update_study_result(self) -> None:
        """Actualiza el tipo de estudio derivado de las selecciones del usuario."""
        self.study_result_var.set(translate(
            "Según los criterios marcados, procede un Estudio de Seguridad y Salud."
            if any(self.selected_study_criteria)
            else "Según los criterios marcados, procede un Estudio Básico de Seguridad y Salud."
        ))

    def _criteria_changed(self) -> None:
        """Persiste las selecciones editadas para usarlas en el texto preventivo posterior."""
        self._update_study_result()
        state = self.app.safety_health_state
        if not state:
            state = serialize_state(self.lines, self._stored_percentage)
        else:
            state = dict(state)
        state["study_criteria"] = {
            f"criterion_{index}_met": value
            for index, value in enumerate(self.selected_study_criteria, start=1)
        }
        self.app.safety_health_state = state
        self.app._mark_dirty()

    def open_proposal(self) -> SafetyProposalWindow:
        """Abre la ventana separada de propuesta y mediciones de unidades preventivas."""
        if self._proposal_window is not None and self._proposal_window.winfo_exists():
            self._proposal_window.lift()
            return self._proposal_window
        self._proposal_window = SafetyProposalWindow(self)
        return self._proposal_window

    def open_external_conditions(self) -> tk.Toplevel:
        """Abre la revisión de condicionantes sin modificar el presupuesto."""
        if self._conditions_window is not None and self._conditions_window.winfo_exists():
            self._conditions_window.lift()
            return self._conditions_window
        window = tk.Toplevel(self)
        self._conditions_window = window
        window.title("Condicionantes externos")
        window.transient(self)
        editor = ExternalConditionsEditor(
            window, self.app.safety_health_state.get("external_conditions"),
        )
        editor.pack(fill="both", expand=True)

        def save_conditions() -> None:
            """Guarda la revisión independiente de las partidas preventivas."""
            try:
                conditions = editor.collect_state()
            except ValueError as exc:
                messagebox.showerror("Seguridad y salud", str(exc), parent=window)
                return
            state = dict(self.app.safety_health_state) or serialize_state(
                self.lines, self._stored_percentage,
            )
            state["external_conditions"] = conditions
            self.app.safety_health_state = state
            self.app._mark_dirty()
            window.destroy()

        ttk.Button(
            window, text="Guardar condicionantes", command=save_conditions,
        ).pack(anchor="e", padx=12, pady=(0, 12))
        localize_widget_tree(window)
        return window

    def open_care_centers_registry(self, event=None) -> None:
        """Abre el registro oficial andaluz de centros asistenciales."""
        self.app._open_web_page(
            CARE_CENTERS_URL,
            translate("Centros Asistenciales"),
            translate("Registro SICESS abierto en el navegador."),
        )


class SafetyProposalWindow(tk.Toplevel):
    """Permite revisar unidades de Seguridad y Salud antes de incorporarlas al presupuesto."""

    def __init__(self, owner: SafetyHealthWindow):
        """Crea una ventana secundaria con la propuesta de unidades."""
        super().__init__(owner)
        self.owner = owner
        self.app = owner.app
        self.lines = owner.lines
        self.percentage = owner.percentage
        self.title("Propuesta de unidades de Seguridad y Salud")
        self.geometry("1100x700")
        self.minsize(950, 600)
        self.library_editor: LibraryEditor | None = None
        header = ttk.Frame(self, padding=(12, 12, 12, 4))
        header.pack(fill="x")
        ttk.Label(header, text="Seguridad y salud — propuesta revisable",
                  style="Total.TLabel").pack(side="left")
        ttk.Button(
            header, text="Editar biblioteca de fichas...",
            command=self.open_library_editor,
        ).pack(side="right")
        ttk.Label(
            self,
            text=("La propuesta se basa en la matriz BCCA por riesgos. El porcentaje es orientativo; "
                  "revisa la selección y las mediciones del capítulo creado antes de usarlo."),
            wraplength=1000,
        ).pack(fill="x", padx=12)
        controls = ttk.Frame(self, padding=12)
        controls.pack(fill="x")
        ttk.Label(controls, text="Porcentaje orientativo del presupuesto previo:").pack(side="left")
        ttk.Entry(controls, textvariable=self.percentage, width=8).pack(side="left", padx=6)
        ttk.Label(controls, text="%").pack(side="left")
        self.tree = ttk.Treeview(self, columns=("selected", "code", "summary", "unit", "price", "weight"),
                                 show="headings", selectmode="browse")
        for column, label, width in (
            ("selected", "Incluir", 65), ("code", "Código", 115), ("summary", "Partida de Seguridad y Salud", 500),
            ("unit", "Ud.", 55), ("price", "Precio", 90), ("weight", "Peso", 70),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, stretch=column == "summary")
        self.tree.pack(fill="both", expand=True, padx=12, pady=4)
        for line in self.lines:
            self.tree.insert("", "end", iid=line.code, values=(
                "X" if line.selected else "", line.code, line.summary, line.unit,
                f"{line.price:.2f}", f"{line.weight:.2f}",
            ))
        self.tree.bind("<ButtonRelease-1>", self._toggle)
        self.tree.bind("<space>", self._toggle)
        buttons = ttk.Frame(self, padding=12)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cerrar", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Aceptar y crear capítulo", command=self.accept,
                   style="Accent.TButton").pack(side="right", padx=8)
        ttk.Button(buttons, text="Generar estudio completo", command=self.generate_report).pack(
            side="left"
        )
        localize_widget_tree(self)

    def open_library_editor(self) -> None:
        """Abre el editor de fichas de Seguridad y Salud."""
        from tools._library_editor import LibraryEditor, library_members

        item_codes = {item.code.upper() for item in self.app.budget_items}
        resource_codes = {
            resource.code.upper()
            for item in self.app.budget_items
            for resource, _, _ in self.app._report_components(item)
            if resource.ctype in {"2", "3"}
        }
        used_members = set()
        for member in library_members(LIBRARY_PATH):
            path = PurePosixPath(member)
            if path.suffix.casefold() != ".md":
                continue
            if member.startswith("UNI/"):
                code = path.stem.removeprefix("FICHA_ESS_").split("-", 1)[0].upper()
                if not code[:2].isdigit() or not code[2:].isalpha():
                    continue
                if any(item_code.startswith(code) for item_code in item_codes):
                    used_members.add(member)
            elif member.startswith("RECURSOS/") and path.stem.split("-", 1)[0].upper() in resource_codes:
                used_members.add(member)
        if self.library_editor is not None and self.library_editor.winfo_exists():
            self.library_editor.highlight_used(used_members)
            self.library_editor.lift()
            return
        self.library_editor = LibraryEditor(
            self, LIBRARY_PATH, "Biblioteca de fichas de Seguridad y Salud",
            used_members=used_members,
        )

    def _toggle(self, event=None):
        """Alterna la inclusión de la partida seleccionada y actualiza su marca."""
        row = self.tree.identify_row(event.y) if event is not None else (self.tree.selection() or ("",))[0]
        if row and (event is None or self.tree.identify_column(event.x) == "#1"):
            line = next(line for line in self.lines if line.code == row)
            line.selected = not line.selected
            self.tree.set(row, "selected", "X" if line.selected else "")
        return "break"

    def accept(self) -> None:
        """Valida la revisión e incorpora el capítulo de Seguridad y Salud."""
        self._create_chapter()

    def _create_chapter(self) -> bool:
        """Incorpora la selección actual y permite continuar revisando o emitir el informe."""
        if not self.app._tool_chapter_available("safety"):
            return False
        try:
            percentage = _decimal(self.percentage.get())
            if not Decimal() <= percentage <= 100:
                raise ValueError("El porcentaje debe estar entre 0 y 100.")
            synchronize_budget(
                self.app, self.lines, percentage,
                **self.owner.centers_editor.collect_state(),
                study_criteria=self.owner.selected_study_criteria,
                resumen_actuacion=self.owner.summary_text.get("1.0", "end-1c").strip(),
            )
        except ValueError as exc:
            messagebox.showerror("Seguridad y salud", str(exc), parent=self)
            return False
        self.owner._stored_percentage = percentage
        self.app._set_status("Capítulo Seguridad y Salud creado; revisa sus mediciones antes de guardar.")
        return True

    def generate_report(self) -> None:
        """Crea el capítulo integrado y genera el estudio completo desde las plantillas."""
        if not self._create_chapter():
            return
        output_format = self.app.report_settings.output_format.lower()
        extension = ".odt" if output_format == "odt" else ".pdf"
        file_type = ("Documento ODT", "*.odt") if extension == ".odt" else ("Documento PDF", "*.pdf")
        target = filedialog.asksaveasfilename(
            parent=self, title="Generar Estudio de Seguridad y Salud",
            initialfile=f"Estudio de Seguridad y Salud{extension}",
            defaultextension=extension, filetypes=[file_type],
        )
        if not target:
            return
        try:
            report_path = Path(target).with_suffix(extension)
            write_study_report(self.app, report_path)
        except (ImportError, OSError, ValueError) as exc:
            messagebox.showerror("No se pudo generar el informe", str(exc), parent=self)
            return
        self.app._set_status(f"Estudio de Seguridad y Salud guardado en {report_path}")
        messagebox.showinfo(
            "Seguridad y salud",
            f"Estudio completo generado:\n{report_path}",
            parent=self,
        )
