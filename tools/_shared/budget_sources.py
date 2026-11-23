"""Marcas persistentes compartidas, independientes de las herramientas activas."""
import re
import unicodedata

RCD_BUDGET_SOURCE = "RCD:"
TRANSPORT_PREFIX = "[RCD demolición] "


def matches_tool_chapter(tool: str, chapter: str, title: str) -> bool:
    """Reconoce capítulos económicos de RCDs o Seguridad y Salud por su código y título."""
    normalized = unicodedata.normalize("NFKD", f"{chapter} {title}".casefold())
    text = re.sub(
        r"[^a-z0-9]+", " ",
        "".join(char for char in normalized if not unicodedata.combining(char)),
    ).strip()
    code = re.sub(r"[^a-z0-9]", "", chapter.casefold())
    if tool == "rcd":
        return bool(
            re.search(r"\b(?:rcds?|residuos?|escombros?)\b", text)
            or re.search(r"\br\s+c\s+d\s*s?\b", text)
            or re.fullmatch(r"rcds?\d*", code)
        )
    return bool(
        re.search(r"\b(?:seguridad|salud)\b|\bprevencion\b.*\briesgos\b|\bs\s+(?:y\s+)?s\b", text)
        or re.fullmatch(r"(?:ss|sys)\d*", code)
    )
