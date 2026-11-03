"""minute_assert：分钟采集日志断言匹配（移植自 H_CCO/analyze_minute_logs.py）。

从 CCO 调试日志提取分钟采集断言：
- parsers：单行解析（主动上报/被动采集/任务配置/档案下发）
- statistics：逐任务逐周期统计（主动/被动上报成功率、被动采集失败明细）
- mclt_698：11E3/06H-F230 帧（1376.2 外层 + 内层 698/645）解析与
  REQS-0037 全链路自动断言

契约来源：H_CCO/analyze_minute_logs.py（安徽分钟采集日志分析）、
REQS-0037（重庆 STA 分钟采集 698 11E3 修复 + 645 回归）。
"""
from .parsers import (
    parse_active_report,
    parse_read_frame,
    parse_task_config,
    parse_full_f231_frame,
    parse_task_config_any,
    parse_f232_frame,
    has_data_region,
    classify_11e3_scene,
)
from .statistics import (
    TaskCycleStats,
    TaskStats,
    collect_task_statistics,
    format_task_statistics,
)
from .timeutil import (
    fz_minutes,
    fz_text,
    cycle_window_key,
    cycle_window_label,
)
from .mclt_698 import (
    Expect698,
    AssertReport,
    assert_698_11e3_chain,
    require_698_11e3_chain,
    parse_11e3_up,
    parse_11e3_down,
    parse_inner_698,
    parse_inner_645,
    parse_f230_report,
    parse_f231_config,
    find_inner_frame,
    split_inner_frames,
)

__all__ = [
    "parse_active_report",
    "parse_read_frame",
    "parse_task_config",
    "parse_full_f231_frame",
    "parse_task_config_any",
    "parse_f232_frame",
    "has_data_region",
    "classify_11e3_scene",
    "TaskCycleStats",
    "TaskStats",
    "collect_task_statistics",
    "format_task_statistics",
    "fz_minutes",
    "fz_text",
    "cycle_window_key",
    "cycle_window_label",
    "Expect698",
    "AssertReport",
    "assert_698_11e3_chain",
    "require_698_11e3_chain",
    "parse_11e3_up",
    "parse_11e3_down",
    "parse_inner_698",
    "parse_inner_645",
    "parse_f230_report",
    "parse_f231_config",
    "find_inner_frame",
    "split_inner_frames",
]
