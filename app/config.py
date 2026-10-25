from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "profiles"


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding the real environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or not value.strip() else value.strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    tev_model: str
    db_path: Path
    log_path: Path
    log_level: str
    host: str
    port: int
    max_new_items: int
    retention_days: int
    window_hours: int
    rescore_days: int
    threshold: float
    profiles_dir: Path = PROFILES_DIR

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> Settings:
        _load_dotenv(env_file or (ROOT / ".env"))
        settings = cls(
            ollama_base_url=_env_str("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            tev_model=_env_str("TEV_MODEL", "tev1:0.8b"),
            db_path=_resolve_path(_env_str("DB_PATH", "./data/feedfilter.db")),
            log_path=_resolve_path(_env_str("LOG_PATH", "./data/feedfilter.log")),
            log_level=_env_str("LOG_LEVEL", "INFO").upper(),
            host=_env_str("HOST", "127.0.0.1"),
            port=_env_int("PORT", 8000),
            max_new_items=_env_int("MAX_NEW_ITEMS", 50),
            retention_days=_env_int("RETENTION_DAYS", 30),
            window_hours=_env_int("WINDOW_HOURS", 72),
            rescore_days=_env_int("RESCORE_DAYS", 14),
            threshold=_env_float("THRESHOLD", 0.55),
            profiles_dir=_resolve_path(_env_str("PROFILES_DIR", str(PROFILES_DIR))),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.ollama_base_url.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://")
        if not 1 <= self.port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        if self.max_new_items < 1:
            raise ValueError("MAX_NEW_ITEMS must be at least 1")
        if self.retention_days < 1:
            raise ValueError("RETENTION_DAYS must be at least 1")
        if self.window_hours < 1:
            raise ValueError("WINDOW_HOURS must be at least 1")
        if self.rescore_days < 1:
            raise ValueError("RESCORE_DAYS must be at least 1")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("THRESHOLD must be between 0.0 and 1.0")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
