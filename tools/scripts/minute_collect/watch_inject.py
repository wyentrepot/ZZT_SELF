#!/usr/bin/env python3
"""simeter 分帧应答守望注入 —— 守 simeter /frames 出现 STA 698 采集请求后，
自动按序注入完整分帧帧（单轮触发，注入完成即退出）。

用途（2026-11-03 23:30 轮实机复测同款流程）：把 CCO 采集 OAD 置 missing
（simeter 脑 `POST /api/simeter/oad`，静默不回）→ STA 冻结边界自主发 698
WithList 请求（触发前缀 68310043：68|L=31|C=0x43）→ 本脚本 +delay-first 注入
首片、片间 delay-between 按序 `POST /api/simeter/send_hex` → STA 回类型 10
确认帧并末帧合并 → 查 STA 日志 `Freeze data for task 1 stored successfully`。

注意注入的是 build_split_frames_full.py 出的完整 698 帧（每行一帧），
不是 build_link_frames.py 的 APDU 片（见 README 坑 6）。

在线依赖：工作台 8790（WORKBENCH_BASE 环境变量可覆盖，默认
http://127.0.0.1:8790），且 simeter 串口已 open（wb-simeter SKILL.md §4）。

典型用法（复测一轮，详见本目录 README「最短调用路径」3c）：
    # 1) 置 missing（脑静默不回，迫使 STA 请求走注入路径）
    curl -X POST http://127.0.0.1:8790/api/simeter/oad \
        -H "Content-Type: application/json" \
        -d '{"entries":[{"oad":"20000201","missing":true},{"oad":"20010201","missing":true}]}'
    # 2) 后台守望注入（帧文件来自 build_split_frames_full.py --out）
    python3 tools/scripts/minute_collect/watch_inject.py \
        --frames /tmp/frames_split.txt --timeout 1200 > /tmp/inject_split.log 2>&1 &
    # 3) 等 15min 冻结边界 → 查 STA 日志 Freeze stored → 恢复 OAD（reset=true）

退出码：0 = 已触发并注入完成；2 = 超时未等到触发请求；1 = 其他错误
（帧文件缺失/send_hex 失败等）。时间线走 stderr，结果走 stdout（--json 时
stdout 为纯 JSON，可 `2>&1 | tee` 一并留存）。
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
#: STA 698 WithList 采集请求帧头前缀：68 | L=0x31 | C=0x43（下行请求）
DEFAULT_TRIGGER_PREFIX = "68310043"


def _req(method: str, path: str, body: dict | None = None, timeout: int = 8):
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


def log(message: str) -> None:
    """时间线/进度统一走 stderr（stdout 留给结果，--json 时为纯 JSON）。"""
    stamp = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
    print(f"[{stamp}] {message}", file=sys.stderr, flush=True)


def load_frames(path: str) -> list[str]:
    """每行一帧 hex（# 注释行与空行跳过）。"""
    frames: list[str] = []
    with open(path, encoding="ascii") as handle:
        for line in handle:
            text = "".join(line.split())
            if not text or text.startswith("#"):
                continue
            frames.append(text.upper())
    if not frames:
        raise ValueError(f"帧文件为空: {path}")
    return frames


def wait_trigger(prefix: str, timeout_s: float, poll_s: float = 0.2) -> dict | None:
    """轮询 /frames，等最新一条 rx 帧以 prefix 开头；返回触发条目或 None。

    先取 baseline seq 只看增量，避免把上一轮残留请求当触发。
    """
    _, payload = _req("GET", "/api/simeter/frames?limit=1")
    entries = payload.get("entries") or []
    cursor = entries[-1].get("seq", 0) if entries else 0
    log(f"baseline seq={cursor}, waiting for STA request {prefix}...")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(poll_s)
        _, payload = _req("GET", f"/api/simeter/frames?after_seq={cursor}&limit=20")
        for entry in payload.get("entries") or []:
            cursor = max(cursor, entry.get("seq", cursor))
            frame_hex = (entry.get("frame_hex") or entry.get("hex") or "").upper()
            if entry.get("dir") == "rx" and frame_hex.startswith(prefix):
                return entry
    return None


def inject(frame_hex: str) -> None:
    code, payload = _req("POST", "/api/simeter/send_hex", {"hex": frame_hex})
    if code != 200:
        raise RuntimeError(f"send_hex HTTP {code}: {json.dumps(payload, ensure_ascii=False)[:200]}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="守 STA 698 请求 → 按序注入分帧应答（单轮触发，联机）")
    parser.add_argument("--frames", required=True,
                        help="分帧 hex 文件（每行一帧；build_split_frames_full.py --out 生成）")
    parser.add_argument("--trigger-prefix", default=DEFAULT_TRIGGER_PREFIX,
                        help=f"触发帧前缀（默认 {DEFAULT_TRIGGER_PREFIX}，STA 698 请求 C=0x43）")
    parser.add_argument("--delay-first", type=float, default=0.30,
                        help="请求→首片间隔秒（默认 0.30，交接文档 §4：+250~300ms）")
    parser.add_argument("--delay-between", type=float, default=0.15,
                        help="片间隔秒（默认 0.15，交接文档 §4：150~200ms）")
    parser.add_argument("--timeout", type=float, default=1200.0,
                        help="等触发超时秒（默认 1200；冻结边界约 15min 一轮）")
    parser.add_argument("--once", action="store_true",
                        help="单轮触发后退出（默认即单轮触发，显式声明便于脚本化调用）")
    parser.add_argument("--json", action="store_true", help="结束时输出 JSON 时间线（进度走 stderr）")
    args = parser.parse_args()
    # 本脚本固定单轮触发（--once 为显式声明）：每轮复测都应重开进程重新取
    # baseline，避免长驻进程误消费上一轮残留请求帧。
    del args.once

    try:
        frames = load_frames(args.frames)
    except (OSError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    log(f"loaded {len(frames)} frames from {args.frames}")

    try:
        trigger = wait_trigger(args.trigger_prefix, args.timeout)
    except urllib.error.URLError as exc:
        print(f"[FAIL] 工作台不可达（{BASE}）: {exc}", file=sys.stderr)
        return 1
    if trigger is None:
        log(f"TIMEOUT no trigger in {args.timeout:.0f}s")
        if args.json:
            print(json.dumps({"ok": False, "reason": "timeout",
                              "trigger_prefix": args.trigger_prefix,
                              "timeout_s": args.timeout}, ensure_ascii=False))
        return 2

    trigger_hex = (trigger.get("frame_hex") or trigger.get("hex") or "").upper()
    log(f"TRIGGER seq={trigger.get('seq')} {trigger_hex[:50]}")
    time.sleep(args.delay_first)

    injected = []
    for index, frame_hex in enumerate(frames):
        if index:
            time.sleep(args.delay_between)
        inject(frame_hex)
        injected.append({"index": index + 1, "frame_hex": frame_hex})
        log(f"injected piece {index + 1}/{len(frames)}: {frame_hex}")
    log("DONE")

    result = {
        "ok": True,
        "once": True,
        "trigger": {"seq": trigger.get("seq"), "frame_hex": trigger_hex, "dir": trigger.get("dir")},
        "frames_file": args.frames,
        "frame_count": len(frames),
        "injected": injected,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"触发 seq={trigger.get('seq')}，注入 {len(frames)}/{len(frames)} 片 → OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
