"""模拟集中器验证工具 CLI。

用法：
    python -m sim_concentrator verify <task.json> [--port COMx] [--baud 9600]  # 不传 --port 时自动选择可用串口（缺省 9600/E/8/1）
    python -m sim_concentrator responders
    python -m sim_concentrator ports

与 REST API 共用同一执行核心（execute_task），供 AI 脚本直接调用。
"""
from __future__ import annotations

import argparse
import json
import sys


def _load_task(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cmd_verify(args) -> int:
    try:
        task = _load_task(args.task)
    except FileNotFoundError:
        print(f"[ERROR] 任务文件不存在: {args.task}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"[ERROR] 任务 JSON 解析失败: {e}", file=sys.stderr)
        return 1
    if args.port:
        task["port"] = args.port
    if getattr(args, "mapping_id", None):
        task["mapping_id"] = args.mapping_id
    if args.baud:
        task["baudrate"] = args.baud

    from sim_concentrator.runner import execute_task

    try:
        out = execute_task(task)
    except Exception as e:
        print(f"[ERROR] 执行失败: {e!r}", file=sys.stderr)
        return 1
    if args.json:
        # bytes 字段（如 parsed.buff）序列化为 hex 字符串
        print(json.dumps(out, ensure_ascii=False, indent=2, default=_json_default))
    else:
        _print_human(out)
    return 0 if out["summary"]["verdict"] == "pass" else 1


def _json_default(o):
    """JSON 序列化兜底：bytes → hex 字符串（decode 结果的 buff 等字段）。"""
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    return str(o)


def _print_human(out: dict) -> None:
    print(f"任务: {out['task_id']}  @ {out['port']}:{out['baudrate']}")
    for s in out["steps"]:
        mark = "PASS" if s["result"] == "pass" else "FAIL"
        print(f"  [{mark}] {s['name']}")
        if s.get("sent_hex"):
            print(f"        sent  : {s['sent_hex']}")
        if s.get("matched"):
            print(f"        recv  : {s['matched']}")
        if s.get("reason"):
            print(f"        reason: {s['reason']}")
    sm = out["summary"]
    print(f"结论: {sm['verdict'].upper()}  ({sm['pass']} pass / {sm['fail']} fail / {sm['total']} total)")


def cmd_responders(args) -> int:
    from sim_concentrator.responder import Responder

    rules = Responder().list_rules()
    if args.json:
        print(json.dumps(rules, ensure_ascii=False, indent=2))
    else:
        for r in rules:
            print(f"- {r['id']}: match={r['match']} reply={r['reply']}")
    return 0


def cmd_ports(args) -> int:
    from sim_concentrator.serial_io import list_serial_ports

    ports = list_serial_ports()
    if args.json:
        print(json.dumps({"ports": ports}, ensure_ascii=False, indent=2))
    else:
        for p in ports:
            print(p)
        if not ports:
            print("(无可用串口)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sim-concentrator",
                                     description="模拟集中器验证工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_verify = sub.add_parser("verify", help="执行验证任务 JSON")
    p_verify.add_argument("task", help="验证任务 JSON 文件路径")
    p_verify.add_argument("--port", default=None, help="覆盖串口（支持 COM 或 Linux 设备别名）")
    p_verify.add_argument("--mapping-id", default=None, help="使用串口映射 ID（须存在于 serial_ports.json）")
    p_verify.add_argument("--baud", type=int, default=None, help="覆盖波特率")
    p_verify.add_argument("--json", action="store_true", help="输出 JSON")
    p_verify.set_defaults(func=cmd_verify)

    p_resp = sub.add_parser("responders", help="列出应答应答规则")
    p_resp.add_argument("--json", action="store_true")
    p_resp.set_defaults(func=cmd_responders)

    p_ports = sub.add_parser("ports", help="列出可用串口")
    p_ports.add_argument("--json", action="store_true")
    p_ports.set_defaults(func=cmd_ports)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
