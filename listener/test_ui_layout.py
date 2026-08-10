import unittest
from pathlib import Path


STATIC_DIR = Path("listener/static")


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
        self.assertIn('id="minute-task-input"', html)
        self.assertIn('list="minute-task-list"', html)
        self.assertIn('id="minute-task-list"', html)

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

    def test_time_range_controls_replace_pagination_modes(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="start-time-filter"', html)
        self.assertIn('id="end-time-filter"', html)
        self.assertIn('type="time"', html)
        self.assertIn('step="1"', html)
        self.assertNotIn('id="page-mode"', html)
        self.assertIn('id="page-size"', html)
        self.assertIn('startTime: $("#start-time-filter")', js)
        self.assertIn('endTime: $("#end-time-filter")', js)
        self.assertIn('pageSize: $("#page-size")', js)
        self.assertIn('start_time: state.startTime', js)
        self.assertIn('end_time: state.endTime', js)
        self.assertIn(".filter-control", css)

    def test_pagination_supports_direct_page_jump(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="page-jump-input"', html)
        self.assertIn('id="page-jump-button"', html)
        self.assertIn('pageJumpInput: $("#page-jump-input")', js)
        self.assertIn('pageJumpButton: $("#page-jump-button")', js)
        self.assertIn("function jumpToPage()", js)
        self.assertIn("state.offset = (target - 1) * state.pageSize", js)

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
        self.assertIn("方向", html)
        self.assertIn("row.directions", js)

    def test_task_config_rows_support_mac_sort_and_inline_details(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="task-config-mac-sort"', html)
        self.assertIn('/static/app.js?v=serial-v2', html)
        self.assertNotIn('id="task-config-raw"', html)
        self.assertIn("task-no-response-mac", js)
        self.assertIn("task-config-inline-detail", js)
        self.assertIn("record.frames", js)
        self.assertIn("task-config-frame", js)
        self.assertIn("frame.direction", js)
        self.assertIn("item.style.setProperty", js)
        self.assertIn(".task-config-frame.downlink", css)
        self.assertIn(".task-config-frame.uplink", css)
        self.assertIn("未应答 STA", js)

    def test_global_analysis_filter_context_and_cached_page_feedback_are_present(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

        self.assertIn('id="active-filter-summary"', html)
        self.assertIn('id="active-filter-chips"', html)
        self.assertIn('id="minute-filter-scope"', html)
        self.assertIn('id="task-config-filter-scope"', html)
        self.assertIn('id="task-config-status-legend"', html)
        self.assertIn('id="page-cache-hint"', html)
        self.assertIn('id="nid-filter"', html)
        self.assertIn("updateActiveFilterSummary", js)
        self.assertIn("renderActiveFilterChips", js)
        self.assertIn("pageCache", js)
        self.assertIn("setFrameLoading", js)
        self.assertIn(".list-panel.is-loading", css)

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
