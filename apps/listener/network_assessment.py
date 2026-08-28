"""网络承载能力评估（按中央信标周期 + 网络隔离）。纯 Python，不依赖 DLL。

数据链路：
  - 帧从 frames 表按时间窗口分页抽样取出（raw_hex + log_time）。
  - 从 GW 侦听台封装帧（7E FF 02 <20字节头> <FCH16> <MPDU> ... 7E）中提取：
      · NID（帧控制 FCH 字节 1-3，小端，与 DLL simple.SNID 一致）
      · 中央信标（FCH[0]&7==0 定界符=信标；MPDU[0]&7==2 信标类型=中央信标）
      · CCO MAC（MPDU[2:8]，6 字节）
      · 信标周期计数（MPDU[8:12]，小端 UInt32，上电从 0 每周期 +1）
  - 按实测相邻信标到达间隔（去重重复抓包）估算信标周期；中央信标帧 Detail
    携带「信标周期Xms」权威参数时优先采用（同相线周期，用户拍板）。
  - 按网络（NID，能取到 CCO MAC 时用联合键）隔离分组统计。

三级判定（记忆库 B 类规则）：
  通信成功率：健康 >=98%，亚健康 90~98%，故障 <90%
  离线率：    健康 <=2%，亚健康 2~10%，故障 >10%
  汇总：全健康=健康；有亚健康无故障=亚健康；有故障=故障
  离线率弱代理：某 STA 周期窗口无上报=该周期离线；active_sta 取全日志活跃 STA
  集合；无法判定时 offline_rate=None 并从评级剔除（仅用成功率）。

B 档/C 档（从 summary_json 的 Detail 文本提取，网络级统计）：
  B 档时隙占用：CSMA 时隙占比=CSMA时隙大小/信标周期，>60% 降级、>80% 故障。
  C 档路由/信道：路由评估剩余时间 <30s 或信道变更 >10 次 → 降级。
  判定与 A 档稳定性一起最差合并进周期/网络评级。
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from typing import Iterable, Optional

# 到达间隔推算的合法范围：交错网络里相邻中央信标到达间隔应在 1~10 秒，
# 超出视为乱序/异常（仅用于 _estimate_period 的间隔过滤，不约束信标参数路径）。
BEACON_PERIOD_MIN_MS = 1_000
BEACON_PERIOD_MAX_MS = 10_000
# 信标参数路径的合法范围：Detail「信标周期Xms」是协议权威值（同相线周期），
# 实际设备可配置更大（实测 14878ms），故放宽为 500ms~120s；低于 500ms 视为
# 解析异常，高于 120s 超出常见信标周期（路由周期 20~420s 是另一概念）。
BEACON_PARAM_MIN_MS = 500
BEACON_PARAM_MAX_MS = 120_000
# 同一信标被重复抓包的时间窗口（实测重复抓包间隔 <200ms，留 800ms 余量）
BEACON_DUP_WINDOW_MS = 800
# 中央信标识别成功所需的最少去重样本数（间隔数）
MIN_BEACON_INTERVALS = 3

# 帧类型（FCH[0] 低 3 位）
FRM_TYPE_BEACON = 0  # 信标帧 / SOF 帧（定界符 0）
# 信标载荷类型（MPDU[0] 低 3 位）
BCN_TYPE_CENTRAL = 2  # 中央信标

# 评级枚举
HEALTHY = "healthy"
DEGRADED = "degraded"
FAULT = "fault"

# 别名兼容：前端可能传中文别名，统一映射
LEVEL_ALIASES = {
    "healthy": HEALTHY, "健康": HEALTHY, "正常": HEALTHY,
    "degraded": DEGRADED, "亚健康": DEGRADED,
    "fault": FAULT, "故障": FAULT, "异常": FAULT,
}

# 成功率阈值（含下界）
SUCCESS_HEALTHY_MIN = 98.0   # >=98 健康
SUCCESS_DEGRADED_MIN = 90.0  # 90~98 亚健康，<90 故障
# 离线率阈值（含下界）
OFFLINE_HEALTHY_MAX = 2.0    # <=2 健康
OFFLINE_DEGRADED_MAX = 10.0  # 2~10 亚健康，>10 故障

# 稳定性维度阈值（用户拍板规格）
PROXY_CHANGE_RATIO_FAULT = 8.0   # 代理变更帧占比 >8% → 该周期稳定性降级
ASSOC_RATIO_DEGRADED = 5.0       # 关联请求帧占比 >5% → 降级（默认值，可调）
STABILITY_MIN_DURATION_S = 7200  # 日志总时长 >7200s(2h) 才启用稳定性判级
# FrmType 精确中文串（C# DLL 输出的 simple.FrmType 取值）
FRMTYPE_PROXY_CHANGE = "代理变更请求"
FRMTYPE_ASSOC = "关联请求"
# 中央信标 FrmType 别名（V1.0.23 后 DLL 输出「中央信标」，实测日志 COM4_20260812 为「广播信标」）
FRMTYPE_CENTRAL_BEACON_ALIASES = ("广播信标", "中央信标")

# B 档：时隙占用/上行余量（CSMA 时隙占比阈值，对齐 A1「绑定:CSMA≈4:1」推算）
SLOT_CSMA_RATIO_DEGRADED = 60.0  # CSMA 时隙占比 >60% → 降级（CSMA 占比过高→拥塞风险）
SLOT_CSMA_RATIO_FAULT = 80.0     # CSMA 时隙占比 >80% → 故障（CSMA 接近满占，无上行余量）
# B 档新定义：CSMA 时段实际帧密度/拥塞（国网通用无绑定时隙，改用帧密度判级）
# 帧密度 = 非信标帧数 / 信标周期秒（帧/秒）；峰值超阈值判拥塞。初值可调。
CSMA_DENSITY_DEGRADED = 50.0     # CSMA 时段帧密度峰值 >50 帧/秒 → 降级（竞争趋拥塞）
CSMA_DENSITY_FAULT = 100.0       # CSMA 时段帧密度峰值 >100 帧/秒 → 故障（重度拥塞）
# C 档：路由/信道切换
ROUTE_ESTIMATE_REMAIN_LOW = 30.0  # 路由评估剩余时间 <30s 视为路由紧张 → 降级
CHANNEL_CHANGE_FAULT = 10         # 信道变更事件次数 >10 视为频繁切换 → 降级

_TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})$")

# B 档/C 档 Detail 文本提取正则（实测格式）
# DLL 输出中央信标 Detail 用「时隙分配[...]」（实测 COM4_20260812），
# 旧代码误写成「时隙配置」导致提取不到；两种写法均兼容。
_SLOT_CONFIG_RE = re.compile(
    r"时隙(?:分配|配置)\[信标周期(\d+)ms 信标时隙长度(\d+)ms "
    r"RF信标时隙长度(\d+)ms CSMA时隙大小(\d+)ms\]"
)
_ROUTE_REMAIN_RE = re.compile(r"路由评估剩余时间[:：](\d+)\s*[sS秒]")
_PCO_SUCCESS_RE = re.compile(r"经PCO通信成功数[:：](\d+)")
# 信道变更/切换：支持 信道变更[...] 与 无线信道变更[...]（内部含 信道/剩余 键）
_CHANNEL_CHANGE_BRACKET_RE = re.compile(r"(?:信道变更|无线信道变更)\[([^\]]*)\]")
_CHANNEL_SWITCH_REMAIN_RE = re.compile(r"信道切换剩余时间[:：](\d+)\s*[sS秒]")
_CHANNEL_MARKERS = ("信道变更", "无线信道变更", "信道切换")


# ---------------------------------------------------------------------------
# 帧解码与字段提取
# ---------------------------------------------------------------------------

def _decode_frame(raw_hex: str) -> Optional[dict]:
    """解码 GW 侦听台封装帧，返回 {nid, frm_type, fch, mpdu} 或 None。

    GW 封装（双模侦听台）：7E FF 02 <20 字节头> <FCH 16 字节> <MPDU> <CRC32> 7E
    与 libs/shared/dll/src/snifferFrame.cs FrmPreprc_gw 的切分一致：
    frm = 去掉首尾 7E 的字节流；payload 从 frm[22] 起；FCH 为 payload 前 16 字节。
    """
    try:
        bs = bytes.fromhex(re.sub(r"\s+", "", raw_hex or ""))
    except (ValueError, TypeError):
        return None
    if len(bs) < 3 or bs[0] != 0x7E:
        return None
    frm = bs[1:-1] if bs[-1] == 0x7E else bs[1:]
    if len(frm) < 22 + 16:
        return None
    if frm[0] != 0xFF or frm[1] != 0x02:
        # 非 GW 封装（普通 NW 帧）暂不支持，返回 None（分组退化为仅凭帧本身无法识别）
        return None
    fch = frm[22:38]
    mpdu = frm[38:]
    # 帧类型 = FCH[0] 低 3 位（0=信标，1=SACK，2=ACK，3=网间协调）
    frm_type = fch[0] & 0x07
    # NID = FCH[1..3]，字节序列为小端（fch[1]=最低字节）
    nid = (fch[3] << 16) | (fch[2] << 8) | fch[1]
    return {"nid": nid, "frm_type": frm_type, "fch": fch, "mpdu": mpdu}


def extract_nid(raw_hex: str) -> Optional[int]:
    """从帧 hex 提取 24 位网络标识 NID，无法识别返回 None。

    帧控制字节 0-3（FCH 前 4 字节）：bit0-2 定界符(0=信标) + bit3-7 网络类型 +
    NID 24bit。实测字节序（与 DLL 的 SNID 一致）：FCH[1]=NID 最低字节。
    """
    frame = _decode_frame(raw_hex)
    if frame is None:
        return None
    return frame["nid"]


def extract_cco_mac(raw_hex: str) -> Optional[str]:
    """从中央信标帧提取 CCO MAC（6 字节，形如 26-09-13-46-60-00）。

    载荷内：信标类型(2=中央信标) → 关联标志 → 组网序列号(8bit) → CCO MAC(48bit)
    → 信标周期计数(32bit)。识别不了（非信标/载荷不完整）返回 None。
    """
    frame = _decode_frame(raw_hex)
    if frame is None or frame["frm_type"] != FRM_TYPE_BEACON:
        return None
    mpdu = frame["mpdu"]
    if len(mpdu) < 8 or (mpdu[0] & 0x07) != BCN_TYPE_CENTRAL:
        return None
    return "-".join(f"{b:02X}" for b in mpdu[2:8])


def _extract_beacon_fields(frame: dict) -> Optional[dict]:
    """从已解码帧中提取中央信标字段：{cnt, mac, tei}，非中央信标返回 None。"""
    if frame["frm_type"] != FRM_TYPE_BEACON:
        return None
    mpdu = frame["mpdu"]
    if len(mpdu) < 12 or (mpdu[0] & 0x07) != BCN_TYPE_CENTRAL:
        return None
    cnt = int.from_bytes(mpdu[8:12], "little")
    mac = "-".join(f"{b:02X}" for b in mpdu[2:8])
    # 源 TEI = FCH[8..9] 小端 UInt16 低 12 位（CCO 恒为 1）
    tei = (frame["fch"][8] | (frame["fch"][9] << 8)) & 0x0FFF
    return {"cnt": cnt, "mac": mac, "tei": tei}


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------

def _clock_ms(text: str) -> Optional[int]:
    """HH:MM:SS.mmm → 当日毫秒；格式不符返回 None。"""
    if not text:
        return None
    match = _TIME_PATTERN.match(text)
    if not match:
        return None
    hour, minute, second, milli = (int(part) for part in match.groups())
    return hour * 3_600_000 + minute * 60_000 + second * 1_000 + milli


def _absolute_ms(times: Iterable[str]) -> list[Optional[int]]:
    """时间序列转绝对毫秒，处理跨天翻转（与前一日时间差 >12h 视为进入新一天）。"""
    result = []
    day_offset = 0
    prev_ms = None
    for text in times:
        ms = _clock_ms(text)
        if ms is None:
            result.append(None)
            continue
        if prev_ms is not None and ms < prev_ms - 12 * 3_600_000:
            day_offset += 1
        absolute = day_offset * 86_400_000 + ms
        result.append(absolute)
        prev_ms = ms
    return result


def _clock_text(absolute_ms: int) -> str:
    """绝对毫秒 → HH:MM:SS.mmm（取当日部分）。"""
    ms = absolute_ms % 86_400_000
    hour, rem = divmod(ms, 3_600_000)
    minute, rem = divmod(rem, 60_000)
    second, milli = divmod(rem, 1_000)
    return f"{hour:02d}:{minute:02d}:{second:02d}.{milli:03d}"


# ---------------------------------------------------------------------------
# 信标周期扫描
# ---------------------------------------------------------------------------

def scan_beacon_periods(frames) -> dict:
    """识别中央信标候选并估算实测信标周期。

    frames: 可迭代的 (log_time, raw_hex) 二元组，或带 log_time/raw_hex 键的 dict。

    返回：
      {beacon_period_ms, confidence, method, sample_count, interval_count}
      method ∈ {"beacon_param", "central_beacon", "sof_cluster", "undetected"}
      识别不出信标时 beacon_period_ms=None，不抛异常。

    周期判定优先读中央信标 Detail 里的「信标周期Xms」参数：协议定义的
    「信标周期」= 同相线 CCO 中央信标重复间隔，是协议权威值（实测设备可配
    置 14878ms 等大周期）；而相邻中央信标到达间隔在三相交错网络里是三相
    轮发的短间隔（约 1/3），不能代表同相线周期，故参数优先（用户拍板）。
    参数落在 [BEACON_PARAM_MIN_MS, BEACON_PARAM_MAX_MS]（500ms~120s）即采
    信；提取失败/越界时才退回到达间隔推算（1~10s）。
    """
    records = []
    for item in frames:
        if isinstance(item, (tuple, list)):
            log_time, raw_hex = item[0], item[1]
        else:
            log_time, raw_hex = item.get("log_time"), item.get("raw_hex")
        frame = _decode_frame(raw_hex)
        if frame is None:
            continue
        records.append((log_time, frame))

    times = [r[0] for r in records]
    absolute = _absolute_ms(times)

    # 参数优先路径（路径零）：读取中央信标帧 Detail 的「信标周期Xms」。
    # 中央信标判定：FrmType ∈ 中央信标别名，或解码 frm_type 为信标帧
    # （FRM_TYPE_BEACON，中央信标周期性广播时隙分配）。收集成功样本的众数，
    # 落在信标参数范围 [BEACON_PARAM_MIN_MS, BEACON_PARAM_MAX_MS]（500ms~
    # 120s）内即直接返回（协议权威值，不受 1~10s 间隔推算范围约束），不再
    # 用到达间隔推算。
    period_params = []
    for item in frames:
        if isinstance(item, (tuple, list)):
            summary_json, raw_hex = None, item[1]
        else:
            summary_json = item.get("summary_json")
            raw_hex = item.get("raw_hex")
        is_central_beacon = False
        if _extract_frm_type(summary_json) in FRMTYPE_CENTRAL_BEACON_ALIASES:
            is_central_beacon = True
        else:
            frame = _decode_frame(raw_hex)
            if frame is not None and frame["frm_type"] == FRM_TYPE_BEACON:
                is_central_beacon = True
        if not is_central_beacon:
            continue
        fields = extract_slot_fields(_extract_detail(summary_json))
        if fields and fields.get("beacon_period_ms"):
            period_params.append(fields["beacon_period_ms"])
    if period_params:
        param_mode = statistics.mode(period_params)
        if BEACON_PARAM_MIN_MS <= param_mode <= BEACON_PARAM_MAX_MS:
            sample_count = len(period_params)
            return {
                "beacon_period_ms": param_mode,
                "confidence": round(min(1.0, sample_count / 8.0), 3),
                "method": "beacon_param",
                "sample_count": sample_count,
                "interval_count": 0,
            }

    # 路径一：中央信标周期计数去重
    beacons = []  # (absolute_ms, cnt)
    for (log_time, frame), abs_ms in zip(records, absolute):
        if abs_ms is None:
            continue
        fields = _extract_beacon_fields(frame)
        if fields is not None:
            beacons.append((abs_ms, fields["cnt"]))
    beacons.sort(key=lambda item: item[0])

    # 同一信标的重复抓包：周期计数相同只保留最先到达的一条
    deduped = []
    seen_cnt = set()
    for abs_ms, cnt in beacons:
        if cnt in seen_cnt:
            continue
        seen_cnt.add(cnt)
        deduped.append((abs_ms, cnt))

    gaps = _inter_arrival_gaps(deduped)
    result = _estimate_period(gaps)
    if result["beacon_period_ms"] is not None:
        result["method"] = "central_beacon"
        result["sample_count"] = len(deduped)
        result["interval_count"] = len(gaps)
        return result

    # 路径二：退化方案——按时间序列检测周期性到达的帧簇（SOF 帧）。
    # 无周期计数时用窗口合并重复抓包，再取簇间到达间隔。
    sof_times = [
        abs_ms for (_, frame), abs_ms in zip(records, absolute)
        if abs_ms is not None and frame["frm_type"] == FRM_TYPE_BEACON
    ]
    sof_times.sort()
    cluster_gaps = _inter_arrival_gaps([(t, None) for t in sof_times])
    result = _estimate_period(cluster_gaps)
    if result["beacon_period_ms"] is not None:
        result["method"] = "sof_cluster"
        result["sample_count"] = len(sof_times)
        result["interval_count"] = len(cluster_gaps)
        return result

    return {
        "beacon_period_ms": None,
        "confidence": 0.0,
        "method": "undetected",
        "sample_count": 0,
        "interval_count": 0,
    }


def _inter_arrival_gaps(sorted_records) -> list[int]:
    """对按时间排序的记录计算相邻到达间隔；记录为 (abs_ms, 去重键) 列表。

    去重键相同且间隔小于重复窗口的记录视为同一传输的重复抓包，跳过。
    """
    gaps = []
    prev_ms = None
    prev_key = None
    for abs_ms, key in sorted_records:
        if prev_ms is None:
            prev_ms, prev_key = abs_ms, key
            continue
        gap = abs_ms - prev_ms
        same_transmission = (
            key is not None
            and prev_key == key
            and gap < BEACON_DUP_WINDOW_MS
        )
        if not same_transmission and gap > 0:
            gaps.append(gap)
            prev_ms, prev_key = abs_ms, key
        elif same_transmission:
            pass  # 同一信标重复抓包，跳过
        else:
            prev_ms, prev_key = abs_ms, key
    return gaps


def _estimate_period(gaps: list[int]) -> dict:
    """由到达间隔估算周期：取合法范围 [1s,10s] 内的间隔众数簇的中位数。

    用间隔计数（直方图 bin=200ms）找出最集中的周期簇，避免漏拍（2×周期）
    与乱序抓包干扰。
    """
    valid = [g for g in gaps if BEACON_PERIOD_MIN_MS <= g <= BEACON_PERIOD_MAX_MS]
    if len(valid) < MIN_BEACON_INTERVALS:
        return {"beacon_period_ms": None, "confidence": 0.0}

    # 直方图：bin 宽度 200ms，取计数值最高的 bin 中心作为周期
    bin_size = 200
    counts: dict[int, list[int]] = {}
    for g in valid:
        bucket = (g // bin_size) * bin_size
        counts.setdefault(bucket, []).append(g)
    best_bucket = max(counts, key=lambda b: (len(counts[b]), -abs(b - 2000)))
    cluster = counts[best_bucket]

    period_ms = int(round(statistics.median(cluster) / 100.0) * 100)
    if not (BEACON_PERIOD_MIN_MS <= period_ms <= BEACON_PERIOD_MAX_MS):
        return {"beacon_period_ms": None, "confidence": 0.0}

    std = statistics.pstdev(cluster) if len(cluster) > 1 else 0.0
    consistency = max(0.0, min(1.0, 1.0 - std / max(period_ms, 1)))
    confidence = consistency * min(1.0, len(cluster) / 8.0)
    return {
        "beacon_period_ms": period_ms,
        "confidence": round(confidence, 3),
        "_cluster_count": len(cluster),
    }


# ---------------------------------------------------------------------------
# 三级判定
# ---------------------------------------------------------------------------

def _classify(
    success_rate: Optional[float],
    offline_rate: Optional[float],
    stability_level: Optional[str] = None,
    slot_level: Optional[str] = None,
    route_channel_level: Optional[str] = None,
) -> tuple[str, str]:
    """按成功率和离线率给出周期评级与原因。

    返回 (level, reason)；离线率不可判定时仅用成功率。
    stability_level/slot_level/route_channel_level: 稳定性/B 档/C 档等级
    （可选），传入时参与最差合并。
    """
    reasons = []
    levels = []

    if success_rate is not None:
        if success_rate >= SUCCESS_HEALTHY_MIN:
            levels.append(HEALTHY)
            reasons.append(f"通信成功率 {success_rate:.1f}%")
        elif success_rate >= SUCCESS_DEGRADED_MIN:
            levels.append(DEGRADED)
            reasons.append(f"通信成功率 {success_rate:.1f}%（亚健康）")
        else:
            levels.append(FAULT)
            reasons.append(f"通信成功率 {success_rate:.1f}%（故障）")

    if offline_rate is not None:
        if offline_rate <= OFFLINE_HEALTHY_MAX:
            levels.append(HEALTHY)
            reasons.append(f"离线率 {offline_rate:.1f}%")
        elif offline_rate <= OFFLINE_DEGRADED_MAX:
            levels.append(DEGRADED)
            reasons.append(f"离线率 {offline_rate:.1f}%（亚健康）")
        else:
            levels.append(FAULT)
            reasons.append(f"离线率 {offline_rate:.1f}%（故障）")

    if stability_level is not None:
        levels.append(stability_level)
        reasons.append(f"稳定性：{stability_level}")

    if slot_level is not None:
        levels.append(slot_level)
        reasons.append(f"时隙占用：{slot_level}")

    if route_channel_level is not None:
        levels.append(route_channel_level)
        reasons.append(f"路由/信道：{route_channel_level}")

    if not levels:
        return HEALTHY, "无有效统计数据"

    # 取最差评级（fault > degraded > healthy）
    order = {FAULT: 2, DEGRADED: 1, HEALTHY: 0}
    level = max(levels, key=lambda value: order[value])
    return level, "；".join(reasons)


def _aggregate_level(levels: list[str]) -> str:
    """周期评级汇总：全健康=健康；有亚健康无故障=亚健康；有故障=故障。"""
    if FAULT in levels:
        return FAULT
    if DEGRADED in levels:
        return DEGRADED
    return HEALTHY


# ---------------------------------------------------------------------------
# 稳定性维度（帧型占比判级）
# ---------------------------------------------------------------------------

def _extract_frm_type(summary_json) -> str:
    """从 summary_json 提取 simple.FrmType 取值，失败返回 "UNKNOWN"。

    兼容两种结构：{"simple": {"FrmType": ...}} 与直接 {"FrmType": ...}。
    """
    if not summary_json:
        return "UNKNOWN"
    try:
        data = json.loads(summary_json)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if not isinstance(data, dict):
        return "UNKNOWN"
    simple = data.get("simple")
    if isinstance(simple, dict) and "FrmType" in simple:
        frm_type = simple["FrmType"]
    else:
        frm_type = data.get("FrmType")
    return str(frm_type) if frm_type is not None else "UNKNOWN"


def _extract_detail(summary_json) -> str:
    """从 summary_json 提取 simple.Detail 文本（|...| 分隔），失败返回 ""。

    兼容两种结构：{"simple": {"Detail": ...}} 与直接 {"Detail": ...}。
    """
    if not summary_json:
        return ""
    try:
        data = json.loads(summary_json)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    simple = data.get("simple")
    if isinstance(simple, dict) and "Detail" in simple:
        detail = simple["Detail"]
    else:
        detail = data.get("Detail")
    return str(detail) if detail is not None else ""


def count_frame_types(frames: list[dict]) -> dict[str, int]:
    """统计帧列表的 FrmType 分布（顶层计数）。

    frames 每项含 summary_json（JSON 字符串）；缺失/解析失败/无 FrmType 计入
    "UNKNOWN"。返回 {FrmType: 帧数}。
    """
    counter: dict[str, int] = {}
    for item in frames:
        frm_type = _extract_frm_type(item.get("summary_json") if isinstance(item, dict) else None)
        counter[frm_type] = counter.get(frm_type, 0) + 1
    return counter


def assess_frames_periods(
    frames: list[dict],
    beacon_period_ms: int,
    stability_level: Optional[str] = None,
    csma_level: Optional[str] = None,
    route_channel_level: Optional[str] = None,
) -> list[dict]:
    """按中央信标周期把【所有帧】分桶统计（不依赖 00E4 分钟上报记录）。

    国网通用日志通常没有 00E4 分钟上报帧，原 assess_periods 依赖
    minute_reports 导致 cycles 为空；本函数改为直接按帧时间戳分桶，
    保证任何日志只要有帧就能产出周期评估。

    frames: 帧列表，每条含 log_time（HH:MM:SS.mmm）/ raw_hex / summary_json。
    返回桶列表，每桶：
      {period_start, period_end, start_time, end_time, beacon_period_ms,
       frame_count, beacon_frame_count, assoc_count, assoc_ratio,
       proxy_change_count, proxy_change_ratio, frame_rate,
       level, rating, level_reason}
    帧时间解析失败全部跳过时返回 []。
    """
    period_ms = int(beacon_period_ms or 0)
    if period_ms <= 0:
        raise ValueError("beacon_period_ms 必须为正数")

    # 用帧时间戳分桶（复用 _absolute_ms 处理跨天）
    buckets: dict[int, list[dict]] = {}
    for item in frames:
        if not isinstance(item, dict):
            continue
        ms = _clock_ms(item.get("log_time"))
        if ms is None:
            continue
        start = ms - (ms % period_ms)
        buckets.setdefault(start, []).append(item)

    order = {HEALTHY: 0, DEGRADED: 1, FAULT: 2}
    levels = [lv for lv in (stability_level, csma_level, route_channel_level) if lv]

    cycles = []
    for start in sorted(buckets):
        rows = buckets[start]
        frame_count = len(rows)
        # 帧型计数（该桶）
        stats = count_frame_types(rows)
        assoc_count = stats.get(FRMTYPE_ASSOC, 0)
        proxy_change_count = stats.get(FRMTYPE_PROXY_CHANGE, 0)
        beacon_count = sum(
            stats.get(key, 0) for key in FRMTYPE_CENTRAL_BEACON_ALIASES
        )
        assoc_ratio = assoc_count * 100.0 / frame_count if frame_count else None
        proxy_ratio = (
            proxy_change_count * 100.0 / frame_count if frame_count else None
        )
        bucket_s = period_ms / 1000.0
        frame_rate = frame_count / bucket_s if bucket_s else None

        level, reason = HEALTHY, None
        if levels:
            worst = max(levels, key=lambda lv: order.get(lv, 0))
            if worst != HEALTHY:
                level = worst
                reason = f"综合维度降级（{worst}）"
        cycles.append({
            "period_start": start,
            "period_end": start + period_ms,
            "start_time": _clock_text(start),
            "end_time": _clock_text(start + period_ms),
            "beacon_period_ms": period_ms,
            "frame_count": frame_count,
            "beacon_frame_count": beacon_count,
            "assoc_count": assoc_count,
            "assoc_ratio": round(assoc_ratio, 2) if assoc_ratio is not None else None,
            "proxy_change_count": proxy_change_count,
            "proxy_change_ratio": round(proxy_ratio, 2) if proxy_ratio is not None else None,
            "frame_rate": round(frame_rate, 2) if frame_rate is not None else None,
            "stability_level": stability_level,
            "slot_level": csma_level,
            "route_channel_level": route_channel_level,
            "level": level,
            "rating": level,
            "level_reason": reason,
        })
    return cycles


def assess_csma_congestion(frames: list[dict], beacon_period_ms: int) -> dict:
    """B 档新定义：CSMA 时段实际帧密度/拥塞（不再用 CSMA 配置占比判级）。

    国网通用场景无绑定时隙；网络好坏的时隙维度应反映 CSMA 竞争时段的
    实际流量密度。按信标周期分桶，统计每桶「非信标帧数 / 周期秒」的帧密度，
    峰值/均值超阈值判拥塞。

    返回 {enabled, csma_density_mean, csma_density_peak, csma_config_ratio,
          level, reason}。
    - csma_config_ratio：CSMA 配置占信标周期比例（extract_slot_fields），
      仅展示不判级（对齐用户确认：配置占比不是健康指标）。
    - 阈值常量 CSMA_DENSITY_DEGRADED / CSMA_DENSITY_FAULT（帧/秒）。
    """
    period_ms = int(beacon_period_ms or 0)
    if period_ms <= 0:
        return {
            "enabled": False, "csma_density_mean": None,
            "csma_density_peak": None, "csma_config_ratio": None,
            "level": HEALTHY, "reason": "invalid_period",
        }

    buckets: dict[int, list[dict]] = {}
    for item in frames:
        if not isinstance(item, dict):
            continue
        ms = _clock_ms(item.get("log_time"))
        if ms is None:
            continue
        start = ms - (ms % period_ms)
        buckets.setdefault(start, []).append(item)

    if not buckets:
        return {
            "enabled": False, "csma_density_mean": None,
            "csma_density_peak": None, "csma_config_ratio": None,
            "level": HEALTHY, "reason": "no_frames",
        }

    bucket_s = period_ms / 1000.0
    densities = []
    for start, rows in buckets.items():
        stats = count_frame_types(rows)
        beacon_count = sum(
            stats.get(key, 0) for key in FRMTYPE_CENTRAL_BEACON_ALIASES
        )
        non_beacon = len(rows) - beacon_count
        if non_beacon > 0 and bucket_s:
            densities.append(non_beacon / bucket_s)

    # CSMA 配置占比（仅展示）：从中央信标 Detail 提取
    config_ratio = None
    for item in frames:
        if not isinstance(item, dict):
            continue
        detail = _extract_detail(item.get("summary_json"))
        slot_field = extract_slot_fields(detail)
        if slot_field and slot_field.get("beacon_period_ms"):
            config_ratio = round(
                slot_field["csma_slot_ms"] * 100.0
                / slot_field["beacon_period_ms"], 2
            )
            break

    peak = max(densities) if densities else None
    mean = sum(densities) / len(densities) if densities else None
    level, reason = HEALTHY, None
    if peak is not None and peak > CSMA_DENSITY_FAULT:
        level = FAULT
        reason = f"CSMA 时段帧密度峰值 {peak:.1f} 帧/秒 超故障阈值 {CSMA_DENSITY_FAULT:.0f}"
    elif peak is not None and peak > CSMA_DENSITY_DEGRADED:
        level = DEGRADED
        reason = f"CSMA 时段帧密度峰值 {peak:.1f} 帧/秒 超降级阈值 {CSMA_DENSITY_DEGRADED:.0f}"

    return {
        "enabled": True,
        "csma_density_mean": round(mean, 2) if mean is not None else None,
        "csma_density_peak": round(peak, 2) if peak is not None else None,
        "csma_config_ratio": config_ratio,
        "level": level,
        "reason": reason,
    }


def assess_stability(
    frame_type_stats: dict, total_duration_s: float, beacon_period_ms: int,
) -> dict:
    """稳定性维度判级：代理变更/关联请求占比超阈值 → 降级。

    返回：
      {enabled, reason, assoc_count, assoc_ratio, proxy_change_count,
       proxy_change_ratio, level}
    日志总时长 <=7200s 时 enabled=False、reason="log_too_short"、level=HEALTHY，
    只统计不判级；否则按 FrmType 计数算占比，任一超阈值即 DEGRADED。
    """
    total = sum(frame_type_stats.values()) if frame_type_stats else 0
    assoc_count = frame_type_stats.get(FRMTYPE_ASSOC, 0)
    proxy_count = frame_type_stats.get(FRMTYPE_PROXY_CHANGE, 0)
    assoc_ratio = assoc_count * 100.0 / total if total else None
    proxy_change_ratio = proxy_count * 100.0 / total if total else None

    base = {
        "assoc_count": assoc_count,
        "assoc_ratio": round(assoc_ratio, 2) if assoc_ratio is not None else None,
        "proxy_change_count": proxy_count,
        "proxy_change_ratio": round(proxy_change_ratio, 2) if proxy_change_ratio is not None else None,
    }

    if total_duration_s <= STABILITY_MIN_DURATION_S:
        return {
            **base,
            "enabled": False,
            "reason": "log_too_short",
            "level": HEALTHY,
        }

    reasons = []
    level = HEALTHY
    if proxy_change_ratio is not None and proxy_change_ratio > PROXY_CHANGE_RATIO_FAULT:
        level = DEGRADED
        reasons.append(
            f"代理变更占比 {proxy_change_ratio:.2f}% 超阈值 {PROXY_CHANGE_RATIO_FAULT:.0f}%"
        )
    if assoc_ratio is not None and assoc_ratio > ASSOC_RATIO_DEGRADED:
        level = DEGRADED
        reasons.append(
            f"关联请求占比 {assoc_ratio:.2f}% 超阈值 {ASSOC_RATIO_DEGRADED:.0f}%"
        )

    return {
        **base,
        "enabled": True,
        "reason": "；".join(reasons) if reasons else None,
        "level": level,
    }


# ---------------------------------------------------------------------------
# B 档：时隙占用/上行余量
# ---------------------------------------------------------------------------

def extract_slot_fields(detail: str) -> Optional[dict]:
    """从 Detail 文本提取时隙配置字段（中央信标周期性广播）。

    实测格式（DLL 输出）：时隙分配[信标周期2094ms 信标时隙长度16ms RF信标时隙长度16ms CSMA时隙大小500ms]
    旧版本 DLL 输出「时隙配置[...]」同样兼容。
    返回 {beacon_period_ms, beacon_slot_ms, rf_beacon_slot_ms, csma_slot_ms}，
    无时隙配置或信标周期非法返回 None。
    """
    if not detail:
        return None
    match = _SLOT_CONFIG_RE.search(detail)
    if not match:
        return None
    period, slot, rf_slot, csma = (int(v) for v in match.groups())
    if period <= 0:
        return None
    return {
        "beacon_period_ms": period,
        "beacon_slot_ms": slot,
        "rf_beacon_slot_ms": rf_slot,
        "csma_slot_ms": csma,
    }


def assess_slot(frame_type_stats: dict, slot_fields_list: list) -> dict:
    """B 档时隙占用判级：CSMA 时隙占比 = CSMA时隙大小 / 信标周期。

    信标周期性广播时隙配置，网络级统计即可（不必逐桶）：取各信标 Detail
    提取的时隙字段，占比均值判级（>60% 降级、>80% 故障），CSMA 值/信标周期
    取众数作代表。返回 {enabled, csma_ratio, csma_slot_ms, beacon_period_ms,
    sample_count, beacon_frame_count, level, reason}；拿不到时隙字段时
    enabled=False、level=HEALTHY、reason="no_slot_config"。
    """
    fields = [
        f for f in (slot_fields_list or [])
        if f and f.get("beacon_period_ms") and f.get("csma_slot_ms")
    ]
    beacon_frame_count = (
        sum(frame_type_stats.get(key, 0) for key in FRMTYPE_CENTRAL_BEACON_ALIASES)
        if frame_type_stats else 0
    )
    if not fields:
        return {
            "enabled": False,
            "csma_ratio": None,
            "csma_slot_ms": None,
            "beacon_period_ms": None,
            "sample_count": 0,
            "beacon_frame_count": beacon_frame_count,
            "level": HEALTHY,
            "reason": "no_slot_config",
        }

    ratios = [f["csma_slot_ms"] * 100.0 / f["beacon_period_ms"] for f in fields]
    csma_ratio = sum(ratios) / len(ratios)
    csma_slot_ms = statistics.mode([f["csma_slot_ms"] for f in fields])
    beacon_period_ms = statistics.mode([f["beacon_period_ms"] for f in fields])

    if csma_ratio > SLOT_CSMA_RATIO_FAULT:
        level = FAULT
        reason = (
            f"CSMA 时隙占比 {csma_ratio:.1f}% 超故障阈值 "
            f"{SLOT_CSMA_RATIO_FAULT:.0f}%"
        )
    elif csma_ratio > SLOT_CSMA_RATIO_DEGRADED:
        level = DEGRADED
        reason = (
            f"CSMA 时隙占比 {csma_ratio:.1f}% 超降级阈值 "
            f"{SLOT_CSMA_RATIO_DEGRADED:.0f}%"
        )
    else:
        level = HEALTHY
        reason = None

    return {
        "enabled": True,
        "csma_ratio": round(csma_ratio, 2),
        "csma_slot_ms": csma_slot_ms,
        "beacon_period_ms": beacon_period_ms,
        "sample_count": len(fields),
        "beacon_frame_count": beacon_frame_count,
        "level": level,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# C 档：路由/信道切换
# ---------------------------------------------------------------------------

def extract_route_fields(detail: str) -> Optional[dict]:
    """从 Detail 文本提取路由评估字段（发现列表/信标）。

    实测格式：|路由评估剩余时间:50s|、|经PCO通信成功数:81|（可选项）。
    返回 {route_estimate_s, pco_success_count}，无路由评估剩余时间返回 None。
    """
    if not detail:
        return None
    remain_match = _ROUTE_REMAIN_RE.search(detail)
    if not remain_match:
        return None
    pco_match = _PCO_SUCCESS_RE.search(detail)
    return {
        "route_estimate_s": int(remain_match.group(1)),
        "pco_success_count": int(pco_match.group(1)) if pco_match else None,
    }


def extract_channel_fields(detail: str) -> Optional[dict]:
    """从 Detail 文本提取信道变更/切换字段（信标）。

    兼容实测/近似格式：
      信道变更[信道:X 剩余:Ys]
      无线信道变更[...]（内部含 信道/剩余 键）
      信道切换剩余时间:X s
    只要出现任一信道变更关键词即视为一次事件（兜底）。返回
    {channel, remain_s}；无信道变更相关内容返回 None。
    """
    if not detail:
        return None
    fields = {"channel": None, "remain_s": None}

    bracket = _CHANNEL_CHANGE_BRACKET_RE.search(detail)
    if bracket:
        inner = bracket.group(1)
        channel_match = re.search(r"信道[:：](\d+)", inner)
        remain_match = re.search(r"剩余[:：](\d+)\s*[sS秒]?", inner)
        if channel_match:
            fields["channel"] = int(channel_match.group(1))
        if remain_match:
            fields["remain_s"] = int(remain_match.group(1))
        return fields

    switch_match = _CHANNEL_SWITCH_REMAIN_RE.search(detail)
    if switch_match:
        fields["remain_s"] = int(switch_match.group(1))
        return fields

    if any(marker in detail for marker in _CHANNEL_MARKERS):
        return fields  # 兜底：仅出现关键词，无法提取数值也计一次事件
    return None


def assess_route_channel(
    frame_type_stats: dict, route_fields_list: list, channel_fields_list: list,
) -> dict:
    """C 档路由/信道判级。

    路由评估剩余时间 <30s（路由紧张）或信道变更事件 >10 次（频繁切换）→
    降级。返回 {enabled, route_estimate_s, channel_change_count, level, reason}；
    既无路由也无信道字段时 enabled=False、level=HEALTHY、reason="no_route_channel_data"。
    """
    routes = [
        f for f in (route_fields_list or [])
        if f and f.get("route_estimate_s") is not None
    ]
    channels = [f for f in (channel_fields_list or []) if f]
    if not routes and not channels:
        return {
            "enabled": False,
            "route_estimate_s": None,
            "channel_change_count": 0,
            "level": HEALTHY,
            "reason": "no_route_channel_data",
        }

    # 取最紧张（最小）的路由评估剩余时间，避免被单次乐观值掩盖
    route_estimate_s = min(f["route_estimate_s"] for f in routes) if routes else None
    channel_change_count = len(channels)

    reasons = []
    level = HEALTHY
    if route_estimate_s is not None and route_estimate_s < ROUTE_ESTIMATE_REMAIN_LOW:
        level = DEGRADED
        reasons.append(
            f"路由评估剩余时间 {route_estimate_s}s 低于阈值 "
            f"{ROUTE_ESTIMATE_REMAIN_LOW:.0f}s"
        )
    if channel_change_count > CHANNEL_CHANGE_FAULT:
        level = DEGRADED
        reasons.append(
            f"信道变更事件 {channel_change_count} 次超阈值 {CHANNEL_CHANGE_FAULT}"
        )

    return {
        "enabled": True,
        "route_estimate_s": route_estimate_s,
        "channel_change_count": channel_change_count,
        "level": level,
        "reason": "；".join(reasons) if reasons else None,
    }


# ---------------------------------------------------------------------------
# 周期分桶评估
# ---------------------------------------------------------------------------

def _is_success(row: dict) -> bool:
    """分钟上报是否成功：无应用层解析失败且响应结果==0（正常应答）。"""
    if row.get("application_error"):
        return False
    return row.get("response_result") == 0


def assess_periods(records, beacon_period_ms: int, active_sta_set: set,
                   stability_level: Optional[str] = None,
                   slot_level: Optional[str] = None,
                   route_channel_level: Optional[str] = None) -> list[dict]:
    """按实测信标周期分桶统计通信成功率/离线率并评级。

    records: 分钟上报记录列表，每条含
        time_seconds(绝对毫秒), station_key, response_result, report_count,
        application_error
    active_sta_set: 全日志活跃 STA 集合（弱代理离线判定的分母）。
    stability_level: 稳定性维度等级（网络级汇总，可选），参与周期评级最差合并。
    slot_level: B 档时隙占用等级（网络级汇总，可选）。
    route_channel_level: C 档路由/信道等级（网络级汇总，可选）。
    """
    period_ms = int(beacon_period_ms or 0)
    if period_ms <= 0:
        raise ValueError("beacon_period_ms 必须为正数")

    buckets: dict[int, list[dict]] = {}
    for row in records:
        start = row["time_seconds"] - (row["time_seconds"] % period_ms)
        buckets.setdefault(start, []).append(row)

    # 剔除空 station_key，避免把未知源计入离线
    active = {s for s in active_sta_set if s}

    cycles = []
    for start in sorted(buckets):
        rows = buckets[start]
        frame_count = len(rows)
        success_count = sum(_is_success(row) for row in rows)
        success_rate = success_count * 100.0 / frame_count if frame_count else None

        reported = {row["station_key"] for row in rows if row.get("station_key")}
        offline_count = len(active - reported) if active else 0
        offline_rate = (
            offline_count * 100.0 / len(active) if active else None
        )

        level, level_reason = _classify(
            success_rate, offline_rate, stability_level,
            slot_level, route_channel_level,
        )
        end = start + period_ms
        cycles.append({
            "period_start": start,
            "period_end": end,
            "start_time": _clock_text(start),
            "end_time": _clock_text(end),
            "beacon_period_ms": period_ms,
            "frame_count": frame_count,
            "success_count": success_count,
            "success_rate": round(success_rate, 2) if success_rate is not None else None,
            "active_sta_count": len(active),
            "offline_sta_count": offline_count if active else None,
            "offline_rate": round(offline_rate, 2) if offline_rate is not None else None,
            "report_count": sum(row.get("report_count") or 0 for row in rows),
            "stability_level": stability_level,  # 网络级稳定性汇总，逐桶透出
            "slot_level": slot_level,            # 网络级 B 档汇总，逐桶透出
            "route_channel_level": route_channel_level,  # 网络级 C 档汇总，逐桶透出
            "level": level,
            "rating": level,  # 前端契约字段
            "level_reason": level_reason,
        })
    return cycles


def _network_summary(cycles: list[dict], stability: Optional[dict] = None,
                     slot: Optional[dict] = None,
                     route_channel: Optional[dict] = None,
                     csma: Optional[dict] = None) -> dict:
    """网络级汇总：总体评级、平均成功率、离线率、各评级周期数、稳定性。

    stability: assess_stability 的返回（可选），其 level 参与总体最差合并，
    并通过 summary.stability 字段透出。
    slot: assess_slot 的返回（B 档，可选），level 参与最差合并，透出 summary.slot。
    route_channel: assess_route_channel 的返回（C 档，可选），level 参与最差
    合并，透出 summary.route_channel。
    csma: assess_csma_congestion 的返回（B 档新定义，可选），level 参与最差
    合并，透出 summary.csma_congestion。
    """
    levels = [cycle["level"] for cycle in cycles]
    if stability is not None:
        levels = [*levels, stability["level"]]
    if slot is not None:
        levels = [*levels, slot["level"]]
    if route_channel is not None:
        levels = [*levels, route_channel["level"]]
    if csma is not None:
        levels = [*levels, csma["level"]]
    rates = [c["success_rate"] for c in cycles if c.get("success_rate") is not None]
    offline = [c["offline_rate"] for c in cycles if c.get("offline_rate") is not None]
    counts = {
        HEALTHY: levels.count(HEALTHY),
        DEGRADED: levels.count(DEGRADED),
        FAULT: levels.count(FAULT),
    }
    summary = {
        "overall_health": _aggregate_level(levels) if levels else HEALTHY,
        "avg_success_rate": round(statistics.mean(rates), 2) if rates else None,
        "offline_rate": round(statistics.mean(offline), 2) if offline else None,
        "counts": counts,
    }
    if stability is not None:
        summary["stability"] = stability
    if slot is not None:
        summary["slot"] = slot
    if route_channel is not None:
        summary["route_channel"] = route_channel
    if csma is not None:
        summary["csma_congestion"] = csma
    return summary


# ---------------------------------------------------------------------------
# NID 解析与 summary 一次性字段提取
# ---------------------------------------------------------------------------

def snid_to_int(snid) -> Optional[int]:
    """summary 的 SNID（24 位网络标识十六进制串，如 00947F69）→ int。

    协议定义 NID 有效取值 1~16777215（Q/GDW 10376.2）；空串/非法十六进制/
    越界返回 None。
    """
    if not isinstance(snid, str):
        return None
    text = snid.strip()
    if not text:
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    if not 0 < value <= 0xFFFFFF:
        return None
    return value


def _parse_summary_fields(summary_json) -> dict:
    """一次 json.loads 提取评估所需字段 {frm_type, detail, snid_int}。

    兼容 {"simple": {...}} 与直接 {FrmType/...} 两种结构；无 summary 或解析
    失败时 frm_type="UNKNOWN"、detail=""、snid_int=None。
    """
    frm_type, detail, snid_int = "UNKNOWN", "", None
    if summary_json:
        try:
            data = json.loads(summary_json)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict):
            simple = data.get("simple")
            source = simple if isinstance(simple, dict) else data
            if source.get("FrmType") is not None:
                frm_type = str(source["FrmType"])
            if source.get("Detail") is not None:
                detail = str(source["Detail"])
            snid_int = snid_to_int(source.get("SNID"))
    return {"frm_type": frm_type, "detail": detail, "snid_int": snid_int}


# 评估可用的 Detail 标记：B/C 档与信标参数的全部正则都要求以下字面量之一，
# 不含任何标记的 Detail（普通帧的 Debug/组网信息等）不参与评估。
DETAIL_ASSESS_MARKERS = (
    "时隙分配", "时隙配置", "路由评估剩余", "信道变更", "无线信道变更", "信道切换",
)


def detail_is_assessable(detail) -> bool:
    """Detail 文本是否含评估可用标记（物化列 assess_detail 的取值依据）。"""
    if not detail:
        return False
    return any(marker in detail for marker in DETAIL_ASSESS_MARKERS)


# ---------------------------------------------------------------------------
# 按网络隔离的分组评估（单趟流式，全量帧参与）
# ---------------------------------------------------------------------------

class _NetworkAccumulator:
    """单个网络（NID）的流式累加器。

    三类入口由评估驱动器按帧调用：
      count_frame —— 每帧一次：帧型计数 + 时间线（周期分桶/帧密度数据源）；
      add_detail —— 携带 Detail 文本的帧：B 档时隙 / C 档路由信道字段；
      add_beacon —— 信标候选帧：Detail 信标周期参数、周期计数样本、CCO MAC。
    """

    __slots__ = (
        "nid", "frm_counts", "timeline", "beacon_params", "slot_fields",
        "route_fields", "channel_fields", "beacon_samples", "sof_times",
        "cco_mac",
    )

    def __init__(self, nid: int):
        self.nid = nid
        self.frm_counts: dict[str, int] = {}
        self.timeline: list[tuple[int, str]] = []        # (绝对毫秒, FrmType)
        self.beacon_params: list[int] = []               # Detail「信标周期Xms」
        self.slot_fields: list[dict] = []
        self.route_fields: list[dict] = []
        self.channel_fields: list[dict] = []
        self.beacon_samples: list[tuple[int, int]] = []  # (绝对毫秒, 周期计数)
        self.sof_times: list[int] = []                   # 信标类帧到达时刻
        self.cco_mac: Optional[str] = None

    def count_frame(self, abs_ms: Optional[int], frm: str) -> None:
        self.frm_counts[frm] = self.frm_counts.get(frm, 0) + 1
        if abs_ms is not None:
            self.timeline.append((abs_ms, frm))
            if frm in FRMTYPE_CENTRAL_BEACON_ALIASES:
                self.sof_times.append(abs_ms)

    def add_detail(self, detail: str) -> None:
        if not detail:
            return
        slot_field = extract_slot_fields(detail)
        if slot_field is not None:
            self.slot_fields.append(slot_field)
        route_field = extract_route_fields(detail)
        if route_field is not None:
            self.route_fields.append(route_field)
        channel_field = extract_channel_fields(detail)
        if channel_field is not None:
            self.channel_fields.append(channel_field)

    def add_beacon(self, abs_ms: Optional[int], frame: Optional[dict],
                   detail: str) -> None:
        slot_field = extract_slot_fields(detail) if detail else None
        if slot_field and slot_field.get("beacon_period_ms"):
            self.beacon_params.append(slot_field["beacon_period_ms"])
        if frame is None or abs_ms is None:
            return
        fields = _extract_beacon_fields(frame)
        if fields is None:
            return
        if fields.get("mac"):
            self.cco_mac = fields["mac"]
        if fields.get("cnt") is not None:
            self.beacon_samples.append((abs_ms, fields["cnt"]))

    def duration_s(self) -> float:
        """时间线跨度（秒）；无有效时间返回 0（旧 _frames_duration_s 语义）。"""
        if not self.timeline:
            return 0.0
        values = [ms for ms, _ in self.timeline]
        return (max(values) - min(values)) / 1000.0

    def scan(self) -> dict:
        """信标周期扫描：Detail 参数众数 → 周期计数间隔 → 信标帧簇间隔。"""
        if self.beacon_params:
            param_mode = statistics.mode(self.beacon_params)
            if BEACON_PARAM_MIN_MS <= param_mode <= BEACON_PARAM_MAX_MS:
                return {
                    "beacon_period_ms": param_mode,
                    "confidence": round(min(1.0, len(self.beacon_params) / 8.0), 3),
                    "method": "beacon_param",
                    "sample_count": len(self.beacon_params),
                    "interval_count": 0,
                }
        deduped: list[tuple[int, int]] = []
        seen_cnt = set()
        for abs_ms, cnt in sorted(self.beacon_samples, key=lambda x: x[0]):
            if cnt in seen_cnt:
                continue
            seen_cnt.add(cnt)
            deduped.append((abs_ms, cnt))
        gaps = _inter_arrival_gaps(deduped)
        result = _estimate_period(gaps)
        if result["beacon_period_ms"] is not None:
            return {
                "beacon_period_ms": result["beacon_period_ms"],
                "confidence": result["confidence"],
                "method": "central_beacon",
                "sample_count": len(deduped),
                "interval_count": len(gaps),
            }
        sof = sorted(self.sof_times)
        cluster_gaps = _inter_arrival_gaps([(t, None) for t in sof])
        result = _estimate_period(cluster_gaps)
        if result["beacon_period_ms"] is not None:
            return {
                "beacon_period_ms": result["beacon_period_ms"],
                "confidence": result["confidence"],
                "method": "sof_cluster",
                "sample_count": len(sof),
                "interval_count": len(cluster_gaps),
            }
        return {
            "beacon_period_ms": None, "confidence": 0.0,
            "method": "undetected", "sample_count": 0, "interval_count": 0,
        }

    def bucket_counters(self, period_ms: int) -> dict[int, Counter]:
        """时间线按信标周期分桶（绝对毫秒，跨天自然分桶）。"""
        buckets: dict[int, Counter] = {}
        for abs_ms, frm in self.timeline:
            start = abs_ms - (abs_ms % period_ms)
            bucket = buckets.get(start)
            if bucket is None:
                bucket = buckets[start] = Counter()
            bucket[frm] += 1
        return buckets

    def build_cycles(self, buckets: dict[int, Counter], period_ms: int,
                     stability_level: Optional[str], csma_level: Optional[str],
                     route_channel_level: Optional[str]) -> list[dict]:
        """由分桶计数构建周期评估（与 assess_frames_periods 字段契约一致）。"""
        order = {HEALTHY: 0, DEGRADED: 1, FAULT: 2}
        levels = [lv for lv in (stability_level, csma_level, route_channel_level) if lv]
        bucket_s = period_ms / 1000.0
        cycles = []
        for start in sorted(buckets):
            counter = buckets[start]
            frame_count = sum(counter.values())
            assoc_count = counter.get(FRMTYPE_ASSOC, 0)
            proxy_change_count = counter.get(FRMTYPE_PROXY_CHANGE, 0)
            beacon_count = sum(
                counter.get(key, 0) for key in FRMTYPE_CENTRAL_BEACON_ALIASES
            )
            assoc_ratio = assoc_count * 100.0 / frame_count if frame_count else None
            proxy_ratio = (
                proxy_change_count * 100.0 / frame_count if frame_count else None
            )
            frame_rate = frame_count / bucket_s if bucket_s else None

            level, reason = HEALTHY, None
            if levels:
                worst = max(levels, key=lambda lv: order.get(lv, 0))
                if worst != HEALTHY:
                    level = worst
                    reason = f"综合维度降级（{worst}）"
            cycles.append({
                "period_start": start,
                "period_end": start + period_ms,
                "start_time": _clock_text(start),
                "end_time": _clock_text(start + period_ms),
                "beacon_period_ms": period_ms,
                "frame_count": frame_count,
                "beacon_frame_count": beacon_count,
                "assoc_count": assoc_count,
                "assoc_ratio": round(assoc_ratio, 2) if assoc_ratio is not None else None,
                "proxy_change_count": proxy_change_count,
                "proxy_change_ratio": round(proxy_ratio, 2) if proxy_ratio is not None else None,
                "frame_rate": round(frame_rate, 2) if frame_rate is not None else None,
                "stability_level": stability_level,
                "slot_level": csma_level,
                "route_channel_level": route_channel_level,
                "level": level,
                "rating": level,
                "level_reason": reason,
            })
        return cycles

    def build_csma(self, buckets: dict[int, Counter], period_ms: int,
                   config_ratio: Optional[float]) -> dict:
        """由分桶计数构建 CSMA 帧密度拥塞（与 assess_csma_congestion 一致）。"""
        period = int(period_ms or 0)
        if period <= 0:
            return {
                "enabled": False, "csma_density_mean": None,
                "csma_density_peak": None, "csma_config_ratio": None,
                "level": HEALTHY, "reason": "invalid_period",
            }
        if not buckets:
            return {
                "enabled": False, "csma_density_mean": None,
                "csma_density_peak": None, "csma_config_ratio": None,
                "level": HEALTHY, "reason": "no_frames",
            }
        bucket_s = period / 1000.0
        densities = []
        for counter in buckets.values():
            beacon_count = sum(
                counter.get(key, 0) for key in FRMTYPE_CENTRAL_BEACON_ALIASES
            )
            non_beacon = sum(counter.values()) - beacon_count
            if non_beacon > 0 and bucket_s:
                densities.append(non_beacon / bucket_s)
        peak = max(densities) if densities else None
        mean = sum(densities) / len(densities) if densities else None
        level, reason = HEALTHY, None
        if peak is not None and peak > CSMA_DENSITY_FAULT:
            level = FAULT
            reason = f"CSMA 时段帧密度峰值 {peak:.1f} 帧/秒 超故障阈值 {CSMA_DENSITY_FAULT:.0f}"
        elif peak is not None and peak > CSMA_DENSITY_DEGRADED:
            level = DEGRADED
            reason = f"CSMA 时段帧密度峰值 {peak:.1f} 帧/秒 超降级阈值 {CSMA_DENSITY_DEGRADED:.0f}"
        return {
            "enabled": True,
            "csma_density_mean": round(mean, 2) if mean is not None else None,
            "csma_density_peak": round(peak, 2) if peak is not None else None,
            "csma_config_ratio": config_ratio,
            "level": level,
            "reason": reason,
        }


def _is_beacon_candidate(frm: str) -> bool:
    """信标候选：FrmType 为中央信标别名，或 summary 缺失/无 FrmType（UNKNOWN，
    需解码定界符位确认）。非别名已知帧型不再解码，消除全量评估的解码瓶颈。"""
    return frm in FRMTYPE_CENTRAL_BEACON_ALIASES or frm == "UNKNOWN"


def assess_by_network_stream(
    rows, records=(), session_duration_s: Optional[float] = None,
    nid_filter: Optional[int] = None, engine: str = "python",
    frame_total: Optional[int] = None, unassigned_total: Optional[int] = None,
) -> dict:
    """网络承载评估主入口：单趟流式消费全量帧，按 NID 严格分网，互不混算。

    rows: 可迭代的帧 dict，字段（全部可选除 log_time）：
      log_time     HH:MM:SS.mmm（必需）
      nid          已解析/物化的 NID int；缺失时按 summary SNID → FCH 解码兜底
      frm_type     已物化的 FrmType 文本；缺失时从 summary_json 提取
      summary_json 原始摘要 JSON（仅在 nid/frm_type 缺失或需 Detail 时解析一次）
      raw_hex      帧_hex（summary 缺失需解码取 NID / 信标候选需解码字段时用）
      _detail_only SQL 物化源的 Detail 行标记：不重复计数/时间线，只补字段
    records: 分钟上报记录（00E4），nid 归网；nid 缺失计入 unassigned_record_count。

    网络键 = NID（组网序列号，Q/GDW 10376.2 网络识别码）。NID 无法识别的帧
    不归属任何网络（计入 unassigned_frame_count 单独透出），绝不混入他网。
    nid_filter 给定时只累计该 NID 的网络。

    session_duration_s: 日志会话权威时长（DB 首尾帧跨度，调用方计算）；
    缺省回退为各网络时间线跨度（兼容旧抽样语义）。稳定性 ≥2h 门禁以此为准。

    frame_total/unassigned_total: SQL 物化源传入的精确总数（时间窗内），
    覆盖驱动器计数；Python 源省略。
    """
    accs: dict[int, _NetworkAccumulator] = {}
    counted = 0
    unassigned = 0
    filtered = 0
    unassigned_records = 0
    day_offset = 0
    prev_clock_ms = None

    for item in rows:
        if not isinstance(item, dict) or not item.get("log_time"):
            continue
        detail_only = bool(item.get("_detail_only"))
        nid = item.get("nid")
        frm = item.get("frm_type")
        summary_json = item.get("summary_json")
        raw_hex = item.get("raw_hex")

        parsed = None
        frame = None
        if nid is None or frm is None:
            parsed = _parse_summary_fields(summary_json)
            if nid is None:
                nid = parsed["snid_int"]
            if frm is None:
                frm = parsed["frm_type"]
        if nid is None and raw_hex:
            frame = _decode_frame(raw_hex)
            if frame is not None:
                nid = frame["nid"]
        if not frm:
            frm = "UNKNOWN"

        if not detail_only:
            counted += 1
            if nid is None:
                unassigned += 1
                continue
            if nid_filter is not None and nid != nid_filter:
                filtered += 1
                continue

        # 跨天翻转（与前一日时间差 >12h 视为进入新一天），全流单一日偏移
        clock = _clock_ms(item.get("log_time"))
        abs_ms = None
        if clock is not None:
            if prev_clock_ms is not None and clock < prev_clock_ms - 12 * 3_600_000:
                day_offset += 1
            abs_ms = day_offset * 86_400_000 + clock
            prev_clock_ms = clock

        acc = accs.get(nid)
        if acc is None:
            acc = accs[nid] = _NetworkAccumulator(nid)

        if detail_only:
            detail = parsed["detail"] if parsed is not None else ""
            if not detail and summary_json:
                detail = _parse_summary_fields(summary_json)["detail"]
            acc.add_detail(detail)
            if _is_beacon_candidate(frm):
                if frame is None and raw_hex:
                    frame = _decode_frame(raw_hex)
                acc.add_beacon(abs_ms, frame, detail)
            continue

        acc.count_frame(abs_ms, frm)
        detail = parsed["detail"] if parsed is not None else ""
        if detail:
            acc.add_detail(detail)
        if _is_beacon_candidate(frm):
            if frame is None and raw_hex:
                frame = _decode_frame(raw_hex)
            acc.add_beacon(abs_ms, frame, detail)

    record_groups: dict[int, list] = {}
    for row in records or ():
        nid = row.get("nid") if isinstance(row, dict) else None
        if nid is None:
            unassigned_records += 1
            continue
        record_groups.setdefault(nid, []).append(row)

    return _finalize_networks(
        accs, record_groups, session_duration_s=session_duration_s, engine=engine,
        frame_total=frame_total, unassigned_total=unassigned_total, counted=counted,
        unassigned=unassigned, filtered=filtered,
        unassigned_records=unassigned_records,
        bucket_resolver=lambda acc, period: acc.bucket_counters(period),
    )


class _AbsTimeTracker:
    """行流的绝对毫秒换算（跨天翻转），与流式驱动器同一套规则。"""

    __slots__ = ("day_offset", "prev_clock_ms")

    def __init__(self):
        self.day_offset = 0
        self.prev_clock_ms = None

    def abs_ms(self, log_time) -> Optional[int]:
        clock = _clock_ms(log_time)
        if clock is None:
            return None
        if (
            self.prev_clock_ms is not None
            and clock < self.prev_clock_ms - 12 * 3_600_000
        ):
            self.day_offset += 1
        self.prev_clock_ms = clock
        return self.day_offset * 86_400_000 + clock


def assess_by_network_aggregate(
    counts_rows, detail_rows, central_rows=(), bucket_rows_fn=None,
    records=(), session_duration_s: Optional[float] = None,
    nid_filter: Optional[int] = None, engine: str = "sql",
    frame_total: Optional[int] = None, unassigned_total: Optional[int] = None,
) -> dict:
    """SQL 物化聚合入口：预聚合行直接构建累加器，避免全量行传输到 Python。

    counts_rows:  (nid, frm_type, cnt) —— GROUP BY 聚合计数（nid 为 NULL 的
                  分组跳过，未识别总数由 unassigned_total 提供）。
    detail_rows:  dict(log_time, nid, frm_type, raw_hex, summary_json) ——
                  携带 Detail 文本的行（B/C 档字段与信标参数来源）。
    central_rows: dict(log_time, raw_hex) —— 中央信标别名行；Detail 缺失时
                  补周期计数样本/CCO MAC（量小，仅信标帧）。
    bucket_rows_fn: callable(period_ms) -> (nid, frm_type, bucket, cnt) 行_iterable，
                  库内窗口函数按信标周期预分桶的聚合结果；周期确定后调用。
    其余参数与 assess_by_network_stream 一致。
    """
    accs: dict[int, _NetworkAccumulator] = {}
    for nid, frm, cnt in counts_rows:
        if nid is None:
            continue
        if nid_filter is not None and nid != nid_filter:
            continue
        acc = accs.get(nid)
        if acc is None:
            acc = accs[nid] = _NetworkAccumulator(nid)
        key = frm or "UNKNOWN"
        acc.frm_counts[key] = acc.frm_counts.get(key, 0) + (cnt or 0)

    tracker = _AbsTimeTracker()
    for row in detail_rows:
        nid = row.get("nid")
        if nid is None or (nid_filter is not None and nid != nid_filter):
            continue
        acc = accs.get(nid)
        if acc is None:
            acc = accs[nid] = _NetworkAccumulator(nid)
        detail = _parse_summary_fields(row.get("summary_json"))["detail"]
        acc.add_detail(detail)
        frm = row.get("frm_type") or "UNKNOWN"
        if _is_beacon_candidate(frm) and row.get("raw_hex"):
            frame = _decode_frame(row["raw_hex"])
            acc.add_beacon(tracker.abs_ms(row.get("log_time")), frame, detail)

    for row in central_rows:
        raw_hex = row.get("raw_hex")
        if not raw_hex:
            continue
        frame = _decode_frame(raw_hex)
        if frame is None:
            continue
        nid = frame["nid"]
        if nid is None or (nid_filter is not None and nid != nid_filter):
            continue
        acc = accs.get(nid)
        if acc is None:
            acc = accs[nid] = _NetworkAccumulator(nid)
        acc.add_beacon(tracker.abs_ms(row.get("log_time")), frame, "")

    # 周期由参数/样本扫描确定后，分桶聚合按（去重的周期值）查询并按网拆分
    periods = {acc.scan()["beacon_period_ms"] for acc in accs.values()}
    periods.discard(None)
    sql_buckets: dict[tuple, dict] = {}
    for period in periods:
        if bucket_rows_fn is None:
            break
        for nid, frm, bucket, cnt in bucket_rows_fn(period):
            if nid is None or (nid_filter is not None and nid != nid_filter):
                continue
            acc = accs.get(nid)
            if acc is None:
                continue
            per_bucket = sql_buckets.setdefault((nid, period), {})
            counter = per_bucket.setdefault(bucket, Counter())
            counter[frm or "UNKNOWN"] += cnt or 0

    def _resolve(acc, period):
        return sql_buckets.get((acc.nid, period)) or {}

    record_groups: dict[int, list] = {}
    unassigned_records = 0
    for row in records or ():
        nid = row.get("nid") if isinstance(row, dict) else None
        if nid is None:
            unassigned_records += 1
            continue
        record_groups.setdefault(nid, []).append(row)

    return _finalize_networks(
        accs, record_groups, session_duration_s=session_duration_s, engine=engine,
        frame_total=frame_total, unassigned_total=unassigned_total,
        counted=frame_total or 0, unassigned=unassigned_total or 0, filtered=0,
        unassigned_records=unassigned_records, bucket_resolver=_resolve,
    )


def _finalize_networks(
    accs, record_groups, *, session_duration_s, engine, frame_total, unassigned_total,
    counted, unassigned, filtered, unassigned_records, bucket_resolver,
) -> dict:
    """网络装配共用尾段：扫描周期 → 分桶 → 四维判级 → 汇总结构。"""
    networks = []
    for nid, acc in accs.items():
        group_records = record_groups.get(nid, [])
        active_sta = {
            row.get("station_key") for row in group_records if row.get("station_key")
        }
        scan = acc.scan()
        period = scan["beacon_period_ms"]
        duration = (
            session_duration_s if session_duration_s is not None else acc.duration_s()
        )
        stability = assess_stability(acc.frm_counts, duration, period or 0)
        slot = assess_slot(acc.frm_counts, acc.slot_fields)
        route_channel = assess_route_channel(
            acc.frm_counts, acc.route_fields, acc.channel_fields
        )
        config_ratio = next(
            (
                f["csma_slot_ms"] * 100.0 / f["beacon_period_ms"]
                for f in acc.slot_fields
                if f.get("beacon_period_ms") and f.get("csma_slot_ms")
            ),
            None,
        )
        if period:
            buckets = bucket_resolver(acc, period)
            csma = acc.build_csma(buckets, period, config_ratio)
            cycles = acc.build_cycles(
                buckets, period, stability["level"], csma["level"],
                route_channel["level"],
            )
        else:
            csma = acc.build_csma({}, 0, config_ratio)
            cycles = []
        # 补充：存在 00E4 分钟上报记录时，成功率/离线率覆盖同周期字段（向前兼容）
        if group_records and cycles:
            try:
                extra = assess_periods(
                    group_records, period, active_sta,
                    stability_level=stability["level"],
                    slot_level=slot["level"],
                    route_channel_level=route_channel["level"],
                )
                extra_by_start = {c["period_start"]: c for c in extra}
                for c in cycles:
                    extra_c = extra_by_start.get(c["period_start"])
                    if extra_c:
                        c["success_rate"] = extra_c.get("success_rate")
                        c["offline_rate"] = extra_c.get("offline_rate")
                        c["success_count"] = extra_c.get("success_count")
                        c["offline_sta_count"] = extra_c.get("offline_sta_count")
            except ValueError:
                pass
        networks.append({
            "nid": nid,
            "cco_mac": acc.cco_mac,
            "beacon_period_ms": period,
            "confidence": scan["confidence"],
            "scan_method": scan["method"],
            "frame_count": sum(acc.frm_counts.values()),
            "record_count": len(group_records),
            "active_sta_count": len(active_sta),
            "cycles": cycles,
            "summary": _network_summary(
                cycles, stability=stability, slot=slot, route_channel=route_channel,
                csma=csma,
            ),
        })

    networks.sort(key=lambda n: n["nid"])
    periods = [n["beacon_period_ms"] for n in networks if n["beacon_period_ms"]]
    beacon_period_ms = statistics.mode(periods) if periods else None
    levels = [n["summary"]["overall_health"] for n in networks]
    return {
        "networks": networks,
        "beacon_period_ms": beacon_period_ms,
        "overall_health": _aggregate_level(levels) if levels else HEALTHY,
        "engine": engine,
        "session_duration_s": (
            round(session_duration_s, 3) if session_duration_s is not None else None
        ),
        "frame_total": frame_total if frame_total is not None else counted,
        "unassigned_frame_count": (
            unassigned_total if unassigned_total is not None else unassigned
        ),
        "filtered_frame_count": filtered,
        "unassigned_record_count": unassigned_records,
    }


def assess_by_network(frames, records, session_duration_s: Optional[float] = None) -> dict:
    """兼容入口：frames 为帧 dict 列表（或 (log_time, raw_hex) 二元组列表）。

    内部转单趟流式评估；session_duration_s 缺省时稳定性门禁回退为各网络
    帧时间跨度（与旧实现一致）。
    """

    def _rows():
        for item in frames:
            if isinstance(item, (tuple, list)):
                yield {"log_time": item[0], "raw_hex": item[1]}
            else:
                yield item

    return assess_by_network_stream(
        _rows(), records, session_duration_s=session_duration_s
    )
