import unittest
from pathlib import Path


MAIN_LAUNCHER = Path("启动工具.bat")
LISTENER_LAUNCHER = Path("apps/listener/启动侦听台.bat")
MODULE_LAUNCHER = Path("apps/module_log/启动模块日志.bat")


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
        self.assertTrue(LISTENER_LAUNCHER.exists(), "apps/listener 应提供启动脚本")
        content = LISTENER_LAUNCHER.read_text(encoding="gbk")

        self.assertIn("python -m venv", content)
        self.assertIn("requirements.txt", content)
        self.assertIn(r"libs\shared\dll\bin\Debug\GwHPLCAnalysis.dll", content)
        self.assertIn("listener.run", content)
        self.assertIn("8765", content)

    def test_module_launcher_bootstraps(self):
        """模块日志脚本（E-SafeNet 环境）启动已构建的桌面 exe。"""
        self.assertTrue(MODULE_LAUNCHER.exists(), "apps/module_log 应提供启动脚本")
        content = MODULE_LAUNCHER.read_text(encoding="gbk")

        # E-SafeNet 加密下源码直跑失败，脚本统一改为启动 dist 内 exe
        self.assertIn(r"dist\模块日志\模块日志.exe", content)
        self.assertIn("build_exe.bat", content)
        # 实际执行走 start 启动 exe（注释可能提及 python -m 但执行不用）
        self.assertIn('start ""', content)

    def test_no_reference_to_old_launcher_names(self):
        """新脚本不应再引用旧 hplc_web / hplc_launcher 命名。"""
        for launcher in (MAIN_LAUNCHER, LISTENER_LAUNCHER, MODULE_LAUNCHER):
            content = launcher.read_text(encoding="gbk")
            self.assertNotIn("hplc_web", content)
            self.assertNotIn("hplc_launcher", content)


if __name__ == "__main__":
    unittest.main()
