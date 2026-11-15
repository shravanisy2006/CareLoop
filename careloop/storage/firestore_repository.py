"""Google Cloud Firestore implementation of the Repository contract.

Selected with ``CARELOOP_STORAGE=firestore``. It stores the same flat records
as the SQLite backend (see ``records.py``), one top-level collection per
entity, each document carrying ``circle_id``.

How the guarantees are kept in Firestore:

* Conditional updates (confirm a dose, climb an escalation level, decide a
  proposal) run inside Firestore transactions that read the document, check
  its state, then write. Firestore retries the transaction if another writer
  touched the same documents.
* Uniqueness (circle codes, member names per circle, one dose per medication
  per time, one notification per kind/subject/recipient) comes from
  deterministic document ids and ``create`` semantics.
* Audit events are only ever created. This class has no update or delete path
  for them, and ``firestore.rules`` denies all client-side access.

Composite indexes this backend needs are declared in ``firestore.indexes.json``.

Verification status: this module is exercised by
``tests/integration/test_repository_contract.py`` when ``FIRESTORE_EMULATOR_HOST``
is set. See TESTING.md for whether that run has been done.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

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

CIRCLES = "circles"
CIRCLE_CODES = "circle_codes"
MEMBERS = "members"
MEMBER_NAMES = "member_names"
MEDICATIONS = "medications"
APPOINTMENTS = "appointments"
DOSES = "doses"
PROPOSALS = "proposals"
NOTIFICATIONS = "notifications"
AUDIT_EVENTS = "audit_events"

RESOLVABLE = {DoseStatus.SCHEDULED.value, DoseStatus.UNCONFIRMED.value}


def _name_doc_id(circle_id: str, name_key: str) -> str:
    return hashlib.sha256(f"{circle_id}\x00{name_key}".encode()).hexdigest()


def _eq(field: str, value: Any) -> FieldFilter:
    return FieldFilter(field, "==", value)


class FirestoreRepository:
    def __init__(self, client: firestore.Client) -> None:
        self._db = client

    @classmethod
    def from_project(cls, project: str | None) -> FirestoreRepository:
        """Uses Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS)
        or the emulator when FIRESTORE_EMULATOR_HOST is set."""
        return cls(firestore.Client(project=project))

    def init_schema(self) -> None:
        """Firestore is schemaless; indexes are deployed from firestore.indexes.json."""

    # --- helpers ----------------------------------------------------------

    def _col(self, name: str) -> firestore.CollectionReference:
        return self._db.collection(name)

    def _get(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        snap = self._col(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None

    def _where(self, collection: str, *filters: FieldFilter) -> list[dict[str, Any]]:
        query: Any = self._col(collection)
        for f in filters:
            query = query.where(filter=f)
        return [snap.to_dict() for snap in query.stream()]

    def _audit_write(self, tx: firestore.Transaction, event: AuditEvent) -> None:
        tx.create(self._col(AUDIT_EVENTS).document(), rec.audit_to_record(event))

    def _run(self, fn: Any) -> Any:
        """Run `fn(transaction)` as a Firestore transaction (retried on contention)."""
        return firestore.transactional(fn)(self._db.transaction())

    # --- Care circles and members ------------------------------------------

    def create_circle(self, circle: Circle, elder: Member, audit: AuditEvent) -> None:
        def work(tx: firestore.Transaction) -> None:
            code_ref = self._col(CIRCLE_CODES).document(circle.code)
            if code_ref.get(transaction=tx).exists:
                raise ConcurrencyConflict("Circle code collision; please try again.")
            tx.create(code_ref, {"circle_id": circle.id})
            tx.create(self._col(CIRCLES).document(circle.id), rec.circle_to_record(circle))
            tx.create(
                self._col(MEMBER_NAMES).document(_name_doc_id(circle.id, elder.name_key)),
                {"member_id": elder.id},
            )
            tx.create(self._col(MEMBERS).document(elder.id), rec.member_to_record(elder))
            self._audit_write(tx, audit)

        self._run(work)

    def get_circle(self, circle_id: str) -> Circle | None:
        record = self._get(CIRCLES, circle_id)
        return rec.record_to_circle(record) if record else None

    def get_circle_by_code(self, code: str) -> Circle | None:
        pointer = self._get(CIRCLE_CODES, code)
        return self.get_circle(pointer["circle_id"]) if pointer else None

    def list_circles(self) -> list[Circle]:
        return [rec.record_to_circle(r) for r in (s.to_dict() for s in self._col(CIRCLES).stream())]

    def update_circle_policy(
        self, circle_id: str, policy: EscalationPolicy, audit: AuditEvent
    ) -> None:
        def work(tx: firestore.Transaction) -> None:
            tx.update(
                self._col(CIRCLES).document(circle_id),
                {
                    "caretaker_after_minutes": policy.caretaker_after_minutes,
                    "family_after_minutes": policy.family_after_minutes,
                    "close_after_minutes": policy.close_after_minutes,
                },
            )
            self._audit_write(tx, audit)

        self._run(work)

    def add_member(self, member: Member, audit: AuditEvent) -> None:
        def work(tx: firestore.Transaction) -> None:
            name_ref = self._col(MEMBER_NAMES).document(
                _name_doc_id(member.circle_id, member.name_key)
            )
            if name_ref.get(transaction=tx).exists:
                raise ValidationFailed(
                    {"display_name": "Someone in this care circle already uses that name."}
                )
            tx.create(name_ref, {"member_id": member.id})
            tx.create(self._col(MEMBERS).document(member.id), rec.member_to_record(member))
            self._audit_write(tx, audit)

        self._run(work)

    def get_member(self, member_id: str) -> Member | None:
        record = self._get(MEMBERS, member_id)
        return rec.record_to_member(record) if record else None

    def find_member_by_name(self, circle_id: str, name_key: str) -> Member | None:
        pointer = self._get(MEMBER_NAMES, _name_doc_id(circle_id, name_key))
        return self.get_member(pointer["member_id"]) if pointer else None

    def list_members(self, circle_id: str) -> list[Member]:
        members = [
            rec.record_to_member(r) for r in self._where(MEMBERS, _eq("circle_id", circle_id))
        ]
        return sorted(members, key=lambda m: m.created_at)

    def record_login_attempt(
        self, member_id: str, failed_attempts: int, locked_until: datetime | None
    ) -> None:
        self._col(MEMBERS).document(member_id).update(
            {"failed_attempts": failed_attempts, "locked_until": rec.ts(locked_until)}
        )

    def deactivate_member(self, member_id: str, audit: AuditEvent) -> None:
        def work(tx: firestore.Transaction) -> None:
            tx.update(self._col(MEMBERS).document(member_id), {"active": False})
            self._audit_write(tx, audit)

        self._run(work)

    # --- Medications, appointments, proposals --------------------------------

    def get_medication(self, medication_id: str) -> Medication | None:
        record = self._get(MEDICATIONS, medication_id)
        return rec.record_to_medication(record) if record else None

    def list_medications(self, circle_id: str) -> list[Medication]:
        meds = [
            rec.record_to_medication(r)
            for r in self._where(MEDICATIONS, _eq("circle_id", circle_id))
        ]
        return sorted(meds, key=lambda m: (m.status.value, m.details.name.casefold()))

    def get_appointment(self, appointment_id: str) -> Appointment | None:
        record = self._get(APPOINTMENTS, appointment_id)
        return rec.record_to_appointment(record) if record else None

    def list_appointments(self, circle_id: str) -> list[Appointment]:
        appts = [
            rec.record_to_appointment(r)
            for r in self._where(APPOINTMENTS, _eq("circle_id", circle_id))
        ]
        return sorted(appts, key=lambda a: a.details.starts_at)

    def list_appointments_starting_between(
        self, start: datetime, end: datetime
    ) -> list[Appointment]:
        rows = self._where(
            APPOINTMENTS,
            _eq("status", "scheduled"),
            FieldFilter("starts_at", ">=", rec.ts(start)),
            FieldFilter("starts_at", "<", rec.ts(end)),
        )
        return sorted(
            (rec.record_to_appointment(r) for r in rows), key=lambda a: a.details.starts_at
        )

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        record = self._get(PROPOSALS, proposal_id)
        return rec.record_to_proposal(record) if record else None

    def list_proposals(
        self, circle_id: str, status: ProposalStatus | None = None
    ) -> list[Proposal]:
        filters = [_eq("circle_id", circle_id)]
        if status is not None:
            filters.append(_eq("status", status.value))
        proposals = [rec.record_to_proposal(r) for r in self._where(PROPOSALS, *filters)]
        return sorted(proposals, key=lambda p: p.created_at, reverse=True)

    def apply_changeset(self, changes: ChangeSet) -> None:
        def work(tx: firestore.Transaction) -> None:
            # Firestore transactions require every read before any write.
            for proposal, expected in changes.proposals:
                if expected is None:
                    continue
                snap = self._col(PROPOSALS).document(proposal.id).get(transaction=tx)
                if not snap.exists or snap.get("status") != expected.value:
                    raise ConcurrencyConflict("This request has already been decided.")
            for collection, items in (
                (MEDICATIONS, changes.medications),
                (APPOINTMENTS, changes.appointments),
            ):
                for entity, expected_version in items:
                    if expected_version is None:
                        continue
                    snap = self._col(collection).document(entity.id).get(transaction=tx)
                    if not snap.exists or snap.get("version") != expected_version:
                        raise ConcurrencyConflict()
            doses_to_cancel = []
            for medication_id, after in changes.cancel_future_doses:
                query = (
                    self._col(DOSES)
                    .where(filter=_eq("medication_id", medication_id))
                    .where(filter=_eq("status", DoseStatus.SCHEDULED.value))
                    .where(filter=FieldFilter("scheduled_for", ">", rec.ts(after)))
                )
                doses_to_cancel.extend(snap.reference for snap in query.get(transaction=tx))
            new_notifications = []
            for notification in changes.notifications:
                ref = self._col(NOTIFICATIONS).document(notification.id)
                if not ref.get(transaction=tx).exists:
                    new_notifications.append((ref, notification))

            for proposal, _ in changes.proposals:
                tx.set(self._col(PROPOSALS).document(proposal.id), rec.proposal_to_record(proposal))
            for medication, _ in changes.medications:
                tx.set(
                    self._col(MEDICATIONS).document(medication.id),
                    rec.medication_to_record(medication),
                )
            for appointment, _ in changes.appointments:
                tx.set(
                    self._col(APPOINTMENTS).document(appointment.id),
                    rec.appointment_to_record(appointment),
                )
            for ref in doses_to_cancel:
                tx.update(ref, {"status": DoseStatus.CANCELLED.value})
            for ref, notification in new_notifications:
                tx.create(ref, rec.notification_to_record(notification))
            for event in changes.audit_events:
                self._audit_write(tx, event)

        self._run(work)

    # --- Doses -----------------------------------------------------------------

    def insert_doses_if_absent(self, doses: list[Dose]) -> int:
        created = 0
        for dose in doses:
            try:
                self._col(DOSES).document(dose.id).create(rec.dose_to_record(dose))
                created += 1
            except AlreadyExists:
                continue
        return created

    def get_dose(self, dose_id: str) -> Dose | None:
        record = self._get(DOSES, dose_id)
        return rec.record_to_dose(record) if record else None

    def list_doses(self, circle_id: str, start: datetime, end: datetime) -> list[Dose]:
        rows = self._where(
            DOSES,
            _eq("circle_id", circle_id),
            FieldFilter("scheduled_for", ">=", rec.ts(start)),
            FieldFilter("scheduled_for", "<", rec.ts(end)),
        )
        return sorted((rec.record_to_dose(r) for r in rows), key=lambda d: d.scheduled_for)

    def list_open_doses_due_by(self, now: datetime) -> list[Dose]:
        rows = self._where(
            DOSES,
            _eq("status", DoseStatus.SCHEDULED.value),
            FieldFilter("scheduled_for", "<=", rec.ts(now)),
        )
        return sorted((rec.record_to_dose(r) for r in rows), key=lambda d: d.scheduled_for)

    def resolve_dose(
        self,
        dose_id: str,
        status: DoseStatus,
        by_member: str,
        by_role: Role,
        at: datetime,
        audit: AuditEvent,
    ) -> bool:
        def work(tx: firestore.Transaction) -> bool:
            ref = self._col(DOSES).document(dose_id)
            snap = ref.get(transaction=tx)
            if not snap.exists or snap.get("status") not in RESOLVABLE:
                return False
            pending = self._col(NOTIFICATIONS).where(filter=_eq("ref_key", dose_id))
            to_cancel = [
                n.reference
                for n in pending.get(transaction=tx)
                if n.get("status")
                in (NotificationStatus.PENDING.value, NotificationStatus.FAILED.value)
            ]
            tx.update(
                ref,
                {
                    "status": status.value,
                    "resolved_at": rec.ts(at),
                    "resolved_by": by_member,
                    "resolved_by_role": by_role.value,
                },
            )
            for n_ref in to_cancel:
                tx.update(n_ref, {"status": NotificationStatus.CANCELLED.value})
            self._audit_write(tx, audit)
            return True

        return bool(self._run(work))

    def advance_escalation(
        self,
        dose_id: str,
        from_level: EscalationLevel,
        to_level: EscalationLevel,
        notifications: list[Notification],
        audit: AuditEvent,
    ) -> bool:
        def work(tx: firestore.Transaction) -> bool:
            ref = self._col(DOSES).document(dose_id)
            snap = ref.get(transaction=tx)
            if (
                not snap.exists
                or snap.get("status") != DoseStatus.SCHEDULED.value
                or snap.get("escalation_level") != int(from_level)
            ):
                return False
            fresh = [
                (self._col(NOTIFICATIONS).document(n.id), n)
                for n in notifications
                if not self._col(NOTIFICATIONS).document(n.id).get(transaction=tx).exists
            ]
            tx.update(ref, {"escalation_level": int(to_level)})
            for n_ref, n in fresh:
                tx.create(n_ref, rec.notification_to_record(n))
            self._audit_write(tx, audit)
            return True

        return bool(self._run(work))

    def close_dose_unconfirmed(
        self, dose_id: str, from_level: EscalationLevel, audit: AuditEvent
    ) -> bool:
        def work(tx: firestore.Transaction) -> bool:
            ref = self._col(DOSES).document(dose_id)
            snap = ref.get(transaction=tx)
            if (
                not snap.exists
                or snap.get("status") != DoseStatus.SCHEDULED.value
                or snap.get("escalation_level") != int(from_level)
            ):
                return False
            tx.update(ref, {"status": DoseStatus.UNCONFIRMED.value})
            self._audit_write(tx, audit)
            return True

        return bool(self._run(work))

    # --- Notifications ---------------------------------------------------------

    def enqueue_notifications(self, notifications: list[Notification]) -> int:
        created = 0
        for notification in notifications:
            try:
                self._col(NOTIFICATIONS).document(notification.id).create(
                    rec.notification_to_record(notification)
                )
                created += 1
            except AlreadyExists:
                continue
        return created

    def list_deliverable_notifications(self, max_attempts: int, limit: int) -> list[Notification]:
        rows = self._where(
            NOTIFICATIONS,
            FieldFilter(
                "status", "in", [NotificationStatus.PENDING.value, NotificationStatus.FAILED.value]
            ),
        )
        items = [
            rec.record_to_notification(r)
            for r in rows
            if r["status"] == NotificationStatus.PENDING.value or int(r["attempts"]) < max_attempts
        ]
        return sorted(items, key=lambda n: n.created_at)[:limit]

    def update_notification_status(
        self,
        notification_id: str,
        status: NotificationStatus,
        attempts: int,
        last_error: str,
        sent_at: datetime | None,
    ) -> None:
        def work(tx: firestore.Transaction) -> None:
            ref = self._col(NOTIFICATIONS).document(notification_id)
            snap = ref.get(transaction=tx)
            if not snap.exists or snap.get("status") == NotificationStatus.CANCELLED.value:
                return
            tx.update(
                ref,
                {
                    "status": status.value,
                    "attempts": attempts,
                    "last_error": last_error[:300],
                    "sent_at": rec.ts(sent_at),
                },
            )

        self._run(work)

    def list_notifications(self, circle_id: str, limit: int = 100) -> list[Notification]:
        query = (
            self._col(NOTIFICATIONS)
            .where(filter=_eq("circle_id", circle_id))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [rec.record_to_notification(s.to_dict()) for s in query.stream()]

    # --- Audit -------------------------------------------------------------------

    def list_audit_events(self, circle_id: str, limit: int = 200) -> list[AuditEvent]:
        query = (
            self._col(AUDIT_EVENTS)
            .where(filter=_eq("circle_id", circle_id))
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [rec.record_to_audit(s.to_dict(), s.id) for s in query.stream()]
