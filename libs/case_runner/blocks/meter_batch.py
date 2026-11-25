"""并发抄表块（REQS-0043 F0-2 / F0-1）：``meter.batch_read``。

**只做薄编排，零重造**（CASE-BACKLOG §F0-1 红线）：滑窗调度、超时档位、否认码
人话、应答匹配全部是模拟集中器已交付能力（REQS-0027 G5 / 0030，
``sim_concentrator/batch.py`` + ``expect_rules``），本块只做
「起 job → 轮询 snapshot → 把 rows 汇成观测」三件事，经
``capabilities/simcon.py`` 的 ``batch_read_create`` / ``batch_read_job`` 访问端点，
块层零 HTTP 字面量（REQS-0044 §5.1）。

判据出处（CASE-BACKLOG §F0-2，逐条随代码保留）：

- 单表变体判据（本台架可测）＝成功**原样回装** F1H-F1 / 抄读不成功回**空载荷**
  （报文长度域=0）/ 超时档位 99s（``CCO实现逻辑/02-并发抄表.md:89`` 代码口径；
  业务文档 60s、台账 90s 与代码三处并存，**本仓库统一取 99s**，
  ``sim_concentrator/expect_rules.json:31-34`` batch_read 档——F0-7-4 登记的差异）。
- 回装/空载荷/否认归属的**判定不在块层**：job 的 rows 已按
  ``success / nodata（空载荷）/ deny（否认码）/ timeout / error`` 分行
  （``batch.py:_row`` / ``:247-341``），本块如实搬运成观测值；
  「成功数应为几」「否认码应是 6E」这类结论全部留给步骤 expect（0044 §4.2）。

观测（Observation，零 pass/fail）：
``任务号`` / ``模式`` / ``协议类型`` / ``抄读项`` / ``表数`` / ``最大并发`` /
``超时档位`` / ``成功数`` / ``否认数`` / ``超时数`` / ``空载荷数`` / ``异常数`` /
``否认明细``（deny_breakdown：否认码 hex → 条数）/ ``否认码列表`` /
``完成行数`` / ``行明细``（rows 精简版）/ ``等待秒`` / ``探测次数``。

等待预算（0044 §8.1-3）：``wait_s`` 是本块的**轮询总预算**（声明已挂
``wait_param``，Procedure 步骤必须显式给）；``expect_timeout`` 是单表业务超时
（透传给 job 的 ``timeout``；缺省 ``None`` = 按 expect_rules 档位，即并抄 99s），
两者**不叠乘**：轮询预算从 ``wait_s`` 里扣，等满即收（job 继续在后台跑，不 stop
——提前收工只影响本轮观测完整度，写进 ``complete=False``）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from case_runner.blocks.meter import (
    _capability,
    block_params,
    format_seconds,
    meter_addr_of,
    norm_hex,
    poll_hooks,
    waited_text,
)
from case_runner.capabilities.simcon import SimconCapability
from case_runner.core.executor import PARAM_ASSERT, BlockResult
from case_runner.core.expect import (
    OBS_BENCH_UNREACHABLE,
    OBS_INVALID_PARAM,
    OBS_SERIAL_BUSY,
    OBS_TIMEOUT,
    Observation,
)
from case_runner.core.models import BLOCK_UNKNOWN
from case_runner.core.registry import has_block, register_block

#: 缺省抄读项：4 字节 DI（8 位 hex）。取 simcon ``BatchReadRequest.item`` 的同款缺省
#: ``02010100``（A 相电压，REQS-0027 G5 口径）；698（protocol_type=3）时它是
#: **OAD**（8 位 hex，如 ``20000201``——F0-3：645 DI 与 698 OAD 是两套编号）。
DEFAULT_ITEM = "02010100"

#: 任务模式：``batch`` = 每表一帧 F1H-F1 并发抄表（F0-2 单表变体判据的口径）；
#: ``single`` = 每表一帧 02H-F1 单抄。与 simcon BatchReadRequest 同词表。
MODE_BATCH = "batch"
MODE_SINGLE = "single"

#: 1376.2「通信协议类型」：02H = DL/T 645-2007，03H = DL/T 698.45
#: （出处：``03_QGDW10376.2_全帧类型.md:1633``；批次 698 构帧分流见
#: ``sim_concentrator/batch.py`` 的 protocol_type 分支修复）
PROTOCOL_645_2007 = 2
PROTOCOL_698_45 = 3

#: 单表业务超时缺省口径 = **99s**（expect_rules batch_read 档；出处见模块头）。
#: 这里只用于「调用方没给 ``expect_timeout`` 也没法问 job」时的轮询预算兜底，
#: 下发给 job 的 ``timeout`` 缺省仍是 ``None``（让 expect_rules 自己选档）。
BATCH_TIMEOUT_TIER_S = 99.0

#: 轮询预算兜底余量：等满单表业务超时后再给 30s 收尾轮询（取快照/落行的间隙）
POLL_MARGIN_S = 30.0

#: ``action`` 里属于块元信息的键（与 blocks/meter.py 同一份口径）
_ACTION_META_KEYS = frozenset(
    {"afn", "fn", "params", "recipe", "semantic", "cleanup", "assert", "tags"}
)

#: 行明细里保留的键（rows 全量进观测会撑爆结果文件；保留判读所需的子集）
_ROW_KEYS = (
    "meter", "afn_fn", "status", "deny_code", "deny_text",
    "elapsed_ms", "note",
)

#: 行 status → 观测字段名（**不用「失败」二字**——AC-1 扫描禁词；语义不变：
#: deny=否认回装 / timeout=等满业务超时无应答 / error=任务行异常 /
#: nodata=空载荷回装（报文长度=0，02-并发抄表.md:235））
_ROW_STATUS_KEYS = {
    "success": "成功数",
    "deny": "否认数",
    "timeout": "超时数",
    "nodata": "空载荷数",
    "error": "异常数",
}


def _bad_param(detail: str, *, evidence: Any = ()) -> BlockResult:
    """参数/配置类问题 → 原因码 ``invalid_param`` 的观测（绝不折成设备结论）。"""
    return BlockResult(
        state=BLOCK_UNKNOWN,
        detail=detail,
        observation=Observation(
            values={}, complete=False, reason_code=OBS_INVALID_PARAM, detail=detail
        ),
        evidence_ids=[str(item) for item in (evidence or ()) if item],
    )


def _capability_error_result(result: Any, what: str) -> BlockResult:
    """能力层错误码 → 环境原因码观测（与 blocks/meter.py 的映射同口径）。"""
    error = str((result or {}).get("error") or "")
    detail = "%s：%s" % (what, (result or {}).get("detail") or error or "未知错误")
    if error in ("http_409",):
        code, state_detail = OBS_SERIAL_BUSY, "%s（闸门/会话忙 409）：%s" % (what, detail)
    elif error in ("no_base_url", "unreachable", "http_404", "transport_error") or error.startswith("http_5"):
        code, state_detail = OBS_BENCH_UNREACHABLE, detail
    else:
        code, state_detail = OBS_INVALID_PARAM, "%s（请求被拒 %s）" % (detail, error)
    observation = Observation(values={}, complete=False, reason_code=code, detail=state_detail)
    # skipped 类错误码保留旧路径语义（wait 轮询据此立即收工）；观测由原因码决定
    from case_runner.core.models import BLOCK_SKIPPED

    state = BLOCK_SKIPPED if code in (OBS_SERIAL_BUSY, OBS_BENCH_UNREACHABLE) else BLOCK_UNKNOWN
    return BlockResult(state=state, detail=state_detail, observation=observation)


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


def meters_of(options: Mapping[str, Any]) -> Tuple[List[str], str]:
    """解析并发抄表的**表清单**（F0-2 单表变体：``meter_addr`` 单地址即可）。

    - ``meter_addr``（12 位 BCD，电表 645 人读序）→ ``meters=[addr]``；
    - ``meter_addrs``（列表，为多表扩展预留；本台架单表，声明里不鼓励用）。
    返回 ``(清单, 非法说明)``；清单为空且无非法说明 = 没给地址。
    """

    def _clean(raw: Any) -> Tuple[Optional[str], bool]:
        text = norm_hex(raw)
        if len(text) == 12 and text.isdigit():
            return text, True
        return None, False

    entries: List[str] = []
    given_list = options.get("meter_addrs")
    if isinstance(given_list, (list, tuple)):
        for item in given_list:
            addr, ok = _clean(item)
            if not ok:
                return [], "meter_addrs 含非法地址：%r（需 12 位 BCD 数字）" % (item,)
            entries.append(addr)
    given = options.get("meter_addr")
    if given not in (None, ""):
        addr, ok = _clean(given)
        if not ok:
            return [], "meter_addr 非法：%r（需 12 位 BCD 数字，如 123456789012）" % (given,)
        if addr not in entries:
            entries.append(addr)
    if entries:
        return entries, ""
    # 与 meter.read_645 同口径：没给地址时兜底 case_defaults.meter_addr（REQS-0042 D2-2）
    default_addr, _bad = meter_addr_of(dict(options), allow_config_default=True)
    if default_addr:
        return [default_addr], ""
    return [], ""


def trim_rows(rows: Any) -> List[Dict[str, Any]]:
    """job rows → 观测行明细（只留判读子集，行数上限 64 防撑爆结果文件）。"""
    out: List[Dict[str, Any]] = []
    for row in list(rows or [])[:64]:
        if not isinstance(row, Mapping):
            continue
        entry = {key: row.get(key) for key in _ROW_KEYS if row.get(key) not in (None, "")}
        if entry:
            out.append(entry)
    return out


@dataclass(frozen=True)
class BatchSnapshot:
    """一次快照轮询的结论：``result`` = 能力层返回（``ok`` 时是 snapshot dict）。"""

    result: Any
    finished: bool = False


def meter_batch_read(params: Mapping[str, Any], ctx: Any = None) -> BlockResult:
    """``meter.batch_read``：并发抄表薄编排块，**只报观测不判定**。

    判据出处与观测清单见模块头（CASE-BACKLOG §F0-2）；「成功几条 / 否认码应为
    6E / 空载荷即预期」全部由步骤 expect 声明（例如 ``{"op":"eq",
    "field":"否认码列表","value":["6E"]}`` 或 ``{"op":"has_value","field":"成功数"}``）。

    等待预算：``wait_s``（轮询总预算，Procedure 必须显式给；缺省兜底 =
    单表业务超时 99s + 30s 收尾余量）；``expect_timeout``（单表业务超时，缺省
    ``None`` = job 按 expect_rules 档位取，即 99s）。等满预算 job 未到终态 →
    观测不完整（``timeout``），已完成的行照报，绝不折算成任何结论。
    """
    from case_runner.core.expect import OBS_INCOMPLETE

    action = dict(params or {})
    options = {
        key: value for key, value in block_params(action).items()
        if key not in (PARAM_ASSERT, "_cleanup")
    }

    meters, bad = meters_of(options)
    if bad:
        return _bad_param(bad)
    if not meters:
        return _bad_param(
            "未解析到表地址 meter_addr（12 位 BCD 电表 645 表地址；"
            "缺省取 case_defaults.meter_addr）——配置问题"
        )

    mode = str(options.get("mode") or MODE_BATCH).strip().lower()
    if mode not in (MODE_BATCH, MODE_SINGLE):
        return _bad_param(
            "mode 非法：%r（应 %s / %s：batch=每表一帧 F1H-F1，single=每表一帧 02H-F1）"
            % (options.get("mode"), MODE_BATCH, MODE_SINGLE)
        )
    try:
        protocol_type = int(options.get("protocol_type") or PROTOCOL_645_2007)
    except (TypeError, ValueError):
        return _bad_param("protocol_type 非法：%r（0 透明 / 1 645-97 / 2 645-07 / 3 698.45）"
                          % (options.get("protocol_type"),))
    item = norm_hex(options.get("di") or options.get("item")) or DEFAULT_ITEM
    if len(item) != 8:
        return _bad_param("抄读项 di 非法：%r（需 8 位 hex：645 为 4 字节 DI，698 为 4 字节 OAD）"
                          % (options.get("di") or options.get("item"),))
    addr_mode = str(options.get("addr_mode") or "wire").strip().lower()
    if addr_mode not in ("wire", "bcd"):
        return _bad_param("addr_mode 非法：%r（wire=CCO 档案地址口径 / bcd=645 BCD）"
                          % (options.get("addr_mode"),))
    try:
        max_concurrent = int(options.get("max_concurrent") or 5)
    except (TypeError, ValueError):
        return _bad_param("max_concurrent 非法：%r" % (options.get("max_concurrent"),))
    if not 1 <= max_concurrent <= 20:
        # 与 simcon 深化应用前置校验同口径（CCO 并发帧上限 20，超限否认 109；
        # deep_app.MAX_CONCURRENT_CAP=20，超限后端直接 422 拒绝不真发）
        return _bad_param(
            "max_concurrent 超限：%d（允许 1~20：CCO 并发帧上限 20，超限否认 109；"
            "后端会直接拒绝）" % max_concurrent
        )

    timeout_raw = options.get("expect_timeout")
    timeout: Optional[float]
    if timeout_raw in (None, ""):
        timeout = None  # 交给 expect_rules 档位（单抄 59s / 并抄 99s）
    else:
        timeout = _seconds_of(timeout_raw)
        if timeout is None or timeout <= 0:
            return _bad_param("expect_timeout 非法：%r（应 >0 的秒数；不传 = 按 99s 档）"
                              % (timeout_raw,))

    wait_given = _seconds_of(options.get("wait_s"))
    if options.get("wait_s") not in (None, "") and (wait_given is None or wait_given < 0):
        return _bad_param("wait_s 非法：%r（应 >=0 的秒数）" % (options.get("wait_s"),))
    tier = timeout if timeout is not None else BATCH_TIMEOUT_TIER_S
    wait_s = wait_given if wait_given else (tier + POLL_MARGIN_S)
    try:
        poll_interval = _seconds_of(options.get("poll_interval_s"))
    except (TypeError, ValueError):
        poll_interval = None
    if poll_interval is None or poll_interval <= 0:
        poll_interval = 2.0

    simcon = _capability(ctx, "simcon", SimconCapability, options)
    clock, sleeper = poll_hooks(options, simcon)

    created = simcon.batch_read_create(
        meters,
        max_concurrent=max_concurrent,
        mode=mode,
        protocol_type=protocol_type,
        item=item,
        addr_mode=addr_mode,
        timeout=timeout,
        profile=options.get("profile"),
        mapping_id=options.get("mapping_id"),
        port=options.get("port"),
    )
    if not isinstance(created, Mapping) or not created.get("ok"):
        return _capability_error_result(created, "并发抄表任务创建被拒")
    job_id = str(created.get("job_id") or "")

    started = float(clock())
    attempts = 0
    snapshot: Mapping[str, Any] = created
    finished = bool(created.get("finished"))
    while not finished:
        elapsed = max(float(clock()) - started, 0.0)
        if elapsed >= wait_s and attempts > 0:
            break  # 预算等满仍未到终态：收工，观测标不完整（timeout）
        remaining = max(wait_s - elapsed, 0.0)
        sleeper(min(poll_interval, remaining))
        polled = simcon.batch_read_job(job_id)
        if not isinstance(polled, Mapping) or not polled.get("ok"):
            # 单次轮询抖动不推翻整轮观测：重试到预算为止；404 = 任务已不在注册表
            attempts += 1
            if str(polled.get("error") or "") == "http_404":
                detail = "任务快照 404（job 已不在注册表）：%s" % (polled.get("detail") or "")
                observation = Observation(
                    values=_values_of(snapshot, meters, attempts=attempts, waited=elapsed),
                    complete=False, reason_code=OBS_INCOMPLETE, detail=detail,
                )
                return BlockResult(state=BLOCK_UNKNOWN,
                                   detail="并发抄表观测不完整：%s" % detail,
                                   observation=observation)
            continue
        attempts += 1
        snapshot = polled
        finished = bool(polled.get("finished"))

    elapsed = max(float(clock()) - started, 0.0)
    values = _values_of(snapshot, meters, attempts=attempts, waited=elapsed)
    detail = _detail_of(values, finished=finished, waited=elapsed, attempts=attempts)
    if not finished:
        # 等满预算仍未到终态：观测不完整（timeout），已完成的行照报，绝不收成结论
        values["等待秒"] = round(elapsed, 3)
        values["探测次数"] = attempts
        detail += waited_text(elapsed, max(attempts, 1))
        observation = Observation(
            values=values, complete=False, reason_code=OBS_TIMEOUT, detail=detail
        )
        return BlockResult(state=BLOCK_UNKNOWN, detail=detail, observation=observation)
    observation = Observation(values=values, complete=True, detail=detail)
    return BlockResult(state=BLOCK_UNKNOWN, detail=detail, observation=observation)


def _values_of(
    snapshot: Mapping[str, Any],
    meters: List[str],
    *,
    attempts: int,
    waited: float,
) -> Dict[str, Any]:
    """snapshot → 观测值（如实搬运 job 计数与否认明细，不做任何折算）。"""
    timeout_meta = snapshot.get("timeout")
    tier_note = ""
    if isinstance(timeout_meta, Mapping):
        tier_note = "%ss(%s)" % (timeout_meta.get("seconds"), timeout_meta.get("tier"))
    values: Dict[str, Any] = {
        "任务号": snapshot.get("job_id") or "",
        "模式": snapshot.get("mode") or "",
        "协议类型": snapshot.get("protocol_type") if "protocol_type" in snapshot
        else _protocol_of(snapshot),
        "抄读项": snapshot.get("item") or "",
        "表数": snapshot.get("meters_total", len(meters)),
        "最大并发": snapshot.get("max_concurrent"),
        "超时档位": tier_note,
        "完成行数": snapshot.get("done", 0),
        "否认明细": dict(snapshot.get("deny_breakdown") or {}),
    }
    counts: Dict[str, int] = {}
    deny_codes: List[str] = []
    for row in snapshot.get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        key = _ROW_STATUS_KEYS.get(str(row.get("status") or ""))
        if key:
            counts[key] = counts.get(key, 0) + 1
        code = row.get("deny_code")
        if code and code not in deny_codes:
            deny_codes.append(str(code))
    for key in _ROW_STATUS_KEYS.values():
        values[key] = counts.get(key, 0)
    values["否认码列表"] = deny_codes
    values["行明细"] = trim_rows(snapshot.get("rows"))
    if waited:
        values["等待秒"] = round(float(waited), 3)
    if attempts:
        values["探测次数"] = int(attempts)
    return values


def _protocol_of(snapshot: Mapping[str, Any]) -> Any:
    """快照没带 protocol_type 时从 afn_fn 反推（02H-F1=single/2 口径仅作展示）。"""
    return snapshot.get("protocol_type", "")


def _detail_of(
    values: Mapping[str, Any],
    *,
    finished: bool,
    waited: float,
    attempts: int,
) -> str:
    """观测的一句话人读摘要（不判结论；「否认 X 条」照实说）。"""
    text = (
        "并发抄表任务 %s：%d 表（%s，抄读项 %s），完成 %d 行"
        % (
            values.get("任务号") or "-",
            values.get("表数", 0) or 0,
            values.get("超时档位") or "档位未知",
            values.get("抄读项") or "-",
            values.get("完成行数", 0) or 0,
        )
    )
    parts = []
    for key in ("成功数", "否认数", "超时数", "空载荷数", "异常数"):
        count = values.get(key, 0) or 0
        if count:
            parts.append("%s %d 条" % (key, count))
    if values.get("否认码列表"):
        parts.append("否认码 %s" % "、".join(str(c) for c in values["否认码列表"]))
    if parts:
        text += "：" + "、".join(parts)
    if not finished:
        text += "；等满预算任务未到终态（观测不完整）"
    return text


#: 块 id → 执行体（声明见 ``data/blocks/meter_batch_read.json``）
BLOCKS: Dict[str, Any] = {
    "meter.batch_read": meter_batch_read,
}


def register() -> None:
    """注册本文件块执行体（幂等，与 ``blocks/__init__.py`` 约定一致）。"""
    for block_id, fn in BLOCKS.items():
        if not has_block(block_id):
            register_block(block_id, fn)


register()

__all__ = [
    "BATCH_TIMEOUT_TIER_S",
    "BLOCKS",
    "DEFAULT_ITEM",
    "MODE_BATCH",
    "MODE_SINGLE",
    "POLL_MARGIN_S",
    "PROTOCOL_645_2007",
    "PROTOCOL_698_45",
    "BatchSnapshot",
    "meter_batch_read",
    "meters_of",
    "register",
    "trim_rows",
]
