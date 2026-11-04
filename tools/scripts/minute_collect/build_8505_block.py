#!/usr/bin/env python3
"""698 内层分块响应帧（APDU 85 05）构造器 —— 离线，无任何在线依赖。

STA 分钟采集补抄里 CCO 发的读块请求内层 APDU = `85 05`，STA 侧应答按
DL/T 698.45 表 76 的"读块"结构回：PIID-ACD → lastblock → block → CHOICE(0x01)
→ 数据。链路层帧复用 parser_lib.adapters.adapter_698.build_frame（单一事实源，
libs/sim_meter/protocol_698.py 同款）：68 | L | C | SA(0x05+6B addr) | CA |
HCS(L..CA) | APDU | FCS(L..APDU) | 16，与台架证据帧 68 34 00 C3 05 00 00 00 00 00 01 01
（SA=05+000000000001、CA=01，minute_assert/test_mclt_698.py INNER_698_TASK1）
逐段同构。

用法（构造 + 输出完整帧 hex）：
    python3 tools/scripts/minute_collect/build_8505_block.py \
        --piid 00 --lastblock 0 --block 0 --data 1120
    python3 tools/scripts/minute_collect/build_8505_block.py \
        --piid 00 --lastblock 1 --block 2 --data aabb --control c3 --json

只构帧不发送（发送/注入走 simeter `POST /send_hex`，见 wb-simeter SKILL.md
§4.1 与本目录 README）；--data 也可用 `@文件` 传整块 hex。
"""
from __future__ import annotations

import argparse
import json
import sys
import os

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "libs"))

from parser_lib.adapters.adapter_698 import build_frame  # noqa: E402

#: 应答控制域 C=0xC3（REQS-0037 实测）、下行请求 C=0x43
CTRL_UP = 0xC3
CTRL_DOWN = 0x43
#: APDU 头：读块 85 05（服务 85 + 方法 05，PIID-ACD 前置）
APDU_CLS = 0x85
APDU_READ_BLOCK = 0x05
#: SECURITY-Response 明文包装前缀 90 00 + 长度（1B，APDU ≤255 时）
SEC_PREFIX = b"\x90\x00"
#: APDU 尾部 9 字节（more_frame_698_ack_struct 口径；00 00 01 00 04 + 4B 时间戳占位）
TAIL = bytes([0x00, 0x00, 0x01, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00])


def addr_to_sa(addr_bcd: str) -> bytes:
    """12 位 BCD 表地址 → SA 域（0x05 长度/类型头 + 6B 地址，低位在前）。

    与 libs/sim_meter/protocol_698.py 口径一致（SA=0x05+addr）；000000000001
    → 05 00 00 00 00 00 01，与证据帧逐字节同构。
    """
    text = str(addr_bcd)
    if len(text) != 12 or not text.isdigit():
        raise ValueError(f"地址需 12 位 BCD: {addr_bcd!r}")
    wire = bytes.fromhex(text)[::-1]   # BCD 低位在前
    return b"\x05" + wire


def build_8505_apdu(*, piid: int, lastblock: int, block: int, data_hex: str) -> bytes:
    """构造 85 05 APDU：PIID-ACD → lastblock → block → CHOICE → data → 9B 尾。

    字段顺序依据 DL/T 698.45 表 76"读块"应答结构（请求同序不含 CHOICE 之后的
    数据）；block/lastblock 均单字节（台架实测块号 ≤255），data 为该块原始 hex。
    """
    if not 0 <= piid <= 0xFF:
        raise ValueError(f"PIID-ACD 需 0~255: {piid}")
    if lastblock not in (0, 1):
        raise ValueError(f"lastblock 需 0/1: {lastblock}")
    if not 0 <= block <= 0xFF:
        raise ValueError(f"block 需 0~255: {block}")
    data = bytes.fromhex(data_hex)
    body = bytearray()
    body.append(piid & 0xFF)
    body.append(lastblock & 0xFF)
    body.append(block & 0xFF)
    body.append(0x01)          # CHOICE：octet-string
    body += data
    body += TAIL
    # APDU 长度字段按"含 9 字节尾"口径（minute_assert.parse_inner_698 的
    # apdu_len_has_tail）——本帧走 SECURITY 包装，长度在包装层写
    return bytes(body)


def build_8505_frame(*, piid: int, lastblock: int, block: int, data_hex: str,
                     control: int = CTRL_UP, addr_bcd: str = "000000000001",
                     ca: int = 0x01) -> bytes:
    """完整内层 698 帧（85 05 读块应答，SECURITY 明文 90 00 包装）。"""
    inner = build_8505_apdu(piid=piid, lastblock=lastblock, block=block,
                            data_hex=data_hex)
    # SECURITY-Response 明文包装：90 00 <inner 长度> + inner（模拟电表 _security_wrap_response 同款）
    apdu = SEC_PREFIX + bytes([len(inner)]) + inner
    return build_frame(apdu, addr_to_sa(addr_bcd), ca=ca, control=control)


def main() -> int:
    parser = argparse.ArgumentParser(description="构造 698 85 05 分块响应内层帧（离线）")
    parser.add_argument("--piid", type=lambda v: int(v, 0), default=0x00,
                        help="PIID-ACD（默认 0）")
    parser.add_argument("--lastblock", type=int, default=0, choices=[0, 1],
                        help="lastblock：0=后续还有块，1=最后一块")
    parser.add_argument("--block", type=int, default=0, help="块序号（0~255）")
    parser.add_argument("--data", required=True,
                        help="块数据 hex（如 1120；或 @文件 读整块 hex）")
    parser.add_argument("--control", default="c3", help="控制域 hex（默认 c3 应答；请求用 43）")
    parser.add_argument("--addr", default="000000000001", help="12 位 BCD 表地址（默认 000000000001）")
    parser.add_argument("--ca", type=lambda v: int(v, 0), default=0x01, help="CA 客户端地址（默认 01）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（apdu_hex/frame_hex）")
    args = parser.parse_args()

    data_hex = args.data
    if data_hex.startswith("@"):
        with open(data_hex[1:], encoding="ascii") as handle:
            data_hex = "".join(handle.read().split())
    data_hex = data_hex.replace(" ", "").lower()

    inner = build_8505_apdu(piid=args.piid, lastblock=args.lastblock,
                            block=args.block, data_hex=data_hex)
    frame = build_8505_frame(piid=args.piid, lastblock=args.lastblock,
                             block=args.block, data_hex=data_hex,
                             control=int(args.control, 16), addr_bcd=args.addr, ca=args.ca)
    if args.json:
        print(json.dumps({
            "inner_apdu_hex": inner.hex(),
            "frame_hex": frame.hex(),
            "inner_len": len(inner),
            "frame_len": len(frame),
            "control": f"{int(args.control, 16):02X}",
            "sa": addr_to_sa(args.addr).hex(),
            "ca": f"{args.ca:02X}",
        }, ensure_ascii=False, indent=2))
    else:
        print(frame.hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
