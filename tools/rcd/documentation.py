"""Revisión de los documentos base y decisiones particulares del estudio RCDs."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from localization import localize_widget_tree, messagebox
from tools._library_editor import LibraryEditor
from tools._library_markdown import document_markdown, text_blocks
from . import study


class RcdDocumentationWindow(tk.Toplevel):
    """Separa los textos comunes editables de los datos particulares del proyecto."""

    def __init__(self, owner, documents: dict[str, str]):
        """Permite revisar los textos incluso antes de completar los datos numéricos."""
        super().__init__(owner)
        self.owner = owner
        self.documents = documents
        self.library_editor = None
        self.texts = {}
        self.title("Documentación del estudio RCDs")
        self.geometry("1080x760")
        self.transient(owner)
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)
        ttk.Label(container, wraplength=1000, text=(
            "Proyecto, emplazamiento, promotor, autoría y expediente: Configuración general. "
            "El PEM se toma del presupuesto actual. Si existe un resumen de la actuación en "
            "Seguridad y Salud o en el PPTP, se reutiliza en la memoria."
        )).pack(fill="x", pady=(0, 10))
        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)
        documents_page = ttk.Frame(notebook, padding=8)
        notebook.add(documents_page, text="Documentos base")
        ttk.Button(documents_page, text="Editar biblioteca de documentos…",
                   command=self.open_library_editor).pack(anchor="w", pady=(0, 8))
        panes = ttk.Panedwindow(documents_page, orient="horizontal")
        panes.pack(fill="both", expand=True)
        self.document_tree = ttk.Treeview(panes, show="tree", selectmode="browse", height=12)
        for name, label in study.DOCUMENTS:
            self.document_tree.insert("", "end", iid=name, text=label)
        panes.add(self.document_tree, weight=1)
        preview_frame = ttk.Frame(panes)
        self.preview = tk.Text(preview_frame, wrap="word", state="disabled", width=70)
        preview_scroll = ttk.Scrollbar(preview_frame, command=self.preview.yview)
        self.preview.configure(yscrollcommand=preview_scroll.set)
        preview_scroll.pack(side="right", fill="y")
        self.preview.pack(fill="both", expand=True)
        panes.add(preview_frame, weight=3)
        self.document_tree.bind("<<TreeviewSelect>>", self.refresh_preview)
        notes_page = ttk.Frame(notebook)
        notebook.add(notes_page, text="Decisiones de la obra")
        canvas = tk.Canvas(notes_page, highlightthickness=0)
        scrollbar = ttk.Scrollbar(notes_page, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        form = ttk.Frame(canvas, padding=10)
        window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        ttk.Label(form, wraplength=970, text=(
            "Estas decisiones se guardan con el proyecto. Los campos vacíos aparecen como pendientes "
            "en el estudio: no implican ausencia de peligrosos, de reutilización o de valorización."
        )).pack(fill="x", pady=(0, 8))
        for key, label in study.DOCUMENT_FIELDS.items():
            ttk.Label(form, text=label).pack(anchor="w")
            frame = ttk.Frame(form)
            frame.pack(fill="x", pady=(2, 10))
            text = tk.Text(frame, wrap="word", height=4, undo=True)
            scroll = ttk.Scrollbar(frame, command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            text.pack(fill="x", expand=True)
            text.insert("1.0", owner.data.documentation.get(key, ""))
            text.edit_modified(False)
            text.bind("<<Modified>>", self.text_changed)
            self.texts[key] = text
        ttk.Button(container, text="Cerrar", command=self.close).pack(anchor="e", pady=(10, 0))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<FocusIn>", self.refresh_preview)
        self.document_tree.selection_set(study.DOCUMENTS[0][0])
        self.refresh_preview()
        localize_widget_tree(self)

    def text_changed(self, event):
        """Publica cada edición particular sin cambiar el análisis ni duplicar ajustes generales."""
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self.owner.data.documentation.update({
                key: widget.get("1.0", "end-1c") for key, widget in self.texts.items()
            })
            self.owner.changed()
            self.refresh_preview()

    def refresh_preview(self, _event=None):
        """Muestra el documento resuelto e indica las tablas que se insertarán al generar."""
        selection = self.document_tree.selection()
        if not selection:
            return
        try:
            values = study.study_variables(
                self.owner.data, self.owner.app._project_title(), self.owner.app.report_settings,
            )
            values.update({
                name: f"[{label}: se integra como tabla al generar el estudio]"
                for name, label in (
                    ("tabla_proyecto", "Datos del proyecto"),
                    ("tabla_residuos", "Residuos seleccionados, evidencias y vínculos LER–BCCA2023"),
                    ("tabla_estimacion", "Datos de cálculo y cantidades por grupos"),
                    ("tabla_demolicion", "Trazabilidad de las mediciones de demolición, si procede"),
                    ("tabla_separacion", "Umbrales de separación"),
                    ("tabla_presupuesto", "Tablas del presupuesto calculado"),
                )
            })
            source = document_markdown(self.documents[selection[0]])
            text = "\n\n".join(study.substitute(text, values) for _, text in text_blocks(source))
        except ValueError as exc:
            text = f"ERROR EN EL DOCUMENTO BASE:\n{exc}"
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def open_library_editor(self):
        """Reutiliza el editor ZIP y su historial, sin imponer el catálogo de fichas del PPTP."""
        if self.library_editor is not None and self.library_editor.winfo_exists():
            self.library_editor.lift()
            return
        self.library_editor = LibraryEditor(
            self, study.LIBRARY_PATH, "Biblioteca del estudio RCDs",
            on_saved=self.reload_library, used_members={name for name, _ in study.DOCUMENTS},
            track_changes=True,
        )

    def reload_library(self):
        """Recarga las versiones guardadas que se utilizarán en el siguiente estudio."""
        try:
            self.documents = dict(study.load_documents(study.LIBRARY_PATH))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Biblioteca RCDs", str(exc), parent=self)
            return
        self.refresh_preview()

    def has_unsaved_library(self) -> bool:
        """Impide exportar un texto distinto del que aún está editándose sin guardar."""
        return (self.library_editor is not None and self.library_editor.winfo_exists()
                and self.library_editor.dirty)

    def close(self):
        """Respeta la cancelación al resolver cambios pendientes en la biblioteca."""
        if self.library_editor is not None and self.library_editor.winfo_exists():
            self.library_editor.close()
            if self.library_editor.winfo_exists():
                return
        self.destroy()
