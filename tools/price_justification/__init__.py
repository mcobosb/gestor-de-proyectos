"""Ventana de datos específicos y generación de justificación de precios."""

from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from localization import filedialog, messagebox
from .document import (
    ANNUAL_HOURS_REFERENCES, CALENDAR_REFERENCES, CONSTRUCTION_AGREEMENT_REFERENCES,
    CONTRIBUTION_BASE_REFERENCES, CONTRIBUTION_DETAIL_REFERENCES,
    CONTRIBUTION_RATE_REFERENCES, FIELDS, PENSION_CONTRIBUTION_REFERENCES,
    SALARY_REVISION_REFERENCES, TITLE, WAGE_REFERENCES, WORK_CONDITION_REFERENCES,
    restore_state, validate_state, write_document,
)
from .templates import DOCUMENTS, LIBRARY_PATH, load_documents


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
    """Edita los datos específicos del anejo y permite generar su documento."""

    def __init__(self, app):
        """Construye el formulario de datos específicos y carga sus valores predeterminados."""
        state = FIELDS | restore_state(app.price_justification_state)
        super().__init__(app.root)
        self.app = app
        self._state = state
        self.library_editor = None
        self.title(TITLE)
        self.geometry("1120x760")
        self.minsize(860, 620)
        self.transient(app.root)
        self._loading = True
        if not state["reference_date"]:
            state["reference_date"] = date.today().isoformat()
        for key in (
            "agreement", "salary_revision", "wage_reference", "contributions",
            "contribution_bases", "contribution_details", "pension_contribution",
            "calendar", "work_conditions", "annual_hours",
        ):
            if not state[key]:
                state[key] = FIELDS[key]
        self.variables: dict[str, tk.StringVar] = {}
        self.selectors: dict[str, ttk.Combobox] = {}
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
            text="Completa los datos específicos de la justificación. "
                 "Los textos base se editan desde la biblioteca de documentos.",
        ).pack(anchor="w", pady=(5, 8))
        self.general_info = tk.StringVar(self)
        ttk.Label(container, textvariable=self.general_info, wraplength=1030).pack(anchor="w", pady=(0, 10))
        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)
        specific = self._tab(self.notebook, "Datos específicos")
        self._entry(specific, "reference_date", "Fecha de referencia (AAAA-MM-DD)", state)
        self._choice(specific, "contract_type", "Régimen de contratación", state, self.contracts)
        self._entry(specific, "price_source", "Fuente de precios y fecha / edición", state)
        self._editable_choice(
            specific, "agreement", "Convenio colectivo y revisión salarial",
            state, CONSTRUCTION_AGREEMENT_REFERENCES,
        )
        self._editable_choice(
            specific, "salary_revision", "Revisión salarial (art. 55)",
            state, SALARY_REVISION_REFERENCES,
        )
        self._editable_choice(
            specific, "wage_reference", "Referencia salarial / SMI aplicable",
            state, WAGE_REFERENCES,
        )
        self._editable_choice(
            specific, "contribution_bases", "Bases de cotización (referencia 2026)",
            state, CONTRIBUTION_BASE_REFERENCES,
        )
        self._editable_choice(
            specific, "contributions", "Tipos de cotización (referencia 2026)",
            state, CONTRIBUTION_RATE_REFERENCES,
        )
        self._editable_choice(
            specific, "contribution_details", "Conceptos cotizables y exentos (referencia 2026)",
            state, CONTRIBUTION_DETAIL_REFERENCES,
        )
        self._editable_choice(
            specific, "pension_contribution",
            "Aportación empresarial al plan de pensiones (art. 56; prorratear si procede)",
            state, PENSION_CONTRIBUTION_REFERENCES,
        )
        self._editable_choice(
            specific, "calendar", "Calendario laboral y días de ajuste (referencia 2026)",
            state, CALENDAR_REFERENCES,
        )
        self._editable_choice(
            specific, "work_conditions", "Vacaciones, horas extra y condiciones climáticas",
            state, WORK_CONDITION_REFERENCES,
        )
        self._editable_choice(
            specific, "annual_hours", "Jornada anual efectiva (horas)",
            state, ANNUAL_HOURS_REFERENCES,
        )
        self._choice(specific, "machinery_method", "Criterio de maquinaria", state, self.methods)
        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Label(actions, text="Datos comunes: Archivo → Configuración…").pack(side="left")
        ttk.Button(actions, text="Cerrar", command=self._close).pack(side="right")
        ttk.Button(actions, text="Guardar anejo…", command=self._export).pack(side="right", padx=8)
        ttk.Button(
            actions, text="Editar biblioteca de documentos…", command=self.open_library_editor,
        ).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._loading = False
        self.bind("<FocusIn>", self._refresh_general_info)
        self._refresh_general_info()

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

    def _editable_choice(self, parent, key, label, state, choices):
        """Ofrece datos de referencia seleccionables y permite sustituirlos por valores locales."""
        ttk.Label(parent, text=label).pack(anchor="w")
        variable = tk.StringVar(self, value=state[key])
        self.variables[key] = variable
        selector = ttk.Combobox(
            parent, textvariable=variable, values=choices, state="normal",
        )
        selector.pack(fill="x", pady=(2, 8))
        self.selectors[key] = selector
        variable.trace_add("write", self._changed)

    def _collect(self) -> dict:
        """Recoge los datos específicos sin descartar datos guardados por versiones anteriores."""
        state = dict(self._state)
        state.update({key: variable.get() for key, variable in self.variables.items()})
        state["contract_type"] = self.contracts[state["contract_type"]]
        state["machinery_method"] = self.methods[state["machinery_method"]]
        return state

    def _changed(self, *_args):
        """Mantiene el estado y el historial de deshacer del proyecto al editar."""
        if not self._loading:
            self.app.price_justification_state = self._collect()
            self.app._mark_dirty()

    def _refresh_general_info(self, _event=None):
        """Actualiza el resumen de los datos generales del proyecto."""
        if self._loading:
            return
        from .templates import general_variables

        general = general_variables(self.app)
        self.general_info.set(
            f"Proyecto: {general['titulo_proyecto']}\n"
            f"Emplazamiento: {general['emplazamiento'] or 'Pendiente en Configuración general'}\n"
            f"Provincia: {general['provincias'] or 'Pendiente'} · "
            f"Comunidad: {general['comunidades_autonomas'] or 'Pendiente'}"
        )

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
        """Comprueba que la biblioteca editada conserva todos los documentos requeridos."""
        try:
            load_documents(LIBRARY_PATH)
        except (OSError, ValueError) as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)

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
