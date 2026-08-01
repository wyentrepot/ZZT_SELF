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

    def list_minute_periods(self, period_minutes=15, cco_tei="001", deduplicate=True):
        self.last_minute_query = (period_minutes, cco_tei, deduplicate)
        return [
            {
                "period_start": 0,
                "period_end": 900000,
                "raw_report_count": 23,
                "unique_station_count": 18,
                "duplicate_count": 5,
                "success_count": 23,
                "failure_count": 0,
                "parse_error_count": 0,
                "report_count": 18,
                "station_keys": ["340100141223"],
                "frame_ids": [1, 2, 3],
                "description": "00:00:00.000 - 00:15:00.000",
            }
        ]


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

    def test_minute_analysis_returns_periods_summary_and_filters(self):
        response = self.client.get(
            "/api/logs/minute-analysis?period_minutes=15&cco_tei=001&deduplicate=true"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("periods", data)
        self.assertIn("summary", data)
        self.assertIn("filters", data)
        self.assertEqual(data["summary"]["total_periods"], 1)
        self.assertEqual(data["summary"]["raw_report_count"], 23)
        self.assertEqual(data["summary"]["duplicate_count"], 5)
        self.assertEqual(
            self.log_service.last_minute_query, (15, "001", True)
        )

    def test_minute_analysis_rejects_invalid_period_and_tei(self):
        for params in (
            "period_minutes=0&cco_tei=001",
            "period_minutes=1441&cco_tei=001",
            "period_minutes=15&cco_tei=00g",
        ):
            response = self.client.get(
                f"/api/logs/minute-analysis?{params}"
            )
            self.assertEqual(response.status_code, 422, params)


if __name__ == "__main__":
    unittest.main()
