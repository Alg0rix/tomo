"""Platform settings — key/value rows in the ``settings`` table.

Values are JSON-encoded. The default settings shape comes from
:func:`app.services.platform_data.seed_settings` (used to seed an empty DB and
as a fallback when the table has no rows).

``llm_api_key`` is stored in full for runtime use. API/UI surfaces must call
:func:`public_settings` so the secret is masked and never echoed.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.services.platform_data import seed_settings

_LLM_KEY = "llm_api_key"


def _defaults() -> dict[str, Any]:
    return dict(seed_settings())


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return full settings (including raw ``llm_api_key``) merged with defaults."""
    rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    out = _defaults()
    for r in rows:
        out[r["key"]] = json.loads(r["value_json"])
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
    """Copy of settings safe for HTTP/HTML — API key masked, plus ``llm_api_key_set``."""
    out = dict(data)
    raw = str(out.get(_LLM_KEY) or "")
    out["llm_api_key_set"] = bool(raw.strip())
    out[_LLM_KEY] = mask_api_key(raw)
    return out


def update_settings(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """Upsert settings. Empty/whitespace ``llm_api_key`` keeps the existing key.

    Empty ``llm_base_url`` / ``llm_model`` are ignored (keep existing / defaults).
    When ``llm_model`` is set, also writes ``default_model`` to the same value.
    Returns **public** settings (masked key) — callers that need the raw key
    should call :func:`get_settings` separately.
    """
    payload = dict(data)
    if _LLM_KEY in payload:
        incoming = payload[_LLM_KEY]
        if incoming is None or (isinstance(incoming, str) and not incoming.strip()):
            payload.pop(_LLM_KEY)

    for soft in ("llm_base_url", "llm_model"):
        if soft in payload:
            val = payload[soft]
            if val is None or (isinstance(val, str) and not str(val).strip()):
                payload.pop(soft)

    if "llm_model" in payload and payload["llm_model"] is not None:
        payload["default_model"] = payload["llm_model"]

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
