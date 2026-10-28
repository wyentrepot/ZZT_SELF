"""Serial bridge status 语义与 config_stale 测试（全 fake：tmp 文件 + pty，不碰真实串口）。"""
from __future__ import annotations

import json
import os
import pty
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from workbench.serial_bridge_api import create_serial_bridge_router

SEMANTICS_KEYS = {
    "ok", "all_enabled_ready", "usable", "workbench_ready", "in_use", "open_ok",
}
BASE_TS = "2026-10-28T16:00:00"
BASE_EPOCH = time.mktime(time.strptime(BASE_TS, "%Y-%m-%dT%H:%M:%S"))


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))


def _port(port_id: str, tcp_port: int, com: str) -> dict:
    return {
        "id": port_id, "label": port_id, "windows_com": com,
        "tcp_port": tcp_port, "baudrate": 115200, "bytesize": 8,
        "parity": "N", "stopbits": 1, "enabled": True,
    }


def _mapping(port_id: str, device: Path, com: str) -> dict:
    return {
        "id": port_id, "linux_device": str(device), "windows_com": com,
        "label": port_id, "usage": "", "module": "",
        "baudrate": 115200, "parity": "N", "bytesize": 8,
        "stopbits": 1, "enabled": True,
    }


@pytest.fixture
def bridge(tmp_path: Path):
    master_fd, slave_fd = pty.openpty()
    pts = os.ttyname(slave_fd)
    link_dir = tmp_path / "serial"
    link_dir.mkdir()
    link = link_dir / "listener"
    link.symlink_to(pts)
    missing = link_dir / "factory"  # 故意不建：模拟没接的 COM7

    config = _write(tmp_path / "serial_bridge.json", {
        "version": 1,
        "windows": {"host": "127.0.0.1", "reconnect_seconds": 1.0},
        "wsl": {
            "listen_host": "127.0.0.1",
            "link_dir": str(link_dir),
            "status_file": str(link_dir / "status.json"),
        },
        "ports": [
            _port("listener", 18824, "COM11"),
            _port("factory", 18817, "COM7"),
        ],
    })
    mapping = _write(tmp_path / "serial_ports.json", {
        "version": 1,
        "ports": [
            _mapping("listener", link, "COM11"),
            _mapping("factory", missing, "COM7"),
        ],
    })
    _write(link_dir / "status.json", {
        "version": 1,
        "pid": os.getpid(),
        "started_at": BASE_TS,
        "updated_at": BASE_TS,
        "config": str(config),
        "link_dir": str(link_dir),
        "ports": {
            "listener": {
                "id": "listener", "tcp_port": 18824, "control_port": 19824,
                "link": str(link), "pts": pts,
                "listening": True, "client_connected": True,
                "control_connected": True, "desired_baud": 115200,
                "rx_bytes": 0, "tx_bytes": 0, "dropped_bytes": 0, "last_error": "",
            },
            "factory": {
                "id": "factory", "tcp_port": 18817, "control_port": 19817,
                "link": str(missing), "pts": "",
                "listening": True, "client_connected": False,
                "control_connected": False, "desired_baud": 9600,
                "rx_bytes": 0, "tx_bytes": 0, "dropped_bytes": 0, "last_error": "",
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
        "link_dir": link_dir,
        "pts": pts,
    }
    os.close(master_fd)
    os.close(slave_fd)


def _patch_status(bridge, **fields) -> None:
    path = bridge["link_dir"] / "status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(fields)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _keep_only(config_path: Path, port_ids: list[str]) -> None:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload["ports"] = [item for item in payload["ports"] if item["id"] in port_ids]
    config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_usable_only_depends_on_own_port(bridge):
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    ports = {port["id"]: port for port in body["ports"]}

    # 没接的 factory 仍把全局 AND 拉低……
    assert body["ok"] is False
    assert body["all_enabled_ready"] is False
    assert ports["factory"]["errors"]
    assert ports["factory"]["usable"] is False
    # ……但就绪的 listener 自身仍然可用，不背全局的锅
    assert ports["listener"]["usable"] is True
    assert body["ready_ports"] == ["listener"]
    assert [item["id"] for item in body["unusable_ports"]] == ["factory"]
    assert "软链" in body["unusable_ports"][0]["reason"]


def test_ok_and_all_enabled_ready_diverge_when_mapping_not_ready(bridge):
    # 只留通道全通的 listener，但把工作台映射改成 parity=E -> 工作台开不了
    _keep_only(bridge["config"], ["listener"])
    payload = json.loads(bridge["mapping"].read_text(encoding="utf-8"))
    payload["ports"][0]["parity"] = "E"
    bridge["mapping"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    body = bridge["client"]().get("/api/serial-bridge/status").json()
    port = body["ports"][0]
    assert body["ok"] is True                 # ok 只看通道层
    assert body["all_enabled_ready"] is False  # 映射没就绪，工作台整体不可用
    assert port["channel_ok"] is True
    assert port["workbench_ready"] is False
    assert port["usable"] is False
    assert body["ready_ports"] == []
    assert [item["id"] for item in body["unusable_ports"]] == ["listener"]
    assert "parity" in body["unusable_ports"][0]["reason"]


def test_semantics_documents_all_six_fields(bridge):
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    semantics = body["semantics"]
    assert set(semantics) == SEMANTICS_KEYS
    assert "全局与" in semantics["ok"]
    assert "usable" in semantics["ok"]
    assert "会话" in semantics["in_use"]
    assert "null" in semantics["open_ok"]
    assert "channel_ok" in semantics["all_enabled_ready"]

    # verify 走同一份 snapshot，也要带 semantics；open=false 不打开真实串口
    verify = bridge["client"]().post(
        "/api/serial-bridge/verify", json={"open": False},
    ).json()
    assert set(verify["semantics"]) == SEMANTICS_KEYS
    checks = {port["id"]: port["checks"] for port in verify["ports"]}
    assert checks["listener"]["open_ok"] is None
    assert checks["listener"]["in_use"] is False


def test_config_stale_true_when_mtime_newer_than_loaded(bridge):
    newer = BASE_EPOCH + 600
    os.utime(bridge["config"], (newer, newer))
    _patch_status(
        bridge,
        config_loaded_at=BASE_TS,
        config_mtime=_iso(BASE_EPOCH + 300),  # 桥自报的 mtime 略旧
        config_stale=False,
        stale_hint="",
    )

    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["config_loaded_at"] == BASE_TS
    assert body["config_mtime"] == _iso(newer)  # 现场 stat，比桥自报的新
    assert body["config_stale"] is True
    assert "被修改" in body["stale_hint"]
    assert "SIGHUP" in body["stale_hint"]
    assert body["config_sources"]["wsl_bridge"]["stale"] is True


def test_config_not_stale_when_equal_or_older(bridge):
    os.utime(bridge["config"], (BASE_EPOCH, BASE_EPOCH))
    _patch_status(bridge, config_loaded_at=BASE_TS, config_mtime=BASE_TS,
                  config_stale=False, stale_hint="")
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["config_stale"] is False
    assert body["stale_hint"] == ""

    older = BASE_EPOCH - 600
    os.utime(bridge["config"], (older, older))
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["config_stale"] is False
    assert body["stale_hint"] == ""


def test_config_stale_hint_relayed_from_bridge(bridge):
    os.utime(bridge["config"], (BASE_EPOCH, BASE_EPOCH))
    hint = "桥自己判定配置已变更"
    _patch_status(bridge, config_loaded_at=BASE_TS, config_mtime=BASE_TS,
                  config_stale=True, stale_hint=hint)
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["config_stale"] is True
    assert body["stale_hint"] == hint


def test_config_freshness_infers_stale_from_bridge_process_start(bridge):
    # 老版本桥不发 config_loaded_at：退回「桥进程启动时刻」做保守推断。
    # 配置 mtime 晚于进程启动 ⇒ 该进程必然在用旧配置 → 报 stale，并标出推断来源，
    # 不等于"假装桥新鲜"，也不需要等桥升级才能发现「改了没生效」。
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["config_stale"] is True
    assert body["config_loaded_at"]
    assert body["config_mtime"] != "unknown"
    assert body["stale_hint"]
    wsl = body["config_sources"]["wsl_bridge"]
    assert wsl["baseline_source"] == "process_start"
    assert wsl["stale"] is True
    # Windows 侧仍然取不到（跨主机），必须如实 unknown，不许假装正常
    windows = body["config_sources"]["windows_bridge"]
    assert windows["known"] is False
    assert windows["stale"] == "unknown"


def test_config_freshness_no_false_positive_when_bridge_restarted_after_config(bridge):
    # 桥进程启动晚于配置改动（正常重启过）→ 不得误报 stale
    _patch_status(bridge, started_at="2099-01-01T00:00:00")
    body = bridge["client"]().get("/api/serial-bridge/status").json()
    assert body["config_stale"] is False
    assert body["config_sources"]["wsl_bridge"]["baseline_source"] == "process_start"
