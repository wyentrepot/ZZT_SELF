"""模拟电表串口通道：独占打开串口，读线程切帧 → 电表脑应答 → 回写。

与 sim_concentrator.serial_io（1376.2 主站侧）相对，本通道是从站侧：
- 切帧复用 parser_lib.adapters.adapter_645.DLT645Adapter.try_extract
  （兼容转义帧与未转义帧、自动跳过前导脏字节）；
- 收到完整帧即交 MeterBrain.handle_frame，应答帧立即回写；
- 支持 17H 更改通信速率后的波特率热切换（先回帧，再重开串口，读线程
  跨重开存活）；
- 端口经 serial_for_url 打开，除 COMx / /dev/ttyUSB* 外支持 loop://
  （pyserial 虚拟回环，供无硬件自动化测试）。

用法：
    ch = MeterSerialChannel(MeterBrain(), port="COM5", baudrate=2400)
    ch.open()
    ...
    ch.close()
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Optional

try:
    import serial
    from serial.tools import list_ports
    _SERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover - 无 pyserial 环境
    serial = None
    list_ports = None
    _SERIAL_AVAILABLE = False

from parser_lib.adapters.adapter_645 import DLT645Adapter
from parser_lib.adapters.adapter_698 import DLT69845Adapter

from sim_meter.journal import MeterJournal
from sim_meter.meter import MeterBrain


def available() -> bool:
    return _SERIAL_AVAILABLE


def list_serial_ports() -> list[str]:
    """枚举本机串口设备名（无 pyserial 时返回空表）。"""
    if not _SERIAL_AVAILABLE:
        return []
    out = []
    for info in list_ports.comports():
        device = str(getattr(info, "device", "") or "").strip()
        if device:
            out.append(device)
    return out


def hex_to_bytes(hex_str: str) -> bytes:
    return bytes.fromhex(str(hex_str).strip().replace(" ", ""))


class MeterSerialChannel:
    """电表从站串口通道：读线程切帧应答 + 写锁 + 波特率热切换。"""

    _ADAPTER = DLT645Adapter()
    _ADAPTER_698 = DLT69845Adapter()
    _STALL_RESYNC = 0.5                   # 假头重同步阈值（秒）

    def __init__(self, brain: MeterBrain, *, port: str, baudrate: Optional[int] = None,
                 bytesize: Optional[int] = None, parity: Optional[str] = None,
                 stopbits: Optional[int] = None, timeout: float = 0.2,
                 write_timeout: float = 5.0,
                 journal: Optional[MeterJournal] = None):
        if not _SERIAL_AVAILABLE:
            raise RuntimeError("缺少 pyserial 依赖，请先安装：pip install pyserial")
        self.brain = brain
        self.port = str(port)
        self.baudrate = int(baudrate if baudrate is not None else brain.baudrate)
        self.bytesize = int(bytesize if bytesize is not None else brain.bytesize)
        self.parity = str(parity if parity is not None else brain.parity).upper()
        self.stopbits = int(stopbits if stopbits is not None else brain.stopbits)
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.journal = journal
        self._ser = None
        self._read_stop = threading.Event()
        self._read_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._reopen_lock = threading.RLock()
        # 接收队列有界（REQS-0039 LR-5 同口径）：48h 高频抄表下无人消费时
        # 无界队列会单调增长；满时丢最旧帧并计数。
        self._rx_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=2048)
        self._rx_history: list = []
        self._rx_dropped = 0
        self._last_error: str = ""
        self._open = False

    # -- 生命周期 -------------------------------------------------------
    def _open_port(self):
        return serial.serial_for_url(
            self.port, baudrate=self.baudrate,
            bytesize=self.bytesize, parity=self.parity, stopbits=self.stopbits,
            timeout=self.timeout, write_timeout=self.write_timeout)

    def open(self) -> bool:
        with self._reopen_lock:
            if self._open:
                return True
            self._ser = self._open_port()
            self._open = True
            self._read_stop.clear()
            self._read_thread = threading.Thread(
                target=self._run, name=f"simeter-rx-{self.port}", daemon=True)
            self._read_thread.start()
            return True

    def close(self) -> None:
        self._read_stop.set()
        t, self._read_thread = self._read_thread, None
        if t is not None:
            t.join(timeout=2.0)
        if self._ser is not None:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._open = False
        if self.journal is not None:
            self.journal.close_file()

    def is_open(self) -> bool:
        return self._open

    # -- 波特率热切换 ----------------------------------------------------
    def set_baudrate(self, baudrate: int) -> int:
        """重开串口切换波特率；未打开时仅记录参数。返回生效波特率。

        读线程每轮重新取 self._ser，重开窗口内读失败自动重试，线程不退出。
        """
        baudrate = int(baudrate)
        with self._reopen_lock:
            old, self.baudrate = self.baudrate, baudrate
            if not self._open:
                return baudrate
            old_ser, self._ser = self._ser, None
            try:
                if old_ser is not None:
                    try:
                        old_ser.close()
                    except Exception:
                        pass
                self._ser = self._open_port()
            except Exception as exc:
                self._last_error = f"切换波特率到 {baudrate} 失败: {exc!r}"
                self.baudrate = old
                try:
                    self._ser = self._open_port()
                    self._last_error = ""
                except Exception:
                    self._open = False
                raise RuntimeError(self._last_error or "切换波特率失败") from exc
        return self.baudrate

    # -- 读线程 ----------------------------------------------------------
    @classmethod
    def _try_extract(cls, buf: bytes):
        """统一切帧入口：返回 (raw, consumed) 或 None。

        - 先跳过前导 FE（698/645 线上帧均可能带 FE 前导），记录偏移并计入
          consumed，buf 中的 FE 被一并消费；
        - buf[pos] == 0x68 且 buf[pos+3] != 0x68 且 698 结构成立 → 698 优先
          （698 帧第 3 字节是控制域，645/1376.2 在该位置是地址/帧头 0x68；
          645 帧控制域后的数据区也可能让 698 误判，故以 698 结构校验为准）；
        - 否则回退 645 adapter（含转义帧处理）；
        - 698 结构不完整（长度不足等）时同样回退 645，保持既有 645 行为。
        """
        pos = 0
        n = len(buf)
        while pos < n and buf[pos] == 0xFE:
            pos += 1
        body = buf[pos:]
        if not body or body[0] != 0x68:
            return None
        if len(body) >= 4 and body[3] != 0x68:
            res = cls._ADAPTER_698.try_extract(body)
            if res is not None:
                return res.raw, res.consumed + pos
        res = cls._ADAPTER.try_extract(body)
        if res is not None:
            return res.raw, res.consumed + pos
        return None

    def _run(self) -> None:
        """读线程：累积字节 → 去前导 FE → 切帧 → 应答回写。

        重同步（2026-09-29 台架实测教训）：DLT645Adapter.try_extract 只从
        buf[0] 切帧，脏字节或假头（如实机 STA 交替下发的 698 帧，其 L 字节
        可达 0xAA）会让切帧器无限等待。故：
        - buf[0] 非 0x68：立即跳到下一个 0x68（脏字节重同步）；
        - buf[0] 为 0x68 但超过 _STALL_RESYNC 秒无切帧进展：判定假头，
          步进丢弃重同步（真实帧在最低 600bps 下整帧也远小于该时限）。
        """
        buf = b""
        fail_count = 0
        last_progress = time.monotonic()
        while not self._read_stop.is_set():
            ser = self._ser
            if ser is None:                     # 波特率热切换窗口
                time.sleep(0.02)
                continue
            try:
                data = ser.read(512)
                fail_count = 0
            except Exception as exc:
                if self._read_stop.is_set():
                    break
                if ser is self._ser:            # 非热切换引起的持续失败
                    fail_count += 1
                    if fail_count >= 5:
                        self._last_error = f"串口读失败退出: {exc!r}"
                        self._open = False
                        break
                time.sleep(0.02)
                continue
            if data:
                buf += data
            while True:
                extracted = self._try_extract(buf)
                if extracted is None:
                    if buf and buf[0] != 0x68:
                        idx = buf.find(0x68)    # 脏字节/FE 前导重同步
                        buf = buf[idx:] if idx >= 0 else b""
                    elif (buf and time.monotonic() - last_progress
                          > self._STALL_RESYNC):
                        # 假头毒化（如实机 STA 交替下发的异常帧，其 L 字节可达
                        # 0xAA）：跳到 buf 内下一个 0x68 重新同步
                        idx = buf.find(0x68, 1)
                        buf = buf[idx:] if idx >= 0 else b""
                    break
                raw, consumed = extracted
                self._rx_history.append(raw)
                if len(self._rx_history) > 1000:
                    del self._rx_history[: len(self._rx_history) - 1000]
                buf = buf[consumed:]
                last_progress = time.monotonic()
                self._on_frame(raw)

    def _enqueue_rx(self, frame: bytes) -> None:
        """入队；队列满时丢最旧一帧再放（有界背压，防长跑内存单调增长）。"""
        try:
            self._rx_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._rx_queue.get_nowait()
                self._rx_dropped += 1
            except queue.Empty:
                pass
            try:
                self._rx_queue.put_nowait(frame)
            except queue.Full:
                self._rx_dropped += 1

    def dropped_frames(self) -> int:
        """接收队列满后被丢弃的帧累计数（背压指标）。"""
        return self._rx_dropped

    def _on_frame(self, raw: bytes) -> None:
        result = self.brain.handle_frame(raw)
        note = dict(result.note) if result is not None else {"error": "处理异常"}
        self._enqueue_rx(raw)
        if self.journal is not None:
            self.journal.append("rx", raw, note)
        if result is not None and (result.reply is not None
                                   or result.reply_wire is not None):
            time.sleep(0.01)                    # 从站应答间隔
            wire = result.reply_wire
            try:
                self._write(wire)
                note["reply_hex"] = wire.hex().upper()
                self._enqueue_rx(wire)
            except Exception as exc:
                note["reply_error"] = repr(exc)
            if self.journal is not None:
                self.journal.append("tx", wire, note)
        if result is not None and result.action is not None:
            kind, value = result.action
            if kind == "baud":
                # 等应答帧完全发出后重开串口（写锁串行 + 间隔）
                time.sleep(0.1)
                try:
                    self.set_baudrate(value)
                    note["baudrate_switched"] = value
                except Exception as exc:
                    note["baudrate_error"] = repr(exc)
                if self.journal is not None:
                    self.journal.event(note)

    # -- 发送 ------------------------------------------------------------
    def _write(self, raw: bytes) -> int:
        with self._write_lock:
            if not self._open or self._ser is None:
                raise RuntimeError("串口未打开")
            return self._ser.write(bytes(raw))

    def send_frame(self, raw: bytes) -> int:
        """手动发送原始帧（调试注入），返回发送字节数。"""
        n = self._write(raw)
        if self.journal is not None:
            self.journal.append("tx", raw, {"kind": "manual_send"})
        return n

    def send_hex(self, hex_str: str) -> int:
        return self.send_frame(hex_to_bytes(hex_str))

    # -- 接收 ------------------------------------------------------------
    def recv_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """阻塞取一帧（rx=主站请求 / tx=本表应答）；超时返回 None。"""
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def rx_history(self) -> list:
        return list(self._rx_history)

    def status(self) -> dict:
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "bytesize": self.bytesize,
            "parity": self.parity,
            "stopbits": self.stopbits,
            "open": self._open,
            "last_error": self._last_error,
        }
