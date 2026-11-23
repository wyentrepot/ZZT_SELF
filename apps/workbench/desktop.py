"""AI 闭环研发验证工作台 —— 统一桌面入口（pywebview 单窗口）。

复用 apps/module_log/desktop.py 模式（ADR-2/3）：后台线程起 Uvicorn(8790)，
主线程 pywebview 开窗；未装 pywebview 回退浏览器。
窗口标题：「AI 闭环工作台」；尺寸 1440×900。

环境变量 HPLC_NO_GUI=1：跳过 pywebview/浏览器，仅后台起服务
（headless / CI / 自动化验证场景）。

用法（开发模式）：.venv\\Scripts\\python.exe -m workbench.desktop
用法（打包模式）：PyInstaller 打包后直接运行 exe
"""
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from shared.infra import ensure_paths

ensure_paths()

PORT = 8790
TITLE = "AI 闭环工作台"


def repo_root() -> Path:
    """仓库根（frozen 打包时取可执行文件所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def runtime_file_path() -> Path:
    """CLI 发现本服务的运行时文件：``<仓库根>/data/runtime/workbench.json``。

    REQS-0042 D2-3「单入口服务发现」。读侧在 ``case_runner/cli.py``
    （``runtime_base_url`` / ``resolve_base_url``）。

    与 ``apps/workbench/run.py`` 的同名函数**口径必须一致**（文件名、字段、
    回环 ``base_url``）；两处都改才算改完——桌面入口与命令行入口可能分别被启动。
    """
    return repo_root() / "data" / "runtime" / "workbench.json"


def write_runtime_file(host: str = "0.0.0.0", port: int = PORT) -> Path | None:
    """写运行时文件（D2-3）；**写失败只 warning，绝不阻塞开窗/起服务**。

    字段：``host`` / ``port`` / ``pid`` / ``started_at``（ISO8601）/ ``base_url``
    （客户端可拨的本机回环地址）。先写 ``.tmp`` 再 ``os.replace``，读侧不会读到半个文件。
    """
    target = runtime_file_path()
    try:
        payload = {
            "host": str(host),
            "port": int(port),
            "pid": os.getpid(),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "base_url": "http://127.0.0.1:%d" % int(port),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, target)
        return target
    except Exception as exc:  # noqa: BLE001 - 运行时文件只是线索，失败不影响服务本身
        print("[workbench] warning: 写运行时文件 %s 失败（不阻塞启动）：%s" % (target, exc))
        return None


def _start_server() -> None:
    import uvicorn

    _runtime = write_runtime_file(host="0.0.0.0", port=PORT)
    if _runtime is not None:
        print(f"[workbench] 运行时文件：{_runtime}（case_run 据此发现本服务，REQS-0042 D2-3）")

    # 0.0.0.0：开放局域网监听（ADR-28），本机仍可 127.0.0.1 访问；页面接口无鉴权，仅限可信局域网。
    uvicorn.run("workbench.app:app", host="0.0.0.0", port=PORT, log_level="warning")


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
