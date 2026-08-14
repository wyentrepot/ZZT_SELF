"""规则表加载、校验、schema 定义与省份自动识别。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 规则数据结构
# ---------------------------------------------------------------------------


@dataclass
class MatchDef:
    """单行/单帧匹配定义（match 字段）。"""

    mode: str  # "text" | "field"
    # text 模式
    pattern: Optional[str] = None
    flags: List[str] = field(default_factory=list)
    # field 模式
    field: Optional[str] = None
    op: Optional[str] = None  # == | != | contains | startswith | regex
    value: Optional[str] = None
    # 通用可选：行号弱约束
    file: Optional[str] = None
    line: Optional[int] = None
    line_tolerance: int = 10

    @classmethod
    def from_dict(cls, d: dict) -> "MatchDef":
        return cls(
            mode=d["mode"],
            pattern=d.get("pattern"),
            flags=d.get("flags", []),
            field=d.get("field"),
            op=d.get("op"),
            value=d.get("value"),
            file=d.get("file"),
            line=d.get("line"),
            line_tolerance=d.get("line_tolerance", 10),
        )


@dataclass
class SequenceStep:
    """状态流中的一个步骤。"""

    step: str
    pattern: str
    flags: List[str] = field(default_factory=list)
    capture_group: Optional[int] = None  # 捕获组序号，用于分桶

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceStep":
        return cls(
            step=d["step"],
            pattern=d["pattern"],
            flags=d.get("flags", []),
            capture_group=d.get("capture_group"),
        )


@dataclass
class SequenceDef:
    """跨行状态流定义（sequence 字段）。"""

    steps: List[SequenceStep]
    window_ms: int = 30000
    on_complete: Optional[dict] = None
    on_timeout: Optional[dict] = None
    bucket_field: Optional[str] = None  # 显式分桶字段

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceDef":
        return cls(
            steps=[SequenceStep.from_dict(s) for s in d.get("sequence", [])],
            window_ms=d.get("window_ms", 30000),
            on_complete=d.get("on_complete"),
            on_timeout=d.get("on_timeout"),
            bucket_field=d.get("bucket_field"),
        )


@dataclass
class Rule:
    """一条完整的钩子规则。"""

    id: str
    category: str
    level: str = "info"
    scope: str = "common"  # "common" | "province"
    module: str = "common"  # "cco" | "sta" | "common"（适用模块）
    province: Optional[str] = None
    source: List[str] = field(default_factory=lambda: ["module_log"])
    match: Optional[MatchDef] = None
    sequence: Optional[SequenceDef] = None
    capture: Dict[str, str] = field(default_factory=dict)
    event: dict = field(default_factory=dict)
    # 原始数据（保留供调试/引用）
    _raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict, module_hint: Optional[str] = None) -> "Rule":
        rule = cls(
            id=d["id"],
            category=d["category"],
            level=d.get("level", "info"),
            scope=d.get("scope", "common"),
            module=d.get("module") or module_hint or "common",
            province=d.get("province"),
            source=d.get("source", ["module_log"]),
            capture=d.get("capture", {}),
            event=d.get("event", {}),
            _raw=d,
        )
        if "match" in d:
            rule.match = MatchDef.from_dict(d["match"])
        if "sequence" in d:
            rule.sequence = SequenceDef.from_dict(d)
        return rule


# ---------------------------------------------------------------------------
# Schema 校验
# ---------------------------------------------------------------------------


class SchemaError(ValueError):
    """规则 schema 校验失败。"""


def _check_required(d: dict, path: str, field: str) -> None:
    if field not in d:
        raise SchemaError(f"{path}: 缺少必填字段 '{field}'")


def _check_type(d: dict, path: str, field: str, expected: type) -> None:
    if field in d and not isinstance(d[field], expected):
        raise SchemaError(f"{path}.{field}: 期望 {expected.__name__}, 实际 {type(d[field]).__name__}")


def validate_rule(d: dict, file_label: str = "") -> None:
    """校验单条规则的 schema 合法性。"""
    prefix = f"{file_label} id={d.get('id', '?')}"

    # 必填字段
    _check_required(d, prefix, "id")
    _check_required(d, prefix, "category")
    _check_required(d, prefix, "event")
    _check_type(d, prefix, "id", str)
    _check_type(d, prefix, "category", str)
    _check_type(d, prefix, "source", list)
    _check_type(d, prefix, "event", dict)

    # scope / province 一致性
    scope = d.get("scope", "common")
    if scope not in ("common", "province"):
        raise SchemaError(f"{prefix}: scope 必须是 'common' 或 'province'，实际 '{scope}'")
    if scope == "province" and not d.get("province"):
        raise SchemaError(f"{prefix}: scope=province 时必须提供 province 字段")

    # match 与 sequence 二选一（至少一个）
    has_match = "match" in d
    has_seq = "sequence" in d
    if not has_match and not has_seq:
        raise SchemaError(f"{prefix}: 必须提供 match 或 sequence（二选一）")

    if has_match:
        m = d["match"]
        _check_required(m, f"{prefix}.match", "mode")
        mode = m["mode"]
        if mode not in ("text", "field"):
            raise SchemaError(f"{prefix}.match.mode: 必须是 'text' 或 'field'，实际 '{mode}'")
        if mode == "text" and not m.get("pattern"):
            raise SchemaError(f"{prefix}.match: mode=text 时必须提供 pattern")
        if mode == "field" and not m.get("field"):
            raise SchemaError(f"{prefix}.match: mode=field 时必须提供 field")

    if has_seq:
        steps = d.get("sequence", [])
        if not steps:
            raise SchemaError(f"{prefix}.sequence: 不能为空列表")
        for i, step in enumerate(steps):
            if not step.get("step") or not step.get("pattern"):
                raise SchemaError(f"{prefix}.sequence[{i}]: 每个 step 必须提供 step 和 pattern")


def validate_ruleset(rules: List[dict], file_label: str = "") -> List[dict]:
    """校验一组规则，返回校验后的列表。"""
    validated = []
    seen_ids = set()
    for d in rules:
        validate_rule(d, file_label)
        rid = d["id"]
        if rid in seen_ids:
            raise SchemaError(f"{file_label}: 重复的规则 id '{rid}'")
        seen_ids.add(rid)
        validated.append(d)
    return validated


# ---------------------------------------------------------------------------
# 规则加载
# ---------------------------------------------------------------------------


class RuleLoader:
    """从目录加载全部规则文件，支持去重校验与省份过滤。"""

    def __init__(self, rules_dir: Optional[Path] = None):
        self.rules_dir = Path(rules_dir) if rules_dir else Path(__file__).parent / "rules"
        self._raw_rules: List[dict] = []
        self._rules: List[Rule] = []
        self._by_id: Dict[str, Rule] = {}
        self._by_province: Dict[Optional[str], List[Rule]] = {}
        self._by_module: Dict[str, List[Rule]] = {}
        self._errors: List[str] = []

    @property
    def rules(self) -> List[Rule]:
        return self._rules

    @property
    def errors(self) -> List[str]:
        return self._errors

    def load_all(self) -> "RuleLoader":
        """加载 rules_dir 下所有 json 文件（cco.json + sta.json + common.json + provinces/*.json）。"""
        self._raw_rules.clear()
        self._rules.clear()
        self._by_id.clear()
        self._by_province.clear()
        self._by_module.clear()
        self._errors.clear()

        # 模块规则文件：文件名 → module hint
        # cco.json → cco, sta.json → sta, common.json → common
        module_files = {
            "cco": self.rules_dir / "cco.json",
            "sta": self.rules_dir / "sta.json",
            "common": self.rules_dir / "common.json",
        }
        for module, path in module_files.items():
            if path.exists():
                self._load_file(path, module_hint=module)

        # 加载 provinces/ 下所有 json（module 由规则内字段指定，默认 common）
        provinces_dir = self.rules_dir / "provinces"
        if provinces_dir.exists():
            for fpath in sorted(provinces_dir.iterdir()):
                if fpath.suffix == ".json":
                    self._load_file(fpath, module_hint=None)

        # 校验 + 构建索引
        for d in self._raw_rules:
            try:
                rule = Rule.from_dict(d, module_hint=d.get("_module_hint"))
                self._rules.append(rule)
                self._by_id[rule.id] = rule
                prov = rule.province if rule.scope == "province" else None
                self._by_province.setdefault(prov, []).append(rule)
                self._by_module.setdefault(rule.module, []).append(rule)
            except SchemaError as e:
                self._errors.append(str(e))

        return self

    def _load_file(self, path: Path, module_hint: Optional[str] = None) -> None:
        """加载单个规则文件。"""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._errors.append(f"加载 {path.name} 失败: {e}")
            return

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            self._errors.append(f"{path.name}: 期望 JSON 数组或对象，实际 {type(data).__name__}")
            return

        label = path.name
        try:
            validated = validate_ruleset(data, label)
            for d in validated:
                if module_hint and "module" not in d:
                    d["_module_hint"] = module_hint
            self._raw_rules.extend(validated)
        except SchemaError as e:
            self._errors.append(str(e))

    def filter_by_module(self, module: Optional[str] = None) -> List[Rule]:
        """按模块过滤规则。

        module=None 或 "common"：返回全部通用（common）规则（不含省份专属）。
        module="cco"：返回 common + cco 规则（不含省份专属）。
        module="sta"：返回 common + sta 规则（不含省份专属）。
        """
        # 省份专属规则（scope=province）不参与模块通用桶，由 --province 单独过滤
        def _is_generic(r):
            return r.scope != "province"

        if module in (None, "", "common"):
            return [r for r in self._by_module.get("common", []) if _is_generic(r)]
        result = [r for r in self._by_module.get("common", []) if _is_generic(r)]  # 通用规则
        result.extend(r for r in self._by_module.get(module, []) if _is_generic(r))
        return result

    def filter_by_province(self, province: Optional[str]) -> List[Rule]:
        """按省份过滤规则。province=None 时返回全部 common 规则；province='anhui' 时返回 common + 该省规则。"""
        result = list(self._by_province.get(None, []))  # common 规则
        if province:
            result.extend(self._by_province.get(province, []))
        return result

    def get_province_list(self) -> List[dict]:
        """列出所有可用省份及其规则数。"""
        provs = {}
        for rule in self._rules:
            if rule.scope == "province" and rule.province:
                provs[rule.province] = provs.get(rule.province, 0) + 1
        return [
            {"province": p, "rule_count": c}
            for p, c in sorted(provs.items())
        ]

    def detect_provinces(self, hit_rule_ids: List[str]) -> List[dict]:
        """根据命中的规则 ID 自动判定日志来源省份。"""
        common_hits = []
        province_hits = {}

        for rid in hit_rule_ids:
            rule = self._by_id.get(rid)
            if not rule:
                continue
            if rule.scope == "common":
                common_hits.append(rid)
            elif rule.scope == "province" and rule.province:
                province_hits.setdefault(rule.province, []).append(rid)

        result = []
        for prov, rids in sorted(province_hits.items()):
            confidence = "high" if len(rids) >= 2 else "medium"
            result.append({"province": prov, "rules_hit": rids, "confidence": confidence})

        if common_hits and not province_hits:
            result.append({"province": None, "rules_hit": common_hits, "confidence": "common-only"})

        return result


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def get_rule_loader() -> RuleLoader:
    """获取默认规则加载器（加载 loghooks/rules/ 目录）。"""
    return RuleLoader().load_all()