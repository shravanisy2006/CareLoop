"""Database efficiency: every query uses an index, and page cost does not grow per record.

All SQL the SQLite repository runs is captured with sqlite3's trace callback while a realistic
workload runs (sign-in, dashboards, history, escalation cycles, proposals). Each captured SELECT,
UPDATE and DELETE is then checked with EXPLAIN QUERY PLAN.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing

import pytest

from careloop.services.container import build_services
from careloop.storage.sqlite_repository import SqliteRepository
from tests.conftest import CARETAKER_PASSCODE, ELDER_PASSCODE, medication_details
from tests.web.helpers import make_client, sign_in

PAGES = ["/", "/medications", "/appointments", "/proposals", "/history", "/activity", "/report"]


class TracingRepository(SqliteRepository):
    """SqliteRepository that records every statement it executes."""

    statements: list[str]

    def __init__(self, path: str) -> None:
        self.statements = []
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        conn = super()._connect()
        conn.set_trace_callback(self.statements.append)
        return conn


@pytest.fixture
def traced(settings, clock, sender) -> Iterator[tuple[object, TracingRepository]]:
    repo = TracingRepository(settings.sqlite_path)
    services = build_services(settings, repo=repo, clock=clock, email_sender=sender)
    yield services, repo


def _queries(statements: list[str]) -> list[str]:
    return [
        s
        for s in statements
        if s.lstrip().upper().startswith(("SELECT", "UPDATE", "DELETE"))
        and "sqlite_master" not in s
        and "pragma_" not in s.lower()
    ]


# The care cycle deliberately visits every circle once per run; reading all rows of the circles
# table is the cheapest plan for that, and no index could make it cheaper.
INTENDED_FULL_READS = {"SELECT * FROM circles ORDER BY created_at"}


def _full_scans(db_path: str, queries: list[str]) -> set[str]:
    """Statements whose plan reads a whole table instead of using an index."""
    bad = set()
    with closing(sqlite3.connect(db_path)) as conn:
        for sql in set(queries) - INTENDED_FULL_READS:
            for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}"):
                detail = row[-1]
                if detail.startswith("SCAN ") and "USING" not in detail:
                    bad.add(f"{detail}  <-  {sql[:120]}")
    return bad


def _workload(services, circle_code: str, clock) -> None:
    elder = make_client(services)
    sign_in(elder, circle_code, "Asha Rao", ELDER_PASSCODE)
    helper = make_client(services)
    sign_in(helper, circle_code, "Ravi", CARETAKER_PASSCODE)
    for minutes in (0, 31, 61, 200):
        clock.advance(minutes=minutes)
        services.cycle.run_once()
        for client in (elder, helper):
            for page in PAGES:
                assert client.get(page).status_code == 200


def _new_circle(services):
    created, elder = services.circles.create_circle(
        elder_name="Asha Rao",
        passcode=ELDER_PASSCODE,
        confirm_passcode=ELDER_PASSCODE,
        email="asha@example.com",
        timezone="Asia/Kolkata",
    )
    services.circles.add_member(
        elder,
        display_name="Ravi",
        role="caretaker",
        email="ravi@example.com",
        passcode=CARETAKER_PASSCODE,
    )
    return created, elder


def test_no_query_scans_a_whole_table(traced, settings, clock):
    services, repo = traced
    created, elder = _new_circle(services)
    for i in range(3):
        services.records.add_medication(elder, medication_details(name=f"Med {i}"))
    _workload(services, created.code, clock)

    queries = _queries(repo.statements)
    assert len(queries) > 50, "workload should exercise the repository"
    assert _full_scans(settings.sqlite_path, queries) == set()


def _dashboard_query_count(services, repo, code: str) -> int:
    client = make_client(services)
    sign_in(client, code, "Asha Rao", ELDER_PASSCODE)
    repo.statements.clear()
    assert client.get("/").status_code == 200
    return len(_queries(repo.statements))


def test_dashboard_query_count_does_not_grow_with_the_number_of_medicines(traced, clock):
    """Guards against N+1 queries: 1 medicine and 10 medicines cost the same number of queries."""
    services, repo = traced
    created, elder = _new_circle(services)
    services.records.add_medication(elder, medication_details(name="Med 0"))
    clock.advance(minutes=1)
    services.cycle.run_once()
    one = _dashboard_query_count(services, repo, created.code)

    for i in range(1, 10):
        services.records.add_medication(elder, medication_details(name=f"Med {i}"))
    services.cycle.run_once()
    ten = _dashboard_query_count(services, repo, created.code)

    assert ten == one, f"dashboard ran {one} queries with 1 medicine but {ten} with 10"
