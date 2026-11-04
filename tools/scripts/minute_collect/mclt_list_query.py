#!/usr/bin/env python3
"""`sta show mclt_list` 查询封装：经 CCO 串口会话下发并解析表体行。

req006 分钟采集验证时手搓过 ensure-cco → send("sta show mclt_list") →
等回显 → 人肉看日志 的流程（/tmp/dsh-subprocess 痕迹里同一命令反复出现），
本脚本把它固化：发命令 → 等待 → 抓该口**自己的**会话日志最新 RX 行 →
解析总行数 n 与表体（注意表头是 `n=...`，列含 mclt_id/req_ssn/last_ssn/
last_rst/frozen，行内列宽浮动，只做要素级提取并保留原始行）。

为什么日志要按 mapping_id 过滤：持续打印的板子会一直刷新全局最新日志的
mtime，全局取最新会读到别的板子的文件（identify_board.py 里踩过同坑）。

用法：
    python3 tools/scripts/minute_collect/mclt_list_query.py cco-main
    python3 tools/scripts/minute_collect/mclt_list_query.py cco-main --json
    python3 tools/scripts/minute_collect/mclt_list_query.py cco-main --grep 04A2
    python3 tools/scripts/minute_collect/mclt_list_query.py cco-main --retry 3

前置：工作台在线（默认 8790）、串口桥该 mapping_id `usable=true`、CCO 已入网。
只走 v2 门面（本机 loopback local_full 免 token）；只发只读查询，不改任何状态。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("WORKBENCH_BASE", "http://127.0.0.1:8790")
REPO = os.environ.get("WORKBENCH_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
# send 响应只回“发了多少字节”，命令回显要等板子真正处理完才落会话日志。
REPLY_WAIT_SECONDS = 3.0

#: mclt_list 表头（CCO 控制台 docmd_sta_mclt_list 打印；列宽浮动）
MCLT_LIST_HEADER = re.compile(r"mclt_id|req_ssn", re.IGNORECASE)


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


def _wait_job(job_id: str, tries: int = 30) -> dict:
    snapshot: dict = {}
    for _ in range(tries):
        _, snapshot = _req("GET", f"/api/ai/v2/jobs/{job_id}")
        if snapshot.get("job_state") in {"succeeded", "error", "failed", "cancelled"}:
            return snapshot
        time.sleep(1)
    return snapshot


def _newest_log(mapping_id: str) -> str | None:
    """只取**该 mapping_id 自己的**会话日志（防串到其他板的日志）。"""
    pattern = os.path.join(REPO, "data", "logs", "模块", "**", f"*[{mapping_id}-COM*.log")
    files = glob.glob(pattern, recursive=True)
    return max(files, key=os.path.getmtime) if files else None


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def collect_console_lines(log_path: str, after_epoch: float = 0.0) -> list[str]:
    """取会话日志里 after_epoch 之后的 RX 行（命令回显与业务日志混在一起）。"""
    lines = []
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            if "[RX]" not in raw:
                continue
            cleaned = _strip_ansi(raw.split("[RX]", 1)[1]).replace("\r", "")
            cleaned = cleaned.strip()
            if cleaned:
                lines.append(cleaned)
    return lines


def parse_mclt_list(lines: list[str]) -> dict:
    """解析 `sta show mclt_list` 输出：n（总行数）+ 表体要素行。"""
    start = None
    for index, line in enumerate(lines):
        if MCLT_LIST_HEADER.search(line):
            start = index
            break
    result: dict = {"found": start is not None, "header": None, "n": None,
                    "rows": [], "raw": []}
    if start is None:
        return result
    result["header"] = lines[start].strip()
    # n= 在表头或表头附近的独立行（如 `n=2` / `n:2`），表头前后 3 行内找
    for line in lines[max(0, start - 3): start + 3]:
        match = re.search(r"n\s*[=:]\s*(\d+)", line)
        if match:
            result["n"] = int(match.group(1))
            break
    body: list[str] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-"}:
            continue
        if stripped.startswith("[") or "info |" in stripped:
            break   # 表体结束，进入后续控制台输出
        body.append(stripped)
    for row in body:
        macs = re.findall(r"\b([0-9A-Fa-f]{12})\b", row)
        nums = re.findall(r"\b(\d{1,10})\b", row)
        frozen = re.search(r"(\d{2}[-/]\d{2}[-/]\d{2}[ ,]+\d{2}:\d{2}:\d{2})", row) \
            or re.search(r"(\d{2}:\d{2}:\d{2})", row)
        result["rows"].append({
            "mac": macs[0] if macs else None,
            "numbers": nums[:4],      # mclt_id / req_ssn / last_ssn / last_rst 之类
            "frozen_time": frozen.group(1) if frozen else None,
            "raw": row,
        })
    result["raw"] = body
    return result


def query_mclt_list(mapping_id: str, *, stop: bool = True, retry: int = 2) -> dict:
    """ensure → send("sta show mclt_list") → 抓日志解析。返回 0=成功。"""
    _, status = _req("GET", "/api/serial-bridge/status")
    ports = {port.get("id"): port for port in status.get("ports") or []}
    port = ports.get(mapping_id)
    if port is None:
        raise RuntimeError(f"status 里没有 {mapping_id}")
    usable = port.get("usable", port.get("workbench_ready"))
    if not usable:
        raise RuntimeError(f"{mapping_id} 不可用：errors={port.get('errors')}（先修桥）")

    _, ensure = _req("POST", "/api/ai/v2/module-actions",
                     {"action": "ensure", "mapping_id": mapping_id})
    envelope = _wait_job(ensure.get("job_id"))
    payload = envelope.get("result") or {}
    session_id = payload.get("session_id")
    if not session_id:
        raise RuntimeError(f"ensure 未拿到 session_id：{json.dumps(envelope, ensure_ascii=False)[:300]}")
    owned = bool(payload.get("owned"))

    try:
        for attempt in range(1, retry + 1):
            before = time.time()
            code, job = _req("POST", "/api/ai/v2/module-actions",
                             {"action": "send", "session_id": session_id,
                              "text": "sta show mclt_list\r\n"})
            if code != 202:
                raise RuntimeError(f"send 失败：HTTP {code} {job.get('detail')}")
            time.sleep(REPLY_WAIT_SECONDS)
            log_path = _newest_log(mapping_id)
            if not log_path:
                raise RuntimeError("找不到会话日志（检查 <ROOT>/data/logs/模块/）")
            if f"[{mapping_id}-" not in os.path.basename(log_path):
                raise RuntimeError(f"日志与端口不符（{os.path.basename(log_path)}）")
            parsed = parse_mclt_list(collect_console_lines(log_path))
            if parsed["found"]:
                parsed["mapping_id"] = mapping_id
                parsed["session_id"] = session_id
                parsed["session_owned"] = owned
                parsed["log"] = log_path
                parsed["attempt"] = attempt
                return parsed
            if attempt < retry:
                time.sleep(2)   # 回显可能比日志落盘慢一拍，稍候重发
        raise RuntimeError("重试后仍未在日志中抓到 mclt_list 表头")
    finally:
        if stop and owned:   # 只释放自己开的会话
            _req("POST", "/api/ai/v2/module-actions",
                 {"action": "stop", "session_id": session_id, "force": True})


def main() -> int:
    parser = argparse.ArgumentParser(description="查询并解析 CCO `sta show mclt_list`")
    parser.add_argument("mapping_id", nargs="?", default="cco-main",
                        help="CCO 逻辑口，默认 cco-main")
    parser.add_argument("--json", action="store_true", dest="json_out", help="输出 JSON")
    parser.add_argument("--grep", help="只打印含该子串的表体原始行（不区分大小写）")
    parser.add_argument("--retry", type=int, default=2, help="回显抓取重试次数（默认 2）")
    parser.add_argument("--no-stop", action="store_true",
                        help="不释放自己开的会话（复用他人会话一律不释放）")
    args = parser.parse_args()
    try:
        result = query_mclt_list(args.mapping_id, stop=not args.no_stop, retry=args.retry)
    except RuntimeError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    rows = result["rows"]
    if args.grep:
        needle = args.grep.lower()
        rows = [row for row in rows if needle in (row["raw"] or "").lower()]
    if args.json_out:
        result["rows"] = rows
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"mclt_list · {args.mapping_id}（{os.path.basename(result['log'])}，第 {result['attempt']} 次抓取）")
    print(f"  表头 : {result['header']}")
    print(f"  总数 n : {result['n']}   实际解析行 : {len(rows)}")
    for row in rows:
        print(f"    - mac={row['mac']} frozen={row['frozen_time']} nums={row['numbers']}")
        print(f"      raw: {row['raw']}")
    if result["n"] is not None and len(rows) != result["n"]:
        print(f"  [WARN] n={result['n']} 与解析行数 {len(rows)} 不一致，核对原始行", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
