"""网络承载能力评估（按中央信标周期 + 网络隔离）。纯 Python，不依赖 DLL。

数据链路：
  - 帧从 frames 表按时间窗口分页抽样取出（raw_hex + log_time）。
  - 从 GW 侦听台封装帧（7E FF 02 <20字节头> <FCH16> <MPDU> ... 7E）中提取：
      · NID（帧控制 FCH 字节 1-3，小端，与 DLL simple.SNID 一致）
      · 中央信标（FCH[0]&7==0 定界符=信标；MPDU[0]&7==2 信标类型=中央信标）
      · CCO MAC（MPDU[2:8]，6 字节）
      · 信标周期计数（MPDU[8:12]，小端 UInt32，上电从 0 每周期 +1）
  - 按实测相邻信标到达间隔（去重重复抓包）估算信标周期。
  - 按网络（NID，能取到 CCO MAC 时用联合键）隔离分组统计。

三级判定（记忆库 B 类规则）：
  通信成功率：健康 >=98%，亚健康 90~98%，故障 <90%
  离线率：    健康 <=2%，亚健康 2~10%，故障 >10%
  汇总：全健康=健康；有亚健康无故障=亚健康；有故障=故障
  离线率弱代理：某 STA 周期窗口无上报=该周期离线；active_sta 取全日志活跃 STA
  集合；无法判定时 offline_rate=None 并从评级剔除（仅用成功率）。
"""
from __future__ import annotations

import re
import statistics
from typing import Iterable, Optional

# 协议规定的中央信标周期范围：1~10 秒
BEACON_PERIOD_MIN_MS = 1_000
BEACON_PERIOD_MAX_MS = 10_000
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

_TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})$")


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
      method ∈ {"central_beacon", "sof_cluster", "undetected"}
      识别不出信标时 beacon_period_ms=None，不抛异常。
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

def _classify(success_rate: Optional[float], offline_rate: Optional[float]) -> tuple[str, str]:
    """按成功率和离线率给出周期评级与原因。

    返回 (level, reason)；离线率不可判定时仅用成功率。
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
# 周期分桶评估
# ---------------------------------------------------------------------------

def _is_success(row: dict) -> bool:
    """分钟上报是否成功：无应用层解析失败且响应结果==0（正常应答）。"""
    if row.get("application_error"):
        return False
    return row.get("response_result") == 0


def assess_periods(records, beacon_period_ms: int, active_sta_set: set) -> list[dict]:
    """按实测信标周期分桶统计通信成功率/离线率并评级。

    records: 分钟上报记录列表，每条含
        time_seconds(绝对毫秒), station_key, response_result, report_count,
        application_error
    active_sta_set: 全日志活跃 STA 集合（弱代理离线判定的分母）。
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

        level, level_reason = _classify(success_rate, offline_rate)
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
            "level": level,
            "rating": level,  # 前端契约字段
            "level_reason": level_reason,
        })
    return cycles


def _network_summary(cycles: list[dict]) -> dict:
    """网络级汇总：总体评级、平均成功率、离线率、各评级周期数。"""
    levels = [cycle["level"] for cycle in cycles]
    rates = [c["success_rate"] for c in cycles if c["success_rate"] is not None]
    offline = [c["offline_rate"] for c in cycles if c["offline_rate"] is not None]
    counts = {
        HEALTHY: levels.count(HEALTHY),
        DEGRADED: levels.count(DEGRADED),
        FAULT: levels.count(FAULT),
    }
    return {
        "overall_health": _aggregate_level(levels) if levels else HEALTHY,
        "avg_success_rate": round(statistics.mean(rates), 2) if rates else None,
        "offline_rate": round(statistics.mean(offline), 2) if offline else None,
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# 按网络隔离的分组评估
# ---------------------------------------------------------------------------

def _frame_network_key(frame: dict) -> tuple:
    """帧所属网络键：中央信标帧取 (NID, CCO MAC) 联合键，否则仅 NID。"""
    nid = frame.get("nid")
    if nid is None:
        return None
    mac = None
    if frame.get("frm_type") == FRM_TYPE_BEACON:
        mac = extract_cco_mac(frame.get("raw_hex", ""))
    return (nid, mac) if mac else (nid, None)


def assess_by_network(frames, records) -> dict:
    """按网络隔离评估：帧按 NID（信标帧加 CCO MAC 联合键）分组，互不混算。

    frames: 帧列表，每条含 log_time / raw_hex（可含已算好的 nid）。
    records: 分钟上报记录列表，每条含 time_seconds / station_key /
             response_result / report_count / application_error / nid。

    返回：
      {networks: [{nid, cco_mac, beacon_period_ms, confidence, scan_method,
                   cycles, summary}],
       beacon_period_ms, overall_health}
    """
    frame_groups: dict[tuple, list] = {}
    for item in frames:
        raw_hex = item.get("raw_hex")
        frame = _decode_frame(raw_hex)
        if frame is None:
            continue
        frame["raw_hex"] = raw_hex
        key = _frame_network_key(frame)
        if key is None:
            continue
        frame_groups.setdefault(key, []).append(item)

    # 合并 (nid, None) 到已知 MAC 的网络：同一 NID 下信标帧能取到 MAC 时，
    # 无 MAC 帧（普通数据帧）归属该网络，避免同网被拆成两个键。
    mac_keys = {k for k in frame_groups if k[1] is not None}
    nid_to_mac = {k[0]: k[1] for k in mac_keys}
    for key in list(frame_groups):
        nid, mac = key
        if mac is None and nid in nid_to_mac:
            merged = (nid, nid_to_mac[nid])
            frame_groups.setdefault(merged, [])
            frame_groups[merged].extend(frame_groups[key])
            del frame_groups[key]

    record_groups: dict[int, list[dict]] = {}
    for row in records:
        nid = row.get("nid")
        if nid is None:
            continue
        record_groups.setdefault(nid, []).append(row)

    networks = []
    for (nid, cco_mac), group_frames in frame_groups.items():
        group_records = record_groups.get(nid, [])
        active_sta = {
            row.get("station_key") for row in group_records if row.get("station_key")
        }
        scan = scan_beacon_periods(group_frames)
        cycles = []
        if scan["beacon_period_ms"] is not None:
            try:
                cycles = assess_periods(
                    group_records, scan["beacon_period_ms"], active_sta
                )
            except ValueError:
                cycles = []
        networks.append({
            "nid": nid,
            "cco_mac": cco_mac,
            "beacon_period_ms": scan["beacon_period_ms"],
            "confidence": scan["confidence"],
            "scan_method": scan["method"],
            "frame_count": len(group_frames),
            "record_count": len(group_records),
            "active_sta_count": len(active_sta),
            "cycles": cycles,
            "summary": _network_summary(cycles),
        })

    # 按 NID 排序，稳定输出
    networks.sort(key=lambda n: n["nid"])

    periods = [n["beacon_period_ms"] for n in networks if n["beacon_period_ms"]]
    beacon_period_ms = statistics.mode(periods) if periods else None
    levels = [n["summary"]["overall_health"] for n in networks]
    return {
        "networks": networks,
        "beacon_period_ms": beacon_period_ms,
        "overall_health": _aggregate_level(levels) if levels else HEALTHY,
    }
