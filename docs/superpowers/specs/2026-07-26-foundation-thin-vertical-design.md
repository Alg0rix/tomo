# Tomo Foundation — Thin Vertical Design

**Date:** 2026-07-26  
**Status:** Approved in brainstorming; awaiting final user review of this document  
**Roles:** Cursor plans/reviews · Cline implements  

---

## 1. Goal

Ship the smallest end-to-end **real** path:

**Web chat → SQLite-backed store → coordinator agent loop → OpenAI-compatible LLM (or mock) → one tool (`calculator`) → SSE + persisted history.**

Preserve multi-agent session shape in the DB; only the **coordinator** executes in this slice.

---

## 2. Decisions (locked)

| Topic | Choice |
|-------|--------|
| Slice | Thin vertical (SQLite + one agent loop + web channel) |
| LLM | OpenAI-compatible HTTP client + mock for tests (`TOMO_LLM_*`) |
| Tools | `calculator` only |
| Persistence | Keep `store` public API; implement on SQLite underneath |
| Swarm | Sessions keep `agent_ids` / `coordinator_id`; only coordinator runs |
| Build order | Layer-by-layer: SQLite → LLM → tools → loop → web wire |
| Modularity | No god-files; prefer ~150–250 lines; smell at ~400+ |

---

## 3. Architecture

```text
Web UI / SSE
    ↓
app/api + app/web  (thin)
    ↓
app/services/store  (facade → SQLite mixins)
    ↓
app/channels/web → app/runtime/agent/loop
    ↓
LLM (OpenAI-compatible | mock)  +  tools/registry → calculator
    ↓
app/models (SQLite)
```

**Rules:**
- Surfaces stay thin; logic lives in `runtime/` and `models/`
- After cutover, no silent fallback to `store.json` for agents/sessions/messages
- Platform seed entities (plugins, eval, workplaces, …) may remain in `platform_data` for this slice

---

## 4. Components & file map

| Path | Responsibility |
|------|----------------|
| `app/models/db.py` | Connection + path only |
| `app/models/schema.py` | DDL + migrate |
| `app/models/mixins/agents.py` | Agents CRUD |
| `app/models/mixins/sessions.py` | Sessions + `session_agents` |
| `app/models/mixins/messages.py` | History entries (new) |
| `app/models/mixins/settings.py` | Settings |
| `app/services/store.py` | Thin facade over mixins (same public methods) |
| `app/runtime/llm/base.py` | LLM protocol / types |
| `app/runtime/llm/openai_compat.py` | HTTP client |
| `app/runtime/llm/mock.py` | Deterministic mock |
| `app/runtime/tools/registry.py` | Load JSON + dispatch |
| `app/runtime/tools/calculator.py` | Calculator backend |
| `app/runtime/agent/context.py` | History → model messages |
| `app/runtime/agent/loop.py` | Turn orchestration only |
| `app/channels/web.py` | Web entry: session + message → loop |
| `app/services/chat.py` | SSE wiring only |

**Cline must not** collapse these into one large file. If a file grows past ~400 lines during implementation, split before merge.

---

## 5. Data model (foundation tables)

- **agents** — id, name, description, model_id, enabled, is_super, tool_count, channel_count, skill_count, created_at  
- **sessions** — id, coordinator_id, user_id, title, message_count, created_at, updated_at  
- **session_agents** — session_id, agent_id, position  
- **messages** — id, session_id, type, content, agent_id, function, params_json, error, ts  
- **settings** — key, value_json  

DB file default: `var/tomo.db` (gitignored via `var/`). Configurable via env if needed.

**Seed:** empty DB loads the same four demo agents and sample sessions used today.

Message `type` values align with existing `ChatEntry`: `user`, `final`, `thinking`, `tool_call`, `tool_output`, `intermediate`, `error`, `delegate`.

---

## 6. Data flow

1. Chat API/SSE receives user message for a session  
2. `chat.py` loads session via store; resolves `coordinator_id`  
3. `channels/web` builds turn context (agent + history)  
4. `agent/loop` calls LLM with calculator tool schema  
5. On tool call → registry → calculator → append tool_output → LLM again  
6. Persist each entry; stream SSE events matching current UI shapes  
7. Clear busy state on success or error  

**Max tool iterations:** 6 (same spirit as CLI retries default).

---

## 7. Configuration

| Env | Purpose |
|-----|---------|
| `TOMO_LLM_PROVIDER` | `mock` (default for tests) or `openai_compat` |
| `TOMO_LLM_BASE_URL` | OpenAI-compatible base URL |
| `TOMO_LLM_API_KEY` | API key |
| `TOMO_LLM_MODEL` | Model id |
| `TOMO_DB_PATH` | Optional override for SQLite path |

---

## 8. Errors

- LLM/network failure → emit `error` history entry; session not left busy  
- Unknown / failed tool → `tool_output` with error; loop stops or continues per max iterations  
- DB failure → API 500; no dual-write to JSON  

---

## 9. Testing

- Unit: models CRUD (temp SQLite), mock LLM shapes, calculator, agent loop (text-only + tool path)  
- Integration: store adapter round-trip; optional SSE smoke with mock  
- Default provider in tests: `mock` (no API key)

---

## 10. Success criteria

1. UI agents/sessions load from SQLite via store  
2. Web chat with mock LLM streams `final` and persists history  
3. Math prompt triggers calculator `tool_call` → `tool_output` → `final`  
4. Real `TOMO_LLM_*` works against an OpenAI-compatible endpoint  
5. Modular file map respected; no new god-files  
6. Progress log updated as each layer ships  

---

## 11. Out of scope

- Telegram / WhatsApp / Discord  
- Tomo Connector  
- Memory / knowledge / learning loop  
- Real swarm delegation beyond coordinator-only  
- Multi-provider LLM registry  
- Migrating plugins, eval, workplaces, schedules into SQLite  
- Fyne / Go connector work  

---

## 12. Implementation process

1. Cursor writes implementation plan (`docs/superpowers/plans/…`) with bite-sized tasks  
2. For each task: Cursor writes a Cline brief → Cline implements → Cursor reviews → progress log update  
3. Cursor does not write production implementation for these tasks unless Cline is blocked and the human asks  

---

## 13. Spec self-review

- No TBD/TODO placeholders left in requirements  
- Decisions consistent with architecture and file map  
- Scope limited to one vertical (not full roadmap)  
- Modularity explicit for implementers  
