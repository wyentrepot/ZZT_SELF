"""台架块：``fix.simeter_session`` / ``fix.simcon_session``（REQS §10.5 BL-1）。

判据（PLAN P2-2）：``probe → {ok, detail}``；台架不在线 → 块 **skipped + 原因**，不崩。

设计要点：

1. **判定逻辑只有一份**：块的判定与事实 ``bench.simeter_online`` / ``bench.simcon_online``
   / ``bench.sta_online``（``blocks/facts_bench.py``）共用 ``blocks/common.py`` 的
   :func:`judge_simeter` / :func:`judge_simcon` / :func:`judge_sta_online`
   （REQS §10.5 FT-2「事实探测复用块的断言函数，不另写一份判定逻辑」）。
   历史问题：这些判定曾经长在本文件里，逼得事实模块 ``facts_bench.py`` 反过来
   反过来从本文件取判定函数 —— 台架块与台架事实互相牵连，现已解耦。
2. **三态不倒向 pass**：能力可达 → ``yes``；工作台明确回 404（子应用未挂载）→ ``no``；
   连不上 / 没注入 base_url / 传输异常 → ``unknown``（我们没法知道台架状态，不能猜）。
3. **能力实例可注入**：块从 ``ctx.extras["simeter"]`` / ``ctx.extras["simcon"]`` 取，
   取不到才按 ``params`` 构造（``base_url`` / ``transport`` / ``mapping_id`` …），因此
   单测可以完全不碰真台架。
4. **``register()`` 幂等**：import 时自动注册；测试 ``clear_registries()`` 之后再调
   :func:`register` 即可重新注册（模块的 import 时注册已执行过，不能靠再次 import）。

``fix.simeter_session`` 的**开口**语义（REQS-0041 §10.13 台架联调修复）
-------------------------------------------------------------------
真实抄表链路里，模拟电表必须在串口上**张着嘴**（``POST /api/simeter/open``）才会自动
应答 STA 表口串行口（COM20 侧，非 485）发来的 645 请求；只探测 ``status()`` 会给出「服务可达就算在线」，
而表口没开 = 没人应答 = ``meter.*`` 必挂。所以本块：

1. ``status()`` → ``serial_open == true``：沿用已开口（**一次 open 都不发**）；
2. ``serial_open == false`` 且 ``ensure_open=true``（默认）→ ``open_port(mapping_id=<port>)``；
3. 再 ``status()`` **复核** ``serial_open``（open 返回 ok 不足以判就绪）；
4. 判定一律走 :func:`judge_simeter` 的 ``require_serial_open=True`` 严格口径。

**不自动关闭端口**：后续 ``meter.*`` 块要靠这条口在线应答，谁开谁关会扯不清；
本块只保证「张着嘴」，关闭由运维/会话收尾负责。

开口失败的错误分层（§10.13 裁决口径）::

    409 端口被占用        → 块 skipped（detail 点名「端口被占用」，先放掉占用方）
    404 子应用未挂载      → 块 skipped（台架缺失不等于设备失败）
    服务不可达/no_base_url → 块 skipped
    其他 4xx/5xx、bad_response、传输异常 → 块 unknown（拿不到结论，绝不折算 pass）
    开口成功 + 严格判定   → pass / fail（表口没开就是没就绪）

块参数（``action.params`` 给缺省，``ctx.block_params[block_id]`` 顶层键覆盖）：

=====================  =======  ==================================================
参数                    默认      含义
=====================  =======  ==================================================
``ensure_open``        ``true``  表口没开就自动 ``open_port()``（``false`` 只探测不开口）
``port``               ``"simeter"``  表口**逻辑口别名（mapping_id）**，不是 COM 号
``mapping_id``         ``"listener"`` 能力实例的逻辑口别名（能力层缺省用）
``require_serial_open`` / ``probe_timeout`` / ``base_url`` / ``transport`` …  沿用能力层口径
=====================  =======  ==================================================
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from case_runner.blocks.common import (  # noqa: F401 - 下面一并对旧调用方再导出
    DEFAULT_ENSURE_OPEN,
    DEFAULT_SIMETER_PORT,
    EXTRAS_SIMCON,
    EXTRAS_SIMETER,
    SIMETER_SESSION_ABSENT,
    SIMETER_SESSION_CONFLICT,
    SIMETER_SESSION_NOT_OPEN,
    SIMETER_SESSION_OPENED,
    SIMETER_SESSION_OPEN_OK,
    SIMETER_SESSION_REUSED,
    SIMETER_SESSION_UNKNOWN,
    SimconCapability,
    SimeterCapability,
    block_action_params,
    ensure_simeter_session,
    flag_of,
    judge_simeter,
    judge_simcon,
    simcon_capability,
    simeter_capability,
)
from case_runner.core.executor import BlockResult, RunContext
from case_runner.core.expect import (
    OBS_BENCH_UNREACHABLE,
    OBS_INCOMPLETE,
    OBS_SERIAL_BUSY,
    Observation,
)
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_UNKNOWN,
)
from case_runner.core.planner import FactValue
from case_runner.core.registry import has_block, register_block

#: 事实 / 块 id（与 ``data/blocks/*.json``、``data/facts/*.json`` 一一对应）
BLOCK_SIMETER_SESSION = "fix.simeter_session"
BLOCK_SIMCON_SESSION = "fix.simcon_session"
FACT_SIMETER_ONLINE = "bench.simeter_online"
FACT_SIMCON_ONLINE = "bench.simcon_online"

#: 开口门结论 → 块态（**开口/错误分层在 common，块态映射在这里**）
_SIMETER_GATE_BLOCK_STATE = {
    SIMETER_SESSION_CONFLICT: BLOCK_SKIPPED,   # 409 端口被占用
    SIMETER_SESSION_ABSENT: BLOCK_SKIPPED,     # 404 未挂载 / 服务不可达
    SIMETER_SESSION_NOT_OPEN: BLOCK_FAIL,      # 严格口径：口没开就是没就绪
    SIMETER_SESSION_UNKNOWN: BLOCK_UNKNOWN,    # 其他 4xx/5xx / bad_response
}


# --------------------------------------------------------------------------- #
# 块执行体
# --------------------------------------------------------------------------- #


def _bench_env_reason(detail: Any) -> str:
    """台架类观测的原因码（只描述「为什么没拿到」）：占用/忙 → serial_busy，其余 → bench_unreachable。"""
    text = str(detail or "")
    if any(token in text for token in ("409", "占用", "闸门", "会话忙", "busy")):
        return OBS_SERIAL_BUSY
    return OBS_BENCH_UNREACHABLE


def _bench_observation_result(
    observation: Observation,
    *,
    detail: str,
    facts: Mapping[str, FactValue] | None = None,
) -> BlockResult:
    """台架块的统一出口：只带观测（旧路径——步骤未声明期望——落 unknown）。"""
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail=detail,
        observation=observation,
        facts=dict(facts or {}),
    )


def _bench_result(fact_id: str, value: FactValue, *, label: str, field: str) -> BlockResult:
    """事实三态 → 只观测的块结果（REQS-0044 §4.2：块只报观测）。

    - ``yes``：观测 ``<field>就绪=True``，观测完整 → 步骤期望（``eq 就绪=true``）成立；
    - ``no`` / ``unknown``：台架不具备条件（服务明确没挂载 / 连不上）→ 原因码
      ``bench_unreachable``，步骤态 skipped（聚合落 blocked），绝不折成产品失败。
    """
    if value.satisfied:
        observation = Observation(
            values={f"{field}就绪": True},
            complete=True,
            reason_code="",
            detail=value.detail,
        )
    else:
        observation = Observation(
            values={},
            complete=False,
            reason_code=_bench_env_reason(value.detail),
            detail=f"{label} 台架前置未满足：{fact_id}={value.state}（{value.detail}）",
        )
    return _bench_observation_result(
        observation, detail=observation.detail, facts={fact_id: value}
    )


def fix_simeter_session(params: Optional[Dict[str, Any]] = None,
                        ctx: Optional[RunContext] = None) -> BlockResult:
    """``fix.simeter_session``：把模拟电表**表口张到能应答**（没开就开，开完不关）——只报观测。

    观测字段：``表口就绪`` / ``串口开`` / ``开口动作``。判据（表口必须就绪）由步骤
    ``expect`` 声明（``eq 表口就绪=true``）。开口失败的错误分层走原因码：
    409 占用 → ``serial_busy``；404 未挂载 / 服务不可达 → ``bench_unreachable``；
    其余 4xx/5xx、bad_response → ``incomplete``（说不清）。
    """
    raw = dict(params or {})
    action = block_action_params(raw)
    capability = simeter_capability(raw, ctx)
    port = str(action.get("port") or DEFAULT_SIMETER_PORT)
    ensure_open = flag_of(action.get("ensure_open"), DEFAULT_ENSURE_OPEN)

    gate = ensure_simeter_session(capability, mapping_id=port, ensure_open=ensure_open)
    # 判定只有一份（FT-2）：复用开口门最后一次 status 快照，不重复发 HTTP。
    value = judge_simeter(capability, require_serial_open=True, status=gate.get("status"))
    facts = {FACT_SIMETER_ONLINE: value}
    marker = f"{FACT_SIMETER_ONLINE}={value.state}（{value.detail}）"
    open_action = str(gate.get("detail") or "")

    if gate["outcome"] in SIMETER_SESSION_OPEN_OK:
        if value.satisfied:
            observation = Observation(
                values={"表口就绪": True, "串口开": True, "开口动作": open_action},
                complete=True,
                reason_code="",
                detail=f"{value.detail}；{open_action}",
            )
            return _bench_observation_result(
                observation, detail=observation.detail, facts=facts
            )
        # 理论不可达（开口门已确认 serial_open）：仍然不倒向就绪。
        observation = Observation(
            values={"表口就绪": False, "串口开": True, "开口动作": open_action},
            complete=True,
            reason_code="",
            detail=f"表口已打开但严格判定未就绪：{marker}",
        )
        return _bench_observation_result(
            observation, detail=observation.detail, facts=facts
        )

    outcome = str(gate["outcome"] or "")
    detail = f"{marker}；{gate['detail']}"
    if outcome in (SIMETER_SESSION_CONFLICT, SIMETER_SESSION_ABSENT):
        # 409 占用 / 404 未挂载 / 服务不可达 = 台架不具备条件（原因码 → 步骤 skipped）
        code = _bench_env_reason(gate.get("detail") or detail)
    elif outcome == SIMETER_SESSION_NOT_OPEN:
        # 表口确实没开（ensure_open=false 或开了仍关）：如实观测，判据归步骤期望
        observation = Observation(
            values={"表口就绪": False, "串口开": False, "开口动作": open_action},
            complete=True,
            reason_code="",
            detail="模拟电表表口未打开（严格口径：表口必须打开并能应答）：" + detail,
        )
        return _bench_observation_result(
            observation, detail=observation.detail, facts=facts
        )
    else:
        code = OBS_INCOMPLETE
    observation = Observation(values={}, complete=False, reason_code=code, detail=detail)
    return _bench_observation_result(observation, detail=detail, facts=facts)


def fix_simcon_session(params: Optional[Dict[str, Any]] = None,
                       ctx: Optional[RunContext] = None) -> BlockResult:
    """``fix.simcon_session``：模拟集中器是否在线——只报观测（``simcon就绪``）。"""
    raw = dict(params or {})
    capability = simcon_capability(raw, ctx)
    value = judge_simcon(capability,
                         require_port_open=bool(raw.get("require_port_open", False)))
    return _bench_result(
        FACT_SIMCON_ONLINE, value, label="模拟集中器", field="simcon"
    )


# --------------------------------------------------------------------------- #
# 注册（幂等）
# --------------------------------------------------------------------------- #


def register() -> None:
    """注册本模块的**块**执行体（幂等：已注册则跳过）。

    事实探测体 ``bench.simeter_online`` / ``bench.simcon_online`` / ``bench.sta_online``
    由 ``blocks/facts_bench.py`` 注册，其判定逻辑与块共用 ``blocks/common.py`` 的那一份
    （FT-2：不另写一份逻辑）。
    """
    if not has_block(BLOCK_SIMETER_SESSION):
        register_block(BLOCK_SIMETER_SESSION, fix_simeter_session)
    if not has_block(BLOCK_SIMCON_SESSION):
        register_block(BLOCK_SIMCON_SESSION, fix_simcon_session)


register()


__all__ = [
    "BLOCK_SIMETER_SESSION",
    "BLOCK_SIMCON_SESSION",
    "FACT_SIMETER_ONLINE",
    "FACT_SIMCON_ONLINE",
    "EXTRAS_SIMETER",
    "EXTRAS_SIMCON",
    "SimeterCapability",
    "SimconCapability",
    "simeter_capability",
    "simcon_capability",
    "ensure_simeter_session",
    "flag_of",
    "block_action_params",
    "DEFAULT_SIMETER_PORT",
    "DEFAULT_ENSURE_OPEN",
    "SIMETER_SESSION_REUSED",
    "SIMETER_SESSION_OPENED",
    "SIMETER_SESSION_NOT_OPEN",
    "SIMETER_SESSION_CONFLICT",
    "SIMETER_SESSION_ABSENT",
    "SIMETER_SESSION_UNKNOWN",
    "SIMETER_SESSION_OPEN_OK",
    "judge_simeter",
    "judge_simcon",
    "fix_simeter_session",
    "fix_simcon_session",
    "register",
]
