"""Plan económico mensual y certificaciones editables por partida.

Informes ofrece los modelos PDF «Resumen certificaciones» y «Certificaciones
con mediciones» para el mes seleccionado. Las cantidades se deducen del importe
mensual y el precio unitario; no se reutilizan las líneas del presupuesto.
Los porcentajes, la baja y las firmas proceden de Archivo > Configuración.
"""
from __future__ import annotations

import calendar
import html
import math
import os
import tempfile
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tkinter import ttk

from bc3_gui import APPLICATION_NAME, ToolTip, fmt_eur, format_chapter_code, parse_float
from localization import filedialog, localize_widget_tree, messagebox, translate
from planning import PlanParams, apply_saved_plan_state, build_activities, schedule
from budget_reports import CERTIFICATION_REPORT_NAMES, ReportSettings, write_certification_pdf
from odt_reports import write_certification_odt


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
        if not activity.start_date or not activity.end_date:
            plan.append(values)
            continue
        total_days = max(
            1, _working_days(activity.start_date, activity.end_date, params.holiday_dates)
        )
        assigned = 0.0
        covered_indexes = [
            index
            for index, month in enumerate(months)
            if activity.start_date <= month.end and activity.end_date >= month.start
        ]
        for index in covered_indexes:
            month = months[index]
            overlap_start = max(activity.start_date, month.start)
            overlap_end = min(activity.end_date, month.end)
            amount = activity.amount * _working_days(
                overlap_start, overlap_end, params.holiday_dates
            ) / total_days
            values[index] = amount
            assigned += amount
        if covered_indexes:
            values[covered_indexes[-1]] += activity.amount - assigned
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
            f'<td class="num">{html.escape(fmt_eur(planned))}</td>'
            f'<td class="num">{html.escape(fmt_eur(certified))}</td>'
            f'<td class="num">{html.escape(fmt_eur(difference))}</td>'
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
<tfoot><tr><td colspan="3">TOTAL</td><td class="num">{fmt_eur(total_plan)}</td>
<td class="num">{fmt_eur(total_certified)}</td>
<td class="num">{fmt_eur(total_certified - total_plan)}</td><td></td></tr></tfoot>
</table></body></html>"""


class CertificationWindow(tk.Toplevel):
    """Muestra el plan económico y registra sus certificaciones mensuales."""

    def __init__(
        self,
        master: tk.Misc,
        items,
        chapter_title,
        title: str,
        planning_state: dict | None = None,
        on_save=None,
        on_dirty_change=None,
        report_settings=None,
    ):
        """Crea la gestión mensual de certificaciones a partir del presupuesto y su planificación."""
        super().__init__(master)
        self.title(APPLICATION_NAME)
        self.geometry("1500x760")
        self.minsize(1000, 600)
        self.state("zoomed")
        self.items = items
        self.project_title = title
        self.chapter_title = chapter_title
        self._report_settings = report_settings or ReportSettings
        self._on_save = on_save
        self._on_dirty_change = on_dirty_change
        self._is_dirty = False
        self.params = PlanParams()
        self.activities = build_activities(items, chapter_title)
        try:
            apply_saved_plan_state(self.params, self.activities, planning_state)
        except ValueError as exc:
            messagebox.showwarning(
                "Planificación guardada no válida",
                f"No se ha podido aplicar la planificación guardada:\n{exc}",
                parent=self,
            )
        self.months = economic_months(self.params.start)
        self.plan = planned_monthly_amounts(self.activities, self.params, self.months)
        self.selected_plan_month: int | None = None
        self.selected_certification_month: int | None = None
        self._editor: ttk.Entry | None = None
        self._selected_cell: tuple[int, int] | None = None
        self._deviation_labels: list[tk.Label] = []
        self._load_saved_plan()
        self._build_ui()
        self._refresh_tree()
        localize_widget_tree(self)

    def _load_saved_plan(self) -> None:
        """Sustituye las previsiones calculadas por importes mensuales guardados por partida."""
        for row, item in enumerate(self.items):
            if not item.planned_amounts:
                continue
            for month_index, month in enumerate(self.months):
                if month.key in item.planned_amounts:
                    self.plan[row][month_index] = item.planned_amounts[month.key]

    def _build_ui(self) -> None:
        """Construye las tablas mensuales, informes, desviaciones y justificaciones."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            toolbar,
            text="Copiar plan seleccionado a su certificación",
            command=self.copy_selected_plan,
            style="Primary.TButton",
        ).pack(side="left")
        ttk.Button(
            toolbar,
            text="Informe de desviaciones",
            command=self.generate_certification_report,
            style="Accent.TButton",
        ).pack(side="left", padx=(8, 0))
        reports_button = ttk.Menubutton(toolbar, text="Informes", style="Toolbar.TMenubutton")
        reports_menu = tk.Menu(reports_button, tearoff=False)
        for name in CERTIFICATION_REPORT_NAMES:
            reports_menu.add_command(
                label=name, command=lambda report=name: self.generate_template_report(report)
            )
        reports_button["menu"] = reports_menu
        reports_button.pack(side="left", padx=(8, 0))
        self.save_button = tk.Button(toolbar, text="💾", width=3, command=self.save_changes)
        self._save_button_background = self.save_button.cget("background")
        self._save_button_foreground = self.save_button.cget("foreground")
        self.save_button.pack(side="right")
        ToolTip(self.save_button, "Guardar los cambios del plan económico y las certificaciones.")
        self.selection_label = ttk.Label(
            toolbar, text="Selecciona una cabecera «Plan» para copiar esa columna.", style="Muted.TLabel"
        )
        self.selection_label.pack(side="left", padx=(12, 0))

        container = ttk.Frame(self, padding=(10, 0))
        container.grid(row=1, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        fixed_columns = ("capitulo", "codigo", "actividad", "importe")
        monthly_columns = tuple(
            column
            for index in range(len(self.months))
            for column in (f"plan_{index}", f"cert_{index}")
        )
        self.tree = ttk.Treeview(
            container,
            columns=fixed_columns + monthly_columns,
            show="headings",
            style="Certification.Treeview",
        )
        ttk.Style(self).configure("Certification.Treeview", rowheight=36)
        for column, heading, width in (
            ("capitulo", "Capítulo", 85),
            ("codigo", "Código", 100),
            ("actividad", "Actividad", 280),
            ("importe", "Importe", 100),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, anchor="w" if column == "actividad" else "e")
        for index, month in enumerate(self.months):
            plan_column, cert_column = f"plan_{index}", f"cert_{index}"
            self.tree.heading(
                plan_column,
                text=f"Plan {month.label}",
                command=lambda selected=index: self.select_plan_month(selected),
            )
            self.tree.heading(
                cert_column,
                text=f"Certificación {index + 1}",
                command=lambda selected=index: self.select_certification_month(selected),
            )
            self.tree.column(plan_column, width=105, anchor="e", stretch=False)
            self.tree.column(cert_column, width=120, anchor="e", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        horizontal = ttk.Scrollbar(container, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        vertical = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        self.tree.configure(
            xscrollcommand=lambda first, last: self._on_tree_scroll(horizontal.set, first, last),
            yscrollcommand=lambda first, last: self._on_tree_scroll(vertical.set, first, last),
        )
        self.tree.bind("<Double-1>", self.edit_certification)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_detail)
        self.tree.bind("<Configure>", lambda _event: self.after_idle(self._position_deviation_labels))
        self.tree.bind("<MouseWheel>", lambda _event: self.after_idle(self._position_deviation_labels))

        detail = ttk.LabelFrame(self, text="Desviación y justificación", padding=8)
        detail.grid(row=2, column=0, sticky="ew", padx=10, pady=(8, 10))
        detail.columnconfigure(1, weight=1)
        self.deviation_label = ttk.Label(detail, text="Selecciona una celda de certificación.")
        self.deviation_label.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(detail, text="Justificación:").grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.justification = tk.Text(detail, height=3, wrap="word")
        self.justification.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        self.justification.bind("<FocusOut>", self.save_justification)

    def save_changes(self) -> None:
        """Actualiza la justificación activa y guarda las certificaciones en el proyecto."""
        self.save_justification()
        if self._on_save is not None and self._on_save():
            self._set_dirty(False)

    def _set_dirty(self, value: bool) -> None:
        """Actualiza el botón de guardado y comunica los cambios al proyecto."""
        self._is_dirty = value
        self.save_button.configure(
            text="💾",
            background="#000000" if value else self._save_button_background,
            foreground="#FFFFFF" if value else self._save_button_foreground,
            activebackground="#000000" if value else self._save_button_background,
            activeforeground="#FFFFFF" if value else self._save_button_foreground,
        )
        if self._on_dirty_change is not None:
            self._on_dirty_change(value)

    def _refresh_tree(self) -> None:
        """Reconstruye la tabla mensual y programa la visualización de desviaciones."""
        self._close_editor()
        self._clear_deviation_labels()
        for row in self.tree.get_children():
            self.tree.delete(row)
        for row, (item, activity) in enumerate(zip(self.items, self.activities)):
            values = [
                format_chapter_code(item.chapter),
                item.code,
                item.summary,
                fmt_eur(activity.amount),
            ]
            for month_index, month in enumerate(self.months):
                planned = self.plan[row][month_index]
                certified = item.certifications.get(month.key)
                values.extend(
                    [
                        fmt_eur(planned) if planned else "",
                        fmt_eur(certified) if certified is not None else "",
                    ]
                )
            self.tree.insert("", "end", iid=str(row), values=values)
        self.after_idle(self._create_deviation_labels)

    def _on_tree_scroll(self, setter, first: str, last: str) -> None:
        """Sincroniza la barra de desplazamiento y reposiciona las desviaciones."""
        setter(first, last)
        self.after_idle(self._position_deviation_labels)

    def _clear_deviation_labels(self) -> None:
        """Destruye las etiquetas superpuestas de desviación y vacía su registro."""
        for label in self._deviation_labels:
            label.destroy()
        self._deviation_labels.clear()

    def _create_deviation_labels(self) -> None:
        """Crea indicadores de desviación por certificación con color y acciones de edición."""
        self._clear_deviation_labels()
        for row, item in enumerate(self.items):
            for month_index, month in enumerate(self.months):
                certified = item.certifications.get(month.key)
                if certified is None:
                    continue
                planned = item.certification_plans.get(
                    month.key, self.plan[row][month_index]
                )
                difference = certified - planned
                color = "#B91C1C" if difference > 0.005 else "#15803D"
                sign = "+" if difference > 0.005 else ""
                label = tk.Label(
                    self.tree,
                    text=f"{sign}{fmt_eur(difference)}",
                    foreground=color,
                    background="#FFFFFF",
                    font=("Segoe UI", 7),
                    anchor="e",
                    padx=0,
                    pady=0,
                )
                label.bind(
                    "<Double-1>",
                    lambda _event, selected_row=row, selected_month=month_index: self._open_certification_editor(
                        selected_row, selected_month
                    ),
                )
                label.bind(
                    "<Button-1>",
                    lambda _event, selected_row=row, selected_month=month_index: self._select_certification(
                        selected_row, selected_month
                    ),
                )
                self._deviation_labels.append(label)
        self._position_deviation_labels()

    def _position_deviation_labels(self) -> None:
        """Sitúa las desviaciones en sus celdas visibles después de desplazar la tabla."""
        for label in self._deviation_labels:
            label.place_forget()
        label_index = 0
        for row, item in enumerate(self.items):
            for month_index, month in enumerate(self.months):
                if item.certifications.get(month.key) is None:
                    continue
                label = self._deviation_labels[label_index]
                label_index += 1
                column = f"#{6 + month_index * 2}"
                bbox = self.tree.bbox(str(row), column)
                if not bbox:
                    continue
                x, y, width, height = bbox
                label.place(x=x + 3, y=y + height - 15, width=width - 6, height=13)

    def select_plan_month(self, month_index: int) -> None:
        """Selecciona la previsión mensual que se copiará como certificación."""
        self.selected_plan_month = month_index
        self.selected_certification_month = None
        self.selection_label.config(
            text=f"Plan {self.months[month_index].label} seleccionado para copiar a Certificación {month_index + 1}."
        )

    def select_certification_month(self, month_index: int) -> None:
        """Selecciona el mes certificado que se utilizará para generar informes."""
        self.selected_certification_month = month_index
        self.selected_plan_month = None
        self.selection_label.config(
            text=(
                f"Certificación {month_index + 1} ({self.months[month_index].label}) "
                "seleccionada para generar el informe."
            )
        )

    def copy_selected_plan(self) -> None:
        """Copia los importes planificados del mes a su certificación por partida."""
        if self.selected_plan_month is None:
            messagebox.showwarning(
                "Certificaciones", "Selecciona primero una cabecera de columna «Plan».", parent=self
            )
            return
        month_index = self.selected_plan_month
        month = self.months[month_index]
        for row, item in enumerate(self.items):
            item.certifications[month.key] = self.plan[row][month_index]
            item.certification_plans[month.key] = self.plan[row][month_index]
        self._refresh_tree()
        self._set_dirty(True)
        self.selection_label.config(
            text=f"Plan {month.label} copiado a Certificación {month_index + 1}."
        )

    def generate_template_report(self, report: str) -> None:
        """Guarda y abre el informe mensual, conservando el destino si falla."""
        if self.selected_certification_month is None:
            messagebox.showwarning(
                "Informe de certificación",
                "Selecciona primero la cabecera de una columna «Certificación».",
                parent=self,
            )
            return
        index = self.selected_certification_month
        month = self.months[index]
        if any(not math.isfinite(item.certifications.get(month.key, 0.0)) for item in self.items):
            messagebox.showerror(
                "Informe de certificación", "Hay importes certificados no válidos en el mes seleccionado.",
                parent=self,
            )
            return
        if not any(abs(item.certifications.get(month.key, 0.0)) >= 0.005 for item in self.items):
            messagebox.showinfo(
                "Informe de certificación",
                f"No hay partidas con importe certificado en Certificación {index + 1}.",
                parent=self,
            )
            return
        settings = self._report_settings()
        is_odt = settings.output_format == "odt"
        extension = ".odt" if is_odt else ".pdf"
        filetype = ("Documento ODT", "*.odt") if is_odt else ("Documento PDF", "*.pdf")
        file_path = filedialog.asksaveasfilename(
            parent=self, title=f"Generar {report}", defaultextension=extension,
            initialfile=f"{report} {index + 1} {month.key}{extension}", filetypes=[filetype],
        )
        if not file_path:
            return
        target = Path(file_path)
        if target.suffix.lower() != extension:
            target = target.with_suffix(extension)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=extension, dir=target.parent, delete=False) as handle:
                temporary_path = Path(handle.name)
            if is_odt:
                write_certification_odt(
                    self.items, self.chapter_title, self.project_title, settings,
                    report, temporary_path, month.key, index + 1, month.label,
                )
            else:
                write_certification_pdf(
                    self.items, self.chapter_title, self.project_title, settings,
                    report, temporary_path, month.key, index + 1, month.label,
                )
            temporary_path.replace(target)
        except ImportError as exc:
            messagebox.showerror(
                "Dependencia de informes",
                f"Instala las dependencias con:\npython -m pip install -r requirements.txt\n\n{exc}",
                parent=self,
            )
            return
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo generar el informe", str(exc), parent=self)
            return
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        self.selection_label.config(text=translate(f"Informe guardado: {target}"))
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showwarning(
                "Informe guardado", f"El PDF se ha guardado en:\n{target}\n\nNo se pudo abrir:\n{exc}",
                parent=self,
            )

    def generate_certification_report(self) -> None:
        """Genera la vista HTML de importes certificados, previsiones y desviaciones del mes."""
        if self.selected_certification_month is None:
            messagebox.showwarning(
                "Informe de certificación",
                "Selecciona primero la cabecera de una columna «Certificación».",
                parent=self,
            )
            return
        month_index = self.selected_certification_month
        month = self.months[month_index]
        certified_items = [
            item for item in self.items
            if not math.isclose(item.certifications.get(month.key, 0.0), 0.0, abs_tol=0.005)
        ]
        if not certified_items:
            messagebox.showinfo(
                "Informe de certificación",
                f"No hay partidas con importe certificado en Certificación {month_index + 1}.",
                parent=self,
            )
            return
        report_path = Path(tempfile.gettempdir()) / (
            f"certificacion_{month_index + 1}_{month.key}.html"
        )
        report_path.write_text(
            certification_report_html(
                self.items, self.activities, self.plan, month, month_index + 1, self.project_title
            ),
            encoding="utf-8",
        )
        webbrowser.open(report_path.as_uri())
        self.selection_label.config(
            text=f"Informe de Certificación {month_index + 1} abierto en el navegador."
        )

    def edit_certification(self, event):
        """Identifica una celda de certificación y abre su editor de importe."""
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row or not column:
            return "break"
        key = self.tree["columns"][int(column[1:]) - 1]
        if not key.startswith("cert_"):
            return "break"
        month_index = int(key.removeprefix("cert_"))
        self._open_certification_editor(int(row), month_index)
        return "break"

    def _open_certification_editor(self, row: int, month_index: int) -> None:
        """Superpone una entrada de texto sobre la celda mensual seleccionada."""
        column = f"#{6 + month_index * 2}"
        bbox = self.tree.bbox(str(row), column)
        if not bbox:
            return
        self._close_editor()
        x, y, width, height = bbox
        editor = ttk.Entry(self.tree)
        item = self.items[row]
        current = item.certifications.get(self.months[month_index].key, self.plan[row][month_index])
        editor.insert(0, f"{current:.2f}")
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        self._editor = editor

        def commit(_event=None):
            """Confirma el importe escrito y cierra el editor de la certificación."""
            value = editor.get()
            self._close_editor()
            self._commit_certification(row, month_index, value)

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", lambda _event: self._close_editor())

    def _commit_certification(self, row: int, month_index: int, raw: str) -> None:
        """Valida el importe, ajusta previsiones futuras y actualiza la desviación mensual."""
        certified = parse_float(raw, float("nan"))
        if not math.isfinite(certified) or certified < 0:
            messagebox.showerror(
                "Certificación", "Introduce un importe numérico igual o mayor que cero.", parent=self
            )
            return
        item = self.items[row]
        month = self.months[month_index]
        item.certification_plans.setdefault(month.key, self.plan[row][month_index])
        remainder = reconcile_certification(self.plan[row], month_index, certified)
        item.certifications[month.key] = certified
        item.planned_amounts = {
            entry.key: self.plan[row][index] for index, entry in enumerate(self.months)
        }
        self._set_dirty(True)
        self._refresh_tree()
        self._select_certification(row, month_index)
        if remainder:
            self.deviation_label.config(
                text=(
                    f"Certificación superior al saldo planificado en {fmt_eur(remainder)}. "
                    "No hay previsión futura suficiente; justifica la desviación."
                )
            )

    def _select_certification(self, row: int, month_index: int) -> None:
        """Selecciona la partida y el mes, y muestra su justificación."""
        self.tree.selection_set(str(row))
        self.tree.focus(str(row))
        self._selected_cell = (row, month_index)
        self._update_detail(row, month_index)
        self.tree.see(str(row))
        self.tree.focus_set()

    def show_selected_detail(self, event=None) -> None:
        """Identifica la certificación pulsada para mostrar su detalle económico."""
        selection = self.tree.selection()
        if not selection:
            return
        row = int(selection[0])
        x = (
            event.x
            if event is not None
            else self.winfo_pointerx() - self.tree.winfo_rootx()
        )
        column = self.tree.identify_column(x)
        if not column:
            return
        key = self.tree["columns"][int(column[1:]) - 1]
        if key.startswith("cert_"):
            self._selected_cell = (row, int(key.removeprefix("cert_")))
            self._update_detail(*self._selected_cell)

    def _update_detail(self, row: int, month_index: int) -> None:
        """Muestra la previsión, el certificado, la desviación y la justificación del mes."""
        item = self.items[row]
        month = self.months[month_index]
        planned = item.certification_plans.get(month.key, self.plan[row][month_index])
        certified = item.certifications.get(month.key)
        difference = (certified if certified is not None else 0.0) - planned
        status = "Pendiente de certificar" if certified is None else (
            "Sin desviación" if math.isclose(difference, 0.0, abs_tol=0.005)
            else f"Desviación: {fmt_eur(difference)}"
        )
        self.deviation_label.config(
            text=(
                f"{item.code} · {month.label} · Plan: {fmt_eur(planned)} · "
                f"Certificado: {fmt_eur(certified) if certified is not None else '-'} · {status}"
            )
        )
        self.justification.delete("1.0", "end")
        self.justification.insert(
            "1.0", item.certification_justifications.get(month.key, "")
        )

    def save_justification(self, _event=None) -> None:
        """Guarda el texto de justificación de la certificación seleccionada."""
        if self._selected_cell is None:
            return
        row, month_index = self._selected_cell
        self.items[row].certification_justifications[self.months[month_index].key] = (
            self.justification.get("1.0", "end-1c").strip()
        )
        self._set_dirty(True)

    def _close_editor(self) -> None:
        """Cierra y descarta el editor de importe que siga abierto."""
        if self._editor is not None:
            editor, self._editor = self._editor, None
            editor.destroy()
