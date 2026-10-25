from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import SCHEMA_VERSION, connect, init_db
from app.seed import seed_database, set_active_profile
from app.versioning import category_version_hash, normalize_text


class FoundationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        self.settings = replace(Settings.from_env(), db_path=self.db_path)
        init_db(self.db_path)

    def connection(self) -> sqlite3.Connection:
        conn = connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def seed(self) -> dict[str, object]:
        with self.connection() as conn:
            return seed_database(conn, self.settings)


class SeedTests(FoundationTestCase):
    def test_seed_creates_profiles_categories_and_feeds(self) -> None:
        summary = self.seed()

        self.assertEqual(summary["profiles_created"], 2)
        self.assertEqual(summary["profiles_existing"], 0)
        self.assertEqual(summary["categories_created"], 17)
        self.assertEqual(summary["feeds_created"], 25)
        self.assertEqual(summary["active_profile_id"], "ds.v1")

        with self.connection() as conn:
            profiles = {
                row["id"]: dict(row)
                for row in conn.execute("SELECT id, kind, threshold, is_active FROM profiles")
            }
            self.assertEqual(profiles["ds.v1"]["threshold"], 0.62)
            self.assertEqual(profiles["ds.v1"]["is_active"], 1)
            self.assertEqual(profiles["fun.v1"]["threshold"], 0.55)
            self.assertEqual(profiles["fun.v1"]["is_active"], 0)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM category_versions").fetchone()[0], 17
            )
            self.assertEqual(
                conn.execute(
                    "SELECT SUM(enabled) FROM categories WHERE profile_id = 'fun.v1'"
                ).fetchone()[0],
                3,
            )

    def test_seed_is_idempotent(self) -> None:
        self.seed()
        summary = self.seed()

        self.assertEqual(summary["profiles_created"], 0)
        self.assertEqual(summary["profiles_existing"], 2)
        self.assertEqual(summary["categories_created"], 0)
        self.assertEqual(summary["feeds_created"], 0)
        self.assertEqual(summary["profiles_total"], 2)
        self.assertEqual(summary["categories_total"], 17)
        self.assertEqual(summary["feeds_total"], 25)

    def test_reseeding_does_not_restore_a_removed_feed(self) -> None:
        self.seed()
        with self.connection() as conn:
            conn.execute(
                "DELETE FROM feeds WHERE profile_id = 'ds.v1' AND title = 'KDnuggets'"
            )
            conn.commit()

        summary = self.seed()

        self.assertEqual(summary["feeds_total"], 24)
        with self.connection() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM feeds WHERE profile_id = 'ds.v1' AND title = 'KDnuggets'"
                ).fetchone()
            )

    def test_active_profile_switch_keeps_single_active_row(self) -> None:
        self.seed()
        with self.connection() as conn:
            set_active_profile(conn, "fun.v1")
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM profiles WHERE is_active = 1").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT id FROM profiles WHERE is_active = 1").fetchone()[0],
                "fun.v1",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT value FROM settings WHERE key = 'active_profile_id'"
                ).fetchone()[0],
                "fun.v1",
            )

    def test_database_version_and_integrity(self) -> None:
        self.seed()
        with self.connection() as conn:
            self.assertEqual(
                conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO feeds (profile_id, url, title) "
                    "VALUES ('missing', 'https://x.test', 'X')"
                )


class VersioningTests(unittest.TestCase):
    def test_whitespace_is_normalized(self) -> None:
        self.assertEqual(normalize_text(" a\n b\tc "), "a b c")

    def test_version_hash_ignores_non_classification_fields(self) -> None:
        base = {
            "name": "ML Methods",
            "description": "Methods and benchmarks.",
            "criteria_true": "Reports a method.",
            "criteria_false": "Product news.",
            "reason_hint": "covers methods",
        }
        changed = {
            **base,
            "name": "ML   Methods ",
            "description": "Methods and\nbenchmarks.",
            "weight": 99,
            "enabled": False,
        }
        self.assertEqual(category_version_hash(base), category_version_hash(changed))

    def test_version_hash_changes_with_classification_fields(self) -> None:
        base = {
            "name": "ML Methods",
            "description": "Methods and benchmarks.",
            "criteria_true": "Reports a method.",
            "criteria_false": "Product news.",
            "reason_hint": "covers methods",
        }
        self.assertNotEqual(
            category_version_hash(base),
            category_version_hash({**base, "description": "Different definition."}),
        )


class ConfigTests(unittest.TestCase):
    def test_invalid_threshold_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {"THRESHOLD": "1.5"}, clear=False),
            self.assertRaises(ValueError),
        ):
            Settings.from_env()

    def test_invalid_port_is_rejected(self) -> None:
        with (
            patch.dict(os.environ, {"PORT": "not-a-port"}, clear=False),
            self.assertRaises(ValueError),
        ):
            Settings.from_env()

    def test_relative_paths_resolve_against_project_root(self) -> None:
        with patch.dict(os.environ, {"DB_PATH": "./data/example.db"}, clear=False):
            settings = Settings.from_env()
        self.assertTrue(settings.db_path.is_absolute())
        self.assertTrue(str(settings.db_path).endswith("/data/example.db"))


if __name__ == "__main__":
    unittest.main()
