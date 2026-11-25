"""698 数据 OAD GET 应答专项单测（REQS-0043 工位 N）。

背景（M 轮真机观测，run 20261125-142602-d33ab2 s6）：CCO 02H-F1 透传的
GET-RequestNormal（OAD 20000201）经 STA 到达 simeter 共 47 帧
（``data/logs/simeter/sm-20261125-142015--run-hplc-serial-simeter.jsonl``
seq 102-148，帧内 SA=05 123456789012 寻址正确），但 simeter 只有探帧
OAD 43000200/40010200 应答，数据 OAD 一律「非 43000200，不应答」→
698 抄读「有值」观测从未获得。本批次给 ``meter.py::_handle_698`` 补
**数据 OAD 注册表**分支（``_OAD_DATA_MAP``）。

OAD 语义/编码出处（逐条留痕，与模块常量注释一致）：
- 645↔698 编号对照：KB ``用例库/STA/能力域/141_分钟级采集_用例蒸馏.md:33-40``
  （单相 645 02010100/02020100 ↔ 698 20000201/20010201；三相 0201FF00~0204FF00
  ↔ 20000200/20010200）；
- OI 语义/换算：仓库 ``docs/_archive/2026-08-28/698.45-OAD覆盖/复核报告.md``
  §3.1（2000=电压 V 换算-1、2001=电流 A 换算-3、属性 2=分相数值组 array），
  parser_lib ``adapter_698/metadata/oad.json`` 同口径；
- 编码取标量（20000201/20010201 属性 2+索引 01 按索引回单值，复核报告 P13）
  或 array（20000200/20010200 属性 2 分相数值组 A/B/C）；值实时取自 645 寄存器
  数据集（同源：220.1 V / 001.234 A），set_register 改值应答跟变。

黄金帧为一次性离线计算后钉死（不在测试里用同一构造函数回算，防循环论证）；
帧内 HCS/FCS 均通过 PPP FCS-16 校验（``adapter_698._check_fcs``），并经
parser_lib facade ``decode(protocol="698.45")``（read_698 块同一判读路径）
解得「A相计量电压 220.1 V」复核。
"""
from parser_lib.adapters.adapter_698 import build_frame as build_698

from sim_meter.meter import MeterBrain
from sim_meter.protocol_698 import FE_PREAMBLE, parse_698_frame

#: M 轮台架实收 698 数据读请求原文（journal seq 102，一字不动）：
#: SECURITY-Request(明文) 包 GET-RequestNormal（05 01 PIID=01 OAD 20000201
#: TimeTag=00 + RN），SA=05+123456789012、CA=00、C=43。
BENCH_REQUEST_HEX = (
    "682C00430512345678901200952310000805010120000201000110000000000000000"
    "00000000000000000C4CC16"
)

#: 698 态（jzjc protocol_type=3）、表地址 123456789012 时对 BENCH_REQUEST_HEX
#: 的应答黄金帧（离线一次性计算后钉死）：
#: FE×4 | 68 1D 00 C3 | SA=05 12 34 56 78 90 12 | CA=00 | HCS 1C21
#: | 90 00 0B | 85 01 01 20 00 02 01 01 12 08 99 | FCS 5BD4 | 16
#: （90 00 0B=SECURITY-Response 明文包装；85 01 PIID=01 + OAD 回显 + 01=数据
#: + 12 08 99=long-unsigned 2201 → 220.1 V，换算-1）
GOLDEN_SECURITY_REPLY_HEX = (
    "FEFEFEFE681D00C305123456789012001C2190000B85010120000201011208995BD416"
)

#: 同一请求去掉 SECURITY 包装（裸 05 01 01 20000201 00，明文）的应答黄金帧：
#: 应答同样不带包装（SECURITY 跟随请求）：
#: FE×4 | 68 1A 00 C3 | SA=05 12 34 56 78 90 12 | CA=00 | HCS FA81
#: | 85 01 01 20 00 02 01 01 12 08 99 | FCS 1269 | 16
GOLDEN_PLAIN_REPLY_HEX = (
    "FEFEFEFE681A00C30512345678901200FA818501012000020101120899126916"
)

ADDR_6B = bytes.fromhex("123456789012")            # 6B 线序（人读 hex 直排）


def _698_brain(addr: str = "123456789012") -> MeterBrain:
    """698 态脑：jzjc 协议类型=03H（698.45）。"""
    brain = MeterBrain(addr=addr)
    brain.set_jzjc(protocol_type=0x03)
    return brain


def _get_request_normal(addr6: bytes, oad: bytes, piid: int = 0x01,
                        security: bool = True) -> bytes:
    """M 轮实测同款 GET-RequestNormal：SA=05+表地址、CA=00、PIID=01。

    ``security=True`` 加 SECURITY-Request(明文) 包装（01 10 + RN 16×00，
    与 BENCH_REQUEST_HEX 同模板）；False 发裸 APDU。
    """
    inner = bytes([0x05, 0x01, piid]) + bytes(oad) + b"\x00"
    if security:
        apdu = (bytes([0x10, 0x00, len(inner)]) + inner
                + b"\x01\x10" + b"\x00" * 16)
    else:
        apdu = inner
    sa = b"\x05" + bytes(addr6)
    return FE_PREAMBLE + build_698(apdu, sa, ca=0x00, control=0x43)


class TestOadDataGetResponses:
    def test_bench_request_golden_reply_security(self):
        """【黄金帧·M 轮原文】台架实收 47 帧同款请求（seq 102 原文）→ 应答逐字节一致。

        覆盖：SECURITY 包装跟随请求（90 00 0B）、OAD 回显、数据=12 08 99
        （long-unsigned 2201 → 220.1 V，取自 645 寄存器 02010100 同源）。
        """
        brain = _698_brain(addr="123456789012")
        res = brain.handle_frame(bytes.fromhex(BENCH_REQUEST_HEX))
        assert res.reply is None                       # 698 应答走 wire
        assert res.reply_wire is not None
        assert res.reply_wire.hex().upper() == GOLDEN_SECURITY_REPLY_HEX
        assert res.note["func"] == "698 数据读点(A相计量电压 20000201)"
        assert res.note["value"] == "220.1"            # 与 645 数据集同源
        assert res.note["unit"] == "V"
        assert res.note["security"] is True

    def test_plain_request_answered_without_security_wrap(self):
        """【包装跟随】裸 GET-RequestNormal（无 SECURITY 包装）→ 明文应答逐字节一致。

        与上一条构成「含/不含 SECURITY 包装都答，且包装形态跟随请求」两态对照。
        """
        brain = _698_brain(addr="123456789012")
        res = brain.handle_frame(
            _get_request_normal(ADDR_6B, bytes.fromhex("20000201"),
                                security=False))
        assert res.reply_wire is not None
        assert res.reply_wire.hex().upper() == GOLDEN_PLAIN_REPLY_HEX
        assert res.note["security"] is False

    def test_reply_structure_and_facade_decode(self):
        """【结构+可解】应答结构 85 01 PIID OAD 01 <data>，SA=真实表地址；
        parser_lib facade（read_698 块同一判读路径）解得 A相计量电压 220.1 V。
        """
        brain = _698_brain(addr="123456789012")
        res = brain.handle_frame(
            _get_request_normal(ADDR_6B, bytes.fromhex("20000201")))
        p = parse_698_frame(res.reply_wire)
        assert p.control == 0xC3                       # 服务器响应
        assert p.addr == b"\x05" + ADDR_6B             # SA=单地址头+真实表地址
        assert p.ca == 0x00
        assert p.security == 0x90 and p.service == 0x85
        assert p.inner_apdu == (bytes([0x85, 0x01, 0x01])
                                + bytes.fromhex("20000201")
                                + b"\x01\x12\x08\x99")
        # facade 判读（FE 前导不入转发报文，剥去后按 698.45 强解）
        from parser_lib import facade
        frame = facade.decode(res.reply_wire[4:].hex().upper(),
                              protocol="698.45").get("frame") or {}
        items = frame.get("items") or []
        assert [(i.get("name"), i.get("value"), i.get("unit"))
                for i in items] == [("A相计量电压", 220.1, "V")]

    def test_all_registered_oads_answer_from_registers(self):
        """【注册表全集】四个数据 OAD 应答值/编码逐条钉死（全部实时取自 645 寄存器）。

        - 20000201 A相计量电压  long-unsigned（12）220.1×10=2201 → 08 99
        - 20010201 A相计量电流  double-long（05）001.234×1000=1234 → 00 00 04 D2
        - 20000200 电压分相数值组 array(3) long-unsigned：220.1/221.0/219.8
        - 20010200 电流分相数值组 array(3) double-long：1.234/1.223/1.245
        """
        brain = _698_brain(addr="123456789012")
        expectations = {
            "20000201": (b"\x12\x08\x99", "220.1", "V"),
            "20010201": (b"\x05\x00\x00\x04\xD2", "001.234", "A"),
            "20000200": (b"\x01\x03\x12\x08\x99\x12\x08\xA2\x12\x08\x96",
                         "[220.1, 221.0, 219.8]", "V"),
            "20010200": (b"\x01\x03\x05\x00\x00\x04\xD2\x05\x00\x00\x04\xC7"
                         b"\x05\x00\x00\x04\xDD",
                         "[001.234, 001.223, 001.245]", "A"),
        }
        for oad_hex, (data, shown, unit) in expectations.items():
            res = brain.handle_frame(
                _get_request_normal(ADDR_6B, bytes.fromhex(oad_hex)))
            assert res.reply_wire is not None, oad_hex
            p = parse_698_frame(res.reply_wire)
            assert p.inner_apdu[3:7] == bytes.fromhex(oad_hex)   # OAD 回显
            assert p.inner_apdu[8:] == data, oad_hex             # A-XDR 数据
            assert res.note["value"] == shown, oad_hex
            assert res.note["unit"] == unit, oad_hex

    def test_register_update_follows(self):
        """【数据同源跟变】set_register 改 A 相电压 → 应答数据与 note.value 跟变。"""
        brain = _698_brain(addr="123456789012")
        brain.set_register("02010100", "225.5", scale=1, length=2)
        res = brain.handle_frame(
            _get_request_normal(ADDR_6B, bytes.fromhex("20000201")))
        p = parse_698_frame(res.reply_wire)
        assert p.inner_apdu[8:] == b"\x12\x08\xCF"     # 225.5×10=2255=0x08CF
        assert res.note["value"] == "225.5"

    def test_set_address_follows_sa(self):
        """【SA 纪律跟变】set_address 后应答帧 SA 跟变（与探帧修复同款：
        STA 表口按应答帧 SA 取 MAC，恒为本表真实地址 05+6B 线序）。"""
        brain = _698_brain(addr="000000000001")
        res = brain.handle_frame(
            _get_request_normal(bytes.fromhex("000000000001"),
                                bytes.fromhex("20000201")))
        p = parse_698_frame(res.reply_wire)
        assert p.addr == b"\x05" + bytes.fromhex("000000000001")
        brain.set_address("123456789012")
        res2 = brain.handle_frame(
            _get_request_normal(ADDR_6B, bytes.fromhex("20000201")))
        p2 = parse_698_frame(res2.reply_wire)
        assert p2.addr == b"\x05" + ADDR_6B

    def test_dataset_entry_wins_over_registry(self):
        """【优先级】数据集显式条目优先于注册表：data_hex 覆盖注册表值；
        missing=true 显式「不应答」配置同样优先（REQS-0037 注入口径不变）。"""
        brain = _698_brain(addr="123456789012")
        brain.set_oad_entries(entries=[{"oad": "20000201", "data_hex": "1101"}])
        res = brain.handle_frame(
            _get_request_normal(ADDR_6B, bytes.fromhex("20000201")))
        p = parse_698_frame(res.reply_wire)
        assert p.inner_apdu[8:] == b"\x11\x01"         # 数据集值，非注册表值
        assert res.note["func"] == "698 采集读点(20000201)"
        # 显式缺项压制注册表
        brain.set_oad_entries(entries=[{"oad": "20000201", "missing": True}])
        res2 = brain.handle_frame(
            _get_request_normal(ADDR_6B, bytes.fromhex("20000201")))
        assert res2.reply is None and res2.reply_wire is None
        assert res2.note["reason"] == "OAD 缺项"


class TestOadDataProtocolGate:
    """【协议一致闸延伸】数据 OAD 应答只在 jzjc=03H（698.45）态生效。"""

    def test_gate_645_state_silent_698_state_golden(self):
        """同一 M 轮请求帧：jzjc=2（645 日常态）→ 空 wire（reason=协议不一致）；
        jzjc=3（698 态）→ 黄金帧。与探帧闸同款语义（645 表不答 698 数据读）。"""
        brain = MeterBrain(addr="123456789012")        # 默认 jzjc protocol_type=0x02
        res = brain.handle_frame(bytes.fromhex(BENCH_REQUEST_HEX))
        assert res.reply is None and res.reply_wire is None   # 空 wire 不应答
        assert res.note["reason"] == "协议不一致(jzjc=645)"
        brain.set_jzjc(protocol_type=0x03)             # 切 698 态 → 解闸
        res3 = brain.handle_frame(bytes.fromhex(BENCH_REQUEST_HEX))
        assert res3.reply_wire is not None
        assert res3.reply_wire.hex().upper() == GOLDEN_SECURITY_REPLY_HEX

    def test_gate_transparent_state_silent(self):
        """闸边界：jzjc=00H（透明）态数据 OAD 也不应答——「只在 698 态生效」。

        与探帧闸的 00H 维持应答不同：探帧有历史应答行为可保留，数据 OAD
        历史上从不应答（注册表是本批次新增），锁定「只在 698 态」字面口径。
        """
        brain = MeterBrain(addr="123456789012")
        brain.set_jzjc(protocol_type=0x00)
        res = brain.handle_frame(
            _get_request_normal(ADDR_6B, bytes.fromhex("20000201")))
        assert res.reply is None and res.reply_wire is None
        assert res.note["reason"] == "协议不一致(jzjc=0)"

    def test_withlist_stays_dataset_only(self):
        """【WithList 不变】注册表不进 WithList：数据集全空时 WithList 采集读
        维持 REQS-0037 行为（逐条 omitted、全部未配置 → 不应答 未知 OAD）。"""
        brain = _698_brain(addr="123456789012")
        inner = (bytes([0x05, 0x02, 0x01, 0x03])
                 + bytes.fromhex("20000201")
                 + bytes.fromhex("20010201")
                 + bytes.fromhex("20000200") + b"\x00")
        apdu = bytes([0x10, 0x00, len(inner)]) + inner + b"\x01\x10" + b"\x00" * 16
        req = FE_PREAMBLE + build_698(apdu, b"\x05" + ADDR_6B,
                                      ca=0x00, control=0x43)
        res = brain.handle_frame(req)
        assert res.reply is None and res.reply_wire is None
        assert res.note["reason"] == "未知 OAD"
        assert res.note["oad_omitted"] == ["20000201(未配置)",
                                           "20010201(未配置)",
                                           "20000200(未配置)"]

    def test_unregistered_oad_no_reply(self):
        """【未注册 OAD 维持不应答】注册表与数据集都没有的 OAD → 未知 OAD。"""
        brain = _698_brain(addr="123456789012")
        res = brain.handle_frame(
            _get_request_normal(ADDR_6B, bytes.fromhex("20990201")))
        assert res.reply is None and res.reply_wire is None
        assert res.note["reason"] == "未知 OAD"
        assert res.note["func"] == "698 OAD=0x20990201 非 43000200，不应答"
