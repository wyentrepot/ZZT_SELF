"""厂测上位机 CLI：解析/构帧、进入厂测、读写参数、RF/HPLC 点名。

入口：``python3 tools/scripts/factory_test.py <子命令> ...``
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

from .client import FactoryTestClient, FactoryTestError, FactoryTestNak
from .codec import FrameStream, build_frame, parse_hex, to_hex
from .commands import (
    CTRL_READ,
    CTRL_WRITE,
    DI_ADDRESS,
    DI_HPLC_BAND,
    DI_HPLC_POWER,
    DI_RF_CHANNEL,
    DI_RF_OPTION,
    DI_RF_POWER,
    DI_VERSION,
    describe_frame,
)

DEFAULT_PORT = os.environ.get("FACTORY_TEST_PORT", "/run/hplc/serial/factory")


def _parse_int(value: str) -> int:
    text = str(value).strip()
    try:
        return int(text, 0)
    except ValueError:
        return int(text, 16)


def _json_default(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex(" ").upper()
    return str(value)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))


def _add_serial_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"串口/PTY，默认 {DEFAULT_PORT}")
    parser.add_argument("--baud", type=int, default=9600, help="波特率，默认 9600")
    parser.add_argument("--timeout", type=float, default=1.0, help="单次响应超时秒数")
    parser.add_argument("--retries", type=int, default=3, help="超时重试次数")
    parser.add_argument("--addr", type=lambda v: _parse_int(v), default=0xFE, help="帧地址字节，默认 0xFE")


def _make_client(args: argparse.Namespace) -> FactoryTestClient:
    return FactoryTestClient(
        port=args.port,
        baud=args.baud,
        timeout=args.timeout,
        retries=args.retries,
        addr=args.addr,
    )


def cmd_parse(args: argparse.Namespace) -> int:
    data = parse_hex(args.hex)
    stream = FrameStream()
    frames = stream.feed(data)
    payload: Dict[str, Any] = {
        "input_hex": to_hex(data),
        "frame_count": len(frames),
        "frames": [describe_frame(frame) for frame in frames],
    }
    if not frames:
        try:
            text = data.decode("ascii")
        except UnicodeDecodeError:
            text = None
        if text and text.strip():
            payload["note"] = f"no A5 frame found; ASCII text: {text!r}"
        else:
            payload["note"] = "no complete A5 frame found"
    _print_json(payload)
    return 0 if frames else 1


def cmd_build(args: argparse.Namespace) -> int:
    data = parse_hex(args.data) if args.data else b""
    frame = build_frame(args.ctrl, args.di, data, addr=args.addr)
    _print_json(
        {
            "ctrl": f"0x{args.ctrl:02X}",
            "di": f"0x{args.di:04X}",
            "data_hex": data.hex(" ").upper(),
            "frame_hex": to_hex(frame),
        }
    )
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    with _make_client(args) as client:
        frame = client.transact_read(args.di)
        _print_json(describe_frame(frame))
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    data = parse_hex(args.data) if args.data else b""
    with _make_client(args) as client:
        frame = client.transact_write(args.di, data, raise_on_nak=not args.allow_nak)
        _print_json(describe_frame(frame))
    return 0 if frame.ctrl == 0x82 else 1


def cmd_enter(args: argparse.Namespace) -> int:
    with _make_client(args) as client:
        client.enter(
            at_prelude=not args.no_at,
            boot_delay=args.boot_delay,
            timeout=args.enter_timeout,
        )
        _print_json({"ok": True, "action": "enter_factory_mode", "port": args.port})
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    with _make_client(args) as client:
        frame = client.reset()
        _print_json({"ok": frame.ctrl == 0x82, "action": "reset", "ack": describe_frame(frame)})
    return 0 if frame.ctrl == 0x82 else 1


def cmd_at(args: argparse.Namespace) -> int:
    with _make_client(args) as client:
        client.send_text(args.text, append_newline=not args.no_newline)
        _print_json({"ok": True, "action": "send_at", "text": args.text})
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    with _make_client(args) as client:
        print(f"# monitoring {args.port} @ {args.baud} 8N1, Ctrl+C to stop", file=sys.stderr)
        try:
            while True:
                frame = client.read_frame(timeout=args.timeout)
                if frame is None:
                    continue
                print(json.dumps(describe_frame(frame), ensure_ascii=False, default=_json_default), flush=True)
        except KeyboardInterrupt:
            return 0
    return 0


def cmd_rf_call(args: argparse.Namespace) -> int:
    with _make_client(args) as client:
        if args.enter:
            client.enter(at_prelude=not args.no_at, boot_delay=args.boot_delay)
        ok = client.rf_call(args.length)
        result: Dict[str, Any] = {"ok": ok, "action": "rf_call", "length": args.length}
        try:
            result["result"] = client.read_rf_call_result()
        except FactoryTestError as exc:
            result["result_error"] = str(exc)
        _print_json(result)
    return 0 if ok else 1


def cmd_hplc_call(args: argparse.Namespace) -> int:
    with _make_client(args) as client:
        if args.enter:
            client.enter(at_prelude=not args.no_at, boot_delay=args.boot_delay)
        if args.band is not None:
            client.set_band(args.band)
        ok = client.hplc_call(args.length)
        result: Dict[str, Any] = {"ok": ok, "action": "hplc_call", "length": args.length}
        try:
            result["result"] = client.read_hplc_call_result()
        except FactoryTestError as exc:
            result["result_error"] = str(exc)
        _print_json(result)
    return 0 if ok else 1


def cmd_test(args: argparse.Namespace) -> int:
    summary: Dict[str, Any] = {"ok": False, "port": args.port}
    with _make_client(args) as client:
        if args.enter:
            client.enter(at_prelude=not args.no_at, boot_delay=args.boot_delay)
            summary["entered"] = True

        if args.set_option is not None:
            client.set_option(args.set_option)
            summary["set_option"] = args.set_option
        if args.set_channel is not None:
            client.set_channel(args.set_channel)
            summary["set_channel"] = args.set_channel
        if args.set_power is not None:
            client.set_power(args.set_power)
            summary["set_power"] = args.set_power
        if args.set_band is not None:
            client.set_band(args.set_band)
            summary["set_band"] = args.set_band

        summary["version"] = client.read_version()
        summary["address"] = client.read_address()
        summary["channel"] = client.get_channel()
        summary["option"] = client.get_option()
        summary["rf_power"] = client.get_power()
        summary["hplc_band"] = client.get_band()

        if args.rf_call:
            ok = client.rf_call(args.length)
            summary["rf_call_ok"] = ok
            try:
                summary["rf_call_result"] = client.read_rf_call_result()
            except FactoryTestError as exc:
                summary["rf_call_result_error"] = str(exc)

        if args.hplc_call:
            ok = client.hplc_call(args.length)
            summary["hplc_call_ok"] = ok
            try:
                summary["hplc_call_result"] = client.read_hplc_call_result()
            except FactoryTestError as exc:
                summary["hplc_call_result_error"] = str(exc)

        summary["ok"] = True
    _print_json(summary)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="模拟厂测上位机：A5 帧解析/构帧、进入厂测、读写参数、RF/HPLC 点名"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="解析 A5 帧（可含噪声/多帧）")
    p_parse.add_argument("--hex", required=True, help="十六进制帧文本")
    p_parse.set_defaults(func=cmd_parse)

    p_build = sub.add_parser("build", help="构建 A5 帧")
    p_build.add_argument("--ctrl", type=_parse_int, default=CTRL_READ, help="控制字节，默认 0x10 读")
    p_build.add_argument("--di", type=_parse_int, required=True, help="命令 ID，如 0x0100")
    p_build.add_argument("--data", default="", help="DATA 十六进制文本")
    p_build.add_argument("--addr", type=_parse_int, default=0xFE, help="地址字节，默认 0xFE")
    p_build.set_defaults(func=cmd_build)

    p_read = sub.add_parser("read", help="读命令")
    _add_serial_args(p_read)
    p_read.add_argument("--di", type=_parse_int, required=True, help="命令 ID")
    p_read.set_defaults(func=cmd_read)

    p_write = sub.add_parser("write", help="写命令")
    _add_serial_args(p_write)
    p_write.add_argument("--di", type=_parse_int, required=True, help="命令 ID")
    p_write.add_argument("--data", default="", help="DATA 十六进制文本")
    p_write.add_argument("--allow-nak", action="store_true", help="0x83 否认时也正常返回")
    p_write.set_defaults(func=cmd_write)

    p_enter = sub.add_parser("enter", help="进入厂测模式（AT 前导 + A5 0x0005）")
    _add_serial_args(p_enter)
    p_enter.add_argument("--no-at", action="store_true", help="不发送 AT+FACTMODE/AT+RESET")
    p_enter.add_argument("--boot-delay", type=float, default=0.5, help="AT 复位后等待秒数")
    p_enter.add_argument("--enter-timeout", type=float, default=10.0, help="进入厂测总超时秒数")
    p_enter.set_defaults(func=cmd_enter)

    p_reset = sub.add_parser("reset", help="A5 0x0004 复位模块")
    _add_serial_args(p_reset)
    p_reset.set_defaults(func=cmd_reset)

    p_at = sub.add_parser("at", help="发送一条 AT 文本命令")
    _add_serial_args(p_at)
    p_at.add_argument("--text", required=True, help="例如 AT+FACTMODE")
    p_at.add_argument("--no-newline", action="store_true", help="不追加 CRLF")
    p_at.set_defaults(func=cmd_at)

    p_monitor = sub.add_parser("monitor", help="持续监听并解析 A5 帧")
    _add_serial_args(p_monitor)
    p_monitor.set_defaults(func=cmd_monitor)

    p_rf = sub.add_parser("rf-call", help="RF 点名 0x0109 + 读结果 0x010A")
    _add_serial_args(p_rf)
    p_rf.add_argument("--length", type=int, default=100, help="点名载荷长度，默认 100")
    p_rf.add_argument("--enter", action="store_true", help="先进入厂测模式")
    p_rf.add_argument("--no-at", action="store_true", help="进入厂测时不发 AT 前导")
    p_rf.add_argument("--boot-delay", type=float, default=0.5, help="AT 复位后等待秒数")
    p_rf.set_defaults(func=cmd_rf_call)

    p_hplc = sub.add_parser("hplc-call", help="HPLC 点名 0x0205 + 读结果 0x0204")
    _add_serial_args(p_hplc)
    p_hplc.add_argument("--length", type=int, default=100, help="点名载荷长度，默认 100")
    p_hplc.add_argument("--band", type=int, default=None, help="先设置 HPLC band")
    p_hplc.add_argument("--enter", action="store_true", help="先进入厂测模式")
    p_hplc.add_argument("--no-at", action="store_true", help="进入厂测时不发 AT 前导")
    p_hplc.add_argument("--boot-delay", type=float, default=0.5, help="AT 复位后等待秒数")
    p_hplc.set_defaults(func=cmd_hplc_call)

    p_test = sub.add_parser("test", help="基础厂测流程：进模式、读版本/参数、可选点名")
    _add_serial_args(p_test)
    p_test.add_argument("--enter", action="store_true", help="先进入厂测模式")
    p_test.add_argument("--no-at", action="store_true", help="进入厂测时不发 AT 前导")
    p_test.add_argument("--boot-delay", type=float, default=0.5, help="AT 复位后等待秒数")
    p_test.add_argument("--set-channel", type=int, default=None, help="先设置 RF 信道")
    p_test.add_argument("--set-option", type=int, default=None, help="先设置 RF option")
    p_test.add_argument("--set-power", type=int, default=None, help="先设置 RF 功率")
    p_test.add_argument("--set-band", type=int, default=None, help="先设置 HPLC band")
    p_test.add_argument("--rf-call", action="store_true", help="执行 RF 点名")
    p_test.add_argument("--hplc-call", action="store_true", help="执行 HPLC 点名")
    p_test.add_argument("--length", type=int, default=100, help="点名载荷长度，默认 100")
    p_test.set_defaults(func=cmd_test)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FactoryTestError as exc:
        _print_json({"ok": False, "error": str(exc), "error_type": type(exc).__name__})
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
