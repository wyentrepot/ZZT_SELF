"""用例（Case）与方案（Suite）声明的**静态校验**（REQS-0041 §10.5 CS-1 / SU-1）。

本文件只做「声明是否自洽」的检查，**不含任何执行逻辑**：不连台架、不发帧、不调块执行体。
执行语义由 ``core/executor.py`` 与 ``blocks/*.py`` 负责，其测试在各自文件里。

校验项（每条对应一个锚点）：

1. ``loader.load_all(register_modules=True, require_registered=True).problems == []``
   —— 块/事实/用例/方案四类声明全部可加载，且执行体已注册（KE-1）。
2. ``CaseSpec.blocks`` 引用的块 id **都存在**（声明集合 ∪ 注册表）。
3. ``CaseSpec.needs`` 引用的事实 id **都存在**（声明集合 ∪ 注册表）。
4. ``CaseSpec.steps`` 非空，且每项 ``do`` / ``expect`` 均有内容（CS-1：UI 展示口径）。
5. ``SuiteSpec.case_ids`` 引用的用例 **都存在**，且无重复；每条用例至少进一个方案。
6. **禁止项**：任何块的 ``provides`` 不得引用**参数化事实**（``parameterized: true``
   显式标记，兼容旧写法 ``required_params(...)`` 非空，如 ``archive.contains`` 按表地址、
   ``nwk.onnet_count_min`` / ``nwk.nodes_online_min`` 按 ``min`` 求值）——
   块回写事实缓存只按 ``fact_id``，跨参数会命中造成假 PASS（REQS §10.13 缺陷 3）。
   0042 D2-2 起参数都有默认值（``"default": null`` 取 ``case_defaults.json``），
   **必需参数可能为空**，所以「参数化」的权威口径是显式标记（``loader.is_parameterized``）。
7. **禁止项**（CS-1）：用例 JSON 里不得出现帧 hex、不得出现参数计算 —— 帧细节归块，
   用例只是编排（``needs[]`` + ``blocks[]`` / ``plan[]`` + ``steps[]``）。
8. **步骤实例化**（REQS-0042 D3-1）：``blocks[]`` 与 ``plan[]`` 至少给一个；``plan`` 内
   step id 唯一、引用的块存在；单测专用用例（``single-test-only``）可不进方案。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set

import pytest

from case_runner.blocks import loader
from case_runner.core.registry import list_blocks, list_facts

#: 包内数据根（``libs/case_runner/data``）
DATA_DIR = Path(loader.DATA_DIR)

#: 第一批必须齐备的块（REQS §10.11 + §10.13 裁决口径：12 块 + 新增 ``reset.sta`` = 13）
FIRST_BATCH_BLOCKS = frozenset(
    {
        "fix.simcon_session",
        "fix.simeter_session",
        "reset.sta",
        "archive.add",
        "archive.del",
        "archive.query",
        "nwk.active_registration",
        "nwk.onnet_count",
        "nwk.route_restart",
        "nwk.route_status",
        "meter.addr_probe",
        "meter.read_645",
        "meter.brain_local",
    }
)

#: 第一批必须齐备的 7 个事实（§10.11 的 6 个 + §10.13 新增 ``bench.sta_online``）
FIRST_BATCH_FACTS = frozenset(
    {
        "bench.simcon_online",
        "bench.simeter_online",
        "bench.sta_online",
        "archive.contains",
        "nwk.onnet_count_min",
        "nwk.route_running",
        "sta.meter_addr_known",
    }
)

#: 「参数化事实」四个（结论随入参变化，REQS-0042 D2-1 定稿）：
#: ``archive.contains`` / ``sta.meter_addr_known`` 按表地址求值，
#: ``nwk.onnet_count_min`` / ``nwk.nodes_online_min`` 按 ``min`` 求值。
#: 0042 D2-2 起这些事实的参数都有默认值（``"default": null`` 取 ``case_defaults.json``），
#: 所以判据**不能**只看 ``required: true``，要用 ``loader.is_parameterized``
#: （显式 ``parameterized: true`` ∪ 必需参数），否则门禁会变成空转。
#: ``sta.meter_addr_known`` 原先因「是 ``meter.addr_probe`` 的 provides 目标」暂缓裁决，
#: 主代理已裁决：**按表地址求值即参数化事实**（声明已标 ``parameterized: true``），
#: 对应地把 ``meter.addr_probe`` 的 ``provides`` 置空（块回写只按 fact_id 缓存 → 换地址假 PASS）。
PARAMETERIZED_FACTS = frozenset(
    {
        "archive.contains",
        "sta.meter_addr_known",
        "nwk.onnet_count_min",
        "nwk.nodes_online_min",
    }
)

#: 1376.2 帧的粗略特征：``68`` + 至少 8 个 hex 字符（用例里出现即违规）
FRAME_HEX_RE = re.compile(r"(?i)\b68[0-9a-f]{8,}")
#: 十六进制字面量 / 参数计算的痕迹
HEX_LITERAL_RE = re.compile(r"(?i)\b0x[0-9a-f]+|\bhex\b")
#: 参数计算痕迹（用例里不该出现算术表达式）
ARITHMETIC_RE = re.compile(r"\d\s*[-+*/%]\s*\d")


# --------------------------------------------------------------------------- #
# 夹具与工具
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module", autouse=True)
def _registered() -> None:
    """确保领域模块已注册（其它测试可能 ``clear_registries()`` 过）。"""
    loader.register_domain_modules(strict=False)


def _report() -> loader.LoadReport:
    return loader.load_all(register_modules=True, require_registered=True)


def _json_files(dirname: str) -> List[Path]:
    return loader.iter_declarations(DATA_DIR / dirname)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _known_block_ids() -> Set[str]:
    return {spec.id for spec in loader.load_blocks()} | set(list_blocks())


def _known_fact_ids() -> Set[str]:
    return {spec.id for spec in loader.load_facts()} | set(list_facts())


# --------------------------------------------------------------------------- #
# 1. 全量加载：四类声明 + 执行体注册
# --------------------------------------------------------------------------- #


def test_load_all_has_no_problems() -> None:
    report = _report()
    assert report.problems == [], f"声明问题：\n" + "\n".join(report.problems)
    assert report.ok is True
    assert report.register is not None and report.register.ok is True


def test_first_batch_inventory_is_declared() -> None:
    """第一批 13 块 + 7 事实必须在声明里齐备（后续批次只增不减）。"""
    report = _report()
    declared_blocks = {spec.id for spec in report.blocks}
    declared_facts = {spec.id for spec in report.facts}
    assert FIRST_BATCH_BLOCKS - declared_blocks == set(), (
        "缺少块声明：%s" % sorted(FIRST_BATCH_BLOCKS - declared_blocks)
    )
    assert FIRST_BATCH_FACTS - declared_facts == set(), (
        "缺少事实声明：%s" % sorted(FIRST_BATCH_FACTS - declared_facts)
    )


def test_cases_and_suites_are_not_empty() -> None:
    report = _report()
    assert len(report.cases) >= 5, "第一批至少 5 条用例（台架/复位/档案/组网/抄表）"
    assert len(report.suites) >= 2, "至少 2 个方案（抄表冒烟 / 只抄表）"


# --------------------------------------------------------------------------- #
# 2/3. 引用完整性：blocks[] → 块，needs[] → 事实
# --------------------------------------------------------------------------- #


def test_case_block_refs_all_exist() -> None:
    known_blocks = _known_block_ids()
    assert FIRST_BATCH_BLOCKS <= known_blocks, "第一批块执行体未全部注册"
    for case in loader.load_cases():
        missing = [block_id for block_id in case.blocks if block_id not in known_blocks]
        assert missing == [], (
            f"用例 {case.id!r} 引用了不存在的块：{missing}（已声明/已注册：{sorted(known_blocks)}）"
        )


def test_case_needs_refs_all_exist() -> None:
    known_facts = _known_fact_ids()
    for case in loader.load_cases():
        missing = [fact_id for fact_id in case.needs if fact_id not in known_facts]
        assert missing == [], (
            f"用例 {case.id!r} 的 needs 引用了不存在的事实：{missing}"
            f"（已声明/已注册：{sorted(known_facts)}）"
        )


def test_case_needs_do_not_contain_parameterized_facts() -> None:
    """用例级 needs 会在任何块之前探测，带参事实拿不到参数 → 只会自锁成 unknown。

    参数化事实（``archive.contains(addr)`` / ``nwk.onnet_count_min(min)``）必须由块内的
    判据直接断言，不能写进 ``CaseSpec.needs``（``needs`` 只是 fact_id 字符串，无参数位）。
    """
    for case in loader.load_cases():
        offenders = [fact_id for fact_id in case.needs if fact_id in PARAMETERIZED_FACTS]
        assert offenders == [], (
            f"用例 {case.id!r} 的 needs 含参数化事实 {offenders}："
            "用例级 needs 无参数位，会以空参数探测并自锁为 unknown；"
            "请把该判据交给块（如 archive.add 的双判据）"
        )


# --------------------------------------------------------------------------- #
# 4. steps[]：UI 展示口径（用例名 + 步骤说明）
# --------------------------------------------------------------------------- #


def test_cases_have_non_empty_steps() -> None:
    for case in loader.load_cases():
        assert case.steps, f"用例 {case.id!r} 的 steps 为空"
        for step in case.steps:
            assert step.do.strip(), f"用例 {case.id!r} 第 {step.no} 步缺少 do"
            assert step.expect.strip(), f"用例 {case.id!r} 第 {step.no} 步缺少 expect"


def test_case_step_numbers_are_sequential_and_unique() -> None:
    for case in loader.load_cases():
        numbers = [step.no for step in case.steps]
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"用例 {case.id!r} 的步骤编号不是 1..N 连续：{numbers}"
        )


def test_case_ids_are_unique_and_match_file_name() -> None:
    """id 唯一由 loader 保证；再要求「文件名 = id.json」，避免方案/结果里对不上人眼。"""
    for path in _json_files(loader.CASES_DIRNAME):
        raw = _read_json(path)
        assert raw["id"] == path.stem, (
            f"{path.name}: 用例 id {raw['id']!r} 与文件名不一致（约定 {path.stem}.json）"
        )


# --------------------------------------------------------------------------- #
# 5. suite：case_ids[] → 用例
# --------------------------------------------------------------------------- #


def test_suite_case_refs_all_exist() -> None:
    known_cases = {case.id for case in loader.load_cases()}
    for suite in loader.load_suites():
        missing = [cid for cid in suite.case_ids if cid not in known_cases]
        assert missing == [], (
            f"方案 {suite.id!r} 引用了不存在的用例：{missing}（已声明：{sorted(known_cases)}）"
        )


def test_suite_case_ids_are_unique() -> None:
    for suite in loader.load_suites():
        assert len(set(suite.case_ids)) == len(suite.case_ids), (
            f"方案 {suite.id!r} 的 case_ids 有重复：{suite.case_ids}"
        )


def test_suite_file_name_and_notes() -> None:
    """方案文件名 = id.json；``notes`` 必须写清为什么这样排（用户口径：第一次组网需复位）。"""
    for path in _json_files(loader.SUITES_DIRNAME):
        raw = _read_json(path)
        assert raw["id"] == path.stem, (
            f"{path.name}: 方案 id {raw['id']!r} 与文件名不一致（约定 {path.stem}.json）"
        )
        assert str(raw.get("notes", "")).strip(), f"{path.name}: 方案缺少 notes（排序理由）"


#: 只用于单测、**不进任何方案**的用例标签（REQS-0042 D3-5）
#: 「台架只有 1 块真表，多 step 多地址只用单测证明」——这类用例若进了方案，
#: 真机跑必然出现「第二块表不存在」的 fail，把验收结论污染成假失败。
SINGLE_TEST_ONLY_TAG = "single-test-only"


def test_every_case_belongs_to_a_suite() -> None:
    """每条用例都要能「按方案跑」——单测专用用例除外（``single-test-only`` 标签）。

    0042 D3-5 起豁免 ``single-test-only``：``archive_multi`` 故意不进方案
    （真机只有 1 块真表），它由单测（``test_planner`` / ``test_executor``）证明
    「同块多 step、各带不同参数」这件事，不该出现在验收方案里。
    """
    used: Set[str] = set()
    for suite in loader.load_suites():
        used.update(suite.case_ids)
    single_test_only = {
        case.id
        for case in loader.load_cases()
        if SINGLE_TEST_ONLY_TAG in case.tags
    }
    orphans = {case.id for case in loader.load_cases()} - used - single_test_only
    assert orphans == set(), (
        f"这些用例没有被任何方案引用（UI 里只能临时单跑）：{sorted(orphans)}；"
        f"若确属单测专用，请打上 {SINGLE_TEST_ONLY_TAG!r} 标签"
    )


def test_required_suite_order_and_stop_on_fail() -> None:
    """两个必备方案的顺序即「前提链」：档案 → 组网 → 抄表；首个非通过即停。"""
    suites = {suite.id: suite for suite in loader.load_suites()}
    assert "smoke_meter" in suites, "缺少抄表冒烟方案 smoke_meter"
    assert "meter_only" in suites, "缺少只抄表方案 meter_only"

    smoke = suites["smoke_meter"]
    assert smoke.stop_on_fail is True
    assert "sta_reset" in smoke.case_ids, "第一次组网必须复位 STA（用户口径）"
    assert smoke.case_ids.index("archive_ready") < smoke.case_ids.index("nwk_join"), (
        "档案必须先于组网"
    )
    assert smoke.case_ids.index("nwk_join") < smoke.case_ids.index("meter_read"), (
        "组网必须先于抄表"
    )
    assert smoke.case_ids.index("sta_reset") < smoke.case_ids.index("archive_ready"), (
        "复位必须先于建档"
    )

    meter_only = suites["meter_only"]
    assert meter_only.stop_on_fail is True
    assert "sta_reset" not in meter_only.case_ids, (
        "只抄表方案按需复位：不带 sta_reset（用户口径「之后就按需复位」）"
    )
    assert "archive_ready" in meter_only.case_ids and "meter_read" in meter_only.case_ids


# --------------------------------------------------------------------------- #
# 6. 禁止项：块 provides 不得引用参数化事实
# --------------------------------------------------------------------------- #


def _parameterized_fact_ids() -> Set[str]:
    """求出「参数化事实」集合：显式 ``parameterized: true`` **∪** 有必需参数的旧写法。

    口径与 ``loader.is_parameterized`` 一致（0042 D2-2：参数都有默认值之后，
    必需参数可能为空，只按 required 反推会让禁止项检查空转）。
    """
    found = set()
    for path in _json_files(loader.FACTS_DIRNAME):
        raw = _read_json(path)
        spec = loader.load_facts()[0].__class__.model_validate(raw)
        if loader.is_parameterized(spec):
            found.add(raw["id"])
    return found


def test_parameterized_fact_set_is_not_empty() -> None:
    """守住「禁止项检查本身不会空转」：参数化事实必须被识别出来。"""
    assert PARAMETERIZED_FACTS <= _parameterized_fact_ids(), (
        "参数化事实识别失败（``parameterized: true`` 与 ``required: true`` 都没解析到），"
        "禁止项检查会变成空转"
    )


def test_block_provides_never_reference_parameterized_facts() -> None:
    """直接读 ``data/blocks/*.json`` 原文断言（不经模型，防止默认值把违规洗掉）。"""
    parameterized = _parameterized_fact_ids()
    offenders: List[str] = []
    for path in _json_files(loader.BLOCKS_DIRNAME):
        raw = _read_json(path)
        for fact_id in raw.get("provides") or []:
            if fact_id in parameterized:
                offenders.append(f"{path.name}: block {raw.get('id')!r} provides {fact_id!r}")
    assert offenders == [], (
        "块 provides 引用了参数化事实（块回写只按 fact_id 缓存，跨参数会假 PASS）：\n"
        + "\n".join(offenders)
    )


def test_loader_rejects_parameterized_provides() -> None:
    """同一禁止项的结构门禁在 loader 里也必须生效（不是只有测试在挡）。"""
    block_specs = loader.load_blocks()
    fact_specs = loader.load_facts()
    problems = loader.check_provides_parameterized(block_specs, fact_specs)
    assert problems == [], problems

    polluted = [spec.model_copy(update={"provides": ["archive.contains"]}) for spec in block_specs][:1]
    caught = loader.check_provides_parameterized(polluted, fact_specs)
    assert caught, "loader 未拦住 provides 引用参数化事实（结构门禁失效）"


# --------------------------------------------------------------------------- #
# 7. 禁止项（CS-1）：用例 JSON 不得出现帧 hex / 参数计算
# --------------------------------------------------------------------------- #


def test_cases_carry_no_frame_hex_or_arithmetic() -> None:
    """用例只做编排：帧 hex、十六进制字面量、参数计算一律归块，不进用例 JSON。"""
    offenders: List[str] = []
    for path in _json_files(loader.CASES_DIRNAME):
        text = path.read_text(encoding="utf-8")
        for label, pattern in (
            ("帧 hex", FRAME_HEX_RE),
            ("hex 字面量", HEX_LITERAL_RE),
            ("参数计算", ARITHMETIC_RE),
        ):
            for match in pattern.finditer(text):
                offenders.append(f"{path.name}: 出现{label} {match.group(0)!r}")
    assert offenders == [], "\n".join(offenders)


def test_cases_only_use_declared_fields() -> None:
    """用例 JSON 的字段白名单（``CaseSpec`` 是 ``extra='forbid'``，这里再从文件侧确认）。

    ``plan`` 是 REQS-0042 D3-1 新增的步骤实例化字段（``blocks`` 起变为可选），
    两种编排写法共用同一份白名单。
    """
    allowed = {"id", "title", "steps", "needs", "blocks", "plan", "asserts", "tags"}
    for path in _json_files(loader.CASES_DIRNAME):
        extra = set(_read_json(path)) - allowed
        assert extra == set(), f"{path.name}: 出现未知字段 {sorted(extra)}"


def test_case_blocks_are_strings_not_inline_params() -> None:
    """``blocks[]`` 只能是块 id 字符串；写成对象（内联参数）说明有人想在用例里塞帧/参数。"""
    for path in _json_files(loader.CASES_DIRNAME):
        raw = _read_json(path)
        for entry in raw.get("blocks") or []:
            assert isinstance(entry, str), (
                f"{path.name}: blocks 里出现非字符串项 {entry!r} —— "
                "块参数属于块声明，用例只写块 id"
            )


# --------------------------------------------------------------------------- #
# 8. 步骤实例化资产（REQS-0042 S1.5）：meter_smoke_min / archive_multi
# --------------------------------------------------------------------------- #


def test_meter_smoke_min_is_the_zero_intervention_mainline() -> None:
    """``meter_smoke_min``：≈40s 一轮、不复位不组网，是「零参与」验收主线（D3-4）。"""
    suites = {suite.id: suite for suite in loader.load_suites()}
    assert "meter_smoke_min" in suites, "缺少零参与验收主线方案 meter_smoke_min"
    suite = suites["meter_smoke_min"]
    assert suite.case_ids == ["bench_ready", "archive_ready", "meter_read"]
    assert suite.stop_on_fail is True
    assert "40s" in suite.notes, "notes 要写清一轮耗时（≈40s）"
    assert "不复位不组网" in suite.notes
    assert "sta_reset" not in suite.case_ids and "nwk_join" not in suite.case_ids


def test_archive_multi_case_is_plan_only_and_single_test_only() -> None:
    """``archive_multi``：同块两次实例化、各带不同表地址；只用于单测，不进方案（D3-5）。"""
    cases = {case.id: case for case in loader.load_cases()}
    assert "archive_multi" in cases, "缺少步骤实例化演示用例 archive_multi"
    case = cases["archive_multi"]
    assert case.uses_plan is True and case.blocks == []
    assert [step.block for step in case.plan] == ["archive.add", "archive.add"]
    addrs = [step.params.get("meter_addr") for step in case.plan]
    assert addrs == ["000000000011", "000000000022"], addrs
    assert "single-test-only" in case.tags, "必须标 single-test-only（台架只有 1 块真表）"
    assert any("真机不跑" in step.expect or "真机不跑" in step.do for step in case.steps), (
        "steps 里要写清「台架只有 1 块真表、真机不跑本用例」"
    )
    for suite in loader.load_suites():
        assert "archive_multi" not in suite.case_ids, (
            f"方案 {suite.id!r} 不能引用 archive_multi：真机只有 1 块真表，"
            "跑起来必然出现「第二块表不存在」的假失败"
        )
