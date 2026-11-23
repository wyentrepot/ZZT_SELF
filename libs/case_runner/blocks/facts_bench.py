"""台架事实：``bench.simeter_online`` / ``bench.simcon_online`` / ``bench.sta_online``
（REQS §10.5 FT-1/FT-2/FT-3）。

**FT-2「事实探测复用块的判定函数」**：本文件**不另写判定逻辑**，只做三件事：

1. 从 ``params`` 解析能力实例（``base_url`` / ``transport`` / ``capability`` / ``mapping_id``）；
2. 调 ``blocks/common.py`` 的 :func:`judge_simeter` / :func:`judge_simcon` /
   :func:`judge_sta_online` 拿三态（与 ``fix.py`` / ``reset.py`` 用的是**同一个对象**；
   历史问题：判定曾长在 ``blocks/fix.py`` 里，逼得本文件反过来从 ````fix`` 取；
   现在公共件只在 ``common.py`` 一份，本文件**不 import 任何兄弟块模块**）；
3. 把注册名登记到事实注册表（``register_fact``）。

**FT-3「三态，unknown 永不折算成 yes」**：``unknown`` 只来自「我们没法知道」
（没注入 base_url / 连不上 / 传输异常），判定函数返回什么就如实向上传递，
本文件不做任何「乐观折算」。

参数（``params_schema``，见 ``data/facts/bench_*_online.json``）：

| 参数 | 类型 | 默认 | 含义 |
|---|---|---|---|
| ``base_url`` | str | 环境变量 ``WORKBENCH_BASE`` | 工作台地址，不写死端口 |
| ``transport`` | callable | urllib | 注入假 HTTP（单测用） |
| ``capability`` | object | — | 直接注入能力实例（优先级最高） |
| ``mapping_id`` | str | ``listener`` / ``simcon`` / ``sta-main`` | 逻辑口别名 |
| ``require_serial_open`` / ``require_port_open`` | bool | ``false`` | 严格口径：口未打开即判 ``no`` |
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from case_runner.blocks.common import (
    DEFAULT_MODULE_MAPPING,
    judge_simeter,
    judge_simcon,
    judge_sta_online,
    module_capability,
    simcon_capability,
    simeter_capability,
)
from case_runner.core.planner import FactValue
from case_runner.core.registry import has_fact, register_fact

#: 事实 id（与 ``data/facts/*.json`` 一一对应）
FACT_SIMETER_ONLINE = "bench.simeter_online"
FACT_SIMCON_ONLINE = "bench.simcon_online"
FACT_STA_ONLINE = "bench.sta_online"


def probe_simeter_online(params: Optional[Dict[str, Any]] = None) -> FactValue:
    """``bench.simeter_online``：模拟电表台架是否在线（``yes`` / ``no`` / ``unknown``）。"""
    raw = dict(params or {})
    capability = raw.get("capability") or simeter_capability(raw)
    return judge_simeter(capability,
                         require_serial_open=bool(raw.get("require_serial_open", False)))


def probe_simcon_online(params: Optional[Dict[str, Any]] = None) -> FactValue:
    """``bench.simcon_online``：模拟集中器台架是否在线（``yes`` / ``no`` / ``unknown``）。"""
    raw = dict(params or {})
    capability = raw.get("capability") or simcon_capability(raw)
    return judge_simcon(capability,
                        require_port_open=bool(raw.get("require_port_open", False)))


def probe_sta_online(params: Optional[Dict[str, Any]] = None) -> FactValue:
    """``bench.sta_online``：STA 控制台口（``sta-main``）是否可用。

    复位（``reset.sta``）走的是 ``sta-main`` 口发 ``reboot``，**不经过模拟集中器**，
    所以它需要的台架事实是「STA 口可用」而不是 ``bench.simcon_online``。
    判定复用 ``ModuleCapability.port_status``（串口桥只读 preflight，FT-2）。
    """
    raw = dict(params or {})
    capability = raw.get("capability") or module_capability(raw)
    return judge_sta_online(capability,
                            mapping_id=str(raw.get("mapping_id") or DEFAULT_MODULE_MAPPING))


def register() -> None:
    """注册三个台架事实（幂等：已注册则跳过）。"""
    if not has_fact(FACT_SIMETER_ONLINE):
        register_fact(FACT_SIMETER_ONLINE, probe_simeter_online)
    if not has_fact(FACT_SIMCON_ONLINE):
        register_fact(FACT_SIMCON_ONLINE, probe_simcon_online)
    if not has_fact(FACT_STA_ONLINE):
        register_fact(FACT_STA_ONLINE, probe_sta_online)


register()


__all__ = [
    "FACT_SIMETER_ONLINE",
    "FACT_SIMCON_ONLINE",
    "FACT_STA_ONLINE",
    "probe_simeter_online",
    "probe_simcon_online",
    "probe_sta_online",
    "judge_simeter",
    "judge_simcon",
    "judge_sta_online",
    "register",
]
