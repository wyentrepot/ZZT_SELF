import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol


HEADER_PATTERN = re.compile(
    r"^\[(?P<sequence>[^\]]+)\]\[(?P<time>[^\]]+)\](?P<payload>.*)$"
)
HEX_BYTE_PATTERN = re.compile(r"(?i)\b[0-9a-f]{2}\b")


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

    def __init__(self, parser: FrameParserService, database_path: Path):
        self.parser = parser
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._status_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._status = self._empty_status()
        self._initialize_database(reset=False)

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
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
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
                    parse_error TEXT
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_minute_reports_cco "
                "ON minute_reports(cco_tei)"
            )

    def _replace_status(self, **values) -> dict:
        with self._status_lock:
            self._status.update(values)
            return dict(self._status)

    def status(self) -> dict:
        with self._status_lock:
            return dict(self._status)

    def start_index(self, path) -> dict:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"日志文件不存在：{source}")
        if self._worker and self._worker.is_alive():
            raise RuntimeError("当前日志仍在建立索引，请完成后再加载新文件")

        file_size = source.stat().st_size
        self._status = self._empty_status()
        self._replace_status(
            state="queued",
            source_path=str(source),
            file_size=file_size,
            message="等待开始建立索引",
        )
        self._worker = threading.Thread(
            target=self._index_worker,
            args=(source,),
            name="hplc-log-indexer",
            daemon=True,
        )
        self._worker.start()
        return self.status()

    def _index_worker(self, source: Path) -> None:
        try:
            self.index_file(source)
        except Exception as exc:
            self._replace_status(state="failed", message=str(exc))

    def index_file(self, path) -> dict:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"日志文件不存在：{source}")

        file_size = source.stat().st_size
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
            for line in stream:
                line_count += 1
                bytes_read += len(line)
                record = extract_log_record(line)
                if record is None:
                    continue

                summary_json = None
                parse_error = None
                simple = {}
                try:
                    parsed = self.parser.parse_summary(record.hex_frame)
                    simple = parsed.get("simple", {})
                    summary_json = json.dumps(simple, ensure_ascii=False)
                except Exception as exc:
                    error_count += 1
                    parse_error = str(exc)

                cursor = connection.execute(
                    insert_sql,
                    (
                        record.sequence,
                        record.log_time,
                        len(record.hex_frame.split()),
                        record.hex_frame,
                        summary_json,
                        parse_error,
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

    def list_task_minute_periods(self, task_no, period_minutes=None, cco_tei="001", nid=""):
        """按每个 STA 实际启用到删除成功之间的周期汇总分钟采集上报。"""
        task = self._task_key(task_no)
        if period_minutes is not None and (
            not isinstance(period_minutes, int) or not 1 <= period_minutes <= 1440
        ):
            raise ValueError("period_minutes 必须在 1 到 1440 之间")

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

        conditions = ["minute_reports.cco_tei = ?", "minute_reports.task_no = ?"]
        parameters = [cco_tei.upper(), int(task)]
        if nid.strip():
            conditions.append("frames.summary_json LIKE ? ESCAPE '\\'")
            parameters.append(self._nid_like(nid))
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
            minutes = (config or {}).get("period_minutes") or period_minutes
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
                "freeze_time": freeze, "expected_freeze_time": expected_clock,
                "freeze_ok": freeze.endswith(expected_clock[:8]),
                "period_minutes": int(minutes),
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
            # 一个 STA 在同一窗口的多帧只算一次应答，但明细全部保留。
            missing = sorted(set(expected) - received)
            periods.append({
                "period_start": start,
                "period_end": start + max([item["period_minutes"] for item in reports] + [config["period_minutes"] for config in expected.values()] + [period_minutes or 1]) * 60_000,
                "description": self._clock_text(start),
                "report_count": len(reports),
                "received_sta_count": len(received),
                "expected_count": len(expected) if source == "configured" else None,
                "missing_stas": missing if source == "configured" else [],
                "freeze_ok_count": sum(item["freeze_ok"] for item in reports),
                "freeze_error_count": sum(not item["freeze_ok"] for item in reports),
                "reports": reports,
            })
        return {
            "task_no": task, "source": source, "periods": periods,
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

    def _e2_records(self, cco_tei: str, nid: str = ""):
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

            if finl_d == cco:
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
            elif ori_s == cco and fields.get("启动/删除标志") in {"删除", "启用"}:
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

    def _task_config_records(self, cco_tei: str, nid: str = ""):
        """产出带绝对日志时间的 00E2 任务配置记录。"""
        day_offset = 0
        previous_ms = None
        for kind, record in self._e2_records(cco_tei, nid):
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

    def list_task_config_numbers(self, cco_tei: str, nid: str = "") -> list[str]:
        """返回当前范围内下发或上报过的任务号。"""
        task_numbers = {
            record["task_no"]
            for _, record in self._task_config_records(cco_tei, nid)
            if record["task_no"]
        }
        return sorted(task_numbers, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))

    def task_config_summary(self, cco_tei: str, task_no: str, nid: str = "") -> dict:
        """按任务号汇总 STA 下发及应答状态。"""
        requested_task = self._task_key(task_no)
        down_by_sta = {}
        up_by_sta = {}
        observed_ms = None
        for kind, record in self._task_config_records(cco_tei, nid):
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
            rows.append({
                "mac": mac,
                "operation": down["operation"] if down else (latest_reply or {}).get("del_flag", ""),
                "sent_time": down["log_time"] if down else "",
                "reply_time": (latest_reply or {}).get("log_time", ""),
                "status": status,
                "sequence": down["seq"] if down else (latest_reply or {}).get("seq", ""),
                "app_raw": (down or latest_reply or {}).get("app_raw", ""),
                "frame_id": (down or latest_reply or {}).get("frame_id"),
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

    def list_frames(self, offset=0, limit=100, query="", nid="") -> dict:
        if offset < 0:
            raise ValueError("offset 不能小于 0")
        if limit < 1 or limit > self.MAX_PAGE_SIZE:
            raise ValueError(f"limit 必须在 1 到 {self.MAX_PAGE_SIZE} 之间")

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
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM frames {where_sql}", parameters
            ).fetchone()[0]
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

        items = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "sequence": row["sequence"],
                    "log_time": row["log_time"],
                    "byte_length": row["byte_length"],
                    "summary": json.loads(row["summary_json"] or "{}"),
                    "parse_error": row["parse_error"],
                }
            )
        return {"items": items, "offset": offset, "limit": limit, "total": total}

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

        analysis = self.parser.parse(row["raw_hex"])
        return {
            "id": row["id"],
            "sequence": row["sequence"],
            "log_time": row["log_time"],
            "byte_length": row["byte_length"],
            "raw_hex": row["raw_hex"],
            "summary": json.loads(row["summary_json"] or "{}"),
            "parse_error": row["parse_error"],
            "analysis": analysis,
        }

    def close(self) -> None:
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)
