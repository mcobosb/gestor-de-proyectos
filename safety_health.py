"""Propuesta revisable de partidas de Seguridad y Salud basada en la matriz BCCA."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from app_paths import application_directory
from localization import filedialog, messagebox

if TYPE_CHECKING:
    from bc3_gui import BudgetApp, ProjectItem


SAFETY_BUDGET_SOURCE = "SS:"
RELATIONSHIP_PATH = application_directory(__file__) / "datos" / "seguridad_salud_relaciones_bcca2023.csv"


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


def serialize_state(lines: list[SafetyLine], percentage: Decimal) -> dict:
    """Conserva la revisión completa, incluidas las partidas desmarcadas."""
    return {
        "percentage": str(percentage),
        "lines": [
            {"code": line.code, "unit": line.unit, "summary": line.summary,
             "price": str(line.price), "weight": str(line.weight), "selected": line.selected}
            for line in lines
        ],
    }


def restore_state(data: object) -> tuple[list[SafetyLine], Decimal]:
    """Valida la revisión JSON sin consultar la matriz ni recalcular el presupuesto."""
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise ValueError("Los datos de Seguridad y Salud no contienen una revisión válida.")
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


def synchronize_budget(app: BudgetApp, lines: list[SafetyLine], percentage: Decimal) -> None:
    """Sustituye exclusivamente las partidas SS generadas por la revisión aceptada."""
    from bc3_gui import MeasurementLine, ProjectItem

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
    chapter = next((item.chapter for item in app.budget_items if item.source.startswith(SAFETY_BUDGET_SOURCE)), "SS#")
    if chapter == "SS#":
        used = set(app.chapter_order) | {item.chapter for item in app.budget_items}
        suffix = 2
        while chapter in used:
            chapter = f"SS{suffix}#"
            suffix += 1
    app.budget_items[:] = [item for item in app.budget_items if not item.source.startswith(SAFETY_BUDGET_SOURCE)]
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
    app.safety_health_state = serialize_state(lines, percentage)
    app.refresh_budget_tree()
    app._mark_dirty()


def write_report(app: BudgetApp, target: Path) -> None:
    """Genera en PDF el presupuesto con mediciones del capítulo SS."""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate
    from bc3_gui import currency_label, fmt_number, format_chapter_code
    from report_layout import CONTENT_WIDTH, LEFT_MARGIN, RIGHT_MARGIN, ReportLayout, report_project_label

    items = [item for item in app.budget_items if item.source.startswith(SAFETY_BUDGET_SOURCE)]
    if not items:
        raise ValueError("Crea primero el capítulo de Seguridad y Salud para generar su informe.")
    chapter = items[0].chapter
    layout = ReportLayout(template_path=app.report_settings.template_path)
    title = "PRESUPUESTO DE SEGURIDAD Y SALUD"
    project = report_project_label(
        app._project_title(), currency_label(app.report_settings.currency, app.report_settings.custom_currency)
    )
    budget_headers = ("CÓDIGO", "UD.", "RESUMEN", "CANTIDAD", "PRECIO", "IMPORTE")
    budget_ratios = (50, 20, CONTENT_WIDTH - 250, 50, 60, 70)
    money = lambda value: fmt_number(float(Decimal(str(value)).quantize(Decimal("0.01"))))
    rows = [
        (item.code, item.unit, item.summary, fmt_number(item.measurement, 3),
         money(item.unit_price), money(item.import_total))
        for item in items
    ]
    total = sum((Decimal(str(item.import_total)) for item in items), Decimal())
    story = [
        layout.chapter(format_chapter_code(chapter), app._chapter_title(chapter)),
        layout.table(budget_headers, rows, budget_ratios),
        layout.total("TOTAL SEGURIDAD Y SALUD", money(total)),
    ]
    document = SimpleDocTemplate(
        str(target), pagesize=A4, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN,
        topMargin=layout.top_margin(title, project, budget_headers, budget_ratios),
        bottomMargin=layout.bottom_margin(), title=title, author=app.report_settings.author,
    )
    document.build(
        story,
        onFirstPage=lambda canvas, doc: layout.header(
            canvas, doc, title, project, column_headers=budget_headers, ratios=budget_ratios
        ),
        onLaterPages=lambda canvas, doc: layout.header(canvas, doc, title, project),
    )


class SafetyHealthWindow(tk.Toplevel):
    """Permite revisar las partidas sugeridas antes de incorporarlas al presupuesto."""
    def __init__(self, master: tk.Misc, app: BudgetApp):
        """Carga las propuestas y construye los controles para revisar su inclusión."""
        super().__init__(master)
        self.app = app
        self.title("Seguridad y salud")
        self.geometry("1060x620")
        self.minsize(850, 480)
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
        except ValueError as exc:
            messagebox.showerror("Seguridad y salud", str(exc), parent=self)
            self.after_idle(self.destroy)
            return
        self.percentage = tk.StringVar(value=str(percentage))
        ttk.Label(self, text="Seguridad y salud — propuesta revisable", style="Total.TLabel").pack(
            anchor="w", padx=12, pady=(12, 4)
        )
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
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Aceptar y crear capítulo", command=self.accept,
                   style="Accent.TButton").pack(side="right", padx=8)
        ttk.Button(buttons, text="Generar informe del capítulo", command=self.generate_report).pack(
            side="left"
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
            synchronize_budget(self.app, self.lines, percentage)
        except ValueError as exc:
            messagebox.showerror("Seguridad y salud", str(exc), parent=self)
            return False
        self.app._set_status("Capítulo Seguridad y Salud creado; revisa sus mediciones antes de guardar.")
        return True

    def generate_report(self) -> None:
        """Crea el capítulo si es necesario y genera su presupuesto con mediciones."""
        if not self._create_chapter():
            return
        target = filedialog.asksaveasfilename(
            parent=self, title="Generar informe de Seguridad y Salud",
            initialfile="Presupuesto Seguridad y Salud.pdf", defaultextension=".pdf",
            filetypes=[("Documento PDF", "*.pdf")],
        )
        if not target:
            return
        try:
            write_report(self.app, Path(target).with_suffix(".pdf"))
        except (ImportError, OSError, ValueError) as exc:
            messagebox.showerror("No se pudo generar el informe", str(exc), parent=self)
            return
