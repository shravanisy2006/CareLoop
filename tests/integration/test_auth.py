"""Passcode sign-in, lockout and removal of access."""

from __future__ import annotations

import pytest

from careloop.domain.errors import AccountLocked, AuthenticationFailed
from careloop.domain.models import Role
from careloop.services.auth_service import MAX_FAILED_ATTEMPTS
from tests.conftest import CARETAKER_PASSCODE, ELDER_PASSCODE


def test_valid_passcode_signs_in_with_correct_role(services, circle):
    member = services.auth.sign_in(
        circle.code, "asha rao", ELDER_PASSCODE
    )  # name is case-insensitive
    assert member.id == circle.elder.id and member.role is Role.ELDER


def test_circle_code_may_be_typed_with_dash_and_lowercase(services, circle):
    pretty = f"{circle.code[:4]}-{circle.code[4:]}".lower()
    assert services.auth.sign_in(pretty, "Ravi", CARETAKER_PASSCODE).role is Role.CARETAKER


@pytest.mark.parametrize(
    ("code", "name", "passcode"),
    [
        ("WRONG", "Asha Rao", ELDER_PASSCODE),  # malformed code
        ("ZZZZZZZZ", "Asha Rao", ELDER_PASSCODE),  # unknown circle
        (None, "Nobody", ELDER_PASSCODE),  # unknown name
        (None, "Asha Rao", "not-the-passcode"),  # wrong passcode
    ],
)
def test_every_failure_gives_the_same_generic_error(services, circle, code, name, passcode):
    with pytest.raises(AuthenticationFailed) as err:
        services.auth.sign_in(code or circle.code, name, passcode)
    assert err.value.message == AuthenticationFailed.default_message


def test_one_persons_passcode_does_not_work_for_another(services, circle):
    with pytest.raises(AuthenticationFailed):
        services.auth.sign_in(circle.code, "Asha Rao", CARETAKER_PASSCODE)


def test_lockout_after_repeated_failures_then_expires(services, circle, clock):
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(AuthenticationFailed):
            services.auth.sign_in(circle.code, "Asha Rao", "wrong-guess")
    with pytest.raises(AccountLocked):
        services.auth.sign_in(circle.code, "Asha Rao", "wrong-guess")
    # Even the right passcode is refused while locked.
    with pytest.raises(AccountLocked):
        services.auth.sign_in(circle.code, "Asha Rao", ELDER_PASSCODE)
    clock.advance(minutes=16)
    assert services.auth.sign_in(circle.code, "Asha Rao", ELDER_PASSCODE).id == circle.elder.id
    assert services.ctx.repo.get_member(circle.elder.id).failed_attempts == 0


def test_removed_member_cannot_sign_in_or_keep_a_session(services, circle):
    services.circles.remove_member(circle.elder, circle.caretaker.id)
    with pytest.raises(AuthenticationFailed):
        services.auth.sign_in(circle.code, "Ravi", CARETAKER_PASSCODE)
    assert services.auth.current_member(circle.caretaker.id, circle.caretaker.circle_id) is None


def test_session_for_a_different_circle_is_rejected(services, circle):
    assert services.auth.current_member(circle.elder.id, "some-other-circle") is None
