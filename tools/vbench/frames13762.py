"""1376.2 本地帧薄封装（vbench 侧）。

复用 sim_concentrator.frame_codec 与 parser_lib 的 10376.2 适配器构帧/解帧
——与被测库同源，保证字节口径一致（这是刻意的最小代码依赖，仅 import，
数据交换仍只经虚拟串口）。本模块补齐 vbench 需要的小工具：
afn/fn 提取、应用数据切片、13-F1 载荷解析。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from sim_concentrator.frame_codec import (  # noqa: F401  再导出
    build_13762_frame,
    build_local_13762_frame,
    decode_frame,
    scan_frame,
)


def afn_fn(decoded: dict) -> Tuple[int, int]:
    """从 decode_frame 结果取 (afn, fn) 整数。"""
    fields = decoded.get("fields", {})
    afn = decoded.get("afn", fields.get("AFN", {}).get("raw"))
    fn = decoded.get("fn", fields.get("FN", {}).get("raw"))
    return int(afn), int(fn)


def has_address(decoded: dict) -> bool:
    """帧是否带地址域 A（本地帧不带）。"""
    val = decoded.get("fields", {}).get("地址域A", {}).get("value", "")
    return bool(val) and val != "(无)"


def app_data(decoded: dict) -> bytes:
    """从 decode_frame 结果取应用数据字节（数据单元标识之后、CS 之前）。

    单 68 帧布局：68 L(2) C R(6) [A(12)] AFN DT(2) DATA CS 16。
    """
    raw = bytes.fromhex(decoded["raw_hex"])
    start = 13 + (12 if has_address(decoded) else 0)
    return raw[start:-2]


def parse_13f1_down(app: bytes) -> dict:
    """解析 13H-F1 下行应用数据 → {protocol, subs, payload_hex}。

    布局（10376.2 适配器口径）：protocol(1) delay_flag(1) subs(1)
    + subs×6B 地址 + payload_len(1) + payload。
    """
    if len(app) < 4:
        raise ValueError(f"13-F1 应用数据过短: {app.hex()}")
    protocol, _, n_sub = app[0], app[1], app[2]
    off = 3 + 6 * n_sub
    payload_len = app[off]
    payload = app[off + 1:off + 1 + payload_len]
    return {"protocol": protocol, "subs": n_sub, "payload": payload}


def build_13f1_up(protocol: int, payload: bytes, up_len: Optional[int] = None,
                  address: Optional[dict] = None) -> bytes:
    """构造 13H-F1 上行应答帧（payload 为内嵌 645/698 帧字节）。"""
    if up_len is None:
        up_len = len(payload)
    app = (up_len.to_bytes(2, "little") + bytes([protocol, len(payload)])
           + payload)
    return build_13762_frame(0x13, 1, appdata=app, direction="up",
                             address=address)


def node_list_reply(nodes: List[dict], total: Optional[int] = None) -> bytes:
    """构造 10H-F2 上行档案应答的 appdata（经 10376.2 适配器编码）。

    nodes: [{"addr": "12位BCD", "info": {"quality":0-15,"relay":0-15}}]
    """
    from parser_lib.adapters.adapter_10376 import encode_app_data

    return encode_app_data(0x10, 2, {"total": total or len(nodes), "nodes": nodes},
                           direction="up")
