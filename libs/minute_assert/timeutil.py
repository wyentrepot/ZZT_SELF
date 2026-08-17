"""分钟采集冻结时刻 BCD/时间辅助（移植自 H_CCO/analyze_minute_logs.py）。

冻结时刻（6 字节 BCD：秒分时日月年）转自纪元分钟数，用于比较与定位周期。
"""
from __future__ import annotations


def bcd(value: int) -> int:
    """BCD 字节转十进制。"""
    return ((value >> 4) & 0xF) * 10 + (value & 0xF)


def _days_from_civil(year: int, month: int, day: int) -> int:
    """公历日期转自纪元天数（与原始脚本一致，Howard Hinnant 算法）。"""
    y = year - (1 if month <= 2 else 0)
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _civil_from_days(days: int) -> tuple[int, int, int]:
    """自纪元天数转公历日期（与原始脚本一致）。"""
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    return (y + (1 if m <= 2 else 0), m, d)


def fz_minutes(fz_bytes: bytes) -> int:
    """冻结时刻（BCD 秒分时日月年）转自纪元公历分钟数。"""
    year = 2000 + bcd(fz_bytes[5])
    month = bcd(fz_bytes[4])
    day = bcd(fz_bytes[3])
    hour = bcd(fz_bytes[2])
    minute = bcd(fz_bytes[1])
    return _days_from_civil(year, month, day) * 1440 + hour * 60 + minute


def fz_text(fz_bytes: bytes) -> str:
    """冻结时刻 BCD 字节转 HH:MM 文本。"""
    return f"{bcd(fz_bytes[2]):02d}:{bcd(fz_bytes[1]):02d}"


def cycle_window_key(fz_bytes: bytes, period: int) -> int:
    """冻结时刻所属采集窗口的排序键（按周期对齐）。"""
    return (fz_minutes(fz_bytes) // period) * period


def cycle_window_label(start_minutes: int, period: int) -> str:
    """窗口起点分钟数转时间区间文本，如 08:44-08:46。"""
    _, _, day = _civil_from_days(start_minutes // 1440)
    start = start_minutes % 1440
    end_minutes = start_minutes + period
    _, _, end_day = _civil_from_days(end_minutes // 1440)
    end = end_minutes % 1440
    if end_day != day:
        end += 1440
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


def cycle_window_label_short(fz_bytes: bytes, period: int) -> str:
    """冻结时刻所属采集窗口的时间区间文本，如 08:44-08:46。"""
    return cycle_window_label(cycle_window_key(fz_bytes, period), period)


def period_range(min_minutes: int, max_minutes: int, period: int) -> range:
    """返回 [min, max] 范围内按周期对齐的全部窗口起点分钟数。"""
    first = min_minutes // period * period
    last = max_minutes // period * period
    return range(first, last + period, period)
