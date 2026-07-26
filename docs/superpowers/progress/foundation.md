# Tomo Foundation — Progress Log

**Started:** 2026-07-26  
**Roles:** Cursor (plan + review) · Cline (implement)  
**Process:** Superpowers brainstorming → design spec → implementation plan → Cline task loop

---

## Working agreement

| Role | Owns |
|------|------|
| **Cursor (this agent)** | Brainstorm, design specs, implementation plans, task briefs for Cline, code review of Cline diffs, progress docs, go/no-go between tasks |
| **Cline** | All code implementation, tests, commits for assigned tasks only |
| **Human** | Approvals at design/spec/plan gates; clarifications |

Cline is invoked with a **task brief** (plan section + files + acceptance criteria). Cursor does **not** write production code for foundation tasks unless Cline is blocked and the human asks.

**Per-task loop (autonomous):** Review → Adversarial review → Fix (if needed) → Next task. Do not wait for human "go" between tasks once the foundation run is underway.

---

## Status

| Phase | Status |
|-------|--------|
| Explore context | done |
| Clarifying questions | **done** (D/B/B/B/B) |
| Approaches | **done** — Approach 1 (layer-by-layer) |
| Design sections | **done** (§1–§4 approved) |
| Spec (`docs/superpowers/specs/…`) | **approved** |
| Plan (`docs/superpowers/plans/…`) | **written** |
| Cline execution loop | **Task 1–7 done** (+ adversarial fix passes) · **foundation vertical complete** |

---

## Context snapshot (2026-07-26)

- Repo: `Alg0rix/tomo` — FastAPI UI shell + JSON store stubs
- Scaffold exists: `app/runtime/`, `models/`, `channels/`, `cli/`, `tools/`, etc. (empty stubs)
- Live path today: hybrid SQLite store → mock/openai LLM → calculator → agent loop → web SSE
- Roadmap: coordinator, learning loop, memory, connector, channels, skills, observability
- Constraint from prior discussion: keep core small — **swarm + workplaces + few channels**; not feature-parity with reference platforms

---

## Decisions log

| Date | Decision |
|------|----------|
| 2026-07-26 | Foundation = **D) Thin vertical** — SQLite + one agent loop + web channel only (smallest end-to-end real path) |
| 2026-07-26 | LLM = **B)** OpenAI-compatible HTTP client + mock for tests (`TOMO_LLM_*`) |
| 2026-07-26 | Tools = **B)** one tool: `calculator` (JSON + Python backend) |
| 2026-07-26 | Persistence = **B)** adapter — store API on SQLite underneath |
| 2026-07-26 | Swarm = **B)** multi-agent session shape in DB; only coordinator runs for now |
| 2026-07-26 | Build = **Approach 1** layer-by-layer (SQLite → LLM → tools → loop → web wire) |
| 2026-07-26 | Constraint: **modular files** — no god-files; split by responsibility |

## Task reviews

| Task | Commit | Verdict |
|------|--------|---------|
| 1 Config + schema | `17cc4ea` | **PASS** |
| 2 Hybrid store facade | `f7b5297` | **PASS** (acceptance) |
| 2b Adversarial fix | `48e9afc` | **PASS** |
| 3 LLM mock + openai_compat | `e636884` | **PASS** |
| 4 Calculator + registry | `788acce` | **PASS** |
| 3–4 Adversarial fix | `057c2cf` | **PASS** |
| 5 Agent turn loop | `6f3b39b` | **PASS** |
| 5b Adversarial fix | `9b620b1` | **PASS** |
| 6 Web + chat SSE wiring | `a2542ac` | **PASS** |
| 6b Adversarial fix | `cb4b804` | **PASS** |
| 7 Docs closeout | `bd71df2` | **PASS** |

---

## Session notes

- 2026-07-26: Started Superpowers brainstorming. Progress dirs created.
- 2026-07-26: Q1 → **D** (thin vertical). Asking Q2 (LLM for first slice).
- 2026-07-26: Q2 → **B** (OpenAI-compatible + mock). Asking Q3 (tools in first slice).
- 2026-07-26: Q3 → **B** (calculator only). Asking Q4 (persistence cutover).
- 2026-07-26: Q4 → **B** (store API → SQLite). Asking Q5 (swarm vs single for vertical).
- 2026-07-26: Q5 → **B** (session shape kept; coordinator-only execution). Presenting approaches.
- 2026-07-26: Approach **1** locked. Presenting design §1 (architecture).
- 2026-07-26: §1 architecture approved. Presenting §2 (components / data).
- 2026-07-26: §2 approved + modular-files constraint. Presenting §3 (flow / errors / modularity).
- 2026-07-26: §3 approved. Presenting §4 (testing / success criteria).
- 2026-07-26: §4 approved. Spec written to `docs/superpowers/specs/2026-07-26-foundation-thin-vertical-design.md`. Awaiting user review before plan.
- 2026-07-26: Spec approved. Plan written to `docs/superpowers/plans/2026-07-26-foundation-thin-vertical.md` (review caveats included). Ready for Cline Task 1.
- 2026-07-26: Dispatched Cline for **Task 1** (schema + config). Brief: `docs/superpowers/handoffs/task-01-cline-brief.md`.
- 2026-07-26: **Task 1 REVIEW PASS** — commit `17cc4ea`. Tests 2 passed. Files modular (db 28 / schema 76 lines). Minor note: FK on `sessions.coordinator_id` is fine; messages.id INTEGER AUTOINCREMENT OK. Proceed to Task 2 when ready.
- 2026-07-26: Dispatched Cline for **Task 2** (hybrid store). Brief: `docs/superpowers/handoffs/task-02-cline-brief.md`.
- 2026-07-26: **Task 2 REVIEW PASS** — commit `f7b5297`. Re-verified: 24 tests passed. Hybrid SQLite + `platform_data`; no `store.json` for core entities; busy in-memory; `rebind` + conftest `TOMO_DB_PATH`. Notes: `store.py` 283 lines (soft ~250, under 400 smell); seeded `message_count` without message rows (stub parity); UI fake log still mentions `store.json`. Proceed to Task 3 when ready.
- 2026-07-26: **Adversarial re-review Task 1–2** (defect-first, not acceptance). Prior PASS was checklist-only. Confirmed **P1**: `seed_if_empty` FK crash when agents non-empty without demo ids but sessions empty (delete all demo agents → create custom → restart/`rebind`). Also **P2**: dashboard `recent_agents` sort regresses (old: `created_at DESC`; new: `is_super DESC, created_at ASC`); stats/dashboard lock not atomic; `get_or_create_session` no agent validation → IntegrityError; `clear_session` creates empty session; `seed`↔`platform_data`↔`services` circular import if seed imported first. Connection-poison after IntegrityError **not** reproduced on Py3.13. Recommend fix pass before Task 3, or accept as debt.
- 2026-07-26: Dispatched Cline for **Task 2b fix pass**. Brief: `docs/superpowers/handoffs/task-02-fix-cline-brief.md`.
- 2026-07-26: **Task 2b REVIEW PASS** — commit `48e9afc`. Re-verified: 36 tests passed; P1 repro (custom-only agents + rebind) no longer crashes. Seed skips demo sessions unless `main`/`ops`/`research` exist + rollback on failure; dashboard recent sort + atomic snapshot; `get_or_create` validates agent; `clear_session` no-ops via `find_session`; lazy import in seed. Ready for Task 3.
- 2026-07-26: Dispatched Cline for **Task 3** (LLM mock + openai_compat). Brief: `docs/superpowers/handoffs/task-03-cline-brief.md`.
- 2026-07-26: **Task 3 REVIEW PASS** — commit `e636884`. Re-verified: LLM + models suites green (56 passed). Modular (`base`/`mock`/`openai_compat`/`get_llm`); missing API key raises `LLMConfigError`; httpx MockTransport tests; no chat/loop wiring. Adversarial note (non-blocking): mock treats any `=` in user text as calc trigger (per brief) — can false-positive on prose; tighten later if agent loop needs it. Ready for Task 4.
- 2026-07-26: Dispatched Cline for **Task 4** (calculator + registry). Brief: `docs/superpowers/handoffs/task-04-cline-brief.md`.
- 2026-07-26: **Task 4 REVIEW PASS** — commit `788acce`. Re-verified: tools+llm **69 passed**. Safe `ast` whitelist (no eval); unknown/invalid → error strings; registry loads `tools/*.json` + hardcoded calculator backend. Adversarial note (P3, non-blocking): int exponent cap (`_MAX_EXPONENT`) does not apply to float exponents (`2**1000.0` still evaluates). Ready for Task 5.
- 2026-07-26: **Adversarial review Tasks 3–4** (defect-first). Confirmed P1s: (1) nested `**` can bypass per-op exponent cap → huge int / DoS; (2) mock `_has_tool_result` is any historical `tool` message → second calc turn in a session never tools again (breaks Task 5 multi-turn under default mock). P2: whitespace API key accepted; non-dict JSON tool arguments; malformed `choices[0]` can leak AttributeError; complex result from `(-2)**0.5`. Awaiting fix-vs-debt decision before Task 5.
- 2026-07-26: Dispatched Cline for **Task 3–4 fix pass** (all P1/P2/P3). Brief: `docs/superpowers/handoffs/task-03-04-fix-cline-brief.md`.
- 2026-07-26: **Task 3–4 fix REVIEW PASS** — commit `057c2cf`. Re-verified: runtime **85 passed**; adversarial recheck: nested pow → error string (no raise); mock multi-turn calc tools again after new user; whitespace key rejected; null choices → LLMRequestError; non-dict args coerced to dict; complex rejected; tools=None suppresses calc; URL no double path; instance httpx client. Ready for Task 5.
- 2026-07-26: Dispatched Cline for **Task 5** (agent context + turn loop). Brief: `docs/superpowers/handoffs/task-05-cline-brief.md`.
- 2026-07-26: **Task 5 REVIEW PASS** — commit `6f3b39b`. Runtime suite **108 passed**. `context.py` maps history→OpenAI messages; `loop.py` `run_turn` yields thinking/tool/tool_result/final/error; max iterations → error; no SSE/HTTP. Minor note: tool error flag is `result.startswith("Error")`. Ready for Task 6.
- 2026-07-26: Human set autonomous loop: Review → Adversarial → Fix → Next (no wait).
- 2026-07-26: **Task 5 adversarial fix PASS** — `9b620b1`. Unpaired tool_calls get synthetic results; unique tool ids across iterations; setup errors → error events; surplus tool_outputs dropped; `user_message=None`; `Error:` prefix; Unicode prompt fallback. Proceeding to Task 6.
- 2026-07-26: Dispatched Cline for **Task 5 fix pass** (all adversarial P1/P2/P3). Brief: `docs/superpowers/handoffs/task-05-fix-cline-brief.md`. Awaiting Cursor review.
- 2026-07-26: **Task 5 fix pass — Cline done, ready for Cursor review.** Fixes: (P1) `context.py` emits synthetic `role: tool` `"Error: missing tool result"` for unpaired `tool_call` entries so no assistant `tool_calls` message dangles; (P1) `loop.py` `_with_ids` now draws empty tool-call ids from a turn-scoped `itertools.count()` (computed once per response, reused for assistant message + tool results) so `call_0` never repeats across rounds; (P1) setup (`get_llm`/`get_openai_tools`/`build_messages`) wrapped in try/except → `{"kind":"error","message":"Agent setup failed: …"}`, `run_turn` never raises; (P2) surplus `tool_output` rows dropped instead of mapped onto `calls[-1]` id; (P2) `user_message: str | None` (context already supported None); (P2) error flag is `str(result).startswith("Error:")`; (P3) system prompt read catches `(OSError, UnicodeError)`. Re-verified: runtime **117 passed** (full suite **153 passed**); +9 new tests (unpaired/surplus/partial pairing, distinct empty ids across 2 rounds, get_llm + get_openai_tools setup failures, `user_message=None` no duplicate, `Error:`-prefix specificity). No Task 6 changes. Commit msg: `fix: harden agent context pairing and turn error surfacing`.
- 2026-07-26: Dispatched Cline for **Task 6** (web channel + chat SSE wiring, coordinator-only). Brief: `docs/superpowers/handoffs/task-06-cline-brief.md`.
- 2026-07-26: **Task 6 REVIEW PASS** — commit `a2542ac`. Web chat runs the real agent loop over SSE (coordinator-only): `chat.py` maps loop kinds → SSE events (`thinking`/`tool`/`tool_result`/`delta`+`done`/`error`), emits `state` busy + `turn.start`, and persists `user`/`tool_call`/`tool_output`/`final` via `append_session_history`. `agent_ids` preserved. Integration test collects a `done` event with mock LLM + temp DB.
- 2026-07-26: **Task 6 adversarial fix PASS** — commit `cb4b804`. P1: `stream_turn_sse` `try/finally` only clears busy (no `yield` in `finally`); the trailing busy-false `state` is yielded after normal completion; `chat.py` and `app/api/stream.py` wrap turn/heartbeat generators in `contextlib.aclosing` so a client disconnect cascades to the synchronous busy clear instead of suspending until GC. P1: `chat.js` `error` listener splits named SSE `error` events (rendered as an agent error bubble) from transport failures ("Stream interrupted"). P2: `turn_id` restored on `done`; user/tool rows persisted before yielding. Re-verified runtime green.
- 2026-07-26: **Foundation thin vertical COMPLETE.** The autonomous Review → Adversarial → Fix → Next loop (no human gate between tasks) carried Tasks 1–6 through every fix pass. Live path: SQLite store (`app/models/`) → mock/openai_compat LLM (`app/runtime/llm/`) → `calculator` tool (`app/runtime/tools/`) → coordinator-only agent turn loop (`app/runtime/agent/`) → web chat over SSE (`app/channels/web.py` + `app/services/chat.py`). Task 7 = docs closeout (this log + `README.md` + `docs/architecture.md`).
