# -*- coding: utf-8 -*-
"""REQS-0013 G2：10H 路由查询全部 Fn 的 resp 契约解析验证。

用 03_QGDW10376.2_全帧类型.md §4.8 的字节结构构造样例，验证 record_extractor
按 v2 契约正确切分头字段与记录行。覆盖 7 个分页列表 + 标量项。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "libs"))

from sim_concentrator.record_extractor import extract_response  # noqa: E402


def _resp(fn: str) -> dict:
    meta = json.loads(
        (ROOT / "libs/parser_lib/adapters/adapter_10376/metadata/afn_fn.json")
        .read_text(encoding="utf-8"))
    for a in meta["afn"]:
        if a["code"] == "10H":
            for f in a["fns"]:
                if f["no"] == fn:
                    return f.get("resp", {})
    raise AssertionError(f"10H-{fn} 无 resp")


def _le(n, size): return n.to_bytes(size, "little")


def _head(total, n, start=0):
    return _le(total, 2) + _le(start, 2) + bytes([n])


def test_f2_node_info():
    resp = _resp("F2")
    appdata = _le(1, 2) + bytes([1]) + bytes.fromhex("123456789012") + _le(0x34, 2)
    out = extract_response(appdata, resp)
    assert out["head"]["从节点总数量"] == 1
    assert len(out["records"]) == 1
    assert out["records"][0]["从节点地址"] == "129078563412"
    assert out["records"][0]["从节点信息"]["hex"] == "3400"


def test_f5_same_as_f2():
    resp = _resp("F5")
    assert resp.get("list"), "F5 应复用 F2 的 list"
    appdata = _le(2, 2) + bytes([2]) + bytes.fromhex("010203040506") + _le(1, 2) + \
        bytes.fromhex("020304050607") + _le(2, 2)
    out = extract_response(appdata, resp)
    assert len(out["records"]) == 2


def test_f6_same_as_f2():
    resp = _resp("F6")
    assert resp.get("list"), "F6 应复用 F2 的 list"


def test_f7_variable_id():
    resp = _resp("F7")
    appdata = _le(1, 2) + bytes([1]) + \
        bytes.fromhex("010203040506") + bytes([0x11]) + b"AB" + bytes([3]) + bytes([1]) + b"XYZ"
    out = extract_response(appdata, resp)
    r = out["records"][0]
    assert r["模块ID号长度"] == 3
    assert r["模块ID号__hex"] == "58595A"
    assert r["模块厂商代码"] == "AB"


def test_f31_phase():
    resp = _resp("F31")
    appdata = _head(2, 2) + \
        bytes.fromhex("010203040506") + _le(0x0007, 2) + \
        bytes.fromhex("010203040507") + _le(0x0010, 2)
    out = extract_response(appdata, resp)
    assert len(out["records"]) == 2
    assert out["records"][0]["相线信息"] == 0x0007
    assert out["records"][1]["相线信息"] == 0x0010


def test_f101_micropower():
    resp = _resp("F101")
    appdata = _le(1, 2) + bytes([1]) + \
        bytes.fromhex("123456789012") + _le(1, 2) + bytes([1, 2, 3])
    out = extract_response(appdata, resp)
    r = out["records"][0]
    assert r["从节点地址"] == "129078563412"
    assert r["软件版本信息__hex"] == "010203"


def test_f112_chip():
    resp = _resp("F112")
    appdata = _head(1, 1) + bytes.fromhex("010203040506") + bytes([0x02]) + \
        bytes(range(24)) + bytes([0x12, 0x34])
    out = extract_response(appdata, resp)
    assert len(out["records"]) == 1
    assert out["records"][0]["芯片软件版本信息"] == "3412"


def test_scalar_f1():
    resp = _resp("F1")
    appdata = _le(32, 2) + _le(64, 2)
    out = extract_response(appdata, resp)
    assert out["head"]["从节点总数量"] == 32
    assert out["head"]["路由支持最大从节点数量"] == 64
    assert not out["records"]


def test_scalar_f4_runstate():
    resp = _resp("F4")
    # 12 个字段：状态字1 + 数量3×2 + 开关1 + 速率2 + 3相级别3 + 3相步骤3 = 16B
    appdata = bytes([0x01]) + _le(32, 2) + _le(30, 2) + _le(10, 2) + bytes([0x00]) + \
        _le(9600, 2) + bytes([1, 2, 3]) + bytes([1, 2, 3])
    out = extract_response(appdata, resp)
    assert out["head"]["从节点总数量"] == 32
    assert out["head"]["第1相中继级别"] == 1
    assert out["head"]["第3相工作步骤"] == 3
    assert out["remaining"] == 0


def test_scalar_f9_scale():
    resp = _resp("F9")
    out = extract_response(_le(128, 2), resp)
    assert out["head"]["网络规模"] == 128


def test_scalar_f100_scale():
    resp = _resp("F100")
    out = extract_response(_le(64, 2), resp)
    assert out["head"]["网络规模"] == 64


def test_scalar_f40_id():
    resp = _resp("F40")
    # 设备类型1 + 地址6 + ID类型1 + ID长度1 + ID信息M
    appdata = bytes([2]) + bytes.fromhex("010203040506") + bytes([1]) + bytes([3]) + b"ABC"
    out = extract_response(appdata, resp)
    assert out["head"]["ID长度"] == 3
    assert out["head"]["ID信息__hex"] == "414243"


def test_scalar_f111_network():
    resp = _resp("F111")
    # 数量1 + 本节点NID3 + 主节点地址6 + 邻居NID3
    appdata = bytes([1]) + bytes([1, 2, 3]) + bytes.fromhex("010203040506") + bytes([9, 9, 9])
    out = extract_response(appdata, resp)
    assert out["head"]["多网络节点总数量n"] == 1
    assert len(out["records"]) == 1
    assert out["records"][0]["邻居节点网络标识号NID__hex"] == "090909"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\n== {passed}/{len(fns)} 通过 ==")
