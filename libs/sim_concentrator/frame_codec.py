"""帧编解码层：统一封装 adapter_10376 的构帧与解析（单 68 标准帧）。

Q/GDW 10376.2—2019 单 68 标准帧（ADR-44）：
    68H | L(2B) | C(1B) | R(信息域) | A(地址域) | AFN | DT1 | DT2 | 应用数据 | CS | 16H

关键简化（用户确认 2026-08-27）：CCO 本地协议帧（68|L|ctrl|info|afn|DT1|DT2|buff|CS|16）
与单 68 标准帧结构完全一致（ctrl=C、info=R、buff=应用数据，区别仅在有/无地址域）。
因此 CCO 本地协议不再需要独立编解码——统一走本文件的单 68 标准实现，
`build_local_13762_frame` / `decode_local_13762_frame` 保留为兼容别名。

对外能力：
- build_13762_frame():  构造 Q/GDW 10376.2 单 68 帧字节（自动算 L 与 CS）。
- extract_frame():      从串口字节流中切出首帧（半包返回 None）。
- scan_frame():         扫描字节流跳过脏字节切帧（单 68）。
- decode_frame():       解析 1376.2 帧 → 结构化 dict（信封字段 + 嵌套 645/698）。
- frame_to_hex()/hex_to_bytes(): 字节帧 ↔ hex 字符串。
- fn_to_dt()/dt_to_fn(): Fn ↔ DT1/DT2 映射。
"""
from __future__ import annotations

from typing import Optional

from parser_lib.adapters.adapter_10376 import (
    QGDW103762Adapter,
    build_frame,
    fn_to_dt,
    dt_to_fn,
)

_ADAPTER = QGDW103762Adapter()


def build_13762_frame(
    afn: int,
    fn: int = 1,
    appdata: bytes = b"",
    direction: str = "down",
    comm_mode: int = 3,
    info: Optional[dict] = None,
    address: Optional[dict] = None,
) -> bytes:
    """构造一个 Q/GDW 10376.2 单 68 标准帧（自动计算 L 与 CS）。

    afn: 应用功能码；fn: 信息类标识 Fn（自动编码 DT1/DT2）。
    appdata: 应用数据单元字节（可含内嵌 645/698 帧）。
    direction: "down"(DIR=0) | "up"(DIR=1)。
    comm_mode: 通信方式（1集中式/2分布式/3HPLC/10微功率/20以太网）。
    info: 信息域 R 字段 dict（含 seq 报文序列号）。
    address: 地址域 A dict {src, relay[], dst}（BCD 字符串）。
    """
    return build_frame(
        direction=direction, comm_mode=comm_mode, info=info, address=address,
        afn=afn, fn=fn, appdata=appdata,
    )


def extract_frame(buf: bytes) -> Optional[bytes]:
    """从字节流开头切出一帧；半包/非本协议返回 None。

    利用 QGDW103762Adapter.try_extract（校验 68/L/C/16 与长度，单 68）。
    """
    res = _ADAPTER.try_extract(buf)
    if res is None:
        return None
    return res.raw


def scan_frame(buf: bytes):
    """扫描字节流，跳过前导脏字节，返回 (frame, consumed)。

    consumed 包含跳过的脏字节数 + 帧长度；找不到完整帧返回 (None, 0)。

    单 68 结构：L = 整帧长（68 + L(2) + C + 用户数据 + CS + 16）。
    """
    i = 0
    n = len(buf)
    while i < n:
        if buf[i] != 0x68:
            i += 1
            continue
        if i + 4 > n:
            return None, 0  # 不够帧头
        L = buf[i + 1] | (buf[i + 2] << 8)
        frame_len = L  # 单68：L = 整帧长
        if L < 10:
            i += 1  # 非法长度，跳过此 0x68
            continue
        if i + frame_len > n:
            return None, 0  # 半包，等后续字节
        candidate = buf[i:i + frame_len]
        # 校验单 68 结构（第3字节非 0x68，避免与双68/645/698 混淆）+ CS
        if candidate[3] != 0x68 and candidate[-1] == 0x16 and \
                sum(candidate[3:-2]) % 256 == candidate[-2]:
            return candidate, i + frame_len
        i += 1
    return None, 0


def decode_frame(raw: bytes) -> dict:
    """解析 1376.2 单 68 帧为结构化 dict（供匹配/判定/输出）。

    返回：
        {
          "structure": "1376.2",
          "raw_hex": "68..." ,
          "fields": { "长度L": {...}, "控制域C": {...}, "信息域R": {...},
                      "地址域A": {...}, "AFN": {...}, "数据单元标识": {...},
                      "FN": {...}, "校验和CS": {...} },
          "items": [ {name,value,hex,desc}, ... ],       # 顶层数据项
          "nested": [ {structure, fields, items}, ... ], # 递归解出的 645/698 帧
          "warnings": [...],
        }
    """
    frame = _ADAPTER.decode(raw)

    def _df(f):
        return {"name": f.name, "value": f.value, "hex": f.hex,
                "raw": f.raw, "desc": f.desc, "unit": f.unit}

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


# ---------------------------------------------------------------------------
# CCO 本地协议（= 单 68 标准帧）兼容别名
#
# 历史：模拟集中器与 CCO 模块交互曾用独立编解码（build_local/decode_local）。
# 用户确认 2026-08-27：CCO 本地协议与 Q/GDW 10376.2 单 68 标准帧结构完全一致，
# 统一走单 68 标准实现。以下函数保留签名与行为语义，作为兼容入口。
# ---------------------------------------------------------------------------

def build_local_13762_frame(
    afn: int,
    fn: int,
    buff: bytes = b"",
    ctrl: int = 0x43,
    info: bytes = b"\x00\x00\x00\x00\x00\x00",
    seq: int = 1,
    end: int = 0x16,
) -> bytes:
    """构造 CCO 本地协议帧（兼容入口）—— 底层即单 68 标准帧。

    ctrl 高两位: bit7=dir(0下行/1上行), bit6=prm(0从动/1启动)，低6位=通信方式。
    info: 6B 信息域（无地址域场景）；info[5]=seq 报文序列号。
    """
    direction = "up" if (ctrl >> 7) & 0x01 else "down"
    prm = (ctrl >> 6) & 0x01
    comm_mode = ctrl & 0x3F
    info_dict = {}
    if len(info) >= 6:
        # 从 info 字节反推 dict（与 build_frame 一致）
        from parser_lib.adapters.adapter_10376 import _parse_info
        info_dict = _parse_info(direction, info)
    info_dict["seq"] = seq & 0xFF
    frame = build_frame(
        direction=direction, prm=prm, comm_mode=comm_mode,
        info=info_dict, address=None, afn=afn, fn=fn, appdata=buff, end=end,
    )
    return frame


def scan_local_frame(buf: bytes):
    """扫描 CCO 本地协议帧（兼容入口）—— 即 scan_frame。"""
    return scan_frame(buf)


def decode_local_13762_frame(raw: bytes) -> dict:
    """解析 CCO 本地协议帧（兼容入口）—— 即 decode_frame。

    返回 structure 保持 "1376.2"（不再单独标记 local），并补充
    ctrl/info/afn/fn/buff 便捷字段以兼容旧消费方。
    """
    d = decode_frame(raw)
    d["structure"] = "1376.2"
    # 兼容字段：ctrl/info/afn/fn/buff
    d["ctrl"] = raw[3]
    d["info"] = raw[4:10].hex()
    d["afn"] = d["fields"].get("AFN", {}).get("raw")
    d["fn"] = d["fields"].get("FN", {}).get("raw")
    # buff = 应用数据（AFN/DT 之后）
    userdata = raw[4:len(raw) - 2]
    pos = 6  # 跳过信息域 R
    d["buff"] = userdata[pos + 3:]  # 跳过 R + AFN/DT
    return d
