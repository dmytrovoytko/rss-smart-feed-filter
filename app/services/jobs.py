from __future__ import annotations

import json
import logging
import queue
import re
import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from app.config import Settings
from app.db import open_db
from app.services.classify import classify_pending_items
from app.services.feeds import FeedError, refresh_profile
from app.timeutil import utc_now_iso

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running", "preview")
JOB_STATUSES = {"queued", "running", "preview", "done", "failed"}
JOB_MODES = {"refresh", "classify"}
FETCH_PROGRESS_SHARE = 20

ProgressCallback = Callable[[str, int, int], None]
StopCheck = Callable[[], bool]
JobHandler = Callable[
    [sqlite3.Connection, Settings, int, str, ProgressCallback, StopCheck],
    dict[str, Any] | None,
]


class JobNotFound(RuntimeError):
    pass


class JobConflict(RuntimeError):
    pass


def job_progress(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "")
    mode = str(job.get("mode") or "refresh")
    phase = job.get("phase")
    processed = int(job.get("processed") or 0)
    total = int(job.get("total") or 0)

    if status == "done":
        return {"percent": 100, "label": "Done", "detail": "Complete"}
    if status == "failed":
        return {"percent": 0, "label": "Failed", "detail": "Stopped"}
    if status == "queued" or not phase:
        return {"percent": 0, "label": "Queued", "detail": "Waiting to start"}

    if phase == "fetching":
        if total:
            percent = round(FETCH_PROGRESS_SHARE * processed / total)
            detail = (
                f"{processed} of {total} source"
                f"{'s' if total != 1 else ''} checked"
            )
            label = f"Fetching {processed}/{total}"
        else:
            percent = 0
            detail = "Fetching sources"
            label = "Fetching sources"
    elif phase == "classifying":
        if total:
            if mode == "classify":
                percent = round(100 * processed / total)
            else:
                percent = FETCH_PROGRESS_SHARE + round(
                    (100 - FETCH_PROGRESS_SHARE) * processed / total
                )
            detail = (
                f"{processed} of {total} item"
                f"{'s' if total != 1 else ''} classified"
            )
            label = f"Classifying {processed}/{total}"
        else:
            percent = 99 if mode == "classify" else FETCH_PROGRESS_SHARE
            detail = "Classifying items"
            label = "Classifying"
    else:
        percent = 0
        detail = "Working"
        label = "Working"

    return {
        "percent": max(0, min(99, int(percent))),
        "label": label,
        "detail": detail,
    }


def _job_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    job = dict(row)
    result_json = job.pop("result_json", None)
    job["result"] = None
    if result_json:
        try:
            job["result"] = json.loads(result_json)
        except (TypeError, ValueError):
            job["result"] = None
    job["progress"] = job_progress(job)
    return job


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _job_from_row(row)


def latest_job(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE profile_id = ? ORDER BY id DESC LIMIT 1",
        (profile_id,),
    ).fetchone()
    return _job_from_row(row)


def active_job(
    conn: sqlite3.Connection,
    profile_id: str | None = None,
) -> dict[str, Any] | None:
    placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
    if profile_id is None:
        row = conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY id DESC LIMIT 1",
            ACTIVE_STATUSES,
        ).fetchone()
    else:
        row = conn.execute(
            f"""
            SELECT * FROM jobs
            WHERE profile_id = ? AND status IN ({placeholders})
            ORDER BY id DESC LIMIT 1
            """,
            (profile_id, *ACTIVE_STATUSES),
        ).fetchone()
    return _job_from_row(row)


def create_job(
    conn: sqlite3.Connection,
    *,
    kind: str,
    profile_id: str,
    mode: str = "refresh",
    total: int = 0,
) -> int:
    if mode not in JOB_MODES:
        raise ValueError(f"invalid job mode: {mode}")
    now = utc_now_iso()
    cursor = conn.execute(
        """
        INSERT INTO jobs (
            kind, mode, profile_id, status, processed, total, created_at, updated_at
        )
        VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)
        """,
        (kind, mode, profile_id, max(0, total), now, now),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_job(conn: sqlite3.Connection, job_id: int, **fields: Any) -> None:
    allowed = {"status", "phase", "processed", "total", "error", "result_json"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    if "status" in updates and updates["status"] not in JOB_STATUSES:
        raise ValueError(f"invalid job status: {updates['status']}")
    updates["updated_at"] = utc_now_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(
        f"UPDATE jobs SET {assignments} WHERE id = ?",
        (*updates.values(), job_id),
    )


def _job_error_message(exc: BaseException) -> str:
    message = re.sub(r"\s+", " ", str(exc)).strip()
    return (message or exc.__class__.__name__)[:1000]


def _classification_fields(classification: dict[str, Any]) -> dict[str, Any]:
    return {
        "classification": classification,
        "classified_items": classification["classified_items"],
        "classified_categories": classification["classified_categories"],
        "pending_items": classification["pending_items"],
        "pending_after": classification.get(
            "pending_after",
            classification["pending_items"],
        ),
        "unprocessed_items": classification["unprocessed_items"],
        "window_hours": classification.get("window_hours"),
        "max_items_per_feed": classification.get("max_items_per_feed"),
        "classification_model": classification["model"],
        "classification_duration_s": classification["duration_s"],
        "stopped": bool(classification.get("stopped", False)),
    }


def _profile_items_total(conn: sqlite3.Connection, profile_id: str) -> int:
    return int(
        conn.execute(
            """
            SELECT COUNT(*) FROM items i
            JOIN feeds f ON f.id = i.feed_id
            WHERE f.profile_id = ?
            """,
            (profile_id,),
        ).fetchone()[0]
    )


def run_refresh_job(
    conn: sqlite3.Connection,
    settings: Settings,
    job_id: int,
    profile_id: str,
    progress: ProgressCallback,
    should_stop: StopCheck,
) -> dict[str, Any]:
    def fetch_progress(processed: int, total: int) -> None:
        progress("fetching", processed, total)

    result = refresh_profile(
        conn,
        settings,
        profile_id,
        on_progress=fetch_progress,
    )
    result["mode"] = "refresh"
    update_job(
        conn,
        job_id,
        phase="classifying",
        processed=0,
        total=0,
        result_json=json.dumps(result, sort_keys=True),
    )
    conn.commit()

    def classify_progress(processed: int, total: int) -> None:
        progress("classifying", processed, total)

    classification = classify_pending_items(
        conn,
        settings,
        profile_id,
        on_progress=classify_progress,
        should_stop=should_stop,
    )
    fields = _classification_fields(classification)
    for key in ("window_hours", "max_items_per_feed"):
        if fields.get(key) is None and key in result:
            fields[key] = result[key]
    result.update(fields)
    return result


def run_classify_job(
    conn: sqlite3.Connection,
    settings: Settings,
    job_id: int,
    profile_id: str,
    progress: ProgressCallback,
    should_stop: StopCheck,
) -> dict[str, Any]:
    def classify_progress(processed: int, total: int) -> None:
        progress("classifying", processed, total)

    classification = classify_pending_items(
        conn,
        settings,
        profile_id,
        on_progress=classify_progress,
        should_stop=should_stop,
    )
    result = {
        "mode": "classify",
        "errors": [],
        "items_total": _profile_items_total(conn, profile_id),
        "duration_s": classification["duration_s"],
    }
    result.update(_classification_fields(classification))
    return result


class JobRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        refresh_handler: JobHandler | None = None,
        classify_handler: JobHandler | None = None,
    ) -> None:
        self.settings = settings
        self._handlers: dict[str, JobHandler] = {
            "refresh": refresh_handler or run_refresh_job,
        }
        self._classify_handler = classify_handler or run_classify_job
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._stop_events: dict[int, threading.Event] = {}
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._recover_interrupted()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="feedfilter-jobs",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            for event in self._stop_events.values():
                event.set()
        self._stop.set()
        self._queue.put(None)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10)
        self._thread = None

    def request_stop(self, job_id: int) -> bool:
        with self._lock:
            event = self._stop_events.get(job_id)
            if event is None:
                return False
            event.set()
            return True

    def is_stop_requested(self, job_id: int) -> bool:
        with self._lock:
            event = self._stop_events.get(job_id)
            return bool(event and event.is_set())

    def _enqueue(self, profile_id: str, mode: str) -> tuple[int, bool]:
        with self._lock:
            with open_db(self.settings.db_path) as conn:
                existing = active_job(conn)
                if existing is not None:
                    return int(existing["id"]), False
                total = 0
                if mode == "refresh":
                    total = int(
                        conn.execute(
                            """
                            SELECT COUNT(*) FROM feeds
                            WHERE profile_id = ? AND enabled = 1
                            """,
                            (profile_id,),
                        ).fetchone()[0]
                    )
                job_id = create_job(
                    conn,
                    kind="refresh",
                    profile_id=profile_id,
                    mode=mode,
                    total=total,
                )
            self._queue.put(job_id)
            return job_id, True

    def enqueue_refresh(self, profile_id: str) -> tuple[int, bool]:
        return self._enqueue(profile_id, "refresh")

    def enqueue_classify(self, profile_id: str) -> tuple[int, bool]:
        return self._enqueue(profile_id, "classify")

    def enqueue_retry(self, job_id: int) -> tuple[int, bool]:
        with open_db(self.settings.db_path) as conn:
            job = conn.execute(
                "SELECT id, profile_id, status, mode FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if job is None:
            raise JobNotFound(f"unknown job: {job_id}")
        if job["status"] != "failed":
            raise JobConflict("only failed jobs can be retried")
        mode = str(job["mode"] or "refresh")
        if mode not in JOB_MODES:
            mode = "refresh"
        return self._enqueue(str(job["profile_id"]), mode)

    def _recover_interrupted(self) -> None:
        now = utc_now_iso()
        with open_db(self.settings.db_path) as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error = 'interrupted by server restart',
                    updated_at = ?
                WHERE status IN ('queued', 'running', 'preview')
                """,
                (now,),
            )
            conn.commit()

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._queue.get()
            if job_id is None:
                break
            try:
                self._run_job(job_id)
            except Exception:
                log.exception("unexpected job runner error for job %s", job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: int) -> None:
        stop_event = threading.Event()
        with self._lock:
            self._stop_events[job_id] = stop_event
        try:
            self._execute_job(job_id, stop_event)
        finally:
            with self._lock:
                self._stop_events.pop(job_id, None)

    def _execute_job(self, job_id: int, stop_event: threading.Event) -> None:
        with open_db(self.settings.db_path) as conn:
            row = conn.execute(
                "SELECT id, kind, mode, profile_id, status FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                log.warning("job %s no longer exists", job_id)
                return

            if row["status"] != "queued":
                log.warning("job %s skipped in status %s", job_id, row["status"])
                return

            kind = str(row["kind"])
            mode = str(row["mode"] or "refresh")
            profile_id = str(row["profile_id"])
            handler = (
                self._classify_handler
                if mode == "classify"
                else self._handlers.get(kind)
            )
            if handler is None:
                update_job(
                    conn,
                    job_id,
                    status="failed",
                    error=f"unsupported job kind: {kind}",
                )
                conn.commit()
                return

            update_job(
                conn,
                job_id,
                status="running",
                phase=None,
                processed=0,
                error=None,
                result_json=None,
            )
            conn.commit()
            log.info(
                "job %s started: kind=%s mode=%s profile=%s",
                job_id,
                kind,
                mode,
                profile_id,
            )

            def progress(phase: str, processed: int, total: int) -> None:
                update_job(
                    conn,
                    job_id,
                    phase=phase,
                    processed=processed,
                    total=total,
                )
                conn.commit()

            try:
                result = handler(
                    conn,
                    self.settings,
                    job_id,
                    profile_id,
                    progress,
                    stop_event.is_set,
                )
            except Exception as exc:
                conn.rollback()
                message = _job_error_message(exc)
                update_job(conn, job_id, status="failed", error=message)
                conn.commit()
                if isinstance(exc, FeedError):
                    log.error("job %s failed: %s", job_id, message)
                else:
                    log.exception("job %s failed: %s", job_id, message)
                return

            total_row = conn.execute(
                "SELECT total FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            total = int(total_row[0]) if total_row is not None else 0
            update_job(
                conn,
                job_id,
                status="done",
                processed=total,
                result_json=json.dumps(result or {}, sort_keys=True),
            )
            conn.commit()
            log.info("job %s finished", job_id)
