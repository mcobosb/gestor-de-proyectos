"""Modelos, cálculos y parser FIEBDC independientes de la interfaz."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from pathlib import Path
import re

CURRENCY_SYMBOLS = {
    "EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF",
    "CAD": "CA$", "AUD": "A$",
}

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


def find_bc3_files(folder: Path) -> tuple[Path | None, Path | None]:
    """Localiza una base BCCA y un presupuesto dentro de una carpeta."""
    files = sorted(path for path in folder.glob("*.bc3") if path.is_file())
    base = next(
        (path for path in files if path.name.upper().startswith("BCCA")),
        None,
    )
    budget = next((path for path in files if path is not base), None)
    return base, budget


def find_reference_files(folder: Path) -> list[Path]:
    """Devuelve todas las bases BCCA de una carpeta."""
    return sorted(
        path
        for path in folder.glob("*.bc3")
        if path.is_file() and path.name.upper().startswith("BCCA")
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
