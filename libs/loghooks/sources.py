"""来源注册表：模块日志 / 侦听台 / 集中器模拟脚本（预留）。

每种来源对应一个"输入 → 待匹配对象"的解析器函数。
解析器注册在 _registry 中，引擎按规则 source 字段路由。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

# ---------------------------------------------------------------------------
# 统一解析结果
# ---------------------------------------------------------------------------


@dataclass
class ParsedLine:
    """解析器产出的统一结构。"""

    source: str  # 来源标识："module_log" | "listener" | "concentrator_10376"
    raw: str  # 原始行
    time: Optional[str] = None  # 时间戳（原始字符串）
    direction: Optional[str] = None  # "RX" | "TX" | "EVENT" | None
    # 匹配对象（文本模式用 text, 字段模式用 fields）
    text: Optional[str] = None  # 纯文本消息（module_log 从内容中提取的消息部分）
    fields: Optional[dict] = None  # 结构化字段（listener 的 simple dict）
    file: Optional[str] = None  # 源文件（模块日志中提取）
    line: Optional[int] = None  # 行号（模块日志中提取）
    metadata: dict = None  # 其它元数据

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


# 解析器类型：输入原始行（str），返回 ParsedLine 或 None
ParserFn = Callable[[str], Optional[ParsedLine]]

# ---------------------------------------------------------------------------
# module_log 解析器
# ---------------------------------------------------------------------------

# 模块日志行格式：[YYYYMMDD-HH:MM:SS:mmm] [RX|TX|EVENT] 内容
_MODULE_LOG_LINE = re.compile(
    r"^\[(?P<ts>\d{8}-\d{2}:\d{2}:\d{2}:\d{3})\]\s+\[(?P<dir>RX|TX|EVENT)\]\s+(?P<content>.*)$",
    re.IGNORECASE,
)

# 内容格式：序列号 | info | 文件.c (行号) | 消息
_CONTENT_PARSE = re.compile(
    r"^\d+\s*\|\s*(?:\w+)\s*\|\s*(?P<file>[^\s(]+)\s*\((?P<line>\d+)\)\s*\|\s*(?P<msg>.*)$"
)


def parse_module_log(line: str) -> Optional[ParsedLine]:
    """解析模块日志文本行，返回 ParsedLine（text 模式）。"""
    m = _MODULE_LOG_LINE.match(line.strip())
    if not m:
        return None

    ts = m.group("ts")
    direction = m.group("dir").upper()
    content = m.group("content")

    # 尝试解析内容中的文件/行号/消息
    cm = _CONTENT_PARSE.match(content)
    if cm:
        msg = cm.group("msg")
        file_ = cm.group("file")
        line_num = int(cm.group("line"))
    else:
        msg = content
        file_ = None
        line_num = None

    return ParsedLine(
        source="module_log",
        raw=line,
        time=ts,
        direction=direction,
        text=msg,
        file=file_,
        line=line_num,
        metadata={"content": content},
    )


# ---------------------------------------------------------------------------
# listener 解析器
# ---------------------------------------------------------------------------

# 侦听台行格式：[序号][HH:MM:SS.mmm]7E...7E
_LISTENER_LINE = re.compile(
    r"^\[(?P<seq>[^\]]*)\]\[(?P<ts>\d{2}:\d{2}:\d{2}\.\d{3})\](?P<frame>.*)$",
)


def parse_listener_frame(
    line: str,
    parser_callback: Optional[Callable[[str], dict]] = None,
) -> Optional[ParsedLine]:
    """解析侦听台 hex 帧行，返回 ParsedLine（fields 模式）。

    需要传入 parser_callback 来解析 hex 帧 → simple dict。
    如果未传入 parser_callback，则只提取时间戳，不解析帧内容。
    """
    m = _LISTENER_LINE.match(line.strip())
    if not m:
        return None

    ts = m.group("ts")
    frame_text = m.group("frame").strip()

    # 提取 7E...7E 帧
    hex_bytes = re.findall(r"(?i)\b[0-9a-f]{2}\b", frame_text)
    try:
        first = next(i for i, h in enumerate(hex_bytes) if h.upper() == "7E")
        last = len(hex_bytes) - 1 - next(
            i for i, h in enumerate(reversed(hex_bytes)) if h.upper() == "7E"
        )
    except StopIteration:
        return None

    if first >= last:
        return None

    frame_hex = " ".join(h.upper() for h in hex_bytes[first : last + 1])
    fields = None
    if parser_callback:
        try:
            result = parser_callback(frame_hex)
            fields = result.get("simple") if isinstance(result, dict) else result
        except Exception:
            fields = None

    return ParsedLine(
        source="listener",
        raw=line,
        time=ts,
        direction="RX",
        text=frame_hex,
        fields=fields,
        metadata={"frame_hex": frame_hex},
    )


# ---------------------------------------------------------------------------
# 来源：concentrator_10376（模拟集中器下发的 13762 帧）
# ---------------------------------------------------------------------------


def parse_concentrator_10376(
    line: str,
    adapter_callback: Optional[Callable[[str], dict]] = None,
) -> Optional[ParsedLine]:
    """解析集中器模拟脚本下发的 13762 帧行，返回 ParsedLine（fields 模式）。

    输入行形如：`[ts] TX 68...16` 或纯 hex 帧文本；提取 1376.2 帧后用
    parser facade 解出信封字段 + 嵌套 645/698。

    兼容 adapter_callback：若传入，优先用它（帧 hex → dict），否则走内置
    parser facade。
    """
    m = _CONCENTRATOR_LINE.match(line.strip())
    frame_text = line.strip()
    if m:
        frame_text = m.group("frame").strip()

    hex_bytes = re.findall(r"(?i)\b[0-9a-f]{2}\b", frame_text)
    if not hex_bytes or hex_bytes[0].upper() != "68":
        return None
    # 找到结尾 16
    last = len(hex_bytes) - 1 - next(
        (i for i, h in enumerate(reversed(hex_bytes)) if h.upper() == "16"),
        len(hex_bytes),
    )
    if last < 0:
        return None
    frame_hex = " ".join(h.upper() for h in hex_bytes[: last + 1])

    fields = None
    try:
        if adapter_callback is not None:
            res = adapter_callback(frame_hex)
            fields = res if isinstance(res, dict) else None
        else:
            from parser_lib.protocol_13762 import decode

            result = decode({"frame": frame_hex})
            if isinstance(result, dict) and result.get("ok") is True:
                fields = result
    except Exception:
        fields = None

    return ParsedLine(
        source="concentrator_10376",
        raw=line,
        time=m.group("ts") if m else None,
        direction="TX",
        text=frame_hex,
        fields=fields,
        metadata={"frame_hex": frame_hex},
    )


_CONCENTRATOR_LINE = re.compile(
    r"^\[(?P<ts>\d{4}[-/]?\d{2}[-/]?\d{2}[ T]?\d{2}:\d{2}:\d{2}(?:\.\d{3})?)\]\s*"
    r"(?:\[(?P<dir>TX|RX)\]\s*)?(?P<frame>.*)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_SOURCE_REGISTRY: Dict[str, Callable[..., Optional[ParsedLine]]] = {
    "module_log": parse_module_log,
    "listener": parse_listener_frame,
    "concentrator_10376": parse_concentrator_10376,
}


def get_parser(source: str) -> Optional[Callable[..., Optional[ParsedLine]]]:
    """获取指定来源的解析器函数。"""
    return _SOURCE_REGISTRY.get(source)


def register_source(name: str, parser_fn: Callable[..., Optional[ParsedLine]]) -> None:
    """注册新的来源解析器（可扩展）。"""
    _SOURCE_REGISTRY[name] = parser_fn


def list_sources() -> List[str]:
    """列出所有注册的来源。"""
    return list(_SOURCE_REGISTRY.keys())


# ---------------------------------------------------------------------------
# 行级迭代器
# ---------------------------------------------------------------------------


def iter_lines(source: str, lines: List[str], **kwargs) -> List[ParsedLine]:
    """对一组行按指定来源解析，返回所有成功解析的 ParsedLine。"""
    parser = get_parser(source)
    if not parser:
        return []
    results: List[ParsedLine] = []
    for line in lines:
        parsed = parser(line, **kwargs)
        if parsed:
            results.append(parsed)
    return results