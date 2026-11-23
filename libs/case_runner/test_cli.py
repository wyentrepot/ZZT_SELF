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
6. **``base_url`` 服务发现四级**（REQS-0042 D2-3）：``--base-url`` > ``WORKBENCH_BASE``
   > 运行时文件 ``data/runtime/workbench.json`` > 本机默认端口（末档打提示）；
7. **真跑**：用假块 + 假事实探测源跑通一条用例并给出完整 ``CaseRunResult.to_dict()``。

参数名口径（REQS-0042 D2-1）：地址参数只叫 ``meter_addr``（多值 ``meter_addrs``）；
``--params`` 里传旧名 ``addr`` / ``meters`` 一律退出码 3 + 迁移提示，**没有兼容别名**。

全部脱离台架：假块执行体、假事实探测体、假能力对象、临时声明根（``tmp_path``），
不发起任何真实 HTTP、不碰真串口。**工作台地址发现也被整文件隔离**（见
``isolate_service_discovery``）——仓库里真实的 ``data/runtime/workbench.json``
（工作台一起来就有）绝不能被单测读走，否则 ``probe --all`` 会真的下发帧。
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
from case_runner.core import defaults
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
#: 台架真实表地址口径（REQS-0042 D2-2 的出厂默认）
METER_ADDR = "123456789012"
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INCOMPLETE = 2
EXIT_USAGE = 3
EXIT_DECLARATION = 4
EXIT_BLOCKED = 5        # blocked：台架/前置不具备（与 cli.EXIT_BLOCKED 同值）

#: 末档默认地址（从常量推出来，测试里不重复写端口）
DEFAULT_URL = "http://127.0.0.1:%d" % cli.DEFAULT_PORT

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
        "action": {"kind": "fake", "params": {"meter_addr": ADDR, "mode": "ok"}},
    })
    _write(root / "blocks" / "fake_fail.json", {
        "id": "fake.fail", "title": "假块·失败",
        "action": {"kind": "fake", "params": {"meter_addr": ADDR}},
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
            "meter_addr": {"type": "string", "pattern": "^[0-9]{12}$", "required": True},
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


@pytest.fixture(autouse=True)
def isolate_service_discovery(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """整文件隔离「工作台地址发现」，并返回被隔离的运行时文件路径。

    为什么必须隔离（REQS-0042 D2-3 落地后的安全底线）：
    工作台一旦启动就会写仓库里的 ``data/runtime/workbench.json``，里面是**真实端口**。
    单测若顺着它去发现地址，``probe --all`` 会真的跑块执行体（建档 / 重启路由 /
    复位 STA），等于单测直接动台架。所以整个文件的默认状态是：

    - 运行时文件指向 ``tmp_path`` 下（**绝不读仓库里那份真实文件**）；
    - 末档默认关掉（``DEFAULT_BASE_URL = ""``）→ 什么都没发现时 ``("", "none")``；
    - 环境变量 ``WORKBENCH_BASE`` 清掉；cwd 换到临时目录（证明发现逻辑不靠 cwd）。

    需要测末档 / 运行时文件档的用例，自己在这个 fixture 之后把对应的常量或文件写回来，
    ``DEFAULT_URL`` 是从 ``cli.DEFAULT_PORT`` 推出来的，测试里不重复写端口。
    """

    runtime_file = tmp_path / "runtime" / "workbench.json"
    monkeypatch.setattr(cli, "RUNTIME_FILE", runtime_file)
    monkeypatch.setattr(cli, "DEFAULT_BASE_URL", "")
    # 结果落盘根也必须隔离：否则每次 `run` 都会往仓库 data/case_runs/ 里写 dir
    # （单测不许碰 data/**）。需要断言落盘内容的用例自己传 --result-dir。
    monkeypatch.setattr(cli, "DEFAULT_RESULT_DIR", tmp_path / "case_runs")
    monkeypatch.delenv(cli.ENV_BASE_URL, raising=False)
    monkeypatch.chdir(tmp_path)
    return runtime_file


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
    assert ready["required"] == ["meter_addr"]


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
    assert set(params) == {"meter_addr", "count", "mode"}
    assert params["meter_addr"]["required_by"] == ["fake.ready"]

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


def test_show_block_prints_real_action_keys_not_empty_object() -> None:
    """渲染瑕疵回归：块摘要曾取错键名，常驻输出一行 ``action：{}``（纯噪音）。"""
    code, out, _err = run_cli(["show", "archive.add"])        # 真声明根，只读
    assert code in (EXIT_OK, EXIT_DECLARATION), code
    assert "action：{}" not in out, out
    assert "action 键：afn、fn、params" in out, out
    assert "action.params：" in out, out


def test_show_meter_addr_default_comes_from_config() -> None:
    """``show`` 要能看见 ``meter_addr`` 的默认值，且来源是两层配置（D2-1 + D2-2）。"""
    expected = defaults.default_meter_addr()
    assert expected, "出厂默认 case_defaults.json 里必须有 meter_addr"
    code, payload = run_json(["show", "archive.add", "--json"])
    assert code in (EXIT_OK, EXIT_DECLARATION), code
    definition = payload["param_spec"]["params"]["meter_addr"]
    assert definition["has_default"] is True
    assert definition["default"] == expected
    assert definition["default_source"] == "块声明"      # loader 已把 null 解析成真值

    code, out, _err = run_cli(["show", "archive.add"])
    assert code in (EXIT_OK, EXIT_DECLARATION), code
    assert "meter_addr" in out and expected in out
    assert "配置默认" in out and "case_defaults.json" in out


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


def test_preflight_blocks_whole_run_when_needed_fact_is_no(bench: FakeBench) -> None:
    """VF-2：前置事实不满足 → **整轮 blocked**（退出码 5），且一个块都不许跑。"""
    bench.truth["fake.ready"] = "no"
    code, payload = run_json([
        "run", "--case", "fake_blocked", "--params", json.dumps({"meter_addr": ADDR}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_BLOCKED
    assert payload["verdict"] == "blocked"
    assert payload["counts"]["pass"] == 0
    assert payload["counts"]["total"] == 0, "预检不过不许逐用例跑出任何块记录"
    assert bench.recorder.block_calls == []
    # 缺项清单：事实 id + 三态 + 原因
    assert payload["unmet_facts"]["fake.ready"]["state"] == "no"
    assert payload["unmet_facts"]["fake.ready"]["reason"]
    assert payload["preflight"]["ok"] is False
    assert payload["interventions"] == []
    assert "预检未通过" in payload["skipped_reason"]
    kinds = [item["kind"] for item in payload["decisions"]]
    assert "preflight" in kinds, "预检本身也要留证据（D4-4）"


def test_gated_block_is_blocked_not_inconclusive_without_preflight(bench: FakeBench) -> None:
    """``--no-preflight``：逐用例跑到一半发现前置不满足 → 块态 skipped 落 **blocked**。

    §3.5：``skipped``（台架/前置不具备）→ 用例 ``blocked``；``unknown``（说不清）才落
    ``inconclusive``。这两档在退出码上分别是 5 与 2（D2-6 六档）。
    """
    bench.truth["fake.ready"] = "no"
    code, payload = run_json([
        "run", "--case", "fake_blocked", "--params", json.dumps({"meter_addr": ADDR}),
        "--no-preflight", "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_BLOCKED
    assert payload["verdict"] == "blocked"
    assert payload["counts"]["skipped"] == 1
    assert payload["counts"]["pass"] == 0
    # 断点：阻断块 + 未满足事实都要给出
    assert payload["blocked_block"] == "fake.gated"
    assert payload["unmet_facts"]["fake.ready"]["state"] == "no"


def test_no_fake_pass_when_fact_unknown(bench: FakeBench) -> None:
    """``unknown`` 永不折算 pass；且**事实 unknown ≠ 块态 unknown**（两档结论不同）。

    - 事实 ``unknown``（台架就绪度没验出来）→ 预检判「未满足」→ 整轮 ``blocked``（5）；
    - 块态 ``unknown``（超时无应答 / 解析不出）→ 用例 ``inconclusive``（2），
      由 ``test_exit_code_2_when_block_unknown`` 守着。
    """
    bench.truth["fake.ready"] = "unknown"
    code, payload = run_json([
        "run", "--case", "fake_blocked", "--params", json.dumps({"meter_addr": ADDR}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_BLOCKED
    assert payload["verdict"] == "blocked"
    assert payload["counts"]["pass"] == 0
    assert all(block["state"] != "pass" for block in payload["blocks"])
    assert payload["unmet_facts"]["fake.ready"]["state"] == "unknown"


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
        (json.dumps({"meter_addr": 123}), "类型非法"),
        (json.dumps({"meter_addr": ADDR, "count": "many"}), "类型非法"),
        (json.dumps({"meter_addr": "12345"}), "取值非法"),        # 事实 schema 的 pattern
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
    """``fake.ready`` 的 meter_addr 是必填，而 ``fake.gated`` 没声明取值 → 必须报缺必填。"""
    code, payload = run_json(["run", "--case", "fake_blocked", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert any("缺少必填参数 'meter_addr'" in problem for problem in payload["problems"])
    assert bench.recorder.block_calls == []


def test_required_param_falls_back_to_unique_block_declaration(bench: FakeBench) -> None:
    """块声明里唯一确定的取值可以顶替 --params（不给也能跑），但要真的传下去。"""
    code, payload = run_json(["run", "--case", "fake_fail", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL  # 跑起来了（不是 3），结论来自假块的 fail
    assert payload["params"] == {"meter_addr": ADDR}
    assert bench.recorder.fact_calls, "事实必须拿到 meter_addr 才会被探测"
    assert bench.recorder.fact_calls[0][1]["meter_addr"] == ADDR


def test_params_are_broadcast_to_block_and_fact(bench: FakeBench) -> None:
    code, _payload = run_json([
        "run", "--case", "fake_fail", "--params", json.dumps({"meter_addr": ADDR2}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_FAIL
    block_params = [params for _kind, params in bench.recorder.block_calls]
    assert any(item.get("meter_addr") == ADDR2 for item in block_params), block_params
    assert bench.recorder.fact_calls[0][1]["meter_addr"] == ADDR2


def test_meter_addr_params_override_the_declared_default(bench: FakeBench) -> None:
    """``--params '{"meter_addr": ...}'`` 覆盖声明默认值，且真的传到块与事实。"""
    code, payload = run_json([
        "run", "--case", "fake_fail", "--params", json.dumps({"meter_addr": METER_ADDR}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_FAIL, payload          # 校验通过（不是 3），跑出真实结论
    assert payload["params"] == {"meter_addr": METER_ADDR}
    assert METER_ADDR != ADDR                  # 确实与声明默认值不同
    block_params = [params for _kind, params in bench.recorder.block_calls]
    assert any(item.get("meter_addr") == METER_ADDR for item in block_params), block_params
    assert bench.recorder.fact_calls[0][1]["meter_addr"] == METER_ADDR


@pytest.mark.parametrize(
    "old_name, new_name",
    [("addr", "meter_addr"), ("meters", "meter_addrs")],
)
def test_old_param_name_is_hard_rejected_with_migration_hint(
    bench: FakeBench, old_name: str, new_name: str
) -> None:
    """REQS-0042 D2-1：旧参数名**不留兼容别名**，直接退出码 3 + 迁移写法。"""
    code, payload = run_json([
        "run", "--case", "fake_fail",
        "--params", json.dumps({old_name: ADDR}), "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_USAGE
    assert payload["ok"] is False and payload["error"] == "usage_error"
    joined = "\n".join(payload["problems"])
    assert old_name in joined and new_name in joined
    assert "D2-1" in joined and "不再兼容旧名" in joined
    assert '{"%s"' % new_name in joined        # 给出可直接抄的迁移写法
    # 关键：参数校验失败时块执行体与事实探测体都没被调用（不进执行体）
    assert bench.recorder.block_calls == []
    assert bench.recorder.fact_calls == []


def test_old_param_name_error_is_readable_in_human_mode(bench: FakeBench) -> None:
    code, _out, err = run_cli([
        "run", "--case", "fake_fail", "--params", json.dumps({"addr": ADDR}),
        "--data-dir", str(bench.root),
    ])
    assert code == EXIT_USAGE
    assert "已改名" in err and "meter_addr" in err


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
    """六档退出码必须写进 ``--help``（REQS-0042 D2-6 / D4-5）。"""
    code = cli.main(["--help"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    for token in (
        "0  pass", "1  fail", "2  inconclusive", "3  用法/参数错误",
        "4  声明加载失败", "5  blocked",
        # 新开关同样要在帮助里说清用途
        "--timeout", "--no-preflight", "--result-dir",
    ):
        assert token in captured.out, token


# --------------------------------------------------------------------------- #
# 5. probe --all 铁律：无台架 → 全部 unknown，绝无 pass
# --------------------------------------------------------------------------- #


def test_probe_all_real_declarations_without_bench_is_all_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """铁律（真声明根 + 真探测体）：没有工作台地址时，13 块 / 7 事实必须全 unknown。

    **安全前提**：地址发现已被 ``isolate_service_discovery`` 隔离成 ``("", "none")``。
    否则（工作台在本机起着时）``probe --all`` 会真的跑块执行体——建档 / 重启路由 /
    复位 STA 都会落到台架上。单测一次台架都不许碰，这条断言就是门槛。
    """
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
# 6. base_url 服务发现四级（REQS-0042 D2-3：--base-url > env > 运行时文件 > 末档）
# --------------------------------------------------------------------------- #


def _write_runtime_file(path: pathlib.Path, payload: dict[str, Any]) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_resolve_base_url_priority(
    monkeypatch: pytest.MonkeyPatch, isolate_service_discovery: pathlib.Path
) -> None:
    """四级优先级：每一档都要能压过下一档，来源如实回报。"""
    runtime_file = isolate_service_discovery
    # 隔离态：运行时文件不存在 + 末档关掉 → 真的什么都没有
    assert cli.resolve_base_url(None) == ("", "none")

    # 第 3 档：运行时文件（工作台启动时写）
    _write_runtime_file(runtime_file, {
        "host": "0.0.0.0", "port": 8790, "pid": 1,
        "started_at": "2026-11-23T15:00:00+08:00",
        "base_url": "http://runtime.invalid:3",
    })
    assert cli.resolve_base_url(None) == ("http://runtime.invalid:3", "runtime")

    # 第 2 档：环境变量压过运行时文件
    monkeypatch.setenv(cli.ENV_BASE_URL, "http://env.invalid:1")
    assert cli.resolve_base_url(None) == ("http://env.invalid:1", "env")

    # 第 1 档：--base-url 压过环境变量与运行时文件
    assert cli.resolve_base_url("http://cli.invalid:2") == ("http://cli.invalid:2", "cli")


def test_default_base_url_is_the_last_tier_and_prints_notice(
    bench: FakeBench, monkeypatch: pytest.MonkeyPatch, isolate_service_discovery: pathlib.Path
) -> None:
    """什么都没发现 → 末档默认地址可跑，**且必须打一行提示**（D2-3 硬要求）。"""
    monkeypatch.setattr(cli, "DEFAULT_BASE_URL", DEFAULT_URL)
    assert not isolate_service_discovery.exists()
    code, out, err = run_cli(["run", "--case", "fake_env_case",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert bench.env_seen == [DEFAULT_URL], "末档地址必须真的灌进 WORKBENCH_BASE"
    assert "未发现 workbench.json" in out
    assert "按本机默认端口 %d" % cli.DEFAULT_PORT in out
    assert err == "" or "未发现 workbench.json" not in err   # 人读态提示在 stdout


def test_default_tier_notice_goes_to_stderr_in_json_mode(
    bench: FakeBench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--json`` 时提示走 stderr：stdout 必须仍是可 ``json.loads`` 的纯 JSON。"""
    monkeypatch.setattr(cli, "DEFAULT_BASE_URL", DEFAULT_URL)
    code, out, err = run_cli(["run", "--case", "fake_env_case", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    payload = json.loads(out)                       # 纯 JSON，没被提示污染
    assert payload["base_url"] == DEFAULT_URL
    assert payload["base_url_source"] == "default"
    assert payload["base_url_note"]
    assert "未发现 workbench.json" in err


def test_runtime_file_host_port_normalized_to_loopback(
    isolate_service_discovery: pathlib.Path,
) -> None:
    """只有 host+port、且 host 是监听用的 ``0.0.0.0`` → 必须归一成回环才能拨号。"""
    _write_runtime_file(isolate_service_discovery,
                        {"host": "0.0.0.0", "port": 8790, "pid": 1})
    assert cli.runtime_base_url() == "http://127.0.0.1:8790"
    assert cli.resolve_base_url(None) == ("http://127.0.0.1:8790", "runtime")
    # 显式 127.0.0.1 原样保留
    _write_runtime_file(isolate_service_discovery, {"host": "127.0.0.1", "port": 9001})
    assert cli.resolve_base_url(None) == ("http://127.0.0.1:9001", "runtime")


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"host": "127.0.0.1"}',                    # 没有 port
        '{"port": 99999}',                          # 端口越界
        '{"base_url": "not-a-url", "port": 0}',      # base_url 不是 http(s)
    ],
)
def test_broken_runtime_file_never_raises_and_falls_through(
    content: str, monkeypatch: pytest.MonkeyPatch, isolate_service_discovery: pathlib.Path
) -> None:
    """运行时文件坏掉 = 只是少一条线索：不抛异常，落到下一档。"""
    isolate_service_discovery.parent.mkdir(parents=True, exist_ok=True)
    isolate_service_discovery.write_text(content, encoding="utf-8")
    assert cli.runtime_base_url() == ""
    assert cli.resolve_base_url(None) == ("", "none")
    monkeypatch.setattr(cli, "DEFAULT_BASE_URL", DEFAULT_URL)
    assert cli.resolve_base_url(None) == (DEFAULT_URL, "default")


def test_runtime_file_makes_zero_arg_run_work(
    bench: FakeBench, isolate_service_discovery: pathlib.Path
) -> None:
    """零参可跑：不传 --base-url、不设环境变量，靠运行时文件发现服务。"""
    _write_runtime_file(isolate_service_discovery, {
        "host": "0.0.0.0", "port": 8790,
        "base_url": "http://runtime.invalid:3",
    })
    code, payload = run_json(["run", "--case", "fake_env_case", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert bench.env_seen == ["http://runtime.invalid:3"]
    assert payload["base_url_source"] == "runtime"


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
    assert payload["base_url"] == "http://cli.invalid:2"
    assert payload["base_url_source"] == "cli"
    assert payload["verdict"] == "pass"


def test_env_base_url_is_used_when_flag_absent(
    bench: FakeBench, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.ENV_BASE_URL, "http://env.invalid:1")
    code, _payload = run_json(["run", "--case", "fake_env_case", "--json",
                               "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert bench.env_seen == ["http://env.invalid:1"]


def test_nothing_discovered_means_empty_when_default_tier_disabled(
    bench: FakeBench,
) -> None:
    """末档常量关掉时（隔离态）不得偷偷塞任何端口：入口层不许有隐藏默认。"""
    code, payload = run_json(["run", "--case", "fake_env_case", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_FAIL           # 假块在「没有地址」时给 fail，证明入口没偷偷塞默认值
    assert bench.env_seen == [""]
    assert payload["base_url"] == ""
    assert payload["base_url_source"] == "none"


def test_port_literal_only_in_the_single_default_constant() -> None:
    """D2-3 之后端口只许出现**一处**：末档默认常量。

    老口径是「入口文件里连注释都不许出现端口」；末档默认端口落地后那条断言必须换口径
    （REQS-0042 D2-5 允许改旧预期），否则 D2-3 与它直接互斥。新口径：
    端口字面量只能出现在定义 ``DEFAULT_PORT`` / ``DEFAULT_BASE_URL`` 的行上，
    其它地方一律拼常量；入口脚本 ``tools/scripts/case_run.py`` 仍然一个都不许有。
    """
    needle = "87" + "90"      # 拆开写，避免本测试文件自己成为命中项
    text = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    hits = [line.strip() for line in text.splitlines() if needle in line]
    assert hits, "末档默认端口常量不见了（D2-3 要求零参可跑）"
    for line in hits:
        assert "DEFAULT_PORT" in line or "DEFAULT_BASE_URL" in line, line
    entry = (
        pathlib.Path(cli.__file__).resolve().parents[2]
        / "tools" / "scripts" / "case_run.py"
    )
    assert needle not in entry.read_text(encoding="utf-8"), "入口脚本里出现了写死端口"


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
        "run", "--suite", "fake_suite", "--params", json.dumps({"meter_addr": ADDR2}),
        "--json", "--data-dir", str(bench.root),
    ])
    assert code == EXIT_FAIL                        # 校验通过（不是 3），跑出真实结论
    assert payload["params"] == {"meter_addr": ADDR2}

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
        "needs": [], "blocks": ["fake.pass"], "asserts": [],
    }, ensure_ascii=False), encoding="utf-8")
    code, payload = run_json(["run", "--case-file", str(case_file), "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert payload["case_id"] == "adhoc"
    assert payload["verdict"] == "pass"

    code, out, err = run_cli(["show", "--case-file", str(case_file),
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK and "临时用例" in out


def test_case_file_with_unknown_need_is_blocked_by_preflight(
    bench: FakeBench, tmp_path: pathlib.Path
) -> None:
    """临时用例的 ``needs`` 同样进预检：unknown 事实 → 整轮 blocked（不跑块）。"""
    case_file = tmp_path / "adhoc.json"
    case_file.write_text(json.dumps({
        "id": "adhoc", "title": "临时用例", "steps": [_step("跑假块")],
        "needs": ["fake.never"], "blocks": ["fake.pass"], "asserts": [],
    }, ensure_ascii=False), encoding="utf-8")
    code, payload = run_json(["run", "--case-file", str(case_file), "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_BLOCKED
    assert payload["case_id"] == "adhoc"
    assert payload["verdict"] == "blocked"
    assert payload["unmet_facts"]["fake.never"]["state"] == "unknown"
    assert bench.recorder.block_calls == []

    # 关掉预检 → 逐用例跑到闸门：块态 skipped → blocked（§3.5），断点在 fake.pass
    code, payload = run_json(["run", "--case-file", str(case_file), "--no-preflight",
                              "--json", "--data-dir", str(bench.root)])
    assert code == EXIT_BLOCKED
    assert payload["blocked_block"] == "fake.pass"
    assert payload["counts"]["pass"] == 0


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


# --------------------------------------------------------------------------- #
# 10. 步骤实例化（REQS-0042 D3-1）：步骤表 / step_id / 每步参数
# --------------------------------------------------------------------------- #


def _write_plan_case(bench: FakeBench, case_id: str = "fake_planned", **over: Any) -> None:
    """往临时声明根里加一条 ``plan`` 用例（同块两次、各带不同表地址）。"""
    payload: dict[str, Any] = {
        "id": case_id,
        "title": "假用例·同一块两次实例化",
        "steps": [_step("同一下发动作跑两次，各带一块表地址")],
        "needs": [],
        "plan": [
            {"id": "add_a", "block": "fake.pass", "params": {"meter_addr": "000000000011"}},
            {
                "id": "add_b",
                "block": "fake.pass",
                "params": {"meter_addr": "000000000022"},
                "on_fail": "continue",
            },
        ],
        "asserts": [],
        "tags": ["fake", "single-test-only"],
    }
    payload.update(over)
    _write(bench.root / "cases" / ("%s.json" % case_id), payload)


def test_run_json_blocks_carry_step_id(bench: FakeBench) -> None:
    _write_plan_case(bench)
    code, payload = run_json(["run", "--case", "fake_planned", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK, payload
    assert [block["step_id"] for block in payload["blocks"]] == ["add_a", "add_b"]
    assert [block["block_id"] for block in payload["blocks"]] == ["fake.pass", "fake.pass"]
    # 同块两次 → 两次下发参数不同（用户诉求：一次下发多块不同档案）
    assert [block["params"]["meter_addr"] for block in payload["blocks"]] == [
        "000000000011", "000000000022",
    ]
    # 汇总里能看出每个 step 的结论
    assert [item["step_id"] for item in payload["steps"]] == ["add_a", "add_b"]
    assert [item["outcome"] for item in payload["steps"]] == ["pass", "pass"]


def test_run_params_override_step_params(bench: FakeBench) -> None:
    """``--params`` 覆盖 step.params（D3-2：run 级 > step.params > 声明默认）。"""
    _write_plan_case(bench)
    code, payload = run_json([
        "run", "--case", "fake_planned", "--json",
        "--params", json.dumps({"meter_addr": "999999999999"}),
        "--data-dir", str(bench.root),
    ])
    assert code == EXIT_OK, payload
    assert [block["params"]["meter_addr"] for block in payload["blocks"]] == [
        "999999999999", "999999999999",
    ]


def test_run_without_params_keeps_step_params(bench: FakeBench) -> None:
    """不传 ``--params`` 时 step.params 生效（不是被块声明的默认值盖掉）。"""
    _write_plan_case(bench)
    code, _payload = run_json(["run", "--case", "fake_planned", "--json",
                               "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert [call[1].get("meter_addr") for call in bench.recorder.block_calls] == [
        "000000000011", "000000000022",
    ]


def test_run_json_summarises_multiple_cases_with_step_ids(bench: FakeBench) -> None:
    """方案（多用例）分支的每块记录也要带 step_id。"""
    _write_plan_case(bench)
    _write(bench.root / "case_suites" / "fake_suite_plan.json", {
        "id": "fake_suite_plan", "name": "假方案·含 steps 实例化",
        "case_ids": ["fake_ok", "fake_planned"], "stop_on_fail": False,
    })
    code, payload = run_json(["run", "--suite", "fake_suite_plan", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK, payload
    cases = {item["case_id"]: item for item in payload["cases"]}
    assert [b["step_id"] for b in cases["fake_planned"]["blocks"]] == ["add_a", "add_b"]
    assert [b["step_id"] for b in cases["fake_ok"]["blocks"]] == ["fake.pass"]


def test_show_case_has_step_table(bench: FakeBench) -> None:
    _write_plan_case(bench)
    code, payload = run_json(["show", "fake_planned", "--json",
                              "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    table = payload["item"]["steps_table"]
    assert [row["step_id"] for row in table] == ["add_a", "add_b"]
    assert [row["block_id"] for row in table] == ["fake.pass", "fake.pass"]
    assert table[0]["on_fail"] == "继承"           # 未显式声明 → 继承 run/方案级
    assert table[1]["on_fail"] == "continue"
    assert "000000000011" in json.dumps(table[0], ensure_ascii=False)

    code, out, _err = run_cli(["show", "fake_planned", "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert "步骤表" in out and "add_a" in out and "add_b" in out and "on_fail" in out


def test_show_real_case_has_step_table_readonly() -> None:
    """真声明根：``show meter_first_join`` 要有步骤表（plan 展开，只读不跑块）。

    REQS-0042 v9 D9-1 起抄表拆成 ``meter_daily``（不复位）/ ``meter_first_join``（首次组网），
    后者含 ``meter.addr_probe``，正好覆盖「步骤表 + 绑表步骤可见」这条意图。
    """
    code, out, _err = run_cli(["show", "meter_first_join"])
    assert code in (EXIT_OK, EXIT_DECLARATION), out
    assert "步骤表" in out, out
    assert "meter.addr_probe" in out


def test_show_real_plan_case_lists_step_params_readonly() -> None:
    """真声明根里的 plan 用例（archive_multi）：步骤表要显示各步参数与 on_fail。"""
    code, out, _err = run_cli(["show", "archive_multi"])
    assert code in (EXIT_OK, EXIT_DECLARATION), out
    assert "add_a" in out and "add_b" in out, out
    assert "000000000011" in out and "000000000022" in out, out


def test_check_hints_when_blocks_and_plan_both_given(bench: FakeBench) -> None:
    """两者都给 → 不报错，但给一条提示（说清以 plan 为准）。"""
    _write_plan_case(bench, case_id="fake_both", blocks=["fake.pass"])
    code, payload = run_json(["check", "--json", "--data-dir", str(bench.root)])
    assert code == EXIT_OK, payload
    assert payload["problems"] == []
    notices = "\n".join(payload.get("notices") or [])
    assert "fake_both" in notices and "plan" in notices


def test_check_catches_plan_referencing_unknown_block(bench: FakeBench) -> None:
    """plan 引用的块不存在 → 报错（loader 校验层），错误信息点名用例与 step。"""
    _write_plan_case(
        bench,
        case_id="fake_bad_plan",
        plan=[{"id": "ghost_step", "block": "no.such.block"}],
    )
    code, payload = run_json(["check", "--json", "--data-dir", str(bench.root)])
    assert code == EXIT_DECLARATION, payload
    joined = "\n".join(payload["problems"])
    assert "fake_bad_plan" in joined and "ghost_step" in joined and "no.such.block" in joined


def test_case_file_plan_unknown_block_is_usage_error(
    bench: FakeBench, tmp_path: pathlib.Path
) -> None:
    case_file = tmp_path / "plan_bad.json"
    case_file.write_text(json.dumps({
        "id": "plan_bad", "title": "坏 plan", "steps": [_step("x")],
        "needs": [], "plan": [{"id": "s1", "block": "no.such.block"}], "asserts": [],
    }, ensure_ascii=False), encoding="utf-8")
    code, _out, err = run_cli(["run", "--case-file", str(case_file),
                               "--data-dir", str(bench.root)])
    assert code == EXIT_USAGE
    assert "不存在的块" in err


def test_run_human_output_marks_steps_when_block_repeats(bench: FakeBench) -> None:
    """人读输出：同块多步时按步骤顺序列明细，并给出行内步骤结论（不靠块 id 猜）。"""
    _write_plan_case(bench)
    code, out, _err = run_cli(["run", "--case", "fake_planned",
                               "--data-dir", str(bench.root)])
    assert code == EXIT_OK
    assert "按步骤顺序" in out
    assert "tenant" not in out            # 没有把别的用例的步骤混进来
    assert "add_a" in out and "add_b" in out
    assert "步骤结论：add_a=pass、add_b=pass" in out
