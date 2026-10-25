from __future__ import annotations

from datetime import UTC, datetime


def to_utc_iso(value: datetime) -> str:
    """Return a UTC ISO-8601 timestamp with milliseconds and a trailing Z."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now_iso() -> str:
    return to_utc_iso(datetime.now(UTC))


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def human_time(value: str | None) -> str:
    parsed = parse_iso(value)
    if parsed is None:
        return "never"
    seconds = int((datetime.now(UTC) - parsed).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 7:
        return f"{days}d ago"
    return parsed.strftime("%Y-%m-%d")
