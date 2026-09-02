# DONE — REQS-0022 AI 侦听台已有解析复用与分层证据

> 只追加不覆盖。每完成一个 Phase / Step 在末尾追加一条记录。

## 2026-09-02

- **Phase 0 Step 1（完成）**：新建 `reqs/0022-ai-listener-existing-parser-evidence/`
  三件套（`REQS.md` / `TODO.md` / `DONE.md`），原样登记计划的「修订结论」、
  「Global Constraints」、「Frozen API Contract」与四个 Phase 停止门。
- **前置（完成）**：补 `.gitignore` 忽略 `data/listener_*.sqlite*`；API 端点清单与
  本计划文档已推送远程 `4ed9384..1aad124`。
- **Phase 0 Step 2–5（完成）**：
  - 先写引用不存在 fixture 的失败测试，运行确认 FAIL（`fixture 'parser' not found`），
    再补 fixture 与断言，符合 TDD。
  - `parser` fixture 用**真实解析后端** `GwHPLCAnalysis.dll`（`ParserService(DotNetHplcParser)`）；
    DLL 缺失时 `skip`，**不降级为通过**。
  - `raw_0003` fixture 内联一帧真实并发抄表帧（176 字节，源自 `测试文件/并发抄表-样本.txt`），
    使资格测试**不依赖被 `.gitignore` 忽略的样本文件**。
  - 资格确认结果：默认解析已暴露 `FrmType=终端主动并发抄表`、`SNID=00947F69`、
    `SRC/DST`、`APP_ID=0003`、`APP_RAW`（222 hex 字符）。`ChType` 在该帧为 `None`，
    故资格测试不断言它。
  - 另加 `test_temp_index_qualification_report`：用临时 SQLite 建只读索引，只记录
    parser backend、字段是否存在与样本 SHA-256，并断言报告**不含** verdict/pass 之类
    结论字段（资格 ≠ 业务验收）。
  - `docs/api-contract.md` 新增 6.0.1 节登记 listener 分层证据契约。
  - 回归：`apps/listener/test_trace_service.py` + `test_trace_api.py` **29 passed**。
  - 提交 `c5e3af1`（仅 REQS 三件套、`REQS-INDEX.md`、`docs/api-contract.md`、资格测试）。
