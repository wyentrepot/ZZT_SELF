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


def _e4_summary(source_mac="340100141223", ori_s="009", finl_d="001",
                response_result=0, application_error=None):
    """构造一份已富化的 0x00E4 简单摘要（模拟 Task 3 之后的 simple 字典）。"""
    summary = {
        "FrmType": "分钟采集数据上报",
        "BaseFrmType": "APS",
        "APP_PORT": "11",
        "APP_ID": "00E4",
        "APP_RAW": "11E40000",
        "ORI_S": ori_s,
        "FINL_D": finl_d,
        "application": {
            "structure": "双模4-3",
            "fields": [
                {"name": "源MAC地址", "value": source_mac, "raw": source_mac},
                {"name": "任务号", "raw": 7},
                {"name": "协议类型", "raw": 2},
                {"name": "电表类型", "raw": 0},
                {"name": "响应结果", "raw": response_result},
                {"name": "冻结时刻", "value": "2026-07-31 23:55:00",
                 "raw": "00-55-23-31-07-26"},
                {"name": "上报数量", "raw": 1},
                {"name": "数据长度", "raw": 76},
            ],
            "nested": [],
            "warnings": [],
        },
    }
    if application_error:
        summary["application_error"] = application_error
    return summary


class FakeMinuteParserService:
    """按行顺序返回预置摘要的假解析器，用于分钟上报持久化测试。"""

    def __init__(self, summaries):
        self.summaries = list(summaries)
        self._i = 0
        self.full_calls = 0

    def parse_summary(self, value: str) -> dict:
        simple = self.summaries[self._i] if self._i < len(self.summaries) else {}
        self._i += 1
        return {"frame": {"length": len(value.split()), "normalized_hex": value},
                "simple": simple}

    def parse(self, value: str) -> dict:
        self.full_calls += 1
        result = self.parse_summary(value)
        result["full"] = {"解析层级": ["侦听台外层", "FCH", "MPDU"]}
        return result


def _write_log(lines):
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "minute.log"
    path.write_bytes(b"".join(lines))
    return directory, path


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


EXPECTED_MINUTE_COLUMNS = [
    "frame_id", "log_time", "time_seconds", "cco_tei", "station_key",
    "source_mac", "source_tei", "task_no", "protocol_type", "meter_type",
    "response_result", "freeze_time", "report_count", "data_length",
    "application_error",
]

LOG_LINE = b"[1][00:00:00.000]7E FF 02 FF 00 00 00 00 00 00 7E\r\n"


class MinuteReportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "minute.sqlite3"
        self._services = []

    def tearDown(self):
        for service in self._services:
            service.close()
        self.temp_dir.cleanup()

    def _service(self, summaries):
        service = LogFileService(
            FakeMinuteParserService(summaries), self.db_path
        )
        self._services.append(service)
        return service

    def test_minute_reports_table_has_expected_columns(self):
        self._service([])
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            columns = [
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(minute_reports)"
                ).fetchall()
            ]
        finally:
            connection.close()
        for name in EXPECTED_MINUTE_COLUMNS:
            self.assertIn(name, columns)

    def test_periods_keep_every_report_and_expose_detail_fields(self):
        summary = _e4_summary(source_mac="340100141223", ori_s="009")
        directory, path = _write_log([LOG_LINE, LOG_LINE])
        self.addCleanup(directory.cleanup)
        service = self._service([summary, summary])

        service.index_file(path)
        periods = service.list_minute_periods(period_minutes=15, cco_tei="001")

        self.assertEqual(len(periods), 1)
        period = periods[0]
        self.assertEqual(periods[0]["report_count"], 2)
        self.assertNotIn("duplicate_count", period)
        self.assertEqual(len(period["reports"]), 2)
        report = period["reports"][0]
        self.assertEqual(report["source_mac"], "340100141223")
        self.assertEqual(report["source_tei"], "009")
        self.assertEqual(report["freeze_time"], "2026-07-31 23:55:00")
        self.assertEqual(report["application_raw"], "11E40000")
        self.assertIn("freeze_time", report)

    def test_period_query_validates_arguments(self):
        service = self._service([])
        with self.assertRaises(ValueError):
            service.list_minute_periods(period_minutes=0, cco_tei="001")
        with self.assertRaises(ValueError):
            service.list_minute_periods(period_minutes=1441, cco_tei="001")
        with self.assertRaises(ValueError):
            service.list_minute_periods(period_minutes=15, cco_tei="00g")

    def test_midnight_rollover_creates_separate_periods(self):
        before = b"[1][23:59:59.900]7E FF 02 FF 00 00 00 00 00 00 7E\r\n"
        after = b"[2][00:00:00.100]7E FF 02 FF 00 00 00 00 00 00 7E\r\n"
        directory, path = _write_log([before, after])
        self.addCleanup(directory.cleanup)
        service = self._service([_e4_summary(), _e4_summary()])

        service.index_file(path)
        periods = service.list_minute_periods(period_minutes=15, cco_tei="001")

        self.assertEqual(len(periods), 2)

    def test_destination_filter_filters_by_cco_tei(self):
        to_other = _e4_summary(finl_d="002")
        directory, path = _write_log([LOG_LINE, LOG_LINE])
        self.addCleanup(directory.cleanup)
        service = self._service([_e4_summary(), to_other])

        service.index_file(path)
        periods_001 = service.list_minute_periods(15, "001")
        periods_002 = service.list_minute_periods(15, "002")

        self.assertEqual(sum(p["report_count"] for p in periods_001), 1)
        self.assertEqual(sum(p["report_count"] for p in periods_002), 1)

    def test_malformed_report_is_counted_as_parse_error(self):
        bad = _e4_summary(application_error="业务报文过短")
        directory, path = _write_log([LOG_LINE])
        self.addCleanup(directory.cleanup)
        service = self._service([bad])

        service.index_file(path)
        periods = service.list_minute_periods(15, "001")

        self.assertEqual(periods[0]["report_count"], 1)
        self.assertEqual(periods[0]["reports"][0]["application_raw"], "11E40000")

    def test_period_reports_keep_frame_ids(self):
        summary_a = _e4_summary(source_mac="340100141223")
        summary_b = _e4_summary(source_mac="340100141224")
        directory, path = _write_log([LOG_LINE, LOG_LINE])
        self.addCleanup(directory.cleanup)
        service = self._service([summary_a, summary_b])

        service.index_file(path)
        periods = service.list_minute_periods(15, "001")

        reports = periods[0]["reports"]
        self.assertEqual(len(reports), 2)
        self.assertEqual([report["frame_id"] for report in reports], [1, 2])


if __name__ == "__main__":
    unittest.main()
