# 串口采集 2 天后的卡顿归因（实测）

> 实测日期：2026-08-09。数据来源：D:\zzt 部署机实况 + 23 万行测试库基准。
> 结论先行：卡顿是「数据被反复清空 + 库文件死页膨胀 + 查询无索引全表扫 + 前端每秒无条件轮询重查」共同作用的结果。

## 1. 部署机实况

| 项目 | 实测值 | 说明 |
|---|---|---|
| `log_index.sqlite3` | 443 MB | 其中 **442.9 MB（99.97%）是 freelist 死页** |
| frames 行数 | 8 | 当前会话启动后只采到 8 帧即停止 |
| 历史真实规模 | 232,973 帧 / 会话 | 8-08 20:33→8-09 20:19 单会话 104 MB LOG（≈2.7 帧/秒） |
| 数据库索引 | 仅 2 个 | `idx_frames_sequence`、`idx_minute_reports_cco`；**无 log_time 索引** |

### 1.1 根因链：数据反复消失 + 文件膨胀

`serial_service.start()` 每次启动串口都调用 `log_service.reset_index()`
→ `_initialize_database(reset=True)` 执行 `DROP TABLE frames/minute_reports`
→ SQLite 把数据页转为 freelist，**但文件大小不回收**。

2 天里反复启动 → 23 万帧被清空 → 443 MB 里只剩 40 KB 活数据。
这就是用户「反复刷新却看不到/不稳定」的直接原因。

## 2. 查询性能基准（23 万行测试库）

| 查询（当前代码路径） | 耗时 | EXPLAIN |
|---|---|---|
| 无筛选列表页 OFFSET 0 | 1 ms | SCAN frames（LIMIT 早停） |
| 深翻页 OFFSET 200,000 | **167 ms** | SCAN frames（线性扫） |
| `COUNT(*)` 全表 | 18 ms | SCAN（覆盖索引） |
| query 三列 LIKE | **221 ms** | SCAN frames |
| nid LIKE（summary_json） | **122 ms** | SCAN frames |
| 00E2 LIKE 全拉（分析页） | **293 ms** / 31,079 行 | SCAN frames |
| log_time 范围 COUNT（无索引） | 数十 ms 级 | SCAN frames |
| log_time 范围 COUNT（有索引） | **0.2 ms** | SEARCH USING COVERING INDEX |

## 3. 卡顿来源排序

| # | 来源 | 证据 | 影响 |
|---|---|---|---|
| 1 | **reset_index() 每次启动清空全部索引** | `serial_service.py:169` → `log_service.reset_index()` | 数据丢失 + 443 MB 死页膨胀，写路径每次从 freelist 分页 |
| 2 | **前端每秒无条件清缓存 + 重查列表** | `app.js:1273-1283`（`state.pageCache.clear(); loadFrames()` 每秒一次，无 await，请求可堆积） | 数据量大时每秒一次 100 ms+ 查询 + DOM 重建 |
| 3 | **筛选/范围查询全表扫描无索引** | EXPLAIN 全部 SCAN；log_time 索引可提升数百倍 | 23 万帧时筛选 122-221 ms/次 |
| 4 | **深翻页 OFFSET 线性扫描** | OFFSET 20 万 = 167 ms | 翻页越深越卡 |
| 5 | **分析接口全表 LIKE 拉取 + 逐行 json.loads** | `_e2_records`/`_e4_records`/`list_minute_periods` | 293 ms/次并随数据线性放大 |
| 6 | **逐帧独立 sqlite 连接 + 单帧 commit** | `append_frame` 每帧 `_connect()` + `commit()`（WAL 每次 fsync） | 2.7 帧/秒×24h 下的磁盘写放大 |
| 7 | **解析全局锁** | `parser_service.py` 的 `_parser_lock` 串行化采集解析与详情解析 | 高帧率时相互排队 |

## 4. 优化方向（对应实施阶段）

1. **停止自动清库**：串口启动不再 `reset_index()`；历史数据保留，用户可手动清空。
2. **回收死页**：对部署库执行 `VACUUM`（443 MB → 数 MB），启动时自动检测 freelist 比例。
3. **补索引**：`frames(log_time)`、`minute_reports(time_seconds)`；后续可提取 APP_ID/SNID 独立列。
4. **keyset 翻页**：`WHERE id > ? ORDER BY id LIMIT ?` 替代 OFFSET。
5. **批量写入**：串口攒批 INSERT + 单次 commit；解析与采集解耦。
6. **前端轮询瘦身**：仅第一页且有新帧才自动刷新；防重入；节流；手动刷新按钮。
7. **连接复用**：`threading.local` 共享 sqlite 连接，去掉每请求/每帧开连接。
8. **LOG 按天切分**：避免单文件无限增长（当前最大 104 MB/24h）。
