"""SQLite implementation of the Repository contract (the default backend).

* Every query uses bound parameters; no user input is ever formatted into SQL.
* A new connection is opened per operation, which is safe across the web
  server's worker threads and the background care cycle.
* Writes run in ``BEGIN IMMEDIATE`` transactions so conditional updates
  (confirm a dose, climb an escalation level, approve a proposal) cannot
  interleave.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from pathlib import Path

from careloop.domain.errors import ConcurrencyConflict, ValidationFailed
from careloop.domain.models import (
    Appointment,
    AuditEvent,
    ChangeSet,
    Circle,
    Dose,
    DoseStatus,
    EscalationLevel,
    EscalationPolicy,
    Medication,
    Member,
    Notification,
    NotificationStatus,
    Proposal,
    ProposalStatus,
    Role,
)
from careloop.storage import records as rec

#: Column lists are fixed here in code (never derived from user input).
_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {}


def _insert_sql(table: str, record: rec.Record, *, or_ignore: bool = False) -> str:
    columns = _TABLE_COLUMNS.setdefault(table, tuple(record.keys()))
    verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
    placeholders = ", ".join(f":{c}" for c in columns)
    return f"{verb} INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"


class SqliteRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self.init_schema()

    # --- Connection handling ---------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        schema = resources.files("careloop.storage").joinpath("sqlite_schema.sql").read_text()
        with self._read() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(schema)

    @staticmethod
    def _audit(conn: sqlite3.Connection, event: AuditEvent) -> None:
        record = rec.audit_to_record(event)
        conn.execute(_insert_sql("audit_events", record), record)

    # --- Care circles and members ----------------------------------------

    def create_circle(self, circle: Circle, elder: Member, audit: AuditEvent) -> None:
        with self._tx() as conn:
            c = rec.circle_to_record(circle)
            conn.execute(_insert_sql("circles", c), c)
            m = rec.member_to_record(elder)
            conn.execute(_insert_sql("members", m), m)
            self._audit(conn, audit)

    def get_circle(self, circle_id: str) -> Circle | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM circles WHERE id = ?", (circle_id,)).fetchone()
        return rec.record_to_circle(dict(row)) if row else None

    def get_circle_by_code(self, code: str) -> Circle | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM circles WHERE code = ?", (code,)).fetchone()
        return rec.record_to_circle(dict(row)) if row else None

    def list_circles(self) -> list[Circle]:
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM circles ORDER BY created_at").fetchall()
        return [rec.record_to_circle(dict(r)) for r in rows]

    def update_circle_policy(
        self, circle_id: str, policy: EscalationPolicy, audit: AuditEvent
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE circles SET caretaker_after_minutes = ?, family_after_minutes = ?,"
                " close_after_minutes = ? WHERE id = ?",
                (
                    policy.caretaker_after_minutes,
                    policy.family_after_minutes,
                    policy.close_after_minutes,
                    circle_id,
                ),
            )
            self._audit(conn, audit)

    def add_member(self, member: Member, audit: AuditEvent) -> None:
        record = rec.member_to_record(member)
        try:
            with self._tx() as conn:
                conn.execute(_insert_sql("members", record), record)
                self._audit(conn, audit)
        except sqlite3.IntegrityError as exc:
            if "name_key" in str(exc):
                raise ValidationFailed(
                    {"display_name": "Someone in this care circle already uses that name."}
                ) from None
            raise

    def get_member(self, member_id: str) -> Member | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        return rec.record_to_member(dict(row)) if row else None

    def find_member_by_name(self, circle_id: str, name_key: str) -> Member | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM members WHERE circle_id = ? AND name_key = ?",
                (circle_id, name_key),
            ).fetchone()
        return rec.record_to_member(dict(row)) if row else None

    def list_members(self, circle_id: str) -> list[Member]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM members WHERE circle_id = ? ORDER BY created_at", (circle_id,)
            ).fetchall()
        return [rec.record_to_member(dict(r)) for r in rows]

    def record_login_attempt(
        self, member_id: str, failed_attempts: int, locked_until: datetime | None
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE members SET failed_attempts = ?, locked_until = ? WHERE id = ?",
                (failed_attempts, rec.ts(locked_until), member_id),
            )

    def deactivate_member(self, member_id: str, audit: AuditEvent) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE members SET active = 0 WHERE id = ?", (member_id,))
            self._audit(conn, audit)

    # --- Medications, appointments, proposals ----------------------------

    def get_medication(self, medication_id: str) -> Medication | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM medications WHERE id = ?", (medication_id,)
            ).fetchone()
        return rec.record_to_medication(dict(row)) if row else None

    def list_medications(self, circle_id: str) -> list[Medication]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM medications WHERE circle_id = ? ORDER BY status, name",
                (circle_id,),
            ).fetchall()
        return [rec.record_to_medication(dict(r)) for r in rows]

    def get_appointment(self, appointment_id: str) -> Appointment | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM appointments WHERE id = ?", (appointment_id,)
            ).fetchone()
        return rec.record_to_appointment(dict(row)) if row else None

    def list_appointments(self, circle_id: str) -> list[Appointment]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM appointments WHERE circle_id = ? ORDER BY starts_at", (circle_id,)
            ).fetchall()
        return [rec.record_to_appointment(dict(r)) for r in rows]

    def list_appointments_starting_between(
        self, start: datetime, end: datetime
    ) -> list[Appointment]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM appointments WHERE status = 'scheduled'"
                " AND starts_at >= ? AND starts_at < ? ORDER BY starts_at",
                (rec.ts(start), rec.ts(end)),
            ).fetchall()
        return [rec.record_to_appointment(dict(r)) for r in rows]

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return rec.record_to_proposal(dict(row)) if row else None

    def list_proposals(
        self, circle_id: str, status: ProposalStatus | None = None
    ) -> list[Proposal]:
        with self._read() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM proposals WHERE circle_id = ? ORDER BY created_at DESC",
                    (circle_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM proposals WHERE circle_id = ? AND status = ?"
                    " ORDER BY created_at DESC",
                    (circle_id, status.value),
                ).fetchall()
        return [rec.record_to_proposal(dict(r)) for r in rows]

    def apply_changeset(self, changes: ChangeSet) -> None:
        with self._tx() as conn:
            for proposal, expected_status in changes.proposals:
                record = rec.proposal_to_record(proposal)
                if expected_status is None:
                    conn.execute(_insert_sql("proposals", record), record)
                    continue
                cur = conn.execute(
                    "UPDATE proposals SET status = :status, decided_by = :decided_by,"
                    " decided_at = :decided_at, decision_note = :decision_note"
                    " WHERE id = :id AND status = :expected",
                    {**record, "expected": expected_status.value},
                )
                if cur.rowcount != 1:
                    raise ConcurrencyConflict("This request has already been decided.")

            for medication, expected_version in changes.medications:
                self._save_versioned(
                    conn, "medications", rec.medication_to_record(medication), expected_version
                )
            for appointment, expected_version in changes.appointments:
                self._save_versioned(
                    conn, "appointments", rec.appointment_to_record(appointment), expected_version
                )

            for medication_id, after in changes.cancel_future_doses:
                conn.execute(
                    "UPDATE doses SET status = 'cancelled' WHERE medication_id = ?"
                    " AND status = 'scheduled' AND scheduled_for > ?",
                    (medication_id, rec.ts(after)),
                )
            for notification in changes.notifications:
                record = rec.notification_to_record(notification)
                conn.execute(_insert_sql("notifications", record, or_ignore=True), record)
            for event in changes.audit_events:
                self._audit(conn, event)

    @staticmethod
    def _save_versioned(
        conn: sqlite3.Connection, table: str, record: rec.Record, expected_version: int | None
    ) -> None:
        if expected_version is None:
            conn.execute(_insert_sql(table, record), record)
            return
        columns = [c for c in record if c not in {"id", "circle_id", "created_at"}]
        assignments = ", ".join(f"{c} = :{c}" for c in columns)
        # Table and column names come from records.py (code), never from input;
        # every value is a bound parameter.
        cur = conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = :id AND version = :expected_version",  # noqa: S608
            {**record, "expected_version": expected_version},
        )
        if cur.rowcount != 1:
            raise ConcurrencyConflict()

    # --- Doses -------------------------------------------------------------

    def insert_doses_if_absent(self, doses: list[Dose]) -> int:
        if not doses:
            return 0
        created = 0
        with self._tx() as conn:
            for dose in doses:
                record = rec.dose_to_record(dose)
                cur = conn.execute(_insert_sql("doses", record, or_ignore=True), record)
                created += cur.rowcount
        return created

    def get_dose(self, dose_id: str) -> Dose | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM doses WHERE id = ?", (dose_id,)).fetchone()
        return rec.record_to_dose(dict(row)) if row else None

    def list_doses(self, circle_id: str, start: datetime, end: datetime) -> list[Dose]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM doses WHERE circle_id = ? AND scheduled_for >= ?"
                " AND scheduled_for < ? ORDER BY scheduled_for",
                (circle_id, rec.ts(start), rec.ts(end)),
            ).fetchall()
        return [rec.record_to_dose(dict(r)) for r in rows]

    def list_open_doses_due_by(self, now: datetime) -> list[Dose]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM doses WHERE status = 'scheduled' AND scheduled_for <= ?"
                " ORDER BY scheduled_for",
                (rec.ts(now),),
            ).fetchall()
        return [rec.record_to_dose(dict(r)) for r in rows]

    def resolve_dose(
        self,
        dose_id: str,
        status: DoseStatus,
        by_member: str,
        by_role: Role,
        at: datetime,
        audit: AuditEvent,
    ) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE doses SET status = ?, resolved_at = ?, resolved_by = ?,"
                " resolved_by_role = ? WHERE id = ? AND status IN ('scheduled', 'unconfirmed')",
                (status.value, rec.ts(at), by_member, by_role.value, dose_id),
            )
            if cur.rowcount != 1:
                return False
            # Any email not yet sent about this dose is no longer needed.
            conn.execute(
                "UPDATE notifications SET status = 'cancelled' WHERE ref_key = ?"
                " AND status IN ('pending', 'failed')",
                (dose_id,),
            )
            self._audit(conn, audit)
            return True

    def advance_escalation(
        self,
        dose_id: str,
        from_level: EscalationLevel,
        to_level: EscalationLevel,
        notifications: list[Notification],
        audit: AuditEvent,
    ) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE doses SET escalation_level = ? WHERE id = ?"
                " AND status = 'scheduled' AND escalation_level = ?",
                (int(to_level), dose_id, int(from_level)),
            )
            if cur.rowcount != 1:
                return False
            for notification in notifications:
                record = rec.notification_to_record(notification)
                conn.execute(_insert_sql("notifications", record, or_ignore=True), record)
            self._audit(conn, audit)
            return True

    def close_dose_unconfirmed(
        self, dose_id: str, from_level: EscalationLevel, audit: AuditEvent
    ) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE doses SET status = 'unconfirmed' WHERE id = ?"
                " AND status = 'scheduled' AND escalation_level = ?",
                (dose_id, int(from_level)),
            )
            if cur.rowcount != 1:
                return False
            self._audit(conn, audit)
            return True

    # --- Notifications -------------------------------------------------------

    def enqueue_notifications(self, notifications: list[Notification]) -> int:
        created = 0
        with self._tx() as conn:
            for notification in notifications:
                record = rec.notification_to_record(notification)
                cur = conn.execute(_insert_sql("notifications", record, or_ignore=True), record)
                created += cur.rowcount
        return created

    def list_deliverable_notifications(self, max_attempts: int, limit: int) -> list[Notification]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE status = 'pending'"
                " OR (status = 'failed' AND attempts < ?) ORDER BY created_at LIMIT ?",
                (max_attempts, limit),
            ).fetchall()
        return [rec.record_to_notification(dict(r)) for r in rows]

    def update_notification_status(
        self,
        notification_id: str,
        status: NotificationStatus,
        attempts: int,
        last_error: str,
        sent_at: datetime | None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE notifications SET status = ?, attempts = ?, last_error = ?, sent_at = ?"
                " WHERE id = ? AND status != 'cancelled'",
                (status.value, attempts, last_error[:300], rec.ts(sent_at), notification_id),
            )

    def list_notifications(self, circle_id: str, limit: int = 100) -> list[Notification]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications WHERE circle_id = ? ORDER BY created_at DESC LIMIT ?",
                (circle_id, limit),
            ).fetchall()
        return [rec.record_to_notification(dict(r)) for r in rows]

    # --- Audit -----------------------------------------------------------------

    def list_audit_events(self, circle_id: str, limit: int = 200) -> list[AuditEvent]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE circle_id = ? ORDER BY id DESC LIMIT ?",
                (circle_id, limit),
            ).fetchall()
        return [rec.record_to_audit(dict(r), str(r["id"])) for r in rows]
