"""块层公共件（REQS-0041 §10.5；BL / FT **唯一一份**判定逻辑的家）。

为什么有这个文件
----------------
``archive.py``（1175 行）曾把「通用 1376.2 单步下发/取帧/判定」小工具和档案专用判定
写在同一处，于是 ``nwk.py`` 与 ``facts_nwk.py`` 都得 ``from case_runner.blocks.archive
import ...`` —— 档案块和组网块被绑在一起，改任一板块都可能牵连另一类块。本文件把这些
**与领域无关**的件抽出来：

- 域模块（``archive`` / ``nwk`` / ``fix`` / ``reset`` / ``meter`` / ``facts_*``）之间
  **零 import**，公共件一律从这里取（``test_archive_nwk.py`` 有 AST 守卫）；
- 判定函数**仍只有一份**（这是原有正确设计）：事实探测体复用块的判定函数，不复制。

段落
----
1. 记法归一（AFN/FN/地址/解析结果取值）
2. ``ctx`` / 块参数取值
3. 1376.2 能力解析与就绪门
4. ``verify`` 调用与整体失败归一（409/不可达/轮询超时/构帧失败）
5. 单步构造与判定（``send_step`` / ``judge_step``）
6. 10H-F2 档案表快照与判定（``archive_snapshot`` / ``judge_contains``）
7. 组网三件事的快照与判定：

   - ``archive_count_snapshot`` / ``judge_archive_count_min`` —— 10H-F1 的**档案数**
     （旧名 ``onnet_snapshot`` / ``judge_onnet_min`` 保留为兼容别名）；
   - ``route_snapshot`` / ``classify_route_state`` / ``judge_route_status`` /
     ``judge_route_running`` —— 10H-F4 路由运行状态（含学习态分层）；
   - ``nodes_online_snapshot`` / ``judge_nodes_online`` —— **真正的在网/可达**判定
     （10H-F4 已抄数 + 10H-F2 逐节点信号品质两路交叉，绝不用档案数推断）。
8. 台架能力解析与就绪判定（simeter / simcon / module）
9. 自愈（占用就抢占一次：simcon 409 / simeter 端口冲突）

自愈的边界（SH-3 代码审查项，REQS-0042 §3.4 / 变更 2 D2-7）
--------------------------------------------------------
用户口径原文：

    「跑测试用例时，是主动调用跑的，这时候如果有占用，那应当以自动化测试为主，
      不看归属，直接抢占为自己所用即可。」

因此 :func:`with_self_heal` **只做接口级释放与重试**：遇到占用类错误（409）就调能力层
的 ``close`` / ``close_port`` 释放，然后重试**一次**；**不判归属、不做时间猜**，抢占后
也不负责还原对方的会话。**禁止进程级操作** —— 本目录下不得起子进程、不得杀进程、
不得直调系统 shell，也不重启服务/断电；那些属人工运维，不是自动化测试该做的事
（三项禁令由 ``test_self_heal.py`` 的静态守卫用例把守，必须零命中）。

块态语义**不变**：自愈成功 → 照常按判据 pass/fail；重试仍被占用 → 仍是既有的
``skipped``（detail 写明「已抢占一次仍失败」+ 原始 409 文本），S3 阶段才由上层映射成
``blocked``。

状态口径（REQS RU-3「绝不假 PASS」，领导裁决口径）
--------------------------------------------------
==========================  ==========================================================
帧已发出但超时无应答        ``fail``（有 ``sent_hex`` 证据，属测试失败）
帧未发出 / 构帧失败(422)    ``unknown``（配置错误，不是设备结论）
409 闸门忙 / 台架不可达     ``skipped``
frames 404（无会话）        ``skipped``（看不到应答帧就不能判 pass）
job 轮询超时                ``unknown``（不表示设备失败）
应答无法解析 / 字段缺失     ``unknown``
==========================  ==========================================================

分层：本文件只依赖 ``case_runner.capabilities``（能力层）+ ``case_runner.core``
（模型/注册表/规划器），**不 import** 任何 ``case_runner.blocks.*`` 兄弟模块。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from case_runner.capabilities.module import ModuleCapability
from case_runner.capabilities.simeter import SimeterCapability
from case_runner.capabilities.simcon import SimconCapability
from case_runner.core.executor import (
    SELF_HEALED_KEY,
    BlockResult,
    RunContext,
    record_self_heal,
    self_heal_entry,
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
from case_runner.core.planner import FactValue

#: 1376.2 帧口径（全会话级常量，所有块共用）
AFN_CONFIRM = 0x00           # 00H：确认/否认
FN_CONFIRM = 1               # 00H-F1 确认
FN_DENY = 2                  # 00H-F2 否认

AFN_ROUTE_QUERY = 0x10       # 10H：路由查询（档案表 / 在网数 / 路由状态共用 AFN）
FN_ARCHIVE_QUERY = 2         # 10H-F2 查询从节点档案
FN_ONNET_COUNT = 1           # 10H-F1 查询在网从节点数量
FN_ROUTE_STATUS = 4          # 10H-F4 查询路由运行状态

#: 12 位 BCD 表地址长度
ADDR_LEN = 12
#: 单次查询条数上限（协议字段 1B）
COUNT_MAX = 255

DEFAULT_TIMEOUT = 5.0
DEFAULT_QUERY_COUNT = 16
DEFAULT_MAX_PAGES = 4

#: 块声明里的**非** action 字段（其余顶层字段都当作 action 参数的扁平覆盖）
DECL_KEYS = frozenset({"afn", "fn", "params", "recipe", "semantic", "assert", "cleanup"})

#: ``ctx.extras`` 里能力句柄的键
SIMCON_KEYS = ("simcon", "simcon_capability", "simcon_cap", "cap_simcon")
EXTRAS_SIMETER = "simeter"
EXTRAS_SIMCON = "simcon"
EXTRAS_MODULE = "module"

#: 默认逻辑口别名（不硬编码 COM 号 / 端口，见 REQS §10.10 事实 L）
DEFAULT_SIMETER_MAPPING = "listener"
DEFAULT_SIMCON_MAPPING = "simcon"
DEFAULT_MODULE_MAPPING = "sta-main"

#: verify 整体失败 → 块态映射
BUSY_ERRORS = frozenset({"http_409"})
DOWN_ERRORS = frozenset(
    {"no_base_url", "unreachable", "transport_error", "http_502", "http_503", "http_504"}
)
CONFIG_ERRORS = frozenset(
    {"empty_steps", "bad_response", "missing_job_id", "http_400", "http_422"}
)

#: 「工作台可达但该子应用没挂载」= 明确没有，不是「不知道」
ABSENT_ERRORS = frozenset({"http_404"})

# --------------------------------------------------------------------------- #
# 自愈口径（REQS-0042 §3.4 / D2-7 / SH-1..3）—— 常量放前面，供下面各函数的默认值用
# --------------------------------------------------------------------------- #

#: 自愈重试上限：**只重试一次**（SH-1「仅一次，不自愈成死循环」）
SELF_HEAL_MAX_RETRY = 1
#: 默认重试次数（等于上限；显式传更大值也会被夹到上限）
SELF_HEAL_RETRY = SELF_HEAL_MAX_RETRY

#: 「占用类」错误码（只有这类才允许自愈；422/参数错/超时/不可达一律不动手）
#: simcon 闸门忙与 simeter 端口冲突后端都给 409，语义相同（= 有人占着）
CONFLICT_ERRORS = frozenset({"http_409"})

#: 自愈留痕的 ``result`` 取值（D4-4 自动决策的分级）
SELF_HEAL_HEALED = "healed"            # 抢占后重试成功
SELF_HEAL_FAILED = "failed"            # 已抢占一次，重试仍被占用 → 交上层按台架不具备处理
SELF_HEAL_UNAVAILABLE = "unavailable"  # 遇到占用但没有可注入的 close 能力，未动手

#: simcon 侧 close 能力的方法名（能力层补上 ``close()`` 即自动生效，块层不用改）
SIMCON_CLOSER_NAMES = ("close",)
#: simeter 侧 close 能力的方法名（现成：``POST /api/simeter/close``）
SIMETER_CLOSER_NAMES = ("close_port",)

#: ``close`` 应答里能指认「抢占了谁」的字段（事后追溯靠它，D2-7 要求写明）
PREEMPT_KEYS = ("actor", "holder", "owner", "session", "session_id", "run_id",
                "job_id", "task", "port", "device")

# --------------------------------------------------------------------------- #
# 模拟电表表口（simeter serial）开口口径（REQS-0041 §10.13 台架联调修复）
# --------------------------------------------------------------------------- #

#: 默认表口**逻辑口别名**（mapping_id；不是 COM 号 —— 物理串口由工作台映射决定）
DEFAULT_SIMETER_PORT = "simeter"
#: ``fix.simeter_session`` 是否默认自动开口（没开就开，开完不关）
DEFAULT_ENSURE_OPEN = True

#: ``capability.status()`` 自己抛异常时的错误码（收敛成三态 ``unknown``）
SIMETER_PROBE_ERROR = "probe_error"
#: 上面那个错误码的可读前缀（保持历史 detail 文案）
SIMETER_PROBE_ERROR_TEXT = "simeter 探测异常"

#: ``open_port`` / ``status`` 失败里「台架明确缺失」的一类 → 块 ``skipped``
SIMETER_ABSENT_ERRORS = ABSENT_ERRORS | {"no_base_url", "unreachable"}

#: ``ensure_simeter_session`` 的结论（**不是**块态；块态由 ``fix.py`` 按本表映射）
SIMETER_SESSION_REUSED = "reused"        # 表口本来就开着（沿用已开口，一个 open 调用都不发）
SIMETER_SESSION_OPENED = "opened"        # 本块刚打开并复核通过
SIMETER_SESSION_NOT_OPEN = "not_open"    # 表口没开（ensure_open=false / 开口后复核仍 False）
SIMETER_SESSION_CONFLICT = "conflict"    # open 返回 409：端口被占用
SIMETER_SESSION_ABSENT = "absent"        # 子应用未挂载（404）/ 服务不可达
SIMETER_SESSION_UNKNOWN = "unknown"      # 其他 4xx/5xx、bad_response、传输异常 …

#: 「表口确实开着」的结论集合（只有这两个才继续走严格判定）
SIMETER_SESSION_OPEN_OK = frozenset({SIMETER_SESSION_REUSED, SIMETER_SESSION_OPENED})

#: 10H-F2 上行应答里的字段名（实测：``sim_concentrator/recipes.py`` 清档流程同口径）
ITEM_TOTAL = "从节点总数量"
ITEM_ANSWERED = "本次应答的从节点数量"
ITEM_ERROR = "错误状态字"
ADDR_ITEM_PREFIX = "从节点"
ADDR_ITEM_SUFFIX = "地址"

#: 10H-F1/F4 的「从节点总数量」是**路由档案中的从节点总数**（不是可达/在线节点数）
#: 出处：``adapter_10376/metadata/afn_fn.json`` 10H-F1 resp.fields[0].d
#: （"当前路由档案中的从节点总数"）、10H-F4 resp.fields[1].d（"路由档案从节点总数"）。
#: 措辞约定：这条 note 只用「档案」，**不出现「在网」** —— 「在网」是
#: :func:`judge_nodes_online`（两路证据）专属的措辞，防止两者再次被混为一谈。
ARCHIVE_COUNT_NOTE = "这是路由档案中的从节点总数，不等于真实可达的节点数"

#: 10H-F2 上行逐节点记录（解析器产物）：``从节点N地址`` / ``从节点N信息``，
#: 后者 ``desc`` 形如 ``信号品质=0 中继级别=15 相线=0 协议类型=1``。
NODE_ITEM_PREFIX = "从节点"
NODE_ITEM_SUFFIX = "信息"
#: 侦听信号品质的 ``desc`` 键名（D7~D4，0=无信号；见 metadata afn_fn.json 10H-F2 resp.list.record[1].d）
ITEM_SIGNAL_QUALITY = "信号品质"
#: 通信协议类型的 ``desc`` 键名（D13~D11；解析器 ``adapter_10376/__init__.py:690``
#: 已写入 desc，位语义同 ``sim_concentrator/deep_app.py:_node_from_record``：
#: 0 透明 / 1 645-1997 / 2 645-2007 / 3 698.45；E3 真帧校正：0x180F→3、0x50F1→2）
ITEM_PROTOCOL_TYPE = "协议类型"

#: 协议类型字节值 → 人读名（deep_app.py 同口径；REQS-0043 §G-6 三方一致性只读核验用）
PROTOCOL_TYPE_NAMES: Dict[int, str] = {
    0: "透明", 1: "645-1997", 2: "645-2007", 3: "698.45",
}

#: 10H-F4 上行应答里的字段名
ITEM_WORK_STATE = "工作状态"
ITEM_CURRENT_STATE = "当前状态"
ITEM_SWITCH = "工作开关"
ITEM_RUN_WORD = "运行状态字"
#: 10H-F4「已抄从节点数量」= 已成功抄读的从节点数（判「在网/可达」的证据 A）
ITEM_ANSWERED_TOTAL = "已抄从节点数量"
#: 10H-F4「中继抄到从节点数量」= 经中继抄读成功的从节点数
ITEM_RELAY_ANSWERED_TOTAL = "中继抄到从节点数量"
WORK_RUNNING = "抄表"          # 工作状态=抄表 ⇒ 路由在运行
WORK_LEARNING = "学习"         # 工作状态=学习 ⇒ 正在注册组网

#: ``运行状态字`` 的位定义（1B）：D1 = 路由完成标志（1=路由学习完成/0=未完成）
#: 出处：metadata ``afn_fn.json`` 10H-F4 resp.fields[0].d；知识库
#: ``03_QGDW10376.2_全帧类型.md:1088`` 与 ``协议层/92_台区识别协议_帧格式.md:187``
RUN_WORD_ROUTE_DONE_BIT = 1
#: ``工作开关`` 的位定义（1B）：D1 = 工作状态（1=学习/0=抄表），D7~D6 = 当前状态
SWITCH_WORK_STATE_BIT = 1
SWITCH_CURRENT_STATE_SHIFT = 6

#: 路由状态分类（``classify_route_state`` 的返回值；块态仍是 pass/fail/skipped/unknown 四态，
#: 这里的 label 只用于 detail/actual 里给出可机器读的细分。位语义出处见
#: ``adapter_10376/metadata/afn_fn.json`` 10H-F4 resp.fields[0].d / fields[4].d）
ROUTE_RUNNING = "running"                            # 工作状态=抄表 → pass
ROUTE_LEARNING_INCONCLUSIVE = "learning_inconclusive"  # 学习 + 路由已完成 + 档案>0 → inconclusive
ROUTE_LEARNING_INCOMPLETE = "learning_incomplete"      # 学习 + 路由完成标志=0 → 单帧不能判 fail
ROUTE_UNKNOWN = "route_unknown"                        # 字段缺失/取值非法 → unknown

#: 默认「期望在网节点数下限」/ 默认 10H-F2 查询条数（``nwk.nodes_online`` 参数默认值）
DEFAULT_MIN_ONLINE = 1
DEFAULT_NODES_ONLINE_COUNT = 32


# --------------------------------------------------------------------------- #
# 1. 记法归一
# --------------------------------------------------------------------------- #


def int_of(value: Any) -> Optional[int]:
    """尽力取十进制整数（``"3"``/``3``）；取不到 → ``None``（绝不猜）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 10)
    except ValueError:
        return None


def afn_int(value: Any) -> Optional[int]:
    """AFN → 字节值：字符串按 hex（``"11"``→0x11），整数按字节值（``17``→0x11）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 0xFF else None
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    try:
        number = int(text, 16)
    except ValueError:
        return None
    return number if 0 <= number <= 0xFF else None


def fn_int(value: Any) -> Optional[int]:
    """FN → 十进制编号：``"F2"``/``"2"``/``2`` → 2。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text[:1].upper() == "F":
        text = text[1:]
    return int_of(text)


def normalize_addr(value: Any) -> str:
    """人读 12 位 BCD 表地址归一（去空格/连字符、大写）；非法 → ``""``。

    只接受 12 位十进制数字（与 ``adapter_10376._bcd_bytes`` 的校验口径一致），
    出现 A~F 即判非法——BCD 地址不含十六进制字母。
    """
    if value is None or isinstance(value, bool):
        return ""
    text = "".join(str(value).split()).replace("-", "")
    return text if len(text) == ADDR_LEN and text.isdigit() else ""


def archive_addr_of(meter_addr: Any) -> str:
    """电表 645 人读地址 → **CCO 档案侧人读地址**（REQS-0042 D2-1/D2-2 唯一换算处）。

    口径（台架实测）：两边是同一串 6 字节 BCD 的**整体反序**——
    电表脑地址 ``123456789012``（字节 ``12 34 56 78 90 12``）对应档案侧人读
    ``129078563412``（字节 ``12 90 78 56 34 12``）。原因是 1376.2 多字节字段
    「低字节在前」，而 645 地址域按人读顺序摆、档案表按传输序展示。

    ``11H-F1/F2`` 建档/查询、``10H-F2`` 判据都用它；``02H-F1`` 内层 645 帧用
    :func:`meter_addr_wire`（**不**反转）——两个函数一个反向一个不反向，别混用。

    非法输入（非 12 位十进制 / ``None`` / 空）→ ``""``，**不抛异常**。
    """
    text = normalize_addr(meter_addr)
    if not text:
        return ""
    return bytes.fromhex(text)[::-1].hex()


def meter_addr_wire(meter_addr: Any) -> bytes:
    """电表 645 人读地址 → 645 帧**地址域 6 字节线序**（按人读顺序，**不反转**）。

    例：``"123456789012"`` → ``bytes.fromhex("123456789012")``
    = ``b"\\x12\\x34\\x56\\x78\\x90\\x12"``。
    注意 ``parser_lib`` 的 ``adapter_10376._bcd_bytes`` 会反序（那是 1376.2 档案
    口径），**不要**把它和本函数当同一个东西：给 645 构帧就用本函数。

    非法输入 → ``b""``，不抛异常。
    """
    text = normalize_addr(meter_addr)
    if not text:
        return b""
    return bytes.fromhex(text)


def items_of(parsed: Any) -> List[dict]:
    """取解析结果里的数据项列表（``decode`` 的 ``items``）。"""
    if not isinstance(parsed, Mapping):
        return []
    items = parsed.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, Mapping)]


def item_value(parsed: Any, name: str) -> Any:
    """按字段名取数据项 ``value``（取不到 → ``None``，绝不猜）。"""
    for item in items_of(parsed):
        if str(item.get("name") or "") == name:
            return item.get("value")
    return None


def field_raw(parsed: Any, name: str) -> Any:
    """按字段名取信封字段 ``raw``（``AFN``/``FN``/``控制域C``）。"""
    if not isinstance(parsed, Mapping):
        return None
    fields = parsed.get("fields")
    if not isinstance(fields, Mapping):
        return None
    entry = fields.get(name)
    return entry.get("raw") if isinstance(entry, Mapping) else None


def afn_fn_of(parsed: Any) -> Tuple[Optional[int], Optional[int]]:
    """从解析结果取 (AFN 字节值, FN 十进制编号)。"""
    afn = field_raw(parsed, "AFN")
    if not isinstance(afn, int) and isinstance(parsed, Mapping):
        afn = parsed.get("afn")
    fn = field_raw(parsed, "FN")
    if not isinstance(fn, int) and isinstance(parsed, Mapping):
        fn = parsed.get("fn")
    return (afn if isinstance(afn, int) else None, fn if isinstance(fn, int) else None)


def coerce_float(value: Any, default: float) -> float:
    """``float(value)`` 且不为负；取不到/非法 → ``default``。"""
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return default


def coerce_int(value: Any, default: int) -> int:
    """``int(value)`` 且不为负；取不到/非法 → ``default``。"""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 2. ctx / 块参数取值
# --------------------------------------------------------------------------- #


def block_action_params(params: Any) -> Dict[str, Any]:
    """取块 action 的**参数字典**：嵌套 ``params`` 为基础，顶层扁平字段覆盖它。

    适配执行器契约 ``params = dict(spec.action); params.update(ctx.block_params[id])``：
    JSON 里参数写在 ``action.params``（嵌套），运行期覆盖写在顶层（扁平），两者都认；
    扁平字段优先（``ctx.block_params`` 覆盖块声明）。
    """
    raw: Mapping = params if isinstance(params, Mapping) else {}
    nested = raw.get("params")
    merged: Dict[str, Any] = dict(nested) if isinstance(nested, Mapping) else {}
    for key, value in raw.items():
        if key in DECL_KEYS or str(key).startswith("_"):
            continue
        merged[key] = value
    return merged


def extras_of(ctx: Any) -> Mapping:
    """取 ctx.extras（``RunContext`` 或普通 dict 都认）。"""
    if ctx is None:
        return {}
    extras = getattr(ctx, "extras", None)
    if isinstance(extras, Mapping):
        return extras
    return ctx if isinstance(ctx, Mapping) else {}


def flag_of(value: Any, default: bool = False) -> bool:
    """把「开关」类块参数归一成 ``bool``（``None`` → ``default``）。

    为什么需要它：块参数既可能来自 JSON（真 ``bool``），也可能来自
    ``ctx.block_params`` 的运行期覆盖（命令行/接口常给字符串 ``"0"`` / ``"false"``）。
    ``bool("false") is True`` 这种坑会让「显式关掉自动开口」变成静默打开，
    所以字符串口径一律按 ``0/false/no/off/n/f`` 判假，空串等于没给（回落到默认值）。
    """
    if value is None:
        return bool(default)
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return bool(default)
        return text not in {"0", "false", "no", "off", "n", "f"}
    return bool(value)


# --------------------------------------------------------------------------- #
# 3. 能力解析与就绪门（1376.2 simcon）
# --------------------------------------------------------------------------- #


def resolve_simcon(ctx: Any = None) -> Tuple[Any, bool]:
    """取模拟集中器能力句柄，返回 ``(capability, injected)``。

    ``injected=True``（调用方注入的替身/共享句柄）时不再 probe；
    ``injected=False`` 表示按 ``WORKBENCH_BASE`` 自建，调用方须先 :func:`capability_gate`。
    """
    extras = extras_of(ctx)
    candidates = [extras.get(key) for key in SIMCON_KEYS]
    bundle = extras.get("capabilities")
    if isinstance(bundle, Mapping):
        candidates.append(bundle.get("simcon"))
    for candidate in candidates:
        if candidate is not None and hasattr(candidate, "verify"):
            return candidate, True
    return SimconCapability(), False


def capability_gate(cap: Any, injected: bool, label: str = "simcon") -> Optional[BlockResult]:
    """自建能力先探就绪；不就绪 → ``skipped``（块未执行），返回 ``None`` 表示可继续。"""
    if injected:
        return None
    probe = getattr(cap, "probe", None)
    if not callable(probe):
        return None
    try:
        health = probe()
    except Exception as exc:  # noqa: BLE001 - 探测失败也不崩
        return BlockResult(
            state=BLOCK_SKIPPED,
            detail="%s 能力探测异常，块未执行：%s: %s" % (label, type(exc).__name__, exc),
        )
    if getattr(health, "ok", False):
        return None
    return BlockResult(
        state=BLOCK_SKIPPED,
        detail="%s 能力不就绪，块未执行（不是 FAIL）：%s"
        % (label, getattr(health, "reason", "") or "probe 返回 ok=False"),
    )


def require_target(
    params: Any, want_afn: int, want_fn: int, block_id: str
) -> Tuple[Optional[Tuple[int, int]], str]:
    """校验块声明的 afn/fn 是本块支持的那一对，返回 ``((afn, fn), 错误原因)``。"""
    raw: Mapping = params if isinstance(params, Mapping) else {}
    afn = afn_int(raw.get("afn"))
    fn = fn_int(raw.get("fn"))
    if afn is None or fn is None:
        return None, "块声明缺少合法的 afn/fn：%s 应为 afn=%r（hex）/fn=%r" % (
            block_id,
            "%02X" % want_afn,
            want_fn,
        )
    if (afn, fn) != (want_afn, want_fn):
        return None, (
            "%s 只支持 AFN=0x%02X / Fn=%d，声明里是 afn=%r / fn=%r"
            "（记法：AFN 字符串按 hex、整数按字节值；FN 为十进制编号）"
            % (block_id, want_afn, want_fn, raw.get("afn"), raw.get("fn"))
        )
    return (afn, fn), ""


# --------------------------------------------------------------------------- #
# 4. verify 调用与整体失败归一
# --------------------------------------------------------------------------- #


def _call_verify_once(cap: Any, steps: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """真调一次 ``cap.verify``，把任何异常收敛成 ``{"ok": False, "error": ...}``。"""
    try:
        raw = cap.verify([dict(step) for step in steps])
    except Exception as exc:  # noqa: BLE001 - 适配器承诺不裸崩，替身也可能抛
        return {
            "ok": False,
            "error": "capability_error",
            "detail": "%s: %s" % (type(exc).__name__, exc),
            "steps": [],
        }
    if not isinstance(raw, Mapping):
        return {"ok": bool(raw), "steps": [], "detail": "verify 返回非字典：%r" % (raw,)}
    return dict(raw)


def _simcon_target(cap: Any) -> str:
    """自愈留痕的 target（点名是哪一路 simcon 的闸门，便于事后追溯）。"""
    return "simcon:%s" % (getattr(cap, "mapping_id", None) or "-")


def call_verify(
    cap: Any,
    steps: Sequence[Mapping[str, Any]],
    *,
    closer: Any = None,
    retry: int = SELF_HEAL_RETRY,
    conflict: Any = None,
) -> Dict[str, Any]:
    """调能力层 ``verify``（simcon 侧提交），**占用类错误自愈一次**后再看结果。

    口径（REQS-0042 §3.4 / D2-7，用户原话「不看归属，直接抢占为自己所用」）：

    - 非占用错误（422 参数错 / 不可达 / 轮询超时 / 空 steps …）→ **一次都不重试**，
      行为与原口径逐字一致（自愈解决不了配置错）；
    - ``http_409``（串口闸门/会话忙，可能有残留任务）→ 调 close 释放（**不判归属**）
      → 重试**一次**；仍 409 就交上层（块态仍是 ``skipped``，S3 才映射 blocked）；
    - 留痕两路落地：写进执行器的自愈收集器（最终挂到 ``BlockRecord.self_healed``），
      同时放在返回值的 ``self_healed`` 键里（块若把整个结果返回也能带上）。

    ``closer`` 可注入（单测用假对象）；缺省按 ``cap.close()`` 解析 —— **不硬编码 HTTP**。
    """
    body = [dict(step) for step in steps]
    result, healed = with_self_heal(
        lambda: _call_verify_once(cap, body),
        action="simcon.close",
        target=_simcon_target(cap),
        cap=cap,
        closer=closer if closer is not None else capability_closer(cap, SIMCON_CLOSER_NAMES),
        retry=retry,
        conflict=conflict,
    )
    if healed and isinstance(result, Mapping):
        result = dict(result)
        result[SELF_HEALED_KEY] = [dict(item) for item in healed]
    return result


def verify_failure_state(result: Mapping[str, Any]) -> Tuple[str, str]:
    """verify 整体失败 → ``(块态, 原因)``；**409 自愈过就不再重试**，超时/配置错不判设备 FAIL。

    ``result`` 里若带 ``self_healed``（:func:`call_verify` 塞的），原因串会补一句
    「自愈记录：…」，这样块的 ``detail`` 也看得见「抢占了谁/已抢占一次仍失败」。
    """
    code = str(result.get("error") or "")
    detail = str(result.get("detail") or "")
    note = self_heal_note(result)
    if code in BUSY_ERRORS:
        return BLOCK_SKIPPED, (
            "串口闸门/会话忙（%s）：%s；不自动重试 POST（占用类只自愈一次、不判归属），"
            "交上层策略%s" % (code, detail, note)
        )
    if code in DOWN_ERRORS:
        return BLOCK_SKIPPED, "台架不可用（%s）：%s%s" % (code, detail, note)
    if code.startswith("http_5"):
        return BLOCK_SKIPPED, "工作台服务异常（%s）：%s%s" % (code, detail, note)
    if code == "poll_timeout":
        return BLOCK_UNKNOWN, (
            "轮询 job 超时（poll_timeout）：%s；不表示设备失败%s" % (detail or "预算用尽", note)
        )
    if code in CONFIG_ERRORS:
        return BLOCK_UNKNOWN, (
            "块参数/构帧配置错误（%s，非设备结论）：%s%s" % (code, detail, note)
        )
    return BLOCK_UNKNOWN, "verify 未得到可判定结论（%s）：%s%s" % (code or "未知错误", detail, note)


def steps_of(result: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """取 verify 结果里的步骤列表（可能为空 = 任务没跑起来）。"""
    steps = result.get("steps")
    return [step for step in steps if isinstance(step, Mapping)] if isinstance(steps, list) else []


def journal_parsed(
    cap: Any, job: Any, want_afn: Optional[int], want_fn: Optional[int]
) -> Tuple[Optional[Mapping[str, Any]], str]:
    """步骤结果里没有 ``parsed`` 时，从会话帧日志补一帧解析结果。

    判定必须**看到**应答帧内容才能成立，所以服务端没回 ``parsed`` 时不能直接采信
    「步骤 pass」。返回 ``(parsed|None, 说明)``；``None`` 时调用方只能给 skipped/unknown，
    **绝不判 pass**。会话不存在后端回 404（``error="http_404"``）——同样是「看不到帧」，
    不是设备失败。
    """
    frames = getattr(cap, "frames", None)
    if not callable(frames):
        return None, "能力不支持会话帧日志查询，无法看到应答帧"
    run_id = job.get("run_id") if isinstance(job, Mapping) else None
    try:
        got = frames(after_seq=0, run_id=run_id, afn=want_afn, fn=want_fn, limit=20)
    except Exception as exc:  # noqa: BLE001 - 替身/能力异常一律降级
        return None, "会话帧日志查询异常：%s: %s" % (type(exc).__name__, exc)
    if not isinstance(got, Mapping) or not got.get("ok"):
        code = str(got.get("error") or "unknown") if isinstance(got, Mapping) else "unknown"
        detail = str(got.get("detail") or "") if isinstance(got, Mapping) else ""
        return None, "会话帧日志不可用（%s）：%s" % (code, detail)
    entries = [e for e in (got.get("entries") or []) if isinstance(e, Mapping)]
    for entry in reversed(entries):
        parsed = entry.get("parsed")
        if isinstance(parsed, Mapping) and parsed.get("items"):
            return parsed, "会话帧日志命中 seq=%s" % entry.get("seq")
    return None, "会话帧日志里没有可解析的应答帧（entries=%d）" % len(entries)


def snapshot_kind(
    block_state: str, result: Optional[Mapping[str, Any]] = None, kind: str = ""
) -> str:
    """块态/失败码 → 快照 ``state`` 名称（供判定函数写 detail 用）。

    唯一实现（原 ``archive._snapshot_kind`` 与 ``nwk._snapshot_kind`` 是同一份逻辑的
    两个副本，已合并）。
    """
    if kind in ("deny", "config", "no_reply", "unavailable"):
        return kind
    code = str((result or {}).get("error") or "")
    if block_state == BLOCK_SKIPPED:
        return "busy" if code in BUSY_ERRORS else "unavailable"
    if code == "poll_timeout":
        return "timeout"
    return "unknown"


# --------------------------------------------------------------------------- #
# 5. 单步构造与判定
# --------------------------------------------------------------------------- #


def send_step(
    name: str,
    afn: Any,
    fn: Any,
    params: Optional[Mapping[str, Any]] = None,
    *,
    expect_afn: int,
    expect_fn: int,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """构造一跳语义步骤（语义化 send，不写死 hex，REQS §10.3「不复制构帧逻辑」）。"""
    try:
        limit = float(timeout)
    except (TypeError, ValueError):
        limit = DEFAULT_TIMEOUT
    if limit <= 0:
        limit = DEFAULT_TIMEOUT
    return {
        "name": name,
        "send": {"afn": afn, "fn": fn, "params": dict(params or {})},
        "expect": {"afn": expect_afn, "fn": expect_fn, "dir": "up"},
        "expect_timeout": limit,
    }


def judge_step(
    step: Any,
    *,
    want_afn: Optional[int],
    want_fn: Optional[int],
    label: str = "步骤",
    cap: Any = None,
    job: Any = None,
) -> Dict[str, Any]:
    """判定一跳结果，返回 ``{"kind", "state", "detail", "parsed"}``。

    ``kind``：``answered`` / ``deny`` / ``no_reply`` / ``config`` / ``unavailable`` /
    ``unknown``。

    状态口径（领导裁决，全块层统一）：

    - 帧已发出但超时无应答（``result=fail`` 且带 ``sent_hex``）→ ``fail``；
    - 帧未发出 / 构帧失败（422）→ ``unknown``（配置错误，不是设备结论）；
    - steps 里没有 ``parsed`` 且帧日志也补不到（含 ``frames`` 404 无会话）→ ``skipped``。

    ``cap``+``job`` 用于「步骤里没有 parsed」时翻会话帧日志补帧（见 :func:`journal_parsed`）：
    补不到 → ``unavailable`` + ``skipped``（看不到应答帧就不能判 pass）。
    """
    if not isinstance(step, Mapping):
        return {
            "kind": "unknown",
            "state": BLOCK_UNKNOWN,
            "detail": "%s：步骤结果缺失（任务可能没执行到这一步）" % label,
            "parsed": None,
        }
    reason = str(step.get("reason") or "")
    parsed = step.get("parsed") if isinstance(step.get("parsed"), Mapping) else None
    matched = str(step.get("matched") or "")
    sent = str(step.get("sent_hex") or "")
    outcome = str(step.get("result") or "").strip().lower()

    if "构帧失败" in reason:
        return {
            "kind": "config",
            "state": BLOCK_UNKNOWN,
            "detail": "%s：构帧失败（块参数错误，非设备结论）：%s" % (label, reason),
            "parsed": None,
        }

    if parsed is None and outcome == "pass":
        supplemented, note = journal_parsed(cap, job, want_afn, want_fn)
        if supplemented is None:
            return {
                "kind": "unavailable",
                "state": BLOCK_SKIPPED,
                "detail": "%s：步骤 pass 但看不到应答帧内容（%s），本块不能判 pass ⇒ skipped"
                % (label, note),
                "parsed": None,
            }
        parsed = supplemented
        matched = matched or "（日志补帧）"

    got_afn, got_fn = afn_fn_of(parsed)
    if got_afn == AFN_CONFIRM and got_fn == FN_DENY:
        err = item_value(parsed, ITEM_ERROR)
        return {
            "kind": "deny",
            "state": BLOCK_FAIL,
            "detail": "%s：设备回否认帧 00H-F2（错误状态字=%s）%s"
            % (label, err if err is not None else "?", (" 帧=" + matched) if matched else ""),
            "parsed": parsed,
        }
    if want_afn is not None and got_afn is not None and got_afn != want_afn:
        return {
            "kind": "unknown",
            "state": BLOCK_UNKNOWN,
            "detail": "%s：应答 AFN 不符（期望 0x%02X，实得 0x%02X）%s"
            % (label, want_afn, got_afn, (" 帧=" + matched) if matched else ""),
            "parsed": parsed,
        }
    if want_fn is not None and got_fn is not None and got_fn != want_fn:
        return {
            "kind": "unknown",
            "state": BLOCK_UNKNOWN,
            "detail": "%s：应答 Fn 不符（期望 F%d，实得 F%d）%s"
            % (label, want_fn, got_fn, (" 帧=" + matched) if matched else ""),
            "parsed": parsed,
        }

    if outcome == "pass":
        return {
            "kind": "answered",
            "state": BLOCK_PASS,
            "detail": "%s：收到期望应答%s" % (label, ("（" + matched + "）") if matched else ""),
            "parsed": parsed,
        }
    if outcome == "fail":
        if sent and "发送失败" not in reason:
            return {
                "kind": "no_reply",
                "state": BLOCK_FAIL,
                "detail": "%s：已发出 %s，但%s"
                % (label, sent, reason or "超时未收到应答"),
                "parsed": parsed,
            }
        return {
            "kind": "unknown",
            "state": BLOCK_UNKNOWN,
            "detail": "%s：激励未发出（%s），无法判定设备行为" % (label, reason or "原因未给出"),
            "parsed": parsed,
        }
    return {
        "kind": "unknown",
        "state": BLOCK_UNKNOWN,
        "detail": "%s：步骤结果无结论字段（result=%r）" % (label, step.get("result")),
        "parsed": parsed,
    }


def judge_confirm_step(
    step: Any, label: str = "确认帧", *, cap: Any = None, job: Any = None
) -> Dict[str, Any]:
    """判定 00H-F1 确认步骤；``kind=="answered"`` 即「确认已收到」。"""
    return judge_step(
        step, want_afn=AFN_CONFIRM, want_fn=FN_CONFIRM, label=label, cap=cap, job=job
    )


# --------------------------------------------------------------------------- #
# 6. 10H-F2 档案表快照与判定（块与事实唯一判定数据源，REQS FT-2）
# --------------------------------------------------------------------------- #


def node_index_of(name: Any) -> Optional[int]:
    """``从节点3地址`` / ``从节点3信息`` → ``3``（其它名字 → ``None``）。"""
    text = str(name or "")
    if not text.startswith(NODE_ITEM_PREFIX):
        return None
    rest = text[len(NODE_ITEM_PREFIX):]
    for suffix in (ADDR_ITEM_SUFFIX, NODE_ITEM_SUFFIX):
        if rest.endswith(suffix):
            return int_of(rest[: -len(suffix)])
    return None


def node_signal_quality(item: Any) -> Optional[int]:
    """从 ``从节点N信息`` 的 ``desc`` 里取「信号品质」（取不到 → ``None``，绝不猜 0）。"""
    if not isinstance(item, Mapping):
        return None
    return int_of(_desc_state(item.get("desc"), ITEM_SIGNAL_QUALITY))


def node_protocol_type(item: Any) -> Optional[int]:
    """从 ``从节点N信息`` 的 ``desc`` 里取「协议类型」（D13~D11，取不到 → ``None``）。

    解析器（``adapter_10376/__init__.py:690``）把节点信息 16 位字的
    D13~D11（通信协议类型）写进 ``desc`` 的 ``协议类型=N``；语义
    0 透明 / 1 645-1997 / 2 645-2007 / 3 698.45（deep_app.py 同口径）。
    REQ S-0043 §G-6：三方一致性只读核验用（CCO 档案侧 10H-F2 实际落地值）。
    """
    if not isinstance(item, Mapping):
        return None
    return int_of(_desc_state(item.get("desc"), ITEM_PROTOCOL_TYPE))


def protocol_type_name(value: Any) -> str:
    """协议类型字节值 → 人读名（取不到 → 空串，绝不猜）。"""
    number = int_of(value)
    if number is None:
        return ""
    return PROTOCOL_TYPE_NAMES.get(number, "未知(0x%X)" % number)


def page_table(parsed: Any) -> Dict[str, Any]:
    """把一页 10H-F2 上行应答归一成 ``{total, answered, addresses, nodes}``。

    字段口径与 ``sim_concentrator.recipes._query_all_archives`` 一致：
    ``从节点总数量`` + ``从节点N地址``。

    ``nodes`` 是逐节点 ``{"addr": <12 位地址>, "signal": int|None, "protocol": int|None}``：
    ``signal`` 取自解析器写在 ``从节点N信息`` 的 ``desc`` 里的 ``信号品质=N``
    （D7~D4 侦听信号品质，0=无信号）；``protocol`` 取自同一 ``desc`` 的
    ``协议类型=N``（D13~D11，0 透明/1 645-1997/2 645-2007/3 698.45，§G-6
    三方一致性核验用）。**它们只描述这一条档案记录的快照，不代表节点在线**，
    在线判定见 :func:`judge_nodes_online`。
    """
    total = int_of(item_value(parsed, ITEM_TOTAL))
    answered = int_of(item_value(parsed, ITEM_ANSWERED))
    addresses: List[str] = []
    slots: Dict[int, Dict[str, Any]] = {}
    order: List[int] = []
    for item in items_of(parsed):
        index = node_index_of(item.get("name"))
        if index is None:
            continue
        if index not in slots:
            slots[index] = {"addr": "", "signal": None, "protocol": None}
            order.append(index)
        if str(item.get("name") or "").endswith(ADDR_ITEM_SUFFIX):
            addr = normalize_addr(item.get("value"))
            if addr:
                slots[index]["addr"] = addr
        else:
            slots[index]["signal"] = node_signal_quality(item)
            slots[index]["protocol"] = node_protocol_type(item)
    for index in order:
        addr = slots[index]["addr"]
        if addr and addr not in addresses:
            addresses.append(addr)
    nodes = [dict(slots[index]) for index in order if slots[index]["addr"]]
    return {"total": total, "answered": answered, "addresses": addresses, "nodes": nodes}


def clamp_count(count: Any) -> int:
    """页容量归一：非正/非法 → 默认 16；超过 1B 上限 → 255。"""
    value = int_of(count)
    if value is None or value <= 0:
        return DEFAULT_QUERY_COUNT
    return min(value, COUNT_MAX)


def clamp_pages(pages: Any) -> int:
    """翻页上限归一：非正/非法 → 默认 4；上限 16（防无限翻页）。"""
    value = int_of(pages)
    if value is None or value <= 0:
        return DEFAULT_MAX_PAGES
    return min(value, 16)


def archive_snapshot(
    *,
    cap: Any = None,
    ctx: Any = None,
    afn: Any = AFN_ROUTE_QUERY,
    fn: Any = FN_ARCHIVE_QUERY,
    start: Any = 0,
    count: Any = DEFAULT_QUERY_COUNT,
    max_pages: Any = DEFAULT_MAX_PAGES,
    expect_timeout: Any = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """发 10H-F2 分页查询，把档案表归一成可判定快照。

    返回：:

        {"state": "answered"|"no_reply"|"deny"|"busy"|"unavailable"|"timeout"
                  |"config"|"unknown",
         "block_state": "pass"|"fail"|"skipped"|"unknown",   # 便于块层直接用
         "addresses": [...12 位地址], "total": int|None, "complete": bool,
         "nodes": [{"addr": <12 位地址>, "signal": int|None}],
         "pages": int, "detail": "...", "job_ids": [...], "steps": [...]}

    ``complete=True`` 表示「已查到的条数 ≥ total」或「应答条数小于页容量（无下一页）」，
    此时「查不到」才能作为否定结论（:func:`judge_contains` 依赖它）。
    """
    capability, injected = (cap, True) if cap is not None else resolve_simcon(ctx)
    gate = capability_gate(capability, injected)
    if gate is not None:
        return {
            "state": "unavailable",
            "block_state": BLOCK_SKIPPED,
            "addresses": [],
            "nodes": [],
            "total": None,
            "complete": False,
            "pages": 0,
            "detail": gate.detail,
            "job_ids": [],
            "steps": [],
        }

    start_base = int_of(start) or 0
    page_size = clamp_count(count)
    page_limit = clamp_pages(max_pages)
    addresses: List[str] = []
    nodes: List[Dict[str, Any]] = []
    total: Optional[int] = None
    complete = False
    pages = 0
    job_ids: List[str] = []
    steps: List[Mapping[str, Any]] = []
    detail = ""

    while pages < page_limit:
        page_start = start_base + pages * page_size
        step = send_step(
            "查询从节点档案 start=%d count=%d" % (page_start, page_size),
            afn,
            fn,
            {"start": page_start, "count": page_size},
            expect_afn=AFN_ROUTE_QUERY,
            expect_fn=FN_ARCHIVE_QUERY,
            timeout=expect_timeout,
        )
        result = call_verify(capability, [step])
        job_id = str(result.get("job_id") or "")
        if job_id:
            job_ids.append(job_id)
        got_steps = steps_of(result)
        pages += 1
        if not got_steps:
            state, why = verify_failure_state(result)
            return {
                "state": snapshot_kind(state, result),
                "block_state": state,
                "addresses": addresses,
                "nodes": nodes,
                "total": total,
                "complete": False,
                "pages": pages,
                "detail": why,
                "job_ids": job_ids,
                "steps": steps,
            }

        page_step = got_steps[-1]
        steps.append(page_step)
        verdict = judge_step(
            page_step,
            want_afn=AFN_ROUTE_QUERY,
            want_fn=FN_ARCHIVE_QUERY,
            label="10H-F2 查询第 %d 页" % pages,
            cap=capability,
            job=result,
        )
        if verdict["kind"] != "answered":
            return {
                "state": snapshot_kind(verdict["state"], None, verdict["kind"]),
                "block_state": verdict["state"],
                "addresses": addresses,
                "nodes": nodes,
                "total": total,
                "complete": False,
                "pages": pages,
                "detail": verdict["detail"],
                "job_ids": job_ids,
                "steps": steps,
            }

        table = page_table(verdict.get("parsed"))
        if table["total"] is None:
            # 「看不到总数」不能当成「空档案表」——否则会把无法解析的应答判成 FAIL
            return {
                "state": "unknown",
                "block_state": BLOCK_UNKNOWN,
                "addresses": addresses,
                "nodes": nodes,
                "total": total,
                "complete": False,
                "pages": pages,
                "detail": "10H-F2 应答里没有可解析的「%s」字段，无法判定档案表（%s）"
                % (ITEM_TOTAL, verdict["detail"]),
                "job_ids": job_ids,
                "steps": steps,
            }
        if total is None:
            total = table["total"]
        new = [addr for addr in table["addresses"] if addr not in addresses]
        addresses.extend(new)
        nodes.extend(node for node in table["nodes"] if node["addr"] in new)
        detail = "10H-F2 查询 %d 页，累计 %d 条（total=%s）" % (
            pages,
            len(addresses),
            total if total is not None else "未知",
        )
        if total is not None and len(addresses) >= total:
            complete = True
            break
        if len(table["addresses"]) < page_size or not new:
            complete = True   # 应答不满一页 / 无新增 → 没有下一页
            break
        if total is None:
            complete = True   # 无总数字段时不敢翻页（避免无限翻页）
            break

    return {
        "state": "answered",
        "block_state": BLOCK_PASS,
        "addresses": addresses,
        "nodes": nodes,
        "total": total,
        "complete": complete,
        "pages": pages,
        "detail": detail,
        "job_ids": job_ids,
        "steps": steps,
    }


def judge_contains(snapshot: Mapping[str, Any], addr: Any) -> Tuple[str, str]:
    """档案表是否含 ``addr``（三态，块与事实共用同一判定，REQS FT-2）。"""
    normalized = normalize_addr(addr)
    if not normalized:
        return FACT_UNKNOWN, "地址非法（需 12 位 BCD 数字）：%r" % (addr,)
    if not isinstance(snapshot, Mapping):
        return FACT_UNKNOWN, "档案表快照缺失，无法判定"
    state = str(snapshot.get("state") or "")
    if state != "answered":
        return FACT_UNKNOWN, "档案表查询未得到可判定应答（%s）：%s" % (
            state or "unknown",
            snapshot.get("detail") or "",
        )
    addresses = list(snapshot.get("addresses") or [])
    total = snapshot.get("total")
    if normalized in addresses:
        return FACT_YES, "10H-F2 档案表含 %s（total=%s，已查 %d 条）" % (
            normalized,
            total if total is not None else "未知",
            len(addresses),
        )
    if snapshot.get("complete"):
        return FACT_NO, "10H-F2 档案表（total=%s，已查 %d 条）不含 %s" % (
            total if total is not None else "未知",
            len(addresses),
            normalized,
        )
    return FACT_UNKNOWN, (
        "查询窗口未覆盖全部档案（total=%s，已查 %d 条），不能断定 %s 不存在"
        % (total if total is not None else "未知", len(addresses), normalized)
    )


# --------------------------------------------------------------------------- #
# 7. 10H-F1 档案数 / 10H-F4 路由状态 / 在网可达（三件事分开判，绝不互相顶替）
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 7.1 10H-F1「从节点总数量」= **档案数**（不是在网数）
# --------------------------------------------------------------------------- #


def archive_count_snapshot(
    *,
    cap: Any = None,
    ctx: Any = None,
    afn: Any = AFN_ROUTE_QUERY,
    fn: Any = FN_ONNET_COUNT,
    expect_timeout: Any = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """发 10H-F1 查**路由档案中的从节点总数**（档案数）。

    返回 ``{state, block_state, total, detail, job_ids, step}``，``total`` 是档案条数。
    10H-F1 只给档案数，**不给在网数** —— 判「在网/可达」必须走
    :func:`nodes_online_snapshot`（10H-F4 已抄数 + 10H-F2 逐节点信号品质）。
    """
    capability, injected = (cap, True) if cap is not None else resolve_simcon(ctx)
    gate = capability_gate(capability, injected)
    if gate is not None:
        return {
            "state": "unavailable",
            "block_state": BLOCK_SKIPPED,
            "total": None,
            "detail": gate.detail,
            "job_ids": [],
            "step": None,
        }
    result = call_verify(
        capability,
        [
            send_step(
                "查询路由档案中的从节点数量（10H-F1）",
                afn,
                fn,
                {},
                expect_afn=AFN_ROUTE_QUERY,
                expect_fn=FN_ONNET_COUNT,
                timeout=expect_timeout,
            )
        ],
    )
    job_ids = [str(result.get("job_id") or "")] if result.get("job_id") else []
    got_steps = steps_of(result)
    if not got_steps:
        state, why = verify_failure_state(result)
        return {
            "state": snapshot_kind(state, result),
            "block_state": state,
            "total": None,
            "detail": why,
            "job_ids": job_ids,
            "step": None,
        }
    step = got_steps[-1]
    verdict = judge_step(
        step,
        want_afn=AFN_ROUTE_QUERY,
        want_fn=FN_ONNET_COUNT,
        label="10H-F1 档案数查询",
        cap=capability,
        job=result,
    )
    if verdict["kind"] != "answered":
        return {
            "state": snapshot_kind(verdict["state"], None, verdict["kind"]),
            "block_state": verdict["state"],
            "total": None,
            "detail": verdict["detail"],
            "job_ids": job_ids,
            "step": step,
        }
    total = int_of(item_value(verdict.get("parsed"), ITEM_TOTAL))
    return {
        "state": "answered",
        "block_state": BLOCK_PASS,
        "total": total,
        "detail": "%s：从节点总数量=%s（%s）"
        % (
            verdict["detail"],
            total if total is not None else "缺失（应答无「%s」字段）" % ITEM_TOTAL,
            ARCHIVE_COUNT_NOTE,
        ),
        "job_ids": job_ids,
        "step": step,
    }


def judge_archive_count_min(snapshot: Mapping[str, Any], minimum: Any) -> Tuple[str, str]:
    """**档案数**是否 ≥ ``minimum``（三态；块与事实共用，REQS FT-2）。

    语义边界（写进每一条 detail，防止再被当成可达节点数）：
    10H-F1 的「从节点总数量」是路由**档案**里的条数，元数据原文
    「当前路由档案中的从节点总数」（``metadata/afn_fn.json`` 10H-F1 resp.fields[0].d）。
    档案里有节点 ≠ 节点真可达 —— 后者见 :func:`judge_nodes_online`。
    """
    limit = int_of(minimum)
    if limit is None or limit < 0:
        return FACT_UNKNOWN, "期望档案数下限 min 非法（需 ≥0 的整数）：%r" % (minimum,)
    if not isinstance(snapshot, Mapping):
        return FACT_UNKNOWN, "档案数快照缺失，无法判定"
    if str(snapshot.get("state") or "") != "answered":
        return FACT_UNKNOWN, "档案数查询未得到可判定应答（%s）：%s" % (
            snapshot.get("state") or "unknown",
            snapshot.get("detail") or "",
        )
    total = snapshot.get("total")
    if not isinstance(total, int):
        return FACT_UNKNOWN, "应答里没有可解析的「%s」字段，无法判定" % ITEM_TOTAL
    if total >= limit:
        return FACT_YES, "档案数 %d ≥ 期望档案数下限 %d（%s）" % (total, limit, ARCHIVE_COUNT_NOTE)
    return FACT_NO, "档案数 %d < 期望档案数下限 %d（%s）" % (total, limit, ARCHIVE_COUNT_NOTE)


#: 兼容别名：旧块 id ``nwk.onnet_count`` / 旧事实 ``nwk.onnet_count_min`` 仍可用，
#: 但它们判的一直是**档案数**（历史命名错误）。新代码请用 ``archive_count_*``。
onnet_snapshot = archive_count_snapshot
judge_onnet_min = judge_archive_count_min


def _bool_text(value: Any, *, missing: str = "缺失") -> str:
    """三态布尔 → 人读文本（True→``1`` / False→``0`` / ``None``→``missing``）。"""
    if value is True:
        return "1"
    if value is False:
        return "0"
    return missing


def _norm_work_state(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    lowered = text.lower()
    if text in ("学习", "learning") or lowered in ("learning", "study"):
        return WORK_LEARNING
    if text in ("抄表", "reading", "running") or lowered in ("reading", "running"):
        return WORK_RUNNING
    if text == "1":
        return WORK_LEARNING
    if text == "0":
        return WORK_RUNNING
    return text


def _norm_current_state(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    numbers = {"0": "抄表", "1": "搜表", "2": "升级", "3": "其他"}
    return numbers.get(text, text)


def _desc_state(desc: Any, key: str) -> Optional[str]:
    """从数据项 ``desc`` 里抠「工作状态=学习 / 当前状态=抄表」（解析器把状态写进 desc）。"""
    text = str(desc or "")
    marker = key + "="
    at = text.find(marker)
    if at < 0:
        return None
    rest = text[at + len(marker):]
    for stop in (" ", "　", ")", "（", "/", "、"):
        cut = rest.find(stop)
        if cut >= 0:
            rest = rest[:cut]
    return rest.strip() or None


def _switch_byte(value: Any) -> Optional[int]:
    """取 1 字节位域字段的字节值（``工作开关``/``运行状态字``；``"0x2C"``/``"2C"``/``44`` → 44）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 0xFF else None
    text = str(value).strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    try:
        number = int(text, 16)
    except ValueError:
        return None
    return number if 0 <= number <= 0xFF else None


def _route_done_of(desc: Any, byte: Optional[int]) -> Optional[bool]:
    """``运行状态字`` 的「路由完成标志」（D1：1=路由学习完成/0=未完成）。

    优先读解析器写在 ``desc`` 里的 ``路由完成=N``，其次按字节位 D1 解；
    两样都取不到 → ``None``（绝不默认 True/False）。
    """
    text = _desc_state(desc, "路由完成")
    if text is not None:
        head = text.strip()[:1]
        if head in ("0", "1"):
            return head == "1"
    if byte is not None:
        return bool((byte >> RUN_WORD_ROUTE_DONE_BIT) & 1)
    return None


def route_status_fields(parsed: Any) -> Dict[str, Any]:
    """从 10H-F4 上行解析结果提取「工作状态 / 当前状态」及其原始依据。

    取值优先级（**不复制解析逻辑，只在解析器未给字段时兜底**）：

    1. 数据项直接叫 ``工作状态`` / ``当前状态`` → 用它；
    2. 从 ``工作开关`` 的 ``desc`` 文本里抠（解析器把两个状态写在 desc）；
    3. 都没有时，才按 ``工作开关`` 字节位解（D1=工作状态 1学习/0抄表，
       D7~D6=当前状态 00抄表/01搜表/10升级/11其他，口径同 ``adapter_10376``）。

    同时给出三个数量/标志（判「在网」与「学习态分层」要用，字段名口径见
    ``metadata/afn_fn.json`` 10H-F4 resp.fields）：

    - ``archive_total``：``从节点总数量`` = **路由档案从节点总数**（档案数）；
    - ``answered_total``：``已抄从节点数量`` = 已成功抄读的从节点数（在网证据 A）；
    - ``relay_answered_total``：``中继抄到从节点数量``；
    - ``route_done``：``运行状态字`` D1 路由完成标志（True/False/None）。
    """
    work: Optional[str] = None
    current: Optional[str] = None
    switch_item: Optional[Mapping[str, Any]] = None
    run_word_item: Optional[Mapping[str, Any]] = None
    archive_total: Optional[int] = None
    answered_total: Optional[int] = None
    relay_answered_total: Optional[int] = None
    for item in items_of(parsed):
        name = str(item.get("name") or "")
        if name == ITEM_WORK_STATE:
            work = _norm_work_state(item.get("value"))
        elif name == ITEM_CURRENT_STATE:
            current = _norm_current_state(item.get("value"))
        elif name == ITEM_SWITCH:
            switch_item = item
        elif name == ITEM_RUN_WORD:
            run_word_item = item
        elif name == ITEM_TOTAL:
            archive_total = int_of(item.get("value"))
        elif name == ITEM_ANSWERED_TOTAL:
            answered_total = int_of(item.get("value"))
        elif name == ITEM_RELAY_ANSWERED_TOTAL:
            relay_answered_total = int_of(item.get("value"))
    source = "item"
    if switch_item is not None:
        desc = switch_item.get("desc")
        if work is None:
            parsed_work = _norm_work_state(_desc_state(desc, ITEM_WORK_STATE))
            if parsed_work is not None:
                work = parsed_work
                source = "desc"
        if current is None:
            parsed_current = _norm_current_state(_desc_state(desc, ITEM_CURRENT_STATE))
            if parsed_current is not None:
                current = parsed_current
                source = "desc"
    raw_switch = None
    if switch_item is not None:
        raw_switch = _switch_byte(switch_item.get("hex"))
        if raw_switch is None:
            raw_switch = _switch_byte(switch_item.get("value"))
        if raw_switch is None:
            raw_switch = _switch_byte(switch_item.get("raw"))
    if raw_switch is not None:
        if work is None:
            work = (WORK_LEARNING if (raw_switch >> SWITCH_WORK_STATE_BIT) & 1 else WORK_RUNNING)
            source = "bits"
        if current is None:
            current = {0: "抄表", 1: "搜表", 2: "升级"}.get(
                (raw_switch >> SWITCH_CURRENT_STATE_SHIFT) & 0x3, "其他"
            )
            source = "bits"
    run_word_byte = None
    route_done = None
    if run_word_item is not None:
        run_word_byte = _switch_byte(run_word_item.get("hex"))
        if run_word_byte is None:
            run_word_byte = _switch_byte(run_word_item.get("value"))
        if run_word_byte is None:
            run_word_byte = _switch_byte(run_word_item.get("raw"))
        route_done = _route_done_of(run_word_item.get("desc"), run_word_byte)
    return {
        "work_state": work,
        "current_state": current,
        "switch_byte": raw_switch,
        "run_word": run_word_item.get("value") if run_word_item is not None else None,
        "run_word_byte": run_word_byte,
        "route_done": route_done,
        "archive_total": archive_total,
        "answered_total": answered_total,
        "relay_answered_total": relay_answered_total,
        "source": source if (work or current) else "",
    }


def route_snapshot(
    *,
    cap: Any = None,
    ctx: Any = None,
    afn: Any = AFN_ROUTE_QUERY,
    fn: Any = FN_ROUTE_STATUS,
    expect_timeout: Any = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """发 10H-F4 查路由运行状态，返回带 ``work_state``/``current_state`` 的快照。"""
    capability, injected = (cap, True) if cap is not None else resolve_simcon(ctx)
    gate = capability_gate(capability, injected)
    if gate is not None:
        return {
            "state": "unavailable",
            "block_state": BLOCK_SKIPPED,
            "detail": gate.detail,
            "job_ids": [],
            "step": None,
            "work_state": None,
            "current_state": None,
            "switch_byte": None,
            "run_word": None,
            "run_word_byte": None,
            "route_done": None,
            "archive_total": None,
            "answered_total": None,
            "relay_answered_total": None,
            "source": "",
        }
    result = call_verify(
        capability,
        [
            send_step(
                "查询路由运行状态",
                afn,
                fn,
                {},
                expect_afn=AFN_ROUTE_QUERY,
                expect_fn=FN_ROUTE_STATUS,
                timeout=expect_timeout,
            )
        ],
    )
    job_ids = [str(result.get("job_id") or "")] if result.get("job_id") else []
    got_steps = steps_of(result)
    if not got_steps:
        state, why = verify_failure_state(result)
        return {
            "state": snapshot_kind(state, result),
            "block_state": state,
            "detail": why,
            "job_ids": job_ids,
            "step": None,
            "work_state": None,
            "current_state": None,
            "switch_byte": None,
            "run_word": None,
            "run_word_byte": None,
            "route_done": None,
            "archive_total": None,
            "answered_total": None,
            "relay_answered_total": None,
            "source": "",
        }
    step = got_steps[-1]
    verdict = judge_step(
        step,
        want_afn=AFN_ROUTE_QUERY,
        want_fn=FN_ROUTE_STATUS,
        label="10H-F4 路由状态查询",
        cap=capability,
        job=result,
    )
    if verdict["kind"] != "answered":
        return {
            "state": snapshot_kind(verdict["state"], None, verdict["kind"]),
            "block_state": verdict["state"],
            "detail": verdict["detail"],
            "job_ids": job_ids,
            "step": step,
            "work_state": None,
            "current_state": None,
            "switch_byte": None,
            "run_word": None,
            "run_word_byte": None,
            "route_done": None,
            "archive_total": None,
            "answered_total": None,
            "relay_answered_total": None,
            "source": "",
        }
    fields = route_status_fields(verdict.get("parsed"))
    detail = (
        "%s：工作状态=%s / 当前状态=%s / 路由完成标志=%s / 档案数=%s / 已抄=%s%s" % (
            verdict["detail"],
            fields["work_state"] or "缺失",
            fields["current_state"] or "缺失",
            _bool_text(fields.get("route_done")),
            fields["archive_total"] if fields["archive_total"] is not None else "缺失",
            fields["answered_total"] if fields["answered_total"] is not None else "缺失",
            "" if not fields["source"] else "（取值来源 %s）" % fields["source"],
        )
    )
    return {
        "state": "answered",
        "block_state": BLOCK_PASS,
        "detail": detail,
        "job_ids": job_ids,
        "step": step,
        **fields,
    }


def classify_route_state(snapshot: Mapping[str, Any]) -> Tuple[str, str]:
    """把 10H-F4 快照归到 :data:`ROUTE_RUNNING` 等四类之一，返回 ``(label, 说明)``。

    分层依据（**协议位语义，不靠猜**）：

    - ``工作开关`` D1 = 工作状态（1=学习/0=抄表）、D7~D6 = 当前状态；
    - ``运行状态字`` D1 = 路由完成标志（1=路由学习完成/0=未完成）。

    出处：``adapter_10376/metadata/afn_fn.json`` 10H-F4 ``resp.fields[0].d`` 与
    ``resp.fields[4].d``；知识库 ``03_QGDW10376.2_全帧类型.md:1088·1096``（两张位表）、
    ``协议层/92_台区识别协议_帧格式.md:187``（"路由完成标志（1=路由学习完成/0=未完成）"）。

    为什么要分层：刚下发 ``12H-F1 启动路由`` / ``11H-F5 主动注册`` 之后，CCO 进入学习态
    是**协议正常行为**（正在注册节点），把「工作状态=学习」直接判 FAIL 会把正常过程判失败。
    """
    if not isinstance(snapshot, Mapping):
        return ROUTE_UNKNOWN, "路由状态快照缺失，无法判定"
    if str(snapshot.get("state") or "") != "answered":
        return ROUTE_UNKNOWN, "路由状态查询未得到可判定应答（%s）：%s" % (
            snapshot.get("state") or "unknown",
            snapshot.get("detail") or "",
        )
    current = _norm_current_state(snapshot.get("current_state")) or "缺失"
    work = _norm_work_state(snapshot.get("work_state"))
    if not work:
        return ROUTE_UNKNOWN, (
            "应答里取不到「%s」，无法判定路由状态（当前状态=%s）"
            % (ITEM_WORK_STATE, current)
        )
    if work == WORK_RUNNING:
        return ROUTE_RUNNING, "工作状态=抄表（当前状态=%s），路由处于抄表工作态" % current
    if work != WORK_LEARNING:
        return ROUTE_UNKNOWN, (
            "「%s」取值非法（%r）：协议只定义了 学习/抄表 两种（工作开关 D1）"
            % (ITEM_WORK_STATE, work)
        )

    done = snapshot.get("route_done")
    archive = snapshot.get("archive_total")
    archive_text = archive if isinstance(archive, int) else "缺失"
    if done is True:
        if isinstance(archive, int) and archive > 0:
            return ROUTE_LEARNING_INCONCLUSIVE, (
                "路由已完成（运行状态字 D1 路由完成标志=1）且路由档案数=%d>0，"
                "但工作状态=学习（当前状态=%s）：路由基础已建立，尚未进入抄表工作"
                " → inconclusive（不确定），不是失败" % (archive, current)
            )
        if isinstance(archive, int):
            return ROUTE_LEARNING_INCONCLUSIVE, (
                "路由已完成（运行状态字 D1=1）但路由档案数=0，工作状态=学习（当前状态=%s）："
                "档案里没有节点可注册（主动注册仍可能补档），无法判失败 → inconclusive"
                % current
            )
        return ROUTE_LEARNING_INCONCLUSIVE, (
            "路由已完成（运行状态字 D1=1）且工作状态=学习（当前状态=%s），"
            "但应答里取不到档案数（%s），无法确认有无可注册节点 → inconclusive"
            % (current, archive_text)
        )
    if done is False:
        return ROUTE_LEARNING_INCOMPLETE, (
            "工作状态=学习（当前状态=%s）且运行状态字 D1 路由完成标志=0："
            "学习/注册尚未完成；本块只采一帧，无法证明「持续未完成」，"
            "按 inconclusive（unknown）处理（要判失败请显式给 params.learning_fail=true）"
            % current
        )
    return ROUTE_UNKNOWN, (
        "工作状态=学习（当前状态=%s），但运行状态字取不到「路由完成标志」"
        "（运行状态字=%r），无法判断学习是否已完成" % (current, snapshot.get("run_word"))
    )


def judge_route_status(
    snapshot: Mapping[str, Any], expect: Any = None, *, learning_fail: bool = False
) -> Tuple[str, str]:
    """「工作状态/当前状态」是否符合期望（三态）。

    - ``expect`` 为空 → 走 :func:`classify_route_state` 的分层口径（不是简单「学习=no」）：

      * ``ROUTE_RUNNING``（工作状态=抄表）→ ``yes``；
      * ``ROUTE_LEARNING_INCONCLUSIVE``（路由已完成 + 档案>0 但处于学习）→ ``unknown``
        （detail 写明 inconclusive，**不是 fail**）；
      * ``ROUTE_LEARNING_INCOMPLETE``（路由完成标志=0 + 学习）→ ``unknown``；
        只有显式 ``learning_fail=True`` 才 ``no``；
      * ``ROUTE_UNKNOWN``（字段缺失/取值非法）→ ``unknown``。

    - ``expect`` 是字符串或列表 → 「工作状态」或「当前状态」任一命中即 ``yes``，
      两项都解析到且都不命中才 ``no``；字段全缺 → ``unknown``（显式期望仍按原口径）。
    """
    if not isinstance(snapshot, Mapping):
        return FACT_UNKNOWN, "路由状态快照缺失，无法判定"
    if str(snapshot.get("state") or "") != "answered":
        return FACT_UNKNOWN, "路由状态查询未得到可判定应答（%s）：%s" % (
            snapshot.get("state") or "unknown",
            snapshot.get("detail") or "",
        )
    work = snapshot.get("work_state")
    current = snapshot.get("current_state")
    if not work and not current:
        return FACT_UNKNOWN, "应答里取不到「%s」/「%s」字段，无法判定" % (
            ITEM_WORK_STATE,
            ITEM_CURRENT_STATE,
        )
    wanted: List[str] = []
    if expect is None or expect == "" or expect == []:
        return judge_route_running(snapshot, learning_fail=learning_fail)
    if isinstance(expect, (list, tuple)):
        wanted = [str(item).strip() for item in expect if str(item).strip()]
    else:
        wanted = [str(expect).strip()]
    if not wanted:
        return judge_route_running(snapshot, learning_fail=learning_fail)
    actual = [value for value in (work, current) if value]
    hit = [value for value in actual if value in wanted]
    if hit:
        return FACT_YES, "状态符合期望：工作状态=%s / 当前状态=%s（期望 %s）" % (
            work or "缺失",
            current or "缺失",
            "/".join(wanted),
        )
    return FACT_NO, "状态不符合期望：工作状态=%s / 当前状态=%s（期望 %s）" % (
        work or "缺失",
        current or "缺失",
        "/".join(wanted),
    )


def judge_route_running(
    snapshot: Mapping[str, Any], *, learning_fail: bool = False
) -> Tuple[str, str]:
    """路由是否在运行（工作状态=抄表）：事实 ``nwk.route_running`` 的判定口径。

    与 :func:`judge_route_status` **同一份分层**（FT-2）：只有抄表态给 ``yes``；
    学习态按路由完成标志细分成两种 inconclusive（``unknown``），
    只有 ``learning_fail=True`` 时「学习 + 路由未完成」才 ``no``。
    """
    label, why = classify_route_state(snapshot)
    if label == ROUTE_RUNNING:
        return FACT_YES, "路由在运行：%s" % why
    if label == ROUTE_LEARNING_INCOMPLETE and learning_fail:
        return FACT_NO, "路由未完成学习（learning_fail=true 显式要求判失败）：%s" % why
    return FACT_UNKNOWN, why


# --------------------------------------------------------------------------- #
# 7.3 在网/可达（10H-F4 已抄数 + 10H-F2 逐节点信号品质，两路独立证据交叉）
# --------------------------------------------------------------------------- #

#: 两路证据的可读名字（detail/证据里都用它，便于人工复核到底看的是哪一路）
EVIDENCE_ARCHIVED = "10H-F4 已抄从节点数量"
EVIDENCE_QUALITY = "10H-F2 信号品质>0 的节点数"


def _nodes_blank(detail: str) -> Dict[str, Any]:
    """两路证据都取不到时的空快照（键齐全，便于调用方 ``.get``）。"""
    return {
        "state": "unknown",
        "block_state": BLOCK_UNKNOWN,
        "online": None,
        "archive_total": None,
        "channel_a": {"available": False, "value": None, "detail": ""},
        "channel_b": {
            "available": False, "value": None, "scanned": 0, "unknown_signals": 0, "detail": ""
        },
        "detail": detail,
        "job_ids": [],
        "route": {},
        "table": {},
    }


def nodes_online_snapshot(
    *,
    cap: Any = None,
    ctx: Any = None,
    count: Any = DEFAULT_NODES_ONLINE_COUNT,
    max_pages: Any = DEFAULT_MAX_PAGES,
    expect_timeout: Any = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """两路**只读**证据交叉判「在网/可达」节点数（10H-F4 + 10H-F2）。

    为什么不用 10H-F1：``从节点总数量`` 是**路由档案中的从节点总数**（档案条数），
    档案里有 12 条、``已抄=0``、逐节点信号品质全 0 时它照样是 12 ——
    拿它判「组网通」就是假 PASS（真实台架已复现）。

    证据：

    - **A** ``10H-F4`` 的 ``已抄从节点数量``（= 已成功抄读的从节点数）：> 0 才说明确有节点抄通；
    - **B** ``10H-F2`` 逐节点 ``信号品质``（``desc`` 里 ``信号品质=N``，D7~D4 侦听信号品质，
      0=无信号）：统计 ``信号品质 > 0`` 的节点数。

    返回：:

        {"state": "answered"|"unknown"|...,
         "block_state": ..., "online": int|None,
         "archive_total": int|None,          # 档案数，仅用于说明，不参与在网判定
         "channel_a": {"available", "value", "detail"},
         "channel_b": {"available", "value", "scanned", "unknown_signals", "detail"},
         "detail": "证据 A：…；证据 B：…", "job_ids": [...], "route": {...}, "table": {...}}

    ``state == "answered"`` 表示两路里**至少一路**给出了可判定的读数；
    ``online`` 取可得读数中的较大者（每一路都是「至少这么多」的下界）。
    """
    capability, injected = (cap, True) if cap is not None else resolve_simcon(ctx)
    gate = capability_gate(capability, injected)
    if gate is not None:
        return _nodes_blank(gate.detail)

    # 证据 A：10H-F4「已抄从节点数量」
    route = route_snapshot(cap=capability, expect_timeout=expect_timeout)
    job_ids = list(route.get("job_ids") or [])
    channel_a: Dict[str, Any] = {"available": False, "value": None, "detail": ""}
    if str(route.get("state") or "") == "answered":
        answered_value = route.get("answered_total")
        if isinstance(answered_value, int):
            channel_a = {
                "available": True,
                "value": answered_value,
                "detail": "%s=%d" % (EVIDENCE_ARCHIVED, answered_value),
            }
        else:
            channel_a["detail"] = (
                "10H-F4 应答案里取不到「%s」（工作状态=%s）"
                % (ITEM_ANSWERED_TOTAL, route.get("work_state") or "缺失")
            )
    else:
        channel_a["detail"] = "10H-F4 路由运行状态查询未得到可判定应答（%s）：%s" % (
            route.get("state") or "unknown",
            route.get("detail") or "",
        )

    # 证据 B：10H-F2 逐节点「信号品质」
    table = archive_snapshot(
        cap=capability,
        count=count,
        max_pages=max_pages,
        expect_timeout=expect_timeout,
    )
    job_ids.extend(table.get("job_ids") or [])
    channel_b: Dict[str, Any] = {
        "available": False, "value": None, "scanned": 0, "unknown_signals": 0, "detail": ""
    }
    archive_total = table.get("total") if isinstance(table.get("total"), int) else None
    if str(table.get("state") or "") == "answered":
        nodes = [node for node in (table.get("nodes") or []) if node.get("addr")]
        signals = [node.get("signal") for node in nodes]
        known = [value for value in signals if isinstance(value, int)]
        unknown = len(signals) - len(known)
        positive = sum(1 for value in known if value > 0)
        channel_b.update(scanned=len(signals), unknown_signals=unknown, value=positive)
        if positive > 0 or (signals and unknown == 0) or (not signals and archive_total == 0):
            channel_b["available"] = True
            channel_b["detail"] = (
                "10H-F2 扫到 %d 条节点记录（档案数=%s），信号品质>0 的节点 %d 个"
                % (
                    len(signals),
                    archive_total if archive_total is not None else "缺失",
                    positive,
                )
            )
        elif not signals:
            channel_b["detail"] = (
                "10H-F2 应答里一条节点记录都没有（档案数=%s），无法据信号品质判定"
                % (archive_total if archive_total is not None else "缺失")
            )
        else:
            channel_b["detail"] = (
                "10H-F2 有 %d 条节点记录，但其中 %d 条取不到「%s」，"
                "不能据此判「一个都不通」" % (len(signals), unknown, ITEM_SIGNAL_QUALITY)
            )
    else:
        channel_b["detail"] = "10H-F2 从节点信息查询未得到可判定应答（%s）：%s" % (
            table.get("state") or "unknown",
            table.get("detail") or "",
        )

    readings = [
        int(channel["value"])
        for channel in (channel_a, channel_b)
        if channel.get("available") and isinstance(channel.get("value"), int)
    ]
    answered = bool(readings)
    detail = "证据 A：%s；证据 B：%s" % (
        channel_a.get("detail") or "取不到",
        channel_b.get("detail") or "取不到",
    )
    if not answered:
        detail = "两路证据都取不到读数，无法判定在网/可达：" + detail
    return {
        "state": "answered" if answered else "unknown",
        "block_state": BLOCK_PASS if answered else BLOCK_UNKNOWN,
        "online": max(readings) if readings else None,
        "archive_total": archive_total,
        "channel_a": channel_a,
        "channel_b": channel_b,
        "detail": detail,
        "job_ids": job_ids,
        "route": route,
        "table": table,
    }


def _archive_side_note(snapshot: Mapping[str, Any]) -> str:
    """在网判定的「旁证」后缀：档案数只作说明，**不参与**在网判定。"""
    total = snapshot.get("archive_total") if isinstance(snapshot, Mapping) else None
    if isinstance(total, int):
        return "（旁证：%s=%d，档案数不参与本判定）" % (ITEM_TOTAL, total)
    return ""


def judge_nodes_online(
    snapshot: Mapping[str, Any], minimum: Any = DEFAULT_MIN_ONLINE
) -> Tuple[str, str]:
    """在网/可达节点数是否 ≥ ``minimum``（三态；块与事实共用同一份判定）。

    口径（领导裁决）：

    - A（已抄）、B（信号品质>0）**任一 > 0** → ``yes``，detail 写清两路各自读数；
    - A、B **都明确为 0** → ``no``（档案有节点但一个都不通 —— 防假 PASS 的核心判据）；
    - 任一路取不到 → 只用可得那路；两路都取不到 → ``unknown``；
    - **绝不用**「从节点总数量」（档案数）推断在网。
    """
    limit = int_of(minimum)
    if limit is None or limit < 0:
        return FACT_UNKNOWN, "期望在网节点数下限 min 非法（需 ≥0 的整数）：%r" % (minimum,)
    if not isinstance(snapshot, Mapping):
        return FACT_UNKNOWN, "在网快照缺失，无法判定"
    if str(snapshot.get("state") or "") != "answered":
        return FACT_UNKNOWN, "%s" % (snapshot.get("detail") or "在网两路证据都取不到，无法判定")

    channel_a = snapshot.get("channel_a") or {}
    channel_b = snapshot.get("channel_b") or {}
    pairs: List[Tuple[str, int]] = []
    if channel_a.get("available") and isinstance(channel_a.get("value"), int):
        pairs.append((EVIDENCE_ARCHIVED, int(channel_a["value"])))
    if channel_b.get("available") and isinstance(channel_b.get("value"), int):
        pairs.append((EVIDENCE_QUALITY, int(channel_b["value"])))
    if not pairs:
        return FACT_UNKNOWN, "两路证据都取不到读数，无法判定在网/可达：%s" % (
            snapshot.get("detail") or ""
        )

    readings = "；".join("%s=%d" % pair for pair in pairs)
    label, best = max(pairs, key=lambda pair: pair[1])
    side = _archive_side_note(snapshot)
    if best == 0:
        if len(pairs) == 2:
            return FACT_NO, (
                "两路证据都明确为 0（%s）：档案里有节点但一个都不通"
                " —— 已抄=0 且没有任何节点的信号品质>0%s" % (readings, side)
            )
        return FACT_NO, (
            "可得的唯一一路证据明确为 0（%s）：另一路取不到，"
            "但这一路已说明没有任何节点被抄通/收到信号%s" % (readings, side)
        )
    if best >= limit:
        return FACT_YES, "%s（两路读数：%s；取较大者 %d ≥ 期望下限 %d%s）" % (
            label,
            readings,
            best,
            limit,
            side,
        )
    return FACT_NO, "%s（两路读数：%s；最大 %d < 期望下限 %d%s）" % (
        label,
        readings,
        best,
        limit,
        side,
    )


# --------------------------------------------------------------------------- #
# 8. 台架能力解析与就绪判定（simeter / simcon / module）
# --------------------------------------------------------------------------- #


def simeter_capability(params: Optional[Dict[str, Any]] = None,
                       ctx: Optional[RunContext] = None) -> SimeterCapability:
    """取模拟电表能力实例：优先 ``ctx.extras['simeter']``，否则按 params 构造。"""
    if ctx is not None:
        injected = ctx.get(EXTRAS_SIMETER)
        if injected is not None:
            return injected  # type: ignore[return-value]
    raw = dict(params or {})
    return SimeterCapability(
        raw.get("base_url"),
        timeout=_float_or(raw.get("timeout"), 10.0),
        probe_timeout=_float_or(raw.get("probe_timeout"), 5.0),
        retries=_int_or(raw.get("retries"), 1),
        retry_backoff=_float_or(raw.get("retry_backoff"), 0.0),
        mapping_id=str(raw.get("mapping_id") or DEFAULT_SIMETER_MAPPING),
        transport=raw.get("transport"),
        env_var=raw.get("env_var", "WORKBENCH_BASE"),
    )


def simcon_capability(params: Optional[Dict[str, Any]] = None,
                      ctx: Optional[RunContext] = None) -> SimconCapability:
    """取模拟集中器能力实例：优先 ``ctx.extras['simcon']``，否则按 params 构造。"""
    if ctx is not None:
        injected = ctx.get(EXTRAS_SIMCON)
        if injected is not None:
            return injected  # type: ignore[return-value]
    raw = dict(params or {})
    return SimconCapability(
        raw.get("base_url"),
        timeout=_float_or(raw.get("timeout"), 10.0),
        probe_timeout=_float_or(raw.get("probe_timeout"), 5.0),
        retries=_int_or(raw.get("retries"), 1),
        retry_backoff=_float_or(raw.get("retry_backoff"), 0.0),
        mapping_id=str(raw.get("mapping_id") or DEFAULT_SIMCON_MAPPING),
        transport=raw.get("transport"),
        env_var=raw.get("env_var", "WORKBENCH_BASE"),
    )


def module_capability(params: Optional[Dict[str, Any]] = None,
                      ctx: Optional[RunContext] = None) -> ModuleCapability:
    """取模块串口能力实例：优先 ``ctx.extras['module']``，否则按 params 构造。"""
    if ctx is not None:
        injected = ctx.get(EXTRAS_MODULE)
        if injected is not None:
            return injected  # type: ignore[return-value]
    raw = dict(params or {})
    return ModuleCapability(
        raw.get("base_url"),
        timeout=_float_or(raw.get("timeout"), 10.0),
        probe_timeout=_float_or(raw.get("probe_timeout"), 5.0),
        retries=_int_or(raw.get("retries"), 1),
        retry_backoff=_float_or(raw.get("retry_backoff"), 0.0),
        poll_wait_seconds=_float_or(raw.get("poll_wait_seconds"), 5.0),
        poll_interval=_float_or(raw.get("poll_interval"), 0.5),
        max_poll_seconds=_float_or(raw.get("max_poll_seconds"), 60.0),
        mapping_id=str(raw.get("mapping_id") or DEFAULT_MODULE_MAPPING),
        transport=raw.get("transport"),
        env_var=raw.get("env_var", "WORKBENCH_BASE"),
    )


def _float_or(value: Any, default: float) -> float:
    """能力构造用的浮点归一（非负；非法 → ``default``）。"""
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return default


def _int_or(value: Any, default: int) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


def _unknown_from_error(kind: str, status: Dict[str, Any]) -> FactValue:
    """能力层 ``status()`` 的错误 → 事实三态。

    - 404（子应用未挂载）= 明确「没有」→ ``no``；
    - 其余（连不上 / 没注入 base_url / 传输异常）= 「不知道」→ ``unknown``。
    """
    code = str(status.get("error") or "unknown")
    detail = str(status.get("detail") or "")
    reason = f"{kind} 无法探测（{code}）：{detail}" if detail else f"{kind} 无法探测（{code}）"
    if code in ABSENT_ERRORS:
        return FactValue(
            state=FACT_NO,
            detail="工作台可达但 %s 子应用未挂载（HTTP 404）：%s" % (kind, detail),
            evidence=[f"HTTP 404: {detail}".strip()],
        )
    return FactValue(state=FACT_UNKNOWN, detail=reason)


def _simeter_status_snapshot(capability: SimeterCapability) -> Dict[str, Any]:
    """``capability.status()`` 的安全快照（判定/开口门共用的唯一一次取值口径）。

    能力层承诺不抛异常，但注入的替身/半成品适配器可能抛 —— 这里收敛成
    ``{"ok": False, "error": "probe_error", ...}``，让上层永远拿到字典。
    """
    try:
        status = capability.status()
    except Exception as exc:  # noqa: BLE001 - 判定链路不抛异常
        return {"ok": False, "error": SIMETER_PROBE_ERROR,
                "detail": f"{SIMETER_PROBE_ERROR_TEXT}：{type(exc).__name__}: {exc}"}
    if not isinstance(status, Mapping):
        return {"ok": False, "error": "bad_response",
                "detail": f"simeter status() 返回非字典：{status!r}"}
    return dict(status)


def _simeter_error_text(where: str, code: str, detail: Any) -> str:
    """错误分层后的可读原因（detail 空也要点名是哪个动作、哪个错误码）。"""
    text = str(detail or "").strip()
    return f"{where} 失败（{code}）：{text}" if text else f"{where} 失败（{code}）"


def _simeter_open_failure_outcome(code: str) -> str:
    """``open_port`` 失败码 → 开口门结论（§10.13 裁决口径）。

    - ``http_409``（串口被占/资源冲突）→ ``conflict`` → 块 ``skipped``（人等：先放掉占用方）
    - ``http_404``（子应用未挂载）/ 服务不可达 → ``absent`` → 块 ``skipped``（台架缺失不是设备失败）
    - 其余 4xx/5xx、``bad_response`` / ``transport_error`` → ``unknown``（拿不到结论，绝不折算 pass）
    """
    if code == "http_409":
        return SIMETER_SESSION_CONFLICT
    if code in SIMETER_ABSENT_ERRORS:
        return SIMETER_SESSION_ABSENT
    return SIMETER_SESSION_UNKNOWN


def _open_simeter_port(capability: SimeterCapability, port: str) -> Dict[str, Any]:
    """调 ``capability.open_port(mapping_id=port)``（不抛异常；缺方法也报 ``unsupported``）。

    ``port`` 是**逻辑口别名（mapping_id）**，不是 COM 号 —— 物理串口由工作台的
    端口映射决定，本层只按名开，绝不写死 COM（REQS §10.10 事实 L）。
    """
    opener = getattr(capability, "open_port", None)
    if not callable(opener):
        return {"ok": False, "error": "unsupported",
                "detail": f"能力实例 {type(capability).__name__} 不支持 open_port()"}
    try:
        result = opener(mapping_id=port)
    except Exception as exc:  # noqa: BLE001 - 适配器承诺不抛，替身可能抛
        return {"ok": False, "error": "transport_error",
                "detail": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, Mapping):
        return {"ok": False, "error": "bad_response",
                "detail": f"open_port() 返回非字典：{result!r}"}
    return dict(result)


def _simeter_conflict_tail(healed: Sequence[Mapping[str, Any]]) -> str:
    """端口占用分支的收尾说明（区分「已抢占一次仍失败」/「没有 close 能力」/「没自愈」）。"""
    results = [str(item.get("result") or "") for item in healed or ()]
    if SELF_HEAL_FAILED in results:
        return ("已抢占一次仍失败（close_port 释放后重试仍被占用），交上层按台架不具备处理；"
                "按 D2-7 不还原占用方会话")
    if SELF_HEAL_UNAVAILABLE in results:
        return "没有可用的 close_port 能力，未自愈；需人工释放占用方"
    if SELF_HEAL_HEALED in results:
        return "已 close_port 抢占释放后重试成功"
    return "表口开不了，需先释放占用方"


def ensure_simeter_session(capability: SimeterCapability, *,
                           mapping_id: str = DEFAULT_SIMETER_PORT,
                           ensure_open: bool = DEFAULT_ENSURE_OPEN) -> Dict[str, Any]:
    """**开口门**：让模拟电表表口在链路上真的「张着嘴」（REQS-0041 台架联调修复）。

    为什么需要它：simeter 只有 ``open_port()`` 之后才会自动应答 STA 表口串行口发来的
    645 请求；只探测 ``status()`` 的旧口径会给出「服务可达就算在线」，而真实链路里
    表口没开 = 没人应答 = 抄表必挂。本函数只负责**开口**与**失败分层**，
    「开没开成 → 就绪与否」一律交给 :func:`judge_simeter`
    （``require_serial_open=True`` 严格口径），判定逻辑仍然只有一份（FT-2）。

    流程：``status()`` →（``serial_open`` 已真 → 沿用）→ ``open_port(mapping_id)``
    → **再** ``status()`` 复核。**成功路径不关端口**：表口要一直在线给后续
    ``meter.*`` 块用，开口方（本块）不拥有关闭时机。**唯一**会调 ``close_port()``
    的场合是「open 撞 409 端口被占用」的自愈：按 §3.4 / D2-7 不判归属，先抢占释放
    再重试**一次**（仍失败才交上层），过程留痕在返回值的 ``self_healed``。

    返回字典（块按 ``outcome`` 给态，不在这里直接决定 pass/fail）::

        {"outcome": <SIMETER_SESSION_*>, "detail": str, "port": str,
         "opened": bool, "ensure_open": bool, "error": str|None,
         "status": dict|None,        # 最后一次可信的 status() 快照（供 judge 复用）
         "self_healed": [ {action, target, result, detail}, ... ]}  # SH-2 自愈留痕
    """
    port = str(mapping_id or DEFAULT_SIMETER_PORT)
    gate: Dict[str, Any] = {
        "outcome": SIMETER_SESSION_UNKNOWN, "detail": "", "port": port,
        "opened": False, "ensure_open": bool(ensure_open), "error": None, "status": None,
        "self_healed": [],
    }

    status = _simeter_status_snapshot(capability)
    gate["status"] = status
    if not status.get("ok"):
        code = str(status.get("error") or "unknown")
        gate["error"] = code
        gate["outcome"] = (SIMETER_SESSION_ABSENT if code in SIMETER_ABSENT_ERRORS
                           else SIMETER_SESSION_UNKNOWN)
        gate["detail"] = _simeter_error_text("simeter status 探测", code, status.get("detail"))
        return gate

    if status.get("serial_open"):
        gate["outcome"] = SIMETER_SESSION_REUSED
        gate["opened"] = True
        gate["detail"] = ("表口已打开（沿用已开口：mapping_id=%s port=%s）；"
                          "端口保持打开，供后续抄表块使用"
                          % (port, status.get("port") or "-"))
        return gate

    if not ensure_open:
        gate["outcome"] = SIMETER_SESSION_NOT_OPEN
        gate["detail"] = ("表口未打开（mapping_id=%s），且 ensure_open=false 不自动开口 —— "
                          "严格口径下这就是「未就绪」" % port)
        return gate

    # 自愈接入点 ②（simeter 端口占用）：open 撞 409 → close_port 释放 → 重试**一次**。
    # 口径见 §3.4 / D2-7：跑用例是主动行为，不判归属，直接抢占（关端口只走能力层接口）。
    opened, healed = with_self_heal(
        lambda: _open_simeter_port(capability, port),
        action="simeter.close_port",
        target="simeter:%s" % port,
        cap=capability,
        closer=capability_closer(capability, SIMETER_CLOSER_NAMES),
        retry=SELF_HEAL_RETRY,
    )
    gate["self_healed"] = healed
    if not opened.get("ok"):
        code = str(opened.get("error") or "unknown")
        gate["error"] = code
        gate["outcome"] = _simeter_open_failure_outcome(code)
        gate["detail"] = _simeter_error_text(f"open_port(mapping_id={port})", code,
                                             opened.get("detail"))
        if gate["outcome"] == SIMETER_SESSION_CONFLICT:
            gate["detail"] = ("端口被占用：%s —— %s"
                              % (gate["detail"], _simeter_conflict_tail(healed)))
        return gate

    recheck = _simeter_status_snapshot(capability)
    gate["status"] = recheck
    if not recheck.get("ok"):
        code = str(recheck.get("error") or "unknown")
        gate["error"] = code
        gate["outcome"] = SIMETER_SESSION_UNKNOWN
        gate["detail"] = ("open_port(mapping_id=%s) 返回成功，但复核 status 失败，"
                          "无法确认表口真的开着：%s"
                          % (port, _simeter_error_text("status 复核", code,
                                                       recheck.get("detail"))))
        return gate

    if not recheck.get("serial_open"):
        gate["outcome"] = SIMETER_SESSION_NOT_OPEN
        gate["detail"] = ("open_port(mapping_id=%s) 返回成功，但复核 serial_open=false —— "
                          "不能因为 open 返回 ok 就判就绪" % port)
        return gate

    gate["outcome"] = SIMETER_SESSION_OPENED
    gate["opened"] = True
    preempt = ""
    if any(str(item.get("result") or "") == SELF_HEAL_HEALED for item in healed):
        preempt = "；先前端口被占用，已 close_port 抢占释放（未还原占用方会话）后重试成功"
    gate["detail"] = ("已打开表口（本次 open_port：mapping_id=%s port=%s）%s；"
                      "端口保持打开，供后续抄表块使用"
                      % (port, recheck.get("port") or "-", preempt))
    return gate


def judge_simeter(capability: SimeterCapability, *,
                  require_serial_open: bool = False,
                  status: Optional[Mapping[str, Any]] = None) -> FactValue:
    """模拟电表台架判定（唯一一份）。

    - 状态可读 → ``yes``（detail 带地址/版本/串口，串口未开时额外提示）
    - ``require_serial_open=True`` 且表口未打开 → ``no``（「会话不在线」严格口径）
    - 工作台 404（子应用未挂载）→ ``no``
    - 连不上 / 未注入 base_url / 传输异常 → ``unknown``

    ``status``：调用方已经探测过时的快照（如 :func:`ensure_simeter_session` 的复核结果），
    传了就**不重复发 HTTP**；不传则自己探一次。判定口径与错误分层完全一致。
    """
    snapshot = (dict(status) if isinstance(status, Mapping)
                else _simeter_status_snapshot(capability))
    if not snapshot.get("ok"):
        if snapshot.get("error") == SIMETER_PROBE_ERROR:
            return FactValue(state=FACT_UNKNOWN,
                             detail=str(snapshot.get("detail") or "simeter 探测异常"))
        return _unknown_from_error("simeter", snapshot)
    serial_open = bool(snapshot.get("serial_open"))
    detail = "simeter 在线（addr=%s version=%s 串口=%s）" % (
        snapshot.get("addr") or "-", snapshot.get("version") or "-",
        snapshot.get("port") if serial_open else "未打开")
    if require_serial_open and not serial_open:
        return FactValue(state=FACT_NO,
                         detail="simeter 可达但表口未打开（会话不在线）", evidence=[detail])
    return FactValue(state=FACT_YES, detail=detail)


def judge_simcon(capability: SimconCapability, *,
                 require_port_open: bool = False) -> FactValue:
    """模拟集中器台架判定（唯一一份）。口径与 :func:`judge_simeter` 对齐。"""
    try:
        status = capability.status()
    except Exception as exc:  # noqa: BLE001
        return FactValue(state=FACT_UNKNOWN,
                         detail=f"simcon 探测异常：{type(exc).__name__}: {exc}")
    if not status.get("ok"):
        return _unknown_from_error("simcon", status)
    is_open = bool(status.get("open"))
    detail = "simcon 在线（串口=%s mapping_error=%s）" % (
        status.get("port") if is_open else "未打开", status.get("mapping_error") or "-")
    if require_port_open and not is_open:
        return FactValue(state=FACT_NO,
                         detail="simcon 可达但串口未打开（会话不在线）", evidence=[detail])
    return FactValue(state=FACT_YES, detail=detail)


def judge_sta_online(capability: ModuleCapability,
                     mapping_id: Optional[str] = None) -> FactValue:
    """STA 控制台口（默认 ``sta-main``）是否可用 —— 事实 ``bench.sta_online`` 的判定口径。

    **不新写判定逻辑（FT-2）**：直接复用 ``ModuleCapability.port_status(mapping_id)``
    （其内部走 ``GET /api/serial-bridge/status`` 的只读 preflight）。

    - 口可用 → ``yes``（detail 带 windows_com / errors）
    - 口明确不可用（桥里没有该 mapping / usable=false）→ ``no``
    - 桥状态读不到（没注入 base_url / 连不上 / 服务异常）→ ``unknown``
      —— 「读不到状态」不等于「口不可用」，更不能当 ``yes``。
    """
    target = str(mapping_id or getattr(capability, "mapping_id", "")
                 or DEFAULT_MODULE_MAPPING)
    port_status = getattr(capability, "port_status", None)
    if not callable(port_status):
        return FactValue(state=FACT_UNKNOWN,
                         detail="能力不支持 port_status，无法判定 STA 口 %r 是否可用" % target)
    try:
        status = port_status(target)
    except Exception as exc:  # noqa: BLE001 - 判定函数不抛异常
        return FactValue(state=FACT_UNKNOWN,
                         detail=f"STA 口 {target} 探测异常：{type(exc).__name__}: {exc}")
    if not isinstance(status, Mapping):
        return FactValue(state=FACT_UNKNOWN,
                         detail=f"port_status 返回非字典：{status!r}")
    if not status.get("ok"):
        return FactValue(
            state=FACT_UNKNOWN,
            detail="串口桥状态不可读（%s），无法判定 STA 口 %r 是否可用"
            % (status.get("detail") or "原因未给出", target),
        )
    usable = status.get("usable")
    errors = status.get("errors") or []
    if usable is True:
        return FactValue(
            state=FACT_YES,
            detail="STA 口 %s 可用（windows_com=%s，串口桥 preflight 通过）"
            % (target, status.get("windows_com") or "-"),
        )
    if usable is False:
        return FactValue(
            state=FACT_NO,
            detail="STA 口 %s 不可用：%s" % (target, errors or status.get("detail") or "原因未知"),
            evidence=list(errors),
        )
    return FactValue(
        state=FACT_UNKNOWN,
        detail="串口桥未给出 STA 口 %r 的可用性（usable=%r），无法判定" % (target, usable),
    )


# --------------------------------------------------------------------------- #
# 9. 自愈（REQS-0042 §3.4 / D2-7 / SH-1..3）
# --------------------------------------------------------------------------- #
#
# 用户口径（原文，SH-3 审查项）
# ----------------------------
# 「跑测试用例时，是主动调用跑的，这时候如果有占用，那应当以自动化测试为主，
#   不看归属，直接抢占为自己所用即可。」
#
# 因此本层**不判归属、不做时间猜**：遇到占用类错误就 close 释放，然后重试**一次**；
# 只有重试后仍被占用才放弃（块态仍是既有 ``skipped``，S3 阶段才由上层映射成
# ``blocked`` + 原因）。抢占后**不负责还原**对方的会话（D2-7）。
#
# 边界（SH-3 硬约束，代码审查项）
# ------------------------------
# 自愈**只做接口级释放与重试**（能力层的 ``close`` / ``close_port``），
# **禁止**任何进程级操作：不起子进程、不杀进程、不直调系统 shell，
# 不重启服务、不断电。那些属人工运维，不是自动化测试能做的事
# （三项禁令的静态守卫见 ``test_self_heal.py``：本目录必须零命中）。
#
# 可注入性：close 能力**不硬编码 HTTP**，一律从能力实例的方法取（或显式传入
# ``closer``，单测用假对象），见 :func:`capability_closer`。
# --------------------------------------------------------------------------- #


def is_conflict_error(result: Any) -> bool:
    """结果是否「占用类」错误 —— **只有这类**才允许自愈（§3.4）。

    - ``{"ok": False, "error": "http_409"}``：simcon 闸门忙 / simeter 端口冲突（后端同码）；
    - ``{"outcome": "conflict"}``：开口门的分层结论（等价于 409）；
    - 其它（``http_422`` 参数错 / ``unreachable`` / ``poll_timeout`` / ``bad_response`` …）
      **一律不动手**：自愈解决不了配置错，乱关端口只会掩盖问题。
    """
    if not isinstance(result, Mapping):
        return False
    if result.get("ok"):
        return False
    if str(result.get("error") or "") in CONFLICT_ERRORS:
        return True
    return str(result.get("outcome") or "") == SIMETER_SESSION_CONFLICT


def capability_closer(cap: Any, names: Sequence[str] = ("close", "close_port")) -> Optional[Any]:
    """从能力实例取 ``close`` 能力（返回可调用对象或 ``None``）。

    取不到 → ``None``：调用方据此判定「本类占用暂时不可自愈」，**绝不**自己拼 HTTP
    去关别人的端口（SH-3：只做接口级释放，且释放动作必须属于能力层）。
    """
    for name in names:
        method = getattr(cap, name, None)
        if callable(method):
            return method
    return None


def _close_names_text(names: Sequence[str]) -> str:
    return " / ".join("%s()" % name for name in names) or "close()"


def _close_once(closer: Any) -> Dict[str, Any]:
    """调一次 close 能力，把任何异常收敛成 ``{"ok": bool, ...}``（不抛）。"""
    if not callable(closer):
        return {"ok": False, "error": "unsupported", "detail": "没有可调用的 close 能力"}
    try:
        raw = closer()
    except Exception as exc:  # noqa: BLE001 - 适配器承诺不抛，替身/半成品可能抛
        return {"ok": False, "error": "close_error",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    if isinstance(raw, Mapping):
        result = dict(raw)
        # 能力契约是 ``{"ok": bool}``；没给 ok 说明调用本身没报错，按成功算
        result["ok"] = bool(result.get("ok", True))
        return result
    return {"ok": bool(raw), "detail": repr(raw)}


def _preempt_note(closed: Mapping[str, Any]) -> str:
    """从 close 应答里挖出「抢占了谁」+ 服务端原话（D2-7 要求事后可追溯）。"""
    found: Dict[str, Any] = {}
    sources: List[Mapping[str, Any]] = [closed]
    for key in ("raw", "status", "data"):
        nested = closed.get(key)
        if isinstance(nested, Mapping):
            sources.append(nested)
    for source in sources:
        for key in PREEMPT_KEYS:
            if key in found:
                continue
            value = source.get(key)
            if value in (None, "", False):
                continue
            found[key] = value
    parts = ["%s=%s" % (key, found[key]) for key in PREEMPT_KEYS if key in found]
    message = str(closed.get("detail") or closed.get("message") or "").strip()
    if message:
        parts.append("服务端应答：%s" % message)
    if not closed.get("ok"):
        code = str(closed.get("error") or "unknown")
        parts.append("close 未成功（%s）" % code)
    return "；".join(parts)


def _close_ok_text(closed: Mapping[str, Any]) -> str:
    """close 动作的可读描述（成功给抢占对象，失败给错误码）。"""
    note = _preempt_note(closed)
    if closed.get("ok"):
        return note or "close 返回 ok（应答里未指名占用方）"
    return note or ("close 失败（%s）" % (closed.get("error") or "unknown"))


def _attempt_error_text(result: Any) -> str:
    """把一次尝试的失败结果压成「码：detail」（原始错误要进留痕，不许吞掉）。"""
    if not isinstance(result, Mapping):
        return str(result)
    code = str(result.get("error") or result.get("outcome") or "unknown")
    detail = str(result.get("detail") or "").strip()
    return "%s：%s" % (code, detail) if detail else code


def _clamp_retry(retry: Any) -> int:
    """重试次数夹到 ``[0, SELF_HEAL_MAX_RETRY]`` —— SH-1「仅一次」，绝不放大成死循环。"""
    try:
        value = int(retry)
    except (TypeError, ValueError):
        value = SELF_HEAL_RETRY
    return max(0, min(value, SELF_HEAL_MAX_RETRY))


def _attempt_call(attempt: Any) -> Any:
    """跑一次尝试，异常收敛成 ``attempt_error``（自愈包装绝不把块打崩）。"""
    try:
        return attempt()
    except Exception as exc:  # noqa: BLE001 - 替身/半成品适配器都可能抛
        return {"ok": False, "error": "attempt_error",
                "detail": "%s: %s" % (type(exc).__name__, exc)}


def with_self_heal(
    attempt: Any,
    *,
    action: str,
    target: str,
    cap: Any = None,
    closer: Any = None,
    retry: int = SELF_HEAL_RETRY,
    conflict: Any = None,
) -> Tuple[Any, List[Dict[str, Any]]]:
    """**统一自愈包装**（§3.4 唯一一份实现，simcon / simeter 两类占用共用）。

    参数
    ----
    - ``attempt``：真正干活的零参可调用（``call_verify`` / ``open_port`` 的一次尝试），
      返回 ``{"ok": bool, "error": ...}`` 形态的字典；
    - ``action`` / ``target``：留痕字段（``simcon.close`` / ``simeter.close_port``，
      target 形如 ``simcon:simcon`` / ``simeter:simeter``）；
    - ``cap``：能力实例，没显式给 ``closer`` 时按 ``close`` / ``close_port`` 解析；
    - ``closer``：**可注入**的 close 能力（单测传假对象，绝不硬编码 HTTP）；
    - ``retry``：重试次数，夹到 ``[0, 1]``（SH-1：仅一次）；
    - ``conflict``：占用类判定，默认 :func:`is_conflict_error`。

    返回 ``(最后一次结果, 留痕列表)``：留痕每项 ``{action, target, result, detail}``，
    ``result`` ∈ ``healed`` / ``failed`` / ``unavailable``。同时写进执行器的
    自愈收集器（``core.executor.record_self_heal``），块本身不用改一行。

    行为（与用户口径逐字对应）
    -------------------------
    1. 第一次尝试不成功但不是占用类 → **原样返回**，一次 close 都不发、一次都不重试；
    2. 占用类 → close 释放（**不看占用方是谁**）→ 重试**一次**：
       - 重试成功 → ``healed``（块态照常由判据决定 pass/fail）；
       - 重试仍被占 → ``failed``（detail 带原始错误 + 「已抢占一次仍失败」，
         块态仍是既有 ``skipped``，交上层按台架不具备处理）；
    3. 没有可注入的 close 能力 → ``unavailable``（不猜、不硬编码 HTTP，直接交上层）。
    """
    is_conflict = conflict if callable(conflict) else is_conflict_error
    limit = _clamp_retry(retry)
    first = _attempt_call(attempt)
    if limit <= 0 or not is_conflict(first):
        return first, []

    close_fn = closer if closer is not None else capability_closer(cap)
    if not callable(close_fn):
        record = self_heal_entry(
            action, target, SELF_HEAL_UNAVAILABLE,
            "遇到占用（原始错误：%s），但能力实例 %s 没有可用的 %s，无法自愈；"
            "不猜、不硬编码 HTTP、不做进程级操作（SH-3），按占用交上层"
            % (_attempt_error_text(first),
               type(cap).__name__ if cap is not None else "（未注入）",
               _close_names_text(SIMCON_CLOSER_NAMES + SIMETER_CLOSER_NAMES)),
        )
        record_self_heal(record)
        return first, [record]

    original = _attempt_error_text(first)
    closed = _close_once(close_fn)
    close_text = _close_ok_text(closed)
    last = first
    healed = False
    for _ in range(limit):
        last = _attempt_call(attempt)
        if not is_conflict(last):
            healed = True
            break

    if healed:
        detail = (
            "遇到占用（原始错误：%s）→ 已 close 抢占释放（%s）→ 重试 1 次成功；"
            "按 D2-7 不还原占用方会话" % (original, close_text)
        )
        record = self_heal_entry(action, target, SELF_HEAL_HEALED, detail)
    else:
        detail = (
            "遇到占用（原始错误：%s）→ 已抢占一次仍失败（close：%s；重试 1 次后仍被占用：%s）；"
            "不再重试，交上层按台架不具备处理（S3 映射为 blocked）"
            % (original, close_text, _attempt_error_text(last))
        )
        record = self_heal_entry(action, target, SELF_HEAL_FAILED, detail)
    record_self_heal(record)
    return last, [record]


_SELF_HEAL_NOTE_TEXT = {
    SELF_HEAL_HEALED: "已抢占释放并重试成功",
    SELF_HEAL_FAILED: "已抢占一次仍失败",
    SELF_HEAL_UNAVAILABLE: "无可用的 close 能力，未自愈",
}


def self_heal_note(result: Any) -> str:
    """把结果里带的自愈留痕压成一句**简短**后缀（全文在结构化留痕里，不在这里灌水）。"""
    if not isinstance(result, Mapping):
        return ""
    healed = result.get(SELF_HEALED_KEY)
    if not isinstance(healed, (list, tuple)) or not healed:
        return ""
    parts: List[str] = []
    for item in healed:
        if not isinstance(item, Mapping):
            continue
        code = str(item.get("result") or "")
        parts.append("%s→%s（%s）" % (item.get("action") or "close", code,
                                      _SELF_HEAL_NOTE_TEXT.get(code, "详情见 self_healed")))
    return "；自愈记录：" + "；".join(parts) if parts else ""


__all__ = [
    # 0. 地址角色化（0042 D2-1：换算只此一处，块内不得再写第二份反转）
    "archive_addr_of",
    "meter_addr_wire",
    # 帧口径
    "AFN_CONFIRM",
    "FN_CONFIRM",
    "FN_DENY",
    "AFN_ROUTE_QUERY",
    "FN_ARCHIVE_QUERY",
    "FN_ONNET_COUNT",
    "FN_ROUTE_STATUS",
    "ADDR_LEN",
    "COUNT_MAX",
    "DEFAULT_TIMEOUT",
    "DEFAULT_QUERY_COUNT",
    "DEFAULT_MAX_PAGES",
    "DECL_KEYS",
    "SIMCON_KEYS",
    "EXTRAS_SIMETER",
    "EXTRAS_SIMCON",
    "EXTRAS_MODULE",
    "DEFAULT_SIMETER_MAPPING",
    "DEFAULT_SIMCON_MAPPING",
    "DEFAULT_MODULE_MAPPING",
    "BUSY_ERRORS",
    "DOWN_ERRORS",
    "CONFIG_ERRORS",
    "ABSENT_ERRORS",
    "DEFAULT_SIMETER_PORT",
    "DEFAULT_ENSURE_OPEN",
    "SIMETER_ABSENT_ERRORS",
    "SIMETER_PROBE_ERROR",
    "SIMETER_PROBE_ERROR_TEXT",
    "SIMETER_SESSION_REUSED",
    "SIMETER_SESSION_OPENED",
    "SIMETER_SESSION_NOT_OPEN",
    "SIMETER_SESSION_CONFLICT",
    "SIMETER_SESSION_ABSENT",
    "SIMETER_SESSION_UNKNOWN",
    "SIMETER_SESSION_OPEN_OK",
    "ITEM_TOTAL",
    "ITEM_ANSWERED",
    "ITEM_ERROR",
    "ADDR_ITEM_PREFIX",
    "ADDR_ITEM_SUFFIX",
    "ITEM_WORK_STATE",
    "ITEM_CURRENT_STATE",
    "ITEM_SWITCH",
    "ITEM_RUN_WORD",
    "ITEM_ANSWERED_TOTAL",
    "ITEM_RELAY_ANSWERED_TOTAL",
    "NODE_ITEM_PREFIX",
    "NODE_ITEM_SUFFIX",
    "ITEM_SIGNAL_QUALITY",
    "ARCHIVE_COUNT_NOTE",
    "WORK_RUNNING",
    "WORK_LEARNING",
    "RUN_WORD_ROUTE_DONE_BIT",
    "SWITCH_WORK_STATE_BIT",
    "SWITCH_CURRENT_STATE_SHIFT",
    "ROUTE_RUNNING",
    "ROUTE_LEARNING_INCONCLUSIVE",
    "ROUTE_LEARNING_INCOMPLETE",
    "ROUTE_UNKNOWN",
    "DEFAULT_MIN_ONLINE",
    "DEFAULT_NODES_ONLINE_COUNT",
    # 1. 记法归一
    "int_of",
    "afn_int",
    "fn_int",
    "normalize_addr",
    "items_of",
    "item_value",
    "field_raw",
    "afn_fn_of",
    "coerce_float",
    "coerce_int",
    # 2. ctx / 参数
    "block_action_params",
    "extras_of",
    "flag_of",
    # 3. 能力解析与门
    "resolve_simcon",
    "capability_gate",
    "require_target",
    # 4. verify 归一
    "call_verify",
    "verify_failure_state",
    "steps_of",
    "journal_parsed",
    "snapshot_kind",
    # 5. 单步
    "send_step",
    "judge_step",
    "judge_confirm_step",
    # 6. 档案表
    "page_table",
    "clamp_count",
    "clamp_pages",
    "archive_snapshot",
    "judge_contains",
    # 7. 组网（档案数 / 路由状态 / 在网可达）
    "archive_count_snapshot",
    "judge_archive_count_min",
    "onnet_snapshot",          # 兼容别名（判的仍是档案数）
    "judge_onnet_min",         # 兼容别名
    "route_status_fields",
    "classify_route_state",
    "route_snapshot",
    "judge_route_status",
    "judge_route_running",
    "nodes_online_snapshot",
    "judge_nodes_online",
    "page_signal_quality",
    "node_index_of",
    "EVIDENCE_ARCHIVED",
    "EVIDENCE_QUALITY",
    # 8. 台架
    "simeter_capability",
    "simcon_capability",
    "module_capability",
    "ensure_simeter_session",
    "judge_simeter",
    "judge_simcon",
    "judge_sta_online",
    # 9. 自愈（§3.4 / D2-7 / SH-1..3）
    "with_self_heal",
    "is_conflict_error",
    "capability_closer",
    "self_heal_note",
    "SELF_HEAL_MAX_RETRY",
    "SELF_HEAL_RETRY",
    "SELF_HEAL_HEALED",
    "SELF_HEAL_FAILED",
    "SELF_HEAL_UNAVAILABLE",
    "CONFLICT_ERRORS",
    "SIMCON_CLOSER_NAMES",
    "SIMETER_CLOSER_NAMES",
]
