import unittest
import json
import re
from pathlib import Path

from hplc_web.app import DEFAULT_DLL
from hplc_web.dotnet_parser import DotNetHplcParser
from hplc_web.tests.fixtures import GW_FRAME_HEX


class DotNetHplcParserIntegrationTests(unittest.TestCase):
    def test_web_app_defaults_to_compiled_gw_dll(self):
        expected = Path("dll/bin/Debug/GwHPLCAnalysis.dll").resolve()
        self.assertEqual(DEFAULT_DLL, expected)

    def test_loads_compiled_gw_dll_and_reads_current_version(self):
        dll_path = Path("dll/bin/Debug/GwHPLCAnalysis.dll").resolve()
        parser = DotNetHplcParser(dll_path)

        version = parser.version()

        self.assertEqual(version["name"], "GW_SMAnalysis")
        self.assertEqual(version["version"], "V1.0.23")
        self.assertTrue(version["date"])

    def test_parses_ff02_gw_sniffer_frame(self):
        parser = DotNetHplcParser(
            Path("dll/bin/Debug/GwHPLCAnalysis.dll").resolve()
        )
        frame = bytes.fromhex(GW_FRAME_HEX)

        result = json.loads(parser.parse_simple(frame))

        self.assertNotEqual(result["FrmType"], "ERROR")
        self.assertIsNotNone(result["Info2"])
        self.assertEqual(result["Info2"]["ProType"], "GW")

    def test_returns_partial_full_result_for_short_gw_physical_block(self):
        parser = DotNetHplcParser(
            Path("dll/bin/Debug/GwHPLCAnalysis.dll").resolve()
        )
        frame = bytes.fromhex(GW_FRAME_HEX)

        result = json.loads(parser.parse_full(frame))

        self.assertEqual(result["Info2"]["ProType"], "GW")
        self.assertIsNotNone(result["FCH"])
        self.assertIsNotNone(result["MPDU"])
        self.assertIn("物理块长度不足", result["Error"])

    def _first_e4_frame(self) -> bytes:
        lines = Path("测试文件/测试文本.txt").read_text(encoding="utf-8").splitlines()
        for line in lines:
            match = re.search(r"](7E(?: [0-9A-Fa-f]{2})+)", line)
            if not match:
                continue
            if "11 E4" in (" " + match.group(1) + " ").upper():
                return bytes.fromhex(match.group(1))
        self.fail("测试文件/测试文本.txt 中未找到第一帧 0x00E4 报文")

    def test_exposes_bounded_e4_application_payload(self):
        parser = DotNetHplcParser(
            Path("dll/bin/Debug/GwHPLCAnalysis.dll").resolve()
        )
        frame = self._first_e4_frame()

        result = json.loads(parser.parse_simple(frame))

        self.assertEqual(result["APP_PORT"], "11")
        self.assertEqual(result["APP_ID"], "00E4")
        self.assertTrue(result["APP_RAW"].startswith("11E400000132"))
        self.assertEqual(len(bytes.fromhex(result["APP_RAW"])), 106)

    def test_parses_gw_carrier_frame_after_gw_beacon(self):
        parser = DotNetHplcParser(
            Path("dll/bin/Debug/GwHPLCAnalysis.dll").resolve()
        )
        lines = Path("hplc_web/tests/data/gw_log_sample.txt").read_text(
            encoding="utf-8"
        ).splitlines()

        def frame_at(line_number: int) -> bytes:
            match = re.search(r"](7E(?: [0-9A-Fa-f]{2})+)", lines[line_number - 1])
            self.assertIsNotNone(match)
            return bytes.fromhex(match.group(1))

        beacon = json.loads(parser.parse_simple(frame_at(25)))
        carrier_frame = json.loads(parser.parse_simple(frame_at(42)))

        self.assertEqual(beacon["FrmType"], "中央信标")
        self.assertNotEqual(carrier_frame["FrmType"], "ERROR")


if __name__ == "__main__":
    unittest.main()
