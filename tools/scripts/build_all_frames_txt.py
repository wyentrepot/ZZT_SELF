"""全量帧类型构建清单生成器（验收用）。

调用模拟集中器构帧层（libs/sim_concentrator/frame_codec → adapter_10376 全量模板），
把 Q/GDW 10376.2 标准 73 个 Fn（含安徽分钟级采集扩展 F230/F231/F232）的
全部帧类型按 TX（集中器下行发出）与 RX（集中器接收上行）两个方向各构建一遍，
逐帧做"构建→解码"回环自检（CS 校验、AFN/FN 一致），结果写入 txt 供验收。

用法:
    python tools/scripts/build_all_frames_txt.py [输出文件路径]
缺省输出: 测试文件/构帧全量清单_TX_RX_YYYYMMDD.txt

用例矩阵复用 libs/parser_lib/adapters/adapter_10376/tests/test_10376_full_coverage.py
的 CASES（与全量回归测试同源，保证验收口径与测试一致），安徽扩展单列追加。
"""
from __future__ import annotations

import datetime
import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _sub in ("", "apps", "libs"):
    _p = os.path.join(_ROOT, _sub) if _sub else _ROOT
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sim_concentrator.frame_codec import (  # noqa: E402
    build_13762_frame,
    decode_frame,
    frame_to_hex,
)
from parser_lib.adapters.adapter_10376 import _AFN_NAMES, encode_app_data  # noqa: E402

# 复用全量覆盖测试的用例矩阵（含标准 73 Fn 双向用例与样例参数）
_TFP = os.path.join(_ROOT, "libs", "parser_lib", "adapters", "adapter_10376",
                    "tests", "test_10376_full_coverage.py")
_spec = importlib.util.spec_from_file_location("_full_cov", _TFP)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
CASES = _mod.CASES
STANDARD_FN_TABLE = _mod.STANDARD_FN_TABLE

# 安徽分钟级采集扩展（构帧模板已覆盖的下行 Fn；样例参数按 05.3_安徽.md）
ANHUI_CASES = [
    (0x10, 230, "down", {}, "安徽扩展：查询采集任务数量"),
    (0x10, 231, "down", {"task_no": 1, "protocol": 2}, "安徽扩展：查询采集任务配置"),
    (0x11, 231, "down",
     {"task_no": 1, "action": "enable", "protocol": 2, "cycle_min": 1,
      "items": [{"meter_type": 0, "item": "02010100", "reply_len": 4},
                {"meter_type": 1, "item": "02020200", "reply_len": 8}]},
     "安徽扩展：设置采集任务配置"),
    (0x11, 232, "down",
     {"task_no": 1, "meters": ["013300000001", "013300000002"]},
     "安徽扩展：设置采集任务关联档案"),
]

# Fn 标题（对照解析侧 _app_items 字段语义；解码回环中的数据项名为权威细节）
_FN_TITLES = {
    0x00: {1: "确认/否认", 2: "否认错误状态字"},
    0x01: {1: "硬件初始化(复位)", 2: "参数区初始化(清除从节点档案)",
           3: "数据区初始化(清除从节点通信信息)"},
    0x02: {1: "数据转发"},
    0x03: {1: "版本信息", 2: "信噪比", 3: "从节点侦听信息", 4: "主节点地址",
           5: "通信模式/速率", 6: "设置主节点干扰持续时间", 7: "通信延时",
           8: "信道/发射功率", 9: "通信延时广播查询(带报文)", 10: "运行模式",
           11: "AFN支持数据单元索引", 12: "模块ID号", 16: "宽带载波频段",
           100: "场强门限"},
    0x04: {1: "发送测试", 2: "从节点点名", 3: "报文通信测试"},
    0x05: {1: "设置主节点地址", 2: "事件上报状态", 3: "广播报文",
           4: "最大应答超时时间", 5: "无线信道/发射功率设置", 6: "台区识别使能",
           16: "宽带载波频段设置", 100: "场强门限设置",
           101: "中心节点时间设置(广播校时)", 200: "拒绝节点上报使能"},
    0x06: {1: "上报从节点信息", 2: "透传数据上报", 3: "路由工作任务变动上报",
           4: "上报从节点信息扩展(含下接节点)", 5: "从节点停复电/台区改切上报"},
    0x10: {1: "从节点总数量", 2: "查询从节点信息", 3: "指定从节点的上一级中继路由信息",
           4: "路由工作状态", 5: "查询中继路由信息", 6: "查询节点信息(搜索)",
           7: "查询从节点通信模块信息", 9: "查询网络规模", 21: "查询网络拓扑信息",
           31: "查询节点信息", 40: "流水线查询", 100: "网络规模",
           101: "从节点版本信息", 104: "查询路由工作模式", 111: "节点网络标识号",
           112: "芯片ID信息", 230: "安徽扩展:查询采集任务数量",
           231: "安徽扩展:查询采集任务配置"},
    0x11: {1: "添加从节点", 2: "删除从节点", 3: "设置固定中继路径",
           4: "设置路由工作模式", 5: "激活从节点主动注册", 6: "终止从节点主动注册",
           100: "设置网络规模", 101: "启动网络维护进程", 102: "启动组网",
           231: "安徽扩展:采集任务配置", 232: "安徽扩展:采集任务关联档案配置"},
    0x12: {1: "路由控制:重启", 2: "路由控制:暂停", 3: "路由控制:恢复"},
    0x13: {1: "路由数据转发(监控从节点)"},
    0x14: {1: "路由请求抄读内容", 2: "路由请求集中器时钟",
           3: "依通信延时修正通信数据", 4: "路由请求交采信息"},
    0x15: {1: "文件传输"},
    0xF1: {1: "并发抄表"},
}


def fn_title(afn: int, fn: int) -> str:
    return _FN_TITLES.get(afn, {}).get(fn, f"F{fn}")


def _fmt_params(params: dict) -> str:
    if not params:
        return "(无数据单元)"
    parts = []
    for k, v in params.items():
        if isinstance(v, list):
            inner = "; ".join(
                ",".join(f"{ik}={iv}" for ik, iv in it.items())
                if isinstance(it, dict) else str(it) for it in v)
            parts.append(f"{k}=[{inner}]")
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


def _decode_summary(d: dict) -> str:
    """解码结果 → 一行摘要（数据项 name=value，最多 6 项）。"""
    out = []
    for it in d["items"]:
        name, value = it["name"], str(it["value"])
        v = value if len(value) <= 28 else value[:25] + "..."
        out.append(f"{name}={v}")
    if not out:
        return "(解析无数据项)"
    return " | ".join(out[:6]) + (f" ...共{len(out)}项" if len(out) > 6 else "")


def build_one(index, afn, fn, direction, params, note="") -> tuple:
    """构一帧并回环解码。返回 (行列表, 是否成功)。"""
    tag = "TX" if direction == "down" else "RX"
    dir_desc = "下行(集中器→通信模块)" if direction == "down" else "上行(通信模块→集中器)"
    lines = []
    ok = False
    try:
        appdata = encode_app_data(afn, fn, params, direction=direction)
        frame = build_13762_frame(afn=afn, fn=fn, appdata=appdata, direction=direction)
        d = decode_frame(frame)
        f = d["fields"]
        afn_got, fn_got = f["AFN"]["raw"], f["FN"]["raw"]
        cs_ok = "通过" in f["校验和CS"]["desc"]
        ok = (afn_got == afn and fn_got == fn and cs_ok and not d["warnings"])
        hexs = frame_to_hex(frame)
        hex_wrapped = "\n".join("      " + hexs[i:i + 87]
                                for i in range(0, len(hexs), 87))
        lines.append(f"[{index:03d}] {tag}  AFN={afn:02X}H({_AFN_NAMES.get(afn, '?')})  "
                     f"{fn_title(afn, fn)}  ({note})" if note else
                     f"[{index:03d}] {tag}  AFN={afn:02X}H({_AFN_NAMES.get(afn, '?')})  "
                     f"{fn_title(afn, fn)}")
        lines.append(f"      方向: {tag} · {dir_desc}   控制域C={f['控制域C']['hex']}"
                     f"({f['控制域C']['value']})")
        lines.append(f"      参数: {_fmt_params(params)}")
        if appdata:
            lines.append(f"      应用数据({len(appdata)}B): "
                         + " ".join(f"{b:02X}" for b in appdata[:32])
                         + (" ..." if len(appdata) > 32 else ""))
        else:
            lines.append("      应用数据: (无)")
        lines.append(f"      帧长度={len(frame)}B  CS={f['校验和CS']['hex']}"
                     f"(校验{'✓' if cs_ok else '✗'})  L={f['长度L']['value']}")
        lines.append("      帧hex:")
        lines.append(hex_wrapped)
        mark = "✓" if ok else "✗"
        lines.append(f"      解码回环: AFN={afn_got:02X}H F{fn_got} {mark} "
                     f"[{_decode_summary(d)}]")
        if d["warnings"]:
            lines.append(f"      ⚠ 告警: {d['warnings']}")
    except Exception as e:
        lines.append(f"[{index:03d}] {tag}  AFN={afn:02X}H  F{fn}  {fn_title(afn, fn)}")
        lines.append(f"      参数: {_fmt_params(params)}")
        lines.append(f"      ✗ 构帧失败: {e!r}")
    lines.append("")
    return lines, ok


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        _ROOT, "测试文件", f"构帧全量清单_TX_RX_{datetime.date.today():%Y%m%d}.txt")

    # 方向语义：down=TX(集中器下行), up=RX(集中器接收上行)
    tx_cases = [(a, f_, p, n) for a, f_, d, p, n in ANHUI_CASES if d == "down"]
    all_tx = [(a, f_, p, "") for a, f_, d, p in CASES if d == "down"] + tx_cases
    all_rx = [(a, f_, p, "") for a, f_, d, p in CASES if d == "up"]

    now = datetime.datetime.now()
    L = []
    ap = L.append
    ap("=" * 100)
    ap("Q/GDW 10376.2—2019 全量帧类型构建清单（模拟集中器构帧层 · TX/RX 双向）—— 验收文件")
    ap("=" * 100)
    ap(f"生成时间: {now:%Y-%m-%d %H:%M:%S}")
    import subprocess
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT,
                                capture_output=True, text=True).stdout.strip()
        ap(f"代码版本: master @{commit}")
    except Exception:
        pass
    ap("构帧入口: libs/sim_concentrator/frame_codec.build_13762_frame "
       "(→ parser_lib/adapters/adapter_10376 全量构帧模板)")
    ap("帧结构: 单68标准帧  68H | L(2B) | C(1B) | 信息域R(6B) | [地址域A] | AFN | DT1 | DT2 "
       "| 应用数据 | CS | 16H")
    ap("信封缺省: 无地址域(通信模块标识=0), seq=1, 通信方式=3(HPLC载波)")
    ap(f"覆盖范围: 标准 73 个 Fn (14 个 AFN, F0H 厂家自定义除外) + 安徽分钟级采集扩展 "
       f"F230/F231/F232")
    ap(f"用例总数: TX(下行) {len(all_tx)} 帧 + RX(上行) {len(all_rx)} 帧 = "
       f"{len(all_tx) + len(all_rx)} 帧; 每帧均做 构建→解码 回环自检")
    ap("")
    ap("方向约定:")
    ap("  TX = 集中器下行帧 (direction=down, DIR=0/PRM=1, 控制域C=43H)")
    ap("  RX = 集中器接收的上行帧 (direction=up, DIR=1/PRM=0, 控制域C=C3H)")
    ap("")

    idx = 0
    n_ok = 0
    fail = []
    ap("─" * 100)
    ap(f"第一部分  TX 下行帧（集中器发出）  共 {len(all_tx)} 帧")
    ap("─" * 100)
    for afn, fn, params, note in all_tx:
        idx += 1
        lines, ok = build_one(idx, afn, fn, "down", params, note)
        L += lines
        n_ok += ok
        if not ok:
            fail.append((idx, "TX", afn, fn))

    ap("─" * 100)
    ap(f"第二部分  RX 上行帧（集中器接收）  共 {len(all_rx)} 帧")
    ap("─" * 100)
    for afn, fn, params, note in all_rx:
        idx += 1
        lines, ok = build_one(idx, afn, fn, "up", params, note)
        L += lines
        n_ok += ok
        if not ok:
            fail.append((idx, "RX", afn, fn))

    # 说明段：上行应答约定（逐 Fn 判断是否有上行构帧用例）
    confirm_only = []
    for a, fns in STANDARD_FN_TABLE.items():
        for f_ in fns:
            has_up = any(c[0] == a and c[1] == f_ and c[2] == "up" for c in CASES)
            has_down = any(c[0] == a and c[1] == f_ and c[2] == "down" for c in CASES)
            if not has_up:
                confirm_only.append((a, f_, has_down))

    ap("─" * 100)
    ap("第三部分  构帧约定说明（验收关注点）")
    ap("─" * 100)
    ap("1. 上行应答约定: 下列下行控制/设置类 Fn 的上行应答按标准回 AFN=00H-F1 确认/否认帧")
    ap("   （模板对其上行方向显式抛 UnsupportedFn，不静默产出错帧；确认/否认帧")
    ap("    已包含在第二部分 RX 开头的 00H 用例中）:")
    line = "   "
    for a, f_, _ in confirm_only:
        if (a, f_) == (0x10, 104):
            continue  # 10H-F104 上行格式标准未定义，单独说明
        s = f" {a:02X}H-F{f_}"
        if len(line) + len(s) > 96:
            ap(line)
            line = "   "
        line += s
    if line.strip():
        ap(line)
    ap("   例外: 10H-F104 上行格式蒸馏文档未定义（构帧模板报 UnsupportedFn，建议 data.raw 透传）。")
    ap("2. 03H/10H 查询类: 下行=查询请求(TX), 上行=查询应答(RX), 两方向数据单元格式不同,")
    ap("   故同一 Fn 在 TX/RX 两部分各出现一次。")
    ap("3. 02H 数据转发上下行同构; 06H 主动上报仅上行; 01H/12H 及 10H-F104 下行无数据单元。")
    ap("4. 多字节 BIN 一律小端（标准备注1）; 14H 路由数据抄读为上行请求/下行应答（反向）。")
    ap("5. 安徽分钟级采集扩展 10H-F230/F231、11H-F231/F232 为下行构帧已覆盖;")
    ap("   11H-F1 另支持安徽单节点 action 格式（本清单采用国网 nodes 格式用例）。")
    ap("6. 每帧自检项: 解码回环 AFN/FN 与构帧一致 + CS 校验通过 + 无解析告警。")
    ap("")
    ap("─" * 100)
    ap(f"验收结论: 共构建 {idx} 帧, 自检通过 {n_ok} 帧, 失败 {idx - n_ok} 帧"
       + (f"  失败清单: {fail}" if fail else "  —— 全部通过 ✓"))
    ap("─" * 100)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(L) + "\n")
    print(f"已生成: {out_path}")
    print(f"共 {idx} 帧 (TX {len(all_tx)} / RX {len(all_rx)}), 自检通过 {n_ok}, 失败 {idx - n_ok}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
