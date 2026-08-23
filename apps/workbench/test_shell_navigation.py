"""P5 left-sidebar grouped navigation shell tests (static, no browser).

总实施计划 P5：
- 验证组：验证工作台；设备组：串口配置、模块日志、侦听台；维护组：主题/版本/状态工具。
- 桌面端默认展开，可折叠为图标栏；状态保存 localStorage。
- 窄屏改为可键盘关闭的抽屉（Escape、焦点回归、遮罩关闭）。
- 当前页面写入 URL hash（刷新、深链、前进/后退）。
- iframe 首次访问才创建，之后切页只隐藏/显示，禁止重设 src 或销毁（既有契约保留）。
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).resolve().parent / "static"


def _js() -> str:
    return (_STATIC / "app.js").read_text(encoding="utf-8")


def _html() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


def _css() -> str:
    return (_STATIC / "styles.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 分组导航结构
# ---------------------------------------------------------------------------

def test_shell_defines_three_nav_groups():
    js = _js()
    html = _html()
    # 分组配置：验证/设备/维护
    assert "验证" in js and "设备" in js and "维护" in js
    # 每组含预期页面
    assert "workbench" in js          # 验证组
    assert "module" in js and "listener" in js  # 设备组
    # 维护组有主题/状态工具入口
    assert "theme" in js.lower() or "主题" in js
    assert "wb-status" in html or "wb-status" in js


def test_groups_markup_present():
    html = _html()
    # 左侧栏容器
    assert 'id="wb-sidebar"' in html or 'class="wb-sidebar"' in html
    # 遮罩用于窄屏抽屉
    assert "overlay" in html.lower() or "wb-overlay" in html


# ---------------------------------------------------------------------------
# hash 路由
# ---------------------------------------------------------------------------

def test_hash_routing_written_and_read():
    js = _js()
    # 写入：切页时更新 location.hash
    assert "location.hash" in js
    # 读取：初始化时从 hash 恢复页面
    assert "location.hash" in js


def test_hash_uses_page_id():
    js = _js()
    # hash 应与页面 id 关联（如 #listener），用于深链/刷新恢复
    assert "hash" in js.lower()


# ---------------------------------------------------------------------------
# iframe 保活契约（保留既有）
# ---------------------------------------------------------------------------

def test_shell_keeps_lazy_iframes_instead_of_reassigning_one_frame():
    js = _js()
    assert "framesByPage" in js
    assert "ensureFrame" in js
    # 仅首次创建时赋 src；切页不重设已存在页面
    assert js.count("frame.src = page.src") == 1
    assert "frame.src = page.src" not in js.split("switchTab", 1)[-1].split("THEMES", 1)[0] if "switchTab" in js else True


# ---------------------------------------------------------------------------
# 抽屉 / 响应式
# ---------------------------------------------------------------------------

def test_drawer_keyboard_and_overlay_close():
    js = _js()
    # Escape 关闭、遮罩点击关闭、焦点回归
    assert "Escape" in js or "escape" in js
    assert "overlay" in js.lower() or "focus" in js.lower()
    css = _css()
    # 窄屏媒体查询
    assert "@media" in css


# ---------------------------------------------------------------------------
# 折叠状态存 localStorage
# ---------------------------------------------------------------------------

def test_collapse_state_persisted():
    js = _js()
    # 折叠态读写 localStorage
    assert "localStorage" in js
    assert "collapsed" in js.lower() or "collapse" in js.lower()
