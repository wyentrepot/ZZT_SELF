# -*- coding: utf-8 -*-
"""WCAG 2.1 对比度回归 —— REQS-0012 P1（适配 tokens-v2.css 三层 token 体系）。

与旧版 contrast-check.py（审计三套方言的 21 组硬编码值）的区别：
  - 直接解析 apps/workbench/static/tokens-v2.css 并展开 var() 引用链，
    调色板改动后无需同步本脚本；
  - 分「门禁区」与「观察区」两段：

  门禁区（0 FAIL 才通过）—— 与 tokens-v2 声明的契约及旧版 21 组审计同口径：
    · 文字四档 fg-default/muted/subtle/dim
      × 实心底四档 canvas/surface/raised/input（tokens-v2 注释声明的四种底色）
    · accent 反色字（on accent 填充）、accent 强调字（on canvas/surface）
    · 收发方向色 / 状态色作为文字（on canvas）

  观察区（如实报告，不阻塞）—— 门禁外的更严组合，供 P2 接入页面时决策：
    · 上述全部前景色 × 第五档实心底 elevated（compat 层 --panel-raised 指向它，
      P2 页面接入后其上是否出现弱化文字/状态文字由人工确认）
    · 状态/方向色 × surface/raised/elevated

附录列出旧方言遗留 FAIL（P2 接入 compat-dialects.css 后自然消除，不计入门禁）。

用法：python contrast-v2.py
退出码：0 = 门禁通过；1 = 门禁存在 FAIL。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOKENS_V2 = os.path.normpath(os.path.join(
    HERE, "..", "..", "apps", "workbench", "static", "tokens-v2.css"))

PROP_RE = re.compile(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);")


def parse_tokens(path):
    """把 tokens-v2.css 解析为 base / daylight 两层自定义属性字典。
    base 收 :root 及 midnight 块；daylight 收 daylight 块（覆盖同名 base 值）。"""
    base, daylight = {}, {}
    with open(path, encoding="utf-8") as f:
        css = f.read()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # 去注释
    pos = 0
    in_daylight = False
    for m in re.finditer(r"[{}]", css):
        if m.group() == "{":
            header = css[pos:m.start()]
            in_daylight = 'data-theme="daylight"' in header
        else:
            body = css[pos:m.start()]
            target = daylight if in_daylight else base
            target.update(PROP_RE.findall(body))
        pos = m.end()
    return base, daylight


def resolve(value, scope, depth=0):
    """展开 var(--x) 引用链到字面值；无法解析时原样返回。"""
    if depth > 10:
        return value
    m = re.fullmatch(r"\s*var\(\s*(--[a-zA-Z0-9-]+)\s*\)\s*", value)
    if m:
        name = m.group(1)
        if name in scope:
            return resolve(scope[name], scope, depth + 1)
        return value
    return value.strip()


HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def srgb_to_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * srgb_to_lin(r) + 0.7152 * srgb_to_lin(g) + 0.0722 * srgb_to_lin(b)


def contrast(fg, bg):
    l1, l2 = luminance(fg), luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


FG_TIERS = [
    ("正文", "--color-fg-default"),
    ("次级", "--color-fg-muted"),
    ("弱化", "--color-fg-subtle"),
    ("最弱", "--color-fg-dim"),
]
BG_GATE = [  # 门禁底色四档（tokens-v2 注释声明的审计口径）
    ("canvas", "--color-bg-canvas"),
    ("surface", "--color-bg-surface"),
    ("raised", "--color-bg-raised"),
    ("input", "--color-bg-input"),
]
BG_OBS = [  # 观察底色（门禁外）
    ("elevated", "--color-bg-elevated"),
]
# 语义色作为文字/图标（门禁只审 on canvas，与旧版 21 组口径一致）
TEXT_COLORS = [
    ("accent 强调字", "--color-accent-emphasis", True),
    ("接收方向 RX", "--color-dir-rx", True),
    ("发送方向 TX", "--color-dir-tx", True),
    ("状态 pass", "--color-status-pass", True),
    ("状态 warn", "--color-status-warn", True),
    ("状态 fail", "--color-status-fail", True),
    ("状态 info", "--color-status-info", True),
    ("状态 inconclusive", "--color-status-inconclusive", True),
]

AA = 4.5


def audit(theme_name, scope, lines, gate):
    """审计一个主题。gate=True 审门禁组合（FAIL 计数）；False 审观察组合（WARN 计数）。"""
    resolved = {k: resolve(v, scope) for k, v in scope.items()}
    fails = 0
    warns = 0
    total = 0

    def emit(ratio, desc):
        nonlocal fails, warns, total
        total += 1
        if ratio >= AA:
            tag = "PASS"
        elif gate:
            tag = "FAIL"
            fails += 1
        else:
            tag = "⚠WARN"
            warns += 1
        lines.append(f"  {tag}  {ratio:6.2f}:1  {desc}")

    lines.append(f"  —— {'门禁区' if gate else '观察区'} · 主题 {theme_name} ——")

    # 文字四档 × 底色（门禁=四档实心底；观察=elevated 档）
    for bg_label, bg_var in (BG_GATE if gate else BG_OBS):
        bg = resolved.get(bg_var, "")
        if not HEX_RE.match(bg):
            lines.append(f"  [跳过] {bg_var} 无法解析: {bg!r}")
            continue
        for fg_label, fg_var in FG_TIERS:
            fg = resolved.get(fg_var, "")
            if not HEX_RE.match(fg):
                lines.append(f"  [跳过] {fg_var} 无法解析: {fg!r}")
                continue
            emit(contrast(fg, bg), f"{fg_label} {fg} / {bg_label} {bg}  ({fg_var} on {bg_var})")

    # accent 反色字（on accent 填充，仅门禁）
    if gate:
        fg = resolved.get("--color-accent-fg", "")
        bg = resolved.get("--color-accent", "")
        if HEX_RE.match(fg) and HEX_RE.match(bg):
            emit(contrast(fg, bg),
                 f"accent 反色字 {fg} / accent 填充 {bg}  (--color-accent-fg on --color-accent)")

    # 语义色作为文字/图标：门禁审 on canvas（与旧版口径一致），观察区审 on elevated
    for desc, var, _ in TEXT_COLORS:
        fg = resolved.get(var, "")
        if not HEX_RE.match(fg):
            lines.append(f"  [跳过] {var} 无法解析: {fg!r}")
            continue
        bg_label, bg_var = ("canvas", "--color-bg-canvas") if gate else ("elevated", "--color-bg-elevated")
        bg = resolved.get(bg_var, "")
        if not HEX_RE.match(bg):
            continue
        emit(contrast(fg, bg), f"{desc} {fg} / {bg_label} {bg}  ({var} on {bg_var})")

    return total, fails, warns


def main():
    base, daylight = parse_tokens(TOKENS_V2)
    # daylight 的 var() 引用需在「base + daylight 覆盖」合并作用域里解析
    scopes = [("midnight 墨夜", base), ("daylight 晴昼", {**base, **daylight})]

    lines = []
    lines.append(f"审计对象：{TOKENS_V2}")
    lines.append(f"门禁：AA >= {AA}:1，0 FAIL 方可通过；观察区 ⚠ 项不阻塞，供 P2 决策")
    lines.append("=" * 80)

    gate_total = gate_fails = 0
    obs_total = obs_warns = 0

    lines.append("")
    lines.append("◆ 门禁区（对应旧版 21 组审计口径 + tokens-v2 声明的四种底色）")
    for name, scope in scopes:
        t, f, w = audit(name, scope, lines, gate=True)
        gate_total += t
        gate_fails += f

    lines.append("")
    lines.append("◆ 观察区（门禁外更严组合：第五档实心底 elevated 等）")
    for name, scope in scopes:
        t, f, w = audit(name, scope, lines, gate=False)
        obs_total += t
        obs_warns += w

    lines.append("")
    lines.append("附录 · 旧方言遗留问题（未计入门禁，P2 页面接入 compat-dialects.css 后消除）")
    lines.append("-" * 80)
    for item, fix in [
        ("A 系 --fg-dim #64748b / surface 3.44", "tokens-v2 --color-fg-dim 已提亮，别名映射后消除"),
        ("B 系 --tx-4 #465768 / bg-2 2.36", "compat 层 --tx-4 → --color-fg-dim，接入后消除"),
        ("C 系 --faint #536678 / panel 3.04", "compat 层 --faint → --color-fg-subtle，接入后消除"),
    ]:
        lines.append(f"  [遗留] {item}  →  {fix}")

    lines.append("")
    lines.append("=" * 80)
    verdict = "✅ 通过" if gate_fails == 0 else "❌ 未通过"
    lines.append(f"门禁 {gate_total} 组 / FAIL {gate_fails} —— {verdict}"
                 f"    （观察区 {obs_total} 组 / ⚠ {obs_warns}，见上表）")

    text = "\n".join(lines)
    print(text)
    out = os.path.join(HERE, "contrast-v2-result.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\n结果已写入 {out}", file=sys.stderr)
    return 1 if gate_fails else 0


if __name__ == "__main__":
    sys.exit(main())
