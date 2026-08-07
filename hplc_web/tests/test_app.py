import inspect
import unittest
from pathlib import Path
from unittest import mock

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

    def list_frames(self, offset=0, limit=100, query="", nid="", start_time="", end_time=""):
        self.last_frames_query = {
            "offset": offset,
            "limit": limit,
            "query": query,
            "nid": nid,
            "start_time": start_time,
            "end_time": end_time,
        }
        return {
            "items": [{"id": 1, "sequence": "406727", "summary": {"帧类型": "SOF"}}],
            "offset": offset,
            "limit": limit,
            "total": 1,
        }

    def get_frame(self, frame_id):
        return {"id": frame_id, "analysis": {"full": {"ok": True}}}

    def list_minute_periods(self, period_minutes=15, cco_tei="001", nid=""):
        self.last_minute_query = (period_minutes, cco_tei, nid)
        return [
            {
                "period_start": 0,
                "period_end": 900000,
                "report_count": 23,
                "reports": [{
                    "frame_id": 1,
                    "source_mac": "340100141223",
                    "source_tei": "009",
                    "freeze_time": "2026-07-31 23:55:00",
                    "application_raw": "11E40000",
                }],
                "description": "00:00:00.000 - 00:15:00.000",
            }
        ]

    def delete_config_stats(self, cco_tei="001", nid=""):
        self.last_delete_stats_query = (cco_tei, nid)
        return {
            "down_total": 0,
            "down_deduped": 0,
            "up_total": 0,
            "up_deduped": 0,
            "up_success": 0,
            "up_fail": 0,
        }

    def delete_config_details(self, cco_tei="001", nid=""):
        self.last_delete_details_query = (cco_tei, nid)
        return {"down": [], "up": []}

    def list_task_config_numbers(self, cco_tei="001", nid=""):
        self.last_task_list_query = (cco_tei, nid)
        return ["2", "3"]

    def task_config_summary(self, cco_tei, task_no, nid=""):
        self.last_task_summary_query = (cco_tei, task_no, nid)
        return {"task_no": task_no, "sent_sta_count": 1, "stas": []}

    def list_task_minute_periods(self, task_no, period_minutes=None, cco_tei="001", nid=""):
        self.last_task_minute_query = (task_no, period_minutes, cco_tei, nid)
        return {
            "task_no": task_no, "source": "configured",
            "derived_period_minutes": 10, "periods": [],
            "unconfigured_report_count": 0, "unconfigured_reports": [],
        }

    def task_derived_period(self, cco_tei="001", task_no="", nid=""):
        self.last_task_derived_query = (cco_tei, task_no, nid)
        return {
            "task_no": task_no, "source": "configured",
            "derived_period_minutes": 10,
        }


class AppTests(unittest.TestCase):
    def setUp(self):
        self.log_service = FakeLogService()
        self.client = TestClient(create_app(FakeService(), self.log_service))

    def test_serves_debug_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("HPLC", response.text)

    def test_version_reports_picker_api_revision(self):
        response = self.client.get("/api/version")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["picker_api_revision"], 2)
        self.assertEqual(response.json()["minute_analysis_api_revision"], 3)
        self.assertEqual(response.json()["frame_filter_api_revision"], 2)

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

    def test_frames_endpoint_accepts_nid_filter(self):
        response = self.client.get("/api/logs/frames?nid=00000123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.log_service.last_frames_query["nid"], "00000123")
        self.assertEqual(self.log_service.last_frames_query["query"], "")

    def test_frames_endpoint_passes_time_range_filter(self):
        response = self.client.get(
            "/api/logs/frames?start_time=09:00:00&end_time=10:30:00"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.log_service.last_frames_query["start_time"], "09:00:00")
        self.assertEqual(self.log_service.last_frames_query["end_time"], "10:30:00")

    def test_returns_selected_frame_detail(self):
        response = self.client.get("/api/logs/frames/7")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 7)

    def test_minute_analysis_returns_periods_summary_and_filters(self):
        response = self.client.get(
            "/api/logs/minute-analysis?period_minutes=15&cco_tei=001"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("periods", data)
        self.assertIn("summary", data)
        self.assertIn("filters", data)
        self.assertEqual(data["summary"]["total_periods"], 1)
        self.assertEqual(data["summary"]["report_count"], 23)
        self.assertNotIn("duplicate_count", data["summary"])
        self.assertNotIn("deduplicate", data["filters"])

    def test_minute_analysis_passes_nid_to_service(self):
        response = self.client.get(
            "/api/logs/minute-analysis?period_minutes=15&cco_tei=001&nid=00000123"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.log_service.last_minute_query, (15, "001", "00000123"))

    def test_delete_config_details_passes_nid_to_service(self):
        response = self.client.get(
            "/api/logs/delete-config-details?cco_tei=001&nid=00000123"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.log_service.last_delete_details_query, ("001", "00000123")
        )

    def test_task_config_endpoints_pass_filters_and_task_number(self):
        tasks = self.client.get(
            "/api/logs/task-config-tasks?cco_tei=001&nid=00000123"
        )
        summary = self.client.get(
            "/api/logs/task-config-summary?cco_tei=001&task_no=2&nid=00000123"
        )

        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(tasks.json()["tasks"], ["2", "3"])
        self.assertEqual(self.log_service.last_task_list_query, ("001", "00000123"))
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["task_no"], "2")
        self.assertEqual(self.log_service.last_task_summary_query, ("001", "2", "00000123"))

    def test_task_minute_analysis_passes_task_and_period_to_service(self):
        response = self.client.get(
            "/api/logs/task-minute-analysis?task_no=2&cco_tei=001&nid=00000123"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.log_service.last_task_minute_query, ("2", None, "001", "00000123")
        )
        self.assertEqual(response.json()["derived_period_minutes"], 10)

    def test_task_derived_period_endpoint(self):
        response = self.client.get(
            "/api/logs/task-derived-period?task_no=2&cco_tei=001&nid=00000123"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.log_service.last_task_derived_query, ("001", "2", "00000123")
        )
        self.assertEqual(response.json()["derived_period_minutes"], 10)

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


class FsApiTests(unittest.TestCase):
    """文件选择器相关 API：roots / list / last。"""

    def setUp(self):
        import tempfile

        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        (root / "sub" / "nested").mkdir(parents=True, exist_ok=True)
        (root / "sub" / "report.txt").write_text("7E FF", encoding="utf-8")
        (root / "sub" / "notes.md").write_text("no", encoding="utf-8")  # 非日志扩展名
        (root / "top.log").write_text("7E", encoding="utf-8")
        self.root = root

        from hplc_web import app as app_module

        self.app_module = app_module
        self.log_service = FakeLogService()
        self.client = TestClient(create_app(FakeService(), self.log_service))
        self._fs_patch = mock.patch.object(
            app_module, "_windows_drives", return_value=[
                {"name": "C:\\", "path": "C:\\"},
                {"name": "D:\\", "path": "D:\\"},
            ]
        )
        self._fs_patch.start()
        self.addCleanup(self._fs_patch.stop)

    def test_roots_returns_windows_drives(self):
        response = self.client.get("/api/fs/roots")
        self.assertEqual(response.status_code, 200)
        names = [item["name"] for item in response.json()["roots"]]
        self.assertIn("C:\\", names)
        self.assertIn("D:\\", names)

    def test_list_returns_dirs_and_log_files_only(self):
        response = self.client.get("/api/fs/list", params={"path": str(self.root)})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["path"], str(self.root))
        dir_names = [item["name"] for item in data["dirs"]]
        file_names = [item["name"] for item in data["files"]]
        self.assertIn("sub", dir_names)
        self.assertIn("top.log", file_names)
        self.assertNotIn("notes.md", file_names)  # 非日志扩展名被过滤

    def test_list_nested_directory_reports_parent(self):
        target = self.root / "sub"
        response = self.client.get("/api/fs/list", params={"path": str(target)})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["parent"], str(self.root))
        self.assertIn("nested", [item["name"] for item in data["dirs"]])
        self.assertIn("report.txt", [item["name"] for item in data["files"]])

    def test_list_missing_directory_returns_404(self):
        response = self.client.get(
            "/api/fs/list", params={"path": str(self.root / "nope")}
        )
        self.assertEqual(response.status_code, 404)

    def test_list_file_path_lists_parent_directory(self):
        """传入文件路径时自动定位到其父目录（文件选择器默认定位用）。"""
        response = self.client.get(
            "/api/fs/list", params={"path": str(self.root / "top.log")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["path"], str(self.root))
        self.assertIn("top.log", [item["name"] for item in data["files"]])

    def test_last_returns_empty_when_never_opened(self):
        with mock.patch.object(
            self.app_module, "LAST_PATH_FILE",
            Path(self.tempdir.name) / "missing_last.txt",
        ):
            response = self.client.get("/api/fs/last")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["path"], "")

    def test_open_log_persists_last_path(self):
        last_file = Path(self.tempdir.name) / "runtime" / "last_path.txt"
        with mock.patch.object(self.app_module, "LAST_PATH_FILE", last_file):
            response = self.client.post(
                "/api/logs/open", json={"path": r"D:\logs\sample.txt"}
            )
            self.assertEqual(response.status_code, 202)
            self.assertEqual(
                last_file.read_text(encoding="utf-8"), r"D:\logs\sample.txt"
            )
            last = self.client.get("/api/fs/last")
            self.assertEqual(last.json()["path"], r"D:\logs\sample.txt")

    def test_open_log_rejects_overlong_path(self):
        """路径超过 1024 字符时返回 422（与 fs_list 限长一致）。"""
        response = self.client.post(
            "/api/logs/open", json={"path": "D:\\" + "a" * 1100}
        )
        self.assertEqual(response.status_code, 422)

    def test_list_real_workspace_directory_finds_sample_log(self):
        """集成验证：真实日志目录可被 fs API 浏览到样本文件。"""
        workspace = Path(__file__).resolve().parents[1].parent
        target = workspace / "测试文件"
        response = self.client.get(
            "/api/fs/list", params={"path": str(target)}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        file_names = [item["name"] for item in data["files"]]
        self.assertIn("并发抄表-样本.txt", file_names)
        self.assertIn("测试文本.txt", file_names)
        dir_names = [item["name"] for item in data["dirs"]]
        self.assertIn("并发抄表-测试文件", dir_names)

    def test_pick_returns_selected_native_path(self):
        """/api/fs/pick 调用原生对话框函数并返回选中的路径。"""
        with mock.patch.object(
            self.app_module, "_pick_file_via_native_dialog",
            return_value=r"D:\logs\sample.txt",
        ):
            response = self.client.get("/api/fs/pick")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["path"], r"D:\logs\sample.txt")

    def test_pick_returns_empty_when_cancelled(self):
        """用户在原生对话框中取消时返回空路径。"""
        with mock.patch.object(
            self.app_module, "_pick_file_via_native_dialog", return_value=""
        ):
            response = self.client.get("/api/fs/pick")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["path"], "")

    def test_pick_passes_last_path_as_initial_dir(self):
        """对话框初始目录取自上次打开的路径（dir 参数）。"""
        with mock.patch.object(
            self.app_module, "_read_last_path", return_value=r"D:\logs\sample.txt"
        ), mock.patch.object(
            self.app_module, "_pick_file_via_native_dialog",
            return_value=r"D:\logs\sample.txt",
        ) as pick:
            self.client.get("/api/fs/pick")
        self.assertEqual(pick.call_args.args[0], r"D:\logs\sample.txt")

    def test_native_picker_uses_sta_powershell_open_file_dialog(self):
        """本地运行时必须通过 STA PowerShell 打开 Windows 文件对话框。"""
        source = inspect.getsource(self.app_module._pick_file_via_native_dialog)

        self.assertIn("subprocess.run", source)
        self.assertIn('"-STA"', source)
        self.assertIn("OpenFileDialog", self.app_module._POWERSHELL_PICK_FILE_SCRIPT)

    def test_native_picker_decodes_utf8_base64_path_from_powershell(self):
        completed = mock.Mock(
            returncode=0,
            stdout="RDpc5L6m5ZCs5Y+w5pS56YCgXOa1i+ivleaWh+S7tlzljp/lp4vmiqXmlocudHh0\r\n",
            stderr="",
        )
        with mock.patch.object(
            self.app_module.subprocess, "run", return_value=completed
        ) as run:
            path = self.app_module._pick_file_via_native_dialog(r"D:\logs")

        self.assertEqual(path, r"D:\侦听台改造\测试文件\原始报文.txt")
        command = run.call_args.args[0]
        self.assertIn("powershell", command[0].lower())
        self.assertIn("-STA", command)
        self.assertEqual(run.call_args.kwargs["env"]["HPLC_PICKER_INITIAL_DIR"], r"D:\logs")
        self.assertIn("ToBase64String", self.app_module._POWERSHELL_PICK_FILE_SCRIPT)

    def test_pick_endpoint_returns_powershell_error(self):
        with mock.patch.object(
            self.app_module, "_pick_file_via_native_dialog",
            side_effect=RuntimeError("dialog unavailable"),
        ):
            response = self.client.get("/api/fs/pick")

        self.assertEqual(response.status_code, 500)
        self.assertIn("dialog unavailable", response.json()["detail"])


class FakeLogMutex:
    """支持 reset_index / 可变状态的日志服务，用于互斥测试。"""

    def __init__(self, state="idle"):
        self._state = state

    def status(self):
        return {"state": self._state}

    def start_index(self, path):
        self._state = "indexing"
        return {"state": "indexing"}

    def reset_index(self):
        self._state = "idle"
        return {"state": "idle"}


class FakeSerialMutex:
    """支持可变状态的串口服务，用于互斥测试。"""

    def __init__(self, state="idle"):
        self._state = state

    def status(self):
        return {"state": self._state}

    def start(self, **kwargs):
        self._state = "running"
        return {"state": "running"}

    def stop(self):
        self._state = "idle"
        return {"state": "idle"}

    def list_available_ports(self):
        return []


class SerialLogMutexTests(unittest.TestCase):
    """数据源二选一：串口监听与日志文件分析运行时互斥。"""

    def _client(self, log_state="idle", serial_state="idle"):
        return TestClient(
            create_app(
                FakeService(),
                FakeLogMutex(state=log_state),
                FakeSerialMutex(state=serial_state),
            )
        )

    def test_open_log_409_when_serial_running(self):
        client = self._client(log_state="idle", serial_state="running")
        response = client.post("/api/logs/open", json={"path": r"D:\x.txt"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("串口监听正在运行", response.json()["detail"])

    def test_open_log_ok_when_serial_idle(self):
        client = self._client(log_state="idle", serial_state="idle")
        response = client.post("/api/logs/open", json={"path": r"D:\x.txt"})
        self.assertEqual(response.status_code, 202)

    def test_serial_start_409_when_log_indexing(self):
        client = self._client(log_state="indexing", serial_state="idle")
        response = client.post("/api/serial/start", json={"port": "COM19"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("日志正在建立索引", response.json()["detail"])

    def test_serial_start_ok_when_log_idle(self):
        client = self._client(log_state="idle", serial_state="idle")
        response = client.post("/api/serial/start", json={"port": "COM19"})
        self.assertEqual(response.status_code, 202)


if __name__ == "__main__":
    unittest.main()
