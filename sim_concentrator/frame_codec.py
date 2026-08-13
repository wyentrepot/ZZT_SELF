"""帧编解码层：统一封装 adapter_10376 的构帧与解析。

对外提供三个能力：
- build_13762_frame():  构造 1376.2 帧字节（自动算 L 与 CS）。
- extract_frame():      从串口字节流中切出首帧（半包返回 None）。
- decode_frame():       解析 1376.2 帧 → 结构化 dict（信封字段 + 嵌套 645/698）。
- frame_to_hex():       字节帧 → 空格分隔 hex 字符串（落盘/展示用）。

数据来源约定（DECISIONS.md ADR-10）：一律走 Q/GDW 10376.2 信封结构，
即 68 | L(2B) | 68 | AFN | SEQ | RTUA(6) | MSAA | PW(2) | 用户数据 | CS | 16。
"""
from __future__ import annotations

from typing import Optional

from parser_lib.adapters.adapter_10376 import QGDW103762Adapter, build_frame

_ADAPTER = QGDW103762Adapter()


def build_13762_frame(
    afn: int,
    seq: int,
    rtsa: bytes,
    msaa: int = 0x01,
    pw: int = 0x0000,
    userdata: bytes = b"",
) -> bytes:
    """构造一个 1376.2 完整帧（自动计算 L 与 CS）。"""
    return build_frame(
        afn=afn, seq=seq & 0xFF, rtsa=rtsa[:6],
        msaa=msaa & 0xFF, pw=pw & 0xFFFF, userdata=userdata,
    )


def extract_frame(buf: bytes) -> Optional[bytes]:
    """从字节流开头切出一帧；半包/非本协议返回 None。

    利用 QGDW103762Adapter.try_extract（校验 68/L/68/16 与长度）。
    """
    res = _ADAPTER.try_extract(buf)
    if res is None:
        return None
    return res.raw


def scan_frame(buf: bytes):
    """扫描字节流，跳过前导脏字节，返回 (frame, consumed)。

    consumed 包含跳过的脏字节数 + 帧长度；找不到完整帧返回 (None, 0)。

    注意：QGDW103762Adapter.try_extract 要求整个 buffer 恰好一帧
    （L == len-2），不能用于粘包流；这里先按 L 字段切出帧长，再验证。
    """
    i = 0
    n = len(buf)
    while i < n:
        # 找到 0x68 起点
        if buf[i] != 0x68:
            i += 1
            continue
        if i + 4 > n:
            return None, 0  # 不够帧头
        L = buf[i + 1] | (buf[i + 2] << 8)
        frame_len = L + 2  # 68 | L(2) | body | CS | 16 中，L=除首尾两个68外字节数
        if L < 12:
            i += 1  # 非法长度，跳过此 0x68
            continue
        if i + frame_len > n:
            return None, 0  # 半包，等后续字节
        candidate = buf[i:i + frame_len]
        # 校验双 68 结构 + CS
        if candidate[3] == 0x68 and candidate[-1] == 0x16 and \
                sum(candidate[1:-2]) % 256 == candidate[-2]:
            return candidate, i + frame_len
        i += 1
    return None, 0


def decode_frame(raw: bytes) -> dict:
    """解析 1376.2 帧为结构化 dict（供匹配/判定/输出）。

    返回：
        {
          "structure": "1376.2",
          "raw_hex": "68..." ,
          "fields": { "AFN": {...}, "SEQ": {...}, "终端地址RTUA": "...",
                      "主站地址MSAA": "...", "密码PW": "...", "校验和CS": {...} },
          "items": [ {name,value,hex,desc}, ... ],       # 顶层数据项
          "nested": [ {structure, fields, items}, ... ], # 递归解出的 645/698 帧
          "warnings": [...],
        }
    """
    frame = _ADAPTER.decode(raw)

    def _df(f):
        return {"name": f.name, "value": f.value, "hex": f.hex,
                "raw": f.raw, "desc": f.desc}

    fields = {f.name: _df(f) for f in frame.fields}
    items = [_df(f) for f in frame.items]
    nested = []
    for n in frame.nested:
        nested.append({
            "structure": n.structure,
            "fields": {f.name: _df(f) for f in n.fields},
            "items": [_df(f) for f in n.items],
            "nested": [_df(f) for f in n.nested],
        })
    return {
        "structure": frame.structure,
        "raw_hex": frame.raw_hex,
        "fields": fields,
        "items": items,
        "nested": nested,
        "warnings": list(frame.warnings),
    }


def frame_to_hex(raw: bytes) -> str:
    """字节帧 → 大写空格分隔 hex 字符串。"""
    return " ".join(f"{b:02X}" for b in raw)


def hex_to_bytes(hex_str: str) -> bytes:
    """空格分隔/连续 hex 字符串 → bytes。"""
    s = hex_str.replace("0x", " ").replace(",", " ").strip()
    if not s:
        return b""
    return bytes(int(p, 16) for p in s.split())
