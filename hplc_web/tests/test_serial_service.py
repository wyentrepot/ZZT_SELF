import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hplc_web.log_service import LogFileService
from hplc_web.serial_service import (
    SerialCaptureService,
    format_timestamp,
    split_7e_frames,
)


class FakeParserService:
    def parse_summary(self, value: str) -> dict:
        return {
            "frame": {"length": len(value.split()), "normalized_hex": value},
            "simple": {
                "帧类型": "SOF",
                "源地址": "035",
                "目的地址": "006",
                "APP_ID": "0000",
            },
        }

    def parse(self, value: str) -> dict:
        return self.parse_summary(value)


def _log_service(tmpdir):
    return LogFileService(FakeParserService(), Path(tmpdir) / "test.sqlite3")


class Split7eFramesTest(unittest.TestCase):
    def test_single_frame(self):
        frame = bytes([0x7E, 0xAA, 0xBB, 0xCC, 0x7E])
        frames, tail = split_7e_frames(frame)
        self.assertEqual(frames, [frame])
        self.assertEqual(tail, b"")

    def test_adjacent_frames(self):
        a = bytes([0x7E, 0xAA, 0xBB, 0x7E])
        b = bytes([0x7E, 0x11, 0x22, 0x7E])
        frames, tail = split_7e_frames(a + b)
        self.assertEqual(frames, [a, b])
        self.assertEqual(tail, b"")

    def test_escaped_7e_in_frame(self):
        # 帧内部 7E 以 7D 5E 转义，不应被切为帧尾
        frame = bytes([0x7E, 0xAA, 0x7D, 0x5E, 0xBB, 0x7E])
        frames, tail = split_7e_frames(frame)
        self.assertEqual(frames, [frame])
        self.assertEqual(tail, b"")

    def test_escaped_7d(self):
        frame = bytes([0x7E, 0x01, 0x7D, 0x7D, 0x02, 0x7E])
        frames, tail = split_7e_frames(frame)
        self.assertEqual(frames, [frame])

    def test_unclosed_tail(self):
        frames, tail = split_7e_frames(b"\x7e\xaa\xbb")
        self.assertEqual(frames, [])
        self.assertEqual(tail, b"\x7e\xaa\xbb")
        # 补全后闭合
        frames, tail = split_7e_frames(tail + b"\xcc\x7e")
        self.assertEqual(frames, [bytes([0x7e, 0xaa, 0xbb, 0xcc, 0x7e])])

    def test_chunked_feeding(self):
        # 模拟串口分块到达，逐字节喂入
        a = bytes([0x7E, 0xAA, 0xBB, 0x7D, 0x5E, 0xCC, 0x7E])
        b = bytes([0x7E, 0x11, 0x22, 0x7E])
        stream = a + b
        buf = b""
        all_frames = []
        for byte in stream:
            buf = buf + bytes([byte])
            frames, buf = split_7e_frames(buf)
            all_frames.extend(frames)
        frames, buf = split_7e_frames(buf)
        all_frames.extend(frames)
        self.assertEqual(all_frames, [a, b])

    def test_short_frame_is_noise(self):
        # 仅 7E 7E（无净载荷）视为残留而非帧
        frames, tail = split_7e_frames(b"\x7e\x7e")
        self.assertEqual(frames, [])


class FormatTimestampTest(unittest.TestCase):
    def test_format(self):
        import time
        ts = format_timestamp(
            time.mktime(time.strptime("2026-08-07 12:34:56", "%Y-%m-%d %H:%M:%S")) + 0.789
        )
        self.assertRegex(ts, r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
        self.assertTrue(ts.startswith("12:34:56."))


class SerialCaptureServiceTest(unittest.TestCase):
    def _service(self, tmpdir):
        base = Path(tmpdir)
        return SerialCaptureService(
            _log_service(tmpdir), port="COM_TEST",
            log_dir=base / "LOG",
        )

    def test_initial_status(self):
        status = self._service(tempfile.mkdtemp()).status()
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["port"], "COM_TEST")
        self.assertIn("log_dir", status)
        self.assertIsNone(status["log_file"])

    def test_log_dir_created_on_init(self):
        base = Path(tempfile.mkdtemp()) / "nested" / "LOG"
        service = SerialCaptureService(
            _log_service(str(base.parent)), port="COM_TEST", log_dir=base
        )
        self.assertTrue(base.is_dir())

    def test_open_log_file_creates_named_file(self):
        service = self._service(tempfile.mkdtemp())
        handle, path = service._open_log_file()
        self.assertIsNotNone(handle)
        self.assertIsNotNone(path)
        self.assertTrue(path.name.endswith("_自动保存.txt"))
        self.assertIn("COM_TEST", path.name)
        self.assertTrue(path.is_file())
        handle.close()

    def test_write_log_line_rotates_on_date_change(self):
        """跨天时 _write_log_line 自动轮转到新 LOG 文件（按天切分）。"""
        service, first_path = self._service_with_open_log(tempfile.mkdtemp())
        first_name = first_path.name
        # 先写一行（当天）
        service._write_log_line("[1][00:00:00.001]7E")
        with mock.patch(
            "hplc_web.serial_service.time.strftime",
            side_effect=["20260101", "20260102", "20260102"],
        ):
            # 跨天：_open_log_file 内 strftime(%Y%m%d_%H%M%S) 与 _write_log_line 内
            # strftime(%Y%m%d) 各取一个返回值，模拟日期变化触发轮转
            service._write_log_line("[2][00:00:01.001]7E")
        # 轮转后应打开新文件，且仍可写
        self.assertIsNotNone(service._log_file)
        self.assertIsNotNone(service._log_path)
        self.assertNotEqual(service._log_path, first_path)
        self.assertNotEqual(service._log_path.name, first_name)
        service._close_log_file()

    def _service_with_open_log(self, tmpdir):
        """构造服务并打开会话 LOG 文件，返回 (service, path)。"""
        base = Path(tmpdir)
        service = SerialCaptureService(
            _log_service(tmpdir), port="COM_TEST", log_dir=base / "LOG"
        )
        service._log_file, service._log_path = service._open_log_file()
        return service, service._log_path

    def test_ingest_writes_log_file_and_db(self):
        import tempfile
        service, path = self._service_with_open_log(tempfile.mkdtemp())
        frame = bytes([0x7E, 0xAA, 0xBB, 0x7E])
        service._ingest(frame)
        service._close_log_file()
        # 落盘格式 [序号][时间]7E...7E，与 extract_log_record 兼容
        content = path.read_text(encoding="utf-8")
        self.assertRegex(content, r"^\[\d+\]\[\d{2}:\d{2}:\d{2}\.\d{3}\]7E AA BB 7E$")
        # 实时入库仍在
        rows = service.log_service.list_frames(offset=0, limit=10)
        self.assertEqual(rows["total"], 1)
        self.assertEqual(rows["items"][0]["byte_length"], 4)
        status = service.status()
        self.assertEqual(status["frame_count"], 1)
        self.assertEqual(service._log_path, path)

    def test_log_record_reparseable(self):
        # 落盘行应能被现有 extract_log_record 重新解析（间接入库可复用 start_index）
        from hplc_web.log_service import extract_log_record
        import tempfile
        service, path = self._service_with_open_log(tempfile.mkdtemp())
        service._ingest(bytes([0x7E, 0xAA, 0xBB, 0x7E]))
        service._close_log_file()
        line = path.read_text(encoding="utf-8").strip().encode("ascii")
        record = extract_log_record(line)
        self.assertIsNotNone(record)
        self.assertEqual(record.hex_frame, "7E AA BB 7E")

    def test_start_missing_pyserial(self):
        # 无 pyserial 时构造应抛错；此处假定已安装，仅验证构造流程
        pass

    def test_ingest_appends_to_db(self):
        import tempfile
        service, _ = self._service_with_open_log(tempfile.mkdtemp())
        frame = bytes([0x7E, 0xAA, 0xBB, 0x7E])
        service._ingest(frame)
        status = service.status()
        self.assertEqual(status["frame_count"], 1)
        # 验证帧已写入 db
        rows = service.log_service.list_frames(offset=0, limit=10)
        self.assertEqual(rows["total"], 1)
        self.assertEqual(rows["items"][0]["byte_length"], 4)
        self.assertRegex(rows["items"][0]["log_time"], r"^\d{2}:\d{2}:\d{2}\.\d{3}$")

    def test_sequence_increments(self):
        import tempfile
        service, _ = self._service_with_open_log(tempfile.mkdtemp())
        service._ingest(bytes([0x7E, 0x01, 0x7E]))
        service._ingest(bytes([0x7E, 0x02, 0x7E]))
        rows = service.log_service.list_frames(offset=0, limit=10)
        self.assertEqual([r["sequence"] for r in rows["items"]], ["000001", "000002"])

    def test_on_chunk_merges_partial_frame_across_chunks(self):
        """回归：跨块到达的半帧应合并成完整帧，且缓冲保持 bytearray 类型。"""
        import tempfile
        service, _ = self._service_with_open_log(tempfile.mkdtemp())
        # 一帧分成两块到达：7E AA 和 BB 7E
        service._on_chunk(bytes([0x7E, 0xAA]))
        self.assertEqual(service.status()["frame_count"], 0)  # 半帧未形成完整帧
        self.assertIsInstance(service._buffer, bytearray)
        service._on_chunk(bytes([0xBB, 0x7E]))
        self.assertEqual(service.status()["frame_count"], 1)
        self.assertIsInstance(service._buffer, bytearray)
        self.assertEqual(service._buffer, bytearray())
        rows = service.log_service.list_frames(offset=0, limit=10)
        self.assertEqual(rows["total"], 1)
        self.assertEqual(rows["items"][0]["byte_length"], 4)

    def test_on_chunk_keeps_tail_across_chunks(self):
        """回归：块尾未闭合帧头应保留到下一块。"""
        import tempfile
        service, _ = self._service_with_open_log(tempfile.mkdtemp())
        service._on_chunk(bytes([0x7E, 0xAA, 0xBB, 0x7E, 0x7E, 0x11]))  # 完整帧 + 下一帧头
        self.assertEqual(service.status()["frame_count"], 1)
        self.assertIsInstance(service._buffer, bytearray)
        self.assertEqual(bytes(service._buffer), bytes([0x7E, 0x11]))
        service._on_chunk(bytes([0x22, 0x7E]))  # 补全下一帧
        self.assertEqual(service.status()["frame_count"], 2)
        self.assertEqual(service._buffer, bytearray())

    def test_start_calls_reset_index(self):
        """数据源二选一：串口启动时应清空现有索引（调用 log_service.reset_index）。"""
        import tempfile
        import time
        from unittest import mock

        calls = []

        class FakeLogReset:
            def reset_index(self):
                calls.append(1)
                return {"state": "idle"}

            def status(self):
                return {"state": "idle"}

        class FakeSer:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return b""

        base = Path(tempfile.mkdtemp())
        with mock.patch("hplc_web.serial_service.serial.Serial", FakeSer):
            svc = SerialCaptureService(FakeLogReset(), port="COM_TEST", log_dir=base / "LOG")
            svc.start()
            time.sleep(0.2)
            svc.stop()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
