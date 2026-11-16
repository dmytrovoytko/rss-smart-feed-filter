from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.timeutil import to_utc_iso, utc_now_iso

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("observability_schema.sql")

_TRACE_ID: ContextVar[str | None] = ContextVar("observability_trace_id", default=None)
_STORE: ContextVar[Any] = ContextVar("observability_store", default=None)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

log = logging.getLogger(__name__)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str | None:
    return _TRACE_ID.get()


def set_trace_id(trace_id: str | None) -> Token[str | None]:
    return _TRACE_ID.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    _TRACE_ID.reset(token)


def current_observability_store() -> ObservabilityStore | None:
    store = _STORE.get()
    return store if isinstance(store, ObservabilityStore) else None


def set_observability_store(store: ObservabilityStore | None) -> Token[Any]:
    return _STORE.set(store)


def reset_observability_store(token: Token[Any]) -> None:
    _STORE.reset(token)


def sanitize_text(value: object, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    text = _URL_RE.sub("[url]", text)
    return text[:limit]


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    path = str(db_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def open_observability_db(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = _connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_observability_db(db_path: str | Path) -> None:
    with open_observability_db(db_path) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"observability schema version {version} is newer than this app "
                f"expects ({SCHEMA_VERSION})"
            )
        if version < SCHEMA_VERSION:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()


class ObservabilityStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def init(self) -> None:
        init_observability_db(self.db_path)

    def _write(self, sql: str, params: tuple[Any, ...]) -> None:
        try:
            self._execute(sql, params)
        except sqlite3.Error:
            try:
                self.init()
                self._execute(sql, params)
            except Exception:
                log.warning("observability write failed", exc_info=True)
        except Exception:
            log.warning("observability write failed", exc_info=True)

    def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        with open_observability_db(self.db_path) as conn:
            conn.execute(sql, params)
            conn.commit()

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        try:
            with open_observability_db(self.db_path) as conn:
                return [dict(row) for row in conn.execute(sql, params)]
        except Exception:
            log.warning("observability query failed", exc_info=True)
            return []

    def start_trace(
        self,
        trace_id: str,
        *,
        kind: str,
        name: str,
        profile_id: str | None = None,
        job_id: int | None = None,
        started_at: str | None = None,
    ) -> None:
        self._write(
            """
            INSERT OR REPLACE INTO traces (
                trace_id, kind, name, profile_id, job_id, started_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                trace_id,
                kind,
                name,
                profile_id,
                job_id,
                started_at or utc_now_iso(),
            ),
        )

    def finish_trace(
        self,
        trace_id: str,
        *,
        status: str = "ok",
        error: object | None = None,
        duration_ms: float | None = None,
        finished_at: str | None = None,
    ) -> None:
        self._write(
            """
            UPDATE traces
            SET finished_at = ?, duration_ms = ?, status = ?, error = ?
            WHERE trace_id = ?
            """,
            (
                finished_at or utc_now_iso(),
                duration_ms,
                status,
                sanitize_text(error) if error is not None else None,
                trace_id,
            ),
        )

    def record_span(
        self,
        trace_id: str,
        name: str,
        *,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_ms: float | None = None,
        status: str = "ok",
        attributes: dict[str, Any] | None = None,
        error: object | None = None,
    ) -> str:
        span_id = span_id or uuid.uuid4().hex
        self._write(
            """
            INSERT OR REPLACE INTO spans (
                span_id, trace_id, parent_span_id, name, started_at, finished_at,
                duration_ms, status, attributes_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                span_id,
                trace_id,
                parent_span_id,
                name,
                started_at or utc_now_iso(),
                finished_at,
                duration_ms,
                status,
                _json(attributes),
                sanitize_text(error) if error is not None else None,
            ),
        )
        return span_id

    @contextmanager
    def span(
        self,
        trace_id: str,
        name: str,
        *,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        span_id = uuid.uuid4().hex
        started = time.perf_counter()
        started_at = to_utc_iso(datetime.now(UTC))
        try:
            yield span_id
        except Exception as exc:
            self.record_span(
                trace_id,
                name,
                span_id=span_id,
                parent_span_id=parent_span_id,
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                status="error",
                attributes=attributes,
                error=exc,
            )
            raise
        else:
            self.record_span(
                trace_id,
                name,
                span_id=span_id,
                parent_span_id=parent_span_id,
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
                status="ok",
                attributes=attributes,
            )

    def record_metric(
        self,
        trace_id: str | None,
        name: str,
        value: float | int,
        *,
        unit: str | None = None,
        labels: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        self._write(
            """
            INSERT INTO metrics (trace_id, name, value, unit, labels_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                name,
                float(value),
                unit,
                _json(labels),
                created_at or utc_now_iso(),
            ),
        )

    def record_log(
        self,
        trace_id: str | None,
        level: str,
        event: str,
        *,
        fields: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        self._write(
            """
            INSERT INTO logs (trace_id, level, event, fields_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                level.upper(),
                event,
                _json(fields),
                created_at or utc_now_iso(),
            ),
        )

    def list_traces(
        self,
        *,
        limit: int = 50,
        kind: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        return self._query(
            f"""
            SELECT * FROM traces
            {where}
            ORDER BY started_at DESC, trace_id
            LIMIT ?
            """,
            tuple(params),
        )

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT * FROM traces WHERE trace_id = ?",
            (trace_id,),
        )
        return rows[0] if rows else None

    def list_spans(self, trace_id: str) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT * FROM spans
            WHERE trace_id = ?
            ORDER BY started_at, span_id
            """,
            (trace_id,),
        )

    def list_metrics(self, trace_id: str) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT * FROM metrics
            WHERE trace_id = ?
            ORDER BY created_at, id
            """,
            (trace_id,),
        )

    def list_logs(self, trace_id: str) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT * FROM logs
            WHERE trace_id = ?
            ORDER BY created_at, id
            """,
            (trace_id,),
        )

    def list_alerts(
        self,
        *,
        limit: int = 50,
        profile_id: str | None = None,
        open_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if profile_id:
            clauses.append("profile_id = ?")
            params.append(profile_id)
        if open_only:
            clauses.append("acknowledged_at IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 500)))
        return self._query(
            f"""
            SELECT * FROM alerts
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        )

    def summary(self) -> dict[str, int]:
        try:
            with open_observability_db(self.db_path) as conn:
                row = conn.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM traces) AS traces,
                        (SELECT COUNT(*) FROM traces WHERE status = 'ok') AS traces_ok,
                        (SELECT COUNT(*) FROM traces WHERE status = 'error') AS traces_error,
                        (SELECT COUNT(*) FROM traces WHERE status = 'running') AS traces_running,
                        (SELECT COUNT(*) FROM spans) AS spans,
                        (SELECT COUNT(*) FROM metrics) AS metrics,
                        (SELECT COUNT(*) FROM logs) AS logs,
                        (SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL)
                            AS open_alerts
                    """
                ).fetchone()
                return {
                    key: int(row[key] or 0)
                    for key in (
                        "traces",
                        "traces_ok",
                        "traces_error",
                        "traces_running",
                        "spans",
                        "metrics",
                        "logs",
                        "open_alerts",
                    )
                }
        except Exception:
            log.warning("observability summary failed", exc_info=True)
            return {
                key: 0
                for key in (
                    "traces",
                    "traces_ok",
                    "traces_error",
                    "traces_running",
                    "spans",
                    "metrics",
                    "logs",
                    "open_alerts",
                )
            }

    def raise_alert(
        self,
        *,
        profile_id: str | None,
        code: str,
        severity: str,
        message: str,
        trace_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        timestamp = created_at or utc_now_iso()
        cleaned = sanitize_text(message, 1000)

        def write() -> None:
            with open_observability_db(self.db_path) as conn:
                existing = conn.execute(
                    """
                    SELECT id FROM alerts
                    WHERE profile_id IS ? AND code = ? AND acknowledged_at IS NULL
                    ORDER BY id
                    LIMIT 1
                    """,
                    (profile_id, code),
                ).fetchone()
                if existing is not None:
                    conn.execute(
                        """
                        UPDATE alerts
                        SET severity = ?, message = ?, trace_id = ?, created_at = ?
                        WHERE id = ?
                        """,
                        (severity, cleaned, trace_id, timestamp, existing["id"]),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO alerts (
                            profile_id, code, severity, message, trace_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (profile_id, code, severity, cleaned, trace_id, timestamp),
                    )
                conn.commit()

        try:
            write()
        except sqlite3.Error:
            try:
                self.init()
                write()
            except Exception:
                log.warning("observability alert write failed", exc_info=True)
        except Exception:
            log.warning("observability alert write failed", exc_info=True)

    def acknowledge_alert(
        self,
        alert_id: int,
        *,
        acknowledged_at: str | None = None,
    ) -> bool:
        try:
            with open_observability_db(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE alerts
                    SET acknowledged_at = ?
                    WHERE id = ? AND acknowledged_at IS NULL
                    """,
                    (acknowledged_at or utc_now_iso(), alert_id),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception:
            log.warning("observability alert acknowledgement failed", exc_info=True)
            return False

    def open_alert_count(self, profile_id: str | None = None) -> int:
        try:
            with open_observability_db(self.db_path) as conn:
                if profile_id:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM alerts "
                        "WHERE acknowledged_at IS NULL AND profile_id = ?",
                        (profile_id,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL"
                    ).fetchone()
                return int(row[0])
        except Exception:
            log.warning("observability alert count failed", exc_info=True)
            return 0

    def health(self) -> dict[str, Any]:
        try:
            with open_observability_db(self.db_path) as conn:
                conn.execute("SELECT 1").fetchone()
                count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL"
                    ).fetchone()[0]
                )
            return {"status": "ok", "open_alerts": count}
        except Exception:
            log.warning("observability health check failed", exc_info=True)
            return {"status": "unavailable", "open_alerts": 0}
