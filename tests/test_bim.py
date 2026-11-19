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

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QToolButton

from tool_loader import discover_tools
from tools.bim.engine import IfcReadError, load_ifc
from tools.bim.qt import BimController


ROOT = Path(__file__).resolve().parent.parent


def _write_ifc(path: Path, schema: str = "IFC4") -> int:
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
    create("IfcRelContainedInSpatialStructure", RelatingStructure=floor, RelatedElements=[wall])
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
        window.close()
        application.processEvents()
