"""Revisión de equivalencias y mediciones dentro del visor BIM."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, replace
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QInputDialog, QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .measurements import existing_links, incorporate
from budget_project import atomic_json_write
from .translation import Catalogue, Proposal, Rule, measure, read_rules

if TYPE_CHECKING:
    from budget_qt_window import BudgetMainWindow
    from .engine import BimModel
    from .scene import Element


_KEY = Qt.ItemDataRole.UserRole
_BUILTIN_RULES = Path(__file__).with_name("bcca_2023_v02.json")


class TranslationPanel(QWidget):
    """Mantiene la lista de medición sincronizada con sus propuestas."""

    selected = Signal(str)
    add_requested = Signal()

    def __init__(
        self, app: BudgetMainWindow, model: BimModel, elements: dict[str, Element],
        project_session: int,
    ) -> None:
        """Crea los paneles de selección y traducción, sin escribir aún al presupuesto."""
        super().__init__()
        self.app, self.model, self.elements = app, model, elements
        self.project_session = project_session
        self._user_rules = app.recent_reference_bases_path.with_name("bim_equivalencias.json")
        self._catalogue = Catalogue(())
        self._catalogue_loaded = False
        self._pending: deque[str] = deque()
        self._rows: dict[str, QTreeWidgetItem] = {}
        self._proposals: dict[str, Proposal] = {}
        self._linked = existing_links(app)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._translate_next)

        self.selection_panel = QWidget()
        left = QVBoxLayout(self.selection_panel)
        self.selection = QTreeWidget()
        self.selection.setHeaderLabel("Elementos seleccionados para medir")
        self.selection.setUniformRowHeights(True)
        self.selection.itemActivated.connect(self._activate)
        left.addWidget(self.selection)
        self.add = QPushButton("Añadir selección o rama")
        self.add.clicked.connect(self.add_requested.emit)
        left.addWidget(self.add)
        remove = QPushButton("Quitar de la lista")
        remove.clicked.connect(self._remove)
        left.addWidget(remove)

        layout = QVBoxLayout(self)
        self.summary = QLabel("Añade elementos del árbol o del visor para traducirlos.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.results = QTreeWidget()
        self.results.setHeaderLabels(("Elemento", "Partida BCCA", "Medición", "Estado"))
        self.results.setUniformRowHeights(True)
        self.results.setColumnWidth(0, 130)
        self.results.setColumnWidth(1, 120)
        self.results.setColumnWidth(2, 75)
        self.results.itemActivated.connect(self._activate)
        layout.addWidget(self.results)
        for label, callback in (
            ("Recalcular con las bases cargadas", self._recalculate),
            ("Ver tabla IFC 4.3 → BCCA", self._show_rules),
            ("Elegir partida por código…", self._choose_entry),
            ("Guardar regla para este tipo IFC…", self._save_rule),
            ("Introducir cantidad…", self._choose_quantity),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            layout.addWidget(button)
        self.insert_button = QPushButton("Incorporar mediciones al presupuesto")
        self.insert_button.clicked.connect(self._apply)
        layout.addWidget(self.insert_button)
        self.app.budget_refreshed.connect(self._refresh_budget_links)
        self._update_summary()

    def _refresh_budget_links(self) -> None:
        """Actualiza las filas cuando se añaden o borran mediciones en el presupuesto."""
        linked = existing_links(self.app)
        if linked != self._linked:
            self._linked = linked
            for key, proposal in self._proposals.items():
                self._set_proposal(key, proposal)
        self._update_summary()

    def add_elements(self, keys: list[str]) -> None:
        """Añade elementos de obra con GlobalId y traduce por tandas."""
        bases = tuple(self.app.reference_bases)
        if (not self._catalogue_loaded or bases != self._catalogue.bases) and not self._recalculate():
            return
        for key in dict.fromkeys(keys):
            element = self.elements.get(key)
            if element is None or key in self._rows or key.startswith("#"):
                continue
            row = QTreeWidgetItem(self.selection, [element.name])
            row.setData(0, _KEY, key)
            row.setToolTip(0, key)
            result = QTreeWidgetItem(self.results, [element.name, "", "", "Pendiente"])
            result.setData(0, _KEY, key)
            result.setToolTip(0, key)
            self._rows[key] = result
            self._pending.append(key)
        if self._pending:
            self._timer.start(0)
        self._update_summary()

    def highlight(self, key: str) -> None:
        """Alinea los resultados con la selección del árbol o del visor."""
        if key in self._rows:
            self.results.setCurrentItem(self._rows[key])
            self.results.scrollToItem(self._rows[key])

    def _activate(self, row: QTreeWidgetItem, _column: int) -> None:
        """Selecciona en el visor el elemento activado en una lista."""
        self.selected.emit(row.data(0, _KEY))

    def _remove(self) -> None:
        """Retira de la revisión sin borrar mediciones ya incorporadas."""
        item = self.selection.currentItem()
        if item is not None:
            key = item.data(0, _KEY)
            self.selection.takeTopLevelItem(self.selection.indexOfTopLevelItem(item))
            self.results.takeTopLevelItem(self.results.indexOfTopLevelItem(self._rows.pop(key)))
            self._proposals.pop(key, None)
            self._update_summary()

    def _read_rule_tables(self) -> tuple[Rule, ...]:
        """Lee reglas iniciales y locales; falla si una tabla existente no es válida."""
        return read_rules(_BUILTIN_RULES) + (
            read_rules(self._user_rules) if self._user_rules.exists() else ()
        )

    def _recalculate(self) -> bool:
        """Rehace propuestas usando las bases actuales, incluidas las recién cargadas."""
        try:
            rules = self._read_rule_tables()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Tabla IFC 4.3 → BCCA", str(exc))
            return False
        self._catalogue = Catalogue(tuple(self.app.reference_bases), rules)
        self._catalogue_loaded = True
        self._linked = existing_links(self.app)
        self._proposals.clear()
        self._pending = deque(self._rows)
        for row in self._rows.values():
            row.setText(1, "")
            row.setText(2, "")
            row.setText(3, "Pendiente")
        if self._pending:
            self._timer.start(0)
        self._update_summary()
        return True

    def _show_rules(self) -> None:
        """Consulta las condiciones y el código de cada fila de la tabla."""
        try:
            rules = self._read_rule_tables()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Tabla IFC 4.3 → BCCA", str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Tabla IFC 4.3 → BCCA")
        dialog.resize(970, 400)
        layout = QVBoxLayout(dialog)
        table = QTreeWidget()
        table.setRootIsDecorated(False)
        table.setHeaderLabels(("Clase IFC 4.3", "Predefinido", "Tipo o condiciones", "Base", "Código", "Unidad"))
        for rule in rules:
            QTreeWidgetItem(table, [
                rule.ifc_class, rule.predefined, rule.type_name or ", ".join(rule.contains),
                rule.base, rule.code, rule.unit,
            ])
        for column in range(table.columnCount()):
            table.resizeColumnToContents(column)
        layout.addWidget(table)
        dialog.exec()

    def _save_rule(self) -> None:
        """Guarda una partida revisada para el nombre exacto de un tipo IFC 4.3."""
        from ifcopenshell.util.element import get_predefined_type, get_type

        key = self._current_key()
        if not key:
            return
        proposal = self._proposals.get(key)
        if proposal is None or proposal.entry is None or proposal.entry.base not in self.app.reference_bases:
            QMessageBox.information(self, "Motor BIM", "Elige antes una partida de una base cargada.")
            return
        if not self.model.source.schema_identifier.startswith("IFC4X3"):
            QMessageBox.information(self, "Motor BIM", "La tabla de reglas es para IFC 4.3.")
            return
        entity = self._entity(key)
        type_name = getattr(get_type(entity), "Name", None)
        if not type_name or not type_name.strip() or not proposal.entry.unit.strip():
            QMessageBox.information(self, "Motor BIM", "Se necesita un tipo IFC con nombre y una unidad BCCA.")
            return
        entry = proposal.entry
        rule = Rule(
            entity.is_a(), entry.base.path.stem, entry.code, entry.unit,
            predefined=get_predefined_type(entity) or "", type_name=type_name.strip(),
        )
        answer = QMessageBox.question(
            self, "Guardar equivalencia",
            f"¿Usar {entry.label} para {rule.ifc_class} de tipo «{rule.type_name}»?\n"
            "La regla se reutilizará con otros modelos IFC 4.3; no guarda la cantidad.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            rules = read_rules(self._user_rules) if self._user_rules.exists() else ()
            rules = tuple(
                previous for previous in rules if (
                    previous.ifc_class, previous.predefined.casefold(), previous.type_name.casefold()
                ) != (rule.ifc_class, rule.predefined.casefold(), rule.type_name.casefold())
            )
            atomic_json_write(self._user_rules, [asdict(item) for item in (*rules, rule)])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "No se pudo guardar la regla", str(exc))
            return
        self._recalculate()

    def _entity(self, key: str):
        """Recupera la entidad nativa a partir del GlobalId mostrado."""
        return self.model.source.by_id(self.elements[key].entity_id)

    def _translate_next(self) -> None:
        """Procesa un bloque pequeño sin compartir el modelo IFC entre hilos."""
        for _ in range(min(25, len(self._pending))):
            key = self._pending.popleft()
            if key not in self._rows or key in self._proposals:
                continue
            proposal = self._catalogue.propose(self.model.source, self._entity(key))
            self._set_proposal(key, proposal)
        if not self._pending:
            self._timer.stop()
        self._update_summary()

    def _set_proposal(self, key: str, proposal: Proposal) -> None:
        """Muestra el candidato, la cantidad y el motivo, sin ocultar fallos."""
        self._proposals[key] = proposal
        row = self._rows[key]
        entry = proposal.entry
        row.setText(1, entry.code if entry else "—")
        row.setToolTip(1, entry.label if entry else "")
        row.setText(2, f"{proposal.quantity:g} {entry.unit}"
                    if entry and proposal.quantity is not None else "—")
        status = "Ya incorporado" if key in self._linked else proposal.reason
        row.setText(3, status)
        row.setToolTip(3, status)

    def _update_summary(self) -> None:
        """Distingue las propuestas utilizables de las pendientes y sin resolver."""
        ready = len(self._ready_rows())
        linked = len(self._rows.keys() & self._linked)
        processing = len(self._rows.keys() - self._proposals.keys() - self._linked)
        unresolved = len(self._rows) - ready - linked - processing
        self.summary.setText(
            f"{ready} listas · {unresolved} sin resolver · "
            f"{processing} calculando · {linked} incorporadas"
        )
        self.insert_button.setEnabled(bool(ready) and self.app.project_session == self.project_session)
        self.insert_button.setToolTip("No hay mediciones nuevas listas para incorporar." if not ready else "")

    def _ready_rows(self) -> list[tuple[Element, Proposal]]:
        """Devuelve propuestas válidas que todavía no tienen una medición en el presupuesto."""
        return [(self.elements[key], proposal) for key, proposal in self._proposals.items()
                if key not in self._linked and proposal.entry is not None
                and proposal.quantity is not None and isfinite(proposal.quantity) and proposal.quantity >= 0.01
                and any(base is proposal.entry.base for base in self.app.reference_bases)]

    def _current_key(self) -> str:
        """Obtiene el resultado seleccionado o avisa de que falta seleccionarlo."""
        row = self.results.currentItem()
        if row is None:
            QMessageBox.information(self, "Motor BIM", "Selecciona un resultado de traducción.")
            return ""
        key = row.data(0, _KEY)
        if key in existing_links(self.app):
            QMessageBox.information(self, "Motor BIM", "Edita esta medición desde el presupuesto.")
            return ""
        return key

    def _choose_entry(self) -> None:
        """Asocia manualmente un resultado a un código exacto de las bases cargadas."""
        key = self._current_key()
        if not key:
            return
        if (tuple(self.app.reference_bases) != self._catalogue.bases) and not self._recalculate():
            return
        code, ok = QInputDialog.getText(self, "Partida BCCA", "Código exacto de la partida:")
        if not ok:
            return
        entries = self._catalogue.codes.get(code.strip().casefold(), [])
        if not entries:
            QMessageBox.warning(self, "Motor BIM", "El código no está en las bases cargadas.")
            return
        index = 0
        if len(entries) > 1:
            labels = [str(entry.base.path) for entry in entries]
            label, ok = QInputDialog.getItem(self, "Partida BCCA", "Base:", labels, 0, False)
            if not ok:
                return
            index = labels.index(label)
        self._set_proposal(key, measure(
            self.model.source, self._entity(key), entries[index], "Partida elegida",
        ))
        self._update_summary()

    def _choose_quantity(self) -> None:
        """Permite resolver una cantidad ausente o ambigua en la unidad de destino."""
        key = self._current_key()
        if not key:
            return
        proposal = self._proposals.get(key)
        if proposal is None or proposal.entry is None:
            QMessageBox.information(self, "Motor BIM", "Elige antes una partida BCCA.")
            return
        quantity, ok = QInputDialog.getDouble(
            self, "Cantidad IFC", f"Medición en {proposal.entry.unit}:",
            proposal.quantity or 1.0, 0.01, 1_000_000_000, 2,
        )
        if ok:
            self._set_proposal(key, replace(proposal, quantity=quantity, reason="Cantidad revisada"))
            self._update_summary()

    def _apply(self) -> None:
        """Confirma y añade únicamente las propuestas completas al proyecto de origen."""
        if self.app.project_session != self.project_session:
            QMessageBox.warning(self, "Motor BIM", "El presupuesto cambió. Vuelve a abrir el IFC.")
            return
        self._linked = existing_links(self.app)
        rows = self._ready_rows()
        if not rows:
            self._update_summary()
            QMessageBox.information(self, "Motor BIM", "No hay mediciones nuevas listas para incorporar.")
            return
        answer = QMessageBox.question(
            self, "Motor BIM",
            f"¿Incorporar {len(rows)} mediciones al presupuesto "
            f"«{self.app._project_title()}»?\nLas pendientes no se incorporarán.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.app.project_session != self.project_session:
            QMessageBox.warning(self, "Motor BIM", "El presupuesto cambió. Vuelve a abrir el IFC.")
            return
        added, errors = incorporate(self.app, rows)
        self._linked = existing_links(self.app)
        for key, proposal in self._proposals.items():
            self._set_proposal(key, proposal)
        for key, message in errors.items():
            self._rows[key].setText(3, message)
            self._rows[key].setToolTip(3, message)
        self._update_summary()
        QMessageBox.information(
            self, "Motor BIM",
            f"{len(added)} mediciones incorporadas. {len(errors)} no incorporadas.\n"
            "Usa Guardar en el presupuesto para conservar los cambios.",
        )
