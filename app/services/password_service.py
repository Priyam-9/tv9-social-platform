"""
Password hashing utilities for the TV9 Publisher.

Uses Python's stdlib scrypt implementation, so no third-party password
hashing dependency is required.

Stored format:
    scrypt$N=32768,r=8,p=1$<base64-salt>$<base64-hash>

Only the resulting encoded hash should be stored in the database.
Plaintext passwords must never be stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


_SCRYPT_N = 32_768
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024  # 64 MiB
_SALT_BYTES = 16
_DK_BYTES = 32

_PASSWORD_PREFIX = "scrypt"
_MIN_PASSWORD_LENGTH = 12


def _b64_encode(value: bytes) -> str:
    """Encode bytes using URL-safe, unpadded base64."""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    """Decode the URL-safe, unpadded base64 representation."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    """
    Create a salted scrypt password hash suitable for database storage.

    Raises:
        ValueError: if the password is shorter than the minimum policy.
        TypeError: if password is not a string.
    """
    if not isinstance(password, str):
        raise TypeError("Password must be a string.")

    if len(password) < _MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {_MIN_PASSWORD_LENGTH} characters long."
        )

    salt = secrets.token_bytes(_SALT_BYTES)

    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_DK_BYTES,
    )

    return (
        f"{_PASSWORD_PREFIX}$"
        f"N={_SCRYPT_N},r={_SCRYPT_R},p={_SCRYPT_P}$"
        f"{_b64_encode(salt)}$"
        f"{_b64_encode(derived_key)}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """
    Verify a plaintext password against a previously generated hash.

    Returns False for malformed hashes rather than exposing parsing details
    to the authentication layer.
    """
    if not isinstance(password, str) or not isinstance(stored_hash, str):
        return False

    try:
        prefix, parameters, encoded_salt, encoded_key = stored_hash.split("$")

        if prefix != _PASSWORD_PREFIX:
            return False

        parameter_map: dict[str, int] = {}

        for item in parameters.split(","):
            key, value = item.split("=", 1)
            parameter_map[key] = int(value)

        n = parameter_map["N"]
        r = parameter_map["r"]
        p = parameter_map["p"]

        salt = _b64_decode(encoded_salt)
        expected_key = _b64_decode(encoded_key)

        actual_key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=_SCRYPT_MAXMEM,
            dklen=len(expected_key),
        )

        return hmac.compare_digest(actual_key, expected_key)

    except (ValueError, KeyError, TypeError, UnicodeError):
        return False


def password_meets_policy(password: str) -> bool:
    """Return whether a password satisfies the application's minimum policy."""
    return isinstance(password, str) and len(password) >= _MIN_PASSWORD_LENGTH