# TODO.md — 需求 0042 交付/阶段状态

> 基线：`REQS.md` **v1**（2026-11-23，黑盒零参与执行 + 地址角色化 + API + UI）。
> 一个阶段验收通过才勾 `- [x]`；全部完成追加到 `DONE.md`。
> 细粒度实现步骤见 `PLAN.md`（开工时由 `writing-plans` 生成），本文件只记交付状态。

> **前置**：REQS-0041 已交付 15 块 / 8 事实 / 5 用例 / 3 方案，真实台架 `meter_read` 3/3 PASS，
> `pytest libs/case_runner -q` = 444 passed / 2 skipped。本需求在其上收口，不改四层边界。

## P0：开工门禁
- [ ] 复核基线：`grep -n '变更 [0-9]' reqs/0042-*/REQS.md | tail -1` = 变更 1
- [ ] 复核 0041 基线绿：`pytest libs/case_runner -q` → 444 passed / 2 skipped
- [ ] 复核分层铁律：`test_layering.py` 10 passed；`grep '^from apps' libs/` = 0
- [ ] 复核台架：`GET /api/serial-bridge/status` 5 口 usable（simcon=COM4 / simeter=COM20 / sta-main=COM18 / cco-main=COM12）
- [ ] `PLAN.md` 生成（writing-plans）

## P1：地址角色化（AD-*，消除 G-1，本需求技术核心）
- [ ] `common.archive_addr_of()` / `common.meter_addr_wire()` 唯一实现 + 双向单测
- [ ] 5 块参数 `addr` → `meter_addr`（`addr` 保留别名 + warning）
- [ ] 2 事实参数同步
- [ ] 5 用例 + 3 方案声明同步（只需 `meter_addr` 一个地址）
- [ ] 门禁：0041 既有 444 测试全绿；真实台架 `--params '{"meter_addr":"123456789012"}'` 跑 `meter_read` 3/3 PASS

## P2：幂等 / 等待 / 自愈（ID-* / WT-* / SH-*）
- [ ] `archive.add` 已存在 → pass +「已存在」；`archive.del` 不存在 → pass
- [ ] `nwk.route_status` `wait_s`（默认 60）轮询到抄表态
- [ ] `nwk.nodes_online` `wait_s`（默认 90）
- [ ] 端口占用自愈一次（simcon/simeter/module）+ `self_healed[]` 留痕
- [ ] 门禁：同一方案连跑两次结论一致；制造端口占用可自愈

## P3：失败三分类 + suite preflight（VF-*，消除 G-5）
- [ ] verdict 增 `blocked`；`skipped→blocked`、`unknown→inconclusive`；优先级 fail > blocked > inconclusive > pass
- [ ] suite 级 preflight：台架不满足 → 整个 run `blocked` + 缺项清单（1s 内返回，不逐用例跑）
- [ ] 门禁：构造三种场景分别得 fail / blocked / inconclusive

## P4：API 层（API-*）

> **已有骨架（2026-11-23 遗留，未提交）**：
> `apps/workbench/cases_api.py`（1184 行，9 端点 + job 线程）与
> `apps/workbench/test_cases_api.py`（857 行，30 tests）已存在，**但按 REQS-0041 旧语义编写**：
> - 缺 `blocked` 三分类（§3.5）
> - 缺地址角色化（§3.1，仍是 `addr` 而非 `meter_addr`）
> - 缺 `/api/cases/bench` 台架快照端点
> - `app.py` 的挂载**已被摘除**（只留注释），需升级后重新挂载
>
> → 实现时可复用其端点骨架与 job 模式，但必须按本文 §3.1/§3.5/§3.6 改造。
> 改造前先跑 `pytest apps/workbench/test_cases_api.py -q`（应 30 passed）确认基座可用。

- [ ] `create_cases_router` + `app.py` 挂载（紧跟 autotest 之后，2-3 行）
- [ ] 10 端点（含 `/api/cases/bench`）
- [ ] 异步 job（复位 30s / 抄表数十秒不得同步阻塞）
- [ ] 门禁：端点单测全绿 + `/api/autotest/*` 不回归 + 无 8790 写死

## P5：UI 层（UI-1..7）
- [ ] 用例列表（用例名 + 块数 + 步骤摘要，可展开 do/expect）
- [ ] 台架就绪横幅 + 不满足时运行按钮置灰并给原因
- [ ] 勾选 → 存方案 → 刷新后勾选恢复
- [ ] 两级运行（立即运行 / 按方案运行）+ job 进度轮询
- [ ] **断点可视化**（failed_block / blocked_block / unmet_facts）
- [ ] 三态着色（pass 绿 / fail 红 / blocked 灰 / inconclusive 黄）
- [ ] 主题令牌合规 0 issue
- [ ] 浏览器全链路实测

## P6：零参与收口（本需求的真正验收）
- [ ] **DoD-1 实测**：无提问、无补参、无解冲突，敲一条命令跑完整个方案拿到结论
- [ ] DoD-2 单参数（只需 `meter_addr`）
- [ ] DoD-3 连跑两次一致
- [ ] DoD-4 三态可分 + UI 着色
- [ ] DoD-5 自愈可用
- [ ] DoD-6 等待有效
- [ ] DoD-10 每次运行落盘完整证据（verdict/断点/self_healed/台架快照）
- [ ] DONE.md + REQS-INDEX 状态更新
