"""模块日志动态会话前端契约测试。

动态页签依赖服务端 sessions API；此处验证独立页与工作台副本的关键
DOM/API 契约，避免再次回退到固定 CCO/STA 双列。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STANDALONE_HTML = ROOT / "static" / "module-serial.html"
STANDALONE_JS = ROOT / "static" / "module-serial.js"
WORKBENCH_HTML = ROOT.parent / "workbench" / "static" / "pages" / "module-serial" / "module-serial.html"
WORKBENCH_JS = ROOT.parent / "workbench" / "static" / "pages" / "module-serial" / "module-serial.js"


class ModuleDynamicFrontendContractTest(unittest.TestCase):
    def test_live_view_has_one_current_session_panel_and_dynamic_tab_controls(self):
        html = STANDALONE_HTML.read_text(encoding="utf-8")

        self.assertIn('id="ms-session-tabs"', html)
        self.assertIn('id="ms-add-session"', html)
        self.assertIn('id="ms-close-session"', html)
        self.assertIn('id="ms-session-panel"', html)
        self.assertNotIn('id="ms-panel-cco"', html)
        self.assertNotIn('id="ms-panel-sta"', html)
        self.assertNotIn('id="ms-send-channel"', html)

    def test_live_logic_targets_session_resource_api_and_keeps_per_session_state(self):
        js = STANDALONE_JS.read_text(encoding="utf-8")

        self.assertIn("sessionsById", js)
        self.assertIn("activeSessionId", js)
        self.assertIn("lastSeqBySessionId", js)
        self.assertIn("viewStateBySessionId", js)
        self.assertIn("createSession", js)
        self.assertIn("switchSession", js)
        self.assertIn("closeActiveSession", js)
        self.assertIn('api("/module-serial/sessions")', js)
        self.assertIn('"/write-text"', js)
        self.assertIn('"/logs?after="', js)
        self.assertIn('"/stop"', js)
        self.assertIn('"/flash"', js)

    def test_mapping_details_and_dynamic_session_are_available_to_compare_view(self):
        html = STANDALONE_HTML.read_text(encoding="utf-8")
        js = STANDALONE_JS.read_text(encoding="utf-8")

        self.assertIn('id="cmp-session"', html)
        self.assertIn("port_details", js)
        self.assertIn("cmpRefreshSessions", js)
        self.assertIn("session_id", js)

    def test_simcon_port_mapping_applies_maintained_serial_defaults(self):
        js = STANDALONE_JS.read_text(encoding="utf-8")

        self.assertIn("simconApplyPortDetail", js)
        self.assertIn("simcon.portDetails", js)
        self.assertIn("mapping_id", js)
        self.assertIn("simcon-port", js)

    def test_embedded_copy_uses_proxy_api_base_but_same_dynamic_contract(self):
        html = WORKBENCH_HTML.read_text(encoding="utf-8")
        js = WORKBENCH_JS.read_text(encoding="utf-8")

        # workbench 副本经 _PrefixProxy 挂载在 /api/module-serial 下：
        # data-api-base="/api" + api("/module-serial/sessions")
        #   -> /api/module-serial/sessions -> 子应用路由命中（无双前缀 404）。
        self.assertIn('data-api-base="/api"', html)
        self.assertNotIn('data-api-base="/api/module-serial"', html)
        self.assertIn("const API_BASE", js)
        self.assertIn('api("/module-serial/sessions")', js)
        self.assertIn("activeSessionId", js)
        self.assertNotIn('id="ms-panel-cco"', html)

    def test_both_javascript_copies_parse(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node 未安装")
        for js_path in (STANDALONE_JS, WORKBENCH_JS):
            subprocess.run([node, "--check", str(js_path)], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
