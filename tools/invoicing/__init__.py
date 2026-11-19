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
from typing import TYPE_CHECKING, Callable
import unicodedata
from uuid import uuid4

from .._shared.budget_sources import RCD_BUDGET_SOURCE, TRANSPORT_PREFIX

TOOL_LABEL = "Facturación"
TOOL_ORDER = 50


def restore_project_state(data: dict) -> dict:
    """Valida las facturas y asignaciones guardadas en el proyecto."""
    return InvoiceState.from_dict(data).to_dict()


def open_tool_qt(app) -> None:
    """Abre una única ventana Qt de facturación."""
    from PySide6.QtWidgets import QMessageBox
    from .qt_window import InvoicingWindowQt

    if not app.budget_items:
        QMessageBox.information(
            app, TOOL_LABEL,
            "Carga o crea un presupuesto antes de gestionar facturas.",
        )
        return
    for window in app._project_windows:
        if isinstance(window, InvoicingWindowQt) and window.isVisible():
            window.raise_()
            window.activateWindow()
            return
    state = InvoiceState.from_dict(app.invoicing_state)
    window = InvoicingWindowQt(app, state)
    app._project_windows.append(window)
    window.show()

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


# Las clases visuales Tk de este módulo se retiraron; la GUI vigente está en PySide6.
