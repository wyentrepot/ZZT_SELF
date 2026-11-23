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
   台架换地址即误判 FAIL）：新旧两帧逐字节对照 + CS 断言 + 显式 ``request_hex`` 优先；
8. 等待型重试（REQS-0042 S2-F，``wait_s``/``poll_interval_s``）：``wait_s=0`` 与改造前
   **逐字一致**（只探一次、不 sleep）；等待到成功 / 等满无应答（``unknown``，不是 fail）/
   有应答但判据不成立（``fail``，不被重试掩盖）/ 等待期只发只读透传帧 / 写帧 payload 被
   只读闸门拦下 / 执行器把「等了 N 秒、探了 K 次」派生进 ``decisions[]``。
   **零真实等待**（注入虚拟时钟）、**零真实网络**（假 simcon 按 plan 逐次应答）。
"""

from __future__ import annotations

import importlib
import json
import os
import pathlib
import time

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

#: 645「读通信地址」（13H）请求帧，``addr_mode="meter"``（**默认**、本台 CCO 可用）：
#: 地址域 = 表地址线序（这里是旧默认表 000000000001），CS=0xE4。
ADDR_PROBE_REQUEST_METER = "68000000000001681300E416"
#: 同上，但表地址 = 台架现用地址 123456789012 的线序，CS=0x99。
ADDR_PROBE_REQUEST_BENCH = "681234567890126813009916"
#: 645「读通信地址」（13H）请求帧，``addr_mode="broadcast"``：**地址域全 FEH**
#: （DL/T645-2007 §6.3 规范写法 ``01_DLT645_帧格式.md:326-340``）；与表地址无关，
#: CS = (0x68 + 6×0xFE + 0x68 + 0x13 + 0x00) & 0xFF = 0xD7。
#: 本台 CCO 对 ``02H-F1``+FE 不转发（实测回空帧，见 ``evidence/0042/s2-meter_read-13h-FE.log``）。
ADDR_PROBE_REQUEST_BROADCAST = "68FEFEFEFEFEFE681300D716"


def timeout_up_frame(seconds: int = 90) -> str:
    """用真实门面构一帧 ``03H-F7`` 上行应答（数据项「最大超时时间」= ``seconds`` 秒）。"""
    built = REAL_FRAMES.build(
        "1376.2",
        {"direction": "up", "afn": "03", "fn": "F7",
         "data": {"params": {"timeout": int(seconds)}}},
    )
    assert built.get("ok"), built
    return meter.norm_hex(built["frame_hex"])


#: 03H-F7 上行应答（t = 90s，真机 CCO 的常见出厂值）= ``timeout_up_frame(90)``
TIMEOUT_UP_FRAME = "681000830000000000000340005A2016"

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
                submit_status=202, frames_entries=None, raise_oserror=False,
                timeout_frame="", timeout_step_result="pass",
                timeout_reason="模块不支持 03H-F7（或未应答）"):
    """假 simcon：verify → job → 步骤 ``matched`` 给上行帧。返回 ``(cap, calls)``。

    ``03H-F7``（取 t）与 ``02H-F1``（透传）**分开应答**：``timeout_frame`` 为空即
    模拟「模块不支持/查不到 t」→ 块按规范回落到 60s。``calls`` 里两条都记，
    断言时用 :func:`forward_steps` / :func:`timeout_steps` 各取各的。
    """
    calls: list = []
    submitted: dict = {}

    def _afn_of(step) -> str:
        return str(((step or {}).get("send") or {}).get("afn", "")).upper()

    def transport(method, url, body, timeout):
        calls.append((method, url, body))
        if raise_oserror:
            raise OSError("connection refused")
        if url.endswith("/api/ai/v2/verification-runs"):
            step = ((body or {}).get("task") or {}).get("steps", [{}])[0]
            submitted["timeout_query"] = _afn_of(step) == "03"
            return submit_status, {"job_id": "job-1", "job_state": "queued"}
        if "/api/ai/v2/jobs/" in url:
            if submitted.get("timeout_query"):
                steps = [{
                    "index": 0, "name": "查最大超时时间", "sent_hex": "680F0043",
                    "matched": timeout_frame or None,
                    "result": timeout_step_result, "reason": timeout_reason,
                }]
                return 200, {
                    "job_id": "job-t", "job_state": "succeeded",
                    "result": {"result": {"steps": steps, "run_id": "run-t"}},
                    "summary": timeout_reason,
                }
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


def _submitted_steps(calls) -> list:
    """假 transport 记下的所有 ``/verification-runs`` 请求里的步骤（按提交顺序）。"""
    out = []
    for _method, url, body in calls:
        if not str(url).endswith("/api/ai/v2/verification-runs"):
            continue
        if not isinstance(body, dict):
            continue
        out.extend((body.get("task") or {}).get("steps") or [])
    return out


def forward_steps(calls) -> list:
    """挑出「02H-F1 数据转发」那几步（忽略前置的 03H-F7 取 t 查询）。"""
    return [s for s in _submitted_steps(calls)
            if str((s.get("send") or {}).get("afn", "")).upper() == "02"]


def timeout_steps(calls) -> list:
    """挑出「03H-F7 取 t」那几步。"""
    return [s for s in _submitted_steps(calls)
            if str((s.get("send") or {}).get("afn", "")).upper() == "03"]


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
    # 下发的确实是 02H-F1 透传 645 13H；缺省 addr_mode="meter" → 地址域 = 该表线序
    sent = forward_steps(calls)[0]["send"]
    assert sent["afn"] == "02" and sent["fn"] == 1
    assert sent["params"]["protocol"] == 2
    assert sent["params"]["payload"] == ADDR_PROBE_REQUEST_METER


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
    sent = forward_steps(calls)[0]["send"]
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
    sent = forward_steps(calls)[0]["send"]
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
    sent = forward_steps(calls)[0]["send"]
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
    sent = forward_steps(calls)[0]["send"]
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
# 4d. 13H「读通信地址」请求帧：``addr_mode`` 两套地址域口径（REQS-0042 裁决）
#     默认 "meter"（本台可用）；"broadcast" = DL/T645-2007 §6.3 规范全 FE
# --------------------------------------------------------------------------- #


def addr_reply_up_frame(addr: str) -> str:
    """构一帧「02H-F1 透传 645 13H 应答」上行帧：应答地址域与回装通信地址都是 ``addr``。"""
    wire = common.meter_addr_wire(addr)
    inner_body = b"\x68" + wire + bytes([0x68, 0x93, 0x06]) + wire
    inner = (inner_body + bytes([sum(inner_body) & 0xFF, 0x16])).hex().upper()
    return up_frame_with(inner)


def empty_forward_up_frame() -> str:
    """真机回归素材：本台 CCO 对 ``02H-F1``+FE 回的**空数据单元**上行帧。

    出处 ``evidence/0042/s2-meter_read-13h-FE.log``：s1 fail「该帧没有内嵌帧
    （上行数据单元为空）」「上行帧里没有「转发报文」字段」。
    """
    built = REAL_FRAMES.build(
        "1376.2",
        {"direction": "up", "afn": "02", "fn": 1,
         "data": {"params": {"protocol": 2, "payload": ""}}},
    )
    assert built.get("ok"), built
    return meter.norm_hex(built["frame_hex"])


def test_build_645_addr_mode_default_is_meter_addr():
    """★ 缺省 ``addr_mode="meter"``：13H 地址域 = 该表线序（本台 CCO 能单播转发）。"""
    frame, reason = meter.build_645_request(
        REAL_FRAMES, addr=MEASURED_ADDR, control=meter.CONTROL_READ_ADDR
    )
    assert (frame, reason) == (ADDR_PROBE_REQUEST_METER, "")
    raw = bytes.fromhex(frame)
    assert len(raw) == 12                        # 68|地址域×6|68|13|00|CS|16
    assert raw[0] == 0x68 and raw[7] == 0x68 and raw[-1] == 0x16
    assert raw[1:7] == bytes.fromhex(MEASURED_ADDR)   # 真实表地址线序
    assert raw[1:7] != b"\xFE" * 6                    # 缺省模式**不**出现广播地址
    assert (raw[8], raw[9]) == (0x13, 0x00)      # 控制码 13H、数据长度 00
    assert raw[10] == sum(raw[:-2]) & 0xFF       # CS = 除尾 2 字节求和取低 8 位
    assert raw[10] == 0xE4
    # 台架现用地址同样按"该表地址"构帧
    assert meter.build_645_request(
        REAL_FRAMES, addr=BENCH_ADDR, control=meter.CONTROL_READ_ADDR
    )[0] == ADDR_PROBE_REQUEST_BENCH


def test_build_645_addr_mode_broadcast_is_fe_frame():
    """★ ``addr_mode="broadcast"``：锁定 ``68 FE FE FE FE FE FE 68 13 00 D7 16``（§6.3）。"""
    frame, reason = meter.build_645_request(
        REAL_FRAMES, addr=BENCH_ADDR, control=meter.CONTROL_READ_ADDR,
        addr_mode=meter.ADDR_MODE_BROADCAST,
    )
    assert (frame, reason) == (ADDR_PROBE_REQUEST_BROADCAST, "")
    raw = bytes.fromhex(frame)
    assert raw[1:7] == b"\xFE" * 6               # 全 FEH 广播（与表地址无关）
    assert raw[10] == sum(raw[:-2]) & 0xFF == 0xD7
    # 换任何表地址都是同一帧
    for addr in (MEASURED_ADDR, BENCH_ADDR, meter.DEFAULT_ADDR, "FEFEFEFEFEFE", "", None):
        assert meter.build_645_request(
            REAL_FRAMES, addr=addr, control=meter.CONTROL_READ_ADDR,
            addr_mode="broadcast",
        )[0] == ADDR_PROBE_REQUEST_BROADCAST


def test_build_645_request_rejects_unknown_addr_mode():
    """非法 ``addr_mode`` → 构帧失败（配置错误），绝不静默落回某个口径。"""
    frame, reason = meter.build_645_request(
        REAL_FRAMES, addr=MEASURED_ADDR, control=meter.CONTROL_READ_ADDR,
        addr_mode="broadcast2",
    )
    assert frame == ""
    assert "addr_mode" in reason and "broadcast2" in reason


def test_build_645_request_keeps_real_addr_for_read_data():
    """``addr_mode`` 只作用于 13H；11H 读数据永远带真实表地址。"""
    for mode in (meter.ADDR_MODE_METER, meter.ADDR_MODE_BROADCAST):
        frame, reason = meter.build_645_request(
            REAL_FRAMES, addr=BENCH_ADDR, control=meter.CONTROL_READ_DATA,
            di="00010000", addr_mode=mode,
        )
        assert reason == ""
        raw = bytes.fromhex(frame)
        assert raw[1:7] == bytes.fromhex(BENCH_ADDR)      # 真实表地址，不是 FE
        assert raw[1:7] != b"\xFE" * 6
        assert (raw[8], raw[9]) == (0x11, 0x04)           # 控制码 11H、DI 长度 04
        assert frame.startswith("68123456789012" + "6811" + "04")
    # 注意：门面按 645 传输层做 1BH 转义，本例 CS 字节恰为 1BH → 与本地构帧器
    # ``build_645_read_frame`` 的产物不逐字节相等（既有差异，非本次改动引入）。
    assert meter.build_645_read_frame(BENCH_ADDR, "00010000")[0] == BENCH_REQUEST


def test_addr_probe_default_mode_sends_meter_addr_and_judges_reply():
    """★ 缺省模式：请求帧带该表地址；判据仍比**应答回装**的地址域。"""
    simcon, calls = make_simcon(reply_frame=addr_reply_up_frame(BENCH_ADDR))
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=BENCH_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail
    assert "123456789012" in result.detail
    assert forward_steps(calls)[0]["send"]["params"]["payload"] == ADDR_PROBE_REQUEST_BENCH

    # 判据与请求口径解耦：期望地址换成另一只表 → fail，期望/实际都对得上
    other, _ = make_simcon(reply_frame=addr_reply_up_frame(BENCH_ADDR))
    mismatched = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR), ctx_with(simcon=other)
    )
    assert mismatched.state == BLOCK_FAIL, mismatched.detail
    assert "期望地址 000000000001" in mismatched.detail
    assert "实际 123456789012" in mismatched.detail


def test_addr_probe_broadcast_mode_sends_fe_and_judges_same_reply():
    """★ ``addr_mode="broadcast"``：请求帧全 FE，判据与缺省模式**完全相同**。"""
    simcon, calls = make_simcon(reply_frame=addr_reply_up_frame(BENCH_ADDR))
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=BENCH_ADDR, addr_mode="broadcast"),
        ctx_with(simcon=simcon),
    )
    assert result.state == BLOCK_PASS, result.detail
    assert "123456789012" in result.detail
    assert forward_steps(calls)[0]["send"]["params"]["payload"] == ADDR_PROBE_REQUEST_BROADCAST

    other, other_calls = make_simcon(reply_frame=addr_reply_up_frame(BENCH_ADDR))
    mismatched = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, addr_mode="broadcast"),
        ctx_with(simcon=other),
    )
    assert mismatched.state == BLOCK_FAIL, mismatched.detail
    assert "实际 123456789012" in mismatched.detail
    assert forward_steps(other_calls)[0]["send"]["params"]["payload"] == ADDR_PROBE_REQUEST_BROADCAST


def test_addr_probe_broadcast_mode_empty_reply_reproduces_bench_fail():
    """★ 真机回归（evidence/0042/s2-meter_read-13h-FE.log）：``broadcast`` 模式下本台 CCO
    回空数据单元 → 必须判 **fail**（有上行帧但内层无 645 嵌套），不许判 pass。"""
    simcon, calls = make_simcon(reply_frame=empty_forward_up_frame())
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=BENCH_ADDR, addr_mode="broadcast"),
        ctx_with(simcon=simcon),
    )
    assert result.state == BLOCK_FAIL, result.detail
    assert "内层无 645 嵌套" in result.detail
    assert "broadcast" in result.detail          # 排障时能看出用的哪套地址口径
    assert forward_steps(calls)[0]["send"]["params"]["payload"] == ADDR_PROBE_REQUEST_BROADCAST


def test_addr_probe_default_mode_also_fails_on_empty_reply():
    """对照：空数据单元在**缺省模式**下同样判 fail（口径没变，只是请求地址域不同）。"""
    simcon, calls = make_simcon(reply_frame=empty_forward_up_frame())
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=BENCH_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_FAIL, result.detail
    assert "内层无 645 嵌套" in result.detail and "meter" in result.detail
    assert forward_steps(calls)[0]["send"]["params"]["payload"] == ADDR_PROBE_REQUEST_BENCH


def test_addr_probe_rejects_unknown_addr_mode():
    """非法 ``addr_mode`` → unknown（配置错误），**不下发**、绝不假 PASS/FAIL。"""
    simcon, calls = make_simcon(reply_frame=addr_reply_up_frame(BENCH_ADDR))
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=BENCH_ADDR, addr_mode="fe"),
        ctx_with(simcon=simcon),
    )
    assert result.state == BLOCK_UNKNOWN, result.detail
    assert "addr_mode" in result.detail
    assert calls == [], "参数非法就不该下发"


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
    forwards = []

    def sequenced_transport(method, url, body, timeout):
        if url.endswith("/api/ai/v2/verification-runs"):
            step = ((body or {}).get("task") or {}).get("steps", [{}])[0]
            issued.append(str((step.get("send") or {}).get("afn", "")).upper())
            return 202, {"job_id": "job-%d" % len(issued)}
        if "/api/ai/v2/jobs/" in url:
            job = url.split("/jobs/")[1].split("?")[0]
            afn = issued[int(job.split("-")[1]) - 1]
            if afn == "03":                      # 取 t 的前置查询（每运行只查一次）
                up = TIMEOUT_UP_FRAME
            else:
                forwards.append(afn)
                up = addr_up if len(forwards) == 1 else read_up
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
# 6b. 等待口径（REQS-0042 D5-2）：显式 > 03H-F7 的 t+5s > 回落 60s
# --------------------------------------------------------------------------- #

def test_timeout_fixture_matches_facade_build():
    """自带 03H-F7 应答素材 = 门面实时构帧（夹具不漂、不是手搓的假帧）。"""
    assert timeout_up_frame(90) == TIMEOUT_UP_FRAME


def test_max_timeout_of_parses_03f7_reply():
    """真解析：``adapter_10376`` 的 ``afn==0x03 and fn==7`` 分支解码出「最大超时时间」。"""
    value, note = meter.max_timeout_of(REAL_FRAMES, TIMEOUT_UP_FRAME)
    assert value == 90.0
    assert "03H-F7" in note and "90" in note


def test_max_timeout_of_other_frame_is_none_not_guess():
    """非 03H-F7 帧（这里塞 02H-F1 上行帧）→ 取不到 t，绝不猜值。"""
    value, note = meter.max_timeout_of(REAL_FRAMES, MEASURED_UP_FRAME)
    assert value is None
    assert "最大超时时间" in note


def test_max_timeout_of_broken_hex_is_none():
    value, note = meter.max_timeout_of(REAL_FRAMES, "68FF")
    assert value is None and note


def test_resolve_timeout_prefers_explicit_value():
    """优先级最高：调用方显式 ``expect_timeout``（连 03H-F7 都不查）。"""
    called = []
    timeout, note = meter.resolve_expect_timeout(
        {"expect_timeout": 12, "max_timeout_provider": lambda: called.append(1) or 90}
    )
    assert timeout == 12.0
    assert "显式" in note
    assert called == []
    # 非法显式值不静默生效，走后面的口径
    timeout, _ = meter.resolve_expect_timeout(
        {"expect_timeout": "abc", "max_timeout_provider": lambda: 90}
    )
    assert timeout == 95.0


def test_resolve_timeout_uses_provider_t_plus_5s():
    timeout, note = meter.resolve_expect_timeout({"max_timeout_provider": lambda: 90})
    assert timeout == 95.0
    assert "t+5s" in note and "95" in note


def test_resolve_timeout_provider_keyword_style_and_tuple():
    """钩子也支持 ``(simcon=, frames=, ctx=)`` 关键字形态，并可返回 ``(t, 说明)``。"""
    seen = {}

    def provider(*, simcon=None, frames=None, ctx=None):
        seen.update({"simcon": simcon, "frames": frames, "ctx": ctx})
        return ("88s", "台架实测 t")

    timeout, note = meter.resolve_expect_timeout({"max_timeout_provider": provider})
    assert timeout == 93.0
    assert "台架实测 t" in note
    assert set(seen) == {"simcon", "frames", "ctx"}


def test_resolve_timeout_provider_error_falls_back():
    def boom():
        raise RuntimeError("钩子坏了")

    timeout, note = meter.resolve_expect_timeout({"max_timeout_provider": boom})
    assert timeout == 60.0
    assert "钩子" in note


def test_resolve_timeout_without_simcon_falls_back_to_60():
    timeout, note = meter.resolve_expect_timeout({})
    assert timeout == 60.0
    assert "回落" in note and "60" in note


def test_resolve_timeout_query_disabled_sends_nothing():
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME)
    timeout, note = meter.resolve_expect_timeout(
        {"query_max_timeout": False}, None, simcon=simcon, frames=REAL_FRAMES)
    assert timeout == 60.0
    assert "已关闭" in note
    assert calls == [], "关掉查询就一帧都不该发"


def test_resolve_timeout_queries_03f7_then_adds_5s():
    """真查 03H-F7：发 AFN=03H、FN=7、无参、等 up 帧，拿到 t=90s → 95s。"""
    simcon, calls = make_simcon(timeout_frame=TIMEOUT_UP_FRAME)
    timeout, note = meter.resolve_expect_timeout(
        {}, None, simcon=simcon, frames=REAL_FRAMES)
    assert timeout == 95.0
    assert "t+5s" in note
    steps = timeout_steps(calls)
    assert len(steps) == 1
    assert steps[0]["send"] == {"afn": "03", "fn": 7, "params": {}}
    assert steps[0]["expect"] == {"format": "local", "afn": 3, "fn": 7, "dir": "up"}
    # 03H-F7 自身的等待按 10376.2 §7「直接交互 < 6s」
    assert steps[0]["expect_timeout"] == meter.TIMEOUT_QUERY_SECONDS


def test_resolve_timeout_unparsable_reply_falls_back():
    """模块回了帧但不是 03H-F7（错回 02H-F1）→ 解析不出 t → 60s，且说明里讲清原因。"""
    simcon, _ = make_simcon(timeout_frame=MEASURED_UP_FRAME)
    timeout, note = meter.resolve_expect_timeout(
        {}, None, simcon=simcon, frames=REAL_FRAMES)
    assert timeout == 60.0
    assert "最大超时时间" in note


def test_resolve_timeout_unsupported_frame_falls_back():
    """模块不支持 03H-F7（步骤 fail）→ 60s，说明里带上模块给的原因。"""
    simcon, _ = make_simcon(timeout_step_result="fail", timeout_reason="不支持该帧")
    timeout, note = meter.resolve_expect_timeout(
        {}, None, simcon=simcon, frames=REAL_FRAMES)
    assert timeout == 60.0
    assert "不支持该帧" in note


def test_addr_probe_step_wait_is_t_plus_5s():
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME,
                                timeout_frame=TIMEOUT_UP_FRAME)
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail
    assert forward_steps(calls)[0]["expect_timeout"] == 95.0


def test_addr_probe_step_wait_falls_back_to_60():
    """查不到 t（模块不支持）→ 下发步骤的 ``expect_timeout`` = 60s（不再是 8s）。"""
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME)
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail
    assert forward_steps(calls)[0]["expect_timeout"] == 60.0


def test_read_645_step_wait_is_t_plus_5s():
    simcon, calls = make_simcon(reply_frame=up_frame_with(MEASURED_645_REPLY),
                                timeout_frame=TIMEOUT_UP_FRAME)
    result = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=MEASURED_ADDR), ctx_with(simcon=simcon)
    )
    assert result.state == BLOCK_PASS, result.detail
    assert forward_steps(calls)[0]["expect_timeout"] == 95.0


def test_explicit_expect_timeout_still_wins_over_03f7():
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME,
                                timeout_frame=TIMEOUT_UP_FRAME)
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=9),
        ctx_with(simcon=simcon),
    )
    assert result.state == BLOCK_PASS, result.detail
    assert forward_steps(calls)[0]["expect_timeout"] == 9.0
    assert timeout_steps(calls) == [], "显式值优先 → 不必再查 03H-F7"


def test_run_level_params_can_lower_expect_timeout():
    """``--params``（``ctx.block_params``）能显式压低等待：真机复测把 95s 压到 3s。"""
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME,
                                timeout_frame=TIMEOUT_UP_FRAME)
    spec = declared_block("meter.addr_probe")
    specs = {spec.id: BlockSpec(id=spec.id, title=spec.title, needs=[],
                                provides=[], action=spec.action)}
    ctx = RunContext(run_id="run-fast", block_specs=specs, extras={"simcon": simcon})
    ctx.block_params["meter.addr_probe"] = {"meter_addr": MEASURED_ADDR,
                                            "expect_timeout": 3}
    result = execute_case(_meter_case(["meter.addr_probe"]), ctx)

    assert result.counts["pass"] == 1, result.to_dict()
    assert forward_steps(calls)[0]["expect_timeout"] == 3.0
    assert timeout_steps(calls) == [], "显式压低等待时不必再查 03H-F7"


def test_timeout_query_cached_once_per_run():
    """★ 同一次运行里 03H-F7 **只查一次**：第二条块复用缓存里的 t。"""
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME,
                                timeout_frame=TIMEOUT_UP_FRAME)
    ctx = ctx_with(simcon=simcon)
    for _ in range(2):
        result = get_block("meter.addr_probe")(
            action_of("meter.addr_probe", meter_addr=MEASURED_ADDR), ctx)
        assert result.state == BLOCK_PASS, result.detail
    assert len(timeout_steps(calls)) == 1, timeout_steps(calls)
    assert [s["expect_timeout"] for s in forward_steps(calls)] == [95.0, 95.0]
    assert isinstance(ctx.extras.get(meter.TIMEOUT_CACHE_KEY), dict)


def test_timeout_query_failure_also_cached():
    """查失败也进缓存：一个运行里不会每块都重试一次 03H-F7（避免拖慢 + 刷帧）。"""
    simcon, calls = make_simcon(reply_frame=MEASURED_UP_FRAME,
                                timeout_step_result="fail")
    ctx = ctx_with(simcon=simcon)
    for _ in range(2):
        get_block("meter.addr_probe")(
            action_of("meter.addr_probe", meter_addr=MEASURED_ADDR), ctx)
    assert len(timeout_steps(calls)) == 1
    assert [s["expect_timeout"] for s in forward_steps(calls)] == [60.0, 60.0]


def test_timeout_note_is_reported_on_non_reply_paths():
    """没收到上行帧时，detail 要带上「等待口径」，排障能一眼看出等了多久、为什么。"""
    simcon, _ = make_simcon(job_state="running", timeout_frame=TIMEOUT_UP_FRAME)
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, max_poll_seconds=0.05),
        ctx_with(simcon=simcon),
    )
    assert result.state == BLOCK_UNKNOWN, result.detail
    assert "等待口径" in result.detail and "95" in result.detail


# --------------------------------------------------------------------------- #
# 7. 等待型重试（REQS-0042 S2-F）：wait_s / poll_interval_s
#
# 真机口径：复位 STA 后链路恢复是十几分钟量级，而读取本身只要 2.3~2.6s —— 所以
# ``wait_s`` 缺省 **0**（只探一次，行为与改造前逐字一致），只有 ``meter_first_join``
# 这类含复位的场景才显式给值。本节的硬要求：
#
# - **零真实等待**：注入虚拟时钟（:class:`FakeClock`），``sleep`` 只把 ``now`` 推前；
# - **零真实网络**：假 simcon 按 ``plan`` 逐次应答（``None`` = 该次无应答）；
# - 三态分界：等满全程无帧 → unknown；等满有帧但判据不成立 → fail（不被重试掩盖）。
# --------------------------------------------------------------------------- #


class FakeClock:
    """虚拟时钟 + 睡眠：``sleep`` 只把 ``now`` 推前，**零真实等待**。"""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(float(seconds), 3))
        self.now += max(float(seconds), 0.0)


def make_wait_simcon(plan, *, submit_status=202, reason="超时(15s)未收到期望帧"):
    """按 ``plan`` **逐次**应答 02H-F1 透传的假 simcon。

    ``plan`` 每项 = 该次尝试命中的上行帧 hex，``None`` = 该次**一帧都没收到**
    （步骤判 fail 且 ``matched`` 为空，正是真机「表口静默」的形状）；超出长度复用最后一项。
    返回 ``(cap, calls, attempts)``：``attempts`` 按尝试顺序记下命中的帧（``None`` = 无应答）。
    """
    calls: list = []
    queue: list = list(plan) or [None]
    attempts: list = []
    pending: dict = {}

    def transport(method, url, body, timeout):
        calls.append((method, url, body))
        if url.endswith("/api/ai/v2/verification-runs"):
            if submit_status != 202:
                return submit_status, {"detail": "boom"}
            step = ((body or {}).get("task") or {}).get("steps", [{}])[0]
            afn = str((step.get("send") or {}).get("afn", "")).upper()
            pending["afn"] = afn
            if afn == "02":
                frame = queue[len(attempts)] if len(attempts) < len(queue) else queue[-1]
                attempts.append(frame)
            return 202, {"job_id": "job-%d" % len(calls), "job_state": "queued"}
        if "/api/ai/v2/jobs/" in url:
            if pending.get("afn") == "03":
                return 200, {
                    "job_id": "job-t", "job_state": "succeeded",
                    "result": {"result": {"steps": [
                        {"index": 0, "name": "查最大超时时间",
                         "matched": TIMEOUT_UP_FRAME, "result": "pass",
                         "reason": "ok"}], "run_id": "run-t"}},
                    "summary": "ok",
                }
            frame = attempts[-1] if attempts else None
            return 200, {
                "job_id": "job-f", "job_state": "succeeded",
                "result": {"result": {"steps": [
                    {"index": 0, "name": "step", "sent_hex": "680F0043",
                     "matched": frame or None,
                     "result": "pass" if frame else "fail",
                     "reason": "匹配成功" if frame else reason}],
                    "run_id": "run-f"}},
                "summary": reason if not frame else "ok",
            }
        if "/api/simcon/frames" in url:
            return 200, {"entries": [], "next_after_seq": 0}
        return 404, {"detail": "未 stub 的端点 %s" % url}

    cap = SimconCapability(base_url="http://fake-simcon", transport=transport)
    return cap, calls, attempts


def _waited_detail(result) -> None:
    """detail 里必须有人读得懂的等待留痕（core 也按这两条正则派生 decisions）。"""
    assert "等了" in result.detail and "只读探测" in result.detail, result.detail


def test_wait_defaults_declared_zero_and_five():
    """声明缺省：``wait_s=0``（等待型默认关闭）+ ``poll_interval_s=5``，title 说明清楚。"""
    for block_id in ("meter.addr_probe", "meter.read_645"):
        params = declared_block(block_id).action["params"]
        assert params["wait_s"] == 0, "等待型默认必须关闭（老用例行为不变）：%s" % block_id
        assert params["poll_interval_s"] == 5
        title = declared_block(block_id).title
        assert "wait_s=0" in title and "默认关闭" in title, title
        assert "meter_first_join" in title, "title 要点明只有含复位场景才开等待"


def test_addr_probe_wait_zero_keeps_legacy_single_attempt():
    """★ ``wait_s=0``（缺省）：只探一次、不 sleep、detail 与改造前**逐字一致**。"""
    legacy_simcon, legacy_calls = make_simcon(step_result="fail", reason="超时(15s)未收到期望帧")
    legacy = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=15),
        ctx_with(simcon=legacy_simcon),
    )

    fake = FakeClock()
    simcon, calls, attempts = make_wait_simcon([None, MEASURED_UP_FRAME])  # 第 2 次本会成功
    waited = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )

    assert waited.state == BLOCK_FAIL, waited.detail
    assert waited.detail == legacy.detail, "默认 wait_s=0 的输出必须与改造前逐字一致"
    assert len(forward_steps(calls)) == 1, "缺省只下发一次 02H-F1"
    assert attempts == [None]
    assert fake.sleeps == [], "缺省不得 sleep"
    assert "等了" not in waited.detail


def test_read_645_wait_zero_keeps_legacy_single_attempt():
    """``read_645`` 的 ``wait_s=0`` 同样只读一次（``meter_daily`` 靠的就是这个）。"""
    legacy_simcon, _ = make_simcon(step_result="fail", reason="超时(15s)未收到期望帧")
    legacy = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=MEASURED_ADDR, expect_timeout=15),
        ctx_with(simcon=legacy_simcon),
    )

    fake = FakeClock()
    simcon, calls, _ = make_wait_simcon([None, up_frame_with(MEASURED_645_REPLY)])
    waited = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )

    assert waited.state == BLOCK_FAIL, waited.detail
    assert waited.detail == legacy.detail
    assert len(forward_steps(calls)) == 1
    assert fake.sleeps == []


def test_addr_probe_wait_recovers_on_third_attempt():
    """★ 前 2 次无应答、第 3 次有 → pass；断言尝试 3 次、零真实等待、全只读透传帧。"""
    fake = FakeClock()
    simcon, calls, attempts = make_wait_simcon([None, None, MEASURED_UP_FRAME])
    started = time.monotonic()
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  wait_s=30, poll_interval_s=5,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )
    real_elapsed = time.monotonic() - started

    assert result.state == BLOCK_PASS, result.detail
    assert result.facts == {"sta.meter_addr_known": "yes"}
    assert attempts == [None, None, MEASURED_UP_FRAME], "第 3 次才应答"
    assert len(forward_steps(calls)) == 3
    _waited_detail(result)
    assert "等了 10 秒" in result.detail and "只读探测 3 次" in result.detail
    assert fake.sleeps == [5.0, 5.0], "只在两次重试之间等 5s（不额外空转）"
    assert real_elapsed < 1.0, "虚拟时钟必须零真实等待（实测 %.3fs）" % real_elapsed
    # 等待期只重发**同一个只读透传查询**（02H-F1 + 645 13H），没有任何写帧
    for step in forward_steps(calls):
        assert step["send"]["afn"] == "02" and step["send"]["fn"] == 1
        assert step["send"]["params"]["payload"] == ADDR_PROBE_REQUEST_METER
    assert {str(s["send"]["afn"]).upper() for s in _submitted_steps(calls)} == {"02"}


def test_addr_probe_wait_silent_until_budget_is_unknown_not_fail():
    """★ 等满仍**全程一帧都没有** → unknown（说不清链路是否恢复），detail 含「等了 N 秒 / M 次」。"""
    fake = FakeClock()
    simcon, calls, _ = make_wait_simcon([None])
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  wait_s=20, poll_interval_s=5,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )

    assert result.state == BLOCK_UNKNOWN, result.detail
    assert result.state != BLOCK_FAIL, "复位后表口静默**不是**产品失败"
    assert BLOCK_TO_OUTCOME[result.state] == "inconclusive"
    assert "等了 20 秒" in result.detail and "共 5 次尝试" in result.detail
    assert "只读探测 5 次" in result.detail
    assert "不判 FAIL" in result.detail
    assert len(forward_steps(calls)) == 5, "20s 预算 / 5s 间隔 = 5 次尝试"
    assert result.actual["探测次数"] == 5 and result.actual["等待秒"] == 20.0


def test_addr_probe_wait_framed_but_mismatched_stays_fail():
    """★ 等满但每次都有应答且判据不成立（地址不符）→ fail，**不被重试掩盖**。"""
    fake = FakeClock()
    simcon, calls, _ = make_wait_simcon([MEASURED_UP_FRAME])
    action = action_of("meter.addr_probe", expect_timeout=15, wait_s=10, poll_interval_s=5,
                       clock=fake.clock, sleep=fake.sleep)
    action["params"] = dict(action["params"], meter_addr="000000000002")
    result = get_block("meter.addr_probe")(action, ctx_with(simcon=simcon))

    assert result.state == BLOCK_FAIL, result.detail
    assert "期望地址 000000000002" in result.detail
    assert "实际 000000000001" in result.detail
    assert "只读探测 3 次" in result.detail, "重试过几次也要留痕"
    assert len(forward_steps(calls)) == 3
    assert not result.facts, "fail 的块不得回写 provides 事实"


def test_addr_probe_wait_skipped_dependency_aborts_immediately():
    """依赖不可用（404 无会话）→ skipped，且**不空转重试**（省掉几十次无用下发）。"""
    fake = FakeClock()
    simcon, _calls, attempts = make_wait_simcon([MEASURED_UP_FRAME], submit_status=404)
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  wait_s=60, poll_interval_s=5,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )

    assert result.state == BLOCK_SKIPPED, result.detail
    assert "404" in result.detail
    assert fake.sleeps == [], "依赖不可用不该继续等"
    assert attempts == [], "根本没提交透传步骤"


#: 645 写数据（控制码 14H）帧：CS 合法但**不是只读帧** —— 等待型必须拒绝重发
WRITE_645_REQUEST = "68123456789012681404333334336B16"


def test_wait_gate_rejects_write_payload():
    """等待期只读闸门：写帧 payload + ``wait_s>0`` → 直接拒绝轮询（一帧都不发）。"""
    fake = FakeClock()
    simcon, calls, _ = make_wait_simcon([MEASURED_UP_FRAME])

    probe = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  wait_s=30, poll_interval_s=5, payload=WRITE_645_REQUEST,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )
    read = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  wait_s=30, poll_interval_s=5, payload=WRITE_645_REQUEST,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )

    for result in (probe, read):
        assert result.state == BLOCK_UNKNOWN, result.detail
        assert "只读闸门" in result.detail and "14H" in result.detail
    assert calls == [], "闸门在下发之前拦下：一帧都不许发出去"
    assert fake.sleeps == []

    # 同一个写帧 payload 在 wait_s=0（不重试）下不会被拦：老行为不受影响
    legacy = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  payload=WRITE_645_REQUEST),
        ctx_with(simcon=simcon),
    )
    assert legacy.state == BLOCK_PASS, legacy.detail


def test_read_645_wait_recovers_after_two_silent_attempts():
    """★ ``read_645`` 等待到成功：前 2 次无应答、第 3 次读到 → pass，尝试 3 次零真实等待。"""
    fake = FakeClock()
    reply_up = up_frame_with(MEASURED_645_REPLY)
    simcon, calls, attempts = make_wait_simcon([None, None, reply_up])
    started = time.monotonic()
    result = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  wait_s=30, poll_interval_s=5,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )
    real_elapsed = time.monotonic() - started

    assert result.state == BLOCK_PASS, result.detail
    assert "1234.56" in result.detail
    assert attempts == [None, None, reply_up]
    assert len(forward_steps(calls)) == 3
    _waited_detail(result)
    assert "等了 10 秒" in result.detail and "只读探测 3 次" in result.detail
    # 结构化读数（执行器 poll_reading 优先读它）与原有 actual 并存，不覆盖数值
    assert result.actual["探测次数"] == 3 and result.actual["等待秒"] == 10.0
    assert result.actual["value"] == 1234.56
    assert fake.sleeps == [5.0, 5.0]
    assert real_elapsed < 1.0
    for step in forward_steps(calls):
        assert step["send"]["params"]["payload"] == "6800000000000168110433333433B316"


def test_read_645_wait_silent_until_budget_is_unknown_not_fail():
    fake = FakeClock()
    simcon, calls, _ = make_wait_simcon([None])
    result = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=MEASURED_ADDR, expect_timeout=15,
                  wait_s=20, poll_interval_s=5,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )

    assert result.state == BLOCK_UNKNOWN, result.detail
    assert "等了 20 秒" in result.detail and "共 5 次尝试" in result.detail
    assert "只读探测 5 次" in result.detail
    assert "不判 FAIL" in result.detail
    assert len(forward_steps(calls)) == 5
    assert result.actual["探测次数"] == 5


def test_read_645_wait_framed_judge_fail_not_masked():
    """★ 有应答但 DI 不回显（判据不成立）→ fail，不因「重试」变成 unknown。"""
    fake = FakeClock()
    simcon, calls, _ = make_wait_simcon([up_frame_with(MEASURED_645_REPLY)])
    result = get_block("meter.read_645")(
        action_of("meter.read_645", meter_addr=MEASURED_ADDR, di="02010100",
                  expect_timeout=15, wait_s=10, poll_interval_s=5,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )

    assert result.state == BLOCK_FAIL, result.detail
    assert "未回显 DI" in result.detail
    assert "只读探测 3 次" in result.detail
    assert len(forward_steps(calls)) == 3


def test_read_645_wait_caps_attempt_timeout_by_remaining_budget():
    """★ 单次尝试上限 = min(expect_timeout, 剩余预算)：``expect_timeout`` 与 ``wait_s`` 不叠乘。

    预算 12s / 间隔 5s / ``expect_timeout`` 60s：第 1 次最多等 12s，之后每次被压缩到
    「剩余预算」，所以总时长由 ``wait_s`` 兜住，而不是 ``attempts × 60s``。
    """
    fake = FakeClock()
    simcon, calls, _ = make_wait_simcon([None])
    result = get_block("meter.addr_probe")(
        action_of("meter.addr_probe", meter_addr=MEASURED_ADDR, expect_timeout=60,
                  wait_s=12, poll_interval_s=5,
                  clock=fake.clock, sleep=fake.sleep),
        ctx_with(simcon=simcon),
    )

    timeouts = [step["expect_timeout"] for step in forward_steps(calls)]
    assert result.state == BLOCK_UNKNOWN, result.detail
    # 单次上限 = min(60, 剩余预算)：12 → 7 → 2 → 触底 1s（MIN_ATTEMPT_TIMEOUT_S）
    assert timeouts == [12.0, 7.0, 2.0, 1.0], timeouts
    assert "单次最多等" in result.detail
    assert BLOCK_TO_OUTCOME[result.state] == "inconclusive"


def test_executor_derives_wait_decision_from_meter_wait_detail():
    """★ 留痕闭环：块不改 core，执行器就能把「等了 N 秒 / 探了 K 次」派生成 decisions。"""
    fake = FakeClock()
    simcon, calls, _ = make_wait_simcon([None, None, MEASURED_UP_FRAME])
    spec = declared_block("meter.addr_probe")
    specs = {spec.id: BlockSpec(id=spec.id, title=spec.title, needs=[],
                                provides=[], action=spec.action)}
    ctx = RunContext(run_id="run-wait", block_specs=specs, extras={"simcon": simcon})
    ctx.block_params["meter.addr_probe"] = {
        "meter_addr": MEASURED_ADDR, "expect_timeout": 15,
        "wait_s": 30, "poll_interval_s": 5,
        "clock": fake.clock, "sleep": fake.sleep,
    }
    result = execute_case(_meter_case(["meter.addr_probe"]), ctx)

    assert result.counts["pass"] == 1, result.to_dict()
    decisions = result.resolved_decisions()
    waits = [item for item in decisions if item["kind"] == "wait"]
    assert waits, decisions
    assert waits[0]["attempts"] == 3 and waits[0]["waited_s"] == 10.0
    assert all(item["kind"] != "timeout" for item in decisions), "pass 不是超时收尾"


def test_executor_marks_timeout_decision_when_wait_budget_exhausted():
    """等满预算仍未通过 → 除 ``wait`` 还派生一条 ``timeout``（D4-4 超时收尾）。"""
    fake = FakeClock()
    simcon, _calls, _ = make_wait_simcon([None])
    spec = declared_block("meter.read_645")
    specs = {spec.id: BlockSpec(id=spec.id, title=spec.title, needs=[],
                                provides=[], action=spec.action)}
    ctx = RunContext(run_id="run-timeout", block_specs=specs, extras={"simcon": simcon})
    ctx.block_params["meter.read_645"] = {
        "meter_addr": MEASURED_ADDR, "expect_timeout": 15,
        "wait_s": 10, "poll_interval_s": 5,
        "clock": fake.clock, "sleep": fake.sleep,
    }
    result = execute_case(_meter_case(["meter.read_645"]), ctx)

    assert result.counts["unknown"] == 1, result.to_dict()
    kinds = [item["kind"] for item in result.resolved_decisions()]
    assert "wait" in kinds and "timeout" in kinds, kinds


# --------------------------------------------------------------------------- #
# 8. 集成路径（有工作台就跑真路径，没有就 skip —— 绝不因台架缺失变红）
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
