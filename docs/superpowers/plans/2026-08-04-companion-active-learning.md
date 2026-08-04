# Companion + Active Learning Implementation Plan

> **For agentic workers:** Execute task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a top-level Companion page backed by a durable `learning_events` ledger, a documented Bond score, and a stronger learning-review harness (diary, enriched digest, always-record).

**Architecture:** Learning reviews already run after top-level finals. Each review completion appends a `learning_events` row. Bond and growth charts are pure reads over that ledger plus USER.md / skills / message counts. The Companion UI is a new rail page + REST endpoints; the learning toggle reuses existing settings.

**Tech Stack:** Python 3 / FastAPI / SQLite / Jinja2 / vanilla JS / existing learning harness under `app/runtime/agent/learning/`.

## Global Constraints

- Never name third-party products in UI copy or docs for this feature.
- Main chat turn must never block on learning review or ledger write.
- Bond is recomputed on read; do not store mutable XP.
- `learning_enabled` has one source of truth (settings).
- Follow existing store mixin + facade patterns.
- Prefer small, focused modules (`bond.py`, `diary.py`, mixin file).

## File map

| Path | Role |
|------|------|
| `app/models/schema.py` | `learning_events` DDL |
| `app/models/mixins/learning_events.py` | CRUD + stats |
| `app/services/store.py` | Facade methods |
| `app/runtime/agent/learning/bond.py` | Bond formula |
| `app/runtime/agent/learning/diary.py` | Diary extract/synthesize |
| `app/runtime/agent/learning/digest.py` | Catalog + USER + refine sections |
| `app/runtime/agent/learning/prompts.py` | Diary / patch / dedupe rules |
| `app/runtime/agent/learning/runner.py` | Always record event |
| `app/runtime/agent/learning/companion.py` | Snapshot composition |
| `app/api/rest.py` or platform | Companion endpoints |
| `app/web/pages.py` | `/companion` route |
| `app/templates/companion.html` | Page |
| `app/templates/partials/app_rail.html` | Nav item |
| `app/static/js/companion.js` | Client |
| `app/static/css/tomo.css` | `.companion-*` styles |
| tests under `tests/unit/...` | Ledger, bond, diary, digest, runner, API |

---

### Task 1: Schema + learning_events mixin + store facade

**Files:**
- Modify: `app/models/schema.py`
- Create: `app/models/mixins/learning_events.py`
- Modify: `app/services/store.py`
- Test: `tests/unit/models/test_learning_events.py`

- [x] Add `learning_events` table + indexes to `_SCHEMA`
- [x] Implement `insert_learning_event`, `list_learning_events`, `learning_event_stats`, `learning_events_by_month`
- [x] Expose on `Store` under lock
- [x] Unit tests: insert, list `before`, stats

### Task 2: Bond + companion snapshot

**Files:**
- Create: `app/runtime/agent/learning/bond.py`
- Create: `app/runtime/agent/learning/companion.py`
- Test: `tests/unit/runtime/agent/test_bond.py`, `test_companion_snapshot.py`

- [x] `compute_bond(**parts) -> int` with tanh formula
- [x] `companion_snapshot(store) -> dict` matching API contract
- [x] Helpers for chats count, days_together, user profile preview

### Task 3: Diary helpers + always-record in runner

**Files:**
- Create: `app/runtime/agent/learning/diary.py`
- Modify: `app/runtime/agent/learning/runner.py`
- Test: `tests/unit/runtime/agent/test_diary.py` + extend `test_learning_harness.py`

- [x] `extract_diary_line`, `synthesize_diary_from_actions`, `derive_diary`
- [x] After every review (success/idle/error), insert learning event
- [x] Verify save path records diary; idle path `saved=0`

### Task 4: Digest + prompt harness upgrades

**Files:**
- Modify: `app/runtime/agent/learning/digest.py`, `prompts.py`
- Test: extend digest tests

- [x] Optional catalog / user_snippet / refine sections in digest
- [x] Runner gathers catalog + USER snippet when building digest
- [x] Prompt rules: Diary line, patch-first, memory dedupe

### Task 5: REST + page + UI

**Files:**
- Modify: `app/api/rest.py` (or `platform.py`), `app/web/pages.py`, `app_rail.html`
- Create: `companion.html`, `companion.js`, CSS block
- Test: `tests/unit/api/test_companion_api.py` or integration

- [x] `GET /api/companion`, `GET /api/companion/events`
- [x] `GET /companion` HTML
- [x] Rail nav Companion after Skills
- [x] JS: load snapshot, render hero/growth/log/profile, toggle learning, load more

### Task 6: Verification

- [x] Unit tests green (29 passed including companion API)
- [x] Companion API empty DB shape
- [x] Existing learning tests green

---

## Spec coverage check

| Spec requirement | Task |
|------------------|------|
| learning_events table | 1 |
| Bond formula | 2 |
| Always record events | 3 |
| Diary | 3 |
| Digest enrichment + prompts | 4 |
| API + Companion UI + rail | 5 |
| Learning toggle reuse settings | 5 |
| Tests | 1–6 |
