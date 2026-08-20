import tempfile
import unittest
from pathlib import Path


class ListenerIndexRegistryTests(unittest.TestCase):
    def test_new_indexes_keep_same_frame_id_readable_in_their_own_database(self):
        from listener.index_registry import ListenerIndexRegistry
        from listener.log_service import LogFileService

        with tempfile.TemporaryDirectory() as temp_dir:
            registry = ListenerIndexRegistry(Path(temp_dir) / "indexes")
            first = registry.create_index(kind="file", source_path="/logs/first.txt")
            first_service = LogFileService(None, registry.database_path_for(first["index_id"]))
            first_service.append_frames([("000001", "10:00:00.000", "7E 01 7E")])

            second = registry.create_index(kind="serial", source_path="/logs/second.txt")
            second_service = LogFileService(None, registry.database_path_for(second["index_id"]))
            second_service.append_frames([("000001", "10:01:00.000", "7E 02 7E")])

            self.assertNotEqual(first["index_id"], second["index_id"])
            self.assertEqual(registry.current_index_id(), second["index_id"])
            self.assertEqual(first_service.get_frame(1)["raw_hex"], "7E 01 7E")
            self.assertEqual(second_service.get_frame(1)["raw_hex"], "7E 02 7E")
            self.assertEqual(
                registry.get_index(first["index_id"])["source_path"], "/logs/first.txt"
            )


    def test_service_reset_switches_current_index_without_erasing_history(self):
        from listener.index_registry import ListenerIndexRegistry
        from listener.log_service import LogFileService

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registry = ListenerIndexRegistry(root / "indexes")
            service = LogFileService(None, root / "legacy.sqlite3", index_registry=registry)
            first_id = service.status()["index_id"]
            service.append_frames([("000001", "10:00:00.000", "7E 11 7E")])

            service.reset_index()
            second_id = service.status()["index_id"]

            self.assertNotEqual(first_id, second_id)
            self.assertEqual(registry.current_index_id(), second_id)
            self.assertEqual(service.open_index(first_id).get_frame(1)["raw_hex"], "7E 11 7E")

            reloaded = LogFileService(None, root / "legacy.sqlite3", index_registry=registry)
            self.assertEqual(reloaded.status()["index_id"], second_id)


    def test_index_file_creates_a_new_current_database_and_retains_previous_file(self):
        from listener.index_registry import ListenerIndexRegistry
        from listener.log_service import LogFileService

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_source = root / "first.txt"
            second_source = root / "second.txt"
            first_source.write_text("[000001][10:00:00.000]7E 31 7E\n", encoding="ascii")
            second_source.write_text("[000001][10:01:00.000]7E 32 7E\n", encoding="ascii")
            registry = ListenerIndexRegistry(root / "indexes")
            service = LogFileService(None, root / "legacy.sqlite3", index_registry=registry)

            service.index_file(first_source)
            first_id = service.status()["index_id"]
            service.index_file(second_source)
            second_id = service.status()["index_id"]

            self.assertNotEqual(first_id, second_id)
            self.assertEqual(service.get_frame(1)["raw_hex"], "7E 32 7E")
            self.assertEqual(service.open_index(first_id).get_frame(1)["raw_hex"], "7E 31 7E")


if __name__ == "__main__":
    unittest.main()
