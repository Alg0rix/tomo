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
| Spec (`docs/superpowers/specs/…`) | **written — awaiting user review** |
| Plan (`docs/superpowers/plans/…`) | pending |
| Cline execution loop | pending |

---

## Context snapshot (2026-07-26)

- Repo: `Alg0rix/tomo` — FastAPI UI shell + JSON store stubs
- Scaffold exists: `app/runtime/`, `models/`, `channels/`, `cli/`, `tools/`, etc. (empty stubs)
- Live path today: `app/services/store.py` + `chat.py` + full Jinja UI
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
