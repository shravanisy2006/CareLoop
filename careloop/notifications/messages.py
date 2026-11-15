"""Builds the notification records (and their email text) for each event.

Emails say only what the recipient needs to act. They never contain
passcodes, and they say plainly that an unconfirmed dose is not proof that
it was missed.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from careloop.domain.escalation import notification_kind_for_level
from careloop.domain.models import (
    Appointment,
    Circle,
    Dose,
    EscalationLevel,
    Medication,
    Member,
    Notification,
    NotificationKind,
    NotificationStatus,
    Proposal,
    Role,
)
from careloop.integrations.google_calendar import add_to_calendar_url

KIND_LABELS = {
    NotificationKind.DOSE_REMINDER: "Dose reminder",
    NotificationKind.DOSE_ESCALATION_CARETAKER: "Unconfirmed dose (caretaker)",
    NotificationKind.DOSE_ESCALATION_FAMILY: "Unconfirmed dose (family)",
    NotificationKind.APPOINTMENT_REMINDER: "Appointment reminder",
    NotificationKind.PROPOSAL_PENDING: "Change waiting for approval",
}


def notification_id(kind: NotificationKind, ref_key: str, recipient_id: str) -> str:
    """Same inputs always give the same id, which is what prevents duplicates."""
    return hashlib.sha256(f"{kind.value}|{ref_key}|{recipient_id}".encode()).hexdigest()[:40]


def local_time_label(moment: datetime, tz_name: str) -> str:
    return moment.astimezone(ZoneInfo(tz_name)).strftime("%a %d %b, %I:%M %p")


def _build(
    kind: NotificationKind,
    ref_key: str,
    circle: Circle,
    recipient: Member,
    subject: str,
    body: str,
    now: datetime,
) -> Notification:
    return Notification(
        id=notification_id(kind, ref_key, recipient.id),
        circle_id=circle.id,
        kind=kind,
        ref_key=ref_key,
        recipient_member_id=recipient.id,
        recipient_email=recipient.email,
        subject=subject,
        body=body,
        status=NotificationStatus.PENDING if recipient.email else NotificationStatus.NO_EMAIL,
        attempts=0,
        last_error="",
        created_at=now,
    )


def dose_notifications(
    circle: Circle,
    dose: Dose,
    medication: Medication,
    level: EscalationLevel,
    recipients: list[Member],
    public_url: str,
    now: datetime,
) -> list[Notification]:
    kind = notification_kind_for_level(level)
    when = local_time_label(dose.scheduled_for, circle.timezone)
    name = medication.details.name
    result = []
    for person in recipients:
        if level is EscalationLevel.ELDER_REMINDED:
            subject = f"Time for your medicine: {name}"
            body = (
                f"Hello {person.display_name},\n\n"
                f"It is time for {name} ({medication.details.dosage}), scheduled for {when}.\n"
                f'When you have taken it, please press "I took it" in CareLoop:\n'
                f"{public_url}/\n\n"
                "If you don't answer, CareLoop will let your caretaker know."
            )
        else:
            subject = f"{circle.elder_name}'s {when} dose has not been confirmed"
            body = (
                f"Hello {person.display_name},\n\n"
                f"Nobody has confirmed {circle.elder_name}'s dose of {name} scheduled for {when}.\n"
                "This does not mean it was missed - only that no answer has been recorded.\n"
                f"Please check in, then record what happened in CareLoop:\n{public_url}/\n"
            )
        result.append(_build(kind, dose.id, circle, person, subject, body, now))
    return result


def appointment_notifications(
    circle: Circle, appointment: Appointment, members: list[Member], public_url: str, now: datetime
) -> list[Notification]:
    d = appointment.details
    when = local_time_label(d.starts_at, circle.timezone)
    # Including the start time in the key means a rescheduled visit gets a new reminder.
    ref_key = f"{appointment.id}@{d.starts_at.isoformat()}"
    calendar_url = add_to_calendar_url(appointment, circle.elder_name)
    result = []
    for person in members:
        if not person.active:
            continue
        subject = f"Reminder: {circle.elder_name}'s appointment with {d.doctor_name}, {when}"
        body = (
            f"Hello {person.display_name},\n\n"
            f"{circle.elder_name} has an appointment coming up.\n"
            f"Doctor: {d.doctor_name}\nFor: {d.purpose}\nWhen: {when}\n"
            f"Where: {d.location or 'not given'}\n\n"
            f"Add it to Google Calendar: {calendar_url}\n"
            f"Details in CareLoop: {public_url}/appointments\n"
        )
        result.append(
            _build(
                NotificationKind.APPOINTMENT_REMINDER, ref_key, circle, person, subject, body, now
            )
        )
    return result


def proposal_notifications(
    circle: Circle,
    proposal: Proposal,
    proposer: Member,
    deciders: list[Member],
    summary: str,
    public_url: str,
    now: datetime,
) -> list[Notification]:
    """Tell the elder (and the elder's trusted decision-maker, if any) that a
    change is waiting. `summary` is the plain-language description."""
    result = []
    for person in deciders:
        if person.role is Role.ELDER:
            subject = f"{proposer.display_name} has suggested a change for you to review"
            why = "Nothing changes unless you approve it."
        else:
            subject = f"A change for {circle.elder_name} needs a decision"
            why = (
                f"{circle.elder_name} has trusted you to decide on their behalf. "
                "Nothing changes unless you or they approve it."
            )
        body = (
            f"Hello {person.display_name},\n\n{summary}\n\n{why}\n"
            f"Review it here: {public_url}/proposals/{proposal.id}\n"
        )
        result.append(
            _build(
                NotificationKind.PROPOSAL_PENDING, proposal.id, circle, person, subject, body, now
            )
        )
    return result
