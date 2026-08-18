"""分钟采集日志单行解析器（移植自 H_CCO/analyze_minute_logs.py）。

将 CCO 调试日志中的主动上报/被动采集/任务配置/档案下发帧解析为结构化数据。
"""
from __future__ import annotations

import re

#: 主动上报帧：11e4 开头 + 十六进制
ACTIVE_REPORT_RE = re.compile(r"11e400000[0-9a-fA-F]+")
#: 任务配置帧：11e2 开头 + 十六进制
TASK_CONFIG_RE = re.compile(r"11e2[0-9a-fA-F]+")
#: 采集帧：11e3 开头 + 十六进制
READ_FRAME_RE = re.compile(r"11e3[0-9a-fA-F]+")

SCENE_ACTIVE_REPORT = "主动上报"
SCENE_PASSIVE_REPORT = "被动上报"
SCENE_PASSIVE_COLLECT = "被动采集"


def dedup_key(line: str) -> str:
    """从原始日志行提取十六进制内容（去时间戳/前缀）。

    与原始脚本一致：去除 [YYYYMMDD-HH:MM:SS:mmm] 时间戳前缀后，取末尾连续的
    十六进制字符作为去重键。
    """
    stripped = re.sub(r"^\[\d{8}-\d{2}:\d{2}:\d{2}:\d{3}\]", "", line).strip()
    match = re.search(r"[0-9a-fA-F]+$", stripped)
    return match.group(0) if match else stripped


def parse_active_report(line: str) -> tuple[str, int, bytes, int] | None:
    """解析主动上报行，返回 (上报地址, 任务号, 冻结时刻 BCD, 结果码)；无法解析返回 None。"""
    content = dedup_key(line)
    if ACTIVE_REPORT_RE.fullmatch(content) is None:
        return None
    try:
        data = bytes.fromhex(content)
    except ValueError:
        return None
    if len(data) < 27:
        return None
    address = data[13:19][::-1].hex().upper()
    task_id = data[19]
    result = (data[20] >> 5) & 7
    return address, task_id, data[21:27], result


def parse_read_frame(line: str) -> tuple[str, int, str, bytes, int | None] | None:
    """解析主动采集帧，返回 (方向, 任务号, 地址, 冻结时刻 BCD, 结果码)；无法解析返回 None。"""
    content = dedup_key(line)
    if READ_FRAME_RE.fullmatch(content) is None:
        return None
    try:
        data = bytes.fromhex(content)
    except ValueError:
        return None
    if len(data) < 24 or data[17] == 0xFF:
        return None
    direction = "reply" if bool((data[4] | (data[5] << 8)) & 0x1000) else "request"
    result = (data[10] >> 5) & 7 if direction == "reply" else None
    return direction, data[17], data[11:17][::-1].hex().upper(), data[18:24], result


def parse_task_config(line: str) -> tuple[int, int, int] | None:
    """解析任务配置帧，返回 (任务号, 采集周期分钟, 启停标志)；无法解析返回 None。

    启停标志对应项目 mclt_logic.c 中 mclt_task_cfg_parse 解析的 switch_flag：
    1=配置/启动任务，0=删除任务；任务号为 0xFF 且启停标志为 0 表示全部任务删除
    （此时周期返回 0）。紧凑帧（11e2 开头）布局：data[16]=任务号，
    data[17] 最低位=启停标志，data[18]=周期。
    """
    content = dedup_key(line)
    if TASK_CONFIG_RE.fullmatch(content) is None:
        return None
    try:
        data = bytes.fromhex(content)
    except ValueError:
        return None
    if len(data) < 17:
        return None
    switch_flag = data[17] & 0x01
    if data[16] == 0xFF:
        if switch_flag == 0:
            return 0xFF, 0, 0
        return None
    if len(data) < 19:
        return None
    return data[16], data[18], switch_flag


def parse_full_f231_frame(line: str) -> tuple[int, int, int] | None:
    """解析完整 1376.2 帧中的 11H_F231 采集任务配置，返回与 parse_task_config 相同的三元组。

    帧布局参考 gw13762.c rx_13762_mclt_task_cfg / mclt_logic.c mclt_task_cfg_parse：
    data[10]=AFN(0x11)，data[11:13]=F231（DT1=0x40，DT2=0x1C），
    载荷 data[13]=任务号、data[14]=启停标志、data[16]=周期分钟；
    任务号 0xFF 且启停标志 0 表示全部任务删除（周期返回 0）。
    """
    content = dedup_key(line)
    if not re.fullmatch(r"[0-9a-fA-F]+", content):
        return None
    try:
        data = bytes.fromhex(content)
    except ValueError:
        return None
    if (
        len(data) < 17
        or data[0] != 0x68
        or data[3] & 0x80
        or data[10] != 0x11
        or data[11] != 0x40
        or data[12] != 0x1C
    ):
        return None
    task_id = data[13]
    switch_flag = data[14]
    if task_id == 0xFF:
        return (0xFF, 0, 0) if switch_flag == 0 else None
    return task_id, data[16], switch_flag & 0x01


def parse_task_config_any(line: str) -> tuple[int, int, int] | None:
    """兼容紧凑 11e2 与完整 11H_F231 两种配置帧格式的统一入口。"""
    config = parse_task_config(line)
    if config is not None:
        return config
    return parse_full_f231_frame(line)


def parse_f232_frame(frame: bytes) -> tuple[int, tuple[str, ...]] | None:
    """解析 11H_F232 采集任务关联档案配置帧，返回 (任务号, 档案地址列表)。"""
    if (
        len(frame) < 16
        or frame[3] != 0x43
        or frame[10] != 0x11
        or frame[11] != 0x80
        or frame[12] != 0x1C
    ):
        return None
    task_id = frame[13]
    if task_id == 0xFF:
        return None
    count = frame[14] | (frame[15] << 8)
    if count == 0 or len(frame) < 16 + count * 6:
        return None
    addresses = tuple(
        frame[16 + i * 6 : 22 + i * 6][::-1].hex().upper()
        for i in range(count)
    )
    return task_id, addresses


def has_data_region(data: bytes, scene: str) -> bool:
    """判断数据区（冻结时刻之后的 cnt+datalen 区域）是否有非空数据。

    11e3 被动上报帧的数据区为 fz 之后的 3 字节（data[24:27]）；
    11e4 主动上报帧的数据区为 fz 之后的 3 字节（data[27:30]）。
    任一字节非零即视为有数据。
    """
    if scene == SCENE_ACTIVE_REPORT:
        return len(data) >= 30 and any(data[27:30])
    if scene == SCENE_PASSIVE_REPORT:
        return len(data) >= 27 and any(data[24:27])
    return False


def classify_11e3_scene(read_frame: tuple[str, int, str, bytes, int | None]) -> str:
    """将解析后的 11e3 帧方向映射到场景：下发→被动采集，上报→被动上报。"""
    return SCENE_PASSIVE_REPORT if read_frame[0] == "reply" else SCENE_PASSIVE_COLLECT
