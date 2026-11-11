from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from app.timeutil import to_utc_iso

RANKED_CANDIDATES_CTE = """
WITH ranked_items AS (
    SELECT i.id,
           i.feed_id,
           i.canonical_url,
           i.title,
           i.summary,
           i.published_at,
           i.fetched_at,
           ROW_NUMBER() OVER (
               PARTITION BY i.feed_id
               ORDER BY COALESCE(i.published_at, i.fetched_at) DESC, i.id DESC
           ) AS feed_rank
    FROM items i
    JOIN feeds f ON f.id = i.feed_id
    WHERE f.profile_id = ?
      AND f.enabled = 1
      AND COALESCE(i.published_at, i.fetched_at) >= ?
      AND COALESCE(i.published_at, i.fetched_at) <= ?
)
"""


def window_bounds(now: datetime, window_hours: int) -> tuple[str, str]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    return (
        to_utc_iso(now - timedelta(hours=window_hours)),
        to_utc_iso(now),
    )


def count_window_items(
    conn: sqlite3.Connection,
    profile_id: str,
    window_start: str,
    window_end: str,
    max_items_per_feed: int,
) -> int:
    row = conn.execute(
        RANKED_CANDIDATES_CTE
        + """
        SELECT COUNT(*)
        FROM ranked_items
        WHERE feed_rank <= ?
        """,
        (profile_id, window_start, window_end, max_items_per_feed),
    ).fetchone()
    return int(row[0])
