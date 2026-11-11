from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.db import open_db
from app.seed import set_active_profile
from app.services.candidates import window_bounds
from app.services.classify import count_pending_items
from app.services.digest import build_digest
from app.services.jobs import JobConflict, JobNotFound, get_job, latest_job
from app.services.preferences import (
    get_display_preferences,
    get_max_items_per_feed,
    get_max_new_items,
    get_window_hours,
    set_display_preferences,
    set_max_items_per_feed,
    set_max_new_items,
    set_window_hours,
)

router = APIRouter()

FAVICON_PATH = Path(__file__).resolve().parent.parent / "static" / "favicon.png"


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


def _active_profile_id(conn: sqlite3.Connection) -> str | None:
    setting = conn.execute(
        "SELECT value FROM settings WHERE key = 'active_profile_id'"
    ).fetchone()
    if setting is not None:
        candidate = str(setting["value"])
        exists = conn.execute("SELECT 1 FROM profiles WHERE id = ?", (candidate,)).fetchone()
        if exists is not None:
            return candidate

    row = conn.execute("SELECT id FROM profiles WHERE is_active = 1 LIMIT 1").fetchone()
    return str(row["id"]) if row is not None else None


def _profile_choices(conn: sqlite3.Connection) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id, name, kind FROM profiles ORDER BY created_at, id"
        )
    ]


def _last_refreshed_at(conn: sqlite3.Connection, profile_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT MAX(updated_at) AS refreshed_at
        FROM jobs
        WHERE profile_id = ? AND kind = 'refresh' AND mode = 'refresh'
          AND status = 'done'
        """,
        (profile_id,),
    ).fetchone()
    if row is None or row["refreshed_at"] is None:
        return None
    return str(row["refreshed_at"])


def _pending_items(
    conn: sqlite3.Connection,
    settings: Settings,
    profile_id: str,
) -> int:
    window_hours = get_window_hours(conn, settings.window_hours)
    max_items_per_feed = get_max_items_per_feed(conn, settings.max_items_per_feed)
    window_start, window_end = window_bounds(datetime.now(UTC), window_hours)
    return count_pending_items(
        conn,
        profile_id,
        settings.tev_model,
        window_start=window_start,
        window_end=window_end,
        max_items_per_feed=max_items_per_feed,
    )


def _profile_or_404(conn: sqlite3.Connection, profile_id: str) -> dict[str, object]:
    profile = conn.execute(
        "SELECT id, name, kind, threshold, is_active FROM profiles WHERE id = ?",
        (profile_id,),
    ).fetchone()
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown profile: {profile_id}")
    return dict(profile)


def _job_or_404(conn: sqlite3.Connection, job_id: int) -> dict[str, object]:
    job = get_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    return job


def _render_job_panel(
    request: Request,
    conn: sqlite3.Connection,
    job_id: int,
    *,
    notice: str | None = None,
    refresh_page_on_done: bool = False,
) -> HTMLResponse:
    settings = _settings(request)
    job = _job_or_404(conn, job_id)
    response = _templates(request).TemplateResponse(
        request=request,
        name="partials/job.html",
        context={
            "job": job,
            "notice": notice,
            "oob_refresh_control": True,
            "oob_candidate_count": True,
            "stop_requested": request.app.state.jobs.is_stop_requested(job_id),
            "pending_items": _pending_items(
                conn,
                settings,
                str(job["profile_id"]),
            ),
            "max_new_items": get_max_new_items(conn, settings.max_new_items),
        },
    )
    if refresh_page_on_done and job["status"] == "done" and _is_htmx(request):
        response.headers["HX-Refresh"] = "true"
    return response


@router.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse(FAVICON_PATH)


def _settings_context(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    preferences: dict[str, bool] | None = None,
    max_new_items: str | int | None = None,
    max_items_per_feed: str | int | None = None,
    window_hours: str | int | None = None,
    saved: bool = False,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "preferences": preferences
        if preferences is not None
        else get_display_preferences(conn),
        "max_new_items": max_new_items
        if max_new_items is not None
        else get_max_new_items(conn, settings.max_new_items),
        "default_max_new_items": settings.max_new_items,
        "max_items_per_feed": max_items_per_feed
        if max_items_per_feed is not None
        else get_max_items_per_feed(conn, settings.max_items_per_feed),
        "default_max_items_per_feed": settings.max_items_per_feed,
        "window_hours": window_hours
        if window_hours is not None
        else get_window_hours(conn, settings.window_hours),
        "default_window_hours": settings.window_hours,
        "profiles": _profile_choices(conn),
        "active_profile_id": _active_profile_id(conn),
        "saved": saved,
        "error": error,
    }


@router.get("/")
def index(request: Request) -> RedirectResponse:
    with open_db(_settings(request).db_path) as conn:
        profile_id = _active_profile_id(conn)
    if profile_id is None:
        raise HTTPException(status_code=404, detail="no profile configured")
    return RedirectResponse(f"/profiles/{profile_id}/digest", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: bool = False) -> HTMLResponse:
    settings = _settings(request)
    with open_db(settings.db_path) as conn:
        context = _settings_context(conn, settings, saved=saved)
    return _templates(request).TemplateResponse(
        request=request,
        name="settings.html",
        context=context,
    )


@router.post("/settings")
def update_settings(
    request: Request,
    hide_low_confidence: str | None = Form(None),
    hide_reason_text: str | None = Form(None),
    max_new_items: str = Form(""),
    max_items_per_feed: str = Form(""),
    window_hours: str = Form(""),
) -> Response:
    settings = _settings(request)
    preferences = {
        "hide_low_confidence": hide_low_confidence is not None,
        "hide_reason_text": hide_reason_text is not None,
    }
    submitted = {
        "max_new_items": max_new_items,
        "max_items_per_feed": max_items_per_feed,
        "window_hours": window_hours,
    }
    labels = {
        "max_new_items": "Max new items",
        "max_items_per_feed": "Max items per source",
        "window_hours": "Digest window",
    }
    values: dict[str, int] = {}
    for field, raw in submitted.items():
        try:
            value = int(raw)
            if value < 1:
                raise ValueError
        except (TypeError, ValueError):
            with open_db(settings.db_path) as conn:
                context = _settings_context(
                    conn,
                    settings,
                    preferences=preferences,
                    error=f"{labels[field]} must be a whole number of at least 1.",
                    **submitted,
                )
            return _templates(request).TemplateResponse(
                request=request,
                name="settings.html",
                context=context,
                status_code=422,
            )
        values[field] = value

    with open_db(settings.db_path) as conn:
        set_display_preferences(
            conn,
            hide_low_confidence=preferences["hide_low_confidence"],
            hide_reason_text=preferences["hide_reason_text"],
        )
        set_max_new_items(conn, values["max_new_items"])
        set_max_items_per_feed(conn, values["max_items_per_feed"])
        set_window_hours(conn, values["window_hours"])
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/profiles/{profile_id}/activate")
def activate_profile(profile_id: str, request: Request) -> RedirectResponse:
    with open_db(_settings(request).db_path) as conn:
        _profile_or_404(conn, profile_id)
        set_active_profile(conn, profile_id)
    return RedirectResponse(f"/profiles/{profile_id}/digest", status_code=303)


@router.get("/profiles/{profile_id}/digest", response_class=HTMLResponse)
def digest(profile_id: str, request: Request) -> HTMLResponse:
    settings = _settings(request)
    with open_db(settings.db_path) as conn:
        profile = _profile_or_404(conn, profile_id)
        feeds = conn.execute(
            """
            SELECT id, title, url, enabled, last_fetched_at, last_status
            FROM feeds
            WHERE profile_id = ?
            ORDER BY title
            """,
            (profile_id,),
        ).fetchall()
        digest = build_digest(conn, settings, profile_id)
        job = latest_job(conn, profile_id)
        stop_requested = (
            request.app.state.jobs.is_stop_requested(int(job["id"]))
            if job is not None
            else False
        )
        profiles = _profile_choices(conn)
        active_profile_id = _active_profile_id(conn)
        max_new_items = get_max_new_items(conn, settings.max_new_items)
        max_items_per_feed = get_max_items_per_feed(conn, settings.max_items_per_feed)
        last_refreshed_at = _last_refreshed_at(conn, profile_id)

    return _templates(request).TemplateResponse(
        request=request,
        name="digest.html",
        context={
            "profile": profile,
            "profiles": profiles,
            "active_profile_id": active_profile_id,
            "feeds": [dict(row) for row in feeds],
            "digest": digest,
            "job": job,
            "stop_requested": stop_requested,
            "settings": settings,
            "max_new_items": max_new_items,
            "max_items_per_feed": max_items_per_feed,
            "pending_items": digest["pending_items"],
            "last_refreshed_at": last_refreshed_at,
        },
    )


@router.post("/profiles/{profile_id}/refresh")
def start_refresh(profile_id: str, request: Request) -> Response:
    with open_db(_settings(request).db_path) as conn:
        _profile_or_404(conn, profile_id)

    job_id, created = request.app.state.jobs.enqueue_refresh(profile_id)
    notice = None if created else "A job is already running."

    if _is_htmx(request):
        with open_db(_settings(request).db_path) as conn:
            return _render_job_panel(request, conn, job_id, notice=notice)
    return RedirectResponse(
        f"/profiles/{profile_id}/digest?job={job_id}",
        status_code=303,
    )


@router.post("/profiles/{profile_id}/classify")
def start_classify(profile_id: str, request: Request) -> Response:
    with open_db(_settings(request).db_path) as conn:
        _profile_or_404(conn, profile_id)

    job_id, created = request.app.state.jobs.enqueue_classify(profile_id)
    notice = None if created else "A job is already running."

    if _is_htmx(request):
        with open_db(_settings(request).db_path) as conn:
            return _render_job_panel(request, conn, job_id, notice=notice)
    return RedirectResponse(
        f"/profiles/{profile_id}/digest?job={job_id}",
        status_code=303,
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_fragment(job_id: int, request: Request) -> HTMLResponse:
    with open_db(_settings(request).db_path) as conn:
        return _render_job_panel(
            request,
            conn,
            job_id,
            refresh_page_on_done=True,
        )


@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: int, request: Request) -> Response:
    runner = request.app.state.jobs
    with open_db(_settings(request).db_path) as conn:
        job = _job_or_404(conn, job_id)

    if job["status"] not in ("queued", "running", "preview"):
        notice = "This job is no longer running."
    elif job["phase"] != "classifying":
        notice = "Stop is available while classification is running."
    elif not runner.request_stop(job_id):
        notice = "The job is not ready to stop yet."
    else:
        notice = None

    if _is_htmx(request):
        with open_db(_settings(request).db_path) as conn:
            return _render_job_panel(request, conn, job_id, notice=notice)
    return RedirectResponse(
        f"/profiles/{job['profile_id']}/digest",
        status_code=303,
    )


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, request: Request) -> Response:
    runner = request.app.state.jobs
    try:
        new_job_id, created = runner.enqueue_retry(job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobConflict as exc:
        with open_db(_settings(request).db_path) as conn:
            job = _job_or_404(conn, job_id)
            if _is_htmx(request):
                return _render_job_panel(request, conn, job_id, notice=str(exc))
        return RedirectResponse(
            f"/profiles/{job['profile_id']}/digest",
            status_code=303,
        )

    notice = None if created else "A job is already running."
    if _is_htmx(request):
        with open_db(_settings(request).db_path) as conn:
            return _render_job_panel(request, conn, new_job_id, notice=notice)
    with open_db(_settings(request).db_path) as conn:
        job = _job_or_404(conn, new_job_id)
    return RedirectResponse(
        f"/profiles/{job['profile_id']}/digest",
        status_code=303,
    )


@router.get("/healthz")
def healthz(request: Request) -> dict[str, object]:
    with open_db(_settings(request).db_path) as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("profiles", "categories", "feeds", "items", "classifications", "jobs")
        }
    return {"status": "ok", **counts}
