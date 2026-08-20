"""版本化侦听台 SQLite 索引目录。

一个 ``index_id`` 对应一个独立数据库文件；frame_id 只在该数据库内唯一，
对外引用必须与 index_id 组合使用。目录中的 catalog.json 保存当前指针和
历史索引元数据，写入采用替换方式避免中断时留下半个 catalog。
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path


class ListenerIndexRegistry:
    CATALOG_NAME = "catalog.json"
    SCHEMA_VERSION = 1

    def __init__(self, indexes_dir):
        self.indexes_dir = Path(indexes_dir).resolve()
        self.indexes_dir.mkdir(parents=True, exist_ok=True)
        self._catalog_path = self.indexes_dir / self.CATALOG_NAME
        self._lock = threading.RLock()
        self._catalog = self._load_catalog()

    def _load_catalog(self):
        if not self._catalog_path.is_file():
            return {"version": self.SCHEMA_VERSION, "current_index_id": None, "indexes": {}}
        try:
            payload = json.loads(self._catalog_path.read_text(encoding="utf-8"))
            if payload.get("version") != self.SCHEMA_VERSION or not isinstance(payload.get("indexes"), dict):
                raise ValueError("索引目录清单格式不兼容")
            payload.setdefault("current_index_id", None)
            return payload
        except (OSError, ValueError, json.JSONDecodeError):
            # 索引数据库不依赖 catalog 才能继续保存；损坏清单不应删除历史文件。
            return {"version": self.SCHEMA_VERSION, "current_index_id": None, "indexes": {}}

    def _save_catalog(self):
        temporary = self._catalog_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._catalog, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, self._catalog_path)

    @staticmethod
    def _new_index_id():
        return time.strftime("idx-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]

    def create_index(self, *, kind="file", source_path=None, parser_version="", index_id=None):
        """创建并设为 current 的空索引数据库记录，返回不可变元数据副本。"""
        with self._lock:
            candidate = index_id or self._new_index_id()
            if candidate in self._catalog["indexes"]:
                raise ValueError(f"索引已存在：{candidate}")
            if not candidate.startswith("idx-"):
                raise ValueError("index_id 必须以 idx- 开头")
            database_path = self.indexes_dir / f"{candidate}.sqlite3"
            record = {
                "index_id": candidate,
                "database_path": str(database_path),
                "source_path": str(source_path) if source_path else None,
                "kind": str(kind),
                "created_at": time.time(),
                "parser_version": str(parser_version),
            }
            self._catalog["indexes"][candidate] = record
            self._catalog["current_index_id"] = candidate
            self._save_catalog()
            return dict(record)

    def adopt_legacy_index(self, database_path, *, index_id="legacy-log-index"):
        """登记旧固定路径数据库，保留其可读性，但不覆盖已有目录记录。"""
        database_path = Path(database_path).resolve()
        if not database_path.is_file():
            return None
        with self._lock:
            existing = self._catalog["indexes"].get(index_id)
            if existing is not None:
                return dict(existing)
            record = {
                "index_id": index_id,
                "database_path": str(database_path),
                "source_path": None,
                "kind": "legacy",
                "created_at": database_path.stat().st_mtime,
                "parser_version": "",
            }
            self._catalog["indexes"][index_id] = record
            self._save_catalog()
            return dict(record)

    def current_index_id(self):
        with self._lock:
            return self._catalog.get("current_index_id")

    def get_index(self, index_id):
        with self._lock:
            record = self._catalog["indexes"].get(index_id)
            if record is None:
                raise KeyError(index_id)
            return dict(record)

    def list_indexes(self):
        with self._lock:
            current = self._catalog.get("current_index_id")
            records = []
            for record in self._catalog["indexes"].values():
                value = dict(record)
                value["is_current"] = value["index_id"] == current
                records.append(value)
            return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def database_path_for(self, index_id):
        record = self.get_index(index_id)
        return Path(record["database_path"])
