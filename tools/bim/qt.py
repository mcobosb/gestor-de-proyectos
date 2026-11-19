"""Desplegable y carga de modelos BIM fuera del hilo de interfaz."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

if TYPE_CHECKING:
    from .engine import BimModel
    from .scene import Scene
    from .viewer import BimViewer


class _LoadSignals(QObject):
    """Entrega el resultado de la carga al hilo de interfaz."""

    loaded = Signal(object, object)
    failed = Signal(str)


class _LoadIfc(QRunnable):
    """Ejecuta la lectura y la geometría sin acceder a widgets."""

    def __init__(self, path: Path) -> None:
        """Prepara el trabajo y sus señales."""
        super().__init__()
        self.path = path
        self.signals = _LoadSignals()

    @Slot()
    def run(self) -> None:
        """Abre el archivo y comunica errores de dependencias o de lectura."""
        try:
            from .engine import load_ifc
            from .scene import prepare_scene
        except (ImportError, OSError) as exc:
            self.signals.failed.emit(f"No se pudo cargar el motor IFC o VTK:\n{exc}")
            return
        try:
            model = load_ifc(self.path)
            scene = prepare_scene(model)
        except (OSError, RuntimeError, ValueError) as exc:
            self.signals.failed.emit(f"No se pudo abrir {self.path.name}:\n{exc}")
        else:
            self.signals.loaded.emit(model, scene)


class BimController(QObject):
    """Gestiona el modelo IFC activo de una ventana principal."""

    def __init__(self, window: QMainWindow) -> None:
        """Vincula el controlador a la ventana que posee el modelo."""
        super().__init__(window)
        self._window = window
        self.model: BimModel | None = None
        self._viewer: BimViewer | None = None
        self._loading = False

    @Slot()
    def choose_file(self) -> None:
        """Solicita un IFC y arranca una sola carga a la vez."""
        if self._loading:
            self._window.statusBar().showMessage("Motor BIM: hay un IFC en proceso.")
            return
        initial = str(self.model.path.parent) if self.model is not None else ""
        filename, _ = QFileDialog.getOpenFileName(
            self._window, "Abrir archivo IFC", initial, "Modelos IFC (*.ifc)"
        )
        if not filename:
            return
        self._loading = True
        self._project_session = getattr(self._window, "project_session", 0)
        path = Path(filename)
        self._window.statusBar().showMessage(f"Motor BIM: procesando {path.name}…")
        worker = _LoadIfc(path)
        worker.signals.loaded.connect(self._loaded, Qt.ConnectionType.QueuedConnection)
        worker.signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _loaded(self, model: BimModel, scene: Scene) -> None:
        """Abre el visor para el IFC nuevo sin tirar el anterior si falla."""
        self._loading = False
        try:
            from .viewer import BimViewer
            viewer = BimViewer(model, scene, self._window, project_session=self._project_session)
        except (ImportError, OSError, RuntimeError) as exc:
            self._failed(f"No se pudo abrir el visor BIM:\n{exc}")
            return
        if self._viewer is not None:
            self._viewer.close()
        self.model = model
        self._viewer = viewer
        viewer.closed.connect(self._viewer_closed)
        viewer.show()
        geometry = (
            f"{len(model.meshes)} mallas, {model.triangle_count} triángulos"
            if model.meshes else "sin mallas trianguladas"
        )
        self._window.statusBar().showMessage(
            f"Motor BIM: {model.path.name} ({model.source.schema_identifier}) · {geometry}",
            12000,
        )

    @Slot()
    def _viewer_closed(self) -> None:
        """Libera la referencia al visor y sus datos al cerrarlo."""
        self._viewer = None
        self.model = None

    @Slot(str)
    def _failed(self, message: str) -> None:
        """Avisa del error sin perder el IFC cargado anteriormente."""
        self._loading = False
        self._window.statusBar().clearMessage()
        QMessageBox.warning(self._window, "Motor BIM", message)
