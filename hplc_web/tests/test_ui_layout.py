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

        self.assertIn('id="period-input"', html)
        self.assertIn('type="number"', html)
        self.assertIn('min="1"', html)
        self.assertIn('max="1440"', html)
        self.assertIn('value="15"', html)
        self.assertIn('id="cco-tei-input"', html)
        self.assertIn('value="001"', html)
        self.assertNotIn('id="dedup-checkbox"', html)
        self.assertIn('id="minute-query-button"', html)
        self.assertNotIn('id="minute-summary-cards"', html)
        self.assertIn('id="minute-period-table"', html)
        self.assertIn("CCO", html)
        self.assertIn('id="minute-report-details"', html)

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

    def test_page_has_one_unified_log_file_picker_button(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="pick-button"', html)
        self.assertIn("选择日志文件", html)
        self.assertNotIn('id="browse-button"', html)
        self.assertIn('id="file-picker"', html)
        self.assertIn('id="picker-roots"', html)
        self.assertIn('id="picker-list"', html)
        self.assertIn('id="picker-confirm"', html)
        self.assertIn('id="picker-up"', html)

    def test_file_picker_is_outside_main_section(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        main_end = html.split("</main>")[0]

        self.assertNotIn('id="file-picker"', main_end)

    def test_js_implements_file_picker_logic(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("pickerOpen", js)
        self.assertIn("pickerList", js)
        self.assertIn("pickerRoots", js)
        self.assertIn("pickerConfirm", js)
        self.assertIn("/api/fs/roots", js)
        self.assertIn("/api/fs/list", js)
        self.assertIn("/api/fs/last", js)
        self.assertIn('const data = await request("/api/fs/pick")', js)
        self.assertNotIn('elements.pick.addEventListener("click", pickerOpen)', js)
        self.assertIn("hplc-log-path", js)
        self.assertIn("encodeURIComponent", js)

    def test_minute_analysis_uses_two_column_detail_interaction(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn("renderMinuteReportDetails", js)
        self.assertIn("application_raw", js)
        self.assertIn("data_status", js)
        self.assertIn("minute-analysis-layout", css)
        self.assertIn("minute-report-row", css)

    def test_nid_filter_and_summary_column_are_present(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="nid-filter"', html)
        self.assertIn("<th>NID</th>", html)
        self.assertIn('nidFilter: $("#nid-filter")', js)
        self.assertIn('summaryValue(summary, "SNID")', js)
        self.assertIn("nid: state.nid", js)
        self.assertIn('nid: state.nid,', js)

    def test_all_analyses_carry_global_nid_filter(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn("updateNidHint", js)
        self.assertIn("当前 NID 筛选", js)
        self.assertIn('id="minute-nid-hint"', html)
        self.assertIn('id="task-config-nid-hint"', html)
        self.assertIn(".nid-hint", css)
        # loadFrames / loadMinuteAnalysis / task config requests 均携带全局 nid
        self.assertGreaterEqual(js.count("nid: state.nid,"), 3)

    def test_task_config_view_uses_task_dropdown_and_sta_summary(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="task-config-task-select"', html)
        self.assertIn('id="task-config-sta-table"', html)
        self.assertIn("/api/logs/task-config-tasks", js)
        self.assertIn("/api/logs/task-config-summary", js)
        self.assertIn("renderTaskConfigSummary", js)

    def test_js_summarizes_minute_report_data_status(self):
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("summarizeMinuteReports", js)
        self.assertIn("帧有数据", js)
        self.assertIn("帧重复上报", js)
        self.assertIn("帧无数据", js)
        # 有数据 = 已携带数据；其余所有未携带采集数据的帧（无数据/无冻结数据/
        # 任务不存在/其他原因/解析失败等）全部计入无数据，dataCount+noDataCount 恒等于 report_count
        self.assertIn('data_status === "已携带数据"', js)
        self.assertIn("noDataCount += 1", js)
        self.assertNotIn('else if (report.data_status === "无数据")', js)

    def test_css_styles_file_picker(self):
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".file-picker-overlay", css)
        self.assertIn(".file-picker", css)
        self.assertIn(".file-picker-row", css)
        self.assertIn(".file-picker-row.selected", css)
        self.assertIn(".file-picker-empty", css)
        self.assertIn(".picker-root-button", css)


if __name__ == "__main__":
    unittest.main()
