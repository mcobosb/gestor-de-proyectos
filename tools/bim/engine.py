"""Lectura espacial, propiedades y teselación de modelos IFC."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.geom
from ifcopenshell.util.element import get_materials, get_psets
from ifcopenshell.util.shape import (
    get_faces, get_faces_material_style_ids, get_shape_material_styles,
    get_shape_matrix, get_vertices,
)
import numpy as np
from numpy.typing import NDArray


class IfcReadError(RuntimeError):
    """El fichero no contiene un proyecto que pueda abrirse como IFC."""


@dataclass(frozen=True)
class SpatialNode:
    """Nodo del árbol espacial y elementos contenidos directamente."""

    entity_id: int
    ifc_class: str
    name: str
    children: tuple[SpatialNode, ...]
    element_ids: tuple[int, ...]


@dataclass(frozen=True)
class ElementProperties:
    """Psets y cantidades heredadas, materiales asociados y fase IFC, si existen."""

    sets: dict[str, dict[str, Any]]
    materials: tuple[str, ...]
    phase: str | None


@dataclass(frozen=True)
class TriangleMesh:
    """Vértices locales en metros, índices, normales por cara y matriz 4×4."""

    vertices: NDArray[np.float64]
    faces: NDArray[np.int32]
    normals: NDArray[np.float32]
    transform: NDArray[np.float64]
    geometry_id: str = ""
    colors: NDArray[np.uint8] | None = None


@dataclass(frozen=True)
class BimModel:
    """Modelo abierto con su jerarquía y mallas indexadas por id de entidad IFC."""

    path: Path
    source: ifcopenshell.file
    spatial_tree: tuple[SpatialNode, ...]
    meshes: dict[int, TriangleMesh]
    triangle_count: int

    def properties(self, entity_id: int) -> ElementProperties:
        """Lee las propiedades sin duplicar en memoria los datos de todo el IFC."""
        entity = self.source.by_id(entity_id)
        return ElementProperties(
            get_psets(entity),
            tuple(material.Name for material in get_materials(entity) if material.Name),
            getattr(entity, "Phase", None),
        )


def _spatial_node(
    entity: ifcopenshell.entity_instance, ancestors: frozenset[int] = frozenset()
) -> SpatialNode:
    """Sigue la agregación espacial sin recorrer ciclos en archivos defectuosos."""
    entity_id = entity.id()
    ancestors = ancestors | {entity_id}
    return SpatialNode(
        entity_id,
        entity.is_a(),
        entity.Name or "",
        tuple(
            _spatial_node(child, ancestors)
            for relation in entity.IsDecomposedBy
            for child in relation.RelatedObjects
            if child.is_a("IfcSpatialStructureElement") and child.id() not in ancestors
        ),
        tuple(
            element.id()
            for relation in getattr(entity, "ContainsElements", ())
            for element in relation.RelatedElements
        ),
    )


def _face_normals(
    vertices: NDArray[np.float64], faces: NDArray[np.int32]
) -> NDArray[np.float32]:
    """Calcula normales unitarias siguiendo el orden de los índices de cada cara."""
    origins = vertices[faces[:, 0]]
    normals = np.cross(vertices[faces[:, 1]] - origins, vertices[faces[:, 2]] - origins)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    np.divide(normals, lengths, out=normals, where=lengths > 0)
    return normals.astype(np.float32)


def _face_colors(geometry) -> NDArray[np.uint8]:
    """Convierte los estilos de superficie IFC a RGB por triángulo."""
    palette = [(172, 181, 190)]
    for style in get_shape_material_styles(geometry):
        rgb = style.get_color().components
        palette.append(tuple(round(max(0.0, min(1.0, c)) * 255) for c in rgb))
    indices = get_faces_material_style_ids(geometry)
    return np.asarray(palette, dtype=np.uint8)[indices + 1]


def load_ifc(path: Path) -> BimModel:
    """Abre un IFC STEP y triangula sus representaciones con el iterador paralelo."""
    if path.suffix.casefold() != ".ifc":
        raise IfcReadError("Selecciona un archivo con extensión .ifc.")
    try:
        source = ifcopenshell.open(path, format=".ifc")
    except ifcopenshell.Error as exc:
        raise IfcReadError(f"No se pudo leer el archivo IFC: {exc}") from exc
    if not isinstance(source, ifcopenshell.file):
        raise IfcReadError("El archivo no contiene un modelo IFC STEP.")
    projects = source.by_type("IfcProject")
    if not projects:
        raise IfcReadError("El archivo no contiene ninguna entidad IfcProject.")
    spatial_tree = tuple(_spatial_node(project) for project in projects)

    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", False)
    num_threads = max(2, min(8, (os.cpu_count() or 2) - 1))
    # OpenCascade tarda varios minutos en algunos perfiles del ejemplo IFC 4.3.
    kernel = "cgal" if source.schema_identifier.startswith("IFC4X3") else "opencascade"
    meshes: dict[int, TriangleMesh] = {}
    shared: dict[str, TriangleMesh] = {}
    for shape in ifcopenshell.geom.iterator(
        settings, source, num_threads=num_threads, geometry_library=kernel
    ):
        geometry = shape.geometry
        template = shared.get(geometry.id)
        transform = get_shape_matrix(shape)
        if template is not None:
            meshes[shape.id] = TriangleMesh(
                template.vertices, template.faces, template.normals, transform,
                template.geometry_id, template.colors,
            )
            continue
        faces = get_faces(geometry)
        if len(faces):
            vertices = get_vertices(geometry)
            mesh = TriangleMesh(
                vertices, faces, _face_normals(vertices, faces), transform,
                geometry.id, _face_colors(geometry),
            )
            meshes[shape.id] = shared[geometry.id] = mesh
    triangle_count = sum(len(mesh.faces) for mesh in meshes.values())
    return BimModel(path, source, spatial_tree, meshes, triangle_count)
