"""Settings, read from environment variables (see .env.example).

Secrets are only ever read from the environment; nothing secret is committed.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing environment wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    secret_key: str
    storage_backend: str  # "sqlite" or "firestore"
    sqlite_path: str
    firestore_project: str | None
    gmail_address: str | None
    gmail_app_password: str | None
    public_url: str
    secure_cookies: bool
    scheduler_enabled: bool
    cycle_seconds: int
    default_timezone: str
    max_email_attempts: int = 3
    session_max_age_seconds: int = 8 * 60 * 60

    @property
    def email_configured(self) -> bool:
        return bool(self.gmail_address and self.gmail_app_password)


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    if env_file is not None:
        _load_dotenv(env_file)

    secret_key = os.environ.get("CARELOOP_SECRET_KEY", "")
    if len(secret_key) < 32:
        if os.environ.get("CARELOOP_ENV", "development") == "production":
            raise RuntimeError("CARELOOP_SECRET_KEY must be set to at least 32 characters.")
        logger.warning(
            "CARELOOP_SECRET_KEY is not set; using a temporary key. Sessions end on restart."
        )
        secret_key = secrets.token_urlsafe(48)

    backend = os.environ.get("CARELOOP_STORAGE", "sqlite").strip().lower()
    if backend not in {"sqlite", "firestore"}:
        raise RuntimeError("CARELOOP_STORAGE must be 'sqlite' or 'firestore'.")

    return Settings(
        secret_key=secret_key,
        storage_backend=backend,
        sqlite_path=os.environ.get("CARELOOP_SQLITE_PATH", "careloop.db"),
        firestore_project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None,
        gmail_address=os.environ.get("CARELOOP_GMAIL_ADDRESS") or None,
        gmail_app_password=os.environ.get("CARELOOP_GMAIL_APP_PASSWORD") or None,
        public_url=os.environ.get("CARELOOP_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"),
        secure_cookies=_flag("CARELOOP_SECURE_COOKIES", False),
        scheduler_enabled=_flag("CARELOOP_SCHEDULER_ENABLED", True),
        cycle_seconds=max(15, int(os.environ.get("CARELOOP_CYCLE_SECONDS", "60"))),
        default_timezone=os.environ.get("CARELOOP_DEFAULT_TIMEZONE", "Asia/Kolkata"),
    )
