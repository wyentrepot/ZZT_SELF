# REQS-0028 — 集中器常用步骤（recipe）库与接口

> 状态：✅ 已完成
> 创建：2026-09-02
> 关联：REQS-0027（1376.2 无地址域）、REQS-0020（实机测试）、REQS-0013（1376.2 帧页面）

## 1. 背景与需求

用户希望把"清空档案""添加档案"等**常用操作流程**沉淀下来，直接暴露接口给
**AI 与前端**：一次调用即自动执行对应 1376.2 下发、自行判定、返回结果。后续
会持续新增常用步骤。测试用例也可直接跑这些常用步骤（如先清档案）。

## 2. 目标

1. 把常用操作定义为可复用的 **recipe**（JSON），含参数化声明。
2. 暴露 **restful 子资源接口**：列目录 / 详情 / 按 id 执行（带参数覆盖）。
3. 执行一次调用即完成整套流程（查询→删除→复查等），返回逐步判定 + 汇总。
4. 未来新增常用步骤只需加一个 JSON + 生成器，接口不变。

## 3. 设计

### 存放位置
`libs/sim_concentrator/recipes/`（JSON 定义）+ `libs/sim_concentrator/recipes.py`（逻辑）。
与 simcon 强内聚（recipe 操作对象就是集中器），复用现有 `runner.execute_task` /
`run_single_step` / `scenario_codec` 作为执行与构帧引擎。

### recipe JSON 结构
```json
{
  "id": "clear_archive",
  "name": "清空全部从节点档案",
  "description": "...",
  "profile": "anhui", "module": "cco", "baudrate": 9600,
  "params": [
    {"key": "confirm", "type": "bool", "default": false, "description": "..."}
  ]
}
```

### 接口（simcon api 挂载）
- `GET  /api/simcon/recipes`             列目录（id/name/description/params）
- `GET  /api/simcon/recipes/{id}`        详情
- `POST /api/simcon/recipes/{id}/run`    执行，body `{"overrides": {参数}}`

### 流程类 recipe（clear_archive）
动态流程：分页查询全部 → 分批删除（每批≤16）→ 复查 total=0。带 `confirm` 安全闸门。

### 静态类 recipe（add_archive）
`build_task` 把 overrides（meters/protocol）渲染成 steps 列表，走 `execute_task`。

## 4. 验收

- [x] `GET /recipes` 列出 clear_archive / add_archive
- [x] 实机：`POST /recipes/add_archive/run` 添加 2 个档案（11H-F1 无地址域）
- [x] 实机：`POST /recipes/clear_archive/run`（confirm=true）删除 2 个 → 复查 total=0
- [x] `confirm` 缺失时返回 skip，不执行删除（安全护栏）
- [x] 测试 41 项通过（recipe 16 + scenario_codec 17 + runner）

## 5. 变更记录

- 2026-09-02：新建 recipes 库 + API；顺带修正 REQS-0027 边界：`params.addr`
  也是业务数据（11H-F1 添加对象），不再触发地址域装配（仅 `params.dst`/`broadcast`）。
