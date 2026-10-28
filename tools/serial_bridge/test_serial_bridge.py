"""Tests for tools/serial_bridge/serial_bridge.py (config + WSL PTY pump)."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).with_name("serial_bridge.py")


def _read_line(sock: socket.socket) -> str:
    buffer = bytearray()
    while b"\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer.extend(chunk)
    line, _, _ = buffer.partition(b"\n")
    return line.decode("utf-8")
_SPEC = importlib.util.spec_from_file_location("zzt_serial_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bridge)


def _write_config(tmp_path: Path, ports: list[dict]) -> Path:
    path = tmp_path / "serial_bridge.json"
    path.write_text(json.dumps({
        "version": 1,
        "windows": {"host": "127.0.0.1", "reconnect_seconds": 0.2},
        "wsl": {
            "listen_host": "127.0.0.1",
            "link_dir": str(tmp_path / "serial"),
            "status_file": str(tmp_path / "serial" / "status.json"),
        },
        "ports": ports,
    }), encoding="utf-8")
    return path


def _port(port_id: str, tcp_port: int) -> dict:
    return {
        "id": port_id, "label": port_id, "windows_com": "COM_TEST",
        "tcp_port": tcp_port, "baudrate": 115200, "bytesize": 8,
        "parity": "N", "stopbits": 1, "enabled": True,
    }


def test_load_config_normalizes_and_resolves_status(tmp_path):
    config_path = _write_config(tmp_path, [_port("listener", 18824)])
    config = bridge.load_config(config_path)
    assert config["windows"]["host"] == "127.0.0.1"
    assert config["wsl"]["link_dir"] == str(tmp_path / "serial")
    assert config["wsl"]["status_file"] == str(tmp_path / "serial" / "status.json")
    assert config["ports"][0]["id"] == "listener"


def test_load_config_rejects_duplicate_ids_and_ports(tmp_path):
    config_path = _write_config(tmp_path, [_port("a", 18824), _port("a", 18825)])
    with pytest.raises(bridge.ConfigError, match="重复"):
        bridge.load_config(config_path)
    config_path = _write_config(tmp_path, [_port("a", 18824), _port("b", 18824)])
    with pytest.raises(bridge.ConfigError, match="tcp_port 重复"):
        bridge.load_config(config_path)


def test_status_reports_config_freshness(tmp_path):
    config_path = _write_config(tmp_path, [_port("listener", 18824)])
    config = bridge.load_config(config_path)
    assert config["_loaded_at"] and config["_loaded_epoch"] > 0

    fresh = bridge._build_status(config, [], "2026-10-28T16:00:00", 4321)
    assert fresh["config_loaded_at"] == config["_loaded_at"]
    assert fresh["config_mtime"] != "unknown"
    assert fresh["config_stale"] is False
    assert fresh["stale_hint"] == ""
    assert fresh["config_sources"]["wsl_bridge"]["stale"] is False
    # Windows 侧取不到：必须明确 unknown，不能假装正常
    windows = fresh["config_sources"]["windows_bridge"]
    assert windows["known"] is False
    assert windows["loaded_at"] == "unknown"
    assert windows["stale"] == "unknown"
    assert "Windows" in windows["reason"]

    loaded = float(config["_loaded_epoch"])
    # 更旧：不算变更
    os.utime(config_path, (loaded - 60, loaded - 60))
    assert bridge._build_status(config, [], "", 1)["config_stale"] is False
    # 相等：严格大于才 stale
    os.utime(config_path, (loaded, loaded))
    assert bridge._build_status(config, [], "", 1)["config_stale"] is False
    # 更新：stale + 人话提示
    os.utime(config_path, (loaded + 60, loaded + 60))
    stale = bridge._build_status(config, [], "", 1)
    assert stale["config_stale"] is True
    assert stale["config_sources"]["wsl_bridge"]["stale"] is True
    assert "被修改" in stale["stale_hint"]
    assert "重启" in stale["stale_hint"]
    assert "SIGHUP" in stale["stale_hint"]
    assert stale["stale_hint"] != fresh["stale_hint"]


def test_config_freshness_unknown_when_file_missing(tmp_path):
    config_path = _write_config(tmp_path, [_port("listener", 18824)])
    config = bridge.load_config(config_path)
    config_path.unlink()
    state = bridge.config_freshness(config)
    assert state["config_mtime"] == "unknown"
    assert state["config_stale"] is False
    assert state["stale_hint"] == ""
    assert "mtime" in state["config_sources"]["wsl_bridge"]["mtime_error"]


@pytest.mark.skipif(os.name == "nt", reason="WSL PTY pump is POSIX-only")
def test_port_runtime_pumps_bytes_and_exposes_symlink(tmp_path):
    serial = pytest.importorskip("serial")
    stop_event = threading.Event()
    status_event = threading.Event()
    spec = bridge.load_config(_write_config(tmp_path, [_port("loop", 18824)]))["ports"][0]
    spec["control_port"] = 0  # 测试用临时端口，避免固定端口冲突
    runtime = bridge.PortRuntime(
        spec,
        {"link_dir": str(tmp_path / "serial"), "listen_host": "127.0.0.1"},
        stop_event,
        status_event,
    )
    runtime.prepare()
    runtime.start()
    link = tmp_path / "serial" / "loop"
    sock = None
    control = None
    handle = None
    try:
        assert link.is_symlink()
        assert link.resolve() == Path(runtime.pts)
        control = socket.create_connection(("127.0.0.1", runtime.control_port), timeout=2.0)
        control.settimeout(2.0)
        hello = json.loads(_read_line(control))
        assert hello["type"] == "config"
        assert hello["baudrate"] == 115200

        sock = socket.create_connection(("127.0.0.1", runtime.tcp_port), timeout=2.0)
        handle = serial.Serial(str(link), 115200, bytesize=8, parity="N",
                               stopbits=1, timeout=0.5)
        time.sleep(0.1)
        assert status_event.is_set()
        assert runtime.snapshot()["client_connected"] is True
        assert runtime.snapshot()["control_connected"] is True

        sock.sendall(b"win-to-wsl")
        assert handle.read(32) == b"win-to-wsl"
        handle.write(b"wsl-to-win")
        assert sock.recv(32) == b"wsl-to-win"

        # APP 改波特率 -> 控制通道应发出 config 命令
        handle.baudrate = 9600
        message = json.loads(_read_line(control))
        assert message["type"] == "config"
        assert message["baudrate"] == 9600
        assert runtime.snapshot()["desired_baud"] == 9600

        handle.baudrate = 115200
        message = json.loads(_read_line(control))
        assert message["baudrate"] == 115200
    finally:
        if handle is not None:
            handle.close()
        if control is not None:
            control.close()
        if sock is not None:
            sock.close()
        stop_event.set()
        runtime.close()
        runtime.join(timeout=2.0)


def test_port_runtime_rejects_non_symlink_path(tmp_path):
    stop_event = threading.Event()
    spec = {
        "id": "loop", "label": "loop", "windows_com": "COM_TEST", "tcp_port": 18824,
        "baudrate": 115200, "bytesize": 8, "parity": "N", "stopbits": 1, "enabled": True,
    }
    link_dir = tmp_path / "serial"
    link_dir.mkdir()
    (link_dir / "loop").write_text("not a link", encoding="utf-8")
    runtime = bridge.PortRuntime(
        spec, {"link_dir": str(link_dir), "listen_host": "127.0.0.1"}, stop_event,
    )
    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        runtime.prepare()
    runtime.close()
