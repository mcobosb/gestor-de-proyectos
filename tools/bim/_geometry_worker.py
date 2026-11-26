"""Proceso auxiliar para aislar fallos nativos de teselación IFC."""
from __future__ import annotations

from pathlib import Path
import pickle
import sys

import ifcopenshell

from .engine import _triangulate


def main(arguments: list[str] | None = None) -> int:
    """Calcula y guarda las mallas del modelo indicado por la aplicación."""
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 2:
        raise ValueError("Se necesitan las rutas del IFC y del resultado.")
    source = ifcopenshell.open(Path(arguments[0]), format=".ifc")
    if not isinstance(source, ifcopenshell.file):
        raise ValueError("El archivo no contiene un modelo IFC STEP.")
    meshes = _triangulate(source)
    with Path(arguments[1]).open("wb") as stream:
        pickle.dump(meshes, stream, protocol=pickle.HIGHEST_PROTOCOL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
