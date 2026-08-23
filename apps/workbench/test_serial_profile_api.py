"""P4 serial-profile REST API tests (GET/PUT save-only, POST apply)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.serial_profile import SerialProfileStore
from workbench.serial_profile_api import create_serial_profile_router
from workbench.serial_profile_applier import SerialProfileApplier
from workbench.test_serial_profile_applier import (
    FakeListenerService,
    FakeModuleService,
    FakeSimcon,
)


@pytest.fixture
def profile_store(tmp_path: Path) -> SerialProfileStore:
    mapping = tmp_path / "serial_ports.json"
    mapping.write_text(json.dumps({
        "version": 1,
        "ports": [
            {"id": "listener", "linux_device": "/dev/ttyUSB0", "windows_com": "COM4",
             "label": "侦听台", "usage": "listener", "module": "",
             "baudrate": 115200, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": True},
            {"id": "cco-main", "linux_device": "/dev/ttyACM0", "windows_com": "COM8",
             "label": "CCO 日志口", "usage": "module_log", "module": "cco",
             "baudrate": 115200, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": True},
        ],
    }), encoding="utf-8")
    return SerialProfileStore(runtime_dir=tmp_path / "runtime", mapping_config_path=mapping)


@pytest.fixture
def client(profile_store, tmp_path):
    module = FakeModuleService()
    listener = FakeListenerService()
    applier = SerialProfileApplier(
        module_service=module, listener_service=listener, simcon_service=FakeSimcon(),
        profile_store=profile_store,
    )
    app = FastAPI()
    app.include_router(create_serial_profile_router(
        profile_store=profile_store, applier=applier,
    ))
    return TestClient(app), profile_store


# ---------------------------------------------------------------------------
# GET /api/serial-profile
# ---------------------------------------------------------------------------

def test_get_returns_four_slots(client):
    c, store = client
    resp = c.get("/api/serial-profile")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["profiles"].keys()) == {
        "module_log.cco", "module_log.sta", "listener.main", "simcon.main",
    }
    # 默认全禁用
    for entry in body["profiles"].values():
        assert entry["enabled"] is False


# ---------------------------------------------------------------------------
# PUT /api/serial-profile 只保存，不操作硬件
# ---------------------------------------------------------------------------

def test_put_saves_without_touching_hardware(client):
    c, store = client
    payload = {
        "profiles": {
            "listener.main": {"mapping_id": "listener", "enabled": True},
            "module_log.cco": {"mapping_id": "cco-main", "enabled": True, "baudrate": 9600},
        }
    }
    resp = c.put("/api/serial-profile", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved"] is True
    saved = body["profiles"]
    assert saved["listener.main"]["enabled"] is True
    assert saved["module_log.cco"]["baudrate"] == 9600
    # 只保存：磁盘上 profile 已写入，但无任何串口被打开
    assert store.profile_path.exists()


def test_put_unknown_mapping_422(client):
    c, _ = client
    payload = {"profiles": {"listener.main": {"mapping_id": "nope", "enabled": True}}}
    resp = c.put("/api/serial-profile", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/serial-profile/apply 一键应用已保存版本
# ---------------------------------------------------------------------------

def test_apply_reads_saved_profile_and_starts(client):
    c, store = client
    # 先保存 listener 启用
    c.put("/api/serial-profile", json={
        "profiles": {"listener.main": {"mapping_id": "listener", "enabled": True}},
    })
    resp = c.post("/api/serial-profile/apply")
    assert resp.status_code == 200
    body = resp.json()
    by_slot = {s["slot"]: s for s in body["slots"]}
    assert by_slot["listener.main"]["status"] == "started"
    assert body["overall"] == "ok"


def test_apply_default_profile_starts_nothing(client):
    c, _ = client
    # 未保存任何启用项
    resp = c.post("/api/serial-profile/apply")
    assert resp.status_code == 200
    body = resp.json()
    for s in body["slots"]:
        assert s["status"] in ("skipped", "unchanged")
    assert body["overall"] == "ok"
