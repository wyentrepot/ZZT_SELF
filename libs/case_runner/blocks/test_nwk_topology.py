"""``nwk.topology``（10H-F21 网络拓扑）与组网块「等待轮询」的单测（REQS-0042 S2-A）。

本文件**只测**三件事，全部脱离台架（真实构帧 + 真实解析 + 脚本化能力替身）：

1. **``nwk.topology`` 三态**（防假 PASS）：
   节点总数量 ≥ ``ceil(档案数 × min_ratio)`` → pass；明确不足且等满 ``wait_s`` → fail；
   档案数本身为 0 → unknown（「没有可入网目标」不是产品失败）；
   应答取不到 / 超时 / 解析不出「节点总数量」→ unknown。
2. **等待轮询只发只读查询**：``wait_s`` / ``poll_interval_s`` 生效，且探测间隔 =
   ``poll_interval_s``；轮询期每一跳都是 ``AFN=10H`` 且 ``Fn ∈ {21, 1, 4, 2}``
   （本文件用 ``FakeSimcon.calls`` 逐跳断言，写帧一旦混入立刻红）。
3. **等待可注入**：``clock`` / ``sleep`` 可注入 → 单测**零真实睡眠**（60s/90s 的等待
   在毫秒级跑完）；``wait_s=0`` = 只探一次、不 sleep（与改动前口径完全一致）。

知识库出处（字段与判据，块实现照此写，不在代码里猜）：

- ``03_QGDW10376.2_全帧类型.md:1154-1180``「#### F21: 查询网络拓扑信息」——
  下行「节点起始序号 BIN 2 + 节点数量 BIN 1」；上行「节点总数量 BIN 2 + 节点起始序号
  BIN 2 + 本次应答的节点数量 n BIN 1 + n×(节点地址 BIN 6 + 节点网络拓扑信息 BIN 5)」，
  网络拓扑信息 = 节点标识(TEI) 2B + 代理节点标识 2B + 节点信息 1B，
  节点信息「D0~D3=节点层级，D4~D7=节点角色(0x0无效,0x1末梢节点STA,0x2代理节点PCO,
  0x4主节点CCO)」。
- ``业务层/37_双模测试用例_业务.md:42-48``——「周期性下发``查询网络拓扑信息``，比对返回：
  节点总数量 = 档案个数的 **98%–100%** …… 计时器超时 → 不通过」。
- ``用例库/CCO/台账/146_国网用例_AFN功能_设置与监控_台账.md:62``（用例 87）——
  「AFN=10H-F21查询网络拓扑信息，起始序号=1，节点数量=2 …… 节点总数量=1，
  本次应答节点总数量=1，只包含主节点」→ **起始序号 1 起编号**，且**节点总数量含主节点**。
"""

from __future__ import annotations

import json
import pathlib
import time

import pytest

from case_runner.blocks import archive, facts_nwk, nwk
from case_runner.core.executor import BLOCK_TO_OUTCOME, RunContext
from case_runner.core.models import BlockSpec, Health
from case_runner.core.expect import evaluate
from case_runner.core.models import BLOCK_FAIL, BLOCK_PASS, BLOCK_SKIPPED, BLOCK_UNKNOWN
from case_runner.core.registry import clear_registries, get_block, has_block

# 真实构帧/解析（测试里允许 import 领域库；分层铁律只约束 libs 生产代码）
from parser_lib.adapters.adapter_10376 import _bcd_bytes, build_frame
from sim_concentrator.frame_codec import decode_frame

DATA_DIR = pathlib.Path(archive.__file__).resolve().parents[1] / "data"
DECL = DATA_DIR / "blocks" / "nwk_topology.json"

#: 只读帧白名单：轮询期只允许这四个 Fn（都在 AFN=10H 路由查询域，全是只读查询）
READONLY_FNS = frozenset({21, 1, 4, 2})
SENT_HEX = "681200430000000000001010020100208616"      # 10H-F21 下行（start=1 count=32）


# --------------------------------------------------------------------------- #
# 真实帧构造
# --------------------------------------------------------------------------- #


#: 0044 观测化助手：组网块步骤期望（在网从节点数 ≥ 门槛 + 观测完整）
TOPO_EXPECT = [{"op": "complete"},
               {"op": "ge_field", "field": "在网从节点数", "value": "门槛"}]


def topo_step_state(result):
    return evaluate(result.observation, result.expect if getattr(result, "expect", None) else TOPO_EXPECT).state


def _le16(value: int) -> bytes:
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def raw_archive_count(total: int, maximum: int = 1152) -> bytes:
    """10H-F1 上行：从节点总数量(2B) + 路由支持最大从节点数量(2B)。"""
    return build_frame(direction="up", afn=0x10, fn=1,
                       appdata=_le16(total) + _le16(maximum))


def raw_topology(total: int, addrs=(), *, start: int = 1, role: int = 0x01,
                 level: int = 2, proxy: int = 1, answered=None,
                 masters=()) -> bytes:
    """10H-F21 上行：总数量(2B) + 起始序号(2B) + 本次条数(1B) + N×(6B 地址 + 5B 拓扑)。

    每节点 5B 拓扑 = TEI(2B) + 代理节点标识(2B) + 节点信息(1B)；
    节点信息 ``D0~D3``=层级、``D4~D7``=角色（0x1 末梢 STA / 0x4 主节点 CCO）。
    ``masters`` 里的地址按主节点（角色 0x4、层级 0）渲染，其余按 ``role``/``level``。
    """
    records = list(addrs)
    master_set = {str(addr) for addr in masters}
    count = len(records) if answered is None else int(answered)
    payload = bytearray(_le16(total)) + bytearray(_le16(start)) + bytes([count])
    for index, addr in enumerate(records):
        is_master = str(addr) in master_set
        node_role, node_level = (0x04, 0) if is_master else (role, level)
        info = ((node_role & 0x0F) << 4) | (node_level & 0x0F)
        payload += _bcd_bytes(addr) + _le16(index + 1) + _le16(proxy) + bytes([info])
    return build_frame(direction="up", afn=0x10, fn=21, appdata=bytes(payload))


def raw_route(switch: int, *, route_word: int = 0x03, total: int = 12,
              answered: int = 12, relay: int = 2) -> bytes:
    """10H-F4 上行 16B（运行状态字 D1=路由完成标志；工作开关 D1=工作状态 1学习/0抄表）。"""
    payload = (
        bytes([route_word]) + _le16(total) + _le16(answered) + _le16(relay)
        + bytes([switch]) + _le16(0x0002) + bytes([1, 1, 1]) + bytes([0, 0, 0])
    )
    assert len(payload) == 16, len(payload)
    return build_frame(direction="up", afn=0x10, fn=4, appdata=payload)


def raw_archive_nodes(total: int, signals) -> bytes:
    """10H-F2 上行逐节点：总数量(2B) + 本帧条数(1B) + N×(6B 地址 + 2B 信息)。"""
    qualities = [int(value) for value in signals]
    payload = bytearray(_le16(total)) + bytes([len(qualities)])
    for index, quality in enumerate(qualities):
        info = ((quality & 0x0F) << 4) | 0x0F
        payload += _bcd_bytes("%012d" % (index + 1)) + _le16(info)
    return build_frame(direction="up", afn=0x10, fn=2, appdata=bytes(payload))


# --------------------------------------------------------------------------- #
# 步骤结果 / 能力替身
# --------------------------------------------------------------------------- #


def step_pass(raw: bytes, *, name: str = "step") -> dict:
    return {
        "name": name,
        "result": "pass",
        "reason": "期望帧匹配",
        "sent_hex": SENT_HEX,
        "matched": raw.hex().upper(),
        "parsed": decode_frame(raw),
    }


def step_timeout(*, name: str = "step", reason: str = "等待帧超时(5.0s)") -> dict:
    return {
        "name": name, "result": "fail", "reason": reason,
        "sent_hex": SENT_HEX, "matched": None, "parsed": None,
    }


def job(*args, job_id: str = "job-1", steps=None) -> dict:
    """verify 结果（``steps`` 既可平铺 ``job(a, b)``、传列表 ``job([a, b])``，
    也可用关键字 ``job(steps=[a, b])`` —— 三种写法都认，避免测试里写法噪音）。"""
    flat: list[dict] = []
    for item in args:
        if isinstance(item, (list, tuple)):
            flat.extend(item)
        else:
            flat.append(item)
    for item in steps or ():
        if isinstance(item, (list, tuple)):
            flat.extend(item)
        else:
            flat.append(item)
    return {
        "ok": True, "job_id": job_id, "job_state": "succeeded",
        "steps": flat, "run_id": "run-1", "steps_failed": 0,
        "verdict": "pass", "summary": "ok",
    }


class FakeSimcon:
    """脚本化能力替身：按序返回预设 verify 结果，并记录每一次下发的步骤序列。"""

    name = "simcon"

    def __init__(self, *results, health=None):
        self.results = list(results)
        self.calls: list[list[dict]] = []
        self.health = health if health is not None else Health(ok=True, reason="fake simcon")

    def probe(self):
        return self.health

    def verify(self, steps):
        self.calls.append(json.loads(json.dumps(steps)))
        if not self.results:
            raise AssertionError("FakeSimcon: verify 调用次数超出脚本（轮询没停在预期轮次）")
        return self.results.pop(0)

    # -- 断言辅助 --------------------------------------------------------- #
    def sent_frames(self) -> list[dict]:
        return [step["send"] for call in self.calls for step in call]

    def sent_fns(self) -> list[int]:
        return [int(frame["fn"]) for frame in self.sent_frames()]

    def sent_afns(self) -> list[int]:
        return [int(frame["afn"]) for frame in self.sent_frames()]


class FakeClock:
    """注入式虚拟时钟：``sleep(n)`` 推进虚拟时间并记账，真实时间一点不耗。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


def ctx_with(cap) -> RunContext:
    return RunContext(extras={"simcon": cap})


def action_of(afn, fn, **params) -> dict:
    """块声明形状 ``{afn, fn, params}``（执行器交给块执行体的就是它）。"""
    return {"afn": afn, "fn": fn, "params": params}


@pytest.fixture(autouse=True)
def _isolated_registries():
    clear_registries()
    archive.register_blocks()
    nwk.register_blocks()
    facts_nwk.register_facts()
    yield
    clear_registries()


# --------------------------------------------------------------------------- #
# A. 声明与注册
# --------------------------------------------------------------------------- #


def test_topology_declaration_matches_core_models():
    """声明文件存在且符合 BlockSpec；afn="10"/fn=21、tags 含 nwk/readonly/online。"""
    raw = json.loads(DECL.read_text(encoding="utf-8"))
    spec = BlockSpec.model_validate(raw)
    assert spec.id == "nwk.topology"
    # 0044 AC-5：组网前置「档案已写入」由事实链表达（archive.add provides）
    assert "archive.written" in spec.needs
    assert spec.action["afn"] == "10" and spec.action["fn"] == 21
    assert spec.needs == ["bench.simcon_online", "archive.written"]  # 0044 AC-5 事实链
    assert spec.provides == ["nwk.joined"]
    for tag in ("nwk", "readonly", "online"):
        assert tag in spec.tags, spec.tags
    assert spec.title and "10H-F21" in spec.title
    # 声明里的默认参数与服务端兜底同源（show 与 run 一致）
    params = spec.action.get("params") or {}
    assert params.get("start") == nwk.DEFAULT_TOPO_START == 1
    assert float(params.get("wait_s")) == nwk.DEFAULT_WAIT_TOPOLOGY
    assert float(params.get("poll_interval_s")) == nwk.DEFAULT_POLL_INTERVAL
    assert float(params.get("min_ratio")) == nwk.DEFAULT_MIN_RATIO == pytest.approx(0.98)
    # ★ 默认严格口径：只数从节点（主节点 CCO 不算进分子）
    assert params.get("master_included") is False


def test_topology_block_is_registered():
    assert has_block(nwk.BLOCK_TOPOLOGY)
    assert get_block(nwk.BLOCK_TOPOLOGY) is nwk.block_topology


def test_nodes_online_and_route_status_declarations_carry_wait_defaults():
    """两个既有只读块的声明也带上等待默认值（90 / 60 秒，间隔 2 秒）。"""
    for filename, wait in (
        ("nwk_nodes_online.json", nwk.DEFAULT_WAIT_NODES_ONLINE),
        ("nwk_route_status.json", nwk.DEFAULT_WAIT_ROUTE_STATUS),
    ):
        params = json.loads((DATA_DIR / "blocks" / filename).read_text(encoding="utf-8"))["action"]["params"]
        assert float(params["wait_s"]) == wait, filename
        assert float(params["poll_interval_s"]) == nwk.DEFAULT_POLL_INTERVAL, filename
    # 依据：台架实测一轮入网约 240s（4 分钟）；预算必须 ≥ 实测 + 余量，否则假 FAIL
    assert nwk.DEFAULT_WAIT_TOPOLOGY == 300.0          # 实测 240s + 60s 余量
    assert nwk.DEFAULT_WAIT_ROUTE_STATUS == 300.0      # 学习态→抄表在入网完成之后，同预算
    assert nwk.DEFAULT_WAIT_NODES_ONLINE == 180.0      # 只判「至少 1 个节点抄通」：实测一半 + 余量


# --------------------------------------------------------------------------- #
# B. 纯函数：门槛计算 / 只读闸门 / 轮询器
# --------------------------------------------------------------------------- #


def test_required_topology_count_is_ceil_of_ratio():
    # 12 × 0.98 = 11.76 → ceil = 12（浮点误差不能把 12 抬成 13）
    assert nwk.required_topology_count(12, 0.98) == 12
    assert nwk.required_topology_count(100, 0.98) == 98
    assert nwk.required_topology_count(3, 0.98) == 3
    assert nwk.required_topology_count(1, 0.98) == 1
    assert nwk.required_topology_count(0, 0.98) == 0
    assert nwk.required_topology_count(10, 0.5) == 5
    assert nwk.required_topology_count(11, 0.5) == 6
    assert nwk.required_topology_count(None, 0.98) is None
    assert nwk.required_topology_count(12, 0) is None          # 非法阈值不给结论


def test_readonly_violations_flags_write_frames_and_accepts_queries():
    queries = [
        nwk.send_step("拓扑", 0x10, 21, {}, expect_afn=0x10, expect_fn=21),
        nwk.send_step("档案数", 0x10, 1, {}, expect_afn=0x10, expect_fn=1),
        nwk.send_step("路由状态", 0x10, 4, {}, expect_afn=0x10, expect_fn=4),
        nwk.send_step("档案表", 0x10, 2, {}, expect_afn=0x10, expect_fn=2),
    ]
    assert nwk.readonly_violations(queries) == []
    # 写帧（12H-F1 启动路由 / 11H-F1 添加从节点 / 01H-F2 参数初始化）必须被拦下
    for afn, fn in ((0x12, 1), (0x11, 1), (0x01, 2)):
        bad = nwk.send_step("写", afn, fn, {}, expect_afn=0x00, expect_fn=1)
        assert nwk.readonly_violations([bad]), (afn, fn)


def test_poll_snapshot_stops_on_satisfied_and_honours_interval():
    clock = FakeClock()
    probes = iter([{"value": 0}, {"value": 0}, {"value": 9}])
    loop = nwk.poll_snapshot(
        lambda: next(probes), wait_s=60, interval_s=2,
        stop=lambda snap: "satisfied" if snap["value"] >= 9 else None,
        clock=clock, sleep=clock.sleep,
    )
    assert loop["reason"] == "satisfied" and loop["attempts"] == 3
    assert clock.sleeps == [2.0, 2.0]        # 间隔严格 = poll_interval_s
    assert loop["waited"] == pytest.approx(4.0)


def test_poll_snapshot_times_out_after_wait_and_never_loops_forever():
    clock = FakeClock()
    loop = nwk.poll_snapshot(
        lambda: {"value": 0}, wait_s=6, interval_s=2,
        stop=lambda snap: None, clock=clock, sleep=clock.sleep,
    )
    assert loop["timed_out"] is True and loop["attempts"] == 4   # t=0,2,4,6
    assert clock.sleeps == [2.0, 2.0, 2.0]
    assert loop["waited"] == pytest.approx(6.0)

    zero = FakeClock()
    loop0 = nwk.poll_snapshot(
        lambda: {"value": 0}, wait_s=0, interval_s=2,
        stop=lambda snap: None, clock=zero, sleep=zero.sleep,
    )
    assert loop0["attempts"] == 1 and zero.sleeps == []          # wait_s=0 → 只探一次
    # poll_interval_s=0：不真 sleep 也必须收敛（按 tick 记账，不无限循环）
    loop_no_sleep = nwk.poll_snapshot(
        lambda: {"value": 0}, wait_s=60, interval_s=0,
        stop=lambda snap: None, clock=FakeClock(), sleep=lambda seconds: None,
    )
    assert loop_no_sleep["timed_out"] is True
    assert loop_no_sleep["attempts"] == 31                       # ceil(60/2)+1
    assert loop_no_sleep["waited"] == pytest.approx(60.0)


# --------------------------------------------------------------------------- #
# C. nwk.topology 三态（防假 PASS）
# --------------------------------------------------------------------------- #


def _topology_params(**params):
    base = {"start": 1, "count": 32, "wait_s": 0, "poll_interval_s": 2}
    base.update(params)
    return action_of("10", 21, **base)


def test_topology_pass_when_total_reaches_threshold():
    """档案 12、节点总数量 12 → 门槛 ceil(11.76)=12 → pass（并写明 98% 口径）。"""
    fake = FakeSimcon(
        job(job_id="job-f1", steps=[step_pass(raw_archive_count(12))]),
        job(job_id="job-f21", steps=[step_pass(raw_topology(12, ["%012d" % i for i in range(1, 13)]))]),
    )
    ok = nwk.block_topology(_topology_params(), ctx_with(fake))
    # 0044 观测化：达标与否由步骤期望（ge_field 在网从节点数 ≥ 门槛）判；块只报观测
    assert ok.observation is not None and ok.observation.complete
    assert ok.observation.values["节点总数量"] == 12
    assert ok.observation.values["档案数"] == 12
    assert ok.observation.values["门槛"] == 12
    assert ok.observation.values["在网从节点数"] == 12
    assert "98" in ok.observation.detail or "0.98" in ok.observation.detail
    assert topo_step_state(ok) == BLOCK_PASS, ok.observation.detail
    assert ok.evidence_ids == ["job-f1", "job-f21"]


def test_topology_min_ratio_is_overridable():
    """min_ratio=0.5 时档案 12 → 门槛 6，节点总数量 6 即 pass。"""
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(6, ["%012d" % i for i in range(1, 7)]))]),
    )
    ok = nwk.block_topology(_topology_params(min_ratio=0.5), ctx_with(fake))
    assert ok.observation is not None and ok.observation.complete
    assert ok.observation.values["门槛"] == 6
    assert topo_step_state(ok) == BLOCK_PASS, ok.observation.detail


def test_topology_fail_when_short_after_waiting_full_wait_s():
    """明确不足（5 < 门槛 12）且等满 wait_s=4 秒 → fail，detail 写明「等了 4 秒」。"""
    clock = FakeClock()
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(5, ["%012d" % i for i in range(1, 6)]))]),
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(5, ["%012d" % i for i in range(1, 6)]))]),
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(5, ["%012d" % i for i in range(1, 6)]))]),
    )
    result = nwk.block_topology(
        _topology_params(wait_s=4, clock=clock, sleep=clock.sleep), ctx_with(fake)
    )
    # 0044：观测完整（翻页取全）且 在网从节点数 < 门槛 → 期望判 fail（等了 4 秒写进观测）
    assert result.observation is not None and result.observation.complete
    assert result.observation.values["在网从节点数"] < result.observation.values["门槛"]
    assert topo_step_state(result) == BLOCK_FAIL, result.observation.detail
    assert "等了 4 秒" in result.observation.detail
    assert "等了 4 秒" in result.detail
    assert "5 < 门槛 12" in result.detail or "5 < 12" in result.detail
    assert clock.sleeps == [2.0, 2.0]
    # 三态没有被静默折算：fail 就是 fail
    assert BLOCK_TO_OUTCOME["fail"] == "fail"


def test_topology_unknown_when_archive_count_is_zero_and_stops_at_once():
    """档案数 0 → unknown（没有可入网目标，不是产品失败），且不再空转等待。"""
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(0))]),
        job(steps=[step_pass(raw_topology(1, ["000000000000"], role=0x04, level=0))]),
    )
    clock = FakeClock()
    result = nwk.block_topology(
        _topology_params(wait_s=60, clock=clock, sleep=clock.sleep), ctx_with(fake)
    )
    assert result.state == "unknown", result.detail
    assert "没有可入网目标" in result.detail
    assert len(fake.calls) == 2, "档案数为 0 时不该继续轮询"
    assert clock.sleeps == []


def test_topology_unknown_when_topology_no_reply():
    """10H-F21 无应答（帧已发但超时）→ unknown（不是 fail），detail 带「等了 N 秒」。"""
    clock = FakeClock()
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_timeout()]),
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_timeout()]),
    )
    result = nwk.block_topology(
        _topology_params(wait_s=2, clock=clock, sleep=clock.sleep), ctx_with(fake)
    )
    assert result.state == "unknown", result.detail
    assert "等了 2 秒" in result.detail
    assert "10H-F21" in result.detail


def test_topology_unknown_when_total_field_unparsable():
    """应答里没有「节点总数量」（解析不出）→ unknown，绝不判 pass。"""
    raw = build_frame(direction="up", afn=0x10, fn=21, appdata=b"")
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw)]),
    )
    result = nwk.block_topology(_topology_params(), ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert "节点总数量" in result.detail


def test_topology_unknown_when_archive_count_unavailable():
    """档案数取不到（10H-F1 无应答）→ 算不出门槛 → unknown。"""
    fake = FakeSimcon(
        job(steps=[step_timeout()]),
        job(steps=[step_pass(raw_topology(12, ["%012d" % i for i in range(1, 13)]))]),
    )
    result = nwk.block_topology(_topology_params(), ctx_with(fake))
    assert result.state == "unknown", result.detail
    assert "档案数" in result.detail


def test_topology_polls_until_satisfied_and_sends_only_readonly_frames():
    """第 1 轮不足 → 第 2 轮达标即 pass；逐跳断言只有只读帧、间隔 = poll_interval_s。"""
    clock = FakeClock()
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(6, ["%012d" % i for i in range(1, 7)]))]),
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(12, ["%012d" % i for i in range(1, 13)]))]),
    )
    ok = nwk.block_topology(
        _topology_params(wait_s=60, clock=clock, sleep=clock.sleep), ctx_with(fake)
    )
    assert topo_step_state(ok) == BLOCK_PASS, ok.observation.detail
    assert "等了 2 秒" in ok.observation.detail
    # 只读：每一跳都是 AFN=10H 且 Fn ∈ {21,1,4,2}；顺序是 档案数(F1) → 拓扑(F21) 两轮
    assert fake.sent_afns() == [0x10] * 4
    assert fake.sent_fns() == [1, 21, 1, 21]
    assert set(fake.sent_fns()) <= READONLY_FNS
    # 拓扑查询带上 start/count（默认 start=1 起编号、count=32）
    assert fake.sent_frames()[1]["params"] == {"start": 1, "count": 32}
    assert clock.sleeps == [2.0]


def test_topology_default_is_strict_master_excluded():
    """★ 默认口径 = 只数从节点：12 档案 / 节点总数量 12（含 1 主 + 11 从）→ **fail**。

    主节点 CCO 本来就一定在，算进分子会让 91.7%（11/12）的从节点在网也判 pass ——
    正是主代理裁决要堵的漏判。
    """
    addrs = ["000000000000"] + ["%012d" % i for i in range(2, 13)]     # 1 主 + 11 从
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(12, addrs, masters=["000000000000"]))]),
    )
    result = nwk.block_topology(_topology_params(), ctx_with(fake))      # 不传 master_included
    # 0044：观测完整 + 在网从节点数 11 < 门槛 12 → 期望判 fail
    assert result.observation is not None and result.observation.complete
    assert result.observation.values["节点总数量"] == 12
    assert result.observation.values["主节点数"] == 1
    assert result.observation.values["在网从节点数"] == 11
    assert result.observation.values["口径"] == "仅从节点"
    assert "入网从节点=11 < 门槛 12" in result.observation.detail
    assert topo_step_state(result) == BLOCK_FAIL, result.observation.detail


def test_topology_master_included_true_is_the_loose_optional_reading():
    """对照：同一条应答，``master_included=true``（照 KB 原句直接用节点总数量）→ pass。

    两种口径差 1 个节点，就是「漏判 vs 严格」的分界；用同一份应答把差异钉死。
    """
    addrs = ["000000000000"] + ["%012d" % i for i in range(2, 13)]     # 1 主 + 11 从
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(12, addrs, masters=["000000000000"]))]),
    )
    ok = nwk.block_topology(
        _topology_params(master_included=True), ctx_with(fake)
    )
    # master_included=true 时观测字段「在网从节点数」= 节点总数量（照 KB 原句口径）→ pass
    assert ok.observation.values["口径"] == "含主节点"
    assert ok.observation.values["在网从节点数"] == 12
    assert "按仅从节点则为 11" in ok.observation.detail
    assert topo_step_state(ok) == BLOCK_PASS, ok.observation.detail


def test_judge_topology_default_argument_is_strict():
    """纯函数层也默认严格：``judge_topology(probe)`` 不带参数时按仅从节点判。"""
    probe = {
        "archive": {"state": "answered", "total": 12},
        "topology": {"state": "answered", "total": 12, "masters": 1},
    }
    state, detail, actual = nwk.judge_topology(probe)
    assert state == "no", detail
    assert actual["在网从节点数"] == 11
    assert "入网从节点=11" in detail


def test_topology_bad_params_fail_before_any_frame():
    fake = FakeSimcon()
    for params in (
        {"min_ratio": 1.5},
        {"min_ratio": 0},
        {"start": -1},
        {"count": 0},
        {"count": 999},
    ):
        result = nwk.block_topology(_topology_params(**params), ctx_with(fake))
        # 0044：坏参数 = 原因码 invalid_param（配置错误不是设备结论），一帧不发
        assert result.observation is not None
        assert result.observation.reason_code == "invalid_param", (params, result.detail)
        assert list(params)[0] in result.observation.detail
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# D. 既有只读块的等待轮询（nodes_online / route_status）
# --------------------------------------------------------------------------- #


def test_nodes_online_wait_passes_when_bench_becomes_online():
    """第 1 轮已抄=0 + 信号全 0（旧口径 fail）→ 等 2 秒后第 2 轮已抄=3 → pass。"""
    clock = FakeClock()
    fake = FakeSimcon(
        job(steps=[step_pass(raw_route(0x06, route_word=0x03, total=12, answered=0, relay=12))]),
        job(steps=[step_pass(raw_archive_nodes(12, [0] * 12))]),
        job(steps=[step_pass(raw_route(0x00, route_word=0x03, total=12, answered=3, relay=2))]),
        job(steps=[step_pass(raw_archive_nodes(12, [0] * 12))]),
    )
    ok = nwk.block_nodes_online(
        action_of("10", 4, min=1, wait_s=90, clock=clock, sleep=clock.sleep), ctx_with(fake)
    )
    assert ok.state == "pass", ok.detail
    assert "等了 2 秒" in ok.detail
    assert clock.sleeps == [2.0]
    assert set(fake.sent_fns()) <= {4, 2}                  # 只发 10H-F4 / 10H-F2
    assert set(fake.sent_afns()) == {0x10}


def test_nodes_online_wait_timeout_keeps_fail_with_waited_text():
    """等满 wait_s 仍两路为 0 → 原三态口径 fail，detail 写「等了 N 秒」。"""
    clock = FakeClock()
    zero_round = [
        job(steps=[step_pass(raw_route(0x06, route_word=0x03, total=12, answered=0, relay=12))]),
        job(steps=[step_pass(raw_archive_nodes(12, [0] * 12))]),
    ]
    fake = FakeSimcon(*[r for _ in range(3) for r in zero_round])
    result = nwk.block_nodes_online(
        action_of("10", 4, min=1, wait_s=4, clock=clock, sleep=clock.sleep), ctx_with(fake)
    )
    assert result.state == "fail", result.detail
    assert "等了 4 秒" in result.detail
    assert "两路证据都明确为 0" in result.detail
    assert clock.sleeps == [2.0, 2.0]


def test_nodes_online_wait_timeout_stays_unknown_when_unanswered():
    """两路都无应答 → 等满后仍 unknown（不是 fail），detail 写「等了 N 秒」。"""
    clock = FakeClock()
    fake = FakeSimcon(*[job(steps=[step_timeout()]), job(steps=[step_timeout()])] * 2)
    result = nwk.block_nodes_online(
        action_of("10", 4, min=1, wait_s=2, clock=clock, sleep=clock.sleep), ctx_with(fake)
    )
    assert result.state == "unknown", result.detail
    assert "等了 2 秒" in result.detail


def test_route_status_wait_passes_when_enters_metering():
    """第 1 轮学习态（inconclusive）→ 等 2 秒后工作状态=抄表 → pass。"""
    clock = FakeClock()
    fake = FakeSimcon(
        job(steps=[step_pass(raw_route(0x06, route_word=0x03, total=12, answered=12))]),
        job(steps=[step_pass(raw_route(0x00, route_word=0x03, total=12, answered=12))]),
    )
    ok = nwk.block_route_status(
        action_of("10", 4, wait_s=60, clock=clock, sleep=clock.sleep), ctx_with(fake)
    )
    assert ok.state == "pass", ok.detail
    assert ok.actual["分类"] == "running"
    assert "工作状态=抄表" in ok.detail
    assert clock.sleeps == [2.0]
    assert fake.sent_fns() == [4, 4]


def test_route_status_learning_until_timeout_is_unknown_with_waited_text():
    """学习态持续到**默认** wait_s=300 超时 → unknown +「等了 300 秒仍处学习态」（不是 fail）。

    300s / 间隔 2s → 151 轮探测、150 次 sleep（虚拟时钟，真实耗时 ~0.01s）。
    """
    clock = FakeClock()
    learning = raw_route(0x06, route_word=0x03, total=12, answered=12)
    fake = FakeSimcon(*[job(steps=[step_pass(learning)]) for _ in range(151)])
    result = nwk.block_route_status(
        action_of("10", 4, clock=clock, sleep=clock.sleep), ctx_with(fake)   # 用默认 wait_s
    )
    assert result.state == "unknown", result.detail
    assert result.state != "fail"
    assert BLOCK_TO_OUTCOME[result.state] == "inconclusive"
    assert "等了 300 秒仍处学习态" in result.detail
    assert result.actual["分类"] == "learning_inconclusive"
    assert result.actual["等待秒"] == pytest.approx(300.0)
    assert len(fake.calls) == 151 and clock.sleeps == [2.0] * 150


def test_route_status_explicit_expect_stops_without_full_wait():
    """显式 expect 命中即停（用的是与最终判定同一份口径），不白等满 60 秒。"""
    clock = FakeClock()
    fake = FakeSimcon(job(steps=[step_pass(raw_route(0x40))]))     # 当前状态=搜表
    ok = nwk.block_route_status(
        action_of("10", 4, expect=["搜表"], clock=clock, sleep=clock.sleep),   # 默认 wait_s=60
        ctx_with(fake),
    )
    assert ok.state == "pass", ok.detail
    assert "当前状态=搜表" in ok.detail
    assert len(fake.calls) == 1 and clock.sleeps == []


def test_topology_archive_count_comes_from_10h_f1_not_10h_f4():
    """档案数口径锁定：取 10H-F1「从节点总数量」，不借用 10H-F4 的结构体字段。"""
    fake = FakeSimcon(
        job(steps=[step_pass(raw_archive_count(12))]),
        job(steps=[step_pass(raw_topology(12, ["%012d" % i for i in range(1, 13)]))]),
    )
    ok = nwk.block_topology(_topology_params(), ctx_with(fake))
    assert ok.observation is not None and ok.observation.complete
    assert ok.observation.values["档案数"] == 12
    assert topo_step_state(ok) == BLOCK_PASS, ok.observation.detail
    assert fake.sent_fns() == [1, 21], "每轮只发 10H-F1（档案数）+ 10H-F21（拓扑）"
    assert 4 not in fake.sent_fns(), "不得改用 10H-F4 取档案数"


# --------------------------------------------------------------------------- #
# E. 等待可注入：单测零真实睡眠
# --------------------------------------------------------------------------- #


def test_default_wait_does_not_really_sleep_when_clock_injected():
    """默认 **300 秒**等待 + 注入虚拟时钟 → 真实耗时远小于 1 秒（证明没有 time.sleep 真睡）。"""
    clock = FakeClock()
    learning = raw_route(0x06, route_word=0x03, total=12, answered=12)
    fake = FakeSimcon(*[job(steps=[step_pass(learning)]) for _ in range(151)])
    started = time.perf_counter()
    result = nwk.block_route_status(
        action_of("10", 4, clock=clock, sleep=clock.sleep), ctx_with(fake)   # 用默认 wait_s=300
    )
    elapsed = time.perf_counter() - started
    assert result.state == "unknown", result.detail
    assert clock.now == pytest.approx(300.0)       # 虚拟时间走满 300 秒
    assert elapsed < 1.0, "注入 clock/sleep 后不许真 sleep：实测 %.3fs" % elapsed
