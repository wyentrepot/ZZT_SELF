"""档案块 / 组网块 / 组网事实的单测（REQS-0041 §10.5 BL-2、BL-3、FT-1~FT-3）。

覆盖口径
--------
1. **``archive.add`` 双判据防假 PASS**：确认帧到了但 10H-F2 查不到 → 必须 FAIL。
2. ``nwk.active_registration`` 四参缺失 → 下发前 FAIL，detail 点名缺哪个参数。
3. 能力层已知坑：409 → 不自动重试、不判 pass；``frames()`` 404 → skipped；
   ``poll_timeout`` / 构帧失败 → unknown（不是设备 FAIL）。
4. 事实三态：``unknown`` 永不折算 ``yes``。
5. **10H-F1 只是档案数**（``nwk.archive_count``）：台架实测 12 条档案 / 已抄=0 /
   信号品质全 0 时**不得**判「组网通」；真实可达由 ``nwk.nodes_online``
   用 10H-F4 已抄数 + 10H-F2 逐节点信号品质两路交叉判（两路都为 0 → FAIL）。
6. ``nwk.route_status`` 学习态分层：路由已完成（运行状态字 D1=1）+ 档案>0 时
   工作状态=学习 → inconclusive（块态 unknown），**不是 fail**。

全部脱离台架：用脚本化替身（:class:`FakeSimcon`）+ **真实构帧/真实解析**
（``parser_lib`` 构帧 → ``frame_codec.decode_frame``）拼出与真机同构的步骤结果，
因此本文件的字段名断言同时守住了「块层字段口径 == 解析器字段口径」。

每个测试前后 ``clear_registries()`` + 重新注册，互不污染。
"""

from __future__ import annotations

import ast
import copy
import json
import os
import pathlib

import pytest

from case_runner.blocks import archive, common, facts_nwk, nwk
from case_runner.core import defaults
from case_runner.core.executor import BLOCK_TO_OUTCOME, RunContext, execute_case
from case_runner.core.models import BlockSpec, CaseSpec, CaseStep, Health
from case_runner.core.planner import FactValue, FactsContext
from case_runner.core.registry import (
    Duplicate,
    clear_registries,
    get_block,
    get_fact,
    has_block,
    has_fact,
    list_blocks,
    register_block,
    unregister_block,
)

# 真实构帧/解析（测试里允许 import 领域库；分层铁律只约束 libs 生产代码）
from parser_lib.adapters.adapter_10376 import _bcd_bytes, build_frame
from sim_concentrator.frame_codec import decode_frame

#: **档案侧**人读地址（10H-F2 档案表里展示的那一串；被 1376.2 编码器整体反转）
ADDR = "000000000001"
ADDR2 = "000000000002"
#: **电表侧** 645 人读地址（调用方只传这个；块内部用 ``common.archive_addr_of``
#: 换算成上面那串档案地址 = 6 字节整体反转，台架实测 123456789012 ↔ 129078563412）
METER_ADDR = "010000000000"       # = archive_addr_of("000000000001")
METER_ADDR2 = "020000000000"      # = archive_addr_of("000000000002")
SENT_HEX = "680F00430000000000011201005716"          # 12H-F1 实测下行帧（仅作证据字段）
DATA_DIR = pathlib.Path(archive.__file__).resolve().parents[1] / "data"

#: 真实台架抓到的 10H-F4 上行帧（领导实测样本，2026-11-23）
#: 应用数据 16B：``03 | 0c00 | 0000 | 0c00 | 06 | 6480 | 000000 | 080808``
#: = 运行状态字 0x03（路由完成=1）/ 档案数 12 / **已抄 0** / 中继抄到 12 /
#:   工作开关 0x06（当前状态=抄表 工作状态=学习）/ 速率 100kbps
REAL_ROUTE_F4_HEX = "681f0083000040000001100800030c0000000c00066480000000080808f916"
#: 真实台架 10H-F2 逐节点信息：12 个节点、信号品质全 0（中继级别 15、相线 0、协议类型 1）
REAL_NODE_COUNT = 12


# --------------------------------------------------------------------------- #
# 真实帧构造 + 步骤结果拼装
# --------------------------------------------------------------------------- #


def _le16(value: int) -> bytes:
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def raw_confirm(cmd_state: int = 0x80) -> bytes:
    """00H-F1 确认帧（命令状态 4B + 等待时间 2B）。"""
    return build_frame(direction="up", afn=0x00, fn=1,
                       appdata=bytes([cmd_state, 0, 0, 0]) + _le16(0))


def raw_deny(error: int = 7) -> bytes:
    """00H-F2 否认帧（错误状态字 1B，7=表号不存在）。"""
    return build_frame(direction="up", afn=0x00, fn=2, appdata=bytes([error]))


def raw_archive_table(total: int, addrs) -> bytes:
    """10H-F2 上行：从节点总数量(2B) + 本次应答数量(1B) + N×(6B 地址 + 2B 信息)。"""
    payload = bytearray(_le16(total)) + bytes([len(addrs)])
    for addr in addrs:
        payload += _bcd_bytes(addr) + _le16(0x12)
    return build_frame(direction="up", afn=0x10, fn=2, appdata=bytes(payload))


def raw_onnet(total: int, maximum: int = 100) -> bytes:
    """10H-F1 上行：从节点总数量(2B) + 路由支持最大从节点数量(2B)。"""
    return build_frame(direction="up", afn=0x10, fn=1,
                       appdata=_le16(total) + _le16(maximum))


def raw_route(
    switch: int,
    *,
    route_word: int = 0x00,
    total: int = 3,
    answered: int = 2,
    relay: int = 1,
) -> bytes:
    """10H-F4 上行：运行状态字 + 三个数量 + 工作开关 + 速率 + 三相中继级别/工作步骤。

    ``route_word`` 是**运行状态字**（D1 = 路由完成标志，1=路由学习完成/0=未完成；
    出处 ``adapter_10376/metadata/afn_fn.json`` 10H-F4 ``resp.fields[0].d``）；
    ``switch`` 是**工作开关**（D1 = 工作状态 1学习/0抄表、D7~D6 = 当前状态）。

    **样本必须正好 16 字节**（1+2+2+2+1+2+3+3 = 16）：解析门限是 ``len(data) >= 16``
    （CCO 固件 ``gw13762.c:4612-4687`` 逐字节写到 ``buff[15]`` 后 ``bufflen = 16``）。
    多塞 1 字节（曾经这里多一个 ``b"\\x00"``，凑成 17B）会让样本只在旧门限 ``>= 17``
    下通过，从而**掩盖真机 16B 应答被判「无应用数据」的缺陷**；少 1 字节则解析器根本
    不出字段。改这个样本前先确认协议长度。
    """
    payload = (
        bytes([route_word]) + _le16(total) + _le16(answered) + _le16(relay)
        + bytes([switch]) + _le16(0x0002) + bytes([1, 1, 1]) + bytes([0, 0, 0])
    )
    assert len(payload) == 16, "10H-F4 上行样本长度必须是 16B，实际 %d" % len(payload)
    return build_frame(direction="up", afn=0x10, fn=4, appdata=payload)


def raw_archive_nodes(total: int, signals, *, relay_level: int = 15) -> bytes:
    """10H-F2 上行逐节点：``总数量(2B) + 本帧条数(1B) + N×(6B 地址 + 2B 信息)``。

    ``signals`` 是每个节点的**侦听信号品质**（D7~D4，0=无信号）；低 4 位是
    ``relay_level``（真实台架样本里为 15）。地址按序号生成（``000000000001``…）。
    与真实台架 ``10H-F2`` 样本同构（12 节点、信号品质全 0）。
    """
    qualities = [int(value) for value in signals]
    payload = bytearray(_le16(total)) + bytes([len(qualities)])
    for index, quality in enumerate(qualities):
        addr = "%012d" % (index + 1)
        info = ((quality & 0x0F) << 4) | (relay_level & 0x0F)
        payload += _bcd_bytes(addr) + _le16(info)
    return build_frame(direction="up", afn=0x10, fn=2, appdata=bytes(payload))


def step_pass(raw: bytes, *, name: str = "step") -> dict:
    """步骤通过（带真实解析结果，形状同 ``runner.run_step`` 的返回值）。"""
    return {
        "name": name,
        "result": "pass",
        "reason": "期望帧匹配",
        "sent_hex": SENT_HEX,
        "matched": raw.hex().upper(),
        "parsed": decode_frame(raw),
    }


def step_pass_without_parsed(raw: bytes, *, name: str = "step") -> dict:
    """步骤通过但**没有** parsed（模拟服务端裁掉解析结果，只能翻帧日志）。"""
    step = step_pass(raw, name=name)
    step.pop("parsed")
    return step


def step_timeout(*, name: str = "step", reason: str = "等待帧超时(5.0s)") -> dict:
    """激励已发出但无应答（``sent_hex`` 非空 = 帧确实发了）。"""
    return {
        "name": name, "result": "fail", "reason": reason,
        "sent_hex": SENT_HEX, "matched": None, "parsed": None,
    }


def step_config_error(*, name: str = "step") -> dict:
    """服务端构帧参数错（不是设备 FAIL）。"""
    return {
        "name": name, "result": "fail", "reason": "构帧失败: ValueError('缺少参数 duration')",
        "sent_hex": "", "matched": None, "parsed": None,
    }


def job_ok(*steps, job_id: str = "job-1", run_id: str = "run-1") -> dict:
    return {
        "ok": True, "job_id": job_id, "job_state": "succeeded",
        "steps": list(steps), "run_id": run_id, "steps_failed": 0,
        "verdict": "pass", "summary": "ok",
    }


def job_error(error: str, detail: str = "") -> dict:
    return {"ok": False, "error": error, "detail": detail, "steps": []}


class FakeSimcon:
    """脚本化模拟集中器能力：按序返回预设的 verify 结果。"""

    name = "simcon"

    def __init__(self, *results, frames_result=None, health=None):
        self.results = list(results)
        self.calls: list[list[dict]] = []
        self.frames_calls: list[dict] = []
        self.frames_result = frames_result
        self.health = health if health is not None else Health(ok=True, reason="fake simcon")
        self._job_seq = 0

    # -- 能力契约 ---------------------------------------------------------- #
    def probe(self):
        return self.health

    def verify(self, steps):
        self.calls.append([copy.deepcopy(step) for step in steps])
        if not self.results:
            raise AssertionError("FakeSimcon: verify 调用次数超出脚本")
        self._job_seq += 1
        item = self.results.pop(0)
        return copy.deepcopy(item) if isinstance(item, dict) else item

    def frames(self, **kwargs):
        self.frames_calls.append(dict(kwargs))
        if self.frames_result is None:
            raise AssertionError("FakeSimcon: 本用例不该查会话帧日志")
        return copy.deepcopy(self.frames_result)


def ctx_with(cap) -> RunContext:
    return RunContext(extras={"simcon": cap})


def action_of(afn, fn, **params) -> dict:
    """块声明形状：``{afn, fn, params}``（执行器把这个 dict 传给块执行体）。"""
    return {"afn": afn, "fn": fn, "params": params}


@pytest.fixture(autouse=True)
def _isolated_registries():
    """每个测试独立注册表（REQS 契约：重复注册抛 Duplicate，测试须 clear）。"""
    clear_registries()
    archive.register_blocks()
    nwk.register_blocks()
    facts_nwk.register_facts()
    yield
    clear_registries()


# --------------------------------------------------------------------------- #
# A. 注册与声明
# --------------------------------------------------------------------------- #


def test_my_blocks_and_facts_are_registered():
    for block_id in (
        "archive.add", "archive.del", "archive.query",
        "nwk.route_restart", "nwk.active_registration",
        "nwk.archive_count",      # 正名后的「档案数」块
        "nwk.onnet_count",        # 兼容别名（内部委托 archive_count）
        "nwk.nodes_online",       # 真实可达（在网）块
        "nwk.route_status",
    ):
        assert has_block(block_id), block_id
        assert get_block(block_id) is not None
    for fact_id in (
        "archive.contains",
        "nwk.onnet_count_min",    # 兼容别名事实（档案数）
        "nwk.nodes_online_min",   # 真在网事实
        "nwk.route_running",
    ):
        assert has_fact(fact_id), fact_id
        assert get_fact(fact_id) is not None


def test_register_is_idempotent_but_foreign_executor_raises_duplicate():
    # 幂等：重复调用自己的 register() 不抛
    archive.register()
    nwk.register()
    facts_nwk.register()
    assert list_blocks().count("archive.add") == 1

    # 同一 id 被别人的执行体占住 → 不静默覆盖
    def other(params, ctx=None):
        return "pass"

    unregister_block("archive.add")     # 让位给"别人的"执行体
    register_block("archive.add", other)
    with pytest.raises(Duplicate):
        archive.register_blocks()
    unregister_block("archive.add")
    archive.register_blocks()          # 复原
    assert get_block("archive.add") is archive.block_add


def test_block_declarations_match_core_models():
    expected = {
        "archive_add.json": ("archive.add", "11", 1),
        "archive_del.json": ("archive.del", "11", 2),
        "archive_query.json": ("archive.query", "10", 2),
        "nwk_route_restart.json": ("nwk.route_restart", "12", 1),
        "nwk_active_registration.json": ("nwk.active_registration", "11", 5),
        "nwk_archive_count.json": ("nwk.archive_count", "10", 1),
        "nwk_onnet_count.json": ("nwk.onnet_count", "10", 1),   # 兼容别名声明
        "nwk_nodes_online.json": ("nwk.nodes_online", "10", 4),
        "nwk_route_status.json": ("nwk.route_status", "10", 4),
    }
    from case_runner.core.models import BlockSpec

    for filename, (block_id, afn, fn) in expected.items():
        path = DATA_DIR / "blocks" / filename
        raw = json.loads(path.read_text(encoding="utf-8"))
        spec = BlockSpec.model_validate(raw)
        assert spec.id == block_id
        assert spec.action["afn"] == afn and spec.action["fn"] == fn
        assert spec.title and spec.tags
        assert "bench.simcon_online" in spec.needs


def test_fact_declarations_match_core_models():
    from case_runner.core.models import FactSpec

    expected = {
        "archive_contains.json": "archive.contains",
        "nwk_onnet_count_min.json": "nwk.onnet_count_min",
        "nwk_nodes_online_min.json": "nwk.nodes_online_min",
        "nwk_route_running.json": "nwk.route_running",
    }
    for filename, fact_id in expected.items():
        path = DATA_DIR / "facts" / filename
        spec = FactSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
        assert spec.id == fact_id and spec.probe == fact_id
        assert has_fact(spec.probe)


# --------------------------------------------------------------------------- #
# B. archive.add 双判据（防假 PASS 核心）
# --------------------------------------------------------------------------- #


def test_archive_add_confirm_received_but_not_landed_is_fail():
    """★ 核心：00H-F1 确认到了，但 10H-F2 查不到该地址 → 必须 FAIL。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),             # 幂等预查：空表（已查全）
        job_ok(step_pass(raw_confirm())),                        # 判据 1：确认
        job_ok(step_pass(raw_archive_table(1, [ADDR2]))),        # 判据 2：表里只有 ADDR2
    )
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(fake))

    assert result.state == "fail", result.detail
    assert "确认已收到但档案未落地" in result.detail
    assert ADDR in result.detail
    assert result.facts == {}          # 失败块绝不回写 provides 事实
    assert len(fake.calls) == 3        # 三次独立提交：幂等预查 / 建档 / 回查


def test_archive_add_double_criterion_passes_and_reports_both():
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, [])), job_id="job-pre"),
        job_ok(step_pass(raw_confirm()), job_id="job-add"),
        job_ok(step_pass(raw_archive_table(2, [ADDR, ADDR2])), job_id="job-query"),
    )
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=3),
                               ctx_with(fake))

    assert result.state == "pass", result.detail
    assert "双判据成立" in result.detail
    assert result.facts["archive.contains"]["state"] == "yes"
    assert result.evidence_ids == ["job-pre", "job-add", "job-query"]
    # 第 0 次是幂等预查（只读 10H-F2），第 1 次才是建档
    assert fake.calls[0][0]["send"]["afn"] == 0x10 and fake.calls[0][0]["send"]["fn"] == 2
    # 下发的建档帧参数就是块参数（语义化构帧，不写死 hex）
    sent = fake.calls[1][0]["send"]
    assert sent["afn"] == 0x11 and sent["fn"] == 1
    assert sent["params"] == {"action": "add", "addr": ADDR, "protocol": 3}
    assert fake.calls[1][0]["expect"]["afn"] == 0x00


def test_archive_add_declaration_does_not_provide_parameterized_fact():
    """★ ``archive.add`` 不得 ``provides: ["archive.contains"]``：该事实的参数是 addr，
    块回写只按 fact_id 缓存 → 给 A 建档成功会让 B 的探测直接命中 yes = 跨地址假 PASS。
    块内部已做「确认 + 10H-F2 回查」双判据，provides 本属冗余。"""
    raw = json.loads(
        (DATA_DIR / "blocks" / "archive_add.json").read_text(encoding="utf-8"))
    assert raw["provides"] == [], raw["provides"]
    assert raw["needs"] == ["bench.simcon_online"]     # needs 是台架事实，参数无区分性


def test_archive_add_does_not_poison_fact_cache_for_other_addresses():
    """真执行器跑真声明（``data/blocks/archive_add.json``）→ 事实缓存里**不得**出现
    ``archive.contains``；否则下一次换地址探测会命中缓存 = 假 PASS。"""
    from case_runner.blocks import loader

    raw = json.loads(
        (DATA_DIR / "blocks" / "archive_add.json").read_text(encoding="utf-8"))
    # 声明里 ``meter_addr: null``（= 取配置默认值）；本用例关注「事实缓存不被污染」，
    # 所以显式钉一只表，免得断言跟着现场 case_defaults.json 漂。
    raw["action"]["params"]["meter_addr"] = METER_ADDR
    raw["assert"] = [dict(item, meter_addr=METER_ADDR) if "meter_addr" in item else item
                     for item in raw["assert"]]
    spec = BlockSpec.model_validate(raw)
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),             # 幂等预查
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),
    )
    case = CaseSpec(
        id="case.add", title="加档案", blocks=["archive.add"],
        steps=[CaseStep(no=1, do="下发 11H-F1 建档", expect="档案落地可回查")],
    )
    facts = FactsContext(probe=lambda fact_id, params: FactValue(state="yes", detail="fake"))
    ctx = RunContext(facts_ctx=facts, block_specs={"archive.add": spec},
                     extras={"simcon": fake})
    outcome = execute_case(case, ctx)

    assert outcome.verdict == "pass", [b.to_dict() for b in outcome.blocks]
    assert ctx.facts_ctx.peek(archive.FACT_CONTAINS) is None, (
        "archive.contains 是参数化事实（addr），块 pass 也不得回写缓存")
    # 声明本身也必须是无害的：loader 结构校验不许报 provides 参数化事实
    report = loader.load_all(loader.DATA_DIR, strict=False)
    assert not [p for p in report.problems if "参数化事实" in p], report.problems


def test_archive_add_confirm_timeout_is_fail_when_frame_was_actually_sent():
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),             # 幂等预查：不存在
        job_ok(step_timeout()),
    )
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(fake))
    assert result.state == "fail"
    assert "已发出" in result.detail and SENT_HEX in result.detail


def test_archive_add_deny_frame_is_fail_with_error_word():
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),             # 幂等预查：不存在
        job_ok(step_pass(raw_deny(7))),
    )
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(fake))
    assert result.state == "fail"
    assert "否认帧 00H-F2" in result.detail
    assert "表号不存在" in result.detail          # 错误状态字已解出人话


def test_archive_add_missing_addr_is_fail_before_any_frame(monkeypatch):
    """入参、声明、配置默认**三处都拿不到**表地址 → 下发前一帧不发，报缺参数。

    （D2-2 之后「不传参」通常会被 ``case_defaults.meter_addr`` 兜住，所以这里显式把
    默认值打成空，模拟现场配置缺失——那才是真正「缺必填参数」的局面。）
    """
    monkeypatch.setattr(archive, "default_meter_addr", lambda: "")
    fake = FakeSimcon()
    result = archive.block_add(action_of("11", 1, action="add", protocol=2), ctx_with(fake))
    assert result.state == "fail"
    assert "缺少必填参数 meter_addr" in result.detail
    assert fake.calls == []                        # 参数不全 → 一帧都不发


def test_archive_add_bad_protocol_is_fail():
    fake = FakeSimcon()
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=5),
                               ctx_with(fake))
    assert result.state == "fail" and "protocol" in result.detail
    assert fake.calls == []


def test_archive_add_declared_afn_mismatch_is_fail():
    """声明写成 12H-F1 却要跑 archive.add → 明确报错（AFN 记法坑）。"""
    fake = FakeSimcon()
    result = archive.block_add(action_of("12", 1, meter_addr=METER_ADDR), ctx_with(fake))
    assert result.state == "fail"
    assert "只支持 AFN=0x11" in result.detail
    assert fake.calls == []


def test_archive_add_truncated_window_is_unknown_not_fail():
    """只查到一页且 total 更大 → 不能断定「不存在」，必须 unknown。"""
    fake = FakeSimcon(
        # 幂等预查必须**查全**（total=1 且已查到 1 条）才敢说「不存在」；
        # 真正要验的是**下发后**那次回查被窗口截断 → unknown
        job_ok(step_pass(raw_archive_table(1, [ADDR2]))),
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(40, [ADDR2]))),
    )
    result = archive.block_add(
        action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2, count=1, max_pages=1),
        ctx_with(fake),
    )
    assert result.state == "unknown", result.detail
    assert "未覆盖全部档案" in result.detail


def test_archive_add_query_answer_without_total_is_unknown_not_fail():
    """10H-F2 应答解析不出「从节点总数量」→ 不能当空表判 FAIL。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR2]))),        # 幂等预查：已查全、不含 ADDR
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(build_frame(direction="up", afn=0x10, fn=2, appdata=b""))),
    )
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert "从节点总数量" in result.detail


# --------------------------------------------------------------------------- #
# C. archive.del / archive.query
# --------------------------------------------------------------------------- #


def test_archive_del_passes_when_address_gone():
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),         # 幂等预查：目标还在
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(0, []))),
    )
    result = archive.block_del(action_of("11", 2, meter_addrs=[METER_ADDR]), ctx_with(fake))
    assert result.state == "pass", result.detail
    assert "双判据成立" in result.detail


def test_archive_del_fails_when_address_still_present():
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),         # 幂等预查：目标还在
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),
    )
    result = archive.block_del(action_of("11", 2, meter_addr=METER_ADDR), ctx_with(fake))
    assert result.state == "fail"
    assert "档案仍在" in result.detail


def test_archive_del_missing_meters_is_fail():
    """★ 破坏性动作不吃默认地址：不传参 → 下发前 FAIL，一帧不发。

    ``archive.del`` 与 ``archive.query`` 同口径（``allow_config_default=False``）：
    ``case_defaults.meter_addr`` 是「现场被测那只表」，拿它当删档默认 = 不传参就删被测表档案。
    这里**不** monkeypatch 默认值——默认值存在也照样拒，才是这条规则的真验证。
    """
    assert defaults.default_meter_addr(), "出厂默认地址应存在（否则本用例没验证到拒默认）"
    fake = FakeSimcon()
    result = archive.block_del(action_of("11", 2), ctx_with(fake))
    assert result.state == "fail"
    assert "meter_addrs" in result.detail and "meter_addr" in result.detail
    assert "破坏性动作" in result.detail and "不接受配置默认地址" in result.detail
    assert fake.calls == []          # 零帧：verify 一次都没调（也就不可能删档）


def test_archive_del_ignores_declaration_assert_addr_too():
    """★ assert 侧也不能当后门：``loader`` 会把声明 ``assert[].meter_addr: null`` 解析成
    配置默认值，若 del 取 assert 兜底，默认表就会被「不传参」删掉。"""
    assert defaults.default_meter_addr()
    fake = FakeSimcon()
    params = action_of("11", 2)                      # action 侧没给地址
    params["_assert"] = [{"type": "archive_not_contains",
                          "meter_addr": defaults.default_meter_addr()}]
    result = archive.block_del(params, ctx_with(fake))
    assert result.state == "fail", result.detail
    assert "破坏性动作" in result.detail
    assert fake.calls == []


def test_archive_del_explicit_addr_runs_normal_flow():
    """显式给地址 → 正常删档流程不变：一帧 11H-F2（档案地址）+ 一帧 10H-F2 回查。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR])), job_id="job-pre"),
        job_ok(step_pass(raw_confirm()), job_id="job-del"),
        job_ok(step_pass(raw_archive_table(0, [])), job_id="job-query"),
    )
    result = archive.block_del(
        action_of("11", 2, meter_addrs=[METER_ADDR]), ctx_with(fake))
    assert result.state == "pass", result.detail
    assert fake.calls[0][0]["send"]["afn"] == 0x10    # 幂等预查（只读）
    assert fake.calls[1][0]["send"]["params"] == {"meters": [ADDR]}
    assert len(fake.calls) == 3                       # 预查 + 删档 + 回查，三次独立提交
    assert result.evidence_ids == ["job-pre", "job-del", "job-query"]


def test_archive_query_contains_addr_pass_and_absent_fail():
    fake = FakeSimcon(job_ok(step_pass(raw_archive_table(2, [ADDR, ADDR2]))))
    ok = archive.block_query(
        action_of("10", 2, start=0, count=16, meter_addr=METER_ADDR), ctx_with(fake))
    assert ok.state == "pass", ok.detail

    fake2 = FakeSimcon(job_ok(step_pass(raw_archive_table(1, [ADDR2]))))
    bad = archive.block_query(
        action_of("10", 2, start=0, count=16, meter_addr=METER_ADDR), ctx_with(fake2))
    assert bad.state == "fail" and "不含" in bad.detail


def test_archive_query_expect_total_and_expect_empty():
    fake = FakeSimcon(job_ok(step_pass(raw_archive_table(3, [ADDR, ADDR2]))))
    assert archive.block_query(action_of("10", 2, expect_total=3), ctx_with(fake)).state == "pass"

    fake_bad = FakeSimcon(job_ok(step_pass(raw_archive_table(3, [ADDR]))))
    bad = archive.block_query(action_of("10", 2, expect_total=0), ctx_with(fake_bad))
    assert bad.state == "fail" and "总数不符" in bad.detail

    fake_empty = FakeSimcon(job_ok(step_pass(raw_archive_table(0, []))))
    empty = archive.block_query(action_of("10", 2, expect_empty=True), ctx_with(fake_empty))
    assert empty.state == "pass" and "档案表为空" in empty.detail

    fake_not_empty = FakeSimcon(job_ok(step_pass(raw_archive_table(1, [ADDR]))))
    not_empty = archive.block_query(action_of("10", 2, expect_empty=True), ctx_with(fake_not_empty))
    assert not_empty.state == "fail" and "期望空表" in not_empty.detail


def test_archive_query_without_any_criterion_is_unknown_not_pass():
    """★ 一条判据都没给（无 addr / expect_total / expect_empty）→ 什么都不该断言，
    只能 unknown；判 pass 就是「什么都没验证的假 PASS」。"""
    fake = FakeSimcon(job_ok(step_pass(raw_archive_table(2, [ADDR]))))
    result = archive.block_query(action_of("10", 2, start=0, count=16), ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert result.state != "pass"
    assert "未指定 meter_addr/expect_total/expect_empty" in result.detail
    assert "无法断言档案内容" in result.detail
    # 查询应答仍然可解析（detail 里要给出看到的事实，便于人工复核）
    assert "total=2" in result.detail
    assert fake.calls, "查询帧照常下发，不下发就无从说「看到了档案表」"


def test_archive_query_any_single_criterion_switches_back_to_judging():
    """三选一任一给出 → 按原判据（unknown 只是「没有判据」的兜底，不是新常态）。"""
    for params, expected in (
        ({"meter_addr": METER_ADDR}, "pass"),
        ({"expect_total": 2}, "pass"),
        ({"expect_empty": True}, "fail"),      # 给了空表判据 → 判 fail，不再兜底 unknown
        ({"count": 0}, "fail"),                # count=0 等价 expect_empty
    ):
        fake = FakeSimcon(job_ok(step_pass(raw_archive_table(2, [ADDR]))))
        options = {"start": 0, "count": 16}
        options.update(params)          # 给了 count=0 就用 0（覆盖默认 16）
        result = archive.block_query(action_of("10", 2, **options), ctx_with(fake))
        assert result.state == expected, (params, result.detail)


def test_archive_query_paginates_until_total_covered():
    """首页 1 条 < total=2 → 自动翻第二页，凑齐后判定。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(2, [ADDR2])), job_id="p1"),
        job_ok(step_pass(raw_archive_table(2, [ADDR])), job_id="p2"),
    )
    result = archive.block_query(
        action_of("10", 2, start=0, count=1, meter_addr=METER_ADDR), ctx_with(fake))
    assert result.state == "pass", result.detail
    assert len(fake.calls) == 2
    assert fake.calls[1][0]["send"]["params"] == {"start": 1, "count": 1}


# --------------------------------------------------------------------------- #
# C2. 地址角色（REQS-0042 §3.1 / S1.3）：调用方只给电表地址，档案侧换算后构帧与回查
# --------------------------------------------------------------------------- #


def test_archive_addr_of_matches_bench_truth():
    """台架实测：电表 645 地址 ``123456789012`` ↔ CCO 档案地址 ``129078563412``。

    顺带守住本文件两个常量的口径（``ADDR*`` 是档案侧、``METER_ADDR*`` 是电表侧）——
    常量写反了，下面几条「换算」用例会变成自证同义反复。
    """
    assert common.archive_addr_of("123456789012") == "129078563412"
    assert common.archive_addr_of("129078563412") == "123456789012"      # 换两次回原值
    assert common.meter_addr_wire("123456789012") == bytes.fromhex("123456789012")
    assert METER_ADDR == common.archive_addr_of(ADDR)
    assert METER_ADDR2 == common.archive_addr_of(ADDR2)


def test_archive_add_frame_carries_archive_addr_of_meter_addr():
    """★ ``archive.add(meter_addr="123456789012")``：帧里发的是**档案地址**
    ``129078563412``，其 6 字节线上线序仍等于 ``bytes.fromhex("123456789012")``。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),             # 幂等预查
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(1, ["129078563412"]))),
    )
    result = archive.block_add(
        action_of("11", 1, action="add", meter_addr="123456789012", protocol=2),
        ctx_with(fake),
    )
    assert result.state == "pass", result.detail

    sent = fake.calls[1][0]["send"]
    assert sent["params"]["addr"] == "129078563412"
    assert sent["params"]["addr"] == common.archive_addr_of("123456789012")
    # 1376.2 编码器把多字节字段按低字节在前摆 → 档案人读地址的线序 == 电表 645 线序
    assert _bcd_bytes(sent["params"]["addr"]) == bytes.fromhex("123456789012")


def test_archive_del_judges_every_address_it_deleted():
    """★ 多值删档：帧里删的与 10H-F2 回查的必须是同一批档案地址，且**逐只**回查。

    「删了 3 只只验第 1 只」= 另外 2 只没验就判 PASS；「删 A 回查 B」= 删档假 PASS。
    """
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(2, [ADDR, ADDR2]))),  # 幂等预查：两只都在
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(1, [ADDR2]))),   # ADDR 已删、ADDR2 还在
    )
    result = archive.block_del(
        action_of("11", 2, meter_addrs=[METER_ADDR, METER_ADDR2]), ctx_with(fake)
    )
    assert fake.calls[1][0]["send"]["params"] == {"meters": [ADDR, ADDR2]}
    assert result.state == "fail", result.detail
    assert "档案仍在" in result.detail
    assert "仍查得到 %s" % ADDR2 in result.detail
    # 逐只回查：只有 ADDR2 仍在（ADDR 确实删掉了，不误报）
    assert result.actual == "仍可查到 %s" % ADDR2


def test_archive_add_prefers_assert_addr_over_config_default():
    """★ 同一块内 action 与 assert 必须同一只表：action 没写地址时取 ``assert`` 里那只。

    ``action.params.meter_addr`` 与 ``assert[].meter_addr`` 都是 ``null`` 时两者都会解析成
    配置默认值（同一只表）；但**任一侧**单独写了地址时，必须让动作跟着判据走，否则
    建档/删档落在 A、回查却在 B —— 那是最典型的假 PASS。
    """
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),         # 幂等预查：空表（已查全）
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),     # 表里只有 ADDR
    )
    params = action_of("11", 1, action="add", protocol=2)
    params["_assert"] = [{"type": "archive_contains", "meter_addr": METER_ADDR}]
    result = archive.block_add(params, ctx_with(fake))

    assert result.state == "pass", result.detail
    assert fake.calls[1][0]["send"]["params"]["addr"] == ADDR


def test_archive_add_falls_back_to_config_default_addr():
    """三处（入参 / 声明 / 配置默认）都没有时用 ``case_defaults.meter_addr``（D2-2）。"""
    fallback = defaults.default_meter_addr()
    assert fallback, "出厂 case_defaults.json 应给出台架表地址"
    archive_addr = common.archive_addr_of(fallback)
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),             # 幂等预查
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(1, [archive_addr]))),
    )
    result = archive.block_add(action_of("11", 1, action="add", protocol=2), ctx_with(fake))
    assert result.state == "pass", result.detail
    assert fake.calls[1][0]["send"]["params"]["addr"] == archive_addr


def test_archive_add_rejects_invalid_given_addr_instead_of_using_default():
    """给了但不合法 ≠ 没给：显式垃圾地址绝不静默换成默认那只表（删档尤其危险）。"""
    fake = FakeSimcon()
    result = archive.block_add(
        action_of("11", 1, action="add", meter_addr="12345", protocol=2), ctx_with(fake)
    )
    assert result.state == "fail", result.detail
    assert "非法" in result.detail and "12345" in result.detail
    assert fake.calls == []          # 参数错 → 一帧都不发


# --------------------------------------------------------------------------- #
# D. 组网块
# --------------------------------------------------------------------------- #


def test_nwk_route_restart_confirm_pass_and_pause_of_reply_is_fail():
    fake = FakeSimcon(job_ok(step_pass(raw_confirm())))
    ok = nwk.block_route_restart(action_of("12", 1), ctx_with(fake))
    assert ok.state == "pass", ok.detail
    assert fake.calls[0][0]["send"] == {"afn": 0x12, "fn": 1, "params": {}}

    fake2 = FakeSimcon(job_ok(step_timeout()))
    bad = nwk.block_route_restart(action_of("12", 1), ctx_with(fake2))
    assert bad.state == "fail"


def test_nwk_active_registration_missing_duration_is_fail_and_names_param():
    """★ 缺参必须在下发前 FAIL，且 detail 点名缺哪个（REQS §10.7 事实 I）。"""
    fake = FakeSimcon()
    result = nwk.block_active_registration(
        action_of("11", 5, time6="000012231126", retry=3, slices=10), ctx_with(fake)
    )
    assert result.state == "fail", result.detail
    assert "duration" in result.detail
    assert "四参" in result.detail
    assert fake.calls == []            # 一帧都不发


def test_nwk_active_registration_missing_time6_is_fail_and_names_param():
    fake = FakeSimcon()
    result = nwk.block_active_registration(
        action_of("11", 5, duration=5, retry=3, slices=10), ctx_with(fake)
    )
    assert result.state == "fail"
    assert "time6" in result.detail
    assert fake.calls == []


def test_nwk_active_registration_full_params_build_correct_payload():
    fake = FakeSimcon(job_ok(step_pass(raw_confirm()), job_id="job-reg"))
    result = nwk.block_active_registration(
        action_of("11", 5, time6="000012231126", duration=5, retry=3, slices=10),
        ctx_with(fake),
    )
    assert result.state == "pass", result.detail
    sent = fake.calls[0][0]["send"]
    assert sent["afn"] == 0x11 and sent["fn"] == 5
    # time6（6B BCD hex）→ 构帧器要的 "SSMMHHDDMMYY" 数字串
    assert sent["params"] == {"time": "000012231126", "duration": 5, "retry": 3, "slices": 10}
    assert fake.calls[0][0]["expect"] == {"afn": 0x00, "fn": 1, "dir": "up"}


def test_nwk_active_registration_bad_time6_and_out_of_range_are_fail():
    fake = FakeSimcon()
    bad_bcd = nwk.block_active_registration(
        action_of("11", 5, time6="0000122311AB", duration=5, retry=3, slices=10), ctx_with(fake)
    )
    assert bad_bcd.state == "fail" and "BCD" in bad_bcd.detail

    bad_range = nwk.block_active_registration(
        action_of("11", 5, time6="000012231126", duration=70000, retry=3, slices=10),
        ctx_with(fake),
    )
    assert bad_range.state == "fail" and "越界" in bad_range.detail
    assert fake.calls == []


def test_nwk_archive_count_uses_archive_wording_not_online():
    """★ 正名：10H-F1「从节点总数量」是**档案数**，detail 必须写「档案」且不出现「在网」。"""
    fake = FakeSimcon(job_ok(step_pass(raw_onnet(12))))       # 台架实测档案数 12
    ok = nwk.block_archive_count(action_of("10", 1, min=1), ctx_with(fake))
    assert ok.state == "pass", ok.detail
    assert "档案" in ok.detail
    assert "在网" not in ok.detail
    assert "12" in ok.detail
    assert ok.expected == "档案数 ≥ 1" and ok.actual == 12


def test_nwk_archive_count_below_min_is_fail_with_archive_wording():
    fake = FakeSimcon(job_ok(step_pass(raw_onnet(0))))
    bad = nwk.block_archive_count(action_of("10", 1, min=1), ctx_with(fake))
    assert bad.state == "fail", bad.detail
    assert "档案数 0 < 期望档案数下限 1" in bad.detail
    assert "在网" not in bad.detail


def test_nwk_archive_count_requires_min_param():
    fake = FakeSimcon()
    result = nwk.block_archive_count(action_of("10", 1), ctx_with(fake))
    assert result.state == "fail" and "min" in result.detail
    assert "档案数" in result.detail and "在网" not in result.detail
    assert fake.calls == []


def test_nwk_onnet_count_is_compat_alias_delegating_to_archive_count():
    """旧 id 保留为兼容别名：内部委托新块，detail 标明别名，判据仍是档案数。"""
    fake = FakeSimcon(job_ok(step_pass(raw_onnet(12))))
    alias = nwk.block_onnet_count(action_of("10", 1, min=1), ctx_with(fake))
    assert alias.state == "pass", alias.detail
    assert "兼容别名" in alias.detail
    assert "nwk.archive_count" in alias.detail
    assert "档案" in alias.detail
    # 两个 id 各自注册（不是同一个对象），旧 id 每次真的走新实现
    assert nwk.block_onnet_count is not nwk.block_archive_count
    assert get_block("nwk.onnet_count") is nwk.block_onnet_count


def test_nwk_nodes_online_fail_when_answered_zero_and_all_qualities_zero():
    """★★ 防假 PASS 核心用例（真实台架现场）：档案 12 条、已抄=0、12 个信号品质全 0。
    旧块（用档案数冒充在网）会判 PASS；新块必须 FAIL。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_route(0x06, route_word=0x03, total=12, answered=0, relay=12))),
        job_ok(step_pass(raw_archive_nodes(REAL_NODE_COUNT, [0] * REAL_NODE_COUNT))),
    )
    # wait_s=0 = **显式单次探测**：只探一轮、不 sleep、不重试（轮询行为由
    # blocks/test_nwk_topology.py 覆盖）。本用例只验三态判定的结论句。
    result = nwk.block_nodes_online(action_of("10", 4, min=1, wait_s=0), ctx_with(fake))
    assert result.state == "fail", result.detail
    assert len(fake.calls) == 2, "wait_s=0 时只探一次（F4 + F2 各一跳）"
    assert "两路证据都明确为 0" in result.detail
    assert "已抄=0" in result.detail
    # 两路各自读数都要出现在 detail 里（便于人工复核）
    assert "10H-F4 已抄从节点数量=0" in result.detail
    assert "10H-F2 信号品质>0 的节点数=0" in result.detail
    assert "信号品质>0 的节点 0 个" in result.detail        # 两路明细里的扫描结果
    assert result.actual["档案数（不参与判定）"] == 12   # 档案数只是旁证，不参与判定
    # 两路都是只读查询：先 10H-F4，再 10H-F2（count 默认 32，start=0）
    assert fake.calls[0][0]["send"] == {"afn": 0x10, "fn": 4, "params": {}}
    assert fake.calls[1][0]["send"] == {
        "afn": 0x10, "fn": 2, "params": {"start": 0, "count": 32}
    }


def test_nwk_nodes_online_real_bench_frame_with_zero_quality_fails():
    """真实台架 10H-F4 帧 + 信号品质全 0 → 仍 FAIL（证明样本确实来自现场）。"""
    real = decode_frame(bytes.fromhex(REAL_ROUTE_F4_HEX))
    fake = FakeSimcon(
        job_ok(step_pass(bytes.fromhex(REAL_ROUTE_F4_HEX))),
        job_ok(step_pass(raw_archive_nodes(REAL_NODE_COUNT, [0] * REAL_NODE_COUNT))),
    )
    # step_pass 自己会 decode；先确认真实帧解出的关键字段口径
    assert real["items"][1]["name"] == "运行状态字"
    assert "路由完成=1" in real["items"][1]["desc"]
    result = nwk.block_nodes_online(action_of("10", 4, min=1, wait_s=0), ctx_with(fake))
    assert result.state == "fail", result.detail
    assert len(fake.calls) == 2, "wait_s=0 时只探一次"
    assert result.facts == {}          # 失败块绝不回写 provides 事实


def test_nwk_nodes_online_pass_when_answered_count_positive():
    """证据 A 已抄=3 > 0 → pass，detail 写清两路读数，provides 事实落地。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_route(0x00, route_word=0x03, total=12, answered=3, relay=2)),
               job_id="job-f4"),
        job_ok(step_pass(raw_archive_nodes(REAL_NODE_COUNT, [0] * REAL_NODE_COUNT)),
               job_id="job-f2"),
    )
    ok = nwk.block_nodes_online(action_of("10", 4, min=2, wait_s=0), ctx_with(fake))
    assert ok.state == "pass", ok.detail
    assert "10H-F4 已抄从节点数量=3" in ok.detail
    assert "10H-F2 信号品质>0 的节点数=0" in ok.detail
    assert ok.facts[nwk.FACT_NODES_ONLINE_MIN]["state"] == "yes"
    assert ok.evidence_ids == ["job-f4", "job-f2"]


def test_nwk_nodes_online_pass_on_signal_quality_alone():
    """证据 B 信号品质>0（已抄仍为 0）→ 也能 pass（两路任一 > 0）。"""
    signals = [0] * 11 + [7]
    fake = FakeSimcon(
        job_ok(step_pass(raw_route(0x00, route_word=0x03, total=12, answered=0, relay=0))),
        job_ok(step_pass(raw_archive_nodes(REAL_NODE_COUNT, signals))),
    )
    ok = nwk.block_nodes_online(action_of("10", 4, min=1, wait_s=0), ctx_with(fake))
    assert ok.state == "pass", ok.detail
    assert "10H-F2 信号品质>0 的节点数=1" in ok.detail
    assert "信号品质>0 的节点 1 个" in ok.detail


def test_nwk_nodes_online_unknown_when_both_channels_unavailable():
    """两路都取不到（F4 无应答 + F2 无应答）→ unknown，绝不判 pass。"""
    fake = FakeSimcon(job_ok(step_timeout()), job_ok(step_timeout()))
    result = nwk.block_nodes_online(action_of("10", 4, min=1, wait_s=0), ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert len(fake.calls) == 2, "wait_s=0 时只探一次（不重试）"
    assert "两路证据都取不到" in result.detail
    assert result.facts == {}


def test_nwk_nodes_online_uses_only_available_channel():
    """A 路取不到、B 路明确为 0 → 只用可得那路 → fail（不因缺一路就放行）。"""
    fake = FakeSimcon(
        job_ok(step_timeout()),                                   # F4 无应答
        job_ok(step_pass(raw_archive_nodes(REAL_NODE_COUNT, [0] * REAL_NODE_COUNT))),
    )
    result = nwk.block_nodes_online(action_of("10", 4, min=1, wait_s=0), ctx_with(fake))
    assert result.state == "fail", result.detail
    assert len(fake.calls) == 2, "wait_s=0 时只探一次"
    assert "可得的唯一一路证据明确为 0" in result.detail
    assert "10H-F4 路由运行状态查询未得到可判定应答" in result.detail   # 两路明细里说明 A 路为何缺


def test_nwk_nodes_online_never_uses_archive_total():
    """★ 档案数 12 但两路都是 0 → 仍然 fail（档案数不参与判定）。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_route(0x06, route_word=0x03, total=12, answered=0, relay=12))),
        job_ok(step_pass(raw_archive_nodes(12, [0] * 12))),
    )
    result = nwk.block_nodes_online(action_of("10", 4, min=1, wait_s=0), ctx_with(fake))
    assert result.state == "fail"
    assert len(fake.calls) == 2, "wait_s=0 时只探一次"
    assert "两路证据都明确为 0" in result.detail
    assert "档案数=12" in result.detail        # 档案数只作旁证出现在明细里


def test_nwk_nodes_online_min_defaults_to_one():
    """``min`` 缺省 = 1（块声明与实现同口径），不给 min 也能判。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_route(0x00, route_word=0x03, total=1, answered=1))),
        job_ok(step_pass(raw_archive_nodes(1, [0]))),
    )
    ok = nwk.block_nodes_online(action_of("10", 4, wait_s=0), ctx_with(fake))
    assert ok.state == "pass", ok.detail
    assert ok.expected == "在网节点数 ≥ 1"


def test_nwk_nodes_online_bad_min_is_fail_before_any_frame():
    fake = FakeSimcon()
    result = nwk.block_nodes_online(action_of("10", 4, min="many"), ctx_with(fake))
    assert result.state == "fail" and "min" in result.detail
    assert fake.calls == []


def test_nwk_route_status_default_expects_running():
    """工作状态=抄表 → pass；detail 里带上路由完成标志/档案数/已抄。"""
    fake = FakeSimcon(job_ok(step_pass(raw_route(0x00, route_word=0x03, total=12, answered=12))))
    ok = nwk.block_route_status(action_of("10", 4, wait_s=0), ctx_with(fake))
    assert ok.state == "pass", ok.detail
    assert "工作状态=抄表" in ok.detail
    assert ok.actual["分类"] == "running"


def test_nwk_route_status_learning_with_route_done_is_inconclusive_not_fail():
    """★★ 真实台架现场帧：路由完成=1 + 档案数 12 + 工作状态=学习 → inconclusive（不是 fail）。

    刚下发 12H-F1 启动路由后进入学习态是协议正常行为（正在注册节点），
    旧块直接判 FAIL = 误判。
    """
    raw = bytes.fromhex(REAL_ROUTE_F4_HEX)
    fake = FakeSimcon(job_ok(step_pass(raw)))
    # wait_s=0 = **显式单次探测**：验学习态分层口径，不验轮询（轮询见 test_nwk_topology.py）
    result = nwk.block_route_status(action_of("10", 4, wait_s=0), ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert len(fake.calls) == 1, "wait_s=0 时只探一次"
    assert result.state != "fail"
    assert BLOCK_TO_OUTCOME[result.state] == "inconclusive"
    assert "inconclusive" in result.detail
    assert "路由已完成" in result.detail and "学习" in result.detail
    assert "尚未进入抄表工作" in result.detail
    assert result.actual["分类"] == "learning_inconclusive"
    assert result.actual["路由完成标志"] is True
    assert result.actual["档案数"] == 12


def test_nwk_route_status_learning_without_route_done_is_unknown_not_fail():
    """学习 + 路由完成标志=0：单帧证明不了「持续」，默认 unknown；learning_fail=true 才 fail。"""
    raw = raw_route(0x02, route_word=0x00, total=3, answered=2)
    fake = FakeSimcon(job_ok(step_pass(raw)))
    default = nwk.block_route_status(action_of("10", 4, wait_s=0), ctx_with(fake))
    assert default.state == "unknown", default.detail
    assert len(fake.calls) == 1, "wait_s=0 时只探一次"
    assert default.actual["分类"] == "learning_incomplete"

    fake2 = FakeSimcon(job_ok(step_pass(raw)))
    strict = nwk.block_route_status(
        action_of("10", 4, learning_fail=True, wait_s=0), ctx_with(fake2))
    assert strict.state == "fail", strict.detail
    assert len(fake2.calls) == 1, "wait_s=0 时只探一次"
    assert "learning_fail=true" in strict.detail


def test_nwk_route_status_missing_fields_is_unknown():
    """应答里没有工作状态/当前状态 → unknown（不是 fail）。"""
    fake = FakeSimcon(job_ok(step_pass(build_frame(direction="up", afn=0x10, fn=4, appdata=b""))))
    # wait_s=0：unknown 结论本身不需要轮询（否则会真 sleep 满默认 60s）
    result = nwk.block_route_status(action_of("10", 4, wait_s=0), ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert len(fake.calls) == 1, "wait_s=0 时只探一次"


def test_nwk_route_status_explicit_expect_can_match_current_state():
    fake = FakeSimcon(job_ok(step_pass(raw_route(0x40))))     # 当前状态=搜表
    result = nwk.block_route_status(
        action_of("10", 4, expect=["搜表"], wait_s=0), ctx_with(fake))
    assert result.state == "pass", result.detail
    assert "当前状态=搜表" in result.detail


# --------------------------------------------------------------------------- #
# E. 能力层已知坑：409 / frames 404 / poll_timeout / 构帧失败 / unknown
# --------------------------------------------------------------------------- #


def _transport_factory(post_response, get_response=None):
    """构造 SimconCapability 的 fake transport，并记录每次调用。"""
    calls: list[tuple[str, str]] = []

    def transport(method, url, body, timeout):
        calls.append((method, url))
        if method == "POST":
            return post_response
        return get_response if get_response is not None else (200, {})

    return transport, calls


def test_verify_409_is_skipped_and_post_is_not_retried():
    """★ 409 = 闸门/会话忙：先抢占释放一次再重试一次（SH-1 / D2-7），仍失败则 skipped。

    口径变更（REQS-0042 v7 D7-1）：自愈层落地后，409 不再"只发一次"——
    时序为 ``POST verify(409) → POST /api/simcon/close → POST verify(重试一次)``。
    断言改成：close 恰好 1 次、verify 恰好 2 次（首发 + 重试），结果绝不判 pass。
    """
    from case_runner.capabilities.simcon import SimconCapability

    transport, calls = _transport_factory((409, {"detail": "simcon 会话忙（lease 被占用）"}))
    cap = SimconCapability(base_url="http://127.0.0.1:6553", transport=transport)
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(cap))

    assert result.state == "skipped", result.detail
    assert "409" in result.detail
    posts = [call for call in calls if call[0] == "POST"]
    verify_posts = [c for c in posts if c[1].endswith("/verification-runs")]
    close_posts = [c for c in posts if c[1].endswith("/api/simcon/close")]
    assert len(close_posts) == 1, calls      # 抢占释放恰好一次
    assert len(verify_posts) == 2, calls     # 首发 + 重试一次，绝不无限重试


def test_poll_timeout_is_unknown_not_fail():
    from case_runner.capabilities.simcon import SimconCapability

    transport, _ = _transport_factory((202, {"job_id": "job-1", "job_state": "queued"}))
    cap = SimconCapability(base_url="http://127.0.0.1:6553", transport=transport,
                           max_poll_seconds=0.0)
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(cap))
    assert result.state == "unknown", result.detail
    assert "poll_timeout" in result.detail and "不表示设备失败" in result.detail


def test_frames_404_gives_skipped_not_pass():
    """★ 步骤 pass 但没有 parsed、帧日志又回 404 → 看不到应答帧 ⇒ skipped。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR2]))),        # 幂等预查：已查全、不含 ADDR
        job_ok(step_pass_without_parsed(raw_confirm())),
        frames_result={"ok": False, "error": "http_404",
                       "detail": "当前没有帧日志会话（先 open 串口或执行 verify/step）"},
    )
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(fake))

    assert result.state == "skipped", result.detail
    assert "http_404" in result.detail
    assert fake.frames_calls and fake.frames_calls[0]["afn"] == 0x00


def test_frames_log_supplies_missing_parsed_instead_of_trusting_pass():
    """步骤没给 parsed 但帧日志能补到 → 用日志里的真实解析结果判定。"""
    raw = raw_confirm()
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR2]))),        # 幂等预查：不含 ADDR
        job_ok(step_pass_without_parsed(raw)),
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),
        frames_result={"ok": True, "entries": [
            {"seq": 7, "afn": "00", "fn": "F1", "updown": "up",
             "frame_hex": raw.hex().upper(), "parsed": decode_frame(raw)},
        ]},
    )
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(fake))
    assert result.state == "pass", result.detail
    assert "双判据成立" in result.detail


def test_server_side_frame_build_failure_is_unknown_not_device_fail():
    fake = FakeSimcon(job_ok(step_config_error()))
    result = nwk.block_active_registration(
        action_of("11", 5, time6="000012231126", duration=5, retry=3, slices=10),
        ctx_with(fake),
    )
    assert result.state == "unknown", result.detail
    assert "构帧失败" in result.detail and "非设备结论" in result.detail


def test_empty_verify_result_is_unknown_never_pass():
    """能力层回了空字典（拿不到结论）→ unknown，绝不折算 pass。"""
    fake = FakeSimcon({})
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert "未得到可判定" in result.detail


class _NotReadyCap:
    """自建能力替身：probe 不就绪（无台架），任何下发都该在门里被挡住。"""

    name = "simcon"

    def probe(self):
        return Health(ok=False, reason="未注入 base_url（构造参数或环境变量 WORKBENCH_BASE）")

    def verify(self, steps):  # pragma: no cover - 落进这里就是门失效
        raise AssertionError("能力不就绪时不该下发任何帧")


def test_bench_not_ready_is_skipped_not_fail(monkeypatch):
    """未注入能力 → 自建 + probe 不就绪 → skipped（块没执行，不是 FAIL）。

    自建能力的工厂现在只有一份（``common.resolve_simcon``），所以替身要打在
    ``common.SimconCapability`` 上；``archive``/``nwk`` 只是复用该工厂。
    """
    monkeypatch.setattr(common, "SimconCapability", lambda *a, **k: _NotReadyCap())

    block_result = archive.block_add(
        action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2))
    assert block_result.state == "skipped", block_result.detail
    assert "不就绪" in block_result.detail

    nwk_result = nwk.block_onnet_count(action_of("10", 1, min=1))
    assert nwk_result.state == "skipped", nwk_result.detail

    # 事实同一口径：探测不到 → unknown（既不是 yes 也不是 no）
    monkeypatch.setattr(facts_nwk, "simcon_handle", lambda: (_NotReadyCap(), False))
    assert facts_nwk.probe_route_running({}).state == "unknown"


# --------------------------------------------------------------------------- #
# F. 事实三态（FT-1 / FT-2 / FT-3）
# --------------------------------------------------------------------------- #


def _fact_with(monkeypatch, *results, frames_result=None):
    fake = FakeSimcon(*results, frames_result=frames_result)
    monkeypatch.setattr(facts_nwk, "simcon_handle", lambda: (fake, True))
    return fake


def test_fact_archive_contains_three_states(monkeypatch):
    _fact_with(monkeypatch, job_ok(step_pass(raw_archive_table(1, [ADDR]))))
    yes = facts_nwk.probe_archive_contains({"meter_addr": METER_ADDR})
    assert isinstance(yes, FactValue) and yes.state == "yes"
    assert ADDR in yes.detail

    _fact_with(monkeypatch, job_ok(step_pass(raw_archive_table(1, [ADDR2]))))
    no = facts_nwk.probe_archive_contains({"meter_addr": METER_ADDR})
    assert no.state == "no"

    _fact_with(monkeypatch, job_error("poll_timeout", "轮询超时"))
    unknown = facts_nwk.probe_archive_contains({"meter_addr": METER_ADDR})
    assert unknown.state == "unknown"
    assert unknown.state != "yes"


def test_fact_archive_contains_bad_addr_is_unknown(monkeypatch):
    _fact_with(monkeypatch)
    result = facts_nwk.probe_archive_contains({"meter_addr": "123"})
    assert result.state == "unknown"
    assert "meter_addr" in result.detail


def test_fact_onnet_count_min_three_states(monkeypatch):
    """兼容别名事实：判的是**档案数**（detail 里用「档案」，不给「在网」结论）。"""
    _fact_with(monkeypatch, job_ok(step_pass(raw_onnet(4))))
    yes = facts_nwk.probe_onnet_count_min({"min": 2})
    assert yes.state == "yes" and "档案" in yes.detail

    _fact_with(monkeypatch, job_ok(step_pass(raw_onnet(1))))
    no = facts_nwk.probe_onnet_count_min({"min": 2})
    assert no.state == "no" and "在网" not in no.detail

    _fact_with(monkeypatch, job_ok(step_pass(raw_onnet(1))))
    assert facts_nwk.probe_onnet_count_min({"min": 2}).state != "yes"


def test_fact_nodes_online_min_three_states(monkeypatch):
    """真在网事实：两路证据都为 0 → no；任一 > 0 → yes；两路都取不到 → unknown。"""
    _fact_with(
        monkeypatch,
        job_ok(step_pass(raw_route(0x06, route_word=0x03, total=12, answered=0, relay=12))),
        job_ok(step_pass(raw_archive_nodes(12, [0] * 12))),
    )
    dead = facts_nwk.probe_nodes_online_min({"min": 1})
    assert dead.state == "no", dead.detail
    assert "两路证据都明确为 0" in dead.detail
    assert "12" in dead.detail            # detail 里给得出档案数旁证

    _fact_with(
        monkeypatch,
        job_ok(step_pass(raw_route(0x00, route_word=0x03, total=12, answered=4))),
        job_ok(step_pass(raw_archive_nodes(12, [0] * 12))),
    )
    alive = facts_nwk.probe_nodes_online_min({"min": 1})
    assert alive.state == "yes" and "已抄从节点数量=4" in alive.detail

    _fact_with(monkeypatch, job_ok(step_timeout()), job_ok(step_timeout()))
    assert facts_nwk.probe_nodes_online_min({"min": 1}).state == "unknown"


def test_fact_route_running_three_states(monkeypatch):
    """事实口径与块同步：只有「工作状态=抄表」才是 yes；学习态按路由完成标志分层。

    学习 + 路由已完成 + 档案>0 → unknown（inconclusive，**不是 no**）；
    学习 + 路由未完成 → 单帧不能证明「持续」→ unknown。
    """
    _fact_with(monkeypatch, job_ok(step_pass(raw_route(0x00))))
    running = facts_nwk.probe_route_running({})
    assert running.state == "yes" and "抄表" in running.detail

    # 学习 + 路由完成标志=1 + 档案 12 条（真实台架现场帧）
    _fact_with(monkeypatch, job_ok(step_pass(bytes.fromhex(REAL_ROUTE_F4_HEX))))
    inconclusive = facts_nwk.probe_route_running({})
    assert inconclusive.state == "unknown"
    assert "学习" in inconclusive.detail and "inconclusive" in inconclusive.detail

    # 学习 + 路由完成标志=0
    _fact_with(monkeypatch, job_ok(step_pass(raw_route(0x02, route_word=0x00))))
    learning = facts_nwk.probe_route_running({})
    assert learning.state == "unknown" and "学习" in learning.detail
    assert learning.state != "yes"

    _fact_with(monkeypatch, job_ok(step_timeout()))
    unknown = facts_nwk.probe_route_running({})
    assert unknown.state == "unknown"


def test_facts_unknown_when_capability_not_ready(monkeypatch):
    """台架不在线 → 事实未知（不是 no，更不是 yes）。"""
    monkeypatch.setattr(facts_nwk, "simcon_handle",
                        lambda: (archive.SimconCapability(base_url=""), False))
    assert facts_nwk.probe_archive_contains({"meter_addr": METER_ADDR}).state == "unknown"
    assert facts_nwk.probe_onnet_count_min({"min": 1}).state == "unknown"
    assert facts_nwk.probe_route_running({}).state == "unknown"


def test_facts_reuse_block_judgement_functions(monkeypatch):
    """FT-2：事实与块共用同一判定函数（不另写一份逻辑）。"""
    assert facts_nwk.archive_snapshot is archive.archive_snapshot
    assert facts_nwk.judge_contains is archive.judge_contains
    assert facts_nwk.archive_count_snapshot is nwk.archive_count_snapshot
    assert facts_nwk.judge_archive_count_min is nwk.judge_archive_count_min
    assert facts_nwk.onnet_snapshot is nwk.onnet_snapshot          # 兼容别名，同一对象
    assert facts_nwk.judge_onnet_min is nwk.judge_onnet_min        # 兼容别名，同一对象
    assert facts_nwk.nodes_online_snapshot is nwk.nodes_online_snapshot
    assert facts_nwk.judge_nodes_online is nwk.judge_nodes_online
    assert facts_nwk.route_snapshot is nwk.route_snapshot
    assert facts_nwk.judge_route_running is nwk.judge_route_running
    # 兼容别名指向同一实现（不是复制的一份）
    assert common.onnet_snapshot is common.archive_count_snapshot
    assert common.judge_onnet_min is common.judge_archive_count_min

    # 反向验证：改一处判定口径，事实与块的结论一起变
    monkeypatch.setattr(archive, "judge_contains", lambda snapshot, addr: ("yes", "被替换"))
    _fact_with(monkeypatch, job_ok(step_pass(raw_archive_table(1, [ADDR2]))))
    monkeypatch.setattr(facts_nwk, "judge_contains", archive.judge_contains)
    assert facts_nwk.probe_archive_contains({"meter_addr": METER_ADDR}).state == "yes"


# --------------------------------------------------------------------------- #
# G. 事实与 planner/执行器串联（前置事实不满足 → 块 skipped）
# --------------------------------------------------------------------------- #


def test_unknown_fact_makes_downstream_block_skipped_not_pass():
    """前置事实不满足 → 块 ``skipped`` → 用例 ``blocked``（台架不具备，绝不是 pass）。

    语义来源 REQS-0042 §3.5 / D2-4：``skipped`` 聚合到 ``blocked``；``unknown`` 才是
    ``inconclusive``。这里事实是 ``unknown``，但块因此 skipped，所以结论是 blocked。
    """
    spec = BlockSpec(
        id="archive.add", title="加档案", needs=["archive.contains"],
        provides=["archive.contains"],
        action={"afn": "11", "fn": 1,
                "params": {"action": "add", "meter_addr": METER_ADDR, "protocol": 2}},
    )
    case = CaseSpec(
        id="case.gated", title="前置事实不满足", blocks=["archive.add"],
        steps=[CaseStep(no=1, do="加档案", expect="档案落地")],
    )
    facts = FactsContext(probe=lambda fact_id, params: FactValue(state="unknown", detail="没台架"))
    fake = FakeSimcon(job_ok(step_pass(raw_confirm())))
    ctx = RunContext(facts_ctx=facts, block_specs={"archive.add": spec},
                     extras={"simcon": fake})
    outcome = execute_case(case, ctx)

    assert outcome.verdict == "blocked"
    assert outcome.blocks[0].state == "skipped"
    assert "archive.contains" in outcome.blocks[0].detail
    assert outcome.counts["pass"] == 0
    assert fake.calls == []           # 事实 unknown → 一帧都不发


def test_case_orchestration_archive_then_nwk():
    """用例编排：档案 → 组网重启 → **真实可达节点数**（顺序由 CaseSpec.blocks 决定）。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),             # archive.add 幂等预查
        job_ok(step_pass(raw_confirm())),                       # archive.add 判据 1
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),         # archive.add 判据 2
        job_ok(step_pass(raw_confirm())),                       # nwk.route_restart 确认
        job_ok(step_pass(raw_route(0x00, route_word=0x03, total=1, answered=1))),  # A 路
        job_ok(step_pass(raw_archive_nodes(1, [9]))),            # B 路
    )
    specs = {
        "archive.add": BlockSpec(
            id="archive.add", title="加档案", needs=["bench.simcon_online"],
            provides=["archive.contains"],
            action={"afn": "11", "fn": 1,
                    "params": {"action": "add", "meter_addr": METER_ADDR, "protocol": 2}}),
        "nwk.route_restart": BlockSpec(
            id="nwk.route_restart", title="启动路由", needs=["bench.simcon_online"],
            action={"afn": "12", "fn": 1, "params": {}}),
        "nwk.nodes_online": BlockSpec(
            id="nwk.nodes_online", title="真实可达节点数", needs=["bench.simcon_online"],
            action={"afn": "10", "fn": 4, "params": {"min": 1, "count": 32}}),
    }
    case = CaseSpec(
        id="case.nwk_join", title="建档后组网", blocks=list(specs),
        steps=[
            CaseStep(no=1, do="下发 11H-F1 建档", expect="00H-F1 确认且档案可回查"),
            CaseStep(no=2, do="下发 12H-F1 启动路由", expect="00H-F1 确认"),
            CaseStep(no=3, do="查真实可达节点数", expect="已抄数与信号品质至少一路 > 0"),
        ],
    )
    facts = FactsContext(probe=lambda fact_id, params: FactValue(state="yes", detail="台架在线"))
    ctx = RunContext(facts_ctx=facts, block_specs=specs, extras={"simcon": fake})
    outcome = execute_case(case, ctx)

    assert outcome.verdict == "pass", [b.to_dict() for b in outcome.blocks]
    assert outcome.passed_blocks() == ["archive.add", "nwk.route_restart", "nwk.nodes_online"]
    # 6 次下发：幂等预查 / 建档 / 回查 / 路由重启 / 10H-F4 / 10H-F2
    assert len(fake.calls) == 6


# --------------------------------------------------------------------------- #
# H. 状态口径一致性（领导裁决）：帧已发出但超时 → fail；帧未发出/构帧失败 → unknown；
#    409 闸门忙 / 台架不可达 / frames 404 无会话 → skipped
# --------------------------------------------------------------------------- #


def test_nwk_confirm_timeout_is_fail_and_cites_sent_frame():
    """帧确实发出去了（有 sent_hex）但没应答 → fail（测试失败，不是 unknown）。"""
    fake = FakeSimcon(job_ok(step_timeout()))
    result = nwk.block_route_restart(action_of("12", 1), ctx_with(fake))
    assert result.state == "fail", result.detail
    assert "已发出" in result.detail and SENT_HEX in result.detail


def test_nwk_frame_not_sent_is_unknown_not_fail():
    """帧没发出去（发送失败 / 无 sent_hex）→ unknown（配置/台架问题，不是设备结论）。"""
    fake = FakeSimcon(job_ok({
        "name": "step", "result": "fail", "reason": "发送失败：串口未打开",
        "sent_hex": "", "matched": None, "parsed": None,
    }))
    result = nwk.block_onnet_count(action_of("10", 1, min=1), ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert "未发出" in result.detail


def test_nwk_verify_409_is_skipped_and_post_not_retried():
    """409 = 闸门/会话忙 → 抢占释放一次 + 重试一次后仍失败 → skipped（不判设备失败）。

    口径变更（REQS-0042 v7 D7-1）：同 archive 侧，409 会触发 SH-1 自愈一次。
    """
    from case_runner.capabilities.simcon import SimconCapability

    transport, calls = _transport_factory((409, {"detail": "simcon 会话忙（lease 被占用）"}))
    cap = SimconCapability(base_url="http://127.0.0.1:6553", transport=transport)
    result = nwk.block_onnet_count(action_of("10", 1, min=1), ctx_with(cap))
    assert result.state == "skipped", result.detail
    posts = [call for call in calls if call[0] == "POST"]
    assert len([c for c in posts if c[1].endswith("/api/simcon/close")]) == 1
    assert len([c for c in posts if c[1].endswith("/verification-runs")]) == 2


def test_verify_unreachable_bench_is_skipped_not_fail():
    """台架不可达 → skipped（前置不满足），不是设备 FAIL，也不是 unknown。"""
    fake = FakeSimcon(job_error("unreachable", "连接被拒绝"))
    result = archive.block_add(action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2),
                               ctx_with(fake))
    assert result.state == "skipped", result.detail
    assert "台架不可用" in result.detail and "unreachable" in result.detail


# --------------------------------------------------------------------------- #
# I. 块层解耦守卫（common.py 是唯一公共件；域模块之间零 import）
# --------------------------------------------------------------------------- #

BLOCKS_DIR = pathlib.Path(archive.__file__).resolve().parent
DOMAIN_MODULES = ("archive", "nwk", "facts_nwk", "fix", "reset",
                  "meter", "facts_bench", "facts_meter")

#: 只允许存在一份的判定/工具函数（复制即违规，REQS FT-2）
SHARED_FUNCTIONS = (
    "judge_step", "judge_confirm_step", "send_step", "call_verify",
    "verify_failure_state", "journal_parsed", "block_action_params",
    "require_target", "capability_gate", "resolve_simcon", "normalize_addr",
    "archive_snapshot", "judge_contains",
    "archive_count_snapshot", "judge_archive_count_min", "onnet_snapshot", "judge_onnet_min",
    "classify_route_state", "nodes_online_snapshot", "judge_nodes_online",
    "route_snapshot", "judge_route_status", "judge_route_running",
    "judge_simeter", "judge_simcon", "judge_sta_online", "snapshot_kind",
    "simeter_capability", "simcon_capability", "module_capability",
)


def _imports_of(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
    return found


def test_domain_modules_do_not_import_each_other():
    """★ 兄弟耦合守卫：``archive``/``nwk``/``facts_nwk``… 之间只能通过 ``common`` 共享。"""
    violations: list[str] = []
    for name in DOMAIN_MODULES:
        path = BLOCKS_DIR / f"{name}.py"
        for lineno, module in _imports_of(path):
            if not module.startswith("case_runner.blocks."):
                continue
            leaf = module.rsplit(".", 1)[-1]
            if leaf in DOMAIN_MODULES and leaf != name:
                violations.append(f"{path.name}:{lineno} → {module}")
    assert violations == [], "块模块之间不得互相 import：\n" + "\n".join(violations)


def test_shared_judgement_functions_are_defined_only_in_common():
    """FT-2：判定逻辑只有一份 —— 除 ``common.py`` 外任何域模块都不得再定义同名函数。"""
    duplicates: list[str] = []
    for name in DOMAIN_MODULES:
        path = BLOCKS_DIR / f"{name}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name in SHARED_FUNCTIONS:
                duplicates.append(f"{path.name}:{node.lineno} def {node.name}")
    assert duplicates == [], "公共判定函数只保留 common.py 一份：\n" + "\n".join(duplicates)

    # 事实与块用的是同一个对象（不是「看起来一样」的两份实现）
    assert facts_nwk.judge_contains is common.judge_contains
    assert facts_nwk.judge_archive_count_min is common.judge_archive_count_min
    assert facts_nwk.judge_onnet_min is common.judge_onnet_min
    assert facts_nwk.judge_nodes_online is common.judge_nodes_online
    assert facts_nwk.judge_route_running is common.judge_route_running
    assert archive.archive_snapshot is common.archive_snapshot
    assert nwk.archive_count_snapshot is common.archive_count_snapshot
    assert nwk.onnet_snapshot is common.onnet_snapshot
    assert nwk.nodes_online_snapshot is common.nodes_online_snapshot
    assert archive.judge_step is common.judge_step
    assert nwk.judge_step is common.judge_step


# --------------------------------------------------------------------------- #
# J. 真台架集成路径（默认跳过；本机 8790 的 mapping_id=simcon 尚未生效，见交付说明）
# --------------------------------------------------------------------------- #

LIVE_BENCH = os.environ.get("CASE_RUNNER_LIVE") == "1" and bool(
    os.environ.get("WORKBENCH_BASE")
)


@pytest.mark.skipif(
    not LIVE_BENCH,
    reason="真台架集成用例：需 WORKBENCH_BASE 且 CASE_RUNNER_LIVE=1 显式开启",
)
def test_live_archive_query_is_readonly_and_parseable():
    """真台架只读路径：10H-F2 查一次档案表（**不建档、不删档**）。"""
    from case_runner.capabilities.simcon import SimconCapability

    cap = SimconCapability()
    health = cap.probe()
    if not health.ok:
        pytest.skip("工作台/simcon 不可用：%s" % health.reason)
    snapshot = archive.archive_snapshot(cap=cap, start=0, count=16, max_pages=1)
    if str(snapshot.get("state")) != "answered":
        pytest.skip(
            "查询未得到可判定应答（%s）：%s"
            % (snapshot.get("state"), snapshot.get("detail"))
        )
    assert isinstance(snapshot.get("total"), int)
    assert isinstance(snapshot.get("addresses"), list)


# --------------------------------------------------------------------------- #
# C3. 幂等（REQS-0042 §3.2 / **ID-1** / **ID-2**）
#     「同一方案连跑两次，两次结论一致」—— 不靠 CCO 容忍重复建档/重复删档。
# --------------------------------------------------------------------------- #


class FakeArchiveBench:
    """**有状态**的假台架：真按帧改档案表，用来验幂等闭环（连跑两次）。

    与 :class:`FakeSimcon`（脚本化队列）不同，这里按「台架现在的档案表」应答：

    ========================= ====================================================
    ``11H-F1``（建档）        地址已在 → **回 00H-F2 否认帧（错误字 7「表号不存在」）**
                              这是真实 CCO 对重复建档的最坏反应；地址不在 → 加进表 + 00H-F1
    ``11H-F2``（删档）        逐个从表里删掉 + 00H-F1
    ``10H-F2``（查档）        回当前档案表（total = len(表)）
    ========================= ====================================================

    于是「幂等一旦失效」不是悄悄过，而是第二次建档就被否认帧判 FAIL —— 用例会红。
    """

    name = "simcon"

    def __init__(self, addrs=()):
        self.addrs: list[str] = [str(addr) for addr in addrs]
        #: 每次 verify 的 (afn, fn)，按发生顺序
        self.sent: list[tuple[int, int]] = []
        #: 下发过几次 11H-F1 建档帧（幂等生效时应为 1；第二次跑必须是 1 不变）
        self.add_frames = 0
        #: 下发过几次 11H-F2 删档帧
        self.del_frames = 0

    # -- 能力契约 ---------------------------------------------------------- #
    def probe(self):
        return Health(ok=True, reason="fake archive bench")

    def verify(self, steps):
        step = steps[0]
        send = step["send"]
        afn = int(send["afn"])
        fn = int(send["fn"])
        params = send.get("params") or {}
        self.sent.append((afn, fn))

        if afn == 0x11 and fn == 1:                    # 建档
            self.add_frames += 1
            addr = str(params.get("addr") or "")
            if addr in self.addrs:
                # 真实 CCO：重复建档 → 00H-F2 否认帧（错误状态字 7 = 表号不存在）
                return job_ok(step_pass(raw_deny(7)))
            self.addrs.append(addr)
            return job_ok(step_pass(raw_confirm()))
        if afn == 0x11 and fn == 2:                    # 删档
            self.del_frames += 1
            for addr in params.get("meters") or []:
                if addr in self.addrs:
                    self.addrs.remove(addr)
            return job_ok(step_pass(raw_confirm()))
        if afn == 0x10 and fn == 2:                    # 查档
            return job_ok(step_pass(raw_archive_table(len(self.addrs), list(self.addrs))))
        raise AssertionError("FakeArchiveBench: 不该收到 afn=0x%02X fn=%d" % (afn, fn))

    def frames(self, **kwargs):  # pragma: no cover - 幂等路径不该翻帧日志
        raise AssertionError("FakeArchiveBench: 幂等路径不该查会话帧日志")


def test_archive_add_already_exists_is_pass_with_zero_send():
    """★ 幂等（ID-1）：档案已在 → ``pass`` +「已存在，跳过下发」，**零 11H-F1 帧**。

    「零次下发」用 fake 的调用次数断言：只允许一次**只读** 10H-F2 预查，
    不允许任何 11H 帧，也不允许去翻会话帧日志（那里什么都没有）。
    """
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR])), job_id="job-pre"),
    )
    result = archive.block_add(
        action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2), ctx_with(fake)
    )

    assert result.state == "pass", result.detail
    assert "已存在，跳过下发" in result.detail
    assert len(fake.calls) == 1, fake.calls          # 只有那一次只读预查
    sends = [step["send"] for call in fake.calls for step in call]
    assert [(step["afn"], step["fn"]) for step in sends] == [(0x10, 2)]   # 零 11H-F1
    assert fake.frames_calls == []                   # 不翻帧日志
    assert result.evidence_ids == ["job-pre"]        # pass 必须挂在那次真实查询上
    assert ADDR in (result.actual or "") and ADDR in result.detail
    assert result.facts["archive.contains"]["state"] == "yes"


def test_archive_add_incomplete_precheck_is_unknown_and_sends_nothing():
    """★ 没查全 ≠ 不存在（ID-1 边界）：预查被窗口截断 → ``unknown``，绝不据此下发。

    这是本需求最容易写错的一条：把 ``complete=False`` 当「不存在」去建档，
    就等于「不知道就先破坏一次」。
    """
    fake = FakeSimcon(job_ok(step_pass(raw_archive_table(40, [ADDR2]))))   # 40 条只看到 1 条
    result = archive.block_add(
        action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2,
                  count=1, max_pages=1),
        ctx_with(fake),
    )

    assert result.state == "unknown", result.detail
    assert "未查全" in result.detail
    assert "不能当作「不存在」去下发" in result.detail and "11H-F1" in result.detail
    assert "未覆盖全部档案" in result.detail          # 来自 judge_contains 的同一份判据
    assert len(fake.calls) == 1
    assert all(step["send"]["afn"] == 0x10 for call in fake.calls for step in call)


def test_archive_add_precheck_without_answer_is_unknown_not_send():
    """预查帧发出但没应答 → ``unknown``（不表示设备没建档），且不据此下发建档帧。"""
    fake = FakeSimcon(job_ok(step_timeout()))
    result = archive.block_add(
        action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2), ctx_with(fake))

    assert result.state == "unknown", result.detail
    assert "未得到可判定应答" in result.detail and "11H-F1" in result.detail
    assert len(fake.calls) == 1


def test_archive_add_twice_on_same_bench_is_pass_both_times():
    """★ 幂等闭环（ID-1）：同一有状态台架连跑两次 ``archive.add`` → **两次都 pass**。

    第二次必须走「已存在，跳过下发」，且**不再发第二颗 11H-F1** —— 否则台架会回
    00H-F2 否认帧（本 fake 就照着最坏反应实现），第二次就会 FAIL。
    """
    bench = FakeArchiveBench()
    call = action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2)

    first = archive.block_add(call, ctx_with(bench))
    second = archive.block_add(call, ctx_with(bench))

    assert first.state == "pass", first.detail
    assert "双判据成立" in first.detail
    assert second.state == "pass", second.detail
    assert "已存在，跳过下发" in second.detail
    assert bench.add_frames == 1, bench.sent        # 建档帧全程只发了一次
    assert bench.sent == [(0x10, 2), (0x11, 1), (0x10, 2), (0x10, 2)]
    assert bench.addrs == [ADDR]                    # 档案表仍是唯一一条


def test_archive_add_precheck_compares_archive_addr_not_meter_addr():
    """★ 地址角色守卫：预查按**档案地址**比对。

    表里只有电表口径那串（``METER_ADDR = 010000000000``）而档案口径的 ``ADDR`` 不在，
    必须判「不存在」→ 照常下发；若拿电表口径去比对，这里会被误判成「已存在」而跳过建档。
    """
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [METER_ADDR]))),      # 表里是电表口径
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),            # 建档后按档案口径落地
    )
    result = archive.block_add(
        action_of("11", 1, action="add", meter_addr=METER_ADDR, protocol=2), ctx_with(fake))

    assert result.state == "pass", result.detail
    assert "双判据成立" in result.detail            # 没有被误判成「已存在，跳过下发」
    assert len(fake.calls) == 3
    assert fake.calls[1][0]["send"]["params"]["addr"] == ADDR


def test_archive_del_target_already_absent_is_pass_with_zero_delete_frame():
    """★ 幂等（ID-2）：目标本就不存在 → ``pass`` +「本就不存在」，**零 11H-F2 删档帧**。"""
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR2])), job_id="job-pre"))
    result = archive.block_del(
        action_of("11", 2, meter_addrs=[METER_ADDR]), ctx_with(fake))

    assert result.state == "pass", result.detail
    assert result.state != "fail"
    assert "本就不存在" in result.detail
    assert "未发 11H-F2" in result.detail
    sends = [step["send"] for call in fake.calls for step in call]
    assert [(step["afn"], step["fn"]) for step in sends] == [(0x10, 2)]   # 零删档帧
    assert result.evidence_ids == ["job-pre"]


def test_archive_del_only_sends_addresses_still_present():
    """预查显示 ADDR 已不在 → 删档帧只带 **仍存在** 的 ADDR2。

    把「表号不存在」的地址塞进 11H-F2，真实 CCO 会回 00H-F2 否认帧 → 假 FAIL；
    但后置判据仍逐个验**调用方要求的全部目标**，不因为少发一只就少验一只。
    """
    fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(1, [ADDR2]))),      # 预查：只剩 ADDR2
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(0, []))),
    )
    result = archive.block_del(
        action_of("11", 2, meter_addrs=[METER_ADDR, METER_ADDR2]), ctx_with(fake))

    assert result.state == "pass", result.detail
    assert fake.calls[1][0]["send"]["params"] == {"meters": [ADDR2]}
    assert "未纳入删档帧" in result.detail and ADDR in result.detail


def test_archive_del_incomplete_precheck_is_unknown_and_sends_no_delete():
    """一个都查不到却「没查全」→ 既不能说「本就不存在」，也不能猜着删。"""
    fake = FakeSimcon(job_ok(step_pass(raw_archive_table(40, [ADDR2]))))
    result = archive.block_del(
        action_of("11", 2, meter_addrs=[METER_ADDR], count=1, max_pages=1), ctx_with(fake))

    assert result.state == "unknown", result.detail
    assert "未发 11H-F2" in result.detail
    assert len(fake.calls) == 1


def test_archive_del_twice_on_same_bench_is_pass_both_times():
    """★ 幂等闭环（ID-2）：同一有状态台架连跑两次 ``archive.del`` → 两次都 pass。"""
    bench = FakeArchiveBench(addrs=[ADDR])
    call = action_of("11", 2, meter_addrs=[METER_ADDR])

    first = archive.block_del(call, ctx_with(bench))
    second = archive.block_del(call, ctx_with(bench))

    assert first.state == "pass", first.detail
    assert "双判据成立" in first.detail
    assert second.state == "pass", second.detail
    assert "本就不存在" in second.detail
    assert bench.del_frames == 1, bench.sent        # 删档帧全程只发了一次
    assert bench.addrs == []


def test_default_addr_guard_add_takes_it_del_refuses_it(monkeypatch):
    """★ 默认地址不被滥用（D2-2）：同一个配置默认地址——
    ``archive.add`` 不传 → 允许用它；``archive.del`` 不传 → ``fail`` 且**零帧**
    （连只读预查都不发，避免「预查顺手看到了默认那只表」被当成授权）。

    ``monkeypatch`` 掉 ``default_meter_addr`` 是为了不依赖现场 ``config/case_defaults.json``
    ——无台架单测必须把地址兜底钉死，否则别人的现场配置会让本用例漂。
    """
    monkeypatch.setattr(archive, "default_meter_addr", lambda: METER_ADDR)

    add_fake = FakeSimcon(
        job_ok(step_pass(raw_archive_table(0, []))),           # 幂等预查：不存在
        job_ok(step_pass(raw_confirm())),
        job_ok(step_pass(raw_archive_table(1, [ADDR]))),
    )
    added = archive.block_add(action_of("11", 1, action="add", protocol=2), ctx_with(add_fake))
    assert added.state == "pass", added.detail
    assert add_fake.calls[1][0]["send"]["params"]["addr"] == ADDR   # 确实用了默认表

    del_fake = FakeSimcon()
    deleted = archive.block_del(action_of("11", 2), ctx_with(del_fake))
    assert deleted.state == "fail", deleted.detail
    assert "破坏性动作" in deleted.detail and "不接受配置默认地址" in deleted.detail
    assert del_fake.calls == []                       # 零帧：连预查都不发
