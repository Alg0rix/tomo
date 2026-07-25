# Foundation Thin Vertical Implementation Plan

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

---

### Task 1: Config, deps, SQLite connection + schema

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/core/config.py`
- Create: `app/models/db.py`
- Create: `app/models/schema.py`
- Test: `tests/unit/models/test_schema.py`

**Interfaces:**
- Produces: `DB_PATH`, `get_connection()`, `migrate(conn)`, env LLM/DB settings

- [ ] **Step 1: Add dependencies**

Add to `pyproject.toml` dependencies: `httpx>=0.27`. Add optional or dev dependency `pytest>=8`. Prefer:

```toml
dependencies = [
    # ...existing...
    "httpx>=0.27",
]
[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]
```

Run: `uv sync --group dev`

- [ ] **Step 2: Extend config**

In `app/core/config.py` add:

```python
REPO_ROOT = APP_DIR.parent
VAR_DIR = Path(os.environ.get("TOMO_VAR_DIR", str(REPO_ROOT / "var")))
DB_PATH = Path(os.environ.get("TOMO_DB_PATH", str(VAR_DIR / "tomo.db")))

LLM_PROVIDER = os.environ.get("TOMO_LLM_PROVIDER", "mock")  # mock | openai_compat
LLM_BASE_URL = os.environ.get("TOMO_LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.environ.get("TOMO_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("TOMO_LLM_MODEL", "gpt-4o-mini")
LLM_MAX_TOOL_ITERATIONS = int(os.environ.get("TOMO_LLM_MAX_TOOL_ITERATIONS", "6"))
```

- [ ] **Step 3: Write failing schema test**

```python
# tests/unit/models/test_schema.py
import sqlite3
from app.models.schema import migrate

def test_migrate_creates_tables(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    migrate(conn)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"agents", "sessions", "session_agents", "messages", "settings"} <= names
```

Run: `uv run pytest tests/unit/models/test_schema.py -v`  
Expected: FAIL (import/migrate missing)

- [ ] **Step 4: Implement `db.py` + `schema.py`**

`db.py`: ensure parent dir exists; `get_connection()` returns `sqlite3` connection with `row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`.

`schema.py`: `CREATE TABLE IF NOT EXISTS` for agents, sessions, session_agents, messages, settings as in the spec. Call from `migrate(conn)` then `conn.commit()`.

- [ ] **Step 5: Re-run test — PASS**

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock app/core/config.py app/models/db.py app/models/schema.py tests/unit/models/test_schema.py
git commit -m "feat: add SQLite schema and LLM/DB config"
```

---

### Task 2: Model mixins + seed + thin store facade (hybrid)

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

- [ ] **Step 1: Failing tests for agent create/list and session history append**

```python
def test_create_and_get_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("TOMO_DB_PATH", str(tmp_path / "t.db"))
    # re-import or call migrate + mixin
    ...
    assert get_agent("main")["name"] == "Tomo"
```

- [ ] **Step 2: Implement mixins**

Keep each mixin focused. `messages.append_entry(session_id, entry: dict)`. Sessions store `coordinator_id`; `session_agents` holds membership order. On create swarm session, insert session + rows.

Busy: module-level `set[str]` with `set_busy` / `is_busy` / clear on errors.

- [ ] **Step 3: Seed**

`seed_if_empty(conn)` inserts the four demo agents + sample sessions from current `_seed_agents` / `_seed_sessions` logic (move seed data into `seed.py`, not leave in store).

- [ ] **Step 4: Rewrite `store.py` as facade**

- Call `migrate` + `seed_if_empty` on init  
- Agents/sessions/history/settings → mixins  
- `list_tools`, plugins, eval, … → existing `platform_data` seed functions (in-memory lists OK)  
- **Do not** read/write `store.json` for agents/sessions/messages  
- Keep public method names used by API/UI  

- [ ] **Step 5: Integration test** — create session, append user+final, list history, list agents from store.

- [ ] **Step 6: Manual smoke** — `uv run python -m app.main`, open `/agents`, confirm agents load.

- [ ] **Step 7: Commit**

```bash
git commit -m "feat: SQLite-backed store facade for agents and sessions"
```

---

### Task 3: LLM client (mock + openai_compat)

**Files:**
- Create: `app/runtime/llm/base.py`, `mock.py`, `openai_compat.py`, `__init__.py`
- Test: `tests/unit/runtime/llm/test_mock.py`, `test_factory.py`

**Interfaces:**
- Produces:

```python
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]

class LLMClient(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse: ...

def get_llm() -> LLMClient: ...
```

- [ ] **Step 1: Failing tests** — mock returns fixed content; when user message contains `calculate` or `=`, mock returns a `calculator` tool_call then (on second call with tool result) a final content string.

- [ ] **Step 2: Implement mock + openai_compat**

`openai_compat`: `POST {base}/chat/completions` via httpx.AsyncClient; map tool_calls from OpenAI format. Raise clear error if API key missing when provider is `openai_compat`.

- [ ] **Step 3: Factory `get_llm()`** reads `LLM_PROVIDER`.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: add mock and OpenAI-compatible LLM clients"
```

---

### Task 4: Tool registry + calculator

**Files:**
- Modify: `tools/calculator.json` (ensure schema matches)
- Create: `app/runtime/tools/calculator.py`
- Modify: `app/runtime/tools/registry.py`
- Test: `tests/unit/runtime/tools/test_calculator.py`, `test_registry.py`

**Interfaces:**
- Produces: `get_openai_tools() -> list[dict]`, `execute(name: str, arguments: dict) -> str`

- [ ] **Step 1: Failing tests** — `2+2` → `"4"`; invalid expr → error string (not exception to caller); unknown tool → error string.

- [ ] **Step 2: Implement calculator** with `ast` literal/eval whitelist (no `eval` of arbitrary code).

- [ ] **Step 3: Registry loads `tools/*.json`, maps backend module path or hardcodes calculator for foundation.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: calculator tool and registry dispatch"
```

---

### Task 5: Agent context + loop (coordinator turn)

**Files:**
- Modify: `app/runtime/agent/context.py`
- Modify: `app/runtime/agent/loop.py`
- Test: `tests/unit/runtime/agent/test_loop.py`

**Interfaces:**
- Produces: async generator or callback of internal events:

```python
# event kinds for chat.py to map to SSE
# {"kind": "thinking", "content": str}
# {"kind": "tool", "tool": str, "args": dict}
# {"kind": "tool_result", "tool": str, "result": str, "error": bool}
# {"kind": "final", "content": str}
# {"kind": "error", "message": str}
```

- Consumes: `get_llm()`, registry, store history for session

- [ ] **Step 1: Failing tests with mock LLM** — text-only path yields final; tool path yields tool → tool_result → final; max iterations stops.

- [ ] **Step 2: `context.py`** builds OpenAI-style messages from session history (user/final/tool_*). System prompt from `defaults/coordinator_system.md` or short constant.

- [ ] **Step 3: `loop.py`** orchestration only — no HTTP, no SSE formatting.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: agent turn loop with tool iterations"
```

---

### Task 6: Web channel + chat SSE wiring (coordinator-only)

**Files:**
- Modify: `app/channels/web.py`
- Rewrite: `app/services/chat.py` (keep `_fmt_sse`; remove stub delegation logic for execution — may keep `delegate` event unused or omit)
- Test: `tests/integration/test_chat_mock.py` (async)

**Interfaces:**
- Consumes: loop events
- Produces: SSE stream compatible with existing UI JS

- [ ] **Step 1: Document mapping in code comments**

| Loop kind | SSE event |
|-----------|-----------|
| thinking | `thinking` |
| tool | `tool` |
| tool_result | `tool_result` |
| final | `delta` (optional chunks) then `done` |
| error | `error` |

Always emit `state` busy true/false and `turn.start`.

- [ ] **Step 2: Coordinator-only** — ignore `_pick_responders` multi-agent execution; always run `coordinator_id` only. Still persist `agent_ids` unchanged.

- [ ] **Step 3: Persist** user message before loop; each tool/final/error via `append_session_history` with DB types (`tool_call` / `tool_output` / `final`).

- [ ] **Step 4: Integration test** with mock LLM + temp DB: stream collects a `done` event; history has user+final.

- [ ] **Step 5: Manual** — chat in UI with mock; then optional real key.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: wire web chat to real agent loop over SSE"
```

---

### Task 7: Docs + progress closeout

**Files:**
- Modify: `docs/superpowers/progress/foundation.md`
- Modify: `README.md` (Getting started: env vars for LLM/DB; note foundation status)
- Modify: `docs/architecture.md` (point to thin vertical)

- [ ] **Step 1: Update progress** — mark all layers done; list commits.

- [ ] **Step 2: README** — how to run with mock vs openai_compat.

- [ ] **Step 3: Commit**

```bash
git commit -m "docs: mark foundation thin vertical complete"
```

---

## Self-review vs spec

| Spec requirement | Task |
|------------------|------|
| SQLite adapter | 1–2 |
| OpenAI-compat + mock | 3 |
| calculator only | 4 |
| Agent loop | 5 |
| Web + SSE | 6 |
| Modular files | Global + file map |
| Hybrid platform_data | Task 2 |
| SSE contract / no JSON migrate / busy / httpx / full completion | Global Constraints |

No TBD placeholders. Types consistent across tasks (`LLMResponse`, SSE mapping table).

---

## Execution handoff

**Plan complete.** Execution for this project:

**Cline loop (required by human):** For each Task 1→7, Cursor posts a brief → Cline implements → Cursor reviews → checkbox + progress log → next task.

Do not start Task 2 until Task 1 review passes.
