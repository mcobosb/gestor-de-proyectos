"""Marcas persistentes compartidas, independientes de las herramientas activas."""
import re
import unicodedata

RCD_BUDGET_SOURCE = "RCD:"
TRANSPORT_PREFIX = "[RCD demolición] "


def matches_tool_chapter(tool: str, chapter: str, title: str) -> bool:
    """Reconoce capítulos económicos de RCDs o Seguridad y Salud."""
    if tool != "rcd":
        return " ".join(title.casefold().split()) == "seguridad y salud"

    normalized = unicodedata.normalize("NFKD", f"{chapter} {title}".casefold())
    text = re.sub(
        r"[^a-z0-9]+", " ",
        "".join(char for char in normalized if not unicodedata.combining(char)),
    ).strip()
    code = re.sub(r"[^a-z0-9]", "", chapter.casefold())
    return bool(
        re.search(r"\b(?:rcds?|residuos?|escombros?)\b", text)
        or re.search(r"\br\s+c\s+d\s*s?\b", text)
        or re.fullmatch(r"rcds?\d*", code)
    )
