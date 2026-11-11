from __future__ import annotations

import sqlite3

TRUE_VALUES = {"1", "true", "yes", "on"}
DISPLAY_PREFERENCES = ("hide_low_confidence", "hide_reason_text")
MAX_NEW_ITEMS_KEY = "max_new_items"
MAX_ITEMS_PER_FEED_KEY = "max_items_per_feed"
WINDOW_HOURS_KEY = "window_hours"


def get_bool(conn: sqlite3.Connection, key: str, default: bool = False) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row["value"]).strip().lower() in TRUE_VALUES


def set_bool(
    conn: sqlite3.Connection,
    key: str,
    value: bool,
    *,
    commit: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        (key, "1" if value else "0"),
    )
    if commit:
        conn.commit()


def get_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return int(str(row["value"]).strip())
    except ValueError:
        return default


def set_int(
    conn: sqlite3.Connection,
    key: str,
    value: int,
    *,
    commit: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )
    if commit:
        conn.commit()


def get_max_new_items(conn: sqlite3.Connection, default: int) -> int:
    value = get_int(conn, MAX_NEW_ITEMS_KEY, default)
    return value if value >= 1 else default


def set_max_new_items(
    conn: sqlite3.Connection,
    value: int,
    *,
    commit: bool = True,
) -> None:
    set_int(conn, MAX_NEW_ITEMS_KEY, value, commit=commit)


def get_max_items_per_feed(conn: sqlite3.Connection, default: int) -> int:
    value = get_int(conn, MAX_ITEMS_PER_FEED_KEY, default)
    return value if value >= 1 else default


def set_max_items_per_feed(
    conn: sqlite3.Connection,
    value: int,
    *,
    commit: bool = True,
) -> None:
    set_int(conn, MAX_ITEMS_PER_FEED_KEY, value, commit=commit)


def get_window_hours(conn: sqlite3.Connection, default: int) -> int:
    value = get_int(conn, WINDOW_HOURS_KEY, default)
    return value if value >= 1 else default


def set_window_hours(
    conn: sqlite3.Connection,
    value: int,
    *,
    commit: bool = True,
) -> None:
    set_int(conn, WINDOW_HOURS_KEY, value, commit=commit)


def get_display_preferences(conn: sqlite3.Connection) -> dict[str, bool]:
    return {key: get_bool(conn, key) for key in DISPLAY_PREFERENCES}


def set_display_preferences(
    conn: sqlite3.Connection,
    *,
    hide_low_confidence: bool,
    hide_reason_text: bool,
) -> None:
    set_bool(conn, "hide_low_confidence", hide_low_confidence, commit=False)
    set_bool(conn, "hide_reason_text", hide_reason_text, commit=False)
    conn.commit()
