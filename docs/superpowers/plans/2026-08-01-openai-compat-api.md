# OpenAI-compat API Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Add `POST /v1/chat/completions` (OpenAI stream/non-stream) and `POST /api/sessions/{id}/chat/stream` (Tomo SSE).

**Architecture:** Thin FastAPI adapters over `run_session_turn` / session helpers. Map Tomo SSE `delta`/`done`/`error` into OpenAI chunks for `/v1`; reuse `fmt_sse` events for session POST stream.

**Tech Stack:** FastAPI, StreamingResponse, existing AuthDep + store + chat services.

## Global Constraints

- Do not change GET EventSource UI routes.
- `model` = agent id; session via `get_or_create_session`.
- `/v1/*` must return JSON 401/404 like `/api/*`.

---

### Task 1: Auth + 404 for `/v1`

**Files:** `app/core/deps.py`, `app/main.py`

- [ ] Treat paths starting with `/v1/` like `/api/` in `require_auth` (401 JSON)
- [ ] Treat `/v1/` like `/api/` in the 404 handler

### Task 2: OpenAI compat route

**Files:** `app/api/openai_compat.py`, `app/api/__init__.py`

- [ ] Implement `POST /v1/chat/completions`
- [ ] Helper: last user message text from `messages`
- [ ] Stream + non-stream via `run_session_turn`
- [ ] Mount router at app root (prefix `/v1`)

### Task 3: Session POST stream

**Files:** `app/api/stream.py`, `app/schemas/models.py` (if needed)

- [ ] `POST /api/sessions/{session_id}/chat/stream` with JSON body
- [ ] Stream Tomo SSE via `run_session_turn` + heartbeats optional

### Task 4: Tests

**Files:** `tests/integration/test_openai_compat_api.py`

- [ ] Bearer auth + non-stream completion
- [ ] Stream completion contains OpenAI chunks + `[DONE]`
- [ ] Session POST stream emits `delta`/`done`
- [ ] Unknown model → 404; unauthenticated → 401
