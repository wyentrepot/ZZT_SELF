"""安徽集中器交互 task 文件验证：帧构造 + 断言可执行。

从 apps/workbench/scenarios/tasks/anhui_minute_collect.json 加载 task，
逐步骤验证：
- send 帧能按 CCO 本地协议构造（afn/fn 编码正确）；
- expect 匹配结构正确（同 afn/fn 的帧匹配、不同 afn/fn 不匹配）；
- responder 规则对 CCO 主动上报统一回 00H-F1 确认；
- 场景模板 minute_collect.json 正确引用该 task。
"""
from __future__ import annotations

import json
from pathlib import Path

from sim_concentrator.frame_codec import (
    build_local_13762_frame,
    decode_local_13762_frame,
)
from sim_concentrator.matcher import match_frame
from sim_concentrator.responder import Responder
from sim_concentrator.runner import build_send_frame, load_task

TASK_PATH = Path(__file__).resolve().parent.parent.parent / "apps" / "workbench" / "scenarios" / "tasks" / "anhui_minute_collect.json"
SCENARIO_PATH = Path(__file__).resolve().parent.parent.parent / "apps" / "workbench" / "scenarios" / "minute_collect.json"


def _cco(afn: int, fn: int, buff: bytes = b""):
    return build_local_13762_frame(afn=afn, fn=fn, buff=buff)


def test_task_json_valid_and_loadable():
    task = load_task(str(TASK_PATH))
    assert task["id"] == "anhui_minute_collect"
    assert task["port"] == "COM24"
    assert len(task["steps"]) == 9


def test_scenario_references_task():
    sc = json.load(open(SCENARIO_PATH, encoding="utf-8"))
    assert sc["stimulus"]["task_file"] == "tasks/anhui_minute_collect.json"
    assert TASK_PATH.exists()


def test_each_send_frame_builds():
    task = load_task(str(TASK_PATH))
    for step in task["steps"]:
        if "send" not in step:
            continue
        raw = build_send_frame(step["send"])
        d = decode_local_13762_frame(raw)
        assert d["afn"] == step["send"]["afn"], (step["name"], d["afn"])
        assert d["fn"] == step["send"]["fn"], (step["name"], d["fn"])


def test_each_expect_matches():
    task = load_task(str(TASK_PATH))
    for step in task["steps"]:
        if "expect" not in step:
            continue
        exp = step["expect"]
        # 同 afn/fn 应匹配
        reply = _cco(exp["afn"], exp["fn"], buff=b"\x00")
        matched, _, reasons = match_frame(reply, exp)
        assert matched, (step["name"], reasons)
        # 不同 afn 应不匹配
        reply2 = _cco((exp["afn"] + 1) & 0xFF, exp["fn"])
        m2, _, _ = match_frame(reply2, exp)
        assert not m2, step["name"]


def test_task_responders_unified_ack():
    task = load_task(str(TASK_PATH))
    rp = Responder(override_rules=task["responders"])
    for afn, fn in [(6, 230), (6, 3), (3, 10)]:
        report = _cco(afn, fn, buff=b"\x00")
        reply = rp.reply_for(report)
        assert reply is not None, (afn, fn)
        d = decode_local_13762_frame(reply)
        assert d["afn"] == 0x00 and d["fn"] == 1, (afn, fn, d)
