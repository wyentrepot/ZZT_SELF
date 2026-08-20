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