"""``capabilities/module.py`` 单测（全部注入假 HTTP，不碰真台架）。

覆盖：``probe`` 三态（可达 / 404 子应用未挂载 / 服务未启用 / 连不上）、``ensure`` 从 job
信封取 ``session_id``、``send`` 提取实发字节数并透传 ``append_newline``、``409`` 闸门不重试、
``logs`` 的 ``after_epoch`` / RX 过滤 / 落盘兜底、``poll_timeout`` 不等于失败、
以及「动作方法一律不抛异常」这条契约。
"""

from __future__ import annotations

import ast
import pathlib
import urllib.parse

import pytest

from case_runner.capabilities.module import (
    ModuleCapability,
    parse_log_line,
    parse_log_timestamp,
)

FAKE_BASE = "http://127.0.0.1:19301"
#: 不接受宿主机 WORKBENCH_BASE 干扰（测试里显式注入 base_url，未注入的走这个空变量名）
NO_ENV = "__CASE_RUNNER_TEST_NO_BASE_URL__"


# --------------------------------------------------------------------------- #
# 假 HTTP / 假时钟
# --------------------------------------------------------------------------- #


class FakeTransport:
    """按 ``(METHOD, path)`` 回放的假 transport；未命中回 ``default``。"""

    def __init__(self, routes=None, errors=None,
                 default=(404, {"detail": "fake: 未配置该路由"})):
        self.routes = dict(routes or {})
        self.errors = dict(errors or {})
        self.default = default
        self.calls = []

    def __call__(self, method, url, body, timeout):
        parsed = urllib.parse.urlparse(url)
        key = (method.upper(), parsed.path)
        self.calls.append({
            "method": method.upper(), "path": parsed.path,
            "query": dict(urllib.parse.parse_qsl(parsed.query)),
            "body": body, "timeout": timeout,
        })
        if key in self.errors:
            raise self.errors[key]
        response = self.routes.get(key, self.default)
        if callable(response):
            response = response(method, url, body)
        return response

    def count(self, method, path):
        return sum(1 for call in self.calls
                   if call["method"] == method.upper() and call["path"] == path)

    def bodies(self, method, path):
        return [call["body"] for call in self.calls
                if call["method"] == method.upper() and call["path"] == path]


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(float(seconds), 0.0)


def make_module(routes=None, errors=None, *, clock=None, **kwargs):
    transport = FakeTransport(routes, errors)
    kwargs.setdefault("retry_backoff", 0.0)
    kwargs.setdefault("poll_interval", 0.0)
    kwargs.setdefault("poll_wait_seconds", 0.0)
    kwargs.setdefault("timeout", 5.0)
    clock = clock or FakeClock()
    capability = ModuleCapability(FAKE_BASE, transport=transport,
                                  clock=clock, sleeper=clock.sleep,
                                  env_var=NO_ENV, **kwargs)
    return capability, transport, clock


PORTS = ("GET", "/api/module-serial/ports")
SESSIONS = ("GET", "/api/module-serial/sessions")
MODULE_ACTIONS = ("POST", "/api/ai/v2/module-actions")
BRIDGE = ("GET", "/api/serial-bridge/status")


def _job_route(result, state="succeeded", summary=""):
    return (200, {"job_id": "job-x", "job_state": state, "summary": summary,
                  "result": result})


def _logs_path(session_id="ms-1"):
    return ("GET", "/api/module-serial/sessions/%s/logs" % session_id)


def _entry(seq, text, epoch, direction="RX"):
    return {"seq": seq, "ts": "20261119-13:43:46:750", "dir": direction,
            "text": text, "epoch": epoch}


# --------------------------------------------------------------------------- #
# probe / 只读快照
# --------------------------------------------------------------------------- #


def test_probe_without_base_url_is_not_ok_and_does_not_raise():
    capability = ModuleCapability(None, env_var=NO_ENV)
    health = capability.probe()
    assert health.ok is False
    assert "base_url" in health.reason


def test_probe_ok_when_module_serial_service_reachable():
    capability, transport, _ = make_module({
        PORTS: (200, {"ports": ["/run/hplc/serial/sta-main"], "mapping_error": None}),
    })
    health = capability.probe()
    assert health.ok is True
    assert "module-serial 可达" in health.reason
    assert transport.count(*PORTS) == 1


def test_probe_reports_404_as_subapp_missing():
    capability, _, _ = make_module()          # 默认路由全 404
    health = capability.probe()
    assert health.ok is False
    assert "未挂载" in health.reason


def test_probe_reports_503_as_service_disabled():
    capability, _, _ = make_module({PORTS: (503, {"detail": "模块串口服务未启用"})})
    health = capability.probe()
    assert health.ok is False
    assert "未启用" in health.reason


def test_probe_never_raises_on_transport_explosion():
    capability, _, _ = make_module(errors={PORTS: RuntimeError("boom")})
    health = capability.probe()
    assert health.ok is False
    assert "transport_error" in health.reason


def test_bridge_port_status_reports_usable_and_errors():
    capability, _, _ = make_module({
        BRIDGE: (200, {"ports": [
            {"id": "sta-main", "windows_com": "COM18", "usable": True, "errors": []},
            {"id": "simcon", "windows_com": "COM4", "usable": False,
             "errors": ["桥接软链不存在或目标不是字符设备"]},
        ], "summary": {"total": 2}}),
    })
    ok = capability.port_status("sta-main")
    assert ok["ok"] is True and ok["usable"] is True and ok["windows_com"] == "COM18"
    bad = capability.port_status("simcon")
    assert bad["usable"] is False
    assert "桥接软链" in bad["raw"]["errors"][0]
    missing = capability.port_status("nope")
    assert missing["usable"] is False and "没有该 mapping_id" in missing["detail"]


# --------------------------------------------------------------------------- #
# ensure / send / stop
# --------------------------------------------------------------------------- #


def test_ensure_extracts_session_id_from_job_envelope():
    capability, transport, _ = make_module({
        MODULE_ACTIONS: (202, {"job_id": "job-ensure", "job_state": "queued"}),
        ("GET", "/api/ai/v2/jobs/job-ensure"): _job_route(
            {"action": "ensure", "owned": True, "session_id": "ms-42",
             "mapping_id": "sta-main", "state": "running"}),
    })
    ensured = capability.ensure("sta-main", module="sta")
    assert ensured["ok"] is True
    assert ensured["session_id"] == "ms-42"
    assert ensured["owned"] is True
    assert transport.bodies(*MODULE_ACTIONS)[0] == {
        "action": "ensure", "mapping_id": "sta-main", "module": "sta"}


def test_ensure_409_is_busy_and_not_retried():
    """409 = 闸门/会话忙，不排队；POST 绝不自动重试（重发=重复下发）。"""
    capability, transport, _ = make_module({
        MODULE_ACTIONS: (409, {"detail": "会话仍有活动观察任务，不能停止"}),
    })
    ensured = capability.ensure("sta-main")
    assert ensured["ok"] is False
    assert ensured["error"] == "http_409"
    assert ensured["busy"] is True
    assert transport.count(*MODULE_ACTIONS) == 1


def test_send_reports_sent_bytes_and_append_newline():
    capability, transport, _ = make_module({
        MODULE_ACTIONS: (202, {"job_id": "job-send", "job_state": "queued"}),
        ("GET", "/api/ai/v2/jobs/job-send"): _job_route(
            {"underlying_state": "succeeded",
             "result": {"sent": 8, "channel": "sta", "append_newline": True}}),
    })
    sent = capability.send("ms-42", "reboot")
    assert sent["ok"] is True and sent["sent_bytes"] == 8
    assert transport.bodies(*MODULE_ACTIONS)[0] == {
        "action": "send", "session_id": "ms-42", "text": "reboot",
        "append_newline": True}


def test_send_rejects_empty_or_ambiguous_payload_without_http_call():
    capability, transport, _ = make_module()
    assert capability.send("ms-42")["error"] == "bad_response"
    both = capability.send("ms-42", "reboot", data_hex="7265626f6f74")
    assert both["error"] == "bad_response"
    no_session = capability.send("", "reboot")
    assert no_session["error"] == "bad_response"
    assert transport.count(*MODULE_ACTIONS) == 0


def test_stop_passes_force_flag():
    capability, transport, _ = make_module({
        MODULE_ACTIONS: (202, {"job_id": "job-stop", "job_state": "queued"}),
        ("GET", "/api/ai/v2/jobs/job-stop"): _job_route({"underlying_state": "idle"}),
    })
    stopped = capability.stop("ms-42", force=True)
    assert stopped["ok"] is True
    assert transport.bodies(*MODULE_ACTIONS)[0] == {
        "action": "stop", "session_id": "ms-42", "force": True}


def test_action_methods_never_raise_on_transport_explosion():
    for error in (RuntimeError("boom"), ConnectionRefusedError("refused")):
        capability, _, _ = make_module(errors={MODULE_ACTIONS: error})
        result = capability.send("ms-42", "reboot")
        assert result["ok"] is False
        assert result["error"] in {"transport_error", "unreachable"}


def test_failed_job_becomes_error_not_success():
    capability, _, _ = make_module({
        MODULE_ACTIONS: (202, {"job_id": "job-bad", "job_state": "queued"}),
        ("GET", "/api/ai/v2/jobs/job-bad"): (200, {
            "job_id": "job-bad", "job_state": "failed",
            "summary": "串口未运行", "result": None}),
    })
    result = capability.send("ms-42", "reboot")
    assert result["ok"] is False
    assert result["error"] == "job_failed"
    assert "串口未运行" in result["detail"]


def test_poll_timeout_is_reported_as_timeout_not_failure():
    """``poll_timeout`` 只是本地轮询预算用尽 —— 不是设备 FAIL。"""
    clock = FakeClock()
    capability, transport, _ = make_module({
        MODULE_ACTIONS: (202, {"job_id": "job-slow", "job_state": "queued"}),
        ("GET", "/api/ai/v2/jobs/job-slow"): (200, {
            "job_id": "job-slow", "job_state": "running", "result": None}),
    }, clock=clock)
    result = capability.poll_job("job-slow", timeout_s=3.0, interval=1.0)
    assert result["ok"] is False
    assert result["error"] == "poll_timeout"
    assert result["job_state"] == "running"
    assert transport.count("GET", "/api/ai/v2/jobs/job-slow") >= 2


# --------------------------------------------------------------------------- #
# logs（只读）
# --------------------------------------------------------------------------- #


def test_logs_filters_by_epoch_and_rx_only():
    entries = [
        _entry(1, "旧启动横幅 Unicorn Bootloader", 900.0),      # t0 之前 → 丢弃
        _entry(2, "reboot", 1000.5, direction="TX"),             # 自己发的 → 不算证据
        _entry(3, "reset reason: 0x1", 1002.0),
        {"seq": 4, "ts": "bad-ts", "text": "no timestamp", "dir": "RX"},  # 无法证明新 → 丢弃
    ]
    capability, _, _ = make_module({
        SESSIONS: (200, {"sessions": [{"session_id": "ms-1",
                                       "port_identity": {"mapping_id": "sta-main"}}]}),
        _logs_path(): (200, {"lines": entries}),
    })
    logs = capability.logs("sta-main", after_epoch=1000.0)
    assert logs["ok"] is True
    assert logs["source"] == "http"
    assert logs["lines"] == ["reset reason: 0x1"]
    assert logs["dropped"] == 1
    assert logs["session_id"] == "ms-1"


def test_logs_falls_back_to_session_log_file(tmp_path):
    log_file = tmp_path / "sta.log"
    log_file.write_text(
        "[20261119-13:43:46:000] [RX] old banner Unicorn Bootloader\n"
        "[20261119-13:43:47:000] [RX] Run image0, wait a moment, please!\n"
        "[20261119-13:43:47:100] [TX] reboot\n",
        encoding="utf-8",
    )
    capability, transport, _ = make_module({
        SESSIONS: (200, {"sessions": [{"session_id": "ms-1",
                                       "port_identity": {"mapping_id": "sta-main"},
                                       "log_file": str(log_file)}]}),
        _logs_path(): (500, {"detail": "内存日志不可用"}),
    })
    logs = capability.logs("sta-main")
    assert logs["ok"] is True and logs["source"] == "file"
    # 只留 RX（``[TX] reboot`` 是我们自己发的，不构成设备端证据）
    assert logs["lines"] == [
        "old banner Unicorn Bootloader",
        "Run image0, wait a moment, please!",
    ]
    # 落盘路径同样支持 after_epoch（用行内时间戳换算出 epoch）
    cutoff = parse_log_timestamp("20261119-13:43:46:500")
    filtered = capability.logs("sta-main", after_epoch=cutoff)
    assert filtered["lines"] == ["Run image0, wait a moment, please!"]
    assert transport.count(*SESSIONS) == 2


def test_logs_returns_no_session_error_when_port_has_no_session():
    capability, _, _ = make_module({SESSIONS: (200, {"sessions": []})})
    logs = capability.logs("sta-main")
    assert logs["ok"] is False
    assert logs["error"] == "no_session"


def test_logs_unreadable_is_error_not_empty_success(tmp_path):
    """日志读不到必须报错：空的成功会变成「没看到指纹 → fail」的假结论。"""
    capability, _, _ = make_module({
        SESSIONS: (200, {"sessions": [{"session_id": "ms-1",
                                       "port_identity": {"mapping_id": "sta-main"},
                                       "log_file": str(tmp_path / "missing.log")}]}),
        _logs_path(): (500, {"detail": "内存日志不可用"}),
    })
    logs = capability.logs("sta-main")
    assert logs["ok"] is False
    assert "不可读" in logs["detail"]


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #


def test_parse_log_line_and_timestamp():
    parsed = parse_log_line("[20261119-13:43:46:750] [RX] hello")
    assert parsed["dir"] == "RX" and parsed["text"] == "hello"
    assert parsed["epoch"] == parse_log_timestamp("20261119-13:43:46:750")
    assert parse_log_line("no-bracket-line") is None
    assert parse_log_timestamp("2026-11-19T13:43:46") is None
    assert parse_log_timestamp(None) is None


def test_module_adapter_is_isolated_and_uses_core_health():
    """结构自检：不与兄弟适配器互 import、不碰领域库、Health 用 core 契约（铁律 2）。"""
    source = (pathlib.Path(__file__).resolve().parent / "module.py").read_text(encoding="utf-8")
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    assert not [m for m in modules if m.startswith("case_runner.capabilities")], modules
    domain = {"parser_lib", "sim_concentrator", "sim_meter", "loghooks",
              "minute_assert", "apps", "workbench"}
    assert not ({m.split(".")[0] for m in modules} & domain), modules
    from case_runner.capabilities.module import Health as module_health
    from case_runner.core.models import Health as core_health

    assert module_health is core_health
    capability = ModuleCapability(FAKE_BASE, env_var=NO_ENV)
    assert isinstance(capability.probe(), core_health)


def test_probe_hits_ports_endpoint_with_injected_transport(monkeypatch):
    """不注入 transport 时走 urllib；这里替换实例上的 transport 验证请求形状。"""
    capability = ModuleCapability(FAKE_BASE, env_var=NO_ENV)
    called = {}

    def fake_transport(method, url, body, timeout):
        called["method"] = method
        called["url"] = url
        return 200, {"ports": []}

    monkeypatch.setattr(capability, "transport", fake_transport)
    assert capability.probe().ok is True
    assert called["method"] == "GET"
    assert called["url"] == FAKE_BASE + "/api/module-serial/ports"
