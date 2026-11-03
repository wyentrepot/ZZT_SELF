"""698/645 分钟采集 11E3/06H-F230 帧解析（REQS-0037 重庆 STA 台架口径）。

帧来源与布局依据（只读引用，不改动源工程）：
- STA 侧 11H-00E3 应答头：`H_STA/01/sta/protocol/aps/src/aps_stack.c`
  `_collect_task_read_struct` + `aps_stack.h` `mclt_data_up_t`（CCO 侧同构）。
- CCO 侧 06H-F230 上报帧：`H_CCO/001/cco/protocol/aps/src/aps_mclt_state.c`
  `rpt_nodeDataSend` + `gw13762_framerpt_struct`（本地 1376.2 帧，AFN=0x06/F230）。
- 内层 698 构帧：`H_STA/01/sta/protocol/aps/src/aps_mclt_state.c`
  `more_frame_698_ack_struct`（C=0xC3、APDU 85 02 01 di_cnt、缺项 FF、9 字节尾）。
- 内层 645 构帧：同文件 `more_frame_645_ack_struct` + `dlt645.c dlt645_ack_struct`
  （一 DI 一帧、缺项 0x32 满长度）。

输入统一为原始帧 bytes：
- 11E3 下行/上行：APS 域（11 E3 00 00 ...），即 1376.2 用户数据区去掉链路头。
  证据文档里的 `11e30000c1...` 为 CCO/STA 日志摘要格式（外层无 HCS/FCS），
  解析时按 27 字节外层 + 内层帧处理，不做链路层校验。
- 06H-F230 / 11H-F231/F232：完整 1376.2 本地帧（68 L L ctrl ... cs 16），
  解析时校验 CS（字节 [2] 起求和取低 8 位）。
- 内层 698：68 L L C ... FCS 16（FCS-16/PPP，初值 0xFFFF，取反输出）。
- 内层 645：68 A0..A5 68 C L DI0..DIn CS 16（CS = 前 10+L 字节求和取低 8 位）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .timeutil import bcd

#: 1376.2 本地帧帧头/帧尾
GW_START = 0x68
GW_END = 0x16
#: 11H-00E3 采集任务数据读取（APS 业务 ID）
CMD_11E3 = 0xE3
#: 06H-F230 采集任务数据上报（AFN=0x06, DT=20 1C）
AFN_06 = 0x06
DT_F230 = (0x20, 0x1C)
#: 11H-F231/F232（AFN=0x11, DT=40 1C / 80 1C）
AFN_11 = 0x11
DT_F231 = (0x40, 0x1C)
DT_F232 = (0x80, 0x1C)
#: 698 协议类型 / 645 协议类型（mclt_data_up_t.procotol 字段）
PROTOCOL_698 = 3
PROTOCOL_645 = 2

#: 11E3 上行外层头长度（ver/hlen(2) + ssn(4) + 协议(1) + src(6) + task_id(1) + fz(6) + di_cnt(1) + bufflen(2)）
E3_UP_HEAD_LEN = 23
#: 11E3 下行外层头长度（同上去掉 result，di_cnt/bufflen 换成无）→ 实测 24 字节含头两字
E3_DOWN_HEAD_LEN = 24
#: 698 固定尾长度：00 00 01 00 04 + 4 字节 TimeTag（大端秒）
MCLT_698_TAIL_LEN = 9


def _sum_cs(data: bytes) -> int:
    """1376.2 本地帧 CS：指定字节求和取低 8 位（STA common_func.c buff_sum_cal）。"""
    return sum(data) & 0xFF


def gw13762_cs_ok(frame: bytes) -> bool:
    """校验 1376.2 本地帧 CS（含 68 头、CS、16 尾的完整帧）。

    CS 为 frame[2]（长度低字节）起至 CS 前一字节的求和取低 8 位，
    与 gw13762_framerpt_struct 的 `txbuf[length-2] += txbuf[4+i]`（从 [2] 累加
    到 [length-3]）等价——长度域高字节为 0 时两种写法一致。
    长度域兼容两种口径：L=帧总长（CCO gw13762_framerpt_struct，F230 实测）
    或 L=帧总长-2（STA _make_698_frame 风格）。
    """
    if len(frame) < 5 or frame[0] != GW_START or frame[-1] != GW_END:
        return False
    length = frame[1] | (frame[2] << 8)
    if length not in (len(frame) - 2, len(frame)):
        return False
    return _sum_cs(frame[2:-2]) == frame[-2]


def bcd6_time_bytes(raw: bytes) -> str:
    """6 字节 BCD（秒分时日月年）转 `YYYY-MM-DD HH:MM:SS` 可读文本。"""
    if len(raw) != 6:
        raise ValueError(f"fz_time 需要 6 字节，实际 {len(raw)}")
    return (
        f"20{bcd(raw[5]):02d}-{bcd(raw[4]):02d}-{bcd(raw[3]):02d} "
        f"{bcd(raw[2]):02d}:{bcd(raw[1]):02d}:{bcd(raw[0]):02d}"
    )


@dataclass(frozen=True)
class E3UpFrame:
    """11H-00E3 上行（STA→CCO 应答）外层字段。

    布局（aps_stack.h mclt_data_up_t，域前 2 字节 ver/hlen/dir/rsv）：
    [0:2]=01 E3 头字? 实为 APS 业务帧以 `11 E3 00 00` 开头，
    [4]=0xC1（ver=1/hlen=5/dir=1 组合字低字节）、[5]=0x15、[6:10]=ssn(LE32)，
    [10] 高 3 位=result 低 3 位=协议、[12:18]=src 地址、
    [18]=task_id、[19:25]=fz_time、[25]=di_cnt、[26:28]=bufflen、[28:]=内层帧。
    """

    task_id: int
    protocol: int
    result: int
    src: str
    fz_time: bytes
    di_cnt: int
    bufflen: int
    ssn: int
    payload: bytes

    @property
    def fz_time_text(self) -> str:
        return bcd6_time_bytes(self.fz_time)


def parse_11e3_up(frame: bytes) -> E3UpFrame | None:
    """解析 11E3 上行外层（APS 域 bytes，即 `11e3...` 日志摘要的 bytes 形态）。

    成功返回 E3UpFrame，载荷区域（task_id 之后不足 27 字节、或 result 非法
    帧头不符）返回 None。本函数不校验 HCS/FCS：日志摘要格式外层无校验域。
    """
    if len(frame) < 27 or frame[0] != 0x11 or frame[1] != CMD_11E3:
        return None
    head_word = frame[4] << 8 | frame[5]  # 0xC115：dir=1
    if not head_word & 0x8000:
        return None
    ssn = int.from_bytes(frame[6:10], "little")
    result = (frame[10] >> 5) & 0x07
    protocol = frame[10] & 0x07
    # src 地址序口径：线上为 STA aps_addrmap_state.c mac_revert 后的倒置序
    # （线上即倒置），此处 [::-1] 反转为人类可读正序（显示序）。
    # 注意：与 sim_concentrator/mclt_e3.py、parser_lib adapter_dualmode 的
    # 线上序口径不同（二者保留线上倒置序不反转），跨模块比对 src/帧时注意。
    src = frame[11:17][::-1].hex().upper()
    task_id = frame[17]
    fz_time = frame[18:24]
    di_cnt = frame[24]
    bufflen = frame[25] | (frame[26] << 8)
    payload = frame[27:]
    if len(payload) < bufflen:
        return None
    payload = payload[:bufflen]
    return E3UpFrame(
        task_id=task_id,
        protocol=protocol,
        result=result,
        src=src,
        fz_time=fz_time,
        di_cnt=di_cnt,
        bufflen=bufflen,
        ssn=ssn,
        payload=payload,
    )


@dataclass(frozen=True)
class E3DownFrame:
    """11H-00E3 下行（CCO→STA 请求）外层字段。"""

    task_id: int
    protocol: int
    dst: str
    fz_time: bytes
    ssn: int

    @property
    def fz_time_text(self) -> str:
        return bcd6_time_bytes(self.fz_time)


def parse_11e3_down(frame: bytes) -> E3DownFrame | None:
    """解析 11E3 下行请求（24 字节 APS 域）。格式不符返回 None。"""
    if len(frame) != 24 or frame[0] != 0x11 or frame[1] != CMD_11E3:
        return None
    head_word = frame[4] << 8 | frame[5]  # 0x0105：dir=0
    if head_word & 0x8000:
        return None
    return E3DownFrame(
        task_id=frame[17],
        protocol=frame[10] & 0x07,
        dst=frame[11:17][::-1].hex().upper(),
        fz_time=frame[18:24],
        ssn=int.from_bytes(frame[6:10], "little"),
    )


@dataclass(frozen=True)
class OadItem:
    """GET-Response 中单个 OAD 项（OAD + choice + data）。

    线上布局（STA 构帧 more_frame_698_ack_struct / 单测 mclt_698_frame_test）：
    OAD(4, 大端) + 0x01(响应选择) + data(配置长度)——长度不上线，由任务配置决定。
    data 全 0xFF 判缺项（REQS-0037 口径：缺项按配置长度写 FF）。
    """

    oad: int
    oad_hex: str
    data: bytes

    @property
    def is_missing(self) -> bool:
        return bool(self.data) and all(b == 0xFF for b in self.data)


@dataclass(frozen=True)
class Inner698Frame:
    """内层 698 帧（68..68）解析结果。"""

    control: int
    oad_count: int
    oads: tuple[OadItem, ...]
    apdu_len_field: int
    apdu_actual_len: int
    apdu_len_has_tail: bool
    tail: bytes
    time_tag: bytes
    total_len: int
    hcs_ok: bool
    fcs_ok: bool

    @property
    def time_tag_text(self) -> str:
        """TimeTag（大端秒）转 `YYYY-MM-DD HH:MM:SS`。"""
        return secs_to_text(
            int.from_bytes(self.time_tag, "big")
        )


_EPOCH_2000 = 946684800  # 2000-01-01 00:00:00 UTC，STA 秒即 2000 纪元（台架口径）


def secs_to_text(sec: int) -> str:
    """TimeTag 秒转 `YYYY-MM-DD HH:MM:SS`。

    TimeTag 为 1970 纪元秒（Unix time），台架本地时间口径直接换算
    （不做时区/闰秒修正，与台架比对口径一致）。
    """
    days, rem = divmod(sec, 86400)
    # 自纪元天数转公历日期（Howard Hinnant 算法，z=0 对应 1970-01-01）
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    year = y + (1 if m <= 2 else 0)
    hour, rem = divmod(rem, 3600)
    minute, second = divmod(rem, 60)
    return f"{year:04d}-{m:02d}-{d:02d} {hour:02d}:{minute:02d}:{second:02d}"


def _ppp_fcs16(data: bytes) -> int:
    """FCS-16（PPP，反射多项式 0x8408，初值 0xFFFF，取反输出）。

    与 STA dlt698.c `_pppfcs16/_dlt698_fcs16` 一致。
    """
    fcs = 0xFFFF
    for byte in data:
        fcs = (fcs >> 8) ^ _FCS16_TAB[(fcs ^ byte) & 0xFF]
    return fcs ^ 0xFFFF


def _build_fcs16_tab() -> tuple[int, ...]:
    """生成 FCS-16 反射表（多项式 0x8408）。"""
    tab = []
    for i in range(256):
        v = i
        for _ in range(8):
            v = (v >> 1) ^ 0x8408 if v & 1 else v >> 1
        tab.append(v)
    return tuple(tab)


_FCS16_TAB = _build_fcs16_tab()


def find_inner_frame(payload: bytes) -> tuple[bytes, int] | None:
    """从 11E3 上行 payload（或 06H-F230 数据区）中定位第一段 68..68 帧。

    返回 (帧 bytes, 起始偏移)，找不到返回 None。帧长度按 698 长度域
    （L+2）与 645 长度域（11+L）分别尝试，取能落到 0x16 结尾者。
    """
    start = 0
    while True:
        pos = payload.find(b"\x68", start)
        if pos < 0:
            return None
        rest = payload[pos:]
        for total in _candidate_frame_len(rest):
            if total and len(rest) >= total and rest[total - 1] == GW_END:
                return rest[:total], pos
        start = pos + 1


def _candidate_frame_len(rest: bytes) -> tuple[int, ...]:
    """按 698（L+2）与 645（12+L，L 在 [9]）两种长度域给出候选总长。"""
    if len(rest) < 3:
        return ()
    L = rest[1] | (rest[2] << 8)
    if rest[7] == 0x68 and len(rest) > 9:
        # 645：第二个 68 在 [7]，长度域在 [9]，总长 = 12 + L
        return (L + 2, 12 + rest[9])
    return (L + 2,)


def split_inner_frames(payload: bytes) -> tuple[bytes, ...]:
    """切分 payload 中连续的 68..68 内层帧（645 一 DI 一帧 / 698 单帧）。"""
    frames = []
    offset = 0
    while offset < len(payload):
        found = find_inner_frame(payload[offset:])
        if found is None:
            break
        frame, pos = found
        frames.append(frame)
        offset += pos + len(frame)
    return tuple(frames)


def parse_inner_698(
    frame: bytes, data_lens: tuple[int, ...] | None = None
) -> Inner698Frame | None:
    """解析内层 698 帧（完整 68 L L C ... FCS 16）。

    校验/提取：C 域、APDU 头（85 02 01 di_cnt）、OAD 逐项（data 全 FF 判缺）、
    APDU 长度字段值与实际长度（含 9 字节尾口径）、HCS/FCS。非 698 帧或
    长度非法返回 None。

    OAD 项 = OAD(4) + choice(0x01) + data(配置长度)，长度不上线：
    - data_lens 给出逐项配置长度时按其切分（断言路径用）；
    - 省略时按“各项等长”推断：len(body)/oad_count - 5（台架帧各项配置长度一致）。
    """
    if len(frame) < 24 or frame[0] != GW_START:
        return None
    total = (frame[1] | (frame[2] << 8)) + 2
    if total != len(frame) or frame[total - 1] != GW_END:
        return None
    # 上行应答 C 域固定 0xC3（REQS-0037）；0x43 为下行请求帧
    control = frame[3]
    hcs = frame[12] | (frame[13] << 8)
    hcs_ok = _ppp_fcs16(frame[1:12]) == hcs
    fcs = frame[total - 3] | (frame[total - 2] << 8)
    fcs_ok = _ppp_fcs16(frame[1 : total - 3]) == fcs

    # SECURITY 域：90 00 + 1 字节内层 APDU 长度（含 9 字节尾）+ APDU
    sec = frame[14:17]
    if sec[0] != 0x90 or sec[1] != 0x00:
        return None
    apdu_len_field = sec[2]
    apdu = frame[17 : 17 + apdu_len_field]
    if len(apdu) != apdu_len_field:
        return None
    # APDU 实际长度 = APDU 数据区（去掉 9 字节固定尾）；长度字段按含尾口径
    apdu_actual_len = max(apdu_len_field - MCLT_698_TAIL_LEN, 0)
    apdu_len_has_tail = apdu_len_field >= MCLT_698_TAIL_LEN
    if len(apdu) < 4 or apdu[0] != 0x85 or apdu[1] != 0x02 or apdu[2] != 0x01:
        return None
    oad_count = apdu[3]
    tail = apdu[-MCLT_698_TAIL_LEN:]
    time_tag = tail[5:9]
    body = apdu[4:-MCLT_698_TAIL_LEN]
    if data_lens is None:
        if oad_count == 0 or len(body) % oad_count:
            return None
        item_len = len(body) // oad_count
        if item_len < 5:
            return None
        data_lens = (item_len - 5,) * oad_count
    if len(data_lens) != oad_count:
        return None
    oads: list[OadItem] = []
    pos = 0
    for data_len in data_lens:
        if pos + 5 + data_len > len(body) or body[pos + 4] != 0x01:
            # choice 字节不在期望位置：按“长度域漏 9 字节尾”重切一次，
            # 尾随的 9 字节里 00 00 01 00 04 前缀可帮助判定真实 APDU 边界。
            return None
        oad = int.from_bytes(body[pos : pos + 4], "big")
        data = body[pos + 5 : pos + 5 + data_len]
        oads.append(OadItem(oad=oad, oad_hex=f"{oad:08X}", data=data))
        pos += 5 + data_len
    if pos != len(body):
        return None
    return Inner698Frame(
        control=control,
        oad_count=oad_count,
        oads=tuple(oads),
        apdu_len_field=apdu_len_field,
        apdu_actual_len=apdu_actual_len,
        apdu_len_has_tail=apdu_len_has_tail,
        tail=tail,
        time_tag=time_tag,
        total_len=total,
        hcs_ok=hcs_ok,
        fcs_ok=fcs_ok,
    )


@dataclass(frozen=True)
class Inner645Frame:
    """内层 645 帧（一 DI 一帧）解析结果。"""

    address: str
    control: int
    data_len: int
    di_wire: str
    di_display: str
    data: bytes
    cs_ok: bool

    @property
    def is_32_fill(self) -> bool:
        """数据区全 0x32 视为缺项填充（645 缺项按满长度 0x32 上报）。"""
        return bool(self.data) and all(b == 0x32 for b in self.data)


def _di_wire_to_display(wire: bytes) -> str:
    """645 DI 线上字节（低字节在前 +0x33）转显示序。"""
    return bytes((b - 0x33) & 0xFF for b in wire)[::-1].hex().upper()


def parse_inner_645(frame: bytes) -> Inner645Frame | None:
    """解析内层 645 帧（68 A0..A5 68 C L DI DATA CS 16）。

    L = 4 + DATA 长度（DI 4 字节计入），总长 = 12 + L。
    CS 口径与 STA dlt645.c `dlt645_ack_struct` + `buff_sum_cal` 一致：
    CS = 前 10+L 字节求和取低 8 位（即帧头 [0] 起至数据区最后一字节）。
    """
    if len(frame) < 16 or frame[0] != GW_START or frame[7] != GW_START:
        return None
    if frame[-1] != GW_END:
        return None
    control = frame[8]
    data_len = frame[9]
    if data_len < 4 or len(frame) != 12 + data_len:
        return None
    di_wire = frame[10:14]
    data = frame[14 : 14 + data_len - 4]
    cs_ok = _sum_cs(frame[: 10 + data_len]) == frame[10 + data_len]
    return Inner645Frame(
        address=frame[1:7].hex().upper(),
        control=control,
        data_len=data_len,
        di_wire=di_wire.hex().upper(),
        di_display=_di_wire_to_display(di_wire),
        data=data,
        cs_ok=cs_ok,
    )


@dataclass(frozen=True)
class F230Report:
    """06H-F230 采集任务数据上报（完整 1376.2 本地帧）解析结果。"""

    task_id: int
    address: str
    protocol: int
    fz_time: bytes
    datalen: int
    inner: bytes
    cs_ok: bool

    @property
    def fz_time_text(self) -> str:
        return bcd6_time_bytes(self.fz_time)


def parse_f230_report(frame: bytes) -> F230Report | None:
    """解析 06H-F230 上报帧（AFN=0x06、DT=20 1C）。

    载荷（本地帧 [13:]）：task_id(1) + addr(6) + protocol(1) + fz_time(6)
    + datalen(2,LE) + inner(datalen)。非 F230 帧或 CS 错误返回 None。
    """
    if len(frame) < 32 or frame[0] != GW_START or frame[-1] != GW_END:
        return None
    if frame[10] != AFN_06 or tuple(frame[11:13]) != DT_F230:
        return None
    if not gw13762_cs_ok(frame):
        return None
    payload = frame[13:]
    task_id = payload[0]
    address = payload[1:7].hex().upper()
    protocol = payload[7]
    fz_time = payload[8:14]
    datalen = payload[14] | (payload[15] << 8)
    inner = payload[16 : 16 + datalen]
    if len(inner) != datalen:
        return None
    return F230Report(
        task_id=task_id,
        address=address,
        protocol=protocol,
        fz_time=fz_time,
        datalen=datalen,
        inner=inner,
        cs_ok=True,
    )


@dataclass(frozen=True)
class F231Config:
    """11H-F231 采集任务配置帧解析结果（用于核对任务配置与断言期望）。"""

    task_id: int
    switch_flag: int
    period_min: int
    di_cnt: int
    di_displays: tuple[str, ...]
    di_lens: tuple[int, ...]


def parse_f231_config(frame: bytes) -> F231Config | None:
    """解析 11H-F231 任务配置帧（AFN=0x11、DT=40 1C，F231 发送帧）。

    载荷（本地帧 [13:]，与 CCO mclt_logic.c mclt_task_cfg_parse +
    gw13762.c rx_13762_mclt_task_cfg 单相段一致）：
    task_id(1) + swi(1) + protocol(1) + period_min(1) + rsv(1)
    + single_cnt(1) + nlen(2) + single_cnt × (DI 线上 4B + len(1))。
    DI 线上字节序为低字节在前（如显示序 02010100 → 线上 00010102）。
    仅支持单相段（台架场景）；含三相段时返回 None。
    """
    if len(frame) < 24 or frame[0] != GW_START or frame[-1] != GW_END:
        return None
    if frame[10] != AFN_11 or tuple(frame[11:13]) != DT_F231:
        return None
    if not gw13762_cs_ok(frame):
        return None
    payload = frame[13:]
    if len(payload) < 8:
        return None
    task_id = payload[0]
    switch_flag = payload[1] & 0x01
    period_min = payload[3]
    single_cnt = payload[5]
    if single_cnt == 0:
        return None  # 三相段/空配置不支持（台架单相场景）
    items_end = 8 + single_cnt * 5
    if len(payload) < items_end:
        return None
    di_displays: list[str] = []
    di_lens: list[int] = []
    for i in range(single_cnt):
        item = payload[8 + i * 5 : 13 + i * 5]
        di_displays.append(item[0:4][::-1].hex().upper())
        di_lens.append(item[4])
    return F231Config(
        task_id=task_id,
        switch_flag=switch_flag,
        period_min=period_min,
        di_cnt=single_cnt,
        di_displays=tuple(di_displays),
        di_lens=tuple(di_lens),
    )


# ---------------------------------------------------------------------------
# 断言：11E3 → 内层 698 → 06H-F230 全链路口径（REQS-0037 / QUOK-17 修复验收）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Expect698:
    """11E3 内层 698 断言期望（来自任务配置与请求时刻）。

    oad_lens：按配置顺序给出每个 OAD 的数据配置长度（用于切分 APDU 数据区）；
    oads：期望的 OAD 列表（大端整数），为 None 时跳过 OAD 值比对；
    missing：期望缺项的 OAD 集合（OAD int），None 时由 data 全 FF 自行判定；
    time_tag_text：期望 TimeTag 可读时间（冻结时刻），None 时跳过。
    """

    task_id: int
    fz_time_text: str
    oad_lens: tuple[int, ...]
    oads: tuple[int, ...] | None = None
    missing: tuple[int, ...] | None = None
    time_tag_text: str | None = None
    protocol: int = PROTOCOL_698


@dataclass(frozen=True)
class AssertReport:
    """断言结果：passed + 差异明细（每条含位置、期望 vs 实际）。"""

    passed: bool
    failures: tuple[str, ...]


def assert_698_11e3_chain(
    frame_11e3_up: bytes,
    expect: Expect698,
    frame_11e3_down: bytes | None = None,
    frame_f230: bytes | None = None,
) -> AssertReport:
    """断言 11E3 上行帧（含内层 698）符合 REQS-0037 修复口径，可级联断言
    下行请求与 06H-F230 上报。

    校验项（失败逐条记入 failures，不抛出）：
    1. 外层：task_id、协议类型、result=0、di_cnt、bufflen、冻结时刻、
       载荷与 bufflen 自洽；
    2. （给下行时）down.task_id/协议/冻结时刻 与 up 一致；
    3. 内层 698：68..68 定位、C=0xC3、APDU 头 85 02 01 di_cnt、
       逐 OAD（值/数据/缺项 FF）、APDU 长度字段含 9 字节尾、
       HCS/FCS、固定尾、TimeTag=冻结时刻；
    4. （给 F230 时）任务号/协议/地址/冻结时刻/datalen/内层一致。

    返回 AssertReport(passed, failures)；全部通过时 failures 为空。
    """
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    up = parse_11e3_up(frame_11e3_up)
    if up is None:
        return AssertReport(False, ("11E3 上行帧无法解析（帧头/长度不符）",))

    # 1. 外层口径
    if up.task_id != expect.task_id:
        fail(f"外层 task_id: 期望 {expect.task_id} 实际 {up.task_id}")
    if up.protocol != expect.protocol:
        fail(f"外层 protocol: 期望 {expect.protocol} 实际 {up.protocol}")
    if up.fz_time_text != expect.fz_time_text:
        fail(f"外层 冻结时刻: 期望 {expect.fz_time_text} 实际 {up.fz_time_text}")

    # 2. 下行请求（可选）
    if frame_11e3_down is not None:
        down = parse_11e3_down(frame_11e3_down)
        if down is None:
            fail("11E3 下行帧无法解析（帧头/长度不符）")
        else:
            if down.task_id != expect.task_id:
                fail(f"下行 task_id: 期望 {expect.task_id} 实际 {down.task_id}")
            if down.protocol != expect.protocol:
                fail(f"下行 protocol: 期望 {expect.protocol} 实际 {down.protocol}")
            if down.fz_time_text != expect.fz_time_text:
                fail(
                    f"下行 冻结时刻: 期望 {expect.fz_time_text} 实际 {down.fz_time_text}"
                )

    # 3. 失败响应（ret_data_len=0）与成功帧分叉
    if up.result != 0 or up.bufflen == 0:
        if expect.oad_lens:
            # 期望成功拿到数据，却是失败响应/空数据
            fail(
                f"期望成功帧但外层失败响应: result={up.result} bufflen={up.bufflen}"
            )
        elif up.result != 0:
            pass  # 失败响应对“无数据期望”是合法口径，result 数值不另行强校验
        if up.di_cnt != 0:
            fail(f"失败响应 di_cnt: 期望 0 实际 {up.di_cnt}")
        if up.payload:
            fail(f"失败响应 payload: 期望空 实际 {up.payload.hex().upper()}")
        if frame_f230 is not None:
            fail("失败响应不应携带 06H-F230 断言（无内层帧）")
        return AssertReport(not failures, tuple(failures))
    if not expect.oad_lens:
        fail("期望缺少 oad_lens，无法校验成功帧内层 698")
        return AssertReport(False, tuple(failures))
    if up.di_cnt != len(expect.oad_lens):
        fail(f"外层 di_cnt: 期望 {len(expect.oad_lens)} 实际 {up.di_cnt}")
    if up.bufflen != len(up.payload):
        fail(f"外层 bufflen: 期望 {len(up.payload)} 实际 {up.bufflen}")

    # 4. 内层 698 帧
    found = find_inner_frame(up.payload)
    if found is None:
        fail("payload 中未找到内层 68..68 帧")
        return AssertReport(False, tuple(failures))
    inner, inner_off = found
    if inner_off != 0:
        fail(f"内层帧偏移: 期望 0 实际 {inner_off}")
    f698 = parse_inner_698(inner, expect.oad_lens)
    if f698 is None:
        fail("内层 698 帧解析失败（长度/SEC 头/APDU 头/OAD 结构不符）")
        # 定向诊断：APDU 头可辨认时给出逐字节差异（如长度漏尾）
        _diagnose_698(inner, expect, fail)
        return AssertReport(False, tuple(failures))

    if f698.control != 0xC3:
        fail(f"内层 C 域: 期望 0xC3 实际 {f698.control:#04x}")
    if not f698.hcs_ok:
        fail(f"内层 HCS 校验失败: 帧内 {inner[12] | (inner[13] << 8):#06x}")
    if not f698.fcs_ok:
        fail(f"内层 FCS 校验失败: 帧内 {inner[-3] | (inner[-2] << 8):#06x}")
    if f698.oad_count != len(expect.oad_lens):
        fail(f"APDU OAD 个数: 期望 {len(expect.oad_lens)} 实际 {f698.oad_count}")

    # APDU 长度：4(头) + Σ(5+len) + 9(尾)，字段必须含尾
    apdu_expect = 4 + sum(5 + n for n in expect.oad_lens) + MCLT_698_TAIL_LEN
    if f698.apdu_len_field != apdu_expect:
        fail(f"APDU 长度字段: 期望 {apdu_expect}（含 9 字节尾）实际 {f698.apdu_len_field}")
    if not f698.apdu_len_has_tail:
        fail(f"APDU 长度字段未含 9 字节尾: 实际 {f698.apdu_len_field}")

    # 逐 OAD
    for idx, item in enumerate(f698.oads):
        prefix = f"OAD[{idx}] {item.oad_hex}"
        if expect.oads is not None and idx < len(expect.oads):
            if item.oad != expect.oads[idx]:
                fail(f"{prefix} OAD 值: 期望 {expect.oads[idx]:08X} 实际 {item.oad_hex}")
        want_missing = (
            expect.missing is not None
            and idx < len(expect.missing)
            and expect.missing[idx]
        )
        if want_missing and not item.is_missing:
            fail(f"{prefix} 缺项数据: 期望全 FF 实际 {item.data.hex().upper()}")
        if not want_missing and item.is_missing:
            fail(f"{prefix} 数据: 期望非缺项 实际全 FF {item.data.hex().upper()}")
        if len(item.data) != expect.oad_lens[idx]:
            fail(
                f"{prefix} 数据长度: 期望 {expect.oad_lens[idx]} "
                f"实际 {len(item.data)}"
            )

    # 固定尾与 TimeTag
    if f698.tail[:5] != b"\x00\x00\x01\x00\x04":
        fail(f"内层 固定尾前缀: 期望 0000010004 实际 {f698.tail[:5].hex().upper()}")
    if expect.time_tag_text is not None and f698.time_tag_text != expect.time_tag_text:
        fail(
            f"内层 TimeTag: 期望 {expect.time_tag_text} 实际 {f698.time_tag_text}"
        )

    # 5. 06H-F230（可选）
    if frame_f230 is not None:
        f230 = parse_f230_report(frame_f230)
        if f230 is None:
            fail("06H-F230 帧无法解析（AFN/DT/CS 不符）")
            f230_raw = frame_f230
            if (
                len(f230_raw) > 30
                and f230_raw[10] == AFN_06
                and tuple(f230_raw[11:13]) == DT_F230
            ):
                # CS 修复重算后仍可读时给出任务号差异（台架日志常有摘录残缺）
                if _sum_cs(f230_raw[2:-2]) != f230_raw[-2]:
                    fail("F230 CS 校验失败")
                f230_task = f230_raw[13]
                if f230_task != expect.task_id:
                    fail(
                        f"F230 任务号: 期望 {expect.task_id} 实际 {f230_task}"
                    )
        else:
            if f230.task_id != expect.task_id:
                fail(f"F230 任务号: 期望 {expect.task_id} 实际 {f230.task_id}")
            if f230.protocol != expect.protocol:
                fail(f"F230 协议: 期望 {expect.protocol} 实际 {f230.protocol}")
            if f230.fz_time_text != expect.fz_time_text:
                fail(
                    f"F230 冻结时刻: 期望 {expect.fz_time_text} "
                    f"实际 {f230.fz_time_text}"
                )
            if f230.datalen != up.bufflen:
                fail(f"F230 datalen: 期望 {up.bufflen} 实际 {f230.datalen}")
            if f230.inner != up.payload:
                fail("F230 内层与 11E3 payload 不一致")

    return AssertReport(not failures, tuple(failures))


def _diagnose_698(
    inner: bytes, expect: Expect698, fail: "list.append"
) -> None:
    """内层 698 解析失败时的定向诊断：APDU 头/长度字段逐字节比对。"""
    if len(inner) < 17:
        return
    sec = inner[14:17]
    if sec[0] != 0x90 or sec[1] != 0x00:
        fail(f"内层 SEC 头: 期望 9000 实际 {sec[:2].hex().upper()}")
        return
    apdu_len_field = sec[2]
    apdu_expect = 4 + sum(5 + n for n in expect.oad_lens) + MCLT_698_TAIL_LEN
    if apdu_len_field != apdu_expect:
        fail(
            f"APDU 长度字段: 期望 {apdu_expect}（含 9 字节尾）实际 {apdu_len_field}"
            f"{'（疑似漏算 9 字节尾）' if apdu_len_field == apdu_expect - MCLT_698_TAIL_LEN else ''}"
        )
    apdu = inner[17 : 17 + apdu_len_field] if len(inner) >= 17 + apdu_len_field else inner[17:]
    if len(apdu) >= 4:
        head = apdu[:4]
        if head[0] != 0x85 or head[1] != 0x02:
            fail(f"APDU 头: 期望 85 02 实际 {head[:2].hex().upper()}")
        elif head[2] != 0x01:
            fail(
                f"APDU 第三字节(PIID-ACD): 期望 0x01 实际 {head[2]:#04x}"
                "（任务号不属于 698 APDU）"
            )
        elif head[3] != len(expect.oad_lens):
            fail(f"APDU OAD 个数: 期望 {len(expect.oad_lens)} 实际 {head[3]}")


def require_698_11e3_chain(
    frame_11e3_up: bytes,
    expect: Expect698,
    frame_11e3_down: bytes | None = None,
    frame_f230: bytes | None = None,
) -> AssertReport:
    """assert_698_11e3_chain 的抛错形态：FAIL 时抛 AssertionError（含全部差异）。"""
    report = assert_698_11e3_chain(frame_11e3_up, expect, frame_11e3_down, frame_f230)
    if not report.passed:
        raise AssertionError("; ".join(report.failures))
    return report
