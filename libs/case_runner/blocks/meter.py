"""抄表类原子块（REQS-0041 §10.5 BL-4）：``meter.addr_probe`` / ``meter.read_645`` /
``meter.brain_local``。

三条**帧级**判据（判据函数在本文件定义，事实层 ``facts_meter.py`` 复用同一份逻辑，
不另写 —— REQS FT-2）：

- ``meter.addr_probe``：02H-F1 数据转发透传 645「读通信地址」（控制码 ``13H``）；
  上行 02H-F1 内嵌 645 的**地址域 == 期望表地址** ⇒ pass
  （REQS §10.7 事实 E 实测：``6821008300...1416`` 可递归解出 ``地址域 = 000000000001``）。
  **请求帧地址域口径（REQS-0042，参数 ``addr_mode``）**：两套写法，判据完全一样（都比
  「**应答**回装的内层 645 地址域」）：

  * ``addr_mode="meter"``（**默认**）→ 地址域 = 该表 ``meter_addr`` 的 645 线序
    （``123456789012`` → ``68 12 34 56 78 90 12 68 13 00 CS 16``）。**本台 CCO 能过**
    （真机 4/4 PASS）。
  * ``addr_mode="broadcast"`` → 地址域全 FEH，即 DL/T645-2007 §6.3 的规范写法
    （``01_DLT645_帧格式.md:326-340``：``68 FE FE FE FE FE FE 68 13 00 CS 16``）。
    **本台实测不通用**：CCO 处理 ``02H-F1`` 透传时是从内层 645 报文里提表地址再单播，
    给全 FE 等于「无地址」→ 回**空数据单元**（``evidence/0042/s2-meter_read-13h-FE.log``：
    s1 fail「该帧没有内嵌帧（上行数据单元为空）」）。规范对照/换 CCO 时用这个开关。
- ``meter.read_645``：02H-F1 透传 645 读数据（控制码 ``11H``，DI 如 ``00010000``）；
  上行内层 645 **CS 合法 + DI 回显正确 + 数值可解** ⇒ pass；给了 ``expect_value`` 再比数值。
- ``meter.brain_local``：表端脑自检（simeter ``handle_hex``，不占串口）；
  正帧 ``reply_hex`` 非空 + ``note.value`` 存在 + DI 回显 ⇒ pass；
  反帧（地址不符）的判据是「**不应答**」：``reply_hex is None`` 且 ``note.mine is False`` ⇒ pass。

状态口径（REQS RU-3「绝不假 PASS」）：

======  ================================================================
pass    判据成立。
fail    收到应答但判据不成立（地址不符、内层无 645 嵌套、CS 坏、数值不可解、
        verify 步骤自己判定 fail、期望不应答却应答了）。
skipped 依赖未就绪（simcon/simeter 未配置、不可达、HTTP 404 无会话、HTTP 409 闸门忙）。
unknown 拿不到结论（job 轮询超时、无上行帧 hex、返回结构无法识别、4xx 配置错误）；
        **永不折算成 pass**。
======  ================================================================

等待型重试（REQS-0042 S2-F）
----------------------------
真机实测（2026-11-23）：**复位 STA 后链路恢复要十几分钟**（16:20 复位 → 16:45 才恢复到
2.5s；17:18 再复位 → 30 分钟后表口仍静默），而**读取本身只要 2.3~2.6s**。同一时刻
``nwk.topology`` 却能通过（CCO 节点计数残留）——「组网判定通过」≠「链路可用」。
因此 ``meter.addr_probe`` / ``meter.read_645`` 都带 ``wait_s`` / ``poll_interval_s``：
每 ``poll_interval_s`` **重发同一个 02H-F1 透传查询**，直到判定通过或等满 ``wait_s``。

- **缺省 ``wait_s=0``**：只探一次、不 sleep —— 与等待型改造前**逐字一致**（老用例、
  ``show``、``wait_s=0`` 的失败口径都不变）。
- **等待期只读**：循环里只重发同一个内嵌 645 帧，且由 :func:`readonly_violation` 闸门
  卡住控制码（只允许 11H 读数据 / 13H 读通信地址）；写帧 payload + ``wait_s>0`` 直接拒绝轮询。
- **三态在「重试」下的口径**（``wait_s>0`` 时）：

  ======================  ==========================================================
  等到 pass 判据成立       ``pass``（立即停；重试的目的就是等链路恢复）
  等满，全程**一帧都没有**  ``unknown``——说不清链路是否恢复，**不是 fail**
  等满，有应答且判据不成立  ``fail``（地址不符 / DI 不回显 / CS 坏……真失败，
                           **不被重试掩盖**，也不会被折算成 unknown）
  中途依赖不可用            ``skipped``（404/409/不可达：立即收工，不空转重试）
  ======================  ==========================================================

- **留痕**：``wait_s>0`` 时 detail 追加「（等了 N 秒，只读探测 K 次；单次最多等 S 秒）」，
  ``actual`` 是映射时并入``等待秒``/``探测次数``——``core.executor`` 的 ``poll_reading``
  正是按这两条派生 ``decisions[]`` 的 ``kind=wait`` / ``timeout``（**不改 core**）。

**单次尝试最大耗时 / 总预算**（``expect_timeout`` 与 ``wait_s`` 不叠乘）::

    单次尝试最大耗时 = expect_timeout                          # wait_s <= 0（旧口径）
    单次尝试最大耗时 = min(expect_timeout, 剩余预算)            # wait_s > 0
                       且下限 MIN_ATTEMPT_TIMEOUT_S（尾部也给一次有意义的窗口）
    总预算          ≈ wait_s + 最后一次尝试的物理往返（透传本身 2.3~2.6s，无法从时钟里省）

所以「``wait_s=120`` × ``expect_timeout=15`` = 每 5s 一次共 24 次、每次最多等 15s」这种
**不会**出现：剩余预算是从 ``wait_s`` 里扣的，末尾那几次的单次等待会被压到剩余预算，
总时长由 ``wait_s`` 兜住，而不是 ``attempts × expect_timeout``。取 ``t`` 的那一次
``03H-F7`` 查询（≤ :data:`TIMEOUT_QUERY_SECONDS` 秒）在预算之外，且同 run 只查一次。

判据函数 :func:`judge_meter_addr` / :func:`judge_read_645` 同时给**事实口径**
（``Verdict.state``）与**块口径**（``Verdict.block_state``）：唯一分歧是「上行帧收到了、
但内层没有 645 地址/数据证据」——事实判 ``unknown``（没探到 ≠ 不存在），块判 ``fail``
（回帧内容不符本身就是缺陷）。事实层 ``facts_meter.py`` 用前者，块用后者。

能力层注入（``ctx.extras``，缺省时按需自建，见 :func:`_capability`）：
``ctx.extras["simcon"]`` / ``ctx.extras["frames"]`` / ``ctx.extras["simeter"]``。

块参数（``action.params`` 给缺省，``ctx.block_params[block_id]`` 顶层键覆盖）：

- ``meter_addr``      表地址（12 位 BCD，**电表 645 地址域人读序**，如 ``123456789012``）；
                      缺省取 ``case_defaults.meter_addr``（REQS-0042 **D2-2**）
- ``protocol``        1376.2 数据单元「通信协议类型」：``2`` = DL/T 645-2007，``1`` = 645-1997
- ``payload``         直接给内嵌 645 请求帧 hex（不给则按 ``meter_addr``/``di`` 构帧）
- ``di``              645 读数据 DI（显示序 8 位 hex，缺省 ``00010000`` 正向有功总电能）
- ``addr_mode``       （``addr_probe``）13H 请求地址域口径：
                      ``"meter"``（**缺省**）= 该表 ``meter_addr`` 线序（本台可用）；
                      ``"broadcast"`` = 全 FEH 广播（645-2007 §6.3 规范写法，本台 CCO
                      对 ``02H-F1``+FE 不转发、回空帧）。判据两种模式相同：都比应答地址域
- ``expect_timeout``  等上行 02H-F1 应答的秒数；**显式给了才用它**（优先级最高）。
                      缺省走规范口径 :func:`resolve_expect_timeout`：先查 CCO 的
                      ``03H-F7``（从节点监控最大超时时间 ``t``）→ 等待 ``t+5s``；
                      取不到 ``t`` → 回落 ``60s``（口径见 :data:`DEFAULT_EXPECT_TIMEOUT`）
- ``query_max_timeout`` 是否发 ``03H-F7`` 取 ``t``（缺省 ``True``；同一次运行只查一次，
                      结果缓存在 ``ctx.extras``）
- ``max_timeout_provider`` 「取 t」可注入钩子（``callable``，返回 ``t`` 秒或 ``None``）；
                      给了就不发 ``03H-F7``（离线/单测用，不伪造解析）
- ``timeout_margin``  ``t`` 之后的余量秒数（缺省 ``5``，即规范口径 ``t+5s``）
- ``wait_s``          等待型重试的**总预算秒数**（缺省 ``0`` = 只探一次，行为与旧版逐字一致）。
                      复位 STA 后链路恢复要十几分钟，``meter_first_join`` 场景才按需给值；
                      单次尝试上限 = ``min(expect_timeout, 剩余预算)``，两者**不叠乘**
                      （口径见模块文档与 :func:`wait_attempts`）
- ``poll_interval_s`` 重试间隔秒数（缺省 ``5``；一次完整透传本身就要 2.3~2.6s）
- ``clock`` / ``sleep``  等待用的时钟/睡眠（``callable``，单测注入虚拟时钟 → 零真实等待）
- ``expect_value``    （可选）``meter.read_645`` 的期望数值
- ``request_hex``     （``brain_local``，**可选**）喂给表端脑的 645 帧 hex；
                      缺省/为空时按 ``meter_addr`` + ``di`` 用 :func:`build_645_read_frame`
                      动态构帧（显式给值仍以显式值为准）
- ``expect_reply``    （``brain_local``）是否期望应答（缺省 True；地址不符反帧传 False）

``meter.brain_local`` 的 ``meter_addr`` 与 ``meter.read_645`` 同口径（645 地址域线序，
人读 ``123456789012`` → 线序 ``12 34 56 78 90 12``，**不反转**；换算唯一实现处是
``blocks/common.py`` 的 :func:`meter_addr_wire`）。**不再有硬编码请求帧**：
旧常量 :data:`SELF_CHECK_REQUEST` 只对应 ``000000000001``，台架换地址即会误判 FAIL。

地址角色（REQS-0042 §3.1）：调用方只给 ``meter_addr``（电表 645 人读地址）；
**档案侧**的换算（``archive_addr_of``）由 ``archive.py`` 负责，本文件不碰档案地址。
"""

from __future__ import annotations

import inspect
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from case_runner.blocks.common import meter_addr_wire, normalize_addr
from case_runner.capabilities.frames import FramesCapability
from case_runner.capabilities.simeter import SimeterCapability
from case_runner.capabilities.simcon import SimconCapability
from case_runner.core.defaults import default_meter_addr
from case_runner.core.executor import PARAM_ASSERT, BlockResult
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_UNKNOWN,
    FACT_YES,
)
from case_runner.core.registry import has_block, register_block

# --------------------------------------------------------------------------- #
# 常量（协议口径集中在此，JSON 声明只引用语义名）
# --------------------------------------------------------------------------- #

#: 1376.2 数据单元「通信协议类型」：02H = DL/T 645-2007，01H = DL/T 645-1997
PROTOCOL_645_2007 = 2
PROTOCOL_645_1997 = 1

#: 外层帧（02H-F1）所属协议：门面里唯一支持「内嵌 645/698 递归」的协议名
OUTER_PROTOCOL = "1376.2"
INNER_PROTOCOL_645 = "645"

#: 645 控制码：13H = 读通信地址；11H = 读数据
CONTROL_READ_ADDR = "13"
CONTROL_READ_DATA = "11"

#: 表地址 / DI 的**离线兜底**（REQS §10.7 事实 E、645-2007 附录 A.2）
#: 注意 ``DEFAULT_ADDR`` 只给**纯函数构帧器**（``build_645_read_frame()`` 无参调用）和
#: 回归基准用；**块运行期不取它**——块一律走 :func:`meter_addr_of`
#: （入参 > 声明默认 > ``case_defaults.meter_addr``，见 REQS-0042 D2-2）。
DEFAULT_ADDR = "000000000001"
DEFAULT_DI = "00010000"

#: 表端脑自检参考帧（已实测：本表地址 000000000001 读 DI 00010000）
#: 仅作「旧地址帧」回归基准，**块运行期不再用它**（按 ``meter_addr`` 动态构造）
SELF_CHECK_REQUEST = "6800000000000168110433333433B316"
#: 地址不符的有效帧（CS 合法）：表端脑按「不应答 + note.mine=False」处理
SELF_CHECK_FOREIGN_REQUEST = "68111111111111681104333334331816"

#: 645「读通信地址」（13H）请求帧的**广播地址域**：全 FEH。
#: DL/T645-2007 §6.3 硬性规定「地址域全部为 FEH（广播方式读取）」——13H 请求帧
#: **不带表地址**，表自己的地址由 13H 应答回装（所以判据看应答、不看请求）。
#: 出处：``01_DLT645_帧格式.md`` §6.3（示例 ``68 FE FE FE FE FE FE 68 13 00 CS 16``）。
#: **但**：本台架 CCO 处理 ``02H-F1`` 透传时是「从内层 645 报文里提表地址再单播」，
#: 给它全 FE 等于「无地址」→ 回**空数据单元**（实测 0/1 pass，
#: ``evidence/0042/s2-meter_read-13h-FE.log`` s1 fail「该帧没有内嵌帧（上行数据单元为空）」），
#: 故 FE 只作为 ``addr_mode="broadcast"`` 的**显式**规范开关，默认走 ``"meter"``。
ADDR_BROADCAST = "FEFEFEFEFEFE"

#: 13H 请求地址域口径（REQS-0042 裁决：规范 vs 本台可用）
#: - ``"meter"``（**默认**）：地址域 = 该表 ``meter_addr`` 的 645 线序 → 本台 CCO 能单播转发；
#: - ``"broadcast"``：地址域 = 全 FEH（DL/T645-2007 §6.3 规范写法）→ 本台 CCO 回空帧，仅作规范对照。
ADDR_MODE_METER = "meter"
ADDR_MODE_BROADCAST = "broadcast"
ADDR_MODES = (ADDR_MODE_METER, ADDR_MODE_BROADCAST)
DEFAULT_ADDR_MODE = ADDR_MODE_METER

#: 1376.2 ``03H-F7``「读取从节点监控最大超时时间」——CCO 侧的抄表最大超时 ``t``
TIMEOUT_QUERY_AFN = "03"
TIMEOUT_QUERY_FN = 7
#: 03H-F7 应答里 ``t`` 的数据项名（``adapter_10376`` 的 ``afn==0x03 and fn==7`` 分支解析）
TIMEOUT_FIELD = "最大超时时间"
#: 03H-F7 **查询本身**的等待秒数：Q/GDW 10376.2 §7「集中器与通信单元直接交互命令
#: 响应时间：小于 6s（不含本地通信环节）」
TIMEOUT_QUERY_SECONDS = 6.0
#: 规范等待口径：每块表最大抄读时间 = ``t + 5s``
#: （用例库 ``CCO/台账/144`` 用例19 步骤6：下发 03H-F7 查 t，设置抄读每块表最大时间为 t+5 秒）
TIMEOUT_MARGIN_SECONDS = 5.0
#: 取不到 ``t`` 时的**回落**等待秒数（REQS-0042 D5-2）。
#: 口径：业务层「集中器↔CCO 并发超时 60s」（``业务层/45_附件一_HPLC高级功能0507_业务.md``
#: §5 高频采集）；旧值 8s 对透传抄表偏短（真机单条数据项 2.3~2.6s 才回，余量不足）。
DEFAULT_EXPECT_TIMEOUT = 60.0

#: 等待型重试（REQS-0042 S2-F）：``wait_s`` 缺省 **0** = 只探一次（旧行为逐字不变）
DEFAULT_WAIT_S = 0.0
#: 重试间隔缺省 5s：一次完整透传（02H-F1 下发 + 等上行）本身就要 2.3~2.6s，
#: 间隔再小只是白占串口；真机复位后链路恢复是分钟级，5s 粒度足够
DEFAULT_POLL_INTERVAL_S = 5.0
#: ``wait_s > 0`` 时**单次尝试**的最小等待秒数：预算尾部哪怕只剩 0.2s，也要给一次
#: 有意义的等应答窗口（否则最后一次必然「还没等就超时」，纯属空转）
MIN_ATTEMPT_TIMEOUT_S = 1.0

#: 等待期**只读**白名单（645 控制码）：11H 读数据 / 13H 读通信地址。
#: 等待循环只重发同一个透传 payload，写帧（12H 冻结、14H 写数据……）一律拒绝轮询。
READONLY_645_CONTROLS = frozenset({int(CONTROL_READ_ADDR, 16), int(CONTROL_READ_DATA, 16)})

#: 模块级时钟/睡眠（与 ``blocks/nwk.py`` 同一套注入约定）：单测
#: ``monkeypatch.setattr(meter, "_clock", ...)``，或经块参数 ``clock=`` / ``sleep=``
#: （callable）、能力对象上的 ``clock`` / ``sleeper`` 属性注入 → **零真实等待**。
_clock: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep

#: 内层 645 异常应答：控制码 bit6（D7=0,D6=1）标识「从站异常应答」
_645_EXCEPTION_BIT = 0x40

#: ``action`` 里属于「块元信息」而非块参数的键（其余顶层键视为参数覆盖）
ACTION_META_KEYS = frozenset(
    {"afn", "fn", "params", "recipe", "semantic", "cleanup", "assert", "tags"}
)

#: 期望应答字段名（门面 decode 的字段名，中文与原样一致）
FIELD_ADDR = "地址域"
FIELD_CONTROL = "控制码"
FIELD_CS = "校验和CS"
FIELD_FORWARDED = "转发报文"
FIELD_PROTOCOL_TYPE = "通信协议类型"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def norm_hex(value: Any) -> str:
    """hex 归一：去空白、大写（``68 21 00`` / ``0x6821`` → ``682100``）。"""
    text = str(value or "")
    text = "".join(text.split())
    if text[:2].lower() == "0x":
        text = text[2:]
    return text.upper()


def block_params(action: Mapping[str, Any]) -> Dict[str, Any]:
    """块入参归一：``action.params`` 作缺省，``action`` 顶层键（运行时覆盖）优先。

    声明里的说明性键（``_`` 开头，如 ``_params_note``）不是参数，跳过。
    """
    merged: Dict[str, Any] = {}
    nested = action.get("params")
    if isinstance(nested, Mapping):
        merged.update(nested)
    for key, value in action.items():
        if key in ACTION_META_KEYS or str(key).startswith("_"):
            continue
        merged[key] = value
    return merged


def meter_addr_of(
    action: Mapping[str, Any], *, allow_config_default: bool = True
) -> Tuple[str, str]:
    """解析本块要作用的**电表 645 表地址**：返回 ``(表地址, 非法入参说明)``。

    解析顺序（REQS-0042 **D2-1** / **D2-2**）：

    1. **入参** ``meter_addr``（``action.params`` 或运行时扁平覆盖）；
    2. **块声明 ``assert`` 里的 ``meter_addr``**——只在入参没给时兜底：``action`` 与
       ``assert`` 是声明 JSON 里两份手写字段，任一侧单独改了地址就会「请求一只表、
       判另一只表」。取 assert 侧的值，保证同一块内动作与判据作用于**同一只表**；
    3. **配置默认** ``case_defaults.meter_addr``（``allow_config_default`` 时）。

    **给了但不合法 ≠ 没给**：显式传 ``"12345"`` 之类垃圾地址绝不静默换成默认表地址
    （那会让「传错地址」变成「拿默认那只表去判」）——此时第二个返回值非空，块报配置错误。

    这里**不做**任何字节反转：645 帧地址域线序由 :func:`build_645_read_frame`
    经 ``common.meter_addr_wire``（唯一实现处）得出。
    """
    options = block_params(action) if isinstance(action, Mapping) else {}
    given = options.get("meter_addr")
    if given not in (None, ""):
        addr = normalize_addr(given)
        if addr:
            return addr, ""
        return "", repr(given)

    entries = action.get(PARAM_ASSERT) if isinstance(action, Mapping) else None
    if isinstance(entries, (list, tuple)):
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            asserted = normalize_addr(entry.get("meter_addr"))
            if asserted:
                return asserted, ""
    if allow_config_default:
        return normalize_addr(default_meter_addr()), ""
    return "", ""


def _bad_addr_result(bad: str, expected: Any = None) -> BlockResult:
    """表地址入参非法的统一块结果（配置错误 → ``unknown``，绝不当设备结论）。"""
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail="请求帧构帧失败（配置错误，非设备 FAIL）：645 地址非法：%s"
        "（需 12 位 BCD 数字，如 123456789012）" % bad,
        expected=expected,
    )


def _entries(frame: Any) -> list:
    if not isinstance(frame, Mapping):
        return []
    out = []
    for key in ("fields", "items"):
        value = frame.get(key)
        if isinstance(value, list):
            out.extend(entry for entry in value if isinstance(entry, Mapping))
    return out


def _field(frame: Any, name: str) -> Optional[Mapping[str, Any]]:
    for entry in _entries(frame):
        if str(entry.get("name") or "") == name:
            return entry
    return None


def _field_value(frame: Any, name: str) -> Any:
    entry = _field(frame, name)
    return None if entry is None else entry.get("value")


def _field_hex(frame: Any, name: str) -> str:
    entry = _field(frame, name)
    if entry is None:
        return ""
    return norm_hex(entry.get("hex") or entry.get("raw") or "")


def _field_names(frame: Any) -> list:
    return [str(e.get("name")) for e in _entries(frame) if e.get("name")]


def di_echo_hex(di: str) -> str:
    """645 读数据应答里 DI 回显的线上字节（DI 显示序 → 传输序，逐字节 +33H）。

    ``00010000`` → ``33333433``（与表端脑应答帧实测一致）。

    说明：这里的字节反序是 **645 数据标识 DI 自己的显示序→传输序**（4 字节 + 33H），
    与「表地址 / 档案地址」的 6 字节换算**不是同一件事**——地址换算的唯一实现处是
    ``blocks/common.py`` 的 ``archive_addr_of`` / ``meter_addr_wire``，本函数不碰地址。
    """
    text = norm_hex(di)
    if len(text) != 8 or any(char not in "0123456789ABCDEF" for char in text):
        return ""
    wire = bytes(reversed(bytes.fromhex(text)))
    return "".join("%02X" % ((byte + 0x33) & 0xFF) for byte in wire)


def _value_matches(actual: Any, expected: Any, tol: float = 1e-6) -> bool:
    try:
        left = float(str(actual).strip())
        right = float(str(expected).strip())
    except (TypeError, ValueError):
        return str(actual).strip() == str(expected).strip()
    return abs(left - right) <= max(tol, abs(right) * tol)


def _protocol_code(value: Any) -> Optional[int]:
    """把 ``protocol`` 参数归成 int；非法/缺省返回 ``None``（不校验协议类型）。"""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


#: HTTP 能力可覆盖的构造参数（与 blocks 层其它模块同名字段口径一致）
_CAP_PARAM_KEYS = ("base_url", "timeout", "probe_timeout", "retries",
                   "retry_backoff", "mapping_id", "transport", "env_var")


def _capability(ctx: Any, name: str, factory, options: Any = None) -> Any:
    """取能力句柄，优先级：能力注入 → ``ctx.extras[name]`` → 按块参数构造。

    1. 块参数里直接给对象：``params["simcon"]`` / ``params["simeter"]`` / ``params["frames"]``
       （单测/离线回放用，与 blocks 层其它模块的 ``*_capability`` 解析口径一致）；
    2. ``ctx.extras[name]``（或 ``ctx.extras['capabilities'][name]``）；
    3. 按块参数 ``base_url`` / ``timeout`` / ``mapping_id`` / ``transport`` 等自建。
    """
    opts = dict(options or {})

    injected = opts.get(name) or opts.get(name + "_capability")
    if injected is not None:
        return injected

    source = ctx
    extras = None
    if isinstance(source, Mapping):
        extras = source
    elif hasattr(source, "extras"):
        extras = getattr(source, "extras")
    if isinstance(extras, Mapping):
        found = extras.get(name)
        if found is not None:
            return found
        nested = extras.get("capabilities")
        if isinstance(nested, Mapping) and nested.get(name) is not None:
            return nested.get(name)

    if name == "frames":
        facade = opts.get("facade")
        return FramesCapability(facade) if facade is not None else FramesCapability()

    kwargs = {key: opts[key] for key in _CAP_PARAM_KEYS
              if key in opts and opts[key] is not None}
    return factory(**kwargs) if kwargs else factory()


# --------------------------------------------------------------------------- #
# 判据结论（三态；块与事实按不同口径映射，判据本身只此一份）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Verdict:
    """判据结论。

    - ``state``：**事实口径**三态 —— ``pass``（证据成立）/ ``fail``（证据相悖）/
      ``unknown``（没拿到证据）。事实层据此映射 ``yes`` / ``no`` / ``unknown``。
    - ``block_state``：**块口径**（缺省同 ``state``）。区别只有一处且必须明确：
      「上行帧已收到、帧是合法的 02H-F1，但内层没有 645 地址/数据证据」—— 对事实是
      ``unknown``（没探到 ≠ 不存在），对块是 ``fail``（设备回了一帧转发报文却不是我们要的
      内容，本身就是缺陷，绝不放过）。
    """

    state: str
    detail: str = ""
    expected: Any = None
    actual: Any = None
    evidence: tuple = ()
    block_state: str = ""

    def block_state_of(self) -> str:
        return self.block_state or self.state

    def to_block(self, *, unknown_prefix: str = "") -> BlockResult:
        """映射成块结果（``block_state`` 优先；``unknown`` 绝不折算 ``pass``）。"""
        state = self.block_state_of()
        detail = self.detail
        if state == BLOCK_UNKNOWN and unknown_prefix:
            detail = unknown_prefix + detail
        return BlockResult(
            state=state,
            detail=detail,
            expected=self.expected,
            actual=self.actual,
            evidence_ids=list(self.evidence),
        )


# --------------------------------------------------------------------------- #
# 帧解码 / 诊断
# --------------------------------------------------------------------------- #


def decode_outer(frames: Any, frame_hex: Any) -> Tuple[Optional[dict], Optional[str]]:
    """解码外层 02H-F1：先按 ``1376.2`` 指定（宽容），失败再自动嗅探。

    返回 ``(decoded, protocol_used)``；都失败返回 ``(None, None)``。
    """
    for protocol in (OUTER_PROTOCOL, None):
        decoded = frames.decode(frame_hex, protocol=protocol)
        if isinstance(decoded, Mapping) and decoded.get("ok"):
            return dict(decoded), protocol
    return None, None


def _decode_error(frames: Any, frame_hex: Any) -> str:
    decoded = frames.decode(frame_hex, protocol=OUTER_PROTOCOL)
    code = decoded.get("error") if isinstance(decoded, Mapping) else decoded
    detail = decoded.get("detail") if isinstance(decoded, Mapping) else ""
    return "%s%s" % (code, ("：" + str(detail)) if detail else "")


def _forwarded_payload(decoded: Any) -> str:
    """取 02H-F1 的「转发报文」内层 hex（门面 decode 的 items 里）。"""
    frame = decoded.get("frame") if isinstance(decoded, Mapping) else None
    entry = _field(frame, FIELD_FORWARDED)
    if entry is None:
        return ""
    return norm_hex(entry.get("hex") or entry.get("value") or "")


def diagnose_inner_payload(frames: Any, payload_hex: str) -> str:
    """内层 645 没能被递归解析时，给一句人话原因（不猜值，只报实测）。"""
    if not payload_hex:
        return "（上行帧里没有「%s」字段）" % FIELD_FORWARDED
    inner = frames.decode(payload_hex, protocol=INNER_PROTOCOL_645)
    if not (isinstance(inner, Mapping) and inner.get("ok")):
        code = inner.get("error") if isinstance(inner, Mapping) else inner
        return "（转发报文 %s… 无法按 645 解析：%s）" % (payload_hex[:32], code)
    inner_frame = inner.get("frame")
    cs = _field(inner_frame, FIELD_CS) or {}
    desc = str(cs.get("desc") or "")
    if desc and "校验通过" not in desc:
        return "（转发报文按 645 解析但 %s，故不递归）" % desc
    return (
        "（转发报文 %s… 按 645 可解，但外层「通信协议类型」非 645-2007/1997 时不递归，"
        "内层字段：%s）" % (payload_hex[:32], "、".join(_field_names(inner_frame)) or "无")
    )


# --------------------------------------------------------------------------- #
# 判据 1：内层 645 地址域 == 期望表地址（REQS §10.7 事实 E）
# --------------------------------------------------------------------------- #


def judge_meter_addr(
    reply_frame: Any,
    expect_addr: Any,
    *,
    frames: Any = None,
    protocol_type: Any = None,
) -> Verdict:
    """判「上行 02H-F1 内嵌 645 的地址域是否等于期望表地址」。

    三态：地址相等 → ``pass``；地址不等 → ``fail``（带期望/实际）；
    拿不到地址证据（解不开 / 无内嵌 / 无地址域） → ``unknown``。
    """
    frames = frames if frames is not None else FramesCapability()
    expect = norm_hex(expect_addr)
    frame_hex = norm_hex(reply_frame)
    if not frame_hex:
        return Verdict(BLOCK_UNKNOWN, "没有上行帧 hex，无法判定")
    if not expect:
        return Verdict(BLOCK_UNKNOWN, "未给出期望表地址（meter_addr），无法判定")

    decoded, protocol_used = decode_outer(frames, frame_hex)
    if decoded is None:
        return Verdict(
            BLOCK_UNKNOWN,
            "上行帧无法解析（既非 1376.2 也未通过自动嗅探）：%s"
            % _decode_error(frames, frame_hex),
            expected=expect,
        )
    outer_frame = decoded.get("frame")

    # 通信协议类型自证（给了 protocol 才校验）：不是 645-2007/1997 就不会递归解析内层
    want_protocol = _protocol_code(protocol_type)
    got_protocol = _field_hex(outer_frame, FIELD_PROTOCOL_TYPE)
    protocol_note = ""
    if want_protocol is not None and got_protocol:
        if int(got_protocol, 16) != want_protocol:
            return Verdict(
                BLOCK_UNKNOWN,
                "上行 02H-F1「%s」= 0x%s，不是期望的 0x%02X（645-2007）→ 不会递归解析内层 645"
                % (FIELD_PROTOCOL_TYPE, got_protocol, want_protocol),
                expected=expect,
                actual="0x%s" % got_protocol,
                block_state=BLOCK_FAIL,  # 请求 645-2007 转发，回来的是别的协议类型 = 缺陷
            )
        protocol_note = "（%s=0x%s）" % (FIELD_PROTOCOL_TYPE, got_protocol)

    sub = frames.nested(frame_hex, protocol=protocol_used)
    if not (isinstance(sub, Mapping) and sub.get("ok")):
        payload = _forwarded_payload(decoded)
        return Verdict(
            BLOCK_UNKNOWN,
            "上行 02H-F1 内层无 645 嵌套：%s%s"
            % (
                (sub.get("detail") if isinstance(sub, Mapping) else sub)
                or "nested 解析失败",
                diagnose_inner_payload(frames, payload),
            ),
            expected=expect,
            block_state=BLOCK_FAIL,  # 帧收到了却没内嵌 645：块判据不成立
        )

    addr_result = frames.meter_addr(frame_hex, protocol=protocol_used)
    if not (isinstance(addr_result, Mapping) and addr_result.get("ok")):
        available = addr_result.get("available") if isinstance(addr_result, Mapping) else None
        return Verdict(
            BLOCK_UNKNOWN,
            "内层 645 取不到「%s」（%s）；可用字段：%s"
            % (
                FIELD_ADDR,
                (addr_result.get("detail") if isinstance(addr_result, Mapping) else addr_result),
                "、".join(str(name) for name in (available or [])) or "无",
            ),
            expected=expect,
            block_state=BLOCK_FAIL,
        )

    actual = norm_hex(addr_result.get("addr"))
    structure = addr_result.get("structure") or (
        sub.get("structure") if isinstance(sub, Mapping) else None
    )
    if actual == expect:
        return Verdict(
            BLOCK_PASS,
            "上行 02H-F1 内嵌 %s 地址域 = %s，与期望表地址一致%s"
            % (structure or "帧", actual, protocol_note),
            expected=expect,
            actual=actual,
            evidence=(frame_hex,),
        )
    return Verdict(
        BLOCK_FAIL,
        "内层 645 地址域不匹配：期望地址 %s，实际 %s（上行帧 %s）"
        % (expect, actual or "空", frame_hex),
        expected=expect,
        actual=actual,
        evidence=(frame_hex,),
    )


# --------------------------------------------------------------------------- #
# 判据 2：内层 645 读数据应答（CS 合法 + DI 回显 + 数值可解）
# --------------------------------------------------------------------------- #


def judge_read_645(
    reply_frame: Any,
    *,
    expect_di: Any = DEFAULT_DI,
    expect_value: Any = None,
    frames: Any = None,
    protocol_type: Any = None,
) -> Verdict:
    """判「上行 02H-F1 内嵌 645 读数据应答是否成立」。

    判据：内层 645 **CS 合法** + **DI 回显正确** + **数值可解**（门面能出字段）；
    给了 ``expect_value`` 再比数值。三态口径同 :func:`judge_meter_addr`。
    """
    frames = frames if frames is not None else FramesCapability()
    frame_hex = norm_hex(reply_frame)
    di = norm_hex(expect_di) or DEFAULT_DI
    if not frame_hex:
        return Verdict(BLOCK_UNKNOWN, "没有上行帧 hex，无法判定")

    decoded, _protocol_used = decode_outer(frames, frame_hex)
    if decoded is None:
        return Verdict(
            BLOCK_UNKNOWN,
            "上行帧无法解析（既非 1376.2 也未通过自动嗅探）：%s"
            % _decode_error(frames, frame_hex),
        )
    outer_frame = decoded.get("frame")

    # 通信协议类型自证（给了 protocol 才校验）：类型不对时内层 645 无从谈起
    want_protocol = _protocol_code(protocol_type)
    got_protocol = _field_hex(outer_frame, FIELD_PROTOCOL_TYPE)
    if want_protocol is not None and got_protocol and int(got_protocol, 16) != want_protocol:
        return Verdict(
            BLOCK_UNKNOWN,
            "上行 02H-F1「%s」= 0x%s，不是期望的 0x%02X（645-2007）"
            % (FIELD_PROTOCOL_TYPE, got_protocol, want_protocol),
            expected="DI %s" % di,
            actual="0x%s" % got_protocol,
            block_state=BLOCK_FAIL,
        )

    payload = _forwarded_payload(decoded)
    if not payload:
        return Verdict(
            BLOCK_UNKNOWN,
            "上行 02H-F1 没有「%s」字段（字段：%s）"
            % (FIELD_FORWARDED, "、".join(_field_names(outer_frame)) or "无"),
            block_state=BLOCK_FAIL,  # 回的 02H-F1 里没有转发报文 = 不是数据转发应答
        )

    inner = frames.decode(payload, protocol=INNER_PROTOCOL_645)
    if not (isinstance(inner, Mapping) and inner.get("ok")):
        code = inner.get("error") if isinstance(inner, Mapping) else inner
        return Verdict(
            BLOCK_FAIL,
            "内层转发报文无法按 645 解析（%s）：%s" % (code, payload),
            evidence=(frame_hex, payload),
        )
    inner_frame = inner.get("frame") or {}

    # (a) CS 合法
    cs = _field(inner_frame, FIELD_CS) or {}
    cs_desc = str(cs.get("desc") or "")
    if cs_desc and "校验通过" not in cs_desc:
        return Verdict(
            BLOCK_FAIL,
            "内层 645 CS 校验不合法：%s（转发报文 %s）" % (cs_desc, payload),
            evidence=(frame_hex, payload),
        )

    # (b) 异常应答（控制码 D6=1）直接判失败
    ctrl_entry = _field(inner_frame, FIELD_CONTROL) or {}
    ctrl_raw = ctrl_entry.get("raw")
    ctrl_txt = str(ctrl_entry.get("value") or "")
    if isinstance(ctrl_raw, int) and ctrl_raw & _645_EXCEPTION_BIT:
        return Verdict(
            BLOCK_FAIL,
            "内层 645 是从站异常应答（控制码 %s），无数据" % (ctrl_txt or "0x%02X" % ctrl_raw),
            evidence=(frame_hex, payload),
        )

    # (c) DI 回显（DI 显示序 → 传输序 +33H，如 00010000 → 33333433）
    echo = di_echo_hex(di)
    haystack = (payload + norm_hex(inner_frame.get("raw_hex") or "")).upper()
    if echo and echo not in haystack:
        return Verdict(
            BLOCK_FAIL,
            "内层 645 未回显 DI：期望 DI %s（回显字节 %s），实际转发报文 %s"
            % (di, echo, payload),
            expected=di,
            evidence=(frame_hex, payload),
        )

    # (d) 数值可解（门面 items 里至少一项带数值）
    item = None
    for entry in inner_frame.get("items") or []:
        if isinstance(entry, Mapping) and entry.get("value") not in (None, ""):
            item = entry
            break
    if item is None:
        return Verdict(
            BLOCK_FAIL,
            "内层 645 数据域未解出数值（DI %s，items 均为空：%s）"
            % (di, "、".join(_field_names(inner_frame)) or "无"),
            expected=di,
            evidence=(frame_hex, payload),
        )

    value = item.get("value")
    unit = str(item.get("unit") or "")
    item_name = str(item.get("name") or "")
    actual = {"di": di, "item": item_name, "value": value, "unit": unit,
              "control": ctrl_txt}
    detail = "内层 645 读数据应答成立：DI %s 回显正确，CS 合法，%s = %s %s" % (
        di, item_name or "数值", value, unit)

    if expect_value not in (None, ""):
        if not _value_matches(value, expect_value):
            return Verdict(
                BLOCK_FAIL,
                "数值不匹配：期望 %s，实际 %s（%s，DI %s）"
                % (expect_value, value, item_name or "数值", di),
                expected=expect_value,
                actual=value,
                evidence=(frame_hex, payload),
            )
        detail += "（与期望值 %s 一致）" % (expect_value,)

    return Verdict(
        BLOCK_PASS,
        detail,
        expected=expect_value,
        actual=actual,
        evidence=(frame_hex, payload),
    )


# --------------------------------------------------------------------------- #
# 下发：02H-F1 数据转发透传 645 → 取上行应答帧
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SendOutcome:
    """一次 02H-F1 下发的结论：``state`` = ``reply``（拿到上行帧，见 ``reply_frame``）
    或块态（``skipped``/``unknown``/``fail``，此时 ``detail`` 说明原因）。

    ``reply_frame`` 在非 ``reply`` 态下**也可能非空**：verify 步骤自己判 fail、但步骤里
    命中了上行帧（``matched``）时照样带出来。等待型重试（REQS-0042 S2-F）就靠这个字段
    分「有应答但判据不成立（``fail``）」和「一帧都没有（``unknown``）」，别把它当空。
    """

    state: str
    detail: str
    reply_frame: str = ""


#: simcon 错误码 → （块态，人话）。顺序：精确匹配优先，再按前缀。
_SEND_ERROR_TABLE = {
    "no_base_url": (BLOCK_SKIPPED, "未配置工作台地址（simcon 不可用），不判设备"),
    "unreachable": (BLOCK_SKIPPED, "工作台/simcon 不可达，不判设备"),
    "transport_error": (BLOCK_UNKNOWN, "传输层异常，未拿到结论"),
    "empty_steps": (BLOCK_UNKNOWN, "verify 未提交任何步骤（配置错误）"),
    "poll_timeout": (BLOCK_UNKNOWN, "verify 轮询超时（job 未到终态）：inconclusive，不判 FAIL"),
    "http_404": (BLOCK_SKIPPED, "无会话/端口未开（HTTP 404），先跑 fix.simcon_session"),
    "http_409": (BLOCK_SKIPPED, "闸门或会话忙（HTTP 409）：不自动重试，稍后重跑"),
    "bad_response": (BLOCK_UNKNOWN, "服务返回结构无法识别"),
}


def _send_outcome_from_error(error: str, detail: str, result: Any = None) -> SendOutcome:
    """错误码 → :class:`SendOutcome`（不改判据、不重试）。"""
    text = "（%s）%s" % (error, detail) if error else str(detail or "")
    known = _SEND_ERROR_TABLE.get(error)
    if known is not None:
        return SendOutcome(known[0], "%s：%s" % (known[1], text))
    if error.startswith("http_5"):
        return SendOutcome(BLOCK_SKIPPED, "工作台服务端错误（%s），不判设备" % text)
    if error.startswith("http_4"):
        return SendOutcome(
            BLOCK_UNKNOWN,
            "请求被拒（%s）：参数/构帧配置错误，不是设备 FAIL" % text,
        )
    if error == "job_not_passed":
        steps = result.get("steps") if isinstance(result, Mapping) else None
        return SendOutcome(
            BLOCK_FAIL,
            "verify 步骤判定 fail：%s%s" % (text, _steps_reasons(steps)),
        )
    if error.startswith("job_"):
        return SendOutcome(BLOCK_UNKNOWN, "verify 任务未正常结束（%s）" % text)
    return SendOutcome(BLOCK_UNKNOWN, "无法识别的 verify 结论（%s）" % (text or "空"))


#: simeeter 错误码 → 块态（服务侧为 skipped，其余 unknown；绝不判设备 FAIL）
_SIMETER_SKIP_ERRORS = frozenset(
    {"no_base_url", "unreachable", "http_404", "http_409"})


def _simeter_state(error: str) -> str:
    """simeeter 错误码 → 块态：依赖不可用 ``skipped``，其余 ``unknown``。"""
    if error in _SIMETER_SKIP_ERRORS or error.startswith("http_5"):
        return BLOCK_SKIPPED
    return BLOCK_UNKNOWN


def _steps_reasons(steps: Any) -> str:
    if not isinstance(steps, list):
        return ""
    parts = []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        if str(step.get("result") or "").lower() == "pass":
            continue
        reason = str(step.get("reason") or "")
        name = str(step.get("name") or "")
        parts.append("%s%s" % (("[" + name + "] ") if name else "", reason or "无原因"))
    return ("；" + "；".join(parts)) if parts else ""


def _matched_hex(result: Mapping[str, Any]) -> str:
    """从 verify 结果里取「命中的上行帧 hex」（步骤 ``matched``）。"""
    steps = result.get("steps")
    if not isinstance(steps, list):
        return ""
    for step in steps:
        if isinstance(step, Mapping) and step.get("matched"):
            return norm_hex(step.get("matched"))
    return ""


def _frames_fallback(
    simcon: Any, result: Mapping[str, Any], *, afn: str = "02", fn: str = "F1"
) -> str:
    """步骤没给 ``matched`` 时，回查会话帧日志取最后一条上行 ``afn/fn`` 帧。"""
    run_id = result.get("run_id")
    try:
        log = simcon.frames(run_id=run_id, updown="up", direction="rx",
                            afn=afn, fn=fn, limit=50)
    except Exception:  # noqa: BLE001 - 回查是尽力而为，不改变判定口径
        return ""
    if not (isinstance(log, Mapping) and log.get("ok")):
        return ""
    for entry in reversed(list(log.get("entries") or [])):
        if isinstance(entry, Mapping) and entry.get("frame_hex"):
            return norm_hex(entry.get("frame_hex"))
    return ""


def probe_forward_reply(
    simcon: Any,
    inner_hex: Any,
    *,
    protocol_type: Any = PROTOCOL_645_2007,
    expect_timeout: Any = DEFAULT_EXPECT_TIMEOUT,
    max_poll_seconds: Any = None,
    name: str = "02H-F1 数据转发（透传 645）",
) -> SendOutcome:
    """下发一帧 02H-F1（透传 ``inner_hex``）并取上行应答帧 hex。

    这是块与事实**共用**的下发+取帧函数（FT-2）；判据不在这里。
    """
    payload = norm_hex(inner_hex)
    if not payload:
        return SendOutcome(BLOCK_FAIL, "内嵌 645 报文为空（构帧失败/参数缺失），无法下发")
    try:
        proto = int(protocol_type)
    except (TypeError, ValueError):
        return SendOutcome(
            BLOCK_UNKNOWN, "参数 protocol 非法：%r（应 1=645-1997 / 2=645-2007）" % (protocol_type,)
        )
    try:
        timeout = float(expect_timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_EXPECT_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_EXPECT_TIMEOUT
    budget = float(max_poll_seconds) if max_poll_seconds not in (None, "") else timeout + 20.0

    steps = [
        {
            "name": name,
            "send": {"afn": "02", "fn": 1,
                     "params": {"protocol": proto, "payload": payload}},
            "expect": {"format": "local", "afn": 2, "fn": 1, "dir": "up"},
            "expect_timeout": timeout,
        }
    ]
    try:
        result = simcon.verify(steps, max_poll_seconds=budget)
    except Exception as exc:  # noqa: BLE001 - 能力层承诺不抛，抛了也不能崩
        return SendOutcome(BLOCK_UNKNOWN, "simcon.verify 抛异常：%r" % (exc,))
    if not isinstance(result, Mapping):
        return SendOutcome(
            BLOCK_UNKNOWN,
            "simcon.verify 返回类型无法识别：%s" % type(result).__name__,
        )
    if result.get("ok"):
        reply = _matched_hex(result) or _frames_fallback(simcon, result)
        if not reply:
            return SendOutcome(
                BLOCK_UNKNOWN,
                "verify 通过但取不到上行 02H-F1 帧 hex（步骤无 matched、帧日志不可用）",
            )
        return SendOutcome("reply", "已收到上行 02H-F1", reply)
    outcome = _send_outcome_from_error(
        str(result.get("error") or ""), str(result.get("detail") or ""), result
    )
    # 失败/无结论时也带上「这一步自己命中的上行帧」（只认 steps[*].matched，**不**回查
    # 帧日志：日志里可能是上一轮尝试的旧帧，会把「没应答」误读成「有应答」）。
    # 状态与 detail 一律不动，只多一份证据 —— 等待型重试据此区分 fail / unknown。
    matched = _matched_hex(result)
    if matched and not outcome.reply_frame:
        return SendOutcome(outcome.state, outcome.detail, matched)
    return outcome


# --------------------------------------------------------------------------- #
# 等待口径（REQS-0042 D5-2）：03H-F7 取 t → t+5s；取不到 → 60s
# --------------------------------------------------------------------------- #

#: ``ctx.extras`` 里缓存 03H-F7 查询结果的键：**同一次运行只查一次**（不每块都查）
TIMEOUT_CACHE_KEY = "_meter_max_timeout_cache"


def max_timeout_of(frames: Any, reply_hex: Any) -> Tuple[Optional[float], str]:
    """从上行 ``03H-F7`` 应答帧里取 ``t``（秒）：返回 ``(t, 说明)`` / ``(None, 原因)``。

    解析**不在这里重写**：直接用门面按 1376.2 解码，读 ``adapter_10376`` 已经解析出的
    数据项「最大超时时间」（该适配器 ``afn==0x03 and fn==7`` 分支，BIN 1 字节，单位 s）。
    本仓库 parser **支持** 03H-F7（``metadata/afn_fn.json`` 的 ``F7`` 定义 +
    ``adapter_10376/__init__.py`` 的 ``fn == 7`` 分支），因此这里是真解析，不伪造。
    """
    hex_text = norm_hex(reply_hex)
    if not hex_text:
        return None, "没有上行 03H-F7 帧 hex"
    decoded = frames.decode(hex_text, protocol=OUTER_PROTOCOL)
    if not (isinstance(decoded, Mapping) and decoded.get("ok")):
        detail = decoded.get("detail") if isinstance(decoded, Mapping) else decoded
        return None, "上行帧按 1376.2 解不开：%s" % (detail or "未知原因")
    entry = _field(decoded.get("frame"), TIMEOUT_FIELD)
    if entry is None:
        return None, "上行帧里没有「%s」数据项（不像 03H-F7 应答）" % TIMEOUT_FIELD
    text = str(entry.get("value") or "")
    found = re.search(r"\d+(?:\.\d+)?", text)
    if found:
        return float(found.group(0)), "03H-F7 t=%ss" % found.group(0)
    raw = norm_hex(entry.get("hex") or "")
    if len(raw) == 2:
        seconds = float(int(raw, 16))
        return seconds, "03H-F7 t=%gs（%s）" % (seconds, raw)
    return None, "「%s」值不可解：%r" % (TIMEOUT_FIELD, text)


def query_max_timeout(
    simcon: Any,
    frames: Any = None,
    *,
    expect_timeout: Any = TIMEOUT_QUERY_SECONDS,
    max_poll_seconds: Any = None,
) -> Tuple[Optional[float], str]:
    """向 CCO 发一帧 ``03H-F7``「读取从节点监控最大超时时间」，返回 ``(t 秒, 说明)``。

    **永不抛**：无能力/无会话/模块不支持该帧/帧解不开，一律返回 ``(None, 原因)``，
    由 :func:`resolve_expect_timeout` 回落到 :data:`DEFAULT_EXPECT_TIMEOUT`（60s）。
    """
    frames = frames if frames is not None else FramesCapability()
    try:
        wait = float(expect_timeout)
    except (TypeError, ValueError):
        wait = TIMEOUT_QUERY_SECONDS
    if wait <= 0:
        wait = TIMEOUT_QUERY_SECONDS
    budget = float(max_poll_seconds) if max_poll_seconds not in (None, "") else wait + 20.0

    steps = [
        {
            "name": "查 CCO 从节点监控最大超时时间（03H-F7）",
            "send": {"afn": TIMEOUT_QUERY_AFN, "fn": TIMEOUT_QUERY_FN, "params": {}},
            "expect": {"format": "local", "afn": int(TIMEOUT_QUERY_AFN, 16),
                       "fn": TIMEOUT_QUERY_FN, "dir": "up"},
            "expect_timeout": wait,
        }
    ]
    try:
        result = simcon.verify(steps, max_poll_seconds=budget)
    except Exception as exc:  # noqa: BLE001 - 取 t 是尽力而为，失败只回落不崩块
        return None, "03H-F7 simcon.verify 抛异常：%r" % (exc,)
    if not isinstance(result, Mapping):
        return None, "03H-F7 simcon.verify 返回类型无法识别：%s" % type(result).__name__
    if not result.get("ok"):
        error = str(result.get("error") or "")
        detail = str(result.get("detail") or "")
        return None, "03H-F7 未拿到应答（%s%s）%s" % (
            error or "未知错误", ("：" + detail) if detail else "",
            _steps_reasons(result.get("steps")),
        )
    reply = _matched_hex(result) or _frames_fallback(simcon, result, afn="03", fn="F7")
    if not reply:
        return None, "03H-F7 应答取不到帧 hex（步骤无 matched、帧日志不可用）"
    return max_timeout_of(frames, reply)


def _timeout_cache(ctx: Any, options: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """取「同一次运行共享」的 03H-F7 查询缓存；拿不到可写容器 → ``None``（不缓存）。

    优先用块参数 ``timeout_cache``（显式 dict，单测用），否则挂到 ``ctx.extras``
    —— 执行器一次运行只用同一个 :class:`RunContext`，所以天然是「本次运行」粒度。
    """
    explicit = options.get("timeout_cache")
    if isinstance(explicit, dict):
        return explicit
    extras: Any = None
    if isinstance(ctx, Mapping):
        extras = ctx
    elif hasattr(ctx, "extras"):
        extras = getattr(ctx, "extras")
    if isinstance(extras, dict):
        cache = extras.get(TIMEOUT_CACHE_KEY)
        if not isinstance(cache, dict):
            cache = {}
            extras[TIMEOUT_CACHE_KEY] = cache
        return cache
    return None


def _provider_timeout(
    provider: Any, *, simcon: Any, frames: Any, ctx: Any
) -> Tuple[Optional[float], str]:
    """调「取 t」钩子：支持无参 callable 与 ``(simcon=, frames=, ctx=)`` 关键字 callable。

    钩子返回 ``t``（数字/``"90s"``/``None``）或 ``(t, 说明)``；异常一律吞掉回落。
    """
    try:
        params = list(inspect.signature(provider).parameters)
    except (TypeError, ValueError):
        params = []
    try:
        if params:
            value = provider(simcon=simcon, frames=frames, ctx=ctx)
        else:
            value = provider()
    except Exception as exc:  # noqa: BLE001 - 钩子是可选注入，失败只回落
        return None, "「取 t」钩子抛异常：%r" % (exc,)

    note = "「取 t」钩子"
    if isinstance(value, tuple) and len(value) == 2:
        value, note = value[0], str(value[1] or note)
    if value in (None, ""):
        return None, "%s没有给出 t" % note
    try:
        seconds = float(str(value).rstrip("sS"))
    except (TypeError, ValueError):
        return None, "%s给出的 t 无法解析：%r" % (note, value)
    if seconds <= 0:
        return None, "%s给出的 t 非正：%r" % (note, value)
    return seconds, "%s t=%gs" % (note, seconds)


def _timeout_t(
    options: Mapping[str, Any],
    ctx: Any = None,
    *,
    simcon: Any = None,
    frames: Any = None,
) -> Tuple[Optional[float], str]:
    """取 ``t``（一次运行只查一次）：钩子 > 03H-F7 查询；结果（含失败）都进缓存。"""
    cache = _timeout_cache(ctx, options)

    provider = options.get("max_timeout_provider")
    if provider is None and cache is not None:
        provider = cache.get("provider")
    if provider is None:
        extras: Any = None
        if isinstance(ctx, Mapping):
            extras = ctx
        elif hasattr(ctx, "extras"):
            extras = getattr(ctx, "extras")
        if isinstance(extras, Mapping):
            provider = extras.get("max_timeout_provider")

    if cache is not None and "seconds" in cache:
        return cache.get("seconds"), str(cache.get("detail") or "")

    if provider is not None:
        seconds, note = _provider_timeout(
            provider, simcon=simcon, frames=frames, ctx=ctx)
    elif not bool(options.get("query_max_timeout", True)):
        seconds, note = None, "已关闭 03H-F7 查询（query_max_timeout=False）"
    elif simcon is None:
        seconds, note = None, "没有 simcon 能力，查不了 03H-F7"
    else:
        seconds, note = query_max_timeout(
            simcon,
            frames,
            expect_timeout=options.get("timeout_query_seconds", TIMEOUT_QUERY_SECONDS),
            max_poll_seconds=options.get("timeout_query_poll_seconds"),
        )

    if cache is not None:
        cache["seconds"] = seconds
        cache["detail"] = note
        cache["provider"] = provider
    return seconds, note


def resolve_expect_timeout(
    options: Mapping[str, Any],
    ctx: Any = None,
    *,
    simcon: Any = None,
    frames: Any = None,
) -> Tuple[float, str]:
    """定「等上行 02H-F1 应答」的秒数：**显式 > t+5s > 60s**（REQS-0042 D5-2）。

    返回 ``(秒数, 口径说明)``；说明只用于 detail/日志，不参与判定。

    1. 调用方显式 ``expect_timeout``（>0 才算）；
    2. ``03H-F7`` 取到的 ``t`` + ``timeout_margin``（缺省 5s）；
    3. 回落到 :data:`DEFAULT_EXPECT_TIMEOUT`（60s）——查不到/解析不出/模块不支持都走这里。
    """
    explicit = options.get("expect_timeout")
    if explicit not in (None, ""):
        try:
            value = float(explicit)
        except (TypeError, ValueError):
            value = -1.0
        if value > 0:
            return value, "调用方显式 expect_timeout=%gs" % value

    try:
        margin = float(options.get("timeout_margin", TIMEOUT_MARGIN_SECONDS))
    except (TypeError, ValueError):
        margin = TIMEOUT_MARGIN_SECONDS
    if margin < 0:
        margin = TIMEOUT_MARGIN_SECONDS

    seconds, note = _timeout_t(options, ctx, simcon=simcon, frames=frames)
    if seconds is not None:
        total = seconds + margin
        return total, "规范口径 %gs（t%+gs，%s）" % (total, margin, note)
    return (
        DEFAULT_EXPECT_TIMEOUT,
        "回落默认 %gs（取不到 t：%s）" % (DEFAULT_EXPECT_TIMEOUT, note or "未知原因"),
    )


# --------------------------------------------------------------------------- #
# 构帧（仅当 JSON 未给 payload 时使用；走能力层，不在此复制构帧逻辑）
# --------------------------------------------------------------------------- #


def build_645_request(
    frames: Any,
    *,
    addr: Any,
    control: str,
    di: Any = None,
    addr_mode: Any = DEFAULT_ADDR_MODE,
) -> Tuple[str, str]:
    """用门面构一帧 645 请求：返回 ``(frame_hex, 失败原因)``。

    只有 ``control == 13H``（读通信地址）受 ``addr_mode`` 影响，两种口径都合规可用，
    选择权交给调用方（REQS-0042 裁决）：

    - ``addr_mode="meter"``（**缺省**）：地址域 = ``addr`` 的 645 线序，
      ``123456789012`` → ``68 12 34 56 78 90 12 68 13 00 CS 16``。**本台 CCO 能单播转发**
      （它从内层 645 报文里提表地址）；``addr`` 同时是判据的期望值。
    - ``addr_mode="broadcast"``：地址域全 FEH，即 DL/T645-2007 §6.3 规范写法
      （``68 FE FE FE FE FE FE 68 13 00 D7 16``，``01_DLT645_帧格式.md:326-340``）。
      **本台实测回空帧**（``evidence/0042/s2-meter_read-13h-FE.log``），仅作规范对照。

    非 13H 控制码（如 11H 读数据）永远用真实 ``addr``，``addr_mode`` 不生效。
    非法 ``addr_mode`` 直接返回失败原因（配置错误，绝不静默当缺省）。
    """
    control_hex = str(control or "").upper()
    mode = str(addr_mode or DEFAULT_ADDR_MODE).strip().lower()
    if mode not in ADDR_MODES:
        return "", "addr_mode 非法：%r（应为 %s 之一）" % (
            addr_mode, "/".join(ADDR_MODES))
    wire_addr = ADDR_BROADCAST if (
        control_hex == CONTROL_READ_ADDR and mode == ADDR_MODE_BROADCAST
    ) else (norm_hex(addr) or DEFAULT_ADDR)
    params: Dict[str, Any] = {"addr": wire_addr, "control": control_hex}
    if di not in (None, ""):
        params["di"] = norm_hex(di)
    try:
        built = frames.build(INNER_PROTOCOL_645, params)
    except Exception as exc:  # noqa: BLE001
        return "", "645 构帧异常：%r" % (exc,)
    if not (isinstance(built, Mapping) and built.get("ok")):
        return "", "645 构帧失败：%s" % (
            (built.get("detail") if isinstance(built, Mapping) else built),
        )
    return norm_hex(built.get("frame_hex")), ""


def build_645_read_frame(addr: Any = None, di: Any = None) -> Tuple[str, str]:
    """按地址+DI 本地构一帧 645 读数据请求（11H）：返回 ``(frame_hex, 失败原因)``。

    **唯一**一份「读数据帧」构造实现：``meter.read_645`` 与 ``meter.brain_local``
    都调这里（不复制第二份）。

    帧结构::

        68 | A0..A5 | 68 | 11 | 04 | DI 传输序 | CS | 16

    - 地址域：``common.meter_addr_wire(addr)`` —— 645 线序**原样**（低字节在前，**不反序**）；
      人读 ``123456789012`` → 线序 ``12 34 56 78 90 12``（真实台架已验）。
      换算只有 ``common.py`` 一份实现，本文件不再手写反转。
    - DI：显示序 → 传输序复用 :func:`di_echo_hex`（``00010000`` → ``33333433``）。
    - CS：整帧除尾 2 字节求和取低 8 位。

    实测基准（真实台架抓到）::

        000000000001 → 6800000000000168110433333433B316
        123456789012 → 68123456789012681104333334336816
    """
    addr_hex = norm_hex(addr) or DEFAULT_ADDR
    di_hex = norm_hex(di) or DEFAULT_DI
    addr_bytes = meter_addr_wire(addr_hex)
    if len(addr_bytes) != 6:
        return "", "645 地址非法：%r（需 12 位 BCD 数字，如 123456789012）" % (addr,)
    di_wire = di_echo_hex(di_hex)
    if not di_wire:
        return "", "645 DI 非法：%r（需 8 位 hex，如 00010000）" % (di,)
    body = (
        bytes([0x68])
        + addr_bytes
        + bytes([0x68, int(CONTROL_READ_DATA, 16), 0x04])
        + bytes.fromhex(di_wire)
    )
    return (body + bytes([sum(body) & 0xFF, 0x16])).hex().upper(), ""


# --------------------------------------------------------------------------- #
# 等待型重试基础设施（REQS-0042 S2-F）：复位 STA 后链路恢复是分钟级，读取只要 2.5s
# --------------------------------------------------------------------------- #
#
# 口径与 ``blocks/nwk.py`` 的 ``poll_snapshot`` / ``waited_text`` **一致**（同一套 detail
# 措辞、同一套可注入时钟），但实现在本文件：``common.py`` 不在本任务写入范围内，
# 复制一份是为了不碰兄弟模块。将来若要把「等待轮询」提到 ``common.py``，本段可整体搬走。


def format_seconds(value: Any) -> str:
    """等待时长 → 人读秒数（整数不带小数点，detail 统一写「等了 N 秒」）。"""
    seconds = _seconds_of(value)
    if seconds is None:
        seconds = 0.0
    rounded = round(seconds)
    if abs(seconds - rounded) < 1e-6:
        return "%d" % int(rounded)
    return "%.1f" % seconds


def _seconds_of(value: Any) -> Optional[float]:
    """任意值 → 有限秒数（``None`` = 不是数字 / NaN / Inf）。"""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def waited_text(waited: Any, attempts: Any, *, per_attempt: Any = None) -> str:
    """等待读数后缀：「（等了 N 秒，只读探测 K 次；单次最多等 S 秒）」。

    措辞**必须**与 ``core.executor`` 的两条正则对齐（``等了 N 秒`` / ``只读探测 K 次``）：
    执行器靠它们把「等了多久、探了几次」派生进 ``decisions[]``（``kind=wait``/``timeout``），
    改字就丢留痕 —— 本任务不改 core，所以措辞在这里钉死。
    """
    text = "（等了 %s 秒，只读探测 %d 次" % (format_seconds(waited), int(attempts or 0))
    seconds = _seconds_of(per_attempt)
    if seconds is not None and seconds > 0:
        text += "；单次最多等 %s 秒" % format_seconds(seconds)
    return text + "）"


def inner_645_control(frame_hex: Any) -> Optional[int]:
    """取内层 645 帧的控制码（``68 | A0..A5 | 68 | C | ...`` 的 ``C``）；解不出 → ``None``。"""
    text = norm_hex(frame_hex)
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return None
    if len(raw) < 10 or raw[0] != 0x68 or raw[7] != 0x68:
        return None
    return raw[8]


def readonly_violation(payload: Any) -> str:
    """等待期**只读**闸门：返回违例说明（空串 = 可以重发）。

    为什么要有这道闸门：等待是「反复重发同一个透传帧」最多几十次，一旦 payload 是写帧
    （合闸/写数据/冻结……），「等链路恢复」就变成了**反复对表下发写动作**。白名单只有
    :data:`READONLY_645_CONTROLS`（11H 读数据 / 13H 读通信地址）；看不出控制码即视为
    无法确认只读，同样拒绝（宁可报配置错误，也不拿台架赌）。
    """
    control = inner_645_control(payload)
    if control is None:
        return "内嵌 645 帧看不出控制码（%r），无法确认是只读透传" % (payload,)
    if control not in READONLY_645_CONTROLS:
        return "内嵌 645 控制码 %02XH 不是只读帧（等待期只允许 11H 读数据 / 13H 读通信地址）" % (
            control,
        )
    return ""


def wait_params(options: Mapping[str, Any]) -> Tuple[float, float, str]:
    """解析 ``wait_s`` / ``poll_interval_s``：返回 ``(等待秒, 间隔秒, 非法说明)``。

    ``wait_s`` 缺省 **0**（只探一次 = 旧行为）；给了但不合法（非数字/负数）→ 第三个返回值
    非空，块报配置错误（``unknown``），**绝不**静默替换成某个"看起来合理"的秒数。
    """
    wait_s = DEFAULT_WAIT_S
    raw_wait = options.get("wait_s")
    if raw_wait not in (None, ""):
        parsed = _seconds_of(raw_wait)
        if parsed is None or parsed < 0:
            return (
                DEFAULT_WAIT_S,
                DEFAULT_POLL_INTERVAL_S,
                "参数 wait_s 非法：%r（应为 >= 0 的秒数，0 = 只探一次）" % (raw_wait,),
            )
        wait_s = parsed

    interval_s = DEFAULT_POLL_INTERVAL_S
    raw_interval = options.get("poll_interval_s")
    if raw_interval not in (None, ""):
        parsed = _seconds_of(raw_interval)
        if parsed is None or parsed < 0:
            return (
                DEFAULT_WAIT_S,
                DEFAULT_POLL_INTERVAL_S,
                "参数 poll_interval_s 非法：%r（应为 >= 0 的秒数）" % (raw_interval,),
            )
        interval_s = parsed
    return wait_s, interval_s, ""


def _first_callable(*candidates: Any) -> Optional[Callable]:
    for candidate in candidates:
        if callable(candidate):
            return candidate
    return None


def poll_hooks(options: Mapping[str, Any], cap: Any = None) -> Tuple[Callable, Callable]:
    """解析注入的 ``(clock, sleep)``：块参数 → 能力对象属性 → 模块默认（真实时钟）。

    JSON 声明里写不出 callable，所以注入只发生在单测/外部装配层；缺省即真实
    ``time.monotonic`` / ``time.sleep``。
    """
    clock = _first_callable(options.get("clock"), getattr(cap, "clock", None), _clock)
    sleeper = _first_callable(
        options.get("sleep"),
        options.get("sleeper"),
        getattr(cap, "sleeper", None),
        getattr(cap, "sleep", None),
        _sleep,
    )
    return clock, sleeper


@dataclass(frozen=True)
class Attempt:
    """一次透传尝试的结论。

    - ``result``：本次尝试的块结果（pass / fail / unknown / skipped 按**原判据**得出）；
    - ``frame``：本次命中的上行 02H-F1 帧 hex（空 = 本次**完全没有应答**）。

    ``frame`` 是 unknown 与 fail 的分水岭：有帧而判据不成立是**产品失败**，
    连帧都没收到只是**说不清**（链路还没恢复）。
    """

    result: BlockResult
    frame: str = ""


@dataclass(frozen=True)
class WaitLoop:
    """等待循环的结论与留痕（供 :func:`annotate_wait` / :func:`silent_unknown` 使用）。

    ``reason``：``single``（``wait_s<=0``，只探一次）/ ``satisfied``（等到 pass 停）/
    ``unavailable``（依赖不可用，立即收工）/ ``judged``（等满，期间有应答但判据不成立）/
    ``silent``（等满，全程一帧应答都没有）。
    """

    result: BlockResult
    attempts: int = 1
    waited: float = 0.0
    per_attempt_max: float = 0.0
    timed_out: bool = False
    reason: str = "single"
    last_detail: str = ""


def wait_attempts(
    attempt: Callable[[float], Attempt],
    *,
    wait_s: Any = DEFAULT_WAIT_S,
    interval_s: Any = DEFAULT_POLL_INTERVAL_S,
    expect_timeout: Any = DEFAULT_EXPECT_TIMEOUT,
    clock: Any = None,
    sleep: Any = None,
) -> WaitLoop:
    """反复调 ``attempt(单次等待秒数)``，直到 pass / 依赖不可用 / 等满 ``wait_s``。

    **单次尝试最大耗时 / 总预算**（``expect_timeout`` 与 ``wait_s`` 不叠乘）::

        单次尝试最大耗时 = expect_timeout                        # wait_s <= 0：只探一次
        单次尝试最大耗时 = min(expect_timeout, wait_s - 已等)      # wait_s > 0，下限 1s
        总预算          ≈ wait_s + 一个 interval_s 的粒度 + 最后一次实际往返

    所以「``wait_s=120``、``expect_timeout=15``、间隔 5s」**不会**变成
    「24 次 × 每次最多 15s = 360s」：剩余预算从总预算里扣，尾部那几次的单次等待被压缩到
    剩余量，两次尝试之间的 sleep 也只睡「到预算为止」的那段，总时长由 ``wait_s`` 兜住。

    终止性保证（三条，缺一条就会挂测试或打爆台架）：

    1. ``wait_s <= 0`` → 只调一次、不 sleep（旧口径逐字不变，``reason="single"``）；
    2. 内层每次先重算 ``剩余预算 = wait_s - 已等``，已等 ≥ 预算即收工；
    3. ``attempts`` 另有上限 ``ceil(wait_s / interval_s) + 1`` —— 即使注入的时钟不前进
       （或 ``poll_interval_s=0`` 根本不 sleep）也一定收敛。

    **只读**由 :func:`readonly_violation` 在块入口把关：本函数只按原样重发同一个 payload。
    """
    now = clock if callable(clock) else _clock
    pause = sleep if callable(sleep) else _sleep
    limit = _seconds_of(wait_s)
    limit = DEFAULT_WAIT_S if limit is None else max(limit, 0.0)
    interval = _seconds_of(interval_s)
    interval = DEFAULT_POLL_INTERVAL_S if interval is None else max(interval, 0.0)
    budget = _seconds_of(expect_timeout)
    budget = DEFAULT_EXPECT_TIMEOUT if budget is None or budget <= 0 else budget

    if limit <= 0:
        # 缺省路径：只探一次、不 sleep —— 与等待型改造前**逐字一致**
        att = attempt(budget)
        return WaitLoop(
            result=att.result, attempts=1, waited=0.0,
            per_attempt_max=budget, timed_out=False, reason="single",
            last_detail=att.result.detail,
        )

    tick = interval if interval > 0 else DEFAULT_POLL_INTERVAL_S
    max_attempts = int(math.ceil(limit / tick)) + 1
    started = float(now())
    attempts = 0
    waited = 0.0
    per_attempt_max = 0.0
    framed: Optional[Attempt] = None
    last: Optional[Attempt] = None
    while True:
        elapsed = max(float(now()) - started, 0.0)
        remaining = max(limit - elapsed, 0.0)
        per_attempt = min(budget, max(remaining, MIN_ATTEMPT_TIMEOUT_S))
        per_attempt_max = max(per_attempt_max, per_attempt)
        last = attempt(per_attempt)
        attempts += 1
        elapsed = max(float(now()) - started, 0.0)
        # 虚拟时钟不前进（注入的时钟不动）时退回「间隔 × 次数」的协议口径秒数，
        # 这样 detail 里的「等了 N 秒」在任何注入方式下都是人读得懂的
        waited = elapsed if elapsed > 0 else (attempts - 1) * interval
        state = last.result.state
        if state == BLOCK_SKIPPED:
            # 依赖不可用（404/409/不可达）：重试不会变好，立即收工
            return WaitLoop(last.result, attempts, waited, per_attempt_max, False,
                            "unavailable", last.result.detail)
        if state == BLOCK_PASS:
            return WaitLoop(last.result, attempts, waited, per_attempt_max, False,
                            "satisfied", last.result.detail)
        if norm_hex(last.frame):
            framed = last
        if elapsed >= limit or attempts >= max_attempts:
            break
        if interval > 0:
            # 只睡「到预算为止」的那段：总时长由 wait_s 兜住，不再多漂一个完整间隔
            pause(min(interval, max(limit - elapsed, 0.0)))

    if framed is not None:
        # 等满但**有过应答**：保留「有应答且判据不成立」的原结论（fail 不被掩盖）
        return WaitLoop(framed.result, attempts, waited, per_attempt_max, True,
                        "judged", framed.result.detail)
    detail = last.result.detail if last is not None else ""
    return WaitLoop(last.result if last is not None else Attempt(
        BlockResult(state=BLOCK_UNKNOWN, detail="")), attempts, waited,
        per_attempt_max, True, "silent", detail)


def silent_unknown(
    what: str, loop: WaitLoop, *, expected: Any = None, evidence: Any = ()
) -> BlockResult:
    """等满预算仍**一帧应答都没有** → ``unknown``（说不清链路是否恢复，**不是 fail**）。

    真机口径：复位 STA 后表口静默十几分钟，这时「读不到」既不能算成功，也不该算产品失败
    —— 只能如实报「等了 N 秒、共 M 次尝试、始终没有上行帧」，交给上层决定是否续等。
    """
    detail = (
        "等了 %s 秒共 %d 次尝试，始终没有收到%s 应答"
        "（超时无帧 = 说不清链路是否已恢复，按 unknown 收尾，不判 FAIL）"
        % (format_seconds(loop.waited), loop.attempts, what)
    )
    if loop.last_detail:
        detail += "；最后一次：%s" % loop.last_detail
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail=detail,
        expected=expected,
        evidence_ids=[str(item) for item in (evidence or ()) if item],
    )


def annotate_wait(result: BlockResult, *, wait_s: Any, loop: WaitLoop) -> BlockResult:
    """把等待读数写进块结果（``wait_s<=0`` 或「第一次就出结论」时**原样返回**）。

    - ``detail`` 追加「（等了 N 秒，只读探测 K 次；单次最多等 S 秒）」——``core.executor``
      的 ``poll_reading`` 按这个口径解析，自动派生 ``decisions[]`` 的 ``wait``/``timeout``；
    - ``actual`` 是映射时并入``等待秒``/``探测次数``（执行器优先读它），
      是空值时就填这两个读数，是标量（如 addr_probe 的「实际地址」）则不动 —— 原证据优先。
    """
    limit = _seconds_of(wait_s)
    if loop.reason == "single" or (limit is None or limit <= 0):
        return result
    if loop.attempts <= 1 and loop.waited <= 0:
        return result  # 第一次就 pass：不留等待痕迹，输出与旧版一致
    result.detail = "%s%s" % (
        result.detail,
        waited_text(loop.waited, loop.attempts, per_attempt=loop.per_attempt_max),
    )
    readings = {"等待秒": round(float(loop.waited), 3), "探测次数": int(loop.attempts)}
    if isinstance(result.actual, Mapping):
        merged = dict(result.actual)
        merged.update(readings)
        result.actual = merged
    elif result.actual in (None, ""):
        result.actual = readings
    return result


# --------------------------------------------------------------------------- #
# 块 1：meter.addr_probe
# --------------------------------------------------------------------------- #


def meter_addr_probe(params: Mapping[str, Any], ctx: Any = None) -> BlockResult:
    """``meter.addr_probe``：02H-F1 透传 645 读通信地址，判内层地址域 == 期望地址。

    入参 ``meter_addr``（电表 645 人读地址，缺省取配置默认值，见 REQS-0042 D2-2）：
    它既是 13H 请求帧地址域的来源（``addr_mode="meter"`` 时），也是**判据**的期望值
    （13H 应答回装的地址域要与它相等）——两种 ``addr_mode`` 判据完全一样。

    ``addr_mode``（REQS-0042 裁决，见 :func:`build_645_request`）：缺省 ``"meter"``
    （本台 CCO 可用）；``"broadcast"`` 是 645-2007 §6.3 的规范写法，本台实测 CCO
    对 ``02H-F1``+全 FE 不转发、回空帧（``evidence/0042/s2-meter_read-13h-FE.log``）。

    等待口径见 :func:`resolve_expect_timeout`：显式 ``expect_timeout`` > ``t+5s`` > ``60s``。

    等待型重试（REQS-0042 S2-F，见 :func:`wait_attempts`）：``wait_s`` 缺省 **0** = 只探一次
    （行为与改造前逐字一致）；``meter_first_join``（含复位）场景按需给值，例如
    ``wait_s=900`` + ``poll_interval_s=15`` 等链路恢复。三态在重试下的口径：

    - 期间某次判据成立 → ``pass``（立即停）；
    - 等满且**全程一帧应答都没有** → ``unknown``（说不清链路是否恢复，**不是 fail**）；
    - 等满但**有应答且判据不成立**（地址不符/内层无 645） → ``fail``（真失败，不被重试掩盖）。
    """
    action = dict(params or {})
    options = block_params(action)
    frames = _capability(ctx, "frames", FramesCapability, options)

    expect_addr, bad = meter_addr_of(action)
    if bad:
        return _bad_addr_result(bad)
    if not expect_addr:
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="未解析到表地址 meter_addr（12 位 BCD 电表 645 表地址；"
            "缺省取 case_defaults.meter_addr）——配置问题，非设备结论",
        )
    protocol_type = options.get("protocol", PROTOCOL_645_2007)
    addr_mode = str(options.get("addr_mode") or DEFAULT_ADDR_MODE).strip().lower()

    wait_s, interval_s, wait_error = wait_params(options)
    if wait_error:
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="等待参数非法（配置错误，非设备结论）：%s" % wait_error,
            expected=expect_addr,
        )

    payload = norm_hex(options.get("payload"))
    if not payload:
        payload, reason = build_645_request(
            frames, addr=expect_addr, control=CONTROL_READ_ADDR, addr_mode=addr_mode
        )
        if not payload:
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail="内嵌 645 13H 请求帧构帧失败（配置错误，非设备 FAIL）：%s" % reason,
                expected=expect_addr,
            )

    if wait_s > 0:
        violation = readonly_violation(payload)
        if violation:
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail="等待型重试被只读闸门拦下（配置错误，非设备结论）：%s"
                "——等待期只允许重发只读透传帧（645 11H/13H）；要发写帧请把 wait_s 置 0"
                % violation,
                expected=expect_addr,
                evidence_ids=[payload],
            )

    simcon = _capability(ctx, "simcon", SimconCapability, options)
    timeout, timeout_note = resolve_expect_timeout(
        options, ctx, simcon=simcon, frames=frames)

    def attempt(attempt_timeout: float) -> Attempt:
        """一次完整透传（02H-F1 下发 + 等上行）+ 原判据。"""
        outcome = probe_forward_reply(
            simcon,
            payload,
            protocol_type=protocol_type,
            expect_timeout=attempt_timeout,
            max_poll_seconds=options.get("max_poll_seconds"),
            name="STA 获取表地址（02H-F1 透传 645 13H）",
        )
        if outcome.state != "reply":
            return Attempt(
                BlockResult(
                    state=outcome.state,
                    detail="%s（地址口径：%s；等待口径：%s）"
                    % (outcome.detail, addr_mode, timeout_note),
                    expected=expect_addr,
                    evidence_ids=[payload],
                ),
                frame=outcome.reply_frame,
            )

        verdict = judge_meter_addr(
            outcome.reply_frame,
            expect_addr,
            frames=frames,
            protocol_type=protocol_type,
        )
        result = verdict.to_block(
            unknown_prefix="上行 02H-F1 已收到但判据不成立：",
        )
        if result.state != BLOCK_PASS:
            # 失败/拿不到结论时把「用的哪套 13H 地址口径」写进 detail：本台 CCO 对
            # broadcast 回空帧，排障必须先能看出请求的是哪种地址域。
            result.detail = "%s（地址口径：%s）" % (result.detail, addr_mode)
        # 块 provides 生效只在 state==pass，这里显式给事实取值（绝不凭空造）
        if result.state == BLOCK_PASS:
            result.facts = {"sta.meter_addr_known": FACT_YES}
        return Attempt(result, frame=outcome.reply_frame)

    clock, sleeper = poll_hooks(options, simcon)
    loop = wait_attempts(
        attempt,
        wait_s=wait_s,
        interval_s=interval_s,
        expect_timeout=timeout,
        clock=clock,
        sleep=sleeper,
    )
    if loop.reason == "silent":
        return annotate_wait(
            silent_unknown(
                "上行 02H-F1（STA 获取表地址）",
                loop,
                expected=expect_addr,
                evidence=[payload],
            ),
            wait_s=wait_s,
            loop=loop,
        )
    return annotate_wait(loop.result, wait_s=wait_s, loop=loop)


# --------------------------------------------------------------------------- #
# 块 2：meter.read_645
# --------------------------------------------------------------------------- #


def meter_read_645(params: Mapping[str, Any], ctx: Any = None) -> BlockResult:
    """``meter.read_645``：02H-F1 透传 645 读数据，判 CS 合法 + DI 回显 + 数值可解。

    入参 ``meter_addr``（电表 645 人读地址，缺省取配置默认值，见 REQS-0042 D2-2）。
    等待口径见 :func:`resolve_expect_timeout`：显式 ``expect_timeout`` > ``t+5s`` > ``60s``。

    等待型重试（REQS-0042 S2-F，见 :func:`wait_attempts`）：``wait_s`` 缺省 **0** = 只读一次
    （``meter_daily`` 不复位，用默认即可）；复位后的 ``meter_first_join`` 才需要等待型，
    三态口径与 ``meter.addr_probe`` 完全一致：等到判据成立 → ``pass``；等满全程无帧
    → ``unknown``（不是 fail）；等满但有帧且判据不成立 → ``fail``。
    """
    action = dict(params or {})
    options = block_params(action)
    frames = _capability(ctx, "frames", FramesCapability, options)

    addr, bad = meter_addr_of(action)
    if bad:
        return _bad_addr_result(bad)
    if not addr:
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="未解析到表地址 meter_addr（12 位 BCD 电表 645 表地址；"
            "缺省取 case_defaults.meter_addr）——配置问题，非设备结论",
        )
    di = norm_hex(options.get("di")) or DEFAULT_DI
    protocol_type = options.get("protocol", PROTOCOL_645_2007)

    wait_s, interval_s, wait_error = wait_params(options)
    if wait_error:
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="等待参数非法（配置错误，非设备结论）：%s" % wait_error,
            expected=di,
        )

    payload = norm_hex(options.get("payload"))
    if not payload:
        payload, reason = build_645_read_frame(addr, di)
        if not payload:
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail="内嵌 645 11H 读数据帧构帧失败（配置错误，非设备 FAIL）：%s" % reason,
                expected=di,
            )

    if wait_s > 0:
        violation = readonly_violation(payload)
        if violation:
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail="等待型重试被只读闸门拦下（配置错误，非设备结论）：%s"
                "——等待期只允许重发只读透传帧（645 11H/13H）；要发写帧请把 wait_s 置 0"
                % violation,
                expected=di,
                evidence_ids=[payload],
            )

    simcon = _capability(ctx, "simcon", SimconCapability, options)
    timeout, timeout_note = resolve_expect_timeout(
        options, ctx, simcon=simcon, frames=frames)

    def attempt(attempt_timeout: float) -> Attempt:
        """一次完整透传（02H-F1 下发 + 等上行）+ 原判据。"""
        outcome = probe_forward_reply(
            simcon,
            payload,
            protocol_type=protocol_type,
            expect_timeout=attempt_timeout,
            max_poll_seconds=options.get("max_poll_seconds"),
            name="抄读表数据（02H-F1 透传 645 11H DI %s）" % di,
        )
        if outcome.state != "reply":
            return Attempt(
                BlockResult(
                    state=outcome.state,
                    detail="%s（等待口径：%s）" % (outcome.detail, timeout_note),
                    expected=di,
                    evidence_ids=[payload],
                ),
                frame=outcome.reply_frame,
            )

        verdict = judge_read_645(
            outcome.reply_frame,
            expect_di=di,
            expect_value=options.get("expect_value"),
            frames=frames,
            protocol_type=protocol_type,
        )
        return Attempt(
            verdict.to_block(unknown_prefix="上行 02H-F1 已收到但判据不成立："),
            frame=outcome.reply_frame,
        )

    clock, sleeper = poll_hooks(options, simcon)
    loop = wait_attempts(
        attempt,
        wait_s=wait_s,
        interval_s=interval_s,
        expect_timeout=timeout,
        clock=clock,
        sleep=sleeper,
    )
    if loop.reason == "silent":
        return annotate_wait(
            silent_unknown(
                "上行 02H-F1（抄读表数据 DI %s）" % di,
                loop,
                expected=di,
                evidence=[payload],
            ),
            wait_s=wait_s,
            loop=loop,
        )
    return annotate_wait(loop.result, wait_s=wait_s, loop=loop)


# --------------------------------------------------------------------------- #
# 块 3：meter.brain_local（表端脑自检，无串口）
# --------------------------------------------------------------------------- #


def meter_brain_local(params: Mapping[str, Any], ctx: Any = None) -> BlockResult:
    """``meter.brain_local``：simeter ``handle_hex`` 纯函数入口自检表端脑 645 链路。

    请求帧口径：显式 ``request_hex`` 优先；未显式给时按 ``meter_addr`` + ``di``
    用 :func:`build_645_read_frame` **动态构造**（台架换表地址后不再误判 FAIL）。

    正帧判据：``reply_hex`` 非空 + DI 回显 + ``note.value`` 存在；
    反帧（``expect_reply=False``，如地址不符）判据：``reply_hex is None`` 且
    ``note.mine is False``（「不应答」正是期望，因此判 pass）。

    注意：反帧场景的**地址不符是刻意的**（声明 ``negative_addr_mismatch`` 显式给一帧
    别的地址），所以「统一地址」只约束「按地址动态构帧」这条路径，**不得**把显式
    ``request_hex`` 换成本表地址帧——那就把负例变成了正例。
    """
    action = dict(params or {})
    options = block_params(action)

    di = norm_hex(options.get("di")) or DEFAULT_DI
    expect_reply = bool(options.get("expect_reply", True))
    request_hex = norm_hex(options.get("request_hex"))
    if not request_hex:
        addr, bad = meter_addr_of(action)
        if bad:
            return _bad_addr_result(bad, expected=di)
        if not addr:
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail="未解析到表地址 meter_addr（12 位 BCD 电表 645 表地址；"
                "缺省取 case_defaults.meter_addr）——配置问题，非设备结论",
                expected=di,
            )
        request_hex, reason = build_645_read_frame(addr, di)
        if not request_hex:
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail="请求帧构帧失败（配置错误，非设备 FAIL）：%s" % reason,
                expected=di,
            )

    simeter = _capability(ctx, "simeter", SimeterCapability, options)
    try:
        answered = simeter.handle_hex(request_hex)
    except Exception as exc:  # noqa: BLE001 - 能力层承诺不抛，抛了也不能崩
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="simeter.handle_hex 抛异常：%r" % (exc,),
            evidence_ids=[request_hex],
        )
    if not isinstance(answered, Mapping):
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail="simeter.handle_hex 返回类型无法识别：%s" % type(answered).__name__,
        )
    if not answered.get("ok"):
        error = str(answered.get("error") or "")
        return BlockResult(
            state=_simeter_state(error),
            detail="simeter 不可用（%s）：%s"
            % (error or "未知错误", answered.get("detail") or "无说明"),
            evidence_ids=[request_hex],
        )

    reply_hex = norm_hex(answered.get("reply_hex")) if answered.get("reply_hex") else ""
    note = answered.get("note") if isinstance(answered.get("note"), Mapping) else {}
    note_value = note.get("value")
    mine = note.get("mine")
    echo = di_echo_hex(di)
    replies_note = "reply_hex=%s note=%s" % (
        reply_hex or "None",
        {k: note.get(k) for k in ("func", "di", "value", "mine", "error") if k in note},
    )

    # --- 反帧：期望不应答 ---
    if not expect_reply:
        if reply_hex:
            return BlockResult(
                state=BLOCK_FAIL,
                detail="期望表端脑不应答，实际应答了：reply_hex=%s（%s）"
                % (reply_hex, replies_note),
                expected="reply_hex=None 且 note.mine=False",
                actual=reply_hex,
                evidence_ids=[request_hex, reply_hex],
            )
        if mine is not False:
            return BlockResult(
                state=BLOCK_FAIL,
                detail="期望不应答且 note.mine=False，实际 %s（请求帧 %s）"
                % (replies_note, request_hex),
                expected="note.mine=False",
                actual=mine,
                evidence_ids=[request_hex],
            )
        return BlockResult(
            state=BLOCK_PASS,
            detail="地址不符帧按预期不应答：reply_hex=None 且 note.mine=False（%s）"
            % replies_note,
            expected="reply_hex=None 且 note.mine=False",
            actual="不应答",
            evidence_ids=[request_hex],
        )

    # --- 正帧：期望应答 ---
    if not reply_hex:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="期望应答，但表端脑未应答：%s（请求帧 %s）" % (replies_note, request_hex),
            expected="reply_hex 非空",
            actual="None",
            evidence_ids=[request_hex],
        )
    if echo and echo not in reply_hex:
        return BlockResult(
            state=BLOCK_FAIL,
            detail="应答帧未回显 DI：期望 DI %s（回显字节 %s），实际 reply_hex=%s"
            % (di, echo, reply_hex),
            expected=di,
            actual=reply_hex,
            evidence_ids=[request_hex, reply_hex],
        )
    if note_value in (None, ""):
        return BlockResult(
            state=BLOCK_FAIL,
            detail="应答帧 note 里没有 value（表端未解出数据）：%s" % replies_note,
            expected="note.value 非空",
            actual=None,
            evidence_ids=[request_hex, reply_hex],
        )
    return BlockResult(
        state=BLOCK_PASS,
        detail="表端脑 645 链路正常：DI %s 回显 %s，reply_hex=%s，note.value=%s（%s）"
        % (di, echo, reply_hex, note_value, note.get("func") or "读数据"),
        expected={"di": di, "reply": "非空", "note.value": "非空"},
        actual={"reply_hex": reply_hex, "note.value": note_value,
                "control": note.get("ctrl")},
        evidence_ids=[request_hex, reply_hex],
    )


# --------------------------------------------------------------------------- #
# 注册（import 即注册；``register()`` 供测试在 clear_registries() 后重注册）
# --------------------------------------------------------------------------- #

#: 块 id → 执行体（声明见 ``data/blocks/meter_*.json``）
BLOCKS: Dict[str, Any] = {
    "meter.addr_probe": meter_addr_probe,
    "meter.read_645": meter_read_645,
    "meter.brain_local": meter_brain_local,
}


def register() -> None:
    """注册本文件的 3 个抄表块执行体（**幂等**，与 ``blocks/__init__.py`` 约定一致）。

    已注册的 id 跳过（不抛 ``Duplicate``），因此可在 ``clear_registries()`` 之后
    反复调用重注册；真正撞 id 由 :func:`case_runner.core.registry.register_block` 守门。
    """
    for block_id, fn in BLOCKS.items():
        if not has_block(block_id):
            register_block(block_id, fn)


register()


__all__ = [
    "ADDR_BROADCAST",
    "ADDR_MODES",
    "ADDR_MODE_BROADCAST",
    "ADDR_MODE_METER",
    "Attempt",
    "BLOCKS",
    "CONTROL_READ_ADDR",
    "CONTROL_READ_DATA",
    "DEFAULT_ADDR",
    "DEFAULT_ADDR_MODE",
    "DEFAULT_DI",
    "DEFAULT_EXPECT_TIMEOUT",
    "DEFAULT_POLL_INTERVAL_S",
    "DEFAULT_WAIT_S",
    "MIN_ATTEMPT_TIMEOUT_S",
    "OUTER_PROTOCOL",
    "PROTOCOL_645_1997",
    "PROTOCOL_645_2007",
    "READONLY_645_CONTROLS",
    "SELF_CHECK_FOREIGN_REQUEST",
    "SELF_CHECK_REQUEST",
    "SendOutcome",
    "TIMEOUT_CACHE_KEY",
    "TIMEOUT_FIELD",
    "TIMEOUT_MARGIN_SECONDS",
    "TIMEOUT_QUERY_AFN",
    "TIMEOUT_QUERY_FN",
    "TIMEOUT_QUERY_SECONDS",
    "Verdict",
    "WaitLoop",
    "annotate_wait",
    "block_params",
    "build_645_read_frame",
    "build_645_request",
    "decode_outer",
    "diagnose_inner_payload",
    "di_echo_hex",
    "format_seconds",
    "inner_645_control",
    "judge_meter_addr",
    "judge_read_645",
    "max_timeout_of",
    "meter_addr_of",
    "meter_addr_probe",
    "meter_brain_local",
    "meter_read_645",
    "norm_hex",
    "poll_hooks",
    "probe_forward_reply",
    "query_max_timeout",
    "readonly_violation",
    "register",
    "resolve_expect_timeout",
    "silent_unknown",
    "wait_attempts",
    "wait_params",
    "waited_text",
]
