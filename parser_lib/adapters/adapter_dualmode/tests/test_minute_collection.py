"""分钟采集报文（0x00E2/0x00E3/0x00E4）解析测试。

真实 E4 fixture 取自 `测试文件/测试文本.txt` 第一帧 0x00E4 主动上报的
有界应用层字节（DLL APP_RAW，106 字节）；E2/E3 依据《分钟采集应用帧格式介绍.md》
构造（报文头长度 16/20）。
"""
from parser_lib.adapters.adapter_dualmode import DualMode43Adapter

# 第一帧 0x00E4 主动上报：通用头(4) + 主动上报头(8) + 转发报文(94)
E4_APP_HEX = (
    "11E400000132C40000005E00"
    "013401001412230702005523310726014C00"
    "6834010014122368910633343435A456AF16"
    "683401001412236891063335343532321A16"
    "683401001412236891063336343532321B16"
    "6834010014122368910A33323435A456323232327916"
)


def _field(frame, name):
    for f in frame.fields:
        if f.name == name:
            return f
    return None


def _e2_message():
    """采集任务配置下行：通用头 + 业务头(16) + 数据项。"""
    header = bytes([0x01, 0x04])  # 版本1 + 报文头长度16
    header += bytes.fromhex("01000000")  # 报文序号
    header += bytes.fromhex("000000000000")  # 目的MAC
    header += bytes([7])  # 任务号
    header += bytes([0x05])  # 启动/删除1 + 协议类型2(bit1-3=010) + 表类型0
    header += bytes([5, 1])  # 采集周期5 + 数据项个数1
    item = bytes.fromhex("02010100") + bytes([4])  # 数据项标识 + 回复长度
    return bytes.fromhex("11E20000") + header + item


def _e3_message():
    """采集任务数据读取下行：通用头 + 业务头(20)。"""
    header = bytes([0x01, 0x05])  # 版本1 + 报文头长度20
    header += bytes.fromhex("02000000")  # 报文序号
    header += bytes([0x02])  # 协议类型2
    header += bytes.fromhex("000000000000")  # 目的MAC
    header += bytes([7])  # 任务号
    header += bytes.fromhex("260731070055")  # 冻结时刻
    return bytes.fromhex("11E30000") + header


def test_active_report_decodes_real_e4_bytes():
    frame = DualMode43Adapter().decode(bytes.fromhex(E4_APP_HEX))

    assert frame.structure == "双模4-3"
    assert _field(frame, "报文ID").raw == 0x00E4
    assert "采集任务数据上报" in _field(frame, "报文ID").value
    assert _field(frame, "分钟采集类型").value == "主动上报"
    assert _field(frame, "协议版本号").raw == 1
    assert _field(frame, "报文头长度").raw == 8
    assert _field(frame, "方向").value == "上行"
    assert _field(frame, "启动位").raw == 1
    assert _field(frame, "报文序号").raw == 0x000000C4
    assert _field(frame, "转发报文长度").raw == 94
    assert _field(frame, "任务号").raw == 7
    assert _field(frame, "协议类型").raw == 2
    assert _field(frame, "响应结果").raw == 0
    freeze = _field(frame, "冻结时刻")
    assert freeze.hex == "005523310726"
    assert freeze.value == "2026-07-31 23:55:00"
    assert _field(frame, "上报数量").raw == 1
    assert _field(frame, "数据长度").raw == 76
    assert len(frame.nested) == 4
    assert all(item.structure == "645" for item in frame.nested)


def test_e2_config_message_header_length_16():
    frame = DualMode43Adapter().decode(_e2_message())

    assert _field(frame, "报文ID").raw == 0x00E2
    assert "采集任务配置" in _field(frame, "报文ID").value
    assert _field(frame, "报文头长度").raw == 16
    assert _field(frame, "方向").value == "下行"
    assert _field(frame, "任务号").raw == 7
    assert _field(frame, "协议类型").raw == 2


def test_e3_read_message_header_length_20():
    frame = DualMode43Adapter().decode(_e3_message())

    assert _field(frame, "报文ID").raw == 0x00E3
    assert "采集任务数据读取" in _field(frame, "报文ID").value
    assert _field(frame, "报文头长度").raw == 20
    assert _field(frame, "方向").value == "下行"
    assert _field(frame, "任务号").raw == 7
    assert _field(frame, "协议类型").raw == 2


def test_try_extract_consumes_full_e4_envelope_not_first_nested_frame():
    adapter = DualMode43Adapter()
    result = adapter.try_extract(bytes.fromhex(E4_APP_HEX))

    assert result is not None
    assert result.consumed == len(bytes.fromhex(E4_APP_HEX)) == 106
    assert result.raw == bytes.fromhex(E4_APP_HEX)


def test_concurrent_read_format_start_flag_zero_is_not_expanded():
    """并发抄读格式（启动位=0，无转发报文长度）本期不展开业务字段。

    与 try_extract 的切帧行为一致：decode 走原始业务报文 + 内嵌帧扫描，
    并给出 warning，不产生伪「报文序号/转发报文长度」字段。
    """
    # 通用头 + 并发抄读头（版本1、方向位0、启动位0）
    business = bytes([0x01, 0x08])
    business += bytes.fromhex("03000000")  # 报文序号
    business += bytes([0x20])  # 协议类型2、电表类型0
    raw = bytes.fromhex("11E40000") + business

    frame = DualMode43Adapter().decode(raw)

    assert _field(frame, "报文ID").raw == 0x00E4
    assert _field(frame, "报文序号") is None
    assert _field(frame, "转发报文长度") is None
    assert any("并发抄读格式" in warning for warning in frame.warnings)
    assert any(item.name == "业务报文(原始)" for item in frame.items)
