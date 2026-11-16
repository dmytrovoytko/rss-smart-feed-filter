from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import init_db
from app.observability import (
    SCHEMA_VERSION,
    ObservabilityStore,
    init_observability_db,
    open_observability_db,
)


class ObservabilityDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.main_db = Path(self._tmp.name) / "feedfilter.db"
        self.obs_db = Path(self._tmp.name) / "observability.db"
        self.settings = replace(
            Settings.from_env(),
            db_path=self.main_db,
            obs_db_path=self.obs_db,
            log_path=Path(self._tmp.name) / "test.log",
        )
        init_db(self.main_db)

    def _tables(self, db_path: Path) -> set[str]:
        with sqlite3.connect(db_path) as conn:
            return {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

    def test_init_creates_independent_schema(self) -> None:
        init_observability_db(self.obs_db)

        obs_tables = self._tables(self.obs_db)
        main_tables = self._tables(self.main_db)
        expected = {"traces", "spans", "metrics", "logs", "alerts"}

        self.assertTrue(expected.issubset(obs_tables))
        self.assertTrue(expected.isdisjoint(main_tables))

        with open_observability_db(self.obs_db) as conn:
            self.assertEqual(
                int(conn.execute("PRAGMA user_version").fetchone()[0]),
                SCHEMA_VERSION,
            )
            self.assertEqual(
                str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "delete",
            )
            for table in expected:
                self.assertEqual(
                    conn.execute(f"PRAGMA foreign_key_list({table})").fetchall(),
                    [],
                )

    def test_init_recreates_database_after_file_removal(self) -> None:
        store = ObservabilityStore(self.obs_db)
        store.init()
        store.start_trace("trace-1", kind="job", name="refresh")
        self.assertIsNotNone(store.get_trace("trace-1"))

        self.obs_db.unlink()
        init_observability_db(self.obs_db)

        self.assertIsNone(store.get_trace("trace-1"))
        self.assertTrue(self.obs_db.is_file())

    def test_store_round_trip(self) -> None:
        store = ObservabilityStore(self.obs_db)
        store.init()
        store.start_trace(
            "trace-2",
            kind="job",
            name="classify",
            profile_id="ds.v1",
            job_id=42,
        )
        with store.span("trace-2", "fetch", attributes={"feeds": 3}):
            pass
        store.record_metric("trace-2", "job.items", 7, unit="count")
        store.record_log("trace-2", "info", "job.finished", fields={"status": "ok"})
        store.finish_trace("trace-2", duration_ms=12.5)

        trace = store.get_trace("trace-2")
        self.assertIsNotNone(trace)
        assert trace is not None
        self.assertEqual(trace["status"], "ok")
        self.assertEqual(trace["job_id"], 42)
        self.assertEqual(len(store.list_spans("trace-2")), 1)
        self.assertEqual(len(store.list_metrics("trace-2")), 1)
        self.assertEqual(len(store.list_logs("trace-2")), 1)

    def test_alert_deduplication_and_acknowledgement(self) -> None:
        store = ObservabilityStore(self.obs_db)
        store.init()

        store.raise_alert(
            profile_id="ds.v1",
            code="job_failed",
            severity="error",
            message="First failure at https://example.com/feed",
            trace_id="trace-1",
        )
        store.raise_alert(
            profile_id="ds.v1",
            code="job_failed",
            severity="error",
            message="Second failure",
            trace_id="trace-2",
        )

        alerts = store.list_alerts(profile_id="ds.v1", open_only=True)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["message"], "Second failure")
        self.assertEqual(alerts[0]["trace_id"], "trace-2")
        self.assertEqual(store.open_alert_count("ds.v1"), 1)

        alert_id = int(alerts[0]["id"])
        self.assertTrue(store.acknowledge_alert(alert_id))
        self.assertFalse(store.acknowledge_alert(alert_id))
        self.assertEqual(store.open_alert_count("ds.v1"), 0)
        self.assertEqual(store.health(), {"status": "ok", "open_alerts": 0})

        store.raise_alert(
            profile_id="ds.v1",
            code="job_failed",
            severity="error",
            message="Failure after acknowledgement",
        )
        self.assertEqual(store.open_alert_count("ds.v1"), 1)

    def test_alert_message_sanitizes_urls(self) -> None:
        store = ObservabilityStore(self.obs_db)
        store.init()

        store.raise_alert(
            profile_id="ds.v1",
            code="all_feeds_failed",
            severity="error",
            message="Failed https://example.com/private-feed",
        )

        alert = store.list_alerts(profile_id="ds.v1", open_only=True)[0]
        self.assertEqual(alert["message"], "Failed [url]")

    def test_obs_db_path_must_differ_from_main_db(self) -> None:
        with self.assertRaisesRegex(ValueError, "OBS_DB_PATH must differ"):
            replace(self.settings, obs_db_path=self.main_db).validate()

    def test_obs_db_path_reads_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"OBS_DB_PATH": "./data/custom-observability.db"},
            clear=False,
        ):
            settings = Settings.from_env()
        self.assertTrue(settings.obs_db_path.is_absolute())
        self.assertTrue(str(settings.obs_db_path).endswith("/data/custom-observability.db"))


if __name__ == "__main__":
    unittest.main()
