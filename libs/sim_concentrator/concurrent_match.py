# -*- coding: utf-8 -*-
"""并发抄表帧识别（REQS-0030）。

口径来源：蒸馏卡《CCO实现逻辑/02-并发抄表》（CCO 代码基线逐行核实，
gw13762.h: FN_CONCURRENT_METER=(1)；《协议层/88》卡在知识库中不存在，
REQS-0030 初版引用有误，2026-09-13 实测验证后更正）：
- 并发抄表命令 AFN=F1H、Fn=1（DT1=01H/DT2=00H）。帧库（journal 归一）把
  Fn 按十进制渲染为 "F{n}" 存库，故 Fn=1 落库为 "F1"——与 AFN 的 "F1"
  同形但语义不同（一个是 AFN 十六进制、一个是 Fn 十进制渲染）。
- 下行数据单元 = 规约类型(1B) + 保留(1B) + 长度L(2B, 低字节在前，
  datalen=(buff[3]<<8)+buff[2]) + 电表协议报文；
- 上行数据单元 = 规约类型(1B) + 长度L(2B, 低字节在前) + 电表应答数据（无保留字节）；
- 失败口径：上行报文长度域为 0（失败帧回空载荷），链路层源地址 A1 为失败电表地址。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

CONCURRENT_AFN = "F1"   # AFN=F1H（十六进制真值）
CONCURRENT_FN = "F1"    # Fn=1 的 journal 渲染（F{n} 十进制，线上真值为 1）

PROTO_TYPES = {
    0x00: "透明传输",
    0x01: "DL/T 645-1997",
    0x02: "DL/T 645-2007",
    0x03: "DL/T 698.45",
}


def is_concurrent_frame(afn: Optional[str], fn: Optional[str]) -> bool:
    """按 AFN/FN 判定是否并发抄表帧（AFN=F1H；Fn=1，帧库渲染为 "F1"）。

    afn/fn 允许 "F1"/"0xF1"/"241"/"f1" 等写法（listener 帧库 fn 存 "F1" 形式，
    即 Fn=1 的 F{n} 渲染；数字真值 Fn=1 因与 0xF1 冲突不在本判定范围，
    帧库写入路径已保证渲染形态）。
    """
    def norm(v: Optional[str]) -> Optional[int]:
        if v is None:
            return None
        s = str(v).strip().upper().replace("H", "")
        if s.startswith("0X"):
            s = s[2:]
        try:
            if s.isdigit():
                return int(s) if int(s) <= 0xFF else int(s, 16)
            return int(s, 16)
        except ValueError:
            return None
    return norm(afn) == 0xF1 and norm(fn) == 0xF1


def parse_data_unit(dir_: str, appdata_hex: str) -> Dict[str, Any]:
    """解析并发抄表数据单元（02 卡 gw13762.c:12099-12101 数据单元布局）。

    返回 {proto_type, proto_name, length, failed, meter_addr}。
    dir_ 为 "down"/"up"（或 "tx"/"rx"），决定有无保留字节。
    长度域低字节在前（02 卡：datalen=(buff[3]<<8)+buff[2]）；判成败
    （长度是否为 0）不受字节序影响，数值口径按 CCO 代码取小端。
    """
    b = bytes.fromhex(appdata_hex.replace(" ", ""))
    reserved = 1 if dir_.lower() in ("down", "tx") else 0
    if len(b) < 1 + reserved + 2:
        return {"proto_type": None, "proto_name": None, "length": None,
                "failed": None, "meter_addr": None, "error": "数据单元过短"}
    proto = b[0]
    off = 1 + reserved
    length = int.from_bytes(b[off:off + 2], "little")
    failed = (dir_.lower() in ("up", "rx")) and length == 0
    return {
        "proto_type": proto,
        "proto_name": PROTO_TYPES.get(proto, f"保留({proto:02X}H)"),
        "length": length,
        "failed": failed,
        "meter_addr": None,  # 失败表地址在链路层 A1，由调用方补充
    }
