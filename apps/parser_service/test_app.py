"""apps/parser_service 路由单测：注入 fake parser，不依赖真实 DLL / 网络。"""
import json

from fastapi.testclient import TestClient

from parser_service.app import create_app


class FakeParser:
    def __init__(self, version=None):
        self._version = version or {
            "name": "fake",
            "version": "1",
            "date": "today",
        }

    def parse_simple(self, frame: bytes) -> str:
        return json.dumps({"simple": True, "len": len(frame)}, ensure_ascii=False)

    def parse_full(self, frame: bytes) -> str:
        return json.dumps({"full": True, "len": len(frame)}, ensure_ascii=False)

    def version(self) -> dict:
        return dict(self._version)


class TestParserServiceApp:
    def _client(self, parser=None):
        return TestClient(create_app(parser))

    def test_health_ok_when_parser_present(self):
        resp = self._client(FakeParser()).get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "dll_available": True}

    def test_health_degraded_when_parser_missing(self):
        resp = self._client(None).get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "degraded", "dll_available": False}

    def test_version(self):
        resp = self._client(FakeParser()).get("/api/version")
        assert resp.status_code == 200
        assert resp.json()["name"] == "fake"

    def test_version_503_when_missing(self):
        resp = self._client(None).get("/api/version")
        assert resp.status_code == 503

    def test_parse_returns_raw_simple_full(self):
        resp = self._client(FakeParser()).post(
            "/api/parse", json={"hex": "7E 01 02 7E"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert json.loads(body["simple"])["simple"] is True
        assert json.loads(body["full"])["full"] is True

    def test_parse_422_on_bad_frame(self):
        resp = self._client(FakeParser()).post("/api/parse", json={"hex": "not hex"})
        assert resp.status_code == 422

    def test_parse_503_when_missing(self):
        resp = self._client(None).post("/api/parse", json={"hex": "7E 01 02 7E"})
        assert resp.status_code == 503

    def test_parse_500_when_parser_raises(self):
        class ExplodingParser(FakeParser):
            def parse_simple(self, frame):
                raise RuntimeError("boom")

        resp = self._client(ExplodingParser()).post(
            "/api/parse", json={"hex": "7E 01 02 7E"}
        )
        assert resp.status_code == 500
        assert "boom" in resp.json()["detail"]
