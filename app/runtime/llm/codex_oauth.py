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

    def __init__(
        self, message: str, *, code: str = "codex_auth_error", relogin_required: bool = False
    ) -> None:
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
    authorization_code: str,
    code_verifier: str,
    *,
    timeout: float,
    transport: httpx.BaseTransport | None,
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
