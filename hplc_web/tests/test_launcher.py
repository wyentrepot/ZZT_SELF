import unittest
from pathlib import Path


PRODUCTION_LAUNCHER = Path("启动解析工具.bat")
TEST_LAUNCHER = Path("启动解析工具-测试模式.bat")
SHARED_LAUNCHER = Path("hplc_launcher.bat")


class LauncherScriptTests(unittest.TestCase):
    def test_production_launcher_bootstraps_and_reuses_launcher(self):
        self.assertTrue(PRODUCTION_LAUNCHER.exists(), "根目录应提供正式启动脚本")
        content = PRODUCTION_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("HPLC_LAUNCH_MODE", content)
        self.assertIn("hplc_launcher.bat", content)

        shared = SHARED_LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("python -m venv", shared)
        self.assertIn("requirements.txt", shared)
        self.assertIn(r"dll\bin\Debug\GwHPLCAnalysis.dll", shared)
        self.assertNotIn(r"dll_Tesll\NwHPLCAnalysis.dll", shared)

    def test_launcher_has_mode_selection(self):
        """启动脚本支持用户选择 1=侦听台 / 2=模块日志 / 3=全部。"""
        content = SHARED_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("1 = Listener", content)
        self.assertIn("2 = Module log", content)
        self.assertIn("3 = Start both", content)
        self.assertIn('set /p "HPLC_CHOICE', content)

        # 两个独立应用入口
        self.assertIn("hplc_web.listener_run", content)
        self.assertIn("hplc_web.module_serial_run", content)

        # 端口 8765 / 8766
        self.assertIn("8765", content)
        self.assertIn("8766", content)

        # venv 就绪标记保留（避免重复依赖检查）
        self.assertIn(".deps_ready", content)

    def test_test_launcher_exists(self):
        self.assertTrue(TEST_LAUNCHER.exists(), "根目录应提供测试模式启动脚本")
        content = TEST_LAUNCHER.read_text(encoding="utf-8")
        self.assertIn("HPLC_LAUNCH_MODE=test", content)
        self.assertIn("hplc_launcher.bat", content)


if __name__ == "__main__":
    unittest.main()
