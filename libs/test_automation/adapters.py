"""SourceAdapter 适配器协议（docs/03 §5）。

- 源错误必须转换为统一 AdapterError（含 code/message），不泄漏线程对象、
  串口句柄或本机绝对路径。
- ``stop()`` 必须幂等；实现可同步、异步或使用后台线程。
- 硬件端口由资源租约独占，离线文件可共享只读。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class AdapterError(Exception):
    """适配器统一错误类型：code + message（docs/03 §6 错误响应契约）。"""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class AdapterHealth:
    """适配器健康状态。"""

    ok: bool
    message: str = ""
    details: dict[str, Any] | None = None


class SourceAdapter(ABC):
    """证据来源适配器抽象基类（docs/03 §5）。

    run_context：Run 上下文（含 case_id、run_id、资源租约等）。
    evidence_sink：证据收集器（回调/对象），适配器向其投递 Evidence。
    """

    @abstractmethod
    def start(self, run_context: dict[str, Any]) -> None:
        """启动适配器并获取其资源。"""

    @abstractmethod
    def collect(self, evidence_sink: Any) -> list[Any]:
        """收集一批证据，投递到 evidence_sink；返回本次投递条目。"""

    @abstractmethod
    def stop(self) -> None:
        """幂等停止，释放资源；重复调用不得抛错。"""

    @abstractmethod
    def health(self) -> AdapterHealth:
        """返回当前健康状态。"""
