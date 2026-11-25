"""0043-F1 补块批次 1 的块层单测（fake 能力 + 逐字节协议样本，全部离线）。

覆盖 5 个新块：``meter.batch_read`` / ``gate.multi_frame_110`` / ``gate.concurrent_109``
/ ``meter.read_698`` / ``param.init_01h_f2``。

样本纪律（0041 10H-F4 教训：样本必须与协议逐字节对齐）：
- 否认帧 / 确认帧 / 645 读帧 / 打包 F1H-F1 / 698 请求与应答全部是**硬编码 hex 基准**，
  逐字节可对回 adapter 构帧器与 KB 帧格式文档（出处写在常量旁）；
- 求值链走 ``core.expect.evaluate`` 真实求值器（观测 → 期望 → 四态），与真机同一链路。

判据归属红线（0044 §4.2）也在本文件守门：同一份否认观测，「期望 6E」与「期望 6D」
分别 pass / fail——块不判结论，负向期望是用例层 expect。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Mapping

import pytest

from case_runner.blocks import gate, meter_698, meter_batch, param_init
from case_runner.capabilities.frames import FramesCapability
from case_runner.core.expect import evaluate
from case_runner.core.executor import BlockResult

# --------------------------------------------------------------------------- #
# 逐字节协议样本（出处随行；改基准必须先核对协议文档）
# --------------------------------------------------------------------------- #

#: 00H-F2 否认帧，错误状态字=6E（110 超单帧条数）——adapter_10376 up 构帧产物
DENY_6E_UP = "681000830000000000020002006EF516"
#: 00H-F2 否认帧，错误状态字=6D（109 超并发帧数）
DENY_6D_UP = "681000830000000000030002006DF516"
#: 00H-F1 确认帧（01H-F2 参数初始化的应答，全帧类型.md §4.2：上行=确认/否认帧）
CONFIRM_UP = "680F00830000000000010001008516"
#: 645 读数据帧（11H，地址 990042708102（块层 build_645_read_frame 人读序口径），
#: DI 03300501 长报文口径，CS 合法；CS=B8）。闸门只看条数计数、与表无关。
F645_GATE = "6899004270810268110434386336B816"
#: §4.15 打包数据单元（规约类型 02 + 保留 00 + 长度 0x0020 LE + 2 条 645 帧拼接）
DATAUNIT_F1_2MSG = "02002000" + F645_GATE + F645_GATE
#: 完整 F1H-F1 下行帧（上面数据单元的 1376.2 封装，ADR-5 语义构帧产物）
PACKED_F1_2MSG = (
    "68330043000000000001F1010002002000"
    "6899004270810268110434386336B816"
    "6899004270810268110434386336B8166416"
)
#: 698 GET-RequestNormal 完整链路帧（SECURITY 明文包，SA=05+000000000001，OAD 20000201）
REQ_698 = (
    "FEFEFEFE682C004305000000000001007CB11000080501012000020100"
    "011000000000000000000000000000000000C4CC16"
)
#: 698 GET-Response-Normal 应答帧（simeter 口径：85 01 PIID OAD 01 data；data=8B34）
RESP_698 = "681900C30500000000000100A0ED85010120000201018B347EBE16"
#: 02H-F1 上行帧（协议类型=03H + 报文长度 1B + 内嵌 698 应答；facade 可解出转发报文）
OUTER_698_UP = (
    "682C0083000000000009020100031B"
    "681900C30500000000000100A0ED85010120000201018B347EBE164016"
)

FRAMES = FramesCapability()

#: 注入参数：零真实等待（clock 走假钟，sleep 空转）
NO_WAIT = {"clock": lambda: 0.0, "sleep": lambda seconds: None}


# --------------------------------------------------------------------------- #
# fake 能力（只实现块用到的口；verify 步骤留档供形态断言）
# --------------------------------------------------------------------------- #


class FakeVerifySimcon:
    """verify 型假 simcon：回放一份 job 结果，并留档提交过的 steps。"""

    name = "simcon"

    def __init__(self, result: Dict[str, Any]):
        self.result = result
        self.submitted: List[Dict[str, Any]] = []

    def verify(self, steps, max_poll_seconds=None, **kwargs):
        self.submitted.append({"steps": steps, "max_poll_seconds": max_poll_seconds})
        return json.loads(json.dumps(self.result))  # 深拷贝，防块内改写污染用例


class FakeBatchSimcon:
    """batch 型假 simcon：create 回首份快照，job 按序回放快照序列。"""

    name = "simcon"

    def __init__(self, create: Dict[str, Any], job_snapshots=()):
        self.create_result = create
        self.job_snapshots = [json.loads(json.dumps(s)) for s in job_snapshots]
        self.create_calls: List[Dict[str, Any]] = []
        self.job_calls: List[str] = []

    def batch_read_create(self, meters, **kwargs):
        self.create_calls.append(dict(kwargs, meters=list(meters)))
        result = json.loads(json.dumps(self.create_result))
        result.setdefault("ok", True)
        return result

    def batch_read_job(self, job_id):
        self.job_calls.append(job_id)
        if self.job_snapshots:
            snap = self.job_snapshots.pop(0)
        else:
            snap = {"job_id": job_id, "finished": True}
        return dict(json.loads(json.dumps(snap)), ok=True)


def _deny_job_result(deny_hex: str, code: str, text: str) -> Dict[str, Any]:
    """闸门 verify 结果：否认帧命中 + simcon matcher 同款 deny 说明。"""
    return {
        "ok": False, "error": "job_not_passed",
        "steps": [{
            "index": 0, "name": "闸门", "result": "fail",
            "matched": deny_hex,
            "deny": {"code": int(code, 16), "code_hex": code, "text": text},
            "reason": "否认（%s）" % text,
        }],
    }


# --------------------------------------------------------------------------- #
# 打包构帧器（F0-4 缺块①）：字节级对齐 §4.15
# --------------------------------------------------------------------------- #


def test_pack_645_payload_concatenates_cs_valid_frames():
    payload, reason = gate.pack_645_payload("990042708102", "03300501", 2)
    assert reason == ""
    assert payload == F645_GATE + F645_GATE


def test_pack_payload_plus_semantic_header_matches_415_frame():
    """块产 payload + simcon 语义头 = §4.15 完整帧（逐字节基准 PACKED_F1_2MSG）。"""
    from parser_lib.adapters.adapter_10376 import encode_app_data, build_frame as b13762

    appdata = encode_app_data(0xF1, 1, {"protocol": 2, "payload": F645_GATE * 2})
    assert appdata.hex().upper() == DATAUNIT_F1_2MSG
    frame = b13762(direction="down", afn=0xF1, fn=1, appdata=appdata, info={"seq": 1})
    assert frame.hex().upper() == PACKED_F1_2MSG


def test_pack_14_messages_stays_under_frame_limit_and_decodes():
    """缺省 14 条打包：整体帧长 ≤2000B，facade 解出 14 条嵌套 645（CCO 逐条计数口径）。"""
    payload, reason = gate.pack_645_payload("990042708102", "03300501", gate.READMETER_MAX + 1)
    assert reason == ""
    appdata = bytes.fromhex("0200") + (len(payload) // 2).to_bytes(2, "little") + bytes.fromhex(payload)
    from parser_lib.adapters.adapter_10376 import build_frame as b13762

    frame = b13762(direction="down", afn=0xF1, fn=1, appdata=appdata, info={"seq": 1})
    assert len(frame) <= gate.FRAME_LENGTH_LIMIT
    decoded = FRAMES.decode(frame.hex(), protocol="1376.2")
    nested = decoded.get("frame", {}).get("nested", [])
    assert len(nested) == gate.READMETER_MAX + 1
    assert all(entry.get("structure") == "645" for entry in nested)


def test_pack_rejects_when_over_frame_limit():
    """整体帧长超 2000B → 配置错误（空 payload + 原因），绝不静默下发。"""
    payload, reason = gate.pack_645_payload("990042708102", "03300501", 200)
    assert payload == ""
    assert "超上限" in reason


# --------------------------------------------------------------------------- #
# gate.multi_frame_110
# --------------------------------------------------------------------------- #


def test_gate_110_observes_deny_6e_and_step_expect_decides():
    simcon = FakeVerifySimcon(_deny_job_result(
        DENY_6E_UP, "6E", "超过单个1376.2帧最大允许的电表协议报文条数（110）"))
    result = gate.gate_multi_frame_110({
        "meter_addr": "990042708102", "di": "03300501",
        "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    obs = result.observation
    assert obs is not None and obs.complete and obs.got
    assert obs.values["否认码"] == "6E"
    assert obs.values["否认帧"] == DENY_6E_UP
    assert obs.values["下发条数"] == gate.DEFAULT_COUNT_110  # 14 = 13+1（READMETER_MAX 上限）

    # 判据在步骤：期望 6E → pass；同一份观测期望 6D → fail（负向期望归用例层）
    assert evaluate(obs, [{"op": "complete"},
                          {"op": "eq", "field": "否认码", "value": "6E"}]).state == "pass"
    assert evaluate(obs, [{"op": "complete"},
                          {"op": "eq", "field": "否认码", "value": "6D"}]).state == "fail"


def test_gate_110_decodes_deny_frame_when_step_has_no_deny_note():
    """步骤没带 deny 说明时，块回解 matched 帧（facade「错误状态字」项）。"""
    simcon = FakeVerifySimcon({"ok": False, "error": "job_not_passed", "steps": [
        {"index": 0, "result": "fail", "matched": DENY_6E_UP, "reason": "否认"},
    ]})
    result = gate.gate_multi_frame_110({
        "meter_addr": "990042708102", "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    assert result.observation.values["否认码"] == "6E"
    assert "超过单个帧最大允许的电表协议报文条数" in result.observation.values["否认码文本"]


def test_gate_110_without_deny_is_timeout_unknown():
    """没等到否认帧 → 原因码 timeout，步骤落 unknown（绝不折成任何结论）。"""
    simcon = FakeVerifySimcon({"ok": False, "error": "job_not_passed", "steps": [
        {"index": 0, "result": "fail", "matched": None, "reason": "超时未收到期望帧"},
    ]})
    result = gate.gate_multi_frame_110({
        "meter_addr": "990042708102", "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    obs = result.observation
    assert obs.complete is False and obs.reason_code == "timeout"
    assert evaluate(obs, [{"op": "eq", "field": "否认码", "value": "6E"}]).state == "unknown"


def test_gate_110_send_step_shape():
    """下发形态：语义化 F1H-F1（protocol=2 + 打包 payload），expect 只锁否认帧。"""
    simcon = FakeVerifySimcon(_deny_job_result(DENY_6E_UP, "6E", "否认"))
    gate.gate_multi_frame_110({
        "meter_addr": "990042708102", "di": "03300501", "count": 14,
        "expect_timeout": 5, "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    step = simcon.submitted[0]["steps"][0]
    assert step["send"]["afn"] == "F1" and step["send"]["fn"] == 1
    assert step["send"]["params"]["protocol"] == 2
    assert step["send"]["params"]["payload"] == F645_GATE * 14
    assert step["expect"] == {"afn": "00", "fn": 2, "dir": "up"}
    assert step["expect_timeout"] == 5


def test_gate_110_invalid_params_never_send():
    """count=0 / di 非法 / 帧长超限 → 原因码 invalid_param，verify 一次都不提交。"""
    for params in (
        {"count": 0},
        {"di": "ZZ00"},
        {"count": 200},
    ):
        simcon = FakeVerifySimcon({"ok": True, "steps": []})
        result = gate.gate_multi_frame_110({
            "meter_addr": "990042708102", "simcon": simcon, "frames": FRAMES, **NO_WAIT,
            **params,
        })
        assert result.observation.reason_code == "invalid_param", params
        assert simcon.submitted == [], params


# --------------------------------------------------------------------------- #
# gate.concurrent_109
# --------------------------------------------------------------------------- #


def test_gate_109_observes_deny_6d():
    simcon = FakeVerifySimcon(_deny_job_result(
        DENY_6D_UP, "6D", "超过最大并发数（6D，CCO aps_max_trans 上限）"))
    result = gate.gate_concurrent_109({
        "meter_addr": "990042708102", "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    obs = result.observation
    assert obs.complete and obs.values["否认码"] == "6D"
    # 默认 frame_count=21（队列上限 20 + 1）
    assert obs.values["下发帧数"] == gate.DEFAULT_FRAMES_109  # 21 = 20+1（队列上限）
    assert evaluate(obs, [{"op": "eq", "field": "否认码", "value": "6D"}]).state == "pass"
    assert evaluate(obs, [{"op": "eq", "field": "否认码", "value": "6E"}]).state == "fail"


def test_gate_109_fire_and_forget_steps_then_listener():
    """步骤编排：21 个「仅下发」步（无 expect）+ 1 个 recv_only 否认监听步。"""
    simcon = FakeVerifySimcon({"ok": False, "error": "job_not_passed", "steps": []})
    gate.gate_concurrent_109({
        "meter_addr": "990042708102", "frame_count": 5, "expect_timeout": 4,
        "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    steps = simcon.submitted[0]["steps"]
    assert len(steps) == 6
    for step in steps[:5]:
        assert "expect" not in step, "仅下发步不得带 expect（要背靠背压队列）"
        assert step["send"]["params"]["payload"] == F645_GATE
    listener = steps[-1]
    assert listener["recv_only"] is True
    assert listener["expect"] == {"afn": "00", "fn": 2, "dir": "up"}
    assert listener["expect_timeout"] == 4


def test_gate_109_without_deny_is_timeout_unknown():
    simcon = FakeVerifySimcon({"ok": False, "error": "job_not_passed", "steps": [
        {"result": "fail", "matched": None, "reason": "超时"},
    ]})
    result = gate.gate_concurrent_109({
        "meter_addr": "990042708102", "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    assert result.observation.reason_code == "timeout"
    assert evaluate(result.observation,
                    [{"op": "eq", "field": "否认码", "value": "6D"}]).state == "unknown"


# --------------------------------------------------------------------------- #
# meter.batch_read
# --------------------------------------------------------------------------- #


BATCH_DONE = {
    "job_id": "batch-0001", "mode": "batch", "afn_fn": "F1H-F1",
    "item": "02010100", "addr_mode": "wire", "meters_total": 1,
    "max_concurrent": 5, "in_flight": 0, "queued": 0, "done": 3,
    "success": 1, "failed": 2,
    "timeout": {"seconds": 99, "tier": "batch_read", "note": "并发抄表业务超时"},
    "deny_breakdown": {"6E": 1},
    "finished": True, "error": None,
    "rows": [
        {"meter": "000000000001", "afn_fn": "F1-F1", "status": "success",
         "elapsed_ms": 2000, "reply_hex": "682E0083…"},
        {"meter": "000000000001", "afn_fn": "00H-F2", "status": "deny",
         "deny_code": "6E", "deny_text": "超过单个1376.2帧最大允许的电表协议报文条数（110）",
         "elapsed_ms": 300},
        {"meter": "000000000001", "status": "nodata",
         "note": "应答空数据（从节点离线/无抄读数据）"},
    ],
}


def test_batch_read_observes_rows_and_deny_breakdown():
    simcon = FakeBatchSimcon(
        create={"job_id": "batch-0001", "finished": False, "rows": []},
        job_snapshots=[BATCH_DONE],
    )
    result = meter_batch.meter_batch_read({
        "meter_addr": "000000000001", "simcon": simcon,
        "clock": lambda: 0.0, "sleep": lambda seconds: None,
    })
    obs = result.observation
    assert obs.complete and obs.got
    assert obs.values["任务号"] == "batch-0001"
    assert obs.values["成功数"] == 1
    assert obs.values["否认数"] == 1
    assert obs.values["空载荷数"] == 1                      # 失败回装空载荷（§4.15 长度域=0）
    assert obs.values["否认码列表"] == ["6E"]
    assert obs.values["否认明细"] == {"6E": 1}
    assert "99" in obs.values["超时档位"]                    # 99s 档如实透出（F0-7-4 口径）

    # 判据在步骤：单表变体「成功回装」= 成功数==表数；否认口径 = 否认码列表
    assert evaluate(obs, [{"op": "complete"},
                          {"op": "eq", "field": "成功数", "value": obs.values["表数"]}]).state == "pass"
    assert evaluate(obs, [{"op": "complete"},
                          {"op": "eq", "field": "否认码列表", "value": ["6E"]}]).state == "pass"
    assert evaluate(obs, [{"op": "complete"},
                          {"op": "eq", "field": "否认码列表", "value": ["6D"]}]).state == "fail"


def test_batch_read_create_body_shape():
    simcon = FakeBatchSimcon(create={"job_id": "b1", "finished": True, "rows": []})
    meter_batch.meter_batch_read({
        "meter_addr": "990042708102", "mode": "single", "protocol_type": 3,
        "di": "20000201", "max_concurrent": 3, "expect_timeout": 99,
        "simcon": simcon, "clock": lambda: 0.0, "sleep": lambda seconds: None,
    })
    call = simcon.create_calls[0]
    assert call["meters"] == ["990042708102"]
    assert call["mode"] == "single" and call["protocol_type"] == 3
    assert call["item"] == "20000201"                       # 698 时 di 即 OAD（两套编号）
    assert call["timeout"] == 99


def test_batch_read_unfinished_job_is_incomplete_timeout():
    """等满预算 job 未到终态 → 观测不完整（timeout），已完成的行照报。"""
    running = dict(BATCH_DONE, finished=False, done=1)
    simcon = FakeBatchSimcon(create={"job_id": "b2", "finished": False, "rows": []},
                             job_snapshots=[running])

    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0])

    def fake_clock():
        return next(ticks, 4.0)

    result = meter_batch.meter_batch_read({
        "meter_addr": "000000000001", "wait_s": 2, "poll_interval_s": 1,
        "simcon": simcon, "clock": fake_clock, "sleep": lambda seconds: None,
    })
    obs = result.observation
    assert obs.complete is False and obs.reason_code == "timeout"
    assert "等了" in result.detail                          # 等待留痕（executor 派生 decisions）
    assert evaluate(obs, [{"op": "got"}]).state == "unknown"


def test_batch_read_maps_capability_errors():
    """闸门忙 409 → serial_busy（skipped 语义）；未配置 → bench_unreachable。"""
    busy = FakeBatchSimcon(create={"ok": False, "error": "http_409", "detail": "闸门忙"})
    result = meter_batch.meter_batch_read({
        "meter_addr": "000000000001", "simcon": busy, **NO_WAIT,
    })
    assert result.observation.reason_code == "serial_busy"

    down = FakeBatchSimcon(create={"ok": False, "error": "unreachable", "detail": "服务不可达"})
    result = meter_batch.meter_batch_read({
        "meter_addr": "000000000001", "simcon": down, **NO_WAIT,
    })
    assert result.observation.reason_code == "bench_unreachable"


def test_batch_read_param_validation_never_send():
    """mode/protocol/max_concurrent(>20=109 口径)/wait_s 非法 → invalid_param 且不建任务。"""
    for params in (
        {"mode": "bulk"},
        {"protocol_type": "x"},
        {"max_concurrent": 21},
        {"wait_s": -3},
        {"di": "123"},
    ):
        simcon = FakeBatchSimcon(create={"job_id": "b", "finished": True, "rows": []})
        result = meter_batch.meter_batch_read({
            "meter_addr": "000000000001", "simcon": simcon, **NO_WAIT, **params,
        })
        assert result.observation.reason_code == "invalid_param", params
        assert simcon.create_calls == [], params


# --------------------------------------------------------------------------- #
# meter.read_698
# --------------------------------------------------------------------------- #


def test_build_698_get_request_is_byte_exact():
    """698 读请求逐字节基准（SECURITY 明文包 GET-RequestNormal，STA 模板）。"""
    frame, reason = meter_698.build_698_get_request("000000000001", "20000201", frames=FRAMES)
    assert reason == ""
    assert frame == REQ_698


def test_build_698_get_apdu_plain_and_invalid():
    assert meter_698.build_698_get_apdu("20000201", security=False) == "0501012000020100"
    assert meter_698.build_698_get_apdu("ZZ") == ""


def test_read_698_observes_get_response_and_oad_echo():
    simcon = FakeVerifySimcon({"ok": True, "steps": [
        {"index": 0, "result": "pass", "matched": OUTER_698_UP, "reason": "匹配成功"},
    ]})
    result = meter_698.meter_read_698({
        "meter_addr": "000000000001", "oad": "20000201", "expect_timeout": 5,
        "query_max_timeout": False,
        "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    obs = result.observation
    assert obs.complete and obs.got
    assert obs.values["帧格式合法"] is True
    assert obs.values["应答APDU"].startswith("85")
    assert obs.values["OAD回显"] is True
    assert obs.values["数值"] == "34" and obs.values["单位"] == "V"  # 有值且可解，不比真值
    # 判据在步骤：KB 冒烟档 + 0044 §8.1-4「有值且可解」
    assert evaluate(obs, [{"op": "complete"},
                          {"op": "eq", "field": "OAD回显", "value": True},
                          {"op": "has_value", "field": "数值"}]).state == "pass"
    # 下发的 payload 就是完整 698 请求帧，协议类型 3（03H=698.45）
    step = simcon.submitted[0]["steps"][0]
    assert step["send"]["params"]["protocol"] == 3
    assert step["send"]["params"]["payload"] == REQ_698


def test_read_698_wrong_oad_is_observed_not_judged():
    """表回的是别的 OAD：观测完整但 OAD回显=False——由步骤 expect 判 fail。"""
    from parser_lib.adapters.adapter_10376 import build_frame as b13762
    from parser_lib.adapters.adapter_698 import build_frame as b698

    inner = bytes([0x85, 0x01, 0x01]) + bytes.fromhex("20000202") + b"\x01" + bytes([0x8B, 0x34])
    other_oad_resp = b698(inner, b"\x05" + bytes.fromhex("000000000001"),
                          ca=0x00, control=0xC3)
    app = bytes([3, len(other_oad_resp)]) + other_oad_resp
    outer = b13762(direction="up", afn=0x02, fn=1, appdata=app, info={"seq": 9})
    obs = meter_698.judge_read_698(outer.hex().upper(), expect_oad="20000201", frames=FRAMES)
    assert obs.complete and obs.values["OAD回显"] is False
    assert evaluate(obs, [{"op": "eq", "field": "OAD回显", "value": True}]).state == "fail"


def test_read_698_no_reply_is_timeout():
    simcon = FakeVerifySimcon({"ok": False, "error": "poll_timeout",
                               "detail": "轮询超时", "steps": []})
    result = meter_698.meter_read_698({
        "meter_addr": "000000000001", "expect_timeout": 5, "query_max_timeout": False,
        "simcon": simcon, "frames": FRAMES, **NO_WAIT,
    })
    obs = result.observation
    assert obs.complete is False
    assert obs.reason_code in ("timeout", "incomplete")
    assert evaluate(obs, [{"op": "got"}]).state == "unknown"


def test_read_698_mislabeled_645_payload_is_reported_not_judged():
    """协议标签贴错（内层塞 645 帧）：facade 硬解出的 698「帧」FCS 校验不过——
    观测如实报「帧格式合法=False / OAD回显=False」，由步骤 expect 判 fail（不折 unknown）。"""
    app = bytes([2, 16]) + bytes.fromhex(F645_GATE)
    from parser_lib.adapters.adapter_10376 import build_frame as b13762

    outer = b13762(direction="up", afn=0x02, fn=1, appdata=app, info={"seq": 8})
    obs = meter_698.judge_read_698(outer.hex().upper(), expect_oad="20000201", frames=FRAMES)
    assert obs.complete is True                      # 帧拿到了、解得开 → 观测完整
    assert obs.values["帧格式合法"] is False          # 但 FCS 不过 → 非法，如实报
    assert obs.values["OAD回显"] is False
    assert evaluate(obs, [{"op": "eq", "field": "OAD回显", "value": True}]).state == "fail"


# --------------------------------------------------------------------------- #
# param.init_01h_f2
# --------------------------------------------------------------------------- #


def test_init_01h_f2_observes_confirm():
    simcon = FakeVerifySimcon({"ok": True, "steps": [
        {"index": 0, "result": "pass", "matched": CONFIRM_UP, "reason": "匹配成功"},
    ]})
    result = param_init.param_init_01h_f2({"simcon": simcon, "frames": FRAMES, **NO_WAIT})
    obs = result.observation
    assert obs.complete and obs.values["应答AFNFn"] == "00H-F1"
    assert evaluate(obs, [{"op": "eq", "field": "应答AFNFn", "value": "00H-F1"}]).state == "pass"
    # 下发形态：01H-F2 无数据单元；expect 只锁 00H 上行（确认/否认都是观测）
    step = simcon.submitted[0]["steps"][0]
    assert step["send"] == {"afn": "01", "fn": 2, "params": {}}
    assert step["expect"] == {"afn": "00", "dir": "up"}


def test_init_01h_f2_observes_deny():
    simcon = FakeVerifySimcon({"ok": False, "error": "job_not_passed", "steps": [
        {"index": 0, "result": "fail", "matched": DENY_6E_UP, "reason": "否认"},
    ]})
    result = param_init.param_init_01h_f2({"simcon": simcon, "frames": FRAMES, **NO_WAIT})
    obs = result.observation
    assert obs.complete
    assert obs.values["应答AFNFn"] == "00H-F2"
    assert obs.values["否认码"] == "6E"
    assert evaluate(obs, [{"op": "eq", "field": "应答AFNFn", "value": "00H-F1"}]).state == "fail"
    assert evaluate(obs, [{"op": "eq", "field": "否认码", "value": "6E"}]).state == "pass"


def test_init_01h_f2_silence_is_timeout():
    simcon = FakeVerifySimcon({"ok": False, "error": "job_not_passed", "steps": [
        {"index": 0, "result": "fail", "matched": None, "reason": "超时"},
    ]})
    result = param_init.param_init_01h_f2({"simcon": simcon, "frames": FRAMES, **NO_WAIT})
    assert result.observation.reason_code == "timeout"
    assert evaluate(result.observation, [{"op": "got"}]).state == "unknown"


# --------------------------------------------------------------------------- #
# 声明侧：5 块三字段 / observation_only / wait_param / 指纹
# --------------------------------------------------------------------------- #


def test_new_block_declarations_pass_loader_gates():
    from case_runner.blocks.loader import load_blocks

    specs = {spec.id: spec for spec in load_blocks()}
    for block_id in (
        "meter.batch_read", "gate.multi_frame_110", "gate.concurrent_109",
        "meter.read_698", "param.init_01h_f2",
    ):
        spec = specs[block_id]
        assert spec.observation_only is True, block_id
        assert "kind" in spec.model_fields_set, block_id
        assert "side_effect" in spec.model_fields_set, block_id
        assert "version" in spec.model_fields_set, block_id
        assert len(spec.fingerprint) == 16, block_id
    assert specs["meter.batch_read"].wait_param == "wait_s"
    assert specs["meter.read_698"].wait_param == "wait_s"
    assert specs["gate.multi_frame_110"].wait_param == ""
    assert specs["param.init_01h_f2"].side_effect == "reversible"
    assert specs["meter.batch_read"].side_effect == "none"
