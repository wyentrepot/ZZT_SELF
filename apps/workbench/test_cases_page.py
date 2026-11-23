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


# ---------- REQS-0042 v11 D11-1..3 报告式呈现 ----------

REPORT_DEFAULT_BEGIN = "报告卡默认区 BEGIN"
REPORT_DEFAULT_END = "报告卡默认区 END"
# D11-2 点名的内部块名（含事实 id）：默认展示区一个都不许出现
BLOCK_IDS = (
    "archive.add",
    "nwk.topology",
    "meter.read_645",
    "meter.addr_probe",
    "reset.sta",
    "fix.simcon_session",
    "fix.simeter_session",
    "archive.contains",
    "nwk.nodes_online_min",
    "bench.sta_online",
)


def report_default_region() -> str:
    """取 cases.js 里「报告卡默认区」标记之间的源码（默认展示区的 DOM 模板都在这段）。"""
    js = js_text()
    assert REPORT_DEFAULT_BEGIN in js, "cases.js 缺少「报告卡默认区 BEGIN」反向守卫标记"
    assert REPORT_DEFAULT_END in js, "cases.js 缺少「报告卡默认区 END」反向守卫标记"
    start = js.index(REPORT_DEFAULT_BEGIN)
    start = js.index("\n", start) + 1          # 从标记行之后开始
    end = js.index(REPORT_DEFAULT_END)
    return js[start:end]


def test_report_card_dom_hooks():
    """D11-1：每条用例一张报告卡——标题行 / 原因行 / 证据行 / 开发者详情折叠。"""
    js = js_text()
    html = html_text()
    for hook in ("case-report", "report-headline", "report-reasons", "report-evidence", "dev-details"):
        assert f'data-hook="{hook}"' in js, f"报告卡缺少钩子 data-hook=\"{hook}\""
    # 逐步明细默认收起（hidden）
    assert 'data-hook="dev-details" hidden' in js, "开发者详情必须默认 hidden（收起）"
    assert 'data-hook="dev-toggle"' in js and "rc-dev-toggle" in js, "开发者详情缺少展开入口"
    # 折叠内仍保留现有 step/block/detail 表（排查口径不回退）
    assert "resultRowsHtml" in js and "run-steps" in js
    assert 'data-hook="breakpoint"' in js
    # 四态标题文案口径
    for text in ("VERDICT_HEAD", "未通过", "台架不具备", "说不清", "PASS"):
        assert text in js, f"报告式标题缺少文案 {text}"
    assert 'clip(reasons.join("；"), TITLE_REASON_MAX)' in js, "非通过结论应拼成人话原因（多条用「；」连接，且标题限长）"
    # 证据路径可点击/可复制
    assert 'data-hook="evidence-path"' in js
    assert "rc-copy" in js and "function copyText" in js
    # 样式沿用四态 token（不新增裸色值），见 test_theme_tokens_only_no_raw_colors
    for token in ("--color-status-pass", "--color-status-fail",
                  "--color-status-warn", "--color-fg-muted"):
        assert f"var({token})" in html, f"报告卡样式未复用令牌 {token}"


def test_report_default_area_never_shows_block_ids():
    """D11-2 反向守卫：默认展示区（dev-details 折叠之外）不得出现任何内部块名。"""
    js = js_text()
    region = report_default_region()
    assert len(region) > 1500, f"报告卡默认区代码段过短，守卫形同虚设：{len(region)} 字符"

    leaked = [block_id for block_id in BLOCK_IDS if block_id in region]
    assert not leaked, f"默认展示区出现内部块名（只允许出现在开发者详情）：{leaked}"
    assert "data-block=" not in region, "默认展示区不许输出 data-block（块名属性）"
    assert 'data-hook="dev-details"' not in region, "开发者详情折叠不应在默认展示区模板里拼接"
    # 卡片模板本身也在被守卫的区间里（否则守卫只是摆设）
    for hook in ("case-report", "report-headline", "report-reasons", "report-evidence"):
        assert f'data-hook="{hook}"' in region, f"报告卡模板 {hook} 不在受守卫的默认区区间内"

    # 块名 → 业务动作的词典确实存在（且按 D11-2 口径翻译），只是不在默认区
    for block_id, label in (
        ("archive.add", "档案下发"),
        ("nwk.topology", "组网"),
        ("meter.read_645", "抄表"),
        ("meter.addr_probe", "绑表（取表地址）"),
    ):
        assert f'["{block_id}", "{label}"]' in js, f"缺少块名翻译 {block_id} → {label}"
    assert "actionLabel(" in region, "默认展示区必须经 actionLabel() 翻成人话"
    assert "function actionLabel(" in js, "缺少块名 → 业务动作的翻译函数"


def test_report_accepts_summary_or_flat_fields():
    """D11-1/D11-3 数据来源：result.summary 对象或平铺 headline/reasons/evidence_paths。"""
    region = report_default_region()
    assert "obj(result.summary)" in region, "未读取 result.summary"
    for field in ("headline", "reasons", "evidence_paths"):
        assert field in region, f"未兼容平铺字段 {field}"
    for field in ("result.headline", "result.reasons", "result.evidence_paths"):
        assert field in region, f"未兼容平铺字段 {field}"
    # 证据条目支持 {path, kind, label} 与纯字符串两种形状
    for field in ("entry.path", "entry.kind", "EVIDENCE_KIND"):
        assert field in region, f"证据路径归一化缺少 {field}"
    # summary 里的 reason 单值/数组都要吃
    assert "summary.reason" in region


def test_report_degrades_without_summary():
    """降级：没有 summary 也要出人话、不白屏、不泄漏块 id。"""
    region = report_default_region()
    for field in ("failed_block", "blocked_block", "unmet_facts", "skipped_reason"):
        assert field in region, f"降级逻辑未消费断点字段 {field}"
    assert "DEV_HINT" in region and "详情见开发者详情" in region
    assert "function sanitizeHuman(" in region, "缺少人话护栏（把混进文案的块 id 换掉）"
    assert "blockReason(" in region and "problemRowReason(" in region
    assert "unmetFactReasons(" in region
    # 标题永远有兜底：没有用例名/结论也不许白屏
    assert '"未命名用例"' in region
    assert "function reportHeadline(" in region
    # 证据行没路径时给一句人话，而不是空白
    assert "证据路径未提供" in region


def test_report_reason_is_single_sentence():
    """① 原因行只保留一句人话：截到「——」/「（」前，单条 ≤40 字，最多 3 条；全文只进开发者详情。"""
    region = report_default_region()
    for token in ("REASON_MAX", "TITLE_REASON_MAX", "oneLineReason", "tightenReason", "clip"):
        assert token in region, f"缺少原因收敛能力：{token}"
    assert '"——"' in region and '"（"' in region and '"；"' in region
    # 已知后缀收敛：组网：明确不足 → 组网未完成
    assert 'return match[1] + "未完成"' in region
    # summary.reasons 优先（后端已做人话映射），没有才走降级收敛
    assert "if (summary.reasons.length)" in region
    assert "clip(sanitizeHuman(item), REASON_MAX)" in region
    assert "oneLineReason(item)" in region
    # 最多 3 条；其余（含块层原始 detail）折叠进开发者详情
    assert ".slice(0, 3)" in region
    assert "allReasons.length > 3" in js_text()
    assert "全部原因" in js_text()


def test_bench_banner_humanizes_internal_details():
    """② 台架横幅：默认区只出一句人话；WORKBENCH_BASE / http / 0x / 事实 id 只进开发者详情。"""
    js = js_text()
    html = html_text()
    # 开发者详情折叠（默认收起）与原始明细容器
    assert 'data-hook="bench-dev-details"' in html and " hidden" in html
    assert 'data-hook="bench-dev-toggle"' in html
    assert 'id="bench-raw"' in html
    # 内部词识别 + 一句人话映射
    assert "hasInternalToken" in js and "WORKBENCH_BASE" in js
    assert "台架正忙（上一轮运行未结束）" in js
    assert "function oneLineBenchReason(" in js
    assert "BENCH_REASON_MAX" in js
    assert "scrubInternal" in js and "injected_base_url" in js
    # 事实条只显示人话，id 落在 data-fact（不显示给用户）
    assert "factLabel(fact.id)" in js
    assert 'data-fact="' in js
    assert """'<span class="dot"></span>' + esc(fact.id)""" not in js
    # 原始 errors/detail 落进开发者详情
    assert "S.benchRaw" in js and "rawBox.textContent" in js
    assert 'raw.push("事实 " + fact.id' in js
    assert 'raw.push("端口 " + port.id' in js


def test_list_row_summary_is_business_chain():
    """③ 行内摘要 = 业务动作链（5 步：档案 → 组网 → 绑表 → 抄表）；帧标识只留在展开区。"""
    js = js_text()
    for token in ("STEP_ACTION", "FRAME_TOKEN", "stepAction", "stepChain", "stepSummary"):
        assert token in js, f"行内摘要缺少 {token}"
    assert '" → "' in js, "业务动作链应用「 → 」连接"
    assert "H[-\\s]?F" in js, "缺少帧标识（如 11H-F1 / 10H-F21）剥离规则"
    for action in ('"档案"', '"组网"', '"绑表"', '"抄表"', '"复位"'):
        assert action in js, f"动作链缺少业务动作 {action}"
    # 展开区仍保留 do/expect 原文（不裁剪排查信息）
    assert 'data-hook="case-steps"' in js and "step.expect" in js


def test_report_summary_is_one_sentence():
    """顶部汇总一句话：本轮 2 例全部通过 / 本轮 1 例通过、1 例未通过（组网超时）。"""
    js = js_text()
    assert "本轮 " in js
    assert '" 例全部通过"' in js
    assert '" 例通过"' in js and '" 例未通过"' in js
    assert '" 例台架不具备"' in js and '" 例说不清"' in js
    # 汇总与卡片共用一套原因口径（reasonList），不另起一套
    assert 'reasonList(result, "fail"' in js


# ---------- 运行时冒烟：node + 最小 DOM 桩跑页面逻辑（无浏览器） ----------


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能跑用例库页面的逻辑冒烟")
def test_page_logic_smoke():
    """跑 testdata/cases_smoke.js 的 6 条路径：

    ① 后端全 404 → 降级不白屏；② 正常路径（分组/排序/方案恢复/运行/轮询/断点）；
    ③ 台架未就绪 → 运行置灰 + 逐项原因；④ 刷新接回上一轮 job；
    ⑤ 报告卡（后端给 summary 直接渲染 / 不给则降级，默认区无块 id）；
    ⑥ 报告卡（全通过 → 「本轮 N 例全部通过」）。
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
    # 6 条路径都要有 OK 标记（少跑一条等同静默降级）
    for case in ("后端 404 降级", "正常路径（含断点渲染）", "台架未就绪置灰", "刷新接回上一轮 job",
                 "报告卡（summary 与降级）", "报告卡（全部通过）"):
        assert "OK  " + case in completed.stdout, f"冒烟未覆盖「{case}」：\n" + output
