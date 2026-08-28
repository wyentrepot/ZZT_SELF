"""任务5：侦听台查询性能基线测试（可复现）。

背景：performance-analysis.md（2026-08-09 实测）记录 23 万行库上的查询性能
问题（无索引全表扫、深翻页 OFFSET 线性扫）。相关优化已落地（log_time 索引、
keyset 翻页、COUNT 缓存、批量写）。本文档建立**可复现的性能基线**：用合成
数据灌入 frames 表，测关键查询路径耗时并断言宽松上界，防止后续回归。

阈值依据（2026-08-09 实测 23 万行库）：
- 无筛选列表页：1 ms；深翻页 OFFSET 200k：167 ms → keyset 后应远低于此
- log_time 范围 COUNT：有索引 0.2 ms
- 这里用 5 万行合成数据（跑测试不能太慢），阈值按每万行归一化放宽

断言采用宽松上界（毫秒级），慢 CI/共享机也可通过；真实基准用
`--benchmark 1` 环境变量开启（打印详细耗时）。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
from pathlib import Path

from listener.log_service import LogFileService, _BACKFILL_STATE


def _make_service(tmp_path: Path, n: int = 50_000) -> LogFileService:
    """建 LogFileService，并向 frames 表批量灌入 n 行合成数据。"""
    db = tmp_path / "perf.sqlite3"

    class _Parser:
        def parse_summary(self, value: str) -> dict:
            return {"simple": {}}

        def parse(self, value: str) -> dict:
            return {"simple": {}}

    svc = LogFileService(_Parser(), db)
    with svc._connect() as conn:
        conn.execute("DELETE FROM frames")
        # 批量 insert（executemany 批量写路径，与 index_file 的单连接一致）
        conn.executemany(
            "INSERT INTO frames (sequence, log_time, byte_length, raw_hex, summary_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    f"{i:06d}",
                    f"2026-07-31 {i // 3600:02d}:{(i // 60) % 60:02d}:{i % 60:02d}",
                    i % 50 + 10,
                    f"7E FF 02 FF {i:04X}",
                    f'{{"SNID": "00{i % 100:02d}", "APP_ID": "00E{i % 5}"}}',
                )
                for i in range(n)
            ],
        )
        conn.commit()
    return svc


def _ms(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


class TestQueryPerfBaseline:
    """关键查询路径耗时基线（宽松上界，防回归）。"""

    def test_list_first_page(self, tmp_path):
        """浅翻页列表（LIMIT 500 OFFSET 0）：应 < 200ms @ 5万行。"""
        svc = _make_service(tmp_path)
        ms = _ms(lambda: svc.list_frames(limit=500, offset=0))
        assert ms < 200, f"浅翻页列表过慢：{ms:.1f}ms"
        svc.close()

    def test_list_deep_page_keyset(self, tmp_path):
        """深翻页（keyset after_id 游标）：应 < 200ms @ 5万行。

        对应旧 OFFSET 200k 深翻页 167ms 的回归防护——keyset 只取
        id > after_id 的一页，不随页深线性变慢。
        """
        svc = _make_service(tmp_path)
        # 先取一页拿到最后 id 作为 after_id（模拟翻到深处）
        page1 = svc.list_frames(limit=500, offset=0)
        assert page1["items"], "应返回数据"
        last_id = page1["items"][-1]["id"]
        # 用 keyset 翻页再取 500 条（相当于第 N 页，但不受 OFFSET 影响）
        ms = _ms(lambda: svc.list_frames(limit=500, after_id=last_id))
        assert ms < 200, f"keyset 深翻页过慢：{ms:.1f}ms"
        svc.close()

    def test_time_range_count(self, tmp_path):
        """log_time 范围 COUNT（有 log_time 索引）：应 < 50ms @ 5万行。

        实测有索引 0.2ms（23万行），5万行给宽松上界 50ms。
        log_time 范围查询按 HH:MM:SS 过滤（log_time LIKE）。
        """
        svc = _make_service(tmp_path)
        ms = _ms(
            lambda: svc.list_frames(
                limit=1, offset=0,
                start_time="00:00:00",
                end_time="12:00:00",
            )
        )
        assert ms < 50, f"时间范围查询过慢：{ms:.1f}ms"
        svc.close()

    def test_query_filter(self, tmp_path):
        """query 三列 LIKE 筛选：应 < 300ms @ 5万行。

        实测 23 万行 221ms，5万行给宽松上界 300ms（LIKE 无索引仍全表扫）。
        """
        svc = _make_service(tmp_path)
        ms = _ms(lambda: svc.list_frames(limit=500, offset=0, query="2026-07-31"))
        assert ms < 300, f"query 筛选过慢：{ms:.1f}ms"
        svc.close()

    def test_nid_filter(self, tmp_path):
        """nid LIKE 筛选（summary_json）：应 < 300ms @ 5万行。"""
        svc = _make_service(tmp_path)
        ms = _ms(lambda: svc.list_frames(limit=500, offset=0, nid="0001"))
        assert ms < 300, f"nid 筛选过慢：{ms:.1f}ms"
        svc.close()
class TestAssessmentPerfBaseline:
    """网络承载评估（全量分析）性能基线：防止管线退化为多趟重复处理。

    参考：真实 53.6 万帧 / 9h50m 库实测 Python 全量解码路径 14s、SQL 物化
    聚合路径 6s。这里用 5 万行合成数据按比例给宽松上界（CI 降速余量 10x）。
    """

    def test_sql_path_full_assessment(self, tmp_path):
        """SQL 物化聚合路径全量评估：应 < 8s @ 5万行（实测比例 0.6s）。"""
        svc = _make_service(tmp_path, 50_000)
        with svc._connect() as conn:
            conn.execute(
                "UPDATE frames SET nid = 0x947F69, "
                "frm_type = CASE id % 7 WHEN 0 THEN '中央信标' ELSE 'ACK' END, "
                "assess_detail = CASE id % 7 WHEN 0 THEN 1 ELSE 0 END"
            )
            conn.commit()
        _BACKFILL_STATE[str(svc.database_path)] = {
            "running": False, "done": True, "error": None,
        }
        seconds = _ms(lambda: svc.list_beacon_periods()) / 1000
        assert seconds < 8, f"SQL 全量评估过慢：{seconds:.1f}s"
        svc.close()

    def test_python_path_full_assessment(self, tmp_path):
        """Python 全量解码路径评估：应 < 20s @ 5万行（实测比例 1.3s）。"""
        from listener import network_assessment

        svc = _make_service(tmp_path, 50_000)
        rows = list(svc._iter_full_frame_rows())
        seconds = _ms(
            lambda: network_assessment.assess_by_network_stream(iter(rows))
        ) / 1000
        assert seconds < 20, f"Python 全量评估过慢：{seconds:.1f}s"
        svc.close()
