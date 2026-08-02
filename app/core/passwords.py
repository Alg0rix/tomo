"""Password hashing — scrypt (stdlib), no third-party crypto deps.

Wire format: ``scrypt$N$r$p$salt_b64$hash_b64``
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# Modest params for local appliance login (interactive, not KDF-at-scale).
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_LEN = 16

MIN_PASSWORD_LEN = 8


def hash_password(password: str, *, allow_short: bool = False) -> str:
    """Return a scrypt hash string for storage."""
    if not allow_short and len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    salt = secrets.token_bytes(_SALT_LEN)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=_DKLEN,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(dk).decode("ascii")
    return f"scrypt${_N}${_R}${_P}${salt_b64}${hash_b64}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify against a :func:`hash_password` string."""
    try:
        algo, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$", 5)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        expected = base64.b64decode(hash_b64.encode("ascii"))
    except (ValueError, TypeError):
        return False
    try:
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(dk, expected)
