import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

# app.py 模块级会实例化 ParserService(DotNetHplcParser(DEFAULT_DLL))，
# 本机可能缺 GwHPLCAnalysis.dll——import 前先打桩，保证测试可跑（模拟 DLL 存在）。
_parser_mock = mock.MagicMock()
with mock.patch("hplc_web.dotnet_parser.DotNetHplcParser", return_value=_parser_mock):
    from hplc_web.app import create_app

from hplc_web.module_serial_service import ModuleSerialService


class FakeService:
    """最小 ParserService 替身（create_app 仅需 version()）。"""

    def version(self):
        return {"app": "fake", "version": "0"}


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
        self.client = TestClient(create_app(FakeService(), module_serial_service=self.svc))

    def test_version_includes_module_serial(self):
        data = self.client.get("/api/version").json()
        self.assertEqual(data["module_serial_api_revision"], 1)

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


if __name__ == "__main__":
    unittest.main()
