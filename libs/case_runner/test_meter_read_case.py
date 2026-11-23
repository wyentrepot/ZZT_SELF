"""``meter_read`` 用例：**真读数值**（正向有功总电能 + A 相电压）的三段守门。

背景（REQS-0042，用户原话）：「STA 中继到了表口 → 模拟电表回了本表地址，这对吗？
抄表为什么是回本表地址？你应该抄读 A 相电压之类的。」

原用例把「645 **13H 读通信地址**回本表地址」当成抄表通了——13H 本来就该回本表地址，
回对了不代表抄到任何数值。现在用例按 ``plan`` 显式实例化 4 步：13H 只判「STA 能取到
表地址」（并在 steps 文案里写明**不是抄表**），抄表改为 **11H 读两个数值**并带期望值：

===========  ==================  ==========================  ==================
step         块                   参数                        判据
===========  ==================  ==========================  ==================
``s1``       ``meter.addr_probe``  （块声明默认 = 645 13H）    内层地址域 == 表地址
``s2``       ``meter.read_645``   ``di=00010000`` 期望 1234.56  电能数值相等
``s3``       ``meter.read_645``   ``di=02010100`` 期望 220.1    A 相电压数值相等
``s4``       ``meter.brain_local`` ``di=02010100``              表端脑本地对照
===========  ==================  ==========================  ==================

覆盖（任务书逐条对应）：

1. 声明可加载：``plan`` 4 步、step id 唯一、引用的块/事实都存在（``problems == []``）；
2. 同一块 ``meter.read_645`` 两步、DI 不同 → 规划出两个 step，参数互不污染
   （``StepSpec.params`` 与 ``ctx.step_params`` 两层都验）；
3. 假能力（无台架）下：A 相电压 220.1 → pass；数值不符 → fail；DI 回显不一致 → fail；
4. 645 帧里 DI 的**字节序**用单测锁死（显示序 → 传输序 +33H，``02010100`` → ``33343435``）；
5. 期望值比较口径（浮点 + 相对容差，非字符串精确相等）与「上行数据单元为空」的行为。

**无台架**：不连 8790、不开串口。``cli`` 的地址兜底全部显式关掉（参照
``test_cli.py::isolate_service_discovery``），能力句柄一律用假对象注入，
事实探测体也换成桩（不查真工作台）。
"""

from __future__ import annotations

import json
import pathlib

import pytest

import case_runner.cli as cli
from case_runner.blocks import loader
from case_runner.blocks.common import meter_addr_wire
from case_runner.blocks.meter import (
    CONTROL_READ_ADDR,
    build_645_read_frame,
    build_645_request,
    di_echo_hex,
    judge_read_645,
)
from case_runner.capabilities.frames import FramesCapability
from case_runner.core.executor import RunContext, effective_params, execute_case
from case_runner.core.models import BLOCK_FAIL, BLOCK_PASS, CaseSpec
from case_runner.core.planner import FactsContext, plan

#: 台架事实：模拟电表脑地址（``case_defaults.meter_addr`` 同值；645 帧地址域不反转）
BENCH_ADDR = "123456789012"

#: 台架寄存器表两个数值（``data/defaults`` 之外的既有事实，见任务书）：
#: ``00010000`` 正向有功总电能 001234.56 kWh、``02010100`` A 相电压 220.1 V
ENERGY_DI = "00010000"
ENERGY_VALUE = 1234.56
VOLTAGE_DI = "02010100"
VOLTAGE_VALUE = 220.1

DATA_DIR = pathlib.Path(loader.DATA_DIR)
REAL_FRAMES = FramesCapability()


# --------------------------------------------------------------------------- #
# 隔离：绝不碰真工作台（地址兜底显式关掉）+ 注册表就绪
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def no_bench(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """整个文件默认状态：没有任何工作台地址可被发现。

    - 运行时文件指到 ``tmp_path``（绝不读仓库里那份真实端口文件）；
    - 末档 ``DEFAULT_BASE_URL = ""`` + 清 ``WORKBENCH_BASE`` → 什么都没发现；
    - 顺手重注册领域模块（别的测试可能 ``clear_registries()`` 过）。
    """
    loader.register_domain_modules(strict=False)
    monkeypatch.setattr(cli, "RUNTIME_FILE", tmp_path / "runtime" / "workbench.json")
    monkeypatch.setattr(cli, "DEFAULT_BASE_URL", "")
    monkeypatch.delenv(cli.ENV_BASE_URL, raising=False)
    monkeypatch.delenv("WORKBENCH_BASE", raising=False)


# --------------------------------------------------------------------------- #
# 素材与工具（帧构造走「与块同口径」的换算，不手写第二套字节序）
# --------------------------------------------------------------------------- #


def bcd_le(digits: str) -> bytes:
    """十进制整数串 → 645 数据域 BCD（**低字节在前**）：``"2201"`` → ``01 22``。"""
    padded = digits if len(digits) % 2 == 0 else "0" + digits
    return bytes(reversed(bytes.fromhex(padded)))


def read_reply_645(
    addr: str, di: str, value_digits: str, *, control: int = 0x91
) -> str:
    """构一帧 645 读数据应答：``68|地址域|68|91|L|DI 传输序 + 数值|CS|16``。

    数据域整段逐字节 +33H（645 数据域加密口径），数值为台架寄存器里那个数的 BCD。
    """
    plain = bytes(reversed(bytes.fromhex(di))) + bcd_le(value_digits)
    data = bytes((byte + 0x33) & 0xFF for byte in plain)
    body = b"\x68" + meter_addr_wire(addr) + bytes([0x68, control, len(data)]) + data
    return (body + bytes([sum(body) & 0xFF, 0x16])).hex().upper()


def read_addr_reply_645(addr: str, comm_addr: str = BENCH_ADDR) -> str:
    """构一帧 645 读通信地址（13H）应答：数据域 6 字节通信地址，**不加 33H**。"""
    data = bytes.fromhex(comm_addr)
    body = b"\x68" + meter_addr_wire(addr) + bytes([0x68, 0x93, len(data)]) + data
    return (body + bytes([sum(body) & 0xFF, 0x16])).hex().upper()


def up_frame_with(inner_hex: str, protocol: int = 2) -> str:
    """把内嵌 645 帧包成 02H-F1 上行帧（用真实门面构帧，只读、不连服务）。"""
    built = REAL_FRAMES.build(
        "1376.2",
        {"afn": "02", "fn": 1,
         "data": {"params": {"protocol": protocol, "payload": inner_hex}}},
    )
    assert built.get("ok"), built
    return str(built["frame_hex"]).upper()


class FakeSimcon:
    """假 simcon：按「请求内嵌 645 帧」回对应的 02H-F1 上行帧（无 HTTP、无台架）。"""

    name = "simcon"

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = {key.upper(): value.upper() for key, value in replies.items()}
        self.sent: list[str] = []

    def verify(self, steps, max_poll_seconds=None):  # noqa: ANN001 - 对标能力层签名
        payload = str(steps[0]["send"]["params"]["payload"]).upper()
        self.sent.append(payload)
        up = self.replies.get(payload)
        if not up:
            return {"ok": False, "error": "job_not_passed", "detail": "",
                    "steps": [{"result": "fail", "reason": "没有匹配的 02H-F1 上行帧"}]}
        return {"ok": True, "run_id": "run-fake",
                "steps": [{"index": 0, "name": "step", "matched": up, "result": "pass"}]}

    def frames(self, **_kwargs):  # noqa: ANN003
        return {"ok": False, "error": "unused", "entries": []}


class FakeSimeter:
    """假 simeter：``handle_hex`` 直接按请求回给定应答（不占串口）。"""

    name = "simeter"

    def __init__(self, reply_hex: str, note: dict | None = None) -> None:
        self.reply_hex = reply_hex
        self.note = dict(note or {})
        self.seen: list[str] = []

    def handle_hex(self, request_hex: str):  # noqa: ANN201
        self.seen.append(str(request_hex).upper())
        return {"ok": True, "reply_hex": self.reply_hex, "note": dict(self.note),
                "action": "645"}


def all_yes(_fact_id: str, _params: dict) -> str:
    """事实桩：任何前置事实都算满足（真实探测会连工作台，单测一律不查）。"""
    return "yes"


def block_specs() -> dict:
    return {spec.id: spec for spec in loader.load_blocks()}


def meter_read_case() -> CaseSpec:
    cases = {case.id: case for case in loader.load_cases()}
    assert "meter_read" in cases, "没找到用例声明 meter_read"
    return cases["meter_read"]


def payloads_for(case: CaseSpec) -> dict[str, str]:
    """s1/s2/s3 各自下发的内嵌 645 请求帧（用与块同口径的构帧器算，不手写 hex）。"""
    assert case.uses_plan
    addr13, err13 = build_645_request(
        REAL_FRAMES, addr=BENCH_ADDR, control=CONTROL_READ_ADDR
    )
    assert not err13, err13
    energy, err2 = build_645_read_frame(BENCH_ADDR, ENERGY_DI)
    voltage, err3 = build_645_read_frame(BENCH_ADDR, VOLTAGE_DI)
    assert not err2 and not err3, (err2, err3)
    return {"s1": addr13, "s2": energy, "s3": voltage}


def fake_bench(*, voltage_digits: str = "2201", energy_digits: str = "00123456"):
    """假台架：s1/s2/s3 各回一帧 02H-F1，s4 回表端脑应答。"""
    sent = payloads_for(meter_read_case())
    simcon = FakeSimcon({
        sent["s1"]: up_frame_with(read_addr_reply_645(BENCH_ADDR)),
        sent["s2"]: up_frame_with(read_reply_645(BENCH_ADDR, ENERGY_DI, energy_digits)),
        sent["s3"]: up_frame_with(read_reply_645(BENCH_ADDR, VOLTAGE_DI, voltage_digits)),
    })
    simeter = FakeSimeter(
        read_reply_645(BENCH_ADDR, VOLTAGE_DI, voltage_digits),
        {"func": "读数据", "di": VOLTAGE_DI, "value": float(voltage_digits) / 10,
         "mine": True},
    )
    return simcon, simeter, sent


def run_case(simcon, simeter) -> object:
    ctx = RunContext(
        run_id="run-meter-read",
        facts_ctx=FactsContext(probe=all_yes),
        block_specs=block_specs(),
        extras={"simcon": simcon, "simeter": simeter},
    )
    return execute_case(meter_read_case(), ctx)


# --------------------------------------------------------------------------- #
# 1. 声明：plan 4 步、id 唯一、引用的块/事实存在
# --------------------------------------------------------------------------- #


def test_case_declaration_loads_without_problems() -> None:
    report = loader.load_all(register_modules=True, require_registered=True)
    assert report.problems == [], "声明问题：\n" + "\n".join(report.problems)
    assert report.ok is True


def test_case_is_plan_only_with_four_unique_steps() -> None:
    case = meter_read_case()
    assert case.uses_plan is True, "必须用 plan 显式声明（同块两次实例化）"
    assert case.blocks == [], "blocks 是冗余的：plan 已完整表达执行序"
    assert [step.id for step in case.plan] == ["s1", "s2", "s3", "s4"]
    assert len({step.id for step in case.plan}) == 4, "step id 必须唯一"
    assert [step.block for step in case.plan] == [
        "meter.addr_probe", "meter.read_645", "meter.read_645", "meter.brain_local",
    ]
    assert all(step.on_fail == "stop" for step in case.plan), "on_fail 默认 stop"


def test_plan_references_only_known_blocks_and_facts() -> None:
    known_blocks = {spec.id for spec in loader.load_blocks()}
    known_facts = {spec.id for spec in loader.load_facts()}
    case = meter_read_case()
    assert [step.block for step in case.plan if step.block not in known_blocks] == []
    assert [fact for fact in case.needs if fact not in known_facts] == []
    assert case.needs == ["bench.simcon_online"]


def test_plan_carries_the_two_numeric_expectations() -> None:
    steps = {step.id: step for step in meter_read_case().plan}
    assert steps["s2"].params == {"di": ENERGY_DI, "expect_value": ENERGY_VALUE}
    assert steps["s3"].params == {"di": VOLTAGE_DI, "expect_value": VOLTAGE_VALUE}
    assert steps["s4"].params == {"di": VOLTAGE_DI}
    assert "expect_value" not in steps["s1"].params, "13H 只判地址，不判数值"


def test_steps_text_says_addr_probe_is_not_meter_reading() -> None:
    """13H 回本表地址 ≠ 抄表：文案里必须写明，避免又被当成「抄表通了」。"""
    case = meter_read_case()
    assert [step.no for step in case.steps] == list(range(1, len(case.steps) + 1))
    addr_step = next(step for step in case.steps if "读通信地址" in step.do)
    assert "13H" in addr_step.do
    assert "不是抄表" in addr_step.do, addr_step.do
    voltage_step = next(step for step in case.steps if VOLTAGE_DI in step.do)
    assert "A 相电压" in voltage_step.do and str(VOLTAGE_VALUE) in voltage_step.expect
    assert meter_read_case().tags.count("voltage") == 1, "tags 加 voltage 便于页面筛选"


def test_case_json_still_carries_no_frame_hex() -> None:
    """用例只做编排：DI / 期望值是参数，不是帧 hex（全局门禁的同口径补测）。"""
    raw = json.loads((DATA_DIR / "cases" / "meter_read.json").read_text(encoding="utf-8"))
    text = json.dumps(raw, ensure_ascii=False)
    assert "68123456789012681104" not in text
    assert all(isinstance(entry, dict) for entry in raw["plan"])


# --------------------------------------------------------------------------- #
# 2. 规划：同块两步、参数互不污染
# --------------------------------------------------------------------------- #


def test_planner_yields_two_read_645_steps_with_own_params() -> None:
    case = meter_read_case()
    specs = block_specs()
    planned = plan(case, FactsContext(probe=all_yes), block_specs=specs)
    assert [item.step_id for item in planned] == ["s1", "s2", "s3", "s4"]
    assert [item.block_id for item in planned] == [step.block for step in case.plan]
    assert all(item.runnable for item in planned)

    ctx = RunContext(run_id="r", block_specs=specs)
    read_spec = specs["meter.read_645"]
    s2 = effective_params(read_spec, planned[1], ctx)
    s3 = effective_params(read_spec, planned[2], ctx)
    assert (s2["di"], s2["expect_value"]) == (ENERGY_DI, ENERGY_VALUE)
    assert (s3["di"], s3["expect_value"]) == (VOLTAGE_DI, VOLTAGE_VALUE)
    # 同一块声明只写一份 di，两步各自覆盖，互不污染
    assert read_spec.action["params"]["di"] == ENERGY_DI


def test_step_params_override_does_not_leak_across_same_block_steps() -> None:
    """``ctx.step_params``（D3-2 第 3 层）只改指定步骤，另一步保持原样。"""
    specs = block_specs()
    planned = plan(meter_read_case(), FactsContext(probe=all_yes), block_specs=specs)
    ctx = RunContext(run_id="r", block_specs=specs)
    ctx.step_params["s3"] = {"di": "02020100", "expect_value": 220.2}
    read_spec = specs["meter.read_645"]
    s2 = effective_params(read_spec, planned[1], ctx)
    s3 = effective_params(read_spec, planned[2], ctx)
    assert (s2["di"], s2["expect_value"]) == (ENERGY_DI, ENERGY_VALUE)
    assert (s3["di"], s3["expect_value"]) == ("02020100", 220.2)
    # run 级 --params 仍是最高层，但它是按块广播（两步同时变），不用于「只改一步」
    ctx.block_params["meter.read_645"] = {"expect_value": 1}
    assert effective_params(read_spec, planned[1], ctx)["expect_value"] == 1
    assert effective_params(read_spec, planned[2], ctx)["expect_value"] == 1


# --------------------------------------------------------------------------- #
# 3. 执行（假能力，无台架）：真读数值的判据
# --------------------------------------------------------------------------- #


def test_full_case_passes_and_reads_both_numbers() -> None:
    simcon, simeter, sent = fake_bench()
    result = run_case(simcon, simeter)

    assert result.verdict == "pass", result.to_dict()
    assert [record.step_id for record in result.blocks] == ["s1", "s2", "s3", "s4"]
    assert [record.state for record in result.blocks] == [BLOCK_PASS] * 4
    # 三步真的发了三帧不同的内嵌 645 请求（13H / 11H 电能 / 11H 电压）
    assert simcon.sent == [sent["s1"], sent["s2"], sent["s3"]]
    assert simcon.sent[0] != simcon.sent[1] != simcon.sent[2]
    # 数值判据进入 detail（不再只说「地址对了」）
    assert "1234.56 kWh" in result.blocks[1].detail
    assert "220.1 V" in result.blocks[2].detail
    assert "与期望值 220.1 一致" in result.blocks[2].detail
    # 表端脑对照用的是同一个 DI（A 相电压），请求帧与 s3 逐字节一致
    assert simeter.seen == [sent["s3"]]


def test_voltage_value_mismatch_is_fail_not_pass() -> None:
    simcon, simeter, _sent = fake_bench(voltage_digits="2192")  # 219.2 V ≠ 220.1 V
    result = run_case(simcon, simeter)
    assert result.verdict != "pass"
    s3 = result.blocks[2]
    assert s3.state == BLOCK_FAIL, s3.detail
    assert "数值不匹配" in s3.detail and "220.1" in s3.detail and "219.2" in s3.detail


def test_voltage_di_echo_mismatch_is_fail() -> None:
    """请求 A 相电压却回了正向有功总电能（DI 回显不对）→ fail，不能当抄表成功。"""
    simcon, simeter, sent = fake_bench()
    simcon.replies[sent["s3"]] = up_frame_with(
        read_reply_645(BENCH_ADDR, ENERGY_DI, "00123456")
    )
    result = run_case(simcon, simeter)
    s3 = result.blocks[2]
    assert s3.state == BLOCK_FAIL, s3.detail
    assert "未回显 DI" in s3.detail and VOLTAGE_DI in s3.detail


def test_empty_data_unit_on_uplink_is_fail_with_reason() -> None:
    """真机「上行数据单元为空」（645 应答 L=0）时的现有口径：**fail**，原因点名未回显 DI。"""
    body = bytes([0x68]) + meter_addr_wire(BENCH_ADDR) + bytes([0x68, 0x91, 0x00])
    empty_reply = (body + bytes([sum(body) & 0xFF, 0x16])).hex().upper()
    verdict = judge_read_645(
        up_frame_with(empty_reply), expect_di=VOLTAGE_DI, expect_value=VOLTAGE_VALUE
    )
    assert verdict.state == BLOCK_FAIL, verdict.detail
    assert "未回显 DI" in verdict.detail
    assert verdict.block_state_of() == BLOCK_FAIL


# --------------------------------------------------------------------------- #
# 4. 645 帧里 DI 的字节序（锁死，防以后改错）
# --------------------------------------------------------------------------- #


def test_di_transport_order_is_locked() -> None:
    """DI 显示序 → 传输序 = **4 字节整体反序 + 逐字节 33H**。

    ``00010000`` → ``33 33 34 33``（台架实测帧里就是这个）
    ``02010100`` → ``33 34 34 35``
    """
    assert di_echo_hex(ENERGY_DI) == "33333433"
    assert di_echo_hex(VOLTAGE_DI) == "33343435"
    # 反向核对：解码器把传输序还原成显示序（parser_lib 645 适配器口径）
    assert di_echo_hex(VOLTAGE_DI) != VOLTAGE_DI, "传输序与显示序必须不同，否则没做换算"


def test_read_request_frames_lock_di_bytes_and_cs() -> None:
    """11H 读数据请求帧逐字节锁死（台架实测：地址域不反转、DI 传输序、CS 合法）。"""
    energy, err = build_645_read_frame(BENCH_ADDR, ENERGY_DI)
    voltage, err2 = build_645_read_frame(BENCH_ADDR, VOLTAGE_DI)
    assert (err, err2) == ("", "")
    assert energy == "68123456789012681104333334336816"
    assert voltage == "68123456789012681104333434356B16"  # DI 段 33 34 34 35
    assert voltage[20:28] == "33343435"
    for frame in (energy, voltage):
        raw = bytes.fromhex(frame)
        assert raw[0] == 0x68 and raw[7] == 0x68 and raw[-1] == 0x16
        assert raw[-2] == sum(raw[:-2]) % 256, "CS 必须是整帧除尾 2 字节求和低 8 位"


def test_expect_value_comparison_is_float_with_tolerance() -> None:
    """``expect_value`` 口径：float 比较 + 相对容差（1e-6），**不是字符串精确相等**。

    ``220.1`` 这种带小数的值不会因解析/浮点表示判错（220.10 / 220.1 都算一致），
    差 0.1 V 就必须判 fail。
    """
    for expect, ok in ((220.1, True), ("220.1", True), (220.10, True), (220.2, False),
                       (220, False)):
        verdict = judge_read_645(
            up_frame_with(read_reply_645(BENCH_ADDR, VOLTAGE_DI, "2201")),
            expect_di=VOLTAGE_DI,
            expect_value=expect,
        )
        assert (verdict.state == BLOCK_PASS) is ok, (expect, verdict.state, verdict.detail)
    assert judge_read_645(
        up_frame_with(read_reply_645(BENCH_ADDR, ENERGY_DI, "00123456")),
        expect_di=ENERGY_DI,
        expect_value=1234.56,
    ).state == BLOCK_PASS
