from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import Settings, get_settings
from app.db import init_db, open_db
from app.logging_config import configure_logging
from app.observability import (
    ObservabilityStore,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)
from app.routes import observability as observability_routes
from app.routes import pages
from app.seed import seed_database
from app.services.jobs import JobRunner
from app.timeutil import human_time, utc_now_iso

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)
    observability = ObservabilityStore(app_settings.obs_db_path)
    jobs = JobRunner(app_settings, observability=observability)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app_settings.db_path)
        with open_db(app_settings.db_path) as conn:
            seed_database(conn, app_settings)
        try:
            observability.init()
        except Exception:
            log.warning(
                "observability database unavailable: %s",
                app_settings.obs_db_path,
                exc_info=True,
            )
        jobs.start()
        log.info(
            "app ready: db=%s obs_db=%s model=%s ollama=%s",
            app_settings.db_path,
            app_settings.obs_db_path,
            app_settings.tev_model,
            app_settings.ollama_base_url,
        )
        try:
            yield
        finally:
            jobs.stop()

    app = FastAPI(title="Feed Filter", version=__version__, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.jobs = jobs
    app.state.observability = observability

    @app.middleware("http")
    async def observability_middleware(
        request: Request,
        call_next,
    ) -> Response:
        path = request.url.path
        if path.startswith("/static/") or path in {"/favicon.ico", "/healthz"}:
            return await call_next(request)

        trace_id = new_trace_id()
        token = set_trace_id(trace_id)
        started = time.perf_counter()
        started_at = utc_now_iso()
        observability.start_trace(
            trace_id,
            kind="http",
            name=f"{request.method} {path}",
            started_at=started_at,
        )
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            status = "ok" if response.status_code < 500 else "error"
            level = "info" if response.status_code < 500 else "error"
            attributes = {
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
            }
            observability.record_span(
                trace_id,
                "http.request",
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_ms=duration_ms,
                status=status,
                attributes=attributes,
            )
            observability.record_log(
                trace_id,
                level,
                "request.finished",
                fields=attributes,
            )
            observability.record_metric(
                trace_id,
                "http.request.duration_ms",
                duration_ms,
                unit="ms",
                labels={"method": request.method, "status": response.status_code},
            )
            observability.finish_trace(
                trace_id,
                status=status,
                duration_ms=duration_ms,
            )
            response.headers["X-Trace-ID"] = trace_id
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            observability.record_span(
                trace_id,
                "http.request",
                started_at=started_at,
                finished_at=utc_now_iso(),
                duration_ms=duration_ms,
                status="error",
                attributes={"method": request.method, "path": path},
                error=exc,
            )
            observability.record_log(
                trace_id,
                "error",
                "request.failed",
                fields={
                    "method": request.method,
                    "path": path,
                    "error_type": exc.__class__.__name__,
                },
            )
            observability.record_metric(
                trace_id,
                "http.request.duration_ms",
                duration_ms,
                unit="ms",
                labels={"method": request.method, "status": 500},
            )
            observability.finish_trace(
                trace_id,
                status="error",
                error=exc.__class__.__name__,
                duration_ms=duration_ms,
            )
            raise
        finally:
            reset_trace_id(token)

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["human_time"] = human_time
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(pages.router)
    app.include_router(observability_routes.router)
    return app


app = create_app()
