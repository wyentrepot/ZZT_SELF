"""参数初始化块（REQS-0043 F0-6 协议切换前置）：``param.init_01h_f2``。

下发 **AFN=01H-F2 参数区初始化**（清除所有从节点档案信息）：
帧格式出处 ``03_QGDW10376.2_全帧类型.md`` §4.2（AFN=01H 初始化帧：**F1/F2/F3 均
无数据单元**，上行为 AFN=00H 确认/否认帧）。

KB 定性（kind=case 的依据）：``用例库/CCO/台账/145_国网用例_AFN功能_初始化与查询_台账.md:42``
（行 47）把它当**正式 AFN 功能用例**判（步骤 3 下发 → 预期「CCO 端应答确认帧」；
步骤 4 查档案数 = 0）；``用例库/CCO/能力域/156_国网AFN功能回归清单_用例蒸馏.md:135``
「初始化三件套：01H-F1 硬件 / 01H-F2 参数 / 01H-F3 数据——都要求先加再清」。
同时它是**协议切换三件套的第一步**（``用例库/CCO/台账/150_山西用例_台账.md:49``
行 11 步骤 5：``01H-F2`` 参数初始化 → ``11H-F1`` 重建档（新协议类型）→ **STA 复位**
→ ``10H-F21`` 确认入网）——编排层把它当 setup 步用是流程级决定，块本身按 KB
定性 kind=case。

副作用与收场（side_effect=reversible，必须显式说明）：
- 清掉 CCO **全部从节点档案**（STA 随之离网；``157_黑白名单…蒸馏卡:43``：黑名单
  **不**随参数初始化清除）；恢复动作 = 重新建档（``archive.add``，需要时带新
  protocol）+ 组网复核（``nwk.topology``）。声明 ``_recovery_note`` 写明。
- **不隐式默认**（11H-F5 教训——危险动作不得藏默认行为）：本块协议层无参数
  （KB §4.2 无数据单元），块参数只有等待/会话类；「清什么」由 AFN 本身定义，
  在声明 title/说明里写死，不给任何「清一部分」的开关。

观测（零 pass/fail）：``命令`` / ``应答AFNFn``（00H-F1 确认 / 00H-F2 否认）/
``否认码`` / ``否认码文本`` / ``应答帧``。「初始化被确认」= 步骤 expect
（``{"op":"eq","field":"应答AFNFn","value":"00H-F1"}``）；否认或超时都是观测。
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from case_runner.blocks.gate import (
    _deny_from_frame,
    _env_error,
    _frame_entries,
    _invalid,
)
from case_runner.blocks.meter import _capability, block_params, norm_hex
from case_runner.capabilities.frames import FramesCapability
from case_runner.capabilities.simcon import SimconCapability
from case_runner.core.executor import PARAM_ASSERT, BlockResult
from case_runner.core.expect import OBS_TIMEOUT, Observation
from case_runner.core.models import BLOCK_UNKNOWN
from case_runner.core.registry import has_block, register_block

#: AFN=01H-F2 参数区初始化（全帧类型.md §4.2：无数据单元；上行为确认/否认帧）
INIT_AFN = "01"
INIT_FN = 2

#: 应答等待缺省秒数：expect_rules default 档 5s（查询/确认/设置类，expect_rules.json:24-26；
#: 01H 不在 per-Fn 规则表里 → default 档）
DEFAULT_EXPECT_TIMEOUT_S = 5.0

_ACTION_META_KEYS = frozenset(
    {"afn", "fn", "params", "recipe", "semantic", "cleanup", "assert", "tags"}
)


def _confirm_of(frame: Any) -> str:
    """应答帧 → ``AFNFn`` 记号（如 ``00H-F1``）；解不出 → ``""``。"""
    afn = fn = None
    for entry in _frame_entries(frame):
        name = str(entry.get("name") or "")
        if name == "AFN" and entry.get("raw") is not None:
            try:
                afn = int(entry.get("raw"))
            except (TypeError, ValueError):
                afn = None
        if name == "FN" and entry.get("raw") is not None:
            try:
                fn = int(entry.get("raw"))
            except (TypeError, ValueError):
                fn = None
    if afn is None or fn is None:
        return ""
    return "%02XH-F%d" % (afn, fn)


def param_init_01h_f2(params: Mapping[str, Any], ctx: Any = None) -> BlockResult:
    """``param.init_01h_f2``：下发 01H-F2 参数区初始化，观测确认/否认帧。

    只报观测：「初始化被确认（00H-F1）」「档案清空（10H-F1=0，先加再清口径）」
    由 Procedure 的步骤判据负责（KB 145 行 47：下发 → 确认 → 查数量=0）。
    本块不回查档案——那条判据用 ``archive.query``/``nwk.archive_count`` 编排。
    """
    action = dict(params or {})
    options = {
        key: value for key, value in block_params(action).items()
        if key not in (PARAM_ASSERT, "_cleanup")
    }
    expect_timeout = DEFAULT_EXPECT_TIMEOUT_S
    raw_timeout = options.get("expect_timeout")
    if raw_timeout not in (None, ""):
        try:
            expect_timeout = float(raw_timeout)
        except (TypeError, ValueError):
            return _invalid("expect_timeout 非法：%r（应 >0 秒数）" % (raw_timeout,))
        if expect_timeout <= 0:
            return _invalid("expect_timeout 非法：%r（应 >0 秒数）" % (raw_timeout,))

    frames_cap = _capability(ctx, "frames", FramesCapability, options)
    simcon = _capability(ctx, "simcon", SimconCapability, options)
    steps = [{
        "name": "参数区初始化（01H-F2，无数据单元）",
        "send": {"afn": INIT_AFN, "fn": INIT_FN, "params": {}},
        # 不锁 fn：确认（00H-F1）与否认（00H-F2）都是观测；deny 由 simcon 特判带码
        "expect": {"afn": "00", "dir": "up"},
        "expect_timeout": expect_timeout,
    }]
    result = simcon.verify(steps, max_poll_seconds=expect_timeout + 30.0)
    if not isinstance(result, Mapping):
        return _env_error({"error": "bad_response",
                           "detail": "verify 返回 %s" % type(result).__name__}, "参数初始化下发")

    matched = ""
    for step in result.get("steps") or []:
        if isinstance(step, Mapping) and step.get("matched"):
            matched = norm_hex(step.get("matched"))
            break
    values: Dict[str, Any] = {"命令": "01H-F2", "否认等待秒": expect_timeout}
    if not matched:
        error = str(result.get("error") or "")
        if error not in ("", "job_not_passed"):
            return _env_error(result, "参数初始化下发")
        detail = "预算内未收到 00H 确认/否认帧（初始化无回音——说不清，不判结论）"
        return BlockResult(
            state=BLOCK_UNKNOWN,
            detail=detail,
            observation=Observation(values=values, complete=False,
                                    reason_code=OBS_TIMEOUT, detail=detail),
        )

    decoded = frames_cap.decode(matched, protocol="1376.2")
    frame = decoded.get("frame") if isinstance(decoded, Mapping) else None
    label = _confirm_of(frame)
    values["应答AFNFn"] = label or "未知"
    values["应答帧"] = matched
    deny_code, deny_text = _deny_from_frame(frames_cap, matched)
    if deny_code:
        values["否认码"] = deny_code
        values["否认码文本"] = deny_text
    detail = "参数初始化（01H-F2）收到应答帧 %s" % (label or matched[:24])
    if deny_code:
        detail += "，为否认帧（否认码 %s：%s）" % (deny_code, deny_text)
    observation = Observation(values=values, complete=True, detail=detail)
    return BlockResult(state=BLOCK_UNKNOWN, detail=detail, observation=observation)


#: 块 id → 执行体（声明见 ``data/blocks/param_init_01h_f2.json``）
BLOCKS: Dict[str, Any] = {
    "param.init_01h_f2": param_init_01h_f2,
}


def register() -> None:
    """注册本文件块执行体（幂等，与 ``blocks/__init__.py`` 约定一致）。"""
    for block_id, fn in BLOCKS.items():
        if not has_block(block_id):
            register_block(block_id, fn)


register()

__all__ = [
    "BLOCKS",
    "DEFAULT_EXPECT_TIMEOUT_S",
    "INIT_AFN",
    "INIT_FN",
    "param_init_01h_f2",
    "register",
]
