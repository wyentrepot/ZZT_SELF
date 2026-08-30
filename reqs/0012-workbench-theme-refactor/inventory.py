#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""REQS-0012 变量盘点：提取各页自定义属性的「定义点」与「引用点」。

用法：python inventory.py
输出：按方言分组的变量清单，用于生成 compat-dialects.css 别名映射。

刻意放在 reqs/ 而非 apps/ 下 —— 规避 E-SafeNet 加密目录风险（项目记忆 R2）。
"""
import re
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "apps" / "workbench" / "static"

DEFINE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
USE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")

# 三套方言 + 通用（非颜色）token 的前缀特征
DIALECTS = {
    "A 工作台": ["--bg-page", "--bg-surface", "--bg-elevated", "--bg-input",
                 "--bg-hover", "--bg-active", "--fg-default", "--fg-muted",
                 "--fg-dim", "--fg-inverse", "--accent", "--accent-hover",
                 "--accent-fg", "--accent-dim", "--st-", "--glass-",
                 "--border-default", "--border-light", "--border-focus"],
    "B 设计稿系": ["--bg-0", "--bg-1", "--bg-2", "--bg-3", "--bg-4",
                   "--tx-1", "--tx-2", "--tx-3", "--tx-4", "--ac", "--am"],
    "C 侦听台系": ["--canvas", "--panel", "--panel-raised", "--ink",
                   "--muted", "--faint", "--cyan"],
}

TARGETS = [
    "index.html",
    "workbench.html",
    "styles.css",
    "tokens.css",
    "pages/trace/trace.html",
    "pages/dict/dict.html",
    "pages/scenario/scenario.html",
    "pages/simcon/simcon.html",
    "pages/maintenance/maintenance.html",
    "pages/serial-profile/serial-profile.html",
    "pages/module-serial/module-serial.html",
    "pages/listener/index.html",
    "preview/index.html",
]


def classify(name):
    for dialect, prefixes in DIALECTS.items():
        for p in prefixes:
            if name == p or name.startswith(p):
                return dialect
    return None


def main():
    rows = []
    for rel in TARGETS:
        path = STATIC / rel
        if not path.exists():
            print("!! 缺失: %s" % rel, file=sys.stderr)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        defined = set(DEFINE.findall(text))
        used = set(USE.findall(text))
        rows.append((rel, defined, used))

    # 汇总：每个方言变量被哪些文件引用
    dialect_vars = {}
    for rel, defined, used in rows:
        for name in sorted(defined | used):
            d = classify(name)
            if not d:
                continue
            dialect_vars.setdefault(d, {}).setdefault(name, {"def": [], "use": []})
            if name in defined:
                dialect_vars[d][name]["def"].append(rel)
            if name in used:
                dialect_vars[d][name]["use"].append(rel)

    for d in ["A 工作台", "B 设计稿系", "C 侦听台系"]:
        print("\n" + "=" * 60)
        print("方言 %s" % d)
        print("=" * 60)
        if d not in dialect_vars:
            print("  (无)")
            continue
        for name in sorted(dialect_vars[d]):
            info = dialect_vars[d][name]
            print("  %-18s 定义:%-2d 引用:%-2d  %s" % (
                name, len(info["def"]), len(info["use"]),
                ",".join(sorted(set(info["use"]))[:4])))

    # 未接入 tokens.css 的页面
    print("\n" + "=" * 60)
    print("未引入 tokens.css 的页面（= 游离页面）")
    print("=" * 60)
    for rel, defined, used in rows:
        p = STATIC / rel
        if p.suffix != ".html":
            continue
        if "tokens.css" not in p.read_text(encoding="utf-8", errors="replace"):
            print("  %s" % rel)


if __name__ == "__main__":
    main()
