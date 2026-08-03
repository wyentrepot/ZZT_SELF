# -*- coding: utf-8 -*-
"""对打包产物（dist/侦听台/侦听台.exe）做端到端冒烟验证。

用法：.venv\\Scripts\\python.exe scripts\\smoke_test_packaged.py [exe路径]
默认 exe：dist/侦听台/侦听台.exe（相对仓库根，从脚本所在目录定位）。

验证项：
1. exe 启动后 /api/version 返回 GwHPLCAnalysis（DLL 加载成功）
2. 首页 / 正常返回
3. 用 hplc_web/tests/data/gw_log_sample.txt 建索引并分页取帧
4. runtime/ 生成在 exe 同目录
退出码 0 = 全部通过；非 0 = 失败。
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "hplc_web" / "tests" / "data" / "gw_log_sample.txt"


def _default_exe() -> Path:
    return ROOT / "dist" / "侦听台" / "侦听台.exe"


def _wait_ready(timeout: float = 90.0, proc=None) -> dict:
    """轮询 /api/version 直至就绪；传入 proc 时感知子进程早退。"""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        if proc is not None:
            code = proc.poll()
            if code is not None:
                raise RuntimeError(f"子进程提前退出（退出码={code}）")
        try:
            with urllib.request.urlopen(f"{HOST}/api/version", timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            time.sleep(1.0)
    raise RuntimeError(f"服务未在 {timeout}s 内就绪：{last_error}")


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{HOST}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    exe = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _default_exe()
    if not exe.exists():
        print(f"[FAIL] exe 不存在：{exe}")
        return 1
    if not SAMPLE.exists():
        print(f"[FAIL] 测试样本不存在：{SAMPLE}")
        return 1

    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    exit_code = proc.poll()
    if exit_code is not None:
        print(f"[FAIL] exe 提前退出，退出码={exit_code}（可能端口 8765 被占用）")
        return 1
    try:
        version = _wait_ready(proc=proc)
        print(f"[OK] /api/version -> {version}")
        if "GW_SMAnalysis" not in version.get("name", ""):
            print(f"[FAIL] DLL 名称异常：{version}")
            return 1

        with urllib.request.urlopen(f"{HOST}/", timeout=3) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        if "国网 HPLC 日志解析台" not in html:
            print("[FAIL] 首页内容异常")
            return 1
        print("[OK] 首页可访问")

        payload = json.dumps({"path": str(SAMPLE)}).encode("utf-8")
        req = urllib.request.Request(
            f"{HOST}/api/logs/open", data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            open_result = json.loads(resp.read().decode("utf-8"))
        print(f"[OK] /api/logs/open -> {open_result.get('state')}")

        deadline = time.monotonic() + 90
        status = {}
        while time.monotonic() < deadline:
            status = _get("/api/logs/status")
            if status.get("state") == "completed":
                break
            if status.get("state") == "failed":
                print(f"[FAIL] 索引失败：{status.get('message')}")
                return 1
            time.sleep(1.0)
        if status.get("state") != "completed":
            print(f"[FAIL] 索引未完成：{status}")
            return 1
        print(f"[OK] 索引完成：{status.get('frame_count')} 帧")

        frames = _get("/api/logs/frames?offset=0&limit=5")
        if not frames.get("items"):
            print("[FAIL] 分页无帧")
            return 1
        print(f"[OK] 分页取帧 {len(frames['items'])} 条（total={frames.get('total')}）")

        runtime_dir = exe.parent / "runtime"
        if not (runtime_dir / "log_index.sqlite3").exists():
            print(f"[FAIL] runtime 未生成在 exe 同目录：{runtime_dir}")
            return 1
        print(f"[OK] runtime 落位 exe 同目录：{runtime_dir}")

        print("[PASS] 冒烟验证全部通过")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
