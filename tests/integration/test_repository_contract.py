"""The storage contract, run against every backend.

SQLite always runs. Firestore runs only when FIRESTORE_EMULATOR_HOST points at
a running Firestore emulator (see TESTING.md); otherwise it is skipped and
reported as skipped - never as passed.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from careloop.domain.errors import ConcurrencyConflict, ValidationFailed
from careloop.domain.models import (
    AuditEvent,
    ChangeSet,
    Circle,
    Dose,
    DoseStatus,
    EscalationLevel,
    EscalationPolicy,
    Medication,
    MedicationDetails,
    MedicationStatus,
    Member,
    Notification,
    NotificationKind,
    NotificationStatus,
    Role,
    bump,
)
from careloop.storage.sqlite_repository import SqliteRepository

NOW = datetime(2026, 11, 15, 2, 0, tzinfo=timezone.utc)


def _sqlite(tmp_path):
    return SqliteRepository(tmp_path / "contract.db")


def _firestore(_tmp_path):
    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        pytest.skip("FIRESTORE_EMULATOR_HOST not set; Firestore contract not run")
    from google.cloud import firestore

    from careloop.storage.firestore_repository import FirestoreRepository

    return FirestoreRepository(firestore.Client(project=f"careloop-test-{uuid.uuid4().hex[:8]}"))


@pytest.fixture(params=[_sqlite, _firestore], ids=["sqlite", "firestore"])
def repo(request, tmp_path):
    return request.param(tmp_path)


def _uid() -> str:
    return uuid.uuid4().hex


def _audit(circle_id: str, action="test") -> AuditEvent:
    return AuditEvent(circle_id, None, "system", action, "test", "x", NOW)


def _seed(repo):
    circle = Circle(
        _uid(), uuid.uuid4().hex[:8].upper(), "Asha", "Asia/Kolkata", EscalationPolicy(), NOW
    )
    elder = Member(
        _uid(), circle.id, "Asha", Role.ELDER, "a@example.com", "hash", 0, None, True, NOW
    )
    repo.create_circle(circle, elder, _audit(circle.id, "circle.created"))
    return circle, elder


def _medication(circle_id: str) -> Medication:
    details = MedicationDetails("Metformin", "1 tablet", "", ("08:00",), date(2026, 11, 15), None)
    return Medication(_uid(), circle_id, details, MedicationStatus.ACTIVE, 1, NOW, NOW)


def _dose(circle_id: str, med_id: str, hours=0) -> Dose:
    when = NOW + timedelta(hours=hours)
    return Dose(
        f"{med_id}-{hours}",
        circle_id,
        med_id,
        when,
        DoseStatus.SCHEDULED,
        EscalationLevel.NONE,
        None,
        None,
        None,
        NOW,
    )


def test_circle_lookup_by_id_and_code(repo):
    circle, elder = _seed(repo)
    assert repo.get_circle(circle.id) == circle
    assert repo.get_circle_by_code(circle.code).id == circle.id
    assert repo.find_member_by_name(circle.id, "asha").id == elder.id


def test_member_names_are_unique_within_a_circle(repo):
    circle, _ = _seed(repo)
    duplicate = Member(_uid(), circle.id, "ASHA", Role.FAMILY, None, "h", 0, None, True, NOW)
    with pytest.raises(ValidationFailed):
        repo.add_member(duplicate, _audit(circle.id))


def test_versioned_update_rejects_stale_writes_atomically(repo):
    circle, _ = _seed(repo)
    med = _medication(circle.id)
    repo.apply_changeset(ChangeSet(medications=[(med, None)]))
    repo.apply_changeset(ChangeSet(medications=[(bump(med, updated_at=NOW), 1)]))
    stale = ChangeSet(
        medications=[(bump(med, updated_at=NOW), 1)], audit_events=[_audit(circle.id, "stale")]
    )
    with pytest.raises(ConcurrencyConflict):
        repo.apply_changeset(stale)
    assert repo.get_medication(med.id).version == 2
    assert "stale" not in [e.action for e in repo.list_audit_events(circle.id)]


def test_doses_are_inserted_once(repo):
    circle, _ = _seed(repo)
    med = _medication(circle.id)
    repo.apply_changeset(ChangeSet(medications=[(med, None)]))
    doses = [_dose(circle.id, med.id, 1), _dose(circle.id, med.id, 2)]
    assert repo.insert_doses_if_absent(doses) == 2
    assert repo.insert_doses_if_absent(doses) == 0


def test_resolve_and_escalate_are_conditional(repo):
    circle, elder = _seed(repo)
    med = _medication(circle.id)
    repo.apply_changeset(ChangeSet(medications=[(med, None)]))
    dose = _dose(circle.id, med.id, -1)
    repo.insert_doses_if_absent([dose])
    assert [d.id for d in repo.list_open_doses_due_by(NOW)] == [dose.id]

    assert repo.advance_escalation(
        dose.id, EscalationLevel.NONE, EscalationLevel.ELDER_REMINDED, [], _audit(circle.id)
    )
    assert not repo.advance_escalation(
        dose.id, EscalationLevel.NONE, EscalationLevel.ELDER_REMINDED, [], _audit(circle.id)
    )

    assert repo.resolve_dose(
        dose.id, DoseStatus.TAKEN, elder.id, Role.ELDER, NOW, _audit(circle.id)
    )
    assert not repo.resolve_dose(
        dose.id, DoseStatus.TAKEN, elder.id, Role.ELDER, NOW, _audit(circle.id)
    )
    assert not repo.advance_escalation(
        dose.id,
        EscalationLevel.ELDER_REMINDED,
        EscalationLevel.CARETAKER_NOTIFIED,
        [],
        _audit(circle.id),
    )
    assert not repo.close_dose_unconfirmed(
        dose.id, EscalationLevel.ELDER_REMINDED, _audit(circle.id)
    )
    assert repo.get_dose(dose.id).status is DoseStatus.TAKEN


def test_notifications_are_deduplicated_and_tracked(repo):
    circle, elder = _seed(repo)
    note = Notification(
        _uid(),
        circle.id,
        NotificationKind.PROPOSAL_PENDING,
        "p1",
        elder.id,
        elder.email,
        "s",
        "b",
        NotificationStatus.PENDING,
        0,
        "",
        NOW,
    )
    assert repo.enqueue_notifications([note]) == 1
    assert repo.enqueue_notifications([note]) == 0
    assert [n.id for n in repo.list_deliverable_notifications(3, 10)] == [note.id]
    repo.update_notification_status(note.id, NotificationStatus.SENT, 1, "", NOW)
    assert repo.list_deliverable_notifications(3, 10) == []
    assert repo.list_notifications(circle.id)[0].status is NotificationStatus.SENT


def test_audit_log_is_append_only_in_sqlite(tmp_path):
    repo = _sqlite(tmp_path)
    circle, _ = _seed(repo)
    conn = sqlite3.connect(tmp_path / "contract.db")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_events SET action = 'tampered'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_events")
    conn.close()
    assert [e.action for e in repo.list_audit_events(circle.id)] == ["circle.created"]


def test_sqlite_rejects_impossible_states(tmp_path):
    repo = _sqlite(tmp_path)
    circle, _ = _seed(repo)
    conn = sqlite3.connect(tmp_path / "contract.db")
    with pytest.raises(sqlite3.IntegrityError):  # "taken" without who/when
        conn.execute(
            "INSERT INTO doses (id, circle_id, medication_id, scheduled_for, status, created_at)"
            " VALUES ('x', ?, 'm', '2026', 'taken', '2026')",
            (circle.id,),
        )
    conn.close()
