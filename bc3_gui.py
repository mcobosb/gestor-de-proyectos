"""Punto de entrada PySide6 y fachada compatible de los modelos presupuestarios."""
from __future__ import annotations

from pathlib import Path

from budget_config import (
    APPLICATION_NAME,
    APPLICATION_RELEASE_DATE,
    APPLICATION_VERSION,
    BUY_ME_A_COFFEE_URL,
    CONCEPT_TYPES,
    DUPLICATE_ITEM_MARKER,
    REFERENCE_PRICE_DATABASES,
    REPOSITORY_URL,
)
from budget_core import (
    Bc3Dataset,
    Concept,
    Measurement,
    MeasurementLine,
    ProjectItem,
    chapter_sort_key,
    currency_label,
    find_bc3_files,
    find_reference_files,
    fmt_eur,
    fmt_number,
    format_chapter_code,
    format_currency,
    format_measurement_lines,
    is_chapter_code,
    norm_code,
    parse_float,
    parse_measurement_lines,
    round_half_up,
)


def main() -> int:
    """Inicia la aplicación PySide6 o el proceso auxiliar de geometría IFC."""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--ifc-geometry-worker":
        from tools.bim._geometry_worker import main as geometry_main

        return geometry_main(sys.argv[2:])
    from bc3_qt import main as qt_main

    return qt_main()


if __name__ == "__main__":
    raise SystemExit(main())
