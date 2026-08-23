"""P3 serial-profile store RED/GREEN tests.

Profile Store 行为契约（总实施计划 P3）：
- 开发态保存到 data/runtime/，冻结态保存到 exe 同级 runtime/（本测试用 tmp_path 注入）。
- 首次无文件时返回四个默认禁用槽（enabled:false），不自动落盘。
- 选择 mapping_id 后从 serial_ports.json 回填默认波特率/数据位/校验位/停止位，可由 Profile 覆盖。
- 保存采用临时文件 + 原子替换。
- serial_ports.json 只读物理映射，不由页面/Profile 修改。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.serial_profile import (
    PROFILE_SLOTS,
    SerialProfileStore,
    UnknownMappingError,
    InvalidProfileError,
)


@pytest.fixture
def mapping_config(tmp_path: Path) -> Path:
    """构造带四个映射的 serial_ports.json（含默认串口参数）。"""
    data = {
        "version": 1,
        "ports": [
            {"id": "listener", "linux_device": "/dev/ttyUSB0", "windows_com": "COM4",
             "label": "侦听台", "usage": "listener", "module": "",
             "baudrate": 115200, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": True},
            {"id": "cco-main", "linux_device": "/dev/ttyACM0", "windows_com": "COM8",
             "label": "CCO 日志口", "usage": "module_log", "module": "cco",
             "baudrate": 115200, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": True},
            {"id": "sta-main", "linux_device": "/dev/ttyACM1", "windows_com": "COM9",
             "label": "STA 日志口", "usage": "module_log", "module": "sta",
             "baudrate": 115200, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": True},
            {"id": "simcon", "linux_device": "/dev/ttyUSB1", "windows_com": "COM24",
             "label": "模拟集中器", "usage": "simcon", "module": "",
             "baudrate": 9600, "parity": "E", "bytesize": 8, "stopbits": 1, "enabled": True},
        ],
    }
    path = tmp_path / "serial_ports.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path: Path, mapping_config: Path) -> SerialProfileStore:
    return SerialProfileStore(
        runtime_dir=tmp_path / "runtime",
        mapping_config_path=mapping_config,
    )


# ---------------------------------------------------------------------------
# 首次无文件：返回四个默认禁用槽，不落盘
# ---------------------------------------------------------------------------

def test_first_read_returns_four_disabled_slots_without_writing(store: SerialProfileStore):
    profiles = store.load()
    assert set(profiles.keys()) == set(PROFILE_SLOTS)
    for slot in PROFILE_SLOTS:
        assert profiles[slot]["enabled"] is False
    # 不自动落盘
    assert not (store.runtime_dir / "serial_profile.json").exists()


def test_slots_are_the_expected_four():
    assert PROFILE_SLOTS == [
        "module_log.cco",
        "module_log.sta",
        "listener.main",
        "simcon.main",
    ]


# ---------------------------------------------------------------------------
# 选择 mapping_id 后从 serial_ports.json 回填默认参数，可覆盖
# ---------------------------------------------------------------------------

def test_slot_mapping_roundtrip_defaults(store: SerialProfileStore, mapping_config: Path):
    """设置 slot->mapping 后回填 serial_ports.json 默认串口参数。"""
    store.update_slot("module_log.cco", mapping_id="cco-main", enabled=True)
    profiles = store.load()
    slot = profiles["module_log.cco"]
    assert slot["mapping_id"] == "cco-main"
    # 默认参数来自 serial_ports.json 的 cco-main
    assert slot["baudrate"] == 115200
    assert slot["parity"] == "N"
    assert slot["bytesize"] == 8
    assert slot["stopbits"] == 1
    assert slot["enabled"] is True


def test_slot_override_baudrate(store: SerialProfileStore):
    store.update_slot("listener.main", mapping_id="listener", enabled=True, baudrate=9600)
    slot = store.load()["listener.main"]
    assert slot["baudrate"] == 9600  # 覆盖默认 115200


def test_simcon_uses_its_own_defaults(store: SerialProfileStore):
    store.update_slot("simcon.main", mapping_id="simcon", enabled=True)
    slot = store.load()["simcon.main"]
    assert slot["baudrate"] == 9600
    assert slot["parity"] == "E"


# ---------------------------------------------------------------------------
# 非法参数 / 未知映射
# ---------------------------------------------------------------------------

def test_unknown_mapping_rejected(store: SerialProfileStore):
    with pytest.raises(UnknownMappingError):
        store.update_slot("module_log.cco", mapping_id="does-not-exist", enabled=True)


def test_invalid_baudrate_rejected(store: SerialProfileStore):
    with pytest.raises(InvalidProfileError):
        store.update_slot("module_log.cco", mapping_id="cco-main", enabled=True, baudrate=-1)


def test_unknown_slot_rejected(store: SerialProfileStore):
    with pytest.raises(InvalidProfileError):
        store.update_slot("nope.missing", mapping_id="cco-main", enabled=True)


# ---------------------------------------------------------------------------
# 保存：临时文件 + 原子替换
# ---------------------------------------------------------------------------

def test_save_is_atomic_and_readable(store: SerialProfileStore, tmp_path: Path):
    store.update_slot("listener.main", mapping_id="listener", enabled=True)
    store.save()
    target = store.runtime_dir / "serial_profile.json"
    assert target.exists()
    # 无 .tmp 残留
    assert not list(store.runtime_dir.glob("*.tmp"))
    # 重新加载保持一致
    reloaded = SerialProfileStore(
        runtime_dir=store.runtime_dir, mapping_config_path=store.mapping_config_path,
    ).load()
    assert reloaded["listener.main"]["mapping_id"] == "listener"
    assert reloaded["listener.main"]["enabled"] is True


def test_load_after_save_roundtrip(store: SerialProfileStore):
    store.update_slot("module_log.sta", mapping_id="sta-main", enabled=False)
    store.update_slot("simcon.main", mapping_id="simcon", enabled=True)
    store.save()
    reloaded = SerialProfileStore(
        runtime_dir=store.runtime_dir, mapping_config_path=store.mapping_config_path,
    ).load()
    assert reloaded["module_log.sta"]["enabled"] is False
    assert reloaded["simcon.main"]["enabled"] is True


# ---------------------------------------------------------------------------
# serial_ports.json 不被修改
# ---------------------------------------------------------------------------

def test_mapping_config_untouched_by_profile(store: SerialProfileStore, mapping_config: Path):
    before = mapping_config.read_text(encoding="utf-8")
    store.update_slot("listener.main", mapping_id="listener", enabled=True)
    store.save()
    assert mapping_config.read_text(encoding="utf-8") == before
