"""Platform settings — key/value rows in the ``settings`` table.

Values are JSON-encoded. The default settings shape comes from
:func:`app.services.platform_data.seed_settings` (used to seed an empty DB and
as a fallback when the table has no rows).

Secret fields (``llm_api_key``, ``telegram_bot_token``) are UI-managed secrets:
stored as **ciphertext** at rest (see :mod:`app.core.secrets`) and decrypted
only in memory by :func:`get_settings` for runtime use. API/UI surfaces must
call :func:`public_settings` so secrets are masked and never echoed over
HTTP/HTML. A blank PUT keeps the existing ciphertext.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core.secrets import decrypt_secret, encrypt_secret
from app.services.platform_data import seed_settings

_LLM_KEY = "llm_api_key"
_TG_TOKEN_KEY = "telegram_bot_token"
_SECRET_KEYS = frozenset({_LLM_KEY, _TG_TOKEN_KEY})


def _defaults() -> dict[str, Any]:
    return dict(seed_settings())


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return full settings (secrets decrypted) merged with defaults."""
    rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    out = _defaults()
    for r in rows:
        val = json.loads(r["value_json"])
        if r["key"] in _SECRET_KEYS:
            val = decrypt_secret(str(val))
        out[r["key"]] = val
    return out


def mask_api_key(key: str) -> str:
    """Mask a secret for display: empty stays empty; else bullets + last 4."""
    raw = (key or "").strip()
    if not raw:
        return ""
    if len(raw) <= 4:
        return "••••"
    return "••••" + raw[-4:]


def public_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Copy of settings safe for HTTP/HTML — secrets masked, plus ``*_set`` flags."""
    out = dict(data)
    for key in _SECRET_KEYS:
        raw = str(out.get(key) or "")
        out[f"{key}_set"] = bool(raw.strip())
        out[key] = mask_api_key(raw)
    return out


def update_settings(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Upsert settings. Empty/whitespace secret fields keep the existing value.

    Empty ``llm_base_url`` / ``llm_model`` are ignored (keep existing / defaults).
    When ``llm_model`` is set, also writes ``default_model`` to the same value.
    Returns **public** settings (masked secrets) — callers that need raw secrets
    should call :func:`get_settings` separately.
    """
    payload = dict(data)
    for secret_key in _SECRET_KEYS:
        if secret_key not in payload:
            continue
        incoming = payload[secret_key]
        if incoming is None or (isinstance(incoming, str) and not incoming.strip()):
            # Blank/missing secret keeps the existing ciphertext (never clears).
            payload.pop(secret_key)
        else:
            # Encrypt at rest; the DB never stores a plaintext secret.
            payload[secret_key] = encrypt_secret(str(incoming))

    for soft in ("llm_base_url", "llm_model"):
        if soft in payload:
            val = payload[soft]
            if val is None or (isinstance(val, str) and not str(val).strip()):
                payload.pop(soft)

    if "llm_model" in payload and payload["llm_model"] is not None:
        payload["default_model"] = payload["llm_model"]

    if "approvals_mode" in payload and payload["approvals_mode"] is not None:
        from app.runtime.permissions.modes import normalize_mode

        payload["approvals_mode"] = normalize_mode(payload["approvals_mode"])

    for key, value in payload.items():
        if value is None:
            continue
        conn.execute(
            "INSERT INTO settings (key, value_json) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value)),
        )
    conn.commit()
    return public_settings(get_settings(conn))
