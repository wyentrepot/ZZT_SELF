#!/usr/bin/env python3
"""85 05 分块响应注入编排（send_hex 注入流程脚本化）。

流程：先等 STA 侧 GET 请求（simeter /frames 出现 85 05 下行请求帧，即 CCO/STA
补抄读块）→ 用 build_8505_block 逐块构帧 → 经 `POST /api/simeter/send_hex`
按块序号逐帧注入 → 每帧后短暂等待，最后核对 /frames 里应答帧全部出现。

依赖 simeter 串口已打开（`POST /api/simeter/open`，见 wb-simeter SKILL.md §4）；
纯构帧不带发送请用 build_8505_block.py / build_link_frames.py（离线可跑）。

用法（典型：3 块，第 3 块 lastblock=1）：
    python3 tools/scripts/minute_collect/inject_8505_blocks.py \
        --blocks 1120 --data1 3301 --data2 aabbccddeeff --wait-req 120

    # 等价语义：--blocks 是第 0 块数据；--dataN 覆盖第 N 块（N≥1）
    # 也可以直接给 --data-files f0.hex f1.hex f2.hex（每文件一块的 hex）

超时未等到请求：exit 2（请求帧可能已被上一轮消费，重跑或先 POST /close 再 open）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("WORKBENCH_BASE", "http://127.0.0.1:8790")
#: 分帧格式域（build_link_frames 同款，注入 85 05 内层帧时通常整帧一条）
SEG_FIRST = 0x00
SEG_MIDDLE = 0x11
SEG_LAST = 0x01


def _req(method: str, path: str, body: dict | None = None, timeout: int = 40):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:300]}


# build_8505_block 与本脚本同目录，直接复用其构帧函数（保持单一事实源）
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from build_8505_block import build_8505_frame  # noqa: E402


def wait_get_request(timeout_s: float, poll_s: float = 2.0, after_seq: int = 0) -> dict | None:
    """轮询 simeter /frames，等最新一条 rx 里出现 85 05 请求 APDU。

    85 05 请求内层 APDU 头：85 05（读块）。外层/SECURITY 因厂商实现有差异，
    这里按"APDU 头两字节出现在帧负载"的宽松口径抓取；抓到即返回该条目。
    """
    deadline = time.time() + timeout_s
    cursor = after_seq
    while time.time() < deadline:
        _, payload = _req("GET", f"/api/simeter/frames?dir=rx&after_seq={cursor}&limit=50")
        entries = payload.get("entries") or []
        for entry in entries:
            cursor = max(cursor, entry.get("seq", cursor))
            frame_hex = (entry.get("frame_hex") or entry.get("hex") or "").lower()
            if "8505" in frame_hex:
                return entry
        time.sleep(poll_s)
    return None


def inject(frame_hex: str) -> dict:
    code, payload = _req("POST", "/api/simeter/send_hex", {"hex": frame_hex})
    if code != 200:
        raise RuntimeError(f"send_hex HTTP {code}: {json.dumps(payload, ensure_ascii=False)[:200]}")
    return payload


def verify_reply(timeout_s: float, expect_blocks: int, poll_s: float = 2.0) -> int:
    """核对 /frames 里 tx 方向出现的 85 05 应答帧块数（block 序号出现个数）。"""
    deadline = time.time() + timeout_s
    seen: set[int] = set()
    cursor = 0
    while time.time() < deadline and len(seen) < expect_blocks:
        _, payload = _req("GET", f"/api/simeter/frames?dir=tx&after_seq={cursor}&limit=100")
        for entry in payload.get("entries") or []:
            cursor = max(cursor, entry.get("seq", cursor))
            frame_hex = (entry.get("frame_hex") or entry.get("hex") or "").lower()
            # 宽松口径：应答帧含 PIID + lastblock/block 结构（85 05 在 APDU 头）
            if "8505" not in frame_hex:
                continue
            # 逐块核对：块号在 CHOICE 前一个字节；这里只认“帧里出现该块号字节”
            for block in range(expect_blocks):
                if f"{block:02x}" in frame_hex:
                    seen.add(block)
        time.sleep(poll_s)
    return len(seen)


def main() -> int:
    parser = argparse.ArgumentParser(description="等 85 05 GET 请求 → send_hex 注入分块应答")
    parser.add_argument("--blocks", default="1120",
                        help="第 0 块数据 hex（默认 1120）")
    parser.add_argument("--data", nargs="*", default=[],
                        metavar="HEX", help="覆盖第 1..N 块数据 hex（顺序对应 block1..blockN）")
    parser.add_argument("--data-files", nargs="*", default=[],
                        metavar="FILE", help="每块一个文件（hex），覆盖 --blocks/--data")
    parser.add_argument("--piid", type=lambda v: int(v, 0), default=0x00, help="PIID-ACD（默认 0）")
    parser.add_argument("--control", default="c3", help="控制域 hex（默认 c3）")
    parser.add_argument("--addr", default="000000000001",
                        help="12 位 BCD 表地址（默认 000000000001）")
    parser.add_argument("--ca", type=lambda v: int(v, 0), default=0x01, help="CA（默认 01）")
    parser.add_argument("--wait-req", type=float, default=120.0, help="等请求超时秒（默认 120）")
    parser.add_argument("--verify-timeout", type=float, default=30.0, help="核对超时秒（默认 30）")
    parser.add_argument("--interval", type=float, default=0.3, help="块间隔秒（默认 0.3）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if args.data_files:
        blocks = []
        for path in args.data_files:
            with open(path, encoding="ascii") as handle:
                blocks.append("".join(handle.read().split()))
    else:
        blocks = [args.blocks] + list(args.data)
    if not blocks:
        parser.error("至少要有一块数据")
    count = len(blocks)

    print(f"[1/3] 等 85 05 GET 请求（{args.wait_req:.0f}s 超时）...", file=sys.stderr)
    request = wait_get_request(args.wait_req)
    if request is None:
        print("[FAIL] 超时未等到 85 05 请求帧", file=sys.stderr)
        return 2
    print(f"      seq={request.get('seq')} frame={request.get('frame_hex', '')[:60]}...",
          file=sys.stderr)

    print(f"[2/3] 注入 {count} 块（lastblock 于第 {count - 1} 块置 1）...", file=sys.stderr)
    injected = []
    for index, data_hex in enumerate(blocks):
        last = 1 if index == count - 1 else 0
        frame = build_8505_frame(piid=args.piid, lastblock=last, block=index,
                                 data_hex=data_hex, control=int(args.control, 16),
                                 addr_bcd=args.addr, ca=args.ca)
        inject(frame.hex())
        injected.append({"block": index, "lastblock": last, "frame_hex": frame.hex()})
        if index < count - 1:
            time.sleep(args.interval)
        print(f"      block {index} lastblock={last} len={len(frame)} 已注入", file=sys.stderr)

    print(f"[3/3] 核对应答落帧（{args.verify_timeout:.0f}s）...", file=sys.stderr)
    seen = verify_reply(args.verify_timeout, count)
    result = {
        "request": request,
        "injected": injected,
        "blocks_expected": count,
        "blocks_seen_in_tx": seen,
        "ok": seen >= count,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"期望 {count} 块，tx 方向核到 {seen} 块 → {'OK' if result['ok'] else 'FAIL'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
