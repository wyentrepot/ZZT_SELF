#!/usr/bin/env python3
"""Windows <-> WSL serial bridge (pure Python, no usbipd, no admin).

单文件、双端使用：

  Windows 侧（需要 pyserial）：
    python serial_bridge.py windows --config serial_bridge.json
    python serial_bridge.py windows --config serial_bridge.json --check
    python serial_bridge.py windows --list-ports

  WSL 侧（只用标准库）：
    python3 serial_bridge.py wsl run    --config ../../config/serial_bridge.json
    python3 serial_bridge.py wsl start  --config ../../config/serial_bridge.json
    python3 serial_bridge.py wsl stop   --config ../../config/serial_bridge.json
    python3 serial_bridge.py wsl status --config ../../config/serial_bridge.json

拓扑：
    Windows COMx <--pyserial--> serial_bridge.py (windows)
                                      |
                                      | TCP 127.0.0.1:<tcp_port>
                                      v
    WSL /run/hplc/serial/<id> --> /dev/pts/N <--serial_bridge.py (wsl)

边界（刻意不做）：
  - 一个物理口同一时刻只允许一个持有者；不做抢占/租约。
  - PTY 不接受 PARENB，WSL 侧应用一律以 parity="N" 打开；真实校验位
    固定在 Windows 侧 COM 打开参数里。
  - 不支持运行中切波特率/DTR/RTS；XMODEM 烧录请用单档波特率。
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_LINK_DIR = "/run/hplc/serial"
_VALID_PARITY = {"N", "E", "O", "M", "S"}
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_LOG_HANDLE = None
_VERBOSE = False


class ConfigError(ValueError):
    """配置不可用（缺字段、类型错误、重复端口等）。"""


# ---------------------------------------------------------------------------
# 通用：日志 / 配置 / 状态落盘
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def log(tag: str, message: str, *, error: bool = False) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] [{tag}] {message}"
    try:
        if _LOG_HANDLE is not None:
            _LOG_HANDLE.write(line + "\n")
            _LOG_HANDLE.flush()
        else:
            print(line, file=(sys.stderr if error else sys.stdout), flush=True)
    except Exception:
        pass


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def default_config_path(side: str) -> Path:
    """Windows 侧优先脚本同目录；WSL 侧优先仓库 config/。"""
    here = Path(__file__).resolve()
    if side == "windows":
        sibling = here.with_name("serial_bridge.json")
        if sibling.is_file():
            return sibling
        return here.parent / "config" / "serial_bridge.json"
    return here.parents[2] / "config" / "serial_bridge.json"


def load_config(path: Path | str) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{config_path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"配置文件不可读：{exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ConfigError("配置 version 必须为 1")

    windows = raw.get("windows") or {}
    wsl = raw.get("wsl") or {}
    if not isinstance(windows, dict) or not isinstance(wsl, dict):
        raise ConfigError("windows / wsl 必须为对象")

    link_dir = str(wsl.get("link_dir") or DEFAULT_LINK_DIR).strip()
    status_file = str(wsl.get("status_file") or "").strip()
    if not status_file:
        status_file = str(Path(link_dir) / "status.json")
    host = str(windows.get("host") or "127.0.0.1").strip()
    listen_host = str(wsl.get("listen_host") or "127.0.0.1").strip()
    try:
        reconnect_seconds = float(windows.get("reconnect_seconds", 1.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("windows.reconnect_seconds 必须为数字") from exc
    if reconnect_seconds < 0.1:
        reconnect_seconds = 0.1

    ports_raw = raw.get("ports")
    if not isinstance(ports_raw, list) or not ports_raw:
        raise ConfigError("ports 必须为非空数组")

    ports: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_tcp: set[int] = set()
    seen_control: set[int] = set()
    for index, item in enumerate(ports_raw):
        prefix = f"ports[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{prefix} 必须为对象")
        port_id = str(item.get("id") or "").strip()
        if not _ID_RE.match(port_id):
            raise ConfigError(f"{prefix}.id 非法：{port_id!r}")
        if port_id in seen_ids:
            raise ConfigError(f"ports id 重复：{port_id}")
        windows_com = str(item.get("windows_com") or "").strip()
        if not windows_com:
            raise ConfigError(f"{prefix}.windows_com 不能为空")
        try:
            tcp_port = int(item.get("tcp_port"))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{prefix}.tcp_port 必须为整数") from exc
        if not 1 <= tcp_port <= 65535:
            raise ConfigError(f"{prefix}.tcp_port 超出范围：{tcp_port}")
        if tcp_port in seen_tcp:
            raise ConfigError(f"tcp_port 重复：{tcp_port}")
        try:
            control_port = int(item.get("control_port") or (tcp_port + 1000))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{prefix}.control_port 必须为整数") from exc
        if not 1 <= control_port <= 65535:
            raise ConfigError(f"{prefix}.control_port 超出范围：{control_port}")
        if control_port in seen_control:
            raise ConfigError(f"control_port 重复：{control_port}")
        try:
            baudrate = int(item.get("baudrate", 115200))
            bytesize = int(item.get("bytesize", 8))
            stopbits = int(item.get("stopbits", 1))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{prefix} 串口参数必须为整数") from exc
        parity = str(item.get("parity", "N")).strip().upper()
        if baudrate < 300 or bytesize < 5 or bytesize > 8 or stopbits not in (1, 2):
            raise ConfigError(f"{prefix} 串口参数非法")
        if parity not in _VALID_PARITY:
            raise ConfigError(f"{prefix}.parity 非法：{parity}")
        enabled = bool(item.get("enabled", True))
        ports.append({
            "id": port_id,
            "label": str(item.get("label") or port_id),
            "windows_com": windows_com,
            "tcp_port": tcp_port,
            "control_port": control_port,
            "baudrate": baudrate,
            "bytesize": bytesize,
            "parity": parity,
            "stopbits": stopbits,
            "enabled": enabled,
        })
        seen_ids.add(port_id)
        seen_tcp.add(tcp_port)
        seen_control.add(control_port)

    overlap = (seen_tcp & seen_control)
    if overlap:
        raise ConfigError(f"tcp_port 与 control_port 冲突：{sorted(overlap)}")

    return {
        "version": 1,
        "windows": {"host": host, "reconnect_seconds": reconnect_seconds},
        "wsl": {
            "listen_host": listen_host,
            "link_dir": link_dir,
            "status_file": status_file,
            "log_file": str(wsl.get("log_file") or "").strip(),
        },
        "ports": ports,
        "_path": str(config_path),
    }


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_status(path: Path | str) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def status_running(status: dict[str, Any] | None) -> bool:
    return bool(status and pid_alive(int(status.get("pid") or 0)))


def _termios_baud_map() -> dict[int, int]:
    """termios Bxxxx 常量 -> 实际波特率。仅 POSIX 调用。"""
    import termios
    mapping: dict[int, int] = {}
    for name in dir(termios):
        if name.startswith("B") and name[1:].isdigit():
            mapping[getattr(termios, name)] = int(name[1:])
    return mapping


# ---------------------------------------------------------------------------
# WSL 侧：PTY 主管
# ---------------------------------------------------------------------------

class PortRuntime:
    """单个端口的 PTY + TCP 监听 + Windows 客户端泵。"""

    def __init__(self, spec: dict[str, Any], wsl_cfg: dict[str, Any],
                 stop_event: threading.Event,
                 status_event: threading.Event | None = None):
        self.spec = dict(spec)
        self.wsl_cfg = dict(wsl_cfg)
        self.stop_event = stop_event
        self.status_event = status_event
        self.link_dir = Path(str(wsl_cfg["link_dir"]))
        self.listen_host = str(wsl_cfg["listen_host"])
        self.master_fd: int | None = None
        self.slave_fd: int | None = None
        self.listener: socket.socket | None = None
        self.client: socket.socket | None = None
        self.client_addr = ""
        self.pts = ""
        self.link = self.link_dir / str(spec["id"])
        self.tcp_port = int(spec["tcp_port"])
        _control = spec.get("control_port")
        self.control_port = int(_control) if _control is not None else (self.tcp_port + 1000)
        self.control_listener: socket.socket | None = None
        self.control_client: socket.socket | None = None
        self.control_connected = False
        self.control_last_error = ""
        self.desired_baud = int(spec.get("baudrate", 115200))
        self._control_buf = bytearray()
        self._baud_map: dict[int, int] = {}
        self._termios_stop = threading.Event()
        self._termios_thread: threading.Thread | None = None
        self.listening = False
        self.client_connected = False
        self.connected_since = ""
        self.last_error = ""
        self.rx_bytes = 0   # Windows -> WSL（应用收到的字节）
        self.tx_bytes = 0   # WSL -> Windows（应用发出的字节）
        self.dropped_bytes = 0
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False

    # -- 生命周期 ----------------------------------------------------------
    def prepare(self) -> None:
        import pty
        import termios
        import tty

        self.link_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.link_dir, 0o755)
        except OSError:
            pass

        self._baud_map = _termios_baud_map()
        self.master_fd, self.slave_fd = pty.openpty()
        tty.setraw(self.master_fd)
        tty.setraw(self.slave_fd)
        attrs = termios.tcgetattr(self.slave_fd)
        attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
        termios.tcsetattr(self.slave_fd, termios.TCSANOW, attrs)
        os.set_blocking(self.master_fd, False)
        self.pts = os.ttyname(self.slave_fd)

        if self.link.is_symlink():
            self.link.unlink()
        elif self.link.exists():
            raise RuntimeError(f"{self.link} 已存在且不是软链，拒绝覆盖")
        os.symlink(self.pts, str(self.link))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.listen_host, self.tcp_port))
        listener.listen(4)
        listener.setblocking(False)
        self.listener = listener
        self.tcp_port = int(listener.getsockname()[1])
        if self.control_port and self.control_port == self.tcp_port:
            raise RuntimeError("control_port 不能与 tcp_port 相同")
        control = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        control.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        control.bind((self.listen_host, self.control_port))
        control.listen(4)
        control.setblocking(False)
        self.control_listener = control
        self.control_port = int(control.getsockname()[1])
        self.listening = True
        self.last_error = ""
        self._notify_status()
        log(self.spec["id"],
            f"PTY {self.pts} -> {self.link}; data {self.listen_host}:{self.tcp_port}; "
            f"control {self.listen_host}:{self.control_port}")

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._serve, name=f"serial-bridge-{self.spec['id']}", daemon=True,
        )
        self._thread.start()
        self._termios_thread = threading.Thread(
            target=self._watch_termios, name=f"serial-bridge-baud-{self.spec['id']}",
            daemon=True,
        )
        self._termios_thread.start()

    def join(self, timeout: float = 2.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._close_client("stopped")
        self._close_control("stopped")
        self._termios_stop.set()
        termios_thread, self._termios_thread = self._termios_thread, None
        if termios_thread is not None and termios_thread is not threading.current_thread():
            termios_thread.join(timeout=1.0)
        control_listener, self.control_listener = self.control_listener, None
        if control_listener is not None:
            try:
                control_listener.close()
            except OSError:
                pass
        listener, self.listener = self.listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        for attr in ("master_fd", "slave_fd"):
            fd = getattr(self, attr)
            setattr(self, attr, None)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.listening = False
        try:
            if self.link.is_symlink() and os.readlink(self.link) == self.pts:
                self.link.unlink()
        except OSError:
            pass
        self._notify_status()

    # -- 泵 ----------------------------------------------------------------
    def _serve(self) -> None:
        import select

        try:
            while not self.stop_event.is_set():
                if self.listener is None or self.master_fd is None:
                    break
                rlist: list[Any] = [self.listener, self.master_fd]
                if self.control_listener is not None:
                    rlist.append(self.control_listener)
                if self.client is not None:
                    rlist.append(self.client)
                if self.control_client is not None:
                    rlist.append(self.control_client)
                try:
                    readable, _, _ = select.select(rlist, [], [], 0.5)
                except (OSError, ValueError, TypeError):
                    break
                if self.stop_event.is_set():
                    break
                if self.listener is not None and self.listener in readable:
                    self._accept_client()
                if self.control_listener is not None and self.control_listener in readable:
                    self._accept_control()
                if self.client is not None and self.client in readable:
                    self._read_client()
                if self.control_client is not None and self.control_client in readable:
                    self._read_control()
                if self.master_fd is not None and self.master_fd in readable:
                    self._read_master()
        except Exception as exc:  # noqa: BLE001 - 单口故障不拖垮其他口
            self._set_error(f"串口泵异常：{exc}")
        finally:
            self.close()

    def _accept_client(self) -> None:
        if self.listener is None:
            return
        try:
            conn, addr = self.listener.accept()
        except (BlockingIOError, OSError):
            return
        conn.settimeout(0.5)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        if self.client is not None:
            self._close_client("被新的 Windows 连接替换")
        self.client = conn
        self.client_addr = f"{addr[0]}:{addr[1]}"
        self.client_connected = True
        self.connected_since = now_iso()
        self.last_error = ""
        self._notify_status()
        log(self.spec["id"], f"Windows 桥已连接 {self.client_addr}")

    def _accept_control(self) -> None:
        if self.control_listener is None:
            return
        try:
            conn, addr = self.control_listener.accept()
        except (BlockingIOError, OSError):
            return
        conn.settimeout(0.5)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        if self.control_client is not None:
            self._close_control("被新的控制连接替换")
        self.control_client = conn
        self.control_connected = True
        self.control_last_error = ""
        self._notify_status()
        self._send_control({"type": "config", "baudrate": self.desired_baud,
                            "source": "connect"})
        log(self.spec["id"], f"Windows 控制通道已连接 {addr[0]}:{addr[1]}")

    def _read_control(self) -> None:
        if self.control_client is None:
            return
        try:
            data = self.control_client.recv(4096)
        except socket.timeout:
            return
        except OSError as exc:
            self._close_control(f"控制通道读失败：{exc}")
            return
        if not data:
            self._close_control("Windows 控制通道已断开")
            return
        self._control_buf.extend(data)
        while b"\n" in self._control_buf:
            line, _, rest = self._control_buf.partition(b"\n")
            self._control_buf = bytearray(rest)
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if message.get("type") != "config_ack":
                continue
            if message.get("ok"):
                self.control_last_error = ""
                log(self.spec["id"], f"Windows 已切到波特率 {message.get('baudrate')}")
            else:
                self.control_last_error = str(message.get("error") or "配置失败")
                log(self.spec["id"], f"Windows 切波特率失败：{self.control_last_error}",
                    error=True)
        self._notify_status()

    def _send_control(self, payload: dict[str, Any]) -> bool:
        client = self.control_client
        if client is None:
            return False
        try:
            client.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            return True
        except OSError as exc:
            self._close_control(f"控制通道写失败：{exc}")
            return False

    def _close_control(self, reason: str) -> None:
        client, self.control_client = self.control_client, None
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
        was_connected = self.control_connected
        self.control_connected = False
        self._control_buf = bytearray()
        if was_connected:
            log(self.spec["id"], reason)
        self._notify_status()

    def _watch_termios(self) -> None:
        """APP 在 PTY 上改 baudrate 时，通知 Windows 侧重开 COM。"""
        import termios

        while not self._termios_stop.wait(0.05):
            if self._closed:
                break
            fd = self.slave_fd
            if fd is None:
                continue
            try:
                attrs = termios.tcgetattr(fd)
            except OSError:
                continue
            speed = attrs[5] or attrs[4]
            baud = self._baud_map.get(speed)
            # 38400 是 Linux pty 默认速率，不是应用设置，忽略。
            if not baud or baud == 38400:
                continue
            if baud != self.desired_baud:
                self.desired_baud = baud
                self._send_control({"type": "config", "baudrate": baud,
                                    "source": "termios"})
                log(self.spec["id"], f"检测到应用波特率变更 -> {baud}")

    def _read_client(self) -> None:
        if self.client is None:
            return
        try:
            data = self.client.recv(65536)
        except socket.timeout:
            return
        except OSError as exc:
            self._close_client(f"TCP 读失败：{exc}")
            return
        if not data:
            self._close_client("Windows 桥已断开")
            return
        self.rx_bytes += len(data)
        if self.master_fd is None:
            return
        try:
            os.write(self.master_fd, data)
        except BlockingIOError:
            # 没有应用在读 PTY 时缓冲会满；串口语义下直接丢弃。
            self.dropped_bytes += len(data)
        except OSError as exc:
            if exc.errno != errno.EIO:
                self._set_error(f"写 PTY 失败：{exc}")
            self.dropped_bytes += len(data)

    def _read_master(self) -> None:
        if self.master_fd is None:
            return
        try:
            data = os.read(self.master_fd, 65536)
        except BlockingIOError:
            return
        except OSError as exc:
            if exc.errno != errno.EIO:
                self._set_error(f"读 PTY 失败：{exc}")
            return
        if not data:
            return
        self.tx_bytes += len(data)
        if self.client is None:
            return
        try:
            self.client.sendall(data)
        except (socket.timeout, OSError) as exc:
            self._close_client(f"TCP 写失败：{exc}")

    def _close_client(self, reason: str) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
        was_connected = self.client_connected
        self.client_connected = False
        self.client_addr = ""
        self.connected_since = ""
        if was_connected:
            log(self.spec["id"], reason)
        self._notify_status()

    def _notify_status(self) -> None:
        if self.status_event is not None:
            self.status_event.set()

    def _set_error(self, message: str) -> None:
        if message != self.last_error:
            self.last_error = message
            self._notify_status()
            log(self.spec["id"], message, error=True)

    # -- 状态快照 ----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.spec["id"],
                "label": self.spec["label"],
                "windows_com": self.spec["windows_com"],
                "tcp_port": self.tcp_port,
                "control_port": self.control_port,
                "control_connected": self.control_connected,
                "desired_baud": self.desired_baud,
                "control_last_error": self.control_last_error,
                "link": str(self.link),
                "pts": self.pts,
                "listening": self.listening,
                "client_connected": self.client_connected,
                "client_addr": self.client_addr,
                "connected_since": self.connected_since,
                "rx_bytes": self.rx_bytes,
                "tx_bytes": self.tx_bytes,
                "dropped_bytes": self.dropped_bytes,
                "last_error": self.last_error,
                "physical": {
                    "baudrate": self.spec["baudrate"],
                    "bytesize": self.spec["bytesize"],
                    "parity": self.spec["parity"],
                    "stopbits": self.spec["stopbits"],
                },
            }


def _build_status(config: dict[str, Any], runtimes: list[PortRuntime],
                  started_at: str, pid: int) -> dict[str, Any]:
    return {
        "version": 1,
        "pid": pid,
        "started_at": started_at,
        "updated_at": now_iso(),
        "config": config.get("_path", ""),
        "link_dir": config["wsl"]["link_dir"],
        "listen_host": config["wsl"]["listen_host"],
        "ports": {rt.spec["id"]: rt.snapshot() for rt in runtimes},
    }


def _acquire_lock(lock_path: Path):
    import fcntl
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError(f"已有 wsl_bridge 实例持有锁：{lock_path}")
    return handle


def run_wsl(config_path: Path | str) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        log("config", str(exc), error=True)
        return 2

    link_dir = Path(config["wsl"]["link_dir"])
    status_path = Path(config["wsl"]["status_file"])
    lock_path = link_dir / "wsl_bridge.lock"
    try:
        lock_handle = _acquire_lock(lock_path)
    except (RuntimeError, OSError) as exc:
        log("wsl", str(exc), error=True)
        return 1

    started_at = now_iso()
    stop_event = threading.Event()
    for sig_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, lambda *_: stop_event.set())
            except (ValueError, OSError):
                pass

    status_event = threading.Event()
    runtimes: list[PortRuntime] = []
    for spec in config["ports"]:
        rt = PortRuntime(spec, config["wsl"], stop_event, status_event)
        if not spec.get("enabled", True):
            rt.last_error = "配置已禁用"
            runtimes.append(rt)
            continue
        try:
            rt.prepare()
            rt.start()
        except Exception as exc:  # noqa: BLE001 - 单口失败继续
            rt.last_error = f"启动失败：{exc}"
            log(spec["id"], rt.last_error, error=True)
            rt.close()
        runtimes.append(rt)

    listening = [rt for rt in runtimes if rt.listening]
    if not listening:
        log("wsl", "没有任何端口启动成功", error=True)
        atomic_write_json(status_path, _build_status(config, runtimes, started_at, os.getpid()))
        for rt in runtimes:
            rt.close()
        try:
            lock_handle.close()
        except OSError:
            pass
        return 1

    try:
        atomic_write_json(status_path, _build_status(config, runtimes, started_at, os.getpid()))
    except OSError as exc:
        log("wsl", f"状态写入失败：{exc}", error=True)
        for rt in runtimes:
            rt.close()
        try:
            lock_handle.close()
        except OSError:
            pass
        return 1
    log("wsl", f"桥已启动：{len(listening)} 个端口；状态 {status_path}")

    def writer() -> None:
        while not stop_event.is_set():
            status_event.wait(1.0)
            status_event.clear()
            if stop_event.is_set():
                break
            try:
                atomic_write_json(
                    status_path,
                    _build_status(config, runtimes, started_at, os.getpid()),
                )
            except OSError as exc:
                log("wsl", f"状态写入失败：{exc}", error=True)

    writer_thread = threading.Thread(target=writer, name="serial-bridge-status", daemon=True)
    writer_thread.start()

    try:
        while not stop_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        stop_event.set()

    for rt in runtimes:
        rt.close()
    stop_event.set()
    writer_thread.join(timeout=2.0)
    try:
        status_path.unlink()
    except OSError:
        pass
    try:
        lock_handle.close()
    except OSError:
        pass
    log("wsl", "桥已停止")
    return 0


def _tail(path: Path, lines: int = 20) -> str:
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def start_wsl(config_path: Path | str) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        log("config", str(exc), error=True)
        return 2

    status_path = Path(config["wsl"]["status_file"])
    current = read_status(status_path)
    if status_running(current):
        print(json.dumps({"started": False, "already_running": True, "status": current},
                         ensure_ascii=False, indent=2))
        return 0

    raw_log = config["wsl"].get("log_file") or ""
    if raw_log:
        log_file = Path(raw_log).expanduser()
        if not log_file.is_absolute():
            log_file = Path(__file__).resolve().parents[2] / log_file
    else:
        log_file = Path(__file__).resolve().parents[2] / "data" / "runtime" / "serial_bridge.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_file, "a", encoding="utf-8")
    try:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "wsl", "run",
             "--config", str(Path(config_path).expanduser().resolve())],
            stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    finally:
        handle.close()

    deadline = time.time() + 5.0
    while time.time() < deadline:
        time.sleep(0.2)
        status = read_status(status_path)
        if status and int(status.get("pid") or 0) == child.pid and status.get("ports"):
            if any(port.get("listening") for port in status["ports"].values()):
                print(json.dumps({
                    "started": True, "pid": child.pid, "log": str(log_file),
                    "status": status,
                }, ensure_ascii=False, indent=2))
                return 0
        if child.poll() is not None:
            break
    tail = _tail(log_file)
    print(json.dumps({
        "started": False, "pid": child.pid, "log": str(log_file),
        "error": "启动失败或 5 秒内未就绪", "log_tail": tail,
    }, ensure_ascii=False, indent=2))
    return 1


def _cleanup_stale_links(config: dict[str, Any]) -> None:
    link_dir = Path(config["wsl"]["link_dir"])
    for spec in config["ports"]:
        link = link_dir / spec["id"]
        try:
            if link.is_symlink() and os.readlink(link).startswith("/dev/pts/"):
                link.unlink()
        except OSError:
            pass


def stop_wsl(config_path: Path | str) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        log("config", str(exc), error=True)
        return 2
    status_path = Path(config["wsl"]["status_file"])
    status = read_status(status_path)
    if not status_running(status):
        _cleanup_stale_links(config)
        try:
            status_path.unlink()
        except OSError:
            pass
        print(json.dumps({"stopped": False, "reason": "wsl bridge 未运行"},
                         ensure_ascii=False, indent=2))
        return 0

    pid = int(status.get("pid") or 0)
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        cmdline = ""
    if "serial_bridge.py" not in cmdline:
        print(json.dumps({
            "stopped": False, "pid": pid,
            "reason": "PID 不是 serial_bridge.py，拒绝发送信号",
        }, ensure_ascii=False, indent=2))
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(json.dumps({"stopped": False, "pid": pid, "error": str(exc)},
                         ensure_ascii=False, indent=2))
        return 1

    deadline = time.time() + 5.0
    while time.time() < deadline and pid_alive(pid):
        time.sleep(0.2)
    forced = False
    if pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
            forced = True
        except OSError:
            pass
        time.sleep(0.3)

    _cleanup_stale_links(config)
    try:
        status_path.unlink()
    except OSError:
        pass
    print(json.dumps({"stopped": True, "pid": pid, "forced": forced},
                     ensure_ascii=False, indent=2))
    return 0


def status_wsl(config_path: Path | str) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        log("config", str(exc), error=True)
        return 2
    status_path = Path(config["wsl"]["status_file"])
    status = read_status(status_path)
    running = status_running(status)
    print(json.dumps({
        "running": running,
        "status_file": str(status_path),
        "status": status,
    }, ensure_ascii=False, indent=2))
    return 0 if running else 1


# ---------------------------------------------------------------------------
# Windows 侧：COM <-> TCP
# ---------------------------------------------------------------------------

def _require_pyserial():
    try:
        import serial  # noqa: F401, PLC0415
    except ImportError as exc:
        raise RuntimeError("缺少 pyserial，请先执行：python -m pip install pyserial") from exc
    return serial


def list_windows_ports() -> int:
    try:
        serial = _require_pyserial()
        from serial.tools import list_ports  # noqa: PLC0415
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    ports = list(list_ports.comports())
    if not ports:
        print("(no COM ports)")
        return 0
    for port in ports:
        print(f"{port.device}\t{port.description}\t{port.hwid}")
    return 0


def check_windows(config: dict[str, Any]) -> int:
    try:
        serial = _require_pyserial()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    host = config["windows"]["host"]
    results = []
    all_tcp = True
    for spec in config["ports"]:
        if not spec.get("enabled", True):
            results.append({"id": spec["id"], "enabled": False})
            continue
        control_port = int(spec.get("control_port") or (int(spec["tcp_port"]) + 1000))
        item: dict[str, Any] = {
            "id": spec["id"], "com": spec["windows_com"], "tcp_port": spec["tcp_port"],
            "control_port": control_port,
            "tcp_reachable": False, "control_reachable": False,
            "com_openable": False, "com_error": "",
        }
        try:
            sock = socket.create_connection((host, spec["tcp_port"]), timeout=1.0)
            sock.close()
            item["tcp_reachable"] = True
        except OSError as exc:
            item["tcp_error"] = str(exc)
            all_tcp = False
        try:
            control = socket.create_connection((host, control_port), timeout=1.0)
            control.close()
            item["control_reachable"] = True
        except OSError as exc:
            item["control_error"] = str(exc)
        try:
            ser = serial.Serial(
                spec["windows_com"], baudrate=spec["baudrate"], bytesize=spec["bytesize"],
                parity=spec["parity"], stopbits=spec["stopbits"], timeout=0.1,
            )
            ser.close()
            item["com_openable"] = True
        except Exception as exc:  # noqa: BLE001 - 诊断输出
            item["com_error"] = str(exc)
        if item["tcp_reachable"] and not item["com_openable"]:
            item["hint"] = "WSL 侧在监听；COM 被占用（Windows 桥、usbipd 或别的工具持有）"
        elif item["tcp_reachable"] and item["com_openable"]:
            item["hint"] = "WSL 侧在监听且 COM 可用；Windows 桥未运行"
        elif not item["tcp_reachable"] and item["com_openable"]:
            item["hint"] = "COM 可用但 WSL 侧未监听；先启动 wsl 端"
        else:
            item["hint"] = "COM 不可用且 WSL 侧未监听；检查 USB/接线与 wsl 端"
        results.append(item)
    print(json.dumps({"host": host, "ok_tcp": all_tcp, "ports": results},
                     ensure_ascii=False, indent=2))
    return 0 if all_tcp else 1


def _connect_tcp(host: str, port: int) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=3.0)
    sock.settimeout(0.2)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    return sock


class PortLink:
    """一个端口的 Windows 侧数据泵 + 控制通道。

    数据 TCP 承载原始字节；控制 TCP 接收 WSL 侧发来的
    ``{"type":"config","baudrate":9600}``，在持锁状态下关闭/重开 COM，
    从而支持 COM4 上 CCO 的 9600↔115200 自适应。
    """

    def __init__(self, spec: dict[str, Any], host: str, reconnect_seconds: float,
                 stop_event: threading.Event):
        self.spec = dict(spec)
        self.host = host
        self.reconnect = reconnect_seconds
        self.stop_event = stop_event
        self.tag = spec["id"]
        self.control_port = int(spec.get("control_port") or (int(spec["tcp_port"]) + 1000))
        self.serial_mod = _require_pyserial()
        self.ser_lock = threading.Lock()
        self.ser = None
        self.sock: socket.socket | None = None
        self.control_sock: socket.socket | None = None
        self.link_stop = threading.Event()
        self.desired = {
            "baudrate": int(spec["baudrate"]),
            "bytesize": int(spec["bytesize"]),
            "parity": str(spec["parity"]),
            "stopbits": int(spec["stopbits"]),
        }
        self.last_message = ""
        self.last_log_at = 0.0

    def note(self, message: str, *, error: bool = False) -> None:
        now = time.time()
        if _VERBOSE or message != self.last_message or now - self.last_log_at >= 30:
            log(self.tag, message, error=error)
            self.last_message = message
            self.last_log_at = now

    # -- 主循环 ------------------------------------------------------------
    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._run_once()
            except Exception as exc:  # noqa: BLE001 - 持续重连
                self.note(f"连接失败：{exc}", error=True)
            finally:
                self._close_all()
            if not self.stop_event.is_set():
                self.stop_event.wait(self.reconnect)
        log(self.tag, "已停止")

    def _open_serial(self):
        with self.ser_lock:
            self.ser = self.serial_mod.Serial(
                self.spec["windows_com"],
                baudrate=self.desired["baudrate"],
                bytesize=self.desired["bytesize"],
                parity=self.desired["parity"],
                stopbits=self.desired["stopbits"],
                timeout=0.1,
                write_timeout=2.0,
            )

    def _run_once(self) -> None:
        self.link_stop.clear()
        # 先连 WSL：WSL 端没起时不去占 COM，Windows 工具可以正常使用。
        self.sock = _connect_tcp(self.host, int(self.spec["tcp_port"]))
        self._open_serial()
        log(self.tag,
            f"{self.spec['windows_com']} <-> {self.host}:{self.spec['tcp_port']} 已连接"
            f"（baudrate={self.desired['baudrate']}）")

        reader = threading.Thread(target=self._serial_to_tcp,
                                  name=f"serial2tcp-{self.tag}", daemon=True)
        control = threading.Thread(target=self._control_loop,
                                   name=f"serial2tcp-control-{self.tag}", daemon=True)
        reader.start()
        control.start()
        try:
            while not self.stop_event.is_set() and not self.link_stop.is_set():
                try:
                    data = self.sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError as exc:
                    self.note(f"读 TCP 失败：{exc}", error=True)
                    break
                if not data:
                    self.note("WSL 侧已断开")
                    break
                with self.ser_lock:
                    ser = self.ser
                    if ser is None:
                        continue
                    try:
                        ser.write(data)
                    except Exception as exc:  # noqa: BLE001 - 断链由外层重连
                        self.note(f"写 COM 失败：{exc}", error=True)
                        self.link_stop.set()
                        break
        finally:
            self.link_stop.set()
            reader.join(timeout=2.0)
            control.join(timeout=2.0)

    def _serial_to_tcp(self) -> None:
        while not self.stop_event.is_set() and not self.link_stop.is_set():
            with self.ser_lock:
                ser = self.ser
                if ser is None:
                    time.sleep(0.02)
                    continue
                try:
                    data = ser.read(4096)
                except Exception as exc:  # noqa: BLE001 - 断链由外层重连
                    if not self.link_stop.is_set():
                        self.note(f"读 COM 失败：{exc}", error=True)
                    break
            if data and self.sock is not None:
                try:
                    self.sock.sendall(data)
                except OSError as exc:
                    self.note(f"写 TCP 失败：{exc}", error=True)
                    break
        self.link_stop.set()

    def _control_loop(self) -> None:
        while not self.stop_event.is_set() and not self.link_stop.is_set():
            control = None
            try:
                control = _connect_tcp(self.host, self.control_port)
                control.settimeout(0.5)
                self.control_sock = control
                control.sendall((json.dumps(
                    {"type": "hello", "port": self.tag}, ensure_ascii=False) + "\n"
                ).encode("utf-8"))
                buffer = bytearray()
                while not self.stop_event.is_set() and not self.link_stop.is_set():
                    try:
                        data = control.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    buffer.extend(data)
                    while b"\n" in buffer:
                        line, _, rest = buffer.partition(b"\n")
                        buffer = bytearray(rest)
                        try:
                            message = json.loads(line.decode("utf-8"))
                        except (UnicodeDecodeError, ValueError):
                            continue
                        self._handle_control(message, control)
            except Exception as exc:  # noqa: BLE001 - 控制通道断了不影响数据
                if not self.link_stop.is_set():
                    self.note(f"控制通道连接失败：{exc}", error=True)
            finally:
                if self.control_sock is control:
                    self.control_sock = None
                if control is not None:
                    try:
                        control.close()
                    except OSError:
                        pass
            if not self.stop_event.is_set() and not self.link_stop.is_set():
                time.sleep(self.reconnect)

    def _handle_control(self, message: dict[str, Any], control: socket.socket) -> None:
        if message.get("type") != "config":
            return
        try:
            baudrate = int(message.get("baudrate") or 0)
        except (TypeError, ValueError):
            baudrate = 0
        if baudrate <= 0:
            return
        ok, error = self._reconfigure(baudrate)
        try:
            control.sendall((json.dumps({
                "type": "config_ack", "ok": ok,
                "baudrate": self.desired["baudrate"], "error": error,
            }, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def _reconfigure(self, baudrate: int) -> tuple[bool, str]:
        with self.ser_lock:
            if self.ser is not None and baudrate == self.desired["baudrate"]:
                return True, ""
            self.desired["baudrate"] = baudrate
            if self.ser is not None:
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
            try:
                self.ser = self.serial_mod.Serial(
                    self.spec["windows_com"],
                    baudrate=self.desired["baudrate"],
                    bytesize=self.desired["bytesize"],
                    parity=self.desired["parity"],
                    stopbits=self.desired["stopbits"],
                    timeout=0.1,
                    write_timeout=2.0,
                )
                log(self.tag, f"COM 已按应用要求重开 baudrate={baudrate}")
                return True, ""
            except Exception as exc:  # noqa: BLE001 - 保持新目标参数，交给外层重连重试
                self.ser = None
                self.link_stop.set()
                log(self.tag, f"重开 COM 失败（将按 {baudrate} 重连重试）：{exc}",
                    error=True)
                return False, str(exc)

    def _close_all(self) -> None:
        self.link_stop.set()
        with self.ser_lock:
            ser, self.ser = self.ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        control, self.control_sock = self.control_sock, None
        if control is not None:
            try:
                control.close()
            except OSError:
                pass


def pump_port(spec: dict[str, Any], host: str, reconnect_seconds: float,
              stop_event: threading.Event) -> None:
    """兼容旧调用：单端口数据泵。"""
    PortLink(spec, host, reconnect_seconds, stop_event).run()


def run_windows(config: dict[str, Any]) -> int:
    try:
        _require_pyserial()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    host = config["windows"]["host"]
    reconnect = float(config["windows"]["reconnect_seconds"])
    stop_event = threading.Event()

    def handler(*_args) -> None:
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    threads = []
    for spec in config["ports"]:
        if not spec.get("enabled", True):
            continue
        thread = threading.Thread(
            target=pump_port, args=(spec, host, reconnect, stop_event),
            name=f"serial-bridge-win-{spec['id']}", daemon=True,
        )
        thread.start()
        threads.append(thread)
    if not threads:
        print("没有 enabled 端口", file=sys.stderr)
        return 2
    log("windows", f"已启动 {len(threads)} 个端口；Ctrl+C 停止")
    try:
        while not stop_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        stop_event.set()
    for thread in threads:
        thread.join(timeout=3.0)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Windows <-> WSL serial bridge")
    parser.add_argument("--verbose", action="store_true", help="打印每次重试日志")
    parser.add_argument("--log", help="Windows 侧日志文件（pythonw 后台运行时用）")
    sub = parser.add_subparsers(dest="side", required=True)

    win = sub.add_parser("windows", help="Windows 侧：COM <-> TCP")
    win.add_argument("--config", help="serial_bridge.json 路径")
    win.add_argument("--check", action="store_true", help="只诊断 COM/TCP 状态，不启动泵")
    win.add_argument("--list-ports", action="store_true", help="列出 Windows COM 口")
    # 允许 --log/--verbose 也写在子命令之后（不覆盖写在最前面的全局值）。
    win.add_argument("--log", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    win.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS,
                     help=argparse.SUPPRESS)

    wsl = sub.add_parser("wsl", help="WSL 侧：TCP <-> PTY")
    wsl.add_argument("action", choices=["run", "start", "stop", "status"])
    wsl.add_argument("--config", help="serial_bridge.json 路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    global _LOG_HANDLE, _VERBOSE
    args = build_parser().parse_args(argv)
    _VERBOSE = bool(args.verbose)
    if getattr(args, "log", None):
        path = Path(args.log).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        _LOG_HANDLE = open(path, "a", encoding="utf-8")

    try:
        if args.side == "windows":
            config_path = Path(args.config) if args.config else default_config_path("windows")
            if args.list_ports:
                return list_windows_ports()
            config = load_config(config_path)
            if args.check:
                return check_windows(config)
            return run_windows(config)

        config_path = Path(args.config) if args.config else default_config_path("wsl")
        if args.action == "run":
            return run_wsl(config_path)
        if args.action == "start":
            return start_wsl(config_path)
        if args.action == "stop":
            return stop_wsl(config_path)
        return status_wsl(config_path)
    except ConfigError as exc:
        log("config", str(exc), error=True)
        return 2
    finally:
        if _LOG_HANDLE is not None:
            _LOG_HANDLE.close()


if __name__ == "__main__":
    sys.exit(main())
