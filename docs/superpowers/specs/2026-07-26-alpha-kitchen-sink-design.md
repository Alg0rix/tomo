# Tomo Alpha — Kitchen-Sink Gaps Master Spec

**Date:** 2026-07-26  
**Status:** accepted 2026-07-26  
**Delivery:** Approach **B** — master spec + sequenced slice specs/plans + Cline briefs  
**Roles:** Cursor plans/reviews · **Cline implements** (Cursor does not implement Alpha slices unless human asks)

---

## 1. Goal

Ship **Tomo Alpha**: a believable multi-agent product where **every visible UI surface works end-to-end** (or is hidden). Close the gaps left after the foundation thin vertical:

> SQLite → settings LLM → calculator → coordinator-only loop → web SSE  
> (+ chat home, streaming, LLM session titles)

**Alpha bar:** no “seed tile that looks live but Connect/Upload/Save is permanently disabled.” Stub nav must either become real or disappear behind a feature flag (same pattern as eval).

**Eval / evaluator remain out of Alpha** (`EVAL_UI_ENABLED` / `TOMO_EVAL_UI` default off).

---

## 2. Decisions (locked)

| Topic | Choice |
|-------|--------|
| Scope | Kitchen-sink Alpha — all prior roadmap gaps **except eval** |
| Delivery | Master spec + **sequenced slices** (B); each slice gets its own plan + Cline brief(s) |
| Implementer | **Cline** |
| Planner/reviewer | **Cursor** |
| UI honesty | Visible ⇒ works; else hide (feature flag) |
| User data home | **`$TOMO_HOME`** (default `~/.tomo`) — Tomo tree (§2.1). **`SOUL.md` / `SYSTEM.md`** names for persona/prompts; rest of layout is Tomo’s |
| Tool defs | Built-in JSON in `app/tools/`; user/overrides under `$TOMO_HOME`; backends in `app/runtime/tools/` |
| Modularity | Prefer ~150–250 lines/file; smell at ~400+; no god-files |
| LLM | OpenAI-compat **model profiles** in SQLite (§2.2): many configs, one default, per-agent assignment; no mock in product path |
| Busy | Process-local in-memory OK for Alpha unless a slice requires otherwise |
| Server | Agents must **not** start/restart Tomo; human runs the server |

### 2.1 Tomo Home (locked layout)

**Idea:** a writable user root for persona, skills, memory, and per-agent prompts/knowledge/workspace — so Alpha is customizable without editing the git tree.

**Layout** (`$TOMO_HOME`, default `~/.tomo`):

```text
$TOMO_HOME/
├── tomo.yaml                       # non-secret prefs only (never API keys / never master key)
├── .env                            # optional bootstrap secrets (dotfile; chmod 600) — plaintext OK here only
├── .secret_key                     # master key for at-rest encryption (dotfile; chmod 600; auto-created)
├── SOUL.md                         # global default persona
├── library/
│   ├── skills/                     # user-installed skill packages
│   └── memory/                     # durable notes / memory files
├── agents/
│   └── <agent_id>/
│       ├── SYSTEM.md               # agent system prompt
│       ├── SOUL.md                 # optional agent persona overlay
│       ├── knowledge/              # on-demand docs
│       └── work/                   # default tool cwd for this agent
├── workplaces/                     # optional FS mirrors / mounts metadata
└── state/
    └── tomo.db                     # SQLite (secret settings stored encrypted)
```

**Secrets policy (locked):**

1. **Primary store:** SQLite `settings` for UI-managed secrets (`llm_api_key`, Telegram token, SSH passwords, …).  
2. **Never store secret values as plaintext in SQLite.** Encrypt at rest with a **master secret key** (Fernet or AES-GCM). Runtime decrypts in memory only for LLM/tools; never log plaintext.  
3. **Master key sources** (first match wins):  
   - Process env `TOMO_SECRET_KEY` (preferred for containers / CI)  
   - Else `$TOMO_HOME/.secret_key` (auto-generate on first `ensure_tomo_home` if missing; `chmod 600`; never overwrite an existing file)  
4. **UI contract unchanged:** GET returns **masked** values + `*_set` flags; blank/missing PUT keeps existing ciphertext; never echo decrypted secrets over HTTP/HTML.  
5. **Optional file:** `$TOMO_HOME/.env` only (leading dot). Never `secrets.env` or other non-dot secret filenames. Mode `0600`. Used for bootstrap / ops override — may hold plaintext; load with `override=False` (process env wins). Prefer moving durable secrets into encrypted SQLite via UI.  
6. **`tomo.yaml` must not hold secrets or the master key** — loaders reject / ignore secret-looking keys.  
7. **Precedence for Alpha** (document exact order in Slice 0): process env → `$TOMO_HOME/.env` → decrypt SQLite settings (SQLite is source of truth for UI-edited keys).  
8. **SQLite secrets are always ciphertext** after any UI/API write. Decrypt only in memory for runtime.  

**Rules:**

1. First run: create `$TOMO_HOME` and seed from repo `defaults/` (copy, don’t bind-mount the repo as live config). Auto-create `.secret_key` if neither env nor file exists. Do **not** seed `.env` with real keys — only an optional `.env.example` in repo docs.  
2. Runtime **reads prompts/skills/knowledge from `$TOMO_HOME`**; SQLite holds structured entities (agents, sessions, schedules, …) with paths pointing into home.  
3. UI can edit `SOUL.md` / `SYSTEM.md` / knowledge files; saving updates disk (and DB metadata timestamps).  
4. Repo `defaults/` and `app/tools/` remain **shipped builtins**; user overlays live only under `$TOMO_HOME`.  
5. Tests use a temp `TOMO_HOME` (never the developer’s `~/.tomo`) and a throwaway master key.  
6. **Allowed familiar names:** `SOUL.md`, `SYSTEM.md`, optional `.env`, `.secret_key`. Other tree names stay Tomo (`tomo.yaml`, `library/`, `knowledge/`, `work/`, `state/`).  
7. Losing `.secret_key` / `TOMO_SECRET_KEY` makes encrypted SQLite secrets unrecoverable — document backup of that file with the same care as the DB.  

**Slice 0** implements this home + path resolution + secret-key / encrypt helpers before identity/UI (Slice A) and skills/memory slices depend on it.

### 2.2 Multi-model profiles (locked)

Alpha configures LLMs as **named profiles** — not a single global URL/key/model. Users add as many as they need, mark one **default**, and assign a profile (or “Use default”) per agent.

**Model profile** (OpenAI-compatible endpoint + credentials + model string):

| Field | Notes |
|-------|--------|
| `id` | Stable id (slug), e.g. `default`, `fast`, `local-ollama` |
| `name` | Display label |
| `base_url` | OpenAI-compat base URL |
| `api_key` | Secret — **encrypted at rest** (same crypto as §2.1) |
| `model` | Provider model string (e.g. `gpt-4o-mini`, `claude-…` via gateway) |
| `enabled` | Disabled profiles cannot be newly assigned; if an agent still points at one, runtime falls back to default with a logged warning |

**Default model:** settings key `default_model_id` → profile `id`. Used when:

- Agent has empty / missing `model_id` (“Use default”)
- Session title / other non-agent LLM calls
- Assigned profile missing or disabled (fallback)

**Per-agent:** `agents.model_id` stores a **profile id**. Agent Config / create dialog: dropdown of enabled profiles (+ “Use default”).

**Runtime resolution** (`get_llm` / loop):

1. Resolve profile: agent’s `model_id` if set and enabled → else `default_model_id` → else first enabled profile  
2. Build `OpenAICompatClient(base_url, api_key, model)` from that profile  
3. Never use mock in product path  

**System → Models UI:**

- Configure profiles: list / add / edit / enable-disable  
- “Set as default”  
- Masked key GET / blank PUT keep (per profile)  
- Fresh Alpha: empty catalog until the user adds at least one profile and sets default (setup / Models guides this)

**Out of Alpha:** multi-provider marketplace / discovery catalogs. Alpha is **user-configured OpenAI-compat profiles only**.

**Slice ownership:** Slice **A** — Models UI + agent picker + `get_llm(agent)`. Slice 0 provides secret encryption used by profile keys.

---

## 3. Baseline (what already works)

| Area | Status |
|------|--------|
| Auth / setup | Works |
| Dashboard chat home | Works (coordinator-only runtime underneath) |
| Sessions / swarm membership UI | Works (create/edit agents on session) |
| Web SSE chat + streaming + history markdown | Works |
| LLM settings (System → Models) | Single global form today → **multi-profile configure in Slice A** (§2.2) |
| LLM session auto-title | Works (uses default profile after Slice A) |
| Calculator tool | Works |
| Agents list/create + agent chat | Mostly works; Config/Tools/Skills/Channels panels seed/stub |
| Workplaces / Skills / Scheduler / Plugins | Seed UI, actions disabled |
| Eval | Hidden + routes gated |
| Swarm mid-turn delegation | **Not implemented** (`delegate: false`; coordinator stub empty) |
| Memory / KB | Stubs only |
| Telegram / WhatsApp / Discord | Seed labels only |
| `platform_data` | Still holds tools catalog, workplaces, schedules, plugins, … |

---

## 4. Alpha UI contract

### 4.1 Nav policy

| Nav item | Alpha requirement |
|----------|-------------------|
| Dashboard | Harden: honest copy (delegation real after Slice 2); overview cards stay useful |
| Chat | Full swarm chat: handoff, @mention, tools, workplaces context when assigned |
| Agents | Create/edit **Name + Role** (+ description, model, enabled); Tools/Skills/Channels panels bind to real data |
| Workplaces | List/create/edit; **Connect** works for local + SSH; tunnel may be “planned” **only if** labeled and not fake-connected |
| Skills | Install/enable at least a minimal path (upload or seeded package that agents can attach) — or **hide Skills nav** until Slice covers it |
| Scheduler | Create/list/enable a schedule that fires (or queues) a session turn — or **hide** until ready |
| System | **Models:** multi-profile CRUD + default (§2.2); Tools list reflects registry; Plugins/Users/Shared channels work or hide |
| Evaluate | **Stay hidden** |

### 4.2 “Works” definition

A surface **works** when:

1. Primary user action succeeds without a disabled button or toast “stub.”  
2. State persists across reload (SQLite or agreed store).  
3. Errors are visible and recoverable.  
4. Empty states explain the next action.

### 4.3 Hide pattern

Use `app/core/config.py` flags (env-overridable), e.g.:

- `EVAL_UI_ENABLED` (exists, default false)  
- Future: `SKILLS_UI_ENABLED`, `SCHEDULER_UI_ENABLED`, `PLUGINS_UI_ENABLED` only if a slice must ship Alpha without that surface  

Prefer **shipping the surface working** over hiding. Hiding is the escape hatch when a slice is deferred mid-Alpha (should be rare given kitchen-sink).

---

## 5. Slice sequence (dependency order)

Each slice: **spec excerpt (or child spec) → plan → Cline brief(s) → implement → Cursor review → adversarial fix if needed → progress log.**

### Slice 0 — Tomo Home (`$TOMO_HOME`)

**Goal:** Establish Tomo-native user data root so later slices load identity/prompts/skills/knowledge/work from disk without editing the git checkout.

**Includes:**

- `TOMO_HOME` in `app/core/config.py` (default `~/.tomo`); `DB_PATH` default → `$TOMO_HOME/state/tomo.db`  
- Bootstrap: create tree + seed `SOUL.md` / `tomo.yaml` from `defaults/`; create `$TOMO_HOME/.secret_key` (chmod 600) when `TOMO_SECRET_KEY` unset  
- Helpers: `home_paths.agent_system(agent_id)`, `agent_soul`, `agent_knowledge_dir`, `agent_work_dir`, `library_skills_dir`  
- Secrets crypto: encrypt/decrypt helpers; secret fields (incl. profile `api_key`) stored as ciphertext in SQLite; GET stays masked  
- Context builder: prefer `$TOMO_HOME/agents/<id>/SYSTEM.md` (+ optional agent `SOUL.md`) then global `SOUL.md` then repo default  
- Docs: README “Tomo Home” + secrets / `.secret_key` backup note; tests with temp home + temp key  

**Acceptance:**

- Fresh env with empty `TOMO_HOME` boots and seeds files including `.secret_key`  
- Editing global `SOUL.md` changes coordinator tone on next turn (no code change)  
- Per-agent `SYSTEM.md` overrides when present  
- Tree uses §2.1 names (`SOUL.md` / `SYSTEM.md` / `.secret_key` OK)  
- Saving a secret via settings stores ciphertext; runtime decrypts; GET never returns full key 

**Depends on:** nothing (first Alpha slice)

---

### Slice A — Agent identity + multi-model + UI honesty

**Goal:** Agents have **Name** and **Role**; users configure **multiple LLM profiles**, set a **default**, and **assign a profile per agent**; dashboard/chat copy matches reality until Slice B.

**Includes:**

- DB/schema: `role` (text) on agents; schema update + seed  
- **Model profiles** table `llm_profiles` + `settings.default_model_id`; encrypt per-profile `api_key`  
- API: list/create/update profiles; set default; agents expose `model_id` as profile id  
- System → Models UI: configure multiple profiles + set default  
- Agent create + Config: Name, Role, **model dropdown** (enabled profiles + “Use default”); save via PUT  
- Runtime: `get_llm(agent_id=…)` / loop uses agent profile → default fallback  
- Session auto-title uses default profile  
- Harden chat home: no claims of routing/handoff until Slice B; after B, restore “via Tomo” language  
- Audit System/Agents stub panels: remove or disable-with-honest label for Tools/Skills/Channels until later slices wire them  

**Acceptance:**

- User configures ≥2 profiles and a default in System → Models  
- Create agent with name + role + model profile; reload shows all three  
- Agent A and agent B can use different profiles; turns use the resolved client  
- Change default; agents on “Use default” follow it  
- Edit config persists; masked keys never leak  
- No false “routes to Ops/Research” copy while coordinator-only  

**Depends on:** Slice 0 (home + secret encryption for profile keys)

---

### Slice B — Real swarm delegation

**Goal:** Mid-turn handoff from coordinator to session members (Ops/Research/…). Matches multi-agent session shape and chat UI.

**Includes:**

- `app/runtime/coordinator/` implements pick/handoff (replace empty stub)  
- Loop or web channel: after coordinator decides (tool `delegate` and/or @mention and/or model tool-call), run a sub-turn on target agent; emit SSE `delegate` then that agent’s deltas/done  
- Persist handoff + member finals in session history  
- @mention parsing in web channel (and optional autocomplete in `sessions.js`)  
- Wire or add `delegate` tool in `app/tools/` + backend  
- Dashboard/chat copy: coordinator routes work  

**Acceptance:**

- Swarm session with main+ops: user asks ops-flavored task → visible handoff → ops reply in transcript  
- `@ops …` forces handoff to ops when member of session  
- Membership still editable; non-members cannot be delegated to  
- Tests: unit/integration with MockLLM driving delegate  

**Depends on:** A (role helps prompts); foundation loop/SSE

---

### Slice C — More tools

**Goal:** Agents do useful work beyond calculator.

**Minimum Alpha toolset (required):**

| Tool | Purpose |
|------|---------|
| `calculator` | Already shipped |
| `bash` (or `shell`) | Run commands in workplace cwd / local sandbox |
| `read_file` | Read file contents |
| `write_file` or `str_replace` | Write/patch files |
| `delegate` | If not fully done in B, finish here |

**Optional stretch in C (if time):** `web_fetch` or `web_search` (one only).

**Includes:**

- JSON defs in `app/tools/`; backends in `app/runtime/tools/`; registry `_BACKENDS`  
- Safety: timeouts, cwd jail relative to workplace root (or `var/workspaces/<id>` until workplaces land), no silent `eval`  
- System → Tools list sourced from registry (not only `platform_data` seed)  
- Agent Tools panel: enable/disable per agent (persist — SQLite or settings JSON accepted if documented)  
- Reimplement tool schemas cleanly under Tomo’s license; no vendored third-party tool runtimes  

**Acceptance:**

- Chat: “create a file … / list dir …” succeeds via tools with tool SSE events  
- Registry tests for each new tool  
- Calculator still green  

**Depends on:** B recommended (delegate); can parallelize defs with B if needed  
**Soft-depends on:** D for real workplace cwd (until then local `var/` sandbox OK)

---

### Slice D — Workplaces (local + SSH)

**Goal:** Execution context is real; UI Connect works.

**Includes:**

- Persist workplaces in **SQLite** (leave tunnel as type with honest “connector later” if incomplete)  
- Backends: `local` (path on disk), `ssh` (host/user/key or password via settings secret pattern)  
- UI: create/edit workplace; Connect/test connection; assign workplace to agent  
- Tool runners resolve cwd/host from agent’s workplace  
- Hide or label tunnel until Connector slice (post-Alpha OK to leave labeled stub **only** for tunnel type)

**Acceptance:**

- Create local workplace → Connect OK → bash/read_file operate in that root  
- Create SSH workplace → Connect OK against a test host (or recorded mock in tests)  
- Agent without workplace falls back to documented default sandbox  

**Depends on:** C (tools need somewhere to run)

---

### Slice E — Memory / KB / light learning

**Goal:** Persistence beyond chat transcript.

**Minimum Alpha:**

- Knowledge entries (title, body, tags) in SQLite  
- `recall` tool: search/retrieve top-k snippets into agent context  
- Optional: “save to memory” tool or UI action from chat  
- Seed a small KB under `defaults/` or DB seed  

**Out of E (post-Alpha):** full learning loop, automatic skill distillation, vector DB requirement (keyword/FTS OK for Alpha)

**Acceptance:**

- Agent can recall a seeded fact in a new session  
- Memory UI or System subsection lists entries (minimal CRUD)  

**Depends on:** Slice 0 + C (`recall` / knowledge tools)

---

### Slice F — Extra channels

**Goal:** At least one non-web channel live; others honest.

**Required:** Telegram bot channel (inbound message → same session/agent turn → reply).  
**Stretch:** WhatsApp or Discord if Telegram lands early.  
**UI:** Agent Channels panel + System Shared channels reflect real status (connected / needs token).  

**Acceptance:**

- Configure bot token in settings (masked like LLM key)  
- Message bot → appears in web session history (or channel-specific session) → agent replies on Telegram  
- Tests with mocked Telegram API  

**Depends on:** B (sessions/turns), settings patterns from LLM config

---

### Slice G — Platform → SQLite migration

**Goal:** End hybrid `platform_data` for Alpha entities.

**Migrate into SQLite (minimum):**

- workplaces (if not fully done in D)  
- schedules (+ run log)  
- plugins metadata (enable/disable)  
- skills metadata + agent_skill links  
- tools enablement per agent (if still in platform_data)  
- providers/models seed tiles: remove; System → Models uses real `llm_profiles` only (§2.2)  
- Keep profile-backed LLM resolution working  

**Keep out / stay gated:** eval_* tables (eval UI off)

**Acceptance:**

- Restart process: workplaces/schedules/skills/plugins survive without re-seed wipe of user data  
- `platform_data` gone for those domains, or only used as one-time empty-DB seed into SQLite  
- Store facade methods unchanged at call sites where possible  

**Depends on:** D, and Skills/Scheduler reality from G’s UI work (may merge Scheduler UI into G)

**Scheduler UI:** Must work by end of G — create schedule → triggers agent/session turn (cron or interval). If too large, split **G1 migration** / **G2 scheduler runner** but both required for Alpha kitchen-sink.

---

### Slice H — Alpha polish

**Goal:** Ship checklist + consistency.

**Includes:**

- Nav/feature-flag audit  
- Empty states, error toasts, loading busy badges  
- README Alpha section + architecture update  
- Progress log closeout  
- Smoke script or documented manual Alpha checklist  

**Acceptance:** Human can demo: home chat → swarm handoff → tools on workplace → recall → Telegram ping → schedule fires — without hitting stub walls.

**Depends on:** A–G

---

## 6. Architecture principles (all slices)

```text
Web / Telegram / …
    ↓
app/api + app/web          (thin)
    ↓
app/services/store         (facade)
    ↓
app/channels/*  →  app/runtime/agent/loop  →  coordinator (handoff)
    ↓
LLM (model profiles)  +  app/runtime/tools/*  +  workplaces backends
    ↓
app/models (SQLite)   [+ gated platform_data only until G]
```

- Surfaces stay thin; logic in `runtime/`, `models/`, channel adapters.  
- SSE event names stay stable (`state`, `turn.start`, `session`, `thinking`, `tool`, `tool_result`, `delta`, `done`, `delegate`, `error`, `heartbeat`).  
- Logging: keep INFO SSE/title logs; new slices add actionable INFO on handoff/workplace/connect failures.  
- Tests: pytest unit + integration; MockLLM for loops; no network in default CI.  
- Do not start the Tomo server from agents.

---

## 7. Cross-cutting requirements

### 7.1 Security

- Secrets (LLM key, SSH passwords, Telegram token): SQLite settings **encrypted at rest** via master key (`TOMO_SECRET_KEY` or `$TOMO_HOME/.secret_key`); **masked GET**; blank PUT keeps existing. Never log decrypted values.  
- Master key: chmod `600`; never commit; never put in `tomo.yaml`.  
- Bash/file tools: cwd jail; timeout; deny path escape (`..`).  
- No executing untrusted HTML in UI (keep markdown pipeline).

### 7.2 Observability

- Server logs for turn begin/end, delegate target, tool name, workplace id, channel ingress.  
- Browser `[tomo sse]` logs may remain for Alpha debug.

### 7.3 Compatibility

- Existing sessions/agents continue to load after migrations.  
- Calculator + streaming + auto-title must not regress.

---

## 8. Documentation deliverables (Cursor + Cline)

| Artifact | Owner |
|----------|--------|
| This master spec | Cursor |
| Per-slice design addendum or `docs/superpowers/specs/2026-07-26-alpha-slice-<X>-design.md` when non-obvious | Cursor before that slice’s Cline work |
| `docs/superpowers/plans/2026-07-26-alpha-slice-<X>.md` | Cursor (`writing-plans`) |
| `docs/superpowers/handoffs/alpha-slice-<X>-cline-brief.md` | Cursor |
| `docs/superpowers/progress/alpha.md` | Cursor updates after each slice review |
| README / architecture Alpha notes | Cline or Cursor on Slice H |

---

## 9. Implementation process (mandatory)

1. User approves this master spec.  
2. Cursor writes **Slice A** plan + Cline brief (then B, C, … in order).  
3. Cline implements **only** from the brief/plan.  
4. Cursor reviews → adversarial fix brief if needed → progress log.  
5. Do **not** skip dependency order without an explicit human override.  
6. Parallelization: only when Cursor documents no file-conflict risk (e.g. pure docs). Default = serial A→H.

---

## 10. Alpha success criteria (demo script)

1. **Home:** Ask Tomo from dashboard → session opens, provisional then LLM title.  
2. **Models:** Two profiles + default; two agents on different profiles; chat uses the right endpoint/model.  
3. **Swarm:** Session with Main+Ops; question needing ops → `delegate` line + Ops answer.  
4. **@mention:** `@ops check disk` → Ops responds.  
5. **Tools:** Ask to write a file under workplace → tool events → file exists.  
6. **Workplace:** Switch agent to SSH or second local root → command runs there.  
7. **Memory:** Ask a fact stored in KB in a fresh session → correct recall.  
8. **Telegram:** Ping bot → reply + history visible.  
9. **Scheduler:** One-minute schedule creates/runs a turn.  
10. **Persist:** Restart server → workplaces, schedules, agents, memories, model profiles intact.  
11. **Nav:** No Evaluate; no dead stub primary actions.

---

## 11. Out of scope (explicit)

- Eval / evaluator UI and `/api/eval/*` (remain gated)  
- Full Connector / Fyne / Go tunnel product (tunnel may stay labeled)  
- Vector DB / embedding memory  
- Multi-provider marketplace / discovery catalogs (Alpha has user-configured model profiles — §2.2)  
- Mobile apps  
- Billing / multi-tenant SaaS  
- Automatic skill distillation / learning-from-trajectory loops beyond light memory  
- Alternate home layouts beyond §2.1 (`$TOMO_HOME` tree is definitive)  

---

## 12. Risk register

| Risk | Mitigation |
|------|------------|
| Kitchen-sink overrun | Serial slices; hide surface rather than ship fake UI if a slice slips (human call) |
| Divergent home layouts | Stick to §2.1; `SOUL.md` / `SYSTEM.md` only as named in that tree |
| Bash/SSH safety | Jail + timeouts + tests; start local-only then SSH |
| Cline context limits | Small briefs; one slice plan at a time |
| SSE client closes early | Keep busy=false end protocol; don’t regress title/delegate events |
| platform_data dual-write bugs | Slice G checklist + tests for persistence across rebind |
| `$TOMO_HOME` vs `var/` | Slice 0 sets default DB under `$TOMO_HOME/state/`; document `TOMO_HOME` / overrides |

---

## 13. Spec self-review

- [x] Kitchen-sink scope named; eval excluded  
- [x] Approach B + Cursor/Cline roles explicit  
- [x] UI honesty contract  
- [x] Tomo Home §2.1 — Tomo tree; **`SOUL.md` / `SYSTEM.md` allowed**; secrets encrypted + `.secret_key`  
- [x] Multi-model profiles §2.2 — catalog, default, per-agent (Slice A)  
- [x] Slices **0**, A–H ordered with dependencies and acceptance  
- [x] No “implement later” without slice ownership  
- [x] Server not started by agents  

---

## 14. Next step after approval

1. User marks this spec **accepted** (edits welcome).  
2. Cursor runs **writing-plans** for **Slice 0** (Tomo Home) and writes `alpha-slice-0-cline-brief.md`.  
3. Human dispatches Cline on Slice 0, then A→H.  
4. Repeat.
