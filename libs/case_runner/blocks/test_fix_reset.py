"""blocks 层单测：台架块/事实（``fix.py``/``facts_bench.py``）、复位块（``reset.py``）、
声明加载器（``loader.py``）与包入口（``blocks/__init__.py``）。

全部用**假 HTTP / 假时钟**，不依赖真台架（本机 8790 的 ``mapping_id=simcon`` 尚未生效，
见 REQS §10.10 事实 L/M，任何集成路径都必须可跳过）。

必覆盖（任务书要求）：
- 缺依赖 → ``skipped``；复位指纹命中 → ``pass``；指纹缺失、重发仍缺失 → ``fail``；
- ``unknown`` 绝不折算 ``pass``（含「日志读不到」「事实 unknown」「断言求值器缺失」三条路径）；
- 每个测试都 ``clear_registries()`` 隔离（autouse fixture）。
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

import pytest

from case_runner.blocks import common, facts_bench, fix, loader, reset
from case_runner.capabilities.module import ModuleCapability
from case_runner.capabilities.simeter import SimeterCapability
from case_runner.capabilities.simcon import SimconCapability
from case_runner.core.executor import BlockResult, RunContext, execute_case
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_UNKNOWN,
    BlockSpec,
    CaseSpec,
    CaseStep,
    FactSpec,
)
from case_runner.core.planner import FACT_NO, FACT_UNKNOWN, FACT_YES, FactsContext, FactValue
from case_runner.core.registry import (
    clear_registries,
    get_block,
    get_fact,
    has_block,
    has_fact,
)

FAKE_BASE = "http://127.0.0.1:19302"
NO_ENV = "__CASE_RUNNER_TEST_NO_BASE_URL__"


# --------------------------------------------------------------------------- #
# 隔离：每个测试都清空两张注册表，按需重新注册
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def isolated_registries():
    clear_registries()
    yield
    clear_registries()


def _register_all() -> None:
    fix.register()
    facts_bench.register()
    reset.register()


# --------------------------------------------------------------------------- #
# 假 HTTP（键为 (method, path)，值可为 (status, payload) 或 callable）
# --------------------------------------------------------------------------- #


class FakeTransport:
    def __init__(self, routes=None, errors=None,
                 default=(404, {"detail": "fake: 未配置该路由"})):
        self.routes = dict(routes or {})
        self.errors = dict(errors or {})
        self.default = default
        self.calls = []

    def __call__(self, method, url, body, timeout):
        parsed = urllib.parse.urlparse(url)
        key = (method.upper(), parsed.path)
        self.calls.append({"method": method.upper(), "path": parsed.path,
                           "query": dict(urllib.parse.parse_qsl(parsed.query)),
                           "body": body})
        if key in self.errors:
            raise self.errors[key]
        response = self.routes.get(key, self.default)
        if callable(response):
            response = response(method, url, body)
        return response

    def count(self, method, path):
        return sum(1 for call in self.calls
                   if call["method"] == method.upper() and call["path"] == path)


# --------------------------------------------------------------------------- #
# 台架能力（simeter / simcon）假桩
# --------------------------------------------------------------------------- #


def _simeter(routes=None, *, timeout=5.0) -> SimeterCapability:
    transport = FakeTransport(routes)
    return SimeterCapability(FAKE_BASE, transport=transport, timeout=timeout,
                             probe_timeout=timeout, retry_backoff=0.0, env_var=NO_ENV)


def _simcon(routes=None, *, timeout=5.0) -> SimconCapability:
    transport = FakeTransport(routes)
    return SimconCapability(FAKE_BASE, transport=transport, timeout=timeout,
                            probe_timeout=timeout, retry_backoff=0.0,
                            poll_interval=0.0, env_var=NO_ENV)


SIMETER_STATUS = ("GET", "/api/simeter/status")
SIMCON_STATUS = ("GET", "/api/simcon/status")
SIMCON_LEASE = ("GET", "/api/ai/v2/simcon/lease")


def _simeter_online(*, serial_open=True):
    return {SIMETER_STATUS: (200, {
        "brain": {"addr": "000000000001", "version": "645-2007", "rx_count": 3},
        "serial": {"open": serial_open, "port": "/run/hplc/serial/simeter"} if serial_open else None,
    })}


def _simcon_online(*, is_open=True):
    return {SIMCON_STATUS: (200, {"open": is_open, "port": "COM28" if is_open else None,
                                  "pending_frames": 0})}


# --------------------------------------------------------------------------- #
# 台架块（BL-1）：pass / skipped，绝不崩
# --------------------------------------------------------------------------- #


def test_fix_simeter_session_passes_when_bench_online():
    _register_all()
    ctx = RunContext()
    ctx[fix.EXTRAS_SIMETER] = _simeter(_simeter_online())
    result = get_block(fix.BLOCK_SIMETER_SESSION)({"mapping_id": "listener"}, ctx)
    assert isinstance(result, BlockResult)
    assert result.state == BLOCK_PASS
    assert result.detail.startswith("simeter 在线")
    assert result.facts[fix.FACT_SIMETER_ONLINE].state == FACT_YES


def test_fix_simeter_session_skipped_when_subapp_missing():
    _register_all()
    ctx = RunContext()
    ctx[fix.EXTRAS_SIMETER] = _simeter({})        # 默认 404 = 子应用未挂载
    result = get_block(fix.BLOCK_SIMETER_SESSION)({}, ctx)
    assert result.state == BLOCK_SKIPPED
    assert "bench.simeter_online=no" in result.detail
    assert "未挂载" in result.detail


def test_fix_simeter_session_skipped_but_unknown_when_unreachable():
    _register_all()
    ctx = RunContext()
    ctx[fix.EXTRAS_SIMETER] = _simeter({}, timeout=0.01)
    ctx[fix.EXTRAS_SIMETER].transport = FakeTransport(
        errors={SIMETER_STATUS: ConnectionRefusedError("refused")})
    result = get_block(fix.BLOCK_SIMETER_SESSION)({}, ctx)
    assert result.state == BLOCK_SKIPPED
    assert "bench.simeter_online=unknown" in result.detail, (
        "连不上 = 不知道，不能记成 no，更不能记成 pass")


def test_fix_simeter_require_serial_open_is_strict():
    capability = _simeter(_simeter_online(serial_open=False))
    lenient = fix.judge_simeter(capability)
    strict = fix.judge_simeter(capability, require_serial_open=True)
    assert lenient.state == FACT_YES, "默认口径只看服务可达"
    assert "未打开" in lenient.detail
    assert strict.state == FACT_NO
    assert "表口未打开" in strict.detail


def test_fix_simcon_session_skipped_when_service_unavailable():
    _register_all()
    ctx = RunContext()
    ctx[fix.EXTRAS_SIMCON] = _simcon({SIMCON_STATUS: (503, {"detail": "模块串口服务未启用"})})
    result = get_block(fix.BLOCK_SIMCON_SESSION)({}, ctx)
    assert result.state == BLOCK_SKIPPED
    assert result.facts[fix.FACT_SIMCON_ONLINE].state == FACT_UNKNOWN
    assert "503" in result.detail


def test_fix_simcon_session_passes_when_online():
    _register_all()
    ctx = RunContext()
    ctx[fix.EXTRAS_SIMCON] = _simcon(_simcon_online())
    result = get_block(fix.BLOCK_SIMCON_SESSION)({}, ctx)
    assert result.state == BLOCK_PASS
    assert result.facts[fix.FACT_SIMCON_ONLINE].state == FACT_YES


# --------------------------------------------------------------------------- #
# 台架事实（FT-1/FT-2/FT-3）
# --------------------------------------------------------------------------- #


def test_fact_probe_reuses_block_judgement_function():
    """FT-2：事实探测复用块的判定函数，不另写一份逻辑（同一个函数对象）。"""
    assert facts_bench.judge_simeter is fix.judge_simeter
    assert facts_bench.judge_simcon is fix.judge_simcon
    assert facts_bench.judge_sta_online is common.judge_sta_online
    assert facts_bench.simeter_capability is common.simeter_capability
    assert facts_bench.simcon_capability is common.simcon_capability
    assert fix.simeter_capability is common.simeter_capability
    assert fix.simcon_capability is common.simcon_capability


def test_fact_simeter_online_three_states():
    _register_all()
    yes = get_fact(fix.FACT_SIMETER_ONLINE)({"capability": _simeter(_simeter_online())})
    no = get_fact(fix.FACT_SIMETER_ONLINE)({"capability": _simeter({})})
    unknown = get_fact(fix.FACT_SIMETER_ONLINE)({"capability": SimeterCapability(
        None, env_var=NO_ENV)})
    assert (yes.state, no.state, unknown.state) == (FACT_YES, FACT_NO, FACT_UNKNOWN)


def test_fact_simcon_online_unknown_is_not_yes():
    _register_all()
    capability = _simcon()
    capability.transport = FakeTransport(errors={SIMCON_STATUS: TimeoutError("timeout")})
    value = get_fact(fix.FACT_SIMCON_ONLINE)({"capability": capability})
    assert value.state == FACT_UNKNOWN
    assert not value.satisfied


# --------------------------------------------------------------------------- #
# 执行器整合：缺依赖 → skipped；unknown 不计 pass
# --------------------------------------------------------------------------- #


def _case(blocks, *, needs=(), case_id="case.t"):
    return CaseSpec(
        id=case_id,
        title="t",
        steps=[CaseStep(no=1, do="跑块", expect="有结论")],
        needs=list(needs),
        blocks=list(blocks),
    )


def test_execute_case_bench_unknown_is_skipped_not_pass():
    """台架探测不到 → 块 ``skipped`` → 用例 ``blocked``（不是 pass，也不是 inconclusive）。

    语义来源 REQS-0042 §3.5 / D2-4：块态 ``skipped`` = **台架不具备条件**，聚合处映射成
    ``blocked``（``unknown`` 才是 ``inconclusive``）。
    """
    _register_all()
    spec = BlockSpec.model_validate({
        "id": fix.BLOCK_SIMETER_SESSION, "title": "台架", "action": {"kind": "probe_bench"},
    })
    ctx = RunContext(block_specs={spec.id: spec})
    ctx[fix.EXTRAS_SIMETER] = _simeter({})   # 404 → no → skipped
    result = execute_case(_case([fix.BLOCK_SIMETER_SESSION]), ctx)
    assert result.blocks[0].state == BLOCK_SKIPPED
    assert result.verdict == "blocked"
    assert result.counts["pass"] == 0, "绝不假 PASS"
    assert result.blocked_block == fix.BLOCK_SIMETER_SESSION


def test_execute_case_skips_block_when_needed_fact_unknown():
    _register_all()
    spec = BlockSpec.model_validate({
        "id": fix.BLOCK_SIMETER_SESSION, "title": "台架",
        "needs": ["bench.simcon_online"], "action": {"kind": "probe_bench"},
    })
    facts = FactsContext(probe=lambda fact_id, params: FactValue.unknown("台架未接线"))
    ctx = RunContext(facts_ctx=facts, block_specs={spec.id: spec})
    result = execute_case(_case([fix.BLOCK_SIMETER_SESSION]), ctx)
    record = result.blocks[0]
    assert record.state == BLOCK_SKIPPED
    assert "bench.simcon_online=unknown" in record.detail
    assert result.verdict != "pass"


def test_register_is_idempotent():
    _register_all()
    _register_all()      # 再来一次不得抛 Duplicate
    assert has_block(fix.BLOCK_SIMETER_SESSION)
    assert has_fact(fix.FACT_SIMCON_ONLINE)
    assert has_block(reset.BLOCK_RESET_STA)


# --------------------------------------------------------------------------- #
# 复位块（reset.sta）：假 module 台架 + 假时钟
# --------------------------------------------------------------------------- #


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(float(seconds), 0.0)


class FakeModuleBench:
    """假 module-serial + module-actions 工作台（只回放复位流程的 5 个端点）。

    - ``delays``：第 i 次 ``reboot`` 之后多少秒出现启动指纹（``None`` = 永不出现）
    - ``old_fingerprint``：日志里常驻一条 ``t0`` 之前的旧指纹（用来证伪「拿旧日志当新证据」）
    - ``tx_only_fingerprint``：指纹只出现在 ``[TX]``（我们自己的回显）里
    - ``logs_broken`` / ``ensure_ok`` / ``bridge_usable`` / ``send_fail_attempt``
    - ``send_response``：把 ``send`` 的 HTTP 应答整体替换成 ``(status, payload)``
      （模拟 422 构帧失败这类「帧根本没发出去」的情况）
    """

    def __init__(self, *, delays=(2.0,), old_fingerprint=False,
                 tx_only_fingerprint=False, logs_broken=False, ensure_ok=True,
                 bridge_usable=True, send_fail_attempt=None, send_response=None,
                 fingerprint_text="reset reason: 0x00000002 SFT_GLB_RST"):
        self.clock = FakeClock()
        self.delays = list(delays)
        self.old_fingerprint = old_fingerprint
        self.tx_only_fingerprint = tx_only_fingerprint
        self.logs_broken = logs_broken
        self.ensure_ok = ensure_ok
        self.bridge_usable = bridge_usable
        self.send_fail_attempt = send_fail_attempt
        self.send_response = send_response
        self.fingerprint_text = fingerprint_text
        self.sends: list[dict] = []
        self.start = self.clock.now
        self.transport = self._handle

    # -- HTTP ------------------------------------------------------------- #

    def _handle(self, method, url, body, timeout):
        path = urllib.parse.urlparse(url).path
        if method == "GET" and path == "/api/serial-bridge/status":
            return 200, {"ports": [{
                "id": "sta-main", "windows_com": "COM18",
                "usable": self.bridge_usable,
                "errors": [] if self.bridge_usable else ["桥接软链不存在或目标不是字符设备"],
            }], "summary": {"total": 1}}
        if method == "POST" and path == "/api/ai/v2/module-actions":
            return self._action(body)
        if method == "GET" and path.startswith("/api/ai/v2/jobs/"):
            return self._job(path.rsplit("/", 1)[-1])
        if method == "GET" and path == "/api/module-serial/sessions":
            return 200, {"sessions": [{
                "session_id": "ms-sta", "port_identity": {"mapping_id": "sta-main"},
                "log_file": None, "state": "running"}]}
        if method == "GET" and path.endswith("/logs"):
            return self._logs()
        return 404, {"detail": "fake: 未配置该路由 %s %s" % (method, path)}

    def _action(self, body):
        action = (body or {}).get("action")
        if action == "ensure":
            return 202, {"job_id": "job-ensure", "job_state": "queued"}
        if action == "send":
            attempt = len(self.sends) + 1
            self.sends.append({"text": body.get("text"),
                               "append_newline": body.get("append_newline"),
                               "at": self.clock.now, "attempt": attempt})
            if self.send_response is not None:
                return self.send_response
            if self.send_fail_attempt == attempt:
                return 500, {"detail": "fake: 串口写失败"}
            return 202, {"job_id": "job-send-%d" % attempt, "job_state": "queued"}
        return 422, {"detail": "fake: 不支持的动作 %r" % action}

    def _job(self, job_id):
        if job_id == "job-ensure":
            if not self.ensure_ok:
                return 200, {"job_id": job_id, "job_state": "failed",
                             "summary": "端口被占用，无法开会话", "result": None}
            return 200, {"job_id": job_id, "job_state": "succeeded", "result": {
                "action": "ensure", "owned": True, "session_id": "ms-sta",
                "mapping_id": "sta-main", "state": "running"}}
        if job_id.startswith("job-send-"):
            return 200, {"job_id": job_id, "job_state": "succeeded", "result": {
                "underlying_state": "succeeded",
                "result": {"sent": 8, "channel": "sta", "append_newline": True}}}
        return 404, {"detail": "fake: 未知 job"}

    def _logs(self):
        if self.logs_broken:
            return 500, {"detail": "fake: 内存日志不可用"}
        entries = []
        if self.old_fingerprint:      # t0 之前的旧横幅（不是本次复位的证据）
            entries.append({"seq": 0, "dir": "RX", "text": self.fingerprint_text,
                            "epoch": self.start - 100.0})
        for index, send in enumerate(self.sends):
            delay = self.delays[index] if index < len(self.delays) else None
            if self.tx_only_fingerprint:
                text = self.fingerprint_text if index == 0 else "reboot"
                direction = "TX" if index == 0 else "RX"
                entries.append({"seq": 10 + index, "dir": direction, "text": text,
                                "epoch": send["at"] + 0.5})
                continue
            if delay is None:
                continue
            if self.clock.now >= send["at"] + delay:
                entries.append({"seq": 10 + index, "dir": "RX",
                                "text": self.fingerprint_text,
                                "epoch": send["at"] + delay})
        return 200, {"lines": entries}

    # -- 便捷 ------------------------------------------------------------- #

    @property
    def send_count(self) -> int:
        return len(self.sends)


def _reset_ctx(bench: FakeModuleBench, *, extras=None, block_specs=None):
    capability = ModuleCapability(
        FAKE_BASE, transport=bench.transport, env_var=NO_ENV,
        clock=bench.clock, sleeper=bench.clock.sleep,
        retry_backoff=0.0, poll_interval=0.0, poll_wait_seconds=0.0)
    ctx = RunContext(block_specs=dict(block_specs or {}))
    ctx[reset.EXTRAS_MODULE] = capability
    for key, value in (extras or {}).items():
        ctx[key] = value
    return ctx


def _reset_params(**overrides):
    params = {"kind": "reset_sta", "mapping_id": "sta-main", "module": "sta",
              "text": "reboot", "retry": 1, "timeout_s": 30.0,
              "fingerprint_window_s": 15.0, "poll_interval_s": 1.0}
    params.update(overrides)
    return params


def test_reset_sta_passes_when_fingerprint_appears():
    _register_all()
    bench = FakeModuleBench(delays=(2.0,))
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_PASS
    assert "复位成功" in result.detail
    assert bench.send_count == 1
    assert bench.sends[0]["text"] == "reboot"
    assert bench.sends[0]["append_newline"] is True
    assert result.actual["fingerprint"] == "reset reason:"
    assert result.actual["attempt"] == 1
    assert result.actual["elapsed_s"] == pytest.approx(2.0, abs=0.1)


def test_reset_sta_sends_reboot_to_sta_main_with_crlf_and_records_8_bytes():
    _register_all()
    bench = FakeModuleBench(delays=(1.0,))
    ctx = _reset_ctx(bench)
    result = reset.reset_sta(_reset_params(), ctx)
    assert result.state == BLOCK_PASS
    assert "实发 8 字节" in result.detail, "reboot + CRLF = 8 字节（调研实测口径）"


def test_reset_sta_fails_when_no_fingerprint_after_retry():
    _register_all()
    bench = FakeModuleBench(delays=(None, None))
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_FAIL
    assert "口无响应" in result.detail
    assert bench.send_count == 2, "15s 内无指纹 → 重发一次"
    assert [send["text"] for send in bench.sends] == ["reboot", "reboot"]
    assert len(result.actual["attempts"]) == 2


def test_reset_sta_passes_on_second_attempt():
    _register_all()
    bench = FakeModuleBench(delays=(None, 1.0))
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_PASS
    assert result.actual["attempt"] == 2
    assert bench.send_count == 2


def test_reset_sta_old_fingerprint_before_t0_does_not_count():
    """t0 之前的旧启动横幅不是本次复位的证据 —— 防假 PASS 的关键用例。"""
    _register_all()
    bench = FakeModuleBench(delays=(None, None), old_fingerprint=True)
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_FAIL
    assert "口无响应" in result.detail


def test_reset_sta_ignores_own_tx_echo():
    _register_all()
    bench = FakeModuleBench(tx_only_fingerprint=True)
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_FAIL, "只看 RX：命令回显不构成复位证据"


def test_reset_sta_skipped_when_bridge_port_unusable():
    _register_all()
    bench = FakeModuleBench(bridge_usable=False)
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_SKIPPED
    assert "不可用" in result.detail
    assert bench.send_count == 0, "口不可用就不该下发任何东西"


def test_reset_sta_skipped_when_ensure_fails():
    _register_all()
    bench = FakeModuleBench(ensure_ok=False)
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_SKIPPED
    assert "模块会话建立失败" in result.detail
    assert bench.send_count == 0


def test_reset_sta_unknown_when_logs_unreadable():
    """日志读不到 → 没法判定 → unknown（既不 pass 也不 fail）。"""
    _register_all()
    bench = FakeModuleBench(logs_broken=True)
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == "unknown"
    assert "无法判定复位" in result.detail


def test_reset_sta_unknown_when_retry_send_fails():
    _register_all()
    bench = FakeModuleBench(delays=(None, None), send_fail_attempt=2)
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == "unknown"
    assert "第 2 次下发" in result.detail


def test_reset_sta_skipped_when_first_send_fails():
    _register_all()
    bench = FakeModuleBench(send_fail_attempt=1)
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_SKIPPED
    assert "首次下发" in result.detail


def test_reset_sta_reads_fingerprints_from_block_spec_assert():
    _register_all()
    spec = BlockSpec.model_validate({
        "id": reset.BLOCK_RESET_STA, "title": "复位", "action": {"kind": "reset_sta"},
        "assert": [{"type": "boot_fingerprint", "any_of": ["MY_BOOT_LINE"]}],
    })
    bench = FakeModuleBench(delays=(1.0,), fingerprint_text="MY_BOOT_LINE boot ok")
    ctx = _reset_ctx(bench, block_specs={reset.BLOCK_RESET_STA: spec})
    result = reset.reset_sta(_reset_params(), ctx)
    assert result.state == BLOCK_PASS
    assert result.actual["fingerprint"] == "MY_BOOT_LINE"


def test_reset_sta_stop_observation_hook_is_called():
    _register_all()
    bench = FakeModuleBench(delays=(1.0,))
    called = []
    ctx = _reset_ctx(bench, extras={"stop_observations": lambda mapping_id: called.append(mapping_id)})
    result = reset.reset_sta(_reset_params(stop_observation=True), ctx)
    assert result.state == BLOCK_PASS
    assert called == ["sta-main"]
    assert "已停观察" in result.detail


def test_reset_sta_reports_missing_stop_observation_hook():
    _register_all()
    bench = FakeModuleBench(delays=(1.0,))
    result = reset.reset_sta(_reset_params(stop_observation=True), _reset_ctx(bench))
    assert result.state == BLOCK_PASS
    assert "未注入" in result.detail


def test_reset_sta_via_executor_writes_no_facts_and_counts_pass_once():
    """执行器端到端：块 pass 才算 pass；reset.sta 不 provides 任何事实。"""
    _register_all()
    bench = FakeModuleBench(delays=(1.0,))
    spec = BlockSpec.model_validate(json.loads(
        (loader.DATA_DIR / "blocks" / "reset_sta.json").read_text(encoding="utf-8")))
    ctx = RunContext(block_specs={spec.id: spec})
    ctx[reset.EXTRAS_MODULE] = ModuleCapability(
        FAKE_BASE, transport=bench.transport, env_var=NO_ENV,
        clock=bench.clock, sleeper=bench.clock.sleep,
        retry_backoff=0.0, poll_interval=0.0, poll_wait_seconds=0.0)
    # reset.sta 的 needs 是 bench.sta_online（复位走 sta-main 口发 reboot，不依赖 simcon）
    assert spec.needs == [facts_bench.FACT_STA_ONLINE]
    ctx.facts_ctx.set(facts_bench.FACT_STA_ONLINE, FactValue(state=FACT_YES, detail="fake 台架"))
    case = _case([spec.id], case_id="case.reset")
    result = execute_case(case, ctx)
    assert result.verdict == "pass"
    assert result.counts["pass"] == 1
    assert spec.provides == []


def test_reset_sta_declaration_needs_sta_online_not_simcon():
    """★ 复位走 ``sta-main`` 口发 ``reboot``，不经过 simcon：needs 不许写 simcon 在线，
    否则 simcon 离线时复位块会被前置事实挡成 skipped（实际它完全能跑）——
    但也不许改成空列表（STA 口不可用时仍然不该跑）。"""
    raw = json.loads(
        (loader.DATA_DIR / "blocks" / "reset_sta.json").read_text(encoding="utf-8"))
    assert raw["needs"] == ["bench.sta_online"], raw["needs"]
    assert "bench.simcon_online" not in raw["needs"]
    assert raw["needs"] != []          # 空列表无法表达「STA 口不可用时不跑」


def test_executor_skips_reset_when_sta_port_fact_unknown():
    """STA 口探测不到（事实 unknown）→ 复位块 skipped，绝不因为 simcon 在线就放行。"""
    _register_all()
    bench = FakeModuleBench(delays=(1.0,))
    spec = BlockSpec.model_validate(json.loads(
        (loader.DATA_DIR / "blocks" / "reset_sta.json").read_text(encoding="utf-8")))
    ctx = RunContext(block_specs={spec.id: spec},
                     facts_ctx=FactsContext(
                         probe=lambda fact_id, params: FactValue(state=FACT_UNKNOWN,
                                                                 detail="STA 口不可读")))
    ctx[reset.EXTRAS_MODULE] = ModuleCapability(
        FAKE_BASE, transport=bench.transport, env_var=NO_ENV,
        clock=bench.clock, sleeper=bench.clock.sleep,
        retry_backoff=0.0, poll_interval=0.0, poll_wait_seconds=0.0)
    result = execute_case(_case([spec.id], case_id="case.reset2"), ctx)
    assert result.blocks[0].state == BLOCK_SKIPPED
    assert "bench.sta_online=unknown" in result.blocks[0].detail
    assert bench.send_count == 0


def test_reset_sta_unknown_when_first_send_hits_frame_build_error():
    """422 = 构帧/参数配置错误 → 帧根本没发出去 → unknown（配置错误，不是设备结论）。"""
    _register_all()
    bench = FakeModuleBench(send_response=(422, {"detail": "缺少参数 protocol"}))
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == "unknown", result.detail
    assert "配置错误" in result.detail and "非设备结论" in result.detail
    assert bench.send_count == 1, "只下发一次，构帧失败不重发"


def test_reset_sta_skipped_when_first_send_gate_busy_409():
    """409 = 闸门/会话忙 → skipped（台架前置不满足，不判 pass 也不判设备失败）。"""
    _register_all()
    bench = FakeModuleBench(send_response=(409, {"detail": "会话 busy（lease 被占用）"}))
    result = reset.reset_sta(_reset_params(), _reset_ctx(bench))
    assert result.state == BLOCK_SKIPPED, result.detail
    assert "409" in result.detail and "台架前置未满足" in result.detail
    assert bench.send_count == 1, "409 不自动重发（重发就是再复位一次）"


# --------------------------------------------------------------------------- #
# 台架事实 bench.sta_online（STA 控制台口 sta-main 是否可用）
# --------------------------------------------------------------------------- #

BRIDGE_STATUS = ("GET", "/api/serial-bridge/status")


def _module(routes=None, *, mapping_id="sta-main") -> ModuleCapability:
    transport = FakeTransport(routes)
    return ModuleCapability(FAKE_BASE, transport=transport, timeout=5.0,
                            probe_timeout=5.0, retry_backoff=0.0, env_var=NO_ENV,
                            mapping_id=mapping_id)


def _bridge(usable=True) -> dict:
    return {BRIDGE_STATUS: (200, {"ports": [{
        "id": "sta-main", "windows_com": "COM18", "usable": usable,
        "errors": [] if usable else ["桥接软链不存在或目标不是字符设备"],
    }], "summary": {"total": 1}})}


def test_fact_sta_online_three_states():
    _register_all()
    probe = get_fact(facts_bench.FACT_STA_ONLINE)
    yes = probe({"capability": _module(_bridge(True))})
    no = probe({"capability": _module(_bridge(False))})
    broker_down = _module()
    broker_down.transport = FakeTransport(errors={BRIDGE_STATUS: ConnectionRefusedError("refused")})
    unknown = probe({"capability": broker_down})

    assert (yes.state, no.state, unknown.state) == (FACT_YES, FACT_NO, FACT_UNKNOWN)
    assert "sta-main" in yes.detail and "COM18" in yes.detail
    assert "不可用" in no.detail
    assert not unknown.satisfied


def test_fact_sta_online_reuses_module_port_status_judgement():
    """FT-2：事实判定只有一份 —— 直接复用 ``common.judge_sta_online`` +
    ``ModuleCapability.port_status``（不新写一份「口可用」判定）。"""
    assert facts_bench.judge_sta_online is common.judge_sta_online
    capability = _module(_bridge(False))
    value = common.judge_sta_online(capability, mapping_id="sta-main")
    assert value.state == FACT_NO and "不可用" in value.detail


def test_fact_sta_online_declaration_matches_core_models():
    from case_runner.core.models import FactSpec

    _register_all()
    path = loader.DATA_DIR / "facts" / "bench_sta_online.json"
    spec = FactSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert spec.id == facts_bench.FACT_STA_ONLINE == spec.probe
    assert has_fact(spec.probe)
    assert spec.params_schema.get("properties", {}).get("mapping_id", {}).get("default") \
        == "sta-main"


def test_facts_count_and_full_declarations_stay_consistent():
    """事实总数 8（含新增 bench.sta_online 与 nwk.nodes_online_min），且全量加载零问题。

    ``nwk.nodes_online_min``（REQS-0041 假 PASS 修复）是「真实在网/可达」事实：
    10H-F4 已抄数 + 10H-F2 信号品质两路交叉，与只判档案数的 ``nwk.onnet_count_min`` 分开。
    """
    report = loader.load_all(loader.DATA_DIR, strict=False)
    assert len(report.facts) == 8, [spec.id for spec in report.facts]
    assert "nwk.nodes_online_min" in {spec.id for spec in report.facts}
    assert report.problems == [], report.problems


# --------------------------------------------------------------------------- #
# 声明加载器（KE-1：file:reason，不静默跳过）
# --------------------------------------------------------------------------- #


def _write(directory: Path, name: str, payload) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return path


GOOD_BLOCK = {
    "id": "t.block", "title": "测试块", "needs": [], "provides": [],
    "action": {"kind": "noop"},
}


def test_load_blocks_rejects_missing_action_with_file_reason(tmp_path):
    _write(tmp_path, "blocks/bad.json",
           {"id": "bad.block", "title": "缺 action"})
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_blocks(tmp_path / "blocks")
    message = str(excinfo.value)
    assert "bad.json" in message
    assert "action" in message


def test_load_blocks_rejects_empty_action(tmp_path):
    _write(tmp_path, "blocks/empty_action.json",
           {"id": "empty.block", "title": "空 action", "action": {}})
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_blocks(tmp_path / "blocks")
    assert "action 为空" in str(excinfo.value)


def test_load_blocks_rejects_broken_json_syntax(tmp_path):
    _write(tmp_path, "blocks/broken.json", "{\"id\": \"x\",,}")
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_blocks(tmp_path / "blocks")
    assert "JSON 解析失败" in str(excinfo.value)


def test_load_blocks_rejects_duplicate_ids(tmp_path):
    _write(tmp_path, "blocks/a.json", GOOD_BLOCK)
    _write(tmp_path, "blocks/b.json", GOOD_BLOCK)
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_blocks(tmp_path / "blocks")
    message = str(excinfo.value)
    assert "id 重复" in message and "a.json" in message and "b.json" in message


def test_load_blocks_rejects_unknown_field(tmp_path):
    _write(tmp_path, "blocks/extra.json", dict(GOOD_BLOCK, unexpected_field=1))
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_blocks(tmp_path / "blocks")
    assert "unexpected_field" in str(excinfo.value)


def test_load_blocks_reports_all_files_at_once(tmp_path):
    _write(tmp_path, "blocks/a.json", {"id": "a", "title": "t"})
    _write(tmp_path, "blocks/b.json", "not json")
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_blocks(tmp_path / "blocks")
    assert len(excinfo.value.problems) == 2, "一次报全，不要修一个才看到下一个"


def test_load_facts_and_cross_reference_needs(tmp_path):
    _write(tmp_path, "facts/known.json", {
        "id": "bench.known", "title": "已知事实", "probe": "bench.known"})
    _write(tmp_path, "blocks/needs_unknown.json", dict(
        GOOD_BLOCK, id="t.block2", needs=["bench.unknown"]))
    _write(tmp_path, "blocks/ok.json", dict(GOOD_BLOCK, id="t.block3",
                                            needs=["bench.known"]))
    report = loader.load_all(tmp_path, strict=False)
    assert [spec.id for spec in report.blocks] == ["t.block2", "t.block3"]
    assert any("bench.unknown" in problem and "未声明/未注册" in problem
               for problem in report.problems)
    with pytest.raises(loader.DeclarationError):
        loader.load_all(tmp_path)


def test_load_cases_and_suites_missing_dirs_return_empty(tmp_path):
    assert loader.load_cases(tmp_path / "cases") == []
    assert loader.load_suites(tmp_path / "case_suites") == []
    assert loader.load_blocks(tmp_path / "blocks") == []
    assert loader.load_facts(tmp_path / "facts") == []


def test_load_cases_requires_steps_and_blocks(tmp_path):
    _write(tmp_path, "cases/empty.json", {"id": "c", "title": "t", "steps": [], "blocks": []})
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_cases(tmp_path / "cases")
    message = str(excinfo.value)
    assert "steps" in message and "blocks" in message


def test_load_all_accepts_case_quoting_declared_blocks(tmp_path):
    _write(tmp_path, "blocks/a.json", GOOD_BLOCK)
    _write(tmp_path, "cases/c.json", {
        "id": "c", "title": "用例",
        "steps": [{"no": 1, "do": "跑", "expect": "过"}],
        "blocks": ["t.block"],
    })
    report = loader.load_all(tmp_path)
    assert report.problems == []
    assert [case.id for case in report.cases] == ["c"]


def test_load_all_rejects_provides_of_parameterized_fact(tmp_path):
    """★ 结构上禁止再犯：``provides`` 引用**参数化事实**（有必需参数）一律报错。

    理由：块回写只按 ``fact_id`` 缓存（``FactsContext.set``），参数不同也会命中 →
    给 addr A 建档成功会让 addr B 的探测直接 yes = 跨地址假 PASS。
    """
    _write(tmp_path, "facts/addr_fact.json", {
        "id": "archive.contains", "title": "参数化事实", "probe": "archive.contains",
        "params_schema": {"addr": {"type": "string", "pattern": "^[0-9]{12}$",
                                   "required": True}},
    })
    _write(tmp_path, "blocks/add.json", dict(
        GOOD_BLOCK, id="archive.add", provides=["archive.contains"]))

    report = loader.load_all(tmp_path, strict=False)
    assert any("参数化事实" in problem and "archive.contains" in problem
               for problem in report.problems), report.problems
    with pytest.raises(loader.DeclarationError) as excinfo:
        loader.load_all(tmp_path)
    assert "参数化事实" in str(excinfo.value)


def test_load_all_rejects_provides_of_object_schema_required_param(tmp_path):
    """JSON-Schema 写法（``type/properties/required``）同样要被抓到。"""
    _write(tmp_path, "facts/f.json", {
        "id": "nwk.onnet_count_min", "title": "参数化事实",
        "probe": "nwk.onnet_count_min",
        "params_schema": {"type": "object", "required": ["min"],
                          "properties": {"min": {"type": "integer"}}},
    })
    _write(tmp_path, "blocks/b.json", dict(
        GOOD_BLOCK, id="nwk.onnet_count", provides=["nwk.onnet_count_min"]))
    report = loader.load_all(tmp_path, strict=False)
    assert any("参数化事实" in problem for problem in report.problems), report.problems


def test_load_all_allows_provides_of_fact_without_required_params(tmp_path):
    """无必需参数的事实（台架在线类，参数都有默认值）允许 provides —— 不产生跨参数歧义。"""
    _write(tmp_path, "facts/bench.json", {
        "id": "bench.simcon_online", "title": "台架事实", "probe": "bench.simcon_online",
        "params_schema": {"type": "object", "additionalProperties": True,
                          "properties": {"base_url": {"type": "string"},
                                         "mapping_id": {"type": "string",
                                                        "default": "simcon"}}},
    })
    _write(tmp_path, "blocks/fix.json", dict(
        GOOD_BLOCK, id="fix.simcon_session",
        needs=["bench.simcon_online"], provides=["bench.simcon_online"]))
    report = loader.load_all(tmp_path, strict=False)
    assert report.problems == [], report.problems


def test_load_all_accepts_my_declarations_from_package_data():
    """本批 7 个 JSON 必须能被子目录加载（并行期别的文件有问题只查我自己的）。"""
    report = loader.load_all(loader.DATA_DIR, strict=False)
    mine = {"fix.simeter_session", "fix.simcon_session", "reset.sta"}
    my_facts = {"bench.simeter_online", "bench.simcon_online", "bench.sta_online"}
    assert mine <= {spec.id for spec in report.blocks}
    assert my_facts <= {spec.id for spec in report.facts}
    mine_files = ("fix_simeter_session.json", "fix_simcon_session.json",
                  "reset_sta.json", "bench_simeter_online.json",
                  "bench_simcon_online.json", "bench_sta_online.json")
    assert not [p for p in report.problems if any(name in p for name in mine_files)], \
        report.problems
    assert not [p for p in report.problems if "参数化事实" in p], report.problems


def test_load_blocks_resolves_data_root_and_subdirectory(tmp_path):
    _write(tmp_path, "blocks/a.json", GOOD_BLOCK)
    assert [spec.id for spec in loader.load_blocks(tmp_path / "blocks")] == ["t.block"]
    assert [spec.id for spec in loader.load_blocks(tmp_path)] == ["t.block"]


def test_register_domain_modules_imports_real_domain_modules():
    report = loader.register_domain_modules(strict=False)
    assert report.ok, report.problems()
    names = {name.rsplit(".", 1)[-1] for name in report.imported}
    assert {"fix", "facts_bench", "reset"} <= names
    assert has_block(fix.BLOCK_SIMETER_SESSION)
    assert has_block(reset.BLOCK_RESET_STA)
    assert has_fact(fix.FACT_SIMETER_ONLINE)


def test_iter_domain_module_names_skips_loader_and_tests():
    names = loader.iter_domain_module_names()
    assert "loader" not in names
    assert not [name for name in names if name.startswith("test_")]


# --------------------------------------------------------------------------- #
# 包入口容错（blocks/__init__.py）
# --------------------------------------------------------------------------- #


def _make_temp_package(tmp_path: Path, name: str, modules: dict[str, str]) -> Path:
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for module_name, source in modules.items():
        filename = module_name if module_name.endswith(".py") else f"{module_name}.py"
        (package / filename).write_text(source, encoding="utf-8")
    return package


def test_blocks_package_import_is_tolerant_and_reports_failures(tmp_path, monkeypatch):
    import case_runner.blocks as blocks_pkg

    package = _make_temp_package(tmp_path, "wbtmp_blocks_ok", {
        "good.py": "REGISTERED = True\n",
        "broken.py": "raise RuntimeError('故意炸')\n",
        "test_ignored.py": "raise RuntimeError('测试文件不该被导入')\n",
    })
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(blocks_pkg, "IMPORT_FAILURES", [])
    blocks_pkg.IMPORT_FAILURES.clear()
    with pytest.warns(blocks_pkg.ModuleImportWarning):
        imported = blocks_pkg.import_domain_modules(package="wbtmp_blocks_ok", warn=True)
    assert imported == ["wbtmp_blocks_ok.good"]
    assert [name for name, _ in blocks_pkg.IMPORT_FAILURES] == ["wbtmp_blocks_ok.broken"]
    assert "test_ignored" not in " ".join(imported)
    for module_name in ("wbtmp_blocks_ok", "wbtmp_blocks_ok.good", "wbtmp_blocks_ok.broken"):
        sys.modules.pop(module_name, None)
    assert package.is_dir()


def test_blocks_package_import_empty_dir_is_ok(tmp_path, monkeypatch):
    import case_runner.blocks as blocks_pkg

    _make_temp_package(tmp_path, "wbtmp_blocks_empty", {})
    monkeypatch.syspath_prepend(str(tmp_path))
    assert blocks_pkg.import_domain_modules(package="wbtmp_blocks_empty", warn=False) == []
    sys.modules.pop("wbtmp_blocks_empty", None)


def test_blocks_package_exports_loader_lazily():
    import case_runner.blocks as blocks_pkg

    assert blocks_pkg.load_all is loader.load_all
    assert blocks_pkg.DeclarationError is loader.DeclarationError
    assert blocks_pkg.loader is loader


# --------------------------------------------------------------------------- #
# fix.simeter_session 的开口语义（REQS-0041 §10.13 台架联调修复）
#
# 真实链路：simcon(1376.2) → CCO → HPLC → STA → 485 → 模拟电表。
# 模拟电表必须在串口上「张着嘴」才会自动应答 STA 发来的 645 请求；
# 只探测 status() 而不 open_port() = 没人应答 = meter.* 必挂。
# 下面每个用例都不碰真台架：只用假 transport 回放 simeter 子应用的三个端点。
# --------------------------------------------------------------------------- #


class FakeSimeterBench:
    """假 simeter 子应用：``status`` 反映口是否真开着，``open`` 可按用例返回任意结果。

    - ``serial_open``：初始口状态（``True`` = 已经有人开过，块应沿用而不是重开）
    - ``open_response``：``POST /api/simeter/open`` 的应答 ``(status, payload)``
    - ``open_effect``：open 返回 200 时是否**真的**把口置为打开
      （现实里可能「返回 ok 但口没真开」，块必须靠复核发现）
    """

    def __init__(self, *, serial_open=False,
                 open_response=(200, {"serial": {"open": True}}),
                 open_effect=True):
        self.serial_open = serial_open
        self.open_response = open_response
        self.open_effect = open_effect
        self.calls = []

    def __call__(self, method, url, body, timeout):
        parsed = urllib.parse.urlparse(url)
        self.calls.append({"method": method.upper(), "path": parsed.path,
                           "body": dict(body or {})})
        if method.upper() == "GET" and parsed.path == "/api/simeter/status":
            serial = ({"open": True, "port": "/run/hplc/serial/simeter"}
                      if self.serial_open else None)
            return 200, {"brain": {"addr": "000000000001", "version": "645-2007"},
                         "serial": serial}
        if method.upper() == "POST" and parsed.path == SIMETER_OPEN_PATH:
            status, payload = self.open_response
            if status == 200 and self.open_effect:
                self.serial_open = True
            return status, payload
        return 404, {"detail": "fake: 未配置该路由 %s %s" % (method, parsed.path)}

    def count(self, method, path):
        return sum(1 for call in self.calls
                   if call["method"] == method.upper() and call["path"] == path)

    def open_bodies(self):
        """每次 ``POST /api/simeter/open`` 的请求体（用来断言 mapping_id 真的传下去了）。"""
        return [call["body"] for call in self.calls if call["path"] == SIMETER_OPEN_PATH]


SIMETER_OPEN_PATH = "/api/simeter/open"
SIMETER_CLOSE_PATH = "/api/simeter/close"


def _simeter_block(bench, params=None):
    """跑一次 ``fix.simeter_session``（能力实例注入假 transport，不碰真台架）。"""
    _register_all()
    ctx = RunContext()
    ctx[fix.EXTRAS_SIMETER] = SimeterCapability(
        FAKE_BASE, transport=bench, timeout=1.0, probe_timeout=1.0,
        retry_backoff=0.0, env_var=NO_ENV)
    return get_block(fix.BLOCK_SIMETER_SESSION)(dict(params or {}), ctx)


def test_simeter_session_reuses_open_port_without_any_open_call():
    """口已开 → pass，且**一次 open 都不发**（沿用已开口，不改动别人的会话）。"""
    bench = FakeSimeterBench(serial_open=True)
    result = _simeter_block(bench)
    assert result.state == BLOCK_PASS
    assert bench.count("POST", SIMETER_OPEN_PATH) == 0, "口已开就不得再发 open"
    assert "沿用已开口" in result.detail
    assert result.facts[fix.FACT_SIMETER_ONLINE].state == FACT_YES


def test_simeter_session_opens_port_when_closed():
    """口未开 + ``ensure_open=true``（默认）→ 调一次 open_port → pass。"""
    bench = FakeSimeterBench(serial_open=False)
    result = _simeter_block(bench)
    assert result.state == BLOCK_PASS
    assert bench.count("POST", SIMETER_OPEN_PATH) == 1
    assert bench.serial_open is True, "开口后工作台侧状态应变成「已打开」"
    assert result.facts[fix.FACT_SIMETER_ONLINE].state == FACT_YES


def test_simeter_session_keeps_port_open_for_following_meter_blocks():
    """块**不自动关端口**：detail 明说端口保持打开，且一次 close 都不发。"""
    bench = FakeSimeterBench(serial_open=False)
    result = _simeter_block(bench)
    assert result.state == BLOCK_PASS
    assert bench.count("POST", SIMETER_CLOSE_PATH) == 0, "块不得自动关端口"
    assert "端口保持打开" in result.detail
    assert "供后续抄表块使用" in result.detail


def test_simeter_session_ensure_open_false_fails_when_closed():
    """``ensure_open=false``：只探测不开口；严格口径下口没开就是没就绪 → fail。"""
    bench = FakeSimeterBench(serial_open=False)
    result = _simeter_block(bench, {"ensure_open": False})
    assert result.state == BLOCK_FAIL
    assert bench.count("POST", SIMETER_OPEN_PATH) == 0
    assert "bench.simeter_online=no" in result.detail
    assert result.facts[fix.FACT_SIMETER_ONLINE].state == FACT_NO


def test_simeter_session_open_conflict_409_is_skipped():
    """409（串口被占/资源冲突）→ skipped，detail 点名「端口被占用」。"""
    bench = FakeSimeterBench(open_response=(409, {"detail": "串口被其他会话持有"}))
    result = _simeter_block(bench)
    assert result.state == BLOCK_SKIPPED
    assert "占用" in result.detail
    assert result.facts[fix.FACT_SIMETER_ONLINE].state == FACT_NO


def test_simeter_session_open_server_error_500_is_unknown():
    """其他 5xx → unknown（拿不到结论，绝不折算 pass）。"""
    bench = FakeSimeterBench(open_response=(500, {"detail": "串口写超时"}))
    result = _simeter_block(bench)
    assert result.state == BLOCK_UNKNOWN
    assert "http_500" in result.detail
    assert result.state != BLOCK_PASS


def test_simeter_session_open_subapp_missing_404_is_skipped():
    """404（子应用未挂载）→ skipped：台架缺失不是设备失败。"""
    bench = FakeSimeterBench(open_response=(404, {"detail": "simeter 子应用未挂载"}))
    result = _simeter_block(bench)
    assert result.state == BLOCK_SKIPPED
    assert "404" in result.detail


def test_simeter_session_open_unreachable_is_skipped():
    """服务不可达（连不上）→ skipped，且事实是 unknown 而不是 no / yes。"""
    def boom(method, url, body, timeout):
        if method.upper() == "GET":
            raise ConnectionRefusedError("refused")
        raise AssertionError("连不上时不得发出任何开口请求")

    _register_all()
    ctx = RunContext()
    ctx[fix.EXTRAS_SIMETER] = SimeterCapability(
        FAKE_BASE, transport=boom, timeout=0.01, probe_timeout=0.01,
        retry_backoff=0.0, env_var=NO_ENV)
    result = get_block(fix.BLOCK_SIMETER_SESSION)({}, ctx)
    assert result.state == BLOCK_SKIPPED
    assert "bench.simeter_online=unknown" in result.detail


def test_simeter_session_open_ok_but_recheck_closed_fails():
    """open 返回 200 但复核 ``serial_open=false`` → fail（不能凭 open 的 ok 判 pass）。"""
    bench = FakeSimeterBench(serial_open=False, open_effect=False)
    result = _simeter_block(bench)
    assert result.state == BLOCK_FAIL
    assert "复核" in result.detail
    assert result.facts[fix.FACT_SIMETER_ONLINE].state == FACT_NO


def test_simeter_session_passes_port_param_as_open_mapping_id():
    """``port`` 参数（mapping_id）必须原样传给 open_port —— 绝不写死物理串口。"""
    bench = FakeSimeterBench(serial_open=False)
    result = _simeter_block(bench, {"port": "meter-485"})
    assert result.state == BLOCK_PASS
    assert bench.open_bodies() == [{"mapping_id": "meter-485"}]


def test_simeter_session_declaration_defaults_ensure_open_true_and_port_mapping():
    """声明口径：``ensure_open`` 默认 true、``port`` 是逻辑口别名、provides 不变。"""
    path = loader.DATA_DIR / "blocks" / "fix_simeter_session.json"
    text = path.read_text(encoding="utf-8")
    decl = json.loads(text)
    assert decl["id"] == fix.BLOCK_SIMETER_SESSION
    assert decl["provides"] == [fix.FACT_SIMETER_ONLINE]
    assert decl["action"]["params"]["ensure_open"] is True
    assert decl["action"]["params"]["port"] == "simeter"
    assert "COM" not in text, "声明里只许写 mapping_id，物理串口由工作台映射决定"


def test_simeter_session_block_params_flattened_override_declaration():
    """执行器口径：``action.params`` 是缺省，扁平键（``ctx.block_params``）覆盖它。"""
    merged = common.block_action_params({
        "kind": "probe_bench",
        "params": {"ensure_open": True, "port": "simeter"},
        "ensure_open": False,
        "port": "meter-485",
    })
    assert merged["ensure_open"] is False
    assert merged["port"] == "meter-485"


def test_flag_of_treats_string_zero_as_false():
    """``bool("false") is True`` 的坑：字符串开关必须按字面判假。"""
    assert common.flag_of("false") is False
    assert common.flag_of("0") is False
    assert common.flag_of("no") is False
    assert common.flag_of("") is False         # 空串 = 没给 → 回落到调用方给的默认值
    assert common.flag_of("", True) is True
    assert common.flag_of(None, True) is True
    assert common.flag_of("true") is True


def test_execute_case_simeter_session_opens_port_per_declaration():
    """端到端：用**真实声明**跑一次 —— 口没开时会被开口，然后 pass。"""
    _register_all()
    spec = {item.id: item for item in loader.load_blocks()}[fix.BLOCK_SIMETER_SESSION]
    bench = FakeSimeterBench(serial_open=False)
    ctx = RunContext(block_specs={spec.id: spec})
    ctx[fix.EXTRAS_SIMETER] = SimeterCapability(
        FAKE_BASE, transport=bench, timeout=1.0, probe_timeout=1.0,
        retry_backoff=0.0, env_var=NO_ENV)
    result = execute_case(_case([fix.BLOCK_SIMETER_SESSION]), ctx)
    assert result.blocks[0].state == BLOCK_PASS
    assert bench.open_bodies() == [{"mapping_id": "simeter"}]
    assert result.verdict == "pass"
