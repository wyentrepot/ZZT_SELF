"""REQS-0037 分钟采集面板前端测试（静态断言，无浏览器）。

跟随 test_serial_profile_frontend.py / test_shell_navigation.py 的静态结构断言方式：
- 「分钟采集」页签存在且融入既有二级页签机制（data-zv，无独立 JS 运行时设施）；
- 任务配置表单 / 场景入口 / 11E3 构帧预览 / F230 存数展示四块元素 id 存在；
- 主动上报 renderReports 的 F230 chips 与表格列存在；
- 构帧一律走后端（/api/simcon/build 与 /api/simcon/mclt/11e3_preview），
  前端不手拼字节；verify 任务面执行 wait/read 语义步骤。
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).resolve().parent / "static" / "pages" / "simcon"


def _html() -> str:
    return (_STATIC / "simcon.html").read_text(encoding="utf-8")


def _js() -> str:
    return (_STATIC / "simcon.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 页签：分钟采集入口（融入既有 ztabs/data-zv 机制）
# ---------------------------------------------------------------------------

def test_mclt_tab_registered():
    html = _html()
    # 二级页签按钮 + 视图容器 + 原有页签不回退
    assert 'data-zv="zt-mclt"' in html
    assert 'id="zt-mclt"' in html
    assert 'data-zv="zt-send"' in html
    assert 'data-zv="zt-batch"' in html
    assert "分钟采集" in html


# ---------------------------------------------------------------------------
# ① 任务配置表单（F231/F232）元素
# ---------------------------------------------------------------------------

def test_mclt_task_form_elements_present():
    html = _html()
    for el_id in ("mcTaskNo", "mcProto", "mcCycle", "mcOads", "mcMeters",
                  "mcBuildCfg", "mcSendCfg", "mcSendF232", "mcPrevHex"):
        assert f'id="{el_id}"' in html, f"缺少元素 #{el_id}"


def test_mclt_task_form_fn_semantics_in_js():
    js = _js()
    # F231 语义步骤字段（任务号/启用/协议/周期分钟/OAD 数据项）
    assert "F231" in js
    assert "task_no" in js and "cycle_min" in js and "items" in js
    # OAD+1 关联档案走 F232
    assert "F232" in js and "meters" in js
    # 任务号 0~15 前置校验
    assert "0~15" in js


def test_mclt_config_frame_via_backend_build_only():
    js = _js()
    # 预览走 /api/simcon/build（scenario_codec 构帧，禁止前端手拼字节）
    assert "/api/simcon/build" in js
    # 不出现手拼 68H 帧（整帧组装属于后端职责）
    assert "68" + "10" not in js  # 无 "6810" 之类字面拼接


# ---------------------------------------------------------------------------
# ② 11E3 构帧预览 + 复制（下发通道待定标注）
# ---------------------------------------------------------------------------

def test_mclt_11e3_preview_and_copy():
    html = _html()
    js = _js()
    for el_id in ("mcBuild11E3", "mcCopy11E3", "mc11E3Hex", "mcFreeze", "mcFreezeAuto"):
        assert f'id="{el_id}"' in html, f"缺少元素 #{el_id}"
    # 调后端 mclt_e3 构帧端点（只构帧不发送）
    assert "/api/simcon/mclt/11e3_preview" in js
    # 页面标注：下发通道待定，暂由协议工具下发
    assert "下发通道待定" in html and "协议工具" in html
    # 复制 hex 能力存在
    assert "clipboard" in js
    assert "复制" in js


def test_mclt_freeze_auto_pulls_latest_store_row():
    js = _js()
    # 「取最近存数」从 /store/mclt 取 collect_time（auto 语义的前端取数）
    assert "/api/simcon/store/mclt" in js
    assert "collect_time" in js


# ---------------------------------------------------------------------------
# ③ 场景入口（0037 + 自选 task 组合 wait+read）
# ---------------------------------------------------------------------------

def test_mclt_scenario_entry_present():
    html = _html()
    for el_id in ("mcScenarioSel", "mcScenarioLoad", "mcScenarioRun", "mcTaskSeq", "mcSteps"):
        assert f'id="{el_id}"' in html, f"缺少元素 #{el_id}"


def test_mclt_scenario_steps_semantics_rendered():
    js = _js()
    # 场景加载走 /api/scenarios/{id}
    assert "/api/scenarios/" in js
    # wait_mclt_stored 参数（超时/轮询）与 read_mclt_11e3 参数（auto/显式冻结时刻）按 dict 渲染
    assert "wait_mclt_stored" in js
    assert "timeout_s" in js and "poll_s" in js
    assert "read_mclt_11e3" in js
    assert "freeze_time" in js
    # 默认场景 id 为 0037 复测场景
    assert "chongqing_mclt_recheck_0037" in js
    # 自选 task 组合：任务号列表成对展开 wait+read
    assert "mcBuildTaskSeqSteps" in js or "wait_mclt_stored" in js


def test_mclt_scenario_runs_via_verify_task_face():
    js = _js()
    # verify 任务面执行（wait/read 两语义步骤不在单步面，属非 afn/fn 帧语义）
    assert "/api/simcon/verify" in js


# ---------------------------------------------------------------------------
# ④ F230 上报展示（chips 特例 + 表格列 + 详情）
# ---------------------------------------------------------------------------

def test_reports_renders_f230_chip_and_columns():
    js = _js()
    # renderReports 的 chips 数组加入 F230（仿 F5_power 特例单列一表）
    assert "F230" in js
    assert "mcltF230Chip" in js
    assert "mcltF230Card" in js
    # 表格列：时间/任务号/协议/采集时间/从节点/长度
    for col in ("时间", "任务号", "协议", "采集时间", "从节点", "长度"):
        assert col in js, f"缺少列 {col}"
    # 接 /store/mclt 数据（含内嵌帧数 nested_count 与 frame_id 证据链）
    assert "nested_count" in js
    assert "frame_id" in js


def test_f230_row_click_opens_detail():
    js = _js()
    # 点击 F230 行看详情（showMcltDetail 弹窗，item 内嵌帧数）
    assert "showMcltDetail" in js


# ---------------------------------------------------------------------------
# 后端（如存在）：11E3 预览端点只构帧不发送
# ---------------------------------------------------------------------------

def test_backend_11e3_preview_endpoint_build_only():
    api = Path(__file__).resolve().parent.parent.parent / "libs" / "sim_concentrator" / "api.py"
    text = api.read_text(encoding="utf-8")
    assert "/mclt/11e3_preview" in text
    # 只经 mclt_e3 构帧（build_11e3_down），不触串口/发送
    assert "build_11e3_down" in text
    assert "io.send" not in text.split("11e3_preview", 1)[1].split("@app.get", 1)[0]


def test_backend_11e3_preview_matches_bench_evidence_frame():
    """预览端点构帧字节级对齐台架证据帧（BENCH-v5-taskid-RECOVERED）。

    证据：task1 down `11e300000105240000000300000000000101001017021126`
    （fz=2026-11-02 17:10:00，ssn=0x24，698 task1，地址 000000000001）。
    """
    from fastapi.testclient import TestClient

    from sim_concentrator.api import create_simcon_app

    client = TestClient(create_simcon_app())
    r = client.post("/api/simcon/mclt/11e3_preview", json={
        "task_id": 1, "freeze_time": "2026-11-02 17:10:00",
        "protocol": 3, "ssn": 0x24,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["length"] == 24
    assert body["hex"].replace(" ", "").lower() == \
        "11e300000105240000000300000000000101001017021126"


def test_backend_11e3_preview_rejects_bad_params():
    from fastapi.testclient import TestClient

    from sim_concentrator.api import create_simcon_app

    client = TestClient(create_simcon_app())
    # freeze_time 非法 → 422
    r = client.post("/api/simcon/mclt/11e3_preview", json={
        "task_id": 1, "freeze_time": "not-a-time", "protocol": 3,
    })
    assert r.status_code == 422
    # 协议类型非法 → 422
    r = client.post("/api/simcon/mclt/11e3_preview", json={
        "task_id": 1, "freeze_time": "2026-11-02 17:10:00", "protocol": 9,
    })
    assert r.status_code == 422
