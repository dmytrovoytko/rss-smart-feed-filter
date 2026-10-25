from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from app.config import Settings
from app.timeutil import utc_now_iso
from app.versioning import category_snapshot_json, category_version_hash

log = logging.getLogger(__name__)

DEFAULT_ACTIVE_PROFILE = "ds.v1"
CATALOG_FILE = "catalog.yaml"


class SeedError(RuntimeError):
    pass


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeedError(f"missing seed file: {path}") from exc
    except yaml.YAMLError as exc:
        raise SeedError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SeedError(f"{path} must contain a YAML mapping")
    return data


def _require_text(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SeedError(f"{context}: missing non-empty {key!r}")
    return value.strip()


def _validate_http_url(url: str, context: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SeedError(f"{context}: feed URL must be public http(s): {url!r}")


def _seed_profile(
    conn: sqlite3.Connection,
    profile_data: dict[str, Any],
    settings: Settings,
) -> dict[str, int]:
    profile_id = _require_text(profile_data, "id", "profile")
    context = f"profile {profile_id}"
    name = _require_text(profile_data, "name", context)
    kind = _require_text(profile_data, "kind", context)
    if kind not in {"work", "fun"}:
        raise SeedError(f"{context}: kind must be 'work' or 'fun'")

    threshold = float(profile_data.get("threshold", settings.threshold))
    if not 0.0 <= threshold <= 1.0:
        raise SeedError(f"{context}: threshold must be between 0.0 and 1.0")

    categories = profile_data.get("categories")
    if not isinstance(categories, list) or not categories:
        raise SeedError(f"{context}: categories must be a non-empty list")
    if len(categories) > 64:
        raise SeedError(f"{context}: tev1 supports at most 64 categories per request")

    created_at = utc_now_iso()
    conn.execute(
        """
        INSERT INTO profiles (id, name, kind, threshold, is_active, created_at)
        VALUES (?, ?, ?, ?, 0, ?)
        """,
        (profile_id, name, kind, threshold, created_at),
    )

    category_count = 0
    for position, category in enumerate(categories):
        if not isinstance(category, dict):
            raise SeedError(f"{context}: category at position {position} must be a mapping")
        category_id = _require_text(category, "id", f"{context} category {position}")
        category_context = f"{context} category {category_id}"

        normalized = {
            "name": _require_text(category, "name", category_context),
            "description": _require_text(category, "description", category_context),
            "criteria_true": _require_text(category, "criteria_true", category_context),
            "criteria_false": _require_text(category, "criteria_false", category_context),
            "reason_hint": _require_text(category, "reason_hint", category_context),
        }
        weight = float(category.get("weight", 1.0))
        enabled = 1 if bool(category.get("enabled", True)) else 0
        version_hash = category_version_hash(normalized)

        conn.execute(
            """
            INSERT INTO categories (
                profile_id, id, name, description, criteria_true, criteria_false,
                reason_hint, weight, enabled, position, version_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                category_id,
                normalized["name"],
                normalized["description"],
                normalized["criteria_true"],
                normalized["criteria_false"],
                normalized["reason_hint"],
                weight,
                enabled,
                position,
                version_hash,
            ),
        )
        conn.execute(
            """
            INSERT INTO category_versions (
                profile_id, category_id, version_hash, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                category_id,
                version_hash,
                category_snapshot_json(normalized),
                created_at,
            ),
        )
        category_count += 1

    return {
        "profiles": 1,
        "categories": category_count,
        "feeds": _seed_catalog_feeds(conn, profile_id, settings),
    }


def _seed_catalog_feeds(
    conn: sqlite3.Connection,
    profile_id: str,
    settings: Settings,
) -> int:
    catalog_path = settings.profiles_dir / CATALOG_FILE
    catalog = _load_yaml(catalog_path)
    feeds_by_profile = catalog.get("feeds")
    if not isinstance(feeds_by_profile, dict):
        raise SeedError(f"{catalog_path}: missing 'feeds' mapping")

    entries = feeds_by_profile.get(profile_id, [])
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise SeedError(f"{catalog_path}: feeds.{profile_id} must be a list")

    count = 0
    for index, entry in enumerate(entries):
        context = f"{catalog_path}: feeds.{profile_id}[{index}]"
        if not isinstance(entry, dict):
            raise SeedError(f"{context} must be a mapping")
        url = _require_text(entry, "url", context)
        title = _require_text(entry, "title", context)
        _validate_http_url(url, context)
        enabled = 1 if bool(entry.get("enabled", True)) else 0
        conn.execute(
            """
            INSERT INTO feeds (profile_id, url, title, enabled)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (profile_id, url) DO NOTHING
            """,
            (profile_id, url, title, enabled),
        )
        count += 1
    return count


def _choose_active_profile(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT id FROM profiles WHERE is_active = 1").fetchone()
    if row is not None:
        return str(row["id"])

    setting = conn.execute(
        "SELECT value FROM settings WHERE key = 'active_profile_id'"
    ).fetchone()
    if setting is not None:
        candidate = str(setting["value"])
        exists = conn.execute("SELECT 1 FROM profiles WHERE id = ?", (candidate,)).fetchone()
        if exists is not None:
            return candidate

    preferred = conn.execute(
        "SELECT id FROM profiles WHERE id = ?", (DEFAULT_ACTIVE_PROFILE,)
    ).fetchone()
    if preferred is not None:
        return str(preferred["id"])

    fallback = conn.execute("SELECT id FROM profiles ORDER BY created_at, id LIMIT 1").fetchone()
    if fallback is None:
        raise SeedError("no profiles available to activate")
    return str(fallback["id"])


def set_active_profile(conn: sqlite3.Connection, profile_id: str) -> None:
    exists = conn.execute("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if exists is None:
        raise SeedError(f"unknown profile: {profile_id}")
    with conn:
        conn.execute("UPDATE profiles SET is_active = 0 WHERE is_active = 1")
        conn.execute("UPDATE profiles SET is_active = 1 WHERE id = ?", (profile_id,))
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ('active_profile_id', ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (profile_id,),
        )


def seed_database(conn: sqlite3.Connection, settings: Settings) -> dict[str, Any]:
    profiles_dir = settings.profiles_dir
    if not profiles_dir.is_dir():
        raise SeedError(f"profiles directory does not exist: {profiles_dir}")

    summary: dict[str, Any] = {
        "profiles_created": 0,
        "profiles_existing": 0,
        "categories_created": 0,
        "feeds_created": 0,
    }

    profile_files = sorted(profiles_dir.glob("*.yaml"))
    profile_files = [path for path in profile_files if path.name != CATALOG_FILE]
    if not profile_files:
        raise SeedError(f"no profile YAML files found in {profiles_dir}")

    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('hide_low_confidence', '0')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('hide_reason_text', '0')"
        )

        for path in profile_files:
            profile_data = _load_yaml(path)
            profile_id = _require_text(profile_data, "id", str(path))
            exists = conn.execute(
                "SELECT 1 FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if exists is not None:
                summary["profiles_existing"] += 1
                continue
            created = _seed_profile(conn, profile_data, settings)
            summary["profiles_created"] += created["profiles"]
            summary["categories_created"] += created["categories"]
            summary["feeds_created"] += created["feeds"]

        active_profile_id = _choose_active_profile(conn)
        conn.execute("UPDATE profiles SET is_active = 0 WHERE is_active = 1")
        conn.execute("UPDATE profiles SET is_active = 1 WHERE id = ?", (active_profile_id,))
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ('active_profile_id', ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (active_profile_id,),
        )

    summary["active_profile_id"] = active_profile_id
    summary["profiles_total"] = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    summary["categories_total"] = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    summary["feeds_total"] = conn.execute("SELECT COUNT(*) FROM feeds").fetchone()[0]
    log.info("seed complete: %s", json.dumps(summary, sort_keys=True))
    return summary
