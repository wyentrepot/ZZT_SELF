"""证据限量保留（REQS-0042 v11 D11-4）。

用户口径（2026-11-23 直批）：
> 「其次，证据目录也不能无限存储，**证据只保留近 30 轮的记录**。」

``case_runs/`` 每跑一轮就多一个目录（CLI 是 ``data/case_runs/``，工作台是
``<storage_dir>/case_runs/``），长跑一天能攒出几百轮。本模块只做一件事：
**按运行时间倒序保留最近 ``keep`` 轮，其余整轮目录删掉**，并把删了谁如实回报。

契约（:func:`prune_runs`）
--------------------------
- 返回 ``{"pruned": [run_id...], "kept": N, "enabled": bool}``：
  ``pruned`` 按**从旧到新**列出（读起来就是「这几轮被清掉了」），``kept`` 是裁剪后
  留下的目录数；
- ``keep <= 0`` → **不裁剪**（``--keep-runs 0`` / 负数 = 不限），返回 ``enabled=False``；
- ``current_run_id``（这一轮的运行号）**永不删除**，哪怕它按时间排在第 ``keep`` 名之外
  —— 正在写的那一轮被自己删掉是荒唐的，宁可多留一轮；
- **只删目录，不删文件**（``case_runs/`` 里混进来的 ``README.txt`` 之类一律不动）；
- 删除失败**只 warning 不抛**：裁剪是附加动作，不能把一次已经跑完的运行搞挂。

排序口径（为什么不是 mtime）
---------------------------
运行号是 ``new_run_id()`` 生成的 ``YYYYmmdd-HHMMSS-<6 位随机>``，**定宽、可字典序排序**，
它本身就是运行时间。所以：

1. 名字能解析成运行号 → **以名字为准**（拷贝/恢复/解包会刷掉 mtime，名字不会）；
2. 名字解析不出（历史遗留目录、手工目录）→ 用 ``mtime`` 兜底，折算成同一条时间轴
   （``YYYYmmdd-HHMMSS``）参与排序，避免「坏名字的目录永远被当成最新，永远删不掉」；
3. 两者都拿不到（``stat`` 失败）→ 排最前（当最旧），但**只有当它确实排在名额之外**
   才会被删；且删除失败也只是 warning。

已知风险
--------
- **跨进程并发**：同一 ``case_runs/`` 被两个进程同时裁剪时，A 可能删掉 B 正在写的
  目录（B 的目录若排在最近 ``keep`` 之内则不会；排在之外说明它已经过期）。CLI 与工作台
  用的是**不同**的根目录（``data/case_runs`` vs ``storage_dir/case_runs``），工作台内部
  又有「台架独占 → 同时只有一个 job」的忙检查，所以当前架构下不会撞上；同机起两个
  工作台实例指向同一个 storage 才会。
- **删除是不可逆的**：本模块不备份、不归档。真要留档请在裁剪之前自己拷贝。
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "DEFAULT_KEEP_RUNS",
    "RUN_ID_RE",
    "looks_like_run_id",
    "list_run_dirs",
    "prune_runs",
]

#: 默认保留轮数（D11-4 写死的 30；``--keep-runs`` / ``CasesService(keep_runs=)`` 可覆盖）
DEFAULT_KEEP_RUNS: int = 30

#: 运行号形状：``YYYYmmdd-HHMMSS-<随机后缀>``（``cli.new_run_id`` / ``cases_api`` 同源）。
#: 只认这一种形态：``case_runs/`` 下由本系统生成的目录都是它，其它一律当「不是运行目录」。
RUN_ID_RE = re.compile(r"^(\d{8}-\d{6})-[0-9A-Za-z]+$")

#: mtime 兜底用的时间戳格式（与运行号前缀同形，才能放在同一条时间轴上比较）
_STAMP_FORMAT = "%Y%m%d-%H%M%S"

#: 排序键尾部兜底：名字（保证同键时结果稳定，不随目录遍历顺序摆动）
_SORT_TAIL = "~~~"


def looks_like_run_id(name: Any) -> bool:
    """``name`` 是否形如运行号（只做形状判断，不查文件系统）。"""

    return bool(RUN_ID_RE.match(str(name or "")))


def list_run_dirs(runs_dir: Path) -> list[Path]:
    """``runs_dir`` 下的**目录**列表（文件不算；目录不存在 → 空列表，不抛）。"""

    root = Path(runs_dir)
    try:
        if not root.is_dir():
            return []
        entries = list(root.iterdir())
    except OSError:
        return []
    return [entry for entry in entries if entry.is_dir()]


def _sort_key(entry: Path) -> tuple[str, str]:
    """排序键 ``(时间戳, 名字)``，时间戳优先取运行号前缀、取不到用 mtime 兜底。

    名字可解析 → ``("20261123-172601", "20261123-172601-1778df")``；
    名字不可解析 → ``(mtime 折算的 "YYYYmmdd-HHMMSS", 名字)``；
    连 ``stat`` 都失败 → 排最前（当最旧），但保留名字保证确定性。
    """

    match = RUN_ID_RE.match(entry.name)
    if match:
        return match.group(1), entry.name
    try:
        stamp = datetime.fromtimestamp(entry.stat().st_mtime).strftime(_STAMP_FORMAT)
    except OSError:
        stamp = ""
    return stamp, entry.name + _SORT_TAIL


def _default_warn(message: str) -> None:
    print(message, file=sys.stderr)


def prune_runs(
    runs_dir: Path,
    keep: int = DEFAULT_KEEP_RUNS,
    *,
    current_run_id: str = "",
    warn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """只保留最近 ``keep`` 轮运行目录，返回 ``{"pruned", "kept", "enabled"}``。

    参数
    ----
    runs_dir
        证据根（``…/case_runs``），其下每个 ``<run_id>/`` 是一轮运行。不存在 = 空。
    keep
        保留轮数；``<= 0`` 表示**不限**（不裁剪，``enabled=False``）。
    current_run_id
        **这一轮**的运行号：永远不在删除名单里（哪怕它按时间排在第 ``keep`` 名之外）。
    warn
        warning 出口（缺省打到 ``stderr``）。删除失败、目录不可读都只 warning，不抛。

    返回
    ----
    ``{"pruned": [从旧到新被删的目录名], "kept": 裁剪后剩下的目录数, "enabled": 是否真的
    按 keep 裁剪（``keep<=0`` 时为 False）}``
    """

    guarded = str(current_run_id or "").strip()
    warned = warn or _default_warn
    directories = list_run_dirs(runs_dir)

    # ``keep <= 0``：显式「不限」，一个都不许删（``--keep-runs 0`` 的语义）
    if int(keep or 0) <= 0:
        return {"pruned": [], "kept": len(directories), "enabled": False}

    if len(directories) <= keep:
        return {"pruned": [], "kept": len(directories), "enabled": True}

    ordered = sorted(directories, key=_sort_key)      # 旧 → 新
    # 名额 = 最近的 keep 个；当前轮额外进白名单（keep 之外也留）
    survivors = {entry.name for entry in ordered[-keep:]}
    if guarded:
        survivors.add(guarded)

    pruned: list[str] = []
    for entry in ordered:
        if entry.name in survivors:
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            # 占用 / 权限 / 只读：留痕后继续，绝不因为删不掉而改变本次运行的结论
            warned("⚠ 旧证据清理失败（已跳过，不影响本轮结论）：%s: %s" % (entry, exc))
            continue
        pruned.append(entry.name)

    return {
        "pruned": pruned,
        "kept": len(directories) - len(pruned),
        "enabled": True,
    }
