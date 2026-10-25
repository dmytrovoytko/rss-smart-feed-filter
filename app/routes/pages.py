from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.db import open_db
from app.services.digest import build_digest
from app.services.jobs import JobConflict, JobNotFound, get_job, latest_job

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
    job = _job_or_404(conn, job_id)
    response = _templates(request).TemplateResponse(
        request=request,
        name="partials/job.html",
        context={"job": job, "notice": notice},
    )
    if refresh_page_on_done and job["status"] == "done" and _is_htmx(request):
        response.headers["HX-Refresh"] = "true"
    return response


@router.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse(FAVICON_PATH)


@router.get("/")
def index(request: Request) -> RedirectResponse:
    with open_db(_settings(request).db_path) as conn:
        profile_id = _active_profile_id(conn)
    if profile_id is None:
        raise HTTPException(status_code=404, detail="no profile configured")
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

    return _templates(request).TemplateResponse(
        request=request,
        name="digest.html",
        context={
            "profile": profile,
            "feeds": [dict(row) for row in feeds],
            "digest": digest,
            "job": job,
            "settings": settings,
        },
    )


@router.post("/profiles/{profile_id}/refresh")
def start_refresh(profile_id: str, request: Request) -> Response:
    with open_db(_settings(request).db_path) as conn:
        _profile_or_404(conn, profile_id)

    job_id, created = request.app.state.jobs.enqueue_refresh(profile_id)
    notice = None if created else "A refresh is already running."

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

    notice = None if created else "A refresh is already running."
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
