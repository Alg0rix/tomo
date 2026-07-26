# Alpha Slice E — Memory / KB Implementation Plan

> Agentic workers: subagent-driven-development or executing-plans.

**Goal:** Knowledge entries in SQLite; `recall` tool; minimal Memory UI CRUD; seed facts.

**Do not** start server. No vector DB. Keyword/LIKE/FTS OK. No F–H.

## Tasks
1. `knowledge_entries` table + mixin CRUD + seed
2. `recall` tool (+ optional `remember` tool); wire registry
3. Minimal UI (System or Memory page) list/create/delete
4. Test: new session can recall seeded fact via MockLLM tool path
5. Progress + commit `feat: knowledge base with recall tool`

## Out of scope
Embeddings, auto skill distillation, Telegram.
