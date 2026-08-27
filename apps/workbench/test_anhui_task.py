"""安徽集中器交互 task 文件验证：帧构造 + 断言可执行（ADR-5 用例语义化）。

从 apps/workbench/scenarios/tasks/anhui_minute_collect.json 加载 task，
逐步骤验证：
- send 语义（afn/fn + params）经 scenario_codec 能构出 1376.2 单 68 帧，
  且帧的 AFN/Fn 与 send 声明一致（send.afn 字符串 "10"/"11"/"03" 需换算）；
- 不再断言手写 buff 字节：appdata 由 parser_lib 按 params 编码生成；
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
from sim_concentrator.scenario_codec import load_profile

TASK_PATH = Path(__file__).resolve().parent.parent.parent / "apps" / "workbench" / "scenarios" / "tasks" / "anhui_minute_collect.json"
SCENARIO_PATH = Path(__file__).resolve().parent.parent.parent / "apps" / "workbench" / "scenarios" / "minute_collect.json"

PROFILE = load_profile("anhui")


def _cco(afn: int, fn: int, buff: bytes = b""):
    return build_local_13762_frame(afn=afn, fn=fn, buff=buff)


def _norm_afn(v) -> int:
    """send.afn 支持 int / 十六进制字符串 "10" / "0x10"。"""
    if isinstance(v, int):
        return v & 0xFF
    return int(str(v).strip().lower().replace("0x", ""), 16)


def _norm_fn(v) -> int:
    """send.fn 支持 int / "F231" / "231"。"""
    if isinstance(v, int):
        return v
    s = str(v).strip().upper()
    if s.startswith("F"):
        s = s[1:]
    return int(s, 10)


def test_task_json_valid_and_loadable():
    task = load_task(str(TASK_PATH))
    assert task["id"] == "anhui_minute_collect"
    assert task["profile"] == "anhui"
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
        send = step["send"]
        # 语义化契约：不得残留 raw/format:buff 手写 hex
        assert "raw" not in send, step["name"]
        assert "buff" not in send, step["name"]
        assert send.get("afn") is not None and send.get("fn") is not None, step["name"]
        raw = build_send_frame(send, profile=PROFILE, seq=1)
        d = decode_local_13762_frame(raw)
        assert d["afn"] == _norm_afn(send["afn"]), (step["name"], d["afn"])
        assert d["fn"] == _norm_fn(send["fn"]), (step["name"], d["fn"])


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
