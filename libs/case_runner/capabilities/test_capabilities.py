"""能力适配器测试（REQS-0041 PLAN P2-1a / P2-1b）。

不打真台架：simcon / simeter 的 HTTP 全部走**注入的假 transport** 回放
（成功路径 / 服务不可达 / 非 2xx），不需要本机起工作台；
frames 用真实的 ``parser_lib.facade``（纯函数，无需服务），断言 02H-F1 内嵌 645
的地址域能取出来（REQS §10.7 事实 E）。

另附两条结构自检：三个适配器互不 import、且每文件只对接一个领域库。
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from case_runner.capabilities import frames as frames_mod
from case_runner.capabilities.frames import (
    NESTED_645_FRAME,
    SELF_CHECK_FRAME,
    FramesCapability,
)
from case_runner.capabilities.simeter import SimeterCapability
from case_runner.capabilities.simcon import SimconCapability

#: 假 transport 用的 base_url（端口只是假地址，不代表任何真实台架）
FAKE_BASE = "http://127.0.0.1:19300"


# --------------------------------------------------------------------------
# 假 HTTP
# --------------------------------------------------------------------------

class FakeTransport:
    """按 (method, path) 回放的假 transport；值可为 (status, payload) 或 callable。

    - ``routes``: 命中的响应；未命中的路由统一回 404（模拟「该子应用未挂载」）。
    - ``errors``: 命中的请求抛异常（``ConnectionRefusedError`` 即「服务不可达」）。
    """

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
            "method": method.upper(), "path": parsed.path, "url": url,
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


def _simcon(routes=None, errors=None, **kwargs):
    transport = FakeTransport(routes, errors)
    kwargs.setdefault("retry_backoff", 0.0)
    kwargs.setdefault("poll_interval", 0.0)
    return SimconCapability(FAKE_BASE, transport=transport, **kwargs), transport


def _simeter(routes=None, errors=None, **kwargs):
    transport = FakeTransport(routes, errors)
    kwargs.setdefault("retry_backoff", 0.0)
    return SimeterCapability(FAKE_BASE, transport=transport, **kwargs), transport


STATUS_OK = ("GET", "/api/simcon/status")
LEASE_OK = ("GET", "/api/ai/v2/simcon/lease")
BUILD_OK = ("POST", "/api/simcon/build")
FRAMES = ("GET", "/api/simcon/frames")
VERIFY = ("POST", "/api/ai/v2/verification-runs")
JOB = ("GET", "/api/ai/v2/jobs/job-1")


# --------------------------------------------------------------------------
# simcon.probe
# --------------------------------------------------------------------------

def test_simcon_probe_ok_reports_port():
    cap, transport = _simcon({STATUS_OK: (200, {"open": True, "port": "/dev/ttyS3",
                                               "pending_frames": 0}),
                              LEASE_OK: (200, {"held": False, "holder": None})})
    health = cap.probe()
    assert health.ok is True
    assert "/dev/ttyS3" in health.reason
    assert transport.count(*STATUS_OK) == 1


def test_simcon_probe_mentions_held_lease_but_stays_healthy():
    cap, _ = _simcon({STATUS_OK: (200, {"open": False, "port": None}),
                      LEASE_OK: (200, {"held": True,
                                       "holder": {"actor": "flash", "kind": "flash_job"}})})
    health = cap.probe()
    assert health.ok is True          # 忙碌不是「能力不可用」
    assert "lease" in health.reason


def test_simcon_probe_unreachable_is_bounded_and_does_not_raise():
    cap, transport = _simcon(
        errors={STATUS_OK: ConnectionRefusedError("Failed to establish connection")},
        retries=2)
    health = cap.probe()
    assert health.ok is False
    assert "不可达" in health.reason
    assert transport.count(*STATUS_OK) == 3        # 1 次 + retries=2，有界


def test_simcon_probe_404_says_not_mounted():
    cap, _ = _simcon({STATUS_OK: (404, {"detail": "Not Found"})})
    health = cap.probe()
    assert health.ok is False
    assert "404" in health.reason and "未挂载" in health.reason


def test_simcon_probe_5xx_retried_then_fails():
    cap, transport = _simcon({STATUS_OK: (503, {"detail": "服务未就绪"})}, retries=1)
    health = cap.probe()
    assert health.ok is False
    assert "503" in health.reason
    assert transport.count(*STATUS_OK) == 2        # GET 允许有限重试


def test_simcon_no_base_url_probe_and_actions(monkeypatch):
    monkeypatch.delenv("WORKBENCH_BASE", raising=False)
    transport = FakeTransport()
    cap = SimconCapability(None, transport=transport)
    health = cap.probe()
    assert health.ok is False
    assert "base_url" in health.reason
    built = cap.build(0x10, 1, {})
    assert built["ok"] is False and built["error"] == "no_base_url"
    assert transport.calls == []                   # 一个请求都不发


def test_simcon_base_url_from_env(monkeypatch):
    monkeypatch.setenv("WORKBENCH_BASE", FAKE_BASE)
    transport = FakeTransport({STATUS_OK: (200, {"open": True, "port": "/dev/ttyS3"}),
                               LEASE_OK: (200, {"held": False})})
    cap = SimconCapability(transport=transport)
    assert cap.probe().ok is True
    assert transport.calls[0]["url"].startswith(FAKE_BASE)


# --------------------------------------------------------------------------
# simcon.build
# --------------------------------------------------------------------------

def test_simcon_build_ok_normalizes_hex():
    cap, transport = _simcon({BUILD_OK: (200, {
        "hex": "68 0F 00 43 00 00 00 00 00 01 10 01 00 55 16", "length": 15})})
    result = cap.build(0x10, 1, {}, profile="anhui", seq=3)
    assert result["ok"] is True
    assert result["hex"] == "680F00430000000000011001005516"
    assert result["hex_spaced"].startswith("68 0F")
    assert result["length"] == 15
    body = transport.calls[0]["body"]
    assert body["afn"] == 0x10 and body["fn"] == 1
    assert body["profile"] == "anhui" and body["seq"] == 3


def test_simcon_build_422_is_explicit():
    cap, _ = _simcon({BUILD_OK: (422, {"detail": "构帧失败：缺少参数 duration"})})
    result = cap.build(0x11, 5, {})
    assert result["ok"] is False
    assert result["error"] == "http_422" and result["status"] == 422
    assert "duration" in result["detail"]


def test_simcon_build_missing_hex_field_is_bad_response():
    cap, _ = _simcon({BUILD_OK: (200, {"length": 0})})
    result = cap.build(0x10, 1, {})
    assert result["ok"] is False and result["error"] == "bad_response"


# --------------------------------------------------------------------------
# simcon.verify（提交 + 轮询）
# --------------------------------------------------------------------------

def _job(steps, *, state="succeeded", summary="done", error=None):
    inner = {"steps": steps, "run_id": "run-9", "frames_seq": [1, 2]}
    outer = {"result": inner}
    if error:
        outer["error"] = error
    return (200, {"job_id": "job-1", "job_state": state, "summary": summary,
                  "verdict": "pass" if state == "succeeded" else "fail",
                  "result": outer})


def test_simcon_verify_ok():
    cap, transport = _simcon({
        VERIFY: (202, {"job_id": "job-1", "job_state": "running"}),
        JOB: _job([{"name": "onnet", "result": "pass"}]),
    })
    result = cap.verify([{"name": "onnet", "send": {"afn": "10H", "fn": "F1"}}])
    assert result["ok"] is True
    assert result["job_state"] == "succeeded"
    assert result["steps_total"] == 1 and result["steps_failed"] == 0
    assert result["run_id"] == "run-9"
    assert transport.count(*VERIFY) == 1
    task = transport.calls[0]["body"]["task"]
    assert task["mapping_id"] == "simcon" and task["profile"] == "anhui"
    assert task["steps"][0]["send"]["afn"] == "10H"
    assert task["client_request_id"]


def test_simcon_verify_step_fail_is_not_ok():
    cap, _ = _simcon({
        VERIFY: (202, {"job_id": "job-1", "job_state": "running"}),
        JOB: _job([{"name": "onnet", "result": "fail"}], summary="1 步未通过"),
    })
    result = cap.verify([{"name": "onnet"}])
    assert result["ok"] is False
    assert result["error"] == "job_not_passed"
    assert result["steps_failed"] == 1
    assert "未通过" in result["detail"]


def test_simcon_verify_busy_409_not_retried():
    cap, transport = _simcon({VERIFY: (409, {"detail": "simcon 被 flash 占用"})})
    result = cap.verify([{"name": "onnet"}])
    assert result["ok"] is False
    assert result["error"] == "http_409" and result["status"] == 409
    assert "占用" in result["detail"]
    assert transport.count(*VERIFY) == 1              # POST 绝不自动重试


def test_simcon_verify_poll_timeout_without_extra_calls():
    cap, transport = _simcon({VERIFY: (202, {"job_id": "job-1", "job_state": "running"}),
                              JOB: (200, {"job_id": "job-1", "job_state": "running"})})
    result = cap.verify([{"name": "onnet"}], max_poll_seconds=0.0)
    assert result["ok"] is False
    assert result["error"] == "poll_timeout"
    assert transport.count(*JOB) == 0                 # 预算为 0 → 不浪费请求


def test_simcon_poll_job_waits_until_terminal():
    responses = iter([(200, {"job_id": "job-1", "job_state": "running"}),
                      _job([{"name": "onnet", "result": "pass"}])])
    cap, transport = _simcon({JOB: lambda *_: next(responses)}, poll_interval=0.0)
    result = cap.poll_job("job-1", max_poll_seconds=5.0)
    assert result["ok"] is True
    assert transport.count(*JOB) == 2


def test_simcon_verify_failed_job_state():
    cap, _ = _simcon({VERIFY: (202, {"job_id": "job-1", "job_state": "running"}),
                      JOB: _job([], state="failed", summary="串口未打开")})
    result = cap.verify([{"name": "onnet"}])
    assert result["ok"] is False and result["error"] == "job_failed"
    assert "串口未打开" in result["detail"]


def test_simcon_verify_empty_steps_rejected_without_http():
    cap, transport = _simcon()
    result = cap.verify([])
    assert result["ok"] is False and result["error"] == "empty_steps"
    assert transport.calls == []


def test_simcon_submit_verify_missing_job_id_is_bad_response():
    cap, _ = _simcon({VERIFY: (202, {"job_state": "running"})})
    result = cap.submit_verify([{"name": "onnet"}])
    assert result["ok"] is False and result["error"] == "bad_response"


# --------------------------------------------------------------------------
# simcon.frames
# --------------------------------------------------------------------------

def test_simcon_frames_ok_passes_query():
    cap, transport = _simcon({FRAMES: (200, {
        "session_id": "sc-1", "entries": [{"seq": 2, "dir": "rx"}],
        "next_after_seq": 2, "has_more": False,
        "counts": {"tx": 1, "rx": 1, "uplink": 1}})})
    result = cap.frames(after_seq=1, run_id="run-9", limit=50)
    assert result["ok"] is True
    assert result["count"] == 1 and result["frames"] == result["entries"]
    assert result["next_after_seq"] == 2
    query = transport.calls[0]["query"]
    assert query["after_seq"] == "1" and query["run_id"] == "run-9"
    assert query["limit"] == "50"


def test_simcon_frames_no_session_404():
    cap, transport = _simcon({FRAMES: (404, {"detail": "当前没有帧日志会话"})})
    result = cap.frames()
    assert result["ok"] is False and result["error"] == "http_404"
    assert "会话" in result["detail"]
    assert transport.count(*FRAMES) == 1


# --------------------------------------------------------------------------
# simeter
# --------------------------------------------------------------------------

SIMETER_STATUS = ("GET", "/api/simeter/status")
HANDLE_HEX = ("POST", "/api/simeter/handle_hex")
SIMETER_OPEN = ("POST", "/api/simeter/open")
SIMETER_CLOSE = ("POST", "/api/simeter/close")
SIMETER_FRAMES = ("GET", "/api/simeter/frames")


def test_simeter_probe_ok():
    cap, _ = _simeter({SIMETER_STATUS: (200, {
        "brain": {"addr": "000000000001", "version": "645-2007",
                  "rx_count": 3, "tx_count": 3},
        "serial": {"open": True, "port": "/dev/ttyS4"}, "session": {"session_id": "sm-1"}})})
    health = cap.probe()
    assert health.ok is True
    assert "/dev/ttyS4" in health.reason and "000000000001" in health.reason


def test_simeter_probe_unreachable():
    cap, _ = _simeter(errors={SIMETER_STATUS: ConnectionRefusedError("refused")})
    health = cap.probe()
    assert health.ok is False and "不可达" in health.reason


def test_simeter_probe_404_not_mounted():
    cap, _ = _simeter({SIMETER_STATUS: (404, {"detail": "Not Found"})})
    health = cap.probe()
    assert health.ok is False and "404" in health.reason


def test_simeter_handle_hex_ok_is_offline_pure_entry():
    cap, transport = _simeter({HANDLE_HEX: (200, {
        "reply_hex": "6800000000000168110433333433B316",
        "reply_wire_hex": "FE FE 68 00 00 00 00 00 00 01 68 11 04 33 33 34 33 B3 16",
        "note": {"di": "00010000"}, "action": ["读数据"]})})
    result = cap.handle_hex("68 00 00 00 00 00 00 01 68 11 04 33 33 34 33 B3 16")
    assert result["ok"] is True
    assert result["reply_hex"].startswith("68")
    assert result["action"] == ["读数据"]
    assert transport.calls[0]["body"]["hex"].startswith("68 ")


def test_simeter_handle_hex_422_is_explicit():
    cap, _ = _simeter({HANDLE_HEX: (422, {"detail": "帧 hex 非法：长度非偶数"})})
    result = cap.handle_hex("6800")
    assert result["ok"] is False
    assert result["error"] == "http_422" and result["status"] == 422
    assert "帧 hex 非法" in result["detail"]


def test_simeter_handle_hex_empty_rejected_without_http():
    cap, transport = _simeter()
    result = cap.handle_hex("   ")
    assert result["ok"] is False and result["error"] == "empty_hex"
    assert transport.calls == []


def test_simeter_handle_hex_unexpected_shape():
    cap, _ = _simeter({HANDLE_HEX: (200, {"unexpected": 1})})
    result = cap.handle_hex("6800")
    assert result["ok"] is False and result["error"] == "bad_response"


def test_simeter_open_port_defaults_to_configured_mapping():
    cap, transport = _simeter({SIMETER_OPEN: (200, {"brain": {"addr": "000000000001"},
                                                    "serial": {"open": True,
                                                               "port": "/dev/ttyS4"}})})
    result = cap.open_port()
    assert result["ok"] is True
    assert transport.calls[0]["body"] == {"mapping_id": "listener"}


def test_simeter_open_port_409_not_retried():
    cap, transport = _simeter({SIMETER_OPEN: (409, {"detail": "串口已打开：/dev/ttyS4"})})
    result = cap.open_port("listener")
    assert result["ok"] is False and result["error"] == "http_409"
    assert transport.count(*SIMETER_OPEN) == 1


def test_simeter_close_port_ok():
    cap, _ = _simeter({SIMETER_CLOSE: (200, {"brain": {"addr": "000000000001"},
                                             "serial": None})})
    result = cap.close_port()
    assert result["ok"] is True


def test_simeter_frames_ok():
    cap, transport = _simeter({SIMETER_FRAMES: (200, {
        "session": {"session_id": "sm-1"},
        "entries": [{"seq": 1, "dir": "rx"}, {"seq": 2, "dir": "tx"}]})})
    result = cap.frames(after_seq=0, direction="rx", limit=10)
    assert result["ok"] is True and result["count"] == 2
    query = transport.calls[0]["query"]
    assert query["dir"] == "rx" and query["limit"] == "10"
    assert "after_seq" not in query          # 0 表示从头，不显式传


def test_simeter_no_base_url(monkeypatch):
    monkeypatch.delenv("WORKBENCH_BASE", raising=False)
    cap = SimeterCapability(None, transport=FakeTransport())
    health = cap.probe()
    assert health.ok is False and "base_url" in health.reason
    result = cap.handle_hex("6800")
    assert result["ok"] is False and result["error"] == "no_base_url"


# --------------------------------------------------------------------------
# frames（真实 facade，纯函数，无需服务）
# --------------------------------------------------------------------------

def _field(frame, name):
    for entry in frame.get("fields") or []:
        if entry.get("name") == name:
            return entry.get("value")
    return None


def test_frames_probe_ok_with_real_facade():
    health = FramesCapability().probe()
    assert health.ok is True
    assert "facade" in health.reason


def test_frames_probe_when_facade_missing(monkeypatch):
    def _boom():
        raise ImportError("No module named 'parser_lib'")

    monkeypatch.setattr(frames_mod, "_import_facade", _boom)
    health = FramesCapability().probe()
    assert health.ok is False and "不可导入" in health.reason


def test_frames_probe_when_self_check_decode_fails():
    class BrokenFacade:
        @staticmethod
        def decode(frame, protocol=None):
            return {"ok": False, "error": "unrecognized"}

    health = FramesCapability(facade=BrokenFacade()).probe()
    assert health.ok is False and "自检解码失败" in health.reason


def test_frames_probe_when_facade_raises():
    class ExplodingFacade:
        @staticmethod
        def decode(frame, protocol=None):
            raise RuntimeError("boom")

    health = FramesCapability(facade=ExplodingFacade()).probe()
    assert health.ok is False and "抛异常" in health.reason


def test_frames_decode_645_returns_address_domain():
    result = FramesCapability().decode(SELF_CHECK_FRAME)
    assert result["ok"] is True and result["protocol"] == "645"
    assert _field(result["frame"], "地址域") == "000000000001"


def test_frames_decode_nested_645_from_02h_f1():
    """REQS §10.7 事实 E：02H-F1 上行帧内嵌 645，能取到地址域。"""
    cap = FramesCapability()
    result = cap.decode(NESTED_645_FRAME)
    assert result["ok"] is True and result["protocol"] == "1376.2"
    nested = result["frame"]["nested"]
    assert nested and nested[0]["structure"] == "645"
    assert _field(nested[0], "地址域") == "000000000001"

    addr = cap.meter_addr(NESTED_645_FRAME)
    assert addr["ok"] is True
    assert addr["addr"] == "000000000001" and addr["structure"] == "645"


def test_frames_meter_addr_without_nested_is_explicit():
    result = FramesCapability().meter_addr(SELF_CHECK_FRAME)
    assert result["ok"] is False and result["error"] == "no_nested"


def test_frames_verify_expect_match_and_mismatch():
    cap = FramesCapability()
    matched = cap.verify(SELF_CHECK_FRAME, expect={"地址域": "000000000001"})
    assert matched["ok"] is True and matched["verified"] is True
    mismatched = cap.verify(SELF_CHECK_FRAME, expect={"地址域": "000000000002"})
    assert mismatched["ok"] is True and mismatched["verified"] is False
    assert mismatched["diff"]["mismatched"][0]["name"] == "地址域"


def test_frames_build_645_and_unsupported_protocol():
    cap = FramesCapability()
    built = cap.build("645", {"addr": "000000000001", "control": "11",
                              "di": "00010000"})
    assert built["ok"] is True and built["frame_hex"] == SELF_CHECK_FRAME
    bad = cap.build("gw", {})
    assert bad["ok"] is False and bad["error"] == "unsupported_protocol"


def test_frames_decode_garbage_does_not_raise():
    result = FramesCapability().decode("zz")
    assert result["ok"] is False and result["error"] == "invalid_hex"


def test_frames_injected_facade_errors_are_wrapped():
    class ExplodingFacade:
        @staticmethod
        def decode(frame, protocol=None):
            raise RuntimeError("boom")

        @staticmethod
        def build(protocol, params):
            raise RuntimeError("boom")

        @staticmethod
        def verify(frame, protocol=None, expect=None):
            raise RuntimeError("boom")

    cap = FramesCapability(facade=ExplodingFacade())
    for result in (cap.decode(SELF_CHECK_FRAME), cap.build("645", {}),
                   cap.verify(SELF_CHECK_FRAME)):
        assert result["ok"] is False and result["error"] == "facade_error"


# --------------------------------------------------------------------------
# 结构自检：能力层互不 import、每文件只对接一个领域库、无 apps 依赖
# --------------------------------------------------------------------------

_ROOT = pathlib.Path(__file__).resolve().parent
_DOMAIN_LIBS = ("parser_lib", "sim_concentrator", "sim_meter", "loghooks",
                "minute_assert", "apps")


# --------------------------------------------------------------------------
# 真实 urllib transport 冒烟：起一个本机临时 HTTP 服务（不是台架，不连任何外部服务）
# --------------------------------------------------------------------------

_LOCAL_ROUTES = {
    ("GET", "/api/simcon/status"): (200, {"open": True, "port": "/dev/ttyS9"}),
    ("GET", "/api/ai/v2/simcon/lease"): (200, {"held": False, "holder": None}),
    ("POST", "/api/simcon/build"): (200, {"hex": "68 0F 00 43", "length": 4}),
    ("GET", "/api/simeter/status"): (503, {"detail": "simeter 未就绪"}),
    ("POST", "/api/simeter/handle_hex"): (200, {"reply_hex": "6816", "note": {},
                                                "action": None}),
}


@pytest.fixture()
def local_http():
    """起一个只按路由表回 JSON 的本机服务，验证默认 transport 的真实 HTTP 路径。"""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _dispatch(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            route = _LOCAL_ROUTES.get((self.command, urllib.parse.urlparse(self.path).path))
            self._reply(*route) if route else self._reply(404, {"detail": "Not Found"})

        do_GET = _dispatch
        do_POST = _dispatch

        def log_message(self, *args):  # 静音
            return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError as exc:  # 本机不允许监听端口 → 跳过，不算失败
        pytest.skip("本机无法监听回环端口：%s" % exc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_simcon_default_transport_real_http(local_http):
    cap = SimconCapability(local_http, retries=0)
    assert cap.probe().ok is True
    built = cap.build(0x10, 1, {})
    assert built["ok"] is True
    assert built["hex"] == "680F0043" and built["length"] == 4


def test_simeter_default_transport_real_http(local_http):
    cap = SimeterCapability(local_http, retries=0)
    health = cap.probe()                       # 本机服务对 simeter/status 回 503
    assert health.ok is False and "503" in health.reason
    handled = cap.handle_hex("6816")           # 非 2xx/2xx 两条真实路径都走一遍
    assert handled["ok"] is True and handled["reply_hex"] == "6816"


def _imported_modules(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("name,allowed_domain", [
    ("simcon.py", set()),
    ("simeter.py", set()),
    ("frames.py", {"parser_lib"}),
])
def test_adapter_imports_are_isolated(name, allowed_domain):
    modules = _imported_modules(_ROOT / name)
    # 1) 适配器之间互不 import
    assert not [m for m in modules
                if m == "case_runner.capabilities"
                or m.startswith("case_runner.capabilities.")], modules
    # 2) 每个适配器只对接一个领域库（simcon/simeter 只走 HTTP）
    used = {m.split(".")[0] for m in modules} & set(_DOMAIN_LIBS)
    assert used <= allowed_domain, "非法领域库依赖：%s" % sorted(used - allowed_domain)

# --------------------------------------------------------------------------
# Health 契约：core/models.py 落地后以 core 为准；未落地时用本地同名 fallback
# --------------------------------------------------------------------------

def test_adapters_share_core_health_when_core_landed():
    core_models = pytest.importorskip(
        "case_runner.core.models", reason="core/models.py 尚未落地（并发开发期）")
    from case_runner.capabilities.frames import Health as frames_health
    from case_runner.capabilities.simeter import Health as simeter_health
    from case_runner.capabilities.simcon import Health as simcon_health

    assert simcon_health is core_models.Health
    assert simeter_health is core_models.Health
    assert frames_health is core_models.Health


def test_adapters_fall_back_when_core_models_missing(monkeypatch):
    """并发兜底分支：core/models.py 不可用时，模块仍可加载并给出同名 Health。"""
    import importlib.util

    monkeypatch.setitem(sys.modules, "case_runner.core.models", None)  # 令 import 失败
    spec = importlib.util.spec_from_file_location(
        "_fallback_probe_frames", _ROOT / "frames.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "_fallback_probe_frames", module)
    spec.loader.exec_module(module)          # 不应抛 ImportError

    health = module.Health(ok=False, reason="core 未落地")
    assert health.ok is False and health.reason == "core 未落地"
    assert module.FramesCapability().probe().ok is True   # 兜底期也能正常工作


def test_adapters_satisfy_core_capability_protocol():
    core_models = pytest.importorskip(
        "case_runner.core.models", reason="core/models.py 尚未落地（并发开发期）")
    caps = [SimconCapability(FAKE_BASE, transport=FakeTransport()),
            SimeterCapability(FAKE_BASE, transport=FakeTransport()),
            FramesCapability()]
    for cap in caps:
        assert isinstance(cap, core_models.Capability), type(cap).__name__
        assert isinstance(cap.probe(), core_models.Health)


# --------------------------------------------------------------------------
# frames：整数协议码（1376.2「通信协议类型」字节）归一
# 块层按协议字节传 protocol=2/3/0，frames 必须先归一，不能原样甩给门面
# --------------------------------------------------------------------------

def test_frames_protocol_code_2_equals_name_645():
    cap = FramesCapability()
    by_name = cap.decode(SELF_CHECK_FRAME, protocol="645")
    by_code = cap.decode(SELF_CHECK_FRAME, protocol=2)
    assert by_code["ok"] is True, by_code
    assert by_code["protocol"] == by_name["protocol"] == "645"
    assert by_code["mode"] == by_name["mode"] == "specified"
    assert by_code["frame"] == by_name["frame"]
    assert "protocol_code" not in by_name      # 名字入参行为不变（不新增字段）
    assert by_code["protocol_code"] == 2


def test_frames_protocol_code_1997_and_digit_string_are_normalized():
    cap = FramesCapability()
    assert cap.decode(SELF_CHECK_FRAME, protocol=1)["protocol"] == "645"
    assert cap.decode(SELF_CHECK_FRAME, protocol="2")["protocol"] == "645"
    assert cap.decode(SELF_CHECK_FRAME, protocol=" 2 ")["protocol_code"] == 2
    # 未知码：显式报错，绝不悄悄当成自动嗅探
    bad = cap.decode(SELF_CHECK_FRAME, protocol=9)
    assert bad["ok"] is False and bad["error"] == "unsupported_protocol"
    assert bad["protocol_code"] == 9


def test_frames_protocol_code_0_sniffs_without_specifying():
    """0 = 透明传输：不做协议指定（门面自动嗅探），返回里保留 protocol_code=0。"""
    cap = FramesCapability()
    result = cap.decode(NESTED_645_FRAME, protocol=0)
    assert result["ok"] is True, result
    assert result["mode"] == "auto"
    assert result["protocol"] == "1376.2"
    assert result["protocol_code"] == 0


def test_frames_protocol_code_3_uses_698():
    cap = FramesCapability()
    built = cap.build("698.45", {"addr": "05353781090030", "oad": "40000200"})
    assert built["ok"] is True
    by_code = cap.decode(built["frame_hex"], protocol=3)
    assert by_code["ok"] is True, by_code
    assert by_code["protocol"] == "698.45" and by_code["protocol_code"] == 3
    by_name = cap.decode(built["frame_hex"], protocol="698.45")
    assert by_code["frame"] == by_name["frame"]


def test_frames_meter_addr_accepts_protocol_code_2():
    """实测 02H-F1 帧：块按协议字节传 protocol=2 也要取到内嵌 645 地址域。"""
    cap = FramesCapability()
    addr = cap.meter_addr(NESTED_645_FRAME, protocol=2)
    assert addr["ok"] is True, addr
    assert addr["addr"] == "000000000001"
    assert addr["structure"] == "645"
    assert addr["protocol_code"] == 2
    # 名字入参老行为不变
    by_name = cap.meter_addr(NESTED_645_FRAME)
    assert by_name["addr"] == "000000000001" and "protocol_code" not in by_name


def test_frames_nested_protocol_code_mismatch_is_explicit():
    """码说内层是 698，实测帧内层是 645 → 明确报错，绝不猜一个地址。"""
    cap = FramesCapability()
    result = cap.meter_addr(NESTED_645_FRAME, protocol=3)
    assert result["ok"] is False
    assert result["error"] == "nested_protocol_mismatch"
    assert result["protocol_code"] == 3


def test_frames_verify_accepts_protocol_code():
    cap = FramesCapability()
    by_code = cap.verify(SELF_CHECK_FRAME, protocol=2, expect={"地址域": "000000000001"})
    assert by_code["ok"] is True and by_code["verified"] is True
    assert by_code["protocol_code"] == 2


def test_frames_digit_protocol_names_are_still_names():
    """纯数字名字（"645"/"69845"）不得被误读成整数协议码——老行为必须不变。"""
    cap = FramesCapability()
    by_name = cap.decode(SELF_CHECK_FRAME, protocol="645")
    assert by_name["ok"] is True and by_name["protocol"] == "645"
    assert "protocol_code" not in by_name
    built = cap.build("698.45", {"addr": "05353781090030", "oad": "40000200"})
    assert cap.decode(built["frame_hex"], protocol="69845")["protocol"] == "698.45"
