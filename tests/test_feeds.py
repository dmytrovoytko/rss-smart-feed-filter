from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from app.config import Settings
from app.db import connect, init_db
from app.seed import seed_database
from app.services.feeds import (
    MAX_FEED_BYTES,
    FeedError,
    FeedFetchError,
    FeedFetchResult,
    FeedUrlError,
    apply_retention,
    canonicalize_url,
    content_hash,
    count_pending_items,
    fetch_feed,
    parse_feed,
    refresh_profile,
    sanitize_text,
    title_key,
    url_hash,
    validate_feed_url,
)
from app.services.jobs import create_job
from app.timeutil import to_utc_iso

PROFILE_ID = "ds.v1"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
SOURCE_URL = "https://example.com/feed.xml"


def rss_document(entries: list[dict[str, object]]) -> bytes:
    items: list[str] = []
    for entry in entries:
        published = entry["published"]
        assert isinstance(published, datetime)
        items.append(
            "<item>"
            f"<title>{escape(str(entry['title']))}</title>"
            f"<link>{escape(str(entry['link']))}</link>"
            f"<guid>{escape(str(entry.get('guid') or entry['link']))}</guid>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            f"<description><![CDATA[{entry.get('summary', '')}]]></description>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<rss version="2.0"><channel>'
        "<title>Example Feed</title>"
        "<link>https://example.com</link>"
        "<description>Test feed</description>"
        + "".join(items)
        + "</channel></rss>"
    ).encode("utf-8")


class FeedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        self.settings = replace(
            Settings.from_env(),
            db_path=self.db_path,
            log_path=Path(self._tmp.name) / "test.log",
            retention_days=30,
        )
        init_db(self.db_path)
        self.conn = connect(self.db_path)
        self.addCleanup(self.conn.close)
        seed_database(self.conn, self.settings)
        self.conn.execute("DELETE FROM feeds WHERE profile_id = ?", (PROFILE_ID,))
        self.conn.commit()

    def add_feed(self, url: str = SOURCE_URL, title: str = "Example") -> int:
        cursor = self.conn.execute(
            "INSERT INTO feeds (profile_id, url, title) VALUES (?, ?, ?)",
            (PROFILE_ID, url, title),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def fetcher(self, result: FeedFetchResult):
        def fetch(feed: dict[str, object]) -> FeedFetchResult:
            return result

        return fetch

    def test_refresh_ingests_recent_items_and_deduplicates(self) -> None:
        self.add_feed()
        first_document = rss_document(
            [
                {
                    "title": "Post A & more",
                    "link": "https://example.com/posts/a?utm_source=rss&b=2&a=1",
                    "published": NOW - timedelta(days=1),
                    "summary": "<p>Hello <script>bad()</script>world</p>",
                },
                {
                    "title": "Post B",
                    "link": "https://example.com/posts/b",
                    "published": NOW - timedelta(days=2),
                },
                {
                    "title": "Very old",
                    "link": "https://example.com/posts/old",
                    "published": NOW - timedelta(days=40),
                },
            ]
        )
        result = FeedFetchResult(status="ok", url=SOURCE_URL, content=first_document)

        summary = refresh_profile(
            self.conn,
            self.settings,
            PROFILE_ID,
            fetcher=self.fetcher(result),
            now=NOW,
        )

        self.assertEqual(summary["feeds_total"], 1)
        self.assertEqual(summary["feeds_ok"], 1)
        self.assertEqual(summary["feeds_failed"], 0)
        self.assertEqual(summary["new_items"], 2)
        self.assertEqual(summary["skipped_old"], 1)
        self.assertEqual(summary["items_total"], 2)
        self.assertEqual(summary["pending_items"], 2)

        items = self.conn.execute(
            "SELECT title, canonical_url, summary FROM items ORDER BY title"
        ).fetchall()
        self.assertEqual([row["title"] for row in items], ["Post A & more", "Post B"])
        self.assertEqual(
            items[0]["canonical_url"], "https://example.com/posts/a?a=1&b=2"
        )
        self.assertEqual(items[0]["summary"], "Hello world")

        feed = self.conn.execute(
            "SELECT last_status, last_fetched_at FROM feeds WHERE profile_id = ?",
            (PROFILE_ID,),
        ).fetchone()
        self.assertEqual(feed["last_status"], "ok: 2 new, 0 updated")
        self.assertEqual(feed["last_fetched_at"], to_utc_iso(NOW))

        second_document = rss_document(
            [
                {
                    "title": "Post A updated",
                    "link": "https://example.com/posts/a?a=1&b=2",
                    "published": NOW - timedelta(hours=6),
                },
                {
                    "title": "Post B",
                    "link": "https://example.com/posts/b",
                    "published": NOW - timedelta(days=2),
                },
                {
                    "title": "Very old",
                    "link": "https://example.com/posts/old",
                    "published": NOW - timedelta(days=40),
                },
            ]
        )
        second = refresh_profile(
            self.conn,
            self.settings,
            PROFILE_ID,
            fetcher=self.fetcher(
                FeedFetchResult(status="ok", url=SOURCE_URL, content=second_document)
            ),
            now=NOW,
        )

        self.assertEqual(second["new_items"], 0)
        self.assertEqual(second["updated_items"], 1)
        self.assertEqual(second["duplicate_entries"], 1)
        self.assertEqual(second["skipped_old"], 1)
        self.assertEqual(second["items_total"], 2)
        updated = self.conn.execute(
            "SELECT title FROM items WHERE canonical_url = ?",
            ("https://example.com/posts/a?a=1&b=2",),
        ).fetchone()
        self.assertEqual(updated["title"], "Post A updated")

    def test_title_key_dedupe_updates_newest_metadata(self) -> None:
        self.add_feed()
        first_document = rss_document(
            [
                {
                    "title": "Same Story",
                    "link": "https://example.com/one",
                    "published": NOW - timedelta(days=1),
                    "summary": "first version",
                }
            ]
        )
        first = refresh_profile(
            self.conn,
            self.settings,
            PROFILE_ID,
            fetcher=self.fetcher(
                FeedFetchResult(status="ok", url=SOURCE_URL, content=first_document)
            ),
            now=NOW,
        )
        self.assertEqual(first["new_items"], 1)

        second_document = rss_document(
            [
                {
                    "title": "Same Story!",
                    "link": "https://example.com/two",
                    "published": NOW - timedelta(hours=12),
                    "summary": "newest version",
                }
            ]
        )
        second = refresh_profile(
            self.conn,
            self.settings,
            PROFILE_ID,
            fetcher=self.fetcher(
                FeedFetchResult(status="ok", url=SOURCE_URL, content=second_document)
            ),
            now=NOW,
        )

        self.assertEqual(second["new_items"], 0)
        self.assertEqual(second["updated_items"], 1)
        self.assertEqual(second["items_total"], 1)
        item = self.conn.execute(
            "SELECT canonical_url, title, summary FROM items"
        ).fetchone()
        self.assertEqual(item["canonical_url"], "https://example.com/two")
        self.assertEqual(item["title"], "Same Story!")
        self.assertEqual(item["summary"], "newest version")

    def test_not_modified_updates_status_without_items(self) -> None:
        self.add_feed()
        summary = refresh_profile(
            self.conn,
            self.settings,
            PROFILE_ID,
            fetcher=self.fetcher(
                FeedFetchResult(status="not_modified", url=SOURCE_URL)
            ),
            now=NOW,
        )

        self.assertEqual(summary["feeds_not_modified"], 1)
        self.assertEqual(summary["feeds_ok"], 0)
        self.assertEqual(summary["items_total"], 0)
        feed = self.conn.execute(
            "SELECT last_status, last_fetched_at FROM feeds WHERE profile_id = ?",
            (PROFILE_ID,),
        ).fetchone()
        self.assertEqual(feed["last_status"], "not modified")
        self.assertEqual(feed["last_fetched_at"], to_utc_iso(NOW))

    def test_one_feed_failure_does_not_abort_the_refresh(self) -> None:
        good_url = "https://example.com/good.xml"
        bad_url = "https://example.com/bad.xml"
        crash_url = "https://example.com/crash.xml"
        self.add_feed(good_url, "Good")
        self.add_feed(bad_url, "Bad")
        self.add_feed(crash_url, "Crash")
        good_document = rss_document(
            [
                {
                    "title": "Good post",
                    "link": "https://example.com/good-post",
                    "published": NOW - timedelta(hours=1),
                }
            ]
        )
        results = {
            good_url: FeedFetchResult(status="ok", url=good_url, content=good_document),
            bad_url: FeedFetchResult(
                status="ok", url=bad_url, content=b"this is not a feed"
            ),
        }

        def fetch(feed: dict[str, object]) -> FeedFetchResult:
            url = str(feed["url"])
            if url == crash_url:
                raise RuntimeError("unexpected network failure")
            return results[url]

        with self.assertLogs("app.services.feeds", level="WARNING"):
            summary = refresh_profile(
                self.conn,
                self.settings,
                PROFILE_ID,
                fetcher=fetch,
                now=NOW,
            )

        self.assertEqual(summary["feeds_total"], 3)
        self.assertEqual(summary["feeds_ok"], 1)
        self.assertEqual(summary["feeds_failed"], 2)
        self.assertEqual(len(summary["errors"]), 2)
        self.assertEqual(summary["new_items"], 1)

        statuses = {
            row["url"]: row["last_status"]
            for row in self.conn.execute(
                "SELECT url, last_status FROM feeds WHERE profile_id = ?",
                (PROFILE_ID,),
            )
        }
        self.assertTrue(statuses[good_url].startswith("ok:"))
        self.assertTrue(statuses[bad_url].startswith("parse error:"))
        self.assertEqual(statuses[crash_url], "error: unexpected network failure")

    def test_refresh_without_enabled_feeds_raises(self) -> None:
        with self.assertRaises(FeedError):
            refresh_profile(self.conn, self.settings, PROFILE_ID, now=NOW)

    def test_retention_and_pending_counts(self) -> None:
        feed_id = self.add_feed()
        recent_url = "https://example.com/recent"
        old_url = "https://example.com/old"
        self.conn.execute(
            """
            INSERT INTO items (
                feed_id, canonical_url, url_hash, title, summary,
                published_at, fetched_at, content_hash
            ) VALUES (?, ?, ?, ?, '', ?, ?, ?)
            """,
            (
                feed_id,
                recent_url,
                url_hash(recent_url),
                "Recent",
                to_utc_iso(NOW - timedelta(days=1)),
                to_utc_iso(NOW),
                content_hash("Recent"),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO items (
                feed_id, canonical_url, url_hash, title, summary,
                published_at, fetched_at, content_hash
            ) VALUES (?, ?, ?, ?, '', ?, ?, ?)
            """,
            (
                feed_id,
                old_url,
                url_hash(old_url),
                "Old",
                to_utc_iso(NOW - timedelta(days=40)),
                to_utc_iso(NOW),
                content_hash("Old"),
            ),
        )
        job_id = create_job(
            self.conn,
            kind="refresh",
            profile_id=PROFILE_ID,
            total=1,
        )
        self.conn.execute(
            "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
            (to_utc_iso(NOW - timedelta(days=40)), to_utc_iso(NOW), job_id),
        )
        self.conn.commit()

        self.assertEqual(count_pending_items(self.conn, PROFILE_ID, "test"), 2)
        categories = self.conn.execute(
            "SELECT id, version_hash FROM categories WHERE profile_id = ? AND enabled = 1",
            (PROFILE_ID,),
        ).fetchall()
        recent_id = self.conn.execute(
            "SELECT id FROM items WHERE canonical_url = ?", (recent_url,)
        ).fetchone()[0]
        for category in categories:
            self.conn.execute(
                """
                INSERT INTO classifications (
                    item_id, profile_id, category_id, category_version,
                    model, probability, created_at
                ) VALUES (?, ?, ?, ?, 'test', 0.9, ?)
                """,
                (
                    recent_id,
                    PROFILE_ID,
                    category["id"],
                    category["version_hash"],
                    to_utc_iso(NOW),
                ),
            )
        self.conn.commit()
        self.assertEqual(count_pending_items(self.conn, PROFILE_ID, "test"), 1)

        deleted = apply_retention(self.conn, self.settings, now=NOW)

        self.assertEqual(deleted["items_deleted"], 1)
        self.assertEqual(deleted["jobs_deleted"], 1)
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM items WHERE canonical_url = ?", (old_url,)
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM items WHERE canonical_url = ?", (recent_url,)
            ).fetchone()
        )


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.chunks = chunks or []
        self.closed = False

    def iter_content(self, chunk_size: int):
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


class FetchTests(unittest.TestCase):
    URL = "http://8.8.8.8/feed.xml"

    def test_conditional_get_returns_not_modified(self) -> None:
        response = FakeResponse(
            status_code=304,
            headers={"ETag": '"abc"', "Last-Modified": "Wed, 21 Oct 2026 07:28:00 GMT"},
        )
        session = FakeSession([response])

        result = fetch_feed(
            self.URL,
            etag='"old"',
            last_modified="Tue, 20 Oct 2026 07:28:00 GMT",
            session=session,
        )

        self.assertEqual(result.status, "not_modified")
        self.assertEqual(result.etag, '"abc"')
        self.assertTrue(response.closed)
        headers = session.calls[0][1]["headers"]
        self.assertEqual(headers["If-None-Match"], '"old"')
        self.assertEqual(
            headers["If-Modified-Since"], "Tue, 20 Oct 2026 07:28:00 GMT"
        )

    def test_fetch_follows_public_redirect_and_reads_content(self) -> None:
        redirect = FakeResponse(status_code=301, headers={"location": "/new-feed"})
        final = FakeResponse(status_code=200, chunks=[b"<rss>ok</rss>"])
        session = FakeSession([redirect, final])

        result = fetch_feed(self.URL, session=session)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.url, "http://8.8.8.8/new-feed")
        self.assertEqual(result.content, b"<rss>ok</rss>")
        self.assertTrue(redirect.closed)
        self.assertTrue(final.closed)

    def test_fetch_rejects_redirect_to_private_address(self) -> None:
        redirect = FakeResponse(
            status_code=302,
            headers={"location": "http://127.0.0.1/feed.xml"},
        )
        session = FakeSession([redirect])

        with self.assertRaises(FeedUrlError):
            fetch_feed(self.URL, session=session)

        self.assertEqual(len(session.calls), 1)
        self.assertTrue(redirect.closed)

    def test_fetch_rejects_http_error_and_oversized_response(self) -> None:
        error = FakeResponse(status_code=500)
        oversized = FakeResponse(
            status_code=200,
            headers={"content-length": str(MAX_FEED_BYTES + 1)},
        )

        with self.assertRaises(FeedFetchError):
            fetch_feed(self.URL, session=FakeSession([error]))
        self.assertTrue(error.closed)

        with self.assertRaises(FeedFetchError):
            fetch_feed(self.URL, session=FakeSession([oversized]))
        self.assertTrue(oversized.closed)


class FeedUtilityTests(unittest.TestCase):
    def test_canonicalize_url_removes_tracking_and_normalizes(self) -> None:
        self.assertEqual(
            canonicalize_url(
                "HTTPS://Example.COM:443/Path/?utm_source=rss&b=2&a=1#fragment"
            ),
            "https://example.com/Path?a=1&b=2",
        )
        self.assertEqual(
            canonicalize_url("http://example.com:80/"),
            "http://example.com",
        )
        with self.assertRaises(ValueError):
            canonicalize_url("ftp://example.com/feed")

    def test_validate_feed_url_rejects_local_and_non_http_targets(self) -> None:
        for url in (
            "http://127.0.0.1/feed",
            "http://localhost/feed",
            "http://[::1]/feed",
            "ftp://example.com/feed",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(FeedUrlError):
                validate_feed_url(url)
        self.assertEqual(
            validate_feed_url("http://8.8.8.8/feed"),
            "http://8.8.8.8/feed",
        )

    def test_sanitize_text_strips_markup_and_truncates(self) -> None:
        self.assertEqual(
            sanitize_text("<p>Hello&nbsp;<b>world</b></p><script>bad()</script>"),
            "Hello world",
        )
        self.assertEqual(sanitize_text(None), "")
        self.assertEqual(sanitize_text("   "), "")
        clipped = sanitize_text("word " * 20, max_chars=10)
        self.assertLessEqual(len(clipped), 10)
        self.assertFalse(clipped.endswith(" "))

    def test_title_keys_and_content_hashes_ignore_punctuation(self) -> None:
        self.assertEqual(title_key("  Hello, WORLD!  "), "hello world")
        self.assertEqual(
            content_hash("Hello, WORLD!"),
            content_hash("hello world"),
        )
        self.assertEqual(url_hash("https://example.com/a"), url_hash("https://example.com/a"))
        self.assertNotEqual(url_hash("https://example.com/a"), url_hash("https://example.com/b"))

    def test_parse_feed_uses_source_url_for_relative_links(self) -> None:
        document = rss_document(
            [
                {
                    "title": "Relative",
                    "link": "/posts/relative?utm_campaign=x",
                    "published": NOW,
                }
            ]
        )
        parsed = parse_feed(document, "https://example.com/feed.xml")
        self.assertEqual(parsed.title, "Example Feed")
        self.assertEqual(len(parsed.entries), 1)
        self.assertEqual(
            parsed.entries[0].canonical_url,
            "https://example.com/posts/relative",
        )


if __name__ == "__main__":
    unittest.main()
