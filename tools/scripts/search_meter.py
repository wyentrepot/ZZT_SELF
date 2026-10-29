#!/usr/bin/env python3
"""15 分钟搜表（从节点主动注册）→ 结果写入档案区。

流程依据（库：协议层/03_QGDW10376.2_全帧类型.md §11 F5 + 业务层/86_档案自动同步_业务.md 6.4）：
  1. 暂停路由（AFN=12H-F2，无数据单元）
  2. 激活从节点主动注册（AFN=11H-F5）：
       开始时间 BCD 6（秒分时日月年）/ 持续时间 BIN 2（单位 min）/ 重发次数 BIN 1 / 随机时间片个数 BIN 1（片=150ms）
  3. 接收上报从节点信息（AFN=06H-F1）或上报从节点信息及设备类型（AFN=06H-F4）
  4. 收到上报从节点事件（AFN=06H-F3）表示搜表任务结束 → 提前收工
  5. 收工发终止从节点主动注册（AFN=11H-F6）兜底

搜到的档案写进档案区（`tools/scripts/archive_pool.py` 的同一张表），下次测试可直接导出下发。

用法：
    python3 tools/scripts/search_meter.py --minutes 15            # 真跑（占 simcon 约 N 分钟）
    python3 tools/scripts/search_meter.py --minutes 15 --dry-run  # 只构帧预检，不发
    python3 tools/scripts/search_meter.py --minutes 15 --no-pause-route
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "libs"))

BASE = os.environ.get("WORKBENCH_BASE", "http://127.0.0.1:8790")
REPO = os.environ.get("WORKBENCH_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
POOL = os.path.join(REPO, "data", "archive", "device-pool.tsv")

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


def _req(method: str, path: str, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"detail": raw[:300]}


def _wait_job(job_id: str, tries: int = 40) -> dict:
    snapshot: dict = {}
    for _ in range(tries):
        _, snapshot = _req("GET", f"/api/ai/v2/jobs/{job_id}")
        if snapshot.get("job_state") in {"succeeded", "error", "failed", "cancelled"}:
            return snapshot
        time.sleep(1)
    return snapshot


def preflight(minutes: int) -> tuple[str, str]:
    """本地构帧预检（不碰串口）：返回 (12H-F2 hex, 11H-F5 hex) 供人工核对。"""
    from parser_lib.adapters.adapter_10376 import encode_app_data
    from sim_concentrator.frame_codec import build_13762_frame

    now = datetime.now()
    time_params = {
        "sec": now.second, "min": now.minute, "hour": now.hour,
        "day": now.day, "mon": now.month, "year": now.year % 100,
    }
    f5_params = {**time_params, "duration": minutes, "retry": 1, "slices": 16}
    pause = build_13762_frame(0x12, 2, encode_app_data(0x12, 2, {}), direction="down")
    activate = build_13762_frame(0x11, 5, encode_app_data(0x11, 5, f5_params), direction="down")
    return pause.hex(" ").upper(), activate.hex(" ").upper()


def _newest_simcon_log() -> str:
    files = glob.glob(os.path.join(REPO, "data", "logs", "simcon", "sc-*.jsonl"))
    if not files:
        raise SystemExit("找不到 simcon 帧日志（data/logs/simcon/sc-*.jsonl）")
    return max(files, key=os.path.getmtime)


NODE_ADDR = re.compile(r"^从节点(\d+)地址$")
NODE_PROTO = re.compile(r"^从节点(\d+)协议类型$")
NODE_DTYPE = re.compile(r"^从节点(\d+)设备类型$")
PROTO_CODE = re.compile(r"0x([0-9A-Fa-f]{2})")
DTYPE_CODE = re.compile(r"0x([0-9A-Fa-f]{2})")


def extract_nodes(record: dict) -> list[dict]:
    """从一条 06H-F1/F4 帧的 parsed.items 里抽从节点（地址/协议/设备类型）。"""
    items = ((record.get("parsed") or {}).get("items") or [])
    found: dict[str, dict] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        for pattern, key in ((NODE_ADDR, "addr"), (NODE_PROTO, "proto"), (NODE_DTYPE, "dtype")):
            match = pattern.match(name)
            if not match:
                continue
            index = match.group(1)
            slot = found.setdefault(index, {"addr": "", "proto": "", "dtype": ""})
            if key == "addr":
                slot["addr"] = value.upper()
            elif key == "proto":
                code = PROTO_CODE.search(value)
                slot["proto"] = code.group(1).upper() if code else ""
            else:
                code = DTYPE_CODE.search(value)
                slot["dtype"] = code.group(1).upper() if code else ""
    return [node for node in found.values() if len(node["addr"]) == 12]


def _follow_offsets() -> dict[str, int]:
    """当前所有 simcon journal 的已读偏移（收集开始时把已有内容都算"已读"）。"""
    return {path: os.path.getsize(path)
            for path in glob.glob(os.path.join(REPO, "data", "logs", "simcon", "sc-*.jsonl"))}


def _read_new_lines(offsets: dict[str, int]) -> list[str]:
    """读取所有 journal 的新增行，并维护偏移。

    为什么不能"开始前挑一个文件读到底"：simcon 每开一次会话就新建一个 sc-*.jsonl，
    实测 23:13 那轮搜表把帧写进了新文件，而采集端还盯着旧文件，于是 0 结果。
    """
    lines: list[str] = []
    for path in glob.glob(os.path.join(REPO, "data", "logs", "simcon", "sc-*.jsonl")):
        offset = offsets.get(path, 0)
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size <= offset:
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            chunk = handle.read()
            offsets[path] = offset + len(chunk.encode("utf-8"))
        lines.extend(chunk.splitlines())
    return lines


def collect(log_path: str, start_offset: int, minutes: int) -> tuple[list[dict], str]:
    """尾随 simcon 帧日志，收集 06H-F1/F4 的从节点；06H-F3 视为搜表结束。"""
    nodes: list[dict] = []
    seen: set[tuple] = set()
    end_reason = "时间到"
    deadline = time.time() + minutes * 60
    next_report = time.time() + 60
    offsets = _follow_offsets()
    finished = False
    while time.time() < deadline and not finished:
        new_lines = _read_new_lines(offsets)
        if not new_lines:
            time.sleep(1.0)
            if time.time() >= next_report:
                print(f"  [进度] 已收集 {len(nodes)} 个从节点"
                      f"（剩余 {int((deadline - time.time()) / 60)} 分钟）", flush=True)
                next_report = time.time() + 60
            continue
        for line in new_lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("dir") != "rx" or str(record.get("afn")) != "06":
                continue
            fn = str(record.get("fn") or "").upper()
            if fn == "F3":
                end_reason = "收到 06H-F3（搜表结束）"
                finished = True
                break
            if fn not in {"F1", "F4"}:
                continue
            key = (record.get("frame_hex"), fn)
            if key in seen:
                continue
            seen.add(key)
            for node in extract_nodes(record):
                node["fn"] = fn
                nodes.append(node)
    return nodes, end_reason



def listen_chunk(mapping_id: str, seconds: float) -> dict:
    """提交一个"只等一帧"的验证任务，占住串口 duration 秒。

    为什么必须这么做：验证任务结束就关串口，此后 CCO 的 06H 主动上报没人接、
    帧不会进 journal，采集端只能干等（2026-10-28 第一轮 15 分钟零结果的真因）。
    simcon 验证有看门狗（缺省 240s，WORKBENCH_VERIFY_TIMEOUT_S 可覆盖），故分片串联。
    """
    code, job = _req("POST", "/api/ai/v2/verification-runs", {"task": {
        "mapping_id": mapping_id, "id": "listen-uplink", "fail_fast": False,
        "steps": [{
            "name": f"监听主动上报 {int(seconds)}s",
            "recv_only": True,
            "expect": {"afn": "06", "fn": "F4"},
            "expect_timeout": float(seconds),
        }],
    }})
    if code != 202:
        return {"error": f"HTTP {code} {job.get('detail')}"}
    return _wait_job(job.get("job_id"), tries=int(seconds) + 20)


def main() -> int:
    parser = argparse.ArgumentParser(description="15 分钟搜表 → 写入档案区")
    parser.add_argument("--minutes", type=int, default=15, help="注册持续时间（分钟）")
    parser.add_argument("--mapping-id", default="simcon", help="simcon 逻辑口（默认 simcon）")
    parser.add_argument("--retry", type=int, default=1, help="从节点重发次数")
    parser.add_argument("--slices", type=int, default=16, help="随机等待时间片个数（片=150ms）")
    parser.add_argument("--pool", default=POOL, help="档案区文件")
    parser.add_argument("--dry-run", action="store_true", help="只做构帧预检，不发不占串口")
    parser.add_argument("--no-pause-route", action="store_true", help="跳过 12H-F2 暂停路由")
    parser.add_argument("--listen-chunk", type=int, default=180,
                        help="监听分片秒数（受 simcon 看门狗 240s 限制，默认 180）")
    args = parser.parse_args()

    pause_hex, activate_hex = preflight(args.minutes)
    print("构帧预检（不占串口）")
    if not args.no_pause_route:
        print(f"  12H-F2 暂停路由 : {pause_hex}")
    print(f"  11H-F5 激活注册 : {activate_hex}")
    print(f"  持续时间 {args.minutes} 分钟 / 重发 {args.retry} / 时间片 {args.slices}")
    if args.dry_run:
        print("（--dry-run：到此为止，未发任何帧）")
        return 0

    _, status = _req("GET", "/api/serial-bridge/status")
    port = {item.get("id"): item for item in status.get("ports") or []}.get(args.mapping_id) or {}
    if not (port.get("usable") if port.get("usable") is not None else port.get("workbench_ready")):
        print(f"[FAIL] {args.mapping_id} 不可用：errors={port.get('errors')}", file=sys.stderr)
        return 1

    log_path = _newest_simcon_log()
    start_offset = os.path.getsize(log_path)

    steps = []
    if not args.no_pause_route:
        steps.append({"name": "暂停路由 12H-F2", "send": {"afn": "12", "fn": "F2", "params": {}}})
    now = datetime.now()
    steps.append({
        "name": f"激活从节点主动注册 {args.minutes}min",
        "send": {"afn": "11", "fn": "F5", "params": {
            "sec": now.second, "min": now.minute, "hour": now.hour,
            "day": now.day, "mon": now.month, "year": now.year % 100,
            "duration": args.minutes, "retry": args.retry, "slices": args.slices,
        }},
    })
    code, job = _req("POST", "/api/ai/v2/verification-runs",
                     {"task": {"mapping_id": args.mapping_id, "id": "search-meter",
                               "fail_fast": False, "steps": steps}})
    if code != 202:
        print(f"[FAIL] 提交搜表失败：HTTP {code} {job.get('detail')}", file=sys.stderr)
        return 1
    name = f"搜表{args.minutes}min-{datetime.now().strftime('%Y%m%d-%H%M')}"
    print(f"已提交搜表 job={job.get('job_id')}（{name}），开始收集 {args.minutes} 分钟…", flush=True)
    envelope = _wait_job(job.get("job_id"))
    print(f"下发阶段：{envelope.get('job_state')} {envelope.get('summary')}", flush=True)

    # 边监听边收集：每片 recv_only 任务占住串口 chunk 秒，期间帧会进 journal。
    chunk = max(30, min(200, args.listen_chunk))
    deadline = time.time() + args.minutes * 60
    nodes: list[dict] = []
    end_reason = "时间到"
    seen: set[tuple] = set()
    offsets = _follow_offsets()
    while time.time() < deadline and end_reason == "时间到":
        budget = min(chunk, int(deadline - time.time()))
        if budget < 15:
            break
        result = listen_chunk(args.mapping_id, budget)
        for line in _read_new_lines(offsets):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("dir") != "rx" or str(record.get("afn")) != "06":
                continue
            fn = str(record.get("fn") or "").upper()
            if fn == "F3":
                end_reason = "收到 06H-F3（搜表结束）"
                break
            if fn not in {"F1", "F4"}:
                continue
            key = (record.get("frame_hex"), fn)
            if key in seen:
                continue
            seen.add(key)
            for node in extract_nodes(record):
                node["fn"] = fn
                nodes.append(node)
        print(f"  [进度] 已收集 {len(nodes)} 个从节点"
              f"（剩余 {int(max(0, deadline - time.time()) / 60)} 分钟；"
              f"监听片结果 {str(result.get('job_state') or result.get('error'))[:40]}）", flush=True)


    # 兜底终止主动注册（失败不影响已收集结果）
    _req("POST", "/api/ai/v2/verification-runs",
         {"task": {"mapping_id": args.mapping_id, "id": "stop-register", "fail_fast": False,
                   "steps": [{"name": "终止主动注册 11H-F6",
                              "send": {"afn": "11", "fn": "F6", "params": {}}}]}})

    sys.path.insert(0, os.path.join(REPO, "tools", "scripts"))
    from archive_pool import render_table, upsert, load_pool  # noqa: E402

    added = updated = 0
    seen_addr: set[str] = set()
    for node in nodes:
        if node["addr"] in seen_addr:
            continue
        seen_addr.add(node["addr"])
        result = upsert(args.pool, addr=node["addr"], proto=node.get("proto") or "02",
                        dtype=node.get("dtype") or "01", source=name,
                        note=f"06H-{node.get('fn')}")
        added += result == "added"
        updated += result == "updated"

    print(f"\n搜表结束：{end_reason}；收到 {len(nodes)} 条上报，去重 {len(seen_addr)} 个从节点")
    print(f"档案区写入：新增 {added}，更新 {updated} → {args.pool}\n")
    print(render_table(load_pool(args.pool)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
