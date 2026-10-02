"""Codex model discovery — live probe (mocked) + curated fallback."""

from __future__ import annotations

import base64
import json

import httpx2

from app.runtime.llm.codex_models import (
    DEFAULT_CODEX_MODELS,
    _extract_chatgpt_account_id,
    list_codex_models,
)


def _fake_jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.sig"




def test_list_codex_models_uses_live_api_sorted_by_priority() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer at-1"
        return httpx2.Response(
            200,
            json={
                "models": [
                    {"slug": "gpt-5.3-codex", "priority": 20},
                    {"slug": "gpt-5.5", "priority": 0},
                    {"slug": "gpt-5-hidden", "priority": 1, "visibility": "hidden"},
                ]
            },
        )

    models = list_codex_models("at-1", transport=httpx2.MockTransport(handler))
    assert models == ["gpt-5.5", "gpt-5.3-codex"]


def test_list_codex_models_falls_back_on_http_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500, json={})

    models = list_codex_models("at-1", transport=httpx2.MockTransport(handler))
    assert models == DEFAULT_CODEX_MODELS




def test_fetch_sends_chatgpt_account_id_header_from_jwt_claim() -> None:
    captured = {}
    token = _fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-123"}})

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["headers"] = dict(request.headers)
        return httpx2.Response(200, json={"models": [{"slug": "gpt-5.6-sol", "priority": 0}]})

    models = list_codex_models(token, transport=httpx2.MockTransport(handler))
    assert captured["headers"].get("chatgpt-account-id") == "acct-123"
    assert models == ["gpt-5.6-sol"]


