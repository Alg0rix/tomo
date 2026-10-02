"""Built-in OpenCode endpoints; custom profile URLs remain untouched."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx2

from app.runtime.llm.http import provider_ssl_context, user_agent

PRESETS = {
    "opencode-go": {"name": "OpenCode Go", "base_url": "https://opencode.ai/zen/go/v1"},
    "opencode-zen": {"name": "OpenCode Zen", "base_url": "https://opencode.ai/zen/v1"},
}

# Effort values published at https://models.opencode.ai/api.json (2026-10-02).
_DEEPSEEK_EFFORTS = {
    "deepseek-v4-flash": ["low", "high", "max"],
    "deepseek-v4-flash-vision-exp": ["low", "high", "max"],
    "deepseek-v4.1-flash": ["low", "high", "max"],
    "deepseek-v4-pro": ["high", "max"],
}


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
    if provider_for_url(profile.get("base_url") or ""):
        model = profile.get("model", "")
        if model in _DEEPSEEK_EFFORTS:
            return list(_DEEPSEEK_EFFORTS[model])
        # Only advertise effort values on models that accept them. Other
        # native protocols still expose their streamed thinking, if supplied.
        return (
            ["low", "medium", "high"]
            if model.startswith("gpt-")
            else []
        )
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
    return models
