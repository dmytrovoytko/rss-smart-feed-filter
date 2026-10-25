from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import Settings, get_settings
from app.db import init_db, open_db
from app.logging_config import configure_logging
from app.routes import pages
from app.seed import seed_database
from app.services.jobs import JobRunner
from app.timeutil import human_time

log = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)
    jobs = JobRunner(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(app_settings.db_path)
        with open_db(app_settings.db_path) as conn:
            seed_database(conn, app_settings)
        jobs.start()
        log.info(
            "app ready: db=%s model=%s ollama=%s",
            app_settings.db_path,
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
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["human_time"] = human_time
    app.state.templates = templates
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(pages.router)
    return app


app = create_app()
