# -*- coding: utf-8 -*-
"""REQS-0013 P0-3 测试：record_extractor（契约驱动） + store（sqlite 分层）。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "libs"))

from sim_concentrator.record_extractor import extract_response  # noqa: E402
from sim_concentrator.store import ListenerStore  # noqa: E402


def _resp(afn: str, fn: str) -> dict:
    meta = json.loads(
        (ROOT / "libs/parser_lib/adapters/adapter_10376/metadata/afn_fn.json")
        .read_text(encoding="utf-8"))
    for a in meta["afn"]:
        if a["code"] == afn:
            for f in a["fns"]:
                if f["no"] == fn:
                    return f.get("resp", {})
    raise AssertionError(f"未找到 {afn}-{fn}")


def _le(n: int, size: int) -> bytes:
    return n.to_bytes(size, "little")


def test_f21_network_topology():
    """10H-F21：头(总数量2B+起始序号2B+本次n) + 记录(地址6+TEI2+代理2+信息1)。"""
    resp = _resp("10H", "F21")
    # 1 条记录：地址 0x010203040506，TEI=0x0001，代理=0x0002，信息=0x21(层级1 角色STA)
    appdata = _le(1, 2) + _le(0, 2) + bytes([1]) + \
        bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06]) + _le(1, 2) + _le(2, 2) + bytes([0x21])
    out = extract_response(appdata, resp)
    assert out["head"]["节点总数量"] == 1, out["head"]
    assert out["head"]["本次应答的节点数量n"] == 1
    assert len(out["records"]) == 1
    r = out["records"][0]
    assert r["节点地址__hex"] == "010203040506"
    assert r["节点标识TEI"] == 1
    assert r["代理节点标识"] == 2
    assert r["节点信息"] == 0x21
    assert r["节点信息__hex"] == "21"
    assert out["remaining"] == 0
    assert not out["warnings"]


def test_f2_node_info_record_len8():
    """10H-F2：记录 8B（地址 BCD6 + 信息2）。"""
    resp = _resp("10H", "F2")
    appdata = _le(1, 2) + bytes([1]) + bytes.fromhex("123456789012") + _le(0x1234, 2)
    out = extract_response(appdata, resp)
    assert len(out["records"]) == 1
    r = out["records"][0]
    # BCD 反序读：低字节在前 12 34 56 78 90 12 → 反序 12 90 78 56 34 12
    assert r["从节点地址"] == "129078563412", r["从节点地址"]
    assert r["从节点信息"]["hex"] == "3412"  # BS 2B 保留 hex


def test_f7_variable_len_id():
    """10H-F7：变长 ID（len_ref:模块ID号长度）。"""
    resp = _resp("10H", "F7")
    # 头(总数2+本次1) + 记录：地址6 + 类型1 + 厂商2 + 长度1(=3) + 格式1 + ID3
    appdata = _le(1, 2) + bytes([1]) + \
        bytes.fromhex("010203040506") + bytes([0x11]) + b"AB" + bytes([3]) + bytes([1]) + b"XYZ"
    out = extract_response(appdata, resp)
    assert len(out["records"]) == 1
    r = out["records"][0]
    assert r["模块ID号长度"] == 3
    assert r["模块ID号__hex"] == "58595A"  # XYZ
    assert out["remaining"] == 0


def test_f112_record_len33():
    """10H-F112：记录 33B（地址6+设备类型1+芯片ID24+版本2）。"""
    resp = _resp("10H", "F112")
    appdata = _le(1, 2) + _le(0, 2) + bytes([1]) + \
        bytes.fromhex("010203040506") + bytes([0x02]) + bytes(range(24)) + bytes([0x12, 0x34])
    out = extract_response(appdata, resp)
    assert len(out["records"]) == 1
    assert out["records"][0]["芯片ID信息__hex"].startswith("000102")
    # BCD 2B 低字节在前：传输 12 34 → 反序 34 12 → BCD "3412"
    assert out["records"][0]["芯片软件版本信息"] == "3412"


def test_store_layers():
    """store 持久层 + 临时层基本读写。"""
    with tempfile.TemporaryDirectory() as td:
        s = ListenerStore(Path(td) / "t.sqlite")
        fid = s.add_frame({"session_id": "sc-1", "seq": 1, "dir": "rx",
                            "afn": "06", "fn": "F4", "updown": "up",
                            "frame_hex": "68", "parsed": {"k": 1}})
        assert fid is not None
        eid = s.add_report_event(frame_id=fid, afn="06", fn="F4",
                                 event_type="上报从节点信息及设备类型",
                                 payload={"head": {"上报从节点的数量n": 1},
                                          "records": [{"从节点通信地址": "123"}]})
        assert eid is not None
        sid = s.open_snapshot(afn="10", fn="F21", mode="auto")
        assert sid is not None
        iid = s.add_snapshot_item(sid, 0, "010203040506",
                                  {"节点标识TEI": 1}, frame_id=fid)
        assert iid is not None
        s.close_snapshot(sid, status="done", total=1, item_count=1)

        snaps = s.list_snapshots(afn="10", fn="F21")
        assert len(snaps) == 1 and snaps[0]["status"] == "done"
        items = s.snapshot_items(sid)
        assert len(items) == 1 and items[0]["addr"] == "010203040506"
        evts = s.list_report_events()
        assert len(evts) == 1
        # frame_log 证据链
        frames = s.query("SELECT * FROM frame_log WHERE afn='06' AND fn='F4'")
        assert len(frames) == 1
        s.close()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\n== {passed}/{len(fns)} 通过 ==")
