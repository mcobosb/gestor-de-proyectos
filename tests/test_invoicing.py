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

from bc3_gui import Bc3Dataset, BudgetApp, Concept, ProjectItem
from invoicing import (
    CostRow, Invoice, InvoiceLine, InvoiceState, InvoicingWindow, amount,
    assign_line, assign_matching_lines, budget_rows, parse_invoice_tables,
    invoice_sort_key, read_invoice, total,
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
        build_script = (root / "build_installer.ps1").read_text(encoding="utf-8")
        self.assertIn("--include-package-data=pdfminer", build_script)
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
        self.window.continue_to_table()
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
            self.assertIn("ha cambiado", warning.call_args.args[1])
        self.assertEqual(invoice.amounts[first_row.id], 123)

    def wait_scan(self):
        deadline = time.monotonic() + 15
        while self.window._scanning and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.window._scanning)

    def test_manual_columns_fields_cell_validation_and_deletions(self):
        self.start()
        self.window.add_invoice()
        self.submit_form("Editar factura", {
            "Nombre de columna": "Mi factura", "Obra": "Obra B", "Expediente": "X-1",
            "Fecha": "01/11/2026", "Importe leído/declarado": "123,45", "Observaciones": "Nota",
        })
        invoice = self.window.data.invoices[0]
        self.assertEqual((invoice.name, invoice.work, invoice.declared), ("Mi factura", "Obra B", 123.45))
        row = self.window.data.rows[0]
        self.window.tree.selection_set(row.id)
        self.window.edit_row()
        self.submit_form("Editar coste", {"Descripción": "Personalizado", "Coste previsto (€)": "150"})
        self.assertEqual(row.description, "Personalizado")
        event = SimpleNamespace(x=1, y=1)
        with (
            patch.object(self.window.tree, "identify_row", return_value=row.id),
            patch.object(self.window.tree, "identify_column", return_value="#7"),
            patch("invoicing.simpledialog.askstring", return_value="125,50"),
        ):
            self.window.edit_cell(event)
        self.assertEqual(invoice.amounts[row.id], 125.5)
        self.assertEqual(self.window.totals.item(row.id, "values"), ("150,00 €", "125,50 €", "24,50 €"))
        with (
            patch.object(self.window.tree, "identify_row", return_value=row.id),
            patch.object(self.window.tree, "identify_column", return_value="#7"),
            patch("invoicing.simpledialog.askstring", return_value="nan"),
            patch("invoicing.messagebox.showerror") as error,
        ):
            self.window.edit_cell(event)
            error.assert_called_once()
        self.assertEqual(invoice.amounts[row.id], 125.5)
        invoice.lines.append(InvoiceLine("MO", 125.5, row.id))
        with patch("invoicing.messagebox.askyesno", return_value=True):
            self.window.delete_row()
        self.assertNotIn(row.id, invoice.amounts)
        self.assertEqual(invoice.lines[0].row_id, "")
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
        self.window.folder.set(str(self.folder / "missing"))
        with patch("invoicing.messagebox.showerror") as error:
            self.window.continue_to_table()
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

        self.window.folder.set(str(self.folder))
        with patch.dict(sys.modules, {"pdfplumber": None}), patch("invoicing.messagebox.showerror") as error:
            self.window.continue_to_table()
            self.assertIn("pdfplumber", error.call_args.args[1])
        self.assertTrue(self.window.start.winfo_exists())

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
        self.assertEqual(self.window.tree.cget("columns")[-3:], ("early", "late", "unknown"))
        self.assertEqual(self.window.tree.item(row.id, "values")[-3:], ("10,00 €", "20,00 €", "30,00 €"))
        self.window.select_invoice("late")
        self.window.edit_invoice()
        self.submit_form("Editar factura", {"Fecha": "01/01/2025"})
        self.assertEqual(self.window.invoice_register.get_children(), ("late", "early", "unknown"))
        self.assertEqual(self.window.selected_invoice().id, "late")
        self.assertEqual(self.window.tree.item(row.id, "values")[-3:], ("20,00 €", "10,00 €", "30,00 €"))
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
            patch.object(self.window.invoice_register, "identify_column", return_value="#3"),
            patch.object(self.window.invoice_register, "identify_row", return_value=invoice.id),
            patch("invoicing.os.startfile") as open_file,
            patch("invoicing.messagebox.showerror") as error,
        ):
            self.window.open_invoice_file(event)
            open_file.assert_called_once_with((self.folder / "invoice.pdf").resolve())
            error.assert_not_called()
            open_file.reset_mock()
            with patch.object(self.window.invoice_register, "identify_column", return_value="#1"):
                self.window.open_invoice_file(event)
            open_file.assert_not_called()
            for path in ("", str(self.folder / "missing.pdf"), str(self.folder / "file.exe")):
                invoice.path = path
                self.window.open_invoice_file(event)
            self.assertEqual(error.call_count, 3)
            open_file.assert_not_called()
            invoice.path = str(self.folder / "invoice.pdf")
            open_file.side_effect = OSError("No hay visor")
            self.window.open_invoice_file(event)
            self.assertIn("No se pudo abrir", error.call_args.args[1])

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
