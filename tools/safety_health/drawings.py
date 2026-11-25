"""Selección y composición de los planos de Seguridad y Salud."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import hashlib
import math
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
    paper: str = "A4"


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
    DrawingSheet("A3-AUX09", "Elementos auxiliares de izado", "FSS_AUX_09_20261124.svg", paper="A3"),
    DrawingSheet("A3-AUX10", "Elementos auxiliares de seguridad", "FSS_AUX_10_20261124.svg", paper="A3"),
    DrawingSheet("A3-CER11", "Cerramiento perimetral", "FSS_CER_11_20261124.svg", paper="A3"),
    DrawingSheet("A3-COD05", "Código de señales de maniobra", "FSS_COD_05_20261124.svg", paper="A3"),
    DrawingSheet("A3-ELE08", "Instalación eléctrica provisional", "FSS_ELE_08_20261124.svg", paper="A3"),
    DrawingSheet("A3-EME06", "Teléfonos de emergencia", "FSS_EME_06_20261124.svg", paper="A3"),
    DrawingSheet("A3-EPI07", "Equipos de protección individual", "FSS_EPI_07_20261124.svg", paper="A3"),
    DrawingSheet("A3-SEN01", "Salvamento, evacuación y extinción", "FSS_SEÑ_01_20261124.svg", paper="A3"),
    DrawingSheet("A3-SEN02", "Señales de peligro y riesgos eléctricos", "FSS_SEÑ_02_20261124.svg", paper="A3"),
    DrawingSheet("A3-SEN03", "Señales de obligación", "FSS_SEÑ_03_20261124.svg", paper="A3"),
    DrawingSheet("A3-SEN04", "Señales de prohibición", "FSS_SEÑ_04_20261124.svg", paper="A3"),
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
                return archive.read(f"{sheet.paper}/{sheet.filename}")
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
        if element.tag == "{http://www.w3.org/2000/svg}image":
            xlink_href = "{http://www.w3.org/1999/xlink}href"
            if element.get("href") is None and element.get(xlink_href) is not None:
                element.set("href", element.attrib.pop(xlink_href))
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
        if identifier == "titulo_proyecto":
            _wrap_project_title(root, element)
    _flatten_simple_text_spans(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _wrap_project_title(root: ET.Element, element: ET.Element) -> None:
    """Distribuye el título en dos líneas y reduce su tamaño si aún no cabe."""
    from PySide6.QtGui import QFont, QFontMetricsF

    text = " ".join("".join(element.itertext()).split())
    if not text:
        return
    namespace = "{http://www.w3.org/2000/svg}"
    first = next((child for child in element if child.tag == namespace + "tspan"), None)
    leaf = next((node for node in first.iter() if not list(node)), first) if first is not None else element
    parents = {child: parent for parent in root.iter() for child in parent}
    ancestors = []
    current = element
    while current is not None:
        ancestors.append(current)
        current = parents.get(current)
    descendants = []
    current = leaf
    while current is not element:
        descendants.append(current)
        current = parents[current]
    properties = {}
    size = 12.0
    element_size = size
    for node in list(reversed(ancestors)) + list(reversed(descendants)):
        if node is None:
            continue
        local = {}
        for name in ("font-size", "font-family", "font-weight", "font-style"):
            if name in node.attrib:
                local[name] = node.get(name)
        for declaration in node.get("style", "").split(";"):
            name, separator, value = declaration.partition(":")
            if separator and name.strip() in ("font-size", "font-family", "font-weight", "font-style"):
                local[name.strip()] = value.strip()
        if "font-size" in local and local["font-size"] != "inherit":
            match = re.fullmatch(
                r"\s*((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))(px|pt|pc|mm|cm|in|em|%)?\s*", local["font-size"],
            )
            if not match:
                raise ValueError("No se reconoce el tamaño de fuente del título del proyecto.")
            unit = match.group(2) or "px"
            units = {"px": 1, "pt": 4 / 3, "pc": 16, "mm": 96 / 25.4, "cm": 96 / 2.54,
                     "in": 96, "em": size, "%": size / 100}
            size = float(match.group(1)) * units[unit]
        properties.update(local)
        if node is element:
            element_size = size
    if not math.isfinite(size) or size <= 0:
        raise ValueError("El tamaño de fuente del título del proyecto no es válido.")
    font = QFont()
    font.setFamilies([family.strip(" '\"") for family in properties.get("font-family", "sans-serif").split(",")])
    font.setPixelSize(max(1, round(size)))
    weight = properties.get("font-weight", "")
    font.setBold(weight in ("bold", "bolder") or (weight.isdigit() and int(weight) >= 600))
    font.setItalic(properties.get("font-style") in ("italic", "oblique"))
    metrics = QFontMetricsF(font)

    def width(value: str) -> float:
        """Mide el texto a su tamaño SVG original."""
        return metrics.horizontalAdvance(value) * size / font.pixelSize()

    available = 32 * size
    shape = re.search(r"shape-inside\s*:\s*url\(\s*#([^) ]+)\s*\)", element.get("style", ""))
    if shape:
        box = next((node for node in root.iter() if node.get("id") == shape.group(1)), None)
        if box is not None and box.tag == namespace + "rect":
            try:
                box_width = float(box.get("width", ""))
            except ValueError as exc:
                raise ValueError("La anchura del área del título del proyecto no es válida.") from exc
            if not math.isfinite(box_width) or box_width <= 0:
                raise ValueError("La anchura del área del título del proyecto no es válida.")
            available = box_width
    if width(text) <= available:
        return

    words = text.split(" ")
    if len(words) > 1:
        choices = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
    else:
        choices = [(text[:i], text[i:]) for i in range(1, len(text))]
    if not choices:
        return
    lines = min(choices, key=lambda pair: max(width(pair[0]), width(pair[1])))
    maximum = max(width(line) for line in lines)
    if maximum > available:
        factor = available / maximum
        for node in element.iter():
            if node is element:
                node.set("font-size", str(element_size * factor))
            if node is not element and "font-size" in node.attrib:
                value = re.fullmatch(
                    r"\s*((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))(px|pt|pc|mm|cm|in|em|%)?\s*",
                    node.get("font-size", ""),
                )
                if not value and node.get("font-size") != "inherit":
                    raise ValueError("No se puede ajustar el tamaño de fuente del título del proyecto.")
                if value and value.group(2) not in ("em", "%"):
                    node.set("font-size", f"{float(value.group(1)) * factor}{value.group(2) or ''}")
            style = node.get("style")
            if style:
                def scale_style(match: re.Match) -> str:
                    """Ajusta el tamaño de fuente SVG conservando unidades relativas."""
                    if node is element:
                        return f"{match.group(1)}{element_size * factor}px"
                    value = re.fullmatch(
                        r"\s*((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))(px|pt|pc|mm|cm|in|em|%)?\s*",
                        match.group(2),
                    )
                    if not value:
                        if match.group(2).strip() == "inherit":
                            return match.group(0)
                        raise ValueError("No se puede ajustar el tamaño de fuente del título del proyecto.")
                    if value.group(2) in ("em", "%"):
                        return match.group(0)
                    return f"{match.group(1)}{float(value.group(1)) * factor}{value.group(2) or ''}"
                node.set("style", re.sub(
                    r"(font-size\s*:\s*)([^;]+)", scale_style, style,
                ))

    try:
        x = float(element.get("x", ""))
        y = float(element.get("y", ""))
    except ValueError as exc:
        raise ValueError("El título del proyecto necesita una posición x e y en el cajetín.") from exc
    line_y = y + size * 1.25
    if first is not None:
        spans = [child for child in element if child.tag == namespace + "tspan"]
        if len(spans) > 1 and spans[1].get("y") is not None:
            try:
                line_y = float(spans[1].get("y"))
            except ValueError as exc:
                raise ValueError("La posición de la segunda línea del título no es válida.") from exc
    leaf.text = lines[0]
    second = deepcopy(element)
    for node in second.iter():
        node.attrib.pop("id", None)
        if node.get("y") is not None:
            try:
                node.set("y", str(float(node.get("y")) + line_y - y))
            except ValueError as exc:
                raise ValueError("La posición de la segunda línea del título no es válida.") from exc
    second.set("x", str(x))
    second.set("y", str(line_y))
    second_leaf = next((node for node in second.iter() if not list(node)), second)
    second_leaf.text = lines[1]
    parent = next((node for node in root.iter() if element in node), None)
    if parent is None:
        raise ValueError("No se pudo colocar la segunda línea del título del proyecto.")
    parent.insert(list(parent).index(element) + 1, second)


def _flatten_simple_text_spans(root: ET.Element) -> None:
    """Conserva posición y estilo de tspan anidados simples no admitidos por Qt SVG."""
    namespace = "{http://www.w3.org/2000/svg}"
    if next(root.iter(namespace + "style"), None) is not None:
        return
    serialized = ET.tostring(root, encoding="unicode")
    for text in root.iter(namespace + "text"):
        for span in text:
            if span.tag != namespace + "tspan" or len(span) != 1:
                continue
            child = span[0]
            if (
                child.tag != namespace + "tspan" or len(child)
                or (span.text or "").strip() or (child.tail or "").strip()
                or set(span.attrib) - {"id", "x", "y"}
                or set(child.attrib) & {"x", "y", "dx", "dy", "rotate", "transform"}
            ):
                continue
            identifier = child.get("id")
            if identifier and serialized.count(identifier) != 1:
                continue
            for name, value in child.attrib.items():
                if name != "id":
                    span.set(name, value)
            span.text = child.text
            span.remove(child)


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
    sheets = [
        sheet for sheet in catalog
        if selection["selected"].get(sheet.code, False)
        and (sheet.paper == selection["paper"] or sheet.path is not None)
    ]
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
    groups = []
    pending = []
    for sheet in sheets:
        if sheet.paper == "A3":
            if pending:
                groups.append(pending)
                pending = []
            groups.append([sheet])
        else:
            pending.append(sheet)
            if len(pending) == (1 if paper == "A4" else 2):
                groups.append(pending)
                pending = []
    if pending:
        groups.append(pending)
    for group in groups:
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
        per_page = 1 if paper == "A4" or group[0].paper == "A3" else 2
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
