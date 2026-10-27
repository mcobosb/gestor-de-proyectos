"""Genera un Excel de planificación (presupuesto + rendimientos + diagrama de Gantt)
a partir de un presupuesto en formato FIEBDC-3 (.bc3).

Uso:
    python bc3_to_xlsx_plan.py [archivo.bc3] [salida.xlsx]

Sin archivo de entrada, busca un único .bc3 en la carpeta del script.
Si no hay ninguno o hay varios, muestra un error y no genera el Excel.
La salida predeterminada es <nombre_del_bc3>_planificacion.xlsx.

Hojas generadas:
    Config         Fecha de inicio, horas por jornada, escala del Gantt y festivos.
    Presupuesto    Partidas por capítulo: medición, precio, importe, costes MO/MAQ/MAT
                   y rendimientos (h/ud) calculados a partir de los descompuestos.
    Descompuestos  Recursos de cada partida con su rendimiento (editable).
    Recursos       Precios unitarios de mano de obra, maquinaria y materiales.
    Capítulos      Resumen por capítulo y comprobación contra el total del BC3.
    Planificación  Actividades: nº de equipos, duración, predecesoras (FC/CC/FF/CF
                   con desfase), "toda la obra" y fechas calculadas.
    Gantt          Diagrama de barras con formato condicional (escala día/semana).
    Plan económico Distribución mensual del importe y curva de avance acumulado.

Si el .bc3 no trae descomposición de una partida, se toma de la base BCCA2023
(xlsx de partidas y de precios básicos). Si el Excel de salida ya existe, se
conservan los datos introducidos en "Planificación" (equipos, duraciones manuales,
predecesoras...) y se guarda una copia .bak del anterior.

Versión: 1.0.0
Autor: Manuel Cobos Budia, Ingeniero de Caminos, Canales y Puertos Municipal,
Servicio de Infraestructuras y Obras del Excelentísimo Ayuntamiento de Lucena.
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.chart import BarChart, LineChart, Reference
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as col
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Falta la dependencia 'openpyxl'. Instálala con: pip install openpyxl") from exc


# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent


def find_unique_input(directory: Path, suffix: str) -> Path:
    """Selecciona un único fichero de entrada o informa de archivos ausentes o ambiguos."""
    candidates = sorted(
        (
            path for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() == suffix.lower()
        ),
        key=lambda path: path.name.lower(),
    )
    if not candidates:
        raise SystemExit(f"Error: no se encontró ningún archivo {suffix} en {directory}.")
    if len(candidates) > 1:
        names = "\n".join(f"  - {path.name}" for path in candidates)
        raise SystemExit(
            f"Error: se encontró más de un archivo {suffix} en {directory}:\n{names}\n"
            "Deja un único archivo de entrada o indica su ruta como argumento."
        )
    return candidates[0]


BCCA_SEARCH_DIRS = [
    BASE_DIR,
    Path.home() / "OneDrive" / "Ayto Lucena" / "docs" / "bases_precios",
]
WORKITEMS_XLSX_NAME = "01_partidas_por_capitulo_BCCA2023_completo.xlsx"
BASICPRICES_XLSX_NAME = "02_bas_por_capitulo_BCCA2023.xlsx"

DEFAULT_START = date(2026, 11, 9)
HOURS_PER_DAY = 8
EXTRA_ROWS = 30          # filas vacías preparadas para añadir actividades
GANTT_COLUMNS = 200      # columnas de la escala temporal
ECON_MONTHS = 24         # meses del plan económico

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

TYPE_NAMES = {"1": "MO", "2": "MAQ", "3": "MAT"}

# Estilos
FILL_HEADER = PatternFill("solid", fgColor="1F4E78")
FILL_INPUT = PatternFill("solid", fgColor="FFF2CC")
FILL_CHAPTER = PatternFill("solid", fgColor="D9E1F2")
FILL_WARN = PatternFill("solid", fgColor="F8CBAD")
FILL_BAR = PatternFill("solid", fgColor="2F75B5")
FILL_BAR_CONT = PatternFill("solid", fgColor="F4B183")
FILL_BAR_MILESTONE = PatternFill("solid", fgColor="000000")
FILL_WEEKEND = PatternFill("solid", fgColor="EDEDED")
FONT_HEADER = Font(bold=True, color="FFFFFF")
FONT_BOLD = Font(bold=True)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FMT_EUR = '#,##0.00 "€"'
FMT_NUM = "#,##0.00"
FMT_REND = "0.0000"
FMT_DATE = "dd/mm/yyyy"


# --------------------------------------------------------------------------
# Lectura del BC3
# --------------------------------------------------------------------------
@dataclass
class Concept:
    code: str
    unit: str = ""
    summary: str = ""
    price: float = 0.0
    ctype: str = "0"
    text: str = ""
    children: List[Tuple[str, float]] = field(default_factory=list)  # (código, factor*rend)


@dataclass
class Bc3:
    concepts: Dict[str, Concept]
    measurements: Dict[Tuple[str, str], Tuple[float, int]]  # (padre, hijo) -> (medición, posición)
    root: Optional[str]


def _num(value: str, default: float = 0.0) -> float:
    """Interpreta un decimal con punto o coma y usa el valor indicado si no es válido."""
    try:
        return float(value.replace(",", ".")) if value.strip() else default
    except ValueError:
        return default


def norm_code(code: str) -> str:
    """Normaliza un código eliminando almohadillas, espacios externos y diferencias de caja."""
    return code.replace("#", "").strip().upper()


def read_bc3(path: Path) -> Bc3:
    """Lee conceptos, descomposiciones, mediciones y textos del presupuesto FIEBDC."""
    raw = path.read_bytes()
    head = raw[:300].decode("latin-1", errors="ignore").upper()
    encoding = "cp850" if "|850|" in head else "cp437" if "|437|" in head else "cp1252"
    text = raw.decode(encoding, errors="replace")

    concepts: Dict[str, Concept] = {}
    decomps: Dict[str, List[Tuple[str, float]]] = {}
    measurements: Dict[Tuple[str, str], Tuple[float, int]] = {}
    texts: Dict[str, str] = {}

    for record in re.split(r"(?:\r?\n)?~", text):
        if not record.strip():
            continue
        fields = record.rstrip("\r\n").split("|")
        kind = fields[0].strip().upper()
        if kind == "C" and len(fields) > 1:
            code = fields[1].split("\\")[0].strip()
            c = concepts.setdefault(code, Concept(code))
            c.unit = fields[2].strip() if len(fields) > 2 else ""
            c.summary = fields[3].strip() if len(fields) > 3 else ""
            c.price = _num(fields[4].split("\\")[0]) if len(fields) > 4 else 0.0
            c.ctype = fields[6].strip() if len(fields) > 6 and fields[6].strip() else "0"
        elif kind in ("D", "Y") and len(fields) > 2:
            parent = fields[1].strip()
            parts = fields[2].split("\\")
            items = decomps.setdefault(parent, [])
            if kind == "D":
                items.clear()
            for i in range(0, len(parts) - 2, 3):
                child = parts[i].strip()
                if child:
                    items.append((child, _num(parts[i + 1], 1.0) * _num(parts[i + 2], 1.0)))
        elif kind == "M" and len(fields) > 3:
            path_codes = fields[1].split("\\")
            if len(path_codes) >= 2:
                parent, child = path_codes[0].strip(), path_codes[1].strip()
                pos = [p for p in fields[2].split("\\") if p.strip()]
                position = int(_num(pos[-1])) if pos else 0
                measurements[(norm_code(parent), child)] = (_num(fields[3]), position)
        elif kind == "T" and len(fields) > 2:
            texts[fields[1].strip()] = fields[2].strip()

    for code, items in decomps.items():
        concepts.setdefault(code, Concept(code)).children = items
    for code, txt in texts.items():
        if code in concepts:
            concepts[code].text = txt

    root = next((c for c in concepts if c.endswith("##")), None)
    return Bc3(concepts, measurements, root)


# --------------------------------------------------------------------------
# Bases BCCA (xlsx)
# --------------------------------------------------------------------------
def find_file(name: str) -> Optional[Path]:
    """Busca un fichero de base de precios en las carpetas configuradas."""
    for folder in BCCA_SEARCH_DIRS:
        candidate = folder / name
        if candidate.exists():
            return candidate
    return None


def load_xlsx_table(path: Optional[Path]) -> Dict[str, dict]:
    """Lee las hojas del libro y devuelve sus registros indexados por código."""
    table: Dict[str, dict] = {}
    if path is None:
        return table
    wb = load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(rows, [])]
        for row in rows:
            if not row or row[0] is None:
                continue
            rec = dict(zip(header, row))
            table.setdefault(str(row[0]).strip(), rec)
    wb.close()
    return table


def parse_decomp(text: Optional[str]) -> List[Tuple[str, float]]:
    """Descompone grupos FIEBDC de código, factor y rendimiento en pares de recurso y cantidad."""
    if not text:
        return []
    parts = str(text).split("\\")
    out = []
    for i in range(0, len(parts) - 2, 3):
        if parts[i].strip():
            out.append((parts[i].strip(), _num(parts[i + 1], 1.0) * _num(parts[i + 2], 1.0)))
    return out


# --------------------------------------------------------------------------
# Modelo de datos del presupuesto
# --------------------------------------------------------------------------
@dataclass
class Item:
    id: int
    chapter: str
    chapter_name: str
    code: str
    unit: str
    summary: str
    quantity: float
    price: float
    text: str = ""
    note: str = ""


@dataclass
class Resource:
    code: str
    rtype: str
    unit: str
    summary: str
    price: float
    source: str


def chapter_sort_key(code: str):
    """Obtiene una clave numérica y textual para ordenar capítulos."""
    nums = re.findall(r"\d+", code)
    return [int(n) for n in nums] if nums else [9999], code


def build_items(bc3: Bc3, basics: Dict[str, dict], warnings: List[str]) -> List[Item]:
    """Reconstruye partidas por capítulo y registra advertencias sobre datos incompletos."""
    concepts = bc3.concepts
    chapter_codes = [c for c in concepts if c.endswith("#") and not c.endswith("##")]

    def resolve(code: str) -> str:
        """En FIEBDC los capítulos pueden referenciarse sin el sufijo '#'."""
        for cand in (code, code + "#", code + "##"):
            if cand in concepts:
                return cand
        return code

    for c in concepts.values():
        c.children = [(resolve(ch), q) for ch, q in c.children]

    if bc3.root and concepts[bc3.root].children:
        top = [c for c, _ in concepts[bc3.root].children]
    else:
        top = sorted(chapter_codes, key=chapter_sort_key)
        if bc3.root:
            warnings.append("El concepto raíz no tiene descomposición: capítulos ordenados por código.")

    referenced = set()
    for c in concepts.values():
        referenced.update(ch for ch, _ in c.children)
    referenced.update(child for (_, child) in bc3.measurements)

    def children_of(chapter: str) -> List[Tuple[str, float, str]]:
        """(código, medición, nota) de un capítulo, combinando ~D y ~M."""
        result: List[Tuple[str, float, str]] = []
        seen = set()
        for child, qty in concepts[chapter].children:
            m = bc3.measurements.get((norm_code(chapter), child))
            result.append((child, m[0] if m else qty, ""))
            seen.add(child)
        extra = [(child, qty, pos) for (parent, child), (qty, pos) in bc3.measurements.items()
                 if parent == norm_code(chapter) and child not in seen]
        for child, qty, _ in sorted(extra, key=lambda x: x[2]):
            result.append((child, qty, "Partida tomada de las mediciones (~M) del BC3."))
        return result

    items: List[Item] = []
    orphans = [c for c, k in concepts.items()
               if k.ctype == "0" and k.price > 0 and c not in referenced and "#" not in c
               and not c.startswith("%") and not k.children and c not in basics]

    def add_chapter(chapter: str, prefix: str = ""):
        """Añade recursivamente partidas del capítulo y avisa de diferencias con su total."""
        cinfo = concepts.get(chapter, Concept(chapter))
        cname = (prefix + cinfo.summary).strip()
        kids = children_of(chapter) if chapter in concepts else []
        sub = [k for k in kids if k[0].endswith("#")]
        leaf = [k for k in kids if not k[0].endswith("#")]
        start = len(items)
        for code, qty, note in leaf:
            c = concepts.get(code, Concept(code))
            items.append(Item(0, chapter, cname, code, c.unit, c.summary, qty, c.price, c.text, note))
        for code, _, _ in sub:
            add_chapter(code, cname + " / ")

        total = sum(i.quantity * i.price for i in items[start:])
        gap = round(cinfo.price - total, 2)
        if abs(gap) > 0.05 and cinfo.price > 0:
            candidates = []
            for o in orphans:
                q = round(gap / concepts[o].price, 2)
                if q > 0 and abs(q * concepts[o].price - gap) < 0.02:
                    candidates.append((o, q))
            if len(candidates) == 1:
                o, q = candidates[0]
                c = concepts[o]
                items.append(Item(0, chapter, cname, o, c.unit, c.summary, q, c.price, c.text,
                                  "Medición INFERIDA (cuadra el total del capítulo). Revisar."))
                orphans.remove(o)
                warnings.append(f"{chapter}: falta {gap:.2f} €; se asigna {o} con medición inferida {q}.")
            elif not kids:
                items.append(Item(0, chapter, cname, norm_code(chapter) + "-PA", "PA",
                                  f"{cinfo.summary} (capítulo sin descomponer en el BC3)", 1.0,
                                  cinfo.price, "",
                                  "Capítulo sin partidas en el BC3: partida alzada. Indicar duración manual."))
                warnings.append(f"{chapter}: sin partidas en el BC3; se crea partida alzada de {cinfo.price:.2f} €.")
            else:
                warnings.append(f"{chapter}: la suma de partidas ({total:.2f}) no coincide con el BC3 ({cinfo.price:.2f}).")

    for chapter in top:
        add_chapter(chapter)

    for o in orphans:
        warnings.append(f"Partida {o} ({concepts[o].summary}) no está asignada a ningún capítulo.")
    for n, it in enumerate(items, start=1):
        it.id = n
    return items


def build_decompositions(items: List[Item], bc3: Bc3, workitems: Dict[str, dict],
                         basics: Dict[str, dict], warnings: List[str]):
    """Combina descomposiciones del BC3 y BCCA y construye el catálogo de recursos."""
    decomps: Dict[str, Tuple[str, List[Tuple[str, float]]]] = {}
    resources: Dict[str, Resource] = {}

    def resource(code: str) -> Resource:
        """Resuelve tipo, precio y procedencia de un recurso y avisa si carece de precio."""
        if code in resources:
            return resources[code]
        c = bc3.concepts.get(code)
        b = basics.get(code) or workitems.get(code) or {}
        btype = str(b.get("tipo") or "").strip()
        ctype = c.ctype if c and c.ctype != "0" else btype or (c.ctype if c else "0")
        if code.startswith("%"):
            rtype = "%"
        else:
            rtype = TYPE_NAMES.get(ctype, "AUX" if ctype == "0" else "MAT")
        price, source = 0.0, "sin precio"
        if c and c.price > 0:
            price, source = c.price, "BC3"
        elif b.get("precio") not in (None, ""):
            price, source = _num(str(b["precio"])), "BCCA2023"
        if price == 0 and rtype != "%":
            warnings.append(f"Recurso {code} sin precio.")
        r = Resource(code, rtype, (c.unit if c and c.unit else str(b.get("unidad") or "")),
                     (c.summary if c and c.summary else str(b.get("resumen") or "")), price, source)
        resources[code] = r
        return r

    for it in items:
        if it.code in decomps:
            continue
        c = bc3.concepts.get(it.code)
        if c and c.children:
            decomps[it.code] = ("BC3", c.children)
        elif it.code in workitems and parse_decomp(workitems[it.code].get("descompuesto")):
            decomps[it.code] = ("BCCA2023", parse_decomp(workitems[it.code].get("descompuesto")))
            if not it.text:
                it.text = str(workitems[it.code].get("texto_t") or "")
        else:
            decomps[it.code] = ("", [])
            if not it.code.endswith("-PA"):
                warnings.append(f"Partida {it.code} sin descomposición (BC3 ni BCCA).")
        for rc, _ in decomps[it.code][1]:
            resource(rc)
    return decomps, resources


# --------------------------------------------------------------------------
# Conservar datos del Excel anterior
# --------------------------------------------------------------------------
PLAN_INPUT_COLS = {"I": "equipos", "K": "dur_manual", "L": "continua", "N": "p1", "O": "t1", "P": "d1",
                   "Q": "p2", "R": "t2", "S": "d2", "T": "p3", "U": "t3", "V": "d3", "W": "min_fecha"}


def read_previous(path: Path) -> Tuple[Dict[Tuple[str, str], dict], Dict[int, Tuple[str, str]], Optional[dict]]:
    """Recupera parámetros y entradas editables de un Excel previo para conservarlas."""
    if not path.exists():
        return {}, {}, None
    try:
        wb = load_workbook(path, data_only=False)
    except Exception:
        return {}, {}, None
    prev, old_ids, cfg = {}, {}, None
    if "Planificación" in wb.sheetnames:
        ws = wb["Planificación"]
        for r in range(6, ws.max_row + 1):
            aid, chap, code = ws[f"A{r}"].value, ws[f"B{r}"].value, ws[f"C{r}"].value
            if aid in (None, "") or code in (None, ""):
                continue
            key = (str(chap), str(code))
            data = {name: ws[f"{c}{r}"].value for c, name in PLAN_INPUT_COLS.items()}
            prev[key] = data
            try:
                old_ids[int(aid)] = key
            except (TypeError, ValueError):
                pass
    if "Config" in wb.sheetnames:
        ws = wb["Config"]
        cfg = {"inicio": ws["B4"].value, "horas": ws["B5"].value, "escala": ws["B6"].value,
               "plan": ws["B12"].value,
               "festivos": [(ws[f"A{r}"].value, ws[f"B{r}"].value) for r in range(15, ws.max_row + 1)
                            if ws[f"A{r}"].value is not None]}
    wb.close()
    return prev, old_ids, cfg


# --------------------------------------------------------------------------
# Escritura del Excel
# --------------------------------------------------------------------------
def header_row(ws, row: int, headers: List[str], widths: Optional[List[float]] = None):
    """Escribe una cabecera de hoja con estilo, ajuste de texto y anchos opcionales."""
    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=i, value=h)
        cell.fill, cell.font, cell.border = FILL_HEADER, FONT_HEADER, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        if widths and i <= len(widths) and widths[i - 1]:
            ws.column_dimensions[col(i)].width = widths[i - 1]
    ws.row_dimensions[row].height = 42


def add_name(wb: Workbook, name: str, ref: str):
    """Registra un nombre definido compatible con las versiones admitidas de openpyxl."""
    dn = DefinedName(name, attr_text=ref)
    try:
        wb.defined_names[name] = dn
    except TypeError:  # openpyxl < 3.1
        wb.defined_names.append(dn)


def write_config(wb: Workbook, bc3_path: Path, cfg: Optional[dict], escala_default: int):
    """Crea la hoja de parámetros, festivos, nombres definidos y ayuda del libro."""
    ws = wb.active
    ws.title = "Config"
    ws["A1"] = "PLANIFICACIÓN DE OBRA"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Origen: {bc3_path.name}  ·  generado el {date.today():%d/%m/%Y}"
    ws["D2"] = "Versión: 1.0.0"
    rows = [
        ("Fecha de inicio de obra", (cfg or {}).get("inicio") or DEFAULT_START, FMT_DATE, True),
        ("Horas por jornada", (cfg or {}).get("horas") or HOURS_PER_DAY, "0", True),
        ("Escala del Gantt (1 = día, 7 = semana)", (cfg or {}).get("escala") or escala_default, "0", True),
        ("Inicio de la escala del Gantt (lunes)", "=InicioObra-WEEKDAY(InicioObra,3)", FMT_DATE, False),
        ("Plazo (días laborables)", "=MAX('Planificación'!$Z$5:$Z$1000)", "0", False),
        ("Fecha de fin de obra", "=WORKDAY(InicioObra-1,MAX(1,Plazo),Festivos)", FMT_DATE, False),
        ("Fin de actividades no continuas (día lab.)", "=MAX('Planificación'!$Y$5:$Y$1000)", "0", False),
        ("PEM total (€)", "=SUM('Planificación'!$G$6:$G$1000)", FMT_EUR, False),
    ]
    for i, (label, value, fmt, editable) in enumerate(rows, start=4):
        ws[f"A{i}"] = label
        ws[f"B{i}"] = value
        ws[f"B{i}"].number_format = fmt
        ws[f"B{i}"].border = BORDER
        if editable:
            ws[f"B{i}"].fill = FILL_INPUT
    ws["A12"] = "Plan de la figura Gantt"
    ws["B12"] = (cfg or {}).get("plan") or "Completo"
    ws["B12"].fill = FILL_INPUT
    ws["B12"].border = BORDER
    plan_validation = DataValidation(type="list", formula1='"Resumen,Completo"', allow_blank=False)
    ws.add_data_validation(plan_validation)
    plan_validation.add(ws["B12"])
    for name, ref in [("InicioObra", "$B$4"), ("HorasJornada", "$B$5"), ("Escala", "$B$6"),
                      ("InicioGantt", "$B$7"), ("Plazo", "$B$8"), ("FinBase", "$B$10")]:
        add_name(wb, name, f"Config!{ref}")

    ws["A13"] = "FESTIVOS (no laborables; revisar y añadir los locales)"
    ws["A13"].font = FONT_BOLD
    header_row(ws, 14, ["Fecha", "Descripción"])
    holidays = (cfg or {}).get("festivos") or DEFAULT_HOLIDAYS
    for i, (d, desc) in enumerate(holidays, start=15):
        ws[f"A{i}"] = d
        ws[f"A{i}"].number_format = FMT_DATE
        ws[f"A{i}"].fill = FILL_INPUT
        ws[f"B{i}"] = desc
    add_name(wb, "Festivos", "Config!$A$15:$A$200")
    for r in range(15, 201):
        ws[f"A{r}"].number_format = FMT_DATE
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 40

    help_lines = [
        "CÓMO USAR",
        "1. Celdas amarillas = datos editables. El resto son fórmulas.",
        "2. En 'Descompuestos' puedes ajustar rendimientos; se recalculan costes y duraciones.",
        "3. En 'Planificación' define la secuencia: hasta 3 predecesoras por actividad (por ID),",
        "   tipo de relación FC (fin-comienzo), CC (comienzo-comienzo), FF (fin-fin), CF (comienzo-fin)",
        "   y desfase en días laborables (puede ser negativo para solapes).",
        "4. Las predecesoras deben estar en FILAS SUPERIORES (ordena las actividades de forma lógica).",
        "5. Duración = medición × rendimiento crítico / (nº equipos × horas/jornada), redondeada.",
        "   Escribe una 'Duración manual' para imponer otra.",
        "6. 'Toda la obra' = S: la actividad dura hasta el final de las demás (S. y S., residuos...).",
        "   Estas actividades no deben usarse como predecesoras.",
        "7. Para añadir actividades usa las filas vacías del final asignando un ID nuevo.",
        "8. Volver a ejecutar el script conserva lo introducido en 'Planificación' y 'Config'.",
        "9. En Config!B12 elige Resumen (solo capítulos) o Completo (capítulos y unidades de obra).",
    ]
    for i, line in enumerate(help_lines, start=4):
        ws[f"D{i}"] = line
        if i == 4:
            ws[f"D{i}"].font = FONT_BOLD
    ws.column_dimensions["D"].width = 95


def write_resources(wb: Workbook, resources: Dict[str, Resource]) -> int:
    """Crea el catálogo de recursos con precios editables y devuelve su última fila."""
    ws = wb.create_sheet("Recursos")
    header_row(ws, 1, ["Código", "Tipo", "Ud", "Resumen", "Precio (€)", "Fuente"], [14, 7, 6, 60, 12, 12])
    order = {"MO": 0, "MAQ": 1, "MAT": 2, "AUX": 3, "%": 4}
    r = 2
    for res in sorted(resources.values(), key=lambda x: (order.get(x.rtype, 9), x.code)):
        ws.append([res.code, res.rtype, res.unit, res.summary, res.price, res.source])
        ws[f"E{r}"].number_format = FMT_EUR
        ws[f"E{r}"].fill = FILL_INPUT
        r += 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:F{r - 1}"
    return r - 1


def write_decomps(wb: Workbook, items: List[Item], decomps, resources, last_res_row: int) -> int:
    """Escribe descomposiciones y fórmulas de costes enlazadas al catálogo de recursos."""
    ws = wb.create_sheet("Descompuestos")
    header_row(ws, 1, ["Partida", "Recurso", "Tipo", "Ud", "Resumen", "Rendimiento (ud/ud partida)",
                       "Precio (€)", "Importe (€)", "Fuente"], [14, 12, 7, 6, 55, 14, 12, 12, 11])
    rng = f"Recursos!$A$2:$A${last_res_row}"
    r = 2
    done = set()
    for it in items:
        if it.code in done:
            continue
        done.add(it.code)
        source, lines = decomps[it.code]
        first = r
        for rc, rend in lines:
            res = resources[rc]
            ws.append([it.code, rc, res.rtype, res.unit, res.summary, rend])
            if res.rtype == "%":
                ws[f"G{r}"] = f"=SUM(H{first}:H{r - 1})" if r > first else 0
            else:
                ws[f"G{r}"] = f"=IFERROR(INDEX(Recursos!$E$2:$E${last_res_row},MATCH(B{r},{rng},0)),0)"
            ws[f"H{r}"] = f"=F{r}*G{r}"
            ws[f"I{r}"] = source
            ws[f"F{r}"].number_format = FMT_REND
            ws[f"F{r}"].fill = FILL_INPUT
            ws[f"G{r}"].number_format = FMT_EUR
            ws[f"H{r}"].number_format = FMT_EUR
            r += 1
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:I{max(2, r - 1)}"
    return max(2, r - 1)


def critical_resource(lines, resources) -> str:
    """Devuelve el recurso de mano de obra o maquinaria con mayor rendimiento unitario."""
    best = None
    for rc, rend in lines:
        if resources[rc].rtype in ("MO", "MAQ") and (best is None or rend > best[1]):
            best = (rc, rend)
    return f"{best[0]} {resources[best[0]].summary}" if best else ""


def write_budget(wb: Workbook, items: List[Item], decomps, resources, last_dec_row: int) -> int:
    """Genera el presupuesto con costes, rendimientos y fórmulas enlazadas a descompuestos."""
    ws = wb.create_sheet("Presupuesto")
    headers = ["ID", "Capítulo", "Nombre capítulo", "Código", "Ud", "Resumen", "Medición", "Precio BC3 (€)",
               "Importe (€)", "Coste MO/ud (€)", "Coste MAQ/ud (€)", "Coste MAT y otros/ud (€)",
               "Precio recompuesto (€)", "h MO/ud", "h MAQ/ud", "Rend. crítico (h/ud)",
               "Recurso crítico", "Fuente descompuesto", "Observaciones", "Descripción"]
    header_row(ws, 1, headers, [5, 10, 28, 13, 5, 50, 11, 11, 13, 11, 11, 11, 11, 9, 9, 10, 40, 11, 40, 60])
    D = "Descompuestos!"
    rA, rC, rF, rH = (f"{D}$A$2:$A${last_dec_row}", f"{D}$C$2:$C${last_dec_row}",
                      f"{D}$F$2:$F${last_dec_row}", f"{D}$H$2:$H${last_dec_row}")
    for r, it in enumerate(items, start=2):
        src, lines = decomps[it.code]
        ws.append([it.id, it.chapter, it.chapter_name, it.code, it.unit, it.summary, it.quantity, it.price])
        ws[f"I{r}"] = f"=G{r}*H{r}"
        ws[f"J{r}"] = f'=SUMIFS({rH},{rA},$D{r},{rC},"MO")'
        ws[f"K{r}"] = f'=SUMIFS({rH},{rA},$D{r},{rC},"MAQ")'
        ws[f"L{r}"] = f"=SUMIFS({rH},{rA},$D{r})-J{r}-K{r}"
        ws[f"M{r}"] = f"=J{r}+K{r}+L{r}"
        ws[f"N{r}"] = f'=SUMIFS({rF},{rA},$D{r},{rC},"MO")'
        ws[f"O{r}"] = f'=SUMIFS({rF},{rA},$D{r},{rC},"MAQ")'
        ws[f"P{r}"] = (f'=MAX(_xlfn.MAXIFS({rF},{rA},$D{r},{rC},"MO"),'
                       f'_xlfn.MAXIFS({rF},{rA},$D{r},{rC},"MAQ"))')
        ws[f"Q{r}"] = critical_resource(lines, resources)
        ws[f"R{r}"] = src
        ws[f"S{r}"] = it.note
        ws[f"T{r}"] = it.text
        for c in "HIJKLM":
            ws[f"{c}{r}"].number_format = FMT_EUR
        ws[f"G{r}"].number_format = FMT_NUM
        for c in "NOP":
            ws[f"{c}{r}"].number_format = FMT_REND
        ws[f"G{r}"].fill = FILL_INPUT
        ws[f"H{r}"].fill = FILL_INPUT
        if it.note:
            ws[f"S{r}"].fill = FILL_WARN
    last = len(items) + 1
    tr = last + 1
    ws[f"F{tr}"] = "TOTAL PEM"
    ws[f"F{tr}"].font = FONT_BOLD
    ws[f"I{tr}"] = f"=SUM(I2:I{last})"
    ws[f"I{tr}"].number_format = FMT_EUR
    ws[f"I{tr}"].font = FONT_BOLD
    ws.freeze_panes = "G2"
    ws.auto_filter.ref = f"A1:T{last}"
    return last


def write_chapters(wb: Workbook, bc3: Bc3, items: List[Item], last_budget_row: int):
    """Genera el resumen de capítulos y compara las sumas con los importes del BC3."""
    ws = wb.create_sheet("Capítulos")
    header_row(ws, 1, ["Capítulo", "Nombre", "Importe partidas (€)", "Total en BC3 (€)", "Diferencia (€)", "% PEM"],
               [12, 45, 16, 16, 14, 9])
    chapters = list(dict.fromkeys(it.chapter for it in items))
    r = 2
    for ch in chapters:
        name = next(it.chapter_name for it in items if it.chapter == ch)
        ws.append([ch, name])
        ws[f"C{r}"] = f"=SUMIFS(Presupuesto!$I$2:$I${last_budget_row},Presupuesto!$B$2:$B${last_budget_row},A{r})"
        ws[f"D{r}"] = bc3.concepts[ch].price if ch in bc3.concepts else 0
        ws[f"E{r}"] = f"=C{r}-D{r}"
        for c in "CDE":
            ws[f"{c}{r}"].number_format = FMT_EUR
        r += 1
    last = r - 1
    for rr in range(2, r):
        ws[f"F{rr}"] = f"=IF(SUM($C$2:$C${last})=0,0,C{rr}/SUM($C$2:$C${last}))"
        ws[f"F{rr}"].number_format = "0.00%"
    ws[f"B{r}"] = "TOTAL"
    ws[f"B{r}"].font = FONT_BOLD
    for c in "CDE":
        ws[f"{c}{r}"] = f"=SUM({c}2:{c}{last})"
        ws[f"{c}{r}"].number_format = FMT_EUR
        ws[f"{c}{r}"].font = FONT_BOLD
    ws.conditional_formatting.add(f"E2:E{r}", FormulaRule(formula=[f"ABS(E2)>0.05"], fill=FILL_WARN))


def pred_term(r: int, p: str, t: str, d: str) -> str:
    """Construye la fórmula de desfase de una predecesora según el tipo de relación."""
    above_a, above_x, above_y = f"$A$5:$A${r - 1}", f"$X$5:$X${r - 1}", f"$Y$5:$Y${r - 1}"
    m = f"MATCH({p}{r},{above_a},0)"
    es, ef = f"INDEX({above_x},{m})", f"INDEX({above_y},{m})"
    lag = f"N({d}{r})"
    return (f'IF({p}{r}="",0,IFERROR(IF({t}{r}="CC",{es}+{lag},IF({t}{r}="FF",{ef}+{lag}-M{r},'
            f'IF({t}{r}="CF",{es}+{lag}-M{r},{ef}+{lag}))),0))')


def pred_check(r: int, p: str, n: int) -> str:
    """Construye la fórmula que avisa de predecesoras inválidas o situadas después de la actividad."""
    return f'IF(AND({p}{r}<>"",ISNA(MATCH({p}{r},$A$5:$A${r - 1},0))),"Pred{n} no existe o está debajo. ","")'


def write_planning(wb: Workbook, items: List[Item], last_budget_row: int,
                   prev: Dict[Tuple[str, str], dict], old_ids: Dict[int, Tuple[str, str]]) -> int:
    """Crea la secuencia editable y sus fórmulas de fechas, conservando entradas anteriores."""
    ws = wb.create_sheet("Planificación")
    ws["A1"] = "PLANIFICACIÓN: define equipos, duraciones y secuencia (celdas amarillas)"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = ("Relaciones: FC fin-comienzo · CC comienzo-comienzo · FF fin-fin · CF comienzo-fin. "
                "Desfase en días laborables (negativo = solape). Las predecesoras deben estar en filas superiores.")
    headers = ["ID", "Capítulo", "Código", "Resumen", "Ud", "Medición", "Importe (€)", "Rend. crítico (h/ud)",
               "Nº equipos", "Duración calculada (días)", "Duración manual (días)", "Toda la obra (S/N)",
               "Duración (días lab.)", "Pred. 1 (ID)", "Tipo 1", "Desfase 1", "Pred. 2 (ID)", "Tipo 2",
               "Desfase 2", "Pred. 3 (ID)", "Tipo 3", "Desfase 3", "No antes de (fecha)",
               "Comienzo (día lab.)", "Fin sin continuas (día lab.)", "Fin (día lab.)",
               "Fecha inicio", "Fecha fin", "Días lab. totales", "Avisos"]
    header_row(ws, 4, headers, [5, 9, 13, 45, 5, 10, 12, 10, 8, 10, 10, 8, 9, 7, 6, 7, 7, 6, 7, 7, 6, 7, 11,
                                9, 9, 9, 11, 11, 9, 45])

    # Hito de inicio
    ws.append([0, "", "INICIO", "INICIO DE OBRA", "", None, None, None, None, None, None, "N", 0])
    for c, v in {"X": 0, "Y": 0, "Z": 0, "AA": "=InicioObra", "AB": "=InicioObra", "AC": 0}.items():
        ws[f"{c}5"] = v
    for c in ("AA5", "AB5"):
        ws[c].number_format = FMT_DATE
    for c in range(1, 31):
        ws.cell(row=5, column=c).font = FONT_BOLD

    # IDs anteriores -> nuevos (para conservar las predecesoras)
    key_to_new = {(it.chapter, it.code): it.id for it in items}
    remap = {old: key_to_new[k] for old, k in old_ids.items() if k in key_to_new}
    remap[0] = 0

    def remap_pred(v):
        """Traduce el identificador de una predecesora del libro anterior al actual."""
        if v in (None, ""):
            return None
        try:
            return remap.get(int(v), int(v))
        except (TypeError, ValueError):
            return v

    last_noncont = 0
    BP = "Presupuesto!"
    rows_total = len(items) + EXTRA_ROWS
    for idx in range(rows_total):
        r = 6 + idx
        it = items[idx] if idx < len(items) else None
        if it:
            continuous = bool(CONTINUOUS_CHAPTER_RE.search(it.chapter_name))
            data = prev.get((it.chapter, it.code))
            ws[f"A{r}"], ws[f"B{r}"], ws[f"C{r}"], ws[f"D{r}"], ws[f"E{r}"] = (
                it.id, it.chapter, it.code, it.summary, it.unit)
            lookup = f"MATCH($A{r},{BP}$A$2:$A${last_budget_row},0)"
            ws[f"F{r}"] = f'=IFERROR(INDEX({BP}$G$2:$G${last_budget_row},{lookup}),"")'
            ws[f"G{r}"] = f'=IFERROR(INDEX({BP}$I$2:$I${last_budget_row},{lookup}),"")'
            ws[f"H{r}"] = f'=IFERROR(INDEX({BP}$P$2:$P${last_budget_row},{lookup}),"")'
            if data:
                for c, name in PLAN_INPUT_COLS.items():
                    v = data.get(name)
                    ws[f"{c}{r}"] = remap_pred(v) if name in ("p1", "p2", "p3") else v
            else:
                ws[f"I{r}"] = 1
                ws[f"L{r}"] = "S" if continuous else "N"
                ws[f"N{r}"] = last_noncont if not continuous else 0
                ws[f"O{r}"] = "FC" if not continuous else "CC"
                ws[f"P{r}"] = 0
            if ws[f"L{r}"].value != "S":
                last_noncont = it.id
        ws[f"J{r}"] = (f'=IF(OR($A{r}="",N(F{r})*N(H{r})=0),"",'
                       f'MAX(1,ROUNDUP(F{r}*H{r}/(IF(N(I{r})<=0,1,I{r})*HorasJornada),0)))')
        ws[f"M{r}"] = f'=IF($A{r}="","",IF(K{r}<>"",K{r},IF(J{r}<>"",J{r},1)))'
        ws[f"X{r}"] = (f'=IF($A{r}="","",MAX(0,IF(W{r}="",0,NETWORKDAYS(InicioObra,W{r},Festivos)-1),'
                       f'{pred_term(r, "N", "O", "P")},{pred_term(r, "Q", "R", "S")},{pred_term(r, "T", "U", "V")}))')
        ws[f"Y{r}"] = f'=IF(OR($A{r}="",L{r}="S"),"",X{r}+M{r})'
        ws[f"Z{r}"] = f'=IF($A{r}="","",IF(L{r}="S",MAX(X{r}+1,FinBase),Y{r}))'
        ws[f"AA{r}"] = f'=IF($A{r}="","",WORKDAY(InicioObra-1,X{r}+1,Festivos))'
        ws[f"AB{r}"] = f'=IF($A{r}="","",IF(Z{r}<=X{r},AA{r},WORKDAY(InicioObra-1,Z{r},Festivos)))'
        ws[f"AC{r}"] = f'=IF($A{r}="","",Z{r}-X{r})'
        ws[f"AD{r}"] = ("=" + "&".join([pred_check(r, "N", 1), pred_check(r, "Q", 2), pred_check(r, "T", 3),
                        f'IF(AND($A{r}<>"",J{r}="",K{r}="",L{r}<>"S"),"Sin rendimiento: indique duración manual.","")']))
        for c in ("A", "B", "C", "D", "E", "I", "K", "L", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W"):
            ws[f"{c}{r}"].fill = FILL_INPUT
        ws[f"F{r}"].number_format = FMT_NUM
        ws[f"G{r}"].number_format = FMT_EUR
        ws[f"H{r}"].number_format = FMT_REND
        ws[f"W{r}"].number_format = FMT_DATE
        ws[f"AA{r}"].number_format = FMT_DATE
        ws[f"AB{r}"].number_format = FMT_DATE
        for c in range(1, 31):
            ws.cell(row=r, column=c).border = BORDER

    last = 5 + rows_total
    dv_tipo = DataValidation(type="list", formula1='"FC,CC,FF,CF"', allow_blank=True)
    dv_sn = DataValidation(type="list", formula1='"S,N"', allow_blank=True)
    ws.add_data_validation(dv_tipo)
    ws.add_data_validation(dv_sn)
    for c in ("O", "R", "U"):
        dv_tipo.add(f"{c}6:{c}{last}")
    dv_sn.add(f"L6:L{last}")
    ws.conditional_formatting.add(f"AD6:AD{last}", FormulaRule(formula=['LEN($AD6)>0'], fill=FILL_WARN))
    ws.conditional_formatting.add(f"K6:K{last}", FormulaRule(
        formula=['AND($A6<>"",$J6="",$K6="",$L6<>"S")'], fill=FILL_WARN))
    ws.freeze_panes = "E5"
    ws.auto_filter.ref = f"A4:AD{last}"
    return last


def write_gantt(wb: Workbook, last_plan_row: int):
    """Dibuja el calendario del Gantt con fórmulas y formatos condicionales."""
    ws = wb.create_sheet("Gantt")
    P = "'Planificación'!"
    ws["A1"] = "DIAGRAMA DE GANTT"
    ws["A1"].font = Font(bold=True, size=13)
    ws["D1"] = "Escala (días/columna):"
    ws["F1"] = "=Escala"
    ws["H1"] = "Azul: actividad · Naranja: toda la obra · Gris: fines de semana y festivos (escala diaria)"
    headers = ["ID", "Código", "Actividad", "Inicio", "Fin", "Días lab.", "Cont."]
    header_row(ws, 4, headers, [5, 12, 42, 10, 10, 7, 5])
    first_tl = 8
    for j in range(GANTT_COLUMNS):
        c = col(first_tl + j)
        ws[f"{c}3"] = "=InicioGantt" if j == 0 else f"={col(first_tl + j - 1)}3+Escala"
        ws[f"{c}3"].number_format = "dd"
        ws[f"{c}3"].alignment = Alignment(horizontal="center", text_rotation=90)
        ws[f"{c}2"] = (f"={c}3" if j == 0 else
                       f'=IF(MONTH({c}3)<>MONTH({col(first_tl + j - 1)}3),{c}3,"")')
        ws[f"{c}2"].number_format = "mmm yy"
        ws[f"{c}2"].font = FONT_BOLD
        ws[f"{c}4"] = f'=IF(Escala=1,MID("LMXJVSD",WEEKDAY({c}3,2),1),"S"&ROUNDUP(({c}3-InicioGantt+1)/7,0))'
        ws[f"{c}4"].alignment = Alignment(horizontal="center")
        ws.column_dimensions[c].width = 3.2
    ws.row_dimensions[3].height = 26
    src = {"A": "A", "B": "C", "C": "D", "D": "AA", "E": "AB", "F": "AC", "G": "L"}
    for r in range(5, last_plan_row + 1):
        for gc, pc in src.items():
            ws[f"{gc}{r}"] = f'=IF(INDEX({P}$A:$A,ROW())="","",INDEX({P}${pc}:${pc},ROW())&"")' \
                if gc in ("B", "C", "G") else f'=IF(INDEX({P}$A:$A,ROW())="","",INDEX({P}${pc}:${pc},ROW()))'
        ws[f"D{r}"].number_format = FMT_DATE
        ws[f"E{r}"].number_format = FMT_DATE
    last_c = col(first_tl + GANTT_COLUMNS - 1)
    grid = f"{col(first_tl)}5:{last_c}{last_plan_row}"
    h = col(first_tl)
    in_bar = f'AND(ISNUMBER($D5),{h}$3<=$E5,{h}$3+Escala-1>=$D5)'
    ws.conditional_formatting.add(grid, FormulaRule(formula=[f'AND({in_bar},$F5=0)'], fill=FILL_BAR_MILESTONE,
                                                    stopIfTrue=True))
    ws.conditional_formatting.add(grid, FormulaRule(formula=[f'AND({in_bar},$G5="S")'], fill=FILL_BAR_CONT,
                                                    stopIfTrue=True))
    ws.conditional_formatting.add(grid, FormulaRule(formula=[in_bar], fill=FILL_BAR, stopIfTrue=True))
    ws.conditional_formatting.add(grid, FormulaRule(
        formula=[f'AND(Escala=1,OR(WEEKDAY({h}$3,2)>5,COUNTIF(Festivos,{h}$3)>0))'], fill=FILL_WEEKEND))
    ws.conditional_formatting.add(f"{h}3:{last_c}4", FormulaRule(
        formula=[f'AND(Escala=1,OR(WEEKDAY({h}$3,2)>5,COUNTIF(Festivos,{h}$3)>0))'], fill=FILL_WEEKEND))
    ws.freeze_panes = ws[f"{h}5"]


def write_economic(wb: Workbook, last_plan_row: int):
    """Crea el reparto mensual de importes y su curva acumulada de avance."""
    ws = wb.create_sheet("Plan económico")
    P = "'Planificación'!"
    ws["A1"] = "PLAN ECONÓMICO MENSUAL (PEM repartido según días laborables de cada actividad)"
    ws["A1"].font = Font(bold=True, size=13)
    header_row(ws, 4, ["ID", "Código", "Actividad", "Inicio", "Fin", "Importe (€)"], [5, 12, 42, 10, 10, 13])
    ws["F2"], ws["F3"] = "Desde", "Hasta"
    first = 7
    for k in range(ECON_MONTHS):
        c = col(first + k)
        ws[f"{c}2"] = f"=DATE(YEAR(InicioObra),MONTH(InicioObra)+{k},1)"
        ws[f"{c}3"] = f"=EOMONTH({c}2,0)"
        ws[f"{c}4"] = f"={c}2"
        for rr in (2, 3):
            ws[f"{c}{rr}"].number_format = FMT_DATE
        ws[f"{c}4"].number_format = "mmm yy"
        ws[f"{c}4"].fill, ws[f"{c}4"].font = FILL_HEADER, FONT_HEADER
        ws.column_dimensions[c].width = 12
    src = {"A": "A", "B": "C", "C": "D", "D": "AA", "E": "AB", "F": "G"}
    for r in range(5, last_plan_row + 1):
        for gc, pc in src.items():
            ws[f"{gc}{r}"] = f'=IF(INDEX({P}$A:$A,ROW())="","",INDEX({P}${pc}:${pc},ROW()))'
        ws[f"B{r}"] = f'=IF(INDEX({P}$A:$A,ROW())="","",INDEX({P}$C:$C,ROW())&"")'
        ws[f"C{r}"] = f'=IF(INDEX({P}$A:$A,ROW())="","",INDEX({P}$D:$D,ROW())&"")'
        ws[f"D{r}"].number_format = FMT_DATE
        ws[f"E{r}"].number_format = FMT_DATE
        ws[f"F{r}"].number_format = FMT_EUR
        for k in range(ECON_MONTHS):
            c = col(first + k)
            ws[f"{c}{r}"] = (f'=IF(OR(NOT(ISNUMBER($D{r})),N($F{r})=0,$E{r}<{c}$2,$D{r}>{c}$3),0,'
                             f'$F{r}*NETWORKDAYS(MAX($D{r},{c}$2),MIN($E{r},{c}$3),Festivos)'
                             f'/MAX(1,NETWORKDAYS($D{r},$E{r},Festivos)))')
            ws[f"{c}{r}"].number_format = FMT_NUM
    t = last_plan_row + 2
    labels = [(t, "Total mes (€)"), (t + 1, "Acumulado (€)"), (t + 2, "% acumulado")]
    for rr, label in labels:
        ws[f"C{rr}"] = label
        ws[f"C{rr}"].font = FONT_BOLD
    ws[f"F{t}"] = f"=SUM(F5:F{last_plan_row})"
    ws[f"F{t}"].number_format = FMT_EUR
    for k in range(ECON_MONTHS):
        c = col(first + k)
        prev_c = col(first + k - 1)
        ws[f"{c}{t}"] = f"=SUM({c}5:{c}{last_plan_row})"
        ws[f"{c}{t + 1}"] = f"={c}{t}" if k == 0 else f"={prev_c}{t + 1}+{c}{t}"
        ws[f"{c}{t + 2}"] = f"=IF($F${t}=0,0,{c}{t + 1}/$F${t})"
        ws[f"{c}{t}"].number_format = FMT_EUR
        ws[f"{c}{t + 1}"].number_format = FMT_EUR
        ws[f"{c}{t + 2}"].number_format = "0.0%"
        for rr in (t, t + 1, t + 2):
            ws[f"{c}{rr}"].font = FONT_BOLD
            ws[f"{c}{rr}"].fill = FILL_CHAPTER

    bar = BarChart()
    bar.title = "Certificación mensual y curva de avance"
    bar.y_axis.title = "€ / mes"
    bar.add_data(Reference(ws, min_col=first, max_col=first + ECON_MONTHS - 1, min_row=t), from_rows=True,
                 titles_from_data=False)
    bar.set_categories(Reference(ws, min_col=first, max_col=first + ECON_MONTHS - 1, min_row=4))
    bar.series[0].tx = None
    line = LineChart()
    line.add_data(Reference(ws, min_col=first, max_col=first + ECON_MONTHS - 1, min_row=t + 2), from_rows=True,
                  titles_from_data=False)
    line.y_axis.axId = 200
    line.y_axis.title = "% acumulado"
    line.y_axis.number_format = "0%"
    line.y_axis.crosses = "max"
    bar += line
    bar.legend = None
    bar.height, bar.width = 9, 26
    ws.add_chart(bar, f"C{t + 4}")
    ws.freeze_panes = ws[f"{col(first)}5"]


# --------------------------------------------------------------------------
def estimate_scale(items, decomps, resources) -> int:
    """Estima una escala diaria o semanal según la duración calculada del presupuesto."""
    days = 0.0
    for it in items:
        rend = max([r for rc, r in decomps[it.code][1] if resources[rc].rtype in ("MO", "MAQ")] or [0])
        days += max(1, it.quantity * rend / HOURS_PER_DAY) if not CONTINUOUS_CHAPTER_RE.search(it.chapter_name) else 0
    return 1 if days * 7 / 5 <= GANTT_COLUMNS - 10 else 7


def main(argv: List[str]) -> int:
    """Convierte el BC3 en un libro de planificación y conserva una copia del Excel previo."""
    bc3_path = Path(argv[1]).resolve() if len(argv) > 1 else find_unique_input(BASE_DIR, ".bc3")
    out_path = Path(argv[2]).resolve() if len(argv) > 2 else bc3_path.with_name(bc3_path.stem + "_planificacion.xlsx")
    if not bc3_path.exists():
        raise SystemExit(f"No existe el archivo {bc3_path}")

    warnings: List[str] = []
    bc3 = read_bc3(bc3_path)
    workitems = load_xlsx_table(find_file(WORKITEMS_XLSX_NAME))
    basics = load_xlsx_table(find_file(BASICPRICES_XLSX_NAME))
    if not workitems:
        warnings.append(f"No se encontró {WORKITEMS_XLSX_NAME}: solo se usarán los descompuestos del BC3.")

    items = build_items(bc3, basics, warnings)
    decomps, resources = build_decompositions(items, bc3, workitems, basics, warnings)
    prev, old_ids, cfg = read_previous(out_path)

    wb = Workbook()
    write_config(wb, bc3_path, cfg, estimate_scale(items, decomps, resources))
    last_res = write_resources(wb, resources)
    last_dec = write_decomps(wb, items, decomps, resources, last_res)
    last_bud = write_budget(wb, items, decomps, resources, last_dec)
    write_chapters(wb, bc3, items, last_bud)
    last_plan = write_planning(wb, items, last_bud, prev, old_ids)
    write_gantt(wb, last_plan)
    write_economic(wb, last_plan)

    ws = wb.create_sheet("Incidencias")
    header_row(ws, 1, ["Incidencias detectadas al leer el BC3"], [120])
    for w in warnings or ["Ninguna."]:
        ws.append([w])

    wb.calculation.fullCalcOnLoad = True
    wb.active = wb.sheetnames.index("Planificación")
    if out_path.exists():
        shutil.copy2(out_path, out_path.with_suffix(".xlsx.bak"))
    wb.save(out_path)

    pem = sum(i.quantity * i.price for i in items)
    print(f"Generado: {out_path}")
    print(f"  {len(items)} actividades · {len(resources)} recursos · PEM {pem:,.2f} €")
    if prev:
        print(f"  Conservados datos de planificación de {len(prev)} actividades del Excel anterior.")
    for w in warnings:
        print("  AVISO:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
