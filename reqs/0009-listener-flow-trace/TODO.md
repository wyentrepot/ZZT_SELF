# TODO.md — 需求 0009：侦听台通信流追踪

> 执行纪律：RED 先行；全部测试用 fixture 索引库/假帧，不碰真实串口与 runtime 数据（0007 红线）。
> 前置：设计稿（DESIGN.md）评审通过后开工。

## 0. 设计与基线

- [x] DESIGN.md 评审定稿（2026-08-29：三层粒度 / 表地址对账 / S3 双判据 / 回放+live 双模式 / G1-G4，用户确认）
- [x] 真机校准样本入库 `samples/`（3 段 2276 帧 / 1.4MB，源自 9h50m 捕获），校准结论回写 DESIGN §10
- [x] G1 专项：ACK 帧源 TEI 提取校准（ACK MAC 头 [27..28]=被确认帧 STA 端 TEI，DESIGN §10.1）

## 1. G1 — 4-3 应用层解析件 + frames 物化（✅ 已完成）

- [x] parser_lib 结构表驱动解析件（报文序号 / 请求目标地址序列 / 应答表地址+状态 三件套，§10.1 序号偏移修订）
- [x] 0x0003 并发抄表 + 单表 645 转发两条提取规则，真机样本单测校准（2276 帧回归 100%）
- [x] frames 表增列 app_port/app_id/msg_seq/flow_dir/meter_addrs/sta_tei/ori_tei/ack_peer + 幂等迁移 + 后台回填 + 局部索引

## 2. G2 — 回放引擎（✅ 已完成）

- [x] TraceService 回放模式：物化列 SQL 预筛选 + 进程内状态机判定（ORI_S 方向修正代理中继帧）
- [x] flow / round / campaign 三粒度；广播轮动态应答；否认三分类；重传序列记录
- [x] fixture 索引库全用例覆盖（19 例）+ 真机样本回归（4 例）

## 3. G3 — live + API + AI 控制面（✅ 已完成）

- [x] live 增量匹配（serial_service 帧入库钩子）+ trace 句柄/注册表（快照与回放同引擎）
- [x] 页面侧 /api/listener/traces[/{id}]；AI 控制面 /api/ai/v1/listener/traces（202+wait，scope listener:trace，审计，幂等 422）
- [x] frames/{id} 响应增 feature_hint；执行核心 state 提升注入（0008 模式）

## 4. G4 — 真机校验与文档（✅ 已完成）

- [x] 真实捕获（53.6 万帧）重放，三段判定与人工核对一致（正例逐帧核对/负例 SQL 独立验证，见 DONE.md）
- [x] docs/16 §8.3；ai-control-plane skill 1.3.0；DECISIONS ADR-9；REQS-INDEX 0009 已完成
