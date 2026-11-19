"""Registro de primera generación, independiente del historial de deshacer."""
import json
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from PySide6.QtCore import QLockFile

from budget_project import atomic_json_write
from .catalog import TEMPLATES, copy_templates
from .office import MASTER_NAME, write_office_document


def receipt_path(project: Path) -> Path:
    """Localiza el registro dentro de un proyecto de carpeta o junto al JSON."""
    project = project.resolve(strict=True)
    if project.is_dir():
        return project / "estructura_documental.json"
    return project.with_name(f"{project.name}.estructura_documental.json")


def generated_at(project: Path) -> str | None:
    """Lee la fecha de generación y no ignora un registro inválido."""
    path = receipt_path(project)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("fecha"), str) or not data["fecha"]:
        raise ValueError(f"Registro de generación no válido: {path.name}")
    return data["fecha"]


def generate(project: Path, destination: Path, selected: list[Path], title: str, *, text_styles=None) -> tuple[int, int]:
    """Impide que dos ventanas o colaboradores generen la estructura a la vez."""
    receipt = receipt_path(project)
    lock = QLockFile(str(receipt.with_suffix(".lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise OSError("Otra sesión está generando la documentación o no se puede escribir junto al proyecto.")
    try:
        if generated_at(project) is not None:
            raise ValueError("Ya se ha generado la documentación de este proyecto.")

        def prepare(source: Path, output: BinaryIO, section: str) -> None:
            """Personaliza cada copia sin modificar la plantilla de origen."""
            write_office_document(source, output, title, section, text_styles=text_styles)

        def record(copied: list[Path]) -> None:
            """Escribe el maestro y registra la generación solo si ambos se guardan."""
            master = destination / MASTER_NAME
            with master.open("xb") as output:
                try:
                    write_office_document(
                        TEMPLATES / copied[0], output, title, "Estructura documental",
                        links=copied, link_folder=destination, text_styles=text_styles,
                    )
                except (OSError, ValueError, ParseError, BadZipFile, KeyError):
                    output.close()
                    master.unlink(missing_ok=True)
                    raise
            try:
                atomic_json_write(receipt, {
                    "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "destino": str(destination.resolve()),
                    "maestro": MASTER_NAME,
                    "plantillas": [path.as_posix() for path in copied],
                })
            except OSError:
                master.unlink(missing_ok=True)
                raise

        try:
            return copy_templates(destination, selected, record=record, prepare=prepare)
        except (ParseError, BadZipFile, KeyError) as exc:
            raise ValueError(f"No se pudo leer una plantilla ODT: {exc}") from exc
    finally:
        lock.unlock()
