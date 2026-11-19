"""Exporta la planificación como una tabla Gantt editable en la plantilla."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from xml.etree import ElementTree

from .._shared.scheduling import PlanActivity, PlanParams

if TYPE_CHECKING:
    from budget_reports import ReportSettings


GANTT_PALETTE = (
    "#1f77b4", "#e4572e", "#29a19c", "#f3a712", "#7b5ea7",
    "#2e86ab", "#c03221", "#5c946e", "#a23b72", "#3d5a80",
)
_MAX_TIMELINE_PERIODS = 10
_GRID_COLOR = "#D9DEE5"


@dataclass(frozen=True)
class _TimelinePeriod:
    start: date
    end: date


@dataclass(frozen=True)
class _ChartRow:
    chapter_index: int
    label: str
    details: str
    measurement: str
    duration: int
    start: date
    end: date
    is_chapter: bool


def _dated_activities(activities: list[PlanActivity]) -> list[tuple[PlanActivity, date, date]]:
    """Conserva las actividades que ya tienen fechas calculadas."""
    return [
        (activity, activity.start_date, activity.end_date)
        for activity in activities
        if activity.start_date is not None and activity.end_date is not None
    ]


def _timeline_periods(start: date, end: date, scale: int) -> list[_TimelinePeriod]:
    """Divide el calendario en un máximo de diez tramos legibles."""
    timeline_start = start - timedelta(days=start.weekday()) if scale == 7 else start
    total_days = (end - timeline_start).days + 1
    if scale == 7:
        weeks = ceil(total_days / 7)
        step = 7 * max(1, ceil(weeks / _MAX_TIMELINE_PERIODS))
    else:
        step = max(1, ceil(total_days / _MAX_TIMELINE_PERIODS))
    return [
        _TimelinePeriod(
            timeline_start + timedelta(days=offset),
            min(end, timeline_start + timedelta(days=offset + step - 1)),
        )
        for offset in range(0, total_days, step)
    ]


def _build_chart_rows(
    dated: list[tuple[PlanActivity, date, date]],
    plan: str,
    currency_formatter: Callable[[float], str],
) -> list[_ChartRow]:
    """Construye filas de capítulo y actividad en el orden de la planificación."""
    chapters: dict[str, list[tuple[PlanActivity, date, date]]] = {}
    for record in dated:
        chapters.setdefault(record[0].chapter, []).append(record)

    rows = []
    for chapter_index, records in enumerate(chapters.values()):
        first = records[0][0]
        start = min(record[1] for record in records)
        end = max(record[2] for record in records)
        chapter_duration = max(record[0].finish_offset for record in records) - min(
            record[0].start_offset for record in records
        )
        if plan == "Resumen":
            rows.append(_ChartRow(
                chapter_index, f"{first.chapter} · {first.chapter_name}",
                f"{start:%d/%m/%Y} – {end:%d/%m/%Y}",
                currency_formatter(sum(record[0].amount for record in records)),
                chapter_duration, start, end, True,
            ))
            continue

        rows.append(_ChartRow(
            chapter_index, f"{first.chapter} · {first.chapter_name}",
            f"{start:%d/%m/%Y} – {end:%d/%m/%Y}",
            currency_formatter(sum(record[0].amount for record in records)),
            chapter_duration, start, end, True,
        ))
        rows.extend(
            _ChartRow(
                chapter_index,
                f"{activity.id} · {activity.code}  {activity.summary}"
                + (" (toda la obra)" if activity.continuous else ""),
                f"{activity_start:%d/%m/%Y} – {activity_end:%d/%m/%Y}",
                f"{_format_quantity(activity.quantity)} {activity.unit}".strip(),
                activity.duration, activity_start, activity_end, False,
            )
            for activity, activity_start, activity_end in records
        )
    return rows


def _format_quantity(value: float) -> str:
    """Formatea una medición con separadores decimales españoles."""
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _describe_duration(
    dated: list[tuple[PlanActivity, date, date]],
) -> str:
    """Resume el plazo total y la actividad de mayor duración."""
    start = min(record[1] for record in dated)
    end = max(record[2] for record in dated)
    working_days = max(activity.finish_offset for activity, _, _ in dated)
    natural_days = (end - start).days + 1
    longest = max((activity for activity, _, _ in dated), key=lambda activity: activity.duration)
    return (
        f"La duración total calculada es de {working_days} días laborables "
        f"({natural_days} días naturales), desde {start:%d/%m/%Y} hasta {end:%d/%m/%Y}. "
        f"La actividad de mayor duración es «{longest.code} — {longest.summary}», "
        f"con {longest.duration} días laborables."
    )


def _light_color(color: str) -> str:
    """Aclara el color de capítulo para distinguir sus celdas descriptivas."""
    values = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    return "#" + "".join(f"{round(value * 0.18 + 255 * 0.82):02X}" for value in values)


def _write_odt_chart(document, rows: list[_ChartRow], periods: list[_TimelinePeriod]) -> None:
    """Añade al ODT una tabla editable con barras temporales coloreadas."""
    from odt_reports import _tag

    content_width = document.layout.content_width

    def add_cell_style(name: str, background: str) -> None:
        """Crea un estilo ODF de celda con fondo y bordes de cuadrícula."""
        if any(style.get(_tag("style:name")) == name for style in document.automatic_styles):
            return
        style = ElementTree.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): name,
            _tag("style:family"): "table-cell",
        })
        ElementTree.SubElement(style, _tag("style:table-cell-properties"), {
            _tag("fo:background-color"): background,
            _tag("fo:border"): f"0.25pt solid {_GRID_COLOR}",
            _tag("fo:padding"): "1pt",
            _tag("style:vertical-align"): "middle",
        })

    for index, color in enumerate(GANTT_PALETTE):
        add_cell_style(f"PlanningChapter{index}", _light_color(color))
        add_cell_style(f"PlanningBar{index}", color)
    add_cell_style("PlanningHeaderCell", "#EDF1F5")
    add_cell_style("PlanningEmptyCell", "#FFFFFF")

    axis_style = ElementTree.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): "PlanningAxis",
        _tag("style:family"): "paragraph",
        _tag("style:parent-style-name"): "TableHeader",
    })
    ElementTree.SubElement(axis_style, _tag("style:text-properties"), {
        _tag("fo:font-size"): "5pt",
    })
    ElementTree.SubElement(axis_style, _tag("style:paragraph-properties"), {
        _tag("fo:text-align"): "center",
    })

    table_name = "PlanningGantt"
    table_style = ElementTree.SubElement(document.automatic_styles, _tag("style:style"), {
        _tag("style:name"): table_name,
        _tag("style:family"): "table",
    })
    ElementTree.SubElement(table_style, _tag("style:table-properties"), {
        _tag("style:width"): f"{content_width:.4f}pt",
        _tag("table:align"): "left",
        _tag("fo:margin-left"): "0pt",
    })
    table = ElementTree.SubElement(document.body, _tag("table:table"), {
        _tag("table:name"): table_name,
        _tag("table:style-name"): table_name,
    })
    width_ratios = [8.6, 1.8, *([1.0] * len(periods))]
    for index, ratio in enumerate(width_ratios):
        column_style_name = f"PlanningColumn{index}"
        column_style = ElementTree.SubElement(document.automatic_styles, _tag("style:style"), {
            _tag("style:name"): column_style_name,
            _tag("style:family"): "table-column",
        })
        ElementTree.SubElement(column_style, _tag("style:table-column-properties"), {
            _tag("style:column-width"): f"{content_width * ratio / sum(width_ratios):.4f}pt",
        })
        ElementTree.SubElement(table, _tag("table:table-column"), {
            _tag("table:style-name"): column_style_name,
        })

    header_rows = ElementTree.SubElement(table, _tag("table:table-header-rows"))
    header = ElementTree.SubElement(header_rows, _tag("table:table-row"))
    header_values = ["ACTIVIDAD / PERIODO", "MEDICIÓN / PEM"] + [
        period.start.strftime("%d/%m") for period in periods
    ]
    for index, value in enumerate(header_values):
        _append_odt_cell(
            header, value, "PlanningHeaderCell", "PlanningAxis" if index >= 2 else "TableHeader",
        )

    for row in rows:
        table_row = ElementTree.SubElement(table, _tag("table:table-row"))
        row_style = f"PlanningChapter{row.chapter_index % len(GANTT_PALETTE)}" if row.is_chapter else (
            "PlanningEmptyCell"
        )
        label = f"{row.label}\n{row.details}"
        _append_odt_cell(table_row, label, row_style, "TableHeader" if row.is_chapter else "TableText")
        _append_odt_cell(
            table_row, f"{row.duration} d", row_style, "TableHeader" if row.is_chapter else "TableText",
        )
        for period in periods:
            is_bar = row.start <= period.end and row.end >= period.start
            style = (
                f"PlanningBar{row.chapter_index % len(GANTT_PALETTE)}"
                if is_bar else "PlanningEmptyCell"
            )
            _append_odt_cell(table_row, "", style, "TableText")


def _append_odt_cell(parent, value: str, cell_style: str, paragraph_style: str) -> None:
    """Añade una celda ODF y sus saltos de línea como contenido editable."""
    from odt_reports import _tag

    cell = ElementTree.SubElement(parent, _tag("table:table-cell"), {
        _tag("table:style-name"): cell_style,
        _tag("office:value-type"): "string",
    })
    paragraph = ElementTree.SubElement(cell, _tag("text:p"), {
        _tag("text:style-name"): paragraph_style,
    })
    lines = value.split("\n")
    paragraph.text = lines[0]
    for line in lines[1:]:
        ElementTree.SubElement(paragraph, _tag("text:line-break")).tail = line


def _pdf_chart_table(layout, rows: list[_ChartRow], periods: list[_TimelinePeriod]):
    """Crea una tabla PDF vectorial con las barras y etiquetas del calendario."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Table, TableStyle
    content_width = layout.content_width

    axis_style = ParagraphStyle(
        "planning-axis", parent=layout.normal, fontSize=5, leading=6, alignment=TA_CENTER,
    )
    data = [[
        layout.text("ACTIVIDAD / PERIODO", layout.bold),
        layout.text("MEDICIÓN / PEM", layout.bold),
        *[layout.text(period.start.strftime("%d/%m"), axis_style) for period in periods],
    ]]
    for row in rows:
        style = layout.bold if row.is_chapter else layout.normal
        data.append([
            layout.text(f"{row.label}\n{row.details}", style),
            layout.text(row.measurement, style),
            *["" for _ in periods],
        ])

    fixed_widths = [content_width * 8.6 / (10.4 + len(periods)),
                    content_width * 1.8 / (10.4 + len(periods))]
    period_width = (content_width - sum(fixed_widths)) / len(periods)
    table = Table(
        data, colWidths=[*fixed_widths, *([period_width] * len(periods))],
        repeatRows=1, splitByRow=1, hAlign="LEFT",
    )
    styles = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor(_GRID_COLOR)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF1F5")),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
    ]
    for row_index, row in enumerate(rows, start=1):
        color = GANTT_PALETTE[row.chapter_index % len(GANTT_PALETTE)]
        if row.is_chapter:
            styles.append(("BACKGROUND", (0, row_index), (1, row_index), colors.HexColor(_light_color(color))))
        for period_index, period in enumerate(periods):
            if row.start <= period.end and row.end >= period.start:
                styles.append((
                    "BACKGROUND", (period_index + 2, row_index), (period_index + 2, row_index),
                    colors.HexColor(color),
                ))
    table.setStyle(TableStyle(styles))
    return table


def write_planning_report(
    activities: list[PlanActivity],
    params: PlanParams,
    target: Path,
    settings: ReportSettings,
    project_title: str,
    currency_formatter: Callable[[float], str],
) -> None:
    """Guarda el Gantt editable en ODT o PDF con la plantilla de informes."""
    from report_layout import ReportLayout

    dated = _dated_activities(activities)
    if not dated:
        raise ValueError("No hay actividades con fechas calculadas para guardar la planificación.")
    rows = _build_chart_rows(dated, params.plan, currency_formatter)
    periods = _timeline_periods(
        min(record[1] for record in dated),
        max(record[2] for record in dated),
        params.scale,
    )
    description = _describe_duration(dated)
    title = "PLAN DE TRABAJO"

    if settings.output_format == "odt":
        from odt_reports import _new_report

        document = _new_report(
            title, project_title, settings, repeat_titles=False,
            footer_title="Planificación", landscape=True, template_title="",
        )
        _write_odt_chart(document, rows, periods)
        document.paragraph(description)
        document.append_signature(settings)
        document.write(target)
        return

    from reportlab.platypus import SimpleDocTemplate
    from reportlab.platypus.doctemplate import LayoutError
    from report_layout import LEFT_MARGIN, RIGHT_MARGIN

    layout = ReportLayout(
        template_path=settings.template_path,
        expediente=settings.expediente, repeat_titles=False,
        footer_title="Planificación", landscape=True,
    )
    top_margin = layout.top_margin("", "")
    bottom_margin = layout.bottom_margin()
    document = SimpleDocTemplate(
        str(target), pagesize=layout.page_size, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN,
        topMargin=top_margin, bottomMargin=bottom_margin, title=title, author=settings.document_author,
    )
    try:
        document.build(
            [_pdf_chart_table(layout, rows, periods), layout.text(description),
             *layout.signature_block(settings)],
            onFirstPage=lambda canvas, doc: layout.header(canvas, doc, title, project_title),
            onLaterPages=lambda canvas, doc: layout.header(canvas, doc, title, project_title),
        )
    except LayoutError as exc:
        raise ValueError(f"La planificación no cabe en la plantilla seleccionada: {exc}") from exc
