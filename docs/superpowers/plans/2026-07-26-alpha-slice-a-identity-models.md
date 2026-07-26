# Alpha Slice A — Agent Identity + Multi-Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents have editable **Name** + **Role**; users configure **multiple OpenAI-compat LLM profiles**, set a **default**, assign a profile per agent; runtime `get_llm(agent_id=…)` resolves the right client; chat copy stays honest until Slice B.

**Architecture:** New SQLite `llm_profiles` table (encrypted `api_key` via Slice 0 secrets). Settings key `default_model_id`. Agents gain `role`; `model_id` means **profile id** (empty = use default). System → Models becomes profile CRUD. `get_llm` reads profiles only (not the old single-form settings triple as runtime source).

**Tech Stack:** SQLite, FastAPI, existing Fernet secrets, Jinja + vanilla JS UI, pytest.

**Master spec:** `docs/superpowers/specs/2026-07-26-alpha-kitchen-sink-design.md` §2.2 + Slice A.

## Global Constraints

- Do **not** start/restart the Tomo server.
- Do **not** implement Slices B–H (no real delegation, no new tools, no workplaces).
- Profile `api_key` always ciphertext at rest (`encrypt_secret` / `decrypt_secret`).
- GET never returns full API keys (masked + `api_key_set`).
- Blank key on update keeps existing ciphertext.
- Alpha-fresh: profiles are configured in UI/setup — no auto-import stories in docs/comments.
- Prefer ~150–250 lines/file; smell at ~400+.
- Cline implements; commit at end per brief.

---

## File map

| Path | Responsibility |
|------|----------------|
| Modify: `app/models/schema.py` | `agents.role`; `llm_profiles` table; idempotent column add |
| Create: `app/models/mixins/llm_profiles.py` | CRUD + public (masked) view + set-default helper |
| Modify: `app/models/mixins/agents.py` | `role`; `model_id` = profile id |
| Modify: `app/models/mixins/settings.py` | `default_model_id`; stop treating top-level `llm_*` as runtime source (may remain unused KV) |
| Modify: `app/models/seed.py` | Seed agent roles; empty `model_id`; optional first profile only if tests need it |
| Modify: `app/schemas/models.py` | Agent + profile schemas |
| Modify: `app/services/store.py` | Facade methods for profiles |
| Modify: `app/api/rest.py` / `app/api/platform.py` | Profile routes; agent role |
| Modify: `app/runtime/llm/__init__.py` | `get_llm(agent_id: str \| None = None)` |
| Modify: `app/runtime/agent/loop.py` | Pass `agent_id` into `get_llm` |
| Modify: `app/runtime/session_title.py` | Default profile via `get_llm()` |
| Modify: Models UI + `system.js` | Multi-profile CRUD + set default |
| Modify: Agent create/config UI + `agents.js` | Name, Role, model dropdown |
| Modify: chat home / sessions empty copy | Honest coordinator-only wording |
| Modify: agent Tools/Skills/Channels panels | Honest disabled/stub labels if still non-functional |
| Tests: `tests/unit/models/test_llm_profiles.py`, `test_agents_role.py`, `tests/unit/runtime/llm/test_get_llm_profiles.py` | |
| Modify: `docs/superpowers/progress/alpha.md` | Slice A done when shipped |

---

### Task 1: Schema + llm_profiles mixin + default_model_id

**Files:** `schema.py`, create `llm_profiles.py`, `settings` seed keys, `store.py`, tests

**DDL:**

```sql
CREATE TABLE IF NOT EXISTS llm_profiles (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    base_url   TEXT NOT NULL DEFAULT '',
    api_key    TEXT NOT NULL DEFAULT '',
    model      TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL DEFAULT 0
);
```

Add `role TEXT NOT NULL DEFAULT ''` to agents. Because SQLite `CREATE TABLE IF NOT EXISTS` won’t alter existing DBs, `migrate()` must also run:

```python
# after executescript — idempotent
cols = {r[1] for r in conn.execute("PRAGMA table_info(agents)")}
if "role" not in cols:
    conn.execute("ALTER TABLE agents ADD COLUMN role TEXT NOT NULL DEFAULT ''")
```

**Interfaces:**
- `list_profiles(conn) -> list[dict]` (decrypted for internal; or separate public)
- `public_profile(row) -> dict` masks key
- `create_profile` / `update_profile` / `get_profile` (decrypt for runtime)
- `set_default_model_id(conn, profile_id)`
- Settings seed includes `default_model_id: ""`

- [ ] **Step 1:** Failing tests for create profile → ciphertext in DB; public mask; blank PUT keep key
- [ ] **Step 2:** Implement schema + mixin + store facade
- [ ] **Step 3:** `uv run pytest tests/unit/models/test_llm_profiles.py -q` → PASS

---

### Task 2: Agent `role` + API + get_llm resolution

**Files:** `agents.py` mixin, `schemas/models.py`, `rest.py`, `platform.py` (profile routes), `runtime/llm/__init__.py`, `loop.py`, `session_title.py`, seed

**Agent fields:** `role: str` on create/update/list; `model_id` stores profile id or `""` for default.

**API (suggested):**
- `GET/POST /api/llm-profiles`
- `GET/PUT/DELETE /api/llm-profiles/{id}`
- `POST /api/llm-profiles/{id}/default` (or PUT settings `{default_model_id}`)
- Agents already under `/api/agents`

**`get_llm(agent_id: str | None = None)` resolution:**

1. If `agent_id` and agent has non-empty `model_id` and that profile exists and `enabled` → use it  
2. Else settings `default_model_id` if set and profile enabled → use it  
3. Else first enabled profile by name/id  
4. Else raise `LLMConfigError("Configure a model profile in System → Models")`  
5. Decrypt `api_key` in memory; build `OpenAICompatClient`

Wire `run_turn` / session title to this. Tests with Mock store or real temp DB + profiles.

- [ ] **Step 1:** Failing tests — two profiles, two agents, `get_llm(agent_id)` returns matching model/base_url  
- [ ] **Step 2:** Implement  
- [ ] **Step 3:** pytest profiles + agents + `tests/unit/runtime/llm/` + loop still green  

---

### Task 3: System → Models UI + setup creates first default profile

**Files:** `models.html`, `system.js`, `setup.html` / `setup.js`, `platform.py` setup handler

Replace single global URL/key/model form with:
- List of profiles (name, model, host, key-set badge, default badge, enabled)
- Add / edit form (name, id slug, base_url, api_key, model, enabled)
- “Set as default” button

Setup step 2: collect base_url + api_key + model (+ name default “Default”) → create profile `id=default`, set `default_model_id=default`, mark setup complete. Do not rely on top-level `llm_*` for chat after this.

Retire or stop surfacing fake `platform_data` providers/models as if live — Models page uses `llm_profiles` only. If `/api/models` still exists for eval, leave gated/unused; don’t wire it into System → Models.

- [ ] **Step 1:** Implement UI + JS calling new APIs  
- [ ] **Step 2:** Manual sanity via tests that hit store/API if UI hard to unit-test  
- [ ] **Step 3:** Ensure existing settings/LLM tests updated for profiles  

---

### Task 4: Agent create/config UI + honesty copy

**Files:** `new_dialog.html`, `panel_config.html`, `agents.js`, `chat_home.html`, `sessions.js` empty state, `modals.html` new-chat lead, agent Tools/Skills/Channels panels

- Create dialog: Name, Role (text), Model `<select>` (enabled profiles + “Use default” → `model_id=""`)
- Config panel: editable Name, Role, Model; Save → PUT `/api/agents/{id}`
- Seed agents: set sensible `role` strings (e.g. coordinator / ops / research); `model_id=""`
- Copy fixes (coordinator-only until Slice B):
  - Dashboard: not “routes the work” → e.g. “Ask the coordinator.”  
  - New swarm modal: not “routes work across the swarm” → honest “multi-agent session; coordinator answers until handoff ships”  
  - Sessions empty: drop “coordinator routes to agents” / soft-pedal @mention as handoff  
- Tools/Skills/Channels agent panels: if still non-binding, label “Not wired yet” / disable Save that pretends to work

- [ ] **Step 1:** UI + copy  
- [ ] **Step 2:** `uv run pytest tests/unit/models/ tests/unit/runtime/llm/ tests/unit/runtime/agent/ -q`  
- [ ] **Step 3:** Update `docs/superpowers/progress/alpha.md`; commit  

```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: agent roles and multi-model LLM profiles

EOF
)"
```

---

## Spec coverage

| Spec item | Task |
|-----------|------|
| `agents.role` | 2, 4 |
| `llm_profiles` + `default_model_id` | 1–3 |
| Per-agent profile assignment | 2, 4 |
| `get_llm(agent)` resolution | 2 |
| System → Models multi-profile | 3 |
| Honest chat copy | 4 |
| Encrypted profile keys | 1 |

## Out of scope

- Real swarm delegation (B)  
- New tools / workplaces / memory / Telegram  
- Marketplace / provider discovery catalogs  
