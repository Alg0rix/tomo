# Tool Permissions + Clarify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tool approval (`manual`/`smart`/`off`) with workplace-escape gating, jail lift, blocking clarify, web HITL UI, and chat slash `/auto` (toggle → `off`).

**Architecture:** Central `gate` in `run_turn` before `execute`. Split package `app/runtime/permissions/`. Async HITL registry + SSE cards in `chat.js`. `/auto` intercepted in chat before the LLM turn.

**Tech Stack:** Python 3.12+, FastAPI, asyncio, existing Tomo store/settings/SSE, pytest.

**Spec:** `docs/superpowers/specs/2026-07-31-tool-permissions-clarify-design.md`

## Global Constraints

- Modular files under `app/runtime/permissions/`; prefer ~150–250 lines; split before ~400.
- Do **not** start/restart/kill the Tomo server.
- Hardline + `approvals.deny` never bypassable, including `/auto` / mode `off`.
- User-facing name for mode `off` is **AUTO** (`/auto` slash).
- Consent contract: deny/timeout → model told not to retry/rephrase/alternate path.
- Smart APPROVE is one-shot (no permanent allowlist write).
- V1: no Tirith; web UI only for HITL cards; other channels can reuse HITL ids later.
- Jail lift via ContextVar grant only for the duration of one `execute` call.

---

## File map

| Path | Role |
|------|------|
| Create: `app/runtime/permissions/__init__.py` | Public exports |
| Create: `app/runtime/permissions/types.py` | `Finding`, `Assessment`, `Decision`, choice literals |
| Create: `app/runtime/permissions/patterns.py` | Hardline + dangerous regexes |
| Create: `app/runtime/permissions/escape.py` | Out-of-root detection |
| Create: `app/runtime/permissions/assess.py` | `assess(tool, args, work_root)` |
| Create: `app/runtime/permissions/allowlist.py` | Session + permanent allowlists |
| Create: `app/runtime/permissions/grants.py` | `outside_grant` ContextVar |
| Create: `app/runtime/permissions/modes.py` | Effective mode + session override (`/auto`) |
| Create: `app/runtime/permissions/smart.py` | Aux LLM APPROVE/DENY/ESCALATE |
| Create: `app/runtime/permissions/hitl.py` | Async pending approval/clarify |
| Create: `app/runtime/permissions/gate.py` | Pipeline orchestration |
| Create: `app/runtime/permissions/slash.py` | Parse `/auto` `/smart` `/manual` |
| Create: `app/runtime/permissions/messages.py` | BLOCKED message builders |
| Modify: `app/runtime/tools/sandbox.py` | Honor grant in `jail_path` |
| Modify: `app/runtime/agent/loop.py` | Gate + HITL yields before execute |
| Modify: `app/runtime/tools/clarify.py` | Blocking via HITL (or loop-special) |
| Modify: `app/tools/clarify.json` | Add `choices` |
| Modify: `app/channels/sse_map.py` | `approval_required` / `clarify_required` / mode notice |
| Modify: `app/api/rest.py` (or new `app/api/approvals.py`) | Resolve endpoints |
| Modify: `app/services/chat.py` | Slash intercept before turn |
| Modify: `app/services/platform_data.py` | Seed `approvals` settings |
| Modify: `app/static/js/chat.js` | Cards + slash builtins + mode badge |
| Modify: `app/static/css/tomo.css` | Card styles |
| Tests: `tests/unit/runtime/permissions/*` | Core matrix |
| Modify: `tests/unit/runtime/tools/test_clarify.py` | New clarify contract |
| Modify: `tests/unit/runtime/tools/test_sandbox.py` | Grant cases |

---

### Task 1: Types, patterns, escape, assess

**Files:**
- Create: `app/runtime/permissions/types.py`
- Create: `app/runtime/permissions/patterns.py`
- Create: `app/runtime/permissions/escape.py`
- Create: `app/runtime/permissions/assess.py`
- Create: `app/runtime/permissions/__init__.py`
- Test: `tests/unit/runtime/permissions/test_assess.py`

**Interfaces:**
- Produces:
  - `@dataclass Finding: kind: Literal["hardline","user_deny","escape","dangerous"]; key: str; description: str; paths: tuple[Path,...] = ()`
  - `@dataclass Assessment: findings: list[Finding]` with helpers `has_hardline()`, `has_user_deny()`, `escape_paths()`, `allowlist_keys()`
  - `detect_hardline(command: str) -> Finding | None`
  - `detect_dangerous(command: str) -> Finding | None`
  - `detect_escape(tool: str, args: dict, work_root: Path) -> list[Finding]`
  - `assess(tool: str, args: dict, work_root: Path, deny_globs: list[str] | None = None) -> Assessment`

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path
from app.runtime.permissions.assess import assess

def test_ls_in_root_clean(tmp_path: Path):
    a = assess("bash", {"command": "ls"}, tmp_path)
    assert a.findings == []

def test_rm_rf_root_hardline(tmp_path: Path):
    a = assess("bash", {"command": "rm -rf /"}, tmp_path)
    assert a.has_hardline()

def test_read_file_escape(tmp_path: Path):
    a = assess("read_file", {"path": str(Path.home() / ".tomo" / "x")}, tmp_path)
    assert any(f.kind == "escape" for f in a.findings)

def test_user_deny_glob(tmp_path: Path):
    a = assess("bash", {"command": "git push --force origin main"}, tmp_path, deny_globs=["git push --force*"])
    assert a.has_user_deny()
```

- [ ] **Step 2: Run tests — expect FAIL (import/module missing)**

Run: `pytest tests/unit/runtime/permissions/test_assess.py -v`

- [ ] **Step 3: Implement patterns/escape/assess**

Port a **trimmed** hardline set (`rm -rf /`, home wipe, mkfs, dd to disk, fork bomb, kill -1, shutdown). Dangerous: recursive rm, curl|sh, chmod 777, sensitive redirects to `~/.ssh` / `~/.tomo`, etc. Escape: file-tool path resolve vs `work_root`; bash/runpy heuristics for `~`, `$HOME`, absolute paths outside root.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add app/runtime/permissions tests/unit/runtime/permissions/test_assess.py
git commit -m "feat(permissions): add assess, patterns, and escape detection"
```

---

### Task 2: Grants + jail_path

**Files:**
- Create: `app/runtime/permissions/grants.py`
- Modify: `app/runtime/tools/sandbox.py` (`jail_path`)
- Test: `tests/unit/runtime/permissions/test_grants.py`
- Modify: `tests/unit/runtime/tools/test_sandbox.py`

**Interfaces:**
- Produces:
  - `set_outside_grant(grant: frozenset[Path] | Literal["*"] | None) -> Token`
  - `reset_outside_grant(token) -> None`
  - `current_outside_grant() -> frozenset[Path] | Literal["*"] | None`
  - `jail_path` allows target if under root OR under grant OR grant is `"*"`

- [ ] **Step 1: Failing tests for reject-without-grant and allow-with-path-grant**
- [ ] **Step 2: Implement grants + update `jail_path`**
- [ ] **Step 3: Tests PASS**
- [ ] **Step 4: Commit** `feat(permissions): jail_path honors outside_grant`

---

### Task 3: Modes, allowlist, messages, gate (manual/off)

**Files:**
- Create: `modes.py`, `allowlist.py`, `messages.py`, `gate.py`
- Test: `tests/unit/runtime/permissions/test_gate.py`

**Interfaces:**
- Produces:
  - `get_effective_mode(session_id: str | None) -> Literal["manual","smart","off"]`
  - `set_session_mode(session_id: str, mode: Literal["manual","smart","off"] | None) -> None`
  - `toggle_auto(session_id: str) -> tuple[bool, str]`  # (auto_on, notice_text)
  - Allowlist: `is_approved` / `approve_session` / `approve_permanent` + `$TOMO_HOME/approvals_allowlist.json`
  - `async def decide(tool, args, *, work_root, session_id, hitl_wait=None) -> Decision`
  - `Decision`: `allowed: bool`, `message: str | None`, `grant: frozenset[Path] | Literal["*"] | None`, `pending: PendingHitl | None`

Gate order (spec §5): hardline → user_deny → off → allowlist → no findings → (smart later) → HITL if waiter else fail-closed block.

Task 3 implements **manual** and **off** only.

- [ ] **Step 1: Tests** — off allows escape with grant `*`; hardline blocks in off; manual escape blocks without waiter
- [ ] **Step 2: Implement**
- [ ] **Step 3: PASS + commit** `feat(permissions): gate for manual and off modes`

---

### Task 4: HITL registry + API resolve

**Files:**
- Create: `app/runtime/permissions/hitl.py`
- Create: `app/api/approvals.py` (include from API package)
- Test: `tests/unit/runtime/permissions/test_hitl.py`

**Interfaces:**
- `async def request_approval(...) -> str` → once|session|always|deny
- `async def request_clarify(question, choices, session_id, timeout) -> str`
- `resolve_approval(id, choice, reason=None)` / `resolve_clarify(id, answer)`
- HTTP: `POST /api/approvals/{id}`, `POST /api/clarify/{id}`

- [ ] **Step 1: Unit test wait/resolve/timeout**
- [ ] **Step 2: Implement hitl + routes**
- [ ] **Step 3: Wire gate to `request_approval` when findings + manual**
- [ ] **Step 4: Commit** `feat(permissions): async HITL approval and clarify waiters`

---

### Task 5: Wire loop + SSE

**Files:**
- Modify: `app/runtime/agent/loop.py`
- Modify: `app/channels/sse_map.py`
- Modify chat/web as needed for event passthrough
- Test: `tests/unit/runtime/agent/test_loop_permissions.py`

**Behavior:**
- Before each non-delegate `execute`, call `decide(...)`.
- Yield `approval_required` then await; on allow set grant around execute; on block return BLOCKED tool result.
- Special-case `clarify`: yield `clarify_required`, wait, JSON tool result.

- [ ] **Step 1: Loop unit test**
- [ ] **Step 2: Implement wiring**
- [ ] **Step 3: Commit** `feat(permissions): gate tools in run_turn with SSE HITL events`

---

### Task 6: Web UI cards

**Files:**
- Modify: `app/static/js/chat.js`
- Modify: `app/static/css/tomo.css`

**Behavior:**
- Inline approval card (Once/Session/Always/Deny) → POST `/api/approvals/{id}`
- Clarify card (choices + Other) → POST `/api/clarify/{id}`
- Mode badge: `manual` | `smart` | `AUTO`
- Respect `smart_denied` / `allow_permanent` flags

- [ ] **Step 1: Implement UI** (user reloads their server)
- [ ] **Step 2: Commit** `feat(ui): approval and clarify cards in chat`

---

### Task 7: Slash `/auto` `/smart` `/manual`

**Files:**
- Create: `app/runtime/permissions/slash.py`
- Modify: `app/services/chat.py`
- Modify: `app/static/js/chat.js` (builtin picker)
- Test: `tests/unit/runtime/permissions/test_slash.py`
- Test: `tests/unit/services/test_approval_slash.py`

**Behavior:**
- `/auto` → `toggle_auto(session_id)`; history notice; **no LLM**
- `/smart` / `/manual` → set session mode; same
- Builtin wins over skill expansion
- Picker lists three builtins above skills

```python
def handle_approval_slash(message: str, session_id: str) -> str | None:
    """Return notice text if handled, else None."""
```

- [ ] **Step 1: Failing tests for toggle + precedence**
- [ ] **Step 2: Intercept in session/agent chat turn paths**
- [ ] **Step 3: Picker builtins**
- [ ] **Step 4: Commit** `feat(permissions): chat slash /auto /smart /manual`

---

### Task 8: Smart mode + settings seed

**Files:**
- Create: `app/runtime/permissions/smart.py`
- Modify: `gate.py`
- Modify: `app/services/platform_data.py` — seed `approvals_mode`, `approvals_timeout`, `approvals_deny`
- Test: `tests/unit/runtime/permissions/test_smart.py`

- [ ] **Step 1: Mock aux LLM tests**
- [ ] **Step 2: Implement + wire**
- [ ] **Step 3: Commit** `feat(permissions): smart approval via aux LLM`

---

### Task 9: Clarify tool rewrite

**Files:**
- Modify: `app/tools/clarify.json`, `app/runtime/tools/clarify.py`
- Modify: `tests/unit/runtime/tools/test_clarify.py`

**Contract:** JSON `{"question","choices_offered","user_response"}`. Still works under `/auto`.

- [ ] **Step 1: Replace old `CLARIFY:` tests**
- [ ] **Step 2: Implement**
- [ ] **Step 3: Commit** `feat(clarify): blocking question with choices via HITL`

---

### Task 10: Integration smoke

**Files:**
- Test: `tests/integration/test_permissions_chat.py`

- [ ] **Step 1: Mock LLM forces escape bash under manual → SSE has `approval_required`**
- [ ] **Step 2: Run** `pytest tests/unit/runtime/permissions tests/unit/runtime/tools/test_clarify.py tests/unit/runtime/tools/test_sandbox.py -q`
- [ ] **Step 3: Commit** `test(permissions): integration smoke for approval SSE`

---

## Spec coverage

| Spec item | Task |
|-----------|------|
| Pipeline | 3, 4, 8 |
| Escape + dangerous | 1 |
| Jail lift | 2, 5 |
| once/session/always/deny | 3, 4, 6 |
| Clarify | 5, 6, 9 |
| SSE + API + UI | 4, 5, 6 |
| `/auto` `/smart` `/manual` | 7 |
| Settings | 8 |
| Hardline in off | 3 |
