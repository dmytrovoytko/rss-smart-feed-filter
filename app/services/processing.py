from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.timeutil import to_utc_iso

MAX_NOTE_CHARS = 2000


def _item_for_profile(
    conn: sqlite3.Connection,
    profile_id: str,
    item_id: int,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT i.id,
               i.title,
               i.canonical_url AS url,
               i.summary,
               i.published_at,
               i.fetched_at,
               f.title AS source
        FROM items AS i
        JOIN feeds AS f ON f.id = i.feed_id
        WHERE i.id = ? AND f.profile_id = ?
        """,
        (item_id, profile_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"unknown item for profile {profile_id}: {item_id}")
    return dict(row)


def archive_item(
    conn: sqlite3.Connection,
    profile_id: str,
    item_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    item = _item_for_profile(conn, profile_id, item_id)
    archived_at = to_utc_iso(now or datetime.now(UTC))
    conn.execute(
        """
        INSERT INTO archived_items (
            profile_id, item_id, title, url, source, summary,
            published_at, fetched_at, archived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (profile_id, item_id) DO UPDATE SET
            title = excluded.title,
            url = excluded.url,
            source = excluded.source,
            summary = excluded.summary,
            published_at = excluded.published_at,
            fetched_at = excluded.fetched_at,
            archived_at = excluded.archived_at
        """,
        (
            profile_id,
            item_id,
            item["title"],
            item["url"],
            item["source"],
            item["summary"],
            item["published_at"],
            item["fetched_at"],
            archived_at,
        ),
    )
    conn.execute(
        "DELETE FROM dismissed_items WHERE profile_id = ? AND item_id = ?",
        (profile_id, item_id),
    )
    conn.commit()
    return {**item, "archived_at": archived_at}


def dismiss_item(
    conn: sqlite3.Connection,
    profile_id: str,
    item_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    item = _item_for_profile(conn, profile_id, item_id)
    dismissed_at = to_utc_iso(now or datetime.now(UTC))
    conn.execute(
        "DELETE FROM archived_items WHERE profile_id = ? AND item_id = ?",
        (profile_id, item_id),
    )
    conn.execute(
        """
        INSERT INTO dismissed_items (profile_id, item_id, dismissed_at)
        VALUES (?, ?, ?)
        ON CONFLICT (profile_id, item_id) DO UPDATE SET
            dismissed_at = excluded.dismissed_at
        """,
        (profile_id, item_id, dismissed_at),
    )
    conn.commit()
    return {**item, "dismissed_at": dismissed_at}


def list_archived(conn: sqlite3.Connection, profile_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT profile_id, item_id, title, url, source, summary,
                   published_at, fetched_at, archived_at
            FROM archived_items
            WHERE profile_id = ?
            ORDER BY archived_at DESC, item_id DESC
            """,
            (profile_id,),
        )
    ]


def remove_from_archive(
    conn: sqlite3.Connection,
    profile_id: str,
    item_id: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    archived = conn.execute(
        """
        SELECT profile_id, item_id, title, url, source, summary,
               published_at, fetched_at, archived_at
        FROM archived_items
        WHERE profile_id = ? AND item_id = ?
        """,
        (profile_id, item_id),
    ).fetchone()
    if archived is None:
        raise LookupError(f"unknown archived item for profile {profile_id}: {item_id}")

    item = conn.execute(
        """
        SELECT 1
        FROM items AS i
        JOIN feeds AS f ON f.id = i.feed_id
        WHERE i.id = ? AND f.profile_id = ?
        """,
        (item_id, profile_id),
    ).fetchone()
    dismissed = False
    if item is not None:
        dismissed_at = to_utc_iso(now or datetime.now(UTC))
        conn.execute(
            """
            INSERT INTO dismissed_items (profile_id, item_id, dismissed_at)
            VALUES (?, ?, ?)
            ON CONFLICT (profile_id, item_id) DO UPDATE SET
                dismissed_at = excluded.dismissed_at
            """,
            (profile_id, item_id, dismissed_at),
        )
        dismissed = True
    conn.execute(
        "DELETE FROM archived_items WHERE profile_id = ? AND item_id = ?",
        (profile_id, item_id),
    )
    conn.commit()
    return {**dict(archived), "dismissed": dismissed}


def recategorize_item(
    conn: sqlite3.Connection,
    profile_id: str,
    item_id: int,
    category_id: str,
    *,
    model: str,
    suggested_category_id: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    item = _item_for_profile(conn, profile_id, item_id)
    category = conn.execute(
        """
        SELECT id, name, version_hash, enabled
        FROM categories
        WHERE profile_id = ? AND id = ?
        """,
        (profile_id, category_id),
    ).fetchone()
    if category is None:
        raise LookupError(f"unknown category for profile {profile_id}: {category_id}")

    classification = conn.execute(
        """
        SELECT category_version, model, probability, created_at
        FROM classifications
        WHERE item_id = ?
          AND profile_id = ?
          AND category_id = ?
          AND category_version = ?
          AND model = ?
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (
            item_id,
            profile_id,
            category_id,
            category["version_hash"],
            model,
            item["fetched_at"],
        ),
    ).fetchone()
    if classification is None:
        raise LookupError(
            f"unknown classification for item {item_id} and category {category_id}"
        )

    suggested = None
    suggested_id = (suggested_category_id or "").strip() or None
    if suggested_id is not None:
        suggested = conn.execute(
            """
            SELECT id, name
            FROM categories
            WHERE profile_id = ? AND id = ?
            """,
            (profile_id, suggested_id),
        ).fetchone()
        if suggested is None:
            raise LookupError(
                f"unknown category for profile {profile_id}: {suggested_id}"
            )

    clean_note = (note or "").strip() or None
    if clean_note is not None:
        clean_note = clean_note[:MAX_NOTE_CHARS]
    created_at = to_utc_iso(now or datetime.now(UTC))
    cursor = conn.execute(
        """
        INSERT INTO category_feedback (
            profile_id, item_id, item_title, item_url, item_source,
            category_id, category_name, category_version, model, probability,
            suggested_category_id, suggested_category_name, note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            profile_id,
            item_id,
            item["title"],
            item["url"],
            item["source"],
            category_id,
            category["name"],
            classification["category_version"],
            classification["model"],
            float(classification["probability"]),
            suggested["id"] if suggested is not None else None,
            suggested["name"] if suggested is not None else None,
            clean_note,
            created_at,
        ),
    )
    conn.commit()
    return {
        "id": int(cursor.lastrowid),
        "profile_id": profile_id,
        "item_id": item_id,
        "item_title": item["title"],
        "item_url": item["url"],
        "item_source": item["source"],
        "category_id": category_id,
        "category_name": category["name"],
        "category_version": classification["category_version"],
        "model": classification["model"],
        "probability": float(classification["probability"]),
        "suggested_category_id": suggested["id"] if suggested is not None else None,
        "suggested_category_name": suggested["name"] if suggested is not None else None,
        "note": clean_note,
        "created_at": created_at,
    }


def list_feedback(
    conn: sqlite3.Connection,
    profile_id: str,
    *,
    item_id: int | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT id, profile_id, item_id, item_title, item_url, item_source,
               category_id, category_name, category_version, model, probability,
               suggested_category_id, suggested_category_name, note, created_at
        FROM category_feedback
        WHERE profile_id = ?
    """
    params: list[Any] = [profile_id]
    if item_id is not None:
        sql += " AND item_id = ?"
        params.append(item_id)
    sql += " ORDER BY created_at, id"
    return [dict(row) for row in conn.execute(sql, tuple(params))]
