"""Pruebas del lector IFC y de su entrada en la barra principal."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

import ifcopenshell
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QToolButton, QWidget

from tool_loader import discover_tools
from tools.bim.engine import IfcReadError, load_ifc
from tools.bim.qt import BimController
from tools.bim.scene import prepare_scene
from tools.bim.viewer import BimViewer


ROOT = Path(__file__).resolve().parent.parent


def _write_ifc(path: Path, schema: str = "IFC4", repeats: int = 0) -> int:
    """Escribe un IFC mínimo con una pared, un Pset y una cara 3D."""
    model = ifcopenshell.file(schema=schema)

    def create(ifc_class, **attributes):
        """Crea una entidad raíz con un GUID válido."""
        return model.create_entity(ifc_class, GlobalId=ifcopenshell.guid.new(), **attributes)

    point = model.create_entity("IfcCartesianPoint", Coordinates=(0., 0., 0.))
    origin = model.create_entity("IfcAxis2Placement3D", Location=point)
    context = model.create_entity(
        "IfcGeometricRepresentationContext", ContextType="Model",
        CoordinateSpaceDimension=3, Precision=1e-5, WorldCoordinateSystem=origin,
    )
    metre = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    project = create(
        "IfcProject", Name="Prueba", Phase="Proyecto básico",
        RepresentationContexts=[context],
        UnitsInContext=model.create_entity("IfcUnitAssignment", Units=[metre]),
    )
    site = create("IfcSite", Name="Solar")
    building = create("IfcBuilding", Name="Edificio")
    floor = create("IfcBuildingStorey", Name="Planta baja")
    for parent, child in ((project, site), (site, building), (building, floor)):
        create("IfcRelAggregates", RelatingObject=parent, RelatedObjects=[child])

    points = model.create_entity(
        "IfcCartesianPointList3D", CoordList=((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))
    )
    triangles = model.create_entity(
        "IfcTriangulatedFaceSet", Coordinates=points, Closed=False, CoordIndex=((1, 2, 3),)
    )
    body = model.create_entity(
        "IfcShapeRepresentation", ContextOfItems=context, RepresentationIdentifier="Body",
        RepresentationType="Tessellation", Items=[triangles],
    )
    wall = create(
        "IfcWall", Name="Pared de prueba",
        Representation=model.create_entity("IfcProductDefinitionShape", Representations=[body]),
    )
    walls = [wall]
    for i in range(repeats):
        location = model.create_entity("IfcCartesianPoint", Coordinates=(float(2 + 2 * i), 0., 0.))
        axes = model.create_entity("IfcAxis2Placement3D", Location=location)
        placement = model.create_entity("IfcLocalPlacement", RelativePlacement=axes)
        walls.append(create(
            "IfcWall", Name=f"Pared {i}", ObjectPlacement=placement,
            Representation=wall.Representation,
        ))
    if repeats:
        location = model.create_entity("IfcCartesianPoint", Coordinates=(0., 2., 0.))
        axes = model.create_entity("IfcAxis2Placement3D", Location=location)
        placement = model.create_entity("IfcLocalPlacement", RelativePlacement=axes)
        unique_body = model.create_entity(
            "IfcShapeRepresentation", ContextOfItems=context, RepresentationIdentifier="Body",
            RepresentationType="Tessellation", Items=[triangles],
        )
        walls.append(create(
            "IfcWall", Name="Pared única", ObjectPlacement=placement,
            Representation=model.create_entity("IfcProductDefinitionShape", Representations=[unique_body]),
        ))
    create("IfcRelContainedInSpatialStructure", RelatingStructure=floor, RelatedElements=walls)
    prop = model.create_entity(
        "IfcPropertySingleValue", Name="FireRating",
        NominalValue=model.create_entity("IfcLabel", "EI60"),
    )
    pset = create("IfcPropertySet", Name="Pset_WallCommon", HasProperties=[prop])
    create("IfcRelDefinesByProperties", RelatingPropertyDefinition=pset, RelatedObjects=[wall])
    material = model.create_entity("IfcMaterial", Name="Hormigón")
    create("IfcRelAssociatesMaterial", RelatingMaterial=material, RelatedObjects=[wall])
    model.write(str(path))
    return wall.id()


class _HeadlessViewport(QWidget):
    """Sustituye la superficie nativa para probar el controlador sin OpenGL."""

    picked = Signal(str)

    def __init__(self, _scene) -> None:
        """Crea un widget Qt sin intentar conseguir un HWND."""
        super().__init__()

    def start(self) -> None:
        """No inicia renderizado en la plataforma de pruebas sin pantalla."""

    def set_state(self, *_state) -> None:
        """Acepta notificaciones del estado sin renderizar."""


class BimTests(unittest.TestCase):
    """Comprueba datos IFC reales y la entrega del resultado a Qt."""

    def test_reads_spatial_structure_properties_and_triangle(self) -> None:
        """Recupera el elemento en su planta con propiedades y geometría válidas."""
        for schema in ("IFC4", "IFC4X3_ADD2"):
            with self.subTest(schema=schema):
                self._check_ifc_model(schema)

    def _check_ifc_model(self, schema: str) -> None:
        """Comprueba un modelo en el esquema indicado."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "edificio.ifc"
            wall_id = _write_ifc(path, schema)
            model = load_ifc(path)

        project, = model.spatial_tree
        site, = project.children
        building, = site.children
        floor, = building.children
        self.assertEqual(
            [node.ifc_class for node in (project, site, building, floor)],
            ["IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey"],
        )
        self.assertEqual(floor.element_ids, (wall_id,))
        self.assertEqual(model.properties(project.entity_id).phase, "Proyecto básico")
        props = model.properties(wall_id)
        self.assertEqual(props.sets["Pset_WallCommon"]["FireRating"], "EI60")
        self.assertEqual(props.materials, ("Hormigón",))
        self.assertEqual(model.triangle_count, 1)
        mesh = model.meshes[wall_id]
        self.assertEqual({tuple(vertex) for vertex in mesh.vertices[mesh.faces[0]]},
                         {(0., 0., 0.), (0., 1., 0.), (1., 0., 0.)})
        np.testing.assert_array_equal(mesh.normals, [[0, 0, 1]])
        np.testing.assert_array_equal(mesh.transform, np.eye(4))

    def test_invalid_file_is_rejected(self) -> None:
        """No confunde un fichero con extensión IFC con un modelo válido."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invalido.ifc"
            path.write_text("Archivo sin modelo IFC", encoding="utf-8")
            with self.assertRaises(IfcReadError):
                load_ifc(path)

    def test_toolbar_loads_in_background_and_publishes_result(self) -> None:
        """Seleccionar un IFC en el desplegable no bloquea el bucle de Qt."""
        application = QApplication.instance() or QApplication([])
        window = QMainWindow()
        toolbar = window.addToolBar("Presupuesto")
        tools, errors = discover_tools(ROOT / "tools")
        self.assertEqual(errors, [])
        bim = next(tool for tool in tools if tool.name == "bim")
        self.assertIsNotNone(bim.install_toolbar_qt)
        bim.install_toolbar_qt(window, toolbar)
        button = next(button for button in toolbar.findChildren(QToolButton)
                      if button.text() == "Motor BIM")
        menu = button.menu()
        self.assertIsNotNone(menu)
        action = menu.actions()[0]
        self.assertEqual(action.text(), "Abrir archivo IFC…")
        controller = window.findChild(BimController)
        self.assertIsNotNone(controller)
        release_worker = Event()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "edificio.ifc"
            _write_ifc(path)

            def deferred_load(selected: Path):
                """Espera a que Qt atienda un temporizador antes de leer el modelo."""
                if not release_worker.wait(3):
                    raise RuntimeError("Qt no procesó el temporizador durante la carga.")
                return load_ifc(selected)

            loop = QEventLoop()
            status = window.statusBar()
            status.messageChanged.connect(
                lambda text: loop.quit() if "1 mallas" in text else None
            )
            with (
                patch("tools.bim.qt.QFileDialog.getOpenFileName", return_value=(str(path), "")),
                patch("tools.bim.engine.load_ifc", side_effect=deferred_load),
                patch("tools.bim.qt.QMessageBox.warning") as warning,
                patch("tools.bim.viewer.Viewport", _HeadlessViewport),
            ):
                action.trigger()
                self.assertIsNone(controller.model)
                self.assertIn("procesando", status.currentMessage())
                QTimer.singleShot(0, release_worker.set)
                QTimer.singleShot(5000, loop.quit)
                loop.exec()
                warning.assert_not_called()
            self.assertIsNotNone(controller.model)
            self.assertEqual(controller.model.path, path)
            viewer = window.findChild(BimViewer)
            self.assertTrue(viewer.isVisible())
            viewer.close()
        window.close()
        application.processEvents()

    def test_native_picking_visibility_and_sections_share_ifc_identity(self) -> None:
        """Selecciona una instancia real, consulta sus datos y recorta sin picks invisibles."""
        application = QApplication.instance() or QApplication([])
        if application.platformName() in {"offscreen", "minimal"}:
            self.skipTest("El picking GPU necesita una superficie Qt nativa.")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "instancias.ifc"
            wall_id = _write_ifc(path, repeats=7)
            model = load_ifc(path)
            scene = prepare_scene(model)
        self.assertEqual(len(scene.instances), 1)
        self.assertEqual(len(scene.instances[0].owners), 8)
        self.assertEqual(len(scene.batch.faces), 1)
        key = scene.elements[wall_id].key
        parent = QMainWindow()
        viewer = BimViewer(model, scene, parent)
        self.addCleanup(parent.close)
        self.addCleanup(viewer.close)
        viewer.show()
        application.processEvents()
        viewport = viewer.viewport
        camera = viewport.renderer.GetActiveCamera()
        camera.SetPosition(0, 0, 20)
        camera.SetFocalPoint(0, 0, 0)
        camera.SetViewUp(0, 1, 0)
        viewport.fit()
        group = scene.instances[0]
        index = scene.mesh_keys.index(key)
        position = group.positions[np.flatnonzero(group.owners == index)[0]]
        def display(point):
            """Proyecta un punto conocido para probar el picking por píxel."""
            viewport.renderer.SetWorldPoint(*point, 1.0)
            viewport.renderer.WorldToDisplay()
            x, y, _ = viewport.renderer.GetDisplayPoint()
            return round(x), round(y)

        unique_key = scene.mesh_keys[scene.batch.owners[0]]
        unique_pixel = display(scene.batch.vertices[scene.batch.faces[0]].mean(axis=0))
        self.assertEqual(viewport.pick_display(*unique_pixel), unique_key)
        pixel = display(position + (0.25, 0.25, 0.0))
        self.assertEqual(viewport.pick_display(*pixel), key)
        self.assertIsNotNone(viewport._pivot)
        viewport.picked.emit(key)
        self.assertEqual(viewer.tree.currentItem().text(0), "Pared de prueba")
        self.assertEqual(viewer.inspector.topLevelItem(1).text(1), key)
        self.assertEqual(viewer.inspector.topLevelItem(4).text(1), "Hormigón")

        viewer.tree.currentItem().setCheckState(0, Qt.CheckState.Unchecked)
        application.processEvents()
        self.assertIn(key, viewer.state.hidden)
        self.assertEqual(viewport.pick_display(*pixel), "")
        viewer._isolate()
        viewer.state.set_ghost(True)
        self.assertTrue(viewport._ghost[0].actor.GetVisibility())
        self.assertEqual(viewport.pick_display(*unique_pixel), "")
        self.assertEqual(viewport.pick_display(*pixel), key)
        viewer.axis.setCurrentIndex(1)
        self.assertEqual(viewport.pick_display(*pixel), "")
        viewer.invert.setChecked(True)
        self.assertEqual(viewport.pick_display(*pixel), key)
