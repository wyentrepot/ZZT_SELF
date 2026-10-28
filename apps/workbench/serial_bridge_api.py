"""Serial bridge（Windows COM <-> WSL PTY）只读状态与可用性验证。

本模块只服务 Python 串口桥，不操作 usbipd，也不主动启停桥进程：

- GET  /api/serial-bridge/status  读配置/软链/桥状态文件，纯只读
- POST /api/serial-bridge/verify  逐口验证（软链、桥连接、pyserial 可打开）
- GET  /api/serial-bridge/verify  同上，默认验证全部启用端口

验证口径：
- channel_ok：WSL 桥进程在跑、PTY 软链是字符设备、Windows 桥已连上。
- workbench_ready：channel_ok 且 config/serial_ports.json 的同 id 映射
  指向桥接软链、parity=N（PTY 不接受 PARENB）。
"""
from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from shared.serial_mapping import SerialPortCatalog


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_bridge_config_path() -> Path:
    override = os.environ.get("WORKBENCH_SERIAL_BRIDGE_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        # 与 serial_ports.json 同口径：优先 exe 同级可维护配置。
        return Path(sys.executable).resolve().parent / "config" / "serial_bridge.json"
    return _repo_root() / "config" / "serial_bridge.json"


def _load_bridge_config(path: Path) -> tuple[dict[str, Any] | None, str]:
    """轻量读取；桥的完整校验在 tools/serial_bridge/serial_bridge.py。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"桥配置不存在：{path}"
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"桥配置不可读：{exc}"
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None, "桥配置 version 必须为 1"
    ports = payload.get("ports")
    if not isinstance(ports, list):
        return None, "桥配置 ports 必须为数组"
    wsl = payload.get("wsl") or {}
    if not isinstance(wsl, dict):
        return None, "桥配置 wsl 必须为对象"
    link_dir = str(wsl.get("link_dir") or "/run/hplc/serial").strip()
    status_file = str(wsl.get("status_file") or "").strip() or str(Path(link_dir) / "status.json")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(ports):
        if not isinstance(item, dict):
            return None, f"ports[{index}] 必须为对象"
        port_id = str(item.get("id") or "").strip()
        if not port_id:
            return None, f"ports[{index}].id 不能为空"
        try:
            tcp_port = int(item.get("tcp_port"))
            baudrate = int(item.get("baudrate", 115200))
            bytesize = int(item.get("bytesize", 8))
            stopbits = int(item.get("stopbits", 1))
        except (TypeError, ValueError):
            return None, f"ports[{index}] 参数必须为整数"
        if not 1 <= tcp_port <= 65535:
            return None, f"ports[{index}].tcp_port 超出范围：{tcp_port}"
        normalized.append({
            "id": port_id,
            "label": str(item.get("label") or port_id),
            "windows_com": str(item.get("windows_com") or "").strip(),
            "tcp_port": tcp_port,
            "baudrate": baudrate,
            "bytesize": bytesize,
            "parity": str(item.get("parity", "N")).strip().upper(),
            "stopbits": stopbits,
            "enabled": bool(item.get("enabled", True)),
        })
    return {
        "path": str(path),
        "link_dir": link_dir,
        "status_file": status_file,
        "listen_host": str(wsl.get("listen_host") or "127.0.0.1").strip(),
        "ports": normalized,
    }, ""


def _pid_alive(pid: int) -> bool:
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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _path_matches(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _mapping_for(catalog: SerialPortCatalog, port_id: str):
    return next((mapping for mapping in catalog.mappings if mapping.id == port_id), None)


def _port_status(port: dict[str, Any], config: dict[str, Any],
                 bridge_status: dict[str, Any] | None,
                 supervisor_running: bool,
                 catalog: SerialPortCatalog) -> dict[str, Any]:
    link = Path(config["link_dir"]) / port["id"]
    link_is_symlink = link.is_symlink()
    try:
        link_exists = link.exists()
        is_char = link_exists and stat.S_ISCHR(link.stat().st_mode)
    except OSError:
        link_exists = False
        is_char = False
    pts = ""
    if link_is_symlink:
        try:
            pts = os.readlink(link)
        except OSError:
            pts = ""

    bridge_ports = (bridge_status or {}).get("ports") or {}
    live = bridge_ports.get(port["id"]) if isinstance(bridge_ports, dict) else None
    if not isinstance(live, dict):
        live = {}
    client_connected = bool(live.get("client_connected"))
    control_connected = bool(live.get("control_connected"))

    mapping = _mapping_for(catalog, port["id"])
    warnings: list[str] = []
    errors: list[str] = []
    mapping_ready = False
    mapping_info: dict[str, Any] = {
        "mapping_id": port["id"],
        "found": mapping is not None,
        "device": "",
        "parity": "",
        "usb_busid": "",
        "matches_bridge_link": False,
        "parity_ok": False,
        "enabled": False,
        "baudrate": None,
    }
    if mapping is None:
        warnings.append("config/serial_ports.json 没有同 id 映射，工作台无法按 mapping_id 打开")
    else:
        device = mapping.device_for(os.name)
        parity_ok = str(mapping.parity).upper() == "N"
        matches = _path_matches(device, str(link))
        mapping_info.update({
            "device": device,
            "parity": mapping.parity,
            "usb_busid": mapping.usb_busid,
            "matches_bridge_link": matches,
            "parity_ok": parity_ok,
            "enabled": bool(getattr(mapping, "enabled", True)),
            "baudrate": int(getattr(mapping, "baudrate", 0) or 0) or None,
        })
        if not matches:
            warnings.append(
                f"工作台映射 linux_device={device!r} 未指向桥接软链 {str(link)!r}"
            )
        if not parity_ok:
            warnings.append(
                "工作台映射 parity 必须为 N：PTY 不接受 PARENB；"
                "真实校验位由 Windows 侧桥配置决定"
            )
        if mapping.usb_busid:
            warnings.append(
                "映射含 usb_busid；usbipd 仍 attach 时会优先打开真实设备而不是桥接软链"
            )
        if not getattr(mapping, "enabled", True):
            warnings.append("工作台映射已禁用")
        mapping_ready = bool(matches and parity_ok and getattr(mapping, "enabled", True))

    if not port.get("enabled", True):
        errors.append("端口在桥配置中已禁用")
    if not supervisor_running:
        errors.append("wsl_bridge 未运行（python3 tools/serial_bridge/serial_bridge.py wsl start）")
    if not link_exists or not is_char:
        errors.append("桥接软链不存在或目标不是字符设备")
    if supervisor_running and not client_connected:
        errors.append("Windows 侧数据通道未连接（检查 serial_bridge.py windows）")
    if supervisor_running and not control_connected:
        errors.append("Windows 侧控制通道未连接（波特率动态切换不可用）")

    channel_ok = bool(
        port.get("enabled", True)
        and supervisor_running
        and client_connected
        and control_connected
        and link_exists
        and is_char
    )
    return {
        "id": port["id"],
        "label": port["label"],
        "enabled": port.get("enabled", True),
        "windows_com": port["windows_com"],
        "tcp_port": port["tcp_port"],
        "control_port": int(live.get("control_port") or (port["tcp_port"] + 1000)),
        "physical": {
            "baudrate": port["baudrate"],
            "bytesize": port["bytesize"],
            "parity": port["parity"],
            "stopbits": port["stopbits"],
        },
        "link": str(link),
        "link_is_symlink": link_is_symlink,
        "link_exists": link_exists,
        "char_device": bool(is_char),
        "pts": pts,
        "bridge": {
            "client_connected": client_connected,
            "control_connected": control_connected,
            "desired_baud": live.get("desired_baud") or port["baudrate"],
            "control_last_error": live.get("control_last_error", ""),
            "tcp_port": live.get("tcp_port") or port["tcp_port"],
            "rx_bytes": live.get("rx_bytes", 0),
            "tx_bytes": live.get("tx_bytes", 0),
            "dropped_bytes": live.get("dropped_bytes", 0),
            "last_error": live.get("last_error", ""),
        },
        "mapping": mapping_info,
        "channel_ok": channel_ok,
        "workbench_ready": bool(channel_ok and mapping_ready),
        "warnings": warnings,
        "errors": errors,
    }


def _snapshot(config_path: Path | None, mapping_config_path: Path | None) -> dict[str, Any]:
    bridge_config_path = Path(config_path) if config_path else _default_bridge_config_path()
    config, config_error = _load_bridge_config(bridge_config_path)
    catalog = SerialPortCatalog.load(mapping_config_path)
    result: dict[str, Any] = {
        "ok": False,
        "config": str(bridge_config_path),
        "config_error": config_error,
        "mapping_config_error": catalog.mapping_error,
        "status_file": "",
        "supervisor": {"running": False, "pid": 0, "stale": False, "age_seconds": None},
        "summary": {"total": 0, "enabled": 0, "channel_ok": 0, "workbench_ready": 0},
        "ports": [],
    }
    if config is None:
        return result

    status_path = Path(config["status_file"])
    result["status_file"] = str(status_path)
    bridge_status = _read_json(status_path)
    pid = int((bridge_status or {}).get("pid") or 0)
    running = _pid_alive(pid)
    age_seconds = None
    try:
        age_seconds = round(max(0.0, time.time() - status_path.stat().st_mtime), 1)
    except OSError:
        age_seconds = None
    stale = bool(running and age_seconds is not None and age_seconds > 10)
    result["supervisor"] = {
        "running": running,
        "pid": pid if running else 0,
        "stale": stale,
        "age_seconds": age_seconds,
        "started_at": (bridge_status or {}).get("started_at", ""),
        "updated_at": (bridge_status or {}).get("updated_at", ""),
    }
    if bridge_status and str(bridge_status.get("link_dir") or "") != config["link_dir"]:
        result["config_error"] = (
            f"桥状态文件 link_dir={bridge_status.get('link_dir')!r} 与配置 "
            f"{config['link_dir']!r} 不一致"
        )

    ports = [
        _port_status(port, config, bridge_status, running, catalog)
        for port in config["ports"]
    ]
    result["ports"] = ports
    enabled = [port for port in ports if port["enabled"]]
    result["summary"] = {
        "total": len(ports),
        "enabled": len(enabled),
        "channel_ok": sum(1 for port in ports if port["channel_ok"]),
        "workbench_ready": sum(1 for port in ports if port["workbench_ready"]),
    }
    result["ok"] = bool(enabled) and all(port["channel_ok"] for port in enabled)
    return result


def _reservations(registry) -> list[dict[str, Any]]:
    if registry is None:
        return []
    try:
        return list(registry.snapshot())
    except Exception:  # noqa: BLE001 - 验证接口不因登记表故障失败
        return []


def _port_in_use(port: dict[str, Any], reservations: list[dict[str, Any]]) -> bool:
    candidates = {
        str(port["id"]).casefold(),
        str(port["link"]).casefold(),
        os.path.abspath(str(port["link"])).casefold(),
        str(port["windows_com"]).casefold(),
    }
    for reservation in reservations:
        if str(reservation.get("resource_id") or "").casefold() in candidates:
            return True
        for alias in reservation.get("aliases") or []:
            normalized = str(alias).strip().replace("\\\\.\\", "").replace("\\\\?\\", "").casefold()
            if normalized and (normalized in candidates
                               or os.path.abspath(normalized).casefold() in candidates):
                return True
    return False


def _verify_ports(snapshot: dict[str, Any], port_ids: list[str] | None,
                  open_port: bool,
                  reservations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ports = snapshot.get("ports") or []
    if port_ids:
        wanted = set(port_ids)
        ports = [port for port in ports if port["id"] in wanted]
        missing = sorted(wanted - {port["id"] for port in ports})
    else:
        missing = []
    serial = None
    import_error = ""
    if open_port:
        try:
            import serial  # noqa: PLC0415
        except ImportError as exc:
            import_error = f"缺少 pyserial：{exc}"
    reservations = reservations or []
    for port in ports:
        in_use = _port_in_use(port, reservations)
        checks: dict[str, Any] = {
            "link_exists": port["link_exists"],
            "char_device": port["char_device"],
            "windows_client_connected": port["bridge"]["client_connected"],
            "windows_control_connected": port["bridge"]["control_connected"],
            "in_use": in_use,
            "open_ok": None,
            "open_error": import_error,
        }
        if in_use:
            checks["open_error"] = "端口被工作台会话占用，跳过打开检查"
        elif open_port and import_error:
            checks["open_ok"] = False
        elif open_port and port["link_exists"] and port["char_device"]:
            try:
                # 用工作台映射的 baud 打开：PTY 的 termios 会经控制通道同步到
                # Windows 侧，避免 verify 把物理口切到与业务不一致的速率。
                baudrate = int(
                    port["mapping"].get("baudrate") or port["physical"]["baudrate"]
                )
                handle = serial.Serial(
                    port=port["link"],
                    baudrate=baudrate,
                    bytesize=int(port["physical"]["bytesize"]),
                    parity="N",  # PTY 不接受 PARENB；真实校验在 Windows 侧
                    stopbits=int(port["physical"]["stopbits"]),
                    timeout=0.2,
                )
                # 给 supervisor 的 termios 轮询一个窗口，确保 verify 的 baud
                # 也能通过控制通道同步到 Windows 侧（否则开/关太快会被漏采）。
                time.sleep(0.15)
                handle.close()
                checks["open_ok"] = True
            except Exception as exc:  # noqa: BLE001 - 验证结果原样返回
                checks["open_ok"] = False
                checks["open_error"] = str(exc)
        port["checks"] = checks
        port["verify_ok"] = bool(
            port["enabled"] and port["channel_ok"] and port["workbench_ready"]
            and checks["open_ok"] is not False
        )
    return {
        "ok": bool(ports) and all(port["verify_ok"] for port in ports if port["enabled"]),
        "missing": missing,
        "ports": ports,
    }


def create_serial_bridge_router(
    *,
    config_path: Path | str | None = None,
    mapping_config_path: Path | str | None = None,
    resource_registry=None,
) -> APIRouter:
    router = APIRouter(prefix="/api/serial-bridge", tags=["serial-bridge"])
    fixed_config = Path(config_path) if config_path else None
    fixed_mapping = Path(mapping_config_path) if mapping_config_path else None

    @router.get("/status")
    def get_status() -> dict[str, Any]:
        return _snapshot(fixed_config, fixed_mapping)

    @router.get("/verify")
    def verify_get() -> dict[str, Any]:
        snapshot = _snapshot(fixed_config, fixed_mapping)
        snapshot.update(_verify_ports(
            snapshot, None, True, _reservations(resource_registry)))
        return snapshot

    @router.post("/verify")
    def verify_post(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        port_ids = payload.get("ports")
        if isinstance(port_ids, str):
            port_ids = [port_ids]
        if port_ids is not None and not isinstance(port_ids, list):
            port_ids = []
        open_port = bool(payload.get("open", True))
        snapshot = _snapshot(fixed_config, fixed_mapping)
        snapshot.update(_verify_ports(
            snapshot,
            [str(item) for item in port_ids] if port_ids else None,
            open_port,
            _reservations(resource_registry),
        ))
        return snapshot

    return router
