"""可读写串口通道：模拟集中器与 CCO 模块通信的串口 IO。

区别于 listener 的只读监听（SerialCaptureService），本通道：
- 独占打开一个 COM；
- 读线程持续读字节，按 1376.2 帧结构切帧入队（复用 frame_codec.extract_frame）；
- 写队列 + 写锁，支持并发下发（send_frame / send_hex）。

用法：
    io = SerialIO(port="COM3", baudrate=115200)
    io.open()
    io.send_frame(raw_bytes)
    frame = io.recv_frame(timeout=5.0)   # 阻塞取一帧
    io.close()
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Optional

from sim_concentrator.frame_codec import (
    scan_frame,
    scan_local_frame,
    frame_to_hex,
    hex_to_bytes,
)


def scan_any_frame(buf: bytes):
    """先尝试 CCO 本地协议（单 68）切帧，失败再尝试标准 1376.2（双 68）。"""
    frame, consumed = scan_local_frame(buf)
    if frame is not None:
        return frame, consumed
    return scan_frame(buf)


try:
    import serial
    from serial.tools import list_ports
    _SERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover - 无 pyserial 环境
    serial = None
    list_ports = None
    _SERIAL_AVAILABLE = False


def available() -> bool:
    return _SERIAL_AVAILABLE


def list_serial_ports() -> list:
    """列出可用串口（无 pyserial 时返回空）。"""
    if not _SERIAL_AVAILABLE:
        return []
    return [p.device for p in list_ports.comports()]


class SerialIO:
    """独占串口通道：读线程切帧入队 + 写锁发送。"""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.2,
                 bytesize: int = 8, parity: str = "N", stopbits: int = 1):
        if not _SERIAL_AVAILABLE:
            raise RuntimeError("缺少 pyserial 依赖，请先安装：pip install pyserial")
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self._ser = None
        self._read_stop = threading.Event()
        self._read_thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._rx_queue: "queue.Queue[bytes]" = queue.Queue()
        # 历史帧记录：读线程收到的所有完整帧（供 expect_history / 主动上报验证）
        self._rx_history: list = []
        self._open = False

    # -- 生命周期 -------------------------------------------------------
    # 字符参数 → pyserial 常量映射
    _PARITY_MAP = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN,
                   "O": serial.PARITY_ODD, "M": serial.PARITY_MARK,
                   "S": serial.PARITY_SPACE}
    _BYTESIZE_MAP = {5: serial.FIVEBITS, 6: serial.SIXBITS,
                     7: serial.SEVENBITS, 8: serial.EIGHTBITS}
    _STOPBITS_MAP = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}

    def open(self) -> bool:
        if self._open:
            return True
        self._ser = serial.Serial(
            port=self.port, baudrate=self.baudrate,
            bytesize=self._BYTESIZE_MAP.get(self.bytesize, serial.EIGHTBITS),
            parity=self._PARITY_MAP.get(self.parity.upper(), serial.PARITY_NONE),
            stopbits=self._STOPBITS_MAP.get(self.stopbits, serial.STOPBITS_ONE),
            timeout=self.timeout,
        )
        self._open = True
        self._read_stop.clear()
        self._read_thread = threading.Thread(
            target=self._run, name=f"simcon-rx-{self.port}", daemon=True)
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

    def is_open(self) -> bool:
        return self._open

    # -- 读线程 ----------------------------------------------------------
    def _run(self) -> None:
        """读线程：累积字节，按 1376.2 帧切帧入队。"""
        buf = b""
        while not self._read_stop.is_set():
            try:
                data = self._ser.read(512)
            except Exception:
                break
            if not data:
                continue
            buf += data
            # 连续切出完整帧（跳过前导脏字节）
            while True:
                frame, consumed = scan_any_frame(buf)
                if frame is not None:
                    self._rx_history.append(frame)
                    # 历史只保留最近 1000 帧，防止长时间运行内存增长
                    if len(self._rx_history) > 1000:
                        del self._rx_history[: len(self._rx_history) - 1000]
                if frame is None:
                    break
                self._rx_queue.put(frame)
                buf = buf[consumed:]
        # 退出前排空剩余（可选）
        if buf:
            # 保留以便将来 flush 语义；此处直接丢弃即可
            pass

    # -- 发送 ------------------------------------------------------------
    def send_frame(self, raw: bytes) -> int:
        """发送一帧字节，返回发送字节数。"""
        with self._write_lock:
            if not self._open or self._ser is None:
                raise RuntimeError("串口未打开")
            n = self._ser.write(raw)
        return n

    def send_hex(self, hex_str: str) -> int:
        return self.send_frame(hex_to_bytes(hex_str))

    # -- 接收 ------------------------------------------------------------
    def recv_frame(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """阻塞取一帧；超时返回 None。timeout=None 表示永不超时。"""
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def pending_frames(self) -> int:
        return self._rx_queue.qsize()

    def rx_history(self) -> list:
        """返回收到的所有完整帧（bytes 列表，供主动上报验证/历史查询）。"""
        return list(self._rx_history)
