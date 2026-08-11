"""module_log/desktop.py（pywebview 内嵌窗口入口）单元测试。

覆盖 pywebview 新增部分的启动逻辑，不实际起 Uvicorn 服务、不弹真实窗口：
1. desktop 模块可导入，PORT/TITLE 常量正确
2. _start_server 用正确的 module_log.app:app 字符串 + 端口（mock uvicorn）
3. main() 装了 webview 时：创建窗口 URL 指向 /module-serial、调用 webview.start
4. main() 未装 webview 时：回退 webbrowser 打开 + 仍启动服务
5. module_log.app:app 字符串可解析出 FastAPI（frozen 下服务启动的关键）

注意：desktop.py 中 uvicorn/webview/webbrowser 均为函数内 `import X`，
故用 mock.patch.dict(sys.modules, ...) 注入假模块拦截。
"""
import importlib
import sys
import threading
import types
import unittest
from unittest import mock

import module_log.desktop as desktop


class _FakeUvicorn:
    """假 uvicorn：记录 run 调用，不真正阻塞。"""

    def __init__(self):
        self.run_calls = []

    def run(self, *args, **kwargs):
        self.run_calls.append((args, kwargs))


class _FakeWebview:
    """假 webview：记录 create_window / start 调用。"""

    def __init__(self):
        self.windows = []
        self.start_calls = 0

    def create_window(self, title, url, **kw):
        self.windows.append((title, url, kw))

    def start(self):
        self.start_calls += 1


class _FakeWebBrowser:
    """假 webbrowser：记录 open 调用。"""

    def __init__(self):
        self.opened = []

    def open(self, url, **kw):
        self.opened.append(url)


class DesktopModuleTests(unittest.TestCase):
    def test_desktop_importable_and_constants(self):
        """desktop.py 可导入，端口 8766，标题正确。"""
        self.assertEqual(desktop.PORT, 8766)
        self.assertIn("模块日志", desktop.TITLE)
        self.assertIn("烧录", desktop.TITLE)

    def test_app_string_resolves_to_fastapi(self):
        """'module_log.app:app' 字符串能解析出 FastAPI，且静态资源就位（frozen 关键）。"""
        mod = importlib.import_module("module_log.app")
        self.assertTrue(hasattr(mod, "app"))
        self.assertIn("FastAPI", type(mod.app).__name__)
        self.assertTrue(mod.STATIC_DIR.exists())
        self.assertTrue((mod.STATIC_DIR / "module-serial.html").exists())


class StartServerTests(unittest.TestCase):
    def test_start_server_uses_app_and_port(self):
        """_start_server 调用 uvicorn.run：module_log.app:app + 127.0.0.1:8766。"""
        fake_uv = _FakeUvicorn()
        with mock.patch.dict(sys.modules, {"uvicorn": fake_uv}):
            desktop._start_server()
        self.assertEqual(len(fake_uv.run_calls), 1)
        args, kwargs = fake_uv.run_calls[0]
        self.assertEqual(args[0], "module_log.app:app")
        self.assertEqual(kwargs.get("host"), "127.0.0.1")
        self.assertEqual(kwargs.get("port"), 8766)


class MainWebviewBranchTests(unittest.TestCase):
    def test_main_with_webview_creates_window_and_starts(self):
        """装了 webview：创建窗口加载 /module-serial，调用 webview.start()，起服务线程。"""
        fake_wv = _FakeWebview()
        fake_uv = _FakeUvicorn()

        with mock.patch.dict(sys.modules, {"webview": fake_wv, "uvicorn": fake_uv}), \
             mock.patch("module_log.desktop.threading.Thread") as fake_thread:
            desktop.main()

        # 创建窗口：URL 指向 127.0.0.1:8766/module-serial
        self.assertEqual(len(fake_wv.windows), 1)
        title, url, kw = fake_wv.windows[0]
        self.assertEqual(title, desktop.TITLE)
        self.assertIn("127.0.0.1:8766", url)
        self.assertIn("/module-serial", url)
        # webview.start 被调用
        self.assertEqual(fake_wv.start_calls, 1)
        # 后台线程启动服务（daemon）
        fake_thread.assert_called_once()
        fake_thread.return_value.start.assert_called_once()

    def test_main_without_webview_falls_back_to_browser(self):
        """未装 webview：回退浏览器打开页面，且仍启动服务。"""
        fake_uv = _FakeUvicorn()
        fake_wb = _FakeWebBrowser()

        # 假 Timer：立即执行回调（真实 Timer 会延迟 1s，测试无法等待）
        class _FakeTimer:
            def __init__(self, _ms, fn, *_a, **_kw):
                self._fn = fn

            def start(self):
                self._fn()  # 立即触发回调

        # 设 webview=None 使 import webview 抛 ImportError，触发回退分支
        with mock.patch.dict(sys.modules, {"webview": None}), \
             mock.patch.dict(sys.modules, {"uvicorn": fake_uv, "webbrowser": fake_wb}), \
             mock.patch("threading.Timer", _FakeTimer):
            desktop.main()

        # 服务启动
        self.assertEqual(len(fake_uv.run_calls), 1)
        self.assertEqual(fake_uv.run_calls[0][1].get("port"), 8766)
        # 浏览器打开 /module-serial（Timer 立即触发）
        self.assertEqual(len(fake_wb.opened), 1)
        self.assertIn("127.0.0.1:8766", fake_wb.opened[0])
        self.assertIn("/module-serial", fake_wb.opened[0])


if __name__ == "__main__":
    unittest.main()
