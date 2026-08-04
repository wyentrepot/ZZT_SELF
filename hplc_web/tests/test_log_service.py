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
                response_result=0, report_count=1, data_length=76,
                application_error=None):
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
                {"name": "上报数量", "raw": report_count},
                {"name": "数据长度", "raw": data_length},
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

    def test_delete_config_stats_counts_and_dedupes_application_layer(self):
        summaries = [
            _del_config_summary(),  # 删除 seq=0001 mac=A
            _del_config_summary(seq="0x0001", mac="11:11:50:00:00:66"),  # 同键重复(网络层重传)
            _del_config_summary(seq="0x0002", mac="12:11:50:00:00:66"),  # 删除 seq=0002 mac=B
            _del_config_summary(flag="启用"),  # 启用：不计
            _del_config_summary(ori_s="002"),  # 其他 CCO：不计
        ]
        parser = FakeMinuteParserService(summaries)
        service = LogFileService(parser, Path(self.temp_dir.name) / "del.sqlite3")
        try:
            directory, path = _write_log(
                [LOG_LINE] * len(summaries)
            )
            try:
                service.index_file(path)
                stats = service.delete_config_stats("001")
                self.assertEqual(stats["down_total"], 3)
                self.assertEqual(stats["down_deduped"], 2)
                self.assertEqual(stats["up_total"], 0)
                self.assertEqual(stats["up_deduped"], 0)
                self.assertEqual(stats["up_success"], 0)
                self.assertEqual(stats["up_fail"], 0)
            finally:
                directory.cleanup()
        finally:
            service.close()

    def test_delete_config_stats_counts_and_dedupes_upload_acks(self):
        summaries = [
            _e2_up_summary(flag="00", result="00"),  # 删除+成功
            _e2_up_summary(flag="00", result="00"),  # 同键重复抓取：去重
            _e2_up_summary(seq="039529", mac_bytes="121150000066",
                           flag="00", result="01"),  # 删除+失败
        ]
        parser = FakeMinuteParserService(summaries)
        service = LogFileService(parser, Path(self.temp_dir.name) / "up.sqlite3")
        try:
            directory, path = _write_log([LOG_LINE] * len(summaries))
            try:
                service.index_file(path)
                stats = service.delete_config_stats("001")
                self.assertEqual(stats["up_total"], 3)
                self.assertEqual(stats["up_deduped"], 2)
                self.assertEqual(stats["up_success"], 1)
                self.assertEqual(stats["up_fail"], 1)
                details = service.delete_config_details("001")
                self.assertEqual(len(details["up"]), 2)
                self.assertEqual(len(details["down"]), 0)
                self.assertEqual(details["up"][0]["del_flag"], "删除")
            finally:
                directory.cleanup()
        finally:
            service.close()

    def test_delete_config_stats_rejects_bad_tei(self):
        with self.assertRaises(ValueError):
            self.service.delete_config_stats("not-tei")


def _e2_up_summary(flag="00", result="00", seq="039528",
                   mac_bytes="111150000066", task="02", period="05"):
    """构造一条发往 CCO（FINL_D=001）的 00E2 上行应答 simple 摘要。

    APP_RAW 结构：11 E2 00 00 | C1 | 序号3 | 00 00 | 源MAC6 | 任务号 | 组合位 | 采集周期
    组合位 byte[17]：bit0=启用/删除标志(0=删除,1=启用)，
                     bit1=结果(0=设置成功,1=设置失败)。
    """
    byte17 = int(flag, 16) | (int(result, 16) << 1)
    raw = f"11E20000C1{seq}0000{mac_bytes}{task}{byte17:02X}{period}"
    return {
        "FrmType": "分钟采集任务配置",
        "BaseFrmType": "APS",
        "APP_ID": "00E2",
        "APP_RAW": raw,
        "ORI_S": "03F",
        "FINL_D": "001",
        "application": {
            "structure": "双模4-3",
            "fields": [],
            "nested": [],
            "warnings": [],
        },
    }


def _del_config_summary(ori_s="001", flag="删除", seq="0x0001",
                        mac="11:11:50:00:00:66"):
    """构造一条「分钟采集任务配置（0x00E2）- 启动/删除标志」的简单摘要。"""
    return {
        "FrmType": "分钟采集任务配置",
        "BaseFrmType": "APS",
        "APP_ID": "00E2",
        "APP_RAW": "11E20000",
        "ORI_S": ori_s,
        "FINL_D": "03F",
        "application": {
            "structure": "双模4-3",
            "fields": [
                {"name": "报文序号", "value": seq, "raw": seq},
                {"name": "目的MAC地址", "value": mac, "raw": mac},
                {"name": "启动/删除标志", "value": flag, "raw": flag},
                {"name": "任务号", "value": 2, "raw": 2},
            ],
            "nested": [],
            "warnings": [],
        },
    }


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

    def test_report_marks_response_result_two_as_no_freeze_data(self):
        summary = _e4_summary(response_result=2, report_count=0, data_length=0)
        directory, path = _write_log([LOG_LINE])
        self.addCleanup(directory.cleanup)
        service = self._service([summary])

        service.index_file(path)
        report = service.list_minute_periods(15, "001")[0]["reports"][0]

        self.assertEqual(report["response_result"], 2)
        self.assertEqual(report["report_count"], 0)
        self.assertEqual(report["data_length"], 0)
        self.assertEqual(report["data_status"], "无冻结数据")

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
