"""Selección y composición de los planos de Seguridad y Salud."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app_paths import user_data_directory
from tools._shared.drawing_templates import find_title_block, read_title_block_fields


LIBRARY = Path(__file__).resolve().parent / "PLANOS.zip"
MAX_SVG_BYTES = 16 * 1024 * 1024
_CUSTOM_CODE = re.compile(r"CUSTOM-[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class DrawingSheet:
    """Ficha incorporada o añadida por el usuario."""

    code: str
    title: str
    filename: str
    path: Path | None = None


SHEETS = (
    DrawingSheet("CIN12", "Cinturones de seguridad", "FSS_CIN_12_v01_20261124.svg"),
    DrawingSheet("SEN01", "Salvamento, evacuación y extinción", "FSS_SEÑ_01_v01_20261124.svg"),
    DrawingSheet("SEN02", "Señales de peligro y riesgos eléctricos", "FSS_SEÑ_02_v01_20261124.svg"),
    DrawingSheet("SEN03", "Señales de obligación", "FSS_SEÑ_03_v01_20261124.svg"),
    DrawingSheet("SEN04", "Señales de prohibición", "FSS_SEÑ_04_v01_20261124.svg"),
    DrawingSheet("SEN05", "Señales de advertencia", "FSS_SEÑ_05_v01_20261124.svg"),
    DrawingSheet("SEN07A", "Elementos auxiliares de señalización I", "FSS_SEÑ_07a_v01_20261124.svg"),
    DrawingSheet("SEN07B", "Elementos auxiliares de señalización II", "FSS_SEÑ_07b_v01_20261124.svg"),
    DrawingSheet("SEN08", "Señalización de obra en calzada", "FSS_SEÑ_08_v01_20261124.svg"),
    DrawingSheet("SEN09", "Instalación eléctrica provisional", "FSS_SEÑ_09_v01_20261124.svg"),
    DrawingSheet("SEN10A", "Señales de prohibición y obligación", "FSS_SEÑ_10a_v01_20261124.svg"),
    DrawingSheet("SEN10B", "Señales de advertencia e información", "FSS_SEÑ_10b_v01_20261124.svg"),
    DrawingSheet("SEN11", "Dúmper y carretilla portapaneles", "FSS_SEÑ_11_v01_20261124.svg"),
    DrawingSheet("ZAN13", "Protecciones en zanjas, huecos y aberturas", "FSS_ZAN_13_v01_20261124.svg"),
)


def user_library_directory() -> Path:
    """Devuelve la biblioteca personal, separada de los recursos instalados."""
    return user_data_directory(__file__) / "fichas_planos"


def available_sheets() -> list[DrawingSheet]:
    """Lista las fichas incluidas y las copias guardadas en la biblioteca personal."""
    folder = user_library_directory()
    if not folder.exists():
        return list(SHEETS)
    try:
        paths = sorted(folder.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        raise ValueError(f"No se pudo leer la biblioteca de planos: {exc}") from exc
    result = list(SHEETS)
    codes = set()
    for path in paths:
        if not path.is_file() or path.suffix.casefold() != ".svg":
            continue
        code, separator, title = path.stem.partition("_")
        if not separator or not title or not _CUSTOM_CODE.fullmatch(code):
            raise ValueError(f"El nombre de la ficha personal no es válido: {path.name}.")
        if code in codes:
            raise ValueError(f"Hay fichas personales duplicadas: {code}.")
        codes.add(code)
        result.append(DrawingSheet(code, title, path.name, path))
    return result


def _validate_svg(data: bytes) -> None:
    """Comprueba que la ficha es un SVG autónomo que Qt puede representar."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    if not data or len(data) > MAX_SVG_BYTES:
        raise ValueError("La ficha SVG está vacía o supera los 16 MB.")
    if b"\x00" in data:
        raise ValueError("Guarda la ficha SVG en una codificación compatible con UTF-8.")
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data, re.IGNORECASE):
        raise ValueError("La ficha SVG no puede contener declaraciones DTD o entidades.")
    if re.search(br"<\?xml-stylesheet\b", data, re.IGNORECASE):
        raise ValueError("La ficha SVG no puede utilizar hojas de estilo externas.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"La ficha SVG no es XML válido: {exc}") from exc
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("La ficha debe ser un documento SVG.")
    for element in root.iter():
        for key, value in element.attrib.items():
            if key.rsplit("}", 1)[-1] == "href" and value and not value.startswith(("#", "data:")):
                raise ValueError("La ficha contiene recursos externos; incrústalos en el SVG antes de añadirla.")
            if any(not match.strip(" '\"").startswith(("#", "data:")) for match in
                   re.findall(r"url\(([^)]*)\)", value, re.IGNORECASE)):
                raise ValueError("La ficha contiene recursos externos; incrústalos en el SVG antes de añadirla.")
        if element.tag == "{http://www.w3.org/2000/svg}style" and element.text:
            if re.search(r"@import\b", element.text, re.IGNORECASE):
                raise ValueError("La ficha SVG no puede utilizar hojas de estilo externas.")
            if any(not match.strip(" '\"").startswith(("#", "data:")) for match in
                   re.findall(r"url\(([^)]*)\)", element.text, re.IGNORECASE)):
                raise ValueError("La ficha contiene recursos externos; incrústalos en el SVG antes de añadirla.")
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid() or renderer.viewBoxF().isEmpty():
        raise ValueError("La ficha SVG no tiene dimensiones válidas o no se puede representar.")


def add_user_sheet(path: Path) -> DrawingSheet:
    """Añade una copia autónoma con identidad estable, sin modificar el original."""
    if path.suffix.casefold() != ".svg":
        raise ValueError("Selecciona un archivo SVG.")
    try:
        with path.open("rb") as source:
            data = source.read(MAX_SVG_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"No se pudo leer la ficha {path.name}: {exc}") from exc
    _validate_svg(data)
    code = "CUSTOM-" + hashlib.sha256(data).hexdigest()
    if any(sheet.code == code for sheet in available_sheets()):
        raise ValueError("Esta ficha ya está en la biblioteca personal.")
    title = path.stem.strip()[:100].rstrip(" .") or "Ficha"
    folder = user_library_directory()
    destination = folder / f"{code}_{title}.svg"
    created = False
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as target:
            created = True
            target.write(data)
    except OSError as exc:
        if created:
            destination.unlink(missing_ok=True)
        raise ValueError(f"No se pudo guardar la ficha en la biblioteca: {exc}") from exc
    return DrawingSheet(code, title, destination.name, destination)


def read_sheet(sheet: DrawingSheet) -> bytes:
    """Lee la ficha sin extracción y detecta cambios en las copias personales."""
    try:
        if sheet.path is None:
            with ZipFile(LIBRARY) as archive:
                return archive.read(sheet.filename)
        with sheet.path.open("rb") as source:
            data = source.read(MAX_SVG_BYTES + 1)
    except (OSError, KeyError, BadZipFile) as exc:
        raise ValueError(f"No se pudo leer la ficha {sheet.title}: {exc}") from exc
    if len(data) > MAX_SVG_BYTES or "CUSTOM-" + hashlib.sha256(data).hexdigest() != sheet.code:
        raise ValueError(f"La ficha personal {sheet.title} ha sido modificada o está dañada.")
    return data


def restore_drawings(value: object) -> dict | None:
    """Valida las decisiones guardadas, sin convertir valores arbitrarios en booleanos."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("paper") not in ("A4", "A3"):
        raise ValueError("El formato de los planos de Seguridad y Salud no es válido.")
    selected = value.get("selected")
    codes = {sheet.code for sheet in SHEETS}
    if not isinstance(selected, dict) or any(
        not isinstance(code, str) or (code not in codes and not _CUSTOM_CODE.fullmatch(code))
        or type(enabled) is not bool for code, enabled in selected.items()
    ):
        raise ValueError("La selección de fichas de Seguridad y Salud no es válida.")
    return {"paper": value["paper"], "selected": dict(selected)}


def _filled_title_block(path: Path, fields: dict[str, str]) -> bytes:
    """Sustituye los textos identificados respetando sus posiciones y estilos."""
    try:
        read_title_block_fields(path)
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"El cajetín SVG no es válido: {path}.") from exc
    for element in root.iter():
        if element.tag == "{http://www.w3.org/2000/svg}text" and not element.get("x"):
            positioned = next(
                (child for child in element.iter() if child is not element and child.get("x") and child.get("y")),
                None,
            )
            if positioned is not None:
                element.set("x", positioned.get("x"))
                element.set("y", positioned.get("y"))
        identifier = element.get("id")
        if identifier not in fields:
            continue
        leaves = [node for node in element.iter() if not list(node)]
        if leaves and leaves[0] is not element:
            for node in element.iter():
                if list(node):
                    node.text = None
                if node is not element:
                    node.tail = None
            leaves[0].text = fields[identifier]
            for leaf in leaves[1:]:
                leaf.text = ""
        else:
            element.text = fields[identifier]
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render_drawings(app, state: dict) -> list[bytes]:
    """Compone imágenes PNG de página completa, sin modificar los SVG originales."""
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    selection = restore_drawings(state)
    if selection is None:
        raise ValueError("Falta la selección de los planos de Seguridad y Salud.")
    catalog = available_sheets()
    available_codes = {sheet.code for sheet in catalog}
    missing = [
        code for code, selected in selection["selected"].items()
        if selected and code not in available_codes
    ]
    if missing:
        raise ValueError(
            "Faltan fichas seleccionadas en la biblioteca personal: " + ", ".join(missing)
        )
    sheets = [sheet for sheet in catalog if selection["selected"].get(sheet.code, False)]
    if not sheets:
        return []
    directory = app.report_settings.drawing_title_block_path.strip()
    if not directory:
        raise ValueError("Configura la carpeta de cajetines de planos en los ajustes del proyecto.")
    try:
        block = find_title_block(Path(directory), selection["paper"])
        source = {sheet.code: read_sheet(sheet) for sheet in sheets}
    except OSError as exc:
        raise ValueError(f"No se pudieron cargar las fichas o el cajetín: {exc}") from exc

    paper = selection["paper"]
    width_mm, height_mm = (210, 297) if paper == "A4" else (420, 297)
    pixels_per_mm = 200 / 25.4
    width, height = round(width_mm * pixels_per_mm), round(height_mm * pixels_per_mm)
    authors = app.report_settings.document_author.splitlines()
    roles = app.report_settings.role.splitlines()
    pages = []
    per_page = 1 if paper == "A4" else 2
    for offset in range(0, len(sheets), per_page):
        group = sheets[offset:offset + per_page]
        fields = {
            "titulo_proyecto": app._project_title(),
            "expediente": app.report_settings.expediente,
            "titulo_plano": " / ".join(sheet.title for sheet in group),
            "numero_plano": str(len(pages) + 1),
            "escala": "S/E",
            "fecha": date.today().strftime("%d/%m/%Y"),
            "autor": authors[0] if authors else "",
            "autor_2": authors[1] if len(authors) > 1 else "",
            "cargo": roles[0] if roles else "",
            "cargo_2": roles[1] if len(roles) > 1 else "",
        }
        title_block = QSvgRenderer(QByteArray(_filled_title_block(block, fields)))
        if not title_block.isValid():
            raise ValueError(f"No se puede representar el cajetín SVG: {block}.")
        image = QImage(width, height, QImage.Format.Format_RGB32)
        if image.isNull():
            raise ValueError("No se pudo crear la imagen del plano.")
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        title_block.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        title_block.render(painter, QRectF(0, 0, width, height))
        margin = 6 * pixels_per_mm
        if paper == "A4":
            area = QRectF(margin, margin, width - 2 * margin, height - 38 * pixels_per_mm)
        else:
            area = QRectF(margin, margin, width - 75 * pixels_per_mm, height - 2 * margin)
        gap = 4 * pixels_per_mm
        cell_width = (area.width() - gap * (per_page - 1)) / per_page
        for index, sheet in enumerate(group):
            renderer = QSvgRenderer(QByteArray(source[sheet.code]))
            if not renderer.isValid():
                painter.end()
                raise ValueError(f"No se puede representar la ficha SVG: {sheet.filename}.")
            renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
            renderer.render(painter, QRectF(
                area.x() + index * (cell_width + gap), area.y(), cell_width, area.height(),
            ))
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
            raise ValueError("No se pudo generar la imagen del plano.")
        pages.append(bytes(data))
        buffer.close()
    return pages
