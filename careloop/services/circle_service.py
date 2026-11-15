"""Creating a care circle and managing who belongs to it.

Only the elder can add or remove people and change the escalation timings.
"""

from __future__ import annotations

from careloop.domain.changes import audit_event
from careloop.domain.errors import InvalidStateTransition, NotFound, ValidationFailed
from careloop.domain.models import Circle, EscalationPolicy, Member, Role
from careloop.domain.permissions import Action, authorize
from careloop.domain.validation import (
    validate_all,
    validate_helper_role,
    validate_matching_passcodes,
    validate_person,
    validate_policy_input,
    validate_timezone,
)
from careloop.security.identifiers import new_circle_code
from careloop.security.passcodes import hash_passcode
from careloop.services.context import ServiceContext

MAX_MEMBERS_PER_CIRCLE = 12


class CircleService:
    """Creates care circles and manages their members and settings."""

    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    def create_circle(
        self,
        *,
        elder_name: str,
        passcode: str,
        confirm_passcode: str,
        email: str,
        timezone: str,
        circle_code: str | None = None,
    ) -> tuple[Circle, Member]:
        """Start a care circle with the elder as its first member.

        `circle_code` is only given for the public demo circle; real circles get a random one.
        """
        elder_input, _, _ = validate_all(
            lambda: validate_person(elder_name, email, passcode),
            lambda: validate_timezone(timezone),
            lambda: validate_matching_passcodes(passcode, confirm_passcode),
        )

        now = self._ctx.clock.now()
        circle = Circle(
            id=self._ctx.id_factory(),
            code=circle_code or self._unused_code(),
            elder_name=elder_input.display_name,
            timezone=timezone,
            policy=EscalationPolicy(),
            created_at=now,
        )
        elder = Member(
            id=self._ctx.id_factory(),
            circle_id=circle.id,
            display_name=circle.elder_name,
            role=Role.ELDER,
            email=elder_input.email,
            passcode_hash=hash_passcode(elder_input.passcode),
            failed_attempts=0,
            locked_until=None,
            active=True,
            created_at=now,
        )
        audit = audit_event(
            elder,
            "circle.created",
            "circle",
            circle.id,
            now,
            after={"elder_name": circle.elder_name, "timezone": timezone},
        )
        self._ctx.repo.create_circle(circle, elder, audit)
        return circle, elder

    def _unused_code(self) -> str:
        for _ in range(10):
            code = new_circle_code()
            if self._ctx.repo.get_circle_by_code(code) is None:
                return code
        raise RuntimeError("Could not generate a unique circle code")

    def get_circle(self, viewer: Member) -> Circle:
        """Return the viewer's own care circle."""
        authorize(viewer, Action.VIEW_CIRCLE, viewer.circle_id)
        circle = self._ctx.repo.get_circle(viewer.circle_id)
        if circle is None:
            raise NotFound()
        return circle

    def list_members(self, viewer: Member) -> list[Member]:
        """Return everyone in the viewer's circle, including removed members."""
        authorize(viewer, Action.VIEW_CIRCLE, viewer.circle_id)
        return self._ctx.repo.list_members(viewer.circle_id)

    def add_member(
        self, elder: Member, *, display_name: str, role: str, email: str, passcode: str
    ) -> Member:
        """Add a caretaker or family member (elder only)."""
        authorize(elder, Action.MANAGE_MEMBERS, elder.circle_id)
        chosen_role, person = validate_all(
            lambda: validate_helper_role(role),
            lambda: validate_person(display_name, email, passcode),
        )
        active = [m for m in self._ctx.repo.list_members(elder.circle_id) if m.active]
        if len(active) >= MAX_MEMBERS_PER_CIRCLE:
            raise ValidationFailed({"display_name": "This care circle is full (12 people)."})

        now = self._ctx.clock.now()
        member = Member(
            id=self._ctx.id_factory(),
            circle_id=elder.circle_id,
            display_name=person.display_name,
            role=chosen_role,
            email=person.email,
            passcode_hash=hash_passcode(person.passcode),
            failed_attempts=0,
            locked_until=None,
            active=True,
            created_at=now,
        )
        audit = audit_event(
            elder,
            "member.added",
            "member",
            member.id,
            now,
            after={"display_name": member.display_name, "role": member.role.value},
        )
        self._ctx.repo.add_member(member, audit)
        return member

    def remove_member(self, elder: Member, member_id: str) -> None:
        """Remove a helper's access (elder only); their history is kept."""
        authorize(elder, Action.MANAGE_MEMBERS, elder.circle_id)
        member = self._ctx.repo.get_member(member_id)
        if member is None or member.circle_id != elder.circle_id:
            raise NotFound()
        if member.role is Role.ELDER:
            raise InvalidStateTransition("The elder cannot be removed from their own circle.")
        if not member.active:
            return
        now = self._ctx.clock.now()
        audit = audit_event(
            elder,
            "member.removed",
            "member",
            member.id,
            now,
            before={"display_name": member.display_name, "role": member.role.value},
        )
        self._ctx.repo.deactivate_member(member.id, audit)
        if self.get_circle(elder).decision_maker_id == member.id:
            self.set_decision_maker(elder, None)

    def set_decision_maker(self, elder: Member, member_id: str | None) -> Circle:
        """Let the elder trust one helper to decide on proposals on their behalf.

        Passing None takes that trust back.
        """
        authorize(elder, Action.MANAGE_MEMBERS, elder.circle_id)
        circle = self.get_circle(elder)
        chosen: Member | None = None
        if member_id is not None:
            chosen = self._ctx.repo.get_member(member_id)
            if chosen is None or chosen.circle_id != elder.circle_id or not chosen.active:
                raise NotFound()
            if chosen.role is Role.ELDER:
                raise InvalidStateTransition("You already decide for yourself.")
        if circle.decision_maker_id == member_id:
            return circle
        names = {m.id: m.display_name for m in self._ctx.repo.list_members(circle.id)}
        audit = audit_event(
            elder,
            "settings.decision_maker_changed",
            "circle",
            circle.id,
            self._ctx.clock.now(),
            before={"decision_maker": names.get(circle.decision_maker_id or "")},
            after={"decision_maker": chosen.display_name if chosen else None},
        )
        self._ctx.repo.update_decision_maker(circle.id, member_id, audit)
        return self.get_circle(elder)

    def update_policy(self, elder: Member, *, caretaker: str, family: str, close: str) -> Circle:
        """Change the escalation timings (elder only)."""
        authorize(elder, Action.CHANGE_SETTINGS, elder.circle_id)
        policy = validate_policy_input(caretaker, family, close)
        circle = self.get_circle(elder)
        now = self._ctx.clock.now()
        audit = audit_event(
            elder,
            "settings.escalation_changed",
            "circle",
            circle.id,
            now,
            before=vars(circle.policy),
            after=vars(policy),
        )
        self._ctx.repo.update_circle_policy(circle.id, policy, audit)
        return self.get_circle(elder)
