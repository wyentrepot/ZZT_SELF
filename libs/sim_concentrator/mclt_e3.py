# -*- coding: utf-8 -*-
"""11E3（AFN=11H 业务 ID 0x00E3）分钟采集读数 APS 域构帧/解析（REQS-0037）。

11E3 是 CCO↔STA 载波侧 APS 业务帧（端口 0x11、报文 ID 0x00E3），不是 1376.2
本地口帧。台架证据（BENCH-v5-taskid-RECOVERED.md / bench-645-regression）里的
`11e3...` 摘要即"APS 域裸字节"：

    11 E3 00 00 | ver/hlen/dir 组合字(2B) | ssn(4B LE) | 协议字节(1B) |
    目的地址(6B) | task_id(1B) | fz_time(6B BCD)

冻结时刻 fz_time 6 字节为 **秒-分-时-日-月-年** 低位在前 BCD（CCO
`_mclt_task_data_read_struct` fz_time[0]=sec … fz_time[5]=year%100；STA
`mclt_fill_freeze_time_to_read_info` frame[1]=min、frame[5]=year 交叉印证）。
证据帧 task1 down `...01 00 10 17 02 11 26` 尾 6 字节 = 00s 10min 17h 02日
11月 26年 → 2026-11-02 17:10:00，与 06H-F230 上报帧采集时间域
`00 10 17 02 11 26`（秒分时日月年）逐字节同构。

字段表（24B，对照证据帧 `11e300000105240000000300000000000101001017021126`）：
    [0:2]   11 E3        APS 头：业务端口 0x11 + 报文 ID 0x00E3（BE 序）
    [2:4]   00 00        控制字（aps_head.ctrByte）+ 高位保留
    [4:6]   01 05        ver/hlen/dir 组合字（LE16）：ver=1, hlen=0x14=20, dir=0
    [6:10]  24 00 00 00  ssn 报文序号（LE32；证据=0x24）
    [10]    03           bit0-2 协议类型(2=645/3=698) + bit3-4 表类型 + bit5-7 保留
    [11:17] 00 00 00 00 00 01  目的地址（线上字节序，STA 侧 mac_revert 回显为源）
    [17]    01           task_id（外层任务号；STA 端重庆模式内部映射 0→16）
    [18:24] 00 10 17 02 11 26  fz_time：秒 分 时 日 月 年（BCD，低位在前）

上行应答同头，[4:6]=C1 15（dir=1），[10] 高 3 位 result + 低 3 位协议，
后接 task_id/fz_time 回显 + di_cnt(1B) + bufflen(2B LE) + 内层帧。

仅做 APS 域字节构造/解析，不组装 1376.2 链路帧（本仓库尚无 11E3 的本地口
承载 Fn，见 runner.wait_mclt_stored/read_mclt_11e3 注释）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: APS 业务端口（STACK_COMM）
APS_COMM = 0x11
#: 采集任务数据读取报文 ID
APS_ID_11E3 = 0x00E3
#: 下行外层总长（4B APS 头 + 20B 业务体）
E3_DOWN_LEN = 24
#: 业务体长度（hlen 固定 0x14=20）
E3_BODY_HLEN = 0x14
#: 协议类型
PROTO_645 = 2
PROTO_698 = 3


def bcd2(v: int) -> int:
    """两位十进制 → BCD 字节（00~99）。"""
    if not 0 <= v <= 99:
        raise ValueError(f"BCD 分量越界: {v}")
    return ((v // 10) << 4) | (v % 10)


def unbcd(b: int) -> int:
    """BCD 字节 → int；非法半字节抛错（防脏数据静默错时刻）。"""
    hi, lo = (b >> 4) & 0x0F, b & 0x0F
    if hi > 9 or lo > 9:
        raise ValueError(f"非法 BCD 字节: 0x{b:02X}")
    return hi * 10 + lo


def freeze_time_bytes(freeze_time: str) -> bytes:
    """`YYYY-MM-DD HH:MM:SS`（秒可省，缺省 00）→ 6B 秒分时日月年 BCD。"""
    text = str(freeze_time).strip().replace("/", "-")
    fmt = "%Y-%m-%d %H:%M:%S" if text.count(":") >= 2 else "%Y-%m-%d %H:%M"
    dt = datetime.strptime(text, fmt)
    return bytes([
        bcd2(dt.second), bcd2(dt.minute), bcd2(dt.hour),
        bcd2(dt.day), bcd2(dt.month), bcd2(dt.year % 100),
    ])


def freeze_time_text(raw: bytes) -> str:
    """6B 秒分时日月年 BCD → `YYYY-MM-DD HH:MM:SS`。"""
    if len(raw) != 6:
        raise ValueError(f"fz_time 需要 6 字节，实际 {len(raw)}")
    return (
        f"20{unbcd(raw[5]):02d}-{unbcd(raw[4]):02d}-{unbcd(raw[3]):02d} "
        f"{unbcd(raw[2]):02d}:{unbcd(raw[1]):02d}:{unbcd(raw[0]):02d}"
    )


def build_11e3_down(*, task_id: int, freeze_time: str, protocol: int = PROTO_698,
                    dst: str = "000000000001", meter_type: int = 0,
                    ssn: int = 1, addr_wire: str | None = None) -> bytes:
    """构造 11E3 下行 APS 域 24 字节（字段表见模块 docstring）。

    task_id: 外层任务号（0~15；STA 重庆模式内部 0→TASK_O_ID=16 映射）。
    freeze_time: "YYYY-MM-DD HH:MM[:SS]"，转 6B 秒分时日月年 BCD。
    protocol: 2=645-2007 / 3=698.45。
    dst/addr_wire: 目的地址。线上字节序 = 12 位 BCD 串两位一组直转 hex
        （证据帧 698 task1 与 645 task1 的 STA/表地址均为 000000000001 →
        wire 00 00 00 00 00 01，与 06H-F230 从节点地址域逐字节同构）；
        addr_wire 显式给 hex 时原样使用。
    ssn: 报文序号（CCO 侧 1~255 自增；仅用于日志/回显匹配）。
    """
    if isinstance(task_id, bool) or not isinstance(task_id, int) \
            or not 0 <= task_id <= 0xFF:
        raise ValueError(f"task_id 非法: {task_id!r}（0~255 整数）")
    if protocol not in (PROTO_645, PROTO_698):
        raise ValueError(f"protocol 非法: {protocol}（2=645 / 3=698）")
    fz = freeze_time_bytes(freeze_time)

    if addr_wire is not None:
        wire = bytes.fromhex(addr_wire)
    else:
        text = str(dst)
        if len(text) != 12 or not text.isdigit():
            raise ValueError(f"dst 需 12 位 BCD 串: {dst!r}")
        wire = bytes.fromhex(text)
    if len(wire) != 6:
        raise ValueError(f"目的地址需 6 字节: {dst!r}")

    head_word = 0x0105                              # ver=1, hlen=0x14, dir=0（证据帧 [4:6]=01 05，BE16 原样字节）
    proto_byte = (protocol & 0x07) | ((meter_type & 0x03) << 3)
    out = bytearray()
    out += bytes([APS_COMM, APS_ID_11E3 & 0xFF, (APS_ID_11E3 >> 8) & 0xFF, 0x00])
    out += head_word.to_bytes(2, "big")
    out += (int(ssn) & 0xFFFFFFFF).to_bytes(4, "little")
    out.append(proto_byte)
    out += wire
    out.append(task_id & 0xFF)
    out += fz
    assert len(out) == E3_DOWN_LEN
    return bytes(out)


@dataclass(frozen=True)
class E3Up:
    """11E3 上行外层解析结果（STA→CCO 应答）。"""

    task_id: int
    protocol: int
    result: int
    src_wire: str          # 线上字节序源地址 hex
    fz_time: bytes
    di_cnt: int
    bufflen: int
    ssn: int
    payload: bytes         # 内层 645/698 帧（bufflen 截断）

    @property
    def fz_time_text(self) -> str:
        return freeze_time_text(self.fz_time)

    @property
    def ok(self) -> bool:
        return self.result == 0


def parse_11e3_up(frame: bytes) -> E3Up | None:
    """解析 11E3 上行 APS 域（≥27B，头 4B + 23B 外层 + payload）。

    与台架证据一致：`11e30000c115…`，组合字线上 [4:6]=C1 15（dir=1 在
    [4] 的 bit7，即组合字 BE 读 0xC115）。非 11E3 或头不符返回 None。
    """
    if len(frame) < 27 or frame[0] != APS_COMM or frame[1] != (APS_ID_11E3 & 0xFF):
        return None
    if not frame[4] & 0x80:         # dir 位必须为 1（上行）：证据帧 [4]=0xC1
        return None
    ssn = int.from_bytes(frame[6:10], "little")
    result = (frame[10] >> 5) & 0x07
    protocol = frame[10] & 0x07
    src_wire = frame[11:17].hex()
    task_id = frame[17]
    fz_time = frame[18:24]
    di_cnt = frame[24]
    bufflen = frame[25] | (frame[26] << 8)
    payload = frame[27:27 + bufflen]
    if len(payload) < bufflen:
        return None
    return E3Up(
        task_id=task_id, protocol=protocol, result=result, src_wire=src_wire,
        fz_time=fz_time, di_cnt=di_cnt, bufflen=bufflen, ssn=ssn,
        payload=payload,
    )
