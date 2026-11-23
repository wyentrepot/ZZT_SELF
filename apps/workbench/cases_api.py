"""workbench.cases_api —— 用例库 / 方案 / 台架快照 / 运行端点（``/api/cases/*``，REQS-0042 S4）。

本模块是「用例库」页面的**唯一后端**（REQS-0042 §3.6 API-1..4、§3.5、§3.7），只做四件事：

1. **装配**：读声明（块/事实/用例/方案 JSON，默认 ``libs/case_runner/data``）、校验用例入参、
   把能力替身、事实探测源与台架快照源注入执行上下文；
2. **预检**（D2-8）：``POST /api/cases/run`` 在**受理前**只探所选用例 ``needs`` 的并集，
   不满足 → 整轮 ``blocked`` + 缺项清单，**不建 job、不逐用例跑**（<1s 返回）；
3. **调度**：预检通过才把运行放到后台线程（复位 STA 30s、抄表几十秒），立即返回 ``job_id``，
   前端轮询 ``GET /api/cases/jobs/{job_id}``；
4. **落盘**：``<storage_dir>/case_runs/<run_id>/result.json``（D4-1/D4-4/D4-6 的证据口径），
   可按 ``run_id`` 回放。

判定逻辑一行都不在本文件
------------------------
四态结论与优先级 ``fail > blocked > inconclusive > pass`` **只有一处实现**：
``case_runner.core.executor.aggregate_verdict``（见 :func:`_aggregate_verdict` 的 import 说明）。
本文件只做「把内核的 ``CaseRunResult.to_dict()`` / ``RunOutcome.to_dict()`` 翻译成读模型」、
「把多条用例结论聚合成方案结论」、「调度」与「落盘」，**不重新判定任何块或事实**。

四态（REQS-0042 §3.5 / 变更 2 **D2-4**）
---------------------------------------
``pass`` / ``fail`` / ``blocked`` / ``inconclusive``；块态仍是
``pass|fail|skipped|unknown``，只在聚合处 ``skipped→blocked``、``unknown→inconclusive``。
断言层（``assertions[].outcome``）仍严格三态——本文件不扩断言层。

硬性语义（真实台架验证过的口径，禁止改动）
-----------------------------------------
- **绝不假 PASS**：``unknown`` / ``skipped`` 永不计入 ``counts.pass``；
  ``skipped`` 折成 ``blocked``（台架问题，不是产品问题），``unknown`` 折成 ``inconclusive``。
- **断点可读**：用例结果必须带 ``failed_block`` / ``blocked_block`` / ``unmet_facts``
  （后者每项含 ``state`` / ``detail`` / ``evidence``），UI 直接据此回答「断在哪个块、
  哪个事实没满足」。
- **资源未满足不崩**：台架不可达 → 块 ``skipped`` / 事实 ``unknown`` / 用例 ``blocked``；
  ``/api/cases/bench`` 照样返回快照（端口 + 8 事实三态）。只有「台架被另一个 job 占着」
  这种**入口层冲突**才回 409（台架是独占资源，与 ``/api/autotest/*`` 同口径）。
- **工作台地址不写死**：优先级 ``body.base_url`` > 环境变量 ``WORKBENCH_BASE`` >
  本次请求的 origin（``request.base_url``）。三者都没有才是空串 → 能力层报
  ``no_base_url`` → 块 skipped（绝不回退到任何写死端口）。

job 状态机（单一写者：后台线程；``state`` 只前进不回退）
-----------------------------------------------------
``queued`` --(线程启动)--> ``running`` --(正常/异常收尾)--> ``done``
                             ``running`` --(装配/落盘抛异常)--> ``failed``

- ``queued``：``POST /api/cases/run`` 已受理、线程未跑起来（瞬时态）
- ``running``：后台线程正在跑，``progress`` 递增（``done/total`` + 当前用例）
- ``done``：**终态**（含 verdict=fail / blocked / inconclusive，测试失败不是 job 失败）
- ``failed``：**终态**（job 自身异常：装配错误、落盘失败等；``error`` 给原因与类型）
- ``blocked``：**不是 job 态**，只出现在「预检阻断、未建 job」那条落盘记录上
  （``POST /api/cases/run`` 返回 ``accepted=false``），历史读回时如实保留。

``WORKBENCH_BASE`` 串号防护
--------------------------
能力层只认环境变量这条注入通道（``capabilities/*.py`` 的 ``env_var``）。本模块用一把
``RLock`` 保证「同一时刻只有一处改这个变量」：job 执行期**阻塞**持锁（整轮跑完才释放），
``/api/cases/bench`` 与预检用**非阻塞**尝试——台架正被一次运行占用时，快照如实写
「本轮不探测事实，避免污染其地址注入」，绝不静默用别人的地址去探自己的结论。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Mapping, Optional, Sequence

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from case_runner.blocks import loader as decl_loader
from case_runner.cli import (
    DEFAULT_RUN_TIMEOUT,
    PREFLIGHT_BUDGET_S,
    Bundle,
    CaseParamSpec,
    Env,
    PreflightResult,
    RunOutcome,
    UsageError,
    blocked_outcome,
    case_block_ids,
    case_param_spec,
    exit_code_for_verdict,
    load_bundle,
    new_run_id,
    probe_fact_params,
    run_case_with_deadline,
    run_preflight,
    skipped_outcome,
    suite_param_spec,
    timeout_outcome,
    track_running_blocks,
)
from case_runner.core.executor import (
    DECISION_TIMEOUT,
    aggregate_verdict,
    decision_entry,
    normalize_decision,
)
from case_runner.core.models import BlockSpec, CaseSpec, CaseStep, FactSpec, SuiteSpec
from case_runner.core.planner import FactsContext
from case_runner.core.registry import has_block, has_fact

# --------------------------------------------------------------------------- #
# 常量与状态口径
# --------------------------------------------------------------------------- #

#: 工作台地址的环境变量名（与 ``case_runner.capabilities.*`` / ``case_runner.cli`` 同一口径）
ENV_BASE_URL = "WORKBENCH_BASE"

#: ``GET /api/cases/bench`` 的快照探测总预算（秒）。
#:
#: 为什么不复用 ``PREFLIGHT_BUDGET_S=5s``：预检只探「本次所选用例 needs 的并集」（通常 2 条），
#: 5s 足够；而快照要**串行探完 8 条事实**，实测约 8–12s，5s 会把后 3 条截成 ``unknown``，
#: 进而让 ``ready=false`` → UI 误把运行按钮置灰（台架明明可用）。故快照单独给 20s。
BENCH_SNAPSHOT_BUDGET_S: float = 20.0

#: 运行结果落盘目录名（``<storage_dir>/case_runs/<run_id>/result.json``）
RUNS_DIRNAME = "case_runs"
RESULT_FILENAME = "result.json"

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
#: 预检阻断的落盘记录态（**不是** job 态：那条记录没有 job）
RUN_BLOCKED = "blocked"
JobState = Literal["queued", "running", "done", "failed", "blocked"]

#: 结论四态（REQS-0042 §3.5 / D2-4）：由内核给出，本模块只透传
Verdict = Literal["pass", "fail", "blocked", "inconclusive"]
VERDICTS: tuple[str, ...] = ("pass", "fail", "blocked", "inconclusive")
#: 块执行态（内核给出；四态结论由内核折算，本模块不折算）
BlockState = Literal["pass", "fail", "skipped", "unknown"]
#: 事实三态
FactState = Literal["yes", "no", "unknown"]

#: run_id / job_id 的路径安全形状（run_id 会拼进落盘路径，必须挡住 ``..`` / 分隔符）
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: 常用度排序权重（REQS-0042 D10-2：``common`` 日常回归 → ``normal`` → ``rare``）
USAGE_ORDER: dict[str, int] = {"common": 0, "normal": 1, "rare": 2}
#: 未声明常用度的用例排在最后（不是错误，只是「未分类」）
USAGE_UNKNOWN_RANK = len(USAGE_ORDER)

#: 分类维度（查询参数名 == `case.category` 的字段名，REQS-0042 D10-2）
CATEGORY_DIMENSIONS: tuple[str, ...] = ("usage", "domain", "scene", "region")

#: 用例声明里**未来字段**的容忍名单（D10-2 的 ``category`` / ``protocols`` 还没进
#: ``CaseSpec``，而 ``CaseSpec`` 是 ``extra="forbid"``）。命中这些键时本模块把它们剥掉
#: 再校验，分类信息从原文里读——这样「声明先加字段、libs 后加模型」的过渡期里
#: 用例**仍然列得出来**（见 :meth:`CasesService._tolerated_cases`）。
TOLERATED_CASE_FIELDS: frozenset[str] = frozenset(
    {"category", "protocols", "usage", "domain", "scene", "region"}
)

#: 等一把「地址注入锁」的最长时间（job 整轮持锁；预检/快照是非阻塞尝试）
DEFAULT_ENV_LOCK_TIMEOUT_S = 15.0


def _now() -> str:
    """本地时区的 ISO 时间（带偏移，前端直接可读）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _round1(value: float) -> float:
    return round(float(value), 1)


# --------------------------------------------------------------------------- #
# 错误响应（detail 统一中文；工作台错误处理器会再补 code/message/request_id）
# --------------------------------------------------------------------------- #


def _error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    """构造中文 detail 的 :class:`HTTPException`（``problems`` 逐条一次报全）。"""
    detail: dict[str, Any] = {"code": code, "message": message}
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            detail[key] = value
    return HTTPException(status_code=status_code, detail=detail)


# --------------------------------------------------------------------------- #
# 工作台地址解析（注入 > 环境变量 > 本次请求 origin；本模块不写死任何端口）
# --------------------------------------------------------------------------- #


def resolve_job_base_url(explicit: Optional[str], request_origin: str = "") -> tuple[str, str]:
    """返回 ``(base_url, 来源)``；来源 ∈ ``body`` / ``env`` / ``http`` / ``none``。

    **没有缺省地址**：三者都没有时返回 ``("", "none")``，由能力层报 ``no_base_url``
    （块 ``skipped`` / 事实 ``unknown``），绝不回退到写死的端口。
    """
    value = (explicit or "").strip()
    if value:
        return value.rstrip("/"), "body"
    from_env = (os.environ.get(ENV_BASE_URL) or "").strip()
    if from_env:
        return from_env.rstrip("/"), "env"
    origin = (request_origin or "").strip().rstrip("/")
    if origin:
        return origin, "http"
    return "", "none"


@contextlib.contextmanager
def _base_url_env(base_url: str) -> Iterator[None]:
    """执行期把本次 job 的地址灌进 ``WORKBENCH_BASE``，退出时**原样还原**。

    为什么走环境变量：块执行体（``archive`` / ``nwk`` / 事实探测体 ``facts_nwk``）自建
    能力实例时只认环境变量（``resolve_simcon(None)``），这是 ``case_runner`` 里既定的
    注入通道（见 ``capabilities/*.py`` 的 ``env_var`` 与 ``cli._base_url_env``）。
    本模块用「同时最多一个 job + 地址注入锁」保证这条进程级通道不被并发污染
    （见 :meth:`CasesService.env_lock`）。
    """
    if not base_url:
        yield
        return
    previous = os.environ.get(ENV_BASE_URL)
    os.environ[ENV_BASE_URL] = base_url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(ENV_BASE_URL, None)
        else:
            os.environ[ENV_BASE_URL] = previous


# --------------------------------------------------------------------------- #
# 读模型（公开契约：全部命名 Pydantic 模型，不用裸 dict）
# --------------------------------------------------------------------------- #


class BlockCounts(BaseModel):
    """块分态计数：``pass`` 只统计 ``state == pass``（``skipped``/``unknown`` 单独成列）。"""

    model_config = ConfigDict(populate_by_name=True)

    total: int = 0
    pass_count: int = Field(default=0, alias="pass")
    fail: int = 0
    skipped: int = 0
    unknown: int = 0


class CaseVerdictCounts(BaseModel):
    """用例结论分布（四态：``pass`` / ``fail`` / ``blocked`` / ``inconclusive``）。"""

    model_config = ConfigDict(populate_by_name=True)

    total: int = 0
    pass_count: int = Field(default=0, alias="pass")
    fail: int = 0
    blocked: int = 0
    inconclusive: int = 0


class FactValueView(BaseModel):
    """事实取值（三态 + 原因 + 证据）；``state=unknown`` 绝不折算成 ``yes``。"""

    state: FactState = "unknown"
    detail: str = ""
    evidence: list[str] = Field(default_factory=list)
    #: 预检缺项清单会带「为什么算缺项」（内核 ``PreflightResult.unmet`` 的原话）
    reason: str = ""
    #: 探测入参（同一事实不同参数会得到不同结论，落盘后要能复现这次是拿什么探的）
    params: dict[str, Any] = Field(default_factory=dict)


class AssertionView(BaseModel):
    """一条断言结论（复用 ``test_automation.models.AssertionResult.to_dict()`` 口径，仍三态）。"""

    id: str = ""
    run_id: str = ""
    assertion_id: str = ""
    outcome: Verdict = "inconclusive"
    expected: Any = None
    actual: Any = None
    evidence_ids: list[str] = Field(default_factory=list)
    message: str = ""


class BlockResultView(BaseModel):
    """单块执行记录（``state`` 是块执行态，``outcome`` 是映射后的四态结论）。"""

    block_id: str
    state: BlockState
    outcome: Verdict
    detail: str = ""
    facts: dict[str, FactValueView] = Field(default_factory=dict)
    assertion_id: str = ""
    message: str = ""


class CaseRunView(BaseModel):
    """一个用例的执行结果（UI 的「断点可视化」全靠这里的断点字段）。

    ``decisions`` / ``self_healed`` / ``interventions`` 直接来自内核
    ``CaseRunResult.to_dict()``（D4-4 的单一来源）；``interventions`` 恒为空数组。
    """

    case_id: str
    title: str = ""
    run_id: str = ""
    verdict: Verdict = "inconclusive"
    failed_block: str = ""
    blocked_block: str = ""
    #: 断点所在的**步骤**（步骤实例化后同块可以多次，块 id 定位不到具体那一步）
    failed_step: str = ""
    blocked_step: str = ""
    stop_on_fail: bool = False
    counts: BlockCounts = Field(default_factory=BlockCounts)
    unmet_facts: dict[str, FactValueView] = Field(default_factory=dict)
    blocks: list[BlockResultView] = Field(default_factory=list)
    #: 步骤摘要（``{"step_id","block_id","state","outcome","on_fail"}``，执行序）
    steps: list[dict[str, Any]] = Field(default_factory=list)
    assertions: list[AssertionView] = Field(default_factory=list)
    #: D4-4 自动决策（自愈 / 重试 / 等待 / 超时；每条带 kind/attempts/reason）
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    #: D4-4 自愈留痕（扁平，含 step_id/block_id；属自动决策，不是「介入」）
    self_healed: list[dict[str, Any]] = Field(default_factory=list)
    #: D4-4 人/AI 介入记录：**恒为空数组**（非空即本次运行不成立）
    interventions: list[dict[str, Any]] = Field(default_factory=list)
    skipped_reason: str = ""
    not_run_reason: str = ""
    stuck_block: str = ""
    exit_code: Optional[int] = None
    duration_s: float = 0.0
    params: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_outcome(cls, outcome: RunOutcome) -> "CaseRunView":
        """内核 ``RunOutcome.to_dict()`` → 读模型（不做任何字段改写，四态原样透传）。"""
        payload = outcome.to_dict()
        payload["title"] = outcome.case.title
        return cls.model_validate(payload)


class JobProgress(BaseModel):
    """job 进度快照（UI 轮询这一段画进度条 + 「当前在跑哪条用例」）。"""

    total: int = 0
    done: int = 0
    current_case_id: str = ""
    current_case_title: str = ""
    percent: int = 0


class BenchPortView(BaseModel):
    """台架一个逻辑口（mapping）的可用性快照（来自 ``/api/serial-bridge/status``）。"""

    mapping_id: str = ""
    port_id: str = ""
    label: str = ""
    enabled: bool = True
    usable: bool = False
    channel_ok: bool = False
    workbench_ready: bool = False
    windows_com: str = ""
    link: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BenchFactView(BaseModel):
    """台架快照里的一个事实三态（``declared=false`` = 声明里有、注册表里没有）。"""

    fact_id: str
    title: str = ""
    state: FactState = "unknown"
    detail: str = ""
    evidence: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    declared: bool = True


class BenchSnapshot(BaseModel):
    """``GET /api/cases/bench``：台架就绪快照 = 逻辑口可用性 + 全部事实三态（D2-8）。

    ``ready`` = **启用口全 usable** 且 **用例库真正需要的事实（全库用例 ``needs`` 的并集）
    全 yes**。它回答的是「这套用例在这台架上有几成能跑」，**不是**「每个事实都绿」——
    ``archive.contains`` / ``nwk.*`` / ``sta.meter_addr_known`` 这类**信息性事实**为
    ``unknown`` 不影响 ``ready``（真机回归：它们在学习态/未注入能力时恒 unknown，若算进
    门禁会让 UI 无谓置灰）。

    真正的「这一轮能不能跑」仍由 ``POST /api/cases/run`` 的按需预检回答（只看**所选用例**
    ``needs`` 的并集）；``unmet_required`` 给出按库口径缺哪几条。
    """

    ready: bool = False
    ports_ok: bool = False
    facts_ok: bool = False
    #: 用例库真正需要的事实（全库用例 ``needs`` 的并集，去重保序）
    required_facts: list[str] = Field(default_factory=list)
    #: 上述必需事实里不成立的（``no`` / ``unknown``），键同 ``unmet_facts``
    unmet_required: dict[str, "FactValueView"] = Field(default_factory=dict)
    ports: list[BenchPortView] = Field(default_factory=list)
    ports_total: int = 0
    ports_usable: int = 0
    ports_disabled: int = 0
    bridge_ok: bool = False
    bridge_error: str = ""
    bridge_supervisor: dict[str, Any] = Field(default_factory=dict)
    facts: list[BenchFactView] = Field(default_factory=list)
    facts_total: int = 0
    fact_counts: dict[str, int] = Field(default_factory=dict)
    unmet_facts: dict[str, FactValueView] = Field(default_factory=dict)
    #: 事实探测被跳过的原因（台架正被一次运行占用时**不探**，避免污染其地址注入）
    probe_skipped: str = ""
    base_url: str = ""
    base_url_source: str = "none"
    probed_at: str = ""
    duration_s: float = 0.0
    problems: list[str] = Field(default_factory=list)

    @property
    def ports_grey(self) -> list[str]:
        """不可用的逻辑口名（UI 置灰 + 给原因用）。"""
        return [port.mapping_id or port.port_id for port in self.ports
                if port.enabled and not port.usable]


class RunRecordView(BaseModel):
    """一次运行（= 一个 job）的完整读模型：job 端点、历史结果端点、落盘文档共用。

    落盘 ``result.json`` 就是本模型的 ``model_dump(mode="json", by_alias=True)``，
    所以 ``GET /api/cases/runs/{run_id}`` 读回来的结构与 ``GET /api/cases/jobs/{job_id}``
    逐字一致（D4-1 / D4-4 / D4-6 的证据口径）。
    """

    model_config = ConfigDict(populate_by_name=True)

    job_id: str = ""
    run_id: str
    state: JobState
    accepted: bool = True
    suite_id: Optional[str] = None
    case_ids: list[str] = Field(default_factory=list)
    base_url: str = ""
    base_url_source: str = "none"
    #: D4-1：**启动瞬间冻结**的入参快照（执行期只读，落盘逐字相等）
    params: dict[str, Any] = Field(default_factory=dict)
    resolved_params: dict[str, Any] = Field(default_factory=dict)
    stop_on_fail: bool = False
    timeout_s: float = 0.0
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    duration_s: float = 0.0
    progress: JobProgress = Field(default_factory=JobProgress)
    verdict: Optional[Verdict] = None
    exit_code: Optional[int] = None
    counts: BlockCounts = Field(default_factory=BlockCounts)
    case_counts: CaseVerdictCounts = Field(default_factory=CaseVerdictCounts)
    results: list[CaseRunView] = Field(default_factory=list)
    #: D4-4 自动决策（run 级预检 + 逐用例，扁平；单一来源见 :meth:`CasesService.view`）
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    #: D4-4 人/AI 介入记录：**恒为空数组**
    interventions: list[dict[str, Any]] = Field(default_factory=list)
    self_healed: list[dict[str, Any]] = Field(default_factory=list)
    #: 台架快照：启动瞬间的逻辑口可用性 + 预检探到的事实三态（D4-6 证据）
    bench: dict[str, Any] = Field(default_factory=dict)
    preflight: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    result_path: str = ""
    problems: list[str] = Field(default_factory=list)


class CaseCategory(BaseModel):
    """用例分类（REQS-0042 D10-2）：全部字段缺省 = **未分类**（不是错误）。"""

    usage: str = ""     # common / normal / rare（常用度）
    domain: str = ""    # CCO / STA / ECU / bench（设备域）
    scene: str = ""     # 组网 / 抄表 …（业务场景）
    region: str = ""    # 国网通用 / 南网通用 / 省份

    @property
    def classified(self) -> bool:
        return any(getattr(self, name).strip() for name in CATEGORY_DIMENSIONS)


class CaseView(BaseModel):
    """用例目录条目：``title`` + ``steps``（do/expect，供 UI 展开）+ 块清单 + 分类。"""

    id: str
    title: str
    steps: list[CaseStep] = Field(default_factory=list)
    blocks: list[str] = Field(default_factory=list)
    block_count: int = 0
    step_count: int = 0
    needs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    #: D10-2 分类（字段缺失 → 四个维度全空 = 未分类，仍可列出）
    category: CaseCategory = Field(default_factory=CaseCategory)
    protocols: list[str] = Field(default_factory=list)
    classified: bool = False
    #: 被「未来字段容忍」剥掉的声明键（过渡期可见性：声明已加、模型还没加）
    tolerated_fields: list[str] = Field(default_factory=list)


class CaseListResponse(BaseModel):
    """``GET /api/cases``：用例目录（``problems`` 非空表示声明有问题，UI 需提示）。"""

    cases: list[CaseView] = Field(default_factory=list)
    total: int = 0
    classified: int = 0
    unclassified: int = 0
    filters: dict[str, str] = Field(default_factory=dict)
    sort: str = ""
    data_dir: str = ""
    problems: list[str] = Field(default_factory=list)


class BlockActionView(BaseModel):
    """块 ``action`` 摘要（``kind`` 区分构帧 / 配方 / 语义 / 台架动作）。"""

    kind: str = "unknown"
    afn: Optional[str] = None
    fn: Optional[int] = None
    recipe: Optional[str] = None
    semantic: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class BlockView(BaseModel):
    """块目录条目；``assert``（别名）按 REQS §10.14 裁定 1 = 文档/UI 元数据，不驱动判定。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    needs: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)
    assert_: list[dict[str, Any]] = Field(default_factory=list, alias="assert")
    assert_types: list[str] = Field(default_factory=list)
    action: BlockActionView = Field(default_factory=BlockActionView)
    tags: list[str] = Field(default_factory=list)
    runnable: bool = False


class BlockListResponse(BaseModel):
    """``GET /api/cases/blocks``：块目录。"""

    blocks: list[BlockView] = Field(default_factory=list)
    total: int = 0
    data_dir: str = ""
    problems: list[str] = Field(default_factory=list)


class FactView(BaseModel):
    """事实目录条目（只读观测；``required`` 是该事实的必需参数名）。"""

    id: str
    title: str
    probe: str
    params_schema: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    registered: bool = False


class FactListResponse(BaseModel):
    """``GET /api/cases/facts``：事实目录。"""

    facts: list[FactView] = Field(default_factory=list)
    total: int = 0
    data_dir: str = ""
    problems: list[str] = Field(default_factory=list)


class SuiteView(BaseModel):
    """方案（= 用户勾选的用例组合；落盘 ``data/case_suites/<id>.json``，文件可 diff）。"""

    id: str
    name: str
    case_ids: list[str] = Field(default_factory=list)
    stop_on_fail: bool = False
    notes: str = ""

    @classmethod
    def from_spec(cls, spec: SuiteSpec) -> "SuiteView":
        return cls(
            id=spec.id,
            name=spec.name,
            case_ids=list(spec.case_ids),
            stop_on_fail=bool(spec.stop_on_fail),
            notes=spec.notes,
        )


class SuiteListResponse(BaseModel):
    """``GET /api/cases/suites``：方案列表。"""

    suites: list[SuiteView] = Field(default_factory=list)
    total: int = 0
    data_dir: str = ""
    problems: list[str] = Field(default_factory=list)


class SuiteSaveResponse(BaseModel):
    """``POST /api/cases/suites`` 结果（``replaced=true`` 表示覆盖了同名方案）。"""

    suite: SuiteView
    path: str
    replaced: bool = False


class SuiteDeleteResponse(BaseModel):
    """``DELETE /api/cases/suites/{suite_id}`` 结果。"""

    deleted: bool = True
    suite_id: str
    path: str


class RunRequest(BaseModel):
    """``POST /api/cases/run`` 请求体：``suite_id`` 与 ``case_ids`` **二者择一**。

    ``timeout``：整轮硬上限（秒，D2-6 / D4-5，缺省 600，``0`` 关闭）。
    ``no_preflight``：跳过预检（诊断用，与 CLI ``--no-preflight`` 同口径）。
    """

    model_config = ConfigDict(extra="forbid")

    suite_id: Optional[str] = None
    case_ids: Optional[list[str]] = None
    params: dict[str, Any] = Field(default_factory=dict)
    base_url: Optional[str] = None
    timeout: Optional[float] = None
    no_preflight: bool = False


class RunResponse(BaseModel):
    """``POST /api/cases/run`` 的受理回执。

    - 预检通过：``accepted=true`` / ``state=queued`` / 带 ``job_id``（异步执行）；
    - 预检不满足：``accepted=false`` / ``state=blocked`` / ``verdict=blocked``，
      **不建 job**（``job_id`` 为空），``unmet_facts`` 逐项点名缺什么，
      并已按同一个 ``run_id`` 落盘证据（``result_path`` 可读）。
    """

    accepted: bool = False
    state: JobState = JOB_QUEUED
    verdict: Optional[Verdict] = None
    job_id: str = ""
    run_id: str = ""
    suite_id: Optional[str] = None
    case_ids: list[str] = Field(default_factory=list)
    base_url: str = ""
    base_url_source: str = "none"
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 0.0
    preflight: dict[str, Any] = Field(default_factory=dict)
    unmet_facts: dict[str, FactValueView] = Field(default_factory=dict)
    problems: list[str] = Field(default_factory=list)
    reason: str = ""
    bench: dict[str, Any] = Field(default_factory=dict)
    result_path: str = ""
    job_url: str = ""
    result_url: str = ""


# --------------------------------------------------------------------------- #
# 内部 job 状态（可变；对外一律经 :class:`RunRecordView` 快照）
# --------------------------------------------------------------------------- #


@dataclass
class _Job:
    """一次运行的内存态（单一写者是后台线程；预检阻断的记录不注册进 ``_jobs``）。"""

    job_id: str
    run_id: str
    suite: Optional[SuiteSpec]
    cases: list[CaseSpec]
    case_ids: list[str]
    base_url: str
    base_url_source: str
    params: dict[str, Any]
    stop_on_fail: bool
    timeout_s: float
    preflight: PreflightResult = field(default_factory=PreflightResult)
    facts_ctx: Any = None
    bench: dict[str, Any] = field(default_factory=dict)
    state: str = JOB_QUEUED
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    started_monotonic: float = 0.0
    finished_monotonic: float = 0.0
    current_case_id: str = ""
    current_case_title: str = ""
    outcomes: list[RunOutcome] = field(default_factory=list)
    run_decisions: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    result_path: str = ""

    @property
    def suite_id(self) -> Optional[str]:
        return self.suite.id if self.suite is not None else None


def _sum_counts(items: Sequence[Mapping[str, Any]]) -> BlockCounts:
    """块计数求和（算术聚合，不是判定）。"""
    total = sum(int(item.get("total", 0)) for item in items)
    pass_count = sum(int(item.get("pass", 0)) for item in items)
    return BlockCounts(
        total=total,
        pass_count=pass_count,
        fail=sum(int(item.get("fail", 0)) for item in items),
        skipped=sum(int(item.get("skipped", 0)) for item in items),
        unknown=sum(int(item.get("unknown", 0)) for item in items),
    )


def _count_verdicts(verdicts: Iterable[str]) -> CaseVerdictCounts:
    """用例结论分布（算术聚合，不是判定；四态各自成列）。"""
    seen = list(verdicts)
    counts = CaseVerdictCounts(total=len(seen))
    for verdict in seen:
        if verdict == "pass":
            counts.pass_count += 1
        elif verdict == "fail":
            counts.fail += 1
        elif verdict == "blocked":
            counts.blocked += 1
        else:
            counts.inconclusive += 1
    return counts


def _aggregate_verdict(verdicts: Sequence[str]) -> Optional[Verdict]:
    """方案级结论 = 各用例结论的聚合。

    **单一实现**（REQS-0042 §3.5，改动点见 :func:`aggregate_verdict` 的 docstring）：
    优先级 ``fail > blocked > inconclusive > pass`` 只在 ``core/executor.py`` 写一次，
    本模块直接调它。空列表 → ``None``（没有跑过任何用例，**绝不给 pass**）。
    """
    if not verdicts:
        return None
    return aggregate_verdict(verdicts)  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# 分类（REQS-0042 D10-2）：dict 形态与扁平形态都认，字段缺失 → 未分类
# --------------------------------------------------------------------------- #


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip() if isinstance(value, str) else ""


def _protocol_list(value: Any) -> list[str]:
    """``protocols`` 归一：列表 / 逗号串 / 单值都认，去空去重保序。"""
    if value is None:
        return []
    raw: list[Any]
    if isinstance(value, str):
        raw = re.split(r"[,，、/]", value)
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = [value]
    out: list[str] = []
    for item in raw:
        token = _text(item)
        if token and token not in out:
            out.append(token)
    return out


def _classification(
    spec: CaseSpec, raw: Mapping[str, Any]
) -> tuple[CaseCategory, list[str]]:
    """从声明里读出分类：**模型字段优先，原文兜底**，两种形态都认。

    - dict 形态：``{"category": {"usage": ..., "domain": ..., "scene": ..., "region": ...}}``；
    - 扁平形态：``{"usage": ..., "domain": ..., "scene": ..., "region": ...}``；
    - 字段缺失（现在还没有）→ 该维度空串 = 未分类，仍能列出。
    """

    def _pick(name: str) -> str:
        value = getattr(spec, name, None)
        token = _text(value)
        if token:
            return token
        return _text((raw or {}).get(name))

    category_raw = getattr(spec, "category", None)
    if not isinstance(category_raw, Mapping):
        category_raw = (raw or {}).get("category")
    nested: Mapping[str, Any] = category_raw if isinstance(category_raw, Mapping) else {}
    category = CaseCategory(
        usage=_text(nested.get("usage")) or _pick("usage"),
        domain=_text(nested.get("domain")) or _pick("domain"),
        scene=_text(nested.get("scene")) or _pick("scene"),
        region=_text(nested.get("region")) or _pick("region"),
    )
    protocols_raw = getattr(spec, "protocols", None)
    if protocols_raw is None:
        protocols_raw = (raw or {}).get("protocols")
    return category, _protocol_list(protocols_raw)


def _wanted(text: Optional[str]) -> list[str]:
    """查询参数 → 期望值列表（支持逗号分隔多值；空 = 不筛）。"""
    if not text:
        return []
    return [token.strip().lower() for token in re.split(r"[,，、]", str(text)) if token.strip()]


def _match_case(view: CaseView, wanted: Mapping[str, list[str]], protocols: list[str]) -> bool:
    """按分类维度过滤（大小写不敏感；未分类的用例只在「不筛该维度」时出现）。"""
    for dimension in CATEGORY_DIMENSIONS:
        expected = wanted.get(dimension) or []
        if not expected:
            continue
        actual = _text(getattr(view.category, dimension)).lower()
        if actual not in expected:
            return False
    if protocols:
        actual_protocols = {item.lower() for item in view.protocols}
        if not (actual_protocols & set(protocols)):
            return False
    return True


def _sort_key(view: CaseView, sort: str) -> tuple[Any, ...]:
    """排序键（D10-2）：``name`` 按标题；``usage``（缺省）按常用度降序。

    同一常用度档内**保持声明顺序**（不拿标题二次排序）：声明文件顺序就是作者
    给的推荐顺序，乱序列表比「按中文码点排」更好读。
    """
    if sort == "name":
        return ((view.title or view.id).strip(), view.id)
    return (USAGE_ORDER.get(_text(view.category.usage).lower(), USAGE_UNKNOWN_RANK),)


# --------------------------------------------------------------------------- #
# 目录读模型构造（声明 JSON → 读模型；零判定）
# --------------------------------------------------------------------------- #


def _action_view(action: Mapping[str, Any]) -> BlockActionView:
    """块 ``action`` → 摘要（构帧 / 配方 / 语义 / 台架动作四类）。"""
    raw = dict(action or {})
    nested = raw.get("params")
    params = dict(nested) if isinstance(nested, Mapping) else {}
    if "afn" in raw:
        kind = "frame"
    elif "recipe" in raw:
        kind = "recipe"
    elif "semantic" in raw:
        kind = "semantic"
    else:
        kind = str(raw.get("kind") or "unknown")

    afn = raw.get("afn")
    fn = raw.get("fn")
    recipe = raw.get("recipe")
    semantic = raw.get("semantic")
    if kind == "frame":
        summary = "AFN=%s / Fn=%s，参数 %d 项" % (
            afn if afn is not None else "?",
            fn if fn is not None else "?",
            len(params),
        )
    elif kind == "recipe":
        summary = "recipe=%s，参数 %d 项" % (recipe, len(params))
    elif kind == "semantic":
        summary = "semantic=%s，参数 %d 项" % (semantic, len(params))
    else:
        summary = "kind=%s，参数 %d 项" % (kind, len(params))

    return BlockActionView(
        kind=kind,
        afn=None if afn is None else str(afn),
        fn=int(fn) if isinstance(fn, int) and not isinstance(fn, bool) else None,
        recipe=None if recipe is None else str(recipe),
        semantic=None if semantic is None else str(semantic),
        params=params,
        summary=summary,
    )


def _case_view(
    case: CaseSpec,
    category: CaseCategory,
    protocols: Sequence[str],
    tolerated_fields: Sequence[str] = (),
) -> CaseView:
    return CaseView(
        id=case.id,
        title=case.title,
        steps=list(case.steps),
        blocks=list(case.blocks),
        block_count=len(case.blocks),
        step_count=len(case.effective_steps()),
        needs=list(case.needs),
        tags=list(case.tags),
        category=category,
        protocols=list(protocols),
        classified=category.classified,
        tolerated_fields=list(tolerated_fields),
    )


def _block_view(spec: BlockSpec) -> BlockView:
    return BlockView(
        id=spec.id,
        title=spec.title,
        needs=list(spec.needs),
        provides=list(spec.provides),
        assert_=[dict(item) for item in spec.assert_],
        assert_types=[
            str(item.get("type"))
            for item in spec.assert_
            if isinstance(item, Mapping) and item.get("type")
        ],
        action=_action_view(spec.action),
        tags=list(spec.tags),
        runnable=has_block(spec.id),
    )


def _fact_view(spec: FactSpec) -> FactView:
    return FactView(
        id=spec.id,
        title=spec.title,
        probe=spec.probe,
        params_schema=dict(spec.params_schema),
        required=decl_loader.required_params(spec),
        registered=has_fact(spec.id),
    )


# --------------------------------------------------------------------------- #
# 台架快照的数据源（默认走同进程串口桥状态，不发起 HTTP、不写死端口）
# --------------------------------------------------------------------------- #


def _default_bridge_snapshot() -> dict[str, Any]:
    """默认台架快照源：同进程调用串口桥的 ``/api/serial-bridge/status`` 实现。

    为什么同进程而不是 HTTP：``/api/cases/bench`` 是 UI 每次进页面都会拉的轻量快照，
    走进程内读（配置 + 状态文件 + 工作台映射）**不依赖端口、不会自打请求**、也不会
    与单 worker 的事件循环互相等待；能力层（块/事实）仍然走 HTTP，两者读的是同一份
    ``config/serial_bridge.json`` 与 ``status.json``。
    """
    from . import serial_bridge_api  # 同包惰性导入：避免与 app 装配顺序耦合

    snapshot = serial_bridge_api._snapshot(None, None)
    return snapshot if isinstance(snapshot, dict) else {}


# --------------------------------------------------------------------------- #
# 服务（装配 + 预检 + 调度 + 落盘）
# --------------------------------------------------------------------------- #


class CasesService:
    """``/api/cases/*`` 的服务体：声明装载、台架快照、预检、job 生命周期、结果落盘。"""

    def __init__(
        self,
        storage_dir: Path,
        *,
        data_dir: Optional[Path] = None,
        extras: Optional[Mapping[str, Any]] = None,
        fact_probe: Any = None,
        register_domain: bool = True,
        bridge_snapshot_provider: Any = None,
        env_lock_timeout_s: float = DEFAULT_ENV_LOCK_TIMEOUT_S,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.data_dir = Path(data_dir) if data_dir is not None else decl_loader.DATA_DIR
        #: 注入的能力替身（``{"simcon": fake, "simeter": fake, "module": fake}``），
        #: 与 ``case_runner.cli.main(extras=...)`` 是同一个装配位（测试/共享句柄用）
        self.extras: dict[str, Any] = dict(extras or {})
        #: 注入的事实探测体（``(fact_id, params) -> FactValue|str|bool``），None = 走注册表
        self.fact_probe = fact_probe
        self.register_domain = bool(register_domain)
        #: 台架端口快照源（``() -> dict``，形状同 ``/api/serial-bridge/status``）
        self.bridge_snapshot_provider = bridge_snapshot_provider
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._active: str = ""
        #: ``WORKBENCH_BASE`` 注入锁（见模块说明「串号防护」）
        self.env_lock = threading.RLock()
        self.env_lock_timeout_s = max(float(env_lock_timeout_s), 0.0)
        #: 被「未来字段容忍」剥掉键的用例：``{case_id: [字段...]}``
        self._tolerated: dict[str, list[str]] = {}

    # -- 声明装载 ----------------------------------------------------------- #

    def bundle(self) -> Bundle:
        """每次请求重新装载声明（1-4ms）：方案保存/用例改 JSON 后立即生效，无需重启。

        在 ``load_bundle`` 之上补两件事（都只影响「**列得出来 / 跑得起来**」，
        不碰任何判定）：

        1. :meth:`_restore_categories`：``loader.load_all`` 的 strict 分支在**任一**
           声明文件有问题时会抛异常，于是「该类别之后的所有类别整体丢弃」——
           一个用例写了未来字段，整套用例目录就空了。这里用公开的逐类 loader 把
           丢掉的条目补回来，``problems`` 去重后照旧报（不吞问题）；
        2. :meth:`_tolerated_cases`：**未来字段容忍**——声明里已经写了
           ``category`` / ``protocols`` 但 ``CaseSpec`` 还没这些字段时，剥掉再校验，
           这样「声明先加、模型后加」的过渡期里用例仍能列出与运行。
        """
        try:
            bundle = load_bundle(
                self.data_dir,
                register_domain=self.register_domain,
                require_registered=False,
            )
        except UsageError as exc:
            raise _error(
                409,
                "declaration_root",
                "声明根目录不可用：%s" % "; ".join(exc.problems),
                problems=exc.problems,
            ) from None
        self._restore_categories(bundle)
        self._tolerated_cases(bundle)
        return bundle

    def _raw_declarations(
        self, dirname: str
    ) -> dict[str, tuple[Path, dict[str, Any]]]:
        """读一类声明原文 ``{id: (路径, 原文)}``（分类 / 容忍 / 来源回填都读它）。"""
        root = decl_loader.resolve_dir(self.data_dir, dirname)
        out: dict[str, tuple[Path, dict[str, Any]]] = {}
        for path in decl_loader.iter_declarations(root):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(raw, dict) and raw.get("id"):
                out[str(raw["id"])] = (path, raw)
        return out

    def _raw_cases(self) -> dict[str, dict[str, Any]]:
        """读用例声明原文 ``{case_id: raw}``（分类与容忍都读它，读不到就跳过）。"""
        return {case_id: raw for case_id, (_path, raw) in self._raw_declarations(decl_loader.CASES_DIRNAME).items()}

    def _restore_categories(self, bundle: Bundle) -> None:
        """把「因 loader strict 分支而整类丢失」的声明补回来（见 :meth:`bundle`）。"""
        report = bundle.report
        loaders = (
            ("blocks", decl_loader.BLOCKS_DIRNAME, decl_loader.load_blocks),
            ("facts", decl_loader.FACTS_DIRNAME, decl_loader.load_facts),
            ("cases", decl_loader.CASES_DIRNAME, decl_loader.load_cases),
            ("suites", decl_loader.SUITES_DIRNAME, decl_loader.load_suites),
        )
        for name, dirname, loader in loaders:
            if getattr(report, name):
                continue  # 这一类的加载没被中断，保持 loader 的结果
            raw = self._raw_declarations(dirname)
            if not raw:
                continue  # 目录本来就没有声明（空目录不是错误）
            try:
                restored = list(loader(self.data_dir, strict=False))
            except Exception:  # noqa: BLE001 - 补不回来就保持原样（problems 里照旧有原因）
                restored = []
            if not restored:
                continue
            setattr(report, name, restored)
            sources = report.sources.setdefault(name, {})
            for spec_id, (path, _raw) in raw.items():
                sources.setdefault(spec_id, path.as_posix())
        # loader 的 strict 分支会把同一批 problems 反复累加（拷贝后 extend），去重保序
        report.problems[:] = list(dict.fromkeys(report.problems))

    def _tolerated_cases(self, bundle: Bundle) -> None:
        """把「只因为未来字段而校验失败」的用例救回来（就地补进 ``bundle.report``）。

        判定很保守：**只有**多出来的键全部落在 :data:`TOLERATED_CASE_FIELDS` 里、
        并且剥掉这些键后能通过 ``CaseSpec`` 校验，才认；
        其它任何问题（缺 ``steps``、id 重复、类型错）照旧留在 ``problems`` 里报。
        """
        self._tolerated = {}
        raw_cases = self._raw_cases()
        known = {spec.id for spec in bundle.cases}
        fields = set(CaseSpec.model_fields)
        for case_id, raw in raw_cases.items():
            if case_id in known:
                continue
            extra_keys = [str(key) for key in raw if str(key) not in fields]
            if not extra_keys:
                continue
            if any(key not in TOLERATED_CASE_FIELDS for key in extra_keys):
                continue
            stripped = {key: value for key, value in raw.items() if str(key) not in extra_keys}
            try:
                spec = CaseSpec.model_validate(decl_loader.resolve_declaration_defaults(stripped))
            except Exception:  # noqa: BLE001 - 救不回来就当成普通声明问题（problems 里照旧报）
                continue
            bundle.report.cases.append(spec)
            # 只摘掉**这个文件里这些键**产生的校验问题，别的问题一个字都不动
            source = str(self.data_dir)
            bundle.report.problems[:] = [
                problem
                for problem in bundle.report.problems
                if not self._is_tolerated_problem(problem, case_id, extra_keys, source)
            ]
            self._tolerated[case_id] = extra_keys
            known.add(case_id)

    @staticmethod
    def _is_tolerated_problem(
        problem: str, case_id: str, extra_keys: Sequence[str], source: str
    ) -> bool:
        """这条校验问题是不是「就因为这些未来字段」产生的（pydantic extra_forbidden）。"""
        if "Extra inputs are not permitted" not in problem:
            return False
        if not any(": %s: " % key in problem or problem.endswith(": %s" % key)
                   for key in extra_keys):
            return False
        # 文案例子：``/tmp/data/cases/case_x.json: category: Extra inputs are not permitted``
        return source in problem or "/cases/" in problem or case_id in problem

    @property
    def runs_dir(self) -> Path:
        return self.storage_dir / RUNS_DIRNAME

    @property
    def suites_dir(self) -> Path:
        """方案落盘目录 ``<data_dir>/case_suites``（与声明根同一处，git 可 diff）。"""
        root = Path(self.data_dir)
        if root.name == decl_loader.SUITES_DIRNAME:
            return root
        return root / decl_loader.SUITES_DIRNAME

    # -- 地址注入（唯一改 ``WORKBENCH_BASE`` 的地方） ------------------------ #

    @contextlib.contextmanager
    def injected_base_url(
        self, base_url: str, *, blocking: bool = True
    ) -> Iterator[bool]:
        """临时把 ``base_url`` 灌进 ``WORKBENCH_BASE``；拿不到锁时 yield ``False``。

        - job 执行 / 预检：``blocking=True``（等锁，整轮只一个人持锁）；
        - ``/api/cases/bench``：``blocking=False``（台架正被运行占用就**不探事实**，
          绝不改别人的地址注入）。
        """
        if blocking:
            acquired = self.env_lock.acquire(timeout=self.env_lock_timeout_s)
        else:
            acquired = self.env_lock.acquire(blocking=False)
        if not acquired:
            yield False
            return
        try:
            with _base_url_env(base_url):
                yield True
        finally:
            self.env_lock.release()

    def _env(self, base_url: str, base_source: str) -> Env:
        """给 libs 的入口层（预检）造一个 ``Env``：输出丢弃，只带注入位。"""
        return Env(
            out=io.StringIO(),
            err=io.StringIO(),
            json=True,
            base_url=base_url,
            base_source=base_source,
            extras=dict(self.extras),
            fact_probe=self.fact_probe,
        )

    # -- 目录端点 ----------------------------------------------------------- #

    def case_catalog(
        self,
        bundle: Bundle,
        *,
        usage: Optional[str] = None,
        domain: Optional[str] = None,
        scene: Optional[str] = None,
        region: Optional[str] = None,
        protocol: Optional[str] = None,
        sort: str = "usage",
    ) -> CaseListResponse:
        """用例目录（D10-2：按 ``usage``/``domain``/``scene``/``region``/``protocol`` 筛与排）。"""
        raw_cases = self._raw_cases()
        views: list[CaseView] = []
        for spec in bundle.cases:
            category, protocols = _classification(spec, raw_cases.get(spec.id) or {})
            views.append(
                _case_view(
                    spec,
                    category,
                    protocols,
                    self._tolerated.get(spec.id, ()),
                )
            )
        wanted = {
            "usage": _wanted(usage),
            "domain": _wanted(domain),
            "scene": _wanted(scene),
            "region": _wanted(region),
        }
        protocols_wanted = _wanted(protocol)
        total = len(views)
        selected = [
            view
            for view in views
            if _match_case(view, wanted, protocols_wanted)
        ]
        chosen_sort = (sort or "").strip().lower()
        if chosen_sort:
            selected.sort(key=lambda view: _sort_key(view, chosen_sort))
        filters = {
            name: str(value)
            for name, value in (
                ("usage", usage),
                ("domain", domain),
                ("scene", scene),
                ("region", region),
                ("protocol", protocol),
            )
            if value
        }
        return CaseListResponse(
            cases=selected,
            total=len(selected),
            classified=sum(1 for view in selected if view.classified),
            unclassified=sum(1 for view in selected if not view.classified),
            filters=filters,
            sort=chosen_sort,
            data_dir=str(bundle.data_dir),
            problems=list(bundle.problems),
            # 注：total 是「过滤后」的条数，未过滤总量在本响应里没有单独字段——
            # 需要全量的调用方不传筛参即可（与 §3.6 的「列表」语义一致）
        )

    def block_catalog(self, bundle: Bundle) -> BlockListResponse:
        blocks = [_block_view(spec) for spec in bundle.blocks]
        return BlockListResponse(
            blocks=blocks,
            total=len(blocks),
            data_dir=str(bundle.data_dir),
            problems=list(bundle.problems),
        )

    def fact_catalog(self, bundle: Bundle) -> FactListResponse:
        facts = [_fact_view(spec) for spec in bundle.facts]
        return FactListResponse(
            facts=facts,
            total=len(facts),
            data_dir=str(bundle.data_dir),
            problems=list(bundle.problems),
        )

    def suite_catalog(self, bundle: Bundle) -> SuiteListResponse:
        suites = [SuiteView.from_spec(spec) for spec in bundle.suites]
        return SuiteListResponse(
            suites=suites,
            total=len(suites),
            data_dir=str(bundle.data_dir),
            problems=list(bundle.problems),
        )

    # -- 方案写端点 --------------------------------------------------------- #

    @staticmethod
    def _check_suite_id(suite_id: str) -> str:
        """方案 id 会被拼进文件名，先挡 ``../`` 之类（只允许字母数字 + ``._-``）。"""
        if not _ID_RE.fullmatch(str(suite_id or "")):
            raise _error(
                422,
                "suite_id_invalid",
                "方案 id 不合法：%r（落盘文件名只允许字母/数字/._-，且不得以符号开头）"
                % (suite_id,),
            )
        return str(suite_id)

    def save_suite(self, bundle: Bundle, spec: SuiteSpec) -> SuiteSaveResponse:
        """保存方案（同名覆盖）；``case_ids`` 必须全部指向已声明用例。"""
        self._check_suite_id(spec.id)
        known = bundle.cases_by_id
        missing = [case_id for case_id in spec.case_ids if case_id not in known]
        if missing:
            raise _error(
                422,
                "case_ids_unknown",
                "方案 %r 引用了不存在的用例：%s" % (spec.id, "、".join(missing)),
                problems=[
                    "用例 %r 未声明（已声明用例：%s）" % (case_id, "、".join(sorted(known)) or "无")
                    for case_id in missing
                ],
            )
        target_dir = self.suites_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / ("%s.json" % spec.id)
        replaced = path.is_file()
        payload = spec.model_dump(mode="json")
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise _error(500, "suite_write_failed", "方案落盘失败：%s" % exc) from None
        return SuiteSaveResponse(
            suite=SuiteView.from_spec(spec), path=str(path), replaced=replaced
        )

    def delete_suite(self, bundle: Bundle, suite_id: str) -> SuiteDeleteResponse:
        """删除方案（不存在 → 404；不在可写方案目录内的声明 → 409，不误删）。"""
        spec = bundle.suites_by_id.get(suite_id)
        if spec is None:
            raise _error(
                404,
                "suite_not_found",
                "方案不存在：%r（已有方案：%s）"
                % (suite_id, "、".join(sorted(bundle.suites_by_id)) or "无"),
            )
        source = (bundle.sources.get("suites") or {}).get(suite_id, "")
        if not source:
            raise _error(409, "suite_source_unknown", "方案 %r 没有可定位的声明文件" % suite_id)
        path = Path(source)
        try:
            inside = path.resolve().parent == self.suites_dir.resolve()
        except OSError:
            inside = False
        if not inside:
            raise _error(
                409,
                "suite_outside_writable_root",
                "方案 %r 的声明文件不在可写方案目录（%s）内，拒绝删除：%s"
                % (suite_id, self.suites_dir, path),
            )
        try:
            path.unlink()
        except FileNotFoundError:
            raise _error(404, "suite_not_found", "方案文件已不存在：%s" % path) from None
        except OSError as exc:
            raise _error(500, "suite_delete_failed", "方案文件删除失败：%s" % exc) from None
        return SuiteDeleteResponse(deleted=True, suite_id=suite_id, path=str(path))

    # -- 台架快照（/api/cases/bench） --------------------------------------- #

    def bench(self, *, base_url: str = "", base_source: str = "none") -> BenchSnapshot:
        """台架就绪快照 = 逻辑口可用性（``/api/serial-bridge/status``）+ 全部事实三态。

        不写死端口：口清单与 ``usable`` 全部来自桥配置/状态；数量随配置变（台架上是 5 个
        启用口）。事实探测有 ``PREFLIGHT_BUDGET_S`` 预算，超预算的事实如实记 ``unknown``
        （「没验出来」不是「就绪」）。
        """
        started = time.monotonic()
        snapshot, bridge_error = self._bridge_snapshot()
        ports = self._bench_ports(snapshot)
        enabled = [port for port in ports if port.enabled]
        ports_usable = sum(1 for port in enabled if port.usable)
        ports_ok = bool(enabled) and ports_usable == len(enabled)
        bundle = self.bundle()  # 一次请求只装一次声明（口快照与事实三态共用）
        facts, probe_skipped = self._bench_facts(
            bundle, base_url=base_url, base_source=base_source
        )
        fact_counts = {"yes": 0, "no": 0, "unknown": 0}
        for fact in facts:
            fact_counts[fact.state] = fact_counts.get(fact.state, 0) + 1
        unmet = {
            fact.fact_id: FactValueView(
                state=fact.state,
                detail=fact.detail,
                evidence=list(fact.evidence),
                reason=(
                    "事实不成立（no）：台架/前置不具备"
                    if fact.state == "no"
                    else "说不清（unknown）：没验出来，不能当就绪"
                ),
                params=dict(fact.params),
            )
            for fact in facts
            if fact.state != "yes"
        }
        #: 用例库真正需要的事实（全库用例 needs 并集）：只有它们参与 ready/facts_ok
        required_facts: list[str] = []
        for case in getattr(bundle, "cases", []) or []:
            for fact_id in (case.needs or []):
                if fact_id not in required_facts:
                    required_facts.append(fact_id)
        unmet_required = {
            fact_id: value for fact_id, value in unmet.items() if fact_id in set(required_facts)
        }
        facts_ok = bool(required_facts) and not unmet_required
        supervisor = (snapshot or {}).get("supervisor")
        return BenchSnapshot(
            ready=bool(ports_ok and facts_ok),
            ports_ok=ports_ok,
            facts_ok=facts_ok,
            required_facts=required_facts,
            unmet_required=unmet_required,
            ports=ports,
            ports_total=len(enabled),
            ports_usable=ports_usable,
            ports_disabled=sum(1 for port in ports if not port.enabled),
            bridge_ok=bool((snapshot or {}).get("ok")),
            bridge_error=bridge_error or str((snapshot or {}).get("config_error") or ""),
            bridge_supervisor=dict(supervisor) if isinstance(supervisor, Mapping) else {},
            facts=facts,
            facts_total=len(facts),
            fact_counts=fact_counts,
            unmet_facts=unmet,
            probe_skipped=probe_skipped,
            base_url=base_url,
            base_url_source=base_source,
            probed_at=_now(),
            duration_s=round(time.monotonic() - started, 3),
            problems=list(bundle.problems),
        )

    def _bridge_snapshot(self) -> tuple[Optional[dict[str, Any]], str]:
        """取一次串口桥状态快照（注入源优先；异常收敛成「读不到」而不是 500）。"""
        provider = self.bridge_snapshot_provider or _default_bridge_snapshot
        try:
            snapshot = provider()
        except Exception as exc:  # noqa: BLE001 - 快照读不到要如实报，不能让快照端点挂掉
            return None, "串口桥状态不可读：%s: %s" % (type(exc).__name__, exc)
        if not isinstance(snapshot, Mapping):
            return None, "串口桥状态结构非法：期望 JSON 对象，实际 %s" % type(snapshot).__name__
        return dict(snapshot), ""

    @staticmethod
    def _bench_ports(snapshot: Optional[Mapping[str, Any]]) -> list[BenchPortView]:
        ports: list[BenchPortView] = []
        for raw in (snapshot or {}).get("ports") or []:
            if not isinstance(raw, Mapping):
                continue
            mapping = raw.get("mapping")
            mapping = mapping if isinstance(mapping, Mapping) else {}
            ports.append(
                BenchPortView(
                    mapping_id=str(mapping.get("mapping_id") or raw.get("id") or ""),
                    port_id=str(raw.get("id") or ""),
                    label=str(raw.get("label") or ""),
                    enabled=bool(raw.get("enabled", True)),
                    usable=bool(raw.get("usable")),
                    channel_ok=bool(raw.get("channel_ok")),
                    workbench_ready=bool(raw.get("workbench_ready")),
                    windows_com=str(raw.get("windows_com") or ""),
                    link=str(raw.get("link") or ""),
                    errors=[str(item) for item in raw.get("errors") or []],
                    warnings=[str(item) for item in raw.get("warnings") or []],
                )
            )
        return ports

    def _bench_facts(
        self, bundle: Bundle, *, base_url: str = "", base_source: str = "none"
    ) -> tuple[list[BenchFactView], str]:
        """探全部事实的三态（复用 libs 的探测路径：``probe_fact_params`` + ``FactsContext``）。"""
        fact_params = probe_fact_params(bundle)
        declared = {spec.id for spec in bundle.facts}
        started = time.monotonic()
        with self.injected_base_url(base_url, blocking=False) as acquired:
            if not acquired:
                reason = (
                    "台架正被一次运行占用（``%s`` 注入中），快照不探测事实："
                    "改这个变量会污染那一轮的台架地址，宁可不探也不给假结论" % ENV_BASE_URL
                )
                return (
                    [
                        BenchFactView(
                            fact_id=spec.id,
                            title=spec.title,
                            state="unknown",
                            detail=reason,
                            params=dict(fact_params.get(spec.id) or {}),
                            declared=spec.id in declared,
                        )
                        for spec in bundle.facts
                    ],
                    reason,
                )
            ctx = FactsContext(
                probe=self.fact_probe,
                params={key: dict(value) for key, value in fact_params.items()},
            )
            facts: list[BenchFactView] = []
            for spec in bundle.facts:
                params = dict(fact_params.get(spec.id) or {})
                if time.monotonic() - started > BENCH_SNAPSHOT_BUDGET_S:
                    facts.append(
                        BenchFactView(
                            fact_id=spec.id,
                            title=spec.title,
                            state="unknown",
                            detail="台架快照探测超出 %.1fs 预算，未探测" % BENCH_SNAPSHOT_BUDGET_S,
                            params=params,
                            declared=spec.id in declared,
                        )
                    )
                    continue
                value = ctx.get(spec.id)
                facts.append(
                    BenchFactView(
                        fact_id=spec.id,
                        title=spec.title,
                        state=value.state,  # type: ignore[arg-type]
                        detail=value.detail,
                        evidence=list(value.evidence),
                        params=params,
                        declared=spec.id in declared,
                    )
                )
            return facts, ""

    def bench_ports_evidence(self) -> dict[str, Any]:
        """启动瞬间的台架端口快照（写进 ``result.json`` 的 ``bench`` 证据，纯只读）。"""
        snapshot, error = self._bridge_snapshot()
        ports = self._bench_ports(snapshot)
        enabled = [port for port in ports if port.enabled]
        return {
            "source": "serial-bridge/status",
            "captured_at": _now(),
            "error": error,
            "ok": bool((snapshot or {}).get("ok")),
            "ports_total": len(enabled),
            "ports_usable": sum(1 for port in enabled if port.usable),
            "ports": [port.model_dump(mode="json") for port in ports],
        }

    # -- 运行 --------------------------------------------------------------- #

    def _select_targets(
        self, bundle: Bundle, body: RunRequest
    ) -> tuple[list[CaseSpec], Optional[SuiteSpec], CaseParamSpec]:
        """``suite_id`` / ``case_ids`` 二者择一 → 待跑用例 + 参数规格（复用 cli 的规格构建）。"""
        has_suite = bool((body.suite_id or "").strip())
        has_cases = bool(body.case_ids)
        if has_suite == has_cases:
            raise _error(
                422,
                "run_target_invalid",
                "suite 与 case_ids 必须二者择一（都给了或都没给）",
                problems=[
                    "请求体里 suite_id=%r、case_ids=%r；二选一：按方案跑给 suite_id，"
                    "临时组合给 case_ids" % (body.suite_id, body.case_ids)
                ],
            )

        if has_suite:
            suite_id = str(body.suite_id).strip()
            suite = bundle.suites_by_id.get(suite_id)
            if suite is None:
                raise _error(
                    404,
                    "suite_not_found",
                    "方案不存在：%r（已有方案：%s）"
                    % (suite_id, "、".join(sorted(bundle.suites_by_id)) or "无"),
                )
            missing = [case_id for case_id in suite.case_ids if case_id not in bundle.cases_by_id]
            if missing:
                raise _error(
                    422,
                    "suite_references_unknown_case",
                    "方案 %r 引用了不存在的用例：%s" % (suite.id, "、".join(missing)),
                    problems=["方案文件已损坏，请修 %s" % (bundle.sources.get("suites", {}).get(suite.id, ""))],
                )
            cases = [bundle.cases_by_id[case_id] for case_id in suite.case_ids]
            return cases, suite, suite_param_spec(suite, cases, bundle)

        case_ids = [str(item) for item in dict.fromkeys(body.case_ids or [])]
        if not case_ids:
            raise _error(422, "run_target_invalid", "case_ids 不能为空")
        unknown = [case_id for case_id in case_ids if case_id not in bundle.cases_by_id]
        if unknown:
            raise _error(
                404,
                "case_not_found",
                "用例不存在：%s（已声明用例：%s）"
                % ("、".join(unknown), "、".join(sorted(bundle.cases_by_id)) or "无"),
            )
        cases = [bundle.cases_by_id[case_id] for case_id in case_ids]
        if len(cases) == 1:
            return cases, None, case_param_spec(cases[0], bundle)
        adhoc = SuiteSpec(
            id="__adhoc__",
            name="临时用例组合",
            case_ids=[case.id for case in cases],
        )
        return cases, None, suite_param_spec(adhoc, cases, bundle)

    def _run_timeout(self, value: Optional[float]) -> float:
        """整轮超时（秒）：缺省 600（D2-6）；``0`` 关闭；负数/非数字 → 422（同 CLI 口径）。"""
        if value is None:
            return DEFAULT_RUN_TIMEOUT
        try:
            timeout_s = float(value)
        except (TypeError, ValueError):
            raise _error(422, "timeout_invalid", "timeout 必须是数字（秒）：收到 %r" % (value,)) from None
        if timeout_s < 0:
            raise _error(422, "timeout_invalid", "timeout 不能为负：收到 %r" % (value,))
        return timeout_s

    def _active_job_locked(self) -> Optional[_Job]:
        """当前在跑的 job（调用方必须持 ``self._lock``）。"""
        if not self._active:
            return None
        job = self._jobs.get(self._active)
        if job is not None and job.state in (JOB_QUEUED, JOB_RUNNING):
            return job
        return None

    def _assert_not_busy(self) -> None:
        with self._lock:
            running = self._active_job_locked()
        if running is not None:
            raise _error(
                409,
                "busy",
                "已有用例任务在跑：%s（台架是独占资源，等它结束再提）" % running.job_id,
                job_id=running.job_id,
            )

    def start_run(self, body: RunRequest, request_origin: str = "") -> RunResponse:
        """受理一次运行：校验 → **预检** → 预检不过就整轮 blocked（不建 job）→ 否则起 job。

        顺序是硬性的（REQS-0042 D2-8 / D4-1 / D4-2）：

        1. 声明装载 + 目标选定 + 入参校验（422/404/409，错误一次报全）；
        2. **忙检查**：台架是独占资源，已有 job 在跑 → 409（不白探一遍）；
        3. 台架端口快照（启动瞬间证据，只读，不启串口）；
        4. **预检**：只探所选用例 ``needs`` 并集（<1s，不逐用例跑）；不满足 → 整轮
           ``blocked`` + 缺项清单，按同一 ``run_id`` 落盘证据，**不建 job**；
        5. 预检通过 → 建 job + 后台线程，立即返回 ``job_id``。
        """
        bundle = self.bundle()
        if bundle.problems:
            # 声明坏了不许跑：run 会在真实台架上产生激励，宁可不跑也不带病跑（同 cli.main）
            raise _error(
                409,
                "declaration_error",
                "声明加载未通过（%d 条），拒绝运行；先修声明再跑" % len(bundle.problems),
                problems=list(bundle.problems),
            )

        cases, suite, param_spec = self._select_targets(bundle, body)
        try:
            resolved = param_spec.resolve(body.params)
        except UsageError as exc:
            # 与 CLI 同一份校验逻辑（CaseParamSpec.resolve），中文原因逐条返回
            raise _error(
                422,
                "params_invalid",
                "%s（%d 条）" % (exc.headline, len(exc.problems)),
                problems=list(exc.problems),
            ) from None

        timeout_s = self._run_timeout(body.timeout)
        base_url, base_source = resolve_job_base_url(body.base_url, request_origin)
        stop_on_fail = bool(suite.stop_on_fail) if suite is not None else False
        _block_params, fact_params = param_spec.split(resolved)

        self._assert_not_busy()
        bench_ports = self.bench_ports_evidence()

        preflight = PreflightResult(enabled=False, skipped_reason="预检已关闭（no_preflight=true）")
        facts_ctx = None
        if not body.no_preflight:
            with self.injected_base_url(base_url) as acquired:
                if not acquired:
                    raise _error(
                        409,
                        "busy",
                        "台架地址注入被占用（另一轮运行正在持锁），稍后重试",
                    )
                preflight, facts_ctx = run_preflight(
                    cases,
                    bundle,
                    fact_params=fact_params,
                    env=self._env(base_url, base_source),
                )

        job = _Job(
            job_id="job-%s" % uuid.uuid4().hex[:16],
            run_id=new_run_id(),
            suite=suite,
            cases=list(cases),
            case_ids=[case.id for case in cases],
            base_url=base_url,
            base_url_source=base_source,
            params=dict(resolved),
            stop_on_fail=stop_on_fail,
            timeout_s=timeout_s,
            preflight=preflight,
            facts_ctx=facts_ctx,
            bench=bench_ports,
        )

        if not preflight.ok:
            return self._blocked_run(job, preflight)

        with self._lock:
            running = self._active_job_locked()
            if running is not None:
                raise _error(
                    409,
                    "busy",
                    "已有用例任务在跑：%s（台架是独占资源，等它结束再提）" % running.job_id,
                    job_id=running.job_id,
                )
            self._jobs[job.job_id] = job
            self._active = job.job_id
            self._prune_locked()

        threading.Thread(
            target=self._execute,
            args=(job, bundle, cases, param_spec, resolved),
            name="cases-job-%s" % job.job_id,
            daemon=True,
        ).start()
        return RunResponse(
            accepted=True,
            state=JOB_QUEUED,
            verdict=None,
            job_id=job.job_id,
            run_id=job.run_id,
            suite_id=job.suite_id,
            case_ids=list(job.case_ids),
            base_url=job.base_url,
            base_url_source=job.base_url_source,
            params=dict(job.params),
            timeout_s=job.timeout_s,
            preflight=preflight.to_dict(),
            unmet_facts={},
            bench=job.bench,
            job_url="/api/cases/jobs/%s" % job.job_id,
            result_url="/api/cases/runs/%s" % job.run_id,
        )

    def _blocked_run(self, job: _Job, preflight: PreflightResult) -> RunResponse:
        """预检不过：整轮 ``blocked``（逐用例记 ``blocked``，但**一个块都不跑**）并落盘。"""
        reason = "预检未通过（整轮 blocked，未逐用例执行）：%s" % "、".join(
            "%s=%s" % (fact_id, entry.get("state", "unknown"))
            for fact_id, entry in preflight.unmet.items()
        )
        job.state = RUN_BLOCKED
        job.started_at = _now()
        job.started_monotonic = time.monotonic()
        job.outcomes = [
            blocked_outcome(
                case,
                run_id=job.run_id,
                reason=reason,
                unmet_facts=preflight.unmet,
            )
            for case in job.cases
        ]
        job.finished_at = _now()
        job.finished_monotonic = time.monotonic()
        try:
            self._persist(job)
        except Exception as exc:  # noqa: BLE001 - 落盘失败不能让 422/500 掩盖 blocked 结论
            job.error = "结果落盘失败：%s: %s" % (type(exc).__name__, exc)
        unmet = {
            fact_id: FactValueView(
                state=str(entry.get("state", "unknown")),  # type: ignore[arg-type]
                detail=str(entry.get("detail") or ""),
                evidence=[str(item) for item in entry.get("evidence") or []],
                reason=str(entry.get("reason") or ""),
                params=dict(entry.get("params") or {}),
            )
            for fact_id, entry in preflight.unmet.items()
        }
        problems = [
            "%s=%s：%s" % (fact_id, entry.get("state", "unknown"), entry.get("reason") or "")
            for fact_id, entry in preflight.unmet.items()
        ]
        return RunResponse(
            accepted=False,
            state=RUN_BLOCKED,
            verdict="blocked",
            job_id="",
            run_id=job.run_id,
            suite_id=job.suite_id,
            case_ids=list(job.case_ids),
            base_url=job.base_url,
            base_url_source=job.base_url_source,
            params=dict(job.params),
            timeout_s=job.timeout_s,
            preflight=preflight.to_dict(),
            unmet_facts=unmet,
            problems=problems,
            reason=reason,
            bench=job.bench,
            result_path=job.result_path,
            result_url="/api/cases/runs/%s" % job.run_id,
        )

    def _prune_locked(self) -> None:
        """只保留最近 50 个 job 的内存视图（历史结果永远可从 ``runs/<run_id>`` 读）。"""
        if len(self._jobs) <= 50:
            return
        for job_id in list(self._jobs)[:-50]:
            if job_id != self._active:
                self._jobs.pop(job_id, None)

    def _execute(
        self,
        job: _Job,
        bundle: Bundle,
        cases: Sequence[CaseSpec],
        param_spec: CaseParamSpec,
        resolved: Mapping[str, Any],
    ) -> None:
        """后台线程：逐用例跑内核执行器（整轮预算 + 断点可读），落盘终态结果。

        执行顺序与 CLI ``cmd_run`` 完全同源：整轮 ``deadline`` 兜底（D2-6/D4-5）、
        每例在工作线程里跑、超时记 ``inconclusive`` 并写明卡在哪个块、``stop_on_fail``
        后序用例记「未执行」而不是假装通过。
        """
        job.state = JOB_RUNNING
        job.started_at = _now()
        job.started_monotonic = time.monotonic()
        try:
            with self.injected_base_url(job.base_url):
                deadline = (
                    job.started_monotonic + job.timeout_s if job.timeout_s > 0 else None
                )
                stopped_at = ""
                track_ids = [block_id for case in cases for block_id in case_block_ids(case)]
                tracker = (
                    track_running_blocks(track_ids)
                    if deadline is not None
                    else contextlib.nullcontext()
                )
                with tracker:
                    for case in cases:
                        job.current_case_id = case.id
                        job.current_case_title = case.title
                        if stopped_at:
                            outcome = skipped_outcome(
                                case,
                                run_id=job.run_id,
                                reason=(
                                    "方案 stop_on_fail：前序用例 %s 未通过，本用例未执行"
                                    % stopped_at
                                ),
                            )
                        elif deadline is not None and time.monotonic() >= deadline:
                            outcome = timeout_outcome(
                                case,
                                run_id=job.run_id,
                                reason="整轮超时未执行（预算已耗尽，上一例卡住后不再往下跑）",
                            )
                        else:
                            case_started = time.monotonic()
                            outcome, timed_out = run_case_with_deadline(
                                bundle,
                                case,
                                deadline=deadline,
                                resolved_params=resolved,
                                param_spec=param_spec,
                                run_id=job.run_id,
                                extras=self.extras,
                                fact_probe=self.fact_probe,
                                stop_on_fail=job.stop_on_fail,
                                facts_ctx=job.facts_ctx,
                            )
                            outcome.duration_s = time.monotonic() - case_started
                            if timed_out:
                                # 预算已耗尽：剩下的用例一律记「未执行」，绝不再开新线程
                                deadline = time.monotonic()
                                job.run_decisions.append(
                                    # 与内核同形（decision_entry）：kind/attempts/reason 必备
                                    decision_entry(
                                        DECISION_TIMEOUT,
                                        attempts=1,
                                        case_id=case.id,
                                        stuck_block=outcome.stuck_block,
                                        budget_s=job.timeout_s,
                                        reason=(
                                            "整轮 %gs 预算耗尽：卡在 %s / %s，剩余用例不再执行"
                                            % (job.timeout_s, case.id, outcome.stuck_block or "预处理")
                                        ),
                                    )
                                )
                        with self._lock:
                            job.outcomes.append(outcome)
                        job.current_case_id = ""
                        job.current_case_title = ""
                        if (
                            job.stop_on_fail
                            and outcome.result.verdict == "fail"
                            and not stopped_at
                        ):
                            stopped_at = case.id
        except Exception as exc:  # noqa: BLE001 - job 自身异常要落到 state=failed，不静默
            job.error = "job 执行异常：%s: %s" % (type(exc).__name__, exc)
            job.state = JOB_FAILED
        finally:
            job.finished_monotonic = time.monotonic()
            job.finished_at = _now()
            if job.state != JOB_FAILED:
                job.state = JOB_DONE
            try:
                self._persist(job)
            except Exception as exc:  # noqa: BLE001 - 落盘失败不能把 job 变「成功」
                job.state = JOB_FAILED
                job.error = (job.error + "；" if job.error else "") + (
                    "结果落盘失败：%s: %s" % (type(exc).__name__, exc)
                )
            with self._lock:
                if self._active == job.job_id:
                    self._active = ""

    # -- 读模型 / 落盘 ------------------------------------------------------ #

    def _duration(self, job: _Job) -> float:
        if not job.started_monotonic:
            return 0.0
        end = job.finished_monotonic or time.monotonic()
        return _round1(max(0.0, end - job.started_monotonic))

    def _merged_decisions(self, job: _Job, outcomes: Sequence[RunOutcome]) -> list[dict[str, Any]]:
        """``decisions[]`` 的扁平合并（**不派生第二套判定**，只把两处现成条目拼起来）。

        来源只有两个：run 级预检的 :meth:`PreflightResult.decisions`，与每例
        :meth:`CaseRunResult.resolved_decisions`（自愈 / 重试 / 等待 / 超时）。
        本层额外补 ``case_id`` / ``blocked_block`` 便于事后回指。
        """
        raw: list[dict[str, Any]] = []
        if job.preflight is not None:
            raw.extend(dict(item) for item in job.preflight.decisions())
        raw.extend(dict(item) for item in job.run_decisions)
        for outcome in outcomes:
            for item in outcome.result.to_dict().get("decisions") or []:
                entry = dict(item)
                entry.setdefault("case_id", outcome.case.id)
                entry.setdefault("blocked_block", outcome.result.blocked_block)
                raw.append(entry)
        # 形状归一（核心同一份 normalize_decision）：缺 kind 的条目丢弃，必备字段补齐
        merged: list[dict[str, Any]] = []
        for item in raw:
            entry = normalize_decision(item)
            if entry:
                merged.append(entry)
        return merged

    def view(self, job: _Job) -> RunRecordView:
        """job → 读模型快照（同一把锁内取结果列表，避免读到半截结果）。"""
        with self._lock:
            outcomes = list(job.outcomes)
            current_id = job.current_case_id
            current_title = job.current_case_title
        results = [CaseRunView.from_outcome(outcome) for outcome in outcomes]
        verdicts = [item.verdict for item in results]
        verdict = _aggregate_verdict(verdicts)
        total = len(job.case_ids)
        done = len(results)
        percent = 100 if total and done >= total else (
            int(round(done * 100.0 / total)) if total else 0
        )
        counts = _sum_counts([item.counts.model_dump(by_alias=True) for item in results])
        self_healed = [
            item for outcome in outcomes for item in outcome.result.self_healed_entries()
        ]
        # D4-4：``interventions[]`` 恒为空数组（``CaseRunResult`` 构造期已硬拒非空，
        # 这里再兜一层：任何一条漏进来都当「本次运行不成立」报出来，而不是静默写盘）
        leaked = [item for outcome in outcomes for item in outcome.result.interventions or []]
        duration = self._duration(job)
        return RunRecordView(
            job_id=job.job_id,
            run_id=job.run_id,
            state=job.state,  # type: ignore[arg-type]
            accepted=job.state != RUN_BLOCKED,
            suite_id=job.suite_id,
            case_ids=list(job.case_ids),
            base_url=job.base_url,
            base_url_source=job.base_url_source,
            params=dict(job.params),
            resolved_params=dict(job.params),
            stop_on_fail=job.stop_on_fail,
            timeout_s=job.timeout_s,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            duration_seconds=duration,
            duration_s=duration,
            progress=JobProgress(
                total=total,
                done=done,
                current_case_id=current_id,
                current_case_title=current_title,
                percent=percent,
            ),
            verdict=verdict,
            exit_code=exit_code_for_verdict(verdict) if verdict else None,
            counts=counts,
            case_counts=_count_verdicts(verdicts),
            results=results,
            decisions=self._merged_decisions(job, outcomes),
            interventions=[],  # 恒空；leaked 已在下面变成 problem
            self_healed=self_healed,
            bench={
                "ports": dict(job.bench),
                "facts": dict(job.preflight.facts),
                "unmet_facts": dict(job.preflight.unmet),
                "preflight_enabled": bool(job.preflight.enabled),
                "skipped_reason": job.preflight.skipped_reason,
            },
            preflight=job.preflight.to_dict(),
            error=job.error,
            result_path=job.result_path,
            problems=[
                "interventions[] 出现 %d 条记录（D4-4 要求恒为空数组，本次运行不成立）" % len(leaked)
            ] if leaked else [],
        )

    def _persist(self, job: _Job) -> None:
        """把终态结果写 ``<storage_dir>/case_runs/<run_id>/result.json``。"""
        target_dir = self.runs_dir / job.run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / RESULT_FILENAME
        job.result_path = str(path)  # 先记路径，落盘内容里的 result_path 才不会空
        record = self.view(job)
        payload = record.model_dump(mode="json", by_alias=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def job_or_404(self, job_id: str) -> _Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise _error(
                404,
                "job_not_found",
                "job 不存在：%r（job 只在进程内存里；历史结果请用 GET /api/cases/runs/{run_id}）"
                % (job_id,),
            )
        return job

    def job_view(self, job_id: str) -> RunRecordView:
        return self.view(self.job_or_404(job_id))

    def load_run(self, run_id: str) -> RunRecordView:
        """按 ``run_id`` 读历史结果 ``<storage_dir>/case_runs/<run_id>/result.json``。"""
        if not _ID_RE.fullmatch(str(run_id or "")) or ".." in str(run_id):
            raise _error(404, "run_not_found", "run_id 不合法：%r" % (run_id,))
        path = self.runs_dir / run_id / RESULT_FILENAME
        if not path.is_file():
            raise _error(
                404,
                "run_not_found",
                "没有这次运行的结果：run_id=%r（期望文件 %s）" % (run_id, path),
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise _error(
                500, "run_unreadable", "结果文件读取失败：%s：%s" % (path, exc)
            ) from None
        try:
            return RunRecordView.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - 结构漂移要报出来，不静默
            raise _error(
                500, "run_unreadable", "结果文件结构非法：%s：%s" % (path, exc)
            ) from None


# --------------------------------------------------------------------------- #
# 路由工厂
# --------------------------------------------------------------------------- #


def create_cases_router(
    storage_dir: Path,
    *,
    data_dir: Optional[Path] = None,
    extras: Optional[Mapping[str, Any]] = None,
    fact_probe: Any = None,
    register_domain: bool = True,
    bridge_snapshot_provider: Any = None,
) -> APIRouter:
    """创建 ``/api/cases/*`` 路由（10 个端点，REQS-0042 §3.6）。

    ``storage_dir``：job 结果落盘根（结果写 ``<storage_dir>/case_runs/<run_id>/result.json``）。
    ``data_dir``：声明根（默认 ``libs/case_runner/data``）；``extras`` / ``fact_probe`` /
    ``bridge_snapshot_provider`` 是能力替身、事实探测源与台架快照源的注入位（测试用），
    生产路径全部走缺省。
    """
    service = CasesService(
        storage_dir,
        data_dir=data_dir,
        extras=extras,
        fact_probe=fact_probe,
        register_domain=register_domain,
        bridge_snapshot_provider=bridge_snapshot_provider,
    )
    rt = APIRouter(prefix="/api/cases", tags=["cases"])

    # -- 目录（API-2 / API-3 + D10-2 筛选排序）------------------------------ #

    @rt.get("", response_model=CaseListResponse)
    def list_cases(
        usage: Optional[str] = Query(None, description="常用度：common / normal / rare（可逗号分隔）"),
        domain: Optional[str] = Query(None, description="设备域：CCO / STA / ECU / bench"),
        scene: Optional[str] = Query(None, description="业务场景：组网 / 抄表 …"),
        region: Optional[str] = Query(None, description="省份/区域：国网通用 / 南网通用 / 重庆 …"),
        protocol: Optional[str] = Query(None, description="协议标签：1376.2 / 645 / 698.45 …"),
        sort: str = Query("usage", description="排序：usage（常用度降序，缺省）/ name（标题）"),
    ) -> CaseListResponse:
        """用例列表：id / title / steps(do+expect) / blocks / needs / tags / category。"""
        return service.case_catalog(
            service.bundle(),
            usage=usage,
            domain=domain,
            scene=scene,
            region=region,
            protocol=protocol,
            sort=sort,
        )

    @rt.get("/blocks", response_model=BlockListResponse)
    def list_blocks_catalog() -> BlockListResponse:
        """块目录：id / title / needs / provides / action 摘要 / assert 元数据。"""
        return service.block_catalog(service.bundle())

    @rt.get("/facts", response_model=FactListResponse)
    def list_facts_catalog() -> FactListResponse:
        """事实目录：id / title / probe / params_schema（+ required / registered）。"""
        return service.fact_catalog(service.bundle())

    # -- 台架快照（D2-8 / UI-2）------------------------------------------- #

    @rt.get("/bench", response_model=BenchSnapshot)
    def bench_snapshot(request: Request, base_url: Optional[str] = Query(None)) -> BenchSnapshot:
        """台架就绪快照：逻辑口可用性（``/api/serial-bridge/status``）+ 全部事实三态。

        只读：不开串口、不写任何东西、不写死端口。``base_url`` 只用于事实探测
        （注入 > ``WORKBENCH_BASE`` > 本次请求 origin）。
        """
        explicit = (base_url or "").strip()
        value, source = resolve_job_base_url(explicit, str(request.base_url))
        return service.bench(base_url=value, base_source=source)

    # -- 方案（API-2 / SU-1）---------------------------------------------- #

    @rt.get("/suites", response_model=SuiteListResponse)
    def list_suites() -> SuiteListResponse:
        """方案列表：id / name / case_ids / stop_on_fail / notes。"""
        return service.suite_catalog(service.bundle())

    @rt.post("/suites", response_model=SuiteSaveResponse)
    def save_suite(spec: SuiteSpec) -> SuiteSaveResponse:
        """保存方案（body = ``SuiteSpec`` 形状；``case_ids`` 必须指向已声明用例）。"""
        return service.save_suite(service.bundle(), spec)

    @rt.delete("/suites/{suite_id}", response_model=SuiteDeleteResponse)
    def delete_suite(suite_id: str) -> SuiteDeleteResponse:
        """删除方案（不存在 → 404）。"""
        return service.delete_suite(service.bundle(), suite_id)

    # -- 运行（API-1 / RU-2 / RU-4 + 预检）--------------------------------- #

    @rt.post("/run", response_model=RunResponse)
    def run_cases(body: RunRequest, request: Request, response: Response) -> RunResponse:
        """受理一次运行：``{suite_id}`` 或 ``{case_ids}``，可选 ``params`` / ``base_url``。

        - 预检通过 → ``202`` + ``accepted=true`` + ``job_id``（异步执行，轮询
          ``GET /api/cases/jobs/{job_id}``）；
        - 预检不满足 → ``200`` + ``accepted=false`` + ``state=blocked`` + ``unmet_facts``
          （**不建 job**，<1s 返回），证据已按同一 ``run_id`` 落盘。
        """
        result = service.start_run(body, str(request.base_url))
        response.status_code = 202 if result.accepted else 200
        return result

    @rt.get("/jobs/{job_id}", response_model=RunRecordView)
    def job_status(job_id: str) -> RunRecordView:
        """job 快照：state / progress / 当前用例 / 已出结果（含断点字段与四态 verdict）。"""
        return service.job_view(job_id)

    @rt.get("/runs/{run_id}", response_model=RunRecordView)
    def run_result(run_id: str) -> RunRecordView:
        """按 run_id 取历史结果（``<storage_dir>/case_runs/<run_id>/result.json``）。"""
        return service.load_run(run_id)

    return rt


__all__ = [
    "CATEGORY_DIMENSIONS",
    "DEFAULT_ENV_LOCK_TIMEOUT_S",
    "ENV_BASE_URL",
    "RESULT_FILENAME",
    "RUNS_DIRNAME",
    "RUN_BLOCKED",
    "TOLERATED_CASE_FIELDS",
    "USAGE_ORDER",
    "VERDICTS",
    "AssertionView",
    "BenchFactView",
    "BenchPortView",
    "BenchSnapshot",
    "BlockActionView",
    "BlockCounts",
    "BlockListResponse",
    "BlockResultView",
    "BlockView",
    "CaseCategory",
    "CaseListResponse",
    "CaseRunView",
    "CaseVerdictCounts",
    "CaseView",
    "CasesService",
    "FactListResponse",
    "FactValueView",
    "FactView",
    "JobProgress",
    "RunRecordView",
    "RunRequest",
    "RunResponse",
    "SuiteDeleteResponse",
    "SuiteListResponse",
    "SuiteSaveResponse",
    "SuiteView",
    "create_cases_router",
    "resolve_job_base_url",
]
