import unittest
from pathlib import Path


PRODUCTION_LAUNCHER = Path("启动解析工具.bat")
TEST_LAUNCHER = Path("启动解析工具-测试模式.bat")
SHARED_LAUNCHER = Path("hplc_launcher.bat")


class LauncherScriptTests(unittest.TestCase):
    def test_production_launcher_bootstraps_and_runs_web_app(self):
        self.assertTrue(PRODUCTION_LAUNCHER.exists(), "根目录应提供正式启动脚本")
        content = PRODUCTION_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("HPLC_LAUNCH_MODE", content)
        self.assertIn("production", content)
        self.assertIn("hplc_launcher.bat", content)

        shared = SHARED_LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("python -m venv", shared)
        self.assertIn("requirements.txt", shared)
        self.assertIn("hplc_web.run", shared)
        self.assertIn(r"dll\bin\Debug\GwHPLCAnalysis.dll", shared)
        self.assertNotIn(r"dll_Tesll\NwHPLCAnalysis.dll", shared)

    def test_test_launcher_selects_test_mode_and_reuses_bootstrap(self):
        self.assertTrue(TEST_LAUNCHER.exists(), "根目录应提供测试模式启动脚本")
        content = TEST_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("HPLC_LAUNCH_MODE=test", content)
        self.assertIn("hplc_launcher.bat", content)

    def test_launcher_opens_matching_page_when_service_already_runs(self):
        content = SHARED_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("Invoke-WebRequest", content)
        self.assertIn("openapi.json", content)
        self.assertIn("/api/fs/pick", content)
        self.assertIn("picker_api_revision", content)
        self.assertIn("minute_analysis_api_revision", content)
        self.assertIn("SERVICE_OUTDATED_RESTARTING", content)
        self.assertIn("taskkill /PID", content)
        self.assertIn(":port_in_use", content)
        self.assertIn("APP_URL", content)
        self.assertIn("mode=test", content)
        self.assertIn("SERVICE_ALREADY_RUNNING", content)


if __name__ == "__main__":
    unittest.main()
