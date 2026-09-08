# REQS-0027 TODO

> v2（变更 2）：新增 G4 统一抄读表格+成功率、G5 并发滑窗调度、G6 上报分类型表格；阶段相应扩为 5 个。

## 阶段 1：应答预期规则库 + 超时档位（后端）
- [x] `libs/sim_concentrator/expect_rules.json`：覆盖常用 AFN（00/02/03/05/06/10/11/F1）的应答形态映射 + 否认码 6D/6E/6F 语义 + 超时档位（默认 5s；单抄 59s；并抄 99s；门限标注蒸馏出处）
- [x] matcher.py：默认 expect 自动生成（显式 expect 可覆盖）+ reasons 中文化 + 否认码识别（deny_info：00H-F2 错误状态字→人话）
- [x] runner.py：per-Fn expect_timeout（expect_timeout=None 时按档位）；匹配窗口内持续等待（对齐 expect_history 轮询语义）；auto_expect 自动生成默认 expect；否认帧特判（00H-F2 不满足 fn=1 字面匹配也立即判失败）
- [x] api.py：/step 响应增加配对结构（step.pairing/deny/bystanders）；新增 GET /expect_rules
- [x] 单测：test_expect_rules.py（规则加载/默认 expect/否认码/档位）

## 阶段 2：并发抄表滑窗调度器（后端，G5 核心）
- [x] batch.py 新增 BatchReadJob：表队列 + 最大并发数（可配）+ 滑窗调度（应答成功/否认/超时均释放槽位，立即补发下一块）
- [x] 每块表独立套 per-Fn 超时档位（mode=single→59s / batch→99s，可手动覆盖）；超时释放槽位计入失败
- [x] api.py 并发任务会话端点：POST /batch_read（创建+启动）、GET /batch_read[/{id}]（进度）、POST …/stop
- [x] 单测：滑窗保持最大并发（peak in_flight ≤ N）、槽位释放三路径（成功/否认/超时）、6D 否认归属（地址域匹配 + 认领去重）、队列收敛

## 阶段 3：抄读汇总与上报计数聚合（后端，G4/G6 数据面）
- [x] aggregate.py collect_readings：单抄/并抄/快照/上报抄读共用明细行（表地址/AFN-Fn/数据项/值/时间/耗时/结果），失败按否认码细分；统计行（下发/应答/成功/失败/成功率，超时算失败）
- [x] aggregate.py report_buckets：AFN=06H 按类型分桶（F1/F2/F3/F4/F5 + 停复电子类 04H 单列）
- [x] api.py：GET /readings（source/result/since 筛选）、GET /report_buckets
- [x] 单测：成功率口径、否认码细分、筛选

## 阶段 4：前端（G1-G6 全量）
- [x] 结果面板（页签：收发配对/抄读汇总/并发抄表/主动上报），脚本版本 v3-0027 防缓存
- [x] 下发后呈现"下发 ↔ 应答"配对卡片；等待期倒计时进度条 + 超时档位 tooltip（蒸馏出处）；应答分格式渲染（确认徽标/否认徽标+6D/6E/6F 人话/数据表格）；旁听帧单独归类展示
- [x] 统一抄读数据表格：统计行（含成功率）+ 来源/结果筛选 + 3s 轮询刷新
- [x] 并发任务面板：最大并发输入 + 表地址输入 + 在途/排队/完成/成功率计数 + 逐表明细实时追加（1s 轮询）+ 停止
- [x] 主动上报分类型表格：F1-F5 各一表 + 数量统计头，停复电单独一表（停电/复电徽标）
- [x] 双副本核对：listener/static 无 simcon 页（0023 起 simcon 页仅在 workbench 单副本），无需同步

## 阶段 5：验收与收尾
- [x] 浏览器实测：配对卡片等待倒计时（default·5s）→ 超时卡（预期应答未到 + 规则 er.03 出处）；否认路径经单测覆盖（6D）；档案见 DONE
- [x] 并发滑窗实测：4 表队列 + 最大并发=2，在途 2/排队 2 实时计数；停止释放；59s 档超时释放后成功率统计 0% 正确
- [x] 上报实测：report_buckets 空库渲染正常（真机停复电样本待有条件时补测）
- [x] simcon 回归全绿（15 文件：142 passed；3 失败为存量——api_cli 2 项端口映射、responder_matcher 14F2 时钟；real_com_pair 4 skipped 需硬件）
- [x] REQS-INDEX/TODO/DONE 状态同步；docs/api-contract.md、docs/features.md 补新端点
