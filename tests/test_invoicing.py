from decimal import Decimal
import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from bc3_gui import Bc3Dataset, BudgetApp, Concept, MeasurementLine, ProjectItem
from invoicing import (
    CostRow, Invoice, InvoiceLine, InvoiceState, InvoicingWindow, amount,
    assign_line, assign_matching_lines, budget_rows, parse_invoice_tables,
    ALL_PERIODS, UNDATED_PERIOD, invoice_month, period_invoices,
    invoice_sort_key, read_invoice, render_invoice_page, total,
)


def create_invoice(path: Path, lines=None):
    rows = [
        ["Obra", "Expediente", "Fecha", "Partida", "Importe (€)"],
        *[
            ["Obra de prueba", "EXP-26", "29/10/2026", code, value]
            for code, value in (lines or [("MAT", "30,00"), ("MO", "20,00")])
        ],
    ]
    table = Table(rows)
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "black")]))
    SimpleDocTemplate(str(path)).build([table])


def set_budget(app):
    dataset = Bc3Dataset.__new__(Bc3Dataset)
    dataset.concepts = {
        "MO": Concept("MO", "h", "Operario", 10, "1"),
        "MAT": Concept("MAT", "kg", "Material", 3, "3"),
        "MAQ": Concept("MAQ", "h", "Equipo", 5, "2"),
        "AUX": Concept("AUX", "ud", "Auxiliar", 8, "0", children=[("MAT", 1), ("MAQ", 1)]),
    }
    app.budget_dataset = dataset
    app.budget_items = [
        ProjectItem("P1", "01#", "m", "Unidad uno", measurement=2, unit_price=35,
                    decomposition=[("MO", 2), ("MAT", 1), ("AUX", 1)],
                    component_prices={"MO": 12}),
        ProjectItem("P2", "01#", "ud", "Sin descomposición", unit_price=50),
    ]
    app.reference_bases = []
    app.chapter_order = ["01#"]
    app.chapter_titles = {}


class InvoiceModelTests(unittest.TestCase):
    def test_accumulated_months_include_previous_years_and_exclude_unknown_dates(self):
        invoices = [
            Invoice("old", "Anterior", date="31/12/2025"),
            Invoice("jan", "Enero", date="2026-01-31"),
            Invoice("feb", "Febrero", date="01.02.2026"),
            Invoice("credit", "Abono", date="2026/02/28"),
            Invoice("future", "Futura", date="01-03-2026"),
            Invoice("unknown", "Sin fecha"),
            Invoice("invalid", "Inválida", date="31/02/2026"),
        ]
        self.assertEqual(invoice_month(invoices[0]), "2025-12")
        self.assertEqual(invoice_month(invoices[-1]), UNDATED_PERIOD)
        self.assertEqual([invoice.id for invoice in period_invoices(invoices, "2026-02")],
                         ["old", "jan", "feb", "credit"])
        self.assertEqual(period_invoices(invoices, ALL_PERIODS), invoices)
        self.assertEqual([invoice.id for invoice in period_invoices(invoices, UNDATED_PERIOD)],
                         ["unknown", "invalid"])
        self.assertEqual(period_invoices(invoices, "2025-01"), [])
        with self.assertRaises(ValueError):
            period_invoices(invoices, "2026-13")

    def test_rcd_budget_lines_have_their_own_kind_and_no_duplicate_components(self):
        app = BudgetApp.__new__(BudgetApp)
        set_budget(app)
        item = ProjectItem(
            "RCD-EARTH", "RCD#", "m3", "Gestión tierras", source="RCD:earth",
            measurement=4, unit_price=10, decomposition=[("MAT", 1)],
        )
        item.set_indirect_costs(3)
        app.budget_items = [item]
        rows, warnings = budget_rows(app)
        self.assertEqual(warnings, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].kind, rows[0].resource, rows[0].budget),
                         ("RCDs", "RCD-EARTH", 40))
        from rcd import TRANSPORT_PREFIX

        app.budget_items = [ProjectItem(
            "RCD-TRANS", "01#", "m3", "Transporte", measurement=5, unit_price=7,
            measurement_lines=[MeasurementLine(comment=TRANSPORT_PREFIX + "Demoliciones", units=5)],
        )]
        rows, warnings = budget_rows(app)
        self.assertEqual(warnings, [])
        self.assertEqual((rows[0].kind, rows[0].budget), ("RCDs", 35))

    def test_invoices_sort_chronologically_with_unknown_dates_last(self):
        invoices = [
            Invoice("blank", "B", date=""),
            Invoice("later", "D", date="01/11/2026"),
            Invoice("earlier", "E", date="31/12/2025"),
            Invoice("iso", "C", date="2026-02-01"),
            Invoice("dash", "F", date="02-02-2026"),
            Invoice("dot", "G", date="03.02.2026"),
            Invoice("slash", "H", date="2026/02/04"),
            Invoice("invalid", "A", date="31/02/2026"),
            Invoice("ambiguous", "Z", date="29/10/2026 | 30/10/2026"),
        ]
        self.assertEqual(
            [invoice.id for invoice in sorted(invoices, key=invoice_sort_key)],
            ["earlier", "iso", "dash", "dot", "slash", "later", "invalid", "blank", "ambiguous"],
        )

    def test_pdf_runtime_dependency_and_packaged_character_maps(self):
        root = Path(__file__).resolve().parent.parent
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pdfplumber>=0.11.10", requirements)
        self.assertIn("pdfminer.six", requirements)
        self.assertIn("pypdfium2>=4.18", requirements)
        build_script = (root / "build_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("--include-package-data=pdfminer", build_script)
        self.assertIn("--include-package-data=pypdfium2_raw", build_script)
        self.assertIn("--noinclude-unittest-mode=nofollow", build_script)

    def test_strict_amounts_and_totals(self):
        for value, expected in (
            ("1.234,56 €", 1234.56), ("1,234.56", 1234.56),
            ("-3,50", -3.5), ("(25,00)", -25), (Decimal("1.005"), 1.01),
        ):
            with self.subTest(value=value):
                self.assertEqual(amount(value), expected)
        for value in ("nan", "inf", "", "abc", True, None, "1e1000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                amount(value)
        self.assertEqual(total([0.1, 0.2, -0.3]), 0)

    def test_nested_resources_prices_and_percentages(self):
        app = BudgetApp.__new__(BudgetApp)
        set_budget(app)
        app.budget_items[0].set_indirect_costs(3)
        rows, warnings = budget_rows(app)
        self.assertFalse(warnings)
        self.assertEqual(
            [(row.resource, row.kind, row.budget) for row in rows[:3]],
            [("MO", "Mano de obra", 48), ("MAT", "Material", 12), ("MAQ", "Maquinaria", 10)],
        )
        self.assertEqual(rows[3].budget, 50)
        self.assertNotIn("%CI", [row.resource for row in rows])

    def test_unavailable_or_circular_resources_are_reported(self):
        app = BudgetApp.__new__(BudgetApp)
        set_budget(app)
        app.budget_items[0].decomposition = [("MISSING", 1)]
        rows, warnings = budget_rows(app)
        self.assertEqual(len(warnings), 1)
        self.assertIn("MISSING", warnings[0])
        self.assertEqual(rows[0].budget, 70)
        app.budget_items[0].decomposition = [("AUX", 1)]
        assert app.budget_dataset is not None
        app.budget_dataset.concepts["AUX"].children = [("AUX", 1)]
        _, warnings = budget_rows(app)
        self.assertIn("circular", warnings[0])

    def test_parser_reads_tables_and_reports_invalid_lines(self):
        table = [
            ["Obra", "Expediente", "Fecha", "Partida", "Importe (€)"],
            ["A", "E", "2026", "MAT", "1.234,56"],
            ["A", "E", "2026", "MO", "abc"],
            ["A", "E", "2026", "MAQ", "-20,00"],
            ["", "", "", "Total", "1.214,56"],
        ]
        lines, warnings = parse_invoice_tables([table], "")
        self.assertEqual([(line.item, line.amount) for line in lines], [("MAT", 1234.56), ("MAQ", -20)])
        self.assertEqual(len(warnings), 1)
        self.assertIn("abc", warnings[0])

    def test_text_columns_without_table_borders(self):
        text = "Obra: Prueba\nExpediente: 123\nFecha: 29/10/2026\nPartida    Importe\nMAT        100,00\nMO         20,00\nTotal      120,00"
        lines, warnings = parse_invoice_tables([], text)
        self.assertEqual([(line.item, line.amount) for line in lines], [("MAT", 100), ("MO", 20)])
        self.assertFalse(warnings)

    def test_matching_is_unique_and_reassignment_does_not_duplicate(self):
        rows = [CostRow("1", item="P1", resource="MAT"), CostRow("2", item="P2", resource="MAT"),
                CostRow("3", item="P1", resource="MO")]
        invoice = Invoice("invoice", "Factura", lines=[
            InvoiceLine("MAT", 100), InvoiceLine("MO", 20), InvoiceLine("P2 / MAT", 15),
            InvoiceLine("desconocido", 5),
        ])
        assign_matching_lines(invoice, rows)
        self.assertEqual([line.row_id for line in invoice.lines], ["", "3", "2", ""])
        self.assertEqual(invoice.amounts, {"3": 20, "2": 15})
        assign_line(invoice, invoice.lines[1], "3")
        self.assertEqual(invoice.amounts["3"], 20)
        assign_line(invoice, invoice.lines[1], "1")
        self.assertEqual(invoice.amounts["3"], 0)
        self.assertEqual(invoice.amounts["1"], 20)

    def test_state_roundtrip_and_validation(self):
        state = InvoiceState("folder", [CostRow("row", budget=100)], [
            Invoice("invoice", "Factura", lines=[InvoiceLine("MAT", 30, "row")], amounts={"row": 30}),
        ])
        self.assertEqual(InvoiceState.from_dict(state.to_dict()), state)
        self.assertEqual(InvoiceState.from_dict({}), InvoiceState())
        for data in (
            [], {"rows": {}}, {"rows": [{"id": ""}]},
            {"rows": [{"id": "r", "budget": "inf"}]},
            {"rows": [{"id": "r"}, {"id": "r"}]},
            {"invoices": [{"id": "i", "amounts": {"unknown": 10}}]},
            {"invoices": [{"id": "i", "lines": [{"item": "A", "amount": 1, "row_id": "unknown"}]}]},
            {"invoices": [{"id": "i", "declared": True}]},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                InvoiceState.from_dict(data)


class InvoicePdfTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def test_real_pdf_extracts_all_five_fields(self):
        path = self.folder / "invoice.pdf"
        create_invoice(path)
        invoice = read_invoice(path)
        self.assertEqual(invoice.work, "Obra de prueba")
        self.assertEqual(invoice.reference, "EXP-26")
        self.assertEqual(invoice.date, "29/10/2026")
        self.assertEqual(invoice.declared, 50)
        self.assertEqual([(line.item, line.amount) for line in invoice.lines], [("MAT", 30), ("MO", 20)])
        self.assertEqual(invoice.notes, "")
        self.assertEqual(len(invoice.digest), 64)

    def test_multiple_pages_are_all_read(self):
        from reportlab.platypus import PageBreak

        path = self.folder / "multi.pdf"
        tables = []
        for number in range(2):
            if number:
                tables.append(PageBreak())
            table = Table([["Partida", "Importe"], ["MAT", "30,00"]])
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, "black")]))
            tables.append(table)
        SimpleDocTemplate(str(path)).build(tables)
        invoice = read_invoice(path)
        self.assertEqual(invoice.declared, 60)
        self.assertEqual(len(invoice.lines), 2)
        self.assertIn("obra", invoice.notes)

    def test_real_text_pdf_without_table_borders(self):
        from reportlab.pdfgen.canvas import Canvas

        path = self.folder / "text.pdf"
        canvas = Canvas(str(path))
        for y, text in ((780, "Obra: Obra de prueba"), (760, "Expediente: EXP-26"),
                        (740, "Fecha: 29/10/2026")):
            canvas.drawString(50, y, text)
        for y, left, right in ((700, "Partida", "Importe"), (680, "MAT", "30,00"),
                               (660, "MO", "20,00"), (640, "Total", "50,00")):
            canvas.drawString(50, y, left)
            canvas.drawString(400, y, right)
        canvas.save()
        invoice = read_invoice(path)
        self.assertEqual(invoice.declared, 50)
        self.assertEqual(invoice.work, "Obra de prueba")
        self.assertEqual(invoice.reference, "EXP-26")
        self.assertEqual(invoice.date, "29/10/2026")
        self.assertFalse(invoice.notes)

    def test_empty_corrupt_and_encrypted_pdfs_are_explicit(self):
        from reportlab.pdfgen.canvas import Canvas
        from pypdf import PdfReader, PdfWriter

        path = self.folder / "blank.pdf"
        canvas = Canvas(str(path))
        canvas.showPage()
        canvas.save()
        self.assertIn("sin texto", read_invoice(path).notes)
        broken = self.folder / "broken.pdf"
        broken.write_bytes(b"not a pdf")
        self.assertIn("No se pudo leer", read_invoice(broken).notes)
        encrypted = self.folder / "encrypted.pdf"
        writer = PdfWriter()
        writer.add_page(PdfReader(path).pages[0])
        writer.encrypt("password")
        writer.write(encrypted)
        self.assertIn("No se pudo leer", read_invoice(encrypted).notes)
        self.assertIn("No se pudo leer", read_invoice(self.folder / "missing.pdf").notes)


class InvoiceWindowTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        with (
            patch("bc3_gui.user_data_directory", return_value=self.folder),
            patch.object(BudgetApp, "_read_recent_projects", return_value=[]),
            patch.object(self.root, "state"),
        ):
            self.app = BudgetApp(self.root)
        set_budget(self.app)
        self.app.open_invoicing()
        window = self.app._invoicing_window
        assert isinstance(window, InvoicingWindow)
        self.window = window

    def start(self):
        self.window.folder.set(str(self.folder))
        self.window.data.folder = str(self.folder)
        self.window.scan()
        deadline = time.monotonic() + 15
        while self.window._scanning and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.window._scanning)
        self.root.update()

    def submit_form(self, title: str, values: dict[str, str]):
        dialog = next(child for child in self.window.winfo_children()
                      if isinstance(child, tk.Toplevel) and child.title() == title)
        frame = dialog.winfo_children()[0]
        for label, value in values.items():
            widget = next(child for child in frame.winfo_children()
                          if isinstance(child, ttk.Label) and child.cget("text") == label)
            entry = frame.grid_slaves(row=widget.grid_info()["row"], column=1)[0]
            entry.delete(0, "end")
            entry.insert(0, value)
        next(child for child in frame.winfo_children() if isinstance(child, ttk.Button)).invoke()

    def test_filters_and_accumulated_periods_preserve_rows_assignments_and_totals(self):
        self.start()
        window = self.window
        window.data.rows = [
            CostRow("mat", item="P1", resource="MAT", kind="Material", budget=100),
            CostRow("maq", item="P1", resource="MAQ", kind="Maquinaria", budget=200),
            CostRow("rcd", item="RCD1", resource="RCD1", kind="RCDs", budget=300),
            CostRow("lab", item="P1", resource="MO", kind="Mano de obra", budget=50),
        ]
        window.data.invoices = [
            Invoice("i1", "Enero", date="31/01/2026", amounts={"mat": 30, "rcd": 20}),
            Invoice("i2", "Febrero", date="2026-02-28", amounts={"mat": 10, "maq": 70}),
            Invoice("i3", "Marzo", date="01/03/2026", amounts={"mat": -5, "rcd": 100}),
            Invoice("i4", "Sin fecha", amounts={"mat": 7}),
        ]
        window._refresh()
        original = window.data.to_dict()
        for kind, row_id in (("Material", "mat"), ("Maquinaria", "maq"), ("RCDs", "rcd")):
            window.filter_buttons[kind].invoke()
            self.assertEqual(window.tree.get_children(), (row_id,))
            self.assertEqual(window.totals.get_children(), (row_id,))
            self.assertEqual(sum(var.get() for var in window.filter_variables.values()), 1)
        window.filter_buttons["Material"].invoke()
        window.period_selector.set("2026-02")
        window._refresh()
        self.assertEqual(window.totals.item("mat", "values"), ("100,00 €", "40,00 €", "60,00 €"))
        self.assertEqual(window._visible_invoice_ids, ["i1"])
        self.assertEqual(window.tree.cget("columns")[-1:], ("i1",))
        self.assertEqual(window.tree.heading("i1", "text"), "31/01/2026")
        self.assertIn("Sin fecha válida excluidas: 1", window.footer.cget("text"))
        window.period_selector.set("2026-03")
        window._refresh()
        self.assertEqual(window.totals.item("mat", "values"), ("100,00 €", "35,00 €", "65,00 €"))
        window.period_selector.set(UNDATED_PERIOD)
        window._refresh()
        self.assertEqual(window.totals.item("mat", "values"), ("100,00 €", "7,00 €", "93,00 €"))
        window.period_selector.set(ALL_PERIODS)
        window._refresh()
        self.assertEqual(window.totals.item("mat", "values"), ("100,00 €", "42,00 €", "58,00 €"))
        window.filter_buttons["Material"].invoke()
        self.assertEqual(window.tree.get_children(), ("mat", "maq", "rcd", "lab"))
        self.assertEqual(window.data.to_dict(), original)
        self.assertEqual(InvoiceState.from_dict(original).to_dict(), original)

    def test_associate_invoice_preselects_the_exact_filtered_row_and_reassigns(self):
        create_invoice(self.folder / "invoice.pdf", [("unknown", "30,00")])
        self.start()
        window = self.window
        material = next(row for row in window.data.rows if row.kind == "Material")
        equipment = next(row for row in window.data.rows if row.kind == "Maquinaria")
        window.filter_buttons["Maquinaria"].invoke()
        window.tree.selection_set(equipment.id)
        window.review_lines()
        dialog = next(child for child in window.winfo_children() if isinstance(child, tk.Toplevel))
        selector = next(child for child in dialog.winfo_children() if isinstance(child, ttk.Combobox))
        self.assertEqual(selector.current(), window.data.rows.index(equipment))
        container = next(child for child in dialog.winfo_children() if isinstance(child, ttk.Frame))
        tree = next(child for child in container.winfo_children() if isinstance(child, ttk.Treeview))
        buttons = next(child for child in dialog.winfo_children()
                       if isinstance(child, ttk.Frame) and child is not container)
        assign = next(child for child in buttons.winfo_children()
                      if isinstance(child, ttk.Button) and child.cget("text") == "Asignar al coste seleccionado")
        tree.selection_set("0")
        assign.invoke()
        invoice = window.data.invoices[0]
        self.assertEqual(invoice.lines[0].row_id, equipment.id)
        self.assertEqual(window.totals.item(equipment.id, "values")[1], "30,00 €")
        tree.selection_set("0")
        selector.current(window.data.rows.index(material))
        assign.invoke()
        self.assertEqual(invoice.amounts[equipment.id], 0)
        self.assertEqual(invoice.amounts[material.id], 30)
        self.assertEqual(invoice.lines[0].row_id, material.id)
        self.assertEqual(window.tree.get_children(), (equipment.id,))
        project = self.folder / "asignaciones.json"
        self.assertTrue(self.app._write_project_file(project))
        self.assertTrue(self.app._load_project_file(project))
        restored = InvoiceState.from_dict(self.app.invoicing_state)
        self.assertEqual(restored.invoices[0].lines[0].row_id, material.id)
        self.assertEqual(restored.invoices[0].amounts[material.id], 30)

    def test_accumulated_overruns_empty_filters_and_translated_periods(self):
        from localization import set_language, translate

        self.start()
        window = self.window
        material = next(row for row in window.data.rows if row.kind == "Material")
        window.data.invoices = [
            Invoice("i", "Factura", date="31/12/2025", amounts={material.id: 50}),
        ]
        window._refresh()
        window.filter_buttons["Material"].invoke()
        window.period_selector.set("2025-12")
        window._refresh()
        self.assertEqual(window.totals.item(material.id, "values"),
                         ("12,00 €", "50,00 €", "-38,00 €"))
        self.assertIn("over", window.totals.item(material.id, "tags"))
        self.assertIn("Previsto: 12,00 €", window.footer.cget("text"))
        window.filter_buttons["RCDs"].invoke()
        self.assertEqual(window.tree.get_children(), ())
        self.assertEqual(window.totals.get_children(), ())
        self.assertIn("Previsto: 0,00 €", window.footer.cget("text"))
        set_language("en")
        self.addCleanup(set_language, "es")
        window.period_selector.set(translate(ALL_PERIODS))
        window._refresh()
        self.assertEqual(window._visible_invoice_ids, ["i"])
        self.assertIn(translate(UNDATED_PERIOD), window.period_selector.cget("values"))
        window.period_selector.set(translate(UNDATED_PERIOD))
        window._refresh()
        self.assertEqual(window._visible_invoice_ids, ["i"])
        self.assertEqual(window.totals.heading("billed", "text"), translate("Sin fecha"))

    def test_period_cell_edits_use_visible_invoice_identity_and_date_changes_recompute(self):
        self.start()
        window = self.window
        row = window.data.rows[0]
        window.data.invoices = [
            Invoice("dated", "Fechada", date="01/02/2026", amounts={row.id: 20}),
            Invoice("undated", "Sin fecha", amounts={row.id: 7}),
        ]
        window._refresh()
        window.period_selector.set(UNDATED_PERIOD)
        window.select_invoice("undated")
        window._commit_cell("undated", row.id, "9")
        self.assertEqual(window.data.invoices[0].amounts[row.id], 20)
        self.assertEqual(window.data.invoices[1].amounts[row.id], 9)
        window.period_selector.set("2026-02")
        window._refresh()
        window.data.invoices[1].date = "31/01/2026"
        window._refresh()
        self.assertEqual(window.totals.item(row.id, "values")[1], "29,00 €")
        self.assertEqual(window._visible_invoice_ids, ["undated"])

    def test_saved_rcd_costs_are_classified_without_changing_ids_or_assignments(self):
        self.app.budget_items.append(ProjectItem(
            "RCD1", "RCD#", "m3", "Tierras", source="RCD:earth",
        ))
        self.window.data.rows = [
            CostRow("legacy", chapter="RCD#", item="RCD1", kind="Otro", budget=100),
        ]
        self.window.data.invoices = [
            Invoice("i", "Factura", amounts={"legacy": 35}),
        ]
        state = self.window.data
        self.window.destroy()
        self.app.invoicing_state = state.to_dict()
        self.app.open_invoicing()
        self.window = self.app._invoicing_window
        self.start()
        self.window.filter_buttons["RCDs"].invoke()
        self.assertEqual(self.window.tree.get_children(), ("legacy",))
        self.assertEqual(self.window.totals.item("legacy", "values"),
                         ("100,00 €", "35,00 €", "65,00 €"))
        self.assertEqual(self.window.data.invoices[0].amounts, {"legacy": 35})

    def test_folder_continue_pdf_matrix_totals_and_project_roundtrip(self):
        create_invoice(self.folder / "first.pdf")
        create_invoice(self.folder / "second.PDF", [("MAQ", "12,00"), ("unknown", "5,00")])
        self.start()
        self.assertEqual(len(self.window.data.invoices), 2)
        rows = self.window.data.rows
        self.assertEqual(len(rows), 4)
        material = next(row for row in rows if row.resource == "MAT")
        equipment = next(row for row in rows if row.resource == "MAQ")
        self.assertEqual(self.window.totals.item(material.id, "values"), ("12,00 €", "30,00 €", "-18,00 €"))
        self.assertEqual(self.window.totals.item(equipment.id, "values"), ("10,00 €", "12,00 €", "-2,00 €"))
        self.assertIn("over", self.window.totals.item(equipment.id, "tags"))
        self.assertEqual(sum(not line.row_id for invoice in self.window.data.invoices for line in invoice.lines), 1)
        project = self.folder / "project.json"
        self.assertTrue(self.app._write_project_file(project))
        saved = self.app.invoicing_state
        self.assertTrue(self.app._load_project_file(project))
        self.assertFalse(self.window.winfo_exists())
        self.assertEqual(self.app.invoicing_state, saved)
        self.app.open_invoicing()
        window = self.app._invoicing_window
        assert isinstance(window, InvoicingWindow)
        self.assertEqual(window.folder.get(), str(self.folder.resolve()))

    def test_rescan_does_not_duplicate_or_overwrite_and_warns_changed_file(self):
        path = self.folder / "invoice.pdf"
        create_invoice(path)
        self.start()
        invoice = self.window.data.invoices[0]
        first_row = self.window.data.rows[0]
        invoice.amounts[first_row.id] = 123
        self.window.scan()
        self.wait_scan()
        self.assertEqual(len(self.window.data.invoices), 1)
        self.assertEqual(invoice.amounts[first_row.id], 123)
        copy = self.folder / "copy.pdf"
        copy.write_bytes(path.read_bytes())
        with patch("invoicing.messagebox.showwarning") as warning:
            self.window.scan()
            self.wait_scan()
            self.assertEqual(len(self.window.data.invoices), 1)
            self.assertIn("duplicado", warning.call_args.args[1])
        copy.unlink()
        create_invoice(path, [("MAT", "999,00")])
        with patch("invoicing.messagebox.showwarning") as warning:
            self.window.scan()
            self.wait_scan()
            self.assertIn("han cambiado", warning.call_args.args[1])
        self.assertEqual(invoice.amounts[first_row.id], 123)
        self.assertEqual(invoice.declared, 999)
        self.assertEqual([(line.item, line.amount) for line in invoice.lines], [("MAT", 999)])

    def wait_scan(self):
        deadline = time.monotonic() + 15
        while self.window._scanning and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.window._scanning)

    def test_inline_amount_editing_validation_and_invoice_deletion(self):
        self.start()
        invoice = Invoice("invoice", "Mi factura", date="01/11/2026", declared=123.45)
        self.window.data.invoices.append(invoice)
        self.window._refresh()
        row = self.window.data.rows[0]
        self.window.tree.selection_set(row.id)
        event = SimpleNamespace(x=1, y=1)
        with (
            patch.object(self.window.tree, "identify_row", return_value=row.id),
            patch.object(self.window.tree, "identify_column", return_value="#7"),
            patch.object(self.window.tree, "bbox", return_value=(0, 0, 140, 24)),
        ):
            self.window.edit_cell(event)
        editor = self.window._cell_editor
        self.assertIsNotNone(editor)
        editor.delete(0, "end")
        editor.insert(0, "125,50")
        editor.event_generate("<FocusOut>")
        self.assertEqual(invoice.amounts[row.id], 125.5)
        self.assertEqual(self.window.totals.item(row.id, "values"), ("48,00 €", "125,50 €", "-77,50 €"))
        with patch("invoicing.messagebox.showerror") as error:
            self.window._commit_cell(invoice.id, row.id, "nan")
            error.assert_called_once()
        self.assertEqual(invoice.amounts[row.id], 125.5)
        with patch("invoicing.messagebox.askyesno", return_value=True):
            self.window.delete_invoice()
        self.assertFalse(self.window.data.invoices)
        self.assertTrue(self.app.is_dirty)

    def test_menu_single_window_and_empty_budget_guard(self):
        button = next(child for child in self.root.grid_slaves(row=0, column=0)[0].winfo_children()
                      if isinstance(child, ttk.Menubutton) and child.cget("text") == "Herramientas")
        menu = self.root.nametowidget(button.cget("menu"))
        index = next(index for index in range(menu.index("end") + 1)
                     if menu.entrycget(index, "label") == "Facturación")
        menu.invoke(index)
        self.assertIs(self.app._invoicing_window, self.window)
        self.app.budget_items = []
        with patch("bc3_gui.messagebox.showinfo") as info:
            self.app.open_invoicing()
            info.assert_called_once()

    def test_invalid_folder_and_old_projects(self):
        self.window.data.folder = str(self.folder / "missing")
        with patch("invoicing.messagebox.showerror") as error:
            self.window.scan()
            error.assert_called_once()
        path = self.folder / "old.json"
        path.write_text('{"items": [{"code": "P", "chapter": "01#"}]}', encoding="utf-8")
        self.assertTrue(self.app._load_project_file(path))
        self.assertEqual(self.app.invoicing_state, InvoiceState().to_dict())

    def test_invalid_invoice_state_is_reported_on_load(self):
        path = self.folder / "invalid.json"
        path.write_text('{"items": [], "invoicing": {"rows": "invalid"}}', encoding="utf-8")
        with patch("bc3_gui.messagebox.showerror") as error:
            self.assertFalse(self.app._load_project_file(path))
            error.assert_called_once()

    def test_missing_dependency_is_reported(self):
        import sys

        self.window.data.folder = str(self.folder)
        with patch.dict(sys.modules, {"pdfplumber": None}), patch("invoicing.messagebox.showerror") as error:
            self.window.scan()
            self.assertIn("pdfplumber", error.call_args.args[1])
        self.assertTrue(self.window.tree.winfo_exists())

    def test_review_assign_edit_and_unassign_lines(self):
        create_invoice(self.folder / "invoice.pdf", [("unknown", "30,00")])
        self.start()
        invoice = self.window.data.invoices[0]
        self.window.review_lines()
        dialog = next(child for child in self.window.winfo_children()
                      if isinstance(child, tk.Toplevel))
        container = next(child for child in dialog.winfo_children() if isinstance(child, ttk.Frame))
        tree = next(child for child in container.winfo_children() if isinstance(child, ttk.Treeview))
        buttons = next(child for child in dialog.winfo_children()
                       if isinstance(child, ttk.Frame) and child is not container)

        def click(label):
            next(child for child in buttons.winfo_children()
                 if isinstance(child, ttk.Button) and child.cget("text") == label).invoke()

        tree.selection_set("0")
        click("Asignar al coste seleccionado")
        row = self.window.data.rows[0]
        self.assertEqual(invoice.amounts[row.id], 30)
        self.assertEqual(invoice.lines[0].row_id, row.id)
        tree.selection_set("0")
        click("Editar línea")
        edit = next(child for child in dialog.winfo_children() if isinstance(child, tk.Toplevel))
        form = edit.winfo_children()[0]
        entries = [child for child in form.winfo_children() if isinstance(child, ttk.Entry)]
        entries[1].delete(0, "end")
        entries[1].insert(0, "40,00")
        next(child for child in form.winfo_children() if isinstance(child, ttk.Button)).invoke()
        self.assertEqual(invoice.lines[0].amount, 40)
        self.assertEqual(invoice.amounts[row.id], 40)
        tree.selection_set("0")
        click("Desasignar")
        self.assertEqual(invoice.lines[0].row_id, "")
        self.assertEqual(invoice.amounts[row.id], 0)
        tree.selection_set("0")
        with patch("invoicing.messagebox.askyesno", return_value=True):
            click("Eliminar línea")
        self.assertFalse(invoice.lines)

    def test_fixed_totals_and_scroll_are_synchronized(self):
        self.start()
        self.window.data.invoices = [Invoice(f"i{index}", f"Factura {index}") for index in range(15)]
        self.window.data.rows = [CostRow(f"r{index}", description=f"Coste {index}") for index in range(100)]
        self.window._refresh()
        self.window.tree.column("description", width=1400)
        self.root.update()
        position = self.window.totals.winfo_rootx()
        self.window.tree.xview_moveto(1)
        self.window._scroll("moveto", 0.5)
        self.root.update()
        self.assertEqual(self.window.totals.winfo_rootx(), position)
        self.assertAlmostEqual(self.window.tree.yview()[0], self.window.totals.yview()[0])
        self.assertGreater(self.window.tree.xview()[0], 0)
        self.window.totals.selection_set("r50")
        self.root.update()
        self.assertEqual(self.window.tree.selection(), ("r50",))

    def test_compact_columns_preserve_description_space_in_both_views(self):
        """Keep secondary fields compact without reducing the description column."""
        window = self.window
        window.data.invoices = [Invoice("invoice", "Factura", date="30/10/2026")]
        window._refresh()
        for unified in (True, False):
            window.unify_button.invoke()
            self.assertEqual(window.unify_resources.get(), unified)
            for column, width in (
                ("chapter", 65), ("item", 75), ("resource", 80),
                ("description", 240), ("kind", 85), ("unit", 50), ("invoice", 110),
            ):
                self.assertEqual(window.tree.column(column, "width"), width)
            for column in ("budget", "billed", "remaining"):
                self.assertEqual(window.totals.column(column, "width"), 90)

    def test_window_opens_directly_without_folder_dialog_or_automatic_reading(self):
        self.assertTrue(self.window.tree.winfo_exists())
        self.assertFalse(hasattr(self.window, "start"))
        self.assertFalse(self.window._scanning)
        toolbar = self.window.billing_panel.winfo_children()[0]
        labels = [child.cget("text") for child in toolbar.winfo_children()
                  if isinstance(child, ttk.Button)]
        self.assertEqual(labels, ["Carpeta…", "Actualizar lectura", "Revisar líneas", "Eliminar factura"])
        self.assertFalse(any(isinstance(child, tk.Toplevel) for child in self.window.winfo_children()))
        with patch("invoicing.messagebox.showinfo") as info:
            self.window.scan()
        info.assert_called_once()
        with patch("invoicing.filedialog.askdirectory", return_value=""):
            self.window.change_folder()
        self.assertEqual(self.window.data.folder, "")
        with patch("invoicing.filedialog.askdirectory", return_value=str(self.folder)):
            self.window.change_folder()
        self.wait_scan()
        self.assertEqual(self.window.data.folder, str(self.folder))

    def test_selected_invoice_is_the_only_editable_column_even_for_equal_dates(self):
        self.start()
        row = self.window.data.rows[0]
        self.window.data.invoices = [
            Invoice("one", "Primera", date="01/02/2026", amounts={row.id: 12}),
            Invoice("two", "Segunda", date="01/02/2026", amounts={row.id: 30}),
        ]
        self.window._refresh()
        self.assertEqual(self.window.tree.cget("columns")[-1], "one")
        self.assertEqual(len(self.window.tree.cget("columns")), 7)
        self.window.invoice_selector.current(1)
        self.window.invoice_selector.event_generate("<<ComboboxSelected>>")
        self.assertEqual(self.window._visible_invoice_ids, ["two"])
        self.assertEqual(self.window.tree.heading("two", "text"), "01/02/2026")
        self.assertEqual(self.window.tree.item(row.id, "values")[-1], "30,00 €")
        before = self.window.data.to_dict()
        with patch("invoicing.messagebox.showerror") as error:
            self.assertFalse(self.window._commit_cell("one", row.id, "999"))
        error.assert_called_once()
        self.assertEqual(self.window.data.to_dict(), before)
        self.assertTrue(self.window._commit_cell("two", row.id, "-5,50"))
        self.assertEqual(self.window.data.invoices[0].amounts[row.id], 12)
        self.assertEqual(self.window.data.invoices[1].amounts[row.id], -5.5)
        self.assertEqual(self.window.totals.item(row.id, "values")[1], "6,50 €")

    def test_inline_editor_cancels_on_selection_and_metadata_are_read_only(self):
        self.start()
        row = self.window.data.rows[0]
        self.window.data.invoices = [Invoice("one", "Una"), Invoice("two", "Dos")]
        self.window._refresh()
        event = SimpleNamespace(x=1, y=1)
        for column in ("#1", "#2", "#3", "#4", "#5", "#6", "#8"):
            with (
                patch.object(self.window.tree, "identify_row", return_value=row.id),
                patch.object(self.window.tree, "identify_column", return_value=column),
            ):
                self.window.edit_cell(event)
            self.assertIsNone(self.window._cell_editor)
        with (
            patch.object(self.window.tree, "identify_row", return_value=row.id),
            patch.object(self.window.tree, "identify_column", return_value="#7"),
            patch.object(self.window.tree, "bbox", return_value=(0, 0, 140, 24)),
        ):
            self.window.edit_cell(event)
        self.window._cell_editor.delete(0, "end")
        self.window._cell_editor.insert(0, "999")
        self.window.select_invoice("two")
        self.assertIsNone(self.window._cell_editor)
        self.assertEqual([invoice.amounts for invoice in self.window.data.invoices], [{}, {}])

    def test_saving_invalid_inline_amount_does_not_save_or_change_assignments(self):
        self.start()
        row = self.window.data.rows[0]
        self.window.data.invoices = [Invoice("i", "Factura")]
        self.window._refresh()
        with (
            patch.object(self.window.tree, "identify_row", return_value=row.id),
            patch.object(self.window.tree, "identify_column", return_value="#7"),
            patch.object(self.window.tree, "bbox", return_value=(0, 0, 140, 24)),
        ):
            self.window.edit_cell(SimpleNamespace(x=1, y=1))
        editor = self.window._cell_editor
        editor.delete(0, "end")
        editor.insert(0, "nan")
        with patch("invoicing.messagebox.showerror"), patch.object(self.window, "on_save") as save:
            self.window.save()
        save.assert_not_called()
        self.assertIs(self.window._cell_editor, editor)
        self.assertEqual(self.window.data.invoices[0].amounts, {})
        editor.delete(0, "end")
        editor.insert(0, "10,25")
        with patch.object(self.window, "on_save", return_value=True) as save:
            self.window.save()
        save.assert_called_once()
        self.assertEqual(self.window.data.invoices[0].amounts[row.id], 10.25)
        self.assertIsNone(self.window._cell_editor)

    def test_refresh_reads_every_pdf_updates_metadata_and_keeps_allocations(self):
        first = self.folder / "first.pdf"
        second = self.folder / "second.pdf"
        create_invoice(first)
        create_invoice(second, [("MAQ", "15,00")])
        self.start()
        originals = {invoice.path: invoice for invoice in self.window.data.invoices}
        material = next(row for row in self.window.data.rows if row.kind == "Material")
        invoice = originals[str(first.resolve())]
        self.window.select_invoice(invoice.id)
        self.window._commit_cell(invoice.id, material.id, "123")
        create_invoice(first, [("MAT", "999,00")])
        with (
            patch("invoicing.read_invoice", wraps=read_invoice) as read,
            patch("invoicing.messagebox.showwarning"),
        ):
            self.window.scan()
            self.wait_scan()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(len(self.window.data.invoices), 2)
        self.assertEqual(invoice.declared, 999)
        self.assertEqual(invoice.amounts[material.id], 123)
        self.assertEqual(self.window.selected_invoice().id, invoice.id)
        for current in self.window.data.invoices:
            self.assertIs(current, originals[current.path])
        first.write_bytes(b"invalid pdf")
        with patch("invoicing.messagebox.showwarning") as warning:
            self.window.scan()
            self.wait_scan()
        self.assertIn("datos anteriores", warning.call_args.args[1])
        self.assertEqual(invoice.declared, 999)
        self.assertEqual(invoice.amounts[material.id], 123)

    def test_manual_edit_detaches_imported_line_and_repeated_refresh_keeps_amount(self):
        path = self.folder / "invoice.pdf"
        create_invoice(path, [("MAT", "30,00")])
        self.start()
        invoice = self.window.data.invoices[0]
        material = next(row for row in self.window.data.rows if row.kind == "Material")
        self.assertEqual(invoice.lines[0].row_id, material.id)
        self.window._commit_cell(invoice.id, material.id, "45")
        self.assertEqual(invoice.lines[0].row_id, "")
        for _ in range(2):
            self.window.scan()
            self.wait_scan()
            self.assertEqual(invoice.amounts[material.id], 45)
            self.assertEqual(invoice.lines[0].row_id, "")

    def test_refresh_updates_date_heading_and_accumulation_without_losing_line_links(self):
        create_invoice(self.folder / "invoice.pdf", [("MAT", "30,00")])
        self.start()
        invoice = self.window.data.invoices[0]
        original_id = invoice.id
        line_row = invoice.lines[0].row_id
        updated = Invoice(
            "new-reading-id", invoice.name, path=invoice.path, digest="updated",
            date="15/02/2026", declared=30, lines=[InvoiceLine("MAT", 30)],
        )
        with patch("invoicing.read_invoice", return_value=updated):
            self.window.scan()
            self.wait_scan()
        self.assertEqual(invoice.id, original_id)
        self.assertEqual(invoice.lines[0].row_id, line_row)
        self.assertEqual(invoice.amounts[line_row], 30)
        self.assertEqual(self.window.tree.heading(original_id, "text"), "15/02/2026")
        self.window.period_selector.set("2026-02")
        self.window._refresh()
        self.assertEqual(self.window.totals.item(line_row, "values")[1], "30,00 €")

    def test_refresh_recovers_moved_pdf_without_recreating_assignments(self):
        path = self.folder / "invoice.pdf"
        create_invoice(path, [("MAT", "30,00")])
        self.start()
        invoice = self.window.data.invoices[0]
        original_id = invoice.id
        assignments = dict(invoice.amounts)
        moved = self.folder / "moved.pdf"
        path.rename(moved)
        self.window.scan()
        self.wait_scan()
        self.assertEqual(len(self.window.data.invoices), 1)
        self.assertEqual(invoice.id, original_id)
        self.assertEqual(invoice.path, str(moved.resolve()))
        self.assertEqual(invoice.amounts, assignments)
        self.assertEqual(self.window.invoice_register.set(invoice.id, "filename"), "moved.pdf")

    def test_save_button_and_new_project_reset(self):
        self.start()
        self.assertEqual(self.window.save_button.cget("text"), "💾")
        self.assertTrue(self.window._is_dirty)
        self.assertEqual(self.window.save_button.cget("background"), "#000000")
        self.assertTrue(self.window.bind("<Control-s>"))
        project = self.folder / "saved.json"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(project)):
            self.window.save_button.invoke()
        self.assertTrue(project.is_file())
        self.assertFalse(self.app.is_dirty)
        self.assertFalse(self.window._is_dirty)
        self.assertEqual(self.window.save_button.cget("background"), self.window._save_button_background)
        with patch("bc3_gui.simpledialog.askstring", return_value="Nuevo"):
            self.app.new_project()
        self.assertEqual(self.app.invoicing_state, {})
        self.assertFalse(self.window.winfo_exists())

    def test_one_corrupt_pdf_does_not_hide_other_invoices(self):
        create_invoice(self.folder / "valid.pdf")
        (self.folder / "invalid.pdf").write_bytes(b"not a pdf")
        with patch("invoicing.messagebox.showwarning") as warning:
            self.start()
            warning.assert_called_once()
            self.assertIn("No se pudo leer", warning.call_args.args[1])
        self.assertEqual(len(self.window.data.invoices), 2)
        self.assertEqual(sum(invoice.declared for invoice in self.window.data.invoices), 50)
        self.assertEqual(
            {self.window.invoice_register.set(invoice.id, "filename") for invoice in self.window.data.invoices},
            {"invalid.pdf", "valid.pdf"},
        )

    def test_sorting_updates_columns_and_register_preserving_selection_and_saved_amounts(self):
        self.start()
        row = self.window.data.rows[0]
        self.window.data.invoices = [
            Invoice("late", "Tardía", date="01/11/2026", amounts={row.id: 20}),
            Invoice("unknown", "Sin fecha", amounts={row.id: 30}),
            Invoice("early", "Temprana", date="31/12/2025", amounts={row.id: 10}),
        ]
        self.window.changed()
        self.window._refresh()
        self.assertEqual(self.window.invoice_register.get_children(), ("early", "late", "unknown"))
        self.assertEqual(self.window.tree.cget("columns")[-1:], ("early",))
        self.assertEqual(self.window.tree.item(row.id, "values")[-1:], ("10,00 €",))
        self.window.select_invoice("late")
        self.window.data.invoices[1].date = "01/01/2025"
        self.window.changed()
        self.window._refresh()
        self.assertEqual(self.window.invoice_register.get_children(), ("late", "early", "unknown"))
        self.assertEqual(self.window.selected_invoice().id, "late")
        self.assertEqual(self.window.tree.item(row.id, "values")[-1:], ("20,00 €",))
        self.assertEqual(self.window.invoice_register.set("late", "date"), "01/01/2025")
        project = self.folder / "sorted.json"
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(project)):
            self.window.save()
        self.assertTrue(self.app._load_project_file(project))
        saved = InvoiceState.from_dict(self.app.invoicing_state)
        self.assertEqual([invoice.id for invoice in saved.invoices], ["late", "early", "unknown"])
        self.assertEqual([invoice.amounts[row.id] for invoice in saved.invoices], [20, 10, 30])

    def test_file_name_click_opens_pdf_and_reports_missing_or_invalid_paths(self):
        create_invoice(self.folder / "invoice.pdf")
        self.start()
        invoice = self.window.data.invoices[0]
        event = SimpleNamespace(x=1, y=1)
        with (
            patch.object(self.window.invoice_register, "identify_region", return_value="cell"),
            patch.object(self.window.invoice_register, "identify_column", return_value="#2"),
            patch.object(self.window.invoice_register, "identify_row", return_value=invoice.id),
            patch("invoicing.os.startfile") as open_file,
            patch("invoicing.messagebox.showerror") as error,
        ):
            self.window.open_invoice_file(event)
            self.assertIsNotNone(self.window._pdf_photo)
            open_file.assert_not_called()
            self.window.open_external_pdf()
            open_file.assert_called_once_with((self.folder / "invoice.pdf").resolve())
            error.assert_not_called()
            open_file.reset_mock()
            with patch.object(self.window.invoice_register, "identify_column", return_value="#1"):
                self.window.open_invoice_file(event)
            open_file.assert_not_called()
            for path in ("", str(self.folder / "missing.pdf"), str(self.folder / "file.exe")):
                invoice.path = path
                self.window.open_invoice_file(event)
                self.assertIn("No se encuentra un PDF válido", self.window.pdf_status.cget("text"))
                self.window.open_external_pdf()
            self.assertEqual(error.call_count, 3)
            open_file.assert_not_called()
            invoice.path = str(self.folder / "invoice.pdf")
            open_file.side_effect = OSError("No hay visor")
            self.window.open_external_pdf()
            self.assertIn("No se pudo abrir", error.call_args.args[1])

    def test_split_view_removes_redundant_name_and_preserves_manual_split(self):
        self.root.update()
        window = self.window
        self.assertEqual(window.invoice_register.cget("columns"), ("date", "filename"))
        self.assertEqual(str(window.splitter.cget("orient")), "horizontal")
        self.assertEqual(len(window.splitter.panes()), 2)
        width = window.splitter.winfo_width()
        self.assertAlmostEqual(window.splitter.sashpos(0) / width, 0.5, delta=0.03)
        window.splitter.sashpos(0, width // 3)
        window._initialize_split()
        self.assertAlmostEqual(window.splitter.sashpos(0) / width, 1 / 3, delta=0.03)
        self.assertIsNone(window._pdf_photo)
        self.assertIn("Selecciona una factura", window.pdf_status.cget("text"))
        self.assertEqual(str(window.pdf_next.cget("state")), "disabled")
        self.assertEqual(str(window.pdf_previous.cget("state")), "disabled")

    def test_unified_resources_sum_all_items_invoices_and_periods_without_mutation(self):
        window = self.window
        window.data.rows = [
            CostRow(f"r{index}", chapter=f"{index}#", item=f"P{index}",
                    resource="HORM", description="Hormigon", kind="Material", unit="m3",
                    budget=10 + index)
            for index in range(10)
        ]
        window.data.rows.append(CostRow(
            "maq", resource="MAQ", description="Grua", kind="Maquinaria", budget=100,
        ))
        window.data.invoices = [
            Invoice("jan", "Enero", date="31/01/2026",
                    amounts={f"r{index}": 1 + index for index in range(10)}),
            Invoice("feb", "Febrero", date="28/02/2026", amounts={"r0": 200, "r9": -5}),
        ]
        window._refresh()
        original = window.data.to_dict()
        window.unify_button.invoke()
        self.assertEqual(window.tree.get_children(), ("r0", "maq"))
        self.assertEqual(window.totals.get_children(), ("r0", "maq"))
        self.assertEqual(window.tree.cget("displaycolumns"),
                         ("resource", "description", "kind", "unit", "jan"))
        self.assertEqual(window.tree.item("r0", "values")[-1], "55,00 €")
        self.assertEqual(window.totals.item("r0", "values"),
                         ("145,00 €", "250,00 €", "-105,00 €"))
        self.assertIn("over", window.totals.item("r0", "tags"))
        window.select_invoice("feb")
        self.assertEqual(window.tree.item("r0", "values")[-1], "195,00 €")
        window.period_selector.set("2026-01")
        window._refresh()
        self.assertEqual(window.totals.item("r0", "values"),
                         ("145,00 €", "55,00 €", "90,00 €"))
        self.assertEqual(window.tree.item("r0", "values")[-1], "195,00 €")
        window.filter_buttons["Material"].invoke()
        self.assertEqual(window.tree.get_children(), ("r0",))
        window.filter_buttons["RCDs"].invoke()
        self.assertEqual(window.tree.get_children(), ())
        window.filter_buttons["RCDs"].invoke()
        window.unify_button.invoke()
        self.assertEqual(len(window.tree.get_children()), 11)
        self.assertEqual(window.tree.cget("displaycolumns")[:2], ("chapter", "item"))
        self.assertEqual(window.view_notice.cget("text"), "")
        self.assertEqual(window.data.to_dict(), original)

    def test_unified_resources_keep_incompatible_or_uncoded_costs_separate(self):
        window = self.window
        window.data.rows = [
            CostRow("one", resource="MAT", description="Hormigon", kind="Material", unit="m3", budget=1),
            CostRow("two", resource="MAT", description="Hormigon", kind="Material", unit="m3", budget=2),
            CostRow("unit", resource="MAT", description="Hormigon", kind="Material", unit="kg", budget=3),
            CostRow("kind", resource="MAT", description="Hormigon", kind="Maquinaria", unit="m3", budget=4),
            CostRow("text", resource="MAT", description="Otro hormigon", kind="Material", unit="m3", budget=5),
            CostRow("empty1", description="Sin codigo", budget=6),
            CostRow("empty2", description="Sin codigo", budget=7),
        ]
        window.unify_button.invoke()
        self.assertEqual(window.tree.get_children(), ("one", "unit", "kind", "text", "empty1", "empty2"))
        self.assertEqual(window.totals.item("one", "values"),
                         ("3,00 €", "0,00 €", "3,00 €"))
        self.assertEqual(len(window.tree.cget("displaycolumns")), 4)

    def test_unified_resources_disable_editing_and_cancel_pending_cell(self):
        window = self.window
        row = window.data.rows[0]
        window.data.invoices = [Invoice("i", "Factura", amounts={row.id: 20})]
        window._refresh()
        event = SimpleNamespace(x=1, y=1)
        with (
            patch.object(window.tree, "identify_row", return_value=row.id),
            patch.object(window.tree, "identify_column", return_value="#7"),
            patch.object(window.tree, "bbox", return_value=(0, 0, 140, 24)),
        ):
            window.edit_cell(event)
            self.assertIsNotNone(window._cell_editor)
            window._cell_editor.delete(0, "end")
            window._cell_editor.insert(0, "999")
            window.unify_button.invoke()
            self.assertIsNone(window._cell_editor)
            window.edit_cell(event)
            self.assertIsNone(window._cell_editor)
        before = window.data.to_dict()
        with patch("invoicing.messagebox.showinfo") as info:
            self.assertFalse(window._commit_cell("i", row.id, "999"))
        info.assert_called_once()
        self.assertEqual(window.data.to_dict(), before)
        self.assertEqual(window.view_notice.cget("text"), "Solo lectura")
        window.unify_button.invoke()
        self.assertTrue(window._commit_cell("i", row.id, "25"))
        self.assertEqual(window.data.invoices[0].amounts[row.id], 25)

    def test_pdf_preview_real_pages_zoom_and_selection_preserve_invoice_amounts(self):
        from reportlab.pdfgen import canvas

        path = self.folder / "pages.pdf"
        document = canvas.Canvas(str(path))
        document.drawString(40, 700, "Primera pagina")
        document.showPage()
        document.drawString(40, 700, "Segunda pagina")
        document.save()
        window = self.window
        row = window.data.rows[0]
        window.data.invoices = [
            Invoice("one", "Primera", path=str(path), amounts={row.id: 12}),
            Invoice("two", "Segunda", path=str(path), amounts={row.id: 30}),
        ]
        window._refresh()
        self.root.update()
        self.assertEqual(window._pdf_pages, 2)
        self.assertEqual(window.pdf_page_label.cget("text"), "1 / 2")
        self.assertIsNotNone(window._pdf_photo)
        self.assertTrue(window.pdf_canvas.find_all())
        self.assertEqual(str(window.pdf_next.cget("state")), "normal")
        original = window.data.to_dict()
        window.pdf_next.invoke()
        self.assertEqual(window._pdf_page, 1)
        self.assertEqual(window.pdf_page_label.cget("text"), "2 / 2")
        self.assertEqual(str(window.pdf_next.cget("state")), "disabled")
        window._change_pdf_page(1)
        self.assertEqual(window._pdf_page, 1)
        width = window._pdf_photo.width()
        window.pdf_zoom_selector.set("150%")
        window.pdf_zoom_selector.event_generate("<<ComboboxSelected>>")
        self.assertGreater(window._pdf_photo.width(), width)
        self.assertEqual(window._pdf_page, 1)
        with patch("invoicing.render_invoice_page", wraps=render_invoice_page) as render:
            window._refresh()
            render.assert_not_called()
        window.select_invoice("two")
        self.assertEqual(window._pdf_source[0], "two")
        self.assertEqual(window._pdf_page, 0)
        self.assertEqual(window.tree.item(row.id, "values")[-1], "30,00 €")
        self.assertEqual(window.data.to_dict(), original)
        window.data.invoices.clear()
        window._refresh()
        self.assertIsNone(window._pdf_photo)
        self.assertFalse(window.pdf_canvas.find_all())

    def test_pdf_render_bounds_and_invalid_documents(self):
        path = self.folder / "invoice.pdf"
        create_invoice(path)
        image, count = render_invoice_page(path, 0, 10000, 2)
        self.assertEqual(count, 1)
        self.assertLessEqual(image.width * image.height, 4_010_000)
        image.close()
        with self.assertRaises(ValueError):
            render_invoice_page(path, 1, 600, 1)
        path.write_bytes(b"not a pdf")
        with self.assertRaises(ValueError):
            render_invoice_page(path, 0, 600, 1)
        path.unlink()

    def test_preview_reports_errors_and_reloads_when_invoice_digest_changes(self):
        path = self.folder / "invoice.pdf"
        path.write_bytes(b"not a pdf")
        window = self.window
        invoice = Invoice("one", "Factura", path=str(path))
        window.data.invoices = [invoice]
        window._refresh()
        self.assertIsNone(window._pdf_photo)
        self.assertIn("No se pudo visualizar", window.pdf_status.cget("text"))
        self.assertEqual(window._pdf_pages, 0)
        create_invoice(path)
        invoice.digest = "new-digest"
        window._refresh()
        self.assertIsNotNone(window._pdf_photo)
        self.assertEqual(window._pdf_pages, 1)
        with patch("invoicing.render_invoice_page", side_effect=ImportError("renderer missing")):
            invoice.digest = "changed"
            window._refresh()
        self.assertIn("renderer missing", window.pdf_status.cget("text"))
        self.assertIsNone(window._pdf_photo)

    def test_pdf_resize_callbacks_are_cancelled_on_window_destroy(self):
        window = self.window
        window._schedule_pdf_render()
        callback = window._pdf_after
        self.assertIn(callback, window.tk.call("after", "info"))
        window.destroy()
        self.assertIsNone(window._pdf_after)
        self.assertNotIn(callback, self.root.tk.call("after", "info"))

    def test_save_cancellation_and_failure_keep_dirty_indicator_and_close_does_not_redirty(self):
        self.start()
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=""):
            self.window.save()
        self.assertTrue(self.window._is_dirty)
        self.assertTrue(self.app.is_dirty)
        self.assertEqual(self.window.save_button.cget("background"), "#000000")
        project = self.folder / "saved.json"
        with (
            patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(project)),
            patch.object(self.app, "_write_project_file", return_value=False),
        ):
            self.window.save()
        self.assertTrue(self.window._is_dirty)
        with patch("bc3_gui.filedialog.asksaveasfilename", return_value=str(project)):
            self.window.save()
        self.assertFalse(self.window._is_dirty)
        self.window.close()
        self.assertFalse(self.app.is_dirty)

    def test_save_waits_for_scan(self):
        self.start()
        self.window._scanning = True
        with patch("invoicing.messagebox.showinfo") as info, patch.object(self.window, "on_save") as save:
            self.window.save()
            save.assert_not_called()
            info.assert_called_once()
        self.window._scanning = False


if __name__ == "__main__":
    unittest.main()
