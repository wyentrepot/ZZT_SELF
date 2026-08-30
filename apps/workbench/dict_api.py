"""workbench.dict_api —— 协议字典只读端点（reqs/0010 P1）。

四本共享字典的统一查询口：698.45 OAD、645-2007 DI、1376.2 AFN/Fn、模块日志事件规则。
数据全部来自仓库真实文件（libs/ 下 metadata 与 loghooks rules），此处不做任何加工拷贝；
simple JSON 键即事实契约，字典文件改动即刻反映到本端点与字典页。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/dict")

_ROOT = Path(__file__).resolve().parents[2]

_OAD_PATH = _ROOT / "libs" / "parser_lib" / "adapters" / "adapter_698" / "metadata" / "oad.json"
_DI_PATH = _ROOT / "libs" / "parser_lib" / "adapters" / "adapter_645" / "metadata" / "di.json"
_AFN_PATH = _ROOT / "libs" / "parser_lib" / "adapters" / "adapter_10376" / "metadata" / "afn_fn.json"
_RULES_DIR = _ROOT / "libs" / "loghooks" / "rules"


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"字典文件缺失：{path.name}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"字典文件非法 JSON：{path.name}：{exc}") from exc


@router.get("")
def list_dicts():
    """字典清单（id/名称/来源路径/条数），供页面列 1 渲染。"""
    oad = _load_json(_OAD_PATH)
    di = _load_json(_DI_PATH)
    afn_doc = _load_json(_AFN_PATH)
    afn = afn_doc.get("afn", [])
    rule_files = sorted(
        str(p.relative_to(_RULES_DIR)).replace("\\", "/")
        for p in _RULES_DIR.rglob("*.json")
    )
    rule_count = 0
    for rel in rule_files:
        data = _load_json(_RULES_DIR / rel)
        rule_count += len(data) if isinstance(data, list) else 0
    return [
        {"id": "oad", "name": "698.45 OAD", "count": len(oad),
         "path": "libs/parser_lib/adapters/adapter_698/metadata/oad.json",
         "desc": "DL/T 698.45 对象-属性描述符（OAD）字典，名称/数据类型/单位/换算出自协议附录 A；desc 记录协议依据、真机实证与纠错注记。"},
        {"id": "di", "name": "645-2007 DI", "count": len(di),
         "path": "libs/parser_lib/adapters/adapter_645/metadata/di.json",
         "desc": "DL/T 645-2007 数据标识（DI）字典，覆盖附录 A.2 编码表；bcd_compact 类记录字节数与换算。"},
        {"id": "afn-fn", "name": "1376.2 AFN/Fn", "count": len(afn),
         "fn_count": sum(len(a.get("fns", [])) for a in afn),
         "path": "libs/parser_lib/adapters/adapter_10376/metadata/afn_fn.json",
         "desc": "Q/GDW 1376.2（原 10376.2）14+1 类 AFN 的 Fn 参考字典；构帧/解析以 adapter_10376 代码模板为准，含安徽扩展。"},
        {"id": "rules", "name": "事件规则", "count": rule_count,
         "path": "libs/loghooks/rules/",
         "desc": "模块日志事件识别规则（loghooks），事件名即场景脚本 expected_flow 的 event_type；含省份扩展。"},
    ]


@router.get("/oad")
def get_oad(q: Optional[str] = Query(None, description="模糊过滤键/名称/描述")):
    items = [{"key": k, **v} for k, v in _load_json(_OAD_PATH).items()]
    return {"dict": "oad", "count": len(items), "items": _filter(items, q)}


@router.get("/di")
def get_di(q: Optional[str] = Query(None, description="模糊过滤键/名称/描述")):
    items = [{"key": k, **v} for k, v in _load_json(_DI_PATH).items()]
    return {"dict": "di", "count": len(items), "items": _filter(items, q)}


@router.get("/afn-fn")
def get_afn_fn(q: Optional[str] = Query(None, description="模糊过滤码/名称/语义")):
    doc = _load_json(_AFN_PATH)
    items = doc.get("afn", [])
    return {
        "dict": "afn-fn",
        "count": len(items),
        "fn_count": sum(len(a.get("fns", [])) for a in items),
        "source": doc.get("source"),
        "note": doc.get("note"),
        "items": _filter(items, q),
    }


@router.get("/rules")
def get_rules(q: Optional[str] = Query(None, description="模糊过滤规则 id/事件名/标签")):
    files = sorted(_RULES_DIR.rglob("*.json"))
    out = []
    for path in files:
        rel = str(path.relative_to(_RULES_DIR)).replace("\\", "/")
        data = _load_json(path)
        entries = data if isinstance(data, list) else []
        if q:
            needle = q.lower()
            entries = [
                e for e in entries
                if needle in json.dumps(e, ensure_ascii=False).lower()
            ]
        out.append({"file": rel, "count": len(entries), "entries": entries})
    return {"dict": "rules", "count": sum(f["count"] for f in out), "files": out}


def _filter(items: list, q: Optional[str]) -> list:
    if not q:
        return items
    needle = q.lower()
    return [
        item for item in items
        if needle in json.dumps(item, ensure_ascii=False).lower()
    ]
