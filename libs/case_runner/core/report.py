"""一句话报告层（REQS-0042 v11 D11-1 / D11-2 / D11-3）。

用户口径（2026-11-23 直批）：
> 「用例库测试完成之后直接生成报告即可，比如抄表日常回归（PASS），
>  失败的话就是（组网超时，档案下发异常）等等，然后把日志或者证据路径给列出来即可，
>  不用这么一步步的说明，**尤其是列出 archive.add 这种名字，压根无法被用户理解**。」

本模块是 CLI / API / UI 共用的**唯一一份**人读口径，纯函数、零 IO、零等待：

- :func:`action_label`：块 id → 业务动作（全部声明块全覆盖，未知块返回 ``""``，
  **绝不回显块 id**）；
- :func:`classify_failure`：单块记录 → 人话失败原因短语（四类，见下）；
- :func:`summarize_case`：用例结果 → 一句话结论 + 原因 + 证据路径；
- :func:`summarize_run`：一次运行（多用例）→ 汇总一句话（取最严重 verdict）。

失败原因四类模板（REQS §3.5 失败三分类的人读化）
------------------------------------------------
===  ==========================================  ==================================
类   触发                                        产出
===  ==========================================  ==================================
一   ``state=fail`` + 超时/未收到应答             ``组网超时`` / ``抄表超时``
二   ``state=fail`` + 有应答但判据不成立          ``档案下发异常`` / ``绑定表地址不符（实读 X，期望 Y）``
                                                   / ``抄表数值不符（读 220.0V，期望 220.1V）``
三   ``state=skipped``（台架/前置不具备）         ``台架不具备：串口桥未运行`` / ``台架不具备：模拟集中器被占用``
四   ``state=unknown`` 或解析不出                 ``说不清：应答解析不出`` / ``说不清：学习态尚未完成``
===  ==========================================  ==================================

映射关系（块态 → 结论层 → 人读类）与 ``executor.BLOCK_TO_OUTCOME`` 一致，
本模块不自己发明第四态：``skipped → blocked``（台架），``unknown → inconclusive``（说不清）。

硬约束：输出里**绝不允许出现块 id**
----------------------------------
块执行体的 ``detail`` 大量以块 id 开头（如 ``"nwk.topology：…"``），直接拼给人看就是
用户投诉的那个 bug。本模块做两层防：

1. 造成人话短语时只用**模板 + 少量提取值**，几乎不搬 detail 原文；
2. 凡是要搬 detail 片段的地方先过 :func:`scrub_block_ids`（块 id → 业务动作，
   文件后缀名 ``result.json`` 之类放行），最后再兜一层 :func:`contains_block_id` 自检。

:func:`contains_block_id` 也是测试里的守卫断言（``test_report.py``）：任何
``headline`` / ``reason`` / ``reasons`` 命中即红。

本模块的已知风险（写在这里而不是藏起来）
----------------------------------------
- **原因归类靠 detail 关键词**（超时 / 不符 / 不可达 / 占用 / 解析 …）。块层改了
  措辞会削弱命中率，但**不会**泄漏块 id、也不会把 ``pass`` 说成失败：命中不了
  就落到「说不清：<清洗后的短句>」兜底，最坏是话说得糙，不是话说错。
- **数值不符**靠两种来源：记录里带了标量 ``expected`` / ``actual``（最可靠，优先），
  否则从 detail 里抠 ``期望 X，实际 Y`` / ``期望 X，实得 Y``（含 ``V`` 等单位时一并带上）。
  两者都没有就退化成「<动作>异常」，**不编数值**。
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping, Sequence

from .executor import BLOCK_TO_OUTCOME, OUTCOME_TO_BLOCK, aggregate_verdict
from .models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_STATES,
    BLOCK_UNKNOWN,
    OUTCOMES,
    OUTCOME_BLOCKED,
    OUTCOME_FAIL,
    OUTCOME_INCONCLUSIVE,
    OUTCOME_PASS,
)

# --------------------------------------------------------------------------- #
# 块 id → 业务动作（REQS D11-2；全部声明块全覆盖，缺一即测试红）
# --------------------------------------------------------------------------- #

#: 块 id → 业务动作。顺序按「台架 → 复位 → 档案 → 组网 → 抄表」，与执行序一致。
#:
#: 只允许翻译，不允许回显：:func:`action_label` 对未知块返回 ``""``（调用方用兜底词），
#: 绝不 ``return block_id``——那正是用户投诉的「压根无法被用户理解」。
BLOCK_ACTIONS: dict[str, str] = {
    # 台架自检
    "fix.simcon_session": "台架自检（模拟集中器）",
    "fix.simeter_session": "台架自检（模拟电表开口）",
    # 复位
    "reset.sta": "复位",
    # 档案
    "archive.add": "档案下发",
    "archive.del": "档案删除",
    "archive.query": "档案查询",
    # 组网
    "nwk.topology": "组网",
    "nwk.nodes_online": "在网节点检查",
    "nwk.onnet_count": "在网数核对",
    "nwk.archive_count": "档案数核对",
    "nwk.route_restart": "路由重启",
    "nwk.route_status": "路由状态检查",
    "nwk.active_registration": "主动注册（搜表）",
    # 抄表
    "meter.addr_probe": "绑表（取表地址）",
    "meter.read_645": "抄表",
    "meter.brain_local": "表端自检",
    "meter.read_698": "抄表（698）",
    "meter.batch_read": "并发抄表",
    # 并发闸门负向观测（0043 F0-4）
    "gate.multi_frame_110": "并发多帧检查",
    "gate.concurrent_109": "并发帧数检查",
    # 参数初始化（0043 F0-6 协议切换前置）
    "param.init_01h_f2": "参数初始化",
    # 协议一致性（0043 H / §G-6 三方补步）
    "bench.simeter_protocol": "台架协议切换（模拟电表）",
    "sta.meter_protocol": "表口协议态判读",
}

#: 短语里用的**短动作**（长的业务动作带说明括注，放进「组网超时」这种短语里太啰嗦）
#: 由 :func:`_short_action` 从 ``BLOCK_ACTIONS`` 按「（」截断派生，不另立一份表。

#: 完全不认识块时的兜底动作词（同样不含块 id）
FALLBACK_ACTION = "该步骤"

# --------------------------------------------------------------------------- #
# 违禁词：块 id 形状 / 放行的文件名后缀
# --------------------------------------------------------------------------- #

#: 块 id 形状：``小写小写``（两侧都有小写字母，所以 ``HTTP 409`` / ``AFN=0x1C`` 不命中）
_BLOCK_ID_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(?![A-Za-z0-9_])"
)

#: 长得像块 id、其实是文件名的后缀（``result.json`` / ``cli.log``），守卫必须放行，
#: 否则证据路径与日志名会被误伤。
_FILE_SUFFIXES = frozenset(
    {
        "json", "jsonl", "log", "txt", "md", "py", "yaml", "yml", "csv", "tsv",
        "html", "htm", "xml", "ini", "toml", "cfg", "conf", "db", "sqlite",
        "png", "jpg", "jpeg", "webp", "gif", "pdf", "hex", "pcap", "sh", "bat",
    }
)

#: detail 开头的块 id 前缀（``"nwk.topology：…"``），造成人话时先摘掉
_LEADING_ID_PREFIX = re.compile(
    r"^\s*[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\s*[：:]\s*"
)

#: 长 hex 串（帧）在人话里没有意义，折叠成「帧」。
#: 只折叠**含 A~F 字母**的长串：纯数字长串可能是表地址（``000000000001``），
#: 地址判据要用它，误折叠会让「实读多少」丢掉。
_HEX_RUN = re.compile(
    r"(?<![0-9A-Za-z])(?=[0-9A-Fa-f]*[A-Fa-f])[0-9A-Fa-f]{12,}(?![0-9A-Za-z])"
)

#: 标题截断：``抄表日常回归（不复位，日常）：档案 → …`` → ``抄表日常回归``
_TITLE_CUT = re.compile(r"[（(：:]")

# --------------------------------------------------------------------------- #
# 原因归类用的 detail 关键词（块层实际措辞见 blocks/common.py 与各块模块）
# --------------------------------------------------------------------------- #

#: 台架 / 前置不具备（``skipped`` 的本质；也有 ``unknown`` 带这些词的情况）
_BENCH_MARKERS = (
    "台架",
    "串口桥",
    "不可达",
    "连不上",
    "unreachable",
    "被占用",
    "占用",
    "409",
    "404",
    "闸门忙",
    "会话忙",
    "无会话",
    "未挂载",
    "未配置",
    "不就绪",
    "未就绪",
    "服务异常",
    "端口未开",
    "端口不可用",
    "不支持",
)

#: 超时 / 未收到应答
_TIMEOUT_MARKERS = (
    "超时",
    "未收到应答",
    "未收到",
    "未应答",
    "无应答",
    "没有应答",
    "没等到",
    "没收到",
    "未到终态",
    "no_reply",
)

#: 判据不成立 / 数值不符（有应答，但内容不符合期望）
_MISMATCH_MARKERS = (
    "不符",
    "不匹配",
    "不一致",
    "不符合",
    "否认帧",
    "未回显",
    "判据不成立",
)

#: 说不清（unknown 的常见措辞）
_UNCLEAR_MARKERS = (
    "解析",
    "递归",
    "无法识别",
    "无法判定",
    "取不到",
    "未给出",
    "字段缺失",
    "学习",
)

#: 数值对的两种写法：``期望 220.1，实际 220.0`` / ``期望 3，实得 2``
_VALUE_PAIR = re.compile(
    r"期望(?:值|地址|DI|数量|数|总数)?\s*[:：]?\s*"
    r"(?P<expect>\d+(?:\.\d+)?)(?P<expect_unit>[A-Za-z%]{0,3})"
    r"\s*[，,；;]\s*"
    r"(?:实际|实得)\s*[:：]?\s*"
    r"(?P<actual>\d+(?:\.\d+)?)(?P<actual_unit>[A-Za-z%]{0,3})"
)

#: 地址对：``期望地址 000000000001，实际 000000000002``
_ADDR_PAIR = re.compile(
    r"期望地址\s*[:：]?\s*(?P<expect>[0-9A-Fa-f]{4,})\s*[，,；;]\s*实际\s*[:：]?\s*"
    r"(?P<actual>[0-9A-Fa-f]{0,})"
)

#: 「这是数量不是数值」的关键词（档案条数 / 在网节点数 / 总数）
_COUNT_MARKERS = ("总数", "数量", "条数", "个数", "节点数", "档案数")

#: 地址判据的关键词
_ADDR_MARKERS = ("地址域不匹配", "地址域不符", "地址不符", "地址不匹配")


# --------------------------------------------------------------------------- #
# 公开：块 id 守卫
# --------------------------------------------------------------------------- #


def contains_block_id(text: Any) -> bool:
    """文本里是否出现**块 id**（D11-2 守卫；文件后缀名放行）。

    用于测试断言与调用方自检：``headline`` / ``reason`` / ``reasons`` 命中即 bug。
    """

    if text is None:
        return False
    value = str(text)
    for match in _BLOCK_ID_TOKEN.finditer(value):
        token = match.group(0)
        if token.rsplit(".", 1)[-1] in _FILE_SUFFIXES:
            continue
        return True
    return False


def scrub_block_ids(text: Any) -> str:
    """把文本里出现的块 id 换成业务动作（不认识 → :data:`FALLBACK_ACTION`）。

    这是「绝不泄漏块 id」的最后一道闸：即使块层 detail 里写了块 id，
    走到用户面前也已经变成人话。
    """

    value = "" if text is None else str(text)

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.rsplit(".", 1)[-1] in _FILE_SUFFIXES:
            return token
        return _short_action(token) or FALLBACK_ACTION

    return _BLOCK_ID_TOKEN.sub(_replace, value)


# --------------------------------------------------------------------------- #
# 公开：块 id → 业务动作
# --------------------------------------------------------------------------- #


def action_label(block_id: str) -> str:
    """块 id → 业务动作；未知块返回 ``""``（**绝不**回显块 id）。"""

    if not isinstance(block_id, str):
        return ""
    return BLOCK_ACTIONS.get(block_id.strip(), "")


def _short_action(block_id: str) -> str:
    """短语用的短动作：``台架自检（模拟集中器）`` → ``台架自检``。"""

    label = action_label(block_id)
    if not label:
        return ""
    return _TITLE_CUT.split(label, maxsplit=1)[0].strip() or label


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _as_mapping(source: Any) -> dict[str, Any]:
    """任意输入 → dict（支撑 Mapping / 带 ``to_dict()`` 的 dataclass；其余 → ``{}``）。"""

    if isinstance(source, Mapping):
        return dict(source)
    to_dict = getattr(source, "to_dict", None)
    if callable(to_dict):
        try:
            value = to_dict()
        except Exception:  # pragma: no cover - 防御：外部对象 to_dict 抛错不该拖垮报告
            return {}
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _text(value: Any) -> str:
    """标量 → 去空白字符串；容器 / ``None`` → ``""``（不编内容）。"""

    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _scalar(value: Any) -> str:
    """期望/实测值：只接受标量（dict/list 当没给，避免把结构体塞进人话）。"""

    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def _short_title(title: Any) -> str:
    """标题截短：``抄表日常回归（不复位，日常）：档案 → …`` → ``抄表日常回归``。"""

    text = str(title or "").strip()
    if not text:
        return ""
    head = _TITLE_CUT.split(text, maxsplit=1)[0].strip()
    return head or text


def _clean_detail(detail: Any) -> str:
    """detail → 可进人话的短文本：去块 id 前缀、块 id 换业务动作、折叠帧 hex。"""

    text = str(detail or "")
    text = _LEADING_ID_PREFIX.sub("", text)
    text = scrub_block_ids(text)
    text = _HEX_RUN.sub("帧", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _first_clause(text: str, limit: int = 24) -> str:
    """取第一小句当兜底原因（太长截断，不搬整段 detail）。

    兜底原因宁短勿长：只是「说不清 / 台架不具备」后面那句补充说明，
    用户要看的是动作与结论，不是块层的实现注释。
    """

    clause = re.split(r"[，,；;。（(\n]", text or "", maxsplit=1)[0].strip(" ：:、`")
    if len(clause) > limit:
        clause = clause[: limit - 1].rstrip() + "…"
    return clause


def _state_of(record: Mapping[str, Any]) -> str:
    """块记录的**块态**（``pass|fail|skipped|unknown``）。

    容忍两种输入：直接是块态（``BlockRecord.to_dict()``），或只给了结论层
    ``outcome``（``pass|fail|blocked|inconclusive``）——后者按
    :data:`executor.OUTCOME_TO_BLOCK` 反查，两套口径只有一份折算表。
    """

    raw = str(record.get("state") or "").strip().lower()
    if raw in BLOCK_STATES:
        return raw
    outcome = str(record.get("outcome") or "").strip().lower()
    if outcome in OUTCOME_TO_BLOCK:
        return OUTCOME_TO_BLOCK[outcome]
    return raw


def _has_any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


def _strip_prefix(text: str, prefix: str) -> str:
    return text[len(prefix) :].strip() if text.startswith(prefix) else text


# --------------------------------------------------------------------------- #
# 原因归类：四类人话模板
# --------------------------------------------------------------------------- #

#: 台架不具备的固定原因（命中关键词即用这几句，不再搬 detail 原文）
BENCH_REASON_SERIAL_BRIDGE = "串口桥未运行"
BENCH_REASON_SERIAL_BRIDGE_DOWN = "串口桥不可用"
BENCH_REASON_SIMCON_BUSY = "模拟集中器被占用"
BENCH_REASON_SIMETER_BUSY = "模拟电表表口被占用"
BENCH_REASON_UNREACHABLE = "工作台服务不可达"
BENCH_REASON_NO_BASE_URL = "工作台地址未配置"
BENCH_REASON_SERVICE_NOT_READY = "台架服务未就绪"
BENCH_REASON_CAPABILITY_NOT_READY = "台架能力未就绪"
BENCH_REASON_PRECONDITION = "前置条件未满足"


def _bench_reason(block_id: str, text: str) -> str:
    """``skipped`` / 台架关键词 → 「台架不具备：<原因>」里的**原因**部分。"""

    if "串口桥" in text:
        if "未运行" in text or "未启动" in text:
            return BENCH_REASON_SERIAL_BRIDGE
        if "不可用" in text or "不可读" in text or "未就绪" in text or "失败" in text:
            return BENCH_REASON_SERIAL_BRIDGE_DOWN
        return BENCH_REASON_SERIAL_BRIDGE

    if "占用" in text or "409" in text or "闸门忙" in text or "会话忙" in text or "冲突" in text:
        # 谁被占用：表口相关 → 模拟电表；其余（闸门 / simcon 会话） → 模拟集中器
        if (
            block_id.startswith(("meter.", "fix.simeter_session"))
            or "表口" in text
            or "simeter" in text
            or "模拟电表" in text
        ):
            return BENCH_REASON_SIMETER_BUSY
        return BENCH_REASON_SIMCON_BUSY

    if "不可达" in text or "连不上" in text or "unreachable" in text:
        return BENCH_REASON_UNREACHABLE

    if "未配置" in text and ("地址" in text or "base_url" in text):
        return BENCH_REASON_NO_BASE_URL

    if "404" in text or "未挂载" in text or "无会话" in text or "端口未开" in text:
        return BENCH_REASON_SERVICE_NOT_READY

    if "不就绪" in text or "未就绪" in text or "不支持" in text:
        return BENCH_REASON_CAPABILITY_NOT_READY

    if "前置" in text and "未满足" in text:
        return BENCH_REASON_PRECONDITION

    return _first_clause(text) or BENCH_REASON_PRECONDITION


def _bench_phrase(block_id: str, text: str) -> str:
    return "台架不具备：%s" % _bench_reason(block_id, text)


def _unclear_reason(action: str, text: str) -> str:
    """``unknown`` / 解析不出 → 「说不清：<原因>」里的**原因**部分。"""

    if "学习" in text:
        return "学习态尚未完成"
    if "解析" in text or "递归" in text:
        return "应答解析不出"
    if _has_any(text, _TIMEOUT_MARKERS):
        return "%s超时未应答" % action
    if _has_any(text, _MISMATCH_MARKERS):
        return "应答与期望不符"
    if "没有上行帧" in text or "无上行帧" in text or "取不到帧" in text:
        return "没拿到应答帧"
    if "取不到" in text or "未给出" in text or "字段缺失" in text:
        return "应答字段不全"
    clause = _first_clause(text)
    return clause or "没有拿到可判定的结论"


def _unclear_phrase(action: str, text: str) -> str:
    return "说不清：%s" % _unclear_reason(action, text)


def _addr_phrase(block_id: str, action: str, text: str, record: Mapping[str, Any]) -> str:
    """地址判据不成立 → 「绑定表地址不符（实读 X，期望 Y）」。"""

    expect = actual = ""
    match = _ADDR_PAIR.search(text)
    if match:
        expect, actual = match.group("expect"), match.group("actual")
    if not (expect and actual):
        expect = expect or _scalar(record.get("expected"))
        actual = actual or _scalar(record.get("actual"))
    base = "绑定表地址" if block_id == "meter.addr_probe" else ("%s表地址" % action if action else "表地址")
    if expect and actual:
        return "%s不符（实读 %s，期望 %s）" % (base, actual, expect)
    if expect:
        return "%s不符（期望 %s，实际没读到）" % (base, expect)
    return "%s不符" % base


def _numeric_phrase(action: str, text: str, record: Mapping[str, Any]) -> str:
    """数值 / 数量不符 → 带实测值的短语（拿不到值就退化成「<动作>异常」）。"""

    expect = actual = unit = ""
    match = _VALUE_PAIR.search(text)
    if match:
        expect = match.group("expect")
        actual = match.group("actual")
        unit = match.group("actual_unit") or match.group("expect_unit")
    if not (expect and actual):
        # detail 里没有数对时，看记录本身有没有标量 expected / actual（更可靠）
        expect = expect or _scalar(record.get("expected"))
        actual = actual or _scalar(record.get("actual"))
    if not (expect and actual):
        return "%s异常" % action

    if _has_any(text, _COUNT_MARKERS):
        return "%s数量不符（实得 %s，期望 %s）" % (action, actual, expect)
    suffix = unit.strip()
    return "%s数值不符（读 %s%s，期望 %s%s）" % (action, actual, suffix, expect, suffix)


def classify_failure(block_record: Mapping[str, Any]) -> str:
    """单块记录 → 人话失败原因（四类模板；``pass`` 返回 ``""``）。

    输入是 ``BlockRecord.to_dict()``（或同形 dict / 带 ``to_dict()`` 的对象）：
    只用 ``block_id`` / ``state`` / ``detail``（可选 ``expected`` / ``actual``）。
    输出**绝不含块 id**，也不含帧 hex——用户只需要知道「哪件事、为什么没成」。
    """

    record = _as_mapping(block_record)
    if not record:
        return _unclear_phrase(FALLBACK_ACTION, "")

    block_id = str(record.get("block_id") or record.get("block") or "").strip()
    action = _short_action(block_id) or FALLBACK_ACTION
    state = _state_of(record)
    # ``BlockRecord.to_dict()`` 同时给 ``detail`` 与 ``message``（后者是断言消息，
    # detail 缺失时用它，别因为少一个键就说「说不清」）
    text = _clean_detail(record.get("detail") or record.get("message"))

    if state == BLOCK_PASS:
        return ""

    if state == BLOCK_SKIPPED:
        return _bench_phrase(block_id, text)

    if state == BLOCK_FAIL:
        # ① 判据不成立里最具体的一类：地址 / 数值不符（带实测值）
        if _has_any(text, _ADDR_MARKERS):
            return _addr_phrase(block_id, action, text, record)
        if _has_any(text, _MISMATCH_MARKERS) and (
            _VALUE_PAIR.search(text)
            or (_scalar(record.get("expected")) and _scalar(record.get("actual")))
        ):
            return _numeric_phrase(action, text, record)
        # ② 帧发出去了但没等到应答
        if _has_any(text, _TIMEOUT_MARKERS):
            return "%s超时" % action
        # ③ 台架类原因即便是 fail 也按台架说（不把环境问题赖到设备上）
        if _has_any(text, _BENCH_MARKERS):
            return _bench_phrase(block_id, text)
        # ④ 有应答但判据不成立
        return "%s异常" % action

    if state == BLOCK_UNKNOWN:
        if _has_any(text, _BENCH_MARKERS):
            return _bench_phrase(block_id, text)
        return _unclear_phrase(action, text)

    # 块态缺失 / 无法识别：一律「说不清」，绝不猜成 pass 或 fail
    if _has_any(text, _BENCH_MARKERS):
        return _bench_phrase(block_id, text)
    return _unclear_phrase(action, text)


# --------------------------------------------------------------------------- #
# 结论层：verdict / 一句话 headline
# --------------------------------------------------------------------------- #

#: verdict 的中文标签（报告里只在「说不清 / 台架不具备 / 未通过」这三处出现；
#: ``pass`` 固定写 ``PASS``，与 REQS D11-1 例子一致）
VERDICT_LABELS: dict[str, str] = {
    OUTCOME_PASS: "PASS",
    OUTCOME_FAIL: "未通过",
    OUTCOME_BLOCKED: "台架不具备",
    OUTCOME_INCONCLUSIVE: "说不清",
}


def _verdict_of(data: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]) -> str:
    """用例 verdict：优先取输入里合法的 ``verdict``，否则从块态聚合（不猜 pass）。"""

    raw = str(data.get("verdict") or data.get("overall_verdict") or "").strip().lower()
    if raw in OUTCOMES:
        return raw
    outcomes = [
        BLOCK_TO_OUTCOME.get(_state_of(record), OUTCOME_INCONCLUSIVE) for record in blocks
    ]
    return aggregate_verdict(outcomes)


def _headline(title: str, verdict: str, reasons: Sequence[str]) -> str:
    """一句话结论：``标题（PASS）`` / ``标题（未通过：组网超时）`` …"""

    name = title or "本轮运行"
    if verdict == OUTCOME_PASS:
        return "%s（PASS）" % name
    if verdict == OUTCOME_FAIL:
        # 未通过里也可能夹着台架/说不清的块（多用例汇总时很常见）：
        # 同类归并成一段，避免出现「未通过：台架不具备：…」这种套娃。
        hard = [r for r in reasons if not r.startswith(("台架不具备：", "说不清："))]
        bench = [_strip_prefix(r, "台架不具备：") for r in reasons if r.startswith("台架不具备：")]
        unclear = [_strip_prefix(r, "说不清：") for r in reasons if r.startswith("说不清：")]
        parts: list[str] = []
        if hard:
            parts.append("、".join(hard))
        if bench:
            parts.append("台架不具备：%s" % "、".join(bench))
        if unclear:
            parts.append("说不清：%s" % "、".join(unclear))
        return "%s（未通过：%s）" % (name, "；".join(parts) or "判据不成立")
    if verdict == OUTCOME_BLOCKED:
        first = _pick_reason(reasons, "台架不具备：")
        detail = _strip_prefix(first, "台架不具备：") or BENCH_REASON_PRECONDITION
        return "%s（台架不具备：%s）" % (name, detail)
    first = _pick_reason(reasons, "说不清：")
    detail = _strip_prefix(first, "说不清：") or "没有拿到可判定的结论"
    return "%s（说不清：%s）" % (name, detail)


def _pick_reason(reasons: Sequence[str], prefix: str) -> str:
    """挑一条与 verdict 同类的 reason（混合原因时 headline 只说最贴题的那条）。"""

    for reason in reasons:
        if reason.startswith(prefix):
            return reason
    return reasons[0] if reasons else ""


# --------------------------------------------------------------------------- #
# 证据路径（D11-3：只从输入里已有的键组合，不确定就空列表，绝不编）
# --------------------------------------------------------------------------- #


def evidence_paths(source: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """从 ``run_id`` / ``result_path`` / ``runs_dir``（及已有 ``evidence_paths``）组合路径。

    返回 ``[{"label": "运行结果", "path": "…/case_runs/<run_id>/result.json"}, …]``，
    顺序即展示序，按 path 去重。**输入里没有的键不去猜**：什么都不知道就返回 ``[]``
    （宁可没有证据链接，也不给用户一个打不开的假路径）。
    """

    data = _as_mapping(source)
    items: list[dict[str, str]] = []

    def _add(label: str, path: Any) -> None:
        value = _text(path)
        if not value:
            return
        if any(item["path"] == value for item in items):
            return
        items.append({"label": label, "path": value})

    # ① 调用方直接给了 result.json 的落盘路径
    _add("运行结果", data.get("result_path"))

    # ② 给了结果根 + run_id：按 CLI 约定拼 <runs_dir>/<run_id>/result.json
    runs_dir = _text(data.get("runs_dir")) or _text(data.get("result_dir"))
    run_id = _text(data.get("run_id"))
    if runs_dir and run_id:
        run_dir = os.path.join(runs_dir, run_id)
        _add("运行目录", run_dir)
        _add("运行结果", os.path.join(run_dir, "result.json"))
    elif runs_dir:
        _add("运行目录", runs_dir)

    # ③ 调用方已经算好的证据列表（原样透传，字符串按「证据」标）
    raw = data.get("evidence_paths")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        for entry in raw:
            if isinstance(entry, Mapping):
                _add(str(entry.get("label") or "证据"), entry.get("path"))
            else:
                _add("证据", entry)

    # ④ 常见日志键（存在才列）
    for key, label in (
        ("log_path", "运行日志"),
        ("cli_log", "CLI 日志"),
        ("log_file", "运行日志"),
        ("frames_log", "帧日志"),
        ("evidence_dir", "证据目录"),
    ):
        _add(label, data.get(key))

    return items


# --------------------------------------------------------------------------- #
# 对外 API：用例摘要 / 运行摘要
# --------------------------------------------------------------------------- #


def summarize_case(case_result: Mapping[str, Any], *, title: str = "") -> dict[str, Any]:
    """用例结果 → 一句话报告（CLI / API / UI 共用）。

    返回::

        {
          "verdict": "pass|fail|blocked|inconclusive",
          "headline": "抄表日常回归（未通过：组网超时）",
          "reason": "组网超时",           # reasons 的合并串（pass 时为空串）
          "reasons": ["组网超时", …],     # 按块执行序、去重
          "evidence_paths": [{"label": "运行结果", "path": "…"}],
          "case_id": "meter_daily",
          "title": "抄表日常回归",
        }

    输入是 ``CaseRunResult.to_dict()``（或同形 dict）。``blocks[]`` 里**只翻非 pass 的块**：
    通过的那几步不需要解释，用户要的是「哪儿没成」。原因**只用业务动作**表述，
    块 id 一律过滤（D11-2）。
    """

    data = _as_mapping(case_result)

    blocks_raw = data.get("blocks")
    if isinstance(blocks_raw, Mapping):
        blocks_raw = list(blocks_raw.values())
    if not isinstance(blocks_raw, Sequence) or isinstance(blocks_raw, (str, bytes)):
        blocks_raw = []
    blocks = [record for record in (_as_mapping(item) for item in blocks_raw) if record]

    verdict = _verdict_of(data, blocks)

    reasons: list[str] = []
    for record in blocks:
        if _state_of(record) == BLOCK_PASS:
            continue
        phrase = classify_failure(record)
        if phrase and phrase not in reasons:
            reasons.append(phrase)

    case_id = str(data.get("case_id") or data.get("id") or "").strip()
    display_title = _short_title(title) or _short_title(data.get("title")) or case_id

    return {
        "verdict": verdict,
        "headline": _headline(display_title, verdict, reasons),
        "reason": "、".join(reasons),
        "reasons": reasons,
        "evidence_paths": evidence_paths(data),
        "case_id": case_id,
        "title": display_title,
    }


def _case_list(data: Mapping[str, Any]) -> list[Any]:
    """运行记录里的用例结果列表（``cases`` / ``case_results`` / ``results``，或本身就是一例）。"""

    for key in ("cases", "case_results", "results"):
        value = data.get(key)
        if isinstance(value, Mapping):
            return list(value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return list(value)
    if data.get("case_id") or data.get("blocks"):
        return [data]
    return []


def summarize_run(
    record: Mapping[str, Any], *, titles: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """一次运行（多用例 / 方案）→ 汇总一句话报告。

    返回::

        {
          "verdict": "fail",                       # 取最严重：fail>blocked>inconclusive>pass
          "headline": "日常回归（未通过：组网超时、抄表超时）",
          "reason": "组网超时、抄表超时",
          "reasons": ["组网超时", …],               # 跨用例、按用例序去重
          "case_summaries": [summarize_case(...), …],
          "evidence_paths": [{"label": "运行结果", "path": "…"}],
        }

    ``titles`` 给的是 ``{case_id: 业务标题}``：UI / CLI 有更友好的用例名就传进来，
    没传就用记录里的 ``title``，再没有就用 ``case_id``（``case_id`` 不是块 id，
    对外可见安全）。
    """

    data = _as_mapping(record)
    title_map = {str(key): str(value) for key, value in (titles or {}).items()}

    summaries: list[dict[str, Any]] = []
    for entry in _case_list(data):
        case = _as_mapping(entry)
        if not case:
            continue
        case_id = str(case.get("case_id") or case.get("id") or "").strip()
        case_title = title_map.get(case_id) or str(case.get("title") or "")
        summaries.append(summarize_case(case, title=case_title))

    if summaries:
        verdict = aggregate_verdict(summary["verdict"] for summary in summaries)
    else:
        verdict = _verdict_of(data, [])

    reasons: list[str] = []
    for summary in summaries:
        if summary["verdict"] == OUTCOME_PASS:
            continue
        for reason in summary["reasons"]:
            if reason not in reasons:
                reasons.append(reason)

    run_title = _short_title(
        data.get("title") or data.get("suite_name") or data.get("suite_title") or data.get("suite_id")
    )

    return {
        "verdict": verdict,
        "headline": _headline(run_title, verdict, reasons),
        "reason": "、".join(reasons),
        "reasons": reasons,
        "case_summaries": summaries,
        "evidence_paths": evidence_paths(data),
    }




# --------------------------------------------------------------------------- #
# 报表契约（REQS-0044 变更 3 L2：字段冻结，前端 Demo 代理按同一份开发）
# --------------------------------------------------------------------------- #


def steps_text_of(case: Any) -> str:
    """Procedure → ``1.xxx；2.xxx``（业务动作文本，**零块名**，0042 D11-2）。

    动作词来自 :func:`action_label`（块 id → 业务动作，未知块兜底「该步骤」，
    绝不回显块 id）；重复动作（如两次抄表）按步骤号各占一项。
    """

    labels: list[str] = []
    effective = getattr(case, "effective_steps", None)
    steps = list(effective()) if callable(effective) else []
    for index, step in enumerate(steps, 1):
        block_id = str(getattr(step, "block", "") or "")
        label = action_label(block_id) or FALLBACK_ACTION
        labels.append("%d.%s" % (index, label))
    return "；".join(labels)


def build_run_report(entries: Sequence[Mapping[str, Any]], *,
                     cases: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """运行结果 → L2 报表契约（``run.report.rows[]`` + ``run.report.summary``）。

    ``entries``：每条用例结果（``summary_entry`` 形态：``case_id`` / ``title`` /
    ``verdict`` / ``summary``）；``cases``：``{case_id: ProcedureSpec}``（取 kind /
    category / 步骤表；缺省按 regression 处理）。字段语义**只加不改**：

    - ``group`` = category.scene、``subgroup`` = category.domain；``kind=setup`` 时
      两项为 ``null``（渲染「—」，L1）；
    - ``reason``：一句话人话（pass 为 ``null``，L3）；``counts_as_product``：
      setup=false，其余 true（产品通过率分母不含 setup 行）。
    """

    rows: list[dict[str, Any]] = []
    counts = {"total": 0, "pass": 0, "fail": 0, "blocked": 0, "inconclusive": 0}
    product_total = 0
    product_pass = 0
    case_map = dict(cases or {})

    for entry in entries:
        data = _as_mapping(entry)
        case_id = str(data.get("case_id") or data.get("id") or "")
        case = case_map.get(case_id)
        kind = str(getattr(case, "kind", "") or "regression")
        is_setup = kind == "setup"
        category = getattr(case, "category", None)
        summary = _as_mapping(data.get("summary"))
        verdict = str(data.get("verdict") or "").strip().lower()
        if verdict not in OUTCOMES:
            verdict = OUTCOME_INCONCLUSIVE

        reasons = [str(item) for item in (summary.get("reasons") or []) if str(item)]
        counts["total"] += 1
        if verdict in counts:
            counts[verdict] += 1
        if not is_setup:
            product_total += 1
            if verdict == OUTCOME_PASS:
                product_pass += 1

        evidence: list[str] = []
        for item in (summary.get("evidence_paths") or []):
            path = _text(_as_mapping(item).get("path") if isinstance(item, Mapping) else item)
            if path and path not in evidence:
                evidence.append(path)

        rows.append({
            "group": None if is_setup else _text(getattr(category, "scene", "")) or None,
            "subgroup": None if is_setup else _text(getattr(category, "domain", "")) or None,
            "name": _short_title(data.get("title")) or case_id,
            "kind": kind,
            "steps_text": steps_text_of(case) if case is not None else "",
            "verdict": verdict,
            "reason": None if verdict == OUTCOME_PASS else ("、".join(reasons) or None),
            "evidence_paths": evidence,
            "counts_as_product": not is_setup,
        })

    return {
        "rows": rows,
        "summary": {
            "total": counts["total"],
            "pass": counts["pass"],
            "fail": counts["fail"],
            "blocked": counts["blocked"],
            "inconclusive": counts["inconclusive"],
            "product_pass_rate": "%d/%d" % (product_pass, product_total),
        },
    }


__all__ = [
    "BLOCK_ACTIONS",
    "FALLBACK_ACTION",
    "VERDICT_LABELS",
    "action_label",
    "classify_failure",
    "contains_block_id",
    "evidence_paths",
    "scrub_block_ids",
    "summarize_case",
    "summarize_run",
    # 报表契约（REQS-0044 变更 3 L2）
    "build_run_report",
    "steps_text_of",
]
