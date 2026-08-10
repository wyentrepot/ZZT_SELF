"""模块日志/烧录串口独立应用启动入口（端口 8766）。

需求 0002 拆分：与侦听台（8765）完全独立。
用法：python -m hplc_web.module_serial_run
"""
import os
import webbrowser
from threading import Timer

import uvicorn

PORT = 8766


def _open() -> None:
    if os.environ.get("HPLC_OPEN_MODULE_SERIAL", "1") != "0":
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}/module-serial")
        except Exception:
            pass


if __name__ == "__main__":
    Timer(1.0, _open).start()
    uvicorn.run("hplc_web.module_serial_app:app", host="127.0.0.1", port=PORT)
