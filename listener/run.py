"""侦听台独立应用启动入口（端口 8765）。

与模块日志（8766）完全独立。
用法：python -m listener.run
"""
import os
import sys
from pathlib import Path
import webbrowser
from threading import Timer

import uvicorn

# 确保仓库根在 sys.path，使 listener/shared 等包可被导入
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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
    uvicorn.run("listener.app:app", host="127.0.0.1", port=PORT)
