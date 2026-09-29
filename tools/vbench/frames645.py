"""DL/T 645-2007 构帧/解帧（vbench 自包含实现，零仓库依赖）。

口径与被测件一致（sim_meter / H_STA 固件）：
- 帧结构 68 A0..A5 68 C L DATA CS 16，前导 FE×N 可选；
- CS = 从第一个帧起始符 68 起至 CS 前所有字节（含两个 68）模 256
  （DL/T 645-2007 标准 §4.2.4；实测 STA 帧吻合：99×6 广播读地址
  `6899..996813007916` 的 CS=0x79）；
- 数据域传输时逐字节 +33H（例外：13H 读地址应答的数据域为表地址原文）；
- 地址域 A0..A5 低字节在前，12 位地址串即线序（bytes.fromhex 直转）。
"""
from __future__ import annotations

from typing import Optional, Tuple

PREAMBLE = b"\xFE"


def cs645(addr: bytes, ctrl: int, data: bytes) -> int:
    """校验和：从第一个帧起始符 68 到 CS 之前所有字节（含两个 68），模 256。"""
    return (0x68 + sum(addr) + 0x68 + ctrl + len(data) + sum(data)) & 0xFF


def build_645(addr6: bytes, ctrl: int, data: bytes = b"",
              *, encrypt: bool = True, preamble_fe: int = 0) -> bytes:
    """构造 645 请求帧。encrypt=True 时数据域逐字节 +33H（主站发往从站口径）。"""
    wire_data = bytes((b + 0x33) & 0xFF for b in data) if encrypt else bytes(data)
    body = bytes(addr6) + bytes([ctrl, len(wire_data)]) + wire_data
    return (PREAMBLE * preamble_fe + b"\x68" + bytes(addr6) + b"\x68"
            + bytes([ctrl, len(wire_data)]) + wire_data
            + bytes([cs645(addr6, ctrl, wire_data), 0x16]))


def parse_645(raw: bytes) -> Tuple[bytes, int, bytes]:
    """拆 645 帧 → (地址域6B 线序, 控制码, 数据域原文未-33)。"""
    if len(raw) < 12 or raw[0] != 0x68 or raw[7] != 0x68 or raw[-1] != 0x16:
        raise ValueError(f"非 645 帧: {bytes(raw).hex().upper()}")
    addr, ctrl, length = raw[1:7], raw[8], raw[9]
    if len(raw) != 12 + length:
        raise ValueError(f"长度域与帧长不符: {bytes(raw).hex().upper()}")
    data = raw[10:10 + length]
    if raw[10 + length] != cs645(addr, ctrl, data):
        raise ValueError(f"CS 校验失败: {bytes(raw).hex().upper()}")
    return addr, ctrl, data


def un33(data: bytes) -> bytes:
    """数据域 -33H 还原原文。"""
    return bytes((b - 0x33) & 0xFF for b in data)


def encrypt33(data: bytes) -> bytes:
    return bytes((b + 0x33) & 0xFF for b in data)


def addr_wire(addr_str: str) -> bytes:
    """12 位地址串 → 地址域线序（A0..A5，bytes.fromhex 直转）。"""
    s = str(addr_str).strip().upper().replace(" ", "")
    if len(s) != 12 or any(c not in "0123456789ABCDEF" for c in s):
        raise ValueError(f"地址须为 12 位十六进制：{addr_str!r}")
    return bytes.fromhex(s)


def bcd_int(raw: bytes) -> int:
    """BCD 小端字节序（低位在前）→ 整数。"""
    return int("".join(f"{b:02X}" for b in reversed(raw)))


def di_wire(di_display: str) -> bytes:
    """显示序 DI3DI2DI1DI0（如 00010000）→ 线上 DI0..DI3 字节序。"""
    s = str(di_display).strip().upper().replace(" ", "")
    if len(s) != 8:
        raise ValueError(f"DI 须为 8 位（DI3DI2DI1DI0）：{di_display!r}")
    return bytes.fromhex(s)[::-1]


def extract_645(buf: bytes) -> tuple[Optional[bytes], int]:
    """从字节流中切出一条完整 645 帧（自包含切帧器）。

    返回 (frame_bytes, consumed)；帧不完整返回 (None, 0)；定位失败时
    丢弃引导字节并返回 (None, n) 供调用方重试。容忍前导 FE 与脏字节，
    以「68 头 + 长度域 + CS」三重校准，CS 不符只丢 1 字节重新同步。
    """
    i, n = 0, len(buf)
    while i < n:
        if buf[i] != 0x68:
            i += 1
            continue
        if n - i < 10:                      # 不够读到长度域
            return None, 0
        length = buf[i + 9]
        total = 12 + length
        if length > 200 or n - i < total:   # 异常长度视为脏字节 / 帧未到齐
            if length > 200:
                i += 1
                continue
            return None, 0
        frame = buf[i:i + total]
        if frame[7] != 0x68 or frame[-1] != 0x16:
            i += 1
            continue
        addr, ctrl, data = frame[1:7], frame[8], frame[10:10 + length]
        if frame[10 + length] != cs645(addr, ctrl, data):
            i += 1                          # CS 不符：假头，步进重新同步
            continue
        return frame, i + total
    return None, n if n else 0
