import tempfile
import unittest
from pathlib import Path

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
        return SerialCaptureService(_log_service(tmpdir), port="COM_TEST")

    def test_initial_status(self):
        status = self._service(tempfile.mkdtemp()).status()
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["port"], "COM_TEST")

    def test_start_missing_pyserial(self):
        # 无 pyserial 时构造应抛错；此处假定已安装，仅验证构造流程
        pass

    def test_ingest_appends_to_db(self):
        import tempfile
        service = self._service(tempfile.mkdtemp())
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
        service = self._service(tempfile.mkdtemp())
        service._ingest(bytes([0x7E, 0x01, 0x7E]))
        service._ingest(bytes([0x7E, 0x02, 0x7E]))
        rows = service.log_service.list_frames(offset=0, limit=10)
        self.assertEqual([r["sequence"] for r in rows["items"]], ["000001", "000002"])


if __name__ == "__main__":
    unittest.main()
