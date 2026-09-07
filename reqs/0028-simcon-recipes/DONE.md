# REQS-0028 — DONE

> 状态：✅ 已完成
> 完成：2026-09-02

## 已交付

1. **recipe 库**：`libs/sim_concentrator/recipes/`（JSON）+ `recipes.py`（逻辑）。
   - `clear_archive`：分页查询全部 → 分批删除（每批≤16）→ 复查 total=0，`confirm` 安全闸门。
   - `add_archive`：11H-F1 添加从节点（meters 数组 + protocol），每步下发。
2. **接口**（simcon api）：
   - `GET /api/simcon/recipes` 列目录
   - `GET /api/simcon/recipes/{id}` 详情
   - `POST /api/simcon/recipes/{id}/run` 执行（overrides 参数覆盖）
3. **实机验证**（2026-09-02）：
   - 添加 2 个档案：11H-F1 无地址域（`68 17 00 43 00 00 00 00 00 01 11 01 ...`），pass
   - 清空档案：查询到 2 个 → 删除批1（11H-F2 无地址域）→ 复查 remain=0，`verdict=pass, cleared=True`
   - `confirm` 缺失 → skip（不下发任何帧）
4. **测试**：`test_recipes.py` 16 项全绿；相关套件 41 项全绿。

## 顺带修复（REQS-0027 边界）

实机发现 `add_archive` 首轮添加帧带地址域：`build_address` 把 `params.addr`
（11H-F1 添加对象）误当路由目标。已修正：`params.addr`/`params.meters` 均为
业务数据单元，不触发地址域装配；仅 `params.dst`/`broadcast` 视为显式路由目标。
对应更新 `test_build_address_from_params_addr` + 新增 `test_build_address_from_params_dst`。
