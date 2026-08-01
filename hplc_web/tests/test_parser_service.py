import json
import unittest

from hplc_web.parser_service import (
    FrameValidationError,
    ParserService,
    normalize_hex_frame,
)


class FakeDllParser:
    def parse_simple(self, frame: bytes) -> str:
        return json.dumps({"kind": "simple", "length": len(frame)})

    def parse_full(self, frame: bytes) -> str:
        return json.dumps({"kind": "full", "hex": frame.hex().upper()})

    def version(self) -> dict:
        return {"name": "fake", "version": "1.0", "date": "today"}


class NormalizeHexFrameTests(unittest.TestCase):
    def test_accepts_spaces_newlines_and_0x_prefixes(self):
        frame = normalize_hex_frame("0x7E 01\n02 0x7E")
        self.assertEqual(frame, bytes.fromhex("7E01027E"))

    def test_rejects_empty_input(self):
        with self.assertRaisesRegex(FrameValidationError, "请输入"):
            normalize_hex_frame("  ")

    def test_rejects_odd_number_of_hex_characters(self):
        with self.assertRaisesRegex(FrameValidationError, "偶数"):
            normalize_hex_frame("7E1")

    def test_rejects_non_hex_characters(self):
        with self.assertRaisesRegex(FrameValidationError, "十六进制"):
            normalize_hex_frame("7E-GG-7E")

    def test_requires_complete_frame_markers(self):
        with self.assertRaisesRegex(FrameValidationError, "7E"):
            normalize_hex_frame("010203")


class ParserServiceTests(unittest.TestCase):
    def test_returns_simple_and_full_results_for_one_frame(self):
        result = ParserService(FakeDllParser()).parse("7E 01 02 7E")

        self.assertEqual(result["frame"]["length"], 4)
        self.assertEqual(result["frame"]["normalized_hex"], "7E 01 02 7E")
        self.assertEqual(result["simple"]["kind"], "simple")
        self.assertEqual(result["full"]["kind"], "full")

    def test_preserves_dll_frame_error_when_dll_returns_legacy_non_json(self):
        class LegacyErrorDll(FakeDllParser):
            def parse_simple(self, frame: bytes) -> str:
                return "{帧错误:-3}"

            def parse_full(self, frame: bytes) -> str:
                return "{帧错误:-3}"

        result = ParserService(LegacyErrorDll()).parse("7E 01 02 7E")

        self.assertEqual(result["simple"], {"帧错误": -3})
        self.assertEqual(result["full"], {"帧错误": -3})


if __name__ == "__main__":
    unittest.main()
