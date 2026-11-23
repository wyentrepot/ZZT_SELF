"""黑盒命令行入口：`case_run list / show / run / probe / check`（REQS-0041 §10.4 ① 入口层）。

用户视角只有一句话：**只输入参数，拿测试结果**。

分层与职责（REQS §10.4，入口层**零测试逻辑**）
----------------------------------------------
本模块只做四件事：**解析参数 / 装配上下文 / 调内核 / 输出**。
任何「这个块算不算通过」的判定都在块执行体（``case_runner.blocks.*``）与事实探测体里；
本模块连一个判据都不写，也不 import ``apps/``、不 import 领域库（``parser_lib`` 等），
只经 ``case_runner`` 门面。唯一驻留在本层的是**入参校验**（用法错误，不是测试判定）：

``--params`` 是本用例**唯一**入参通道，执行前必须校验通过，否则直接返回错误、
**不进执行体**（REQS 0041 第四批验收）：

- 多余参数：名字不在「本用例参数规格」里；
- 缺必填：事实 ``params_schema`` 里 ``required: true`` 的参数无处取值；
- 类型非法：与事实声明类型 / 块声明取值类型不符（另查 ``pattern`` / ``enum`` / 上下限）。

参数规格的来源只有两处（不臆造第三种）：
``块声明的 action.params`` 与 ``事实声明的 params_schema``。

两态输出
--------
默认人读（中文、断点优先：失败块 + 原因 / ``unmet_facts`` / ``failed_block`` /
``blocked_block``）；``--json`` 输出机器可读（``run`` 为完整 ``CaseRunResult.to_dict()``，
错误态为 ``{"ok": false, "error": ...}``），**stdout 永远是纯 JSON**，可供 ``json.loads``。

退出码（固定契约，写进 ``--help``）
-----------------------------------
=====  ==================================================
0      全部 pass
1      有 fail
2      有 unknown/skipped 但无 fail（**未完成验证**）
3      用法/参数错误（含 ``--params`` 校验失败、id 不存在）
4      声明加载失败（JSON 非法 / 引用未注册的块或事实）
=====  ==================================================

绝不假 PASS（REQS RU-3）
------------------------
``unknown`` / ``skipped`` 永不计入 PASS 计数，且在输出里单独成列；``probe --all``
在台架未就绪时**全部 unknown**——块没有跑出确定结论就不许出现 pass。

``base_url`` 服务发现（REQS-0042 **D2-3** 单入口服务发现）
--------------------------------------------------------
四级，**先命中先用**，来源如实回报 ``base_source`` ∈ ``cli`` / ``env`` / ``runtime`` / ``default``：

1. ``--base-url``（显式指定，最高优先）；
2. 环境变量 ``WORKBENCH_BASE``（沿用 REQS-0041 的能力层注入通道）；
3. 运行时文件 ``<仓库根>/data/runtime/workbench.json`` —— **工作台启动时自己写**
   （``apps/workbench/run.py`` / ``apps/workbench/desktop.py``），本模块**只读**；
4. 末档默认 :data:`DEFAULT_BASE_URL`（本机默认端口，零参可跑）—— 走到这一档
   **必须打一行提示**，不静默（见 :func:`base_url_notice`；``--json`` 时提示走
   stderr，stdout 仍是纯 JSON）。只有真正用地址的 ``run`` / ``probe`` 才提示，
   ``list`` / ``show`` / ``check`` 是离线只读，不掺这一行。

第 3/4 档解析出来的地址同样灌进 ``WORKBENCH_BASE``（能力层只认这个通道），退出时原样还原。
「不写死」原则仍在：本模块**只有一处**常量为「零参兜底」定义端口，其余一切由调用方/环境/
运行时文件注入；能力层不读运行时文件，也拿不到本模块的常量。
``runtime`` / ``default`` 都只回答「去哪儿找服务」，不改变块与事实的判定。

运行时文件的路径固定在**仓库根**（``data/runtime/workbench.json``），与 ``--data-dir``
指定的声明根**无关**：换一套声明根不影响服务发现（声明归声明，服务归服务）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence, TextIO

from case_runner.blocks import loader as decl_loader
from case_runner.core import defaults
from case_runner.core.executor import CaseRunResult, RunContext, execute_case
from case_runner.core.models import (
    BLOCK_FAIL,
    BLOCK_PASS,
    BLOCK_SKIPPED,
    BLOCK_UNKNOWN,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    BlockSpec,
    CaseSpec,
    CaseStep,
    FactSpec,
    SuiteSpec,
)
from case_runner.core.planner import FactsContext
from case_runner.core.registry import has_block, has_fact, list_blocks, list_facts
from test_automation.models import AssertionResult

# --------------------------------------------------------------------------- #
# 退出码契约（REQS 第四批；顺序即语义，改这里必须同步文档与 --help）
# --------------------------------------------------------------------------- #

EXIT_PASS = 0          # 全部 pass
EXIT_FAIL = 1          # 有 fail
EXIT_INCOMPLETE = 2    # 有 unknown/skipped 但无 fail（未完成验证）
EXIT_USAGE = 3         # 用法/参数错误（含 --params 校验失败）
EXIT_DECLARATION = 4   # 声明加载失败

#: 环境变量名（与 ``case_runner.capabilities.*`` 的口径一致；本模块只读这个名字，不写死地址）
ENV_BASE_URL = "WORKBENCH_BASE"

#: 本机默认端口（REQS-0042 D2-3 末档，**全文件唯一**一处写死端口的地方；改这里必须同步
#: ``apps/workbench/run.py`` / ``desktop.py`` 的 ``PORT``）
DEFAULT_PORT = 8790
#: 末档默认地址：走到这一档必须打提示（:func:`base_url_notice`），绝不静默
DEFAULT_BASE_URL = "http://127.0.0.1:%d" % DEFAULT_PORT

#: 运行时文件（工作台启动时写、本模块只读）：``<仓库根>/data/runtime/workbench.json``。
#: 固定在仓库根，**不跟随** ``--data-dir``（声明根换了不影响服务发现）。
RUNTIME_FILE: Path = defaults.REPO_ROOT / "data" / "runtime" / "workbench.json"

#: 运行时文件里认的字段：``base_url`` 优先，其次由 ``host`` + ``port`` 拼
RUNTIME_BASE_URL_KEY = "base_url"
RUNTIME_HOST_KEY = "host"
RUNTIME_PORT_KEY = "port"

#: 通配/空 host 不能当客户端目标（监听 ``0.0.0.0`` 与本机访问 ``127.0.0.1`` 是两回事）
WILDCARD_HOSTS: frozenset[str] = frozenset({"", "0.0.0.0", "*", "::", "[::]", "::0"})
#: 通配 host 归一成的本机回环地址
LOOPBACK_HOST = "127.0.0.1"

#: ``base_source`` → 人读来源（如实显示，用户要能看出「地址是哪儿来的」）
BASE_URL_SOURCE_LABELS: dict[str, str] = {
    "cli": "--base-url",
    "env": "环境变量 WORKBENCH_BASE",
    "runtime": "运行时文件 data/runtime/workbench.json",
    "default": "本机默认端口（未发现 workbench.json）",
    "none": "无来源",
}

#: D2-1 已改名的旧参数名 → 新参数名（**硬拒绝，不留兼容别名**）。
#: 用户在 ``--params`` 里传旧名时直接报参数错误（退出码 3）+ 迁移提示，
#: 而不是静默忽略或当成「多余参数」——静默兼容只会让地址角色错位重现。
RENAMED_PARAMS: tuple[tuple[str, str], ...] = (
    ("addr", "meter_addr"),
    ("meters", "meter_addrs"),
)

EXIT_CODE_HELP = """退出码（固定契约）：
  0  全部 pass
  1  有 fail
  2  有 unknown/skipped 但无 fail（未完成验证）
  3  用法/参数错误（含 --params 校验失败、id 不存在）
  4  声明加载失败（JSON 非法 / 引用未注册的块或事实）"""

#: 块执行态 → 计数列（``skipped``/``unknown`` 单独成列，绝不计入 pass）
STATE_LABELS: tuple[tuple[str, str], ...] = (
    (BLOCK_PASS, "pass"),
    (BLOCK_FAIL, "fail"),
    (BLOCK_SKIPPED, "skipped"),
    (BLOCK_UNKNOWN, "unknown"),
)


# --------------------------------------------------------------------------- #
# 错误类型
# --------------------------------------------------------------------------- #


class UsageError(ValueError):
    """用法/参数错误（退出码 3）。``problems`` 逐条可读，一次报全。"""

    def __init__(self, problems: Iterable[str], *, headline: str = "用法/参数错误") -> None:
        self.problems: list[str] = [str(item) for item in problems]
        self.headline = headline
        message = f"{headline}（{len(self.problems)} 条）：\n" + "\n".join(
            f"  - {problem}" for problem in self.problems
        )
        super().__init__(message)


# --------------------------------------------------------------------------- #
# 声明装载（入口层只做「读 + 报告」，不做语义判定）
# --------------------------------------------------------------------------- #


@dataclass
class Bundle:
    """一次 CLI 调用加载到的声明集合（块 / 事实 / 用例 / 方案 + 校验问题）。"""

    data_dir: Path
    report: decl_loader.LoadReport

    @property
    def blocks(self) -> list[BlockSpec]:
        return list(self.report.blocks)

    @property
    def facts(self) -> list[FactSpec]:
        return list(self.report.facts)

    @property
    def cases(self) -> list[CaseSpec]:
        return list(self.report.cases)

    @property
    def suites(self) -> list[SuiteSpec]:
        return list(self.report.suites)

    @property
    def block_specs(self) -> dict[str, BlockSpec]:
        return self.report.block_specs

    @property
    def facts_by_id(self) -> dict[str, FactSpec]:
        return {spec.id: spec for spec in self.report.facts}

    @property
    def cases_by_id(self) -> dict[str, CaseSpec]:
        return {spec.id: spec for spec in self.report.cases}

    @property
    def suites_by_id(self) -> dict[str, SuiteSpec]:
        return {spec.id: spec for spec in self.report.suites}

    @property
    def problems(self) -> list[str]:
        return list(self.report.problems)

    @property
    def sources(self) -> dict[str, dict[str, str]]:
        return {kind: dict(items) for kind, items in (self.report.sources or {}).items()}


def load_bundle(
    data_dir: str | Path | None = None,
    *,
    register_domain: bool = True,
    require_registered: bool = False,
) -> Bundle:
    """加载声明根（默认 ``libs/case_runner/data``）。

    ``strict=False``：**问题不吞**，收集进 ``report.problems`` 由命令层统一报
    （退出码 4），这样 ``list`` 仍能展示已加载到的部分，``check`` 能一次列全。
    """
    root = (
        Path(data_dir).expanduser().resolve()
        if data_dir is not None
        else decl_loader.DATA_DIR
    )
    if not root.is_dir():
        # 目录写错是用法问题（3），不是「声明为空」——空目录会让 probe 静悄悄地什么都自检不了
        raise UsageError(
            ["声明根目录不存在：%s（默认 %s）" % (root, decl_loader.DATA_DIR)]
        )
    report = decl_loader.load_all(
        root,
        strict=False,
        register_modules=register_domain,
        require_registered=require_registered,
    )
    return Bundle(data_dir=root, report=report)


# --------------------------------------------------------------------------- #
# base_url 服务发现（--base-url > WORKBENCH_BASE > 运行时文件 > 本机默认端口）
# --------------------------------------------------------------------------- #


def normalize_base_host(host: Any) -> str:
    """运行时文件里的 ``host`` → 客户端可用的主机名（通配地址归一成回环）。

    工作台监听 ``0.0.0.0``（局域网开放，ADR-28），但 ``http://0.0.0.0:<port>``
    **不是一个可拨号的目标**（部分栈直接拒绝）。读取侧必须把它换成 ``127.0.0.1``。
    """

    text = str(host or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if text in WILDCARD_HOSTS:
        return LOOPBACK_HOST
    return text or LOOPBACK_HOST


def runtime_base_url(path: str | Path | None = None) -> str:
    """从运行时文件读工作台地址；读不到 / 读坏 / 内容不可用 → ``""``（绝不抛）。

    认两种写法（工作台自己写两个都写，这里对任一缺失都容错）：

    - ``base_url``：完整的 ``http(s)://host:port``；
    - ``host`` + ``port``：``0.0.0.0`` / ``::`` 这类监听地址由
      :func:`normalize_base_host` 归一成回环。
    """

    target = Path(path) if path is not None else Path(RUNTIME_FILE)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        payload = json.loads(raw)
    except ValueError:
        return ""
    if not isinstance(payload, Mapping):
        return ""

    explicit = payload.get(RUNTIME_BASE_URL_KEY)
    if isinstance(explicit, str) and explicit.strip().lower().startswith(("http://", "https://")):
        return explicit.strip()

    port = payload.get(RUNTIME_PORT_KEY)
    try:
        port_number = int(port)
    except (TypeError, ValueError):
        port_number = 0
    if not 1 <= port_number <= 65535:
        return ""
    return "http://%s:%d" % (normalize_base_host(payload.get(RUNTIME_HOST_KEY)), port_number)


def resolve_base_url(
    explicit: str | None = None, runtime_file: str | Path | None = None
) -> tuple[str, str]:
    """返回 ``(base_url, 来源)``；来源 ∈ ``cli`` / ``env`` / ``runtime`` / ``default`` / ``none``。

    优先级（REQS-0042 D2-3，**先命中先用**）：

    1. ``--base-url`` → ``cli``；
    2. 环境变量 ``WORKBENCH_BASE`` → ``env``；
    3. 运行时文件 ``data/runtime/workbench.json`` → ``runtime``；
    4. 末档默认 :data:`DEFAULT_BASE_URL` → ``default``（调用方负责打提示）。

    ``DEFAULT_BASE_URL`` 为空串时第 4 档关闭，返回 ``("", "none")``，由能力层报
    ``no_base_url``（块 skipped / 事实 unknown）—— 这是测试与「不想要兜底」场景的开关，
    生产路径下它非空，于是零参可跑。
    """

    if explicit:
        return str(explicit), "cli"
    from_env = os.environ.get(ENV_BASE_URL) or ""
    if from_env:
        return from_env, "env"
    from_runtime = runtime_base_url(runtime_file)
    if from_runtime:
        return from_runtime, "runtime"
    fallback = str(DEFAULT_BASE_URL or "").strip()
    if fallback:
        return fallback, "default"
    return "", "none"


def base_url_source_label(source: str) -> str:
    """``base_source`` → 人读来源（未知来源原样显示，不臆造）。"""

    return BASE_URL_SOURCE_LABELS.get(str(source or ""), str(source or "无来源"))


def base_url_notice(base_url: str, source: str) -> str:
    """末档兜底时的一行提示（D2-3 硬要求：**必须说出来**）；其它来源 → ``""``。"""

    if source != "default" or not base_url:
        return ""
    return (
        "提示：未发现 workbench.json，按本机默认端口 %d（%s）；"
        "工作台没起或换了端口时用 --base-url / 环境变量 %s 指定"
        % (DEFAULT_PORT, base_url, ENV_BASE_URL)
    )


@contextlib.contextmanager
def _base_url_env(
    explicit: str | None = None, runtime_file: str | Path | None = None
) -> Iterator[tuple[str, str]]:
    """执行期间把解析出的地址灌进 ``WORKBENCH_BASE``，退出时**原样还原**。

    能力实例是在块执行体里按 ``base_url`` 参数 / 环境变量自建的，入口层不去构造能力，
    所以注入走环境变量这一条公认通道（见 capabilities/*.py ``env_var`` 默认值）。
    三者都需要注入：``cli``（显式）、``runtime`` / ``default``（发现而来）——
    否则「零参可跑」只是入口层自己知道地址，块执行体拿不到。
    """

    resolved_url, resolved_source = resolve_base_url(explicit, runtime_file)
    if not resolved_url:
        yield resolved_url, resolved_source
        return
    previous = os.environ.get(ENV_BASE_URL)
    os.environ[ENV_BASE_URL] = str(resolved_url)
    try:
        yield resolved_url, resolved_source
    finally:
        if previous is None:
            os.environ.pop(ENV_BASE_URL, None)
        else:
            os.environ[ENV_BASE_URL] = previous


# --------------------------------------------------------------------------- #
# 用例参数规格（--params 的合法名字 / 类型 / 必填，来自块 action.params 与事实 params_schema）
# --------------------------------------------------------------------------- #

#: 事实 ``params_schema`` 里**不属用例入参**的条目：能力句柄与传输替身注入位
#: （``simcon`` / ``frames`` 是能力实例，用户从命令行传 JSON 只会打破注入）；
#: ``base_url`` 也有专用开关 ``--base-url``，不走 ``--params``。
RESERVED_PARAM_NAMES: frozenset[str] = frozenset(
    {
        "transport",
        "capability",
        "simcon",
        "simcon_capability",
        "simeter",
        "simeter_capability",
        "module",
        "module_capability",
        "frames",
        "facade",
        "env_var",
        "base_url",
    }
)

#: 对外暴露给用户的标量类型（对象/数组型条目一律视为内部句柄，不进 --params）
SCALAR_TYPES: frozenset[str] = frozenset({"string", "integer", "number", "boolean"})

#: 地址类参数名（REQS-0042 D2-1 的唯一口径）：声明没给出默认值时按两层配置兜底展示
ADDR_PARAM_NAMES: frozenset[str] = frozenset({"meter_addr", "meter_addrs"})

_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}

#: ``params_schema`` 里不算参数定义的 JSON-Schema 关键字（与 loader 口径一致）
_SCHEMA_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "description",
        "title",
        "$schema",
        "definitions",
        "$defs",
        "patternProperties",
        "default",
    }
)

_MISSING = object()
_AMBIGUOUS = object()


def json_type_name(value: Any) -> str:
    """Python 值 → JSON 类型名（bool 必须先判，否则会被当成 integer）。"""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    if value is None:
        return "null"
    return type(value).__name__


def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "boolean":
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False  # JSON Schema 里 true 不是 integer/number
    expected = _PY_TYPES.get(type_name)
    if expected is None:
        return True
    return isinstance(value, expected)


def flatten_params_schema(schema: Any) -> dict[str, dict[str, Any]]:
    """把 ``params_schema`` 归一成 ``{参数名: 定义}``。

    两种写法都认（与 ``blocks/loader.required_params`` 口径一致）：

    - 扁平写法：``{"addr": {"type": "string", "required": true}}``
    - JSON-Schema 写法：``{"type": "object", "properties": {...}, "required": [...]}``
    """
    entries: dict[str, dict[str, Any]] = {}
    if not isinstance(schema, Mapping):
        return entries
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, entry in properties.items():
            if isinstance(entry, Mapping):
                entries[str(name)] = dict(entry)
    for name, entry in schema.items():
        if name in _SCHEMA_KEYWORDS or not isinstance(entry, Mapping):
            continue
        merged = dict(entries.get(str(name)) or {})
        merged.update(entry)
        entries[str(name)] = merged
    top_required = schema.get("required")
    if isinstance(top_required, (list, tuple)):
        for name in top_required:
            entries.setdefault(str(name), {})
    return entries


@dataclass
class ParamDef:
    """一个可传入参数的全部已知定义（同名参数可能被多个块/事实消费）。"""

    name: str
    fact_types: list[str] = field(default_factory=list)      # 事实 params_schema 声明类型
    block_types: list[str] = field(default_factory=list)     # 块声明值推断类型
    patterns: list[str] = field(default_factory=list)
    enums: list[list[Any]] = field(default_factory=list)
    bounds: list[tuple[str, Any]] = field(default_factory=list)  # (minimum/maximum/…, 界限)
    required_by: list[str] = field(default_factory=list)     # 需要该参数的事实 id
    required_in: list[str] = field(default_factory=list)     # 需要它的用例 id（报错指路）
    fact_ids: list[str] = field(default_factory=list)        # 会把值传给的事实
    block_ids: list[str] = field(default_factory=list)       # 会把值覆盖到的块
    declared: list[tuple[str, Any]] = field(default_factory=list)  # (block_id, 声明值)
    fact_defaults: list[Any] = field(default_factory=list)   # 事实 params_schema 的 default
    step_decls: list[dict[str, Any]] = field(default_factory=list)  # plan[].params 声明

    def add_fact_schema(self, fact_id: str, entry: Mapping[str, Any], *, required: bool) -> None:
        self._push_unique(self.fact_ids, fact_id)
        type_name = entry.get("type")
        if isinstance(type_name, str):
            self._push_unique(self.fact_types, type_name)
        pattern = entry.get("pattern")
        if isinstance(pattern, str) and pattern:
            self._push_unique(self.patterns, pattern)
        enum = entry.get("enum")
        if isinstance(enum, (list, tuple)) and enum:
            self.enums.append(list(enum))
        for key in ("minimum", "maximum", "minLength", "maxLength"):
            if key in entry:
                self.bounds.append((key, entry[key]))
        if "default" in entry and entry.get("default") is not None:
            self.fact_defaults.append(entry["default"])
        if required:
            self._push_unique(self.required_by, fact_id)

    def add_block_param(self, block_id: str, name: str, value: Any) -> None:
        self._push_unique(self.block_ids, block_id)
        self.declared.append((block_id, value))
        inferred = json_type_name(value)
        if inferred != "null":
            self._push_unique(self.block_types, inferred)

    def add_step_param(self, step_id: str, block_id: str, name: str, value: Any) -> None:
        """登记 ``plan[].params`` 里的参数（步骤实例化，REQS-0042 D3-1）。

        与 ``add_block_param`` 的区别：步骤参数**不是**块默认值，不能进 ``declared``
        （否则 ``unique_declared`` 会把「两步各带不同地址」误判成「声明歧义」）。
        这里只做两件事：

        - 把 ``block_id`` 记进 ``block_ids``：run 级 ``--params`` 仍要能广播到这个块
          （D3-2：run 级 > step.params）；
        - 登记原始声明，供 ``show`` 展示（**不登记就不会被接受**，``--params`` 会报「多余参数」）。

        **不登记类型**：参数类型以块/事实声明为权威。若把步骤里这次给的值类型也并进
        ``block_types``，同一参数的「块声明 integer、步骤写 string」会让两种取值互相
        判非法（一个合法值被另一条约束拒掉），那是把类型校验搞成自锁。
        """

        self._push_unique(self.block_ids, block_id)
        self.step_decls.append(
            {"step_id": step_id, "block_id": block_id, "value": value}
        )

    @staticmethod
    def _push_unique(target: list, item: Any) -> None:
        if item not in target:
            target.append(item)

    def unique_declared(self) -> Any:
        """块声明值是否给出唯一取值；``_MISSING`` / ``_AMBIGUOUS`` 表示不能用。

        ``null`` 声明（``"meter_addr": null``）是「取两层配置默认」的占位（REQS-0042
        D2-2），由 ``blocks/loader.resolve_declaration_defaults`` 在加载期解析成真值；
        解析不出来时这里**跳过**它 —— 把 ``None`` 当权威取值广播给块，等于发一帧
        地址全空的报文，比「没有默认值」更坏。
        """

        seen: dict[str, Any] = {}
        for _block_id, value in self.declared:
            if value is None:
                continue
            seen[json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)] = value
        if not seen:
            return _MISSING
        if len(seen) > 1:
            return _AMBIGUOUS
        return next(iter(seen.values()))

    def effective_default(self) -> Any:
        """本参数的默认取值（``show`` 展示 + 「不传也能跑」的依据）；取不到 → ``_MISSING``。

        顺序：块声明唯一取值（含 loader 解析过的 ``null``） > 事实 ``params_schema.default``
        > 地址类参数的**两层配置默认**（:func:`case_runner.core.defaults.default_meter_addr`）。
        """

        declared = self.unique_declared()
        if declared is not _MISSING and declared is not _AMBIGUOUS:
            return declared
        for value in self.fact_defaults:
            if value is not None:
                return value
        if self.name in ADDR_PARAM_NAMES:
            config_default = defaults.default_meter_addr()
            if config_default:
                return config_default
        return _MISSING

    def describe_declared(self) -> str:
        return "、".join(
            f"{block_id}={json.dumps(value, ensure_ascii=False, default=str)}"
            for block_id, value in self.declared
        )

    def to_dict(self) -> dict[str, Any]:
        default = self.effective_default()
        return {
            "name": self.name,
            "fact_types": list(self.fact_types),
            "block_types": list(self.block_types),
            "patterns": list(self.patterns),
            "required_by": list(self.required_by),
            "required_in": list(self.required_in),
            "fact_ids": list(self.fact_ids),
            "block_ids": list(self.block_ids),
            "has_default": default is not _MISSING,
            "default": None if default is _MISSING else default,
            "default_source": self._default_source(),
            "declared": [
                {"block_id": block_id, "value": value} for block_id, value in self.declared
            ],
            # 步骤实例化声明（plan[].params）：同一块可多步各带不同取值
            "step_declared": [dict(item) for item in self.step_decls],
        }

    def _default_source(self) -> str:
        """默认值是哪一层的（人读诊断用：``块声明`` / ``事实 schema`` / ``配置默认``）。"""

        declared = self.unique_declared()
        if declared is not _MISSING and declared is not _AMBIGUOUS:
            return "块声明"
        if any(value is not None for value in self.fact_defaults):
            return "事实 params_schema"
        if self.name in ADDR_PARAM_NAMES and defaults.default_meter_addr():
            return "两层配置（case_defaults.json）"
        return ""


@dataclass
class CaseParamSpec:
    """一次运行的参数规格（单用例或整方案合并后的视图）。"""

    scope: str
    defs: dict[str, ParamDef] = field(default_factory=dict)

    # -- 查询 --------------------------------------------------------------- #

    def names(self) -> list[str]:
        return list(self.defs)

    def required_names(self) -> list[str]:
        return [name for name, item in self.defs.items() if item.required_by]

    def _def(self, name: str) -> ParamDef:
        definition = self.defs.get(name)
        if definition is None:
            definition = ParamDef(name=name)
            self.defs[name] = definition
        return definition

    # -- 构建 --------------------------------------------------------------- #

    def add_fact(self, fact: FactSpec) -> None:
        entries = flatten_params_schema(fact.params_schema)
        required = set(decl_loader.required_params(fact))
        for name, entry in entries.items():
            if name in RESERVED_PARAM_NAMES:
                continue
            type_name = entry.get("type")
            # 非标量且无默认值 → 视为内部句柄（如能力对象），不暴露给 --params
            if (
                isinstance(type_name, str)
                and type_name not in SCALAR_TYPES
                and "default" not in entry
            ):
                continue
            self._def(name).add_fact_schema(fact.id, entry, required=name in required)

    def add_block(self, spec: BlockSpec) -> None:
        nested = spec.action.get("params") if isinstance(spec.action, Mapping) else None
        if not isinstance(nested, Mapping):
            return
        for name, value in nested.items():
            self._def(str(name)).add_block_param(spec.id, str(name), value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "params": {name: item.to_dict() for name, item in self.defs.items()},
            "required": self.required_names(),
        }

    # -- 校验 + 解析 -------------------------------------------------------- #

    def resolve(self, values: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """校验 ``--params`` 并按需补「块声明唯一取值」，返回 ``{参数名: 值}``。

        任何一条不合法 → :class:`UsageError`（一次报全），**不返回半成品**，
        调用方据此在进入执行体之前退出（退出码 3）。
        """
        provided = dict(values or {})
        problems: list[str] = []

        extra = [name for name in provided if name not in self.defs]
        if extra:
            accepted = "、".join(self.names()) or "（本用例没有可传参数）"
            problems.append(
                "多余参数：%s；本用例可接受的参数：%s"
                % ("、".join(sorted(extra)), accepted)
            )

        resolved: dict[str, Any] = {}
        for name, definition in self.defs.items():
            if name in provided:
                problems.extend(_check_param_value(name, provided[name], definition))
                resolved[name] = provided[name]

        for name, definition in self.defs.items():
            if name in resolved or not definition.required_by:
                continue
            fallback = definition.unique_declared()
            if fallback is _MISSING:
                problems.append(
                    "缺少必填参数 %r：事实 %s 需要它，--params 里没有给出，"
                    "块声明也没给出取值（请用 --params 指定，如 '{\"%s\": ...}'）"
                    % (name, "、".join(definition.required_by), name)
                )
            elif fallback is _AMBIGUOUS:
                problems.append(
                    "缺少必填参数 %r：事实 %s 需要它，块声明取值不一致（%s），"
                    "请用 --params 显式指定" % (name, "、".join(definition.required_by),
                                                definition.describe_declared())
                )
            else:
                resolved[name] = fallback

        if problems:
            raise UsageError(problems, headline="用例入参校验未通过")
        return resolved

    def split(
        self, resolved: Mapping[str, Any]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """解析结果 → ``(block_params, fact_params)``。

        同一个值广播给**所有消费它的**块与事实（如 ``meter_addr`` 同时进 ``archive.add``
        的块参数和 ``archive.contains`` 的事实参数）；不消费它的对象完全不碰。
        """
        block_params: dict[str, dict[str, Any]] = {}
        fact_params: dict[str, dict[str, Any]] = {}
        for name, value in resolved.items():
            definition = self.defs[name]
            for block_id in definition.block_ids:
                block_params.setdefault(block_id, {})[name] = value
            for fact_id in definition.fact_ids:
                fact_params.setdefault(fact_id, {})[name] = value
        return block_params, fact_params


def _check_param_value(name: str, value: Any, definition: ParamDef) -> list[str]:
    """单个参数值的校验（类型 / 正则 / 枚举 / 上下限）。"""
    problems: list[str] = []
    for type_name in definition.fact_types:
        if not _matches_type(value, type_name):
            problems.append(
                "参数 %r 类型非法：事实声明为 %s，实际是 %s（%r）"
                % (name, type_name, json_type_name(value), value)
            )
    for type_name in definition.block_types:
        if not _matches_type(value, type_name):
            problems.append(
                "参数 %r 类型非法：块声明为 %s，实际是 %s（%r）"
                % (name, type_name, json_type_name(value), value)
            )
    for pattern in definition.patterns:
        if isinstance(value, str) and not re.fullmatch(pattern, value):
            problems.append(
                "参数 %r 取值非法：不匹配 %s，实际为 %r" % (name, pattern, value)
            )
    for enum in definition.enums:
        if value not in enum:
            problems.append(
                "参数 %r 取值非法：允许 %s，实际为 %r" % (name, enum, value)
            )
    for key, limit in definition.bounds:
        if key == "minLength" and isinstance(value, str):
            if len(value) < limit:
                problems.append("参数 %r 长度不足：最少 %r 个字符" % (name, limit))
        elif key == "maxLength" and isinstance(value, str):
            if len(value) > limit:
                problems.append("参数 %r 长度超限：最多 %r 个字符" % (name, limit))
        elif key == "minimum" and isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < limit:
                problems.append("参数 %r 取值过小：最小 %r，实际 %r" % (name, limit, value))
        elif key == "maximum" and isinstance(value, (int, float)) and not isinstance(value, bool):
            if value > limit:
                problems.append("参数 %r 取值过大：最大 %r，实际 %r" % (name, limit, value))
    return problems


def case_param_spec(case: CaseSpec, bundle: Bundle) -> CaseParamSpec:
    """单用例参数规格：用例 needs ∪ 各块 needs 的事实 + 各块 action.params + 各步 params。

    步骤实例化（REQS-0042 D3-1）之后，参数来源多了一层 ``plan[].params``：
    步骤里写的参数名必须被认（否则 ``--params`` 会报「多余参数」），
    同时 run 级 ``--params`` 仍要能广播到该块的所有步骤（D3-2：run 级 > 步骤声明）。
    展开入口统一走 :meth:`CaseSpec.effective_steps` —— 老用例（只有 ``blocks``）
    得到的步骤序列与改动前逐个块完全一致。
    """
    spec = CaseParamSpec(scope=case.id)
    facts_by_id = bundle.facts_by_id
    block_specs = bundle.block_specs
    steps = case.effective_steps()

    for fact_id in dict.fromkeys(case.needs):
        fact = facts_by_id.get(fact_id)
        if fact is not None:
            spec.add_fact(fact)
    for step in steps:
        block = block_specs.get(step.block)
        if block is None:
            continue
        for fact_id in block.needs:
            fact = facts_by_id.get(fact_id)
            if fact is not None:
                spec.add_fact(fact)
    for step in steps:
        block = block_specs.get(step.block)
        if block is not None:
            spec.add_block(block)
        for name, value in step.params.items():
            spec._def(str(name)).add_step_param(  # noqa: SLF001 - 同类内部构建
                step.id, step.block, str(name), value
            )

    for name, definition in spec.defs.items():
        if definition.required_by:
            definition.required_in.append(case.id)
    return spec


def suite_param_spec(suite: SuiteSpec, cases: Sequence[CaseSpec], bundle: Bundle) -> CaseParamSpec:
    """整方案参数规格 = 各用例规格的合并（多余参数按**所有**用例的并集判）。"""
    merged = CaseParamSpec(scope=suite.id)
    for case in cases:
        one = case_param_spec(case, bundle)
        for name, definition in one.defs.items():
            target = merged._def(name)  # noqa: SLF001 - 同类内部构建
            target.fact_types = list(dict.fromkeys(target.fact_types + definition.fact_types))
            target.block_types = list(dict.fromkeys(target.block_types + definition.block_types))
            target.patterns = list(dict.fromkeys(target.patterns + definition.patterns))
            target.enums.extend(definition.enums)
            target.bounds.extend(definition.bounds)
            target.required_by = list(
                dict.fromkeys(target.required_by + definition.required_by)
            )
            target.required_in = list(
                dict.fromkeys(target.required_in + definition.required_in)
            )
            target.fact_ids = list(dict.fromkeys(target.fact_ids + definition.fact_ids))
            target.block_ids = list(dict.fromkeys(target.block_ids + definition.block_ids))
            target.declared.extend(definition.declared)
            target.fact_defaults.extend(definition.fact_defaults)
            target.step_decls.extend(dict(item) for item in definition.step_decls)
    return merged


# --------------------------------------------------------------------------- #
# 运行装配（只装配，不判定）
# --------------------------------------------------------------------------- #


def new_run_id() -> str:
    """运行号：``YYYYmmdd-HHMMSS-<6 位随机>``（同秒多次运行也可区分）。"""
    return "%s-%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])


@dataclass
class RunOutcome:
    """一次 ``run`` 的结果：走内核的结果对象 + 实际生效的入参（供输出复述）。"""

    case: CaseSpec
    result: CaseRunResult
    resolved_params: dict[str, Any] = field(default_factory=dict)
    skipped_reason: str = ""

    @property
    def exit_code(self) -> int:
        return exit_code_for_verdict(self.result.verdict)

    def to_dict(self) -> dict[str, Any]:
        payload = self.result.to_dict()
        payload["exit_code"] = self.exit_code
        if self.resolved_params:
            payload["params"] = dict(self.resolved_params)
        if self.skipped_reason:
            payload["skipped_reason"] = self.skipped_reason
        return payload


def exit_code_for_verdict(verdict: str) -> int:
    if verdict == OUTCOME_PASS:
        return EXIT_PASS
    if verdict == OUTCOME_FAIL:
        return EXIT_FAIL
    return EXIT_INCOMPLETE


def exit_code_for_states(states: Iterable[str]) -> int:
    """按块/事实状态算退出码：``fail`` 优先，其次「未完成验证」。"""
    seen = list(states)
    if any(state == BLOCK_FAIL for state in seen):
        return EXIT_FAIL
    if seen and all(state == BLOCK_PASS for state in seen):
        return EXIT_PASS
    return EXIT_INCOMPLETE


def build_run_context(
    bundle: Bundle,
    *,
    run_id: str,
    block_params: Mapping[str, Mapping[str, Any]],
    fact_params: Mapping[str, Mapping[str, Any]],
    extras: Mapping[str, Any] | None = None,
    fact_probe: Callable[[str, dict[str, Any]], Any] | None = None,
    stop_on_fail: bool = False,
    step_params: Mapping[str, Mapping[str, Any]] | None = None,
) -> RunContext:
    """装配执行上下文（事实缓存 + 块声明 + 入参 + 注入的能力替身）。

    ``block_params`` 是 run 级参数（按块广播，优先级最高）；``step_params`` 是按**步骤**
    覆盖（REQS-0042 D3-1，同块多次实例化要只改某一步时用）。CLI 的 ``--params`` 走前者，
    后者留给 API/后续快照层按需注入。
    """
    facts_ctx = FactsContext(
        probe=fact_probe,  # None → 走注册表里的默认探测体
        params={key: dict(value) for key, value in (fact_params or {}).items()},
    )
    return RunContext(
        run_id=run_id,
        facts_ctx=facts_ctx,
        block_specs=bundle.block_specs,
        block_params={key: dict(value) for key, value in (block_params or {}).items()},
        step_params={key: dict(value) for key, value in (step_params or {}).items()},
        stop_on_fail=stop_on_fail,
        extras=dict(extras or {}),
    )


def run_case(
    bundle: Bundle,
    case: CaseSpec,
    *,
    resolved_params: Mapping[str, Any],
    param_spec: CaseParamSpec,
    run_id: str,
    extras: Mapping[str, Any] | None = None,
    fact_probe: Callable[[str, dict[str, Any]], Any] | None = None,
    stop_on_fail: bool = False,
) -> RunOutcome:
    """跑一个用例：装配 → 内核执行 → 返回结果（判定全在块与事实里）。"""
    block_params, fact_params = param_spec.split(resolved_params)
    ctx = build_run_context(
        bundle,
        run_id=run_id,
        block_params=block_params,
        fact_params=fact_params,
        extras=extras,
        fact_probe=fact_probe,
        stop_on_fail=stop_on_fail,
    )
    result = execute_case(case, ctx)
    return RunOutcome(case=case, result=result, resolved_params=dict(resolved_params))


def skipped_outcome(case: CaseSpec, *, run_id: str, reason: str) -> RunOutcome:
    """未执行（方案 stop_on_fail 或用户取消）也要有记录，且**绝不算 pass**。"""
    record = CaseRunResult(
        case_id=case.id,
        run_id=run_id,
        blocks=[],
        assertions=[
            AssertionResult(
                run_id=run_id,
                assertion_id=f"{case.id}.not_run",
                outcome="inconclusive",
                message=reason,
            )
        ],
        verdict="inconclusive",
    )
    return RunOutcome(case=case, result=record, skipped_reason=reason)


# --------------------------------------------------------------------------- #
# 参数解析（--params / --case-file）
# --------------------------------------------------------------------------- #


def check_renamed_params(values: Mapping[str, Any]) -> list[str]:
    """``--params`` 里出现已改名的旧参数名 → 迁移提示（REQS-0042 D2-1，**不留别名**）。

    为什么硬拒绝而不是兼容：``addr`` 与 ``meter_addr`` 是**语义相反**的两个地址
    （电表 645 地址 vs CCO 档案地址，互为 6 字节反转，见 REQS G-1）。留一个同名别名
    就等于把「地址角色错位」原样保留下来——用户以为传了地址，实际把另一种地址发出去。
    所以宁可报错、给出迁移写法，也不猜。
    """

    problems: list[str] = []
    for old_name, new_name in RENAMED_PARAMS:
        if old_name not in values:
            continue
        if old_name == "meters":
            hint = '{"meter_addrs": ["12位表地址", ...]}'
        else:
            hint = '{"%s": "12位表地址"}' % new_name
        problems.append(
            "参数 %s 已改名为 %s（REQS 0042 D2-1，不再兼容旧名），请改用 %s"
            % (old_name, new_name, hint)
        )
    return problems


def parse_params_json(text: str | None, flag: str = "--params") -> dict[str, Any]:
    """``--params`` → dict；非法 JSON / 非对象 / 旧参数名 → :class:`UsageError`（退出码 3）。"""
    if text is None or not str(text).strip():
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UsageError(
            [f"{flag} 不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}（原文：{text!r}）"]
        ) from None
    if not isinstance(value, dict):
        raise UsageError([f"{flag} 必须是 JSON 对象（键=参数名），实际是 {type(value).__name__}"])
    renamed = check_renamed_params(value)
    if renamed:
        raise UsageError(renamed, headline="参数名已改名（REQS 0042 D2-1）")
    return value


def missing_blocks_of(case: CaseSpec, bundle: Bundle) -> list[str]:
    """用例引用但未声明/未注册的块（``blocks[]`` 与 ``plan[].block`` 都算）。

    步骤实例化之后「用例用了哪些块」不再等于 ``case.blocks``——plan 形态下
    ``blocks`` 可能是空的，必须走 :meth:`CaseSpec.block_ids` 才能查全。
    同一块被实例化多次时只报一次（去重保序），便于人读。
    """

    missing: list[str] = []
    for block_id in case.block_ids():
        if block_id in bundle.block_specs or has_block(block_id):
            continue
        if block_id not in missing:
            missing.append(block_id)
    return missing


def load_case_file(path: str | Path, bundle: Bundle) -> CaseSpec:
    """读临时用例 JSON（不落库、不入声明目录），并做引用校验。"""
    target = Path(path).expanduser()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UsageError([f"用例文件读取失败：{target}：{exc}"]) from None
    except json.JSONDecodeError as exc:
        raise UsageError(
            [f"用例文件 JSON 解析失败：{target}：第 {exc.lineno} 行第 {exc.colno} 列：{exc.msg}"]
        ) from None
    from pydantic import ValidationError

    try:
        case = CaseSpec.model_validate(raw)
    except ValidationError as exc:
        problems = [
            "用例文件字段非法：%s：%s"
            % (".".join(str(part) for part in error.get("loc") or ()) or "<root>",
               error.get("msg", "校验失败"))
            for error in exc.errors()
        ]
        raise UsageError(problems, headline="用例文件校验未通过") from None
    missing = missing_blocks_of(case, bundle)
    if missing:
        raise UsageError(
            [
                "用例文件引用了不存在的块：%s（已声明：%s）"
                % ("、".join(missing), "、".join(sorted(bundle.block_specs)) or "无")
            ]
        )
    missing_facts = [
        fact_id for fact_id in case.needs
        if fact_id not in bundle.facts_by_id and not has_fact(fact_id)
    ]
    if missing_facts:
        raise UsageError(
            ["用例文件引用了不存在的事实：%s" % "、".join(missing_facts)]
        )
    return case


# --------------------------------------------------------------------------- #
# 文本对齐（中文按两格宽计算，表格才不会歪）
# --------------------------------------------------------------------------- #


def display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    padding = width - display_width(text)
    return text + " " * padding if padding > 0 else text


def truncate(text: str, limit: int) -> str:
    """按显示宽度截断（超出加省略号）；中文按两格宽算。"""
    if display_width(text) <= limit:
        return text
    out = ""
    width = 0
    for char in text:
        step = 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
        if width + step > limit - 1:
            break
        out += char
        width += step
    return out + "…"


def _print(out: TextIO, text: str = "") -> None:
    print(text, file=out)


# --------------------------------------------------------------------------- #
# 人读渲染
# --------------------------------------------------------------------------- #

def render_counts(counts: Mapping[str, int]) -> str:
    return " / ".join(
        "%s %d" % (label, counts.get(state, 0)) for state, label in STATE_LABELS
    ) + " / 共 %d" % counts.get("total", 0)


def render_case_result(
    outcome: RunOutcome, *, out: TextIO | None = None, head: bool = True
) -> None:
    """人读：结论 + 块明细 + 未满足事实 + 断点定位（用户最关心的四块）。"""
    out = out or sys.stdout
    result = outcome.result
    case = outcome.case
    if head:
        _print(out, "═══ 用例执行结果 ═══")
    _print(out, "用例：%s —— %s" % (case.id, case.title))
    _print(out, "运行号：%s" % (result.run_id or "-"))
    _print(
        out,
        "结论：%s（退出码 %d）"
        % (_verdict_label(result.verdict), outcome.exit_code),
    )
    if outcome.skipped_reason:
        _print(out, "说明：%s" % outcome.skipped_reason)
    if outcome.resolved_params:
        _print(
            out,
            "生效入参：%s"
            % "、".join(
                "%s=%s" % (name, json.dumps(value, ensure_ascii=False))
                for name, value in outcome.resolved_params.items()
            ),
        )
    _print(out, "块计数：%s" % render_counts(result.counts))
    _print(out)

    if result.blocks:
        # 步骤实例化（D3-1）：同一块可能出现多次，只有 step_id 能区分；
        # 老用例（step_id == 块 id）不多印一列，输出与改动前一致。
        show_step = any(
            (record.step_id or record.block_id) != record.block_id
            for record in result.blocks
        )
        _print(out, "块执行明细%s：" % ("（按步骤顺序）" if show_step else ""))
        width = max((display_width(record.block_id) for record in result.blocks), default=0)
        width = max(width, len("块 id"))
        step_width = 0
        if show_step:
            step_width = max(
                [display_width(record.step_id or record.block_id) for record in result.blocks]
                + [len("step id")]
            )
        head = "  %s  %s  " % (pad("#", 3), pad("状态", 8))
        if show_step:
            head += "%s  " % pad("step id", step_width)
        head += "%s  说明" % pad("块 id", width)
        _print(out, head)
        for index, record in enumerate(result.blocks, start=1):
            line = "  %s  %s  " % (pad("%d" % index, 3), pad(record.state, 8))
            if show_step:
                line += "%s  " % pad(record.step_id or record.block_id, step_width)
            line += "%s  %s" % (
                pad(record.block_id, width),
                truncate(record.detail or "-", 88),
            )
            _print(out, line)
        if show_step:
            _print(
                out,
                "  步骤结论：%s"
                % "、".join(
                    "%s=%s" % (record.step_id or record.block_id, record.state)
                    for record in result.blocks
                ),
            )
    else:
        _print(out, "块执行明细：（无块被执行）")
    _print(out)

    unmet = result.unmet_facts
    _print(out, "未满足的事实（unmet_facts，共 %d）：" % len(unmet))
    if not unmet:
        _print(out, "  （无）")
    else:
        width = max(display_width(fact_id) for fact_id in unmet)
        for fact_id, value in unmet.items():
            _print(
                out,
                "  %s  %s  %s"
                % (
                    pad(fact_id, width),
                    pad(_state_of(value), 8),
                    truncate(str(value.get("detail") or "-"), 88),
                ),
            )
    _print(out)

    _print(out, "断点定位：")
    _print(out, "  failed_block：%s" % (result.failed_block or "（无）"))
    if result.failed_block:
        # 步骤实例化后同一块可能有多步：优先按 step_id 取那**一条**记录，
        # 否则「第一步 pass、第二步 fail」会打印第一步的说明（误导）。
        detail = _block_detail(result, result.failed_step or result.failed_block)
        _print(
            out,
            "     失败原因%s：%s"
            % (
                "（步骤 %s）" % result.failed_step
                if result.failed_step and result.failed_step != result.failed_block
                else "",
                detail or "（该块未留下说明）",
            ),
        )
    _print(out, "  blocked_block：%s" % (result.blocked_block or "（无）"))
    if result.blocked_block:
        detail = _block_detail(result, result.blocked_step or result.blocked_block)
        _print(
            out,
            "     阻断原因%s：%s"
            % (
                "（步骤 %s）" % result.blocked_step
                if result.blocked_step and result.blocked_step != result.blocked_block
                else "",
                detail or "（该块未留下说明）",
            ),
        )
    non_pass = [
        (record.step_id or record.block_id)
        if (record.step_id or record.block_id) != record.block_id
        else record.block_id
        for record in result.blocks
        if record.state != BLOCK_PASS
    ]
    _print(
        out,
        "  非 pass 块：%s" % ("、".join(non_pass) if non_pass else "（无）"),
    )
    if unmet:
        _print(out, "  未满足事实：%s" % "、".join(unmet))


def _block_detail(result: CaseRunResult, ref: str) -> str:
    """按**步骤 id 优先、块 id 兜底**取那一条记录的说明（同块多步时块 id 会取错）。"""

    for record in result.blocks:
        if (record.step_id or record.block_id) == ref:
            return record.detail
    for record in result.blocks:
        if record.block_id == ref:
            return record.detail
    return ""


def _verdict_label(verdict: str) -> str:
    return {OUTCOME_PASS: "PASS", OUTCOME_FAIL: "FAIL"}.get(verdict, "INCOMPLETE（未完成验证）")


def render_probe(payload: Mapping[str, Any], *, out: TextIO) -> None:
    """人读：逐块 / 逐事实自检表（**没有 pass 就是没验证出来**）。"""
    _print(out, "═══ 台架自检 ═══")
    _print(out, "声明根：%s" % payload.get("data_dir", "-"))
    source = payload.get("base_url_source", "none")
    _print(
        out,
        "工作台地址：%s（%s）"
        % (
            payload.get("base_url") or "未提供（块/事实将按 no_base_url 判为不可用）",
            base_url_source_label(source),
        ),
    )
    _print(out)

    blocks = list(payload.get("blocks") or [])
    facts = list(payload.get("facts") or [])
    block_counts = payload.get("block_counts") or {}
    fact_counts = payload.get("fact_counts") or {}

    if blocks:
        _print(out, "块自检（共 %d；%s）" % (len(blocks), _render_probe_counts(block_counts)))
        width = max(max((display_width(item["block_id"]) for item in blocks), default=0),
                    len("块 id"))
        fact_width = max(
            max((display_width(item.get("needs_text") or "-") for item in blocks), default=0),
            len("前置事实"),
        )
        _print(
            out,
            "  %s  %s  %s  %s"
            % (pad("状态", 8), pad("块 id", width), pad("前置事实", fact_width), "说明"),
        )
        for item in blocks:
            _print(
                out,
                "  %s  %s  %s  %s"
                % (
                    pad(item["state"], 8),
                    pad(item["block_id"], width),
                    pad(item.get("needs_text") or "-", fact_width),
                    truncate(item.get("detail") or "-", 84),
                ),
            )
        _print(out)
    if facts:
        _print(out, "事实自检（共 %d；%s）" % (len(facts), _render_probe_counts(fact_counts)))
        width = max(max((display_width(item["fact_id"]) for item in facts), default=0),
                    len("事实 id"))
        _print(out, "  %s  %s  %s" % (pad("状态", 8), pad("事实 id", width), "说明"))
        for item in facts:
            _print(
                out,
                "  %s  %s  %s"
                % (pad(item["state"], 8), pad(item["fact_id"], width),
                   truncate(item.get("detail") or "-", 96)),
            )
        _print(out)

    _print(out, "结论：%s" % payload.get("summary", "-"))
    if payload.get("hint"):
        _print(out, "提示：%s" % payload["hint"])
    _print(out, "退出码：%d" % payload.get("exit_code", EXIT_INCOMPLETE))


def _render_probe_counts(counts: Mapping[str, int]) -> str:
    total = counts.get("total", sum(counts.values()))
    return " / ".join(
        "%s %d" % (label, counts.get(state, 0))
        for state, label in STATE_LABELS + (("yes", "yes"), ("no", "no"))
        if state in counts
    ) + " / 共 %d" % total


def render_list(payload: Mapping[str, Any], *, out: TextIO) -> None:
    _print(out, "声明根：%s" % payload.get("data_dir", "-"))
    problems = payload.get("problems") or []
    if problems:
        _print(out, "声明问题（%d）：" % len(problems))
        for problem in problems:
            _print(out, "  - %s" % problem)
    counts = payload.get("counts") or {}
    _print(
        out,
        "计数：块 %d / 事实 %d / 用例 %d / 方案 %d"
        % (
            counts.get("blocks", 0),
            counts.get("facts", 0),
            counts.get("cases", 0),
            counts.get("suites", 0),
        ),
    )
    for kind, label in (
        ("blocks", "块"),
        ("facts", "事实"),
        ("cases", "用例"),
        ("suites", "方案"),
    ):
        items = payload.get(kind)
        if items is None:
            continue
        _print(out)
        _print(out, "%s（%d）：" % (label, len(items)))
        width = max(
            [display_width(str(item.get("id", ""))) for item in items] + [len("id")]
        )
        for item in items:
            ident = str(item.get("id", ""))
            title = str(item.get("title") or item.get("name") or "")
            extra = ""
            if kind == "blocks":
                needs = "、".join(item.get("needs") or []) or "无"
                extra = "  前置事实：%s" % needs
            elif kind == "facts":
                extra = "  probe：%s%s" % (
                    item.get("probe", "-"),
                    "  必填：%s" % "、".join(item.get("required") or [])
                    if item.get("required") else "",
                )
            elif kind == "cases":
                # REQS-0042 D3-1：显示「步骤数」而不是「块数」——同一块可实例化多次
                extra = "  步骤 %d%s：%s" % (
                    item.get("step_count", len(item.get("blocks") or [])),
                    "（plan 显式声明）" if item.get("uses_plan") else "",
                    "、".join(item.get("block_ids") or []) or "无",
                )
            elif kind == "suites":
                extra = "  用例 %d：%s" % (
                    len(item.get("case_ids") or []),
                    "、".join(item.get("case_ids") or []),
                )
            _print(out, "  %s  %s%s" % (pad(ident, width), title, extra))


def _render_steps_table(
    rows: Sequence[Mapping[str, Any]], *, out: TextIO, indent: str = ""
) -> None:
    """步骤表（REQS-0042 D3-1）：step id / 块 id / 关键参数 / on_fail。

    「块清单」只说明**用了哪些块**，看不出「同一个块跑了几次、每次带什么参数」；
    步骤表补的就是这件事（用户诉求：一次下发多块不同档案）。``on_fail`` 写「继承」
    表示该步没显式声明，跟随 run/方案级 ``stop_on_fail``。
    """

    _print(out, "%s步骤表（执行顺序）：" % indent)
    if not rows:
        _print(out, "%s  （无步骤）" % indent)
        _print(out)
        return
    id_width = max(
        [display_width(str(row.get("step_id", ""))) for row in rows] + [len("step id")]
    )
    block_width = max(
        [display_width(str(row.get("block_id", ""))) for row in rows] + [len("块 id")]
    )
    _print(
        out,
        "%s  %s  %s  %s  %s  %s"
        % (
            indent,
            pad("#", 3),
            pad("step id", id_width),
            pad("块 id", block_width),
            pad("关键参数", 36),
            "on_fail",
        ),
    )
    for row in rows:
        params = row.get("params") or {}
        params_text = (
            "、".join(
                "%s=%s" % (name, json.dumps(value, ensure_ascii=False, default=str))
                for name, value in params.items()
            )
            or "—（用块声明参数）"
        )
        _print(
            out,
            "%s  %s  %s  %s  %s  %s"
            % (
                indent,
                pad("%d" % row.get("no", 0), 3),
                pad(str(row.get("step_id", "")), id_width),
                pad(str(row.get("block_id", "")), block_width),
                pad(truncate(params_text, 36), 36),
                row.get("on_fail") or "继承",
            ),
        )
        if row.get("note"):
            _print(out, "%s     备注：%s" % (indent, row["note"]))
        if row.get("error"):
            _print(out, "%s      ⚠ %s" % (indent, row["error"]))
    _print(out)


def render_show(payload: Mapping[str, Any], *, out: TextIO) -> None:
    kind = payload.get("kind")
    item = payload.get("item") or {}
    _print(out, "声明根：%s" % payload.get("data_dir", "-"))
    _print(out)

    if kind == "case":
        _print(out, "用例：%s —— %s" % (item.get("id"), item.get("title")))
        tags = "、".join(item.get("tags") or []) or "无"
        _print(out, "标签：%s" % tags)
        _print(out, "前置事实（needs）：%s" % ("、".join(item.get("needs") or []) or "无"))
        _print(
            out,
            "步骤数：%d（%s）"
            % (
                len(item.get("steps_table") or []),
                "plan 显式声明：同一块可多次实例化，各步参数独立"
                if item.get("uses_plan")
                else "由 blocks 自动展开：一块 = 一步",
            ),
        )
        _print(out, "块数量：%d" % len(item.get("blocks") or []))
        _print(out)
        _print(out, "步骤说明：")
        for step in item.get("steps") or []:
            _print(out, "  %d. %s" % (step.get("no", 0), step.get("do")))
            _print(out, "     预期：%s" % step.get("expect"))
        _print(out)
        _render_steps_table(item.get("steps_table") or [], out=out)
        _print(out, "块清单（按执行顺序）：")
        width = max(
            [display_width(str(block.get("id", ""))) for block in item.get("blocks") or []]
            + [len("块 id")]
        )
        for index, block in enumerate(item.get("blocks") or [], start=1):
            _print(
                out,
                "  %2d. %s  %s" % (index, pad(str(block.get("id", "")), width),
                                  block.get("title") or ""),
            )
            if block.get("error"):
                _print(out, "      ⚠ %s" % block["error"])
            if block.get("needs"):
                _print(out, "      前置事实：%s" % "、".join(block["needs"]))
            if block.get("params_text"):
                _print(out, "      块声明参数：%s" % block["params_text"])
            if block.get("provides"):
                _print(out, "      提供事实：%s" % "、".join(block["provides"]))
        _print(out)
        _print(out, "用例级断言：%d 条%s" % (
            len(item.get("asserts") or []),
            "（本 CLI 不提供断言求值器 → 未求值，结论按 unknown 计，绝不假 PASS）"
            if item.get("asserts") else "",
        ))
    elif kind == "block":
        _print(out, "块：%s —— %s" % (item.get("id"), item.get("title")))
        _print(out, "前置事实（needs）：%s" % ("、".join(item.get("needs") or []) or "无"))
        _print(out, "提供事实（provides）：%s" % ("、".join(item.get("provides") or []) or "无"))
        _print(out, "标签：%s" % ("、".join(item.get("tags") or []) or "无"))
        # 显示真实的 action 键（``afn`` / ``fn`` / ``params`` / ``recipe`` / ``semantic``），
        # 不是空对象 —— 之前取错键名，常驻输出一行 ``action：{}``，纯噪音。
        _print(
            out,
            "action 键：%s" % ("、".join(item.get("action_keys") or []) or "（无）"),
        )
        if item.get("params"):
            _print(out, "action.params：%s"
                   % json.dumps(item.get("params"), ensure_ascii=False))
        _print(out, "断言声明（assert）：%s"
               % json.dumps(item.get("assert") or [], ensure_ascii=False))
        if item.get("cleanup"):
            _print(out, "清理声明（cleanup）：%s"
                   % json.dumps(item.get("cleanup"), ensure_ascii=False))
        _print(out, "可传入参数（--params）：%s" % (item.get("params_text") or "无"))
    elif kind == "fact":
        _print(out, "事实：%s —— %s" % (item.get("id"), item.get("title")))
        _print(out, "探测体（probe）：%s" % item.get("probe"))
        _print(out, "必填参数：%s" % ("、".join(item.get("required") or []) or "无"))
        _print(out, "可传入参数（--params）：%s" % (item.get("params_text") or "无"))
        _print(out, "params_schema：%s"
               % json.dumps(item.get("params_schema") or {}, ensure_ascii=False, indent=2))
    else:  # suite
        _print(out, "方案：%s —— %s" % (item.get("id"), item.get("name")))
        _print(out, "用例（%d）：%s" % (
            len(item.get("case_ids") or []), "、".join(item.get("case_ids") or [])))
        _print(out, "stop_on_fail：%s" % ("是" if item.get("stop_on_fail") else "否"))
        if item.get("notes"):
            _print(out, "备注：%s" % item["notes"])
        if item.get("missing"):
            _print(out, "⚠ 方案引用了不存在的用例：%s" % "、".join(item["missing"]))
        if item.get("cases"):
            _print(out)
            _print(out, "方案内各用例的步骤表（执行顺序；同一块可多次实例化）：")
            for index, case in enumerate(item["cases"], start=1):
                _print(
                    out,
                    "  [%d/%d] %s —— %s（%d 步%s）"
                    % (
                        index,
                        len(item["cases"]),
                        case.get("id"),
                        case.get("title"),
                        case.get("step_count", 0),
                        "，plan 显式声明" if case.get("uses_plan") else "，由 blocks 自动展开",
                    ),
                )
                _render_steps_table(case.get("steps_table") or [], out=out, indent="    ")

    _print(out)
    _print(out, "可传入的用例参数（--params）：")
    spec = payload.get("param_spec") or {}
    params = spec.get("params") or {}
    if not params:
        _print(out, "  （无）")
    else:
        width = max(display_width(name) for name in params)
        default_width = max(
            [display_width(_render_default(definition)) for definition in params.values()]
            + [display_width("默认值")]
        )
        type_width = max(
            [display_width(_render_param_types(definition)) for definition in params.values()]
            + [display_width("类型")]
        )
        for name, definition in params.items():
            types = _render_param_types(definition)
            required = "（必需）" if definition.get("required_by") else ""
            sources = "、".join(
                list(dict.fromkeys(
                    list(definition.get("block_ids") or [])
                    + list(definition.get("fact_ids") or [])
                ))
            )
            step_ids = "、".join(
                dict.fromkeys(
                    str(item.get("step_id"))
                    for item in definition.get("step_declared") or []
                )
            )
            if step_ids:
                # 步骤实例化：同一个块多步各带不同取值，来源里点名是哪几步写的
                sources += "（步骤 %s 各自带值）" % step_ids
            _print(out, "  %s  %s %s  %s  来源：%s"
                   % (pad(name, width), pad(types, type_width), required,
                      pad(_render_default(definition), default_width), sources))
        _print(out)
        _print(out, "  注：默认值 = 不传 --params 时实际生效的取值（REQS 0042 D2-2 两层配置）。")

    config_note = _config_defaults_note(params)
    if config_note:
        _print(out, config_note)


def _render_param_types(definition: Mapping[str, Any]) -> str:
    """参数类型一列（事实声明优先，块声明推断补后，去重保序）。"""

    return "、".join(
        list(dict.fromkeys(definition.get("fact_types") or []))
        + [t for t in dict.fromkeys(definition.get("block_types") or [])
           if t not in (definition.get("fact_types") or [])]
    ) or "未声明类型"


def _render_default(definition: Mapping[str, Any]) -> str:
    """参数默认值一列（没有默认值 → 「—」，与「默认值是 null」区分开）。"""

    if not definition.get("has_default"):
        return "—"
    text = json.dumps(definition.get("default"), ensure_ascii=False, default=str)
    return text if display_width(text) <= 32 else truncate(text, 32)


def _config_defaults_note(params: Mapping[str, Mapping[str, Any]]) -> str:
    """地址类参数的配置默认来自哪个文件（REQS-0042 D2-2 诊断用；没有 → ``""``）。"""

    addr_params = [name for name in params if name in ADDR_PARAM_NAMES]
    if not addr_params:
        return ""
    sources = defaults.defaults_sources()
    meter_addr = defaults.default_meter_addr()
    if not meter_addr:
        return "  ⚠ 两层配置里没有可用的 meter_addr（已读：%s / %s）" % (
            defaults.DATA_DEFAULTS_FILE,
            defaults.CONFIG_OVERRIDE_FILE,
        )
    origin = sources.get("meter_addr") or str(defaults.DATA_DEFAULTS_FILE)
    return "  meter_addr 的配置默认：%s（来源：%s）" % (meter_addr, origin)


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #


@dataclass
class Env:
    """命令执行环境（输出流 / 输出形态 / 注入的台架替身）。"""

    out: TextIO
    err: TextIO
    json: bool
    base_url: str = ""
    base_source: str = "none"
    extras: Mapping[str, Any] = field(default_factory=dict)
    fact_probe: Callable[[str, dict[str, Any]], Any] | None = None


def _fact_summary(fact: FactSpec) -> dict[str, Any]:
    return {
        "id": fact.id,
        "title": fact.title,
        "probe": fact.probe,
        "required": decl_loader.required_params(fact),
        "params_schema": fact.params_schema,
    }


def _block_summary(spec: BlockSpec) -> dict[str, Any]:
    action = spec.action if isinstance(spec.action, Mapping) else {}
    nested_params = action.get("params")
    return {
        "id": spec.id,
        "title": spec.title,
        "needs": list(spec.needs),
        "provides": list(spec.provides),
        "params": dict(nested_params) if isinstance(nested_params, Mapping) else {},
        "action_keys": sorted(str(key) for key in action),
        "cleanup": [dict(item) for item in spec.cleanup],
        "tags": list(spec.tags),
        "assert": [dict(item) for item in spec.assert_],
    }


def _case_summary(case: CaseSpec) -> dict[str, Any]:
    return {
        "id": case.id,
        "title": case.title,
        "needs": list(case.needs),
        "blocks": list(case.blocks),
        "plan": [step.model_dump() for step in case.plan],
        "uses_plan": case.uses_plan,
        # 步骤实例化（D3-1）：列表页看「几步」而不是「几块」——同一块可以跑多次
        "step_count": len(case.effective_steps()),
        "block_ids": case.block_ids(),
        "steps": [step.model_dump() for step in case.steps],
        "asserts": [dict(item) for item in case.asserts],
        "tags": list(case.tags),
    }


def _step_on_fail_text(step: Any) -> str:
    """步骤的失败策略文案：显式声明 → 原值；没声明 → 「继承」（run/方案级 stop_on_fail）。"""

    if getattr(step, "declares_on_fail", False):
        return step.on_fail
    return "继承"


def _case_item(case: CaseSpec, bundle: Bundle) -> dict[str, Any]:
    """用例详情：把**步骤序列**展开成「step id + 块 id + 标题 + 前置事实 + 参数」。

    步骤实例化之后「块清单」不再等价于「执行序」——同一个块可能出现多次，
    所以这里按 :meth:`CaseSpec.effective_steps` 展开，并另外给一份人读步骤表
    （``steps_table``：step id / 块 id / 关键参数 / on_fail）。
    """
    item = _case_summary(case)
    expanded: list[dict[str, Any]] = []
    table: list[dict[str, Any]] = []
    for index, step in enumerate(case.effective_steps(), start=1):
        spec = bundle.block_specs.get(step.block)
        on_fail_text = _step_on_fail_text(step)
        if spec is None:
            expanded.append(
                {
                    "id": step.block,
                    "step_id": step.id,
                    "on_fail": on_fail_text,
                    "step_params": dict(step.params),
                    "note": step.note,
                    "title": "",
                    "error": "块未声明（执行时该块会被记为 unknown）",
                    "needs": [],
                    "provides": [],
                    "params": {},
                }
            )
            table.append(
                {
                    "no": index,
                    "step_id": step.id,
                    "block_id": step.block,
                    "block_title": "",
                    "params": dict(step.params),
                    "on_fail": on_fail_text,
                    "note": step.note,
                    "error": "块未声明",
                }
            )
            continue
        summary = _block_summary(spec)
        # 人读这一层要的是「声明了哪些参数」，保留结构化 params 供脚本用，
        # 文案另放 params_text（别把 dict 直接塞进字符串列）
        summary["params_text"] = "、".join(str(key) for key in summary["params"]) or "无"
        summary["step_id"] = step.id
        summary["on_fail"] = on_fail_text
        summary["step_params"] = dict(step.params)
        summary["note"] = step.note
        expanded.append(summary)
        table.append(
            {
                "no": index,
                "step_id": step.id,
                "block_id": step.block,
                "block_title": spec.title,
                "params": dict(step.params),
                "on_fail": on_fail_text,
                "note": step.note,
                "error": "",
            }
        )
    item["blocks"] = expanded
    item["steps_table"] = table
    return item


def _suite_summary(suite: SuiteSpec) -> dict[str, Any]:
    return {
        "id": suite.id,
        "name": suite.name,
        "case_ids": list(suite.case_ids),
        "stop_on_fail": suite.stop_on_fail,
        "notes": suite.notes,
    }


def cmd_list(args: argparse.Namespace, bundle: Bundle, env: Env) -> int:
    want = {
        "blocks": bool(getattr(args, "blocks", False)),
        "facts": bool(getattr(args, "facts", False)),
        "cases": bool(getattr(args, "cases", False)),
        "suites": bool(getattr(args, "suites", False)),
    }
    everything = not any(want.values())
    payload: dict[str, Any] = {
        "ok": not bundle.problems,
        "data_dir": str(bundle.data_dir),
        "counts": {
            "blocks": len(bundle.blocks),
            "facts": len(bundle.facts),
            "cases": len(bundle.cases),
            "suites": len(bundle.suites),
        },
        "problems": bundle.problems,
        "registered": {"blocks": list_blocks(), "facts": list_facts()},
    }
    if everything or want["blocks"]:
        payload["blocks"] = [_block_summary(spec) for spec in bundle.blocks]
    if everything or want["facts"]:
        payload["facts"] = [_fact_summary(spec) for spec in bundle.facts]
    if everything or want["cases"]:
        payload["cases"] = [_case_summary(case) for case in bundle.cases]
    if everything or want["suites"]:
        payload["suites"] = [_suite_summary(suite) for suite in bundle.suites]

    if env.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=env.out)
    else:
        render_list(payload, out=env.out)
    return EXIT_DECLARATION if bundle.problems else EXIT_PASS


def _param_text(spec: CaseParamSpec) -> str:
    if not spec.defs:
        return "无"
    return "、".join(
        "%s(%s)%s"
        % (
            name,
            "/".join(dict.fromkeys(list(d.fact_types) + list(d.block_types))) or "?",
            "*" if d.required_by else "",
        )
        for name, d in spec.defs.items()
    )


def cmd_show(args: argparse.Namespace, bundle: Bundle, env: Env) -> int:
    target = (getattr(args, "target", None) or "").strip()
    case_file = getattr(args, "case_file", None)
    if not target and not case_file:
        raise UsageError(["show 需要给出 id（用例 / 块 / 事实 / 方案）或 --case-file"])
    if target and case_file:
        raise UsageError(["show 的 id 与 --case-file 只能二选一"])

    payload: dict[str, Any] = {"ok": True, "data_dir": str(bundle.data_dir), "problems": bundle.problems}
    if case_file:
        case = load_case_file(case_file, bundle)
        spec = case_param_spec(case, bundle)
        payload.update({"kind": "case", "item": _case_item(case, bundle),
                        "param_spec": spec.to_dict()})
    else:
        case = bundle.cases_by_id.get(target)
        if case is not None:
            spec = case_param_spec(case, bundle)
            payload.update({"kind": "case", "item": _case_item(case, bundle),
                            "param_spec": spec.to_dict()})
        elif target in bundle.block_specs:
            spec = CaseParamSpec(scope=target)
            spec.add_block(bundle.block_specs[target])
            summary = _block_summary(bundle.block_specs[target])
            summary["params_text"] = _param_text(spec)
            payload.update({"kind": "block", "item": summary, "param_spec": spec.to_dict()})
        elif target in bundle.facts_by_id:
            spec = CaseParamSpec(scope=target)
            spec.add_fact(bundle.facts_by_id[target])
            summary = _fact_summary(bundle.facts_by_id[target])
            summary["params_text"] = _param_text(spec)
            payload.update({"kind": "fact", "item": summary, "param_spec": spec.to_dict()})
        elif target in bundle.suites_by_id:
            suite = bundle.suites_by_id[target]
            summary = _suite_summary(suite)
            summary["missing"] = [
                case_id for case_id in suite.case_ids if case_id not in bundle.cases_by_id
            ]
            suite_cases = [
                bundle.cases_by_id[case_id]
                for case_id in suite.case_ids
                if case_id in bundle.cases_by_id
            ]
            # 方案也有参数规格（合并各用例）：show 一个方案就能知道 --params 能传什么
            spec = (
                suite_param_spec(suite, suite_cases, bundle)
                if suite_cases
                else CaseParamSpec(scope=suite.id)
            )
            summary["cases"] = [
                {
                    "id": case.id,
                    "title": case.title,
                    "uses_plan": case.uses_plan,
                    "step_count": len(case.effective_steps()),
                    "steps_table": _case_item(case, bundle)["steps_table"],
                }
                for case in suite_cases
            ]
            payload.update(
                {"kind": "suite", "item": summary, "param_spec": spec.to_dict()}
            )
        else:
            raise UsageError(
                [
                    "找不到 id：%r（已声明 用例 %d / 块 %d / 事实 %d / 方案 %d）"
                    % (
                        target,
                        len(bundle.cases),
                        len(bundle.blocks),
                        len(bundle.facts),
                        len(bundle.suites),
                    )
                ]
            )

    if env.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=env.out)
    else:
        render_show(payload, out=env.out)
    if bundle.problems:
        _print(env.err, "⚠ 声明根存在问题（%d 条），见 check" % len(bundle.problems))
        return EXIT_DECLARATION
    return EXIT_PASS


def _select_targets(args: argparse.Namespace, bundle: Bundle) -> tuple[list[CaseSpec], SuiteSpec | None]:
    """解析 ``--case`` / ``--suite`` / ``--case-file``，返回待跑用例与所属方案。"""
    case_ids = list(getattr(args, "case", None) or [])
    suite_id = getattr(args, "suite", None)
    case_file = getattr(args, "case_file", None)
    given = sum(bool(item) for item in (case_ids, suite_id, case_file))
    if given == 0:
        raise UsageError(["run 需要 --case / --suite / --case-file 之一"])
    if given > 1:
        raise UsageError(["--case / --suite / --case-file 只能三选一"])

    if case_file:
        return [load_case_file(case_file, bundle)], None
    if suite_id:
        suite = bundle.suites_by_id.get(suite_id)
        if suite is None:
            raise UsageError(
                ["找不到方案：%r（已声明：%s）"
                 % (suite_id, "、".join(sorted(bundle.suites_by_id)) or "无")]
            )
        missing = [case_id for case_id in suite.case_ids if case_id not in bundle.cases_by_id]
        if missing:
            raise UsageError(
                ["方案 %r 引用了不存在的用例：%s" % (suite_id, "、".join(missing))]
            )
        return [bundle.cases_by_id[case_id] for case_id in suite.case_ids], suite

    cases: list[CaseSpec] = []
    unknown: list[str] = []
    for case_id in case_ids:
        case = bundle.cases_by_id.get(case_id)
        if case is None:
            unknown.append(case_id)
        else:
            cases.append(case)
    if unknown:
        raise UsageError(
            ["找不到用例：%s（已声明：%s）"
             % ("、".join(unknown), "、".join(sorted(bundle.cases_by_id)) or "无")]
        )
    missing_blocks = {case.id: missing_blocks_of(case, bundle) for case in cases}
    broken = {case_id: ids for case_id, ids in missing_blocks.items() if ids}
    if broken:
        raise UsageError(
            ["用例引用了不存在的块：%s：%s" % (case_id, "、".join(ids))
             for case_id, ids in broken.items()]
        )
    return cases, None


def cmd_run(args: argparse.Namespace, bundle: Bundle, env: Env) -> int:
    cases, suite = _select_targets(args, bundle)
    values = parse_params_json(getattr(args, "params", None))

    if suite is not None:
        spec = suite_param_spec(suite, cases, bundle)
    elif len(cases) == 1:
        spec = case_param_spec(cases[0], bundle)
    else:
        spec = suite_param_spec(
            SuiteSpec(id="__adhoc__", name="临时用例组合", case_ids=[c.id for c in cases]),
            cases,
            bundle,
        )
    resolved = spec.resolve(values)

    stop_on_fail = bool(getattr(args, "stop_on_fail", False)) or bool(
        suite.stop_on_fail if suite else False
    )
    run_id = new_run_id()

    outcomes: list[RunOutcome] = []
    stopped_at = ""
    for case in cases:
        if stopped_at:
            outcomes.append(
                skipped_outcome(
                    case,
                    run_id=run_id,
                    reason="方案 stop_on_fail：前序用例 %s 未通过，本用例未执行" % stopped_at,
                )
            )
            continue
        outcome = run_case(
            bundle,
            case,
            resolved_params=resolved,
            param_spec=spec,
            run_id=run_id,
            extras=env.extras,
            fact_probe=env.fact_probe,
            stop_on_fail=stop_on_fail,
        )
        outcomes.append(outcome)
        if stop_on_fail and outcome.result.verdict == OUTCOME_FAIL:
            stopped_at = case.id

    exit_code = _overall_exit_code(outcomes)

    if env.json:
        if len(outcomes) == 1 and not outcomes[0].skipped_reason:
            payload = outcomes[0].to_dict()
            payload["base_url"] = env.base_url
            payload["base_url_source"] = env.base_source
            payload["base_url_note"] = base_url_notice(env.base_url, env.base_source)
        else:
            payload = {
                "ok": exit_code == EXIT_PASS,
                "run_id": run_id,
                "exit_code": exit_code,
                "suite_id": suite.id if suite else "",
                "stop_on_fail": stop_on_fail,
                "base_url": env.base_url,
                "base_url_source": env.base_source,
                "base_url_note": base_url_notice(env.base_url, env.base_source),
                "params": dict(resolved),
                "cases": [
                    {
                        "case_id": outcome.case.id,
                        "title": outcome.case.title,
                        "verdict": outcome.result.verdict,
                        "exit_code": outcome.exit_code,
                        "failed_block": outcome.result.failed_block,
                        "blocked_block": outcome.result.blocked_block,
                        "unmet_facts": outcome.result.unmet_facts,
                        "counts": outcome.result.counts,
                        "blocks": [record.to_dict() for record in outcome.result.blocks],
                        # 每个 step 的结论（同块多步时只有 step_id 能区分）
                        "steps": outcome.result.step_summaries(),
                        "skipped_reason": outcome.skipped_reason,
                    }
                    for outcome in outcomes
                ],
            }
            if suite is not None:
                payload["verdict"] = _overall_verdict(outcomes)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=env.out)
    else:
        _print(env.out, "═══ 用例运行 ═══")
        _print(env.out, "运行号：%s" % run_id)
        _print(
            env.out,
            "工作台地址：%s（%s）"
            % (
                env.base_url or "未提供（缺台架时块 skipped / 事实 unknown，不是 fail）",
                base_url_source_label(env.base_source),
            ),
        )
        if suite is not None:
            _print(env.out, "方案：%s —— %s" % (suite.id, suite.name))
        _print(env.out, "用例数：%d；stop_on_fail：%s"
               % (len(outcomes), "是" if stop_on_fail else "否"))
        _print(env.out)
        for index, outcome in enumerate(outcomes, start=1):
            if index > 1:
                _print(env.out)
            _print(env.out, "── 第 %d/%d 例 ──" % (index, len(outcomes)))
            render_case_result(outcome, out=env.out, head=False)
        if len(outcomes) > 1 or suite is not None:
            _print(env.out)
            _print(env.out, "═══ 汇总 ═══")
            id_width = max(
                [display_width(outcome.case.id) for outcome in outcomes] + [8]
            )
            for outcome in outcomes:
                _print(
                    env.out,
                    "  %s  %s  退出码 %d%s"
                    % (
                        pad(outcome.case.id, id_width),
                        pad(_verdict_label(outcome.result.verdict), 24),
                        outcome.exit_code,
                        ("  " + outcome.skipped_reason) if outcome.skipped_reason else "",
                    ),
                )
            _print(env.out, "总退出码：%d" % exit_code)
    return exit_code


def _overall_exit_code(outcomes: Sequence[RunOutcome]) -> int:
    """整次运行的退出码：有 fail → 1；全 pass → 0；否则 2（未完成验证）。"""
    if not outcomes:
        return EXIT_INCOMPLETE
    codes = [outcome.exit_code for outcome in outcomes]
    if EXIT_FAIL in codes:
        return EXIT_FAIL
    if all(code == EXIT_PASS for code in codes):
        return EXIT_PASS
    return EXIT_INCOMPLETE


def _overall_verdict(outcomes: Sequence[RunOutcome]) -> str:
    codes = [outcome.exit_code for outcome in outcomes]
    if EXIT_FAIL in codes:
        return OUTCOME_FAIL
    if codes and all(code == EXIT_PASS for code in codes):
        return OUTCOME_PASS
    return "inconclusive"


# -- probe ------------------------------------------------------------------ #


def probe_fact_params(bundle: Bundle) -> dict[str, dict[str, Any]]:
    """自检用事实入参：块声明里能唯一确定的必填参数 + schema 默认值。

    例：``archive.contains`` 需要 ``meter_addr``，而 ``archive.add`` / ``meter.read_645``
    声明里都是同一个地址（声明写 ``null`` 时由 loader 解析成两层配置默认）→ 自检就用它；
    声明不一致则不猜（事实给 unknown）。
    """
    declared: dict[str, list[tuple[str, Any]]] = {}
    for block in bundle.blocks:
        nested = block.action.get("params") if isinstance(block.action, Mapping) else None
        if not isinstance(nested, Mapping):
            continue
        for name, value in nested.items():
            declared.setdefault(str(name), []).append((block.id, value))

    out: dict[str, dict[str, Any]] = {}
    for fact in bundle.facts:
        params: dict[str, Any] = {}
        for name, entry in flatten_params_schema(fact.params_schema).items():
            if name in RESERVED_PARAM_NAMES:
                continue
            if "default" in entry:
                params[name] = entry["default"]
        for name in decl_loader.required_params(fact):
            entries = declared.get(name) or []
            values = {
                json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
                for _block_id, value in entries
            }
            if len(values) == 1 and entries:
                params[name] = entries[0][1]
        if params:
            out[fact.id] = params
    return out


def _state_of(value: Any) -> str:
    """事实取值归一成三态字符串（``FactValue`` / dict / 字面量都认）。"""
    state = getattr(value, "state", None)
    if isinstance(state, str):
        return state
    if isinstance(value, Mapping):
        got = value.get("state")
        return str(got) if isinstance(got, str) else "unknown"
    return "unknown" if value is None else str(value)


def _needs_text(record_facts: Mapping[str, Any]) -> str:
    """前置事实一列：只写 ``事实=状态``（详情留给「说明」列，别把表格撑爆）。"""
    if not record_facts:
        return "-"
    return "、".join("%s=%s" % (fact_id, _state_of(value))
                    for fact_id, value in record_facts.items())


def probe_one_block(
    bundle: Bundle,
    spec: BlockSpec,
    *,
    run_id: str,
    env: Env,
    facts_ctx: FactsContext,
) -> dict[str, Any]:
    """自检一个块：复用执行器（判据仍在块里），前置事实不满足就不执行。

    ``probe`` 口径：**块执行体返回 ``skipped``（台架前置不满足 / 未执行）时，自检记为
    ``unknown``**——自检没有「跳过」这一档，没跑出结论就是 unknown，绝不算 pass。
    """
    ctx = RunContext(
        run_id=run_id,
        facts_ctx=facts_ctx,
        block_specs={spec.id: spec},
        block_params={},
        extras=dict(env.extras or {}),
    )
    case = CaseSpec(
        id="probe.%s" % spec.id,
        title="块自检：%s" % spec.title,
        steps=[CaseStep(no=1, do="自检该块", expect="得到 pass/fail/unknown 结论")],
        blocks=[spec.id],
        needs=[],
    )
    result = execute_case(case, ctx)
    record = result.blocks[0] if result.blocks else None
    raw_state = record.state if record is not None else BLOCK_UNKNOWN
    detail = record.detail if record is not None else "块未产生执行记录"
    state = BLOCK_UNKNOWN if raw_state == BLOCK_SKIPPED else raw_state
    if raw_state == BLOCK_SKIPPED:
        detail = "未完成验证（原状态 skipped）：%s" % detail
    need_facts: dict[str, Any] = {}
    if record is not None:
        need_facts = {
            fact_id: value
            for fact_id, value in record.facts.items()
            if fact_id in spec.needs
        }
    return {
        "block_id": spec.id,
        "title": spec.title,
        "state": state,
        "raw_state": raw_state,
        "needs": list(spec.needs),
        "needs_text": _needs_text(need_facts) if spec.needs else "-",
        "detail": detail,
        "outcome": record.outcome if record is not None else "inconclusive",
    }


def probe_all(
    bundle: Bundle,
    *,
    env: Env,
    run_id: str,
    block_ids: Sequence[str] | None = None,
    fact_ids: Sequence[str] | None = None,
) -> tuple[dict[str, Any], int]:
    """逐块 + 逐事实自检，返回 ``(payload, 退出码)``。"""
    fact_params = probe_fact_params(bundle)
    facts_ctx = FactsContext(
        probe=env.fact_probe,
        params={key: dict(value) for key, value in fact_params.items()},
    )

    facts_payload: list[dict[str, Any]] = []
    for fact in bundle.facts:
        if block_ids is not None:
            break  # 只自检块时不必探测事实本身（块的前置事实仍会在块自检里求值）
        if fact_ids is not None and fact.id not in fact_ids:
            continue
        value = facts_ctx.get(fact.id)
        facts_payload.append(
            {
                "fact_id": fact.id,
                "title": fact.title,
                "state": value.state,
                "detail": value.detail,
                "evidence": list(value.evidence),
                "params": fact_params.get(fact.id, {}),
            }
        )

    blocks_payload: list[dict[str, Any]] = []
    for spec in bundle.blocks:
        if fact_ids is not None:
            break  # 只自检事实时不跑块（跑块会真的下发帧，改变台架状态）
        if block_ids is not None and spec.id not in block_ids:
            continue
        blocks_payload.append(
            probe_one_block(bundle, spec, run_id=run_id, env=env, facts_ctx=facts_ctx)
        )

    block_counts: dict[str, int] = {state: 0 for state, _label in STATE_LABELS}
    for item in blocks_payload:
        block_counts[item["state"]] = block_counts.get(item["state"], 0) + 1
    block_counts["total"] = len(blocks_payload)
    fact_counts: dict[str, int] = {"yes": 0, "no": 0, "unknown": 0}
    for item in facts_payload:
        fact_counts[item["state"]] = fact_counts.get(item["state"], 0) + 1
    fact_counts["total"] = len(facts_payload)

    if blocks_payload:
        exit_code = exit_code_for_states(item["state"] for item in blocks_payload)
    else:
        exit_code = exit_code_for_states(
            "pass" if item["state"] == "yes" else "unknown" for item in facts_payload
        )

    passed = block_counts.get(BLOCK_PASS, 0)
    if blocks_payload and passed:
        summary = "有 %d 个块拿到 pass，%d 个块 unknown / %d 个 fail（逐块结论见上）" % (
            passed,
            block_counts.get(BLOCK_UNKNOWN, 0),
            block_counts.get(BLOCK_FAIL, 0),
        )
    elif blocks_payload:
        summary = (
            "没有任何块被判 pass（%d 块 unknown / %d 块 fail）——"
            "台架未就绪时这是预期结果：未完成验证，绝不假 PASS"
            % (block_counts.get(BLOCK_UNKNOWN, 0), block_counts.get(BLOCK_FAIL, 0))
        )
    else:
        summary = "事实自检：yes %d / no %d / unknown %d" % (
            fact_counts.get("yes", 0),
            fact_counts.get("no", 0),
            fact_counts.get("unknown", 0),
        )

    hint = ""
    if not env.base_url:
        hint = (
            "未提供工作台地址，全部不可达 → 用 --base-url 或环境变量 "
            "WORKBENCH_BASE 指定后重跑才能拿到真实结论"
        )
    elif env.base_source == "default" and blocks_payload and not passed:
        hint = (
            "按本机默认端口 %d 没拿到任何 pass —— 先确认工作台已启动"
            "（或运行时文件 data/runtime/workbench.json 是否存在）" % DEFAULT_PORT
        )

    payload: dict[str, Any] = {
        "ok": exit_code == EXIT_PASS,
        "run_id": run_id,
        "data_dir": str(bundle.data_dir),
        "base_url": env.base_url,
        "base_url_source": env.base_source,
        "base_url_note": base_url_notice(env.base_url, env.base_source),
        "blocks": blocks_payload,
        "facts": facts_payload,
        "block_counts": block_counts,
        "fact_counts": fact_counts,
        "summary": summary,
        "hint": hint,
        "exit_code": exit_code,
        "problems": bundle.problems,
    }
    return payload, exit_code


def cmd_probe(args: argparse.Namespace, bundle: Bundle, env: Env) -> int:
    run_id = new_run_id()
    block_ids = None
    fact_ids = None
    if getattr(args, "block", None):
        block_ids = [args.block]
        if args.block not in bundle.block_specs:
            raise UsageError(
                ["找不到块：%r（已声明：%s）"
                 % (args.block, "、".join(sorted(bundle.block_specs)) or "无")]
            )
    if getattr(args, "fact", None):
        fact_ids = [args.fact]
        if args.fact not in bundle.facts_by_id:
            raise UsageError(
                ["找不到事实：%r（已声明：%s）"
                 % (args.fact, "、".join(sorted(bundle.facts_by_id)) or "无")]
            )

    payload, exit_code = probe_all(
        bundle, env=env, run_id=run_id, block_ids=block_ids, fact_ids=fact_ids
    )

    if env.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=env.out)
    else:
        render_probe(payload, out=env.out)
    if bundle.problems:
        _print(env.err, "⚠ 声明根存在问题（%d 条），见 check" % len(bundle.problems))
        return EXIT_DECLARATION
    return exit_code


def suite_reference_problems(bundle: Bundle) -> list[str]:
    """方案引用的用例必须存在（``check`` 专用）。

    ``blocks/loader.load_all`` 目前只校验「用例 → 块」的引用，**没有**校验
    「方案 → 用例」；方案写错用例 id 时 loader 一片绿、跑到 ``run --suite`` 才炸。
    入口层在这里补齐这条只读校验（不改 loader：那属于另一个批次的写入范围）。
    """
    problems: list[str] = []
    known = "、".join(sorted(bundle.cases_by_id)) or "无"
    for suite in bundle.suites:
        for case_id in suite.case_ids:
            if case_id not in bundle.cases_by_id:
                problems.append(
                    "方案 %r 引用了不存在的用例：%r（已声明用例：%s）"
                    % (suite.id, case_id, known)
                )
    return problems


def case_orchestration_notices(bundle: Bundle) -> list[str]:
    """编排提示（**不是**错误）：``blocks`` 与 ``plan`` 同时给了 → 说明以 ``plan`` 为准。

    REQS-0042 D3-1 的口径是「至少给一个；两者都给以 plan 为准」，所以这里不拦，
    只提醒一声——否则用例作者会以为 ``blocks`` 也在跑（它只进 UI 展示与参数规格）。
    """
    notices: list[str] = []
    for case in bundle.cases:
        if case.blocks and case.plan:
            notices.append(
                "提示：用例 %r 同时给了 blocks 与 plan，执行时以 plan 为准"
                "（blocks=%s 不参与执行；确认无误可删掉 blocks 或 plan 之一）"
                % (case.id, "、".join(case.blocks))
            )
    return notices


def step_table_of(case: CaseSpec, bundle: Bundle) -> list[dict[str, Any]]:
    """人读步骤表（check 摘要用）：step id / 块 id / on_fail。"""

    return [
        {
            "no": index,
            "step_id": step.id,
            "block_id": step.block,
            "on_fail": _step_on_fail_text(step),
        }
        for index, step in enumerate(case.effective_steps(), start=1)
    ]


def cmd_check(args: argparse.Namespace, bundle: Bundle, env: Env) -> int:
    suite_problems = suite_reference_problems(bundle)
    notices = case_orchestration_notices(bundle)
    problems = list(bundle.problems) + suite_problems
    register = bundle.report.register
    payload: dict[str, Any] = {
        "ok": not problems,
        "data_dir": str(bundle.data_dir),
        "counts": {
            "blocks": len(bundle.blocks),
            "facts": len(bundle.facts),
            "cases": len(bundle.cases),
            "suites": len(bundle.suites),
        },
        "problems": problems,
        "notices": notices,
        "loader_problems": list(bundle.problems),
        "suite_reference_problems": suite_problems,
        "register": register.to_dict() if register else None,
        "sources": bundle.sources,
        "cases": [
            {
                "id": case.id,
                "uses_plan": case.uses_plan,
                "step_count": len(case.effective_steps()),
                "steps_table": step_table_of(case, bundle),
            }
            for case in bundle.cases
        ],
    }
    if env.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=env.out)
    else:
        _print(env.out, "声明校验：%s" % ("通过" if not problems else "未通过（%d 条）" % len(problems)))
        _print(env.out, "声明根：%s" % bundle.data_dir)
        _print(
            env.out,
            "计数：块 %d / 事实 %d / 用例 %d / 方案 %d"
            % (len(bundle.blocks), len(bundle.facts), len(bundle.cases), len(bundle.suites)),
        )
        _print(
            env.out,
            "已注册执行体：块 %d / 事实 %d"
            % (len(list_blocks()), len(list_facts())),
        )
        if register and register.failed:
            _print(env.out, "领域模块导入失败：")
            for name, reason in register.failed:
                _print(env.out, "  - %s：%s" % (name, reason))
        _print(
            env.out,
            "步骤实例化：%d 条用例用 plan，%d 条由 blocks 自动展开"
            % (
                sum(1 for case in bundle.cases if case.uses_plan),
                sum(1 for case in bundle.cases if not case.uses_plan),
            ),
        )
        if problems:
            _print(env.out)
            _print(env.out, "问题清单：")
            for problem in problems:
                _print(env.out, "  - %s" % problem)
        else:
            _print(env.out, "未发现问题。")
        if notices:
            _print(env.out)
            _print(env.out, "提示（不影响校验通过）：")
            for notice in notices:
                _print(env.out, "  - %s" % notice)
    return EXIT_DECLARATION if problems else EXIT_PASS


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #


def _add_data_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--data-dir",
        default=None,
        metavar="PATH",
        help="声明根目录（默认 libs/case_runner/data；其下 blocks/ facts/ cases/ case_suites/）",
    )


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="机器可读输出（stdout 只有 JSON，可直接 json.loads）",
    )


def _add_base_url(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help=(
            "工作台地址；缺省按「环境变量 WORKBENCH_BASE > 运行时文件 "
            "data/runtime/workbench.json > 本机默认端口」发现（末档会打一行提示）"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="case_run",
        description=(
            "黑盒用例入口：只给参数，拿测试结果。"
            "四层模型「事实 → 原子块 → 用例 → 方案」，判定全在块与事实里。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  case_run list --json                      列全部声明（块/事实/用例/方案）\n"
            "  case_run list --blocks                    只列块\n"
            "  case_run show nwk_join                    用例名 + 步骤 + 块清单 + needs\n"
            "  case_run show archive.add                 块的 action 键 + 参数默认值（含配置默认）\n"
            "  case_run run --case nwk_join --params '{\"meter_addr\":\"123456789012\"}'\n"
            "  case_run run --case meter_read --json      零参可跑（地址走两层配置默认）\n"
            "  case_run run --suite smoke --base-url http://<host>:<port> --json\n"
            "  case_run probe --all --json               逐块/逐事实自检（无台架 → 全 unknown）\n"
            "  case_run probe --block archive.add        单块自检\n"
            "  case_run check                            声明校验\n\n"
            "参数名：REQS 0042 D2-1 起地址参数只叫 meter_addr（多值 meter_addrs）；\n"
            "传旧名 addr / meters 会直接报参数错误（退出码 3），不给兼容别名。\n\n"
            "工作台地址发现顺序（D2-3）：--base-url > 环境变量 WORKBENCH_BASE >\n"
            "运行时文件 data/runtime/workbench.json（工作台启动时写）> 本机默认端口。\n\n"
            + EXIT_CODE_HELP
        ),
    )
    sub = parser.add_subparsers(
        dest="cmd", required=True, metavar="{list,show,run,probe,check}"
    )

    lp = sub.add_parser(
        "list",
        help="列声明（块 / 事实 / 用例 / 方案）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXIT_CODE_HELP,
    )
    group = lp.add_mutually_exclusive_group()
    group.add_argument("--blocks", action="store_true", help="只列块")
    group.add_argument("--facts", action="store_true", help="只列事实")
    group.add_argument("--cases", action="store_true", help="只列用例")
    group.add_argument("--suites", action="store_true", help="只列方案")
    _add_data_dir(lp)
    _add_json(lp)

    sp = sub.add_parser(
        "show",
        help="看一个声明的详情（用例 → 块 → 事实 → 方案 顺序解析）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXIT_CODE_HELP,
    )
    sp.add_argument("target", nargs="?", default=None, metavar="ID",
                    help="用例 / 块 / 事实 / 方案 id")
    sp.add_argument("--case-file", default=None, metavar="PATH",
                    help="临时用例 JSON 文件（不落库，用于跑未入编的用例）")
    _add_data_dir(sp)
    _add_json(sp)

    rp = sub.add_parser(
        "run",
        help="跑用例或方案（--params 是用例唯一入参）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXIT_CODE_HELP,
    )
    rp.add_argument("--case", action="append", default=[], metavar="CASE_ID",
                    help="用例 id（可重复；与 --suite 二选一）")
    rp.add_argument("--suite", default=None, metavar="SUITE_ID",
                    help="方案 id（与 --case 二选一）")
    rp.add_argument("--case-file", default=None, metavar="PATH",
                    help="临时用例 JSON 文件（与 --case/--suite 二选一）")
    rp.add_argument("--params", default=None, metavar="JSON",
                    help='用例入参 JSON 对象，如 \'{"meter_addr":"123456789012"}\'；'
                         "执行前校验缺必填/多余/类型非法；传旧名 addr/meters 直接报错（D2-1）")
    rp.add_argument("--stop-on-fail", action="store_true",
                    help="首块非 pass 即跳过下游块（方案自身 stop_on_fail 也会生效）")
    _add_base_url(rp)
    _add_data_dir(rp)
    _add_json(rp)

    pp = sub.add_parser(
        "probe",
        help="逐块 / 逐事实自检（无台架时全部 unknown，绝不判 pass）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXIT_CODE_HELP,
    )
    target = pp.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--all",
        action="store_true",
        help="全部块 + 全部事实（注意：块自检会**真的下发帧**——复位/建档/组网都会动台架，"
             "只想看就绪度请用 --fact）",
    )
    target.add_argument("--block", metavar="BLOCK_ID", help="只自检一个块")
    target.add_argument("--fact", metavar="FACT_ID", help="只自检一个事实")
    _add_base_url(pp)
    _add_data_dir(pp)
    _add_json(pp)

    cp = sub.add_parser(
        "check",
        help="声明校验（loader 的 problems，含执行体注册缺失）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EXIT_CODE_HELP,
    )
    _add_data_dir(cp)
    _add_json(cp)
    return parser


# --------------------------------------------------------------------------- #
# 错误输出
# --------------------------------------------------------------------------- #


def _emit_error(
    *,
    error: str,
    headline: str,
    problems: Sequence[str],
    env: Env,
    extra: Mapping[str, Any] | None = None,
) -> None:
    if env.json:
        payload = {
            "ok": False,
            "error": error,
            "headline": headline,
            "problems": list(problems),
        }
        if extra:
            payload.update(extra)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=env.out)
        return
    _print(env.err, "✘ %s（%d 条）：" % (headline, len(problems)))
    for problem in problems:
        _print(env.err, "  - %s" % problem)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

_HANDLERS = {
    "list": cmd_list,
    "show": cmd_show,
    "run": cmd_run,
    "probe": cmd_probe,
    "check": cmd_check,
}


def main(
    argv: Sequence[str] | None = None,
    *,
    data_dir: str | Path | None = None,
    extras: Mapping[str, Any] | None = None,
    fact_probe: Callable[[str, dict[str, Any]], Any] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    register_domain: bool = True,
) -> int:
    """CLI 主入口：返回退出码（0/1/2/3/4），**不** ``sys.exit``。

    ``extras`` / ``fact_probe`` / ``data_dir`` 是测试与上层 API 复用的装配位
    （注入假能力、假事实探测源、换一套声明根），生产路径全部走缺省。
    """
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        args = build_parser().parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:  # argparse：--help → 0；用法错误 → 3
        return EXIT_PASS if not exc.code else EXIT_USAGE

    cli_data_dir = getattr(args, "data_dir", None) or data_dir
    want_json = bool(getattr(args, "json", False))

    with _base_url_env(getattr(args, "base_url", None)) as (base_url, base_source):
        env = Env(
            out=out,
            err=err,
            json=want_json,
            base_url=base_url,
            base_source=base_source,
            extras=dict(extras or {}),
            fact_probe=fact_probe,
        )
        # D2-3：走到末档（本机默认端口）必须**说出来**；--json 时走 stderr，
        # 保证 stdout 仍是纯 JSON（可直接 json.loads）。
        # 只对真正用到地址的命令（run / probe）提示：check / list / show 是离线只读，
        # 每敲一条命令都掺一行地址提示等于噪音。
        notice = base_url_notice(base_url, base_source)
        if notice and args.cmd in ("run", "probe"):
            _print(err if want_json else out, notice)
        try:
            bundle = load_bundle(
                cli_data_dir,
                register_domain=register_domain,
                require_registered=(args.cmd == "check"),
            )
            # 声明坏了就不许跑：run / probe 会在真实台架上产生激励，宁可不跑也不带病跑。
            # list / show / check 仍可执行（只读），收尾照实返回 4。
            if bundle.problems and args.cmd in ("run", "probe"):
                _emit_error(
                    error="declaration_error",
                    headline="声明加载失败（先跑 case_run check 看问题清单）",
                    problems=bundle.problems,
                    env=env,
                )
                return EXIT_DECLARATION
            handler = _HANDLERS[args.cmd]
            return handler(args, bundle, env)
        except decl_loader.DeclarationError as exc:
            _emit_error(
                error="declaration_error",
                headline="声明加载失败",
                problems=exc.problems,
                env=env,
            )
            return EXIT_DECLARATION
        except UsageError as exc:
            _emit_error(
                error="usage_error",
                headline=exc.headline,
                problems=exc.problems,
                env=env,
            )
            return EXIT_USAGE


__all__ = [
    "EXIT_PASS",
    "EXIT_FAIL",
    "EXIT_INCOMPLETE",
    "EXIT_USAGE",
    "EXIT_DECLARATION",
    "EXIT_CODE_HELP",
    "ENV_BASE_URL",
    "DEFAULT_PORT",
    "DEFAULT_BASE_URL",
    "RUNTIME_FILE",
    "BASE_URL_SOURCE_LABELS",
    "RENAMED_PARAMS",
    "ADDR_PARAM_NAMES",
    "UsageError",
    "Bundle",
    "Env",
    "ParamDef",
    "CaseParamSpec",
    "RunOutcome",
    "build_parser",
    "main",
    "load_bundle",
    "load_case_file",
    "parse_params_json",
    "check_renamed_params",
    "case_param_spec",
    "suite_param_spec",
    "probe_fact_params",
    "probe_all",
    "run_case",
    "new_run_id",
    "exit_code_for_verdict",
    "exit_code_for_states",
    "resolve_base_url",
    "runtime_base_url",
    "normalize_base_host",
    "base_url_source_label",
    "base_url_notice",
    "flatten_params_schema",
]
