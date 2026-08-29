# TODO.md — 需求 0009：侦听台通信流追踪

> 执行纪律：RED 先行；全部测试用 fixture 索引库/假帧，不碰真实串口与 runtime 数据（0007 红线）。
> 前置：设计稿（DESIGN.md）评审通过后开工。

## 0. 设计与基线

- [x] DESIGN.md 评审定稿（2026-08-29：三层粒度 / 表地址对账 / S3 双判据 / 回放+live 双模式 / G1-G4，用户确认）
- [x] 真机校准样本入库 `samples/`（3 段 2276 帧 / 1.4MB，源自 9h50m 捕获），校准结论回写 DESIGN §10
- [ ] G1 专项：ACK 帧源 TEI 提取校准（DLL 当前仅出 DST）

## 1. G1 — 4-3 应用层解析件 + frames 物化（未开工）

- [ ] parser_lib 结构表驱动解析件（报文序号 / 请求目标地址序列 / 应答表地址+状态 三件套）
- [ ] 0x0003 并发抄表 + 单表 645 转发两条提取规则，真机样本单测校准
- [ ] frames 表增列 app_port/app_id/msg_seq/meter_addrs/TEI 列 + 幂等迁移 + 后台回填 + 局部索引

## 2. G2 — 回放引擎（未开工）

- [ ] TraceService 回放模式：SQL 自连接（序号+表地址+时间窗）+ 状态机判定
- [ ] flow / round / campaign 三粒度；广播轮动态应答；否认三分类；重传序列记录
- [ ] fixture 索引库全用例覆盖（含已知失败轮的人工核对基线）

## 3. G3 — live + API + AI 控制面（未开工）

- [ ] live 增量匹配（帧入库钩子）+ trace 句柄/注册表
- [ ] 页面侧 /api/listener/traces[/{id}]；AI 控制面 /api/ai/v1/listener/traces（202+wait，scope listener:trace，审计）
- [ ] frames/{id} 响应增 feature_hint；执行核心 state 提升注入（0008 模式）

## 4. G4 — 真机校验与文档（未开工）

- [ ] 真实捕获重放已知失败轮，三段判定与人工核对一致
- [ ] docs/16 增补侦听台追踪节；ai-control-plane skill 增补；DECISIONS 新 ADR；本索引状态更新
