"""GW 侦听台封装帧剥离（REQS-0024 G1 前置）。

封装格式（经 reqs/0009 真机样本 2276 帧实证，2026-09-05）::

    7E FF 02 | GW头 20B | FCH 16B | 载荷区 ... | GW尾 4B | 7E

- 载荷区 = 完整 PB 块体（GW 硬件剥离块头 1B 与 PBCS 3B），
  单块时长度 = pb_size-4（TMI4→132、TMI11/12→260、TMI0/1/7~10→516），
  多块时 = PBCnt×(pb_size-4) 连续拼接（实测 1298/1434 SOF 帧吻合）。
- SOF：载荷区 = MAC帧头(16/28B) + MSDU + ICV4 + 零填充；
  ICV = crc32(MSDU) 小端（实测 1066 帧校验通过）。
- 信标：载荷区 = 信标内容 + 零填充 + BPCS4；
  BPCS = crc32(内容+填充) 小端（实测 59/59 中央信标全对）。
- 选择确认帧无载荷区（FCH 后直接 GW 尾 4B）。
- GW 尾 4B 语义未知（非前缀 CRC，逐帧变化），原样透出。
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field

GW_PREFIX = bytes.fromhex("7EFF02")
GW_TAIL_SIZE = 4
GW_HDR_SIZE = 20
FCH_SIZE = 16

DELIM_BEACON = 0
DELIM_SOF = 1
DELIM_SACK = 2
DELIM_COORD = 3

DELIM_NAMES = {0: "信标帧", 1: "SOF帧", 2: "选择确认帧", 3: "网间协调帧"}

# TMI → PB 块体大小（=HplcPbSize-4，dll_utils.c:23-69 减块头1B+PBCS3B）
PB_BODY_BY_TMI = {
    0: 516, 1: 516, 2: 132, 3: 132, 4: 132, 5: 132, 6: 132,
    7: 516, 8: 516, 9: 516, 10: 516, 11: 260, 12: 260, 13: 68, 14: 68,
}


def le16(b: bytes, off: int) -> int:
    return b[off] | (b[off + 1] << 8)


def le24(b: bytes, off: int) -> int:
    return b[off] | (b[off + 1] << 8) | (b[off + 2] << 16)


def le32(b: bytes, off: int) -> int:
    return b[off] | (b[off + 1] << 8) | (b[off + 2] << 16) | (b[off + 3] << 24)


def get_tei(b: bytes, off: int) -> int:
    """12bit TEI，GET_TEI 语义：低 8bit 在前，高 4bit 在下一字节低半字节。"""
    return b[off] | ((b[off + 1] & 0x0F) << 8)


def tei_text(tei: int) -> str:
    return f"{tei:03X}"


def strip_gw(raw) -> bytes:
    """归一化输入为「7EFF02 之后」的纯载荷字节（含 GW 头与尾）。

    接受三种形态：①已剥离前缀的 bytes（直接校验长度）；②带 7E FF 02 前缀的
    bytes；③十六进制文本/日志行（可含空白、0x 前缀、7E 边界）。
    """
    if isinstance(raw, (bytearray, memoryview)):
        raw = bytes(raw)
    if isinstance(raw, str):
        text = re.sub(r"(0x|0X|\s+)", "", raw).upper()
        start = text.find("7EFF02")
        if start < 0:
            data = bytes.fromhex(text)  # 裸 hex 载荷（无 7E 边界）
        else:
            body = text[start + 6:]
            end = body.rfind("7E")
            if end >= 0:
                body = body[:end]
            data = bytes.fromhex(body)
    else:
        if raw.startswith(GW_PREFIX):
            data = raw[3:]
            if data.endswith(b"\x7e"):
                data = data[:-1]  # 完整帧形态：同步去尾部 7E 边界
        else:
            # 约定：bytes 输入要么带 7E FF 02 前缀，要么已是剥离后的纯载荷
            # （不全文扫描——载荷数据里可能碰巧包含 7E FF 02 字节）
            data = raw
    if len(data) < GW_HDR_SIZE + FCH_SIZE:
        raise ValueError("GW 帧长度不足")
    return data


@dataclass
class GwFrame:
    """剥离后的 GW 封装帧分层结果。"""

    gw_header: bytes            # GW 私有 20B 头（语义未知，原样保留）
    fch: bytes                  # 16B 帧控制域（表13+可变区）
    region: bytes               # 载荷区（PB 块体拼接，可能含零填充）
    gw_tail: bytes              # GW 尾 4B（语义未知）
    delimiter: int = 0          # 定界符 0信标/1 SOF/2选择确认/3网间协调
    nid: int = 0
    warnings: list = field(default_factory=list)

    @property
    def nid_hex(self) -> str:
        return f"{self.nid:06X}"


def parse_gw_frame(data: bytes) -> GwFrame:
    """剥离 GW 封装；data 为 7EFF02 之后的字节（含 GW 头与尾）。"""
    if len(data) < GW_HDR_SIZE + FCH_SIZE:
        raise ValueError("GW 帧长度不足")
    fch = data[GW_HDR_SIZE:GW_HDR_SIZE + FCH_SIZE]
    delimiter = fch[0] & 0x07
    nid = le24(fch, 1)
    rest = data[GW_HDR_SIZE + FCH_SIZE:]
    if len(rest) >= GW_TAIL_SIZE:
        region, tail = rest[:-GW_TAIL_SIZE], rest[-GW_TAIL_SIZE:]
    else:
        region, tail = b"", rest
    frame = GwFrame(
        gw_header=data[:GW_HDR_SIZE],
        fch=fch,
        region=region,
        gw_tail=tail,
        delimiter=delimiter,
        nid=nid,
    )
    if delimiter == DELIM_SACK and region:
        # 选择确认帧无载荷区；若仍有字节说明结构异常，保留原样并告警
        frame.warnings.append("选择确认帧携带意外载荷区")
    return frame


def split_gw_stream(text: str):
    """从日志行文本提取 GW 帧 payload（7EFF02 与末尾 7E 之间），供批量扫描。"""
    match = re.search(r"7E FF 02 (.+?) 7E\s*$", text.strip())
    if not match:
        return None
    try:
        return bytes.fromhex(match.group(1).replace(" ", ""))
    except ValueError:
        return None


def verify_icv(msdu: bytes, icv: bytes) -> bool:
    """ICV = crc32(MSDU) 小端（仅覆盖 MSDU，不含 MAC 帧头）。"""
    if len(icv) < 4:
        return False
    return (zlib.crc32(msdu) & 0xFFFFFFFF) == int.from_bytes(icv[:4], "little")


def verify_bpcs(content: bytes, bpcs: bytes) -> bool:
    """BPCS = crc32(信标内容+零填充) 小端。"""
    if len(bpcs) < 4:
        return False
    return (zlib.crc32(content) & 0xFFFFFFFF) == int.from_bytes(bpcs[:4], "little")
