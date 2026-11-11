from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.services.candidates import (
    RANKED_CANDIDATES_CTE,
    count_window_items,
    window_bounds,
)
from app.services.preferences import get_max_items_per_feed, get_window_hours
from app.timeutil import parse_iso, to_utc_iso

MIN_RECENCY = 0.25
RECENCY_HALF_LIFE_HOURS = 36.0


def recency_factor(age_hours: float) -> float:
    age_hours = max(0.0, age_hours)
    return max(MIN_RECENCY, 0.5 ** (age_hours / RECENCY_HALF_LIFE_HOURS))


def rank_score(probability: float, weight: float, age_hours: float) -> float:
    return probability * weight * recency_factor(age_hours)


def _setting_bool(conn: sqlite3.Connection, key: str, default: bool = False) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row["value"]).strip().lower() in {"1", "true", "yes", "on"}


def _load_categories(
    conn: sqlite3.Connection,
    profile_id: str,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, name, reason_hint, weight, position, version_hash
            FROM categories
            WHERE profile_id = ? AND enabled = 1
            ORDER BY position, id
            """,
            (profile_id,),
        )
    ]


def _classified_rows(
    conn: sqlite3.Connection,
    profile_id: str,
    model: str,
    window_start: str,
    window_end: str,
    max_items_per_feed: int,
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            RANKED_CANDIDATES_CTE
            + """
            SELECT i.id AS item_id,
                   i.title,
                   i.canonical_url,
                   i.published_at,
                   i.fetched_at,
                   f.title AS source,
                   c.id AS category_id,
                   c.name AS category_name,
                   c.reason_hint,
                   c.weight,
                   c.position,
                   cl.probability
            FROM classifications cl
            JOIN categories c
              ON c.profile_id = cl.profile_id
             AND c.id = cl.category_id
             AND c.version_hash = cl.category_version
            JOIN ranked_items i ON i.id = cl.item_id
            JOIN feeds f ON f.id = i.feed_id
            WHERE cl.profile_id = ?
              AND cl.model = ?
              AND c.enabled = 1
              AND f.enabled = 1
              AND cl.created_at >= i.fetched_at
              AND i.feed_rank <= ?
            ORDER BY c.position, cl.probability DESC, i.id
            """,
            (
                profile_id,
                window_start,
                window_end,
                profile_id,
                model,
                max_items_per_feed,
            ),
        )
    )


def build_digest(
    conn: sqlite3.Connection,
    settings: Settings,
    profile_id: str,
    *,
    now: datetime | None = None,
    window_hours: int | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    window_hours = (
        get_window_hours(conn, settings.window_hours)
        if window_hours is None
        else max(1, window_hours)
    )
    max_items_per_feed = get_max_items_per_feed(conn, settings.max_items_per_feed)
    window_start, window_end = window_bounds(now, window_hours)

    profile_row = conn.execute(
        "SELECT id, name, kind, threshold FROM profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if profile_row is None:
        raise LookupError(f"unknown profile: {profile_id}")
    profile = dict(profile_row)
    threshold = float(profile["threshold"])

    categories = _load_categories(conn, profile_id)
    hide_low_confidence = _setting_bool(conn, "hide_low_confidence")
    hide_reason_text = _setting_bool(conn, "hide_reason_text")
    window_items = count_window_items(
        conn,
        profile_id,
        window_start,
        window_end,
        max_items_per_feed,
    )
    rows = _classified_rows(
        conn,
        profile_id,
        settings.tev_model,
        window_start,
        window_end,
        max_items_per_feed,
    )

    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        item_id = int(row["item_id"])
        item = grouped.setdefault(
            item_id,
            {
                "id": item_id,
                "title": str(row["title"]),
                "url": str(row["canonical_url"]),
                "source": str(row["source"]),
                "published_at": row["published_at"],
                "fetched_at": str(row["fetched_at"]),
                "matches": [],
            },
        )
        item["matches"].append(
            {
                "category_id": str(row["category_id"]),
                "category_name": str(row["category_name"]),
                "reason_hint": str(row["reason_hint"]),
                "weight": float(row["weight"]),
                "position": int(row["position"]),
                "probability": float(row["probability"]),
            }
        )

    section_map = {
        str(category["id"]): {
            "id": str(category["id"]),
            "name": str(category["name"]),
            "weight": float(category["weight"]),
            "position": int(category["position"]),
            "entries": [],
        }
        for category in categories
    }
    low_confidence: list[dict[str, Any]] = []

    for item in grouped.values():
        matches = sorted(
            item["matches"],
            key=lambda match: (match["position"], match["category_id"]),
        )
        effective_at = (
            parse_iso(item["published_at"])
            or parse_iso(item["fetched_at"])
            or now
        )
        age_hours = max(0.0, (now - effective_at).total_seconds() / 3600.0)
        matched = [match for match in matches if match["probability"] >= threshold]
        tags = [
            {
                "id": match["category_id"],
                "name": match["category_name"],
                "probability": match["probability"],
            }
            for match in matched
        ]

        if matched:
            for match in matched:
                score = rank_score(match["probability"], match["weight"], age_hours)
                section_map[match["category_id"]]["entries"].append(
                    {
                        "id": item["id"],
                        "title": item["title"],
                        "url": item["url"],
                        "source": item["source"],
                        "effective_at": to_utc_iso(effective_at),
                        "probability": match["probability"],
                        "score": round(score, 6),
                        "reason": (
                            f"Matched {match['category_name']}: "
                            f"{match['reason_hint']} "
                            f"(p={match['probability']:.2f})"
                        ),
                        "tags": tags,
                    }
                )
            continue

        if not matches:
            continue
        best = max(matches, key=lambda match: match["probability"])
        low_confidence.append(
            {
                "id": item["id"],
                "title": item["title"],
                "url": item["url"],
                "source": item["source"],
                "effective_at": to_utc_iso(effective_at),
                "probability": best["probability"],
                "best_category": best["category_name"],
            }
        )

    sections: list[dict[str, Any]] = []
    for category in categories:
        section = section_map[str(category["id"])]
        if not section["entries"]:
            continue
        section["entries"].sort(
            key=lambda entry: (
                entry["score"],
                entry["probability"],
                entry["effective_at"],
                entry["id"],
            ),
            reverse=True,
        )
        sections.append(section)

    low_confidence.sort(
        key=lambda entry: (
            entry["probability"],
            entry["effective_at"],
            entry["id"],
        ),
        reverse=True,
    )
    low_confidence_count = len(low_confidence)
    if hide_low_confidence:
        low_confidence = []

    classified_items = len(grouped)
    return {
        "profile": profile,
        "sections": sections,
        "low_confidence": low_confidence,
        "low_confidence_count": low_confidence_count,
        "hide_low_confidence": hide_low_confidence,
        "hide_reason_text": hide_reason_text,
        "threshold": threshold,
        "window_hours": window_hours,
        "max_items_per_feed": max_items_per_feed,
        "window_start": window_start,
        "window_end": window_end,
        "window_items": window_items,
        "classified_items": classified_items,
        "pending_items": max(0, window_items - classified_items),
        "generated_at": window_end,
    }
