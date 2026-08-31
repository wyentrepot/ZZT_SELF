# -*- coding: utf-8 -*-
"""响应记录提取器（REQS-0013 P0-3）：用 afn_fn.json v2 的 resp 契约
通用解析 1376.2 上行帧应用数据 → 头部标量 + 记录行列表。

设计：库驱动而非每 Fn 手写解析——契约（resp.fields / resp.list.record）
描述每个字段的名称/格式/字节长度，本模块按契约切字节。
变长字段 b="len_ref:<字段名>"（同记录内长度字段）；嵌套列表 f="list_ref:<数量字段名>"。

字节序（Q/GDW 1376.2 链路层）：低位在前、低字节在前：
  - BIN  多字节按小端取整
  - BCD  地址类 6 字节传输序为低字节在前 → 展示前反序再按 BCD 读
用法：
    from sim_concentrator.record_extractor import extract_response
    out = extract_response(appdata_bytes, resp_contract)
    # {"head": {...}, "records": [...], "consumed": n, "warnings": [...]}
"""
from __future__ import annotations

from typing import Any, Optional


def _bcd_str(b: bytes) -> str:
    return "".join(f"{x >> 4:x}{x & 0xF:x}" for x in b)


def _decode_value(fmt: str, raw: bytes) -> Any:
    if fmt == "BIN":
        return int.from_bytes(raw, "little")
    if fmt == "BCD":
        return _bcd_str(raw[::-1])  # 传输低字节在前 → 反序后按 BCD 读
    if fmt == "ASCII":
        return raw.decode("ascii", errors="replace").strip()
    # BS / 其他：保留 hex 与二进制，交由展示层解释
    return {"hex": raw.hex().upper(), "bits": f"{int.from_bytes(raw, 'little'):b}"}


def _read_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, dict) and "hex" in value:
        return int(value["hex"], 16)
    if isinstance(value, str):
        s = value.strip()
        try:
            return int(s)
        except ValueError:
            try:
                return int(s, 16)
            except ValueError:
                return 0
    return 0


def _parse_record(buf: bytes, record: list[dict], warnings: list[str]) -> Optional[dict]:
    """按 record 契约从 buf 头部解析一条记录；字节不足返回 None。"""
    row: dict[str, Any] = {}
    pos = 0
    for f in record:
        name = f.get("n", "")
        fmt = str(f.get("f", "BIN"))
        b = f.get("b")
        if isinstance(b, str) and b.startswith("len_ref:"):
            len_field = b.split(":", 1)[1]
            n = _read_int(row.get(len_field, 0))
        else:
            n = int(b or 0)
        if fmt.startswith("list_ref:"):
            count_field = fmt.split(":", 1)[1]
            m = _read_int(row.get(count_field, 0))
            sub_record = f.get("sub_record") or []
            subs = []
            for _ in range(m):
                sub = _parse_record(buf[pos:], sub_record, warnings)
                if sub is None:
                    warnings.append(f"{name}: 嵌套记录字节不足（pos={pos}）")
                    return None
                subs.append(sub)
                pos += sub.pop("_consumed", 0)
            row[name] = subs
            continue
        if pos + n > len(buf):
            warnings.append(f"{name}: 字节不足（需 {n}B，剩 {len(buf) - pos}B）")
            return None
        raw = buf[pos:pos + n]
        row[name] = _decode_value("BIN" if fmt.startswith("list_ref") else fmt, raw) if n else ""
        row[f"{name}__hex"] = raw.hex().upper()
        pos += n
    row["_consumed"] = pos
    return row


def extract_response(appdata: bytes, resp: dict) -> dict:
    """按 resp 契约解析应用数据。

    appdata: 上行帧 AFN/DT 之后的纯数据单元字节。
    resp: afn_fn.json v2 的 fn["resp"]（fields + list）。
    """
    warnings: list[str] = []
    buf = bytes(appdata or b"")
    pos = 0
    head: dict[str, Any] = {}

    for f in resp.get("fields") or []:
        name = f.get("n", "")
        fmt = str(f.get("f", "BIN"))
        b = f.get("b")
        n = int(b or 0) if not isinstance(b, str) else 0
        if isinstance(b, str) and b.startswith("len_ref:"):
            n = _read_int(head.get(b.split(":", 1)[1], 0))
        if pos + n > len(buf):
            warnings.append(f"head.{name}: 字节不足（需 {n}B，剩 {len(buf) - pos}B）")
            break
        raw = buf[pos:pos + n]
        head[name] = _decode_value(fmt, raw)
        head[f"{name}__hex"] = raw.hex().upper()
        pos += n

    records: list[dict] = []
    lst = resp.get("list")
    consumed_total = pos
    if lst:
        record = lst.get("record") or []
        # 记录区起点：头部之后；若 count 缺失则尽力按定长循环
        try:
            count = _read_int(head.get(lst.get("count", ""), 0))
        except Exception:
            count = 0
        for i in range(count):
            row = _parse_record(buf[pos:], record, warnings)
            if row is None:
                break
            row["_seq_index"] = i
            consumed = row.pop("_consumed", 0)
            records.append(row)
            pos += consumed
        consumed_total = pos

    return {
        "head": head,
        "records": records,
        "consumed": consumed_total,
        "remaining": len(buf) - consumed_total,
        "warnings": warnings,
    }
