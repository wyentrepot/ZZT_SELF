"""REQS-0042 §3.7 UI-1..7：用例库页面的契约。

静态断言（不启浏览器、不连后端）：页面文件存在、导航表口径、
页面引用的静态资源真实存在、主题只用 var(--color-*) 令牌、
四态着色与断点可视化的 DOM 约定齐全、API 未挂载时降级路径存在；
另加一条无浏览器运行时冒烟（node + 最小 DOM 桩，见 testdata/cases_smoke.js）。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent
STATIC = APP_DIR / "static"
PAGE_DIR = STATIC / "pages" / "cases"
HTML_PATH = PAGE_DIR / "cases.html"
JS_PATH = PAGE_DIR / "cases.js"
APP_JS_PATH = STATIC / "app.js"
SMOKE_PATH = APP_DIR / "testdata" / "cases_smoke.js"

RAW_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\s*\(")
COLOR_TOKEN_USE = re.compile(r"var\(--color-[a-z0-9-]+")


def html_text() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def js_text() -> str:
    return JS_PATH.read_text(encoding="utf-8")


def app_js_text() -> str:
    return APP_JS_PATH.read_text(encoding="utf-8")


# ---------- UI-1 页面与导航 ----------


def test_cases_page_files_exist():
    assert HTML_PATH.is_file(), "缺少 apps/workbench/static/pages/cases/cases.html"
    assert JS_PATH.is_file(), "缺少 apps/workbench/static/pages/cases/cases.js"
    html = html_text()
    assert "用例库" in html
    assert 'src="cases.js"' in html


def test_nav_registers_cases_page():
    app = app_js_text()
    assert '"/static/pages/cases/cases.html"' in app
    assert '{ id: "cases", title: "用例库"' in app
    # 分组归属：与 trace/dict/scenario 同组（辅助）
    assert '{ name: "辅助", pages: ["trace", "dict", "scenario", "cases"] }' in app
    # 图标表里有对应条目（否则侧栏渲染成空白图标）
    assert "cases:" in app


def test_nav_no_longer_lists_autotest():
    app = app_js_text()
    # D2-11：旧「自动测试」页从导航移除……
    assert "自动测试" not in app
    assert '"/static/pages/autotest/autotest.html"' not in app
    assert '"autotest"' not in app
    # ……但页面文件本身保留、不删（回归保护）
    assert (STATIC / "pages" / "autotest" / "autotest.html").is_file()


def test_page_referenced_static_assets_exist():
    html = html_text()
    refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert refs, "页面没有引用任何 /static 资源"
    for ref in refs:
        rel = ref[len("/static/"):]
        target = STATIC / rel
        assert target.is_file(), f"页面引用的静态资源不存在：{ref}"


# ---------- UI-7 主题合规 ----------


def test_theme_tokens_only_no_raw_colors():
    for name, text in (("cases.html", html_text()), ("cases.js", js_text())):
        raw = RAW_COLOR.findall(text)
        assert not raw, f"{name} 出现裸色值（只允许 var(--color-*) 令牌）：{sorted(set(raw))}"
    html = html_text()
    assert "/static/tokens-v2.css" in html
    assert "/static/tokens.css" not in html
    assert COLOR_TOKEN_USE.search(html), "页面样式里没有使用任何 var(--color-*) 令牌"


def test_theme_boot_script_follows_shell_contract():
    html = html_text()
    # 与 autotest/simeter 同口径：先按白名单恢复保存的主题，再接收壳广播
    assert 'localStorage.getItem("wb-theme")' in html
    assert "document.documentElement.dataset.theme" in html
    assert "wb-theme-change" in html
    assert "--theme-registry" in html


# ---------- UI-1 用例列表 ----------


def test_case_list_rows_show_steps_and_expandable_detail():
    js = js_text()
    html = html_text()
    assert 'data-hook="case-list"' in html
    # 每行：用例名 + 步骤数 + 步骤摘要
    assert "step_count" in js
    assert "步</span>" in js
    assert "stepSummary" in js
    # 展开看完整 do / expect
    assert 'data-hook="case-steps"' in js
    assert "step.expect" in js
    assert "期望：" in js


# ---------- D10-2 分类分组与筛选 ----------


def test_category_dimensions_and_filters():
    js = js_text()
    html = html_text()
    for dim in ("usage", "domain", "scene", "region"):
        assert f'"{dim}"' in js
        assert f'id="f-{dim}"' in html
    assert 'id="f-protocol"' in html
    assert 'id="group-by"' in html
    # 取值口径（D10-2）
    assert "common" in js and "normal" in js and "rare" in js
    assert '"CCO"' in js and '"STA"' in js and '"ECU"' in js and '"bench"' in js
    assert "组网" in js and "抄表" in js
    assert "国网通用" in js and "南网通用" in js


def test_category_missing_falls_back_to_uncategorized():
    js = js_text()
    assert "未分类" in js
    # 缺失的维度返回空串 → 归入「未分类」，用例仍可见（不过滤掉）
    assert 'return UNCAT_LABEL;' in js
    assert 'data-hook="uncategorized"' in js
    assert 'data-group="' in js  # 分组渲染仍会输出该组


def test_default_sort_order_is_usage_domain_scene_title():
    js = js_text()
    # 只看「默认排序」那一段：先用常用度，再设备域，再场景，最后名称兜底
    block = js[js.index("// 默认排序（D10-2）"):]
    block = block[: block.index("\n    };")]
    order = ["USAGE_ORDER", "DOMAIN_ORDER", "SCENE_ORDER", "localeCompare"]
    positions = [block.index(token) for token in order]
    assert positions == sorted(positions), "默认排序必须是 常用度 → 设备域 → 场景 → 名称"
    assert 'sort === "steps"' in js and 'sort === "name"' in js


# ---------- UI-2 台架就绪横幅 ----------


def test_bench_banner_and_run_gating():
    js = js_text()
    html = html_text()
    assert "/api/cases/bench" in js
    assert 'data-hook="bench-ports"' in html
    assert 'data-hook="bench-facts"' in html
    assert 'data-hook="bench-reasons"' in html
    # 三态事实取值 + 端口 usable/errors
    assert "usable" in js and "errors" in js
    assert '"yes"' in js and '"no"' in js and '"unknown"' in js
    # 不满足 → 置灰运行按钮 + 逐项原因
    assert "function gate()" in js
    assert "disabled = !decision.ok" in js
    assert "台架未就绪" in js


# ---------- UI-3 勾选 → 存方案 → 刷新恢复 ----------


def test_selection_survives_reload_via_suite_and_localstorage():
    js = js_text()
    html = html_text()
    assert 'id="btn-save-suite"' in html
    assert "/api/cases/suites" in js
    assert 'id="suite-select"' in html
    assert "restoreState" in js
    assert 'localStorage.setItem(STORE_KEY' in js
    assert 'var STORE_KEY = "wb-cases-state"' in js
    assert "suite_id" in js


# ---------- UI-4 两级运行 + 轮询 ----------


def test_two_level_run_and_job_polling():
    js = js_text()
    html = html_text()
    assert 'data-hook="run-now"' in html
    assert 'data-hook="run-suite"' in html
    assert "/api/cases/run" in js
    assert "suite_id" in js and "case_ids" in js
    assert "/api/cases/jobs/" in js
    assert "setTimeout(pollJob" in js
    assert 'data-hook="job-panel"' in html


# ---------- UI-5 断点可视化 ----------


def test_breakpoint_visualization_fields():
    js = js_text()
    for field in ("failed_block", "blocked_block", "unmet_facts", "self_healed"):
        assert field in js, f"断点可视化缺少字段 {field}"
    assert 'data-hook="breakpoint"' in js
    assert 'class="bp-item bp-failed"' in js
    assert 'class="bp-item bp-blocked"' in js
    assert 'class="bp-item bp-fact"' in js
    assert 'class="bp-item bp-heal"' in js
    # 问题块在步骤表里高亮
    assert "is-problem" in js


# ---------- UI-6 四态着色 ----------


def test_four_state_verdict_coloring():
    js = js_text()
    html = html_text()
    for cls in ("verdict-pass", "verdict-fail", "verdict-blocked", "verdict-inconclusive"):
        assert f".{cls}" in html, f"缺少四态样式类 .{cls}"
    # 四态语义文案
    for label in ("PASS", "FAIL", "BLOCKED", "INCONCLUSIVE"):
        assert label in js
    assert "台架问题" in html or "台架" in js
    # blocked 用灰（不是红/绿）：配色取自中性令牌
    blocked_rule = [line for line in html.splitlines() if line.startswith(".verdict-blocked")]
    assert blocked_rule, "缺少 .verdict-blocked 样式规则"
    assert "var(--color-fg-muted)" in blocked_rule[0]
    assert "var(--color-status-fail)" not in blocked_rule[0]
    # inconclusive 用 warn（黄）语义令牌
    inconclusive_rule = [line for line in html.splitlines() if line.startswith(".verdict-inconclusive")]
    assert inconclusive_rule, "缺少 .verdict-inconclusive 样式规则"
    assert "var(--color-status-warn)" in inconclusive_rule[0]


# ---------- 后端未就绪时优雅降级 ----------


def test_graceful_degradation_when_api_missing():
    js = js_text()
    html = html_text()
    assert "后端未就绪" in js
    assert "error.status === 404" in js  # 404 单独识别，不当成白屏
    assert 'data-hook="api-state"' in html
    # 轮询 404 时停止而不是无限重试
    assert "已停止轮询" in js


# ---------- 守卫：旧「自动测试」逻辑不许被粘回用例库页 ----------


def test_cases_page_has_no_autotest_leftovers():
    """REQS-0042 D2-11：旧「自动测试」页只是保留文件，不许把它的逻辑粘回用例库。

    导航注册的反向断言在 test_app.py；这里守住页面自身——pages/cases/ 下
    任何文件都不应再出现 autotest 字样（旧页的 /api/autotest/* 调用、旧钩子等）。
    """
    leftovers = []
    for path in sorted(PAGE_DIR.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "autotest" in text.lower():
            leftovers.append(path.name)
    assert not leftovers, f"pages/cases/ 残留旧「自动测试」引用：{leftovers}"


# ---------- 运行时冒烟：node + 最小 DOM 桩跑页面逻辑（无浏览器） ----------


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能跑用例库页面的逻辑冒烟")
def test_page_logic_smoke():
    """跑 testdata/cases_smoke.js 的 4 条路径：

    ① 后端全 404 → 降级不白屏；② 正常路径（分组/排序/方案恢复/运行/轮询/断点）；
    ③ 台架未就绪 → 运行置灰 + 逐项原因；④ 刷新接回上一轮 job。
    断言失败时把 node 的 stdout/stderr 全带进 message，便于定位。
    """
    node = shutil.which("node")
    assert SMOKE_PATH.is_file(), f"缺少冒烟脚本 {SMOKE_PATH}"
    completed = subprocess.run(
        [node, str(SMOKE_PATH), str(JS_PATH)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    assert completed.returncode == 0, "用例库页面逻辑冒烟失败：\n" + output
    assert "SMOKE PASS" in completed.stdout, "冒烟未打印 SMOKE PASS：\n" + output
    # 4 条路径都要有 OK 标记（少跑一条等同静默降级）
    for case in ("后端 404 降级", "正常路径（含断点渲染）", "台架未就绪置灰", "刷新接回上一轮 job"):
        assert "OK  " + case in completed.stdout, f"冒烟未覆盖「{case}」：\n" + output
