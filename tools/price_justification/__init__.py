"""Herramienta editable de justificación de precios y sus listados presupuestarios."""

from datetime import date
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
from localization import messagebox

from .document import FIELDS, TITLE, restore_state, validate_state, write_document


TOOL_LABEL = TITLE
TOOL_ORDER = 0


def restore_project_state(data) -> dict:
    """Restaura datos independientes del presupuesto y comprueba su estructura."""
    return restore_state(data)


def before_project_save(app) -> None:
    """Valida también las ediciones pendientes antes de escribir el proyecto."""
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
    """Organiza los datos variables sin anticipar una calculadora de fórmulas."""

    def __init__(self, app):
        """Construye las pestañas de documentación y enlaza las ediciones al proyecto."""
        super().__init__(app.root)
        self.app = app
        self.title(TITLE)
        self.geometry("1000x760")
        self.minsize(800, 620)
        self.transient(app.root)
        self._loading = True
        state = FIELDS | restore_state(app.price_justification_state)
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
            container, wraplength=930,
            text="Un único anejo con texto editable y los tres listados del presupuesto actual. "
                 "Las fórmulas quedan pendientes; estos datos no recalculan ni cambian los precios.",
        ).pack(anchor="w", pady=(5, 10))
        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)
        general = self._tab(notebook, "Datos y normativa")
        self._entry(general, "reference_date", "Fecha de referencia (AAAA-MM-DD)", state)
        self._entry(general, "territory", "Ámbito territorial (provincia / comunidad)", state)
        self._choice(general, "contract_type", "Régimen de contratación", state, self.contracts)
        self._entry(general, "price_source", "Fuente de precios y fecha / edición", state)
        self._text(general, "legislation_notes", "Normativa y referencias complementarias", state, 5)
        self._text(general, "composition_notes", "Criterios de composición de los precios", state, 5)
        indirect = self._tab(notebook, "Costes indirectos")
        ttk.Label(
            indirect, wraplength=890,
            text=f"Porcentaje de los informes: {app.report_settings.indirect_costs:g} %. "
                 "Se cambia en la configuración de informes, no aquí. No se aplicará dos veces.",
        ).pack(anchor="w", pady=(0, 8))
        self._text(indirect, "indirect_staff", "Personal: puesto, dedicación, plazo y coste documentado", state, 4)
        self._text(indirect, "indirect_installations", "Instalaciones provisionales y medios comunes", state, 4)
        self._text(indirect, "indirect_technical", "Trabajos técnicos, ensayos y controles contractuales", state, 4)
        self._text(indirect, "indirect_other", "Otros costes y justificación del porcentaje adoptado", state, 4)
        labor = self._tab(notebook, "Mano de obra")
        for key, label in (
            ("agreement", "Convenio colectivo y revisión salarial"),
            ("wage_reference", "Referencia salarial / SMI aplicable"),
            ("contributions", "Cotizaciones y contingencias profesionales"),
            ("calendar", "Calendario laboral aplicable"),
            ("annual_hours", "Jornada anual efectiva (horas, opcional)"),
        ):
            self._entry(labor, key, label, state)
        self._text(labor, "labor_notes", "Retribuciones, conceptos no salariales y criterios", state, 7)
        materials = self._tab(notebook, "Materiales")
        ttk.Label(
            materials, wraplength=890,
            text="Documenta adquisición, carga, transporte y descarga; comprueba qué conceptos "
                 "ya están incluidos en el presupuesto para no duplicarlos.",
        ).pack(anchor="w", pady=(0, 10))
        self._text(
            materials, "materials_notes",
            "Procedencias, ofertas, distancias y condiciones de suministro a pie de obra", state, 17,
        )
        machinery = self._tab(notebook, "Maquinaria")
        self._choice(machinery, "machinery_method", "Criterio de valoración", state, self.methods)
        self._text(
            machinery, "machinery_parameters",
            "Parámetros y fuentes: reposición, vida útil, horas, días, interés, seguros y conservación",
            state, 5,
        )
        self._text(machinery, "machinery_consumption", "Operarios, energía, combustibles y consumos secundarios", state, 5)
        self._text(machinery, "machinery_notes", "Criterios adicionales y conceptos ya incluidos", state, 4)
        listings = self._tab(notebook, "Listados y salida")
        ttk.Label(
            listings, justify="left", wraplength=890,
            text="El documento incluye:\n\n"
                 "1. Legislación vigente.\n"
                 "2. Composición de los precios: costes indirectos, mano de obra, materiales y maquinaria.\n"
                 "3. Precios elementales: mano de obra, maquinaria y materiales identificados.\n"
                 "4. Precios auxiliares utilizados en el proyecto.\n"
                 "5. Precios descompuestos por capítulos.\n\n"
                 "Se reutilizan los rendimientos, precios y redondeos de los informes existentes. "
                 "El formato PDF u ODT y la plantilla son los seleccionados en Configuración de informes.\n\n"
                 "Los campos sin completar se señalan expresamente como pendientes en el documento. "
                 "Revisa referencias y datos antes de aprobarlo. No se incluyen las fórmulas del ejemplo.",
        ).pack(anchor="w")
        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Label(actions, text="Las ediciones se guardan con el proyecto.").pack(side="left")
        ttk.Button(actions, text="Cerrar", command=self._close).pack(side="right")
        ttk.Button(actions, text="Guardar anejo…", command=self._export).pack(side="right", padx=8)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._loading = False

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
        """Añade un dato de una línea con persistencia inmediata de su edición."""
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
        """Añade texto libre con desplazamiento y detecta sus cambios."""
        ttk.Label(parent, text=label, wraplength=870).pack(anchor="w")
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
        """Recoge también las entradas intermedias para que el proyecto detecte cambios pendientes."""
        state = {key: variable.get() for key, variable in self.variables.items()}
        state.update({key: widget.get("1.0", "end-1c") for key, widget in self.texts.items()})
        state["contract_type"] = self.contracts[state["contract_type"]]
        state["machinery_method"] = self.methods[state["machinery_method"]]
        return state

    def _changed(self, *_args):
        """Mantiene el estado y el historial de deshacer del proyecto al editar."""
        if not self._loading:
            self.app.price_justification_state = self._collect()
            self.app._mark_dirty()

    def _validated(self) -> dict:
        """Valida antes de cerrar o exportar y registra la fecha propuesta si no había ediciones."""
        state = validate_state(self._collect())
        self.app.price_justification_state = state
        self.app._mark_dirty()
        return state

    def _close(self):
        """Conserva los datos válidos al cerrar y muestra los errores en la propia ventana."""
        try:
            self._validated()
        except ValueError as exc:
            messagebox.showerror(TITLE, str(exc), parent=self)
            return
        self.destroy()

    def _export(self):
        """Solicita una ruta solo tras validar y exporta con el formato común del proyecto."""
        try:
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
