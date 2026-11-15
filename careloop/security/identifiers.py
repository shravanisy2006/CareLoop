"""Random identifiers.

Record ids and circle codes are unguessable, but access is never granted by knowing an id: every
request is also checked against the signed-in member's care circle and role.
"""

from __future__ import annotations

import secrets
import uuid

#: No 0/O or 1/I, so codes are easy to read aloud and type.
CIRCLE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CIRCLE_CODE_LENGTH = 8


def new_id() -> str:
    """Return a random 128-bit id as hex."""
    return uuid.uuid4().hex


def new_circle_code() -> str:
    """Return a random 8-character circle code from an unambiguous alphabet."""
    return "".join(secrets.choice(CIRCLE_CODE_ALPHABET) for _ in range(CIRCLE_CODE_LENGTH))


def format_circle_code(code: str) -> str:
    """Show ABCD2345 as ABCD-2345 for readability."""
    return f"{code[:4]}-{code[4:]}"
