"""Vision (image-input) capability detection and image-input routing.

Capability is resolved as a chain — first hit wins, ``None`` means unknown
and falls through to the next probe:

1. ``model_capability_overrides`` setting (per-model user pin).
2. Ollama ``/api/show`` probe — only for local endpoints (remote
   OpenAI-compatible APIs expose Ollama-compat routes that can misidentify).
3. models.dev catalog (:mod:`app.runtime.llm.models_dev`), when the profile's
   ``base_url`` maps to a known provider. Network fetch happens only when an
   image needs routing; the result is disk-cached for 12h.
4. Static prefix table — last resort, normalized model ids only.

Model ids are normalized before matching (``openai/gpt-4o`` → ``gpt-4o``) so
aggregated gateways and Azure-style deployment names still resolve.

Routing (:func:`decide_image_input_mode`, ``image_input_mode`` setting):
``auto`` (default) attaches image parts natively when the main model proves
vision-capable, else pre-analyzes them through the auxiliary vision profile
into text; an explicit ``vision_profile_id`` pin forces that describe path
even for a vision-capable main model. ``native`` / ``text`` are absolute
overrides.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Prefix -> vision-capable. Longest-prefix-first match (see
# ``_KNOWN_VISION_SORTED``). Researched August 2026; update as new model
# families ship. Deliberately conservative — omitting a real vision model
# only costs a text fallback note, never a broken request.
_KNOWN_VISION_PREFIXES: list[str] = [
    # OpenAI — GPT-4o/4.1/4.5, the GPT-5 family (incl. 5.x/mini/nano/sol/
    # terra/luna variants), and the o-series reasoning models all take image
    # input.
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.5",
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-5",
    "o1",
    "o3",
    "o4",
    # Anthropic — every Claude 3+ model ships vision (Claude 3 was the first
    # vision-capable Claude generation); this covers 3.x, Sonnet/Opus/Haiku
    # 4.x, Opus 5, Sonnet 5, and the Fable/Mythos-class models.
    "claude-3",
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable",
    "claude-mythos",
    # Google — Gemini has been natively multimodal since 1.5; Gemini 3 /
    # Omni made every modality co-equal.
    "gemini-1.5",
    "gemini-2.",
    "gemini-3",
    "gemini-omni",
    # Mistral's vision line (base mistral-large/small text models are not
    # vision-capable and are intentionally not listed).
    "pixtral",
    # Alibaba Qwen vision-language line (plain "qwen" text models excluded).
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "qwen-vl",
    # Meta Llama vision (3.2+ added vision variants; Llama 4 is natively
    # multimodal). Llama 3.1 and earlier are text-only and excluded.
    "llama-3.2-vision",
    "llama-4",
    # Google Gemma vision (3+; Gemma 2 and earlier are text-only).
    "gemma-3",
    # DeepSeek's vision-language variant — the plain deepseek-chat /
    # deepseek-reasoner models are text-only and intentionally excluded.
    "deepseek-vl",
]

_KNOWN_VISION_SORTED = sorted(_KNOWN_VISION_PREFIXES, key=len, reverse=True)

_VALID_MODES = frozenset({"auto", "native", "text"})

# (base_url, model) -> (verdict, ts); Ollama probes are re-asked hourly.
_OLLAMA_CACHE: dict[tuple[str, str], tuple[bool | None, float]] = {}
_OLLAMA_CACHE_TTL = 3600.0
_OLLAMA_PROBE_TIMEOUT = 3.0


def normalize_model_id(model_id: str | None) -> str:
    """Lowercased wire id minus its ``vendor/`` prefixes.

    ``openai/gpt-4o`` -> ``gpt-4o``; ``meta-llama/llama-4-scout`` ->
    ``llama-4-scout``. Only the last path segment carries the capability
    signal — vendor namespaces are transport detail, not model identity.
    """
    return (model_id or "").strip().lower().rsplit("/", 1)[-1]


def _settings_overrides() -> dict[str, Any]:
    """``model_capability_overrides`` map from settings; {} when absent."""
    try:
        from app.services import store

        raw = store.get_settings().get("model_capability_overrides")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _coerce_bool(raw: Any) -> bool | None:
    """Strict bool coercion: real bools, 0/1, and boolean strings only."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return bool(raw) if raw in (0, 1) else None
    if isinstance(raw, str):
        return {"true": True, "yes": True, "on": True, "1": True,
                "false": False, "no": False, "off": False, "0": False}.get(
            raw.strip().lower()
        )
    return None


def _override_verdict(model_id: str) -> bool | None:
    """User-pinned capability for *model_id*; matched raw, then normalized."""
    overrides = _settings_overrides()
    if not overrides:
        return None
    for key in (model_id.strip(), normalize_model_id(model_id), model_id.strip().lower()):
        if not key or key not in overrides:
            continue
        entry = overrides[key]
        verdict = _coerce_bool(entry.get("supports_vision")) if isinstance(entry, dict) else _coerce_bool(entry)
        if verdict is not None:
            return verdict
    return None


def _is_local_base_url(base_url: str) -> bool:
    host = (urlparse((base_url or "").strip()).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(".local")


def _ollama_supports_vision(model_id: str, base_url: str) -> bool | None:
    """Ollama ``/api/show`` verdict for local endpoints; None when unreachable.

    ``capabilities`` (0.6.0+) is authoritative; older builds expose
    ``model_info.*.vision.block_count`` for projector-carrying models.
    """
    if not _is_local_base_url(base_url) or not model_id:
        return None
    key = (base_url.rstrip("/"), model_id)
    cached = _OLLAMA_CACHE.get(key)
    if cached and time.time() - cached[1] < _OLLAMA_CACHE_TTL:
        return cached[0]
    verdict: bool | None = None
    try:
        import httpx2

        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        resp = httpx2.post(
            f"{root}/api/show", json={"model": model_id}, timeout=_OLLAMA_PROBE_TIMEOUT
        )
        if resp.status_code == 200:
            payload = resp.json()
            caps = payload.get("capabilities")
            if isinstance(caps, list) and caps:
                verdict = any(str(c).lower() == "vision" for c in caps)
            else:
                info = payload.get("model_info")
                verdict = bool(
                    isinstance(info, dict)
                    and any("vision." in str(k).lower() for k in info)
                )
    except Exception as exc:
        logger.debug("ollama vision probe failed for %s: %s", model_id, exc)
    _OLLAMA_CACHE[key] = (verdict, time.time())
    return verdict


def lookup_vision_capability(
    model_id: str | None,
    base_url: str | None = "",
    *,
    allow_network: bool = False,
) -> bool | None:
    """True/False when vision capability is resolvable, None when unknown.

    See module docstring for the chain order. ``allow_network`` permits the
    models.dev fetch — only turn paths that actually carry images should set
    it, so text-only turns never pay a network round-trip.
    """
    mid = (model_id or "").strip()
    if not mid:
        return None
    verdict = _override_verdict(mid)
    if verdict is not None:
        return verdict
    verdict = _ollama_supports_vision(mid, base_url or "")
    if verdict is not None:
        return verdict
    from app.runtime.llm.models_dev import catalog_supports_vision, provider_for_base_url

    verdict = catalog_supports_vision(
        provider_for_base_url(base_url), mid, allow_network=allow_network
    )
    if verdict is not None:
        return verdict
    normalized = normalize_model_id(mid)
    for prefix in _KNOWN_VISION_SORTED:
        if normalized.startswith(prefix):
            return True
    return None


def model_supports_vision(model_id: str | None, base_url: str | None = "") -> bool:
    """True when *model_id* is proven to accept image input.

    Conservative: unknown ids return False so image bytes are never sent to
    a model on a guess (a proven-wrong send 400s the whole turn). The
    describe path (:func:`analyze_image_data_url`) still covers unknown
    models via the auxiliary vision profile.
    """
    return lookup_vision_capability(model_id, base_url) is True


def _main_profile(agent_id: str | None):
    from app.runtime.llm import resolve_main_profile

    return resolve_main_profile(agent_id)


def resolve_model_id(agent_id: str | None) -> str:
    """Best-effort model id for *agent_id*'s resolved LLM profile."""
    try:
        profile: dict[str, Any] | None = _main_profile(agent_id)
        return ((profile or {}).get("model") or "").strip()
    except Exception:
        return ""


def agent_supports_vision(agent_id: str | None) -> bool:
    """True when *agent_id*'s resolved profile is proven vision-capable."""
    try:
        profile = _main_profile(agent_id) or {}
        return model_supports_vision(
            profile.get("model"), profile.get("base_url") or ""
        )
    except Exception:
        return False


def _image_input_mode_setting() -> str:
    try:
        from app.services import store

        mode = str(store.get_settings().get("image_input_mode") or "auto").strip().lower()
        return mode if mode in _VALID_MODES else "auto"
    except Exception:
        return "auto"


def _vision_profile_pin() -> str:
    try:
        from app.services import store

        return str(store.get_settings().get("vision_profile_id") or "").strip()
    except Exception:
        return ""


def decide_image_input_mode(agent_id: str | None) -> str:
    """``"native"`` or ``"text"`` for *agent_id*'s next turn.

    ``auto``: an explicit ``vision_profile_id`` pin means the user wants the
    dedicated vision model — describe images into text even when the main
    model could see them; otherwise attach natively only when the resolved
    main profile's capability lookup proves ``True``.
    """
    mode = _image_input_mode_setting()
    if mode != "auto":
        return mode
    if _vision_profile_pin():
        return "text"
    try:
        profile = _main_profile(agent_id) or {}
    except Exception:
        return "text"
    if lookup_vision_capability(
        profile.get("model"), profile.get("base_url") or "", allow_network=True
    ) is True:
        return "native"
    return "text"


def resolve_vision_profile(agent_id: str | None) -> dict[str, Any] | None:
    """The profile that answers image-describe calls for *agent_id*.

    Order: explicit ``vision_profile_id`` pin (trusted as a user assertion —
    capability is not re-checked) → the agent's resolved main profile when
    proven vision-capable → the first enabled profile whose model proves
    vision-capable. ``None`` when nothing can see images.
    """
    from app.services import store

    pinned = _vision_profile_pin()
    if pinned:
        try:
            from app.models.mixins import llm_profiles as llm_profiles_store

            prof = store.with_db(
                lambda conn: llm_profiles_store.get_profile(conn, pinned)
            )
        except Exception:
            prof = None
        if prof and prof.get("enabled"):
            prof['model'] = store.get_settings().get('vision_model_name') or prof['model']
            return prof
    try:
        main = _main_profile(agent_id)
    except Exception:
        main = None
    if main and lookup_vision_capability(
        main.get("model"), main.get("base_url") or "", allow_network=True
    ) is True:
        return main
    try:
        candidates = store.with_db(_enabled_profiles)
    except Exception:
        candidates = []
    main_id = (main or {}).get("id")
    for prof in candidates:
        if prof.get("id") == main_id or prof.get("id") == pinned:
            continue
        if lookup_vision_capability(
            prof.get("model"), prof.get("base_url") or "", allow_network=True
        ) is True:
            return prof
    return None


def _enabled_profiles(conn) -> list[dict[str, Any]]:
    from app.models.mixins import llm_profiles as llm_profiles_store

    rows = conn.execute(
        "SELECT id FROM llm_profiles WHERE enabled=1 ORDER BY created_at ASC"
    ).fetchall()
    return [p for r in rows if (p := llm_profiles_store.get_profile(conn, r["id"]))]


def vision_client(agent_id: str | None):
    """An :class:`LLMClient` for image description, or None when no vision
    profile resolves. Pin → capable main → first capable enabled profile."""
    profile = resolve_vision_profile(agent_id)
    if not profile:
        return None
    from app.runtime.llm import get_llm

    try:
        return get_llm(profile_id=profile["id"], model=profile['model'])
    except Exception as exc:
        logger.debug("vision client for profile %s failed: %s", profile.get("id"), exc)
        return None


_DESCRIBE_PROMPT = (
    "Describe everything visible in this image in thorough detail. "
    "Include any text, code, UI, data, objects, people, layout, colors, "
    "and other notable visual information."
)


async def analyze_image_data_url(
    agent_id: str | None, data_url: str, question: str = ""
) -> str:
    """Describe a ``data:`` image URL with the resolved vision profile.

    Returns the description text; raises on resolution/API failure so the
    caller can surface a useful error or skip the image.
    """
    from app.runtime.llm.base import LLMResponse

    client = vision_client(agent_id)
    if client is None:
        raise RuntimeError(
            "No vision-capable model profile is configured. Set a vision "
            "profile under System → Models (vision_profile_id) or add an "
            "image-capable profile."
        )
    prompt = question.strip() or _DESCRIBE_PROMPT
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    resp: LLMResponse = await client.complete(messages)
    return (resp.content or "").strip()


def reset_vision_caches() -> None:
    """Drop in-process capability caches (tests)."""
    _OLLAMA_CACHE.clear()
    from app.runtime.llm.models_dev import reset_catalog_cache

    reset_catalog_cache()


__all__ = [
    "model_supports_vision",
    "lookup_vision_capability",
    "normalize_model_id",
    "resolve_model_id",
    "agent_supports_vision",
    "decide_image_input_mode",
    "resolve_vision_profile",
    "vision_client",
    "analyze_image_data_url",
    "reset_vision_caches",
]
