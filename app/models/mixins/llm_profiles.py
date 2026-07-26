"""LLM model profiles — CRUD over the ``llm_profiles`` table (Alpha §2.2).

Each profile is an OpenAI-compatible endpoint config: ``base_url``, an
encrypted ``api_key`` (Fernet at rest via :mod:`app.core.secrets`), a
``model`` string, and an ``enabled`` flag. The default profile id lives in
the ``settings`` key ``default_model_id``.

Secret contract (same as ``llm_api_key`` in settings):

* ``api_key`` is **ciphertext** at rest — never plaintext in the DB column.
* Public views (:func:`list_profiles`, :func:`get_public_profile`,
  :func:`create_profile`, :func:`update_profile` returns) **mask** the key and
  add ``api_key_set``; the decrypted key never leaves this module's runtime
  helpers (:func:`get_profile`, :func:`resolve_profile`).
* A **blank** ``api_key`` on update keeps the existing ciphertext (never
  clears).

Runtime resolution (:func:`resolve_profile`): agent's ``model_id`` profile if
set and enabled → ``default_model_id`` if enabled → first enabled profile →
``None``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from app.core.secrets import decrypt_secret, encrypt_secret
from app.models.mixins.settings import mask_api_key

_DEFAULT_MODEL_KEY = "default_model_id"


def _now() -> float:
    return time.time()


def _row_to_profile(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "api_key": row["api_key"],  # ciphertext at rest
        "model": row["model"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


def public_profile(row: dict[str, Any]) -> dict[str, Any]:
    """Mask the api_key and add ``api_key_set`` — safe for HTTP/HTML."""
    out = dict(row)
    raw_key = decrypt_secret(str(out.get("api_key") or ""))
    out["api_key_set"] = bool(raw_key)
    out["api_key"] = mask_api_key(raw_key)
    return out


def _decrypt_profile(row: sqlite3.Row) -> dict[str, Any]:
    """Return a profile with the **decrypted** api_key (runtime use only)."""
    prof = _row_to_profile(row)
    prof["api_key"] = decrypt_secret(str(prof["api_key"] or ""))
    return prof


def list_profiles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Public (masked) profiles, oldest first."""
    rows = conn.execute("SELECT * FROM llm_profiles ORDER BY created_at ASC").fetchall()
    return [public_profile(_row_to_profile(r)) for r in rows]


def get_public_profile(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM llm_profiles WHERE id=?", (profile_id,)).fetchone()
    return public_profile(_row_to_profile(row)) if row else None


def get_profile(conn: sqlite3.Connection, profile_id: str) -> dict[str, Any] | None:
    """Return a profile with the **decrypted** api_key (runtime use only)."""
    row = conn.execute("SELECT * FROM llm_profiles WHERE id=?", (profile_id,)).fetchone()
    return _decrypt_profile(row) if row else None


def create_profile(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    from app.models.ids import unique_id

    name = (data.get("name") or "").strip() or "profile"
    pid = unique_id(
        conn,
        "llm_profiles",
        name=name,
        prefix="",
        explicit=(data.get("id") or None),
    )
    conn.execute(
        "INSERT INTO llm_profiles (id, name, base_url, api_key, model, enabled, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            pid,
            name,
            data.get("base_url") or "",
            encrypt_secret(data.get("api_key")),
            data.get("model") or "",
            1 if data.get("enabled", True) else 0,
            _now(),
        ),
    )
    conn.commit()
    return get_public_profile(conn, pid)


def update_profile(
    conn: sqlite3.Connection, profile_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM llm_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return None
    sets: list[str] = []
    params: list[Any] = []
    for key in ("name", "base_url", "model"):
        if key in data and data[key] is not None:
            sets.append(f"{key}=?")
            params.append(data[key])
    # Blank/missing api_key keeps the existing ciphertext (never clears).
    if "api_key" in data:
        incoming = data["api_key"]
        if incoming is not None and str(incoming).strip():
            sets.append("api_key=?")
            params.append(encrypt_secret(str(incoming)))
    if "enabled" in data and data["enabled"] is not None:
        sets.append("enabled=?")
        params.append(1 if data["enabled"] else 0)
    if sets:
        params.append(profile_id)
        conn.execute(f"UPDATE llm_profiles SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return get_public_profile(conn, profile_id)


def delete_profile(conn: sqlite3.Connection, profile_id: str) -> bool:
    if not conn.execute("SELECT 1 FROM llm_profiles WHERE id=?", (profile_id,)).fetchone():
        return False
    conn.execute("DELETE FROM llm_profiles WHERE id=?", (profile_id,))
    conn.commit()
    return True


def set_default_model_id(conn: sqlite3.Connection, profile_id: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value_json) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
        (_DEFAULT_MODEL_KEY, json.dumps(profile_id)),
    )
    conn.commit()


def get_default_model_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value_json FROM settings WHERE key=?", (_DEFAULT_MODEL_KEY,)
    ).fetchone()
    if not row:
        return ""
    try:
        return str(json.loads(row["value_json"])) or ""
    except (TypeError, ValueError):
        return ""


def resolve_profile(
    conn: sqlite3.Connection, agent_id: str | None = None
) -> dict[str, Any] | None:
    """Resolve the runtime LLM profile (decrypted) for an agent or default.

    Order: agent's ``model_id`` (if set + enabled) → ``default_model_id``
    (if enabled) → first enabled profile → ``None``.
    """
    if agent_id:
        arow = conn.execute(
            "SELECT model_id FROM agents WHERE id=?", (agent_id,)
        ).fetchone()
        mid = (arow["model_id"] if arow else "") or ""
        if mid:
            prof = get_profile(conn, mid)
            if prof and prof["enabled"]:
                return prof
    default_id = get_default_model_id(conn)
    if default_id:
        prof = get_profile(conn, default_id)
        if prof and prof["enabled"]:
            return prof
    row = conn.execute(
        "SELECT * FROM llm_profiles WHERE enabled=1 ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    return _decrypt_profile(row) if row else None


__all__ = [
    "list_profiles",
    "get_public_profile",
    "get_profile",
    "create_profile",
    "update_profile",
    "delete_profile",
    "set_default_model_id",
    "get_default_model_id",
    "resolve_profile",
    "public_profile",
]
