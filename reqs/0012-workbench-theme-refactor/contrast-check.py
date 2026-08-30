# -*- coding: utf-8 -*-
"""WCAG 2.1 对比度审计：工作台三套 token 方言的正文/次级文字可读性。
输出：对比度比值 + AA(4.5) / AA-large(3.0) 达标情况。
"""
import sys

def srgb_to_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

def hex_rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(ch * 2 for ch in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def luminance(h):
    r, g, b = hex_rgb(h)
    return 0.2126*srgb_to_lin(r) + 0.7152*srgb_to_lin(g) + 0.0722*srgb_to_lin(b)

def contrast(fg, bg):
    l1, l2 = luminance(fg), luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)

CASES = [
    # (分组, 说明, 前景, 背景, 是否大字/非文本)
    ("A 工作台 tokens.css", "正文 fg-default / bg-page",        "#e2e8f0", "#0a1628", False),
    ("A 工作台 tokens.css", "次级 fg-muted / bg-surface",       "#94a3b8", "#0f1f3d", False),
    ("A 工作台 tokens.css", "弱化 fg-dim / bg-surface",         "#64748b", "#0f1f3d", False),
    ("A 工作台 tokens.css", "弱化 fg-dim / bg-input",           "#64748b", "#0d1a30", False),
    ("A 工作台 tokens.css", "表头 fg-dim / bg-input",           "#64748b", "#0d1a30", False),
    ("A 工作台 tokens.css", "accent 青 / bg-page",              "#06b6d4", "#0a1628", False),
    ("A 工作台 tokens.css", "accent-fg 深字 / accent 底",        "#0a1628", "#06b6d4", False),
    ("A 工作台 tokens.css", "st-pass / bg-page",                "#10b981", "#0a1628", False),
    ("A 工作台 tokens.css", "st-warn / bg-page",                "#f59e0b", "#0a1628", False),
    ("A 工作台 tokens.css", "st-fail / bg-page",                "#ef4444", "#0a1628", False),
    ("B 设计稿 trace/dict",  "正文 tx-1 / bg-0",                 "#e8f0f7", "#090c10", False),
    ("B 设计稿 trace/dict",  "正文 tx-1 / bg-1",                 "#e8f0f7", "#0e1319", False),
    ("B 设计稿 trace/dict",  "次级 tx-2 / bg-1",                 "#9aabbb", "#0e1319", False),
    ("B 设计稿 trace/dict",  "弱化 tx-3 / bg-1",                 "#65798c", "#0e1319", False),
    ("B 设计稿 trace/dict",  "最弱 tx-4 / bg-2",                 "#465768", "#131a22", False),
    ("B 设计稿 trace/dict",  "ac 青 / bg-1",                     "#22d3ee", "#0e1319", False),
    ("C 侦听台 module-serial", "正文 ink / panel",               "#e8f0f7", "#0d1722", False),
    ("C 侦听台 module-serial", "次级 muted / panel",             "#7e91a4", "#0d1722", False),
    ("C 侦听台 module-serial", "弱化 faint / panel",             "#536678", "#0d1722", False),
    ("C 侦听台 module-serial", "弱化 faint / canvas-2",          "#536678", "#0a121b", False),
    ("C 侦听台 module-serial", "cyan / panel",                   "#45e0c2", "#0d1722", False),
]

AA, AA_LARGE = 4.5, 3.0

def verdict(ratio, large=False):
    need = AA_LARGE if large else AA
    if ratio >= need:
        return "PASS"
    return "FAIL" if ratio < need - 1.0 else "WARN"

enc = sys.stdout.encoding or 'utf-8'
out = []
out.append(f"{'方言':<22}{'组合':<28}{'对比度':>8}  {'判定':<5}")
out.append("-" * 72)
last = None
fails = 0
for grp, desc, fg, bg, large in CASES:
    r = contrast(fg, bg)
    v = verdict(r, large)
    if v == "FAIL":
        fails += 1
    if last and last != grp:
        out.append("")
    last = grp
    out.append(f"{grp:<22}{desc:<28}{r:>7.2f}:1  {v:<5}")
out.append("-" * 72)
out.append(f"合计 {len(CASES)} 组，其中 {fails} 组未达 WCAG AA（4.5:1）")

text = "\n".join(out)
print(text)
with open("contrast_result.txt", "w", encoding="utf-8") as f:
    f.write(text)
