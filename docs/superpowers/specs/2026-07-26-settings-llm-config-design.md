# Settings-backed LLM config

**Date:** 2026-07-26  
**Status:** Approved (user: settings win · no env · SQLite API key · no mock provider)  
**Roles:** Cursor plans/implements  

---

## 1. Goal

Make the live agent LLM configurable from **System → Models**, with SQLite as the only source of truth. Remove the user-facing **mock** provider. Chat fails clearly until an API key (and base URL / model) is configured.

---

## 2. Decisions (locked)

| Topic | Choice |
|-------|--------|
| Source of truth | SQLite settings (not env) |
| Provider | OpenAI-compatible only — **no mock in product** |
| API key storage | SQLite; GET returns masked; blank PUT keeps existing |
| UI surface | `/system#models` editable form (replace read-only stub) |
| Unconfigured | Seed defaults with empty key; `get_llm()` / chat errors with “Configure LLM in System → Models” |
| `max_tool_iterations` | Already in settings; agent loop reads it from store |
| `default_model` | Alias of / sync with `llm_model` |
| Tests | Keep `MockLLMClient` as an injectable test double only — not a factory provider |

---

## 3. Settings keys

| Key | Default | Notes |
|-----|---------|-------|
| `llm_base_url` | `https://api.openai.com/v1` | Required for chat |
| `llm_api_key` | `""` | Required for chat; masked on GET |
| `llm_model` | `gpt-4o-mini` | Also written to `default_model` on save |
| `max_tool_iterations` | `12` (existing) | Used by agent loop |

No `llm_provider` field — always OpenAI-compatible.

### Masking

- GET `/api/settings`: if key length > 4, return `••••` + last 4; if empty, `""`; never return full secret.
- Response may include `llm_api_key_set: true|false` for UI.
- PUT: if `llm_api_key` is missing, `null`, or `""`, do not overwrite stored key.

---

## 4. Runtime

```text
get_llm() → store.get_settings()
         → if no api_key: raise LLMConfigError("Configure LLM in System → Models")
         → OpenAICompatClient(base_url, api_key, model)
```

- Remove `mock` branch from `get_llm()`.
- Stop reading `TOMO_LLM_*` for the live path (delete or leave unused in `config.py` for one release — prefer delete from live docs; tests use injection / temp settings).
- Agent loop: `max_iterations` from `store.get_settings()["max_tool_iterations"]` when not passed explicitly.
- Setup / config errors already become SSE `error` events — ensure message is user-actionable.

---

## 5. UI

**Models** section (`partials/settings/models.html` + `system.js`):

- Base URL (text)
- API key (password; placeholder shows masked or “not set”)
- Model id (text; free-form for any compatible host)
- Save → `PUT /api/settings`
- Short help: “OpenAI-compatible endpoint (OpenAI, OpenRouter, vLLM, LM Studio, …)”
- Optional: demote or keep the old read-only catalog table below as reference — prefer remove toggles that pretend to enable providers

**General:** keep `max_tool_iterations` (and theme etc.). `default_model` select can stay in sync with `llm_model` or be removed from General to avoid two sources — prefer **single model field on Models**; General drops model select or mirrors read-only.

---

## 6. Seed / migration

- Extend `seed_settings` / empty-DB seed with `llm_base_url`, `llm_api_key`, `llm_model`.
- Existing DBs: `update_settings` / `get_settings` should merge missing keys with defaults (or one-shot migrate on read) so upgrades don’t crash.

---

## 7. Testing

- Unit: mask on get; blank put keeps key; `get_llm` raises without key; `get_llm` builds client from store when key set
- Loop: still uses injected `MockLLMClient` in unit tests (no factory mock)
- Integration chat tests: inject llm or set store settings + httpx MockTransport
- API: PUT/GET settings round-trip with auth override

---

## 8. Out of scope

- Multi-provider registry / per-agent model overrides beyond stored default
- Encrypting the API key at rest (local single-admin trust model)
- Setup wizard redirect
- Telegram / other channels
- Removing `MockLLMClient` file (kept for tests)

---

## 9. Spec self-review

- No TBD placeholders  
- Decisions match user locks (settings, no env, SQLite key, no mock provider)  
- Scope is one vertical: settings → get_llm → UI Models tab  
