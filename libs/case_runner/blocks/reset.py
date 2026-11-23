"""复位块：``reset.sta``（REQS §10.11 第一批新增；结论依据 ``RESEARCH-reset.md``）。

**手段**：经 ``sta-main`` 模块串口会话向 STA 控制台发 ``reboot``（补 CRLF，实发 8 字节）。
调研结论（``RESEARCH-reset.md``）：本台架唯一已验证、可原子化、有明确回显判据的 STA
复位手段就是它 —— CCO 控制台没有 ``sta reset`` 一类命令；1376.2 ``01H-F1`` 复位的是
CCO 自己（本地通信模块），不是远端 STA；``AT+RESET`` 依赖的厂测口当前台架不存在；
断电重上电不可原子化且无软件判据。

**前置事实**：``needs: ["bench.sta_online"]``（STA 控制台口 ``sta-main`` 是否可用）。
**不是** ``bench.simcon_online`` —— 复位根本不经过模拟集中器，写 simcon 会让 simcon
离线时把本块挡成 skipped（实际它完全能跑）；也不能写成空列表（口不可用时不该跑）。

**判据**（不可只看命令返回；``send`` 成功只代表字节写出了）：复位时刻 ``t0`` 之后，该口
会话日志里出现启动指纹之一 —— ``reset reason:`` / ``SFT_GLB_RST`` /
``Unicorn Bootloader`` / ``Run image0, wait a moment, please!``。
指纹来源优先取块声明 ``assert: [{"type": "boot_fingerprint", "any_of": [...]}]``
（通过 ``ctx.block_specs`` 读到），其次 ``action.fingerprints``，最后内置默认。

**重发策略**（实测 69 发 5 次首轮无回显 ≈7%）：单次指纹窗口 15s；窗口内无指纹 →
重发一次；两次都无指纹 → ``fail``，detail 写明「口无响应」。总等待上限 30s
（复位到网络层恢复实测 10–20s，见调研「复位后重新入网耗时」），轮询 1s。

**结论口径**（领导裁决的统一状态口径，全块层一致）：

| 情形 | 块状态 |
|---|---|
| 指纹命中 | ``pass`` |
| 两次下发都成功但均无指纹 | ``fail``（帧发出去了、口无响应 = 测试失败） |
| 串口桥明确不可用 / ensure 失败 / 首次下发遇 409 闸门忙·台架不可达 | ``skipped``（台架前置不满足，不赖设备） |
| **首次下发 422/400（构帧/参数配置错误，帧根本没发出去）** | ``unknown``（配置错误，不是设备结论） |
| 会话日志读不到、或第二次下发失败导致协议没走完 | ``unknown``（没法判定 —— 绝不假 PASS） |
| 块内任何异常 | 由执行器记 ``fail``（``fn`` 不吞异常） |

**不做**：断电重上电（不可原子化）、``01H-F1``（那复位的是 CCO 自己）、把「复位成功」
等同于「已入网」（入网交由后续 ``nwk.*`` 块判定）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from case_runner.blocks.common import (  # noqa: F401 - 下面一并对旧调用方再导出
    DEFAULT_MODULE_MAPPING,
    EXTRAS_MODULE,
    ModuleCapability,
    coerce_float,
    coerce_int,
    module_capability,
)
from case_runner.core.executor import BlockResult, RunContext
from case_runner.core.models import BLOCK_FAIL, BLOCK_PASS, BLOCK_SKIPPED, BLOCK_UNKNOWN
from case_runner.core.registry import has_block, register_block

#: 块 id（与 ``data/blocks/reset_sta.json`` 一致）
BLOCK_RESET_STA = "reset.sta"

#: 示例/兜底判据：复位后板子自打的启动指纹（``RESEARCH-reset.md`` 实测原文）
DEFAULT_BOOT_FINGERPRINTS: Tuple[str, ...] = (
    "reset reason:",
    "SFT_GLB_RST",
    "Unicorn Bootloader",
    "Run image0, wait a moment, please!",
)

#: 复位命令原文（``xmodem_flash.enter_bootloader_command`` 同款）
RESET_COMMAND = "reboot"

DEFAULT_MAPPING_ID = DEFAULT_MODULE_MAPPING
DEFAULT_MODULE = "sta"
DEFAULT_RETRY = 1
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_FINGERPRINT_WINDOW_S = 15.0
DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_LOG_LINES = 400

#: 帧**没发出去**且属于「我们的配置/构帧错」→ ``unknown``（不是台架问题，也不是设备失败）
CONFIG_ERRORS = frozenset({"http_400", "http_422", "bad_response", "config_error"})


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def boot_fingerprints(params: Optional[Dict[str, Any]] = None,
                      ctx: Optional[RunContext] = None) -> Tuple[str, ...]:
    """取启动指纹集合：块声明 ``assert[].any_of`` → ``action.fingerprints`` → 内置默认。"""
    raw = dict(params or {})
    explicit = raw.get("fingerprints") or raw.get("any_of")
    if isinstance(explicit, (list, tuple)) and explicit:
        return tuple(str(item) for item in explicit if str(item).strip())
    if ctx is not None:
        spec = (ctx.block_specs or {}).get(BLOCK_RESET_STA)
        for decl in getattr(spec, "assert_", None) or []:
            if not isinstance(decl, dict):
                continue
            if str(decl.get("type") or "") != "boot_fingerprint":
                continue
            any_of = decl.get("any_of") or []
            picked = tuple(str(item) for item in any_of if str(item).strip())
            if picked:
                return picked
    return DEFAULT_BOOT_FINGERPRINTS


def match_boot_fingerprint(entries: Sequence[Dict[str, Any]],
                           fingerprints: Sequence[str]) -> Optional[Dict[str, Any]]:
    """在日志行里找启动指纹（大小写不敏感）；命中返回证据字典，否则 ``None``。"""
    needles = [(fp, fp.lower()) for fp in fingerprints if fp]
    for entry in entries or ():
        text = str(entry.get("text") or "")
        if not text:
            continue
        lowered = text.lower()
        for original, needle in needles:
            if needle in lowered:
                return {"fingerprint": original, "line": text,
                        "ts": entry.get("ts"), "seq": entry.get("seq")}
    return None


def send_failure_state(error: Any) -> Tuple[str, str]:
    """首次下发失败 → ``(块态, 说明)``。

    口径（领导裁决）：**帧根本没发出去**时不能判设备失败 ——
    构帧/参数配置错（400/422/bad_response）→ ``unknown``；其余（409 闸门忙、台架不可达、
    服务异常、无会话…）→ ``skipped``（台架前置不满足）。
    """
    code = str(error or "")
    if code in CONFIG_ERRORS:
        return BLOCK_UNKNOWN, "块参数/构帧配置错误（%s，非设备结论）" % code
    return BLOCK_SKIPPED, "台架前置未满足（%s）" % (code or "原因未给出")


def _stop_observations(params: Dict[str, Any], ctx: Optional[RunContext],
                       mapping_id: str) -> Dict[str, Any]:
    """复位前停掉本口观察任务（可选）。

    调研结论 ``RESEARCH-reset.md`` E.3：复位会打断该口正在跑的观察任务。但工作台
    **没有**「按 mapping 列出 / 停止观察」的 v2 端点（只有 ``/api/ai/operations/{id}/cancel``，
    需先知道 operation_id），因此这里只能走调用方注入的回调
    ``ctx.extras['stop_observations'](mapping_id)``；没注入就如实报告「未停」。
    """
    if not params.get("stop_observation"):
        return {"ok": True, "stopped": False, "detail": "未要求停观察（stop_observation=false）"}
    stopper = ctx.get("stop_observations") if ctx is not None else None
    if not callable(stopper):
        return {"ok": False, "stopped": False,
                "detail": "要求停观察但未注入 ctx.extras['stop_observations'] 回调"
                          "（工作台无按 mapping 停观察的端点）"}
    try:
        outcome = stopper(mapping_id)
    except Exception as exc:  # noqa: BLE001 - 停观察失败不阻断，但证据要留
        return {"ok": False, "stopped": False,
                "detail": f"停观察回调异常：{type(exc).__name__}: {exc}"}
    return {"ok": True, "stopped": True, "detail": f"已停观察：{outcome!r}"}


# --------------------------------------------------------------------------- #
# 块执行体
# --------------------------------------------------------------------------- #


def reset_sta(params: Optional[Dict[str, Any]] = None,
              ctx: Optional[RunContext] = None) -> BlockResult:
    """``reset.sta``：ensure 会话 → 发 ``reboot`` → 轮询日志找启动指纹（可重发一次）。"""
    raw = dict(params or {})
    mapping_id = str(raw.get("mapping_id") or DEFAULT_MAPPING_ID)
    module_name = str(raw.get("module") or DEFAULT_MODULE)
    command = str(raw.get("text") or RESET_COMMAND)
    retry = coerce_int(raw.get("retry"), DEFAULT_RETRY)
    timeout_s = coerce_float(raw.get("timeout_s"), DEFAULT_TIMEOUT_S)
    window_s = coerce_float(raw.get("fingerprint_window_s"), DEFAULT_FINGERPRINT_WINDOW_S)
    interval_s = coerce_float(raw.get("poll_interval_s"), DEFAULT_POLL_INTERVAL_S)
    log_lines = coerce_int(raw.get("lines"), DEFAULT_LOG_LINES)
    append_newline = bool(raw.get("append_newline", True))
    fingerprints = boot_fingerprints(raw, ctx)

    cap = module_capability(raw, ctx)
    clock = cap.clock
    sleeper = cap.sleeper

    # 0) 复位前：可选停观察 + 串口桥只读 preflight
    observation = _stop_observations(raw, ctx, mapping_id)
    port = cap.port_status(mapping_id)
    if port.get("ok") and port.get("usable") is False:
        return BlockResult(
            state=BLOCK_SKIPPED,
            detail=f"串口桥 {mapping_id} 不可用，复位无从下发：{port.get('detail')}",
            expected=list(fingerprints), actual=None,
        )

    # 1) ensure 会话（幂等）
    ensured = cap.ensure(mapping_id, module=module_name,
                         timeout_s=timeout_s)
    if not ensured.get("ok"):
        return BlockResult(
            state=BLOCK_SKIPPED,
            detail="模块会话建立失败（台架前置未满足）：%s：%s"
                   % (ensured.get("error"), ensured.get("detail", "")),
            expected=list(fingerprints), actual=None,
        )
    session_id = str(ensured.get("session_id") or "")

    # 2) 逐次下发 reboot，每次在窗口内等指纹
    overall_deadline = clock() + timeout_s
    attempts: List[Dict[str, Any]] = []
    observed_lines = 0
    for attempt in range(1, retry + 2):
        sent = cap.send(session_id, command, append_newline=append_newline,
                        timeout_s=timeout_s)
        if not sent.get("ok"):
            if attempt == 1:
                # 一次都没发出去 → 按失败码分流：构帧/配置错 = unknown；其余 = skipped
                state, why = send_failure_state(sent.get("error"))
                return BlockResult(
                    state=state,
                    detail="首次下发 %r 失败（%s）：%s：%s"
                           % (command, why, sent.get("error"), sent.get("detail", "")),
                    expected=list(fingerprints), actual=None,
                )
            # 已成功下发过，但重发失败导致协议没走完 → 不能判 fail，也不能判 pass
            return BlockResult(
                state=BLOCK_UNKNOWN,
                detail="第 %d 次下发 %r 失败，协议未走完，无法判定复位：%s：%s"
                       "（此前成功下发 %d 次均未见指纹；累计 RX %d 行）"
                       % (attempt, command, sent.get("error"), sent.get("detail", ""),
                          attempt - 1, observed_lines),
                expected=list(fingerprints), actual={"attempts": attempts},
            )
        sent_at = clock()
        attempt_deadline = min(sent_at + window_s, overall_deadline)
        hit: Optional[Dict[str, Any]] = None
        while True:
            logs = cap.logs(mapping_id, after_epoch=sent_at, lines=log_lines,
                            session_id=session_id)
            if not logs.get("ok"):
                return BlockResult(
                    state=BLOCK_UNKNOWN,
                    detail="会话日志不可读，无法判定复位（%s）：%s"
                           % (logs.get("error"), logs.get("detail", "")),
                    expected=list(fingerprints), actual=None,
                )
            entries = logs.get("entries") or []
            observed_lines = len(entries)
            hit = match_boot_fingerprint(entries, fingerprints)
            if hit is not None:
                break
            now = clock()
            if now >= attempt_deadline:
                break
            sleeper(min(interval_s, max(attempt_deadline - now, 0.0)))
        elapsed = clock() - sent_at
        attempts.append({
            "attempt": attempt,
            "sent_bytes": sent.get("sent_bytes"),
            "elapsed_s": round(elapsed, 3),
            "rx_lines": observed_lines,
            "matched": bool(hit),
        })
        if hit is not None:
            return BlockResult(
                state=BLOCK_PASS,
                detail="复位成功：第 %d 次下发 %r 后 %.1fs 命中启动指纹 %r"
                       "（%s；实发 %s 字节；%s）"
                       % (attempt, command, elapsed, hit["fingerprint"],
                          hit.get("line", "")[:80], sent.get("sent_bytes"),
                          observation.get("detail", "")),
                expected=list(fingerprints),
                actual={"fingerprint": hit["fingerprint"], "line": hit.get("line"),
                        "ts": hit.get("ts"), "attempt": attempt,
                        "elapsed_s": round(elapsed, 3)},
            )
        if clock() >= overall_deadline:
            break

    # 3) 所有下发都成功但都没指纹 → 口无响应
    return BlockResult(
        state=BLOCK_FAIL,
        detail="口无响应：%d 次下发 %r 均未见启动指纹（判据 %s；累计 RX %d 行；%s）"
               % (len(attempts), command, " / ".join(fingerprints),
                  observed_lines, observation.get("detail", "")),
        expected=list(fingerprints), actual={"attempts": attempts},
    )


# --------------------------------------------------------------------------- #
# 注册（幂等）
# --------------------------------------------------------------------------- #


def register() -> None:
    """注册 ``reset.sta`` 块执行体（幂等：已注册则跳过）。"""
    if not has_block(BLOCK_RESET_STA):
        register_block(BLOCK_RESET_STA, reset_sta)


register()


__all__ = [
    "BLOCK_RESET_STA",
    "EXTRAS_MODULE",
    "RESET_COMMAND",
    "DEFAULT_BOOT_FINGERPRINTS",
    "DEFAULT_MAPPING_ID",
    "DEFAULT_RETRY",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_FINGERPRINT_WINDOW_S",
    "CONFIG_ERRORS",
    "ModuleCapability",
    "module_capability",
    "boot_fingerprints",
    "match_boot_fingerprint",
    "send_failure_state",
    "reset_sta",
    "register",
]
