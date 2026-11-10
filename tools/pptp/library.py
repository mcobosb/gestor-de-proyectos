"""Lectura directa de fichas catalogadas en ZIP, sin extraer archivos."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from zipfile import BadZipFile, ZipFile, ZipInfo


MAX_RESOURCE_BYTES = 16 * 1024 * 1024


def validate_relative(relative: str) -> PurePosixPath:
    """Rechaza rutas externas o ambiguas incluso sin extraer el contenido."""
    parts = PurePosixPath(relative)
    if (not relative or "\\" in relative or parts.is_absolute()
            or ".." in parts.parts or ":" in relative or "\x00" in relative
            or parts.as_posix() != relative):
        raise ValueError(f"Ruta de biblioteca no válida: {relative}")
    return parts


@dataclass(frozen=True)
class CatalogEntry:
    """Metadatos suficientes para buscar sin leer el contenido de cada ficha."""

    identifier: str
    code: str
    title: str
    path: str
    warning: str
    aliases: tuple[str, ...] = ()


class ZipLibrary:
    """Biblioteca ZIP con documentos base y fichas clasificadas."""

    def __init__(self, archive: Path, docs: Path):
        """Valida el catalogo y sus rutas sin descomprimir ni leer las fichas."""
        self.archive = archive
        self.docs = docs
        self.entries: dict[str, CatalogEntry] = {}
        self.aliases: dict[str, str] = {}
        self.replacements: dict[str, tuple[str, ...]] = {}
        self.chapter_titles: dict[str, str] = {}
        self._signature = self._file_signature()
        try:
            with ZipFile(archive) as source:
                self._members = {}
                for member in source.infolist():
                    name = member.filename.rstrip("/") if member.is_dir() else member.filename
                    validate_relative(name)
                    if name in self._members:
                        raise ValueError(f"Entrada duplicada en la biblioteca: {name}")
                    self._members[name] = member
                catalogs = [
                    name for name in self._members
                    if PurePosixPath(name).name == "catalogo.json"
                ]
                if len(catalogs) != 1:
                    raise ValueError("El ZIP debe contener un único catalogo.json.")
                catalog_path = PurePosixPath(catalogs[0])
                self._catalog_directory = catalog_path.parent
                catalog = json.loads(self._read_member(source, self._members[catalogs[0]]))
                if (not isinstance(catalog, dict)
                        or catalog.get("formato") != "biblioteca-fichas-uni"
                        or type(catalog.get("version_esquema")) is not int
                        or catalog["version_esquema"] != 1
                        or not isinstance(catalog.get("fichas"), list)):
                    raise ValueError("El formato o la versión de catalogo.json no es compatible.")
                rows = catalog["fichas"]
                if (not rows or type(catalog.get("total_fichas")) is not int
                        or catalog["total_fichas"] != len(rows)):
                    raise ValueError("El total de fichas del catálogo no coincide con su contenido.")
                index = catalog.get("indice")
                if not isinstance(index, str):
                    raise ValueError("El catálogo no declara su índice de lectura.")
                self._catalog_member(index)
                identifiers = set()
                for row in rows:
                    if not isinstance(row, dict) or any(
                        not isinstance(row.get(key), str) or not row[key].strip()
                        for key in ("id", "codigo_uni", "titulo", "archivo")
                    ):
                        raise ValueError("El catálogo contiene una ficha incompleta.")
                    code = row["codigo_uni"]
                    if not re.fullmatch(r"\d{2}[A-Z]*", code):
                        raise ValueError(f"Código UNI no válido: {code}")
                    member = self._catalog_member(row["archivo"])
                    if PurePosixPath(member).suffix.lower() != ".md":
                        raise ValueError(f"La ficha no es Markdown: {member}")
                    relative = "UNI/" + row["archivo"]
                    levels = [
                        part.split("_", 1)[0].upper()
                        for part in PurePosixPath(relative).parts[1:-1]
                    ]
                    if (not levels or not code.startswith(levels[-1])
                            or any(not re.fullmatch(r"\d{2}[A-Z]*", level) for level in levels)
                            or any(not child.startswith(parent) for parent, child in zip(levels, levels[1:]))):
                        raise ValueError(f"Clasificación de ficha no válida: {relative}")
                    chapter = row.get("capitulo")
                    if chapter is not None:
                        if (not isinstance(chapter, dict) or chapter.get("codigo") != code[:2]
                                or not isinstance(chapter.get("titulo"), str)
                                or not chapter["titulo"].strip()):
                            raise ValueError(f"Capítulo de ficha no válido: {relative}")
                        chapter_title = chapter["titulo"].strip()
                        previous = self.chapter_titles.setdefault(code[:2], chapter_title)
                        if previous != chapter_title:
                            raise ValueError(f"Títulos de capítulo contradictorios: {code[:2]}")
                    if row["id"] in identifiers or relative in self.entries:
                        raise ValueError(f"Ficha duplicada en el catálogo: {row['id']}")
                    selection = row.get("seleccion", {})
                    if not isinstance(selection, dict):
                        raise ValueError(f"Datos de selección no válidos: {relative}")
                    aliases = row.get("rutas_anteriores", [])
                    if not isinstance(aliases, list) or any(
                        not isinstance(alias, str) or not alias.startswith("UNI/")
                        for alias in aliases
                    ):
                        raise ValueError(f"Alias de ficha no válidos: {relative}")
                    for alias in aliases:
                        validate_relative(alias)
                        if alias in self.aliases and self.aliases[alias] != relative:
                            raise ValueError(f"Alias de ficha duplicado: {alias}")
                        self.aliases[alias] = relative
                    warning = (
                        "Revisar operación, material y uso antes de incorporar la ficha."
                        if selection.get("requiere_confirmacion") else ""
                    )
                    self.entries[relative] = CatalogEntry(
                        row["id"], code, row["titulo"], relative, warning, tuple(aliases),
                    )
                    identifiers.add(row["id"])
                if any(alias in self.entries and alias != target for alias, target in self.aliases.items()):
                    raise ValueError("Un alias de ficha coincide con otra ficha del catálogo.")
                retired = catalog.get("fichas_sustituidas", [])
                if not isinstance(retired, list):
                    raise ValueError("Las sustituciones de fichas no son válidas.")
                for record in retired:
                    if not isinstance(record, dict):
                        raise ValueError("Sustitución de ficha no válida.")
                    paths, targets = record.get("rutas"), record.get("destinos")
                    if (not isinstance(paths, list) or not paths
                            or not isinstance(targets, list) or not targets
                            or any(not isinstance(target, str) or target not in self.entries for target in targets)):
                        raise ValueError("Las rutas de sustitución no son válidas.")
                    for path in paths:
                        if not isinstance(path, str) or not path.startswith("UNI/"):
                            raise ValueError("Ruta de ficha sustituida no válida.")
                        validate_relative(path)
                        if path in self.entries or path in self.aliases or path in self.replacements:
                            raise ValueError(f"Ruta de ficha sustituida duplicada: {path}")
                        self.replacements[path] = tuple(targets)
        except (BadZipFile, RuntimeError, NotImplementedError, UnicodeError) as exc:
            raise ValueError(f"No se pudo leer la biblioteca ZIP: {exc}") from exc

    def _file_signature(self) -> tuple[int, int]:
        """Detecta sustituciones de biblioteca mientras hay un asistente abierto."""
        info = self.archive.stat()
        return info.st_size, info.st_mtime_ns

    def _catalog_member(self, relative: str) -> str:
        """Comprueba que una ruta del catalogo apunta a un archivo del ZIP."""
        validate_relative(relative)
        name = (self._catalog_directory / relative).as_posix()
        member = self._members.get(name)
        if member is None or member.is_dir():
            raise ValueError(f"No se encuentra el archivo del catálogo: {relative}")
        if member.file_size > MAX_RESOURCE_BYTES or member.flag_bits & 1:
            raise ValueError(f"Recurso demasiado grande o cifrado: {relative}")
        return name

    @staticmethod
    def _read_member(source: ZipFile, member: ZipInfo) -> str:
        """Limita la lectura y comprueba CRC y codificacion UTF-8 del recurso."""
        if member.file_size > MAX_RESOURCE_BYTES or member.flag_bits & 1:
            raise ValueError(f"Recurso demasiado grande o cifrado: {member.filename}")
        with source.open(member) as handle:
            content = handle.read(MAX_RESOURCE_BYTES + 1)
        if len(content) > MAX_RESOURCE_BYTES:
            raise ValueError(f"Recurso demasiado grande: {member.filename}")
        return content.decode("utf-8-sig")

    def read_sheet(self, relative: str) -> str:
        """Lee solo una ficha seleccionada."""
        relative = self.resolve_sheet(relative)
        entry = self.entries.get(relative)
        if entry is None:
            raise ValueError(f"La ficha no figura en catalogo.json: {relative}")
        if self._file_signature() != self._signature:
            raise ValueError("La biblioteca ha cambiado. Cierra y vuelve a abrir PPTP.")
        name = self._catalog_member(relative.removeprefix("UNI/"))
        try:
            with ZipFile(self.archive) as source:
                member = source.getinfo(name)
                with source.open(member) as handle:
                    content = handle.read(MAX_RESOURCE_BYTES + 1)
                if len(content) > MAX_RESOURCE_BYTES:
                    raise ValueError(f"Ficha demasiado grande: {relative}")
        except (BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise ValueError(f"No se pudo leer la ficha {relative}: {exc}") from exc
        return content.decode("utf-8-sig")

    def resolve_sheet(self, relative: str) -> str:
        """Traduce rutas anteriores a la ficha actual sin perder decisiones de proyecto."""
        return self.aliases.get(relative, relative)

    def resolve_sheets(self, relative: str, code: str = "") -> tuple[str, ...]:
        """Resuelve sustituciones por código sin escoger arbitrariamente un material."""
        targets = self.replacements.get(relative)
        if targets is None:
            return (self.resolve_sheet(relative),)
        code = code.strip().upper().rstrip("#*")
        matching = tuple(
            target for target in targets
            if code and (code.startswith(self.entries[target].code) or self.entries[target].code.startswith(code))
        )
        return matching or targets

    def read_documents(self, prefix: str) -> list[tuple[str, str]]:
        """Lee documentos base del ZIP y conserva el directorio externo como respaldo."""
        if self._file_signature() != self._signature:
            raise ValueError("La biblioteca ha cambiado. Cierra y vuelve a abrir PPTP.")
        members = sorted(
            name for name, info in self._members.items()
            if "/" not in name and name.startswith(prefix)
            and name.lower().endswith(".md") and not info.is_dir()
        )
        if members:
            try:
                with ZipFile(self.archive) as source:
                    return [
                        (name, self._read_member(source, self._members[name]))
                        for name in members
                    ]
            except (BadZipFile, RuntimeError, NotImplementedError, UnicodeError) as exc:
                raise ValueError(f"No se pudieron leer los documentos base del PPTP: {exc}") from exc
        files = sorted(self.docs.glob(f"{prefix}*.md"))
        return [
            (path.name, path.read_text(encoding="utf-8-sig"))
            for path in files
        ]
