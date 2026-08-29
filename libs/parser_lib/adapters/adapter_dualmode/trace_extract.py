"""4-3 应用层追踪提取件（需求 0009 通信流追踪）。

输入 app_id + APP_RAW（DLL summary 的 APP_RAW 十六进制字节）+ 源 TEI，输出通信流
配对所需的三件套：报文序号 / 请求目标地址序列 / 应答表地址+应答状态。

字节布局 = 04 蒸馏文档位域（小端位序），并经真机样本 2276 帧回归固化
（reqs/0009-listener-flow-trace/samples/，DESIGN §10.1）：

- 通用头 APP_RAW[0:4] = 端口(1B) + 报文ID(2B LE) + 控制字(1B)。
- 抄表族 0x0001/0x0002/0x0003 业务报文头 8 字节：
  [0] 版本(6b)+报文头长度(6b)；下行 [1] 高 4bit=配置字（bit0 未应答重试、
  bit1 否认重试、bit2~3 最大重试次数）；[2] 低 4bit=规约类型，[2] 高 4bit+[3]
  =转发数据长度(12b)；[4:6]=报文序号(LE16)；下行 [6]=设备超时(100ms)、[7]=选项字，
  上行 [6:8]=报文应答位图(LE16，bit i=第 i 报文有应答)；DATA 自报文头长度（缺省 8）。
- 0x00A1/0x0020：[0] 版本+头长度，[1] bit4=方向、bit5=启动/确认，[2:4]=序号(LE16)。
- 0x0008：[1] bit4=方向，[4:6]=序号，[6:12]=电能表地址(6B)。

方向判定以 MAC 层源 TEI 为准（SRC=='001' 即 CCO 下行）；业务头方向位仅作无
SRC 输入时的兜底。目标/应答表地址：
- 规约 1/2（DL/T645）：DATA 内嵌 645 帧地址域（6B BCD 小端，反序显示），
  控制码 bit6=1 判否认。
- 规约 3（DL/T698.45）：DATA 内嵌 698 帧 APDU 的 OAD 条目列表（5B 条目
  ``00 XX YY ZZ 00``，末条目分隔符可省略）；OI 语义后置，以稳定 4B token 为
  对账键（请求/响应回显一致，样本回归通过）。
"""
from dataclasses import dataclass, field

CCO_TEI = "001"

# 抄表族（业务头同构，专用解析）
_METER_FAMILY = (0x0001, 0x0002, 0x0003)

# 非抄表族的序号布局表：msg_id -> (业务报文内序号偏移, 序号字节数, 方向位字节, 方向位位)
# 00A1/0020 序号在业务报文 [2:4]；0008/0011 在 [4:6]/[4:8]（04 文档 §4.2/4.4/4.6/4.8）。
_SIMPLE_LAYOUTS = {
    0x0008: (4, 2, 1, 4),
    0x0011: (4, 4, 1, 4),
    0x0020: (2, 2, 1, 4),
    0x00A1: (2, 2, 1, 4),
}

_PROTO_NAMES = {0: "透明传输", 1: "DL/T645-1997", 2: "DL/T645-2007", 3: "DL/T698.45"}


@dataclass
class TraceExtract:
    """单帧应用层追踪三件套。"""

    app_port: str = ""
    app_id: str = ""
    msg_seq: int | None = None
    direction: str = ""          # down / up / ""（无法判定）
    proto_type: int | None = None
    proto_name: str = ""
    timeout_ms: int | None = None       # 下行设备超时
    retry_cfg: int | None = None        # 下行配置字 4bit（bit0 未应答重试 bit1 否认重试 bit2~3 最大重试）
    resp_bitmap: int | None = None      # 上行报文应答位图（0x0003）
    confirm: bool | None = None         # 0x0020 确认位（True 确认 / False 否认）
    targets: list[str] = field(default_factory=list)    # 请求目标地址序列
    responses: list[dict] = field(default_factory=list) # 应答 [{"addr","denied"}]
    payload_error: str | None = None


def extract_trace_fields(app_id, app_raw, src_tei=None) -> TraceExtract | None:
    """提取一帧的追踪三件套；非追踪关注 ID 或载荷不足返回 None/空结果。

    app_id: 十六进制字符串（如 "0003"）或 int；app_raw: bytes；src_tei: MAC 源
    TEI（"001"=CCO），用于方向判定。
    """
    if app_raw is None:
        return None
    if isinstance(app_id, str):
        try:
            msg_id = int(app_id, 16)
        except ValueError:
            return None
    else:
        msg_id = int(app_id)
    if len(app_raw) < 4:
        return None

    ext = TraceExtract(
        app_port=f"{app_raw[0]:02X}",
        app_id=f"{msg_id:04X}",
    )
    business = app_raw[4:]
    if src_tei:
        ext.direction = "down" if str(src_tei).upper() == CCO_TEI else "up"

    if msg_id in _METER_FAMILY:
        _extract_meter(ext, business, msg_id)
    elif msg_id in _SIMPLE_LAYOUTS:
        _extract_simple(ext, business, msg_id)
    else:
        return None
    return ext


def _extract_meter(ext: TraceExtract, business: bytes, msg_id: int) -> None:
    """抄表族：seq / 配置字 / 应答位图 / DATA 递归 645·698 地址。"""
    if len(business) < 8:
        ext.payload_error = "业务报文过短（<8B）"
        return
    header_len = (business[0] >> 6) | ((business[1] & 0x0F) << 2)
    config = (business[1] >> 4) & 0x0F
    proto_type = business[2] & 0x0F
    fwd_len = (business[3] << 4) | (business[2] >> 4)
    ext.proto_type = proto_type
    ext.proto_name = _PROTO_NAMES.get(proto_type, f"保留({proto_type})")
    ext.msg_seq = business[4] | (business[5] << 8)
    if ext.direction != "up":
        # 下行头：配置字 + 设备超时 + 选项字
        ext.retry_cfg = config
        ext.timeout_ms = business[6] * 100
    else:
        # 上行头：[6:8] = 报文应答位图（bit i = 第 i 报文有应答，最大 16 报文）
        ext.resp_bitmap = business[6] | (business[7] << 8)

    if header_len == 0 or header_len > len(business):
        # 报文头长度缺失/异常时按 04 文档抄表头固定 8 字节兜底
        data = business[8:] if header_len > len(business) else business[header_len:]
        if header_len > len(business):
            ext.payload_error = f"报文头长度({header_len})超出业务报文"
    else:
        data = business[header_len:]
    if fwd_len and len(data) > fwd_len:
        data = data[:fwd_len]
    if not data:
        return

    if proto_type in (1, 2):
        for addr, ctrl, denied in _scan_645(data):
            if ext.direction == "up":
                ext.responses.append({"addr": addr, "denied": denied})
            else:
                ext.targets.append(addr)
    elif proto_type == 3:
        tokens = _scan_698_tokens(data)
        if ext.direction == "up":
            # 应答位图=0：STA 明确报告无应答（帧在、数据无）；None 视为有应答
            answered = ext.resp_bitmap is None or ext.resp_bitmap != 0
            if answered:
                for item in tokens:
                    ext.responses.append({"addr": item, "denied": False})
        else:
            ext.targets.extend(tokens)
    # 规约 0 透明传输：无结构化地址，targets 留空


def _extract_simple(ext: TraceExtract, business: bytes, msg_id: int) -> None:
    """00A1/0020/0008/0011：固定布局 seq + 方向位 +（0008）表地址。"""
    seq_off, seq_size, dir_byte, dir_bit = _SIMPLE_LAYOUTS[msg_id]
    if len(business) < seq_off + seq_size:
        ext.payload_error = "业务报文过短"
        return
    ext.msg_seq = int.from_bytes(business[seq_off:seq_off + seq_size], "little")
    if not ext.direction and len(business) > dir_byte:
        ext.direction = "up" if (business[dir_byte] >> dir_bit) & 1 else "down"
    if msg_id == 0x0020 and len(business) > 1:
        ext.confirm = bool((business[1] >> 5) & 1)
    if msg_id == 0x0008 and len(business) >= 12:
        if ext.direction == "up":
            ext.responses.append({"addr": business[6:12][::-1].hex().upper(), "denied": False})
        else:
            ext.targets.append(business[6:12][::-1].hex().upper())


def ack_peer_tei(frame_raw: bytes) -> int | None:
    """ACK（链路层选择确认）帧的被确认 STA 端 TEI。

    MAC 头字节 [27..28]（12bit：低 nibble 字节高半部 + 下一字节）= 被确认帧的
    STA 端 TEI——确认下行帧时 = 该下行 DST，确认上行帧时 = 该上行 SRC。
    校准记录：DESIGN §10.1（DLL DST=001 子集 16/16 闭环，匹配时距≈0ms）。
    DLL 输出的 ACK DST 字段不可靠（含 D800 等非 TEI 值），不要用它归属。
    frame_raw 须含首部 7E 定界符（与 frames.raw_hex 一致）。过短返回 None。
    """
    if len(frame_raw) < 29:
        return None
    return ((frame_raw[27] & 0x0F) << 8) | frame_raw[28]


def _scan_645(data: bytes):
    """扫描 DATA 中的 DL/T645 帧（容忍 FE 前导）。

    返回 (地址显示[BCD 反序], 控制码, 是否否认)。否认 = 控制码 bit7=1（从站）
    且 bit6=1（异常标志）。
    """
    out = []
    i = 0
    n = len(data)
    while i + 11 <= n:
        if data[i] != 0x68:
            i += 1
            continue
        length = data[i + 8]
        end = i + length + 11
        if length > 200 or end > n or data[end - 1] != 0x16:
            i += 1
            continue
        addr = data[i + 1:i + 7]
        ctrl = data[i + 7]
        denied = (ctrl & 0xC0) == 0xC0
        out.append((addr[::-1].hex().upper(), ctrl, denied))
        i = end
    return out


def _scan_698_tokens(data: bytes) -> list[str]:
    """提取 DATA 内嵌 698.45 帧的表计身份 token（请求 OAD / OAD 条目列表）。

    APDU 安全头固定结构（真机样本回归，DESIGN §10.1）：
        [安全模式 10/90][00][内层长 L][tag][choice][OAD 4B]…
        tag: 0x05=请求 / 0x85=响应；choice: 0x01/0x02=单 OAD（Normal/Next）、
        0x03=WithList（OAD 为固定任务对象，身份 = 其后的 OAD 条目列表）。

    条目 = 5 字节 ``00 XX YY ZZ 00``（末条目分隔符可省略），token = 前 4 字节
    大写 hex；XX/ZZ 非零为样本观测约束（排除 RSD 时间戳/记录区误扫）。
    """
    start = _find_698_apdu(data)
    # 找不到完整信封（截断/非标帧）时把整段当 APDU 处理，仍可按固定偏移取 OAD
    apdu = data[start:] if start is not None else data
    if len(apdu) >= 9 and apdu[3] in (0x05, 0x85) and apdu[4] != 0x03:
        # 单 OAD 形态：请求/响应身份即 tag+choice 之后的 4B OAD
        return [apdu[5:9].hex().upper()]
    return _scan_oad_entries(apdu)


def _scan_oad_entries(apdu: bytes) -> list[str]:
    """扫描 APDU 的 OAD 条目列表（WithList 形态），返回最长条目 run。

    条目区先于响应记录区回显，记录区可能出现零散相似字节；
    取最长 run（步进 5 连续）排除记录区噪声。
    """
    runs: list[list] = []
    current: list = []
    i = 0
    n = len(apdu)
    while i + 4 <= n:
        shaped = (
            apdu[i] == 0x00 and apdu[i + 1] != 0x00 and apdu[i + 3] != 0x00
        )
        if shaped and i + 4 < n and apdu[i + 4] == 0x00:
            current.append((i, apdu[i:i + 4].hex().upper()))
            i += 5
            continue
        if shaped and current and i == current[-1][0] + 5:
            # 末条目分隔符省略形态：紧接当前 run，闭合 run（其后不可能再续）
            current.append((i, apdu[i:i + 4].hex().upper()))
            runs.append(current)
            current = []
            i += 5
            continue
        if current:
            runs.append(current)
            current = []
        i += 1
    if current:
        runs.append(current)
    if not runs:
        return []
    best = max(runs, key=len)
    return [token for _, token in best]


def _find_698_apdu(data: bytes) -> int | None:
    """定位 DATA 内 698.45 帧的 APDU 起点（68 + 长度2B + 控制 + 地址域 + HCS 2B）。

    地址域：SA 长度字节（bit0~5=长度 N，地址 N+1 字节）+ SA + CA(1B)。
    容忍 FE 前导；校验失败返回 None。
    """
    i = 0
    n = len(data)
    while i + 10 <= n:
        if data[i] != 0x68:
            i += 1
            continue
        sa_len = data[i + 4] & 0x3F
        apdu_start = i + 5 + (sa_len + 1) + 1 + 2  # 68+L2+C | SA | CA | HCS2
        if apdu_start < n:
            return apdu_start
        return None
    return None
