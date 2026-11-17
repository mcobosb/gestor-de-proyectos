"""Confirmacion de normativa y asociaciones NTP antes de emitir el estudio."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import webbrowser

from localization import localize_widget_tree, messagebox
from .regulations import (
    REGULATIONS, WorkUnit, initially_selected, law_proposals, load_ntps,
    ntp_proposals, restore_review, review_context,
)


class RegulatoryReviewWindow(tk.Toplevel):
    """Conserva decisiones por norma y por pareja unidad de obra/NTP."""

    def __init__(self, owner, app, units: list[WorkUnit], base_state: dict):
        """Carga propuestas y decisiones previas sin confirmar automaticamente su aplicacion."""
        self.catalog = load_ntps()
        self.proposals = ntp_proposals(units, self.catalog)
        saved = restore_review(app.safety_health_state.get("regulatory_review"))
        self.laws = law_proposals(units)
        self.laws.update(saved.get("laws", {}))
        self.ntps = {code: dict(choices) for code, choices in saved.get("ntps", {}).items()}
        for unit, proposals in self.proposals.items():
            choices = self.ntps.setdefault(unit, {})
            for code, reason in proposals.items():
                choices.setdefault(code, initially_selected(self.catalog[code], reason))
        for choices in self.ntps.values():
            for code in choices:
                if code not in self.catalog:
                    raise ValueError(f"La NTP guardada {code} ya no existe en el catálogo.")
        super().__init__(owner)
        self.app, self.units, self.base_state = app, units, base_state
        self.transient(owner)
        self.title("Confirmar normativa y NTP del estudio")
        self.geometry("1180x740")
        self.minsize(900, 600)
        ttk.Label(
            self, padding=10, wraplength=1100,
            text="Confirma ámbito, fecha de ejecución y condiciones reales. Las propuestas no acreditan "
                 "aplicabilidad ni vigencia. Las NTP son guías técnicas, no legislación obligatoria. "
                 "Las coincidencias solo temáticas y las referencias con avisos no se preseleccionan.",
        ).pack(fill="x")
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10)
        legal, technical = ttk.Frame(notebook), ttk.Frame(notebook)
        notebook.add(legal, text="Normativa de aplicación")
        notebook.add(technical, text="Unidades de obra y NTP")
        self.legal_tree = self._table(legal, (
            ("include", "Incluir", 60), ("law", "Disposición", 750),
            ("units", "Unidades relacionadas / condición", 320),
        ))
        for law in REGULATIONS:
            related = [
                unit.code for unit in units
                if set(law.triggers).intersection((*unit.risks, *unit.measures))
            ]
            self.legal_tree.insert("", "end", iid=law.code, values=(
                "X" if self.laws[law.code] else "", law.title,
                ", ".join(dict.fromkeys(related)) if law.triggers else "Marco general; confirmar condiciones.",
            ))
        self.legal_tree.bind("<ButtonRelease-1>", self._toggle_law)
        self.legal_tree.bind("<space>", self._toggle_law)
        controls = ttk.Frame(technical, padding=6)
        controls.pack(fill="x")
        self.unit_choice = ttk.Combobox(
            controls, state="readonly", width=22,
            values=list(dict.fromkeys(unit.code for unit in units)),
        )
        self.unit_choice.pack(side="left", padx=4)
        self.ntp_labels = {f"{entry.code} — {entry.title}": entry.code for entry in self.catalog.values()}
        self.ntp_choice = ttk.Combobox(controls, width=70, values=list(self.ntp_labels))
        self.ntp_choice.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(controls, text="Asignar NTP", command=self._assign).pack(side="left")
        self.ntp_tree = self._table(technical, (
            ("include", "Incluir", 60), ("unit", "Unidad de obra", 140),
            ("ntp", "NTP", 85), ("title", "Título", 410),
            ("state", "Aviso del catálogo", 130), ("reason", "Origen de la propuesta", 320),
        ))
        self.ntp_tree.bind("<ButtonRelease-1>", self._toggle_ntp)
        self.ntp_tree.bind("<space>", self._toggle_ntp)
        self.ntp_tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.detail = tk.Text(self, height=5, wrap="word", state="disabled")
        self.detail.pack(fill="x", padx=10, pady=6)
        actions = ttk.Frame(self, padding=10)
        actions.pack(fill="x")
        ttk.Button(actions, text="Ver NTP oficial", command=self._open_ntp).pack(side="left")
        ttk.Button(actions, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(
            actions, text="Confirmar selección", command=self._accept, style="Accent.TButton",
        ).pack(side="right", padx=8)
        self._refresh_ntps()
        localize_widget_tree(self)
        self.grab_set()

    def _table(self, parent, columns):
        """Construye una tabla con desplazamiento en ambos ejes."""
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[key for key, _label, _width in columns], show="headings")
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=False)
        tree.grid(row=0, column=0, sticky="nsew")
        for orient, command, row, column, sticky, option in (
            ("vertical", tree.yview, 0, 1, "ns", "yscrollcommand"),
            ("horizontal", tree.xview, 1, 0, "ew", "xscrollcommand"),
        ):
            scrollbar = ttk.Scrollbar(frame, orient=orient, command=command)
            scrollbar.grid(row=row, column=column, sticky=sticky)
            tree.configure(**{option: scrollbar.set})
        return tree

    def _event_row(self, tree, event):
        """Limita los clics a la casilla y permite alternar con la barra espaciadora."""
        if event is not None and event.keysym != "space":
            return tree.identify_row(event.y) if tree.identify_column(event.x) == "#1" else ""
        return (tree.selection() or ("",))[0]

    def _toggle_law(self, event=None):
        """Conserva las inclusiones y exclusiones expresas de normativa."""
        row = self._event_row(self.legal_tree, event)
        if row:
            self.laws[row] = not self.laws[row]
            self.legal_tree.set(row, "include", "X" if self.laws[row] else "")
        return "break"

    def _refresh_ntps(self):
        """Mantiene visibles las unidades sin propuestas y las asignaciones manuales."""
        self.ntp_tree.delete(*self.ntp_tree.get_children())
        self.rows = {}
        for unit in dict.fromkeys(unit.code for unit in self.units):
            choices = self.ntps.get(unit, {})
            if not choices:
                self.ntp_tree.insert("", "end", values=(
                    "", unit, "", "Sin propuesta: revisar y asignar manualmente si procede.", "", "",
                ))
            for code, selected in choices.items():
                if code not in self.catalog:
                    raise ValueError(f"La NTP guardada {code} ya no existe en el catálogo.")
                entry = self.catalog[code]
                row = self.ntp_tree.insert("", "end", values=(
                    "X" if selected else "", unit, code, entry.title, entry.status,
                    self.proposals.get(unit, {}).get(code, "Asignación manual."),
                ))
                self.rows[row] = (unit, code)

    def _toggle_ntp(self, event=None):
        """Alterna solo la pareja partida/NTP seleccionada, sin afectar a otras partidas."""
        row = self._event_row(self.ntp_tree, event)
        if row in self.rows:
            unit, code = self.rows[row]
            self.ntps[unit][code] = not self.ntps[unit][code]
            self.ntp_tree.set(row, "include", "X" if self.ntps[unit][code] else "")
        return "break"

    def _assign(self):
        """Permite asociar cualquier NTP del catalogo, sin inventar sus identificadores."""
        unit = self.unit_choice.get()
        code = self.ntp_labels.get(self.ntp_choice.get(), self.ntp_choice.get().strip().upper())
        if unit not in {item.code for item in self.units} or code not in self.catalog:
            messagebox.showerror("Normativa y NTP", "Selecciona una unidad y una NTP del catálogo.", parent=self)
            return
        self.ntps.setdefault(unit, {})[code] = True
        self._refresh_ntps()

    def _show_detail(self, _event=None):
        """Presenta riesgos, medidas, avisos y enlace oficial antes de la confirmacion."""
        row = (self.ntp_tree.selection() or ("",))[0]
        if row not in self.rows:
            return
        unit_code, code = self.rows[row]
        units = [unit for unit in self.units if unit.code == unit_code]
        entry = self.catalog[code]
        text = "\n".join((
            f"{code} — {entry.title} ({entry.publication}; {entry.status})",
            "Riesgos: " + ", ".join(dict.fromkeys(risk for unit in units for risk in unit.risks)),
            "Medidas: " + ", ".join(dict.fromkeys(measure for unit in units for measure in unit.measures)),
            self.proposals.get(unit_code, {}).get(code, "Asignación manual."),
            "Comprobar el documento original, su alcance y los avisos: " + entry.url,
        ))
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _open_ntp(self):
        """Abre exclusivamente el enlace oficial validado por el lector del catalogo."""
        row = (self.ntp_tree.selection() or ("",))[0]
        if row in self.rows and not webbrowser.open(self.catalog[self.rows[row][1]].url):
            messagebox.showerror("Normativa y NTP", "No se pudo abrir la ficha oficial.", parent=self)

    def _accept(self):
        """Guarda la confirmacion con el contexto exacto de las unidades revisadas."""
        state = dict(self.app.safety_health_state) or dict(self.base_state)
        state["regulatory_review"] = restore_review({
            "context": review_context(self.units), "laws": self.laws, "ntps": self.ntps,
        })
        self.app.safety_health_state = state
        self.app._mark_dirty()
        self.destroy()
