"""事实：``sta.meter_addr_known(addr)`` —— 「STA 是否已知该表地址」（REQS §10.5 FT-1/FT-3）。

探测面（只读观测，不产生激励）：下发 02H-F1 数据转发（透传 645 ``13H`` 读通信地址），
看上行 02H-F1 内嵌 645 的地址域是否就是 ``addr``。

**判定逻辑不在这里另写**（FT-2）：下发+取帧用 ``blocks/meter.py`` 的
:func:`probe_forward_reply`，判据用同一个 :func:`judge_meter_addr`。本文件只做
「块态 → 事实三态」的映射：

============================  ==================================================
探测结果                      事实
============================  ==================================================
内层 645 地址域 == addr        ``yes``
内层 645 地址域 != addr        ``no``（明确探测到，只是不匹配）
服务不可达 / 404 / 409 / 超时
/ 无上行帧 / 无内层 645 嵌套   ``unknown``（**不判 no**：没探到 ≠ 不存在）
============================  ==================================================

入参（``FactsContext.params["sta.meter_addr_known"]``，即执行器调用 ``fn(params)`` 的
``params``；事实探测体拿不到 ``ctx``，能力句柄必须从这里注入）：

- ``addr``            期望表地址（12 位 BCD hex，缺省 ``000000000001``）
- ``simcon``          :class:`case_runner.capabilities.simcon.SimconCapability`
                      （**缺省=None → ``unknown``**：没有下发能力就不假装知道）
- ``frames``          可选，:class:`case_runner.capabilities.frames.FramesCapability`
- ``protocol``        1376.2「通信协议类型」，缺省 2（645-2007）
- ``expect_timeout``  等上行应答秒数，缺省 8
- ``payload``         可选，直接给内嵌 645 请求帧 hex
"""

from __future__ import annotations

from typing import Any, Mapping

from case_runner.capabilities.frames import FramesCapability
from case_runner.core.models import FACT_NO, FACT_UNKNOWN, FACT_YES
from case_runner.core.planner import FactValue
from case_runner.core.registry import has_fact, register_fact

from .meter import (
    CONTROL_READ_ADDR,
    DEFAULT_ADDR,
    DEFAULT_EXPECT_TIMEOUT,
    PROTOCOL_645_2007,
    build_645_request,
    judge_meter_addr,
    norm_hex,
    probe_forward_reply,
)

#: 事实 id（与 ``data/facts/sta_meter_addr_known.json`` 的 ``id`` 一致）
FACT_ID = "sta.meter_addr_known"


def probe_sta_meter_addr_known(params: Mapping[str, Any] | None = None) -> FactValue:
    """探测 STA 是否已知该表地址：``yes`` / ``no`` / ``unknown``。"""
    options = dict(params or {})
    addr = norm_hex(options.get("addr")) or DEFAULT_ADDR
    frames = options.get("frames")
    if frames is None:
        frames = FramesCapability()

    simcon = options.get("simcon") or options.get("simcon_capability")
    if simcon is None:
        # 没有下发能力 ≠ 表地址未知：给 unknown，绝不给 no / yes
        return FactValue(
            state=FACT_UNKNOWN,
            detail="未注入 simcon 能力（params['simcon']），无法探测 STA 是否已知表地址",
        )

    payload = norm_hex(options.get("payload"))
    if not payload:
        payload, reason = build_645_request(
            frames, addr=addr, control=CONTROL_READ_ADDR
        )
        if not payload:
            return FactValue(
                state=FACT_UNKNOWN,
                detail="内嵌 645 13H 请求帧构帧失败，无法探测：%s" % reason,
            )

    outcome = probe_forward_reply(
        simcon,
        payload,
        protocol_type=options.get("protocol", PROTOCOL_645_2007),
        expect_timeout=options.get("expect_timeout", DEFAULT_EXPECT_TIMEOUT),
        max_poll_seconds=options.get("max_poll_seconds"),
        name="事实探测：STA 是否已知表地址 %s（02H-F1 透传 645 13H）" % addr,
    )
    if outcome.state != "reply":
        # 探测不到（不可达/无会话/超时/无上行帧）一律 unknown —— 不折算 yes，也不判 no
        return FactValue(
            state=FACT_UNKNOWN,
            detail="未探测到上行 02H-F1 应答帧：%s" % outcome.detail,
        )

    verdict = judge_meter_addr(
        outcome.reply_frame,
        addr,
        frames=frames,
        protocol_type=options.get("protocol", PROTOCOL_645_2007),
    )
    if verdict.state == "pass":
        return FactValue(
            state=FACT_YES,
            detail="STA 已知表地址 %s：%s" % (addr, verdict.detail),
            evidence=[outcome.reply_frame],
        )
    if verdict.state == "fail":
        return FactValue(
            state=FACT_NO,
            detail="已探测到上行 02H-F1，但地址不匹配：%s" % verdict.detail,
            evidence=[outcome.reply_frame],
        )
    return FactValue(
        state=FACT_UNKNOWN,
        detail="上行 02H-F1 已收到但取不到地址证据：%s" % verdict.detail,
        evidence=[outcome.reply_frame],
    )


def register() -> None:
    """注册事实探测体（**幂等**，与 ``blocks/__init__.py`` 约定一致）。"""
    if not has_fact(FACT_ID):
        register_fact(FACT_ID, probe_sta_meter_addr_known)


register()


__all__ = ["FACT_ID", "probe_sta_meter_addr_known", "register"]
