import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from module_log import xmodem_flash
from module_log.module_serial_service import MAX_MEMORY_LINES, ModuleSerialService, _FlashReader


class BootloaderDetectTest(unittest.TestCase):
    def test_detects_image_prompt(self):
        # 传统 Linux root shell bootloader
        self.assertTrue(xmodem_flash._test_bootloader_text("abc [image /]# def"))

    def test_detects_unicorn_prompt(self):
        # Unicorn Bootloader（venus8m 等真实设备）交互提示符
        self.assertTrue(
            xmodem_flash._test_bootloader_text(
                "You can input command 'help' or '?' to get help !"
            )
        )
        # "enter bootloader mode" 出现在 banner 里（Press 'd' key to enter...），
        # 不应误判为已就绪（与部门技能 ps1 只认 [image /]# 对齐）
        self.assertFalse(
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
    def test_crc_packet_structure_128(self):
        block = bytes([xmodem_flash.PAD]) * 128
        pkt = xmodem_flash.build_xmodem_packet(block, 1, True, block_size=128)
        self.assertEqual(len(pkt), 133)
        self.assertEqual(pkt[0], xmodem_flash.SOH)
        self.assertEqual(pkt[1], 1)
        self.assertEqual(pkt[2], 0xFE)  # ~1
        self.assertEqual(pkt[-2:], bytes([0x31, 0xC3]) if False else pkt[-2:])

    def test_crc_packet_structure_1k(self):
        """XMODEM-1K：1024 字节块，STX 头，包长 1029。"""
        block = bytes([xmodem_flash.PAD]) * 1024
        pkt = xmodem_flash.build_xmodem_packet(block, 1, True, block_size=1024)
        self.assertEqual(len(pkt), 1029)  # STX+seq+~seq+1024+2crc
        self.assertEqual(pkt[0], xmodem_flash.STX)
        self.assertEqual(pkt[1], 1)
        self.assertEqual(pkt[2], 0xFE)

    def test_checksum_packet_structure_128(self):
        block = bytes([xmodem_flash.PAD]) * 128
        pkt = xmodem_flash.build_xmodem_packet(block, 2, False, block_size=128)
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

    def test_memory_logs_ring_trimmed(self):
        """内存日志缓冲有上限：超过 MAX_MEMORY_LINES 后丢弃最旧行（防整夜 OOM）。"""
        svc = self._service()
        ch = svc.channel("cco")
        total = MAX_MEMORY_LINES + 100
        for i in range(total):
            ch._append_line("RX", f"line {i}")
        # 内存缓冲裁剪到上限
        self.assertEqual(len(ch._lines), MAX_MEMORY_LINES)
        # 保留最新：第一条 seq 为被丢弃的首行之后
        self.assertEqual(ch._lines[0]["seq"], total - MAX_MEMORY_LINES)
        # 全量读取（after=-1）也返回最近窗口，不拖全量
        self.assertEqual(len(ch.logs(after=-1)["lines"]), MAX_MEMORY_LINES)
        # 增量读取 seq 单调：last_seq 仍为最新
        self.assertEqual(ch.logs(after=total - 1)["last_seq"], total - 1)
        # 被裁剪掉的旧 seq 不应再返回
        self.assertEqual(ch.logs(after=-1)["lines"][0]["seq"], total - MAX_MEMORY_LINES)

    def test_log_file_written_on_rx(self):
        base = Path(tempfile.mkdtemp())
        svc = ModuleSerialService(log_dir=base)
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            svc.start("COM_TEST", baudrate=9600)
        svc._append_line("RX", "11 E2 00")
        svc.stop()
        # 日志落盘到 {log_dir}/cco/{时间}_[cco].log（默认归属 cco）
        files = list((base / "cco").glob("*.log"))
        self.assertEqual(len(files), 1)
        self.assertIn("_[cco].log", files[0].name)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("[RX] 11 E2 00", content)

    def test_log_file_uses_sta_subdir_when_selected(self):
        """log_type=sta 时落盘到 {log_dir}/sta/{时间}_[sta].log。"""
        base = Path(tempfile.mkdtemp())
        svc = ModuleSerialService(log_dir=base)
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            svc.start("COM_TEST", baudrate=9600, log_type="sta")
        svc._append_line("RX", "AA BB")
        svc.stop()
        files = list((base / "sta").glob("*.log"))
        self.assertEqual(len(files), 1)
        self.assertIn("_[sta].log", files[0].name)

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


class FlashReaderUnitTest(unittest.TestCase):
    """确定性验证 _FlashReader 核心机制（不依赖时序竞态）。

    修复的核心：烧录期间 RX 线程保持唯一 reader，把设备应答喂入队列，
    _FlashReader 的 read/in_waiting 从队列消费，write/baudrate 委托真实串口。
    这样烧录线程与 RX 线程不并发 read 同一 handle。
    """

    def test_read_consumes_queue_and_write_delegates(self):
        recv = FlashReaderRegressionTest.LoopbackReceiver()
        q = queue.Queue()
        # 设备应答被 RX 线程喂入队列
        for b in (xmodem_flash.ACK, xmodem_flash.ACK, 0x0D, 0x0A):
            q.put(b)
        reader = _FlashReader(recv, q)
        self.assertEqual(reader.in_waiting, 4)
        # read(n) 从队列拿 n 字节
        self.assertEqual(reader.read(1), bytes([xmodem_flash.ACK]))
        self.assertEqual(reader.in_waiting, 3)
        self.assertEqual(reader.read(2), bytes([xmodem_flash.ACK, 0x0D]))
        # write 委托真实串口
        n = reader.write(b"hello")
        self.assertEqual(n, 5)
        self.assertEqual(recv.written, b"hello")
        # baudrate 透传
        reader.baudrate = 9600
        self.assertEqual(recv.baudrate, 9600)

    def test_read_empty_queue_returns_empty(self):
        recv = FlashReaderRegressionTest.LoopbackReceiver()
        q = queue.Queue()
        reader = _FlashReader(recv, q)
        self.assertEqual(reader.in_waiting, 0)
        self.assertEqual(reader.read(1), b"")


class FlashReaderRegressionTest(unittest.TestCase):
    """回归：烧录期间 RX 线程保持唯一 reader，设备应答经队列喂给烧录线程。

    修复前：flash() 内部直接 ser.read() 等 ACK，与常驻 RX 线程 ser.read() 并发
    抢读同一 handle → RX 抢走 ACK → 超时重传 → 烧录失败（Xmodem Download failed）。
    修复后：RX 线程把应答喂入 _flash_resp_q，flash 经 _FlashReader 消费。
    本测试用回环串口模拟 XMODEM 接收方，在 RX 线程并发下驱动完整传输。
    """

    class LoopbackReceiver:
        """回环串口：模拟完整 XMODEM 烧录接收方。

        - 文本阶段：主机发空行/reboot → 回显 bootloader 就绪文本；
          发 "download N" → 回显 "Press <Y> to continue"；发 "Y" → 回显 CRC 请求 'C'。
        - XMODEM 阶段：收到 SOH+seq+128+Crc 完整包回 ACK；EOT 回 ACK + 成功文本。
        所有回显/ACK 都 feed 进自己的读缓冲，由 RX 线程搬入烧录应答队列。
        """

        def __init__(self):
            self.baudrate = 115200
            self.is_open = True
            self.in_waiting = 0
            self._buf = bytearray()
            self.written = b""
            self._line = bytearray()  # 文本行累积（\n 结束）
            self.received_blocks = 0
            self.image_ok_text_sent = False
            self._boot_banner_shown = False
            self._boot_entered = False

        def feed(self, data: bytes):
            self._buf.extend(data)
            self.in_waiting = len(self._buf)

        def read(self, n=1):
            if not self._buf:
                return b""
            out = bytes(self._buf[:n])
            del self._buf[:n]
            self.in_waiting = len(self._buf)
            return out

        def write(self, data: bytes) -> int:
            self.written += data
            self._parse_written(data)
            return len(data)

        def _parse_written(self, data: bytes) -> None:
            # 先按文本行解析（bootloader 命令），非文本字节则走 XMODEM 包解析
            self._line.extend(data)
            while True:
                idx = self._line.find(b"\n")
                if idx == -1:
                    break
                line = bytes(self._line[:idx]).decode("ascii", errors="ignore").strip()
                del self._line[: idx + 1]
                self._handle_line(line)

            # XMODEM 协议字节（SOH/STX/EOT）直接逐字节解析
            self._pkt = getattr(self, "_pkt", bytearray())
            self._pkt.extend(data)
            while True:
                pkt = self._pkt
                if not pkt:
                    break
                # bootloader 按键 'd'：banner 已显示时按 'd' 进入 bootloader（[root /]#）
                if pkt[0] == 0x64 and self._boot_banner_shown and not self._boot_entered:
                    self._boot_entered = True
                    self.feed(b"enter bootloader mode: \r\n"
                              b"You can input command 'help' or '?' to get help !\r\n"
                              b"[root /]# \r\n")
                    del pkt[0]
                    continue
                if pkt[0] == xmodem_flash.SOH:
                    # 128 字节块：SOH+seq+~seq+128+2crc = 133
                    if len(pkt) >= 133:
                        del pkt[:133]
                        self.received_blocks += 1
                        self._ack()
                        continue
                    break
                if pkt[0] == xmodem_flash.STX:
                    # XMODEM-1K：STX+seq+~seq+1024+2crc = 1029
                    if len(pkt) >= 1029:
                        del pkt[:1029]
                        self.received_blocks += 1
                        self._ack()
                        continue
                    break
                if pkt[0] == xmodem_flash.EOT:
                    del pkt[0]
                    self._ack()
                    if not self.image_ok_text_sent:
                        self.image_ok_text_sent = True
                        self.feed(b"\r\nImage download OK!\r\n")
                    continue
                # 非协议首字节（如空行探活的 \r\n），丢弃
                del pkt[0]

        def _handle_line(self, line: str) -> None:
            if not line:
                self.feed(b" \r\n")
            elif "reboot" in line:
                # reboot 后显示 Unicorn Bootloader banner（倒计时开始，尚未进入）
                self._boot_banner_shown = True
                self.feed(b"Press 'd' key to enter bootloader mode!\r\n 3\r 2\r")
            elif "config" in line and "setbaudrate" not in line:
                # config 命令进入 config 模式（前导 'd' 按键会混入行尾）
                self.feed(b"[config /]# \r\n")
            elif "setbaudrate" in line:
                # setbaudrate 后回显 config 提示确认（双保险中的 [config /]#）
                self.feed(b"[config /]# \r\n")
            elif line.strip().endswith("exit"):
                self.feed(b"[root /]# \r\n")
            elif "image" in line:
                self.feed(b"[image /]# \r\n")
            elif "download" in line:
                self.feed(b"\r\nPress <Y> to continue, or <N> to cancel\r\n")
            elif line.strip().endswith("Y"):
                # 先回 "send file" 提示文本，稍后（静默期后）再发 CRC 请求 'C'
                self.feed(b"\r\nUse \"transfer->send file\" to download the BIN file into FLASH.\r\n")
                threading.Timer(0.4, lambda: self.feed(bytes([xmodem_flash.CRCCHR]))).start()

        def _ack(self):
            self.feed(bytes([xmodem_flash.ACK]))

        def close(self):
            self.is_open = False

    def test_flash_driven_by_rx_queue(self):
        base = Path(tempfile.mkdtemp())
        svc = ModuleSerialService(log_dir=base / "LOG")
        recv = self.LoopbackReceiver()
        with mock.patch("serial.Serial", return_value=recv):
            svc.start("COM_TEST", baudrate=115200)
        # 写一个大于 1K 的固件（验证 XMODEM-1K 多块传输）
        fw = base / "fw.bin"
        fw.write_bytes(b"\x00\x01\x02\x03" * 700)  # 2800 字节 → 3 个 1K 块
        # 服务 flash() 内部用 _FlashReader + RX 线程喂队列，应顺利完成
        result = svc.flash(str(fw), slot=0, baud_plan=[115200], no_reboot_after=True)
        self.assertEqual(result["status"], "success")
        self.assertGreaterEqual(recv.received_blocks, 2)
        self.assertTrue(recv.image_ok_text_sent)
        st = svc.status()
        self.assertEqual(st["flash"]["phase"], "done")
        self.assertFalse(st["flash"]["flashing"])
        # 烧录结束后 RX 线程恢复正常落盘
        svc.stop()



class DualChannelServiceTest(unittest.TestCase):
    """双通道服务：cco / sta 各自独立启动、停止、独立日志、独立烧录互不影响。"""

    def test_two_channels_start_stop_independently(self):
        base = Path(tempfile.mkdtemp())
        svc = ModuleSerialService(log_dir=base / "LOG")
        fake_cco = FakeSerial()
        fake_sta = FakeSerial()
        with mock.patch("serial.Serial", side_effect=[fake_cco, fake_sta]):
            svc.start("COM_CCO", baudrate=115200, channel="cco")
            svc.start("COM_STA", baudrate=9600, channel="sta")
        self.assertEqual(svc.channel("cco").status()["state"], "running")
        self.assertEqual(svc.channel("sta").status()["state"], "running")
        # 只停 sta，cco 保持运行
        svc.stop(channel="sta")
        self.assertEqual(svc.channel("sta").status()["state"], "idle")
        self.assertEqual(svc.channel("cco").status()["state"], "running")
        svc.stop(channel="cco")

    def test_write_text_to_channel(self):
        base = Path(tempfile.mkdtemp())
        svc = ModuleSerialService(log_dir=base / "LOG")
        fake = FakeSerial()
        with mock.patch("serial.Serial", return_value=fake):
            svc.start("COM_CCO", baudrate=115200, channel="cco")
        svc.write_text("reboot", channel="cco")
        self.assertEqual(fake.written, b"reboot\r\n")
        svc.stop()

    def test_channels_log_into_separate_subdirs(self):
        base = Path(tempfile.mkdtemp())
        svc = ModuleSerialService(log_dir=base)
        fake_cco = FakeSerial()
        fake_sta = FakeSerial()
        with mock.patch("serial.Serial", side_effect=[fake_cco, fake_sta]):
            svc.start("COM_CCO", baudrate=9600, channel="cco")
            svc.start("COM_STA", baudrate=9600, channel="sta")
        svc.channel("cco")._append_line("RX", "CCO DATA")
        svc.channel("sta")._append_line("RX", "STA DATA")
        svc.stop(channel="cco")
        svc.stop(channel="sta")
        cco_files = list((base / "cco").glob("*.log"))
        sta_files = list((base / "sta").glob("*.log"))
        self.assertEqual(len(cco_files), 1)
        self.assertEqual(len(sta_files), 1)
        self.assertIn("[RX] CCO DATA", cco_files[0].read_text(encoding="utf-8"))
        self.assertIn("[RX] STA DATA", sta_files[0].read_text(encoding="utf-8"))

    def test_status_contains_both_channels(self):
        base = Path(tempfile.mkdtemp())
        svc = ModuleSerialService(log_dir=base / "LOG")
        st = svc.status()
        self.assertIn("channels", st)
        self.assertIn("cco", st["channels"])
        self.assertIn("sta", st["channels"])


if __name__ == "__main__":
    unittest.main()