#!/usr/bin/env python3
"""独立模块烧录脚本（需求 0001 serial-flash-session）。

适用场景：ZZT_SELF 项目【不在运行】时，独立烧录 SPLC 模块固件。
- 自己 open 指定 COM 口 → 复用 hplc_web/xmodem_flash.flash() → close。
- 项目运行时请改用前端 /module-serial 页面按钮（同一 XMODEM 核心，
  ModuleSerialService 用其常驻 handle 执行），避免与本脚本争用串口。

用法示例：
  python flash_module.py --port COM7 --bin D:\\fw\\app.bin --slot 0
  python flash_module.py --port COM7 --bin /mnt/c/fw/app.bin --baud-plan 9600,115200,9600
  python flash_module.py --selftest          # 无硬件自检（CRC 0x31C3）
  python flash_module.py --list-ports          # 列出串口
  python flash_module.py --dry-run --port COM7 --bin xxx.bin   # 只校验参数
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hplc_web import xmodem_flash  # noqa: E402


def _require_pyserial():
    try:
        import serial  # noqa: F401, PLC0415
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 pyserial 依赖，请先安装：pip install pyserial") from exc


def list_ports() -> None:
    try:
        import serial.tools.list_ports  # noqa: PLC0415

        ports = [p.device for p in serial.tools.list_ports.comports()]
        print("Visible COM ports:")
        for p in ports:
            print(f"  {p}")
        if not ports:
            print("  (none)")
    except Exception as exc:  # pragma: no cover
        print(f"failed to list ports: {exc}")


def selftest() -> int:
    result = xmodem_flash.selftest()
    print(f"Self-test PASS: {result}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="独立模块 XMODEM 烧录（项目不在运行时用）")
    parser.add_argument("--port", help="目标 COM 口，如 COM7")
    parser.add_argument("--bin", dest="bin_path", help="固件 .bin 路径")
    parser.add_argument("--slot", type=int, default=0, help="image slot（默认 0）")
    parser.add_argument("--baud-plan", default="9600,115200,9600",
                        help="波特率方案：导航,传输,恢复（逗号分隔；单值=不切换）")
    parser.add_argument("--no-reboot-after", action="store_true",
                        help="烧录后不重启")
    parser.add_argument("--enter-bootloader-command", default="reboot",
                        help="进入 bootloader 的命令（默认 reboot）")
    parser.add_argument("--list-ports", action="store_true", help="列出串口")
    parser.add_argument("--selftest", action="store_true", help="无硬件自检（CRC 0x31C3）")
    parser.add_argument("--dry-run", action="store_true", help="只校验参数，不真正烧录")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.list_ports:
        list_ports()
        return 0

    if not args.port:
        print("Error: --port 必填（未确认 COM 口不烧录，安全规则）", file=sys.stderr)
        return 2
    if not args.bin_path:
        print("Error: --bin 必填（未指定固件不烧录）", file=sys.stderr)
        return 2
    bin_file = Path(args.bin_path)
    if not bin_file.is_file():
        print(f"Error: 固件不存在：{args.bin_path}", file=sys.stderr)
        return 2

    baud_plan = [int(x) for x in args.baud_plan.replace("，", ",").split(",") if x.strip()]
    if not baud_plan:
        print("Error: 无效波特率方案", file=sys.stderr)
        return 2

    if args.dry_run:
        print("Dry-run PASS")
        print(f"  Firmware : {bin_file.resolve()}")
        print(f"  Size     : {bin_file.stat().st_size} bytes")
        print(f"  Port     : {args.port}")
        print(f"  Boud plan: {baud_plan}")
        print(f"  Command  : download {args.slot}")
        return 0

    _require_pyserial()
    import serial  # noqa: PLC0415

    print(f"Opening {args.port} at {baud_plan[0]} (baud plan {baud_plan}) ...")
    ser = serial.Serial(
        port=args.port, baudrate=baud_plan[0], bytesize=8,
        parity="N", stopbits=1, timeout=0.1,
    )
    try:
        result = xmodem_flash.flash(
            ser,
            args.bin_path,
            slot=args.slot,
            baud_plan=baud_plan,
            no_reboot_after=args.no_reboot_after,
            enter_bootloader_command=args.enter_bootloader_command,
            log=lambda m: print(f"[{__import__('datetime').datetime.now().strftime('%H:%M:%S')}] {m}"),
        )
        print(f"BURN SUCCESS: {result}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"BURN FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            if ser.is_open:
                ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
