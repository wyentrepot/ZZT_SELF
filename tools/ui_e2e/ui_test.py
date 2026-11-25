#!/usr/bin/env python3
"""UI 真实操作验证：Playwright(headless Chromium) 操控工作台外壳，点击完成与 AI 脚本同源的自动测试。

覆盖：导航入口 → 页面渲染 → 搜索/标签过滤 → 行内单跑（实板 CCO）→ 详情抽屉（结果/文档）
→ 链路一键跑 → 勾选批量跑。每步截图到 /tmp/req-ui-evidence/。
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:8790"
EV = Path("/tmp/req-ui-evidence")
EV.mkdir(exist_ok=True)
results = []


def shot(page, name):
    page.screenshot(path=str(EV / f"{name}.png"), full_page=False)
    results.append({"step": name, "screenshot": f"{name}.png", "t": time.strftime("%H:%M:%S")})
    print(f"[shot] {name}")


def frame(page):
    return page.frame_locator('iframe[data-page-id="autotest"]')


def wait_job_done(fl, timeout=150):
    """轮询任务面板直到出现汇总（job-summary 有内容且 chips 齐全）。"""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        s = fl.locator("#job-summary").inner_text().strip()
        if s and "运行中" not in s:
            return s
        last = s
        time.sleep(1.5)
    raise TimeoutError(f"job 未在 {timeout}s 内结束（last={last}）")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(15000)

        # 1. 外壳加载 + 导航入口
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector("#wbNavGroups")
        nav_item = page.get_by_text("自动测试", exact=True)
        expect(nav_item).to_be_visible()
        shot(page, "01-shell-nav")

        # 2. 点击进入自动测试页，iframe 渲染
        nav_item.click()
        fl = frame(page)
        fl.locator("#pill-text").wait_for(state="visible", timeout=20000)
        pill = fl.locator("#pill-text").inner_text()
        assert "22" in pill, f"用例数异常: {pill}"
        rows = fl.locator("#list-wrap tbody tr")
        expect(rows.first).to_be_visible()
        n_rows = rows.count()
        shot(page, "02-autotest-page")
        print(f"[ok] 页面渲染 {n_rows} 行, pill={pill}")

        # 3. 搜索过滤
        fl.locator("#q").fill("vendor_version")
        time.sleep(0.4)
        n_filtered = fl.locator("#list-wrap tbody tr").count()
        assert n_filtered == 1, f"搜索过滤异常: {n_filtered}"
        shot(page, "03-search-filter")
        fl.locator("#q").fill("")
        time.sleep(0.4)

        # 4. 标签过滤
        fl.locator('#tag-bar .chip[data-tag="cco-afn"]').click()
        time.sleep(0.4)
        n_tag = fl.locator("#list-wrap tbody tr").count()
        assert 0 < n_tag < n_rows, f"标签过滤异常: {n_tag}"
        shot(page, "04-tag-filter")
        fl.locator('#tag-bar .chip[data-tag="cco-afn"]').click()  # 取消
        time.sleep(0.3)

        # 5. 行内 ▶ 单跑一个真实用例（CCO 实板 03H-F1）
        row = fl.locator('tr[data-id="cco_03f1_vendor_version"]')
        row.locator("button.run-one").click()
        fl.locator("#job-panel").wait_for(state="visible")
        summary = wait_job_done(fl, timeout=120)
        assert summary.startswith("汇总 1/1"), f"单跑结果异常: {summary}"
        chip = fl.locator('#job-chips .badge.pass').first.inner_text()
        shot(page, "05-run-one-pass")
        print(f"[ok] 单跑 PASS, summary={summary}, chip={chip}")

        # 6. 详情抽屉：结果 + 文档
        fl.locator('tr[data-id="cco_03f1_vendor_version"]').click()
        fl.locator("#drawer").wait_for(state="visible")
        fl.locator("#d-result").click()
        fl.locator("#d-extra pre").wait_for(state="visible")
        result_txt = fl.locator("#d-extra pre").inner_text()
        assert "passed" in result_txt and "cco_03f1_vendor_version" in result_txt
        shot(page, "06-drawer-result")
        fl.locator("#d-doc").click()
        time.sleep(0.6)
        doc_txt = fl.locator("#d-extra pre").inner_text()
        assert "用例 ID" in doc_txt, doc_txt[:120]
        shot(page, "07-drawer-doc")
        fl.locator("#d-close").click()
        print("[ok] 抽屉结果+文档 OK")

        # 7. 链路一键跑（meters-brain：1 例，无串口依赖）
        fl.locator('button.chain-btn[data-chain="meters-brain"]').click()
        fl.locator("#job-panel").wait_for(state="visible")
        summary = wait_job_done(fl, timeout=120)
        assert summary.startswith("汇总 1/1"), f"链路结果异常: {summary}"
        shot(page, "08-chain-pass")
        print(f"[ok] 链路 meters-brain {summary}")

        # 8. 勾选批量跑（2 个查询类用例）
        for cid in ("cco_03f1_vendor_version", "cco_03f4_master_addr"):
            fl.locator(f'tr[data-id="{cid}"] input.sel-row').check()
        sel_txt = fl.locator("#sel-count").inner_text()
        assert "2" in sel_txt, f"选中计数异常: {sel_txt}"
        fl.locator("#btn-run-sel").click()
        fl.locator("#job-panel").wait_for(state="visible")
        summary = wait_job_done(fl, timeout=180)
        assert summary.startswith("汇总 2/2"), f"批量跑结果异常: {summary}"
        shot(page, "09-run-selected-pass")
        print(f"[ok] 批量 2 例 {summary}")

        browser.close()

    out = EV / "ui_test_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nALL UI STEPS PASS — {len(results)} 步, 证据在 {EV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
