from __future__ import annotations

import csv
from collections.abc import Iterator
from copy import deepcopy
import html
import json
import os
import re
import tempfile
import tkinter as tk
import unicodedata
import webbrowser
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import partial
from math import isfinite
from pathlib import Path
from tkinter import ttk

from app_paths import application_directory, user_data_directory
from localization import (
    LANGUAGES, filedialog, localize_widget_tree, messagebox, set_language,
    simpledialog, translate,
)
from ui_theme import COLORS, apply_theme
from budget_reports import REPORT_NAMES, ReportSettings, write_pdf
from odt_reports import write_report_odt


APPLICATION_NAME = "Gestor de Proyectos"
APPLICATION_VERSION = "1.5.1"
APPLICATION_RELEASE_DATE = date(2026, 10, 30)
REPOSITORY_URL = "https://github.com/mcobosb/gestor-de-proyectos"
BUY_ME_A_COFFEE_URL = "https://buymeacoffee.com/mcobosb"
REFERENCE_PRICE_DATABASES = (
    (
        "Andalucía (JA) — BCCA",
        "https://www.juntadeandalucia.es/organismos/viviendajuventudyordenaciondelterritorio/areas/vivienda-rehabilitacion/planes-instrumentos/paginas/vivienda-bcca.html",
    ),
    (
        "Extremadura — Base de precios",
        "https://www.juntaex.es/w/base-precios-construccion-2026",
    ),
    (
        "Madrid — Base de Datos de la Construcción",
        "https://www.comunidad.madrid/vivienda/base-datos-construccion",
    ),
)

CONCEPT_TYPES = {
    "chapter": "▣ Capítulo",
    "0": "◆ Partida",
    "aux": "◇ Auxiliar",
    "1": "♟ Mano de obra",
    "2": "⚙ Maquinaria",
    "3": "▧ Material",
    "%": "% Porcentaje",
}

DUPLICATE_ITEM_MARKER = "*"
CURRENCY_SYMBOLS = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "CHF": "CHF",
    "CAD": "CA$",
    "AUD": "A$",
}

ITEM_FIELDS = (
    ("Capítulo", "chapter_var"),
    ("Código", "code_var"),
    ("Unidad", "unit_var"),
    ("Resumen", "summary_var"),
    ("Medición", "measurement_var"),
    ("Precio", "price_var"),
    ("Rendimiento", "rend_var"),
)


class ToolTip:
    """Ayuda al pasar el ratón, sin cambiar el foco ni la acción del botón."""

    def __init__(self, widget: tk.Widget, text: str):
        """Asocia los eventos del puntero y prepara el estado de la ayuda emergente."""
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        for event in ("<Leave>", "<ButtonPress>", "<Destroy>", "<Unmap>"):
            widget.bind(event, self._hide, add="+")

    def _schedule(self, event=None) -> None:
        """Programa la aparición diferida de la ayuda al entrar el puntero."""
        self._hide()
        self._after_id = self.widget.after(400, self._show)

    def _show(self) -> None:
        """Crea y coloca una ventana emergente con el texto de ayuda."""
        self._after_id = None
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
            return
        window = tk.Toplevel(self.widget)
        self.window = window
        window.withdraw()
        window.wm_overrideredirect(True)
        window.attributes("-topmost", True)
        tk.Label(
            window, text=self.text, background="#fffbd6", foreground="#202020",
            relief="solid", borderwidth=1, padx=8, pady=5, wraplength=320, justify="left",
        ).pack()
        window.update_idletasks()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        x = max(0, min(x, window.winfo_screenwidth() - window.winfo_reqwidth()))
        if y + window.winfo_reqheight() > window.winfo_screenheight():
            y = max(0, self.widget.winfo_rooty() - window.winfo_reqheight() - 5)
        window.geometry(f"+{x}+{y}")
        window.deiconify()

    def _hide(self, event=None) -> None:
        """Cancela la ayuda pendiente y destruye la ventana emergente visible."""
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def round_half_up(value: float, digits: int = 2) -> float:
    """Redondeo comercial usado por FIEBDC/Presto (0,005 sube a 0,01)."""
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def fmt_eur(value: float) -> str:
    """Formatea una cantidad como euros con separadores numéricos españoles."""
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def currency_label(currency: str = "EUR", custom_currency: str = "") -> str:
    """Devuelve el símbolo o código de una divisa conocida o personalizada."""
    return custom_currency if currency == "OTHER" else CURRENCY_SYMBOLS.get(currency, currency)


def format_currency(value: float, currency: str = "EUR", custom_currency: str = "") -> str:
    """Formatea un importe con la divisa del proyecto y separadores españoles."""
    amount = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    symbol = currency_label(currency, custom_currency)
    if currency in {"USD", "GBP", "CAD", "AUD"}:
        return f"{symbol}{amount}"
    return f"{amount} {symbol}"


def fmt_number(value: float, digits: int = 2) -> str:
    """Representa un número con los decimales indicados y formato español."""
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_float(value: str | None, default: float = 0.0) -> float:
    """Convierte texto numérico con coma o punto decimal y usa un valor alternativo si falla."""
    if value is None:
        return default
    text = str(value).strip().replace(" ", "")
    if not text:
        return default

    # FIEBDC files in the wild use either "." or "," as decimal separator.
    # When both occur, the last one is the decimal separator.
    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        text = text.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def chapter_sort_key(code: str):
    """Obtiene una clave que ordena códigos por sus grupos numéricos y luego por texto."""
    nums = re.findall(r"\d+", code)
    digits = [int(n) for n in nums] if nums else [999999]
    return digits, code


def is_chapter_code(code: str) -> bool:
    """Indica si el código identifica un capítulo mediante el sufijo «#»."""
    return bool(code and code.endswith("#"))


def norm_code(code: str) -> str:
    """Normaliza un código eliminando marcas finales y espacios y pasándolo a mayúsculas."""
    return code.rstrip("#").strip().upper()


def format_chapter_code(code: str) -> str:
    """Muestra los códigos de capítulo de forma homogénea: 'CAP.01#' -> 'CAP. 01'."""
    label = code.rstrip("#").strip()
    match = re.match(r"^(?:CAP\.?\s*)?(\d+[A-Z0-9]*)$", label, re.IGNORECASE)
    return f"CAP. {match.group(1).upper()}" if match else (label or code)


@dataclass
class Concept:
    code: str
    unit: str = ""
    summary: str = ""
    price: float = 0.0
    ctype: str = "0"
    text: str = ""
    children: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class Measurement:
    total: float
    position: int = 0
    detail: str = ""


@dataclass
class MeasurementLine:
    """Medición: K × unidades × longitud × anchura × altura, omitiendo vacíos."""

    comment: str = ""
    units: float | None = None
    length: float | None = None
    width: float | None = None
    height: float | None = None
    ltype: str = ""
    k: float = 1.0

    @property
    def partial(self) -> float:
        """Calcula el producto de los factores de medición y lo redondea a céntimos."""
        factors = [self.units, self.length, self.width, self.height]
        if all(factor is None for factor in factors):
            return 0.0
        # El producto se calcula en decimal exacto: con floats, casos como
        # 1229,65 × 0,5 pierden el céntimo al redondear.
        result = Decimal(str(self.k))
        for factor in factors:
            if factor is not None:
                result *= Decimal(str(factor))
        return float(result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @property
    def is_subtotal(self) -> bool:
        """Indica si la línea está marcada como subtotal y debe excluirse del cálculo."""
        return self.ltype.strip() not in {"", "0"}


def parse_measurement_lines(detail: str) -> list[MeasurementLine]:
    """Convierte el detalle de un registro ~M en líneas de medición.

    El detalle son grupos de 6 campos separados por '\\': tipo, comentario,
    unidades, longitud, latitud (anchura) y altura.
    """
    if not detail:
        return []

    def optional(value: str) -> float | None:
        """Convierte un campo de medición no vacío en número y conserva vacíos como None."""
        return parse_float(value) if value.strip() else None

    parts = detail.split("\\")
    lines: list[MeasurementLine] = []
    for start in range(0, len(parts) - 5, 6):
        group = parts[start:start + 6]
        line = MeasurementLine(
            ltype=group[0].strip(),
            comment=group[1].strip(),
            units=optional(group[2]),
            length=optional(group[3]),
            width=optional(group[4]),
            height=optional(group[5]),
        )
        if line.is_subtotal:
            continue
        if not line.comment and line.partial == 0.0:
            continue
        lines.append(line)
    return lines


def format_measurement_lines(lines: list[MeasurementLine]) -> str:
    """Conserva los seis campos FIEBDC incorporando K a las unidades."""
    def render(value: float | None) -> str:
        """Serializa un factor numérico como texto o deja vacío el factor ausente."""
        return "" if value is None else f"{value:g}"

    def render_units(line: MeasurementLine) -> str:
        """Integra el coeficiente K en las unidades al reconstruir el detalle BC3."""
        if line.k == 1.0 or all(
            value is None for value in (line.units, line.length, line.width, line.height)
        ):
            return render(line.units)
        units = Decimal(str(line.units)) if line.units is not None else Decimal(1)
        return format(units * Decimal(str(line.k)), "f")

    return "".join(
        "\\".join(
            [
                line.ltype,
                line.comment,
                render_units(line),
                render(line.length),
                render(line.width),
                render(line.height),
            ]
        )
        + "\\"
        for line in lines
    )


@dataclass
class ProjectItem:
    code: str
    chapter: str
    unit: str
    summary: str
    measurement: float = 1.0
    unit_price: float = 0.0
    rendimiento: float = 0.0
    source: str = "manual"
    reference_base: str = ""
    text: str = ""
    measurement_detail: str = ""
    decomposition: list[tuple[str, float]] = field(default_factory=list)
    component_prices: dict[str, float] = field(default_factory=dict)
    measurement_lines: list[MeasurementLine] = field(default_factory=list)
    planned_amounts: dict[str, float] = field(default_factory=dict)
    certifications: dict[str, float] = field(default_factory=dict)
    certification_plans: dict[str, float] = field(default_factory=dict)
    certification_justifications: dict[str, str] = field(default_factory=dict)
    concept_type: str = ""
    component_types: dict[str, str] = field(default_factory=dict)
    component_concepts: dict[str, Concept] = field(default_factory=dict)
    direct_unit_price: float | None = None
    indirect_costs: float = 0.0
    round_import_total: bool = False

    def set_indirect_costs(self, percentage: float, direct_price: float | None = None) -> None:
        """Aplica el recargo sin perder el precio directo ni acumular porcentajes."""
        if direct_price is None:
            direct_price = self.unit_price if self.direct_unit_price is None else self.direct_unit_price
        if not isfinite(direct_price) or not isfinite(percentage) or not 0 <= percentage <= 100:
            raise ValueError("El precio directo y el porcentaje de costes indirectos deben ser válidos.")
        self.indirect_costs = percentage
        self.direct_unit_price = direct_price if percentage else None
        if not percentage:
            self.unit_price = direct_price
            return
        amount = Decimal(str(direct_price)) * Decimal(str(percentage)) / 100
        self.unit_price = float(
            Decimal(str(direct_price)) + amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )

    def indirect_components(self) -> list[tuple[Concept, float, float]]:
        """Genera las líneas calculadas sin modificar los recursos originales."""
        if not self.indirect_costs or self.direct_unit_price is None:
            return []
        rows = []
        if not self.decomposition:
            rows.append((Concept("DIRECTO", self.unit, "Coste directo"), 1.0, self.direct_unit_price))
        rows.append((
            Concept("%CI", "%", "Costes Indirectos", ctype="%"),
            self.indirect_costs, self.direct_unit_price / 100,
        ))
        return rows

    @property
    def import_total(self) -> float:
        """Devuelve el importe de la partida como medición por precio unitario."""
        if self.round_import_total:
            amount = Decimal(str(self.measurement)) * Decimal(str(self.unit_price))
            return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return self.measurement * self.unit_price

    def recalculate_measurement(self) -> float:
        """Recalcula la medición total a partir del desglose, si lo hay."""
        if self.measurement_lines:
            self.measurement = round_half_up(sum(line.partial for line in self.measurement_lines))
            self.measurement_detail = format_measurement_lines(self.measurement_lines)
        return self.measurement

    def component_price(self, code: str, default: float) -> float:
        """Obtiene el precio editado de un componente o el precio de referencia dado."""
        return self.component_prices.get(code, default)

    def cost_components(
        self, dataset: Bc3Dataset | None, *, reconcile: bool = True, strict: bool = True,
    ) -> list[tuple[Concept, float, float]]:
        """Calcula porcentajes BC3 sobre el acumulado y concilia solo redondeos pequeños."""
        if self.decomposition and all("%" in code for code, _ in self.decomposition):
            direct = self.unit_price if self.direct_unit_price is None else self.direct_unit_price
            return [(Concept("DIRECTO", self.unit, "Precio sin descomposición"), 1.0, direct)]
        rows = []
        accumulated = Decimal(0)
        for code, quantity in self.decomposition:
            concept = self.component_concepts.get(code)
            if concept is None:
                concept = dataset.concepts.get(code) if dataset else None
            if concept is None and code not in self.component_prices and strict:
                raise ValueError(
                    f"No se dispone del precio del recurso {code} de la partida {self.code}. "
                    "Carga su base de referencia antes de generar el informe."
                )
            concept = concept or Concept(code=code, summary=code)
            if "%" in code:
                price = float(accumulated)
                concept = replace(concept, ctype="%", unit="%")
            else:
                price = self.component_price(code, concept.price)
            amount = (Decimal(str(quantity)) * Decimal(str(price))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            accumulated += amount
            rows.append((concept, quantity, price))
        if reconcile and rows:
            direct = self.unit_price if self.direct_unit_price is None else self.direct_unit_price
            difference = Decimal(str(direct)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            ) - accumulated
            tolerance = Decimal("0.005") * len(rows) + Decimal("0.01")
            if difference and abs(difference) <= tolerance:
                rows.append((
                    Concept("REDONDEO", summary="Ajuste de redondeo", ctype="%"),
                    1.0, float(difference),
                ))
        return rows


class Bc3Dataset:
    def __init__(self, path: Path | str):
        """Inicializa las estructuras de datos y carga el archivo BC3 indicado."""
        self.path = Path(path)
        self.concepts: dict[str, Concept] = {}
        self.measurements: dict[tuple[str, str], Measurement] = {}
        self.root_codes: list[str] = []
        self.chapter_children: dict[str, list[str]] = {}
        self.load()

    def load(self) -> None:
        """Lee registros BC3 y construye conceptos, descomposiciones y mediciones."""
        if not self.path.exists():
            raise FileNotFoundError(f"No existe el archivo: {self.path}")
        raw = self.path.read_bytes()
        head = raw[:300].decode("latin-1", errors="ignore").upper()
        encoding = "cp850" if "|850|" in head else "cp437" if "|437|" in head else "cp1252"
        text = raw.decode(encoding, errors="replace")

        decompositions: dict[str, list[tuple[str, float]]] = {}
        raw_measurements: list[tuple[str, str, float, int, str]] = []
        texts: dict[str, str] = {}

        for record in re.split(r"(?:\r?\n)?~", text):
            if not record.strip():
                continue
            fields = record.rstrip("\r\n").split("|")
            kind = fields[0].strip().upper()

            if kind == "C" and len(fields) > 1:
                code = fields[1].strip()
                if not code:
                    continue
                concept = self.concepts.setdefault(code, Concept(code=code))
                concept.unit = fields[2].strip() if len(fields) > 2 else concept.unit
                concept.summary = fields[3].strip() if len(fields) > 3 else concept.summary
                concept.price = parse_float(fields[4].split("\\")[0], concept.price) if len(fields) > 4 else concept.price
                concept.ctype = fields[6].strip() if len(fields) > 6 else concept.ctype

            elif kind in {"D", "Y"} and len(fields) > 2:
                parent = fields[1].strip()
                if not parent:
                    continue
                children = decompositions.setdefault(parent, [])
                if kind == "D":
                    children.clear()
                parts = fields[2].split("\\")
                for idx in range(0, len(parts) - 2, 3):
                    child = parts[idx].strip()
                    if not child:
                        continue
                    qty = parse_float(parts[idx + 1], 1.0)
                    coef = parse_float(parts[idx + 2], 1.0)
                    children.append((child, qty * coef))

            elif kind == "M" and len(fields) > 3:
                path_codes = fields[1].split("\\")
                if len(path_codes) < 2:
                    continue
                positions = [part for part in fields[2].split("\\") if part.strip()]
                position = int(parse_float(positions[-1])) if positions else 0
                raw_measurements.append(
                    (
                        path_codes[0].strip(),
                        path_codes[1].strip(),
                        parse_float(fields[3]),
                        position,
                        fields[4].strip() if len(fields) > 4 else "",
                    )
                )

            elif kind == "T" and len(fields) > 2:
                texts[fields[1].strip()] = fields[2].strip()

        def resolve(code: str) -> str:
            """Resuelve variantes del código buscando sus formas con marcas de capítulo."""
            for candidate in (code, code + "#", code + "##"):
                if candidate in self.concepts:
                    return candidate
            return code

        for raw_parent, raw_children in decompositions.items():
            parent = resolve(raw_parent)
            concept = self.concepts.setdefault(parent, Concept(code=parent))
            concept.children = [(resolve(child), quantity) for child, quantity in raw_children]
            self.chapter_children[parent] = [child for child, _ in concept.children]

        for parent, child, quantity, position, detail in raw_measurements:
            self.measurements[(norm_code(parent), resolve(child))] = Measurement(
                quantity, position, detail
            )

        for code, text_value in texts.items():
            resolved = resolve(code)
            if resolved in self.concepts:
                self.concepts[resolved].text = text_value

        roots = [code for code in self.concepts if code.endswith("##")]
        if roots:
            root_children = self.concepts[roots[0]].children
            self.root_codes = [code for code, _ in root_children if is_chapter_code(code)]
        else:
            referenced = {
                child
                for concept in self.concepts.values()
                for child, _ in concept.children
                if is_chapter_code(child)
            }
            self.root_codes = [
                code
                for code in self.concepts
                if is_chapter_code(code)
                and not code.endswith("##")
                and code not in referenced
                and code not in {"-AUX#", "-BAS#", "-UNI#"}
            ]
        self.root_codes.sort(key=chapter_sort_key)

    def get_summary(self, code: str) -> str:
        """Devuelve el resumen del concepto o «Sin nombre» si no está definido."""
        return self.concepts.get(code, Concept(code)).summary or "Sin nombre"

    def get_children(self, code: str) -> list[str]:
        """Obtiene una copia de los códigos hijos asociados al capítulo indicado."""
        return list(self.chapter_children.get(code, []))

    def to_project_items(self) -> list[ProjectItem]:
        """Convierte las partidas de los capítulos raíz en elementos del presupuesto."""
        items: list[ProjectItem] = []

        def add_chapter(chapter: str) -> None:
            """Recorre un capítulo y añade sus partidas presupuestables al resultado."""
            concept = self.concepts.get(chapter)
            if concept is None:
                return
            for code, decomposition_quantity in concept.children:
                child = self.concepts.get(code)
                if child is None:
                    continue
                if is_chapter_code(code):
                    add_chapter(code)
                    continue
                if child.ctype != "0" or not child.summary:
                    continue
                measured = self.measurements.get((norm_code(chapter), code))
                items.append(
                    ProjectItem(
                        code=code,
                        chapter=chapter,
                        unit=child.unit,
                        summary=child.summary.upper(),
                        measurement=measured.total if measured else decomposition_quantity,
                        unit_price=child.price,
                        rendimiento=self.calculate_rendimiento(code),
                        source="BC3",
                        text=child.text.upper(),
                        measurement_detail=measured.detail if measured else "",
                        measurement_lines=parse_measurement_lines(measured.detail if measured else ""),
                        decomposition=list(child.children),
                    )
                )

        for chapter in self.root_codes:
            add_chapter(chapter)
        return items

    def calculate_rendimiento(self, code: str) -> float:
        """Suma las cantidades de mano de obra incluidas en la descomposición."""
        concept = self.concepts.get(code)
        if concept is None:
            return 0.0
        return sum(
            quantity
            for child_code, quantity in concept.children
            if self.concepts.get(child_code, Concept(child_code)).ctype == "1"
        )

    def find_parent_chapter(self, code: str) -> str:
        """Busca el capítulo que contiene directamente el concepto y usa una aproximación si no existe."""
        for parent, concept in self.concepts.items():
            if not is_chapter_code(parent):
                continue
            if any(child == code for child, _ in concept.children):
                return parent
        return self.closest_chapter(code)

    def closest_chapter(self, code: str) -> str:
        """Estima el capítulo raíz más cercano comparando los prefijos alfanuméricos."""
        prefix = re.sub(r"[^0-9A-Z]", "", code)
        for chapter in self.root_codes:
            if prefix.startswith(re.sub(r"[^0-9A-Z]", "", chapter)):
                return chapter
        return "00#"


RESOURCE_TAGS = {"1": "bcca_labour", "2": "bcca_machinery", "3": "bcca_material", "0": "bcca_item"}


def find_bc3_files(folder: Path) -> tuple[Path | None, Path | None]:
    """Localiza en la carpeta la base de precios (BCCA*) y el presupuesto de trabajo."""
    files = sorted(p for p in folder.glob("*.bc3") if p.is_file())
    base = next((p for p in files if p.name.upper().startswith("BCCA")), None)
    budget = next((p for p in files if p is not base), None)
    return base, budget


def find_reference_files(folder: Path) -> list[Path]:
    """Todas las bases de precios (BCCA*) de la carpeta."""
    return sorted(
        p for p in folder.glob("*.bc3") if p.is_file() and p.name.upper().startswith("BCCA")
    )


class BudgetApp:
    def __init__(self, root: tk.Tk):
        """Inicia la interfaz sin proyecto ni bases, conservando preferencias y recientes."""
        self.root = root
        self.root.title("Aplicación")
        self.root.geometry("1400x820")
        self.root.minsize(1100, 650)
        self.root.state("zoomed")
        apply_theme(self.root)

        self.reference_bases: list[Bc3Dataset] = []
        self.budget_dataset: Bc3Dataset | None = None
        self.budget_items: list[ProjectItem] = []
        self.selected_bcca_code: str | None = None
        self.selected_bcca_base: Bc3Dataset | None = None
        self.selected_budget_index: int | None = None
        self.selected_chapter: str | None = None
        self.chapter_order: list[str] = []
        self.chapter_titles: dict[str, str] = {}
        self.planning_state: dict = {}
        self.invoicing_state: dict = {}
        self._invoicing_window: tk.Toplevel | None = None
        self.rcd_state: dict = {}
        self.safety_health_state: dict = {}
        self._rcd_window: tk.Toplevel | None = None
        self._safety_health_window: tk.Toplevel | None = None
        self._project_windows: list[tk.Toplevel] = []
        self.project_title: str | None = None
        self.project_path: Path | None = None
        self.source_budget_path: Path | None = None
        self.is_dirty = False
        self.bcca_code_by_iid: dict[str, str] = {}
        self.bcca_base_by_iid: dict[str, Bc3Dataset] = {}
        self.budget_index_by_iid: dict[str, int] = {}
        self.chapter_iid_by_code: dict[str, str] = {}
        self._bcca_iid_counter = 0
        self._measurement_editor: ttk.Entry | None = None
        self._component_editor: ttk.Entry | None = None
        self._type_editor: ttk.Combobox | None = None
        self._drag_code: str | None = None
        self._drag_base: Bc3Dataset | None = None
        self._dragging = False
        self._sash_moved_by_user = False
        self._adjusting_sash = False
        self._bcca_visible = True
        self._collapse_chapters_on_refresh = False
        self._budget_detached: list[tuple[str, str, int]] = []
        self._budget_flattened: list[tuple[str, str, int]] = []
        self._budget_filter_open_state: dict[str, bool] | None = None
        self.budget_resource_filter = ""
        self.bcca_resource_filter = ""
        data_directory = user_data_directory(__file__)
        self.recent_projects_path = data_directory / "proyectos_recientes.json"
        self.recent_projects = self._read_recent_projects()
        self.recent_reference_bases_path = data_directory / "bases_referencia_recientes.json"
        self.recent_reference_bases = self._read_recent_reference_bases()
        self.preferences_path = data_directory / "preferencias.json"
        self.language = self._read_language_preference()
        set_language(self.language)
        self.report_settings_path = data_directory / "configuracion_informes.json"
        self.default_report_settings = self._read_report_settings()
        self.report_settings = ReportSettings.from_dict(self.default_report_settings.to_dict())

        self._build_ui()
        self._reset_history()
        self.root.bind("<Control-s>", self._save_shortcut)
        self.root.bind("<Control-z>", self.undo_last_action)
        self.root.protocol("WM_DELETE_WINDOW", self._close_application)

    def _build_ui(self) -> None:
        """Construye menús, controles, paneles y árboles de la ventana principal."""
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self.root, padding=(10, 8), style="Toolbar.TFrame")
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)

        file_menubutton = ttk.Menubutton(toolbar, text="Archivo", style="Toolbar.TMenubutton")
        file_menu = tk.Menu(file_menubutton, tearoff=False)
        self.file_menu = file_menu
        self._refresh_recent_projects_menu()
        file_menu.add_separator()
        file_menu.add_command(label="Nuevo proyecto", command=self.new_project)
        file_menu.add_separator()
        file_menu.add_command(label="Importar presupuesto BC3…", command=self.load_budget_dialog)
        file_menu.add_command(
            label="Añadir base de referencia BCCA…", command=self.load_bcca_dialog
        )
        file_menu.add_command(
            label="Quitar base de referencia…", command=self.remove_reference_base_dialog
        )
        file_menu.add_separator()
        file_menu.add_command(label="Abrir proyecto (JSON)…", command=self.load_project_dialog)
        file_menu.add_command(
            label="Guardar como (JSON)…", command=self.save_project_as
        )
        file_menu.add_separator()
        file_menu.add_command(label="Configuración…", command=self.open_report_settings)
        file_menubutton["menu"] = file_menu
        file_menubutton.pack(side="left", padx=(0, 8))

        self.export_menubutton = ttk.Menubutton(
            toolbar, text="Imprimir y exportar", style="Toolbar.TMenubutton"
        )
        export_menu = tk.Menu(self.export_menubutton, tearoff=False)
        export_menu.add_command(label="Vista previa del presupuesto…", command=self.preview_budget)
        export_menu.add_command(label="Imprimir presupuesto", command=self.print_budget)
        export_menu.add_separator()
        export_menu.add_command(label="Exportar presupuesto a ODS…", command=self.export_budget_ods)
        export_menu.add_command(label="Exportar mediciones a CSV…", command=self.export_measurements_csv)
        self.export_menubutton["menu"] = export_menu
        self.export_menubutton.pack(side="left", padx=(0, 8))

        reports_menubutton = ttk.Menubutton(toolbar, text="Informes", style="Toolbar.TMenubutton")
        reports_menu = tk.Menu(reports_menubutton, tearoff=False)
        for report in REPORT_NAMES:
            reports_menu.add_command(label=report, command=lambda name=report: self.generate_report(name))
        reports_menubutton["menu"] = reports_menu
        reports_menubutton.pack(side="left", padx=(0, 8))
        export_menu.add_separator()
        export_menu.add_command(label="Exportar recursos a ODS…", command=lambda: self.export_resource_report("Recursos"))
        export_menu.add_command(label="Exportar descompuestos a ODS…", command=lambda: self.export_resource_report("Descompuestos"))

        tools_menubutton = ttk.Menubutton(toolbar, text="Herramientas", style="Toolbar.TMenubutton")
        tools_menu = tk.Menu(
            tools_menubutton, tearoff=False, postcommand=self._refresh_tool_menu
        )
        self.tools_menu = tools_menu
        tools_menu.add_command(label="RCDs", command=self.open_rcds)
        tools_menu.add_command(label="Seguridad y salud", command=self.open_safety_health)
        tools_menu.add_command(label="Planificación", command=self.open_planning)
        tools_menu.add_command(label="Certificaciones", command=self.open_certifications)
        tools_menu.add_command(label="Facturación", command=self.open_invoicing)
        tools_menubutton["menu"] = tools_menu
        tools_menubutton.pack(side="left", padx=(0, 8))
        info_menubutton = ttk.Menubutton(toolbar, text="Información", style="Toolbar.TMenubutton")
        info_menu = tk.Menu(info_menubutton, tearoff=False)
        info_menu.add_command(label=APPLICATION_NAME, state="disabled")
        info_menu.add_command(
            label=translate("Versión {version} — {date}").format(
                version=APPLICATION_VERSION,
                date=APPLICATION_RELEASE_DATE.strftime("%d/%m/%Y"),
            ),
            state="disabled",
        )
        info_menu.add_command(
            label="Presupuestos BC3, mediciones, planificación y certificaciones",
            state="disabled",
        )
        info_menu.add_separator()
        reference_prices_menu = tk.Menu(info_menu, tearoff=False)
        for label, url in REFERENCE_PRICE_DATABASES:
            reference_prices_menu.add_command(
                label=label,
                command=partial(
                    self._open_web_page, url, "Bases de precios de referencia",
                    "Base de precios abierta en el navegador.",
                ),
            )
        info_menu.add_cascade(
            label="Bases de precios de referencia", menu=reference_prices_menu
        )
        info_menu.add_separator()
        info_menu.add_command(
            label="Repositorio en GitHub", command=self.open_repository_page
        )
        info_menu.add_command(
            label="Licencia y responsabilidad…", command=self.show_license_info
        )
        info_menu.add_command(
            label="Apoyar el proyecto (Buy Me a Coffee)", command=self.open_donation_page
        )
        info_menubutton["menu"] = info_menu
        info_menubutton.pack(side="left", padx=(0, 8))
        self.budget_summary = ttk.Label(
            toolbar,
            text=f"Total del presupuesto: {self._format_currency(0)}",
            style="Toolbar.TLabel",
        )
        self.budget_summary.pack(side="right", padx=(12, 0))
        self.bcca_visibility_text = tk.StringVar(value="▶")

        actions = ttk.Frame(self.root, padding=(10, 8))
        actions.grid(row=1, column=0, sticky="ew")
        self.save_button = ttk.Button(actions, text="Guardar", command=self.save_project)
        self.save_button.pack(side="left", padx=(0, 4))
        ToolTip(self.save_button, translate("Guardar el proyecto JSON (Ctrl+S). No modifica el BC3 original."))
        self.undo_button = ttk.Button(actions, text="Deshacer", command=self.undo_last_action)
        self.undo_button.pack(side="left", padx=(0, 8))
        ToolTip(self.undo_button, translate("Deshacer la última acción del proyecto (Ctrl+Z). Hasta 50 acciones."))
        self.save_state_label = ttk.Label(actions, style="Muted.TLabel")
        self.save_state_label.pack(side="right", padx=(8, 0))
        self.add_button = ttk.Button(
            actions, text="+", width=3, command=self.show_add_menu, style="Accent.TButton"
        )
        self.add_menu = tk.Menu(self.add_button, tearoff=False)
        self.add_menu.add_command(label="Añadir capítulo", command=self.add_chapter)
        self.add_menu.add_command(label="Añadir partida", command=self.add_manual_item)
        self.add_button.pack(side="left", padx=(0, 4))
        ttk.Button(
            actions,
            text="×",
            width=3,
            command=self.delete_selected_budget_entry,
            style="Danger.TButton",
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="←",
            width=3,
            command=self.add_selected_bcca_to_budget,
            style="Primary.TButton",
        ).pack(side="left", padx=(0, 12))
        ttk.Button(actions, text="↑", width=3, command=lambda: self.move_selected_budget_entry(-1)).pack(
            side="left", padx=(0, 4)
        )
        ttk.Button(actions, text="↓", width=3, command=lambda: self.move_selected_budget_entry(1)).pack(
            side="left", padx=(0, 12)
        )
        ttk.Button(
            actions, textvariable=self.bcca_visibility_text, command=self.toggle_bcca_panel
        ).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="✎", width=3, command=self.update_selected_budget_item).pack(side="left")
        ttk.Button(
            actions,
            text="⧉",
            width=3,
            command=self.duplicate_selected_budget_item,
        ).pack(side="left", padx=(4, 0))
        for button, help_text in zip((
            child for child in actions.winfo_children()
            if child not in (self.save_button, self.undo_button, self.save_state_label)
        ), (
            "Añadir un capítulo o una partida al presupuesto.",
            "Eliminar la partida o el capítulo seleccionado.",
            "Añadir la partida o el capítulo de referencia al presupuesto, o el precio básico al descompuesto seleccionado.",
            "Subir el recurso, la partida o el capítulo seleccionado.",
            "Bajar el recurso, la partida o el capítulo seleccionado.",
            "Mostrar u ocultar el panel de la base de referencia.",
            "Editar el código y nombre del capítulo o los datos de la partida seleccionada.",
            "Duplicar la partida seleccionada. La copia marcada con * debe modificarse manualmente.",
        )):
            ToolTip(button, help_text)
        expand_button = ttk.Button(
            actions, text="↕", width=3, command=self.toggle_all_budget_nodes,
        )
        expand_button.pack(side="left", padx=(12, 4))
        ToolTip(expand_button, "Expandir o contraer todos los capítulos y partidas.")
        # Panel divisible: arranca al 50 % y el usuario puede reajustarlo.
        self.splitter = ttk.PanedWindow(self.root, orient="horizontal")
        self.splitter.grid(row=2, column=0, sticky="nsew")

        self.left_panel = ttk.Frame(self.splitter, padding=(10, 0, 8, 10))
        self.right_panel = ttk.Frame(self.splitter, padding=(8, 0, 10, 10))
        self.splitter.add(self.left_panel, weight=1)
        self.splitter.add(self.right_panel, weight=1)
        self.splitter.bind("<Configure>", self._keep_even_split, add="+")
        self.splitter.bind("<ButtonRelease-1>", self._remember_manual_sash, add="+")

        self.left_panel.columnconfigure(0, weight=1)
        self.left_panel.rowconfigure(1, weight=1)
        self.right_panel.columnconfigure(0, weight=1)
        self.right_panel.rowconfigure(1, weight=1)

        self.project_panel_title = tk.StringVar(value="Proyecto: Sin título")
        budget_header = ttk.Frame(self.left_panel, style="BudgetHeader.TFrame")
        budget_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        budget_header.columnconfigure(0, weight=1)
        left_title = ttk.Label(
            budget_header, textvariable=self.project_panel_title, style="Section.TLabel"
        )
        left_title.grid(row=0, column=0, sticky="ew")
        self.budget_filter_buttons: dict[str, ttk.Checkbutton] = {}
        self.budget_filter_variables: dict[str, tk.BooleanVar] = {}
        for column, kind in enumerate(("2", "1", "3"), start=1):
            variable = tk.BooleanVar(value=False)
            self.budget_filter_variables[kind] = variable
            button = ttk.Checkbutton(
                budget_header, text=CONCEPT_TYPES[kind].split()[0], width=3,
                style="BudgetFilter.TCheckbutton", variable=variable,
                command=lambda selected=kind: self.toggle_budget_resource_filter(selected),
            )
            button.grid(row=0, column=column, padx=(4, 0))
            self.budget_filter_buttons[kind] = button
            ToolTip(button, f"Filtrar recursos: {CONCEPT_TYPES[kind]}. Pulsar de nuevo para mostrar todos.")
        self.reference_panel_title = tk.StringVar(value="Base de referencia: Sin cargar")
        reference_header = ttk.Frame(self.right_panel)
        reference_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        reference_header.columnconfigure(0, weight=1)
        right_title = ttk.Label(
            reference_header, textvariable=self.reference_panel_title, style="Reference.Section.TLabel"
        )
        right_title.grid(row=0, column=0, sticky="ew")
        self.bcca_filter_buttons: dict[str, ttk.Checkbutton] = {}
        self.bcca_filter_variables: dict[str, tk.BooleanVar] = {}
        for column, kind in enumerate(("2", "1", "3"), start=1):
            variable = tk.BooleanVar(value=False)
            self.bcca_filter_variables[kind] = variable
            button = ttk.Checkbutton(
                reference_header, text=CONCEPT_TYPES[kind].split()[0], width=3,
                style="BudgetFilter.TCheckbutton", variable=variable,
                command=lambda selected=kind: self.toggle_bcca_resource_filter(selected),
            )
            button.grid(row=0, column=column, padx=(4, 0))
            self.bcca_filter_buttons[kind] = button
            ToolTip(button, f"Filtrar recursos: {CONCEPT_TYPES[kind]}. Pulsar de nuevo para mostrar todos.")

        self.budget_splitter = ttk.PanedWindow(self.left_panel, orient="vertical")
        self.budget_splitter.grid(row=1, column=0, sticky="nsew")
        tree_frame = ttk.Frame(self.budget_splitter)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.budget_splitter.add(tree_frame, weight=3)

        columns_budget = ("tipo", "texto", "medicion", "importe")
        self.budget_tree = ttk.Treeview(
            tree_frame, columns=(*columns_budget, "capitulo", "partida", "codigo"),
            displaycolumns=columns_budget, show="tree headings",
        )
        for column, heading, width in (
            ("capitulo", "Capítulo", 220),
            ("partida", "Unidad de obra", 260),
            ("codigo", "Código", 130),
        ):
            self.budget_tree.heading(column, text=heading)
            self.budget_tree.column(column, width=width, minwidth=100, stretch=False)
        self.budget_tree.heading("#0", text="Código")
        self.budget_tree.column("#0", width=130, minwidth=100, stretch=False)
        self.budget_tree.heading("tipo", text="Tipo ▾")
        self.budget_tree.column("tipo", width=120, minwidth=95, anchor="w", stretch=False)
        self.budget_tree.heading("texto", text="Texto")
        self.budget_tree.column("texto", width=330, minwidth=200, anchor="w", stretch=True)
        self.budget_tree.heading("medicion", text="Medición")
        self.budget_tree.column("medicion", width=105, minwidth=85, anchor="e", stretch=False)
        self.budget_tree.heading("importe", text="Importe")
        self.budget_tree.column("importe", width=120, minwidth=95, anchor="e", stretch=False)

        self.budget_tree.tag_configure("chapter", background=COLORS["navy"], foreground=COLORS["surface"], font=("Segoe UI", 9, "bold"))
        self.budget_tree.tag_configure("item_even", background=COLORS["surface"], foreground=COLORS["text"])
        self.budget_tree.tag_configure("item_odd", background=COLORS["blue_soft"], foreground=COLORS["text"])
        self.budget_tree.tag_configure(
            "duplicate_review",
            background=COLORS["warning_soft"],
            foreground=COLORS["warning_text"],
            font=("Segoe UI", 9, "bold"),
        )
        self.budget_tree.tag_configure("resource", background=COLORS["background"], foreground=COLORS["muted"])

        self.budget_tree.grid(row=0, column=0, sticky="nsew")
        budget_scroll = ttk.Scrollbar(
            tree_frame, orient="horizontal", command=self.budget_tree.xview,
        )
        budget_scroll.grid(row=1, column=0, sticky="ew")
        self.budget_tree.configure(xscrollcommand=budget_scroll.set)
        self.budget_tree.bind("<<TreeviewSelect>>", self.on_budget_select)
        self.budget_tree.bind("<Double-1>", self.toggle_budget_node)
        self.budget_tree.bind("<ButtonRelease-1>", self.edit_budget_type)
        self.budget_tree.bind("<Configure>", lambda _event: self._close_type_editor())

        details = ttk.Notebook(self.budget_splitter, height=190)
        self.budget_splitter.add(details, weight=1)
        measurement_frame = ttk.Frame(details, padding=4)
        text_frame = ttk.Frame(details, padding=4)
        components_frame = ttk.Frame(details, padding=4)
        details.add(measurement_frame, text="Mediciones")
        details.add(text_frame, text="Texto de la partida")
        details.add(components_frame, text="Costes y rendimientos")
        measurement_frame.columnconfigure(0, weight=1)
        measurement_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        measurement_columns = ("comentario", "unidades", "longitud", "anchura", "altura", "k", "parcial")
        self.measurement_tree = ttk.Treeview(
            measurement_frame, columns=measurement_columns, show="headings", height=5
        )
        for column, title, width in (
            ("comentario", "Descripción", 240),
            ("unidades", "Uds", 70),
            ("longitud", "Longitud", 85),
            ("anchura", "Anchura", 85),
            ("altura", "Altura", 85),
            ("k", "K", 70),
            ("parcial", "Parcial", 95),
        ):
            self.measurement_tree.heading(column, text=title)
            self.measurement_tree.column(column, width=width, anchor="w" if column == "comentario" else "e")
        self.measurement_tree.grid(row=0, column=0, sticky="nsew")
        self.measurement_tree.bind("<ButtonRelease-1>", self.edit_measurement_cell)

        measurement_actions = ttk.Frame(measurement_frame)
        measurement_actions.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(measurement_actions, text="Añadir medición", command=self.add_measurement_line, style="Accent.TButton").pack(side="left")
        ttk.Button(measurement_actions, text="Eliminar medición", command=self.delete_measurement_line, style="Danger.TButton").pack(side="left", padx=(6, 0))
        self.measurement_total_label = ttk.Label(
            measurement_actions, text="Pulsa sobre una celda para editarla.", style="Muted.TLabel"
        )
        self.measurement_total_label.pack(side="right")

        self.description_text = tk.Text(text_frame, wrap="word", height=6, state="disabled")
        self.description_text.grid(row=0, column=0, sticky="nsew")
        self.description_text.bind("<KeyRelease>", self.on_description_edited)

        components_frame.columnconfigure(0, weight=1)
        components_frame.rowconfigure(0, weight=1)
        component_columns = ("codigo", "descripcion", "cantidad", "precio", "coste")
        self.component_tree = ttk.Treeview(
            components_frame, columns=component_columns, show="headings", height=5
        )
        for column, title, width in (
            ("codigo", "Código", 110),
            ("descripcion", "Descripción", 270),
            ("cantidad", "Cantidad", 90),
            ("precio", "Precio unit.", 100),
            ("coste", "Coste", 100),
        ):
            self.component_tree.heading(column, text=title)
            self.component_tree.column(
                column, width=width, anchor="w" if column in {"codigo", "descripcion"} else "e"
            )
        self.component_tree.grid(row=0, column=0, sticky="nsew")
        self.component_tree.bind("<Double-1>", self.edit_component_cell)
        ttk.Label(
            components_frame,
            text="Doble clic en descripción, cantidad o precio unitario para editar el recurso. "
                 "El rendimiento se edita con el botón ↻.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))

        self.budget_fields = {key: tk.StringVar() for _label, key in ITEM_FIELDS}

        search_frame = ttk.Frame(self.left_panel)
        search_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(search_frame, text="Buscar en el proyecto:").grid(row=0, column=0, padx=(0, 6))
        self.budget_search_var = tk.StringVar()
        self.budget_search = ttk.Entry(search_frame, textvariable=self.budget_search_var)
        self.budget_search.grid(row=0, column=1, sticky="ew")
        self.budget_search_var.trace_add("write", self.filter_budget)
        self.budget_search.bind("<Escape>", lambda _event: self.budget_search_var.set(""))

        bcca_columns = ("codigo", "ud", "rend", "precio")
        reference_tree_frame = ttk.Frame(self.right_panel)
        reference_tree_frame.grid(row=1, column=0, sticky="nsew")
        reference_tree_frame.columnconfigure(0, weight=1)
        reference_tree_frame.rowconfigure(0, weight=1)
        self.bcca_tree = ttk.Treeview(
            reference_tree_frame,
            columns=(*bcca_columns, "base", "capitulo", "partida", "texto"),
            displaycolumns=bcca_columns, show="tree headings",
        )
        for column, heading, width in (
            ("base", "Base de referencia", 160), ("capitulo", "Capítulo", 220),
            ("partida", "Unidad de obra", 260), ("texto", "Texto", 300),
        ):
            self.bcca_tree.heading(column, text=heading)
            self.bcca_tree.column(column, width=width, minwidth=100, stretch=column == "texto")
        self.bcca_tree.heading("#0", text="Capítulo / partida")
        self.bcca_tree.column("#0", width=300, minwidth=180, stretch=True)
        self.bcca_tree.heading("codigo", text="Código")
        self.bcca_tree.column("codigo", width=105, minwidth=80, anchor="w", stretch=False)
        self.bcca_tree.heading("ud", text="Ud / cantidad")
        self.bcca_tree.column("ud", width=95, minwidth=70, anchor="e", stretch=False)
        self.bcca_tree.heading("rend", text="Rend. (h)")
        self.bcca_tree.column("rend", width=80, minwidth=65, anchor="e", stretch=False)
        self.bcca_tree.heading("precio", text="Precio")
        self.bcca_tree.column("precio", width=90, minwidth=70, anchor="e", stretch=False)

        self.bcca_tree.tag_configure("bcca_base", background=COLORS["teal"], foreground=COLORS["surface"], font=("Segoe UI", 10, "bold"))
        self.bcca_tree.tag_configure("bcca_chapter", background=COLORS["teal_soft"], foreground=COLORS["teal_text"], font=("Segoe UI", 9, "bold"))
        self.bcca_tree.tag_configure("bcca_item", background=COLORS["surface"], foreground=COLORS["text"])
        self.bcca_tree.tag_configure("bcca_aux", background="#EDE3F7", foreground="#4B2E6B")
        self.bcca_tree.tag_configure("bcca_labour", background="#E3F4E6", foreground="#1E5B2E")
        self.bcca_tree.tag_configure("bcca_machinery", background="#FDF0DC", foreground="#8A5200")
        self.bcca_tree.tag_configure("bcca_material", background="#E6F0FA", foreground="#17456E")

        self.bcca_tree.grid(row=0, column=0, sticky="nsew")
        reference_scroll = ttk.Scrollbar(
            reference_tree_frame, orient="horizontal", command=self.bcca_tree.xview,
        )
        reference_scroll.grid(row=1, column=0, sticky="ew")
        self.bcca_tree.configure(xscrollcommand=reference_scroll.set)
        self.bcca_tree.bind("<<TreeviewSelect>>", self.on_bcca_select)
        self.bcca_tree.bind("<<TreeviewOpen>>", self.load_bcca_node)
        self.bcca_tree.bind("<Double-1>", self.toggle_bcca_node)
        self.bcca_tree.bind("<ButtonPress-1>", self.start_bcca_drag, add="+")
        self.bcca_tree.bind("<B1-Motion>", self.motion_bcca_drag, add="+")
        self.bcca_tree.bind("<ButtonRelease-1>", self.drop_on_budget, add="+")

        self.bcca_search = ttk.Entry(self.right_panel)
        self.bcca_search.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.bcca_search.bind("<KeyRelease>", self.filter_bcca)

        bcca_details = ttk.LabelFrame(self.right_panel, text="Detalle de la selección", padding=4)
        bcca_details.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        bcca_details.columnconfigure(0, weight=1)
        bcca_details.rowconfigure(0, weight=1)
        self.bcca_detail_text = tk.Text(bcca_details, wrap="word", height=8, state="disabled")
        self.bcca_detail_text.grid(row=0, column=0, sticky="nsew")
        localize_widget_tree(self.root)

    def _clear_tree(self, tree: ttk.Treeview):
        """Elimina todos los elementos de un árbol de Tkinter."""
        for item in tree.get_children():
            tree.delete(item)

    def _keep_even_split(self, event=None) -> None:
        """Mantiene la división vertical al 50 % mientras el usuario no la mueva."""
        if (not self._bcca_visible or len(self.splitter.panes()) != 2
                or self._sash_moved_by_user or self._adjusting_sash):
            return
        width = self.splitter.winfo_width()
        if width <= 1:
            return
        target = width // 2
        # Reposicionar dispara otro <Configure>: evitamos la realimentación.
        if abs(self.splitter.sashpos(0) - target) <= 2:
            return
        self._adjusting_sash = True
        try:
            self.splitter.sashpos(0, target)
        finally:
            self._adjusting_sash = False

    def _remember_manual_sash(self, event=None) -> None:
        """Registra que el usuario ha desplazado manualmente el separador de paneles."""
        if event is not None and abs(event.x - self.splitter.sashpos(0)) <= 8:
            self._sash_moved_by_user = True

    def reset_split(self) -> None:
        """Muestra el panel de referencia y restablece la división entre paneles."""
        if not self._bcca_visible:
            self.toggle_bcca_panel()
        self._sash_moved_by_user = False
        self._keep_even_split()
        self._set_status("División restablecida al 50 %.")

    def toggle_bcca_panel(self) -> None:
        """Alterna la visibilidad del panel de bases de referencia."""
        if self._bcca_visible:
            self.splitter.forget(self.right_panel)
            self._bcca_visible = False
            self.bcca_visibility_text.set("◀")
            self._set_status("BCCA oculto; el presupuesto ocupa toda la ventana.")
            return
        self.splitter.add(self.right_panel, weight=1)
        self._bcca_visible = True
        self.bcca_visibility_text.set("▶")
        self._sash_moved_by_user = False
        self.root.after_idle(self._restore_reference_split)
        self._set_status("BCCA mostrado.")

    def _restore_reference_split(self) -> None:
        """Espera la geometría del panel añadido antes de situar su separador."""
        if not self._bcca_visible:
            return
        self.splitter.update_idletasks()
        self._keep_even_split()

    def _populate_bcca_tree(self):
        """Rellena el árbol de referencia con los capítulos de las bases cargadas."""
        self._clear_bcca_tree()
        self.bcca_tree.configure(
            show="tree headings", displaycolumns=("codigo", "ud", "rend", "precio"),
        )
        grouped = len(self.reference_bases) > 1
        for base in self.reference_bases:
            parent = self._insert_base_header(base, open_=False) if grouped else ""
            self._recursive_insert_bcca(base, base.root_codes, parent)

    def _clear_bcca_tree(self) -> None:
        """Vacía el árbol de referencia y reinicia sus índices de elementos."""
        self._clear_tree(self.bcca_tree)
        self.bcca_code_by_iid.clear()
        self.bcca_base_by_iid.clear()
        self._bcca_iid_counter = 0
        self.selected_bcca_code = None
        self.selected_bcca_base = None
        self.bcca_detail_text.configure(state="normal")
        self.bcca_detail_text.delete("1.0", "end")
        self.bcca_detail_text.configure(state="disabled")

    def _insert_base_header(self, base: Bc3Dataset, open_: bool, count: int | None = None) -> str:
        """Añade al árbol el encabezado de una base y devuelve su identificador."""
        iid = self._next_bcca_iid()
        self.bcca_base_by_iid[iid] = base
        label = base.path.stem if count is None else f"{base.path.stem} ({count} resultados)"
        self.bcca_tree.insert("", "end", iid=iid, text=label, open=open_, tags=("bcca_base",))
        return iid

    def _next_bcca_iid(self) -> str:
        """Genera un identificador único para un nodo del árbol de referencia."""
        self._bcca_iid_counter += 1
        return f"bcca:{self._bcca_iid_counter}"

    def _insert_bcca_node(
        self, base: Bc3Dataset, parent: str, code: str, quantity: float | None = None
    ) -> str:
        """Inserta un concepto en el árbol y prepara un marcador si tiene componentes."""
        concept = base.concepts.get(code, Concept(code))
        iid = self._next_bcca_iid()
        self.bcca_code_by_iid[iid] = code
        self.bcca_base_by_iid[iid] = base

        if is_chapter_code(code):
            tag, rendimiento = "bcca_chapter", ""
        elif quantity is None:
            tag = "bcca_item"
            rend = base.calculate_rendimiento(code)
            rendimiento = f"{fmt_number(rend, 4)}" if rend else ""
        else:
            tag, rendimiento = RESOURCE_TAGS.get(concept.ctype, "bcca_material"), ""
            if concept.ctype == "0" and concept.children:
                tag = "bcca_aux"

        amount = f"{fmt_number(quantity, 4)} {concept.unit}".strip() if quantity is not None else concept.unit
        self.bcca_tree.insert(
            parent,
            "end",
            iid=iid,
            text=f"{code} {concept.summary}".strip() if is_chapter_code(code) else concept.summary or code,
            values=(code, amount, rendimiento, fmt_number(concept.price)),
            tags=(tag,),
        )
        if not is_chapter_code(code) and concept.children:
            self.bcca_tree.insert(iid, "end", iid=f"{iid}:placeholder", text="Cargando...")
        return iid

    def _load_bcca_children(self, iid: str) -> None:
        """Carga de forma diferida los componentes de un nodo con marcador pendiente."""
        base = self.bcca_base_by_iid.get(iid)
        if base is None or iid not in self.bcca_code_by_iid:
            return
        children = self.bcca_tree.get_children(iid)
        if not children or not children[0].endswith(":placeholder"):
            return
        self.bcca_tree.delete(children[0])
        code = self.bcca_code_by_iid[iid]
        for child_code, quantity in base.concepts.get(code, Concept(code)).children:
            self._insert_bcca_node(base, iid, child_code, quantity)

    def _recursive_insert_bcca(self, base: Bc3Dataset, codes: list[str], parent: str):
        """Inserta recursivamente capítulos y descendientes en el árbol de referencia."""
        for code in sorted(codes, key=chapter_sort_key):
            iid = self._insert_bcca_node(base, parent, code)
            if is_chapter_code(code):
                children = base.get_children(code)
                if children:
                    self._recursive_insert_bcca(base, children, iid)

    @staticmethod
    def _search_base(base: Bc3Dataset, term: str) -> list[str]:
        """Busca partidas de una base cuyo código o resumen contiene el término."""
        return [
            code
            for code, concept in base.concepts.items()
            if concept.ctype == "0"
            and not is_chapter_code(code)
            and term in f"{code} {concept.summary}".lower()
        ]

    def filter_bcca(self, event=None):
        """Filtra las partidas de las bases cargadas y limita los resultados mostrados."""
        term = (self.bcca_search.get() or "").strip().lower()
        if self.bcca_resource_filter:
            self._filter_bcca_resources(term)
            return
        if not term:
            self._populate_bcca_tree()
            return
        self._clear_bcca_tree()
        self.bcca_tree.configure(
            show="tree headings", displaycolumns=("codigo", "ud", "rend", "precio"),
        )
        grouped = len(self.reference_bases) > 1
        remaining = 500
        for base in self.reference_bases:
            if remaining <= 0:
                break
            codes = self._search_base(base, term)
            if not codes:
                continue
            parent = self._insert_base_header(base, open_=True, count=len(codes)) if grouped else ""
            for code in codes[:remaining]:
                self._insert_bcca_node(base, parent, code)
            remaining -= len(codes)

    def toggle_bcca_resource_filter(self, kind: str) -> None:
        """Alterna el filtro exclusivo de naturaleza de las bases de referencia."""
        self.bcca_resource_filter = "" if self.bcca_resource_filter == kind else kind
        for code, variable in self.bcca_filter_variables.items():
            variable.set(code == self.bcca_resource_filter)
        self.filter_bcca()

    def _filter_bcca_resources(self, term: str) -> None:
        """Muestra recursos con su base, capítulo y unidad de obra a la izquierda."""
        self._clear_bcca_tree()
        self.bcca_tree.configure(
            show="headings",
            displaycolumns=("base", "capitulo", "partida", "codigo", "texto", "ud", "precio"),
        )
        count = 0
        for base in self.reference_bases:
            reached: set[str] = set()

            def visit(
                code: str, chapter: str = "", item: str = "",
                quantity: float | None = None, path: tuple[str, ...] = (),
                inherited_match: bool = False,
            ) -> Iterator[tuple[str, str, str, float | None]]:
                """Recorre cada uso del recurso sin expandir el árbol ni seguir ciclos."""
                if code in path or code not in base.concepts:
                    return
                reached.add(code)
                concept = base.concepts[code]
                matches = inherited_match or not term or term in (
                    f"{code} {concept.summary} {concept.text}".lower()
                )
                if is_chapter_code(code):
                    chapter = f"{format_chapter_code(code)} · {concept.summary}"
                elif concept.ctype == "0" and not item:
                    item = f"{code} · {concept.summary}"
                if concept.ctype == self.bcca_resource_filter and not is_chapter_code(code):
                    if matches:
                        yield code, chapter, item, quantity
                for child_code, child_quantity in concept.children:
                    yield from visit(
                        child_code, chapter, item, child_quantity, (*path, code), matches,
                    )

            referenced = {
                child for concept in base.concepts.values() for child, _ in concept.children
            }
            roots = [code for code in base.concepts if code not in referenced]
            for root in (*base.root_codes, *roots, *base.concepts):
                if root in reached:
                    continue
                for code, chapter, item, quantity in visit(root):
                    if count >= 500:
                        self._set_status("Se muestran los primeros 500 recursos. Acota la búsqueda.")
                        return
                    concept = base.concepts[code]
                    iid = self._next_bcca_iid()
                    self.bcca_code_by_iid[iid] = code
                    self.bcca_base_by_iid[iid] = base
                    amount = (
                        f"{fmt_number(quantity, 4)} {concept.unit}".strip()
                        if quantity is not None else concept.unit
                    )
                    self.bcca_tree.insert(
                        "", "end", iid=iid, text=concept.summary or code,
                        values=(
                            code, amount, "", fmt_number(concept.price), base.path.stem,
                            chapter, item, concept.summary or code,
                        ),
                        tags=(RESOURCE_TAGS[concept.ctype],),
                    )
                    count += 1

    def toggle_bcca_node(self, event=None):
        """Abre o cierra el nodo de referencia bajo el puntero, cargándolo si procede."""
        row = self.bcca_tree.identify_row(event.y) if event else ""
        if not row or row not in self.bcca_base_by_iid:
            return
        self._load_bcca_children(row)
        self.bcca_tree.item(row, open=not bool(self.bcca_tree.item(row, "open")))
        return "break"

    def load_bcca_node(self, event=None):
        """Carga los componentes del nodo que el usuario acaba de desplegar."""
        selection = self.bcca_tree.focus()
        if selection:
            self._load_bcca_children(selection)

    def on_bcca_select(self, event):
        """Actualiza la selección de referencia y presenta los detalles del concepto."""
        selection = self.bcca_tree.selection()
        if not selection:
            return
        code = self.bcca_code_by_iid.get(selection[0])
        base = self.bcca_base_by_iid.get(selection[0])
        if not code or base is None:
            return
        self.selected_bcca_code = code
        self.selected_bcca_base = base
        self._show_bcca_details(base, code)
        self._set_status(f"Seleccionado {base.path.stem}: {code}")

    def _show_bcca_details(self, base: Bc3Dataset, code: str) -> None:
        """Muestra descripción, precio, rendimiento y descomposición del concepto."""
        concept = base.concepts.get(code)
        if concept is None:
            return
        lines = [
            f"Base: {base.path.stem}",
            f"C?digo: {concept.code}",
            f"Unidad: {concept.unit or '-'}",
            f"Precio: {self._format_currency(concept.price)}",
            f"Rendimiento: {fmt_number(base.calculate_rendimiento(code), 4)} h",
            "",
            concept.text or concept.summary or "Sin texto descriptivo.",
        ]
        if concept.children:
            lines.extend(("", "Descomposici?n:"))
            for child_code, quantity in concept.children:
                child = base.concepts.get(child_code, Concept(child_code))
                lines.append(
                    f"  {child_code}: {child.summary or child_code} | "
                    f"{fmt_number(quantity, 4)} {child.unit} ? {self._format_currency(child.price)}"
                )
        self.bcca_detail_text.config(state="normal")
        self.bcca_detail_text.delete("1.0", "end")
        self.bcca_detail_text.insert("1.0", "\n".join(lines))
        self.bcca_detail_text.config(state="disabled")

    def _update_reference_title(self) -> None:
        """Actualiza el título del panel según las bases de referencia cargadas."""
        names = [base.path.stem for base in self.reference_bases]
        if not names:
            self.reference_panel_title.set("Base de referencia: Sin cargar")
        elif len(names) == 1:
            self.reference_panel_title.set(f"Base de referencia: {names[0]}")
        else:
            self.reference_panel_title.set(f"Bases de referencia: {', '.join(names)}")

    def _refresh_reference_views(self) -> None:
        """Sincroniza título, árbol de referencias y presupuesto tras un cambio de bases."""
        self.selected_bcca_code = None
        self.selected_bcca_base = None
        self._update_reference_title()
        if self.reference_bases:
            self.filter_bcca()
        else:
            self._clear_bcca_tree()
        if self.budget_items:
            self.refresh_budget_tree()

    def _load_bcca_file(self, path: Path):
        """Carga una base de referencia y reemplaza otra ya abierta desde la misma ruta."""
        try:
            dataset = Bc3Dataset(path)
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo cargar BCCA: {exc}")
            return
        resolved = path.resolve()
        # Volver a cargar una base ya abierta la sustituye en lugar de duplicarla.
        for index, base in enumerate(self.reference_bases):
            if base.path.resolve() == resolved:
                self.reference_bases[index] = dataset
                break
        else:
            self.reference_bases.append(dataset)
        self._remember_recent_reference_base(path)
        self._refresh_reference_views()
        self._set_status(f"BCCA cargado: {path.name}")

    def remove_reference_base_dialog(self) -> None:
        """Permite seleccionar y retirar una o varias bases de referencia cargadas."""
        if not self.reference_bases:
            messagebox.showinfo("Sin bases", "No hay bases de referencia cargadas.", parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Quitar base de referencia")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        ttk.Label(dialog, text="Selecciona las bases a quitar:", padding=(10, 10, 10, 4)).pack(anchor="w")
        listbox = tk.Listbox(dialog, selectmode="extended", width=60, height=min(10, len(self.reference_bases)))
        for base in self.reference_bases:
            listbox.insert("end", base.path.name)
        listbox.pack(fill="both", expand=True, padx=10)

        def accept() -> None:
            """Retira las bases seleccionadas y refresca las vistas de la aplicación."""
            selected = {self.reference_bases[i] for i in listbox.curselection()}
            dialog.destroy()
            if not selected:
                return
            self.reference_bases = [base for base in self.reference_bases if base not in selected]
            self._refresh_reference_views()
            self._set_status(f"Quitadas {len(selected)} base(s) de referencia.")

        buttons = ttk.Frame(dialog, padding=10)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Quitar", command=accept).pack(side="right")
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="right", padx=(0, 6))
        localize_widget_tree(dialog)
        dialog.grab_set()

    def _reference_base_for(self, item: ProjectItem) -> Bc3Dataset | None:
        """Base de la que procede la partida; en proyectos antiguos se busca por c?digo."""
        for base in self.reference_bases:
            if item.reference_base and base.path.stem == item.reference_base:
                return base
        for base in self.reference_bases:
            if item.code in base.concepts:
                return base
        return self.reference_bases[0] if self.reference_bases else None

    def _load_budget_file(self, path: Path):
        """Carga un BC3 como presupuesto o restaura el proyecto JSON asociado."""
        try:
            ds = Bc3Dataset(path)
            project_path = path.with_suffix(".json")
            if project_path.exists():
                self._load_project_file(project_path, ds, path)
                return
            self._dismiss_project_windows()
            self._dismiss_invoicing()
            self._dismiss_rcds()
            self._dismiss_safety_health()
            self.budget_dataset = ds
            self.source_budget_path = path
            self.budget_items = ds.to_project_items()
            self.selected_budget_index = None
            self.selected_chapter = None
            self.chapter_order = self._chapters_from_items()
            self.chapter_titles = {
                code: concept.summary for code, concept in ds.concepts.items()
                if is_chapter_code(code) and not code.endswith("##")
            }
            self.planning_state = {}
            self.invoicing_state = {}
            self.rcd_state = {}
            self.safety_health_state = {}
            self.report_settings = ReportSettings.from_dict(self.default_report_settings.to_dict())
            for item in self.budget_items:
                item.set_indirect_costs(self.report_settings.indirect_costs)
            self.project_title = path.stem
            self.project_path = project_path
            self.project_panel_title.set(f"Proyecto: {self._project_title()}")
            self._collapse_chapters_on_refresh = True
            self.refresh_budget_tree()
            self._reset_history(saved=False)
            if not self._write_project_file(self.project_path):
                return
            self._set_status(f"Presupuesto cargado: {path.name}")
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo cargar el presupuesto: {exc}")

    def new_project(self):
        """Crea un presupuesto vacío después de solicitar el nombre del proyecto."""
        project_title = simpledialog.askstring(
            "Nuevo proyecto",
            "Nombre del proyecto:",
            initialvalue="Nuevo proyecto",
            parent=self.root,
        )
        if project_title is None:
            return
        if not self._confirm_project_change():
            return
        self._dismiss_project_windows()
        self._dismiss_invoicing()
        self._dismiss_rcds()
        self._dismiss_safety_health()
        self.budget_dataset = None
        self.budget_items = []
        self.selected_budget_index = None
        self.selected_chapter = None
        self.chapter_order = []
        self.chapter_titles.clear()
        self.planning_state = {}
        self.invoicing_state = {}
        self.rcd_state = {}
        self.safety_health_state = {}
        self.report_settings = ReportSettings.from_dict(self.default_report_settings.to_dict())
        self.project_title = project_title.strip() or "Nuevo proyecto"
        self.project_path = None
        self.source_budget_path = None
        self.is_dirty = False
        self.project_panel_title.set(f"Proyecto: {self._project_title()}")
        self._collapse_chapters_on_refresh = True
        self.refresh_budget_tree()
        self._show_item_details(None)
        self._reset_history()
        self._set_status("Nuevo proyecto vacío creado.")

    def _chapters_from_items(self) -> list[str]:
        """Devuelve los capítulos del presupuesto en el orden de su primera aparición."""
        chapters: list[str] = []
        for item in self.budget_items:
            if item.chapter not in chapters:
                chapters.append(item.chapter)
        return chapters

    def _ensure_chapter(self, chapter: str) -> None:
        """Añade el capítulo al orden del proyecto si todavía no está registrado."""
        if chapter not in self.chapter_order:
            self.chapter_order.append(chapter)

    def add_chapter(self) -> None:
        """Solicita código y descripción opcional para incorporar un capítulo vacío."""
        code = simpledialog.askstring(
            "Añadir capítulo",
            "Código del capítulo:",
            parent=self.root,
        )
        if code is None:
            return
        code = code.strip()
        if not code:
            messagebox.showwarning("Código requerido", "Indica un código para el capítulo.")
            return
        if not code.endswith("#"):
            code += "#"
        if code in self.chapter_order:
            messagebox.showwarning("Capítulo existente", f"El capítulo {format_chapter_code(code)} ya existe.")
            return
        title = simpledialog.askstring(
            "Añadir capítulo",
            "Descripción del capítulo (opcional):",
            parent=self.root,
        )
        self.chapter_order.append(code)
        if title and title.strip():
            self.chapter_titles[code] = title.strip()
        self.selected_budget_index = None
        self.selected_chapter = code
        self.refresh_budget_tree()
        chapter_iid = self.chapter_iid_by_code[code]
        self.budget_tree.selection_set(chapter_iid)
        self.budget_tree.see(chapter_iid)
        self._mark_dirty()
        self._set_status(f"Añadido el capítulo {format_chapter_code(code)}.")

    def show_add_menu(self) -> None:
        """Muestra las opciones de alta junto al botón Añadir."""
        self.add_menu.tk_popup(
            self.add_button.winfo_rootx(),
            self.add_button.winfo_rooty() + self.add_button.winfo_height(),
        )

    def add_manual_item(self) -> None:
        """Solicita los datos de una partida y la añade al capítulo seleccionado."""
        chapter = self.selected_chapter
        if chapter is None and self.selected_budget_index is not None:
            chapter = self.budget_items[self.selected_budget_index].chapter
        if chapter is None and self.chapter_order:
            chapter = self.chapter_order[-1]
        if chapter is None:
            messagebox.showwarning(
                "Sin capítulo",
                "Crea o selecciona un capítulo antes de añadir una partida.",
                parent=self.root,
            )
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Añadir partida")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)
        values = {
            "chapter_var": tk.StringVar(value=chapter),
            "code_var": tk.StringVar(),
            "unit_var": tk.StringVar(value="ud"),
            "summary_var": tk.StringVar(),
            "measurement_var": tk.StringVar(value="1"),
            "price_var": tk.StringVar(value="0"),
            "rend_var": tk.StringVar(value="0"),
        }
        for row, (label, key) in enumerate(ITEM_FIELDS):
            if key == "price_var" and self.report_settings.indirect_costs:
                label = "Precio directo"
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(form, textvariable=values[key], width=50).grid(
                row=row, column=1, sticky="ew", pady=3
            )

        def accept(_event=None):
            """Valida la partida e inserta el nuevo elemento en el presupuesto."""
            code = values["code_var"].get().strip()
            if not code:
                messagebox.showerror("Código requerido", "Indica un código para la partida.", parent=dialog)
                return
            raw_numbers = (
                values["measurement_var"].get(),
                values["price_var"].get(),
                values["rend_var"].get(),
            )
            measurement, price, rendimiento = (
                parse_float(value, float("nan")) for value in raw_numbers
            )
            if not all(isfinite(value) for value in (measurement, price, rendimiento)):
                messagebox.showerror(
                    "Dato inválido",
                    "Comprueba que medición, precio y rendimiento son números válidos.",
                    parent=dialog,
                )
                return
            target_chapter = values["chapter_var"].get().strip()
            if not target_chapter:
                messagebox.showerror("Capítulo requerido", "Indica el capítulo de la partida.", parent=dialog)
                return
            item = ProjectItem(
                code=code,
                chapter=target_chapter,
                unit=values["unit_var"].get().strip(),
                summary=values["summary_var"].get().strip(),
                measurement=measurement,
                unit_price=price,
                rendimiento=rendimiento,
            )
            dialog.destroy()
            self._insert_item(item, self._end_of_chapter(target_chapter), target_chapter)
            self._mark_dirty()

        buttons = ttk.Frame(form)
        buttons.grid(row=len(ITEM_FIELDS), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Añadir", command=accept).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        localize_widget_tree(dialog)
        dialog.grab_set()

    def delete_selected_budget_entry(self) -> None:
        """Elimina, previa confirmación, el capítulo o la partida seleccionados."""
        if self.selected_chapter is not None:
            chapter = self.selected_chapter
            item_count = sum(item.chapter == chapter for item in self.budget_items)
            if not messagebox.askyesno(
                "Eliminar capítulo",
                (
                    f"¿Eliminar el capítulo {format_chapter_code(chapter)} "
                    f"y sus {item_count} partidas?"
                ),
                parent=self.root,
            ):
                return
            self.budget_items = [
                item for item in self.budget_items if item.chapter != chapter
            ]
            self.chapter_order.remove(chapter)
            self.chapter_titles.pop(chapter, None)
            self.selected_chapter = None
            self.planning_state = {}
            self.refresh_budget_tree()
            self._show_item_details(None)
            self._mark_dirty()
            self._set_status(f"Eliminado el capítulo {format_chapter_code(chapter)}.")
            return

        if self.selected_budget_index is None:
            messagebox.showwarning(
                "Sin selección",
                "Selecciona un capítulo o una partida del presupuesto para eliminarlo.",
                parent=self.root,
            )
            return
        item = self.budget_items[self.selected_budget_index]
        if not messagebox.askyesno(
            "Eliminar partida",
            f"¿Eliminar la partida {item.code} - {item.summary}?",
            parent=self.root,
        ):
            return
        del self.budget_items[self.selected_budget_index]
        self.selected_budget_index = None
        self.planning_state = {}
        self.refresh_budget_tree()
        self._show_item_details(None)
        self._mark_dirty()
        self._set_status(f"Eliminada la partida {item.code}.")

    def _format_currency(self, value: float) -> str:
        """Formatea un importe según la divisa guardada para el proyecto."""
        return format_currency(
            value,
            self.report_settings.currency,
            self.report_settings.custom_currency,
        )

    def _currency_label(self) -> str:
        """Devuelve el símbolo o código de la divisa configurada."""
        return currency_label(
            self.report_settings.currency,
            self.report_settings.custom_currency,
        )

    def duplicate_selected_budget_item(self) -> None:
        """Duplica la partida seleccionada y la marca para su revisión manual."""
        if self.selected_budget_index is None:
            messagebox.showwarning(
                "Sin selección",
                "Selecciona una partida del presupuesto para duplicarla.",
                parent=self.root,
            )
            return

        source = self.budget_items[self.selected_budget_index]
        duplicate = deepcopy(source)
        existing_codes = {item.code for item in self.budget_items}
        duplicate.code = f"{source.code}{DUPLICATE_ITEM_MARKER}"
        while duplicate.code in existing_codes:
            duplicate.code += DUPLICATE_ITEM_MARKER
        duplicate.planned_amounts = {}
        duplicate.certifications = {}
        duplicate.certification_plans = {}
        duplicate.certification_justifications = {}

        code = duplicate.code
        self._insert_item(duplicate, self.selected_budget_index + 1, source.chapter)
        self._mark_dirty()
        messagebox.showwarning(
            "Revisar partida duplicada",
            (
                f"La copia se ha creado con el código {code}. Modifícala y "
                f"revísala manualmente; elimina {DUPLICATE_ITEM_MARKER} cuando hayas terminado."
            ),
            parent=self.root,
        )

    def load_budget_dialog(self):
        """Abre el selector de archivos para importar un presupuesto BC3."""
        path = filedialog.askopenfilename(title="Selecciona un presupuesto .bc3", filetypes=[("BC3", "*.bc3")])
        if path and self._confirm_project_change():
            self._load_budget_file(Path(path))

    def load_bcca_dialog(self):
        """Abre el selector para cargar una o varias bases de referencia BCCA."""
        paths = filedialog.askopenfilenames(
            title="Selecciona una o varias bases de precios .bc3", filetypes=[("BC3", "*.bc3")]
        )
        for path in paths:
            self._load_bcca_file(Path(path))

    def _build_item_from_bcca(self, code: str, base: Bc3Dataset | None) -> ProjectItem | None:
        """Crea una partida del presupuesto a partir del concepto seleccionado en BCCA."""
        if base is None:
            return None
        concept = base.concepts.get(code)
        if not concept:
            return None
        if is_chapter_code(code) or concept.ctype != "0":
            messagebox.showwarning(
                "Selección no válida",
                "Arrastra una partida del BCCA, no un capítulo ni un recurso descompuesto.",
            )
            return None
        return ProjectItem(
            code=code,
            chapter=base.find_parent_chapter(code),
            unit=concept.unit,
            summary=concept.summary.upper(),
            measurement=1.0,
            unit_price=concept.price,
            rendimiento=base.calculate_rendimiento(code),
            source="BCCA",
            reference_base=base.path.stem,
            text=concept.text.upper(),
            decomposition=list(concept.children),
            measurement_lines=[MeasurementLine(comment="Medición inicial", units=1.0)],
        )

    def _insert_bcca_chapter(self, code: str, base: Bc3Dataset) -> None:
        """Incorpora un capítulo de referencia y todas sus partidas al presupuesto."""
        concept = base.concepts.get(code)
        if concept is None or not is_chapter_code(code):
            messagebox.showwarning(
                "Selección no válida",
                "Selecciona un capítulo de la base de referencia.",
                parent=self.root,
            )
            return

        items: list[ProjectItem] = []
        visited_chapters: set[str] = set()

        def collect(chapter_code: str) -> None:
            """Recorre subcapítulos y recopila las partidas presupuestables."""
            if chapter_code in visited_chapters:
                return
            visited_chapters.add(chapter_code)
            for child_code in base.get_children(chapter_code):
                child = base.concepts.get(child_code)
                if child is None:
                    continue
                if is_chapter_code(child_code):
                    collect(child_code)
                    continue
                if child.ctype != "0":
                    continue
                item = self._build_item_from_bcca(child_code, base)
                if item is None:
                    continue
                item.chapter = code
                measured = base.measurements.get((norm_code(chapter_code), child_code))
                if measured is not None:
                    item.measurement = measured.total
                    item.measurement_detail = measured.detail
                    item.measurement_lines = parse_measurement_lines(measured.detail)
                items.append(item)

        collect(code)
        self._ensure_chapter(code)
        if concept.summary:
            self.chapter_titles.setdefault(code, concept.summary)
        position = self._end_of_chapter(code)
        for item in items:
            item.recalculate_measurement()
            item.set_indirect_costs(self.report_settings.indirect_costs)
        self.budget_items[position:position] = items
        self.selected_chapter = None if items else code
        self.selected_budget_index = position if items else None
        self.refresh_budget_tree()
        selected_iid = f"item:{position}" if items else self.chapter_iid_by_code[code]
        self.budget_tree.selection_set(selected_iid)
        self.budget_tree.see(selected_iid)
        self._mark_dirty()
        self._set_status(
            f"Capítulo {format_chapter_code(code)} añadido con {len(items)} partidas."
        )

    def _insert_item(self, item: ProjectItem, position: int | None = None, chapter: str | None = None) -> None:
        """Inserta una partida en el capítulo y posición indicados y actualiza la vista."""
        item.summary = item.summary.upper()
        item.text = item.text.upper()
        if chapter:
            item.chapter = chapter
        self._ensure_chapter(item.chapter)
        item.recalculate_measurement()
        item.set_indirect_costs(self.report_settings.indirect_costs)
        index = len(self.budget_items) if position is None else max(0, min(position, len(self.budget_items)))
        self.budget_items.insert(index, item)
        self.refresh_budget_tree()
        self.selected_budget_index = index
        self.selected_chapter = None
        iid = f"item:{index}"
        if self.budget_tree.exists(iid):
            self.budget_tree.selection_set(iid)
            self.budget_tree.see(iid)
        self._set_status(f"Partida añadida a {item.chapter}: {item.code} - {item.summary}")
        self._mark_dirty()

    def add_selected_bcca_to_budget(self):
        """Añade al presupuesto la partida o capítulo de referencia seleccionado."""
        if not self.selected_bcca_code or self.selected_bcca_base is None:
            messagebox.showwarning(
                "Sin selección",
                "Primero selecciona una partida o capítulo del BCCA.",
                parent=self.root,
            )
            return
        if is_chapter_code(self.selected_bcca_code):
            self._insert_bcca_chapter(self.selected_bcca_code, self.selected_bcca_base)
            return
        concept = self.selected_bcca_base.concepts.get(self.selected_bcca_code)
        if concept is not None and concept.ctype in {"1", "2", "3"}:
            self._add_basic_to_item(concept, self.selected_budget_index)
            return
        item = self._build_item_from_bcca(self.selected_bcca_code, self.selected_bcca_base)
        if item is None:
            return
        chapter = self._default_target_chapter(item.chapter)
        self._insert_item(item, self._end_of_chapter(chapter), chapter)

    def _add_basic_to_item(self, concept: Concept, index: int | None) -> None:
        """Copia un precio básico al descompuesto sin modificar su base original."""
        if index is None:
            messagebox.showwarning(
                "Sin selección", "Selecciona una partida de destino en el presupuesto.",
                parent=self.root,
            )
            return
        item = self.budget_items[index]
        if any(code == concept.code for code, _quantity in item.decomposition):
            messagebox.showwarning(
                "Recurso existente",
                "La partida ya contiene este código. Edita su cantidad en el descompuesto.",
                parent=self.root,
            )
            return
        quantity = simpledialog.askfloat(
            "Cantidad", f"Cantidad de {concept.code} por {item.unit or 'unidad de obra'}:",
            initialvalue=1.0, minvalue=0.0, parent=self.root,
        )
        if quantity is None:
            return
        if not isfinite(quantity) or quantity <= 0 or not isfinite(concept.price):
            messagebox.showerror(
                "Dato inválido", "La cantidad debe ser positiva y el precio debe ser finito.",
                parent=self.root,
            )
            return
        candidate = deepcopy(item)
        candidate.decomposition.append((concept.code, quantity))
        candidate.component_concepts[concept.code] = deepcopy(concept)
        candidate.component_prices[concept.code] = concept.price
        candidate.component_types[concept.code] = concept.ctype
        try:
            self._recalculate_component_cost(candidate)
            candidate.cost_components(self._component_dataset(candidate))
        except ValueError as exc:
            messagebox.showerror("No se pudo añadir el recurso", str(exc), parent=self.root)
            return
        item.decomposition = candidate.decomposition
        item.component_concepts = candidate.component_concepts
        item.component_prices = candidate.component_prices
        item.component_types = candidate.component_types
        item.unit_price = candidate.unit_price
        item.direct_unit_price = candidate.direct_unit_price
        item.indirect_costs = candidate.indirect_costs
        item.rendimiento = sum(
            amount for code, amount in item.decomposition
            if self._budget_concept_type(item, code) == "1"
        )
        self.selected_budget_index = index
        self.selected_chapter = None
        self.budget_fields["price_var"].set(
            f"{item.unit_price if item.direct_unit_price is None else item.direct_unit_price:.2f}"
        )
        self._show_item_details(item)
        self.refresh_budget_tree()
        self._mark_dirty()
        self._set_status(f"Recurso {concept.code} añadido a la partida {item.code}.")

    def _default_target_chapter(self, fallback: str) -> str:
        """Elige el capítulo de destino usando la selección actual o el capítulo alternativo."""
        if self.selected_budget_index is not None:
            return self.budget_items[self.selected_budget_index].chapter
        if self.budget_items:
            return self.budget_items[-1].chapter
        return fallback

    def _end_of_chapter(self, chapter: str) -> int:
        """Calcula el índice inmediatamente posterior a las partidas de un capítulo."""
        positions = [i for i, entry in enumerate(self.budget_items) if entry.chapter == chapter]
        return positions[-1] + 1 if positions else len(self.budget_items)

    def start_bcca_drag(self, event):
        """Inicia el arrastre de una partida de referencia bajo el puntero."""
        row = self.bcca_tree.identify_row(event.y)
        self._drag_code = self.bcca_code_by_iid.get(row) if row else None
        self._drag_base = self.bcca_base_by_iid.get(row) if row else None
        self._dragging = False

    def motion_bcca_drag(self, event):
        """Actualiza el cursor durante el arrastre de una partida de referencia."""
        if not self._drag_code:
            return
        if not self._dragging:
            self._dragging = True
            self.bcca_tree.config(cursor="hand2")
        target = self._budget_drop_target(event)
        if target is None:
            self._set_status("Suelta sobre un capítulo o partida del presupuesto.")
        else:
            chapter, _ = target
            self._set_status(f"Soltar en {chapter}")

    def drop_on_budget(self, event):
        """Finaliza el arrastre e inserta una partida o capítulo de referencia."""
        code, base, dragging = self._drag_code, self._drag_base, self._dragging
        self._drag_code, self._drag_base, self._dragging = None, None, False
        self.bcca_tree.config(cursor="")
        if not code or not dragging:
            return
        concept = base.concepts.get(code) if base is not None else None
        if concept is not None and concept.ctype in {"1", "2", "3"}:
            x = event.x_root - self.budget_tree.winfo_rootx()
            y = event.y_root - self.budget_tree.winfo_rooty()
            if not (0 <= x <= self.budget_tree.winfo_width() and 0 <= y <= self.budget_tree.winfo_height()):
                return
            row = self.budget_tree.identify_row(y)
            index = self.budget_index_by_iid.get(row)
            if row.startswith("resource:"):
                index = int(row.split(":")[1])
            self._add_basic_to_item(concept, index)
            return
        target = self._budget_drop_target(event)
        if is_chapter_code(code):
            x = event.x_root - self.budget_tree.winfo_rootx()
            y = event.y_root - self.budget_tree.winfo_rooty()
            inside_budget = (
                0 <= x <= self.budget_tree.winfo_width()
                and 0 <= y <= self.budget_tree.winfo_height()
            )
            if base is not None and (target is not None or (not self.budget_items and inside_budget)):
                self._insert_bcca_chapter(code, base)
            return
        if target is None:
            return
        chapter, position = target
        item = self._build_item_from_bcca(code, base)
        if item is not None:
            self._insert_item(item, position, chapter)

    def _budget_drop_target(self, event) -> tuple[str, int] | None:
        """Capítulo y posición de inserción bajo el cursor, en coordenadas del presupuesto."""
        x = event.x_root - self.budget_tree.winfo_rootx()
        y = event.y_root - self.budget_tree.winfo_rooty()
        if not (0 <= x <= self.budget_tree.winfo_width() and 0 <= y <= self.budget_tree.winfo_height()):
            return None

        row = self.budget_tree.identify_row(y)
        if row.startswith("chapter:"):
            chapter = next(
                (code for code, chapter_iid in self.chapter_iid_by_code.items() if chapter_iid == row),
                None,
            )
            if chapter is None:
                return None
            return chapter, self._end_of_chapter(chapter)

        index = self.budget_index_by_iid.get(row)
        if index is None and row.startswith("resource:"):
            index = int(row.split(":")[1])
        if index is not None:
            return self.budget_items[index].chapter, index + 1

        if self.budget_items:
            chapter = self.budget_items[-1].chapter
            return chapter, len(self.budget_items)
        return None

    def _chapter_rows(self) -> dict[str, ProjectItem]:
        """Asocia cada capítulo con la primera partida que contiene, si existe."""
        rows: dict[str, ProjectItem] = {}
        seen: set[str] = set()
        for item in self.budget_items:
            if item.chapter not in seen:
                rows[self.chapter_iid_by_code.get(item.chapter, "")] = item
                seen.add(item.chapter)
        return rows

    def _select_budget_item(self, item: ProjectItem) -> None:
        """Selecciona una partida por identidad y actualiza su posición en el árbol."""
        index = next(index for index, candidate in enumerate(self.budget_items) if candidate is item)
        self.selected_budget_index = index
        self.selected_chapter = None
        self.refresh_budget_tree()
        iid = f"item:{index}"
        self.budget_tree.selection_set(iid)
        self.budget_tree.see(iid)

    def move_selected_budget_entry(self, direction: int) -> None:
        """Desplaza el recurso, capítulo o partida seleccionados en la dirección solicitada."""
        components = self.component_tree.selection()
        if components:
            if components[0].startswith("indirect:"):
                self._set_status("Las líneas calculadas no se pueden mover.")
                return
            if self.selected_budget_index is not None:
                self._move_component(self.selected_budget_index, int(components[0]), direction)
                return
        selection = self.budget_tree.selection()
        if selection and selection[0].startswith("resource:"):
            _, index, component = selection[0].split(":")
            self._move_component(int(index), int(component), direction)
            return
        if selection and selection[0].startswith("indirect:"):
            self._set_status("Las líneas calculadas no se pueden mover.")
            return
        if self.selected_chapter is not None:
            self._move_chapter(self.selected_chapter, direction)
            return
        if self.selected_budget_index is None:
            messagebox.showwarning("Sin selección", "Selecciona un capítulo o una partida del presupuesto.")
            return
        self._move_item(self.budget_items[self.selected_budget_index], direction)

    def _move_component(self, index: int, component: int, direction: int) -> None:
        """Reordena un descompuesto y recalcula sus porcentajes dependientes del orden."""
        item = self.budget_items[index]
        target = component + direction
        if not 0 <= target < len(item.decomposition):
            self._set_status("El recurso ya está en el extremo del descompuesto.")
            return
        candidate = deepcopy(item)
        candidate.decomposition[component], candidate.decomposition[target] = (
            candidate.decomposition[target], candidate.decomposition[component],
        )

        def direct_cost(entry: ProjectItem) -> Decimal:
            """Compara costes sin ajustes para conservar el precio si solo cambia el orden."""
            return sum((
                (Decimal(str(quantity)) * Decimal(str(price))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP,
                )
                for _, quantity, price in entry.cost_components(
                    self._component_dataset(entry), reconcile=False,
                )
            ), Decimal(0))

        try:
            if direct_cost(candidate) != direct_cost(item):
                self._recalculate_component_cost(candidate)
        except ValueError as exc:
            messagebox.showerror("No se pudo mover el recurso", str(exc), parent=self.root)
            return
        item.decomposition = candidate.decomposition
        item.unit_price = candidate.unit_price
        item.direct_unit_price = candidate.direct_unit_price
        item.indirect_costs = candidate.indirect_costs
        self.selected_budget_index = index
        self.selected_chapter = None
        self._show_item_details(item)
        self.budget_fields["price_var"].set(
            f"{item.unit_price if item.direct_unit_price is None else item.direct_unit_price:.2f}"
        )
        self.refresh_budget_tree()
        row = f"resource:{index}:{target}"
        self.budget_tree.selection_set(row)
        self.budget_tree.see(row)
        self.on_budget_select(None)
        self.component_tree.selection_set(str(target))
        self.component_tree.focus(str(target))
        self._mark_dirty()
        self._set_status(f"Movido el recurso {item.decomposition[target][0]} en {item.code}.")

    def _move_chapter(self, chapter: str, direction: int) -> None:
        """Reordena un capítulo y agrupa de nuevo sus partidas según el nuevo orden."""
        chapter_index = self.chapter_order.index(chapter)
        target_index = chapter_index + direction
        if not 0 <= target_index < len(self.chapter_order):
            self._set_status("El capítulo ya está en el extremo de la lista.")
            return
        self.chapter_order[chapter_index], self.chapter_order[target_index] = (
            self.chapter_order[target_index],
            self.chapter_order[chapter_index],
        )
        self.budget_items = [
            item
            for chapter_code in self.chapter_order
            for item in self.budget_items
            if item.chapter == chapter_code
        ]
        self.selected_budget_index = None
        self.selected_chapter = chapter
        self.refresh_budget_tree()
        iid = self.chapter_iid_by_code[chapter]
        self.budget_tree.selection_set(iid)
        self.budget_tree.see(iid)
        self._mark_dirty()
        self._set_status(f"Movido el capítulo {format_chapter_code(chapter)}.")

    def _move_item(self, item: ProjectItem, direction: int) -> None:
        """Mueve una partida dentro del capítulo o la pasa al capítulo contiguo."""
        index = next(index for index, candidate in enumerate(self.budget_items) if candidate is item)
        chapter = item.chapter
        chapter_items = [index for index, candidate in enumerate(self.budget_items) if candidate.chapter == chapter]
        chapter_position = chapter_items.index(index)

        if direction < 0 and chapter_position > 0:
            target_index = chapter_items[chapter_position - 1]
            self.budget_items[index], self.budget_items[target_index] = (
                self.budget_items[target_index],
                self.budget_items[index],
            )
        elif direction > 0 and chapter_position < len(chapter_items) - 1:
            target_index = chapter_items[chapter_position + 1]
            self.budget_items[index], self.budget_items[target_index] = (
                self.budget_items[target_index],
                self.budget_items[index],
            )
        else:
            chapter_index = self.chapter_order.index(chapter)
            target_chapter_index = chapter_index + direction
            if not 0 <= target_chapter_index < len(self.chapter_order):
                self._set_status("La partida ya está en el extremo del presupuesto.")
                return
            self.budget_items.pop(index)
            target_chapter = self.chapter_order[target_chapter_index]
            item.chapter = target_chapter
            items_by_chapter = {
                chapter_code: [
                    candidate for candidate in self.budget_items if candidate.chapter == chapter_code
                ]
                for chapter_code in self.chapter_order
            }
            if direction < 0:
                items_by_chapter[target_chapter].append(item)
            else:
                items_by_chapter[target_chapter].insert(0, item)
            self.budget_items = [
                candidate
                for chapter_code in self.chapter_order
                for candidate in items_by_chapter[chapter_code]
            ]
        self._select_budget_item(item)
        self._mark_dirty()
        self._set_status(f"Movida la partida {item.code}.")

    def update_selected_budget_item(self):
        """Edita el nombre del capítulo o los campos de la partida seleccionada."""
        if self.selected_chapter is not None:
            self.rename_selected_chapter()
            return
        if self.selected_budget_index is None:
            messagebox.showwarning("Sin selección", "Selecciona una partida del presupuesto para actualizarla.")
            return
        self.on_budget_select(None)
        dialog = tk.Toplevel(self.root)
        dialog.title("Editar partida")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)
        for row, (label, key) in enumerate(ITEM_FIELDS):
            if key == "price_var" and self.report_settings.indirect_costs:
                label = "Precio directo"
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            ttk.Entry(form, textvariable=self.budget_fields[key], width=50).grid(
                row=row, column=1, sticky="ew", pady=3
            )

        def accept(_event=None):
            """Aplica los campos del formulario y cierra el diálogo si son válidos."""
            if self._apply_budget_fields():
                dialog.destroy()

        buttons = ttk.Frame(form)
        buttons.grid(row=len(ITEM_FIELDS), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancelar", command=dialog.destroy).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Guardar", command=accept).pack(side="left")
        dialog.bind("<Return>", accept)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        localize_widget_tree(dialog)
        dialog.grab_set()

    def rename_selected_chapter(self) -> None:
        """Solicita y guarda un nuevo código y nombre para el capítulo seleccionado."""
        chapter = self.selected_chapter
        if chapter is None:
            messagebox.showwarning(
                "Sin selección",
                "Selecciona un capítulo del presupuesto para cambiar su nombre.",
                parent=self.root,
            )
            return
        code = simpledialog.askstring(
            "Editar capítulo",
            "Código del capítulo:",
            initialvalue=chapter,
            parent=self.root,
        )
        if code is None:
            return
        code = code.strip()
        if not code:
            messagebox.showwarning(
                "Código requerido",
                "Indica un código para el capítulo.",
                parent=self.root,
            )
            return
        if not code.endswith("#"):
            code += "#"
        existing_chapters = (
            set(self.chapter_order)
            | set(self.chapter_titles)
            | {item.chapter for item in self.budget_items}
        )
        if code != chapter and code in existing_chapters:
            messagebox.showwarning(
                "Capítulo existente",
                f"El capítulo {format_chapter_code(code)} ya existe.",
                parent=self.root,
            )
            return
        initial_title = self._chapter_title(chapter)
        if chapter not in self.chapter_titles and initial_title == format_chapter_code(chapter):
            initial_title = format_chapter_code(code)
        title = simpledialog.askstring(
            "Editar capítulo",
            "Nombre del capítulo:",
            initialvalue=initial_title,
            parent=self.root,
        )
        if title is None:
            return
        title = title.strip()
        if not title:
            messagebox.showwarning(
                "Nombre requerido",
                "Indica un nombre para el capítulo.",
                parent=self.root,
            )
            return
        if chapter in self.chapter_order:
            position = self.chapter_order.index(chapter)
            self.chapter_order[position] = code
        else:
            self.chapter_order.append(code)
        self.chapter_titles.pop(chapter, None)
        self.chapter_titles[code] = title
        for item in self.budget_items:
            if item.chapter == chapter:
                item.chapter = code
        self.refresh_budget_tree()
        self.selected_budget_index = None
        self.selected_chapter = code
        chapter_iid = self.chapter_iid_by_code[code]
        self.budget_tree.selection_set(chapter_iid)
        self.budget_tree.see(chapter_iid)
        self._mark_dirty()
        self._set_status(
            f"Actualizado el capítulo {format_chapter_code(code)}."
        )

    def _apply_budget_fields(self) -> bool:
        """Valida y aplica los campos editables de la partida actualmente seleccionada."""
        if self.selected_budget_index is None:
            return False
        item = self.budget_items[self.selected_budget_index]
        raw_numbers = (
            self.budget_fields["measurement_var"].get(),
            self.budget_fields["price_var"].get(),
            self.budget_fields["rend_var"].get(),
        )
        parsed_numbers = tuple(parse_float(value, float("nan")) for value in raw_numbers)
        if not all(isfinite(number) for number in parsed_numbers):
            messagebox.showerror("Dato inválido", "Comprueba que medición, precio y rendimiento son números válidos.")
            return False
        item.chapter = self.budget_fields["chapter_var"].get().strip() or item.chapter
        self._ensure_chapter(item.chapter)
        item.code = self.budget_fields["code_var"].get().strip() or item.code
        item.unit = self.budget_fields["unit_var"].get().strip() or item.unit
        item.summary = self.budget_fields["summary_var"].get().strip() or item.summary
        item.measurement, price, item.rendimiento = parsed_numbers
        item.set_indirect_costs(self.report_settings.indirect_costs, price)
        if item.measurement_lines:
            item.recalculate_measurement()
            note = " (medición recalculada desde su desglose)"
        else:
            note = ""
        self.refresh_budget_tree()
        self._show_item_details(item)
        self._mark_dirty()
        self._set_status(f"Actualizada la partida {item.code}{note}")
        return True

    def on_budget_select(self, event):
        """Sincroniza la selección del árbol con los campos y detalles del presupuesto."""
        selection = self.budget_tree.selection()
        if not selection:
            return
        selected_iid = selection[0]
        if selected_iid.startswith("chapter:"):
            self.selected_budget_index = None
            self.selected_chapter = next(
                (code for code, chapter_iid in self.chapter_iid_by_code.items() if chapter_iid == selected_iid),
                None,
            )
            self._show_item_details(None)
            return
        index = self.budget_index_by_iid.get(selected_iid)
        if index is None and selected_iid.startswith("resource:"):
            index = int(selected_iid.split(":")[1])
        if index is None:
            return
        self.selected_budget_index = index
        self.selected_chapter = None
        item = self.budget_items[index]
        self.budget_fields["chapter_var"].set(item.chapter)
        self.budget_fields["code_var"].set(item.code)
        self.budget_fields["unit_var"].set(item.unit)
        self.budget_fields["summary_var"].set(item.summary)
        self.budget_fields["measurement_var"].set(f"{item.measurement:.4f}")
        direct_price = item.unit_price if item.direct_unit_price is None else item.direct_unit_price
        self.budget_fields["price_var"].set(f"{direct_price:.2f}")
        self.budget_fields["rend_var"].set(f"{item.rendimiento:.4f}")
        self._show_item_details(item)

    def refresh_budget_tree(self):
        """Reconstruye el árbol del presupuesto, sus capítulos, recursos y totales."""
        self._refresh_tool_menu()
        self._close_type_editor()
        self._restore_budget_rows()
        for row in self.budget_tree.get_children():
            self.budget_tree.delete(row)
        self.budget_index_by_iid.clear()
        self.chapter_iid_by_code = {}

        chapter_totals: dict[str, float] = {}
        for item in self.budget_items:
            chapter_totals[item.chapter] = chapter_totals.get(item.chapter, 0.0) + item.import_total

        total = 0.0
        items_per_chapter: dict[str, int] = {}
        for chapter in self.chapter_order:
            chapter_iid = f"chapter:{len(self.chapter_iid_by_code)}"
            self.chapter_iid_by_code[chapter] = chapter_iid
            self.budget_tree.insert(
                "",
                "end",
                iid=chapter_iid,
                text=format_chapter_code(chapter),
                values=(CONCEPT_TYPES["chapter"], self._chapter_title(chapter), "", self._format_currency(chapter_totals.get(chapter, 0.0))),
                open=not self._collapse_chapters_on_refresh,
                tags=("chapter",),
            )

        for index, item in enumerate(self.budget_items):
            total += item.import_total

            if item.chapter not in self.chapter_iid_by_code:
                self._ensure_chapter(item.chapter)
                chapter_iid = f"chapter:{len(self.chapter_iid_by_code)}"
                self.chapter_iid_by_code[item.chapter] = chapter_iid
                self.budget_tree.insert(
                    "",
                    "end",
                    iid=chapter_iid,
                    text=format_chapter_code(item.chapter),
                    values=(CONCEPT_TYPES["chapter"], self._chapter_title(item.chapter), "", self._format_currency(chapter_totals[item.chapter])),
                    open=True,
                    tags=("chapter",),
                )

            position = items_per_chapter.get(item.chapter, 0)
            items_per_chapter[item.chapter] = position + 1
            item_iid = f"item:{index}"
            self.budget_index_by_iid[item_iid] = index
            self.budget_tree.insert(
                self.chapter_iid_by_code[item.chapter],
                "end",
                iid=item_iid,
                text=item.code,
                values=self._item_row_values(item),
                tags=(
                    ("duplicate_review",)
                    if item.code.endswith(DUPLICATE_ITEM_MARKER)
                    else ("item_even" if position % 2 == 0 else "item_odd",)
                ),
            )

            dataset = self._component_dataset(item)
            for resource_index, (resource, quantity, price) in enumerate(
                item.cost_components(dataset, strict=False)
            ):
                resource_code = resource.code
                self.budget_tree.insert(
                    item_iid,
                    "end",
                    iid=f"resource:{index}:{resource_index}",
                    text=resource_code,
                    values=(
                        CONCEPT_TYPES[self._budget_concept_type(item, resource_code)],
                        resource.summary or resource_code,
                        f"{fmt_number(quantity, 4)} {resource.unit}".strip(),
                        self._format_currency(round_half_up(Decimal(str(quantity)) * Decimal(str(price)))),
                    ),
                    tags=("resource",),
                )
            for resource_index, (resource, quantity, price) in enumerate(item.indirect_components()):
                self.budget_tree.insert(
                    item_iid, "end", iid=f"indirect:{index}:{resource_index}",
                    text=resource.code,
                    values=(
                        CONCEPT_TYPES["%" if resource.ctype == "%" else "0"],
                        resource.summary,
                        f"{fmt_number(quantity, 4)} {resource.unit}".strip(),
                        self._format_currency(round_half_up(Decimal(str(quantity)) * Decimal(str(price)))),
                    ),
                    tags=("resource",),
                )
        self.budget_summary.config(text=f"Total del presupuesto: {self._format_currency(total)}")
        self._collapse_chapters_on_refresh = False
        self.filter_budget()

    def _restore_budget_rows(self) -> None:
        """Reincorpora las filas ocultas sin alterar sus identificadores ni su orden."""
        for iid, parent, index in self._budget_flattened:
            if self.budget_tree.exists(iid):
                self.budget_tree.move(iid, parent, index)
        self._budget_flattened.clear()
        for iid, parent, index in self._budget_detached:
            if self.budget_tree.exists(iid):
                self.budget_tree.move(iid, parent, index)
        self._budget_detached.clear()

    def filter_budget(self, *_args) -> None:
        """Filtra por texto y naturaleza, mostrando el contexto a la izquierda del recurso."""
        self._close_type_editor()
        selected = set(self.budget_tree.selection())
        if self.selected_budget_index is not None:
            selected.add(f"item:{self.selected_budget_index}")
        if self.selected_chapter in self.chapter_iid_by_code:
            selected.add(self.chapter_iid_by_code[self.selected_chapter])
        self._restore_budget_rows()
        term = self.budget_search_var.get().strip().casefold()
        kind = self.budget_resource_filter
        active = bool(term or kind)
        if active and self._budget_filter_open_state is None:
            self._budget_filter_open_state = {
                iid: bool(self.budget_tree.item(iid, "open"))
                for iid in (*self.chapter_iid_by_code.values(), *self.budget_index_by_iid)
            }
        hidden: list[tuple[str, str, int]] = []
        leaves: list[tuple[str, str, int]] = []
        visible: set[str] = set()
        chapter_codes = {iid: code for code, iid in self.chapter_iid_by_code.items()}

        def visit(parent: str, inherited_match: bool = False) -> bool:
            """Evalúa cada rama antes de ocultarla, manteniendo su posición original."""
            any_visible = False
            for index, iid in enumerate(self.budget_tree.get_children(parent)):
                values = self.budget_tree.item(iid, "values")
                text = f"{self.budget_tree.item(iid, 'text')} {' '.join(map(str, values[:2]))}"
                if iid in chapter_codes:
                    text += " " + chapter_codes[iid]
                if iid.startswith("item:"):
                    text += " " + self.budget_items[self.budget_index_by_iid[iid]].text
                matches = inherited_match or not term or term in text.casefold()
                child_visible = visit(iid, matches)
                is_leaf = not self.budget_tree.get_children(iid)
                nature_matches = not kind or (
                    is_leaf and not iid.startswith("chapter:") and
                    bool(values) and values[0] == CONCEPT_TYPES[kind]
                )
                keep = child_visible or (matches and nature_matches)
                if keep:
                    visible.add(iid)
                    any_visible = True
                    if active and child_visible:
                        self.budget_tree.item(iid, open=True)
                    if kind and is_leaf:
                        leaves.append((iid, parent, index))
                else:
                    hidden.append((iid, parent, index))
            return any_visible

        visit("")
        for iid, parent, index in hidden:
            self.budget_tree.detach(iid)
            self._budget_detached.append((iid, parent, index))
        if kind:
            for iid, parent, index in leaves:
                item_index = (
                    self.budget_index_by_iid[iid] if iid.startswith("item:")
                    else int(iid.split(":")[1])
                )
                item = self.budget_items[item_index]
                self.budget_tree.set(
                    iid, "capitulo",
                    f"{format_chapter_code(item.chapter)} · {self._chapter_title(item.chapter)}",
                )
                self.budget_tree.set(
                    iid, "partida",
                    f"{item.code} · {item.summary}" if iid.startswith("resource:") else "",
                )
                self.budget_tree.set(iid, "codigo", self.budget_tree.item(iid, "text"))
                self.budget_tree.move(iid, "", "end")
                self._budget_flattened.append((iid, parent, index))
            for index, iid in enumerate(self.chapter_iid_by_code.values()):
                if iid in visible:
                    self.budget_tree.detach(iid)
                    self._budget_detached.append((iid, "", index))
        self.budget_tree.configure(
            show="headings" if kind else "tree headings",
            displaycolumns=(
                ("capitulo", "partida", "codigo", "tipo", "texto", "medicion", "importe")
                if kind else ("tipo", "texto", "medicion", "importe")
            ),
        )
        if not active and self._budget_filter_open_state is not None:
            for iid, opened in self._budget_filter_open_state.items():
                if self.budget_tree.exists(iid):
                    self.budget_tree.item(iid, open=opened)
            self._budget_filter_open_state = None
        if selected - visible:
            self.budget_tree.selection_remove(*self.budget_tree.selection())
            self.selected_budget_index = None
            self.selected_chapter = None
            self._show_item_details(None)

    def toggle_budget_resource_filter(self, kind: str) -> None:
        """Alterna un filtro exclusivo de recursos sin modificar el presupuesto."""
        self.budget_resource_filter = "" if self.budget_resource_filter == kind else kind
        for code, variable in self.budget_filter_variables.items():
            variable.set(code == self.budget_resource_filter)
        self.filter_budget()

    def toggle_all_budget_nodes(self) -> None:
        """Expande todas las ramas visibles o las contrae si ya estaban desplegadas."""
        branches = [
            iid for iid in (*self.chapter_iid_by_code.values(), *self.budget_index_by_iid)
            if self.budget_tree.get_children(iid)
        ]
        expanded = not all(self.budget_tree.item(iid, "open") for iid in branches)
        for iid in branches:
            self.budget_tree.item(iid, open=expanded)

    def _chapter_title(self, chapter: str) -> str:
        """Obtiene el título del capítulo personalizado, de una base o de su código."""
        if chapter in self.chapter_titles:
            return self.chapter_titles[chapter]
        for dataset in (self.budget_dataset, *self.reference_bases):
            if dataset and chapter in dataset.concepts:
                summary = dataset.concepts[chapter].summary
                if summary:
                    return summary
        return format_chapter_code(chapter)

    def _item_row_values(self, item: ProjectItem) -> tuple[str, str, str, str]:
        """Prepara tipo, descripción, medición e importe para mostrar una partida."""
        return (
            CONCEPT_TYPES[self._budget_concept_type(item)],
            item.summary,
            f"{fmt_number(item.measurement)} {item.unit}".strip(),
            self._format_currency(item.import_total),
        )

    def toggle_budget_node(self, event=None):
        """Alterna el despliegue de una fila del árbol del presupuesto."""
        if self.budget_resource_filter:
            return "break"
        if event and self.budget_tree.identify_column(event.x) == "#1":
            return "break"
        row = self.budget_tree.identify_row(event.y) if event else ""
        if row:
            self.budget_tree.item(row, open=not bool(self.budget_tree.item(row, "open")))
        return "break"

    def _budget_concept_type(self, item: ProjectItem, component_code: str | None = None) -> str:
        """Determina el tipo visible de una partida o recurso, respetando sus cambios manuales."""
        override = item.concept_type if component_code is None else item.component_types.get(component_code, "")
        if override in CONCEPT_TYPES and override != "chapter":
            return override
        code = item.code if component_code is None else component_code
        if code.startswith("%"):
            return "%"
        dataset = self._component_dataset(item)
        concept = item.component_concepts.get(code) if component_code is not None else None
        if concept is None:
            concept = dataset.concepts.get(code) if dataset else None
        if concept is None:
            return "0"
        if concept.ctype == "0" and component_code is not None and concept.children:
            return "aux"
        return concept.ctype if concept.ctype in {"0", "1", "2", "3"} else "0"

    def edit_budget_type(self, event):
        """Abre un selector en la columna Tipo y confirma el cambio elegido por el usuario."""
        if self.budget_tree.identify_region(event.x, event.y) != "cell":
            return
        column = self.budget_tree.identify_column(event.x)
        if not column or self.budget_tree.column(column, "id") != "tipo":
            return
        row = self.budget_tree.identify_row(event.y)
        if row.startswith("indirect:"):
            return "break"
        bbox = self.budget_tree.bbox(row, "tipo") if row else ()
        if not bbox:
            return
        self._close_type_editor()
        chapter = row.startswith("chapter:")
        options = [CONCEPT_TYPES["chapter"]] if chapter else [
            label for key, label in CONCEPT_TYPES.items() if key != "chapter"
        ]
        editor = ttk.Combobox(self.budget_tree, values=options, state="readonly")
        editor.set(self.budget_tree.set(row, "tipo"))
        x, y, width, height = bbox
        editor.place(x=x, y=y, width=width, height=height)
        self._type_editor = editor

        def commit(_event=None):
            """Guarda el tipo elegido en el selector emergente para la fila activa."""
            value = editor.get()
            self._close_type_editor()
            kind = next(key for key, label in CONCEPT_TYPES.items() if label == value)
            self._commit_budget_type(row, kind)

        editor.bind("<<ComboboxSelected>>", commit)
        editor.bind("<Return>", commit)
        editor.bind("<Escape>", lambda _event: self._close_type_editor())
        editor.bind("<FocusOut>", lambda _event: self._close_type_editor())
        editor.focus_set()
        editor.event_generate("<Down>")
        return "break"

    def _commit_budget_type(self, row: str, kind: str) -> None:
        """Aplica el tipo de concepto a la partida o recurso y refresca los importes visibles."""
        if row.startswith("indirect:"):
            return
        if row.startswith("chapter:"):
            if kind != "chapter":
                messagebox.showwarning("Tipo no válido", "Los capítulos deben conservar su tipo.")
            return
        if kind not in CONCEPT_TYPES or kind == "chapter":
            messagebox.showwarning("Tipo no válido", "Selecciona un tipo de partida o recurso.")
            return
        if row.startswith("resource:"):
            _, item_index, component_index = row.split(":")
            item = self.budget_items[int(item_index)]
            code, _quantity = item.decomposition[int(component_index)]
            item.component_types[code] = kind
            item.rendimiento = sum(
                quantity for code, quantity in item.decomposition
                if self._budget_concept_type(item, code) == "1"
            )
        else:
            item = self.budget_items[self.budget_index_by_iid[row]]
            item.concept_type = kind
        opened = [
            iid for iid in (*self.chapter_iid_by_code.values(), *self.budget_index_by_iid)
            if self.budget_tree.item(iid, "open")
        ]
        self.refresh_budget_tree()
        for iid in opened:
            self.budget_tree.item(iid, open=True)
        self.budget_tree.selection_set(row)
        self.budget_tree.see(row)
        self.filter_budget()
        self._mark_dirty()

    def _close_type_editor(self) -> None:
        """Cierra el selector de tipo si permanece abierto."""
        editor = self._type_editor
        if editor is not None:
            self._type_editor = None
            editor.destroy()

    def _show_item_details(self, item: ProjectItem | None):
        """Actualiza paneles de medición, descomposición y descripción de la partida."""
        self._close_measurement_editor()
        self._close_component_editor()
        for row in self.measurement_tree.get_children():
            self.measurement_tree.delete(row)
        for row in self.component_tree.get_children():
            self.component_tree.delete(row)

        if item is None:
            self.measurement_total_label.config(text="Sin partida seleccionada.")
        else:
            for index, line in enumerate(item.measurement_lines):
                self.measurement_tree.insert("", "end", iid=str(index), values=self._measurement_values(line))
            dataset = self._component_dataset(item)
            for index, (component, quantity, price) in enumerate(
                item.cost_components(dataset, strict=False)
            ):
                code = component.code
                editable = index < len(item.decomposition) and item.decomposition[index][0] == code
                self.component_tree.insert(
                    "", "end", iid=str(index) if editable else f"indirect:calculated:{index}", values=(
                        code,
                        component.summary or code,
                        fmt_number(quantity, 4),
                        fmt_number(price),
                        self._format_currency(round_half_up(Decimal(str(quantity)) * Decimal(str(price)))),
                    )
                )
            for index, (component, quantity, price) in enumerate(item.indirect_components()):
                self.component_tree.insert(
                    "", "end", iid=f"indirect:{index}", values=(
                        component.code, component.summary, fmt_number(quantity, 4),
                        fmt_number(price, 4),
                        self._format_currency(round_half_up(Decimal(str(quantity)) * Decimal(str(price)))),
                    ),
                )
            self._update_measurement_total(item)

        description = "" if item is None else (item.text or item.summary)
        self.description_text.config(state="normal")
        self.description_text.delete("1.0", "end")
        self.description_text.insert("1.0", description)
        if item is None:
            self.description_text.config(state="disabled")

    def _component_dataset(self, item: ProjectItem) -> Bc3Dataset | None:
        """Selecciona la base de conceptos que corresponde al origen de la partida."""
        return self._reference_base_for(item) if item.source == "BCCA" else self.budget_dataset

    def _recalculate_component_cost(self, item: ProjectItem) -> None:
        """Recalcula el precio unitario sumando los costes de los componentes."""
        dataset = self._component_dataset(item)
        if not item.decomposition or (
            dataset is None and any(
                code not in item.component_prices and code not in item.component_concepts
                for code, _ in item.decomposition
            )
        ):
            return
        direct_price = float(sum((
            (Decimal(str(quantity)) * Decimal(str(price))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            for _, quantity, price in item.cost_components(dataset, reconcile=False)
        ), Decimal(0)))
        item.set_indirect_costs(self.report_settings.indirect_costs, direct_price)

    def edit_component_cell(self, event):
        """Permite editar descripción, cantidad o precio de un recurso con doble clic."""
        if self.selected_budget_index is None:
            return "break"
        row = self.component_tree.identify_row(event.y)
        column = self.component_tree.identify_column(event.x)
        if not row or row.startswith("indirect:") or column not in {"#2", "#3", "#4"}:
            return "break"

        self._close_component_editor()
        bbox = self.component_tree.bbox(row, column)
        if not bbox:
            return "break"
        x, y, width, height = bbox
        editor = ttk.Entry(self.component_tree)
        editor.insert(0, self.component_tree.set(row, column))
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        self._component_editor = editor

        def commit(_event=None):
            """Confirma el valor introducido en la celda de componente editable."""
            if self._component_editor is not editor:
                return
            value = editor.get()
            self._close_component_editor()
            self._commit_component_cell(row, column, value)

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", lambda _event: self._close_component_editor())
        return "break"

    def _commit_component_cell(self, row: str, column: str, value: str) -> None:
        """Guarda la descripción local o recalcula cantidad y precio de un componente."""
        if self.selected_budget_index is None:
            return
        if row.startswith("indirect:"):
            return
        item = self.budget_items[self.selected_budget_index]
        component_index = int(row)
        if component_index >= len(item.decomposition):
            return
        code, _quantity = item.decomposition[component_index]
        if column == "#2":
            summary = value.strip()
            if not summary:
                messagebox.showerror("Dato inválido", "La descripción no puede estar vacía.")
                return
            dataset = self._component_dataset(item)
            original = item.component_concepts.get(code)
            if original is None:
                original = dataset.concepts.get(code) if dataset else None
            item.component_concepts[code] = replace(
                original or Concept(code, summary=code), summary=summary,
            )
            self._show_item_details(item)
            self._refresh_item_row(self.selected_budget_index)
            self._mark_dirty()
            self._set_status(f"Actualizada la descripción de {code} en la partida {item.code}.")
            return
        number = parse_float(value, float("nan"))
        if not isfinite(number):
            messagebox.showerror("Dato inválido", f"'{value}' no es un número válido.")
            return
        if "%" in code and column == "#4":
            messagebox.showwarning(
                "Precio calculado",
                "La base de un porcentaje se calcula desde los componentes anteriores. "
                "Edita su coeficiente en Cantidad.",
            )
            return
        if column == "#3":
            item.decomposition[component_index] = (code, number)
        else:
            item.component_prices[code] = number
        self._recalculate_component_cost(item)
        direct_price = item.unit_price if item.direct_unit_price is None else item.direct_unit_price
        self.budget_fields["price_var"].set(f"{direct_price:.2f}")
        self._show_item_details(item)
        self._refresh_item_row(self.selected_budget_index)
        self._mark_dirty()
        self._set_status(f"Actualizado el coste de {code} en la partida {item.code}.")

    def _close_component_editor(self) -> None:
        """Cierra el editor emergente de una celda de componente, si existe."""
        editor = getattr(self, "_component_editor", None)
        if editor is not None:
            self._component_editor = None
            editor.destroy()

    @staticmethod
    def _measurement_values(line: MeasurementLine) -> tuple[str, ...]:
        """Convierte una línea de medición en los valores formateados de la tabla."""
        def render(value: float | None) -> str:
            """Formatea un factor de medición como número sin ceros finales innecesarios."""
            return "" if value is None else f"{value:g}"

        return (
            line.comment,
            render(line.units),
            render(line.length),
            render(line.width),
            render(line.height),
            render(line.k),
            f"{line.partial:,.4f}",
        )

    def _update_measurement_total(self, item: ProjectItem) -> None:
        """Actualiza la etiqueta con la medición total o indica que es manual."""
        if item.measurement_lines:
            text = f"Medición total: {item.measurement:,.4f} {item.unit}".strip()
        else:
            text = f"Medición manual: {item.measurement:,.4f} {item.unit} (añade líneas para desglosarla)".strip()
        self.measurement_total_label.config(text=text)

    def _apply_measurement_change(self, item: ProjectItem) -> None:
        """Recalcula la partida y propaga el cambio al presupuesto."""
        item.recalculate_measurement()
        self._update_measurement_total(item)
        self.budget_fields["measurement_var"].set(f"{item.measurement:.4f}")
        self._refresh_item_row(self.selected_budget_index)
        self._mark_dirty()

    def _refresh_item_row(self, index: int | None) -> None:
        """Actualiza la fila de partida, el total del capítulo y el total general."""
        if index is None:
            return
        self._restore_budget_rows()
        item = self.budget_items[index]
        iid = f"item:{index}"
        if self.budget_tree.exists(iid):
            self.budget_tree.item(iid, text=item.code, values=self._item_row_values(item))
        components = item.cost_components(self._component_dataset(item), strict=False)
        if self.budget_tree.exists(iid):
            for row in self.budget_tree.get_children(iid):
                if row.startswith(f"resource:{index}:") and int(row.rsplit(":", 1)[1]) >= len(components):
                    self.budget_tree.delete(row)
        for component_index, (component, quantity, price) in enumerate(components):
            row = f"resource:{index}:{component_index}"
            values = (
                CONCEPT_TYPES[self._budget_concept_type(item, component.code)],
                component.summary or component.code,
                f"{fmt_number(quantity, 4)} {component.unit}".strip(),
                self._format_currency(round_half_up(Decimal(str(quantity)) * Decimal(str(price)))),
            )
            if self.budget_tree.exists(row):
                self.budget_tree.item(row, text=component.code, values=values)
            elif self.budget_tree.exists(iid):
                self.budget_tree.insert(iid, "end", iid=row, text=component.code, values=values, tags=("resource",))
        for component_index, (component, quantity, price) in enumerate(item.indirect_components()):
            row = f"indirect:{index}:{component_index}"
            if self.budget_tree.exists(row):
                self.budget_tree.item(row, values=(
                    CONCEPT_TYPES["%" if component.ctype == "%" else "0"],
                    component.summary, f"{fmt_number(quantity, 4)} {component.unit}".strip(),
                    self._format_currency(round_half_up(Decimal(str(quantity)) * Decimal(str(price)))),
                ))

        chapter_iid = self.chapter_iid_by_code.get(item.chapter)
        if chapter_iid and self.budget_tree.exists(chapter_iid):
            chapter_total = sum(
                entry.import_total for entry in self.budget_items if entry.chapter == item.chapter
            )
            self.budget_tree.item(
                chapter_iid,
                text=format_chapter_code(item.chapter),
                values=(CONCEPT_TYPES["chapter"], self._chapter_title(item.chapter), "", self._format_currency(chapter_total)),
            )

        total = sum(entry.import_total for entry in self.budget_items)
        self.budget_summary.config(text=f"Total del presupuesto: {self._format_currency(total)}")
        self.filter_budget()

    def add_measurement_line(self):
        """Añade una línea inicial de medición a la partida seleccionada."""
        if self.selected_budget_index is None:
            messagebox.showwarning("Sin selección", "Selecciona primero una partida del presupuesto.")
            return
        item = self.budget_items[self.selected_budget_index]
        item.measurement_lines.append(MeasurementLine(comment="Nueva línea", units=1.0))
        index = len(item.measurement_lines) - 1
        self.measurement_tree.insert(
            "", "end", iid=str(index), values=self._measurement_values(item.measurement_lines[index])
        )
        self.measurement_tree.selection_set(str(index))
        self._apply_measurement_change(item)

    def delete_measurement_line(self):
        """Elimina la línea de medición seleccionada y recalcula la partida."""
        if self.selected_budget_index is None:
            return
        selection = self.measurement_tree.selection()
        if not selection:
            messagebox.showwarning("Sin selección", "Selecciona la línea de medición que quieres eliminar.")
            return
        item = self.budget_items[self.selected_budget_index]
        del item.measurement_lines[int(selection[0])]
        self._show_item_details(item)
        self._apply_measurement_change(item)

    def edit_measurement_cell(self, event):
        """Abre un campo de edición sobre una celda editable de medición."""
        if self.selected_budget_index is None:
            return "break"
        row = self.measurement_tree.identify_row(event.y)
        column = self.measurement_tree.identify_column(event.x)
        if not row or column in ("", "#7"):
            return "break"

        self._close_measurement_editor()
        bbox = self.measurement_tree.bbox(row, column)
        if not bbox:
            return "break"

        x, y, width, height = bbox
        current = self.measurement_tree.set(row, column)
        editor = ttk.Entry(self.measurement_tree)
        editor.insert(0, current)
        editor.select_range(0, "end")
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        self._measurement_editor = editor
        item_index = self.selected_budget_index

        def commit(_event=None):
            """Confirma la edición si sigue activa la partida en la que se inició."""
            if self._measurement_editor is not editor:
                return
            value = editor.get()
            self._close_measurement_editor()
            if self.selected_budget_index == item_index:
                self._commit_measurement_cell(row, column, value)

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", lambda _event: self._close_measurement_editor())
        return "break"

    def _commit_measurement_cell(self, row: str, column: str, value: str) -> None:
        """Valida y guarda un factor de medición o comentario y propaga el recálculo."""
        if self.selected_budget_index is None:
            return
        item = self.budget_items[self.selected_budget_index]
        line_index = int(row)
        if line_index >= len(item.measurement_lines):
            return
        line = item.measurement_lines[line_index]
        text = value.strip()

        if column == "#1":
            line.comment = text
        else:
            number = parse_float(text, float("nan")) if text else (1.0 if column == "#6" else None)
            if number is not None and not isfinite(number):
                messagebox.showerror("Dato inválido", f"'{value}' no es un número válido.")
                return
            setattr(
                line,
                {"#2": "units", "#3": "length", "#4": "width", "#5": "height", "#6": "k"}[column],
                number,
            )

        self.measurement_tree.item(row, values=self._measurement_values(line))
        self._apply_measurement_change(item)

    def _close_measurement_editor(self) -> None:
        """Cierra el editor emergente de una celda de medición, si está abierto."""
        editor = getattr(self, "_measurement_editor", None)
        if editor is not None:
            self._measurement_editor = None
            editor.destroy()

    def on_description_edited(self, event=None):
        """Guarda el texto descriptivo editado para la partida seleccionada."""
        if self.selected_budget_index is None:
            return
        item = self.budget_items[self.selected_budget_index]
        item.text = self.description_text.get("1.0", "end-1c")
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        """Registra una edición real y actualiza el indicador de cambios pendientes."""
        state = self._capture_project_state()
        if state != self._history_state:
            self._undo_history.append(self._history_state)
            del self._undo_history[:-50]
            self._history_state = state
        self.is_dirty = state != self._saved_state
        self._update_save_controls()

    def _capture_project_state(self) -> dict:
        """Copia los datos editables sin duplicar bases BC3 ni objetos de interfaz."""
        return deepcopy({
            "items": self.budget_items,
            "order": self.chapter_order,
            "titles": self.chapter_titles,
            "planning": self.planning_state,
            "invoicing": self.invoicing_state,
            "rcds": self.rcd_state,
            "safety_health": self.safety_health_state,
            "settings": self.report_settings,
            "title": self.project_title,
        })

    def _reset_history(self, saved: bool = True) -> None:
        """Establece el estado inicial de una sesión y descarta el historial anterior."""
        self._undo_history: list[dict] = []
        self._history_state = self._capture_project_state()
        self._saved_state: dict | None = self._history_state if saved else None
        self.is_dirty = not saved
        self._update_save_controls()

    def _update_save_controls(self) -> None:
        """Muestra si quedan cambios pendientes y si existe alguna acción reversible."""
        self.undo_button.configure(state="normal" if self._undo_history else "disabled")
        self.save_state_label.configure(
            text=translate("Cambios sin guardar" if self.is_dirty else "Sin cambios pendientes")
        )

    def undo_last_action(self, event=None) -> str:
        """Restaura la última edición sin modificar los archivos guardados en disco."""
        self._close_measurement_editor()
        self._close_component_editor()
        self._close_type_editor()
        if not self._undo_history:
            self._set_status("No hay acciones que deshacer.")
            return "break"
        self._dismiss_project_windows()
        self._dismiss_invoicing()
        self._dismiss_rcds()
        self._dismiss_safety_health()
        state = self._undo_history.pop()
        restored = deepcopy(state)
        self.budget_items = restored["items"]
        self.chapter_order = restored["order"]
        self.chapter_titles = restored["titles"]
        self.planning_state = restored["planning"]
        self.invoicing_state = restored["invoicing"]
        self.rcd_state = restored["rcds"]
        self.safety_health_state = restored["safety_health"]
        self.report_settings = restored["settings"]
        self.project_title = restored["title"]
        self.selected_budget_index = None
        self.selected_chapter = None
        self._history_state = state
        self.is_dirty = state != self._saved_state
        self.refresh_budget_tree()
        self._show_item_details(None)
        self.project_panel_title.set(f"Proyecto: {self._project_title()}")
        self._update_save_controls()
        self._set_status("Última acción deshecha. Guarda el proyecto para conservar este estado.")
        return "break"

    def _save_shortcut(self, event=None) -> str:
        """Guarda el proyecto desde el teclado sin propagar el atajo."""
        self.save_project()
        return "break"

    def _confirm_project_change(self) -> bool:
        """Ofrece guardar antes de sustituir un proyecto con cambios pendientes."""
        if not self.is_dirty:
            return True
        choice = messagebox.askyesnocancel(
            "Cambios sin guardar",
            "Hay cambios sin guardar. ¿Quieres guardarlos antes de abrir otro proyecto?",
            parent=self.root,
        )
        return choice is not None and (not choice or self.save_project())

    def _read_language_preference(self) -> str:
        """Lee el idioma global guardado y avisa si el archivo no es válido."""
        try:
            data = json.loads(self.preferences_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "es"
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showwarning(
                "Preferencias de idioma",
                f"No se pudieron leer las preferencias; se usará español:\n{exc}",
                parent=self.root,
            )
            return "es"
        language = data.get("language") if isinstance(data, dict) else None
        if not isinstance(language, str) or language not in LANGUAGES:
            messagebox.showwarning(
                "Preferencias de idioma",
                "El idioma guardado no es válido; se usará español.",
                parent=self.root,
            )
            return "es"
        return language

    def _save_language_preference(self, language: str) -> bool:
        """Guarda el idioma global de forma atómica y aplica el cambio al instante."""
        if language not in LANGUAGES:
            raise ValueError(f"Idioma no admitido: {language}")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.preferences_path.parent,
                suffix=".tmp", delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump({"language": language}, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            temporary_path.replace(self.preferences_path)
        except OSError as exc:
            messagebox.showerror(
                translate("No se pudo guardar el idioma"),
                f"{translate('No se pudo guardar el idioma')}:\n{exc}",
                parent=self.root,
            )
            return False
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        self.language = language
        set_language(language)
        localize_widget_tree(self.root)
        self._set_status(translate("Idioma cambiado"))
        return True

    def _read_report_settings(self) -> ReportSettings:
        """Lee la configuración de informes y usa valores predeterminados ante errores."""
        try:
            return ReportSettings.from_dict(json.loads(self.report_settings_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return ReportSettings()
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showwarning("Configuración", f"No se pudo leer la configuración de informes:\n{exc}")
            return ReportSettings()

    def _save_report_settings(self, data: dict) -> bool:
        """Valida y persiste la configuración y actualiza la del proyecto activo."""
        try:
            settings = ReportSettings.from_dict(data)
            self.report_settings_path.write_text(
                json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Configuración", f"No se pudo guardar la configuración:\n{exc}")
            return False
        self.default_report_settings = settings
        self.report_settings = ReportSettings.from_dict(settings.to_dict())
        for item in self.budget_items:
            item.set_indirect_costs(settings.indirect_costs)
        self.refresh_budget_tree()
        selected = self.selected_budget_index
        self._show_item_details(self.budget_items[selected] if selected is not None else None)
        self._mark_dirty()
        if self.project_path is not None and not self._write_project_file(self.project_path):
            return False
        self._set_status("Configuración de informes guardada.")
        return True

    def open_report_settings(self) -> None:
        """Abre las pestañas de configuración de informes e idioma."""
        window = tk.Toplevel(self.root)
        self._project_windows.append(window)
        window.title("Configuración de informes")
        window.transient(self.root)
        window.resizable(True, False)
        notebook = ttk.Notebook(window, padding=12)
        notebook.pack(fill="both", expand=True)
        reports_tab = ttk.Frame(notebook, padding=8)
        language_tab = ttk.Frame(notebook, padding=8)
        notebook.add(reports_tab, text="Informes")
        notebook.add(language_tab, text="Idioma")
        fields = (
            ("Autoría / nombre", "author"), ("Cargo / titulación", "role"),
            ("Entidad / servicio", "organization"), ("Localidad", "location"),
            ("Texto de firma / fecha", "signature"),
            ("Empresa constructora", "contractor"),
            ("Responsable de la empresa constructora", "contractor_representative"),
            ("Costes Indirectos (%)", "indirect_costs"),
            ("Gastos generales (%)", "general_expenses"),
            ("Beneficio industrial (%)", "industrial_profit"), ("IVA (%)", "vat"),
            ("Baja de adjudicación (%)", "tender_discount"),
            ("Seguridad y salud orientativa (%)", "safety_health_percentage"),
        )
        variables = {}
        values = self.report_settings.to_dict()
        for row, (label, key) in enumerate(fields):
            ttk.Label(reports_tab, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
            variables[key] = tk.StringVar(value=str(values[key]))
            ttk.Entry(reports_tab, textvariable=variables[key], width=55).grid(row=row, column=1, sticky="ew")
        format_row = len(fields)
        ttk.Label(reports_tab, text="Formato de informes").grid(
            row=format_row, column=0, sticky="w", pady=5, padx=(0, 12)
        )
        variables["output_format"] = tk.StringVar(value=values["output_format"].upper())
        ttk.Combobox(
            reports_tab, textvariable=variables["output_format"], values=("PDF", "ODT"),
            state="readonly", width=12,
        ).grid(row=format_row, column=1, sticky="w")
        currency_row = format_row + 1
        ttk.Label(reports_tab, text="Divisa del proyecto").grid(
            row=currency_row, column=0, sticky="w", pady=5, padx=(0, 12)
        )
        currency_options = {
            "EUR (€)": "EUR",
            "USD ($)": "USD",
            "GBP (£)": "GBP",
            "CHF": "CHF",
            "CAD (CA$)": "CAD",
            "AUD (A$)": "AUD",
            "Otra (personalizada)": "OTHER",
        }
        variables["currency"] = tk.StringVar(value=values["currency"])
        currency_selector = ttk.Combobox(
            reports_tab,
            values=tuple(currency_options),
            state="readonly",
            width=24,
        )
        currency_selector.set(next(
            label for label, code in currency_options.items() if code == values["currency"]
        ))
        currency_selector.grid(row=currency_row, column=1, sticky="w")
        currency_selector.bind(
            "<<ComboboxSelected>>",
            lambda _event: set_custom_currency_state(),
        )
        custom_currency_row = currency_row + 1
        ttk.Label(reports_tab, text="Símbolo o código personalizado").grid(
            row=custom_currency_row, column=0, sticky="w", pady=5, padx=(0, 12)
        )
        variables["custom_currency"] = tk.StringVar(value=values["custom_currency"])
        custom_currency_entry = ttk.Entry(
            reports_tab, textvariable=variables["custom_currency"], width=20
        )
        custom_currency_entry.grid(row=custom_currency_row, column=1, sticky="w")

        def set_custom_currency_state() -> None:
            """Activa el campo de divisa personalizada solo cuando se ha seleccionado."""
            currency = currency_options[currency_selector.get()]
            variables["currency"].set(currency)
            custom_currency_entry.state(
                ("!disabled",) if currency == "OTHER" else ("disabled",)
            )

        set_custom_currency_state()
        template_row = custom_currency_row + 1
        ttk.Label(reports_tab, text="Plantilla de cabecera y pie (ODT)").grid(
            row=template_row, column=0, sticky="w", pady=5, padx=(0, 12)
        )
        variables["template_path"] = tk.StringVar(value=values["template_path"])
        ttk.Entry(reports_tab, textvariable=variables["template_path"], width=55).grid(
            row=template_row, column=1, sticky="ew"
        )

        def select_report_template() -> None:
            """Permite seleccionar la plantilla ODT de cabecera y pie de los informes."""
            path = filedialog.askopenfilename(
                parent=window,
                title="Selecciona la plantilla de informes",
                filetypes=[("Plantilla ODT", "*.odt")],
            )
            if path:
                variables["template_path"].set(path)

        ttk.Button(reports_tab, text="Examinar…", command=select_report_template).grid(
            row=template_row, column=2, sticky="w", padx=(8, 0)
        )
        ttk.Label(
            reports_tab, text="La plantilla aporta la cabecera y el pie de todos los informes de\n"
            "presupuesto y certificaciones. Déjala vacía para usar la incluida.\n"
            "GG y BI se aplican al PEM. En certificaciones, se deduce la baja\n"
            "antes del IVA. La baja no modifica el presupuesto de licitación.\n"
            "Guardar conserva estos datos para este proyecto y como valores\n"
            "iniciales de nuevos proyectos. Los proyectos guardados mantienen su configuración.\n"
            "La divisa solo cambia cómo se muestran los importes; no los convierte.",
        ).grid(row=template_row + 1, column=0, columnspan=2, sticky="w", pady=12)

        language_tab.columnconfigure(1, weight=1)
        ttk.Label(language_tab, text="Idioma").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=8)
        language_var = tk.StringVar(value=LANGUAGES[self.language])
        language_selector = ttk.Combobox(
            language_tab, textvariable=language_var, values=tuple(LANGUAGES.values()),
            state="readonly", width=24,
        )
        language_selector.grid(row=0, column=1, sticky="w", pady=8)
        ttk.Label(
            language_tab,
            text="El idioma se guarda como preferencia de la aplicación y se aplica inmediatamente.",
            wraplength=420,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 12))

        def save_reports():
            """Guarda los valores del formulario y cierra el diálogo si son válidos."""
            data = {key: variable.get() for key, variable in variables.items()}
            data["output_format"] = str(data["output_format"]).lower()
            data["currency"] = currency_options[currency_selector.get()]
            if self._save_report_settings(data):
                window.destroy()

        def save_language():
            """Guarda la preferencia global y conserva abierto el diálogo."""
            selected = language_var.get()
            language = next(
                code for code, label in LANGUAGES.items() if label == selected
            )
            if self._save_language_preference(language):
                language_var.set(LANGUAGES[language])

        ttk.Button(
            reports_tab, text="Guardar informes", style="Accent.TButton",
            command=save_reports,
        ).grid(row=template_row + 2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(
            language_tab, text="Guardar idioma", style="Accent.TButton",
            command=save_language,
        ).grid(row=2, column=1, sticky="e", pady=(8, 0))
        localize_widget_tree(window)
        window.grab_set()

    def generate_report(self, report: str) -> None:
        """Genera y guarda el informe en el formato configurado y trata de abrirlo."""
        if not self.budget_items:
            messagebox.showinfo("Presupuesto vacío", "No hay partidas para generar el informe.")
            return
        is_odt = self.report_settings.output_format == "odt"
        extension = ".odt" if is_odt else ".pdf"
        filetype = ("Documento ODT", "*.odt") if is_odt else ("Documento PDF", "*.pdf")
        file_path = filedialog.asksaveasfilename(
            title=f"Generar {report}", initialfile=f"{report}{extension}",
            defaultextension=extension, filetypes=[filetype],
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
                write_report_odt(self, report, temporary_path)
            else:
                write_pdf(self, report, temporary_path)
            temporary_path.replace(target)
        except ImportError as exc:
            messagebox.showerror("Dependencia de informes", f"Instala las dependencias con:\npython -m pip install -r requirements.txt\n\n{exc}")
            return
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo generar el informe", str(exc))
            return
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        self._set_status(f"Informe guardado: {target}")
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showwarning("Informe guardado", f"El PDF se ha guardado en:\n{target}\n\nNo se pudo abrir:\n{exc}")

    def _read_recent_projects(self) -> list[Path]:
        """Lee hasta cinco rutas absolutas, de más reciente a más antigua."""
        return self._read_recent_paths(
            self.recent_projects_path, "Proyectos recientes", "proyectos", 5
        )

    def _read_recent_reference_bases(self) -> list[Path]:
        """Lee hasta tres rutas absolutas de bases de referencia recientes."""
        return self._read_recent_paths(
            self.recent_reference_bases_path, "Bases recientes", "bases de referencia", 3
        )

    def _read_recent_paths(
        self, history_path: Path, dialog_title: str, item_name: str, maximum: int
    ) -> list[Path]:
        """Lee un historial de rutas absolutas y descarta entradas duplicadas."""
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(data, list) or not all(
                isinstance(value, str) and value.strip() and Path(value).is_absolute()
                for value in data
            ):
                raise ValueError(f"Se esperaba una lista de rutas absolutas de {item_name}.")
            return list(dict.fromkeys(Path(value) for value in data))[:maximum]
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            messagebox.showwarning(
                dialog_title, f"No se pudo leer el historial de {item_name}:\n{exc}"
            )
            return []

    def _refresh_recent_projects_menu(self) -> None:
        """Reconstruye las secciones de proyectos y bases recientes del menú Archivo."""
        self.file_menu.insert_command(0, label="Recientes", state="disabled")
        if not self.recent_projects:
            self.file_menu.insert_command(1, label="(No hay proyectos recientes)", state="disabled")
        for index, path in enumerate(self.recent_projects, start=1):
            self.file_menu.insert_command(
                index, label=f"{index}. {path}",
                command=lambda project_path=path: self.open_recent_project(project_path),
            )
        reference_start = 1 + max(1, len(self.recent_projects))
        self.file_menu.insert_command(
            reference_start, label="Bases de referencia recientes", state="disabled"
        )
        if not self.recent_reference_bases:
            self.file_menu.insert_command(
                reference_start + 1,
                label="(No hay bases de referencia recientes)",
                state="disabled",
            )
        for index, path in enumerate(self.recent_reference_bases, start=1):
            self.file_menu.insert_command(
                reference_start + index,
                label=f"{index}. {path}",
                command=lambda reference_path=path: self._load_bcca_file(reference_path),
            )
        self._recent_menu_entries = reference_start + 1 + max(1, len(self.recent_reference_bases))

    def _remember_recent_project(self, path: Path) -> None:
        """Guarda una ruta como proyecto reciente y actualiza el menú correspondiente."""
        path = path.resolve()
        self.recent_projects = [path] + [
            previous for previous in self.recent_projects if previous != path
        ][:4]
        try:
            self.recent_projects_path.write_text(
                json.dumps([str(value) for value in self.recent_projects], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showwarning(
                "Proyectos recientes", f"No se pudo guardar el historial de proyectos:\n{exc}"
            )
        self.file_menu.delete(0, self._recent_menu_entries - 1)
        self._refresh_recent_projects_menu()

    def _remember_recent_reference_base(self, path: Path) -> None:
        """Guarda una base de referencia reciente y actualiza la sección del menú."""
        path = path.resolve()
        self.recent_reference_bases = [path] + [
            previous for previous in self.recent_reference_bases if previous != path
        ][:2]
        try:
            self.recent_reference_bases_path.write_text(
                json.dumps(
                    [str(value) for value in self.recent_reference_bases],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showwarning(
                "Bases recientes", f"No se pudo guardar el historial de bases de referencia:\n{exc}"
            )
        self.file_menu.delete(0, self._recent_menu_entries - 1)
        self._refresh_recent_projects_menu()

    def open_recent_project(self, path: Path) -> None:
        """Abre un proyecto reciente tras ofrecer guardar los cambios pendientes."""
        if self._confirm_project_change():
            self._load_project_file(path)

    def _write_project_file(self, path: Path) -> bool:
        """Guarda el JSON mediante sustitución atómica para no truncar el anterior."""
        temporary: Path | None = None
        try:
            from rcd import RCD_BUDGET_SOURCE, RcdState, synchronize_budget

            if (self.rcd_state and not self._has_external_tool_chapter("rcd")
                    and any(item.source.startswith(RCD_BUDGET_SOURCE) for item in self.budget_items)):
                synchronize_budget(self, RcdState.from_dict(self.rcd_state))
            self._mark_dirty()
            content = json.dumps(self._project_payload(), ensure_ascii=False, indent=2)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(content)
            os.replace(temporary, path)
        except (OSError, ValueError) as exc:
            message = str(exc)
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    message += f"\nNo se pudo eliminar el archivo temporal: {cleanup_error}"
            messagebox.showerror("No se pudo guardar el proyecto", f"No se pudo escribir el archivo:\n{message}")
            return False
        self.project_path = path
        self._saved_state = self._history_state
        self.is_dirty = False
        self._update_save_controls()
        self._remember_recent_project(path)
        return True

    def _close_application(self) -> None:
        """Cierra la ventana y permite guardar los cambios sin guardar antes de salir."""
        if not self.is_dirty:
            self.root.destroy()
            return
        choice = messagebox.askyesnocancel(
            "Cambios sin guardar",
            "Hay cambios sin guardar. ¿Quieres guardarlos antes de cerrar?",
            parent=self.root,
        )
        if choice is None:
            return
        if choice and not self.save_project():
            return
        self.root.destroy()

    @staticmethod
    def _item_from_project_data(data: dict) -> ProjectItem:
        """Reconstruye una partida y sus mediciones desde los datos de un proyecto."""
        round_import_total = data.get("round_import_total", False)
        if not isinstance(round_import_total, bool):
            raise ValueError("El redondeo del importe de la partida debe ser booleano.")
        measurement_lines = [
            MeasurementLine(
                comment=str(line.get("comment", "")),
                units=line.get("units"),
                length=line.get("length"),
                width=line.get("width"),
                height=line.get("height"),
                k=float(line.get("k", 1.0)),
            )
            for line in data.get("measurement_lines", [])
        ]
        return ProjectItem(
            code=str(data["code"]),
            chapter=str(data["chapter"]),
            unit=str(data.get("unit", "")),
            summary=str(data.get("summary", "")),
            measurement=float(data.get("measurement", 0.0)),
            unit_price=float(data.get("unit_price", 0.0)),
            round_import_total=round_import_total,
            direct_unit_price=(
                float(data["direct_unit_price"]) if data.get("direct_unit_price") is not None else None
            ),
            rendimiento=float(data.get("rendimiento", 0.0)),
            source=str(data.get("source", "manual")),
            reference_base=str(data.get("reference_base", "")),
            text=str(data.get("text", "")),
            measurement_detail=str(data.get("measurement_detail", "")),
            decomposition=[
                (str(code), float(quantity))
                for code, quantity in data.get("decomposition", [])
            ],
            component_prices={
                str(code): float(price)
                for code, price in data.get("component_prices", {}).items()
            },
            concept_type=str(data.get("concept_type", "")),
            component_types={
                str(code): str(kind)
                for code, kind in data.get("component_types", {}).items()
            },
            component_concepts={
                str(code): Concept(
                    code=str(code), unit=str(concept.get("unit", "")),
                    summary=str(concept.get("summary", "")),
                    price=float(concept.get("price", 0.0)),
                    ctype=str(concept.get("ctype", "0")),
                    text=str(concept.get("text", "")),
                    children=[(str(child), float(quantity)) for child, quantity in concept.get("children", [])],
                )
                for code, concept in data.get("component_concepts", {}).items()
            },
            measurement_lines=measurement_lines,
            planned_amounts={
                str(month): float(amount)
                for month, amount in data.get("planned_amounts", {}).items()
            },
            certifications={
                str(month): float(amount)
                for month, amount in data.get("certifications", {}).items()
            },
            certification_plans={
                str(month): float(amount)
                for month, amount in data.get("certification_plans", {}).items()
            },
            certification_justifications={
                str(month): str(text)
                for month, text in data.get("certification_justifications", {}).items()
            },
        )

    def _project_payload(self) -> dict:
        """Construye el diccionario JSON que representa el estado persistente del proyecto."""
        return {
            "format_version": 2,
            "title": self._project_title(),
            "source_budget_bc3": str(self.source_budget_path.resolve()) if self.source_budget_path else None,
            "chapters": {
                "order": self.chapter_order,
                "titles": self.chapter_titles,
            },
            "planning": self.planning_state,
            "invoicing": self.invoicing_state,
            "rcds": self.rcd_state,
            "safety_health": self.safety_health_state,
            "report_settings": self.report_settings.to_dict(),
            "items": [
                {
                    "code": item.code,
                    "chapter": item.chapter,
                    "unit": item.unit,
                    "summary": item.summary,
                    "measurement": item.measurement,
                    "unit_price": item.unit_price,
                    "round_import_total": item.round_import_total,
                    "direct_unit_price": item.direct_unit_price,
                    "rendimiento": item.rendimiento,
                    "source": item.source,
                    "reference_base": item.reference_base,
                    "text": item.text,
                    "measurement_detail": item.measurement_detail,
                    "component_prices": item.component_prices,
                    "concept_type": item.concept_type,
                    "component_types": item.component_types,
                    "component_concepts": {
                        code: {
                            "unit": concept.unit, "summary": concept.summary,
                            "price": concept.price, "ctype": concept.ctype,
                            "text": concept.text, "children": concept.children,
                        }
                        for code, concept in item.component_concepts.items()
                    },
                    "planned_amounts": item.planned_amounts,
                    "certifications": item.certifications,
                    "certification_plans": item.certification_plans,
                    "certification_justifications": item.certification_justifications,
                    "measurement_lines": [
                        {
                            "comment": line.comment,
                            "units": line.units,
                            "length": line.length,
                            "width": line.width,
                            "height": line.height,
                            "k": line.k,
                        }
                        for line in item.measurement_lines
                    ],
                    "decomposition": item.decomposition,
                }
                for item in self.budget_items
            ],
        }

    def _load_project_file(
        self,
        project_path: Path,
        budget_dataset: Bc3Dataset | None = None,
        source_budget_path: Path | None = None,
    ) -> bool:
        """Valida y restaura un proyecto JSON, sus partidas, capítulos y configuración."""
        missing_source: Path | None = None
        try:
            raw_payload = json.loads(project_path.read_text(encoding="utf-8"))
            payload = {"items": raw_payload} if isinstance(raw_payload, list) else raw_payload
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("El archivo no contiene una lista de partidas válida.")
            report_settings = ReportSettings.from_dict(
                payload.get("report_settings", self.default_report_settings.to_dict())
            )
            from invoicing import InvoiceState

            invoicing_state = InvoiceState.from_dict(payload.get("invoicing", {})).to_dict()
            from rcd import RcdState

            raw_rcds = payload.get("rcds", {})
            rcd_state = RcdState.from_dict(raw_rcds).to_dict() if raw_rcds else {}
            if not isinstance(raw_rcds, dict):
                raise ValueError("Los datos RCDs deben ser un objeto JSON.")
            from safety_health import restore_state, serialize_state

            raw_safety = payload.get("safety_health", {})
            if not isinstance(raw_safety, dict):
                raise ValueError("Los datos de Seguridad y Salud deben ser un objeto JSON.")
            safety_health_state = (
                serialize_state(*restore_state(raw_safety)) if raw_safety else {}
            )
            if source_budget_path is not None:
                declared_source = payload.get("source_budget_bc3")
                if declared_source is not None and Path(declared_source).name != source_budget_path.name:
                    raise ValueError(
                        f"El proyecto está asociado a '{declared_source}', no a '{source_budget_path.name}'."
                    )
            elif payload.get("source_budget_bc3"):
                source_budget_path = Path(payload["source_budget_bc3"])
                if not source_budget_path.is_absolute():
                    source_budget_path = project_path.parent / source_budget_path
                if not source_budget_path.is_file():
                    local_source = project_path.parent / source_budget_path.name
                    if local_source.is_file():
                        source_budget_path = local_source
                    else:
                        missing_source = source_budget_path
                if missing_source is None:
                    budget_dataset = Bc3Dataset(source_budget_path)
            items = [
                self._item_from_project_data(entry)
                for entry in payload["items"]
                if isinstance(entry, dict)
            ]
            if len(items) != len(payload["items"]):
                raise ValueError("Hay una partida con datos no válidos.")
            for item in items:
                item.set_indirect_costs(report_settings.indirect_costs)
            chapters = payload.get("chapters", {})
            order = chapters.get("order", []) if isinstance(chapters, dict) else []
            chapter_order = [str(chapter) for chapter in order]
            for item in items:
                if item.chapter not in chapter_order:
                    chapter_order.append(item.chapter)
            titles = chapters.get("titles", {}) if isinstance(chapters, dict) else {}
            chapter_titles = (
                {str(chapter): str(title) for chapter, title in titles.items()}
                if isinstance(titles, dict)
                else {}
            )
            if budget_dataset is not None:
                for code, concept in budget_dataset.concepts.items():
                    if is_chapter_code(code) and not code.endswith("##"):
                        chapter_titles.setdefault(code, concept.summary)
            planning = payload.get("planning", {})
            planning_state = planning if isinstance(planning, dict) else {}
            title = payload.get("title")
            project_title = str(title) if title else None
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            messagebox.showerror("No se pudo abrir el proyecto", f"El archivo no es válido:\n{exc}")
            return False

        self._dismiss_project_windows()
        self._dismiss_invoicing()
        self._dismiss_rcds()
        self._dismiss_safety_health()
        self.invoicing_state = invoicing_state
        self.budget_items = items
        self.chapter_order = chapter_order
        self.chapter_titles = chapter_titles
        self.planning_state = planning_state
        self.project_title = project_title
        self.rcd_state = rcd_state
        self.safety_health_state = safety_health_state
        self.budget_dataset = budget_dataset
        self.report_settings = report_settings
        self.project_path = project_path
        self.source_budget_path = source_budget_path
        self.selected_budget_index = None
        self.selected_chapter = None
        self._collapse_chapters_on_refresh = True
        self.refresh_budget_tree()
        self._show_item_details(None)
        self.project_panel_title.set(f"Proyecto: {self._project_title()}")
        self._reset_history()
        self._remember_recent_project(project_path)
        if missing_source is not None:
            messagebox.showwarning(
                "BC3 de origen no encontrado",
                f"El proyecto se ha abierto, pero no se encuentra su BC3:\n{missing_source}\n\n"
                "Los informes de recursos y descompuestos necesitan los precios de esa base "
                "o los precios guardados en el proyecto.",
            )
        self._set_status(f"Proyecto abierto: {project_path.name}")
        return True

    def load_project_dialog(self) -> None:
        """Abre el selector de archivos para elegir un proyecto JSON guardado."""
        file_path = filedialog.askopenfilename(
            title="Abrir proyecto guardado",
            filetypes=[("Proyecto JSON", "*.json")],
        )
        if file_path and self._confirm_project_change():
            self._load_project_file(Path(file_path))

    def save_project(self) -> bool:
        """Guarda el proyecto actual, solicitando una ruta si aún no tiene ninguna."""
        return self._save_project(choose_path=False)

    def save_project_as(self) -> bool:
        """Permite elegir siempre carpeta y nombre para guardar el proyecto JSON."""
        return self._save_project(choose_path=True)

    def _save_project(self, *, choose_path: bool) -> bool:
        """Valida las ediciones y guarda en la ruta actual o en una elegida."""
        if self._measurement_editor is not None or self._component_editor is not None:
            messagebox.showwarning(
                "Edición pendiente",
                "Confirma la celda con Intro o cancélala con Escape antes de guardar el proyecto.",
                parent=self.root,
            )
            return False
        path = self.project_path
        if choose_path or path is None:
            file_path = filedialog.asksaveasfilename(
                parent=self.root,
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
                title="Guardar como (JSON)…",
                initialdir=str(path.parent if path else Path.cwd()),
                initialfile=path.name if path else "proyecto.json",
                confirmoverwrite=True,
            )
            if not file_path:
                return False
            path = Path(file_path)
        if not self._write_project_file(path):
            return False
        self._set_status(f"Proyecto guardado en {self.project_path}")
        return True

    def _budget_html(self) -> str:
        """Genera el presupuesto en HTML para vista previa o impresión."""
        esc = html.escape
        title = self._project_title()
        rows: list[str] = []
        chapter_totals: dict[str, float] = {}
        for item in self.budget_items:
            chapter_totals[item.chapter] = chapter_totals.get(item.chapter, 0.0) + item.import_total

        seen: set[str] = set()
        for item in self.budget_items:
            if item.chapter not in seen:
                seen.add(item.chapter)
                rows.append(
                    f'<tr class="cap"><td>{esc(format_chapter_code(item.chapter))}</td>'
                    f'<td colspan="4">{esc(self._chapter_title(item.chapter))}</td>'
                    f'<td class="num">{esc(self._format_currency(chapter_totals[item.chapter]))}</td></tr>'
                )
            rows.append(
                f"<tr><td>{esc(item.code)}</td><td>{esc(item.unit)}</td>"
                f'<td class="txt">{esc(item.text or item.summary)}</td>'
                f'<td class="num">{fmt_number(item.measurement)}</td>'
                f'<td class="num">{esc(self._format_currency(item.unit_price))}</td>'
                f'<td class="num">{esc(self._format_currency(item.import_total))}</td></tr>'
            )

        total = sum(item.import_total for item in self.budget_items)
        summary = "".join(
            f'<tr><td>{esc(format_chapter_code(code))} · {esc(self._chapter_title(code))}</td>'
            f'<td class="num">{esc(self._format_currency(amount))}</td></tr>'
            for code, amount in chapter_totals.items()
        )
        return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>
 body {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 11px; margin: 24px; color: #1a1a1a; }}
 h1 {{ font-size: 18px; margin-bottom: 2px; }}
 .sub {{ color: #666; margin-bottom: 14px; }}
 table {{ border-collapse: collapse; width: 100%; }}
 th, td {{ border: 1px solid #c8d0da; padding: 3px 5px; vertical-align: top; }}
 th {{ background: #1f4e78; color: #fff; }}
 tr.cap td {{ background: #dce6f1; font-weight: bold; }}
 td.num {{ text-align: right; white-space: nowrap; }}
 td.txt {{ max-width: 520px; }}
 tfoot td {{ font-weight: bold; background: #f2f5f9; }}
 @media print {{ body {{ margin: 8mm; }} }}
</style></head><body>
<h1>{esc(title)}</h1>
<div class="sub">Presupuesto y mediciones · {date.today():%d/%m/%Y}</div>
<table><thead><tr><th>Código</th><th>Ud</th><th>Descripción</th><th>Medición</th><th>Precio</th><th>Importe</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
<tfoot><tr><td colspan="5">TOTAL EJECUCIÓN MATERIAL</td><td class="num">{esc(self._format_currency(total))}</td></tr></tfoot></table>
<h1 style="margin-top:22px">Resumen por capítulos</h1>
<table><tbody>{summary}</tbody>
<tfoot><tr><td>TOTAL</td><td class="num">{esc(self._format_currency(total))}</td></tr></tfoot></table>
</body></html>"""

    def _project_title(self) -> str:
        """Obtiene el título del proyecto o lo deduce del concepto raíz del BC3."""
        if self.project_title:
            return self.project_title
        if self.budget_dataset:
            for code, concept in self.budget_dataset.concepts.items():
                if code.endswith("##") and concept.summary:
                    return concept.summary
        return "Presupuesto"

    def _write_report(self) -> Path | None:
        """Genera el HTML del presupuesto en un archivo para vista previa o impresión."""
        if not self.budget_items:
            messagebox.showinfo("Presupuesto vacío", "No hay partidas que imprimir.")
            return None
        path = Path(tempfile.gettempdir()) / "presupuesto_bc3.html"
        path.write_text(self._budget_html(), encoding="utf-8")
        return path

    def preview_budget(self):
        """Abre en el navegador la vista previa HTML del presupuesto."""
        path = self._write_report()
        if path is None:
            return
        webbrowser.open(path.as_uri())
        self._set_status("Vista previa del presupuesto abierta en el navegador.")

    def print_budget(self):
        """Envía el informe del presupuesto a imprimir o abre una vista previa alternativa."""
        path = self._write_report()
        if path is None:
            return
        try:
            os.startfile(str(path), "print")  # type: ignore[attr-defined]
            self._set_status("Presupuesto enviado a la impresora.")
        except OSError:
            webbrowser.open(path.as_uri())
            messagebox.showinfo(
                "Imprimir",
                "No se pudo imprimir directamente. Se ha abierto la vista previa: "
                "usa Ctrl+P en el navegador.",
            )

    def export_budget_ods(self):
        """Exporta partidas, capítulos, mediciones e importes a un libro ODS."""
        if not self.budget_items:
            messagebox.showinfo("Presupuesto vacío", "No hay partidas que exportar.")
            return
        file_path = filedialog.asksaveasfilename(
            defaultextension=".ods", filetypes=[("Hoja de cálculo ODS", "*.ods")],
            title="Exportar presupuesto",
        )
        if not file_path:
            return
        target = Path(file_path)
        if target.suffix.lower() != ".ods":
            target = target.with_suffix(".ods")
        try:
            from ods_export import write_ods

            budget_rows = [
                ["Capítulo", "Código", "Ud", "Descripción", "Medición", "Precio", "Importe"],
                *[
                    [
                        format_chapter_code(item.chapter), item.code, item.unit,
                        item.text or item.summary, item.measurement, item.unit_price, item.import_total,
                    ]
                    for item in self.budget_items
                ],
                ["", "", "", "TOTAL", "", "",
                 sum(item.import_total for item in self.budget_items)],
            ]
            measurement_rows = [
                ["Código", "Descripción", "Uds", "Longitud", "Anchura", "Altura", "K", "Parcial"],
                *[
                    [
                        item.code, line.comment, line.units, line.length, line.width,
                        line.height, line.k, line.partial,
                    ]
                    for item in self.budget_items
                    for line in item.measurement_lines
                ],
            ]
            write_ods(target, [
                {"name": "Presupuesto", "rows": budget_rows, "widths": (12, 14, 6, 70, 12, 12, 14)},
                {"name": "Mediciones", "rows": measurement_rows, "widths": (14, 50, 12, 12, 12, 12, 10, 14)},
            ])
        except ImportError as exc:
            messagebox.showerror(
                "Falta odfpy", f"Instala las dependencias con:\npython -m pip install -r requirements.txt\n\n{exc}",
            )
            return
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo exportar el presupuesto", str(exc))
            return
        self._set_status(f"Presupuesto exportado a {target}")

    def _report_components(self, item: ProjectItem) -> list[tuple[Concept, float, float]]:
        """Usa los mismos precios editados que el detalle de descomposición."""
        dataset = self._component_dataset(item)
        rows = item.cost_components(dataset)
        return rows + item.indirect_components()

    def export_resource_report(self, report: str) -> None:
        """Exporta recursos o descompuestos a una hoja de cálculo ODS."""
        if not self.budget_items:
            messagebox.showinfo("Presupuesto vacío", "No hay partidas que exportar.")
            return
        file_path = filedialog.asksaveasfilename(
            defaultextension=".ods", filetypes=[("Hoja de cálculo ODS", "*.ods")],
            title=f"Exportar {report}",
        )
        if not file_path:
            return
        target = Path(file_path)
        if target.suffix.lower() != ".ods":
            target = target.with_suffix(".ods")
        resources = report == "Recursos"
        headers = (
            ["Código", "Tipo", "Ud", "Resumen", f"Precio ({self._currency_label()})", "Fuente", "Medición"] if resources else
            ["Partida", "Recurso", "Tipo", "Ud", "Resumen", "Rendimiento (ud/ud partida)",
             f"Precio ({self._currency_label()})", f"Importe ({self._currency_label()})", "Fuente"]
        )
        widths = (14, 7, 6, 60, 12, 12, 16) if resources else (14, 14, 7, 6, 55, 18, 12, 14, 12)
        rows = [headers]
        number_formats = {}
        if resources:
            number_formats.update({4: 2, 6: 4})
        else:
            number_formats.update({5: 4, 6: 2, 7: 2})
        resource_measurements: dict[tuple, Decimal] = {}
        try:
            for item in self.budget_items:
                for concept, quantity, price in self._report_components(item):
                    rtype = {
                        "%": "%",
                        "1": "MO", "2": "MAQ", "3": "MAT",
                    }.get(self._budget_concept_type(item, concept.code), "AUX")
                    resource = (concept.code, rtype, concept.unit, concept.summary, price, item.source)
                    # Un mismo código puede tener precios editados distintos por partida.
                    if resources:
                        measurement = Decimal(str(item.measurement)) * Decimal(str(quantity))
                        resource_measurements[resource] = (
                            resource_measurements.get(resource, Decimal(0)) + measurement
                        )
                    else:
                        rows.append((
                            item.code, *resource[:4], quantity, price,
                            round_half_up(Decimal(str(quantity)) * Decimal(str(price))), item.source
                        ))
            for resource, measurement in resource_measurements.items():
                rows.append((*resource, float(measurement)))
            from ods_export import write_ods

            write_ods(target, [{
                "name": report,
                "rows": rows,
                "widths": widths,
                "header": True,
                "number_formats": number_formats,
            }])
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo exportar el informe", str(exc))
            return
        except ImportError as exc:
            messagebox.showerror(
                "Falta odfpy", f"Instala las dependencias con:\npython -m pip install -r requirements.txt\n\n{exc}",
            )
            return
        self._set_status(f"{report} exportado a {target}")

    def _price_report_html(self, number: int) -> str:
        """Genera el HTML del cuadro de precios simple o descompuesto indicado."""
        esc = html.escape
        rows: list[str] = []
        for item in self.budget_items:
            description = (
                f'<td colspan="3">{esc(item.text or item.summary)}</td>' if number == 2
                else f'<td>{esc(item.text or item.summary)}</td>'
            )
            rows.append(
                f'<tr class="partida"><td>{esc(item.code)}</td><td>{esc(item.unit)}</td>'
                f'{description}'
                f'<td class="num">{html.escape(self._format_currency(item.unit_price))}</td></tr>'
            )
            if number == 2:
                components = self._report_components(item)
                for concept, quantity, price in components:
                    rows.append(
                        f'<tr><td>{esc(concept.code)}</td><td>{esc(concept.unit)}</td>'
                        f'<td>{esc(concept.summary)}</td><td class="num">{fmt_number(quantity, 4)}</td>'
                        f'<td class="num">{html.escape(self._format_currency(price))}</td>'
                        f'<td class="num">{html.escape(self._format_currency(round_half_up(Decimal(str(quantity)) * Decimal(str(price)))))}</td></tr>'
                    )
                if not components:
                    rows.append('<tr><td colspan="6">Sin descomposición.</td></tr>')
                else:
                    component_total = sum(
                        (Decimal(str(quantity)) * Decimal(str(price))).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP,
                        )
                        for _, quantity, price in components
                    )
                    rows.append(
                        '<tr><td colspan="5">Total descompuesto</td>'
                        f'<td class="num">{html.escape(self._format_currency(component_total))}</td></tr>'
                    )
                    if round_half_up(component_total) != round_half_up(item.unit_price):
                        rows.append(
                            '<tr><td colspan="5">Diferencia respecto al precio unitario</td>'
                            f'<td class="num">{html.escape(self._format_currency(Decimal(str(item.unit_price)) - component_total))}</td></tr>'
                        )
        title = f"Cuadro de precios Nº{number}"
        headers = (
            "<th>Código</th><th>Ud</th><th>Descripción</th><th>Rendimiento</th>"
            "<th>Precio</th><th>Importe unitario</th>" if number == 2 else
            "<th>Código</th><th>Ud</th><th>Descripción</th><th>Precio unitario</th>"
        )
        return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>
body {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 11px; margin: 24px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #c8d0da; padding: 4px; vertical-align: top; white-space: pre-line; }}
th {{ background: #1f4e78; color: white; }}
.partida {{ background: #dce6f1; font-weight: bold; }}
.num {{ text-align: right; white-space: nowrap; }}
@media print {{ body {{ margin: 8mm; }} thead {{ display: table-header-group; }} }}
</style></head><body><h1>{esc(self._project_title())}</h1>
<h2>{esc(title)}</h2><p>{date.today():%d/%m/%Y} · Imprimir: Ctrl+P</p>
<table><thead><tr>{headers}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""

    def preview_price_report(self, number: int) -> None:
        """Crea y abre en el navegador la vista imprimible del cuadro de precios."""
        if not self.budget_items:
            messagebox.showinfo("Presupuesto vacío", "No hay partidas que imprimir.")
            return
        try:
            report = self._price_report_html(number)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".html", prefix=f"cuadro_precios_{number}_",
                delete=False,
            ) as output:
                output.write(report)
                path = Path(output.name)
            if not webbrowser.open(path.as_uri()):
                messagebox.showerror("Informe", f"No se pudo abrir el navegador. Abre el informe:\n{path}")
                return
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo generar el informe", str(exc))
            return
        self._set_status(f"Cuadro de precios Nº{number} abierto en el navegador. Usa Ctrl+P para imprimir.")

    def export_measurements_csv(self):
        """Exporta las líneas de medición y las mediciones manuales a un archivo CSV."""
        if not self.budget_items:
            messagebox.showinfo("Presupuesto vacío", "No hay mediciones que exportar.")
            return
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], title="Exportar mediciones"
        )
        if not file_path:
            return
        with open(file_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["Capítulo", "Código", "Descripción", "Uds", "Longitud", "Anchura", "Altura", "K", "Parcial"])
            for item in self.budget_items:
                for line in item.measurement_lines:
                    writer.writerow([
                        format_chapter_code(item.chapter), item.code, line.comment,
                        line.units, line.length, line.width, line.height, line.k, line.partial,
                    ])
                if not item.measurement_lines:
                    writer.writerow([
                        format_chapter_code(item.chapter), item.code, item.summary,
                        "", "", "", "", "", item.measurement,
                    ])
        self._set_status(f"Mediciones exportadas a {file_path}")

    def open_planning(self):
        """Abre la ventana de planificación vinculada al presupuesto y su estado guardado."""
        if not self.budget_items:
            messagebox.showinfo("Planificación", "Carga o crea un presupuesto antes de planificar.")
            return
        from planning import PlanningWindow

        window = PlanningWindow(
            self.root,
            self.budget_items,
            self._chapter_title,
            self._project_title(),
            saved_state=self.planning_state,
            on_state_change=self._store_planning_state,
            on_save=self.save_project,
            on_dirty_change=lambda dirty: self._mark_dirty() if dirty else None,
            currency_formatter=self._format_currency,
        )
        self._project_windows.append(window)

    def _store_planning_state(self, state: dict) -> None:
        """Actualiza el estado de planificación recibido desde su ventana de edición."""
        self.planning_state = state

    def open_certifications(self):
        """Abre la ventana de certificaciones con las partidas y planificación actuales."""
        if not self.budget_items:
            messagebox.showinfo(
                "Certificaciones", "Carga o crea un presupuesto antes de gestionar certificaciones."
            )
            return
        from certifications import CertificationWindow

        window = CertificationWindow(
            self.root,
            self.budget_items,
            self._chapter_title,
            self._project_title(),
            planning_state=self.planning_state,
            report_settings=lambda: self.report_settings,
            on_save=self.save_project,
            on_dirty_change=lambda dirty: self._mark_dirty() if dirty else None,
        )
        self._project_windows.append(window)

    def _dismiss_project_windows(self) -> None:
        """Cierra vistas con referencias a partidas que van a ser sustituidas."""
        for window in self._project_windows:
            if window.winfo_exists():
                window.destroy()
        self._project_windows.clear()

    def _dismiss_rcds(self) -> None:
        """Cierra la herramienta RCDs antes de sustituir el proyecto activo."""
        if self._rcd_window is not None and self._rcd_window.winfo_exists():
            self._rcd_window.destroy()
        self._rcd_window = None

    def _has_external_tool_chapter(self, tool: str) -> bool:
        """Detecta capítulos de la especialidad que no fueron generados por su herramienta."""
        prefix = "RCD:" if tool == "rcd" else "SS:"
        chapters = set(self.chapter_order) | set(self.chapter_titles)
        chapters.update(item.chapter for item in self.budget_items)
        for chapter in chapters:
            title = self._chapter_title(chapter)
            normalized = unicodedata.normalize("NFKD", f"{chapter} {title}".casefold())
            text = re.sub(r"[^a-z0-9]+", " ", "".join(
                char for char in normalized if not unicodedata.combining(char)
            )).strip()
            code = re.sub(r"[^a-z0-9]", "", chapter.casefold())
            if tool == "rcd":
                matches = (re.search(r"\b(?:rcds?|residuos?|escombros?)\b", text)
                           or re.search(r"\br\s+c\s+d\s*s?\b", text)
                           or re.fullmatch(r"rcds?\d*", code))
            else:
                matches = (re.search(
                    r"\b(?:seguridad|salud)\b|\bprevencion\b.*\briesgos\b|"
                    r"\bs\s+(?:y\s+)?s\b",
                    text,
                ) or re.fullmatch(r"(?:ss|sys)\d*", code))
            members = [item for item in self.budget_items if item.chapter == chapter]
            if matches and not any(item.source.startswith(prefix) for item in members):
                return True
        return False

    def _refresh_tool_menu(self) -> None:
        """Desactiva las herramientas que duplicarían capítulos importados."""
        for index, tool in enumerate(("rcd", "safety")):
            self.tools_menu.entryconfigure(
                index, state="disabled" if self._has_external_tool_chapter(tool) else "normal"
            )

    def _tool_chapter_available(self, tool: str) -> bool:
        """Impide también el acceso directo a herramientas con capítulos ajenos."""
        if not self._has_external_tool_chapter(tool):
            return True
        messagebox.showwarning(
            "RCDs" if tool == "rcd" else "Seguridad y salud",
            "El presupuesto ya contiene un capítulo de esta especialidad no generado "
            "por la herramienta. Se conserva sin recalcular ni crear otro capítulo.",
            parent=self.root,
        )
        return False

    def _dismiss_safety_health(self) -> None:
        """Cierra la ventana de Seguridad y Salud antes de cambiar de proyecto."""
        if self._safety_health_window is not None and self._safety_health_window.winfo_exists():
            self._safety_health_window.destroy()
        self._safety_health_window = None

    def open_rcds(self) -> None:
        """Abre la estimación de residuos y sus datos guardados en el proyecto."""
        if not self._tool_chapter_available("rcd"):
            return
        if not self.budget_items:
            messagebox.showinfo("RCDs", "Carga o crea un presupuesto antes de estimar residuos.")
            return
        if self._rcd_window is not None and self._rcd_window.winfo_exists():
            self._rcd_window.lift()
            return
        from rcd import RcdWindow

        self._rcd_window = RcdWindow(self.root, self)

    def open_safety_health(self) -> None:
        """Propone y revisa las partidas de Seguridad y Salud del presupuesto activo."""
        if not self._tool_chapter_available("safety"):
            return
        if not self.budget_items:
            messagebox.showinfo(
                "Seguridad y salud",
                "Carga o crea un presupuesto antes de analizar Seguridad y Salud.",
            )
            return
        if self._safety_health_window is not None and self._safety_health_window.winfo_exists():
            self._safety_health_window.lift()
            return
        from safety_health import SafetyHealthWindow

        self._safety_health_window = SafetyHealthWindow(self.root, self)

    def _dismiss_invoicing(self) -> None:
        """Cierra la ventana y su lectura pendiente antes de cambiar de proyecto."""
        window = self._invoicing_window
        if window is not None and window.winfo_exists():
            window.destroy()
        self._invoicing_window = None

    def open_invoicing(self) -> None:
        """Abre la facturación local vinculada al proyecto actual."""
        if not self.budget_items:
            messagebox.showinfo("Facturación", "Carga o crea un presupuesto antes de gestionar facturas.")
            return
        if self._invoicing_window is not None and self._invoicing_window.winfo_exists():
            self._invoicing_window.lift()
            return
        from invoicing import InvoiceState, InvoicingWindow

        def store(state: InvoiceState) -> None:
            """Conserva las ediciones de facturación y marca el proyecto modificado."""
            self.invoicing_state = state.to_dict()
            self._mark_dirty()

        self._invoicing_window = InvoicingWindow(
            self.root, self, InvoiceState.from_dict(self.invoicing_state), store, self.save_project,
        )

    def show_application_info(self):
        """Muestra la versión, el repositorio y las funciones principales del programa."""
        messagebox.showinfo(
            "Información de la aplicación",
            f"{APPLICATION_NAME} — v{APPLICATION_VERSION}\n"
            "Presupuestos, planificación y certificaciones de obra\n\n"
            f"Repositorio: {REPOSITORY_URL}\n\n"
            "Gestión de presupuestos BC3, bases BCCA, mediciones, "
            "planificación económica y certificaciones.",
            parent=self.root,
        )

    def open_repository_page(self) -> None:
        """Abre el repositorio del proyecto en el navegador predeterminado."""
        self._open_web_page(
            REPOSITORY_URL, "Repositorio en GitHub",
            "Repositorio abierto en el navegador.",
        )

    def _open_web_page(self, url: str, title: str, success_status: str) -> None:
        """Abre una web e informa si no se puede iniciar el navegador."""
        try:
            if not webbrowser.open(url):
                messagebox.showerror(
                    title,
                    translate("No se pudo abrir el navegador. Visita:\n{url}").format(url=url),
                    parent=self.root,
                )
                return
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                title,
                translate("No se pudo abrir el navegador:\n{error}\n\nVisita:\n{url}").format(
                    error=exc, url=url,
                ),
                parent=self.root,
            )
            return
        self._set_status(success_status)

    def open_donation_page(self) -> None:
        """Abre la página de apoyo del proyecto en el navegador predeterminado."""
        try:
            if not webbrowser.open(BUY_ME_A_COFFEE_URL):
                messagebox.showerror(
                    "Apoyar el proyecto",
                    f"No se pudo abrir el navegador. Visita:\n{BUY_ME_A_COFFEE_URL}",
                    parent=self.root,
                )
                return
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Apoyar el proyecto",
                f"No se pudo abrir el navegador:\n{exc}\n\nVisita:\n{BUY_ME_A_COFFEE_URL}",
                parent=self.root,
            )
            return
        self._set_status("Página de apoyo abierta en el navegador.")

    def show_license_info(self) -> None:
        """Presenta el resumen de la licencia MIT y el aviso de responsabilidad técnica."""
        messagebox.showinfo(
            "Licencia MIT y responsabilidad",
            f"{APPLICATION_NAME}\n\n"
            "Software libre · Licencia MIT\n\n"
            "La licencia MIT permite usar, copiar, modificar, combinar, publicar, "
            "distribuir, sublicenciar y vender copias del software. El aviso de "
            "copyright y el aviso de permiso deben conservarse en todas las copias "
            "o partes sustanciales del software.\n\n"
            "AUSENCIA DE GARANTÍAS\n"
            "El software se proporciona «tal cual», sin garantías de ningún tipo, "
            "expresas o implícitas, incluidas las de comerciabilidad, idoneidad "
            "para un fin particular y no infracción.\n\n"
            "LIMITACIÓN DE RESPONSABILIDAD\n"
            "En la medida permitida por la legislación aplicable, los autores o "
            "titulares del copyright no serán responsables de reclamaciones, daños "
            "u otras responsabilidades, ya sea por contrato, responsabilidad "
            "extracontractual u otra causa, derivados del software, de su uso "
            "o de otras operaciones con él.\n\n"
            "La persona usuaria debe comprobar los datos, cálculos, mediciones, "
            "presupuestos, certificaciones e informes antes de utilizarlos. "
            "El programa no sustituye la revisión técnica ni la responsabilidad "
            "profesional correspondiente.\n\n"
            "Este aviso resume la licencia MIT. Texto íntegro:\n"
            "https://opensource.org/license/mit",
            parent=self.root,
        )

    def _set_status(self, text: str):
        """Mantiene el título del marco de la ventana al actualizar el estado."""
        self.root.title(translate("Aplicación"))


def show_startup_screen(root: tk.Tk) -> None:
    """Muestra la imagen de arranque centrada durante dos segundos."""
    from PIL import Image, ImageTk

    image_path = application_directory(__file__) / "iconos" / "Arranque.jpg"
    try:
        with Image.open(image_path) as source:
            resized = source.resize((612, 245), Image.Resampling.LANCZOS)
            image = ImageTk.PhotoImage(resized, master=root)
    except OSError as exc:
        messagebox.showwarning(
            "Imagen de arranque",
            f"No se pudo cargar la imagen de arranque:\n{exc}",
            parent=root,
        )
        return

    splash = tk.Toplevel(root)
    splash.withdraw()
    splash.overrideredirect(True)
    tk.Label(splash, image=image, borderwidth=0, highlightthickness=0).pack()
    x = max(0, (splash.winfo_screenwidth() - image.width()) // 2)
    y = max(0, (splash.winfo_screenheight() - image.height()) // 2)
    splash.geometry(f"{image.width()}x{image.height()}+{x}+{y}")
    splash.deiconify()
    splash.wait_visibility()
    splash.after(2000, splash.destroy)
    root.wait_window(splash)


def main():
    """Muestra el arranque y después inicia la ventana principal de Tkinter."""
    root = tk.Tk()
    root.withdraw()
    show_startup_screen(root)
    app = BudgetApp(root)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
