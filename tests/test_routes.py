from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.db import init_db, open_db
from app.main import create_app
from app.seed import seed_database
from app.services.jobs import create_job, get_job, update_job
from fastapi.testclient import TestClient


class RouteIntegrationTests(unittest.TestCase):
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
        with open_db(self.db_path) as conn:
            seed_database(conn, self.settings)
            profile = conn.execute(
                "SELECT id, name FROM profiles WHERE is_active = 1"
            ).fetchone()
            self.profile_id = str(profile["id"])
            self.profile_name = str(profile["name"])
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def _make_job(self, *, status: str | None = None, error: str | None = None) -> int:
        with open_db(self.db_path) as conn:
            job_id = create_job(
                conn,
                kind="refresh",
                profile_id=self.profile_id,
                total=1,
            )
            if status is not None:
                update_job(conn, job_id, status=status, error=error)
            conn.commit()
        return job_id

    def _job_row(self, job_id: int) -> dict[str, object] | None:
        with open_db(self.db_path) as conn:
            return get_job(conn, job_id)

    def test_healthz_reports_seeded_counts(self) -> None:
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertGreaterEqual(payload["profiles"], 2)
        self.assertGreater(payload["categories"], 0)
        self.assertGreater(payload["feeds"], 0)

    def test_index_redirects_to_active_profile_digest(self) -> None:
        response = self.client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            f"/profiles/{self.profile_id}/digest",
        )

    def test_digest_renders_for_active_profile(self) -> None:
        response = self.client.get(f"/profiles/{self.profile_id}/digest")

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.profile_name, response.text)
        self.assertIn('id="job-panel"', response.text)
        self.assertIn("Last", response.text)

    def test_unknown_profile_digest_returns_404(self) -> None:
        response = self.client.get("/profiles/unknown/digest")

        self.assertEqual(response.status_code, 404)

    def test_refresh_enqueues_job_and_redirects(self) -> None:
        response = self.client.post(
            f"/profiles/{self.profile_id}/refresh",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        prefix = f"/profiles/{self.profile_id}/digest?job="
        location = response.headers["location"]
        self.assertTrue(location.startswith(prefix), location)
        job_id = int(location.removeprefix(prefix))
        job = self._job_row(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "queued")

    def test_refresh_htmx_returns_job_panel(self) -> None:
        response = self.client.post(
            f"/profiles/{self.profile_id}/refresh",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="job-panel"', response.text)
        self.assertIn("Queued", response.text)

    def test_second_refresh_reuses_active_job(self) -> None:
        first = self.client.post(
            f"/profiles/{self.profile_id}/refresh",
            follow_redirects=False,
        )
        first_id = int(first.headers["location"].rsplit("job=", 1)[1])

        second = self.client.post(
            f"/profiles/{self.profile_id}/refresh",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(second.status_code, 200)
        self.assertIn("A refresh is already running.", second.text)
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE profile_id = ? AND status = 'queued'",
                (self.profile_id,),
            ).fetchall()
        self.assertEqual([int(row["id"]) for row in rows], [first_id])

    def test_unknown_profile_refresh_returns_404(self) -> None:
        response = self.client.post("/profiles/unknown/refresh")

        self.assertEqual(response.status_code, 404)

    def test_job_fragment_renders_queued_job(self) -> None:
        job_id = self._make_job()

        response = self.client.get(f"/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"Refresh job #{job_id}", response.text)
        self.assertIn("Queued", response.text)

    def test_job_fragment_done_requests_page_refresh(self) -> None:
        job_id = self._make_job(status="done")

        response = self.client.get(
            f"/jobs/{job_id}",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Refresh complete", response.text)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")

    def test_unknown_job_fragment_returns_404(self) -> None:
        response = self.client.get("/jobs/999999")

        self.assertEqual(response.status_code, 404)

    def test_retry_failed_job_enqueues_new_job(self) -> None:
        failed_id = self._make_job(status="failed", error="boom")

        response = self.client.post(
            f"/jobs/{failed_id}/retry",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            f"/profiles/{self.profile_id}/digest",
        )
        with open_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, status FROM jobs WHERE profile_id = ? ORDER BY id",
                (self.profile_id,),
            ).fetchall()
        jobs = {int(row["id"]): str(row["status"]) for row in rows}
        self.assertEqual(jobs[failed_id], "failed")
        queued = [job_id for job_id, status in jobs.items() if status == "queued"]
        self.assertEqual(len(queued), 1)
        self.assertNotEqual(queued[0], failed_id)

    def test_retry_non_failed_job_redirects_to_digest(self) -> None:
        job_id = self._make_job()

        response = self.client.post(
            f"/jobs/{job_id}/retry",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            f"/profiles/{self.profile_id}/digest",
        )

    def test_retry_unknown_job_returns_404(self) -> None:
        response = self.client.post("/jobs/999999/retry")

        self.assertEqual(response.status_code, 404)

    def test_favicon_is_served(self) -> None:
        response = self.client.get("/favicon.ico")

        self.assertEqual(response.status_code, 200)
        self.assertIn("image/", response.headers["content-type"])
