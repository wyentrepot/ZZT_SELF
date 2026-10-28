#!/usr/bin/env python3
"""档案区：设备档案表（下发"添加从节点/设备池"的来源）。

设计口径（2026-10-28 用户定）：
- **一张表就够**，人工可维护；每次测试从这里导出，再下发给测试 CCO；
- 搜表（从节点主动注册）搜到的档案可以先写进表里，下次直接先下发档案，
  这些设备可能仍在线，等 CCO 组网即可；
- 表是**唯一真相源**，脚本只负责 upsert / 导出，不擅自删行。

存储：`<WORKBENCH_ROOT>/data/archive/device-pool.tsv`（TSV，Excel 可直接开）
列：从节点地址 / 协议类型 / 设备类型 / 来源 / 首次发现 / 最近发现 / 出现次数 / 备注

导出为"添加从节点"（1376.2 AFN=11H-F1）的数据单元：
    从节点数量n(1B) + [从节点地址(BCD 6B) + 通信协议类型(1B)] × n
字段依据（库：协议层/03_QGDW10376.2_全帧类型.md §11 路由管理 F1 添加从节点）：
  从节点的数量n BIN 1 / 从节点地址 BCD 6 / 从节点通信协议类型 BIN 1
协议类型取值：00H 透明传输 / 01H DL/T645-1997 / 02H DL/T645-2007 / 03H DL/T698.45

用法：
    python3 tools/scripts/archive_pool.py list
    python3 tools/scripts/archive_pool.py add --addr 066110000066 --proto 02 --dtype 01 --source 搜表-20261028
    python3 tools/scripts/archive_pool.py export --format hex        # 11H-F1 数据单元
    python3 tools/scripts/archive_pool.py export --format tsv --out /tmp/pool.tsv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime

REPO = os.environ.get("WORKBENCH_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
DEFAULT_POOL = os.path.join(REPO, "data", "archive", "device-pool.tsv")

COLUMNS = ["从节点地址", "协议类型", "设备类型", "来源", "首次发现", "最近发现", "出现次数", "备注"]

PROTO_NAMES = {
    "00": "透明传输",
    "01": "DL/T645-1997",
    "02": "DL/T645-2007",
    "03": "DL/T698.45",
}
DTYPE_NAMES = {"00": "采集器", "01": "电能表"}


def _norm_addr(value: str) -> str:
    """从节点地址统一成 12 位大写 hex。"""
    text = str(value or "").strip().replace(" ", "").replace(":", "").replace("-", "").upper()
    if len(text) != 12 or any(ch not in "0123456789ABCDEF" for ch in text):
        raise ValueError(f"从节点地址须为 12 位 hex（6 字节 BCD）：{value!r}")
    return text


def _norm_code(value: str | None, names: dict[str, str], field: str) -> str:
    if value is None or str(value).strip() == "":
        return ""
    text = str(value).strip().upper().replace("0X", "")
    if text not in names:
        raise ValueError(f"{field} 取值非法：{value!r}（可用：{[f'{k}={v}' for k, v in names.items()]}）")
    return text


def load_pool(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = []
        for raw in reader:
            rows.append({column: (raw.get(column) or "").strip() for column in COLUMNS})
        return rows


def save_pool(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})
    os.replace(tmp, path)


def upsert(path: str, *, addr: str, proto: str = "", dtype: str = "", source: str = "人工",
           note: str = "", when: str | None = None) -> str:
    """按地址去重写入；返回 'added' 或 'updated'。已有行不会被清空其它字段。"""
    addr = _norm_addr(addr)
    proto = _norm_code(proto, PROTO_NAMES, "协议类型")
    dtype = _norm_code(dtype, DTYPE_NAMES, "设备类型")
    stamp = when or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = load_pool(path)
    for row in rows:
        if _norm_addr(row["从节点地址"] or "000000000000") == addr:
            row["最近发现"] = stamp
            try:
                row["出现次数"] = str(int(row.get("出现次数") or "0") + 1)
            except ValueError:
                row["出现次数"] = "1"
            if proto:
                row["协议类型"] = proto
            if dtype:
                row["设备类型"] = dtype
            if source:
                row["来源"] = source
            if note:
                row["备注"] = note
            save_pool(path, rows)
            return "updated"
    rows.append({
        "从节点地址": addr,
        "协议类型": proto,
        "设备类型": dtype,
        "来源": source,
        "首次发现": stamp,
        "最近发现": stamp,
        "出现次数": "1",
        "备注": note,
    })
    save_pool(path, rows)
    return "added"


def export_hex(path: str) -> str:
    """导出 11H-F1「添加从节点」的数据单元 hex（数量 + 每节点地址6B/协议1B）。"""
    rows = [row for row in load_pool(path) if row.get("从节点地址")]
    if not rows:
        raise SystemExit("档案区为空，无可导出内容")
    if len(rows) > 255:
        raise SystemExit(f"从节点数量 {len(rows)} 超过 1 字节上限 255")
    out = [f"{len(rows):02X}"]
    for row in rows:
        addr = _norm_addr(row["从节点地址"])
        proto = _norm_code(row.get("协议类型") or "02", PROTO_NAMES, "协议类型")
        out.append(addr)
        out.append(proto)
    return "".join(out)


def render_table(rows: list[dict]) -> str:
    if not rows:
        return "（档案区为空）"
    header = COLUMNS
    body = [[
        row.get("从节点地址", ""),
        f"{row.get('协议类型', '')} {PROTO_NAMES.get(row.get('协议类型', ''), '')}".strip(),
        f"{row.get('设备类型', '')} {DTYPE_NAMES.get(row.get('设备类型', ''), '')}".strip(),
        row.get("来源", ""),
        row.get("首次发现", ""),
        row.get("最近发现", ""),
        row.get("出现次数", ""),
        row.get("备注", ""),
    ] for row in rows]
    widths = [max(len(header[i]), *(len(line[i]) for line in body)) for i in range(len(header))]
    lines = ["  ".join(header[i].ljust(widths[i]) for i in range(len(header)))]
    lines.append("  ".join("-" * width for width in widths))
    for line in body:
        lines.append("  ".join(line[i].ljust(widths[i]) for i in range(len(header))))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="档案区：设备档案表（下发添加从节点的来源）")
    parser.add_argument("--pool", default=DEFAULT_POOL, help=f"档案区文件（默认 {DEFAULT_POOL}）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出档案区")

    add = sub.add_parser("add", help="按地址写入/更新一条档案")
    add.add_argument("--addr", required=True, help="从节点地址，12 位 hex")
    add.add_argument("--proto", default="02", help="通信协议类型：00/01/02/03（默认 02=DL/T645-2007）")
    add.add_argument("--dtype", default="01", help="设备类型：00 采集器 / 01 电能表")
    add.add_argument("--source", default="人工", help="来源（搜表-日期 / 人工 / 厂家）")
    add.add_argument("--note", default="", help="备注")

    export = sub.add_parser("export", help="导出（hex=11H-F1 数据单元 / tsv / json / csv）")
    export.add_argument("--format", default="hex", choices=["hex", "tsv", "json", "csv"])
    export.add_argument("--out", default="", help="输出文件（默认打印到 stdout）")

    args = parser.parse_args()
    if args.command == "list":
        print(render_table(load_pool(args.pool)))
        return 0
    if args.command == "add":
        result = upsert(args.pool, addr=args.addr, proto=args.proto, dtype=args.dtype,
                        source=args.source, note=args.note)
        print(f"[{result}] {args.addr}  →  {args.pool}")
        return 0

    # export
    rows = load_pool(args.pool)
    if args.format == "hex":
        text = export_hex(args.pool)
    elif args.format == "json":
        text = json.dumps(rows, ensure_ascii=False, indent=2)
    elif args.format == "csv":
        buffer = []
        buffer.append(",".join(COLUMNS))
        buffer.extend(",".join(row.get(column, "") for column in COLUMNS) for row in rows)
        text = "\n".join(buffer)
    else:
        buffer = ["\t".join(COLUMNS)]
        buffer.extend("\t".join(row.get(column, "") for column in COLUMNS) for row in rows)
        text = "\n".join(buffer)
    if args.out:
        with open(args.out, "w", encoding="utf-8-sig") as handle:
            handle.write(text + "\n")
        print(f"已写出 {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
