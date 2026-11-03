"""sim_meter 698 采集 OAD 数据集：配置加载 / 校验 / 合并（REQS-0037）。

背景：STA 分钟采集任务经 485 发 698 GET（任务点 OAD 如 20000201 / 20010201 /
20000200），sim_meter 脑此前只应答 jzjc OAD（43000200），采集 OAD 无内建应答，
台架只能靠外挂注入脚本伪造应答（/d/sta/01-sta/20261103-003456/
inject_698_reply.py）。本模块给脑加"可配置 OAD 数据集"：

- 每条 OAD 二选一：应答数据字节（data_hex）或缺项（missing=true）；
  length 为数据长度（字节），有 data_hex 时须与其一致（缺项时仅作说明，
  即任务配置里该点的数据长度，供对账）。
- 全局集（任务无关，台架一次配置所有任务共用），两层载体：
  1) 文件配置：随库示例 data/oad_dataset.example.json，复制为
     data/oad_dataset.json 自动加载；或环境变量 SIMETER_OAD_DATASET 指定路径；
  2) 运行时 API：POST /api/simeter/oad 逐条设置，同 OAD 覆盖文件层
     （API 优先级高于文件）；reset=true 清空运行时层回落文件层。

缺项口径（698.45 表端行为 + 20261103 台架注入脚本实证）：
- 配置 missing 的 OAD 不进应答条目（不否认、不回错误码、也不回 FF）；
- STA 读层对缺席 OAD 按任务配置长度补 FF（libs/minute_assert/mclt_698.py
  OadItem：data 全 FF 判缺项，REQS-0037 口径）；
- 需要表端显式回 FF 时把 data_hex 配成 "FF"×N（STA 同样判缺项）。

文件加载失败不致命：记入 file_error（GET /api/simeter/oad 可见），服务照常
起，避免一个坏文件把整个模拟电表打挂；API 层设置前先校验，非法即 400。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

#: 文件路径环境变量（优先于随库默认路径）
ENV_OAD_DATASET = "SIMETER_OAD_DATASET"

_HEX = set("0123456789abcdefABCDEF")


def lib_data_dir() -> Path:
    """库自带数据目录（示例文件与默认配置文件所在）。"""
    return Path(__file__).resolve().parent / "data"


def default_dataset_path() -> Path:
    """默认自动加载的文件配置路径（不存在则视为未配置文件层）。"""
    return lib_data_dir() / "oad_dataset.json"


def example_dataset_path() -> Path:
    """随库示例文件（含 20261103 台架实测 OAD 集合）。"""
    return lib_data_dir() / "oad_dataset.example.json"


def normalize_oad(value: Any) -> str:
    """OAD 归一为 8 位大写十六进制（显示序，如 "20000201"）。"""
    s = str(value or "").strip().replace(" ", "").upper()
    if len(s) != 8 or any(c not in "0123456789ABCDEF" for c in s):
        raise ValueError(f"OAD 须为 8 位十六进制（4 字节）：{value!r}")
    return s


def normalize_oad_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    """校验并归一单条 OAD 配置。

    - data_hex 与 missing 二选一：同时给出 / 都不给均报错；
    - data_hex 为空串视同未给（缺项请显式配 missing=true）；
    - length 与 len(data_hex) 不一致报错（防止与任务配置长度对不上导致
      STA 读层切分错位）。
    """
    oad = normalize_oad(item.get("oad"))
    missing = bool(item.get("missing", False))
    note = str(item.get("note", "") or "")
    length_raw = item.get("length")
    data_hex_raw = item.get("data_hex")

    if missing and data_hex_raw is not None:
        raise ValueError(f"OAD {oad}：data_hex 与 missing=true 互斥（缺项不应答该条目）")

    if missing:
        data_hex = None
        length = 0
        if length_raw is not None:
            length = int(length_raw)
            if length < 0:
                raise ValueError(f"OAD {oad}：length 不能为负：{length_raw!r}")
    else:
        if data_hex_raw is None or not str(data_hex_raw).strip():
            raise ValueError(
                f"OAD {oad}：须配置 data_hex（应答数据字节）或 missing=true（缺项）")
        s = str(data_hex_raw).strip().replace(" ", "").upper()
        if len(s) % 2 or any(c not in _HEX for c in s):
            raise ValueError(f"OAD {oad}：data_hex 非十六进制字节串：{data_hex_raw!r}")
        data = bytes.fromhex(s)
        if not data:
            raise ValueError(f"OAD {oad}：data_hex 不能为空（缺项请配 missing=true）")
        data_hex = data.hex().upper()
        length = len(data)
        if length_raw is not None and int(length_raw) != length:
            raise ValueError(
                f"OAD {oad}：length={length_raw} 与 data_hex 长度 {length} 不符")

    return {"oad": oad, "missing": missing, "data_hex": data_hex,
            "length": length, "note": note}


def normalize_oad_entries(
    items: Union[List[Dict[str, Any]], Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """校验并归一 OAD 配置列表 → {oad: entry}；重复 OAD 后者覆盖前者。"""
    if isinstance(items, dict):
        items = items.get("oads")
    if not isinstance(items, list):
        raise ValueError('OAD 数据集须为 {"oads": [...]} 或条目列表')
    out: Dict[str, Dict[str, Any]] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"OAD 数据集第 {i + 1} 条须为对象：{item!r}")
        entry = normalize_oad_entry(item)
        out[entry["oad"]] = entry
    return out


def load_oad_dataset_file(path: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
    """加载 OAD 数据集 JSON 文件（UTF-8），返回归一化条目表。

    兼容两种顶层形态：{"oads": [...]}（示例文件带 comment/usage 字段）或
    裸条目列表。解析/校验失败抛 ValueError（调用方决定致命与否）。
    """
    p = Path(path)
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OAD 数据集文件非合法 JSON：{p}（{exc}）") from exc
    return normalize_oad_entries(doc)


def resolve_dataset_path(explicit: Optional[Union[str, Path]]) -> Optional[Path]:
    """收敛 OAD 数据集文件路径：显式传入 > 环境变量 > 随库默认路径。

    返回 None 表示无文件层（默认文件不存在且未显式/未设环境变量）。
    """
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get(ENV_OAD_DATASET, "").strip()
    if env:
        return Path(env)
    default = default_dataset_path()
    return default if default.exists() else None
