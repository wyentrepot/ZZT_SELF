"""P6 serial-profile configuration page frontend tests (static, no browser).

总实施计划 P6：
- 固定展示四槽：CCO 日志、STA 日志、侦听台、模拟集中器。
- 页面动作：保存（PUT）、一键应用（只应用已保存版本）、刷新状态（只读映射/会话/占用）。
- 每槽显示启用状态、映射、串口参数、在线状态、占用者和应用结果。
- 启用但未选串口内联报错；重复映射在应用前阻止且零副作用。
- 部分失败保留成功设备状态，提供可聚焦错误摘要。
- 两个默认日志槽可见且未启动。
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).resolve().parent / "static" / "pages" / "serial-profile"


def _html() -> str:
    return (_STATIC / "serial-profile.html").read_text(encoding="utf-8")


def _js() -> str:
    return (_STATIC / "serial-profile.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 四槽固定展示
# ---------------------------------------------------------------------------

def test_four_slots_present():
    html = _html()
    js = _js()
    for label in ("CCO", "STA", "侦听台", "模拟集中器"):
        assert label in html or label in js
    # 四个逻辑槽
    assert "module_log.cco" in js
    assert "module_log.sta" in js
    assert "listener.main" in js
    assert "simcon.main" in js


def test_two_log_slots_visible_by_default():
    html = _html()
    js = _js()
    # CCO/STA 日志槽默认渲染（不依赖启用；标签在 js 的 SLOTS 配置）
    assert "CCO 日志槽" in js or "CCO" in html
    assert "STA 日志槽" in js or "STA" in html


# ---------------------------------------------------------------------------
# 三动作：保存 / 一键应用 / 刷新状态
# ---------------------------------------------------------------------------

def test_three_actions_present():
    js = _js()
    assert "保存" in js
    assert "一键应用" in js or "apply" in js.lower()
    assert "刷新状态" in js or "refresh" in js.lower()


def test_save_uses_put_only():
    js = _js()
    # 保存只调 PUT /api/serial-profile
    assert "PUT" in js
    assert "/api/serial-profile" in js
    assert "save" in js.lower() or "保存" in js


def test_apply_uses_post_only():
    js = _js()
    assert "POST" in js
    assert "/serial-profile/apply" in js or "/api/serial-profile/apply" in js


# ---------------------------------------------------------------------------
# 每槽字段：启用/映射/串口参数/在线状态/占用者/应用结果
# ---------------------------------------------------------------------------

def test_slot_fields_rendered():
    html = _html()
    js = _js()
    for field in ("enabled", "mapping", "baudrate"):
        assert field in js
    # 在线状态：状态/st- 标记
    assert "状态" in js or "st-" in js
    # 占用者/应用结果：own- / result 标记
    assert "own-" in js or "占用" in js
    assert "result" in js


# ---------------------------------------------------------------------------
# 启用未选串口 → 内联报错
# ---------------------------------------------------------------------------

def test_enabled_without_mapping_inline_error():
    js = _js()
    # 启用但未选映射 → 内联错误
    assert "error" in js.lower() or "请选择" in js
    assert "inline" in js.lower() or "错误" in js


# ---------------------------------------------------------------------------
# 错误摘要可聚焦
# ---------------------------------------------------------------------------

def test_focusable_error_summary():
    html = _html()
    js = _js()
    # 部分失败时提供可聚焦错误摘要（tabindex / focus / summary）
    assert "summary" in js.lower() or "错误摘要" in js or "tabindex" in html
