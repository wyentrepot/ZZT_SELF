"""两层默认配置（REQS-0042 变更 2 · **D2-2**）：出厂默认 + 现场覆盖。

为什么有这个文件
----------------
D2-2 的原话是「地址之类的，需要提前写好默认地址，没有特殊需求就用默认的」。落地成两层：

1. **出厂默认** ``libs/case_runner/data/case_defaults.json``（随代码走、可 diff、进 git）：
   台架真实表地址 ``123456789012`` + 三个逻辑口名 ``simcon`` / ``simeter`` / ``sta-main``。
2. **现场覆盖** ``config/case_defaults.json``（部署现场改、**不存在就跳过**）：
   按**顶层键**覆盖；``mapping_id`` 例外——按**子键**合并，只写一个角色不影响其它角色。

于是「块/事实声明不再各自写死地址」：声明里写 ``"meter_addr": null`` 表示「取默认」，
``blocks/loader.py`` 加载时把它解析成 :func:`default_meter_addr` 的值。调用方「没有特殊需求
就不传参」，``--params`` 仍可覆盖。

口径（照 REQS 硬约束，不是实现细节）
------------------------------------
- **读不到就空**：两个文件都不存在 / 读不了 / JSON 坏了 → 返回 ``{}``，
  :func:`default_meter_addr` 返回 ``""``，:func:`default_mapping_id` 返回 ``""``；
  本模块**绝不抛异常**（默认值缺失是配置问题，不该在 import/加载期炸掉 CLI）。
- **缓存按文件 mtime+size 失效**：性能上不必每次读盘，但 ``config/`` 一改就立刻生效
  （不要求调用方记得 ``refresh=True``）。
- **不做猜测**：配置里写了非法表地址（非 12 位十进制）→ :func:`default_meter_addr` 返回
  ``""``，而不是把垃圾字符串当地址发到台架。
- **分层铁律**：本文件只依赖 stdlib，**不 import** ``apps.*`` / ``parser_lib`` /
  能力层（``test_layering.py`` 铁律 1 守门）；也不 import ``blocks.*``（方向相反）。

地址换算**不在本文件**：唯一实现处是 ``blocks/common.py`` 的 ``archive_addr_of`` /
``meter_addr_wire``（D2-1「不得在别处再写第二份反转逻辑」）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

#: ``libs/case_runner``（本包根）
CASE_RUNNER_ROOT: Path = Path(__file__).resolve().parents[1]
#: 仓库根（``libs/case_runner/core/defaults.py`` 上溯 4 层）
REPO_ROOT: Path = CASE_RUNNER_ROOT.parents[1]

#: 出厂默认（随代码走；不存在 → 视为空配置）
DATA_DEFAULTS_FILE: Path = CASE_RUNNER_ROOT / "data" / "case_defaults.json"
#: 现场覆盖（部署时改；不存在 → 不覆盖）
CONFIG_OVERRIDE_FILE: Path = REPO_ROOT / "config" / "case_defaults.json"

#: ``mapping_id`` 支持的逻辑口角色（D2-2 的三个口名）
MAPPING_ROLES: tuple[str, ...] = ("simcon", "simeter", "sta")
#: 需要按子键合并的键（其它键一律整值覆盖）
MAPPING_KEY = "mapping_id"

#: 表地址长度（12 位十进制 BCD，人读口径）
ADDR_LEN = 12
#: 以 ``_`` 开头的键是**说明文字**，不算配置项（不进 ``defaults_sources``）
NOTE_PREFIX = "_"

# 缓存：(签名, 合并结果, 来源)；签名 = 两个文件的 (路径, mtime_ns, size|None)
_CACHE: dict[str, Any] = {"signature": None, "data": {}, "sources": {}}


# --------------------------------------------------------------------------- #
# 读盘与合并
# --------------------------------------------------------------------------- #


def _file_signature(path: Path) -> tuple[str, int | None, int | None]:
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
    except OSError:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _merge(factory: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """合并两层配置：顶层键覆盖；``mapping_id`` 按子键合并。

    ``override`` 里值为 ``null`` 的键**不覆盖**（不能用一个空值把出厂默认抹掉——
    「null = 取默认」是声明层的语义，不是配置层的语义）。
    """

    merged: dict[str, Any] = dict(factory)
    for key, value in override.items():
        if value is None:
            continue
        if key == MAPPING_KEY:
            base = dict(merged.get(key) or {}) if isinstance(merged.get(key), Mapping) else {}
            if isinstance(value, Mapping):
                base.update({str(name): item for name, item in value.items() if item is not None})
            merged[key] = base
            continue
        merged[key] = value
    return merged


def _sources_of(
    factory: Mapping[str, Any],
    override: Mapping[str, Any],
    factory_path: Path,
    override_path: Path,
) -> dict[str, str]:
    """``{键: 该键实际来自哪个文件}``；嵌套键用点号（``mapping_id.simcon``）。"""

    factory_text = Path(factory_path).as_posix()
    override_text = Path(override_path).as_posix()
    sources: dict[str, str] = {}
    # 先记出厂，再用覆盖层改写（覆盖层写了哪个键，就把哪个键的来源指过去）
    for key, value in factory.items():
        if str(key).startswith(NOTE_PREFIX):
            continue
        if key == MAPPING_KEY and isinstance(value, Mapping):
            for role in value:
                sources[f"{MAPPING_KEY}.{role}"] = factory_text
            continue
        sources[str(key)] = factory_text
    for key, value in override.items():
        if value is None or str(key).startswith(NOTE_PREFIX):
            continue
        if key == MAPPING_KEY and isinstance(value, Mapping):
            for role in value:
                if value[role] is not None:
                    sources[f"{MAPPING_KEY}.{role}"] = override_text
            continue
        sources[str(key)] = override_text
    return sources


def _refresh_cache() -> tuple[dict[str, Any], dict[str, str]]:
    """按文件签名重读两层配置（签名没变就直接用缓存）。"""

    data_path = Path(DATA_DEFAULTS_FILE)
    config_path = Path(CONFIG_OVERRIDE_FILE)
    signature = (_file_signature(data_path), _file_signature(config_path))
    if _CACHE["signature"] == signature:
        return _CACHE["data"], _CACHE["sources"]

    factory = _read_mapping(data_path)
    override = _read_mapping(config_path)
    data = _merge(factory, override)
    _CACHE["signature"] = signature
    _CACHE["data"] = data
    _CACHE["sources"] = _sources_of(factory, override, data_path, config_path)
    return data, _CACHE["sources"]


def reset_cache() -> None:
    """清空缓存（测试 / 换配置用；平时不需要，签名会自动失效）。"""

    _CACHE["signature"] = None
    _CACHE["data"] = {}
    _CACHE["sources"] = {}


# --------------------------------------------------------------------------- #
# 公开接口（其他子代理按此契约写代码，名字不得改）
# --------------------------------------------------------------------------- #


def load_defaults(refresh: bool = False) -> dict[str, Any]:
    """两层合并后的默认配置（``refresh=True`` 强制重读盘）。

    返回的是**副本**：调用方改它不会污染缓存。两个文件都读不到 → ``{}``。
    """

    if refresh:
        reset_cache()
    data, _ = _refresh_cache()
    merged: dict[str, Any] = dict(data)
    mapping = merged.get(MAPPING_KEY)
    if isinstance(mapping, Mapping):
        merged[MAPPING_KEY] = dict(mapping)
    return merged


def default_for(key: str, fallback: Any = None) -> Any:
    """按键取默认值（取不到 → ``fallback``）。

    ``key`` 支持点号取子键：``default_for("mapping_id.sta")``。用点号且首段不是
    ``mapping_id`` 时按「查无此键」处理（不做任意深挖）。
    """

    data = load_defaults()
    if not key:
        return fallback
    if "." in key:
        head, _, tail = key.partition(".")
        nested = data.get(head)
        if not isinstance(nested, Mapping) or head != MAPPING_KEY:
            return fallback
        value = nested.get(tail)
    else:
        value = data.get(key)
    return fallback if value is None else value


def default_meter_addr() -> str:
    """默认电表 645 人读地址（12 位）；读不到或非法 → ``""``。"""

    value = default_for("meter_addr", "")
    text = "" if value is None else str(value).strip().replace("-", "")
    if len(text) == ADDR_LEN and text.isdigit():
        return text
    return ""


def default_mapping_id(role: str) -> str:
    """按逻辑口角色取默认 ``mapping_id``（``simcon`` / ``simeter`` / ``sta``）。

    取不到 → ``""``（**不猜**：调用方要么显式传，要么自己回落到自己的常量）。
    """

    name = str(role or "").strip()
    if not name:
        return ""
    value = default_for(f"{MAPPING_KEY}.{name}", None)
    if value is None and "-" in name:
        # 容错：``sta-main`` 这类「角色-别名」写法按前段再找一次
        value = default_for(f"{MAPPING_KEY}.{name.split('-', 1)[0]}", None)
    text = "" if value is None else str(value).strip()
    return text


def defaults_sources() -> dict[str, str]:
    """``{键: 该键实际来自哪个文件}``（诊断 / ``case_run show`` 用）。

    值为文件的**绝对路径**（posix 写法），由调用方决定怎么缩短展示；
    嵌套键写成 ``mapping_id.simcon``；``_`` 开头的说明键不在其中。
    """

    _, sources = _refresh_cache()
    return dict(sources)


__all__ = [
    "CASE_RUNNER_ROOT",
    "REPO_ROOT",
    "DATA_DEFAULTS_FILE",
    "CONFIG_OVERRIDE_FILE",
    "MAPPING_ROLES",
    "MAPPING_KEY",
    "ADDR_LEN",
    "load_defaults",
    "default_for",
    "default_meter_addr",
    "default_mapping_id",
    "defaults_sources",
    "reset_cache",
]
