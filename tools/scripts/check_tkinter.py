"""验证后端进程能否用 tkinter 调用 Windows 原生文件对话框（只探测可用性，不实际弹出）。"""
import sys


def check() -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        print(f"tkinter 不可用: {exc}")
        sys.exit(1)
    print(f"tkinter 可用，版本: {tk.TkVersion}")
    print(f"filedialog.askopenfilename 存在: {callable(filedialog.askopenfilename)}")
    print("OK")


if __name__ == "__main__":
    check()
