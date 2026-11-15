"""Shared test fixtures.

Tests use a real SQLite database in a temporary folder, a clock that only
moves when told to, and a recording email sender in place of Gmail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from careloop.clock import FixedClock
from careloop.config import Settings
from careloop.domain.models import Member
from careloop.domain.validation import validate_medication_input
from careloop.notifications.email_sender import EmailDeliveryError, OutgoingEmail
from careloop.services.container import Services, build_services
from careloop.storage.sqlite_repository import SqliteRepository

# 08:00 in India (UTC+05:30) on 15 Nov 2026 is 02:30 UTC.
START = datetime(2026, 11, 15, 2, 0, tzinfo=timezone.utc)  # 07:30 IST
ELDER_PASSCODE = "river-blue-42"
CARETAKER_PASSCODE = "mango-tree-77"
FAMILY_PASSCODE = "kite-sky-19"


@dataclass
class RecordingSender:
    """Stands in for Gmail. Records what would be sent; can be told to fail."""

    sent: list[OutgoingEmail] = field(default_factory=list)
    failures_remaining: int = 0

    def send(self, message: OutgoingEmail) -> None:
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise EmailDeliveryError("simulated SMTP failure")
        self.sent.append(message)

    def to(self, address: str) -> list[OutgoingEmail]:
        return [m for m in self.sent if m.to == address]


def make_settings(tmp_path, **overrides) -> Settings:
    values = dict(
        secret_key="test-secret-key-that-is-long-enough-1234567890",
        storage_backend="sqlite",
        sqlite_path=str(tmp_path / "test.db"),
        firestore_project=None,
        gmail_address="careloop@example.com",
        gmail_app_password="not-a-real-password",
        public_url="http://testserver",
        secure_cookies=False,
        scheduler_enabled=False,
        cycle_seconds=60,
        default_timezone="Asia/Kolkata",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(START)


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def services(settings, clock, sender) -> Services:
    return build_services(
        settings, repo=SqliteRepository(settings.sqlite_path), clock=clock, email_sender=sender
    )


@dataclass
class Circle3:
    elder: Member
    caretaker: Member
    family: Member
    code: str


@pytest.fixture
def circle(services: Services) -> Circle3:
    created, elder = services.circles.create_circle(
        elder_name="Asha Rao",
        passcode=ELDER_PASSCODE,
        confirm_passcode=ELDER_PASSCODE,
        email="asha@example.com",
        timezone="Asia/Kolkata",
    )
    caretaker = services.circles.add_member(
        elder,
        display_name="Ravi",
        role="caretaker",
        email="ravi@example.com",
        passcode=CARETAKER_PASSCODE,
    )
    family = services.circles.add_member(
        elder,
        display_name="Meera",
        role="family",
        email="meera@example.com",
        passcode=FAMILY_PASSCODE,
    )
    return Circle3(elder=elder, caretaker=caretaker, family=family, code=created.code)


def medication_details(**overrides):
    values = dict(
        name="Metformin",
        dosage="1 tablet (500 mg)",
        instructions="With breakfast",
        times="08:00",
        start_date="2026-11-15",
        end_date="",
    )
    values.update(overrides)
    return validate_medication_input(**values)
