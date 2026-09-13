# -*- coding: utf-8 -*-
"""REQS-0030：并发帧识别 + 周期统计单测（构帧为主，不依赖真实日志）。"""
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sim_concentrator.concurrent_match import is_concurrent_frame, parse_data_unit
from sim_concentrator.period_stats import aggregate, parse_period


class TestConcurrentMatch(unittest.TestCase):
    def test_afn_fn_variants(self):
        for afn, fn in [("F1", "F1"), ("0xF1", "f1"), ("241", "241"), ("F1H", "F1H")]:
            self.assertTrue(is_concurrent_frame(afn, fn), (afn, fn))
        for afn, fn in [("10", "F1"), ("F1", "F2"), (None, "F1"), ("11", "F1")]:
            self.assertFalse(is_concurrent_frame(afn, fn), (afn, fn))

    def test_down_unit_has_reserved(self):
        # 下行：规约类型=02(645-2007) 保留=00 长度=0x0004 内容4字节
        r = parse_data_unit("down", "02 00 00 04 AA BB CC DD")
        self.assertEqual(r["proto_type"], 0x02)
        self.assertEqual(r["proto_name"], "DL/T 645-2007")
        self.assertEqual(r["length"], 4)
        self.assertFalse(r["failed"])

    def test_up_unit_no_reserved_and_fail_L0(self):
        # 上行无保留字节：规约类型=03 长度=0x0002
        r = parse_data_unit("up", "03 00 02 AA BB")
        self.assertEqual(r["length"], 2)
        self.assertFalse(r["failed"])
        # 失败：长度域=0 → failed=True（表地址由链路层 A1 补充）
        r0 = parse_data_unit("up", "02 00 00")
        self.assertTrue(r0["failed"])

    def test_short_unit(self):
        r = parse_data_unit("down", "02")
        self.assertIn("error", r)


class TestPeriodStats(unittest.TestCase):
    BASE = 1788338400.0  # 任意锚点

    def test_parse_period(self):
        self.assertEqual(parse_period("15m"), 900)
        self.assertEqual(parse_period("30s"), 30)
        self.assertEqual(parse_period("1h"), 3600)
        self.assertEqual(parse_period("900"), 900)
        self.assertEqual(parse_period(None), 900)
        self.assertEqual(parse_period("abc"), 900)

    def _mk(self, meter, start_off, dur, status="success"):
        a = {"meter": meter, "start_epoch": self.BASE + start_off,
             "end_epoch": None if dur is None else self.BASE + start_off + dur,
             "status": status}
        return a

    def test_basic_metrics(self):
        atts = [
            self._mk("m1", 0, 10),
            self._mk("m2", 0, 20, "timeout"),
            self._mk("m3", 5, 15),
        ]
        r = aggregate(atts, period="15m")
        self.assertEqual(r["period_seconds"], 900)
        b = r["buckets"][0]
        self.assertEqual(b["dispatch_count"], 3)
        self.assertEqual(b["success_count"], 2)
        self.assertEqual(b["failed_count"], 1)
        self.assertEqual(b["max_concurrent"], 3)          # 0~5s 三块同时在飞
        self.assertEqual(b["avg_duration_ms"], 15000)     # (10+20+15)/3
        self.assertEqual(b["duplicate_count"], 0)

    def test_duplicate_dispatch(self):
        atts = [
            self._mk("m1", 0, 100),      # 0~100 在飞
            self._mk("m1", 50, 10),      # 未结清再发 → 重复
            self._mk("m1", 200, 10),     # 已结清后再发 → 不算
        ]
        r = aggregate(atts, period=900)
        self.assertEqual(r["buckets"][0]["duplicate_count"], 1)

    def test_two_periods_and_peak(self):
        atts = [
            self._mk("m1", 0, 30),
            self._mk("m2", 10, 30),
            self._mk("m3", 950, 30),     # 第二个 15min 桶
        ]
        r = aggregate(atts, period=900)
        self.assertEqual(len(r["buckets"]), 2)
        self.assertEqual(r["buckets"][0]["max_concurrent"], 2)
        self.assertEqual(r["buckets"][1]["dispatch_count"], 1)

    def test_unsettled_excluded_from_avg(self):
        atts = [self._mk("m1", 0, None), self._mk("m2", 0, 10)]
        r = aggregate(atts, period=900)
        self.assertEqual(r["buckets"][0]["avg_duration_ms"], 10000)
        self.assertEqual(r["buckets"][0]["max_concurrent"], 2)

    def test_bucket_start_iso(self):
        r = aggregate([self._mk("m1", 0, 1)], period=900)
        b = r["buckets"][0]
        datetime.fromisoformat(b["period_start"])  # 可解析


if __name__ == "__main__":
    unittest.main()
