"""apps/listener `_build_parser_service` 三档降级矩阵单测（不依赖真实 DLL / 网络）。

矩阵：local（本地 net8.0 DLL）→ remote（远程 Windows 服务）→ none（None）。
"""
import pytest
from fastapi.testclient import TestClient

import listener.app as app


class FakeDllParser:
    def parse_simple(self, frame):
        return '{"simple": true}'

    def parse_full(self, frame):
        return '{"full": true}'

    def version(self):
        return {"name": "fake", "version": "1", "date": "today"}


class FakeRemoteParser(FakeDllParser):
    def __init__(self, reachable=True):
        self.reachable = reachable

    def close(self):
        pass

    def version(self):
        if not self.reachable:
            from shared.remote_parser import RemoteParseError

            raise RemoteParseError("远程服务不可达")
        return {"name": "remote", "version": "1", "date": "today"}


def _patch_local_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("no local dll")

    monkeypatch.setattr(app, "DotNetHplcParser", boom)


def _patch_remote(monkeypatch, url, reachable=True):
    monkeypatch.setattr(app.remote_parser, "resolve_remote_parse_url", lambda: url)
    monkeypatch.setattr(
        app.remote_parser,
        "RemoteHplcParser",
        lambda base_url: FakeRemoteParser(reachable=reachable),
    )


class TestBuildParserServiceTiers:
    def test_tier1_local(self, monkeypatch):
        monkeypatch.setattr(app, "DotNetHplcParser", lambda dll: FakeDllParser())
        service = app._build_parser_service()
        assert service is not None
        assert service.parse_backend == "local"
        assert service.version()["name"] == "fake"

    def test_tier2_remote(self, monkeypatch):
        _patch_local_failure(monkeypatch)
        _patch_remote(monkeypatch, "http://127.0.0.1:8700", reachable=True)
        service = app._build_parser_service()
        assert service is not None
        assert service.parse_backend == "remote"

    def test_tier3_no_remote_configured(self, monkeypatch):
        _patch_local_failure(monkeypatch)
        _patch_remote(monkeypatch, None)
        assert app._build_parser_service() is None

    def test_tier3_remote_unreachable(self, monkeypatch):
        _patch_local_failure(monkeypatch)
        _patch_remote(monkeypatch, "http://127.0.0.1:8700", reachable=False)
        assert app._build_parser_service() is None


class TestVersionParseBackend:
    def test_version_reports_remote_backend(self, monkeypatch):
        _patch_local_failure(monkeypatch)
        _patch_remote(monkeypatch, "http://127.0.0.1:8700", reachable=True)
        client = TestClient(app.create_app(app._build_parser_service()))
        resp = client.get("/api/version")
        assert resp.status_code == 200
        assert resp.json()["parse_backend"] == "remote"
        assert resp.json()["dll_available"] is True

    def test_version_reports_none_backend_when_no_service(self):
        client = TestClient(app.create_app(None))
        resp = client.get("/api/version")
        assert resp.status_code == 200
        assert resp.json()["parse_backend"] == "none"
        assert resp.json()["dll_available"] is False

    def test_parse_503_when_no_backend(self):
        client = TestClient(app.create_app(None))
        resp = client.post("/api/parse", json={"hex": "7E 01 02 7E"})
        assert resp.status_code == 503
        assert "远程解析" in resp.json()["detail"] or "不可用" in resp.json()["detail"]
