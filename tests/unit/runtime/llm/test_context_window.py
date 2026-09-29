"""Tests for the context window resolver: API fetch, cache, known table, fallbacks.

All tests are pure (no real network). Mock transport for OpenAI-compat
client and monkeypatched store for seed lookups.
"""
from __future__ import annotations

import httpx

from app.runtime.llm.openai_compat import (
    OpenAICompatClient,
    extract_context_window,
    _match_model_context,
)
from app.runtime.llm.context_window import (
    _DEFAULT,
    _catalog_context,
    _lookup_known,
    clear_context_window_cache,
    resolve_context_window,
    resolve_context_window_sync,
)

_BASE = "https://example.test/v1"
_KEY = "sk-test"


# ── extract_context_window ────────────────────────────────────────


def test_extract_top_level_context_window() -> None:
    assert extract_context_window({"context_window": 32768}) == 32768


def test_extract_max_model_len() -> None:
    assert extract_context_window({"max_model_len": 4096}) == 4096


def test_extract_from_nested_model_info() -> None:
    obj = {"model_info": {"context_length": 8192}}
    assert extract_context_window(obj) == 8192


def test_extract_from_nested_meta() -> None:
    obj = {"meta": {"n_ctx": 4096}}
    assert extract_context_window(obj) == 4096


def test_extract_string_value() -> None:
    obj = {"num_ctx": "16384"}
    assert extract_context_window(obj) == 16384


def test_extract_rejects_too_small() -> None:
    assert extract_context_window({"context_window": 100}) is None


def test_extract_rejects_too_large() -> None:
    assert extract_context_window({"context_window": 999_999_999}) is None


def test_extract_none_for_no_fields() -> None:
    assert extract_context_window({"name": "gpt-4o", "id": "gpt-4o"}) is None


def test_extract_none_for_non_dict() -> None:
    assert extract_context_window("not a dict") is None  # type: ignore[arg-type]


# ── _match_model_context ──────────────────────────────────────────


def test_match_exact_id() -> None:
    items = [
        {"id": "gpt-4o", "context_window": 128000},
        {"id": "gpt-4o-mini", "context_window": 64000},
    ]
    assert _match_model_context(items, "gpt-4o-mini") == 64000


def test_match_slash_suffix() -> None:
    items = [
        {"id": "org/gpt-4o", "context_window": 128000},
    ]
    assert _match_model_context(items, "gpt-4o") == 128000


def test_match_colon_suffix() -> None:
    items = [
        {"id": "ollama:gpt-4o", "max_model_len": 8192},
    ]
    assert _match_model_context(items, "gpt-4o") == 8192


def test_match_prefix_longest_wins() -> None:
    items = [
        {"id": "gpt-4o", "context_window": 128000},
        {"id": "gpt-4o-2024-08-06", "context_window": 131072},
    ]
    assert _match_model_context(items, "gpt-4o-2024-08-06") == 131072


def test_match_no_context_field_returns_none() -> None:
    items = [{"id": "gpt-4o", "name": "gpt-4o"}]
    assert _match_model_context(items, "gpt-4o") is None


def test_match_skips_non_dict_items() -> None:
    items = ["bad", {"id": "gpt-4o", "context_window": 128000}]
    assert _match_model_context(items, "gpt-4o") == 128000


def test_match_empty_list_returns_none() -> None:
    assert _match_model_context([], "gpt-4o") is None


# ── fetch_model_context_window (via MockTransport) ────────────────


def _make_client(handler, *, model="vllm-model", base_url=_BASE, api_key=_KEY):
    transport = httpx.MockTransport(handler)
    return OpenAICompatClient(
        base_url=base_url, api_key=api_key, model=model, transport=transport
    )


async def test_fetch_context_from_models_list() -> None:
    """Provider returns context_length in /models list."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [
                    {"id": "vllm-model", "max_model_len": 16384},
                    {"id": "other", "context_window": 4096},
                ]
            })
        return httpx.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 16384
    finally:
        await client.aclose()


async def test_fetch_context_from_nested_model_info() -> None:
    """Context field nested inside model_info."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [
                    {"id": "vllm-model", "model_info": {"context_length": 32768}},
                ]
            })
        return httpx.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 32768
    finally:
        await client.aclose()


async def test_fetch_context_fallback_to_single_model_endpoint() -> None:
    """List has no match; falls back to GET /models/{model}."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        if path.endswith("/models/vllm-model"):
            return httpx.Response(200, json={"id": "vllm-model", "context_window": 65536})
        return httpx.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 65536
    finally:
        await client.aclose()


async def test_fetch_context_returns_none_on_network_error() -> None:
    """Network failure → None, no exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx is None
    finally:
        await client.aclose()


async def test_fetch_context_returns_none_when_no_field() -> None:
    """Model exists but has no context field → None."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": "vllm-model", "owned_by": "vllm"}]
            })
        return httpx.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx is None
    finally:
        await client.aclose()


async def test_fetch_context_list_format_without_data_key() -> None:
    """Some providers return a bare list instead of {data: [...]}."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=[
                {"id": "vllm-model", "context_length": 8192},
            ])
        return httpx.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 8192
    finally:
        await client.aclose()


# ── _lookup_known ─────────────────────────────────────────────────


def test_known_match_gpt4o() -> None:
    assert _lookup_known("gpt-4o") == 128_000


def test_known_match_prefix() -> None:
    assert _lookup_known("gpt-4o-2024-08-06") == 128_000


def test_known_no_match() -> None:
    assert _lookup_known("unknown-llm") is None


def test_known_match_claude() -> None:
    assert _lookup_known("claude-3-5-sonnet") == 200_000


def test_known_matches_proxy_alias_for_deepseek_v4() -> None:
    assert _lookup_known("cline-pass/deepseek-v4-flash") == 1_000_000


def test_catalog_prefers_original_provider_and_rejects_ambiguous_unknown() -> None:
    catalog = {
        "deepseek": {"deepseek-v4-flash": 1_000_000},
        "proxy": {"deepseek-v4-flash": 128_000, "custom-model": 32_000},
        "other": {"custom-model": 64_000},
    }
    assert _catalog_context(catalog, "cline-pass/deepseek-v4-flash") == 1_000_000
    assert _catalog_context(catalog, "custom-model") is None


# ── resolve_context_window_sync ───────────────────────────────────


def test_sync_uses_known_table(monkeypatch) -> None:
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "gpt-4o"})
    monkeypatch.setattr(store, "list_models", lambda: [])
    assert resolve_context_window_sync("main") == 128_000


def test_sync_falls_back_to_seed(monkeypatch) -> None:
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "custom-model"})
    monkeypatch.setattr(store, "list_models", lambda: [{"id": "custom-model", "context": 65536}])
    assert resolve_context_window_sync("main") == 65536


def test_sync_returns_default_for_unknown(monkeypatch) -> None:
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "unknown"})
    monkeypatch.setattr(store, "list_models", lambda: [])
    assert resolve_context_window_sync("main") == _DEFAULT


def test_sync_returns_default_when_no_profile(monkeypatch) -> None:
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: None)
    assert resolve_context_window_sync("main") == _DEFAULT


def test_sync_codex_uses_route_limit(monkeypatch) -> None:
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {
        "auth_mode": "subscription", "model": "gpt-5.6-sol",
    })
    assert resolve_context_window_sync("main") == 272_000


# ── resolve_context_window (async) caching ────────────────────────


async def test_async_caches_result(monkeypatch) -> None:
    """Subsequent calls hit cache (no second API call)."""
    from app.services import store

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": "test-m", "context_window": 99999}]
            })
        return httpx.Response(404)

    clear_context_window_cache()
    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {
        "model": "test-m", "base_url": _BASE, "api_key": _KEY,
    })

    # Monkeypatch OpenAICompatClient to use our mock transport
    orig_init = OpenAICompatClient.__init__

    def patched_init(self, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        orig_init(self, **kwargs)

    monkeypatch.setattr(OpenAICompatClient, "__init__", patched_init)

    r1 = await resolve_context_window("main")
    r2 = await resolve_context_window("main")
    assert r1 == 99999
    assert r2 == 99999
    # Only one actual HTTP call (second hit cache)
    assert call_count == 1

    clear_context_window_cache()


async def test_async_known_table_fallback(monkeypatch) -> None:
    """API returns no context → known table match."""
    from app.services import store

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={
                "data": [{"id": "gpt-4o", "owned_by": "openai"}]
            })
        return httpx.Response(404)

    clear_context_window_cache()
    import app.runtime.llm.context_window as context_mod

    async def no_catalog(_model_id):
        return None

    monkeypatch.setattr(context_mod, "_fetch_public_catalog_context", no_catalog)
    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {
        "model": "gpt-4o", "base_url": _BASE, "api_key": _KEY,
    })

    orig_init = OpenAICompatClient.__init__

    def patched_init(self, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        orig_init(self, **kwargs)

    monkeypatch.setattr(OpenAICompatClient, "__init__", patched_init)

    result = await resolve_context_window("main")
    assert result == 128_000

    clear_context_window_cache()

async def test_codex_subscription_uses_its_own_catalog(monkeypatch) -> None:
    """Codex's 272K route limit wins over the direct API's larger window."""
    from app.services import store
    import app.runtime.llm.context_window as context_mod

    async def fake_codex(profile, model_id):
        assert profile["auth_mode"] == "subscription"
        assert model_id == "gpt-5.6-sol"
        return 272_000

    async def unexpected_public(_model_id):
        raise AssertionError("Codex must not use the public API catalog")

    clear_context_window_cache()
    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {
        "auth_mode": "subscription", "model": "gpt-5.6-sol",
        "base_url": "https://chatgpt.com/backend-api/codex", "access_token": "token",
    })
    monkeypatch.setattr(context_mod, "_fetch_codex_context", fake_codex)
    monkeypatch.setattr(context_mod, "_fetch_public_catalog_context", unexpected_public)
    assert await resolve_context_window("main") == 272_000
    clear_context_window_cache()


async def test_codex_catalog_reads_account_scoped_context(monkeypatch) -> None:
    import base64
    import json
    import app.runtime.llm.context_window as context_mod

    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-123"}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    token = f"header.{payload}.signature"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["ChatGPT-Account-Id"] == "acct-123"
        assert request.url.params["client_version"] == "99.0.0"
        return httpx.Response(200, json={"models": [
            {"slug": "gpt-5.6-sol", "context_window": 272_000},
        ]})

    real_client = httpx.AsyncClient

    def mock_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(context_mod.httpx, "AsyncClient", mock_client)
    result = await context_mod._fetch_codex_context({
        "access_token": token,
        "base_url": "https://chatgpt.com/backend-api/codex",
    }, "gpt-5.6-sol")
    assert result == 272_000


async def test_unknown_model_uses_public_catalog(monkeypatch) -> None:
    from app.services import store
    import app.runtime.llm.context_window as context_mod

    clear_context_window_cache()
    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {
        "model": "custom-new-model", "base_url": _BASE, "api_key": _KEY,
    })
    monkeypatch.setattr(store, "list_models", lambda: [])
    monkeypatch.setattr(OpenAICompatClient, "fetch_model_context_window", lambda self: _return_none())

    async def catalog(_model_id):
        return 262_144

    monkeypatch.setattr(context_mod, "_fetch_public_catalog_context", catalog)
    assert await resolve_context_window("main") == 262_144
    clear_context_window_cache()


async def test_public_catalog_reads_exact_context_without_credentials(monkeypatch) -> None:
    import app.runtime.llm.context_window as context_mod

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "models.dev"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={
            "deepseek": {"models": {"deepseek-v4-flash": {
                "limit": {"context": 1_000_000, "output": 393_216},
            }}},
        })

    real_client = httpx.AsyncClient

    def mock_client(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    clear_context_window_cache()
    monkeypatch.setattr(context_mod.httpx, "AsyncClient", mock_client)
    assert await context_mod._fetch_public_catalog_context("cline-pass/deepseek-v4-flash") == 1_000_000
    clear_context_window_cache()


async def _return_none():
    return None
