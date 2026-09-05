"""network_assert.schema —— 断言定义静态校验器（REQS-0025 G2/C1）。

断言规则库是**声明式定义**（触发条件 + 观测窗口 + 判定 + 阈值 + 出处），
本模块只做结构与可追溯性校验，不做求值；求值依赖 REQS-0024 事件流，留后续需求。
"""
from __future__ import annotations

from typing import Any

RULE_ID_PREFIX = "nwk."
ALLOWED_FAMILIES = {
    "association",    # 入网准入/关联
    "heartbeat",      # 心跳保活/离网判定
    "beacon",         # 信标周期/合法性
    "route",          # 路由周期/修复
    "csma",           # 信道访问/占用
    "success_rate",   # 通信成功率/离线率
    "conflict",       # NID/RF 信道冲突仲裁
}
ALLOWED_SEVERITY = {"info", "warn", "fault"}
ALLOWED_OPS = {"<", "<=", ">", ">=", "==", "in_range", "in_set"}
ALLOWED_WINDOW_KINDS = {"per_event", "rolling_window", "period_count", "session"}

# 阈值出处白名单：断言门限必须能追溯到这些文档/代码口径之一
KNOWN_SOURCE_DOCS = {
    "CCO实现逻辑/07-NWK入网-路由-心跳-信标-冲突.md",
    "CCO实现逻辑/08-MAC链路层-时隙调度-CSMA-分片重组.md",
    "蒸馏/06_测试用例.md",
    "侦听台网络承载评估 B 类规则（apps/listener/network_assessment.py，用户拍板口径）",
}

_REQUIRED_TOP = ("id", "name", "family", "severity_hint", "description",
                 "trigger", "observation_window", "verdict", "thresholds", "source")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_rule(rule: Any) -> list[str]:
    """校验单条断言定义，返回问题列表（空列表 = 通过）。"""
    problems: list[str] = []
    if not isinstance(rule, dict):
        return ["规则必须是对象"]
    rid = rule.get("id", "<no-id>")

    for key in _REQUIRED_TOP:
        if key not in rule:
            problems.append(f"{rid}: 缺少必需字段 {key}")
    if problems:
        return problems

    if not str(rid).startswith(RULE_ID_PREFIX):
        problems.append(f"{rid}: id 必须以 {RULE_ID_PREFIX!r} 开头")
    if rule["family"] not in ALLOWED_FAMILIES:
        problems.append(f"{rid}: family {rule['family']!r} 不在允许集合 {sorted(ALLOWED_FAMILIES)}")
    if rule["severity_hint"] not in ALLOWED_SEVERITY:
        problems.append(f"{rid}: severity_hint {rule['severity_hint']!r} 非法")
    if not str(rule["description"]).strip():
        problems.append(f"{rid}: description 不能为空")

    trigger = rule["trigger"]
    if not isinstance(trigger, dict) or not str(trigger.get("event", "")).strip():
        problems.append(f"{rid}: trigger.event 不能为空（REQS-0024 组网事件类型）")
    elif not isinstance(trigger.get("params", {}), dict):
        problems.append(f"{rid}: trigger.params 必须是对象")

    window = rule["observation_window"]
    if not isinstance(window, dict) or window.get("kind") not in ALLOWED_WINDOW_KINDS:
        problems.append(f"{rid}: observation_window.kind 必须是 {sorted(ALLOWED_WINDOW_KINDS)}")

    verdict = rule["verdict"]
    if not isinstance(verdict, dict) or not str(verdict.get("pass", "")).strip() \
            or not str(verdict.get("fail", "")).strip():
        problems.append(f"{rid}: verdict.pass / verdict.fail 均不能为空")

    thresholds = rule["thresholds"]
    if not isinstance(thresholds, list) or not thresholds:
        problems.append(f"{rid}: thresholds 必须是非空数组")
        return problems
    for th in thresholds:
        if not isinstance(th, dict):
            problems.append(f"{rid}: threshold 项必须是对象")
            continue
        tkey = th.get("key", "<no-key>")
        for req in ("key", "op", "value", "unit", "meaning"):
            if req not in th:
                problems.append(f"{rid}: threshold[{tkey}] 缺 {req}")
        if th.get("op") not in ALLOWED_OPS:
            problems.append(f"{rid}: threshold[{tkey}] op {th.get('op')!r} 非法")
            continue
        value = th.get("value")
        if th["op"] == "in_range":
            if not (isinstance(value, list) and len(value) == 2
                    and all(_is_number(v) for v in value) and value[0] <= value[1]):
                problems.append(f"{rid}: threshold[{tkey}] in_range 需要 [下界,上界] 数值对")
        elif th["op"] == "in_set":
            if not (isinstance(value, list) and value and all(_is_number(v) for v in value)):
                problems.append(f"{rid}: threshold[{tkey}] in_set 需要非空数值数组")
        elif not _is_number(value):
            problems.append(f"{rid}: threshold[{tkey}] value 必须是数值")
        if not str(th.get("unit", "")).strip():
            problems.append(f"{rid}: threshold[{tkey}] unit 不能为空")
        if not str(th.get("meaning", "")).strip():
            problems.append(f"{rid}: threshold[{tkey}] meaning 不能为空（阈值语义）")

    source = rule["source"]
    if not isinstance(source, dict):
        problems.append(f"{rid}: source 必须是对象")
    else:
        for req in ("doc", "section", "quote"):
            if not str(source.get(req, "")).strip():
                problems.append(f"{rid}: source.{req} 不能为空（阈值出处可追溯）")
        if source.get("doc") not in KNOWN_SOURCE_DOCS:
            problems.append(f"{rid}: source.doc {source.get('doc')!r} 不在出处白名单")
    return problems


def validate_rules(rules: list[dict]) -> list[str]:
    """批量校验 + 跨规则 id 唯一性。"""
    problems: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        rid = rule.get("id", "<no-id>") if isinstance(rule, dict) else "<no-id>"
        if rid in seen:
            problems.append(f"{rid}: 规则 id 重复")
        seen.add(rid)
        problems.extend(validate_rule(rule))
    return problems
