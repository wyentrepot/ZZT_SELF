"""STA 表口协议探测 OAD 40010200 应答专项单测（REQS-0043 工位 H 引入，工位 L 重写）。

台架证据（逐字节基准来源）：run 20261124-192633，simeter 帧日志
``sm-20261124-192324 seq 59/76``——STA addrmap 判 645 后 `try 698 pro` 发探帧
``6817004345AAAAAAAAAAAA10DA5F0501134001020000204D16``（GET-RequestNormal：
``05 01 13 40 01 02 00 00``，SA=45+AA×6 通配、CA=10、明文无 SECURITY 包装）。

工位 L 两处修复（REQS-0043 §K 根因①，CASE-BACKLOG §K.4/K.5 实锤）：
1. **SA=本表真实地址**：STA 表口 698 绑表按**应答帧 SA** 取 MAC——旧实现回显
   请求通配 SA（45+AA×6）→ STA `set mac addr:aaaaaaaaaaaa` → CCO 白名单
   （=档案 6B 地址）查无 → `ASSOC_RSLT_OUTOFLIST(0x1) not allow`。修复后应答
   SA = ``05 + 6B 表地址``（单地址头，KB `02_DLT69845_帧格式.md` §2.5：bit6~7
   =00 单地址、bit0~3=长度；645 线序即人读 hex 直排，与档案/白名单同口径，
   §K.1 已证 645 绑表所得 129078563412 就是这个字序）。
2. **协议一致闸**：jzjc 档案协议类型=02H（645-2007）时不答 698 探帧
   （行为=未知 OAD 同款：空 wire 不应答）；03H（698.45）态应答行为同上，
   00H（透明）态维持历史应答不动（低风险待议项，本轮锁定现状）。

黄金帧为一次性离线计算后钉死（不在测试里用同一构造函数回算，防循环论证）：
HCS/FCS 用 PPP FCS-16（生成多项式 0x8408 反射态、初值 0xFFFF、结果异或
0xFFFF、低字节在前），HCS 覆盖 L..CA、FCS 覆盖 L..APDU，离线经
``parser_lib.adapters.adapter_698._fcs_bytes`` 算出，并以台架实收帧
（§K.4 simeter 应答帧 SA=45+AA×6 版，帧内 FCS=CD97）同法验算命中校准。
"""
from parser_lib.adapters.adapter_645 import build_frame as build_645
from parser_lib.adapters.adapter_698 import build_frame as build_698

from sim_meter.meter import PROBE_OAD, MeterBrain
from sim_meter.protocol_698 import FE_PREAMBLE, parse_698_frame

#: 台架实收 STA 探帧原文（sm-20261124-192324 seq 59，一字不动）
STA_PROBE_FRAME_HEX = "6817004345AAAAAAAAAAAA10DA5F0501134001020000204D16"

#: 698 态（jzjc protocol_type=3）、表地址 123456789012 时的应答黄金帧
#: （离线一次性计算后钉死，FCS 口径见模块 docstring）：
#: FE×4 | 68 1D 00 C3 | SA=05 12 34 56 78 90 12 | CA=10 | HCS 9D31
#: | 85 01 13 40 01 02 00 01 12 34 56 78 90 12 | FCS CD97 | 16
#: 与修复前旧黄金帧（FEFEFEFE681D00C345AAAAAAAAAAAA10858C...3BC716，默认
#: 地址 000000000001 + SA 回显）的字段差异：SA 由 45+AA×6（通配回显）改为
#: 05+123456789012（单地址真实表地址）；L/C/CA/APDU 布局与长度不变；
#: HCS 858C→9D31、FCS 3BC7→CD97（旧帧数据域是 000000000001，两帧整体
#: 差异按字段对照，不作逐字节 diff）。
GOLDEN_REPLY_HEX = (
    "FEFEFEFE681D00C305123456789012109D318501134001020001123456789012CD9716"
)

ADDR_6B = bytes.fromhex("123456789012")            # 6B 线序（人读 hex 直排）


def _698_brain(addr: str = "123456789012") -> MeterBrain:
    """698 态脑：jzjc 协议类型=03H（698.45）。"""
    brain = MeterBrain(addr=addr)
    brain.set_jzjc(protocol_type=0x03)
    return brain


def _get_request_normal(addr6: bytes, oad: bytes, piid: int = 0x13,
                        security: bool = False, sa_head: int = 0x45,
                        ca: int = 0x10) -> bytes:
    """构造 STA 探帧同款 GET-RequestNormal（缺省按台架实测口径：通配 AA×6 + CA=10）。

    ``security=True`` 时加 SECURITY-Request(明文) 包装（RN 16×00，与 43000200
    探针同模板），用于验证包装应答分支。
    """
    inner = bytes([0x05, 0x01, piid]) + bytes(oad) + b"\x00"
    if security:
        apdu = bytes([0x10, 0x00, len(inner)]) + inner + b"\x01\x10" + b"\x00" * 16
    else:
        apdu = inner
    sa = bytes([sa_head]) + b"\xAA" * 6
    return FE_PREAMBLE + build_698(apdu, sa, ca=ca, control=0x43)


class TestProbeOad40010200:
    def test_bench_probe_frame_golden_reply(self):
        """【修复①黄金帧】台架实收探帧（seq 59 原文）→ 698 态应答逐字节一致。

        覆盖：应答 SA=05+123456789012（真实表地址）、数据域含同地址；
        L/C/CA/APDU 布局与修复前一致。
        """
        brain = _698_brain(addr="123456789012")
        res = brain.handle_frame(bytes.fromhex(STA_PROBE_FRAME_HEX))
        assert res.reply is None                       # 698 应答走 wire
        assert res.reply_wire is not None
        assert res.reply_wire.hex().upper() == GOLDEN_REPLY_HEX
        assert res.note["func"] == "698 表口协议探测读通信地址(40010200)"
        assert res.note["probe_addr"] == "123456789012"
        assert res.note["security"] is False

    def test_reply_structure_get_response_normal(self):
        """【修复①结构】应答结构：85 01 PIID 40010200 01 <6B 地址>，C=0xC3，
        应答帧 SA=05+本表地址（单地址头，非通配回显）。

        （解析器只对 GET-Request 填 ``oad``/``inner_apdu``，应答帧取 ``apdu`` 对账。）
        """
        brain = _698_brain(addr="123456789012")
        res = brain.handle_frame(_get_request_normal(b"\xAA" * 6, PROBE_OAD))
        assert res.reply_wire is not None
        p = parse_698_frame(res.reply_wire)
        assert p.control == 0xC3                       # 服务器响应
        assert p.security is None                      # 明文探帧 → 明文应答
        assert p.service == 0x85                       # GET-Response
        assert p.addr == b"\x05" + ADDR_6B             # SA=单地址头+真实表地址
        assert p.apdu == (bytes([0x85, 0x01, 0x13]) + PROBE_OAD
                          + b"\x01" + ADDR_6B)         # 数据域=同地址

    def test_reply_carries_current_meter_address(self):
        """【修复①跟变】set_address 后探帧应答的 SA 与数据域都跟着变（STA 按地址对账）。"""
        brain = _698_brain(addr="000000000001")
        res = brain.handle_frame(_get_request_normal(b"\xAA" * 6, PROBE_OAD))
        assert res.reply_wire is not None
        p = parse_698_frame(res.reply_wire)
        assert p.addr == b"\x05" + bytes.fromhex("000000000001")
        assert p.apdu[-6:] == bytes.fromhex("000000000001")
        assert res.note["probe_addr"] == "000000000001"
        brain.set_address("123456789012")
        res2 = brain.handle_frame(_get_request_normal(b"\xAA" * 6, PROBE_OAD))
        assert res2.reply_wire is not None
        p2 = parse_698_frame(res2.reply_wire)
        assert p2.addr == b"\x05" + ADDR_6B            # SA 跟 set_address 变
        assert p2.apdu[-6:] == ADDR_6B                 # 数据域同变
        assert res2.note["probe_addr"] == "123456789012"

    def test_probe_answered_without_oad_dataset(self):
        """【数据集无关】探帧不依赖采集数据集：698 态默认脑（两层 OAD 数据集全空）也必须应答。

        STA 表口探测发生在开机建档之前，此时没人配过数据集——这正是 §G H2
        缺陷的现场条件（simeter seq 59：数据集未命中 → 不应答）。
        """
        brain = _698_brain()
        assert brain.get_oad_entries()["effective"] == {}
        res = brain.handle_frame(_get_request_normal(b"\xAA" * 6, PROBE_OAD))
        assert res.reply_wire is not None
        assert res.note["func"].startswith("698 表口协议探测")

    def test_probe_broadcast_aa6_answered_with_own_addr(self):
        """【广播判读不变+修复①】SA=45+AA×6 通配探帧（台架实测口径）与 05 单播探帧同答，
        应答 SA 恒为本表真实地址（不再回显请求通配 SA）。
        """
        brain = _698_brain(addr="123456789012")
        for sa_head in (0x45, 0x05):
            res = brain.handle_frame(
                _get_request_normal(b"\xAA" * 6, PROBE_OAD, sa_head=sa_head))
            assert res.reply_wire is not None
            p = parse_698_frame(res.reply_wire)
            assert p.addr == b"\x05" + ADDR_6B         # 两种请求 SA 都答真实地址
            assert p.apdu[-6:] == ADDR_6B

    def test_probe_security_wrapped_reply(self):
        """【包装分支+修复①】SECURITY-Request(明文) 包装的探帧 → 90 00 包装应答，
        且 SA=真实表地址（修复①在安全包装路径同样生效）。
        """
        brain = _698_brain(addr="123456789012")
        res = brain.handle_frame(_get_request_normal(b"\xAA" * 6, PROBE_OAD,
                                                     security=True))
        assert res.reply_wire is not None
        p = parse_698_frame(res.reply_wire)
        assert p.security == 0x90 and p.service == 0x85
        assert p.addr == b"\x05" + ADDR_6B
        assert p.inner_apdu == (bytes([0x85, 0x01, 0x13]) + PROBE_OAD
                                + b"\x01" + ADDR_6B)

    def test_other_oads_keep_historic_no_reply(self):
        """【历史行为不变】未注册 OAD 保持历史行为：不应答（不悄悄放宽）。

        工位 N 起数据 OAD 注册表（20000201/20010201/20000200/20010200）在 698
        态应答（见 test_oad_data_get.py）；本条钉的是**注册表之外**的 OAD 与
        数据集未配置的采集 OAD 不应答语义不变。
        """
        brain = _698_brain()
        res = brain.handle_frame(_get_request_normal(b"\xAA" * 6,
                                                     bytes.fromhex("00000001")))
        assert res.reply is None and res.reply_wire is None
        assert res.note["reason"] == "未知 OAD"
        # 注册表之外、数据集未配置的采集 OAD 同样不应答（test_oad_dataset 口径不变）
        res2 = brain.handle_frame(_get_request_normal(b"\xAA" * 6,
                                                      bytes.fromhex("20990201")))
        assert res2.reply is None and res2.reply_wire is None


class TestProbeProtocolGate:
    """【修复②】协议一致闸：jzjc 档案协议类型=02H（645）不答 698 探帧。"""

    def test_gate_645_state_silent_698_state_golden(self):
        """同一探帧：jzjc=2（645 日常态）→ 空 wire 不应答；jzjc=3（698 态）→ 新黄金帧。

        用户口径「档案-表口-STA 三方一致，除非特定测试用例」：645 表答 698 探帧
        会让 STA 误绑 698 并被 CCO 拒（§K.4 恢复轮同构现场）。
        """
        brain = MeterBrain(addr="123456789012")        # 默认 jzjc protocol_type=0x02
        res = brain.handle_frame(bytes.fromhex(STA_PROBE_FRAME_HEX))
        assert res.reply is None and res.reply_wire is None   # 空 wire 不应答
        assert res.note["reason"] == "协议不一致(jzjc=645)"
        assert "645" in res.note["func"]
        brain.set_jzjc(protocol_type=0x03)             # 切 698 态 → 解闸
        res3 = brain.handle_frame(bytes.fromhex(STA_PROBE_FRAME_HEX))
        assert res3.reply_wire is not None
        assert res3.reply_wire.hex().upper() == GOLDEN_REPLY_HEX

    def test_gate_scoped_to_probe_only(self):
        """闸不外溢：jzjc=2 时 43000200（即装即采）与未知 OAD 语义不变。

        （工位 N 起闸延伸到数据 OAD 注册表分支——见 test_oad_data_get.py
        TestOadDataProtocolGate；本条钉的是 43000200 免闸与未知 OAD 原语义。）"""
        brain = MeterBrain(addr="123456789012")        # protocol_type=0x02
        res = brain.handle_frame(_get_request_normal(b"\xAA" * 6,
                                                     bytes.fromhex("00000001")))
        assert res.reply is None and res.reply_wire is None
        assert res.note["reason"] == "未知 OAD"        # 未知 OAD 原语义，非协议闸
        # 43000200 不受闸影响（jzjc 描述符本来就是 jzjc 面）
        from sim_meter.protocol_698 import build_get_request_43000200
        res_jzjc = brain.handle_frame(build_get_request_43000200(ADDR_6B))
        assert res_jzjc.reply_wire is not None
        assert res_jzjc.note["func"] == "即装即采读设备描述符(43000200)"

    def test_gate_transparent_type0_still_answers(self):
        """闸边界：jzjc protocol_type=00H（透明）维持历史应答不动（待议项锁定现状）。"""
        brain = MeterBrain(addr="123456789012")
        brain.set_jzjc(protocol_type=0x00)
        res = brain.handle_frame(bytes.fromhex(STA_PROBE_FRAME_HEX))
        assert res.reply_wire is not None
        p = parse_698_frame(res.reply_wire)
        assert p.addr == b"\x05" + ADDR_6B

    def test_645_13h_reply_unchanged(self):
        """【645 回归】13H 读通信地址应答行为不变：闸只拦 698 探帧，645 路径不动。

        jzjc=2（645 日常态）与 jzjc=3（698 态）下 13H 都照常应答、控制码 93H、
        数据域=本表地址原始字节（不加 33H，与 645 知识库示例一致）。
        """
        req_13h = build_645(ADDR_6B, 0x13, b"")
        for pt in (0x02, 0x03):
            brain = MeterBrain(addr="123456789012")
            brain.set_jzjc(protocol_type=pt)
            res = brain.handle_frame(req_13h)
            assert res.reply is not None, f"jzjc={pt} 下 13H 必须照常应答"
            assert res.note["func"] == "读通信地址"
            reply = res.reply
            assert reply[0] == 0x68 and reply[7] == 0x68 and reply[-1] == 0x16
            assert reply[8] == 0x93                    # 13H 应答控制码
            assert reply[10:-2] == ADDR_6B             # 数据域=本表地址（不加 33H）
