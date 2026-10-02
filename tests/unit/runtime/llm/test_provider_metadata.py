"""Catalog-driven effort discovery, refresh, and outage behavior."""
import json
import time

import httpx2
import pytest

from app.runtime.llm import provider_catalog as catalog

URL = "https://opencode.ai/zen/go/v1"


def metadata(values):
    return {"opencode-go": {"models": {
        "new-model": {"reasoning_options": [{"type": "effort", "values": values}]},
        "toggle-only": {"reasoning_options": [{"type": "toggle"}]},
        "qwen-new": {"reasoning_options": [{"type": "effort", "values": values}]},
    }}}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    monkeypatch.setattr(catalog, "_metadata_path", lambda: path)
    monkeypatch.setattr(catalog, "_metadata", None)
    monkeypatch.setattr(catalog, "_metadata_fetched_at", 0.0)
    monkeypatch.setattr(catalog, "_metadata_retry_after", 0.0)
    return path


@pytest.mark.asyncio
async def test_new_model_and_changed_efforts_are_discovered(cache, monkeypatch):
    calls = []
    values = ["low", "max"]

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json=metadata(values))

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kw: real_client(
        **kw, transport=httpx2.MockTransport(respond),
    ))
    profile = {"base_url": URL, "model": "new-model"}
    assert catalog.profile_efforts(profile) == []  # hot path does not fetch
    await catalog.ensure_model_metadata(URL)
    assert catalog.profile_efforts(profile) == ["low", "max"]
    await catalog.ensure_model_metadata(URL)
    assert len(calls) == 1
    assert catalog.profile_efforts({**profile, "model": "toggle-only"}) == []
    assert catalog.profile_efforts({**profile, "model": "qwen-new"}) == []
    values[:] = ["medium", "high"]
    monkeypatch.setattr(catalog, "_metadata_fetched_at", 0.0)
    await catalog.ensure_model_metadata(URL)
    assert catalog.profile_efforts(profile) == values
    monkeypatch.setattr(catalog, "_metadata", None)
    assert catalog.profile_efforts(profile) == values  # restart reads disk


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", [False, True])
async def test_failed_refresh_preserves_disk_cache_and_backs_off(cache, monkeypatch, broken):
    cache.write_text(json.dumps({"fetched_at": 1, "data": metadata(["high"])}))
    calls = []

    def respond(request):
        calls.append(request)
        return httpx2.Response(200, json={}) if broken else httpx2.Response(503)

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kw: real_client(
        **kw, transport=httpx2.MockTransport(respond),
    ))
    await catalog.ensure_model_metadata(URL)
    assert catalog.profile_efforts({"base_url": URL, "model": "new-model"}) == ["high"]
    await catalog.ensure_model_metadata(URL)
    assert len(calls) == 1
    assert catalog._metadata_retry_after > time.time()


def test_custom_profile_keeps_declared_efforts(cache):
    assert catalog.profile_efforts({"base_url": "https://custom.test/v1", "reasoning_efforts": ["deep"]}) == ["deep"]
