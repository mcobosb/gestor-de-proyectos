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
    CollaborativeWatchService,
    CollaborativeProjectStore,
    ProjectLockManager,
    StorageConflictError,
    project_directory,
)
from collaborative_crypto import (
    InvalidProjectPasswordError,
    ProjectCrypto,
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

    def test_stale_save_does_not_remove_new_remote_item(self) -> None:
        """Conserva las partidas creadas por otra sesión después de abrir."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "obra"
            first = self.make_window()
            second = self.make_window()
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            store = CollaborativeProjectStore.create_from_payload(
                root, first, ReportSettings()
            )
            stale = CollaborativeProjectStore(root, ReportSettings())
            loaded = stale.load()
            second.budget_items = loaded.items
            second.chapter_order = loaded.chapter_order
            second.chapter_titles = loaded.chapter_titles
            second.chapter_ids = loaded.chapter_ids
            new_item = ProjectItem("P3", "01#", "ud", "Partida remota", 1, 10)
            first.budget_items.append(new_item)
            store.save(first)
            second.budget_items[0].summary = "Cambio local independiente"

            with self.assertRaises(StorageConflictError):
                stale.save(second)

            self.assertTrue(store.item_path(new_item.storage_id).is_file())
            self.assertIn("P3", [item.code for item in store.load().items])

    def test_stale_save_does_not_remove_new_remote_chapter(self) -> None:
        """Conserva los capítulos creados por otra sesión después de abrir."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "obra"
            first = self.make_window()
            second = self.make_window()
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            store = CollaborativeProjectStore.create_from_payload(
                root, first, ReportSettings()
            )
            stale = CollaborativeProjectStore(root, ReportSettings())
            loaded = stale.load()
            second.budget_items = loaded.items
            second.chapter_order = loaded.chapter_order
            second.chapter_titles = loaded.chapter_titles
            second.chapter_ids = loaded.chapter_ids
            first.chapter_order.append("02#")
            first.chapter_titles["02#"] = "Capítulo remoto"
            store.save(first)
            new_chapter = store.chapter_path(first.chapter_ids["02#"])
            second.budget_items[0].summary = "Cambio local independiente"

            with self.assertRaises(StorageConflictError):
                stale.save(second)

            self.assertTrue(new_chapter.is_file())
            self.assertIn("02#", store.load().chapter_order)

    def test_save_removes_unchanged_known_item(self) -> None:
        """Permite eliminar una partida que no ha cambiado externamente."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "obra"
            window = self.make_window()
            self.addCleanup(window.close)
            store = CollaborativeProjectStore.create_from_payload(
                root, window, ReportSettings()
            )
            removed = store.item_path(window.budget_items[0].storage_id)
            window.budget_items.pop(0)
            store.save(window)
            self.assertFalse(removed.exists())

    def test_disjoint_item_fields_merge_without_expanding_lock_scope(self) -> None:
        """Dos ediciones distintas de una partida se fusionan por campo."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "obra"
            window = self.make_window()
            store = CollaborativeProjectStore.create_from_payload(
                root, window, ReportSettings()
            )
            path = store.item_path(window.budget_items[0].storage_id)
            external = json.loads(path.read_text(encoding="utf-8"))
            external["unit_price"] = 12.5
            path.write_text(json.dumps(external), encoding="utf-8")
            window.budget_items[0].summary = "Descripción local"
            store.save(window)
            merged = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(merged["summary"], "Descripción local")
            self.assertEqual(merged["unit_price"], 12.5)
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

    def test_encrypted_project_requires_password_and_hides_json(self) -> None:
        """Cifra manifiesto y partidas con autenticación de contraseña."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "encrypted"
            window = self.make_window()
            crypto = ProjectCrypto.create("contraseña compartida")
            store = CollaborativeProjectStore.create_from_payload(
                root, window, ReportSettings(), crypto
            )
            manifest = json.loads(
                (root / "proyecto.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("items", manifest)
            self.assertIn("payload", manifest)
            self.assertIn("encryption", manifest)
            item_path = store.item_path(window.budget_items[0].storage_id)
            encrypted_item = item_path.read_text(encoding="utf-8")
            self.assertNotIn("Partida uno", encrypted_item)
            with self.assertRaises(ValueError):
                CollaborativeProjectStore(
                    root, ReportSettings()
                ).load()
            metadata = manifest["encryption"]
            wrong = ProjectCrypto.from_metadata("incorrecta", metadata)
            with self.assertRaises(InvalidProjectPasswordError):
                CollaborativeProjectStore(
                    root, ReportSettings(), wrong
                ).load()
            reopened = CollaborativeProjectStore(
                root,
                ReportSettings(),
                ProjectCrypto.from_metadata(
                    "contraseña compartida", metadata
                ),
            ).load()
            self.assertEqual(reopened.items[0].summary, "Partida uno")
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_shared_libraries_are_encrypted_inside_project(self) -> None:
        """No deja bibliotecas ZIP en texto dentro de la carpeta compartida."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            source = root / "source.zip"
            source.write_bytes(b"PK-test-library")
            window = self.make_window()
            store = CollaborativeProjectStore.create_from_payload(
                project, window, ReportSettings(), ProjectCrypto.create("secret")
            )
            target = store.install_library("pptp", source)
            self.assertTrue(target.name.endswith(".enc"))
            self.assertNotEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(
                store.materialize_library("pptp", source.name).read_bytes(),
                source.read_bytes(),
            )
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_public_project_and_libraries_remain_readable_without_password(self) -> None:
        """El modo público conserva estructura colaborativa sin cifrado."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "public"
            source = Path(directory) / "library.zip"
            source.write_bytes(b"PK-public-library")
            window = self.make_window()
            store = CollaborativeProjectStore.create_from_payload(
                root, window, ReportSettings(), None
            )
            manifest = json.loads(
                (root / "proyecto.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("payload", manifest)
            self.assertNotIn("encryption", manifest)
            library = store.install_library("pptp", source)
            self.assertEqual(library.name, source.name)
            self.assertEqual(library.read_bytes(), source.read_bytes())
            reopened = CollaborativeProjectStore(
                root, ReportSettings(), None
            ).load()
            self.assertEqual(reopened.items[0].summary, "Partida uno")
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_presence_heartbeat_counts_multiple_nicknames_and_expires(self) -> None:
        """La presencia muestra apodos activos sin pertenecer al documento."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "shared"
            window = self.make_window()
            store = CollaborativeProjectStore.create_from_payload(
                root, window, ReportSettings(), None
            )
            watcher = CollaborativeWatchService(store)
            watcher.heartbeat("a", "Alicia", "PC-1")
            watcher.heartbeat("b", "Berto", "PC-2")
            people = watcher._presence_snapshot()
            self.assertEqual(
                [person["nickname"] for person in people],
                ["Alicia", "Berto"],
            )
            watcher.leave("b")
            self.assertEqual(len(watcher._presence_snapshot()), 1)
            window.is_dirty = False
            window.close()
            self.application.processEvents()

    def test_presence_is_encrypted_with_protected_project(self) -> None:
        """Los apodos también se protegen en un proyecto cifrado."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "encrypted"
            window = self.make_window()
            store = CollaborativeProjectStore.create_from_payload(
                root,
                window,
                ReportSettings(),
                ProjectCrypto.create("secret"),
            )
            watcher = CollaborativeWatchService(store)
            watcher.heartbeat("session", "Apodo privado", "PC")
            source = (
                root / "presencia" / "session.json"
            ).read_text(encoding="utf-8")
            self.assertNotIn("Apodo privado", source)
            self.assertEqual(
                watcher._presence_snapshot()[0]["nickname"],
                "Apodo privado",
            )
            window.is_dirty = False
            window.close()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
