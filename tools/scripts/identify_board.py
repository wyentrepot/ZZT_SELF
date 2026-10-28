#!/usr/bin/env python3
"""识别模块板身份与固件版本——**不重启板子**。

做法：向模块串口会话发控制台只读命令 `version` 与 `fcversion`，从会话日志里
抓回显并解析。这两条命令两端固件都有（STA: bsp/venus/sdk/src/cmd.c 的
CMDTAB_ENTRY；CCO: bsp/venus/sdk/libdepend/cmd.c 的 CMDTAB_ENTRY）。

为什么不用 reboot 读横幅：
- reboot 会打断该口正在跑的任务、CCO 重启还要重建入网，代价大；
- `version` / `fcversion` 是纯只读查询，输出比横幅字段更全（含地区模式与硬件版本）。

判定板身份的关键字段：
- `diqu name`  / `diqu mode`  → 地区（如 HE_BEI_MODE / 0x23）
- `Hardware`   / `hversion`   → 硬件版本
- `sversion` + `ver_date`     → 大版本（sv）与日期
- `fc sversion` + `fc ver_date` → 小版本（isv）与日期
- `temperature` **存在与否**   → 该行只在 `#if defined(VENUS_V7)` 编译分支里存在，
  所以：有它 = 板上运行的是 **v7 分支固件**，没有 = v2 分支固件。
  **注意这是固件分支，不是硬件自报代次**；因 v2/v7 固件不能互升（硬件代次必须与
  件分支配套），才可把它当作硬件代次的代理判据。

用法：
    python3 tools/scripts/identify_board.py sta-main
    python3 tools/scripts/identify_board.py cco-main --json
    python3 tools/scripts/identify_board.py sta-main --no-stop   # 复用已有会话时不释放

前置：工作台在线（默认 8790）、串口桥 status 里该 mapping_id 的 `usable=true`。
本脚本只走 v2 门面（本机 loopback local_full 免 token）；串口独占由工作台会话机制保证。
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
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
LOG_GLOB = os.path.join(REPO, "data", "logs", "模块", "**", "*.log")

# 命令回显要等板子真正处理完；实测 1s 内多半就回，给 3s 稳妥（不影响串口独占语义）。
REPLY_WAIT_SECONDS = 3.0

FIELD_PATTERNS = {
    "software": re.compile(r"Software\s*:\s*(\S+)"),
    "build_time": re.compile(r"Build time\s*:\s*(.+?)\s*$", re.MULTILINE),
    "hardware": re.compile(r"Hardware\s*:\s*(\S+)"),
    "hversion": re.compile(r"hversion\s*:\s*([0-9a-fA-F]+)"),
    "sversion": re.compile(r"(?<!fc )sversion\s*:\s*([0-9a-fA-F]+)"),
    "ver_date": re.compile(r"(?<!fc )ver_date\s*:\s*((?:\d{2}|\d{4})-\d{1,2}-\d{1,2})"),
    "diqu_mode": re.compile(r"diqu mode\s*:\s*([0-9a-fA-F]+)"),
    "diqu_name": re.compile(r"diqu name\s*:\s*(\S+)"),
    "fc_sversion": re.compile(r"fc sversion\s*:\s*([0-9a-fA-F]+)"),
    "fc_ver_date": re.compile(r"fc ver[\s_]*date\s*:\s*(\d{4}-\d{1,2}-\d{1,2})"),
    "temperature": re.compile(r"temperature\s*:\s*(-?\d+)"),
}


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
    """只看**该 mapping_id 自己的**会话日志。

    不能取全局最新：持续打印的板子（STA 的 beacon/工厂日志）会把自己的日志 mtime
    一直刷新，全局取最新就会读到别的板子的文件 —— 实测过，会把 cco 读成 sta 的身份。
    会话日志名形如 `20261028-212753_[cco]_[sta-main-COM9--run-hplc-serial-sta-main].log`。
    """
    pattern = os.path.join(REPO, "data", "logs", "模块", "**", f"*[{mapping_id}-COM*.log")
    files = glob.glob(pattern, recursive=True)
    return max(files, key=os.path.getmtime) if files else None


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def collect_console_lines(log_path: str) -> list[str]:
    """取会话日志里的 RX 行（命令回显与业务日志混在一起，按关键字再筛）。"""
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    lines = []
    for raw in text.splitlines():
        if "[RX]" not in raw:
            continue
        cleaned = _strip_ansi(raw.split("[RX]", 1)[1]).replace("\r", "")
        lines.append(cleaned.strip())
    return lines


def _normalise_date(value: str) -> str:
    """统一成 YYYY-M-D。

    STA 的 `ver_date` 是四位年（2026-10-20），CCO 的是两位年（26-10-15，
    来自 `20%.2x-...` 之前的老写法），两种都要能读。
    """
    year, month, day = value.split("-")
    if len(year) == 2:
        year = "20" + year
    return f"{year}-{int(month)}-{int(day)}"


def parse_identity(lines: list[str]) -> dict:
    joined = "\n".join(lines)
    result: dict = {}
    for name, pattern in FIELD_PATTERNS.items():
        match = pattern.search(joined)
        if not match:
            continue
        value = match.group(1).strip()
        if name in {"ver_date", "fc_ver_date"}:
            try:
                value = _normalise_date(value)
            except ValueError:
                pass
        result[name] = value
    # temperature 行只存在于 VENUS_V7 编译分支 → 推的是"固件分支"，不是硬件自报代次。
    # 因为 v2/v7 固件不能互升（件必须与硬件代次配套），固件分支可作硬件代次的代理判据。
    result["firmware_branch"] = "v7" if "temperature" in result else "v2"
    result["hardware_generation_inferred"] = result["firmware_branch"]
    result["inference_basis"] = (
        "fcversion 输出了 temperature 行（该行仅存在于 #if defined(VENUS_V7) 分支）"
        if "temperature" in result
        else "fcversion 未输出 temperature 行（v2 分支不编入该行）"
    ) + "；v2/v7 固件不能互升，故固件分支可作硬件代次的代理判据"
    return result


STA_SHOW_HEADER = re.compile(r"index\s+tei\s+lv\s+pxy\s+ty", re.IGNORECASE)


def parse_sta_show(lines: list[str]) -> dict:
    """解析 CCO 控制台 `sta show` 的表格。

    表头（固件 bsp 侧 docmd_sta 打印）：
      index tei lv pxy ty p phase fcl md fcode mac version date ccode isv isv_date ontime
    行内列宽随内容浮动、'-' 占位，逐列对齐不可靠 —— 所以只做**要素级提取**
    （mac / version / isv / 版本日期 / 入网时长），并保留原始行供人工核对。
    """
    start = None
    for index, line in enumerate(lines):
        if STA_SHOW_HEADER.search(line):
            start = index
            break
    if start is None:
        return {"header": None, "rows": [], "nodes": [], "raw": []}

    header = lines[start].strip()
    body: list[str] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-"}:
            continue
        if stripped.startswith("[") or "| info |" in stripped or "info |" in stripped:
            break
        body.append(stripped)

    nodes = []
    for row in body:
        macs = re.findall(r"\b([0-9A-Fa-f]{12})\b", row)
        versions = re.findall(r"\b([0-9A-Fa-f]{6})\b", row)
        dates = re.findall(r"\b(\d{2}/\d{2}/\d{2}|\d{2}-\d{2}-\d{2})\b", row)
        nodes.append({
            "mac": macs[0] if macs else None,
            "version": versions[0] if versions else None,
            "version_date": dates[0] if dates else None,
            "isv": versions[1] if len(versions) > 1 else None,
            "isv_date": dates[1] if len(dates) > 1 else None,
            "role": "CCO" if " CCO " in row or row.split()[:1] and "CCO" in row else "STA",
            "raw": row,
        })
    return {"header": header, "rows": body, "nodes": nodes, "raw": body}


def identify(mapping_id: str, *, stop: bool = True, json_out: bool = False,
             sta_show: bool = False) -> int:
    """识别一个 mapping_id 对应的板子。返回 0=成功，1=失败（原因写 stderr）。"""
    _, status = _req("GET", "/api/serial-bridge/status")
    ports = {port.get("id"): port for port in status.get("ports") or []}
    port = ports.get(mapping_id)
    if port is None:
        print(f"[FAIL] status 里没有 {mapping_id}", file=sys.stderr)
        return 1
    # usable 是 2026-10-28 起 status 的按口判定字段；老版本工作台没有就看 workbench_ready
    usable = port.get("usable")
    if usable is None:
        usable = port.get("workbench_ready")
    if not usable:
        print(
            f"[FAIL] {mapping_id} 不可用：errors={port.get('errors')}（先修桥，别硬开）",
            file=sys.stderr,
        )
        return 1

    _, ensure = _req(
        "POST", "/api/ai/v2/module-actions", {"action": "ensure", "mapping_id": mapping_id}
    )
    envelope = _wait_job(ensure.get("job_id"))
    payload = envelope.get("result") or {}
    session_id = payload.get("session_id")
    if not session_id:
        print(f"[FAIL] ensure 未拿到 session_id：{json.dumps(envelope, ensure_ascii=False)[:300]}",
              file=sys.stderr)
        return 1
    owned = bool(payload.get("owned"))

    try:
        commands = ["version", "fcversion"]
        if sta_show:
            commands.append("sta show")   # 仅 CCO 有；STA 无此命令（会回错误提示）
        for command in commands:
            code, job = _req(
                "POST",
                "/api/ai/v2/module-actions",
                {"action": "send", "session_id": session_id, "text": command + "\r\n"},
            )
            if code != 202:
                print(f"[FAIL] 发送 {command} 失败：HTTP {code} {job.get('detail')}", file=sys.stderr)
                return 1
            # 必须等：send 的响应只回“发了多少字节”，命令回显落在会话日志里。
            time.sleep(REPLY_WAIT_SECONDS)

        log_path = _newest_log(mapping_id)
        if not log_path:
            print(f"[FAIL] 找不到 {mapping_id} 的会话日志（检查 <ROOT>/data/logs/模块/）", file=sys.stderr)
            return 1
        if f"[{mapping_id}-" not in os.path.basename(log_path):
            print(f"[FAIL] 日志与端口不符（{os.path.basename(log_path)}），拒绝输出可能错板的身份",
                  file=sys.stderr)
            return 1
        console_lines = collect_console_lines(log_path)
        identity = parse_identity(console_lines)
        if sta_show:
            identity["sta_show"] = parse_sta_show(console_lines)
        identity["mapping_id"] = mapping_id
        identity["session_id"] = session_id
        identity["session_owned"] = owned
        identity["log"] = log_path

        if not identity.get("software") and not identity.get("sversion"):
            print(
                "[FAIL] 没有抓到任何 version 回显：板子控制台可能不可交互（或该口是纯日志口）",
                file=sys.stderr,
            )
            return 1

        if json_out:
            print(json.dumps(identity, ensure_ascii=False, indent=2))
        else:
            print(f"板身份 · {mapping_id}（{os.path.basename(log_path)}）")
            print(f"  地区      : {identity.get('diqu_name', '未知')}（diqu mode {identity.get('diqu_mode', '?')}）")
            print(f"  硬件      : {identity.get('hardware', '未知')}  hversion {identity.get('hversion', '?')}")
            print(f"  大版本 sv : {identity.get('sversion', '?')}  日期 {identity.get('ver_date', '?')}")
            print(f"  小版本 isv: {identity.get('fc_sversion', '?')}  日期 {identity.get('fc_ver_date', '?')}")
            print(f"  固件分支  : {identity['firmware_branch']}"
                  f"（硬件代次代理：{identity['hardware_generation_inferred']}）")
            print(f"              依据：{identity['inference_basis']}")
            print(f"  SDK/软件  : {identity.get('software', '?')}  编译于 {identity.get('build_time', '?')}")
            show = identity.get("sta_show")
            if show:
                print(f"  表内节点  : {len(show.get('nodes') or [])} 个（sta show）")
                for node in show.get("nodes") or []:
                    print(f"    - {node.get('role')} mac={node.get('mac')} sv={node.get('version')}"
                          f" isv={node.get('isv')} isv_date={node.get('isv_date')}")
        return 0
    finally:
        # 只释放自己开的会话；复用别人的会话不动（避免踩正在用的口）
        if stop and owned:
            _req("POST", "/api/ai/v2/module-actions",
                 {"action": "stop", "session_id": session_id, "force": True})


def main() -> int:
    parser = argparse.ArgumentParser(description="识别模块板身份与固件版本（不重启）")
    parser.add_argument("mapping_id", help="逻辑口，如 sta-main / cco-main")
    parser.add_argument("--json", action="store_true", dest="json_out", help="输出 JSON")
    parser.add_argument("--sta-show", action="store_true",
                        help="额外发 `sta show`（仅 CCO 支持），列出表内节点与其版本")
    parser.add_argument("--no-stop", action="store_true",
                        help="不释放自己开的会话（默认释放；复用他人会话一律不释放）")
    args = parser.parse_args()
    return identify(args.mapping_id, stop=not args.no_stop, json_out=args.json_out,
                    sta_show=args.sta_show)


if __name__ == "__main__":
    raise SystemExit(main())
