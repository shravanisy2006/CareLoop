"""Settings come from the environment, with safe production behaviour."""

from __future__ import annotations

import pytest

from careloop.config import load_settings

KEYS = (
    "CARELOOP_ENV",
    "CARELOOP_SECRET_KEY",
    "CARELOOP_STORAGE",
    "CARELOOP_GMAIL_ADDRESS",
    "CARELOOP_GMAIL_APP_PASSWORD",
    "CARELOOP_CYCLE_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


def test_production_refuses_to_start_without_a_secret_key(monkeypatch):
    monkeypatch.setenv("CARELOOP_ENV", "production")
    with pytest.raises(RuntimeError, match="CARELOOP_SECRET_KEY"):
        load_settings(env_file=None)


def test_development_uses_a_temporary_key():
    settings = load_settings(env_file=None)
    assert len(settings.secret_key) >= 32
    assert settings.storage_backend == "sqlite"
    assert settings.email_configured is False


def test_email_is_configured_only_with_both_values(monkeypatch):
    monkeypatch.setenv("CARELOOP_GMAIL_ADDRESS", "careloop@gmail.com")
    assert load_settings(env_file=None).email_configured is False
    monkeypatch.setenv("CARELOOP_GMAIL_APP_PASSWORD", "abcdabcdabcdabcd")
    assert load_settings(env_file=None).email_configured is True


def test_unknown_storage_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("CARELOOP_STORAGE", "mysql")
    with pytest.raises(RuntimeError, match="CARELOOP_STORAGE"):
        load_settings(env_file=None)


def test_cycle_interval_has_a_floor(monkeypatch):
    monkeypatch.setenv("CARELOOP_CYCLE_SECONDS", "1")
    assert load_settings(env_file=None).cycle_seconds == 15


def test_dotenv_file_is_read_but_environment_wins(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CARELOOP_STORAGE=firestore\nCARELOOP_GMAIL_ADDRESS=from-file@gmail.com\n")
    monkeypatch.setenv("CARELOOP_STORAGE", "sqlite")
    settings = load_settings(env_file=env_file)
    assert settings.storage_backend == "sqlite"
    assert settings.gmail_address == "from-file@gmail.com"
