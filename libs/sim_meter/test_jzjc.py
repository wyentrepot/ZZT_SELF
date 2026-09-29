"""重庆即装即采（JZJC）配置与上送报文单元测试。

报文结构对照知识库 协议层/100_重庆即装即采设备信息上送_帧格式.md：
- STA 上报数据子域（事件类型 51）：类型1 + 地址6 + 协议1 + 描述符(1+N) + M + 记录(3+N1+N2)×M
- AFN=06H F7 上行数据单元：n + 地址6 + 协议1 + 序号2 + 描述符(1+N) + M + m + 记录×M
设备类型 4036 取值与描述符前缀对照 协议层/102 卡 §7/§8。
"""
import pytest

from sim_meter.meter import JZJC_TYPE_PRESETS, MeterBrain


class TestJzjcConfig:
    def test_default_preset(self):
        b = MeterBrain()
        jc = b.get_jzjc()
        assert jc["device_type"] == "0x10"
        assert jc["descriptor"] == "DDZY666-Z"
        assert jc["protocol_type"] == 0x02

    def test_change_type_updates_preset_descriptor(self):
        b = MeterBrain()
        b.set_jzjc(device_type="0x12")
        jc = b.get_jzjc()
        assert jc["device_type"] == "0x12"
        assert jc["descriptor"] == "DTSU666-D"            # 预设描述符联动
        assert jc["device_type_name"] == "导轨式电能表"

    def test_change_type_keep_custom_descriptor(self):
        b = MeterBrain()
        b.set_jzjc(device_type="0x28", descriptor="LKT888Z-D")
        jc = b.get_jzjc()
        assert jc["device_type"] == "0x28"
        assert jc["descriptor"] == "LKT888Z-D"            # 显式描述符优先

    def test_protocol_type_validate(self):
        b = MeterBrain()
        with pytest.raises(ValueError):
            b.set_jzjc(protocol_type=0x01)
        b.set_jzjc(protocol_type=0x03)
        assert b.get_jzjc()["protocol_type"] == 0x03

    def test_sub_devices(self):
        b = MeterBrain()
        b.set_jzjc(device_type="0x12", sub_devices=[
            {"addr": "0000000000AA", "descriptor": "DDZY111-Z", "protocol_type": 0x02},
            {"addr": "0000000000BB", "descriptor": "DDZY222-Z", "protocol_type": 0x02},
        ])
        jc = b.get_jzjc()
        assert len(jc["sub_devices"]) == 2
        assert jc["sub_devices"][0]["addr"] == "0000000000AA"
        with pytest.raises(ValueError):
            b.set_jzjc(sub_devices=[{"addr": f"0000000000{i:02d}",
                                     "descriptor": "X"} for i in range(33)])

    def test_presets_cover_kb_types(self):
        # 102 卡 §7/§8 的主要取值都有预设（类型名 + 描述符前缀）
        assert JZJC_TYPE_PRESETS[0x10]["descriptor"].startswith("DDZY")
        assert JZJC_TYPE_PRESETS[0x12]["descriptor"].startswith("DTSU")
        assert JZJC_TYPE_PRESETS[0x13]["descriptor"].startswith("DJS")
        assert JZJC_TYPE_PRESETS[0x28]["descriptor"].startswith("LKT")
        assert JZJC_TYPE_PRESETS[0x29]["descriptor"].startswith("DFDL")


class TestJzjcReportSta:
    def test_structure_no_subdevice(self):
        b = MeterBrain()
        b.set_address("1200000000AA")
        payload = b.build_jzjc_report("sta")
        # 事件类型 51 + 地址6 + 协议1 + 描述符长1 + 描述符9 + M=0
        assert payload[0] == 51
        assert payload[1:7] == bytes.fromhex("1200000000AA")
        assert payload[7] == 0x02                          # DL/T 645-2007
        n = payload[8]
        desc = payload[9:9 + n]
        assert desc == b"DDZY666-Z"
        assert payload[9 + n] == 0                         # 下挂设备数量 M
        assert len(payload) == 10 + n

    def test_structure_with_subdevices(self):
        b = MeterBrain()
        b.set_jzjc(sub_devices=[
            {"addr": "0000000000AA", "descriptor": "DDZY111-Z", "protocol_type": 0x02},
        ])
        payload = b.build_jzjc_report("sta")
        base = 9 + len(b"DDZY666-Z")                       # M 位于 9+N
        assert payload[base] == 1                          # M=1
        rec = payload[base + 1:]
        assert rec[0] == 6                                 # 通信地址长度 N1
        assert rec[1:7] == bytes.fromhex("0000000000AA")
        n2 = rec[7]
        assert rec[8:8 + n2] == b"DDZY111-Z"
        assert rec[8 + n2] == 0x02                         # 下挂设备协议类型


class TestJzjcReport06F7:
    def test_structure(self):
        b = MeterBrain()
        b.set_address("1200000000AA")
        b.set_jzjc(seq=7)
        payload = b.build_jzjc_report("afn06f7")
        # n=1 + 地址6 + 协议1 + 序号2 + 描述符长1 + 描述符9 + M + m
        assert payload[0] == 1                             # 从节点数量 n
        assert payload[1:7] == bytes.fromhex("1200000000AA")
        assert payload[7] == 0x02
        assert payload[8:10] == (7).to_bytes(2, "little")  # 序号
        n = payload[10]
        assert payload[11:11 + n] == b"DDZY666-Z"
        m = payload[11 + n]
        m_pos = 12 + n
        assert m == 0 and payload[m_pos] == 0              # M、m
        assert len(payload) == m_pos + 1

    def test_seq_auto_increment_via_api(self):
        b = MeterBrain()
        b.set_jzjc(seq=0x10FF)
        payload = b.build_jzjc_report("afn06f7")
        assert payload[8:10] == b"\xFF\x10"                # 低字节在前


class TestJzjcDisabledFlag:
    def test_enabled_flag(self):
        b = MeterBrain()
        b.set_jzjc(enabled=False)
        assert b.get_jzjc()["enabled"] is False
        # 关闭只影响业务侧开关，报文生成仍可用（便于复演）
        assert b.build_jzjc_report("sta")[0] == 51
