"""Vínculos verificables a disposiciones, normas UNE y notas técnicas."""
from __future__ import annotations

from functools import lru_cache
import re
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from reference_search import load_reference_entries
from tools._library_markdown import link_runs


_LAWS = {
    "Ley 7/2022": ("l", "2022/04/08", "7"),
    "Ley 31/1995": ("l", "1995/11/08", "31"),
    "Ley 21/2013": ("l", "2013/12/09", "21"),
    "Ley 37/2003": ("l", "2003/11/17", "37"),
    "Ley 32/2006": ("l", "2006/10/18", "32"),
    "Ley 9/2017": ("l", "2017/11/08", "9"),
    "Real Decreto 105/2008": ("rd", "2008/02/01", "105"),
    "Real Decreto 553/2020": ("rd", "2020/06/02", "553"),
    "Real Decreto 646/2020": ("rd", "2020/07/07", "646"),
    "Real Decreto 396/2006": ("rd", "2006/03/31", "396"),
    "Real Decreto 108/1991": ("rd", "1991/02/01", "108"),
    "Real Decreto 180/2015": ("rd", "2015/02/17", "180"),
    "Real Decreto 110/2015": ("rd", "2015/02/20", "110"),
    "Real Decreto 1055/2022": ("rd", "2022/12/27", "1055"),
    "Real Decreto 9/2005": ("rd", "2005/01/14", "9"),
    "Real Decreto 1627/1997": ("rd", "1997/10/24", "1627"),
    "Real Decreto 486/1997": ("rd", "1997/04/14", "486"),
    "Real Decreto 1432/2008": ("rd", "2008/08/29", "1432"),
    "Real Decreto 39/1997": ("rd", "1997/01/17", "39"),
    "Real Decreto 171/2004": ("rd", "2004/01/30", "171"),
    "Real Decreto 1109/2007": ("rd", "2007/08/24", "1109"),
    "Real Decreto 1215/1997": ("rd", "1997/07/18", "1215"),
    "Real Decreto 773/1997": ("rd", "1997/05/30", "773"),
    "Real Decreto 485/1997": ("rd", "1997/04/14", "485"),
    "Real Decreto 487/1997": ("rd", "1997/04/14", "487"),
    "Real Decreto 614/2001": ("rd", "2001/06/08", "614"),
    "Real Decreto 374/2001": ("rd", "2001/04/06", "374"),
    "Real Decreto 286/2006": ("rd", "2006/03/10", "286"),
    "Real Decreto 1311/2005": ("rd", "2005/11/04", "1311"),
    "Real Decreto 1644/2008": ("rd", "2008/10/10", "1644"),
    "Real Decreto 842/2002": ("rd", "2002/08/02", "842"),
    "Real Decreto 470/2021": ("rd", "2021/06/29", "470"),
    "Real Decreto 1428/2003": ("rd", "2003/11/21", "1428"),
    "Real Decreto 3/2023": ("rd", "2023/01/10", "3"),
    "Orden APM/1007/2017": ("o", "2017/10/10", "apm1007"),
}
LAW_URLS = {
    name: f"https://www.boe.es/eli/es/{kind}/{date}/{number}/con"
    for name, (kind, date, number) in _LAWS.items()
}
LAW_URLS.update({
    name.replace("Real Decreto", "RD"): url
    for name, url in tuple(LAW_URLS.items()) if name.startswith("Real Decreto")
})
LAW_URLS["Decisión 2000/532/CE"] = "https://eur-lex.europa.eu/eli/dec/2000/532/oj?locale=es"
LAW_URLS.update({
    "Código Estructural": "https://www.boe.es/eli/es/rd/2021/06/29/470/con",
    "REBT": "https://www.boe.es/eli/es/rd/2002/08/02/842/con",
    "CTE DB-SUA 1": "https://www.codigotecnico.org/pdf/Documentos/SUA/DBSUA.pdf",
    "CTE DB-SUA": "https://www.codigotecnico.org/pdf/Documentos/SUA/DBSUA.pdf",
    "CTE DB-SE-C": "https://www.codigotecnico.org/pdf/Documentos/SE/DBSE-C.pdf",
    "PG-3": "https://www.boe.es/buscar/legislacion.php?" + urlencode({
        "campo[2]": "DOC",
        "dato[2]": '"Pliego de Prescripciones Técnicas Generales" "carreteras"',
        "checkbox_solo_tit": "S", "accion": "Buscar",
    }),
})
_LAW_LOOKUP = {name.casefold(): url for name, url in LAW_URLS.items()}
_CITATION = re.compile(
    r"\b(?:"
    + "|".join(re.escape(name) for name in sorted(LAW_URLS, key=len, reverse=True))
    + r"|(?:Real Decreto Legislativo|Real Decreto|RD|Ley|Orden(?:[ \t]+[A-Z]+)?)"
      r"[ \t]+\d+/(?:19|20)\d{2}"
      r"|BOE-A-\d{4}-\d+|NTP[ \t]+\d+|UNE(?:-EN)?(?:[ \t]+(?:ISO|IEC))?[ \t]+\d+(?:-\d+)*"
      r"(?::\d{4}(?:\+A\d+:\d{4})?(?:/(?:AC|ERRATUM):\d{4}|[ \t]+ERRATUM:\d{4})?)?)"
      r"(?![\w/:\-+])",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _une_links() -> dict[str, str]:
    """Indexa ediciones exactas y designaciones con una única edición vigente."""
    versions: dict[str, set[str]] = {}
    current: dict[str, set[str]] = {}
    for entry in load_reference_entries("UNE"):
        code = entry.code.upper().strip()
        versions.setdefault(code, set()).add(entry.url)
        if entry.status.casefold() == "vigente" and re.fullmatch(
            r"[^:]+:\d{4}(?:\+A\d+:\d{4})?", code
        ):
            current.setdefault(code.split(":", 1)[0], set()).add(entry.url)
    une = {code: next(iter(urls)) for code, urls in versions.items() if len(urls) == 1}
    une.update({code: next(iter(urls)) for code, urls in current.items() if len(urls) == 1})
    return une


@lru_cache(maxsize=1)
def _ntp_links() -> dict[str, str]:
    """Indexa los enlaces oficiales de las NTP."""
    entries: dict[str, set[str]] = {}
    for entry in load_reference_entries("NTP"):
        entries.setdefault(entry.code.upper(), set()).add(entry.url)
    return {code: next(iter(urls)) for code, urls in entries.items() if len(urls) == 1}


def normative_runs(text: str):
    """Conserva enlaces explícitos y enlaza únicamente citas identificadas sin ambigüedad."""
    for part, explicit_url in link_runs(text):
        if explicit_url:
            yield part, explicit_url
            continue
        cursor = 0
        for match in _CITATION.finditer(part):
            citation = match.group()
            url = _LAW_LOOKUP.get(citation.casefold(), "")
            if citation.upper().startswith("BOE-A-"):
                url = f"https://www.boe.es/buscar/doc.php?id={citation.upper()}"
            if not url and citation.upper().startswith(("UNE", "NTP")):
                key = re.sub(r"\s+", " ", citation.upper())
                url = (_ntp_links() if key.startswith("NTP ") else _une_links()).get(key, "")
            elif not url:
                query = re.sub(r"^RD\b", "Real Decreto", citation, flags=re.IGNORECASE)
                url = "https://www.boe.es/buscar/legislacion.php?" + urlencode({
                    "campo[2]": "DOC", "dato[2]": f'"{query}"',
                    "checkbox_solo_tit": "S", "accion": "Buscar",
                })
            if not url:
                continue
            yield part[cursor:match.start()], ""
            yield citation, url
            cursor = match.end()
        yield part[cursor:], ""


def append_odt_links(parent: ET.Element, text: str) -> None:
    """Añade texto y enlaces ODF sin perder los nodos ya existentes."""
    from odt_reports import _tag

    for value, url in normative_runs(text):
        if not value:
            continue
        if url:
            ET.SubElement(parent, _tag("text:a"), {
                _tag("xlink:href"): url, _tag("xlink:type"): "simple",
            }).text = value
        elif len(parent):
            parent[-1].tail = (parent[-1].tail or "") + value
        else:
            parent.text = (parent.text or "") + value


def link_odt_tree(element: ET.Element) -> None:
    """Enlaza los textos de un árbol ODF sin modificar enlaces preexistentes."""
    from odt_reports import _tag

    if element.tag == _tag("text:a"):
        return
    children = list(element)
    text = element.text or ""
    element.text = None
    for child in children:
        element.remove(child)
    append_odt_links(element, text)
    for child in children:
        tail = child.tail or ""
        child.tail = None
        link_odt_tree(child)
        element.append(child)
        append_odt_links(element, tail)
