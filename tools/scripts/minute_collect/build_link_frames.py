#!/usr/bin/env python3
"""链路层分帧序列构造器 —— 离线，无任何在线依赖。

APDU 超过单帧承载上限时分帧传输，分帧格式域 2 字节：
    00 = 起始帧（带完整头）/ 11 = 中间帧 / 01 = 末尾帧
输入完整 698 APDU，输出按 --mtu 切片的分帧序列（每片 = 分帧格式域 + APDU 片），
供 send_hex 逐帧注入（模拟 STA 分块应答），或核对 STA 侧真实分帧序列。

用法：
    python3 tools/scripts/minute_collect/build_link_frames.py \
        --apdu 8505030001... --mtu 200 --json
    python3 tools/scripts/minute_collect/build_link_frames.py \
        --data @block.hex --mtu 200        # --data 为 APDU hex（@文件 也行）

口径说明：mtu 是"每片携带的 APDU 字节数"上限（不含 2B 分帧格式域），
默认 200 对齐台架实测的单帧承载；3 片以内典型序列 00 / 11 / 01。
"""
from __future__ import annotations

import argparse
import json
import sys

#: 分帧格式域：起始帧 00 / 中间帧 11 / 末尾帧 01（DL/T 698.45 分帧传输）
SEG_FIRST = 0x00
SEG_MIDDLE = 0x11
SEG_LAST = 0x01


def split_apdu(apdu: bytes, mtu: int) -> list[tuple[int, bytes]]:
    """APDU → [(分帧格式域, APDU 片), ...]。单帧装得下时输出单元素起始+末尾同帧。

    口径：APDU 长度 ≤ mtu 时仍打起始域 00（完整单帧即起始亦即末尾，固件按
    格式域 00 走完整接收分支）；> mtu 时严格 00 → (11)* → 01。
    """
    if mtu <= 0:
        raise ValueError(f"mtu 需 >0: {mtu}")
    if len(apdu) <= mtu:
        return [(SEG_FIRST, apdu)]
    segments: list[tuple[int, bytes]] = []
    offset = 0
    total = len(apdu)
    while offset < total:
        chunk = apdu[offset: offset + mtu]
        offset += len(chunk)
        if offset >= total:
            segments.append((SEG_LAST, chunk))
        elif not segments:
            segments.append((SEG_FIRST, chunk))
        else:
            segments.append((SEG_MIDDLE, chunk))
    return segments


def build_link_frames(apdu_hex: str, mtu: int) -> list[str]:
    """APDU hex → 分帧序列 hex 列表（每元素 = 格式域 + 片，可直接 send_hex）。"""
    apdu = bytes.fromhex(apdu_hex)
    return [bytes([seg]) + chunk for seg, chunk in split_apdu(apdu, mtu)]


def main() -> int:
    parser = argparse.ArgumentParser(description="构造链路层分帧序列（离线）")
    parser.add_argument("--apdu", help="完整 APDU hex")
    parser.add_argument("--data", help="APDU hex（@文件 读整段）；与 --apdu 二选一")
    parser.add_argument("--mtu", type=int, default=200,
                        help="每片 APDU 字节上限（不含 2B 分帧格式域，默认 200）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    hex_text = args.apdu or args.data
    if not hex_text:
        parser.error("--apdu 与 --data 至少给一个")
    if hex_text.startswith("@"):
        with open(hex_text[1:], encoding="ascii") as handle:
            hex_text = "".join(handle.read().split())
    hex_text = hex_text.replace(" ", "").lower()
    if len(hex_text) % 2:
        parser.error("APDU hex 长度需为偶数")

    frames = build_link_frames(hex_text, args.mtu)
    if args.json:
        print(json.dumps({
            "apdu_len": len(hex_text) // 2,
            "mtu": args.mtu,
            "frame_count": len(frames),
            "frames_hex": [f.hex() for f in frames],
        }, ensure_ascii=False, indent=2))
    else:
        for index, frame in enumerate(frames, 1):
            print(f"# {index}/{len(frames)}  len={len(frame)}")
            print(frame.hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
