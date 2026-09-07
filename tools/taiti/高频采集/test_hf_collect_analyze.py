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



class TestTaishRetryMetrics:
    def test_cycle_wait_retry_and_topology_are_correlated(self, tmp_path):
        """并发日志只能给出全局 F101；拓扑缺失地址须能与未成功地址对齐。"""
        log = tmp_path / "taish_retry_metrics.log"
        missing = "170000011101"
        ok = "020000011101"

        def send(ts, addr, seq):
            return (
                f"{ts} MTC@admin-PC: \"send cmd to cco:"
                f"'685100430400000000{seq:02X}630198900000{addr}F1010003003200681700'\""
            )

        lines = [
            send("2026-09-02 15:00:00:000", missing, 1),
            send("2026-09-02 15:00:00:010", ok, 2),
            "2026-09-02 15:00:00:100 MTC@admin-PC: \"Recieved F101\"",
            "2026-09-02 15:00:00:116 MTC@admin-PC: \"restart timer ti_wait_new_report....,cycle :0\"",
            "2026-09-02 15:00:00:200 MTC@admin-PC: \"More than maximum allowable number of 376.2 (109)\"",
            "2026-09-02 15:00:00:210 MTC@admin-PC: \"Meter reading busy (111)\"",
            send("2026-09-02 15:00:01:000", missing, 3),
            send("2026-09-02 15:00:01:010", ok, 4),
            "2026-09-02 15:00:01:015 MTC@admin-PC: \"ReadMeter Success, mac addr:'020000011101'\"",
            send("2026-09-02 15:00:02:000", missing, 5),
            "2026-09-02 15:00:05:061 MTC@admin-PC: \"Total read time in middle:5.061000\"",
            "2026-09-02 15:00:05:062 MTC@admin-PC: \"restart timer ti_wait_new_report....,cycle ..:1\"",
            "2026-09-02 15:00:05:062 MTC@admin-PC: \"not in net mac { '01'O, '10'O, '01'O, '00'O, '00'O, '04'O }\"",
            "2026-09-02 15:00:05:063 MTC@admin-PC: \"not in net mac { '01'O, '11'O, '01'O, '00'O, '00'O, '17'O }\"",
            "2026-09-02 15:00:05:064 MTC@admin-PC: \"successRate=0.993103, NeedsuccessRate=0.980000\"",
            "2026-09-02 15:00:05:065 MTC@admin-PC: \"read cycle reach max(3)\"",
            "2026-09-02 15:00:05:066 MTC@admin-PC: \"First duration:2.000000\"",
            "2026-09-02 15:00:05:067 MTC@admin-PC: \"Second duration:3.000000\"",
            "2026-09-02 15:00:05:068 MTC@admin-PC: \"High Frequence RM fail, time consumed: 5.000000\"",
        ]
        log.write_text("\n".join(lines), encoding="gbk")

        res = analyze_taish_log(log)

        assert res.phase_count == 2
        assert res.f101_received == 1
        assert res.busy_counts["busy"] == 2
        assert res.busy_code_counts == {"109": 1, "111": 1}
        assert res.extra_resend_total == 1
        assert res.extra_resend_counts[missing] == 1
        assert res.extra_resend_counts[ok] == 0
        assert res.topology_missing_addrs == {missing}
        assert res.topology_never_ok == [missing]
        assert [(event.kind, event.cycle) for event in res.cycle_events] == [
            ("RESET", 0), ("ADVANCE", 1)
        ]
        assert res.cycle_max_values == [3]
        assert len(res.idle_waits) == 1
        assert res.idle_waits[0].seconds == pytest.approx(4.046)
        assert res.last_success_rate is not None
        assert res.last_success_rate[1] == pytest.approx(0.993103)
        assert res.total_duration == pytest.approx(5.0)

        report = format_report(res)
        for text in (
            "Recieved F101 应答（仅全局计数）: 1",
            "109=1 111=1",
            "额外补发: 1 次",
            "最终未入网 MAC: 170000011101",
            "未入网且无 Success: 170000011101",
            "cycle 事件: RESET=1，ADVANCE=1",
            "4.046 秒",
        ):
            assert text in report
