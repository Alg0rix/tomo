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
_MAX_REASONING_EFFORTS = 24


def _now() -> float:
    return time.time()


def normalize_reasoning_efforts(value: object) -> list[str]:
    """Return a compact ordered list of unique provider effort values."""
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        effort = item.strip()
        if not effort or effort in out:
            continue
        out.append(effort)
        if len(out) >= _MAX_REASONING_EFFORTS:
            break
    return out


def _reasoning_efforts_from_row(row: sqlite3.Row) -> list[str]:
    try:
        raw = row["reasoning_efforts_json"] or "[]"
    except (IndexError, KeyError):
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return normalize_reasoning_efforts(value)


def _row_to_profile(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "api_key": row["api_key"],  # ciphertext at rest
        "model": row["model"],
        "reasoning_efforts": _reasoning_efforts_from_row(row),
        "auth_mode": row["auth_mode"],
        "subscription_provider": row["subscription_provider"],
        "access_token": row["access_token"],  # ciphertext at rest
        "refresh_token": row["refresh_token"],  # ciphertext at rest
        "token_expires_at": row["token_expires_at"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


def public_profile(row: dict[str, Any]) -> dict[str, Any]:
    """Mask secrets and add ``*_set`` — safe for HTTP/HTML."""
    out = dict(row)
    raw_key = decrypt_secret(str(out.get("api_key") or ""))
    out["api_key_set"] = bool(raw_key)
    out["api_key"] = mask_api_key(raw_key)
    out["access_token_set"] = bool(decrypt_secret(str(out.get("access_token") or "")))
    out["refresh_token_set"] = bool(decrypt_secret(str(out.get("refresh_token") or "")))
    out.pop("access_token", None)
    out.pop("refresh_token", None)
    return out


def _decrypt_profile(row: sqlite3.Row) -> dict[str, Any]:
    """Return a profile with **decrypted** secrets (runtime use only)."""
    prof = _row_to_profile(row)
    prof["api_key"] = decrypt_secret(str(prof["api_key"] or ""))
    prof["access_token"] = decrypt_secret(str(prof["access_token"] or ""))
    prof["refresh_token"] = decrypt_secret(str(prof["refresh_token"] or ""))
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
    reasoning_efforts = normalize_reasoning_efforts(data.get("reasoning_efforts"))
    conn.execute(
        "INSERT INTO llm_profiles "
        "(id, name, base_url, api_key, model, reasoning_efforts_json, "
        "auth_mode, subscription_provider, access_token, refresh_token, "
        "token_expires_at, enabled, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            pid,
            name,
            data.get("base_url") or "",
            encrypt_secret(data.get("api_key")),
            data.get("model") or "",
            json.dumps(reasoning_efforts),
            "api_key",
            "",
            "",
            "",
            0.0,
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
    if "reasoning_efforts" in data:
        sets.append("reasoning_efforts_json=?")
        params.append(json.dumps(normalize_reasoning_efforts(data["reasoning_efforts"])))
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


def find_subscription_profile(
    conn: sqlite3.Connection, provider: str
) -> dict[str, Any] | None:
    """First (oldest) subscription profile for *provider*, decrypted."""
    row = conn.execute(
        "SELECT * FROM llm_profiles WHERE auth_mode='subscription' "
        "AND subscription_provider=? ORDER BY created_at ASC LIMIT 1",
        (provider,),
    ).fetchone()
    return _decrypt_profile(row) if row else None


def create_subscription_profile(
    conn: sqlite3.Connection,
    *,
    provider: str,
    access_token: str,
    refresh_token: str,
    expires_at: float,
    name: str,
    model: str,
    base_url: str,
) -> dict[str, Any]:
    """Create a new subscription-backed profile (ChatGPT/Codex login)."""
    from app.models.ids import unique_id

    pid = unique_id(conn, "llm_profiles", name=name, prefix="", explicit=None)
    conn.execute(
        "INSERT INTO llm_profiles "
        "(id, name, base_url, api_key, model, reasoning_efforts_json, "
        "auth_mode, subscription_provider, access_token, refresh_token, "
        "token_expires_at, enabled, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            pid, name, base_url, "", model, "[]",
            "subscription", provider,
            encrypt_secret(access_token), encrypt_secret(refresh_token),
            float(expires_at), 1, _now(),
        ),
    )
    conn.commit()
    return get_public_profile(conn, pid)


def save_subscription_tokens(
    conn: sqlite3.Connection,
    profile_id: str,
    *,
    access_token: str,
    refresh_token: str,
    expires_at: float,
) -> None:
    """Overwrite the token pair on a subscription profile after login/refresh."""
    conn.execute(
        "UPDATE llm_profiles SET access_token=?, refresh_token=?, token_expires_at=? "
        "WHERE id=?",
        (
            encrypt_secret(access_token),
            encrypt_secret(refresh_token),
            float(expires_at),
            profile_id,
        ),
    )
    conn.commit()


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


def effective_reasoning_effort(
    profile: dict[str, Any] | None, selected: str | None
) -> str | None:
    """Use a supported session value or the profile's highest configured value."""
    efforts = list((profile or {}).get("reasoning_efforts") or [])
    requested = (selected or "").strip()
    if requested and requested in efforts:
        return requested
    return efforts[-1] if efforts else None


_SUBSCRIPTION_REFRESH_SKEW_SECONDS = 60


def _maybe_refresh_subscription(conn: sqlite3.Connection, prof: dict[str, Any]) -> dict[str, Any]:
    """Proactively refresh an expiring subscription token before returning it.

    On an unrecoverable refresh failure, sets ``needs_reauth=True`` on the
    returned dict instead of raising — callers (``get_llm``) turn that into
    a clear config error rather than a confusing wire-level 401.
    """
    prof = dict(prof)
    prof["needs_reauth"] = False
    if prof.get("auth_mode") != "subscription":
        return prof
    expires_at = float(prof.get("token_expires_at") or 0)
    if expires_at <= 0 or expires_at - _now() > _SUBSCRIPTION_REFRESH_SKEW_SECONDS:
        return prof
    from app.runtime.llm import codex_oauth

    try:
        refreshed = codex_oauth.refresh_tokens(prof.get("refresh_token") or "")
    except codex_oauth.CodexAuthError as exc:
        if exc.relogin_required:
            prof["needs_reauth"] = True
            return prof
        # Transient failure (e.g. rate limit) — keep serving the stale token;
        # the next resolve retries.
        return prof
    save_subscription_tokens(
        conn,
        prof["id"],
        access_token=refreshed["access_token"],
        refresh_token=refreshed["refresh_token"],
        expires_at=refreshed["expires_at"],
    )
    prof["access_token"] = refreshed["access_token"]
    prof["refresh_token"] = refreshed["refresh_token"]
    prof["token_expires_at"] = refreshed["expires_at"]
    return prof


def resolve_profile(
    conn: sqlite3.Connection, agent_id: str | None = None
) -> dict[str, Any] | None:
    """Resolve the runtime LLM profile (decrypted) for an agent or default.

    Order: agent's ``model_id`` (if set + enabled) → ``default_model_id``
    (if enabled) → first enabled profile → ``None``. For a ``subscription``
    profile, proactively refreshes an expiring access token first.
    """
    if agent_id:
        arow = conn.execute(
            "SELECT model_id FROM agents WHERE id=?", (agent_id,)
        ).fetchone()
        mid = (arow["model_id"] if arow else "") or ""
        if mid:
            prof = get_profile(conn, mid)
            if prof and prof["enabled"]:
                return _maybe_refresh_subscription(conn, prof)
    default_id = get_default_model_id(conn)
    if default_id:
        prof = get_profile(conn, default_id)
        if prof and prof["enabled"]:
            return _maybe_refresh_subscription(conn, prof)
    row = conn.execute(
        "SELECT * FROM llm_profiles WHERE enabled=1 ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return _maybe_refresh_subscription(conn, _decrypt_profile(row))


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
    "normalize_reasoning_efforts",
    "effective_reasoning_effort",
    "find_subscription_profile",
    "create_subscription_profile",
    "save_subscription_tokens",
]
