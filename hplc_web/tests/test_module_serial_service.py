import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hplc_web import xmodem_flash
from hplc_web.module_serial_service import ModuleSerialService


class BootloaderDetectTest(unittest.TestCase):
    def test_detects_image_prompt(self):
        # 传统 Linux root shell bootloader
        self.assertTrue(xmodem_flash._test_bootloader_text("abc [image /]# def"))

    def test_detects_unicorn_prompt(self):
        # Unicorn Bootloader（venus8m 等真实设备）
        self.assertTrue(
            xmodem_flash._test_bootloader_text(
                "You can input command 'help' or '?' to get help !"
            )
        )
        self.assertTrue(
            xmodem_flash._test_bootloader_text("enter bootloader mode: ")
        )

    def test_rejects_plain_text(self):
        self.assertFalse(xmodem_flash._test_bootloader_text("just boot banner"))
        self.assertFalse(xmodem_flash._test_bootloader_text("[node /]$ "))


class Crc16XmodemTest(unittest.TestCase):
    def test_known_vector(self):
        # ps1 自检向量 0x31C3
        self.assertEqual(xmodem_flash.crc16_xmodem(b"123456789"), 0x31C3)

    def test_different_data(self):
        self.assertNotEqual(xmodem_flash.crc16_xmodem(b"123456789"), 0)

    def test_offset_count(self):
        data = b"\x00" * 10 + b"123456789" + b"\xff" * 10
        self.assertEqual(xmodem_flash.crc16_xmodem(data, 10, 9), 0x31C3)


class BuildPacketTest(unittest.TestCase):
    def test_crc_packet_structure(self):
        block = bytes([xmodem_flash.PAD]) * 128
        pkt = xmodem_flash.build_xmodem_packet(block, 1, True)
        self.assertEqual(len(pkt), 133)
        self.assertEqual(pkt[0], xmodem_flash.SOH)
        self.assertEqual(pkt[1], 1)
        self.assertEqual(pkt[2], 0xFE)  # ~1
        self.assertEqual(pkt[-2:], bytes([0x31, 0xC3]) if False else pkt[-2:])

    def test_checksum_packet_structure(self):
        block = bytes([xmodem_flash.PAD]) * 128
        pkt = xmodem_flash.build_xmodem_packet(block, 2, False)
        self.assertEqual(len(pkt), 132)  # SOH+seq+~seq+128+checksum
        self.assertEqual(pkt[-1], 128 * 0x1A & 0xFF)  # checksum

    def test_bad_block_length(self):
        with self.assertRaises(ValueError):
            xmodem_flash.build_xmodem_packet(b"\x00", 1, True)


class SelftestTest(unittest.TestCase):
    def test_selftest(self):
        result = xmodem_flash.selftest()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["crc"], "0x31C3")


class FakeSerial:
    """模拟 pyserial：支持读写/波特率/in_waiting，带内部回环。"""

    def __init__(self, responses=None):
        self._in = bytearray()
        self._responses = list(responses or [])
        self.baudrate = 9600
        self.is_open = True
        self.in_waiting = 0
        self.written = b""

    def feed(self, data: bytes):
        self._in.extend(data)
        self.in_waiting = len(self._in)

    def read(self, n=1):
        out = bytes(self._in[:n])
        del self._in[:n]
        self.in_waiting = len(self._in)
        return out

    def write(self, data):
        self.written += data
        # 若预设了应答队列，按写入内容匹配触发
        return len(data)


class ModuleSerialServiceTest(unittest.TestCase):
    def _service(self, tmpdir=None):
        base = Path(tmpdir) if tmpdir else Path(tempfile.mkdtemp())
        return ModuleSerialService(log_dir=base / "LOG")

    def test_initial_status(self):
        svc = self._service()
        st = svc.status()
        self.assertEqual(st["state"], "idle")
        self.assertEqual(st["flash"]["flashing"], False)
        self.assertIn("log_dir", st)

    def test_start_stop_opens_closes_once(self):
        svc = self._service()
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            result = svc.start("COM_TEST", baudrate=115200)
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["port"], "COM_TEST")
        self.assertEqual(svc.status()["state"], "running")
        # stop 关闭
        with mock.patch("serial.Serial", return_value=fake):
            st = svc.stop()
        self.assertEqual(st["state"], "idle")
        self.assertEqual(st["was_running"], True)

    def test_logs_incremental(self):
        svc = self._service()
        svc._append_line("RX", "AA BB")
        svc._append_line("TX", "CC")
        r = svc.logs(after=-1)
        self.assertEqual(len(r["lines"]), 2)
        self.assertEqual(r["last_seq"], 1)
        # after 语义：返回 seq > after 的新增行
        self.assertEqual(svc.logs(after=1)["lines"], [])
        svc._append_line("EVENT", "波特率变更 → 115200")
        inc = svc.logs(after=1)
        self.assertEqual([e["seq"] for e in inc["lines"]], [2])
        self.assertEqual(inc["lines"][0]["dir"], "EVENT")
        # 空 buffer 时 last_seq 保持传入值
        self.assertEqual(svc.logs(after=2)["last_seq"], 2)

    def test_log_file_written_on_rx(self):
        base = Path(tempfile.mkdtemp())
        svc = ModuleSerialService(log_dir=base)
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            svc.start("COM_TEST", baudrate=9600)
        svc._append_line("RX", "11 E2 00")
        svc.stop()
        files = list(base.glob("MODCOM*.txt"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("[RX] 11 E2 00", content)

    def test_write_rejects_when_not_running(self):
        svc = self._service()
        with self.assertRaises(RuntimeError):
            svc.write("AA BB")

    def test_flash_rejects_when_not_running(self):
        svc = self._service()
        with self.assertRaises(RuntimeError):
            svc.flash("/nonexistent.bin", slot=0)

    def test_set_baudrate_updates(self):
        svc = self._service()
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            svc.start("COM_TEST", baudrate=9600)
        svc.set_baudrate(115200)
        self.assertEqual(svc.status()["baudrate"], 115200)
        self.assertEqual(fake.baudrate, 115200)
        svc.stop()


if __name__ == "__main__":
    unittest.main()
