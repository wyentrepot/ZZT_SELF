"""元数据（数据字典）存储：JSON 文件外置，按协议加载，按 key 查询。

设计：字典外置，新增数据项只改 JSON，不动代码。查不到返回 None，由解码层标记 unknown。
"""
import json
import os
from typing import Any, Dict, Optional


class MetadataStore:
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}

    def load_protocol(self, protocol: str, directory: str):
        """加载某协议目录下所有 .json 字典，合并到一个映射。"""
        merged: Dict[str, Any] = {}
        if os.path.isdir(directory):
            for fn in sorted(os.listdir(directory)):
                if fn.endswith(".json"):
                    with open(os.path.join(directory, fn), encoding="utf-8") as f:
                        merged.update(json.load(f))
        self._data[protocol] = merged

    def lookup(self, protocol: str, key: str) -> Optional[Any]:
        return self._data.get(protocol, {}).get(key)
