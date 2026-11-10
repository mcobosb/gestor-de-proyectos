"""Pruebas de entrada SICESS y generación de texto de centros asistenciales."""
from decimal import Decimal
import tkinter as tk
import unittest
from unittest.mock import patch

from tools.safety_health import restore_project_state, serialize_state
from tools.safety_health.care_centers import CareCentersEditor, format_centers


class CareCentersTests(unittest.TestCase):
    def test_format_sicess_address_and_telephone(self):
        """Include contact details without administrative SICESS metadata."""
        self.assertEqual(format_centers([
            {
                "nombre": "LUCENA_SUAP", "via": "Calle", "domicilio": "PASEO DE ROJAS",
                "numero": "S/N", "cp": "14900", "municipio": "LUCENA",
                "provincia": "CORDOBA", "telefono": "957514500", "ambito": "PROVINCIAL",
                "dependencia": "S.A.S.", "sspa": "Sí", "hospitalario": "No",
            },
            {
                "nombre": "Hospital Universitario Costa del Sol", "domicilio": "A-7",
                "numero": "Km. 187", "cp": "29603", "localidad": "Marbella",
                "municipio": "Marbella", "provincia": "Málaga", "telefono": "123456789",
            },
            {},
        ]), (
            "LUCENA_SUAP\nCalle PASEO DE ROJAS, S/N, 14900, LUCENA, CORDOBA\n"
            "Teléfono: 957514500\n\nHOSPITAL UNIVERSITARIO COSTA DEL SOL\n"
            "A-7, Km. 187, 29603, Marbella, Málaga\nTeléfono: 123456789"
        ))
        self.assertEqual(format_centers([{}]), "")
        self.assertEqual(format_centers([{
            "nombre": "Centro", "escalera": "A", "piso": "2", "puerta": "B",
        }]), "CENTRO\nEsc. A, Piso 2, Pta. B")

    def test_state_roundtrip_legacy_and_validation(self):
        """Preserve both modes, accept old text and reject malformed SICESS rows."""
        state = serialize_state(
            [], Decimal("2"), centros_asistenciales="CENTRO\nTeléfono: 123",
            centros_asistenciales_modo="tabla", centros_asistenciales_texto="Texto anterior",
            centros_asistenciales_datos=[{"nombre": "Centro", "telefono": "123"}],
        )
        restored = restore_project_state(state)
        self.assertEqual(restored["centros_asistenciales"], state["centros_asistenciales"])
        self.assertEqual(restored["centros_asistenciales_texto"], "Texto anterior")
        self.assertEqual(restored["centros_asistenciales_modo"], "tabla")
        self.assertEqual(restored["centros_asistenciales_datos"][0]["telefono"], "123")
        legacy = restore_project_state({
            "lines": [], "percentage": "2", "centros_asistenciales": "Texto antiguo",
        })
        self.assertEqual(legacy["centros_asistenciales_texto"], "Texto antiguo")
        self.assertEqual(legacy["centros_asistenciales_modo"], "texto")
        for invalid in (
            {"centros_asistenciales_modo": "otro"},
            {"centros_asistenciales_texto": None},
            {"centros_asistenciales_datos": {}},
            {"centros_asistenciales_datos": [None]},
            {"centros_asistenciales_datos": [{"telefono": 123}]},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                restore_project_state({**state, **invalid})


class CareCentersEditorTests(unittest.TestCase):
    def setUp(self):
        """Create an isolated Tk editor with existing manual text."""
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.editor = CareCentersEditor(self.root, {"centros_asistenciales": "Texto previo"})

    def test_add_switch_convert_remove_and_reopen(self):
        """Keep row edits and manual text, then generate a reusable summary."""
        editor = self.editor
        editor.variables["nombre"].set("Centro de salud")
        editor.variables["telefono"].set("111")
        editor.add_center()
        editor.variables["nombre"].set("Hospital")
        editor.variables["telefono"].set("222")
        state = editor.collect_state()
        self.assertEqual(state["centros_asistenciales_texto"], "Texto previo")
        self.assertEqual(state["centros_asistenciales_modo"], "tabla")
        self.assertEqual(
            state["centros_asistenciales"],
            "CENTRO DE SALUD\nTeléfono: 111\n\nHOSPITAL\nTeléfono: 222",
        )
        editor.list.selection_set("0")
        editor.select_center()
        self.assertEqual(editor.variables["telefono"].get(), "111")
        with patch("tools.safety_health.care_centers.messagebox.askyesno", return_value=False):
            editor.to_text()
        self.assertEqual(editor.text.get("1.0", "end-1c"), "Texto previo")
        with patch("tools.safety_health.care_centers.messagebox.askyesno", return_value=True):
            editor.to_text()
        self.assertEqual(editor.collect_state()["centros_asistenciales_modo"], "texto")
        self.assertEqual(editor.text.get("1.0", "end-1c"), state["centros_asistenciales"])
        reopened = CareCentersEditor(self.root, state)
        self.assertEqual(reopened.collect_state(), state)
        with patch("tools.safety_health.care_centers.messagebox.askyesno", return_value=True):
            reopened.remove_center()
        self.assertEqual(len(reopened.rows), 1)
        self.assertEqual(reopened.variables["nombre"].get(), "Hospital")
        with patch("tools.safety_health.care_centers.messagebox.askyesno", return_value=True):
            reopened.remove_center()
        self.assertEqual(reopened.collect_state()["centros_asistenciales"], "")


if __name__ == "__main__":
    unittest.main()
