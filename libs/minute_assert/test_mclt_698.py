"""mclt_698：698/645 分钟采集 11E3/06H-F230 帧解析与断言 契约测试。

固定测试数据全部来自真实台架证据（只读引用，严禁改动证据目录）：
- task 1 全链路 PASS 帧（11E3 下/上行、内层 698、06H-F230）：
  H_STA/01/sta/reqs/0005-chongqing-sta-minute-collection-698-11e3/
  evidence/BENCH-v5-taskid-RECOVERED.md
- task 0 / task 15 失败响应帧（ret_data_len=0）：同上。
- 645 task 1 / task 2（缺项 0x32）帧、645 F231/F232：
  同目录 evidence/bench-645-regression-20261102-180650/（README + parsed JSON）。
"""
import pytest

from minute_assert.mclt_698 import (
    Expect698,
    assert_698_11e3_chain,
    find_inner_frame,
    parse_11e3_down,
    parse_11e3_up,
    parse_f230_report,
    parse_f231_config,
    parse_inner_645,
    parse_inner_698,
    secs_to_text,
    split_inner_frames,
)
from minute_assert.mclt_698 import bcd6_time_bytes

# ---------------------------------------------------------------------------
# 真实台架帧（BENCH-v5-taskid-RECOVERED.md，2026-11-02 task 1 全链路 PASS）
# ---------------------------------------------------------------------------

# 11E3 下行（CCO→STA 请求，task 1）
E3_DOWN_TASK1 = bytes.fromhex(
    "11e300000105240000000300000000000101001017021126"
)
# 11E3 上行（STA→CCO 应答，task 1，含内层 698 54B）
E3_UP_TASK1 = bytes.fromhex(
    "11e30000c115240000000300000000000101001017021126033600"
    "683400c30500000000000101cda990002285020103200002010111012001020101"
    "11022000020001ffff00000100046ae8c3e84f7716"
)
# 内层 698 帧（54B，与 11E3 上行 payload 一致）
INNER_698_TASK1 = bytes.fromhex(
    "683400c30500000000000101cda990002285020103200002010111012001020101"
    "11022000020001ffff00000100046ae8c3e84f7716"
)
# 06H-F230 上报（task 1，嵌套内层 698）
F230_TASK1 = bytes.fromhex(
    "685500c300004000002006201c01000000000001030010170211263600"
    "683400c30500000000000101cda990002285020103200002010111012001020101"
    "11022000020001ffff00000100046ae8c3e84f77168616"
)

# task 0 / task 15 失败响应（17:10 冻结时刻无数据，ret_data_len=0）
E3_UP_TASK0 = bytes.fromhex(
    "11e30000c115280000004300000000000100001017021126000000"
)
E3_UP_TASK15 = bytes.fromhex(
    "11e30000c11525000000430000000000010f001017021126000000"
)
E3_DOWN_TASK0 = bytes.fromhex(
    "11e300000105280000000300000000000100001017021126"
)
E3_DOWN_TASK15 = bytes.fromhex(
    "11e30000010525000000030000000000010f001017021126"
)

# 645 task 1（正常 3 DI，18:05 冻结）与 task 2（缺项 0x32，18:15 冻结）
# bench-645-regression-20261102-180650/parsed-11e3-f230-task1.json 等
E3_UP_645_TASK1 = bytes.fromhex(
    "11e30000c115710000000200000000000101000518021126033800"
    "68000000000001689106333434353455c116"
    "68000000000001689107333435356745331916"
    "68000000000001689107333236356587335816"
)
E3_UP_645_TASK2_MISSING = bytes.fromhex(
    "11e30000c115770000000200000000000102001518021126033800"
    "68000000000001689106333434353455c116"
    "68000000000001689107333435356745331916"
    "6800000000000168910733343635323232d116"
)
F231_645_TASK1 = bytes.fromhex(
    "682a004300000000002111401c01010205000308000001010202000102020300ff03"
    "020301000200fd16"
)
F231_645_TASK2 = bytes.fromhex(
    "682a004300000000002511401c020102050003080000010102020001020203000103"
    "0203010002000416"
)

FZ_TASK1_698 = "2026-11-02 17:10:00"
EXPECT_TASK1 = Expect698(
    task_id=1,
    fz_time_text=FZ_TASK1_698,
    oad_lens=(2, 2, 2),
    oads=(0x20000201, 0x20010201, 0x20000200),
    missing=(False, False, True),
    time_tag_text=FZ_TASK1_698,
)


class TestParse11e3Up:
    def test_task1_full_chain_pass_frame(self):
        up = parse_11e3_up(E3_UP_TASK1)
        assert up is not None
        assert up.task_id == 1
        assert up.protocol == 3  # 698.45
        assert up.result == 0
        assert up.di_cnt == 3
        assert up.bufflen == 54
        assert len(up.payload) == 54
        assert up.fz_time == bytes.fromhex("001017021126")
        assert up.fz_time_text == FZ_TASK1_698
        assert up.ssn == 36  # 0x24，与下行一致

    def test_task0_failure_response(self):
        up = parse_11e3_up(E3_UP_TASK0)
        assert up is not None
        assert up.task_id == 0
        assert up.result == 2
        assert up.di_cnt == 0
        assert up.bufflen == 0
        assert up.payload == b""

    def test_task15_failure_response(self):
        up = parse_11e3_up(E3_UP_TASK15)
        assert up is not None
        assert up.task_id == 15
        assert up.result == 2
        assert up.bufflen == 0

    def test_645_task1_frame(self):
        up = parse_11e3_up(E3_UP_645_TASK1)
        assert up is not None
        assert up.task_id == 1
        assert up.protocol == 2  # 645
        assert up.di_cnt == 3
        assert up.bufflen == 56
        assert up.fz_time_text == "2026-11-02 18:05:00"

    def test_garbage_returns_none(self):
        assert parse_11e3_up(b"\x11\xe2" + bytes(40)) is None
        assert parse_11e3_up(b"hello") is None


class TestParse11e3Down:
    def test_task1_down(self):
        down = parse_11e3_down(E3_DOWN_TASK1)
        assert down is not None
        assert down.task_id == 1
        assert down.protocol == 3
        assert down.fz_time_text == FZ_TASK1_698
        assert down.ssn == 36

    def test_down_matches_up_ssn_and_time(self):
        down = parse_11e3_down(E3_DOWN_TASK1)
        up = parse_11e3_up(E3_UP_TASK1)
        assert down.ssn == up.ssn
        assert down.fz_time == up.fz_time


class TestInner698:
    def test_locate_inner_frame(self):
        up = parse_11e3_up(E3_UP_TASK1)
        found = find_inner_frame(up.payload)
        assert found is not None
        frame, off = found
        assert off == 0
        assert frame == INNER_698_TASK1

    def test_parse_task1_inner(self):
        f = parse_inner_698(INNER_698_TASK1, (2, 2, 2))
        assert f is not None
        assert f.control == 0xC3
        assert f.oad_count == 3
        assert f.apdu_len_field == 0x22  # 34，含 9 字节尾
        assert f.apdu_len_has_tail is True
        assert f.apdu_actual_len == 25
        assert f.hcs_ok and f.fcs_ok
        assert f.total_len == 54
        assert [o.oad_hex for o in f.oads] == [
            "20000201",
            "20010201",
            "20000200",
        ]
        assert f.oads[0].data == bytes.fromhex("1101")
        assert f.oads[1].data == bytes.fromhex("1102")
        assert f.oads[2].data == bytes.fromhex("ffff")
        assert [o.is_missing for o in f.oads] == [False, False, True]
        assert f.tail[:5] == bytes.fromhex("0000010004")
        assert f.time_tag == bytes.fromhex("6ae8c3e8")
        assert f.time_tag_text == FZ_TASK1_698  # TimeTag=冻结时刻

    def test_infer_equal_lens_when_no_config(self):
        f_cfg = parse_inner_698(INNER_698_TASK1, (2, 2, 2))
        f_inf = parse_inner_698(INNER_698_TASK1)
        assert f_inf is not None
        assert [(o.oad_hex, o.data) for o in f_inf.oads] == [
            (o.oad_hex, o.data) for o in f_cfg.oads
        ]

    def test_timetag_epoch_conversion(self):
        # 0x6AE8C3E8 = 2026-11-02 17:10:00（台架本地时间口径）
        assert secs_to_text(0x6AE8C3E8) == FZ_TASK1_698

    def test_reject_downlink_frame(self):
        # C=0x43 的下行请求帧不是上行应答结构（无 90 00 SEC 域）
        down_698 = bytes.fromhex(
            "68170043570000000000c3a41305010000000000c3a401030d101b0206c0a5a0"
            "4a654c03e6e75216"
        )
        assert parse_inner_698(down_698) is None


class TestInner645:
    def test_task1_three_frames_one_di_each(self):
        up = parse_11e3_up(E3_UP_645_TASK1)
        frames = split_inner_frames(up.payload)
        assert len(frames) == 3
        di_displays = []
        for fr in frames:
            f = parse_inner_645(fr)
            assert f is not None
            assert f.control == 0x91
            assert f.address == "000000000001"
            assert f.cs_ok is True
            assert f.is_32_fill is False
            di_displays.append(f.di_display)
        assert di_displays == ["02010100", "02020100", "0203FF00"]
        # 内层总长 18+19+19=56 与外层 bufflen 一致
        assert sum(len(fr) for fr in frames) == up.bufflen

    def test_task2_missing_di_is_32_full_length(self):
        up = parse_11e3_up(E3_UP_645_TASK2_MISSING)
        assert up.fz_time_text == "2026-11-02 18:15:00"
        frames = split_inner_frames(up.payload)
        assert len(frames) == 3
        third = parse_inner_645(frames[2])
        assert third is not None
        assert third.di_display == "02030100"
        assert third.data == b"\x32\x32\x32"  # 缺项按配置长度 3 写 0x32
        assert third.is_32_fill is True
        assert third.cs_ok is True

    def test_645_no_698_apdu(self):
        # 645 内层不得出现 698 APDU（85 02 ...）
        up = parse_11e3_up(E3_UP_645_TASK1)
        assert b"\x85\x02" not in up.payload


class TestF230:
    def test_task1_report_nested_inner(self):
        r = parse_f230_report(F230_TASK1)
        assert r is not None
        assert r.task_id == 1
        assert r.protocol == 3
        assert r.address == "000000000001"
        assert r.fz_time_text == FZ_TASK1_698
        assert r.datalen == 54
        assert r.cs_ok is True
        up = parse_11e3_up(E3_UP_TASK1)
        assert r.inner == up.payload  # 嵌套帧与 11E3 内层一致

    def test_reject_non_f230(self):
        assert parse_f230_report(INNER_698_TASK1) is None
        assert parse_f230_report(E3_UP_TASK1) is None


class TestF231:
    def test_645_task1_config(self):
        cfg = parse_f231_config(F231_645_TASK1)
        assert cfg is not None
        assert cfg.task_id == 1
        assert cfg.switch_flag == 1
        assert cfg.period_min == 5
        assert cfg.di_displays == ("02010100", "02020100", "0203FF00")
        assert cfg.di_lens == (2, 3, 3)

    def test_645_task2_config(self):
        cfg = parse_f231_config(F231_645_TASK2)
        assert cfg is not None
        assert cfg.task_id == 2
        assert cfg.di_displays == ("02010100", "02020100", "02030100")
        assert cfg.di_lens == (2, 3, 3)


class TestBcd6Time:
    def test_task1_698_fz(self):
        assert bcd6_time_bytes(bytes.fromhex("001017021126")) == FZ_TASK1_698

    def test_645_task2_fz(self):
        assert (
            bcd6_time_bytes(bytes.fromhex("001518021126"))
            == "2026-11-02 18:15:00"
        )


# ---------------------------------------------------------------------------
# 断言函数
# ---------------------------------------------------------------------------


class TestAssert69811e3Chain:
    def test_task1_full_chain_pass(self):
        rep = assert_698_11e3_chain(
            E3_UP_TASK1, EXPECT_TASK1, E3_DOWN_TASK1, F230_TASK1
        )
        assert rep.passed, rep.failures
        assert rep.failures == ()

    def test_task1_up_only_pass(self):
        rep = assert_698_11e3_chain(E3_UP_TASK1, EXPECT_TASK1)
        assert rep.passed, rep.failures

    def test_task0_failure_response_pass(self):
        """task 0 失败响应本身正确：result=2、di_cnt=0、bufflen=0、无内层。"""
        rep = assert_698_11e3_chain(
            E3_UP_TASK0,
            Expect698(task_id=0, fz_time_text=FZ_TASK1_698, oad_lens=()),
            E3_DOWN_TASK0,
        )
        assert rep.passed, rep.failures

    def test_task15_failure_response_pass(self):
        rep = assert_698_11e3_chain(
            E3_UP_TASK15,
            Expect698(task_id=15, fz_time_text=FZ_TASK1_698, oad_lens=()),
            E3_DOWN_TASK15,
        )
        assert rep.passed, rep.failures

    def test_task0_expect_success_fails(self):
        """对失败响应误配成功期望时必须 FAIL（不能误判 PASS）。"""
        rep = assert_698_11e3_chain(
            E3_UP_TASK0,
            Expect698(
                task_id=0, fz_time_text=FZ_TASK1_698, oad_lens=(2, 2, 2)
            ),
        )
        assert not rep.passed
        assert any("result" in f for f in rep.failures)

    def test_wrong_control_0x13_detected(self):
        """根因①：C 域写成 0x13 必须被判 FAIL（QUOK-17 旧 bug）。"""
        broken = bytearray(E3_UP_TASK1)
        broken[30] = 0x13  # 内层 C 域（27 起 + 3）
        rep = assert_698_11e3_chain(bytes(broken), EXPECT_TASK1)
        assert not rep.passed
        assert any("C 域: 期望 0xC3 实际 0x13" in f for f in rep.failures)

    def test_apdu_len_missing_tail_detected(self):
        """根因④：APDU 长度漏算 9 字节尾（0x22→0x19）必须被判 FAIL。"""
        broken = bytearray(E3_UP_TASK1)
        broken[43] = 0x19  # 内层 SEC 长度字节（27 起 + 16）
        rep = assert_698_11e3_chain(bytes(broken), EXPECT_TASK1)
        assert not rep.passed
        assert any("漏算 9 字节尾" in f for f in rep.failures)

    def test_apdu_third_byte_task_id_detected(self):
        """根因②：APDU 第三字节写成任务号必须被判 FAIL。"""
        broken = bytearray(E3_UP_TASK1)
        broken[46] = 0x03  # PIID-ACD 位置（27+19）
        rep = assert_698_11e3_chain(bytes(broken), EXPECT_TASK1)
        assert not rep.passed
        assert any("PIID-ACD" in f for f in rep.failures)

    def test_missing_oad_not_ff_detected(self):
        """缺项数据非 FF 必须被判 FAIL。"""
        broken = bytearray(E3_UP_TASK1)
        broken[67] = 0x00  # OAD3 数据首字节（FFFF→00FF）
        rep = assert_698_11e3_chain(bytes(broken), EXPECT_TASK1)
        assert not rep.passed
        assert any("20000200" in f and "FF" in f for f in rep.failures)

    def test_wrong_timetag_detected(self):
        """根因③：TimeTag 不随冻结时刻必须被判 FAIL。"""
        broken = bytearray(E3_UP_TASK1)
        broken[76] ^= 0xFF  # TimeTag 第 3 字节（27+49）
        rep = assert_698_11e3_chain(bytes(broken), EXPECT_TASK1)
        assert not rep.passed
        assert any("TimeTag" in f for f in rep.failures)

    def test_hardcoded_timetag_from_root_cause_detected(self):
        """TimeTag 硬编码 CA 7D A4 05（旧 bug）必须被判 FAIL。"""
        broken = bytearray(E3_UP_TASK1)
        broken[73:77] = bytes.fromhex("ca7da405")
        rep = assert_698_11e3_chain(bytes(broken), EXPECT_TASK1)
        assert not rep.passed
        assert any("TimeTag" in f for f in rep.failures)

    def test_wrong_task_id_detected(self):
        broken = bytearray(E3_UP_TASK1)
        broken[17] = 2
        rep = assert_698_11e3_chain(bytes(broken), EXPECT_TASK1)
        assert not rep.passed
        assert any("task_id: 期望 1 实际 2" in f for f in rep.failures)

    def test_down_up_mismatch_detected(self):
        rep = assert_698_11e3_chain(
            E3_UP_TASK1, EXPECT_TASK1, E3_DOWN_TASK0
        )
        assert not rep.passed
        assert any("下行 task_id" in f for f in rep.failures)

    def test_f230_mismatch_detected(self):
        broken = bytearray(F230_TASK1)
        broken[13] = 2  # 任务号 1→2
        rep = assert_698_11e3_chain(
            E3_UP_TASK1, EXPECT_TASK1, None, bytes(broken)
        )
        assert not rep.passed
        assert any("F230 任务号" in f for f in rep.failures)

    def test_require_raises_assertion_error(self):
        broken = bytearray(E3_UP_TASK1)
        broken[30] = 0x13
        with pytest.raises(AssertionError) as excinfo:
            from minute_assert.mclt_698 import require_698_11e3_chain

            require_698_11e3_chain(bytes(broken), EXPECT_TASK1)
        assert "C 域" in str(excinfo.value)

    def test_unparseable_frame_fails(self):
        rep = assert_698_11e3_chain(b"\x11\xe3garbage" + bytes(30), EXPECT_TASK1)
        assert not rep.passed
        assert rep.failures == ("11E3 上行帧无法解析（帧头/长度不符）",)
