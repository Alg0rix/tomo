"""Tests for route metadata, caching and unknown context limits.

All tests are pure (no real network). Mock transport for OpenAI-compat
client and monkeypatched store for seed lookups.
"""
from __future__ import annotations

import httpx2
import pytest

from app.runtime.llm.openai_compat import (
    OpenAICompatClient,
    extract_context_window,
    _match_model_context,
)
from app.runtime.llm.context_window import (
    _catalog_context,
    clear_context_window_cache,
    resolve_context_window,
    resolve_context_window_sync,
)

_BASE = "https://example.test/v1"
_KEY = "sk-test"


# ── extract_context_window ────────────────────────────────────────










def test_extract_string_value() -> None:
    obj = {"num_ctx": "16384"}
    assert extract_context_window(obj) == 16384








def test_extract_none_for_non_dict() -> None:
    assert extract_context_window("not a dict") is None  # type: ignore[arg-type]


# ── _match_model_context ──────────────────────────────────────────












def test_match_skips_non_dict_items() -> None:
    items = ["bad", {"id": "gpt-4o", "context_window": 128000}]
    assert _match_model_context(items, "gpt-4o") == 128000




# ── fetch_model_context_window (via MockTransport) ────────────────


def _make_client(handler, *, model="vllm-model", base_url=_BASE, api_key=_KEY):
    transport = httpx2.MockTransport(handler)
    return OpenAICompatClient(
        base_url=base_url, api_key=api_key, model=model, transport=transport
    )


async def test_fetch_context_from_models_list() -> None:
    """Provider returns context_length in /models list."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={
                "data": [
                    {"id": "vllm-model", "max_model_len": 16384},
                    {"id": "other", "context_window": 4096},
                ]
            })
        return httpx2.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 16384
    finally:
        await client.aclose()


async def test_fetch_context_from_nested_model_info() -> None:
    """Context field nested inside model_info."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={
                "data": [
                    {"id": "vllm-model", "model_info": {"context_length": 32768}},
                ]
            })
        return httpx2.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 32768
    finally:
        await client.aclose()


async def test_fetch_context_fallback_to_single_model_endpoint() -> None:
    """List has no match; falls back to GET /models/{model}."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/models"):
            return httpx2.Response(200, json={"data": []})
        if path.endswith("/models/vllm-model"):
            return httpx2.Response(200, json={"id": "vllm-model", "context_window": 65536})
        return httpx2.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 65536
    finally:
        await client.aclose()


async def test_fetch_context_returns_none_on_network_error() -> None:
    """Network failure → None, no exception."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx is None
    finally:
        await client.aclose()


async def test_fetch_context_returns_none_when_no_field() -> None:
    """Model exists but has no context field → None."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={
                "data": [{"id": "vllm-model", "owned_by": "vllm"}]
            })
        return httpx2.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx is None
    finally:
        await client.aclose()


async def test_fetch_context_list_format_without_data_key() -> None:
    """Some providers return a bare list instead of {data: [...]}."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json=[
                {"id": "vllm-model", "context_length": 8192},
            ])
        return httpx2.Response(404)

    client = _make_client(handler)
    try:
        ctx = await client.fetch_model_context_window()
        assert ctx == 8192
    finally:
        await client.aclose()


# ── _lookup_known ─────────────────────────────────────────────────












def test_catalog_requires_selected_provider_when_limits_differ() -> None:
    catalog = {
        "deepseek": {"deepseek-v4-flash": 1_000_000},
        "proxy": {"deepseek-v4-flash": 128_000, "custom-model": 32_000},
        "other": {"custom-model": 64_000},
    }
    assert _catalog_context(catalog, "cline-pass/deepseek-v4-flash") is None
    assert _catalog_context(catalog, "deepseek-v4-flash", provider_id="proxy") == 128_000
    assert _catalog_context(catalog, "custom-model") is None


# ── resolve_context_window_sync ───────────────────────────────────


def test_sync_unknown_model_does_not_guess_from_name_or_seed(monkeypatch):
    from app.services import store

    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {"model": "gpt-4o"})
    monkeypatch.setattr(store, "list_models", lambda: [{"id": "gpt-4o", "context": 128000}])
    assert resolve_context_window_sync("main") is None


def test_sync_explicit_override(monkeypatch):
    import app.runtime.llm.context_window as mod

    monkeypatch.setattr(mod, "_get_profile", lambda *a, **kw: {"model": "custom", "context_window": 65536})
    assert resolve_context_window_sync("main") == 65536


# ── resolve_context_window (async) caching ────────────────────────


async def test_async_caches_result(monkeypatch) -> None:
    """Subsequent calls hit cache (no second API call)."""
    from app.services import store

    call_count = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={
                "data": [{"id": "test-m", "context_window": 99999}]
            })
        return httpx2.Response(404)

    clear_context_window_cache()
    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {
        "model": "test-m", "base_url": _BASE, "api_key": _KEY,
    })

    # Monkeypatch OpenAICompatClient to use our mock transport
    orig_init = OpenAICompatClient.__init__

    def patched_init(self, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(handler)
        orig_init(self, **kwargs)

    monkeypatch.setattr(OpenAICompatClient, "__init__", patched_init)

    r1 = await resolve_context_window("main")
    r2 = await resolve_context_window("main")
    assert r1 == 99999
    assert r2 == 99999
    # Only one actual HTTP call (second hit cache)
    assert call_count == 1

    clear_context_window_cache()


async def test_async_missing_metadata_stays_unknown(monkeypatch) -> None:
    """API and public catalog lack context → unknown."""
    from app.services import store

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/models"):
            return httpx2.Response(200, json={
                "data": [{"id": "gpt-4o", "owned_by": "openai"}]
            })
        return httpx2.Response(404)

    clear_context_window_cache()
    import app.runtime.llm.context_window as context_mod

    async def no_catalog(_model_id, **_kwargs):
        return None

    monkeypatch.setattr(context_mod, "_fetch_public_catalog_context", no_catalog)
    monkeypatch.setattr(store, "resolve_llm_profile", lambda aid=None: {
        "model": "gpt-4o", "base_url": _BASE, "api_key": _KEY,
    })

    orig_init = OpenAICompatClient.__init__

    def patched_init(self, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(handler)
        orig_init(self, **kwargs)

    monkeypatch.setattr(OpenAICompatClient, "__init__", patched_init)

    result = await resolve_context_window("main")
    assert result is None

    clear_context_window_cache()

async def test_codex_subscription_uses_its_own_catalog(monkeypatch) -> None:
    """Codex's 272K route limit wins over the direct API's larger window."""
    from app.services import store
    import app.runtime.llm.context_window as context_mod

    async def fake_codex(profile, model_id):
        assert profile["auth_mode"] == "subscription"
        assert model_id == "gpt-5.6-sol"
        return 272_000

    async def unexpected_public(_model_id, **_kwargs):
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

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["ChatGPT-Account-Id"] == "acct-123"
        assert request.url.params["client_version"] == "99.0.0"
        return httpx2.Response(200, json={"models": [
            {"slug": "gpt-5.6-sol", "context_window": 272_000},
        ]})

    real_client = httpx2.AsyncClient

    def mock_client(**kwargs):
        return real_client(transport=httpx2.MockTransport(handler), **kwargs)

    monkeypatch.setattr(context_mod.httpx2, "AsyncClient", mock_client)
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

    async def catalog(_model_id, **_kwargs):
        return 262_144

    monkeypatch.setattr(context_mod, "_fetch_public_catalog_context", catalog)
    assert await resolve_context_window("main") == 262_144
    clear_context_window_cache()


async def test_public_catalog_reads_exact_context_without_credentials(monkeypatch) -> None:
    import app.runtime.llm.context_window as context_mod

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "models.dev"
        assert "authorization" not in request.headers
        return httpx2.Response(200, json={
            "deepseek": {"models": {"deepseek-v4-flash": {
                "limit": {"context": 1_000_000, "output": 393_216},
            }}},
        })

    real_client = httpx2.AsyncClient

    def mock_client(**kwargs):
        return real_client(transport=httpx2.MockTransport(handler), **kwargs)

    clear_context_window_cache()
    monkeypatch.setattr(context_mod.httpx2, "AsyncClient", mock_client)
    assert await context_mod._fetch_public_catalog_context("cline-pass/deepseek-v4-flash") == 1_000_000
    clear_context_window_cache()


async def _return_none():
    return None


@pytest.fixture
def context_lookup(monkeypatch):
    import app.runtime.llm.context_window as mod

    clock = [0.0]
    profile = {"model": "custom-model", "base_url": _BASE, "api_key": _KEY, "access_token": _KEY}
    results = []

    async def fetch(*_args):
        return results.pop(0)

    clear_context_window_cache()
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mod, "_get_profile", lambda *_args, **_kwargs: profile.copy())
    monkeypatch.setattr(OpenAICompatClient, "fetch_model_context_window", fetch)
    monkeypatch.setattr(mod, "_fetch_codex_context", fetch)
    monkeypatch.setattr(mod, "_fetch_public_catalog_context", lambda _model, **_kwargs: _return_none())
    yield mod, clock, profile, results
    clear_context_window_cache()


@pytest.mark.parametrize("auth_mode", ["api_key", "subscription"])
async def test_metadata_fallback_retries_without_waiting_an_hour(context_lookup, auth_mode):
    mod, clock, profile, results = context_lookup
    profile["auth_mode"] = auth_mode
    results.extend([None, 1_000_000])

    assert await resolve_context_window("main") is None
    clock[0] = mod._CATALOG_RETRY_S - 1
    assert await resolve_context_window("main") is None
    assert results == [1_000_000]
    clock[0] += 2
    assert await resolve_context_window("main") == 1_000_000





@pytest.fixture(autouse=True)
def isolated_context_state(monkeypatch, tmp_path):
    import app.runtime.llm.context_window as mod

    monkeypatch.setattr(mod, "_state_path", lambda: tmp_path / "context.json")
    clear_context_window_cache()
    yield
    clear_context_window_cache()


async def test_switches_use_endpoint_metadata_and_survive_restart(monkeypatch):
    import app.runtime.llm.context_window as mod

    # Identical model IDs advertise different route limits, including a
    # namespaced ID. Output limits must never replace context limits.
    payload = {
        "route-a": {"api": "https://proxy.test/v1", "models": {
            "deepseek-v4.1-flash": {"limit": {"context": 765432, "output": 20000}},
        }},
        "route-b": {"api": "https://proxy.test/go/v1", "models": {
            "deepseek-v4.1-flash": {"limit": {"context": 345678, "output": 10000}},
            "next-model": {"limit": {"context": 543210}},
        }},
        "namespaced": {"api": "https://namespace.test/v1", "models": {
            "vendor/deepseek-v4.1-flash": {"limit": {"context": 876543}},
        }},
    }
    catalog_calls = []
    real_client = httpx2.AsyncClient

    def handler(request):
        assert "authorization" not in request.headers
        catalog_calls.append(request)
        return httpx2.Response(200, json=payload)

    monkeypatch.setattr(mod.httpx2, "AsyncClient", lambda **kw: real_client(transport=httpx2.MockTransport(handler), **kw))
    monkeypatch.setattr(OpenAICompatClient, "fetch_model_context_window", lambda self: _return_none())
    profile = {"base_url": "https://proxy.test/v1", "model": "deepseek-v4.1-flash", "api_key": _KEY}
    monkeypatch.setattr(mod, "_get_profile", lambda *a, **kw: profile.copy())
    assert await resolve_context_window(profile=profile) == 765432
    profile.update(base_url="https://proxy.test/go/v1", reasoning_effort="high")
    assert await resolve_context_window(profile=profile) == 345678
    profile.update(model="next-model")
    assert await resolve_context_window(profile=profile) == 543210
    profile.update(base_url="https://namespace.test/v1", model="vendor/deepseek-v4.1-flash")
    assert await resolve_context_window(profile=profile) == 876543
    assert await resolve_context_window(profile={**profile, "model": "deepseek-v4.1-flash"}) == 876543
    profile.update(base_url="https://proxy.test/v1", model="deepseek-v4.1-flash", reasoning_effort="low")
    assert await resolve_context_window(profile=profile) == 765432
    assert resolve_context_window_sync(session_id="selected-session") == 765432
    clear_context_window_cache()
    assert await resolve_context_window(profile=profile) == 765432
    assert len(catalog_calls) == 1  # Disk metadata survived the process reset.


async def test_confirmed_limit_survives_restart_but_not_route_or_credential_change(monkeypatch):
    import app.runtime.llm.context_window as mod

    profile = {"base_url": _BASE, "model": "custom-model", "api_key": _KEY}
    results = [654321, None, None, None]

    async def fetch(self):
        return results.pop(0)

    monkeypatch.setattr(OpenAICompatClient, "fetch_model_context_window", fetch)
    monkeypatch.setattr(mod, "_fetch_public_catalog_context", lambda *a, **kw: _return_none())
    assert await resolve_context_window(profile=profile) == 654321
    clear_context_window_cache()
    assert await resolve_context_window(profile=profile) == 654321
    assert await resolve_context_window(profile={**profile, "api_key": "different"}) is None
    assert await resolve_context_window(profile={**profile, "base_url": "https://another.test/v1"}) is None


async def test_picker_retries_unknown_metadata_immediately(context_lookup):
    mod, clock, profile, results = context_lookup
    results.extend([None, 432100])
    assert await resolve_context_window(profile=profile) is None
    assert await resolve_context_window(profile=profile, refresh_unknown=True) == 432100


def test_provider_model_prefixes_and_ambiguous_aliases_are_not_context_matches():
    rows = [{"id": "gpt-4", "context_window": 8192},
            {"id": "gpt-4.1", "context_window": 1000000}]
    assert _match_model_context(rows, "gpt-4.1-new-version") is None
    assert _match_model_context(rows, "gpt-4") == 8192
    assert _match_model_context(rows, "vendor/gpt-4.1") == 1000000
    assert _match_model_context([
        {"id": "one/model", "context_window": 32000},
        {"id": "two/model", "context_window": 64000},
    ], "model") is None
