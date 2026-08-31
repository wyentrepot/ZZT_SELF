# -*- coding: utf-8 -*-
"""1376.2 收发库 sink（REQS-0013 P0-3）：把 journal 帧条目灌入 ListenerStore。

职责：
  1. 每帧 → frame_log（证据链）
  2. 上行帧（updown=up）且 AFN=06H → 按 resp 契约提取并落库：
       F1/F3/F4/F5 → report_event；F2 → report_meter_data
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
