"""Asistente de revision y generacion del PPTP del proyecto."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from localization import filedialog, localize_widget_tree, messagebox
from .library import ZipLibrary
from .document import (
    DEFAULT_TOC_DEPTH, PENDING_DURATION, VARIABLES, Selection, compose, directory_codes, initial_variables,
    load_catalog, load_regulations, normalized_code, price_revision_fields, propose, restore_state, write_document,
    duration_price_revision, regulation_matches, une_designation,
)

if TYPE_CHECKING:
    from bc3_gui import BudgetApp
    from tools._library_editor import LibraryEditor

TOOL_LABEL = "PPTP (BCCA · Junta de Andalucía)"
TOOL_ORDER = 35
LIBRARY_PATH = Path(__file__).resolve().parent / "BIBLIOTECA.zip"
DOCS_ROOT = Path(__file__).resolve().parent / "docs"


def restore_project_state(data: dict) -> dict:
    """Recupera la revision aceptada sin consultar ni modificar el presupuesto."""
    return restore_state(data)


def open_tool(app: BudgetApp) -> None:
    """Abre una unica instancia del asistente vinculada al proyecto actual."""
    if not app.budget_items:
        messagebox.showinfo(TOOL_LABEL, "Carga o crea un presupuesto antes de preparar el PPTP.",
                            parent=app.root)
        return
    for window in app._project_windows:
        if isinstance(window, PptpWindow) and window.winfo_exists():
            window.lift()
            return
    window = PptpWindow(app.root, app)
    app._project_windows.append(window)


class PptpWindow(tk.Toplevel):
    """Tres pasos de datos, coincidencias automaticas y asignacion manual."""

    def __init__(self, master: tk.Misc, app: BudgetApp):
        """Lee catalogo y decisiones guardadas antes de construir el asistente."""
        self.library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        self.catalog = load_catalog(self.library)
        self.sheets = {sheet.path: sheet for sheet in self.catalog}
        saved = restore_state(app.pptp_state)
        self.automatic, self.unmatched = propose(
            app.budget_items, app.chapter_titles, self.catalog, saved,
        )
        values, self.missing_plan = initial_variables(app)
        super().__init__(master)
        self.app = app
        self.budget_snapshot = deepcopy(app.budget_items)
        self.transient(master)
        self.geometry("1120x720")
        self.minsize(860, 560)
        self.variables = {key: tk.StringVar(self, value=value) for key, value in values.items()}
        self.price_revision = tk.BooleanVar(self, value=saved.get("aplica_revision_precios", False))
        self.price_start = (
            app.planning_state.get("params", {}).get("start", "")
            if app.planning_state else saved.get("revision_inicio_planificacion", "")
        )
        self.toc_depth = tk.StringVar(self, value=str(saved.get("toc_depth", DEFAULT_TOC_DEPTH)))
        self.regulations = []
        self.regulation_choices = dict(saved.get("regulations", {}))
        self.reviewed_unes = set(saved.get("une_revisadas", []))
        self.step = 0
        self.editor: ttk.Combobox | None = None
        self.library_editor: LibraryEditor | None = None
        self.content = ttk.Frame(self, padding=12)
        self.content.pack(fill="both", expand=True)
        self.buttons = ttk.Frame(self, padding=12)
        self.buttons.pack(fill="x")
        ttk.Button(self.buttons, text="Cancelar", command=self.destroy).pack(side="right")
        self.next_button = ttk.Button(self.buttons, text="Aceptar", command=self.accept,
                                      style="Accent.TButton")
        self.next_button.pack(side="right", padx=8)
        self.back_button = ttk.Button(self.buttons, text="Atrás", command=self.back)
        self.back_button.pack(side="left")
        self.show_step()
        self.variables["plazo_ejecucion"].trace_add("write", self.duration_changed)

    def show_step(self) -> None:
        """Reconstruye el paso conservando los valores editados en memoria."""
        self.editor = None
        for child in self.content.winfo_children():
            child.destroy()
        self.title(f"PPTP — Paso {self.step + 1} de 3")
        self.back_button.configure(state="normal" if self.step else "disabled")
        self.next_button.configure(text="Aceptar y generar" if self.step == 2 else "Aceptar")
        if self.step == 0:
            self.show_variables()
        elif self.step == 1:
            ttk.Label(
                self.content, text="Fichas encontradas por código. Desmarca las que no procedan.",
            ).pack(anchor="w", pady=6)
            self.tree = self.selection_table(self.content)
            for index, row in enumerate(self.automatic):
                sheet = self.sheets[row.path]
                self.tree.insert("", "end", iid=str(index), values=(
                    f"{row.code} — {row.summary}", f"{sheet.code} — {sheet.title}",
                    "☑" if row.selected else "☐",
                ))
            if not self.automatic:
                ttk.Label(self.content, text="No hay coincidencias automáticas; asígnalas en el siguiente paso.").pack()
            self.tree.bind("<ButtonRelease-1>", self.toggle)
            self.tree.bind("<space>", self.toggle)
            self.add_sheet_detail(self.content)
        else:
            self.update_regulations()
            notebook = ttk.Notebook(self.content)
            notebook.pack(fill="both", expand=True)
            manual = ttk.Frame(notebook, padding=6)
            legal = ttk.Frame(notebook, padding=6)
            notebook.add(manual, text="Partidas sin coincidencia")
            notebook.add(legal, text="Disposiciones técnicas legales")
            ttk.Label(manual, text="Doble clic en la segunda columna para elegir una ficha del índice.").pack(anchor="w")
            self.tree = self.selection_table(manual)
            self.refresh_manual()
            self.tree.bind("<Double-1>", self.edit_sheet)
            self.tree.bind("<Return>", self.edit_sheet)
            self.tree.bind("<ButtonRelease-1>", self.toggle)
            self.tree.bind("<space>", self.toggle)
            self.add_sheet_detail(manual)
            ttk.Button(manual, text="Añadir otra ficha a la partida", command=self.add_sheet).pack(anchor="w", pady=6)
            ttk.Label(
                legal, wraplength=1000,
                text="Confirma ámbito, vigencia y condiciones de cada referencia. "
                     "Se preselecciona la UNE más reciente por familia, sin distinguir partes; "
                     "las decisiones UNE ajustadas manualmente se conservan. Las otras referencias «revision» "
                     "no se marcan automáticamente. Desmarca las que no procedan.",
            ).pack(fill="x", pady=6)
            self.legal_tree = self.selection_table(
                legal, labels=("Referencia / estado", "Partidas relacionadas", "Fichas relacionadas", "Incluir"),
            )
            self.refresh_regulations()
            self.legal_tree.bind("<ButtonRelease-1>", self.toggle_regulation)
            self.legal_tree.bind("<space>", self.toggle_regulation)
            self.legal_tree.bind("<<TreeviewSelect>>", self.show_regulation)
            self.legal_detail = tk.Text(legal, height=6, wrap="word", state="disabled")
            self.legal_detail.pack(fill="x", pady=6)
        localize_widget_tree(self)

    def update_regulations(self) -> None:
        """Recalcula propuestas legales al cambiar las fichas manuales o sus checks."""
        codes = set()
        relations: list[tuple[str, str, str]] = []
        for row in [*self.automatic, *self.unmatched]:
            if row.selected and row.path in self.sheets:
                sheet = self.sheets[row.path]
                levels = directory_codes(sheet)
                code = normalized_code(row.code)
                code = code if row.automatic and code.startswith(levels[0]) else sheet.code
                codes.add(code)
                relations.append((
                    code, f"{row.code} — {row.summary}",
                    f"{sheet.code} — {sheet.title}",
                ))
        self.regulations = load_regulations(self.library, codes)
        self.regulation_relations: dict[str, list[tuple[str, str]]] = {}
        for ref in self.regulations:
            if une_designation(ref) and ref.id not in self.reviewed_unes:
                self.regulation_choices[ref.id] = ref.selected
            else:
                self.regulation_choices.setdefault(ref.id, ref.selected)
            self.regulation_relations[ref.id] = list(dict.fromkeys(
                (item, sheet) for code, item, sheet in relations
                if regulation_matches(ref, {code})
            ))

    def refresh_regulations(self) -> None:
        """Presenta cada norma una sola vez junto a las partidas y fichas vinculadas."""
        self.legal_tree.delete(*self.legal_tree.get_children())
        for index, ref in enumerate(self.regulations):
            relations = self.regulation_relations[ref.id]
            self.legal_tree.insert("", "end", iid=str(index), values=(
                f"{ref.id} [{ref.status}] — {ref.text[2:]}",
                "; ".join(dict.fromkeys(item for item, _ in relations)),
                "; ".join(dict.fromkeys(sheet for _, sheet in relations)),
                "☑" if self.regulation_choices[ref.id] else "☐",
            ))

    def add_sheet_detail(self, parent) -> None:
        """Permite leer ruta y advertencias de las fichas antes de aceptarlas."""
        self.sheet_detail = tk.Text(parent, height=4, wrap="word", state="disabled")
        self.sheet_detail.pack(fill="x", pady=6)
        self.tree.bind("<<TreeviewSelect>>", self.show_sheet)

    def show_sheet(self, event=None) -> None:
        """Presenta la ruta y el aviso tecnico de la fila seleccionada."""
        selection = self.tree.selection()
        if selection:
            rows = self.automatic if self.step == 1 else self.unmatched
            sheet = self.sheets.get(rows[int(selection[0])].path)
            self.sheet_detail.configure(state="normal")
            self.sheet_detail.delete("1.0", "end")
            if sheet:
                self.sheet_detail.insert("1.0", f"{sheet.path}\n{sheet.warning}")
            self.sheet_detail.configure(state="disabled")

    def show_variables(self) -> None:
        """Muestra variables editables y explica el origen del plazo de ejecucion."""
        ttk.Label(self.content, text="Revisa los datos del proyecto antes de continuar.",
                  style="Total.TLabel").pack(anchor="w", pady=8)
        form = ttk.Frame(self.content)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for index, (key, label) in enumerate((
            ("titulo_proyecto", "Título del proyecto"), ("entidad", "Entidad"),
            ("plazo_garantia", "Plazo de garantía (con unidad)"),
            ("plazo_ejecucion", "Plazo de ejecución (con unidad)"),
        )):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", pady=6, padx=(0, 12))
            ttk.Entry(form, textvariable=self.variables[key]).grid(row=index, column=1, sticky="ew")
        ttk.Label(form, text="Niveles del índice (1–6)").grid(
            row=4, column=0, sticky="w", pady=6, padx=(0, 12),
        )
        ttk.Combobox(
            form, textvariable=self.toc_depth, values=tuple(str(level) for level in range(1, 7)),
            state="readonly", width=5,
        ).grid(row=4, column=1, sticky="w")
        self.price_check = ttk.Checkbutton(
            form, text="Aplicar revisión de precios", variable=self.price_revision,
            command=self.update_price_revision,
        )
        self.price_check.grid(row=5, column=0, columnspan=2, sticky="w", pady=6)
        self.price_entries = {}
        for index, (key, label) in enumerate((
            ("JUSTIFICACION", "Justificación"),
            ("FORMULA_REVISION_PRECIOS", "Fórmula de revisión de precios"),
        ), start=6):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky="w", padx=(0, 12), pady=4)
            variable = self.variables[
                "JUSTIFICACION_APLICACION" if self.price_revision.get() else "JUSTIFICACION_NO_APLICACION"
            ] if key == "JUSTIFICACION" else self.variables[key]
            entry = ttk.Entry(form, textvariable=variable)
            entry.grid(row=index, column=1, sticky="ew")
            self.price_entries[key] = entry
        self.duration_changed()
        if self.missing_plan:
            ttk.Label(
                self.content, wraplength=1000,
                text="No se ha realizado la planificación. Realiza Herramientas → Planificación "
                     "para obtener el plazo de ejecución. Mientras tanto, debes rellenarlo manualmente; "
                     "no se asigna un plazo ficticio.",
            ).pack(fill="x", pady=12)
        ttk.Label(self.content, text="Resumen de las obras").pack(anchor="w")
        self.summary = tk.Text(self.content, wrap="word", height=12)
        self.summary.insert("1.0", self.variables["bullet_capitulos_obra"].get())
        self.summary.pack(fill="both", expand=True, pady=6)
        ttk.Label(
            self.content, text="Los cambios aceptados de título, entidad y garantía se conservan en el proyecto.",
        ).pack(anchor="w")
        ttk.Button(
            self.content, text="Editar biblioteca de fichas...",
            command=self.open_library_editor,
        ).pack(anchor="e", pady=(8, 0))

    def open_library_editor(self) -> None:
        """Abre el editor de fichas PPTP sin cerrar el asistente del proyecto."""
        from tools._library_editor import LibraryEditor

        used_members = {
            row.path for row in [*self.automatic, *self.unmatched]
            if row.selected and row.path in self.sheets
        }
        if self.library_editor is not None and self.library_editor.winfo_exists():
            self.library_editor.highlight_used(used_members)
            self.library_editor.lift()
            return
        self.library_editor = LibraryEditor(
            self, LIBRARY_PATH, "Biblioteca de fichas PPTP", on_saved=self.reload_library,
            used_members=used_members, track_changes=True,
        )

    def update_price_revision(self) -> None:
        """Vincula la justificación visible a la alternativa elegida conservando ambos textos."""
        applies = self.price_revision.get()
        self.price_entries["JUSTIFICACION"].configure(textvariable=self.variables[
            "JUSTIFICACION_APLICACION" if applies else "JUSTIFICACION_NO_APLICACION"
        ])
        self.price_entries["FORMULA_REVISION_PRECIOS"].configure(
            state="normal" if applies else "disabled",
        )

    def duration_changed(self, *_args) -> None:
        """Actualiza el límite temporal y la justificación conservando las aportaciones manuales."""
        values = {key: variable.get() for key, variable in self.variables.items()}
        applies, reason = duration_price_revision(values, self.price_revision.get(), self.price_start)
        self.price_revision.set(applies)
        self.variables["JUSTIFICACION_NO_APLICACION"].set(reason)
        if self.step == 0:
            self.price_check.configure(state="normal")
            self.update_price_revision()

    def reload_library(self) -> None:
        """Actualiza el catálogo abierto tras modificar una ficha del ZIP."""
        self.library = ZipLibrary(LIBRARY_PATH, DOCS_ROOT)
        self.catalog = load_catalog(self.library)
        self.sheets = {sheet.path: sheet for sheet in self.catalog}
        for row in [*self.automatic, *self.unmatched]:
            row.path = self.library.resolve_sheet(row.path)
        self.automatic, self.unmatched = propose(
            self.budget_snapshot, self.app.chapter_titles, self.catalog,
            {"selections": [asdict(row) for row in [*self.automatic, *self.unmatched]]},
        )
        if self.step:
            self.show_step()
        if self.library_editor is not None and self.library_editor.winfo_exists():
            self.library_editor.highlight_used({
                row.path for row in [*self.automatic, *self.unmatched]
                if row.selected and row.path in self.sheets
            })

    def selection_table(
        self, parent: tk.Misc,
        labels: tuple[str, ...] = ("Partida del presupuesto", "Código y nombre de ficha", "Incluir"),
    ) -> ttk.Treeview:
        """Construye una tabla desplazable con la inclusión en la última columna."""
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=6)
        columns = ("item", "sheet", "related", "selected") if len(labels) == 4 else ("item", "sheet", "selected")
        widths = (380, 300, 380, 70) if len(labels) == 4 else (380, 520, 70)
        tree = ttk.Treeview(frame, columns=columns,
                            show="headings", selectmode="browse")
        for column, label, width in zip(columns, labels, widths):
            tree.heading(column, text=label)
            tree.column(column, width=width, minwidth=60, stretch=column != "selected")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    def refresh_manual(self) -> None:
        """Actualiza las asignaciones manuales sin perder decisiones de inclusion."""
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(self.unmatched):
            sheet = self.sheets.get(row.path)
            self.tree.insert("", "end", iid=str(index), values=(
                f"{row.code} — {row.summary}",
                f"{sheet.code} — {sheet.title}" if sheet else "Seleccionar ficha…",
                "☑" if row.selected else "☐",
            ))

    def toggle(self, event) -> str:
        """Alterna el check por raton o teclado sin editar el resto de columnas."""
        keyboard = event.keysym == "space"
        iid = (self.tree.selection() or ("",))[0] if keyboard else self.tree.identify_row(event.y)
        if iid and (keyboard or self.tree.identify_column(event.x) == "#3"):
            rows = self.automatic if self.step == 1 else self.unmatched
            row = rows[int(iid)]
            row.selected = not row.selected
            self.tree.set(iid, "selected", "☑" if row.selected else "☐")
            if self.step == 2:
                self.show_step()
        return "break" if keyboard else ""

    def edit_sheet(self, event) -> None:
        """Presenta todas las opciones del indice en un desplegable de la celda."""
        keyboard = event.keysym == "Return"
        iid = (self.tree.selection() or ("",))[0] if keyboard else self.tree.identify_row(event.y)
        if not iid or (not keyboard and self.tree.identify_column(event.x) != "#2"):
            return
        if self.editor is not None:
            self.editor.destroy()
        bounds = self.tree.bbox(iid, "sheet")
        if not bounds:
            return
        options = [f"{sheet.code} — {sheet.title}" for sheet in self.catalog]
        editor = ttk.Combobox(self.tree, values=options, state="readonly")
        self.editor = editor
        current = self.unmatched[int(iid)].path
        if current in self.sheets:
            editor.current(next(index for index, sheet in enumerate(self.catalog) if sheet.path == current))
        editor.place(x=bounds[0], y=bounds[1], width=bounds[2], height=bounds[3])

        def commit(event=None) -> None:
            """Guarda la ficha por su ruta estable y retira el editor de celda."""
            if editor.current() >= 0:
                self.unmatched[int(iid)].path = self.catalog[editor.current()].path
            editor.destroy()
            self.editor = None
            self.show_step()

        editor.bind("<<ComboboxSelected>>", commit)
        editor.bind("<Escape>", lambda event: editor.destroy())
        editor.focus_set()

    def add_sheet(self) -> None:
        """Permite asociar varias fichas a una partida sin coincidencia automatica."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(TOOL_LABEL, "Selecciona primero una partida de la tabla.", parent=self)
            return
        row = self.unmatched[int(selected[0])]
        self.unmatched.append(Selection(row.item, row.code, row.chapter, row.summary, "",
                                        automatic=False))
        self.refresh_manual()
        self.tree.selection_set(str(len(self.unmatched) - 1))
        self.tree.see(str(len(self.unmatched) - 1))

    def toggle_regulation(self, event) -> str:
        """Exige una decision expresa incluso para referencias pendientes de revision."""
        keyboard = event.keysym == "space"
        iid = ((self.legal_tree.selection() or ("",))[0] if keyboard
               else self.legal_tree.identify_row(event.y))
        if iid and (keyboard or self.legal_tree.identify_column(event.x) == "#4"):
            ref = self.regulations[int(iid)]
            value = not self.regulation_choices.get(ref.id, False)
            self.regulation_choices[ref.id] = value
            if une_designation(ref):
                self.reviewed_unes.add(ref.id)
            self.legal_tree.set(iid, "selected", "☑" if value else "☐")
            self.show_regulation()
        return "break" if keyboard else ""

    def show_regulation(self, event=None) -> None:
        """Muestra completa la condicion y referencia seleccionada en la tabla."""
        selected = self.legal_tree.selection()
        if selected:
            ref = self.regulations[int(selected[0])]
            self.legal_detail.configure(state="normal")
            self.legal_detail.delete("1.0", "end")
            relations = "\n".join(
                f"• {item}\n  Ficha: {sheet}"
                for item, sheet in self.regulation_relations[ref.id]
            )
            included = self.regulation_choices[ref.id]
            status = (
                "Incluida en el pliego. Las UNE vinculadas aparecerán también en la tabla del apartado 9."
                if included else "No incluida. Marca «Incluir» para incorporarla al pliego y, si es UNE, a la tabla del apartado 9."
            )
            self.legal_detail.insert(
                "1.0", f"{ref.text}\n\n{ref.condition}\n\n{status}\n\n"
                f"Partidas y fichas relacionadas:\n{relations}",
            )
            self.legal_detail.configure(state="disabled")

    def back(self) -> None:
        """Retrocede sin aplicar cambios al proyecto ni perder la revision."""
        if self.step:
            self.step -= 1
            self.show_step()

    def accept(self) -> None:
        """Valida cada paso y persiste la revision antes de emitir el documento."""
        try:
            if self.step == 0:
                self.duration_changed()
                self.selected_toc_depth()
                self.variables["bullet_capitulos_obra"].set(self.summary.get("1.0", "end-1c").strip())
                for key in (*VARIABLES, *price_revision_fields(self.price_revision.get())):
                    variable = self.variables[key]
                    if not variable.get().strip() or variable.get().strip() == PENDING_DURATION:
                        raise ValueError(f"Rellena el campo {key} antes de continuar.")
            if self.step < 2:
                self.step += 1
                self.show_step()
                return
            self.generate()
        except (OSError, UnicodeError, ValueError, ImportError) as exc:
            messagebox.showerror(TOOL_LABEL, str(exc), parent=self)

    def selected_toc_depth(self) -> int:
        """Lee y valida los niveles del indice seleccionados en el primer paso."""
        value = self.toc_depth.get()
        if value not in tuple(str(level) for level in range(1, 7)):
            raise ValueError("Los niveles del índice deben ser un entero entre 1 y 6.")
        return int(value)

    def generate(self) -> None:
        """Guarda el JSON y escribe el documento atomicamente sin destruir uno previo."""
        if self.app.budget_items != self.budget_snapshot:
            raise ValueError("El presupuesto ha cambiado. Cierra y vuelve a abrir PPTP para revisar las nuevas partidas.")
        self.update_regulations()
        self.duration_changed()
        for row in [*self.automatic, *self.unmatched]:
            if row.selected and not row.path:
                raise ValueError(f"Asigna una ficha a {row.code} o desmarca su incorporación.")
            if row.path and row.path not in self.sheets:
                raise ValueError(f"La ficha guardada de {row.code} ya no figura en el índice: {row.path}")
        state = restore_state({
            "variables": {key: variable.get().strip() for key, variable in self.variables.items()},
            "selections": [asdict(row) for row in [*self.automatic, *self.unmatched]],
            "regulations": self.regulation_choices,
            "toc_depth": self.selected_toc_depth(),
            "aplica_revision_precios": self.price_revision.get(),
            "revision_inicio_planificacion": self.price_start,
            "une_revisadas": sorted(self.reviewed_unes),
        })
        budget_chapter_titles = {
            row["chapter"]: self.app._chapter_title(row["chapter"])
            for row in state["selections"] if row["selected"]
        }
        source = compose(
            self.library, state, self.regulations,
            budget_chapter_titles=budget_chapter_titles,
        )
        extension = "." + self.app.report_settings.output_format
        target = filedialog.asksaveasfilename(
            parent=self, title="Generar PPTP", initialfile="PPTP" + extension,
            defaultextension=extension, filetypes=[("Documento " + extension[1:].upper(), "*" + extension)],
        )
        if not target:
            return
        self.app.pptp_state = state
        self.app.project_title = state["variables"]["titulo_proyecto"]
        self.app.report_settings.organization = state["variables"]["entidad"]
        self.app.report_settings.guarantee_period = state["variables"]["plazo_garantia"]
        self.app.project_panel_title.set(f"Proyecto: {self.app._project_title()}")
        self.app._mark_dirty()
        if not self.app.save_project():
            return
        path = Path(target).with_suffix(extension)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=extension, delete=False) as handle:
                temporary = Path(handle.name)
            write_document(
                source, temporary, state["variables"], self.app.report_settings,
                toc_depth=state["toc_depth"],
            )
            temporary.replace(path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self.app._set_status(f"PPTP guardado en {path}")
        messagebox.showinfo(TOOL_LABEL, f"Selección guardada y documento generado:\n{path}", parent=self)
        self.destroy()
