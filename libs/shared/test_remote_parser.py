"""RemoteHplcParser / resolve_remote_parse_url 单测（纯本地，无真实网络/DLL）。"""
import json
from pathlib import Path

import httpx
import pytest

from shared.remote_parser import (
    RemoteHplcParser,
    RemoteParseError,
    resolve_remote_parse_url,
)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok", "dll_available": True})
    if request.url.path == "/api/version":
        return httpx.Response(
            200,
            json={"name": "GW_SMAnalysis", "version": "V1.0.23", "date": "2026-01-01"},
        )
    if request.url.path == "/api/parse":
        body = json.loads(request.content.decode())
        assert body["hex"] == "7e01027e"
        simple = json.dumps({"simple": True, "len": 4}, ensure_ascii=False)
        full = json.dumps({"full": True, "len": 4}, ensure_ascii=False)
        return httpx.Response(200, json={"simple": simple, "full": full})
    return httpx.Response(404, text="not found")


class TestResolveRemoteParseUrl:
    def test_env_wins_over_config(self, tmp_path: Path):
        cfg = tmp_path / "remote_parse.json"
        cfg.write_text('{"url": "http://cfg"}', encoding="utf-8")
        assert (
            resolve_remote_parse_url({"HPLC_REMOTE_PARSE_URL": "http://env"}, cfg)
            == "http://env"
        )

    def test_config_fallback(self, tmp_path: Path):
        cfg = tmp_path / "remote_parse.json"
        cfg.write_text('{"url": "http://cfg"}', encoding="utf-8")
        assert resolve_remote_parse_url({}, cfg) == "http://cfg"

    def test_none_when_unset(self, tmp_path: Path):
        assert resolve_remote_parse_url({}, tmp_path / "missing.json") is None

    def test_invalid_config_returns_none(self, tmp_path: Path):
        cfg = tmp_path / "remote_parse.json"
        cfg.write_text("{broken", encoding="utf-8")
        assert resolve_remote_parse_url({}, cfg) is None


class TestRemoteHplcParser:
    def _parser(self) -> RemoteHplcParser:
        return RemoteHplcParser(
            "http://127.0.0.1:8700", transport=httpx.MockTransport(_ok_handler)
        )

    def test_version(self):
        assert self._parser().version()["name"] == "GW_SMAnalysis"

    def test_health(self):
        assert self._parser().health()["dll_available"] is True

    def test_parse_simple_and_full_single_request(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            return _ok_handler(request)

        parser = RemoteHplcParser("http://x", transport=httpx.MockTransport(handler))
        frame = bytes.fromhex("7e01027e")
        simple = parser.parse_simple(frame)
        full = parser.parse_full(frame)
        assert json.loads(simple)["simple"] is True
        assert json.loads(full)["full"] is True
        assert calls == ["/api/parse"]  # 同帧只发一次请求（共享缓存）

    def test_503_raises_remote_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        parser = RemoteHplcParser("http://x", transport=httpx.MockTransport(handler))
        with pytest.raises(RemoteParseError):
            parser.parse_simple(bytes.fromhex("7e01027e"))

    def test_422_raises_remote_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, text="bad frame")

        parser = RemoteHplcParser("http://x", transport=httpx.MockTransport(handler))
        with pytest.raises(RemoteParseError):
            parser.parse_simple(bytes.fromhex("7e01027e"))

    def test_parse_malformed_body_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"unexpected": True})

        parser = RemoteHplcParser("http://x", transport=httpx.MockTransport(handler))
        with pytest.raises(RemoteParseError):
            parser.parse_simple(bytes.fromhex("7e01027e"))
