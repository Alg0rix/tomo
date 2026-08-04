# Companion + stronger active learning harness

**Date:** 2026-08-04  
**Status:** Approved for implementation planning  
**Product name:** Companion (top-level nav)  
**Scope:** Full companion UI + durable growth ledger + harness quality upgrades

---

## 1. Problem

Tomo already runs an **active learning harness** after eligible top-level turns:

- Counters (memory every N turns, skill iters / skill-touched refine)
- Sticky dues + cooldown + isolated background review
- Writes via `memory` / `remember` / `manage_skill` / etc.

That loop is mostly **invisible** to the operator:

| Desired surface | Current gap |
|-----------------|-------------|
| Bond / relationship signal | Counters are in-process only; no user-facing score |
| Growth log / diary | Reviews only hit process logs; no inspectable journal |
| Self-learning status | Settings toggle exists but is buried |
| “What I know about you” | `USER.md` / skills exist but are opaque |

Goal: ship a **Companion** experience that is warmer than a settings page but **honest as a harness** — every metric and diary row is backed by real reviews and durable stores, not cosmetic scores.

---

## 2. Goals and non-goals

### Goals

1. **Top-level Companion page** (`/companion`) with app-rail entry labeled **Companion**.
2. **Durable growth ledger** (`learning_events`) written on every completed learning review (saved, idle, or error).
3. **Bond (0–100)** derived from real aggregates (chats, saved reviews, USER.md richness, library skills, active days). Documented formula; display-only.
4. **Growth chart** — monthly buckets from the ledger (last 12 months).
5. **Growth log + diary** — reverse-chron human-readable rows with actions, reason, agent.
6. **What I know** — preview of curated USER profile + skill/memory links.
7. **Learning loop control** on the page (same `learning_enabled` setting as System → General).
8. **Stronger distill harness** in the same milestone:
   - Review digest includes existing skill catalog snapshot + USER.md snippet (patch-first is grounded).
   - Diary line on successful saves (model prose preferred; fallback from actions).
   - Soft memory dedupe guidance (prefer replace/skip near-duplicates).
   - Stronger refine-first rules when `skills_touched` is non-empty.
   - Always persist a learning event after a review attempt.

### Non-goals (this milestone)

- Referral / invite rewards, streak gimmicks, multiplayer “closeness”.
- Per-user isolated companion graphs beyond storing `user_id` on events (single-tenant-first; field is ready for multi-account).
- Channel or external “daily digest” push.
- Vector/embedding-based bond or memory merge.
- Changing the mid-turn tool surface (`memory`, `manage_skill`) contracts beyond minor prompt/dedupe helpers.
- Automatic migration of pre-ledger historical “learning” from logs.

---

## 3. Architecture

```text
run_turn (final, top-level)
  └─ schedule_learning_review
       ├─ hydrate_from_session / observe_turn → ReviewPlan | None
       ├─ begin_review (claim sticky dues)
       ├─ build_review_digest  (+ skill catalog, USER snippet, refine list)
       ├─ isolated review LLM loop (allowed tools only)
       ├─ derive diary (model note or actions fallback)
       ├─ store.insert_learning_event(...)   ← always
       └─ finish_review(saved=…)

GET  /companion              → HTML shell
GET  /api/companion          → bond, stats, growth, recent events, profile preview
GET  /api/companion/events   → paginated growth log
settings learning_enabled    → existing settings path (Companion toggle reuses)
```

### Principles

- **Main turn never blocks** on review or ledger write (background task, as today).
- **Ledger is append-only** for the UI; no silent rewrites of past diary rows.
- **Bond is pure** over aggregates — recomputed on read, not stored as mutable XP.
- **Harness truth over theater** — idle reviews still appear (muted); bond weights *saves* and durable profile more than raw review count.

---

## 4. Data model

### Table `learning_events`

```sql
CREATE TABLE IF NOT EXISTS learning_events (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    agent_id        TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    user_id         TEXT NOT NULL DEFAULT 'web',
    reason          TEXT NOT NULL DEFAULT '',
    review_memory   INTEGER NOT NULL DEFAULT 0,
    review_skills   INTEGER NOT NULL DEFAULT 0,
    saved           INTEGER NOT NULL DEFAULT 0,
    actions_json    TEXT NOT NULL DEFAULT '[]',
    diary           TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    plan_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_learning_events_created
    ON learning_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_events_agent
    ON learning_events(agent_id, created_at DESC);
```

### Store API (mixin + facade)

- `insert_learning_event(...)` → dict  
- `list_learning_events(*, limit, before=None, agent_id=None)` → list[dict]  
- `learning_event_stats()` → aggregates for bond/growth  
- `companion_snapshot()` → composed payload for `GET /api/companion` (or compose in API layer)

### Bond formula

```text
bond = clamp(0, 100, round(
    25 * tanh(chats / 40)
  + 25 * tanh(saved_events / 15)
  + 20 * tanh(user_memory_chars / 800)
  + 15 * tanh(library_skills / 10)
  + 15 * tanh(days_active / 30)
))
```

Definitions:

| Signal | Source |
|--------|--------|
| `chats` | Count of user messages (or sessions) — prefer total user message count across sessions for volume |
| `saved_events` | `COUNT(*) WHERE saved=1` on `learning_events` |
| `user_memory_chars` | Length of `$TOMO_HOME` USER.md (or sum of entries) |
| `library_skills` | Count of discoverable/managed library skills |
| `days_active` | Distinct UTC calendar days with at least one learning event **or** user message |
| `days_together` | Calendar days from first session (or first message) to now, inclusive floor ≥ 0 |

Expose `bond_parts` raw inputs in the API for transparency (UI can show a “why this score” collapse later; V1 may only show bond + substats).

Use `math.tanh` (stdlib). Document formula on the Companion page in muted helper text.

---

## 5. Harness upgrades

### 5.1 Digest enrichment (`digest.py`)

Extend `build_review_digest` (or a wrapper used only by the runner) to append:

1. **Existing skills (catalog)** — up to ~40 skill ids + one-line descriptions (from skill index / list_skills data). Cap characters (~2k).  
2. **USER profile snippet** — first ~800 chars of USER.md entries (or “empty”).  
3. **Refine-first** — when `skills_touched` non-empty, a bold instruction: load each touched skill before create; prefer patch.

Do not dump full skill bodies into the digest (token burn); reviewer still uses `use_skill` for body when patching.

### 5.2 Prompt upgrades (`prompts.py`)

Add rules:

- On any successful write, final assistant text must include a **Diary:** line (1–3 sentences, past tense, what changed for future sessions).  
- Prefer **patch** over create; before create, call `list_skills` and consider catalog in digest.  
- For memory: if a similar preference already exists, `replace` or skip — do not stack duplicates.  
- Idle remains: reply exactly `Nothing to save.` when nothing durable.

### 5.3 Diary derivation (`runner.py`)

After review LLM loop:

```text
if saved:
  diary = extract_diary_line(note) or synthesize_diary_from_actions(actions)
else:
  diary = ""
```

`extract_diary_line`: parse `Diary:` prefix from final note if present.  
`synthesize_diary_from_actions`: e.g. `"Saved memory (user); patched skill python-unit-testing."`

### 5.4 Always record

After `finish_review` path (including errors), call `insert_learning_event` with:

- plan fields, actions, diary, note, saved flag  
- `user_id` from session when available (else `"web"`)

Ledger insert failures are logged; they must not crash the background task or affect the user turn.

### 5.5 Optional soft dedupe

Prefer prompt-level first. If a small helper is cheap:

- `curated.near_duplicate(entries, content) -> existing|None` (normalized whitespace, casefold, substring / high overlap)  
- Reviewer still decides; no hard block unless clearly identical.

### 5.6 Unchanged

- Counter model, sticky dues, isolation scope, allowed review tools, cooldown, nested exclusion, mid-turn tools.

---

## 6. API contracts

### `GET /api/companion`

```json
{
  "bond": 42,
  "bond_parts": {
    "chats": 120,
    "saved_events": 8,
    "user_memory_chars": 400,
    "library_skills": 5,
    "days_active": 12
  },
  "days_together": 63,
  "first_seen_at": 1710000000.0,
  "learning_enabled": true,
  "stats": {
    "events_total": 20,
    "events_saved": 8,
    "events_idle": 12,
    "skills_library": 5,
    "user_entries": 3
  },
  "growth": [
    { "month": "2026-07", "events": 4, "saved": 2 },
    { "month": "2026-08", "events": 6, "saved": 3 }
  ],
  "recent_events": [ /* up to 20 LearningEvent */ ],
  "user_profile_preview": [ "Prefers concise answers", "..." ]
}
```

### `GET /api/companion/events?limit=30&before=<unix_float>`

```json
{
  "events": [ /* LearningEvent */ ],
  "next_before": 1710000000.0
}
```

`next_before` is null when no more rows.

### `LearningEvent`

```json
{
  "id": "…",
  "created_at": 1710000000.0,
  "agent_id": "main",
  "session_id": "…",
  "reason": "memory_every_5_turns",
  "review_memory": true,
  "review_skills": false,
  "saved": true,
  "actions": ["memory: added user entry"],
  "diary": "Noted that you prefer short answers over long preambles.",
  "note": "Diary: …"
}
```

### Settings

Learning toggle on Companion uses the **existing** settings update endpoint / payload key `learning_enabled` (same as System → General). No second source of truth.

Auth: same as other `/api/*` routes (`AuthDep`).

---

## 7. UI

### Navigation

- App rail item **Companion** after **Skills**, before modules/Evaluate/Scheduler/System.
- Icon: simple bond/heart-or-orbit glyph consistent with stroke-1.8 rail icons.
- Active when `page == 'companion'`.

### Page `/companion`

Server-rendered template `companion.html` extending `base.html`. Client JS loads `GET /api/companion` on mount.

**Sections:**

1. **Hero** — title, subtitle, bond number + bar, days together, chat/review stats, learning toggle + badge.
2. **Growth** — 12-month CSS bar chart from `growth[]` (no chart library required).
3. **Growth log** — cards from `recent_events`; Load more → `/api/companion/events`.
4. **What I know** — `user_profile_preview` list; links to `/skills` and `/system#memory`.

**Empty state** when `events_total == 0`: explanatory copy that Learning must be on and a few multi-step chats will seed the log.

**Copy tone:** Warm, product-facing, but never claim closeness beyond the documented bond. Helper under bond:

> Bond reflects real collaboration: chats, saved lessons, profile notes, and skills — not a streak game.

### CSS

Scoped classes under `.companion-*` in `tomo.css` (or small companion block). Use existing tokens (surface, accent, ok, faint).

### JS

`app/static/js/companion.js` — fetch, render stats/log/chart, toggle learning, pagination. No framework.

---

## 8. Testing

| Layer | Cases |
|-------|--------|
| Unit — ledger | insert, list with `before`, stats counts |
| Unit — bond | zeros → 0; large inputs clamp ≤100; monotonic-ish in each axis |
| Unit — digest | includes catalog/USER/refine sections when provided |
| Unit — diary | extract `Diary:`; fallback from actions; idle empty diary |
| Unit — runner | mock LLM save → event with `saved=1` + diary; idle → event `saved=0` |
| Unit — existing harness | sticky dues / cooldown / isolation still green |
| API | `/api/companion` shape with empty DB; events pagination |

---

## 9. Implementation order (for plan skill)

1. Schema + mixin + store facade + unit tests  
2. Bond + companion snapshot helpers + tests  
3. Wire runner: always insert event + diary derivation  
4. Digest/prompt harness upgrades + tests  
5. REST endpoints  
6. Page + rail + JS/CSS  
7. Manual smoke: enable learning, chat enough to trigger review, confirm log row  

---

## 10. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Extra tokens in review digest | Cap catalog + USER snippet; optional cheaper `learning_review_profile_id` (already exists) |
| Diary noise / model ignores format | Fallback synthesize from actions |
| Bond feels arbitrary | Publish formula; expose `bond_parts`; weight durable writes |
| Ledger write fails | Log + continue; UI shows empty until first success |
| UI overpromise vs weak reviews | Idle rows visible; stronger patch-first + catalog grounding |

---

## 11. Success criteria

1. Companion appears in rail; page loads without error on fresh DB.  
2. After a successful learning review, a growth-log row appears with diary and actions.  
3. Idle reviews create muted rows (`saved=false`).  
4. Bond moves when USER.md / skills / saved events change (recompute on read).  
5. Learning toggle on Companion matches System → General.  
6. Unit tests for ledger, bond, diary, and enriched digest pass.  
7. Existing learning harness tests remain green.

---

## 12. Decisions log

| Decision | Choice |
|----------|--------|
| Product priority | Full companion UI (not audit-only) |
| Placement | Top-level page + rail |
| Nav label | **Companion** |
| Approach | Durable ledger + bond + harness quality (not reconstruct-from-files) |
| Scope extras | No Telegram digest / social in V1 |
| Bond | tanh blend of five real signals |
| Diary | Prefer model `Diary:`; fallback from actions |
