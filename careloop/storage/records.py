"""Conversion between domain objects and flat storage records.

Both backends store the same flat dictionaries (column names in SQLite, field
names in Firestore), so this mapping is written once. Timestamps are stored as
UTC ISO-8601 strings with microseconds, which sort correctly as text.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from careloop.domain.models import (
    Advice,
    AdviceSource,
    Appointment,
    AppointmentDetails,
    AppointmentStatus,
    AuditEvent,
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
    Proposal,
    ProposalKind,
    ProposalStatus,
    Role,
)

Record = dict[str, Any]


def ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Naive datetimes are not stored")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _req_ts(value: str) -> datetime:
    parsed = parse_ts(value)
    assert parsed is not None
    return parsed


# --- Circle ----------------------------------------------------------------


def circle_to_record(circle: Circle) -> Record:
    return {
        "id": circle.id,
        "code": circle.code,
        "elder_name": circle.elder_name,
        "timezone": circle.timezone,
        "caretaker_after_minutes": circle.policy.caretaker_after_minutes,
        "family_after_minutes": circle.policy.family_after_minutes,
        "close_after_minutes": circle.policy.close_after_minutes,
        "created_at": ts(circle.created_at),
        "decision_maker_id": circle.decision_maker_id,
    }


def record_to_circle(r: Record) -> Circle:
    return Circle(
        id=r["id"],
        code=r["code"],
        elder_name=r["elder_name"],
        timezone=r["timezone"],
        policy=EscalationPolicy(
            caretaker_after_minutes=int(r["caretaker_after_minutes"]),
            family_after_minutes=int(r["family_after_minutes"]),
            close_after_minutes=int(r["close_after_minutes"]),
        ),
        created_at=_req_ts(r["created_at"]),
        decision_maker_id=r.get("decision_maker_id"),
    )


# --- Member ----------------------------------------------------------------


def member_to_record(m: Member) -> Record:
    return {
        "id": m.id,
        "circle_id": m.circle_id,
        "display_name": m.display_name,
        "name_key": m.name_key,
        "role": m.role.value,
        "email": m.email,
        "passcode_hash": m.passcode_hash,
        "failed_attempts": m.failed_attempts,
        "locked_until": ts(m.locked_until),
        "active": bool(m.active),
        "created_at": ts(m.created_at),
    }


def record_to_member(r: Record) -> Member:
    return Member(
        id=r["id"],
        circle_id=r["circle_id"],
        display_name=r["display_name"],
        role=Role(r["role"]),
        email=r["email"],
        passcode_hash=r["passcode_hash"],
        failed_attempts=int(r["failed_attempts"]),
        locked_until=parse_ts(r["locked_until"]),
        active=bool(r["active"]),
        created_at=_req_ts(r["created_at"]),
    )


# --- Medication ------------------------------------------------------------


def medication_to_record(m: Medication) -> Record:
    d = m.details
    return {
        "id": m.id,
        "circle_id": m.circle_id,
        "name": d.name,
        "dosage": d.dosage,
        "instructions": d.instructions,
        "times_json": json.dumps(list(d.times)),
        "start_date": d.start_date.isoformat(),
        "end_date": d.end_date.isoformat() if d.end_date else None,
        "status": m.status.value,
        "version": m.version,
        "created_at": ts(m.created_at),
        "updated_at": ts(m.updated_at),
    }


def record_to_medication(r: Record) -> Medication:
    details = MedicationDetails.from_dict(
        {
            "name": r["name"],
            "dosage": r["dosage"],
            "instructions": r["instructions"],
            "times": json.loads(r["times_json"]),
            "start_date": r["start_date"],
            "end_date": r["end_date"],
        }
    )
    return Medication(
        id=r["id"],
        circle_id=r["circle_id"],
        details=details,
        status=MedicationStatus(r["status"]),
        version=int(r["version"]),
        created_at=_req_ts(r["created_at"]),
        updated_at=_req_ts(r["updated_at"]),
    )


# --- Appointment -----------------------------------------------------------


def appointment_to_record(a: Appointment) -> Record:
    d = a.details
    return {
        "id": a.id,
        "circle_id": a.circle_id,
        "doctor_name": d.doctor_name,
        "purpose": d.purpose,
        "location": d.location,
        "starts_at": ts(d.starts_at),
        "notes": d.notes,
        "status": a.status.value,
        "version": a.version,
        "created_at": ts(a.created_at),
        "updated_at": ts(a.updated_at),
    }


def record_to_appointment(r: Record) -> Appointment:
    return Appointment(
        id=r["id"],
        circle_id=r["circle_id"],
        details=AppointmentDetails(
            doctor_name=r["doctor_name"],
            purpose=r["purpose"],
            location=r["location"],
            starts_at=_req_ts(r["starts_at"]),
            notes=r["notes"],
        ),
        status=AppointmentStatus(r["status"]),
        version=int(r["version"]),
        created_at=_req_ts(r["created_at"]),
        updated_at=_req_ts(r["updated_at"]),
    )


# --- Dose ------------------------------------------------------------------


def dose_to_record(d: Dose) -> Record:
    return {
        "id": d.id,
        "circle_id": d.circle_id,
        "medication_id": d.medication_id,
        "scheduled_for": ts(d.scheduled_for),
        "status": d.status.value,
        "escalation_level": int(d.escalation_level),
        "resolved_at": ts(d.resolved_at),
        "resolved_by": d.resolved_by,
        "resolved_by_role": d.resolved_by_role.value if d.resolved_by_role else None,
        "created_at": ts(d.created_at),
    }


def record_to_dose(r: Record) -> Dose:
    return Dose(
        id=r["id"],
        circle_id=r["circle_id"],
        medication_id=r["medication_id"],
        scheduled_for=_req_ts(r["scheduled_for"]),
        status=DoseStatus(r["status"]),
        escalation_level=EscalationLevel(int(r["escalation_level"])),
        resolved_at=parse_ts(r["resolved_at"]),
        resolved_by=r["resolved_by"],
        resolved_by_role=Role(r["resolved_by_role"]) if r["resolved_by_role"] else None,
        created_at=_req_ts(r["created_at"]),
    )


# --- Proposal --------------------------------------------------------------


def proposal_to_record(p: Proposal) -> Record:
    return {
        "id": p.id,
        "circle_id": p.circle_id,
        "kind": p.kind.value,
        "target_id": p.target_id,
        "base_version": p.base_version,
        "payload_json": json.dumps(p.payload_dict()),
        "reason": p.reason,
        "status": p.status.value,
        "proposed_by": p.proposed_by,
        "proposed_by_role": p.proposed_by_role.value,
        "created_at": ts(p.created_at),
        "decided_by": p.decided_by,
        "decided_at": ts(p.decided_at),
        "decision_note": p.decision_note,
        "advice_source": p.advice.source.value,
        "advised_by": p.advice.advised_by,
        "advised_on": p.advice.advised_on.isoformat() if p.advice.advised_on else None,
    }


def record_to_proposal(r: Record) -> Proposal:
    kind = ProposalKind(r["kind"])
    payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
    med = MedicationDetails.from_dict(payload) if payload and "dosage" in payload else None
    appt = AppointmentDetails.from_dict(payload) if payload and "doctor_name" in payload else None
    return Proposal(
        id=r["id"],
        circle_id=r["circle_id"],
        kind=kind,
        target_id=r["target_id"],
        base_version=int(r["base_version"]) if r["base_version"] is not None else None,
        medication_details=med,
        appointment_details=appt,
        reason=r["reason"],
        status=ProposalStatus(r["status"]),
        proposed_by=r["proposed_by"],
        proposed_by_role=Role(r["proposed_by_role"]),
        created_at=_req_ts(r["created_at"]),
        decided_by=r["decided_by"],
        decided_at=parse_ts(r["decided_at"]),
        decision_note=r["decision_note"] or "",
        advice=Advice(
            source=AdviceSource(r.get("advice_source") or AdviceSource.CARE_TEAM.value),
            advised_by=r.get("advised_by") or "",
            advised_on=date.fromisoformat(r["advised_on"]) if r.get("advised_on") else None,
        ),
    )


# --- Notification ----------------------------------------------------------


def notification_to_record(n: Notification) -> Record:
    return {
        "id": n.id,
        "circle_id": n.circle_id,
        "kind": n.kind.value,
        "ref_key": n.ref_key,
        "recipient_member_id": n.recipient_member_id,
        "recipient_email": n.recipient_email,
        "subject": n.subject,
        "body": n.body,
        "status": n.status.value,
        "attempts": n.attempts,
        "last_error": n.last_error,
        "created_at": ts(n.created_at),
        "sent_at": ts(n.sent_at),
    }


def record_to_notification(r: Record) -> Notification:
    return Notification(
        id=r["id"],
        circle_id=r["circle_id"],
        kind=NotificationKind(r["kind"]),
        ref_key=r["ref_key"],
        recipient_member_id=r["recipient_member_id"],
        recipient_email=r["recipient_email"],
        subject=r["subject"],
        body=r["body"],
        status=NotificationStatus(r["status"]),
        attempts=int(r["attempts"]),
        last_error=r["last_error"] or "",
        created_at=_req_ts(r["created_at"]),
        sent_at=parse_ts(r["sent_at"]),
    )


# --- Audit -----------------------------------------------------------------


def audit_to_record(e: AuditEvent) -> Record:
    return {
        "circle_id": e.circle_id,
        "actor_member_id": e.actor_member_id,
        "actor_role": e.actor_role,
        "action": e.action,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "before_json": json.dumps(e.before) if e.before is not None else None,
        "after_json": json.dumps(e.after) if e.after is not None else None,
        "created_at": ts(e.created_at),
    }


def record_to_audit(r: Record, event_id: str) -> AuditEvent:
    return AuditEvent(
        id=event_id,
        circle_id=r["circle_id"],
        actor_member_id=r["actor_member_id"],
        actor_role=r["actor_role"],
        action=r["action"],
        entity_type=r["entity_type"],
        entity_id=r["entity_id"],
        before=json.loads(r["before_json"]) if r["before_json"] else None,
        after=json.loads(r["after_json"]) if r["after_json"] else None,
        created_at=_req_ts(r["created_at"]),
    )
