"""Preparación NumPy del visor, sin widgets ni contexto de renderizado."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from vtkmodules.vtkCommonCore import vtkMath

from .engine import BimModel, TriangleMesh


DEFAULT_RGB = (172, 181, 190)


@dataclass(frozen=True)
class MeshBatch:
    """Malla combinada con RGB e índice de elemento por triángulo."""

    vertices: NDArray[np.float32]
    faces: NDArray[np.int32]
    colors: NDArray[np.uint8]
    owners: NDArray[np.int32]


@dataclass(frozen=True)
class Instances:
    """Una triangulación compartida por varias colocaciones rígidas."""

    mesh: TriangleMesh
    owners: NDArray[np.int32]
    positions: NDArray[np.float32]
    rotations: NDArray[np.float32]
    color: NDArray[np.uint8]


@dataclass(frozen=True)
class Element:
    """Identidad IFC que comparten el árbol, el inspector y el visor."""

    entity_id: int
    key: str
    ifc_class: str
    name: str


@dataclass(frozen=True)
class Scene:
    """Buffers y catálogo de elementos que el trabajador entrega a Qt."""

    elements: dict[int, Element]
    mesh_keys: tuple[str, ...]
    batch: MeshBatch
    instances: tuple[Instances, ...]


def _quaternion(rotation: NDArray[np.float64]) -> list[float]:
    """Convierte la rotación rígida al orden WXYZ que usa VTK."""
    value = [0.0] * 4
    vtkMath.Matrix3x3ToQuaternion(rotation.tolist(), value)
    return value


def prepare_scene(model: BimModel) -> Scene:
    """Desplaza el origen y prepara mallas combinadas e instancias de GPU."""
    ids = set(model.meshes) | {entity.id() for entity in model.source.by_type("IfcElement")}
    if model.source.schema_identifier.startswith("IFC4X3"):
        ids.update(entity.id() for entity in model.source.by_type("IfcAlignment"))
    nodes = list(model.spatial_tree)
    while nodes:
        node = nodes.pop()
        ids.add(node.entity_id)
        ids.update(node.element_ids)
        nodes.extend(node.children)
    elements = {}
    for entity_id in ids:
        entity = model.source.by_id(entity_id)
        elements[entity_id] = Element(
            entity_id, getattr(entity, "GlobalId", None) or f"#{entity_id}",
            entity.is_a(), getattr(entity, "Name", None) or entity.is_a(),
        )

    meshes = list(model.meshes.items())
    transforms = np.asarray([mesh.transform for _, mesh in meshes]).reshape(-1, 4, 4)
    translations = transforms[:, :3, 3]
    origin = np.median(translations, axis=0) if meshes else np.zeros(3)
    rotations = transforms[:, :3, :3]
    rigid = np.all(
        np.isclose(rotations @ rotations.transpose(0, 2, 1), np.eye(3)),
        axis=(1, 2),
    ) & np.isclose(np.linalg.det(rotations), 1)

    groups: dict[str, list[int]] = defaultdict(list)
    for index, (_, mesh) in enumerate(meshes):
        if mesh.geometry_id and rigid[index]:
            groups[mesh.geometry_id].append(index)
    instances = []
    instanced: set[int] = set()
    # Limita los actores: los grupos poco repetidos rinden mejor en el lote.
    for group in sorted(groups.values(), key=len, reverse=True):
        if len(group) < 8 or len(instances) == 64:
            break
        mesh = meshes[group[0]][1]
        colors = mesh.colors
        if colors is not None and not np.all(colors == colors[0]):
            continue
        indices = np.asarray(group, dtype=np.int32)
        instances.append(Instances(
            mesh, indices, (translations[indices] - origin).astype(np.float32),
            np.asarray([_quaternion(rotations[i]) for i in group], dtype=np.float32),
            colors[0] if colors is not None else np.asarray(DEFAULT_RGB, dtype=np.uint8),
        ))
        instanced.update(group)

    unique = [(i, mesh) for i, (_, mesh) in enumerate(meshes) if i not in instanced]
    vertices = np.empty((sum(len(m.vertices) for _, m in unique), 3), dtype=np.float32)
    count = sum(len(mesh.faces) for _, mesh in unique)
    faces = np.empty((count, 3), dtype=np.int32)
    colors = np.empty((count, 3), dtype=np.uint8)
    owners = np.empty(count, dtype=np.int32)
    point_start = cell_start = 0
    for index, mesh in unique:
        point_end = point_start + len(mesh.vertices)
        cell_end = cell_start + len(mesh.faces)
        vertices[point_start:point_end] = (
            mesh.vertices @ rotations[index].T + (translations[index] - origin)
        )
        faces[cell_start:cell_end] = mesh.faces + point_start
        colors[cell_start:cell_end] = mesh.colors if mesh.colors is not None else DEFAULT_RGB
        owners[cell_start:cell_end] = index
        point_start, cell_start = point_end, cell_end
    return Scene(
        elements, tuple(elements[entity_id].key for entity_id, _ in meshes),
        MeshBatch(vertices, faces, colors, owners), tuple(instances),
    )
