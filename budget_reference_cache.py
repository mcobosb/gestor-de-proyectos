"""Copia local persistente de bases BC3 de referencia."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from budget_core import Bc3Dataset


def reference_cache_path(source: Path, cache_root: Path) -> Path:
    """Identifica la copia por la ruta original sin acceder a la red."""
    identity = os.path.normcase(os.path.abspath(source))
    digest = hashlib.sha256(os.fsencode(identity)).hexdigest()
    return cache_root / digest / source.name


def load_reference_copy(
    source: Path, cache_root: Path, *, cached_only: bool = False
) -> Bc3Dataset:
    """Carga una copia validada y conserva la ruta original como identidad."""
    source = Path(os.path.abspath(source))
    target = reference_cache_path(source, cache_root)
    if cached_only:
        dataset = Bc3Dataset(target)
        dataset.path = source
        return dataset

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        before = source.stat()
        with tempfile.NamedTemporaryFile(
            dir=target.parent, suffix=".bc3", delete=False
        ) as output:
            temporary = Path(output.name)
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError(f"La base cambió mientras se copiaba: {source}")
        dataset = Bc3Dataset(temporary)
        os.replace(temporary, target)
        temporary = None
        dataset.path = source
        return dataset
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
