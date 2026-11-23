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
9. **禁止项**（REQS-0042 v7 **D6-2**）：用例与方案里**不得出现**搜表块
   ``nwk.active_registration``（``11H-F5``，临时关白名单放行档案外新表，
   ``业务层/86_档案自动同步_业务.md:51-52``）与重启路由块 ``nwk.route_restart``
   （``12H-F1``，可选路由控制）——两者都不是入网手段，入网判定用 ``nwk.topology``
   （``10H-F21``，``业务层/37_双模测试用例_业务.md:42-48``）。
10. **拆案守卫**（REQS-0042 v9 **D9-1 / D9-2**）：抄表被拆成两条用例——
   ``meter_daily``（**不复位**的日常主线）与 ``meter_first_join``（**含复位**的首次组网）。
   真机实测：复位 STA 后链路恢复要十几分钟量级，而读取本身只要 2.3–2.6s；复位后
   ``nwk.topology`` 会因 CCO 节点计数残留**立刻通过**——「组网判定通过」≠「链路可用」。
   所以 ``meter_daily`` **不得**含 ``reset.sta``、``meter_first_join`` **必须**含
   ``reset.sta`` 且排第一步；旧的单条 ``meter_read`` 已删除，不得复活。
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

#: **只供 UI 单跑 / 排障**、不进任何验收方案的用例（REQS-0042 v9 D9-1 收敛后）：
#: - ``sta_reset`` / ``archive_ready`` / ``nwk_join``：三条动作级用例，块级判据与真机
#:   抽样时单跑用；抄表两条用例（``meter_daily`` / ``meter_first_join``）已内含复位 /
#:   档案 / 组网，验收方案里再带上它们只会重复复位与重复建档；
#: - ``archive_multi`` 另有 ``single-test-only`` 标签豁免（见 D3-5）。
#: 这些用例**仍在** ``data/cases/`` 里、``case_run run --case <id>`` 可直接单跑。
UI_SINGLE_RUN_CASES = frozenset({"sta_reset", "archive_ready", "nwk_join"})

#: 拆案后的用例清单与方案映射（REQS-0042 v9 **D9-1 / D9-2**）：删 ``meter_read``，
#: 新增 ``meter_daily``（不复位，日常主线）/ ``meter_first_join``（含复位，首次组网）。
EXPECTED_CASES = frozenset(
    {
        "bench_ready",
        "sta_reset",
        "archive_ready",
        "archive_multi",
        "nwk_join",
        "meter_daily",
        "meter_first_join",
    }
)

#: 方案 → 用例映射（D9-2 定稿；顺序也是契约：台架前置在前、业务主线在后）
EXPECTED_SUITES = {
    "meter_smoke_min": ["bench_ready", "meter_daily"],
    "smoke_meter": ["bench_ready", "meter_first_join"],
}

#: 抄表两条用例的块序列（D9-1 定稿；``meter_daily`` **不得**含 ``reset.sta``）
DAILY_PLAN = ["archive.add", "nwk.topology", "meter.addr_probe", "meter.read_645", "meter.read_645"]
FIRST_JOIN_PLAN = [
    "reset.sta", "archive.add", "nwk.topology", "meter.addr_probe",
    "meter.read_645", "meter.read_645",
]

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

#: **禁止进用例 / 方案**的块（REQS-0042 v7 D6-2「禁止出现搜表」）：
#: - ``nwk.active_registration``（11H-F5）= **搜表**：临时关闭白名单过滤、放行档案外新表
#:   上报 06H-F4 / 06H-F1（权威出处 ``业务层/86_档案自动同步_业务.md:51-52``）。
#:   它是「发现档案外电表」的业务，**不是入网手段**；
#: - ``nwk.route_restart``（12H-F1）= 可选的路由控制命令，也不是入网手段
#:   （入网判定用 10H-F21 查网络拓扑，``业务层/37_双模测试用例_业务.md:42-48``）。
#: 块声明本身保留（供其它场景/单测用），但用例与方案里一律不得出现。
BANNED_CASE_BLOCKS = ("nwk.active_registration", "nwk.route_restart")

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
    """用例/方案的数量与**清单**都钉死：删改用例必须同步本清单（防止悄悄消失）。

    v9 D9-1 拆案后是 7 条用例 / 2 个方案：``meter_read`` 已删除，换成
    ``meter_daily``（不复位）+ ``meter_first_join``（含复位）。
    """
    report = _report()
    case_ids = {case.id for case in report.cases}
    assert case_ids == set(EXPECTED_CASES), (
        "用例清单变了：缺 %s，多 %s（v9 D9-1 定稿清单见 EXPECTED_CASES）"
        % (sorted(set(EXPECTED_CASES) - case_ids), sorted(case_ids - set(EXPECTED_CASES)))
    )
    assert "meter_read" not in case_ids, (
        "meter_read 已按 v9 D9-1 拆成 meter_daily / meter_first_join，不得复活"
    )
    suite_ids = {suite.id for suite in report.suites}
    assert suite_ids == set(EXPECTED_SUITES), (
        "方案清单变了：期望 %s，实际 %s" % (sorted(EXPECTED_SUITES), sorted(suite_ids))
    )


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


def test_every_case_belongs_to_a_suite_or_is_ui_single_run() -> None:
    """每条用例要么进验收方案，要么是**已知的** UI 单跑用例（不许悄悄变孤儿）。

    两类豁免：
    - ``single-test-only`` 标签：``archive_multi`` 故意不进方案（真机只有 1 块真表），
      由单测证明「同块多 step、各带不同参数」（0042 D3-5）；
    - ``UI_SINGLE_RUN_CASES``：``sta_reset`` / ``archive_ready`` / ``nwk_join`` 三条动作级
      用例，v9 D9-1 把复位/档案/组网收进抄表两条用例（``meter_daily`` /
      ``meter_first_join``）之后不再进验收方案，保留供单跑与真机抽样；进方案会让同一次
      回归重复复位（≈30s）与重复建档。
    """
    used: Set[str] = set()
    for suite in loader.load_suites():
        used.update(suite.case_ids)
    exempt = {
        case.id
        for case in loader.load_cases()
        if SINGLE_TEST_ONLY_TAG in case.tags
    } | set(UI_SINGLE_RUN_CASES)
    orphans = {case.id for case in loader.load_cases()} - used - exempt
    assert orphans == set(), (
        f"这些用例既没进方案、也不在已知的单跑清单里：{sorted(orphans)}；"
        f"若确属单测专用，请打上 {SINGLE_TEST_ONLY_TAG!r} 标签；"
        f"若是新加的单跑用例，请显式登记到 UI_SINGLE_RUN_CASES"
    )
    # 反向：登记为单跑却仍出现在方案里 → 清单过期，同样要修
    stale = set(UI_SINGLE_RUN_CASES) & used
    assert stale == set(), f"这些用例已登记为单跑，却又出现在方案里：{sorted(stale)}"


def test_suites_are_bench_precondition_then_one_meter_case() -> None:
    """v9 D9-2 定稿的方案形态：**台架前置（只读自检）→ 一条抄表用例**，首个非通过即停。

    两条方案对应两种节奏，不能混：
    - ``meter_smoke_min`` → ``meter_daily``：**不复位**的日常主线；
    - ``smoke_meter`` → ``meter_first_join``：**含复位**的首次组网（用户原话「第一次组网
      需要复位 sta。之后就按需复位。」）。
    历史方案 ``meter_only`` / ``smoke_meter_noreset`` 已删除：抄表用例自含档案 + 组网，
    「不复位 / 不组网」这层语义在 ``meter_daily`` 之后已由方案映射直接表达，留着只会重复复位。
    """
    suites = {suite.id: suite for suite in loader.load_suites()}
    assert set(suites) == set(EXPECTED_SUITES), sorted(suites)
    for suite_id, expected_case_ids in EXPECTED_SUITES.items():
        suite = suites[suite_id]
        assert suite.case_ids == expected_case_ids, (suite_id, suite.case_ids)
        assert suite.stop_on_fail is True
        assert "台架前置" in suite.notes, "notes 要写清 bench_ready 只做台架前置、不是业务动作"

    # 日常主线（不复位）与首次组网（含复位）的语义必须在 notes 里说清
    daily_notes = suites["meter_smoke_min"].notes
    assert "不复位" in daily_notes and "日常" in daily_notes, daily_notes[:80]
    join_notes = suites["smoke_meter"].notes
    assert "第一次组网需要复位 sta。之后就按需复位。" in join_notes, (
        "smoke_meter 的 notes 要保留用户原话「第一次组网需要复位 sta。之后就按需复位。」"
    )
    assert "十几分钟" in join_notes, (
        "smoke_meter 的 notes 要写明真机教训：复位后链路恢复要十几分钟量级（D9-1）"
    )

    # 旧方案不得复活（语义已被 meter_daily / meter_first_join 取代）
    for retired in ("meter_only", "smoke_meter_noreset"):
        assert retired not in suites, f"{retired} 已按 D9-1/D9-2 删除，请勿恢复"


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
# 8. 步骤实例化资产（REQS-0042 S1.5 / v9 D9-1）：meter_daily / meter_first_join / archive_multi
# --------------------------------------------------------------------------- #


def test_meter_smoke_min_is_the_daily_mainline() -> None:
    """``meter_smoke_min`` = ``bench_ready`` + ``meter_daily``：**不复位**的日常主线（5 步）。

    旧答案是 ``["bench_ready", "meter_read"]``（含复位 → 档案 → 组网 → 绑表 → 抄表）。
    真机实测（D9-1）：复位后链路恢复要十几分钟量级，而读取本身只要 2.3–2.6s ——
    复位不能塞进日常回归，所以日常主线换成 ``meter_daily``（4 动作 5 步，不复位），
    首次组网走 ``smoke_meter``（``meter_first_join``）。``bench_ready`` 的两个 fix 块负责
    台架前置（simcon / simeter 会话 + 开表口），``archive.add`` 是 ``meter_daily`` 的 s1。
    """
    suites = {suite.id: suite for suite in loader.load_suites()}
    assert "meter_smoke_min" in suites, "缺少抄表最小冒烟方案 meter_smoke_min"
    suite = suites["meter_smoke_min"]
    assert suite.case_ids == ["bench_ready", "meter_daily"], suite.case_ids
    assert suite.stop_on_fail is True
    assert "台架前置" in suite.notes, "notes 要写清 bench_ready 只做台架前置（simcon/simeter 会话 + 开表口）"
    assert "日常" in suite.notes, "notes 要写清这是日常回归主线"
    assert "nwk.topology" in suite.notes, "notes 要交代组网等待的预算口径（300s 上限）"
    assert "600s" in suite.notes, "notes 要写出与整轮 600s 上限的关系"

    cases = {case.id: case for case in loader.load_cases()}
    daily = cases["meter_daily"]
    assert [step.block for step in daily.plan] == DAILY_PLAN
    assert "reset.sta" not in {step.block for step in daily.plan}, (
        "meter_daily 是日常主线，不得含 reset.sta（复位只属于 meter_first_join）"
    )


def test_meter_cases_split_reset_guard() -> None:
    """★ **拆案守卫**（REQS-0042 v9 D9-1）：日常不复位、首次组网必复位。

    直接读 ``data/cases/*.json`` **原文**断言（不经模型）：``meter_daily.json`` 里出现
    ``reset.sta`` 即违规（复位是物理副作用，且复位后链路要十几分钟才恢复）；
    ``meter_first_join.json`` 里必须有 ``reset.sta``，且是 plan 的第一块（复位排在建档
    与组网之前）。
    """
    daily_raw = _read_json(DATA_DIR / loader.CASES_DIRNAME / "meter_daily.json")
    join_raw = _read_json(DATA_DIR / loader.CASES_DIRNAME / "meter_first_join.json")

    assert "reset.sta" not in json.dumps(daily_raw, ensure_ascii=False), (
        "meter_daily.json 出现了 reset.sta：日常主线不复位（D9-1）"
    )
    daily_blocks = [step["block"] for step in daily_raw["plan"]]
    assert daily_blocks == DAILY_PLAN, daily_blocks
    assert daily_raw["needs"] == ["bench.simcon_online"], (
        "不复位就不该依赖 bench.sta_online：%s" % daily_raw["needs"]
    )

    join_blocks = [step["block"] for step in join_raw["plan"]]
    assert join_blocks == FIRST_JOIN_PLAN, join_blocks
    assert join_blocks[0] == "reset.sta", (
        "meter_first_join 必须复位，且复位是第一步（先复位再建档组网）"
    )
    assert set(join_raw["needs"]) == {"bench.sta_online", "bench.simcon_online"}, join_raw["needs"]

    # 读步的等待预算（真机实测：复位后链路恢复≈10 分钟、冷启动有瞬态）——
    # 只允许出现在读步，且组网步的 wait_s 必须保持块声明默认（不得压小）
    expected_wait = {
        "meter_daily": {"s3": 60, "s4": 30, "s5": 30},
        "meter_first_join": {"s4": 180, "s5": 30, "s6": 30},
    }
    for raw, label in ((daily_raw, "meter_daily"), (join_raw, "meter_first_join")):
        got = {s["id"]: (s.get("params") or {}).get("wait_s", 0) for s in raw["plan"]}
        for step_id, want in expected_wait[label].items():
            assert got.get(step_id) == want, (
                "%s 的 %s 等待预算应为 %ss，实际 %r" % (label, step_id, want, got.get(step_id))
            )
        for step_id, value in got.items():
            if step_id not in expected_wait[label]:
                assert value == 0, "%s 的 %s 不该写 wait_s（组网等块用声明默认）" % (label, step_id)

    # 两条用例的人读说明步数与 plan 步数一致
    assert len(daily_raw["steps"]) == len(daily_blocks) == 5
    assert len(join_raw["steps"]) == len(join_blocks) == 6


def test_sampling_blocks_have_no_case_entry_gap_is_recorded() -> None:
    """**已知缺口（照实记录，别偷偷塞回 nwk_join）**：

    ``nwk.nodes_online``（``10H-F4``+``10H-F2``）与 ``nwk.route_status``（``10H-F4``）
    现在**只有块级单测**（``blocks/test_archive_nwk.py``），**没有任何用例 / 方案入口**
    —— 真机抽样证据待补（S6 收口时用一条单独的只读探测用例补，不塞回 ``nwk_join``：
    三者语义是同一件事的三个视角，拓扑通过后另两个单次探测必然已满足，且三块等待
    默认值叠加最坏 780s 会顶穿整轮 600s 上限）。

    这条测试的作用：**让缺口在每次跑测试时都可见**，而不是被悄悄遗忘。
    """
    gap_blocks = ("nwk.nodes_online", "nwk.route_status")
    used: Set[str] = set()
    for case in loader.load_cases():
        used.update(case.blocks)
        used.update(step.block for step in case.plan)
    assert set(gap_blocks) & used == set(), (
        "真机抽样块不该进用例（等待预算会叠加）：%s" % sorted(set(gap_blocks) & used)
    )
    for block_id in gap_blocks:
        assert block_id in _known_block_ids(), f"{block_id} 块声明应保留（只是没有用例入口）"


def test_read_steps_pin_expect_timeout_in_both_meter_cases() -> None:
    """抄表两条用例的三个读步都显式钉 ``expect_timeout``：等待预算可控（REQS D5-2）。"""
    cases = {case.id: case for case in loader.load_cases()}
    for case_id, read_ids, plain_ids in (
        ("meter_daily", ("s3", "s4", "s5"), ("s1", "s2")),
        ("meter_first_join", ("s4", "s5", "s6"), ("s1", "s2", "s3")),
    ):
        steps = {step.id: step for step in cases[case_id].plan}
        for step_id in read_ids:
            assert steps[step_id].params.get("expect_timeout") == 15, (
                f"{case_id} 的 {step_id} 必须显式钉 expect_timeout=15（实测单数据项往返 "
                "2.3–2.6s 的 6 倍余量；显式值下块不再查 03H-F7，避免按 t+5s 空等 95s）"
            )
        for step_id in plain_ids:
            assert "expect_timeout" not in steps[step_id].params, (
                f"{case_id} 的 {step_id} 保持块声明默认（组网 300s 不压）"
            )


def test_cases_and_suites_use_no_search_meter_or_route_restart() -> None:
    """**禁止项**（REQS-0042 v7 D6-2）：用例与方案里不得出现搜表块与重启路由块。

    直接读原文断言（不经模型，避免被默认值/别名洗掉）：``data/cases`` 与
    ``data/case_suites`` 两个目录下，任何一个文件里出现 ``nwk.active_registration``
    （11H-F5 搜表：临时关白名单放行档案外新表，``业务层/86:51-52``）或
    ``nwk.route_restart``（12H-F1 重启路由）都算违规 —— 两者都不是入网手段，
    入网判定只认 ``nwk.topology``（10H-F21）。
    """
    offenders: List[str] = []
    for dirname in (loader.CASES_DIRNAME, loader.SUITES_DIRNAME):
        for path in _json_files(dirname):
            text = path.read_text(encoding="utf-8")
            for block_id in BANNED_CASE_BLOCKS:
                if block_id in text:
                    offenders.append(f"{dirname}/{path.name}: 出现禁止块 {block_id!r}")
    assert offenders == [], (
        "用例/方案里出现了搜表块或重启路由块（v7 D6-2 禁止；块声明保留但只能供其它场景用）：\n"
        + "\n".join(offenders)
    )


def test_search_meter_block_is_declared_but_marked_not_for_cases() -> None:
    """搜表块本身保留，但必须被标明「不是入网手段、禁止进用例」（D6-2 / D6-3）。"""
    path = DATA_DIR / loader.BLOCKS_DIRNAME / "nwk_active_registration.json"
    raw = _read_json(path)
    tags = set(raw.get("tags") or [])
    assert {"search-meter", "not-for-cases"} <= tags, tags
    title = str(raw.get("title") or "")
    for needle in ("搜表", "11H-F5", "不是入网手段"):
        assert needle in title, "title 要写清%s：%r" % (needle, title)


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
