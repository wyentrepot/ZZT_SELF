"""workbench.orchestration.feedback —— 归因规则引擎（FR-5.4 落地）。

验证失败时，把结构化失败原因（缺失事件/超时/帧匹配失败/规则漂移）组装为
给编码模型/工程师的反馈，形成"失败 → 归因 → 修复 → 再验证"回路。

规则表 JSON 可配置（NFR-3 配置驱动）：
  {"rules": [
     {"when": {"compare.negated": ["join.assoc.err"]},
      "then": "关联流程异常：检查 assoc 相关打印与信标接收，重点看 NID 分配"},
     {"when": {"compare.missing": ["collect.minute.e4"]},
      "then": "分钟上报缺失：检查采集任务配置（OI 6000/6001）与集中器是否下发抄读帧"}
  ]}

输出：结构化反馈 [{issue, evidence, suggestion}]，接口先行，消费方后续对接。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_RULES: Dict[str, Any] = {
    "rules": [
        {
            "when": {"compare.negated": ["join.assoc.err", "assoc err", "assoc_err"]},
            "then": "关联流程异常：检查 assoc 相关打印与信标接收，重点看 NID 分配",
        },
        {
            "when": {"compare.missing": ["collect.minute.e4", "collect.minute"]},
            "then": "分钟上报缺失：检查采集任务配置（OI 6000/6001）与集中器是否下发抄读帧",
        },
        {
            "when": {"compare.timeouts": ["onnet", "network.onnet"]},
            "then": "入网超时：检查 CCO 信标发送频率与 STA 入网参数（beacon interval / NID 广播）",
        },
        {
            "when": {"compare.out_of_order": []},
            "then": "流程顺序错乱：检查状态机时序，比对期望流程的步骤顺序",
        },
        {
            "when": {"simcon.summary.fail": True},
            "then": "模拟集中器断言失败：检查下发帧的 AFN/SEQ/RTUA 信封字段与嵌套数据项",
        },
        {
            "when": {"loghooks.drift": True},
            "then": "规则行号漂移：运行 rules diff 更新规则文件，再回归命中率",
        },
    ]
}


def _load_rules(path: Optional[Path] = None) -> List[dict]:
    if path and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("rules", [])
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_RULES["rules"]


def _match_rule(rule: dict, ctx: Dict[str, Any]) -> Optional[str]:
    """规则 when 子句匹配：ctx 为 {compare.missing: [...], compare.timeouts: [...], ...}。"""
    when = rule.get("when", {})
    for key, expected in when.items():
        actual = ctx.get(key)
        if key == "simcon.summary.fail":
            if actual is not True:
                return None
        elif key == "loghooks.drift":
            if actual is not True:
                return None
        else:
            # 列表匹配：expected 是事件名清单，actual 是出现清单，有交集即命中
            if not actual or not (set(actual) & set(expected)):
                return None
    return rule.get("then", "")


def build_feedback(
    flow_compare: Any,
    simcon_summary: Optional[dict] = None,
    loghooks_drift: bool = False,
    rules_path: Optional[Path] = None,
) -> List[dict]:
    """根据比对结论 + 激励结论 + 漂移标志生成结构化归因反馈。

    flow_compare 可为 FlowCompare 模型或 dict。
    """
    if flow_compare is None:
        return []
    if hasattr(flow_compare, "model_dump"):
        fc = flow_compare.model_dump()
    else:
        fc = flow_compare

    ctx: Dict[str, Any] = {
        "compare.missing": fc.get("missing", []),
        "compare.timeouts": fc.get("timeouts", []),
        "compare.out_of_order": fc.get("out_of_order", []),
        "compare.negated": fc.get("negated", []),
        "simcon.summary.fail": bool(simcon_summary and simcon_summary.get("verdict") != "pass"),
        "loghooks.drift": bool(loghooks_drift),
    }

    # 失败才归因（verdict=pass 且无漂移 → 无反馈）
    if fc.get("verdict") == "pass" and not loghooks_drift and not ctx["simcon.summary.fail"]:
        return []

    out: List[dict] = []
    for rule in _load_rules(rules_path):
        suggestion = _match_rule(rule, ctx)
        if not suggestion:
            continue
        issue = rule.get("issue") or suggestion[:24]
        evidence = _build_evidence(fc, simcon_summary, loghooks_drift)
        if not any(o.get("suggestion") == suggestion for o in out):
            out.append({"issue": issue, "evidence": evidence, "suggestion": suggestion})
    return out


def _build_evidence(fc: dict, simcon_summary: Optional[dict], drift: bool) -> str:
    parts: List[str] = []
    if fc.get("missing"):
        parts.append("缺失:" + ",".join(fc["missing"]))
    if fc.get("timeouts"):
        parts.append("超时:" + ",".join(fc["timeouts"]))
    if fc.get("out_of_order"):
        parts.append("乱序:" + ",".join(fc["out_of_order"]))
    if fc.get("negated"):
        parts.append("负向:" + ",".join(fc["negated"]))
    if simcon_summary:
        parts.append(
            f"激励:{simcon_summary.get('pass', 0)}/{simcon_summary.get('total', 0)}"
        )
    if drift:
        parts.append("规则漂移")
    return "; ".join(parts) if parts else "验证失败"
