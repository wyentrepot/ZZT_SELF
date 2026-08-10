"""模块日志/烧录串口独立应用启动入口（端口 8766）。

与侦听台（8765）完全独立。
用法：python -m module_log.run
"""
import os
import sys
from pathlib import Path
import webbrowser
from threading import Timer

import uvicorn

# 确保仓库根在 sys.path，使 module_log/shared 等包可被导入
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

PORT = 8766


def _open() -> None:
    if os.environ.get("HPLC_OPEN_MODULE_SERIAL", "1") != "0":
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}/module-serial")
        except Exception:
            pass


if __name__ == "__main__":
    Timer(1.0, _open).start()
    uvicorn.run("module_log.app:app", host="127.0.0.1", port=PORT)
