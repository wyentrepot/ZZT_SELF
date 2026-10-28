"""厂测 A5 帧的构建、解析与流式切帧。

帧格式（与 STA 固件 misc/src/factory_test.c 保持一致）::

    A5 | ADDR | CTRL | LEN | DI_lo | DI_hi | DATA (LEN-2) | CRC32 (4, little-endian)

- ``LEN`` 只统计 ``DI + DATA``，不包含 CRC；整帧长度 = ``4 + LEN + 4``。
- ``CTRL``：bit7=1 表示响应；低 4 位 0=读、1=写。
- 写响应（0x82/0x83）的 ``LEN`` 为 0，没有 DI/DATA 字段，只有 4 字节 CRC。
- CRC 为 CRC-32/MPEG-2：多项式 0x04C11DB7，初值 0，不反转输入/输出，不做最终异或。
  覆盖范围是帧头到 DATA 末尾（即 CRC 之前的全部字节）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

FRAME_HEADER = 0xA5
DEFAULT_ADDR = 0xFE
CRC32_POLY = 0x04C11DB7
# LEN 是 uint8_t：4 + LEN + 4 必须 <= 255，因此 DATA 上限 245。
MAX_DATA_LEN = 245
MIN_FRAME_LEN = 8


class FrameError(ValueError):
    """帧格式或 CRC 错误。"""


class IncompleteFrame(FrameError):
    """缓冲区里还没有完整的一帧。"""


@dataclass(frozen=True)
class Frame:
    """一条厂测 A5 帧。

    ``di`` 为 None 表示写响应（0x82/0x83），该帧没有 DI 字段。
    """

    addr: int
    ctrl: int
    di: Optional[int]
    data: bytes
    crc: int
    raw: bytes

    @property
    def is_response(self) -> bool:
        return bool(self.ctrl & 0x80)

    @property
    def is_read_request(self) -> bool:
        return not self.is_response and (self.ctrl & 0x0F) == 0

    @property
    def is_write_request(self) -> bool:
        return not self.is_response and (self.ctrl & 0x0F) == 1

    @property
    def is_write_ok(self) -> bool:
        return self.ctrl == 0x82

    @property
    def is_write_nak(self) -> bool:
        return self.ctrl == 0x83

    def to_dict(self) -> dict:
        return {
            "addr": self.addr,
            "ctrl": self.ctrl,
            "di": self.di,
            "data_hex": self.data.hex(" ").upper(),
            "crc": self.crc,
            "raw_hex": self.raw.hex(" ").upper(),
        }


def crc32_mpeg2(data: bytes) -> int:
    """计算厂测帧使用的 CRC-32/MPEG-2（init=0, refin=false, refout=false, xorout=0）。"""

    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ CRC32_POLY) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def build_frame(ctrl: int, di: int, data: bytes = b"", addr: int = DEFAULT_ADDR) -> bytes:
    """按厂测协议封装一帧请求。

    ``ctrl`` 使用 0x10/0x11（读/写请求）；``di`` 为 16 位命令 ID；
    ``data`` 为 DATA 域（可为空）。
    """

    if not 0 <= addr <= 0xFF:
        raise ValueError("addr must be 0..255")
    if not 0 <= ctrl <= 0xFF:
        raise ValueError("ctrl must be 0..255")
    if not 0 <= di <= 0xFFFF:
        raise ValueError("di must be 0..0xFFFF")
    if len(data) > MAX_DATA_LEN:
        raise ValueError(f"data too long: {len(data)} > {MAX_DATA_LEN}")

    length = 2 + len(data)
    body = bytes((FRAME_HEADER, addr, ctrl, length, di & 0xFF, (di >> 8) & 0xFF)) + bytes(data)
    crc = crc32_mpeg2(body)
    return body + crc.to_bytes(4, "little")


def build_response(
    ctrl: int,
    di: Optional[int] = None,
    data: bytes = b"",
    addr: int = DEFAULT_ADDR,
) -> bytes:
    """封装一帧响应；写响应（0x82/0x83）用 ``di=None``，LEN=0、无 DI/DATA。"""

    if not 0 <= addr <= 0xFF:
        raise ValueError("addr must be 0..255")
    if not 0 <= ctrl <= 0xFF:
        raise ValueError("ctrl must be 0..255")

    if di is None:
        if len(data) > 253:
            raise ValueError(f"data too long: {len(data)} > 253")
        length = len(data)
        body = bytes((FRAME_HEADER, addr, ctrl, length)) + bytes(data)
    else:
        if not 0 <= di <= 0xFFFF:
            raise ValueError("di must be 0..0xFFFF")
        if len(data) > MAX_DATA_LEN:
            raise ValueError(f"data too long: {len(data)} > {MAX_DATA_LEN}")
        length = 2 + len(data)
        body = bytes((FRAME_HEADER, addr, ctrl, length, di & 0xFF, (di >> 8) & 0xFF)) + bytes(data)

    crc = crc32_mpeg2(body)
    return body + crc.to_bytes(4, "little")


def parse_frame(buf: bytes) -> Frame:
    """解析缓冲区开头的第一帧；不足一帧抛 IncompleteFrame，CRC/格式错抛 FrameError。"""

    if len(buf) < MIN_FRAME_LEN:
        raise IncompleteFrame(f"need >= {MIN_FRAME_LEN} bytes, got {len(buf)}")
    if buf[0] != FRAME_HEADER:
        raise FrameError(f"bad header 0x{buf[0]:02X}, expected 0x{FRAME_HEADER:02X}")

    length = buf[3]
    total = 4 + length + 4
    if len(buf) < total:
        raise IncompleteFrame(f"need {total} bytes, got {len(buf)}")

    raw = bytes(buf[:total])
    expected = crc32_mpeg2(raw[:-4])
    actual = int.from_bytes(raw[-4:], "little")
    if expected != actual:
        raise FrameError(
            f"bad crc: got 0x{actual:08X}, expected 0x{expected:08X}"
        )

    if length >= 2:
        di: Optional[int] = raw[4] | (raw[5] << 8)
        data = raw[6:-4]
    else:
        # 写响应 0x82/0x83：LEN=0，没有 DI/DATA，只有 CRC。
        di = None
        data = b""

    return Frame(addr=raw[1], ctrl=raw[2], di=di, data=data, crc=actual, raw=raw)


class FrameStream:
    """处理串口粘包/断包/噪声的 A5 帧流式切帧器。"""

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> List[Frame]:
        self._buf.extend(data)
        frames: List[Frame] = []

        while True:
            idx = self._buf.find(FRAME_HEADER)
            if idx < 0:
                self._buf.clear()
                break
            if idx > 0:
                del self._buf[:idx]
            if len(self._buf) < 4:
                break

            total = 4 + self._buf[3] + 4
            if len(self._buf) < total:
                break

            try:
                frame = parse_frame(bytes(self._buf[:total]))
            except FrameError:
                # 假帧头或坏 CRC：丢掉当前字节，继续向后找 A5。
                del self._buf[0]
                continue

            frames.append(frame)
            del self._buf[:total]

        return frames


def parse_hex(text: str) -> bytes:
    """把 "A5 FE 10 02 ..." / "A5FE1002..." / 带 0x 前缀的文本转成 bytes。"""

    cleaned = re.sub(r"0[xX]", "", str(text))
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", cleaned)
    if len(cleaned) % 2:
        raise ValueError("hex string must contain an even number of digits")
    return bytes.fromhex(cleaned)


def to_hex(data: bytes, sep: str = " ") -> str:
    return sep.join(f"{byte:02X}" for byte in data)
