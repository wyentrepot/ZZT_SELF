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
# simcon 无固定映射：空串 = 自动选择可用串口（applier 侧透传，端口不锁定）
SLOT_DEFAULT_MAPPING = {
    "module_log.cco": "cco-main",
    "module_log.sta": "sta-main",
    "listener.main": "listener",
    "simcon.main": "",
}

# simcon 自动模式的缺省串口参数（1376.2 本地总线）
_SIMCON_AUTO_PARAMS = {"baudrate": 9600, "parity": "E", "bytesize": 8, "stopbits": 1}

_PROFILE_FILENAME = "serial_profile.json"

# update_slot 串口参数哨兵：UNSET = 未提供（回填映射/auto 缺省）；显式 None =
# 保存 null（清除已存参数，apply 时由使用侧回落 auto 语义）。与"未传"区分开，
# PUT 才能复原"从未配置"的 null 态（DEF-13）。
UNSET = object()


class UnknownMappingError(ValueError):
    """mapping_id 不在 serial_ports.json 中。"""


class InvalidProfileError(ValueError):
    """Profile 参数非法（未知槽、非法串口参数等）。"""


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

    def device_for(self, mapping_id: str, platform_name: str | None = None) -> str:
        """解析 mapping_id 到可打开的串口设备/COM 名（P4 apply 用）。"""
        return self._mapping_by_id(mapping_id)["device"]

    def mapping_params(self, mapping_id: str) -> dict[str, Any]:
        """映射缺省串口参数（baudrate/parity/bytesize/stopbits）。

        供 applier 对显式 null（未配置）的槽参数回落 auto 语义，与"从不保存
        参数直接 apply"的取值口径一致。
        """
        mapping = self._mapping_by_id(mapping_id)
        return {
            "baudrate": mapping["baudrate"], "parity": mapping["parity"],
            "bytesize": mapping["bytesize"], "stopbits": mapping["stopbits"],
        }

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
                    enabled: bool = False, baudrate: Any = UNSET,
                    parity: Any = UNSET, bytesize: Any = UNSET,
                    stopbits: Any = UNSET) -> dict[str, Any]:
        """更新单槽配置。

        - 串口参数未提供（UNSET）→ 从 serial_ports.json 回填默认参数（simcon
          自动口用 1376.2 缺省）。
        - 串口参数显式传 None → 保存 null（清除已存参数，可经 PUT 复原 null 态）。
        - mapping_id 显式传空串表示"自动"（仅 simcon.main 支持：自动选择可用
          串口，参数缺省用 1376.2 本地总线值）。
        """
        if slot not in PROFILE_SLOTS:
            raise InvalidProfileError(f"未知槽：{slot}")
        profiles = self.load()
        current = profiles[slot]
        if mapping_id is not None:
            chosen = str(mapping_id).strip()
        else:
            chosen = current["mapping_id"] or SLOT_DEFAULT_MAPPING[slot]
        if not chosen and slot != "simcon.main":
            raise InvalidProfileError(f"{slot} 必须选择映射")
        if chosen:
            mapping = self._mapping_by_id(chosen)
            fallback = {
                "baudrate": mapping["baudrate"], "parity": mapping["parity"],
                "bytesize": mapping["bytesize"], "stopbits": mapping["stopbits"],
            }
        else:
            fallback = dict(_SIMCON_AUTO_PARAMS)
        resolved: dict[str, Any] = {}
        for name, value in (("baudrate", baudrate), ("parity", parity),
                            ("bytesize", bytesize), ("stopbits", stopbits)):
            if value is UNSET:
                resolved[name] = fallback[name]
            else:
                # 显式 None（null）原样保存；显式值原样保存
                resolved[name] = value
        for name in ("baudrate", "bytesize", "stopbits"):
            if resolved[name] is not None:
                resolved[name] = int(resolved[name])
        if resolved["parity"] is not None:
            resolved["parity"] = str(resolved["parity"])
        if resolved["stopbits"] is not None:
            resolved["stopbits"] = float(resolved["stopbits"])
        # 仅校验给出的值（null 字段已被清除，不参与校验）
        if "baudrate" in resolved and resolved["baudrate"] is not None and resolved["baudrate"] <= 0:
            raise InvalidProfileError(f"非法波特率：{resolved['baudrate']}")
        if resolved["parity"] is not None and resolved["parity"] not in ("N", "E", "O", "M", "S"):
            raise InvalidProfileError(f"非法校验位：{resolved['parity']!r}")
        if resolved["bytesize"] is not None and resolved["bytesize"] not in (5, 6, 7, 8):
            raise InvalidProfileError(f"非法数据位：{resolved['bytesize']}")
        if resolved["stopbits"] is not None and resolved["stopbits"] not in (1, 1.5, 2):
            raise InvalidProfileError(f"非法停止位：{resolved['stopbits']}")
        profiles[slot] = {
            "slot": slot,
            "mapping_id": chosen,
            "enabled": bool(enabled),
            "baudrate": resolved["baudrate"],
            "parity": resolved["parity"],
            "bytesize": resolved["bytesize"],
            "stopbits": resolved["stopbits"],
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
