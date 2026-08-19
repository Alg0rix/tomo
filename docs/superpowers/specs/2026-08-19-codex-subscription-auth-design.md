# Codex/ChatGPT subscription auth for LLM profiles

Date: 2026-08-19
Status: approved (via /goal directive — see conversation)

## Problem

Today `llm_profiles` (Alpha §2.2) only supports API-key-based OpenAI-compatible
endpoints (`app/models/mixins/llm_profiles.py`, `app/runtime/llm/openai_compat.py`).
Users with a ChatGPT Plus/Pro/Team subscription cannot use their subscription's
included Codex usage — they must pay separately for API tokens.

`tmp/hermes-agent` (a reference agent codebase, not part of tomo) solves this by
authenticating against OpenAI's Codex backend via OAuth (device-code flow or
importing `~/.codex/auth.json` from the Codex CLI) and talking to
`https://chatgpt.com/backend-api/codex` via the **Responses API**, not
chat/completions. This spec adopts that pattern into tomo.

## Scope (v1)

- ChatGPT/Codex subscription only. Data model generalized so future
  subscription-backed providers (e.g. a Claude Pro/Max equivalent) can be
  added without a schema change.
- Web UI device-code login (no CLI command).
- New Responses-API client implementing the existing `LLMClient` protocol.
- Proactive token refresh on profile resolve.
- Core client behavior only — no cross-issuer encrypted-reasoning replay,
  no Harmony tool-call-leak recovery, no xAI-style answer salvage. Those
  exist in hermes because it juggles many Responses-API backends (xAI,
  GitHub Copilot, Codex) simultaneously; tomo targets Codex/ChatGPT only.
- No self-heal-from-`~/.codex/auth.json` fallback. On unrecoverable refresh
  failure the profile is flagged `needs_reauth` and the user re-runs the
  login button.

## Data model

`llm_profiles` gets five new columns, added via the existing idempotent
`ALTER TABLE` migration pattern in `app/models/schema.py` (see the
`reasoning_efforts_json` migration for precedent):

```sql
ALTER TABLE llm_profiles ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'api_key';
ALTER TABLE llm_profiles ADD COLUMN subscription_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_profiles ADD COLUMN access_token TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_profiles ADD COLUMN refresh_token TEXT NOT NULL DEFAULT '';
ALTER TABLE llm_profiles ADD COLUMN token_expires_at REAL NOT NULL DEFAULT 0;
```

- `auth_mode`: `'api_key'` (default, existing behavior) | `'subscription'`.
- `subscription_provider`: `'openai-codex'` for now; empty for `api_key` rows.
- `access_token` / `refresh_token`: ciphertext at rest via
  `app.core.secrets.encrypt_secret` / `decrypt_secret` — same contract as
  the existing `api_key` column (never plaintext in the DB, masked in public
  views).
- `token_expires_at`: unix epoch seconds; `0` means unknown/never-refreshed.
- For `auth_mode='subscription'` rows, `base_url` is populated with
  `https://chatgpt.com/backend-api/codex` at creation time; `api_key` stays
  empty and unused.

`app/models/mixins/llm_profiles.py` changes:
- `_row_to_profile` / `_decrypt_profile` include the five new fields,
  decrypting `access_token`/`refresh_token` in `_decrypt_profile` only
  (runtime use), masking them in `public_profile` the same way `api_key` is
  masked today (`access_token_set` / `refresh_token_set` booleans, no raw
  value in public payloads).
- `resolve_profile()` gains the proactive-refresh step described below.
- New `save_subscription_tokens(conn, profile_id, access_token, refresh_token,
  expires_at)` helper — writes encrypted tokens back after a refresh or login.

## Login flow (web)

Two new endpoints in `app/api/platform.py`, backed by a new
`app/runtime/llm/codex_oauth.py` module (ported/trimmed from hermes'
`hermes_cli/auth.py` device-code + refresh functions):

- `POST /api/llm-profiles/codex-login/start`
  → `codex_oauth.start_device_login()`: POSTs to
  `https://auth.openai.com/api/accounts/deviceauth/usercode` with the public
  Codex client id (`app_EMoamEEZ73f0CkXaXp7hrann` — OpenAI's published Codex
  CLI client id, not a secret, same one hermes uses).
  Returns `{user_code, device_auth_id, verification_url, interval}` to the
  browser; nothing persisted yet.

- `POST /api/llm-profiles/codex-login/poll`
  Body: `{device_auth_id, user_code}`. Polls
  `.../deviceauth/token` once; frontend re-calls on the `interval` cadence
  from `start`. On success (`200`), exchanges the returned
  `authorization_code`/`code_verifier` for tokens at
  `https://auth.openai.com/oauth/token` (PKCE, `grant_type=authorization_code`),
  then creates or updates an `llm_profiles` row: `auth_mode='subscription'`,
  `subscription_provider='openai-codex'`, `base_url` defaulted, tokens
  encrypted and stored, `model` left for the user to fill in (e.g.
  `gpt-5-codex`). Returns the created/updated public profile.
  On `403`/`404` (not yet authorized) returns `{status: "pending"}` so the
  frontend keeps polling; on terminal failure returns an error the UI
  surfaces inline.

Settings UI (existing model-profile drawer, `app/web` templates touched by
the recent settings redesign) gets a "Sign in with ChatGPT" action next to
the manual API-key form. Clicking it calls `start`, shows the code plus a
clickable `verification_url` link, and polls client-side (setInterval at
the returned `interval`) until `poll` returns a profile or an error.

## Token refresh

`resolve_profile()` in `app/models/mixins/llm_profiles.py`: for a resolved
profile with `auth_mode == 'subscription'`, check
`token_expires_at - now() < 60` (60s skew). If expiring/expired, call
`codex_oauth.refresh_tokens(refresh_token)` synchronously, write the new
pair back via `save_subscription_tokens`, and return the updated profile.
`resolve_profile` is synchronous today (sqlite3, no async) — `codex_oauth`'s
HTTP calls use `httpx` synchronously here (mirrors hermes'
`refresh_codex_oauth_pure`, which is also sync) to avoid threading an event
loop through a currently-sync call path.

On unrecoverable failure (`invalid_grant`, `invalid_token`, HTTP 401/403,
or any non-retryable OAuth error), `resolve_profile` does not raise —
it returns the profile with `needs_reauth=True` in the dict so
`get_llm()` can raise a clear `LLMConfigError` ("ChatGPT sign-in expired —
reconnect in System → Models") instead of a confusing wire-level 401 from
the Responses API.

## New client — `app/runtime/llm/codex_responses.py`

Implements the existing `LLMClient` protocol (`complete`, `stream_complete`)
against the Responses API instead of chat/completions:

- **Message conversion**: tomo's internal `messages` (OpenAI chat-message
  shape: `role`, `content`, `tool_calls`, tool-role `tool_call_id`) convert
  to Responses `input` items (`{role, content}` for user/assistant text,
  `function_call` / `function_call_output` items for tool calls/results).
  A pure function `_chat_messages_to_responses_input`, ported and trimmed
  from hermes' `agent/codex_responses_adapter.py` (drop the encrypted-
  reasoning-replay and cross-issuer branches per the core-only scope
  decision).
- **Tools**: `_responses_tools()` converts tomo's `tools` (chat function-tool
  schema) to Responses `{type: "function", name, description, parameters}`
  items — same conversion hermes does, without the built-in-tool-type
  passthrough (tomo doesn't need xAI's server-side web_search yet).
- **Request**: `client.responses.create(model=..., instructions="", input=...,
  tools=..., store=False)` via `openai.AsyncOpenAI(base_url=..., api_key=
  access_token)` — the Responses API accepts the OAuth access token as the
  bearer `api_key` the SDK sends in `Authorization`.
- **Non-streaming `complete()`**: call `responses.create(stream=False,
  ...)`, normalize `response.output` items (`message` → content,
  `function_call` → `ToolCall`) into `LLMResponse`. Trimmed version of
  hermes' `_normalize_codex_response` (drop reasoning-item capture, phase
  handling, leak recovery, xAI salvage — keep only `message` and
  `function_call` item handling).
- **`stream_complete()`**: `client.responses.create(stream=True, ...)`,
  consume raw SSE events directly (never read
  `response.completed.response.output` for content — same structural-
  immunity reasoning as hermes' `_consume_codex_event_stream`, trimmed to
  just `response.output_text.delta` → `{"type": "delta", ...}` and
  `response.output_item.done` → accumulate tool-call items), yield a final
  `{"type": "done", "response": LLMResponse}`.
- **Errors**: reuse `LLMRequestError` / `format_llm_error` conventions from
  `openai_compat.py` so the UI's error surface stays consistent.
- **Usage**: Responses API usage object uses `input_tokens`/`output_tokens`
  — `openai_compat.parse_usage` already recognizes those aliases, reused
  as-is.

No context-window-fetch method for v1 (Codex models' context length can be
hardcoded/looked up statically later; the `/models` chat-completions-style
endpoint doesn't apply to the Responses API surface the same way).

## Wiring

`app/runtime/llm/__init__.py`'s `get_llm()`:

```python
profile = store.resolve_llm_profile(agent_id)
if not profile:
    raise LLMConfigError(...)
if profile.get("needs_reauth"):
    raise LLMConfigError("ChatGPT sign-in expired — reconnect in System → Models")
if profile.get("auth_mode") == "subscription":
    return CodexResponsesClient(
        base_url=profile.get("base_url") or DEFAULT_CODEX_BASE_URL,
        access_token=profile.get("access_token") or "",
        model=profile.get("model") or "gpt-5-codex",
        timeout=default_llm_timeout_seconds(),
    )
return OpenAICompatClient(...)  # existing path, unchanged
```

## Testing

- `codex_oauth.py`: device-code start/poll/token-exchange and refresh, all
  with `httpx` mocked (`respx` or the existing test double pattern used
  elsewhere in `tests/unit`) — success, pending (403/404), rate-limited
  (429), invalid_grant.
- `llm_profiles.py`: `resolve_profile` proactive-refresh branch (token
  fresh → no refresh call; expiring → refresh called and persisted;
  refresh fails terminally → `needs_reauth=True`).
- `codex_responses.py`: chat-message → Responses-input conversion (text,
  tool_calls, tool results), tool schema conversion, non-stream response
  normalization (text-only, tool-call, empty-output edge case), streaming
  delta assembly and final tool-call accumulation, usage parsing.
- `app/api/platform.py`: login start/poll endpoints against a mocked OAuth
  backend — happy path creates a profile with `auth_mode='subscription'`.

## Out of scope / explicitly deferred

- CLI login command.
- Separate `oauth_credentials` table (rejected in favor of columns on
  `llm_profiles` — see Data model).
- Global/singleton ChatGPT credential shared across profiles (rejected —
  each profile row holds its own token pair, same granularity as API-key
  profiles today).
- Cross-issuer encrypted-reasoning replay, tool-call-leak recovery, xAI
  answer salvage (hermes-specific multi-backend hardening, not applicable
  yet).
- Importing tokens from `~/.codex/auth.json` (Codex CLI's own store) as a
  fallback/self-heal path.
- Context-window auto-detection for Codex models.
