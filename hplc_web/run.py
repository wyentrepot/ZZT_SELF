import os
import webbrowser
from threading import Timer

import uvicorn


def _open_urls() -> None:
    """启动后自动打开侦听台主页 + 模块日志/烧录页（新标签，两页独立）。"""
    base = "http://127.0.0.1:8765/"
    urls = [base]
    # 模块日志/烧录页：串口烧录核心场景，默认一并打开
    if os.environ.get("HPLC_OPEN_MODULE_SERIAL", "1") != "0":
        urls.append(base + "module-serial")
    for u in urls:
        try:
            webbrowser.open(u)
        except Exception:
            pass


if __name__ == "__main__":
    mode = os.environ.get("HPLC_LAUNCH_MODE", "production").lower()
    if mode == "test":
        # 测试模式仅开主页（带 ?mode=test），与既有行为一致
        Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8765/?mode=test")).start()
    else:
        Timer(1.0, _open_urls).start()
    uvicorn.run("hplc_web.app:app", host="127.0.0.1", port=8765)
