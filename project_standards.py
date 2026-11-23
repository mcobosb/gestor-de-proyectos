"""Referencias seleccionables para el control documental del proyecto."""
from collections.abc import Mapping

PROJECT_DISCIPLINES = {
    "civil": "Obra civil y urbanización",
    "building": "Edificación",
    "installations": "Instalaciones",
    "industrial": "Industrial",
    "systems": "Sistemas y tecnologías de la información",
    "other": "Otros ámbitos",
}

# Código, alcance resumido y ficha oficial; no implican certificación ni obligatoriedad.
DOCUMENT_STANDARDS = {
    "une_157001": (
        "UNE 157001:2014",
        "criterios generales para la elaboración formal de los documentos que constituyen un proyecto técnico",
        "https://www.une.org/encuentra-tu-norma/busca-tu-norma/norma?c=N0052985",
    ),
    "iso_21502": (
        "ISO 21502:2020", "orientación sobre la gestión de proyectos",
        "https://www.iso.org/standard/74947.html",
    ),
    "iso_10006": (
        "ISO 10006:2017", "directrices de gestión de la calidad en proyectos",
        "https://www.iso.org/standard/70376.html",
    ),
    "iso_19650_1": (
        "ISO 19650-1:2018", "conceptos y principios de gestión de información BIM en activos construidos",
        "https://www.iso.org/standard/68078.html",
    ),
    "iso_15288": (
        "ISO/IEC/IEEE 15288:2023", "procesos del ciclo de vida de sistemas",
        "https://www.iso.org/standard/81702.html",
    ),
    "iso_29148": (
        "ISO/IEC/IEEE 29148:2018", "ingeniería de requisitos de sistemas y software",
        "https://www.iso.org/standard/72089.html",
    ),
}

DOCUMENT_CONTROL_TEXT = (
    "Esta hoja tiene por objeto identificar los documentos que integran el proyecto y llevar "
    "el registro de su edición, revisión y validación."
)


def validate_selection(value: object, options: Mapping[str, object], label: str) -> list[str]:
    """Valida selecciones persistidas sin aceptar identificadores desconocidos."""
    if not isinstance(value, list) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} debe ser una lista de códigos de texto.")
    if any(key not in options for key in value):
        raise ValueError(f"{label} contiene una selección desconocida.")
    return list(value)


def document_control_text(disciplines: list[str], standards: list[str]) -> str:
    """Redacta el alcance documental sin afirmar cumplimiento o certificación."""
    validate_selection(disciplines, PROJECT_DISCIPLINES, "Los tipos de proyecto")
    validate_selection(standards, DOCUMENT_STANDARDS, "Las referencias normativas")
    parts = [DOCUMENT_CONTROL_TEXT]
    scope = "; ".join(
        "ingeniería industrial" if key == "industrial" else label.lower()
        for key, label in PROJECT_DISCIPLINES.items() if key in disciplines
    )
    if standards:
        references = "; ".join(
            f"la norma {code} ({description})"
            for key, (code, description, _) in DOCUMENT_STANDARDS.items() if key in standards
        )
        prefix = (
            f"Para un proyecto en el ámbito de {scope}, se toman"
            if scope else "Se toman"
        )
        parts.append(f"{prefix} como marco de referencia aplicable {references}.")
        parts.append(
            "Dichas referencias orientan la estructura y el control documental del trabajo, "
            "sin que su mera selección implique la acreditación formal del cumplimiento "
            "normativo ni la certificación del proyecto."
        )
    elif scope:
        parts.append(f"El proyecto se desarrolla en el ámbito de {scope}.")
    return " ".join(parts)
