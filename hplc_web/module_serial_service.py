"""模块日志串口服务（需求 0001 serial-flash-session）。

设计要点（REQS.md 变更 2，串口单一模块模型）：
- 本模块是唯一持有「模块日志/烧录串口」handle 的地方。
- start 时 open COM 一次、stop 才 close：常驻独占，绝无重开。
- RX 线程常驻读原始字节 → ① 追加写 LOG/MODCOM{port}_{ts}_模块日志.txt
  （跨天轮转）② 内存增量 buffer（供前端 ?after= 增量轮询）。
- 烧录 = 同一 handle 上由独立线程执行 XMODEM 文件传输 + 动态切波特率，
  RX 线程全程不停（pyserial read/write 线程安全）；TX 行/事件行共用同一份日志。
- 与 SerialCaptureService（侦听台）完全独立：两套串口、两套处理，互不干扰。
"""
from __future__ import annotations

import datetime
import os
import threading
from pathlib import Path
from typing import Callable, List, Optional

from hplc_web import xmodem_flash


def format_event_timestamp(ts: float) -> str:
    """毫秒级时间戳，格式 YYYYMMDD-HH:MM:SS:mmm（用户要求）。"""
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%Y%m%d-%H:%M:%S") + f":{int((ts - int(ts)) * 1000):03d}"


class ModuleSerialService:
    def __init__(self, log_dir: Optional[Path | str] = None):
        self._lock = threading.RLock()
        self._ser = None  # 常驻独占的 pyserial 对象
        self._rx_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state = "idle"  # idle | running | error
        self._port: str = ""
        self._baudrate: int = 115200
        self._bytesize = 8
        self._parity = "N"
        self._stopbits = 1
        self._log_dir = Path(log_dir) if log_dir else Path("LOG")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file: Optional[Path] = None
        self._log_handle = None  # 文本文件句柄（追加写）
        self._log_lock = threading.Lock()
        self._lines: List[dict] = []  # 增量 buffer：[{seq, ts, dir, text}]
        self._next_seq = 0
        # 按换行切行的缓冲（跨 chunk 累积，遇 \n/\r\n 切出完整行）
        self._rx_line_buf = bytearray()  # RX 未闭合行
        self._tx_line_buf = bytearray()  # TX 未闭合行
        self._flash_state = {  # 烧录进行时状态
            "flashing": False,
            "packet": 0,
            "total": 0,
            "phase": "",
            "message": "",
            "error": None,
        }
        self._flash_lock = threading.RLock()  # 烧录线程串行化（含 write 互斥）

    # ---------- 基础查询 ----------
    @staticmethod
    def list_available_ports() -> List[str]:
        """列出可用 COM 口；无 pyserial 时返回空列表。"""
        try:
            import serial.tools.list_ports  # noqa: PLC0415

            return [p.device for p in serial.tools.list_ports.comports()]
        except Exception:
            return []

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "port": self._port,
                "baudrate": self._baudrate,
                "bytesize": self._bytesize,
                "parity": self._parity,
                "stopbits": self._stopbits,
                "log_dir": str(self._log_dir),
                "log_file": str(self._log_file) if self._log_file else None,
                "lines": len(self._lines),
                "flash": dict(self._flash_state),
            }

    # ---------- 生命周期 ----------
    def start(self, port: str, baudrate: int = 115200, bytesize: int = 8,
              parity: str = "N", stopbits: int = 1) -> dict:
        with self._lock:
            if self._state == "running":
                return {"state": self._state, "port": self._port}
            try:
                import serial  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - 与 serial_service 一致
                raise RuntimeError("缺少 pyserial 依赖，请先安装：pip install pyserial") from exc

            ser = serial.Serial(
                port=port, baudrate=baudrate, bytesize=bytesize,
                parity=parity, stopbits=stopbits, timeout=0.1,
            )
            self._ser = ser
            self._port = port
            self._baudrate = baudrate
            self._bytesize = bytesize
            self._parity = parity
            self._stopbits = stopbits
            self._state = "running"
            self._stop_event.clear()
            self._open_log_file()
            self._append_line("EVENT", f"串口 {port} 打开 @{baudrate} 8{parity[0]}1，模块日志口常驻独占")
            self._rx_thread = threading.Thread(
                target=self._rx_loop, name="module-serial-rx", daemon=True
            )
            self._rx_thread.start()
            return {"state": self._state, "port": self._port, "log_file": str(self._log_file)}

    def stop(self) -> dict:
        with self._lock:
            was_running = self._state == "running"
            self._state = "idle"
            self._stop_event.set()
            thread = self._rx_thread
            self._rx_thread = None
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
            try:
                self._flush_char_buf("TX")
            except Exception:
                pass
            if self._ser is not None:
                try:
                    if self._ser.is_open:
                        self._append_line("EVENT", "串口关闭")
                        self._ser.close()
                except Exception:
                    pass
                self._ser = None
            self._close_log_file()
            return {"state": self._state, "port": self._port, "was_running": was_running}

    # ---------- 日志落盘 ----------
    def _open_log_file(self) -> None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_port = "".join(c for c in self._port if c.isalnum())
        path = self._log_dir / f"MODCOM{safe_port}_{stamp}_模块日志.txt"
        self._log_file = path
        self._log_handle = open(path, "a", encoding="utf-8", buffering=1)

    def _close_log_file(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.flush()
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
            self._log_file = None

    def _rotate_if_needed(self) -> None:
        """跨天轮转：日期变化时开新文件。"""
        now = datetime.date.today()
        if self._log_file is not None:
            mtime = datetime.date.fromtimestamp(self._log_file.stat().st_mtime)
            if mtime == now:
                return
        self._close_log_file()
        self._open_log_file()

    def _append_line(self, direction: str, text: str) -> None:
        """追加一行到内存增量 buffer + 日志文件（RX/TX/EVENT 共用，时间线连续）。"""
        ts = format_event_timestamp(datetime.datetime.now().timestamp())
        entry = {"seq": self._next_seq, "ts": ts, "dir": direction, "text": text}
        self._next_seq += 1
        with self._lock:
            self._lines.append(entry)
        with self._log_lock:
            if self._log_handle is not None:
                self._log_handle.write(f"[{ts}] [{direction}] {text}\n")

    def _ingest_char_stream(self, direction: str, data: bytes) -> None:
        """按换行切字符行：跨 chunk 累积到行缓冲，遇 \\n/\\r\\n 切出完整行。

        每个完整行带时间标签落盘/入前端；未闭合的行保留缓冲等待下一块。
        （用户要求：全部按字符显示，有换行符时每行前加时间标签）
        """
        buf = self._rx_line_buf if direction == "RX" else self._tx_line_buf
        buf.extend(data)
        # 按 \n 切行（兼容 \r\n：切行时保留 \r 于行内容，前端可清洗）
        while True:
            idx = buf.find(b"\n")
            if idx == -1:
                break
            line = bytes(buf[: idx + 1])  # 含换行符
            del buf[: idx + 1]
            self._append_line(direction, line.decode("utf-8", errors="replace"))

    def _flush_char_buf(self, direction: str) -> None:
        """停止时把未闭合行缓冲刷出（若有内容）。"""
        buf = self._rx_line_buf if direction == "RX" else self._tx_line_buf
        if buf:
            line = bytes(buf)
            buf.clear()
            self._append_line(direction, line.decode("utf-8", errors="replace"))


    def _rx_loop(self) -> None:
        """常驻 RX 线程：原始字节实时按换行切字符行落盘，绝不停。"""
        while not self._stop_event.is_set():
            ser = self._ser
            if ser is None:
                break
            try:
                if ser.in_waiting > 0:
                    chunk = ser.read(ser.in_waiting)
                    if chunk:
                        self._ingest_char_stream("RX", chunk)
                else:
                    self._stop_event.wait(0.05)
            except Exception as exc:
                with self._lock:
                    self._state = "error"
                self._append_line("EVENT", f"RX 读取异常：{exc}")
                break
        # 停止时把未闭合的 RX 行刷出
        try:
            self._flush_char_buf("RX")
        except Exception:
            pass

    # ---------- 下发（写字节） ----------
    def write(self, data_hex: str) -> dict:
        """向已持有的 handle 发送原始字节（十六进制字符串）。烧录期间拒绝。"""
        with self._lock:
            if self._state != "running":
                raise RuntimeError("串口未运行")
            ser = self._ser
        with self._flash_lock:
            if self._flash_state["flashing"]:
                raise RuntimeError("烧录进行中，禁止手动写串口")
            data = bytes.fromhex(data_hex.replace(" ", "").replace(",", " "))
            ser.write(data)
            self._ingest_char_stream("TX", data)
            return {"sent": len(data)}

    def set_baudrate(self, baudrate: int) -> dict:
        """动态修改波特率（SetCommState，不关句柄不清缓冲），RX 线程不停。"""
        with self._lock:
            if self._state != "running":
                raise RuntimeError("串口未运行")
            ser = self._ser
        with self._flash_lock:
            if self._flash_state["flashing"]:
                raise RuntimeError("烧录进行中，禁止修改波特率")
            ser.baudrate = baudrate
            self._baudrate = baudrate
            self._append_line("EVENT", f"波特率变更 → {baudrate}")
            return {"baudrate": baudrate}

    # ---------- 烧录 ----------
    def flash(self, bin_path: str, slot: int = 0,
              baud_plan: Optional[List[int]] = None,
              no_reboot_after: bool = False) -> dict:
        """在同一 handle 上执行 XMODEM 烧录；RX 线程全程不停。

        通过 _flash_lock 串行化烧录与手动 write/set_baudrate；完成后状态复位。
        烧录在独立线程执行，不阻塞请求。
        """
        with self._lock:
            if self._state != "running":
                raise RuntimeError("串口未运行")
            ser = self._ser
        with self._flash_lock:
            if self._flash_state["flashing"]:
                raise RuntimeError("烧录已在执行中")

            self._flash_state.update(
                flashing=True, packet=0, total=0, phase="starting",
                message="", error=None,
            )
            self._append_line("EVENT", f"开始烧录：{bin_path} slot={slot}")

            def _log(msg: str) -> None:
                self._append_line("EVENT", msg)

            def _progress(packet: int, total: int) -> None:
                with self._lock:
                    self._flash_state["packet"] = packet
                    self._flash_state["total"] = total
                    self._flash_state["phase"] = "transfer"
                    self._flash_state["message"] = f"{packet}/{total}"

            try:
                result = xmodem_flash.flash(
                    ser, bin_path, slot=slot, baud_plan=baud_plan,
                    no_reboot_after=no_reboot_after,
                    log=_log, progress=_progress,
                    on_baud_change=lambda r: self._append_line("EVENT", f"波特率 → {r}"),
                )
                with self._lock:
                    self._flash_state.update(
                        flashing=False, phase="done",
                        message=result.get("status", "success"),
                    )
                self._append_line("EVENT", "烧录完成：BURN SUCCESS")
                return {"status": "success"}
            except Exception as exc:
                with self._lock:
                    self._flash_state.update(
                        flashing=False, phase="error",
                        message=str(exc), error=str(exc),
                    )
                self._append_line("EVENT", f"烧录失败：{exc}")
                raise

    # ---------- 增量读取 ----------
    def logs(self, after: int = 0) -> dict:
        """返回 seq > after 的新增日志行；after=-1 返回全部。"""
        with self._lock:
            if after < 0:
                lines = list(self._lines)
            else:
                lines = [e for e in self._lines if e["seq"] > after]
            last_seq = self._next_seq - 1 if self._lines else after
            return {"lines": lines, "last_seq": last_seq}
