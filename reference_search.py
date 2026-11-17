"""Consulta independiente de los catalogos locales de NTP y UNE."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from app_paths import application_directory
from localization import localize_widget_tree, translate
from municipalities import municipality_search_key

if TYPE_CHECKING:
    from bc3_gui import BudgetApp


ReferenceKind = Literal["NTP", "UNE"]


@dataclass(frozen=True)
class ReferenceEntry:
    """Conserva los datos de consulta y enlaces sin reproducir el documento."""

    code: str
    title: str
    publication: str
    status: str
    committee: str
    url: str
    pdf_url: str = ""


def load_reference_entries(
    kind: ReferenceKind, path: Path | None = None,
) -> tuple[ReferenceEntry, ...]:
    """Lee el catalogo completo, conservando ediciones y fichas con igual designacion."""
    root = application_directory(__file__) / "tools"
    if kind == "NTP":
        source = path if path is not None else root / "safety_health" / "datos" / "ntps.csv"
        required = {"codigo", "titulo", "anio", "url_ntp", "url_pdf"}
        host, url_column = "www.insst.es", "url_ntp"
    elif kind == "UNE":
        source = path if path is not None else root / "pptp" / "docs" / "unes.csv"
        required = {"codigo", "titulo", "fecha", "estado", "comite_nombre", "url_norma"}
        host, url_column = "www.une.org", "url_norma"
    else:
        raise ValueError("El catálogo debe ser NTP o UNE.")
    entries = []
    try:
        with source.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if not required <= set(reader.fieldnames or []):
                raise ValueError(f"El catálogo {kind} no contiene las columnas requeridas.")
            for line, row in enumerate(reader, 2):
                if any(not isinstance(row.get(key), str) for key in required):
                    raise ValueError(f"Catálogo {kind}, fila {line}: datos incompletos.")
                code, title, url = (row[key].strip() for key in ("codigo", "titulo", url_column))
                pdf_url = row["url_pdf"].strip() if kind == "NTP" else ""
                if not code or not title or not url:
                    raise ValueError(f"Catálogo {kind}, fila {line}: faltan código, título o enlace.")
                for link in (url, pdf_url):
                    if link:
                        parsed = urlsplit(link)
                        if parsed.scheme != "https" or parsed.netloc != host:
                            raise ValueError(f"Catálogo {kind}, fila {line}: enlace oficial no válido.")
                if kind == "NTP" and not re.fullmatch(r"NTP \d+", code):
                    raise ValueError(f"Catálogo NTP, fila {line}: código no válido.")
                entries.append(ReferenceEntry(
                    code, title, row["anio" if kind == "NTP" else "fecha"].strip(),
                    row["estado"].strip() if kind == "UNE" else "",
                    row["comite_nombre"].strip() if kind == "UNE" else "", url, pdf_url,
                ))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"No se pudo leer el catálogo {kind}: {exc}") from exc
    if not entries:
        raise ValueError(f"El catálogo {kind} está vacío.")
    if kind == "NTP":
        entries.sort(key=lambda entry: int(entry.code.split()[1]))
    else:
        entries.sort(key=lambda entry: entry.code.casefold())
    return tuple(entries)


class ReferenceSearchWindow(tk.Toplevel):
    """Busca por codigo, titulo y metadatos sin cambiar el proyecto abierto."""

    def __init__(
        self, app: BudgetApp, kind: ReferenceKind, entries: tuple[ReferenceEntry, ...],
    ) -> None:
        """Crea un buscador separado con filtrado inmediato y enlaces oficiales."""
        super().__init__(app.root)
        self.app, self.kind = app, kind
        self.title(translate("Buscador de {kind}").format(kind=kind))
        self.geometry("1100x680")
        self.minsize(720, 480)
        self.transient(app.root)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.entries = {str(index): entry for index, entry in enumerate(entries)}
        self.searchable = {
            key: municipality_search_key(" ".join((
                entry.code, entry.title, entry.publication, entry.status, entry.committee,
            )))
            for key, entry in self.entries.items()
        }
        search = ttk.Frame(self, padding=12)
        search.grid(row=0, column=0, sticky="ew")
        search.columnconfigure(1, weight=1)
        ttk.Label(search, text="Buscar por código o texto:").grid(row=0, column=0, padx=(0, 8))
        self.query = tk.StringVar(self)
        self.search_entry = ttk.Entry(search, textvariable=self.query)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.count = tk.StringVar(self)
        ttk.Label(self, textvariable=self.count).grid(row=1, column=0, sticky="w", padx=12)
        results = ttk.Frame(self, padding=(12, 4))
        results.grid(row=2, column=0, sticky="nsew")
        results.columnconfigure(0, weight=1)
        results.rowconfigure(0, weight=1)
        columns = [("code", "Código", 190), ("publication", "Año" if kind == "NTP" else "Fecha", 100)]
        if kind == "UNE":
            columns.append(("status", "Estado", 110))
        columns.append(("title", "Título", 650))
        self.tree = ttk.Treeview(
            results, columns=tuple(column for column, _label, _width in columns),
            show="headings", selectmode="browse",
        )
        for column, label, width in columns:
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=70, stretch=column == "title")
        self.tree.grid(row=0, column=0, sticky="nsew")
        for orient, command, row, column, sticky in (
            ("vertical", self.tree.yview, 0, 1, "ns"),
            ("horizontal", self.tree.xview, 1, 0, "ew"),
        ):
            scrollbar = ttk.Scrollbar(results, orient=orient, command=command)
            scrollbar.grid(row=row, column=column, sticky=sticky)
            self.tree.configure(**{
                "yscrollcommand" if orient == "vertical" else "xscrollcommand": scrollbar.set,
            })
        for key, entry in self.entries.items():
            values = [entry.code, entry.publication]
            if kind == "UNE":
                values.append(entry.status)
            self.tree.insert("", "end", iid=key, values=(*values, entry.title))
        details_frame = ttk.Frame(self)
        details_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=4)
        self.details = tk.Text(
            details_frame, height=6, wrap="word", state="disabled", padx=8, pady=8,
        )
        details_scroll = ttk.Scrollbar(details_frame, command=self.details.yview)
        self.details.configure(yscrollcommand=details_scroll.set)
        details_scroll.pack(side="right", fill="y")
        self.details.pack(fill="both", expand=True)
        actions = ttk.Frame(self, padding=12)
        actions.grid(row=4, column=0, sticky="ew")
        self.open_button = ttk.Button(
            actions, text="Ver ficha oficial", command=self.open_selected, state="disabled",
        )
        self.open_button.pack(side="left")
        ttk.Button(actions, text="Cerrar", command=self.destroy).pack(side="right")
        ttk.Label(
            self, text="Catálogo local. Comprueba en la fuente oficial la vigencia y aplicabilidad.",
            wraplength=1000,
        ).grid(row=5, column=0, sticky="w", padx=12, pady=(0, 12))
        self.tree.bind("<<TreeviewSelect>>", self._show_selected)
        self.tree.bind("<Double-1>", self._open_from_tree)
        self.tree.bind("<Return>", self._open_from_tree)
        self.query.trace_add("write", self._filter)
        self._filter()
        localize_widget_tree(self)
        self.search_entry.focus_set()

    def _selected(self) -> ReferenceEntry | None:
        """Devuelve solo una referencia que siga visible tras aplicar el filtro."""
        selected = self.tree.selection()
        if selected and self.tree.parent(selected[0]) == "" and selected[0] in self.tree.get_children():
            return self.entries[selected[0]]
        return None

    def _filter(self, *_args) -> None:
        """Filtra al escribir por todas las palabras, ignorando tildes y mayusculas."""
        words = municipality_search_key(self.query.get()).split()
        visible = [
            key for key, text in self.searchable.items() if all(word in text for word in words)
        ]
        self.tree.set_children("", *visible)
        selected = self.tree.selection()
        if selected and selected[0] not in visible:
            self.tree.selection_remove(*selected)
        self.count.set(translate("{count} de {total} referencias").format(
            count=len(visible), total=len(self.entries),
        ))
        self._show_selected()

    def _show_selected(self, _event=None) -> None:
        """Muestra el titulo completo y habilita solo los enlaces disponibles."""
        entry = self._selected()
        text = ""
        if entry is not None:
            text = f"{entry.code}\n{entry.title}"
            for label, value in (
                ("Año" if self.kind == "NTP" else "Fecha", entry.publication),
                ("Estado", entry.status), ("Comité", entry.committee),
            ):
                if value:
                    text += f"\n{translate(label)}: {value}"
            text += f"\n{entry.url}"
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")
        self.open_button.configure(state="normal" if entry else "disabled")

    def _open_from_tree(self, _event=None) -> str:
        """Abre la ficha con doble clic o Intro sobre los resultados."""
        if (
            _event is not None and _event.type == tk.EventType.ButtonPress
            and not self.tree.identify_row(_event.y)
        ):
            return "break"
        self.open_selected()
        return "break"

    def open_selected(self) -> None:
        """Delega la apertura en el navegador en el manejador habitual de la aplicacion."""
        entry = self._selected()
        if entry is None:
            return
        self.app._open_web_page(
            entry.url, translate("Buscador de {kind}").format(kind=self.kind),
            translate("Referencia abierta en el navegador."),
        )
