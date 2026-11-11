from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.db import connect, init_db
from app.seed import seed_database
from app.services.digest import build_digest, rank_score, recency_factor
from app.timeutil import human_time, to_utc_iso
from jinja2 import Environment, FileSystemLoader

PROFILE_ID = "fun.v1"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


class DigestTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        self.settings = replace(
            Settings.from_env(),
            db_path=self.db_path,
            log_path=Path(self._tmp.name) / "test.log",
            window_hours=72,
        )
        init_db(self.db_path)
        self.conn = connect(self.db_path)
        self.addCleanup(self.conn.close)
        seed_database(self.conn, self.settings)
        self.conn.execute("DELETE FROM feeds WHERE profile_id = ?", (PROFILE_ID,))
        self.conn.commit()
        self._feed_seq = 0
        self._item_seq = 0
        self.feed_id = self.add_feed("Test Feed")
        self.categories = {
            str(row["id"]): dict(row)
            for row in self.conn.execute(
                """
                SELECT id, name, weight, position, version_hash
                FROM categories
                WHERE profile_id = ? AND enabled = 1
                """,
                (PROFILE_ID,),
            )
        }

    def add_feed(self, title: str, *, enabled: bool = True) -> int:
        self._feed_seq += 1
        cursor = self.conn.execute(
            """
            INSERT INTO feeds (profile_id, url, title, enabled)
            VALUES (?, ?, ?, ?)
            """,
            (
                PROFILE_ID,
                f"https://example.com/feed-{self._feed_seq}.xml",
                title,
                1 if enabled else 0,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def add_item(
        self,
        title: str,
        *,
        published_hours_ago: float | None = 1.0,
        fetched_hours_ago: float = 1.0,
        feed_id: int | None = None,
    ) -> int:
        self._item_seq += 1
        fetched_at = to_utc_iso(NOW - timedelta(hours=fetched_hours_ago))
        published_at = (
            None
            if published_hours_ago is None
            else to_utc_iso(NOW - timedelta(hours=published_hours_ago))
        )
        cursor = self.conn.execute(
            """
            INSERT INTO items (
                feed_id, guid, canonical_url, url_hash, title, summary,
                published_at, fetched_at, content_hash
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)
            """,
            (
                feed_id or self.feed_id,
                f"guid-{self._item_seq}",
                f"https://example.com/item-{self._item_seq}",
                f"url-hash-{self._item_seq}",
                title,
                published_at,
                fetched_at,
                f"content-hash-{self._item_seq}",
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def add_score(
        self,
        item_id: int,
        category_id: str,
        probability: float,
        *,
        created_at: datetime = NOW,
        model: str | None = None,
        version: str | None = None,
    ) -> None:
        category = self.categories[category_id]
        self.conn.execute(
            """
            INSERT INTO classifications (
                item_id, profile_id, category_id, category_version,
                model, probability, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                PROFILE_ID,
                category_id,
                version or str(category["version_hash"]),
                model or self.settings.tev_model,
                probability,
                to_utc_iso(created_at),
            ),
        )
        self.conn.commit()

    def test_ranks_sections_and_keeps_multi_category_items(self) -> None:
        books = "books-film-music"
        cycling = "cycling-outdoors"
        games = "games-tabletop"

        recent = self.add_item("Recent books", published_hours_ago=1)
        for category_id, probability in ((books, 0.8), (cycling, 0.1), (games, 0.1)):
            self.add_score(recent, category_id, probability)

        multi = self.add_item("Multi category", published_hours_ago=36)
        for category_id, probability in ((books, 0.9), (cycling, 0.9), (games, 0.1)):
            self.add_score(multi, category_id, probability)

        game = self.add_item("Recent game", published_hours_ago=2)
        for category_id, probability in ((books, 0.1), (cycling, 0.1), (games, 0.7)):
            self.add_score(game, category_id, probability)

        low = self.add_item("Low item", published_hours_ago=3)
        for category_id, probability in ((books, 0.4), (cycling, 0.3), (games, 0.2)):
            self.add_score(low, category_id, probability)

        digest = build_digest(self.conn, self.settings, PROFILE_ID, now=NOW)

        self.assertEqual(
            [section["id"] for section in digest["sections"]],
            [books, cycling, games],
        )
        books_section = digest["sections"][0]
        self.assertEqual(
            [item["title"] for item in books_section["entries"]],
            ["Recent books", "Multi category"],
        )
        multi_item = books_section["entries"][1]
        self.assertAlmostEqual(multi_item["score"], 0.54)
        self.assertEqual(
            [tag["name"] for tag in multi_item["tags"]],
            ["Books, Film & Music", "Cycling & Outdoors"],
        )
        self.assertIn("Matched Books, Film & Music:", multi_item["reason"])
        self.assertEqual(
            [item["title"] for item in digest["sections"][1]["entries"]],
            ["Multi category"],
        )
        self.assertEqual(
            [item["title"] for item in digest["sections"][2]["entries"]],
            ["Recent game"],
        )
        self.assertEqual(len(digest["low_confidence"]), 1)
        self.assertEqual(digest["low_confidence"][0]["title"], "Low item")
        self.assertEqual(digest["low_confidence"][0]["best_category"], "Books, Film & Music")
        self.assertEqual(digest["window_items"], 4)
        self.assertEqual(digest["classified_items"], 4)
        self.assertEqual(digest["pending_items"], 0)

    def test_recency_and_rank_score(self) -> None:
        self.assertEqual(recency_factor(0), 1.0)
        self.assertAlmostEqual(recency_factor(36), 0.5)
        self.assertAlmostEqual(recency_factor(1000), 0.25)
        self.assertEqual(recency_factor(-4), 1.0)
        self.assertAlmostEqual(rank_score(0.8, 1.2, 36), 0.48)

    def test_window_uses_published_fallback_and_excludes_future(self) -> None:
        fallback = self.add_item("No date", published_hours_ago=None, fetched_hours_ago=1)
        self.add_score(fallback, "books-film-music", 0.8)

        recent = self.add_item("Recent", published_hours_ago=2)
        self.add_score(recent, "books-film-music", 0.7)

        old = self.add_item("Too old", published_hours_ago=100)
        self.add_score(old, "books-film-music", 0.9)

        future = self.add_item("Future", published_hours_ago=-1)
        self.add_score(future, "books-film-music", 0.9)

        digest = build_digest(self.conn, self.settings, PROFILE_ID, now=NOW)

        titles = [item["title"] for item in digest["sections"][0]["entries"]]
        self.assertEqual(titles, ["No date", "Recent"])
        self.assertEqual(digest["window_items"], 2)
        self.assertEqual(digest["classified_items"], 2)
        fallback_item = digest["sections"][0]["entries"][0]
        self.assertEqual(fallback_item["effective_at"], to_utc_iso(NOW - timedelta(hours=1)))

    def test_window_and_per_feed_overrides_limit_candidates(self) -> None:
        for hours in (1, 2, 30, 100):
            self.add_item(f"Item {hours}", published_hours_ago=hours)
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('window_hours', '48')"
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('max_items_per_feed', '2')"
        )
        self.conn.commit()

        digest = build_digest(self.conn, self.settings, PROFILE_ID, now=NOW)

        self.assertEqual(digest["window_hours"], 48)
        self.assertEqual(digest["max_items_per_feed"], 2)
        self.assertEqual(digest["window_items"], 2)
        self.assertEqual(digest["pending_items"], 2)

    def test_filters_disabled_stale_wrong_model_and_old_version(self) -> None:
        valid = self.add_item("Valid")
        self.add_score(valid, "books-film-music", 0.8)

        disabled_feed = self.add_feed("Disabled feed", enabled=False)
        disabled_feed_item = self.add_item("Disabled feed item", feed_id=disabled_feed)
        self.add_score(disabled_feed_item, "books-film-music", 0.9)

        disabled_category = self.conn.execute(
            """
            SELECT id, version_hash FROM categories
            WHERE profile_id = ? AND enabled = 0 LIMIT 1
            """,
            (PROFILE_ID,),
        ).fetchone()
        disabled_item = self.add_item("Disabled category item")
        self.conn.execute(
            """
            INSERT INTO classifications (
                item_id, profile_id, category_id, category_version,
                model, probability, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                disabled_item,
                PROFILE_ID,
                str(disabled_category["id"]),
                str(disabled_category["version_hash"]),
                self.settings.tev_model,
                0.9,
                to_utc_iso(NOW),
            ),
        )
        self.conn.commit()

        wrong_model = self.add_item("Wrong model")
        self.add_score(wrong_model, "books-film-music", 0.9, model="other:1b")

        old_version = self.add_item("Old version")
        self.add_score(old_version, "books-film-music", 0.9, version="old-version")

        stale = self.add_item("Stale", fetched_hours_ago=1)
        self.add_score(
            stale,
            "books-film-music",
            0.9,
            created_at=NOW - timedelta(hours=2),
        )

        digest = build_digest(self.conn, self.settings, PROFILE_ID, now=NOW)

        titles = [item["title"] for item in digest["sections"][0]["entries"]]
        self.assertEqual(titles, ["Valid"])
        self.assertEqual(digest["window_items"], 5)
        self.assertEqual(digest["classified_items"], 1)
        self.assertEqual(digest["pending_items"], 4)
        self.assertEqual(digest["low_confidence"], [])

    def test_low_confidence_sorted_by_best_probability(self) -> None:
        for title, probability in (
            ("Third", 0.4),
            ("First", 0.54),
            ("Second", 0.5),
        ):
            item_id = self.add_item(title)
            self.add_score(item_id, "books-film-music", probability)
            self.add_score(item_id, "cycling-outdoors", 0.2)

        digest = build_digest(self.conn, self.settings, PROFILE_ID, now=NOW)

        self.assertEqual(
            [item["title"] for item in digest["low_confidence"]],
            ["First", "Second", "Third"],
        )

    def test_template_renders_sections_and_low_confidence(self) -> None:
        matched = self.add_item("Matched item")
        self.add_score(matched, "books-film-music", 0.9)
        low = self.add_item("Low item")
        self.add_score(low, "books-film-music", 0.4)
        digest = build_digest(self.conn, self.settings, PROFILE_ID, now=NOW)

        class FakeUrlFor:
            def __call__(self, name, **params):
                if name == "static":
                    return f"/static/{params['path']}"
                return f"/{name}"

        class FakeRequest:
            url_for = FakeUrlFor()

        templates = Path(__file__).resolve().parents[1] / "app" / "templates"
        env = Environment(
            loader=FileSystemLoader(templates),
            autoescape=True,
        )
        env.filters["human_time"] = human_time
        html = env.get_template("digest.html").render(
            request=FakeRequest(),
            profile=digest["profile"],
            profiles=[],
            active_profile_id=PROFILE_ID,
            feeds=[],
            digest=digest,
            job=None,
            settings=self.settings,
            max_new_items=self.settings.max_new_items,
            max_items_per_feed=self.settings.max_items_per_feed,
            last_refreshed_at=None,
        )

        self.assertIn("Books, Film &amp; Music", html)
        self.assertIn("Matched item", html)
        self.assertIn("Low confidence", html)
        self.assertIn("Best match:", html)
        self.assertIn('class="panel digest-section"', html)

    def test_hide_reason_text_setting(self) -> None:
        item = self.add_item("Matched item")
        self.add_score(item, "books-film-music", 0.9)
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('hide_reason_text', '1')"
        )
        self.conn.commit()

        digest = build_digest(self.conn, self.settings, PROFILE_ID, now=NOW)

        self.assertTrue(digest["hide_reason_text"])

    def test_hide_low_confidence_setting(self) -> None:
        low = self.add_item("Low item")
        self.add_score(low, "books-film-music", 0.4)
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('hide_low_confidence', '1')"
        )
        self.conn.commit()

        digest = build_digest(self.conn, self.settings, PROFILE_ID, now=NOW)

        self.assertTrue(digest["hide_low_confidence"])
        self.assertEqual(digest["low_confidence"], [])
        self.assertEqual(digest["low_confidence_count"], 1)


if __name__ == "__main__":
    unittest.main()
