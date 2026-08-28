import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from listener import network_assessment


HEADER_PATTERN = re.compile(
    r"^\[(?P<sequence>[^\]]+)\]\[(?P<time>[^\]]+)\](?P<payload>.*)$"
)
HEX_BYTE_PATTERN = re.compile(r"(?i)\b[0-9a-f]{2}\b")

# 分析物化列（nid/frm_type）回填状态：db 路径 → {"running", "done", "error"}。
# 存量库在首次评估时后台补齐；完成后的库走 SQL 聚合快路径。
_BACKFILL_STATE: dict[str, dict] = {}
_BACKFILL_LOCK = threading.Lock()

# 评估结果缓存：(db 路径, 时间窗, nid, 窗口内最大帧 id) → 结果。
# 最大帧 id 参与键：串口实时模式追加新帧后自然失效，无需 TTL。
_ASSESSMENT_CACHE: dict[tuple, dict] = {}
_ASSESSMENT_CACHE_MAX = 32


class FrameParserService(Protocol):
    def parse_summary(self, value: str) -> dict: ...

    def parse(self, value: str) -> dict: ...


@dataclass(frozen=True)
class LogRecord:
    sequence: str
    log_time: str
    hex_frame: str


def extract_log_record(line: bytes) -> Optional[LogRecord]:
    text = line.decode("ascii", errors="ignore").strip()
    match = HEADER_PATTERN.match(text)
    if not match:
        return None

    tokens = HEX_BYTE_PATTERN.findall(match.group("payload"))
    try:
        first = next(index for index, token in enumerate(tokens) if token.upper() == "7E")
        last = len(tokens) - 1 - next(
            index for index, token in enumerate(reversed(tokens)) if token.upper() == "7E"
        )
    except StopIteration:
        return None

    if first >= last:
        return None
    frame = " ".join(token.upper() for token in tokens[first : last + 1])
    return LogRecord(match.group("sequence"), match.group("time"), frame)


class LogFileService:
    MAX_PAGE_SIZE = 500

    def __init__(self, parser: FrameParserService, database_path: Path, index_registry=None,
                 read_only: bool = False):
        self.parser = parser
        self.database_path = Path(database_path).resolve()
        self._index_registry = index_registry
        self._read_only = read_only
        self._index_id = None
        self._status_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._status = self._empty_status()

        if self._read_only:
            if not self.database_path.is_file():
                raise FileNotFoundError(f"索引数据库不存在：{self.database_path}")
        elif self._index_registry is not None:
            # 旧的固定路径数据库只登记为历史记录，新的 current 一律落在
            # runtime/indexes/{index_id}.sqlite3，避免下一次 DROP TABLE 覆盖历史。
            self._index_registry.adopt_legacy_index(self.database_path)
            current_index_id = self._index_registry.current_index_id()
            if current_index_id:
                self._index_id = current_index_id
                self.database_path = self._index_registry.database_path_for(current_index_id)
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
                self._initialize_database(reset=False)
            else:
                self._activate_new_index(kind="startup")
        else:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database(reset=False)

    def _activate_new_index(self, *, kind: str, source_path=None) -> dict:
        if self._index_registry is None:
            raise RuntimeError("未配置版本化索引目录")
        record = self._index_registry.create_index(kind=kind, source_path=source_path)
        self._index_id = record["index_id"]
        self.database_path = self._index_registry.database_path_for(self._index_id)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database(reset=False)
        return record

    def open_index(self, index_id: str):
        """打开历史索引的独立只读视图；不会切换或影响 current 索引。"""
        if self._index_registry is None:
            if index_id != self._index_id:
                raise KeyError(index_id)
            return self
        return LogFileService(
            self.parser, self._index_registry.database_path_for(index_id), read_only=True
        )

    @staticmethod
    def _empty_status() -> dict:
        return {
            "state": "idle",
            "source_path": None,
            "file_size": 0,
            "bytes_read": 0,
            "progress": 0.0,
            "line_count": 0,
            "frame_count": 0,
            "error_count": 0,
            "message": "请选择日志文件",
        }

    @contextmanager
    def _connect(self):
        if self._read_only:
            connection = sqlite3.connect(
                f"file:{self.database_path.as_posix()}?mode=ro", uri=True, timeout=30
            )
        else:
            connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        if not self._read_only:
            connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            if not self._read_only:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_database(self, reset: bool) -> None:
        with self._connect() as connection:
            if reset:
                connection.execute("DROP TABLE IF EXISTS minute_reports")
                connection.execute("DROP TABLE IF EXISTS frames")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS frames (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sequence TEXT NOT NULL,
                    log_time TEXT NOT NULL,
                    byte_length INTEGER NOT NULL,
                    raw_hex TEXT NOT NULL,
                    summary_json TEXT,
                    parse_error TEXT,
                    nid INTEGER,
                    frm_type TEXT,
                    assess_detail INTEGER
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS minute_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    frame_id INTEGER NOT NULL,
                    log_time TEXT NOT NULL,
                    time_seconds INTEGER NOT NULL,
                    cco_tei TEXT NOT NULL,
                    station_key TEXT NOT NULL,
                    source_mac TEXT,
                    source_tei TEXT,
                    task_no INTEGER,
                    protocol_type INTEGER,
                    meter_type INTEGER,
                    response_result INTEGER,
                    freeze_time TEXT,
                    report_count INTEGER,
                    data_length INTEGER,
                    application_error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_frames_sequence ON frames(sequence)"
            )
            # 时间范围查询是列表页与分钟分析的常用路径：log_time 索引把
            # 全表扫描降为 COVERING INDEX SEARCH（实测 76ms → 0.4ms @ 23 万行）。
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_frames_log_time ON frames(log_time)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_minute_reports_cco "
                "ON minute_reports(cco_tei)"
            )
            # 分钟周期/任务周期统计按 time_seconds（绝对毫秒）窗口分组。
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_minute_reports_time "
                "ON minute_reports(time_seconds)"
            )
            self._ensure_analysis_columns(connection)

    @staticmethod
    def _ensure_analysis_columns(connection) -> None:
        """评估分析物化列（nid/frm_type/assess_detail）的幂等迁移。

        新建库由 CREATE TABLE 直接带列；此处兼容更早版本创建的库。nid 列上的
        覆盖索引服务 SQL 聚合快路径；两个 partial 索引分别只含待回填行与
        携带 Detail 的行，回填扫描与 Detail 行源均零成本。
        """
        existing = {
            row["name"] for row in connection.execute("PRAGMA table_info(frames)")
        }
        if "nid" not in existing:
            connection.execute("ALTER TABLE frames ADD COLUMN nid INTEGER")
        if "frm_type" not in existing:
            connection.execute("ALTER TABLE frames ADD COLUMN frm_type TEXT")
        if "assess_detail" not in existing:
            connection.execute("ALTER TABLE frames ADD COLUMN assess_detail INTEGER")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_frames_nid_time_ftype "
            "ON frames(nid, log_time, frm_type)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_frames_nid_pending "
            "ON frames(id) WHERE nid IS NULL"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_frames_assess_detail "
            "ON frames(id) WHERE assess_detail = 1"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_frames_hs_pending "
            "ON frames(id) WHERE assess_detail IS NULL"
        )

    def _replace_status(self, **values) -> dict:
        with self._status_lock:
            self._status.update(values)
            result = dict(self._status)
            result["index_id"] = self._index_id
            return result

    def status(self) -> dict:
        with self._status_lock:
            result = dict(self._status)
            result["index_id"] = self._index_id
            return result

    def reset_index(self) -> dict:
        """清空并重建索引（供串口模式启动时调用，保证从干净库开始）。

        丢弃 frames / minute_reports 全部数据，仅保留表结构。
        """
        if self._index_registry is not None:
            self._activate_new_index(kind="serial")
        else:
            self._initialize_database(reset=True)
        return self._replace_status(
            state="idle",
            source_path=None,
            file_size=0,
            bytes_read=0,
            progress=0.0,
            line_count=0,
            frame_count=0,
            error_count=0,
            message="索引已清空（串口模式）",
        )

    def start_index(self, path) -> dict:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"日志文件不存在：{source}")
        if self._worker and self._worker.is_alive():
            raise RuntimeError("当前日志仍在建立索引，请完成后再加载新文件")

        file_size = source.stat().st_size
        if self._index_registry is not None:
            self._activate_new_index(kind="file", source_path=source)
        self._status = self._empty_status()
        self._replace_status(
            state="queued",
            source_path=str(source),
            file_size=file_size,
            message="等待开始建立索引",
        )
        self._worker = threading.Thread(
            target=self._index_worker,
            args=(source, self._index_registry is not None),
            name="hplc-log-indexer",
            daemon=True,
        )
        self._worker.start()
        return self.status()

    def _index_worker(self, source: Path, prepared_index: bool = False) -> None:
        try:
            self.index_file(source, _prepared_index=prepared_index)
        except Exception as exc:
            self._replace_status(state="failed", message=str(exc))

    def index_file(self, path, _prepared_index: bool = False) -> dict:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"日志文件不存在：{source}")

        file_size = source.stat().st_size
        if self._index_registry is not None and not _prepared_index:
            self._activate_new_index(kind="file", source_path=source)
        self._initialize_database(reset=True)
        self._replace_status(
            state="indexing",
            source_path=str(source),
            file_size=file_size,
            bytes_read=0,
            progress=0.0,
            line_count=0,
            frame_count=0,
            error_count=0,
            message="正在逐行建立索引",
        )

        line_count = 0
        frame_count = 0
        error_count = 0
        bytes_read = 0
        minute_day_offset = 0
        minute_prev_abs_ms = None

        with source.open("rb") as stream, self._connect() as connection:
            insert_sql = """
                INSERT INTO frames (
                    sequence, log_time, byte_length, raw_hex, summary_json,
                    parse_error, nid, frm_type, assess_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            insert_minute_sql = """
                INSERT INTO minute_reports (
                    frame_id, log_time, time_seconds, cco_tei, station_key,
                    source_mac, source_tei, task_no, protocol_type, meter_type,
                    response_result, freeze_time, report_count, data_length,
                    application_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            for line in stream:
                line_count += 1
                bytes_read += len(line)
                record = extract_log_record(line)
                if record is None:
                    continue

                summary_json = None
                parse_error = None
                simple = {}
                if self.parser is not None:
                    try:
                        parsed = self.parser.parse_summary(record.hex_frame)
                        simple = parsed.get("simple", {})
                        summary_json = json.dumps(simple, ensure_ascii=False)
                    except Exception as exc:
                        error_count += 1
                        parse_error = str(exc)

                # 分析物化列：NID 以 summary 的 SNID（DLL 权威）优先，缺失时
                # FCH 解码兜底；FrmType/Detail 直接取 summary 值。
                nid = network_assessment.snid_to_int(simple.get("SNID"))
                if nid is None:
                    nid = network_assessment.extract_nid(record.hex_frame)
                raw_frm = simple.get("FrmType")
                frm_type = str(raw_frm) if raw_frm is not None else None
                assess_detail = (
                    1 if network_assessment.detail_is_assessable(simple.get("Detail")) else 0
                )

                cursor = connection.execute(
                    insert_sql,
                    (
                        record.sequence,
                        record.log_time,
                        len(record.hex_frame.split()),
                        record.hex_frame,
                        summary_json,
                        parse_error,
                        nid,
                        frm_type,
                        assess_detail,
                    ),
                )
                frame_count += 1

                if simple.get("APP_ID") == "00E4":
                    minute_day_offset, minute_prev_abs_ms = self._insert_minute_report(
                        connection,
                        insert_minute_sql,
                        cursor.lastrowid,
                        record,
                        simple,
                        minute_day_offset,
                        minute_prev_abs_ms,
                    )

                if frame_count % 100 == 0:
                    connection.commit()
                    self._replace_status(
                        bytes_read=bytes_read,
                        progress=(bytes_read / file_size) if file_size else 1.0,
                        line_count=line_count,
                        frame_count=frame_count,
                        error_count=error_count,
                    )

            connection.commit()

        return self._replace_status(
            state="completed",
            bytes_read=bytes_read,
            progress=1.0,
            line_count=line_count,
            frame_count=frame_count,
            error_count=error_count,
            message=f"索引完成，共 {frame_count} 帧",
        )

    # ---------- 分钟采集上报持久化与周期统计 ----------

    _TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})$")

    @staticmethod
    def _parse_time_ms(text: str):
        match = LogFileService._TIME_PATTERN.match(text)
        if not match:
            return None
        hour, minute, second, milli = (int(part) for part in match.groups())
        return hour * 3_600_000 + minute * 60_000 + second * 1_000 + milli

    @staticmethod
    def _minute_fields(simple: dict) -> dict:
        """从已富化的简单摘要提取 minute_reports 表需要的字段。"""
        application = simple.get("application") or {}
        fields = {f.get("name"): f for f in application.get("fields", [])}

        def raw(name):
            field = fields.get(name)
            return field.get("raw") if field else None

        def value(name):
            field = fields.get(name)
            if not field:
                return None
            return field.get("value") or field.get("raw")

        source_mac = raw("源MAC地址")
        station_key = source_mac or simple.get("ORI_S") or simple.get("SRC") or ""
        return {
            "cco_tei": simple.get("FINL_D") or simple.get("DST") or "",
            "station_key": str(station_key),
            "source_mac": source_mac,
            "source_tei": simple.get("ORI_S"),
            "task_no": raw("任务号"),
            "protocol_type": raw("协议类型"),
            "meter_type": raw("电表类型"),
            "response_result": raw("响应结果"),
            "freeze_time": value("冻结时刻"),
            "report_count": raw("上报数量"),
            "data_length": raw("数据长度"),
            "application_error": simple.get("application_error"),
        }

    def _insert_minute_report(self, connection, insert_sql, frame_id, record,
                              simple, day_offset, prev_abs_ms):
        """插入一条分钟上报；返回更新后的 (day_offset, prev_abs_ms)。"""
        fields = self._minute_fields(simple)
        ms = self._parse_time_ms(record.log_time)
        if ms is None:
            return day_offset, prev_abs_ms
        if prev_abs_ms is not None and ms < prev_abs_ms - 12 * 3_600_000:
            day_offset += 1
        absolute_ms = day_offset * 86_400_000 + ms

        connection.execute(
            insert_sql,
            (
                frame_id,
                record.log_time,
                absolute_ms,
                fields["cco_tei"],
                fields["station_key"],
                fields["source_mac"],
                fields["source_tei"],
                fields["task_no"],
                fields["protocol_type"],
                fields["meter_type"],
                fields["response_result"],
                fields["freeze_time"],
                fields["report_count"],
                fields["data_length"],
                fields["application_error"],
            ),
        )
        return day_offset, absolute_ms

    @staticmethod
    def _clock_text(absolute_ms: int) -> str:
        ms = absolute_ms % 86_400_000
        hour, rem = divmod(ms, 3_600_000)
        minute, rem = divmod(rem, 60_000)
        second, milli = divmod(rem, 1_000)
        return f"{hour:02d}:{minute:02d}:{second:02d}.{milli:03d}"

    @staticmethod
    def _minute_report_data_status(row) -> str:
        if row["application_error"]:
            return "应用层解析失败"
        result = row["response_result"]
        if result == 0:
            return "无数据" if not row["data_length"] else "已携带数据"
        return {
            1: "任务不存在",
            2: "无冻结数据",
            3: "其他原因",
        }.get(result, "响应结果未知")

    @staticmethod
    def _nid_like(nid: str) -> str:
        """构造匹配 summary_json 中 SNID 键（24 位网络标识 NID）的 LIKE 模式，并转义通配符。

        与 SQL 中的 `ESCAPE '\\'` 搭配使用；转义 `%`/`_`/`\\` 防止输入被当作通配符。
        """
        escaped = (
            nid.strip()
            .upper()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f'%"SNID": "{escaped}%'

    def _append_time_range(self, conditions, parameters, start_time="", end_time="", column="log_time"):
        start_bound = self._time_range_bound(start_time)
        end_bound = self._time_range_bound(end_time, is_end=True)
        if start_bound and end_bound and start_bound > end_bound:
            raise ValueError("结束时间不能早于起始时间")
        if start_bound:
            conditions.append(f"{column} >= ?")
            parameters.append(start_bound)
        if end_bound:
            conditions.append(f"{column} <= ?")
            parameters.append(end_bound)

    def list_minute_periods(self, period_minutes=15, cco_tei="001", nid="") -> list:
        if not isinstance(period_minutes, int) or not 1 <= period_minutes <= 1440:
            raise ValueError("period_minutes 必须在 1 到 1440 之间")
        if not isinstance(cco_tei, str) or not re.fullmatch(
            r"[0-9A-F]{3}", cco_tei.upper()
        ):
            raise ValueError("cco_tei 必须为三个大写十六进制字符")

        period_ms = period_minutes * 60_000
        conditions = ["minute_reports.cco_tei = ?"]
        parameters = [cco_tei.upper()]
        if nid.strip():
            # 按 24 位网络标识（SNID 键）过滤，与帧浏览页 NID 筛选一致
            conditions.append("frames.summary_json LIKE ? ESCAPE '\\'")
            parameters.append(self._nid_like(nid))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT minute_reports.frame_id, minute_reports.log_time,
                       minute_reports.time_seconds, minute_reports.source_mac,
                       minute_reports.source_tei, minute_reports.freeze_time,
                       minute_reports.response_result,
                       minute_reports.report_count, minute_reports.data_length,
                       minute_reports.application_error,
                       frames.summary_json
                FROM minute_reports
                LEFT JOIN frames ON frames.id = minute_reports.frame_id
                WHERE {' AND '.join(conditions)}
                ORDER BY minute_reports.id
                """,
                parameters,
            ).fetchall()

        groups = {}
        for row in rows:
            start = row["time_seconds"] - (row["time_seconds"] % period_ms)
            groups.setdefault(start, []).append(row)

        periods = []
        for start in sorted(groups):
            bucket = groups[start]
            reports = []
            for row in bucket:
                summary = json.loads(row["summary_json"] or "{}")
                reports.append(
                    {
                        "frame_id": row["frame_id"],
                        "log_time": row["log_time"],
                        "source_mac": row["source_mac"],
                        "source_tei": row["source_tei"],
                        "freeze_time": row["freeze_time"],
                        "response_result": row["response_result"],
                        "report_count": row["report_count"],
                        "data_length": row["data_length"],
                        "application_error": row["application_error"],
                        "data_status": self._minute_report_data_status(row),
                        "application_raw": summary.get("APP_RAW"),
                    }
                )
            periods.append(
                {
                    "period_start": start,
                    "period_end": start + period_ms,
                    "report_count": len(reports),
                    "reports": reports,
                    "description": (
                        f"{self._clock_text(start)} - "
                        f"{self._clock_text(start + period_ms)}"
                    ),
                }
            )
        return periods

    def list_task_minute_periods(self, task_no, period_minutes=None, cco_tei="001", nid="", start_time="", end_time=""):
        """按每个 STA 实际启用到删除成功之间的周期汇总分钟采集上报。

        划分周期以实际周期为准：有任务配置时按配置周期，否则按手工输入的
        period_minutes。每个周期窗口内统计该任务所有分钟采集上报帧，按
        (STA, 应用层原文) 双重去重后再做统计计算，保留冻结数据判定逻辑。
        """
        task = self._task_key(task_no)
        if period_minutes is not None and (
            not isinstance(period_minutes, int) or not 1 <= period_minutes <= 1440
        ):
            raise ValueError("period_minutes 必须在 1 到 1440 之间")

        configured, derived_period = self._task_config_periods(cco_tei, task, nid)
        effective_period = derived_period or period_minutes

        conditions = ["minute_reports.cco_tei = ?", "minute_reports.task_no = ?"]
        parameters = [cco_tei.upper(), int(task)]
        if nid.strip():
            conditions.append("frames.summary_json LIKE ? ESCAPE '\\'")
            parameters.append(self._nid_like(nid))
        self._append_time_range(conditions, parameters, start_time, end_time, "minute_reports.log_time")
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT minute_reports.*, frames.summary_json
                FROM minute_reports
                LEFT JOIN frames ON frames.id = minute_reports.frame_id
                WHERE {' AND '.join(conditions)}
                ORDER BY minute_reports.id
                """, parameters
            ).fetchall()

        source = "configured" if configured else "manual"
        reports_by_window, unconfigured_reports = {}, []
        horizon = max((row["time_seconds"] for row in rows), default=None)
        for row in rows:
            mac = self._sta_key(row["source_mac"])
            config = configured.get(mac)
            minutes = (config or {}).get("period_minutes") or effective_period
            if not minutes:
                continue
            period_ms = int(minutes) * 60_000
            start = row["time_seconds"] - row["time_seconds"] % period_ms
            expected_freeze = start - period_ms
            freeze = row["freeze_time"] or ""
            # 冻结时刻含日期时，日志并不一定含日期；以时分秒校验固定周期边界。
            expected_clock = self._clock_text(expected_freeze)
            item = {
                "frame_id": row["frame_id"], "mac": mac, "log_time": row["log_time"],
                "source_mac": row["source_mac"] or mac,
                "source_tei": row["source_tei"] or "",
                "freeze_time": freeze, "expected_freeze_time": expected_clock,
                "freeze_ok": freeze.endswith(expected_clock[:8]),
                "period_minutes": int(minutes),
                "response_result": row["response_result"],
                "report_count": row["report_count"],
                "data_length": row["data_length"],
                "application_error": row["application_error"],
                "data_status": self._minute_report_data_status(row),
                "config_time": (config or {}).get("log_time", ""),
                "config_end_time": (config or {}).get("end_time", ""),
                "config_content": (config or {}).get("config_content", {}),
                "in_config_period": bool(config and row["time_seconds"] >= config["absolute_ms"]
                                         and (config["end_ms"] is None or row["time_seconds"] < config["end_ms"])),
                "application_raw": json.loads(row["summary_json"] or "{}").get("APP_RAW"),
            }
            if source == "configured" and not item["in_config_period"]:
                unconfigured_reports.append(item)
                continue
            reports_by_window.setdefault(start, []).append(item)

        # 为每一台已配置 STA 生成其实际周期内的应报窗口，未上报的窗口也会被展示。
        expected_by_window = {}
        if configured and horizon is not None:
            for mac, config in configured.items():
                period_ms = config["period_minutes"] * 60_000
                start = config["absolute_ms"] - config["absolute_ms"] % period_ms
                if start < config["absolute_ms"]:
                    start += period_ms
                stop = min(horizon, config["end_ms"] - 1) if config["end_ms"] else horizon
                while start <= stop:
                    expected_by_window.setdefault(start, {})[mac] = config
                    start += period_ms

        periods = []
        for start in sorted(set(reports_by_window) | set(expected_by_window)):
            reports = reports_by_window.get(start, [])
            expected = expected_by_window.get(start, {})
            received = {item["mac"] for item in reports}
            # 双重去重：同一 STA 同一窗口内相同应用层原文（同一上报被多次抓取）
            # 只算一次；明细全部保留。
            deduped_items = {}
            for item in reports:
                deduped_items.setdefault(
                    (item["mac"], item["application_raw"] or ""), item
                )
            deduped_items = list(deduped_items.values())
            # 窗口实际周期取窗口内上报/预期配置周期中的主流周期。
            window_minutes = self._mode(
                [item["period_minutes"] for item in reports]
                + [config["period_minutes"] for config in expected.values()]
            ) or effective_period or 1
            window_ms = window_minutes * 60_000
            missing = sorted(set(expected) - received)
            periods.append({
                "period_start": start,
                "period_end": start + window_ms,
                "period_minutes": window_minutes,
                "description": (
                    f"{self._clock_text(start)} - "
                    f"{self._clock_text(start + window_ms)}"
                ),
                "report_count": len(reports),
                "received_sta_count": len(received),
                "deduped_app_count": len(deduped_items),
                "expected_count": len(expected) if source == "configured" else None,
                "missing_stas": missing if source == "configured" else [],
                "freeze_ok_count": sum(item["freeze_ok"] for item in deduped_items),
                "freeze_error_count": sum(not item["freeze_ok"] for item in deduped_items),
                "reports": reports,
            })
        return {
            "task_no": task, "source": source,
            "derived_period_minutes": derived_period,
            "periods": periods,
            "unconfigured_report_count": len(unconfigured_reports),
            "unconfigured_reports": unconfigured_reports,
        }

    @staticmethod
    def _e2_fields(simple: dict):
        """从 00E2 帧的 simple 提取应用层字段名 -> 值 映射。"""
        application = simple.get("application") or {}
        return {
            f.get("name"): (f.get("value") or f.get("raw"))
            for f in application.get("fields", [])
        }

    def _e2_records(self, cco_tei: str, nid: str = "", start_time="", end_time=""):
        """遍历 00E2 帧，产出 (kind, 记录) ；kind ∈ {down_delete, up}。

        下行删除：报文源（ORI_S）为指定 CCO 且「启动/删除标志 = 删除」；
        上行应答：报文目的（FINL_D）为指定 CCO（发往 CCO 即上行）。
        上行应答的应用层字段 DLL 未完整解析，从 APP_RAW 补充解析：
        结构 11 E2 00 00 | C1 | 序号3 | 00 00 | 源MAC6 | 任务号 | 结果 | 长度
        """
        if not isinstance(cco_tei, str) or not re.fullmatch(
            r"[0-9A-F]{3}", cco_tei.upper()
        ):
            raise ValueError("cco_tei 必须为三个大写十六进制字符")
        cco = cco_tei.upper()

        conditions = [
            "summary_json IS NOT NULL",
            "summary_json != ''",
            "summary_json LIKE '%00E2%'",
        ]
        parameters = []
        if nid.strip():
            # 按 24 位网络标识（SNID 键）过滤，与帧浏览页 NID 筛选一致
            conditions.append("summary_json LIKE ? ESCAPE '\\'")
            parameters.append(self._nid_like(nid))

        self._append_time_range(conditions, parameters, start_time, end_time)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, log_time, summary_json FROM frames
                WHERE {' AND '.join(conditions)}
                ORDER BY id
                """,
                parameters,
            ).fetchall()

        for frame_id, log_time, summary_json in rows:
            try:
                simple = json.loads(summary_json)
            except (ValueError, TypeError):
                continue
            if (simple.get("APP_ID") or "").upper() != "00E2":
                continue
            fields = self._e2_fields(simple)
            finl_d = (simple.get("FINL_D") or "").upper()
            ori_s = (simple.get("ORI_S") or "").upper()

            if finl_d == cco and ori_s and ori_s != cco:
                # 上行应答：发往 CCO
                raw = simple.get("APP_RAW") or ""
                try:
                    bs = bytes.fromhex(raw)
                except (ValueError, TypeError):
                    continue
                if len(bs) < 19:
                    continue
                seq = bytes(bs[5:8])
                mac = "".join(f"{b:02X}" for b in bs[10:16])
                task_no = bs[16]
                # 字段布局（用户真机解析确认）：
                #   byte[16]=任务号；byte[18]=采集周期(min)；
                #   byte[17] 组合位字段：bit0=启用/删除标志(0=删除,1=启用)，
                #   bit1=结果(0=设置成功,1=设置失败)。
                del_flag = "删除" if (bs[17] & 0x01) == 0x00 else "启用"
                result = "失败" if ((bs[17] >> 1) & 0x01) == 0x01 else "成功"
                period = str(bs[18])
                yield "up", {
                    "frame_id": frame_id,
                    "log_time": log_time,
                    "mac": mac,
                    "seq": seq.hex().upper(),
                    "task_no": str(task_no),
                    "del_flag": del_flag,
                    "period": period,
                    "result": result,
                    "app_raw": raw,
                }
            elif (
                ori_s == cco
                and finl_d
                and finl_d != cco
                and fields.get("启动/删除标志") in {"删除", "启用"}
            ):
                # 下行任务配置：CCO 发出
                yield "down_config", {
                    "frame_id": frame_id,
                    "log_time": log_time,
                    "mac": fields.get("目的MAC地址") or "",
                    "seq": fields.get("报文序号") or "",
                    "task_no": str(fields.get("任务号") or ""),
                    "operation": fields.get("启动/删除标志"),
                    "period_minutes": fields.get("采集周期"),
                    "config_content": {
                        "protocol_type": fields.get("协议类型"),
                        "meter_type": fields.get("电表类型"),
                        "data_item_count": fields.get("数据项个数"),
                    },
                    "app_raw": simple.get("APP_RAW") or "",
                }

    def delete_config_stats(self, cco_tei: str, nid: str = "") -> dict:
        """统计删除配置下发与上行应答（均按应用层去重）。

        返回：
          down_total / down_deduped  删除配置下发帧数（去重键 报文序号+目的MAC）
          up_total / up_deduped      上行应答帧数（去重键 序号+源MAC）
          up_success / up_fail       上行应答去重后成功/失败条数
        """
        down_total = 0
        down_seen = set()
        up_total = 0
        up_seen = set()
        up_success = 0
        up_fail = 0
        for kind, record in self._e2_records(cco_tei, nid):
            if kind == "down_config" and record["operation"] == "删除":
                down_total += 1
                down_seen.add((record["seq"], record["mac"]))
            elif kind == "up":
                up_total += 1
                key = (record["seq"], record["mac"])
                if key not in up_seen:
                    up_seen.add(key)
                    if record["result"] == "成功":
                        up_success += 1
                    else:
                        up_fail += 1
        return {
            "down_total": down_total,
            "down_deduped": len(down_seen),
            "up_total": up_total,
            "up_deduped": len(up_seen),
            "up_success": up_success,
            "up_fail": up_fail,
        }

    def delete_config_details(self, cco_tei: str, nid: str = "") -> dict:
        """删除配置统计详情（去重后记录列表）。"""
        down_seen = {}
        up_seen = {}
        for kind, record in self._e2_records(cco_tei, nid):
            if kind == "down_config" and record["operation"] == "删除":
                down_seen.setdefault((record["seq"], record["mac"]), record)
            elif kind == "up":
                up_seen.setdefault((record["seq"], record["mac"]), record)
        return {
            "down": sorted(down_seen.values(), key=lambda r: r["log_time"]),
            "up": sorted(up_seen.values(), key=lambda r: r["log_time"]),
        }

    @staticmethod
    def _task_key(value) -> str:
        """规范任务号，避免解析器将同一任务表示为 int 或 str。"""
        try:
            return str(int(str(value), 10))
        except (TypeError, ValueError):
            return str(value or "").strip()

    @staticmethod
    def _sta_key(mac) -> str:
        return re.sub(r"[^0-9A-Fa-f]", "", str(mac or "")).upper()

    @staticmethod
    def _mode(values) -> object:
        """返回出现次数最多的值；并列取较小者；空序列返回 None。"""
        if not values:
            return None
        counts = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return max(counts, key=lambda value: (counts[value], -value))

    def _task_config_periods(self, cco_tei: str, task: str, nid: str = ""):
        """遍历 00E2 任务配置，返回 (configured, derived_period)。

        configured: mac -> 该 STA 最后一次启用的配置记录（含 period_minutes、
                    end_ms/end_time，删除成功应答后 end_ms 非空）；
        derived_period: 任务级推导周期（启用配置周期的众数），无配置时为 None。
        """
        # 每台 STA 以最后一次启用作为本轮配置开始，以该 STA 的删除成功应答作为结束。
        active, configured = {}, {}
        for kind, record in self._task_config_records(cco_tei, nid):
            if record["task_no"] != task or not record["mac"]:
                continue
            mac = record["mac"]
            if kind == "down_config" and record["operation"] == "启用":
                try:
                    configured_period = int(record.get("period_minutes") or 0)
                except (TypeError, ValueError):
                    configured_period = 0
                if configured_period:
                    active[mac] = {
                        **record, "period_minutes": configured_period,
                        "end_ms": None, "end_time": "",
                    }
                    configured[mac] = active[mac]
            elif kind == "up" and record.get("del_flag") == "删除" and record.get("result") == "成功":
                config = active.get(mac)
                if config and record["absolute_ms"] is not None:
                    config["end_ms"] = record["absolute_ms"]
                    config["end_time"] = record["log_time"]
                    active.pop(mac, None)
        periods = [config["period_minutes"] for config in configured.values()]
        return configured, self._mode(periods)

    def task_derived_period(self, cco_tei: str, task_no: str, nid: str = "") -> dict:
        """返回任务在配置推导下的实际周期（启用配置周期的众数）。

        source 为 configured 表示该任务存在启用配置；否则为 manual（无配置）。
        """
        task = self._task_key(task_no)
        configured, derived_period = self._task_config_periods(cco_tei, task, nid)
        return {
            "task_no": task,
            "source": "configured" if configured else "manual",
            "derived_period_minutes": derived_period,
        }

    def _task_config_records(self, cco_tei: str, nid: str = "", start_time="", end_time=""):
        """产出带绝对日志时间的 00E2 任务配置记录。"""
        day_offset = 0
        previous_ms = None
        for kind, record in self._e2_records(cco_tei, nid, start_time, end_time):
            clock_ms = self._parse_time_ms(record["log_time"])
            if clock_ms is None:
                absolute_ms = None
            else:
                if previous_ms is not None and clock_ms < previous_ms - 12 * 3_600_000:
                    day_offset += 1
                absolute_ms = day_offset * 86_400_000 + clock_ms
                previous_ms = clock_ms
            record = dict(record)
            record["task_no"] = self._task_key(record["task_no"])
            record["mac"] = self._sta_key(record["mac"])
            record["absolute_ms"] = absolute_ms
            yield kind, record

    def list_task_config_numbers(self, cco_tei: str, nid: str = "", start_time="", end_time="") -> list[str]:
        """返回当前范围内下发或上报过的任务号。"""
        task_numbers = {
            record["task_no"]
            for _, record in self._task_config_records(cco_tei, nid, start_time, end_time)
            if record["task_no"]
        }
        return sorted(task_numbers, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))

    def task_config_summary(self, cco_tei: str, task_no: str, nid: str = "", start_time="", end_time="") -> dict:
        """按任务号汇总 STA 下发及应答状态。"""
        requested_task = self._task_key(task_no)
        down_by_sta = {}
        up_by_sta = {}
        observed_ms = None
        for kind, record in self._task_config_records(cco_tei, nid, start_time, end_time):
            if record["absolute_ms"] is not None:
                observed_ms = max(observed_ms or record["absolute_ms"], record["absolute_ms"])
            if record["task_no"] != requested_task or not record["mac"]:
                continue
            if kind == "down_config":
                down_by_sta[record["mac"]] = record
            elif kind == "up":
                up_by_sta.setdefault(record["mac"], []).append(record)

        rows = []
        counts = {
            "sent_sta_count": len(down_by_sta), "success_sta_count": 0,
            "failed_sta_count": 0, "no_response_sta_count": 0,
            "pending_sta_count": 0, "unissued_report_sta_count": 0,
        }
        for mac in sorted(set(down_by_sta) | set(up_by_sta)):
            down = down_by_sta.get(mac)
            replies = up_by_sta.get(mac, [])
            success = next((reply for reply in reversed(replies) if reply["result"] == "成功"), None)
            latest_reply = success or (replies[-1] if replies else None)
            if down is None:
                status = "未下发"
                counts["unissued_report_sta_count"] += 1
            elif success:
                status = "成功"
                counts["success_sta_count"] += 1
            elif replies:
                status = "失败"
                counts["failed_sta_count"] += 1
            elif (down["absolute_ms"] is not None and observed_ms is not None
                  and observed_ms - down["absolute_ms"] >= 60_000):
                status = "未应答"
                counts["no_response_sta_count"] += 1
            else:
                status = "等待应答"
                counts["pending_sta_count"] += 1
            frames = []
            if down:
                frames.append({
                    "direction": "downlink",
                    "label": f"下行 {cco_tei.upper()}→STA",
                    "log_time": down["log_time"],
                    "app_raw": down.get("app_raw", ""),
                    "frame_id": down["frame_id"],
                })
            frames.extend({
                "direction": "uplink",
                "label": f"上行 STA→{cco_tei.upper()}",
                "log_time": reply["log_time"],
                "app_raw": reply.get("app_raw", ""),
                "frame_id": reply["frame_id"],
            } for reply in replies)
            frames.sort(key=lambda frame: frame["frame_id"])
            rows.append({
                "mac": mac,
                "directions": "；".join(
                    direction for direction, present in (
                        (f"下行 {cco_tei.upper()}→STA", down is not None),
                        (f"上行 STA→{cco_tei.upper()}", bool(replies)),
                    ) if present
                ),
                "operation": down["operation"] if down else (latest_reply or {}).get("del_flag", ""),
                "sent_time": down["log_time"] if down else "",
                "reply_time": (latest_reply or {}).get("log_time", ""),
                "status": status,
                "sequence": down["seq"] if down else (latest_reply or {}).get("seq", ""),
                "app_raw": (down or latest_reply or {}).get("app_raw", ""),
                "frame_id": (down or latest_reply or {}).get("frame_id"),
                "frames": frames,
            })
        return {"task_no": requested_task, **counts, "stas": rows}

    def _e4_records(self, cco_tei: str, nid: str = ""):
        conditions = ["summary_json IS NOT NULL", "summary_json LIKE '%00E4%'"]
        parameters = []
        if nid.strip():
            conditions.append("summary_json LIKE ? ESCAPE '\\'")
            parameters.append(self._nid_like(nid))
        with self._connect() as connection:
            rows = connection.execute(f"SELECT id, log_time, summary_json FROM frames WHERE {' AND '.join(conditions)} ORDER BY id", parameters).fetchall()
        for frame_id, log_time, summary_json in rows:
            try:
                simple = json.loads(summary_json)
            except (TypeError, ValueError):
                continue
            if (simple.get("APP_ID") or "").upper() != "00E4" or (simple.get("FINL_D") or "").upper() != cco_tei.upper():
                continue
            fields = self._e2_fields(simple)
            yield {"frame_id": frame_id, "log_time": log_time, "mac": fields.get("源MAC地址") or "", "task_no": str(fields.get("任务号") or ""), "app_raw": simple.get("APP_RAW") or ""}

    def task_config_lifecycle_summary(self, cco_tei: str, task_no: str, nid: str = "", cycle_index: int | None = None) -> dict:
        task = self._task_key(task_no)
        events = [(kind, record) for kind, record in self._task_config_records(cco_tei, nid)]
        events += [("report", {**record, "task_no": self._task_key(record["task_no"]), "mac": self._sta_key(record["mac"]), "absolute_ms": self._parse_time_ms(record["log_time"])}) for record in self._e4_records(cco_tei, nid)]
        events.sort(key=lambda item: item[1]["frame_id"])
        cycles, active = [], {}
        def start(record):
            cycle = {"start_time": record["log_time"], "start_ms": record["absolute_ms"], "stas": {}, "deletes": {}, "delete_mode": "", "delete_success_count": 0, "delete_fail_count": 0, "delete_pending_count": 0, "anomalies": [], "status": "进行中", "end_time": None, "last_delete_time": None}
            cycles.append(cycle); active[task] = cycle
            return cycle
        for kind, record in events:
            record_task = record["task_no"]
            affected = list(active) if kind == "down_config" and record["operation"] == "删除" and record["mac"] == "999999999999" and record_task == "255" else [record_task]
            for key in affected:
                cycle = active.get(key)
                if kind == "down_config" and record["operation"] == "启用" and key == task:
                    cycle = start(record) if cycle is None else cycle; cycle["stas"][record["mac"]] = record
                elif kind == "down_config" and record["operation"] == "删除" and cycle:
                    targets = list(cycle["stas"]) if record["mac"] == "999999999999" else [record["mac"]]
                    cycle["delete_mode"] = "广播全清" if record["mac"] == "999999999999" else cycle["delete_mode"] or "单播删除"; cycle["last_delete_time"] = record["log_time"]
                    for mac in targets: cycle["deletes"].setdefault(mac, {"down": record, "reply": None})
                elif kind == "up" and cycle and key == task and record["mac"] in cycle["deletes"]:
                    prior = cycle["deletes"][record["mac"]]["reply"]
                    if prior is None or record["result"] == "成功": cycle["deletes"][record["mac"]]["reply"] = record
                elif kind == "report" and cycle and key == task and record["mac"] in cycle["deletes"]:
                    reply = cycle["deletes"][record["mac"]]["reply"]
                    if reply and reply["result"] == "成功": cycle["anomalies"].append({"type": "删除成功后仍上报", "mac": record["mac"], "delete_time": reply["log_time"], "report_time": record["log_time"]})
        for cycle in cycles:
            replies = [item["reply"] for item in cycle["deletes"].values()]
            cycle["delete_success_count"] = sum(reply is not None and reply["result"] == "成功" for reply in replies)
            cycle["delete_fail_count"] = sum(reply is not None and reply["result"] != "成功" for reply in replies)
            # 删除未下发与删除已下发未应答必须分开统计，避免误把前者显示为未应答。
            cycle["delete_not_sent_count"] = sum(mac not in cycle["deletes"] for mac in cycle["stas"])
            cycle["delete_pending_count"] = sum(
                mac in cycle["deletes"] and item["reply"] is None
                for mac, item in cycle["deletes"].items()
            )
            if cycle["stas"] and cycle["delete_success_count"] == len(cycle["stas"]):
                cycle["status"] = "已完成"; cycle["end_time"] = max((item["reply"] for item in cycle["deletes"].values() if item["reply"] and item["reply"]["result"] == "成功"), key=lambda reply: reply["frame_id"])["log_time"]
            elif cycle["delete_fail_count"]: cycle["status"] = "删除异常"
            elif cycle["deletes"]: cycle["status"] = "删除未完成"
            cycle["configured_sta_count"] = len(cycle["stas"]); cycle["stas"] = [{"mac": mac, "config_time": info["log_time"], "delete_time": cycle["deletes"].get(mac, {}).get("down", {}).get("log_time", ""), "delete_result": (cycle["deletes"].get(mac, {}).get("reply") or {}).get("result", "未下发删除")} for mac, info in sorted(cycle["stas"].items())]
        selected = cycles[cycle_index if cycle_index is not None else len(cycles) - 1] if cycles else None
        return {"task_no": task, "cycles": cycles, "cycle": selected}

    @staticmethod
    def _time_range_bound(value, is_end=False):
        if not value:
            return ""
        match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d{3}))?", value)
        if not match:
            raise ValueError("时间必须是 HH:MM:SS 或 HH:MM:SS.mmm 格式")
        hour, minute, second, milli = match.groups()
        if int(hour) > 23 or int(minute) > 59 or int(second) > 59:
            raise ValueError("时间不在有效的时分秒范围内")
        return f"{hour}:{minute}:{second}.{milli or ('999' if is_end else '000')}"

    def list_frames(
        self, offset=0, limit=100, query="", nid="", start_time="", end_time="",
        after_id=None, start_id=None, end_id=None,
    ) -> dict:
        if offset < 0:
            raise ValueError("offset 不能小于 0")
        if limit < 1 or limit > self.MAX_PAGE_SIZE:
            raise ValueError(f"limit 必须在 1 到 {self.MAX_PAGE_SIZE} 之间")
        start_bound = self._time_range_bound(start_time)
        end_bound = self._time_range_bound(end_time, is_end=True)
        if start_bound and end_bound and start_bound > end_bound:
            raise ValueError("结束时间不能早于起始时间")
        for value, name in ((start_id, "start_id"), (end_id, "end_id")):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"{name} 必须是整数")
            if value is not None and value < 0:
                raise ValueError(f"{name} 不能小于 0")
        if start_id is not None and end_id is not None and start_id > end_id:
            raise ValueError("end_id 不能小于 start_id")

        conditions = []
        parameters = []
        if query:
            conditions.append(
                "(sequence LIKE ? OR log_time LIKE ? OR summary_json LIKE ?)"
            )
            wildcard = f"%{query}%"
            parameters.extend([wildcard, wildcard, wildcard])
        if nid.strip():
            # summary_json 中的 SNID 键即 24 位网络标识（NID）
            conditions.append("summary_json LIKE ? ESCAPE '\\'")
            parameters.append(self._nid_like(nid))
        if start_bound:
            conditions.append("log_time >= ?")
            parameters.append(start_bound)
        if end_bound:
            conditions.append("log_time <= ?")
            parameters.append(end_bound)
        if start_id is not None:
            conditions.append("id >= ?")
            parameters.append(start_id)
        if end_id is not None:
            conditions.append("id <= ?")
            parameters.append(end_id)
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._connect() as connection:
            if after_id is not None:
                # keyset 游标翻页：仅取 id > after_id 的下一页，避免 OFFSET 深翻页线性扫描
                id_cond = f"{where_sql} AND id > ?" if conditions else "WHERE id > ?"
                rows = connection.execute(
                    f"""
                    SELECT id, sequence, log_time, byte_length, summary_json, parse_error
                    FROM frames
                    {id_cond}
                    ORDER BY id
                    LIMIT ?
                    """,
                    [*parameters, after_id, limit],
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT id, sequence, log_time, byte_length, summary_json, parse_error
                    FROM frames
                    {where_sql}
                    ORDER BY id
                    LIMIT ? OFFSET ?
                    """,
                    [*parameters, limit, offset],
                ).fetchall()
            # COUNT 降频：同一筛选条件 2 秒内复用计数，避免列表页每次请求全表扫描
            count_key = (where_sql, tuple(parameters))
            cached = getattr(self, "_count_cache", None)
            now = time.time()
            if cached and cached[0] == count_key and now - cached[2] < 2.0:
                total = cached[1]
            else:
                total = connection.execute(
                    f"SELECT COUNT(*) FROM frames {where_sql}", parameters
                ).fetchone()[0]
                self._count_cache = (count_key, total, now)

        items = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "frame_id": row["id"],
                    "sequence": row["sequence"],
                    "log_time": row["log_time"],
                    "byte_length": row["byte_length"],
                    "summary": json.loads(row["summary_json"] or "{}"),
                    "parse_error": row["parse_error"],
                }
            )

        return {
            "index_id": self._index_id,
            "items": items,
            "offset": offset,
            "limit": limit,
            "total": total,
            "after_id": items[-1]["id"] if items else None,
        }

    # ---------- 网络承载能力评估（按中央信标周期 + 网络隔离）----------

    # ---------- 网络承载能力评估（全量单趟 + NID 严格分网 + 物化列快路径）----------

    SQL_PATH_MIN_COVERAGE = 0.5   # 窗口内 frm_type 物化覆盖率低于该值时退回 Python 解码路径
    _BACKFILL_BATCH = 5000

    @staticmethod
    def _nid_from_summary(summary_json) -> Optional[int]:
        """从 summary_json 的 SNID 键（24 位网络标识，8 位十六进制）解析 NID。"""
        try:
            simple = json.loads(summary_json or "{}")
            return network_assessment.snid_to_int(simple.get("SNID"))
        except (TypeError, ValueError):
            return None

    def _frames_where(self, start_time="", end_time="", column="log_time"):
        """frames 表时间窗 WHERE 片段与参数。"""
        conditions, parameters = [], []
        self._append_time_range(conditions, parameters, start_time, end_time, column)
        return conditions, parameters

    @staticmethod
    def _where_sql(conditions, extra=None):
        parts = list(conditions)
        if extra:
            parts.append(extra)
        return f"WHERE {' AND '.join(parts)}" if parts else ""

    def _frames_max_id(self, start_time="", end_time="") -> int:
        conditions, parameters = self._frames_where(start_time, end_time)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT MAX(id) FROM frames {self._where_sql(conditions)}", parameters
            ).fetchone()
            return row[0] or 0

    def _session_duration_s(self, start_time="", end_time="") -> Optional[float]:
        """日志会话权威时长：时间窗内按 id 序首尾帧的 log_time 跨度（秒）。

        log_time 只有时分秒没有日期，跨天日志的字符串 MIN/MAX 会算错；
        帧按 id 插入即时序即时间序，首尾两端经 _absolute_ms 处理跨天翻转。
        """
        conditions, parameters = self._frames_where(start_time, end_time)
        where_sql = self._where_sql(conditions)
        with self._connect() as connection:
            first = connection.execute(
                f"SELECT log_time FROM frames {where_sql} ORDER BY id LIMIT 1",
                parameters,
            ).fetchone()
            last = connection.execute(
                f"SELECT log_time FROM frames {where_sql} ORDER BY id DESC LIMIT 1",
                parameters,
            ).fetchone()
        if not first or not last:
            return None
        absolute = network_assessment._absolute_ms([first[0], last[0]])
        if absolute[0] is None or absolute[1] is None:
            return None
        return max(0.0, (absolute[1] - absolute[0]) / 1000.0)

    def resolve_assessment_nid(self, nid_text: str, start_time="", end_time="") -> Optional[int]:
        """评估的 NID 过滤参数解析：兼容十进制（网络卡片显示）与十六进制
        （SNID/帧列表过滤习惯）两种写法；有歧义时查库消歧，都在/都不在偏向
        十六进制解读（与既有全局 NID 过滤一致）。无效输入抛 ValueError。"""
        text = (nid_text or "").strip()
        if not text:
            return None
        candidates = []
        if text.isdigit():
            value = int(text)
            if 0 < value <= 0xFFFFFF:
                candidates.append(value)
        try:
            hex_value = int(text, 16)
        except ValueError:
            if not candidates:
                raise ValueError(f"NID 格式无效：{nid_text}") from None
        else:
            if 0 < hex_value <= 0xFFFFFF and hex_value not in candidates:
                candidates.append(hex_value)
        if not candidates:
            raise ValueError(f"NID 超出协议范围（1~16777215）：{nid_text}")
        if len(candidates) == 1:
            return candidates[0]
        conditions, parameters = self._frames_where(start_time, end_time)
        counts = {}
        with self._connect() as connection:
            for value in candidates:
                row = connection.execute(
                    f"SELECT COUNT(*) FROM frames {self._where_sql(conditions, 'nid = ?')}",
                    [*parameters, value],
                ).fetchone()
                counts[value] = row[0]
        present = [v for v in candidates if counts[v] > 0]
        if len(present) == 1:
            return present[0]
        return candidates[-1]  # 十六进制解读优先（与帧列表过滤语义一致）

    # -- 物化列回填 ---------------------------------------------------------

    def request_backfill(self):
        """存量库补齐 nid/frm_type 物化列（幂等，后台一次）。返回线程或 None。"""
        state = _BACKFILL_STATE.setdefault(
            str(self.database_path), {"running": False, "done": False, "error": None}
        )
        with _BACKFILL_LOCK:
            if state["running"] or state["done"]:
                return None
            state["running"] = True
        worker = threading.Thread(
            target=self._backfill_worker, name="hplc-analysis-backfill", daemon=True
        )
        worker.start()
        return worker

    def _backfill_worker(self) -> None:
        path = str(self.database_path)
        state = _BACKFILL_STATE[path]
        try:
            # 回填需要写连接；WAL 模式下与读连接共存，不阻塞评估查询。
            connection = sqlite3.connect(self.database_path, timeout=30)
            connection.row_factory = sqlite3.Row
            try:
                self._ensure_analysis_columns(connection)
                self._backfill_nid_batches(connection)
                self._backfill_frm_type_passes(connection)
                self._backfill_assess_detail_pass(connection)
                connection.commit()
            finally:
                connection.close()
            state["done"] = True
            state["error"] = None
        except Exception as exc:  # 失败不阻断评估，下次评估重新触发
            state["error"] = str(exc)
        finally:
            state["running"] = False

    def _backfill_nid_batches(self, connection) -> None:
        """批次回填 nid（summary SNID 优先，FCH 解码兜底），顺带补 frm_type/assess_detail。"""
        last_id = 0
        while True:
            rows = connection.execute(
                """
                SELECT id, summary_json, raw_hex FROM frames
                WHERE id > ? AND nid IS NULL ORDER BY id LIMIT ?
                """,
                (last_id, self._BACKFILL_BATCH),
            ).fetchall()
            if not rows:
                return
            updates = []
            for row in rows:
                frm_type = None
                nid = None
                assess_detail = 0
                if row["summary_json"]:
                    parsed = network_assessment._parse_summary_fields(row["summary_json"])
                    nid = parsed["snid_int"]
                    if parsed["frm_type"] != "UNKNOWN":
                        frm_type = parsed["frm_type"]
                    assess_detail = (
                        1 if network_assessment.detail_is_assessable(parsed["detail"]) else 0
                    )
                if nid is None and row["raw_hex"]:
                    frame = network_assessment._decode_frame(row["raw_hex"])
                    nid = frame["nid"] if frame else None
                updates.append((nid, frm_type, assess_detail, row["id"]))
            connection.executemany(
                "UPDATE frames SET nid = ?, frm_type = ?, assess_detail = ? WHERE id = ?",
                updates,
            )
            connection.commit()
            last_id = rows[-1]["id"]

    def _backfill_frm_type_passes(self, connection) -> None:
        """补仅有 summary 而缺 FrmType 的行（有 nid 无 frm_type 的残留）。"""
        for _ in range(5):
            rows = connection.execute(
                """
                SELECT id, summary_json FROM frames
                WHERE frm_type IS NULL AND summary_json IS NOT NULL LIMIT ?
                """,
                (self._BACKFILL_BATCH,),
            ).fetchall()
            if not rows:
                return
            updates = []
            for row in rows:
                parsed = network_assessment._parse_summary_fields(row["summary_json"])
                if parsed["frm_type"] == "UNKNOWN":
                    continue
                updates.append((parsed["frm_type"], row["id"]))
            if not updates:
                return
            connection.executemany(
                "UPDATE frames SET frm_type = ? WHERE id = ?", updates
            )
            connection.commit()

    def _backfill_assess_detail_pass(self, connection) -> None:
        """补 assess_detail 残留（此前只回填过 nid/frm_type 的库）。"""
        for _ in range(5):
            rows = connection.execute(
                """
                SELECT id, summary_json FROM frames
                WHERE assess_detail IS NULL AND summary_json IS NOT NULL LIMIT ?
                """,
                (self._BACKFILL_BATCH,),
            ).fetchall()
            if not rows:
                return
            updates = []
            for row in rows:
                parsed = network_assessment._parse_summary_fields(row["summary_json"])
                updates.append(
                    (1 if network_assessment.detail_is_assessable(parsed["detail"]) else 0,
                     row["id"])
                )
            if not updates:
                return
            connection.executemany(
                "UPDATE frames SET assess_detail = ? WHERE id = ?", updates
            )
            connection.commit()

    def _count_pending_analysis(self) -> int:
        """待回填行数：nid 未解析，或有 summary 但 assess_detail 未标注。"""
        with self._connect() as connection:
            a = connection.execute(
                "SELECT COUNT(*) FROM frames WHERE nid IS NULL"
            ).fetchone()[0]
            b = connection.execute(
                "SELECT COUNT(*) FROM frames "
                "WHERE assess_detail IS NULL AND summary_json IS NOT NULL"
            ).fetchone()[0]
            return (a or 0) + (b or 0)

    def _sql_path_ready(self, start_time="", end_time="") -> bool:
        """SQL 聚合快路径可用性：物化列就绪（回填完成或无待回填行）且窗口内
        frm_type 覆盖率达标（summary 缺失为主的库退回 Python 解码路径）。"""
        state = _BACKFILL_STATE.get(str(self.database_path), {})
        if state.get("running"):
            return False
        if self._count_pending_analysis() and not state.get("done"):
            return False
        conditions, parameters = self._frames_where(start_time, end_time)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*), COUNT(frm_type) FROM frames {self._where_sql(conditions)}",
                parameters,
            ).fetchone()
        total = row[0] or 0
        if not total:
            return False
        return (row[1] or 0) / total >= self.SQL_PATH_MIN_COVERAGE

    # -- 评估行源（全量，无抽样上限）---------------------------------------

    def _iter_full_frame_rows(self, start_time="", end_time=""):
        """Python 解码路径行源：全量流式输出帧（含 raw_hex/summary_json）。"""
        conditions, parameters = self._frames_where(start_time, end_time)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                SELECT id, log_time, raw_hex, summary_json FROM frames
                {self._where_sql(conditions)}
                ORDER BY id
                """,
                parameters,
            )
            for row in cursor:
                yield {
                    "_id": row["id"],
                    "log_time": row["log_time"],
                    "raw_hex": row["raw_hex"],
                    "summary_json": row["summary_json"],
                }

    def _agg_frm_counts(self, start_time="", end_time="", nid=None):
        """库内预聚合：按（NID, 帧型）计数的行迭代 (nid, frm_type, cnt)。"""
        conditions, parameters = self._frames_where(start_time, end_time)
        extra = "nid = ?" if nid is not None else "nid IS NOT NULL"
        params = [*parameters, nid] if nid is not None else list(parameters)
        with self._connect() as connection:
            for row in connection.execute(
                f"""
                SELECT nid, frm_type, COUNT(*) AS cnt FROM frames
                {self._where_sql(conditions, extra)}
                GROUP BY nid, frm_type
                """,
                params,
            ):
                yield row["nid"], row["frm_type"], row["cnt"]

    def _agg_detail_rows(self, start_time="", end_time="", nid=None):
        """携带非空 Detail 的行（partial 索引直取，量小）：B/C 档与信标参数来源。"""
        conditions, parameters = self._frames_where(start_time, end_time)
        extra = "assess_detail = 1"
        if nid is not None:
            extra = f"({extra} AND nid = ?)"
            parameters = [*parameters, nid]
        with self._connect() as connection:
            for row in connection.execute(
                f"""
                SELECT log_time, nid, frm_type, raw_hex, summary_json FROM frames
                {self._where_sql(conditions, extra)} ORDER BY id
                """,
                parameters,
            ):
                yield {
                    "log_time": row["log_time"],
                    "nid": row["nid"],
                    "frm_type": row["frm_type"],
                    "raw_hex": row["raw_hex"],
                    "summary_json": row["summary_json"],
                }

    def _agg_central_rows(self, start_time="", end_time="", nid=None):
        """中央信标别名行（量小）：Detail 缺失时的周期计数样本/CCO MAC 兜底。

        只取无 Detail 的别名行——带 Detail 的已由 _agg_detail_rows 覆盖，
        避免重复解码。"""
        aliases = list(network_assessment.FRMTYPE_CENTRAL_BEACON_ALIASES)
        placeholders = ", ".join("?" for _ in aliases)
        conditions, parameters = self._frames_where(start_time, end_time)
        extra = f"(frm_type IN ({placeholders}) AND (assess_detail IS NULL OR assess_detail = 0))"
        if nid is not None:
            extra = f"({extra} AND nid = ?)"
            parameters = [*parameters, nid]
        with self._connect() as connection:
            for row in connection.execute(
                f"""
                SELECT log_time, raw_hex FROM frames
                {self._where_sql(conditions, extra)} ORDER BY id
                """,
                [*parameters, *aliases],
            ):
                yield {"log_time": row["log_time"], "raw_hex": row["raw_hex"]}

    def _agg_bucket_rows(self, start_time="", end_time="", nid=None, period_ms=0):
        """库内按信标周期预分桶（SQLite ≥3.25 窗口函数处理跨天）：
        迭代 (nid, frm_type, bucket, cnt) 聚合行，行数 = 桶数×帧型数。"""
        if not period_ms or period_ms <= 0:
            return
        conditions, parameters = self._frames_where(start_time, end_time)
        extra = "nid IS NOT NULL"
        params = list(parameters)
        if nid is not None:
            extra = "nid = ?"
            params = [*parameters, nid]
        where_sql = self._where_sql(conditions, extra)
        # period_ms 经 int() 消毒后内联：SELECT 表达式中的占位符按文本序先于
        # FROM 中的 ?，混用命名/位置参数易错，内联最稳妥。
        sql = f"""
            WITH tl AS (
                SELECT id, nid, frm_type,
                    (CAST(substr(log_time, 1, 2) AS INTEGER) * 3600
                     + CAST(substr(log_time, 4, 2) AS INTEGER) * 60
                     + CAST(substr(log_time, 7, 2) AS INTEGER)) * 1000
                     + CAST(substr(log_time, 9, 3) AS INTEGER) AS clock_ms
                FROM frames {where_sql}
            ),
            marked AS (
                SELECT id, nid, frm_type, clock_ms,
                    CASE WHEN clock_ms < LAG(clock_ms) OVER (ORDER BY id) - 43200000
                         THEN 1 ELSE 0 END AS rollover
                FROM tl
            ),
            days AS (
                SELECT nid, frm_type, clock_ms,
                    COALESCE(SUM(rollover) OVER (ORDER BY id
                                 ROWS UNBOUNDED PRECEDING), 0) AS day_offset
                FROM marked
            )
            SELECT nid, frm_type,
                day_offset * 86400000 + clock_ms
                    - ((day_offset * 86400000 + clock_ms) % {int(period_ms)}) AS bucket,
                COUNT(*) AS cnt
            FROM days GROUP BY nid, frm_type, bucket
        """
        with self._connect() as connection:
            connection.execute("PRAGMA temp_store = MEMORY")
            for row in connection.execute(sql, params):
                yield row["nid"], row["frm_type"], row["bucket"], row["cnt"]

    def _sql_row_totals(self, start_time="", end_time="", nid=None) -> tuple:
        """SQL 路径的精确总数：（窗口总帧数, NID 无法识别帧数）。"""
        conditions, parameters = self._frames_where(start_time, end_time)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM frames {self._where_sql(conditions)}", parameters
            ).fetchone()
            if nid is not None:
                extra, params2 = "nid = ?", [*parameters, nid]
            else:
                extra, params2 = "nid IS NULL", list(parameters)
            row2 = connection.execute(
                f"SELECT COUNT(*) FROM frames {self._where_sql(conditions, extra)}",
                params2,
            ).fetchone()
        return row[0] or 0, row2[0] or 0

    def _iter_assessment_records(self, start_time="", end_time=""):
        """分钟上报记录行源（全量流式）：nid 取物化列，缺失回退 SNID 解析。"""
        conditions, parameters = [], []
        self._append_time_range(
            conditions, parameters, start_time, end_time, "minute_reports.log_time"
        )
        where_sql = self._where_sql(conditions)
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                SELECT minute_reports.time_seconds AS time_seconds,
                       minute_reports.station_key AS station_key,
                       minute_reports.response_result AS response_result,
                       minute_reports.report_count AS report_count,
                       minute_reports.application_error AS application_error,
                       frames.nid AS nid, frames.summary_json AS summary_json
                FROM minute_reports
                LEFT JOIN frames ON frames.id = minute_reports.frame_id
                {where_sql}
                ORDER BY minute_reports.id
                """,
                parameters,
            )
            for row in cursor:
                nid = row["nid"]
                if nid is None:
                    nid = self._nid_from_summary(row["summary_json"])
                yield {
                    "time_seconds": row["time_seconds"],
                    "station_key": row["station_key"],
                    "response_result": row["response_result"],
                    "report_count": row["report_count"],
                    "application_error": row["application_error"],
                    "nid": nid,
                }

    def list_beacon_periods(
        self, index_id=None, start_time="", end_time="", nid="",
    ) -> dict:
        """按网络隔离扫描中央信标周期并分桶评估网络承载能力（全量分析）。

        全部帧参与统计（无抽样上限），网络按 NID（组网序列号）严格隔离，
        NID 无法识别的帧不混入任何网络。稳定性 ≥2h 门禁使用日志会话权威
        时长（首尾帧跨度），与统计覆盖面解耦。

        数据路径：
          - sql     物化列（nid/frm_type）就绪时：时间线走 SQL 轻量列 +
                    Detail 行归并流，评估全程零解码/零 json 解析；
          - python  物化列未就绪时：全量流式解码，同时触发后台回填，
                    回填完成后下次评估自动切换 sql 路径。

        返回 assess_by_network_stream 结构；结果按（库, 时间窗, NID, 窗口内
        最大帧 id）缓存，串口实时追加新帧后自然失效。
        """
        service = self.open_index(index_id) if index_id else self
        nid_value = service.resolve_assessment_nid(nid, start_time, end_time)
        max_id = service._frames_max_id(start_time, end_time)
        cache_key = (
            str(service.database_path), str(start_time), str(end_time),
            nid_value, max_id,
        )
        cached = _ASSESSMENT_CACHE.get(cache_key)
        if cached is not None:
            return cached

        session_duration_s = service._session_duration_s(start_time, end_time)
        records = list(service._iter_assessment_records(start_time, end_time))
        if service._sql_path_ready(start_time, end_time):
            frame_total, unassigned_total = service._sql_row_totals(
                start_time, end_time, nid_value
            )
            result = network_assessment.assess_by_network_aggregate(
                service._agg_frm_counts(start_time, end_time, nid_value),
                service._agg_detail_rows(start_time, end_time, nid_value),
                central_rows=service._agg_central_rows(start_time, end_time, nid_value),
                bucket_rows_fn=lambda period: service._agg_bucket_rows(
                    start_time, end_time, nid_value, period
                ),
                records=records,
                session_duration_s=session_duration_s,
                nid_filter=nid_value,
                engine="sql",
                frame_total=frame_total,
                unassigned_total=unassigned_total,
            )
        else:
            result = network_assessment.assess_by_network_stream(
                service._iter_full_frame_rows(start_time, end_time),
                records,
                session_duration_s=session_duration_s,
                nid_filter=nid_value,
                engine="python",
            )
            service.request_backfill()

        if len(_ASSESSMENT_CACHE) >= _ASSESSMENT_CACHE_MAX:
            _ASSESSMENT_CACHE.pop(next(iter(_ASSESSMENT_CACHE)))
        _ASSESSMENT_CACHE[cache_key] = result
        return result

    def list_indexes(self) -> dict:
        if self._index_registry is None:
            return {"current_index_id": self._index_id, "indexes": []}
        return {
            "current_index_id": self._index_registry.current_index_id(),
            "indexes": self._index_registry.list_indexes(),
        }

    def list_index_frames(self, index_id: str, **filters) -> dict:
        page = self.open_index(index_id).list_frames(**filters)
        page["index_id"] = index_id
        return page

    def get_index_frame(self, index_id: str, frame_id: int) -> dict:
        frame = self.open_index(index_id).get_frame(frame_id)
        frame["index_id"] = index_id
        frame["frame_id"] = frame_id
        return frame

    def get_frame(self, frame_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, sequence, log_time, byte_length, raw_hex,
                       summary_json, parse_error
                FROM frames WHERE id = ?
                """,
                (frame_id,),
            ).fetchone()
        if row is None:
            raise KeyError(frame_id)

        try:
            if self.parser is None:
                raise RuntimeError("解析库不可用（当前环境未提供 GwHPLCAnalysis.dll）")
            analysis = self.parser.parse(row["raw_hex"])
        except Exception as exc:
            # 详情解析失败时保留原始帧数据，返回错误信息而非抛异常
            return {
                "id": row["id"],
                "index_id": self._index_id,
                "frame_id": row["id"],
                "sequence": row["sequence"],
                "log_time": row["log_time"],
                "byte_length": row["byte_length"],
                "raw_hex": row["raw_hex"],
                "summary": json.loads(row["summary_json"] or "{}"),
                "parse_error": row["parse_error"] or str(exc),
                "analysis": {
                    "parse_error": f"完整解析失败：{exc}",
                    "simple": {},
                    "full": {},
                },
            }

        return {
            "id": row["id"],
            "index_id": self._index_id,
            "frame_id": row["id"],
            "sequence": row["sequence"],
            "log_time": row["log_time"],
            "byte_length": row["byte_length"],
            "raw_hex": row["raw_hex"],
            "summary": json.loads(row["summary_json"] or "{}"),
            "parse_error": row["parse_error"],
            "analysis": analysis,
        }

    def append_frame(self, sequence, log_time, hex_frame, minute_state=None):
        """追加单帧到索引（供串口实时采集复用）。

        minute_state: 可变的 dict，携带 minute_day_offset / minute_prev_abs_ms，
        用于跨多次追加保持分钟上报的日偏移推导。为 None 时不做分钟入库。
        返回 (frame_id, simple_dict)；解析失败时 simple 为空 dict。
        """
        results = self.append_frames(
            [(sequence, log_time, hex_frame)], minute_state=minute_state
        )
        return results[0]

    def append_frames(self, records, minute_state=None) -> list:
        """批量追加多帧（单连接单事务），避免逐帧连接/逐帧提交。

        records: 可迭代的 (sequence, log_time, hex_frame) 三元组；
        minute_state: 与 append_frame 相同语义，批量内共享日偏移推导。
        返回 [(frame_id, simple_dict), ...]，顺序与 records 一致。
        """
        results = []
        insert_sql = """
            INSERT INTO frames (
                sequence, log_time, byte_length, raw_hex, summary_json, parse_error
            ) VALUES (?, ?, ?, ?, ?, ?)
        """
        insert_minute_sql = """
            INSERT INTO minute_reports (
                frame_id, log_time, time_seconds, cco_tei, station_key,
                source_mac, source_tei, task_no, protocol_type, meter_type,
                response_result, freeze_time, report_count, data_length,
                application_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._connect() as connection:
            for sequence, log_time, hex_frame in records:
                simple = {}
                summary_json = None
                parse_error = None
                if self.parser is not None:
                    try:
                        parsed = self.parser.parse_summary(hex_frame)
                        simple = parsed.get("simple", {})
                        summary_json = json.dumps(simple, ensure_ascii=False)
                    except Exception as exc:
                        parse_error = str(exc)

                cursor = connection.execute(
                    insert_sql,
                    (
                        sequence,
                        log_time,
                        len(hex_frame.split()),
                        hex_frame,
                        summary_json,
                        parse_error,
                    ),
                )
                frame_id = cursor.lastrowid
                results.append((frame_id, simple))

                if minute_state is not None and simple.get("APP_ID") == "00E4":
                    record = LogRecord(sequence, log_time, hex_frame)
                    day_offset = minute_state.setdefault("day_offset", 0)
                    prev_abs_ms = minute_state.get("prev_abs_ms")
                    day_offset, prev_abs_ms = self._insert_minute_report(
                        connection, insert_minute_sql, frame_id, record, simple,
                        day_offset, prev_abs_ms,
                    )
                    minute_state["day_offset"] = day_offset
                    minute_state["prev_abs_ms"] = prev_abs_ms
            connection.commit()
        return results

    def close(self) -> None:
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)
