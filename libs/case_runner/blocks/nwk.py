"""组网类原子块：``nwk.route_restart`` / ``nwk.active_registration`` /
``nwk.archive_count``（兼容别名 ``nwk.onnet_count``）/ ``nwk.topology`` /
``nwk.nodes_online`` / ``nwk.route_status``（REQS-0041 §10.5 BL-3、REQS-0042 S2-A）。

判据
----
==========================  ==================================================
``nwk.route_restart``       12H-F1（无参）→ 00H-F1 确认
``nwk.active_registration`` 11H-F5 → 00H-F1 确认；**必填四参**
                            ``time6`` + ``duration`` + ``retry`` + ``slices``，
                            缺参在**下发前**就 fail 并点名缺哪个（REQS §10.7 事实 I：
                            ``encode_app_data(0x11, 5, {})`` 会抛「缺少参数 duration」）
``nwk.archive_count``       10H-F1（无参）→ 上行「从节点总数量」= **路由档案数** ≥ ``min``
``nwk.topology``            **入网权威判据**：10H-F21「节点总数量」≥
                            ``ceil(档案数 × min_ratio)``（默认 0.98 = 98%）
``nwk.nodes_online``        **真实可达（在网）节点数**：10H-F4「已抄从节点数量」与
                            10H-F2 逐节点「信号品质>0」两路交叉，> 0 才可能 pass
``nwk.route_status``        10H-F4 → 「工作状态/当前状态」+「运行状态字」路由完成标志分层
==========================  ==================================================

等待轮询（REQS-0042 S2-A）
--------------------------
组网是**过程**，单帧要么把正常注册中的 CCO 判失败，要么把还没入网的节点判通过。
三个只读块因此都带 ``wait_s`` / ``poll_interval_s``：反复探测直到满足判据，
等满 ``wait_s`` 仍不满足才出结论（``nodes_online`` 90s / ``route_status`` 60s /
``topology`` 60s，间隔 2s）。铁律：

- **等待期只发只读查询**：``10H-F21`` / ``10H-F1`` / ``10H-F4`` / ``10H-F2``
  （``READONLY_AFN_FNS`` 白名单 + :func:`readonly_violations` 闸门，测试逐跳断言）；
- **等待可注入**：``clock`` / ``sleep`` 两个块参数（callable），或能力对象上的
  ``clock`` / ``sleeper`` 属性，取不到才回落到 :data:`_clock` / :data:`_sleep`
  —— 单测注入虚拟时钟即可零真实睡眠跑完 60s/90s 等待；
- **不乱判**：等满仍不足 → ``fail``（明确不足）；应答取不到/解析不出 → ``unknown``；
  档案数为 0 → ``unknown``（「没有可入网目标」不是产品失败）。

历史缺陷（真实台架已复现）
--------------------------
``nwk.onnet_count`` 曾把 10H-F1 的「从节点总数量」当成可达节点数：台架实测
``从节点总数量=12 / 已抄从节点数量=0 / 12 个节点信号品质全 0``，旧块判
「12 ≥ 期望 1 → 组网通 → PASS」= **假 PASS**。元数据
（``adapter_10376/metadata/afn_fn.json`` 10H-F1 ``resp.fields[0].d``）明确写的是
「当前路由档案中的从节点总数」——**档案数**，不是在线数。因此：

- 旧块正名为 ``nwk.archive_count``（判据 = 档案数 ≥ ``min``，detail 里写明
  「这是路由档案中的从节点总数，不等于真实可达的节点数」）；
- 新块 ``nwk.nodes_online`` 用 10H-F4「已抄数」+ 10H-F2「信号品质」两路证据判真实可达；
- ``nwk.onnet_count`` 只作兼容别名保留（内部委托 ``nwk.archive_count``）。

组网与档案的事实关系（不在代码里写死顺序）
-----------------------------------------
**组网前应有档案**：CCO 只给"在档案里"的从节点分配网内地址，档案数是入网数的上限。
块层因此**不**强制 ``needs: ["archive.contains"]``——顺序由用例编排
（``CaseSpec.blocks``）决定；本文件只在文档与 detail 里提示这一事实关系。

依赖方向（REQS §10.5 解耦要求）
-----------------------------
本文件**不 import 任何兄弟块模块**：通用 1376.2 单步/取帧/判定小工具、10H-F1/F4 组网
快照与判定都在 ``blocks/common.py``（**只有一份**，事实探测体 ``facts_nwk.py`` 也从
``common`` 取同一对象，REQS FT-2）。历史问题：这些件曾经长在 ``archive.py`` 里，导致
组网块曾必须从 ``blocks/archive.py`` 里取这些工具，档案与组网互相牵连。
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from case_runner.blocks.common import (  # noqa: F401 - 下面一并对旧调用方再导出
    AFN_CONFIRM,
    AFN_ROUTE_QUERY,
    ARCHIVE_COUNT_NOTE,
    DEFAULT_MIN_ONLINE,
    DEFAULT_NODES_ONLINE_COUNT,
    DEFAULT_TIMEOUT,
    EVIDENCE_ARCHIVED,
    EVIDENCE_QUALITY,
    FN_ARCHIVE_QUERY,
    FN_CONFIRM,
    FN_ONNET_COUNT,
    FN_ROUTE_STATUS,
    ITEM_ANSWERED_TOTAL,
    ITEM_CURRENT_STATE,
    ITEM_RELAY_ANSWERED_TOTAL,
    ITEM_RUN_WORD,
    ITEM_SIGNAL_QUALITY,
    ITEM_SWITCH,
    ITEM_TOTAL,
    ITEM_WORK_STATE,
    ROUTE_LEARNING_INCOMPLETE,
    ROUTE_LEARNING_INCONCLUSIVE,
    ROUTE_RUNNING,
    ROUTE_UNKNOWN,
    WORK_LEARNING,
    WORK_RUNNING,
    afn_int,
    archive_count_snapshot,
    block_action_params,
    call_verify,
    capability_gate,
    classify_route_state,
    coerce_float,
    flag_of,
    fn_int,
    int_of,
    item_value,
    items_of,
    judge_archive_count_min,
    judge_confirm_step,
    judge_nodes_online,
    judge_onnet_min,
    judge_route_running,
    judge_route_status,
    judge_step,
    nodes_online_snapshot,
    onnet_snapshot,
    require_target,
    resolve_simcon,
    route_snapshot,
    route_status_fields,
    send_step,
    snapshot_kind,
    steps_of,
    verify_failure_state,
)
from case_runner.core.executor import BlockResult
from case_runner.core.planner import FactValue
from case_runner.core.expect import (
    OBS_BENCH_UNREACHABLE,
    OBS_INCOMPLETE,
    OBS_INVALID_PARAM,
    OBS_SERIAL_BUSY,
    OBS_TIMEOUT,
    Observation,
)
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_UNKNOWN,
    FACT_NO,
    FACT_UNKNOWN,
    FACT_YES,
)
from case_runner.core.registry import (
    BLOCK_REGISTRY,
    Duplicate,
    register_block,
    unregister_block,
)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

BLOCK_ROUTE_RESTART = "nwk.route_restart"
BLOCK_ACTIVE_REGISTRATION = "nwk.active_registration"
BLOCK_ARCHIVE_COUNT = "nwk.archive_count"
#: **兼容别名**：旧 id 内部委托 ``nwk.archive_count``（判的一直是档案数，历史命名错误）
BLOCK_ONNET_COUNT = "nwk.onnet_count"
BLOCK_TOPOLOGY = "nwk.topology"
BLOCK_NODES_ONLINE = "nwk.nodes_online"
BLOCK_ROUTE_STATUS = "nwk.route_status"

FACT_ONNET_COUNT_MIN = "nwk.onnet_count_min"     # 兼容别名事实（判档案数）
FACT_NODES_ONLINE_MIN = "nwk.nodes_online_min"   # 真在网事实（参数 min）
FACT_ROUTE_RUNNING = "nwk.route_running"

AFN_ROUTE_CONTROL = 0x12        # 12H：路由控制
FN_ROUTE_RESTART = 1            # 12H-F1 启动路由（重新组网）
AFN_NWK_SET = 0x11              # 11H：路由设置
FN_ACTIVE_REGISTRATION = 5      # 11H-F5 激活从节点主动注册
FN_TOPO_QUERY = 21              # 10H-F21 查询网络拓扑信息（入网权威判据）

# --------------------------------------------------------------------------- #
# 等待轮询与 10H-F21 相关常量（REQS-0042 S2-A）
# --------------------------------------------------------------------------- #

#: 模块级时钟/睡眠：单测可 ``monkeypatch.setattr(nwk, "_clock", ...)``；
#: 运行期可经块参数 ``clock=`` / ``sleep=``（callable）或能力对象上的
#: ``clock`` / ``sleeper`` / ``sleep`` 属性注入 —— 注入后**零真实睡眠**。
_clock: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep

#: 轮询间隔默认 2s（协议没有规定探测周期，按台架观测网络拓扑的刷新节奏取 2s）
DEFAULT_POLL_INTERVAL = 2.0
#: 三个只读块的等待上限（组网是过程：学习/注册/抄表都要时间）。
#:
#: **共同依据（别拍脑袋调小）**：台架实测一轮入网约 **240s**（4 分钟），学习态转抄表
#: 发生在这之后。等待期是逐轮探测 + 达标即停，所以：
#:
#: - **成功路径不付这个代价**：已入网的站最多等 1 个 ``poll_interval_s``（2s）就 pass；
#: - 只有「确实不达标」才走满预算 —— 这正是该等的，也是唯一能给出 ``fail`` 的路径；
#: - 预算 < 实测入网时长（例如曾用的 60s）会把「还在入网」判成「明确不足 → fail」，
#:   即**假 FAIL**，比等得久伤得多。
#:
#: ⇒ 统一按「实测 240s + 60s 余量 = **300s**」给预算；``nodes_online`` 判的是「至少 1 个
#: 节点抄通」（首个节点入网通常 10~60s），给 **180s**（实测值的一半 + 余量）即可，
#: 不必占用整站预算。
DEFAULT_WAIT_TOPOLOGY = 300.0
DEFAULT_WAIT_NODES_ONLINE = 180.0
DEFAULT_WAIT_ROUTE_STATUS = 300.0

#: 轮询期**只读**帧白名单：AFN → 允许的 Fn 集合。写帧（12H-F1 / 11H-F1 / 01H-F2 …）
#: 一旦混进等待循环就被 :func:`readonly_violations` 拦下，块不下发。
READONLY_AFN_FNS: Dict[int, frozenset] = {
    AFN_ROUTE_QUERY: frozenset({FN_TOPO_QUERY, FN_ONNET_COUNT, FN_ROUTE_STATUS, FN_ARCHIVE_QUERY}),
}

#: 10H-F21 上行字段名（解析器实际输出的名字；``n`` 后缀是协议文档的写法）
ITEM_TOPO_TOTAL = "节点总数量"
ITEM_TOPO_START = "节点起始序号"
ITEM_TOPO_ANSWERED = "本次应答的节点数量"
ITEM_TOPO_ANSWERED_ALIAS = "本次应答的节点数量n"
#: 10H-F21 上行逐节点记录的前后缀（``节点1地址`` / ``节点1网络拓扑``）
TOPO_RECORD_PREFIX = "节点"
TOPO_ADDR_SUFFIX = "地址"
TOPO_INFO_SUFFIX = "网络拓扑"
#: 节点角色里的主节点（节点信息 D4~D7 = 0x4，见 KB 全帧类型 F21 位表）
TOPO_ROLE_MASTER = "主节点CCO"

#: 10H-F21 下行默认：起始序号 **1 起编号**、本次查询 32 条
DEFAULT_TOPO_START = 1
DEFAULT_TOPO_COUNT = 32
#: 入网验收比例（KB 业务层 37：节点总数量 = 档案个数的 98%~100%）
DEFAULT_MIN_RATIO = 0.98

TOPO_START_NOTE = (
    "10H-F21 的节点起始序号按 **1 起编号**（KB 用例库/CCO/台账/146:62 用例 87"
    "「起始序号=1，节点数量=2」；协议层/104 与 协议层/91 同为「首帧从 1 开始」）"
)

#: 协议里「工作状态 / 当前状态」的合法取值（文档用；判定实现见 common）
WORK_STATES = (WORK_RUNNING, WORK_LEARNING)
CURRENT_STATES = ("抄表", "搜表", "升级", "其他")
#: 默认「期望档案数下限」（``nwk.archive_count`` 的 ``min`` 语义）
DEFAULT_MIN_ARCHIVE = 1
#: 兼容别名：旧名字指的也是档案数
DEFAULT_MIN_ONNET = DEFAULT_MIN_ARCHIVE
#: ``nwk.route_status`` 的分类标签（= ``common.classify_route_state`` 的取值集合）
ROUTE_LABELS = (
    ROUTE_RUNNING,
    ROUTE_LEARNING_INCONCLUSIVE,
    ROUTE_LEARNING_INCOMPLETE,
    ROUTE_UNKNOWN,
)

#: 11H-F5 构帧四参（REQS §10.7 事实 I）
REGISTRATION_TIME_KEYS = ("time6", "time", "sec")
REGISTRATION_VALUE_KEYS = ("duration", "retry", "slices")
REGISTRATION_VALUE_RANGE = {"duration": (0, 0xFFFF), "retry": (0, 0xFF), "slices": (0, 0xFF)}
_TIME_PARTS = ("sec", "min", "hour", "day", "mon", "year")


# --------------------------------------------------------------------------- #
# 11H-F5 四参处理
# --------------------------------------------------------------------------- #


def time6_to_digits(value: Any) -> Tuple[Optional[str], str]:
    """6 字节 BCD 时间 hex（``"000000000126"``）→ 构帧器的 ``"SSMMHHDDMMYY"`` 数字串。

    BCD 每字节高/低半字节都必须 ≤ 9，否则不是合法 BCD（A~F 会变成非法十进制位）。
    """
    text = "".join(str(value).split())
    if text[:2].lower() == "0x":
        text = text[2:]
    if len(text) != 12:
        return None, "time6 必须是 6 字节 BCD 时间的 12 位 hex（如 000000000126），收到 %r" % (
            value,
        )
    digits: List[str] = []
    for index in range(0, 12, 2):
        pair = text[index:index + 2]
        if pair[0].upper() in "ABCDEF" or pair[1].upper() in "ABCDEF":
            return None, "time6 含非 BCD 半字节（%r），BCD 每位必须 0~9：%r" % (pair, value)
        digits.append(pair)
    return "".join(digits), ""


def registration_time_params(action: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """把三种时间写法归一成构帧器要的 ``{"time": ...}`` 或分项字典。"""
    time6 = action.get("time6")
    if time6 not in (None, ""):
        digits, error = time6_to_digits(time6)
        if error:
            return None, error
        return {"time": digits}, ""
    plain = action.get("time")
    if plain not in (None, ""):
        text = "".join(str(plain).split()).replace("-", "").replace(":", "")
        if len(text) != 12 or not text.isdigit():
            return None, (
                "参数 time 须为 12 位数字（秒分时日月年，如 \"000000000126\"）：%r" % (plain,)
            )
        return {"time": text}, ""
    if any(key in action for key in _TIME_PARTS):
        payload: Dict[str, Any] = {}
        for key in _TIME_PARTS:
            value = int_of(action.get(key, 0))
            if value is None:
                return None, "参数 %s 不是整数：%r" % (key, action.get(key))
            payload[key] = value
        return payload, ""
    return None, (
        "缺少必填参数 time6（6 字节 BCD 时间 hex，如 000000000126；"
        "也接受 time=\"SSMMHHDDMMYY\" 或 sec/min/hour/day/mon/year 分项）"
    )


def registration_payload(action: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """11H-F5 全参构造：四参齐给才放行，缺哪个点名哪个（下发前失败）。"""
    problems: List[str] = []
    time_payload, time_error = registration_time_params(action)
    if time_error:
        problems.append(time_error)

    missing = [key for key in REGISTRATION_VALUE_KEYS if action.get(key) in (None, "")]
    if missing:
        problems.append("缺少必填参数 %s" % "/".join(missing))

    values: Dict[str, int] = {}
    if not missing:
        for key in REGISTRATION_VALUE_KEYS:
            number = int_of(action.get(key))
            low, high = REGISTRATION_VALUE_RANGE[key]
            if number is None:
                problems.append("参数 %s 不是整数：%r" % (key, action.get(key)))
            elif not low <= number <= high:
                problems.append("参数 %s 越界 [%d,%d]：%r" % (key, low, high, action.get(key)))
            else:
                values[key] = number

    if problems:
        return None, (
            "%s 参数不全/非法：%s（11H-F5 构帧必须四参齐给："
            "time6 + duration + retry + slices）" % (BLOCK_ACTIVE_REGISTRATION, "；".join(problems))
        )
    payload = dict(time_payload or {})
    payload.update(values)
    return payload, ""


# --------------------------------------------------------------------------- #
# 等待轮询基础设施（只读 + 可注入）
# --------------------------------------------------------------------------- #


def format_seconds(value: Any) -> str:
    """等待时长 → 人读秒数：整数秒不带小数点（detail 里统一写「等了 N 秒」）。"""
    seconds = coerce_float(value, 0.0)
    rounded = round(seconds)
    if abs(seconds - rounded) < 1e-6:
        return "%d" % int(rounded)
    return "%.1f" % seconds


def readonly_violations(steps: Any) -> List[str]:
    """检查一组步骤里有没有**非只读**帧，返回违例说明（空列表 = 全是只读查询）。

    为什么要有这道闸门：``nwk.route_restart`` / ``nwk.active_registration`` 会发写帧
    （12H-F1 / 11H-F5），一旦被误当成"探测"放进等待循环，就会在等组网的 90 秒里反复
    重启路由/重开注册窗口，把被测设备打乱。白名单只有 :data:`READONLY_AFN_FNS`。
    """
    violations: List[str] = []
    for step in steps or []:
        send = step.get("send") if isinstance(step, Mapping) else None
        if not isinstance(send, Mapping):
            violations.append("步骤缺少 send 段，无法确认是只读查询：%r" % (step,))
            continue
        afn = afn_int(send.get("afn"))
        fn = fn_int(send.get("fn"))
        allowed = READONLY_AFN_FNS.get(afn if isinstance(afn, int) else -1, frozenset())
        if fn not in allowed:
            violations.append(
                "非只读帧 afn=%r fn=%r（等待期只允许 10H-F21/F1/F4/F2 只读查询）"
                % (send.get("afn"), send.get("fn"))
            )
    return violations


def _first_callable(*candidates: Any) -> Optional[Callable]:
    for candidate in candidates:
        if callable(candidate):
            return candidate
    return None


def poll_hooks(action: Mapping[str, Any], cap: Any = None) -> Tuple[Callable, Callable]:
    """解析注入的 ``(clock, sleep)``：块参数 → 能力对象属性 → 模块默认（真实时钟）。

    块参数里的 ``clock`` / ``sleep`` 只可能是 callable（JSON 里的字符串会被忽略），
    所以声明文件写不出"注入"来，注入只发生在测试或外部装配层。
    """
    clock = _first_callable(action.get("clock"), getattr(cap, "clock", None), _clock)
    sleeper = _first_callable(
        action.get("sleep"),
        action.get("sleeper"),
        getattr(cap, "sleeper", None),
        getattr(cap, "sleep", None),
        _sleep,
    )
    return clock, sleeper


def poll_snapshot(
    probe: Callable[[], Any],
    *,
    wait_s: Any = 0.0,
    interval_s: Any = DEFAULT_POLL_INTERVAL,
    stop: Optional[Callable[[Any], Optional[str]]] = None,
    clock: Any = None,
    sleep: Any = None,
) -> Dict[str, Any]:
    """反复调**只读**探针 ``probe()``，直到 ``stop(snapshot)`` 返回原因或等满 ``wait_s``。

    返回::

        {"snapshot": 最后一次快照, "attempts": 探测次数, "waited": 等了 N 秒,
         "reason": "satisfied"/"timed_out"/自定义原因, "timed_out": bool,
         "snapshots": [每轮快照]}

    终止性保证（三条，缺一条就会挂测试或打爆台架）：

    1. ``wait_s=0`` → 只探一次、不 sleep（既有单测/``show`` 口径不变）；
    2. ``attempts`` 上限 ``ceil(wait_s / tick) + 1`` —— 即使注入的时钟不动
       （或 ``poll_interval_s=0`` 不真 sleep）也一定收敛，不会无限循环；
    3. ``waited`` 取 ``max(真实经过时间, (attempts-1) × tick)``，
       所以虚拟时钟/不睡眠模式下 detail 里的「等了 N 秒」仍是协议口径的秒数。
    """
    now = clock if callable(clock) else _clock
    pause = sleep if callable(sleep) else _sleep
    limit = coerce_float(wait_s, 0.0)
    interval = coerce_float(interval_s, DEFAULT_POLL_INTERVAL)
    tick = interval if interval > 0 else DEFAULT_POLL_INTERVAL
    max_attempts = int(math.ceil(limit / tick)) + 1 if limit > 0 else 1
    started = float(now())
    attempts = 0
    snapshots: List[Any] = []
    while True:
        snapshot = probe()
        attempts += 1
        snapshots.append(snapshot)
        elapsed = max(float(now()) - started, 0.0)
        waited = max(elapsed, (attempts - 1) * tick)
        reason = ""
        if stop is not None:
            reason = str(stop(snapshot) or "")
        if reason:
            return {
                "snapshot": snapshot, "attempts": attempts, "waited": waited,
                "reason": reason, "timed_out": False, "snapshots": snapshots,
            }
        if elapsed >= limit or attempts >= max_attempts:
            return {
                "snapshot": snapshot, "attempts": attempts, "waited": waited,
                "reason": "timed_out", "timed_out": True, "snapshots": snapshots,
            }
        if interval > 0:
            pause(interval)


def waited_text(loop: Mapping[str, Any], *, tail: str = "") -> str:
    """轮询结果的统一 detail 后缀：「（等了 N 秒{:tail}，只读探测 K 次）」。"""
    return "（等了 %s 秒%s，只读探测 %d 次）" % (
        format_seconds(loop.get("waited")),
        tail,
        int(loop.get("attempts") or 0),
    )


def merge_evidence_ids(*groups: Any) -> List[str]:
    """合并多轮探测的 job/证据 id（去重保序，人工复核时能一路翻回去）。"""
    merged: List[str] = []
    for group in groups:
        for item in group or []:
            text = str(item or "")
            if text and text not in merged:
                merged.append(text)
    return merged


def _snapshot_unavailable(snapshot: Any) -> bool:
    """快照是不是「台架/能力不可用」（等待无意义，直接 skipped）。"""
    if not isinstance(snapshot, Mapping):
        return False
    if str(snapshot.get("state") or "") in ("unavailable", "busy"):
        return True
    return str(snapshot.get("block_state") or "") == BLOCK_SKIPPED


# --------------------------------------------------------------------------- #
# 10H-F21 网络拓扑（入网权威判据）
# --------------------------------------------------------------------------- #


def required_topology_count(archive_total: Any, min_ratio: Any = DEFAULT_MIN_RATIO) -> Optional[int]:
    """节点总数量门槛 = ``ceil(档案数 × min_ratio)``；输入非法 → ``None``（不猜）。

    减 ``1e-9`` 是为了吃掉浮点误差：``100 × 0.98`` 在二进制浮点里是
    ``98.00000000000001``，直接 ``ceil`` 会得到 99（把 98% 抬成 99%）。
    """
    if isinstance(archive_total, bool) or not isinstance(archive_total, int) or archive_total < 0:
        return None
    ratio = coerce_float(min_ratio, -1.0)
    if ratio <= 0 or ratio > 1:
        return None
    return int(math.ceil(archive_total * ratio - 1e-9))


def topo_node_index_of(name: Any) -> Optional[int]:
    """``节点3地址`` / ``节点3网络拓扑`` → ``3``（其它名字 → ``None``）。

    ``节点总数量`` / ``节点起始序号`` / ``节点1TEI`` / ``节点1代理节点标识`` 都不命中，
    所以逐节点记录只按「地址 + 网络拓扑」两件聚合。
    """
    text = str(name or "")
    if not text.startswith(TOPO_RECORD_PREFIX):
        return None
    rest = text[len(TOPO_RECORD_PREFIX):]
    for suffix in (TOPO_ADDR_SUFFIX, TOPO_INFO_SUFFIX):
        if rest.endswith(suffix):
            return int_of(rest[: -len(suffix)])
    return None


def topo_nodes_of(parsed: Any) -> List[Dict[str, Any]]:
    """10H-F21 上行逐节点记录 → ``[{addr, topology, role, master}]``。

    ``role`` 取自解析器把 5B 网络拓扑信息渲染成的 ``层级=N 角色=末梢节点STA`` 文本；
    主节点识别用 ``角色=主节点CCO``（节点信息 D4~D7 = 0x4）。
    """
    slots: Dict[int, Dict[str, Any]] = {}
    order: List[int] = []
    for item in items_of(parsed):
        index = topo_node_index_of(item.get("name"))
        if index is None:
            continue
        if index not in slots:
            slots[index] = {"addr": "", "topology": "", "role": "", "master": False}
            order.append(index)
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        if name.endswith(TOPO_ADDR_SUFFIX):
            slots[index]["addr"] = value.strip()
            continue
        slots[index]["topology"] = value
        slots[index]["role"] = value.split("角色=")[-1].strip() if "角色=" in value else ""
        slots[index]["master"] = TOPO_ROLE_MASTER in value
    return [dict(slots[index]) for index in order]


def _first_int_item(parsed: Any, names: Sequence[str]) -> Optional[int]:
    for name in names:
        value = int_of(item_value(parsed, name))
        if value is not None:
            return value
    return None


def topology_snapshot(
    *,
    cap: Any = None,
    ctx: Any = None,
    start: Any = DEFAULT_TOPO_START,
    count: Any = DEFAULT_TOPO_COUNT,
    expect_timeout: Any = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """发 10H-F21 查网络拓扑（**只读**），返回归一化快照。

    字段布局照 KB ``03_QGDW10376.2_全帧类型.md:1154-1180``：下行「节点起始序号 BIN 2 +
    节点数量 BIN 1」；上行「节点总数量 BIN 2 + 节点起始序号 BIN 2 + 本次应答节点数量
    BIN 1 + n×(节点地址 BIN 6 + 网络拓扑信息 BIN 5)」。

    返回 ``{state, block_state, total, start_echo, answered, nodes, masters, detail,
    job_ids, step}``；``state == "answered"`` 才表示拿到了可判定的应答。
    """
    blank: Dict[str, Any] = {
        "state": "unknown",
        "block_state": BLOCK_UNKNOWN,
        "total": None,
        "start_echo": None,
        "answered": None,
        "nodes": [],
        "masters": 0,
        "detail": "",
        "job_ids": [],
        "step": None,
    }
    capability, injected = (cap, True) if cap is not None else resolve_simcon(ctx)
    gate = capability_gate(capability, injected)
    if gate is not None:
        return dict(blank, state="unavailable", block_state=gate.state, detail=gate.detail)

    query = {"start": int(start), "count": int(count)}
    steps = [
        send_step(
            "查询网络拓扑信息（10H-F21 start=%d count=%d）" % (query["start"], query["count"]),
            AFN_ROUTE_QUERY,
            FN_TOPO_QUERY,
            query,
            expect_afn=AFN_ROUTE_QUERY,
            expect_fn=FN_TOPO_QUERY,
            timeout=expect_timeout,
        )
    ]
    violations = readonly_violations(steps)
    if violations:
        return dict(
            blank, state="config", detail="只读闸门拒绝下发：%s" % "；".join(violations)
        )

    result = call_verify(capability, steps)
    job_ids = [str(result.get("job_id") or "")] if result.get("job_id") else []
    got_steps = steps_of(result)
    if not got_steps:
        state, why = verify_failure_state(result)
        return dict(
            blank, state=snapshot_kind(state, result), block_state=state,
            detail=why, job_ids=job_ids,
        )
    step = got_steps[-1]
    verdict = judge_step(
        step,
        want_afn=AFN_ROUTE_QUERY,
        want_fn=FN_TOPO_QUERY,
        label="10H-F21 网络拓扑查询",
        cap=capability,
        job=result,
    )
    if verdict["kind"] != "answered":
        return dict(
            blank,
            state=snapshot_kind(verdict["state"], None, verdict["kind"]),
            block_state=verdict["state"],
            detail=verdict["detail"],
            job_ids=job_ids,
            step=step,
        )

    parsed = verdict.get("parsed")
    total = int_of(item_value(parsed, ITEM_TOPO_TOTAL))
    start_echo = int_of(item_value(parsed, ITEM_TOPO_START))
    answered = _first_int_item(parsed, (ITEM_TOPO_ANSWERED, ITEM_TOPO_ANSWERED_ALIAS))
    nodes = topo_nodes_of(parsed)
    masters = sum(1 for node in nodes if node.get("master"))
    return {
        "state": "answered",
        "block_state": BLOCK_PASS,
        "total": total,
        "start_echo": start_echo,
        "answered": answered,
        "nodes": nodes,
        "masters": masters,
        "detail": "%s；节点总数量=%s、起始序号=%s、本次应答=%s 条、其中主节点 %d 个"
        % (
            verdict["detail"],
            total if total is not None else "缺失",
            start_echo if start_echo is not None else "缺失",
            answered if answered is not None else "缺失",
            masters,
        ),
        "job_ids": job_ids,
        "step": step,
    }


#: 翻页拉全的页数上限（§8.1-2：按总数翻页取全；上限防无限翻页）
DEFAULT_TOPO_MAX_PAGES = 8


def topology_snapshot_paged(
    *,
    cap: Any = None,
    ctx: Any = None,
    start: Any = DEFAULT_TOPO_START,
    count: Any = DEFAULT_TOPO_COUNT,
    expect_timeout: Any = DEFAULT_TIMEOUT,
    max_pages: Any = DEFAULT_TOPO_MAX_PAGES,
) -> Dict[str, Any]:
    """10H-F21 **翻页拉全**节点记录（修 R-1：主节点数只在首页统计会把「差一个」读成达标）。

    v2 拍板 #2（0044 §8.1）：一次查询条数不足时按「节点总数量」翻页，**主节点数取全
    才叫观测完整**；取不全必须如实标注（``complete=False``），绝不静默按本页数下结论。

    返回在 :func:`topology_snapshot` 字段之上补：

    - ``complete``：bool —— 已取的**唯一**节点记录数 ≥ 节点总数量（且总数已知）；
    - ``collected``：取到的唯一节点序号数；``pages``：实际翻页数；
    - ``masters``：**取全后**的主节点数（不再只是首页的主节点数）。
    """
    limit = int_of(max_pages)
    limit = DEFAULT_TOPO_MAX_PAGES if limit is None or limit < 1 else limit

    first = topology_snapshot(
        cap=cap, ctx=ctx, start=start, count=count, expect_timeout=expect_timeout
    )
    job_ids = list(first.get("job_ids") or [])
    if str(first.get("state") or "") != "answered":
        return dict(first, complete=False, collected=0, pages=1)

    total = first.get("total") if isinstance(first.get("total"), int) else None
    collected = _collected_index_count(first)

    snapshots = [first]
    next_start = int(start) + max(collected, int_of(first.get("answered") or 0) or 0)
    pages = 1
    complete = total is not None and collected >= max(total, 0)
    while not complete and pages < limit:
        page = topology_snapshot(
            cap=cap, start=next_start, count=count, expect_timeout=expect_timeout
        )
        job_ids.extend(page.get("job_ids") or [])
        snapshots.append(page)
        pages += 1
        if str(page.get("state") or "") != "answered":
            break
        answered = int_of(page.get("answered")) or 0
        collected += answered
        next_start += max(answered, 1)
        if total is not None and collected >= max(total, 0):
            complete = True
        if answered <= 0:
            break

    nodes: List[Dict[str, Any]] = []
    masters = 0
    for snap in snapshots:
        for node in snap.get("nodes") or []:
            if not isinstance(node, Mapping):
                continue
            nodes.append(node)
            if node.get("master"):
                masters += 1
    detail = first.get("detail") or ""
    if not complete:
        known = "总数=%s" % (total if total is not None else "未知")
        detail = "%s；观测不完整：%s，翻页 %d 页只取到 %d 条节点记录" % (
            detail or "10H-F21 查询", known, pages, collected,
        )
    else:
        detail = "%s；翻页 %d 页取全 %d 条节点记录（其中主节点 %d 个）" % (
            detail, pages, collected, masters,
        )
    return {
        **first,
        "state": "answered",
        "total": total,
        "masters": masters,
        "nodes": nodes,
        "complete": complete,
        "collected": collected,
        "pages": pages,
        "job_ids": job_ids,
        "detail": detail,
    }


def _collected_index_count(snapshot: Mapping[str, Any]) -> int:
    """首页已取的节点记录数（按本次应答数优先，逐节点记录兜底）。"""
    answered = int_of(snapshot.get("answered"))
    if answered is not None and answered >= 0:
        return answered
    return len([n for n in (snapshot.get("nodes") or []) if isinstance(n, Mapping)])


def judge_topology(
    probe: Any,
    *,
    min_ratio: Any = DEFAULT_MIN_RATIO,
    master_included: Any = False,
) -> Tuple[str, str, Dict[str, Any]]:
    """拓扑是否达到「档案数 × min_ratio」门槛（三态；块唯一判定入口）。

    口径与出处：

    - 门槛 = ``ceil(档案数 × min_ratio)``，默认 0.98 —— KB ``业务层/37_双模测试用例_业务.md:42-48``
      「节点总数量 = 档案个数的 98%–100% …… 计时器超时 → 不通过」；
    - **档案数**取 ``10H-F1`` 的「从节点总数量」（``common.ARCHIVE_COUNT_NOTE``：
      元数据原文「当前路由档案中的从节点总数」）。为什么不用 10H-F4 的那份：10H-F4 是
      16B 路由运行状态结构体，档案数只是它的一个字段（且有长度门限历史缺陷），
      F1 的**第一个字段就是**档案数、无旁支，且块/事实已共用同一份
      ``common.archive_count_snapshot``（FT-2 单源），本块不另造取数路径；
    - **节点总数量含主节点**（KB 用例 87：无档案时 节点总数量=1「只包含主节点」），
      因此有两种口径，**默认严格**：

      * ``master_included=False``（**默认**）=「仅从节点」：分子 = ``节点总数量 − 主节点数``。
        我们要判的是「**STA 是否都入网**」，主节点本来就一定在，把它算进分子会让判据虚高
        （12 档案时 11 个从节点在网 = 91.7% 也会 pass，属漏判）。KB 的「节点总数量 =
        档案个数的 98%~100%」是**验收线的意图**，不是让主节点混进分子；
      * ``master_included=True`` = 「照原文直接比」：分子 = ``节点总数量`` 原值，
        逐字对应 KB 业务层 37 的句子，保留作可选口径（用例可显式传）。

      两种口径的读数都写进 detail/actual，人工复核不必再算。
    - 档案数 0 → ``unknown``：没有可入网目标不是产品失败。

    返回 ``(FACT_YES|FACT_NO|FACT_UNKNOWN, detail, actual)``。
    """
    probe = probe if isinstance(probe, Mapping) else {}
    archive = probe.get("archive") if isinstance(probe.get("archive"), Mapping) else {}
    topology = probe.get("topology") if isinstance(probe.get("topology"), Mapping) else {}
    archive_total = archive.get("total") if isinstance(archive.get("total"), int) else None
    total = topology.get("total") if isinstance(topology.get("total"), int) else None
    masters = topology.get("masters") if isinstance(topology.get("masters"), int) else 0
    required = required_topology_count(archive_total, min_ratio)
    include_master = flag_of(master_included, False)
    actual = {
        "节点总数量": total,
        "档案数": archive_total,
        "门槛": required,
        "节点起始序号": topology.get("start_echo"),
        "本次应答节点数": topology.get("answered"),
        "主节点数": masters,
        "口径": "含主节点" if include_master else "仅从节点",
    }

    if archive_total == 0:
        return FACT_UNKNOWN, (
            "档案数=0（10H-F1「%s」=0）：没有可入网目标，不判失败"
            "（这不是产品失败），本块不给出入网结论" % ITEM_TOTAL
        ), actual
    if archive_total is None:
        return FACT_UNKNOWN, (
            "取不到档案数（10H-F1「%s」），算不出 %.0f%% 门槛：%s"
            % (ITEM_TOTAL, DEFAULT_MIN_RATIO * 100, archive.get("detail") or "查询未得到可判定应答")
        ), actual
    if required is None:
        return FACT_UNKNOWN, "min_ratio=%r 非法（需 0 < ratio ≤ 1），算不出门槛" % (min_ratio,), actual
    if str(topology.get("state") or "") != "answered":
        return FACT_UNKNOWN, "10H-F21 网络拓扑查询未得到可判定应答（%s）：%s" % (
            topology.get("state") or "unknown",
            topology.get("detail") or "",
        ), actual
    if total is None:
        return FACT_UNKNOWN, (
            "10H-F21 应答里没有可解析的「%s」，无法判定（解析口径见 KB 全帧类型 F21 上行）"
            % ITEM_TOPO_TOTAL
        ), actual

    slaves = max(total - masters, 0)
    effective = total if include_master else slaves
    ratio_text = "%g" % coerce_float(min_ratio, DEFAULT_MIN_RATIO)
    if include_master:
        head = "节点总数量"
        both = "含主节点口径（主节点 %d 个；按仅从节点则为 %d）" % (masters, slaves)
    else:
        head = "入网从节点"
        both = "仅从节点口径 = 节点总数量 %d − 主节点 %d" % (total, masters)
    actual["在网从节点数"] = slaves
    verdict = FACT_YES if effective >= required else FACT_NO
    detail = "%s=%d %s 门槛 %d（档案数=%d × %s 向上取整；%s）" % (
        head, effective, "≥" if verdict == FACT_YES else "<", required,
        archive_total, ratio_text, both,
    )
    return verdict, detail, actual


# --------------------------------------------------------------------------- #
# 块执行体
# --------------------------------------------------------------------------- #


def block_route_restart(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.route_restart``：12H-F1 启动路由（重新组网），判据 = 00H-F1 确认。"""
    target, error = require_target(params, AFN_ROUTE_CONTROL, FN_ROUTE_RESTART, BLOCK_ROUTE_RESTART)
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    result = call_verify(
        cap,
        [
            send_step(
                "启动路由（重新组网）",
                AFN_ROUTE_CONTROL,
                FN_ROUTE_RESTART,
                {},
                expect_afn=AFN_CONFIRM,
                expect_fn=FN_CONFIRM,
                timeout=action.get("confirm_timeout", DEFAULT_TIMEOUT),
            )
        ],
    )
    job_ids = [str(result.get("job_id") or "")] if result.get("job_id") else []
    got_steps = steps_of(result)
    if not got_steps:
        state, why = verify_failure_state(result)
        return BlockResult(
            state=state,
            detail="%s：路由启动帧未得到可判定结论（%s）" % (BLOCK_ROUTE_RESTART, why),
            evidence_ids=job_ids,
        )
    verdict = judge_confirm_step(
        got_steps[-1], label="12H-F1 启动路由确认", cap=cap, job=result
    )
    if verdict["kind"] != "answered":
        return BlockResult(
            state=verdict["state"],
            detail="%s：%s" % (BLOCK_ROUTE_RESTART, verdict["detail"]),
            evidence_ids=job_ids,
            expected="00H-F1 确认帧",
            actual=verdict["kind"],
        )
    return BlockResult(
        state=BLOCK_PASS,
        detail="%s：%s（组网是否真起来由 nwk.nodes_online（真在网）/ nwk.route_status 再判；"
        "档案数由 nwk.archive_count 判，不代表可达状态）"
        % (BLOCK_ROUTE_RESTART, verdict["detail"]),
        evidence_ids=job_ids,
    )


def block_active_registration(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.active_registration``：11H-F5 激活从节点主动注册，判据 = 00H-F1 确认。

    四参（time6 + duration + retry + slices）在**下发前**校验，缺哪个在 detail 点名。
    """
    target, error = require_target(
        params, AFN_NWK_SET, FN_ACTIVE_REGISTRATION, BLOCK_ACTIVE_REGISTRATION
    )
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    payload, error = registration_payload(action)
    if payload is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    result = call_verify(
        cap,
        [
            send_step(
                "激活从节点主动注册（time6=%s duration=%s retry=%s slices=%s）"
                % (
                    action.get("time6") or action.get("time"),
                    payload.get("duration"),
                    payload.get("retry"),
                    payload.get("slices"),
                ),
                AFN_NWK_SET,
                FN_ACTIVE_REGISTRATION,
                payload,
                expect_afn=AFN_CONFIRM,
                expect_fn=FN_CONFIRM,
                timeout=action.get("confirm_timeout", DEFAULT_TIMEOUT),
            )
        ],
    )
    job_ids = [str(result.get("job_id") or "")] if result.get("job_id") else []
    got_steps = steps_of(result)
    if not got_steps:
        state, why = verify_failure_state(result)
        return BlockResult(
            state=state,
            detail="%s：主动注册帧未得到可判定结论（%s）"
            % (BLOCK_ACTIVE_REGISTRATION, why),
            evidence_ids=job_ids,
        )
    verdict = judge_confirm_step(
        got_steps[-1], label="11H-F5 主动注册确认", cap=cap, job=result
    )
    if verdict["kind"] != "answered":
        return BlockResult(
            state=verdict["state"],
            detail="%s：%s" % (BLOCK_ACTIVE_REGISTRATION, verdict["detail"]),
            evidence_ids=job_ids,
            expected="00H-F1 确认帧",
            actual=verdict["kind"],
        )
    return BlockResult(
        state=BLOCK_PASS,
        detail="%s：%s（注册窗口 duration=%s retry=%s slices=%s，"
        "入网结果由 nwk.nodes_online（真在网）/ nwk.route_status 再判）"
        % (
            BLOCK_ACTIVE_REGISTRATION,
            verdict["detail"],
            payload.get("duration"),
            payload.get("retry"),
            payload.get("slices"),
        ),
        evidence_ids=job_ids,
    )


def block_archive_count(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.archive_count``：10H-F1 查**路由档案中的从节点数量**，判据 = 档案数 ≥ ``min``。

    **这不是可达节点数**：10H-F1 只回「从节点总数量」（元数据原文「当前路由档案中的
    从节点总数」）+「路由支持最大从节点数量」，没有任何"抄通/可达"信息。判真实可达
    请用 :func:`block_nodes_online`。``min`` 语义 = **期望档案数下限**。
    """
    target, error = require_target(
        params, AFN_ROUTE_QUERY, FN_ONNET_COUNT, BLOCK_ARCHIVE_COUNT
    )
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    if action.get("min") in (None, ""):
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s 缺少必填参数 min（期望的**档案数**下限，如 1；%s）"
            % (BLOCK_ARCHIVE_COUNT, ARCHIVE_COUNT_NOTE),
        )
    minimum = int_of(action.get("min"))
    if minimum is None or minimum < 0:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s 参数 min 必须 ≥0 的整数，收到 %r" % (BLOCK_ARCHIVE_COUNT, action.get("min")),
        )

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    snapshot = archive_count_snapshot(
        cap=cap, expect_timeout=action.get("expect_timeout", DEFAULT_TIMEOUT)
    )
    job_ids = list(snapshot.get("job_ids") or [])
    if snapshot.get("state") != "answered":
        return BlockResult(
            state=str(snapshot.get("block_state") or BLOCK_UNKNOWN),
            detail="%s：%s"
            % (BLOCK_ARCHIVE_COUNT, snapshot.get("detail") or "查询未得到可判定应答"),
            evidence_ids=job_ids,
        )
    state, detail = judge_archive_count_min(snapshot, minimum)
    if state == FACT_YES:
        return BlockResult(
            state=BLOCK_PASS,
            detail="%s：%s" % (BLOCK_ARCHIVE_COUNT, detail),
            evidence_ids=job_ids,
            expected="档案数 ≥ %d" % minimum,
            actual=snapshot.get("total"),
        )
    if state == FACT_NO:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s：%s（组网前应先建档；档案数达标不代表节点真实可达，"
            "可达性请看 %s）" % (BLOCK_ARCHIVE_COUNT, detail, BLOCK_NODES_ONLINE),
            evidence_ids=job_ids,
            expected="档案数 ≥ %d" % minimum,
            actual=snapshot.get("total"),
        )
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail="%s：%s" % (BLOCK_ARCHIVE_COUNT, detail),
        evidence_ids=job_ids,
    )


def block_onnet_count(params: Any, ctx: Any = None) -> BlockResult:
    """**兼容别名** ``nwk.onnet_count`` → 内部委托 :func:`block_archive_count`。

    旧 id 从未判过可达节点数（10H-F1 也给不出可达性），保留只为不打断已有用例/方案；
    detail 会标明这是兼容别名，判据仍是**档案数**。
    """
    result = block_archive_count(params, ctx)
    detail = result.detail.replace(BLOCK_ARCHIVE_COUNT, BLOCK_ONNET_COUNT, 1)
    return BlockResult(
        state=result.state,
        detail="%s（兼容别名 %s → %s：判据是档案数，真实可达节点数见 %s）"
        % (detail, BLOCK_ONNET_COUNT, BLOCK_ARCHIVE_COUNT, BLOCK_NODES_ONLINE),
        facts=dict(result.facts),
        evidence_ids=list(result.evidence_ids),
        expected=result.expected,
        actual=result.actual,
        assertion=result.assertion,
    )


def _topo_observation_result(
    observation: Observation,
    *,
    detail: str,
    evidence: Sequence[str] = (),
    facts: Mapping[str, FactValue] | None = None,
) -> BlockResult:
    """组网块的统一出口：只带观测，旧路径（步骤未声明期望）落 unknown。"""
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail=detail,
        observation=observation,
        evidence_ids=list(evidence),
        facts=dict(facts or {}),
    )


def block_topology(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.topology``：10H-F21 查网络拓扑，**只报观测不判定**（REQS-0044 §4.2）。

    观测字段：``节点总数量`` / ``主节点数`` / ``在网从节点数`` / ``档案数`` /
    ``门槛`` / ``口径``。「在网从节点数是否达到门槛」由步骤的 ``expect`` 声明：
    ``{"op":"complete"},{"op":"ge_field","field":"在网从节点数","value":"门槛"}``；
    块不再输出通过/失败。门槛 = ceil(档案数 × min_ratio)（默认 0.98，严格口径
    主节点不计入分子，``master_included`` 可选照 KB 原句口径），由块按观测算出、
    作为观测字段随观测返回——阈值不是块内的“判定”，步骤用它比对。

    **翻页拉全**（修 R-1，v2 拍板 #2）：按「节点总数量」翻页取全节点记录，
    主节点数取全才算观测完整（``complete=True``）；取不全 → 原因码 ``incomplete``，
    步骤只能落 inconclusive，绝不按本页数下结论。

    等待预算 ``wait_s`` 由 Procedure 步骤显式给（§8.1-3，声明默认 300 仅兜底）；
    等待期只发只读查询（10H-F1 档案数 + 10H-F21 拓扑），达标即停。
    """
    target, error = require_target(params, AFN_ROUTE_QUERY, FN_TOPO_QUERY, BLOCK_TOPOLOGY)
    if target is None:
        return _topo_observation_result(
            Observation(values={}, complete=False, reason_code=OBS_INVALID_PARAM,
                        detail=error),
            detail=error,
        )
    action = block_action_params(params)

    raw_start = action.get("start", DEFAULT_TOPO_START)
    start_index = DEFAULT_TOPO_START if raw_start in (None, "") else int_of(raw_start)
    if start_index is None or not 0 <= start_index <= 0xFFFF:
        detail = (
            "%s 参数 start 必须是 0~65535 的整数（节点起始序号，默认 1）：%r"
            % (BLOCK_TOPOLOGY, raw_start)
        )
        return _topo_observation_result(
            Observation(values={}, complete=False, reason_code=OBS_INVALID_PARAM,
                        detail=detail),
            detail=detail,
        )
    raw_count = action.get("count", DEFAULT_TOPO_COUNT)
    count = DEFAULT_TOPO_COUNT if raw_count in (None, "") else int_of(raw_count)
    if count is None or not 1 <= count <= 0xFF:
        detail = (
            "%s 参数 count 必须是 1~255 的整数（本次查询的节点条数，默认 %d）：%r"
            % (BLOCK_TOPOLOGY, DEFAULT_TOPO_COUNT, raw_count)
        )
        return _topo_observation_result(
            Observation(values={}, complete=False, reason_code=OBS_INVALID_PARAM,
                        detail=detail),
            detail=detail,
        )
    raw_ratio = action.get("min_ratio", DEFAULT_MIN_RATIO)
    ratio = coerce_float(raw_ratio, -1.0)
    if ratio <= 0 or ratio > 1:
        detail = (
            "%s 参数 min_ratio 必须落在 (0,1]（默认 %g）：%r"
            % (BLOCK_TOPOLOGY, DEFAULT_MIN_RATIO, raw_ratio)
        )
        return _topo_observation_result(
            Observation(values={}, complete=False, reason_code=OBS_INVALID_PARAM,
                        detail=detail),
            detail=detail,
        )
    master_included = flag_of(action.get("master_included"), False)
    wait_s = coerce_float(action.get("wait_s"), DEFAULT_WAIT_TOPOLOGY)
    interval = coerce_float(action.get("poll_interval_s"), DEFAULT_POLL_INTERVAL)
    expect_timeout = action.get("expect_timeout", DEFAULT_TIMEOUT)

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        # 台架不具备条件：能力未就绪 → bench_unreachable（步骤态 skipped → blocked）
        return _topo_observation_result(
            Observation(values={}, complete=False,
                        reason_code=OBS_BENCH_UNREACHABLE, detail=gate.detail),
            detail=gate.detail,
        )
    clock, sleeper = poll_hooks(action, cap)

    def probe() -> Dict[str, Any]:
        return {
            "archive": archive_count_snapshot(cap=cap, expect_timeout=expect_timeout),
            "topology": topology_snapshot_paged(
                cap=cap, start=start_index, count=count, expect_timeout=expect_timeout
            ),
        }

    def stop(snapshot: Any) -> Optional[str]:
        fact, _why, _actual = judge_topology(
            snapshot, min_ratio=ratio, master_included=master_included
        )
        if fact == FACT_YES:
            return "satisfied"
        archive = snapshot.get("archive") if isinstance(snapshot, Mapping) else {}
        topology = snapshot.get("topology") if isinstance(snapshot, Mapping) else {}
        if isinstance((archive or {}).get("total"), int) and archive.get("total") == 0:
            return "no_target"          # 没有可入网目标：等下去也不会变
        if _snapshot_unavailable(archive) and _snapshot_unavailable(topology):
            return "bench"              # 台架/能力不可用：等待无意义
        if str((topology or {}).get("state") or "") == "config":
            return "config"             # 构帧失败是参数错，不会自愈
        return None

    loop = poll_snapshot(
        probe, wait_s=wait_s, interval_s=interval, stop=stop, clock=clock, sleep=sleeper
    )
    latest = loop["snapshot"] if isinstance(loop["snapshot"], Mapping) else {}
    archive = latest.get("archive") or {}
    topology = latest.get("topology") or {}
    job_ids = merge_evidence_ids(
        *[snap.get("archive", {}).get("job_ids") for snap in loop["snapshots"]],
        *[snap.get("topology", {}).get("job_ids") for snap in loop["snapshots"]],
    )
    fact, judge_detail, judge_actual = judge_topology(
        latest, min_ratio=ratio, master_included=master_included
    )

    # ---- 环境类：台架/能力不可用 ---------------------------------------- #
    if loop["reason"] == "bench":
        detail = "%s：台架/能力不就绪，查询未执行：%s" % (
            BLOCK_TOPOLOGY, archive.get("detail") or topology.get("detail") or "",
        )
        code = OBS_SERIAL_BUSY if "忙" in detail or "409" in detail or "占用" in detail \
            else OBS_BENCH_UNREACHABLE
        return _topo_observation_result(
            Observation(values={}, complete=False, reason_code=code, detail=detail),
            detail=detail, evidence=job_ids,
        )

    # ---- 观测值（走到哪层报到哪层） -------------------------------------- #
    values: Dict[str, Any] = {
        "节点总数量": topology.get("total") if isinstance(topology.get("total"), int) else None,
        "主节点数": topology.get("masters") if isinstance(topology.get("masters"), int) else None,
        "档案数": archive.get("total") if isinstance(archive.get("total"), int) else None,
        "门槛": judge_actual.get("门槛") if isinstance(judge_actual, Mapping) else None,
        "口径": "含主节点" if master_included else "仅从节点",
    }
    total = values["节点总数量"]
    masters = values["主节点数"]
    if isinstance(total, int) and isinstance(masters, int):
        values["在网从节点数"] = max(total - masters, 0) if not master_included else total
    else:
        values["在网从节点数"] = None
    values["探测次数"] = loop["attempts"]
    values["等待秒"] = round(float(loop["waited"]), 3)

    wait_suffix = waited_text(loop) if loop["attempts"] > 1 else ""

    # 原因分流：档案数/拓扑查询的「为什么没拿到」
    topo_state = str(topology.get("state") or "")
    if topo_state == "config":
        detail = "%s：%s" % (BLOCK_TOPOLOGY, topology.get("detail") or "只读闸门拒绝下发")
        return _topo_observation_result(
            Observation(values=values, complete=False,
                        reason_code=OBS_INVALID_PARAM, detail=detail),
            detail=detail, evidence=job_ids,
        )
    if topo_state in ("busy", "unavailable") or str(archive.get("state") or "") in ("busy", "unavailable"):
        detail = "%s：%s" % (
            BLOCK_TOPOLOGY,
            topology.get("detail") or archive.get("detail") or "查询未执行",
        )
        code = OBS_SERIAL_BUSY if "忙" in detail or "409" in detail or "占用" in detail \
            else OBS_BENCH_UNREACHABLE
        return _topo_observation_result(
            Observation(values=values, complete=False, reason_code=code, detail=detail),
            detail=detail, evidence=job_ids,
        )
    if topo_state in ("no_reply", "timeout") and not topology.get("total"):
        detail = "%s：%s%s" % (BLOCK_TOPOLOGY, topology.get("detail") or "拓扑查询超时", wait_suffix)
        return _topo_observation_result(
            Observation(values=values, complete=False, reason_code=OBS_TIMEOUT, detail=detail),
            detail=detail, evidence=job_ids,
        )
    complete = bool(topology.get("complete")) and isinstance(values["节点总数量"], int) \
        and values["节点总数量"] > 0
    if not complete:
        detail = "%s：%s%s" % (BLOCK_TOPOLOGY, topology.get("detail") or "观测不完整", wait_suffix)
        return _topo_observation_result(
            Observation(values=values, complete=False,
                        reason_code=OBS_INCOMPLETE, detail=detail),
            detail=detail, evidence=job_ids,
        )

    # 观测完整：在网从节点数 / 门槛 都在观测里，达标与否由步骤期望判（ge_field）
    detail = "%s：%s%s" % (BLOCK_TOPOLOGY, judge_detail, wait_suffix)
    facts: Dict[str, FactValue] = {}
    if fact == FACT_YES:
        facts["nwk.joined"] = FactValue(
            state="yes",
            detail="组网观测达标（%s）" % judge_detail,
            evidence=list(job_ids),
        )
    return _topo_observation_result(
        Observation(values=values, complete=True, reason_code="", detail=detail),
        detail=detail, evidence=job_ids, facts=facts,
    )


def block_nodes_online(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.nodes_online``：判**真实在网/可达**节点数（两路只读证据交叉）。

    证据 A = ``10H-F4``「已抄从节点数量」（> 0 才说明确有节点被成功抄到）；
    证据 B = ``10H-F2`` 逐节点「信号品质>0」的节点数。

    - 两路任一 > 0 且达到 ``min`` → pass；都明确为 0 → **fail**（档案有节点但一个都不通）；
    - 任一路取不到 → 只用可得那路；两路都取不到 → unknown；
    - **绝不**用「从节点总数量」（档案数）推断在网。

    参数：``min``（期望在网节点数下限，默认 1）、``count``（10H-F2 查询条数，默认 32）、
    ``wait_s``（等待上限，默认 **180**）、``poll_interval_s``（探测间隔，默认 2）、
    ``clock`` / ``sleep``（注入用 callable）。

    等待轮询（REQS-0042 S2-A）：每轮只发 **10H-F4 + 10H-F2** 两条只读查询，轮询到
    「已抄数 ≥ min 或 信号品质>0 的节点数 ≥ min」即 pass；等满 ``wait_s`` 仍不满足才按
    上面的原三态口径给 fail/unknown（detail 写「等了 N 秒」）。

    ``wait_s`` 默认值的依据（**别拍脑袋调小**）：台架实测一轮入网约 **240s**，学习态转
    抄表还要在这之后。本块只判「至少 1 个节点抄通」，首个节点入网通常 10~60s，故给
    「实测的一半 + 余量 = **180s**」；原先的 90s 在冷启动/大衰减下会把「还没抄通」判成
    明确不足 → **假 FAIL**。达标即停，所以成功路径最多付 1 个 ``poll_interval_s``。

    块声明的 ``afn/fn`` 取 **10H-F4**（主证据 A 就在这条应答里），本块另发**一次**
    10H-F2（带 ``start=0 / count=``）取逐节点信号品质；两条都是只读查询。
    """
    target, error = require_target(
        params, AFN_ROUTE_QUERY, FN_ROUTE_STATUS, BLOCK_NODES_ONLINE
    )
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    raw_min = action.get("min")
    minimum = int_of(DEFAULT_MIN_ONLINE if raw_min in (None, "") else raw_min)
    if minimum is None or minimum < 0:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s 参数 min 必须 ≥0 的整数（期望在网节点数下限），收到 %r"
            % (BLOCK_NODES_ONLINE, raw_min),
        )

    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    wait_s = coerce_float(action.get("wait_s"), DEFAULT_WAIT_NODES_ONLINE)
    interval = coerce_float(action.get("poll_interval_s"), DEFAULT_POLL_INTERVAL)
    count = action.get("count", DEFAULT_NODES_ONLINE_COUNT)
    max_pages = action.get("max_pages")
    expect_timeout = action.get("expect_timeout", DEFAULT_TIMEOUT)
    clock, sleeper = poll_hooks(action, cap)

    def probe() -> Dict[str, Any]:
        return nodes_online_snapshot(
            cap=cap, count=count, max_pages=max_pages, expect_timeout=expect_timeout
        )

    def stop(candidate: Any) -> Optional[str]:
        if judge_nodes_online(candidate, minimum)[0] == FACT_YES:
            return "satisfied"
        if _snapshot_unavailable(candidate):
            return "bench"
        return None

    loop = poll_snapshot(
        probe, wait_s=wait_s, interval_s=interval, stop=stop, clock=clock, sleep=sleeper
    )
    snapshot = loop["snapshot"]
    job_ids = merge_evidence_ids(*[snap.get("job_ids") for snap in loop["snapshots"]])
    channel_a = snapshot.get("channel_a") or {}
    channel_b = snapshot.get("channel_b") or {}
    readings = {
        EVIDENCE_ARCHIVED: channel_a.get("value"),
        EVIDENCE_QUALITY: channel_b.get("value"),
        "档案数（不参与判定）": snapshot.get("archive_total"),
    }
    if loop["reason"] == "bench":
        return BlockResult(
            state=BLOCK_SKIPPED,
            detail="%s：台架/能力不就绪，块未执行（不是 FAIL）：%s"
            % (BLOCK_NODES_ONLINE, snapshot.get("detail") or ""),
            evidence_ids=job_ids,
        )
    state, detail = judge_nodes_online(snapshot, minimum)
    # 判定结论后面追加两路证据明细（扫描条数/各自读数），人工复核时不用再翻 job
    evidence_text = snapshot.get("detail") or ""
    if evidence_text:
        detail = "%s（两路证据明细：%s）" % (detail, evidence_text)
    detail = "%s%s" % (detail, waited_text(loop))
    if state == FACT_YES:
        return BlockResult(
            state=BLOCK_PASS,
            detail="%s：%s" % (BLOCK_NODES_ONLINE, detail),
            facts={
                FACT_NODES_ONLINE_MIN: {
                    "state": FACT_YES,
                    "detail": detail,
                    "evidence": list(job_ids),
                }
            },
            evidence_ids=job_ids,
            expected="在网节点数 ≥ %d" % minimum,
            actual=readings,
        )
    if state == FACT_NO:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s：%s（这是防假 PASS 的 FAIL：两路独立证据都显示没有节点被抄通）"
            % (BLOCK_NODES_ONLINE, detail),
            evidence_ids=job_ids,
            expected="在网节点数 ≥ %d" % minimum,
            actual=readings,
        )
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail="%s：%s" % (BLOCK_NODES_ONLINE, detail),
        evidence_ids=job_ids,
        expected="在网节点数 ≥ %d" % minimum,
        actual=readings,
    )


def block_route_status(params: Any, ctx: Any = None) -> BlockResult:
    """``nwk.route_status``：10H-F4 查路由运行状态（学习态按协议语义分层，不直接判 FAIL）。

    分层（``common.classify_route_state``，含协议位出处）：

    - ``ROUTE_RUNNING``（工作状态=抄表）→ **pass**；
    - ``ROUTE_LEARNING_INCONCLUSIVE``（运行状态字 D1 路由完成标志=1 **且** 档案数>0，
      但工作状态=学习）→ **inconclusive**（块态 unknown）：路由基础已建立，
      尚未进入抄表工作 —— 刚下发 12H-F1/11H-F5 后进入学习态是协议正常行为；
    - ``ROUTE_LEARNING_INCOMPLETE``（路由完成标志=0 + 学习）→ 块态 unknown（单帧不足以
      证明"持续未完成"），显式 ``params.learning_fail=true`` 才判 fail；
    - ``ROUTE_UNKNOWN``（字段缺失/取值非法）→ unknown。

    显式 ``params.expect``（字符串/数组）仍按原口径匹配「工作状态/当前状态」。

    等待轮询（REQS-0042 S2-A）：``wait_s`` 默认 **300**、``poll_interval_s`` 默认 **2**，
    每轮只发 **10H-F4** 一条只读查询；轮询到「工作状态=抄表」即 pass。学习态（协议正常
    注册过程）持续到超时 → ``unknown`` + detail「等了 N 秒仍处学习态」，分层口径不变。

    ``wait_s`` 默认值的依据（**别拍脑袋调小**）：台架实测一轮入网约 **240s**，而「学习态
    → 抄表」的切换发生在这之后，所以预算必须 ≥ 实测入网时长 + 余量 = **300s**。原先的
    60s 会让这块在真台上**几乎恒判 unknown(inconclusive)**（永远停在入网中段），等于这条
    判据没有信息量；它不误判失败，但也从不说通过。达标即停，成功路径最多付 1 个间隔。
    """
    target, error = require_target(params, AFN_ROUTE_QUERY, FN_ROUTE_STATUS, BLOCK_ROUTE_STATUS)
    if target is None:
        return BlockResult(state=BLOCK_FAIL, detail=error)
    action = block_action_params(params)
    expect = action.get("expect", action.get("expect_state"))
    learning_fail = flag_of(action.get("learning_fail"))
    cap, injected = resolve_simcon(ctx)
    gate = capability_gate(cap, injected)
    if gate is not None:
        return gate
    wait_s = coerce_float(action.get("wait_s"), DEFAULT_WAIT_ROUTE_STATUS)
    interval = coerce_float(action.get("poll_interval_s"), DEFAULT_POLL_INTERVAL)
    expect_timeout = action.get("expect_timeout", DEFAULT_TIMEOUT)
    clock, sleeper = poll_hooks(action, cap)

    def probe() -> Dict[str, Any]:
        return route_snapshot(cap=cap, expect_timeout=expect_timeout)

    def stop(candidate: Any) -> Optional[str]:
        # 用与最终判定**同一份**口径（含显式 expect）判「已满足」，避免 expect 命中时
        # 还要白等满 wait_s；默认口径下只有「工作状态=抄表」才算满足。
        if judge_route_status(candidate, expect, learning_fail=learning_fail)[0] == FACT_YES:
            return "satisfied"
        if _snapshot_unavailable(candidate):
            return "bench"
        return None

    loop = poll_snapshot(
        probe, wait_s=wait_s, interval_s=interval, stop=stop, clock=clock, sleep=sleeper
    )
    snapshot = loop["snapshot"]
    job_ids = merge_evidence_ids(*[snap.get("job_ids") for snap in loop["snapshots"]])
    if loop["reason"] == "bench":
        return BlockResult(
            state=BLOCK_SKIPPED,
            detail="%s：台架/能力不就绪，块未执行（不是 FAIL）：%s"
            % (BLOCK_ROUTE_STATUS, snapshot.get("detail") or ""),
            evidence_ids=job_ids,
        )
    if snapshot.get("state") != "answered":
        return BlockResult(
            state=str(snapshot.get("block_state") or BLOCK_UNKNOWN),
            detail="%s：%s%s"
            % (
                BLOCK_ROUTE_STATUS,
                snapshot.get("detail") or "查询未得到可判定应答",
                waited_text(loop),
            ),
            evidence_ids=job_ids,
        )
    label, reason = classify_route_state(snapshot)
    state, detail = judge_route_status(snapshot, expect, learning_fail=learning_fail)
    # 学习态一直是"过程"：等满 wait_s 仍处学习态 → detail 明确写「等了 N 秒仍处学习态」
    learning = label in (ROUTE_LEARNING_INCONCLUSIVE, ROUTE_LEARNING_INCOMPLETE)
    suffix = waited_text(loop, tail="仍处学习态" if learning else "")
    actual = {
        "分类": label,
        "工作状态": snapshot.get("work_state"),
        "当前状态": snapshot.get("current_state"),
        "路由完成标志": snapshot.get("route_done"),
        "档案数": snapshot.get("archive_total"),
        "已抄": snapshot.get("answered_total"),
        "探测次数": loop["attempts"],
        "等待秒": round(float(loop["waited"]), 3),
    }
    if state == FACT_YES:
        return BlockResult(
            state=BLOCK_PASS,
            detail="%s：%s%s" % (BLOCK_ROUTE_STATUS, detail, suffix),
            evidence_ids=job_ids,
            actual=actual,
        )
    if state == FACT_NO:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="%s：%s（分类=%s）%s" % (BLOCK_ROUTE_STATUS, detail, label, suffix),
            evidence_ids=job_ids,
            expected=expect,
            actual=actual,
        )
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail="%s：%s（分类=%s）%s" % (BLOCK_ROUTE_STATUS, detail, label, suffix),
        evidence_ids=job_ids,
        actual=actual,
    )


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #

_BLOCKS = {
    BLOCK_ROUTE_RESTART: block_route_restart,
    BLOCK_ACTIVE_REGISTRATION: block_active_registration,
    BLOCK_ARCHIVE_COUNT: block_archive_count,
    BLOCK_ONNET_COUNT: block_onnet_count,        # 兼容别名（内部委托 archive_count）
    BLOCK_TOPOLOGY: block_topology,
    BLOCK_NODES_ONLINE: block_nodes_online,
    BLOCK_ROUTE_STATUS: block_route_status,
}


def register_blocks(*, replace: bool = False) -> None:
    """注册本模块的块执行体（幂等；同 id 被别的执行体占用则抛 ``Duplicate``）。"""
    for block_id, fn in _BLOCKS.items():
        existing = BLOCK_REGISTRY.get(block_id)
        if existing is fn:
            continue
        if existing is not None:
            if not replace:
                raise Duplicate(
                    "块 %r 已被其它执行体注册（如需接管请 replace=True）" % block_id
                )
            unregister_block(block_id)
        register_block(block_id, fn)


register_blocks()


def register() -> None:
    """兼容 ``case_runner.blocks.loader.register_domain_modules`` 的约定入口。"""
    register_blocks()


__all__ = [
    "BLOCK_ROUTE_RESTART",
    "BLOCK_ACTIVE_REGISTRATION",
    "BLOCK_ARCHIVE_COUNT",
    "BLOCK_ONNET_COUNT",      # 兼容别名
    "BLOCK_TOPOLOGY",
    "BLOCK_NODES_ONLINE",
    "BLOCK_ROUTE_STATUS",
    "FACT_ONNET_COUNT_MIN",   # 兼容别名事实（判档案数）
    "FACT_NODES_ONLINE_MIN",
    "FACT_ROUTE_RUNNING",
    "AFN_ROUTE_CONTROL",
    "FN_ROUTE_RESTART",
    "AFN_NWK_SET",
    "FN_ACTIVE_REGISTRATION",
    "FN_TOPO_QUERY",
    "WORK_STATES",
    "CURRENT_STATES",
    "ROUTE_LABELS",
    "DEFAULT_MIN_ARCHIVE",
    "DEFAULT_MIN_ONNET",
    "DEFAULT_TOPO_START",
    "DEFAULT_TOPO_COUNT",
    "DEFAULT_MIN_RATIO",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_WAIT_TOPOLOGY",
    "DEFAULT_WAIT_NODES_ONLINE",
    "DEFAULT_WAIT_ROUTE_STATUS",
    "READONLY_AFN_FNS",
    "ITEM_TOPO_TOTAL",
    "ITEM_TOPO_START",
    "ITEM_TOPO_ANSWERED",
    "TOPO_START_NOTE",
    "TOPO_ROLE_MASTER",
    "time6_to_digits",
    "registration_time_params",
    "registration_payload",
    "block_route_restart",
    "block_active_registration",
    "block_archive_count",
    "block_onnet_count",
    "block_topology",
    "block_nodes_online",
    "block_route_status",
    "register_blocks",
    "register",
    # 再导出（事实模块与旧调用方从 nwk 取这些名字仍然可用；实现只有 common 一份）
    "archive_count_snapshot",
    "judge_archive_count_min",
    "topology_snapshot",
    "judge_topology",
    "required_topology_count",
    "topo_nodes_of",
    "topo_node_index_of",
    "poll_snapshot",
    "poll_hooks",
    "readonly_violations",
    "waited_text",
    "merge_evidence_ids",
    "format_seconds",
    "onnet_snapshot",          # 兼容别名
    "judge_onnet_min",         # 兼容别名
    "nodes_online_snapshot",
    "judge_nodes_online",
    "route_snapshot",
    "route_status_fields",
    "classify_route_state",
    "judge_route_status",
    "judge_route_running",
    "judge_step",
    "judge_confirm_step",
    "send_step",
    "call_verify",
    "verify_failure_state",
    "steps_of",
    "snapshot_kind",
    "block_action_params",
    "require_target",
    "resolve_simcon",
    "capability_gate",
    "int_of",
    "item_value",
    "items_of",
    "AFN_CONFIRM",
    "FN_CONFIRM",
    "AFN_ROUTE_QUERY",
    "FN_ONNET_COUNT",
    "FN_ROUTE_STATUS",
    "ITEM_TOTAL",
    "ITEM_WORK_STATE",
    "ITEM_CURRENT_STATE",
    "ITEM_SWITCH",
    "ITEM_RUN_WORD",
    "DEFAULT_TIMEOUT",
]
