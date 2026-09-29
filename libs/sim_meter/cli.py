"""模拟电表 CLI。

用法：
    python -m sim_meter serve --port COM5                       # 从站常驻（默认 2400/E/8/1，645-07）
    python -m sim_meter serve --port COM5 --baud 9600 --addr 000000000001 \
        --jzjc-type 0x12 --api 8782                             # 导轨表类型 + REST API
    python -m sim_meter api --port 8782                         # 仅 REST API（无串口，配 handle_hex 调测）
    python -m sim_meter ports                                   # 列出本机串口
    python -m sim_meter demo                                    # 无串口演示：内置请求帧 → 应答帧

与 REST API 共用同一电表脑（MeterBrain）。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading


def _add_serial_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", required=True,
                   help="串口设备（COMx / /dev/ttyUSB* / loop://）")
    p.add_argument("--baud", type=int, default=None,
                   help="波特率（缺省 2400，645-2007 RS-485 缺省）")
    p.add_argument("--parity", default=None, choices=["N", "E", "O"],
                   help="校验位（缺省 E，645 字节格式为偶校验）")
    p.add_argument("--bytesize", type=int, default=None, choices=[7, 8])
    p.add_argument("--stopbits", type=int, default=None, choices=[1, 2])
    p.add_argument("--addr", default=None, help="表通信地址，12 位十六进制")
    p.add_argument("--version", default="07", choices=["07", "97"],
                   help="645 版本：07=DL/T 645-2007（默认），97=DL/T 645-1997")
    p.add_argument("--jzjc-type", default=None, metavar="0xNN",
                   help="重庆即装即采设备类型（4036）：0x10 表 / 0x12 导轨表 / 0x28 量测开关 等")
    p.add_argument("--jzjc-desc", default=None,
                   help="设备描述符（缺省取类型预设，如 DTSU666-D）")


def _build_brain(args) -> "MeterBrain":
    from sim_meter.meter import MeterBrain
    brain = MeterBrain(version=args.version)
    if args.addr:
        brain.set_address(args.addr)
    if args.jzjc_type:
        brain.set_jzjc(device_type=args.jzjc_type, descriptor=args.jzjc_desc)
    return brain


def cmd_serve(args) -> int:
    from sim_meter.journal import MeterJournal
    from sim_meter.serial_channel import MeterSerialChannel

    brain = _build_brain(args)
    brain.set_serial_params(baudrate=args.baud, parity=args.parity,
                            bytesize=args.bytesize, stopbits=args.stopbits)
    journal = MeterJournal(port=args.port)
    channel = MeterSerialChannel(
        brain, port=args.port, baudrate=args.baud, parity=args.parity,
        bytesize=args.bytesize, stopbits=args.stopbits, journal=journal)

    if args.api:
        _start_api(brain, journal, channel, args)

    channel.open()
    print(f"[simeter] 模拟电表已上线：port={args.port} baud={channel.baudrate} "
          f"{channel.parity}{channel.bytesize}{channel.stopbits}", flush=True)
    print(f"[simeter] 645-{brain.version}  地址={brain.addr_hex}  "
          f"即装即采类型=0x{brain.jzjc.device_type:02X}({brain.jzjc.descriptor})", flush=True)
    print(f"[simeter] 会话日志={journal.session_id}  Ctrl-C 退出", flush=True)
    try:
        while True:
            frame = channel.recv_frame(timeout=1.0)
            if frame is None:
                continue
            # 最新一条日志即本帧处理结果（rx/tx 已含 note）
            for entry in journal.entries(limit=2):
                note = entry.get("note") or {}
                line = f"[simeter] {entry['dir'].upper()} {entry['frame_hex']}"
                if note.get("func"):
                    line += f"  {note['func']}"
                if note.get("value"):
                    line += f" = {note['value']}"
                print(line, flush=True)
    except KeyboardInterrupt:
        print("\n[simeter] 退出", flush=True)
    finally:
        channel.close()
    return 0


def _start_api(brain, journal, channel, args) -> None:
    import uvicorn
    from sim_meter.api import SimMeterService, create_simeter_app

    svc = SimMeterService(brain=brain, log_dir=None)
    # serve 模式：CLI 已建好的通道与会话日志直接挂进服务，
    # /status、/baud（热切换）、/frames、/send_hex 即对真实通道生效
    svc.journal = journal
    svc.channel = channel
    app = create_simeter_app(service=svc)
    threading.Thread(
        target=uvicorn.run, args=(app,),
        kwargs={"host": args.host, "port": args.api, "log_level": "warning"},
        daemon=True).start()
    print(f"[simeter] REST API: http://{args.host}:{args.api}/api/simeter/status", flush=True)


def cmd_api(args) -> int:
    import uvicorn
    from sim_meter.api import create_simeter_app
    uvicorn.run(create_simeter_app(), host=args.host, port=args.port)
    return 0


def cmd_ports(_args) -> int:
    from sim_meter.serial_channel import list_serial_ports
    print(json.dumps({"ports": list_serial_ports()}, ensure_ascii=False))
    return 0


def cmd_demo(_args) -> int:
    """无串口演示：构造主站请求帧 → 电表脑应答（覆盖核心能力）。"""
    from sim_meter.meter import MeterBrain
    from parser_lib.adapters.adapter_645 import build_frame_escaped

    brain = MeterBrain()
    bc = lambda b: bytes((x + 0x33) & 0xFF for x in b)  # noqa: E731

    def show(title, addr, ctrl, data, raw=False):
        # 广播校时数据域不加 33H，其余按 +33H 传输
        req = (build_frame_escaped(addr, ctrl, data) if raw
               else build_frame_escaped(addr, ctrl, bc(data)))
        result = brain.handle_frame(req)
        print(f"== {title}")
        print(f"   请求: {req.hex().upper()}")
        if result is None or result.reply is None:
            print(f"   应答: (无)  {result.note if result else ''}")
        else:
            note = {k: v for k, v in result.note.items()
                    if k in ("func", "value", "baudrate", "new_addr")}
            print(f"   应答: {result.reply_wire.hex().upper()}  {note}")
            if result.action:
                print(f"   动作: {result.action[0]}={result.action[1]}")

    ADDR = bytes.fromhex("000000000001")
    show("应答地址：广播读通信地址 13H", b"\xFE" * 6, 0x13, b"")
    show("应答地址：DI 04000401 读通信地址", ADDR, 0x11,
         bytes.fromhex("01040004"))
    show("查询时间：读日期及周次 04000101", ADDR, 0x11,
         bytes.fromhex("01010004"))
    show("查询时间：读时间 04000102", ADDR, 0x11,
         bytes.fromhex("02010004"))
    show("抄表：正向有功总电能 00010000", ADDR, 0x11,
         bytes.fromhex("00000100"))
    show("抄表：A相电压 02010100", ADDR, 0x11,
         bytes.fromhex("00010102"))
    show("更改波特率：17H → 9600", ADDR, 0x17,
         bytes([0x20]))
    show("广播校时 08H（不应答，时钟拨到 2023-07-12 20:30:00）", b"\x99" * 6, 0x08,
         bytes([0x00, 0x30, 0x20, 0x12, 0x07, 0x23]), raw=True)
    rep = brain.build_jzjc_report("sta")
    print("== 重庆即装即采 STA 上报（事件类型51）")
    print(f"   报文: {rep.hex().upper()}  类型=0x{brain.jzjc.device_type:02X} "
          f"描述符={brain.jzjc.descriptor}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sim_meter", description="模拟电表（DL/T 645 从站）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="串口从站常驻运行")
    _add_serial_args(p_serve)
    p_serve.add_argument("--api", type=int, default=None, metavar="PORT",
                         help="同时启动 REST API 于指定端口")
    p_serve.add_argument("--host", default="127.0.0.1")

    p_api = sub.add_parser("api", help="仅运行 REST API")
    p_api.add_argument("--host", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=8782)

    sub.add_parser("ports", help="列出本机串口")
    sub.add_parser("demo", help="无串口演示：内置请求帧 → 应答帧")

    args = parser.parse_args(argv)
    return {
        "serve": cmd_serve,
        "api": cmd_api,
        "ports": cmd_ports,
        "demo": cmd_demo,
    }[args.cmd](args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
