"""Lectura local de facturas PDF y control editable de costes por recurso."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
from pathlib import Path
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable
import unicodedata
from uuid import uuid4

from localization import filedialog, localize_widget_tree, messagebox, translate

if TYPE_CHECKING:
    from bc3_gui import BudgetApp, ProjectItem
    from PIL import Image


KEYWORDS = ("obra", "expediente", "fecha", "partida", "importe")
ALL_PERIODS = "Todo el proyecto"
UNDATED_PERIOD = "Sin fecha válida"


def render_invoice_page(
    path: Path, page_index: int, width: int, zoom: float,
) -> tuple[Image.Image, int]:
    """Renderiza una sola página local con memoria acotada y libera los objetos PDF."""
    import pypdfium2 as pdfium

    try:
        with pdfium.PdfDocument(path) as document:
            count = len(document)
            if not 0 <= page_index < count:
                raise ValueError("La página solicitada no existe en el PDF.")
            page = document[page_index]
            try:
                page_width, page_height = page.get_size()
                if page_width <= 0 or page_height <= 0:
                    raise ValueError("La página PDF tiene dimensiones no válidas.")
                scale = min(
                    max(100, width) * zoom / page_width,
                    (4_000_000 / (page_width * page_height)) ** 0.5,
                    8000 / max(page_width, page_height),
                )
                bitmap = page.render(scale=scale)
                try:
                    return bitmap.to_pil().convert("RGB"), count
                finally:
                    bitmap.close()
            finally:
                page.close()
    except pdfium.PdfiumError as exc:
        raise ValueError(f"No se pudo visualizar el PDF: {exc}") from exc


def normalized(value: str) -> str:
    """Normaliza claves para compararlas sin acentos ni diferencias de mayúsculas."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    ).strip()


def header_name(value: str) -> str:
    """Reconoce las cabeceras admitidas, incluidas las unidades monetarias."""
    label = normalized(value).replace("\n", " ").strip(" :")
    match = re.fullmatch(r"(obra|expediente|fecha|partida|importe)(?:\s*(?:\((?:€|eur)\)|€|eur))?", label)
    return match.group(1) if match else ""


def amount(value: object) -> float:
    """Acepta importes europeos o internacionales y rechaza valores no finitos."""
    if isinstance(value, bool):
        raise ValueError("El importe debe ser un número.")
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    text = re.sub(r"(?i)(€|EUR)$", "", text).strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    else:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Importe no válido: {value}") from exc
    if not number.is_finite() or abs(number) > Decimal("1e15"):
        raise ValueError(f"Importe fuera de rango: {value}")
    return float(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def total(values) -> float:
    """Suma importes decimales sin acumular errores binarios."""
    return amount(sum((Decimal(str(value)) for value in values), Decimal(0)))


def identifier() -> str:
    """Crea una clave persistente independiente de nombres y posiciones."""
    return uuid4().hex


@dataclass
class CostRow:
    id: str
    chapter: str = ""
    item: str = ""
    resource: str = ""
    description: str = ""
    kind: str = "Otro"
    unit: str = ""
    budget: float = 0.0


@dataclass
class InvoiceLine:
    item: str
    amount: float
    row_id: str = ""


@dataclass
class Invoice:
    id: str
    name: str
    path: str = ""
    digest: str = ""
    work: str = ""
    reference: str = ""
    date: str = ""
    declared: float = 0.0
    notes: str = ""
    lines: list[InvoiceLine] = field(default_factory=list)
    amounts: dict[str, float] = field(default_factory=dict)


def invoice_sort_key(invoice: Invoice) -> tuple[date, str, str]:
    """Ordena cronológicamente y deja al final las fechas vacías o no reconocidas."""
    parsed = date.max
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(invoice.date.strip(), pattern).date()
            break
        except ValueError:
            continue
    return parsed, normalized(invoice.name), invoice.id


def invoice_month(invoice: Invoice) -> str:
    """Obtiene el mes de la fecha reconocida o identifica las facturas sin fecha."""
    parsed = invoice_sort_key(invoice)[0]
    return parsed.strftime("%Y-%m") if parsed != date.max else UNDATED_PERIOD


def period_invoices(invoices: list[Invoice], period: str) -> list[Invoice]:
    """Selecciona todas las facturas, las no fechadas o el acumulado hasta un mes."""
    if period == ALL_PERIODS:
        return list(invoices)
    if period == UNDATED_PERIOD:
        return [invoice for invoice in invoices if invoice_month(invoice) == UNDATED_PERIOD]
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise ValueError(f"Periodo de facturación no válido: {period}")
    return [
        invoice for invoice in invoices
        if invoice_month(invoice) != UNDATED_PERIOD and invoice_month(invoice) <= period
    ]


@dataclass
class InvoiceState:
    folder: str = ""
    rows: list[CostRow] = field(default_factory=list)
    invoices: list[Invoice] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializa la tabla y los datos de las facturas para el proyecto."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> InvoiceState:
        """Valida la estructura, las referencias y los importes guardados."""
        if not isinstance(data, dict):
            raise ValueError("La facturación debe ser un objeto JSON.")

        def text(record: dict, key: str, default: str = "") -> str:
            """Lee un campo textual sin aceptar tipos incompatibles."""
            value = record.get(key, default)
            if not isinstance(value, str):
                raise ValueError(f"Campo de facturación no válido: {key}.")
            return value

        def records(key: str) -> list[dict]:
            """Valida una lista de objetos del estado persistente."""
            value = data.get(key, [])
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                raise ValueError(f"Lista de facturación no válida: {key}.")
            return value

        rows = [CostRow(
            id=text(row, "id"), chapter=text(row, "chapter"), item=text(row, "item"),
            resource=text(row, "resource"), description=text(row, "description"),
            kind=text(row, "kind", "Otro"), unit=text(row, "unit"),
            budget=amount(row.get("budget", 0)),
        ) for row in records("rows")]
        row_ids = {row.id for row in rows}
        if "" in row_ids or len(row_ids) != len(rows):
            raise ValueError("Identificadores de costes vacíos o duplicados.")
        invoices = []
        for record in records("invoices"):
            raw_lines = record.get("lines", [])
            raw_amounts = record.get("amounts", {})
            if not isinstance(raw_lines, list) or not isinstance(raw_amounts, dict):
                raise ValueError("Líneas o importes de factura no válidos.")
            lines = []
            for line in raw_lines:
                if not isinstance(line, dict):
                    raise ValueError("Línea de factura no válida.")
                row_id = text(line, "row_id")
                if row_id and row_id not in row_ids:
                    raise ValueError("La línea de factura hace referencia a un coste inexistente.")
                lines.append(InvoiceLine(text(line, "item"), amount(line.get("amount", 0)), row_id))
            if any(key not in row_ids for key in raw_amounts):
                raise ValueError("Hay importes asignados a costes inexistentes.")
            invoices.append(Invoice(
                id=text(record, "id"), name=text(record, "name"),
                path=text(record, "path"), digest=text(record, "digest"),
                work=text(record, "work"), reference=text(record, "reference"),
                date=text(record, "date"), declared=amount(record.get("declared", 0)),
                notes=text(record, "notes"), lines=lines,
                amounts={key: amount(value) for key, value in raw_amounts.items()},
            ))
        ids = {invoice.id for invoice in invoices}
        if "" in ids or len(ids) != len(invoices):
            raise ValueError("Identificadores de facturas vacíos o duplicados.")
        return cls(text(data, "folder"), rows, invoices)


def is_rcd_item(item: ProjectItem) -> bool:
    """Reconoce gestión y transporte generados por RCDs mediante sus marcas persistentes."""
    from rcd import RCD_BUDGET_SOURCE, TRANSPORT_PREFIX

    return item.source.startswith(RCD_BUDGET_SOURCE) or any(
        line.comment.startswith(TRANSPORT_PREFIX) for line in item.measurement_lines
    )


def budget_rows(app: BudgetApp) -> tuple[list[CostRow], list[str]]:
    """Desglosa auxiliares; no incluye porcentajes ni inventa precios ausentes."""
    rows: list[CostRow] = []
    warnings: list[str] = []
    for item in app.budget_items:
        if is_rcd_item(item):
            direct = item.unit_price if item.direct_unit_price is None else item.direct_unit_price
            rows.append(CostRow(
                identifier(), item.chapter, item.code, item.code, item.summary, "RCDs", item.unit,
                amount(Decimal(str(direct)) * Decimal(str(item.measurement))),
            ))
            continue
        grouped: dict[tuple[str, str, float], CostRow] = {}
        costs: dict[tuple[str, str, float], Decimal] = {}
        dataset = app._component_dataset(item)

        def walk(concept, quantity: Decimal, price: float, stack: tuple[str, ...], kind: str):
            """Acumula recursos finales y detecta auxiliares circulares o incompletos."""
            if kind == "%" or concept.unit == "%" or concept.code.startswith("%"):
                return
            if concept.code in stack:
                raise ValueError(f"Auxiliar circular: {concept.code}.")
            if concept.children:
                if dataset is None:
                    raise ValueError(f"Falta la base del auxiliar {concept.code}.")
                for code, factor in concept.children:
                    child = dataset.concepts.get(code)
                    if child is None:
                        raise ValueError(f"Falta el recurso {code}.")
                    walk(child, quantity * Decimal(str(factor)), child.price,
                         (*stack, concept.code), child.ctype)
                return
            if kind == "aux":
                raise ValueError(f"Falta la descomposición del auxiliar {concept.code}.")
            key = (concept.code, kind, price)
            cost = quantity * Decimal(str(price)) * Decimal(str(item.measurement))
            if key not in grouped:
                grouped[key] = CostRow(
                    identifier(), item.chapter, item.code, concept.code, concept.summary,
                    {"1": "Mano de obra", "2": "Maquinaria", "3": "Material"}.get(kind, "Otro"),
                    concept.unit,
                )
            costs[key] = costs.get(key, Decimal(0)) + cost

        try:
            for concept, quantity, price in app._report_components(item):
                walk(concept, Decimal(str(quantity)), price, (),
                     app._budget_concept_type(item, concept.code))
        except ValueError as exc:
            grouped.clear()
            warnings.append(f"{item.code}: {exc}")
        if grouped:
            for key, row in grouped.items():
                row.budget = amount(costs[key])
            rows.extend(grouped.values())
        else:
            direct = item.unit_price if item.direct_unit_price is None else item.direct_unit_price
            rows.append(CostRow(
                identifier(), item.chapter, item.code, "", item.summary, "Otro", item.unit,
                amount(Decimal(str(direct)) * Decimal(str(item.measurement))),
            ))
    return rows, warnings


def parse_invoice_tables(tables: list[list[list[str | None]]], text: str) -> tuple[list[InvoiceLine], list[str]]:
    """Lee cabeceras explícitas; deja sin interpretar tablas ambiguas."""
    lines: list[InvoiceLine] = []
    warnings: list[str] = []
    for table in tables:
        columns: dict[str, int] = {}
        for row in table:
            cells = [str(cell or "").strip() for cell in row]
            headers = {}
            for index, cell in enumerate(cells):
                label = header_name(cell)
                if label:
                    headers[label] = index
            if "partida" in headers and "importe" in headers:
                columns = headers
                continue
            if not columns or max(columns.values()) >= len(cells):
                continue
            item = cells[columns["partida"]]
            value = cells[columns["importe"]]
            if not item and not value:
                continue
            if normalized(item).startswith(("total", "subtotal", "iva", "base imponible")):
                continue
            try:
                if not item:
                    raise ValueError("Falta la partida.")
                lines.append(InvoiceLine(item, amount(value)))
            except ValueError:
                warnings.append(f"Revisar línea: {' | '.join(cells)}")
    if lines:
        return lines, warnings
    # Algunos PDF usan columnas de texto sin bordes ni objetos de tabla.
    header = False
    for raw in text.splitlines():
        cells = [cell.strip() for cell in re.split(r"\s{2,}|\t|\|", raw.strip()) if cell.strip()]
        labels = [header_name(cell) for cell in cells]
        if "partida" in labels and "importe" in labels:
            header = True
            columns = {label: index for index, label in enumerate(labels) if label in KEYWORDS}
            continue
        if not header:
            continue
        if any(normalized(raw).startswith(prefix) for prefix in ("total", "subtotal", "iva", "base imponible")):
            header = False
            continue
        if max(columns.values()) >= len(cells):
            if raw.strip():
                warnings.append(f"Revisar línea: {raw.strip()}")
            continue
        try:
            lines.append(InvoiceLine(cells[columns["partida"]], amount(cells[columns["importe"]])))
        except ValueError:
            warnings.append(f"Revisar línea: {raw.strip()}")
    return lines, warnings


def read_invoice(path: Path) -> Invoice:
    """Lee todas las páginas y conserva las incidencias junto a la factura."""
    import pdfplumber
    from pdfplumber.utils.exceptions import PdfminerException
    from pdfminer.pdfparser import PDFSyntaxError
    from pdfminer.pdfdocument import PDFPasswordIncorrect

    invoice = Invoice(identifier(), path.stem, path=str(path.resolve()))
    try:
        invoice.digest = sha256(path.read_bytes()).hexdigest()
        texts = []
        metadata: dict[str, list[str]] = {"obra": [], "expediente": [], "fecha": []}
        with pdfplumber.open(path) as document:
            for page in document.pages:
                text = page.extract_text(layout=True) or ""
                texts.append(text)
                tables = page.extract_tables()
                lines, warnings = parse_invoice_tables(tables, text)
                invoice.lines.extend(lines)
                for table in tables:
                    columns = {}
                    for row in table:
                        cells = [str(cell or "").strip() for cell in row]
                        headers = {header_name(cell): index for index, cell in enumerate(cells)
                                   if header_name(cell)}
                        if "partida" in headers and "importe" in headers:
                            columns = headers
                            continue
                        if len(cells) >= 2 and header_name(cells[0]) in metadata:
                            label = header_name(cells[0])
                            if cells[1] and cells[1] not in metadata[label]:
                                metadata[label].append(cells[1])
                        for label in metadata:
                            if label in columns and columns[label] < len(cells):
                                value = cells[columns[label]]
                                if value and value not in metadata[label]:
                                    metadata[label].append(value)
                if warnings:
                    invoice.notes += "\n".join(warnings) + "\n"
        text = "\n".join(texts)
        if not text.strip():
            invoice.notes += "PDF sin texto extraíble (escaneado). Requiere OCR externo o entrada manual.\n"
        missing = [word for word in KEYWORDS if not re.search(rf"\b{word}\b", normalized(text))]
        if missing:
            invoice.notes += f"Palabras clave no encontradas: {', '.join(missing)}.\n"
        for label, attribute in (("Obra", "work"), ("Expediente", "reference"), ("Fecha", "date")):
            match = re.search(
                rf"(?im)^\s*{label}\s*[:=]\s*(.+?)(?=\s{{2,}}(?:Obra|Expediente|Fecha)\b|$)",
                text,
            )
            if match:
                setattr(invoice, attribute, match.group(1).strip())
            elif metadata[normalized(label)]:
                setattr(invoice, attribute, " | ".join(metadata[normalized(label)]))
        invoice.declared = total(line.amount for line in invoice.lines)
        if not invoice.lines:
            invoice.notes += "No se ha reconocido una tabla Partida / Importe. Completa los importes manualmente.\n"
    except (OSError, ValueError, PDFSyntaxError, PDFPasswordIncorrect, PdfminerException) as exc:
        invoice.notes += f"No se pudo leer el PDF: {exc}"
    return invoice


def assign_matching_lines(invoice: Invoice, rows: list[CostRow]) -> None:
    """Solo acepta códigos de recurso exactos y únicos, no similitudes de texto."""
    for line in invoice.lines:
        matches = [
            row for row in rows
            if row.resource and normalized(line.item) in {
                normalized(row.resource), normalized(f"{row.item} / {row.resource}"),
            }
        ]
        if len(matches) == 1:
            assign_line(invoice, line, matches[0].id)


def assign_line(invoice: Invoice, line: InvoiceLine, row_id: str) -> None:
    """Mueve el importe de una línea sin duplicarlo en la asignación anterior."""
    if line.row_id:
        invoice.amounts[line.row_id] = total((invoice.amounts.get(line.row_id, 0), -line.amount))
    line.row_id = row_id
    if row_id:
        invoice.amounts[row_id] = total((invoice.amounts.get(row_id, 0), line.amount))


class InvoicingWindow(tk.Toplevel):
    def __init__(
        self, master: tk.Misc, app: BudgetApp, state: InvoiceState,
        on_change: Callable[[InvoiceState], None], on_save: Callable[[], bool],
    ):
        """Abre directamente la tabla con una copia validada del estado del proyecto."""
        super().__init__(master)
        self.title("Facturación")
        self.geometry("1450x760")
        self.minsize(1000, 550)
        self.app = app
        self.data = InvoiceState.from_dict(state.to_dict())
        self.on_change = on_change
        self.on_save = on_save
        self._is_dirty = False
        self._invoice_ids: list[str] = []
        self._visible_invoice_ids: list[str] = []
        self.resource_filter = ""
        self._cell_editor: ttk.Entry | None = None
        self._cell_commit: Callable[[], bool] | None = None
        self._committing_cell = False
        self._closed = threading.Event()
        self._results: queue.Queue[Invoice | None] = queue.Queue()
        self._scanning = False
        self._syncing = False
        self._scan_notices: list[str] = []
        self._after: str | None = None
        self._pdf_after: str | None = None
        self._pdf_source: tuple[str, str, str] | None = None
        self._pdf_page = 0
        self._pdf_pages = 0
        self._pdf_zoom = 1.0
        self._pdf_photo = None
        self._pdf_failed = False
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self.folder = tk.StringVar(value=self.data.folder)
        if not self.data.rows:
            self.data.rows, warnings = budget_rows(self.app)
            if warnings:
                messagebox.showwarning("Facturación", "\n".join(warnings), parent=self)
        else:
            rcd_items = {
                (item.chapter, item.code) for item in self.app.budget_items
                if is_rcd_item(item)
            }
            for row in self.data.rows:
                if row.kind == "Otro" and (row.chapter, row.item) in rcd_items:
                    row.kind = "RCDs"
        self._build_matrix()
        self._refresh()
        if self.data.to_dict() != state.to_dict():
            self.changed()

    def changed(self):
        """Publica una copia validada de los cambios en el proyecto activo."""
        self.data.invoices.sort(key=invoice_sort_key)
        self.on_change(InvoiceState.from_dict(self.data.to_dict()))
        self._set_dirty(True)

    def _set_dirty(self, value: bool) -> None:
        """Indica cambios pendientes con el mismo disquete que las otras ventanas."""
        self._is_dirty = value
        if hasattr(self, "save_button"):
            self.save_button.configure(
                background="#000000" if value else self._save_button_background,
                foreground="#FFFFFF" if value else self._save_button_foreground,
                activebackground="#000000" if value else self._save_button_background,
                activeforeground="#FFFFFF" if value else self._save_button_foreground,
            )

    def _build_matrix(self):
        """Construye la matriz editable con los totales en un panel fijo."""
        from bc3_gui import ToolTip

        self.splitter = ttk.PanedWindow(self, orient="horizontal")
        self.splitter.pack(fill="both", expand=True)
        self.billing_panel = ttk.Frame(self.splitter)
        self.pdf_panel = ttk.Frame(self.splitter, padding=8)
        self.splitter.add(self.billing_panel, weight=1)
        self.splitter.add(self.pdf_panel, weight=1)
        self._split_initialized = False
        self.splitter.bind("<Configure>", self._initialize_split)
        self._build_pdf_viewer()
        toolbar = ttk.Frame(self.billing_panel, padding=8)
        toolbar.pack(fill="x")
        for index, (label, command) in enumerate((
            ("Carpeta…", self.change_folder), ("Actualizar lectura", self.scan),
            ("Revisar líneas", self.review_lines), ("Eliminar factura", self.delete_invoice),
        )):
            ttk.Button(toolbar, text=label, command=command).grid(
                row=index // 6, column=index % 6, sticky="w", padx=2, pady=2,
            )
        self.save_button = tk.Button(toolbar, text="💾", width=3, command=self.save)
        self._save_button_background = self.save_button.cget("background")
        self._save_button_foreground = self.save_button.cget("foreground")
        self.save_button.grid(row=0, column=4, sticky="e", padx=2, pady=2)
        ToolTip(self.save_button, "Guardar los cambios de facturación en el proyecto (Ctrl+S).")
        self.bind("<Control-s>", lambda _event: self.save())
        self._set_dirty(self._is_dirty)
        ttk.Label(self.billing_panel, textvariable=self.folder, wraplength=600).pack(fill="x", padx=8)
        filters = ttk.Frame(self.billing_panel, padding=(8, 0))
        filters.pack(fill="x")
        self.filter_buttons: dict[str, ttk.Checkbutton] = {}
        self.filter_variables: dict[str, tk.BooleanVar] = {}
        for kind, label in (("Material", "▧"), ("Maquinaria", "⚙"), ("RCDs", "RCDs")):
            variable = tk.BooleanVar(self, value=False)
            button = ttk.Checkbutton(
                filters, text=label, variable=variable, style="BudgetFilter.TCheckbutton",
                command=lambda selected=kind: self.toggle_resource_filter(selected),
            )
            button.pack(side="left", padx=(0, 6))
            self.filter_buttons[kind] = button
            self.filter_variables[kind] = variable
            ToolTip(button, f"Filtrar costes: {kind}. Pulsar de nuevo para mostrar todos.")
        ttk.Label(filters, text="Periodo acumulado:").pack(side="left", padx=(12, 6))
        self.period_selector = ttk.Combobox(
            filters, state="readonly", values=[ALL_PERIODS], width=24,
        )
        self.period_selector.set(translate(ALL_PERIODS))
        self.period_selector.pack(side="left")
        self.period_selector.bind("<<ComboboxSelected>>", lambda _event: self._refresh())
        selector = ttk.Frame(self.billing_panel, padding=(8, 0))
        selector.pack(fill="x")
        ttk.Label(selector, text="Factura:").pack(side="left")
        self.invoice_selector = ttk.Combobox(selector, state="readonly", width=55)
        self.invoice_selector.pack(side="left", fill="x", expand=True, padx=8)
        self.invoice_selector.bind("<<ComboboxSelected>>", lambda _event: self._refresh())
        ttk.Label(
            self.billing_panel, wraplength=600,
            text="Doble clic en el importe de la factura seleccionada para vincularlo a un recurso. "
                 "Enter confirma; Esc cancela. Los totales de la derecha corresponden al periodo acumulado.",
        ).pack(fill="x", padx=8, pady=(4, 0))
        register_frame = ttk.Frame(self.billing_panel, padding=(8, 6))
        register_frame.pack(fill="x")
        register_frame.columnconfigure(0, weight=1)
        self.invoice_register = ttk.Treeview(
            register_frame, columns=("date", "filename"),
            show="headings", selectmode="browse", height=4,
        )
        for column, heading, width in (
            ("date", "Fecha", 110),
            ("filename", "Nombre del fichero (clic para abrir)", 350),
        ):
            self.invoice_register.heading(column, text=heading)
            self.invoice_register.column(column, width=width, stretch=column == "filename")
        self.invoice_register.grid(row=0, column=0, sticky="ew")
        register_scroll = ttk.Scrollbar(register_frame, command=self.invoice_register.yview)
        register_scroll.grid(row=0, column=1, sticky="ns")
        self.invoice_register.configure(yscrollcommand=register_scroll.set)
        self.invoice_register.bind("<<TreeviewSelect>>", self._select_registered_invoice)
        self.invoice_register.bind("<ButtonRelease-1>", self.open_invoice_file)
        self.status = ttk.Label(self.billing_panel, wraplength=600)
        self.status.pack(fill="x", padx=8, pady=6)
        container = ttk.Frame(self.billing_panel, padding=8)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(container, show="headings", selectmode="browse")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.totals = ttk.Treeview(
            container, show="headings", columns=("budget", "billed", "remaining"),
            selectmode="browse", height=15,
        )
        self.totals.grid(row=0, column=1, sticky="ns")
        for column, heading in (("budget", "Previsto"), ("billed", "Facturado"), ("remaining", "Restante")):
            self.totals.heading(column, text=heading)
            self.totals.column(column, width=110, stretch=False, anchor="e")
        self.totals.tag_configure("over", foreground="#B91C1C")
        horizontal = ttk.Scrollbar(container, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.vertical = ttk.Scrollbar(container, command=self._scroll)
        self.vertical.grid(row=0, column=2, sticky="ns")
        self.tree.configure(yscrollcommand=lambda first, last: self._sync_scroll(self.totals, first, last))
        self.totals.configure(yscrollcommand=lambda first, last: self._sync_scroll(self.tree, first, last))
        for tree in (self.tree, self.totals):
            tree.bind("<<TreeviewSelect>>", lambda _event, source=tree: self._sync_selection(source))
        self.tree.bind("<Double-1>", self.edit_cell)
        self.tree.bind("<Configure>", lambda _event: self._close_cell_editor())
        self.footer = ttk.Label(self.billing_panel, style="Total.TLabel", wraplength=600)
        self.footer.pack(fill="x", padx=8, pady=8)
        localize_widget_tree(self)

    def _initialize_split(self, event=None) -> None:
        """Coloca la separación inicial al 50 % sin impedir ajustes posteriores."""
        width = self.splitter.winfo_width()
        if not self._split_initialized and width > 100:
            self.splitter.sashpos(0, width // 2)
            self._split_initialized = True

    def _build_pdf_viewer(self) -> None:
        """Prepara el visor con navegación, zoom y barras de desplazamiento."""
        from bc3_gui import ToolTip

        self.pdf_panel.columnconfigure(0, weight=1)
        self.pdf_panel.rowconfigure(2, weight=1)
        controls = ttk.Frame(self.pdf_panel)
        controls.grid(row=0, column=0, sticky="ew")
        self.pdf_previous = ttk.Button(controls, text="←", width=3, command=lambda: self._change_pdf_page(-1))
        self.pdf_previous.pack(side="left")
        ToolTip(self.pdf_previous, "Página anterior")
        self.pdf_next = ttk.Button(controls, text="→", width=3, command=lambda: self._change_pdf_page(1))
        self.pdf_next.pack(side="left", padx=4)
        ToolTip(self.pdf_next, "Página siguiente")
        self.pdf_page_label = ttk.Label(controls)
        self.pdf_page_label.pack(side="left", padx=4)
        self.pdf_zoom_selector = ttk.Combobox(
            controls, values=("50%", "75%", "100%", "125%", "150%", "200%"),
            state="readonly", width=6,
        )
        self.pdf_zoom_selector.set("100%")
        self.pdf_zoom_selector.pack(side="left", padx=4)
        self.pdf_zoom_selector.bind("<<ComboboxSelected>>", self._change_pdf_zoom)
        ToolTip(self.pdf_zoom_selector, "Zoom respecto al ancho del panel")
        ttk.Button(controls, text="Abrir PDF externo", command=self.open_external_pdf).pack(side="right")
        self.pdf_status = ttk.Label(self.pdf_panel, wraplength=600)
        self.pdf_status.grid(row=1, column=0, sticky="ew", pady=6)
        canvas_frame = ttk.Frame(self.pdf_panel)
        canvas_frame.grid(row=2, column=0, sticky="nsew")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)
        self.pdf_canvas = tk.Canvas(canvas_frame, background="#505050", highlightthickness=0)
        self.pdf_canvas.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(canvas_frame, command=self.pdf_canvas.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.pdf_canvas.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.pdf_canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.pdf_canvas.bind("<Configure>", self._schedule_pdf_render)
        self.pdf_canvas.bind("<MouseWheel>", self._scroll_pdf)

    def _scroll_pdf(self, event) -> str:
        """Desplaza el documento con la rueda sin afectar a la tabla de facturación."""
        self.pdf_canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def _show_invoice_pdf(self, invoice: Invoice | None) -> None:
        """Sincroniza el documento con la factura y conserva la página al editar costes."""
        source = (invoice.id, invoice.path, invoice.digest) if invoice else None
        if source == self._pdf_source:
            if source is None:
                self._render_pdf()
            return
        self._pdf_source = source
        self._pdf_page = 0
        self._pdf_pages = 0
        self._pdf_failed = False
        self.pdf_canvas.delete("all")
        self._pdf_photo = None
        self._render_pdf()

    def _schedule_pdf_render(self, _event=None) -> None:
        """Agrupa redimensionados para ajustar la página al ancho disponible."""
        if self._pdf_after is not None:
            self.after_cancel(self._pdf_after)
        self._pdf_after = self.after(180, self._render_pdf)

    def _render_pdf(self) -> None:
        """Dibuja la página actual o muestra explícitamente el error en el panel."""
        from PIL import ImageTk

        if self._pdf_after is not None:
            self.after_cancel(self._pdf_after)
            self._pdf_after = None
        source = self._pdf_source
        self.pdf_previous.configure(state="disabled")
        self.pdf_next.configure(state="disabled")
        if source is None:
            self.pdf_status.configure(text=translate("Selecciona una factura para visualizar su PDF."))
            self.pdf_page_label.configure(text="")
            return
        if self._pdf_failed:
            return
        path = Path(source[1]).expanduser()
        try:
            if not source[1].strip() or path.suffix.lower() != ".pdf" or not path.is_file():
                raise ValueError(f"No se encuentra un PDF válido:\n{source[1]}")
            image, count = render_invoice_page(
                path, self._pdf_page, max(100, self.pdf_canvas.winfo_width() - 16), self._pdf_zoom,
            )
            try:
                self._pdf_photo = ImageTk.PhotoImage(image, master=self.pdf_canvas)
            finally:
                image.close()
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            self.pdf_canvas.delete("all")
            self._pdf_photo = None
            self._pdf_pages = 0
            self._pdf_failed = True
            self.pdf_page_label.configure(text="")
            self.pdf_status.configure(text=f"{translate('No se pudo visualizar la factura:')} {exc}")
            return
        self._pdf_pages = count
        self.pdf_canvas.delete("all")
        self.pdf_canvas.create_image(8, 8, anchor="nw", image=self._pdf_photo)
        self.pdf_canvas.configure(
            scrollregion=(0, 0, self._pdf_photo.width() + 16, self._pdf_photo.height() + 16),
        )
        self.pdf_status.configure(text=path.name)
        self.pdf_page_label.configure(text=f"{self._pdf_page + 1} / {count}")
        self.pdf_previous.configure(state="normal" if self._pdf_page > 0 else "disabled")
        self.pdf_next.configure(state="normal" if self._pdf_page + 1 < count else "disabled")

    def _change_pdf_page(self, direction: int) -> None:
        """Cambia de página dentro de los límites del documento seleccionado."""
        target = self._pdf_page + direction
        if 0 <= target < self._pdf_pages:
            self._pdf_page = target
            self._render_pdf()
            self.pdf_canvas.yview_moveto(0)
            self.pdf_canvas.xview_moveto(0)

    def _change_pdf_zoom(self, _event=None) -> None:
        """Ajusta el tamaño de la página sin cambiar la factura activa."""
        self._pdf_zoom = int(self.pdf_zoom_selector.get().rstrip("%")) / 100
        self._render_pdf()

    def _scroll(self, *args):
        """Desplaza verticalmente ambos paneles mediante una sola barra."""
        self._close_cell_editor()
        self.tree.yview(*args)
        self.totals.yview(*args)

    def _sync_scroll(self, other, first, last):
        """Mantiene alineadas las filas al desplazar cualquiera de los paneles."""
        self.vertical.set(first, last)
        if not self._syncing:
            self._syncing = True
            other.yview_moveto(first)
            self._syncing = False

    def _sync_selection(self, source):
        """Comparte la selección entre la matriz y su panel de totales."""
        selection = source.selection()
        target = self.totals if source is self.tree else self.tree
        if selection and target.selection() != selection:
            target.selection_set(selection)

    def _refresh(self):
        """Reconstruye las columnas y calcula los saldos de todos los costes."""
        from bc3_gui import fmt_eur

        self._close_cell_editor()
        selected = self.tree.selection()
        invoice_index = self.invoice_selector.current()
        selected_invoice_id = (
            self._invoice_ids[invoice_index] if 0 <= invoice_index < len(self._invoice_ids) else None
        )
        self.data.invoices.sort(key=invoice_sort_key)
        active_invoice = next(
            (invoice for invoice in self.data.invoices if invoice.id == selected_invoice_id),
            self.data.invoices[0] if self.data.invoices else None,
        )
        period_label = self.period_selector.get()
        period = (
            ALL_PERIODS if period_label == translate(ALL_PERIODS)
            else UNDATED_PERIOD if period_label == translate(UNDATED_PERIOD) else period_label
        )
        months = sorted({
            invoice_month(invoice) for invoice in self.data.invoices
            if invoice_month(invoice) != UNDATED_PERIOD
        })
        periods = [ALL_PERIODS, *months, UNDATED_PERIOD]
        self.period_selector.configure(values=[translate(value) for value in periods])
        if period not in periods:
            period = ALL_PERIODS
            self.period_selector.set(translate(period))
        invoices = period_invoices(self.data.invoices, period)
        self._visible_invoice_ids = [active_invoice.id] if active_invoice is not None else []
        rows = [row for row in self.data.rows if not self.resource_filter or row.kind == self.resource_filter]
        columns = ["chapter", "item", "resource", "description", "kind", "unit"]
        columns.extend(self._visible_invoice_ids)
        self.tree.configure(columns=columns)
        for column, heading, width in (
            ("chapter", "Capítulo", 90), ("item", "Partida", 100), ("resource", "Recurso", 100),
            ("description", "Descripción", 240), ("kind", "Tipo", 105), ("unit", "Unidad", 60),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, stretch=False)
        if active_invoice is not None:
            self.tree.heading(active_invoice.id, text=active_invoice.date or translate("Sin fecha"))
            self.tree.column(active_invoice.id, width=140, stretch=False, anchor="e")
        for tree in (self.tree, self.totals):
            for row_id in tree.get_children():
                tree.delete(row_id)
        for row in rows:
            billed = total(invoice.amounts.get(row.id, 0) for invoice in invoices)
            remaining = total((row.budget, -billed))
            self.tree.insert("", "end", iid=row.id, values=(
                row.chapter, row.item, row.resource, row.description, row.kind, row.unit,
                *(
                    (fmt_eur(active_invoice.amounts.get(row.id, 0)),)
                    if active_invoice is not None else ()
                ),
            ))
            self.totals.insert("", "end", iid=row.id, values=(
                fmt_eur(row.budget), fmt_eur(billed), fmt_eur(remaining),
            ), tags=("over",) if remaining < 0 else ())
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected)
        self.invoice_selector.configure(values=[
            f"{invoice.date or translate('Sin fecha')} — {invoice.name}"
            for invoice in self.data.invoices
        ])
        self._invoice_ids = [invoice.id for invoice in self.data.invoices]
        for invoice_id in self.invoice_register.get_children():
            self.invoice_register.delete(invoice_id)
        for invoice in self.data.invoices:
            self.invoice_register.insert("", "end", iid=invoice.id, values=(
                invoice.date, Path(invoice.path).name if invoice.path else "",
            ))
        if self.data.invoices:
            index = self._invoice_ids.index(selected_invoice_id) if selected_invoice_id in self._invoice_ids else 0
            self.invoice_selector.current(index)
        else:
            self.invoice_selector.set("")
        budget = total(row.budget for row in rows)
        billed = total(invoice.amounts.get(row.id, 0) for invoice in invoices for row in rows)
        billed_heading = (
            "Facturado" if period == ALL_PERIODS else
            "Sin fecha" if period == UNDATED_PERIOD else "Acumulado"
        )
        self.totals.heading("billed", text=translate(billed_heading))
        self.totals.heading("remaining", text=translate("Restante" if period == ALL_PERIODS else "Diferencia"))
        undated = sum(invoice_month(invoice) == UNDATED_PERIOD for invoice in self.data.invoices)
        context = (
            "" if period == ALL_PERIODS else
            f" | Sin fecha válida ({undated} facturas)" if period == UNDATED_PERIOD else
            f" | Acumulado hasta {period} | Sin fecha válida excluidas: {undated}"
        )
        self.footer.config(text=f"Previsto: {fmt_eur(budget)}    Facturado: {fmt_eur(billed)}    "
                           f"Restante: {fmt_eur(total((budget, -billed)))}{context}")
        self.show_invoice_status()

    def toggle_resource_filter(self, kind: str) -> None:
        """Filtra las filas sin modificar sus identificadores ni sus asignaciones."""
        self.resource_filter = "" if self.resource_filter == kind else kind
        for code, variable in self.filter_variables.items():
            variable.set(code == self.resource_filter)
        self._refresh()

    def selected_invoice(self) -> Invoice | None:
        """Obtiene la factura seleccionada o indica que falta seleccionarla."""
        index = self.invoice_selector.current()
        if 0 <= index < len(self._invoice_ids):
            invoice_id = self._invoice_ids[index]
            invoice = next((invoice for invoice in self.data.invoices if invoice.id == invoice_id), None)
            if invoice is not None:
                return invoice
        messagebox.showinfo("Facturación", "Selecciona una factura.", parent=self)
        return None

    def select_invoice(self, invoice_id: str):
        """Selecciona una factura y reconstruye su única columna editable."""
        index = next(index for index, invoice in enumerate(self.data.invoices) if invoice.id == invoice_id)
        self.invoice_selector.current(index)
        self._refresh()

    def show_invoice_status(self, _event=None):
        """Muestra los datos, incidencias y diferencias de la factura activa."""
        from bc3_gui import fmt_eur

        index = self.invoice_selector.current()
        if index < 0 or index >= len(self._invoice_ids):
            self.status.config(text="Selecciona una carpeta y pulsa Actualizar lectura para cargar las facturas.")
            self._show_invoice_pdf(None)
            return
        invoice_id = self._invoice_ids[index]
        invoice = next((invoice for invoice in self.data.invoices if invoice.id == invoice_id), None)
        if invoice is None:
            return
        if self.invoice_register.selection() != (invoice.id,):
            self.invoice_register.selection_set(invoice.id)
            self.invoice_register.see(invoice.id)
        pending = sum(not line.row_id for line in invoice.lines)
        difference = total((invoice.declared, -total(invoice.amounts.values())))
        self.status.config(text=(
            f"{invoice.name} | Obra: {invoice.work} | Expediente: {invoice.reference} | Fecha: {invoice.date}\n"
            f"Importe leído/declarado: {fmt_eur(invoice.declared)} | "
            f"Sin asignar/diferencia: {fmt_eur(difference)} | Líneas pendientes: {pending}\n"
            f"{invoice.notes[:500]}"
        ))
        self._show_invoice_pdf(invoice)

    def _select_registered_invoice(self, _event=None):
        """Sincroniza el listado de archivos con la factura activa de la matriz."""
        selection = self.invoice_register.selection()
        index = self.invoice_selector.current()
        current = self._invoice_ids[index] if 0 <= index < len(self._invoice_ids) else None
        if selection and selection[0] in self._invoice_ids and selection[0] != current:
            self.select_invoice(selection[0])

    def open_invoice_file(self, event):
        """Muestra en el visor integrado la factura cuyo fichero se ha pulsado."""
        if self.invoice_register.identify_region(event.x, event.y) != "cell":
            return
        if self.invoice_register.identify_column(event.x) != "#2":
            return
        invoice_id = self.invoice_register.identify_row(event.y)
        invoice = next((invoice for invoice in self.data.invoices if invoice.id == invoice_id), None)
        if invoice is None:
            return
        self.select_invoice(invoice.id)
        if self._pdf_failed:
            self._pdf_failed = False
            self._render_pdf()

    def open_external_pdf(self) -> None:
        """Permite abrir opcionalmente el PDF seleccionado en el visor del sistema."""
        invoice = self.selected_invoice()
        if invoice is None:
            return
        path = Path(invoice.path).expanduser()
        if not invoice.path.strip() or path.suffix.lower() != ".pdf" or not path.is_file():
            messagebox.showerror("Facturación", f"No se encuentra un PDF válido:\n{invoice.path}", parent=self)
            return
        try:
            os.startfile(path.resolve())
        except OSError as exc:
            messagebox.showerror("Facturación", f"No se pudo abrir la factura:\n{exc}", parent=self)

    def change_folder(self):
        """Guarda otra carpeta y relee sus documentos sin perder asignaciones previas."""
        if self._scanning:
            messagebox.showinfo("Facturación", "Espera a que termine la lectura antes de cambiar de carpeta.", parent=self)
            return
        chosen = filedialog.askdirectory(parent=self, initialdir=self.data.folder)
        if chosen:
            self.data.folder = chosen
            self.folder.set(chosen)
            self.changed()
            self.scan()

    def scan(self):
        """Inicia la lectura fuera del hilo de interfaz, sin modificar widgets."""
        if self._scanning:
            messagebox.showinfo("Facturación", "La lectura de PDF sigue en curso.", parent=self)
            return
        if not self.data.folder.strip():
            messagebox.showinfo("Facturación", "Selecciona una carpeta con el botón Carpeta…", parent=self)
            return
        try:
            import pdfplumber
        except ImportError as exc:
            messagebox.showerror(
                "Facturación",
                f"Falta pdfplumber. Instala las dependencias con:\n"
                f"python -m pip install -r requirements.txt\n\n{exc}", parent=self,
            )
            return
        folder = Path(self.data.folder)
        try:
            paths = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
        except OSError as exc:
            messagebox.showerror("Facturación", f"No se pudo leer la carpeta:\n{exc}", parent=self)
            return
        self._scanning = True
        self._close_cell_editor()
        self._scan_notices = []
        self.status.config(text=f"Leyendo {len(paths)} PDF…")
        closed = self._closed
        results = self._results

        def worker():
            """Entrega cada resultado o error explícito a la cola de la interfaz."""
            try:
                for path in paths:
                    if closed.is_set():
                        break
                    try:
                        result = read_invoice(path)
                    except Exception as exc:
                        # Aísla fallos del lector externo sin perder ni ocultar la factura afectada.
                        result = Invoice(identifier(), path.stem, path=str(path.resolve()),
                                         notes=f"Error al procesar el PDF: {type(exc).__name__}: {exc}")
                    results.put(result)
            finally:
                results.put(None)

        threading.Thread(target=worker, daemon=True).start()
        self._after = self.after(100, self._poll)

    def _poll(self):
        """Integra resultados en el hilo principal y conserva las ediciones previas."""
        finished = False
        imported = False
        notices = []
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                break
            if result is None:
                finished = True
                break
            same_path = next((invoice for invoice in self.data.invoices if invoice.path == result.path), None)
            duplicate = next((invoice for invoice in self.data.invoices
                              if result.digest and invoice.digest == result.digest), None)
            if same_path:
                if "No se pudo leer el PDF:" in result.notes or "Error al procesar el PDF:" in result.notes:
                    notices.append(f"{result.name}: {result.notes}. Se conservan los datos anteriores.")
                    continue
                previous = list(same_path.lines)
                for line in result.lines:
                    match = next(
                        (old for old in previous if old.item == line.item and old.amount == line.amount),
                        None,
                    )
                    if match is not None:
                        line.row_id = match.row_id
                        previous.remove(match)
                if any(line.row_id for line in previous):
                    result.notes += (
                        "\nHay líneas asignadas que han cambiado o desaparecido del PDF. "
                        "Sus importes se conservan como asignaciones manuales; revisa la diferencia."
                    )
                for key in ("name", "digest", "work", "reference", "date", "declared", "notes", "lines"):
                    setattr(same_path, key, getattr(result, key))
                imported = True
                if result.notes:
                    notices.append(f"{result.name}: {result.notes}")
                continue
            if duplicate:
                if not Path(duplicate.path).is_file():
                    duplicate.path = result.path
                    imported = True
                    continue
                notices.append(f"{result.name}: PDF duplicado de {duplicate.name}; no se ha añadido.")
                continue
            assign_matching_lines(result, self.data.rows)
            self.data.invoices.append(result)
            imported = True
            if result.notes:
                notices.append(f"{result.name}: {result.notes}")
        self._scan_notices.extend(notices)
        if imported:
            self.changed()
        if finished:
            self._scanning = False
            self._after = None
            self.changed()
            self._refresh()
            if self._scan_notices:
                summary = "\n".join(self._scan_notices)
                if len(summary) > 2000:
                    summary = summary[:2000] + "\n… Consulta Revisar líneas en cada factura para ver sus incidencias."
                messagebox.showwarning("Revisión de facturas", summary, parent=self)
            if not self.data.invoices:
                self.status.config(text="No se encontraron PDF en la carpeta seleccionada.")
        else:
            self._after = self.after(100, self._poll)

    def form(self, title: str, fields: list[tuple[str, str]], initial: dict, accept, parent=None):
        """Abre un formulario modal y aplica sus datos únicamente tras validarlos."""
        dialog = tk.Toplevel(parent or self)
        dialog.title(title)
        dialog.transient(parent or self)
        form = ttk.Frame(dialog, padding=12)
        form.pack(fill="both", expand=True)
        variables = {}
        for index, (key, label) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", padx=6, pady=4)
            variables[key] = tk.StringVar(value=str(initial.get(key, "")))
            ttk.Entry(form, textvariable=variables[key], width=70).grid(row=index, column=1, sticky="ew")

        def close():
            """Cierra el formulario y restaura la modalidad del diálogo de revisión."""
            dialog.destroy()
            if parent is not None and parent.winfo_exists():
                parent.grab_set()

        def commit():
            """Aplica los datos o mantiene el formulario abierto mostrando el error."""
            try:
                accept({key: variable.get() for key, variable in variables.items()})
            except ValueError as exc:
                messagebox.showerror("Facturación", str(exc), parent=dialog)
                return
            self.changed()
            self._refresh()
            close()

        ttk.Button(form, text="Guardar", command=commit).grid(row=len(fields), column=1, sticky="e", pady=8)
        dialog.protocol("WM_DELETE_WINDOW", close)
        dialog.bind("<Escape>", lambda _event: close())
        localize_widget_tree(dialog)
        dialog.grab_set()

    def delete_invoice(self):
        """Elimina una columna con confirmación, sin modificar el PDF original."""
        invoice = self.selected_invoice()
        if invoice and messagebox.askyesno("Facturación", "¿Eliminar esta columna y sus importes? "
                                           "No se borrará el PDF.", parent=self):
            self.data.invoices.remove(invoice)
            self.changed()
            self._refresh()

    def edit_cell(self, event):
        """Edita sobre la tabla únicamente el importe de la factura seleccionada."""
        row_id = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row_id or column != "#7" or not self._visible_invoice_ids:
            return
        self.tree.selection_set(row_id)
        invoice_id = self._visible_invoice_ids[0]
        invoice = next(invoice for invoice in self.data.invoices if invoice.id == invoice_id)
        bbox = self.tree.bbox(row_id, column)
        if not bbox:
            return
        self._close_cell_editor()
        editor = ttk.Entry(self.tree, justify="right")
        editor.insert(0, str(invoice.amounts.get(row_id, 0)))
        editor.select_range(0, "end")
        x, y, width, height = bbox
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        self._cell_editor = editor

        def commit(_event=None):
            """Confirma el importe contra la identidad de la factura que abrió la celda."""
            if self._cell_editor is not editor or self._committing_cell:
                return False
            self._committing_cell = True
            try:
                return self._commit_cell(invoice_id, row_id, editor.get())
            finally:
                self._committing_cell = False

        self._cell_commit = commit
        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", lambda _event: self._close_cell_editor())

    def _commit_cell(self, invoice_id: str, row_id: str, raw: str) -> bool:
        """Valida una asignación manual sin modificar otros costes ni otras facturas."""
        if self._visible_invoice_ids != [invoice_id] or not self.tree.exists(row_id):
            messagebox.showerror("Facturación", "La selección ha cambiado. Vuelve a editar el importe.", parent=self)
            return False
        try:
            value = amount(raw)
        except ValueError as exc:
            messagebox.showerror("Facturación", str(exc), parent=self)
            return False
        invoice = next(invoice for invoice in self.data.invoices if invoice.id == invoice_id)
        for line in invoice.lines:
            if line.row_id == row_id:
                line.row_id = ""
        invoice.amounts[row_id] = value
        self.changed()
        self._refresh()
        return True

    def _close_cell_editor(self) -> None:
        """Cancela una edición pendiente al cambiar de factura, filtro o tabla."""
        editor = self._cell_editor
        if editor is not None:
            self._cell_editor = None
            self._cell_commit = None
            editor.destroy()

    def review_lines(self):
        """Permite revisar, corregir y reasignar las líneas extraídas del PDF."""
        invoice = self.selected_invoice()
        if invoice is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Revisar líneas de factura")
        dialog.geometry("950x520")
        dialog.transient(self)
        detail = tk.Text(dialog, height=6, wrap="word")
        detail.pack(fill="x", padx=8, pady=8)
        detail.insert("1.0", f"{invoice.name} | {invoice.date}\n{invoice.path}\n{invoice.notes}")
        detail.configure(state="disabled")
        container = ttk.Frame(dialog)
        container.pack(fill="both", expand=True, padx=8)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        tree = ttk.Treeview(container, columns=("item", "amount", "row"), show="headings")
        for column, label in (("item", "Partida leída"), ("amount", "Importe"), ("row", "Asignación")):
            tree.heading(column, text=label)
            tree.column(column, width=250)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(container, command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        options = [f"{index + 1}. {row.chapter} / {row.item} / {row.resource} — {row.description} ({row.kind})"
                   for index, row in enumerate(self.data.rows)]
        selector = ttk.Combobox(dialog, values=options, state="readonly", width=100)
        selector.pack(fill="x", padx=8, pady=8)
        if options:
            selected = self.tree.selection()
            index = next(
                (index for index, row in enumerate(self.data.rows) if selected and row.id == selected[0]),
                0,
            )
            selector.current(index)

        def refresh():
            """Actualiza el detalle de las líneas con sus asignaciones actuales."""
            for child in tree.get_children():
                tree.delete(child)
            names = {row.id: f"{row.chapter} / {row.item} / {row.resource}" for row in self.data.rows}
            for index, line in enumerate(invoice.lines):
                tree.insert("", "end", iid=str(index), values=(
                    line.item, f"{line.amount:.2f}", names.get(line.row_id, "Pendiente"),
                ))

        def assign():
            """Asigna la línea seleccionada al coste elegido en el selector."""
            selected = tree.selection()
            index = selector.current()
            if not selected or index < 0:
                messagebox.showinfo("Facturación", "Selecciona una línea y un coste de destino.", parent=dialog)
                return
            assign_line(invoice, invoice.lines[int(selected[0])], self.data.rows[index].id)
            self.changed()
            self._refresh()
            refresh()

        def selected_line() -> InvoiceLine | None:
            """Solicita una línea antes de ejecutar una acción de revisión."""
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("Facturación", "Selecciona una línea.", parent=dialog)
                return None
            return invoice.lines[int(selected[0])]

        def unassign():
            """Deshace la asignación de una línea y descuenta su importe."""
            line = selected_line()
            if line is not None:
                assign_line(invoice, line, "")
                self.changed()
                self._refresh()
                refresh()

        def edit_line(new=False):
            """Abre un formulario para añadir o corregir una línea de factura."""
            line = InvoiceLine("", 0) if new else selected_line()
            if line is None:
                return

            def accept(values):
                """Actualiza la línea manteniendo coherente su importe asignado."""
                value = amount(values["amount"])
                if not values["item"].strip():
                    raise ValueError("Indica la partida o descripción de la línea.")
                row_id = line.row_id
                assign_line(invoice, line, "")
                line.item, line.amount = values["item"], value
                if new:
                    invoice.lines.append(line)
                assign_line(invoice, line, row_id)
                refresh()

            self.form("Editar línea de factura", [("item", "Partida"), ("amount", "Importe")],
                      {"item": line.item, "amount": line.amount}, accept, parent=dialog)

        def delete_line():
            """Elimina la línea seleccionada y su asignación tras confirmarla."""
            line = selected_line()
            if line is not None and messagebox.askyesno(
                "Facturación", "¿Eliminar esta línea y su asignación?", parent=dialog,
            ):
                assign_line(invoice, line, "")
                invoice.lines.remove(line)
                self.changed()
                self._refresh()
                refresh()

        buttons = ttk.Frame(dialog)
        buttons.pack(pady=8)
        for label, command in (
            ("Asignar al coste seleccionado", assign), ("Desasignar", unassign),
            ("Añadir línea", lambda: edit_line(True)), ("Editar línea", edit_line),
            ("Eliminar línea", delete_line),
        ):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=4)
        refresh()
        localize_widget_tree(dialog)
        dialog.grab_set()

    def save(self):
        """Publica el estado y solicita guardar el proyecto completo."""
        if self._cell_commit is not None and not self._cell_commit():
            return
        if self._scanning:
            messagebox.showinfo("Facturación", "Espera a que termine la lectura antes de guardar.", parent=self)
            return
        self.changed()
        if self.on_save():
            self._set_dirty(False)
            self.status.config(text="Facturación guardada con el proyecto.")

    def close(self):
        """Cancela lecturas pendientes y conserva los datos ya incorporados."""
        if self._cell_commit is not None and not self._cell_commit():
            return
        if self._scanning and not messagebox.askyesno(
            "Facturación", "La lectura sigue en curso. ¿Cancelar y cerrar? "
            "Puedes volver a leer la carpeta más adelante.", parent=self,
        ):
            return
        self._closed.set()
        if self._after is not None:
            self.after_cancel(self._after)
            self._after = None
        self.destroy()

    def _destroyed(self, event):
        """Cancela los callbacks incluso si el proyecto o la aplicación se cierran."""
        if event.widget is self:
            self._closed.set()
            if self._pdf_after is not None:
                self.after_cancel(self._pdf_after)
                self._pdf_after = None
            self._pdf_photo = None
            if self._after is not None:
                self.after_cancel(self._after)
                self._after = None
