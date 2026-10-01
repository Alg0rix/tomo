"""Generic session-scoped private bundles and secure-input requests.

No protocol, service-specific fields, plaintext CLI/API getter or environment
export lives here. Backend consumers may read values after authorizing scope.
This does NOT isolate a local shell sharing the backend's OS account/master key.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
import secrets
import threading
import time
from typing import Any

from app.core.secrets import decrypt_secret, encrypt_secret
from app.services import store
from app.services.secret_forms import validate_form, validate_values

NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_LOCK = threading.RLock()
_CAPABILITIES: dict[str, dict[str, Any]] = {}
_PENDING: dict[str, dict[str, Any]] = {}


def public_bundle(row: Any) -> dict[str, Any]:
    return {
        **{key: row[key] for key in ("id", "name", "session_id")},
        "form": json.loads(row["form_json"]),
        "usage": json.loads(row["usage_json"]),
    }


def list_bundles(session_id: str, user_id: str) -> list[dict[str, Any]]:
    return store.with_db(
        lambda conn: [
            public_bundle(row)
            for row in conn.execute(
                "SELECT * FROM secret_bundles WHERE session_id=? AND user_id=? ORDER BY name",
                (session_id, user_id),
            )
        ]
    )


def scoped_bundle(scope: dict[str, Any], name: str):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("A valid secret bundle name or ID is required")
    row = store.with_db(
        lambda conn: conn.execute(
            "SELECT * FROM secret_bundles WHERE (name=? OR id=?) AND session_id=? AND user_id=?",
            (name, name, scope["session_id"], scope["user_id"]),
        ).fetchone()
    )
    if not row:
        raise KeyError(name)
    return row


def runtime_values(row: Any) -> dict[str, str]:
    """Backend-consumer ONLY; never expose through an agent tool, CLI or API."""
    private = decrypt_secret(row["values_ciphertext"])
    if not private:
        raise ValueError("Private values unavailable; request secure input again")
    try:
        values = (
            {"secret": private}
            if row["values_format"] == "single"
            else json.loads(private)
        )
    except ValueError:
        raise ValueError(
            "Private values unavailable; request secure input again"
        ) from None
    return validate_values(json.loads(row["form_json"]), values)


def _prune() -> None:
    now = time.time()
    for token, scope in list(_CAPABILITIES.items()):
        if scope["expires_at"] <= now:
            _CAPABILITIES.pop(token, None)
    for pid, pending in list(_PENDING.items()):
        if pending["expires_at"] <= now or pending["capability"] not in _CAPABILITIES:
            _PENDING.pop(pid, None)


def issue_capability(
    session_id: str,
    user_id: str,
    ttl: float = 120,
    *,
    work_root: str | None = None,
    workplace_id: str | None = None,
) -> str:
    session = store.get_owned_session(session_id, user_id)
    if not session or session.get("channel") != "web":
        raise ValueError("Secure input requires an owned web chat session")
    if workplace_id:
        from app.services.secret_tunnel import available

        wp = store.get_workplace(workplace_id)
        if not wp or wp.get("kind") != "tunnel" or not available(workplace_id):
            raise ValueError(
                "Secure broker requires an online updated tunnel connector"
            )
    token = secrets.token_urlsafe(32)
    with _LOCK:
        _prune()
        _CAPABILITIES[token] = {
            "session_id": session_id,
            "user_id": user_id,
            "expires_at": time.time() + min(max(ttl, 1), 300),
            "work_root": work_root,
            "workplace_id": workplace_id,
        }
    return token


def capability_scope(token: str) -> dict[str, Any] | None:
    with _LOCK:
        _prune()
        scope = _CAPABILITIES.get(token)
        if not scope:
            return None
        scope = dict(scope)
    return (
        scope
        if store.get_owned_session(scope["session_id"], scope["user_id"])
        else None
    )


def revoke_capability(token: str) -> None:
    with _LOCK:
        _CAPABILITIES.pop(token, None)
        _prune()


def cancel_session(session_id: str) -> None:
    with _LOCK:
        for token, scope in list(_CAPABILITIES.items()):
            if scope["session_id"] == session_id:
                _CAPABILITIES.pop(token, None)
        _prune()


@contextmanager
def shell_environment(timeout: float, *, work_root: str | None = None):
    from app.core import config
    from app.runtime.artifacts.fs import current_session_id
    from app.runtime.tools.user_ctx import current_user_id

    env = dict(os.environ)
    for key in (
        "TOMO_BROKER_TOKEN",
        "TOMO_BROKER_URL",
        "TOMO_HTTP_TOKEN",
        "TOMO_HTTP_BROKER",
        "TOMO_SECRET_KEY",
    ):
        env.pop(key, None)
    token = None
    sid = current_session_id()
    if sid:
        try:
            token = issue_capability(
                sid, current_user_id(), ttl=timeout + 5, work_root=work_root
            )
        except ValueError:
            pass
    if token:
        host = "[::1]" if ":" in config.HOST else "127.0.0.1"
        env["TOMO_BROKER_URL"] = f"http://{host}:{config.PORT}"
        env["TOMO_BROKER_TOKEN"] = token
    try:
        yield env
    finally:
        if token:
            revoke_capability(token)


def _public_pending(pending: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v for k, v in pending.items() if k not in {"capability", "user_id", "result"}
    }


def create_request(
    token: str, data: dict[str, Any], *, usage: dict[str, Any] | None = None
) -> dict[str, Any]:
    scope = capability_scope(token)
    if not scope:
        raise ValueError("Broker access expired")
    name = data.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(
            "Bundle name must be 1–64 letters, digits, underscores or hyphens"
        )
    if set(data) - {"name", "form"}:
        raise ValueError("A secret request accepts only a name and metadata-only form")
    form = validate_form(data.get("form"))
    pid = secrets.token_hex(16)
    with _LOCK:
        _prune()
        if (
            sum(
                p["session_id"] == scope["session_id"] and p["status"] == "pending"
                for p in _PENDING.values()
            )
            >= 4
        ):
            raise ValueError("Too many pending secure-input requests")
        pending = {
            "id": pid,
            "name": name,
            "form": form,
            "usage": usage or {},
            "session_id": scope["session_id"],
            "user_id": scope["user_id"],
            "capability": token,
            "status": "pending",
            "expires_at": min(time.time() + 100, scope["expires_at"]),
        }
        _PENDING[pid] = pending
        return _public_pending(pending)


def pending_for_session(session_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        _prune()
        return [
            _public_pending(p)
            for p in _PENDING.values()
            if p["session_id"] == session_id and p["status"] == "pending"
        ]


def pending_request(pid: str) -> dict[str, Any] | None:
    with _LOCK:
        _prune()
        p = _PENDING.get(pid)
        return _public_pending(p) if p else None


def request_status(pid: str, token: str) -> dict[str, Any] | None:
    with _LOCK:
        _prune()
        p = _PENDING.get(pid)
        if not p or p["capability"] != token:
            return None
        return {"id": pid, "status": p["status"], "bundle": p.get("result")}


def resolve_request(
    pid: str,
    session_id: str,
    user_id: str,
    data: dict[str, Any],
    *,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Browser-only input. Consumers validate their usage policy before calling."""
    with _LOCK:
        _prune()
        pending = _PENDING.get(pid)
        if (
            not pending
            or pending["session_id"] != session_id
            or pending["user_id"] != user_id
        ):
            raise KeyError(pid)
        if pending["status"] != "pending":
            raise RuntimeError("Request already resolved")
        if data.get("cancel") is True:
            pending["status"] = "cancelled"
            return {"status": "cancelled"}
        raw_values = data.get("values")
        # A cached first-version single-field form can still submit safely.
        if raw_values is None and [f["name"] for f in pending["form"]["fields"]] == [
            "secret"
        ]:
            raw_values = {"secret": data.get("secret")}
        values = validate_values(pending["form"], raw_values)
        cipher = encrypt_secret(json.dumps(values, ensure_ascii=False))
        bid = "sec_" + secrets.token_hex(8)
        policy = pending["usage"] if usage is None else usage

        def save(conn):
            conn.execute(
                "INSERT INTO secret_bundles (id, session_id, user_id, name, form_json, usage_json, values_ciphertext, values_format) "
                "VALUES (?,?,?,?,?,?,?,'map') ON CONFLICT(session_id, name) DO UPDATE SET "
                "form_json=excluded.form_json, usage_json=excluded.usage_json, values_ciphertext=excluded.values_ciphertext, values_format='map'",
                (
                    bid,
                    session_id,
                    user_id,
                    pending["name"],
                    json.dumps(pending["form"]),
                    json.dumps(policy),
                    cipher,
                ),
            )
            conn.commit()
            return public_bundle(
                conn.execute(
                    "SELECT * FROM secret_bundles WHERE session_id=? AND user_id=? AND name=?",
                    (session_id, user_id, pending["name"]),
                ).fetchone()
            )

        result = store.with_db(save)
        pending["result"] = result
        pending["status"] = "ready"
        return {"status": "ready", "bundle": result}


def delete_bundle(session_id: str, user_id: str, bid: str) -> bool:
    def delete(conn):
        cursor = conn.execute(
            "DELETE FROM secret_bundles WHERE id=? AND session_id=? AND user_id=?",
            (bid, session_id, user_id),
        )
        conn.commit()
        return bool(cursor.rowcount)

    return store.with_db(delete)
