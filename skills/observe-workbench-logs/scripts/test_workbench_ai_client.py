"""P2 observe-workbench-logs client RED/GREEN tests (mock transport, no real HTTP).

行为契约锚点，对应 docs/03-骨架设计.md §6.3 与 docs/04-任务安排.md P2。
测试只替换 HTTP 传输层（monkeypatch），不连接真实 workbench 服务；
不设置 WORKBENCH_AI_TOKEN 环境变量（避免任何真实凭据进入测试）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# 让测试可 import 待实现的 workbench_ai_client
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 本测试绝不使用真实 Token；显示断言该环境变量未被污染
assert not os.environ.get("WORKBENCH_AI_TOKEN"), (
    "test must not run with a real WORKBENCH_AI_TOKEN in environment"
)


@pytest.fixture
def fake_transport(monkeypatch):
    """替换 transport 层：记录发出的请求，返回可编程响应。

    待实现客户端应通过一个可注入的 transport 函数发 HTTP；
    这里 monkeypatch 该函数，使测试零真实网络。
    """
    calls = []
    responses = {}

    def _fake_request(method, url, *, headers=None, body=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers, "body": body, "timeout": timeout})
        key = (method, url)
        if key in responses:
            return responses[key]
        if "default" in responses:
            return responses["default"]
        raise AssertionError(f"Unexpected request {method} {url}")

    monkeypatch.setattr(
        "workbench_ai_client.transport_request",
        _fake_request,
        raising=False,
    )
    return FakeTransport(calls, responses)


class FakeTransport:
    """给测试设置响应 / 读取调用的辅助类。契约与真实 transport_request 一致：
    返回 {"status": int, "body": dict}。
    """

    def __init__(self, calls, responses):
        self.calls = calls
        self.responses = responses

    def respond(self, method, url, payload, status=200):
        self.responses[(method, url)] = {
            "status": status,
            "body": payload,
        }

    def respond_default(self, payload, status=200):
        self.responses["default"] = {
            "status": status,
            "body": payload,
        }


# ---------------------------------------------------------------------------
# CLI 帮助与命令路由
# ---------------------------------------------------------------------------

def test_cli_help_lists_six_commands(fake_transport, capsys):
    with pytest.raises(SystemExit) as exc:
        from workbench_ai_client import main

        main(["--help"])
    out = capsys.readouterr().out + capsys.readouterr().err
    for cmd in ("status", "observe", "wait", "artifact", "listener-schema", "frame-detail"):
        assert cmd in out
    # 六命令之外的危险命令不得出现在 help
    for danger in ("ensure", "start", "stop", "send", "flash", "burn", "cancel"):
        assert danger not in out
    assert exc.value.code == 0


def test_unknown_command_fails_nonzero(fake_transport, capsys):
    from workbench_ai_client import main

    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code != 0
    captured = capsys.readouterr().err
    assert "frobnicate" in captured


# ---------------------------------------------------------------------------
# 默认 dry-run：零 HTTP、只输出脱敏计划
# ---------------------------------------------------------------------------

def test_dry_run_status_emits_no_http_and_prints_plan(fake_transport, capsys, monkeypatch):
    """默认（无 --execute）时 status 只打印计划，不发出任何请求。"""
    monkeypatch.delenv("WORKBENCH_AI_TOKEN", raising=False)
    from workbench_ai_client import main

    main(["status"])
    assert fake_transport.calls == []  # 零 HTTP
    out = capsys.readouterr().out + capsys.readouterr().err
    # dry-run 输出规范化计划：不访问工作台，execute 标记为 false
    plan = json.loads(out)
    assert plan["action"] == "status"
    assert plan["execute"] is False


def test_dry_run_observe_requires_no_token(fake_transport, capsys, monkeypatch):
    """observe 默认 dry-run：无 Token 也能成功输出脱敏计划，零 HTTP。"""
    monkeypatch.delenv("WORKBENCH_AI_TOKEN", raising=False)
    from workbench_ai_client import main

    main([
        "observe",
        "--source", "module_log",
        "--session-id", "ms-1234",
        "--kind", "literal",
        "--value", "central beacon",
    ])
    assert fake_transport.calls == []
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "ms-1234" in out
    assert "central beacon" in out


# ---------------------------------------------------------------------------
# 仅 --execute 才允许 observe POST
# ---------------------------------------------------------------------------

def test_execute_observe_posts_only_with_token(fake_transport, monkeypatch):
    """observe --execute 且存在 Token 时才 POST /observations。"""
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-test")
    fake_transport.respond("POST", "http://127.0.0.1:8790/api/ai/v1/observations", {
        "operation_id": "op-log-test", "state": "created",
    }, status=202)
    from workbench_ai_client import main

    main([
        "observe",
        "--source", "module_log",
        "--session-id", "ms-1234",
        "--kind", "literal",
        "--value", "central beacon",
        "--execute",
    ])
    assert len(fake_transport.calls) == 1
    call = fake_transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://127.0.0.1:8790/api/ai/v1/observations"
    body = json.loads(call["body"])
    assert body["source"] == "module_log"
    assert body["target"]["session_id"] == "ms-1234"
    assert body["match"]["kind"] == "literal"


def test_execute_observe_without_token_fails_safely(fake_transport, monkeypatch, capsys):
    """--execute 但缺 Token：安全失败，非零退出码，不发请求。"""
    monkeypatch.delenv("WORKBENCH_AI_TOKEN", raising=False)
    from workbench_ai_client import main

    rc = main([
        "observe",
        "--source", "module_log",
        "--session-id", "ms-1234",
        "--kind", "literal", "--value", "x",
        "--execute",
    ])
    assert rc != 0
    assert fake_transport.calls == []
    assert "WORKBENCH_AI_TOKEN" in (capsys.readouterr().err)


# ---------------------------------------------------------------------------
# Token 边界：唯一来源、不出现明文、userinfo 拒绝
# ---------------------------------------------------------------------------

def test_token_never_appears_in_output(fake_transport, monkeypatch, capsys):
    """即使 execute 成功，Token 也不得出现在 stdout/stderr/异常文本。"""
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-super-secret")
    fake_transport.respond("POST", "http://127.0.0.1:8790/api/ai/v1/observations", {
        "operation_id": "op-x", "state": "created",
    }, status=202)
    from workbench_ai_client import main

    main([
        "observe",
        "--source", "module_log", "--session-id", "ms-1",
        "--kind", "literal", "--value", "hit", "--execute",
    ])
    captured = capsys.readouterr().out + capsys.readouterr().err
    assert "tok-super-secret" not in captured
    # 请求头中的 Authorization 也不得被回显到输出
    for call in fake_transport.calls:
        auth = (call.get("headers") or {}).get("Authorization", "")
        assert auth == "Bearer tok-super-secret"
        assert "tok-super-secret" not in str(call.get("body") or b"")


def test_base_url_userinfo_rejected(fake_transport, monkeypatch, capsys):
    """带 userinfo 的 base URL（如 http://user:pass@host）必须被拒绝。"""
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-x")
    from workbench_ai_client import main

    rc = main([
        "status",
        "--base-url", "http://user:secret@127.0.0.1:8790",
        "--execute",
    ])
    assert rc != 0
    assert fake_transport.calls == []


# ---------------------------------------------------------------------------
# 既有目标校验：session/index 必须存在
# ---------------------------------------------------------------------------

def test_execute_observe_missing_session_fails(fake_transport, monkeypatch, capsys):
    """observe 目标 session_id 缺失时安全失败，不发请求。"""
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-x")
    from workbench_ai_client import main

    rc = main([
        "observe",
        "--source", "module_log",
        "--kind", "literal", "--value", "x",
        "--execute",
    ])
    assert rc != 0
    assert fake_transport.calls == []


def test_execute_listener_observe_requires_index(fake_transport, monkeypatch, capsys):
    """listener 目标必须提供 index_id，缺失时安全失败。"""
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-x")
    from workbench_ai_client import main

    rc = main([
        "observe",
        "--source", "listener",
        "--kind", "literal", "--value", "x",
        "--execute",
    ])
    assert rc != 0
    assert fake_transport.calls == []


# ---------------------------------------------------------------------------
# wait：终态停止、不 cancel、30s 上限
# ---------------------------------------------------------------------------

def test_wait_stops_at_terminal_state(fake_transport, monkeypatch):
    """wait 到 matched 终态即停止，不再请求 cancel。"""
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-x")
    fake_transport.respond("GET", "http://127.0.0.1:8790/api/ai/v1/operations/op-1/wait", {
        "operation_id": "op-1", "state": "matched",
    }, status=200)
    from workbench_ai_client import main

    main(["wait", "--operation-id", "op-1", "--execute"])
    urls = [c["url"] for c in fake_transport.calls]
    assert any("/operations/op-1/wait" in u for u in urls)
    assert not any("cancel" in u for u in urls)


def test_wait_timeout_parameter_bounded(fake_transport, monkeypatch):
    """wait 的单次服务端 timeout 参数不得超过 30。"""
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-x")
    fake_transport.respond("GET", "http://127.0.0.1:8790/api/ai/v1/operations/op-1/wait", {
        "operation_id": "op-1", "state": "waiting",
    }, status=200)
    from workbench_ai_client import main

    main(["wait", "--operation-id", "op-1", "--execute"])
    call = fake_transport.calls[0]
    assert "timeout_seconds" in call["url"]
    # 解析 query 中的 timeout_seconds 值
    import urllib.parse
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(call["url"]).query)
    assert int(qs["timeout_seconds"][0]) <= 30


# ---------------------------------------------------------------------------
# artifact / frame-detail 保留服务端 ID 与复合深链
# ---------------------------------------------------------------------------

def test_artifact_uses_server_artifact_id(fake_transport, monkeypatch, capsys):
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-x")
    fake_transport.respond("GET", "http://127.0.0.1:8790/api/ai/v1/artifacts/art-42", {
        "artifact_id": "art-42", "kind": "log_slice",
    }, status=200)
    from workbench_ai_client import main

    main(["artifact", "--artifact-id", "art-42", "--execute"])
    urls = [c["url"] for c in fake_transport.calls]
    assert any("/artifacts/art-42" in u for u in urls)
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "art-42" in out


def test_frame_detail_preserves_index_and_frame_keys(fake_transport, monkeypatch, capsys):
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-x")
    fake_transport.respond(
        "GET",
        "http://127.0.0.1:8790/api/ai/v1/listener/indexes/idx-7/frames/42",
        {"index_id": "idx-7", "frame_id": 42, "parsed": {}},
        status=200,
    )
    from workbench_ai_client import main

    main([
        "frame-detail",
        "--index-id", "idx-7", "--frame-id", "42", "--execute",
    ])
    urls = [c["url"] for c in fake_transport.calls]
    assert any("/listener/indexes/idx-7/frames/42" in u for u in urls)
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "idx-7" in out and "42" in out


def test_listener_schema_command(fake_transport, monkeypatch, capsys):
    monkeypatch.setenv("WORKBENCH_AI_TOKEN", "tok-x")
    fake_transport.respond("GET", "http://127.0.0.1:8790/api/ai/v1/listener/schema", {
        "fields": ["frame_id", "timestamp", "central_beacon"],
    }, status=200)
    from workbench_ai_client import main

    main(["listener-schema", "--execute"])
    urls = [c["url"] for c in fake_transport.calls]
    assert any("/listener/schema" in u for u in urls)
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "central_beacon" in out


# ---------------------------------------------------------------------------
# 危险命令不存在
# ---------------------------------------------------------------------------

def test_no_dangerous_subcommands(fake_transport, capsys):
    """客户端不提供 ensure/start/stop/send/flash/burn/cancel 任何子命令。"""
    from workbench_ai_client import main

    for danger in ("ensure", "start", "stop", "send", "flash", "burn", "cancel"):
        with pytest.raises(SystemExit) as exc:
            main([danger])
        assert exc.value.code != 0, f"dangerous subcommand {danger} must not exist"
