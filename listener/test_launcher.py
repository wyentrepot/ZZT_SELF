import unittest
from pathlib import Path


MAIN_LAUNCHER = Path("启动工具.bat")
LISTENER_LAUNCHER = Path("listener/启动侦听台.bat")
MODULE_LAUNCHER = Path("module_log/启动模块日志.bat")


class LauncherScriptTests(unittest.TestCase):
    def test_main_launcher_is_menu_entry(self):
        """根目录总入口提供 1=侦听台 / 2=模块日志 / 3=全部 选择。"""
        self.assertTrue(MAIN_LAUNCHER.exists(), "根目录应提供总启动入口")
        content = MAIN_LAUNCHER.read_text(encoding="gbk")

        self.assertIn("1 = 侦听台", content)
        self.assertIn("2 = 模块日志", content)
        self.assertIn("3 = 全部", content)
        self.assertIn("启动侦听台.bat", content)
        self.assertIn("启动模块日志.bat", content)
        self.assertIn("8765", content)
        self.assertIn("8766", content)

    def test_listener_launcher_bootstraps(self):
        """侦听台脚本自建 venv、装依赖、启动 listener.run。"""
        self.assertTrue(LISTENER_LAUNCHER.exists(), "listener 应提供启动脚本")
        content = LISTENER_LAUNCHER.read_text(encoding="gbk")

        self.assertIn("python -m venv", content)
        self.assertIn("requirements.txt", content)
        self.assertIn(r"shared\dll\bin\Debug\GwHPLCAnalysis.dll", content)
        self.assertIn("listener.run", content)
        self.assertIn("8765", content)

    def test_module_launcher_bootstraps(self):
        """模块日志脚本自建 venv、装依赖、启动 module_log.run。"""
        self.assertTrue(MODULE_LAUNCHER.exists(), "module_log 应提供启动脚本")
        content = MODULE_LAUNCHER.read_text(encoding="gbk")

        self.assertIn("python -m venv", content)
        self.assertIn("requirements.txt", content)
        self.assertIn("module_log.run", content)
        self.assertIn("8766", content)

    def test_no_reference_to_old_launcher_names(self):
        """新脚本不应再引用旧 hplc_web / hplc_launcher 命名。"""
        for launcher in (MAIN_LAUNCHER, LISTENER_LAUNCHER, MODULE_LAUNCHER):
            content = launcher.read_text(encoding="gbk")
            self.assertNotIn("hplc_web", content)
            self.assertNotIn("hplc_launcher", content)


if __name__ == "__main__":
    unittest.main()
