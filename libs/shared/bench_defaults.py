"""台架默认值**唯一来源**（REQS-0042 / D2-2 / S1.6）：``config/case_defaults.json``。

为什么要有这一层
----------------
现场两个真缺陷：

1. **模拟电表的表地址重启就回默认**：``MeterBrain.__init__(addr="000000000001")``
   是硬编码默认，而模拟电表此前没有持久化设置（对比 simcon 有
   ``data/runtime/simcon_settings.json``）。工作台一重启，现场配好的
   ``123456789012`` 直接丢回 ``000000000001``，STA「搜表」阶段取不到表、链路失败。
2. **默认地址不统一**：用例/档案默认表地址是 ``123456789012``
   （``libs/case_runner/data/case_defaults.json`` + 现场覆盖层），模拟电表默认却是
   ``000000000001``。用户原话：「000000000001 这个默认地址，应该与 CCO 档案默认表地址
   一致才对，这个复位就恢复 000000000001 不应该」。

于是：台架默认值只写在 ``<repo>/config/case_defaults.json`` 一处，谁要用默认谁来读：

- ``case_runner.core.defaults``（用例/档案默认表地址）——同一文件的
  ``meter_addr`` 键，按「出厂 + 现场」两层合并；
- ``shared.simeter_settings.default_settings``（模拟电表未持久化时的默认）
  ——本模块的 :func:`simeter_addr` / :func:`simeter_version`；
- 其它新功能需要默认值时也走本模块，**不要各自写死**。

口径（照项目硬约束，不是实现细节）
----------------------------------
- **读不到就空**：文件不存在 / 读不了 / JSON 坏 → :func:`load` 返回 ``{}``，
  :func:`meter_addr` 返回调用方给的 ``fallback``；本模块**绝不抛异常**
  （默认值缺失是配置问题，不该在 import/服务启动期炸掉）。
- **缓存按文件 mtime + size 失效**：不必每次读盘，``config/`` 一改就生效；
  ``refresh=True`` 强制重读（同长度 JSON 在同一文件系统时间 tick 内写入时，
  签名可能不变，此时显式 ``refresh=True``）。
- **分层铁律**：只依赖标准库，**不 import** ``apps.*`` / ``sim_meter.*`` /
  ``case_runner.*`` / ``sim_concentrator.*``（否则服务层会被能力层反向绑死）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

#: 仓库根（``libs/shared/bench_defaults.py`` 上溯 3 层：shared → libs → 仓库根）
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

#: 台架现场默认值（唯一来源；部署现场改这一处）
DEFAULTS_FILE: Path = REPO_ROOT / "config" / "case_defaults.json"

#: 645 版本归一：配置里写 "07"/"2007" 都认成 2007，写 "97"/"1997" 认成 1997
_VERSION_ALIASES: dict[str, str] = {
    "07": "2007", "2007": "2007",
    "97": "1997", "1997": "1997",
}

# 缓存：(文件签名, 内容)；签名 = (路径, mtime_ns|None, size|None)
_CACHE: dict[str, Any] = {"signature": None, "data": {}}


# --------------------------------------------------------------------------- #
# 读盘
# --------------------------------------------------------------------------- #
def _signature(path: Path) -> tuple[str, int | None, int | None]:
    """文件的缓存签名：不存在 → ``(path, None, None)``；存在 → mtime_ns + size。"""

    try:
        stat = Path(path).stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _read_mapping(path: Path) -> dict[str, Any]:
    """读一个 JSON 对象；读不到 / 不是对象 / JSON 坏 → ``{}``（绝不抛）。"""

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def reset_cache() -> None:
    """清空缓存（测试 / 换配置用；平时不需要，签名会自动失效）。"""

    _CACHE["signature"] = None
    _CACHE["data"] = {}


def load(refresh: bool = False) -> dict[str, Any]:
    """``config/case_defaults.json`` 的内容（``refresh=True`` 强制重读盘）。

    返回**副本**：调用方改它不会污染缓存。读不到 → ``{}``（不抛异常）。
    """

    signature = _signature(DEFAULTS_FILE)
    if refresh or _CACHE["signature"] != signature:
        _CACHE["data"] = _read_mapping(DEFAULTS_FILE)
        _CACHE["signature"] = signature
    data = dict(_CACHE["data"])
    nested = data.get("simeter")
    if isinstance(nested, Mapping):
        data["simeter"] = dict(nested)
    return data


# --------------------------------------------------------------------------- #
# 取键（都是「取不到 → fallback」的宽容口径）
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    """任意值 → 去空白字符串（``None`` → ``""``）。"""

    return "" if value is None else str(value).strip()


def meter_addr(fallback: str = "") -> str:
    """台架默认电表 645 人读地址（12 位）；没配 → ``fallback``。

    这是**用例/档案默认地址**与**模拟电表默认地址**的共同来源。
    """

    return _text(load().get("meter_addr")) or fallback


def simeter_addr(fallback: str = "") -> str:
    """模拟电表默认地址：优先 ``simeter.addr``，否则回落 :func:`meter_addr`。

    ``fallback`` 只在两处都取不到时才用（例如现场既没写 simeter 段也没写 meter_addr）。
    """

    nested = load().get("simeter")
    if isinstance(nested, Mapping):
        text = _text(nested.get("addr"))
        if text:
            return text
    return meter_addr(fallback)


def simeter_version(fallback: str = "2007") -> str:
    """模拟电表默认 645 版本（``"2007"`` / ``"1997"``）；没配或非法 → ``fallback``。

    现场层可直接写 ``"07"`` / ``"97"`` 简写，这里统一归一成引擎口径。
    """

    nested = load().get("simeter")
    text = ""
    if isinstance(nested, Mapping):
        text = _text(nested.get("version"))
    return _VERSION_ALIASES.get(text, fallback)


__all__ = [
    "REPO_ROOT",
    "DEFAULTS_FILE",
    "load",
    "reset_cache",
    "meter_addr",
    "simeter_addr",
    "simeter_version",
]
