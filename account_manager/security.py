"""Password hashing and API tokens.

Uses scrypt from the standard library rather than argon2. argon2id would be
the textbook choice, but it means a compiled dependency, and this project's
main selling point is that a stranger can pip install it and have it work.
scrypt is memory-hard, has been in Python since 3.6, and at these
parameters is far beyond what a stolen database of a small company's
logins is worth attacking.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# ~16 MB and roughly 100 ms per hash on ordinary hardware: slow enough to
# make guessing expensive, fast enough that logging in feels instant.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

TOKEN_BYTES = 32
MIN_PASSWORD_LENGTH = 8


class WeakPassword(ValueError):
    """The password is too easy to guess to be worth storing."""


def check_password_strength(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )


def hash_password(password: str) -> str:
    """Return a self-describing hash: scrypt$n$r$p$salt$key, all hex."""
    check_password_strength(password)
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    try:
        scheme, n, r, p, salt_hex, key_hex = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        key = hashlib.scrypt(
            (password or "").encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(key_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(key.hex(), key_hex)


def new_token() -> str:
    """A fresh API token. Shown once, then only its hash is kept."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Tokens are long random strings, so a fast hash is the right tool —
    there is nothing to guess, and lookups happen on every request."""
    return hashlib.sha256((token or "").encode()).hexdigest()
