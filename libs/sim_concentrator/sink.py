# -*- coding: utf-8 -*-
"""1376.2 收发库 sink（REQS-0013 P0-3）：把 journal 帧条目灌入 ListenerStore。

职责：
  1. 每帧 → frame_log（证据链）
  2. 上行帧（updown=up）且 AFN=06H → 按契约/适配器提取并落库：
       F1/F3/F4/F5 → report_event；F2 → report_meter_data；
       F230 → report_mclt_data（分钟采集上报，REQS-0037，入库并自动应答 00H-F1）
  3. 上行响应帧（AFN=10H / 03H-F3 等分页型）→ 由调用方经 snapshot API 显式落库
     （查询结果归属某次遍历快照，需 snapshot_id 上下文，故不在此自动写）。

纯函数 + 幂等：重复 ingest 同一 entry 会产生重复 frame_log 行（无去重键），
调用方应保证每个 entry 只 ingest 一次（与 journal.append 一一对应）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_contract() -> dict:
    meta = json.loads(
        (_ROOT / "libs/parser_lib/adapters/adapter_10376/metadata/afn_fn.json")
        .read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    for a in meta["afn"]:
        for f in a["fns"]:
            index[f"{a['code']}-{f['no']}"] = f
    return index


_CONTRACT = _load_contract()


def _contract_key(afn: Optional[str], fn: Optional[str]) -> Optional[str]:
    """把 journal 的 afn('06')/fn('F1') 归一为契约 key('06H-F1')。"""
    if not afn or not fn:
        return None
    a = str(afn).strip().upper()
    if not a.endswith("H"):
        a = a + "H"
    f = str(fn).strip().upper()
    if not f.startswith("F"):
        f = "F" + f
    return f"{a}-{f}"

_EVENT_FN = {
    "F1": "上报从节点信息",
    "F3": "上报路由工况变动",
    "F4": "上报从节点信息及设备类型",
    "F5": "上报从节点事件",
}


def _f230_payload(entry: dict) -> dict:
    """从 journal entry 提取 06H-F230 分钟采集上报明细（REQS-0037）。

    adapter_10376 已把 F230 应用层解析为 items（任务号/从节点地址/通信协议类型/
    采集时间/报文长度/报文内容/嵌套帧），这里仅做提取与规整：
      - task_id / node_addr / payload_hex：字符串
      - proto_type：协议代码 "2"(645-2007) / "3"(698.45)，原值透传
      - collect_time：BCD 采集（冻结）时间，规整为 "yyyy-MM-dd HH:mm:ss"
      - data_len / nested_count：int
    字段缺失置空（F230 无 persist 契约，完整结构化以 parsed 为准）。
    """
    parsed = entry.get("parsed") or {}
    items = {str(i.get("name") or ""): i for i in (parsed.get("items") or [])}

    def _val(name: str) -> str:
        v = items.get(name, {}).get("value")
        return "" if v is None else str(v)

    # 协议类型："0x02 (DL/T 645—2007)"/"0x03 (DL/T 698.45)" → 代码 "2"/"3"
    proto_raw = _val("通信协议类型")
    proto = proto_raw.strip()
    if proto.startswith("0x"):
        proto = proto.split(" ")[0][2:].lstrip("0") or "0"

    # 采集时间："2026年11月02日 17:10:00" → "2026-11-02 17:10:00"
    t = _val("采集时间")
    collect_time = t
    try:
        y = t[0:4]
        mo = t[5:7]
        d = t[8:10]
        hms = t[12:20] if len(t) >= 20 else ""
        if y.isdigit() and mo.isdigit() and d.isdigit():
            collect_time = f"{y}-{mo}-{d} {hms}".strip()
    except Exception:
        pass

    # 报文长度："54B" → 54
    dl_raw = _val("报文长度").rstrip("Bb")
    try:
        data_len = int(dl_raw) if dl_raw.isdigit() else None
    except Exception:
        data_len = None

    nested = parsed.get("nested") or []
    return {
        "task_id": _val("任务号"),
        "proto_type": proto,
        "proto_raw": proto_raw,
        "collect_time": collect_time,
        "node_addr": _val("从节点地址"),
        "data_len": data_len,
        "payload_hex": _val("报文内容"),
        "nested_count": len(nested),
        "nested_structures": [str(n.get("structure") or "") for n in nested],
        "items": parsed.get("items") or [],
        "warnings": parsed.get("warnings") or [],
    }


def _appdata_from_entry(entry: dict) -> Optional[bytes]:
    """从 journal entry 还原应用数据字节（AFN/DT 之后）。

    优先取 parsed 里的 raw_hex 重算；否则用 frame_hex 按单68结构切。
    由于 journal 的 parsed 来自 decode_frame，含完整 raw_hex，
    这里直接用 frame_hex 重切最可靠。
    """
    hx = str(entry.get("frame_hex") or "")
    hx = hx.replace(" ", "")
    if len(hx) < 8:
        return None
    try:
        raw = bytes.fromhex(hx)
    except ValueError:
        return None
    # 单68：raw[0]=68, L=raw[1..2], C=raw[3], userdata=raw[4:-2]
    if raw[0] != 0x68:
        return None
    userdata = raw[4:len(raw) - 2]
    if len(userdata) < 9:
        return None
    # 信息域 R 6B；module_id=1 时地址域（6 + 6*relay + 6）
    info = userdata[0:6]
    module_id = (info[0] >> 3) & 0x01  # 通信模块标识
    pos = 6
    if module_id == 1:
        relay = (info[0] >> 4) & 0x0F  # 中继级别（高4位）
        pos += 6 + 6 * relay + 6
    if pos + 3 > len(userdata):
        return None
    return userdata[pos + 3:]


def ingest_entry(store: Any, entry: dict) -> Optional[int]:
    """灌入单帧：frame_log 必写；06H 上报按契约提取并落业务表。

    返回 frame_log.id（失败 None）。
    """
    from sim_concentrator.record_extractor import extract_response

    fid = store.add_frame(entry)
    afn = entry.get("afn")
    fn = entry.get("fn")
    updown = entry.get("updown")
    if fid is None:
        return None
    if updown != "up" or afn != "06":
        return fid

    key = _contract_key(afn, fn)
    contract = _CONTRACT.get(key or "")
    appdata = _appdata_from_entry(entry)
    if fn == "F230":
        # REQS-0037：F230 无 persist 契约，走 adapter items 提取入库；
        # 入库后由 responder 自动应答 00H-F1（主动上报需确认，防 CCO 重复上报）。
        store.add_report_mclt_data(frame_id=fid, payload=_f230_payload(entry))
        return fid
    payload: dict = {"head": {}, "records": [], "warnings": []}
    if contract and contract.get("persist") and appdata:
        resp = contract.get("resp") or {}
        out = extract_response(appdata, resp)
        payload = {"head": out["head"], "records": out["records"],
                   "warnings": out["warnings"]}
    else:
        payload = {"head": {}, "records": [],
                   "raw_appdata": appdata.hex().upper() if appdata else "",
                   "warnings": [] if contract else ["无契约"]}

    if fn == "F2":
        head = payload.get("head") or {}
        # F2 是单条明细（fields 无 list），head 即整条
        store.add_report_meter_data(frame_id=fid, payload=head)
    else:
        store.add_report_event(
            frame_id=fid, afn=afn, fn=fn,
            event_type=_EVENT_FN.get(fn, f"F{fn}"),
            payload=payload,
        )
    return fid


def enrich_response(entry: dict) -> dict:
    """对上行响应帧做契约驱动解析，返回 {head, records, warnings} 供前端表格。

    与 ingest 的 06H 分支共用 extractor；这里覆盖所有上行（含 10H 查询响应）。
    """
    from sim_concentrator.record_extractor import extract_response

    key = _contract_key(entry.get("afn"), entry.get("fn"))
    contract = _CONTRACT.get(key or "")
    appdata = _appdata_from_entry(entry)
    if not contract or not appdata:
        return {"head": {}, "records": [], "warnings": ["无契约或无数据"]}
    resp = contract.get("resp") or {}
    out = extract_response(appdata, resp)
    return {"head": out["head"], "records": out["records"],
            "warnings": out["warnings"]}


def attach_store(journal: Any, store: Any) -> None:
    """把 store 挂到 journal：之后 journal.append 自动 ingest 到库。

    实现：给 journal 打一个 ingest 回调引用，journal 的 append 尾部调用。
    （journal.append 已把 parsed 写入 entry；此处 hook 以独立函数形式提供，
      实际接线见 api.py 的 FrameJournalStoreHook。）
    """
    journal.store = store
