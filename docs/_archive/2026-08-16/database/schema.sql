-- 侦听台改造项目 数据库建表 SQL（SQLite 3.x）
-- 用途：侦听台索引库 + platform 闭环验证 Run/Report 库
-- 说明：
--   1) 前两张表（frames / minute_reports）属于侦听台离线索引库，已在现网运行，禁止随意破坏。
--   2) 其余表属于 platform 统一工作台闭环验证库（规划 / 目标），按本 SQL 初始化。
--   3) SQLite 开启 WAL，所有时间文本统一 ISO8601 格式。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- 一、侦听台索引库（现状库）
-- 库文件：runtime/log_index.sqlite3（frozen 下在 exe 同目录 runtime/）
-- ============================================================

-- 1.1 帧索引表：每行一条被切出的 HPLC 帧
CREATE TABLE IF NOT EXISTS frames (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,   -- 帧主键，也是分页游标
    seq            INTEGER NOT NULL,                    -- 帧序号（日志内顺序）
    log_time       TEXT    NOT NULL,                    -- 日志时间字符串 HH:MM:SS.mmm
    time_seconds   INTEGER NOT NULL DEFAULT 0,          -- 归一化毫秒时间（跨天已按 12 小时回退规则累加日偏移）
    nid            TEXT,                                -- 网络标识 NID（筛选用）
    summary_json   TEXT,                                -- DLL 摘要 JSON（含 APP_PORT/APP_ID/APP_RAW/FrmType/BaseFrmType）
    raw_hex        TEXT    NOT NULL,                    -- 原始帧十六进制（证据回溯）
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_frames_sequence  ON frames(seq);
CREATE INDEX IF NOT EXISTS idx_frames_log_time  ON frames(time_seconds);
CREATE INDEX IF NOT EXISTS idx_frames_nid       ON frames(nid);

-- 1.2 分钟采集上报表：建索引时从 E4 帧富化写入，用于周期统计
CREATE TABLE IF NOT EXISTS minute_reports (
    frame_id          INTEGER PRIMARY KEY,             -- 与 frames.id 一一对应
    log_time          TEXT    NOT NULL,
    time_seconds      INTEGER NOT NULL,
    cco_tei           TEXT    NOT NULL,                -- CCO 归属：FINL_D，缺省回退 DST
    station_key       TEXT    NOT NULL,                -- 站点身份：源 MAC 优先，回退 ORI_S，再 SRC
    source_mac        TEXT,
    source_tei        TEXT,
    task_no           INTEGER,
    protocol_type     INTEGER,
    meter_type        INTEGER,
    response_result   INTEGER,
    freeze_time       TEXT,                             -- 冻结时刻，小端 BCD 解码后字符串
    report_count      INTEGER,
    data_length       INTEGER,
    application_error TEXT,                             -- 富化失败原因（非空记解析异常）
    created_at        TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (frame_id) REFERENCES frames(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_minute_reports_cco      ON minute_reports(cco_tei);
CREATE INDEX IF NOT EXISTS idx_minute_reports_time     ON minute_reports(time_seconds);
CREATE INDEX IF NOT EXISTS idx_minute_reports_station  ON minute_reports(station_key);

-- ============================================================
-- 二、platform 统一工作台闭环验证库（目标库）
-- 库文件：data/runs.sqlite（frozen 下在 exe 同目录 runtime/runs.sqlite）
-- ============================================================

-- 2.1 场景模板注册表
CREATE TABLE IF NOT EXISTS scenarios (
    id             TEXT PRIMARY KEY,                    -- 场景模板 id，如 minute_collect
    name           TEXT NOT NULL,
    version        TEXT NOT NULL DEFAULT 'v1',
    module         TEXT,                                -- cco / sta
    content_json   TEXT NOT NULL,                       -- 场景模板全文（expected_flow / stimulus / monitor / timing）
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 2.2 固件信息（同一 Run 内的固件指纹）
CREATE TABLE IF NOT EXISTS firmware_infos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    version             TEXT NOT NULL,
    commit              TEXT,
    flash_file_sha256   TEXT,
    file_path           TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(version, commit, flash_file_sha256)
);

-- 2.3 验证批次（Run）
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,                  -- run-YYYYMMDD-HHMMSS-xxxx
    scenario_id      TEXT NOT NULL REFERENCES scenarios(id),
    firmware_id      INTEGER REFERENCES firmware_infos(id),
    task_id          TEXT,                              -- 关联的 sim_concentrator 验证任务 id（可空）
    status           TEXT NOT NULL DEFAULT 'pending',   -- 状态机见系统架构与核心模型设计.md
    verdict          TEXT,                              -- pass / fail / inconclusive / null（未出结论）
    created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    started_at       TEXT,
    finished_at      TEXT,
    updated_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    report_path      TEXT                               -- data/reports/{run_id}.json
);
CREATE INDEX IF NOT EXISTS idx_runs_scenario ON runs(scenario_id);
CREATE INDEX IF NOT EXISTS idx_runs_status   ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created  ON runs(created_at);

-- 2.4 运行步骤（RunStep）
CREATE TABLE IF NOT EXISTS run_steps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    kind           TEXT NOT NULL,                       -- flash | monitor | stimulus | compare | feedback | checkpoint
    status         TEXT NOT NULL DEFAULT 'pending',     -- pending | running | passed | failed | skipped | aborted
    detail_json    TEXT,                                -- 步骤参数与结果摘要
    evidence_json  TEXT,                                -- 证据引用数组
    started_at     TEXT,
    finished_at    TEXT,
    UNIQUE(run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id);

-- 2.5 统一验证报告（不可变报告归档）
CREATE TABLE IF NOT EXISTS reports (
    run_id         TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    report_json    TEXT NOT NULL,                       -- 完整 Report（FR-5.2 schema）
    verdict        TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 2.6 证据/产物登记表（原始证据链）
CREATE TABLE IF NOT EXISTS run_artifacts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    step_id        INTEGER,                             -- 可空：Run 级公共证据
    artifact_type  TEXT NOT NULL,                       -- log_file | frame_hex | report_json | flash_log | screenshot
    path           TEXT NOT NULL,
    sha256         TEXT,
    mime           TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run ON run_artifacts(run_id);

-- 2.7 归因规则表（可配置反馈规则）
CREATE TABLE IF NOT EXISTS feedback_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id        TEXT NOT NULL UNIQUE,
    when_json      TEXT NOT NULL,                       -- 触发条件（compare.missing / negated 等）
    then_text      TEXT NOT NULL,                       -- 归因建议
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ============================================================
-- 三、初始化数据（建议）
-- ============================================================
INSERT OR IGNORE INTO scenarios (id, name, version, module, content_json) VALUES
    ('minute_collect', '分钟采集闭环（安徽）', 'v1', 'cco', '{}'),
    ('join_anhui',     '入网（安徽）',       'v1', 'cco', '{}'),
    ('open_close',     '拉合闸',             'v1', 'cco', '{}'),
    ('search_meter',   '搜表',               'v1', 'cco', '{}');
