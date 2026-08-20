"""统一串口映射配置。

所有 UI/服务通过此模块把 Windows COM、WSL/Linux 设备名和业务身份关联起来。
配置错误只影响映射增强，不阻塞系统枚举或真实串口打开。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_VALID_USAGE = {"", "listener", "module_log", "simcon"}
_VALID_MODULE = {"", "cco", "sta"}


def default_config_path() -> Path:
    """返回外部可维护配置路径，而不是 PyInstaller 内置资源路径。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config" / "serial_ports.json"
    return Path(__file__).resolve().parents[2] / "config" / "serial_ports.json"


def _normalise(value: str) -> str:
    return str(value or "").strip().lower()


@dataclass(frozen=True)
class SerialPortMapping:
    """一条稳定的物理串口身份映射。"""

    id: str
    linux_device: str = ""
    windows_com: str = ""
    label: str = ""
    usage: str = ""
    module: str = ""
    baudrate: int = 115200
    parity: str = "N"
    bytesize: int = 8
    stopbits: int = 1
    enabled: bool = True

    def device_for(self, platform_name: str | None = None) -> str:
        platform_name = platform_name or os.name
        if platform_name == "nt":
            return self.windows_com or self.linux_device
        return self.linux_device or self.windows_com

    def aliases(self) -> set[str]:
        return {_normalise(value) for value in (self.linux_device, self.windows_com) if value}

    def matches(self, device: str) -> bool:
        return _normalise(device) in self.aliases()

    def as_dict(self, platform_name: str | None = None) -> dict[str, Any]:
        return {
            "mapping_id": self.id,
            "device": self.device_for(platform_name),
            "linux_device": self.linux_device,
            "windows_com": self.windows_com,
            "com": self.windows_com if (platform_name or os.name) != "nt" else "",
            "label": self.label,
            "usage": self.usage,
            "module": self.module,
            "baudrate": self.baudrate,
            "parity": self.parity,
            "bytesize": self.bytesize,
            "stopbits": self.stopbits,
            "enabled": self.enabled,
        }


class SerialPortCatalog:
    """已校验映射及与系统枚举结果合并的只读目录。"""

    def __init__(self, mappings: Iterable[SerialPortMapping] = (), mapping_error: str = "", path: Path | None = None):
        self._mappings = tuple(mappings)
        self.mapping_error = mapping_error
        self.path = Path(path) if path is not None else default_config_path()
        self._by_alias: dict[str, SerialPortMapping] = {}
        for mapping in self._mappings:
            for alias in mapping.aliases():
                self._by_alias[alias] = mapping

    @property
    def mappings(self) -> tuple[SerialPortMapping, ...]:
        return self._mappings

    @classmethod
    def load(cls, path: Path | str | None = None) -> "SerialPortCatalog":
        config_path = Path(path) if path is not None else default_config_path()
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(mapping_error=f"串口映射文件不存在：{config_path}", path=config_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return cls(mapping_error=f"串口映射文件不可用：{exc}", path=config_path)

        if not isinstance(payload, dict) or payload.get("version") != 1:
            return cls(mapping_error="串口映射文件 version 必须为 1", path=config_path)
        entries = payload.get("ports")
        if not isinstance(entries, list):
            return cls(mapping_error="串口映射文件 ports 必须为数组", path=config_path)

        errors: list[str] = []
        mappings: list[SerialPortMapping] = []
        ids: set[str] = set()
        aliases: set[str] = set()
        for index, raw in enumerate(entries):
            prefix = f"ports[{index}]"
            if not isinstance(raw, dict):
                errors.append(f"{prefix} 必须为对象")
                continue
            mapping_id = str(raw.get("id", "")).strip()
            linux_device = str(raw.get("linux_device", "")).strip()
            windows_com = str(raw.get("windows_com", "")).strip()
            usage = str(raw.get("usage", "")).strip()
            module = str(raw.get("module", "")).strip().lower()
            if not mapping_id:
                errors.append(f"{prefix}.id 不能为空")
            elif mapping_id in ids:
                errors.append(f"映射 id 重复：{mapping_id}")
            if not linux_device and not windows_com:
                errors.append(f"{prefix} 至少需要 linux_device 或 windows_com")
            if usage not in _VALID_USAGE:
                errors.append(f"{prefix}.usage 非法：{usage}")
            if module not in _VALID_MODULE:
                errors.append(f"{prefix}.module 非法：{module}")
            try:
                baudrate = int(raw.get("baudrate", 115200))
                bytesize = int(raw.get("bytesize", 8))
                stopbits = int(raw.get("stopbits", 1))
            except (TypeError, ValueError):
                errors.append(f"{prefix} 串口参数必须为整数")
                continue
            parity = str(raw.get("parity", "N")).strip().upper()
            if baudrate < 300 or bytesize < 5 or bytesize > 8 or stopbits not in (1, 2) or parity not in {"N", "E", "O", "M", "S"}:
                errors.append(f"{prefix} 串口参数非法")
            entry_aliases = {_normalise(item) for item in (linux_device, windows_com) if item}
            duplicate_aliases = entry_aliases.intersection(aliases)
            if duplicate_aliases:
                errors.append(f"映射设备别名重复：{', '.join(sorted(duplicate_aliases))}")
            if errors:
                # 本配置按原子性处理：有任一错误时不选取半份映射，避免误开错误串口。
                continue
            ids.add(mapping_id)
            aliases.update(entry_aliases)
            mappings.append(
                SerialPortMapping(
                    id=mapping_id,
                    linux_device=linux_device,
                    windows_com=windows_com,
                    label=str(raw.get("label", "")).strip(),
                    usage=usage,
                    module=module,
                    baudrate=baudrate,
                    parity=parity,
                    bytesize=bytesize,
                    stopbits=stopbits,
                    enabled=bool(raw.get("enabled", True)),
                )
            )
        if errors:
            return cls(mapping_error="；".join(errors), path=config_path)
        return cls(mappings=mappings, path=config_path)

    def find(self, device: str) -> SerialPortMapping | None:
        return self._by_alias.get(_normalise(device))

    def identity_key(self, device: str) -> str:
        mapping = self.find(device)
        return mapping.id if mapping is not None else _normalise(device)

    def merge_system_ports(self, ports: Iterable[Any], platform_name: str | None = None) -> list[dict[str, Any]]:
        """返回可展示端口。映射存在但离线时也返回，供 UI 明确标记。"""
        platform_name = platform_name or os.name
        results: list[dict[str, Any]] = []
        seen_mapping_ids: set[str] = set()
        seen_devices: set[str] = set()
        for raw in ports:
            if isinstance(raw, str):
                device, description = raw, ""
            elif isinstance(raw, dict):
                device, description = str(raw.get("device", "")), str(raw.get("description", ""))
            else:
                device, description = str(getattr(raw, "device", "")), str(getattr(raw, "description", ""))
            if not device:
                continue
            normalised = _normalise(device)
            if normalised in seen_devices:
                continue
            seen_devices.add(normalised)
            mapping = self.find(device)
            record: dict[str, Any] = {
                "device": device,
                "description": description,
                "online": True,
                "mapping_id": "",
                "linux_device": "",
                "windows_com": "",
                "com": "",
                "label": "",
                "usage": "",
                "module": "",
                "baudrate": None,
                "parity": "",
                "bytesize": None,
                "stopbits": None,
                "enabled": True,
            }
            if mapping is not None:
                record.update(mapping.as_dict(platform_name))
                record["device"] = device
                record["description"] = description
                record["online"] = True
                seen_mapping_ids.add(mapping.id)
            results.append(record)

        for mapping in self._mappings:
            if not mapping.enabled or mapping.id in seen_mapping_ids:
                continue
            record = mapping.as_dict(platform_name)
            record.update({"description": "配置的串口当前离线", "online": False})
            results.append(record)
        return results