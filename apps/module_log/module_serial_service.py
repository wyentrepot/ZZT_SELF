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
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from shared.serial_mapping import SerialPortCatalog
from shared.serial_resources import SerialResourceConflict, SerialResourceRegistry

from module_log import xmodem_flash

CHANNELS = ("cco", "sta")  # 固定双通道顺序

# 每路内存增量日志缓冲上限（环形裁剪）：超过后丢弃最旧行。
# 前端按 seq 增量轮询 + 日志框上限裁剪，历史日志始终落盘在 LOG/模块/<ch>/*.log，
# 内存只保留最近窗口，避免整夜监听后后端内存与每轮 O(n) 扫描无限增长。
MAX_MEMORY_LINES = 5000


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

    def __init__(self, name: str, log_dir: Path, session_id: str = "",
                 port_identity: Optional[dict] = None,
                 include_identity_in_log_name: bool = False):
        self.name = name  # cco | sta
        self._session_id = session_id
        self._port_identity = dict(port_identity or {})
        self._include_identity_in_log_name = include_identity_in_log_name
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
            self._append_line("EVENT", self._open_event_text(port, baudrate, bytesize, parity, stopbits))
            self._rx_thread = threading.Thread(
                target=self._rx_loop, name=f"module-serial-rx-{self.name}", daemon=True
            )
            self._rx_thread.start()
            return {"state": self._state, "port": self._port, "channel": self.name,
                    "log_file": str(self._log_file)}

    def stop(self) -> dict:
        """停止一个会话的串口；不在状态锁内等待 RX 线程退出。"""
        with self._lock:
            if self._flash_state["flashing"]:
                raise RuntimeError(f"[{self.name}] 烧录进行中，不能停止串口")
            was_running = self._state == "running"
            self._state = "idle"
            self._stop_event.set()
            thread = self._rx_thread
            self._rx_thread = None
            ser = self._ser
            self._ser = None

        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        try:
            self._flush_char_buf("TX")
        except Exception:
            pass
        if ser is not None:
            try:
                if ser.is_open:
                    self._append_line("EVENT", "串口关闭")
                    ser.close()
            except Exception:
                pass
        self._close_log_file()
        return {
            "state": "idle", "port": self._port, "channel": self.name,
            "session_id": self._session_id, "was_running": was_running,
        }

    # ---------- 日志落盘 ----------
    def _set_session_context(self, session_id: str, module: str,
                             port_identity: Optional[dict] = None,
                             include_identity_in_log_name: bool = True) -> None:
        """在空闲态更新所属会话与身份；运行中不得切换模块类型。"""
        with self._lock:
            if self._state == "running" and module != self.name:
                raise RuntimeError(f"[{self.name}] 串口运行中，不能修改模块类型")
            self._session_id = session_id
            self.name = module
            self._port_identity = dict(port_identity or {})
            self._include_identity_in_log_name = include_identity_in_log_name

    def _identity_file_tag(self) -> str:
        parts = [
            str(self._port_identity.get("mapping_id") or self._session_id or "unmapped"),
            str(self._port_identity.get("windows_com") or ""),
            str(self._port_identity.get("linux_device") or self._port or ""),
        ]
        cleaned = [
            re.sub(r"[^A-Za-z0-9._-]+", "", part.replace("/", "-").replace("\\", "-"))
            for part in parts if part
        ]
        return "-".join(cleaned) or "unmapped"

    def _open_event_text(self, port: str, baudrate: int, bytesize: int,
                         parity: str, stopbits: int) -> str:
        if not self._include_identity_in_log_name:
            return (
                f"串口 {port} 打开 @{baudrate} {bytesize}{parity[0]}{stopbits}，"
                f"模块日志口常驻独占（{self.name}）"
            )
        identity = self._port_identity
        return (
            "串口会话打开："
            f"session_id={self._session_id}；"
            f"mapping_id={identity.get('mapping_id') or 'unmapped'}；"
            f"label={identity.get('label') or ''}；"
            f"module={self.name}；"
            f"device={port}；"
            f"windows_com={identity.get('windows_com') or ''}；"
            f"linux_device={identity.get('linux_device') or ''}；"
            f"serial={baudrate}/{bytesize}{parity[0]}{stopbits}"
        )

    def _open_log_file(self) -> None:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        sub = self.name if self.name in ("cco", "sta") else "cco"
        dirpath = self._log_dir / sub
        dirpath.mkdir(parents=True, exist_ok=True)
        if self._include_identity_in_log_name:
            filename = f"{stamp}_[{sub}]_[{self._identity_file_tag()}].log"
        else:
            filename = f"{stamp}_[{sub}].log"
        path = dirpath / filename
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
        with self._lock:
            entry = {"seq": self._next_seq, "ts": ts, "dir": direction, "text": text}
            self._next_seq += 1
            self._lines.append(entry)
            if len(self._lines) > MAX_MEMORY_LINES:
                del self._lines[: len(self._lines) - MAX_MEMORY_LINES]
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
                # 全部行存在时（after<0）返回最近的窗口，避免整夜后拖全量
                if len(lines) > MAX_MEMORY_LINES:
                    lines = lines[-MAX_MEMORY_LINES:]
            else:
                # 环形裁剪后 seq 不再连续：只取 seq > after 的新增行
                lines = [e for e in self._lines if e["seq"] > after]
            last_seq = self._next_seq - 1 if self._lines else after
            return {"lines": lines, "last_seq": last_seq, "channel": self.name}

    def status(self) -> dict:
        with self._lock:
            return {
                "channel": self.name,
                "session_id": self._session_id,
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

@dataclass
class _ModuleSession:
    """动态模块日志会话的服务端登记项。"""

    session_id: str
    title: str
    module: str
    channel: _SerialChannel
    created_at: str
    legacy_channel: Optional[str] = None
    port_identity: dict = field(default_factory=dict)
    port_key: str = ""


class ModuleSerialService:
    """动态模块串口会话服务。

    物理串口仅由本服务中的单会话对象打开一次。UI、AI 和旧接口都通过同一个
    会话登记表访问状态、日志与动作；只有后端句柄独占。
    """

    def __init__(self, log_dir: Optional[Path | str] = None, port_catalog=None, resource_registry=None):
        self._log_dir = Path(log_dir) if log_dir else (Path("data") / "logs" / "模块")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._port_catalog = port_catalog or SerialPortCatalog.load()
        self._resource_registry = resource_registry or SerialResourceRegistry()
        self._lock = threading.RLock()
        self._sessions: dict[str, _ModuleSession] = {}
        self._legacy_session_ids: dict[str, str] = {}
        self._port_owners: dict[str, str] = {}
        self._next_default_title = 1

    def set_resource_registry(self, resource_registry: SerialResourceRegistry) -> None:
        """Attach the process-level registry before any session is started."""
        if resource_registry is None:
            raise ValueError("resource_registry 不能为空")
        with self._lock:
            if any(item.channel.status()["state"] == "running" for item in self._sessions.values()):
                raise RuntimeError("存在运行中的模块会话，不能切换串口资源注册表")
            self._resource_registry = resource_registry

    @staticmethod
    def _resource_owner_id(session_id: str) -> str:
        return "module:" + str(session_id)

    @staticmethod
    def _resource_aliases(open_port: str, identity: dict) -> tuple[str, ...]:
        return tuple(
            value for value in (
                open_port,
                identity.get("device"),
                identity.get("windows_com"),
                identity.get("linux_device"),
            ) if value
        )

    def _reserve_serial_resource(self, session: _ModuleSession, open_port: str,
                                 identity: dict) -> None:
        try:
            self._resource_registry.reserve(
                self._resource_owner_id(session.session_id),
                label="模块日志会话“" + session.title + "”",
                resource_id=str(identity.get("mapping_id") or ""),
                aliases=self._resource_aliases(open_port, identity),
            )
        except SerialResourceConflict as exc:
            raise RuntimeError(
                f"串口 {open_port} 已被后端会话“{exc.owner_label}”占用（{exc.owner_id}）"
            ) from exc

    def _release_serial_resource(self, session_id: str) -> None:
        self._resource_registry.release(self._resource_owner_id(session_id))

    # ---------- 端口目录 ----------
    @staticmethod
    def _system_ports() -> list:
        try:
            import serial.tools.list_ports  # noqa: PLC0415

            return [
                {"device": p.device, "description": p.description}
                for p in serial.tools.list_ports.comports()
            ]
        except Exception:
            return []

    def list_available_port_details(self) -> list:
        """返回真实设备与统一映射合并后的端口详情。"""
        return self._port_catalog.merge_system_ports(self._system_ports())

    def list_available_ports(self) -> List[str]:
        """兼容旧 API：仅返回当前在线的实际设备名。"""
        return [item["device"] for item in self.list_available_port_details() if item.get("online")]

    def mapping_error(self) -> str:
        return self._port_catalog.mapping_error

    def _mapping_is_online(self, mapping_id: str) -> bool:
        return any(
            item.get("mapping_id") == mapping_id and item.get("online")
            for item in self.list_available_port_details()
        )

    def _identity_for_port(self, port: str) -> tuple[str, dict]:
        mapping = self._port_catalog.find(port)
        if mapping is None:
            return port, {
                "mapping_id": "",
                "label": "",
                "device": port,
                "linux_device": "",
                "windows_com": "",
                "usage": "",
                "module": "",
            }
        if not mapping.enabled:
            raise RuntimeError(f"串口映射 {mapping.id} 已禁用")
        if not self._mapping_is_online(mapping.id):
            raise RuntimeError(f"映射串口 {mapping.label or mapping.id} 当前离线，请刷新端口列表")
        open_port = mapping.device_for()
        return open_port, {
            "mapping_id": mapping.id,
            "label": mapping.label,
            "device": open_port,
            "linux_device": mapping.linux_device,
            "windows_com": mapping.windows_com,
            "usage": mapping.usage,
            "module": mapping.module,
        }

    # ---------- 动态会话登记 ----------
    @staticmethod
    def _validate_module(module: str) -> str:
        normalized = str(module or "").strip().lower()
        if normalized not in CHANNELS:
            raise RuntimeError("模块类型必须为 cco 或 sta")
        return normalized

    def _new_session_id(self) -> str:
        return "ms-" + uuid.uuid4().hex[:12]

    def _default_title(self) -> str:
        title = f"实时日志 {self._next_default_title}"
        self._next_default_title += 1
        return title

    def _require_session(self, session_id: str) -> _ModuleSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _session_payload(self, session: _ModuleSession) -> dict:
        channel_state = session.channel.status()
        serial_config = {
            "baudrate": channel_state["baudrate"],
            "bytesize": channel_state["bytesize"],
            "parity": channel_state["parity"],
            "stopbits": channel_state["stopbits"],
        }
        return {
            "session_id": session.session_id,
            "title": session.title,
            "module": session.module,
            "legacy_channel": session.legacy_channel,
            "created_at": session.created_at,
            "state": channel_state["state"],
            "port": channel_state["port"],
            "port_identity": dict(session.port_identity),
            "serial_config": serial_config,
            "baudrate": serial_config["baudrate"],
            "bytesize": serial_config["bytesize"],
            "parity": serial_config["parity"],
            "stopbits": serial_config["stopbits"],
            "log_dir": channel_state["log_dir"],
            "log_file": channel_state["log_file"],
            "lines": channel_state["lines"],
            "flash": channel_state["flash"],
        }

    def create_session(self, title: str = "", module: str = "cco",
                       legacy_channel: Optional[str] = None) -> dict:
        module = self._validate_module(module)
        with self._lock:
            session_id = self._new_session_id()
            session = _ModuleSession(
                session_id=session_id,
                title=str(title or "").strip() or self._default_title(),
                module=module,
                channel=_SerialChannel(
                    name=module,
                    log_dir=self._log_dir,
                    session_id=session_id,
                    include_identity_in_log_name=legacy_channel is None,
                ),
                created_at=datetime.datetime.now().isoformat(timespec="seconds"),
                legacy_channel=legacy_channel,
            )
            self._sessions[session_id] = session
            if legacy_channel:
                self._legacy_session_ids[legacy_channel] = session_id
            return self._session_payload(session)

    def list_sessions(self) -> list[dict]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [self._session_payload(session) for session in sessions]

    def get_session(self, session_id: str) -> dict:
        return self._session_payload(self._require_session(session_id))

    def update_session(self, session_id: str, title: Optional[str] = None,
                       module: Optional[str] = None) -> dict:
        session = self._require_session(session_id)
        with self._lock:
            if title is not None:
                normalized_title = str(title).strip()
                if not normalized_title:
                    raise RuntimeError("会话标题不能为空")
                session.title = normalized_title
            if module is not None:
                normalized_module = self._validate_module(module)
                if normalized_module != session.module:
                    if session.channel.status()["state"] == "running":
                        raise RuntimeError("会话运行中，不能修改模块类型")
                    session.module = normalized_module
                    session.channel._set_session_context(
                        session_id=session.session_id,
                        module=normalized_module,
                        port_identity=session.port_identity,
                        include_identity_in_log_name=session.legacy_channel is None,
                    )
        return self._session_payload(session)

    def delete_session(self, session_id: str) -> dict:
        session = self._require_session(session_id)
        state = session.channel.status()
        if state["state"] == "running" or state["flash"]["flashing"]:
            raise RuntimeError("会话运行中，必须先停止后删除")
        with self._lock:
            self._sessions.pop(session_id, None)
            if session.legacy_channel:
                self._legacy_session_ids.pop(session.legacy_channel, None)
            if session.port_key and self._port_owners.get(session.port_key) == session_id:
                self._port_owners.pop(session.port_key, None)
        self._release_serial_resource(session_id)
        return {"session_id": session_id, "deleted": True}

    # ---------- 动态会话动作 ----------
    def start_session(self, session_id: str, port: str, baudrate: int = 115200,
                      bytesize: int = 8, parity: str = "N", stopbits: int = 1) -> dict:
        session = self._require_session(session_id)
        open_port, identity = self._identity_for_port(port)
        parity = str(parity or "N").upper()
        requested_config = (int(baudrate), int(bytesize), parity, int(stopbits))
        current = session.channel.status()

        if current["state"] == "running":
            current_key = self._port_catalog.identity_key(current["port"])
            requested_key = self._port_catalog.identity_key(open_port)
            current_config = (
                current["baudrate"], current["bytesize"],
                current["parity"], current["stopbits"],
            )
            if current_key == requested_key and current_config == requested_config:
                return self._session_payload(session)
            raise RuntimeError("会话正在运行，不能更换串口或串口参数")

        port_key = self._port_catalog.identity_key(open_port)
        with self._lock:
            owner_id = self._port_owners.get(port_key)
            if owner_id and owner_id != session_id:
                owner = self._sessions.get(owner_id)
                owner_title = owner.title if owner is not None else owner_id
                raise RuntimeError(
                    f"串口 {open_port} 已被会话“{owner_title}”占用（{owner_id}）"
                )
            self._port_owners[port_key] = session_id

        try:
            self._reserve_serial_resource(session, open_port, identity)
            session.port_identity = identity
            session.port_key = port_key
            session.channel._set_session_context(
                session_id=session.session_id,
                module=session.module,
                port_identity=identity,
                include_identity_in_log_name=session.legacy_channel is None,
            )
            session.channel.start(
                open_port, baudrate=requested_config[0], bytesize=requested_config[1],
                parity=requested_config[2], stopbits=requested_config[3],
            )
            return self._session_payload(session)
        except Exception:
            with self._lock:
                if self._port_owners.get(port_key) == session_id:
                    self._port_owners.pop(port_key, None)
            self._release_serial_resource(session_id)
            session.port_key = ""
            raise

    def stop_session(self, session_id: str) -> dict:
        session = self._require_session(session_id)
        result = session.channel.stop()
        with self._lock:
            if session.port_key and self._port_owners.get(session.port_key) == session_id:
                self._port_owners.pop(session.port_key, None)
            session.port_key = ""
        self._release_serial_resource(session_id)
        payload = self._session_payload(session)
        payload["was_running"] = result.get("was_running", False)
        return payload

    def write_session(self, session_id: str, data_hex: str) -> dict:
        result = self._require_session(session_id).channel.write(data_hex)
        result["session_id"] = session_id
        return result

    def write_text_session(self, session_id: str, text: str,
                           append_newline: bool = True) -> dict:
        result = self._require_session(session_id).channel.write_text(
            text, append_newline=append_newline,
        )
        result["session_id"] = session_id
        return result

    def set_session_baudrate(self, session_id: str, baudrate: int) -> dict:
        result = self._require_session(session_id).channel.set_baudrate(baudrate)
        result["session_id"] = session_id
        return result

    def flash_session(self, session_id: str, bin_path: str, slot: int = 0,
                      baud_plan: Optional[List[int]] = None,
                      no_reboot_after: bool = False) -> dict:
        result = self._require_session(session_id).channel.flash(
            bin_path, slot, baud_plan, no_reboot_after,
        )
        result["session_id"] = session_id
        return result

    def logs_session(self, session_id: str, after: int = -1) -> dict:
        result = self._require_session(session_id).channel.logs(after=after)
        result["session_id"] = session_id
        return result

    # ---------- 旧 cco/sta 兼容层 ----------
    def _legacy_session(self, name: str) -> _ModuleSession:
        if name not in CHANNELS:
            raise RuntimeError(f"未知通道：{name}（可选 {CHANNELS}）")
        with self._lock:
            session_id = self._legacy_session_ids.get(name)
            if session_id:
                return self._sessions[session_id]
        return self._require_session(
            self.create_session(
                title=name.upper(),
                module=name,
                legacy_channel=name,
            )["session_id"]
        )

    def channel(self, name: str) -> _SerialChannel:
        return self._legacy_session(name).channel

    @property
    def channels(self) -> dict:
        return {name: self.channel(name) for name in CHANNELS}

    def start(self, port: str, baudrate: int = 115200, bytesize: int = 8,
              parity: str = "N", stopbits: int = 1,
              log_type: str = "cco", channel: Optional[str] = None) -> dict:
        legacy = channel or log_type or "cco"
        return self.start_session(
            self._legacy_session(legacy).session_id,
            port=port, baudrate=baudrate, bytesize=bytesize,
            parity=parity, stopbits=stopbits,
        )

    def stop(self, channel: Optional[str] = None) -> dict:
        legacy = channel or "cco"
        return self.stop_session(self._legacy_session(legacy).session_id)

    def write(self, data_hex: str, channel: Optional[str] = None) -> dict:
        legacy = channel or "cco"
        return self.write_session(self._legacy_session(legacy).session_id, data_hex)

    def write_text(self, text: str, channel: str = "cco",
                   append_newline: bool = True) -> dict:
        return self.write_text_session(
            self._legacy_session(channel).session_id, text,
            append_newline=append_newline,
        )

    def set_baudrate(self, baudrate: int, channel: Optional[str] = None) -> dict:
        legacy = channel or "cco"
        return self.set_session_baudrate(self._legacy_session(legacy).session_id, baudrate)

    def flash(self, bin_path: str, slot: int = 0,
              baud_plan: Optional[List[int]] = None,
              no_reboot_after: bool = False,
              channel: Optional[str] = None) -> dict:
        legacy = channel or "cco"
        return self.flash_session(
            self._legacy_session(legacy).session_id,
            bin_path, slot, baud_plan, no_reboot_after,
        )

    def logs(self, after: int = -1, channel: Optional[str] = None) -> dict:
        legacy = channel or "cco"
        return self.logs_session(self._legacy_session(legacy).session_id, after=after)

    def status(self) -> dict:
        """保留旧响应形状，同时提供所有动态会话摘要。"""
        chs = {
            name: self._session_payload(self._legacy_session(name))
            for name in CHANNELS
        }
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
            "sessions": self.list_sessions(),
        }

    def _append_line(self, direction: str, text: str, channel: str = "cco") -> None:
        """向旧兼容通道追加一行（默认 cco）。"""
        self.channel(channel)._append_line(direction, text)
