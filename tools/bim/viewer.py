"""Ventana BIM: árbol, inspector y estado compartido con el visor."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterator

from PySide6.QtCore import QObject, QSignalBlocker, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox, QMainWindow, QSlider, QSplitter, QToolBar, QTreeWidget,
    QTreeWidgetItem,
)

from .engine import BimModel, SpatialNode
from .scene import Element, Scene
from .viewport import Viewport


_KEY = Qt.ItemDataRole.UserRole


class ViewerState(QObject):
    """Selección y visibilidad centralizadas por identificador IFC."""

    selection_changed = Signal(str)
    visibility_changed = Signal()

    def __init__(self, parent: QObject) -> None:
        """Inicia el modelo visible y sin selección."""
        super().__init__(parent)
        self.selected = ""
        self.hidden: frozenset[str] = frozenset()
        self.isolated: frozenset[str] | None = None
        self.ghost = False

    @Slot(str)
    def select(self, key: str) -> None:
        """Cambia la selección solo cuando cambia el elemento."""
        if self.selected != key:
            self.selected = key
            self.selection_changed.emit(key)

    def set_hidden(self, keys: frozenset[str]) -> None:
        """Actualiza el conjunto de elementos ocultados desde el árbol."""
        if self.hidden != keys:
            self.hidden = keys
            self.visibility_changed.emit()

    def isolate(self, keys: frozenset[str] | None) -> None:
        """Establece o elimina el grupo aislado."""
        self.isolated = keys
        self.visibility_changed.emit()

    @Slot(bool)
    def set_ghost(self, enabled: bool) -> None:
        """Activa el modo translúcido para el contexto de un aislamiento."""
        self.ghost = enabled
        self.visibility_changed.emit()


class BimViewer(QMainWindow):
    """Ventana independiente del presupuesto para interrogar un IFC cargado."""

    closed = Signal()

    def __init__(self, model: BimModel, scene: Scene, parent: QMainWindow) -> None:
        """Monta las tres vistas y conecta el estado común."""
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"Motor BIM — {model.path.name}")
        self.resize(1380, 800)
        self.model = model
        self.scene = scene
        self.state = ViewerState(self)
        self._elements = {element.key: element for element in scene.elements.values()}
        self._tree_items: dict[str, QTreeWidgetItem] = {}
        self._mesh_keys = frozenset(scene.mesh_keys)

        splitter = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Árbol del modelo")
        self.tree.setUniformRowHeights(True)
        self.viewport = Viewport(scene)
        self.inspector = QTreeWidget()
        self.inspector.setHeaderLabels(("Propiedad", "Valor"))
        self.inspector.setColumnWidth(0, 145)
        for widget in (self.tree, self.viewport, self.inspector):
            splitter.addWidget(widget)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes((290, 760, 330))
        self.setCentralWidget(splitter)
        self._populate_tree()
        self._make_toolbar()
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.timeout.connect(self._sync_checks)
        self.tree.itemChanged.connect(self._queue_checks)
        self.tree.currentItemChanged.connect(self._tree_selection)
        self.viewport.picked.connect(self.state.select)
        self.state.selection_changed.connect(self._sync_selection)
        self.state.visibility_changed.connect(self._update_viewport)
        self.statusBar().showMessage(
            "Clic: seleccionar · Arrastre izquierdo: orbitar · Botón central: desplazar · Rueda: zoom"
            if scene.mesh_keys else "El IFC no contiene mallas trianguladas."
        )

    def show(self) -> None:
        """Inicializa el contexto de render una vez visible la ventana Qt."""
        super().show()
        self.viewport.start()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Libera el contexto OpenGL antes de destruir la ventana."""
        self.tree.blockSignals(True)
        self._check_timer.stop()
        self.viewport.close()
        self.closed.emit()
        super().closeEvent(event)

    def _make_toolbar(self) -> None:
        """Añade encuadre, aislamiento y controles de corte ortogonal."""
        bar = QToolBar("Navegación BIM", self)
        self.addToolBar(bar)
        bar.addAction("Encuadrar", lambda: self.viewport.fit())
        bar.addAction("Aislar selección", self._isolate)
        bar.addAction("Mostrar todo", self._show_all)
        ghost = bar.addAction("Fantasma")
        ghost.setCheckable(True)
        ghost.setToolTip("Al aislar, muestra el resto visible de forma translúcida.")
        ghost.toggled.connect(self.state.set_ghost)
        bar.addSeparator()
        self.axis = QComboBox()
        self.axis.addItems(("Sin corte", "Corte X", "Corte Y", "Corte Z"))
        self.axis.currentIndexChanged.connect(self._update_section)
        bar.addWidget(self.axis)
        self.section = QSlider(Qt.Orientation.Horizontal)
        self.section.setRange(0, 100)
        self.section.setValue(50)
        self.section.setFixedWidth(130)
        self.section.setToolTip("Posición del plano dentro de la extensión del modelo")
        self.section.setEnabled(False)
        self.section.valueChanged.connect(self._update_section)
        bar.addWidget(self.section)
        self.invert = bar.addAction("Invertir corte")
        self.invert.setCheckable(True)
        self.invert.setEnabled(False)
        self.invert.toggled.connect(self._update_section)

    def _append_item(
        self, parent: QTreeWidgetItem, text: str, element: Element | None = None
    ) -> QTreeWidgetItem:
        """Crea una fila con casilla propagable y conserva su identidad IFC."""
        item = QTreeWidgetItem(parent, [text])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate)
        item.setCheckState(0, Qt.CheckState.Checked)
        if element is not None:
            item.setData(0, _KEY, element.key)
            self._tree_items[element.key] = item
        return item

    def _append_elements(
        self, parent: QTreeWidgetItem, ids: tuple[int, ...], placed: set[int]
    ) -> None:
        """Agrupa los elementos contenidos por clase IFC para poder aislarlos juntos."""
        groups: defaultdict[str, list[Element]] = defaultdict(list)
        for entity_id in ids:
            if entity_id not in placed:
                element = self.scene.elements[entity_id]
                groups[element.ifc_class].append(element)
                placed.add(entity_id)
        for name, elements in sorted(groups.items()):
            group = self._append_item(parent, f"{name} ({len(elements)})")
            for element in sorted(elements, key=lambda value: value.name.casefold()):
                self._append_item(group, element.name, element)

    def _append_spatial(
        self, parent: QTreeWidgetItem, node: SpatialNode, placed: set[int]
    ) -> None:
        """Crea la rama espacial del proyecto, solar, edificios y plantas."""
        if node.entity_id in placed:
            return
        element = self.scene.elements[node.entity_id]
        placed.add(node.entity_id)
        item = self._append_item(parent, f"{element.name} · {element.ifc_class}", element)
        for child in node.children:
            self._append_spatial(item, child, placed)
        self._append_elements(item, node.element_ids, placed)

    def _populate_tree(self) -> None:
        """Construye la jerarquía y reserva una rama para elementos no contenidos."""
        root = self.tree.invisibleRootItem()
        placed: set[int] = set()
        for node in self.model.spatial_tree:
            self._append_spatial(root, node, placed)
        remaining = self.scene.elements.keys() - placed
        if remaining:
            other = self._append_item(root, "Otros elementos")
            self._append_elements(other, tuple(remaining), placed)
        self.tree.expandToDepth(1)

    @staticmethod
    def _walk(item: QTreeWidgetItem) -> Iterator[QTreeWidgetItem]:
        """Itera por una rama sin depender de si está desplegada."""
        stack = [item]
        while stack:
            current = stack.pop()
            yield current
            stack.extend(current.child(i) for i in range(current.childCount()))

    def _queue_checks(self, _item: QTreeWidgetItem, _column: int) -> None:
        """Agrupa el aluvión de señales de una casilla de planta o proyecto."""
        if not self._check_timer.isActive():
            self._check_timer.start(0)

    @Slot()
    def _sync_checks(self) -> None:
        """Traduce las casillas desmarcadas al conjunto central de ocultos."""
        self.state.set_hidden(frozenset(
            key for key in self._mesh_keys
            if self._tree_items[key].checkState(0) == Qt.CheckState.Unchecked
        ))

    def _tree_selection(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        """Propaga al visor y al inspector la selección hecha en el árbol."""
        key = current.data(0, _KEY) if current is not None else ""
        self.state.select(key or "")

    @Slot(str)
    def _sync_selection(self, key: str) -> None:
        """Sincroniza árbol e inspector sin realimentar su señal de selección."""
        item = self._tree_items.get(key)
        if item is not None or not key:
            current = self.tree.currentItem()
            if item is not None or (current is not None and current.data(0, _KEY)):
                with QSignalBlocker(self.tree):
                    self.tree.setCurrentItem(item)
                if item is not None:
                    self.tree.scrollToItem(item)
        self._inspect(key)
        self._update_viewport()

    def _inspect(self, key: str) -> None:
        """Muestra la identidad, materiales y conjuntos de propiedades o cantidades IFC."""
        self.inspector.clear()
        element = self._elements.get(key)
        if element is None:
            return
        props = self.model.properties(element.entity_id)
        for label, value in (
            ("Nombre", element.name), ("GlobalId", key if not key.startswith("#") else "—"),
            ("Tipo IFC", element.ifc_class), ("Id STEP", str(element.entity_id)),
            ("Materiales", ", ".join(props.materials) or "—"), ("Fase", props.phase or "—"),
        ):
            QTreeWidgetItem(self.inspector, [label, value]).setToolTip(1, value)
        for name, values in sorted(props.sets.items()):
            item = QTreeWidgetItem(self.inspector, [name])
            for label, value in sorted(values.items()):
                if label != "id":
                    text = "—" if value is None else str(value)
                    QTreeWidgetItem(item, [label, text]).setToolTip(1, text)

    @Slot()
    def _isolate(self) -> None:
        """Aísla el elemento o rama seleccionados en el árbol."""
        item = self.tree.currentItem()
        keys = frozenset(
            key for row in self._walk(item)
            if (key := row.data(0, _KEY)) in self._mesh_keys
        ) if item is not None else frozenset()
        if not keys:
            self.statusBar().showMessage("Selecciona una rama o elemento con geometría.", 4000)
            return
        with QSignalBlocker(self.tree):
            item.setCheckState(0, Qt.CheckState.Checked)
        self._sync_checks()
        self.state.isolate(keys)

    @Slot()
    def _show_all(self) -> None:
        """Restablece las casillas y elimina el aislamiento."""
        with QSignalBlocker(self.tree):
            for i in range(self.tree.topLevelItemCount()):
                self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked)
        self.state.set_hidden(frozenset())
        self.state.isolate(None)

    @Slot()
    def _update_viewport(self) -> None:
        """Aplica al lienzo el estado compartido."""
        self.viewport.set_state(
            self.state.hidden, self.state.isolated, self.state.ghost, self.state.selected
        )

    @Slot()
    def _update_section(self) -> None:
        """Actualiza el plano interactivo y sus controles asociados."""
        axis = self.axis.currentIndex() - 1
        self.section.setEnabled(axis >= 0)
        self.invert.setEnabled(axis >= 0)
        self.viewport.set_section(axis, self.section.value(), self.invert.isChecked())
