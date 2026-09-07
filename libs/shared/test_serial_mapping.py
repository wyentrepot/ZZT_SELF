import json
import tempfile
from pathlib import Path

import pytest

from shared.serial_mapping import SerialPortCatalog


def _write_config(data: dict) -> Path:
    folder = Path(tempfile.mkdtemp())
    path = folder / "serial_ports.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _config() -> dict:
    return {
        "version": 1,
        "ports": [
            {
                "id": "listener",
                "linux_device": "/dev/ttyUSB0",
                "windows_com": "COM4",
                "label": "侦听台",
                "usage": "listener",
                "module": "",
                "baudrate": 115200,
                "parity": "N",
                "bytesize": 8,
                "stopbits": 1,
                "enabled": True,
            },
            {
                "id": "cco-main",
                "linux_device": "/dev/ttyACM0",
                "windows_com": "COM8",
                "label": "CCO 日志口",
                "usage": "module_log",
                "module": "cco",
                "baudrate": 115200,
                "parity": "N",
                "bytesize": 8,
                "stopbits": 1,
                "enabled": True,
            },
        ],
    }


def test_catalog_matches_windows_and_wsl_aliases():
    catalog = SerialPortCatalog.load(_write_config(_config()))

    listener = catalog.find("/dev/ttyUSB0")
    assert listener is not None
    assert listener.id == "listener"
    assert catalog.find("COM4") is listener
    assert listener.device_for("nt") == "COM4"
    assert listener.device_for("posix") == "/dev/ttyUSB0"


def test_catalog_merges_mapped_and_unmapped_system_ports():
    catalog = SerialPortCatalog.load(_write_config(_config()))

    ports = catalog.merge_system_ports(
        [
            {"device": "/dev/ttyUSB0", "description": "CP210x"},
            {"device": "/dev/ttyACM0", "description": "CH342"},
            {"device": "/dev/ttyUSB9", "description": "Unknown"},
        ],
        platform_name="posix",
    )

    by_device = {port["device"]: port for port in ports}
    assert by_device["/dev/ttyUSB0"]["mapping_id"] == "listener"
    assert by_device["/dev/ttyUSB0"]["com"] == "COM4"
    assert by_device["/dev/ttyUSB0"]["label"] == "侦听台"
    assert by_device["/dev/ttyACM0"]["module"] == "cco"
    assert by_device["/dev/ttyUSB9"]["mapping_id"] == ""
    assert by_device["/dev/ttyUSB9"]["online"] is True


def test_invalid_or_missing_config_degrades_without_throwing():
    missing = Path(tempfile.mkdtemp()) / "missing.json"
    catalog = SerialPortCatalog.load(missing)
    assert catalog.mapping_error
    assert catalog.merge_system_ports([{"device": "COM99"}], platform_name="nt")[0]["device"] == "COM99"

    broken = _write_config({"version": 1, "ports": [{"id": "bad", "module": "invalid"}]})
    catalog = SerialPortCatalog.load(broken)
    assert "module" in catalog.mapping_error


def test_duplicate_ids_are_reported_without_choosing_one():
    data = _config()
    data["ports"].append({**data["ports"][0], "windows_com": "COM40"})

    catalog = SerialPortCatalog.load(_write_config(data))

    assert "重复" in catalog.mapping_error
    assert catalog.find("COM4") is None

def test_usb_busid_mapping():
    """REQS-串口锚点：config 含 usb_busid，按 BusId 匹配映射。"""
    data = _config()
    data["ports"][0]["usb_busid"] = "6-1"  # listener
    data["ports"][1]["usb_busid"] = "5-2"  # cco-main
    catalog = SerialPortCatalog.load(_write_config(data))

    # find_by_busid 稳定命中
    assert catalog.find_by_busid("6-1").id == "listener"
    assert catalog.find_by_busid("5-2").id == "cco-main"
    # as_dict 暴露 usb_busid
    assert catalog.find_by_busid("6-1").as_dict()["usb_busid"] == "6-1"


def test_merge_system_ports_priority_busid():
    """枚举时优先按 usb_busid 匹配（即使设备名与映射别名不同）。"""
    data = _config()
    data["ports"][0]["usb_busid"] = "6-1"
    # 故意让 listener 的 linux_device 与实际枚举名不一致（模拟漂移）
    data["ports"][0]["linux_device"] = "/dev/ttyUSB99"
    catalog = SerialPortCatalog.load(_write_config(data))

    merged = catalog.merge_system_ports(
        [{"device": "/dev/ttyUSB3", "description": "CP2102"}],
        device_busids={"/dev/ttyUSB3": "6-1"},
    )
    hit = [r for r in merged if r["device"] == "/dev/ttyUSB3"]
    assert hit and hit[0]["mapping_id"] == "listener"
    assert hit[0]["usb_busid"] == "6-1"


def test_merge_system_ports_ch342_dual_interface():
    """CH342 双串口：usb_busid 支持 busid:interface（5-2:1.0 / 5-2:1.2）区分。"""
    data = _config()
    # cco-main=5-2:1.0（ttyACM0）, sta-main=5-2:1.2（ttyACM1）
    data["ports"][1]["usb_busid"] = "5-2:1.0"
    data["ports"].append({
        "id": "sta-main",
        "linux_device": "/dev/ttyACM1",
        "windows_com": "COM9",
        "usb_busid": "5-2:1.2",
        "label": "STA 模块",
        "usage": "module_log",
        "module": "sta",
        "baudrate": 115200, "parity": "N", "bytesize": 8, "stopbits": 1, "enabled": True,
    })
    catalog = SerialPortCatalog.load(_write_config(data))

    merged = catalog.merge_system_ports(
        [
            {"device": "/dev/ttyACM0", "description": "USB Dual_Serial"},
            {"device": "/dev/ttyACM1", "description": "USB Dual_Serial"},
        ],
        device_busids={"/dev/ttyACM0": "5-2:1.0", "/dev/ttyACM1": "5-2:1.2"},
    )
    by_dev = {r["device"]: r for r in merged}
    assert by_dev["/dev/ttyACM0"]["mapping_id"] == "cco-main"
    assert by_dev["/dev/ttyACM1"]["mapping_id"] == "sta-main"
