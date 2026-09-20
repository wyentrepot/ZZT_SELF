"""AI 闭环研发验证工作台 —— 统一应用启动入口（端口 8790）。

用法：python3 apps/workbench/run.py   # 任意 cwd 可启动（本文件自注入 sys.path）
或：cd apps && python3 -m workbench.run   # 或 .venv\\Scripts\\python.exe -m workbench.run
"""
import glob
import os
import stat
import sys
from pathlib import Path
from threading import Timer

import uvicorn

# cwd 无关启动（使用不便记录 #1）：必须在任何 shared 导入之前把仓库根/apps/libs
# 注入 sys.path。ensure_paths() 本身来自 shared 包，无法完成自举前的自救；
# frozen（PyInstaller）环境依赖 spec 的 pathex 注入，此处跳过（与 ensure_paths 约束一致）。
if not getattr(sys, "frozen", False):
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    for _entry in (str(_REPO_ROOT), str(_REPO_ROOT / "apps"), str(_REPO_ROOT / "libs")):
        if _entry not in sys.path:
            sys.path.insert(0, _entry)

from shared.infra import ensure_paths

ensure_paths()

PORT = 8790

# 用户态串口设备节点前缀：工作台通过 /dev 下的这些节点枚举串口。
_SERIAL_NODE_GLOBS = ("/dev/ttyACM*", "/dev/ttyUSB*", "/dev/ttyXRUSB*")


def _ensure_serial_nodes() -> None:
    """在 /dev 补齐缺失的用户态串口设备节点。

    部分环境（如 devtmpfs 未自动建节点、或 usbip/vhci 挂载的 tty 不经 udev
    补齐 /dev/tty* 节点）内核态 tty 已就绪（/sys/class/tty/ttyACM*、ttyUSB*），
    但 /dev 下缺少对应字符设备节点，导致 serial.tools.list_ports 扫描不到。
    本函数据 /sys/class/tty/*/dev 的真实主次设备号补建缺失节点，仅在建节点
    缺失时动作，已存在或未就绪时静默跳过。
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        sysfs_tty_dir = "/sys/class/tty"
        if not os.path.isdir(sysfs_tty_dir):
            return
        existing = {os.readlink(n).split("/")[-1] if os.path.islink(n) else os.path.basename(n)
                    for g in _SERIAL_NODE_GLOBS for n in glob.glob(g)}
        for entry in os.listdir(sysfs_tty_dir):
            # 仅关心串口类节点（ttyACM / ttyUSB / ttyXRUSB 等）
            if not any(entry.startswith(p) for p in ("ttyACM", "ttyUSB", "ttyXRUSB")):
                continue
            if entry in existing:
                continue
            devfile = os.path.join(sysfs_tty_dir, entry, "dev")
            try:
                with open(devfile, encoding="ascii") as fh:
                    dev = fh.read().strip()
            except OSError:
                continue
            try:
                major, minor = (int(x) for x in dev.split(":"))
            except ValueError:
                continue
            node = f"/dev/{entry}"
            try:
                if not os.path.exists(node):
                    os.mknod(node, stat.S_IFCHR | 0o660, os.makedev(major, minor))
                try:
                    os.chown(node, 0, 0)
                except OSError:
                    pass
            except OSError:
                pass
    except Exception:  # pragma: no cover - 补节点失败不阻塞启动
        pass


def _open() -> None:
    if os.environ.get("HPLC_OPEN_WORKBENCH", "1") != "0":
        try:
            import webbrowser

            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        except Exception:
            pass


if __name__ == "__main__":
    _ensure_serial_nodes()
    Timer(1.0, _open).start()
    # 0.0.0.0：开放局域网监听（ADR-28），本机仍可 127.0.0.1 访问；页面接口无鉴权，仅限可信局域网。
    uvicorn.run("workbench.app:app", host="0.0.0.0", port=PORT)
