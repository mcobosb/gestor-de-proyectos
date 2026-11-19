"""Renderizado nativo y selección de elementos IFC mediante VTK."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkDataObject, vtkPlane, vtkPolyData, vtkSelectionNode
from vtkmodules.vtkCommonTransforms import vtkTransform
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkRenderingCore import (
    vtkActor, vtkGlyph3DMapper, vtkHardwareSelector, vtkPolyDataMapper, vtkRenderer,
)
import vtkmodules.vtkRenderingOpenGL2  # Registra el backend nativo.

from .scene import Instances, MeshBatch, Scene


_HIGHLIGHT = (255, 164, 35)


def _points(vertices: np.ndarray, *, copy: bool = True) -> vtkPoints:
    """Construye puntos VTK; solo comparte buffers retenidos por la escena."""
    points = vtkPoints()
    points.SetData(numpy_to_vtk(np.ascontiguousarray(vertices), deep=copy))
    return points


def _triangles(faces: np.ndarray, *, copy: bool = True) -> vtkCellArray:
    """Construye la conectividad VTK con índices de 32 bits."""
    cells = vtkCellArray()
    cells.SetData(3, numpy_to_vtk(np.ascontiguousarray(faces.reshape(-1)), deep=copy))
    return cells


class _Layer:
    """Actor combinable o instanciado con índice de selección propio."""

    def __init__(
        self, renderer: vtkRenderer, data: MeshBatch | Instances, ghost: bool,
        original: _Layer | None = None,
    ) -> None:
        """Crea un actor y su mapper; la capa fantasma no intercepta clics."""
        self.data = data
        self.poly = vtkPolyData()
        self.picked_owners = np.empty(0, dtype=np.int32)
        self._mask: np.ndarray | None = None
        if isinstance(data, MeshBatch):
            self.poly.SetPoints(
                original.poly.GetPoints() if original is not None else _points(data.vertices, copy=False)
            )
            self.mapper = vtkPolyDataMapper()
            self.mapper.SetScalarModeToUseCellData()
        else:
            mapper = vtkGlyph3DMapper()
            if original is None:
                source = vtkPolyData()
                source.SetPoints(_points(data.mesh.vertices.astype(np.float32)))
                source.SetPolys(_triangles(data.mesh.faces, copy=False))
            else:
                assert isinstance(original.mapper, vtkGlyph3DMapper)
                source = original.mapper.GetSource(0)
            mapper.SetSourceData(source)
            mapper.ScalingOff()
            mapper.SetOrientationModeToQuaternion()
            mapper.SetOrientationArray("orientation")
            mapper.SetSelectionIdArray("ifc_index")
            mapper.UseSelectionIdsOn()
            mapper.SetScalarModeToUsePointData()
            self.mapper = mapper
        self.mapper.SetInputData(self.poly)
        self.mapper.SetColorModeToDirectScalars()
        self.actor = vtkActor()
        self.actor.SetMapper(self.mapper)
        if ghost:
            self.actor.GetProperty().SetOpacity(0.16)
            self.actor.PickableOff()
        renderer.AddActor(self.actor)

    def update(self, visible: np.ndarray, selected: int) -> None:
        """Actualiza conectividad solo al cambiar visibilidad; recolorea al seleccionar."""
        data = self.data
        mask = visible[data.owners]
        self.actor.SetVisibility(bool(mask.any()))
        if not mask.any():
            return
        if self._mask is None or not np.array_equal(mask, self._mask):
            self._mask = mask
            self.picked_owners = data.owners[mask]
            if isinstance(data, MeshBatch):
                self.poly.SetPolys(
                    _triangles(data.faces, copy=False) if mask.all() else _triangles(data.faces[mask])
                )
            else:
                self.poly.SetPoints(_points(data.positions[mask]))
                attributes = self.poly.GetPointData()
                orientation = numpy_to_vtk(data.rotations[mask], deep=True)
                orientation.SetName("orientation")
                attributes.AddArray(orientation)
                ids = numpy_to_vtk(self.picked_owners.astype(np.int64), deep=True)
                ids.SetName("ifc_index")
                attributes.AddArray(ids)
        if isinstance(data, MeshBatch):
            colors = data.colors[mask]
            attributes = self.poly.GetCellData()
        else:
            colors = np.broadcast_to(data.color, (len(self.picked_owners), 3)).copy()
            attributes = self.poly.GetPointData()
        colors[self.picked_owners == selected] = _HIGHLIGHT
        attributes.SetScalars(numpy_to_vtk(colors, deep=True))
        self.poly.Modified()

    def owner(self, selection_id: int) -> int:
        """Resuelve un id VTK: triángulo local o id de instancia explícito."""
        return (
            int(self.picked_owners[selection_id])
            if isinstance(self.data, MeshBatch) else selection_id
        )


class Viewport(QVTKRenderWindowInteractor):
    """Lienzo 3D con selección GPU, órbita y planos de corte."""

    picked = Signal(str)

    def __init__(self, scene: Scene, parent=None) -> None:
        """Configura VTK y añade un actor por lote o geometría repetida."""
        super().__init__(parent)
        self.scene = scene
        self.renderer = vtkRenderer()
        self.renderer.SetBackground(0.12, 0.15, 0.20)
        window = self.GetRenderWindow()
        window.SetMultiSamples(0)
        window.AddRenderer(self.renderer)
        window.GetInteractor().SetInteractorStyle(vtkInteractorStyleTrackballCamera())
        self._main: list[_Layer] = []
        self._ghost: list[_Layer] = []
        for data in (scene.batch, *scene.instances):
            if len(data.owners):
                main = _Layer(self.renderer, data, False)
                self._main.append(main)
                self._ghost.append(_Layer(self.renderer, data, True, original=main))
        self._keys = {key: i for i, key in enumerate(scene.mesh_keys)}
        self._selector = vtkHardwareSelector()
        self._selector.SetRenderer(self.renderer)
        self._selector.SetFieldAssociation(vtkDataObject.FIELD_ASSOCIATION_CELLS)
        self._selector.SetCaptureZValues(True)
        self._plane = vtkPlane()
        self._section_bounds: tuple[float, ...] = ()
        self._pivot: tuple[float, float, float] | None = None
        self._drag_start = self._drag_last = None
        self._dragged = False
        self._ready = False
        self._visibility(frozenset(), None, False, "")
        self._section_bounds = self.renderer.ComputeVisiblePropBounds()
        camera = self.renderer.GetActiveCamera()
        camera.SetPosition(1, -2, 1)
        camera.SetViewUp(0, 0, 1)
        self.fit(render=False)

    def start(self) -> None:
        """Inicializa OpenGL después de mostrar la ventana nativa."""
        if not self._ready:
            self.Initialize()
            self._ready = True
            self.GetRenderWindow().Render()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Impide volver a dibujar cuando Qt está destruyendo el visor."""
        self._ready = False
        super().closeEvent(event)

    def _render(self) -> None:
        """Renderiza una vez inicializada la ventana."""
        if self._ready:
            self.GetRenderWindow().Render()

    def fit(self, render: bool = True) -> None:
        """Encuadra la geometría visible y restablece el pivote inicial."""
        self.renderer.ResetCamera()
        self._pivot = None
        if render:
            self._render()

    def set_state(
        self, hidden: frozenset[str], isolated: frozenset[str] | None, ghost: bool, selected: str
    ) -> None:
        """Aplica el estado central a las capas y redibuja."""
        self._visibility(hidden, isolated, ghost, selected)
        self._render()

    def _visibility(
        self, hidden: frozenset[str], isolated: frozenset[str] | None, ghost: bool, selected: str
    ) -> None:
        """Distribuye los elementos entre la capa normal, fantasma y oculta."""
        visible = np.ones(len(self._keys), dtype=bool)
        gone = np.zeros_like(visible)
        visible[[self._keys[key] for key in hidden if key in self._keys]] = False
        if isolated is not None:
            keep = np.zeros_like(visible)
            keep[[self._keys[key] for key in isolated if key in self._keys]] = True
            if ghost:
                gone = visible & ~keep
            visible &= keep
        selected_id = self._keys.get(selected, -1)
        for layer in self._main:
            layer.update(visible, selected_id)
        for layer in self._ghost:
            layer.update(gone, -1)

    def set_section(self, axis: int, percent: int, inverted: bool) -> None:
        """Conserva el lado seleccionado de un plano X, Y o Z."""
        bounds = self._section_bounds
        enabled = axis in (0, 1, 2) and bounds[2 * axis] <= bounds[2 * axis + 1]
        if enabled:
            low, high = bounds[2 * axis:2 * axis + 2]
            origin = [0.0, 0.0, 0.0]
            origin[axis] = low + (high - low) * percent / 100
            normal = [0.0, 0.0, 0.0]
            normal[axis] = -1.0 if inverted else 1.0
            self._plane.SetOrigin(origin)
            self._plane.SetNormal(normal)
        for layer in (*self._main, *self._ghost):
            layer.mapper.RemoveAllClippingPlanes()
            if enabled:
                layer.mapper.AddClippingPlane(self._plane)
        self._render()

    def pick_display(self, x: int, y: int) -> str:
        """Devuelve el GlobalId bajo el píxel y actualiza el pivote con su profundidad."""
        width, height = self.GetRenderWindow().GetSize()
        if not (0 <= x < width and 0 <= y < height):
            return ""
        self._selector.SetArea(x, y, x, y)
        result = self._selector.Select()
        if result is None or not result.GetNumberOfNodes():
            return ""
        node = result.GetNode(0)
        props = node.GetProperties()
        selection = node.GetSelectionList()
        if selection is None or selection.GetNumberOfTuples() == 0:
            return ""
        actor = props.Get(vtkSelectionNode.PROP())
        for layer in self._main:
            if layer.actor == actor:
                if props.Has(vtkSelectionNode.ZBUFFER_VALUE()):
                    self.renderer.SetDisplayPoint(x, y, props.Get(vtkSelectionNode.ZBUFFER_VALUE()))
                    self.renderer.DisplayToWorld()
                    wx, wy, wz, w = self.renderer.GetWorldPoint()
                    if w:
                        self._pivot = (wx / w, wy / w, wz / w)
                return self.scene.mesh_keys[layer.owner(int(selection.GetTuple1(0)))]
        return ""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Reserva el botón izquierdo para distinguir clic y órbita."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = self._drag_last = event.position()
            self._dragged = False
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Orbita sin cambiar el encuadre cuando se fija un nuevo pivote."""
        if self._drag_start is None or self._drag_last is None:
            super().mouseMoveEvent(event)
            return
        now = event.position()
        if self._dragged or (now - self._drag_start).manhattanLength() >= 4:
            delta = now - self._drag_last
            self._dragged = True
            self._orbit(delta.x(), delta.y())
            self._drag_last = now

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Selecciona solo al terminar un clic, no un arrastre de cámara."""
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._drag_start is not None and not self._dragged:
            ratio = self.devicePixelRatioF()
            pos = event.position()
            x = round(pos.x() * ratio)
            y = self.GetRenderWindow().GetSize()[1] - 1 - round(pos.y() * ratio)
            self.picked.emit(self.pick_display(x, y))
        self._drag_start = self._drag_last = None

    def _orbit(self, dx: float, dy: float) -> None:
        """Rota cámara, encuadre y vertical alrededor del punto de selección."""
        camera = self.renderer.GetActiveCamera()
        pivot = self._pivot or camera.GetFocalPoint()
        up = camera.GetViewUp()
        right = np.cross(camera.GetDirectionOfProjection(), up)
        rotation = vtkTransform()
        rotation.Translate(*pivot)
        rotation.RotateWXYZ(-dx * 0.35, *up)
        rotation.RotateWXYZ(dy * 0.35, *right)
        rotation.Translate(*(-np.asarray(pivot)))
        camera.SetPosition(rotation.TransformPoint(camera.GetPosition()))
        camera.SetFocalPoint(rotation.TransformPoint(camera.GetFocalPoint()))
        camera.SetViewUp(rotation.TransformVector(up))
        self.renderer.ResetCameraClippingRange()
        self._render()
