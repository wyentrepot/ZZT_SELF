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
                "CREATE INDEX IF NOT EXISTS idx_frames_sequence ON frames(sequence)"
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

        with source.open("rb") as stream, self._connect() as connection:
            insert_sql = """
                INSERT INTO frames (
                    sequence, log_time, byte_length, raw_hex, summary_json, parse_error
                ) VALUES (?, ?, ?, ?, ?, ?)
            """
            for line in stream:
                line_count += 1
                bytes_read += len(line)
                record = extract_log_record(line)
                if record is None:
                    continue

                summary_json = None
                parse_error = None
                try:
                    parsed = self.parser.parse_summary(record.hex_frame)
                    summary_json = json.dumps(
                        parsed.get("simple", {}), ensure_ascii=False
                    )
                except Exception as exc:
                    error_count += 1
                    parse_error = str(exc)

                connection.execute(
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

    def list_frames(self, offset=0, limit=100, query="") -> dict:
        if offset < 0:
            raise ValueError("offset 不能小于 0")
        if limit < 1 or limit > self.MAX_PAGE_SIZE:
            raise ValueError(f"limit 必须在 1 到 {self.MAX_PAGE_SIZE} 之间")

        where_sql = ""
        parameters = []
        if query:
            where_sql = """
                WHERE sequence LIKE ? OR log_time LIKE ? OR summary_json LIKE ?
            """
            wildcard = f"%{query}%"
            parameters.extend([wildcard, wildcard, wildcard])

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
