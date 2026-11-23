"""``meter.*`` 三个抄表块 + ``sta.meter_addr_known`` 事实的单测（REQS-0041 BL-4 / FT-1~3）。

**不依赖真台架**：HTTP 走假 transport（``(method, url, body, timeout) -> (status, payload)``），
``FramesCapability`` 用真实 ``parser_lib.facade``（纯进程内、只读，无需服务）。

覆盖口径（任务书要求逐条对应）：

1. ``meter.addr_probe`` 用**实测上行帧** ``6821008300…1416`` 取出 ``000000000001`` → pass；
2. 地址不匹配 → **fail**，detail 含「期望/实际」；
3. 内层无 645 嵌套（``protocol=0``）→ 明确失败原因，**不得判 pass**；
4. ``meter.brain_local`` 正帧 pass、反帧（地址不符，不应答）也 pass，并覆盖
   ``reply_hex=None`` 分支；
5. ``sta.meter_addr_known`` 三态各一条；
6. ``unknown`` 绝不折算 pass（块态 + 执行器 outcome 两层都断言）；
7. ``meter.brain_local`` 请求帧**按 addr 动态构造**（旧硬编码帧只对 000000000001 有效，
   台架换地址即误判 FAIL）：新旧两帧逐字节对照 + CS 断言 + 显式 ``request_hex`` 优先。
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib

import pytest

import case_runner.blocks.common as common
import case_runner.blocks.facts_meter as facts_meter
import case_runner.blocks.meter as meter
from case_runner.capabilities.frames import FramesCapability
from case_runner.capabilities.simeter import SimeterCapability
from case_runner.capabilities.simcon import SimconCapability
from case_runner.core import defaults
from case_runner.core.executor import (
    BLOCK_TO_OUTCOME,
    RunContext,
    coerce_block_return,
    execute_case,
)
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_UNKNOWN,
    FACT_NO,
    FACT_UNKNOWN,
    FACT_YES,
    BlockSpec,
    CaseSpec,
    CaseStep,
    FactSpec,
)
from case_runner.core.registry import (
    BLOCK_REGISTRY,
    clear_registries,
    get_block,
    get_fact,
)

# --------------------------------------------------------------------------- #
# 实测素材（REQS §10.7 事实 E；表端脑应答帧由 sim_meter 离线跑出）
# --------------------------------------------------------------------------- #

#: 02H-F1 上行帧：内嵌 645 地址域 = 000000000001（事实 E 原帧）
MEASURED_UP_FRAME = "6821008300000000000002010002106800000000000168110433333433B3161416"
#: 645 读数据应答（DI 00010000 → 001234.56 kWh），由 sim_meter 表端脑离线产物的同构帧
MEASURED_645_REPLY = "6800000000000168910833333433896745339F16"
#: 表端脑自检正帧（本表地址）与其应答帧
SELF_CHECK_REQUEST = meter.SELF_CHECK_REQUEST
SELF_CHECK_REPLY = "6800000000000168910833333433896745339F16"
#: 表端脑自检反帧（地址不符的有效帧）
FOREIGN_REQUEST = meter.SELF_CHECK_FOREIGN_REQUEST

#: 实测上行帧 ``MEASURED_UP_FRAME`` 内层 645 地址域（=== 旧默认表地址）
MEASURED_ADDR = "000000000001"

#: 真实台架现状：模拟电表地址已改为 123456789012（645 地址域线序 12 34 56 78 90 12），
#: 下面两条帧都由台架实测取得（旧默认帧对它只会得到「非本表地址，不应答」）
BENCH_ADDR = "123456789012"
BENCH_REQUEST = "68123456789012681104333334336816"
BENCH_REPLY = "6812345678901268910833333433896745335416"

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
REAL_FRAMES = FramesCapability()


# --------------------------------------------------------------------------- #
# 隔离与注册
# --------------------------------------------------------------------------- #


def _reregister_all_domain_modules() -> None:
    """清表后尽量恢复各领域模块的注册（减少对其他测试文件的相互干扰）。"""
    package = "case_runner.blocks"
    try:
        blocks_pkg = importlib.import_module(package)
    except Exception:  # noqa: BLE001 - 并行开发期包可能不可用
        return
    for name in getattr(blocks_pkg, "iter_domain_modules", lambda: [])():
        try:
            module = importlib.import_module("%s.%s" % (package, name))
        except Exception:  # noqa: BLE001
            continue
        register = getattr(module, "register", None)
        if callable(register):
            try:
                register()
            except Exception:  # noqa: BLE001 - 他人在写的模块不影响本文件
                continue


@pytest.fixture(autouse=True)
def isolated_registries():
    """每个测试独立注册表；结束时把其他领域模块的注册恢复回去。"""
    clear_registries()
    meter.register()
    facts_meter.register()
    yield
    clear_registries()
    _reregister_all_domain_modules()


# --------------------------------------------------------------------------- #
# 假 transport（能力层契约：(method, url, body, timeout) -> (status, payload)）
# --------------------------------------------------------------------------- #


def make_simcon(*, reply_frame="", step_result="pass", reason="", job_state="succeeded",
                submit_status=202, frames_entries=None, raise_oserror=False):
    """假 simcon：verify → job → 步骤 ``matched`` 给上行帧。返回 ``(cap, calls)``。"""
    calls: list = []

    def transport(method, url, body, timeout):
        calls.append((method, url, body))
        if raise_oserror:
            raise OSError("connection refused")
        if url.endswith("/api/ai/v2/verification-runs"):
            return submit_status, {"job_id": "job-1", "job_state": "queued"}
        if "/api/ai/v2/jobs/" in url:
            steps = [{
                "index": 0, "name": "step", "sent_hex": "680F0043",
                "matched": reply_frame or None,
                "result": step_result, "reason": reason,
            }]
            return 200, {
                "job_id": "job-1", "job_state": job_state,
                "result": {"result": {"steps": steps, "run_id": "run-1",
                                      "frames_seq": [1, 2]}},
                "summary": reason or "ok",
            }
        if "/api/simcon/frames" in url:
            return 200, {"entries": list(frames_entries or []), "next_after_seq": 0}
        return 404, {"detail": "未 stub 的端点 %s" % url}

    cap = SimconCapability(base_url="http://fake-simcon", transport=transport)
    return cap, calls


def make_simeter(*, reply_hex=None, note=None, status=200, raise_oserror=False):
    """假 simeter：``handle_hex`` 直接回给定结构。返回 ``(cap, calls)``。"""
    calls: list = []

    def transport(method, url, body, timeout):
        calls.append((method, url, body))
        if raise_oserror:
            raise OSError("connection refused")
        if url.endswith("/api/simeter/handle_hex"):
            if status != 200:
                return status, {"detail": "boom"}
            return 200, {"reply_hex": reply_hex, "reply_wire_hex": reply_hex,
                         "note": dict(note or {}), "action": "645"}
        return 404, {"detail": "未 stub 的端点 %s" % url}

    cap = SimeterCapability(base_url="http://fake-simeter", transport=transport)
    return cap, calls


def ctx_with(**caps) -> RunContext:
    """把能力句柄挂进 ``RunContext.extras``（块执行体的 ctx）。"""
    ctx = RunContext(run_id="run-test")
    ctx.extras.update(caps)
    return ctx


def declared_block(block_id: str) -> BlockSpec:
    """读 ``data/blocks/*.json`` 里该块的声明（声明与执行体一致性也一并被测）。"""
    for path in sorted((DATA_DIR / "blocks").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("id") == block_id:
            return BlockSpec.model_validate(data)
    raise AssertionError("没找到块声明 %s" % block_id)


def action_of(block_id: str, **overrides) -> dict:
    """块的 ``action``（模拟执行器传给块执行体的 params），``overrides`` 走顶层覆盖。"""
    action = dict(declared_block(block_id).action)
    action.update(overrides)
    return action


def up_frame_with(inner_hex: str, protocol: int = 2) -> str:
    """用真实门面构一帧 02H-F1 上行帧（含指定内嵌报文）。"""
    built = REAL_FRAMES.build(
        "1376.2", {"afn": "02", "fn": 1,
                   "data": {"params": {"protocol": protocol, "payload": inner_hex}}}
    )
    assert built.get("ok"), built
    return meter.norm_hex(built["frame_hex"])


# --------------------------------------------------------------------------- #
# 0. 注册与声明
# --------------------------------------------------------------------------- #


def test_three_blocks_and_one_fact_registered():
    for block_id in ("meter.addr_probe", "meter.read_645", "meter.brain_local"):
        assert block_id in BLOCK_REGISTRY, "块未注册：%s" % block_id
        assert callable(get_block(block_id))
    assert callable(get_fact("sta.meter_addr_known"))


def test_register_is_idempotent():
    """register() 幂等（blocks/__init__.py 约定）：重复调用不抛 Duplicate。"""
    meter.register()
    facts_meter.register()
    assert "meter.addr_probe" in BLOCK_REGISTRY


def test_json_declarations_valid_and_consistent():
    expected_blocks = {
        # ``sta.meter_addr_known`` 是**参数化事实**（按 meter_addr 求值）：块回写只按
        # fact_id 缓存 → 换地址会假 PASS，故 provides 必须为空（与 nwk.nodes_online 同款）
        "meter.addr_probe": (["bench.simcon_online"], []),
        "meter.read_645": (["bench.simcon_online"], []),
        "meter.brain_local": (["bench.simeter_online"], []),
    }
    for block_id, (needs, provides) in expected_blocks.items():
        spec = declared_block(block_id)
        assert spec.needs == needs
        assert spec.provides == provides
        assert spec.assert_, "块声明应有 assert 判据：%s" % block_id
        assert spec.title

    fact_decl = json.loads(
        (DATA_DIR / "facts" / "sta_meter_addr_known.json").read_text(encoding="utf-8")
    )
    spec = FactSpec.model_validate(fact_decl)
    assert spec.id == "sta.meter_addr_known"
    assert spec.probe == "sta.meter_addr_known"
    assert "meter_addr" in spec.params_schema
    assert "addr" not in spec.params_schema, "D2-1 硬改名：不留 addr 别名"
    assert spec.parameterized is True


# --------------------------------------------------------------------------- #
# 1. 判据纯函数（真实 facade，无需服务）
# --------------------------------------------------------------------------- #


def test_judge_meter_addr_on_measured_frame_is_pass():
    verdict = meter.judge_meter_addr(MEASURED_UP_FRAME, "000000000001")
    assert verdict.state == BLOCK_PASS, verdict.detail
    assert "000000000001" in verdict.detail
    assert MEASURED_UP_FRAME in verdict.evidence


def test_judge_meter_addr_mismatch_is_fail_with_expected_and_actual():
    verdict = meter.judge_meter_addr(MEASURED_UP_FRAME, "000000000002")
    assert verdict.state == BLOCK_FAIL
    assert "期望地址 000000000002" in verdict.detail
    assert "实际 000000000001" in verdict.detail
    assert verdict.expected == "000000000002"
    assert verdict.actual == "000000000001"


def test_judge_meter_addr_without_nested_is_unknown_not_pass():
    no_nested = up_frame_with("681234567812345678", protocol=0)
    verdict = meter.judge_meter_addr(no_nested, "000000000001", protocol_type=0)
    assert verdict.state == BLOCK_UNKNOWN
    assert "内层无 645 嵌套" in verdict.detail


def test_judge_read_645_on_measured_reply():
    verdict = meter.judge_read_645(up_frame_with(MEASURED_645_REPLY), expect_di="00010000")
    assert verdict.state == BLOCK_PASS, verdict.detail
    assert verdict.actual["value"] == 1234.56
    assert "33333433" in "".join(str(i) for i in verdict.evidence).upper()


def test_judge_read_645_bad_cs_is_fail():
    bad_cs = MEASURED_645_REPLY[:-4] + "0000"  # CS/尾字节改坏（长度不变）
    verdict = meter.judge_read_645(up_frame_with(bad_cs), expect_di="00010000")
    assert verdict.state == BLOCK_FAIL
    assert "CS" in verdict.detail


def test_judge_read_645_wrong_di_is_fail():
    verdict = meter.judge_read_645(up_frame_with(MEASURED_645_REPLY), expect_di="02010100")
    assert verdict.state == BLOCK_FAIL
    assert "未回显 DI" in verdict.detail


def test_di_echo_hex_matches_measured_reply():
    assert meter.di_echo_hex("00010000") == "33333433"
    assert meter.di_echo_hex("") == ""


# --------------------------------------------------------------------------- #
# 2. meter.addr_probe
# --------------------------------------------------------------------------- #


def test_addr_probe_pass_on_measured_frame():
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME)
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail
    assert "000000000001" in result.detail
    # provides 只在 pass 时显式给事实取值（不凭空造）
    assert result.facts == {"sta.meter_addr_known": "yes"}
    # 下发的确实是 02H-F1 透传 645 13H（构帧走门面，实测产物）
    sent = calls[0][2]["task"]["steps"][0]["send"]
    assert sent["afn"] == "02" and sent["fn"] == 1
    assert sent["params"]["protocol"] == 2
    assert sent["params"]["payload"] == "68000000000001681300E416"


def test_addr_probe_mismatch_is_fail_with_expected_and_actual():
    simcon, _ = make_simcon(reply_frame=MEASURED_UP_FRAME)
    action = action_of("meter.addr_probe")
    action["params"] = dict(action["params"], meter_addr="000000000002")
    result = get_block("meter.addr_probe")(action, ctx_with(simcon=simcon))
    assert result.state == BLOCK_FAIL, result.detail
    assert "期望地址 000000000002" in result.detail
    assert "实际 000000000001" in result.detail
    assert not result.facts, "fail 的块不得回写 provides 事实"


def test_addr_probe_no_nested_protocol0_is_fail_not_pass():
    """内层无 645 嵌套（通信协议类型=0 不递归）：必须明确失败，绝不判 pass。"""
    no_nested = up_frame_with("681234567812345678", protocol=0)
    simcon, _ = make_simcon(reply_frame=no_nested)
    result = get_block("meter.addr_probe")(action_of("meter.addr_probe"), ctx_with(simcon=simcon))
    assert result.state == BLOCK_FAIL, result.detail
    assert result.state != BLOCK_PASS
    assert "内层无 645 嵌套" in result.detail or "通信协议类型" in result.detail
    assert "645" in result.detail


def test_addr_probe_poll_timeout_is_unknown_not_fail():
    simcon, _ = make_simcon(job_state="running")
    action = action_of("meter.addr_probe", max_poll_seconds=0.05)
    result = get_block("meter.addr_probe")(action, ctx_with(simcon=simcon))
    assert result.state == BLOCK_UNKNOWN, result.detail
    assert "超时" in result.detail or "poll_timeout" in result.detail
    assert BLOCK_TO_OUTCOME[result.state] != "pass"


def test_addr_probe_step_failed_is_fail_with_reason():
    simcon, _ = make_simcon(step_result="fail", reason="超时(8s)未收到期望帧")
    result = get_block("meter.addr_probe")(action_of("meter.addr_probe"), ctx_with(simcon=simcon))
    assert result.state == BLOCK_FAIL, result.detail
    assert "超时(8s)未收到期望帧" in result.detail


def test_addr_probe_no_session_404_is_skipped():
    simcon, _ = make_simcon(submit_status=404)
    result = get_block("meter.addr_probe")(action_of("meter.addr_probe"), ctx_with(simcon=simcon))
    assert result.state == BLOCK_SKIPPED, result.detail
    assert "404" in result.detail


def test_addr_probe_unreachable_is_skipped():
    simcon, _ = make_simcon(raise_oserror=True)
    result = get_block("meter.addr_probe")(action_of("meter.addr_probe"), ctx_with(simcon=simcon))
    assert result.state == BLOCK_SKIPPED, result.detail


def test_addr_probe_reply_via_frames_log_when_step_has_no_matched():
    """步骤没给 matched 时回查会话帧日志（run_id + up 02H-F1）。"""
    simcon, _ = make_simcon(
        reply_frame="",
        frames_entries=[{"seq": 2, "frame_hex": MEASURED_UP_FRAME, "afn": "02",
                         "fn": "F1", "updown": "up"}],
    )
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail


def test_addr_probe_accepts_capability_object_in_params():
    """能力对象可直接经块参数注入（离线回放/单测口径），不必挂 ctx.extras。"""
    simcon, _ = make_simcon(reply_frame=MEASURED_UP_FRAME)
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, simcon=simcon), None
    )
    assert result.state == BLOCK_PASS, result.detail


def test_addr_probe_builds_capability_from_base_url_and_transport():
    """无 ctx 时按块参数 base_url + transport 自建能力（生产入口的注入方式）。"""
    simcon, _ = make_simcon(reply_frame=MEASURED_UP_FRAME)
    action = action_of(
        "meter.addr_probe", meter_addr=MEASURED_ADDR,
        base_url="http://fake-simcon", transport=simcon.transport,
    )
    result = get_block("meter.addr_probe")(action, None)
    assert result.state == BLOCK_PASS, result.detail


# --------------------------------------------------------------------------- #
# 3. meter.read_645
# --------------------------------------------------------------------------- #


def test_read_645_pass_with_real_reply():
    simcon, calls = make_simcon(reply_frame=up_frame_with(MEASURED_645_REPLY))
    result = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=MEASURED_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail
    assert "1234.56" in result.detail
    sent = calls[0][2]["task"]["steps"][0]["send"]
    # 11H 读数据 DI 00010000 的实测构帧
    assert sent["params"]["payload"] == "6800000000000168110433333433B316"


def test_read_645_expect_value_match_and_mismatch():
    reply = up_frame_with(MEASURED_645_REPLY)
    ok_simcon, _ = make_simcon(reply_frame=reply)
    ok_result = get_block("meter.read_645")(
        action_of("meter.read_645", expect_value=1234.56), ctx_with(simcon=ok_simcon)
    )
    assert ok_result.state == BLOCK_PASS, ok_result.detail
    assert "与期望值" in ok_result.detail

    bad_simcon, _ = make_simcon(reply_frame=reply)
    bad_result = get_block("meter.read_645")(
        action_of("meter.read_645", expect_value=999.99), ctx_with(simcon=bad_simcon)
    )
    assert bad_result.state == BLOCK_FAIL, bad_result.detail
    assert "期望 999.99" in bad_result.detail and "1234.56" in bad_result.detail


def test_read_645_bad_cs_is_fail():
    bad_cs = MEASURED_645_REPLY[:-4] + "0000"
    simcon, _ = make_simcon(reply_frame=up_frame_with(bad_cs))
    result = get_block("meter.read_645")(action_of("meter.read_645"), ctx_with(simcon=simcon))
    assert result.state == BLOCK_FAIL, result.detail
    assert "CS" in result.detail


def test_read_645_unparseable_reply_is_unknown_not_pass():
    simcon, _ = make_simcon(reply_frame="68FF")
    result = get_block("meter.read_645")(action_of("meter.read_645"), ctx_with(simcon=simcon))
    assert result.state == BLOCK_UNKNOWN, result.detail
    assert BLOCK_TO_OUTCOME[result.state] == "inconclusive"


# --------------------------------------------------------------------------- #
# 4. meter.brain_local
# --------------------------------------------------------------------------- #


def test_brain_local_positive_frame_is_pass():
    simeter, calls = make_simeter(
        reply_hex=SELF_CHECK_REPLY,
        note={"di": "00010000", "func": "读数据", "value": "001234.56"},
    )
    result = get_block("meter.brain_local")(
        action_of("meter.brain_local", meter_addr=MEASURED_ADDR), ctx_with(simeter=simeter)
    )
    assert result.state == BLOCK_PASS, result.detail
    assert "001234.56" in result.detail and "33333433" in result.detail
    assert calls[0][2] == {"hex": SELF_CHECK_REQUEST}


def test_brain_local_negative_foreign_addr_no_reply_is_pass():
    """地址不符 → 表端脑不应答，**不应答正是期望** → pass。"""
    simeter, calls = make_simeter(
        reply_hex=None,
        note={"func": "非本表地址，不应答", "mine": False},
    )
    action = action_of(
        "meter.brain_local",
        request_hex=FOREIGN_REQUEST,
        expect_reply=False,
    )
    result = get_block("meter.brain_local")(action, ctx_with(simeter=simeter))
    assert result.state == BLOCK_PASS, result.detail
    assert "不应答" in result.detail
    assert calls[0][2] == {"hex": FOREIGN_REQUEST}


def test_brain_local_negative_but_replied_is_fail():
    simeter, _ = make_simeter(reply_hex=SELF_CHECK_REPLY, note={"mine": True})
    action = action_of("meter.brain_local", expect_reply=False)
    result = get_block("meter.brain_local")(action, ctx_with(simeter=simeter))
    assert result.state == BLOCK_FAIL, result.detail
    assert "却得到应答" in result.detail or "实际应答" in result.detail


def test_brain_local_negative_without_mine_flag_is_fail():
    """reply_hex=None 但 note.mine 缺失：不是「地址不符丢弃」，判 fail 不放过。"""
    simeter, _ = make_simeter(reply_hex=None, note={"error": "CS 校验失败，从站丢弃"})
    action = action_of("meter.brain_local", expect_reply=False)
    result = get_block("meter.brain_local")(action, ctx_with(simeter=simeter))
    assert result.state == BLOCK_FAIL, result.detail
    assert "note.mine=False" in result.detail


def test_brain_local_reply_none_when_reply_expected_is_fail():
    """``reply_hex=None`` 分支（正帧期望应答却没答）→ fail。"""
    simeter, _ = make_simeter(reply_hex=None, note={"error": "帧结构不合法"})
    result = get_block("meter.brain_local")(
        action_of("meter.brain_local"), ctx_with(simeter=simeter)
    )
    assert result.state == BLOCK_FAIL, result.detail
    assert "未应答" in result.detail and "reply_hex=None" in result.detail


def test_brain_local_missing_note_value_is_fail():
    simeter, _ = make_simeter(reply_hex=SELF_CHECK_REPLY, note={"di": "00010000"})
    result = get_block("meter.brain_local")(
        action_of("meter.brain_local"), ctx_with(simeter=simeter)
    )
    assert result.state == BLOCK_FAIL, result.detail
    assert "value" in result.detail


def test_brain_local_wrong_di_echo_is_fail():
    # 应答回显的是 DI 04000101（回显字节 34343337），不是期望的 00010000（33333433）
    simeter, _ = make_simeter(
        reply_hex="68000000000001689107343433375644590016",
        note={"di": "04000101", "value": "2026-11-23"},
    )
    result = get_block("meter.brain_local")(
        action_of("meter.brain_local"), ctx_with(simeter=simeter)
    )
    assert result.state == BLOCK_FAIL, result.detail
    assert "未回显 DI" in result.detail


def test_brain_local_simeter_unreachable_is_skipped():
    simeter, _ = make_simeter(raise_oserror=True)
    result = get_block("meter.brain_local")(
        action_of("meter.brain_local"), ctx_with(simeter=simeter)
    )
    assert result.state == BLOCK_SKIPPED, result.detail


# --------------------------------------------------------------------------- #
# 4b. 请求帧按地址动态构造（REQS-0041 修复：旧硬编码帧换表地址即误判 FAIL）
# --------------------------------------------------------------------------- #


def test_build_645_read_frame_bench_addr_is_measured_frame():
    """真实台架表地址 → 真实台架验证过的请求帧。"""
    frame, reason = meter.build_645_read_frame(BENCH_ADDR, "00010000")
    assert reason == ""
    assert frame == BENCH_REQUEST


def test_build_645_read_frame_default_addr_regression():
    """旧地址 000000000001 仍构造出旧的默认帧（回归）。"""
    frame, reason = meter.build_645_read_frame(meter.DEFAULT_ADDR, meter.DEFAULT_DI)
    assert (frame, reason) == (meter.SELF_CHECK_REQUEST, "")
    # 参数全省略时也按默认 addr/di 构帧
    assert meter.build_645_read_frame() == (meter.SELF_CHECK_REQUEST, "")


def test_build_645_read_frame_layout_and_cs():
    """帧结构 68|地址域|68|11|04|DI 传输序|CS|16，CS = 除尾 2 字节求和取低 8 位。"""
    for addr in (BENCH_ADDR, meter.DEFAULT_ADDR):
        frame, _ = meter.build_645_read_frame(addr, "00010000")
        raw = bytes.fromhex(frame)
        assert len(raw) == 16
        assert raw[0] == 0x68 and raw[7] == 0x68 and raw[-1] == 0x16
        assert raw[1:7].hex().upper() == addr          # 地址域原样（不反序）
        assert (raw[8], raw[9]) == (0x11, 0x04)        # 控制码 11H、数据长度 04
        assert raw[10:14].hex().upper() == "33333433"  # DI 显示序 → 传输序
        assert raw[14] == sum(raw[:-2]) & 0xFF
    assert BENCH_REQUEST[-4:-2] == "68"                # 实测帧 CS 字节
    assert meter.SELF_CHECK_REQUEST[-4:-2] == "B3"     # 旧默认帧 CS 字节


def test_build_645_read_frame_di_transport_order():
    frame, reason = meter.build_645_read_frame(BENCH_ADDR, "02010100")
    assert reason == ""
    assert frame[20:28] == meter.di_echo_hex("02010100")


def test_build_645_read_frame_rejects_bad_addr_or_di():
    frame, reason = meter.build_645_read_frame("12345", "00010000")
    assert frame == "" and "地址" in reason
    frame, reason = meter.build_645_read_frame("ZZZZZZZZZZZZ", "00010000")
    assert frame == "" and "地址" in reason
    frame, reason = meter.build_645_read_frame(BENCH_ADDR, "0001")
    assert frame == "" and "DI" in reason


def test_brain_local_builds_frame_from_addr_when_request_hex_absent():
    """缺省 request_hex（JSON 里为 null）→ 按 addr 动态构造，新表地址判 pass。"""
    simeter, calls = make_simeter(
        reply_hex=BENCH_REPLY,
        note={"di": "00010000", "func": "读数据", "value": "001234.56"},
    )
    result = get_block("meter.brain_local")(
        action_of("meter.brain_local", meter_addr=BENCH_ADDR), ctx_with(simeter=simeter)
    )
    assert result.state == BLOCK_PASS, result.detail
    assert calls[0][2] == {"hex": BENCH_REQUEST}


def test_brain_local_stale_default_frame_reproduces_bench_fail():
    """旧默认帧对台架表（123456789012）→ 表端「非本表地址」→ fail（修复前的现象）。"""
    simeter, calls = make_simeter(
        reply_hex=None, note={"func": "非本表地址，不应答", "mine": False}
    )
    result = get_block("meter.brain_local")(
        action_of("meter.brain_local", meter_addr=meter.DEFAULT_ADDR),
        ctx_with(simeter=simeter),
    )
    assert result.state == BLOCK_FAIL, result.detail
    assert "未应答" in result.detail
    assert calls[0][2] == {"hex": meter.SELF_CHECK_REQUEST}


def test_brain_local_explicit_request_hex_beats_addr():
    """显式 request_hex 优先于 addr 动态构帧（既有能力保留）。"""
    simeter, calls = make_simeter(
        reply_hex=None, note={"func": "非本表地址，不应答", "mine": False}
    )
    action = action_of(
        "meter.brain_local",
        meter_addr=BENCH_ADDR,
        request_hex=FOREIGN_REQUEST,
        expect_reply=False,
    )
    result = get_block("meter.brain_local")(action, ctx_with(simeter=simeter))
    assert result.state == BLOCK_PASS, result.detail
    assert calls[0][2] == {"hex": FOREIGN_REQUEST}


def test_brain_local_bad_addr_is_unknown_not_fail():
    """addr 非法 → 构帧失败按配置错误判 unknown（绝不假 FAIL/PASS）。"""
    simeter, calls = make_simeter(reply_hex=None, note={"mine": False})
    result = get_block("meter.brain_local")(
        action_of("meter.brain_local", meter_addr="12345"), ctx_with(simeter=simeter)
    )
    assert result.state == BLOCK_UNKNOWN, result.detail
    assert "构帧失败" in result.detail
    assert calls == []          # 构帧失败就不该去打扰表端脑


def test_read_645_uses_same_frame_builder():
    """``meter.read_645`` 与 ``meter.brain_local`` 共用同一份构帧实现。"""
    simcon, calls = make_simcon(reply_frame=up_frame_with(BENCH_REPLY))
    result = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=BENCH_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail
    sent = calls[0][2]["task"]["steps"][0]["send"]
    assert sent["params"]["payload"] == BENCH_REQUEST


# --------------------------------------------------------------------------- #
# 4c. 地址角色（REQS-0042 §3.1 / S1.3）：电表侧**不反转**，且与档案侧互为反序
# --------------------------------------------------------------------------- #


def test_read_645_frame_address_field_is_meter_addr_wire():
    """★ ``meter.read_645(meter_addr="123456789012")`` → 帧地址域 = ``bytes.fromhex(...)``。

    645 地址域人读序即线序（**不反转**）——反转是档案侧的事（``archive_addr_of``）。
    """
    simcon, calls = make_simcon(reply_frame=up_frame_with(BENCH_REPLY))
    result = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=BENCH_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail
    sent = calls[0][2]["task"]["steps"][0]["send"]
    raw = bytes.fromhex(sent["params"]["payload"])
    assert (raw[0], raw[7], raw[-1]) == (0x68, 0x68, 0x16)
    assert raw[1:7] == bytes.fromhex(BENCH_ADDR)          # 线序 = 人读序（不反转）
    assert raw[1:7] == common.meter_addr_wire(BENCH_ADDR)  # 唯一换算处
    # 反证：若误用档案侧换算，地址域会变成 12 90 78 56 34 12 —— 那是错帧
    assert raw[1:7] != bytes.fromhex(common.archive_addr_of(BENCH_ADDR))


def test_meter_blocks_fall_back_to_config_default_addr():
    """块声明 ``meter_addr: null`` + 不传参 → 用 ``case_defaults.meter_addr``（D2-2）。"""
    expected = defaults.default_meter_addr()
    assert expected, "出厂 case_defaults.json 应给出台架表地址"
    simcon, calls = make_simcon(reply_frame=up_frame_with(MEASURED_645_REPLY))
    result = get_block("meter.read_645")(action_of("meter.read_645"), ctx_with(simcon=simcon))
    assert result.state == BLOCK_PASS, result.detail
    sent = calls[0][2]["task"]["steps"][0]["send"]
    raw = bytes.fromhex(sent["params"]["payload"])
    assert raw[1:7] == bytes.fromhex(expected)
    assert raw[1:7] == common.meter_addr_wire(expected)


def test_meter_blocks_reject_invalid_given_addr():
    """给了但不合法 ≠ 没给：显式垃圾地址不静默换默认表，按配置错误判 unknown。"""
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME)
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr="12345"), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_UNKNOWN, result.detail
    assert "非法" in result.detail and "12345" in result.detail
    assert calls == []          # 参数错 → 不下发


def test_brain_local_negative_fixture_keeps_its_foreign_address():
    """★ 负例夹具 ``negative_addr_mismatch`` 是**故意**给错地址的，不许被「统一地址」改掉。

    显式 ``request_hex`` 优先于 ``meter_addr`` 动态构帧；把反帧换成本表地址帧会把负例
    变成正例（永远 PASS），负例就白测了。
    """
    simeter, calls = make_simeter(
        reply_hex=None, note={"func": "非本表地址，不应答", "mine": False}
    )
    action = action_of(
        "meter.brain_local", meter_addr=BENCH_ADDR,
        request_hex=FOREIGN_REQUEST, expect_reply=False,
    )
    result = get_block("meter.brain_local")(action, ctx_with(simeter=simeter))
    assert result.state == BLOCK_PASS, result.detail
    assert calls[0][2] == {"hex": FOREIGN_REQUEST}
    # 反帧确实是外来的（与本表地址帧不同）
    assert FOREIGN_REQUEST != meter.build_645_read_frame(BENCH_ADDR, "00010000")[0]


# --------------------------------------------------------------------------- #
# 5. 事实 sta.meter_addr_known 三态
# --------------------------------------------------------------------------- #


def test_fact_yes_when_addr_matched():
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME)
    value = get_fact("sta.meter_addr_known")({"meter_addr": "000000000001", "simcon": simcon})
    assert value.state == FACT_YES, value.detail
    assert MEASURED_UP_FRAME in value.evidence
    assert calls, "事实探测必须真的下发 02H-F1（不是空转）"


def test_fact_no_when_addr_mismatched():
    simcon, _ = make_simcon(reply_frame=MEASURED_UP_FRAME)
    value = get_fact("sta.meter_addr_known")({"meter_addr": "000000000002", "simcon": simcon})
    assert value.state == FACT_NO, value.detail
    assert "不匹配" in value.detail


def test_fact_unknown_when_no_nested():
    no_nested = up_frame_with("681234567812345678", protocol=0)
    simcon, _ = make_simcon(reply_frame=no_nested)
    value = get_fact("sta.meter_addr_known")(
        {"meter_addr": "000000000001", "simcon": simcon, "protocol": 0}
    )
    assert value.state == FACT_UNKNOWN, value.detail


def test_fact_unknown_when_service_unreachable():
    simcon, _ = make_simcon(raise_oserror=True)
    value = get_fact("sta.meter_addr_known")({"meter_addr": "000000000001", "simcon": simcon})
    assert value.state == FACT_UNKNOWN, value.detail
    assert value.state != FACT_NO


def test_fact_unknown_when_no_simcon_injected():
    value = get_fact("sta.meter_addr_known")({"meter_addr": "000000000001"})
    assert value.state == FACT_UNKNOWN
    assert "未注入 simcon" in value.detail


def test_fact_unknown_on_poll_timeout():
    simcon, _ = make_simcon(job_state="running")
    value = get_fact("sta.meter_addr_known")(
        {"meter_addr": "000000000001", "simcon": simcon, "max_poll_seconds": 0.05}
    )
    assert value.state == FACT_UNKNOWN, value.detail


# --------------------------------------------------------------------------- #
# 6. 与执行器/规划器接线：provides 回写、unknown 绝不折算 pass
# --------------------------------------------------------------------------- #


def _meter_case(block_ids):
    return CaseSpec(
        id="case.meter.smoke",
        title="抄表是否正常（addr_probe + read_645）",
        steps=[CaseStep(no=1, do="STA 获取表地址", expect="内层 645 地址域一致")],
        blocks=list(block_ids),
    )


def test_executor_runs_declared_blocks_and_does_not_cache_parameterized_fact():
    """真执行器 + 真声明跑两条块：结论 pass，且**参数化事实不得被块回写**。

    历史写法断言「addr_probe provides 的事实被回写」；REQS-0042 起 ``sta.meter_addr_known``
    是参数化事实（按 meter_addr 求值），回写只按 fact_id 缓存 → 换一只表探测会命中旧结论
    = 跨地址假 PASS，所以 provides 置空，这里改断言**缓存里没有它**。
    """
    addr_up = MEASURED_UP_FRAME
    read_up = up_frame_with(MEASURED_645_REPLY)
    issued = []

    def sequenced_transport(method, url, body, timeout):
        if url.endswith("/api/ai/v2/verification-runs"):
            issued.append(1)
            return 202, {"job_id": "job-%d" % len(issued)}
        if "/api/ai/v2/jobs/" in url:
            job = url.split("/jobs/")[1].split("?")[0]
            up = addr_up if job == "job-1" else read_up
            return 200, {
                "job_id": job, "job_state": "succeeded",
                "result": {"result": {"steps": [
                    {"index": 0, "name": "step", "matched": up, "result": "pass",
                     "reason": "匹配成功"}], "run_id": "run-1"}},
            }
        return 404, {"detail": "no"}

    cap = SimconCapability(base_url="http://fake-simcon", transport=sequenced_transport)
    specs = {}
    for bid in ("meter.addr_probe", "meter.read_645"):
        declared = declared_block(bid)
        action = json.loads(json.dumps(declared.action, ensure_ascii=False))
        # 声明里 ``meter_addr: null``（= 取配置默认值）；实测素材的地址是 000000000001，
        # 这里钉住它，免得断言随现场 case_defaults.json 漂
        action.setdefault("params", {})["meter_addr"] = MEASURED_ADDR
        specs[bid] = BlockSpec(
            id=bid, title=declared.title, needs=[], provides=declared.provides, action=action
        )
    ctx = RunContext(run_id="run-e2e", block_specs=specs, extras={"simcon": cap})
    result = execute_case(_meter_case(["meter.addr_probe", "meter.read_645"]), ctx)

    assert result.verdict == "pass", result.to_dict()
    assert [b.block_id for b in result.blocks] == ["meter.addr_probe", "meter.read_645"]
    assert all(b.state == BLOCK_PASS for b in result.blocks)
    # 参数化事实（按 meter_addr 求值）不得被块回写缓存：换地址即假 PASS
    assert ctx.facts_ctx.peek("sta.meter_addr_known") is None
    assert specs["meter.addr_probe"].provides == []


def test_executor_keeps_unknown_out_of_pass_count():
    simcon, _ = make_simcon(job_state="running")
    spec = declared_block("meter.addr_probe")
    specs = {"meter.addr_probe": BlockSpec(
        id=spec.id, title=spec.title, needs=[], provides=spec.provides, action=spec.action)}
    ctx = RunContext(run_id="run-unknown", block_specs=specs,
                     extras={"simcon": simcon,
                             "capabilities": {"simcon": simcon}})
    ctx.block_params["meter.addr_probe"] = {"max_poll_seconds": 0.05}
    result = execute_case(_meter_case(["meter.addr_probe"]), ctx)

    assert result.verdict != "pass"
    assert result.counts["pass"] == 0
    assert result.counts["unknown"] == 1
    assert result.blocks[0].outcome == "inconclusive"


def test_coerce_never_turns_none_into_pass():
    """块返回 None / 无法识别的字符串 → unknown（回归保护，防后人改坏口径）。"""
    assert coerce_block_return(None).state == BLOCK_UNKNOWN
    assert coerce_block_return("随便什么").state == BLOCK_UNKNOWN
    assert coerce_block_return({"detail": "只有说明没有结论"}).state == BLOCK_UNKNOWN


def test_executor_skips_block_via_capabilities_mapping():
    """``ctx.extras['capabilities']['simcon']`` 也能注入（接线兼容），无会话 → skipped。"""
    simcon, _ = make_simcon(submit_status=404)
    ctx = ctx_with(capabilities={"simcon": simcon})
    result = get_block("meter.addr_probe")(action_of("meter.addr_probe"), ctx)
    assert result.state == BLOCK_SKIPPED, result.detail


# --------------------------------------------------------------------------- #
# 7. 集成路径（有工作台就跑真路径，没有就 skip —— 绝不因台架缺失变红）
# --------------------------------------------------------------------------- #

#: 工作台地址（不写死端口在库里；这里只是测试缺省，可用环境变量覆盖）
LIVE_BASE = os.environ.get("WORKBENCH_BASE", "http://127.0.0.1:8790")


def _live_simeter_or_skip():
    """拿一个可达且表地址为 000000000001 的 simeeter 能力；否则 skip。"""
    from case_runner.capabilities.simeter import SimeterCapability

    cap = SimeterCapability(base_url=LIVE_BASE, timeout=5.0, retries=0)
    health = cap.probe()
    if not health.ok:
        pytest.skip("工作台 simeter 不可用，跳过集成路径：%s" % health.reason)
    status = cap.status()
    if status.get("addr") != meter.DEFAULT_ADDR:
        pytest.skip("模拟电表地址 %r 非 %s，跳过（本用例按该地址构帧）"
                    % (status.get("addr"), meter.DEFAULT_ADDR))
    return cap


def test_live_brain_local_roundtrip():
    """真跑：``handle_hex`` 正帧 pass、地址不符反帧 pass（不占串口）。"""
    simeter = _live_simeter_or_skip()
    positive = get_block("meter.brain_local")(
        action_of("meter.brain_local"), ctx_with(simeter=simeter)
    )
    assert positive.state == BLOCK_PASS, positive.detail
    assert "33333433" in positive.detail

    negative = get_block("meter.brain_local")(
        action_of("meter.brain_local", request_hex=FOREIGN_REQUEST, expect_reply=False),
        ctx_with(simeter=simeter),
    )
    assert negative.state == BLOCK_PASS, negative.detail
    assert "不应答" in negative.detail
