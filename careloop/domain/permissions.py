"""Role-based permissions, enforced on the server for every request.

The elder keeps control: only the elder can change records directly, decide on
proposals, manage who is in the circle, and change escalation settings.
Caretakers and family can see everything and propose changes.
"""

from __future__ import annotations

from enum import Enum

from careloop.domain.errors import PermissionDenied
from careloop.domain.models import Member, Role


class Action(str, Enum):
    VIEW_CIRCLE = "view_circle"
    CONFIRM_OWN_DOSE = "confirm_own_dose"
    RECORD_DOSE_ON_BEHALF = "record_dose_on_behalf"
    EDIT_RECORDS_DIRECTLY = "edit_records_directly"
    CREATE_PROPOSAL = "create_proposal"
    DECIDE_PROPOSAL = "decide_proposal"
    MANAGE_MEMBERS = "manage_members"
    CHANGE_SETTINGS = "change_settings"
    VIEW_AUDIT = "view_audit"


PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.ELDER: frozenset(
        {
            Action.VIEW_CIRCLE,
            Action.CONFIRM_OWN_DOSE,
            Action.EDIT_RECORDS_DIRECTLY,
            Action.DECIDE_PROPOSAL,
            Action.MANAGE_MEMBERS,
            Action.CHANGE_SETTINGS,
            Action.VIEW_AUDIT,
        }
    ),
    Role.CARETAKER: frozenset(
        {
            Action.VIEW_CIRCLE,
            Action.RECORD_DOSE_ON_BEHALF,
            Action.CREATE_PROPOSAL,
            Action.VIEW_AUDIT,
        }
    ),
    Role.FAMILY: frozenset(
        {
            Action.VIEW_CIRCLE,
            Action.CREATE_PROPOSAL,
            Action.VIEW_AUDIT,
        }
    ),
}


def can(member: Member, action: Action) -> bool:
    return member.active and action in PERMISSIONS[member.role]


def authorize(member: Member, action: Action, circle_id: str) -> None:
    """Raise PermissionDenied unless `member` may perform `action` on the
    care circle `circle_id`. Membership of a different circle is always
    denied, which is what keeps one elder's records away from another's."""
    if member.circle_id != circle_id or not can(member, action):
        raise PermissionDenied()
