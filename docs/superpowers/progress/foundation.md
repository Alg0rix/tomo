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
| Cline execution loop | **Task 1–2 done + fix pass** · next Task 3 |

---

## Context snapshot (2026-07-26)

- Repo: `Alg0rix/tomo` — FastAPI UI shell + JSON store stubs
- Scaffold exists: `app/runtime/`, `models/`, `channels/`, `cli/`, `tools/`, etc. (empty stubs)
- Live path today: hybrid SQLite store (`app/services/store.py`) + `chat.py` + full Jinja UI
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
