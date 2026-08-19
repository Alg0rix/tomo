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


def test_start_device_login_returns_code() -> None:
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
