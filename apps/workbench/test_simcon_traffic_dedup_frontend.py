"""模拟集中器「收发记录」去重前端断言（2026-11-19 现场：收发一帧数据，显示了 2 遍）。

现场事实：会话帧日志（sc-*.jsonl）与 /api/simcon/frames 都只有 1 帧 TX + 1 帧 RX，
屏幕上却出现 4 行（同一批帧渲染两遍）——问题在前端，不在后端。

根因：refreshFrames() 有 4 个触发点（800ms 轮询、下发后 350ms 补刷、visibilitychange、
打开串口），而帧游标 lastSeq 只在响应返回后才推进。两条请求并发在途时带同一个
after_seq，同一批帧被插入 DOM 两次。

修复口径：
- 单飞：framesBusy 在途时只记 framesQueued，响应结束再补刷一次；
- 兜底：seenSeq 按 seq 去重，同一 seq 不重复插行；
- 换会话 / 切方向筛选时 resetFrameView() 清屏 + 清游标 + 清去重表。

静态断言（无浏览器，同 test_simcon_mclt_frontend.py 的方式）。
"""
from __future__ import annotations

from pathlib import Path

_STATIC = Path(__file__).resolve().parent / "static" / "pages" / "simcon"


def _js() -> str:
    return (_STATIC / "simcon.js").read_text(encoding="utf-8")


def _refresh_body() -> str:
    """截 refreshFrames 函数体（到下一个顶层函数注释段）。"""
    js = _js()
    start = js.index("function refreshFrames(")
    end = js.index("function normAfnCode(")
    return js[start:end]


# ---------------------------------------------------------------------------
# ① 单飞：在途请求不并发
# ---------------------------------------------------------------------------

def test_refresh_frames_is_single_flight():
    body = _refresh_body()
    assert "if (state.framesBusy) { state.framesQueued = true; return; }" in body, \
        "refreshFrames 必须先挡在途请求，否则并发拿到同一 after_seq → 同一批帧插两遍"
    assert "state.framesBusy = true;" in body
    # 释放与补刷必须在同一个收尾链上（成功/失败都要释放）
    assert "state.framesBusy = false;" in body
    assert "if (state.framesQueued) { state.framesQueued = false; refreshFrames(); }" in body


# ---------------------------------------------------------------------------
# ② 兜底去重：同一 seq 不重复插行
# ---------------------------------------------------------------------------

def test_frames_deduped_by_seq():
    body = _refresh_body()
    assert "state.seenSeq[f.seq]" in body, "插入前必须按 seq 查重"
    assert "state.seenSeq[f.seq] = true;" in body
    # 去重在 insertBefore 之前生效
    assert body.index("state.seenSeq[f.seq]") < body.index("body.insertBefore(row")


# ---------------------------------------------------------------------------
# ③ 视图切换必须清去重表与游标
# ---------------------------------------------------------------------------

def test_reset_frame_view_clears_dedup_and_cursor():
    js = _js()
    start = js.index("function resetFrameView(")
    end = js.index("function refreshFrames(")
    body = js[start:end]
    assert "state.seenSeq = Object.create(null)" in body
    assert "state.lastSeq = 0" in body
    assert '$("#trBody").innerHTML = ""' in body
    # 会话切换与方向筛选都走 resetFrameView（不能只重置 lastSeq）
    assert js.count("resetFrameView()") >= 2
    seg = js[js.index('$("#segF").addEventListener'):][:600]
    assert "resetFrameView()" in seg, "切换方向筛选必须清去重表，否则旧 seq 会挡住新视图的行"


# ---------------------------------------------------------------------------
# ④ 反例守卫：不允许退回「只重置 lastSeq 而不清去重表」的写法
# ---------------------------------------------------------------------------

def test_no_bare_cursor_reset_in_filter_switch():
    js = _js()
    seg = js[js.index('$("#segF").addEventListener'):][:600]
    assert "state.lastSeq = 0;\n    $(\"#trBody\").innerHTML = '<div class=\"empty\"" not in seg
