#!/usr/bin/env python3
"""补测：tag 选中 → ▶ 跑该组（对齐 runner --group）。"""
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:8790"
EV = Path("/tmp/req-ui-evidence")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(15000)
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector("#wbNavGroups")
    page.get_by_text("自动测试", exact=True).click()
    fl = page.frame_locator('iframe[data-page-id="autotest"]')
    fl.locator("#pill-text").wait_for(state="visible", timeout=20000)

    fl.locator('#tag-bar .chip[data-tag="smoke"]').click()
    btn = fl.locator("#btn-run-group")
    btn.wait_for(state="visible")
    label = btn.inner_text()
    assert "3" in label, f"组计数异常: {label}"
    btn.click()
    fl.locator("#job-panel").wait_for(state="visible")
    deadline = time.time() + 240
    summary = ""
    while time.time() < deadline:
        s = fl.locator("#job-summary").inner_text().strip()
        if s and "运行中" not in s:
            summary = s
            break
        time.sleep(2)
    assert summary.startswith("汇总 3/3"), f"组跑结果异常: {summary}"
    chips = fl.locator("#job-chips").inner_text().replace("\n", " | ")
    page.screenshot(path=str(EV / "10-group-run-pass.png"))
    print(f"[ok] 组跑 smoke {summary}; chips={chips}")
    browser.close()

print("GROUP RUN PASS — 截图 10-group-run-pass.png")
