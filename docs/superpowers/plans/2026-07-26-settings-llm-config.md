# Settings-backed LLM config — Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Drive the live OpenAI-compatible LLM from System → Models (SQLite settings); remove the mock provider from the product path.

**Architecture:** Settings mixin masks/keeps API keys; `get_llm()` builds `OpenAICompatClient` from `store.get_settings()`; Models tab saves via existing `PUT /api/settings`. Tests inject `MockLLMClient` or set store + httpx transport — never via a factory “mock” provider.

**Tech Stack:** FastAPI, SQLite settings KV, Jinja `/system`, `system.js`, httpx OpenAI client.

## Global Constraints

- No user-facing mock provider; no env as live config source.
- GET never returns full `llm_api_key`; blank PUT keeps existing key.
- Unconfigured key → `LLMConfigError` with “Configure LLM in System → Models”.
- Keep `MockLLMClient` file for unit-test injection only.

---

### Task 1: Settings keys + masking

**Files:**
- Modify: `app/services/platform_data.py` (`seed_settings`)
- Modify: `app/models/mixins/settings.py` (defaults merge, mask on get, keep-on-blank put)
- Modify: `app/services/store.py` if facade needs thin wrappers
- Test: `tests/unit/models/test_llm_settings.py`

**Interfaces:**
- Produces: `get_settings()` includes `llm_base_url`, `llm_api_key` (masked), `llm_api_key_set`, `llm_model`, `max_tool_iterations`
- Produces: `update_settings({"llm_api_key": ""})` does not clear stored key; full key stored when non-empty

- [x] **Step 1: Failing tests** for mask, keep-on-blank, seed defaults
- [x] **Step 2: Implement defaults + mask/keep logic**
- [x] **Step 3: Tests pass; commit** `feat: persist LLM settings with masked API key`

---

### Task 2: `get_llm()` from store; drop mock factory

**Files:**
- Modify: `app/runtime/llm/__init__.py`
- Modify: `app/runtime/llm/openai_compat.py` (accept explicit args; error message points to Settings)
- Modify: `app/runtime/agent/loop.py` (max iterations from store)
- Modify: `app/core/config.py` (remove live LLM env vars or leave unused — prefer remove from docs/README; strip factory use)
- Test: `tests/unit/runtime/llm/test_factory.py` (rewrite)
- Update: `tests/integration/test_chat_mock.py` to inject llm / store settings

**Interfaces:**
- Produces: `get_llm() -> OpenAICompatClient` reading store
- Produces: `LLMConfigError` if api key empty

- [x] **Step 1: Rewrite factory tests**
- [x] **Step 2: Implement store-backed `get_llm`**
- [x] **Step 3: Wire loop max_iterations from settings**
- [x] **Step 4: Fix chat/loop tests; commit** `feat: build LLM client from settings`

---

### Task 3: System → Models UI

**Files:**
- Modify: `app/templates/partials/settings/models.html`
- Modify: `app/templates/partials/settings/general.html` (drop duplicate model select or sync)
- Modify: `app/static/js/system.js`
- Modify: `app/web/pages.py` if template context needs settings LLM fields
- Docs: `README.md` LLM section

- [x] **Step 1: Models form + save JS**
- [x] **Step 2: Load masked key / key_set into form**
- [x] **Step 3: README update; commit** `feat: configure LLM on System Models page`

---

### Task 4: Verify

- [x] **Step 1:** `pytest -q` green
- [x] **Step 2:** Manual note — set key in UI, chat works against real endpoint

---

## Out of scope

Encrypting keys; per-agent model overrides; setup wizard redirect; deleting `mock.py`.
