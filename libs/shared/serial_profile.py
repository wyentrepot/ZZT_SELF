"""P3 serial-profile store: four logical slots over the read-only physical mapping.

总实施计划 P3：
- 保留 config/serial_ports.json 作为只读物理映射和默认参数来源。
- 新增独立运行 Profile，四个槽：module_log.cco / module_log.sta / listener.main / simcon.main。
- 四槽默认均 enabled:false；空槽或未启用槽不打开串口。
- 开发态保存到 data/runtime/，冻结态保存到 exe 同级 runtime/（由调用方注入 runtime_dir）。
- 首次无文件时返回四个默认禁用槽，不自动落盘。
- 选择 mapping_id 后从 serial_ports.json 回填默认波特率/数据位/校验位/停止位，可由 Profile 覆盖。
- 保存采用临时文件加原子替换。
- serial_ports.json 继续兼容现有 schema，不由页面/Profile 修改。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from shared.serial_mapping import SerialPortCatalog

PROFILE_SLOTS = ["module_log.cco", "module_log.sta", "listener.main", "simcon.main"]

# 槽 -> 默认 mapping_id（serial_ports.json 中的物理映射）
SLOT_DEFAULT_MAPPING = {
    "module_log.cco": "cco-main",
    "module_log.sta": "sta-main",
    "listener.main": "listener",
    "simcon.main": "simcon",
}

_PROFILE_FILENAME = "serial_profile.json"


class UnknownMappingError(ValueError):
    """mapping_id 不在 serial_ports.json 中。"""


class InvalidProfileError(ValueError):
    """Profile 参数非法（未知槽、非法串口参数等）。"""


def _validate_serial_params(*, baudrate: int, parity: str, bytesize: int, stopbits: int) -> None:
    if baudrate <= 0:
        raise InvalidProfileError(f"非法波特率：{baudrate}")
    if parity not in ("N", "E", "O", "M", "S"):
        raise InvalidProfileError(f"非法校验位：{parity!r}")
    if bytesize not in (5, 6, 7, 8):
        raise InvalidProfileError(f"非法数据位：{bytesize}")
    if stopbits not in (1, 1.5, 2):
        raise InvalidProfileError(f"非法停止位：{stopbits}")


class SerialProfileStore:
    """持久化四槽运行 Profile；不操作硬件。

    只保存配置，串口启停由 P4 的 SerialProfileApplier 在显式 apply 时执行。
    """

    def __init__(self, runtime_dir: Path | str, mapping_config_path: Path | str | None = None):
        self.runtime_dir = Path(runtime_dir)
        self.mapping_config_path = Path(mapping_config_path) if mapping_config_path else None
        self._catalog = (
            SerialPortCatalog.load(self.mapping_config_path)
            if self.mapping_config_path and self.mapping_config_path.exists()
            else SerialPortCatalog.load()
        )

    @property
    def profile_path(self) -> Path:
        return self.runtime_dir / _PROFILE_FILENAME

    def _mapping_by_id(self, mapping_id: str) -> dict[str, Any]:
        for mapping in self._catalog.mappings:
            if mapping.id == mapping_id:
                return mapping.as_dict()
        raise UnknownMappingError(f"未知映射：{mapping_id}")

    def _default_profile(self) -> dict[str, Any]:
        """四槽默认禁用，不落盘。"""
        return {
            slot: {
                "slot": slot,
                "mapping_id": SLOT_DEFAULT_MAPPING[slot],
                "enabled": False,
                "baudrate": None,
                "parity": None,
                "bytesize": None,
                "stopbits": None,
            }
            for slot in PROFILE_SLOTS
        }

    def load(self) -> dict[str, dict[str, Any]]:
        """读取 Profile；无文件时返回默认禁用槽且不落盘。"""
        if not self.profile_path.exists():
            return self._default_profile()
        try:
            raw = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise InvalidProfileError(f"Profile 文件损坏：{exc}") from exc
        profiles = self._default_profile()
        for slot in PROFILE_SLOTS:
            if slot in raw and isinstance(raw[slot], dict):
                entry = raw[slot]
                profiles[slot] = {
                    "slot": slot,
                    "mapping_id": str(entry.get("mapping_id") or SLOT_DEFAULT_MAPPING[slot]),
                    "enabled": bool(entry.get("enabled", False)),
                    "baudrate": entry.get("baudrate"),
                    "parity": entry.get("parity"),
                    "bytesize": entry.get("bytesize"),
                    "stopbits": entry.get("stopbits"),
                }
        return profiles

    def update_slot(self, slot: str, *, mapping_id: str | None = None,
                    enabled: bool = False, baudrate: int | None = None,
                    parity: str | None = None, bytesize: int | None = None,
                    stopbits: int | None = None) -> dict[str, Any]:
        """更新单槽配置；从 serial_ports.json 回填未显式给出的默认参数。"""
        if slot not in PROFILE_SLOTS:
            raise InvalidProfileError(f"未知槽：{slot}")
        profiles = self.load()
        current = profiles[slot]
        chosen = mapping_id or current["mapping_id"] or SLOT_DEFAULT_MAPPING[slot]
        mapping = self._mapping_by_id(chosen)

        resolved_baudrate = mapping["baudrate"] if baudrate is None else baudrate
        resolved_parity = mapping["parity"] if parity is None else parity
        resolved_bytesize = mapping["bytesize"] if bytesize is None else bytesize
        resolved_stopbits = mapping["stopbits"] if stopbits is None else stopbits
        _validate_serial_params(
            baudrate=int(resolved_baudrate),
            parity=str(resolved_parity),
            bytesize=int(resolved_bytesize),
            stopbits=float(resolved_stopbits),
        )
        profiles[slot] = {
            "slot": slot,
            "mapping_id": chosen,
            "enabled": bool(enabled),
            "baudrate": int(resolved_baudrate),
            "parity": str(resolved_parity),
            "bytesize": int(resolved_bytesize),
            "stopbits": float(resolved_stopbits),
        }
        self._write(profiles)
        return profiles[slot]

    def save(self) -> Path:
        """临时文件 + 原子替换保存到 runtime_dir。"""
        return self._write(self.load())

    def _write(self, payload: dict[str, Any]) -> Path:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".serial_profile.", suffix=".tmp", dir=str(self.runtime_dir),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.profile_path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return self.profile_path
