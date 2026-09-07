# -*- coding: utf-8 -*-
"""集中器常用步骤（recipe）库：把可复用的操作流程（清空档案、添加档案、查询档案…）
定义成 JSON recipe，暴露给 AI 与前端一次调用即执行并返回判定结果。

设计（REQS-0028）：
- recipe 以 JSON 存放在 libs/sim_concentrator/recipes/ 下；
- 每个 recipe 有 kind（内置生成器）+ params（参数化声明）；
- recipes.build_task(id, overrides) 把 recipe + 参数渲染成 execute_task 的
  task dict（steps 列表），复用 runner.execute_task 逐步骤构帧/下发/判定；
- HTTP 层（simcon api）暴露 restful 子资源：
    GET  /api/simcon/recipes            列目录
    GET  /api/simcon/recipes/{id}       详情
    POST /api/simcon/recipes/{id}/run   执行（body 带 overrides）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from sim_concentrator.runner import execute_task, run_single_step
from sim_concentrator.scenario_codec import load_profile

RECIPES_DIR = Path(__file__).resolve().parent / "recipes"
MAX_BATCH = 16  # 每批下发上限（REQS-0020：≤16）

# ---------------------------------------------------------------------------
# 基础：recipe 元信息加载
# ---------------------------------------------------------------------------
def list_recipes() -> list[dict]:
    """列出所有 recipe 的元信息（不含 steps/kind 内部细节）。"""
    out = []
    if not RECIPES_DIR.is_dir():
        return out
    for p in sorted(RECIPES_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "id": data.get("id", p.stem),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "params": data.get("params", []),
            "profile": data.get("profile", ""),
            "module": data.get("module", ""),
            "baudrate": data.get("baudrate"),
        })
    return out


def get_recipe(recipe_id: str) -> dict:
    """加载单个 recipe；不存在抛 KeyError。"""
    path = RECIPES_DIR / f"{recipe_id}.json"
    if not path.is_file():
        raise KeyError(f"recipe 不存在: {recipe_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_params(recipe: dict, overrides: dict) -> None:
    """校验必填参数（模板占位 {key} 必须有值）。"""
    for p in recipe.get("params", []):
        key = p.get("key")
        if p.get("required") and key and key not in overrides:
            raise ValueError(f"缺少必填参数: {key}")
        if key in overrides and overrides[key] is None and p.get("required"):
            raise ValueError(f"参数 {key} 不能为空")


# ---------------------------------------------------------------------------
# 参数化：把 recipe + overrides 渲染成 execute_task 的 task dict
# ---------------------------------------------------------------------------
def _bcd_bytes(addr: str) -> bytes:
    """12 位 BCD 地址 → 6 字节（与 adapter_10376._bcd_bytes 一致）。"""
    return bytes.fromhex(addr) if len(addr) == 12 and all(c in "0123456789" for c in addr) \
        else bytes.fromhex(addr.ljust(12, "0"))


def _step(name: str, send: dict, expect: Optional[dict] = None,
          expect_timeout: float = 5.0, recv_only: bool = False) -> dict:
    step = {"name": name, "send": send, "expect_timeout": expect_timeout}
    if expect:
        step["expect"] = expect
    if recv_only:
        step.pop("send", None)
        step["recv_only"] = True
    return step


def _query_step(start: int, count: int) -> dict:
    """10H-F2 查询步骤：下发并期望收到查询应答（解析 total/地址）。"""
    return _step(
        f"查询档案 start={start}",
        {"afn": "10", "fn": "F2", "params": {"start": start, "count": count}},
        expect={"afn": 0x10, "fn": 2},
        expect_timeout=5.0)


def build_task(recipe_id: str, overrides: Optional[dict] = None) -> dict:
    """把 recipe 渲染成 execute_task 的 task dict。

    overrides 为 {参数key: 值}，用于模板参数化与校验。
    返回 task dict（含 steps），可直接交给 execute_task 执行。
    仅适用于静态 steps 的 recipe（如 add_archive）；清空档案是动态流程，
    走 run_recipe → _run_clear_archive。
    """
    recipe = get_recipe(recipe_id)
    overrides = dict(overrides or {})
    kind = recipe.get("kind", recipe_id)

    if kind == "clear_archive":
        raise ValueError("clear_archive 为动态流程，请用 run_recipe 执行")
    if kind == "add_archive":
        _validate_params(recipe, overrides)
        meters = overrides["meters"]
        if not isinstance(meters, list) or not meters:
            raise ValueError("meters 必须为非空数组")
        if len(meters) > MAX_BATCH:
            raise ValueError(f"单批最多 {MAX_BATCH} 个，当前 {len(meters)}")
        protocol = overrides.get("protocol", 2)
        steps = [_step(
            "添加从节点档案",
            {"afn": "11", "fn": "F1",
             "params": {"action": "add", "addr": meters[0], "protocol": protocol}},
            expect_timeout=5.0)]
        for m in meters[1:]:
            steps.append(_step(
                f"添加从节点 {m}",
                {"afn": "11", "fn": "F1",
                 "params": {"action": "add", "addr": m, "protocol": protocol}},
                expect_timeout=5.0))
        return {
            "id": recipe_id, "profile": recipe.get("profile", ""),
            "baudrate": recipe.get("baudrate", 9600),
            "fail_fast": True, "steps": steps,
        }
    raise KeyError(f"recipe 未支持的 kind: {kind!r}")


def run_recipe(recipe_id: str, overrides: Optional[dict] = None,
               io: Any = None, journal: Any = None) -> dict:
    """执行一个 recipe，返回 execute_task 的完整结论 JSON。

    复用 execute_task：不传 io 时按 recipe 的 port/baudrate 自建串口。
    清空类 recipe 需要动态分批删除，走专门实现。
    """
    recipe = get_recipe(recipe_id)
    kind = recipe.get("kind", recipe_id)
    if kind == "clear_archive":
        return _run_clear_archive(recipe, overrides, io=io, journal=journal)
    task = build_task(recipe_id, overrides)
    return execute_task(task, io=io, journal=journal)


# ---------------------------------------------------------------------------
# 清空档案：动态流程（查询全部 → 分批删除 → 复查）
# ---------------------------------------------------------------------------
def _query_all_archives(io: Any, profile_id: str) -> tuple[list[str], int | None]:
    """分页查询全部从节点地址。返回 (地址列表, total)。"""
    all_addrs: list[str] = []
    total: Optional[int] = None
    for start in range(0, 400, MAX_BATCH):
        r = run_single_step(
            io, send={"afn": "10", "fn": "F2", "params": {"start": start, "count": MAX_BATCH}},
            profile=load_profile(profile_id), name=f"查询档案start={start}",
            expect={"afn": 0x10, "fn": 2},
        )
        parsed = (r.get("step") or {}).get("parsed") or {}
        items = parsed.get("items", [])
        addrs = []
        for it in items:
            name = it.get("name", "")
            if name == "从节点总数量":
                try:
                    total = int(it["value"])
                except (TypeError, ValueError):
                    pass
            elif name.startswith("从节点") and name.endswith("地址"):
                v = str(it.get("value", ""))
                if v and len(v) >= 10:
                    addrs.append(v)
        new = [a for a in addrs if a not in all_addrs]
        all_addrs.extend(new)
        if len(new) < MAX_BATCH or (total is not None and len(all_addrs) >= total):
            break
    return all_addrs, total


def _run_clear_archive(recipe: dict, overrides: dict | None, *,
                       io: Any = None, journal: Any = None) -> dict:
    """清空档案：确认开关 → 查询全部 → 分批删除 → 复查。"""
    overrides = dict(overrides or {})
    if not overrides.get("confirm"):
        return {
            "task_id": recipe["id"], "steps": [],
            "summary": {"total": 0, "pass": 0, "fail": 0,
                        "verdict": "skip", "reason": "confirm 未置 true，拒绝执行"},
            "message": "清空档案是破坏性操作，需显式传 confirm=true",
        }

    own_io = io is None
    if own_io:
        from sim_concentrator.serial_io import SerialIO
        io = SerialIO(port=recipe.get("port", "COM3"), baudrate=recipe.get("baudrate", 9600),
                      port_identity={"mapping_id": recipe.get("mapping_id", "")})
        io.open()
    try:
        profile_id = recipe.get("profile", "anhui")
        profile = load_profile(profile_id)
        addrs, total = _query_all_archives(io, profile_id)

        step_results: list[dict] = []
        # 删除阶段：每批 ≤MAX_BATCH
        for i in range(0, len(addrs), MAX_BATCH):
            batch = addrs[i:i + MAX_BATCH]
            r = run_single_step(
                io, send={"afn": "11", "fn": "F2", "params": {"meters": batch}},
                profile=profile, name=f"删除档案批{i // MAX_BATCH + 1}",
                expect={"afn": 0x00, "fn": 1},
            )
            step_results.append(r.get("step", r))
        # 复查：查询 total
        r = run_single_step(
            io, send={"afn": "10", "fn": "F2", "params": {"start": 0, "count": 16}},
            profile=profile, name="复查档案",
            expect={"afn": 0x10, "fn": 2},
        )
        parsed = (r.get("step") or {}).get("parsed") or {}
        remain = None
        for it in parsed.get("items", []):
            if it.get("name") == "从节点总数量":
                try:
                    remain = int(it["value"])
                except (TypeError, ValueError):
                    pass
        step_results.append(r.get("step", r))

        pass_count = sum(1 for s in step_results if s.get("result") == "pass")
        fail_count = sum(1 for s in step_results if s.get("result") == "fail")
        cleared = remain == 0
        return {
            "task_id": recipe["id"],
            "steps": step_results,
            "summary": {
                "total": len(step_results), "pass": pass_count, "fail": fail_count,
                "verdict": "pass" if cleared else "fail",
                "queried_total": total, "deleted": len(addrs), "remain": remain,
                "cleared": cleared,
            },
            "message": f"清空完成：删除 {len(addrs)} 个，剩余 {remain}" if cleared
                      else f"清空未完成：剩余 {remain}",
        }
    finally:
        if own_io:
            io.close()
