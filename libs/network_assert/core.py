"""network_assert.core —— 断言规则加载 / 描述 / 预留求值接口（REQS-0025 G2/C1）。

规则文件：rules/*.json（每文件一条规则声明，门槛出处必须可追溯，schema.py 校验）。
求值：本需求（REQS-0025）只做定义与静态校验；evaluate() 预留接口，
输入契约 = REQS-0024 组网观测事件流（nwk_events）/网络承载评估快照，接入留后续需求。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema import validate_rule, validate_rules

_RULES_DIR = Path(__file__).resolve().parent / "rules"


def list_rule_files() -> list[str]:
    return sorted(p.name for p in _RULES_DIR.glob("*.json"))


def load_rules() -> list[dict]:
    """加载全部内置断言规则（按文件名排序，解析失败抛异常——规则库必须保持可用）。"""
    rules = []
    for path in sorted(_RULES_DIR.glob("*.json")):
        rules.append(json.loads(path.read_text(encoding="utf-8")))
    return rules


def get_rule(rule_id: str) -> dict | None:
    for rule in load_rules():
        if rule.get("id") == rule_id:
            return rule
    return None


def validate() -> list[str]:
    """静态校验全部规则（含跨规则 id 唯一性），返回问题列表；空 = 通过。"""
    return validate_rules(load_rules())


def describe(rule: dict) -> str:
    """规则的一句话人读描述（供 digest/AI 知识包引用，不构造帧、不触设备）。"""
    ths = rule.get("thresholds", [])
    th_text = "；".join(
        f"{th.get('key')}={th.get('value')}{th.get('unit', '')}" for th in ths[:4]
    )
    more = f" 等{len(ths)}项" if len(ths) > 4 else ""
    return f"{rule['name']}（{rule['id']}）：触发 {rule['trigger']['event']}；{th_text}{more}"


def evaluate(rule: dict, observations: Any) -> dict:
    """预留求值接口——本需求不实现。

    契约（供后续需求接入时遵循）：
      - observations：REQS-0024 组网观测事件流片段（nwk_events 行 /
        network_assessment 周期快照），由调用方按 trigger.event 过滤；
      - 返回 {rule_id, verdict: pass|fail|inconclusive, evidence: [...],
        window: {...}, source: rule.source}；
      - verdict=inconclusive 用于观测窗口数据不足，不判 fail。
    """
    raise NotImplementedError(
        "network_assert 求值属后续需求（REQS-0025 G2 只做定义+静态校验）；"
        f"规则 {rule.get('id', '<no-id>')} 的求值需 REQS-0024 事件流接入后实现"
    )
