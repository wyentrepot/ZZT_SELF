# -*- coding: utf-8 -*-
"""CCO 档案查询 + 全部删除驱动脚本（每批 ≤16）。

流程：
1) 循环 10H-F2 查询从节点档案（每批 16 条，从 start=0 递增直到取完总数）；
2) 汇总全部从节点地址；
3) 循环 11H-F2 删除从节点档案（每批 ≤16）。

通过 REST API 对已连接的模拟集中器服务（默认 http://127.0.0.1:8766/api/simcon）
下发命令（服务即“集中器通信状态”，串口已由服务打开）。

安全默认：--dry-run 只构帧展示（调 /api/simcon/build，不发串口），
确认无误后再用 --apply 真正执行删除。

用法：
    python tools/scripts/cco_archive_clear.py --dry-run          # 只构帧预览（默认）
    python tools/scripts/cco_archive_clear.py --apply            # 实际执行查询+删除
    python tools/scripts/cco_archive_clear.py --dry-run --query-count 16
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8766/api/simcon"
MAX_BATCH = 16  # 查询/删除每批上限（用户要求 ≤16）


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {path}: {detail}") from e


def _build_preview(base: str, send: dict) -> str:
    """调 /api/simcon/build 只算帧字节，不触串口。"""
    body = {
        "afn": send["afn"], "fn": send["fn"],
        "params": send.get("params", {}),
        "direction": send.get("direction", "down"),
        "profile": "anhui",
        "seq": 1,
    }
    out = _post(base, "/build", body)
    return out.get("hex", "")


def query_all(base: str, query_count: int, apply: bool) -> list[str]:
    """10H-F2 分批查询全部从节点档案地址。返回地址列表。"""
    addrs: list[str] = []
    start = 0
    total = None
    page = 0
    while True:
        send = {"afn": "10", "fn": "F2", "params": {"start": start, "count": query_count}}
        hexstr = _build_preview(base, send)
        print(f"[查询 10H-F2] start={start} count={query_count}")
        print(f"    帧: {hexstr}")
        if not apply:
            print("    (dry-run：未发送)")
            # dry-run 无法拿到真实总数，按 start 递增模拟 2 页后停止
            page += 1
            if page >= 2:
                break
            start += query_count
            continue
        # 实际执行
        resp = _post(base, "/step", {"send": send, "profile": "anhui", "name": f"查询档案{start}"})
        parsed = resp.get("step", {}).get("parsed", {}) or {}
        items = parsed.get("items", [])
        # 从 items 里取“从节点地址”字段
        page_addrs = []
        for it in items:
            name = it.get("name", "")
            if "从节点地址" in name or (name and it.get("hex") and len(it.get("hex", "").replace(" ", "")) == 12):
                page_addrs.append(it.get("value") or it.get("hex", "").replace(" ", ""))
        # 响应头：从节点总数量
        for it in items:
            if "总数量" in it.get("name", ""):
                try:
                    total = int(it.get("value"))
                except (TypeError, ValueError):
                    pass
        addrs.extend(page_addrs)
        print(f"    本页地址: {page_addrs}")
        if total is not None:
            print(f"    从节点总数量: {total}，已收集: {len(addrs)}")
        if total is not None and len(addrs) >= total:
            break
        if len(page_addrs) < query_count:
            break  # 本页不足一页，说明取完
        start += query_count
        page += 1
        if page >= 200:  # 防御：最多 200 页
            break
    return addrs


def delete_batches(base: str, addrs: list[str], apply: bool) -> None:
    """11H-F2 分批删除，每批 ≤MAX_BATCH。"""
    if not addrs:
        print("[删除] 无档案地址，跳过")
        return
    for i in range(0, len(addrs), MAX_BATCH):
        batch = addrs[i:i + MAX_BATCH]
        send = {"afn": "11", "fn": "F2", "params": {"meters": batch}}
        hexstr = _build_preview(base, send)
        print(f"[删除 11H-F2] 批 {i // MAX_BATCH + 1}: {len(batch)} 个")
        print(f"    帧: {hexstr}")
        print(f"    地址: {batch}")
        if not apply:
            print("    (dry-run：未发送)")
            continue
        resp = _post(base, "/step", {"send": send, "profile": "anhui", "name": f"删除档案批{i // MAX_BATCH + 1}"})
        step = resp.get("step", {})
        print(f"    结果: {step.get('result')} | {step.get('reason')}")


def main() -> int:
    ap = argparse.ArgumentParser(description="CCO 档案查询+全部删除（每批≤16）")
    ap.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run 只构帧）")
    ap.add_argument("--query-count", type=int, default=16,
                    help="每次查询条数，默认16（≤16）")
    ap.add_argument("--base", default=BASE, help="simcon API 地址")
    args = ap.parse_args()

    if args.query_count > 16:
        print(f"[ERROR] query-count 不得超过 16，当前 {args.query_count}", file=sys.stderr)
        return 2

    print("=" * 70)
    print(f"  CCO 档案 查询+全部删除  模式: {'APPLY(真实执行)' if args.apply else 'DRY-RUN(只构帧)'}")
    print(f"  每批上限: {MAX_BATCH}   查询条数/批: {args.query_count}")
    print("=" * 70)

    # 先探测服务连通性
    try:
        status = _post(args.base, "/step", {"send": {"afn": 0}, "recv_only": True, "expect_timeout": 0.5})
        print(f"  服务连通 OK: {status.get('step', {}).get('result', '')}")
    except Exception as e:
        print(f"[ERROR] 无法连接模拟集中器服务 {args.base}: {e}", file=sys.stderr)
        print("  请确认模块日志/集中器服务(端口 8766)已启动且串口已连接。", file=sys.stderr)
        return 1

    if args.apply:
        confirm = input("即将真实删除 CCO 全部从节点档案，输入 YES 继续: ").strip()
        if confirm != "YES":
            print("已取消。")
            return 0

    # 1) 查询全部
    print("\n[阶段1] 查询全部从节点档案")
    addrs = query_all(args.base, args.query_count, apply=args.apply)
    print(f"\n  查询汇总: 共 {len(addrs)} 个从节点地址")
    for i, a in enumerate(addrs):
        print(f"    [{i + 1}] {a}")

    # 2) 删除全部
    print("\n[阶段2] 删除全部从节点档案")
    delete_batches(args.base, addrs, apply=args.apply)

    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
