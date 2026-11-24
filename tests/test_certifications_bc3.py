"""Importación segura de certificaciones BC3 a origen."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from budget_core import Bc3Dataset, ProjectItem
from budget_project import _item_payload, item_from_data
from tools.certifications.bc3_import import prepare_import


def write_bc3(
    path: Path, number: int, date_text: str, quantity: float, price: float = 10,
) -> None:
    """Escribe una certificación mínima con una partida."""
    path.write_text(
        f"~V|TEST|FIEBDC-3/2020|||ANSI||3|{number}|{date_text}|\n"
        "~C|ROOT##||Obra|0||0|\n"
        "~C|CAP#||Capítulo|0||0|\n"
        f"~C|P|m|Partida|{price}||0|\n"
        "~D|ROOT##|CAP\\1\\1\\|\n"
        f"~D|CAP#|P\\1\\{quantity}\\|\n"
        f"~M|CAP\\P|1\\1\\|{quantity}||\n",
        encoding="cp1252",
    )


class CertificationBc3Tests(unittest.TestCase):
    """Comprueba mediciones a origen, secuencia y persistencia."""

    def test_import_accumulated_and_validate_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cert.bc3"
            item = ProjectItem("P", "CAP#", "m", "Partida", 100, 10)
            write_bc3(path, 1, "31052026", 5)
            number, month, values = prepare_import(Bc3Dataset(path), [item])
            self.assertEqual((number, month, values), (1, date(2026, 5, 1), [(5, 50)]))
            item.certifications[month.isoformat()] = 50
            item.certification_bc3[month.isoformat()] = {
                "number": 1, "quantity": 5, "amount": 50, "price": 10,
            }
            item = item_from_data(_item_payload(item))
            write_bc3(path, 2, "30062026", 8)
            self.assertEqual(prepare_import(Bc3Dataset(path), [item])[2], [(8, 30)])
            item.certifications["2026-05-01"] = 49
            with self.assertRaisesRegex(ValueError, "modificada"):
                prepare_import(Bc3Dataset(path), [item])

    def test_rejects_missing_history_and_unmatched_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cert.bc3"
            item = ProjectItem("P", "CAP#", "m", "Partida", 100, 10)
            write_bc3(path, 2, "30062026", 8)
            with self.assertRaisesRegex(ValueError, "anteriores"):
                prepare_import(Bc3Dataset(path), [item])
            write_bc3(path, 1, "31052026", 5)
            with self.assertRaisesRegex(ValueError, "no coincide"):
                prepare_import(Bc3Dataset(path), [ProjectItem("OTHER", "CAP#", "m", "Otra", 1, 10)])
            with self.assertRaisesRegex(ValueError, "más de una vez"):
                prepare_import(Bc3Dataset(path), [item, item])

    def test_rounds_accumulated_amount_before_subtracting_previous_month(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cert.bc3"
            item = ProjectItem("P", "CAP#", "m", "Partida", 1, .01)
            write_bc3(path, 1, "31052026", .5, .01)
            number, month, values = prepare_import(Bc3Dataset(path), [item])
            self.assertEqual(values, [(.5, .01)])
            item.certifications[month.isoformat()] = .01
            item.certification_bc3[month.isoformat()] = {
                "number": number, "quantity": .5, "amount": .01, "price": .01,
            }
            write_bc3(path, 2, "30062026", 1, .01)
            self.assertEqual(prepare_import(Bc3Dataset(path), [item])[2], [(1, 0)])


if __name__ == "__main__":
    unittest.main()
