"""REQS-0032 P1/P2：应用层帧工具门面（facade）单测。

基线：REQS.md v1.2（变更 3）。
- 自动模式（不指定协议）= 严格：confidence 阈值 0.8，低于阈值拒绝并给得分；
- 指定模式（protocol=...）= 宽容：允许错误帧，校验失败进 warnings；
- 日常路由仅 {645, 698.45, 1376.2}，双模/GW 不在范围；
- build：1376.2 语义透传 build_frame_json；645 DI 小端 +33H；698 Get-RequestNormal。
"""
import pytest

from parser_lib.facade import decode, build, verify, DAILY_PROTOCOLS

# 仓库真实样本（校验合法）
F645 = "6812345678901268910833333433AB896745CC16"  # 读应答，DI=00010000 正向有功总电能
F698 = ("688400c30535378109003010f18390006b85033750020200080020210200"
        "002001040000200002000020010200002004020000200a02000000100201"
        "000020020101011c07ea061d0e1e00050000000001011208a30101050000"
        "000001020500000000050000000001021003e81003e80600000000060000"
        "00000000010004d0c1a502010016")


def _f13762_relay() -> str:
    """1376.2 AFN=02H 数据转发，内嵌真实 645 帧（同库 test_10376 造法）。"""
    from parser_lib.adapters.adapter_10376 import build_frame
    f645 = bytes.fromhex(F645)
    appdata = bytes([0x02, len(f645)]) + f645
    return build_frame(afn=0x02, fn=1, direction="down", appdata=appdata).hex().upper()


# ---------- 自动模式（严格） ----------

def test_decode_auto_selects_645():
    r = decode(F645)
    assert r["ok"] is True
    assert r["protocol"] == "645"
    assert r["frame"]["structure"] == "645"


def test_decode_auto_selects_698():
    r = decode(F698)
    assert r["ok"] is True
    assert r["protocol"] == "698.45"


def test_decode_auto_13762_recurses_nested_645():
    r = decode(_f13762_relay())
    assert r["ok"] is True
    assert r["protocol"] == "1376.2"
    nested = r["frame"]["nested"]
    assert len(nested) == 1
    assert nested[0]["structure"] == "645"


def test_decode_auto_refuses_cs_broken_645_with_scores():
    bad = bytes.fromhex(F645)
    bad_cs = bad[:-2] + bytes([bad[-2] ^ 0xFF]) + bad[-1:]
    r = decode(bad_cs)
    assert r["ok"] is False
    assert r["error"] == "unrecognized"
    assert r["scores"]["645"] == pytest.approx(0.4)
    assert "--protocol" in r["hint"]


def test_decode_auto_refuses_truncated_698():
    r = decode(F698[: len(F698) // 2])
    assert r["ok"] is False
    assert r["error"] == "unrecognized"
    assert set(r["scores"]) == set(DAILY_PROTOCOLS)


# ---------- 指定模式（宽容，允许错误帧） ----------

def test_decode_specified_bad_cs_645_still_parses_with_warning():
    bad = bytes.fromhex(F645)
    bad_cs = bad[:-2] + bytes([bad[-2] ^ 0xFF]) + bad[-1:]
    r = decode(bad_cs, protocol="645")
    assert r["ok"] is True
    assert r["protocol"] == "645"
    assert any("CS校验失败" in w for w in r["warnings"])


def test_decode_specified_truncated_698_still_parses_with_warning():
    r = decode(F698[: len(F698) // 2], protocol="698.45")
    assert r["ok"] is True
    assert r["protocol"] == "698.45"
    assert r["warnings"], "残缺帧必须以 warnings 表达问题"


def test_decode_rejects_non_daily_protocol():
    r = decode(F645, protocol="dualmode")
    assert r["ok"] is False
    assert r["error"] == "unsupported_protocol"
    assert "1376.2" in r["hint"]


# ---------- build ----------

def test_build_645_read_di_roundtrip():
    r = build("645", {"addr": "123456789012", "control": "11", "di": "00010000"})
    assert r["ok"] is True
    assert r["protocol"] == "645"
    v = verify(r["frame_hex"], protocol="645",
               expect={"地址域": "123456789012"})
    assert v["ok"] is True and v["verified"] is True
    # DI 语义：数据域为 DI 小端 + 33H → 解回命中"正向有功总电能"
    names = " ".join(i["name"] for i in v["frame"]["items"])
    assert "正向有功总电能" in names


def test_build_13762_semantic_passthrough():
    inner = bytes([0x02, len(bytes.fromhex(F645))]).hex() + F645
    r = build("1376.2", {"afn": "02", "fn": 1, "data": {"raw": inner}})
    assert r["ok"] is True
    v = verify(r["frame_hex"])
    assert v["ok"] is True and v["protocol"] == "1376.2"
    assert len(v["frame"]["nested"]) == 1


def test_build_1103_concurrent_read_f1_f1_roundtrip():
    """REQS-0030 口径 1103 并发抄表：F1H-F1，appdata=协议类型+00+LE16长度+645读帧
    （libs/sim_concentrator/batch.py 同款数据单元结构），构→自动回验命中 1376.2
    且内嵌 645 递归解出。"""
    inner = build("645", {"addr": "123456789012", "control": "11",
                          "di": "00010000"})
    assert inner["ok"] is True
    f645 = bytes.fromhex(inner["frame_hex"])
    appdata = ("0200" + len(f645).to_bytes(2, "little").hex().upper()
               + f645.hex().upper())
    r = build("1376.2", {"afn": "F1", "fn": 1, "data": {"raw": appdata}})
    assert r["ok"] is True
    v = verify(r["frame_hex"])  # 自动模式，不指定协议
    assert v["ok"] is True and v["protocol"] == "1376.2"
    assert v["verified"] is True
    assert len(v["frame"]["nested"]) == 1
    assert v["frame"]["nested"][0]["structure"] == "645"


def test_build_698_get_request_normal_roundtrip():
    # OAD=0201 0200（分钟冻结当前属性示例），GET-RequestNormal 无时标
    r = build("698.45", {"addr": "05353781090030", "ca": "00",
                         "oad": "02010200"})
    assert r["ok"] is True
    v = verify(r["frame_hex"], protocol="698.45")
    assert v["ok"] is True and v["verified"] is True
    assert not v["warnings"], f"合法构帧回验不应有 warnings: {v['warnings']}"


def test_build_rejects_non_daily_protocol():
    r = build("dualmac", {})
    assert r["ok"] is False
    assert r["error"] == "unsupported_protocol"


# ---------- verify（回验） ----------

def test_verify_roundtrip_ok_without_expect():
    r = verify(_f13762_relay())
    assert r["ok"] is True and r["verified"] is True
    assert r["diff"]["missing"] == [] and r["diff"]["mismatched"] == []


def test_verify_expect_mismatch_reports_diff():
    r = verify(F645, expect={"地址域": "FFFFFFFFFFFF"})
    assert r["ok"] is True
    assert r["verified"] is False
    assert len(r["diff"]["mismatched"]) == 1


def test_verify_auto_refuses_garbage():
    bad = bytes.fromhex(F645)
    bad_cs = bad[:-2] + bytes([bad[-2] ^ 0xFF]) + bad[-1:]
    r = verify(bad_cs)
    assert r["ok"] is False and r["error"] == "unrecognized"
