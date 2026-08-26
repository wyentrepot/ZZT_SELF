# -*- coding: utf-8 -*-
"""tools/taiti/高频采集 高频采集分析工具单测。

用各子目录 samples/ 的精简样例（真实日志片段，GBK/UTF-8 编码保留）验证分析函数
能产出关键结论：失败表、最终判定、二次证据命中。测试不依赖外部文件，不连网。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让测试可 import 待测模块（高频采集 根 + 三子目录）
_HF = Path(__file__).resolve().parent
for _p in (str(_HF), str(_HF / "台体"), str(_HF / "CCO"), str(_HF / "侦听台")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from CCO.analyze_cco import analyze_cco_log  # noqa: E402
from run import run_cross  # noqa: E402
from 台体.analyze_taish import analyze_taish_log, format_report  # noqa: E402
from 侦听台.analyze_sniff import analyze_sniff_log  # noqa: E402

TAISH_SAMPLE = _HF / "台体" / "samples" / "台体日志_精简.log"
CCO_SAMPLE = _HF / "CCO" / "samples" / "cco日志_精简.log"
SNIFF_SAMPLE = _HF / "侦听台" / "samples" / "侦听台报文_精简.txt"


class TestTaish:
    def test_samples_exist(self):
        assert TAISH_SAMPLE.exists() and TAISH_SAMPLE.stat().st_size > 0

    def test_analyze_produces_ok_set(self):
        res = analyze_taish_log(TAISH_SAMPLE)
        assert len(res.ok_set) >= 90  # 样例中 92 只表 Success
        assert "010000012201" in res.ok_set or "020000012201" in res.ok_set or len(res.ok_set) > 0

    def test_profile_blocks_extracted(self):
        res = analyze_taish_log(TAISH_SAMPLE)
        assert len(res.profile_blocks) > 0
        # 档案表总数应覆盖失败表所在段
        assert len(res.profile_addresses) > 100

    def test_final_verdict_read_fail(self):
        res = analyze_taish_log(TAISH_SAMPLE)
        assert "read fail" in res.final_verdict.lower() or "执行结果" in res.final_verdict

    def test_format_report_includes_key_sections(self):
        res = analyze_taish_log(TAISH_SAMPLE)
        report = format_report(res)
        for key in ("台体高频采集日志分析", "最终判定", "补抄次数分布"):
            assert key in report

    def test_never_ok_property(self):
        res = analyze_taish_log(TAISH_SAMPLE)
        # 样例只保留 6 条采集帧，且都在 16:42 后；这些地址应出现在 never_ok 或 ok_set
        for addr in res.never_ok:
            assert addr not in res.ok_set

    def test_over_retry_detects_failed_tables(self, tmp_path):
        """核心结论：send>=3 且从未 Success 的表 = 失败表候选（over_retry）。

        真实样例每表 send=1 不触发该分支，故用合成小日志显式覆盖。
        采集帧须含 12 位 hex 地址：630198900000<12hex>F101。
        """
        log = tmp_path / "taish_retry.log"
        lines = []
        # 表 A（000000012201）被抄 4 次从未成功 -> 失败表候选
        for i in range(4):
            lines.append(
                f"2026-08-20 16:42:37:{i:03d} MTC@admin-PC: \"send cmd to cco:"
                f"'6851004304000000000{i}630198900000000000012201F1010003003200681700'\""
            )
        # 表 B（000000012202）被抄 3 次后 ReadMeter Success -> 不应进 over_retry
        for i in range(3):
            lines.append(
                f"2026-08-20 16:43:10:{i:03d} MTC@admin-PC: \"send cmd to cco:"
                f"'6851004304000000000{i}630198900000000000012202F1010003003200681700'\""
            )
        lines.append(
            "2026-08-20 16:43:20:000 MTC@admin-PC: \"ReadMeter Success, mac addr:'000000012202'\""
        )
        lines.append('2026-08-20 16:44:00:000 MTC@admin-PC: "read fail(4)"')
        log.write_text("\n".join(lines), encoding="gbk")

        res = analyze_taish_log(log)
        # 表 000000012201 被抄 4 次从未成功
        assert res.send_counts.get("000000012201", 0) == 4
        assert "000000012201" in res.over_retry
        assert "000000012201" in res.never_ok
        # 表 000000012202 虽被抄 3 次但成功，不进 over_retry
        assert res.send_counts.get("000000012202", 0) == 3
        assert "000000012202" not in res.over_retry
        assert "000000012202" not in res.never_ok


class TestCross:
    def test_samples_exist(self):
        assert CCO_SAMPLE.exists() and SNIFF_SAMPLE.exists()

    def test_cco_hits_target_table(self):
        text = analyze_cco_log(CCO_SAMPLE, ["010000012201"], "16:42:00", "16:44:50")
        assert "010000012201" in text
        assert "CCO 命中" in text

    def test_sniff_hits_target_table(self):
        text = analyze_sniff_log(SNIFF_SAMPLE, ["020000012201"], "16:42:00", "16:44:50")
        assert "侦听台命中帧数" in text
        assert "020000012201" in text or "012201000002" in text

    def test_run_cross_combined(self):
        text = run_cross(CCO_SAMPLE, SNIFF_SAMPLE,
                         ["010000012201", "020000012201"],
                         "16:42:00", "16:44:50")
        assert "CCO 日志二次证据" in text
        assert "侦听台 HPLC 报文二次证据" in text

    def test_cco_only_flag(self):
        text = run_cross(CCO_SAMPLE, SNIFF_SAMPLE, ["010000012201"],
                         cco_only=True)
        assert "侦听台 HPLC 报文二次证据" not in text
        assert "CCO 日志二次证据" in text

    def test_sniff_only_flag(self):
        text = run_cross(CCO_SAMPLE, SNIFF_SAMPLE, ["010000012201"],
                         sniff_only=True)
        assert "CCO 日志二次证据" not in text
        assert "侦听台 HPLC 报文二次证据" in text


class TestCli:
    def test_main_taish(self, capsys):
        import run as cli
        rc = cli.main(["taish", str(TAISH_SAMPLE)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "台体高频采集日志分析" in out

    def test_main_unknown_command(self, capsys):
        import run as cli
        rc = cli.main(["no-such-cmd"])
        assert rc == 2

    def test_main_help(self, capsys):
        import run as cli
        rc = cli.main(["--help"])
        assert rc == 0
        assert "taish" in capsys.readouterr().out

    def test_main_cco_rejects_missing_args(self, capsys):
        import run as cli
        rc = cli.main(["cco"])
        assert rc == 2

    def test_main_sniff(self, capsys):
        import run as cli
        rc = cli.main(["sniff", str(SNIFF_SAMPLE), "020000012201",
                       "--start", "16:42:00", "--end", "16:44:50"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "侦听台 HPLC 报文二次证据" in out

    def test_main_dangling_start_rejected(self, capsys):
        import run as cli
        rc = cli.main(["cco", str(CCO_SAMPLE), "010000012201", "--start"])
        assert rc == 2
        assert "参数错误" in capsys.readouterr().err
