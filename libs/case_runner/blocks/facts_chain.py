"""事实链事实（REQS-0044 AC-5）：``archive.written`` / ``nwk.joined`` / ``sta.addr_obtained``。

「档案→组网→绑表→抄表」的顺序约束由事实链表达：后序块的 ``needs`` 由前序块的
``provides`` 满足（调换顺序会被规划层拦下）。这三条事实是**运行内事实**：

- **唯一合法的 yes 来源是块回写**：``archive.add`` / ``nwk.topology`` / ``meter.addr_probe``
  的步骤在有效态 pass 时经 executor 的 provides 通道写进事实缓存；
- **独立探测恒 unknown**：预检 / 台架快照单独探它们时，结论只能是「本轮还没跑过」
  （``unknown``），绝不假 ``yes`` —— 否则台架没跑建档也会被判「档案已写入」，
  正是 0044 §6.4 说的「跨参数/跨轮假 PASS」形态；
- 三条都**不参数化**（``parameterized`` 不声明、无必需参数）：它们描述「本轮」的
  事实，不是「某地址」的事实；带参结论（如 archive.contains）仍由参数化事实承担。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from case_runner.core.planner import FactValue
from case_runner.core.registry import has_fact, register_fact

#: 事实 id（与 ``data/facts/*.json`` 一一对应）
FACT_ARCHIVE_WRITTEN = "archive.written"
FACT_NWK_JOINED = "nwk.joined"
FACT_STA_ADDR_OBTAINED = "sta.addr_obtained"

#: 提供该事实的块（人话留痕用）
_PROVIDER = {
    FACT_ARCHIVE_WRITTEN: "archive.add（下发档案步骤）",
    FACT_NWK_JOINED: "nwk.topology（组网判定步骤）",
    FACT_STA_ADDR_OBTAINED: "meter.addr_probe（绑表取址步骤）",
}


def _probe_factory(fact_id: str):
    """生成一条「运行内事实」的独立探测体（恒 unknown，理由点名提供块）。"""

    def probe(params: Optional[Dict[str, Any]] = None) -> FactValue:
        return FactValue(
            state="unknown",
            detail=(
                "运行内事实：由 %s 的步骤在本轮执行成功后回写（provides 通道）；"
                "独立探测不产生结论，绝不假 yes（REQS-0044 AC-5）" % _PROVIDER[fact_id]
            ),
            evidence=[],
        )

    probe.__name__ = "probe_%s" % fact_id.replace(".", "_")
    probe.__doc__ = "%s：运行内事实，独立探测恒 unknown（yes 只能由块回写）" % fact_id
    return probe


probe_archive_written = _probe_factory(FACT_ARCHIVE_WRITTEN)
probe_nwk_joined = _probe_factory(FACT_NWK_JOINED)
probe_sta_addr_obtained = _probe_factory(FACT_STA_ADDR_OBTAINED)


def register() -> None:
    """注册三条事实链事实（幂等：已注册则跳过）。"""
    for fact_id, probe in (
        (FACT_ARCHIVE_WRITTEN, probe_archive_written),
        (FACT_NWK_JOINED, probe_nwk_joined),
        (FACT_STA_ADDR_OBTAINED, probe_sta_addr_obtained),
    ):
        if not has_fact(fact_id):
            register_fact(fact_id, probe)


register()


__all__ = [
    "FACT_ARCHIVE_WRITTEN",
    "FACT_NWK_JOINED",
    "FACT_STA_ADDR_OBTAINED",
    "probe_archive_written",
    "probe_nwk_joined",
    "probe_sta_addr_obtained",
    "register",
]
