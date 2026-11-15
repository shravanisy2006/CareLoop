"""Passcode hashing with scrypt (Python standard library).

Stored format: ``scrypt$<n>$<r>$<p>$<salt b64>$<hash b64>``. Each passcode
gets its own random salt. Verification uses a constant-time comparison.
Passcodes themselves are never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
KEY_LENGTH = 32
SALT_BYTES = 16


def _derive(passcode: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        passcode.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=KEY_LENGTH,
        maxmem=64 * 1024 * 1024,
    )


def hash_passcode(passcode: str) -> str:
    """Hash a passcode with scrypt and a fresh random salt."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(passcode, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_passcode(passcode: str, stored_hash: str) -> bool:
    """Check a passcode against a stored hash in constant time."""
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = _derive(passcode, salt, int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


#: Verified against when a login names an unknown person, so that a wrong name
#: and a wrong passcode take about the same time to reject.
DUMMY_HASH = hash_passcode(secrets.token_urlsafe(16))
