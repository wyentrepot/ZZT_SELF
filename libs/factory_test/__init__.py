"""厂测 A5 帧编解码与模拟上位机客户端。"""

__version__ = "0.1.0"

from .codec import (  # noqa: F401
    DEFAULT_ADDR,
    FRAME_HEADER,
    Frame,
    FrameError,
    FrameStream,
    IncompleteFrame,
    build_frame,
    build_response,
    crc32_mpeg2,
    parse_frame,
    parse_hex,
    to_hex,
)

__all__ = [
    "DEFAULT_ADDR",
    "FRAME_HEADER",
    "Frame",
    "FrameError",
    "FrameStream",
    "IncompleteFrame",
    "build_frame",
    "build_response",
    "crc32_mpeg2",
    "parse_frame",
    "parse_hex",
    "to_hex",
]
