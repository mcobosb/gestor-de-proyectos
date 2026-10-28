"""Rutas de recursos y datos de usuario de la aplicación."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def _is_packaged() -> bool:
    """Indica si el programa se ejecuta desde el instalable compilado de Windows."""
    return Path(sys.argv[0]).suffix.lower() == ".exe"


def application_directory(module_file: str) -> Path:
    """Devuelve la carpeta de recursos de la aplicación."""
    if _is_packaged():
        return Path(sys.argv[0]).resolve().parent
    return Path(module_file).resolve().parent


def user_data_directory(module_file: str) -> Path:
    """Devuelve la carpeta escribible para preferencias e historial."""
    if not _is_packaged():
        return application_directory(module_file)
    app_data = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    directory = app_data / "Gestor de Proyectos"
    directory.mkdir(parents=True, exist_ok=True)
    return directory
