# -*- coding: utf-8 -*-
"""并发抄表帧识别（REQS-0030）。

口径来源：蒸馏卡《协议层/88_高频采集并发抄表_帧格式》（附件6 电科院手册 V2.7
表 2-1/2-2、附录 A）——并发抄表命令为 AFN=F1H, FN=F1H；上下行数据单元：
下行 = 规约类型(1B) + 保留(1B) + 长度L(2B, 大端?) + 报文内容；
上行 = 规约类型(1B) + 长度L(2B) + 报文内容（无保留字节）。
失败口径：上行报文长度域为 0，链路层源地址 A1 为失败电表地址。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

CONCURRENT_AFN = "F1"   # AFN=F1H
CONCURRENT_FN = "F1"    # FN=F1H

PROTO_TYPES = {
    0x00: "透明传输",
    0x01: "DL/T 645-1997",
    0x02: "DL/T 645-2007",
    0x03: "DL/T 698.45",
}


def is_concurrent_frame(afn: Optional[str], fn: Optional[str]) -> bool:
    """按 AFN/FN 判定是否并发抄表帧（AFN=F1H, FN=F1H）。

    afn/fn 允许 "F1"/"0xF1"/"241"/"f1" 等写法（listener 帧库 fn 存 "F1" 形式）。
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
    """解析并发抄表数据单元（88 卡 2.2/2.3）。

    返回 {proto_type, proto_name, length, failed, meter_addr}。
    dir_ 为 "down"/"up"（或 "tx"/"rx"），决定有无保留字节。
    """
    b = bytes.fromhex(appdata_hex.replace(" ", ""))
    reserved = 1 if dir_.lower() in ("down", "tx") else 0
    if len(b) < 1 + reserved + 2:
        return {"proto_type": None, "proto_name": None, "length": None,
                "failed": None, "meter_addr": None, "error": "数据单元过短"}
    proto = b[0]
    off = 1 + reserved
    length = int.from_bytes(b[off:off + 2], "big")
    failed = (dir_.lower() in ("up", "rx")) and length == 0
    return {
        "proto_type": proto,
        "proto_name": PROTO_TYPES.get(proto, f"保留({proto:02X}H)"),
        "length": length,
        "failed": failed,
        "meter_addr": None,  # 失败表地址在链路层 A1，由调用方补充
    }
