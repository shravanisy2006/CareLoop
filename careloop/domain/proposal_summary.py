"""Plain-language descriptions of suggested changes.

The person deciding on a change is often older and not medically trained, so
every request is also described in one or two everyday sentences, including
who advised it. The same text is used on the request page and in the email.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from careloop.domain.models import (
    Advice,
    AdviceSource,
    Appointment,
    AppointmentDetails,
    Medication,
    MedicationDetails,
    Proposal,
    ProposalKind,
)


def clock_time(value: str) -> str:
    """ "08:00" -> "8:00 AM"."""
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute).strftime("%I:%M %p").lstrip("0")


def join_words(items: list[str]) -> str:
    """["a"] -> "a"; ["a", "b"] -> "a and b"; ["a", "b", "c"] -> "a, b and c"."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def day_label(value: date) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


def when_label(moment: datetime, tz_name: str) -> str:
    local = moment.astimezone(ZoneInfo(tz_name))
    return f"{local.day} {local.strftime('%b %Y')} at {local.strftime('%I:%M %p').lstrip('0')}"


def _times(details: MedicationDetails) -> str:
    return join_words([clock_time(t) for t in details.times])


def _medication_changes(old: MedicationDetails, new: MedicationDetails) -> list[str]:
    changes = []
    if old.name != new.name:
        changes.append(f'the name from "{old.name}" to "{new.name}"')
    if old.dosage != new.dosage:
        changes.append(f'the dose from "{old.dosage}" to "{new.dosage}"')
    if old.times != new.times:
        changes.append(f"the time from {_times(old)} to {_times(new)}")
    if old.instructions != new.instructions:
        changes.append(f'the instructions to "{new.instructions or "none"}"')
    if old.start_date != new.start_date:
        changes.append(f"the start date to {day_label(new.start_date)}")
    if old.end_date != new.end_date:
        end = day_label(new.end_date) if new.end_date else "no end date"
        changes.append(f"the end date to {end}")
    return changes


def _appointment_changes(old: AppointmentDetails, new: AppointmentDetails, tz: str) -> list[str]:
    changes = []
    if old.starts_at != new.starts_at:
        changes.append(f"the time to {when_label(new.starts_at, tz)}")
    if old.doctor_name != new.doctor_name:
        changes.append(f"the doctor to {new.doctor_name}")
    if old.purpose != new.purpose:
        changes.append(f'the reason to "{new.purpose}"')
    if old.location != new.location:
        changes.append(f"the place to {new.location or 'not given'}")
    if old.notes != new.notes:
        changes.append("the notes")
    return changes


def describe_change(
    proposal: Proposal,
    current: Medication | Appointment | None,
    proposer_name: str,
    tz_name: str,
) -> str:
    """One sentence saying what would change if the request is approved."""
    kind, who = proposal.kind, proposer_name
    med, appt = proposal.medication_details, proposal.appointment_details

    if kind is ProposalKind.ADD_MEDICATION and med:
        until = f" until {day_label(med.end_date)}" if med.end_date else ""
        return (
            f"{who} suggests adding {med.name}: {med.dosage} at {_times(med)}, "
            f"starting {day_label(med.start_date)}{until}."
        )
    if kind is ProposalKind.CHANGE_MEDICATION and med and isinstance(current, Medication):
        changes = _medication_changes(current.details, med)
        if not changes:
            return (
                f"{who} suggests changing {current.details.name}, but nothing would be different."
            )
        return f"{who} suggests changing {current.details.name}: {join_words(changes)}."
    if kind is ProposalKind.STOP_MEDICATION and isinstance(current, Medication):
        med_now = current.details
        return f"{who} suggests stopping {med_now.name} ({med_now.dosage} at {_times(med_now)})."
    if kind is ProposalKind.ADD_APPOINTMENT and appt:
        return (
            f"{who} suggests adding an appointment with {appt.doctor_name} on "
            f"{when_label(appt.starts_at, tz_name)} for {appt.purpose}."
        )
    if kind is ProposalKind.CHANGE_APPOINTMENT and appt and isinstance(current, Appointment):
        changes = _appointment_changes(current.details, appt, tz_name)
        what = join_words(changes) if changes else "nothing would be different"
        return (
            f"{who} suggests changing the appointment with {current.details.doctor_name}: {what}."
        )
    if kind is ProposalKind.CANCEL_APPOINTMENT and isinstance(current, Appointment):
        visit = current.details
        return (
            f"{who} suggests cancelling the appointment with {visit.doctor_name} on "
            f"{when_label(visit.starts_at, tz_name)}."
        )
    return f"{who} suggests a change."


def describe_advice(advice: Advice, proposer_name: str) -> str:
    """Who advised the change, said plainly - including when nobody medical did."""
    on = f" on {day_label(advice.advised_on)}" if advice.advised_on else ""
    if advice.source is AdviceSource.DOCTOR:
        name = advice.advised_by
        name = name if name.lower().startswith(("dr", "doctor")) else f"doctor {name}"
        return f"Advised by {name}{on}."
    if advice.source is AdviceSource.PHARMACIST:
        return f"Advised by pharmacist {advice.advised_by}{on}."
    return f"This is {proposer_name}'s own suggestion, not a doctor's instruction."


def summarise_proposal(
    proposal: Proposal,
    current: Medication | Appointment | None,
    proposer_name: str,
    tz_name: str,
) -> str:
    return (
        f"{describe_change(proposal, current, proposer_name, tz_name)} "
        f"{describe_advice(proposal.advice, proposer_name)}"
    )
