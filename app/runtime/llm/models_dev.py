"""models.dev catalog — shared model capability metadata for vision routing.

The catalog (``https://models.dev/api.json``) maps provider -> model id ->
``modalities.input`` / ``attachment``. It is fetched lazily, only when an
image actually needs routing (never on a text-only turn), cached in-process
and on disk under ``$TOMO_HOME/state/cache/models_dev.json`` for 12h. A
failed or stale fetch degrades to ``None`` ("unknown") so the caller falls
through to the next probe — a cold cache must never wedge a turn.
"""

from __future__ import annotations

import json
import logging
import time
from threading import Lock
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_CATALOG_URL = "https://models.dev/api.json"
_TTL_SECONDS = 12 * 3600
_FETCH_TIMEOUT = 8.0

# base_url hostname (or suffix) -> models.dev provider key. Profiles carry no
# provider field, so the catalog lane is only reachable when the endpoint is
# recognizable; unknown/custom hosts return None and fall through.
_PROVIDER_HOSTS: dict[str, str] = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "google",
    "aiplatform.googleapis.com": "google",
    "openrouter.ai": "openrouter",
    "api.mistral.ai": "mistral",
    "api.x.ai": "xai",
    "api.deepseek.com": "deepseek",
    "api.groq.com": "groq",
    "api.cerebras.ai": "cerebras",
    "api.together.xyz": "together",
    "api.fireworks.ai": "fireworks",
    "dashscope.aliyuncs.com": "alibaba",
    "open.bigmodel.cn": "zai",
    "api.z.ai": "zai",
    "api.minimaxi.com": "minimax",
    "api.minimax.io": "minimax",
    "api.moonshot.ai": "moonshotai",
    "api.moonshot.cn": "moonshotai",
}

_lock = Lock()
_index: dict[str, dict[str, bool]] | None = None
_index_loaded_at = 0.0


def _cache_path():
    from app.core.config import VAR_DIR

    return VAR_DIR / "cache" / "models_dev.json"


def provider_for_base_url(base_url: str | None) -> str | None:
    """models.dev provider key for *base_url*, or None for custom/local hosts."""
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    if not host:
        return None
    if host in _PROVIDER_HOSTS:
        return _PROVIDER_HOSTS[host]
    # Suffix match covers regional/vanity hosts (e.g. api.us.openrouter.ai).
    for known, provider in _PROVIDER_HOSTS.items():
        if host.endswith("." + known):
            return provider
    return None


def _entry_supports_vision(entry: Any) -> bool | None:
    """True/False from a catalog model entry, None when unusable."""
    if not isinstance(entry, dict):
        return None
    modalities = entry.get("modalities")
    if isinstance(modalities, dict):
        inputs = modalities.get("input")
        if isinstance(inputs, list) and inputs:
            return "image" in inputs
    attachment = entry.get("attachment")
    if attachment is not None:
        return bool(attachment)
    return None


def _parse_index(payload: Any) -> dict[str, dict[str, bool]]:
    """``{provider: {model_id: supports_vision}}`` from the api.json payload."""
    if not isinstance(payload, dict):
        return {}
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        providers = payload  # models.dev top-level keys are provider ids
    index: dict[str, dict[str, bool]] = {}
    for pid, pdata in providers.items():
        models = pdata.get("models") if isinstance(pdata, dict) else None
        if not isinstance(models, dict):
            continue
        table: dict[str, bool] = {}
        for mid, entry in models.items():
            verdict = _entry_supports_vision(entry)
            if verdict is not None:
                table[str(mid)] = verdict
        if table:
            index[str(pid)] = table
    return index


def _fetch_index() -> dict[str, dict[str, bool]]:
    """Network fetch + parse. Errors propagate to the caller's fallback."""
    import httpx

    resp = httpx.get(_CATALOG_URL, timeout=_FETCH_TIMEOUT)
    resp.raise_for_status()
    index = _parse_index(resp.json())
    if index:
        try:
            path = _cache_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"fetched_at": time.time(), "index": index}),
                encoding="utf-8",
            )
        except OSError:
            pass  # cache write is best-effort; the in-memory index still works
    return index


def _load_index(*, allow_network: bool) -> dict[str, dict[str, bool]] | None:
    """Cached catalog index, or None when unavailable and no fetch allowed."""
    global _index, _index_loaded_at
    with _lock:
        if _index is not None:
            return _index
    disk_index: dict[str, dict[str, bool]] | None = None
    disk_fresh = False
    try:
        raw = json.loads(_cache_path().read_text(encoding="utf-8"))
        fetched_at = float(raw.get("fetched_at") or 0)
        idx = raw.get("index")
        disk_index = idx if isinstance(idx, dict) else None
        disk_fresh = time.time() - fetched_at < _TTL_SECONDS
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if disk_index is not None:
        with _lock:
            if _index is None:
                _index = disk_index
                _index_loaded_at = time.time()
    if not allow_network or (disk_index is not None and disk_fresh):
        return disk_index
    try:
        fresh = _fetch_index()
    except Exception as exc:
        logger.debug("models.dev fetch failed: %s", exc)
        return disk_index  # stale beats nothing
    with _lock:
        _index = fresh
        _index_loaded_at = time.time()
    return fresh


def catalog_supports_vision(
    provider: str | None, model_id: str | None, *, allow_network: bool = False
) -> bool | None:
    """Catalog verdict for ``provider``/``model_id``; None when unknown.

    Tries the raw model id first (OpenRouter entries keep the ``vendor/``
    prefix), then the last path segment (``openai/gpt-4o`` -> ``gpt-4o`` for
    direct-provider catalogs).
    """
    provider = (provider or "").strip()
    model_id = (model_id or "").strip()
    if not provider or not model_id:
        return None
    index = _load_index(allow_network=allow_network)
    if not index:
        return None
    table = index.get(provider)
    if not table:
        return None
    if model_id in table:
        return table[model_id]
    tail = model_id.rsplit("/", 1)[-1]
    if tail != model_id and tail in table:
        return table[tail]
    return None


def reset_catalog_cache() -> None:
    """Drop the in-memory index (tests)."""
    global _index, _index_loaded_at
    with _lock:
        _index = None
        _index_loaded_at = 0.0


__all__ = [
    "provider_for_base_url",
    "catalog_supports_vision",
    "reset_catalog_cache",
]
