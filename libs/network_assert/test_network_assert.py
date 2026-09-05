"""libs.network_assert 单测（REQS-0025 G2）。

覆盖：内置规则全量静态校验、TODO Step 2.2 七个门限族齐备、
阈值出处可追溯、篡改注入校验器报警、evaluate 预留接口契约。
"""
from __future__ import annotations

import copy

import pytest

from libs.network_assert import (describe, evaluate, get_rule, list_rule_files,
                                 load_rules, validate, validate_rule, validate_rules)

EXPECTED_FAMILIES = {
    "nwk.assoc_reject.backoff_tiers": "association",
    "nwk.heartbeat.offline_4cycles": "heartbeat",
    "nwk.beacon.period_domain": "beacon",
    "nwk.route.period_adaptive": "route",
    "nwk.heartbeat.period_ratio": "heartbeat",
    "nwk.csma.occupancy_two_tier": "csma",
    "nwk.comm_success_rate.three_tier": "success_rate",
    "nwk.conflict.arbitration_timing": "conflict",
}


def test_builtin_rules_load_and_validate():
    rules = load_rules()
    assert {r["id"] for r in rules} == set(EXPECTED_FAMILIES)
    assert list_rule_files() == sorted(list_rule_files())
    assert validate() == [], f"内置规则必须全部通过静态校验：{validate()}"


def test_todo_step22_families_covered():
    """TODO Step 2.2 的七个门限族（心跳×2 归 heartbeat）逐条齐备。"""
    rules = {r["id"]: r for r in load_rules()}
    for rid, family in EXPECTED_FAMILIES.items():
        assert rules[rid]["family"] == family
    # 关键阈值抽样核对（口径防漂移）
    th = {t["key"]: t["value"] for t in rules["nwk.csma.occupancy_two_tier"]["thresholds"]}
    assert th["csma_slot_ratio_degraded_pct"] == 60 and th["csma_slot_ratio_fault_pct"] == 80
    th = {t["key"]: t["value"] for t in rules["nwk.comm_success_rate.three_tier"]["thresholds"]}
    assert th["success_rate_healthy_min_pct"] == 98 and th["success_rate_degraded_min_pct"] == 90
    th = {t["key"]: t["value"] for t in rules["nwk.heartbeat.offline_4cycles"]["thresholds"]}
    assert th["leave_ind_cycles"] == 4 and th["leave_delete_delay_ms"] == 10000
    th = {t["key"]: t["value"] for t in rules["nwk.conflict.arbitration_timing"]["thresholds"]}
    assert th["nid_change_mac_small_ms"] == 307 and th["rf_change_coord_ms"] == 20700
    th = {t["key"]: t["value"] for t in rules["nwk.heartbeat.period_ratio"]["thresholds"]}
    assert th["heart_cycle_ratio_small_net"] == 2 and th["heart_cycle_ratio_large_net"] == 4


def test_every_threshold_traceable():
    """阈值出处可追溯：每条规则 source 三要素非空且 doc 在白名单；阈值带语义。"""
    for rule in load_rules():
        src = rule["source"]
        assert src["doc"] and src["section"] and src["quote"]
        for th in rule["thresholds"]:
            assert th["unit"] and th["meaning"]


def test_get_rule_and_describe():
    rule = get_rule("nwk.beacon.period_domain")
    assert rule is not None
    text = describe(rule)
    assert "信标周期合法域" in text and "nwk.beacon.period_domain" in text
    assert get_rule("nwk.nope") is None


def test_validate_rejects_missing_source():
    rule = copy.deepcopy(get_rule("nwk.csma.occupancy_two_tier"))
    rule.pop("source")
    problems = validate_rule(rule)
    assert any("source" in p for p in problems)


def test_validate_rejects_bad_op_and_value():
    rule = copy.deepcopy(get_rule("nwk.csma.occupancy_two_tier"))
    rule["thresholds"][0]["op"] = "!="
    problems = validate_rule(rule)
    assert any("op" in p for p in problems)
    rule2 = copy.deepcopy(get_rule("nwk.beacon.period_domain"))
    rule2["thresholds"][0]["value"] = [10, 1]  # 上下界颠倒
    problems2 = validate_rule(rule2)
    assert any("in_range" in p for p in problems2)


def test_validate_rejects_duplicate_ids():
    rule = copy.deepcopy(get_rule("nwk.route.period_adaptive"))
    problems = validate_rules([rule, copy.deepcopy(rule)])
    assert any("重复" in p for p in problems)


def test_validate_rejects_unknown_family_and_doc():
    rule = copy.deepcopy(get_rule("nwk.route.period_adaptive"))
    rule["family"] = "nope"
    rule["source"]["doc"] = "南网/某文档.md"
    problems = validate_rule(rule)
    assert any("family" in p for p in problems)
    assert any("白名单" in p for p in problems)  # 南网文档必须被出处白名单拦截


def test_evaluate_is_reserved_stub():
    rule = get_rule("nwk.heartbeat.offline_4cycles")
    with pytest.raises(NotImplementedError, match="REQS-0024"):
        evaluate(rule, [])
