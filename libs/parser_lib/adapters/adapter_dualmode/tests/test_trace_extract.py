"""4-3 追踪提取件（trace_extract）单测：合成结构用例 + 真机样本回归。

样本回归（SampleRegression）依赖 CLR（pythonnet）与 reqs/0009-listener-flow-trace
/samples/ 三段真机样本；环境不具备时跳过。回归断言与 DESIGN §10.1 校准记录一一对应。
"""
import json
import re
from pathlib import Path

import pytest

from parser_lib.adapters.adapter_dualmode.trace_extract import (
    extract_trace_fields,
    _scan_645,
    _scan_698_tokens,
)

HERE = Path(__file__).resolve().parent
REPO_ROOT = Path(HERE).parents[4]  # tests -> adapter_dualmode -> adapters -> parser_lib -> libs -> repo
SAMPLE_DIR = REPO_ROOT / "reqs" / "0009-listener-flow-trace" / "samples"
SAMPLE_FILES = sorted(SAMPLE_DIR.glob("sample-*.txt"))


# ---------------------------------------------------------------------------
# 合成结构用例（不依赖 DLL）
# ---------------------------------------------------------------------------

def _meter_app_raw(seq, proto_type, data, *, timeout=0x28, option=0x00, config=0,
                   up=False, resp_bitmap=None):
    """构造抄表族 APP_RAW：通用头 + 业务头（8B）+ DATA。"""
    header_len = 8
    b0 = 1 | ((header_len & 0x03) << 6)
    if up:
        b1 = ((header_len >> 2) & 0x0F) | (0 << 4)   # 应答状态=0
        b2 = (proto_type & 0x0F) | (((len(data) >> 8) & 0x0F) << 4)
        b3 = len(data) & 0xFF
        head = bytes([b0, b1, b2, b3, seq & 0xFF, (seq >> 8) & 0xFF,
                      resp_bitmap & 0xFF, (resp_bitmap >> 8) & 0xFF]) if resp_bitmap is not None else \
               bytes([b0, b1, b2, b3, seq & 0xFF, (seq >> 8) & 0xFF, 0, 0])
    else:
        b1 = ((header_len >> 2) & 0x0F) | ((config & 0x0F) << 4)
        b2 = (proto_type & 0x0F) | (((len(data) >> 8) & 0x0F) << 4)
        b3 = len(data) & 0xFF
        head = bytes([b0, b1, b2, b3, seq & 0xFF, (seq >> 8) & 0xFF, timeout, option])
    return bytes([0x11, 0x00, 0x03, 0x00]) + head + data


def _frame645(addr_bcd_le, ctrl=0x11, data=b"", wake=True):
    """构造 645-2007 帧（无 CS 校验字节填充为 0）。"""
    body = bytes([0x68]) + addr_bcd_le + bytes([ctrl, len(data)]) + data
    cs = sum(body[1:]) & 0xFF
    frame = body + bytes([cs, 0x16])
    return (b"\xfe" * 4 if wake else b"") + frame


def test_meter_downlink_seq_timeout_config():
    raw = _meter_app_raw(0x1EC2, 2, b"\x68" + b"\x00" * 20, timeout=0x28, config=0b0111)
    ext = extract_trace_fields("0003", raw, src_tei="001")
    assert ext.msg_seq == 0x1EC2
    assert ext.direction == "down"
    assert ext.timeout_ms == 4000
    assert ext.retry_cfg == 0b0111
    assert ext.proto_type == 2


def test_meter_uplink_seq_and_bitmap():
    raw = _meter_app_raw(0x1EC2, 2, b"\x68" + b"\x00" * 20, up=True, resp_bitmap=0x0005)
    ext = extract_trace_fields("0003", raw, src_tei="087")
    assert ext.msg_seq == 0x1EC2
    assert ext.direction == "up"
    assert ext.resp_bitmap == 0x0005
    assert ext.timeout_ms is None and ext.retry_cfg is None


def test_meter_downlink_645_targets():
    addr = bytes.fromhex("010000000000")  # 表地址 000000000001（BCD 小端）
    data = _frame645(addr)
    raw = _meter_app_raw(0x0201, 2, data)
    ext = extract_trace_fields("0001", raw, src_tei="001")
    assert ext.targets == ["000000000001"]
    assert ext.proto_name == "DL/T645-2007"


def test_645_denied_classification():
    addr = bytes.fromhex("010000000000")
    frames = _frame645(addr, ctrl=0x11) + _frame645(addr, ctrl=0xC1)
    results = _scan_645(frames)
    assert [r[2] for r in results] == [False, True]


def test_meter_uplink_645_denied_response():
    addr = bytes.fromhex("010000000000")
    data = _frame645(addr, ctrl=0xC1)
    raw = _meter_app_raw(0x1234, 2, data, up=True, resp_bitmap=0x0001)
    ext = extract_trace_fields("0003", raw, src_tei="087")
    assert ext.responses == [{"addr": "000000000001", "denied": True}]


def test_meter_downlink_698_oad_targets():
    # 真机形态：698 帧 + APDU 含 7 个 5B OAD 条目（样本 406737 同构）
    entries = b"".join(bytes([0x00, x, 0x02, 0x01, 0x00]) for x in (0x20, 0x30, 0x40))
    data = bytes.fromhex("685D004305163568000070 107A86".replace(" ", "")) + \
        bytes.fromhex("10003905032750020200012021" + "02001C07EA061D0A2D000700") + entries + \
        bytes.fromhex("0110") + bytes.fromhex("F0D55698838E08158F013B87D49A8CD7") + \
        bytes.fromhex("B3C716")
    raw = _meter_app_raw(0x1EC2, 3, data)
    ext = extract_trace_fields("0003", raw, src_tei="001")
    assert ext.targets == ["00200201", "00300201", "00400201"]


def test_698_withlist_entries_and_last_without_separator():
    # WithList（choice=03）：身份 = OAD 条目列表；响应末条目分隔符可省略
    request = bytes.fromhex("10003905" + "03275002020001202102001C07EA061D0A2D000700" +
                            "0020020100" + "0030020100" + "0110")
    assert _scan_698_tokens(request) == ["00200201", "00300201"]
    response = bytes.fromhex("90005285" + "0327500202000700" +
                             "0020020100" + "0030020100" + "00400201" +
                             "0101060000000005")
    assert _scan_698_tokens(response) == ["00200201", "00300201", "00400201"]


def test_698_single_oad_request_and_response():
    # 单 OAD 形态（choice=01/02，真机样本 B 同构）：身份 = tag+choice 后 4B OAD
    request = bytes.fromhex("1000090502" + "1E014000" + "020000" + "0110")
    assert _scan_698_tokens(request) == ["1E014000"]
    response = bytes.fromhex("90000985" + "021E0140" + "0002" + "0000" + "0110")
    assert _scan_698_tokens(response) == ["1E014000"]


def test_698_timestamp_region_produces_no_false_entries():
    # RSD 区时间戳/长度字段不产生假条目（样本回归中的误扫防线）
    apdu = bytes.fromhex("10003905" + "03275002020001202102001C07EA061D0A2D000700" +
                         "0110F0D55698")
    assert _scan_698_tokens(apdu) == []


def test_0020_confirm_and_deny():
    def app20(dir_bit, confirm_bit, seq):
        b1 = (dir_bit << 4) | (confirm_bit << 5)
        return bytes([0x11, 0x20, 0x00, 0x00, 0x01, b1, seq & 0xFF, (seq >> 8) & 0xFF])
    ext = extract_trace_fields("0020", app20(0, 1, 0x0113), src_tei="001")
    assert ext.msg_seq == 0x0113 and ext.direction == "down" and ext.confirm is True
    ext = extract_trace_fields("0020", app20(1, 0, 0x0113), src_tei="0D8")
    assert ext.msg_seq == 0x0113 and ext.direction == "up" and ext.confirm is False


def test_0020_direction_fallback_without_src():
    # 无 src_tei 时按业务头方向位兜底（04 文档 §4.6）
    raw = bytes([0x11, 0x20, 0x00, 0x00, 0x01, 0x30, 0x23, 0x01])
    ext = extract_trace_fields("0020", raw)
    assert ext.direction == "up" and ext.msg_seq == 0x0123 and ext.confirm is True


def test_0008_seq_and_meter_addr():
    raw = bytes([0x11, 0x08, 0x00, 0x00, 0x01, 0x10, 0x00, 0x00,
                 0x21, 0x03, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
    ext = extract_trace_fields("0008", raw, src_tei="035")
    assert ext.msg_seq == 0x0321
    assert ext.direction == "up"
    assert ext.responses == [{"addr": "665544332211", "denied": False}]


def test_00a1_seq_layout():
    # 样本 406785：11 A1 00 00 01 23 C5 07 ... -> seq=0x07C5（业务报文 [2:4]）
    raw = bytes.fromhex("11A100000123C5073000090535400302")
    ext = extract_trace_fields("00A1", raw, src_tei="001")
    assert ext.msg_seq == 0x07C5
    assert ext.direction == "down"


def test_unsupported_app_id_returns_none():
    assert extract_trace_fields("0004", bytes.fromhex("1104000001080090")) is None
    assert extract_trace_fields("0003", None) is None
    assert extract_trace_fields("0003", b"\x11") is None


# ---------------------------------------------------------------------------
# 真机样本回归（需 CLR + 样本文件；断言 = DESIGN §10.1）
# ---------------------------------------------------------------------------

def _runtime_supported():
    from shared.dotnet_runtime import probe_dotnet_runtime
    return probe_dotnet_runtime().supported


@pytest.mark.skipif(not _runtime_supported(), reason="CLR 不可用，跳过真机样本回归")
@pytest.mark.skipif(not SAMPLE_FILES, reason="校准样本不存在")
class TestSampleRegression:
    """样本回归：三段 2276 帧（DLL parse_simple）。"""

    @staticmethod
    def _iter_sample_frames():
        from shared.dotnet_parser import DotNetHplcParser
        dll = REPO_ROOT / "libs" / "shared" / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
        parser = DotNetHplcParser(dll)
        for path in SAMPLE_FILES:
            for line in path.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\[[^\]]+\]\[([^\]]+)\](.*)$", line.strip())
                if not m:
                    continue
                try:
                    raw = bytes.fromhex("".join(m.group(2).split()))
                except ValueError:
                    continue
                try:
                    simple = json.loads(parser.parse_simple(raw))
                except Exception:
                    continue
                yield m.group(1), raw, simple

    @classmethod
    def _collect(cls):
        frames = []
        for t, raw, simple in cls._iter_sample_frames():
            app_raw = None
            if simple.get("APP_RAW"):
                try:
                    app_raw = bytes.fromhex(simple["APP_RAW"])
                except ValueError:
                    app_raw = None
            frames.append({
                "t": t, "raw": raw, "simple": simple, "app_raw": app_raw,
                "src": simple.get("SRC"), "dst": simple.get("DST"),
                "app_id": simple.get("APP_ID"), "frm": simple.get("FrmType"),
            })
        return frames

    def test_0003_seq_at_business_4_6_and_echo_pairing(self):
        """§10.1：seq=APP_RAW[8:10]（业务报文[4:6]），上行回填 100% 配对。"""
        frames = self._collect()
        down_seqs, up_seqs = set(), []
        for f in frames:
            if f["app_id"] != "0003" or not f["app_raw"] or len(f["app_raw"]) < 10:
                continue
            ext = extract_trace_fields("0003", f["app_raw"], src_tei=f["src"])
            assert ext is not None and ext.msg_seq is not None
            if ext.direction == "down":
                down_seqs.add(ext.msg_seq)
            else:
                up_seqs.append(ext.msg_seq)
        # 序号非静态：样本内应有大量不同取值（0x0201 误读已修正）
        assert len(down_seqs) >= 50
        assert not (down_seqs == {0x0201})
        # 上行序号回填：全部命中下行序号集合（142/142）
        assert up_seqs and all(seq in down_seqs for seq in up_seqs)

    def test_0003_698_oad_echo_consistency(self):
        """§10.1：698 OAD token 上行回显 ⊆ 下行目标集（对账键成立）。"""
        frames = self._collect()
        down_targets = {}
        mismatch = total = 0
        for f in frames:
            if f["app_id"] != "0003" or not f["app_raw"] or len(f["app_raw"]) < 12:
                continue
            ext = extract_trace_fields("0003", f["app_raw"], src_tei=f["src"])
            if ext.direction == "down" and ext.targets:
                down_targets[ext.msg_seq] = set(ext.targets)
            elif ext.direction == "up" and ext.responses and ext.msg_seq in down_targets:
                total += 1
                if not {r["addr"] for r in ext.responses} <= down_targets[ext.msg_seq]:
                    mismatch += 1
        assert total >= 50
        assert mismatch == 0

    def test_00a1_seq_and_echo(self):
        """§10.1：00A1 seq=APP_RAW[6:8]，上行 echo（含窗口截断容差）。"""
        frames = self._collect()
        down_seqs, up_seqs = set(), []
        for f in frames:
            if f["app_id"] != "00A1" or not f["app_raw"] or len(f["app_raw"]) < 8:
                continue
            ext = extract_trace_fields("00A1", f["app_raw"], src_tei=f["src"])
            assert ext.msg_seq is not None
            if ext.direction == "down":
                down_seqs.add(ext.msg_seq)
            else:
                up_seqs.append(ext.msg_seq)
        assert len(down_seqs) >= 5
        assert up_seqs and all(seq in down_seqs for seq in up_seqs)

    def test_ack_peer_matches_preceding_downlink_dst(self):
        """§10.1：ACK [27..28]（BE12）= 被确认帧 STA 端 TEI。

        以"DLL DST=001 的 ACK"子集做闭环：其 peer 应等于紧邻前一下行帧的 DST。
        """
        from parser_lib.adapters.adapter_dualmode.trace_extract import ack_peer_tei
        frames = self._collect()
        by_index = {i: f for i, f in enumerate(frames)}
        checked = 0
        for i, f in enumerate(frames):
            if f["frm"] != "ACK" or f["simple"].get("DST") != "001":
                continue
            for back in range(1, 6):
                prev = by_index.get(i - back)
                if prev and prev["src"] == "001" and prev["dst"] and prev["dst"] != "001":
                    peer = ack_peer_tei(f["raw"])
                    assert peer is not None
                    assert f"{peer:03X}" == prev["dst"], (
                        f"ACK#{i} peer {peer:03X} != 前下行 DST {prev['dst']}"
                    )
                    checked += 1
                    break
        assert checked >= 10
