"""Generación y reemplazo seguro de la estructura documental del proyecto."""
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from PySide6.QtCore import QLockFile

from budget_project import atomic_json_write
from project_standards import document_control_text
from tools._shared.document_layout import (
    add_odt_blue_table_cells, add_odt_blue_table_headers, style_odt_blue_table,
)
from .catalog import (
    CATALOG, DOCUMENTATION_PROCEDURE_FIELDS, TEMPLATES, copy_templates,
)
from .office import (
    MASTER_NAME, _master_link, renumber_annex,
    write_office_document, write_project_master,
)

TECHNICAL_DOCUMENTS_FOLDER = "Documentos tecnicos"
DOCUMENT_FOLDERS = (
    "DOC01 - MEMORIA Y ANEJOS",
    "DOC02 - PLANOS",
    "DOC03 - PLIEGO DE PRESCRIPCIONES TÉCNICAS PARTICULARES",
    "DOC04 - PRESUPUESTO",
)
DOCUMENT_SECTIONS = {
    f"DOC{index:02}": folder for index, folder in enumerate(DOCUMENT_FOLDERS, 1)
}
MEMORY_DOCUMENT_TITLE = "Memoria Descriptiva y Justificativa"


def receipt_path(project: Path) -> Path:
    """Localiza el registro dentro de un proyecto de carpeta o junto al JSON."""
    project = project.resolve(strict=True)
    if project.is_dir():
        return project / "estructura_documental.json"
    return project.with_name(f"{project.name}.estructura_documental.json")


def _read_receipt(project: Path) -> dict | None:
    """Lee el registro de generación y rechaza contenido incompleto."""
    path = receipt_path(project)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("fecha"), str) or not data["fecha"]:
        raise ValueError(f"Registro de generación no válido: {path.name}")
    return data


def _project_root(project: Path) -> Path:
    """Devuelve la carpeta del proyecto guardado."""
    project = project.resolve(strict=True)
    return project if project.is_dir() else project.parent


def _delivery_root(project: Path, value: str) -> Path:
    """Resuelve una ubicación registrada, conservando la portabilidad del proyecto."""
    if value == ".":
        return _project_root(project)
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("El registro de generación contiene una ubicación no válida.")
    return path.resolve(strict=False)


def _delivery_records(project: Path, data: dict | None) -> list[dict]:
    """Recupera los registros por carpeta, incluyendo el formato anterior."""
    if data is None:
        return []
    records = data.get("ubicaciones")
    if records is None:
        return [{**data, "raiz": "."}]
    if not isinstance(records, list):
        raise ValueError("El registro de generación contiene ubicaciones no válidas.")
    roots: set[Path] = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("raiz"), str):
            raise ValueError("El registro de generación contiene una ubicación no válida.")
        if not isinstance(record.get("fecha"), str) or not record["fecha"]:
            raise ValueError("El registro de generación contiene una fecha no válida.")
        root = _delivery_root(project, record["raiz"])
        if root in roots:
            raise ValueError("El registro de generación contiene ubicaciones duplicadas.")
        roots.add(root)
    return records


def suggested_destination(project: Path) -> Path:
    """Sugiere la última carpeta elegida o la carpeta del proyecto."""
    data = _read_receipt(project)
    records = _delivery_records(project, data)
    if records:
        root = _delivery_root(project, records[-1]["raiz"])
        if root.is_dir():
            return root
    return _project_root(project)


def suggested_document_directory(project: Path, section: str) -> Path | None:
    """Sugiere la carpeta del documento dentro de la última entrega registrada."""
    try:
        folder = DOCUMENT_SECTIONS[section]
    except KeyError as exc:
        raise ValueError(f"Sección documental desconocida: {section}") from exc
    data = _read_receipt(project)
    records = _delivery_records(project, data)
    if not records:
        return None
    root = _delivery_root(project, records[-1]["raiz"])
    directory = root / TECHNICAL_DOCUMENTS_FOLDER / folder
    return directory if directory.is_dir() else None


def suggested_tool_annex_path(project: Path, annex_tool: str) -> Path | None:
    """Recupera la ruta y el nombre asignados al anejo de una herramienta."""
    if not isinstance(annex_tool, str) or not annex_tool:
        raise ValueError("Indica la herramienta propietaria del anejo.")
    data = _read_receipt(project)
    records = _delivery_records(project, data)
    for record in reversed(records):
        assignments = record.get("anejos_generables", {})
        if not isinstance(assignments, dict):
            raise ValueError("El registro de generación contiene anejos no válidos.")
        assignment = assignments.get(annex_tool)
        if assignment is None:
            continue
        if not isinstance(assignment, dict):
            raise ValueError("El registro de generación contiene un anejo no válido.")
        directory_value = assignment.get("directorio")
        filename = assignment.get("nombre")
        if not isinstance(directory_value, str) or not isinstance(filename, str):
            raise ValueError("El registro de generación contiene una ruta de anejo no válida.")
        directory = Path(directory_value)
        if (
            directory.is_absolute()
            or ".." in directory.parts
            or directory != Path(TECHNICAL_DOCUMENTS_FOLDER) / DOCUMENT_FOLDERS[0]
            or not filename
            or Path(filename).name != filename
            or Path(filename).suffix.casefold() != ".odt"
        ):
            raise ValueError("El registro de generación contiene una ruta de anejo no válida.")
        root = _delivery_root(project, record["raiz"])
        if not root.is_dir():
            continue
        target = root / directory / filename
        if not target.parent.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
            raise ValueError("La ruta del anejo queda fuera de la entrega documental.")
        if (
            target.is_symlink()
            or (target.exists() and not target.is_file())
            or not target.resolve(strict=False).is_relative_to(root.resolve(strict=True))
        ):
            raise ValueError("La ruta asignada al anejo no apunta a un archivo válido.")
        if target.parent.is_dir():
            return target
    return None


def generated_at(project: Path, *, destination: Path | None = None) -> str | None:
    """Devuelve la fecha de la última generación, opcionalmente de una carpeta."""
    data = _read_receipt(project)
    if destination is None:
        return data["fecha"] if data else None
    root = destination.resolve(strict=True)
    return next(
        (record["fecha"] for record in _delivery_records(project, data)
         if _delivery_root(project, record["raiz"]) == root),
        None,
    )


def _previous_output_files(root: Path, data: dict) -> set[Path]:
    """Valida las rutas propias del paquete antes de reemplazarlas."""
    result: set[Path] = set()
    master = data.get("maestro")
    if master is not None:
        if not isinstance(master, str) or Path(master).is_absolute():
            raise ValueError("El registro de generación contiene un nombre maestro no válido.")
        relative_master = Path(master)
        if (
            not relative_master.parts
            or len(relative_master.parts) != 1
            and relative_master.parts[0] != TECHNICAL_DOCUMENTS_FOLDER
            or ".." in relative_master.parts
        ):
            raise ValueError("El registro de generación contiene una ruta de maestro no válida.")
        if not (root / relative_master).resolve(strict=False).is_relative_to(root):
            raise ValueError("El registro de generación contiene una ruta de maestro fuera del destino.")
        result.add(relative_master)

    for key in ("documentos", "archivos_plano"):
        paths = data.get(key, [])
        if not isinstance(paths, list):
            raise ValueError(f"El registro de generación contiene rutas no válidas: {key}.")
        for value in paths:
            if not isinstance(value, str):
                raise ValueError(f"El registro de generación contiene una ruta no válida: {key}.")
            relative = Path(value)
            if (
                relative.is_absolute() or not relative.parts or ".." in relative.parts
                or relative.parts[0] != TECHNICAL_DOCUMENTS_FOLDER
            ):
                raise ValueError(f"El registro de generación contiene una ruta fuera del paquete: {value}.")
            if not (root / relative).resolve(strict=False).is_relative_to(root):
                raise ValueError(f"El registro de generación contiene una ruta fuera del destino: {value}.")
            result.add(relative)
    return result


def generate(project: Path, destination: Path, selected: list[Path], title: str, *, text_styles=None) -> tuple[int, int]:
    """Serializa la exportación de plantillas para evitar escrituras concurrentes."""
    receipt = receipt_path(project)
    lock = QLockFile(str(receipt.with_suffix(".lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise OSError("Otra sesión está generando la documentación o no se puede escribir junto al proyecto.")
    try:
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


def _safe_component(value: str) -> str:
    """Normaliza títulos editables para usarlos como nombres de archivo Windows."""
    component = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(" .")
    if not component:
        raise ValueError("El nombre de un documento no puede estar vacío.")
    if component.split(".", 1)[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        component = f"_{component}"
    return component


def _master_href(path: Path) -> str:
    """Convierte una ruta del paquete en un enlace relativo desde el ODM."""
    relative = path.relative_to(Path(TECHNICAL_DOCUMENTS_FOLDER))
    return _master_link(relative)


def _master_filename(value: str) -> str:
    """Valida que el nombre elegido sea un único archivo ODM."""
    value = value.strip()
    if not value or Path(value).name != value or any(char in value for char in '<>:"/\\|?*'):
        raise ValueError("El nombre del documento maestro debe ser un nombre de archivo válido.")
    if value.endswith((".", " ")):
        raise ValueError("El nombre del documento maestro no puede terminar en punto o espacio.")
    suffix = Path(value).suffix
    if not suffix:
        value += ".odm"
    elif suffix.lower() != ".odm":
        raise ValueError("El documento maestro debe tener la extensión .odm.")
    return value


def default_master_name(expediente: str) -> str:
    """Construye el nombre inicial GEX… solicitado para el documento maestro."""
    identifier = expediente.strip()
    if identifier.upper().startswith("GEX"):
        identifier = identifier[3:]
    return _safe_component(f"GEX{identifier}-ProyectoConstructivo") + ".odm"


def _master_fields(app) -> dict[str, str]:
    """Prepara los valores dinámicos de la carátula desde la configuración del proyecto."""
    from budget_core import currency_label, fmt_number
    from budget_reports import budget_totals

    settings = app.report_settings
    pem = sum(item.import_total for item in app.budget_items)
    amounts = budget_totals(pem, settings)
    duration = ""
    if app.planning_state:
        from tools.pptp.document import planning_duration

        duration = planning_duration(app)
    if not duration:
        duration = app.pptp_state.get("variables", {}).get("plazo_ejecucion", "")
    currency = currency_label(settings.currency, settings.custom_currency)
    return {
        "{{entidad}}": settings.organization,
        "{{servicio}}": settings.service,
        "{{expediente}}": settings.expediente or "No especificado",
        "{{tipo_proyecto}}": "Proyecto de Construcción",
        "{{título}}": app._project_title(),
        "{{autores}}": settings.document_author,
        "{{PBL}}": f"{fmt_number(float(amounts['taxable']))} {currency}",
        "{{PBL+IVA}}": f"{fmt_number(float(amounts['total']))} {currency}",
        "{{plazo_ejecucion}}": duration or "No especificado",
        "{{fecha}}": datetime.now().astimezone().strftime("%d/%m/%Y"),
        "{{capítulo}}": "DOCUMENTO MAESTRO",
    }


def _new_catalog_document(app, title: str, settings, *, annex_number: int | None = None):
    """Prepara el título, las cabeceras y los estilos de un documento base."""
    from odt_reports import _new_report

    heading = f"ANEJO Nº {annex_number}: {title}" if annex_number is not None else title
    heading = heading.strip()
    document = _new_report(
        heading.upper(), app._project_title(), settings,
        repeat_titles=False, footer_title=heading.upper(), template_title=heading,
    )
    document.paragraph(heading.upper(), "ReportTitle")
    return document


def _write_catalog_document(
    app, target: Path, title: str, outline: object, settings, *,
    annex_number: int | None = None,
) -> None:
    """Genera un ODT editable desde el esquema del catálogo y los estilos del proyecto."""
    if not isinstance(outline, list) or any(
        not isinstance(section, str) or not section.strip() for section in outline
    ):
        raise ValueError(f"El documento «{title}» no tiene un esquema válido.")
    document = _new_catalog_document(app, title, settings, annex_number=annex_number)
    headings = [(1, section.strip()) for section in outline]
    for index, (_, section) in enumerate(headings):
        document.heading(section, level=1, bookmark=f"PPTPHeading{index}")
        document.paragraph("", "ReportBody")
    document.insert_table_of_contents(headings, position=1)
    document.write(target)


def _write_documentation_annex(
    app, target: Path, title: str, settings, *, annex_number: int,
) -> None:
    """Genera el anejo de documentación y trámites como tabla rellenable."""
    document = _new_catalog_document(app, title, settings, annex_number=annex_number)
    document.table(
        ["DOCUMENTACIÓN / TRÁMITE", "ESTADO / REFERENCIA"],
        [(field, "") for field in DOCUMENTATION_PROCEDURE_FIELDS],
        ratios=[0.62, 0.38],
        body_text_style="TableText",
        tinted_rows=True,
    )
    styles = SimpleNamespace(automatic_styles=document.automatic_styles)
    add_odt_blue_table_cells(styles, "DocumentationAnnex")
    add_odt_blue_table_headers(styles, "DocumentationAnnex")
    style_odt_blue_table(document.body[-1], "DocumentationAnnex")
    document.write(target)


def _write_memory_document(app, target: Path, sections: list[dict[str, object]], settings) -> None:
    """Reúne las secciones de memoria seleccionadas en un único ODT base."""
    document = _new_catalog_document(app, MEMORY_DOCUMENT_TITLE, settings)
    if not sections:
        raise ValueError("Selecciona al menos una sección para generar la memoria.")
    headings = []
    for section in sections:
        title = str(section.get("title", "")).strip()
        outline = section.get("outline", [])
        if not title or not isinstance(outline, list) or any(
            not isinstance(subsection, str) or not subsection.strip()
            for subsection in outline
        ):
            raise ValueError("Una sección de la memoria no tiene un esquema válido.")
        document.heading(title, level=1, bookmark=f"PPTPHeading{len(headings)}")
        headings.append((1, title))
        for subsection in outline:
            document.heading(
                subsection.strip(), level=2, bookmark=f"PPTPHeading{len(headings)}",
            )
            headings.append((2, subsection.strip()))
            document.paragraph("", "ReportBody")
    document.insert_table_of_contents(headings, position=1)
    document.write(target)


def generate_project_package(
    project: Path,
    app,
    master_name: str,
    annexes: list[dict[str, object]],
    plans: Sequence[Mapping[str, object]],
    *,
    memory_documents: list[dict[str, object]] | None = None,
    include_pptp: bool = False,
    include_budget: bool = False,
    destination: Path | None = None,
) -> tuple[Path, int]:
    """Genera la entrega técnica y reemplaza las salidas registradas."""
    receipt = receipt_path(project)
    lock = QLockFile(str(receipt.with_suffix(".lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise OSError("Otra sesión está generando la documentación o no se puede escribir junto al proyecto.")
    destination_lock = None
    try:
        project_root = _project_root(project)
        root = destination.resolve(strict=True) if destination is not None else project_root
        if not root.is_dir():
            raise ValueError(f"La ubicación seleccionada no es una carpeta: {root}")
        destination_lock = QLockFile(str(root / ".estructura_documental.lock"))
        destination_lock.setStaleLockTime(0)
        if not destination_lock.tryLock(0):
            raise OSError("Otra sesión está usando la carpeta de destino o no se puede escribir en ella.")
        previous_receipt = _read_receipt(project)
        records = _delivery_records(project, previous_receipt)
        previous_delivery = next(
            (record for record in records if _delivery_root(project, record["raiz"]) == root),
            None,
        )
        previous_outputs = (
            _previous_output_files(root, previous_delivery)
            if previous_delivery else set()
        )
        receipt_existed = receipt.exists()
        master_name = _master_filename(master_name)
        master_relative = Path(TECHNICAL_DOCUMENTS_FOLDER) / master_name
        memory_documents = memory_documents or []
        if not annexes and not memory_documents and not plans and not include_pptp and not include_budget:
            raise ValueError(
                "Selecciona al menos un apartado de memoria, anejo, plano o documento generado por la aplicación."
            )

        output_folder = root / TECHNICAL_DOCUMENTS_FOLDER
        if output_folder.exists() and not output_folder.resolve().is_relative_to(root):
            raise ValueError("La carpeta de documentación queda fuera de la carpeta de destino.")
        document_paths = [
            Path(TECHNICAL_DOCUMENTS_FOLDER) / name for name in DOCUMENT_FOLDERS
        ]
        author = app.report_settings.document_author
        entries = [{"text": "DOCUMENTO Nº 1. MEMORIA Y ANEJOS.", "style": "P18"}]
        traceability: list[tuple[str, str, str, str, str, str]] = [
            ("MAESTRO", master_name, "", author, "", ""),
        ]
        linked_documents: list[Path] = []
        tool_annexes: dict[str, dict[str, str]] = {}
        plan_directories: list[Path] = []
        plan_files: list[Path] = []

        with tempfile.TemporaryDirectory(prefix=".estructura_documental_", dir=root) as temporary:
            stage = Path(temporary)
            for relative in document_paths:
                (stage / relative).mkdir(parents=True, exist_ok=True)

            settings = app.report_settings
            original_format = settings.output_format
            settings.output_format = "odt"
            try:
                memory_relative = (
                    Path(TECHNICAL_DOCUMENTS_FOLDER) / DOCUMENT_FOLDERS[0]
                    / f"{_safe_component(MEMORY_DOCUMENT_TITLE)}.odt"
                )
                if memory_documents:
                    _write_memory_document(
                        app, stage / memory_relative, memory_documents, settings,
                    )
                    linked_documents.append(memory_relative)
                    entries.append({
                        "text": f"{MEMORY_DOCUMENT_TITLE}.",
                        "style": "P15",
                        "href": _master_href(memory_relative),
                    })
                    traceability.append((
                        "DOC01-M01", MEMORY_DOCUMENT_TITLE, "", author, "", "",
                    ))
                else:
                    entries.append({"text": "MEMORIA DESCRIPTIVA.", "style": "P15"})

                annex_number = 0
                for annex in annexes:
                    annex_number += 1
                    title = str(annex.get("title", "")).strip()
                    if not title:
                        raise ValueError(f"Indica el nombre del anejo seleccionado número {annex_number}.")
                    previous = int(annex["number"])
                    filename = _safe_component(
                        f"ANEJO {annex_number:02} - {title}",
                    ) + ".odt"
                    relative = Path(TECHNICAL_DOCUMENTS_FOLDER) / DOCUMENT_FOLDERS[0] / filename
                    target = stage / relative
                    kind = annex.get("kind", "tool")
                    if kind not in {"template", "tool", "documentation"}:
                        raise ValueError(f"Tipo de anejo desconocido: {annex.get('kind')}")
                    if kind == "tool":
                        tool_key = annex.get("tool")
                        if not isinstance(tool_key, str) or not tool_key:
                            raise ValueError("Un anejo de herramienta no tiene una clave válida.")
                        if tool_key in tool_annexes:
                            raise ValueError(f"El anejo de herramienta está duplicado: {tool_key}.")
                        tool_annexes[tool_key] = {
                            "directorio": relative.parent.as_posix(),
                            "nombre": filename,
                        }
                    if kind == "documentation":
                        _write_documentation_annex(
                            app, target, title, settings, annex_number=previous,
                        )
                    else:
                        _write_catalog_document(
                            app, target, title, annex.get("outline", []), settings,
                            annex_number=previous,
                        )
                    renumber_annex(target, previous, annex_number)
                    linked_documents.append(relative)
                    entries.append({
                        "text": f"ANEJO Nº {annex_number}. {title}",
                        "style": "P19",
                        "href": _master_href(relative),
                        "annex_number": str(annex_number),
                        "annex_title": title,
                    })
                    traceability.append((
                        f"DOC01-A{annex_number:02}", title, "", author, "", "",
                    ))

                plan_titles = [str(plan.get("title", "")).strip() for plan in plans]
                for index, title in enumerate(plan_titles, 1):
                    if not title:
                        raise ValueError(f"Indica el nombre del plano seleccionado número {index}.")

                plans_document_relative = None
                if plan_titles:
                    plans_document_relative = (
                        Path(TECHNICAL_DOCUMENTS_FOLDER)
                        / DOCUMENT_FOLDERS[1] / "Planos.odt"
                    )
                    _write_catalog_document(
                        app,
                        stage / plans_document_relative,
                        "DOCUMENTO Nº 2: Planos",
                        [
                            f"PLANO Nº {index:02}. {title}"
                            for index, title in enumerate(plan_titles, 1)
                        ],
                        settings,
                    )
                    linked_documents.append(plans_document_relative)

                entries.append({
                    "text": "DOCUMENTO Nº 2. PLANOS.",
                    "style": "P21",
                    **(
                        {"href": _master_href(plans_document_relative)}
                        if plans_document_relative else {}
                    ),
                })
                for index, (plan, title) in enumerate(zip(plans, plan_titles), 1):
                    folder_name = _safe_component(f"PLANO {index:02} - {title}")
                    relative_dir = (
                        Path(TECHNICAL_DOCUMENTS_FOLDER) / DOCUMENT_FOLDERS[1] / folder_name
                    )
                    plan_directories.append(relative_dir)
                    (stage / relative_dir).mkdir(parents=True, exist_ok=True)
                    source_text = str(plan.get("file", "")).strip()
                    relative_file = None
                    if source_text:
                        source = Path(source_text).resolve(strict=True)
                        if not source.is_file():
                            raise ValueError(f"El archivo seleccionado para el plano no es un archivo: {source}")
                        relative_file = relative_dir / source.name
                        shutil.copy2(source, stage / relative_file)
                        plan_files.append(relative_file)
                    entries.append({
                        "text": f"PLANO Nº {index:02}. {title}",
                        "style": "P20",
                        **({"href": _master_href(relative_file)} if relative_file else {}),
                    })
                    document_title = (
                        f"{title} — {relative_file.name}" if relative_file else title
                    )
                    traceability.append((
                        f"DOC02-P{index:02}", document_title, "", author, "", "",
                    ))

                entries.append({
                    "text": "DOCUMENTO Nº 3. PLIEGO DE PRESCRIPCIONES TÉCNICAS PARTICULARES.",
                    "style": "P21",
                })
                if include_pptp:
                    relative = Path(TECHNICAL_DOCUMENTS_FOLDER) / DOCUMENT_FOLDERS[2] / "PPTP.odt"
                    _write_catalog_document(
                        app, stage / relative, "Pliego de Prescripciones Técnicas Particulares",
                        [child.title for child in CATALOG[2].children], settings,
                    )
                    linked_documents.append(relative)
                    entries.append({
                        "text": "Pliego de Prescripciones Técnicas Particulares.",
                        "style": "P20",
                        "href": _master_href(relative),
                    })
                    traceability.append((
                        "DOC03", "Pliego de Prescripciones Técnicas Particulares",
                        "", author, "", "",
                    ))

                entries.append({"text": "DOCUMENTO Nº 4. PRESUPUESTO.", "style": "P21"})
                if include_budget:
                    from budget_reports import REPORT_NAMES

                    for index, report in enumerate(REPORT_NAMES, 1):
                        filename = _safe_component(report) + ".odt"
                        relative = Path(TECHNICAL_DOCUMENTS_FOLDER) / DOCUMENT_FOLDERS[3] / filename
                        _write_catalog_document(app, stage / relative, report, [], settings)
                        linked_documents.append(relative)
                        entries.append({
                            "text": report, "style": "P20",
                            "href": _master_href(relative),
                        })
                        traceability.append((
                            f"DOC04-{index:02}", report, "", author, "", "",
                        ))
            finally:
                settings.output_format = original_format

            master_fields = _master_fields(app)
            master_fields["{{no_pagina}}"] = ""
            master_template = Path(settings.master_template_path) if settings.master_template_path else None
            master_logo = (
                Path(settings.master_logo_path)
                if master_template is not None and settings.master_logo_path else None
            )
            master_links = [
                path.relative_to(Path(TECHNICAL_DOCUMENTS_FOLDER))
                for path in linked_documents
            ]
            write_project_master(
                master_template, stage / master_relative, fields=master_fields,
                index_entries=entries, traceability=traceability,
                links=master_links, folder=stage / TECHNICAL_DOCUMENTS_FOLDER,
                text_styles=settings.text_styles, logo=master_logo,
                document_control_text=document_control_text(
                    settings.project_disciplines, settings.document_standards,
                ),
            )

            files = [path for path in stage.rglob("*") if path.is_file()]
            staged_paths = {path.relative_to(stage) for path in files}
            existing_outputs: set[Path] = set()
            for relative in previous_outputs:
                existing = root / relative
                if not existing.exists():
                    continue
                if existing.is_symlink() or not existing.is_file():
                    raise ValueError(f"No se puede reemplazar una salida que no sea un archivo: {relative}")
                if not existing.resolve(strict=True).is_relative_to(root):
                    raise ValueError(f"Una salida anterior queda fuera de la carpeta de destino: {relative}")
                existing_outputs.add(relative)
            conflicts = [
                relative for relative in staged_paths
                if ((root / relative).exists() or (root / relative).is_symlink())
                and relative not in existing_outputs
            ]
            if conflicts:
                raise FileExistsError(f"Ya existe un archivo ajeno a la entrega: {conflicts[0]}")

            created_directories: list[Path] = []
            installed_paths: list[Path] = []
            try:
                for relative in (
                    Path(TECHNICAL_DOCUMENTS_FOLDER), *document_paths, *plan_directories,
                ):
                    destination = root / relative
                    if destination.exists() and not destination.resolve().is_relative_to(root):
                        raise ValueError("Una carpeta de salida queda fuera de la carpeta de destino.")
                    if not destination.exists():
                        destination.mkdir()
                        created_directories.append(destination)
                for source in files:
                    relative = source.relative_to(stage)
                    destination = root / relative
                    os.replace(source, destination)
                    installed_paths.append(relative)
                obsolete = existing_outputs - staged_paths
                for relative in obsolete:
                    (root / relative).unlink()
                record = {
                    "fecha": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "destino": str(output_folder.resolve()),
                    "maestro": master_relative.as_posix(),
                    "documentos": [path.as_posix() for path in linked_documents],
                    "anejos_generables": tool_annexes,
                    "planos": [path.as_posix() for path in plan_directories],
                    "archivos_plano": [path.as_posix() for path in plan_files],
                }
                record["raiz"] = "." if root == project_root else str(root)
                record["ubicaciones"] = [
                    item for item in records if _delivery_root(project, item["raiz"]) != root
                ] + [dict(record)]
                atomic_json_write(receipt, record)
            except (OSError, ValueError) as error:
                try:
                    for relative in installed_paths:
                        if relative not in existing_outputs:
                            (root / relative).unlink(missing_ok=True)
                    if not receipt_existed:
                        receipt.unlink(missing_ok=True)
                except OSError as rollback_error:
                    raise OSError(
                        "No se pudo completar la generación ni limpiar todos los archivos nuevos."
                    ) from rollback_error
                for path in reversed(created_directories):
                    try:
                        path.rmdir()
                    except OSError:
                        pass
                raise error
        return root / master_relative, 0
    finally:
        if destination_lock is not None:
            destination_lock.unlock()
        lock.unlock()
