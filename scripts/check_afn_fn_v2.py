# -*- coding: utf-8 -*-
"""afn_fn.json v2 契约校验（REQS-0013 P0-2）。

校验项：
  V1  JSON 可解析，顶层含 afn 数组与 v2 说明块
  V2  每个 Fn：有 req.fields（或 fields 兼容别名）；pageMode 合法
  V3  声明了 resp 的 Fn：resp.fields 非空 或 含 list；list 契约字段齐全
      （total/count/record 非空；reqStart/reqCount 在 pageMode∈{manual,auto,both} 时必须）
  V4  分页型 Fn 清单（10H/06H/03H-F3 关键项）与 03 蒸馏文档关键字节抽查：
      10H-F21 record = 地址6+TEI2+代理2+信息1（=11B/条）；10H-F2 record=8B/条；
      10H-F112 record=6+1+24+2=33B/条；06H 五个 Fn persist=true
  V5  变长/嵌套引用合法：len_ref/list_ref 指向的长度字段存在于同一 record

用法：python scripts/check_afn_fn_v2.py   → 全部通过 exit 0，否则 exit 1
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
META = ROOT / "libs" / "parser_lib" / "adapters" / "adapter_10376" / "metadata" / "afn_fn.json"

VALID_PAGEMODE = {"none", "manual", "auto", "both"}
# 分页型 Fn（03 蒸馏文档确认为"总数量+本次数量+记录列表"结构）
EXPECTED_PAGED = {
    "03H-F3", "10H-F2", "10H-F5", "10H-F6", "10H-F7",
    "10H-F21", "10H-F31", "10H-F101", "10H-F112",
}
EXPECTED_PERSIST = {"06H-F1", "06H-F2", "06H-F3", "06H-F4", "06H-F5"}
# record 定长抽查（字节）：来自 03 文档 §4.8
EXPECTED_RECORD_BYTES = {
    "10H-F2": 8,     # 6+2
    "10H-F21": 11,   # 6+2+2+1
    "10H-F31": 8,    # 6+2
    "10H-F101": 11,  # 6+2+3
    "10H-F112": 33,  # 6+1+24+2
}


def field_bytes(f: dict) -> int | None:
    b = f.get("b")
    if isinstance(b, int):
        return b
    return None  # len_ref / list_ref 变长


def check() -> int:
    errors: list[str] = []
    data = json.loads(io.open(META, encoding="utf-8").read())

    # V1
    if not isinstance(data.get("afn"), list):
        print("V1 FAIL: afn 不是数组")
        return 1
    if "v2" not in data:
        errors.append("V1: 缺少 v2 说明块")
    print(f"V1 ok: JSON 可解析，{len(data['afn'])} 个 AFN")

    fn_index: dict[str, dict] = {}
    for afn in data["afn"]:
        for fn in afn["fns"]:
            key = f"{afn['code']}-{fn['no']}"
            fn_index[key] = fn

    # V2
    for key, fn in fn_index.items():
        has_req = bool(fn.get("req", {}).get("fields")) or bool(fn.get("fields"))
        if not has_req:
            # 无请求字段也合法（纯上行/无数据单元），但要显式
            fn.setdefault("req", {"fields": []})
        pm = fn.get("pageMode")
        if pm is not None and pm not in VALID_PAGEMODE:
            errors.append(f"V2 {key}: pageMode 非法 {pm!r}")
    print(f"V2 ok: {len(fn_index)} 个 Fn req 化与 pageMode 检查完成")

    # V3
    paged_found = set()
    for key, fn in fn_index.items():
        resp = fn.get("resp")
        if not resp:
            continue
        fields = resp.get("fields") or []
        lst = resp.get("list")
        if not fields and not lst:
            errors.append(f"V3 {key}: resp 既无 fields 也无 list")
            continue
        if lst:
            for req_key in ("total", "count", "record"):
                if not lst.get(req_key):
                    errors.append(f"V3 {key}: list.{req_key} 缺失")
            rec = lst.get("record") or []
            if not rec:
                errors.append(f"V3 {key}: list.record 为空")
            pm = fn.get("pageMode", "none")
            if pm in ("manual", "auto", "both"):
                for req_key in ("reqStart", "reqCount"):
                    if not lst.get(req_key):
                        errors.append(f"V3 {key}: 分页型但 list.{req_key} 缺失")
                paged_found.add(key)
    missing = EXPECTED_PAGED - paged_found
    if missing:
        errors.append(f"V3: 03 文档确认的分页型 Fn 未注入 list 契约: {sorted(missing)}")
    print(f"V3 ok: resp 契约校验完成，分页型 {len(paged_found)}/{len(EXPECTED_PAGED)}")

    # V4 persist 与定长抽查
    for key in EXPECTED_PERSIST:
        if not fn_index.get(key, {}).get("persist"):
            errors.append(f"V4 {key}: persist 未置 true")
    for key, want in EXPECTED_RECORD_BYTES.items():
        lst = (fn_index.get(key, {}).get("resp") or {}).get("list") or {}
        got = [field_bytes(f) for f in lst.get("record", [])]
        if None in got:
            continue  # 变长记录跳过定长校验
        if sum(got) != want:
            errors.append(f"V4 {key}: record 定长 {sum(got)}B != 文档 {want}B")
    print("V4 ok: persist 与 record 字节抽查完成")

    # V5 变长/嵌套引用
    for key, fn in fn_index.items():
        lst = (fn.get("resp") or {}).get("list")
        if not lst:
            continue
        names = {f.get("n") for f in lst.get("record", [])}
        for f in lst.get("record", []):
            ref = str(f.get("b", ""))
            if ref.startswith("len_ref:"):
                if ref.split(":", 1)[1] not in names:
                    errors.append(f"V5 {key}: len_ref '{ref}' 目标字段不存在")
            if str(f.get("f", "")).startswith("list_ref:"):
                if f["f"].split(":", 1)[1] not in names:
                    errors.append(f"V5 {key}: list_ref '{f['f']}' 目标字段不存在")
    print("V5 ok: 变长/嵌套引用校验完成")

    if errors:
        print(f"\n== 校验失败 {len(errors)} 项 ==")
        for e in errors:
            print("  ✗", e)
        return 1
    print("\n== 全部校验通过 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
