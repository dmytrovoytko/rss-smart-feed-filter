from __future__ import annotations

import logging
import math
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import requests

from app.config import Settings
from app.services.candidates import RANKED_CANDIDATES_CTE, window_bounds
from app.services.preferences import (
    get_max_items_per_feed,
    get_max_new_items,
    get_window_hours,
)
from app.timeutil import to_utc_iso

log = logging.getLogger(__name__)

MAX_STATE_CHARS = 1200
MAX_QUESTIONS = 64
KEEP_ALIVE = "30m"
DEFAULT_TIMEOUT = 120.0
DEFAULT_RETRIES = 2
RETRY_STATUSES = {429, 500, 502, 503, 504}
ProgressCallback = Callable[[int, int], None]
StopCheck = Callable[[], bool]


class ClassificationError(RuntimeError):
    """A classification job could not be completed."""


class OllamaUnavailableError(ClassificationError):
    """Ollama could not be reached."""


class OllamaModelMissingError(ClassificationError):
    """The configured model is not installed."""


class OllamaRejectedError(ClassificationError):
    """Ollama rejected a request without it being retryable."""


class OllamaResponseError(ClassificationError):
    """Ollama returned an invalid or incomplete response."""


class OllamaRequestError(ClassificationError):
    """A request to Ollama failed after retries."""


def build_state(item: Mapping[str, Any], max_chars: int = MAX_STATE_CHARS) -> str:
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    state = f"{title}\n\n{summary}".strip()
    if max_chars <= 0:
        return ""
    if len(state) <= max_chars:
        return state
    clipped = state[:max_chars]
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped.rstrip() or state[:max_chars]


def build_questions(categories: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    questions: dict[str, dict[str, Any]] = {}
    for category in categories:
        category_id = str(category["id"])
        questions[category_id] = {
            "type": "noul",
            "instructions": (
                f"Does this item focus on {category['name']}? "
                f"{category['description']}"
            ),
            "criteria": {
                "true": str(category["criteria_true"]),
                "false": str(category["criteria_false"]),
            },
        }
    return questions


def normalize_probability(answer: Any) -> float:
    if isinstance(answer, bool):
        value = 1.0 if answer else 0.0
    elif isinstance(answer, (int, float)):
        value = float(answer)
    elif isinstance(answer, Mapping):
        for key in ("noul", "probability", "score"):
            if key in answer:
                return normalize_probability(answer[key])
        raise ValueError(f"unsupported answer shape: {answer!r}")
    else:
        raise ValueError(f"unsupported answer shape: {answer!r}")

    if not math.isfinite(value):
        raise ValueError(f"probability must be finite: {answer!r}")
    return min(1.0, max(0.0, value))


def parse_answers(body: Any, requested_ids: Sequence[str]) -> dict[str, float]:
    if not isinstance(body, Mapping):
        raise OllamaResponseError(f"Ollama returned a non-object response: {body!r}")
    answers = body.get("answers")
    if not isinstance(answers, Mapping):
        raise OllamaResponseError("Ollama response is missing the answers object")

    missing = [category_id for category_id in requested_ids if category_id not in answers]
    if missing:
        raise OllamaResponseError(
            "Ollama response is missing categories: " + ", ".join(sorted(missing))
        )

    parsed: dict[str, float] = {}
    for category_id in requested_ids:
        try:
            parsed[category_id] = normalize_probability(answers[category_id])
        except (TypeError, ValueError) as exc:
            raise OllamaResponseError(
                f"invalid answer for category {category_id!r}: {exc}"
            ) from exc
    return parsed


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retries = max(0, retries)
        self.session = session or requests.Session()
        self._owns_session = session is None
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def preflight(self) -> None:
        timeout = min(10.0, self.timeout)
        try:
            version_response = self.session.get(
                f"{self.base_url}/api/version", timeout=timeout
            )
            version_response.raise_for_status()
            tags_response = self.session.get(f"{self.base_url}/api/tags", timeout=timeout)
            tags_response.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. "
                "Start it with: ollama serve"
            ) from exc

        try:
            body = tags_response.json()
            models = body.get("models", []) if isinstance(body, Mapping) else []
            names = {
                str(model.get("name"))
                for model in models
                if isinstance(model, Mapping) and model.get("name")
            }
        except (TypeError, ValueError) as exc:
            raise OllamaResponseError(f"Ollama returned an invalid model list: {exc}") from exc

        if self.model not in names and f"{self.model}:latest" not in names:
            raise OllamaModelMissingError(
                f"Model {self.model} is not installed. "
                f"Run: ollama pull {self.model}"
            )

    def decide(
        self,
        state: str,
        questions: Mapping[str, Any],
    ) -> dict[str, float]:
        payload = {
            "model": self.model,
            "state": state,
            "questions": questions,
            "keep_alive": KEEP_ALIVE,
        }
        requested_ids = list(questions)
        last_error: str | None = None

        for attempt in range(self.retries + 1):
            try:
                response = self.session.post(
                    f"{self.base_url}/v1/systemone",
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                if attempt < self.retries:
                    self._sleep(1.5 * (attempt + 1))
                continue

            if response.status_code == 404:
                raise OllamaModelMissingError(
                    f"Model {self.model} is not installed or the endpoint is unavailable. "
                    f"Run: ollama pull {self.model}"
                )
            if response.status_code in RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if attempt < self.retries:
                    self._sleep(1.5 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise OllamaRejectedError(
                    f"Ollama rejected the request (HTTP {response.status_code}): "
                    f"{response.text[:300]}"
                )

            try:
                body = response.json()
                return parse_answers(body, requested_ids)
            except (OllamaResponseError, ValueError) as exc:
                last_error = str(exc)
                if attempt < self.retries:
                    self._sleep(1.5 * (attempt + 1))

        raise OllamaRequestError(
            f"Ollama request failed after {self.retries + 1} attempts: {last_error}. "
            "Check that Ollama is running (`ollama serve`) and retry."
        )


def count_pending_items(
    conn: sqlite3.Connection,
    profile_id: str,
    model: str,
    *,
    window_start: str,
    window_end: str,
    max_items_per_feed: int,
) -> int:
    row = conn.execute(
        RANKED_CANDIDATES_CTE
        + """
        SELECT COUNT(*)
        FROM ranked_items i
        WHERE i.feed_rank <= ?
          AND EXISTS (
              SELECT 1
              FROM categories c
              WHERE c.profile_id = ?
                AND c.enabled = 1
                AND NOT EXISTS (
                    SELECT 1
                    FROM classifications cl
                    WHERE cl.item_id = i.id
                      AND cl.profile_id = c.profile_id
                      AND cl.category_id = c.id
                      AND cl.category_version = c.version_hash
                      AND cl.model = ?
                      AND cl.created_at >= i.fetched_at
                )
          )
        """,
        (
            profile_id,
            window_start,
            window_end,
            max_items_per_feed,
            profile_id,
            model,
        ),
    ).fetchone()
    return int(row[0])


def _load_enabled_categories(
    conn: sqlite3.Connection,
    profile_id: str,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, name, description, criteria_true, criteria_false, version_hash
            FROM categories
            WHERE profile_id = ? AND enabled = 1
            ORDER BY position, id
            """,
            (profile_id,),
        )
    ]


def _select_pending_items(
    conn: sqlite3.Connection,
    profile_id: str,
    model: str,
    limit: int,
    *,
    window_start: str,
    window_end: str,
    max_items_per_feed: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    return [
        dict(row)
        for row in conn.execute(
            RANKED_CANDIDATES_CTE
            + """
            SELECT i.id, i.title, i.summary, i.published_at, i.fetched_at
            FROM ranked_items i
            WHERE i.feed_rank <= ?
              AND EXISTS (
                  SELECT 1
                  FROM categories c
                  WHERE c.profile_id = ?
                    AND c.enabled = 1
                    AND NOT EXISTS (
                        SELECT 1
                        FROM classifications cl
                        WHERE cl.item_id = i.id
                          AND cl.profile_id = c.profile_id
                          AND cl.category_id = c.id
                          AND cl.category_version = c.version_hash
                          AND cl.model = ?
                          AND cl.created_at >= i.fetched_at
                    )
              )
            ORDER BY COALESCE(i.published_at, i.fetched_at) DESC, i.id DESC
            LIMIT ?
            """,
            (
                profile_id,
                window_start,
                window_end,
                max_items_per_feed,
                profile_id,
                model,
                limit,
            ),
        )
    ]


def _missing_categories(
    conn: sqlite3.Connection,
    item_id: int,
    fetched_at: str,
    profile_id: str,
    model: str,
    categories: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    existing = {
        (str(row["category_id"]), str(row["category_version"]))
        for row in conn.execute(
            """
            SELECT category_id, category_version
            FROM classifications
            WHERE item_id = ? AND profile_id = ? AND model = ?
              AND created_at >= ?
            """,
            (item_id, profile_id, model, fetched_at),
        )
    }
    return [
        dict(category)
        for category in categories
        if (str(category["id"]), str(category["version_hash"])) not in existing
    ]


def classify_pending_items(
    conn: sqlite3.Connection,
    settings: Settings,
    profile_id: str,
    *,
    client: Any | None = None,
    limit: int | None = None,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    now = now or datetime.now(UTC)
    created_at = to_utc_iso(now)
    model = settings.tev_model
    window_hours = get_window_hours(conn, settings.window_hours)
    max_items_per_feed = get_max_items_per_feed(conn, settings.max_items_per_feed)
    window_start, window_end = window_bounds(now, window_hours)
    categories = _load_enabled_categories(conn, profile_id)
    if len(categories) > MAX_QUESTIONS:
        raise ClassificationError(
            f"Profile has {len(categories)} enabled categories; "
            f"the model supports at most {MAX_QUESTIONS}."
        )

    pending_before = count_pending_items(
        conn,
        profile_id,
        model,
        window_start=window_start,
        window_end=window_end,
        max_items_per_feed=max_items_per_feed,
    )
    effective_limit = (
        get_max_new_items(conn, settings.max_new_items)
        if limit is None
        else max(0, limit)
    )
    items = _select_pending_items(
        conn,
        profile_id,
        model,
        effective_limit,
        window_start=window_start,
        window_end=window_end,
        max_items_per_feed=max_items_per_feed,
    )
    total = len(items)
    summary: dict[str, Any] = {
        "model": model,
        "categories": len(categories),
        "window_hours": window_hours,
        "max_items_per_feed": max_items_per_feed,
        "pending_before": pending_before,
        "selected_items": total,
        "classified_items": 0,
        "classified_categories": 0,
        "pending_items": pending_before,
        "pending_after": pending_before,
        "unprocessed_items": max(0, pending_before - total),
        "requests": 0,
        "stopped": False,
        "duration_s": 0.0,
    }

    if on_progress:
        on_progress(0, total)
    stopped = bool(should_stop and should_stop())
    if stopped or total == 0:
        summary["stopped"] = stopped
        summary["duration_s"] = round(time.perf_counter() - started, 3)
        return summary

    owns_client = client is None
    active = client or OllamaClient(settings.ollama_base_url, model)
    rows: list[tuple[Any, ...]] = []
    classified_items = 0
    requests_made = 0

    try:
        preflight = getattr(active, "preflight", None)
        if callable(preflight):
            preflight()

        for index, item in enumerate(items, 1):
            if should_stop and should_stop():
                stopped = True
                break
            missing = _missing_categories(
                conn,
                int(item["id"]),
                str(item["fetched_at"]),
                profile_id,
                model,
                categories,
            )
            if missing:
                probabilities = active.decide(
                    build_state(item),
                    build_questions(missing),
                )
                for category in missing:
                    category_id = str(category["id"])
                    if category_id not in probabilities:
                        raise OllamaResponseError(
                            f"Ollama response is missing category {category_id!r}"
                        )
                    rows.append(
                        (
                            item["id"],
                            profile_id,
                            category_id,
                            str(category["version_hash"]),
                            model,
                            float(probabilities[category_id]),
                            created_at,
                        )
                    )
                classified_items += 1
                requests_made += 1
            if on_progress:
                on_progress(index, total)

        if rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO classifications (
                    item_id, profile_id, category_id, category_version,
                    model, probability, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()

        remaining = count_pending_items(
            conn,
            profile_id,
            model,
            window_start=window_start,
            window_end=window_end,
            max_items_per_feed=max_items_per_feed,
        )
        summary.update(
            classified_items=classified_items,
            classified_categories=len(rows),
            pending_items=remaining,
            pending_after=remaining,
            unprocessed_items=remaining,
            requests=requests_made,
            stopped=stopped,
        )
        summary["duration_s"] = round(time.perf_counter() - started, 3)
        log.info(
            "classification %s: profile=%s model=%s items=%s scores=%s pending=%s",
            "stopped" if stopped else "complete",
            profile_id,
            model,
            classified_items,
            len(rows),
            remaining,
        )
        return summary
    finally:
        if owns_client:
            close = getattr(active, "close", None)
            if callable(close):
                close()
