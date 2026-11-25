"""抄表两条用例（REQS-0042 v9 **D9-1 / D9-2**）：日常主线与首次组网**拆开**。

真机实测驱动（2026-11-23）：**复位 STA 后链路恢复要十几分钟量级**（16:20 复位 → 16:45
才恢复到 2.5s；17:18 复位 → 30 分钟后表口仍静默），而读取本身只要 2.3–2.6s。且
``nwk.topology`` 在复位后**立刻就能通过**（CCO 节点计数残留）——**「组网判定通过」≠
「链路可用」**。所以「复位」不能塞在日常回归里，一条用例也承担不了两种节奏：

=============================  =============================================
用例                           结构
=============================  =============================================
``meter_daily``（**不复位**）   6 步：档案 → 查档 → 组网 → 绑表 → 抄表（电能/电压）
``meter_first_join``（**含复位**） 7 步：复位 → 档案 → 查档 → 组网 → 绑表 → 抄表

REQS-0044（2026-11-24）重构：①查档步（archive.query）为必备步（§8.1 第1条，6/7 步）；
②判据归位——抄表类块只报观测，「有值且可解」由步骤 expect 判，不再比对 1234.56/220.1
真值（§8.1 第4条）；③等待预算写死在用例（§8.1 第3条，组网 300s 也显式给）。
=============================  =============================================

用户裁决（v9）：**拆成两条用例**；D9-2 方案映射 ``meter_smoke_min=[bench_ready,
meter_daily]``、``smoke_meter=[bench_ready, meter_first_join]``。旧的单条 ``meter_read``
（复位 + 档案 + 组网 + 绑表 + 抄表 5 动作 6 步）已删除，语义被上面两条完整覆盖。

**关键断言**（本文件的核心守卫）：``meter_daily`` **不得**含 ``reset.sta``；
``meter_first_join`` **必须**含 ``reset.sta`` 且复位排在第一步。

覆盖：

1. 两条用例均可加载：``plan`` 5 步 / 6 步、step id 唯一、引用的块/事实都存在
   （``problems == []``）；
2. 块序列与顺序被钉死；``on_fail`` 全为 ``stop``；``needs`` 分别只含
   ``bench.simcon_online``（daily）/ 再加 ``bench.sta_online``（first_join）；
3. 组网步骤**不得**在用例里改 ``wait_s``（默认 300s 由块给，压小会把「还在入网」判成
   假 FAIL）；``meter.addr_probe`` / ``meter.read_645`` 的 ``wait_s`` 延展口**预留但不写进
   plan**（默认 0 = 只探一次）；
4. 同一块 ``meter.read_645`` 两步、DI 不同 → 规划出两个 step，参数互不污染；
5. 645 帧里 DI 的**字节序**用单测锁死（显示序 → 传输序 +33H，``02010100`` → ``33343435``）；
6. 期望值比较口径（浮点 + 相对容差）与「上行数据单元为空」的行为。

**无台架**：不连 8790、不开串口、单测零真实等待。复位 / 档案 / 组网三块的**块级判据**
分别由 ``blocks/test_fix_reset.py``、``blocks/test_archive_nwk.py`` 覆盖；本文件执行段把
它们换成桩（``_stub_non_meter_blocks``），只验证用例编排与表侧三个动作的真判据。
"""

from __future__ import annotations

import json
import pathlib

import pytest

import case_runner.cli as cli
import case_runner.core.executor as executor
from case_runner.blocks import loader
from case_runner.blocks.common import block_action_params, meter_addr_wire
from case_runner.blocks.meter import (
    CONTROL_READ_ADDR,
    build_645_read_frame,
    build_645_request,
    di_echo_hex,
    judge_read_645,
)
from case_runner.capabilities.frames import FramesCapability
from case_runner.core.executor import RunContext, effective_params, execute_case
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_UNKNOWN,
    CaseSpec,
)
from case_runner.core.planner import FactsContext, plan

# --------------------------------------------------------------------------- #
# 两条用例的定稿结构（D9-1）
# --------------------------------------------------------------------------- #

DAILY = "meter_daily"
FIRST_JOIN = "meter_first_join"

#: ``meter_daily``：**不复位**的日常主线（0044 §8.1 第1条：查档步必备 → 6 步）
DAILY_STEP_IDS = ["s1", "s1b", "s2", "s3", "s4", "s5"]
DAILY_BLOCK_ORDER = [
    "archive.add",
    "archive.query",
    "nwk.topology",
    "meter.addr_probe",
    "meter.read_645",
    "meter.read_645",
]
DAILY_NEEDS = ["bench.simcon_online"]
DAILY_TAGS = ["meter", "645", "voltage", "daily", "smoke"]

#: ``meter_first_join``：**含复位**的首次组网（含查档步 → 7 步）
JOIN_STEP_IDS = ["s1", "s2", "s2b", "s3", "s4", "s5", "s6"]
JOIN_BLOCK_ORDER = [
    "reset.sta",
    "archive.add",
    "archive.query",
    "nwk.topology",
    "meter.addr_probe",
    "meter.read_645",
    "meter.read_645",
]
JOIN_NEEDS = ["bench.sta_online", "bench.simcon_online"]
JOIN_TAGS = ["meter", "645", "first-join"]

#: 两条用例共用的两个数值（台架寄存器既有事实）
ENERGY_DI = "00010000"
ENERGY_VALUE = 1234.56
VOLTAGE_DI = "02010100"
VOLTAGE_VALUE = 220.1

#: 台架事实：模拟电表脑地址（``case_defaults.meter_addr`` 同值；645 帧地址域不反转）
BENCH_ADDR = "123456789012"

#: 用例里**显式钉死**的读等待上限（实测单数据项往返 2.3–2.6s → 15s = 6 倍余量；
#: 显式值存在时块不再查 03H-F7，避免按 t+5s 空等 95s）
READ_EXPECT_TIMEOUT = 15

#: 块级判据另有专项测试覆盖的四个块；执行段用桩替换（只测编排 + 表侧判据）
#: 0044：archive.query 也入桩 —— 查档步已从建档块的双判据拆出，成为独立步骤，
#: 桩回「在档=True」的完整观测（真块判据由 blocks/test_archive_nwk.py 覆盖）
STUBBED_BLOCKS = ("reset.sta", "archive.add", "archive.query", "nwk.topology")

#: ``nwk.topology`` 块声明里的等待上限默认值（用例不得覆盖）
TOPOLOGY_WAIT_S = 300.0

#: 表侧三步的参数覆盖（按块而不是按 step id，两条用例共用：同一个块同一份参数）
#: 三步读的等待预算（真机实测驱动）：
#: - ``expect_timeout`` = 15s（单数据项往返 2.3–2.6s 的 6 倍余量；显式值存在时不查 03H-F7）
#: - ``wait_s`` = 绑表 60s / 两次读 30s（吸收冷启动瞬态；``meter_first_join`` 绑表 180s）
READ_PARAMS = {
    # 0044 §8.1 第4条：``expect_value`` 判据搬进步骤期望（默认只验「有值且可解」），
    # 块参数不再带 expect_value；显式比对由 Procedure 的 expect.eq 声明
    "meter.addr_probe": {"expect_timeout": READ_EXPECT_TIMEOUT, "wait_s": 60},
    "meter.read_645": [
        {"di": ENERGY_DI, "expect_timeout": READ_EXPECT_TIMEOUT, "wait_s": 30},
        {"di": VOLTAGE_DI, "expect_timeout": READ_EXPECT_TIMEOUT, "wait_s": 30},
    ],
}

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


@pytest.fixture
def stub_non_meter_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """把复位 / 档案 / 组网三块换成 pass 桩（块级判据见各自 ``blocks/test_*.py``）。

    桩打在 ``executor.get_block``（执行器执行前才解析执行体），因此用例声明、规划、
    参数优先级、表侧三个动作仍是**真**实现。``meter_daily`` 不含 ``reset.sta``，桩对它
    无害（不会被调用）。
    """

    real_get_block = executor.get_block

    def fake_get_block(block_id: str):
        if block_id not in STUBBED_BLOCKS:
            return real_get_block(block_id)

        def run(params, ctx=None):  # noqa: ANN001 - 对标块执行体签名
            from case_runner.core.expect import Observation

            if block_id == "archive.query":
                # 查档步（0044 拆出的独立步骤）：回「在档=True」的完整观测，
                # 让步骤期望（eq 在档=true）走求值器判 pass
                return executor.BlockResult(
                    state=BLOCK_UNKNOWN,
                    detail="夹具桩：档案表含目标地址（观测完整）",
                    observation=Observation(
                        values={"总数": 1, "已查条数": 1, "查询地址": "010000000000",
                                "在档": True},
                        complete=True,
                        reason_code="",
                        detail="夹具桩：档案表含目标地址（观测完整）",
                    ),
                )
            return executor.BlockResult(
                state=BLOCK_PASS,
                detail="夹具桩：%s 直接 pass（块级判据由 blocks/test_*.py 覆盖）" % block_id,
            )

        return run

    monkeypatch.setattr(executor, "get_block", fake_get_block)


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

    #: 单测注入虚拟时钟/睡眠：``meter`` 块的 ``poll_hooks`` 会优先读取能力对象上的
    #: ``clock`` / ``sleeper`` —— 因此带 ``wait_s`` 的用例**零真实等待**（实测省 ~180s）。
    clock = staticmethod(lambda: 0.0)
    sleeper = staticmethod(lambda seconds: None)

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = {key.upper(): value.upper() for key, value in replies.items()}
        self.sent: list[str] = []
        #: 收到过几次「03H-F7 取 t」前置查询（用例显式钉 expect_timeout 后应当为 0）
        self.timeout_queries = 0

    def verify(self, steps, max_poll_seconds=None):  # noqa: ANN001 - 对标能力层签名
        send = steps[0]["send"]
        if str(send.get("afn", "")).upper() == "03":
            # 前置「03H-F7 取 t」查询（REQS-0042 D5-2）：本夹具不模拟该应答，
            # 按「模块不支持该帧」失败 → 块回落到 60s（不污染 ``sent`` 的三个读步）
            self.timeout_queries += 1
            return {"ok": False, "error": "job_not_passed", "detail": "夹具不支持 03H-F7",
                    "steps": [{"result": "fail", "reason": "夹具不支持 03H-F7"}]}
        payload = str(send["params"]["payload"]).upper()
        self.sent.append(payload)
        up = self.replies.get(payload)
        if not up:
            return {"ok": False, "error": "job_not_passed", "detail": "",
                    "steps": [{"result": "fail", "reason": "没有匹配的 02H-F1 上行帧"}]}
        return {"ok": True, "run_id": "run-fake",
                "steps": [{"index": 0, "name": "step", "matched": up, "result": "pass"}]}

    def frames(self, **_kwargs):  # noqa: ANN003
        return {"ok": False, "error": "unused", "entries": []}


def all_yes(_fact_id: str, _params: dict) -> str:
    """事实桩：任何前置事实都算满足（真实探测会连工作台，单测一律不查）。"""
    return "yes"


def block_specs() -> dict:
    return {spec.id: spec for spec in loader.load_blocks()}


def load_case(case_id: str) -> CaseSpec:
    cases = {case.id: case for case in loader.load_cases()}
    assert case_id in cases, "没找到用例声明 %s（现有：%s）" % (case_id, sorted(cases))
    return cases[case_id]


def structure(case_id: str) -> tuple[list[str], list[str]]:
    """``(step ids, block order)``：两条用例的结构对照表。"""
    return (
        (DAILY_STEP_IDS, DAILY_BLOCK_ORDER)
        if case_id == DAILY
        else (JOIN_STEP_IDS, JOIN_BLOCK_ORDER)
    )


def read_slice(case_id: str) -> slice:
    """两个抄表步（电能 / 电压）在 plan 里的下标区间（查档步插入后 +1）。"""
    offset = 0 if case_id == DAILY else 1  # first_join 的 s1 是复位
    return slice(offset + 4, offset + 6)


def payloads_for(_case_id: str = DAILY) -> dict[str, str]:
    """三个读步各自下发的内嵌 645 请求帧（用与块同口径的构帧器算，不手写 hex）。

    两条用例的表侧三步完全相同（同一块、同一份参数），所以只算一份。
    """
    addr13, err13 = build_645_request(
        REAL_FRAMES, addr=BENCH_ADDR, control=CONTROL_READ_ADDR
    )
    assert not err13, err13
    energy, err2 = build_645_read_frame(BENCH_ADDR, ENERGY_DI)
    voltage, err3 = build_645_read_frame(BENCH_ADDR, VOLTAGE_DI)
    assert not err2 and not err3, (err2, err3)
    return {"addr": addr13, "energy": energy, "voltage": voltage}


def fake_bench(*, voltage_digits: str = "2201", energy_digits: str = "00123456"):
    """假台架：三个读步各回一帧 02H-F1（复位/档案/组网是桩，不碰能力层）。"""
    sent = payloads_for()
    simcon = FakeSimcon({
        sent["addr"]: up_frame_with(read_addr_reply_645(BENCH_ADDR)),
        sent["energy"]: up_frame_with(read_reply_645(BENCH_ADDR, ENERGY_DI, energy_digits)),
        sent["voltage"]: up_frame_with(read_reply_645(BENCH_ADDR, VOLTAGE_DI, voltage_digits)),
    })
    return simcon, sent


def run_case(case_id: str, simcon) -> object:
    ctx = RunContext(
        run_id="run-%s" % case_id,
        facts_ctx=FactsContext(probe=all_yes),
        block_specs=block_specs(),
        extras={"simcon": simcon},
    )
    return execute_case(load_case(case_id), ctx)


# --------------------------------------------------------------------------- #
# 1. 声明：两条用例都 5 步 / 6 步、id 唯一、引用的块/事实存在、没有搜表/重启路由
# --------------------------------------------------------------------------- #


def test_declarations_load_without_problems() -> None:
    report = loader.load_all(register_modules=True, require_registered=True)
    assert report.problems == [], "声明问题：\n" + "\n".join(report.problems)
    assert report.ok is True


def test_meter_read_case_is_gone() -> None:
    """D9-1：旧的单条 ``meter_read`` 已删除，语义被两条新用例完整覆盖。"""
    cases = {case.id for case in loader.load_cases()}
    assert "meter_read" not in cases, "meter_read 已被 D9-1 拆成两条用例，不该再存在"
    assert {DAILY, FIRST_JOIN} <= cases


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_case_is_plan_only_with_declared_step_order(case_id: str) -> None:
    case = load_case(case_id)
    step_ids, block_order = structure(case_id)
    assert case.uses_plan is True, "必须用 plan 显式声明（同块两次实例化）"
    assert case.blocks == [], "blocks 是冗余的：plan 已完整表达执行序"
    assert [step.id for step in case.plan] == step_ids
    assert len({step.id for step in case.plan}) == len(step_ids), "step id 必须唯一"
    assert [step.block for step in case.plan] == block_order, (
        "%s 的块序列被改了：期望 %s" % (case_id, block_order)
    )
    assert all(step.on_fail == "stop" for step in case.plan), "每步 on_fail 必须显式 stop"


def test_meter_daily_has_no_reset_step() -> None:
    """★ D9-1 核心守卫：日常主线**不复位** —— 复位是物理副作用，且复位后链路要十几分钟才好。"""
    case = load_case(DAILY)
    used = {step.block for step in case.plan} | set(case.blocks)
    assert "reset.sta" not in used, (
        "meter_daily 不得含 reset.sta：日常回归不复位（真机实测复位后链路恢复要十几分钟量级，"
        "而读取本身只要 2.3–2.6s）；复位只属于 meter_first_join"
    )
    assert case.needs == DAILY_NEEDS, (
        "不复位就不该依赖 bench.sta_online：needs 只留 bench.simcon_online，实际 %s" % case.needs
    )
    assert "不复位" in case.title and "日常" in case.title, case.title


def test_meter_first_join_starts_with_reset() -> None:
    """★ D9-1 核心守卫：首次组网**必须复位**，且复位排在第一步（先复位再建档组网）。"""
    case = load_case(FIRST_JOIN)
    used = {step.block for step in case.plan} | set(case.blocks)
    assert "reset.sta" in used, (
        "meter_first_join 必须含 reset.sta：用户原话「第一次组网需要复位 sta。之后就按需复位。」"
    )
    assert case.plan[0].block == "reset.sta", (
        "复位必须是第一步：先复位再建档组网，才能避免把复位打断造成的失败算到档案/组网头上"
    )
    assert case.needs == JOIN_NEEDS, (
        "复位要 bench.sta_online，其余动作要 bench.simcon_online，实际 %s" % case.needs
    )
    assert "含复位" in case.title and "首次组网" in case.title, case.title


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_plan_has_no_search_meter_or_route_restart(case_id: str) -> None:
    """D6-2：搜表（11H-F5）与重启路由（12H-F1）不是入网手段，禁止进用例。"""
    case = load_case(case_id)
    used = {step.block for step in case.plan} | set(case.blocks)
    assert "nwk.active_registration" not in used, "搜表块禁止进用例（用户原话「不要多出什么搜表之类的」）"
    assert "nwk.route_restart" not in used, "重启路由是可选路由控制，不是入网手段"
    assert "meter.brain_local" not in used, "表端脑本地对照已移出：用例只做抄表主线"
    _, block_order = structure(case_id)
    assert used == set(block_order), "除了主线动作的块，不许出现别的块：%s" % sorted(used)


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_plan_references_only_known_blocks_and_facts(case_id: str) -> None:
    known_blocks = {spec.id for spec in loader.load_blocks()}
    known_facts = {spec.id for spec in loader.load_facts()}
    case = load_case(case_id)
    assert [step.block for step in case.plan if step.block not in known_blocks] == []
    assert [fact for fact in case.needs if fact not in known_facts] == []

    # 每个块的块级 needs 由「用例 needs ∪ 前序步骤 provides」覆盖（REQS-0044 AC-5
    # 事实链：archive.written / nwk.joined / sta.addr_obtained 是运行内事实，由前序步
    # 回写，故意不进用例 needs——它们是链式结论，不是台架预检事实）
    specs = block_specs()
    declared = set(case.needs)
    provided_so_far: set[str] = set()
    for step in case.plan:
        missing = set(specs[step.block].needs) - declared - provided_so_far
        assert missing == set(), (
            f"步骤 {step.id} 的块前置事实 {sorted(missing)} 既不在用例 needs、"
            "也没有前序步骤 provides（事实链断裂）"
        )
        provided_so_far.update(specs[step.block].provides)


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_plan_carries_the_step_expects_and_params(case_id: str) -> None:
    """判据归步骤（REQS-0044 §4.2）：抄表类步骤都声明 expect；参数口径沿用 0042 D8-3。

    覆盖行为说明（D7-1）：旧断言「plan 带 expect_value=1234.56/220.1」已删除 ——
    0044 §8.1 第4条改为「默认只验有值且可解，显式给期望值才比对」，期望值不再进块参数，
    改为校验步骤 expect 的声明式判据（CS合法/DI回显/异常应答/数值 has_value）。
    """
    step_ids, _ = structure(case_id)
    steps = {step.id: step for step in load_case(case_id).plan}
    add_id, query_id = ("s1", "s1b") if case_id == DAILY else ("s2", "s2b")
    topo_id = "s2" if case_id == DAILY else "s3"
    addr_id = "s3" if case_id == DAILY else "s4"
    energy_id = "s4" if case_id == DAILY else "s5"
    voltage_id = "s5" if case_id == DAILY else "s6"

    # 步骤判据（声明式 expect）：建档=写入确认；查档=在档；组网=ge_field 门槛；
    # 绑表=地址一致；抄表=CS合法 + 非异常应答 + DI回显 + 数值有值（不比真值）
    ops_of = lambda sid: [item["op"] for item in steps[sid].expect]  # noqa: E731
    fields_of = lambda sid: [item.get("field") for item in steps[sid].expect]  # noqa: E731
    assert "eq" in ops_of(add_id) and "写入确认" in fields_of(add_id)
    assert "在档" in fields_of(query_id), "查档步必须声明「在档」期望（AC-2 的「含」方向）"
    assert "ge_field" in ops_of(topo_id) and "在网从节点数" in fields_of(topo_id)
    assert "地址一致" in fields_of(addr_id)
    for sid in (energy_id, voltage_id):
        expect_fields = fields_of(sid)
        for field in ("CS合法", "异常应答", "DI回显", "数值"):
            assert field in expect_fields, "%s 缺期望字段 %s" % (sid, field)
        has_value = [i for i in steps[sid].expect if i["op"] == "has_value"]
        assert has_value and has_value[0]["field"] == "数值", (
            "%s 默认只验「有值且可解」（0044 §8.1 第4条），不得写死数值真值" % sid
        )
        assert "eq" not in ops_of(sid) or all(
            item.get("field") != "数值" for item in steps[sid].expect if item["op"] == "eq"
        ), "%s 不得把数值真值写进默认期望" % sid

    # 参数口径：数值/超时必须一致；``wait_s`` 因用例而异（另有用例校验）
    def subset(actual: dict, expected: dict) -> None:
        for key, value in expected.items():
            if key == "wait_s":
                continue
            assert actual.get(key) == value, "%s 期望 %r 实际 %r" % (key, value, actual.get(key))

    subset(steps[addr_id].params, READ_PARAMS["meter.addr_probe"])
    assert "expect_value" not in steps[addr_id].params, "13H 只判地址，不判数值"
    subset(steps[energy_id].params, READ_PARAMS["meter.read_645"][0])
    subset(steps[voltage_id].params, READ_PARAMS["meter.read_645"][1])
    # 查档步给 meter_addr（null → 加载期解析成默认地址）；组网步只写等待预算
    assert steps[query_id].params.get("meter_addr"), "查档步必须带表地址（null 取默认）"
    assert set(steps[topo_id].params) == {"wait_s"}, (
        "组网步只允许显式等待预算，不得夹带其它参数覆盖"
    )


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_plan_pins_read_wait_budget_and_skips_03h_f7(case_id: str) -> None:
    """三个读步显式 ``expect_timeout``：预算可控，且**不再走 03H-F7 取 t**（D5-2 的回落口）。

    为什么必须钉死：台架 03H-F7 若回 t=90s，每步等待会变成 t+5s=95s，三步就能吃掉
    285s，叠上组网 300s 会顶穿整轮 600s 上限。显式值存在时
    ``resolve_expect_timeout`` 直接返回（**不查 03H-F7**），口径 = 实测往返 2.3–2.6s 的
    6 倍余量。组网（唯一等待型判据）仍保持块默认 300s，不在这里压。
    """
    step_ids, _ = structure(case_id)
    steps = {step.id: step for step in load_case(case_id).plan}
    read_ids = step_ids[-3:]
    for step_id in read_ids:
        assert steps[step_id].params.get("expect_timeout") == READ_EXPECT_TIMEOUT, step_id
    for step_id in step_ids[:-3]:
        assert "expect_timeout" not in steps[step_id].params, (
            f"{step_id} 不该改等待上限：复位/档案/组网用块声明默认"
        )

    specs = block_specs()
    ctx = RunContext(run_id="r", block_specs=specs)
    planned = plan(load_case(case_id), FactsContext(probe=all_yes), block_specs=specs)
    effective = effective_params(specs["meter.read_645"], planned[-1], ctx)
    assert float(effective["expect_timeout"]) == float(READ_EXPECT_TIMEOUT)


#: 等待预算（2026-11-23 真机实测：复位后链路恢复≈10 分钟；冷启动存在瞬态）
EXPECTED_WAIT_S = {
    # 0044 §8.1 第3条：等待预算写死在用例——组网 300s 也显式给（旧口径「用声明默认」废除）
    DAILY: {"s2": 300, "s3": 60, "s4": 30, "s5": 30},
    FIRST_JOIN: {"s3": 300, "s4": 180, "s5": 30, "s6": 30},
}


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_plan_writes_wait_budget_to_absorb_cold_start(case_id: str) -> None:
    """读步显式带 ``wait_s``：吸收「桥刚起 / CCO 忙」这类冷启动瞬态。

    为什么现在要写（推翻此前的"延展口先预留"）：真机实测 23% 的冷启动首探会回空帧，
    而重试一次即通过；不给预算就必须人工重跑，违反零参与。
    预算上限仍受 PLAN §0.4 约束：整轮最坏 < 600s。
    """
    expected = EXPECTED_WAIT_S[case_id]
    got = {step.id: (step.params or {}).get("wait_s", 0) for step in load_case(case_id).plan}
    for step_id, want in expected.items():
        assert got.get(step_id) == want, (
            "%s 的 %s 等待预算应为 %ss，实际 %r" % (case_id, step_id, want, got.get(step_id))
        )
    # 非等待型步（复位/建档/查档）一律不写 wait_s；等待型步必须显式给（缺预算视为缺判据）
    for step_id, value in got.items():
        if step_id not in expected:
            assert value == 0, "%s 的 %s 不该写 wait_s（非等待型步骤）" % (case_id, step_id)


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_plan_does_not_shrink_nwk_wait_budget(case_id: str) -> None:
    """等待预算硬规则（0044 §8.1 第3条）：组网预算**写死在用例**且不得压小。

    覆盖行为说明（D7-1）：旧断言「用例不得写 wait_s、保持块默认」已被 v2 拍板 #3
    取代 —— 现在要求用例**显式给** 300s（缺预算视为缺判据，loader 门禁拦截），
    数值仍不得低于块默认（压小会把「还在入网」判成「明确不足」）。
    """
    specs = block_specs()
    ctx = RunContext(run_id="r", block_specs=specs)
    planned = plan(load_case(case_id), FactsContext(probe=all_yes), block_specs=specs)
    topology = [item for item in planned if item.block_id == "nwk.topology"]
    assert len(topology) == 1, "组网判据必须恰好一个（10H-F21），实际 %d" % len(topology)
    effective = effective_params(specs["nwk.topology"], topology[0], ctx)
    assert float(block_action_params(effective)["wait_s"]) == TOPOLOGY_WAIT_S, (
        "nwk.topology 的 wait_s 必须显式 300s（写死在用例，0044 §8.1 第3条）："
        "台架实测一轮入网约 240s，压小会把「还在入网」判成「明确不足」"
    )
    assert "poll_interval_s" not in effective
    # 其余等待型块一次都不该出现在用例里（多块默认值叠加会顶穿整轮 600s 上限）
    waiting_blocks = {"nwk.topology", "nwk.route_status", "nwk.nodes_online"}
    used_waiting = [step.block_id for step in planned if step.block_id in waiting_blocks]
    assert used_waiting == ["nwk.topology"], (
        "一个用例只允许一个等待型判据，实际出现：%s" % used_waiting
    )


def test_steps_text_documents_six_steps_for_daily() -> None:
    """``meter_daily`` 人读说明 6 步（0044 §8.1 第1条：含查档步）；标题写明「不复位，日常」。"""
    case = load_case(DAILY)
    assert [step.no for step in case.steps] == list(range(1, 7))
    archive, query, nwk, addr, energy, voltage = case.steps

    assert "11H-F1" in archive.do and "写入确认" in archive.expect
    assert "不复位" in archive.do, "第 1 步就要交代本流程不复位（与首次组网区分）"
    assert "10H-F2" in query.do and "唯一出口" in query.do, (
        "查档步是「回了确认却没落地」的唯一出口（0044 §8.1 第1条必备步）"
    )
    assert "10H-F21" in nwk.do and "98%" in nwk.expect, (
        "组网判据要照 KB 写：在网从节点数 ≥ ceil(档案数 × 98%)"
    )
    assert "13H" in addr.do and "不是抄表" in addr.do, (
        "13H 回本表地址 ≠ 抄表：文案里必须写明，避免又被当成「抄表通了」"
    )
    assert "正向有功总电能" in energy.do and "数值可解" in energy.expect
    assert "不比对数值真值" in energy.expect, "默认判据必须写明不校验数值真值（0044 §8.1 第4条）"
    assert "A 相电压" in voltage.do and "数值可解" in voltage.expect
    # 等待预算写死在人读说明里（0044 §8.1 第3条）
    for step, budget in ((nwk, "300 秒"), (addr, "60s"), (energy, "30s"), (voltage, "30s")):
        assert budget in step.do, (step.no, step.do)

    assert set(DAILY_TAGS) <= set(case.tags), case.tags
    for action in ("档案", "组网", "绑表", "抄表"):
        assert action in case.title, "标题要能一眼看出动作：缺 %r" % action


def test_steps_text_documents_seven_steps_for_first_join() -> None:
    """``meter_first_join`` 人读说明 7 步（含复位 + 查档）；复位步写明「复位成立 ≠ 链路已恢复」。"""
    case = load_case(FIRST_JOIN)
    assert [step.no for step in case.steps] == list(range(1, 8))
    reset, archive, query, nwk, addr, energy, voltage = case.steps

    assert "复位" in reset.do and "重启指纹" in reset.expect
    assert "十几分钟" in reset.do, (
        "复位步要写明真机教训：复位后链路恢复要十几分钟量级（D9-1 的实测依据）"
    )
    assert "写入确认" in archive.expect
    assert "10H-F2" in query.do, "查档步必备（0044 §8.1 第1条）"
    assert "10H-F21" in nwk.do and "残留" in nwk.expect, (
        "组网步必须写明「拓扑立刻通过 ≠ 链路可用」（CCO 节点计数残留）"
    )
    assert "13H" in addr.do and "180s" in addr.do, (
        "绑表步等待预算 180s 写死在本流程（复位后链路恢复分钟级）"
    )
    assert "正向有功总电能" in energy.do and "数值可解" in energy.expect
    assert "A 相电压" in voltage.do and "数值可解" in voltage.expect

    assert set(JOIN_TAGS) <= set(case.tags), case.tags
    for action in ("复位", "档案", "组网", "绑表", "抄表"):
        assert action in case.title, "标题要能一眼看出动作：缺 %r" % action


@pytest.mark.parametrize("file_name", ["meter_daily.json", "meter_first_join.json"])
def test_case_json_still_carries_no_frame_hex(file_name: str) -> None:
    """用例只做编排：DI / 期望值是参数，不是帧 hex（全局门禁的同口径补测）。"""
    raw = json.loads((DATA_DIR / "cases" / file_name).read_text(encoding="utf-8"))
    text = json.dumps(raw, ensure_ascii=False)
    assert "68123456789012681104" not in text
    assert all(isinstance(entry, dict) for entry in raw["plan"])


# --------------------------------------------------------------------------- #
# 2. 规划：步序、同块两步参数互不污染
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_planner_yields_steps_in_declared_order(case_id: str) -> None:
    step_ids, block_order = structure(case_id)
    specs = block_specs()
    planned = plan(load_case(case_id), FactsContext(probe=all_yes), block_specs=specs)
    assert [item.step_id for item in planned] == step_ids
    assert [item.block_id for item in planned] == block_order
    assert all(item.runnable for item in planned)


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_planner_yields_two_read_645_steps_with_own_params(case_id: str) -> None:
    specs = block_specs()
    planned = plan(load_case(case_id), FactsContext(probe=all_yes), block_specs=specs)

    ctx = RunContext(run_id="r", block_specs=specs)
    read_spec = specs["meter.read_645"]
    energy, voltage = planned[read_slice(case_id)]
    s_energy = effective_params(read_spec, energy, ctx)
    s_voltage = effective_params(read_spec, voltage, ctx)
    # 0044 §8.1 第4条：期望值不再进块参数（判据在步骤 expect），只按 DI 区分两步
    assert (s_energy["di"], s_voltage["di"]) == (ENERGY_DI, VOLTAGE_DI)
    # 同一块声明只写一份 di，两步各自覆盖，互不污染
    assert read_spec.action["params"]["di"] == ENERGY_DI


def test_step_params_override_does_not_leak_across_same_block_steps() -> None:
    """``ctx.step_params``（D3-2 第 3 层）只改指定步骤，另一步保持原样。"""
    specs = block_specs()
    planned = plan(load_case(DAILY), FactsContext(probe=all_yes), block_specs=specs)
    ctx = RunContext(run_id="r", block_specs=specs)
    ctx.step_params["s5"] = {"di": "02020100"}
    read_spec = specs["meter.read_645"]
    s4 = effective_params(read_spec, planned[4], ctx)
    s5 = effective_params(read_spec, planned[5], ctx)
    assert (s4["di"], s5["di"]) == (ENERGY_DI, "02020100")
    # run 级 --params 仍是最高层，但它是按块广播（两步同时变），不用于「只改一步」
    ctx.block_params["meter.read_645"] = {"di": "000000000000"}
    assert effective_params(read_spec, planned[4], ctx)["di"] == "000000000000"
    assert effective_params(read_spec, planned[5], ctx)["di"] == "000000000000"


# --------------------------------------------------------------------------- #
# 3. 执行（假能力 + 三块桩，无台架）：真读数值的判据
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_full_case_passes_and_reads_both_numbers(case_id: str, stub_non_meter_blocks) -> None:  # noqa: ANN001
    step_ids, block_order = structure(case_id)
    simcon, sent = fake_bench()
    result = run_case(case_id, simcon)

    assert result.verdict == "pass", result.to_dict()
    assert [record.step_id for record in result.blocks] == step_ids
    assert [record.block_id for record in result.blocks] == block_order
    assert [record.state for record in result.blocks] == [BLOCK_PASS] * len(step_ids)
    # 表侧三步真的发了三帧不同的内嵌 645 请求（13H / 11H 电能 / 11H 电压）
    assert simcon.sent == [sent["addr"], sent["energy"], sent["voltage"]]
    assert simcon.sent[0] != simcon.sent[1] != simcon.sent[2]
    # 三步都显式给了 expect_timeout → 块**不再查 03H-F7**（省掉一次 26s 级的前置查询）
    assert simcon.timeout_queries == 0, (
        "用例已显式钉 expect_timeout=15s，块不该再去查 03H-F7 取 t（实测 %d 次）"
        % simcon.timeout_queries
    )
    # 观测值进观测（REQS-0044：块报观测；数值真值不比对，只报解出多少）
    assert "1234.56 kWh" in (result.blocks[-2].observation or result.blocks[-2]).detail
    assert "220.1 V" in (result.blocks[-1].observation or result.blocks[-1]).detail
    # 抄表步带结构化观测（observation），且四项判据字段齐全
    meter_obs = result.blocks[-1].observation
    assert meter_obs is not None and meter_obs.complete
    for field in ("CS合法", "异常应答", "DI回显", "数值"):
        assert field in meter_obs.values, (field, meter_obs.values)


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_voltage_value_drift_still_passes_but_value_is_observed(case_id: str, stub_non_meter_blocks) -> None:  # noqa: ANN001
    """数值漂移不再判负（REQS-0044 §8.1 第4条：只验「有值且可解」，不校验数值真值）。

    覆盖行为说明（D7-1）：旧测试 ``test_voltage_value_mismatch_is_fail_not_pass`` 删除 ——
    它锁的是「块内比对 220.1 真值」的旧判据；新口径下 219.2 V 是**如实观测**，
    步骤仍 pass（数值可解即成立），数值记录在观测里供事后核对。
    """
    simcon, _sent = fake_bench(voltage_digits="2192")  # 219.2 V ≠ 220.1 V
    result = run_case(case_id, simcon)
    assert result.verdict == "pass", result.to_dict()
    voltage = result.blocks[-1]
    assert voltage.state == BLOCK_PASS, voltage.detail
    assert voltage.observation is not None
    assert voltage.observation.values.get("数值") == 219.2, voltage.observation.values


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_explicit_expected_value_still_fails_on_mismatch(case_id: str, stub_non_meter_blocks) -> None:
    """显式给期望值才比对（0044 §8.1 第4条后半）：把电压步的 expect 换成 eq 数值=220.1。"""
    import copy

    case = copy.deepcopy(load_case(case_id))
    voltage_step = case.plan[-1]
    assert voltage_step.block == "meter.read_645"
    voltage_step.expect = [
        {"op": "complete"},
        {"op": "eq", "field": "数值", "value": 220.1},
    ]
    simcon, _sent = fake_bench(voltage_digits="2192")  # 219.2 V ≠ 220.1 V
    ctx = RunContext(
        run_id="run-explicit-%s" % case_id,
        facts_ctx=FactsContext(probe=all_yes),
        block_specs=block_specs(),
        extras={"simcon": simcon},
    )
    result = execute_case(case, ctx)
    voltage = result.blocks[-1]
    assert voltage.state == BLOCK_FAIL, voltage.detail
    assert "220.1" in voltage.detail and "219.2" in voltage.detail


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_voltage_di_echo_mismatch_is_fail(case_id: str, stub_non_meter_blocks) -> None:  # noqa: ANN001
    """请求 A 相电压却回了正向有功总电能（DI 回显不对）→ fail，不能当抄表成功。"""
    simcon, sent = fake_bench()
    simcon.replies[sent["voltage"]] = up_frame_with(
        read_reply_645(BENCH_ADDR, ENERGY_DI, "00123456")
    )
    result = run_case(case_id, simcon)
    voltage = result.blocks[-1]
    assert voltage.state == BLOCK_FAIL, voltage.detail
    # 0044：块层的原始观测搬进了 observation.detail（判据描述在步骤 detail 里）
    assert voltage.observation is not None
    assert "未回显 DI" in voltage.observation.detail and VOLTAGE_DI in voltage.observation.detail


@pytest.mark.parametrize("case_id", [DAILY, FIRST_JOIN])
def test_on_fail_stop_skips_downstream_steps(case_id: str, stub_non_meter_blocks) -> None:  # noqa: ANN001
    """每步显式 ``on_fail="stop"``：电能步失败后，电压步记 skipped 而不是照跑。

    覆盖行为说明（D7-1）：触发失败的手段从「数值 1234.57≠1234.56」改为「从站异常应答」
    —— 数值真值已不比对（0044 §8.1 第4条），异常应答是 expect 明确判负的观测
    （eq 异常应答=false 不成立）。
    """
    simcon, sent = fake_bench()
    # 电能步回「从站异常应答」（控制码 D6=1：0x91 | 0x40 = 0xD1）
    body = bytes([0x68]) + meter_addr_wire(BENCH_ADDR) + bytes([0x68, 0x91 | 0x40, 0x01, 0x33])
    exception_reply = (body + bytes([sum(body) & 0xFF, 0x16])).hex().upper()
    simcon.replies[sent["energy"]] = up_frame_with(exception_reply)
    result = run_case(case_id, simcon)
    energy = result.blocks[-2]
    voltage = result.blocks[-1]
    assert energy.state == BLOCK_FAIL, energy.detail
    assert energy.observation is not None and energy.observation.values.get("异常应答") is True
    assert voltage.state == BLOCK_SKIPPED, voltage.detail
    # 跳过原因要点名「前序哪一步非 pass」+ 明说下游跳过
    assert energy.step_id in voltage.detail and "下游跳过" in voltage.detail


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
