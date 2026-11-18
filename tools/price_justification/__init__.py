"""Asistente de documentos base y listados de justificación de precios."""

from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from localization import filedialog, messagebox
from .document import FIELDS, TITLE, restore_state, validate_state, write_document
from .templates import (
    DOCUMENTS, LIBRARY_PATH, document_blocks, general_variables, load_documents, template_variables,
)


TOOL_LABEL = TITLE
TOOL_ORDER = 0


def restore_project_state(data) -> dict:
    """Restaura los datos específicos del anejo sin duplicar la configuración general."""
    return restore_state(data)


def before_project_save(app) -> None:
    """Recoge y valida las ediciones pendientes antes de guardar el proyecto."""
    state = app.price_justification_state
    for window in app._project_windows:
        if isinstance(window, PriceJustificationWindow) and window.winfo_exists():
            state = window._collect()
    app.price_justification_state = validate_state(state)


def open_tool(app) -> None:
    """Abre una única ventana vinculada al proyecto presupuestario actual."""
    if not app.budget_items:
        messagebox.showinfo(TITLE, "Añade partidas al proyecto antes de justificar sus precios.", parent=app.root)
        return
    for window in app._project_windows:
        if isinstance(window, PriceJustificationWindow) and window.winfo_exists():
            window.lift()
            return
    window = PriceJustificationWindow(app)
    app._project_windows.append(window)


class PriceJustificationWindow(tk.Toplevel):
    """Muestra el texto base y sus fórmulas antes de generar el anejo."""

    def __init__(self, app):
        """Carga la biblioteca y construye la revisión de documentos y datos específicos."""
        state = FIELDS | restore_state(app.price_justification_state)
        documents = dict(load_documents(LIBRARY_PATH))
        super().__init__(app.root)
        self.app = app
        self._state = state
        self.documents = documents
        self.library_editor = None
        self.title(TITLE)
        self.geometry("1120x760")
        self.minsize(860, 620)
        self.transient(app.root)
        self._loading = True
        if not state["reference_date"]:
            state["reference_date"] = date.today().isoformat()
        self.variables: dict[str, tk.StringVar] = {}
        self.texts: dict[str, tk.Text] = {}
        self.contracts = {"Contratación pública": "public", "Contratación privada": "private"}
        self.methods = {
            "Precios de la fuente presupuestaria": "budget",
            "Valoración analítica documentada": "analytical",
        }
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)
        ttk.Label(container, text=TITLE, font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(
            container, wraplength=1030,
            text="Revisa los documentos base con su texto y fórmulas, como en el PPTP. "
                 "La biblioteca se puede editar; los listados se toman del presupuesto actual.",
        ).pack(anchor="w", pady=(5, 8))
        self.general_info = tk.StringVar(self)
        ttk.Label(container, textvariable=self.general_info, wraplength=1030).pack(anchor="w", pady=(0, 10))
        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)
        documents_page = ttk.Frame(notebook, padding=8)
        notebook.add(documents_page, text="Documentos base")
        panes = ttk.Panedwindow(documents_page, orient="horizontal")
        panes.pack(fill="both", expand=True)
        self.document_tree = ttk.Treeview(panes, show="tree", selectmode="browse")
        self.document_tree.column("#0", width=265, minwidth=200)
        panes.add(self.document_tree, weight=1)
        preview_frame = ttk.Frame(panes)
        panes.add(preview_frame, weight=3)
        self.preview = tk.Text(preview_frame, wrap="word", state="disabled", padx=12, pady=10)
        scrollbar = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)
        for name, title in DOCUMENTS:
            self.document_tree.insert("", "end", iid=name, text=title)
        self.document_tree.bind("<<TreeviewSelect>>", self._refresh_preview)
        self.document_tree.selection_set(DOCUMENTS[0][0])
        ttk.Button(
            documents_page, text="Editar biblioteca de documentos…", command=self.open_library_editor,
        ).pack(anchor="e", pady=(8, 0))
        specific = self._tab(notebook, "Datos específicos")
        self._entry(specific, "reference_date", "Fecha de referencia (AAAA-MM-DD)", state)
        self._choice(specific, "contract_type", "Régimen de contratación", state, self.contracts)
        self._entry(specific, "price_source", "Fuente de precios y fecha / edición", state)
        for key, label in (
            ("agreement", "Convenio colectivo y revisión salarial"),
            ("wage_reference", "Referencia salarial / SMI aplicable"),
            ("contributions", "Cotizaciones y contingencias profesionales"),
            ("calendar", "Calendario laboral aplicable"),
            ("annual_hours", "Jornada anual efectiva (horas, opcional)"),
        ):
            self._entry(specific, key, label, state)
        self._choice(specific, "machinery_method", "Criterio de maquinaria", state, self.methods)
        complements = self._tab(notebook, "Complementos opcionales")
        ttk.Label(
            complements, wraplength=970,
            text="Estos datos complementan el texto base. No sustituyen sus apartados y se "
                 "conservan las notas introducidas con la versión anterior de la herramienta.",
        ).pack(anchor="w", pady=(0, 10))
        for key, label in (
            ("legislation_notes", "Referencias normativas adicionales"),
            ("composition_notes", "Criterios particulares de composición"),
            ("indirect_staff", "Personal técnico y administrativo"),
            ("indirect_installations", "Instalaciones y medios comunes"),
            ("indirect_technical", "Trabajos técnicos, ensayos y controles"),
            ("indirect_other", "Otros costes y justificación del porcentaje"),
            ("labor_notes", "Criterios particulares de mano de obra"),
            ("materials_notes", "Procedencias y condiciones de suministro"),
            ("machinery_parameters", "Parámetros y fuentes de maquinaria"),
            ("machinery_consumption", "Operarios y consumos"),
            ("machinery_notes", "Otros criterios de maquinaria"),
        ):
            self._text(complements, key, label, state, 4)
        output = self._tab(notebook, "Listados y salida")
        ttk.Label(
            output, justify="left", wraplength=970,
            text="El anejo reúne los documentos base y sus fórmulas, seguidos de:\n\n"
                 "3. Precios elementales: mano de obra, maquinaria y materiales identificados.\n"
                 "4. Precios auxiliares utilizados en el proyecto.\n"
                 "5. Precios descompuestos por capítulos.\n\n"
                 "Se utilizan la plantilla, el formato PDF/ODT, la autoría, la entidad, "
                 "el expediente y el emplazamiento de la configuración general. "
                 "Los municipios aportan sus provincias y comunidades autónomas.\n\n"
                 "Las fórmulas se incorporan como texto editable con subíndices y exponentes, "
                 "no como imágenes. No recalculan ni modifican los precios del presupuesto. "
                 "Los costes indirectos se toman de la configuración sin aplicar otro recargo.",
        ).pack(anchor="w")
        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Label(actions, text="Datos comunes: Archivo → Configuración…").pack(side="left")
        ttk.Button(actions, text="Cerrar", command=self._close).pack(side="right")
        ttk.Button(actions, text="Guardar anejo…", command=self._export).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._loading = False
        self.bind("<FocusIn>", self._refresh_preview)
        self._refresh_preview()

    def _tab(self, notebook, title):
        """Crea un panel desplazable para conservar todos los campos en pantallas pequeñas."""
        page = ttk.Frame(notebook)
        notebook.add(page, text=title)
        canvas = tk.Canvas(page, highlightthickness=0)
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas, padding=12)
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        return content

    def _entry(self, parent, key, label, state):
        """Añade un dato específico del anejo, no una copia de la configuración general."""
        ttk.Label(parent, text=label).pack(anchor="w")
        variable = tk.StringVar(self, value=state[key])
        self.variables[key] = variable
        ttk.Entry(parent, textvariable=variable).pack(fill="x", pady=(2, 8))
        variable.trace_add("write", self._changed)

    def _choice(self, parent, key, label, state, choices):
        """Presenta valores legibles manteniendo identificadores estables en el proyecto."""
        ttk.Label(parent, text=label).pack(anchor="w")
        selected = next(label for label, value in choices.items() if value == state[key])
        variable = tk.StringVar(self, value=selected)
        self.variables[key] = variable
        ttk.Combobox(parent, textvariable=variable, values=list(choices), state="readonly").pack(
            fill="x", pady=(2, 8),
        )
        variable.trace_add("write", self._changed)

    def _text(self, parent, key, label, state, height):
        """Conserva las notas particulares como complementos opcionales del documento base."""
        ttk.Label(parent, text=label, wraplength=950).pack(anchor="w")
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(2, 8))
        text = tk.Text(frame, height=height, wrap="word", undo=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", state[key])
        text.edit_modified(False)
        text.bind("<<Modified>>", self._text_changed)
        self.texts[key] = text

    def _text_changed(self, event):
        """Consume la marca de modificación sin convertir su restablecimiento en otra edición."""
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._changed()

    def _collect(self) -> dict:
        """Recoge los datos específicos conservando los complementos y campos desconocidos."""
        state = dict(self._state)
        state.update({key: variable.get() for key, variable in self.variables.items()})
        state.update({key: widget.get("1.0", "end-1c") for key, widget in self.texts.items()})
        state["contract_type"] = self.contracts[state["contract_type"]]
        state["machinery_method"] = self.methods[state["machinery_method"]]
        return state

    def _changed(self, *_args):
        """Mantiene el estado y el historial de deshacer del proyecto al editar."""
        if not self._loading:
            self.app.price_justification_state = self._collect()
            self.app._mark_dirty()
            self._refresh_preview()

    def _refresh_preview(self, _event=None):
        """Actualiza el texto seleccionado con la configuración general vigente."""
        if self._loading:
            return
        general = general_variables(self.app)
        self.general_info.set(
            f"Proyecto: {general['titulo_proyecto']}\n"
            f"Emplazamiento: {general['emplazamiento'] or 'Pendiente en Configuración general'}\n"
            f"Provincia: {general['provincias'] or 'Pendiente'} · "
            f"Comunidad: {general['comunidades_autonomas'] or 'Pendiente'}"
        )
        selection = self.document_tree.selection()
        if not selection:
            return
        try:
            values = template_variables(self.app, self._collect())
            text = "\n\n".join(
                text for _, text in document_blocks(self.documents[selection[0]], values)
            )
        except ValueError as exc:
            text = f"ERROR EN EL DOCUMENTO BASE:\n{exc}"
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def open_library_editor(self):
        """Abre el mismo editor ZIP con historial de revisiones utilizado por el PPTP."""
        from tools._library_editor import LibraryEditor

        if self.library_editor is not None and self.library_editor.winfo_exists():
            self.library_editor.lift()
            return
        self.library_editor = LibraryEditor(
            self, LIBRARY_PATH, "Biblioteca de Justificación de precios",
            on_saved=self._reload_library, used_members={name for name, _ in DOCUMENTS},
            track_changes=True,
        )

    def _reload_library(self):
        """Recarga las versiones guardadas para que la vista y el próximo anejo las utilicen."""
        try:
            self.documents = dict(load_documents(LIBRARY_PATH))
        except (OSError, ValueError) as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return
        self._refresh_preview()

    def _validated(self) -> dict:
        """Valida las entradas antes de cerrar o exportar, sin copiar datos generales."""
        state = validate_state(self._collect())
        self.app.price_justification_state = state
        self.app._mark_dirty()
        return state

    def _close(self):
        """Conserva los datos y permite resolver las ediciones de biblioteca sin guardar."""
        if self.library_editor is not None and self.library_editor.winfo_exists():
            self.library_editor.close()
            if self.library_editor.winfo_exists():
                return
        try:
            self._validated()
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return
        self.destroy()

    def _export(self):
        """Genera el anejo desde los documentos base guardados y la configuración actual."""
        try:
            if (self.library_editor is not None and self.library_editor.winfo_exists()
                    and self.library_editor.dirty):
                raise ValueError("Guarda o descarta los cambios de la biblioteca antes de generar el anejo.")
            state = self._validated()
            output_format = self.app.report_settings.output_format
            if output_format not in {"pdf", "odt"}:
                raise ValueError(f"Formato de informe no admitido: {output_format}")
            target = filedialog.asksaveasfilename(
                parent=self, title="Guardar justificación de precios",
                defaultextension=f".{output_format}",
                initialfile=f"Justificación de precios.{output_format}",
                filetypes=[(output_format.upper(), f"*.{output_format}")],
            )
            if not target:
                return
            write_document(self.app, Path(target).with_suffix(f".{output_format}"), state)
        except (OSError, ValueError, ImportError) as exc:
            messagebox.showerror(TITLE, f"No se pudo generar el anejo:\n{exc}", parent=self)
            return
        messagebox.showinfo(TITLE, "Justificación de precios guardada.", parent=self)
