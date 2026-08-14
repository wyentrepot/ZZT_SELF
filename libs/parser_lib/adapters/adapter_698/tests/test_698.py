"""DL/T 698.45 面向对象数据交换协议 单元测试（结构化 golden 断言）。"""
import os

import pytest

from parser_lib.core.metadata import MetadataStore
from parser_lib.adapters.adapter_698 import DLT69845Adapter, build_frame

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures")


@pytest.fixture
def adapter():
    # 带上字典，使 OAD 能解析出语义名（贴近真实运行）
    store = MetadataStore()
    store.load_protocol("698.45", os.path.join(HERE, "..", "metadata"))
    return DLT69845Adapter(metadata_store=store)


def _load(name):
    return bytes.fromhex(open(os.path.join(FIX, name)).read().strip())


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


def test_login_frame_decode(adapter):
    """H.1.1 登录帧：链路层 + LINK-Request APDU。"""
    fr = adapter.decode(_load("login_req.hex"))
    assert fr.structure == "698.45"
    assert _field(fr, "控制域C").value == "0x81"
    assert _field(fr, "服务器地址SA").value == "05070919051620"
    assert _field(fr, "APDU类型").value == "LINK-Request"
    assert _item(fr, "APDU数据") is not None


def test_get_response_normal(adapter):
    """H.3.1 读取通信地址响应：OAD=40010200 → 通信地址，值=123456789012。"""
    fr = adapter.decode(_load("get_comm_addr_rsp.hex"))
    assert fr.structure == "698.45"
    assert _field(fr, "APDU类型").value == "GET-Response"
    assert _field(fr, "响应类型").value == "0x01 (GetResponseNormal)"
    assert _field(fr, "PIID-ACD").value == 1
    item = _item(fr, "通信地址")
    assert item is not None, "OAD 40010200 应解析为「通信地址」"
    assert item.value == "123456789012"


def test_get_response_normal_list(adapter):
    """H.3.2 读取三相电压电流响应：2 个 OAD，数组值正确。"""
    fr = adapter.decode(_load("get_vi_rsp.hex"))
    assert fr.structure == "698.45"
    assert _field(fr, "响应类型").value == "0x02 (GetResponseNormalList)"
    assert _field(fr, "OAD个数").value == 2
    v = _item(fr, "电压")
    assert v is not None and v.value == [2413, 2413, 2413] and v.unit == "V"
    i = _item(fr, "电流")
    assert i is not None and i.value == [1000, 1000, 1000] and i.unit == "A"


def test_confidence(adapter):
    assert adapter.confidence(_load("login_req.hex")) == 1.0
    # 1376.2 帧在第 3 字节也是 0x68，698 必须拒绝（隔离）
    assert adapter.confidence(b"\x68" * 20) != 1.0
    assert adapter.confidence(b"\x00" * 20) == 0.0


def test_try_extract(adapter):
    r = adapter.try_extract(_load("login_req.hex"))
    assert r is not None


def test_multi_frames(adapter):
    from parser_lib.core.splitter import FrameSplitter
    sp = FrameSplitter([adapter])
    raw = _load("login_req.hex") + _load("get_comm_addr_rsp.hex")
    frames = sp.feed(raw)
    assert len(frames) >= 1


def test_get_response_array_voltage_oad_20000201(adapter):
    """IMA 参考固件样例 OAD=20000201（A相计量电压，三相数组）。

    来源：IMA 知识库「645/698协议」test.txt 中 fill_mclt_data_legacy 默认 OAD。
    验证字典回填（M7 #6）后，该 OAD 能解析出「A相计量电压」语义且数组值正确。
    """
    # GetResponseNormal: 85 01 PIID | OAD(4)=20 00 02 01 | [1]Data = array(3)×long-unsigned(2413)
    apdu = bytes([0x85, 0x01, 0x01,
                  0x20, 0x00, 0x02, 0x01,
                  0x01, 0x01, 0x03,
                  0x12, 0x09, 0x6D, 0x12, 0x09, 0x6D, 0x12, 0x09, 0x6D])
    raw = build_frame(apdu, bytes([0x05, 0x07, 0x09, 0x19, 0x05, 0x16, 0x20]),
                      ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    item = _item(fr, "A相计量电压")
    assert item is not None, "OAD 20000201 应解析为「A相计量电压」（字典回填）"
    assert item.value == [2413, 2413, 2413], "三相电压数组值应为 [2413, 2413, 2413]（单位 V）"
    assert item.unit == "V"


def test_get_response_array_current_oad_20010201(adapter):
    """IMA 参考固件样例 OAD=20010201（A相计量电流，三相数组）。

    与 test.txt 中 电流 A 默认 OAD 一致，验证字典回填后电流语义与数组解析。
    """
    # array(3)×long-unsigned(1000=0x03E8)
    apdu = bytes([0x85, 0x01, 0x01,
                  0x20, 0x01, 0x02, 0x01,
                  0x01, 0x01, 0x03,
                  0x12, 0x03, 0xE8, 0x12, 0x03, 0xE8, 0x12, 0x03, 0xE8])
    raw = build_frame(apdu, bytes([0x05, 0x07, 0x09, 0x19, 0x05, 0x16, 0x20]),
                      ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    item = _item(fr, "A相计量电流")
    assert item is not None, "OAD 20010201 应解析为「A相计量电流」（字典回填）"
    assert item.value == [1000, 1000, 1000]
    assert item.unit == "A"


def test_backfilled_oad_lookup_count(adapter):
    """冒烟校验：oad.json 已回填至约 40 条基础对象（M7 #6 首批）。"""
    store = adapter.metadata_store
    # 抽样若干回填项，确认均能被字典命中
    for oad in ["20000201", "20010201", "20020100", "30000300",
                "60010000", "60010100", "F1000000", "40010400"]:
        assert store.lookup("698.45", oad) is not None, f"oad.json 缺失回填项 {oad}"


def test_security_apdu_recognized_no_warning(adapter):
    """M7 #2：APDU 服务类型 0x10/0x90 = SECURITY（IMA §6.3.4.4）。

    占 79MB 压测日志 99.97% 的帧；此前因不在 _APDU_NAMES 而全帧告警。
    补全后不应再产生「未识别」warning，且 APDU 类型应给出权威语义名。
    """
    for tag, name in ((0x10, "SECURITY-Request"), (0x90, "SECURITY-Response")):
        apdu = bytes([tag, 0x01, 0x02, 0x03])
        raw = build_frame(apdu, bytes([0x05, 0x07, 0x09, 0x19, 0x05, 0x16, 0x20]),
                          ca=0x01, control=0x43)
        fr = adapter.decode(raw)
        assert fr.structure == "698.45"
        assert _field(fr, "APDU类型").value == name, f"APDU 类型应识别为 {name}"
        assert not any("未识别" in w for w in fr.warnings), \
            f"{name} 不应产生「未识别」warning，实际 warnings={fr.warnings}"
        assert _item(fr, "APDU数据") is not None, f"{name} 应记录原始 APDU 数据"


def test_wrong_report_proxy_tags_now_unrecognized(adapter):
    """回归护栏：旧字典错把 REPORT/PROXY 写在 0x0A/0x0B/0x8A/0x8B，已按 IMA §6.3.4.2/55 修正。

    修正后这些字节在 698.45 规范中无对应服务类型（正确值应为 0x08/0x88/0x09/0x89），
    因此应被标记为「未识别」，而非沿用错误的 REPORT/PROXY 命名。
    """
    for tag in (0x0A, 0x0B, 0x8A, 0x8B):
        apdu = bytes([tag, 0x01, 0x02, 0x03])
        raw = build_frame(apdu, bytes([0x05, 0x07, 0x09, 0x19, 0x05, 0x16, 0x20]),
                          ca=0x01, control=0x43)
        fr = adapter.decode(raw)
        assert any("未识别" in w for w in fr.warnings), \
            f"规范无服务类型 0x{tag:02X}，应标记为未识别"


def test_security_response_deep_parse(adapter):
    """SECURITY-Response (0x90) → 内层 GetResponseRecord 深度解析。

    样例帧 = 用户提供的验收标准（134字节）：
    68 8400 C3 05353781090030 10 F183 | 90 006B [85 03 ...] | 0100 04D0C1A502 | 0100 16
    链路层 → SECURITY-Response → 明文内层APDU → GetResponseRecord →
    OAD(分钟冻结) + RCSD(8 CSD) + 1记录×8数据项 + 跟随上报/时间标签(NULL) + MAC
    """
    hex_str = (
        "688400c30535378109003010f18390006b85033750020200080020210200"
        "002001040000200002000020010200002004020000200a02000000100201"
        "000020020101011c07ea061d0e1e00050000000001011208a30101050000"
        "000001020500000000050000000001021003e81003e80600000000060000"
        "00000000010004d0c1a502010016"
    )
    fr = adapter.decode(bytes.fromhex(hex_str))
    assert fr.structure == "698.45"
    assert not fr.warnings, f"不应有警告，实际: {fr.warnings}"

    # 链路层
    sa = _field(fr, "服务器地址SA")
    assert sa is not None and "300009813735" in (sa.desc or "")

    # SECURITY-Response
    assert _field(fr, "APDU类型").value == "SECURITY-Response"
    assert _field(fr, "应用数据类型").value == "明文APDU"
    assert _field(fr, "内层APDU长度").value == 107

    # MAC
    mac_field = _field(fr, "数据验证信息")
    assert mac_field is not None and "D0C1A502" in (mac_field.desc or "")

    # 内层 GetResponseRecord
    assert _field(fr, "内层APDU类型").value == "GET-Response"
    assert "GetResponseRecord" in _field(fr, "响应类型").value
    assert _field(fr, "RCSD对象个数").value == 8

    # 8 个 CSD 的 OAD 中文名
    for i, expected_name in enumerate([
        "数据冻结时间", "电流-零线电流", "电压", "电流",
        "有功功率", "功率因数", "正向有功电能-总", "反向有功电能-总",
    ]):
        csd = _field(fr, f"CSD[{i}]")
        assert csd is not None and expected_name in (csd.desc or ""), \
            f"CSD[{i}] 期望含「{expected_name}」，实际 desc={csd.desc if csd else None}"

    # 数据项验证
    dt = _item(fr, "数据冻结时间")
    assert dt is not None and dt.value == "2026-06-29 14:30:00"

    zero_current = _item(fr, "电流-零线电流")
    assert zero_current is not None and zero_current.value == 0.0

    voltage = _item(fr, "电压")
    assert voltage is not None and voltage.value == [2211]

    power_factor = _item(fr, "功率因数")
    assert power_factor is not None and power_factor.value == [1000, 1000]

    forward_energy = _item(fr, "正向有功电能-总")
    assert forward_energy is not None and forward_energy.value == 0.0

    # 跟随上报 + 时间标签
    assert _field(fr, "跟随上报域").value == "无"
    assert _field(fr, "时间标签域").value == "无"


def test_get_request_record_deep_parse(adapter):
    """GetRequestRecord (req_type=3) 深度解析：PIID + OAD + RSD[1] + RCSD(8 CSD) + timeTag。

    样例来源于 79MB 压测日志中最高频的请求帧（13,190 帧，占 98%）。
    结构：05 03 | PIID | OAD(分钟冻结) | RSD[1]{OAD+date-time-s} | RCSD(8×CSD) | timeTag=0
    62 字节全部解析完毕，0 残留。
    """
    apdu = bytes.fromhex(
        "05" "03" "3B"             # GET-Request / Record, PIID=0x3B
        "50020200"                  # OAD = 分钟冻结
        "01"                        # RSD method = [1] Selector1
        "20210200"                  #   Selector1 OAD = 数据冻结时间
        "1C" "07EA" "06" "1D" "0E" "1E" "00"  #   date-time-s = 2026-06-29 14:30:00
        "08"                        # RCSD count = 8
        "00" "20210200"             #   CSD[0] 数据冻结时间
        "00" "20010400"             #   CSD[1] 电流-零线电流
        "00" "20000200"             #   CSD[2] 电压
        "00" "20010200"             #   CSD[3] 电流
        "00" "20040200"             #   CSD[4] 有功功率
        "00" "200A0200"             #   CSD[5] 功率因数
        "00" "00100201"             #   CSD[6] 正向有功电能-总
        "00" "00200201"             #   CSD[7] 反向有功电能-总
        "00"                        # timeTag = 0 (无)
    )
    raw = build_frame(apdu, bytes([0x05, 0x07, 0x09, 0x19, 0x05, 0x16, 0x20]),
                      ca=0x01, control=0x43)
    fr = adapter.decode(raw)
    assert fr.structure == "698.45"
    assert not fr.warnings, f"不应有警告，实际: {fr.warnings}"

    # APDU 层
    assert _field(fr, "APDU类型").value == "GET-Request"
    assert "GetRequestRecord" in _field(fr, "请求类型").value
    assert _field(fr, "PIID").value == 0x3B

    # OAD (冻结对象) → item
    item = _item(fr, "分钟冻结")
    assert item is not None, "OAD 50020200 应解析为「分钟冻结」"

    # RSD[1] Selector1
    assert _field(fr, "RSD选择方法").value == "指定值(Selector1)"
    rsd_obj = _field(fr, "RSD对象")
    assert rsd_obj is not None and "20210200" in rsd_obj.value
    assert "数据冻结时间" in (rsd_obj.desc or "")
    assert _field(fr, "RSD选择值").value == "2026-06-29 14:30:00"

    # RCSD
    assert _field(fr, "RCSD对象个数").value == 8
    expected_csd_names = [
        "数据冻结时间", "电流-零线电流", "电压", "电流",
        "有功功率", "功率因数", "正向有功电能-总", "反向有功电能-总",
    ]
    for i, name in enumerate(expected_csd_names):
        csd = _field(fr, f"读取列CSD[{i}]")
        assert csd is not None, f"读取列CSD[{i}] 应存在"
        assert name in (csd.desc or ""), f"CSD[{i}] 期望含「{name}」，实际 desc={csd.desc}"

    # 时间标签
    assert _field(fr, "时间标签域").value == "无"
