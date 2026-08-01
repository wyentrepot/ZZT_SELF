import json
import re
import threading
from typing import Protocol


class FrameValidationError(ValueError):
    pass


class DllParser(Protocol):
    def parse_simple(self, frame: bytes) -> str: ...

    def parse_full(self, frame: bytes) -> str: ...

    def version(self) -> dict: ...


def normalize_hex_frame(value: str) -> bytes:
    compact = re.sub(r"0[xX]", "", value)
    compact = re.sub(r"\s+", "", compact)
    if not compact:
        raise FrameValidationError("请输入一帧十六进制报文")
    if not re.fullmatch(r"[0-9a-fA-F]+", compact):
        raise FrameValidationError("报文只能包含十六进制字符、空格和换行")
    if len(compact) % 2:
        raise FrameValidationError("十六进制字符数量必须是偶数")

    frame = bytes.fromhex(compact)
    if len(frame) < 2 or frame[0] != 0x7E or frame[-1] != 0x7E:
        raise FrameValidationError("完整帧必须以 7E 开始并以 7E 结束")
    return frame


class ParserService:
    def __init__(self, parser: DllParser):
        self.parser = parser
        self._parser_lock = threading.Lock()

    def parse_summary(self, value: str) -> dict:
        frame = normalize_hex_frame(value)
        with self._parser_lock:
            simple = self.parser.parse_simple(frame)
        return {
            "frame": {
                "length": len(frame),
                "normalized_hex": " ".join(f"{byte:02X}" for byte in frame),
            },
            "simple": self._decode_result(simple),
        }

    def parse(self, value: str) -> dict:
        frame = normalize_hex_frame(value)
        with self._parser_lock:
            simple = self.parser.parse_simple(frame)
            full = self.parser.parse_full(frame)
        return {
            "frame": {
                "length": len(frame),
                "normalized_hex": " ".join(f"{byte:02X}" for byte in frame),
            },
            "simple": self._decode_result(simple),
            "full": self._decode_result(full),
        }

    def version(self) -> dict:
        with self._parser_lock:
            return self.parser.version()

    @staticmethod
    def _decode_result(value: str) -> dict:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            legacy_error = re.fullmatch(
                r"\{(?:帧错误|甯ч敊璇\?):?(-?\d+)\}", value.strip()
            )
            if legacy_error:
                return {"帧错误": int(legacy_error.group(1))}
            raise
