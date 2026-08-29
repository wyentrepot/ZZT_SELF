# HANDOFF.md — 需求 0009 开工交接（写给新会话）

> 目标：实现侦听台通信流追踪（发送→响应→接收三段证据链）。
> 设计已评审定稿，**不要重新论证设计**——本文件 + DESIGN.md 是唯一事实源，按 G1→G4 执行。
> 工作分支：master。需求流程：完成一门在 TODO.md 勾选，全部完成写 DONE.md 并更新 REQS-INDEX。

## 1. 必读文件（按顺序）

| 顺序 | 文件 | 作用 |
|---|---|---|
| 1 | `reqs/0009-listener-flow-trace/DESIGN.md` | **权威设计**（10 节：协议依据/真机实证/三层粒度/表地址对账/判据/特征JSON/输出schema/架构/G门/校准记录） |
| 2 | `reqs/0009-listener-flow-trace/REQS.md` / `TODO.md` | 边界、验收标准、G1-G4 清单 |
| 3 | `D:\3-obsidian-data\蒸馏\04_双模4-3_应用层通信协议.md`（1111 行） | 应用层字节结构表（逐报文 ID）——解析件的结构表来源 |
| 4 | `D:\3-obsidian-data\蒸馏\协议层\13_双模4-2_数据链路层_帧格式.md`、`14_双模4-3_应用层_帧格式.md` | 链路层确认/TEI、应用层通用报文头 |
| 5 | `reqs/0009-listener-flow-trace/samples/`（3 段 2276 帧） | 校准样本 + DESIGN §10 校准记录 |

## 2. 已确证的关键事实（省你重新踩坑）

### 协议/数据层面
- **报文序号语义 = 业务任务号，不逐轮递增**：0x0003 并发抄表在 0min/28min/5h 三段样本中序号恒为 `0x0201`。因此 **round = (报文序号 × 时间簇)**，按空闲间隔切簇（缺省 60s 可配），重传判定/S3 反证限定簇内。这是设计定稿前的最后修正，别按"同序号=同轮"实现。
- 上行序号确实回填下行序号（00A1 实测 echo 两个序号）——配对键成立。
- **方向判定**：`SRC == '001'`（CCO TEI）→ 下行；SRC 非空即上行。**ACK 帧 SRC=null 只有 DST**——ACK 源 TEI 提取是 G1 专项校准（试 MAC 头解析或 `parse_full` 的 Detail 字段）。
- 字节布局锚点：0x0003 请求/响应业务头共享前缀 `11 03 00 00 01 02`，自 645 控制字节分叉（下行 `33 06`+表地址 / 上行应答码+表地址）；序号候选位 = APP_RAW bytes[4:6]；**精确偏移必须按 04 蒸馏文档逐 ID 定位并用样本单测固化**，不许拍脑袋。
- 心跳/发现列表/信标/关联等链路层帧无 APP 字段——不参与应用层配对，只作 S2a/背景观测。
- 坏帧率 ~0.7%，缺省口径"坏帧不计入判定、单独计数"。

### 代码层面
- **DLL 解析**：`libs/shared/dotnet_parser.py` `DotNetHplcParser.parse_simple(frame_bytes)` → JSON 字符串（keys: FrmType/SNID/SRC/DST/ORI_S/FINL_D/APP_PORT/APP_ID/APP_RAW/Info/Info2/Detail…）。DLL 在 `libs/shared/dll/bin/Debug/GwHPLCAnalysis.dll`（GW_SMAnalysis V1.0.23）。**警告：必须先 `shared.dotnet_runtime.require_dotnet_runtime()` 再 `import clr`，顺序错了原生 SIGABRT 不可捕获**（见 dotnet_parser.py 注释）。
- **frames 表物化先例**：`apps/listener/log_service.py`——`nid/frm_type/assess_detail` 物化列 + 幂等回填 + 局部索引（含覆盖索引 76ms→0.4ms 的实测注释）。0009 的 `app_port/app_id/msg_seq/meter_addrs/TEI` 列照此模式做；SQL 自连接聚合的性能先例见 `apps/listener/network_assessment.py`（53.6 万帧 6.3s）。
- **frames 表结构**：`id, sequence, log_time, byte_length, raw_hex, summary_json, parse_error, nid, frm_type, assess_detail`；版本化索引 registry 在 `apps/listener/index_registry.py`。
- **异步 operation 模式**（G3 用）：`apps/workbench/ai_api.py` + `ai_operations.py` + `ai_store.py`——202+operation_id+wait(≤30s/次)+幂等(client_request_id)+audit，simcon_verify 是最新的同构参照。
- **层间进程内注入模式**（G3 用）：0008 的链路 simcon→module_log(app.state 提升)→workbench(SimconAIService 桥)→AIControlService(simcon_service=...)，全程不走 HTTP 回调。0009 照抄：listener 访问器 → workbench 接线 → `AIControlService(trace_service=...)`。
- **listener 挂载**：workbench 经 `_mount_proxied` 挂 `/api/listener/*`，`_sub.state.log_service` 等已在读取。

### 数据源
- 校准样本：`reqs/0009-listener-flow-trace/samples/sample-{A,B,C}_*.txt`（行格式 `[帧号][HH:MM:SS.mmm]<空格分隔hex>`，7E 定界）。
- 全量原始捕获：`测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt`（537,701 帧 / 9h50m / 303MB，**gitignored 不入库**）。需要全量索引时走 listener 现有导入管线重建。
- 既有 300 帧小索引：`apps/listener/runtime/indexes/idx-20260829-*.sqlite3`（仅作字段形状参考）。

## 3. 实用探针（已验证可跑）

```python
# DLL 解析单帧（win32）
import sys, json
sys.path.insert(0, 'libs'); sys.path.insert(0, 'apps')
from pathlib import Path
from shared.dotnet_parser import DotNetHplcParser
p = DotNetHplcParser(Path('libs/shared/dll/bin/Debug/GwHPLCAnalysis.dll'))
line = open('reqs/0009-listener-flow-trace/samples/sample-A_并发抄表轮_406727-407600.txt', encoding='utf-8').readline()
frame = bytes.fromhex(line.split(']', 2)[2].strip().replace(' ', ''))
s = json.loads(p.parse_simple(frame))   # FrmType/SRC/DST/APP_ID/APP_RAW...
```

## 4. 任务分解（详见 TODO.md，此处补实现落点）

- **G1**：`parser_lib` 结构表驱动 4-3 解析件（输入 app_id+APP_RAW → msg_seq/请求目标地址序列/应答表地址+状态 三件套；一期 0x0003+单表645，逐 ID 单测固化）；frames 物化回填；ACK 源 TEI 专项校准。
- **G2**：listener 侧 `TraceService` 回放模式（SQL 自连接：序号+表地址+时间窗；flow/round/campaign 三粒度；广播轮动态应答；否认三分类；重传序列）；fixture 索引库全覆盖测试（伪造已知失败轮做人工核对基线）。
- **G3**：live 增量匹配（帧入库钩子）；页面 API `POST/GET /api/listener/traces[/{id}]`；AI 门面 `POST /api/ai/v1/listener/traces`（202+wait，新 scope `listener:trace`，resource=索引 mapping，audit）；`GET /frames/{id}` 增 `feature_hint`；state 提升注入。
- **G4**：全量捕获重建索引 → 回放已知失败轮人工核对；docs/16 增补、ai-control-plane skill 增补、DECISIONS 新 ADR、REQS-INDEX/DONE 收尾。

## 5. 红线与约定

- 所有测试 fixture 索引库/假帧，**绝不打开真实 COM、绝不写入 runtime 数据**（0007 红线）；`HPLC_TEST_DATA_ROOT` 指向 `测试文件/`。
- 提交风格：`feat(listener): …——…` 中文正文 bullet 列要点（参照 git log 近十条）。
- 设计若需变更：先改 DESIGN.md 并在文中标注修订原因，再动代码；发现与 §10 校准记录矛盾的数据，停下核实再继续。
