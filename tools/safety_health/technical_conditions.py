"""Selección revisable de prescripciones técnicas, sin sustituir la evaluación de riesgos."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import tkinter as tk
from tkinter import ttk
import unicodedata

from localization import localize_widget_tree
from .external_conditions import restore_external_conditions
from .regulations import WorkUnit


@dataclass(frozen=True)
class TechnicalBlock:
    """Identifica una prescripción y los indicios que permiten proponerla."""

    key: str
    title: str
    references: tuple[str, ...] = ()
    equipment: str = ""
    conditions: tuple[str, ...] = ()


TECHNICAL_BLOCKS = (
    TechnicalBlock(
        "barriers", "Vallas, barreras y cerramientos móviles",
        ("PC-BARRERA-RIGIDA", "MP-SEGREGACION", "R-TERCEROS"),
        r"\b(?:valla|barrera|cerramiento)", ("peatones_terceros",),
    ),
    TechnicalBlock(
        "openings", "Protección de huecos, arquetas y registros",
        ("MP-PROTEGER-HUECOS", "PC-TAPA-RESISTENTE", "PC-ENTIBACION"),
    ),
    TechnicalBlock(
        "access", "Pasos provisionales y accesos",
        ("MP-ACCESOS-SEGUROS", "PC-ACCESO-EXCAVACION"), conditions=("peatones_terceros",),
    ),
    TechnicalBlock(
        "mobile", "Maquinaria móvil y vehículos",
        ("EQ-CAMION", "EQ-EXCAVADORA", "EQ-CARGADORA", "R-RETROCESO-MAQUINA"),
        r"\b(?:camion|excavadora|cargadora|dumper|motoniveladora|carretilla|tractor)",
    ),
    TechnicalBlock(
        "paving", "Equipos de extendido y compactación",
        ("EQ-EXTENDEDORA", "EQ-RODILLO-COMPACTADOR"),
        r"\b(?:extendedora|rodillo|rulo|compactador(?!a?\s+manual))",
    ),
    TechnicalBlock(
        "cutting", "Equipos de corte, demolición y compactación manual",
        ("EQ-CORTE", "EQ-MARTILLO-DEMOLEDOR", "EQ-EXTRACCION-TESTIGOS"),
        r"\b(?:cortadora|sierra|martillo|radial|pison|bandeja\s+vibrante)",
    ),
    TechnicalBlock(
        "lifting", "Elevación de cargas",
        ("EQ-ELEVACION", "R-IZADO-CARGAS", "R-CAIDA-CARGA-SUSPENDIDA"),
        r"\b(?:grua|polipasto|maquinillo)",
    ),
    TechnicalBlock(
        "signage", "Señalización temporal de obras",
        ("PC-SENALIZACION", "PC-BALIZAMIENTO"), conditions=("trafico_existente",),
    ),
    TechnicalBlock(
        "signallers", "Señalistas",
        ("R-RETROCESO-MAQUINA", "R-IZADO-CARGAS"), conditions=("trafico_existente",),
    ),
    TechnicalBlock(
        "pedestrians", "Peatones, residentes y accesos",
        ("R-TERCEROS", "MP-SEGREGACION"), conditions=("peatones_terceros",),
    ),
)
_KEYS = {block.key for block in TECHNICAL_BLOCKS}
_BLOCK_RE = re.compile(
    r"^<!-- ESS-TECH:([a-z]+) -->\n(.*?)^<!-- /ESS-TECH -->[ \t]*\n?",
    re.MULTILINE | re.DOTALL,
)
_REVIEW_MARKER = "<!-- ESS-TECH-REVIEW -->"


def restore_technical_review(value: object) -> dict:
    """Valida las decisiones explícitas sin confirmar datos ausentes o incompletos."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("La revisión de condiciones técnicas debe ser un objeto JSON.")
    context, blocks = value.get("context"), value.get("blocks")
    if (
        not isinstance(context, str) or not re.fullmatch(r"[a-f0-9]{64}", context)
        or not isinstance(blocks, dict)
        or any(key not in _KEYS or not isinstance(selected, bool) for key, selected in blocks.items())
    ):
        raise ValueError("La revisión de condiciones técnicas contiene decisiones no válidas.")
    return {"context": context, "blocks": dict(blocks)}


def technical_review_confirmed(value: object, context: str) -> bool:
    """Exige revisar todos los bloques para el contexto actual."""
    review = restore_technical_review(value)
    return review.get("context") == context and set(review.get("blocks", {})) == _KEYS


def technical_review_data(
    units: list[WorkUnit], references: dict[str, list[str]],
    resources: list[tuple[str, str]], conditions: object, template: str,
) -> tuple[str, dict[str, str]]:
    """Propone bloques por referencias, recursos físicos y condicionantes expresamente aplicables."""
    external = restore_external_conditions(conditions)
    codes = {code for group in references.values() for code in group}
    proposals = {}
    for block in TECHNICAL_BLOCKS:
        reasons = []
        matched = codes.intersection(block.references)
        if matched:
            reasons.append("Referencias de las fichas: " + ", ".join(sorted(matched)))
        for code, label in resources:
            normalized = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().casefold()
            if block.equipment and re.search(block.equipment, normalized):
                reasons.append(f"Recurso del presupuesto: {code} — {label}")
        for key in block.conditions:
            if external.get(key, {}).get("applicability") == "yes":
                reasons.append("Condicionante confirmado: " + key)
        if reasons:
            proposals[block.key] = "; ".join(reasons)
    payload = {
        "units": sorted(
            (asdict(unit) for unit in units),
            key=lambda row: (row["chapter"], row["code"], row["summary"]),
        ),
        "references": sorted(codes),
        "resources": sorted(set(resources)), "conditions": external,
        "template": template, "rules": [asdict(block) for block in TECHNICAL_BLOCKS],
    }
    context = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return context, proposals


def selected_technical_template(
    template: str, saved: object, context: str, pending: list[str],
) -> str:
    """Mantiene el núcleo general e incorpora exclusivamente decisiones actuales confirmadas."""
    template = template.replace("\r\n", "\n")
    keys = [match.group(1) for match in _BLOCK_RE.finditer(template)]
    if (
        len(keys) != len(_KEYS) or set(keys) != _KEYS
        or template.count("<!-- ESS-TECH:") != len(keys)
        or template.count("<!-- /ESS-TECH -->") != len(keys)
        or template.count(_REVIEW_MARKER) != 1
    ):
        raise ValueError("La plantilla del pliego no contiene todos los bloques técnicos revisables.")
    confirmed = technical_review_confirmed(saved, context)
    review = restore_technical_review(saved)
    choices = review.get("blocks", {}) if confirmed else {}
    notice = ""
    if not confirmed:
        notice = (
            "[VALIDAR] Confirmar las condiciones técnicas específicas para las unidades, "
            "equipos y condicionantes actuales. Los bloques específicos pendientes de "
            "revisión no se han incorporado; este documento no acredita su innecesariedad."
        )
        pending.append(notice)
    elif not any(choices.values()):
        notice = "Tras la revisión no se han seleccionado bloques técnicos específicos; se conservan las condiciones generales."

    def include(match: re.Match[str]) -> str:
        """Retira también los marcadores de los bloques incluidos."""
        return match.group(2).strip() + "\n\n" if choices.get(match.group(1), False) else ""

    return _BLOCK_RE.sub(include, template).replace(_REVIEW_MARKER, notice)


class TechnicalReviewWindow(tk.Toplevel):
    """Muestra todos los bloques para confirmar propuestas y añadir condiciones no detectadas."""

    def __init__(self, owner, app, context, proposals, pending, base_state):
        """Restaura inclusiones y exclusiones sin convertir indicios en una evaluación."""
        saved = restore_technical_review(app.safety_health_state.get("technical_review"))
        super().__init__(owner)
        self.app, self.context, self.base_state = app, context, base_state
        self.proposals, self.pending = proposals, pending
        self.transient(owner)
        self.title("Confirmar condiciones técnicas del estudio")
        self.geometry("1060x680")
        self.minsize(850, 550)
        ttk.Label(
            self, padding=10, wraplength=980,
            text="Las condiciones generales se incluyen siempre. Confirma los bloques específicos: "
                 "las propuestas son indicios, no una evaluación de riesgos. Puedes marcar cualquier "
                 "bloque no detectado. La ausencia de una ficha o recurso no demuestra que sea innecesario.",
        ).pack(fill="x")
        if saved and not technical_review_confirmed(saved, context):
            ttk.Label(
                self, padding=10, wraplength=980,
                text="Las fuentes han cambiado desde la revisión anterior. Se conservan tus "
                     "decisiones; comprueba las propuestas actuales antes de confirmarlas de nuevo.",
            ).pack(fill="x")
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            frame, columns=("include", "block", "reason"), show="headings", selectmode="browse",
        )
        for key, label, width in (
            ("include", "Incluir", 60), ("block", "Condición técnica", 350),
            ("reason", "Origen de la propuesta / revisión", 580),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, stretch=key == "reason")
        self.tree.grid(row=0, column=0, sticky="nsew")
        for orient, command, row, column, sticky, option in (
            ("vertical", self.tree.yview, 0, 1, "ns", "yscrollcommand"),
            ("horizontal", self.tree.xview, 1, 0, "ew", "xscrollcommand"),
        ):
            scrollbar = ttk.Scrollbar(frame, orient=orient, command=command)
            scrollbar.grid(row=row, column=column, sticky=sticky)
            self.tree.configure(**{option: scrollbar.set})
        previous = saved.get("blocks", {})
        self.choices = {
            block.key: previous.get(block.key, block.key in proposals) for block in TECHNICAL_BLOCKS
        }
        for block in TECHNICAL_BLOCKS:
            self.tree.insert("", "end", iid=block.key, values=(
                "X" if self.choices[block.key] else "", block.title,
                proposals.get(block.key, "Sin propuesta: revisar e incluir manualmente si procede."),
            ))
        self.tree.bind("<ButtonRelease-1>", self._toggle)
        self.tree.bind("<space>", self._toggle)
        self.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.detail = tk.Text(self, height=6, wrap="word")
        self.detail.pack(fill="x", padx=10, pady=6)
        self._show_detail()
        actions = ttk.Frame(self, padding=10)
        actions.pack(fill="x")
        ttk.Button(actions, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(
            actions, text="Confirmar selección", command=self._accept, style="Accent.TButton",
        ).pack(side="right", padx=8)
        localize_widget_tree(self)
        self.grab_set()

    def _toggle(self, event=None):
        """Alterna únicamente la casilla pulsada o la fila activa con el teclado."""
        if event is not None and event.keysym != "space":
            key = self.tree.identify_row(event.y) if self.tree.identify_column(event.x) == "#1" else ""
        else:
            key = (self.tree.selection() or ("",))[0]
        if key in self.choices:
            self.choices[key] = not self.choices[key]
            self.tree.set(key, "include", "X" if self.choices[key] else "")
        return "break"

    def _accept(self):
        """Guarda la revisión completa con su huella de ámbito y recursos."""
        state = dict(self.app.safety_health_state) or dict(self.base_state)
        state["technical_review"] = restore_technical_review({
            "context": self.context, "blocks": self.choices,
        })
        self.app.safety_health_state = state
        self.app._mark_dirty()
        self.destroy()

    def _show_detail(self, _event=None):
        """Permite leer completas las razones y las carencias de las fuentes."""
        key = (self.tree.selection() or ("",))[0]
        reason = self.proposals.get(key, "Comprueba los equipos y procedimientos que se utilizarán realmente.")
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", "\n".join((reason, *self.pending)))
        self.detail.configure(state="disabled")
