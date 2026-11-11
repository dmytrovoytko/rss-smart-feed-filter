from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import socket
import sqlite3
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser
import requests
from dateutil import parser as date_parser

from app.config import Settings, effective_item_retention_days
from app.services.candidates import count_window_items, window_bounds
from app.services.classify import count_pending_items
from app.services.preferences import get_max_items_per_feed, get_window_hours
from app.timeutil import parse_iso, to_utc_iso

log = logging.getLogger(__name__)

MAX_FEED_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_SUMMARY_CHARS = 700
USER_AGENT = "FeedFilter/0.1 (+local RSS reader)"
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "ref"}
TRACKING_QUERY_PREFIXES = ("utm_",)
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class FeedError(RuntimeError):
    """Base class for isolated feed errors."""


class FeedUrlError(FeedError):
    """The URL is not a public HTTP(S) URL."""


class FeedFetchError(FeedError):
    """The feed could not be fetched."""


class FeedParseError(FeedError):
    """The feed response could not be parsed."""


@dataclass(frozen=True)
class FeedFetchResult:
    status: str
    url: str
    content: bytes = b""
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class ParsedEntry:
    guid: str | None
    canonical_url: str
    url_hash: str
    title: str
    summary: str
    published_at: str | None
    content_hash: str


@dataclass(frozen=True)
class ParsedFeed:
    title: str
    entries: list[ParsedEntry]


@dataclass
class IngestStats:
    new: int = 0
    updated: int = 0
    duplicates: int = 0
    skipped_old: int = 0
    capped: int = 0


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
        elif tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def sanitize_text(value: object, max_chars: int | None = None) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text.strip():
        return ""

    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return ""

    cleaned = re.sub(r"\s+", " ", "".join(parser.parts)).strip()
    if max_chars is not None and len(cleaned) > max_chars:
        clipped = cleaned[:max_chars]
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        cleaned = clipped.rstrip() or cleaned[:max_chars]
    return cleaned


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(address.is_global and not address.is_multicast)


def validate_feed_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise FeedUrlError("feed URL is empty")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FeedUrlError(f"invalid feed URL: {url!r}") from exc

    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise FeedUrlError("feed URL must use http or https")
    if parts.username is not None or parts.password is not None:
        raise FeedUrlError("feed URL must not contain credentials")

    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise FeedUrlError("feed URL has no host")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(BLOCKED_HOST_SUFFIXES):
        raise FeedUrlError("feed URL must not point at a local host")
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 1 <= port <= 65535:
        raise FeedUrlError("feed URL has an invalid port")

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if not _is_public_ip(literal_ip):
            raise FeedUrlError("feed URL must point at a public address")
        return url

    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FeedUrlError(f"cannot resolve feed host: {host}") from exc
    if not addresses:
        raise FeedUrlError(f"cannot resolve feed host: {host}")

    resolved: set[str] = set()
    for address in addresses:
        raw_address = address[4][0].split("%", 1)[0]
        try:
            resolved_ip = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise FeedUrlError(f"invalid resolved address for {host}") from exc
        if not _is_public_ip(resolved_ip):
            raise FeedUrlError(f"feed host resolves to a non-public address: {resolved_ip}")
        resolved.add(str(resolved_ip))

    if not resolved:
        raise FeedUrlError(f"cannot resolve feed host: {host}")
    return url


def _is_tracking_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in TRACKING_QUERY_KEYS or lowered.startswith(TRACKING_QUERY_PREFIXES)


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("only http and https URLs can be canonicalized")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("URL has no host")

    port = parts.port
    default_port = 443 if scheme == "https" else 80
    if port == default_port:
        port = None

    display_host = f"[{host}]" if ":" in host else host
    netloc = f"{display_host}:{port}" if port is not None else display_host

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    ]
    query.sort()

    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def url_hash(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def title_key(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).lower()
    without_punctuation = "".join(
        char for char in normalized if char.isalnum() or char.isspace()
    )
    return " ".join(without_punctuation.split())


def content_hash(title: str) -> str:
    return hashlib.sha256(title_key(title).encode("utf-8")).hexdigest()


def _entry_summary(entry: Mapping[str, Any]) -> str:
    summary = entry.get("summary") or entry.get("description")
    if summary:
        return str(summary)
    content = entry.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, Mapping) and first.get("value"):
            return str(first["value"])
    return ""


def _parse_entry_datetime(entry: Mapping[str, Any]) -> str | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            try:
                return to_utc_iso(datetime(*value[:6], tzinfo=UTC))
            except (TypeError, ValueError):
                continue

    for key in ("published", "updated", "created", "pubDate"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            parsed = date_parser.parse(str(raw))
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return to_utc_iso(parsed)
    return None


def parse_feed(content: bytes, source_url: str) -> ParsedFeed:
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise FeedParseError(f"invalid feed: {parsed.get('bozo_exception', 'unknown error')}")

    feed_data = parsed.get("feed") or {}
    feed_title = sanitize_text(feed_data.get("title"), 300)
    entries: list[ParsedEntry] = []

    for entry in parsed.entries:
        raw_link = entry.get("link")
        if not raw_link:
            candidate = entry.get("id") or entry.get("guid")
            raw_link = candidate if isinstance(candidate, str) else None
        if not raw_link:
            continue

        try:
            absolute_link = urljoin(source_url, str(raw_link))
            canonical_url = canonicalize_url(absolute_link)
        except ValueError:
            continue

        title = sanitize_text(entry.get("title"), 500) or canonical_url
        digest = content_hash(title) if title_key(title) else content_hash(canonical_url)
        entries.append(
            ParsedEntry(
                guid=str(entry.get("id") or entry.get("guid") or "") or None,
                canonical_url=canonical_url,
                url_hash=url_hash(canonical_url),
                title=title,
                summary=sanitize_text(_entry_summary(entry), MAX_SUMMARY_CHARS),
                published_at=_parse_entry_datetime(entry),
                content_hash=digest,
            )
        )

    return ParsedFeed(title=feed_title, entries=entries)


def _read_limited(response: requests.Response, max_bytes: int = MAX_FEED_BYTES) -> bytes:
    declared = response.headers.get("content-length")
    if declared:
        try:
            if int(declared) > max_bytes:
                raise FeedFetchError(f"feed is larger than {max_bytes} bytes")
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise FeedFetchError(f"feed is larger than {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_feed(
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
    session: requests.Session | None = None,
    timeout: float = 30.0,
) -> FeedFetchResult:
    http = session or requests.Session()
    owns_session = session is None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": (
            "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
            "text/xml;q=0.8, */*;q=0.1"
        ),
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    current = url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            validate_feed_url(current)
            try:
                response = http.get(
                    current,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException as exc:
                raise FeedFetchError(f"request failed: {exc}") from exc

            try:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise FeedFetchError("redirect response had no location")
                    current = urljoin(current, location)
                    continue

                response_etag = response.headers.get("ETag")
                response_modified = response.headers.get("Last-Modified")
                if response.status_code == 304:
                    return FeedFetchResult(
                        status="not_modified",
                        url=current,
                        etag=response_etag,
                        last_modified=response_modified,
                    )
                if response.status_code >= 400:
                    raise FeedFetchError(f"HTTP {response.status_code}")
                if response.status_code >= 300:
                    raise FeedFetchError(f"unexpected HTTP {response.status_code}")

                return FeedFetchResult(
                    status="ok",
                    url=current,
                    content=_read_limited(response),
                    etag=response_etag,
                    last_modified=response_modified,
                )
            finally:
                response.close()
    finally:
        if owns_session:
            http.close()

    raise FeedFetchError("too many redirects")


class FeedFetcher:
    def __init__(self, timeout: float = 30.0, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()

    def __call__(self, feed: Mapping[str, Any]) -> FeedFetchResult:
        etag = feed.get("etag")
        last_modified = feed.get("last_modified")
        return fetch_feed(
            str(feed["url"]),
            etag=str(etag) if etag else None,
            last_modified=str(last_modified) if last_modified else None,
            session=self.session,
            timeout=self.timeout,
        )

    def close(self) -> None:
        self.session.close()


def _short_error(exc: BaseException) -> str:
    return re.sub(r"\s+", " ", str(exc)).strip()[:200] or exc.__class__.__name__


def _update_feed_status(
    conn: sqlite3.Connection,
    feed_id: int,
    *,
    fetched_at: str,
    status: str,
    result: FeedFetchResult | None = None,
) -> None:
    if result is None:
        conn.execute(
            "UPDATE feeds SET last_fetched_at = ?, last_status = ? WHERE id = ?",
            (fetched_at, status, feed_id),
        )
        return
    conn.execute(
        """
        UPDATE feeds
        SET last_fetched_at = ?, last_status = ?, etag = ?, last_modified = ?
        WHERE id = ?
        """,
        (fetched_at, status, result.etag, result.last_modified, feed_id),
    )


def _find_existing_item(
    conn: sqlite3.Connection,
    feed_id: int,
    entry: ParsedEntry,
) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT id, published_at FROM items WHERE feed_id = ? AND url_hash = ?",
        (feed_id, entry.url_hash),
    ).fetchone()
    if row is not None:
        return row
    return conn.execute(
        "SELECT id, published_at FROM items WHERE feed_id = ? AND content_hash = ?",
        (feed_id, entry.content_hash),
    ).fetchone()


def ingest_entries(
    conn: sqlite3.Connection,
    feed_id: int,
    entries: Iterable[ParsedEntry],
    *,
    fetched_at: str,
    cutoff: datetime,
    max_entries: int | None = None,
) -> IngestStats:
    stats = IngestStats()
    fetched_dt = parse_iso(fetched_at) or datetime.now(UTC)
    ordered = sorted(
        entries,
        key=lambda entry: parse_iso(entry.published_at) or fetched_dt,
        reverse=True,
    )
    processed = 0

    for entry in ordered:
        effective = parse_iso(entry.published_at) or fetched_dt
        if effective < cutoff:
            stats.skipped_old += 1
            continue
        if max_entries is not None and processed >= max_entries:
            stats.capped += 1
            continue
        processed += 1

        existing = _find_existing_item(conn, feed_id, entry)
        if existing is None:
            conn.execute(
                """
                INSERT INTO items (
                    feed_id, guid, canonical_url, url_hash, title, summary,
                    published_at, fetched_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feed_id,
                    entry.guid,
                    entry.canonical_url,
                    entry.url_hash,
                    entry.title,
                    entry.summary,
                    entry.published_at,
                    fetched_at,
                    entry.content_hash,
                ),
            )
            stats.new += 1
            continue

        existing_published = parse_iso(existing["published_at"])
        incoming_published = parse_iso(entry.published_at)
        is_newer = (
            incoming_published is not None
            and (existing_published is None or incoming_published > existing_published)
        )
        if not is_newer:
            stats.duplicates += 1
            continue

        conn.execute(
            """
            UPDATE items
            SET guid = ?, canonical_url = ?, url_hash = ?, title = ?, summary = ?,
                published_at = ?, fetched_at = ?, content_hash = ?
            WHERE id = ?
            """,
            (
                entry.guid,
                entry.canonical_url,
                entry.url_hash,
                entry.title,
                entry.summary,
                entry.published_at,
                fetched_at,
                entry.content_hash,
                existing["id"],
            ),
        )
        stats.updated += 1

    return stats


def apply_retention(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    window_hours: int | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    window_hours = settings.window_hours if window_hours is None else window_hours
    item_retention_days = effective_item_retention_days(
        settings.retention_days,
        window_hours,
    )
    item_cutoff = to_utc_iso(now - timedelta(days=item_retention_days))
    job_cutoff = to_utc_iso(now - timedelta(days=settings.retention_days))
    items_deleted = conn.execute(
        "DELETE FROM items WHERE COALESCE(published_at, fetched_at) < ?",
        (item_cutoff,),
    ).rowcount
    jobs_deleted = conn.execute(
        "DELETE FROM jobs WHERE created_at < ?",
        (job_cutoff,),
    ).rowcount
    return {
        "items_deleted": max(0, items_deleted),
        "jobs_deleted": max(0, jobs_deleted),
    }


def refresh_profile(
    conn: sqlite3.Connection,
    settings: Settings,
    profile_id: str,
    *,
    fetcher: Callable[[Mapping[str, Any]], FeedFetchResult] | None = None,
    now: datetime | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    now = now or datetime.now(UTC)
    fetched_at = to_utc_iso(now)
    feeds = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, profile_id, url, title, etag, last_modified,
                   last_fetched_at, last_status
            FROM feeds
            WHERE profile_id = ? AND enabled = 1
            ORDER BY id
            """,
            (profile_id,),
        )
    ]
    if not feeds:
        raise FeedError("no enabled feeds for this profile")

    owns_fetcher = fetcher is None
    active_fetcher = fetcher or FeedFetcher()
    total = len(feeds)
    window_hours = get_window_hours(conn, settings.window_hours)
    max_items_per_feed = get_max_items_per_feed(conn, settings.max_items_per_feed)
    item_retention_days = effective_item_retention_days(
        settings.retention_days,
        window_hours,
    )

    summary: dict[str, Any] = {
        "feeds_total": total,
        "feeds_ok": 0,
        "feeds_not_modified": 0,
        "feeds_failed": 0,
        "new_items": 0,
        "updated_items": 0,
        "duplicate_entries": 0,
        "skipped_old": 0,
        "capped_entries": 0,
        "window_hours": window_hours,
        "max_items_per_feed": max_items_per_feed,
        "errors": [],
        "fetched_at": fetched_at,
    }

    try:
        if on_progress:
            on_progress(0, total)
        for index, feed in enumerate(feeds, 1):
            last_status = str(feed["last_status"] or "").lower()
            last_fetched = parse_iso(feed["last_fetched_at"])
            if last_fetched is not None and "error" not in last_status:
                cutoff = max(
                    last_fetched - timedelta(hours=settings.refresh_overlap_hours),
                    now - timedelta(days=item_retention_days),
                )
            else:
                cutoff = now - timedelta(hours=window_hours)

            try:
                result = active_fetcher(feed)
            except Exception as exc:
                message = _short_error(exc)
                _update_feed_status(
                    conn,
                    feed["id"],
                    fetched_at=fetched_at,
                    status=f"error: {message}",
                )
                conn.commit()
                summary["feeds_failed"] += 1
                summary["errors"].append(
                    {"feed_id": feed["id"], "title": feed["title"], "error": message}
                )
                log.warning("feed %s failed: %s", feed["title"], message)
                if on_progress:
                    on_progress(index, total)
                continue

            if result.status == "not_modified":
                _update_feed_status(
                    conn,
                    feed["id"],
                    fetched_at=fetched_at,
                    status="not modified",
                    result=result,
                )
                conn.commit()
                summary["feeds_not_modified"] += 1
                if on_progress:
                    on_progress(index, total)
                continue

            try:
                parsed = parse_feed(result.content, result.url)
            except Exception as exc:
                message = _short_error(exc)
                _update_feed_status(
                    conn,
                    feed["id"],
                    fetched_at=fetched_at,
                    status=f"parse error: {message}",
                )
                conn.commit()
                summary["feeds_failed"] += 1
                summary["errors"].append(
                    {"feed_id": feed["id"], "title": feed["title"], "error": message}
                )
                log.warning("feed %s parse failed: %s", feed["title"], message)
                if on_progress:
                    on_progress(index, total)
                continue

            stats = ingest_entries(
                conn,
                feed["id"],
                parsed.entries,
                fetched_at=fetched_at,
                cutoff=cutoff,
                max_entries=max_items_per_feed,
            )
            summary["new_items"] += stats.new
            summary["updated_items"] += stats.updated
            summary["duplicate_entries"] += stats.duplicates
            summary["skipped_old"] += stats.skipped_old
            summary["capped_entries"] += stats.capped
            summary["feeds_ok"] += 1

            if stats.new or stats.updated:
                status = f"ok: {stats.new} new, {stats.updated} updated"
            else:
                status = "ok: no new items"
            _update_feed_status(
                conn,
                feed["id"],
                fetched_at=fetched_at,
                status=status,
                result=result,
            )
            conn.commit()
            if on_progress:
                on_progress(index, total)

        summary["retention"] = apply_retention(
            conn,
            settings,
            window_hours=window_hours,
            now=now,
        )
        conn.commit()
        window_start, window_end = window_bounds(now, window_hours)
        summary["window_items"] = count_window_items(
            conn,
            profile_id,
            window_start,
            window_end,
            max_items_per_feed,
        )
        summary["pending_items"] = count_pending_items(
            conn,
            profile_id,
            settings.tev_model,
            window_start=window_start,
            window_end=window_end,
            max_items_per_feed=max_items_per_feed,
        )
        summary["items_total"] = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM items i
                JOIN feeds f ON f.id = i.feed_id
                WHERE f.profile_id = ?
                """,
                (profile_id,),
            ).fetchone()[0]
        )
        summary["duration_s"] = round(time.perf_counter() - started, 3)
        log.info(
            "refresh complete: profile=%s feeds=%s new=%s updated=%s failed=%s",
            profile_id,
            total,
            summary["new_items"],
            summary["updated_items"],
            summary["feeds_failed"],
        )
        return summary
    finally:
        if owns_fetcher:
            active_fetcher.close()
