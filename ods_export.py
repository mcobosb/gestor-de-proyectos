"""Utilidades para exportar hojas de cálculo editables en formato ODS."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path


ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"


def write_ods(target: Path, sheets: list[dict]) -> None:
    """Escribe hojas con valores, anchos de columna y formato numérico opcional."""
    from odf.number import Number, NumberStyle
    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.style import (
        Style, TableCellProperties, TableColumnProperties, TextProperties,
    )
    from odf.table import Table, TableCell, TableColumn, TableRow
    from odf.text import P

    document = OpenDocumentSpreadsheet()
    header_style = Style(name="ColumnHeading", family="table-cell")
    header_style.addElement(TableCellProperties(backgroundcolor="#1F4E78", padding="0.08in"))
    header_style.addElement(TextProperties(fontweight="bold", color="#FFFFFF"))
    document.automaticstyles.addElement(header_style)

    numeric_styles = {}
    for decimals in {precision for sheet in sheets
                     for precision in sheet.get("number_formats", {}).values()}:
        number_style = NumberStyle(name=f"Number{decimals}")
        number_style.addElement(Number(
            decimalplaces=str(decimals), minintegerdigits="1", grouping="true",
        ))
        document.automaticstyles.addElement(number_style)
        cell_style = Style(
            name=f"NumberCell{decimals}", family="table-cell",
            datastylename=f"Number{decimals}",
        )
        document.automaticstyles.addElement(cell_style)
        numeric_styles[decimals] = f"NumberCell{decimals}"

    width_styles = {}
    for sheet in sheets:
        for width in sheet.get("widths", ()):
            if width not in width_styles:
                style = Style(name=f"ColumnWidth{len(width_styles) + 1}", family="table-column")
                style.addElement(TableColumnProperties(columnwidth=f"{max(0.8, width * 0.22):.2f}cm"))
                document.automaticstyles.addElement(style)
                width_styles[width] = style.getAttribute("name")

    for sheet_data in sheets:
        table = Table(name=sheet_data["name"])
        document.spreadsheet.addElement(table)
        rows = sheet_data["rows"]
        widths = sheet_data.get("widths", ())
        for column_index in range(max(len(widths), max((len(row) for row in rows), default=0))):
            column = TableColumn()
            if column_index < len(widths):
                column.setAttribute("stylename", width_styles[widths[column_index]])
            table.addElement(column)

        number_formats = sheet_data.get("number_formats", {})
        for row_index, values in enumerate(rows):
            row = TableRow()
            table.addElement(row)
            for column_index, value in enumerate(values):
                cell = TableCell()
                if row_index == 0 and sheet_data.get("header", False):
                    cell.setAttribute("stylename", "ColumnHeading")
                elif column_index in number_formats:
                    cell.setAttribute("stylename", numeric_styles[number_formats[column_index]])
                paragraph = P()
                if isinstance(value, bool):
                    cell.setAttribute("valuetype", "boolean")
                    cell.setAttribute("booleanvalue", "true" if value else "false")
                    paragraph.addText("TRUE" if value else "FALSE")
                elif isinstance(value, (int, float, Decimal)):
                    cell.setAttribute("valuetype", "float")
                    cell.setAttribute("value", str(float(value)))
                    paragraph.addText(str(value))
                elif value is not None:
                    cell.setAttribute("valuetype", "string")
                    paragraph.addText(str(value))
                else:
                    cell.setAttribute("valuetype", "string")
                cell.addElement(paragraph)
                row.addElement(cell)

    document.save(str(target))
