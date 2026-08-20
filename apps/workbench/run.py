"""AI 闭环研发验证工作台 —— 统一应用启动入口（端口 8790）。

用法：python -m workbench.run
（apps/ 在 sys.path 时；或 .venv\\Scripts\\python.exe -m workbench.run）
"""
import os
import sys
import webbrowser
from threading import Timer

import uvicorn

from shared.infra import ensure_paths

ensure_paths()

PORT = 8790


def _open() -> None:
    if os.environ.get("HPLC_OPEN_WORKBENCH", "1") != "0":
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        except Exception:
            pass


if __name__ == "__main__":
    Timer(1.0, _open).start()
    # 0.0.0.0：开放局域网监听（ADR-28），本机仍可 127.0.0.1 访问；页面接口无鉴权，仅限可信局域网。
    uvicorn.run("workbench.app:app", host="0.0.0.0", port=PORT)
