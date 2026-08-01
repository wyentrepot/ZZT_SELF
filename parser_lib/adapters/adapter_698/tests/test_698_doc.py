"""DL/T 698.45 蒸馏文档示例帧测试。

来源：蒸馏文档/02_DLT69845_帧格式.md
每个测试用例对应文档中的一个示例帧或APDU片段，验证适配器解析结果与文档描述是否一致。
使用 build_frame() 将APDU片段封装为完整链路层帧（自动计算HCS/FCS）。
"""
import os

import pytest

from parser_lib.core.metadata import MetadataStore
from parser_lib.adapters.adapter_698 import DLT69845Adapter, build_frame

HERE = os.path.dirname(__file__)

# 698 文档示例使用的 SA 地址（登录帧示例）
_SA = bytes([0x05, 0x07, 0x09, 0x19, 0x05, 0x16, 0x20])
# 安全帧示例使用的 SA 地址
_SA_SEC = bytes([0x05, 0x01, 0x00, 0x29, 0x01, 0x16, 0x20])


@pytest.fixture
def adapter():
    store = MetadataStore()
    store.load_protocol("698.45", os.path.join(HERE, "..", "metadata"))
    return DLT69845Adapter(metadata_store=store)


def _field(frame, name):
    for f in frame.fields:
        if f.name == name:
            return f
    return None


def _item(frame, name):
    for it in frame.items:
        if it.name == name:
            return it
    return None


# ========== §2.1 登录请求帧 ==========

def test_doc_698_login_request(adapter):
    """§2.1 登录请求帧（LINK-Request）。

    文档hex: 68 1E 00 81 05 07 09 19 05 16 20 00 CS CS 01 00 00 00 B4 07 E0 05 13 04 08 05 00 00 A4 CS CS 16
    APDU: 01 00 00 00 B4 07 E0 05 13 04 08 05 00 00 A4
    - 01 = LINK-Request
    - 00 00 00 = PIID + 时间
    - B4 = 预连接类型
    - 07 E0 05 13 04 08 05 00 00 A4 = 预连接数据
    """
    apdu = bytes.fromhex("01000000B407E0051304080500 00A4".replace(" ", ""))
    raw = build_frame(apdu, _SA, ca=0x00, control=0x81)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "控制域C").value == "0x81"
    assert _field(fr, "服务器地址SA").value == "05070919051620"
    assert _field(fr, "APDU类型").value == "LINK-Request"
    assert _item(fr, "APDU数据") is not None


# ========== §3.2 GetRequestNormal（读通信地址） ==========

def test_doc_698_get_request_normal(adapter):
    """§3.2 读取通信地址请求（GetRequestNormal）。

    APDU: 05 01 01 40 01 02 00 00
    - 05 = GET-Request
    - 01 = GetRequestNormal
    - 01 = PIID
    - 40 01 02 00 = OAD（通信地址40010200）
    - 00 = 无时间标签
    """
    apdu = bytes.fromhex("05010140 01020000".replace(" ", ""))
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "GET-Request"
    assert "GetRequestNormal" in _field(fr, "请求类型").value
    assert _field(fr, "PIID").value == 0x01
    item = _item(fr, "通信地址")
    assert item is not None, "OAD 40010200 应解析为「通信地址」"


# ========== §3.3 GetResponseNormal（读通信地址响应） ==========

def test_doc_698_get_response_normal(adapter):
    """§3.3 读取通信地址响应（GetResponseNormal）。

    APDU: 85 01 01 40 01 02 00 01 09 06 12 34 56 78 90 12 00 00
    - 85 = GET-Response
    - 01 = GetResponseNormal
    - 01 = PIID-ACD
    - 40 01 02 00 = OAD
    - 01 = Data
    - 09 = octet-string
    - 06 = SIZE(6)
    - 12 34 56 78 90 12 = 通信地址123456789012
    - 00 = FollowReport=0
    - 00 = 无时间标签
    """
    apdu = bytes.fromhex("85010140 01020001 09061234 56789012 0000".replace(" ", ""))
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "GET-Response"
    assert _field(fr, "响应类型").value == "0x01 (GetResponseNormal)"
    assert _field(fr, "PIID-ACD").value == 1
    item = _item(fr, "通信地址")
    assert item is not None, "OAD 40010200 应解析为「通信地址」"
    assert item.value == "123456789012"


# ========== §3.4 SetRequestNormal（设置时钟） ==========

def test_doc_698_set_request_normal(adapter):
    """§3.4 设置时钟请求（SetRequestNormal）。

    APDU: 06 01 02 40 00 02 00 1C 07 E0 01 14 10 1B 0B 00
    - 06 = SET-Request
    - 01 = SetRequestNormal
    - 02 = PIID
    - 40 00 02 00 = OAD（时钟40000200）
    - 1C = date_time_s
    - 07 E0 01 14 10 1B 0B = 2016-01-20 16:27:11
    - 00 = 无时间标签
    """
    apdu = bytes.fromhex("06010240 0002001C 07E00114 101B0B00".replace(" ", ""))
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "SET-Request"
    assert "SetRequestNormal" in _field(fr, "请求类型").value
    assert _field(fr, "PIID").value == 0x02
    item = _item(fr, "时钟")
    assert item is not None, "OAD 40000200 应解析为「时钟」"
    assert item.value == "2016-01-20 16:27:11"


# ========== §3.5 SetResponseNormal（设置时钟响应） ==========

def test_doc_698_set_response_normal(adapter):
    """§3.5 设置时钟响应（SetResponseNormal）。

    APDU: 86 01 02 40 00 02 00 00 00 00
    - 86 = SET-Response
    - 01 = SetResponseNormal
    - 02 = PIID-ACD
    - 40 00 02 00 = OAD
    - 00 = DAR（0=成功）
    - 00 = FollowReport=0
    - 00 = 无时间标签
    """
    apdu = bytes.fromhex("86010240 00020000 0000".replace(" ", ""))
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "SET-Response"
    assert "SetResponseNormal" in _field(fr, "响应类型").value


# ========== §3.6 ActionRequest（电能量复位） ==========

def test_doc_698_action_request(adapter):
    """§3.6 电能量复位请求（ActionRequest）。

    APDU: 07 01 05 00 10 01 00 0F 00 00
    - 07 = ACTION-Request
    - 01 = ActionRequest
    - 05 = PIID
    - 00 10 01 00 = OMD
    - 0F 00 = 参数Data（integer=0）
    - 00 = 无时间标签
    """
    apdu = bytes.fromhex("07010500 1001000F 0000".replace(" ", ""))
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "ACTION-Request"
    assert _field(fr, "PIID").value == 0x05


# ========== §3.7 ActionResponse（电能量复位响应） ==========

def test_doc_698_action_response(adapter):
    """§3.7 电能量复位响应（ActionResponseNormal）。

    APDU: 87 01 05 00 10 01 00 00 00 00 00
    - 87 = ACTION-Response
    - 01 = ActionResponseNormal
    - 05 = PIID-ACD
    - 00 10 01 00 = OMD
    - 00 = DAR（0=成功）
    - 00 = Data OPTIONAL=0
    - 00 = FollowReport=0
    - 00 = 无时间标签
    """
    apdu = bytes.fromhex("87010500 10010000 000000".replace(" ", ""))
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "ACTION-Response"


# ========== §3.8 ProxyRequest（代理读取2个电能表） ==========

def test_doc_698_proxy_request(adapter):
    """§3.8 代理读取2个电能表的当前电能量（ProxyGetRequestList）。

    APDU: 09 01 0A 00 78 02 07 05 20 16 01 20 00 01 00 3C 01 00 10 02 00 07 05 20 16 01 20 00 02 00 3C 01 00 10 02 00 00
    - 09 = PROXY-Request
    - 01 = ProxyGetRequestList
    - 0A = PIID
    - 00 78 = 超时时间
    - 02 = 2个目标服务器
    - TSA1 + 超时 + OAD个数 + OAD
    - TSA2 + 超时 + OAD个数 + OAD
    - 00 = 无时间标签
    """
    apdu = bytes.fromhex(
        "09010A007802070520160120000100"
        "3C01001002000705201601200002"
        "003C0100100200 00".replace(" ", "")
    )
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "PROXY-Request"
    assert _field(fr, "PIID").value == 0x0A


# ========== §3.10 Security请求帧（明文+MAC） ==========

def test_doc_698_security_request(adapter):
    """§3.10 安全请求帧（SECURITY-Request，明文+MAC）。

    文档hex: 68 L L 43 05 01 00 29 01 16 20 0A HCS_L HCS_H 10 00 08 05 01 01 40 01 02 00 00 00 85 01 02 03 06 12 34 56 78 90 12 04 12 34 56 78 FCS_L FCS_H 16
    APDU: 10 00 08 05 01 01 40 01 02 00 00 00 85 01 02 03 06 12 34 56 78 90 12 04 12 34 56 78
    - 10 = SECURITY-Request
    - 00 = 明文
    - 08 = 明文长度
    - 05 01 01 40 01 02 00 00 = 明文APDU（GET-Request 通信地址）
    - 00 = SID_MAC
    - 85 01 02 03 = 标识
    - 06 12 34 56 78 90 12 = 附加数据
    - 04 = MAC长度
    - 12 34 56 78 = MAC
    """
    apdu = bytes.fromhex(
        "10000805010140 0102000085"
        "010203061234567890120412 345678".replace(" ", "")
    )
    raw = build_frame(apdu, _SA_SEC, ca=0x0A, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "SECURITY-Request"
    assert _field(fr, "应用数据类型").value == "明文APDU"
    assert _field(fr, "内层APDU长度").value == 8


# ========== §3.10 Security响应帧（明文+MAC） ==========

def test_doc_698_security_response(adapter):
    """§3.10 安全响应帧（SECURITY-Response，明文+MAC）。

    文档hex: 68 L L C3 05 01 00 29 01 16 20 0A HCS_L HCS_H 90 00 11 85 01 01 40 01 02 00 01 09 06 20 16 01 29 00 01 00 01 00 04 12 34 56 78 FCS_L FCS_H 16
    APDU: 90 00 11 85 01 01 40 01 02 00 01 09 06 20 16 01 29 00 01 00 00 01 00 04 12 34 56 78
    - 90 = SECURITY-Response
    - 00 = 明文
    - 11 = 明文长度=17
    - 85 01 01 40 01 02 00 01 09 06 20 16 01 29 00 01 00 00 = 明文APDU（GET-Response）
    - 01 = 含数据验证信息
    - 00 = [0] MAC
    - 04 = MAC长度
    - 12 34 56 78 = MAC
    """
    apdu = bytes.fromhex(
        "90001185010140 0102000109062016"
        "012900010001000412 345678".replace(" ", "")
    )
    raw = build_frame(apdu, _SA_SEC, ca=0x0A, control=0xC3)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "SECURITY-Response"
    assert _field(fr, "应用数据类型").value == "明文APDU"
    assert _field(fr, "内层APDU长度").value == 17
    # 内层APDU应为GET-Response
    assert _field(fr, "内层APDU类型").value == "GET-Response"
    # MAC
    mac_field = _field(fr, "数据验证信息")
    assert mac_field is not None and "12345678" in (mac_field.desc or "")


# ========== §4.4 date-time-s 数据类型 ==========

def test_doc_698_date_time_s(adapter):
    """§4.4 date_time_s 数据类型验证。

    文档hex: 1C 07 E0 01 14 10 1B 0B
    - 1C = 类型标记28：date_time_s
    - 07 E0 = year=2016
    - 01 = month=1
    - 14 = day=20
    - 10 = hour=16
    - 1B = minute=27
    - 0B = second=11
    即 2016-01-20 16:27:11

    通过 SET-Request 封装该数据类型进行测试。
    """
    apdu = bytes.fromhex("06010240 0002001C 07E00114 101B0B00".replace(" ", ""))
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    item = _item(fr, "时钟")
    assert item is not None, "OAD 40000200 应解析为「时钟」"
    assert item.value == "2016-01-20 16:27:11"


# ========== §4.5 RCSD 数据类型 ==========

def test_doc_698_rcsd_example(adapter):
    """§4.5 RCSD 数据类型示例。

    文档hex: 02 00 20 21 02 00 00 00 10 02 00
    - 02 = RCSD个数=2
    - 00 20 21 02 00 = [0] OAD：第1列
    - 00 00 10 02 00 = [0] OAD：第2列

    通过 GetRequestRecord 封装测试。
    """
    # 构造一个包含RCSD的GetRequestRecord
    # 05 03 = GET-Request / Record
    # PIID = 01
    # OAD = 50020200 (分钟冻结)
    # RSD = 00 (NULL, 不选择)
    # RCSD = 02 00 20 21 02 00 00 00 10 02 00
    # timeTag = 00
    apdu = bytes.fromhex(
        "0503" "01"              # GET-Request / Record, PIID=0x01
        "50020200"               # OAD = 分钟冻结
        "00"                     # RSD = NULL
        "02"                     # RCSD count = 2
        "00" "20210200"          # CSD[0] OAD
        "00" "00100200"          # CSD[1] OAD
        "00"                     # timeTag = 0
    )
    raw = build_frame(apdu, _SA, ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "GET-Request"
    assert "GetRequestRecord" in _field(fr, "请求类型").value
    assert _field(fr, "RCSD对象个数").value == 2
