"""Role permissions and passcode hashing."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from careloop.domain.errors import PermissionDenied
from careloop.domain.models import Member, Role
from careloop.domain.permissions import Action, authorize, can
from careloop.security.passcodes import hash_passcode, verify_passcode

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def member(role: Role, circle_id="c1", active=True) -> Member:
    return Member("m-" + role.value, circle_id, role.value, role, None, "h", 0, None, active, NOW)


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        (Role.ELDER, Action.DECIDE_PROPOSAL, True),
        (Role.ELDER, Action.EDIT_RECORDS_DIRECTLY, True),
        (Role.ELDER, Action.MANAGE_MEMBERS, True),
        (Role.ELDER, Action.CREATE_PROPOSAL, False),
        (Role.CARETAKER, Action.DECIDE_PROPOSAL, False),
        (Role.CARETAKER, Action.EDIT_RECORDS_DIRECTLY, False),
        (Role.CARETAKER, Action.RECORD_DOSE_ON_BEHALF, True),
        (Role.CARETAKER, Action.CREATE_PROPOSAL, True),
        (Role.FAMILY, Action.RECORD_DOSE_ON_BEHALF, False),
        (Role.FAMILY, Action.CHANGE_SETTINGS, False),
        (Role.FAMILY, Action.CREATE_PROPOSAL, True),
        (Role.FAMILY, Action.VIEW_AUDIT, True),
    ],
)
def test_permission_matrix(role, action, allowed):
    assert can(member(role), action) is allowed


def test_members_of_another_circle_are_always_denied():
    with pytest.raises(PermissionDenied):
        authorize(member(Role.ELDER, circle_id="c1"), Action.VIEW_CIRCLE, "c2")


def test_removed_members_lose_every_permission():
    removed = member(Role.CARETAKER, active=False)
    assert not any(can(removed, action) for action in Action)


def test_passcode_hash_is_salted_and_verifiable():
    first, second = hash_passcode("river-blue-42"), hash_passcode("river-blue-42")
    assert first != second  # unique salt each time
    assert "river-blue-42" not in first
    assert verify_passcode("river-blue-42", first)
    assert not verify_passcode("river-blue-43", first)


@pytest.mark.parametrize("bad_hash", ["", "plain", "bcrypt$1$2$3$4$5", "scrypt$x$y$z$!!$!!"])
def test_malformed_hashes_never_verify(bad_hash):
    assert verify_passcode("anything", bad_hash) is False
