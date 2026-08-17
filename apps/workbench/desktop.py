"""AI 闭环研发验证工作台 —— 统一桌面入口（pywebview 单窗口）。

复用 apps/module_log/desktop.py 模式（ADR-2/3）：后台线程起 Uvicorn(8790)，
主线程 pywebview 开窗；未装 pywebview 回退浏览器。
窗口标题：「AI 闭环工作台」；尺寸 1440×900。

环境变量 HPLC_NO_GUI=1：跳过 pywebview/浏览器，仅后台起服务
（headless / CI / 自动化验证场景）。

用法（开发模式）：.venv\\Scripts\\python.exe -m workbench.desktop
用法（打包模式）：PyInstaller 打包后直接运行 exe
"""
import os
import threading

from shared.infra import ensure_paths

ensure_paths()

PORT = 8790
TITLE = "AI 闭环工作台"


def _start_server() -> None:
    import uvicorn

    uvicorn.run("workbench.app:app", host="127.0.0.1", port=PORT, log_level="warning")


def main() -> None:
    if os.environ.get("HPLC_NO_GUI", "0") == "1":
        # headless/CI：仅起服务（前台阻塞，Ctrl+C 退出）
        _start_server()
        return

    try:
        import webview
    except ImportError:
        import webbrowser
        from threading import Timer

        Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
        _start_server()
        return

    server_thread = threading.Thread(target=_start_server, daemon=True)
    server_thread.start()

    window = webview.create_window(TITLE, f"http://127.0.0.1:{PORT}/", width=1440, height=900)
    webview.start()
    print("窗口已关闭，服务已停止。")


if __name__ == "__main__":
    main()
