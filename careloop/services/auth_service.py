"""Passcode sign-in with lockout.

A person signs in with their care circle code, their name in that circle and
their own passcode. Every failure returns the same message, and an unknown
name costs the same hashing time as a wrong passcode.
"""

from __future__ import annotations

from datetime import timedelta

from careloop.domain.errors import AccountLocked, AuthenticationFailed, ValidationFailed
from careloop.domain.models import Member, normalise_name
from careloop.domain.validation import validate_circle_code
from careloop.security.passcodes import DUMMY_HASH, verify_passcode
from careloop.services.context import ServiceContext

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)


class AuthService:
    def __init__(self, ctx: ServiceContext) -> None:
        self._ctx = ctx

    def sign_in(self, circle_code: str, display_name: str, passcode: str) -> Member:
        repo, now = self._ctx.repo, self._ctx.clock.now()
        try:
            code = validate_circle_code(circle_code)
        except ValidationFailed:
            verify_passcode(passcode, DUMMY_HASH)
            raise AuthenticationFailed() from None

        circle = repo.get_circle_by_code(code)
        member = (
            repo.find_member_by_name(circle.id, normalise_name(display_name)) if circle else None
        )
        if member is None or not member.active:
            verify_passcode(passcode, DUMMY_HASH)
            raise AuthenticationFailed()

        if member.locked_until is not None and member.locked_until > now:
            raise AccountLocked()

        if not verify_passcode(passcode, member.passcode_hash):
            failures = member.failed_attempts + 1
            if failures >= MAX_FAILED_ATTEMPTS:
                repo.record_login_attempt(member.id, 0, now + LOCKOUT_DURATION)
                raise AccountLocked()
            repo.record_login_attempt(member.id, failures, None)
            raise AuthenticationFailed()

        if member.failed_attempts or member.locked_until:
            repo.record_login_attempt(member.id, 0, None)
        return member

    def current_member(self, member_id: str | None, circle_id: str | None) -> Member | None:
        """Re-load the signed-in member on every request, so removing someone
        from the circle takes effect immediately."""
        if not member_id or not circle_id:
            return None
        member = self._ctx.repo.get_member(member_id)
        if member is None or not member.active or member.circle_id != circle_id:
            return None
        return member
