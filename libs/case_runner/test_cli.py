"""CLI 入口层单测（REQS-0041 第四批：黑盒命令行 `case_run`）。

口径
----
``case_runner/cli.py`` 是**入口层**：只做参数解析 / 装配 / 输出，判定全在块与事实里。
因此本文件不做「块该怎么判」的测试（那是 ``blocks/test_*.py`` 的事），只测入口契约：

1. **退出码四种语义**：0 全 pass / 1 有 fail / 2 有 unknown·skipped / 3 用法与参数 / 4 声明坏；
2. **``--params`` 执行前校验**：缺必填 / 多余参数 / 类型非法 → 退出码 3，且**执行体一次都没被调用**；
3. **``probe --all`` 铁律**：台架未就绪时**全部 unknown，绝无任何块被判 pass**（两条独立证据：
   真声明根 + 无工作台地址；假块假事实 + 未就绪假能力）；
4. **断点呈现**：人读输出含失败块 id 与原因、``unmet_facts``、``failed_block`` / ``blocked_block``；
5. **两点输出**：``--json`` 的 stdout 必须是合法 JSON（``json.loads`` 可解）；
6. **``base_url`` 注入**：``--base-url`` 优先于环境变量，执行期可见、执行后还原；
7. **真跑**：用假块 + 假事实探测源跑通一条用例并给出完整 ``CaseRunResult.to_dict()``。

全部脱离台架：假块执行体、假事实探测体、假能力对象、临时声明根（``tmp_path``），
不发起任何真实 HTTP、不碰真串口。
"""

from __future__ import annotations

import io
import json
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

import pytest

from case_runner import cli
from case_runner.core.executor import BlockResult
from case_runner.core.models import Health
from case_runner.core.planner import FactValue
from case_runner.core.registry import (
    has_block,
    has_fact,
    register_block,
    register_fact,
    unregister_block,
    unregister_fact,
)

ADDR = "000000000001"
ADDR2 = "000000000012"
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3
EXIT_DECLARATION = 4

#: 真声明根（块 + 事实已注册；用例目录不参与本轮断言）
REAL_DATA_DIR = pathlib.Path(cli.decl_loader.DATA_DIR)


# --------------------------------------------------------------------------- #
# 调用工具
# --------------------------------------------------------------------------- #


def run_cli(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
    """跑一次 CLI，返回 ``(退出码, stdout, stderr)``（不碰真 stdout）。"""
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, out=out, err=err, **kwargs)
    return code, out.getvalue(), err.getvalue()


def run_json(argv: list[str], **kwargs: Any) -> tuple[int, dict[str, Any]]:
    code, out, err = run_cli(argv, **kwargs)
    assert out.strip(), "退出码 %d 时 stdout 为空（stderr=%r）" % (code, err)
    return code, json.loads(out)


# --------------------------------------------------------------------------- #
# 假台架：记录调用 + 可切换事实三态
# --------------------------------------------------------------------------- #


@dataclass
class Recorder:
    """记录块执行体 / 事实探测体收到的入参（用于「校验失败时不许执行」）。"""

    block_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fact_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def block(self, block_id: str, params: dict[str, Any]) -> None:
        self.block_calls.append((block_id, dict(params)))

    def fact(self, fact_id: str, params: dict[str, Any]) -> None:
        self.fact_calls.append((fact_id, dict(params)))


@dataclass
class FakeBench:
    """临时声明根 + 可编程的假执行体。"""

    root: pathlib.Path
    recorder: Recorder
    truth: dict[str, str]
    env_seen: list[str] = field(default_factory=list)


def _write(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _step(text: str) -> dict[str, Any]:
    return {"no": 1, "do": text, "expect": "拿到结论"}


def build_fake_bench(root: pathlib.Path) -> FakeBench:
    """造一套最小声明根：5 假块 / 2 假事实 / 4 假用例 / 2 假方案。"""
    recorder = Recorder()
    truth = {"fake.ready": "yes"}
    bench = FakeBench(root=root, recorder=recorder, truth=truth)

    _write(root / "blocks" / "fake_pass.json", {
        "id": "fake.pass", "title": "假块·通过",
        "action": {"kind": "fake", "params": {"addr": ADDR, "mode": "ok"}},
    })
    _write(root / "blocks" / "fake_fail.json", {
        "id": "fake.fail", "title": "假块·失败",
        "action": {"kind": "fake", "params": {"addr": ADDR}},
    })
    _write(root / "blocks" / "fake_unknown.json", {
        "id": "fake.unknown", "title": "假块·不知道",
        "action": {"kind": "fake"},
    })
    _write(root / "blocks" / "fake_env.json", {
        "id": "fake.env", "title": "假块·记录注入的工作台地址",
        "action": {"kind": "fake"},
    })
    _write(root / "blocks" / "fake_gated.json", {
        "id": "fake.gated", "title": "假块·需要前置事实",
        "needs": ["fake.ready"], "action": {"kind": "fake"},
    })

    _write(root / "facts" / "fake_ready.json", {
        "id": "fake.ready", "title": "假事实·台架就绪", "probe": "fake.ready",
        "params_schema": {
            "addr": {"type": "string", "pattern": "^[0-9]{12}$", "required": True},
            "count": {"type": "integer", "default": 4},
        },
    })
    _write(root / "facts" / "fake_never.json", {
        "id": "fake.never", "title": "假事实·永远不知道", "probe": "fake.never",
        "params_schema": {},
    })

    _write(root / "cases" / "fake_ok.json", {
        "id": "fake_ok", "title": "假用例·全通过", "steps": [_step("跑假块")],
        "needs": [], "blocks": ["fake.pass"], "asserts": [], "tags": ["fake"],
    })
    _write(root / "cases" / "fake_fail.json", {
        "id": "fake_fail", "title": "假用例·有失败块", "steps": [_step("跑假块")],
        "needs": ["fake.ready"], "blocks": ["fake.pass", "fake.fail"], "asserts": [],
        "tags": ["fake"],
    })
    _write(root / "cases" / "fake_blocked.json", {
        "id": "fake_blocked", "title": "假用例·前置事实不满足", "steps": [_step("跑假块")],
        "needs": ["fake.ready"], "blocks": ["fake.gated"], "asserts": [],
        "tags": ["fake"],
    })
    _write(root / "cases" / "fake_unknown.json", {
        "id": "fake_unknown_case", "title": "假用例·块不知道", "steps": [_step("跑假块")],
        "needs": [], "blocks": ["fake.unknown"], "asserts": [], "tags": ["fake"],
    })
    _write(root / "cases" / "fake_env_case.json", {
        "id": "fake_env_case", "title": "假用例·看注入地址", "steps": [_step("跑假块")],
        "needs": [], "blocks": ["fake.env"], "asserts": [], "tags": ["fake"],
    })

    _write(root / "case_suites" / "fake_suite.json", {
        "id": "fake_suite", "name": "假方案·两例",
        "case_ids": ["fake_ok", "fake_fail"], "stop_on_fail": False,
    })
    _write(root / "case_suites" / "fake_suite_stop.json", {
        "id": "fake_suite_stop", "name": "假方案·首败即停",
        "case_ids": ["fake_fail", "fake_ok"], "stop_on_fail": True,
    })
    return bench


def _register_blocks(bench: FakeBench) -> None:
    recorder, truth = bench.recorder, bench.truth

    def make(state: str, detail: str):
        def executor(params: dict[str, Any], ctx: Any = None) -> BlockResult:
            recorder.block(str(params.get("kind") or "fake"), params)
            return BlockResult(state=state, detail=detail)

        return executor

    fake_blocks = {
        "fake.pass": make("pass", "假块：pass（判定逻辑不在入口层）"),
        "fake.fail": make("fail", "假块：故意失败（用于验证断点呈现）"),
        "fake.unknown": make("unknown", "假块：不知道（未完成验证）"),
        "fake.gated": make("pass", "假块：前置事实满足，pass"),
    }

    def env_executor(params: dict[str, Any], ctx: Any = None) -> BlockResult:
        seen = os.environ.get("WORKBENCH_BASE", "")
        bench.env_seen.append(seen)
        recorder.block("fake.env", params)
        return BlockResult(state="pass" if seen else "fail",
                           detail="执行期看到的 WORKBENCH_BASE=%r" % seen)

    fake_blocks["fake.env"] = env_executor

    for block_id, executor in fake_blocks.items():
        if has_block(block_id):
            unregister_block(block_id)
        register_block(block_id, executor)

    def ready_probe(params: dict[str, Any]) -> FactValue:
        recorder.fact("fake.ready", params)
        state = truth.get("fake.ready", "yes")
        return FactValue(state=state, detail="假事实 fake.ready=%s" % state)

    def never_probe(params: dict[str, Any]) -> FactValue:
        recorder.fact("fake.never", params)
        return FactValue(state="unknown", detail="假事实：永远不知道")

    for fact_id, probe in (("fake.ready", ready_probe), ("fake.never", never_probe)):
        if has_fact(fact_id):
            unregister_fact(fact_id)
        register_fact(fact_id, probe)


def _unregister_fakes() -> None:
    for block_id in ("fake.pass", "fake.fail", "fake.unknown", "fake.gated", "fake.env"):
        unregister_block(block_id)
    for fact_id in ("fake.ready", "fake.never"):
        unregister_fact(fact_id)


@pytest.fixture
def bench(tmp_path: pathlib.Path) -> FakeBench:
    fake = build_fake_bench(tmp_path / "data")
    _register_blocks(fake)
    try:
        yield fake
    finally:
        _unregister_fakes()


class FakeCapability:
    """未就绪的假能力：对标能力层「依赖不可用」分支（不抛异常，返回 ok=False）。"""

    name = "fake"

    def __init__(self, reason: str = "假能力：台架未接", payload: dict | None = None) -> None:
        self.reason = reason
        self.payload = payload or {}

    def probe(self) -> Health:
        return Health(ok=False, reason=self.reason)

    def status(self) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable", "detail": self.reason}

    def verify(self, steps: Any) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable", "detail": self.reason, "steps": []}

    def frames(self, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable", "detail": self.reason, "entries": []}

    def port_status(self, mapping_id: Any = None) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable", "detail": self.reason}

    def ensure(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable", "detail": self.reason}

    def send(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable", "detail": self.reason}

    def logs(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable", "detail": self.reason}

    def handle_hex(self, request_hex: str) -> dict[str, Any]:
        return {"ok": False, "error": "unreachable", "detail": self.reason}

    clock = staticmethod(lambda: 0.0)
    sleeper = staticmethod(lambda seconds: None)


# --------------------------------------------------------------------------- #
# 1. list / show
# --------------------------------------------------------------------------- #


def test_list_json_has_blocks_facts_cases_suites(bench: FakeBench) -> None:
    code, payload = run_json(["list", "--json", "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert payload["counts"] == {"blocks": 5, "facts": 2, "cases": 5, "suites": 2}
    assert {item["id"] for item in payload["blocks"]} == {
        "fake.pass", "fake.fail", "fake.unknown", "fake.env", "fake.gated",
    }
    assert {item["id"] for item in payload["facts"]} == {"fake.ready", "fake.never"}
    assert payload["problems"] == []
    # 事实的必填参数随 list 一起给出（UI / 脚本都要能看到）
    ready = next(item for item in payload["facts"] if item["id"] == "fake.ready")
    assert ready["required"] == ["addr"]


def test_list_human_is_chinese_and_names_each_kind(bench: FakeBench) -> None:
    code, out, _err = run_cli(["list", "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    for token in ("块（5）", "事实（2）", "用例（5）", "方案（2）", "前置事实", "probe"):
        assert token in out, token


def test_show_case_lists_steps_blocks_needs_and_params(bench: FakeBench) -> None:
    code, payload = run_json(["show", "fake_fail", "--json", "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    item = payload["item"]
    assert payload["kind"] == "case"
    assert item["id"] == "fake_fail"
    assert item["needs"] == ["fake.ready"]
    assert [block["id"] for block in item["blocks"]] == ["fake.pass", "fake.fail"]
    assert item["steps"][0]["do"] == "跑假块"
    # 参数规格来自块 action.params + 事实 params_schema
    params = payload["param_spec"]["params"]
    assert set(params) == {"addr", "count", "mode"}
    assert params["addr"]["required_by"] == ["fake.ready"]

    code, out, _err = run_cli(["show", "fake_fail", "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert "步骤说明" in out and "块清单" in out and "可传入的用例参数" in out


def test_show_resolves_block_fact_and_suite_ids(bench: FakeBench) -> None:
    for target, kind in (("fake.pass", "block"), ("fake.ready", "fact"),
                         ("fake_suite", "suite")):
        code, payload = run_json(["show", target, "--json", "--data-dir", str(bench.root)])
        assert code == EXIT_OK, target
        assert payload["kind"] == kind


def test_show_unknown_id_is_usage_error(bench: FakeBench) -> None:
    code, out, err = run_cli(["show", "nope", "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert "找不到 id" in err


# --------------------------------------------------------------------------- #
# 2. 退出码四种语义
# --------------------------------------------------------------------------- #


def test_exit_code_0_when_all_blocks_pass(bench: FakeBench) -> None:
    code, payload = run_json(["run", "--case", "fake_ok", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert payload["verdict"] == "pass"
    assert payload["counts"]["pass"] == 1
    assert payload["failed_block"] == ""


def test_exit_code_1_and_breakpoint_on_fail(bench: FakeBench) -> None:
    code, payload = run_json(["run", "--case", "fake_fail", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL
    assert payload["verdict"] == "fail"
    # 断点：失败块 id 必须点出来
    assert payload["failed_block"] == "fake.fail"
    assert payload["counts"] == {"pass": 1, "fail": 1, "skipped": 0, "unknown": 0,
                                 "total": 2}
    states = {block["block_id"]: block["state"] for block in payload["blocks"]}
    assert states == {"fake.pass": "pass", "fake.fail": "fail"}


def test_exit_code_1_human_output_shows_breakpoint(bench: FakeBench) -> None:
    code, out, _err = run_cli(["run", "--case", "fake_fail", "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL
    for token in ("结论：FAIL", "fake.fail", "断点定位", "failed_block：fake.fail",
                  "失败原因", "未满足的事实"):
        assert token in out, token


def test_exit_code_2_when_block_unknown(bench: FakeBench) -> None:
    code, payload = run_json(["run", "--case", "fake_unknown_case", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_INCOMPLETE
    assert payload["verdict"] == "inconclusive"
    assert payload["counts"]["unknown"] == 1
    assert payload["counts"]["pass"] == 0      # unknown 绝不进 pass 计数


def test_exit_code_2_when_gated_by_unmet_fact(bench: FakeBench) -> None:
    bench.truth["fake.ready"] = "no"
    code, payload = run_json([
        "run", "--case", "fake_blocked", "--params", json.dumps({"addr": ADDR}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_INCOMPLETE
    assert payload["verdict"] == "inconclusive"
    assert payload["counts"]["skipped"] == 1
    assert payload["counts"]["pass"] == 0
    # 断点：阻断块 + 未满足事实都要给出
    assert payload["blocked_block"] == "fake.gated"
    assert "fake.ready" in payload["unmet_facts"]
    assert payload["unmet_facts"]["fake.ready"]["state"] == "no"


def test_no_fake_pass_when_fact_unknown(bench: FakeBench) -> None:
    """``unknown`` 永不折算 pass：事实不知道 → 块 skipped → 用例 inconclusive。"""
    bench.truth["fake.ready"] = "unknown"
    code, payload = run_json([
        "run", "--case", "fake_blocked", "--params", json.dumps({"addr": ADDR}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_INCOMPLETE
    assert payload["verdict"] == "inconclusive"
    assert payload["counts"]["pass"] == 0
    assert all(block["state"] != "pass" for block in payload["blocks"])


def test_exit_code_4_on_broken_declaration(bench: FakeBench) -> None:
    (bench.root / "blocks" / "broken.json").write_text('{"id": "broken.", "title": 1}',
                                                       encoding="utf-8")
    code, payload = run_json(["check", "--json", "--data-dir", str(bench.root)])
    assert code == EXIT_DECLARATION
    assert payload["ok"] is False
    assert payload["problems"], "声明坏了必须报出 file:reason"
    assert any("broken.json" in problem for problem in payload["problems"])

    # run 带病不许跑（宁可不跑，也不带错声明上真台架）
    code, out, err = run_cli(["run", "--case", "fake_ok", "--data-dir", str(bench.root)])
    assert code == EXIT_DECLARATION
    assert "声明加载失败" in err
    assert bench.recorder.block_calls == []


# --------------------------------------------------------------------------- #
# 3. --params 校验（执行前拦下，执行体一次都不许被调）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "params_text, expect_problem",
    [
        ("{not json", "--params 不是合法 JSON"),
        ("[1, 2]", "必须是 JSON 对象"),
        (json.dumps({"nope": 1}), "多余参数"),
        (json.dumps({"addr": 123}), "类型非法"),
        (json.dumps({"addr": ADDR, "count": "many"}), "类型非法"),
        (json.dumps({"addr": "12345"}), "取值非法"),        # 事实 schema 的 pattern
    ],
)
def test_params_validation_blocks_execution(
    bench: FakeBench, params_text: str, expect_problem: str
) -> None:
    code, payload = run_json([
        "run", "--case", "fake_fail", "--params", params_text, "--json",
        "--data-dir", str(bench.root),
    ])
    assert code == EXIT_USAGE
    assert payload["ok"] is False
    assert payload["error"] == "usage_error"
    assert any(expect_problem in problem for problem in payload["problems"]), payload
    # 关键：入参没校验通过，块执行体与事实探测体都不得被调用
    assert bench.recorder.block_calls == []
    assert bench.recorder.fact_calls == []


def test_missing_required_param_is_usage_error(bench: FakeBench) -> None:
    """``fake.ready`` 的 addr 是必填，而 ``fake.gated`` 没声明取值 → 必须报缺必填。"""
    code, payload = run_json(["run", "--case", "fake_blocked", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert any("缺少必填参数 'addr'" in problem for problem in payload["problems"])
    assert bench.recorder.block_calls == []


def test_required_param_falls_back_to_unique_block_declaration(bench: FakeBench) -> None:
    """块声明里唯一确定的取值可以顶替 --params（不给也能跑），但要真的传下去。"""
    code, payload = run_json(["run", "--case", "fake_fail", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL  # 跑起来了（不是 3），结论来自假块的 fail
    assert payload["params"] == {"addr": ADDR}
    assert bench.recorder.fact_calls, "事实必须拿到 addr 才会被探测"
    assert bench.recorder.fact_calls[0][1]["addr"] == ADDR


def test_params_are_broadcast_to_block_and_fact(bench: FakeBench) -> None:
    code, _payload = run_json([
        "run", "--case", "fake_fail", "--params", json.dumps({"addr": ADDR2}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_FAIL
    block_params = [params for _kind, params in bench.recorder.block_calls]
    assert any(item.get("addr") == ADDR2 for item in block_params), block_params
    assert bench.recorder.fact_calls[0][1]["addr"] == ADDR2


def test_params_json_output_is_complete_case_run_result(bench: FakeBench) -> None:
    code, payload = run_json(["run", "--case", "fake_fail", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL
    for key in ("case_id", "run_id", "verdict", "failed_block", "blocked_block",
                "counts", "unmet_facts", "blocks", "assertions", "stop_on_fail"):
        assert key in payload, key
    assert payload["case_id"] == "fake_fail"
    assert isinstance(payload["assertions"], list) and payload["assertions"]


# --------------------------------------------------------------------------- #
# 4. 用例选择相关的用法错误
# --------------------------------------------------------------------------- #


def test_run_requires_case_or_suite(bench: FakeBench) -> None:
    code, out, err = run_cli(["run", "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert "--case" in err


def test_case_and_suite_are_mutually_exclusive(bench: FakeBench) -> None:
    code, _out, err = run_cli(["run", "--case", "fake_ok", "--suite", "fake_suite",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert "只能三选一" in err


def test_unknown_case_id_is_usage_error(bench: FakeBench) -> None:
    code, _out, err = run_cli(["run", "--case", "nope", "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert "找不到用例" in err


def test_missing_data_dir_is_usage_error(tmp_path: pathlib.Path) -> None:
    code, _out, err = run_cli(["list", "--data-dir", str(tmp_path / "no-such-dir")])
    assert code == EXIT_USAGE
    assert "声明根目录不存在" in err


def test_probe_unknown_block_and_fact_are_usage_errors(bench: FakeBench) -> None:
    code, _out, err = run_cli(["probe", "--block", "nope", "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE and "找不到块" in err
    code, _out, err = run_cli(["probe", "--fact", "nope", "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE and "找不到事实" in err


def test_argparse_usage_error_maps_to_exit_3(tmp_path: pathlib.Path) -> None:
    code, _out, _err = run_cli([])                       # 缺子命令
    assert code == EXIT_USAGE
    code, _out, _err = run_cli(["run", "--nope"])        # 未知参数
    assert code == EXIT_USAGE


def test_help_documents_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["--help"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    for token in ("0  全部 pass", "1  有 fail", "2  有 unknown/skipped", "3  用法/参数错误",
                  "4  声明加载失败"):
        assert token in captured.out, token


# --------------------------------------------------------------------------- #
# 5. probe --all 铁律：无台架 → 全部 unknown，绝无 pass
# --------------------------------------------------------------------------- #


def test_probe_all_real_declarations_without_bench_is_all_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """铁律（真声明根 + 真探测体）：没有工作台地址时，13 块 / 7 事实必须全 unknown。"""
    monkeypatch.delenv(cli.ENV_BASE_URL, raising=False)
    code, payload = run_json(["probe", "--all", "--json"])
    if code == EXIT_DECLARATION:
        pytest.skip("声明根此刻有非法声明（并行开发期），问题：%r" % (payload.get("problems"),))
    assert payload["base_url"] == "" and payload["base_url_source"] == "none"
    assert payload["blocks"], "必须逐块自检"
    assert payload["facts"], "必须逐事实自检"
    states = {item["block_id"]: item["state"] for item in payload["blocks"]}
    assert all(state == "unknown" for state in states.values()), states
    assert [item["fact_id"] for item in payload["facts"]
            if item["state"] == "yes"] == []
    assert payload["block_counts"]["pass"] == 0
    assert code == EXIT_INCOMPLETE


def test_probe_all_with_not_ready_capabilities_is_all_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """铁律（假能力替身）：能力全未就绪时同样一块都不许 pass。"""
    monkeypatch.delenv(cli.ENV_BASE_URL, raising=False)
    cap = FakeCapability()
    extras = {"simcon": cap, "simeter": cap, "module": cap}
    code, payload = run_json(
        ["probe", "--all", "--json"],
        extras=extras,
        fact_probe=lambda fact_id, params: FactValue(state="unknown",
                                                    detail="假探测：没有台架"),
    )
    if code == EXIT_DECLARATION:
        pytest.skip("声明根此刻有非法声明（并行开发期）")
    assert all(item["state"] == "unknown" for item in payload["blocks"])
    assert payload["block_counts"]["pass"] == 0
    assert payload["block_counts"]["fail"] == 0
    assert payload["summary"].startswith("没有任何块被判 pass")
    assert code == EXIT_INCOMPLETE


def test_probe_all_in_fake_root_reports_each_block_conclusion(bench: FakeBench) -> None:
    code, payload = run_json(["probe", "--all", "--json", "--data-dir", str(bench.root)],
                             extras={"simcon": FakeCapability()})
    states = {item["block_id"]: item["state"] for item in payload["blocks"]}
    assert states["fake.pass"] == "pass"      # 假执行体自己给的结论（判定不在入口层）
    assert states["fake.fail"] == "fail"
    assert states["fake.unknown"] == "unknown"
    assert states["fake.env"] == "fail"       # 没有工作台地址时假块自报 fail
    assert code == EXIT_FAIL                  # 自检里有 fail → 1（优先于 unknown）
    assert payload["block_counts"]["total"] == len(states)


def test_probe_skipped_becomes_unknown_in_probe_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """块执行体因台架不在返回 skipped 时，自检口径记为 unknown（不是 pass，也不是 skipped）。"""
    cap = FakeCapability()
    code, payload = run_json(["probe", "--block", "fix.simcon_session", "--json"],
                             extras={"simcon": cap},
                             fact_probe=lambda fact_id, params: FactValue(state="yes"))
    if code == EXIT_DECLARATION:
        pytest.skip("声明根此刻有非法声明（并行开发期）")
    item = payload["blocks"][0]
    assert item["raw_state"] == "skipped"
    assert item["state"] == "unknown"
    assert code == EXIT_INCOMPLETE


def test_probe_fact_and_block_targets(bench: FakeBench) -> None:
    code, payload = run_json(["probe", "--fact", "fake.never", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_INCOMPLETE
    assert payload["blocks"] == []           # 只查事实时不跑块（不产生激励）
    assert payload["facts"][0]["fact_id"] == "fake.never"
    assert payload["facts"][0]["state"] == "unknown"

    code, payload = run_json(["probe", "--block", "fake.pass", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert payload["facts"] == []
    assert payload["blocks"][0]["state"] == "pass"


def test_probe_never_reports_pass_when_gating_fact_unknown(bench: FakeBench) -> None:
    """假根 + 前置事实 unknown：依赖它的块必须 unknown，且退出码是「未完成验证」。"""
    bench.truth["fake.ready"] = "unknown"
    code, payload = run_json(["probe", "--block", "fake.gated", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_INCOMPLETE
    item = payload["blocks"][0]
    assert item["state"] == "unknown"
    assert item["needs_text"] == "fake.ready=unknown"
    assert bench.recorder.block_calls == [], "前置事实不满足时块执行体不许被调用"


# --------------------------------------------------------------------------- #
# 6. base_url 注入（--base-url > WORKBENCH_BASE；不写死端口）
# --------------------------------------------------------------------------- #


def test_resolve_base_url_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cli.ENV_BASE_URL, raising=False)
    assert cli.resolve_base_url(None) == ("", "none")
    monkeypatch.setenv(cli.ENV_BASE_URL, "http://env.invalid:1")
    assert cli.resolve_base_url(None) == ("http://env.invalid:1", "env")
    assert cli.resolve_base_url("http://cli.invalid:2") == ("http://cli.invalid:2", "cli")


def test_base_url_flag_reaches_execution_and_is_restored(
    bench: FakeBench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.ENV_BASE_URL, "http://env.invalid:1")
    code, payload = run_json([
        "run", "--case", "fake_env_case", "--base-url", "http://cli.invalid:2",
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_OK
    assert bench.env_seen == ["http://cli.invalid:2"]       # 执行期看到 --base-url
    assert os.environ[cli.ENV_BASE_URL] == "http://env.invalid:1"   # 执行后还原
    assert payload["verdict"] == "pass"


def test_env_base_url_is_used_when_flag_absent(
    bench: FakeBench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.ENV_BASE_URL, "http://env.invalid:1")
    code, _payload = run_json(["run", "--case", "fake_env_case", "--json",
                               "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert bench.env_seen == ["http://env.invalid:1"]


def test_no_base_url_anywhere_means_empty_not_default_port(
    bench: FakeBench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(cli.ENV_BASE_URL, raising=False)
    code, payload = run_json(["run", "--case", "fake_env_case", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL           # 假块在「没有地址」时给 fail，证明入口没偷偷塞默认值
    assert bench.env_seen == [""]
    assert payload["verdict"] == "fail"


def test_sources_contain_no_hardcoded_workbench_port() -> None:
    """入口文件不得写死工作台端口（REQS 第四批硬约束，连注释里也不许有）。"""
    needle = "87" + "90"      # 拆开写，避免本测试文件自己成为命中项
    for path in (
        pathlib.Path(cli.__file__),
        pathlib.Path(cli.__file__).resolve().parents[2] / "tools" / "scripts" / "case_run.py",
    ):
        assert needle not in path.read_text(encoding="utf-8"), "%s 里出现了写死端口" % path


# --------------------------------------------------------------------------- #
# 7. 方案（suite）运行
# --------------------------------------------------------------------------- #


def test_suite_run_aggregates_cases(bench: FakeBench) -> None:
    code, payload = run_json(["run", "--suite", "fake_suite", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL                      # 有一例 fail → 1
    assert payload["suite_id"] == "fake_suite"
    verdicts = {item["case_id"]: item["verdict"] for item in payload["cases"]}
    assert verdicts == {"fake_ok": "pass", "fake_fail": "fail"}
    assert payload["verdict"] == "fail"


def test_suite_stop_on_fail_marks_remaining_cases_not_run(bench: FakeBench) -> None:
    code, payload = run_json(["run", "--suite", "fake_suite_stop", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL
    by_id = {item["case_id"]: item for item in payload["cases"]}
    assert by_id["fake_fail"]["verdict"] == "fail"
    assert by_id["fake_ok"]["skipped_reason"], "首败之后用例未执行必须留原因"
    assert by_id["fake_ok"]["counts"]["pass"] == 0     # 没跑就不许有 pass


def test_suite_params_validated_against_union(bench: FakeBench) -> None:
    """方案里只有部分用例需要某参数时，该参数仍合法（按并集判多余）。"""
    code, payload = run_json([
        "run", "--suite", "fake_suite", "--params", json.dumps({"addr": ADDR2}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_FAIL                        # 校验通过（不是 3），跑出真实结论
    assert payload["params"] == {"addr": ADDR2}

    code, payload = run_json([
        "run", "--suite", "fake_suite", "--params", json.dumps({"nope": 1}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_USAGE
    assert any("多余参数" in problem for problem in payload["problems"])


def test_suite_with_missing_case_is_reported_and_blocks_run(bench: FakeBench) -> None:
    """方案引用了不存在的用例：``check`` 要点出来（loader 不查这条），``run`` 要拒跑。"""
    (bench.root / "case_suites" / "broken_suite.json").write_text(
        json.dumps(
            {"id": "broken_suite", "name": "坏方案", "case_ids": ["no_such_case"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    code, payload = run_json(["check", "--json", "--data-dir", str(bench.root)])
    assert code == EXIT_DECLARATION
    assert payload["suite_reference_problems"]
    assert any("no_such_case" in problem for problem in payload["suite_reference_problems"])

    code, _out, err = run_cli(["run", "--suite", "broken_suite", "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert "不存在的用例" in err


# --------------------------------------------------------------------------- #
# 8. 临时用例文件（--case-file）：不入库也能跑
# --------------------------------------------------------------------------- #

def test_case_file_runs_without_being_registered(bench: FakeBench, tmp_path: pathlib.Path) -> None:
    case_file = tmp_path / "adhoc.json"
    case_file.write_text(json.dumps({
        "id": "adhoc", "title": "临时用例", "steps": [_step("跑假块")],
        "needs": ["fake.never"], "blocks": ["fake.pass"], "asserts": [],
    }, ensure_ascii=False), encoding="utf-8")
    code, payload = run_json(["run", "--case-file", str(case_file), "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_INCOMPLETE          # 前置事实 unknown → 块 skipped
    assert payload["case_id"] == "adhoc"
    assert payload["blocked_block"] == "fake.pass"

    code, out, err = run_cli(["show", "--case-file", str(case_file),
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK and "临时用例" in out


def test_case_file_with_unknown_block_is_usage_error(
    bench: FakeBench, tmp_path: pathlib.Path
) -> None:
    case_file = tmp_path / "bad.json"
    case_file.write_text(json.dumps({
        "id": "bad", "title": "坏用例", "steps": [_step("x")],
        "needs": [], "blocks": ["no.such.block"], "asserts": [],
    }, ensure_ascii=False), encoding="utf-8")
    code, _out, err = run_cli(["run", "--case-file", str(case_file),
                               "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert "不存在的块" in err


def test_case_file_invalid_json_is_usage_error(bench: FakeBench, tmp_path: pathlib.Path) -> None:
    case_file = tmp_path / "broken.json"
    case_file.write_text("{not json", encoding="utf-8")
    code, _out, err = run_cli(["run", "--case-file", str(case_file),
                               "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert "JSON 解析失败" in err


# --------------------------------------------------------------------------- #
# 9. 真跑演示（假块 + 假事实）：跑通一条用例，贴完整 JSON
# --------------------------------------------------------------------------- #


def test_real_run_demo_prints_complete_json(bench: FakeBench) -> None:
    """端到端：装配 → 执行 → 输出（这条就是「只给参数拿结果」的最小闭环）。"""
    code, payload = run_json(["run", "--case", "fake_ok", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert payload["verdict"] == "pass"
    assert payload["counts"] == {"pass": 1, "fail": 0, "skipped": 0, "unknown": 0,
                                 "total": 1}
    block = payload["blocks"][0]
    assert block["block_id"] == "fake.pass"
    assert block["outcome"] == "pass"
    assert block["assertion_id"] == "fake_ok.fake.pass"
    assert payload["run_id"]
    # 人读输出与 JSON 结论一致（同一份 RunOutcome）
    code2, out, _err = run_cli(["run", "--case", "fake_ok", "--data-dir", str(bench.root)])
    assert code2 == EXIT_OK and "结论：PASS" in out
