from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from app.config import Settings
from app.db import connect, init_db
from app.seed import seed_database
from app.services.classify import (
    ClassificationError,
    OllamaClient,
    OllamaModelMissingError,
    OllamaRejectedError,
    OllamaRequestError,
    OllamaUnavailableError,
    build_questions,
    build_state,
    classify_pending_items,
    count_pending_items,
    normalize_probability,
    parse_answers,
)
from app.services.feeds import content_hash, url_hash
from app.services.preferences import set_max_new_items
from app.timeutil import to_utc_iso

PROFILE_ID = "ds.v1"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
FEED_URL = "https://example.com/feed.xml"


class StubResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        text: str = "",
        json_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text
        self.json_error = json_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class StubSession:
    def __init__(
        self,
        get_responses: list[Any] | None = None,
        post_responses: list[Any] | None = None,
    ) -> None:
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> Any:
        self.get_calls.append((url, kwargs))
        response = self.get_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url: str, **kwargs: Any) -> Any:
        self.post_calls.append((url, kwargs))
        response = self.post_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


class FakeClassifier:
    def __init__(self, probability: float = 0.9, fail_on_call: int | None = None) -> None:
        self.probability = probability
        self.fail_on_call = fail_on_call
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.preflight_calls = 0

    def preflight(self) -> None:
        self.preflight_calls += 1

    def decide(self, state: str, questions: dict[str, Any]) -> dict[str, float]:
        self.calls.append((state, questions))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise ClassificationError("model exploded")
        return {category_id: self.probability for category_id in questions}


class ClassifyTestCase(unittest.TestCase):
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
        self.conn.execute("DELETE FROM feeds WHERE profile_id = ?", (PROFILE_ID,))
        self.conn.execute(
            "INSERT INTO feeds (profile_id, url, title) VALUES (?, ?, 'Example')",
            (PROFILE_ID, FEED_URL),
        )
        self.conn.commit()
        feed_row = self.conn.execute(
            "SELECT id FROM feeds WHERE profile_id = ?", (PROFILE_ID,)
        ).fetchone()
        self.feed_id = int(feed_row[0])
        self.categories = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT id, name, description, criteria_true, criteria_false, version_hash
                FROM categories
                WHERE profile_id = ? AND enabled = 1
                ORDER BY position, id
                """,
                (PROFILE_ID,),
            )
        ]

    def add_item(
        self,
        title: str,
        summary: str = "",
        *,
        published: datetime | None = None,
        slug: str | None = None,
    ) -> int:
        url = f"https://example.com/{slug or title.lower().replace(' ', '-')}"
        cursor = self.conn.execute(
            """
            INSERT INTO items (
                feed_id, canonical_url, url_hash, title, summary,
                published_at, fetched_at, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.feed_id,
                url,
                url_hash(url),
                title,
                summary,
                to_utc_iso(published or NOW),
                to_utc_iso(NOW),
                content_hash(title),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def pending_count(self, model: str | None = None, *, now: datetime | None = None) -> int:
        now = now or NOW
        return count_pending_items(
            self.conn,
            PROFILE_ID,
            model or self.settings.tev_model,
            window_start=to_utc_iso(now - timedelta(hours=self.settings.window_hours)),
            window_end=to_utc_iso(now),
            max_items_per_feed=self.settings.max_items_per_feed,
        )

    def test_build_state_uses_title_summary_and_truncates_on_word_boundary(self) -> None:
        self.assertEqual(
            build_state({"title": "Title", "summary": "Summary"}),
            "Title\n\nSummary",
        )
        state = build_state({"title": "alpha beta gamma delta", "summary": ""}, max_chars=12)
        self.assertEqual(state, "alpha beta")
        self.assertLessEqual(len(state), 12)
        self.assertEqual(build_state({"title": "", "summary": ""}), "")

    def test_build_questions_matches_contract(self) -> None:
        questions = build_questions(self.categories[:2])
        self.assertEqual(set(questions), {c["id"] for c in self.categories[:2]})
        first = self.categories[0]
        self.assertEqual(questions[first["id"]]["type"], "noul")
        self.assertIn(first["name"], questions[first["id"]]["instructions"])
        self.assertEqual(
            questions[first["id"]]["criteria"],
            {"true": first["criteria_true"], "false": first["criteria_false"]},
        )

    def test_normalize_probability_accepts_expected_shapes(self) -> None:
        self.assertEqual(normalize_probability(True), 1.0)
        self.assertEqual(normalize_probability(False), 0.0)
        self.assertEqual(normalize_probability(0.25), 0.25)
        self.assertEqual(normalize_probability({"noul": 0.83}), 0.83)
        self.assertEqual(normalize_probability({"probability": 0.4}), 0.4)
        self.assertEqual(normalize_probability(1.2), 1.0)
        self.assertEqual(normalize_probability(-0.2), 0.0)
        with self.assertRaises(ValueError):
            normalize_probability("0.5")
        with self.assertRaises(ValueError):
            normalize_probability(float("nan"))

    def test_parse_answers_requires_every_question(self) -> None:
        parsed = parse_answers(
            {"answers": {"a": {"noul": 0.7}, "b": {"noul": 0.2}}},
            ["a", "b"],
        )
        self.assertEqual(parsed, {"a": 0.7, "b": 0.2})
        with self.assertRaises(ClassificationError):
            parse_answers({"answers": {"a": {"noul": 0.7}}}, ["a", "b"])

    def test_classify_pending_items_stores_all_category_scores(self) -> None:
        item_id = self.add_item("First item", "Summary text", published=NOW - timedelta(hours=1))
        client = FakeClassifier(probability=0.82)

        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=client,
            now=NOW,
        )

        self.assertEqual(client.preflight_calls, 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], "First item\n\nSummary text")
        self.assertEqual(
            set(client.calls[0][1]),
            {category["id"] for category in self.categories},
        )
        self.assertEqual(summary["classified_items"], 1)
        self.assertEqual(summary["classified_categories"], len(self.categories))
        self.assertEqual(summary["pending_before"], 1)
        self.assertEqual(summary["pending_items"], 0)
        self.assertEqual(summary["unprocessed_items"], 0)
        self.assertEqual(summary["requests"], 1)

        rows = self.conn.execute(
            "SELECT category_id, category_version, model, probability, created_at "
            "FROM classifications WHERE item_id = ?",
            (item_id,),
        ).fetchall()
        self.assertEqual(len(rows), len(self.categories))
        self.assertEqual({row["model"] for row in rows}, {self.settings.tev_model})
        self.assertEqual({row["probability"] for row in rows}, {0.82})
        self.assertEqual({row["created_at"] for row in rows}, {to_utc_iso(NOW)})
        self.assertEqual(self.pending_count(), 0)

    def test_no_pending_items_skips_preflight_and_model_calls(self) -> None:
        self.add_item("Already done", published=NOW - timedelta(hours=1))
        first = FakeClassifier()
        classify_pending_items(self.conn, self.settings, PROFILE_ID, client=first, now=NOW)

        second = FakeClassifier()
        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=second,
            now=NOW,
        )

        self.assertEqual(second.preflight_calls, 0)
        self.assertEqual(second.calls, [])
        self.assertEqual(summary["selected_items"], 0)
        self.assertEqual(summary["pending_items"], 0)

    def test_retry_only_requests_missing_category_versions(self) -> None:
        item_id = self.add_item("Partial item", published=NOW - timedelta(hours=1))
        classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=FakeClassifier(),
            now=NOW,
        )
        missing = self.categories[0]
        self.conn.execute(
            "DELETE FROM classifications WHERE item_id = ? AND category_id = ?",
            (item_id, missing["id"]),
        )
        self.conn.commit()

        client = FakeClassifier()
        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=client,
            now=NOW,
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(set(client.calls[0][1]), {missing["id"]})
        self.assertEqual(summary["classified_items"], 1)
        self.assertEqual(summary["classified_categories"], 1)
        self.assertEqual(summary["pending_items"], 0)

    def test_model_change_reclassifies_without_deleting_old_rows(self) -> None:
        item_id = self.add_item("Model change", published=NOW - timedelta(hours=1))
        classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=FakeClassifier(),
            now=NOW,
        )
        old_count = self.conn.execute(
            "SELECT COUNT(*) FROM classifications WHERE item_id = ?", (item_id,)
        ).fetchone()[0]

        new_settings = replace(self.settings, tev_model="tev1:4b")
        summary = classify_pending_items(
            self.conn,
            new_settings,
            PROFILE_ID,
            client=FakeClassifier(probability=0.6),
            now=NOW,
        )

        self.assertEqual(summary["pending_before"], 1)
        self.assertEqual(summary["classified_items"], 1)
        self.assertEqual(
            self.pending_count(new_settings.tev_model),
            0,
        )
        new_count = self.conn.execute(
            "SELECT COUNT(*) FROM classifications WHERE item_id = ?", (item_id,)
        ).fetchone()[0]
        self.assertEqual(new_count, old_count + len(self.categories))
        models = {
            row["model"]
            for row in self.conn.execute(
                "SELECT model FROM classifications WHERE item_id = ?", (item_id,)
            )
        }
        self.assertEqual(models, {self.settings.tev_model, new_settings.tev_model})

    def test_item_metadata_refresh_invalidates_existing_scores(self) -> None:
        item_id = self.add_item("Stale item", published=NOW - timedelta(hours=1))
        classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=FakeClassifier(probability=0.9),
            now=NOW,
        )
        refreshed_at = NOW + timedelta(minutes=1)
        self.conn.execute(
            "UPDATE items SET fetched_at = ? WHERE id = ?",
            (to_utc_iso(refreshed_at), item_id),
        )
        self.conn.commit()

        self.assertEqual(self.pending_count(), 1)
        client = FakeClassifier(probability=0.7)
        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=client,
            now=NOW + timedelta(minutes=2),
        )

        self.assertEqual(summary["classified_items"], 1)
        self.assertEqual(len(client.calls), 1)
        probabilities = {
            row["probability"]
            for row in self.conn.execute(
                "SELECT probability FROM classifications WHERE item_id = ?",
                (item_id,),
            )
        }
        self.assertEqual(probabilities, {0.7})

    def test_window_override_excludes_older_items(self) -> None:
        self.add_item("Recent", published=NOW - timedelta(hours=1), slug="recent")
        self.add_item("Older", published=NOW - timedelta(hours=100), slug="older")
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('window_hours', '48')"
        )
        self.conn.commit()

        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=FakeClassifier(),
            now=NOW,
        )

        self.assertEqual(summary["window_hours"], 48)
        self.assertEqual(summary["pending_before"], 1)
        self.assertEqual(summary["selected_items"], 1)
        self.assertEqual(summary["classified_items"], 1)

    def test_limit_classifies_newest_and_reports_backlog(self) -> None:
        oldest = self.add_item("Oldest", published=NOW - timedelta(days=3), slug="oldest")
        middle = self.add_item("Middle", published=NOW - timedelta(days=2), slug="middle")
        newest = self.add_item("Newest", published=NOW - timedelta(days=1), slug="newest")
        progress: list[tuple[int, int]] = []
        client = FakeClassifier()

        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=client,
            limit=2,
            on_progress=lambda processed, total: progress.append((processed, total)),
            now=NOW,
        )

        self.assertEqual(summary["pending_before"], 3)
        self.assertEqual(summary["selected_items"], 2)
        self.assertEqual(summary["classified_items"], 2)
        self.assertEqual(summary["pending_items"], 1)
        self.assertEqual(summary["unprocessed_items"], 1)
        self.assertEqual(progress, [(0, 2), (1, 2), (2, 2)])
        classified_ids = {
            row["item_id"]
            for row in self.conn.execute("SELECT DISTINCT item_id FROM classifications")
        }
        self.assertEqual(classified_ids, {newest, middle})
        self.assertNotIn(oldest, classified_ids)

    def test_settings_limit_overrides_environment_default(self) -> None:
        for index in range(3):
            self.add_item(f"Item {index}", published=NOW - timedelta(hours=index + 1))

        set_max_new_items(self.conn, 2)
        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=FakeClassifier(),
            now=NOW,
        )

        self.assertEqual(summary["pending_before"], 3)
        self.assertEqual(summary["selected_items"], 2)
        self.assertEqual(summary["classified_items"], 2)
        self.assertEqual(summary["unprocessed_items"], 1)

    def test_failure_does_not_persist_partial_classifications(self) -> None:
        self.add_item("First", published=NOW - timedelta(hours=2), slug="first")
        self.add_item("Second", published=NOW - timedelta(hours=1), slug="second")
        client = FakeClassifier(fail_on_call=2)

        with self.assertRaises(ClassificationError):
            classify_pending_items(
                self.conn,
                self.settings,
                PROFILE_ID,
                client=client,
                limit=2,
                now=NOW,
            )

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0],
            0,
        )
        self.assertEqual(len(client.calls), 2)

    def test_stop_keeps_completed_classifications_and_leaves_remaining_pending(
        self,
    ) -> None:
        self.add_item("First", published=NOW - timedelta(hours=2), slug="first")
        newest = self.add_item(
            "Second",
            published=NOW - timedelta(hours=1),
            slug="second",
        )
        client = FakeClassifier()

        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=client,
            limit=2,
            should_stop=lambda: len(client.calls) >= 1,
            now=NOW,
        )

        self.assertTrue(summary["stopped"])
        self.assertEqual(summary["classified_items"], 1)
        self.assertEqual(summary["pending_items"], 1)
        self.assertEqual(summary["pending_after"], 1)
        self.assertEqual(len(client.calls), 1)
        classified_ids = {
            row["item_id"]
            for row in self.conn.execute("SELECT DISTINCT item_id FROM classifications")
        }
        self.assertEqual(classified_ids, {newest})

    def test_no_enabled_categories_is_a_noop(self) -> None:
        self.add_item("Uncategorized", published=NOW - timedelta(hours=1))
        self.conn.execute("UPDATE categories SET enabled = 0 WHERE profile_id = ?", (PROFILE_ID,))
        self.conn.commit()
        client = FakeClassifier()

        summary = classify_pending_items(
            self.conn,
            self.settings,
            PROFILE_ID,
            client=client,
            now=NOW,
        )

        self.assertEqual(summary["categories"], 0)
        self.assertEqual(summary["pending_before"], 0)
        self.assertEqual(summary["classified_items"], 0)
        self.assertEqual(client.preflight_calls, 0)
        self.assertEqual(client.calls, [])


class OllamaClientTests(unittest.TestCase):
    def client(self, session: StubSession, *, retries: int = 0) -> OllamaClient:
        return OllamaClient(
            "http://127.0.0.1:11434",
            "tev1:0.8b",
            retries=retries,
            session=session,
            sleep=lambda _: None,
        )

    def test_preflight_accepts_installed_model(self) -> None:
        session = StubSession(
            get_responses=[
                StubResponse(payload={"version": "0.9"}),
                StubResponse(payload={"models": [{"name": "tev1:0.8b"}]}),
            ]
        )
        self.client(session).preflight()
        self.assertEqual(len(session.get_calls), 2)

    def test_preflight_reports_missing_model_and_unreachable_server(self) -> None:
        missing = StubSession(
            get_responses=[
                StubResponse(payload={"version": "0.9"}),
                StubResponse(payload={"models": [{"name": "other:latest"}]}),
            ]
        )
        with self.assertRaises(OllamaModelMissingError) as ctx:
            self.client(missing).preflight()
        self.assertIn("ollama pull tev1:0.8b", str(ctx.exception))

        unreachable = StubSession(get_responses=[requests.ConnectionError("refused")])
        with self.assertRaises(OllamaUnavailableError) as ctx:
            self.client(unreachable).preflight()
        self.assertIn("ollama serve", str(ctx.exception))

    def test_decide_sends_contract_payload_and_parses_answers(self) -> None:
        session = StubSession(
            post_responses=[
                StubResponse(payload={"answers": {"a": {"noul": 0.83}, "b": {"noul": 0.2}}})
            ]
        )
        questions = {"a": {"type": "noul"}, "b": {"type": "noul"}}

        result = self.client(session).decide("state text", questions)

        self.assertEqual(result, {"a": 0.83, "b": 0.2})
        url, kwargs = session.post_calls[0]
        self.assertEqual(url, "http://127.0.0.1:11434/v1/systemone")
        self.assertEqual(kwargs["json"]["model"], "tev1:0.8b")
        self.assertEqual(kwargs["json"]["state"], "state text")
        self.assertEqual(kwargs["json"]["questions"], questions)
        self.assertEqual(kwargs["json"]["keep_alive"], "30m")

    def test_decide_retries_transient_errors(self) -> None:
        session = StubSession(
            post_responses=[
                StubResponse(status_code=503, text="busy"),
                StubResponse(payload={"answers": {"a": {"noul": 0.5}}}),
            ]
        )
        result = self.client(session, retries=1).decide("state", {"a": {"type": "noul"}})
        self.assertEqual(result, {"a": 0.5})
        self.assertEqual(len(session.post_calls), 2)

    def test_decide_raises_actionable_errors(self) -> None:
        missing = StubSession(post_responses=[StubResponse(status_code=404, text="not found")])
        with self.assertRaises(OllamaModelMissingError):
            self.client(missing).decide("state", {"a": {"type": "noul"}})

        rejected = StubSession(
            post_responses=[StubResponse(status_code=400, text="bad request")]
        )
        with self.assertRaises(OllamaRejectedError):
            self.client(rejected).decide("state", {"a": {"type": "noul"}})
        self.assertEqual(len(rejected.post_calls), 1)

        failed = StubSession(
            post_responses=[
                requests.ConnectionError("refused"),
                requests.ConnectionError("refused"),
            ]
        )
        with self.assertRaises(OllamaRequestError) as ctx:
            self.client(failed, retries=1).decide("state", {"a": {"type": "noul"}})
        self.assertIn("ollama serve", str(ctx.exception))
        self.assertEqual(len(failed.post_calls), 2)

    def test_decide_retries_invalid_response(self) -> None:
        session = StubSession(
            post_responses=[
                StubResponse(payload={"wrong": "shape"}),
                StubResponse(payload={"answers": {"a": {"noul": 0.4}}}),
            ]
        )
        result = self.client(session, retries=1).decide("state", {"a": {"type": "noul"}})
        self.assertEqual(result, {"a": 0.4})
        self.assertEqual(len(session.post_calls), 2)


if __name__ == "__main__":
    unittest.main()
