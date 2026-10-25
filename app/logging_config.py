from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import Settings

LOGGER_TAG = "_feedfilter_handler"


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    if any(getattr(handler, LOGGER_TAG, False) for handler in root.handlers):
        return

    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.log_level, logging.INFO)
    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = RotatingFileHandler(
        settings.log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    setattr(file_handler, LOGGER_TAG, True)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)
    setattr(stream_handler, LOGGER_TAG, True)

    root.addHandler(file_handler)
    root.addHandler(stream_handler)
