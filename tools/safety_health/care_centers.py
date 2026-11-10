"""Edición y formato de centros asistenciales procedentes del registro SICESS."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TypedDict

from localization import localize_widget_tree, messagebox


FIELDS = (
    ("nombre", "Nombre"), ("ambito", "Ámbito"), ("dependencia", "Dep. funcional"),
    ("sspa", "Sistema Sanitario Público Andaluz"), ("zona", "Zona Básica de Salud"),
    ("hospitalario", "Es C. Hospitalario"), ("via", "Vía"), ("domicilio", "Domicilio"),
    ("numero", "Nº"), ("escalera", "Escalera"), ("piso", "Piso"), ("puerta", "Puerta"),
    ("cp", "CP"), ("provincia", "Provincia"), ("municipio", "Municipio"),
    ("localidad", "Localidad"), ("telefono", "Teléfono"),
)


class CareCentersState(TypedDict):
    """Estado persistente del texto libre y de las fichas SICESS."""

    centros_asistenciales: str
    centros_asistenciales_modo: str
    centros_asistenciales_texto: str
    centros_asistenciales_datos: list[dict[str, str]]


def restore_centers(data: dict) -> list[dict[str, str]]:
    """Valida las filas SICESS y el modo sin alterar proyectos antiguos."""
    if data.get("centros_asistenciales_modo", "texto") not in ("texto", "tabla"):
        raise ValueError("El modo de centros asistenciales no es válido.")
    if not isinstance(data.get("centros_asistenciales_texto", ""), str):
        raise ValueError("El texto de centros asistenciales no es válido.")
    rows = data.get("centros_asistenciales_datos", [])
    if not isinstance(rows, list):
        raise ValueError("La tabla de centros asistenciales no es válida.")
    result = []
    for row in rows:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key, ""), str) for key, _ in FIELDS
        ):
            raise ValueError("Los datos SICESS de los centros deben ser texto.")
        result.append({key: row.get(key, "") for key, _ in FIELDS})
    return result


def format_centers(rows: list[dict[str, str]]) -> str:
    """Resume cada centro con nombre, dirección postal y teléfono."""
    blocks = []
    for row in rows:
        values = {key: row.get(key, "").strip() for key, _ in FIELDS}
        street = " ".join(filter(None, (values["via"], values["domicilio"])))
        address = ", ".join(filter(None, (
            street, values["numero"],
            f"Esc. {values['escalera']}" if values["escalera"] else "",
            f"Piso {values['piso']}" if values["piso"] else "",
            f"Pta. {values['puerta']}" if values["puerta"] else "",
            values["cp"], values["localidad"] or values["municipio"], values["provincia"],
        )))
        lines = [values["nombre"].upper(), address]
        if values["telefono"]:
            lines.append(f"Teléfono: {values['telefono']}")
        block = "\n".join(line for line in lines if line)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


class CareCentersEditor(ttk.Frame):
    """Alterna texto libre y fichas SICESS sin perder ninguna de las dos entradas."""

    def __init__(self, master: tk.Misc, state: dict):
        """Construye los dos modos y recupera las filas guardadas."""
        super().__init__(master)
        self.rows = restore_centers(state)
        self.current: int | None = None
        self.tabs = ttk.Notebook(self, height=270)
        self.tabs.pack(fill="x")
        self.text_page = ttk.Frame(self.tabs)
        self.form_page = ttk.Frame(self.tabs, padding=4)
        self.tabs.add(self.text_page, text="Texto libre")
        self.tabs.add(self.form_page, text="Tabla SICESS")
        self.text = tk.Text(self.text_page, height=8, wrap="word", undo=True)
        scroll = ttk.Scrollbar(self.text_page, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", state.get(
            "centros_asistenciales_texto", state.get("centros_asistenciales", ""),
        ))
        sidebar = ttk.Frame(self.form_page)
        sidebar.pack(side="left", fill="y", padx=(0, 8))
        buttons = ttk.Frame(sidebar)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="+", width=3, command=self.add_center).pack(side="left")
        ttk.Button(buttons, text="Eliminar", command=self.remove_center).pack(side="left")
        self.list = ttk.Treeview(sidebar, show="tree", height=7, selectmode="browse")
        self.list.column("#0", width=155)
        self.list.pack(side="left", fill="y")
        list_scroll = ttk.Scrollbar(sidebar, command=self.list.yview)
        self.list.configure(yscrollcommand=list_scroll.set)
        list_scroll.pack(side="right", fill="y")
        self.list.bind("<<TreeviewSelect>>", self.select_center)
        form = ttk.Frame(self.form_page)
        form.pack(fill="both", expand=True)
        self.variables: dict[str, tk.StringVar] = {}
        for index, (key, label) in enumerate(FIELDS):
            row, pair = divmod(index, 3)
            ttk.Label(form, text=label).grid(row=row * 2, column=pair, sticky="w")
            variable = tk.StringVar(self)
            self.variables[key] = variable
            ttk.Entry(form, textvariable=variable, width=20).grid(
                row=row * 2 + 1, column=pair, sticky="ew", padx=(0, 8),
            )
            form.columnconfigure(pair, weight=1)
        ttk.Button(
            self, text="Pasar tabla a texto", command=self.to_text,
        ).pack(anchor="e", pady=(4, 0))
        if not self.rows:
            self.rows.append({key: "" for key, _ in FIELDS})
        for index, row in enumerate(self.rows):
            self.list.insert("", "end", iid=str(index), text=row["nombre"] or "—")
        self.list.selection_set("0")
        self.select_center()
        if state.get("centros_asistenciales_modo") == "tabla":
            self.tabs.select(self.form_page)
        localize_widget_tree(self)

    def commit_center(self) -> None:
        """Recoge los campos del centro actual antes de cambiar de fila o guardar."""
        if self.current is not None:
            self.rows[self.current] = {key: value.get() for key, value in self.variables.items()}
            self.list.item(str(self.current), text=self.rows[self.current]["nombre"] or "—")

    def select_center(self, event=None) -> None:
        """Muestra el centro seleccionado sin descartar los cambios del anterior."""
        selection = self.list.selection()
        if not selection:
            return
        self.commit_center()
        self.current = int(selection[0])
        for key, variable in self.variables.items():
            variable.set(self.rows[self.current][key])

    def add_center(self) -> None:
        """Añade otra fila SICESS y la selecciona para rellenarla."""
        self.commit_center()
        index = len(self.rows)
        self.rows.append({key: "" for key, _ in FIELDS})
        self.list.insert("", "end", iid=str(index), text="—")
        self.list.selection_set(str(index))
        self.list.see(str(index))
        self.select_center()
        self.tabs.select(self.form_page)

    def remove_center(self) -> None:
        """Elimina la ficha seleccionada previa confirmación."""
        if self.current is None:
            return
        if not messagebox.askyesno(
            "Centros Asistenciales", "¿Eliminar el centro seleccionado?", parent=self,
        ):
            return
        del self.rows[self.current]
        self.current = None
        self.list.delete(*self.list.get_children())
        if not self.rows:
            self.rows.append({key: "" for key, _ in FIELDS})
        for index, row in enumerate(self.rows):
            self.list.insert("", "end", iid=str(index), text=row["nombre"] or "—")
        self.list.selection_set("0")
        self.select_center()

    def to_text(self) -> None:
        """Convierte las fichas al texto final sin sobrescribir texto libre sin permiso."""
        self.commit_center()
        generated = format_centers(self.rows)
        previous = self.text.get("1.0", "end-1c").strip()
        if previous and previous != generated and not messagebox.askyesno(
            "Centros Asistenciales",
            "¿Sustituir el texto libre por el texto generado desde la tabla?", parent=self,
        ):
            return
        self.text.delete("1.0", "end")
        self.text.insert("1.0", generated)
        self.tabs.select(self.text_page)

    def collect_state(self) -> CareCentersState:
        """Devuelve ambas entradas y el texto final correspondiente al modo activo."""
        self.commit_center()
        manual = self.text.get("1.0", "end-1c").strip()
        mode = "tabla" if self.tabs.select() == str(self.form_page) else "texto"
        return {
            "centros_asistenciales": format_centers(self.rows) if mode == "tabla" else manual,
            "centros_asistenciales_modo": mode,
            "centros_asistenciales_texto": manual,
            "centros_asistenciales_datos": [dict(row) for row in self.rows],
        }
