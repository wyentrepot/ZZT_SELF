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


# ---------------------------------------------------------------------------
# CCO 本地协议（单 68 帧）编解码 —— 供模拟集中器与 CCO 模块交互
#
# 帧格式（gw13762.c，Q/GDW 1376.2 本地接口）：
#     68 | L(2B,LE) | ctrl(1B) | info(6B) | afn(1B) | DT1(1B) | DT2(1B)
#        | buff(n) | CS(1B) | 16
# - L = 除首尾两个字节外的总字节数；无地址域时 L = 15 + len(buff)
# - ctrl: mode(6bit) | prm(1bit) | dir(1bit)，下行 dir=0/prm=0
# - info(6B) 下行: rout_id(1) attach(1) module_id(1) clash(1) relay(4)
#                 | err_id(4) chn_id(4) | ack_len | rate_v[2] | serial_num
# - FN 编码：DT2=(fn-1)>>3，DT1=1<<((fn-1)%8)（余数0→128）
# ---------------------------------------------------------------------------


def fn_to_dt(fn: int):
    """Fn 码 -> (DT1, DT2) 2 字节编码（CCO 本地协议）。"""
    fn = int(fn) & 0xFF
    dt2 = (fn - 1) >> 3
    rem = fn & 0x07
    dt1 = 1 << (rem - 1) if rem != 0 else 128
    return dt1, dt2


def dt_to_fn(dt1: int, dt2: int) -> int:
    """(DT1, DT2) 2 字节编码 -> Fn 码。"""
    mapping = {1: 1, 2: 2, 4: 3, 8: 4, 16: 5, 32: 6, 64: 7, 128: 8}
    base = mapping.get(int(dt1))
    if base is None:
        return 0
    return int(dt2) * 8 + base


def build_local_13762_frame(
    afn: int,
    fn: int,
    buff: bytes = b"",
    ctrl: int = 0x43,
    info: bytes = b"\x00\x00\x00\x00\x00\x00",
    seq: int = 1,
    end: int = 0x16,
) -> bytes:
    """构造 CCO 本地协议帧（单 68）：68 L ctrl info afn DT1 DT2 buff CS 16。

    默认 ctrl=0x43（mode=3 宽带载波，prm=1 启动站，dir=0 下行——模拟集中器
    作为启动站下发查询，与 GW-CASS Creat_3762_Frame('43',...) 对齐）；
    info 默认 6 字节（无地址域，HOST_NODE 操作），info[5] = seq 帧序号
    （GW-CASS 每帧递增，CCO 在响应中回显 serial_num）。
    """
    dt1, dt2 = fn_to_dt(fn)
    if len(info) < 6:
        info = bytes(info) + b"\x00" * (6 - len(info))
    info = bytearray(info[:6])
    info[5] = seq & 0xFF  # 帧序号（GW-CASS：info 第6字节 = serial_num）
    info = bytes(info)
    body = bytes([afn & 0xFF, dt1, dt2]) + bytes(buff)
    mid = bytes([ctrl & 0xFF]) + info + body
    # CCO 本地协议：length 字段 = 帧总长（含首字节 0x68）。
    # CCO rx 校验（gw13762.c）：datalen = p_rxbuf[2]<<8 | p_rxbuf[1]（大端），
    # 要求 p_rxbuf[datalen-1] == 0x16，故 L = 帧总长 = 15 + len(buff)。
    L = 1 + 2 + len(mid) + 1 + 1  # = 15 + len(buff)
    frame = bytes([0x68, L & 0xFF, (L >> 8) & 0xFF]) + mid
    # CS 校验：CCO（gw13762.c）从控制域 p_rxbuf[3] 起累加（不含 68/L 头、不含 CS/16），
    # 与 GW-CASS CheckSum(..., 3) 对齐。因此 = sum(mid) % 256。
    cs = sum(mid) % 256
    return frame + bytes([cs, end & 0xFF])


def scan_local_frame(buf: bytes):
    """从字节流开头扫描一帧 CCO 本地协议帧（单 68）。

    返回 (frame, consumed)；找不到完整帧返回 (None, 0)。
    """
    i = 0
    n = len(buf)
    while i < n:
        if buf[i] != 0x68:
            i += 1
            continue
        if i + 4 > n:
            return None, 0
        L = buf[i + 1] | (buf[i + 2] << 8)
        frame_len = L  # CCO 本地协议：L 字段 = 整个帧长
        if L < 15:
            i += 1  # 非法长度（本地协议最小帧长 15）
            continue
        if i + frame_len > n:
            return None, 0
        candidate = buf[i:i + frame_len]
        # 单 68：第 3 字节是控制域（非 68），帧尾 16，CS 校验
        # CS = sum(控制域起，即 index 3 到 -3) % 256（与 CCO gw13762.c 一致）
        if candidate[3] != 0x68 and candidate[-1] == 0x16 and \
                sum(candidate[3:-2]) % 256 == candidate[-2]:
            return candidate, i + frame_len
        i += 1
    return None, 0


def decode_local_13762_frame(raw: bytes) -> dict:
    """解析 CCO 本地协议帧为结构化 dict（供匹配/判定/展示）。

    返回：
        {
          "structure": "1376.2-local",
          "raw_hex": "68...",
          "fields": { "控制域": {...}, "AFN": {...}, "FN": {...},
                       "DT1": {...}, "DT2": {...}, "校验和CS": {...} },
          "items": [ {name, value, hex}, ... ],   # buff 字节项
          "buff_hex": "...",
          "ctrl": int, "info": "hex", "afn": int, "fn": int,
          "buff": bytes,
        }
    """
    L = raw[1] | (raw[2] << 8)
    ctrl = raw[3]
    info = raw[4:10]
    afn = raw[10]
    dt1 = raw[11]
    dt2 = raw[12]
    fn = dt_to_fn(dt1, dt2)
    buff = raw[13:len(raw) - 2]
    cs = raw[-2]
    fields = {
        "控制域": {"raw": ctrl, "value": f"0x{ctrl:02X}",
                  "hex": f"{ctrl:02X}", "desc": "mode/prm/dir"},
        "AFN": {"raw": afn, "value": f"0x{afn:02X}",
                "hex": f"{afn:02X}", "desc": "应用层功能码"},
        "FN": {"raw": fn, "value": str(fn),
               "hex": f"{dt1:02X} {dt2:02X}", "desc": "Fn 码(DT1 DT2)"},
        "DT1": {"raw": dt1, "value": f"0x{dt1:02X}", "hex": f"{dt1:02X}"},
        "DT2": {"raw": dt2, "value": f"0x{dt2:02X}", "hex": f"{dt2:02X}"},
        "校验和CS": {"raw": cs, "value": f"0x{cs:02X}", "hex": f"{cs:02X}"},
    }
    items = [{"name": f"buff[{i}]", "value": f"0x{b:02X}", "hex": f"{b:02X}"}
             for i, b in enumerate(buff)]
    return {
        "structure": "1376.2-local",
        "raw_hex": raw.hex(),
        "fields": fields,
        "items": items,
        "buff_hex": buff.hex(),
        "ctrl": ctrl,
        "info": info.hex(),
        "afn": afn,
        "fn": fn,
        "buff": buff,
    }
