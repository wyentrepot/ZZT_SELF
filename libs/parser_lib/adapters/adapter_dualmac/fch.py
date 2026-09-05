"""FCH 帧控制域解析（双模 4-2 表13 + 可变区 表17/19/23/26/27/30/34）。

位域出处：CCO 工程蒸馏库 template/protocol/dll/src/frame/mpdu_frameControl.c
（与参考工程逐字节一致的快照），并经 reqs/0009 真机样本回归核验。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .gw import DELIM_NAMES, le16, le24, le32, get_tei

# 可变区字段名（按定界符）用于通用渲染
TMI_PB = {0: 516, 1: 516, 2: 132, 3: 132, 4: 132, 5: 132, 6: 132,
          7: 516, 8: 516, 9: 516, 10: 516, 11: 260, 12: 260, 13: 68, 14: 68}
RF_PB = {0: 16, 1: 40, 2: 72, 3: 136, 4: 264, 5: 520}
MCS_NAMES = {
    0: "BPSK 1/2 分集4", 1: "BPSK 1/2 分集2", 2: "QPSK 1/2 分集2",
    3: "QPSK 1/2", 4: "QPSK 4/5", 5: "16QAM 1/2", 6: "16QAM 4/5",
}
PHASE_NAMES = {0: "未知", 1: "A相", 2: "B相", 3: "C相"}


@dataclass
class Fch:
    """16B 帧控制域。variable 按定界符给出对应可变区字段 dict。"""

    delimiter: int = 0
    nwk_type: int = 0
    nid: int = 0
    version: int = 0
    fccs: int = 0
    variable: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def delimiter_name(self) -> str:
        return DELIM_NAMES.get(self.delimiter, f"保留({self.delimiter})")

    @property
    def nid_hex(self) -> str:
        return f"{self.nid:06X}"


def _tei_pair_hi_lo(b: bytes, lo_off: int, hi_off: int) -> int:
    """低 4bit 在 lo 字节高半字节、高 8bit 在 hi 字节（表19/23/30/34 TEId 布局）。"""
    return ((b[lo_off] >> 4) & 0x0F) | (b[hi_off] << 4)


def parse_fch(f: bytes) -> Fch:
    if len(f) < 16:
        raise ValueError("FCH 不足 16 字节")
    out = Fch(
        delimiter=f[0] & 0x07,
        nwk_type=(f[0] >> 3) & 0x1F,
        nid=le24(f, 1),
        version=(f[12] >> 4) & 0x0F,
        fccs=le24(f, 13),
    )
    if out.nwk_type:
        out.warnings.append(f"网络类型非 0（{out.nwk_type}），按保留值处理")
    try:
        if out.delimiter == 1:
            out.variable = _var_sof(f)
        elif out.delimiter == 2:
            out.variable = _var_sack(f)
        elif out.delimiter == 0:
            out.variable = _var_beacon(f)
        elif out.delimiter == 3:
            out.variable = _var_coord(f)
    except IndexError:
        out.warnings.append("可变区解析越界，已尽力解析")
    return out


def _var_sof(f: bytes) -> dict:
    # 表19 载波 SOF / 表30 无线 SOF 共用位域，帧长单位不同（10us / 100us）
    return {
        "teis": get_tei(f, 4),
        "teid": _tei_pair_hi_lo(f, 5, 6),
        "lid": f[7],
        "frame_time_10us": get_tei(f, 8),
        "pbcnt": (f[9] >> 4) & 0x0F,
        "symbols": f[10] + (f[11] & 1) * 256,
        "broadcast": bool((f[11] >> 1) & 1),
        "retrans": bool((f[11] >> 2) & 1),
        "encrypt": bool((f[11] >> 3) & 1),
        "base_tmi": (f[11] >> 4) & 0x0F,
        "expand_tmi": f[12] & 0x0F,
    }


def _var_sack(f: bytes) -> dict:
    # 表23 载波选择确认（TEIs/TEId 互换：TEIs=对端 TEId）
    return {
        "recv_result": f[4] & 0x0F,
        "recv_state": (f[4] >> 4) & 0x0F,
        "teis": get_tei(f, 5),
        "teid": _tei_pair_hi_lo(f, 6, 7),
        "pbcnt": f[8] & 0x07,
        "lqi": f[9],
        "sta_load": f[10],
        "expand_frame_type": f[12] & 0x0F,
    }


def _var_beacon(f: bytes) -> dict:
    # 表17 载波信标 / 表27 无线信标；NTB=LE32@4
    return {
        "ntb": le32(f, 4),
        "teis": get_tei(f, 8),
        "base_tmi": (f[9] >> 4) & 0x0F,
        "mcs": (f[9] >> 4) & 0x0F,
        "symbols": f[10] + (f[11] & 1) * 256,
        "phase": (f[11] >> 1) & 0x03,
        "pbsz_index": f[10] & 0x0F,
    }


def _var_coord(f: bytes) -> dict:
    # 表26 网间协调帧
    return {
        "duration_ms": le16(f, 4),
        "offset_ms": le16(f, 6),
        "neighbor_nid": le24(f, 8),
        "rf_chnl": f[11],
        "rf_option": f[12] & 0x03,
    }
