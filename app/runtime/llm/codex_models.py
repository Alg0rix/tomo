"""Codex model discovery — live API probe with a curated offline fallback.

Trimmed port of ``tmp/hermes-agent``'s ``hermes_cli/codex_models.py``: only
the live-fetch + curated-fallback path. Drops the ``~/.codex`` local
cache/config reads and the forward-compat synthetic-model templating —
those exist in hermes to interoperate with a co-installed Codex CLI, which
is out of scope here (see the design spec's "Out of scope").
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Curated fallback used when there's no token yet (fresh "New profile" form)
# or the live probe fails (network error, expired token). Kept short and
# current rather than exhaustive — live discovery is authoritative whenever
# a valid access token is available.
DEFAULT_CODEX_MODELS: list[str] = [
    "gpt-5.6-sol",
    "gpt-5.6-sol-pro",
    "gpt-5.6-terra",
    "gpt-5.6-terra-pro",
    "gpt-5.6-luna",
    "gpt-5.6-luna-pro",
    "gpt-5.5",
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
]


def _extract_chatgpt_account_id(access_token: str) -> str | None:
    """Best-effort ``chatgpt_account_id`` claim from the OAuth JWT.

    The Codex backend only returns the per-account catalog when the
    ``ChatGPT-Account-Id`` header is present — without it,
    ``GET /backend-api/codex/models`` returns ``{"models":[]}`` (HTTP 200),
    silently degrading to the curated fallback.
    """
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        acct_id = (
            claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
            if isinstance(claims, dict)
            else None
        )
        return acct_id if isinstance(acct_id, str) and acct_id else None
    except Exception:
        return None


def _fetch_models_from_api(
    access_token: str, *, transport: httpx.BaseTransport | None = None
) -> list[str]:
    """Live-probe the Codex backend's model catalog. Returns ``[]`` on any failure."""
    headers = {"Authorization": f"Bearer {access_token}"}
    acct_id = _extract_chatgpt_account_id(access_token)
    if acct_id:
        headers["ChatGPT-Account-Id"] = acct_id

    kwargs: dict[str, Any] = {"timeout": httpx.Timeout(10.0)}
    if transport is not None:
        kwargs["transport"] = transport

    try:
        with httpx.Client(**kwargs) as client:
            resp = client.get(
                "https://chatgpt.com/backend-api/codex/models?client_version=1.0.0",
                headers=headers,
            )
        if resp.status_code != 200:
            return []
        data = resp.json()
        entries = data.get("models", []) if isinstance(data, dict) else []
    except Exception as exc:
        logger.debug("Failed to fetch Codex models from API: %s", exc)
        return []

    sortable: list[tuple[int, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        slug = slug.strip()
        # ``supported_in_api`` describes the *public* OpenAI API, not this
        # OAuth-backed Codex backend — intentionally not filtered on.
        visibility = item.get("visibility", "")
        if isinstance(visibility, str) and visibility.strip().lower() in {"hide", "hidden"}:
            continue
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        sortable.append((rank, slug))

    sortable.sort(key=lambda x: (x[0], x[1]))
    seen: set[str] = set()
    ordered: list[str] = []
    for _, slug in sortable:
        if slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def list_codex_models(
    access_token: str | None, *, transport: httpx.BaseTransport | None = None
) -> list[str]:
    """Return available Codex model ids: live API when a token works, else curated defaults."""
    if access_token:
        api_models = _fetch_models_from_api(access_token, transport=transport)
        if api_models:
            return api_models
    return list(DEFAULT_CODEX_MODELS)


__all__ = ["DEFAULT_CODEX_MODELS", "list_codex_models"]
