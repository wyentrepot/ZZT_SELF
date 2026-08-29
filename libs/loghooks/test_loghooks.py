"""loghooks 测试：规则加载/schema/引擎/状态机/来源解析/关联/输出。

运行：python -m pytest loghooks/test_loghooks.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 确保仓库根在 path（conftest 也会处理，这里兜底）
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from loghooks.correlate import correlate, extract_anchors
from loghooks.engine import Engine
from loghooks.matchers import apply_capture, format_message, match_line
from loghooks.rules import MatchDef, Rule, RuleLoader, SchemaError, validate_rule
from loghooks.sequence import SequenceTracker
from loghooks.sources import parse_listener_frame, parse_module_log
from loghooks.output import build_summary, format_json, format_table


# ---------------------------------------------------------------------------
# 规则加载与 schema
# ---------------------------------------------------------------------------


def test_loader_loads_builtin_rules():
    loader = RuleLoader().load_all()
    assert not loader.errors, loader.errors
    assert len(loader.rules) >= 9
    ids = {r.id for r in loader.rules}
    assert "common.join_onnet" in ids
    assert "anhui.collect.e4_frame" in ids


def test_loader_province_filter():
    loader = RuleLoader().load_all()
    # 全部
    all_rules = loader.filter_by_province(None)
    assert all(r.scope == "common" for r in all_rules)
    # anhui: common + anhui
    anhui = loader.filter_by_province("anhui")
    assert len(anhui) > len(all_rules)
    assert any(r.province == "anhui" for r in anhui)


def test_province_list():
    loader = RuleLoader().load_all()
    provs = {p["province"] for p in loader.get_province_list()}
    assert "anhui" in provs


def test_module_filter_cco_sta_separated():
    """cco 与 sta 规则必须隔离，不能通用。"""
    loader = RuleLoader().load_all()
    cco_rules = loader.filter_by_module("cco")
    sta_rules = loader.filter_by_module("sta")

    cco_ids = {r.id for r in cco_rules}
    sta_ids = {r.id for r in sta_rules}
    # cco 专属规则不应出现在 sta
    assert "common.join_onnet" in cco_ids
    assert "common.join_onnet" not in sta_ids
    # sta 专属规则不应出现在 cco
    assert "sta.beacon_recv" in sta_ids
    assert "sta.beacon_recv" not in cco_ids


def test_loader_module_hint_from_filename():
    """cco.json/sta.json 加载时应自动注入对应 module。"""
    loader = RuleLoader().load_all()
    by_id = {r.id: r for r in loader.rules}
    # cco.json 里的规则 module=cco
    assert by_id["common.join_onnet"].module == "cco"
    # sta.json 里的规则 module=sta
    assert by_id["sta.beacon_recv"].module == "sta"
    # common.json 里的通用规则 module=common
    assert by_id["common.beacon_crc_err"].module == "common"


def test_schema_rejects_missing_id():
    with pytest.raises(SchemaError):
        validate_rule({"category": "join", "event": {}})


def test_schema_rejects_bad_scope():
    with pytest.raises(SchemaError):
        validate_rule({
            "id": "x", "category": "join", "scope": "bad",
            "event": {}, "match": {"mode": "text", "pattern": "a"},
        })


def test_schema_rejects_duplicate_id():
    r = {
        "id": "dup", "category": "join", "scope": "common",
        "source": ["module_log"],
        "match": {"mode": "text", "pattern": "a"},
        "event": {"type": "x"},
    }
    from loghooks.rules import validate_ruleset
    with pytest.raises(SchemaError):
        validate_ruleset([r, dict(r)])


# ---------------------------------------------------------------------------
# 来源解析
# ---------------------------------------------------------------------------


def test_parse_module_log_with_file_line():
    line = "[20260811-19:15:08:510] [RX] 0 | info | aps_ioctrl_nwk.c (950) | onnet cnt = 12"
    p = parse_module_log(line)
    assert p is not None
    assert p.text == "onnet cnt = 12"
    assert p.file == "aps_ioctrl_nwk.c"
    assert p.line == 950
    assert p.direction == "RX"


def test_parse_module_log_fallback():
    p = parse_module_log("[20260811-19:15:08:511] [EVENT] some raw line")
    assert p is not None
    assert p.text == "some raw line"
    assert p.file is None


def test_parse_listener_frame():
    p = parse_listener_frame("[1][19:15:09.012]7E 01 02 7E")
    assert p is not None
    assert p.time == "19:15:09.012"
    assert "7E" in p.text


def test_parse_listener_frame_with_parser_callback():
    calls = []

    def fake_callback(frame_hex):
        calls.append(frame_hex)
        return {"simple": {"SNID": "123", "FrmType": "广播信标"}}

    p = parse_listener_frame(
        "[1][19:15:09.012]7E 01 02 7E", parser_callback=fake_callback
    )
    assert p is not None
    assert p.fields == {"SNID": "123", "FrmType": "广播信标"}
    assert calls == ["7E 01 02 7E"]


def test_parse_listener_frame_callback_error_keeps_fields_none():
    def bad_callback(frame_hex):
        raise RuntimeError("解析失败")

    p = parse_listener_frame(
        "[1][19:15:09.012]7E 01 02 7E", parser_callback=bad_callback
    )
    assert p is not None
    assert p.fields is None


# ---------------------------------------------------------------------------
# CLI listener 解析参数装配（曾因类名误写 DotnetParser 静默降级）
# ---------------------------------------------------------------------------


def test_listener_parser_kwargs_builds_parser_service(monkeypatch):
    import shared.dotnet_parser as dp

    from loghooks.cli import _listener_parser_kwargs

    created = {}

    class FakeHplcParser:
        def __init__(self, dll_path):
            created["dll_path"] = dll_path

        def parse_simple(self, frame):
            return "{}"

        def parse_full(self, frame):
            return "{}"

        def version(self):
            return {"name": "fake", "version": "0", "date": ""}

    monkeypatch.setattr(dp, "DotNetHplcParser", FakeHplcParser)
    kwargs = _listener_parser_kwargs()
    assert set(kwargs) == {"parser_callback"}
    assert created["dll_path"].name == "GwHPLCAnalysis.dll"
    # 回调应可实际解析一帧（7E 封装 → {"frame":..., "simple":...}）
    out = kwargs["parser_callback"]("7E 01 02 7E")
    assert "simple" in out and "frame" in out


def test_listener_parser_kwargs_degrades_with_warning(monkeypatch, capsys):
    import shared.dotnet_parser as dp

    from loghooks.cli import _listener_parser_kwargs

    def boom(dll_path):
        raise FileNotFoundError("no dll")

    monkeypatch.setattr(dp, "DotNetHplcParser", boom)
    kwargs = _listener_parser_kwargs()
    assert kwargs == {"parser_callback": None}
    assert "侦听台帧解析不可用" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 匹配器
# ---------------------------------------------------------------------------


def test_match_line_text_and_capture():
    line = parse_module_log("[20260811-19:15:08:510] [RX] 0 | info | aps_ioctrl_nwk.c (950) | onnet cnt = 12")
    md = MatchDef(mode="text", pattern=r"onnet cnt = (\d+)", line=950, line_tolerance=10)
    result = match_line(line, md)
    assert result.matched
    assert result._raw_groups == ("12",)
    cap = apply_capture(line, md, result, {"node_count": 1})
    assert cap["node_count"] == "12"
    assert format_message("入网节点数 = {node_count}", cap) == "入网节点数 = 12"


def test_match_line_drift_still_hits():
    line = parse_module_log("[20260811-19:15:08:510] [RX] 0 | info | aps_ioctrl_nwk.c (950) | onnet cnt = 12")
    # 期望行号 900，容差 5，实际 950 -> 漂移但命中
    md = MatchDef(mode="text", pattern=r"onnet cnt = (\d+)", line=900, line_tolerance=5)
    result = match_line(line, md)
    assert result.matched
    assert result.line_drift


# ---------------------------------------------------------------------------
# 跨行状态机
# ---------------------------------------------------------------------------


def _sta_rule():
    return Rule.from_dict({
        "id": "common.join_sta_flow",
        "category": "join", "scope": "common", "source": ["module_log"],
        "event": {"type": "join.sta"},
        "sequence": [
            {"step": "disc_done", "pattern": "nwk disc done"},
            {"step": "assoc_start", "pattern": "start nwk assoc"},
            {"step": "track_succ", "pattern": "nwk track done.*succ"},
            {"step": "bcn_recv", "pattern": r"recv bcn.*NID=([0-9a-fA-F]+)"},
        ],
        "capture": {"nid": 4},
        "window_ms": 30000,
        "on_complete": {"type": "join.sta.ok", "message": "STA 入网成功 NID={nid}"},
        "on_timeout": {"type": "join.sta.timeout", "level": "warn"},
    })


def test_sequence_complete():
    tracker = SequenceTracker(_sta_rule())
    events = []
    for text, ts in [
        ("nwk disc done", "t1"),
        ("start nwk assoc", "t2"),
        ("nwk track done ind succ", "t3"),
        ("recv bcn, from 0x01 NID=61475d", "t4"),
    ]:
        events.extend(tracker.feed(parse_module_log(f"[20260811-19:15:00:000] [RX] {text}")))
    assert len(events) == 1
    assert events[0].event_type == "join.sta.ok"
    assert "61475d" in events[0].message


def test_sequence_timeout():
    rule = Rule.from_dict({
        "id": "t", "category": "join", "scope": "common", "source": ["module_log"],
        "event": {"type": "x"},
        "sequence": [{"step": "a", "pattern": "stepA"}, {"step": "b", "pattern": "stepB"}],
        "window_ms": 5000,
        "on_complete": {"type": "ok"},
        "on_timeout": {"type": "timeout", "level": "warn"},
    })
    tracker = SequenceTracker(rule)
    events = tracker.feed(parse_module_log("[20260811-19:15:00:000] [RX] stepA"))
    assert events == []
    # 时间戳递增超过 window_ms 触发超时（秒递增 1..10，超过 5s 窗口）
    timeout_events = []
    for i in range(10):
        sec = i + 1
        timeout_events.extend(
            tracker.feed(parse_module_log(f"[20260811-19:15:{sec:02d}:000] [RX] noise {i}"))
        )
    assert any(e.event_type == "timeout" for e in timeout_events)


def test_sequence_flush_on_truncation():
    """日志截断（流程未走完）时，finalize 应产出 timeout 而非静默丢弃。"""
    rule = Rule.from_dict({
        "id": "trunc", "category": "join", "scope": "common", "source": ["module_log"],
        "event": {"type": "x"},
        "sequence": [{"step": "a", "pattern": "stepA"}, {"step": "b", "pattern": "stepB"}],
        "window_ms": 30000,
        "on_complete": {"type": "ok"},
        "on_timeout": {"type": "timeout", "level": "warn"},
    })
    from loghooks.engine import Engine
    engine = Engine([rule], source="module_log")
    engine.feed(parse_module_log("[20260811-19:15:00:000] [RX] stepA"))  # 只走第一步，截断
    result = engine.finalize()
    assert any(e.type == "timeout" for e in result.events)


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------


def test_engine_scan_rules():
    rule = Rule.from_dict({
        "id": "common.join_onnet",
        "category": "join", "scope": "common", "source": ["module_log"],
        "match": {"mode": "text", "pattern": r"onnet cnt = (\d+)", "line": 950, "line_tolerance": 10},
        "capture": {"node_count": 1},
        "event": {"type": "network.onnet", "label": "入网节点数", "message": "入网节点数 = {node_count}"},
    })
    lines = [
        "[20260811-19:15:08:510] [RX] 0 | info | aps_ioctrl_nwk.c (950) | onnet cnt = 12",
        "[20260811-19:15:08:511] [RX] 0 | info | bps_check.c (123) | bpsCheck_state0 trycnt 0",
        "[20260811-19:15:08:512] [RX] 0 | info | aps_ioctrl_nwk.c (950) | onnet cnt = 5",
    ]
    engine = Engine([rule], source="module_log")
    for l in lines:
        p = parse_module_log(l)
        if p:
            engine.feed(p)
    result = engine.finalize()
    assert len(result.events) == 2
    assert result.events[0].message == "入网节点数 = 12"
    assert result.hit_rule_ids == ["common.join_onnet"]
    assert result.unmatched == 1


# ---------------------------------------------------------------------------
# 跨来源关联
# ---------------------------------------------------------------------------


def test_correlate_by_nid():
    from loghooks.engine import Event, ScanResult
    mev = Event(type="join.sta.ok", label="", message="STA 入网成功 NID=61475d",
                level="info", time="20260811-19:15:08:510", rule_id="r1",
                category="join", source="module_log", source_line="")
    lev = Event(type="beacon.recv", label="", message="recv bcn", level="info",
                time="19:15:09.012", rule_id="r2", category="beacon",
                source="listener", source_line="", captures={"snid": "61475d"})
    corrs = correlate(
        ScanResult("module_log", [], events=[mev]),
        ScanResult("listener", [], events=[lev]),
        time_window_s=5,
    )
    assert len(corrs) == 1
    assert corrs[0].matched
    assert corrs[0].time_gap_s <= 5


def test_extract_anchors():
    assert extract_anchors("STA 入网成功 NID=61475d") == ["nid:61475d"]
    assert extract_anchors("freeze_time: 2026-07-31 23:55:00")
    assert extract_anchors("源MAC 340100141223")


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------


def test_build_summary_and_format():
    rule = Rule.from_dict({
        "id": "common.join_onnet",
        "category": "join", "scope": "common", "source": ["module_log"],
        "match": {"mode": "text", "pattern": r"onnet cnt = (\d+)"},
        "capture": {"node_count": 1},
        "event": {"type": "network.onnet", "label": "入网", "message": "入网 {node_count}"},
    })
    engine = Engine([rule], source="module_log")
    p = parse_module_log("[20260811-19:15:08:510] [RX] 0 | info | aps_ioctrl_nwk.c (950) | onnet cnt = 3")
    engine.feed(p)
    result = engine.finalize()

    summary = build_summary(result)
    assert "join" in summary
    assert summary["join"]["count"] == 1

    js = format_json(result)
    assert "events" in js
    assert "join" in js

    tb = format_table(result)
    assert "join" in tb or "事件" in tb


def test_engine_no_rule_no_crash():
    engine = Engine([], source="module_log")
    p = parse_module_log("[20260811-19:15:08:510] [RX] 0 | info | aps_ioctrl_nwk.c (950) | onnet cnt = 3")
    engine.feed(p)
    result = engine.finalize()
    assert len(result.events) == 0
    assert result.total_lines == 1