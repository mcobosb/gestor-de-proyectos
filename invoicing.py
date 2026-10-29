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

from localization import filedialog, localize_widget_tree, messagebox, simpledialog

if TYPE_CHECKING:
    from bc3_gui import BudgetApp


KEYWORDS = ("obra", "expediente", "fecha", "partida", "importe")


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


def budget_rows(app: BudgetApp) -> tuple[list[CostRow], list[str]]:
    """Desglosa auxiliares; no incluye porcentajes ni inventa precios ausentes."""
    rows: list[CostRow] = []
    warnings: list[str] = []
    for item in app.budget_items:
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
        """Prepara la selección de carpeta con una copia validada del estado."""
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
        self._closed = threading.Event()
        self._results: queue.Queue[Invoice | None] = queue.Queue()
        self._scanning = False
        self._syncing = False
        self._scan_notices: list[str] = []
        self._after: str | None = None
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self.folder = tk.StringVar(value=self.data.folder)
        self._build_folder_page()
        localize_widget_tree(self)

    def _build_folder_page(self):
        """Muestra el primer paso de selección de la carpeta de facturas."""
        self.start = ttk.Frame(self, padding=24)
        self.start.pack(fill="both", expand=True)
        ttk.Label(self.start, text="Carpeta de facturas PDF").pack(anchor="w", pady=8)
        ttk.Entry(self.start, textvariable=self.folder, width=100).pack(fill="x", pady=8)
        ttk.Button(self.start, text="Examinar…", command=self.choose_folder).pack(anchor="w")
        ttk.Label(
            self.start, wraplength=850,
            text="Se leerán los PDF de esta carpeta, sin enviarlos a ningún servicio externo. "
            "Los PDF escaneados y los datos no reconocidos se señalarán para revisión manual. "
            "Las facturas ya importadas no se duplican ni sobrescriben.",
        ).pack(anchor="w", pady=16)
        ttk.Button(self.start, text="Continuar", command=self.continue_to_table,
                   style="Accent.TButton").pack(anchor="e", pady=12)

    def choose_folder(self):
        """Selecciona la carpeta inicial sin leer documentos todavía."""
        chosen = filedialog.askdirectory(parent=self, initialdir=self.folder.get() or None)
        if chosen:
            self.folder.set(chosen)

    def continue_to_table(self):
        """Valida la carpeta y el lector antes de generar la matriz y leer PDF."""
        path = Path(self.folder.get()).expanduser()
        if not self.folder.get().strip() or not path.is_dir():
            messagebox.showerror("Facturación", "Selecciona una carpeta existente.", parent=self)
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
        self.data.folder = str(path.resolve())
        if not self.data.rows:
            self.data.rows, warnings = budget_rows(self.app)
            if warnings:
                messagebox.showwarning("Facturación", "\n".join(warnings), parent=self)
        self.start.destroy()
        self._build_matrix()
        self._refresh()
        self.changed()
        self.scan()

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

        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        for index, (label, command) in enumerate((
            ("Carpeta…", self.change_folder), ("Leer PDF", self.scan),
            ("Añadir factura", self.add_invoice), ("Editar factura", self.edit_invoice),
            ("Revisar líneas", self.review_lines), ("Eliminar factura", self.delete_invoice),
            ("Añadir coste", self.add_row), ("Editar coste", self.edit_row),
            ("Eliminar coste", self.delete_row),
        )):
            ttk.Button(toolbar, text=label, command=command).grid(
                row=index // 6, column=index % 6, sticky="w", padx=2, pady=2,
            )
        self.save_button = tk.Button(toolbar, text="💾", width=3, command=self.save)
        self._save_button_background = self.save_button.cget("background")
        self._save_button_foreground = self.save_button.cget("foreground")
        self.save_button.grid(row=1, column=5, sticky="e", padx=2, pady=2)
        ToolTip(self.save_button, "Guardar los cambios de facturación en el proyecto (Ctrl+S).")
        self.bind("<Control-s>", lambda _event: self.save())
        self._set_dirty(self._is_dirty)
        selector = ttk.Frame(self, padding=(8, 0))
        selector.pack(fill="x")
        ttk.Label(selector, text="Factura:").pack(side="left")
        self.invoice_selector = ttk.Combobox(selector, state="readonly", width=55)
        self.invoice_selector.pack(side="left", padx=8)
        self.invoice_selector.bind("<<ComboboxSelected>>", self.show_invoice_status)
        register_frame = ttk.Frame(self, padding=(8, 6))
        register_frame.pack(fill="x")
        register_frame.columnconfigure(0, weight=1)
        self.invoice_register = ttk.Treeview(
            register_frame, columns=("date", "name", "filename"),
            show="headings", selectmode="browse", height=4,
        )
        for column, heading, width in (
            ("date", "Fecha", 110), ("name", "Factura", 250),
            ("filename", "Nombre del fichero (clic para abrir)", 500),
        ):
            self.invoice_register.heading(column, text=heading)
            self.invoice_register.column(column, width=width, stretch=column == "filename")
        self.invoice_register.grid(row=0, column=0, sticky="ew")
        register_scroll = ttk.Scrollbar(register_frame, command=self.invoice_register.yview)
        register_scroll.grid(row=0, column=1, sticky="ns")
        self.invoice_register.configure(yscrollcommand=register_scroll.set)
        self.invoice_register.bind("<<TreeviewSelect>>", self._select_registered_invoice)
        self.invoice_register.bind("<ButtonRelease-1>", self.open_invoice_file)
        self.status = ttk.Label(self, wraplength=1300)
        self.status.pack(fill="x", padx=8, pady=6)
        container = ttk.Frame(self, padding=8)
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
        self.totals.bind("<Double-1>", lambda _event: self.edit_row())
        self.footer = ttk.Label(self, style="Total.TLabel")
        self.footer.pack(fill="x", padx=8, pady=8)
        localize_widget_tree(self)

    def _scroll(self, *args):
        """Desplaza verticalmente ambos paneles mediante una sola barra."""
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

        selected = self.tree.selection()
        invoice_index = self.invoice_selector.current()
        selected_invoice_id = (
            self._invoice_ids[invoice_index] if 0 <= invoice_index < len(self._invoice_ids) else None
        )
        self.data.invoices.sort(key=invoice_sort_key)
        columns = ["chapter", "item", "resource", "description", "kind", "unit"]
        columns.extend(invoice.id for invoice in self.data.invoices)
        self.tree.configure(columns=columns)
        for column, heading, width in (
            ("chapter", "Capítulo", 90), ("item", "Partida", 100), ("resource", "Recurso", 100),
            ("description", "Descripción", 240), ("kind", "Tipo", 105), ("unit", "Unidad", 60),
        ):
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=width, stretch=False)
        for invoice in self.data.invoices:
            self.tree.heading(invoice.id, text=invoice.name,
                              command=lambda selected=invoice.id: self.select_invoice(selected))
            self.tree.column(invoice.id, width=140, stretch=False, anchor="e")
        for tree in (self.tree, self.totals):
            for row_id in tree.get_children():
                tree.delete(row_id)
        for row in self.data.rows:
            billed = total(invoice.amounts.get(row.id, 0) for invoice in self.data.invoices)
            remaining = total((row.budget, -billed))
            self.tree.insert("", "end", iid=row.id, values=(
                row.chapter, row.item, row.resource, row.description, row.kind, row.unit,
                *(fmt_eur(invoice.amounts.get(row.id, 0)) for invoice in self.data.invoices),
            ))
            self.totals.insert("", "end", iid=row.id, values=(
                fmt_eur(row.budget), fmt_eur(billed), fmt_eur(remaining),
            ), tags=("over",) if remaining < 0 else ())
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected)
        self.invoice_selector.configure(values=[invoice.name for invoice in self.data.invoices])
        self._invoice_ids = [invoice.id for invoice in self.data.invoices]
        for invoice_id in self.invoice_register.get_children():
            self.invoice_register.delete(invoice_id)
        for invoice in self.data.invoices:
            self.invoice_register.insert("", "end", iid=invoice.id, values=(
                invoice.date, invoice.name, Path(invoice.path).name if invoice.path else "",
            ))
        if self.data.invoices:
            index = self._invoice_ids.index(selected_invoice_id) if selected_invoice_id in self._invoice_ids else 0
            self.invoice_selector.current(index)
        else:
            self.invoice_selector.set("")
        budget = total(row.budget for row in self.data.rows)
        billed = total(value for invoice in self.data.invoices for value in invoice.amounts.values())
        self.footer.config(text=f"Previsto: {fmt_eur(budget)}    Facturado: {fmt_eur(billed)}    "
                           f"Restante: {fmt_eur(total((budget, -billed)))}")
        self.show_invoice_status()

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
        """Selecciona una factura al pulsar su cabecera de columna."""
        index = next(index for index, invoice in enumerate(self.data.invoices) if invoice.id == invoice_id)
        self.invoice_selector.current(index)
        self.show_invoice_status()

    def show_invoice_status(self, _event=None):
        """Muestra los datos, incidencias y diferencias de la factura activa."""
        from bc3_gui import fmt_eur

        index = self.invoice_selector.current()
        if index < 0 or index >= len(self._invoice_ids):
            self.status.config(text="Sin facturas. Puedes añadir columnas manuales.")
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

    def _select_registered_invoice(self, _event=None):
        """Sincroniza el listado de archivos con la factura activa de la matriz."""
        selection = self.invoice_register.selection()
        if selection and selection[0] in self._invoice_ids:
            self.select_invoice(selection[0])

    def open_invoice_file(self, event):
        """Abre únicamente el PDF local pulsado y notifica archivos ausentes o errores."""
        if self.invoice_register.identify_region(event.x, event.y) != "cell":
            return
        if self.invoice_register.identify_column(event.x) != "#3":
            return
        invoice_id = self.invoice_register.identify_row(event.y)
        invoice = next((invoice for invoice in self.data.invoices if invoice.id == invoice_id), None)
        if invoice is None:
            return
        self.select_invoice(invoice.id)
        path = Path(invoice.path).expanduser()
        if not invoice.path.strip() or path.suffix.lower() != ".pdf" or not path.is_file():
            messagebox.showerror("Facturación", f"No se encuentra un PDF válido:\n{invoice.path}", parent=self)
            return
        try:
            os.startfile(path.resolve())
        except OSError as exc:
            messagebox.showerror("Facturación", f"No se pudo abrir la factura:\n{exc}", parent=self)

    def change_folder(self):
        """Guarda otra carpeta y busca sus documentos sin perder columnas previas."""
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
        folder = Path(self.data.folder)
        try:
            paths = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
        except OSError as exc:
            messagebox.showerror("Facturación", f"No se pudo leer la carpeta:\n{exc}", parent=self)
            return
        self._scanning = True
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
                if same_path.digest != result.digest:
                    notices.append(f"{result.name}: el PDF ha cambiado. Se conserva la columna editada; "
                                   "elimínala y vuelve a leerlo para sustituirla.")
                continue
            if duplicate:
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
                self.status.config(text="No se encontraron PDF. Puedes añadir facturas manualmente.")
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

    def add_invoice(self):
        """Crea una columna manual mediante el formulario de factura."""
        self.edit_invoice(Invoice(identifier(), f"Factura {len(self.data.invoices) + 1}"))

    def edit_invoice(self, invoice: Invoice | None = None):
        """Permite modificar todos los metadatos de una columna de factura."""
        invoice = invoice or self.selected_invoice()
        if invoice is None:
            return

        def accept(values):
            """Valida el total declarado antes de modificar los metadatos."""
            declared = amount(values["declared"])
            if not values["name"].strip():
                raise ValueError("La factura necesita un nombre.")
            for key, value in values.items():
                setattr(invoice, key, declared if key == "declared" else value)
            if invoice not in self.data.invoices:
                self.data.invoices.append(invoice)

        self.form("Editar factura", [
            ("name", "Nombre de columna"), ("work", "Obra"), ("reference", "Expediente"),
            ("date", "Fecha"), ("declared", "Importe leído/declarado"),
            ("path", "Archivo PDF"), ("notes", "Observaciones"),
        ], asdict(invoice), accept)

    def delete_invoice(self):
        """Elimina una columna con confirmación, sin modificar el PDF original."""
        invoice = self.selected_invoice()
        if invoice and messagebox.askyesno("Facturación", "¿Eliminar esta columna y sus importes? "
                                           "No se borrará el PDF.", parent=self):
            self.data.invoices.remove(invoice)
            self.changed()
            self._refresh()

    def selected_row(self) -> CostRow | None:
        """Obtiene el coste seleccionado o solicita seleccionar una fila."""
        selection = self.tree.selection()
        if selection:
            return next(row for row in self.data.rows if row.id == selection[0])
        messagebox.showinfo("Facturación", "Selecciona una fila de coste.", parent=self)
        return None

    def add_row(self):
        """Añade un coste personalizado mediante el formulario de fila."""
        self.edit_row(CostRow(identifier()))

    def edit_row(self, row: CostRow | None = None):
        """Edita los datos identificativos y el importe previsto de un coste."""
        row = row or self.selected_row()
        if row is None:
            return

        def accept(values):
            """Valida el coste previsto antes de modificar la fila."""
            budget = amount(values["budget"])
            for key, value in values.items():
                setattr(row, key, budget if key == "budget" else value)
            if row not in self.data.rows:
                self.data.rows.append(row)

        self.form("Editar coste", [
            ("chapter", "Capítulo"), ("item", "Partida"), ("resource", "Recurso"),
            ("description", "Descripción"), ("kind", "Tipo"), ("unit", "Unidad"),
            ("budget", "Coste previsto (€)"),
        ], asdict(row), accept)

    def delete_row(self):
        """Elimina un coste y devuelve sus líneas de factura al estado pendiente."""
        row = self.selected_row()
        if row and messagebox.askyesno(
            "Facturación", "¿Eliminar el coste y sus importes asignados? "
            "Las líneas de PDF asignadas a esta fila volverán a quedar pendientes.", parent=self,
        ):
            for invoice in self.data.invoices:
                invoice.amounts.pop(row.id, None)
                for line in invoice.lines:
                    if line.row_id == row.id:
                        line.row_id = ""
            self.data.rows.remove(row)
            self.changed()
            self._refresh()

    def edit_cell(self, event):
        """Edita un importe monetario o abre los datos de la fila seleccionada."""
        row_id = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not row_id or not column:
            return
        self.tree.selection_set(row_id)
        index = int(column[1:]) - 1
        if index < 6:
            self.edit_row()
            return
        invoice = self.data.invoices[index - 6]
        raw = simpledialog.askstring(
            "Importe facturado", invoice.name, parent=self,
            initialvalue=str(invoice.amounts.get(row_id, 0)),
        )
        if raw is None:
            return
        try:
            value = amount(raw)
        except ValueError as exc:
            messagebox.showerror("Facturación", str(exc), parent=self)
            return
        invoice.amounts[row_id] = value
        self.changed()
        self._refresh()

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
        detail.insert("1.0", f"{invoice.path}\n{invoice.notes}")
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
        options = [f"{index + 1}. {row.item} / {row.resource} — {row.description}"
                   for index, row in enumerate(self.data.rows)]
        selector = ttk.Combobox(dialog, values=options, state="readonly", width=100)
        selector.pack(fill="x", padx=8, pady=8)
        if options:
            selector.current(0)

        def refresh():
            """Actualiza el detalle de las líneas con sus asignaciones actuales."""
            for child in tree.get_children():
                tree.delete(child)
            names = {row.id: f"{row.item} / {row.resource}" for row in self.data.rows}
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
        if self._scanning:
            messagebox.showinfo("Facturación", "Espera a que termine la lectura antes de guardar.", parent=self)
            return
        self.changed()
        if self.on_save():
            self._set_dirty(False)
            self.status.config(text="Facturación guardada con el proyecto.")

    def close(self):
        """Cancela lecturas pendientes y conserva los datos ya incorporados."""
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
            if self._after is not None:
                self.after_cancel(self._after)
                self._after = None
