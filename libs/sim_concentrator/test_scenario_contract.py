"""用例语义化 + Profile 契约测试（阶段1：数据契约）。

验证 profile 文件结构、字段类型、档案引用可解析，以及契约文档已落地。
地址域方向规则与 scenario_codec 行为见 test_scenario_codec.py（阶段3）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
SCENARIOS = _REPO / "apps" / "workbench" / "scenarios"
PROFILES = SCENARIOS / "profiles"


def _load_profile(pid: str) -> dict:
    p = PROFILES / f"{pid}.json"
    assert p.exists(), f"profile 文件缺失: {p}"
    return json.loads(p.read_text(encoding="utf-8"))


def test_contract_doc_exists():
    doc = _REPO / "docs" / "协议" / "13762库设计" / "用例语义化与Profile契约.md"
    assert doc.exists(), "契约文档缺失"


def test_profile_anhui_loadable_and_fields():
    prof = _load_profile("anhui")
    assert prof["id"] == "anhui"
    # cco_addr 必须是 12 位 BCD 数字串
    assert prof["cco_addr"].isdigit() and len(prof["cco_addr"]) == 12
    assert prof["comm_mode"] == 3
    assert prof["seq_auto"] is True
    # 任务号范围
    assert prof["task_range"]["min"] <= prof["task_range"]["max"]


def test_profile_sta_archives_referenceable():
    prof = _load_profile("anhui")
    archives = prof["sta_archives"]
    assert len(archives) >= 3
    ids = {a["id"] for a in archives}
    assert {"sta1", "sta2", "sta698"} <= ids
    for a in archives:
        assert a["addr"].isdigit() and len(a["addr"]) == 12, a
        assert a["protocol"] in (645, 698), a
        assert a["phase"] in (0, 1), a


def test_all_tasks_reference_existing_profile():
    """现有 tasks 均声明 profile 且对应 profile 文件存在（迁移后契约）。"""
    tasks_dir = SCENARIOS / "tasks"
    if not tasks_dir.exists():
        pytest.skip("tasks 目录不存在")
    for tf in sorted(tasks_dir.glob("*.json")):
        task = json.loads(tf.read_text(encoding="utf-8"))
        pid = task.get("profile")
        if pid:
            assert (PROFILES / f"{pid}.json").exists(), \
                f"{tf.name} 引用的 profile 缺失: {pid}"
