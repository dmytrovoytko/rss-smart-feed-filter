from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import init_db, open_db
from app.main import create_app
from app.routes.pages import _last_refreshed_at
from app.seed import seed_database
from app.services.jobs import create_job, get_job, update_job
from app.timeutil import to_utc_iso
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

    def _make_job(
        self,
        *,
        status: str | None = None,
        error: str | None = None,
        mode: str = "refresh",
        phase: str | None = None,
    ) -> int:
        with open_db(self.db_path) as conn:
            job_id = create_job(
                conn,
                kind="refresh",
                profile_id=self.profile_id,
                mode=mode,
                total=1,
            )
            if status is not None:
                update_job(
                    conn,
                    job_id,
                    status=status,
                    error=error,
                    phase=phase,
                )
            conn.commit()
        return job_id

    def _add_pending_item(self) -> None:
        now = to_utc_iso(datetime.now(UTC))
        with open_db(self.db_path) as conn:
            feed = conn.execute(
                "SELECT id FROM feeds WHERE profile_id = ? ORDER BY id LIMIT 1",
                (self.profile_id,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO items (
                    feed_id, canonical_url, url_hash, title, summary,
                    published_at, fetched_at, content_hash
                ) VALUES (?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    feed["id"],
                    "https://example.com/pending",
                    "pending-url",
                    "Pending item",
                    now,
                    now,
                    "pending-content",
                ),
            )
            conn.commit()

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
        self.assertIn('id="refresh-control"', response.text)
        self.assertIn('hx-swap-oob="true"', response.text)

    def test_settings_page_renders_current_values(self) -> None:
        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Max new items per refresh", response.text)
        self.assertIn("Max items per source", response.text)
        self.assertIn("Digest window (hours)", response.text)
        self.assertIn(f'value="{self.settings.max_new_items}"', response.text)
        self.assertIn(f'value="{self.settings.max_items_per_feed}"', response.text)
        self.assertIn(f'value="{self.settings.window_hours}"', response.text)
        self.assertIn("Hide the low-confidence section", response.text)
        self.assertIn("Hide match explanations", response.text)

    def test_settings_post_persists_preferences_and_limit(self) -> None:
        response = self.client.post(
            "/settings",
            data={
                "max_new_items": "7",
                "max_items_per_feed": "5",
                "window_hours": "48",
                "hide_low_confidence": "on",
                "hide_reason_text": "on",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/settings?saved=1")
        with open_db(self.db_path) as conn:
            values = {
                str(row["key"]): str(row["value"])
                for row in conn.execute(
                    """
                    SELECT key, value FROM settings
                    WHERE key IN (
                        'max_new_items',
                        'max_items_per_feed',
                        'window_hours',
                        'hide_low_confidence',
                        'hide_reason_text'
                    )
                    """
                )
            }
        self.assertEqual(values["max_new_items"], "7")
        self.assertEqual(values["max_items_per_feed"], "5")
        self.assertEqual(values["window_hours"], "48")
        self.assertEqual(values["hide_low_confidence"], "1")
        self.assertEqual(values["hide_reason_text"], "1")

    def test_settings_post_rejects_invalid_limit(self) -> None:
        response = self.client.post("/settings", data={"max_new_items": "0"})

        self.assertEqual(response.status_code, 422)
        self.assertIn("at least 1", response.text)

    def test_activate_profile_persists_choice(self) -> None:
        response = self.client.post(
            "/profiles/fun.v1/activate",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/profiles/fun.v1/digest")
        with open_db(self.db_path) as conn:
            active = conn.execute(
                "SELECT value FROM settings WHERE key = 'active_profile_id'"
            ).fetchone()
            active_row = conn.execute(
                "SELECT id FROM profiles WHERE is_active = 1"
            ).fetchone()
        self.assertEqual(active["value"], "fun.v1")
        self.assertEqual(active_row["id"], "fun.v1")

        index = self.client.get("/", follow_redirects=False)
        self.assertEqual(index.headers["location"], "/profiles/fun.v1/digest")

    def test_digest_shows_last_refreshed_and_fetch_progress(self) -> None:
        self._make_job(status="done")
        running_id = self._make_job(status="running")
        with open_db(self.db_path) as conn:
            update_job(
                conn,
                running_id,
                phase="fetching",
                processed=1,
                total=4,
            )
            conn.commit()

        response = self.client.get(f"/profiles/{self.profile_id}/digest")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Last refreshed", response.text)
        self.assertIn("Fetching 1/4", response.text)
        self.assertIn("1 of 4 sources checked", response.text)
        self.assertIn("width: 5%", response.text)
        self.assertIn('id="digest-candidate-count"', response.text)

    def test_running_refresh_job_reports_fetch_and_window_counts(self) -> None:
        job_id = self._make_job(status="running", phase="classifying")
        with open_db(self.db_path) as conn:
            update_job(
                conn,
                job_id,
                processed=0,
                total=5,
                result_json=json.dumps(
                    {
                        "mode": "refresh",
                        "feeds_total": 3,
                        "feeds_failed": 1,
                        "new_items": 2,
                        "updated_items": 1,
                        "window_items": 9,
                    }
                ),
            )
            conn.commit()

        response = self.client.get(f"/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Fetched <strong>2</strong> new items", response.text)
        self.assertIn("<strong>1</strong> updated", response.text)
        self.assertIn("<strong>9</strong> candidate items in window", response.text)
        self.assertIn("1 source failed", response.text)
        self.assertIn(
            '<span id="digest-candidate-count" hx-swap-oob="true">'
            "9 candidate items in window</span>",
            response.text,
        )

    def test_running_fetch_job_does_not_report_candidate_count(self) -> None:
        job_id = self._make_job(status="running", phase="fetching")

        response = self.client.get(f"/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="digest-candidate-count"', response.text)
        self.assertNotIn("candidate item", response.text)
        self.assertNotIn("Stop classification", response.text)

    def test_running_classification_job_shows_stop_button(self) -> None:
        job_id = self._make_job(status="running", phase="classifying")

        response = self.client.get(f"/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Stop classification", response.text)
        self.assertIn(f"/jobs/{job_id}/stop", response.text)

    def test_stop_classification_requests_stop_and_renders_notice(self) -> None:
        job_id = self._make_job(status="running", phase="classifying")

        with (
            patch.object(self.app.state.jobs, "request_stop", return_value=True),
            patch.object(self.app.state.jobs, "is_stop_requested", return_value=True),
        ):
            response = self.client.post(
                f"/jobs/{job_id}/stop",
                headers={"HX-Request": "true"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Stopping after current item...", response.text)
        self.assertNotIn("Stop classification", response.text)

    def test_done_stopped_classification_job_renders_stopped_summary(self) -> None:
        job_id = self._make_job(status="done", mode="classify")
        with open_db(self.db_path) as conn:
            update_job(
                conn,
                job_id,
                result_json=json.dumps(
                    {
                        "mode": "classify",
                        "stopped": True,
                        "classified_items": 2,
                        "classified_categories": 16,
                        "pending_after": 1,
                        "items_total": 5,
                        "duration_s": 0.2,
                    }
                ),
            )
            conn.commit()

        response = self.client.get(f"/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Classification stopped", response.text)
        self.assertIn("Classification stopped after 2 items.", response.text)
        self.assertNotIn("Classification complete", response.text)

    def test_classify_enqueues_classification_job_and_redirects(self) -> None:
        response = self.client.post(
            f"/profiles/{self.profile_id}/classify",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        prefix = f"/profiles/{self.profile_id}/digest?job="
        location = response.headers["location"]
        self.assertTrue(location.startswith(prefix), location)
        job_id = int(location.removeprefix(prefix))
        job = self._job_row(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["mode"], "classify")
        self.assertEqual(job["status"], "queued")

    def test_classify_htmx_returns_classification_job_panel(self) -> None:
        response = self.client.post(
            f"/profiles/{self.profile_id}/classify",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Classification job", response.text)
        self.assertIn("Queued", response.text)
        self.assertIn('id="refresh-control"', response.text)

    def test_digest_shows_classify_pending_action(self) -> None:
        self._add_pending_item()

        response = self.client.get(f"/profiles/{self.profile_id}/digest")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Classify 1 pending", response.text)
        self.assertIn(
            f"/profiles/{self.profile_id}/classify",
            response.text,
        )
        self.assertIn("Use Classify pending", response.text)

    def test_classify_job_does_not_change_last_refreshed(self) -> None:
        refresh_id = self._make_job(status="done")
        classify_id = self._make_job(status="done", mode="classify")
        future = to_utc_iso(datetime.now(UTC) + timedelta(hours=1))
        with open_db(self.db_path) as conn:
            refresh_job = get_job(conn, refresh_id)
            conn.execute(
                "UPDATE jobs SET updated_at = ? WHERE id = ?",
                (future, classify_id),
            )
            conn.commit()
            last_refreshed = _last_refreshed_at(conn, self.profile_id)

        self.assertEqual(last_refreshed, refresh_job["updated_at"])
        self.assertNotEqual(last_refreshed, future)

    def test_done_classification_job_renders_classification_summary(self) -> None:
        job_id = self._make_job(status="done", mode="classify")
        with open_db(self.db_path) as conn:
            update_job(
                conn,
                job_id,
                result_json=json.dumps(
                    {
                        "mode": "classify",
                        "classified_items": 2,
                        "classified_categories": 16,
                        "pending_after": 1,
                        "items_total": 5,
                        "duration_s": 0.2,
                    }
                ),
            )
            conn.commit()

        response = self.client.get(f"/jobs/{job_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Classification complete", response.text)
        self.assertIn("<strong>2</strong> item", response.text)
        self.assertIn("items classified", response.text)
        self.assertIn("Use Classify pending", response.text)

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
        self.assertIn("A job is already running.", second.text)
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
