"""Importa materiales y maquinaria de una base BC3 a la biblioteca ESS."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import unicodedata
from zipfile import BadZipFile, ZipFile

from bc3_gui import Bc3Dataset, Concept

RESOURCE_PREFIX = "RECURSOS/"


def _directory_name(concept: Concept) -> str:
    """Crea un nombre de carpeta estable con el código y clasificación BC3."""
    code = re.sub(r"[^A-Za-z0-9_-]+", "_", concept.code.rstrip("#")).strip("_")
    title = unicodedata.normalize("NFKD", concept.summary)
    title = "".join(char for char in title if not unicodedata.combining(char))
    title = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").upper()
    return f"{code}_{title}" if title else code


def resource_documents(dataset: Bc3Dataset) -> list[tuple[str, str]]:
    """Genera fichas clasificadas para materiales y maquinaria, nunca mano de obra."""
    documents = []
    stack = [
        (code, ()) for code in reversed(dataset.get_children("-BAS#"))
    ]
    visited = set()
    while stack:
        code, groups = stack.pop()
        if code in groups or (code, groups) in visited:
            continue
        visited.add((code, groups))
        concept = dataset.concepts.get(code)
        if concept is None or concept.ctype == "1":
            continue
        if concept.ctype in {"2", "3"}:
            category = "MAQUINARIA" if concept.ctype == "2" else "MATERIALES"
            member = PurePosixPath(
                RESOURCE_PREFIX, category,
                *(_directory_name(dataset.concepts[group]) for group in groups),
                f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', concept.code)}.md",
            ).as_posix()
            label = "Maquinaria" if concept.ctype == "2" else "Material"
            lines = [
                f"# {concept.summary or concept.code}",
                "",
                f"- Código BC3: `{concept.code}`",
                f"- Tipo: {label}",
                f"- Unidad: {concept.unit}",
                f"- Precio unitario BCCA 2023: {concept.price:g} €",
            ]
            if concept.text:
                lines.extend(("", concept.text))
            documents.append((member, "\n".join(lines) + "\n"))
            continue

        children = dataset.get_children(code)
        next_groups = (*groups, code) if code != "-BAS#" else groups
        stack.extend((child, next_groups) for child in reversed(children))
    names = [name for name, _ in documents]
    if len(names) != len(set(names)):
        raise ValueError("La base BC3 produce rutas duplicadas para recursos.")
    return documents


def add_bcca_resources(archive_path: Path, source_path: Path) -> tuple[int, int]:
    """Reemplaza la carpeta RECURSOS del ZIP con los recursos de tipo material y maquinaria."""
    if archive_path.resolve() == source_path.resolve():
        raise ValueError("El BC3 de origen y la biblioteca de destino deben ser distintos.")
    dataset = Bc3Dataset(source_path)
    documents = resource_documents(dataset)
    if not documents:
        raise ValueError("La base BC3 no contiene materiales ni maquinaria.")

    signature = archive_path.stat().st_size, archive_path.stat().st_mtime_ns
    temporary_path = None
    try:
        with ZipFile(archive_path) as source:
            with tempfile.NamedTemporaryFile(
                prefix=f".{archive_path.name}.", suffix=".tmp",
                dir=archive_path.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            with ZipFile(temporary_path, "w") as target:
                target.comment = source.comment
                for info in source.infolist():
                    if info.filename.startswith(RESOURCE_PREFIX):
                        continue
                    target.writestr(info, source.read(info))
                for member, content in documents:
                    target.writestr(member, content)
        current = archive_path.stat()
        if (current.st_size, current.st_mtime_ns) != signature:
            raise ValueError("La biblioteca cambió durante la importación. No se guardaron los recursos.")
        os.replace(temporary_path, archive_path)
        temporary_path = None
    except (BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f"No se pudieron importar los recursos BCCA: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return (
        sum(name.startswith(f"{RESOURCE_PREFIX}MATERIALES/") for name, _ in documents),
        sum(name.startswith(f"{RESOURCE_PREFIX}MAQUINARIA/") for name, _ in documents),
    )
