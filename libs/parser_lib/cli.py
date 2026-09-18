"""REQS-0032 应用层帧工具 CLI：parse / build / verify 三子命令，JSON 输出到 stdout。

用法（免工作台，纯库层）：
    PYTHONPATH=libs python3 -m parser_lib.cli parse  --hex "68 ..." [--protocol 645]
    PYTHONPATH=libs python3 -m parser_lib.cli build  --protocol 645 --params '{"addr":...}'
    PYTHONPATH=libs python3 -m parser_lib.cli verify --hex "68 ..." [--expect '{...}'] [--protocol ...]

退出码：0 = 成功（verify 需 verified）；1 = 业务失败（JSON 内 error/verified=false）；
2 = 用法错误（argparse）。
日常协议范围与容错语义见 parser_lib.facade（基线 REQS-0032 v1.2）。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import facade


def _parse_hex_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--hex", required=True,
                   help="原始帧 hex（可含空白；'-' 从 stdin 读一行）")
    p.add_argument("--protocol", default=None,
                   help="指定协议 645|698.45|1376.2（缺省=自动嗅探，严格阈值）")


def _read_hex(value: str) -> str:
    if value == "-":
        return sys.stdin.readline()
    return value


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="parser_lib.cli",
        description="AI 日常应用层帧工具（1376.2/698/645 构建+解析+回验，免工作台）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("parse", help="解析：hex → JSON（自动嗅探或指定协议）")
    _parse_hex_args(sp)

    bp = sub.add_parser("build", help="构帧：语义参数 → 帧 hex")
    bp.add_argument("--protocol", required=True, help="645|698.45|1376.2")
    bp.add_argument("--params", required=True,
                    help="语义参数 JSON，如 '{\"addr\":\"123456789012\",\"control\":\"11\",\"di\":\"00010000\"}'；"
                         "1376.2 透传 adapter_10376.build_frame_json 契约")

    vp = sub.add_parser("verify", help="回验：解析 + （可选）意图字段比对")
    _parse_hex_args(vp)
    vp.add_argument("--expect", default=None,
                    help="期望字段 JSON（字段名→值），如 '{\"地址域\":\"123456789012\"}'")
    return p


def main(argv: Optional[list] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse 用法错误 → 契约退出码 2
        return int(exc.code or 2)

    if args.cmd == "parse":
        payload = facade.decode(_read_hex(args.hex), protocol=args.protocol)
    elif args.cmd == "build":
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            payload = {"ok": False, "error": "invalid_json",
                       "detail": f"--params 不是合法 JSON：{exc}"}
        else:
            if not isinstance(params, dict):
                payload = {"ok": False, "error": "invalid_json",
                           "detail": "--params 必须是 JSON 对象"}
            else:
                payload = facade.build(args.protocol, params)
    else:  # verify
        try:
            expect = json.loads(args.expect) if args.expect else None
        except json.JSONDecodeError as exc:
            payload = {"ok": False, "error": "invalid_json",
                       "detail": f"--expect 不是合法 JSON：{exc}"}
        else:
            payload = facade.verify(_read_hex(args.hex), protocol=args.protocol,
                                    expect=expect)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload.get("ok"):
        return 1
    return 0 if payload.get("verified", True) else 1


if __name__ == "__main__":
    sys.exit(main())
