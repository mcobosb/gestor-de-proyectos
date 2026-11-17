"""Lectura del capitulo 45 del vocabulario principal CPV 2008 suministrado."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from functools import lru_cache
from io import BytesIO
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from app_paths import application_directory


_XLSX = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_RELATIONSHIP = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


@dataclass(frozen=True)
class CpvEntry:
    """Conserva el codigo completo, incluido su digito de control, y el texto espanol."""

    code: str
    description: str

    @property
    def label(self) -> str:
        """Muestra juntos el codigo y su descripcion para evitar ambiguedades."""
        return f"{self.code} \u2014 {self.description}"


def _cpv_rows(workbook: ZipFile) -> Iterator[tuple[str, str]]:
    """Lee solo CODE y ES del OOXML, sin materializar los otros 23 idiomas como celdas."""
    metadata = ET.fromstring(workbook.read("xl/workbook.xml"))
    sheet = next((
        node for node in metadata.iter(f"{_XLSX}sheet")
        if node.get("name") == "CPV codes"
    ), None)
    if sheet is None:
        raise ValueError("No se encuentra la hoja CPV codes.")
    relationships = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    relation = next((
        node for node in relationships
        if node.get("Id") == sheet.get(_RELATIONSHIP)
    ), None)
    if relation is None or relation.get("TargetMode") == "External":
        raise ValueError("La hoja CPV codes no tiene una relacion interna valida.")
    target = relation.attrib["Target"]
    sheet_path = posixpath.normpath(
        target if target.startswith("/") else posixpath.join("xl", target)
    ).lstrip("/")
    strings = []
    if "xl/sharedStrings.xml" in workbook.namelist():
        strings = [
            "".join(text.text or "" for text in node.iter(f"{_XLSX}t"))
            for node in ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        ]

    def cell_text(cell: ET.Element) -> str:
        """Resuelve los textos compartidos e inline usados por el libro CPV."""
        if cell.get("t") == "inlineStr":
            return "".join(text.text or "" for text in cell.iter(f"{_XLSX}t"))
        value = cell.findtext(f"{_XLSX}v", "")
        if cell.get("t") == "s":
            index = int(value)
            if not 0 <= index < len(strings):
                raise ValueError("Indice de texto compartido no valido en el CPV.")
            return strings[index]
        return value

    columns = None
    with workbook.open(sheet_path) as source:
        for _event, row in ET.iterparse(source, events=("end",)):
            if row.tag != f"{_XLSX}row":
                continue
            cells = {
                cell.attrib["r"].rstrip("0123456789"): cell
                for cell in row.findall(f"{_XLSX}c")
            }
            if columns is None:
                headers = {cell_text(cell): column for column, cell in cells.items()}
                if "CODE" not in headers or "ES" not in headers:
                    raise ValueError("El libro CPV debe contener las columnas CODE y ES.")
                columns = (headers["CODE"], headers["ES"])
            else:
                values = tuple(
                    cell_text(cells[column]).strip() if column in cells else ""
                    for column in columns
                )
                if any(values):
                    if not values[0]:
                        raise ValueError("Hay una fila del vocabulario CPV sin codigo.")
                    yield values[0], values[1]
            row.clear()
    if columns is None:
        raise ValueError("La hoja CPV codes esta vacia.")


@lru_cache(maxsize=1)
def load_cpv_codes() -> tuple[CpvEntry, ...]:
    """Lee la columna ES sin extraer ni modificar el libro del archivo original."""
    path = application_directory(__file__) / "tools" / "cpv_2008_xls.zip"
    try:
        with ZipFile(path) as archive:
            data = archive.read("cpv_2008_ver_2013.xlsx")
        with ZipFile(BytesIO(data)) as workbook:
            entries = {}
            for code, description in _cpv_rows(workbook):
                if not code.startswith("45"):
                    continue
                if (
                    not re.fullmatch(r"45[0-9]{6}-[0-9]", code)
                    or not description
                ):
                    raise ValueError(f"Codigo o descripcion espanola no validos: {code}.")
                if code in entries:
                    raise ValueError(f"Codigo CPV duplicado: {code}.")
                entries[code] = CpvEntry(code, description)
            if not entries:
                raise ValueError("El capitulo 45 del CPV esta vacio.")
            return tuple(entries[code] for code in sorted(entries))
    except (
        OSError, BadZipFile, KeyError, ValueError, ET.ParseError,
        RuntimeError, NotImplementedError,
    ) as exc:
        raise ValueError(f"No se pudo leer el catalogo CPV de {path}: {exc}") from exc


def resolve_cpv_codes(codes: list[str]) -> tuple[CpvEntry, ...]:
    """Valida la seleccion contra el capitulo 45 sin aceptar duplicados."""
    if not codes:
        return ()
    if len(set(codes)) != len(codes):
        raise ValueError("La seleccion contiene codigos CPV repetidos.")
    by_code = {entry.code: entry for entry in load_cpv_codes()}
    unknown = [code for code in codes if code not in by_code]
    if unknown:
        raise ValueError("Codigos CPV desconocidos o fuera del capitulo 45: " + ", ".join(unknown))
    return tuple(by_code[code] for code in codes)
