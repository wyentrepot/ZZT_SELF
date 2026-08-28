"""国网协议通用解析库（解析库核心）。"""
__version__ = "0.1.0"

from .protocol_13762 import decode as decode_13762

__all__ = ["decode_13762"]
