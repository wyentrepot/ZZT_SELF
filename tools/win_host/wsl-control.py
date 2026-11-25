#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wsl-control.py — Windows 宿主控制面（v2）

常驻 Windows，给 WSL 侧技能/页面提供**一个入口**：看板 + 服务启停 + 逐口开关 + 自检。
v2 与 v1 的差别：

* 数据面用纯 Python 直接读/测（winreg、UNC 状态文件、socket、HTTP），**不再每次 fork
  powershell.exe uart-map.ps1**（单次 4~9 秒）；uart-map.ps1 退回“人用的交互菜单”。
* 配置从 `%USERPROFILE%\\.wsl-control\\settings.json` 读，端口/路径不再写死在代码里。
* 统一 token 鉴权（非回环必须带 `X-Host-Token`），写操作记审计。
* 新增 `/api/v1/caps`、`/api/v1/ports`、`/api/v1/services`、`/api/v1/doctor`、长轮询。
* **usbipd 接口已移除**（串口统一走 Python 桥）：`/api/usbipd/*` 返回 410。

兼容：v1 的 `/api/status`、`/api/bridge/*`、`/api/gateway/*`、`/api/diag/*`、日志 tail 仍可用。

用法：
    pythonw wsl-control.py            # 计划任务 FC-Wsl-Control 拉起
    python wsl-control.py --port 8767 # 调试用另一个端口
    python wsl-control.py --print-settings
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = "2.1.0"
SERVICE = "wsl-control"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
RUN_DIR = os.path.join(HOME, ".wsl-control")
SETTINGS_PATH = os.path.join(RUN_DIR, "settings.json")
TOKEN_PATH = os.path.join(RUN_DIR, "token")
LOG_PATH = os.path.join(RUN_DIR, "service.log")
AUDIT_PATH = os.path.join(RUN_DIR, "audit.jsonl")

DEFAULT_SETTINGS = {
    "version": 2,
    "host": "0.0.0.0",
    "port": 8766,
    "require_token": True,
    "status_ttl_seconds": 8.0,
    "status_stale_seconds": 120.0,
    "log_lines_default": 100,
    "log_lines_max": 2000,
    "wsl": {
        "distro": "Ubuntu-22.04",
        "repo": "/01-workfile-ai/01-zzt/ZZT_SELF",
    },
    "bridge": {
        "script_rel": "tools/serial_bridge/serial_bridge.py",
        "config_rel": "config/serial_bridge.json",
        "status_unc": r"\\wsl.localhost\{distro}\run\hplc\serial\status.json",
        "pid_file": "%USERPROFILE%\\.serial-bridge\\bridge.pid",
        "log_file": "%USERPROFILE%\\.serial-bridge\\bridge.log",
        "start_timeout_seconds": 20,
    },
    "gateway": {
        "port": 8700,
        "build_plain": r"D:\019-wy-tool\ZZT_SELF\.build_plain",
        "python": r"D:\019-wy-tool\ZZT_SELF\.venv\Scripts\python.exe",
        "module": "parser_service.run",
        "start_timeout_seconds": 25,
    },
    "diag": {
        "task": "FC-Diag-Service",
        "port": 8765,
        "start_timeout_seconds": 30,
    },
    "workbench": {"port": 8790, "health_path": "/api/health"},
}

# ---------------------------------------------------------------- 基础工具


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path or ""))


def atomic_write_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def deep_merge(default: dict, override: dict) -> dict:
    out = dict(default)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings(path: str = SETTINGS_PATH) -> dict:
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        data = {}
    settings = deep_merge(DEFAULT_SETTINGS, data)
    if not os.path.exists(path):
        try:
            atomic_write_json(path, settings)
        except OSError:
            pass
    return settings


class WindowsHost:
    """宿主操作层；测试里用 FakeHost 替身，不碰真实系统。"""

    def __init__(self, settings: dict):
        self.settings = settings
        self.is_windows = sys.platform == "win32"

    # ---- 串口 ----
    def serial_ports(self) -> list[dict]:
        """返回 [{'com': 'COM12', 'device': '...'}]，按 COM 号排序。

        优先用 pyserial 的 list_ports（有友好名，如 "Silicon Labs CP210x (COM18)"），
        拿不到再退回注册表 SERIALCOMM（只有 \\Device\\xxx）。
        """
        if not self.is_windows:
            return []
        try:
            from serial.tools import list_ports  # noqa: PLC0415
            items = []
            for port in list_ports.comports():
                desc = re.sub(r"\s*\(COM\d+\)\s*$", "", (port.description or "").strip())
                if desc and desc != "n/a":
                    items.append({"com": port.device,
                                  "device": "%s (%s)" % (desc, port.device)})
                else:
                    items.append({"com": port.device, "device": port.device})
            if items:
                return sorted(items, key=lambda x: _com_sort_key(x["com"]))
        except Exception:  # noqa: BLE001
            pass
        try:
            import winreg  # noqa: PLC0415
        except ImportError:
            return []
        found = {}
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        except OSError:
            return []
        try:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                found[str(value)] = name
        finally:
            key.Close()
        return [{"com": com, "device": found[com]} for com in sorted(found, key=_com_sort_key)]

    # ---- 进程 ----
    def proc_alive(self, pid: int) -> bool:
        if not pid:
            return False
        if not self.is_windows:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        try:
            import ctypes  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415
        except ImportError:
            return True
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_ACCESS_DENIED = 5
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            # 只有“权限不足”才保守认为存活；87(参数错)等表示进程不存在 → 已退出
            return kernel32.GetLastError() == ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    def spawn(self, args: list[str], cwd: str | None, env: dict | None,
              log_path: str | None) -> int:
        kwargs = {}
        if self.is_windows:
            DETACHED = 0x00000008
            NEW_GROUP = 0x00000200
            NO_WINDOW = 0x08000000
            kwargs["creationflags"] = DETACHED | NEW_GROUP | NO_WINDOW
        out = subprocess.DEVNULL
        if log_path:
            os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
            out = open(log_path, "ab")
        try:
            proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=out, stderr=out,
                                    stdin=subprocess.DEVNULL, close_fds=True, **kwargs)
        finally:
            if hasattr(out, "close") and out is not subprocess.DEVNULL:
                out.close()
        return proc.pid

    def run(self, args: list[str], timeout: float = 8.0) -> tuple[int, str]:
        try:
            proc = subprocess.run(args, capture_output=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL,
                                  creationflags=(0x08000000 if self.is_windows else 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return -1, f"{exc}"
        raw = proc.stdout or b""
        for enc in ("utf-8", "gbk"):
            try:
                return proc.returncode, raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return proc.returncode, raw.decode("utf-8", errors="replace")

    def terminate(self, pid: int) -> bool:
        if not pid:
            return False
        if self.is_windows:
            rc, out = self.run(["taskkill", "/PID", str(pid), "/F"], timeout=6)
            text = (out or "").lower()
            if "not found" in text or "没有找到" in text or "找不到" in text:
                return True
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                return False
        deadline = time.time() + 4
        while time.time() < deadline:
            if not self.proc_alive(pid):
                return True
            time.sleep(0.2)
        return not self.proc_alive(pid)

    def pid_connected_to(self, ports) -> int | None:
        """反查正在连接到指定本地端口集合的进程（Windows 桥是 TCP 客户端）。"""
        wanted = {int(p) for p in ports if p}
        if not wanted:
            return None
        rc, out = self.run(["netstat", "-ano", "-p", "TCP"], timeout=6)
        if rc != 0:
            return None
        hits = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 5 or "ESTABLISHED" not in line.upper():
                continue
            remote = parts[2]
            try:
                remote_port = int(remote.rsplit(":", 1)[-1])
                pid = int(parts[-1])
            except (ValueError, IndexError):
                continue
            if remote_port in wanted and pid > 4:
                hits[pid] = hits.get(pid, 0) + 1
        if not hits:
            return None
        return max(hits, key=lambda k: hits[k])

    def pid_listening(self, port: int) -> int | None:
        rc, out = self.run(["netstat", "-ano", "-p", "TCP"], timeout=6)
        if rc != 0:
            return None
        needle = ":%d" % port
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 5 or "LISTEN" not in line.upper():
                continue
            local = parts[1]
            if local.endswith(needle):
                try:
                    return int(parts[-1])
                except ValueError:
                    return None
        return None

    # ---- 网络/文件 ----
    def tcp_open(self, host: str, port: int, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except OSError:
            return False

    def http_json(self, url: str, timeout: float = 1.5):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def read_text(self, path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None

    def stat(self, path: str):
        try:
            st = os.stat(path)
            return {"mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                    "size": st.st_size}
        except OSError:
            return None

    def write_text(self, path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)

    def append_text(self, path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def _com_sort_key(com: str):
    digits = "".join(ch for ch in str(com) if ch.isdigit())
    return (int(digits) if digits else 999, str(com))


# 端口映射增改的校验规则与自动分配范围
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_COM_PATTERN = re.compile(r"^COM\d+$", re.IGNORECASE)
_PARITIES = ("N", "E", "O")
_TCP_RANGE = range(8818, 8900)
_CTL_RANGE = range(9818, 9900)
# 仅这些字段变化时无需重启桥（Windows 桥/WSL supervisor 都不读它们）
_LABEL_ONLY_FIELDS = ("label", "usage")
_PORT_FIELDS_EDITABLE = ("label", "usage", "windows_com", "baudrate", "bytesize",
                         "parity", "stopbits", "tcp_port", "control_port")


def _norm_parity(value) -> str:
    parity = str(value or "N").strip().upper()
    if parity not in _PARITIES:
        raise HostError("VALIDATION", "parity 必须是 N/E/O：%r" % value, {"field": "parity"})
    return parity


# ---------------------------------------------------------------- 控制面逻辑


class HostError(Exception):
    def __init__(self, code: str, message: str, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


class HostAgent:
    def __init__(self, settings: dict, host: WindowsHost | None = None,
                 token_path: str = TOKEN_PATH, audit_path: str = AUDIT_PATH,
                 log_path: str = LOG_PATH, clock=time.time):
        self.settings = settings
        self.host = host or WindowsHost(settings)
        self.token_path = token_path
        self.audit_path = audit_path
        self.log_path = log_path
        self.clock = clock
        self.started_at = clock()
        self.token = self._load_or_create_token()
        self._snapshot = None
        self._snapshot_at = 0.0
        self._snapshot_ver = 0
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._cond = threading.Condition()
        self._action_locks = {
            "bridge": threading.Lock(),
            "gateway": threading.Lock(),
            "diag": threading.Lock(),
            "config": threading.Lock(),
        }

    # ---- 路径 ----
    def _wsl_unc(self) -> str:
        distro = self.settings["wsl"]["distro"]
        repo = self.settings["wsl"]["repo"].strip("/").replace("/", "\\")
        return "\\\\wsl.localhost\\%s\\%s" % (distro, repo)

    def bridge_script(self) -> str:
        return self._wsl_unc() + "\\" + self.settings["bridge"]["script_rel"].replace("/", "\\")

    def bridge_config(self) -> str:
        return self._wsl_unc() + "\\" + self.settings["bridge"]["config_rel"].replace("/", "\\")

    def bridge_status_path(self) -> str:
        pat = self.settings["bridge"]["status_unc"]
        return expand(pat.replace("{distro}", self.settings["wsl"]["distro"]))

    def bridge_pid_file(self) -> str:
        return expand(self.settings["bridge"]["pid_file"])

    def bridge_log(self) -> str:
        return expand(self.settings["bridge"]["log_file"])

    # ---- token / 审计 ----
    def _load_or_create_token(self) -> str:
        try:
            with open(self.token_path, "r", encoding="utf-8") as fh:
                token = fh.read().strip()
                if token:
                    return token
        except OSError:
            pass
        token = secrets.token_hex(24)
        try:
            os.makedirs(os.path.dirname(self.token_path), exist_ok=True)
            with open(self.token_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(token + "\n")
        except OSError:
            pass
        return token

    def audit(self, action: str, target: str, client: str, ok: bool,
              error: str | None = None, detail: dict | None = None) -> None:
        line = {
            "ts": now_iso(),
            "action": action,
            "target": target,
            "client": client,
            "ok": bool(ok),
            "error": error,
            "detail": detail or {},
        }
        try:
            self.host.append_text(self.audit_path, json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def audit_tail(self, lines: int = 50) -> list[dict]:
        raw = self.host.read_text(self.audit_path) or ""
        out = []
        for line in raw.splitlines()[-lines:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # ---- 各域状态 ----
    def bridge_config_state(self) -> dict:
        path = self.bridge_config()
        text = self.host.read_text(path)
        if text is None:
            return {"path": path, "ports": [], "error": "配置文件不可读（WSL 未运行或路径不对）",
                    "stat": None}
        try:
            cfg = json.loads(text)
        except ValueError as exc:
            return {"path": path, "ports": [], "error": "JSON 解析失败：%s" % exc,
                    "stat": self.host.stat(path)}
        ports = []
        for item in cfg.get("ports", []):
            ports.append({
                "id": item.get("id"),
                "windows_com": item.get("windows_com"),
                "label": item.get("label") or "",
                "usage": item.get("usage") or "",
                "enabled": item.get("enabled", True) is not False,
                "baudrate": item.get("baudrate"),
                "parity": item.get("parity"),
                "tcp_port": item.get("tcp_port"),
            })
        return {"path": path, "ports": ports, "error": None, "stat": self.host.stat(path)}

    def bridge_status(self) -> dict:
        pid_file = self.bridge_pid_file()
        pid = None
        detected_by = None
        raw_pid = self.host.read_text(pid_file)
        if raw_pid:
            raw_pid = raw_pid.strip()
            if raw_pid.isdigit() and self.host.proc_alive(int(raw_pid)):
                pid = int(raw_pid)
                detected_by = "pid_file"
        if pid is None:
            config = self.bridge_config_state()
            tcp_ports = [p.get("id") and p.get("tcp_port") for p in config["ports"]]
            pid = self.host.pid_connected_to([p for p in tcp_ports if p])
            if pid:
                detected_by = "netstat"
        windows = {"running": bool(pid), "pid": pid, "detected_by": detected_by,
                   "pid_file": pid_file}
        status_path = self.bridge_status_path()
        text = self.host.read_text(status_path)
        wsl = {"running": False, "pid": None, "ports": {}, "error": None,
               "status_file": status_path}
        if text is None:
            wsl["error"] = "WSL 侧状态文件不可读（supervisor 未运行？）"
        else:
            try:
                data = json.loads(text)
            except ValueError as exc:
                wsl["error"] = "状态文件解析失败：%s" % exc
            else:
                wsl.update({
                    "running": True,
                    "pid": data.get("pid"),
                    "started_at": data.get("started_at"),
                    "updated_at": data.get("updated_at"),
                    "ports": data.get("ports") or {},
                    "config_stale": data.get("config_stale"),
                    "stale_hint": data.get("stale_hint"),
                    "link_dir": data.get("link_dir"),
                })
        return {"windows": windows, "wsl": wsl, "config": self.bridge_config_state()}

    def gateway_status(self) -> dict:
        port = int(self.settings["gateway"]["port"])
        listening = self.host.tcp_open("127.0.0.1", port)
        health = None
        health_err = None
        pid = None
        if listening:
            pid = self.host.pid_listening(port)
            health = self.host.http_json("http://127.0.0.1:%d/health" % port, timeout=2.0)
            if health is None:
                health_err = "端口在监听但 /health 无响应"
        return {
            "port": port,
            "running": listening,
            "pid": pid,
            "health": ({"status": health.get("status"), "dll_available": health.get("dll_available")}
                       if isinstance(health, dict) else None),
            "health_error": health_err,
        }

    def diag_status(self) -> dict:
        task = self.settings["diag"]["task"]
        port = int(self.settings["diag"]["port"])
        rc, out = self.host.run(["schtasks", "/Query", "/TN", task, "/FO", "LIST"], timeout=8)
        installed = rc == 0
        state = None
        if installed:
            state = "Running" if ("Running" in out or "正在运行" in out) else "Stopped"
        health = self.host.http_json("http://127.0.0.1:%d/health" % port, timeout=1.5)
        detail = self.host.http_json("http://127.0.0.1:%d/api/state" % port, timeout=1.5) \
            if health else None
        warnings = []
        if health and installed and state != "Running":
            warnings.append("8765 有响应但计划任务未运行（残留实例）")
        return {
            "task": task,
            "task_installed": installed,
            "task_state": state,
            "health_ok": health is not None,
            "health": ({"service": health.get("service"), "version": health.get("version")}
                       if isinstance(health, dict) else None),
            "state": ({"port": detail.get("port"), "port_open": detail.get("port_open"),
                       "logged_in": detail.get("logged_in"), "mode": detail.get("mode"),
                       "start_running": detail.get("start_running"), "error": detail.get("error")}
                      if isinstance(detail, dict) else None),
            "warnings": warnings,
        }

    def workbench_status(self) -> dict:
        port = int(self.settings["workbench"]["port"])
        path = self.settings["workbench"]["health_path"]
        body = self.host.http_json("http://127.0.0.1:%d%s" % (port, path), timeout=1.0)
        return {"port": port, "reachable": body is not None,
                "health": body if isinstance(body, dict) else None}

    # ---- 端口归属 ----
    def ports_view(self, snapshot: dict) -> list[dict]:
        coms = {item["com"]: item["device"] for item in snapshot["host"]["coms"]}
        bridge = snapshot["bridge"]
        wsl_ports = bridge["wsl"].get("ports") or {}
        diag = snapshot["diag"]
        diag_state = diag.get("state") or {}
        diag_com = diag_state.get("port") if diag_state.get("port_open") else None
        out = []
        for item in bridge["config"]["ports"]:
            pid = item["id"]
            runtime = wsl_ports.get(pid) or {}
            com = item.get("windows_com")
            present = com in coms
            client = bool(runtime.get("client_connected"))
            if not item["enabled"]:
                owner = "disabled"
            elif diag_com and com == diag_com:
                owner = "fc-diag"
            elif not present:
                owner = "absent"
            elif client:
                owner = "serial-bridge"
            else:
                owner = "free"
            out.append({
                "id": pid,
                "com": com,
                "device": coms.get(com),
                "com_present": present,
                "label": item.get("label"),
                "usage": item.get("usage"),
                "enabled": item["enabled"],
                "owner": owner,
                "baud": item.get("baudrate"),
                "parity": item.get("parity"),
                "bridge": {
                    "client_connected": client,
                    "control_connected": bool(runtime.get("control_connected")),
                    "desired_baud": runtime.get("desired_baud"),
                    "last_error": runtime.get("last_error") or "",
                },
                "link": runtime.get("link"),
                "pts": runtime.get("pts"),
            })
        for entry in out:
            entry["conflict"] = bool(
                entry["com"] and entry["com"] == diag_com
                and entry["bridge"]["client_connected"])
        return out

    # ---- 快照 ----
    def collect(self) -> dict:
        host = {
            "computer": os.environ.get("COMPUTERNAME") or "",
            "user": os.environ.get("USERNAME") or "",
            "python": sys.executable,
            "distro": self.settings["wsl"]["distro"],
            "repo": self.settings["wsl"]["repo"],
            "coms": self.host.serial_ports(),
            "workbench": self.workbench_status(),
        }
        snapshot = {
            "host": host,
            "bridge": self.bridge_status(),
            "gateway": self.gateway_status(),
            "diag": self.diag_status(),
        }
        snapshot["ports"] = self.ports_view(snapshot)
        return snapshot

    def refresh(self) -> dict:
        with self._refresh_lock:
            snap = self.collect()
            with self._lock:
                self._snapshot = snap
                self._snapshot_at = self.clock()
                self._snapshot_ver += 1
                version = self._snapshot_ver
            with self._cond:
                self._cond.notify_all()
            return self._decorate(snap, version)

    def _decorate(self, snap: dict, version: int) -> dict:
        age = max(0.0, self.clock() - self._snapshot_at)
        data = json.loads(json.dumps(snap, ensure_ascii=False))  # 深拷贝
        data["meta"] = {
            "service": SERVICE,
            "version": VERSION,
            "pid": os.getpid(),
            "started_at": datetime.fromtimestamp(self.started_at).astimezone().isoformat(timespec="seconds"),
            "fetched_at": now_iso(),
            "age_seconds": round(age, 1),
            "stale": age > float(self.settings["status_ttl_seconds"]),
            "version_no": version,
        }
        return data

    def status(self, fresh: bool = False, wait_seconds: float = 0.0,
               since: int | None = None) -> dict:
        ttl = float(self.settings["status_ttl_seconds"])
        stale_limit = float(self.settings["status_stale_seconds"])
        with self._lock:
            snap = self._snapshot
            age = self.clock() - self._snapshot_at if snap else None
            version = self._snapshot_ver
        if fresh or snap is None:
            return self.refresh()
        if age is not None and age <= ttl:
            return self._decorate(snap, version)
        if age is not None and age <= stale_limit:
            # 先回旧快照，后台刷新
            threading.Thread(target=self._safe_refresh, daemon=True).start()
            return self._decorate(snap, version)
        return self.refresh()

    def wait_status(self, since: int, timeout: float) -> dict:
        deadline = self.clock() + max(0.0, min(timeout, 25.0))
        with self._lock:
            version = self._snapshot_ver
        if version != since:
            with self._lock:
                snap = self._snapshot
            return self._decorate(snap, version) if snap else self.refresh()
        threading.Thread(target=self._safe_refresh, daemon=True).start()
        with self._cond:
            if self._snapshot_ver == since:
                self._cond.wait(max(0.1, deadline - self.clock()))
            version = self._snapshot_ver
        with self._lock:
            snap = self._snapshot
        if snap is None:
            return self.refresh()
        return self._decorate(snap, version)

    def invalidate(self) -> None:
        with self._lock:
            self._snapshot_at = 0.0

    def _safe_refresh(self):
        try:
            self.refresh()
        except Exception:  # noqa: BLE001
            logging.exception("后台刷新失败")

    # ---- 服务动作 ----
    def service_start(self, name: str) -> dict:
        try:
            if name == "bridge":
                return self._bridge_start()
            if name == "gateway":
                return self._gateway_start()
            if name == "diag":
                return self._diag_action("start")
            raise HostError("UNKNOWN_SERVICE", "未知服务：%s" % name)
        finally:
            self.invalidate()

    def service_stop(self, name: str) -> dict:
        try:
            if name == "bridge":
                return self._bridge_stop()
            if name == "gateway":
                return self._gateway_stop()
            if name == "diag":
                return self._diag_action("stop")
            raise HostError("UNKNOWN_SERVICE", "未知服务：%s" % name)
        finally:
            self.invalidate()

    def service_restart(self, name: str) -> dict:
        try:
            if name == "diag":
                return self._diag_action("restart")
            if name == "bridge":
                self._bridge_stop()
                return self._bridge_start()
            if name == "gateway":
                self._gateway_stop()
                return self._gateway_start()
            raise HostError("UNKNOWN_SERVICE", "未知服务：%s" % name)
        finally:
            self.invalidate()

    def _bridge_start(self) -> dict:
        with self._action_locks["bridge"]:
            pid = self.bridge_status()["windows"]["pid"]
            if pid:
                return {"started": False, "already_running": True, "windows_pid": pid,
                        "status": self.bridge_status()}
            python = self._windows_python()
            if not python:
                raise HostError("PYTHON_MISSING", "未找到 Windows Python（pythonw.exe/python.exe/py.exe）")
            script = self.bridge_script()
            if self.host.read_text(script) is None:
                raise HostError("BRIDGE_SCRIPT_MISSING", "桥脚本不可读：%s" % script,
                                {"path": script})
            # 确保 WSL supervisor 在跑（失败不阻塞，Windows 桥会每秒重连）
            self.host.run(["wsl.exe", "-d", self.settings["wsl"]["distro"], "-u", "root", "-e",
                           "bash", "-lc",
                           "cd %s && python3 tools/serial_bridge/serial_bridge.py wsl start --config config/serial_bridge.json"
                           % self.settings["wsl"]["repo"]], timeout=20)
            new_pid = self.host.spawn(
                [python, script, "--log", self.bridge_log(), "windows", "--config", self.bridge_config()],
                cwd=HOME, env=os.environ.copy(), log_path=None)
            try:
                self.host.write_text(self.bridge_pid_file(), str(new_pid))
            except OSError:
                pass
            deadline = self.clock() + float(self.settings["bridge"]["start_timeout_seconds"])
            while self.clock() < deadline:
                st = self.bridge_status()
                ports = st["wsl"].get("ports") or {}
                present = {item["com"] for item in self.host.serial_ports()}
                wanted = [p for p in st["config"]["ports"]
                          if p["enabled"] and p["windows_com"] in present]
                if wanted and all((ports.get(p["id"]) or {}).get("client_connected") for p in wanted):
                    return {"started": True, "windows_pid": new_pid, "status": st}
                if not self.host.proc_alive(new_pid):
                    break
                time.sleep(0.5)
            st = self.bridge_status()
            missing = [p["id"] for p in st["config"]["ports"]
                       if p["enabled"] and p["windows_com"] not in present]
            return {"started": True, "windows_pid": new_pid, "status": st,
                    "warning": ("部分启用端口未连上（COM 不在系统里：%s）" % ", ".join(missing))
                               if missing else None}

    def _bridge_stop(self) -> dict:
        with self._action_locks["bridge"]:
            pid = self.bridge_status()["windows"]["pid"]
            if not pid:
                try:
                    os.remove(self.bridge_pid_file())
                except OSError:
                    pass
                return {"stopped": False, "already_stopped": True, "status": self.bridge_status()}
            ok = self.host.terminate(pid)
            try:
                os.remove(self.bridge_pid_file())
            except OSError:
                pass
            if not ok:
                raise HostError("BRIDGE_STOP_FAILED", "Windows 桥 PID %s 停止失败" % pid,
                                {"pid": pid})
            return {"stopped": True, "windows_pid": pid, "status": self.bridge_status()}

    def _bridge_pid(self) -> int | None:
        raw = self.host.read_text(self.bridge_pid_file())
        if not raw:
            return None
        raw = raw.strip()
        return int(raw) if raw.isdigit() else None

    def _wsl_bridge(self, action: str) -> None:
        """在 WSL 侧启停串口桥 supervisor（stop 会断开 PTY 上已有的应用会话）。"""
        self.host.run(["wsl.exe", "-d", self.settings["wsl"]["distro"], "-u", "root", "-e",
                       "bash", "-lc",
                       "cd %s && python3 tools/serial_bridge/serial_bridge.py wsl %s --config config/serial_bridge.json"
                       % (self.settings["wsl"]["repo"], action)], timeout=25)

    def _windows_python(self) -> str | None:
        if not self.host.is_windows:
            return sys.executable
        for name in ("pythonw.exe", "python.exe", "py.exe"):
            rc, out = self.host.run(["where", name], timeout=5)
            if rc == 0:
                first = out.strip().splitlines()[0].strip() if out.strip() else ""
                if first:
                    return first
        return None

    def _gateway_start(self) -> dict:
        with self._action_locks["gateway"]:
            if self.gateway_status()["running"]:
                return {"started": False, "already_running": True, "status": self.gateway_status()}
            cfg = self.settings["gateway"]
            build_plain = expand(cfg["build_plain"])
            python = expand(cfg["python"])
            if not os.path.isdir(build_plain):
                raise HostError("GATEWAY_BUILD_PLAIN_MISSING",
                                "解析网关明文区不存在：%s" % build_plain, {"path": build_plain})
            if not os.path.isfile(python):
                raise HostError("GATEWAY_PYTHON_MISSING", "解析网关 Python 不存在：%s" % python,
                                {"path": python})
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join([os.path.join(build_plain, "apps"),
                                                 os.path.join(build_plain, "libs")])
            log = os.path.join(build_plain, "parse_svc.out.log")
            pid = self.host.spawn([python, "-m", cfg["module"]], cwd=build_plain, env=env,
                                  log_path=log)
            deadline = self.clock() + float(cfg["start_timeout_seconds"])
            while self.clock() < deadline:
                health = self.host.http_json("http://127.0.0.1:%d/health" % int(cfg["port"]),
                                             timeout=1.5)
                if isinstance(health, dict) and health.get("status") == "ok":
                    return {"started": True, "pid": pid, "status": self.gateway_status()}
                time.sleep(1.0)
            raise HostError("GATEWAY_NOT_READY",
                            "解析网关 %d 秒内未就绪，见 %s"
                            % (cfg["start_timeout_seconds"],
                               os.path.join(build_plain, "parse_svc.err.log")),
                            {"log": log})

    def _gateway_stop(self) -> dict:
        with self._action_locks["gateway"]:
            st = self.gateway_status()
            if not st["running"]:
                return {"stopped": False, "already_stopped": True, "status": st}
            pid = st.get("pid")
            if not pid:
                raise HostError("GATEWAY_PID_UNKNOWN", "8700 在监听但查不到 PID，请手动结束")
            ok = self.host.terminate(pid)
            if not ok:
                raise HostError("GATEWAY_STOP_FAILED", "解析网关 PID %s 停止失败" % pid)
            return {"stopped": True, "pid": pid, "status": self.gateway_status()}

    def _diag_action(self, action: str) -> dict:
        with self._action_locks["diag"]:
            task = self.settings["diag"]["task"]
            if action == "start":
                rc, out = self.host.run(["schtasks", "/Run", "/TN", task], timeout=10)
            elif action == "stop":
                rc, out = self.host.run(["schtasks", "/End", "/TN", task], timeout=10)
            else:
                self.host.run(["schtasks", "/End", "/TN", task], timeout=10)
                time.sleep(1)
                rc, out = self.host.run(["schtasks", "/Run", "/TN", task], timeout=10)
            if rc != 0:
                raise HostError("DIAG_TASK_FAILED", "schtasks %s 失败：%s" % (action, out.strip()),
                                {"task": task})
            deadline = self.clock() + float(self.settings["diag"]["start_timeout_seconds"])
            port = int(self.settings["diag"]["port"])
            while self.clock() < deadline:
                if action == "stop":
                    if not self.host.tcp_open("127.0.0.1", port, timeout=0.3):
                        return {"action": action, "status": self.diag_status()}
                else:
                    if self.host.http_json("http://127.0.0.1:%d/health" % port, timeout=1.0):
                        return {"action": action, "status": self.diag_status()}
                time.sleep(1.0)
            if action == "stop":
                raise HostError("DIAG_STOP_TIMEOUT", "诊断服务停止后 %d 仍在响应" % port)
            raise HostError("DIAG_NOT_READY", "诊断服务 %d 秒内 8765 未就绪"
                            % self.settings["diag"]["start_timeout_seconds"])

    # ---- 逐口开关 ----
    def _restart_bridge_chain(self) -> dict:
        """配置变更后的统一重启链：停 Windows 桥（释放 COM）→ 重启 WSL supervisor
        （重读配置）→ 起桥。"""
        try:
            self._bridge_stop()
        except HostError as exc:
            logging.warning("停桥失败，继续重启 supervisor：%s", exc.message)
        self._wsl_bridge("stop")
        time.sleep(1)
        self._wsl_bridge("start")
        return self._bridge_start()

    def set_port_enabled(self, port_id: str, enabled: bool, restart: bool = True) -> dict:
        with self._action_locks["config"]:
            config = self.bridge_config_state()
            if config["error"]:
                raise HostError("CONFIG_UNREADABLE", config["error"], {"path": config["path"]})
            target = [p for p in config["ports"] if p["id"] == port_id]
            if not target:
                raise HostError("PORT_NOT_FOUND", "桥配置里没有端口：%s" % port_id,
                                {"ports": [p["id"] for p in config["ports"]]})
            before = target[0]["enabled"]
            if before == bool(enabled):
                return {"changed": False, "port": port_id, "enabled": before,
                        "restarted": False, "status": self.bridge_status()}
            path = self.bridge_config()
            text = self.host.read_text(path)
            try:
                json.loads(text)
            except (TypeError, ValueError) as exc:
                raise HostError("CONFIG_UNREADABLE", "配置 JSON 解析失败：%s" % exc) from exc
            backup = path + ".bak-hostcontrol"
            try:
                self.host.write_text(backup, text)
            except OSError:
                pass
            # 只改目标端口那一处 enabled，保留原文件排版（避免整文件重排污染 diff）
            pattern = re.compile(
                r'("id"\s*:\s*"%s".*?"enabled"\s*:\s*)(true|false)' % re.escape(port_id),
                re.S)
            new_text, hit = pattern.subn(
                lambda m: m.group(1) + ("true" if enabled else "false"), text, count=1)
            if not hit:
                raise HostError("CONFIG_UNWRITABLE", "配置里定位不到端口 %s 的 enabled 字段" % port_id,
                                {"path": path})
            self.host.write_text(path, new_text)
            result = {"changed": True, "port": port_id, "enabled": bool(enabled),
                      "restarted": False, "backup": backup}
            if restart:
                self._restart_bridge_chain()
                result["restarted"] = True
            self.invalidate()
            result["status"] = self.bridge_status()
            return result

    # ---- 端口映射增改（配置真相源：WSL 侧 serial_bridge.json，经 UNC 读写） ----
    def _load_config_json(self) -> tuple[dict, str, str]:
        """读桥配置 → (cfg, 原始文本, 路径)。结构化增改走这里，不再用正则定点改。"""
        path = self.bridge_config()
        text = self.host.read_text(path)
        if text is None:
            raise HostError("CONFIG_UNREADABLE", "桥配置不可读（WSL 未运行或路径不对）",
                            {"path": path})
        try:
            cfg = json.loads(text)
        except ValueError as exc:
            raise HostError("CONFIG_PARSE", "桥配置 JSON 解析失败：%s" % exc,
                            {"path": path}) from exc
        if not isinstance(cfg.get("ports"), list):
            raise HostError("CONFIG_PARSE", "桥配置缺少 ports 数组", {"path": path})
        return cfg, text, path

    def _save_config_json(self, cfg: dict, original_text: str, path: str) -> str:
        """保持 key 顺序 + indent=2 + 中文不转义写回；先备份原文本。返回备份路径。"""
        backup = path + ".bak-hostcontrol"
        try:
            self.host.write_text(backup, original_text)
        except OSError:
            pass
        self.host.write_text(path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        return backup

    @staticmethod
    def _norm_com(value) -> str:
        com = str(value or "").strip().upper()
        if not _COM_PATTERN.match(com):
            raise HostError("VALIDATION", "windows_com 必须形如 COM12：%r" % value,
                            {"field": "windows_com"})
        return com

    @staticmethod
    def _norm_int(value, field: str, minimum: int = 1, maximum: int = 2 ** 31) -> int:
        try:
            num = int(value)
        except (TypeError, ValueError):
            raise HostError("VALIDATION", "%s 必须是整数：%r" % (field, value),
                            {"field": field}) from None
        if not (minimum <= num <= maximum):
            raise HostError("VALIDATION", "%s 超出范围 %d~%d：%d" % (field, minimum, maximum, num),
                            {"field": field})
        return num

    @staticmethod
    def _ensure_com_free(cfg: dict, com: str, exclude_id: str | None = None) -> None:
        for p in cfg["ports"]:
            if p.get("id") == exclude_id:
                continue
            if str(p.get("windows_com") or "").upper() == com:
                raise HostError("COM_IN_USE",
                                "%s 已被端口 %s（%s）映射" % (com, p.get("id"), p.get("label") or ""),
                                {"com": com, "port": p.get("id")})

    @staticmethod
    def _ensure_tcp_free(cfg: dict, field: str, num: int, exclude_id: str | None = None) -> None:
        for p in cfg["ports"]:
            if p.get("id") == exclude_id:
                continue
            if p.get(field) == num:
                raise HostError("TCP_PORT_IN_USE",
                                "%s %d 已被端口 %s 占用" % (field, num, p.get("id")),
                                {"field": field, "value": num, "port": p.get("id")})

    @staticmethod
    def _alloc_tcp(cfg: dict, field: str, rng) -> int:
        used = {p.get(field) for p in cfg["ports"]}
        for num in rng:
            if num not in used:
                return num
        raise HostError("VALIDATION", "%s 自动分配失败（%d~%d 已用完）" % (field, rng[0], rng[-1]),
                        {"field": field})

    def _norm_tcp_or_alloc(self, cfg: dict, field: str, value, rng) -> int:
        """给了就校验范围且不冲突；没给就在 rng 里挑空闲号。"""
        if value in (None, ""):
            return self._alloc_tcp(cfg, field, rng)
        num = self._norm_int(value, field, 1024, 65535)
        self._ensure_tcp_free(cfg, field, num)
        return num

    def create_port(self, body: dict, restart: bool = True) -> dict:
        """添加端口映射。tcp/control 端口缺省时自动挑空闲号。"""
        with self._action_locks["config"]:
            cfg, text, path = self._load_config_json()
            port_id = str(body.get("id") or "").strip()
            if not _ID_PATTERN.match(port_id):
                raise HostError("VALIDATION",
                                "id 需以小写字母开头，仅含小写字母/数字/连字符，2~32 字符：%r" % port_id,
                                {"field": "id"})
            if any(p.get("id") == port_id for p in cfg["ports"]):
                raise HostError("PORT_EXISTS", "端口 id 已存在：%s" % port_id,
                                {"existing": [p.get("id") for p in cfg["ports"]]})
            com = self._norm_com(body.get("windows_com"))
            self._ensure_com_free(cfg, com)
            item = {
                "id": port_id,
                "label": str(body.get("label") or "").strip() or port_id,
                "windows_com": com,
                "tcp_port": self._norm_tcp_or_alloc(cfg, "tcp_port", body.get("tcp_port"), _TCP_RANGE),
                "control_port": self._norm_tcp_or_alloc(cfg, "control_port",
                                                        body.get("control_port"), _CTL_RANGE),
                "baudrate": self._norm_int(body.get("baudrate", 115200), "baudrate", 50, 12_000_000),
                "bytesize": self._norm_int(body.get("bytesize", 8), "bytesize", 5, 8),
                "parity": _norm_parity(body.get("parity", "N")),
                "stopbits": self._norm_int(body.get("stopbits", 1), "stopbits", 1, 3),
                "enabled": bool(body.get("enabled", True)),
            }
            usage = str(body.get("usage") or "").strip()
            if usage:
                item["usage"] = usage
            cfg["ports"].append(item)
            backup = self._save_config_json(cfg, text, path)
            result = {"changed": True, "created": item, "backup": backup, "restarted": False}
            if restart:
                self._restart_bridge_chain()
                result["restarted"] = True
            self.invalidate()
            return result

    def update_port(self, port_id: str, body: dict, restart: bool = True) -> dict:
        """编辑端口映射（label/usage/COM/波特率/校验位/TCP 端口等）。
        仅 label/usage 变化时不重启桥；COM 被其他端口占用时拒绝。"""
        with self._action_locks["config"]:
            cfg, text, path = self._load_config_json()
            target = next((p for p in cfg["ports"] if p.get("id") == port_id), None)
            if target is None:
                raise HostError("PORT_NOT_FOUND", "桥配置里没有端口：%s" % port_id,
                                {"ports": [p.get("id") for p in cfg["ports"]]})
            unknown = [k for k in body if k not in _PORT_FIELDS_EDITABLE and k != "restart"]
            if unknown:
                raise HostError("VALIDATION", "不支持的字段：%s" % ", ".join(sorted(unknown)),
                                {"editable": list(_PORT_FIELDS_EDITABLE)})
            changes = {}
            if "label" in body:
                label = str(body["label"] or "").strip()
                if not label:
                    raise HostError("VALIDATION", "label 不能为空", {"field": "label"})
                changes["label"] = label
            if "usage" in body:
                changes["usage"] = str(body["usage"] or "").strip()
            if "windows_com" in body:
                com = self._norm_com(body["windows_com"])
                self._ensure_com_free(cfg, com, exclude_id=port_id)
                changes["windows_com"] = com
            if "parity" in body:
                changes["parity"] = _norm_parity(body["parity"])
            for field in ("baudrate", "bytesize", "stopbits"):
                if field in body:
                    hi = 12_000_000 if field == "baudrate" else 8
                    lo = 50 if field == "baudrate" else (1 if field == "stopbits" else 5)
                    changes[field] = self._norm_int(body[field], field, lo, hi)
            for field in ("tcp_port", "control_port"):
                if field in body:
                    num = self._norm_int(body[field], field, 1024, 65535)
                    if num != target.get(field):
                        self._ensure_tcp_free(cfg, field, num, exclude_id=port_id)
                    changes[field] = num
            # 只保留真变化，避免无意义写回
            changes = {k: v for k, v in changes.items() if target.get(k) != v}
            if not changes:
                return {"changed": False, "port": port_id, "changes": {},
                        "restarted": False, "status": self.bridge_status()}
            target.update(changes)
            backup = self._save_config_json(cfg, text, path)
            result = {"changed": True, "port": port_id, "changes": changes,
                      "backup": backup, "restarted": False}
            structural = any(k not in _LABEL_ONLY_FIELDS for k in changes)
            if restart and structural:
                self._restart_bridge_chain()
                result["restarted"] = True
            elif restart and not structural:
                result["restart_skipped"] = "仅名称/用途变化，无需重启桥"
            self.invalidate()
            result["status"] = self.bridge_status()
            return result

    def batch_ports(self, action: str, ids: list | None = None, restart: bool = True) -> dict:
        """一键释放/接管：一次写回所有 enabled，只重启一次桥。"""
        if action not in ("enable", "disable"):
            raise HostError("VALIDATION", "action 必须是 enable 或 disable：%r" % action,
                            {"field": "action"})
        want = action == "enable"
        with self._action_locks["config"]:
            cfg, text, path = self._load_config_json()
            targets = cfg["ports"]
            if ids is not None:
                idset = set(ids)
                known = {p.get("id") for p in cfg["ports"]}
                unknown = sorted(idset - known)
                if unknown:
                    raise HostError("PORT_NOT_FOUND", "配置里没有端口：%s" % ", ".join(unknown),
                                    {"ports": sorted(k for k in known if k)})
                targets = [p for p in cfg["ports"] if p.get("id") in idset]
            changed, unchanged = [], []
            for p in targets:
                current = p.get("enabled", True) is not False
                if current == want:
                    unchanged.append(p.get("id"))
                else:
                    p["enabled"] = want
                    changed.append(p.get("id"))
            result = {"action": action, "changed": changed, "unchanged": unchanged,
                      "restarted": False}
            if changed:
                result["backup"] = self._save_config_json(cfg, text, path)
                if restart:
                    self._restart_bridge_chain()
                    result["restarted"] = True
            self.invalidate()
            return result

    def coms_view(self, fresh: bool = False) -> dict:
        """系统串口扫描 + 映射/占用标记（供添加/编辑弹窗的 COM 下拉）。"""
        cfg_state = self.bridge_config_state()
        mapped = {str(p.get("windows_com") or "").upper(): p
                  for p in cfg_state.get("ports", []) if p.get("windows_com")}
        snap = self.status()  # 走 TTL 缓存，用于诊断服务占用判定
        diag_state = snap["diag"].get("state") or {}
        diag_com = str(diag_state.get("port") or "").upper() if diag_state.get("port_open") else None
        if fresh:
            coms = self.host.serial_ports()
            cached = False
            scanned_at = now_iso()
        else:
            coms = snap["host"]["coms"]
            cached = True
            scanned_at = (snap["meta"] or {}).get("fetched_at")
        out = []
        for item in coms:
            com = str(item.get("com") or "").upper()
            owner = mapped.get(com)
            out.append({
                "com": com,
                "device": item.get("device"),
                "mapped_by": owner.get("id") if owner else None,
                "free": owner is None,
                "diag_used": bool(diag_com and com == diag_com),
            })
        result = {"cached": cached, "scanned_at": scanned_at, "coms": out,
                  "config_error": cfg_state.get("error")}
        if not out:
            result["warning"] = "未扫描到系统串口（pyserial/注册表均无结果）"
        return result

    # ---- 自检 ----
    def doctor(self) -> dict:
        snap = self.refresh()
        checks = []

        def add(cid, level, title, detail="", fix=""):
            checks.append({"id": cid, "level": level, "title": title,
                           "detail": detail, "fix": fix})

        token_ok = bool(self.token) and os.path.exists(self.token_path)
        add("token", "ok" if token_ok else "warn",
            "访问令牌", "文件：%s" % self.token_path if token_ok else "令牌文件不存在",
            "重启本服务会自动生成")

        config = snap["bridge"]["config"]
        if config["error"]:
            add("bridge_config", "error", "串口桥配置不可读", config["error"],
                "确认 WSL 发行版/仓库路径（settings.json → wsl）")
        else:
            ids = [p["id"] for p in config["ports"]]
            dup = sorted({i for i in ids if ids.count(i) > 1})
            add("bridge_config", "ok" if not dup else "error",
                "串口桥配置", "%d 个端口映射" % len(ids),
                "重复端口 id：%s" % ", ".join(dup) if dup else "")
            coms = [p["windows_com"] for p in config["ports"] if p["enabled"]]
            dup_com = sorted({c for c in coms if coms.count(c) > 1})
            if dup_com:
                add("bridge_com_unique", "error", "COM 号重复",
                    "多个启用端口指向同一 COM：%s" % ", ".join(dup_com),
                    "改 config/serial_bridge.json")
            present = {item["com"] for item in snap["host"]["coms"]}
            missing = [(p["id"], p["windows_com"]) for p in config["ports"]
                       if p["enabled"] and p["windows_com"] not in present]
            add("bridge_coms", "ok" if not missing else "warn",
                "启用端口的 COM 是否在系统里",
                "全部在位" if not missing else
                "缺失：%s" % ", ".join("%s(%s)" % (i, c) for i, c in missing),
                "检查接线/驱动；历史 usbipd attach 也会让 COM 消失")

        bridge = snap["bridge"]
        add("bridge_wsl", "ok" if bridge["wsl"]["running"] else "error",
            "WSL 串口桥 supervisor",
            "运行中（pid %s）" % bridge["wsl"].get("pid") if bridge["wsl"]["running"]
            else (bridge["wsl"].get("error") or "未运行"),
            "POST /api/v1/services/bridge/start")
        if bridge["wsl"].get("config_stale"):
            add("bridge_config_stale", "warn", "桥仍在用旧配置",
                bridge["wsl"].get("stale_hint") or "",
                "POST /api/v1/services/bridge/restart")
        add("bridge_windows", "ok" if bridge["windows"]["running"] else "warn",
            "Windows 桥进程",
            "运行中（pid %s）" % bridge["windows"]["pid"] if bridge["windows"]["running"]
            else "未运行：COM 现在归 Windows 工具所有",
            "POST /api/v1/services/bridge/start")

        if bridge["windows"]["running"] and bridge["wsl"]["running"]:
            ports = bridge["wsl"].get("ports") or {}
            down = []
            for item in config["ports"]:
                if not item["enabled"]:
                    continue
                runtime = ports.get(item["id"]) or {}
                if not runtime.get("client_connected"):
                    down.append("%s(%s)" % (item["id"], item["windows_com"]))
            add("bridge_channels", "ok" if not down else "warn",
                "数据通道",
                "全部连通" if not down else "未连通：%s" % ", ".join(down),
                "COM 被占用/不存在时会出现；看 ports[].bridge.last_error")

        gw = snap["gateway"]
        add("gateway", "ok" if gw["running"] else "info",
            "解析网关 8700",
            "运行中 pid %s，dll_available=%s" % (gw["pid"], (gw["health"] or {}).get("dll_available"))
            if gw["running"] else "未运行（WSL 深度解析会降级为 none）",
            "POST /api/v1/services/gateway/start")

        diag = snap["diag"]
        add("diag_task", "ok" if diag["task_installed"] and diag["task_state"] == "Running"
            else ("warn" if diag["task_installed"] else "error"),
            "诊断服务计划任务",
            "%s：%s" % (diag["task"], diag["task_state"] or "未安装"),
            "POST /api/v1/services/diag/start")
        add("diag_health", "ok" if diag["health_ok"] else "warn",
            "诊断服务 8765",
            "已响应，版本 %s" % (diag["health"] or {}).get("version") if diag["health_ok"]
            else "无响应（hs-dw31 不可用）",
            "POST /api/v1/services/diag/start")

        wb = snap["host"]["workbench"]
        add("workbench", "ok" if wb["reachable"] else "info",
            "工作台 8790",
            "可达" if wb["reachable"] else "不可达（在线类技能需要它；在 WSL 侧启动）",
            "WSL: python3 <WORKBENCH_ROOT>/apps/workbench/run.py")

        conflicts = [p for p in snap["ports"] if p.get("conflict")]
        if conflicts:
            add("port_conflict", "error", "COM 被两个域同时持有",
                ", ".join("%s(%s)" % (p["id"], p["com"]) for p in conflicts),
                "POST /api/v1/ports/{id}/disable 释放其一")

        worst = "ok"
        for level in ("error", "warn", "info"):
            if any(c["level"] == level for c in checks):
                worst = level
                break
        return {"generated_at": now_iso(), "verdict": worst, "checks": checks,
                "snapshot_version": snap["meta"]["version_no"]}

    # ---- 日志 ----
    def log_tail(self, source: str, lines: int) -> dict:
        lines = max(1, min(int(lines), int(self.settings["log_lines_max"])))
        gw_dir = expand(self.settings["gateway"]["build_plain"])
        mapping = {
            "bridge": self.bridge_log(),
            "gateway-out": os.path.join(gw_dir, "parse_svc.out.log"),
            "gateway-err": os.path.join(gw_dir, "parse_svc.err.log"),
            "service": self.log_path,
            "audit": self.audit_path,
        }
        if source not in mapping:
            raise HostError("UNKNOWN_LOG", "未知日志源：%s" % source,
                            {"sources": sorted(mapping)})
        path = mapping[source]
        text = self.host.read_text(path)
        if text is None:
            raise HostError("LOG_MISSING", "日志不存在：%s" % path, {"file": path})
        return {"source": source, "file": path, "lines": text.splitlines()[-lines:]}

    # ---- 能力清单 ----
    def caps(self) -> dict:
        return {
            "service": SERVICE,
            "version": VERSION,
            "pid": os.getpid(),
            "started_at": datetime.fromtimestamp(self.started_at).astimezone().isoformat(timespec="seconds"),
            "token_file": self.token_path,
            "require_token": bool(self.settings["require_token"]),
            "settings_file": SETTINGS_PATH,
            "domains": {
                "bridge": {"service": "WSL 串口桥（Windows 半边）",
                           "actions": ["start", "stop", "restart"],
                           "config": self.bridge_config()},
                "gateway": {"service": "解析网关 8700",
                            "actions": ["start", "stop", "restart"]},
                "diag": {"service": "FC 诊断服务 8765",
                         "actions": ["start", "stop", "restart"]},
            },
            "ports": {"actions": ["enable", "disable", "create", "update", "batch"],
                      "note": "逐口开关/增改会改 config/serial_bridge.json 并按需重启 Windows 桥；"
                              "一键释放/接管只重启一次桥"},
            "removed": {"/api/usbipd/*": "2026-11-17 移除；串口统一走 Python 桥"},
            "endpoints": [
                "GET /health", "GET /api/v1/caps", "GET /api/v1/status",
                "GET /api/v1/ports", "POST /api/v1/ports/{id}/{enable|disable}",
                "POST /api/v1/ports", "PATCH /api/v1/ports/{id}",
                "POST /api/v1/ports/batch {action: enable|disable, ids?}",
                "GET /api/v1/coms?fresh=1",
                "GET /api/v1/services", "POST /api/v1/services/{name}/{start|stop|restart}",
                "POST /api/v1/doctor", "GET /api/v1/logs?source=...&lines=N",
                "GET /api/v1/audit?lines=N",
            ],
        }


# ---------------------------------------------------------------- HTTP 层


def _client_ip(handler) -> str:
    try:
        return handler.client_address[0]
    except (AttributeError, IndexError):
        return "?"


_STATUS_BY_CODE = {
    "NOT_FOUND": 404,
    "LOG_MISSING": 404,
    "METHOD_NOT_ALLOWED": 405,
    "UNAUTHORIZED": 401,
    "GONE": 410,
    "UNKNOWN_SERVICE": 404,
    "PORT_NOT_FOUND": 404,
    "UNKNOWN_LOG": 404,
    "BAD_REQUEST": 400,
    "VALIDATION": 400,
    "CONFIG_PARSE": 400,
    "PORT_EXISTS": 409,
    "COM_IN_USE": 409,
    "TCP_PORT_IN_USE": 409,
}


def _http_status_for(code: str) -> int:
    return _STATUS_BY_CODE.get(code, 409)


def _is_loopback(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1", "localhost") or ip.startswith("127.")


class ControlHandler(BaseHTTPRequestHandler):
    server_version = "WslControl/%s" % VERSION
    protocol_version = "HTTP/1.1"
    agent: HostAgent = None  # 由 main() 注入

    # ---------- 基础 ----------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Host-Token, Authorization")

    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _ok(self, data, **extra) -> None:
        payload = {"ok": True, "service": SERVICE, "version": VERSION,
                   "ts": now_iso(), "data": data}
        payload.update(extra)
        self._send(200, payload)

    def _err(self, code: int, err: HostError | None = None, message: str = "",
             detail=None) -> None:
        payload = {
            "ok": False,
            "service": SERVICE,
            "version": VERSION,
            "ts": now_iso(),
            "error": {
                "code": (err.code if err else "ERROR"),
                "message": (err.message if err else message),
                "detail": (err.detail if err else (detail or {})),
            },
        }
        self._send(code, payload)

    def log_message(self, fmt, *args):  # noqa: A003
        logging.info("%s %s", _client_ip(self), fmt % args)

    def _query(self) -> dict:
        return {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items() if v}

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not length:
            return {}
        raw = self.rfile.read(min(length, 1 << 20))
        try:
            return json.loads(raw.decode("utf-8")) or {}
        except (UnicodeDecodeError, ValueError):
            return {}

    def _auth(self) -> bool:
        agent = self.agent
        if not agent.settings.get("require_token"):
            return True
        if _is_loopback(_client_ip(self)):
            return True
        header = self.headers.get("X-Host-Token") or ""
        if not header:
            auth = self.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                header = auth[7:].strip()
        if not header:
            header = self._query().get("token") or ""
        return bool(header) and hmac.compare_digest(header, agent.token)

    def _require_auth(self) -> bool:
        if self._auth():
            return True
        self._err(401, HostError("UNAUTHORIZED", "非回环请求需要 X-Host-Token",
                                 {"token_file": self.agent.token_path,
                                  "hint": "WSL 可读 /mnt/c/Users/<user>/.wsl-control/token"}))
        return False

    # ---------- 路由 ----------
    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def do_PATCH(self):  # noqa: N802
        self._route("PATCH")

    def _route(self, method: str) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/health":
                self._ok({"status": "ok", "config_ok": os.path.exists(SETTINGS_PATH)})
                return
            if path in ("/", "/index.html", "/dashboard"):
                self._serve_dashboard()
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self._cors()
                self.end_headers()
                return
            if not self._require_auth():
                return
            if path.startswith("/api/usbipd"):
                self._err(410, HostError(
                    "GONE", "usbipd 接口已于 2026-11-17 移除",
                    {"hint": "串口统一走 Python 桥：POST /api/v1/services/bridge/start",
                     "ports": "GET /api/v1/ports"}))
                return
            if path in ("/api/v1/caps",):
                self._ok(self.agent.caps())
                return
            if path in ("/api/v1/status", "/api/status"):
                self._handle_status()
                return
            if path == "/api/v1/ports":
                if method == "POST":
                    self._handle_ports_create()
                else:
                    snap = self.agent.status()
                    self._ok({"ports": snap["ports"], "meta": snap["meta"]})
                return
            if path == "/api/v1/ports/batch":
                self._handle_ports_batch(method)
                return
            if path == "/api/v1/coms":
                q = self._query()
                self._ok(self.agent.coms_view(fresh=q.get("fresh") in ("1", "true", "yes")))
                return
            if path.startswith("/api/v1/ports/"):
                self._handle_port(path, method)
                return
            if path == "/api/v1/services":
                snap = self.agent.status()
                self._ok({"bridge": {"windows": snap["bridge"]["windows"],
                                     "wsl": {"running": snap["bridge"]["wsl"]["running"],
                                             "pid": snap["bridge"]["wsl"].get("pid")}},
                          "gateway": snap["gateway"], "diag": snap["diag"]})
                return
            if path.startswith("/api/v1/services/"):
                self._handle_service(path, method)
                return
            if path == "/api/v1/doctor":
                self._handle_doctor(method)
                return
            if path == "/api/v1/logs" or path in ("/api/bridge/log", "/api/gateway/log"):
                self._handle_logs(path)
                return
            if path == "/api/v1/audit":
                q = self._query()
                lines = int(q.get("lines", 50))
                self._ok({"lines": self.agent.audit_tail(lines)})
                return
            # v1 兼容别名
            legacy = {
                "/api/bridge/status": ("GET", lambda: self.agent.status()["bridge"]),
                "/api/diag/status": ("GET", lambda: self.agent.status()["diag"]),
                "/api/bridge/toggle": ("POST", self._bridge_toggle),
                "/api/bridge/start": ("POST", lambda: self._service_call("bridge", "start")),
                "/api/bridge/stop": ("POST", lambda: self._service_call("bridge", "stop")),
                "/api/bridge/restart": ("POST", lambda: self._service_call("bridge", "restart")),
                "/api/gateway/start": ("POST", lambda: self._service_call("gateway", "start")),
                "/api/gateway/stop": ("POST", lambda: self._service_call("gateway", "stop")),
                "/api/diag/start": ("POST", lambda: self._service_call("diag", "start")),
                "/api/diag/stop": ("POST", lambda: self._service_call("diag", "stop")),
                "/api/diag/restart": ("POST", lambda: self._service_call("diag", "restart")),
            }
            if path in legacy:
                expected, fn = legacy[path]
                if method != expected:
                    self._err(405, HostError("METHOD_NOT_ALLOWED",
                                             "%s 只支持 %s" % (path, expected)))
                    return
                self._ok(fn())
                return
            self._err(404, HostError("NOT_FOUND", "路径不存在：%s" % path,
                                     {"caps": "/api/v1/caps"}))
        except HostError as exc:
            logging.warning("业务失败 %s %s: %s", method, path, exc.message)
            self._err(_http_status_for(exc.code), exc)
        except Exception as exc:  # noqa: BLE001
            logging.exception("处理异常 %s %s", method, path)
            self._err(500, HostError("INTERNAL", "服务内部异常：%s" % exc))

    def _serve_dashboard(self):
        page_path = os.path.join(SCRIPT_DIR, "dashboard.html")
        try:
            with open(page_path, "r", encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            body = ("<!doctype html><meta charset=utf-8><body style='background:#0f1216;color:#e6edf3;font-family:sans-serif'>"
                    "<h3>dashboard.html 缺失</h3><p>请把 dashboard.html 与 wsl-control.py 放在同一目录。</p>"
                    "<p>接口：<code>/api/v1/caps</code> <code>/api/v1/status</code></p>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # 本机回环访问直接把令牌注进页面；从 WSL/局域网访问时由页面自己提示粘贴
        if _is_loopback(_client_ip(self)):
            inject = ('<script>window.__HOST_TOKEN__=%s;</script>'
                      % json.dumps(self.agent.token))
            html = html.replace("</head>", inject + "\n</head>", 1)
        html = html.replace("__SERVICE_VERSION__", VERSION)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------- 具体处理 ----------
    def _handle_status(self):
        q = self._query()
        fresh = q.get("fresh") in ("1", "true", "yes")
        try:
            wait = float(q.get("wait", 0))
        except ValueError:
            wait = 0.0
        since = q.get("since")
        if wait > 0 and since is not None:
            try:
                snap = self.agent.wait_status(int(since), wait)
            except ValueError:
                snap = self.agent.status(fresh=fresh)
        else:
            snap = self.agent.status(fresh=fresh)
        self._ok(snap)

    def _handle_port(self, path: str, method: str):
        parts = [p for p in path.split("/") if p]
        # PATCH /api/v1/ports/{id} —— 编辑端口映射
        if len(parts) == 4 and method == "PATCH":
            self._handle_port_update(parts[3])
            return
        # POST /api/v1/ports/{id}/{action}
        if len(parts) != 5 or parts[4] not in ("enable", "disable"):
            self._err(404, HostError("NOT_FOUND",
                                     "用法：/api/v1/ports/{id}/enable|disable 或 PATCH /api/v1/ports/{id}"))
            return
        if method != "POST":
            self._err(405, HostError("METHOD_NOT_ALLOWED", "端口开关请用 POST"))
            return
        port_id, action = parts[3], parts[4]
        body = self._body()
        restart = body.get("restart", True) not in (False, 0, "0", "false")
        try:
            result = self.agent.set_port_enabled(port_id, action == "enable", restart=restart)
        except HostError as exc:
            self.agent.audit("port.%s" % action, port_id, _client_ip(self), False, exc.message)
            raise
        self.agent.audit("port.%s" % action, port_id, _client_ip(self), True,
                         detail={"restarted": result.get("restarted")})
        self._ok(result)

    @staticmethod
    def _restart_flag(body: dict) -> bool:
        return body.get("restart", True) not in (False, 0, "0", "false")

    def _handle_ports_create(self):
        body = self._body()
        try:
            result = self.agent.create_port(body, restart=self._restart_flag(body))
        except HostError as exc:
            self.agent.audit("port.create", str(body.get("id") or "?"), _client_ip(self),
                             False, exc.message)
            raise
        created = result.get("created") or {}
        self.agent.audit("port.create", created.get("id") or "?", _client_ip(self), True,
                         detail={"com": created.get("windows_com"),
                                 "restarted": result.get("restarted")})
        self._ok(result)

    def _handle_port_update(self, port_id: str):
        body = self._body()
        try:
            result = self.agent.update_port(port_id, body, restart=self._restart_flag(body))
        except HostError as exc:
            self.agent.audit("port.update", port_id, _client_ip(self), False, exc.message)
            raise
        self.agent.audit("port.update", port_id, _client_ip(self), True,
                         detail={"changes": sorted(result.get("changes") or {}),
                                 "restarted": result.get("restarted")})
        self._ok(result)

    def _handle_ports_batch(self, method: str):
        if method != "POST":
            self._err(405, HostError("METHOD_NOT_ALLOWED", "一键释放/接管请用 POST"))
            return
        body = self._body()
        action = str(body.get("action") or "")
        ids = body.get("ids")
        if ids is not None and not isinstance(ids, list):
            raise HostError("VALIDATION", "ids 必须是端口 id 数组", {"field": "ids"})
        try:
            result = self.agent.batch_ports(action, ids, restart=self._restart_flag(body))
        except HostError as exc:
            self.agent.audit("port.batch_%s" % (action or "?"), ",".join(ids or ["all"]),
                             _client_ip(self), False, exc.message)
            raise
        self.agent.audit("port.batch_%s" % action, ",".join(result["changed"]) or "无变化",
                         _client_ip(self), True,
                         detail={"changed": result["changed"],
                                 "restarted": result.get("restarted")})
        self._ok(result)

    def _handle_service(self, path: str, method: str):
        parts = [p for p in path.split("/") if p]
        if len(parts) != 5 or parts[4] not in ("start", "stop", "restart", "toggle"):
            self._err(404, HostError("NOT_FOUND",
                                     "用法：/api/v1/services/{bridge|gateway|diag}/{start|stop|restart}"))
            return
        if method != "POST":
            self._err(405, HostError("METHOD_NOT_ALLOWED", "服务动作请用 POST"))
            return
        self._body()
        name, action = parts[3], parts[4]
        try:
            result = self._service_call(name, action)
        except HostError as exc:
            self.agent.audit("service.%s.%s" % (name, action), name, _client_ip(self),
                             False, exc.message)
            raise
        self.agent.audit("service.%s.%s" % (name, action), name, _client_ip(self), True)
        self._ok(result)

    def _service_call(self, name: str, action: str):
        if action == "start":
            return self.agent.service_start(name)
        if action == "stop":
            return self.agent.service_stop(name)
        if action == "restart":
            return self.agent.service_restart(name)
        running = self.agent.status()["bridge"]["windows"]["running"]
        return self.agent.service_stop(name) if running else self.agent.service_start(name)

    def _bridge_toggle(self):
        running = self.agent.status()["bridge"]["windows"]["running"]
        return self.agent.service_stop("bridge") if running else self.agent.service_start("bridge")

    def _handle_doctor(self, method: str):
        if method != "POST" and method != "GET":
            self._err(405, HostError("METHOD_NOT_ALLOWED", "doctor 支持 GET/POST"))
            return
        result = self.agent.doctor()
        self._ok(result)

    def _handle_logs(self, path: str):
        q = self._query()
        if path == "/api/bridge/log":
            source = "bridge"
        elif path == "/api/gateway/log":
            which = (q.get("which") or "out").lower()
            source = "gateway-err" if which == "err" else "gateway-out"
        else:
            source = q.get("source") or "bridge"
        lines = int(q.get("lines", self.agent.settings["log_lines_default"]))
        try:
            result = self.agent.log_tail(source, lines)
        except HostError:
            raise
        self._ok(result)


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def setup_logging(log_path: str = LOG_PATH) -> None:
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    handlers = [handler]
    if sys.stderr:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        handlers.append(stream)
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Windows host control plane for WSL")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--print-settings", action="store_true")
    # v1 计划任务写的是 `pythonw wsl-control.py 8766`（位置参数），保留兼容
    parser.add_argument("legacy_port", nargs="?", type=int, default=None)
    args = parser.parse_args(argv)

    settings = load_settings()
    if args.print_settings:
        print(json.dumps(settings, ensure_ascii=False, indent=2))
        return 0
    if args.port:
        settings["port"] = args.port
    elif args.legacy_port:
        settings["port"] = args.legacy_port
    if args.host:
        settings["host"] = args.host

    setup_logging()
    agent = HostAgent(settings)
    ControlHandler.agent = agent
    server = ControlServer((settings["host"], int(settings["port"])), ControlHandler)
    logging.info("wsl-control %s 启动: http://%s:%d (pid=%d, token=%s)",
                 VERSION, settings["host"], int(settings["port"]), os.getpid(),
                 agent.token_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logging.info("wsl-control 退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
