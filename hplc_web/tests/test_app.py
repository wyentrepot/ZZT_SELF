import unittest

from fastapi.testclient import TestClient

from hplc_web.app import create_app


class FakeService:
    def parse(self, value: str) -> dict:
        return {
            "frame": {"length": 2, "normalized_hex": value},
            "simple": {"ok": True},
            "full": {"ok": True},
        }

    def version(self) -> dict:
        return {"name": "test", "version": "1", "date": "today"}


class FakeLogService:
    def __init__(self):
        self.loaded_path = None

    def start_index(self, path):
        self.loaded_path = path
        return {"state": "indexing", "source_path": str(path)}

    def status(self):
        return {"state": "completed", "frame_count": 12, "progress": 1.0}

    def list_frames(self, offset=0, limit=100, query=""):
        return {
            "items": [{"id": 1, "sequence": "406727", "summary": {"帧类型": "SOF"}}],
            "offset": offset,
            "limit": limit,
            "total": 1,
        }

    def get_frame(self, frame_id):
        return {"id": frame_id, "analysis": {"full": {"ok": True}}}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.log_service = FakeLogService()
        self.client = TestClient(create_app(FakeService(), self.log_service))

    def test_serves_debug_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("HPLC", response.text)

    def test_parse_endpoint_returns_results(self):
        response = self.client.post("/api/parse", json={"hex": "7E 7E"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["simple"]["ok"])

    def test_parse_endpoint_returns_validation_error(self):
        class InvalidService(FakeService):
            def parse(self, value: str) -> dict:
                from hplc_web.parser_service import FrameValidationError

                raise FrameValidationError("坏报文")

        client = TestClient(create_app(InvalidService(), self.log_service))
        response = client.post("/api/parse", json={"hex": "bad"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "坏报文")

    def test_starts_local_log_index(self):
        response = self.client.post(
            "/api/logs/open", json={"path": r"D:\logs\sample.txt"}
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.log_service.loaded_path, r"D:\logs\sample.txt")

    def test_returns_log_status_and_paginated_frames(self):
        status = self.client.get("/api/logs/status")
        frames = self.client.get("/api/logs/frames?offset=20&limit=50")
        self.assertEqual(status.json()["frame_count"], 12)
        self.assertEqual(frames.json()["offset"], 20)
        self.assertEqual(frames.json()["limit"], 50)

    def test_returns_selected_frame_detail(self):
        response = self.client.get("/api/logs/frames/7")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 7)


if __name__ == "__main__":
    unittest.main()
