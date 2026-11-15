"""Domain exceptions. The web layer maps each to an HTTP status and a plain,
non-technical message; none of them carry health data or secrets."""

from __future__ import annotations


class CareLoopError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400
    default_message = "Something went wrong. Please try again."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message


class ValidationFailed(CareLoopError):
    """Input did not pass validation. `field_errors` maps field name to message."""

    status_code = 422
    default_message = "Please check the highlighted fields."

    def __init__(self, field_errors: dict[str, str], message: str | None = None) -> None:
        super().__init__(message)
        self.field_errors = field_errors


class AuthenticationFailed(CareLoopError):
    status_code = 401
    default_message = "Those details did not match. Please check and try again."


class AccountLocked(CareLoopError):
    status_code = 429
    default_message = "Too many wrong attempts. Please wait 15 minutes and try again."


class PermissionDenied(CareLoopError):
    status_code = 403
    default_message = "You do not have permission to do that."


class NotFound(CareLoopError):
    status_code = 404
    default_message = "We could not find that."


class AlreadyResolved(CareLoopError):
    status_code = 409
    default_message = "This dose has already been recorded."


class ConcurrencyConflict(CareLoopError):
    status_code = 409
    default_message = (
        "This record was changed by someone else in the meantime. Please review it again."
    )


class InvalidStateTransition(CareLoopError):
    status_code = 409
    default_message = "That action is no longer possible."
