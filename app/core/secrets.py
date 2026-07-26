"""At-rest encryption for UI-managed secrets (Alpha spec §2.1).

UI-managed secrets (``llm_api_key``, tokens, …) live in the SQLite ``settings``
table as **ciphertext**, never plaintext. A master key encrypts/decrypts with
Fernet (symmetric authenticated encryption).

Master key sources (first match wins):

1. Process env ``TOMO_SECRET_KEY`` (preferred for containers / CI).
2. ``$TOMO_HOME/.secret_key`` (auto-created by
   :func:`app.core.home.ensure_tomo_home`; chmod 600; never overwritten).

Ciphertext carries a versioned prefix ``enc:v1:`` so a plaintext value is never
mistaken for ciphertext. Decrypt only in memory for runtime; never log
plaintext. Losing the master key makes encrypted secrets unrecoverable — back
up ``.secret_key`` / ``TOMO_SECRET_KEY`` with the same care as the DB.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core import config

_PREFIX = "enc:v1:"


def load_master_key(*, home_root: Path | None = None) -> bytes:
    """Return the master key bytes from env or ``$TOMO_HOME/.secret_key``.

    When neither source is available, a new key is generated into
    ``$TOMO_HOME/.secret_key`` (chmod 600) as a last resort so encryption always
    works — :func:`app.core.home.ensure_tomo_home` normally creates it first.
    """
    env_key = (os.environ.get("TOMO_SECRET_KEY") or "").strip()
    if env_key:
        return env_key.encode("utf-8")
    root = Path(home_root) if home_root is not None else config.TOMO_HOME
    sk = root / ".secret_key"
    if sk.is_file():
        data = sk.read_bytes().strip()
        if data:
            return data
    root.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    sk.write_bytes(key)
    try:
        os.chmod(sk, 0o600)
    except OSError:
        pass
    return key


def _fernet() -> Fernet:
    return Fernet(load_master_key())


def encrypt_secret(value: str | None) -> str:
    """Encrypt a secret string, returning ``enc:v1:<token>``. Empty -> empty."""
    raw = (value or "").strip()
    if not raw:
        return ""
    token = _fernet().encrypt(raw.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_secret(value: str | None) -> str:
    """Decrypt an ``enc:v1:`` value. Empty -> empty; non-ciphertext -> empty.

    A non-empty value without the ``enc:v1:`` prefix is refused (Alpha
    invariant: secrets are always ciphertext at rest). A bad/expired token also
    yields an empty string so runtime never crashes on a corrupt row.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if not raw.startswith(_PREFIX):
        return ""
    token = raw[len(_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return ""


__all__ = ["load_master_key", "encrypt_secret", "decrypt_secret"]
