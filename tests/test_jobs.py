from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import connect, init_db, open_db
from app.seed import seed_database
from app.services.jobs import (
    JobConflict,
    JobNotFound,
    JobRunner,
    create_job,
    get_job,
    latest_job,
    run_refresh_job,
    update_job,
)

PROFILE_ID = "ds.v1"


class JobTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        self.settings = replace(
            Settings.from_env(),
            db_path=self.db_path,
            log_path=Path(self._tmp.name) / "test.log",
        )
        init_db(self.db_path)
        self.conn = connect(self.db_path)
        self.addCleanup(self.conn.close)
        seed_database(self.conn, self.settings)

    def start_runner(self, handler) -> JobRunner:
        runner = JobRunner(self.settings, refresh_handler=handler)
        runner.start()
        self.addCleanup(runner.stop)
        return runner

    def wait_for_status(
        self,
        job_id: int,
        expected: set[str],
        *,
        timeout: float = 3.0,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with open_db(self.db_path) as conn:
                job = get_job(conn, job_id)
            if job is not None and job["status"] in expected:
                return job
            time.sleep(0.01)
        with open_db(self.db_path) as conn:
            job = get_job(conn, job_id)
        self.fail(f"job {job_id} did not reach {expected}: {job}")

    def test_runner_completes_job_and_stores_result(self) -> None:
        def handler(
            conn: sqlite3.Connection,
            settings: Settings,
            job_id: int,
            profile_id: str,
            progress,
        ) -> dict[str, object]:
            progress(1, 2)
            return {"new_items": 4, "profile_id": profile_id}

        runner = self.start_runner(handler)
        job_id, created = runner.enqueue_refresh(PROFILE_ID)
        self.assertTrue(created)

        job = self.wait_for_status(job_id, {"done", "failed"})

        self.assertEqual(job["status"], "done")
        self.assertEqual(job["processed"], 2)
        self.assertEqual(job["total"], 2)
        self.assertEqual(job["result"], {"new_items": 4, "profile_id": PROFILE_ID})

    def test_run_refresh_job_merges_classification_progress_and_summary(self) -> None:
        progress: list[tuple[int, int]] = []

        def fake_refresh(conn, settings, profile_id, *, on_progress=None):
            if on_progress:
                on_progress(1, 1)
            return {"feeds_total": 1, "pending_items": 2, "items_total": 2}

        def fake_classify(conn, settings, profile_id, *, on_progress=None):
            if on_progress:
                on_progress(0, 2)
                on_progress(1, 2)
                on_progress(2, 2)
            return {
                "classified_items": 2,
                "classified_categories": 16,
                "pending_items": 0,
                "unprocessed_items": 0,
                "model": settings.tev_model,
                "duration_s": 0.1,
            }

        with (
            patch("app.services.jobs.refresh_profile", side_effect=fake_refresh),
            patch("app.services.jobs.classify_pending_items", side_effect=fake_classify),
        ):
            result = run_refresh_job(
                self.conn,
                self.settings,
                1,
                PROFILE_ID,
                lambda processed, total: progress.append((processed, total)),
            )

        self.assertEqual(progress, [(1, 1), (1, 3), (2, 3), (3, 3)])
        self.assertEqual(result["feeds_total"], 1)
        self.assertEqual(result["classified_items"], 2)
        self.assertEqual(result["classified_categories"], 16)
        self.assertEqual(result["pending_items"], 0)
        self.assertEqual(result["unprocessed_items"], 0)
        self.assertEqual(result["classification"]["model"], self.settings.tev_model)

    def test_second_enqueue_while_running_returns_existing_job(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def handler(
            conn: sqlite3.Connection,
            settings: Settings,
            job_id: int,
            profile_id: str,
            progress,
        ) -> dict[str, object]:
            started.set()
            if not release.wait(timeout=3):
                raise RuntimeError("test did not release handler")
            progress(1, 1)
            return {"ok": True}

        runner = self.start_runner(handler)
        job_id, created = runner.enqueue_refresh(PROFILE_ID)
        self.assertTrue(created)
        self.assertTrue(started.wait(timeout=3))

        second_id, second_created = runner.enqueue_refresh(PROFILE_ID)

        self.assertEqual(second_id, job_id)
        self.assertFalse(second_created)

        release.set()
        job = self.wait_for_status(job_id, {"done", "failed"})
        self.assertEqual(job["status"], "done")

    def test_failed_job_can_be_retried(self) -> None:
        calls: list[int] = []

        def handler(
            conn: sqlite3.Connection,
            settings: Settings,
            job_id: int,
            profile_id: str,
            progress,
        ) -> dict[str, object]:
            calls.append(job_id)
            if len(calls) == 1:
                raise RuntimeError("first attempt fails")
            progress(1, 1)
            return {"retried": True}

        runner = self.start_runner(handler)
        job_id, _ = runner.enqueue_refresh(PROFILE_ID)

        with self.assertLogs("app.services.jobs", level="ERROR"):
            failed = self.wait_for_status(job_id, {"done", "failed"})
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "first attempt fails")

        with self.assertRaises(JobNotFound):
            runner.enqueue_retry(999_999)

        retry_id, created = runner.enqueue_retry(job_id)
        self.assertTrue(created)
        self.assertNotEqual(retry_id, job_id)

        done = self.wait_for_status(retry_id, {"done", "failed"})
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"], {"retried": True})
        self.assertEqual(calls, [job_id, retry_id])

        with self.assertRaises(JobConflict):
            runner.enqueue_retry(retry_id)

    def test_start_recovers_interrupted_jobs(self) -> None:
        job_id = create_job(
            self.conn,
            kind="refresh",
            profile_id=PROFILE_ID,
            total=3,
        )
        update_job(self.conn, job_id, status="running")
        self.conn.commit()

        runner = JobRunner(self.settings)
        runner.start()
        runner.stop()

        with open_db(self.db_path) as conn:
            job = get_job(conn, job_id)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error"], "interrupted by server restart")

    def test_latest_job_returns_newest_for_profile(self) -> None:
        first = create_job(self.conn, kind="refresh", profile_id=PROFILE_ID)
        second = create_job(self.conn, kind="refresh", profile_id=PROFILE_ID)
        update_job(self.conn, second, status="running")
        self.conn.commit()

        with open_db(self.db_path) as conn:
            latest = latest_job(conn, PROFILE_ID)
        self.assertEqual(latest["id"], second)
        self.assertGreater(second, first)


if __name__ == "__main__":
    unittest.main()
