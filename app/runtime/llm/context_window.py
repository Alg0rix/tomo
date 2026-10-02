"""Resolve route-specific context limits from metadata, never model-name guesses.

Confirmed limits survive outages and restarts. Unknown limits remain None.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx2

from app.runtime.llm.codex_models import _extract_chatgpt_account_id
from app.runtime.llm.openai_compat import extract_context_window

_logger = logging.getLogger(__name__)
_CACHE_TTL_S = 3600.0
_CATALOG_TTL_S = 4 * 3600.0
_CATALOG_RETRY_S = 300.0
_CATALOG_URL = "https://models.dev/api.json"
_cache: dict[tuple[str, str, str], tuple[int | None, float]] = {}
_provider_windows: dict[str, int] = {}
_catalog: dict[str, dict[str, int]] = {}
_catalog_routes: dict[str, str] = {}
_catalog_expiry = 0.0
_catalog_fetched_at = 0.0
_state_loaded = False


def _state_path():
    from app.core.config import VAR_DIR

    return VAR_DIR / "cache" / "context_metadata.json"


def _load_state() -> None:
    global _state_loaded, _catalog, _catalog_routes, _catalog_fetched_at
    if _state_loaded:
        return
    _state_loaded = True
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        catalog, routes, confirmed = data["catalog"], data["routes"], data["confirmed"]
        if not all(isinstance(v, dict) for v in (catalog, routes, confirmed)):
            return
        _catalog = {
            provider: {model: ctx for model, ctx in models.items()
                       if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0}
            for provider, models in catalog.items() if isinstance(models, dict)
        }
        _catalog_routes = {url: provider for url, provider in routes.items()
                           if isinstance(url, str) and isinstance(provider, str)}
        _provider_windows.update({key: ctx for key, ctx in confirmed.items()
                                  if isinstance(ctx, int) and not isinstance(ctx, bool) and ctx > 0})
        _catalog_fetched_at = float(data.get("fetched_at", 0))
    except (OSError, ValueError, TypeError, KeyError):
        pass


def _save_state() -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "catalog": _catalog, "routes": _catalog_routes,
            "fetched_at": _catalog_fetched_at, "confirmed": _provider_windows,
        }), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def _canonical_model_id(model_id: str) -> str:
    return model_id.rsplit("/", 1)[-1].lstrip("~").strip()


def _cache_key(profile: dict[str, Any]) -> tuple[str, str, str]:
    credential = profile.get("access_token") if profile.get("auth_mode") == "subscription" else profile.get("api_key")
    fingerprint = hashlib.sha256((credential or "").encode()).hexdigest()[:16]
    return (profile.get("base_url", "").rstrip("/"), profile.get("model", ""),
            f"{profile.get('auth_mode', 'api_key')}:{fingerprint}")


def _confirmed_key(key: tuple[str, str, str]) -> str:
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def record_context_window(profile: dict[str, Any], context_window: int) -> None:
    """Retain a limit reported by the active route, including overflow errors."""
    _load_state()
    if extract_context_window({"context_window": context_window}) is None:
        return
    key = _cache_key(profile)
    _provider_windows[_confirmed_key(key)] = context_window
    _cache[key] = (context_window, time.monotonic() + _CACHE_TTL_S)
    _save_state()


async def _fetch_codex_context(profile: dict[str, Any], model_id: str) -> int | None:
    """Use the authenticated Codex catalog, whose limits are route-specific."""
    token = profile.get("access_token") or ""
    base_url = (profile.get("base_url") or "").rstrip("/")
    if not token or not base_url:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    account_id = _extract_chatgpt_account_id(token)
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    model = _canonical_model_id(model_id)
    try:
        async with httpx2.AsyncClient(timeout=8.0) as client:
            for version in ("99.0.0", "0.0.0"):
                response = await client.get(
                    base_url + "/models", params={"client_version": version}, headers=headers
                )
                if response.status_code != 200:
                    continue
                payload = response.json()
                entries = payload.get("models") if isinstance(payload, dict) else None
                if not isinstance(entries, list) or not entries:
                    continue
                for item in entries:
                    if isinstance(item, dict) and item.get("slug") == model:
                        return extract_context_window(item)
                break
    except Exception as exc:
        _logger.debug("Codex context catalog unavailable: %s", exc)
    return None


def _catalog_context(
    catalog: dict[str, dict[str, int]], model_id: str, *, provider_id: str | None = None,
) -> int | None:
    """Use exact route metadata; never borrow a different provider's limit."""
    model = _canonical_model_id(model_id)
    if not model:
        return None
    if provider_id:
        models = catalog.get(provider_id, {})
        exact = models.get(model_id) or models.get(model)
        if exact is not None:
            return exact
        matches = {ctx for mid, ctx in models.items() if _canonical_model_id(mid) == model}
        return next(iter(matches)) if len(matches) == 1 else None
    matches = {ctx for models in catalog.values()
               for mid, ctx in models.items() if mid in {model_id, model}}
    return next(iter(matches)) if len(matches) == 1 else None


def _catalog_provider(base_url: str) -> str | None:
    selected = urlsplit(base_url)
    matches = []
    for api, provider in _catalog_routes.items():
        route = urlsplit(api)
        path = route.path.rstrip("/")
        if (selected.scheme == route.scheme and selected.netloc == route.netloc
                and (selected.path.rstrip("/") == path or selected.path.startswith(path + "/"))):
            matches.append((len(path), provider))
    return max(matches)[1] if matches else None


async def _fetch_public_catalog_context(model_id: str, *, base_url: str = "") -> int | None:
    """Fetch public endpoint/model metadata without credentials or prompts."""
    global _catalog, _catalog_expiry, _catalog_routes, _catalog_fetched_at
    _load_state()
    now = time.monotonic()
    if now >= _catalog_expiry and (not _catalog or time.time() - _catalog_fetched_at >= _CATALOG_TTL_S):
        try:
            async with httpx2.AsyncClient(timeout=8.0) as client:
                response = await client.get(_CATALOG_URL)
                response.raise_for_status()
                payload = response.json()
            catalog: dict[str, dict[str, int]] = {}
            routes: dict[str, str] = {}
            if isinstance(payload, dict):
                for provider_id, provider in payload.items():
                    if not isinstance(provider, dict):
                        continue
                    models = provider.get("models")
                    if not isinstance(models, dict):
                        continue
                    api = provider.get("api")
                    if isinstance(api, str) and api:
                        routes[api] = provider_id
                    known = {}
                    for mid, info in models.items():
                        ctx = extract_context_window(info)
                        if ctx is not None:
                            known[mid] = ctx
                    catalog[provider_id] = known
            if not any(catalog.values()):
                raise ValueError("empty public model catalog")
            _catalog, _catalog_routes = catalog, routes
            _catalog_fetched_at = time.time()
            _save_state()
            _catalog_expiry = now + _CATALOG_TTL_S
        except Exception as exc:
            _logger.debug("public model catalog unavailable: %s", exc)
            _catalog_expiry = now + _CATALOG_RETRY_S
    return _catalog_context(_catalog, model_id, provider_id=_catalog_provider(base_url))


def resolve_context_window_sync(agent_id: str | None = None, *, session_id: str | None = None) -> int | None:
    """Read explicit overrides and cached metadata without network I/O."""
    profile = _get_profile(agent_id, session_id=session_id) or {}
    _load_state()
    explicit = extract_context_window(profile)
    if explicit is not None:
        return explicit
    key = _cache_key(profile)
    cached = _cache.get(key)
    if cached and cached[0] is not None:
        return cached[0]
    confirmed = _provider_windows.get(_confirmed_key(key))
    if confirmed is not None or profile.get("auth_mode") == "subscription":
        return confirmed
    return _catalog_context(_catalog, profile.get("model") or "",
                            provider_id=_catalog_provider(profile.get("base_url") or ""))


async def resolve_context_window(
    agent_id: str | None = None, *, session_id: str | None = None,
    profile: dict[str, Any] | None = None, refresh_unknown: bool = False,
) -> int | None:
    """Resolve the selected route. Reasoning changes share the same model cache."""
    from app.runtime.llm.openai_compat import LLMConfigError, OpenAICompatClient

    profile = profile if profile is not None else _get_profile(agent_id, session_id=session_id)
    profile = profile or {}
    _load_state()
    explicit = extract_context_window(profile)
    if explicit is not None:
        return explicit
    key = _cache_key(profile)
    base_url, model_id, _ = key
    if not model_id:
        return None
    cached = _cache.get(key)
    if cached and time.monotonic() < cached[1] and (cached[0] is not None or not refresh_unknown):
        return cached[0]
    result = None
    if profile.get("auth_mode") == "subscription":
        result = await _fetch_codex_context(profile, model_id)
    else:
        try:
            client = OpenAICompatClient(base_url=base_url, api_key=profile.get("api_key") or "", model=model_id)
            try:
                result = await client.fetch_model_context_window()
            finally:
                await client.aclose()
        except LLMConfigError:
            _logger.debug("no API key; skipping provider context lookup")
        except Exception as exc:
            _logger.info("provider context lookup failed: %s", exc)
    confirmed_key = _confirmed_key(key)
    if result is not None:
        record_context_window(profile, result)
    else:
        result = _provider_windows.get(confirmed_key)
    if result is None and profile.get("auth_mode") != "subscription":
        result = await _fetch_public_catalog_context(model_id, base_url=base_url)
    ttl = _CACHE_TTL_S if result is not None else _CATALOG_RETRY_S
    _cache[key] = (result, time.monotonic() + ttl)
    return result


def clear_context_window_cache() -> None:
    """Reset process state; persisted metadata remains available after restart."""
    global _catalog_expiry, _catalog_fetched_at, _state_loaded
    _cache.clear()
    _provider_windows.clear()
    _catalog.clear()
    _catalog_routes.clear()
    _catalog_expiry = 0.0
    _catalog_fetched_at = 0.0
    _state_loaded = False


def _get_profile(agent_id: str | None, *, session_id: str | None = None) -> dict[str, Any] | None:
    try:
        from app.runtime.llm import resolve_main_profile

        return resolve_main_profile(agent_id, session_id=session_id)
    except Exception:
        return None


def _agent_model(agent_id: str | None, *, session_id: str | None = None) -> str:
    profile = _get_profile(agent_id, session_id=session_id)
    return ((profile or {}).get("model") or "").strip()
