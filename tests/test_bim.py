"""Pruebas del lector IFC y de su entrada en la barra principal."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

import ifcopenshell
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMainWindow, QToolBar, QToolButton, QWidget

from budget_core import Bc3Dataset, Concept
from budget_project import _item_payload, item_from_data
from tool_loader import discover_tools
from tools.bim import translation
from tools.bim.engine import IfcReadError, _isolated_meshes, load_ifc
from tools.bim.measurements import incorporate
from tools.bim.qt import BimController, _LoadIfc
from tools.bim.scene import prepare_scene
from tools.bim.viewer import BimViewer
from tools.bim.viewport import Viewport


ROOT = Path(__file__).resolve().parent.parent


def _bcca_fixture(directory: str) -> Bc3Dataset:
    """Construye una base pequeña para probar sin distribuir el catálogo completo."""
    path = Path(directory) / "BCCA2023_V02.bc3"
    path.touch()
    base = Bc3Dataset(path)
    for code, diameter in (("08CAV00095", 90), ("08CAV00096", 75)):
        base.concepts[code] = Concept(code, unit="m", summary=f"Tubería de PPR {diameter} mm")
    return base


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

    def test_bim_has_its_own_menu_between_collaboration_and_information(self) -> None:
        """Motor BIM sale de Herramientas, sin perder el acceso al selector IFC."""
        from budget_qt_window import BudgetMainWindow

        application = QApplication.instance() or QApplication([])
        with (
            TemporaryDirectory() as directory,
            patch("budget_qt_window.user_data_directory", return_value=Path(directory)),
            patch("budget_qt_window.apply_theme"),
            patch("tools.bim.qt.QFileDialog.getOpenFileName", return_value=("", "")) as selector,
        ):
            window = BudgetMainWindow()
            try:
                actions = {action.text(): action for action in window.menuBar().actions()}
                labels = list(actions)
                index = labels.index("Colaboración")
                self.assertEqual(labels[index:index + 3], ["Colaboración", "Motor BIM", "Información"])
                tool_labels = [action.text() for action in actions["Herramientas"].menu().actions()]
                self.assertNotIn("Motor BIM", tool_labels)
                toolbar, = window.findChildren(QToolBar)
                self.assertNotIn("Motor BIM", [button.text() for button in toolbar.findChildren(QToolButton)])
                bim_menu = actions["Motor BIM"].menu()
                open_ifc = next(action for action in bim_menu.actions()
                                if action.text() == "Abrir archivo IFC…")
                open_ifc.trigger()
                selector.assert_called_once()
            finally:
                window.close()
                application.processEvents()

    def test_bcca_rules_match_specific_ifc_types_and_measure_in_catalogue_units(self) -> None:
        """Comprueba códigos reales, precedencia, conversión y rechazo de ambigüedades."""
        rules = translation.read_rules(ROOT / "tools" / "bim" / "bcca_2023_v02.json")
        catalogue_path = ROOT / "tests" / "BCCA2023_V02.bc3"
        if catalogue_path.is_file():
            catalogue_base = Bc3Dataset(catalogue_path)
            for rule in rules:
                self.assertEqual(translation.unit_key(catalogue_base.concepts[rule.code].unit), rule.unit)
        with TemporaryDirectory() as directory:
            base = _bcca_fixture(directory)
        catalogue = translation.Catalogue((base,), rules)
        model = ifcopenshell.file(schema="IFC4X3_ADD2")
        pipe = model.create_entity("IfcPipeSegment", Name="PPR 90 mm")
        generic = model.create_entity("IfcPipeSegment", Name="Tubería")
        unit = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE", Prefix="CENTI")
        length = model.create_entity("IfcQuantityLength", Name="Length", Unit=unit, LengthValue=240.)
        qto = model.create_entity("IfcElementQuantity", Name="Qto_PipeSegmentBaseQuantities",
                                  Quantities=[length])
        model.create_entity("IfcRelDefinesByProperties", RelatedObjects=[pipe],
                            RelatingPropertyDefinition=qto)
        result = catalogue.propose(model, pipe)
        self.assertIsNotNone(result.entry)
        self.assertEqual(result.entry.code, "08CAV00095")
        self.assertEqual(result.quantity, 2.4)
        self.assertIn("Tabla IFC 4.3", result.reason)
        self.assertIsNone(catalogue.propose(model, generic).entry)
        pipe.Tag = "08CAV00096"
        self.assertEqual(catalogue.propose(model, pipe).entry.code, "08CAV00096")
        other = model.create_entity("IfcQuantityLength", Name="GrossLength", Unit=unit, LengthValue=270.)
        qto.Quantities = [length, other]
        self.assertIsNone(catalogue.propose(model, pipe).quantity)

    def test_bim_measurement_is_persisted_and_cannot_be_imported_twice(self) -> None:
        """Incorpora en la ventana real y verifica la persistencia del GlobalId."""
        from budget_qt_window import BudgetMainWindow
        from tools.bim.scene import Element

        application = QApplication.instance() or QApplication([])
        with (
            TemporaryDirectory() as directory,
            patch("budget_qt_window.apply_theme"),
        ):
            base = _bcca_fixture(directory)
            entry = translation.Catalogue((base,)).codes["08cav00095"][0]
            rows = [(Element(1, ifcopenshell.guid.new(), "IfcPipeSegment", "Tubería PPR"),
                     translation.Proposal(entry, quantity, "Revisado")) for quantity in (2.4, 1.6)]
            keys = {element.key for element, _ in rows}
            with patch("budget_qt_window.user_data_directory", return_value=Path(directory)):
                window = BudgetMainWindow()
            window.reference_bases = [base]
            added, errors = incorporate(window, rows)
            self.assertEqual(added, keys)
            self.assertFalse(errors)
            self.assertTrue(window.is_dirty)
            item, = window.budget_items
            restored = item_from_data(_item_payload(item))
            self.assertEqual(restored.measurement, 4.)
            self.assertEqual({line.ifc_global_id for line in restored.measurement_lines}, keys)
            window.budget_items[:] = [restored]
            added, errors = incorporate(window, rows)
            self.assertFalse(added)
            self.assertEqual(set(errors.values()), {"Ya incorporado"})
            self.assertEqual(len(restored.measurement_lines), 2)

            path = Path(directory) / "visor.ifc"
            wall_id = _write_ifc(path, "IFC4X3_ADD2")
            model = load_ifc(path)
            model.source.by_id(wall_id).Tag = entry.code
            scene = prepare_scene(model)
            with patch("tools.bim.viewer.Viewport", _HeadlessViewport):
                viewer = BimViewer(model, scene, window, project_session=window.project_session)
                self.assertIsNotNone(viewer.translation)
                viewer.state.select(scene.elements[wall_id].key)
                viewer._add_translation_selection()
                application.processEvents()
                panel = viewer.translation
                self.assertEqual(panel.selection.topLevelItemCount(), 1)
                row = panel.results.topLevelItem(0)
                self.assertEqual(row.text(1), entry.code)
                self.assertIn("sin cantidad IFC compatible", row.text(3))
                self.assertIn("1 sin resolver", panel.summary.text())
                self.assertFalse(panel.insert_button.isEnabled())
                key = scene.elements[wall_id].key
                proposal = translation.Proposal(entry, 1.5, "Revisado")
                panel._set_proposal(key, proposal)
                panel._update_summary()
                self.assertTrue(panel.insert_button.isEnabled())
                added, errors = incorporate(window, [(panel.elements[key], proposal)])
                self.assertEqual(added, {key})
                self.assertFalse(errors)
                self.assertFalse(panel.insert_button.isEnabled())
                self.assertEqual(row.text(3), "Ya incorporado")
                viewer.close()
                application.processEvents()
            window.is_dirty = False
            window.close()
            application.processEvents()

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

    def test_native_geometry_failure_is_reported_without_killing_parent(self) -> None:
        """Un fallo fatal del proceso auxiliar se convierte en error de lectura."""
        run = subprocess.run

        def crash(_command, **kwargs):
            """Simula un proceso nativo que termina sin una excepción Python."""
            return run([sys.executable, "-c", "import os; os._exit(7)"], **kwargs)

        with patch("tools.bim.engine.subprocess.run", side_effect=crash):
            with self.assertRaisesRegex(IfcReadError, "código 7"):
                _isolated_meshes(Path("modelo.ifc"))

    def test_worker_emits_geometry_failure(self) -> None:
        """El error del proceso llega al controlador para mostrarlo al usuario."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "edificio.ifc"
            _write_ifc(path)
            worker = _LoadIfc(path)
            errors = []
            worker.signals.failed.connect(errors.append)
            with patch("tools.bim.engine.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 7, b"", b"fallo de geometria"
            )):
                worker.run()
            self.assertEqual(len(errors), 1)
            self.assertIn("código 7", errors[0])
            self.assertIn("fallo de geometria", errors[0])

    def test_alignment_curves_are_reported_and_do_not_enter_triangulation(self) -> None:
        """Abre alineaciones lineales y avisa de geometrías sin producto asociado."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "alineaciones.ifc"
            source = ifcopenshell.file(schema="IFC4X3_ADD2")
            source.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), Name="Proyecto")
            point = source.create_entity("IfcCartesianPoint", Coordinates=(0., 0., 0.))
            origin = source.create_entity("IfcAxis2Placement3D", Location=point)
            context = source.create_entity(
                "IfcGeometricRepresentationContext", ContextType="Model",
                CoordinateSpaceDimension=3, Precision=1e-5, WorldCoordinateSystem=origin,
            )
            points = [source.create_entity("IfcCartesianPoint", Coordinates=coords)
                      for coords in ((0., 0., 0.), (10., 0., 0.))]
            line = source.create_entity("IfcPolyline", Points=points)
            curve = source.create_entity(
                "IfcShapeRepresentation", ContextOfItems=context, RepresentationIdentifier="Axis",
                RepresentationType="Curve3D", Items=[line],
            )
            shape = source.create_entity("IfcProductDefinitionShape", Representations=[curve])
            source.create_entity("IfcAlignmentVertical", GlobalId=ifcopenshell.guid.new(), Representation=shape)
            alignment = source.create_entity("IfcAlignment", GlobalId=ifcopenshell.guid.new(), Name="Eje")
            source.create_entity("IfcProductDefinitionShape", Representations=[curve])
            source.write(str(path))
            with patch("tools.bim.engine._isolated_meshes") as triangulate:
                model = load_ifc(path)
                triangulate.assert_not_called()
            scene = prepare_scene(model)
            self.assertIn(alignment.id(), scene.elements)
            self.assertFalse(model.meshes)
            self.assertTrue(any("curvas" in warning for warning in model.warnings))
            self.assertTrue(any("sin asociar" in warning for warning in model.warnings))

    def test_bim_loads_in_background_and_publishes_result(self) -> None:
        """Seleccionar un IFC en el desplegable no bloquea el bucle de Qt."""
        application = QApplication.instance() or QApplication([])
        window = QMainWindow()
        tools, errors = discover_tools(ROOT / "tools")
        self.assertEqual(errors, [])
        bim = next(tool for tool in tools if tool.name == "bim")
        open_bim = bim.open_qt
        assert open_bim is not None
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
                open_bim(window)
                controller = window.findChild(BimController)
                assert controller is not None
                self.assertIsNone(controller.model)
                self.assertIn("procesando", status.currentMessage())
                QTimer.singleShot(0, release_worker.set)
                QTimer.singleShot(15000, loop.quit)
                loop.exec()
                warning.assert_not_called()
            self.assertIsNotNone(controller.model)
            self.assertEqual(controller.model.path, path)
            viewer = window.findChild(BimViewer)
            self.assertTrue(viewer.isVisible())
            viewer.close()
        window.close()
        application.processEvents()

    def test_duplicate_global_ids_keep_distinct_scene_and_viewport_identities(self) -> None:
        """Los GlobalId repetidos no colisionan ni permiten vínculos ambiguos."""
        application = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as directory:
            path = Path(directory) / "duplicados.ifc"
            _write_ifc(path, repeats=7)
            source = ifcopenshell.open(path)
            walls = source.by_type("IfcWall")
            walls[1].GlobalId = walls[0].GlobalId
            source.by_type("IfcProject")[0].GlobalId = walls[2].GlobalId
            source.write(str(path))
            model = load_ifc(path)
            scene = prepare_scene(model)

        affected = [scene.elements[wall.id()] for wall in walls[:3]]
        self.assertTrue(all(element.key == f"#{element.entity_id}" for element in affected))
        self.assertEqual(len(scene.elements), len({element.key for element in scene.elements.values()}))
        self.assertEqual(len(scene.mesh_keys), len(set(scene.mesh_keys)))
        self.assertTrue(any("GlobalId" in warning and "duplicad" in warning for warning in model.warnings))
        viewport = Viewport(scene)
        try:
            viewport.set_state(frozenset({affected[0].key}), None, False, affected[1].key)
            for layer in viewport._main:
                hidden = scene.mesh_keys.index(affected[0].key)
                self.assertNotIn(hidden, layer.picked_owners)
            self.assertTrue(any(
                scene.mesh_keys.index(affected[1].key) in layer.picked_owners
                for layer in viewport._main
            ))
        finally:
            viewport.close()
            application.processEvents()
        parent = QMainWindow()
        with patch("tools.bim.viewer.Viewport", _HeadlessViewport):
            viewer = BimViewer(model, scene, parent)
            try:
                for element in affected:
                    viewer.state.select(element.key)
                    self.assertEqual(viewer.tree.currentItem().text(0), element.name)
                    self.assertEqual(
                        viewer.inspector.topLevelItem(1).text(1),
                        model.source.by_id(element.entity_id).GlobalId,
                    )
            finally:
                viewer.close()
                parent.close()
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
