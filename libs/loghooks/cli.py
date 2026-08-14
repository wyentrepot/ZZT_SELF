"""命令行入口：python -m loghooks scan / rules diff / --list-provinces。

用法：
    python -m loghooks scan <log> [--source module_log|listener] [--province X]
            [--auto-detect] [--list-provinces] [--correlate <另一日志>] [--format json|table] [--out <文件>]
    python -m loghooks rules diff --old <旧扫描结果.json> --new <新扫描结果.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .correlate import correlate
from .engine import Engine, ScanResult
from .output import format_json, format_table
from .rules import RuleLoader
from .sources import ParsedLine, iter_lines, list_sources


# ---------------------------------------------------------------------------
# 日志读取
# ---------------------------------------------------------------------------


def read_lines(path: Path) -> List[str]:
    """读取日志文件（自动识别编码）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="gbk", errors="ignore")
    return text.splitlines()


def scan_file(
    path: Path,
    source: str,
    parser_kwargs: Optional[dict] = None,
) -> List[ParsedLine]:
    """扫描单个文件，返回解析后的行。"""
    lines = read_lines(path)
    return iter_lines(source, lines, **(parser_kwargs or {}))


def scan_dir(
    path: Path,
    source: str,
    parser_kwargs: Optional[dict] = None,
) -> tuple[List[ParsedLine], List[str]]:
    """扫描目录下的所有日志文件。"""
    if path.is_file():
        return scan_file(path, source, parser_kwargs), [str(path)]

    parsed_all: List[ParsedLine] = []
    files: List[str] = []
    for f in sorted(path.iterdir()):
        if f.is_file() and f.suffix in (".txt", ".log", ".jsonl"):
            parsed_all.extend(scan_file(f, source, parser_kwargs))
            files.append(str(f))
    return parsed_all, files


# ---------------------------------------------------------------------------
# scan 子命令
# ---------------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> int:
    loader = RuleLoader().load_all()
    if loader.errors:
        for e in loader.errors:
            print(f"[规则加载错误] {e}", file=sys.stderr)
        return 1

    # 先按模块过滤（cco/sta/common），再按省份附加专属规则
    rules = loader.filter_by_module(args.module)
    if args.province:
        # 附加该省的专属规则（scope=province 默认被 filter_by_module 排除）
        province_only = [r for r in loader.filter_by_province(args.province)
                         if r.scope == "province"]
        rules = rules + province_only
    if not rules:
        print("没有匹配的规则，请检查 --module/--province 或规则文件", file=sys.stderr)
        return 1

    path = Path(args.log)
    if not path.exists():
        print(f"日志路径不存在: {path}", file=sys.stderr)
        return 1

    # 来源解析参数
    parser_kwargs = {}
    if args.source == "listener":
        # 离线解析侦听台帧：尝试复用 shared.parser_service（可用时）
        try:
            from shared.parser_service import ParserService
            from shared.dotnet_parser import DotnetParser

            parser = ParserService(DotnetParser())
            parser_kwargs["parser_callback"] = parser.parse_summary
        except Exception:
            parser_kwargs["parser_callback"] = None

    parsed, files = scan_dir(path, args.source, parser_kwargs)
    if not parsed:
        print(f"未能从 {path} 解析出任何行（source={args.source}）", file=sys.stderr)
        return 1

    engine = Engine(rules, source=args.source)
    for line in parsed:
        engine.feed(line)
    result = engine.finalize()
    result.files = files

    # 省份自动识别
    detected = []
    if args.auto_detect:
        detected = loader.detect_provinces(result.hit_rule_ids)

    # 跨来源关联
    correlations = None
    if args.correlate:
        other = Path(args.correlate)
        other_source = "listener" if args.source == "module_log" else "module_log"
        other_parsed, other_files = scan_dir(other, other_source, parser_kwargs)
        other_engine = Engine(rules, source=other_source)
        for line in other_parsed:
            other_engine.feed(line)
        other_result = other_engine.finalize()
        other_result.files = other_files
        correlations = correlate(result, other_result)

    # 输出
    if args.format == "table":
        text = format_table(result)
    else:
        text = format_json(result, correlations=correlations, detected_provinces=detected)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入: {args.out}")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------------------
# rules diff 子命令
# ---------------------------------------------------------------------------


def _load_scan_json(path: Path) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    return data


def _stable_key(rec: dict) -> tuple:
    """稳定标识：(file, msg)。"""
    return (rec.get("file", ""), rec.get("msg", ""))


def cmd_rules_diff(args: argparse.Namespace) -> int:
    old_path = Path(args.old)
    new_path = Path(args.new)
    if not old_path.exists() or not new_path.exists():
        print("--old 或 --new 文件不存在", file=sys.stderr)
        return 1

    old_recs = _load_scan_json(old_path)
    new_recs = _load_scan_json(new_path)

    old_map = {_stable_key(r): r for r in old_recs}
    new_map = {_stable_key(r): r for r in new_recs}

    added = [r for k, r in new_map.items() if k not in old_map]
    removed = [r for k, r in old_map.items() if k not in new_map]

    # 行号漂移（同一 (file,msg) 行号变化 > 灵敏度）
    shift_sensitivity = args.sensitivity
    drifted = []
    for k in old_map.keys() & new_map.keys():
        old_line = old_map[k].get("line")
        new_line = new_map[k].get("line")
        if old_line and new_line and abs(old_line - new_line) > shift_sensitivity:
            drifted.append((old_map[k], new_map[k]))

    # msg 变更（同一 file + 行号相近但 msg 不同）
    msg_changed = []
    msg_tolerance = max(args.sensitivity, 3)
    for old_r in old_recs:
        old_file = old_r.get("file", "")
        old_line = old_r.get("line")
        if old_line is None:
            continue
        for new_r in new_recs:
            if new_r.get("file", "") != old_file:
                continue
            new_line = new_r.get("line")
            if new_line is None:
                continue
            if abs(new_line - old_line) <= msg_tolerance and old_r.get("msg") != new_r.get("msg"):
                msg_changed.append((old_r, new_r))

    print(f"=== 新增打印（需要补规则）=== ({len(added)} 条)")
    for r in added[:30]:
        print(f"  {r.get('file')}:{r.get('line')}  {r.get('msg')}")
    if len(added) > 30:
        print(f"  ... 还有 {len(added)-30} 条")
    print()
    print(f"=== 删除打印（规则可废弃）=== ({len(removed)} 条)")
    for r in removed[:30]:
        print(f"  {r.get('file')}:{r.get('line')}  {r.get('msg')}")
    if len(removed) > 30:
        print(f"  ... 还有 {len(removed)-30} 条")
    print()
    print(f"=== 行号漂移（规则需更新 line）=== ({len(drifted)} 条)")
    for old_r, new_r in drifted[:30]:
        print(f"  {old_r.get('file')}:{old_r.get('line')} → {new_r.get('line')}  {old_r.get('msg')}")
    if len(drifted) > 30:
        print(f"  ... 还有 {len(drifted)-30} 条")
    print()
    print(f"=== msg 变更（规则需重审）=== ({len(msg_changed)} 条)")
    for old_r, new_r in msg_changed[:20]:
        print(f"  old: {old_r.get('msg')}  new: {new_r.get('msg')}")
    if len(msg_changed) > 20:
        print(f"  ... 还有 {len(msg_changed)-20} 条")

    return 0


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="loghooks", description="配置驱动的日志运行状态钩子")
    sub = parser.add_subparsers(dest="command")

    # scan
    p_scan = sub.add_parser("scan", help="离线扫描日志")
    p_scan.add_argument("log", help="日志文件或目录")
    p_scan.add_argument("--source", choices=list_sources(), default="module_log",
                        help="日志来源（默认 module_log）")
    p_scan.add_argument("--module", choices=["cco", "sta", "common"], default=None,
                        help="模块过滤（cco/sta/common，不传 = 只加载通用 common 规则）")
    p_scan.add_argument("--province", default=None, help="省份过滤（不传 = 全部规则）")
    p_scan.add_argument("--auto-detect", action="store_true", default=True,
                        help="扫描后自动识别省份（默认开）")
    p_scan.add_argument("--list-provinces", action="store_true",
                        help="只列出可用省份，不扫描")
    p_scan.add_argument("--correlate", default=None, help="另一份日志（跨来源关联）")
    p_scan.add_argument("--format", choices=["json", "table"], default="json")
    p_scan.add_argument("--out", default=None, help="输出到文件")
    p_scan.set_defaults(func=cmd_scan)

    # rules diff
    p_diff = sub.add_parser("rules", help="规则工具")
    p_diff_sub = p_diff.add_subparsers(dest="rules_cmd")
    p_diff_sub2 = p_diff_sub.add_parser("diff", help="对比新旧扫描结果")
    p_diff_sub2.add_argument("--old", required=True, help="旧扫描结果 json")
    p_diff_sub2.add_argument("--new", required=True, help="新扫描结果 json")
    p_diff_sub2.add_argument("--sensitivity", type=int, default=5,
                             help="行号漂移灵敏度（默认 5 行）")
    p_diff_sub2.set_defaults(func=cmd_rules_diff)

    args = parser.parse_args(argv)

    # --list-provinces 快速路径
    if args.command == "scan" and args.list_provinces:
        loader = RuleLoader().load_all()
        for p in loader.get_province_list():
            print(f"{p['province']}: {p['rule_count']} 条规则")
        if loader.errors:
            for e in loader.errors:
                print(f"[错误] {e}", file=sys.stderr)
        return 0

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())