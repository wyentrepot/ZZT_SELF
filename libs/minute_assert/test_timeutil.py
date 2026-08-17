"""timeutil：分钟采集冻结时刻 BCD/时间辅助 契约测试（移植自 H_CCO/analyze_minute_logs.py）。"""
import pytest

from minute_assert.timeutil import (
    bcd,
    fz_minutes,
    fz_text,
    cycle_window_key,
    cycle_window_label,
    period_range,
)


class TestBcd:
    def test_bcd_basic(self):
        assert bcd(0x46) == 46
        assert bcd(0x08) == 8

    def test_bcd_high_nibble(self):
        assert bcd(0x31) == 31


class TestFzMinutes:
    def test_known_freeze_time(self):
        # fz = 00 46 08 31 07 26 → 2026-07-31 08:46
        fz = bytes.fromhex("004608310726")
        # 与原始脚本 _fz_minutes 一致（实测 = 29758126）
        assert fz_minutes(fz) == 29758126

    def test_fz_text(self):
        fz = bytes.fromhex("004608310726")
        assert fz_text(fz) == "08:46"


class TestCycleWindow:
    def test_window_key_aligns_to_period(self):
        fz = bytes.fromhex("004608310726")  # 08:46
        assert cycle_window_key(fz, 2) == 29758126 // 2 * 2

    def test_window_label(self):
        # 08:46 起 2 分钟窗口 → 08:46-08:48
        fz = bytes.fromhex("004608310726")
        start = cycle_window_key(fz, 2)
        assert cycle_window_label(start, 2) == "08:46-08:48"

    def test_period_range_includes_all_windows(self):
        first = 29758126  # 08:46
        last = 29758128  # 08:48
        windows = list(period_range(first, last, 2))
        assert len(windows) == 2
        assert windows[0] == 29758126
        assert windows[1] == 29758128
