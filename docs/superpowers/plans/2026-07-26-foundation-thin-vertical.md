# Foundation Thin Vertical Implementation Plan

> **Status: DONE** (2026-07-26) — Tasks 1–7 complete (+ adversarial fix passes). Progress: `docs/superpowers/progress/foundation.md`.

> **For agentic workers:** REQUIRED process: Cursor writes a Cline brief per task → Cline implements → Cursor reviews → update `docs/superpowers/progress/foundation.md`. Do not collapse modular files. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Web chat → SQLite store adapter → coordinator-only agent loop → OpenAI-compatible/mock LLM → `calculator` tool → SSE + persisted history.

**Architecture:** Layer-by-layer. Core entities (agents, sessions, messages, settings) move to SQLite behind the existing `store` API. Platform lists (plugins, eval, workplaces, …) stay on `platform_data`. Runtime is modular under `app/runtime/`. Wire format stays the current SSE events in `chat.py`.

**Tech Stack:** Python 3.12+, FastAPI, SQLite (`sqlite3` stdlib), httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-07-26-foundation-thin-vertical-design.md`

## Global Constraints

- Modular files: prefer ~150–250 lines; split before ~400.
- Cursor plans/reviews; Cline implements production code for these tasks.
- No silent JSON fallback for agents/sessions/messages after cutover.
- No migrate-from-`store.json`: empty DB seeds; existing `store.json` is ignored once SQLite is live.
- Busy state: process-local in-memory set (not SQLite) for foundation.
- LLM: full completion first (not true token streaming). Optional: chunk `done` content into `delta` events for UI polish.
- SSE wire events (UI contract — do not rename): `state`, `turn.start`, `thinking`, `tool`, `tool_result`, `delta`, `done`, `error`, `delegate`, `heartbeat`.
- History entry types (DB/API): `user`, `final`, `thinking`, `tool_call`, `tool_output`, `intermediate`, `error`, `delegate`. Map `tool`↔`tool_call`, `tool_result`↔`tool_output` at the chat boundary.
- Hybrid store: SQLite for agents/sessions/session_agents/messages/settings; `platform_data` for tools/skills/plugins/workplaces/schedules/models/providers/safety/users/shared_channels/eval_*.
- Max tool iterations: 6.
- Default `TOMO_LLM_PROVIDER=mock` in tests.

---

## File map (create / modify)

| Path | Role |
|------|------|
| Create: `app/models/db.py` | Connect + `get_connection()` |
| Create: `app/models/schema.py` | DDL + `migrate()` |
| Create: `app/models/seed.py` | Seed agents/sessions into empty DB |
| Modify: `app/models/mixins/agents.py` | Agents CRUD |
| Modify: `app/models/mixins/sessions.py` | Sessions + session_agents |
| Create: `app/models/mixins/messages.py` | Message history CRUD |
| Modify: `app/models/mixins/settings.py` | Settings get/update |
| Create: `app/models/mixins/busy.py` | Process-local busy set helpers (optional thin module) |
| Rewrite thin: `app/services/store.py` | Facade only — delegate to mixins / platform_data |
| Create: `app/runtime/llm/__init__.py` | `get_llm()` factory |
| Create: `app/runtime/llm/base.py` | Types + protocol |
| Create: `app/runtime/llm/openai_compat.py` | httpx client |
| Create: `app/runtime/llm/mock.py` | Deterministic mock |
| Create: `app/runtime/tools/calculator.py` | Eval arithmetic safely |
| Modify: `app/runtime/tools/registry.py` | Load JSON + dispatch |
| Modify: `app/runtime/agent/context.py` | History → LLM messages |
| Modify: `app/runtime/agent/loop.py` | Turn loop; yields structured events |
| Modify: `app/channels/web.py` | Session + message → loop |
| Rewrite: `app/services/chat.py` | SSE mapping + coordinator-only |
| Modify: `app/core/config.py` | DB + LLM env vars |
| Modify: `pyproject.toml` | Add httpx, pytest |
| Create: `tests/unit/models/…` | CRUD tests |
| Create: `tests/unit/runtime/…` | LLM/tools/loop tests |
| Create: `tests/integration/test_store_sqlite.py` | Store facade round-trip |
| Create: `tests/integration/test_chat_mock.py` | Chat SSE + mock LLM |

---

## Task status summary

| Task | Feat commit | Fix commits | Status |
|------|-------------|-------------|--------|
| 1 Config + schema | `17cc4ea` | — | **done** |
| 2 Hybrid store facade | `f7b5297` | `48e9afc` | **done** |
| 3 LLM mock + openai_compat | `e636884` | `057c2cf` (w/ 4) | **done** |
| 4 Calculator + registry | `788acce` | `057c2cf` (w/ 3) | **done** |
| 5 Agent turn loop | `6f3b39b` | `9b620b1` | **done** |
| 6 Web + chat SSE | `a2542ac` | `cb4b804` | **done** |
| 7 Docs closeout | `bd71df2` | `d5ae967` | **done** |

---

### Task 1: Config, deps, SQLite connection + schema — DONE

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/core/config.py`
- Create: `app/models/db.py`
- Create: `app/models/schema.py`
- Test: `tests/unit/models/test_schema.py`

**Interfaces:**
- Produces: `DB_PATH`, `get_connection()`, `migrate(conn)`, env LLM/DB settings

- [x] **Step 1: Add dependencies**
- [x] **Step 2: Extend config**
- [x] **Step 3: Write failing schema test**
- [x] **Step 4: Implement `db.py` + `schema.py`**
- [x] **Step 5: Re-run test — PASS**
- [x] **Step 6: Commit** (`17cc4ea`)

---

### Task 2: Model mixins + seed + thin store facade (hybrid) — DONE

**Files:**
- Create: `app/models/seed.py`
- Modify: `app/models/mixins/agents.py`, `sessions.py`, `settings.py`
- Create: `app/models/mixins/messages.py`
- Create: `app/models/mixins/busy.py` (in-memory set)
- Rewrite: `app/services/store.py` (facade ≤ ~250 lines)
- Test: `tests/unit/models/test_agents.py`, `test_sessions_messages.py`
- Test: `tests/integration/test_store_sqlite.py`

**Interfaces:**
- Consumes: `get_connection()`, `migrate()`
- Produces: store methods for agents/sessions/history/settings backed by SQLite; platform_* still from `platform_data`

- [x] **Step 1: Failing tests for agent create/list and session history append**
- [x] **Step 2: Implement mixins**
- [x] **Step 3: Seed** (`seed_if_empty`)
- [x] **Step 4: Rewrite `store.py` as facade**
- [x] **Step 5: Integration test**
- [x] **Step 6: Manual smoke**
- [x] **Step 7: Commit** (`f7b5297`); adversarial fix `48e9afc`

---

### Task 3: LLM client (mock + openai_compat) — DONE

**Files:**
- Create: `app/runtime/llm/base.py`, `mock.py`, `openai_compat.py`, `__init__.py`
- Test: `tests/unit/runtime/llm/test_mock.py`, `test_factory.py`, `test_openai_compat.py`

**Interfaces:**
- Produces: `ToolCall`, `LLMResponse`, `LLMClient`, `get_llm()`

- [x] **Step 1: Failing tests** — mock calc two-step + factory
- [x] **Step 2: Implement mock + openai_compat**
- [x] **Step 3: Factory `get_llm()`**
- [x] **Step 4: Commit** (`e636884`); shared adversarial fix with Task 4: `057c2cf`

---

### Task 4: Tool registry + calculator — DONE

**Files:**
- Modify: `tools/calculator.json`
- Create: `app/runtime/tools/calculator.py`
- Modify: `app/runtime/tools/registry.py`
- Test: `tests/unit/runtime/tools/test_calculator.py`, `test_registry.py`

**Interfaces:**
- Produces: `get_openai_tools() -> list[dict]`, `execute(name: str, arguments: dict) -> str`

- [x] **Step 1: Failing tests**
- [x] **Step 2: Implement calculator** (safe `ast` whitelist)
- [x] **Step 3: Registry loads `tools/*.json` + calculator backend**
- [x] **Step 4: Commit** (`788acce`); adversarial fix `057c2cf`

---

### Task 5: Agent context + loop (coordinator turn) — DONE

**Files:**
- Modify: `app/runtime/agent/context.py`
- Modify: `app/runtime/agent/loop.py`
- Test: `tests/unit/runtime/agent/test_loop.py`, `test_context.py`

**Interfaces:**
- Produces: async generator events (`thinking` / `tool` / `tool_result` / `final` / `error`)

- [x] **Step 1: Failing tests with mock LLM**
- [x] **Step 2: `context.py`** — history → OpenAI messages + system prompt
- [x] **Step 3: `loop.py`** — orchestration only
- [x] **Step 4: Commit** (`6f3b39b`); adversarial fix `9b620b1`

---

### Task 6: Web channel + chat SSE wiring (coordinator-only) — DONE

**Files:**
- Modify: `app/channels/web.py`
- Rewrite: `app/services/chat.py`
- Test: `tests/integration/test_chat_mock.py`

**Interfaces:**
- Consumes: loop events
- Produces: SSE stream compatible with existing UI JS

- [x] **Step 1: Document mapping in code comments** (loop kind → SSE)
- [x] **Step 2: Coordinator-only**
- [x] **Step 3: Persist** user before loop; tool/final/error via `append_session_history`
- [x] **Step 4: Integration test** with mock LLM + temp DB
- [x] **Step 5: Manual** — UI chat with mock
- [x] **Step 6: Commit** (`a2542ac`); adversarial fix `cb4b804`

---

### Task 7: Docs + progress closeout — DONE

**Files:**
- Modify: `docs/superpowers/progress/foundation.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [x] **Step 1: Update progress** — all layers done; commits listed
- [x] **Step 2: README** — mock vs openai_compat + `TOMO_DB_PATH`
- [x] **Step 3: Commit** (`bd71df2`); finalize `d5ae967`

---

## Self-review vs spec

| Spec requirement | Task | Done |
|------------------|------|------|
| SQLite adapter | 1–2 | yes |
| OpenAI-compat + mock | 3 | yes |
| calculator only | 4 | yes |
| Agent loop | 5 | yes |
| Web + SSE | 6 | yes |
| Modular files | Global + file map | yes |
| Hybrid platform_data | Task 2 | yes |
| SSE contract / no JSON migrate / busy / httpx / full completion | Global Constraints | yes |
| Docs closeout | 7 | yes |

No TBD placeholders. Types consistent across tasks (`LLMResponse`, SSE mapping table).

---

## Execution handoff

**Plan executed and closed.** Live path: SQLite → mock/openai LLM → calculator → coordinator loop → web SSE.

**Process used:** For each Task 1→7, Cursor posted a Cline brief → Cline implemented → Cursor reviewed → adversarial review → fix pass when needed → progress log → next task (autonomous after human enabled Review→Adversarial→Fix→Next).
