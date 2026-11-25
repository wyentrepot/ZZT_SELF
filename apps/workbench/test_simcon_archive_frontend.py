"""模拟集中器「路由档案」行编辑表前端测试（静态断言，无浏览器）。

背景（2026-11-19 用户现场反馈）：11H-F1「添加从节点」的数据单元是
`数量n + (地址 BCD 6B + 协议类型 1B) × n`，而协议字典里只有「从节点的数量 n」
带 key，地址/协议两个字段 key=null —— 通用字段表单**根本表达不出地址与协议**，
只能手拼；实际用法又是一行一个档案、批量下发。故本页改为：
- 一行一个从节点（地址 + 协议类型下拉），可增删行、可校验、可拆帧下发；
- 读取（10H-F2）以表格回显（地址/协议类型/信号品质/中继级别/相位/在网），可勾选导入或删除。

沿用 test_simcon_mclt_frontend.py / test_shell_navigation.py 的静态结构断言方式。
"""

from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).resolve().parent / "static" / "pages" / "simcon"


def _html() -> str:
    return (_STATIC / "simcon.html").read_text(encoding="utf-8")


def _js() -> str:
    return (_STATIC / "simcon.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 行编辑表：11H-F1/F2 专用面板（不再是通用字段表单）
# ---------------------------------------------------------------------------

def test_archive_fn_detected_by_afn_fn_not_generic_form():
    """11H-F1/F2 必须走档案专用面板，而不是通用字段表单（后者的地址/协议 key=null）。"""
    js = _js()
    assert "isArchiveFn" in js
    assert "renderArchivePanel" in js
    # 判据是 AFN=0x11 且 Fn 为 F1/F2，不许按中文名字符串硬匹配（名字会变）
    assert 'sel.code === "11"' in js
    assert 'sel.no === "F1"' in js and 'sel.no === "F2"' in js
    # 通用表单路径里必须先分流
    split = js.split("function selectFn", 1)[1].split("function setupRespGrid", 1)[0]
    assert "isArchiveFn()" in split, "selectFn 未分流到档案面板"


def test_archive_row_editor_has_addr_and_protocol_per_row():
    """一行一个从节点：每行地址输入 + 协议类型下拉（4 种协议取值齐全）。"""
    js = _js()
    assert "arc-addr" in js and "arc-proto" in js
    assert "arcRows" in js
    # 协议取值与 1376.2 定义一致：00H 透明 / 01H 645-1997 / 02H 645-2007 / 03H 698.45
    for token in ("透明传输", "DL/T 645-1997", "DL/T 645-2007", "DL/T 698.45"):
        assert token in js, f"缺少协议类型选项 {token}"


def test_archive_row_editor_buttons_present():
    """新增行 / 清空 / 删除行 三个操作都在。"""
    js = _js()
    for el_id in ("arcAddRow", "arcClearRows", "arcMeta", "arcRead", "arcImport"):
        assert f'id="{el_id}"' in js or f'"#{el_id}"' in js, f"缺少元素 #{el_id}"
    assert "arc-del" in js, "缺少删除行按钮"


def test_archive_params_shaped_for_backend():
    """参数形状：添加 → {nodes:[{addr,protocol}]}；删除 → {meters:[addr]}。

    后端 adapter_10376 (0x11,1) 消费 nodes[].addr/protocol，(0x11,2) 消费 meters[]。
    """
    js = _js()
    assert "arcInlineParams" in js
    assert "{ nodes: rows.map" in js
    assert "protocol: r.proto" in js
    assert "{ meters: rows.map" in js
    # collectFormParams 必须优先返回档案表参数，否则回到通用表单的空参数
    collect = js.split("function collectFormParams", 1)[1].split("function buildPreview", 1)[0]
    assert "isArchiveFn()" in collect and "arcInlineParams()" in collect


def test_archive_addr_validation_12_digit():
    """地址必须是 12 位数字（BCD），非法/重复行在下发前拦住。"""
    js = _js()
    assert r"/^\d{12}$/" in js
    assert "12 位数字" in js
    assert "重复" in js


def test_archive_batch_split_per_frame():
    """超过单帧上限（16）要拆帧下发，不能只发第一帧、其余静默丢弃。"""
    js = _js()
    assert "ARC_MAX_PER_FRAME" in js
    assert "arcSendChunks" in js
    # 单帧上限口径与后端 recipes.MAX_BATCH 一致
    assert "= 16" in js
    send = js.split('$("#btnSend").addEventListener', 1)[1].split("/* ================= 帧着色", 1)[0]
    assert "isArchiveFn()" in send and "arcSendChunks" in send


# ---------------------------------------------------------------------------
# 读取：10H-F2 以表格回显（不是纯文本/日志）
# ---------------------------------------------------------------------------

def test_archive_read_renders_table_with_protocol_column():
    js = _js()
    assert "/api/simcon/archive/query" in js
    assert "renderArcReadTable" in js
    # 表头列：地址 / 协议类型 / 信号品质 / 中继级别 / 相位 / 在网
    for col in ("从节点地址", "协议类型", "信号品质", "中继级别", "相位", "在网"):
        assert col in js, f"读取表缺少列 {col}"
    # 协议码 → 人话映射（后端返回数字码 0~3）
    assert "ARC_PROTO_NAME" in js
    assert "698.45" in js


def test_archive_read_supports_import_and_delete():
    """读到的档案能勾选导入编辑表，也能勾选直接删除（11H-F2）。"""
    js = _js()
    assert "arcChk" in js and "arcAll" in js
    assert "arcImportSelected" in js
    assert "arcDeleteSelected" in js
    # 删除走 11H-F2 单帧多地址（不是逐条发）
    delete = js.split("function arcDeleteSelected", 1)[1].split("/* 多帧下发", 1)[0]
    assert "fn: 2" in delete and "meters:" in delete


# ---------------------------------------------------------------------------
# 红线：前端不手拼字节，构帧/下发一律走后端
# ---------------------------------------------------------------------------

def test_archive_frames_built_by_backend_only():
    js = _js()
    assert "/api/simcon/build" in js and "/api/simcon/step" in js
    # 不出现手拼整帧的字面（68H 头 + AFN 常量拼装）
    assert "6810" not in js
    assert "0x68" not in js


# ---------------------------------------------------------------------------
# 10H-F2 起始序号基址（2026-11-19 现场：start=0 回空壳、start=1 回明细）
# ---------------------------------------------------------------------------

def test_read_passes_start_base_to_backend():
    """读取档案必须把全局勾选的 start_base 传给后端，且两处勾选同源。"""
    js = _js()
    assert "start_base=" in js, "读取请求必须带 start_base（否则后端默认值与勾选脱节）"
    assert "state.queryStartBase" in js
    # 两处勾选：会话设置全局 + 档案面板内联
    assert 'id="setStartBase"' in _html()
    assert 'id="arcStartBase"' in js
    # 同一落盘端点（不是两套本地状态）
    assert "/api/simcon/settings" in js
    assert js.count("saveQueryStartBase") >= 3


def test_empty_shell_warns_instead_of_silent():
    """总数量>0 但明细 0 条时必须提示，别让人以为模块没档案。"""
    js = _js()
    seg = js.split("function arcReadArchive", 1)[1].split("function applyQueryStartBase", 1)[0]
    assert "本次应答" in seg or "banner(" in seg
    assert "起始序号从 1 开始" in seg or "01 00" in seg


def test_settings_loaded_on_boot():
    """启动即读全局设置，两处勾选同步。"""
    js = _js()
    assert "loadSettings()" in js
    init = js.split("/* ================= 初始化", 1)[1]
    assert "loadSettings()" in init
