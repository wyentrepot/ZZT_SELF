"""模块日志串口服务（双通道：cco + sta 同时监控，各自独立烧录）。

设计要点（REQS.md 变更，模块串口双通道模型）：
- 固定两路通道 cco / sta，各自持有独立 COM handle。
- start(channel) 时 open COM 一次、stop(channel) 才 close：常驻独占，绝无重开。
- 每路 RX 线程常驻读原始字节 → ① 追加写 LOG/模块/{name}/*.log（跨天轮转）
  ② 内存增量 buffer（供前端 ?after= 增量轮询）。
- 烧录 = 同一 handle 上由独立线程执行 XMODEM 文件传输 + 动态切波特率，
  RX 线程全程不停（pyserial read/write 线程安全）；TX 行/事件行共用同一份日志。
- 前端固定两个面板各自选串口、各自启动/停止/烧录；底部发送框选择目标通道下发。
"""
from __future__ import annotations

import datetime
import os
import queue
import threading
from pathlib import Path
from typing import List, Optional

from module_log import xmodem_flash

CHANNELS = ("cco", "sta")  # 固定双通道顺序


def format_event_timestamp(ts: float) -> str:
    """毫秒级时间戳，格式 YYYYMMDD-HH:MM:SS:mmm（用户要求）。"""
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%Y%m%d-%H:%M:%S") + f":{int((ts - int(ts)) * 1000):03d}"


class _FlashReader:
    """烧录应答 reader：唯一 reader（RX 线程）把设备回显喂入队列，本代理从此消费。

    供 xmodem_flash.flash() 作为 ser 传入：read/in_waiting 走烧录应答队列
    （由 RX 线程在烧录期间喂入），write/baudrate 委托真实 pyserial 对象。
    这样烧录线程与 RX 线程不并发 read 同一 handle，根除抢读竞争。
    """

    def __init__(self, real_ser, resp_queue: "queue.Queue"):
        self._ser = real_ser
        self._q = resp_queue

    # ---- 读侧：来自烧录应答队列（RX 线程烧录期间喂入）----
    def read(self, n=1):
        if n is None or n <= 0:
            n = 1
        out = bytearray()
        while len(out) < n:
            try:
                b = self._q.get(timeout=0.05)
            except queue.Empty:
                break
            out.append(b)
        return bytes(out)

    @property
    def in_waiting(self) -> int:
        return self._q.qsize()

    # ---- 写侧：委托真实串口 ----
    def write(self, data):
        return self._ser.write(data)

    @property
    def baudrate(self):
        return self._ser.baudrate

    @baudrate.setter
    def baudrate(self, value):
        self._ser.baudrate = value
class _SerialChannel:
    """单路串口通道（cco 或 sta）：常驻独占一个 COM handle。

    每通道独立持有：pyserial handle / RX 线程 / 增量 buffer / 日志文件 /
    烧录状态。所有实例方法线程安全（内部 RLock）。
    """

    def __init__(self, name: str, log_dir: Path):
        self.name = name  # cco | sta
        self._lock = threading.RLock()
        self._ser = None
        self._rx_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._state = "idle"  # idle | running | error
        self._port: str = ""
        self._baudrate: int = 115200
        self._bytesize = 8
        self._parity = "N"
        self._stopbits = 1
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file: Optional[Path] = None
        self._log_handle = None
        self._log_lock = threading.Lock()
        self._lines: List[dict] = []
        self._next_seq = 0
        self._rx_line_buf = bytearray()
        self._tx_line_buf = bytearray()
        self._flash_state = {
            "flashing": False, "packet": 0, "total": 0,
            "phase": "", "message": "", "error": None,
        }
        self._flash_lock = threading.RLock()
        self._flash_resp_q: Optional["queue.Queue"] = None

    # ---------- 生命周期 ----------
    def start(self, port: str, baudrate: int = 115200, bytesize: int = 8,
              parity: str = "N", stopbits: int = 1) -> dict:
        with self._lock:
            if self._state == "running":
                return {"state": self._state, "port": self._port, "channel": self.name}
            try:
                import serial  # noqa: PLC0415
            except ImportError as exc:
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
            self._append_line("EVENT", f"串口 {port} 打开 @{baudrate} 8{parity[0]}1，模块日志口常驻独占（{self.name}）")
            self._rx_thread = threading.Thread(
                target=self._rx_loop, name=f"module-serial-rx-{self.name}", daemon=True
            )
            self._rx_thread.start()
            return {"state": self._state, "port": self._port, "channel": self.name,
                    "log_file": str(self._log_file)}

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
            return {"state": self._state, "port": self._port,
                    "channel": self.name, "was_running": was_running}

    # ---------- 日志落盘 ----------
    def _open_log_file(self) -> None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        sub = self.name if self.name in ("cco", "sta") else "cco"
        dirpath = self._log_dir / sub
        dirpath.mkdir(parents=True, exist_ok=True)
        path = dirpath / f"{stamp}_[{sub}].log"
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
        now = datetime.date.today()
        if self._log_file is not None:
            mtime = datetime.date.fromtimestamp(self._log_file.stat().st_mtime)
            if mtime == now:
                return
        self._close_log_file()
        self._open_log_file()

    def _append_line(self, direction: str, text: str) -> None:
        ts = format_event_timestamp(datetime.datetime.now().timestamp())
        entry = {"seq": self._next_seq, "ts": ts, "dir": direction, "text": text}
        self._next_seq += 1
        with self._lock:
            self._lines.append(entry)
        with self._log_lock:
            if self._log_handle is not None:
                self._log_handle.write(f"[{ts}] [{direction}] {text}\n")
        # loghooks 运行时接入点（可选，异步 + 失败静默降级，不拖慢主链路）
        self._run_loghooks_hook(direction, text)

    def _run_loghooks_hook(self, direction: str, text: str) -> None:
        """可选调用 run_loghooks；异常一律吞掉，绝不影响日志主链路。"""
        try:
            from loghooks.runtime import run_loghooks
            run_loghooks(self.name, direction, text)
        except Exception:
            pass

    def _ingest_char_stream(self, direction: str, data: bytes) -> None:
        buf = self._rx_line_buf if direction == "RX" else self._tx_line_buf
        buf.extend(data)
        while True:
            idx = buf.find(b"\n")
            if idx == -1:
                break
            line = bytes(buf[: idx + 1])
            del buf[: idx + 1]
            self._append_line(direction, line.decode("utf-8", errors="replace"))

    def _flush_char_buf(self, direction: str) -> None:
        buf = self._rx_line_buf if direction == "RX" else self._tx_line_buf
        if buf:
            line = bytes(buf)
            buf.clear()
            self._append_line(direction, line.decode("utf-8", errors="replace"))

    def _rx_loop(self) -> None:
        while not self._stop_event.is_set():
            ser = self._ser
            if ser is None:
                break
            try:
                if ser.in_waiting > 0:
                    chunk = ser.read(ser.in_waiting)
                    if not chunk:
                        continue
                    q = self._flash_resp_q
                    if q is not None:
                        for b in chunk:
                            q.put(b)
                        continue
                    self._ingest_char_stream("RX", chunk)
                else:
                    self._stop_event.wait(0.05)
            except Exception as exc:
                with self._lock:
                    self._state = "error"
                self._append_line("EVENT", f"RX 读取异常：{exc}")
                break
        try:
            self._flush_char_buf("RX")
        except Exception:
            pass
    # ---------- 下发（写字节） ----------
    def write(self, data_hex: str) -> dict:
        with self._lock:
            if self._state != "running":
                raise RuntimeError(f"[{self.name}] 串口未运行")
            ser = self._ser
        with self._flash_lock:
            if self._flash_state["flashing"]:
                raise RuntimeError(f"[{self.name}] 烧录进行中，禁止手动写串口")
            data = bytes.fromhex(data_hex.replace(" ", "").replace(",", " "))
            ser.write(data)
            self._ingest_char_stream("TX", data)
            return {"sent": len(data), "channel": self.name}

    def write_text(self, text: str, append_newline: bool = True) -> dict:
        """发送文本（UTF-8，默认末尾补 CRLF \\r\\n）。

        append_newline=True（默认）：任何发送数据末尾自动携带回车换行。
        与 CRT / 烧录 _send_line 行为一致：模块命令行以 \\r 作为回车命令结束符，
        \\n 单独可能不被识别。故补 \\r\\n 而非仅 \\n。
        append_newline=False：原样发送，不补。
        """
        with self._lock:
            if self._state != "running":
                raise RuntimeError(f"[{self.name}] 串口未运行")
            ser = self._ser
        with self._flash_lock:
            if self._flash_state["flashing"]:
                raise RuntimeError(f"[{self.name}] 烧录进行中，禁止手动写串口")
            data = text.encode("utf-8")
            if append_newline:
                if not (data.endswith(b"\r\n") or data.endswith(b"\r") or data.endswith(b"\n")):
                    data += b"\r\n"
            ser.write(data)
            self._ingest_char_stream("TX", data)
            return {"sent": len(data), "channel": self.name, "append_newline": append_newline}

    def set_baudrate(self, baudrate: int) -> dict:
        with self._lock:
            if self._state != "running":
                raise RuntimeError(f"[{self.name}] 串口未运行")
            ser = self._ser
        with self._flash_lock:
            if self._flash_state["flashing"]:
                raise RuntimeError(f"[{self.name}] 烧录进行中，禁止修改波特率")
            ser.baudrate = baudrate
            self._baudrate = baudrate
            self._append_line("EVENT", f"波特率变更 → {baudrate}")
            return {"baudrate": baudrate, "channel": self.name}

    # ---------- 烧录 ----------
    def flash(self, bin_path: str, slot: int = 0,
              baud_plan: Optional[List[int]] = None,
              no_reboot_after: bool = False) -> dict:
        with self._lock:
            if self._state != "running":
                raise RuntimeError(f"[{self.name}] 串口未运行")
            ser = self._ser
        with self._flash_lock:
            if self._flash_state["flashing"]:
                raise RuntimeError(f"[{self.name}] 烧录已在执行中")

            self._flash_state.update(
                flashing=True, packet=0, total=0, phase="starting",
                message="", error=None,
            )
            self._append_line("EVENT", f"开始烧录：{bin_path} slot={slot}")

            resp_q: "queue.Queue" = queue.Queue()
            self._flash_resp_q = resp_q
            reader = _FlashReader(ser, resp_q)

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
                    reader, bin_path, slot=slot, baud_plan=baud_plan,
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
                return {"status": "success", "channel": self.name}
            except Exception as exc:
                with self._lock:
                    self._flash_state.update(
                        flashing=False, phase="error",
                        message=str(exc), error=str(exc),
                    )
                self._append_line("EVENT", f"烧录失败：{exc}")
                raise
            finally:
                self._flash_resp_q = None

    # ---------- 增量读取 ----------
    def logs(self, after: int = 0) -> dict:
        with self._lock:
            if after < 0:
                lines = list(self._lines)
            else:
                lines = [e for e in self._lines if e["seq"] > after]
            last_seq = self._next_seq - 1 if self._lines else after
            return {"lines": lines, "last_seq": last_seq, "channel": self.name}

    def status(self) -> dict:
        with self._lock:
            return {
                "channel": self.name,
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

class ModuleSerialService:
    """双通道模块串口服务：cco 与 sta 两路可同时监控、独立烧录。

    向后兼容：start/stop/write/logs/flash 等默认作用于 cco 通道（channel="cco"），
    保持旧测试与旧调用可用；新增 channel 参数可指定目标通道。
    """

    def __init__(self, log_dir: Optional[Path | str] = None):
        base = Path(log_dir) if log_dir else (Path("data") / "logs" / "模块")
        base.mkdir(parents=True, exist_ok=True)
        self._channels: dict = {
            name: _SerialChannel(name=name, log_dir=base) for name in CHANNELS
        }

    def channel(self, name: str) -> _SerialChannel:
        if name not in self._channels:
            raise RuntimeError(f"未知通道：{name}（可选 {CHANNELS}）")
        return self._channels[name]

    @property
    def channels(self) -> dict:
        return dict(self._channels)

    @staticmethod
    def list_available_ports() -> List[str]:
        """列出可用 COM 口；无 pyserial 时返回空列表。"""
        try:
            import serial.tools.list_ports  # noqa: PLC0415

            return [p.device for p in serial.tools.list_ports.comports()]
        except Exception:
            return []

    # ---- 兼容旧调用：默认 cco 通道 ----
    def start(self, port: str, baudrate: int = 115200, bytesize: int = 8,
              parity: str = "N", stopbits: int = 1,
              log_type: str = "cco", channel: Optional[str] = None) -> dict:
        ch = channel or log_type or "cco"
        return self.channel(ch).start(port, baudrate, bytesize, parity, stopbits)

    def stop(self, channel: Optional[str] = None) -> dict:
        ch = channel or "cco"
        return self.channel(ch).stop()

    def write(self, data_hex: str, channel: Optional[str] = None) -> dict:
        ch = channel or "cco"
        return self.channel(ch).write(data_hex)

    def write_text(self, text: str, channel: str = "cco", append_newline: bool = True) -> dict:
        return self.channel(channel).write_text(text, append_newline=append_newline)

    def set_baudrate(self, baudrate: int, channel: Optional[str] = None) -> dict:
        ch = channel or "cco"
        return self.channel(ch).set_baudrate(baudrate)

    def flash(self, bin_path: str, slot: int = 0,
              baud_plan: Optional[List[int]] = None,
              no_reboot_after: bool = False,
              channel: Optional[str] = None) -> dict:
        ch = channel or "cco"
        return self.channel(ch).flash(bin_path, slot, baud_plan, no_reboot_after)

    def logs(self, after: int = -1, channel: Optional[str] = None) -> dict:
        ch = channel or "cco"
        return self.channel(ch).logs(after=after)

    def status(self) -> dict:
        """聚合两路状态。channels 含每路详情；顶层保留 cco 字段以兼容旧前端。"""
        chs = {name: self._channels[name].status() for name in CHANNELS}
        cco = chs["cco"]
        return {
            "state": cco["state"],
            "port": cco["port"],
            "baudrate": cco["baudrate"],
            "bytesize": cco["bytesize"],
            "parity": cco["parity"],
            "stopbits": cco["stopbits"],
            "log_dir": cco["log_dir"],
            "log_file": cco["log_file"],
            "lines": cco["lines"],
            "flash": cco["flash"],
            "channels": chs,
        }
    # ---- 便捷方法（兼容旧测试/旧调用：默认 cco 通道）----
    def _append_line(self, direction: str, text: str, channel: str = "cco") -> None:
        """向指定通道追加一行（默认 cco）。等价于 channel(channel)._append_line。"""
        self.channel(channel)._append_line(direction, text)