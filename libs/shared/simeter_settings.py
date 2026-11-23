"""模拟电表设置持久化（``data/runtime/simeter_settings.json``，REQS-0042 / S1.6）。

为什么要有这个文件
------------------
用户现场（2026-11-23）指出的真缺陷：模拟电表**没有持久化设置**（对比 simcon 有
``data/runtime/simcon_settings.json``），``MeterBrain.__init__(addr="000000000001")``
是硬编码默认值。今天重启工作台后，现场配好的 ``123456789012`` 直接丢回
``000000000001``，STA「搜表」阶段取不到表、链路失败。

于是照 ``shared/simcon_settings.py`` 的范式落一层设置存储：

- 文件：``<repo>/data/runtime/simeter_settings.json``（冻结态 exe 同级 ``runtime/``；
  ``data/runtime/`` 已被 ``.gitignore`` 覆盖）；
- 内容：``{"addr": "...", "version": "2007"}``（只有「表配置」这类该跨重启保留的项；
  串口参数由工作台串口目录管，不在这里）；
- 写入：临时文件 + ``os.replace`` 原子替换（半截文件不会成为下次启动的配置）；
- 读坏：坏 JSON / 非法地址 → **回落默认值**，不抛异常（设置层是增强，不阻塞业务）。

默认值**不硬编码**：一律取 :func:`shared.bench_defaults.simeter_addr` /
:func:`shared.bench_defaults.simeter_version`，即 ``config/case_defaults.json`` 的
``simeter.addr`` / ``simeter.version``，与用例默认地址、档案默认表地址同源。

谁读它：
- ``sim_meter.api.SimMeterService`` 启动时（服务自己装配电表脑的情况）；
- 谁写它：``POST /api/simeter/config`` 改完地址/版本后。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from shared.bench_defaults import simeter_addr, simeter_version

_FILENAME = "simeter_settings.json"

#: 645 版本归一（"07"/"2007" → 2007；"97"/"1997" → 1997）
_VERSION_ALIASES: dict[str, str] = {
    "07": "2007", "2007": "2007",
    "97": "1997", "1997": "1997",
}

#: 地址长度（12 位十六进制 / 十进制 BCD 人读口径）
ADDR_LEN = 12


def default_runtime_dir() -> Path:
    """运行时可写目录：冻结态 exe 同级 runtime/，开发态仓库 data/runtime/。"""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime"
    # libs/shared/simeter_settings.py → 仓库根
    return Path(__file__).resolve().parents[2] / "data" / "runtime"


def default_settings() -> dict[str, Any]:
    """默认设置：地址/版本都来自 ``config/case_defaults.json``（绝不硬编码引擎默认值）。

    现场层没配地址时 ``addr`` 为空串——服务层此时才回落到 ``MeterBrain`` 类默认
    （引擎层兜底，本层不复制那个常量）。
    """

    return {"addr": simeter_addr(), "version": simeter_version()}


def _normalise_addr(value: Any) -> str:
    """12 位十六进制地址 → 大写去空白；非法 → ``""``。"""

    text = "" if value is None else str(value).strip().upper().replace(" ", "")
    if len(text) != ADDR_LEN or any(c not in "0123456789ABCDEF" for c in text):
        return ""
    return text


def _normalise_settings(raw: Any) -> dict[str, Any]:
    """持久化内容 → 合法设置（非法字段逐项回落默认值）。"""

    fallback = default_settings()
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out
    addr = _normalise_addr(raw.get("addr"))
    if addr:
        out["addr"] = addr
    version = "" if raw.get("version") is None else str(raw.get("version")).strip()
    out["version"] = _VERSION_ALIASES.get(version, fallback["version"])
    return out


class SimeterSettingsStore:
    """模拟电表设置的读写；load 失败静默降级为默认值（本层是增强，不阻塞业务）。"""

    def __init__(self, runtime_dir: Path | str | None = None):
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else default_runtime_dir()

    @property
    def path(self) -> Path:
        return self.runtime_dir / _FILENAME

    def load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return default_settings()
        return _normalise_settings(raw)

    def save(self, settings: dict[str, Any]) -> Path:
        payload = {"_schema": 1, **_normalise_settings(settings)}
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".simeter_settings.", suffix=".tmp", dir=str(self.runtime_dir),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return self.path

    # -- 便捷读取 ---------------------------------------------------------
    def addr(self) -> str:
        """当前地址（没持久化 / 非法 → 台架默认地址，可能是空串）。"""

        return str(self.load()["addr"])

    def version(self) -> str:
        return str(self.load()["version"])

    def public(self) -> dict[str, Any]:
        """API / 诊断用：当前值 + 默认值 + 文件位置。"""

        return {
            "settings": self.load(),
            "defaults": default_settings(),
            "path": str(self.path),
        }


def load_settings(runtime_dir: Path | str | None = None) -> dict[str, Any]:
    return SimeterSettingsStore(runtime_dir).load()


def save_settings(settings: dict[str, Any],
                  runtime_dir: Path | str | None = None) -> Path:
    return SimeterSettingsStore(runtime_dir).save(settings)


__all__ = [
    "ADDR_LEN",
    "SimeterSettingsStore",
    "default_runtime_dir",
    "default_settings",
    "load_settings",
    "save_settings",
]
