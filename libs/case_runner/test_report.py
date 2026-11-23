"""S6-A 报告层测试（REQS-0042 v11 D11-1/D11-2/D11-3）。

用户口径（2026-11-23 直批）：
> 「用例库测试完成之后直接生成报告即可，比如抄表日常回归（PASS），
>  失败的话就是（组网超时，档案下发异常）等等，然后把日志或者证据路径给列出来即可，
>  **尤其是列出 archive.add 这种名字，压根无法被用户理解**。」

所以本文件守四件事：
1. **16 块全覆盖**映射成业务动作，且**任何输出里都不许出现块 id**（D11-2 是硬约束）；
2. 四类失败原因（超时 / 判据不成立 / 台架不具备 / 说不清）各给人话短语；
3. 四种 verdict 的一句话 headline 与 REQS 例子逐字一致；
4. 空输入 / 未知块 id 不崩、不假 pass、不编证据路径。

测试全绿的前提是 ``report.py`` 的纯函数层：无台架交互、无真实等待。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from case_runner.core import report
from case_runner.core.report import (
    action_label,
    classify_failure,
    contains_block_id,
    summarize_case,
    summarize_run,
)

#: 16 块的业务动作映射（REQS D11-2 逐条点名的那几个必须在这里躺着）
EXPECTED_ACTIONS = {
    "fix.simcon_session": "台架自检（模拟集中器）",
    "fix.simeter_session": "台架自检（模拟电表开口）",
    "reset.sta": "复位",
    "archive.add": "档案下发",
    "archive.del": "档案删除",
    "archive.query": "档案查询",
    "nwk.topology": "组网",
    "nwk.nodes_online": "在网节点检查",
    "nwk.onnet_count": "在网数核对",
    "nwk.archive_count": "档案数核对",
    "nwk.route_restart": "路由重启",
    "nwk.route_status": "路由状态检查",
    "meter.addr_probe": "绑表（取表地址）",
    "meter.read_645": "抄表",
    "meter.brain_local": "表端自检",
    "nwk.active_registration": "主动注册（搜表）",
}

#: 块声明目录（只读：用来守「映射不许和声明漂移」）
BLOCKS_DIR = Path(__file__).resolve().parent / "data" / "blocks"

#: 块 id 形状（与 report.py 的守卫同一口径；命中即泄漏）
BLOCK_ID_RE = re.compile(r"(?<![A-Za-z0-9_])[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(?![A-Za-z0-9_])")

#: 不是块 id 的「小写.小写」token（文件后缀），守卫必须放行
FILE_SUFFIXES = ("json", "jsonl", "log", "txt", "md", "py", "yaml", "yml", "csv", "html")


def _block(block_id: str, state: str, detail: str = "") -> dict:
    """构造一条 BlockRecord.to_dict() 形状的记录（只保留报告层会看的键）。"""

    return {
        "block_id": block_id,
        "step_id": block_id,
        "state": state,
        "detail": detail,
    }


def _case(case_id: str, verdict: str = "", blocks=(), title: str = "") -> dict:
    record = {"case_id": case_id, "title": title, "blocks": list(blocks)}
    if verdict:
        record["verdict"] = verdict
    return record


def _assert_no_leak(*texts: str) -> None:
    """D11-2 守卫：给定文本里出现任何块 id → 测试直接红。"""

    for text in texts:
        assert not contains_block_id(text), "块 id 泄漏给用户了：%r" % (text,)
        for match in BLOCK_ID_RE.finditer(text or ""):
            token = match.group(0)
            assert token.rsplit(".", 1)[1] in FILE_SUFFIXES, "块 id 泄漏给用户了：%r" % (token,)


# --------------------------------------------------------------------------- #
# 1. 块 → 业务动作（16 块全覆盖 + 未知块不泄漏）
# --------------------------------------------------------------------------- #


def test_action_label_covers_all_16_blocks():
    assert len(EXPECTED_ACTIONS) == 16
    assert dict(report.BLOCK_ACTIONS) == EXPECTED_ACTIONS

    for block_id, expected in EXPECTED_ACTIONS.items():
        assert action_label(block_id) == expected, block_id


def test_action_labels_match_declared_block_specs():
    """映射不许和 ``data/blocks/*.json`` 漂移：声明里的每个块都必须有业务动作。"""

    declared = set()
    for path in sorted(BLOCKS_DIR.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        declared.add(spec["id"])

    assert declared == set(EXPECTED_ACTIONS), (
        "块声明与报告映射不一致：声明多了 %s，映射多了 %s"
        % (sorted(declared - set(EXPECTED_ACTIONS)), sorted(set(EXPECTED_ACTIONS) - declared))
    )


def test_action_labels_never_expose_block_ids():
    for label in report.BLOCK_ACTIONS.values():
        _assert_no_leak(label)


@pytest.mark.parametrize("unknown", ["", "  ", "foo.bar", "archive.addd", "nwk.topo", "无"])
def test_action_label_unknown_block_is_empty(unknown):
    assert action_label(unknown) == ""


def test_contains_block_id_ignores_file_names():
    assert contains_block_id("archive.add") is True
    assert contains_block_id("见 nwk.topology 的细节") is True
    assert contains_block_id("x.meter.read_645.y") is True

    assert contains_block_id("") is False
    assert contains_block_id("证据：/data/case_runs/r1/result.json") is False
    assert contains_block_id("日志 cli.log 与 stdout.jsonl") is False
    assert contains_block_id("AFN=0x1C / Fn=F1，读 220.0V") is False


# --------------------------------------------------------------------------- #
# 2. 四类失败原因 → 人话短语（每类 ≥2 条）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "block_id,state,detail,expected",
    [
        # ---- 超时 / 未收到应答 → 「<动作>超时」
        (
            "nwk.topology",
            "fail",
            "nwk.topology：已发出 7E7E0102ABCDEF，但超时未收到应答",
            "组网超时",
        ),
        (
            "meter.read_645",
            "fail",
            "meter.read_645：已发出 7E7E0102ABCDEF，但超时无应答",
            "抄表超时",
        ),
        (
            "nwk.nodes_online",
            "fail",
            "nwk.nodes_online：等满 180 秒仍未收到应答",
            "在网节点检查超时",
        ),
        # ---- 有应答但判据不成立 → 「<动作>异常」
        (
            "archive.add",
            "fail",
            "archive.add：设备回否认帧 00H-F2（错误状态字=01）",
            "档案下发异常",
        ),
        (
            "nwk.topology",
            "fail",
            "nwk.topology：明确不足 —— 入网节点数 0 低于期望（档案数×0.98）",
            "组网异常",
        ),
        (
            "archive.del",
            "fail",
            "archive.del：删档后反证仍能查到该地址",
            "档案删除异常",
        ),
        # ---- 台架 / 前置不具备 → 「台架不具备：<原因>」
        (
            "fix.simeter_session",
            "skipped",
            "台架前置未满足：串口桥未运行",
            "台架不具备：串口桥未运行",
        ),
        (
            "fix.simcon_session",
            "skipped",
            "fix.simcon_session：串口闸门/会话忙（HTTP 409：端口被占用）",
            "台架不具备：模拟集中器被占用",
        ),
        (
            "nwk.topology",
            "skipped",
            "nwk.topology：台架/能力不就绪，块未执行（不是 FAIL）：工作台/simcon 不可达",
            "台架不具备：工作台服务不可达",
        ),
        (
            "meter.brain_local",
            "skipped",
            "meter.brain_local：台架前置未满足（HTTP 404 无会话）",
            "台架不具备：台架服务未就绪",
        ),
        # ---- 说不清 → 「说不清：<原因>」
        (
            "nwk.route_status",
            "unknown",
            "nwk.route_status：等了 300 秒仍处学习态",
            "说不清：学习态尚未完成",
        ),
        (
            "meter.read_645",
            "unknown",
            "meter.read_645：没有上行帧 hex，无法判定",
            "说不清：没拿到应答帧",
        ),
        (
            "meter.read_645",
            "unknown",
            "meter.read_645：已发出 7E7E0102ABCDEF，但超时未收到应答",
            "说不清：抄表超时未应答",
        ),
        (
            "archive.query",
            "unknown",
            "archive.query：应答结构无法识别，解析不出总数",
            "说不清：应答解析不出",
        ),
    ],
)
def test_classify_failure_four_categories(block_id, state, detail, expected):
    phrase = classify_failure(_block(block_id, state, detail))
    assert phrase == expected
    _assert_no_leak(phrase)


def test_classify_failure_pass_state_has_no_reason():
    assert classify_failure(_block("nwk.topology", "pass", "nwk.topology：收到期望应答")) == ""


def test_classify_failure_numeric_mismatch_carries_measured_value():
    """数值不符必须带实测值：用户要看见「读多少 / 期望多少」。"""

    phrase = classify_failure(
        _block(
            "meter.read_645",
            "fail",
            "meter.read_645：数值不匹配：期望 220.1，实际 220.0（正向有功总电能，DI 00010000）",
        )
    )
    assert phrase == "抄表数值不符（读 220.0，期望 220.1）"
    _assert_no_leak(phrase)

    with_unit = classify_failure(
        _block(
            "meter.read_645",
            "fail",
            "meter.read_645：数值不匹配：期望 220.1V，实际 220.0V（电压，DI 02010100）",
        )
    )
    assert with_unit == "抄表数值不符（读 220.0V，期望 220.1V）"


def test_classify_failure_numeric_mismatch_prefers_record_expected_actual():
    """记录里已有 expected/actual 时优先用它（比从 detail 里抠更可靠）。"""

    phrase = classify_failure(
        {
            "block_id": "meter.read_645",
            "state": "fail",
            "detail": "meter.read_645：数值不匹配",
            "expected": "220.1",
            "actual": "220.0",
        }
    )
    assert phrase == "抄表数值不符（读 220.0，期望 220.1）"


def test_classify_failure_count_mismatch_uses_shude():
    phrase = classify_failure(
        _block("archive.query", "fail", "archive.query：总数不符（期望 3，实得 2；地址表见证据）")
    )
    assert phrase == "档案查询数量不符（实得 2，期望 3）"
    _assert_no_leak(phrase)


def test_classify_failure_address_mismatch_is_human():
    phrase = classify_failure(
        _block(
            "meter.addr_probe",
            "fail",
            "meter.addr_probe：内层 645 地址域不匹配：期望地址 000000000001，"
            "实际 000000000002（上行帧 7E7E0102ABCDEF）",
        )
    )
    assert phrase == "绑定表地址不符（实读 000000000002，期望 000000000001）"
    _assert_no_leak(phrase)


def test_classify_failure_unknown_block_id_does_not_crash_or_leak():
    phrase = classify_failure(
        {"block_id": "weird.thing", "state": "unknown", "detail": "weird.thing：解析不出应答字段"}
    )
    assert phrase.startswith("说不清：")
    _assert_no_leak(phrase)

    # 块 id 完全不认识也不会把 id 漏出去
    assert classify_failure({"block_id": "brand.new.block", "state": "fail", "detail": ""}) == (
        "该步骤异常"
    )


def test_classify_failure_empty_record_does_not_crash():
    phrase = classify_failure({})
    assert phrase.startswith("说不清：")
    _assert_no_leak(phrase)


def test_classify_failure_accepts_dataclass_like_record():
    class _Record:
        def to_dict(self):
            return {
                "block_id": "nwk.topology",
                "state": "skipped",
                "detail": "台架前置未满足：串口桥未运行",
            }

    assert classify_failure(_Record()) == "台架不具备：串口桥未运行"


def test_classify_failure_bench_and_unclear_fallback_stay_short_and_clean():
    """认不出的措辞也要给人话：台架类兜「台架不具备：<短句>」，其余兜「说不清：<短句>」。"""

    bench = classify_failure(
        _block("reset.sta", "skipped", "台架状态文件读不到（实现注释：见 common.with_bench）")
    )
    assert bench.startswith("台架不具备：")
    assert "（" not in bench and len(bench) <= 40
    _assert_no_leak(bench)

    unclear = classify_failure(_block("archive.query", "unknown", "未定义情况：见实现注释"))
    assert unclear.startswith("说不清：")
    assert len(unclear) <= 40
    _assert_no_leak(unclear)


def test_classify_failure_falls_back_to_message_when_detail_missing():
    """``BlockRecord.to_dict()`` 的 ``message`` 是 detail 的孪生字段，缺一个不该退化。"""

    phrase = classify_failure(
        {"block_id": "nwk.topology", "state": "fail", "message": "已发出帧，但超时未收到应答"}
    )
    assert phrase == "组网超时"


def test_scrub_block_ids_replaces_ids_but_keeps_file_names():
    assert report.scrub_block_ids("先 archive.add 再 nwk.topology") == "先 档案下发 再 组网"
    assert report.scrub_block_ids("证据 data/case_runs/r1/result.json") == (
        "证据 data/case_runs/r1/result.json"
    )
    assert report.scrub_block_ids("未知模块 weird.thing 崩了") == "未知模块 该步骤 崩了"
    assert report.scrub_block_ids(None) == ""


# --------------------------------------------------------------------------- #
# 3. 四种 verdict 的 headline（与 REQS D11-1 例子逐字一致）
# --------------------------------------------------------------------------- #


def test_headline_pass():
    summary = summarize_case(
        _case("meter_daily", "pass", [_block("nwk.topology", "pass")]), title="抄表日常回归"
    )
    assert summary["verdict"] == "pass"
    assert summary["headline"] == "抄表日常回归（PASS）"
    assert summary["reason"] == ""
    assert summary["reasons"] == []
    assert summary["case_id"] == "meter_daily"
    assert summary["title"] == "抄表日常回归"


def test_headline_fail_reason_is_business_action():
    summary = summarize_case(
        _case(
            "meter_daily",
            "fail",
            [
                _block("nwk.topology", "pass"),
                _block("nwk.topology", "fail", "nwk.topology：已发出 7E7E01，但超时未收到应答"),
            ],
        ),
        title="抄表日常回归",
    )
    assert summary["verdict"] == "fail"
    assert summary["headline"] == "抄表日常回归（未通过：组网超时）"
    assert summary["reason"] == "组网超时"
    assert summary["reasons"] == ["组网超时"]
    _assert_no_leak(summary["headline"], summary["reason"], *summary["reasons"])


def test_headline_fail_lists_multiple_reasons():
    summary = summarize_case(
        _case(
            "meter_daily",
            "fail",
            [
                _block("nwk.topology", "fail", "nwk.topology：超时未收到应答"),
                _block("archive.add", "fail", "archive.add：设备回否认帧 00H-F2"),
            ],
        ),
        title="抄表日常回归",
    )
    assert summary["headline"] == "抄表日常回归（未通过：组网超时、档案下发异常）"
    assert summary["reasons"] == ["组网超时", "档案下发异常"]


def test_headline_fail_does_not_nest_bench_reasons():
    """未通过里夹着台架问题时，不许出现「未通过：台架不具备：…」这种套娃。"""

    summary = summarize_case(
        _case(
            "meter_daily",
            "fail",
            [
                _block("nwk.topology", "fail", "nwk.topology：超时未收到应答"),
                _block("fix.simcon_session", "skipped", "HTTP 409 端口被占用"),
                _block("meter.read_645", "unknown", "没有上行帧 hex"),
            ],
        ),
        title="抄表日常回归",
    )
    assert summary["headline"] == (
        "抄表日常回归（未通过：组网超时；台架不具备：模拟集中器被占用；说不清：没拿到应答帧）"
    )
    _assert_no_leak(summary["headline"], *summary["reasons"])


def test_headline_blocked_is_bench_not_ready():
    summary = summarize_case(
        _case(
            "bench_ready",
            "blocked",
            [_block("fix.simeter_session", "skipped", "台架前置未满足：串口桥未运行")],
        ),
        title="抄表日常回归",
    )
    assert summary["verdict"] == "blocked"
    assert summary["headline"] == "抄表日常回归（台架不具备：串口桥未运行）"
    assert summary["reason"] == "台架不具备：串口桥未运行"


def test_headline_inconclusive_is_unclear():
    summary = summarize_case(
        _case(
            "meter_daily",
            "inconclusive",
            [_block("meter.read_645", "unknown", "meter.read_645：已发出 7E7E01，但超时未收到应答")],
        ),
        title="抄表日常回归",
    )
    assert summary["verdict"] == "inconclusive"
    assert summary["headline"] == "抄表日常回归（说不清：抄表超时未应答）"
    assert summary["reason"] == "说不清：抄表超时未应答"


def test_summarize_case_derives_verdict_from_blocks_when_missing():
    blocked = summarize_case(
        _case("bench_ready", blocks=[_block("fix.simcon_session", "skipped", "串口桥未运行")]),
        title="台架就绪自检",
    )
    assert blocked["verdict"] == "blocked"
    assert blocked["headline"] == "台架就绪自检（台架不具备：串口桥未运行）"

    inconclusive = summarize_case(
        _case("meter_daily", blocks=[_block("meter.read_645", "unknown", "没有上行帧 hex")]),
        title="抄表日常回归",
    )
    assert inconclusive["verdict"] == "inconclusive"

    still_pass = summarize_case(
        _case("x", blocks=[_block("nwk.topology", "pass")]), title="组网"
    )
    assert still_pass["verdict"] == "pass"


def test_summarize_case_title_defaults_and_shortens():
    long_title = "抄表日常回归（不复位，日常）：档案 → 组网 → 绑表 → 抄表"
    summary = summarize_case(_case("meter_daily", "pass", title=long_title))
    assert summary["title"] == "抄表日常回归"
    assert summary["headline"] == "抄表日常回归（PASS）"

    no_title = summarize_case(_case("meter_daily", "pass"))
    assert no_title["title"] == "meter_daily"
    assert no_title["headline"] == "meter_daily（PASS）"


def test_summarize_case_never_leaks_block_ids_everywhere():
    summary = summarize_case(
        _case(
            "meter_daily",
            "fail",
            [
                _block("archive.add", "fail", "archive.add：设备回否认帧 00H-F2"),
                _block("nwk.topology", "skipped", "nwk.topology：台架/能力不就绪"),
            ],
        ),
        title="抄表日常回归",
    )
    _assert_no_leak(summary["headline"], summary["reason"], *summary["reasons"])


# --------------------------------------------------------------------------- #
# 4. summarize_run：多用例取最严重（fail > blocked > inconclusive > pass）
# --------------------------------------------------------------------------- #


def test_summarize_run_takes_most_severe_verdict():
    record = {
        "run_id": "r-1",
        "cases": [
            _case("bench_ready", "pass"),
            _case("archive_ready", "inconclusive", [_block("archive.query", "unknown", "解析不出")]),
            _case("sta_reset", "blocked", [_block("reset.sta", "skipped", "串口桥未运行")]),
            _case("meter_daily", "fail", [_block("nwk.topology", "fail", "超时未收到应答")]),
        ],
    }
    out = summarize_run(record, titles={"meter_daily": "抄表日常回归"})

    assert out["verdict"] == "fail"
    assert len(out["case_summaries"]) == 4
    assert out["case_summaries"][0]["headline"] == "bench_ready（PASS）"
    assert out["case_summaries"][3]["headline"] == "抄表日常回归（未通过：组网超时）"
    assert out["reasons"] == ["说不清：应答解析不出", "台架不具备：串口桥未运行", "组网超时"]
    _assert_no_leak(out["headline"], *out["reasons"])


def test_summarize_run_priority_blocked_over_inconclusive_and_pass():
    blocked = summarize_run(
        {
            "cases": [
                _case("a", "pass"),
                _case("b", "inconclusive", [_block("meter.read_645", "unknown", "解析不出")]),
                _case("c", "blocked", [_block("fix.simcon_session", "skipped", "串口桥未运行")]),
            ]
        }
    )
    assert blocked["verdict"] == "blocked"
    assert "台架不具备" in blocked["headline"]

    inconclusive = summarize_run(
        {
            "cases": [
                _case("a", "pass"),
                _case("b", "inconclusive", [_block("meter.read_645", "unknown", "解析不出")]),
            ]
        }
    )
    assert inconclusive["verdict"] == "inconclusive"
    assert "说不清" in inconclusive["headline"]

    passed = summarize_run({"cases": [_case("a", "pass"), _case("b", "pass")]})
    assert passed["verdict"] == "pass"
    assert passed["headline"] == "本轮运行（PASS）"
    assert passed["reasons"] == []


def test_summarize_run_accepts_suite_title_and_evidence():
    out = summarize_run(
        {
            "suite_id": "daily",
            "title": "日常回归套件（组网 + 抄表）",
            "verdict": "fail",
            "result_path": "/data/case_runs/r-7/result.json",
            "cases": [_case("meter_daily", "fail", [_block("nwk.topology", "fail", "超时未收到应答")])],
        }
    )
    assert out["headline"].startswith("日常回归套件（未通过：")
    assert "组网超时" in out["headline"]
    assert [item["label"] for item in out["evidence_paths"]] == ["运行结果"]
    assert out["evidence_paths"][0]["path"] == "/data/case_runs/r-7/result.json"
    _assert_no_leak(out["headline"], *out["reasons"])


def test_summarize_run_falls_back_to_record_verdict_without_cases():
    out = summarize_run({"verdict": "blocked", "title": "台架就绪"})
    assert out["verdict"] == "blocked"
    assert out["case_summaries"] == []
    assert out["headline"] == "台架就绪（台架不具备：前置条件未满足）"


# --------------------------------------------------------------------------- #
# 5. 证据路径（只从输入已有的键组合，不确定就空列表，绝不编）
# --------------------------------------------------------------------------- #


def test_evidence_paths_compose_from_run_id_and_runs_dir():
    summary = summarize_case(
        {"case_id": "x", "verdict": "pass", "run_id": "r-9", "runs_dir": "/data/case_runs"}
    )
    paths = {item["label"]: item["path"] for item in summary["evidence_paths"]}
    assert "/data/case_runs/r-9/result.json" in paths.values()
    assert "运行结果" in paths
    for item in summary["evidence_paths"]:
        assert set(item) == {"label", "path"}
        _assert_no_leak(item["path"])  # 文件名 allowed，块 id 不允许


def test_evidence_paths_pass_through_existing_entries():
    summary = summarize_case(
        {
            "case_id": "x",
            "verdict": "pass",
            "result_path": "/tmp/r/result.json",
            "evidence_paths": [{"label": "CLI 日志", "path": "/tmp/r/cli.log"}, "/tmp/r/frames.log"],
            "log_path": "/tmp/r/stdout.log",
        }
    )
    paths = [item["path"] for item in summary["evidence_paths"]]
    assert paths == ["/tmp/r/result.json", "/tmp/r/cli.log", "/tmp/r/frames.log", "/tmp/r/stdout.log"]
    labels = [item["label"] for item in summary["evidence_paths"]]
    assert labels == ["运行结果", "CLI 日志", "证据", "运行日志"]


def test_evidence_paths_empty_when_nothing_known():
    assert summarize_case({"case_id": "x", "verdict": "pass"})["evidence_paths"] == []
    assert summarize_run({})["evidence_paths"] == []


# --------------------------------------------------------------------------- #
# 6. 空输入 / 垃圾输入：不崩、不假 pass、不泄漏
# --------------------------------------------------------------------------- #


def test_empty_inputs_do_not_crash_and_never_assume_pass():
    empty_case = summarize_case({})
    assert empty_case["verdict"] == "inconclusive"
    assert empty_case["verdict"] != "pass"
    assert empty_case["case_id"] == ""
    assert empty_case["reasons"] == []
    assert empty_case["headline"]
    _assert_no_leak(empty_case["headline"], empty_case["reason"])

    assert summarize_case(None)["verdict"] == "inconclusive"  # type: ignore[arg-type]
    assert summarize_run(None)["verdict"] == "inconclusive"  # type: ignore[arg-type]

    empty_run = summarize_run({})
    assert empty_run["verdict"] == "inconclusive"
    assert empty_run["case_summaries"] == []
    assert empty_run["headline"]
    _assert_no_leak(empty_run["headline"])


def test_garbage_blocks_are_ignored_not_crashing():
    summary = summarize_case(
        {
            "case_id": "x",
            "verdict": "fail",
            "blocks": [None, "不是字典", 42, {"block_id": "nwk.topology", "state": "fail"}],
        },
        title="组网",
    )
    assert summary["verdict"] == "fail"
    assert summary["reasons"] == ["组网异常"]
    _assert_no_leak(summary["headline"], *summary["reasons"])


def test_invalid_verdict_value_falls_back_to_blocks():
    summary = summarize_case(
        _case("x", "pending", [_block("nwk.topology", "skipped", "串口桥未运行")]), title="组网"
    )
    assert summary["verdict"] == "blocked"


# --------------------------------------------------------------------------- #
# 7. 公开面（CLI / API / UI 都按这一份调，名字不许改）
# --------------------------------------------------------------------------- #


def test_public_api_is_exported_from_core_package():
    from case_runner import core

    for name in ("action_label", "classify_failure", "summarize_case", "summarize_run"):
        assert callable(getattr(report, name)), name
        assert name in core.__all__, "core/__init__.py 的 __all__ 缺少 %s" % name
        assert callable(getattr(core, name)), name

    assert isinstance(report.BLOCK_ACTIONS, dict)
    assert len(report.BLOCK_ACTIONS) == 16
