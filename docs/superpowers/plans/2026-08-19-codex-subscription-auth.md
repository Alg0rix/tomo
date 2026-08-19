# Codex/ChatGPT Subscription Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an `llm_profiles` row authenticate via a ChatGPT/Codex subscription (OAuth device-code login) instead of an API key, and route chat through the Responses API against `https://chatgpt.com/backend-api/codex`.

**Architecture:** Five new columns on `llm_profiles` (`auth_mode`, `subscription_provider`, encrypted `access_token`/`refresh_token`, `token_expires_at`). A new sync `codex_oauth.py` module owns the device-code login and refresh HTTP calls. `resolve_profile()` proactively refreshes an expiring subscription token before returning the profile. A new `codex_responses.py` client implements the existing `LLMClient` protocol against the Responses API. `get_llm()` branches on `auth_mode`. Two new web endpoints drive the login UI.

**Tech Stack:** Python 3.12, FastAPI, sqlite3, `openai` SDK (`>=2.50.0`, already a dependency) for the Responses API, `httpx` for the raw OAuth HTTP calls, vanilla JS (`app/static/js/system.js`) for the settings UI.

## Global Constraints

- `access_token`/`refresh_token` are ciphertext at rest via `app.core.secrets.encrypt_secret`/`decrypt_secret` — same contract as the existing `api_key` column. Never plaintext in the DB, never raw in a public API/HTML payload.
- All DB schema changes use the existing idempotent `ALTER TABLE ... ADD COLUMN` migration pattern in `app/models/schema.py` (see the `reasoning_efforts_json` precedent at line ~532).
- New client (`codex_responses.py`) implements the existing `LLMClient` protocol (`app/runtime/llm/base.py`) exactly — `complete(messages, tools=None) -> LLMResponse` and `stream_complete(messages, tools=None) -> AsyncIterator[dict]` yielding `{"type": "delta", ...}` then `{"type": "done", "response": LLMResponse}`.
- Reuse `app/runtime/llm/openai_compat.py`'s `LLMConfigError`, `LLMRequestError`, `format_llm_error`, `parse_usage`, `_parse_arguments`, `default_llm_timeout_seconds` rather than duplicating them.
- OAuth client id `app_EMoamEEZ73f0CkXaXp7hrann` is OpenAI's published Codex CLI client id (public, not a secret) — same one `tmp/hermes-agent` uses.
- No CLI login command, no separate `oauth_credentials` table, no global singleton credential, no reasoning-item replay, no `~/.codex/auth.json` import fallback — see spec `docs/superpowers/specs/2026-08-19-codex-subscription-auth-design.md` "Out of scope".
- Every task ends green (`pytest` passing) and commits before moving to the next task.

---

### Task 1: Schema + model-layer support for subscription profiles

**Files:**
- Modify: `app/models/schema.py` (CREATE TABLE `llm_profiles` block ~line 49-58, migration block ~line 532-537)
- Modify: `app/models/mixins/llm_profiles.py`
- Test: `tests/unit/models/test_llm_profiles.py`

**Interfaces:**
- Produces: `llm_profiles_store.public_profile(row)` now includes `auth_mode: str`, `subscription_provider: str`, `access_token_set: bool`, `refresh_token_set: bool`, `token_expires_at: float` (no raw token values). `llm_profiles_store.get_profile()`/`resolve_profile()` (decrypted, runtime-only) include `access_token: str`, `refresh_token: str` (decrypted), `token_expires_at: float`.
- Produces: `llm_profiles_store.save_subscription_tokens(conn, profile_id, *, access_token, refresh_token, expires_at) -> None`.
- Produces: `llm_profiles_store.find_subscription_profile(conn, provider) -> dict | None` (decrypted, first match by `created_at`).
- Produces: `llm_profiles_store.create_subscription_profile(conn, *, provider, access_token, refresh_token, expires_at, name, model, base_url) -> dict` (public/masked return).

- [ ] **Step 1: Add columns to the CREATE TABLE statement**

In `app/models/schema.py`, the `llm_profiles` table currently ends:
```sql
    reasoning_efforts_json TEXT NOT NULL DEFAULT '[]',
    enabled                INTEGER NOT NULL DEFAULT 1,
    created_at             REAL NOT NULL DEFAULT 0
);
```
Change to:
```sql
    reasoning_efforts_json TEXT NOT NULL DEFAULT '[]',
    auth_mode              TEXT NOT NULL DEFAULT 'api_key',
    subscription_provider  TEXT NOT NULL DEFAULT '',
    access_token           TEXT NOT NULL DEFAULT '',
    refresh_token          TEXT NOT NULL DEFAULT '',
    token_expires_at       REAL NOT NULL DEFAULT 0,
    enabled                INTEGER NOT NULL DEFAULT 1,
    created_at             REAL NOT NULL DEFAULT 0
);
```

- [ ] **Step 2: Add idempotent migration for existing DBs**

In `app/models/schema.py`, right after the existing block:
```python
    profile_cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_profiles)")}
    if "reasoning_efforts_json" not in profile_cols:
        conn.execute(
            "ALTER TABLE llm_profiles "
            "ADD COLUMN reasoning_efforts_json TEXT NOT NULL DEFAULT '[]'"
        )
```
add:
```python
    _profile_alters = {
        "auth_mode": "ALTER TABLE llm_profiles ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'api_key'",
        "subscription_provider": "ALTER TABLE llm_profiles ADD COLUMN subscription_provider TEXT NOT NULL DEFAULT ''",
        "access_token": "ALTER TABLE llm_profiles ADD COLUMN access_token TEXT NOT NULL DEFAULT ''",
        "refresh_token": "ALTER TABLE llm_profiles ADD COLUMN refresh_token TEXT NOT NULL DEFAULT ''",
        "token_expires_at": "ALTER TABLE llm_profiles ADD COLUMN token_expires_at REAL NOT NULL DEFAULT 0",
    }
    for _col, _sql in _profile_alters.items():
        if _col not in profile_cols:
            conn.execute(_sql)
```

- [ ] **Step 3: Write failing tests for the model layer**

Append to `tests/unit/models/test_llm_profiles.py`:
```python
def test_create_subscription_profile_encrypts_tokens(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        prof = llm_profiles_store.create_subscription_profile(
            store._conn,
            provider="openai-codex",
            access_token="at-secret-123",
            refresh_token="rt-secret-456",
            expires_at=9999999999.0,
            name="ChatGPT (Codex)",
            model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
    assert prof["auth_mode"] == "subscription"
    assert prof["subscription_provider"] == "openai-codex"
    assert prof["access_token_set"] is True
    assert prof["refresh_token_set"] is True
    assert "access_token" not in prof
    assert "refresh_token" not in prof
    raw = store._conn.execute(
        "SELECT access_token, refresh_token FROM llm_profiles WHERE id=?", (prof["id"],)
    ).fetchone()
    assert raw["access_token"].startswith("enc:v1:")
    assert "at-secret-123" not in raw["access_token"]
    assert raw["refresh_token"].startswith("enc:v1:")


def test_find_subscription_profile_returns_decrypted(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-1",
            refresh_token="rt-1", expires_at=123.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        found = llm_profiles_store.find_subscription_profile(store._conn, "openai-codex")
    assert found is not None
    assert found["id"] == created["id"]
    assert found["access_token"] == "at-1"
    assert found["refresh_token"] == "rt-1"
    assert found["token_expires_at"] == 123.0


def test_save_subscription_tokens_reencrypts(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-old",
            refresh_token="rt-old", expires_at=1.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.save_subscription_tokens(
            store._conn, created["id"],
            access_token="at-new", refresh_token="rt-new", expires_at=2.0,
        )
        refreshed = llm_profiles_store.get_profile(store._conn, created["id"])
    assert refreshed["access_token"] == "at-new"
    assert refreshed["refresh_token"] == "rt-new"
    assert refreshed["token_expires_at"] == 2.0


def test_resolve_profile_includes_subscription_fields(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-x",
            refresh_token="rt-x", expires_at=42.0, name="ChatGPT (Codex)",
            model="gpt-5-codex", base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])
    resolved = store.resolve_llm_profile(None)
    assert resolved["auth_mode"] == "subscription"
    assert resolved["access_token"] == "at-x"
    assert resolved["token_expires_at"] == 42.0
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/models/test_llm_profiles.py -k subscription -v`
Expected: FAIL with `AttributeError: module 'app.models.mixins.llm_profiles' has no attribute 'create_subscription_profile'`

- [ ] **Step 5: Implement the model-layer changes**

In `app/models/mixins/llm_profiles.py`, update `_row_to_profile`:
```python
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
```

Update `public_profile`:
```python
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
```

Update `_decrypt_profile`:
```python
def _decrypt_profile(row: sqlite3.Row) -> dict[str, Any]:
    """Return a profile with **decrypted** secrets (runtime use only)."""
    prof = _row_to_profile(row)
    prof["api_key"] = decrypt_secret(str(prof["api_key"] or ""))
    prof["access_token"] = decrypt_secret(str(prof["access_token"] or ""))
    prof["refresh_token"] = decrypt_secret(str(prof["refresh_token"] or ""))
    return prof
```

Update `create_profile`'s INSERT to include the new columns with their `api_key`-style defaults (empty/`'api_key'` mode — this function stays the path for manual API-key profiles, so it never sets subscription fields):
```python
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
```

Add three new functions after `update_profile`:
```python
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
```

Add the three new names to `__all__`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/models/test_llm_profiles.py -v`
Expected: PASS (all tests, including pre-existing ones — confirms the new columns didn't break the api_key path)

- [ ] **Step 7: Add store wrappers and commit**

In `app/services/store.py`, after `resolve_llm_profile`, add:
```python
    def find_subscription_llm_profile(self, provider: str) -> dict[str, Any] | None:
        with self._lock:
            return llm_profiles_store.find_subscription_profile(self._conn, provider)

    def create_subscription_llm_profile(
        self, *, provider: str, access_token: str, refresh_token: str,
        expires_at: float, name: str, model: str, base_url: str,
    ) -> dict[str, Any]:
        with self._lock:
            return llm_profiles_store.create_subscription_profile(
                self._conn, provider=provider, access_token=access_token,
                refresh_token=refresh_token, expires_at=expires_at,
                name=name, model=model, base_url=base_url,
            )

    def save_subscription_llm_tokens(
        self, profile_id: str, *, access_token: str, refresh_token: str, expires_at: float
    ) -> None:
        with self._lock:
            llm_profiles_store.save_subscription_tokens(
                self._conn, profile_id, access_token=access_token,
                refresh_token=refresh_token, expires_at=expires_at,
            )
```

```bash
cd /home/dev-serv/Project/py-proj/tomo
git add app/models/schema.py app/models/mixins/llm_profiles.py app/services/store.py tests/unit/models/test_llm_profiles.py
git commit -m "feat(llm): add subscription auth columns to llm_profiles"
```

---

### Task 2: `codex_oauth.py` — device-code login and token refresh

**Files:**
- Create: `app/runtime/llm/codex_oauth.py`
- Test: `tests/unit/runtime/llm/test_codex_oauth.py`

**Interfaces:**
- Consumes: nothing from other tasks (standalone HTTP module).
- Produces: `DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"`, `CodexAuthError(RuntimeError)` with `.code: str` and `.relogin_required: bool` attributes, `start_device_login(*, timeout=15.0, transport=None) -> dict` returning `{"user_code": str, "device_auth_id": str, "verification_url": str, "interval": int}`, `poll_device_login(device_auth_id: str, user_code: str, *, timeout=15.0, transport=None) -> dict | None` returning `{"access_token": str, "refresh_token": str, "expires_at": float}` or `None` (still pending), `refresh_tokens(refresh_token: str, *, timeout=20.0, transport=None) -> dict` returning the same shape, raising `CodexAuthError` on failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/runtime/llm/test_codex_oauth.py`:
```python
"""Codex OAuth device-code login + refresh — HTTP mapping via httpx.MockTransport.

No real network calls. Mirrors the wire shapes tmp/hermes-agent's
hermes_cli/auth.py uses against auth.openai.com.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.runtime.llm.codex_oauth import (
    CodexAuthError,
    poll_device_login,
    refresh_tokens,
    start_device_login,
)


def _jwt_with_exp(exp: float) -> str:
    import base64

    def _b64(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{_b64({'alg': 'none'})}.{_b64({'exp': exp})}.sig"


def test_start_device_login_returns_code(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/accounts/deviceauth/usercode"
        return httpx.Response(
            200,
            json={"user_code": "ABCD-1234", "device_auth_id": "dev-1", "interval": "5"},
        )

    result = start_device_login(transport=httpx.MockTransport(handler))
    assert result["user_code"] == "ABCD-1234"
    assert result["device_auth_id"] == "dev-1"
    assert result["interval"] == 5
    assert result["verification_url"] == "https://auth.openai.com/codex/device"


def test_poll_device_login_pending_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    result = poll_device_login("dev-1", "ABCD-1234", transport=httpx.MockTransport(handler))
    assert result is None


def test_poll_device_login_success_exchanges_tokens() -> None:
    exp = time.time() + 3600
    access_token = _jwt_with_exp(exp)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/accounts/deviceauth/token":
            return httpx.Response(
                200, json={"authorization_code": "auth-code-1", "code_verifier": "verifier-1"}
            )
        if request.url.path == "/oauth/token":
            body = dict(httpx.QueryParams(request.content.decode()))
            assert body["grant_type"] == "authorization_code"
            assert body["code"] == "auth-code-1"
            return httpx.Response(
                200, json={"access_token": access_token, "refresh_token": "rt-1"}
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    result = poll_device_login("dev-1", "ABCD-1234", transport=httpx.MockTransport(handler))
    assert result is not None
    assert result["access_token"] == access_token
    assert result["refresh_token"] == "rt-1"
    assert result["expires_at"] == pytest.approx(exp, abs=1)


def test_refresh_tokens_success() -> None:
    exp = time.time() + 7200
    new_access = _jwt_with_exp(exp)

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(httpx.QueryParams(request.content.decode()))
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "rt-old"
        return httpx.Response(200, json={"access_token": new_access, "refresh_token": "rt-new"})

    result = refresh_tokens("rt-old", transport=httpx.MockTransport(handler))
    assert result["access_token"] == new_access
    assert result["refresh_token"] == "rt-new"
    assert result["expires_at"] == pytest.approx(exp, abs=1)


def test_refresh_tokens_invalid_grant_requires_relogin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant", "error_description": "expired"})

    with pytest.raises(CodexAuthError) as exc_info:
        refresh_tokens("rt-old", transport=httpx.MockTransport(handler))
    assert exc_info.value.relogin_required is True
    assert exc_info.value.code == "invalid_grant"


def test_refresh_tokens_rate_limited_does_not_require_relogin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    with pytest.raises(CodexAuthError) as exc_info:
        refresh_tokens("rt-old", transport=httpx.MockTransport(handler))
    assert exc_info.value.relogin_required is False
    assert exc_info.value.code == "codex_rate_limited"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/runtime/llm/test_codex_oauth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.runtime.llm.codex_oauth'`

- [ ] **Step 3: Implement `app/runtime/llm/codex_oauth.py`**

```python
"""OpenAI Codex/ChatGPT subscription OAuth — device-code login and refresh.

Ported and trimmed from ``tmp/hermes-agent``'s ``hermes_cli/auth.py``. Lets a
user authenticate against OpenAI's Codex backend with their ChatGPT
Plus/Pro/Team subscription instead of an API key, then talks to
``https://chatgpt.com/backend-api/codex`` via the Responses API
(:mod:`app.runtime.llm.codex_responses`).

All HTTP calls here are synchronous (:class:`httpx.Client`) to match
``resolve_profile()`` (plain sqlite3, no event loop) — see the design spec
at ``docs/superpowers/specs/2026-08-19-codex-subscription-auth-design.md``.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

CODEX_ISSUER = "https://auth.openai.com"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = f"{CODEX_ISSUER}/oauth/token"
CODEX_DEVICE_VERIFICATION_URL = f"{CODEX_ISSUER}/codex/device"
DEFAULT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_REDIRECT_URI = f"{CODEX_ISSUER}/deviceauth/callback"


class CodexAuthError(RuntimeError):
    """Raised on any Codex OAuth failure (device login or refresh)."""

    def __init__(self, message: str, *, code: str = "codex_auth_error", relogin_required: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.relogin_required = relogin_required


def _decode_jwt_exp(access_token: str) -> float:
    """Best-effort ``exp`` claim from a JWT access token; ``0.0`` if unreadable."""
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return 0.0
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode()))
        exp = claims.get("exp")
        return float(exp) if isinstance(exp, (int, float)) else 0.0
    except Exception:
        return 0.0


def _client(timeout: float, transport: httpx.BaseTransport | None) -> httpx.Client:
    kwargs: dict[str, Any] = {"timeout": httpx.Timeout(timeout)}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.Client(**kwargs)


def start_device_login(
    *, timeout: float = 15.0, transport: httpx.BaseTransport | None = None
) -> dict[str, Any]:
    """Request a device code. Returns ``{user_code, device_auth_id, verification_url, interval}``."""
    with _client(timeout, transport) as client:
        resp = client.post(
            f"{CODEX_ISSUER}/api/accounts/deviceauth/usercode",
            json={"client_id": CODEX_OAUTH_CLIENT_ID},
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code == 429:
        raise CodexAuthError(
            "OpenAI is rate-limiting Codex login requests. Wait a minute and try again.",
            code="codex_rate_limited",
        )
    if resp.status_code != 200:
        raise CodexAuthError(
            f"Device code request returned status {resp.status_code}.",
            code="device_code_request_error",
        )
    data = resp.json()
    user_code = data.get("user_code", "")
    device_auth_id = data.get("device_auth_id", "")
    if not user_code or not device_auth_id:
        raise CodexAuthError(
            "Device code response missing required fields.", code="device_code_incomplete"
        )
    interval = 5
    try:
        interval = max(3, int(data.get("interval", 5)))
    except (TypeError, ValueError):
        pass
    return {
        "user_code": user_code,
        "device_auth_id": device_auth_id,
        "verification_url": CODEX_DEVICE_VERIFICATION_URL,
        "interval": interval,
    }


def _exchange_authorization_code(
    authorization_code: str, code_verifier: str, *, timeout: float, transport: httpx.BaseTransport | None
) -> dict[str, Any]:
    with _client(timeout, transport) as client:
        resp = client.post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": _REDIRECT_URI,
                "client_id": CODEX_OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise CodexAuthError(
            f"Token exchange returned status {resp.status_code}.", code="token_exchange_error"
        )
    payload = resp.json()
    access_token = payload.get("access_token", "")
    refresh_token = payload.get("refresh_token", "")
    if not access_token:
        raise CodexAuthError(
            "Token exchange did not return an access_token.",
            code="token_exchange_no_access_token",
        )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": _decode_jwt_exp(access_token),
    }


def poll_device_login(
    device_auth_id: str,
    user_code: str,
    *,
    timeout: float = 15.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    """Poll once. Returns tokens on success, ``None`` if still pending.

    Callers re-invoke this on the ``interval`` cadence returned by
    :func:`start_device_login` (the web endpoint wraps this; the browser
    drives the polling cadence via ``setInterval``).
    """
    with _client(timeout, transport) as client:
        resp = client.post(
            f"{CODEX_ISSUER}/api/accounts/deviceauth/token",
            json={"device_auth_id": device_auth_id, "user_code": user_code},
            headers={"Content-Type": "application/json"},
        )
    if resp.status_code in {403, 404}:
        return None
    if resp.status_code != 200:
        raise CodexAuthError(
            f"Device auth polling returned status {resp.status_code}.",
            code="device_code_poll_error",
        )
    data = resp.json()
    authorization_code = data.get("authorization_code", "")
    code_verifier = data.get("code_verifier", "")
    if not authorization_code or not code_verifier:
        raise CodexAuthError(
            "Device auth response missing authorization_code or code_verifier.",
            code="device_code_incomplete_exchange",
        )
    return _exchange_authorization_code(
        authorization_code, code_verifier, timeout=timeout, transport=transport
    )


def refresh_tokens(
    refresh_token: str,
    *,
    timeout: float = 20.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Refresh an expiring/expired Codex access token."""
    if not refresh_token or not refresh_token.strip():
        raise CodexAuthError(
            "Codex auth is missing refresh_token. Sign in again.",
            code="codex_auth_missing_refresh_token",
            relogin_required=True,
        )
    with _client(timeout, transport) as client:
        resp = client.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
        )
    if resp.status_code == 429:
        raise CodexAuthError(
            "Codex provider quota exhausted (429). Credentials are still valid; "
            "retry after the usage limit resets.",
            code="codex_rate_limited",
            relogin_required=False,
        )
    if resp.status_code != 200:
        code = "codex_refresh_failed"
        relogin_required = resp.status_code in {401, 403}
        try:
            err = resp.json()
            err_code = err.get("error") if isinstance(err, dict) else None
            if isinstance(err_code, str) and err_code.strip():
                code = err_code.strip()
        except Exception:
            pass
        if code in {"invalid_grant", "invalid_token", "invalid_request"}:
            relogin_required = True
        raise CodexAuthError(
            f"Codex token refresh failed with status {resp.status_code}.",
            code=code,
            relogin_required=relogin_required,
        )
    payload = resp.json()
    new_access = payload.get("access_token")
    if not isinstance(new_access, str) or not new_access.strip():
        raise CodexAuthError(
            "Codex token refresh response was missing access_token.",
            code="codex_refresh_missing_access_token",
            relogin_required=True,
        )
    new_refresh = payload.get("refresh_token")
    if not isinstance(new_refresh, str) or not new_refresh.strip():
        new_refresh = refresh_token
    return {
        "access_token": new_access.strip(),
        "refresh_token": new_refresh.strip(),
        "expires_at": _decode_jwt_exp(new_access.strip()),
    }


__all__ = [
    "CodexAuthError",
    "DEFAULT_CODEX_BASE_URL",
    "CODEX_OAUTH_CLIENT_ID",
    "start_device_login",
    "poll_device_login",
    "refresh_tokens",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/runtime/llm/test_codex_oauth.py -v`
Expected: PASS (all 6 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/dev-serv/Project/py-proj/tomo
git add app/runtime/llm/codex_oauth.py tests/unit/runtime/llm/test_codex_oauth.py
git commit -m "feat(llm): add Codex OAuth device-code login and refresh"
```

---

### Task 3: Proactive refresh in `resolve_profile()`

**Files:**
- Modify: `app/models/mixins/llm_profiles.py`
- Test: `tests/unit/models/test_llm_profiles.py`

**Interfaces:**
- Consumes: `codex_oauth.refresh_tokens(refresh_token, transport=...) -> dict` and `codex_oauth.CodexAuthError` from Task 2; `save_subscription_tokens` from Task 1.
- Produces: `resolve_profile()` return dict gains `needs_reauth: bool` key (always present, `False` for `api_key` profiles and fresh `subscription` profiles).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/models/test_llm_profiles.py`:
```python
def test_resolve_profile_refreshes_expiring_subscription_token(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-stale",
            refresh_token="rt-1", expires_at=1.0,  # already expired
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fake_refresh(refresh_token, **kw):
        assert refresh_token == "rt-1"
        return {"access_token": "at-fresh", "refresh_token": "rt-2", "expires_at": 99999999999.0}

    monkeypatch.setattr(
        "app.runtime.llm.codex_oauth.refresh_tokens", fake_refresh
    )
    resolved = store.resolve_llm_profile(None)
    assert resolved["access_token"] == "at-fresh"
    assert resolved["needs_reauth"] is False
    raw = store._conn.execute(
        "SELECT access_token FROM llm_profiles WHERE id=?", (created["id"],)
    ).fetchone()
    assert "at-fresh" not in raw["access_token"]  # persisted encrypted, not plaintext
    assert raw["access_token"].startswith("enc:v1:")


def test_resolve_profile_skips_refresh_when_token_fresh(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-fresh",
            refresh_token="rt-1", expires_at=time.time() + 3600,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fail_refresh(*a, **kw):
        raise AssertionError("refresh should not be called for a fresh token")

    monkeypatch.setattr("app.runtime.llm.codex_oauth.refresh_tokens", fail_refresh)
    resolved = store.resolve_llm_profile(None)
    assert resolved["access_token"] == "at-fresh"
    assert resolved["needs_reauth"] is False


def test_resolve_profile_flags_needs_reauth_on_terminal_refresh_failure(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store
    from app.runtime.llm.codex_oauth import CodexAuthError

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-stale",
            refresh_token="rt-1", expires_at=1.0,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fake_refresh(refresh_token, **kw):
        raise CodexAuthError("expired", code="invalid_grant", relogin_required=True)

    monkeypatch.setattr("app.runtime.llm.codex_oauth.refresh_tokens", fake_refresh)
    resolved = store.resolve_llm_profile(None)
    assert resolved["needs_reauth"] is True


def test_resolve_profile_api_key_profile_has_needs_reauth_false(tmp_path) -> None:
    _rebind(tmp_path)
    store.create_llm_profile({"id": "p", "name": "P", "api_key": "sk-p", "model": "m"})
    store.set_default_llm_profile("p")
    resolved = store.resolve_llm_profile(None)
    assert resolved["needs_reauth"] is False
```

Add `import time` to the top of `tests/unit/models/test_llm_profiles.py` if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/models/test_llm_profiles.py -k reauth -v`
Expected: FAIL with `KeyError: 'needs_reauth'`

- [ ] **Step 3: Implement the proactive-refresh branch**

In `app/models/mixins/llm_profiles.py`, add a module-level constant and helper above `resolve_profile`:
```python
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
```

Update `resolve_profile` to route every return path through `_maybe_refresh_subscription`:
```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/models/test_llm_profiles.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
cd /home/dev-serv/Project/py-proj/tomo
git add app/models/mixins/llm_profiles.py tests/unit/models/test_llm_profiles.py
git commit -m "feat(llm): proactively refresh expiring Codex subscription tokens"
```

---

### Task 4: `codex_responses.py` — Responses-API `LLMClient`

**Files:**
- Create: `app/runtime/llm/codex_responses.py`
- Test: `tests/unit/runtime/llm/test_codex_responses.py`

**Interfaces:**
- Consumes: `LLMResponse`, `ToolCall` (`app/runtime/llm/base.py`); `LLMConfigError`, `LLMRequestError`, `format_llm_error`, `parse_usage`, `_parse_arguments`, `default_llm_timeout_seconds` (`app/runtime/llm/openai_compat.py`); `DEFAULT_CODEX_BASE_URL` (`app/runtime/llm/codex_oauth.py`, Task 2).
- Produces: `CodexResponsesClient(base_url=None, access_token=None, model=None, *, timeout=None, transport=None)` implementing `LLMClient` (`complete`, `stream_complete`, `aclose`).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/runtime/llm/test_codex_responses.py`:
```python
"""CodexResponsesClient HTTP mapping tests via httpx.MockTransport.

No real network calls: a mock transport inspects the outgoing Responses-API
request and returns canned Responses-shaped JSON/SSE so we can verify the
wire mapping (content <-> LLMResponse, tool_calls <-> ToolCall).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.runtime.llm.base import LLMResponse
from app.runtime.llm.codex_responses import (
    CodexResponsesClient,
    _messages_to_responses_input,
    _responses_tools,
)
from app.runtime.llm.openai_compat import LLMConfigError, LLMRequestError

_BASE = "https://chatgpt.com/backend-api/codex"
_TOKEN = "at-test"
_MODEL = "gpt-5-codex"


def _client(transport: httpx.MockTransport) -> CodexResponsesClient:
    return CodexResponsesClient(base_url=_BASE, access_token=_TOKEN, model=_MODEL, transport=transport)


def test_missing_token_raises_config_error() -> None:
    with pytest.raises(LLMConfigError):
        CodexResponsesClient(base_url=_BASE, access_token="", model=_MODEL)


def test_messages_to_responses_input_splits_system_as_instructions() -> None:
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
    ]
    instructions, items = _messages_to_responses_input(messages)
    assert instructions == "You are helpful."
    assert items == [{"role": "user", "content": "hi"}]


def test_messages_to_responses_input_converts_tool_calls_and_results() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run ls"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": '{"cmd":"ls"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "file1\nfile2"},
    ]
    _, items = _messages_to_responses_input(messages)
    assert items[0] == {"role": "user", "content": "run ls"}
    assert items[1] == {
        "type": "function_call", "call_id": "call_1", "name": "bash", "arguments": '{"cmd":"ls"}'
    }
    assert items[2] == {"type": "function_call_output", "call_id": "call_1", "output": "file1\nfile2"}


def test_responses_tools_converts_function_schema() -> None:
    tools = [{"type": "function", "function": {"name": "bash", "description": "run", "parameters": {"type": "object"}}}]
    converted = _responses_tools(tools)
    assert converted == [
        {"type": "function", "name": "bash", "description": "run", "parameters": {"type": "object"}}
    ]


@pytest.mark.asyncio
async def test_complete_returns_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == _MODEL
        assert body["store"] is False
        assert body["instructions"] == "sys"
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": "hello there"}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    client = _client(httpx.MockTransport(handler))
    resp = await client.complete([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    assert isinstance(resp, LLMResponse)
    assert resp.content == "hello there"
    assert resp.tool_calls == []
    assert resp.prompt_tokens == 10
    assert resp.completion_tokens == 5


@pytest.mark.asyncio
async def test_complete_returns_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call", "id": "fc_1", "call_id": "call_1",
                        "name": "bash", "arguments": '{"cmd":"ls"}', "status": "completed",
                    }
                ],
            },
        )

    client = _client(httpx.MockTransport(handler))
    resp = await client.complete(
        [{"role": "user", "content": "run ls"}],
        tools=[{"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}],
    )
    assert resp.content is None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "bash"
    assert resp.tool_calls[0].arguments == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_complete_raises_llm_request_error_on_http_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad token", "code": "invalid_api_key"}})

    client = _client(httpx.MockTransport(handler))
    with pytest.raises(LLMRequestError):
        await client.complete([{"role": "user", "content": "hi"}])


def _sse(events: list[dict]) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


@pytest.mark.asyncio
async def test_stream_complete_yields_deltas_then_done() -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
        {"type": "response.output_text.delta", "delta": "hel"},
        {"type": "response.output_text.delta", "delta": "lo"},
        {
            "type": "response.output_item.done",
            "item": {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                      "content": [{"type": "output_text", "text": "hello"}]},
        },
        {
            "type": "response.completed",
            "response": {"id": "resp_1", "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 2}},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_sse(events), headers={"content-type": "text/event-stream"})

    client = _client(httpx.MockTransport(handler))
    deltas = []
    final = None
    async for ev in client.stream_complete([{"role": "user", "content": "hi"}]):
        if ev["type"] == "delta":
            deltas.append(ev["content"])
        else:
            final = ev["response"]
    assert "".join(deltas) == "hello"
    assert final.content == "hello"
    assert final.prompt_tokens == 3
    assert final.completion_tokens == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/runtime/llm/test_codex_responses.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.runtime.llm.codex_responses'`

- [ ] **Step 3: Implement `app/runtime/llm/codex_responses.py`**

```python
"""Codex/ChatGPT subscription LLM client using the Responses API.

Talks to ``https://chatgpt.com/backend-api/codex`` (or any Responses-API
endpoint) via ``openai.AsyncOpenAI().responses.create(...)`` instead of
chat/completions — the wire format the ChatGPT-subscription Codex backend
actually accepts an OAuth access token against.

Trimmed port of ``tmp/hermes-agent``'s ``agent/codex_responses_adapter.py``
+ ``agent/codex_runtime.py``: only the message/tool conversion and response
normalization needed for a single backend (Codex) — no cross-issuer
encrypted-reasoning replay, no Harmony tool-call-leak recovery, no xAI
answer salvage (see the design spec's "Out of scope").
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx
import openai

from app.runtime.llm.base import LLMResponse, ToolCall
from app.runtime.llm.codex_oauth import DEFAULT_CODEX_BASE_URL
from app.runtime.llm.openai_compat import (
    LLMConfigError,
    LLMRequestError,
    _parse_arguments,
    default_llm_timeout_seconds,
    format_llm_error,
    parse_usage,
)

_logger = logging.getLogger(__name__)


def _flatten_content(content: Any) -> str:
    """Best-effort plain-text flatten of a chat-message ``content`` field."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Convert tomo's chat-style ``messages`` to ``(instructions, input_items)``.

    System-role messages become the Responses ``instructions`` string
    (joined, in order). Everything else becomes an ``input`` item:
    plain user/assistant text, ``function_call`` for assistant tool calls,
    ``function_call_output`` for tool-role results.
    """
    instructions_parts: list[str] = []
    items: list[dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")

        if role == "system":
            text = _flatten_content(msg.get("content"))
            if text.strip():
                instructions_parts.append(text)
            continue

        if role == "tool":
            call_id = msg.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id.strip():
                continue
            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": _flatten_content(msg.get("content")),
            })
            continue

        if role not in {"user", "assistant"}:
            continue

        text = _flatten_content(msg.get("content"))
        tool_calls = msg.get("tool_calls")
        if role == "assistant" and isinstance(tool_calls, list) and tool_calls:
            if text.strip():
                items.append({"role": "assistant", "content": text})
            for idx, tc in enumerate(tool_calls):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                if not isinstance(fn, dict):
                    continue
                name = fn.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                arguments = fn.get("arguments", "{}")
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments)
                elif not isinstance(arguments, str):
                    arguments = str(arguments)
                call_id = tc.get("id") or tc.get("call_id") or f"call_{idx}"
                items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments or "{}",
                })
            continue

        items.append({"role": role, "content": text})

    return "\n\n".join(instructions_parts), items


def _responses_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Convert chat-completions tool schemas to Responses function-tool schemas."""
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for item in tools:
        fn = item.get("function", {}) if isinstance(item, dict) else {}
        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        converted.append({
            "type": "function",
            "name": name,
            "description": fn.get("description", "") or "",
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted or None


def _extract_message_text(item: Any) -> str:
    content = getattr(item, "content", None)
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        ptype = getattr(part, "type", None)
        if ptype not in {"output_text", "text"}:
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            chunks.append(text)
    return "".join(chunks)


def _tool_call_from_item(item: Any) -> ToolCall | None:
    if getattr(item, "type", None) != "function_call":
        return None
    name = getattr(item, "name", "") or ""
    arguments_raw = getattr(item, "arguments", "{}")
    call_id = getattr(item, "call_id", None) or getattr(item, "id", None) or ""
    return ToolCall(id=call_id, name=name, arguments=_parse_arguments(arguments_raw))


def _normalize_response(resp: Any) -> LLMResponse:
    output = getattr(resp, "output", None) or []
    content_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            text = _extract_message_text(item)
            if text:
                content_parts.append(text)
        elif item_type == "function_call":
            tc = _tool_call_from_item(item)
            if tc is not None:
                tool_calls.append(tc)

    text = "\n".join(p for p in content_parts if p).strip() or None
    if text is None and not tool_calls:
        out_text = getattr(resp, "output_text", None)
        if isinstance(out_text, str) and out_text.strip():
            text = out_text.strip()
    if text is None and not tool_calls:
        raise LLMRequestError("LLM request failed: Responses API returned no output")

    prompt_tok, completion_tok = parse_usage(getattr(resp, "usage", None))
    return LLMResponse(
        content=text, tool_calls=tool_calls, prompt_tokens=prompt_tok, completion_tokens=completion_tok
    )


class CodexResponsesClient:
    """Async Responses-API client for Codex/ChatGPT-subscription profiles.

    Implements the same duck-typed contract as
    :class:`~app.runtime.llm.openai_compat.OpenAICompatClient`
    (``complete``, ``stream_complete``) but wire-encodes against
    ``client.responses.create`` instead of ``chat.completions.create``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        access_token: str | None = None,
        model: str | None = None,
        *,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        resolved_token = (access_token or "").strip()
        if not resolved_token:
            raise LLMConfigError(
                "ChatGPT sign-in required in System → Models (subscription profile has no token)."
            )
        self._base_url = (base_url or DEFAULT_CODEX_BASE_URL).rstrip("/")
        self._model = model or "gpt-5-codex"
        self._timeout = (
            float(timeout) if timeout is not None else default_llm_timeout_seconds()
        )

        http_client = None
        if transport is not None:
            http_client = httpx.AsyncClient(transport=transport, timeout=self._timeout)

        self._client = openai.AsyncOpenAI(
            base_url=self._base_url,
            api_key=resolved_token,
            timeout=self._timeout,
            max_retries=0 if transport is not None else 2,
            http_client=http_client,
        )

    def _payload(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        instructions, input_items = _messages_to_responses_input(messages)
        payload: dict[str, Any] = {
            "model": self._model,
            "instructions": instructions,
            "input": input_items,
            "store": False,
        }
        responses_tools = _responses_tools(tools)
        if responses_tools:
            payload["tools"] = responses_tools
        return payload

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        payload = self._payload(messages, tools)
        try:
            resp = await self._client.responses.create(**payload)
        except LLMRequestError:
            raise
        except Exception as exc:
            _logger.warning(
                "Codex Responses complete failed model=%s: %s", self._model, format_llm_error(exc)
            )
            raise LLMRequestError(format_llm_error(exc)) from exc
        return _normalize_response(resp)

    async def stream_complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a Responses-API turn; yield text deltas then a final response.

        Never reads ``response.completed.response.output`` for content —
        only ``response.output_text.delta`` (text) and
        ``response.output_item.done`` (tool calls, and a message-text
        fallback when no deltas were streamed) are used to assemble the
        result, plus ``response.completed.response.usage`` for token counts.
        """
        payload = dict(self._payload(messages, tools))
        payload["stream"] = True

        content_parts: list[str] = []
        output_items: list[Any] = []
        prompt_tok = 0
        completion_tok = 0

        try:
            stream = await self._client.responses.create(**payload)
            async for event in stream:
                etype = getattr(event, "type", "") or ""
                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    if delta:
                        content_parts.append(delta)
                        yield {"type": "delta", "content": delta}
                elif etype == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is not None:
                        output_items.append(item)
                elif etype == "response.completed":
                    resp_obj = getattr(event, "response", None)
                    usage = getattr(resp_obj, "usage", None) if resp_obj is not None else None
                    if usage is not None:
                        prompt_tok, completion_tok = parse_usage(usage)
                elif etype == "response.failed":
                    resp_obj = getattr(event, "response", None)
                    err = getattr(resp_obj, "error", None) if resp_obj is not None else None
                    message = getattr(err, "message", None) if err is not None else None
                    raise LLMRequestError(
                        f"LLM request failed: {message or 'Codex Responses stream failed'}"
                    )
        except LLMRequestError:
            raise
        except Exception as exc:
            _logger.warning(
                "Codex Responses stream failed model=%s deltas=%d: %s",
                self._model, len(content_parts), format_llm_error(exc),
            )
            raise LLMRequestError(format_llm_error(exc)) from exc

        tool_calls = [tc for tc in (_tool_call_from_item(it) for it in output_items) if tc is not None]
        text = "".join(content_parts) if content_parts else None
        if text is None and not tool_calls:
            # No deltas streamed (e.g. the whole message arrived in one
            # output_item.done) — fall back to the completed message item.
            for item in output_items:
                if getattr(item, "type", None) == "message":
                    fallback = _extract_message_text(item)
                    if fallback:
                        text = fallback
                        break
        if text is None and not tool_calls:
            raise LLMRequestError(
                "LLM request failed: stream ended with no content and no tool calls"
            )
        yield {
            "type": "done",
            "response": LLMResponse(
                content=text, tool_calls=tool_calls,
                prompt_tokens=prompt_tok, completion_tokens=completion_tok,
            ),
        }

    async def aclose(self) -> None:
        await self._client.close()


__all__ = ["CodexResponsesClient"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/runtime/llm/test_codex_responses.py -v`
Expected: PASS (all tests). If `test_stream_complete_yields_deltas_then_done` fails on SSE parsing, check whether the installed `openai` SDK version expects a trailing `data: [DONE]\n\n` sentinel on Responses streams (some proxies emit one) — if so add `events.append({"type": "done"})` is wrong; instead append a raw `b"data: [DONE]\n\n"` suffix to `_sse()`'s output and re-run.

- [ ] **Step 5: Commit**

```bash
cd /home/dev-serv/Project/py-proj/tomo
git add app/runtime/llm/codex_responses.py tests/unit/runtime/llm/test_codex_responses.py
git commit -m "feat(llm): add Responses-API client for Codex subscription profiles"
```

---

### Task 5: Wire `get_llm()` to branch on `auth_mode`

**Files:**
- Modify: `app/runtime/llm/__init__.py`
- Test: `tests/unit/runtime/llm/test_get_llm_profiles.py`

**Interfaces:**
- Consumes: `profile["auth_mode"]`, `profile["needs_reauth"]`, `profile["access_token"]` (Tasks 1+3), `CodexResponsesClient` (Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/runtime/llm/test_get_llm_profiles.py`:
```python
def test_get_llm_returns_codex_client_for_subscription_profile(tmp_path) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store
    from app.runtime.llm.codex_responses import CodexResponsesClient

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-1",
            refresh_token="rt-1", expires_at=99999999999.0,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])
    client = get_llm()
    assert isinstance(client, CodexResponsesClient)


def test_get_llm_raises_needs_reauth_message(tmp_path, monkeypatch) -> None:
    _rebind(tmp_path)
    from app.models.mixins import llm_profiles as llm_profiles_store
    from app.runtime.llm.codex_oauth import CodexAuthError

    with store._lock:
        created = llm_profiles_store.create_subscription_profile(
            store._conn, provider="openai-codex", access_token="at-stale",
            refresh_token="rt-1", expires_at=1.0,
            name="ChatGPT (Codex)", model="gpt-5-codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
        llm_profiles_store.set_default_model_id(store._conn, created["id"])

    def fake_refresh(*a, **kw):
        raise CodexAuthError("expired", code="invalid_grant", relogin_required=True)

    monkeypatch.setattr("app.runtime.llm.codex_oauth.refresh_tokens", fake_refresh)
    with pytest.raises(LLMConfigError, match="ChatGPT sign-in expired"):
        get_llm()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/runtime/llm/test_get_llm_profiles.py -k subscription -v`
Expected: FAIL — `get_llm` still returns `OpenAICompatClient` and raises `LLMConfigError` for the empty `api_key`.

- [ ] **Step 3: Implement the branch**

Replace `get_llm` in `app/runtime/llm/__init__.py`:
```python
from app.runtime.llm.base import LLMClient, LLMResponse, ToolCall
from app.runtime.llm.codex_responses import CodexResponsesClient
from app.runtime.llm.mock import MockLLMClient
from app.runtime.llm.openai_compat import (
    LLMConfigError,
    LLMRequestError,
    OpenAICompatClient,
    default_llm_timeout_seconds,
    format_llm_error,
)


def get_llm(
    agent_id: str | None = None, reasoning_effort: str | None = None
) -> LLMClient:
    """Return an LLM client resolved from LLM profiles.

    Resolution (Alpha §2.2): the agent's assigned profile (if set and enabled)
    → ``default_model_id`` → the first enabled profile. Raises
    :class:`LLMConfigError` when no usable profile exists, the resolved
    profile has no API key (``auth_mode='api_key'``), or a subscription
    profile's ChatGPT sign-in has expired and could not be refreshed.
    """
    from app.services import store
    from app.models.mixins.llm_profiles import effective_reasoning_effort

    profile = store.resolve_llm_profile(agent_id)
    if not profile:
        raise LLMConfigError("Configure a model profile in System → Models")
    if profile.get("needs_reauth"):
        raise LLMConfigError(
            "ChatGPT sign-in expired — reconnect in System → Models"
        )
    if profile.get("auth_mode") == "subscription":
        return CodexResponsesClient(
            base_url=profile.get("base_url") or "",
            access_token=profile.get("access_token") or "",
            model=profile.get("model") or "gpt-5-codex",
            timeout=default_llm_timeout_seconds(),
        )
    base_url = (profile.get("base_url") or "").strip() or "https://api.openai.com/v1"
    model = (profile.get("model") or "").strip() or "gpt-4o-mini"
    effective_effort = effective_reasoning_effort(profile, reasoning_effort)
    # OpenAICompatClient raises LLMConfigError when the API key is empty.
    return OpenAICompatClient(
        base_url=base_url,
        api_key=profile.get("api_key") or "",
        model=model,
        reasoning_effort=effective_effort,
        timeout=default_llm_timeout_seconds(),
    )


__all__ = [
    "LLMClient",
    "LLMResponse",
    "ToolCall",
    "MockLLMClient",
    "OpenAICompatClient",
    "CodexResponsesClient",
    "LLMConfigError",
    "LLMRequestError",
    "format_llm_error",
    "default_llm_timeout_seconds",
    "get_llm",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/unit/runtime/llm/test_get_llm_profiles.py -v`
Expected: PASS (all tests, including pre-existing api_key-path tests)

- [ ] **Step 5: Commit**

```bash
cd /home/dev-serv/Project/py-proj/tomo
git add app/runtime/llm/__init__.py tests/unit/runtime/llm/test_get_llm_profiles.py
git commit -m "feat(llm): route subscription profiles through CodexResponsesClient"
```

---

### Task 6: Web API — device-code login endpoints

**Files:**
- Modify: `app/schemas/models.py`
- Modify: `app/api/platform.py`
- Test: `tests/integration/test_llm_profiles_api.py`

**Interfaces:**
- Consumes: `store.find_subscription_llm_profile`, `store.create_subscription_llm_profile`, `store.save_subscription_llm_tokens` (Task 1); `codex_oauth.start_device_login`, `codex_oauth.poll_device_login`, `codex_oauth.CodexAuthError` (Task 2).
- Produces: `POST /api/llm-profiles/codex-login/start` → `{user_code, device_auth_id, verification_url, interval}`. `POST /api/llm-profiles/codex-login/poll` → `{status: "pending"}` or the created/updated public profile (`{status: "ok", profile: {...}}`).

- [ ] **Step 1: Write the failing tests**

Look at the top of `tests/integration/test_llm_profiles_api.py` first to match its client-setup fixture pattern, then append:
```python
def test_codex_login_start_returns_device_code(client, monkeypatch):
    def fake_start(**kw):
        return {
            "user_code": "ABCD-1234", "device_auth_id": "dev-1",
            "verification_url": "https://auth.openai.com/codex/device", "interval": 5,
        }
    monkeypatch.setattr("app.api.platform.codex_oauth.start_device_login", fake_start)
    resp = client.post("/api/llm-profiles/codex-login/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_code"] == "ABCD-1234"
    assert body["device_auth_id"] == "dev-1"


def test_codex_login_poll_pending(client, monkeypatch):
    monkeypatch.setattr("app.api.platform.codex_oauth.poll_device_login", lambda *a, **kw: None)
    resp = client.post(
        "/api/llm-profiles/codex-login/poll",
        json={"device_auth_id": "dev-1", "user_code": "ABCD-1234"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "pending"}


def test_codex_login_poll_success_creates_profile(client, monkeypatch):
    def fake_poll(device_auth_id, user_code, **kw):
        return {"access_token": "at-1", "refresh_token": "rt-1", "expires_at": 99999999999.0}
    monkeypatch.setattr("app.api.platform.codex_oauth.poll_device_login", fake_poll)
    resp = client.post(
        "/api/llm-profiles/codex-login/poll",
        json={"device_auth_id": "dev-1", "user_code": "ABCD-1234"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["profile"]["auth_mode"] == "subscription"
    assert body["profile"]["access_token_set"] is True
    assert "access_token" not in body["profile"]

    # Second login updates the same profile rather than creating a duplicate.
    resp2 = client.post(
        "/api/llm-profiles/codex-login/poll",
        json={"device_auth_id": "dev-2", "user_code": "EFGH-5678"},
    )
    assert resp2.json()["profile"]["id"] == body["profile"]["id"]


def test_codex_login_poll_terminal_failure_returns_400(client, monkeypatch):
    from app.runtime.llm.codex_oauth import CodexAuthError

    def fake_poll(*a, **kw):
        raise CodexAuthError("boom", code="device_code_poll_error")
    monkeypatch.setattr("app.api.platform.codex_oauth.poll_device_login", fake_poll)
    resp = client.post(
        "/api/llm-profiles/codex-login/poll",
        json={"device_auth_id": "dev-1", "user_code": "ABCD-1234"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/integration/test_llm_profiles_api.py -k codex_login -v`
Expected: FAIL with 404 (routes don't exist yet)

- [ ] **Step 3: Add the request schema**

In `app/schemas/models.py`, after `LLMProfileUpdate`:
```python
class CodexLoginPoll(BaseModel):
    """Poll body for the Codex device-code login flow."""

    device_auth_id: str = Field(min_length=1, max_length=200)
    user_code: str = Field(min_length=1, max_length=64)
```
Add `CodexLoginPoll` to `app/schemas/__init__.py`'s exports (match how `LLMProfileCreate` is exported there).

- [ ] **Step 4: Implement the endpoints**

In `app/api/platform.py`, add the import near the other `app.schemas` import:
```python
from app.runtime.llm import codex_oauth
from app.schemas import (
    CodexLoginPoll,
    KnowledgeEntryCreate,
    ...  # existing entries unchanged
)
```
Add after `set_default_llm_profile`:
```python
@router.post("/llm-profiles/codex-login/start")
async def codex_login_start(_: AuthDep):
    try:
        return codex_oauth.start_device_login()
    except codex_oauth.CodexAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/llm-profiles/codex-login/poll")
async def codex_login_poll(body: CodexLoginPoll, _: AuthDep):
    try:
        tokens = codex_oauth.poll_device_login(body.device_auth_id, body.user_code)
    except codex_oauth.CodexAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if tokens is None:
        return {"status": "pending"}

    existing = store.find_subscription_llm_profile("openai-codex")
    if existing:
        store.save_subscription_llm_tokens(
            existing["id"],
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=tokens["expires_at"],
        )
        profile = store.get_llm_profile(existing["id"])
    else:
        profile = store.create_subscription_llm_profile(
            provider="openai-codex",
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=tokens["expires_at"],
            name="ChatGPT (Codex)",
            model="gpt-5-codex",
            base_url=codex_oauth.DEFAULT_CODEX_BASE_URL,
        )
        if not store.get_default_llm_profile_id():
            store.set_default_llm_profile(profile["id"])
    return {"status": "ok", "profile": profile}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest tests/integration/test_llm_profiles_api.py -v`
Expected: PASS (all tests, including pre-existing profile CRUD tests)

- [ ] **Step 6: Commit**

```bash
cd /home/dev-serv/Project/py-proj/tomo
git add app/schemas/models.py app/schemas/__init__.py app/api/platform.py tests/integration/test_llm_profiles_api.py
git commit -m "feat(api): add Codex device-code login endpoints"
```

---

### Task 7: Settings UI — "Sign in with ChatGPT"

**Files:**
- Modify: `app/templates/partials/settings/models.html`
- Modify: `app/static/js/system.js`

**Interfaces:**
- Consumes: `POST /api/llm-profiles/codex-login/start`, `POST /api/llm-profiles/codex-login/poll` (Task 6); existing `Tomo.api`, `Tomo.toast`, `Tomo.escapeHtml` helpers already used elsewhere in `system.js`.

- [ ] **Step 1: Add the button and status dock to the template**

In `app/templates/partials/settings/models.html`, change the toolbar:
```html
  <div class="machine-toolbar">
    <p class="machine-toolbar-label">LLM profiles</p>
    <button class="btn ghost sm" type="button" id="codexLoginBtn">Sign in with ChatGPT</button>
    <button class="btn primary sm" type="button" id="addProfileBtn">New profile</button>
  </div>
```
Add a status dock right after the closing `</div>` of `profileList` (before `profileFormCard`):
```html
  <div class="machine-dock hidden" id="codexLoginCard" role="dialog" aria-modal="true" aria-labelledby="codexLoginTitle">
    <div class="machine-sheet-head">
      <h3 id="codexLoginTitle">Sign in with ChatGPT</h3>
      <button class="btn ghost sm" type="button" data-dock-close>Close</button>
    </div>
    <p class="machine-note">Open the link below, enter the code, then come back here — this waits for you.</p>
    <p class="machine-field-hint">Code: <strong id="codexLoginCode" class="mono"></strong></p>
    <p class="machine-field-hint"><a id="codexLoginLink" href="#" target="_blank" rel="noopener">Open sign-in page</a></p>
    <p class="machine-field-hint" id="codexLoginStatus">Waiting for sign-in…</p>
  </div>
```

- [ ] **Step 2: Wire the button in `system.js`**

In `app/static/js/system.js`, right after the `loadProfiles();` call (the line that follows the `saveProf` click handler block), add:
```javascript
  // ---- ChatGPT/Codex subscription login ----
  var codexBtn = document.getElementById('codexLoginBtn');
  var codexCard = document.getElementById('codexLoginCard');
  var codexCode = document.getElementById('codexLoginCode');
  var codexLink = document.getElementById('codexLoginLink');
  var codexStatus = document.getElementById('codexLoginStatus');
  var codexPollTimer = null;

  function stopCodexPoll() {
    if (codexPollTimer) { clearInterval(codexPollTimer); codexPollTimer = null; }
  }

  function closeCodexCard() {
    stopCodexPoll();
    if (codexCard) codexCard.classList.add('hidden');
  }

  async function pollCodexLogin(deviceAuthId, userCode) {
    try {
      var d = await Tomo.api('/api/llm-profiles/codex-login/poll', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_auth_id: deviceAuthId, user_code: userCode }),
      });
      if (d && d.status === 'ok') {
        stopCodexPoll();
        if (codexStatus) codexStatus.textContent = 'Signed in as ' + (d.profile.name || 'ChatGPT (Codex)') + '.';
        Tomo.toast('Signed in with ChatGPT', 'ok');
        loadProfiles();
        setTimeout(closeCodexCard, 1200);
      }
      // status === 'pending' -> keep polling silently.
    } catch (er) {
      stopCodexPoll();
      if (codexStatus) codexStatus.textContent = (er && er.message) || 'Sign-in failed.';
      Tomo.toast((er && er.message) || 'ChatGPT sign-in failed', 'err');
    }
  }

  if (codexBtn) {
    codexBtn.addEventListener('click', async function () {
      if (codexCard) codexCard.classList.remove('hidden');
      if (codexCode) codexCode.textContent = '…';
      if (codexStatus) codexStatus.textContent = 'Requesting a sign-in code…';
      try {
        var start = await Tomo.api('/api/llm-profiles/codex-login/start', { method: 'POST' });
        if (!start) return;
        if (codexCode) codexCode.textContent = start.user_code;
        if (codexLink) codexLink.href = start.verification_url;
        if (codexStatus) codexStatus.textContent = 'Waiting for sign-in…';
        stopCodexPoll();
        codexPollTimer = setInterval(function () {
          pollCodexLogin(start.device_auth_id, start.user_code);
        }, Math.max(3, start.interval || 5) * 1000);
      } catch (er) {
        if (codexStatus) codexStatus.textContent = (er && er.message) || 'Could not start sign-in.';
        Tomo.toast((er && er.message) || 'Could not start ChatGPT sign-in', 'err');
      }
    });
  }

  var codexCloseBtns = codexCard ? codexCard.querySelectorAll('[data-dock-close]') : [];
  codexCloseBtns.forEach(function (btn) { btn.addEventListener('click', closeCodexCard); });
```

- [ ] **Step 3: Manual verification**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/python -m app.web` (or however the dev server is normally started — check `README.md`/`app/web/__init__.py` for the exact entry point if this differs), open **System → Models**, click "Sign in with ChatGPT", confirm the dock opens with a code and a working link, and confirm clicking an existing profile row still opens the edit form unaffected (the new button must not be caught by the `listEl` row-click delegation — it lives in the toolbar, outside `#profileList`, so it won't be).

- [ ] **Step 4: Commit**

```bash
cd /home/dev-serv/Project/py-proj/tomo
git add app/templates/partials/settings/models.html app/static/js/system.js
git commit -m "feat(ui): add ChatGPT sign-in button to LLM profiles settings"
```

---

### Task 8: Full suite, review, and push

**Files:** none new — verification only.

- [ ] **Step 1: Run the full test suite**

Run: `cd /home/dev-serv/Project/py-proj/tomo && .venv/bin/pytest -q`
Expected: PASS, zero failures, zero new warnings from the touched files.

- [ ] **Step 2: Run lint/type checks if configured**

Run: `cd /home/dev-serv/Project/py-proj/tomo && ruff check app/runtime/llm/codex_oauth.py app/runtime/llm/codex_responses.py app/runtime/llm/__init__.py app/models/mixins/llm_profiles.py app/models/schema.py app/api/platform.py app/schemas/models.py`
Fix any findings.

- [ ] **Step 3: Self-review the diff**

Run: `cd /home/dev-serv/Project/py-proj/tomo && git diff main --stat` and read through `git diff main` for the whole feature. Check specifically:
- No raw `access_token`/`refresh_token` value ever appears in a public API/HTML response (only `*_set` booleans).
- `resolve_profile()`'s subscription branch never raises on a transient (rate-limit) refresh failure — it must keep serving the stale-but-still-valid token.
- `get_llm()`'s `needs_reauth` check runs before the `auth_mode` branch, so an expired subscription profile never reaches `CodexResponsesClient` with a stale token.

- [ ] **Step 4: Push to main**

```bash
cd /home/dev-serv/Project/py-proj/tomo
git push origin main
```
