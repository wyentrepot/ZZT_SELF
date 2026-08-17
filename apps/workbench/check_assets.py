"""打包资产完整性校验（B-03 静态资源门禁）。

遍历 apps/workbench/static 下全部静态资源，并解析各 HTML 引用的
/static/... 相对资源，校验：
  1. static 目录内每个文件存在且非空；
  2. 每个 HTML 引用的静态资源实际存在（防"页面引用了但没打包"）；
  3. 关键路径（index/workbench/app.js/tokens.css 等）存在。

用法：
  python -m workbench.check_assets            # 源码模式校验
  python -m workbench.check_assets --strict   # 退出码非 0 表示失败（打包 CI 门禁）

返回：0 全部通过；1 有缺失/引用断裂（--strict 时）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

# 关键资产（打包必需，缺失即失败）
KEY_ASSETS = [
    "index.html",
    "app.js",
    "styles.css",
    "tokens.css",
    "workbench.html",
    "pages/listener/index.html",
    "pages/module-serial/module-serial.html",
]

# 允许的非 HTML 静态资源后缀（HTML 内联之外）
STATIC_EXT = {".js", ".css", ".html", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf"}


def _referenced_static_paths() -> list[str]:
    """解析 static 下所有 HTML 引用的 /static/... 与相对资源路径。"""
    refs: list[str] = []
    for html in STATIC_DIR.rglob("*.html"):
        text = html.read_text(encoding="utf-8", errors="replace")
        # <script src="..."> / <link href="..."> / <img src="..."> / fetch('/static/...')
        for m in re.finditer(r"""(?:src|href)\s*=\s*["']([^"'#]+)["']""", text):
            url = m.group(1).strip()
            if url.startswith(("http://", "https://", "data:", "#")):
                continue
            if url.startswith("/static/"):
                refs.append(url[len("/static/"):])
        for m in re.finditer(r"""fetch\(\s*["']([^"']+)["']""", text):
            url = m.group(1).strip()
            if url.startswith("/static/"):
                refs.append(url[len("/static/"):])
    return refs


def check_assets(static_dir: Path = STATIC_DIR, strict: bool = False) -> int:
    """执行校验，返回 0 通过 / 1 失败。"""
    problems: list[str] = []

    if not static_dir.is_dir():
        problems.append(f"静态资源目录不存在：{static_dir}")
        return 1 if strict else 0

    # 1. 关键资产存在且非空
    for rel in KEY_ASSETS:
        f = static_dir / rel
        if not f.is_file():
            problems.append(f"缺失关键资产：{rel}")
        elif f.stat().st_size == 0:
            problems.append(f"关键资产为空文件：{rel}")

    # 2. HTML 引用的 /static/ 资源存在
    for rel in _referenced_static_paths():
        # 去掉 query string
        rel = rel.split("?", 1)[0]
        if not rel:
            continue
        f = static_dir / rel
        if not f.is_file():
            problems.append(f"HTML 引用了但文件不存在：/static/{rel}")

    # 3. 目录内全部静态文件存在且非空
    for f in sorted(static_dir.rglob("*")):
        if f.is_file() and f.suffix.lower() in STATIC_EXT and f.stat().st_size == 0:
            problems.append(f"空文件：{f.relative_to(static_dir)}")

    if problems:
        print(f"[check_assets] 发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"  - {p}")
        return 1 if strict else 0

    count = sum(1 for _ in static_dir.rglob("*") if _.is_file())
    print(f"[check_assets] 通过：{count} 个静态资产完整，无缺失/空文件/引用断裂。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="workbench 打包资产完整性校验")
    parser.add_argument("--strict", action="store_true", help="有任一问题即返回非 0（CI 门禁）")
    args = parser.parse_args()
    sys.exit(check_assets(strict=args.strict))


if __name__ == "__main__":
    main()
