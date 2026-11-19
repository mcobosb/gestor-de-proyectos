"""Pruebas del formato dividido, bloqueos y conflictos colaborativos."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from budget_core import ProjectItem
from budget_qt_window import BudgetMainWindow
from budget_reports import ReportSettings
from collaborative_storage import (
    CollaborativeProjectStore,
    ProjectLockManager,
    StorageConflictError,
    project_directory,
)


class CollaborativeStorageTests(unittest.TestCase):
    """Verifica persistencia por UUID, atomicidad y propiedad de locks."""

    @classmethod
    def setUpClass(cls) -> None:
        """Crea la aplicación Qt para las pruebas de integración."""
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def make_window() -> BudgetMainWindow:
        """Construye un proyecto mínimo sin datos privados."""
        window = BudgetMainWindow()
        window.budget_items = [
            ProjectItem("P1", "01#", "ud", "Partida uno", 2, 10),
            ProjectItem("P2", "01#", "m2", "Partida dos", 3, 20),
        ]
        window.chapter_order = ["01#"]
        window.chapter_titles = {"01#": "Movimiento de tierras"}
        return window

    def test_split_project_roundtrips_items_by_uuid(self) -> None:
        """Separa manifiesto, capítulo, partidas y estados de herramientas."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "obra"
            window = self.make_window()
            store = CollaborativeProjectStore.create_from_payload(
                root, window, ReportSettings()
            )
            self.assertEqual(project_directory(root), root)
            self.assertEqual(
                len(list((root / "partidas").glob("part_*.json"))), 2
            )
            self.assertTrue((root / "planificacion" / "tareas_gantt.json").is_file())
            loaded = store.load()
            self.assertEqual(
                [item.storage_id for item in loaded.items],
                [item.storage_id for item in window.budget_items],
            )
            self.assertEqual(loaded.chapter_ids, window.chapter_ids)
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_external_item_change_blocks_stale_save(self) -> None:
        """Un colaborador no sobrescribe una partida cambiada desde su lectura."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "obra"
            window = self.make_window()
            store = CollaborativeProjectStore.create_from_payload(
                root, window, ReportSettings()
            )
            path = store.item_path(window.budget_items[0].storage_id)
            external = json.loads(path.read_text(encoding="utf-8"))
            external["summary"] = "Cambio externo"
            path.write_text(json.dumps(external), encoding="utf-8")
            window.budget_items[0].summary = "Cambio local"
            with self.assertRaises(StorageConflictError):
                store.save(window)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["summary"],
                "Cambio externo",
            )
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_lock_rejects_other_user_and_recovers_expired_lock(self) -> None:
        """Solo el propietario renueva y los locks huérfanos caducan."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partida.json"
            path.write_text("{}", encoding="utf-8")
            alice = ProjectLockManager("Alice", expiration_minutes=5)
            bob = ProjectLockManager("Bob", expiration_minutes=5)
            alice_handle, _ = alice.acquire(path)
            self.assertIsNotNone(alice_handle)
            bob_handle, message = bob.acquire(path)
            self.assertIsNone(bob_handle)
            self.assertIn("Alice", message)
            stale = json.loads(alice_handle.lock_path.read_text(encoding="utf-8"))
            stale["timestamp"] = (
                datetime.now(timezone.utc) - timedelta(minutes=10)
            ).isoformat()
            alice_handle.lock_path.write_text(
                json.dumps(stale), encoding="utf-8"
            )
            bob_handle, _ = bob.acquire(path)
            self.assertIsNotNone(bob_handle)
            self.assertFalse(alice.renew(alice_handle)[0])
            bob.release(bob_handle)
            self.assertFalse(alice_handle.lock_path.exists())

    def test_unchanged_entities_are_not_rewritten(self) -> None:
        """Una edición aislada conserva el mtime de otras partidas."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "obra"
            window = self.make_window()
            store = CollaborativeProjectStore.create_from_payload(
                root, window, ReportSettings()
            )
            untouched = store.item_path(window.budget_items[1].storage_id)
            before = untouched.stat().st_mtime_ns
            window.budget_items[0].summary = "Solo una partida"
            store.save(window)
            self.assertEqual(untouched.stat().st_mtime_ns, before)
            window.is_dirty = False
            window.close()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
