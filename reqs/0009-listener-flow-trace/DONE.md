# DONE.md — 需求 0009：侦听台通信流追踪（完成归档）

> 2026-08-29 完成。G1→G4 全部通过；ADR-9；docs/16 §8.3；ai-control-plane skill 1.3.0。

## 交付清单

### G1 — 4-3 应用层解析件 + frames 物化（f8626fd）

- `libs/parser_lib/adapters/adapter_dualmode/trace_extract.py`：结构表驱动提取件
  （输入 app_id + APP_RAW + 源 TEI → msg_seq / 请求目标地址序列 / 应答表地址+状态）。
  覆盖抄表族 0x0001/0x0002/0x0003（seq/配置字/设备超时/应答位图）、00A1、0020（确认位）、
  0008、0011；645 地址域 + 控制码否认判定；698.45 OAD token 双形态
  （固定 APDU 头 `[10/90][00][L][05/85][choice][OAD]` 单 OAD + WithList 条目 run）。
- **开工复核修订（DESIGN §10.1）**：设计定稿时"0x0003 序号恒 0x0201"实为业务头静态字段
  （版本 6b+头长度 6b LE 打包恰为 0x0201）误读；真序号在 APP_RAW[8:10]（LE），全局递增、
  重发不增；0x0001/0x0003 共享同一递增空间。ACK 源 TEI = MAC 头 [27..28]（BE12）= 被确认帧
  STA 端 TEI（DLL DST 字段不可靠、弃用）。2276 帧样本回归 100%（上行回填 142/142、
  OAD 回显 0 失配、ACK peer 16/16 闭环）。
- frames 表增列 `app_port/app_id/msg_seq/flow_dir/meter_addrs/sta_tei/ori_tei/ack_peer`：
  幂等迁移（`_ensure_trace_columns`）+ 批次回填（`_backfill_trace_pass`，非应用帧写 '' 关账）+
  局部索引（flow_dir=0/1 方向索引、ack_peer 索引、pending 索引）；index_file / append_frames /
  回填三路径共用 `_trace_material`。

### G2 — 回放引擎（f8626fd）

- `apps/listener/trace_service.py`：TraceService 回放模式。
  - 三粒度：flow=(业务ID,序号)（回绕按时间就近）；round=时间簇（空闲切簇缺省 60s，
    `cluster_gap_seconds` 可配）；campaign=窗口内多轮聚合。
  - 状态机：armed→sent(S1)→acked(S2a)→responded(S2b)→confirmed(S3)/denied/timeout；
    S3 双判据 `evidence_kind`（explicit_ack / no_retransmit_inference / retransmitted）；
    重传序列完整保留（时刻+间隔）；S3 反证仅认响应之后的重发。
  - 表地址对账：M_req（下行载荷目标序列）vs M_rsp（上行应答+位图），ok/denied/missing 三分类，
    档案外应答单独标 `extra`；否认是一等结果（全部应答否认 → flow denied）。
  - 代理图 (表地址→应答 STA) 副产物；坏帧口径=不计入判定、单独计数（`bad_frames`）。
- 测试：`test_trace_service.py` 19 例（fixture 索引库+假帧）+ `test_trace_extract.py` 18 例
  （合成结构 14 + 真机样本回归 4，CLR 不可用时自动跳过）。

### G3 — live + 页面 API + AI 控制面（4b095d6）

- live 模式：`register_live`（句柄只匹配注册后入库帧）+ `on_frames_appended` 帧入库钩子
  （serial_service 批量入库回调）+ `live_snapshot`（cursor_range 重算+按 last_frame_id 缓存，
  与回放共用同一引擎与 schema）。
- 页面 API：`POST /api/listener/traces`（live→201 句柄 / 回放→200 报告）、
  `GET /api/listener/traces`（列表）、`GET|DELETE /api/listener/traces/{id}`（快照/停止）；
  `frames/{id}` 响应增 `feature_hint`（样例反推，§5.3）。
- AI 门面：`POST /api/ai/v1/listener/traces`（202+operation，scope `listener:trace`，
  幂等 client_request_id，坏特征 HTTP 层 422，wait 复用，审计落账）、
  `GET /api/ai/v1/listener/traces[/{id}]`（evidence:read 读快照/列表）；
  执行核心经 listener app.state 进程内注入（0008 模式，不走 HTTP 回调）。

### G4 — 真机校验与文档

- 全量捕获（`测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt`，
  537,701 行 / 303MB）重建索引：152s，536,456 帧，坏帧 2,585（0.48%）。
- campaign 全量回放：115.8s，10,325 条流（full_chain 1,837 / no_ack 4,022 /
  no_response 4,374 / no_confirm 92）；带 time_range 窗口回放秒级。
- **人工核对基线（三段判定与人工核对一致）**：
  - 正例 flow 0003:1EC2（confirmed）：S1=frame 11（下行 0003，001→087，seq 0x1EC2）、
    S2a=frame 12（ACK，raw MAC [27..28] 手工解出 peer=087 = via_tei）、
    S2b=frame 18（上行 0003，087→001，seq 回显 0x1EC2）——逐帧核对一致。
  - 负例 flow 0003:1EC3（acked）：SQL 独立查证同序号上行帧数 = 0，与引擎判定一致。
  - 轮级：00:00–00:03 窗口回放（31 流 / 16 目标表 / 7 应答 / 9 缺席）与三段样本分析一致，
    缺席表集中在 via 0D8 广播轮，符合原始捕获结构。
- 文档：docs/16 §8.3（追踪节 + `listener:trace` scope 行 + checklist 第 8 项）；
  ai-control-plane skill 1.3.0（第七步追踪 + 授权示例 + checklist）；
  DECISIONS.md ADR-9；REQS-INDEX 0009 → ✅ 已完成。

## 已知口径与后续（不阻塞验收）

- 连续流量的全量库上 60s 空闲切簇会合成长轮（round 视图建议配合 time_range 窗口或调大
  `cluster_gap_seconds`）；flow/campaign 粒度不受影响。
- 698.45 OAD 的 OI 语义与 WithList 响应记录区内 per-meter 结果解码后置（DESIGN §9 风险项）；
  当前以回显 token 对账，位图=0 视为整帧无应答。
- 一期 698 响应不产出 per-meter denied（645 路径有控制码否认判定）。
- 存量失败（与 0009 无关）：apps/listener/test_ui_layout.py 3 例在 0009 开工前已失败
  （0008 布局收敛提交遗留）。
