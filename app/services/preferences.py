from __future__ import annotations

import sqlite3

TRUE_VALUES = {"1", "true", "yes", "on"}
DISPLAY_PREFERENCES = ("hide_low_confidence", "hide_reason_text")


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
