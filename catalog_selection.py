"""Selector filtrable de catalogos con varias filas y confirmacion explicita."""
from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from localization import messagebox
from municipalities import municipality_search_key


@dataclass(frozen=True)
class CatalogOption:
    """Define una opcion visible y sus textos de busqueda y confirmacion."""

    code: str
    label: str
    values: tuple[str, ...]
    search_text: str
    aliases: tuple[str, ...] = ()


class CatalogSelection(ttk.Frame):
    """Comparte la seleccion multiple de municipios y codigos CPV sin guardar al editar."""

    def __init__(
        self, parent: ttk.Notebook, *, options: tuple[CatalogOption, ...],
        selected_codes: list[str], columns: tuple[tuple[str, str, int], ...],
        title: str, add_label: str, help_text: str, invalid_message: str,
    ) -> None:
        """Crea el desplegable editable, la tabla seleccionada y los controles comunes."""
        super().__init__(parent, padding=8)
        self.columnconfigure(0, weight=1)
        self.invalid_message = invalid_message
        self._selected = dict.fromkeys(selected_codes)
        by_code = {option.code: option for option in options}
        self._searchable = [
            (
                option, municipality_search_key(option.search_text),
                {municipality_search_key(alias) for alias in (option.label, *option.aliases)},
            )
            for option in options
        ]
        ttk.Label(self, text=title).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8),
        )
        self.query = tk.StringVar()
        self.selector = ttk.Combobox(self, textvariable=self.query, width=60, height=16)
        self.selector.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.tree = ttk.Treeview(
            self, columns=tuple(column for column, _label, _width in columns),
            show="headings", selectmode="extended", height=7,
        )
        for column, label, width in columns:
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=100)
        self.tree.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=2, column=2, sticky="ns", pady=(8, 0))
        self.tree.configure(yscrollcommand=scrollbar.set)
        for code in selected_codes:
            self.tree.insert("", "end", iid=code, values=by_code[code].values)
        self.query.trace_add("write", self._filter)
        self._filter()
        self.selector.bind("<Return>", self._add_from_keyboard)
        ttk.Button(self, text=add_label, command=self.add).grid(row=1, column=1, sticky="e")
        ttk.Button(self, text="Quitar seleccionados", command=self._remove).grid(
            row=3, column=1, sticky="e", pady=(8, 0),
        )
        ttk.Label(self, text=help_text, wraplength=600).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=12,
        )

    @property
    def codes(self) -> list[str]:
        """Devuelve una copia de la seleccion en el orden de incorporacion."""
        return list(self._selected)

    def _filter(self, *_args) -> None:
        """Actualiza las opciones al escribir sin cambiar el texto ni el foco."""
        query = municipality_search_key(self.query.get())
        self.selector.configure(values=tuple(
            option.label for option, text, aliases in self._searchable
            if query in text or query in aliases
        ))

    def add(self) -> bool:
        """Confirma solo una opcion inequivoca y evita anadir filas repetidas."""
        query = municipality_search_key(self.query.get())
        matches = [
            option for option, _text, aliases in self._searchable if query in aliases
        ]
        if len(matches) != 1:
            messagebox.showerror(
                "Configuración", self.invalid_message, parent=self.winfo_toplevel(),
            )
            self.selector.focus_set()
            return False
        option = matches[0]
        if option.code not in self._selected:
            self._selected[option.code] = None
            self.tree.insert("", "end", iid=option.code, values=option.values)
        self.tree.selection_set(option.code)
        self.tree.see(option.code)
        self.query.set("")
        self.selector.focus_set()
        return True

    def _add_from_keyboard(self, _event) -> str:
        """Permite confirmar con Intro sin activar otros botones del dialogo."""
        self.add()
        return "break"

    def _remove(self) -> None:
        """Quita las filas seleccionadas sin aplicar cambios al proyecto."""
        for code in self.tree.selection():
            del self._selected[code]
            self.tree.delete(code)

    def confirm_pending(self) -> bool:
        """Incluye una eleccion escrita antes de guardar o notifica que no es valida."""
        return not self.query.get().strip() or self.add()
