"""侦听台 —— 本地桌面软件入口（pywebview 内嵌窗口）。

与网页模式（python -m listener.run，浏览器打开）完全独立，二者可并存。
双击 exe 或运行本脚本：后台起 Uvicorn(127.0.0.1:8765)，pywebview 内嵌
原生窗口加载 / 页面；窗口关闭时优雅停止服务。

用法（开发模式）：.venv\\Scripts\\python.exe -m listener.desktop
用法（打包模式）：PyInstaller 打包后直接运行 exe
"""
import os
import sys
import threading

# 目录分层后：把 apps/ 与 libs/ 加入 sys.path，使 listener/shared 等包可被导入（开发模式）
from shared.infra import ensure_paths

ensure_paths()

PORT = 8765
TITLE = "侦听台"


def _start_server() -> None:
    """后台线程运行 Uvicorn（阻塞调用，直至服务终止）。"""
    import uvicorn

    uvicorn.run("listener.app:app", host="127.0.0.1", port=PORT, log_level="warning")


def main() -> None:
    try:
        import webview
    except ImportError:
        # 未安装 pywebview 时回退到浏览器模式，保证功能可用
        import webbrowser
        from threading import Timer

        Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
        _start_server()
        return

    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()

    # pywebview 需在调用 start 前创建窗口
    window = webview.create_window(TITLE, f"http://127.0.0.1:{PORT}/", width=1280, height=860)
    webview.start()

    # 窗口关闭后：让 daemon 线程随进程自然结束
    print("窗口已关闭，服务已停止。")


if __name__ == "__main__":
    main()
