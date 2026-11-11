"""Incorpora referencias UNE clasificadas por BCCA al apartado normativo del ZIP."""
from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
import re
import unicodedata
from typing import TypedDict
from urllib.parse import urlsplit
from zipfile import ZipFile

from tools._library_archive import update_library
from tools._library_editor import _signature
from tools._library_history import add_revision
from .document import REGULATION_PATTERN


START = "<!-- UNE_REFERENCIAS_INICIO -->"
END = "<!-- UNE_REFERENCIAS_FINAL -->"
LEGACY_START = "<!-- UNE_CSV_BCCA_INICIO -->"
LEGACY_END = "<!-- UNE_CSV_BCCA_FINAL -->"
CONDITION = (
    "Correspondencia temática aproximada del CSV por código BCCA; comprobar material, "
    "operación, uso, edición vigente y exigibilidad por el proyecto o normativa aplicable "
    "antes de incluir. La asociación no acredita aplicación ni obligatoriedad."
)

TOPIC_TITLES = {
    "Accesibilidad_Urbanismo": "Accesibilidad y espacios públicos urbanizados",
    "Urbanismo_Medio_Ambiente": "Urbanismo, ordenación del territorio y medio ambiente",
    "Edificacion": "Edificación",
    "Estructuras_Hormigon_Cementos": "Estructuras, hormigones y cementos",
    "Firmes_Pavimentos": "Firmes y pavimentos",
    "Saneamiento_Abastecimiento": "Saneamiento y abastecimiento de agua",
    "Redes_Electricas_Alumbrado": "Redes eléctricas y alumbrado",
    "Carreteras_Movilidad_Transporte": "Carreteras, movilidad y transporte",
    "Telecomunicaciones": "Telecomunicaciones",
    "Proteccion_Contra_Incendios": "Protección contra incendios",
    "Jardineria_Fertilizantes": "Jardinería y fertilizantes",
    "Gestion_Residuos_Medio_Ambiente": "Gestión de residuos y protección ambiental",
    "Seguridad_Salud_Laboral": "Seguridad y salud laboral",
    "Control_Calidad_NTE": "Control de calidad y normas tecnológicas",
}

ROUTE_TOPICS = (
    (("GESTION DE RESIDUOS", "RESIDUOS"), "Gestion_Residuos_Medio_Ambiente"),
    (("CONTROL DE CALIDAD", "PRUEBAS Y ENSAYOS"), "Control_Calidad_NTE"),
    (("SEGURIDAD Y SALUD", "SEGURIDAD Y PROTECCION"), "Seguridad_Salud_Laboral"),
    (("INCENDIO",), "Proteccion_Contra_Incendios"),
    (("ACCESIBILIDAD", "AMUEBLAMIENTO URBANO", "ESPACIOS PUBLICOS"), "Accesibilidad_Urbanismo"),
    (("JARDINERIA", "FERTILIZANTES", "RIEGO"), "Jardineria_Fertilizantes"),
    (("COMUNICACION", "TELEFONO", "TELECOMUNICACIONES"), "Telecomunicaciones"),
    (("CARRETERAS", "MOVILIDAD", "TRANSPORTE", "INDICADORES DE CIRCULACION"), "Carreteras_Movilidad_Transporte"),
    (("PAVIMENTACIONES", "PAVIMENTOS", "FIRMES", "REVESTIMIENTOS > SUELOS"), "Firmes_Pavimentos"),
    (("SANEAMIENTO", "FONTANERIA", "AGUA", "ALCANTARILLADO", "SALUBRIDAD"), "Saneamiento_Abastecimiento"),
    (("ELECTRICIDAD", "ELECTRICA", "FOTOVOLTAICA", "ENERGIA SOLAR"), "Redes_Electricas_Alumbrado"),
    (("ESTRUCTURAS", "CIMENTACION", "HORMIGON", "CEMENTOS"), "Estructuras_Hormigon_Cementos"),
    (("ACONDICIONAMIENTO DE TERRENOS", "MOVIMIENTO DE TIERRAS", "DEMOLICIONES", "TRABAJOS PREVIOS"),
     "Urbanismo_Medio_Ambiente"),
)

FALLBACK_TOPICS = {
    "01": "Urbanismo_Medio_Ambiente",
    "02": "Urbanismo_Medio_Ambiente",
    "03": "Estructuras_Hormigon_Cementos",
    "04": "Saneamiento_Abastecimiento",
    "05": "Estructuras_Hormigon_Cementos",
    "06": "Edificacion",
    "07": "Edificacion",
    "08": "Edificacion",
    "09": "Edificacion",
    "10": "Edificacion",
    "11": "Edificacion",
    "12": "Edificacion",
    "13": "Edificacion",
    "14": "Edificacion",
    "15": "Accesibilidad_Urbanismo",
    "17": "Gestion_Residuos_Medio_Ambiente",
    "18": "Control_Calidad_NTE",
    "19": "Seguridad_Salud_Laboral",
    "21": "Edificacion",
}


class UneReference(TypedDict):
    """Datos de una edición UNE y sus correspondencias temáticas del CSV."""

    designation: str
    title: str
    url: str
    codes: set[str]
    chapters: dict[str, str]
    topics: dict[str, int]
    publication_date: str


def _topic_for_route(route: str, chapter: str) -> str:
    """Asigna una ruta BCCA al ámbito normativo más específico disponible."""
    normalized = "".join(
        char for char in unicodedata.normalize("NFKD", route).upper()
        if not unicodedata.combining(char)
    )
    for terms, topic in ROUTE_TOPICS:
        if any(term in normalized for term in terms):
            return topic
    return FALLBACK_TOPICS[chapter]


def _reference_topic(entry: UneReference) -> str:
    """Elige el ámbito más frecuente entre las asociaciones BCCA de una UNE."""
    priority = {topic: index for index, topic in enumerate(TOPIC_TITLES)}
    return max(entry["topics"], key=lambda topic: (entry["topics"][topic], -priority[topic]))


def reference_id(code: str) -> str:
    """Identifica de forma estable la designación y edición de una UNE."""
    return re.sub(r"[^A-Z0-9]+", "-", code.upper()).strip("-")


def read_une_csv(path: Path) -> dict[str, UneReference]:
    """Valida asociaciones y reúne una referencia por norma con todos sus prefijos."""
    result: dict[str, UneReference] = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"codigo_bcca", "capitulo", "ruta_bcca", "codigo_UNE", "titulo_UNE", "url_norma"}
        if not required <= set(reader.fieldnames or []):
            raise ValueError("El CSV UNE no contiene las columnas requeridas.")
        for line, row in enumerate(reader, 2):
            if any(not isinstance(row.get(key), str) or not row[key].strip() for key in required):
                raise ValueError(f"Fila UNE {line}: faltan datos obligatorios.")
            code, chapter = row["codigo_bcca"].strip().upper(), row["capitulo"].strip()
            designation, title, url = (row[key].strip() for key in ("codigo_UNE", "titulo_UNE", "url_norma"))
            publication = row.get("fecha", "").strip()
            if publication:
                date.fromisoformat(publication)
            parsed = urlsplit(url)
            if (not re.fullmatch(r"\d{2}[A-Z0-9]+", code) or chapter != code[:2]
                    or chapter not in FALLBACK_TOPICS
                    or not re.match(r"^(?:ESPECIFICACION )?UNE\b", designation)
                    or parsed.scheme != "https"
                    or parsed.netloc != "www.une.org"
                    or any(char in designation + title + url for char in ("\n", "\r"))
                    or "<!--" in title or "{{" in title or "}}" in title):
                raise ValueError(f"Fila UNE {line}: referencia o clasificación no válida.")
            identifier = reference_id(designation)
            chapter_title = row["ruta_bcca"].split(">", 1)[0].strip()
            entry = result.setdefault(identifier, {
                "designation": designation, "title": title, "url": url,
                "codes": set(), "chapters": {}, "topics": {},
                "publication_date": publication,
            })
            if (entry["designation"], entry["title"], entry["url"], entry["publication_date"]) != (designation, title, url, publication):
                raise ValueError(f"Fila UNE {line}: datos contradictorios para {designation}.")
            entry["codes"].add(code)
            previous = entry["chapters"].setdefault(chapter, chapter_title)
            if previous != chapter_title:
                raise ValueError(f"Fila UNE {line}: título de capítulo contradictorio.")
            topic = _topic_for_route(row["ruta_bcca"], chapter)
            entry["topics"][topic] = entry["topics"].get(topic, 0) + 1
    if not result:
        raise ValueError("El CSV UNE no contiene referencias.")
    return result


def classified_markdown(source: str, references: dict[str, UneReference]) -> tuple[str, int]:
    """Incluye UNE en ámbitos legales generales y amplía asociaciones existentes."""
    for start, end in ((LEGACY_START, LEGACY_END), (START, END)):
        if source.count(start) != source.count(end):
            raise ValueError("El bloque UNE importado tiene delimitadores no válidos.")
        source = re.sub(re.escape(start) + r".*?" + re.escape(end), "", source, flags=re.DOTALL)
    pending = dict(references)

    def merge(match: re.Match[str]) -> str:
        """Reutiliza una UNE ya anotada sin duplicarla ni borrar sus correspondencias previas."""
        data = json.loads(match[1])
        designation = re.match(r"[*+-] ((?:ESPECIFICACION )?UNE.*?)\. ", match[2])
        identifier = reference_id(designation[1]) if designation else data["id"]
        entry = pending.pop(identifier, None)
        if entry is None:
            return match[0]
        data["uni"] = sorted(set(data["uni"]) | entry["codes"])
        data["seleccion"] = "revision"
        data["codigo_une"] = entry["designation"]
        data["fecha_publicacion"] = entry["publication_date"]
        data["condicion"] += " " + CONDITION if CONDITION not in data["condicion"] else ""
        return "<!-- NORMATIVA_UNI " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + " -->\n" + match[2]

    source = REGULATION_PATTERN.sub(merge, source)
    groups: dict[str, list[str]] = {}
    for identifier, entry in pending.items():
        groups.setdefault(_reference_topic(entry), []).append(
            "\n".join((
                START,
                "<!-- NORMATIVA_UNI " + json.dumps({
                    "id": identifier, "uni": sorted(entry["codes"]), "seleccion": "revision",
                    "condicion": CONDITION,
                    "codigo_une": entry["designation"],
                    "fecha_publicacion": entry["publication_date"],
                }, ensure_ascii=False, separators=(",", ":")) + " -->",
                f"* {entry['designation']}. {entry['title']} [UNE]({entry['url']}).",
                END,
            ))
        )

    def insert_into_existing_topic(match: re.Match[str]) -> str:
        """Añade las referencias UNE al grupo temático legal que ya existe."""
        marker, body = match[1], match[2]
        entries = groups.pop(marker, None)
        if not entries:
            return match[0]
        return (
            f"<!-- {marker} -->{body.rstrip()}\n\n"
            + "\n\n".join(sorted(entries))
            + f"\n<!-- FIN_{marker} -->"
        )

    group_pattern = re.compile(
        r"<!--\s*(\w+)\s*-->(.*?)<!--\s*FIN_\1\s*-->", flags=re.DOTALL
    )
    source = group_pattern.sub(insert_into_existing_topic, source)

    if groups:
        new_groups = []
        for marker in TOPIC_TITLES:
            entries = groups.pop(marker, None)
            if entries:
                new_groups.extend((
                    f"<!-- {marker} -->", f"## {TOPIC_TITLES[marker]}", "",
                    "\n\n".join(sorted(entries)), f"<!-- FIN_{marker} -->", "",
                ))
        if groups:
            raise ValueError(f"Ámbitos normativos sin título: {', '.join(sorted(groups))}.")
        source = source.rstrip() + "\n\n" + "\n".join(new_groups)
    return source, len(references)


def import_une(archive: Path, csv_path: Path, *, current_confirmed: bool) -> int:
    """Actualiza únicamente el Markdown del apartado 7 mediante el guardado atómico estándar."""
    if not current_confirmed:
        raise ValueError("Confirma que el CSV contiene únicamente UNE vigentes antes de importarlo.")
    references = read_une_csv(csv_path)
    signature = _signature(archive)
    with ZipFile(archive) as source:
        names = [name for name in source.namelist() if re.match(r"^07(?!\d).*\.md$", name)]
        if len(names) != 1:
            raise ValueError("La biblioteca debe contener un único documento del apartado 7.")
        name = names[0]
        original = source.read(name).decode("utf-8-sig")
    updated, count = classified_markdown(original, references)
    if updated != original:
        updated = add_revision(
            updated, "Copilot", date.today().strftime("%d/%m/%Y"),
            "Incorporación de referencias UNE del CSV por BCCA; vigencia del listado "
            "confirmada por su responsable, aplicabilidad pendiente de revisión.",
        )
        update_library(archive, signature, updates={name: updated})
    return count


def main() -> None:
    """Ejecuta la importación verificable del CSV a una biblioteca PPTP."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("csv", type=Path)
    parser.add_argument(
        "--vigentes-confirmadas", action="store_true", required=True,
        help="Confirma que el listado se ha depurado previamente para excluir UNE no vigentes.",
    )
    args = parser.parse_args()
    print(f"Referencias UNE disponibles: {import_une(args.archive, args.csv, current_confirmed=args.vigentes_confirmadas)}")


if __name__ == "__main__":
    main()
