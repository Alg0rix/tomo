# Alpha Slice 0 — Tomo Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `$TOMO_HOME` (default `~/.tomo`) with Tomo’s locked tree (`SOUL.md`, per-agent `SYSTEM.md`, `tomo.yaml`, `.secret_key`, `library/`, `state/tomo.db`), bootstrap from `defaults/`, encrypt UI secrets at rest in SQLite, and wire the coordinator prompt loader to prefer home files. Optional hidden `$TOMO_HOME/.env` for bootstrap only — never `secrets.env`.

**Architecture:** Thin `app/core/home.py` owns paths + ensure/bootstrap. `config.py` exposes `TOMO_HOME` / default `DB_PATH`. `context.py` builds system text from agent `SYSTEM.md` + `SOUL.md` overlays.

**Tech Stack:** Python 3.11+, pathlib, pytest, existing FastAPI app.

**Master spec:** `docs/superpowers/specs/2026-07-26-alpha-kitchen-sink-design.md` §2.1 + Slice 0.

## Global Constraints

- Do **not** start/restart the Tomo server.
- Do **not** create `secrets.env` — only optional `.env` (dotfile).
- Do **not** put API keys or the master key in `tomo.yaml`.
- SQLite secret fields must be **ciphertext** (Fernet or AES-GCM); master key from `TOMO_SECRET_KEY` or `$TOMO_HOME/.secret_key`.
- Tests must set `TOMO_HOME` to a temp dir (never real `~/.tomo`) and use a throwaway key.
- Prefer ~150–250 lines/file; smell at ~400+.
- Commit when the human asks, or once at end of slice if brief says so — follow brief.
- Cline implements; Cursor reviews.

---

## File map

| Path | Responsibility |
|------|----------------|
| Create: `app/core/home.py` | Path helpers + `ensure_tomo_home()` (incl. `.secret_key`) |
| Create: `app/core/secrets.py` | Load master key; encrypt/decrypt settings secret values |
| Modify: `app/core/config.py` | `TOMO_HOME`, default `DB_PATH` under home `state/` |
| Modify: `app/models/mixins/settings.py` (and callers) | Persist ciphertext for secret keys; decrypt for runtime `get_settings` |
| Modify: `app/runtime/agent/context.py` | Load SOUL/SYSTEM from home |
| Create: `defaults/SOUL.md` | Seed global persona |
| Create: `defaults/tomo.yaml` | Minimal non-secret prefs seed |
| Keep: `defaults/coordinator_system.md` | Fallback / seed source for default agent `SYSTEM.md` |
| Modify: `tests/conftest.py` | Set `TOMO_HOME` before imports |
| Create: `tests/unit/core/test_home.py` | Bootstrap + paths |
| Create: `tests/unit/runtime/agent/test_context_home.py` | Prompt resolution |
| Modify: `README.md` | Tomo Home section |
| Modify: `docs/superpowers/progress/alpha.md` | Mark Slice 0 done when shipped |

---

### Task 1: Config + path helpers + ensure_home

**Files:**
- Create: `app/core/home.py`
- Modify: `app/core/config.py`
- Modify: `tests/conftest.py`
- Test: `tests/unit/core/test_home.py`

**Interfaces:**
- Produces:
  - `config.TOMO_HOME: Path`
  - `config.DB_PATH` default = `TOMO_HOME / "state" / "tomo.db"` (still overridable via `TOMO_DB_PATH`)
  - `home.ensure_tomo_home(root: Path | None = None) -> Path`
  - `home.soul_path(root=None) -> Path`
  - `home.agent_dir(agent_id, root=None) -> Path`
  - `home.agent_system_path(agent_id, root=None) -> Path`
  - `home.agent_soul_path(agent_id, root=None) -> Path`
  - `home.agent_knowledge_dir(agent_id, root=None) -> Path`
  - `home.agent_work_dir(agent_id, root=None) -> Path`
  - `home.library_skills_dir(root=None) -> Path`
  - `home.library_memory_dir(root=None) -> Path`

- [ ] **Step 1: Write failing tests** for ensure + paths

```python
# tests/unit/core/test_home.py
from pathlib import Path
from app.core import home

def test_ensure_tomo_home_creates_tree(tmp_path, monkeypatch):
    root = tmp_path / "tomo-home"
    monkeypatch.setenv("TOMO_HOME", str(root))
    # re-import or pass root= explicitly
    got = home.ensure_tomo_home(root)
    assert got == root
    assert (root / "SOUL.md").is_file()
    assert (root / "tomo.yaml").is_file()
    assert (root / "library" / "skills").is_dir()
    assert (root / "library" / "memory").is_dir()
    assert (root / "agents").is_dir()
    assert (root / "workplaces").is_dir()
    assert (root / "state").is_dir()
    assert not (root / "secrets.env").exists()
    # .env must NOT be auto-created with secrets
    assert not (root / ".env").exists() or (root / ".env").stat().st_size == 0
    sk = root / ".secret_key"
    assert sk.is_file()
    assert sk.stat().st_mode & 0o777 == 0o600
    assert len(sk.read_text(encoding="utf-8").strip()) >= 32

def test_agent_paths(tmp_path):
    root = tmp_path / "h"
    home.ensure_tomo_home(root)
    assert home.agent_system_path("main", root).name == "SYSTEM.md"
    assert home.agent_soul_path("main", root).name == "SOUL.md"
    assert home.agent_knowledge_dir("main", root).name == "knowledge"
```

- [ ] **Step 2: Implement `config.py`**

```python
TOMO_HOME = Path(os.environ.get("TOMO_HOME", str(Path.home() / ".tomo"))).expanduser()
VAR_DIR = Path(os.environ.get("TOMO_VAR_DIR", str(TOMO_HOME / "state")))
DB_PATH = Path(os.environ.get("TOMO_DB_PATH", str(VAR_DIR / "tomo.db")))
```

Keep `REPO_ROOT / "var"` out of the default path. If `TOMO_VAR_DIR` / `TOMO_DB_PATH` set, honor them (tests).

- [ ] **Step 3: Implement `home.py`**

`ensure_tomo_home(root)`:
- mkdir parents for layout dirs
- If `SOUL.md` missing: copy `defaults/SOUL.md` (or write short Tomo persona)
- If `tomo.yaml` missing: copy `defaults/tomo.yaml`
- If `TOMO_SECRET_KEY` unset and `.secret_key` missing: write a new random key (`secrets.token_urlsafe(32+)`), `chmod 600`
- Never write API keys into home files; never overwrite an existing `.secret_key`
- Idempotent (second call no-op for existing files)
- Return root Path

Add Task 1b (same PR): `app/core/secrets.py` + settings mixin tests:

- Prefer `cryptography.fernet.Fernet` (add `cryptography` to project deps if missing); store ciphertext with a clear prefix e.g. `enc:v1:` + token
- `encrypt_secret` / `decrypt_secret` round-trip
- After `update_settings({"llm_api_key": "sk-test"})`, raw DB value is ciphertext (not `"sk-test"`)
- `get_settings()` returns decrypted key for runtime
- `public_settings` still masks
- Reject or refuse to treat non-ciphertext as a valid stored secret in Alpha (always write encrypted)

- [ ] **Step 4: Update `tests/conftest.py`**

Before other app imports:

```python
_TEST_HOME = tempfile.mkdtemp(prefix="tomo-home-pytest-")
os.environ["TOMO_HOME"] = _TEST_HOME
os.environ.setdefault("TOMO_DB_PATH", os.path.join(_TEST_HOME, "state", "tomo.db"))
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/core/test_home.py -q
```

Expected: PASS

---

### Task 2: Seed defaults + wire context prompts

**Files:**
- Create: `defaults/SOUL.md`
- Create: `defaults/tomo.yaml`
- Modify: `app/runtime/agent/context.py`
- Test: `tests/unit/runtime/agent/test_context_home.py`
- Optionally call `ensure_tomo_home()` from app startup (`create_app` lifespan or store init) — once, non-fatal

**Interfaces:**
- Produces: `build_system_prompt(agent_id: str | None = None, *, home_root: Path | None = None) -> str`
- Keep `coordinator_system_prompt()` as thin wrapper or deprecate into `build_system_prompt(None)` for coordinator

**Resolution order (locked):**

1. If `agent_id` and `$TOMO_HOME/agents/<id>/SYSTEM.md` exists and non-empty → use it as base  
2. Else use `defaults/coordinator_system.md` (or existing fallback constant) as base  
3. Prepend or append global `$TOMO_HOME/SOUL.md` if present (persona layer)  
4. If `agents/<id>/SOUL.md` exists, append as agent persona overlay after global SOUL  

Document the exact concatenation in a short docstring (e.g. sections with blank-line separators). Do not pull secrets from files.

- [ ] **Step 1: Failing tests**

```python
def test_build_system_prompt_uses_soul_and_system(tmp_path):
    home.ensure_tomo_home(tmp_path)
    (tmp_path / "SOUL.md").write_text("PERSONA: concise.\n", encoding="utf-8")
    agent = tmp_path / "agents" / "ops"
    agent.mkdir(parents=True)
    (agent / "SYSTEM.md").write_text("You are Ops.\n", encoding="utf-8")
    text = build_system_prompt("ops", home_root=tmp_path)
    assert "PERSONA: concise" in text
    assert "You are Ops" in text

def test_missing_agent_falls_back_to_defaults(tmp_path):
    home.ensure_tomo_home(tmp_path)
    text = build_system_prompt("ghost", home_root=tmp_path)
    assert len(text) > 20  # repo default or fallback
```

- [ ] **Step 2: Implement defaults + `build_system_prompt`**
- [ ] **Step 3: Update call sites** that use `coordinator_system_prompt()` / `build_messages` to pass coordinator agent id when known (at least from `loop.py` if agent_id available)
- [ ] **Step 4: pytest**

```bash
uv run pytest tests/unit/core/test_home.py tests/unit/runtime/agent/test_context_home.py tests/unit/runtime/agent/test_loop.py -q
```

Expected: PASS (fix loop if prompt wiring broke)

---

### Task 3: Optional `.env` load + docs + progress

**Files:**
- Modify: `app/main.py` or `app/core/config.py` (load dotenv)
- Modify: `README.md`
- Modify: `docs/superpowers/progress/alpha.md`

**Secrets behavior:**

- If `$TOMO_HOME/.env` exists, load with `override=False` (process env wins). Use stdlib or a tiny parser if python-dotenv not present — prefer adding `python-dotenv` only if already a dependency; otherwise skip file load in Slice 0 and document “optional later” **only if** dotenv absent — check `pyproject.toml`. If no dotenv, implement a minimal KEY=VAL reader for Slice 0 (no export of values into logs).
- UI-edited keys remain SQLite (`get_llm` unchanged).
- Never log `.env` contents.

- [ ] **Step 1: README “Tomo Home”** — tree, `TOMO_HOME`, `SOUL.md`/`SYSTEM.md`, secrets policy  
- [ ] **Step 2: progress/alpha.md** — Slice 0 → done after verify  
- [ ] **Step 3: Full related tests**

```bash
uv run pytest tests/unit/core/ tests/unit/runtime/agent/ -q
```

- [ ] **Step 4: Commit** (if brief requests)

```bash
git add -A && git commit -m "$(cat <<'EOF'
feat: add Tomo Home ($TOMO_HOME) with SOUL/SYSTEM prompt loading

EOF
)"
```

---

## Spec coverage

| Spec item | Task |
|-----------|------|
| `$TOMO_HOME` layout | 1 |
| Seed SOUL/tomo.yaml | 1–2 |
| No secrets.env; encrypted SQLite secrets; optional .env; `.secret_key` | 1 (+ secrets.py), 3 |
| SYSTEM.md / SOUL.md resolution | 2 |
| Tests use temp home | 1 (conftest) |
| README | 3 |

## Out of scope for Slice 0

- Agent Name/Role UI (Slice A)  
- Creating per-agent dirs on agent create (can mkdir in ensure helpers when first reading; full agent-create wiring in A)  
- Skills install UI, memory CRUD, workplaces  
- Migrating existing `var/tomo.db` automatically (document: set `TOMO_DB_PATH` or copy into `$TOMO_HOME/state/`)
