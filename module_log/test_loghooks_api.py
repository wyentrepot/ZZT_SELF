"""loghooks_api 对照解析 API 单元测试。"""

import tempfile
import unittest
from pathlib import Path

from module_log import loghooks_api


def _write_log(path: Path, lines):
    path.write_text("\n".join(lines), encoding="utf-8")


CCO_LOG = [
    "[20260812-16:25:10:165] [RX] 15945 | info | aps_ioctrl_nwk.c (950)| onnet cnt = 0",
    "[20260812-16:25:10:166] [RX] 15946 | info | bps_check.c (123)| bpsCheck_state0 trycnt 0",
    "[20260812-16:25:18:981] [RX] 24758 | info | aps_intf.c (1425)| ONNET(3) devtype 3 IdChanged 1",
    "[20260812-16:26:52:349] [RX] 118042 | info | assoc.c (1608)| assocreq send ok",
    "[20260812-16:27:00:000] [RX] 120000 | info | nwk_bcn.c (692)| bcn crc check err",
]

STA_LOG = [
    "[20260812-16:25:04:307] [RX] 76133386 | info | nwk_nsm.c (752)| nwk disc done",
    "[20260812-16:25:04:369] [RX] 76133388 | info | nwk_assoc.c (114)| start nwk assoc",
    "[20260812-16:25:18:967] [RX] 76148072 | info | nwk_mmsg.c (457)| assoc success! tei=002, pco=001, layer=1",
    "[20260812-16:25:19:029] [RX] 76148076 | info | nwk_assoc.c (233)| nwk assoc ok",
]


class ScanLogFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_cco_dir(self):
        d = self.root / "cco"
        d.mkdir(parents=True, exist_ok=True)
        _write_log(d / "20260812-100000_[cco].log", CCO_LOG)
        return d

    def test_scan_cco_file_events_bound_to_raw_lines(self):
        d = self._make_cco_dir()
        res = loghooks_api.scan_log_file(d / "20260812-100000_[cco].log", module="cco")
        self.assertFalse(res.get("error"))
        self.assertEqual(res["module"], "cco")
        events = res["events"]

        # 应命中 onnet / onnet_again / assoc_req / bcn_crc
        types = {e["type"] for e in events}
        self.assertIn("network.onnet", types)
        self.assertIn("network.onnet_again", types)
        self.assertIn("join.assoc.req", types)
        self.assertIn("beacon.crc_err", types)

        # onnet 事件绑定到含 onnet cnt 的原始行
        onnet = next(e for e in events if e["type"] == "network.onnet")
        self.assertIn("onnet cnt = 0", onnet["raw"])
        # assoc 事件绑定到 assocreq 原始行
        assoc = next(e for e in events if e["type"] == "join.assoc.req")
        self.assertIn("assocreq send ok", assoc["raw"])

    def test_scan_sta_file_uses_sta_rules(self):
        d = self.root / "sta"
        d.mkdir(parents=True, exist_ok=True)
        _write_log(d / "20260812-100000_[sta].log", STA_LOG)
        res = loghooks_api.scan_log_file(d / "20260812-100000_[sta].log", module="sta")
        events = res["events"]
        types = {e["type"] for e in events}
        # STA 规则命中
        self.assertIn("join.disc.done", types)
        self.assertIn("join.assoc.success", types)
        self.assertIn("join.assoc.ok", types)
        # 不应命中 CCO 规则
        self.assertNotIn("network.onnet", types)
        # 绑定正确
        disc = next(e for e in events if e["type"] == "join.disc.done")
        self.assertIn("nwk disc done", disc["raw"])

    def test_scan_missing_path_returns_error(self):
        res = loghooks_api.scan_log_file(self.root / "nonexist.log", module="cco")
        self.assertIn("error", res)

    def test_scan_directory_binds_cross_files(self):
        d = self._make_cco_dir()
        res = loghooks_api.scan_log_file(d, module="cco")
        self.assertFalse(res.get("error"))
        self.assertEqual(res["total_lines"], len(CCO_LOG))
        self.assertGreaterEqual(res["event_count"], 4)

    def test_multi_file_line_key_not_colliding(self):
        """多文件目录：两个文件都有 line=0，事件必须带 file 区分，不得互相覆盖。"""
        d = self.root / "cco"
        d.mkdir(parents=True, exist_ok=True)
        _write_log(d / "a_[cco].log", CCO_LOG)          # line0 = onnet cnt = 0
        _write_log(d / "b_[cco].log", [
            "[20260812-17:00:00:000] [RX] 1 | info | aps_ioctrl_nwk.c (950)| onnet cnt = 9",
        ])  # 另一个文件的 line0 = onnet cnt = 9
        res = loghooks_api.scan_log_file(d, module="cco")
        events = res["events"]
        # 两个文件的 onnet 事件都应存在，且 file 不同
        onnet_events = [e for e in events if e["type"] == "network.onnet"]
        files = {e["file"] for e in onnet_events}
        self.assertGreaterEqual(len(files), 2, "应有两个不同文件的事件")
        # 关键：line=0 的两个事件 raw 各自来自自己的文件（不覆盖）
        line0_raws = {e["raw"] for e in onnet_events if e["line"] == 0}
        self.assertIn("onnet cnt = 0", "\n".join(line0_raws))
        self.assertIn("onnet cnt = 9", "\n".join(line0_raws))

    def test_sequence_event_bound_to_line(self):
        """跨行序列事件（如 STA 入网流程完成）应绑定到最后命中行，不被丢弃。"""
        d = self.root / "sta"
        d.mkdir(parents=True, exist_ok=True)
        # 完整 STA 入网流程：disc -> assoc_start -> track -> assoc_ok（4 步齐全）
        _write_log(d / "s_[sta].log", [
            "[20260812-16:25:04:307] [RX] 76133386 | info | nwk_nsm.c (752)| nwk disc done",
            "[20260812-16:25:04:369] [RX] 76133388 | info | nwk_assoc.c (114)| start nwk assoc",
            "[20260812-16:25:04:613] [RX] 76133673 | info | nwk_assoc.c (841)| nwk track done ind(291413927), succ",
            "[20260812-16:25:19:029] [RX] 76148076 | info | nwk_assoc.c (233)| nwk assoc ok",
        ])
        res = loghooks_api.scan_log_file(d / "s_[sta].log", module="sta")
        events = res["events"]
        types = {e["type"] for e in events}
        # 序列完成事件 join.sta.ok 应被保留（而非丢弃）
        self.assertIn("join.sta.ok", types)
        sta_ok = next(e for e in events if e["type"] == "join.sta.ok")
        # 应绑定到具体行（assoc ok 那行）
        self.assertGreaterEqual(sta_ok["line"], 0)
        self.assertIn("nwk assoc ok", sta_ok["raw"])

    def test_full_lines_returned_and_event_located(self):
        """右侧全量日志：返回所有 lines，事件能在其中定位到对应行。"""
        d = self._make_cco_dir()
        res = loghooks_api.scan_log_file(d, module="cco")
        self.assertIn("lines", res)
        # 全量行数量应等于日志总行数（单文件）
        self.assertEqual(len(res["lines"]), len(CCO_LOG))
        # 每个事件都能在 lines 中找到对应 (file, line)
        line_index = {(ln["file"], ln["line"]): ln for ln in res["lines"]}
        for ev in res["events"]:
            key = (ev["file"], ev["line"])
            self.assertIn(key, line_index, f"事件 {ev['type']} 找不到对应日志行")
            self.assertEqual(line_index[key]["raw"], ev["raw"])

    def test_crlf_no_fake_empty_lines(self):
        """真实串口日志行尾 \r\r\n 不应产生假空行（行间多换行问题）。"""
        d = self.root / "cco"
        d.mkdir(parents=True, exist_ok=True)
        # 模拟真实串口日志：行尾 \r\r\n
        raw = "AAA\r\r\nBBB\r\r\nCCC\r\n"
        (d / "crlf_[cco].log").write_text(raw, encoding="utf-8")
        lines = loghooks_api._read_file_lines(d / "crlf_[cco].log")
        # 只应有 3 行有效内容，无假空行，无 \r 残留
        self.assertEqual(lines, ["AAA", "BBB", "CCC"])
        self.assertNotIn("", lines)


class FrontendIntegrityTest(unittest.TestCase):
    """前端 module-serial.js 完整性静态检查（防止虚拟滚动等重构误删关键函数）。"""

    def test_key_functions_defined_and_called(self):
        js_path = Path(__file__).resolve().parent / "static" / "module-serial.js"
        js = js_path.read_text(encoding="utf-8")
        # 关键函数必须定义（function xxx）
        required = [
            "cmpSelectEvent", "cmpRenderEvents", "cmpRenderLog",
            "cmpVirtUpdate", "cmpScrollToLine", "cmpSelectLine",
            "cmpScan", "cmpUpdateStats", "cmpStartRealtime",
        ]
        for fn in required:
            self.assertIn(f"function {fn}(", js, f"前端缺少关键函数 {fn}()")
        # 事件点击必须绑定到 cmpSelectEvent
        self.assertIn("cmpSelectEvent(ev.__i)", js)
        # 事件跳转必须调用 cmpScrollToLine
        self.assertIn("cmpScrollToLine(ev.__key", js)


if __name__ == "__main__":
    unittest.main()