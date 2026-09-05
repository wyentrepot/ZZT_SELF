"""libs.network_assert —— 网络层断言规则库（REQS-0025 G2/C1）。

蒸馏库 CCO实现逻辑 07/08 篇的工程门限 + 侦听台网络承载评估 B 类规则的
声明式断言定义：触发条件 + 观测窗口 + 判定 + 阈值 + 出处引用。
只做定义与静态校验；求值接口 evaluate() 预留（输入为 REQS-0024 事件流）。
"""
from .core import describe, evaluate, get_rule, list_rule_files, load_rules, validate
from .schema import ALLOWED_FAMILIES, KNOWN_SOURCE_DOCS, validate_rule, validate_rules

__all__ = [
    "ALLOWED_FAMILIES", "KNOWN_SOURCE_DOCS",
    "describe", "evaluate", "get_rule", "list_rule_files", "load_rules",
    "validate", "validate_rule", "validate_rules",
]
