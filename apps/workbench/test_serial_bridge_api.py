"""Serial bridge workbench API tests (status + verify)."""
from __future__ import annotations

import json
import os
import pty
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from workbench.serial_bridge_api import create_serial_bridge_router


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def bridge(tmp_path: Path):
    master_fd, slave_fd = pty.openpty()
    pts = os.ttyname(slave_fd)
    link_dir = tmp_path / "serial"
    link_dir.mkdir()
    link = link_dir / "listener"
    link.symlink_to(pts)

    config = _write(tmp_path / "serial_bridge.json", {
        "version": 1,
        "windows": {"host": "127.0.0.1", "reconnect_seconds": 1.0},
        "wsl": {
            "listen_host": "127.0.0.1",
            "link_dir": str(link_dir),
            "status_file": str(link_dir / "status.json"),
        },
        "ports": [{
            "id": "listener", "label": "侦听台", "windows_com": "COM24",
            "tcp_port": 18824, "baudrate": 115200, "bytesize": 8,
            "parity": "E", "stopbits": 1, "enabled": True,
        }],
    })
    mapping = _write(tmp_path / "serial_ports.json", {
        "version": 1,
        "ports": [{
            "id": "listener", "linux_device": str(link), "windows_com": "COM24",
            "label": "侦听台", "usage": "listener", "module": "",
            "baudrate": 115200, "parity": "N", "bytesize": 8,
            "stopbits": 1, "enabled": True,
        }],
    })
    _write(link_dir / "status.json", {
        "version": 1,
        "pid": os.getpid(),
        "started_at": "2026-10-22T00:00:00",
        "updated_at": "2026-10-22T00:00:01",
        "link_dir": str(link_dir),
        "ports": {
            "listener": {
                "id": "listener", "tcp_port": 18824, "control_port": 19824,
                "link": str(link), "pts": pts,
                "listening": True, "client_connected": True,
                "control_connected": True, "desired_baud": 115200,
                "rx_bytes": 10, "tx_bytes": 20, "dropped_bytes": 0, "last_error": "",
            },
        },
    })

    def client() -> TestClient:
        app = FastAPI()
        app.include_router(create_serial_bridge_router(
            config_path=config, mapping_config_path=mapping,
        ))
        return TestClient(app)

    yield {
        "client": client,
        "config": config,
        "mapping": mapping,
        "link": link,
        "pts": pts,
        "link_dir": link_dir,
    }
    os.close(master_fd)
    os.close(slave_fd)


def test_status_reports_channel_and_workbench_ready(bridge):
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["config_error"] == ""
    assert body["supervisor"]["running"] is True
    assert body["ok"] is True
    assert body["summary"] == {
        "total": 1, "enabled": 1, "channel_ok": 1, "workbench_ready": 1,
    }
    port = body["ports"][0]
    assert port["channel_ok"] is True
    assert port["workbench_ready"] is True
    assert port["pts"] == bridge["pts"]
    assert port["mapping"]["matches_bridge_link"] is True
    assert port["mapping"]["parity_ok"] is True
    assert port["bridge"]["rx_bytes"] == 10
    assert port["bridge"]["control_connected"] is True
    assert port["control_port"] == 19824


def test_verify_opens_pty_and_reports_checks(bridge):
    body = bridge["client"]().post("/api/serial-bridge/verify", json={}).json()
    assert body["ok"] is True
    port = body["ports"][0]
    assert port["verify_ok"] is True
    assert port["checks"]["open_ok"] is True
    assert port["checks"]["open_error"] == ""


def test_verify_uses_mapping_baudrate_not_physical_default(bridge):
    from unittest import mock

    payload = json.loads(bridge["mapping"].read_text(encoding="utf-8"))
    payload["ports"][0]["baudrate"] = 9600
    bridge["mapping"].write_text(json.dumps(payload), encoding="utf-8")
    fake_handle = mock.Mock()
    with mock.patch("serial.Serial", return_value=fake_handle) as ctor:
        body = bridge["client"]().post("/api/serial-bridge/verify", json={}).json()
    assert body["ok"] is True
    assert ctor.call_args.kwargs["baudrate"] == 9600
    fake_handle.close.assert_called_once()


def test_verify_skips_open_when_port_is_in_use(bridge):
    from shared.serial_resources import SerialResourceRegistry

    registry = SerialResourceRegistry()
    registry.reserve(
        "listener:1", label="侦听台", resource_id="listener",
        aliases=(str(bridge["link"]), "COM24"),
    )
    app = FastAPI()
    app.include_router(create_serial_bridge_router(
        config_path=bridge["config"],
        mapping_config_path=bridge["mapping"],
        resource_registry=registry,
    ))
    body = TestClient(app).post("/api/serial-bridge/verify", json={}).json()
    port = body["ports"][0]
    assert port["checks"]["in_use"] is True
    assert port["checks"]["open_ok"] is None
    assert "占用" in port["checks"]["open_error"]
    assert port["verify_ok"] is True


def test_parity_e_mapping_blocks_workbench_ready(bridge):
    payload = json.loads(bridge["mapping"].read_text(encoding="utf-8"))
    payload["ports"][0]["parity"] = "E"
    bridge["mapping"].write_text(json.dumps(payload), encoding="utf-8")

    body = bridge["client"]().post("/api/serial-bridge/verify", json={}).json()
    assert body["ok"] is False
    port = body["ports"][0]
    assert port["channel_ok"] is True
    assert port["workbench_ready"] is False
    assert any("parity" in warning for warning in port["warnings"])


def test_missing_link_is_not_channel_ok(bridge):
    bridge["link"].unlink()
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["ok"] is False
    port = body["ports"][0]
    assert port["link_exists"] is False
    assert port["channel_ok"] is False
    assert any("软链" in error for error in port["errors"])


def test_windows_control_disconnected_is_not_channel_ok(bridge):
    status_path = bridge["link_dir"] / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["ports"]["listener"]["control_connected"] = False
    status_path.write_text(json.dumps(status), encoding="utf-8")

    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["ok"] is False
    assert body["ports"][0]["bridge"]["control_connected"] is False
    assert any("控制通道" in error for error in body["ports"][0]["errors"])


def test_windows_client_disconnected_is_not_channel_ok(bridge):
    status_path = bridge["link_dir"] / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["ports"]["listener"]["client_connected"] = False
    status_path.write_text(json.dumps(status), encoding="utf-8")

    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["ok"] is False
    assert body["ports"][0]["bridge"]["client_connected"] is False
    assert any("Windows" in error for error in body["ports"][0]["errors"])


def test_verify_unknown_port_is_reported(bridge):
    body = bridge["client"]().post(
        "/api/serial-bridge/verify", json={"ports": ["nope"]},
    ).json()
    assert body["ok"] is False
    assert body["missing"] == ["nope"]


def test_missing_bridge_config_degrades(tmp_path):
    app = FastAPI()
    app.include_router(create_serial_bridge_router(
        config_path=tmp_path / "missing.json",
        mapping_config_path=tmp_path / "serial_ports.json",
    ))
    body = TestClient(app).get("/api/serial-bridge/status").json()
    assert body["ok"] is False
    assert "不存在" in body["config_error"]
