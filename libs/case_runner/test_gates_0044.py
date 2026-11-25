"""REQS-0044 验收门禁（AC-1 / AC-2 / AC-4 / AC-5 / AC-6）。

本文件把 0044 的可检查验收项落成测试，编号对应 REQS §7：

- **AC-1**：已迁移的 7 个块函数**不输出**通过/失败结论 —— 代码扫描：函数源里无
  ``BLOCK_PASS`` / ``BLOCK_FAIL`` 结论常量、无「通过 / 失败 / PASS / FAIL」字样；
- **AC-2**：同一查询块（``archive.query``）服务两条**相反期望**的 Procedure ——
  期望「含」与期望「不含」各自判 pass（真机入口：``archive_ready`` / ``archive_absent``）；
- **AC-4**：16 块声明三字段（kind / side_effect / version）显式齐备 + 指纹可复算；
  setup 类 Procedure 不计入产品通过率（报表契约测试覆盖 rows 侧）；
- **AC-5**：事实链闭合 —— 「档案→组网→绑表→抄表」needs 由前序 provides 满足；
  调换顺序会被规划层拦下（not runnable → blocked）；
- **AC-6**：无判据不入库 —— loader 拒绝「引用只观测块但未声明 expect」的 Procedure，
  并拒绝「等待型步骤缺显式等待预算」的 Procedure（v2 拍板 #3）。

全部离线运行（不启工作台、不碰串口）。
"""

from __future__ import annotations

import inspect
import json
import pathlib

import pytest

from case_runner.blocks import loader
from case_runner.core import expect as expect_mod
from case_runner.core.executor import execute_case, RunContext
from case_runner.core.models import BLOCK_UNKNOWN, BlockSpec, CaseSpec, CaseStep
from case_runner.core.planner import FactValue, FactsContext, plan
from case_runner.core.registry import clear_registries

DATA_DIR = pathlib.Path(loader.DATA_DIR)

#: 已迁移为「只报观测」的块（REQS §6.2 第 3 步切片；0043-F1 起新块天生只观测）
MIGRATED_BLOCKS = (
    "fix.simcon_session",
    "fix.simeter_session",
    "archive.add",
    "archive.query",
    "nwk.topology",
    "meter.addr_probe",
    "meter.read_645",
    # 0043-F1 补块批次 1（5 块）：出生即 observation_only
    "meter.batch_read",
    "gate.multi_frame_110",
    "gate.concurrent_109",
    "meter.read_698",
    "param.init_01h_f2",
    # 0043-H 协议一致性补块（2 块）：出生即 observation_only
    "bench.simeter_protocol",
    "sta.meter_protocol",
)

#: 等待型块（v2 拍板 #3：Procedure 必须显式给等待预算）
WAITING_BLOCKS = {
    "nwk.topology",
    "meter.addr_probe",
    "meter.read_645",
    "meter.batch_read",
    "meter.read_698",
}


@pytest.fixture(autouse=True)
def _registries():
    clear_registries()
    loader.register_domain_modules(strict=False)
    yield
    clear_registries()


# --------------------------------------------------------------------------- #
# AC-1：块返回不含通过/失败结论（代码扫描）
# --------------------------------------------------------------------------- #


def _module_of(block_id: str):
    from case_runner.blocks import (
        archive, fix, gate, meter, meter_698, meter_batch, param_init, nwk, protocol,
    )

    return {
        "fix.simcon_session": fix,
        "fix.simeter_session": fix,
        "archive.add": archive,
        "archive.query": archive,
        "nwk.topology": nwk,
        "meter.addr_probe": meter,
        "meter.read_645": meter,
        "meter.batch_read": meter_batch,
        "gate.multi_frame_110": gate,
        "gate.concurrent_109": gate,
        "meter.read_698": meter_698,
        "param.init_01h_f2": param_init,
        "bench.simeter_protocol": protocol,
        "sta.meter_protocol": protocol,
    }[block_id]


def _block_source(block_id: str) -> str:
    module = _module_of(block_id)
    attr = block_id.split(".")[1]
    fn = getattr(module, {
        "simcon_session": "fix_simcon_session",
        "simeter_session": "fix_simeter_session",
        "add": "block_add",
        "query": "block_query",
        "topology": "block_topology",
        "addr_probe": "meter_addr_probe",
        "read_645": "meter_read_645",
        "batch_read": "meter_batch_read",
        "multi_frame_110": "gate_multi_frame_110",
        "concurrent_109": "gate_concurrent_109",
        "read_698": "meter_read_698",
        "init_01h_f2": "param_init_01h_f2",
        "simeter_protocol": "bench_simeter_protocol",
        "meter_protocol": "sta_meter_protocol",
    }[attr])
    return inspect.getsource(fn)


#: 块函数**输出**里禁止出现的结论词（AC-1）：字符串字面量（detail/观测说明）里
#: 不许出现「通过 / 失败 / PASS / FAIL」；名字空间里不许引用 pass/fail 结论常量。
#: docstring 与注释豁免（它们是契约说明，不是输出）。
WORD_VERDICT_TOKENS = ("通过", "失败", "PASS", "FAIL")
NAME_VERDICT_TOKENS = {"BLOCK_PASS", "BLOCK_FAIL", "OUTCOME_PASS", "OUTCOME_FAIL"}


def _scan_function(source: str) -> list[str]:
    """AST 扫描：返回块函数的违规清单（结论常量名 + 字面量里的结论词）。"""
    import ast

    tree = ast.parse(source)
    fn = tree.body[0]
    # 跳过函数 docstring（body[0] 的 Expr/Constant str）
    doc_ids: set[int] = set()
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        doc_ids.add(id(fn.body[0].value))
    offenders: list[str] = []
    for node in ast.walk(fn):
        if id(node) in doc_ids:
            continue
        if isinstance(node, ast.Name) and node.id in NAME_VERDICT_TOKENS:
            offenders.append(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            for token in WORD_VERDICT_TOKENS:
                if token in text:
                    offenders.append("%r:%s" % (token, text[:40]))
    return offenders


@pytest.mark.parametrize("block_id", MIGRATED_BLOCKS)
def test_ac1_block_functions_have_no_verdict_constants_or_wording(block_id: str):
    """AC-1：块函数输出无 pass/fail 结论常量、无「通过/失败」字样（docstring 豁免）。"""
    offenders = _scan_function(_block_source(block_id))
    assert offenders == [], (
        "块 %s 的函数输出出现结论字样 %s —— 判据必须归步骤期望（REQS-0044 §4.2）"
        % (block_id, offenders)
    )


def test_ac1_migrated_blocks_declared_observation_only():
    """AC-1 的声明侧：7 块声明 ``observation_only: true``（其余块未迁移不受影响）。"""
    specs = {spec.id: spec for spec in loader.load_blocks()}
    for block_id in MIGRATED_BLOCKS:
        assert specs[block_id].observation_only is True, block_id
    assert specs["reset.sta"].observation_only is False, "未迁移块不受影响"


# --------------------------------------------------------------------------- #
# AC-2：同一查询块服务两条相反期望
# --------------------------------------------------------------------------- #


class _ArchiveTableFake:
    """假 simcon：按序回 10H-F2 档案表应答（最小实现，只为 AC-2 求值链）。"""

    name = "simcon"

    def __init__(self, total: int, addresses: list[str]):
        self._total = total
        self._addresses = addresses
        from parser_lib.adapters.adapter_10376 import build_frame

        fields = []
        for index, addr in enumerate(addresses, 1):
            fields.append({"name": "从节点总数量", "value": str(total)})
            fields.append({"name": "从节点%d地址" % index, "value": addr})
        # 简化：archive_snapshot 的解析口径在 blocks/test_archive_nwk.py 有真实帧覆盖；
        # 这里直接走 judge_contains 的输入形状（snapshot dict 由 archive_snapshot 产出，
        # 本假实现按其返回结构构造最小说明）——实际帧解析已在 AC-2 的真块测试覆盖。
        self._frame_hex = build_frame(
            direction="up", afn=0x10, fn=2,
            data={"params": {}},
        ) if False else ""

    def verify(self, steps, max_poll_seconds=None):  # pragma: no cover - 见上
        raise AssertionError("AC-2 用例走求值器层，不依赖 verify")


def test_ac2_same_query_block_serves_opposite_expects():
    """AC-2：同一份观测，「期望含」与「期望不含」两条 Procedure 各自 pass。

    观测：档案表 total=1、只含 000000000002（目标 000000000001 不在）。
    - Procedure A（档案就绪方向）：expect ``eq 在档=true`` → fail（含判据不成立）；
    - Procedure B（清库反证方向）：expect ``eq 在档=false`` → pass。
    反过来换一份含目标地址的观测，A pass / B fail —— 同一块、零改动、双向成立。
    """
    from case_runner.core.expect import Observation, evaluate

    observation_with = Observation(
        values={"总数": 1, "已查条数": 1, "查询地址": "010000000000", "在档": True},
        complete=True, reason_code="", detail="档案表含目标地址（观测完整）",
    )
    observation_without = Observation(
        values={"总数": 1, "已查条数": 1, "查询地址": "010000000000", "在档": False},
        complete=True, reason_code="", detail="档案表不含目标地址（观测完整）",
    )
    contains = [{"op": "complete"}, {"op": "eq", "field": "在档", "value": True}]
    not_contains = [{"op": "complete"}, {"op": "eq", "field": "在档", "value": False}]

    # 观测 A（含）：期望含 → pass；期望不含 → fail
    assert evaluate(observation_with, contains).state == "pass"
    assert evaluate(observation_with, not_contains).state == "fail"
    # 观测 B（不含）：期望含 → fail；期望不含 → pass
    assert evaluate(observation_without, contains).state == "fail"
    assert evaluate(observation_without, not_contains).state == "pass"


def test_ac2_declared_procedures_are_the_two_directions():
    """AC-2 的声明侧：archive_ready 期望「在档=true」，archive_absent 期望「在档=false」，
    且两者引用同一个块 archive.query（同一份观测服务相反期望）。"""
    cases = {case.id: case for case in loader.load_cases()}
    ready = cases["archive_ready"]
    absent = cases["archive_absent"]
    blocks_of = lambda case: [step.block for step in case.effective_steps()]  # noqa: E731
    assert "archive.query" in blocks_of(ready) and "archive.query" in blocks_of(absent)

    def expects_field_value(case, field):
        for step in case.effective_steps():
            for item in step.expect:
                if item.get("field") == field:
                    return item.get("value")
        return None

    assert expects_field_value(ready, "在档") is True
    assert expects_field_value(absent, "在档") is False


def test_ac2_end_to_end_via_executor_with_fake_observation():
    """AC-2 端到端（fake 台架）：同一 archive.query 块，两条 Procedure 各自 pass。

    块执行体用桩回「在档=True/False」的完整观测，步骤期望按声明求值 —— 与真机
    ``archive_ready`` / ``archive_absent`` 的行为逐字同链路（块 → 观测 → 期望 → 四态）。
    """
    from case_runner.core.expect import Observation
    from case_runner.core.executor import get_block

    def fake_query(params, ctx=None):
        action = dict(params or {})
        present = bool(action.get("present", True))
        return __import__("case_runner.core.executor", fromlist=["BlockResult"]).BlockResult(
            state=BLOCK_UNKNOWN,
            detail="AC-2 桩：档案表%s目标地址（观测完整）" % ("含" if present else "不含"),
            observation=Observation(
                values={"总数": 1, "已查条数": 1,
                        "查询地址": str(action.get("meter_addr") or ""),
                        "在档": present},
                complete=True, reason_code="", detail="AC-2 桩观测",
            ),
        )

    clear_registries()
    from case_runner.core.registry import register_block

    register_block("archive.query", fake_query)
    spec = BlockSpec(
        id="archive.query", title="查询档案表（AC-2）",
        needs=[], provides=[], action={"afn": "10", "fn": 2, "params": {}},
        observation_only=True,
    )
    ctx = RunContext(run_id="run-ac2", block_specs={"archive.query": spec})

    procedure_contains = CaseSpec(
        id="proc_contains", title="档案就绪（期望含）",
        steps=[CaseStep(no=1, do="查档", expect="在档=true")],
        plan=[{"id": "s1", "block": "archive.query", "on_fail": "stop",
               "params": {"meter_addr": "123456789012"},
               "expect": [{"op": "complete"}, {"op": "eq", "field": "在档", "value": True}]}],
    )
    procedure_absent = CaseSpec(
        id="proc_absent", title="档案反证（期望不含）",
        steps=[CaseStep(no=1, do="查档", expect="在档=false")],
        plan=[{"id": "s1", "block": "archive.query", "on_fail": "stop",
               "params": {"meter_addr": "999999999999", "present": False},
               "expect": [{"op": "complete"}, {"op": "eq", "field": "在档", "value": False}]}],
    )
    assert execute_case(procedure_contains, ctx).verdict == "pass"
    assert execute_case(procedure_absent, ctx).verdict == "pass"


# --------------------------------------------------------------------------- #
# AC-4：三字段 + 指纹
# --------------------------------------------------------------------------- #


def test_ac4_all_block_declarations_carry_metadata_and_fingerprint():
    """AC-4：全部块声明（16 旧 + 0043-F1 新 5 + 0043-H 新 2）kind/side_effect/version
    显式齐备；指纹随内容变化且可复算。"""
    spec_list = loader.load_blocks()
    assert len(spec_list) == 23, [spec.id for spec in spec_list]
    specs = {spec.id: spec for spec in spec_list}
    fingerprints = {}
    for spec in spec_list:
        assert spec.kind in ("case", "setup", "teardown", "probe"), spec.id
        assert spec.side_effect in ("none", "reversible", "irreversible"), spec.id
        assert spec.version >= 1, spec.id
        assert len(spec.fingerprint) == 16, spec.id
        fingerprints[spec.id] = spec.fingerprint

    # 不可逆动作必须标 irreversible（复位是典型）
    assert specs["reset.sta"].side_effect == "irreversible"
    # 指纹对内容敏感：改一个字段 → 指纹变
    raw = json.loads((DATA_DIR / "blocks" / "archive_query.json").read_text(encoding="utf-8"))
    modified = dict(raw, version=raw["version"] + 1)
    from case_runner.core.models import BlockSpec

    changed = BlockSpec.model_validate(modified)
    assert changed.fingerprint != fingerprints["archive.query"]


# --------------------------------------------------------------------------- #
# AC-5：事实链（顺序约束）
# --------------------------------------------------------------------------- #


def test_ac5_fact_chain_declared_in_order():
    """AC-5：档案→组网→绑表→抄表 的 needs 由前序 provides 满足（声明侧）。"""
    specs = {spec.id: spec for spec in loader.load_blocks()}
    chain = [
        ("archive.add", "archive.written", "nwk.topology"),
        ("nwk.topology", "nwk.joined", "meter.addr_probe"),
        ("meter.addr_probe", "sta.addr_obtained", "meter.read_645"),
    ]
    for provider, fact, consumer in chain:
        assert fact in specs[provider].provides, provider
        assert fact in specs[consumer].needs, consumer


def test_ac5_swapped_order_is_blocked_by_fact_chain():
    """AC-5：调换「建档/组网」顺序 → 规划层把组网步判为不可执行（blocked 断点）。"""
    specs = {spec.id: spec for spec in loader.load_blocks()}
    case = CaseSpec(
        id="proc_swapped", title="顺序错误（组网在建档前）",
        steps=[CaseStep(no=1, do="组网", expect="—"), CaseStep(no=2, do="建档", expect="—")],
        plan=[
            {"id": "s1", "block": "nwk.topology", "on_fail": "stop",
             "params": {"wait_s": 300},
             "expect": [{"op": "complete"},
                        {"op": "ge_field", "field": "在网从节点数", "value": "门槛"}]},
            {"id": "s2", "block": "archive.add", "on_fail": "stop",
             "expect": [{"op": "eq", "field": "写入确认", "value": "确认"}]},
        ],
    )
    # 台架事实全 yes（隔离台架）：archive.written 没有前序 provides → 探测体恒 unknown
    facts = FactsContext(probe=lambda fact_id, params: FactValue.unknown("隔离：未运行"))
    planned = plan(case, facts, block_specs=specs)
    assert planned[0].runnable is False, "组网前置 archive.written 未满足 → 不可执行"
    assert "archive.written" in planned[0].skip_reason
    assert planned[1].runnable is False, "断点下游随之跳过"

    ctx = RunContext(run_id="run-ac5", block_specs=specs, facts_ctx=facts)
    result = execute_case(case, ctx)
    assert result.verdict == "blocked", result.verdict
    assert result.counts["pass"] == 0  # 绝不假 PASS


# --------------------------------------------------------------------------- #
# AC-6：无判据不入库（loader 门禁）
# --------------------------------------------------------------------------- #


def _write(root: pathlib.Path, rel: str, payload) -> pathlib.Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


OBS_BLOCK = {
    "id": "gate.block", "title": "只观测假块",
    "kind": "case", "side_effect": "none", "version": 1,
    "observation_only": True, "wait_param": "wait_s",
    "needs": [], "provides": [],
    "action": {"kind": "noop"},
}


def test_ac6_loader_rejects_procedure_without_expect(tmp_path):
    """AC-6：引用只观测块但未声明 expect → loader 直接拒绝（无判据不入库）。"""
    _write(tmp_path, "blocks/obs.json", OBS_BLOCK)
    _write(tmp_path, "cases/no_judgement.json", {
        "id": "no_judgement", "title": "无判据流程",
        "steps": [{"no": 1, "do": "跑", "expect": "—"}],
        "plan": [{"id": "s1", "block": "gate.block", "params": {"wait_s": 5}}],
    })
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_all(tmp_path)
    assert "无判据不入库" in str(excinfo.value)


def test_ac6_loader_rejects_waiting_step_without_budget(tmp_path):
    """AC-6 + v2 拍板 #3：等待型步骤缺显式等待预算 → 视为缺判据，拒绝入库。"""
    _write(tmp_path, "blocks/obs.json", OBS_BLOCK)
    _write(tmp_path, "cases/no_budget.json", {
        "id": "no_budget", "title": "缺预算流程",
        "steps": [{"no": 1, "do": "跑", "expect": "—"}],
        "plan": [{"id": "s1", "block": "gate.block",
                  "expect": [{"op": "got"}]}],
    })
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_all(tmp_path)
    assert "等待预算" in str(excinfo.value)


def test_ac6_loader_rejects_zero_budget(tmp_path):
    """预算给了 0 也算缺（等待型步骤预算必须 > 0）。"""
    _write(tmp_path, "blocks/obs.json", OBS_BLOCK)
    _write(tmp_path, "cases/zero_budget.json", {
        "id": "zero_budget", "title": "零预算流程",
        "steps": [{"no": 1, "do": "跑", "expect": "—"}],
        "plan": [{"id": "s1", "block": "gate.block", "params": {"wait_s": 0},
                  "expect": [{"op": "got"}]}],
    })
    with pytest.raises(loader.DeclarationError):
        loader.load_all(tmp_path)


def test_ac6_proper_procedure_passes_all_gates(tmp_path):
    """对照：判据齐、预算齐的 Procedure 通过全部门禁（problems==[]）。"""
    _write(tmp_path, "blocks/obs.json", OBS_BLOCK)
    _write(tmp_path, "cases/ok.json", {
        "id": "ok", "title": "判据齐备流程",
        "kind": "regression",
        "steps": [{"no": 1, "do": "跑", "expect": "拿到观测"}],
        "plan": [{"id": "s1", "block": "gate.block", "params": {"wait_s": 30},
                  "expect": [{"op": "got"}]}],
    })
    report = loader.load_all(tmp_path)
    assert report.problems == [], report.problems
