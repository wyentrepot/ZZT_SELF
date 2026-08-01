"""协议适配器抽象接口与通用数据结构。

所有协议适配器（645 / 698 / 1376...）都实现 ProtocolAdapter：
- try_extract: 从字节流开头切出一帧（含结构校验），用于断包/粘包切分
- confidence:   对一帧打分 0~1，用于自动嗅探路由
- decode:       解码为协议原生结构（字段带元数据，供前端通用渲染）
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DataField:
    """一个带元数据的字段，前端据此通用渲染（无需为每种协议写 UI）。"""
    name: str = ""
    value: Any = None
    unit: Optional[str] = None
    hex: str = ""
    raw: Any = None
    desc: str = ""


@dataclass
class ProtocolFrame:
    """协议原生结构。不同协议的 structure 不同，但字段统一带元数据。"""
    structure: str
    address: str = ""
    fields: list = field(default_factory=list)   # 顶层字段（地址/控制码/长度/校验）
    items: list = field(default_factory=list)    # 数据项列表（DI / OAD / AFN 等）
    nested: list = field(default_factory=list)    # 递归解出的内部帧（如 1376.2 嵌套的 645/698 帧）
    raw_hex: str = ""
    warnings: list = field(default_factory=list)


@dataclass
class ExtractResult:
    raw: bytes
    consumed: int


class ProtocolAdapter(ABC):
    protocol: str

    @abstractmethod
    def try_extract(self, buf: bytes) -> Optional[ExtractResult]:
        """尝试从 buf 开头切出一帧；成功返回(帧字节, 消耗字节数)，半包/非本协议返回 None。"""

    @abstractmethod
    def confidence(self, raw: bytes) -> float:
        """对一帧字节打分 0~1，用于嗅探路由（越高越可能是本协议）。"""

    @abstractmethod
    def decode(self, raw: bytes) -> ProtocolFrame:
        """解码为协议原生结构（字段带元数据）。"""
