"""Módulo de planificación: parámetros, secuenciación de actividades y plan de trabajo.

Toma las partidas del presupuesto de `bc3_gui`, calcula duraciones a partir del
rendimiento y dibuja el diagrama de Gantt reutilizando `figura_gantt.draw`.
"""
from __future__ import annotations

import os
import tempfile
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from bc3_gui import APPLICATION_NAME, ToolTip, fmt_eur, fmt_number, format_chapter_code
from localization import filedialog, localize_widget_tree, messagebox, translate
from ui_theme import COLORS

from .._shared.scheduling import (
    RELATIONS, PlanActivity, PlanParams, apply_saved_plan_state, build_activities,
    chain_sequence, parse_date, schedule,
)


class PlanningWindow(tk.Toplevel):
    """Ventana de planificación: parámetros, secuencia y dibujo del plan de trabajo."""

    EDITABLE = {
        "teams": ("Nº equipos", int),
        "manual": ("Duración manual", int),
        "continuous": ("Toda la obra (S/N)", str),
        "pred": ("Predecesora (ID)", int),
        "relation": ("Tipo (FC/CC/FF/CF)", str),
        "lag": ("Desfase (días)", int),
    }

    def __init__(
        self,
        master: tk.Misc,
        items,
        chapter_title,
        title: str,
        saved_state: dict | None = None,
        on_state_change=None,
        on_save=None,
        on_dirty_change=None,
        currency_formatter=None,
    ):
        """Crea la ventana, restaura la planificación guardada y calcula las fechas."""
        super().__init__(master)
        self.title(APPLICATION_NAME)
        self.geometry("1280x760")
        self.minsize(1000, 600)
        self.state("zoomed")

        self.project_title = title
        self.params = PlanParams()
        self.activities = build_activities(items, chapter_title)
        self._on_state_change = on_state_change
        self._on_save = on_save
        self._on_dirty_change = on_dirty_change
        self._currency_formatter = currency_formatter or fmt_eur
        self._is_dirty = False
        self._ready = False
        self._editor: ttk.Entry | ttk.Combobox | None = None
        if saved_state:
            self._restore_state(saved_state)

        self._build_ui()
        self._refresh_holidays()
        self._recalculate()
        localize_widget_tree(self)
        self._ready = True
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _restore_state(self, state: dict) -> None:
        """Aplica los parámetros y actividades de un estado de planificación."""
        apply_saved_plan_state(self.params, self.activities, state)

    def to_state(self) -> dict:
        """Serializa parámetros, festivos y relaciones entre actividades en un diccionario."""
        return {
            "params": {
                "start": self.params.start.isoformat(),
                "hours_per_day": self.params.hours_per_day,
                "scale": self.params.scale,
                "plan": self.params.plan,
                "holidays": [
                    {"date": day.isoformat(), "description": description}
                    for day, description in self.params.holidays
                ],
            },
            "activities": [
                {
                    "chapter": activity.chapter,
                    "code": activity.code,
                    "teams": activity.teams,
                    "manual_duration": activity.manual_duration,
                    "continuous": activity.continuous,
                    "pred": activity.pred,
                    "relation": activity.relation,
                    "lag": activity.lag,
                }
                for activity in self.activities
            ],
        }

    def _notify_state_change(self) -> None:
        """Comunica al proyecto principal el estado actual de la planificación."""
        if self._on_state_change is not None:
            self._on_state_change(self.to_state())

    def _set_dirty(self, value: bool) -> None:
        """Actualiza el indicador de cambios y avisa al proyecto principal."""
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

    def _close(self) -> None:
        """Comunica el estado actual antes de cerrar la ventana de planificación."""
        self._notify_state_change()
        self.destroy()

    def save_changes(self) -> None:
        """Guarda la planificación mediante el proyecto y limpia su indicador de cambios."""
        self._notify_state_change()
        if self._on_save is not None and self._on_save():
            self._set_dirty(False)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        """Construye las pestañas de parámetros y secuencia, el guardado y la barra de estado."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        actions = ttk.Frame(self, padding=(8, 8, 8, 0))
        actions.grid(row=0, column=0, sticky="ew")
        self.save_button = tk.Button(actions, text="💾", width=3, command=self.save_changes)
        self._save_button_background = self.save_button.cget("background")
        self._save_button_foreground = self.save_button.cget("foreground")
        self.save_button.pack(side="right")
        ToolTip(self.save_button, "Guardar los cambios de planificación en el proyecto.")

        notebook = ttk.Notebook(self, padding=8)
        notebook.grid(row=1, column=0, sticky="nsew")
        self.notebook = notebook

        params_tab = ttk.Frame(notebook, padding=10)
        plan_tab = ttk.Frame(notebook, padding=10)
        notebook.add(params_tab, text="1. Parámetros")
        notebook.add(plan_tab, text="2. Secuencia y plan de trabajo")

        self._build_params_tab(params_tab)
        self._build_plan_tab(plan_tab)

        self.status = ttk.Label(self, text="", padding=(12, 0, 12, 8))
        self.status.grid(row=2, column=0, sticky="w")

    def _build_params_tab(self, parent: ttk.Frame) -> None:
        """Crea los controles de calendario, jornada, escala y festivos."""
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(6, weight=1)

        self.start_var = tk.StringVar(value=f"{self.params.start:%d/%m/%Y}")
        self.hours_var = tk.StringVar(value=str(int(self.params.hours_per_day)))
        self.scale_var = tk.StringVar(value=str(self.params.scale))
        self.plan_var = tk.StringVar(value=self.params.plan)

        rows = [
            ("Fecha de inicio de obra (dd/mm/aaaa)", ttk.Entry(parent, textvariable=self.start_var, width=18)),
            ("Horas por jornada", ttk.Entry(parent, textvariable=self.hours_var, width=18)),
            ("Escala del Gantt (1 = día, 7 = semana)",
             ttk.Combobox(parent, textvariable=self.scale_var, values=("1", "7"), width=15, state="readonly")),
            ("Detalle de la figura",
             ttk.Combobox(parent, textvariable=self.plan_var, values=("Resumen", "Completo"), width=15, state="readonly")),
        ]
        for index, (label, widget) in enumerate(rows):
            ttk.Label(parent, text=label).grid(row=index, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=index, column=1, sticky="w", pady=3)

        ttk.Label(parent, text="Festivos (no laborables)", font=("Segoe UI", 10, "bold")).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(14, 4)
        )

        holidays_frame = ttk.Frame(parent)
        holidays_frame.grid(row=6, column=0, columnspan=2, sticky="nsew")
        holidays_frame.columnconfigure(0, weight=1)
        holidays_frame.rowconfigure(0, weight=1)

        self.holidays_tree = ttk.Treeview(holidays_frame, columns=("fecha", "descripcion"), show="headings", height=10)
        self.holidays_tree.heading("fecha", text="Fecha")
        self.holidays_tree.heading("descripcion", text="Descripción")
        self.holidays_tree.column("fecha", width=110, anchor="center", stretch=False)
        self.holidays_tree.column("descripcion", width=360)
        self.holidays_tree.grid(row=0, column=0, sticky="nsew")

        actions = ttk.Frame(holidays_frame)
        actions.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.new_holiday_date = ttk.Entry(actions, width=14)
        self.new_holiday_desc = ttk.Entry(actions, width=34)
        ttk.Label(actions, text="Fecha:").pack(side="left")
        self.new_holiday_date.pack(side="left", padx=(4, 10))
        ttk.Label(actions, text="Descripción:").pack(side="left")
        self.new_holiday_desc.pack(side="left", padx=(4, 10))
        ttk.Button(actions, text="Añadir festivo", command=self.add_holiday).pack(side="left")
        ttk.Button(actions, text="Eliminar festivo", command=self.delete_holiday).pack(side="left", padx=(6, 0))

        ttk.Button(parent, text="Aplicar parámetros y continuar  →", command=self.apply_params).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

    def _build_plan_tab(self, parent: ttk.Frame) -> None:
        """Crea la tabla editable de actividades y las acciones para secuenciar y dibujar."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(toolbar, text="Secuenciar una tras otra", command=self.sequence_chain).pack(side="left")
        ttk.Button(toolbar, text="Recalcular fechas", command=self._recalculate).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Dibujar plan de trabajo", command=self.draw_plan, style="Accent.TButton").pack(side="left", padx=(6, 0))
        ttk.Label(
            toolbar,
            text="Doble clic sobre equipos, duración, continuidad, predecesora, tipo o desfase para editarlos.",
        ).pack(side="left", padx=(12, 0))

        columns = (
            "id", "capitulo", "codigo", "resumen", "medicion", "rend",
            "teams", "duracion", "manual", "continuous", "pred", "relation", "lag", "inicio", "fin",
        )
        headers = [
            ("id", "ID", 40, "center"), ("capitulo", "Capítulo", 90, "w"), ("codigo", "Código", 100, "w"),
            ("resumen", "Resumen", 260, "w"), ("medicion", "Medición", 90, "e"), ("rend", "Rend. (h/ud)", 90, "e"),
            ("teams", "Equipos", 65, "center"), ("duracion", "Duración", 70, "center"),
            ("manual", "Dur. manual", 80, "center"), ("continuous", "Toda la obra", 85, "center"),
            ("pred", "Pred.", 55, "center"), ("relation", "Tipo", 50, "center"), ("lag", "Desfase", 60, "center"),
            ("inicio", "Inicio", 90, "center"), ("fin", "Fin", 90, "center"),
        ]
        self.tree = ttk.Treeview(parent, columns=columns, show="headings")
        for key, title, width, anchor in headers:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor, stretch=key == "resumen")
        self.tree.tag_configure("odd", background=COLORS["blue_soft"])
        self.tree.tag_configure("cont", background="#FDF0DC")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self.edit_cell)

        self.plan_summary = ttk.Label(parent, text="", style="Total.TLabel")
        self.plan_summary.grid(row=2, column=0, sticky="ew", pady=(6, 0))

    # ------------------------------------------------------------- Festivos
    def _refresh_holidays(self) -> None:
        """Ordena los festivos y vuelve a mostrarlos en la tabla del calendario."""
        for row in self.holidays_tree.get_children():
            self.holidays_tree.delete(row)
        self.params.holidays.sort(key=lambda h: h[0])
        for index, (day, description) in enumerate(self.params.holidays):
            self.holidays_tree.insert("", "end", iid=str(index), values=(f"{day:%d/%m/%Y}", description))

    def add_holiday(self) -> None:
        """Valida y añade un festivo, y recalcula la planificación."""
        day = parse_date(self.new_holiday_date.get())
        if day is None:
            messagebox.showerror("Festivo", "Introduce la fecha como dd/mm/aaaa.", parent=self)
            return
        self.params.holidays.append((day, self.new_holiday_desc.get().strip() or "Festivo"))
        self.new_holiday_date.delete(0, "end")
        self.new_holiday_desc.delete(0, "end")
        self._refresh_holidays()
        self._recalculate()

    def delete_holiday(self) -> None:
        """Elimina el festivo seleccionado y recalcula las fechas de las actividades."""
        selection = self.holidays_tree.selection()
        if not selection:
            return
        del self.params.holidays[int(selection[0])]
        self._refresh_holidays()
        self._recalculate()

    def apply_params(self) -> None:
        """Valida los parámetros de trabajo y actualiza la secuencia de actividades."""
        start = parse_date(self.start_var.get())
        if start is None:
            messagebox.showerror("Parámetros", "La fecha de inicio debe tener el formato dd/mm/aaaa.", parent=self)
            return
        try:
            hours = float(self.hours_var.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Parámetros", "Las horas por jornada deben ser un número.", parent=self)
            return
        if hours <= 0:
            messagebox.showerror("Parámetros", "Las horas por jornada deben ser mayores que cero.", parent=self)
            return
        self.params.start = start
        self.params.hours_per_day = hours
        self.params.scale = int(self.scale_var.get())
        self.params.plan = self.plan_var.get()
        self._recalculate()
        self.notebook.select(1)

    # ---------------------------------------------------------- Actividades
    def sequence_chain(self) -> None:
        """Encadena las actividades mediante relaciones fin-comienzo y recalcula el plan."""
        chain_sequence(self.activities)
        self._recalculate()
        self._set_status("Actividades encadenadas una tras otra (fin-comienzo).")

    def _recalculate(self) -> None:
        """Calcula fechas, refresca la tabla y comunica los cambios al proyecto."""
        schedule(self.activities, self.params)
        self._refresh_tree()
        self._notify_state_change()
        if self._ready:
            self._set_dirty(True)

    def _refresh_tree(self) -> None:
        """Muestra actividades, fechas y resumen económico de la planificación."""
        self._close_editor()
        for row in self.tree.get_children():
            self.tree.delete(row)
        for index, act in enumerate(self.activities):
            self.tree.insert(
                "", "end", iid=str(index),
                values=(
                    act.id, format_chapter_code(act.chapter), act.code, act.summary,
                    f"{fmt_number(act.quantity)} {act.unit}".strip(), fmt_number(act.rendimiento, 4),
                    act.teams, act.duration, act.manual_duration or "", "S" if act.continuous else "N",
                    act.pred or "", act.relation, act.lag,
                    f"{act.start_date:%d/%m/%Y}" if act.start_date else "",
                    f"{act.end_date:%d/%m/%Y}" if act.end_date else "",
                ),
                tags=("cont" if act.continuous else ("odd" if index % 2 else "even"),),
            )
        plazo = max((a.finish_offset for a in self.activities), default=0)
        pem = sum(a.amount for a in self.activities)
        end = max((a.end_date for a in self.activities if a.end_date), default=None)
        fin = f"  ·  Fin previsto: {end:%d/%m/%Y}" if end else ""
        self.plan_summary.config(
            text=f"Actividades: {len(self.activities)}  ·  Plazo: {plazo} días laborables"
                 f"{fin}  ·  PEM: {self._currency_formatter(pem)}"
        )

    def edit_cell(self, event=None):
        """Abre el editor apropiado para una celda editable de la secuencia."""
        self._close_editor()
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row or not column:
            return
        key = self.tree["columns"][int(column[1:]) - 1]
        if key not in self.EDITABLE:
            return
        act = self.activities[int(row)]
        self.tree.see(row)
        bbox = self.tree.bbox(row, column)
        if not bbox:
            return
        x, y, width, height = bbox

        current = {
            "teams": str(act.teams),
            "manual": str(act.manual_duration or ""),
            "continuous": "S" if act.continuous else "N",
            "pred": str(act.pred or ""),
            "relation": act.relation,
            "lag": str(act.lag),
        }[key]

        if key == "relation":
            editor: ttk.Entry | ttk.Combobox = ttk.Combobox(self.tree, values=RELATIONS, state="readonly")
        elif key == "continuous":
            editor = ttk.Combobox(self.tree, values=("S", "N"), state="readonly")
        else:
            editor = ttk.Entry(self.tree)
        editor.place(x=x, y=y, width=width, height=height)
        editor.insert(0, current) if isinstance(editor, ttk.Entry) else editor.set(current)
        editor.focus_set()
        editor.bind("<Return>", lambda _e: self._commit_cell(act, key, editor.get()))
        editor.bind("<<ComboboxSelected>>", lambda _e: self._commit_cell(act, key, editor.get()))
        editor.bind("<Escape>", lambda _e: self._close_editor())
        editor.bind("<FocusOut>", lambda _e: self._commit_cell(act, key, editor.get()))
        self._editor = editor
        return "break"

    def _commit_cell(self, act: PlanActivity, key: str, raw: str) -> None:
        """Valida el valor editado y recalcula equipos, duración o relaciones."""
        if self._editor is None:
            return
        self._close_editor()
        raw = (raw or "").strip()
        try:
            if key == "teams":
                act.teams = max(1, int(raw or 1))
            elif key == "manual":
                act.manual_duration = int(raw) if raw else None
            elif key == "continuous":
                act.continuous = raw.upper().startswith("S")
            elif key == "pred":
                act.pred = int(raw) if raw else None
            elif key == "relation":
                act.relation = raw.upper() if raw.upper() in RELATIONS else "FC"
            elif key == "lag":
                act.lag = int(raw or 0)
        except ValueError:
            messagebox.showerror("Valor no válido", f"«{raw}» no es un número válido.", parent=self)
            return
        self._recalculate()

    def _close_editor(self) -> None:
        """Destruye el editor de celda activo y libera su referencia."""
        if self._editor is not None:
            editor, self._editor = self._editor, None
            editor.destroy()

    # ---------------------------------------------------------------- Gantt
    def draw_plan(self) -> None:
        """Transforma actividades programadas y genera las vistas PNG y PDF del Gantt."""
        self._recalculate()
        if not self.activities:
            messagebox.showinfo("Plan de trabajo", "No hay actividades que dibujar.", parent=self)
            return
        try:
            from . import figura_gantt
        except ImportError as exc:
            messagebox.showerror("Plan de trabajo", f"No se pudo cargar figura_gantt: {exc}", parent=self)
            return

        acts = [
            figura_gantt.Activity(
                id=act.id, chapter=act.chapter, code=act.code, summary=act.summary, unit=act.unit,
                quantity=act.quantity, amount=act.amount, duration=act.duration,
                start=act.start_date, end=act.end_date, continuous=act.continuous,
            )
            for act in self.activities
            if act.start_date and act.end_date
        ]
        names = {act.chapter: act.chapter_name for act in self.activities}
        start_works = min(a.start for a in acts)
        end_works = max(a.end for a in acts)
        plazo = max(a.finish_offset for a in self.activities)
        pem = sum(a.amount for a in self.activities)

        out_base = Path(tempfile.gettempdir()) / "plan_de_trabajo"
        try:
            png, pdf = figura_gantt.draw(
                acts, names, start_works, end_works, plazo, pem,
                self.params.holiday_dates, self.project_title, self.params.plan, out_base,
                currency_formatter=self._currency_formatter,
            )
        except Exception as exc:  # pragma: no cover - depende de matplotlib
            messagebox.showerror("Plan de trabajo", f"No se pudo dibujar el plan:\n{exc}", parent=self)
            return

        self._set_status(f"Plan de trabajo generado: {png.name} y {pdf.name}")
        self._show_result(png, pdf)

    def _show_result(self, png: Path, pdf: Path) -> None:
        """Abre una ventana con la imagen del plan y acciones para guardar o abrirla."""
        window = tk.Toplevel(self)
        window.title(APPLICATION_NAME)
        window.state("zoomed")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        canvas_frame = ttk.Frame(window, padding=8)
        canvas_frame.grid(row=0, column=0, sticky="nsew")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)

        try:
            image = tk.PhotoImage(file=str(png))
            factor = max(1, image.width() // 1100)
            preview = image.subsample(factor, factor)
            label = ttk.Label(canvas_frame, image=preview)
            label.image = preview  # evita que el recolector borre la imagen
            label.grid(row=0, column=0)
        except tk.TclError:
            ttk.Label(canvas_frame, text=f"Figura generada en:\n{png}").grid(row=0, column=0)

        actions = ttk.Frame(window, padding=(8, 0, 8, 8))
        actions.grid(row=1, column=0, sticky="ew")
        ttk.Button(actions, text="Abrir PNG", command=lambda: self._open_file(png)).pack(side="left")
        ttk.Button(actions, text="Abrir PDF", command=lambda: self._open_file(pdf)).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Guardar como…", command=lambda: self._save_copy(png, pdf)).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Cerrar", command=window.destroy).pack(side="right")
        localize_widget_tree(window)

    @staticmethod
    def _open_file(path: Path) -> None:
        """Abre el fichero del plan con su aplicación asociada o con el navegador."""
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            webbrowser.open(path.as_uri())

    def _save_copy(self, png: Path, pdf: Path) -> None:
        """Guarda una copia del plan generado en formato PNG o PDF."""
        target = filedialog.asksaveasfilename(
            parent=self, defaultextension=".pdf", initialfile="plan_de_trabajo.pdf",
            filetypes=[("PDF", "*.pdf"), ("PNG", "*.png")], title="Guardar plan de trabajo",
        )
        if not target:
            return
        destination = Path(target)
        source = pdf if destination.suffix.lower() == ".pdf" else png
        destination.write_bytes(source.read_bytes())
        self._set_status(f"Plan de trabajo guardado en {destination}")

    def _set_status(self, text: str) -> None:
        """Muestra un mensaje en la barra de estado de planificación."""
        self.status.config(text=translate(text))
