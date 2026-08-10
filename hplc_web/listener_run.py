"""侦听台独立应用启动入口（端口 8765）。

需求 0002 拆分：与模块日志（8766）完全独立。
用法：python -m hplc_web.listener_run
"""
import os
import webbrowser
from threading import Timer

import uvicorn

PORT = 8765


def _open() -> None:
    mode = os.environ.get("HPLC_LAUNCH_MODE", "production").lower()
    url = f"http://127.0.0.1:{PORT}/"
    if mode == "test":
        url += "?mode=test"
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    Timer(1.0, _open).start()
    uvicorn.run("hplc_web.listener_app:app", host="127.0.0.1", port=PORT)
