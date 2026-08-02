import unittest
from pathlib import Path


STATIC_DIR = Path("hplc_web/static")


class UiLayoutTests(unittest.TestCase):
    def test_workspace_is_bounded_to_viewport_with_internal_table_scroll(self):
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".data-section", css)
        self.assertIn("height: calc(100vh", css)
        self.assertIn("overflow-y: auto", css)

    def test_hidden_debugger_cannot_be_overridden_by_component_display(self):
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn("[hidden]", css)
        self.assertIn("display: none !important", css)

    def test_page_keeps_file_workspace_and_detail_inspector(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('class="operation-panel"', html)
        self.assertIn('class="table-shell"', html)
        self.assertIn('class="detail-panel"', html)

    def test_page_has_frame_and_minute_analysis_tabs(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn("帧浏览", html)
        self.assertIn("分钟采集分析", html)
        self.assertIn('data-view="frames"', html)
        self.assertIn('data-view="minute"', html)

    def test_minute_analysis_controls_and_layout_are_present(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="period-select"', html)
        self.assertIn('value="15" selected', html)
        self.assertIn('id="cco-tei-input"', html)
        self.assertIn('value="001"', html)
        self.assertIn('id="dedup-checkbox"', html)
        self.assertIn("checked", html)
        self.assertIn('id="minute-query-button"', html)
        self.assertIn('id="minute-summary-cards"', html)
        self.assertIn('id="minute-period-table"', html)
        for header in ("周期", "去重上报STA数", "原始帧数", "重复数",
                       "成功", "失败", "解析异常", "简介"):
            self.assertIn(header, html)
        self.assertIn('id="minute-period-details"', html)

    def test_detail_panel_has_base_and_app_expand_tabs(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-detail-tab="base"', html)
        self.assertIn('data-detail-tab="app"', html)
        self.assertIn("基础解析", html)
        self.assertIn("应用层展开", html)
        self.assertIn('id="detail-base"', html)
        self.assertIn('id="detail-app"', html)
        self.assertIn('id="app-expand-content"', html)

    def test_detail_tabs_are_inside_detail_content(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        detail_content = html.split('id="detail-content"')[1]
        detail_panel_end = detail_content.split("</aside>")[0]

        self.assertIn('data-detail-tab="base"', detail_panel_end)
        self.assertIn('data-detail-tab="app"', detail_panel_end)
        self.assertIn('id="app-expand-content"', detail_panel_end)

    def test_js_implements_app_expand_rendering(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("renderApplicationDetail", js)
        self.assertIn("APP_EXPAND_IDS", js)
        self.assertIn('"0003"', js)
        self.assertIn('"00E2"', js)
        self.assertIn('"00E3"', js)
        self.assertIn('"00E4"', js)
        self.assertIn("switchDetailTab", js)
        self.assertIn("renderNestedFrame", js)
        self.assertIn("renderFieldTable", js)

    def test_css_styles_detail_tabs_and_nested_tree(self):
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".detail-tabs", css)
        self.assertIn(".detail-tab.active", css)
        self.assertIn(".app-field-table", css)
        self.assertIn(".nested-frame", css)
        self.assertIn(".app-expand-hint", css)


if __name__ == "__main__":
    unittest.main()
