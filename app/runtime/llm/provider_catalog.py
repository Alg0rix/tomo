"""Built-in OpenCode endpoints; custom profile URLs remain untouched."""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import httpx2

from app.runtime.llm.http import provider_ssl_context, user_agent

PRESETS = {
    "opencode-go": {"name": "OpenCode Go", "base_url": "https://opencode.ai/zen/go/v1"},
    "opencode-zen": {"name": "OpenCode Zen", "base_url": "https://opencode.ai/zen/v1"},
}

_METADATA_URL = "https://models.opencode.ai/api.json"
_METADATA_TTL = 4 * 3600
_metadata: dict | None = None
_metadata_fetched_at = 0.0
_metadata_retry_after = 0.0


def _metadata_path():
    from app.core.config import VAR_DIR

    return VAR_DIR / "cache" / "opencode_models.json"


def _cached_metadata() -> dict:
    """Runtime lookups only read cached metadata; never issue network requests."""
    global _metadata, _metadata_fetched_at
    if _metadata is None:
        try:
            cached = json.loads(_metadata_path().read_text(encoding="utf-8"))
            data = cached["data"]
            if not isinstance(data, dict) or not data:
                raise ValueError("Empty model metadata")
            _metadata_fetched_at = float(cached["fetched_at"])
            _metadata = data
        except (OSError, ValueError, KeyError, TypeError):
            _metadata = {}
    return _metadata


async def ensure_model_metadata(base_url: str) -> None:
    """Warm/refresh the catalog at setup and picker boundaries; stale beats failure."""
    global _metadata, _metadata_fetched_at, _metadata_retry_after
    if not provider_for_url(base_url):
        return
    cached = _cached_metadata()
    now = time.time()
    if (cached and now - _metadata_fetched_at < _METADATA_TTL) or now < _metadata_retry_after:
        return
    try:
        async with httpx2.AsyncClient(timeout=8, verify=provider_ssl_context()) as client:
            response = await client.get(_METADATA_URL)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or not any(
            isinstance(data.get(provider), dict) and isinstance(data[provider].get("models"), dict)
            for provider in PRESETS
        ):
            raise ValueError("Invalid model metadata")
    except (httpx2.HTTPError, ValueError):
        _metadata_retry_after = now + 300
        return
    _metadata, _metadata_fetched_at = data, now
    _metadata_retry_after = 0.0
    try:
        path = _metadata_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"fetched_at": now, "data": data}), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def provider_for_url(base_url: str) -> str:
    url = urlparse(base_url)
    if url.scheme == "https" and url.hostname == "opencode.ai":
        path = url.path.rstrip("/")
        for provider, preset in PRESETS.items():
            if path == urlparse(preset["base_url"]).path:
                return provider
    return ""


def model_protocol(provider: str, model: str) -> str:
    """Published endpoint tables: https://opencode.ai/docs/{go,zen}/."""
    model = model.lower()
    if model.startswith(("gpt-", "grok-", "muse-spark")):
        return "responses"
    if model.startswith(("claude-", "union-alpha")):
        return "messages"
    if provider == "opencode-go" and model.startswith(("minimax-", "qwen")):
        return "messages"
    if provider == "opencode-zen":
        if model.startswith("gemini-"):
            return "google"
        if model.startswith("qwen") and model != "qwen3.8-max":
            return "messages"
    return "chat"


def profile_efforts(profile: dict | None) -> list[str]:
    if not profile:
        return []
    provider = provider_for_url(profile.get("base_url") or "")
    if provider:
        model = profile.get("model", "")
        # Native Messages/Google clients do not yet project effort fields.
        if model_protocol(provider, model) not in ("chat", "responses"):
            return []
        provider_data = _cached_metadata().get(provider)
        models = provider_data.get("models") if isinstance(provider_data, dict) else None
        entry = models.get(model) if isinstance(models, dict) else None
        options = entry.get("reasoning_options") if isinstance(entry, dict) else None
        if not isinstance(options, list):
            return []
        efforts = []
        for option in options:
            if isinstance(option, dict) and option.get("type") == "effort" and isinstance(option.get("values"), list):
                for value in option["values"]:
                    if isinstance(value, str) and value.strip() and value not in efforts:
                        efforts.append(value)
        return efforts
    return list(profile.get("reasoning_efforts") or [])


async def fetch_models(provider: str, api_key: str) -> list[str]:
    if provider not in PRESETS:
        raise ValueError("Unknown provider")
    if not api_key.strip():
        raise ValueError("Enter an API token first")
    try:
        async with httpx2.AsyncClient(
            timeout=20, verify=provider_ssl_context()
        ) as client:
            response = await client.get(
                PRESETS[provider]["base_url"] + "/models",
                headers={
                    "Authorization": f"Bearer {api_key.strip()}",
                    "User-Agent": user_agent(),
                },
            )
            response.raise_for_status()
            rows = response.json().get("data", [])
    except httpx2.HTTPStatusError as exc:
        raise ValueError(
            f"Provider could not load models (HTTP {exc.response.status_code}). Check your token and account."
        ) from exc
    except (httpx2.HTTPError, ValueError) as exc:
        raise ValueError("Provider catalog is unavailable. Try again.") from exc
    models = list(
        dict.fromkeys(
            row["id"]
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]
        )
    )
    if not models:
        raise ValueError("Provider returned no models")
    await ensure_model_metadata(PRESETS[provider]["base_url"])
    return models
