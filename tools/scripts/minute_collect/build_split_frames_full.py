#!/usr/bin/env python3
"""698 链路层分帧完整帧构造器 —— 离线，无任何在线依赖。

用途：把一条完整 698 APDU 按链路层分帧切片，输出**可直接 send_hex 注入 STA
的完整 698 帧**（模拟 STA 分块应答上行）。默认 APDU 为 2026-11-03 23:30 轮
实机复测通过的内容（8502 WithList，OAD=20000201/20010201 与 CCO 任务配置
匹配 → STA 回 2 帧类型 10 确认 + 末帧合并 + Freeze stored）。

与 build_link_frames.py 的区别：后者输出的是 APDU 片（1B 段标记 + 片，语义
对照/拼帧用），不是完整帧，不能直接 send_hex；本脚本输出完整 698 帧：

    68 | L(2B LE) | C=0xE3 | SA(7B) | CA | HCS(2B) | 分帧格式域(2B) | 片 | FCS(2B) | 16

口径：
- L = 自身 2B + C..FCS = 帧总长 - 2（低字节在前）；分帧比单帧多 2B 格式域，
  复用 adapter_698.build_frame 时把"格式域 + 片"整体当 APDU 载荷传入（L 自动 +2）；
- 分帧格式域 2B：b0 = seq 低 8 位；b1 高 2 位 = 帧类型（00 起始 / C0 中间 /
  40 末尾）+ 低 4 位 = seq 高 4 位；
- HCS 覆盖 b[1:12]（L..CA），FCS 覆盖 b[1:-3]（L..末片尾），两窗口都含最终
  L 值——先定 L 再算 HCS/FCS（事实源 adapter_698.build_frame 已按此实现）。

踩坑（2026-11-03 实机复测教训，详见本目录 README 坑 5/6/7）：
- SA 是 7B 不是 6B：首字节 0x05 是地址长度/类型头，后面才是 6B BCD 地址
  （低位在前，AF05+6B）；少算一字节 HCS/FCS/APDU 全体错位；
- HCS/FCS 覆盖窗含最终 L 值：分帧帧比单帧多 2B 格式域，L 变了校验窗跟着变；
- APDU 内容必须与 CCO 任务配置 OAD 匹配（OAD/计数），只过 HCS/FCS 不够：
  22:30 轮旧帧 OAD=22000201/计数=01 与任务(20000201/20010201)不匹配，链路层
  ACK 正常但末帧合并后解析跳过、不落盘；换正确 APDU 后 23:30 轮复测通过。

自检：每次构建前先跑 golden 自检——用 22:30 轮已过 STA 链路层 ACK 的三帧
做字节级基准比对（MISMATCH 即 exit 1；注意那三帧 APDU 是旧口径 OAD=22000201，
仅作帧布局基准，不要直接注入）。构建后再做 HCS/FCS 回验与分片合并检查。

用法：
    python3 tools/scripts/minute_collect/build_split_frames_full.py \
        --apdu 850201022000020101110120010201011102 --mtu 8 \
        --out /tmp/frames_split.txt      # 每行一帧，喂 watch_inject.py --frames
    python3 tools/scripts/minute_collect/build_split_frames_full.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "libs"))

from parser_lib.adapters.adapter_698 import _check_fcs, build_frame  # noqa: E402

#: STA 侧 SA 域（7B）：0x05 地址长度/类型头 + 6B BCD 地址低位在前
#: 000000000001 → 05 00 00 00 00 00 01（少算成 6B 会全体错位，见踩坑）
STA_SA = bytes.fromhex("05000000000001")
CA_STA = 0x01        #: 客户机地址 CA（STA 侧 01）
CTRL_SPLIT = 0xE3    #: 控制域：DIR=1 PRM=1 分帧标志=1

#: 分帧格式域 b1 高 2 位帧类型（b1 = 类型<<6 | seq 高 4 位）
TYPE_START = 0x00
TYPE_MID = 0xC0
TYPE_END = 0x40

#: 23:30 轮实机复测通过的默认 APDU（8502 WithList：OAD=20000201 计数 01 +
#: OAD=20010201 计数 01，与 CCO 任务配置匹配）
DEFAULT_APDU = "850201022000020101110120010201011102"

#: golden 自检基准：22:30 轮已过 STA 链路层 ACK 的三帧（字节级布局基准）。
#: 条目 = (参考帧 hex, APDU 片 hex, seq, 帧类型)。注意参考帧 APDU 内容是旧
#: 口径 OAD=22000201（与任务配置不匹配，见坑 5），仅验帧布局、勿直接注入。
GOLDEN = [
    ("681C00E30500000000000101D4000000850201012200020101110149A816",
     "8502010122000201011101", 0, TYPE_START),
    ("681900E30500000000000101100B01C00111012200020101350A16",
     "0111012200020101", 1, TYPE_MID),
    ("681400E305000000000001017EBC0240011102C87916",
     "011102", 2, TYPE_END),
]


def fmt_field(seq: int, seg_type: int) -> bytes:
    """2B 分帧格式域：b0 = seq 低 8 位；b1 = 帧类型（高 2 位）+ seq 高 4 位。"""
    return bytes([seq & 0xFF, seg_type | ((seq >> 8) & 0x0F)])


def split_pieces(apdu: bytes, mtu: int) -> list[tuple[int, int, bytes]]:
    """APDU → [(seq, 帧类型, 片), ...]，每片 ≤ mtu 字节（不含 2B 格式域）。

    单帧装得下（≤mtu）时输出单片起始帧并告警——此时本不该走分帧（STA 等不到
    末尾帧会一直挂起合并），请确认真的需要分帧。
    """
    if mtu <= 0:
        raise ValueError(f"mtu 需 >0: {mtu}")
    chunks = [apdu[i:i + mtu] for i in range(0, len(apdu), mtu)]
    if len(chunks) == 1:
        print(f"[WARN] APDU {len(apdu)}B ≤ mtu {mtu}，单片起始帧不会触发 STA 末帧合并；"
              f"单帧装得下时无需分帧", file=sys.stderr)
    pieces: list[tuple[int, int, bytes]] = []
    for seq, chunk in enumerate(chunks):
        if seq == 0:
            seg_type = TYPE_START
        elif seq == len(chunks) - 1:
            seg_type = TYPE_END
        else:
            seg_type = TYPE_MID
        pieces.append((seq, seg_type, chunk))
    return pieces


def build_split_frame(piece: bytes, fmt: bytes) -> bytes:
    """格式域 + 片 → 完整 698 帧（68 头/HCS/FCS 由 adapter_698.build_frame 计算）。

    把"2B 分帧格式域 + 片"整体当 APDU 载荷传入，L/HCS/FCS 覆盖窗自动含格式域，
    与 22:30/23:30 轮实机验证帧逐字节同构（golden 自检兜底）。
    """
    return build_frame(fmt + piece, STA_SA, ca=CA_STA, control=CTRL_SPLIT)


def check_frame(frame: bytes) -> dict:
    """HCS/FCS 回验（覆盖窗与构帧口径一致：HCS=b[1:12]，FCS=b[1:-3]）。"""
    return {"hcs_ok": _check_fcs(frame[1:12], frame[12:14]),
            "fcs_ok": _check_fcs(frame[1:-3], frame[-3:-1])}


def run_golden() -> list[dict]:
    """逐字节复现 22:30 已验证三帧；任一 MISMATCH 即退出（exit 1）。"""
    results: list[dict] = []
    for index, (ref_hex, piece_hex, seq, seg_type) in enumerate(GOLDEN):
        got = build_split_frame(bytes.fromhex(piece_hex), fmt_field(seq, seg_type))
        match = got.hex() == ref_hex.lower()
        checks = check_frame(got)
        results.append({"index": index, "match": match, **checks})
        print(f"golden#{index}: {'MATCH' if match else 'MISMATCH'}  "
              f"hcs={checks['hcs_ok']} fcs={checks['fcs_ok']}", file=sys.stderr)
        if not match:
            print(f"  ref: {ref_hex}", file=sys.stderr)
            print(f"  got: {got.hex()}", file=sys.stderr)
            raise SystemExit(1)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构造 698 链路层分帧完整帧（离线；实机 send_hex 注入用，见 docstring）")
    parser.add_argument("--apdu", default=DEFAULT_APDU,
                        help="完整 APDU hex（默认 23:30 轮已验证内容；@文件 读整段）")
    parser.add_argument("--mtu", type=int, default=8,
                        help="每片 APDU 字节上限（不含 2B 分帧格式域；23:30 轮验证值 8）")
    parser.add_argument("--out",
                        help="保存帧序列到文件（每行一帧 hex，供 watch_inject.py --frames）；缺省仅 stdout")
    parser.add_argument("--json", action="store_true", help="输出 JSON（golden 结果 + 帧序列）")
    args = parser.parse_args()

    apdu_hex = args.apdu
    if apdu_hex.startswith("@"):
        with open(apdu_hex[1:], encoding="ascii") as handle:
            apdu_hex = "".join(handle.read().split())
    apdu_hex = apdu_hex.replace(" ", "").lower()
    if len(apdu_hex) % 2:
        parser.error("APDU hex 长度需为偶数")
    apdu = bytes.fromhex(apdu_hex)

    golden = run_golden()

    pieces = split_pieces(apdu, args.mtu)
    merged = b"".join(chunk for _, _, chunk in pieces)
    merge_ok = merged == apdu
    frames = [build_split_frame(chunk, fmt_field(seq, seg_type))
              for seq, seg_type, chunk in pieces]
    checks = [check_frame(frame) for frame in frames]
    all_ok = merge_ok and all(c["hcs_ok"] and c["fcs_ok"] for c in checks)
    if not all_ok:
        print("[FAIL] HCS/FCS 回验或合并检查未通过", file=sys.stderr)
        return 1

    print(f"APDU len={len(apdu)}, {len(frames)} pieces, mtu={args.mtu}, "
          f"merge={'OK' if merge_ok else 'FAIL'}", file=sys.stderr)
    if args.out:
        with open(args.out, "w", encoding="ascii") as handle:
            for frame in frames:
                handle.write(frame.hex().upper() + "\n")
        print(f"saved -> {args.out}", file=sys.stderr)

    if args.json:
        print(json.dumps({
            "golden": golden,
            "apdu_len": len(apdu),
            "mtu": args.mtu,
            "frame_count": len(frames),
            "merge_ok": merge_ok,
            "frames_hex": [frame.hex().upper() for frame in frames],
            "checks": checks,
            "out": args.out,
        }, ensure_ascii=False, indent=2))
    else:
        for index, frame in enumerate(frames, 1):
            print(f"{frame.hex().upper()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
