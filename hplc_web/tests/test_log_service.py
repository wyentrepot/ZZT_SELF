import tempfile
import unittest
from pathlib import Path

from hplc_web.log_service import LogFileService, extract_log_record


SAMPLE_FILE = Path(__file__).parent / "data" / "gw_log_sample.txt"


class FakeParserService:
    def __init__(self):
        self.summary_calls = 0
        self.full_calls = 0

    def parse_summary(self, value: str) -> dict:
        self.summary_calls += 1
        return {
            "frame": {"length": len(value.split()), "normalized_hex": value},
            "simple": {
                "帧类型": "SOF",
                "源地址": "035",
                "目的地址": "006",
                "通道": "载波",
            },
        }

    def parse(self, value: str) -> dict:
        self.full_calls += 1
        result = self.parse_summary(value)
        result["full"] = {"解析层级": ["侦听台外层", "FCH", "MPDU"]}
        return result


class LogRecordTests(unittest.TestCase):
    def test_extracts_sequence_time_and_frame(self):
        record = extract_log_record(
            b"[406727][00:00:00.016]7E FF 02 FF 24 6B 7E\r\n"
        )
        self.assertEqual(record.sequence, "406727")
        self.assertEqual(record.log_time, "00:00:00.016")
        self.assertEqual(record.hex_frame, "7E FF 02 FF 24 6B 7E")

    def test_ignores_line_without_complete_frame(self):
        self.assertIsNone(extract_log_record(b"[1][00:00:00.001]not a frame"))


class LogFileServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.parser = FakeParserService()
        self.service = LogFileService(
            self.parser, Path(self.temp_dir.name) / "index.sqlite3"
        )

    def tearDown(self):
        self.service.close()
        self.temp_dir.cleanup()

    def test_indexes_sample_and_returns_bounded_pages(self):
        status = self.service.index_file(SAMPLE_FILE)
        self.assertEqual(status["state"], "completed")
        self.assertGreater(status["frame_count"], 10)
        self.assertEqual(self.parser.full_calls, 0)

        page = self.service.list_frames(offset=0, limit=5)
        self.assertEqual(len(page["items"]), 5)
        self.assertEqual(page["total"], status["frame_count"])
        self.assertEqual(page["items"][0]["sequence"], "406727")
        self.assertEqual(page["items"][0]["summary"]["帧类型"], "SOF")

    def test_full_parse_is_deferred_until_detail_is_requested(self):
        self.service.index_file(SAMPLE_FILE)
        first = self.service.list_frames(offset=0, limit=1)["items"][0]

        self.assertEqual(self.parser.full_calls, 0)
        detail = self.service.get_frame(first["id"])

        self.assertEqual(self.parser.full_calls, 1)
        self.assertIn("full", detail["analysis"])
        self.assertTrue(detail["raw_hex"].startswith("7E "))

    def test_rejects_page_larger_than_safe_limit(self):
        self.service.index_file(SAMPLE_FILE)
        with self.assertRaises(ValueError):
            self.service.list_frames(offset=0, limit=501)

    def test_missing_source_is_reported(self):
        with self.assertRaises(FileNotFoundError):
            self.service.index_file(Path(self.temp_dir.name) / "missing.txt")


if __name__ == "__main__":
    unittest.main()
