from __future__ import annotations

import html
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
from app.observability import ObservabilityStore
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
            obs_db_path=Path(self._tmp.name) / "observability.db",
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

    def _add_classified_item(
        self,
        title: str,
        *,
        category_id: str | None = None,
        probability: float = 0.9,
        profile_id: str | None = None,
    ) -> tuple[int, str]:
        profile = profile_id or self.profile_id
        now = to_utc_iso(datetime.now(UTC))
        with open_db(self.db_path) as conn:
            category = conn.execute(
                """
                SELECT id, version_hash
                FROM categories
                WHERE profile_id = ? AND enabled = 1
                  AND (? IS NULL OR id = ?)
                ORDER BY position, id
                LIMIT 1
                """,
                (profile, category_id, category_id),
            ).fetchone()
            if category is None:
                raise AssertionError(f"missing category for {profile}: {category_id}")
            feed = conn.execute(
                "SELECT id FROM feeds WHERE profile_id = ? ORDER BY id LIMIT 1",
                (profile,),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO items (
                    feed_id, canonical_url, url_hash, title, summary,
                    published_at, fetched_at, content_hash
                ) VALUES (?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    feed["id"],
                    f"https://example.com/{title.lower().replace(' ', '-')}",
                    f"url-{title}",
                    title,
                    now,
                    now,
                    f"content-{title}",
                ),
            )
            item_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO classifications (
                    item_id, profile_id, category_id, category_version,
                    model, probability, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    profile,
                    str(category["id"]),
                    str(category["version_hash"]),
                    self.settings.tev_model,
                    probability,
                    now,
                ),
            )
            conn.commit()
        return item_id, str(category["id"])

    def _add_score(
        self,
        item_id: int,
        category_id: str,
        probability: float,
        *,
        profile_id: str | None = None,
    ) -> None:
        profile = profile_id or self.profile_id
        now = to_utc_iso(datetime.now(UTC))
        with open_db(self.db_path) as conn:
            category = conn.execute(
                """
                SELECT version_hash
                FROM categories
                WHERE profile_id = ? AND id = ?
                """,
                (profile, category_id),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO classifications (
                    item_id, profile_id, category_id, category_version,
                    model, probability, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    profile,
                    category_id,
                    str(category["version_hash"]),
                    self.settings.tev_model,
                    probability,
                    now,
                ),
            )
            conn.commit()

    def _job_row(self, job_id: int) -> dict[str, object] | None:
        with open_db(self.db_path) as conn:
            return get_job(conn, job_id)

    def test_digest_request_records_observability_trace(self) -> None:
        response = self.client.get(f"/profiles/{self.profile_id}/digest")

        self.assertEqual(response.status_code, 200)
        trace_id = response.headers.get("X-Trace-ID")
        self.assertIsNotNone(trace_id)
        assert trace_id is not None

        store = self.app.state.observability
        trace = store.get_trace(trace_id)
        self.assertIsNotNone(trace)
        assert trace is not None
        self.assertEqual(trace["kind"], "http")
        self.assertEqual(trace["status"], "ok")
        self.assertTrue(store.list_spans(trace_id))
        self.assertIn(
            "request.finished",
            {item["event"] for item in store.list_logs(trace_id)},
        )
        self.assertIn(
            "http.request.duration_ms",
            {item["name"] for item in store.list_metrics(trace_id)},
        )

    def test_static_and_favicon_requests_are_not_traced(self) -> None:
        response = self.client.get("/favicon.ico")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Trace-ID", response.headers)

    def test_healthz_reports_seeded_counts(self) -> None:
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertGreaterEqual(payload["profiles"], 2)
        self.assertGreater(payload["categories"], 0)
        self.assertGreater(payload["feeds"], 0)

    def test_observability_dashboard_renders_empty_state(self) -> None:
        self.app.state.observability.init()

        response = self.client.get("/observability?kind=job&status=error")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Observability", response.text)
        self.assertIn("/observability", response.text)
        self.assertIn("No matching traces", response.text)
        self.assertIn("No alerts", response.text)

    def test_observability_dashboard_filters_traces(self) -> None:
        store = self.app.state.observability
        store.init()
        store.start_trace("trace-job", kind="job", name="refresh.job")
        store.finish_trace("trace-job", status="ok", duration_ms=12.0)
        store.start_trace("trace-http", kind="http", name="GET /digest")
        store.finish_trace("trace-http", status="ok", duration_ms=4.0)

        response = self.client.get("/observability?kind=job&status=ok")

        self.assertEqual(response.status_code, 200)
        self.assertIn("refresh.job", response.text)
        self.assertNotIn("GET /digest", response.text)
        self.assertIn('value="job" selected', response.text)

    def test_observability_trace_detail_renders_spans_metrics_and_logs(self) -> None:
        store = self.app.state.observability
        store.init()
        store.start_trace(
            "trace-detail",
            kind="job",
            name="refresh.job",
            profile_id=self.profile_id,
            job_id=7,
        )
        with store.span("trace-detail", "feed.fetch", attributes={"feeds": 2}):
            pass
        store.record_metric(
            "trace-detail",
            "job.duration_ms",
            12.5,
            unit="ms",
            labels={"mode": "refresh"},
        )
        store.record_log(
            "trace-detail",
            "info",
            "job.finished",
            fields={"status": "done"},
        )
        store.finish_trace("trace-detail", status="ok", duration_ms=12.5)

        response = self.client.get("/observability/traces/trace-detail")

        self.assertEqual(response.status_code, 200)
        self.assertIn("refresh.job", response.text)
        self.assertIn("feed.fetch", response.text)
        self.assertIn("job.duration_ms", response.text)
        self.assertIn("job.finished", response.text)
        self.assertIn("Trace metadata", response.text)

    def test_observability_trace_detail_unknown_trace_returns_404(self) -> None:
        self.app.state.observability.init()

        response = self.client.get("/observability/traces/missing-trace")

        self.assertEqual(response.status_code, 404)

    def test_observability_dashboard_handles_unavailable_database(self) -> None:
        self.app.state.observability = ObservabilityStore(
            Path(self._tmp.name) / "unavailable" / "observability.db"
        )

        response = self.client.get("/observability")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Observability database unavailable", response.text)

    def test_digest_shows_open_alert_banner(self) -> None:
        store = self.app.state.observability
        store.init()
        store.raise_alert(
            profile_id=self.profile_id,
            code="zero_items",
            severity="warning",
            message="Refresh completed but the 72-hour digest has no candidate items.",
            trace_id="banner-trace",
        )

        response = self.client.get(f"/profiles/{self.profile_id}/digest")

        self.assertEqual(response.status_code, 200)
        self.assertIn("1 active alert", response.text)
        self.assertIn("no candidate items", response.text)
        self.assertIn("Review and acknowledge alerts", response.text)

    def test_observability_acknowledge_alert(self) -> None:
        store = self.app.state.observability
        store.init()
        store.raise_alert(
            profile_id=self.profile_id,
            code="zero_items",
            severity="warning",
            message="Acknowledge me",
        )
        alert_id = int(store.list_alerts(profile_id=self.profile_id)[0]["id"])

        response = self.client.post(
            f"/observability/alerts/{alert_id}/acknowledge",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/observability")
        self.assertEqual(store.open_alert_count(self.profile_id), 0)
        detail = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertNotIn("Acknowledge me", detail.text)

    def test_healthz_reports_observability_status_and_open_alerts(self) -> None:
        store = self.app.state.observability
        store.init()
        store.raise_alert(
            profile_id=self.profile_id,
            code="job_failed",
            severity="error",
            message="Failed job",
        )

        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["observability"]["status"], "ok")
        self.assertEqual(payload["observability"]["open_alerts"], 1)

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

    def test_digest_shows_fetchable_sources_and_category_disabled_state(self) -> None:
        with open_db(self.db_path) as conn:
            conn.execute(
                "UPDATE categories SET enabled = 0 "
                "WHERE profile_id = 'fun.v1' AND id = 'games-tabletop'"
            )
            conn.commit()

        response = self.client.get("/profiles/fun.v1/digest")

        self.assertEqual(response.status_code, 200)
        self.assertIn("sources fetchable", response.text)
        self.assertIn("category disabled", response.text)

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

    def test_archive_item_hides_digest_and_remove_converts_to_dismiss(self) -> None:
        item_id, _ = self._add_classified_item("Archive me")

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{item_id}/archive",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            f"/profiles/{self.profile_id}/digest",
        )

        archive = self.client.get(f"/profiles/{self.profile_id}/archive")
        self.assertIn("Archive me", archive.text)
        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertNotIn("Archive me", digest.text)

        removed = self.client.post(
            f"/profiles/{self.profile_id}/archive/{item_id}/remove",
            headers={"HX-Request": "true"},
        )
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(removed.text, "")

        with open_db(self.db_path) as conn:
            archived = conn.execute(
                "SELECT COUNT(*) FROM archived_items WHERE profile_id = ? AND item_id = ?",
                (self.profile_id, item_id),
            ).fetchone()[0]
            dismissed = conn.execute(
                "SELECT COUNT(*) FROM dismissed_items WHERE profile_id = ? AND item_id = ?",
                (self.profile_id, item_id),
            ).fetchone()[0]
        self.assertEqual(archived, 0)
        self.assertEqual(dismissed, 1)

        archive = self.client.get(f"/profiles/{self.profile_id}/archive")
        self.assertNotIn("Archive me", archive.text)
        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertNotIn("Archive me", digest.text)

    def test_dismiss_item_hides_item_without_processed_view(self) -> None:
        item_id, _ = self._add_classified_item("Dismiss me")

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{item_id}/dismiss",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="digest-results"', response.text)
        self.assertNotIn("Dismiss me", response.text)
        self.assertIn("0 candidate item", response.text)
        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertNotIn("Dismiss me", digest.text)
        archive = self.client.get(f"/profiles/{self.profile_id}/archive")
        self.assertNotIn("Dismiss me", archive.text)
        with open_db(self.db_path) as conn:
            dismissed = conn.execute(
                "SELECT COUNT(*) FROM dismissed_items WHERE profile_id = ? AND item_id = ?",
                (self.profile_id, item_id),
            ).fetchone()[0]
        self.assertEqual(dismissed, 1)

    def test_archive_updates_category_counts_without_reload(self) -> None:
        first_id, category_id = self._add_classified_item("Count one")
        second_id, _ = self._add_classified_item(
            "Count two",
            category_id=category_id,
        )

        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertIn(f'id="category-count-{category_id}">2</span>', digest.text)

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{first_id}/archive",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'id="category-count-{category_id}">1</span>', response.text)
        self.assertIn("1 candidate item in window", response.text)
        self.assertIn("1 classified", response.text)
        self.assertNotIn("Count one", response.text)
        self.assertIn("Count two", response.text)

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{second_id}/archive",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(f'id="category-count-{category_id}"', response.text)
        self.assertIn("No items in this window", response.text)
        self.assertIn("0 candidate item", response.text)

    def test_archive_updates_low_confidence_count_without_reload(self) -> None:
        first_id, _ = self._add_classified_item("Low one", probability=0.2)
        self._add_classified_item("Low two", probability=0.2)

        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertIn('id="low-confidence-count">2</span>', digest.text)

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{first_id}/archive",
            headers={"HX-Request": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="low-confidence-count">1</span>', response.text)
        self.assertNotIn("Low one", response.text)
        self.assertIn("Low two", response.text)

    def test_recategorize_accepts_disabled_suggestion_without_enabling(self) -> None:
        with open_db(self.db_path) as conn:
            rejected = conn.execute(
                """
                SELECT id, name FROM categories
                WHERE profile_id = ? AND enabled = 1
                ORDER BY position, id
                LIMIT 1
                """,
                (self.profile_id,),
            ).fetchone()
            suggested = conn.execute(
                """
                SELECT id, name FROM categories
                WHERE profile_id = ? AND enabled = 1 AND id != ?
                ORDER BY position, id
                LIMIT 1
                """,
                (self.profile_id, rejected["id"]),
            ).fetchone()
            assert suggested is not None
            conn.execute(
                "UPDATE categories SET enabled = 0 WHERE profile_id = ? AND id = ?",
                (self.profile_id, suggested["id"]),
            )
            conn.commit()

        item_id, _ = self._add_classified_item(
            "Disabled suggestion item",
            category_id=str(rejected["id"]),
        )

        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertIn(
            f'<option value="{suggested["id"]}">'
            f'{html.escape(str(suggested["name"]))} (disabled)</option>',
            digest.text,
        )

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{item_id}/recategorize",
            data={
                "category_id": rejected["id"],
                "suggested_category_id": suggested["id"],
                "note": "Disabled but useful",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        with open_db(self.db_path) as conn:
            feedback = conn.execute(
                "SELECT * FROM category_feedback WHERE profile_id = ? AND item_id = ?",
                (self.profile_id, item_id),
            ).fetchone()
            enabled = conn.execute(
                "SELECT enabled FROM categories WHERE profile_id = ? AND id = ?",
                (self.profile_id, suggested["id"]),
            ).fetchone()[0]
        self.assertIsNotNone(feedback)
        self.assertEqual(feedback["suggested_category_id"], suggested["id"])
        self.assertEqual(feedback["suggested_category_name"], suggested["name"])
        self.assertEqual(enabled, 0)

    def test_recategorize_hides_match_and_records_feedback(self) -> None:
        with open_db(self.db_path) as conn:
            categories = [
                str(row["id"])
                for row in conn.execute(
                    """
                    SELECT id FROM categories
                    WHERE profile_id = ? AND enabled = 1
                    ORDER BY position, id
                    LIMIT 2
                    """,
                    (self.profile_id,),
                )
            ]
        rejected, suggested = categories
        item_id, _ = self._add_classified_item(
            "Recategorize me",
            category_id=rejected,
        )
        self._add_score(item_id, suggested, 0.9)

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{item_id}/recategorize",
            data={
                "category_id": rejected,
                "suggested_category_id": suggested,
                "note": "Wrong category match",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertEqual(digest.text.count("Recategorize me"), 1)
        feedback = self.client.get(
            f"/profiles/{self.profile_id}/feedback?item_id={item_id}"
        )
        self.assertIn("Wrong category match", feedback.text)
        with open_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM category_feedback WHERE profile_id = ? AND item_id = ?",
                (self.profile_id, item_id),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["category_id"], rejected)
        self.assertEqual(row["suggested_category_id"], suggested)
        self.assertEqual(row["note"], "Wrong category match")

    def test_recategorize_from_low_confidence_hides_entry(self) -> None:
        item_id, category_id = self._add_classified_item(
            "Low confidence item",
            probability=0.2,
        )
        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertIn("Low confidence item", digest.text)

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{item_id}/recategorize",
            data={"category_id": category_id},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        digest = self.client.get(f"/profiles/{self.profile_id}/digest")
        self.assertNotIn("Low confidence item", digest.text)

    def test_feedback_page_and_jsonl_export_are_deterministic(self) -> None:
        item_id, category_id = self._add_classified_item("Feedback item")
        self.client.post(
            f"/profiles/{self.profile_id}/items/{item_id}/recategorize",
            data={"category_id": category_id, "note": "Export me"},
        )

        page = self.client.get(
            f"/profiles/{self.profile_id}/feedback?item_id={item_id}"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn("Export me", page.text)
        all_feedback = self.client.get(f"/profiles/{self.profile_id}/feedback")
        self.assertIn("Export me", all_feedback.text)

        first = self.client.get(
            f"/profiles/{self.profile_id}/feedback.jsonl?item_id={item_id}"
        )
        second = self.client.get(
            f"/profiles/{self.profile_id}/feedback.jsonl?item_id={item_id}"
        )
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.headers["content-type"].startswith("application/x-ndjson"))
        self.assertEqual(first.text, second.text)
        rows = [json.loads(line) for line in first.text.splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_id"], item_id)
        self.assertEqual(rows[0]["category_id"], category_id)
        self.assertEqual(rows[0]["note"], "Export me")

    def test_processing_actions_validate_profile_item_and_category(self) -> None:
        response = self.client.post(
            "/profiles/unknown/items/1/archive",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/999999/archive",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            f"/profiles/{self.profile_id}/archive/999999/remove",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

        item_id, category_id = self._add_classified_item("Unknown category item")
        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{item_id}/recategorize",
            data={"category_id": "missing-category"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{item_id}/recategorize",
            data={
                "category_id": category_id,
                "suggested_category_id": "missing-suggestion",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

        foreign_item, _ = self._add_classified_item(
            "Foreign item",
            profile_id="fun.v1",
        )
        response = self.client.post(
            f"/profiles/{self.profile_id}/items/{foreign_item}/archive",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.get(
            f"/profiles/{self.profile_id}/feedback?item_id=999999"
        )
        self.assertEqual(response.status_code, 404)

    def test_favicon_is_served(self) -> None:
        response = self.client.get("/favicon.ico")

        self.assertEqual(response.status_code, 200)
        self.assertIn("image/", response.headers["content-type"])
