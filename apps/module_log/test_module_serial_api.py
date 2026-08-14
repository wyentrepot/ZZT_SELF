import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

# app.py 模块级会实例化 ParserService(DotNetHplcParser(DEFAULT_DLL))，
# 本机可能缺 GwHPLCAnalysis.dll——import 前先打桩，保证测试可跑（模拟 DLL 存在）。
_parser_mock = mock.MagicMock()
with mock.patch("shared.dotnet_parser.DotNetHplcParser", return_value=_parser_mock):
    from module_log.app import create_app

from module_log.module_serial_service import ModuleSerialService


class FakeSerial:
    """pyserial 替身：带 in_waiting/read/write/baudrate，供服务 start。"""

    def __init__(self):
        self.baudrate = 115200
        self.is_open = True
        self.in_waiting = 0
        self._buf = bytearray()
        self.written = b""

    def feed(self, data: bytes):
        self._buf.extend(data)
        self.in_waiting = len(self._buf)

    def read(self, n=1):
        out = bytes(self._buf[:n])
        del self._buf[:n]
        self.in_waiting = len(self._buf)
        return out

    def write(self, data):
        self.written += data
        return len(data)

    def close(self):
        self.is_open = False


class ModuleSerialApiTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = ModuleSerialService(log_dir=Path(self.tmpdir) / "LOG")
        self.client = TestClient(create_app(module_serial_service=self.svc))

    def test_version_includes_module_serial(self):
        data = self.client.get("/api/version").json()
        self.assertEqual(data["module_serial_api_revision"], 2)

    def test_page_served(self):
        resp = self.client.get("/module-serial")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("模块日志", resp.text)

    def test_ports_empty_or_list(self):
        resp = self.client.get("/api/module-serial/ports")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("ports", resp.json())

    def test_status_idle(self):
        resp = self.client.get("/api/module-serial/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state"], "idle")

    def test_start_stop_flow(self):
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            resp = self.client.post(
                "/api/module-serial/start",
                json={"port": "COM_TEST", "baudrate": 115200},
            )
            self.assertEqual(resp.status_code, 202)
            self.assertEqual(resp.json()["state"], "running")
            resp = self.client.get("/api/module-serial/status")
            self.assertEqual(resp.json()["state"], "running")
            resp = self.client.post("/api/module-serial/stop")
            self.assertEqual(resp.json()["state"], "idle")

    def test_write_flow(self):
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            self.client.post(
                "/api/module-serial/start",
                json={"port": "COM_TEST", "baudrate": 115200},
            )
            resp = self.client.post("/api/module-serial/write", json={"data": "AA BB CC"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["sent"], 3)
            self.assertEqual(fake.written, bytes([0xAA, 0xBB, 0xCC]))

    def test_write_rejected_when_not_running(self):
        resp = self.client.post("/api/module-serial/write", json={"data": "AA"})
        self.assertEqual(resp.status_code, 409)

    def test_baudrate_flow(self):
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            self.client.post(
                "/api/module-serial/start",
                json={"port": "COM_TEST", "baudrate": 9600},
            )
            resp = self.client.post("/api/module-serial/baudrate", json={"baudrate": 115200})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(fake.baudrate, 115200)

    def test_flash_requires_bin(self):
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            self.client.post(
                "/api/module-serial/start",
                json={"port": "COM_TEST", "baudrate": 115200},
            )
            resp = self.client.post(
                "/api/module-serial/flash",
                json={"bin_path": "/nonexistent.bin", "slot": 0},
            )
            self.assertEqual(resp.status_code, 404)

    def test_logs_incremental(self):
        self.svc._append_line("RX", "AA BB")
        self.svc._append_line("TX", "CC DD")
        resp = self.client.get("/api/module-serial/logs?after=-1")
        data = resp.json()
        self.assertEqual(len(data["lines"]), 2)
        self.assertEqual(data["last_seq"], 1)
        resp = self.client.get("/api/module-serial/logs?after=1")
        self.assertEqual(resp.json()["lines"], [])

    def test_upload_saves_and_returns_path(self):
        import base64

        data = b"\x11\xE2\x00\x00\x01\x02\x03"
        resp = self.client.post(
            "/api/module-serial/upload",
            json={"name": "fw.bin", "base64": base64.b64encode(data).decode()},
        )
        self.assertEqual(resp.status_code, 200)
        path = resp.json()["path"]
        self.assertTrue(path.endswith("fw.bin"))
        # 文件确实写入（Path 应存在）
        from pathlib import Path

        self.assertTrue(Path(path).is_file())

    def test_upload_rejects_bad_base64(self):
        resp = self.client.post(
            "/api/module-serial/upload",
            json={"name": "fw.bin", "base64": "!!!not-base64!!!"},
        )
        self.assertEqual(resp.status_code, 422)



class ModuleSerialDualChannelApiTest(unittest.TestCase):
    """双通道 API：cco 与 sta 各自独立启动/停止/烧录/日志/发送。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.svc = ModuleSerialService(log_dir=Path(self.tmpdir) / "LOG")
        self.client = TestClient(create_app(module_serial_service=self.svc))

    def test_status_returns_both_channels(self):
        data = self.client.get("/api/module-serial/status").json()
        self.assertIn("channels", data)
        self.assertIn("cco", data["channels"])
        self.assertIn("sta", data["channels"])
        self.assertEqual(data["channels"]["cco"]["state"], "idle")
        self.assertEqual(data["channels"]["sta"]["state"], "idle")

    def test_start_stop_each_channel_independently(self):
        fake_cco = FakeSerial()
        fake_sta = FakeSerial()
        with mock.patch("serial.Serial", side_effect=[fake_cco, fake_sta]):
            r1 = self.client.post("/api/module-serial/start",
                                  json={"port": "COM_CCO", "baudrate": 115200, "channel": "cco"})
            r2 = self.client.post("/api/module-serial/start",
                                  json={"port": "COM_STA", "baudrate": 9600, "channel": "sta"})
        self.assertEqual(r1.status_code, 202)
        self.assertEqual(r2.status_code, 202)
        st = self.client.get("/api/module-serial/status").json()
        self.assertEqual(st["channels"]["cco"]["port"], "COM_CCO")
        self.assertEqual(st["channels"]["sta"]["port"], "COM_STA")
        self.assertEqual(st["channels"]["cco"]["state"], "running")
        self.assertEqual(st["channels"]["sta"]["state"], "running")
        # 只停 cco，sta 仍在运行
        r = self.client.post("/api/module-serial/stop", json={"channel": "cco"})
        self.assertEqual(r.json()["state"], "idle")
        st = self.client.get("/api/module-serial/status").json()
        self.assertEqual(st["channels"]["cco"]["state"], "idle")
        self.assertEqual(st["channels"]["sta"]["state"], "running")

    def test_write_targets_channel(self):
        fake_cco = FakeSerial()
        fake_sta = FakeSerial()
        with mock.patch("serial.Serial", side_effect=[fake_cco, fake_sta]):
            self.client.post("/api/module-serial/start",
                             json={"port": "COM_CCO", "channel": "cco"})
            self.client.post("/api/module-serial/start",
                             json={"port": "COM_STA", "channel": "sta"})
        # 往 sta 发送
        resp = self.client.post("/api/module-serial/write",
                                json={"data": "AA BB", "channel": "sta"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sent"], 2)
        self.assertEqual(fake_sta.written, bytes([0xAA, 0xBB]))
        self.assertEqual(fake_cco.written, b"")

    def test_write_text_appends_newline_and_targets_channel(self):
        fake_cco = FakeSerial()
        fake_sta = FakeSerial()
        with mock.patch("serial.Serial", side_effect=[fake_cco, fake_sta]):
            self.client.post("/api/module-serial/start",
                             json={"port": "COM_CCO", "channel": "cco"})
            self.client.post("/api/module-serial/start",
                             json={"port": "COM_STA", "channel": "sta"})
        resp = self.client.post("/api/module-serial/write_text",
                                json={"text": "reboot", "channel": "sta"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(fake_sta.written, b"reboot\r\n")
        self.assertEqual(fake_cco.written, b"")

    def test_write_text_append_newline_off(self):
        """append_newline=false 时不补换行，原样发送。"""
        fake_cco = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake_cco):
            self.client.post("/api/module-serial/start",
                             json={"port": "COM_CCO", "channel": "cco"})
        resp = self.client.post("/api/module-serial/write_text",
                                json={"text": "reboot", "channel": "cco",
                                      "append_newline": False})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(fake_cco.written, b"reboot")

    def test_write_text_blank_sends_newline(self):
        """空文本 + 默认 append_newline=true → 发送一个换行。"""
        fake_cco = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake_cco):
            self.client.post("/api/module-serial/start",
                             json={"port": "COM_CCO", "channel": "cco"})
        resp = self.client.post("/api/module-serial/write_text",
                                json={"text": "", "channel": "cco"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(fake_cco.written, b"\r\n")

    def test_write_text_rejected_when_channel_not_running(self):
        resp = self.client.post("/api/module-serial/write_text",
                                json={"text": "hello", "channel": "sta"})
        self.assertEqual(resp.status_code, 409)

    def test_logs_per_channel(self):
        self.svc.channel("cco")._append_line("RX", "CCO LINE")
        self.svc.channel("sta")._append_line("RX", "STA LINE")
        cco = self.client.get("/api/module-serial/logs?after=-1&channel=cco").json()
        sta = self.client.get("/api/module-serial/logs?after=-1&channel=sta").json()
        self.assertEqual(len(cco["lines"]), 1)
        self.assertEqual(cco["lines"][0]["text"], "CCO LINE")
        self.assertEqual(len(sta["lines"]), 1)
        self.assertEqual(sta["lines"][0]["text"], "STA LINE")

    def test_flash_rejects_when_channel_not_running(self):
        resp = self.client.post("/api/module-serial/flash",
                                json={"bin_path": "/nope.bin", "channel": "sta"})
        self.assertEqual(resp.status_code, 409)


if __name__ == "__main__":
    unittest.main()