# OpenAI-compat + session POST SSE API

**Date:** 2026-08-01  
**Status:** approved (approach 1)

## Goal

Expose Hermes-style integration endpoints for API clients:

1. `POST /v1/chat/completions` — OpenAI Chat Completions (stream + non-stream)
2. `POST /api/sessions/{session_id}/chat/stream` — Tomo turn SSE with JSON body

Keep existing GET EventSource streams for the web UI.

## Decisions

| Topic | Choice |
|-------|--------|
| Scope | Minimal subset only (no `/v1/responses`, `/v1/runs`) |
| Agent selection | `model` = Tomo agent id |
| Session | Default: `get_or_create_session(agent_id, auth_user_id)` (solo). Optional request header `X-Tomo-Session-Id` continues that session (solo or swarm). |
| History source | Session DB; request `messages` only supply the last user turn |
| Non-stream | Supported (`stream: false` → one JSON body) |
| Session SSE events | Existing Tomo vocabulary (`delta`, `done`, `tool`, …) |
| Auth | Existing `AuthDep` (cookie / Bearer / `X-API-Key`) |
| `/v1` auth errors | JSON 401 (same as `/api`), not login redirect |

## Contracts

### `POST /v1/chat/completions`

- Body: OpenAI-shaped `{model, messages, stream?}`
- Request header `X-Tomo-Session-Id` (optional): continue existing session; 404 if missing
- Without header: 404 if agent (`model`) missing; creates/reuses solo session
- 400 if no user message
- Response header `X-Tomo-Session-Id` on success
- Stream: OpenAI `chat.completion.chunk` SSE + final `data: [DONE]`
- Non-stream: `chat.completion` JSON with assistant `content` from turn `done`/`delta`
- Note: OpenAI wire format still only forwards text deltas (not subagent events)

### `POST /api/sessions/{session_id}/chat/stream`

- Body: `{message: str, attachment_ids?: string[]}`
- 404 if session missing; 400 if empty message and no attachments
- `text/event-stream` with Tomo `event:` / `data:` frames (same as GET stream)

## Out of scope

`/v1/models`, `/v1/responses`, Hermes event renames, changing UI GET streams.
