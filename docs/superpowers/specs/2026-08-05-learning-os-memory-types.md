# Learning OS memory types (SQLite + filesystem)

**Date:** 2026-08-05  
**Status:** Slice 1 + Slice 2 + Slice 3 implemented  
**Scope:** Typed memory lanes on Tomo's existing stack — no Qdrant/Neo4j/Redis/NATS.

## Principle

Reasoning, execution, and learning stay separated. Memory is **nine typed lanes** mapped onto SQLite + markdown + FTS (optional embeddings later), not one undifferentiated vector table.

## Nine types

| Type | Meaning | Store | Write | Read / inject |
|------|---------|-------|-------|---------------|
| **diary** | Short growth-log line for Companion | `learning_events.diary` | Review final `Diary:` line only | Companion growth log |
| **episodic** | Concrete past experiences (structured) | `episodic_memories` payload + index (per user) | `record_episode` | `recall_episodes` / turn retrieve |
| **semantic** | Durable facts / procedures | `knowledge_entries` + FTS | `remember` | `recall` / turn retrieve |
| **user** | Preferences, style, profile | `$TOMO_HOME/memories/users/<id>/USER.md` | `memory` target=user | Frozen into system prompt |
| **project** | Architecture / stack / open tasks | `$TOMO_HOME/workplaces/<id>/PROJECT.md` | `memory` target=project | Context when workplace bound |
| **agent** | Per-agent notes / KV | `agents/<id>/users/<uid>/MEMORY.md` + `agent_state` | `memory` / `agent_state` | Agent prompt + retrieve |
| **execution** | Tool outcomes worth keeping | Artifacts + `execution_snippets` | `save_artifact` / review tags | Digest trail + retrieve search |
| **conversation** | Session-scoped working memory | `messages` + `session_summaries` | Chat + summary update | Session history / summary |
| **shared** | Swarm-visible mid-task notes | SQLite `swarm_notes` | Auto on delegate complete | Sibling agents via session retrieve |

**Episodic (production + Phase 3, SQLite only):** full experience model — trigger, objective, context, trajectory (+ `episodic_events`), outcome, evaluation scores, reflection, hierarchy, relations (supersedes/related/similar/contradicts), dedupe, decay, retrieval feedback (`reuse_success`/`reuse_fail`), **lexical search with learned rank weights + graph expand** (no vector embeddings yet), experience-graph auto-link, contradiction analysis, semantic consolidation → per-user `knowledge_entries`, procedural extraction, session open/close boundaries, cross-agent (same-user) retrieval, LTM optimize pass. Freeform `content` remains a fallback. Auto-built from learning reviews when the turn is non-trivial. Failures are valuable.

**Diary is not episodic:** diary is a 1–3 sentence Companion growth note only.

**HTTP:** `GET/POST /api/episodes`, `GET /api/episodes/{id}`, `POST /api/episodes/{id}/feedback`, `GET /api/episodes/meta/contradictions`, `POST /api/episodes/meta/optimize`, `POST /api/episodes/session/{id}/open|close`.

**Skills** stay adjacent (executable workflows via `manage_skill` / `SKILL.md`) — not a ninth memory type. Review may emit skill updates alongside memory writes.

## Do / don't (review LLM)

- **Do** choose a lane before writing; put prefs in **user**, stack in **project**, searchable procedures in **semantic**, env quirks in **agent**.
- **Don't** dump deploy logs or one-off ticket IDs into USER; don't put preferences into orphan KB docs via `remember`; don't copy full chat into knowledge (**diary** only for growth log; use **episodic** for concrete experiences).
- Near-duplicates on curated USER/agent/project: prefer replace/skip (soft evaluator → `saved=0`).

## `saved` truth

`saved=1` only when at least one **write** tool succeeds for a durable lesson type (`episodic`, `semantic`, `user`, `project`, `agent`, `execution`, `shared`).

Write tools: `remember`, `memory`, `agent_state`, `manage_skill`, `save_artifact`, `record_episode`.

Read-only (`list_skills`, `use_skill`, `list_artifacts`) and near-duplicate / error results → audit only, `saved=0`.

Diary line / conversation summary alone do **not** count as saved lessons (use `record_episode` for episodic).

## Soft evaluator

1. Result must not start with `Error:`
2. Curated near-duplicate / already-present → noop (not saved)
3. Provider failures skip the growth ledger (no spam)

## Persistence

Sticky dues and counters live in `learning_agent_state` (wall-clock cooldown) and hydrate across restart. In-flight flags do not survive crash (avoids soft-lock).

## Companion

- Diagnostics: dues, cooldown remaining, in-flight, review counts
- Growth filter: saved-only
- Memory-type chips from `extract_json`

## Slice 2 — Confidence + ranking

- `learning_events.extract_json` includes `{ items, memory_types, saved, confidence }` (0–1 heuristic from successful write lanes).
- `knowledge_entries` columns: `confidence` (default 0.7), `use_count`, `success_count`.
- Review-scoped `remember` writes land at confidence ≈ 0.9 with `success_count=1`.
- Hybrid search re-ranks hits by confidence → success rate → use_count.
- `retrieve_for_turn` order: **user** prefs → bound **project** → high-confidence **semantic** KB → agent/conversation/execution.

## Slice 3 — Shared bus + skill revisions + execution index

- **Shared:** `swarm_notes` rows published in `_stream_delegate_bundle` after `subagent_done` (full content, before truncate). Injected into digest + `retrieve_for_turn` as `[shared]`.
- **Skill revisions:** before overwrite, `edit` / `patch` / overwrite-`write` snapshot `$TOMO_HOME/library/skills/<id>/revisions/vN.md`.
- **Execution index:** `execution_snippets` filled from `save_artifact` catalog and review extract items with `type=execution` + `saved_eligible`. Searchable via `store.search_execution_snippets`.

## Code map

- `app/runtime/agent/learning/memory_types.py` — taxonomy + classification + extract confidence
- `app/runtime/agent/learning/evaluator.py` — soft evaluator
- `app/runtime/memory/project.py` — PROJECT.md lane
- `app/runtime/memory/retrieve.py` — lane-ordered turn retrieve
- `app/models/mixins/learning_events.py` — ledger + agent state CRUD
- `app/models/mixins/knowledge_entries.py` — confidence / use counters
- `app/models/mixins/swarm_notes.py` — shared session notes
- `app/models/mixins/execution_snippets.py` — execution index
- `app/extensions/skills.py` — `snapshot_skill_revision` / `list_skill_revisions`

## Non-goals

Qdrant, Neo4j, Redis, Kafka/NATS, MinIO, ClickHouse; one vector "memory" table; blocking main chat on reflection; mutable XP bond.
