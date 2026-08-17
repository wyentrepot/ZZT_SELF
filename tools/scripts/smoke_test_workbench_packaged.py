# -*- coding: utf-8 -*-
"""workbench 打包版启动冒烟（B-03）。

对 dist/工作台/工作台.exe 做 headless 启动验证（HPLC_NO_GUI=1 仅起服务，不弹窗）：
1. exe 启动后 /api/health 返回 200（应用可构建）
2. / 首页返回（静态外壳）
3. /static/ 关键静态资源 200（tokens.css/workbench.html）
4. /api/platform-version 子应用挂载状态（listener DLL 加载路径可解析）
5. 打包版已运行时生成 runtime/（exe 同目录）

退出码 0 = 全部通过；非 0 = 失败。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:8790"
ROOT = Path(__file__).resolve().parents[2]
EXE = ROOT / "dist" / "工作台" / "工作台.exe"


def _wait_ready(timeout: float = 60.0, proc: subprocess.Popen | None = None) -> dict:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"exe 提前退出（退出码={proc.returncode}）")
        try:
            with urllib.request.urlopen(f"{HOST}/api/health", timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"服务未在 {timeout}s 内就绪：{last_error}")


def _get_text(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{HOST}{path}", timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except urllib.error.URLError:
        return 0, ""


def main() -> int:
    if not EXE.exists():
        print(f"[smoke] 未找到打包产物：{EXE}")
        return 1

    env = dict(os.environ, HPLC_NO_GUI="1")
    proc = subprocess.Popen([str(EXE)], env=env, cwd=str(EXE.parent),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = []
    try:
        health = _wait_ready(proc=proc)
        results.append(("health", True, health.get("app", "")))

        # 首页
        st, _ = _get_text("/")
        results.append(("index /", st == 200, f"status={st}"))

        # 关键静态资源
        for p in ("/static/tokens.css", "/static/workbench.html", "/static/app.js"):
            st, _ = _get_text(p)
            results.append((p, st == 200, f"status={st}"))

        # 子应用挂载状态
        st, body = _get_text("/api/platform-version")
        ok = st == 200 and "module_log_mounted" in body
        results.append(("platform-version", ok, f"status={st}"))

        # 子应用代理（module-log/listener）
        for p in ("/api/module-serial/version", "/api/listener/version"):
            st, body = _get_text(p)
            results.append((p, st == 200, f"status={st}"))

        # runtime/ 生成在 exe 同目录
        runtime = EXE.parent / "runtime"
        results.append(("runtime dir", runtime.is_dir(), str(runtime)))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("=== workbench 打包版启动冒烟 ===")
    failed = 0
    for name, ok, note in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {note}")
        if not ok:
            failed += 1
    if failed:
        print(f"失败 {failed} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
